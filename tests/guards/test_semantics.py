"""Zero-dependence at the SEMANTIC level: GT-Z3a, GT-Z3b, GT-Z3c and SC4 (spec 9.1, B5, B6, B9).

The toy of `zero_toys.py` wraps a reference-stack swap behind method names that cross the schema (the schema's `gamma` is the toy's `convexity` and the schema's `convexity`
layer is its `gamma`), arguments named `zzz`/`qqq`, and decoys that raise when a schema-named method is reached WITHOUT a binding. It runs in a full engine backtest and must give
exactly the numbers of the reference stack's own swap; its unbound twin must be rejected. Each guard has a twin proving that it can fail.
"""
from __future__ import annotations

import copy
import subprocess
import sys

import pandas as pd
import pytest

from guards import scan, zero_toys as Z
from pricebt import api
from pricebt.contracts.schema import SchemaRegistry
from pricebt.contracts.spec import Kit, build_spec
from pricebt.errors import ConfigError, MethodCallError
from pricebt.testing import refstack as R

pytestmark = pytest.mark.core

CONV = {**R.USD_SOFR_OIS_CONVENTIONS, "calendar": "wk"}
BASE = {
    "name": "mismatched_names",
    "registry": {"allow": ["pricebt", "guards"]},
    "backtest": {
        "tz": "America/New_York", "grid": {"start": "2024-06-03", "end": "2024-07-31", "freq": "1b"}, "progress": {"show": False},
        "attribution": {"layers": ["carry", "roll", "delta", "convexity"], "cadence": "eod", "baseline": True}, "measures": ["dv01", "pv"], "vector_measures": ["delta_ladder"],
    },
    "market": {"mdps": {"syn": {"type": "pricebt.testing.synthetic:SyntheticMarket", "kwargs": {"start": "2024-06-03", "end": "2024-07-31", "calendar_name": "wk", "seed": 7, "history_years": 1}}},
               "pricers": {"primary": {"mdp": "syn", "wrap": "pricebt.testing.refstack:wrap"}}},
    "instruments": {"ois": {"asset_class": "swap", "factory": "guards.zero_toys:mismatched_swap", "conventions": CONV}},
    "strategy": {"triggers": [{"type": "periodic", "frequency": "1w", "start_date": "2024-06-03", "actions": [
        {"type": "add_trade", "priceables": {"instrument": "ois", "terms": {"side": "pay", "maturity": "2Y", "notional": 1e7, "fixed_rate": 3.9}}, "trade_duration": "3w"},
        {"type": "add_trade", "priceables": {"instrument": "ois", "terms": {"side": "receive", "maturity": "5Y", "notional": 5e6, "fixed_rate": 4.1}}, "trade_duration": "3w"}]}]},
}


def reference_config():
    c = copy.deepcopy(BASE)
    c["instruments"]["ois"]["factory"] = "pricebt.testing.refstack:swap"
    return c


@pytest.fixture(scope="module")
def runs():
    Z.CALLS.clear()
    mismatched = api.run(BASE)
    calls = list(Z.CALLS)
    return mismatched, api.run(reference_config()), calls


def spec_with(bind_overrides=None, drop=(), cls=Z.Mismatched):
    bind = {k: v for k, v in Z.MISMATCHED_BIND.items() if k not in drop}
    bind.update(bind_overrides or {})
    kit = Kit(factory=Z.mismatched_factory, asset_class="swap", default_bind=bind, cls=cls)
    return build_spec("ois", {"factory": kit, "conventions": CONV}, schemas=SchemaRegistry.default())


# ------------------------------------------------------------------------------------ SC4 / GT-Z3a: the mismatched-names toy conforms by binding alone and runs
def test_sc4_the_mismatched_names_toy_satisfies_the_swap_schema_and_runs_in_an_engine_backtest_with_the_numbers_of_the_reference_stack(runs):
    mismatched, reference, calls = runs
    assert len(mismatched.trades) >= 8 and mismatched.reconcile().ok
    pd.testing.assert_series_equal(mismatched.equity_curve, reference.equity_curve, check_exact=True)
    pd.testing.assert_frame_equal(mismatched.trades.reset_index(drop=True), reference.trades.reset_index(drop=True), check_exact=True)
    assert set(mismatched.layers.columns) >= {"carry", "roll", "delta", "convexity", "unexplained"}
    pd.testing.assert_frame_equal(mismatched.layers, reference.layers, check_exact=True)
    assert reference.layers["convexity"].abs().max() > 0, "the crossed pair is not vacuous: the layer is non-zero, so a swapped resolution would change the numbers"


def test_gt_z3a_every_odd_name_was_called_through_its_binding_and_no_decoy_was_reached(runs):
    _, _, calls = runs
    assert {c[0] for c in calls} == {"price_it", "one_bp", "level_of", "buckets", "lay_a", "lay_b", "lay_c", "convexity", "gamma"}
    # a decoy raises AssertionError when called: reaching this line proves none was, and the crossed pair really was bound crosswise:
    assert {c[0] for c in calls if c[0] in ("convexity", "gamma")} == {"convexity", "gamma"}


def test_gt_z3a_an_unbound_required_name_is_rejected_although_the_object_has_a_method_of_that_name():
    assert callable(Z.Mismatched.gamma) and callable(Z.Mismatched.delta_ladder)
    with pytest.raises(ConfigError) as e:
        spec_with(drop=("gamma",))
    assert "gamma" in str(e.value)
    with pytest.raises(ConfigError) as e:
        spec_with(drop=("delta_ladder",))
    assert "delta_ladder" in str(e.value)
    for layer in ("carry", "roll", "delta", "convexity"):  # layers are bound names too: the object has a decoy method of each name
        with pytest.raises(ConfigError) as e:
            spec_with(drop=(layer,))
        assert layer in str(e.value)


def test_gt_z3a_a_binding_to_a_missing_target_does_not_fall_back_to_the_method_of_the_same_name():
    with pytest.raises(ConfigError) as e:
        spec_with({"gamma": {"target": {"method": "gamma_but_misspelt"}, "kwargs": {"zzz": "@ctx"}}})
    assert "gamma_but_misspelt" in str(e.value)


def test_twin_the_decoys_are_live_so_a_call_by_name_would_have_been_caught():
    with pytest.raises(AssertionError, match="decoy `value`"):
        Z.Mismatched(None).value()
    c = copy.deepcopy(BASE)
    c["instruments"]["ois"]["bind"] = {"dv01": {"target": {"method": "dv01"}, "kwargs": {"ctx": "@ctx", "tenors": Z.TENORS}}}  # bound BY NAME to the decoy
    with pytest.raises(Exception, match="decoy `dv01`"):
        api.run(c)


def test_twin_a_by_name_resolution_of_the_crossed_pair_gives_different_numbers(runs):
    mismatched, reference, _ = runs
    c = reference_config()
    c["instruments"]["ois"]["bind"] = {"convexity": {"target": {"method": "gamma"}, "kwargs": {"ctx": "@ctx", "tenors": Z.TENORS}}}  # the reference swap's PAR gamma as the layer
    swapped = api.run(c)
    assert not swapped.layers["convexity"].equals(reference.layers["convexity"]), "if the guard could not fail, this run would equal the reference"


# ------------------------------------------------------------------------------------ GT-Z3b: exactly the declared arguments, whatever their names
def test_gt_z3b_a_binding_passes_exactly_the_declared_keyword_arguments_and_nothing_else(runs):
    _, _, calls = runs
    declared = {"price_it": ("zzz",), "level_of": ("zzz",), "lay_a": ("zzz",), "lay_b": ("zzz",), "lay_c": ("zzz",), "gamma": ("zzz",),
                "one_bp": ("qqq", "zzz"), "buckets": ("qqq", "zzz"), "convexity": ("qqq", "zzz")}
    assert calls and all(args == () for _, args, _ in calls), "no positional argument is ever invented"
    for name, _, kwnames in calls:
        assert kwnames == declared[name], (name, kwnames)


@pytest.mark.parametrize("kwargs", [{"zzz": "@ctx", "unexpected": 1}, {"unexpected": 1}], ids=["an-undeclared-keyword-is-passed-on", "the-declared-one-is-missing"])
def test_twin_gt_z3b_what_the_binding_declares_is_what_the_callable_gets_so_a_mismatch_is_an_error(kwargs):
    c = copy.deepcopy(BASE)
    c["instruments"]["ois"]["bind"] = {"value": {"target": {"method": "price_it"}, "kwargs": kwargs}}
    with pytest.raises(MethodCallError, match="price_it"):
        api.run(c)


# ------------------------------------------------------------------------------------ GT-Z3c: the loader never imports an adapter because of text
CONFIG_WITH_ADAPTER_WORDS = """
# this config was ported from a rateslib study: factory rl_swap, risk_model default, pricebt.contrib.rateslib, QuantLib
name: words_only
doc: "rl_swap on rateslib and QuantLib, ladder rl_par_swap_ladder"
meta: {notes: "import pricebt.contrib.rateslib; import QuantLib"}
backtest: {tz: America/New_York, grid: {start: 2024-01-02, end: 2024-01-31, freq: 1b}, progress: {show: false}}
market: {mdps: {rates: {type: toy}}, pricers: {primary: {mdp: rates}}}
instruments: {fwd: {asset_class: toy, factory: "pricebt.testing.toys:forward", conventions: {strike: 4.0}}}
strategy: {triggers: []}
"""
PROBE = """
import sys
from pricebt import api
try:
    api.build(sys.argv[1])
    status = 'BUILT'
except Exception as e:
    status = 'BUILD-ERROR ' + type(e).__name__
hit = sorted(m for m in sys.modules if m.split('.')[0] in ('rateslib', 'QuantLib', 'gs_quant') or m.startswith('pricebt.contrib'))
print(status)
print('IMPORTED', hit)
"""


def _probe(tmp_path, text):
    p = tmp_path / "c.yaml"
    p.write_text(text, encoding="utf8")
    env = {**__import__("os").environ, "PYTHONPATH": str(scan.PROJECT / "src")}
    out = subprocess.run([sys.executable, "-c", PROBE, str(p)], capture_output=True, text=True, cwd=scan.PROJECT, env=env)
    assert out.returncode == 0, out.stderr[-1500:]
    status, imported = out.stdout.strip().splitlines()[-2:]
    return status, imported


def test_gt_z3c_adapter_words_in_a_config_import_nothing(tmp_path):
    assert _probe(tmp_path, CONFIG_WITH_ADAPTER_WORDS) == ("BUILT", "IMPORTED []")


def test_twin_gt_z3c_naming_an_adapter_as_a_factory_does_import_it(tmp_path):
    pytest.importorskip("rateslib")
    text = CONFIG_WITH_ADAPTER_WORDS.replace('asset_class: toy, factory: "pricebt.testing.toys:forward", conventions: {strike: 4.0}', 'asset_class: swap, factory: "pricebt.contrib.rateslib:swap", conventions: {}')
    _, imported = _probe(tmp_path, text)
    assert "pricebt.contrib.rateslib" in imported, "naming the adapter's factory imports it (the build then fails on the empty conventions), so the probe above CAN fail"
