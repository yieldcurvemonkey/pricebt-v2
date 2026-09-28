"""skills/pricebt-strategy-intake/scripts/spec.py: defaults + assumptions, validation errors, CLI."""
from __future__ import annotations

import copy
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "skills" / "pricebt-strategy-intake" / "scripts" / "spec.py"
sys.path.insert(0, str(SCRIPT.parent))
import spec as specmod  # noqa: E402


def filled(**over) -> dict:
    s, _ = specmod.apply_defaults({"name": "toy-test", **over})
    return s


def test_the_template_is_valid_and_needs_no_defaults():
    s, assumptions = specmod.apply_defaults(specmod.TEMPLATE)
    assert assumptions == []
    assert specmod.validate_spec(s) == []


def test_apply_defaults_records_every_default_and_only_those():
    user = {"name": "my-idea", "archetype": "periodic_roll", "signal": {"type": "none"},
            "dates": {"start": "2024-02-01", "end": "2024-06-03"},
            "instruments": {"primary": {"class": "IRSwap", "kwargs": {"pay_or_receive": "Pay", "notional_currency": "USD"}}}}
    s, assumptions = specmod.apply_defaults(user)
    joined = "\n".join(assumptions)
    assert "dates.frequency = 1b (template default)" in assumptions
    assert "costs = {model: dv01_bp, level: 0.25} (template default)" in assumptions
    assert "signal.lookback = 30 (template default)" in assumptions
    for given in ("dates.start", "dates.end", "archetype", "signal.type", "instruments", "name ="):
        assert given not in joined
    # instruments are not merged field-by-field with the template's example swap
    assert s["instruments"]["primary"]["kwargs"] == {"pay_or_receive": "Pay", "notional_currency": "USD"}
    assert s["dates"]["start"] == date(2024, 2, 1)  # ISO strings coerced
    assert s["deliverables"]["out_dir"] == "reports/my-idea"
    assert "deliverables = {out_dir: reports/my-idea, formats: [html, md]} (template default)" in assumptions
    assert s["assumptions"] == assumptions
    assert user["dates"]["start"] == "2024-02-01"  # the caller's dict is not mutated
    # idempotent: a second pass finds nothing left to default and keeps the recorded list
    s2, again = specmod.apply_defaults(s)
    assert again == [] and s2["assumptions"] == assumptions


def test_existing_assumptions_are_kept():
    s, new = specmod.apply_defaults({"name": "x", "assumptions": ["user said: ignore holidays"]})
    assert s["assumptions"][0] == "user said: ignore holidays" and s["assumptions"][1:] == new


def _mutate(path: str, value):
    def f(s):
        *parents, leaf = path.split(".")
        node = s
        for p in parents:
            node = node[p]
        if value is KeyError:
            del node[leaf]
        else:
            node[leaf] = value
    return f


@pytest.mark.parametrize("mutation, message", [
    (_mutate("archetype", "carry"), "archetype: 'carry'"),
    (_mutate("signal.type", "rsi"), "signal.type: 'rsi'"),
    (_mutate("costs.model", "bid_ask"), "costs.model: 'bid_ask'"),
    (_mutate("sizing.method", "kelly"), "sizing.method: 'kelly'"),
    (_mutate("signal.instrument", "nope"), "signal.instrument: 'nope'"),
    (_mutate("dates.end", date(2023, 1, 1)), "must be before end"),
    (_mutate("dates.in_sample_end", date(2026, 1, 1)), "in_sample_end 2026-01-01 must lie inside"),
    (_mutate("dates.frequency", "daily"), "dates.frequency"),
    (_mutate("sizing.method", "dv01_target"), "needs a positive sizing.dv01_target"),
    (_mutate("signal.params.z_entry", -1), "positive z_entry"),
    (_mutate("signal.lookback", 1), "lookback >= 2"),
    (_mutate("signal.lag", -1), "signal.lag"),
    (_mutate("costs.level", -0.5), "costs.level"),
    (_mutate("assets", ["tests/assets/no_such.yaml"]), "does not exist"),
    (_mutate("result_ccy", "USD"), "needs an FX config"),
    (_mutate("name", "Bad Name"), "kebab-case"),
    (_mutate("instruments.primary.class", "Bond"), "not a pricebt.instrument class"),
    (_mutate("risks_to_report", ["Pricee"]), "risks_to_report"),
    (_mutate("benchmark", "sp500"), "benchmark"),
    (_mutate("instruments", {"main": {"class": "IRSwap", "kwargs": {}}}), "'primary' instrument is required"),
])
def test_validation_errors(mutation, message):
    s = filled()
    assert specmod.validate_spec(s) == []  # the defaults alone are valid
    mutation(s)
    errors = specmod.validate_spec(s)
    assert any(message in e for e in errors), errors


def test_archetype_specific_rules():
    s = filled(archetype="mean_reversion", sizing={"method": "nav", "nav": 1e6})
    assert any("mean_reversion supports sizing.method notional only" in e for e in specmod.validate_spec(s))

    s = filled(archetype="momentum")  # template signal is par_rate_zscore
    assert any("needs signal.type rate_momentum" in e for e in specmod.validate_spec(s))

    pay = {"class": "IRSwap", "kwargs": {"pay_or_receive": "Pay", "notional_currency": "USD"}}
    s = filled(archetype="curve_trade", signal={"type": "none"}, instruments={"primary": pay, "second": copy.deepcopy(pay)})
    assert any("opposite pay_or_receive" in e for e in specmod.validate_spec(s))
    s = filled(archetype="curve_trade", signal={"type": "none"}, instruments={"primary": pay})
    assert any("'primary' and 'second'" in e for e in specmod.validate_spec(s))

    s = filled(archetype="delta_hedged", signal={"type": "none"}, instruments={"primary": pay})
    assert any("needs a 'hedge' instrument" in e for e in specmod.validate_spec(s))
    s = filled(archetype="risk_band", signal={"type": "none"}, instruments={"primary": pay, "hedge": pay})
    assert any("positive max_abs_dv01" in e for e in specmod.validate_spec(s))

    s = filled(archetype="event", signal={"type": "none"}, event_dates=["2030-01-01"])
    assert any("outside" in e for e in specmod.validate_spec(s))
    s = filled(archetype="event", signal={"type": "none"})
    assert any("at least one date" in e for e in specmod.validate_spec(s))

    eur = {"class": "IRSwap", "kwargs": {"pay_or_receive": "Pay", "notional_currency": "EUR"}}
    s = filled(instruments={"primary": pay, "hedge": eur})
    assert any("set result_ccy" in e for e in specmod.validate_spec(s))
    s = filled(result_ccy="USD", fx="tests/assets/toy_fx.yaml", risk_limits={"stop_loss_mtm": 1e5})
    assert any("stop_loss_mtm with result_ccy" in e for e in specmod.validate_spec(s))


def test_missing_sections_are_reported_not_crashed():
    assert any("missing" in e for e in specmod.validate_spec({"name": "x"}))


def test_parse_risk():
    from pricebt.risk import IRDelta, IRDeltaParallel, Price
    assert specmod.parse_risk("Price") == Price
    assert specmod.parse_risk("IRDeltaParallel") == IRDeltaParallel
    assert specmod.parse_risk("IRDelta(aggregation_level='Type')") == IRDelta(aggregation_level="Type")
    with pytest.raises(ValueError):
        specmod.parse_risk("NotAMeasure")


def test_dump_and_load_round_trip(tmp_path):
    s = filled()
    out = specmod.dump_spec(s, tmp_path / "sub" / "spec.yaml")
    assert specmod.load_spec(out) == s


def _cli(*args):
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT / "tests")])}
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, cwd=ROOT, env=env)


def test_cli_validate_and_defaults(tmp_path):
    good = tmp_path / "good.yaml"
    good.write_text(yaml.safe_dump({"name": "cli-idea", "archetype": "periodic_roll", "signal": {"type": "none"}}))
    r = _cli("validate", str(good))
    assert r.returncode == 0 and r.stdout.strip().endswith("OK"), r.stdout + r.stderr

    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump({"name": "cli-idea", "archetype": "carry"}))
    r = _cli("validate", str(bad))
    assert r.returncode == 1 and "ERROR: archetype: 'carry'" in r.stdout

    out = tmp_path / "filled.yaml"
    r = _cli("defaults", str(good), "--out", str(out))
    assert r.returncode == 0 and "costs = {model: dv01_bp, level: 0.25} (template default)" in r.stdout
    assert yaml.safe_load(out.read_text())["deliverables"]["out_dir"] == "reports/cli-idea"
