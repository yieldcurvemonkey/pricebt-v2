"""Tie-out harness, doubt cycle 2 (H3, H4, H5, M8, M10 and equal infinities): the harness must not invent a pass, mask a second difference, silently widen an invariant or crash on an empty run."""
import copy

import numpy as np
import pytest

from pricebt.errors import ConfigError
from pricebt.tieout import Tol, Tolerances, compare, run_stack, run_tieout, to_markdown
from pricebt.tieout.compare import _measure
from tieout_helpers import BASE, SAME

pytestmark = pytest.mark.core


# ------------------------------------------------------------------ equal infinities
def test_a_statistic_that_is_infinite_on_both_sides_is_equal_not_nan():
    inf = np.array([np.inf, 1.0])
    mx, mr, _, mismatch = _measure(inf, inf.copy(), Tol(1e-5))
    assert (mx, mr, mismatch) == (0.0, 0.0, 0)
    mx, mr, i, _ = _measure(np.array([np.inf, 1.0]), np.array([1.0, 1.0]), Tol(1e-5))
    assert mx == np.inf and i == 0, "infinite against finite is the largest difference there is"
    mx, _, _, _ = _measure(np.array([np.inf]), np.array([-np.inf]), Tol(1e-5))
    assert mx == np.inf, "and +inf against -inf is not equal"


# ------------------------------------------------------------------ H3: a comparison that compared nothing is not `exact`
def test_h3_a_measure_that_is_nan_on_both_sides_is_reported_as_nothing_compared():
    base = copy.deepcopy(BASE)  # an option is DEFINED (it binds `vega`) but never traded: the forwards that are traded bind no `vega`
    base["instruments"]["opt"] = {"asset_class": "toy", "factory": "pricebt.testing.toys:option", "conventions": {"strike": 4.0, "expiry": "2024-06-28", "notional": 1e3}}
    a = run_stack(base, [SAME], audit_measures=("dv01", "vega"))[1]
    b = run_stack(base, [SAME], audit_measures=("dv01", "vega"))[1]
    rep = compare(a, b, names=("a", "b"))
    by = {(r.level, r.quantity): r for r in rep.rows}
    assert by[("L2", "dv01")].status == "exact"
    assert by[("L2", "vega")].status == "info" and "nothing was compared" in by[("L2", "vega")].note
    assert rep.passed


# ------------------------------------------------------------------ H4: an L0 difference explains ITS positions, not every position
def _two_positions():
    _, a = run_stack(BASE, [SAME])
    return a, copy.deepcopy(a), list(a.record.positions["id"])


def test_h4_a_genuine_difference_at_a_position_with_identical_inputs_is_not_labelled_input():
    a, b, ids = _two_positions()
    for res, val in ((a, "2025-01-03"), (b, "2025-01-06")):  # inputs differ for the FIRST position only
        tr = res.record.trades
        tr["term_maturity"] = [val if p == ids[0] else "2025-02-03" for p in tr["position"]]
    m = b.record.audit["marks"]
    m.loc[m["position"] == ids[2], "pv"] += 0.5  # a genuine pricing difference on the third position
    rep = compare(a, b, names=("a", "b"))
    pv = [r for r in rep.rows if r.level == "L1" and r.quantity == "pv"][0]
    assert [r for r in rep.rows if r.quantity == "resolved_terms"][0].status == "input"
    assert pv.status == "exceeds" and not rep.passed, "the third position's inputs are identical: this is a model difference and must say so"


def test_h4_a_difference_confined_to_the_position_whose_inputs_differ_is_input():
    a, b, ids = _two_positions()
    for res, val in ((a, "2025-01-03"), (b, "2025-01-06")):
        tr = res.record.trades
        tr["term_maturity"] = [val if p == ids[0] else "2025-02-03" for p in tr["position"]]
    m = b.record.audit["marks"]
    m.loc[m["position"] == ids[0], "pv"] += 0.5
    pv = [r for r in compare(a, b, names=("a", "b")).rows if r.level == "L1" and r.quantity == "pv"][0]
    assert pv.status == "input" and "L0" in pv.note


def test_h4_a_difference_in_the_conventions_block_explains_everything_downstream():
    a, b, ids = _two_positions()
    n = next(iter(b.record.manifest["instruments"]))
    b.record.manifest["instruments"][n]["conventions_digest"] = "different"
    m = b.record.audit["marks"]
    m["pv"] += 0.5
    rep = compare(a, b, names=("a", "b"))
    assert [r for r in rep.rows if r.level == "L1" and r.quantity == "pv"][0].status == "input"


# ------------------------------------------------------------------ H5: tolerances
def test_h5_a_wildcard_declaration_does_not_widen_an_invariant_but_widens_the_rest():
    t = Tolerances({"L1.*": 1e-2})
    assert t.for_("L1", "quantity", "toy").rel == 0.0, "a position size must match exactly: a level wildcard does not touch it"
    assert t.for_("L1", "cash", "toy").rel == 1e-2 and t.for_("L1", "pv", "bond").rel == 1e-2
    assert Tolerances({"L1.quantity": 0.5}).for_("L1", "quantity", "toy").rel == 0.5, "a declaration that NAMES the quantity is honoured"


def test_h5_a_declaration_still_beats_a_more_specific_shipped_default():
    assert Tolerances({"L4.*": 1e-2}).for_("L4", "trade_pv").rel == 1e-2


def test_h5_the_report_prints_the_tolerance_actually_used_per_quantity():
    a, b, _ = _two_positions()
    md = to_markdown(compare(a, b, names=("a", "b"), tolerances=Tolerances({"L1.*": 1e-2})))
    line = [ln for ln in md.splitlines() if ln.startswith("| L1.cash")][0]
    assert "1.000e-02" in line, line
    q = [ln for ln in md.splitlines() if ln.startswith("| L1.quantity")][0]
    assert "1.000e-02" not in q, q


@pytest.mark.parametrize("bad", ["false", "no", 1, 0, None])
def test_h5_expected_must_be_a_real_boolean(bad):
    with pytest.raises(ConfigError, match="expected"):
        Tolerances({"L1.pv": {"rel": 1e-9, "expected": bad}})
    assert Tolerances({"L1.pv": {"rel": 1e-9, "expected": False}}).for_("L1", "pv").expected is False


# ------------------------------------------------------------------ M8: layer definitions are shown
def test_m8_two_stacks_that_define_a_layer_differently_show_it_at_l3_and_still_pass():
    a, b, _ = _two_positions()
    n = next(iter(b.record.manifest["instruments"]))
    layers = b.record.manifest["instruments"][n]["layers"]
    first = next(iter(layers))
    layers[first] = "other." + layers[first].split(".", 1)[-1].replace("@1", "@2")
    rep = compare(a, b, names=("a", "b"))
    row = [r for r in rep.rows if r.quantity == "layer_definitions"][0]
    assert row.status == "info" and first in row.where and row.passed
    same = [r for r in compare(a, copy.deepcopy(a), names=("a", "b")).rows if r.quantity == "layer_definitions"][0]
    assert same.status == "exact"


# ------------------------------------------------------------------ M10: a run that never trades is a report, not a crash
def test_m10_a_tieout_of_a_base_that_never_trades_reports_instead_of_crashing():
    base = copy.deepcopy(BASE)
    base["strategy"] = {"triggers": []}
    res = run_tieout(base, {"a": [SAME], "b": [SAME]}, selftest=False)
    assert res.passed and len(res.results["a"].trades) == 0
    assert [r for r in res.reports["a_vs_b"].rows if r.quantity == "resolved_terms"][0].status == "exact"


# ------------------------------------------------------------------ the attribution rule, pinned from every side
def _terms_differ_for(a, b, pid):
    for res, val in ((a, "2025-01-03"), (b, "2025-01-06")):
        tr = res.record.trades
        tr["term_maturity"] = [val if p == pid else "2025-02-03" for p in tr["position"]]


def test_h4_one_explained_and_one_unexplained_position_together_are_not_explained():
    a, b, ids = _two_positions()
    _terms_differ_for(a, b, ids[0])
    m = b.record.audit["marks"]
    m.loc[m["position"].isin([ids[0], ids[2]]), "pv"] += 0.5  # the explained position AND a position whose inputs match
    pv = [r for r in compare(a, b, names=("a", "b")).rows if r.level == "L1" and r.quantity == "pv"][0]
    assert pv.status == "exceeds", "one violating value is unexplained, so the row is a model difference (not `any`, `all`)"


def test_h4_a_portfolio_quantity_is_explained_by_a_position_whose_inputs_differ():
    a, b, ids = _two_positions()
    _terms_differ_for(a, b, ids[0])
    b.record.equity["equity"] = b.record.equity["equity"] + 1.0  # the equity curve differs at every point
    row = [r for r in compare(a, b, names=("a", "b")).rows if r.level == "L4" and r.quantity == "equity"][0]
    assert row.status == "input"


def test_h4_a_position_opened_in_one_run_only_explains_the_portfolio_difference():
    a, b, ids = _two_positions()
    b.record.trades = b.record.trades[b.record.trades["position"] != ids[1]]
    b.record.equity["equity"] = b.record.equity["equity"] + 1.0
    rep = compare(a, b, names=("a", "b"))
    assert [r for r in rep.rows if r.quantity == "resolved_terms"][0].status == "input"
    assert [r for r in rep.rows if r.level == "L4" and r.quantity == "equity"][0].status == "input"


def test_h4_a_portfolio_difference_is_explained_from_the_first_timestamp_whose_snapshot_differs_inclusive():
    a, b, _ = _two_positions()
    inputs = b.record.audit["inputs"]
    t1 = inputs["ts"].iloc[5]
    inputs.loc[inputs["ts"] == t1, "digest"] = "different"
    eq = b.record.equity
    eq.loc[eq.index == t1, "equity"] += 1.0  # a difference exactly AT the first differing snapshot
    row = [r for r in compare(a, b, names=("a", "b")).rows if r.level == "L4" and r.quantity == "equity"][0]
    assert row.status == "input", "the snapshot that differs at t1 explains the equity at t1"
    eq.loc[eq.index == inputs["ts"].iloc[2], "equity"] += 1.0  # ... but not a difference BEFORE any input differs
    row = [r for r in compare(a, b, names=("a", "b")).rows if r.level == "L4" and r.quantity == "equity"][0]
    assert row.status == "exceeds"


def test_an_infinite_difference_is_an_infinite_relative_difference():
    mx, mr, i, _ = _measure(np.array([np.inf, 1.0]), np.array([1.0, 1.0]), Tol(1e-5))
    assert mr == np.inf and i == 0


# ------------------------------------------------------------------ the ladder is compared bucket by bucket with the ladder's tolerance
def test_the_delta_ladder_is_audited_per_bucket_and_compared_with_its_own_tolerance():
    from test_tieout_selftest import NUMERIC

    base = copy.deepcopy(NUMERIC)
    base["backtest"]["grid"] = {"start": "2024-06-03", "end": "2024-06-14", "freq": "1b"}
    base["strategy"]["triggers"][0]["actions"] = base["strategy"]["triggers"][0]["actions"][:1]
    ref = {"instruments": {"ois": {"factory": "pricebt.testing.refstack:swap"}}, "market": {"pricers": {"primary": {"wrap": "pricebt.testing.refstack:wrap"}}}}
    _, a = run_stack(base, [ref], audit_measures=("dv01", "delta_ladder"))
    marks = a.record.audit["marks"]
    buckets = [c for c in marks.columns if c.startswith("measure_delta_ladder.")]
    assert len(buckets) >= 8 and "measure_delta_ladder" not in marks.columns, "one column per bucket, not their sum"
    assert marks[buckets].abs().sum(axis=1).max() > 0
    rep = compare(a, a, names=("a", "b"))
    rows = [r for r in rep.rows if r.level == "L2" and r.quantity.startswith("delta_ladder.")]
    assert len(rows) == len(buckets) and all(r.tol_rel == 1e-3 and r.tol_floor == 100.0 and r.status == "exact" for r in rows)


# ------------------------------------------------------------------ the bindings each stack used are disclosed (a stack-owned `bind` can carry a unit conversion or a literal library argument)
def test_the_report_header_discloses_every_stacks_bindings_including_a_hidden_scale():
    scaled = {"instruments": {"fwd": {**SAME["instruments"]["fwd"], "bind": {"dv01": {"target": {"method": "dv01"}, "kwargs": {"ctx": "@ctx"}, "scale": 2.0}}}}}
    res = run_tieout(BASE, {"a": [SAME], "b": [scaled]}, selftest=False)
    ba, bb = res.header["bindings"]["a"]["fwd"], res.header["bindings"]["b"]["fwd"]
    assert ba["dv01"]["scale"] == 1.0 and bb["dv01"]["scale"] == 2.0 and bb["dv01"]["target"] == "method:dv01"
    assert bb["dv01"]["kwargs"] == {"ctx": "@ctx"} and ba["value"]["target"] == bb["value"]["target"]
    l2 = [r for r in res.reports["a_vs_b"].rows if r.level == "L2" and r.quantity == "dv01"][0]
    assert l2.status == "exceeds", "and the doubled dollar delta is what the tie-out finds: the disclosure says WHY"


# ------------------------------------------------------------------ sizes of a risk-sized strategy, and the statistics that are ratios of small numbers
def test_the_ledgers_position_sizes_follow_the_declared_quantity_tolerance():
    a, b, _ = _two_positions()
    b.record.trades["quantity"] = b.record.trades["quantity"] * (1.0 + 1e-4)  # two libraries size the same hedge 1e-4 apart
    strict = [r for r in compare(a, b, names=("a", "b")).rows if r.quantity == "trade_ledger_structure"][0]
    assert strict.status == "structure" and "quantity" in strict.where, "an invariant by default"
    loose = compare(a, b, names=("a", "b"), tolerances=Tolerances({"L1.quantity": 1e-3}))
    assert [r for r in loose.rows if r.quantity == "trade_ledger_structure"][0].status == "exact"
    too_loose_for_it = compare(a, b, names=("a", "b"), tolerances=Tolerances({"L1.quantity": 1e-6}))
    assert [r for r in too_loose_for_it.rows if r.quantity == "trade_ledger_structure"][0].status == "structure"


def test_ratios_of_small_numbers_have_their_own_looser_default_than_the_money_statistics():
    import pandas as pd

    a, b, _ = _two_positions()
    sa = a.summary_stats()
    sb = sa.copy()
    sb["kurt"] = sa["kurt"] * 1.01 if sa["kurt"] == sa["kurt"] and sa["kurt"] != 0 else 0.5
    sa = sa.copy()
    a.summary_stats, b.summary_stats = (lambda s=sa: s), (lambda s=sb: s)
    rows = {r.quantity: r for r in compare(a, b, names=("a", "b")).rows if r.level == "L4"}
    assert rows["stats"].status == "exact" and rows["stats_ratios"].status == "noise", "a 1% kurtosis difference is noise; money statistics are still compared tightly"
    sb["total_pnl"] = sa["total_pnl"] * (1 + 1e-3) if sa["total_pnl"] != 0 else 1.0
    rows = {r.quantity: r for r in compare(a, b, names=("a", "b")).rows if r.level == "L4"}
    assert rows["stats"].status == "exceeds" and "total_pnl" in rows["stats"].where
    assert isinstance(sb, pd.Series)


def test_the_quantity_floor_makes_tiny_sizes_compare_absolutely():
    a, b, _ = _two_positions()
    a.record.trades["quantity"] = a.record.trades["quantity"] * 1e-4  # sizes of the order of 1e-4 ...
    b.record.trades["quantity"] = a.record.trades["quantity"] + 5e-6  # ... that differ by 5e-6: 2% relative, nothing absolutely
    row = lambda tol: [r for r in compare(a, b, names=("a", "b"), tolerances=Tolerances({"L1.quantity": tol})).rows if r.quantity == "trade_ledger_structure"][0].status  # noqa: E731
    assert row({"rel": 1e-3, "floor": 1e-2}) == "exact", "below the floor the tolerance is absolute: 1e-3 x 1e-2 = 1e-5 >= 5e-6"
    assert row({"rel": 1e-3, "floor": 1e-4}) == "structure", "with a floor of 1e-4 it is 1e-7: the same difference is now a real one"


def test_layers_are_also_compared_per_unit_so_a_sizing_difference_is_not_mistaken_for_a_library_difference():
    a, b, _ = _two_positions()
    lay = b.record.audit["layers"]
    lay["amount"] = lay["amount"] * 1.01  # the same per-unit layer, held 1% larger under the other stack
    rep = compare(a, b, names=("a", "b"))
    by = {(r.level, r.quantity): r for r in rep.rows}
    unit = [r for (lv, q), r in by.items() if lv == "L3" and q.endswith("/unit")]
    amount = [r for (lv, q), r in by.items() if lv == "L3" and q in ("carry", "delta", "convexity") and not q.endswith("/unit")]
    assert unit and all(r.status in ("exact", "noise") for r in unit), "per unit the two stacks agree"
    assert amount and any(r.status == "exceeds" for r in amount), "while the amounts differ by the sizing"


# ---- a resolved term that one stack reports and the other does not (found by the bond tie-out through the CLI) ----------------------------------------------------------------
@pytest.mark.parametrize("x, y, same", [
    (1.5, None, False), (None, 1.5, False), (np.float64(1.5), None, False), (None, np.float64(1.5), False),
    (None, None, True), (float("nan"), None, True), (None, float("nan"), True), (float("nan"), float("nan"), True), (np.float64("nan"), float("nan"), True),
    (1.5, 1.5, True), (np.float64(1.5), 1.5, True), (1.5, 1.5 + 1e-9, False), ("USD", "USD", True), ("USD", "EUR", False), ("USD", None, False),
])
def test_resolved_terms_compare_a_missing_value_as_different_from_a_value_and_equal_to_a_missing_one(x, y, same):
    from pricebt.tieout.compare import _same
    assert _same(x, y) is same


# ---- turnover: sum |quantity x entry pv| is currency traded, and for par swaps the entry pv is numerical zero --------------------------------------------------------------------
def _with_turnover(x, y):
    a, b, _ = _two_positions()
    sa = a.summary_stats().copy()
    sb = sa.copy()
    sa["turnover"], sb["turnover"] = x, y
    a.summary_stats, b.summary_stats = (lambda s=sa: s), (lambda s=sb: s)
    return {r.quantity: r for r in compare(a, b, names=("a", "b")).rows if r.level == "L4"}


def test_turnover_of_par_swaps_is_numerical_zero_and_is_compared_absolutely_in_currency():
    """measured on the swap book: 6.9e-8 against 8.0e-8 against 2.2e-7 (currency): a 3x relative difference of nothing. Its own group, `L4.stats_traded`, rel 1e-5 floor 1."""
    rows = _with_turnover(6.9e-8, 2.2e-7)
    assert rows["stats_traded"].status == "noise" and rows["stats_traded"].tol_floor == 1.0
    assert "turnover" not in rows["stats_ratios"].where, "it no longer shares the ratios' tolerance"


def test_turnover_of_real_money_is_still_compared_tightly():
    assert _with_turnover(1.0e6, 1.0e6 * (1 + 1e-3))["stats_traded"].status == "exceeds"
    assert _with_turnover(1.0e6, 1.0e6 * (1 + 1e-7))["stats_traded"].status == "noise"


def test_the_traded_statistics_tolerance_can_be_declared():
    a, b, _ = _two_positions()
    sa = a.summary_stats().copy()
    sb = sa.copy()
    sa["turnover"], sb["turnover"] = 1.0e6, 1.0e6 * (1 + 1e-3)
    a.summary_stats, b.summary_stats = (lambda s=sa: s), (lambda s=sb: s)
    tol = Tolerances({"L4.stats_traded": {"rel": 2e-3, "floor": 1.0, "reason": "test"}})
    rows = {r.quantity: r for r in compare(a, b, names=("a", "b"), tolerances=tol).rows if r.level == "L4"}
    assert rows["stats_traded"].status == "noise"
    assert rows["stats"].status == "exact", "and the turnover is compared ONCE: it is not also a money statistic under the tight default"
