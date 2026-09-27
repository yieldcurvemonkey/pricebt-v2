"""The tie-out harness (spec X1-X6) on the toy stack: what it proves, what it detects, where it says the difference is, and how it reports it."""
import copy
import json

import numpy as np
import pandas as pd
import pytest

from pricebt.errors import ConfigError
from pricebt.tieout import DEFAULT_TOLERANCES, Tol, Tolerances, TieoutError, compare, run_stack, run_tieout, save, to_html, to_markdown
from pricebt.tieout.compare import _measure, _status
from tieout_helpers import BASE, DIFFERENT, SAME

pytestmark = pytest.mark.core

AUDIT = ("dv01", "gamma", "rate")


@pytest.fixture(scope="module")
def two_same():
    return {"a": [SAME], "b": [SAME]}


@pytest.fixture(scope="module")
def outcome():
    """One run of the base under a stack that is the same twice and one that mis-prices by a strike shift of 0.05."""
    return run_tieout(BASE, {"ref": [SAME], "twin": [SAME], "mutant": [DIFFERENT]}, selftest=True)


# ------------------------------------------------------------------ X5a: the same stack twice is EXACTLY zero at every level
def test_the_same_stack_twice_is_exactly_zero_at_every_level(outcome):
    rep = outcome.reports["ref_vs_twin"]
    assert rep.passed and outcome.header["selftest"] == "passed"
    for r in rep.rows:
        assert r.status in ("exact", "info"), (r.level, r.quantity, r.status, r.max_abs)
        assert r.max_abs == 0.0
    assert {"L0", "L1", "L2", "L3", "L4"} <= {r.level for r in rep.rows}
    assert not rep.offenders and rep.frame()["status"].isin(["exact", "info"]).all()


def test_the_self_test_report_is_kept_and_covers_every_level(outcome):
    st = outcome.selftest
    assert st is not None and st.passed and {r.level for r in st.rows} == {"L0", "L1", "L2", "L3", "L4"}


# ------------------------------------------------------------------ detection and location
def test_a_model_difference_is_detected_at_l1_and_located(outcome):
    rep = outcome.reports["ref_vs_mutant"]
    assert not rep.passed
    pv = [r for r in rep.rows if r.level == "L1" and r.quantity == "pv"]
    assert len(pv) == 1 and pv[0].status == "exceeds" and pv[0].asset_class == "toy"
    n, q = 10.0, 1.0  # the mutant strike is 0.05 higher: pv is lower by notional * 0.05 per unit
    assert pv[0].max_abs == pytest.approx(n * 0.05 * q, rel=1e-9)
    assert "position=" in pv[0].where and "ts=" in pv[0].where, "the report says WHERE the worst difference occurs"
    assert rep.offenders and rep.offenders[0].level in ("L1", "L4") and rep.offenders[0].diff > 0


def test_only_what_the_difference_touches_fails_and_the_rest_stays_exact(outcome):
    rep = outcome.reports["ref_vs_mutant"]
    status = {(r.level, r.quantity): r.status for r in rep.rows}
    assert status[("L1", "cash")] == "exact" and status[("L1", "financing")] == "exact" and status[("L1", "quantity")] == "exact"
    assert status[("L2", "dv01")] == "exact", "the mutant only moves the strike: the dollar delta is unchanged"
    assert status[("L0", "resolved_terms")] == "exact" and status[("L0", "conventions_digest")] == "exact" and status[("L4", "trade_ledger_structure")] == "exact"
    assert status[("L4", "equity")] in ("exact", "noise"), "a constant strike shift cancels in the P&L: only the LEVEL of pv differs, not the equity curve"


def test_a_failure_is_attributed_to_inputs_when_l0_already_differs():
    """Two providers whose snapshots differ (different digests AND different rates): every downstream difference is explained by the inputs."""
    from pricebt.pricer import FunctionMDP
    from pricebt.testing.toys import ToyMDP
    from pricebt.results import BacktestResult
    from test_engine_audit import STRAT, Tagged, run as run_provider

    def provider_with_offset(tag, scale):
        inner = ToyMDP()
        return FunctionMDP(lambda ts, req: Tagged(ts, inner.get_pricer(ts).rate * scale, tag))

    a = BacktestResult(run_provider(STRAT(), audit=True, mdp=provider_with_offset("one", 1.0))[0])
    b = BacktestResult(run_provider(STRAT(), audit=True, mdp=provider_with_offset("two", 1.05))[0])
    rep = compare(a, b, names=("one", "two"))
    by = {(r.level, r.quantity): r for r in rep.rows}
    assert by[("L0", "snapshot_digests")].status == "input" and not rep.passed
    assert by[("L1", "pv")].status == "input" and "explained by an L0 input difference" in by[("L1", "pv")].note
    assert by[("L4", "equity")].status == "input"
    assert by[("L1", "cash")].status in ("exact", "noise"), "what the inputs did not move stays exact"


def test_missing_digests_are_reported_as_info_not_as_proof():
    from pricebt.results import BacktestResult
    from test_engine_audit import run as run_provider, STRAT

    from pricebt.testing.toys import ToyMDP

    a = BacktestResult(run_provider(STRAT(), audit=True, mdp=ToyMDP())[0])
    rep = compare(a, a, names=("a", "b"))
    d = [r for r in rep.rows if r.quantity == "snapshot_digests"][0]
    assert d.status == "info" and "not proven" in d.note


# ------------------------------------------------------------------ structure
def test_different_rows_are_a_structure_failure_not_a_numeric_one():
    a = run_stack(BASE, [SAME], audit_measures=AUDIT)[1]
    cfg = copy.deepcopy(BASE)
    cfg["strategy"]["triggers"][0]["frequency"] = "2w"
    b = run_stack(cfg, [SAME], audit_measures=AUDIT)[1]
    rep = compare(a, b, names=("weekly", "biweekly"))
    bad = {(r.level, r.quantity): r.status for r in rep.failures()}
    assert bad.get(("L1", "marks_rows")) == "structure" and bad.get(("L4", "trade_ledger_structure")) == "structure"


def test_a_baseline_layer_produced_by_one_run_only_is_a_structure_failure():
    with_b = run_stack(BASE, [SAME], audit_measures=AUDIT, baseline=True)[1]
    without = run_stack(BASE, [SAME], audit_measures=AUDIT, baseline=False)[1]
    rep = compare(with_b, without, names=("with", "without"))
    row = [r for r in rep.rows if r.quantity == "baseline_layers"][0]
    assert row.status == "structure" and "tay_delta" in row.where


def test_a_result_without_an_audit_trail_cannot_be_compared():
    from pricebt import api

    plain = api.run(BASE)
    with pytest.raises(TieoutError, match="no audit trail"):
        compare(plain, plain)


# ------------------------------------------------------------------ X1 and the runner
def test_the_stacks_must_share_one_base_and_there_must_be_two(two_same):
    with pytest.raises(TieoutError, match="at least two"):
        run_tieout(BASE, {"only": [SAME]})
    with pytest.raises(TieoutError, match="reference"):
        run_tieout(BASE, two_same, reference="nope", selftest=False)


def test_an_overlay_that_touches_the_shared_config_is_refused_before_anything_runs():
    bad = {"strategy": {"triggers": []}}
    with pytest.raises(ConfigError, match="a stack may not set 'strategy'"):
        run_tieout(BASE, {"a": [SAME], "b": [bad]}, selftest=False)
    bad2 = {"instruments": {"fwd": {"conventions": {"strike": 5.0}}}}
    with pytest.raises(ConfigError, match="conventions"):
        run_tieout(BASE, {"a": [SAME], "b": [bad2]}, selftest=False)


def test_the_self_test_fails_loudly_when_the_reference_run_is_not_reproducible(monkeypatch):
    from pricebt.tieout import runner

    real = runner.run_stack
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        built, res = real(*a, **kw)
        if calls["n"] == 3:  # the third run is the self-test's repeat of the reference: corrupt one mark
            res.record.audit["marks"].loc[5, "pv"] += 1e-3
        return built, res

    monkeypatch.setattr(runner, "run_stack", flaky)
    with pytest.raises(TieoutError, match="harness self-test failed"):
        run_tieout(BASE, {"a": [SAME], "b": [SAME]})


def test_run_stack_turns_the_audit_on_and_the_baseline_on_by_default():
    built, res = run_stack(BASE, [SAME])
    assert built.settings.audit and built.settings.baseline and built.settings.audit_measures == AUDIT
    assert set(res.record.audit) == {"inputs", "marks", "layers"} and "tay_delta" in set(res.record.audit["layers"]["layer"])


# ------------------------------------------------------------------ tolerances
def test_placeholder_tolerances_are_the_appendix_b_table():
    t = Tolerances()
    assert t.for_("L1", "pv").rel == 1e-6 and t.for_("L1", "pv", "bond").rel == 1e-7 and t.for_("L1", "cash").rel == 1e-8 and t.for_("L2", "dv01").rel == 1e-4
    assert t.for_("L2", "gamma").rel == 1e-2 and t.for_("L4", "equity").rel == 1e-5 and t.for_("L3", "carry").rel == 1e-3
    assert t.for_("L2", "anything_else").rel == DEFAULT_TOLERANCES["L2.*"].rel, "a quantity without its own entry takes the level's"


def test_overrides_by_number_and_mapping_and_the_most_specific_key_wins():
    t = Tolerances({"L1.pv": 1e-9, "L1.pv.swap": {"rel": 1e-3, "floor": 100.0}, "L3.carry": {"rel": 0.5, "expected": True}})
    assert t.for_("L1", "pv", "bond").rel == 1e-9 and t.for_("L1", "pv", "swap") == Tol(1e-3, 100.0) and t.for_("L3", "carry").expected is True
    with pytest.raises(ConfigError, match="rel"):
        Tolerances({"L1.pv": {"floor": 1.0}})
    with pytest.raises(ConfigError, match="number or a mapping"):
        Tolerances({"L1.pv": "tight"})
    with pytest.raises(ConfigError, match="no tolerance"):
        Tolerances({}).for_("L9", "x")


def test_a_wider_tolerance_turns_an_exceedance_into_noise_and_expected_makes_it_a_reported_non_failure():
    a = run_stack(BASE, [SAME], audit_measures=AUDIT)[1]
    b = run_stack(BASE, [DIFFERENT], audit_measures=AUDIT)[1]
    strict = compare(a, b, names=("a", "b"))
    assert not strict.passed
    wide = compare(a, b, names=("a", "b"), tolerances=Tolerances({"L1.pv": 1.0, "L4.*": 1.0, "L3.*": 1.0, "L2.*": 1.0, "L4.stats": 1.0}))
    assert wide.passed and [r.status for r in wide.rows if r.quantity == "pv"] == ["noise"]
    exp = compare(a, b, names=("a", "b"), tolerances=Tolerances({"L1.pv": {"rel": 1e-12, "expected": True}, "L4.*": 1.0, "L3.*": 1.0, "L4.stats": 1.0}))
    row = [r for r in exp.rows if r.level == "L1" and r.quantity == "pv"][0]
    assert row.status == "expected" and row.passed, "a declared definition difference is reported, not a failure"


def test_measure_floors_relative_differences_and_ignores_matching_nans():
    tol = Tol(1e-6, floor=1.0)
    mx, mr, i, mism = _measure(np.array([1000.0, 0.0, np.nan]), np.array([1000.001, 5e-7, np.nan]), tol)
    assert mx == pytest.approx(1e-3) and mr == pytest.approx(1e-6) and i == 0 and mism == 0, "0 vs 5e-7 is measured against the floor 1, not against 0"
    assert _measure(np.array([1.0, np.nan]), np.array([1.0, 2.0]), tol)[3] == 1
    assert _status(0.0, 0.0, 0, tol) == "exact" and _status(1.0, 1e-7, 0, tol) == "noise" and _status(1.0, 1e-3, 0, tol) == "exceeds"
    assert _status(1.0, 1e-3, 0, Tol(1e-6, expected=True)) == "expected" and _status(0.0, 0.0, 2, tol) == "structure"


# ------------------------------------------------------------------ the report
def test_markdown_and_html_name_every_level_the_verdict_and_the_location(outcome):
    rep = outcome.reports["ref_vs_mutant"]
    md = to_markdown(rep, title="toy")
    assert md.startswith("# Tie-out: toy") and "**Verdict: FAIL**" in md and all(f"## L{i}" in md for i in range(5)) and "EXCEEDS" in md and "position=" in md
    assert "## Largest offenders" in md and "## Tolerances used" in md
    ok = to_markdown(outcome.reports["ref_vs_twin"])
    assert "**Verdict: PASS**" in ok
    page = to_html(rep, title="toy")
    assert page.startswith("<!doctype html>") and "<table>" in page and "class=bad" in page and "class=ok" in page


def test_the_report_is_saved_as_parquet_markdown_html_and_json(outcome, tmp_path):
    d = save(outcome.reports, tmp_path / "out", header=outcome.header)
    for name in outcome.reports:
        assert (d / f"{name}.md").exists() and (d / f"{name}.html").exists()
        assert len(pd.read_parquet(d / f"{name}.summary.parquet")) == len(outcome.reports[name].rows)
        pd.read_parquet(d / f"{name}.offenders.parquet")
    meta = json.loads((d / "tieout.json").read_text(encoding="utf8"))
    assert meta["reports"]["ref_vs_twin"]["passed"] is True and meta["reports"]["ref_vs_mutant"]["passed"] is False and meta["header"]["selftest"] == "passed"
    assert meta["reports"]["ref_vs_mutant"]["failures"]


def test_run_tieout_writes_its_output_directory(tmp_path):
    run_tieout(BASE, {"a": [SAME], "b": [DIFFERENT]}, selftest=False, out=tmp_path / "t")
    assert (tmp_path / "t" / "a_vs_b.md").exists()


def test_the_unexplained_share_is_reported_per_stack_and_asset_class(outcome):
    stats = outcome.reports["ref_vs_twin"].stack_stats
    assert {s["stack"] for s in stats} == {"ref", "twin"} and all(0.0 <= s["unexplained_share"] < 1e-9 for s in stats) and stats[0]["pnl_explained_abs"] > 0
