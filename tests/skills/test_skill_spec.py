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
    (_mutate("instruments.primary.class", "IRCap"), "not a pricebt.instrument class"),
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


_OPT = {"pay_or_receive": "Pay", "buy_sell": "Buy", "expiration_date": "3m", "termination_date": "10y",
        "notional_currency": "USD", "notional_amount": 1e7, "strike": "A-50"}
_BOND = {"buy_sell": "Buy", "identifier": "TOY 4.25 2034-11-15", "size": 1e7, "settlement_currency": "USD"}
_SWAP = {"pay_or_receive": "Pay", "termination_date": "10y", "notional_currency": "USD", "notional_amount": 1e7}


def _errors(cls, kwargs, **over):
    return specmod.validate_spec(filled(instruments={"primary": {"class": cls, "kwargs": kwargs}}, **over))


def test_swaption_and_bond_kwargs_are_checked_against_the_generated_class():
    assert _errors("IRSwaption", _OPT) == []
    assert _errors("IRSwaption", {**_OPT, "pay_or_receive": "Straddle", "settlement": "Physical"}) == []
    assert _errors("Bond", _BOND) == []
    camel = {("payOrReceive" if k == "pay_or_receive" else k): v for k, v in _SWAP.items()}
    assert _errors("IRSwap", camel) == []  # gs camelCase names are snake-cased first
    for cls, kwargs, message in [
        ("IRSwaption", {**_OPT, "expiry": "3m"}, "'expiry' is not a IRSwaption field"),  # typo gs would ignore
        ("Bond", {**_BOND, "notional_amount": 1e7}, "'notional_amount' is not a Bond field"),  # a bond sizes by `size`
        ("IRSwaption", {**_OPT, "buy_sell": "Hold"}, "not a valid BuySell"),
        ("IRSwaption", {k: v for k, v in _OPT.items() if k != "buy_sell"}, "buy_sell is required for IRSwaption"),
        ("IRSwaption", {k: v for k, v in _OPT.items() if k != "pay_or_receive"}, "pay_or_receive is required for IRSwaption"),
        ("Bond", {k: v for k, v in _BOND.items() if k != "identifier"}, "identifier is required for Bond"),
        ("IRSwaption", {**_OPT, "premium": 25_000.0}, "premium 25000.0 must be 0 in a backtest"),
    ]:
        errors = _errors(cls, kwargs)
        assert any(message in e for e in errors), (message, errors)


@pytest.mark.parametrize("first, second, ok", [
    (("Bond", _BOND), ("Bond", {**_BOND, "buy_sell": "Sell"}), True),
    (("Bond", _BOND), ("Bond", _BOND), False),
    (("Bond", _BOND), ("IRSwap", _SWAP), True),  # asset swap: long bond (delta < 0) vs payer (delta > 0)
    (("Bond", _BOND), ("IRSwap", {**_SWAP, "pay_or_receive": "Receive"}), False),
    (("IRSwaption", _OPT), ("IRSwaption", {**_OPT, "buy_sell": "Sell"}), True),
    (("IRSwaption", _OPT), ("IRSwaption", {**_OPT, "pay_or_receive": "Receive", "buy_sell": "Sell"}), False),  # both long delta
    (("IRSwaption", {**_OPT, "pay_or_receive": "Straddle"}), ("IRSwaption", {**_OPT, "pay_or_receive": "Straddle", "buy_sell": "Sell"}), True),
    (("IRSwaption", {**_OPT, "pay_or_receive": "Straddle"}), ("IRSwaption", {**_OPT, "pay_or_receive": "Straddle"}), False),
])
def test_curve_trade_needs_opposite_positions_for_any_class(first, second, ok):
    s = filled(archetype="curve_trade", signal={"type": "none"},
               instruments={"primary": {"class": first[0], "kwargs": first[1]}, "second": {"class": second[0], "kwargs": second[1]}})
    errors = specmod.validate_spec(s)
    assert (not any("opposite positions" in e for e in errors)) == ok, errors


def test_dv01_sizing_refuses_a_straddle_and_hedge_measure_must_parse():
    straddle = {**_OPT, "pay_or_receive": "Straddle"}
    dv01 = dict(archetype="periodic_roll", signal={"type": "none"}, sizing={"method": "dv01_target", "dv01_target": 5000.0})
    errors = _errors("IRSwaption", straddle, **dv01)
    assert any("sizing.dv01_target: instruments.primary" in e and "Straddle" in e for e in errors)
    assert _errors("IRSwaption", _OPT, **dv01) == []
    assert _errors("Bond", _BOND, risk_limits={"hedge_measure": "IRDeltaParallel"}) == []
    assert any("risk_limits.hedge_measure" in e for e in _errors("Bond", _BOND, risk_limits={"hedge_measure": "IRDeltaa"}))


def test_instrument_terms_cli(capsys):
    import instrument_terms

    assert instrument_terms.main(["IRSwaption", "pay_or_receive=Pay", "buy_sell=Sell", "expiration_date=3m"]) == 0
    out = capsys.readouterr().out
    assert "unit IRDelta sign: -1" in out and "'buy_sell': Buy" in out and "'pay_or_receive': Pay" in out
    assert instrument_terms.main(["Bond", "buy_sell=Buy", "expiry=3m"]) == 1
    assert "'expiry' is not a Bond field" in capsys.readouterr().out


def test_missing_sections_are_reported_not_crashed():
    assert any("missing" in e for e in specmod.validate_spec({"name": "x"}))


def test_parse_risk():
    from pricebt.risk import IRDelta, IRDeltaParallel, Price
    assert specmod.parse_risk("Price") == Price
    assert specmod.parse_risk("IRDeltaParallel") == IRDeltaParallel
    assert specmod.parse_risk("IRDelta(aggregation_level='Type')") == IRDelta(aggregation_level="Type")
    from pricebt.risk import IRVanna
    vanna = specmod.parse_risk("IRVanna(aggregation_level=Type, bump_size=0.5)")  # unquoted numbers stay numbers
    assert vanna == IRVanna(aggregation_level="Type", bump_size=0.5)
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
