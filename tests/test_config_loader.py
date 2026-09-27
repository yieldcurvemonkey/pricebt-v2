import subprocess
import sys
from pathlib import Path

import pytest

from pricebt import api
from pricebt.config import yamlio
from pricebt.config.loader import build
from pricebt.errors import ConfigError

pytestmark = pytest.mark.core

BASE = """
name: toy_carry
backtest:
  tz: America/New_York
  grid: {start: 2024-01-02, end: 2024-03-29, freq: 1b}
  progress: {show: false}
  attribution: {layers: [carry, delta], cadence: eod}
  measures: [dv01]
market:
  mdps:
    rates: {type: toy}
  pricers:
    primary: {mdp: rates}
instruments:
  fwd:
    asset_class: toy
    factory: "pricebt.testing.toys:forward"
    conventions: {strike: 4.0, notional: 10.0, carry_bp_per_day: 0.5}
    layers: [carry, delta]
strategy:
  triggers:
    - type: periodic
      frequency: 1m
      actions:
        - {type: add_trade, priceables: fwd, trade_duration: 1m}
"""


def cfg(**edits):
    c = yamlio.loads(BASE)
    for k, v in edits.items():
        yamlio.set_path(c, k.replace("__", "."), v)
    return c


def test_end_to_end_toy_config_runs_and_reconciles():
    res = api.run(cfg())
    assert len(res.trades) >= 4 and res.reconcile().ok
    assert res.equity_curve.iloc[-1] != 0.0
    assert res.config_hash and len(res.config_hash) == 16


def test_config_and_python_equivalence_and_determinism():
    a, b = api.run(cfg()), api.run(cfg())
    assert (a.equity_curve == b.equity_curve).all() and a.config_hash == b.config_hash


def test_unknown_top_level_key_suggests():
    c = cfg()
    c["instrument"] = {}
    with pytest.raises(ConfigError) as e:
        build(c)
    assert e.value.code == "CFG-UNKNOWN-KEY" and "instruments" in str(e.value)


def test_unknown_action_kwarg_reports_path():
    c = cfg()
    c["strategy"]["triggers"][0]["actions"][0]["trade_duratin"] = "1m"
    with pytest.raises(ConfigError) as e:
        build(c)
    assert "strategy.triggers[0].actions[0].trade_duratin" in str(e.value)


def test_unknown_type_and_bad_reference():
    c = cfg()
    c["strategy"]["triggers"][0]["type"] = "periodik"
    with pytest.raises(ConfigError) as e:
        build(c)
    assert e.value.code == "CFG-UNKNOWN-TYPE" and "periodic" in str(e.value)
    c = cfg()
    c["strategy"]["triggers"][0]["actions"][0]["priceables"] = "nope"
    with pytest.raises(ConfigError) as e:
        build(c)
    assert e.value.code == "CFG-REF" and "fwd" in str(e.value)


def test_unknown_dotted_path_is_blocked_by_allow_list():
    c = cfg()
    c["instruments"]["fwd"]["factory"] = "os:system"
    with pytest.raises(ConfigError) as e:
        build(c)
    assert e.value.code == "CFG-ALLOW"
    c["instruments"]["fwd"]["factory"] = "pricebt.testing.toys:forward"  # allowed prefix
    build(c)


def test_asset_class_and_binding_target_checks():
    c = cfg()
    c["instruments"]["fwd"]["asset_class"] = "swap"  # the toy kit builds asset class `toy`
    with pytest.raises(ConfigError) as e:
        build(c)
    assert e.value.code == "CFG-SCHEMA"
    c = cfg()
    c["instruments"]["fwd"]["bind"] = {"value": {"target": {"method": "mark_to_model"}, "kwargs": {"ctx": "@ctx"}}}
    with pytest.raises(ConfigError) as e:
        build(c)
    assert e.value.code == "CFG-BINDING" and "mark_to_model" in str(e.value)


def test_enum_strings_and_flat_trigger_requirements():
    c = cfg()
    c["signals"] = {"lvl": {"type": "pricer", "lookup": "level", "kwargs": {"name": "rate"}}}
    c["strategy"]["triggers"] = [{"type": "mkt", "data_source": "lvl", "trigger_level": 4.0, "direction": "above", "actions": [{"type": "add_trade", "priceables": "fwd", "trade_duration": "5b"}]}]
    res = api.run(c)
    assert len(res.trades) >= 1


def test_set_override_changes_result(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(BASE)
    a = api.run(p)
    b = api.run(p, sets=["instruments.fwd.conventions.notional=20"])
    assert a.config_hash != b.config_hash and abs(b.equity_curve.iloc[-1]) != abs(a.equity_curve.iloc[-1])


def test_cli_validate_run_and_error_exit(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(BASE)
    env = {**__import__("os").environ, "PYTHONPATH": "src"}
    ok = subprocess.run([sys.executable, "-m", "pricebt", "validate", str(p)], capture_output=True, text=True, env=env)
    assert ok.returncode == 0 and "OK" in ok.stdout
    run = subprocess.run([sys.executable, "-m", "pricebt", "run", str(p), "--no-progress", "--out", str(tmp_path / "out")], capture_output=True, text=True, env=env)
    assert run.returncode == 0, run.stderr
    assert (tmp_path / "out").exists()
    bad = tmp_path / "bad.yaml"
    bad.write_text(BASE.replace("type: periodic", "type: periodik"))
    r = subprocess.run([sys.executable, "-m", "pricebt", "validate", str(bad)], capture_output=True, text=True, env=env)
    assert r.returncode == 2 and "periodic" in r.stderr


def test_book_signals_and_record_signals_from_config():
    c = cfg()
    c["backtest"]["measures"] = ["dv01"]
    c["backtest"]["record_signals"] = ["book_dv01"]
    c["signals"] = {"book_dv01": {"type": "book_measure", "measure": "dv01"}}
    res = api.run(c)
    eq = res.equity
    assert "signal_book_dv01" in eq.columns and eq["signal_book_dv01"].abs().max() > 0
    assert eq["signal_book_dv01"].iloc[0] == 0.0 and eq["signal_book_dv01"].max() == pytest.approx(eq["measure_dv01"].max()), "the signal reads the book's dv01 (0 before the first trade)"
    with pytest.raises(ConfigError):
        c2 = cfg()
        c2["backtest"]["record_signals"] = ["nope"]
        api.build(c2).run()


def test_a_derived_signal_with_a_named_input_can_drive_a_trigger():
    # a trigger reading a derived signal whose `inputs` are NAMES works in plain YAML (no Python factory is needed)
    c = cfg()
    c["signals"] = {"lvl": {"type": "pricer", "lookup": "level", "kwargs": {"name": "rate"}},
                    "lvl_z": {"type": "derived", "op": "zscore", "inputs": ["lvl"], "window": 3},
                    "lvl_zc": {"type": "derived", "op": "clip", "inputs": ["lvl_z"], "lo": -1.0, "hi": 1.0}}
    c["strategy"]["triggers"] = [{"type": "mkt", "data_source": "lvl_zc", "trigger_level": 0.5, "direction": "above", "on_missing": "skip",
                                  "actions": [{"type": "add_trade", "priceables": "fwd", "trade_duration": "5b"}]}]
    assert len(api.run(c).trades) >= 1
    c["strategy"]["triggers"][0]["trigger_level"] = 1.5  # the clip bounds the z-score at 1, so this level is never reached
    assert len(api.run(c).trades) == 0
