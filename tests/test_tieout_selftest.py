"""Harness self-tests X5b and X5c (spec 6.8), on the LIBRARY-FREE reference stack so no pricing library is needed:

X5a  the same stack twice differs by exactly zero at every level            (tests/test_tieout.py, and again here on real swaps)
X5b  a deliberately mutated convention on ONE side is detected at L1 and reported at the right place
X5c  the reference stack agrees with a hand-derived answer to machine precision (tests/test_refstack.py: closed forms; tests/test_refstack_golden.py: golden numbers)

The base config is shared by every stack; a stack is an overlay that sets only the factory / bind / wrap.
"""
import copy

import pytest

from pricebt.tieout import compare, run_tieout, run_stack
from pricebt.testing import refstack as R

pytestmark = pytest.mark.core

CONV = {**R.USD_SOFR_OIS_CONVENTIONS, "calendar": "wk"}
BASE = {
    "name": "refstack_swap",
    "registry": {"allow": ["pricebt", "tieout_helpers"]},
    "backtest": {
        "tz": "America/New_York", "grid": {"start": "2024-06-03", "end": "2024-08-30", "freq": "1b"}, "progress": {"show": False},
        "attribution": {"layers": ["carry", "roll", "delta", "convexity"], "cadence": "eod"}, "measures": ["dv01", "pv"], "vector_measures": ["delta_ladder"],
    },
    "market": {"mdps": {"syn": {"type": "pricebt.testing.synthetic:SyntheticMarket", "kwargs": {"start": "2024-06-03", "end": "2024-08-30", "calendar_name": "wk", "seed": 7, "history_years": 1}}},
               "pricers": {"primary": {"mdp": "syn"}}},
    "instruments": {"ois": {"asset_class": "swap", "factory": "pricebt.testing.refstack:swap", "conventions": CONV}},
    "strategy": {"triggers": [{"type": "periodic", "frequency": "1m", "start_date": "2024-06-03", "actions": [
        {"type": "add_trade", "priceables": {"instrument": "ois", "terms": {"side": "pay", "maturity": "2Y", "notional": 1e7, "fixed_rate": "par"}}, "trade_duration": "2m"},
        {"type": "add_trade", "priceables": {"instrument": "ois", "terms": {"side": "receive", "maturity": "5Y", "notional": 5e6, "fixed_rate": "par"}}, "trade_duration": "2m"}]}]},
}
NUMERIC = copy.deepcopy(BASE)  # the same trades at fixed numeric rates: no `par` to resolve, so the terms are identical whatever the library does with the day count
for _a in NUMERIC["strategy"]["triggers"][0]["actions"]:
    _a["priceables"]["terms"]["fixed_rate"] = 3.9 if _a["priceables"]["terms"]["side"] == "pay" else 4.1
REF = {"instruments": {"ois": {"factory": "pricebt.testing.refstack:swap"}}, "market": {"pricers": {"primary": {"wrap": "pricebt.testing.refstack:wrap"}}}}
WRONG_DAY_COUNT = {"instruments": {"ois": {"factory": "tieout_helpers:wrong_day_count_swap"}}, "market": {"pricers": {"primary": {"wrap": "pricebt.testing.refstack:wrap"}}}}
SHIFTED_CALENDAR = {"instruments": {"ois": {"factory": "pricebt.testing.refstack:swap"}}, "market": {"pricers": {"primary": {"wrap": "tieout_helpers:wrap_with_an_extra_holiday"}}}}


@pytest.fixture(scope="module")
def runs():
    return run_tieout(BASE, {"ref": [REF], "twin": [REF], "day_count": [WRONG_DAY_COUNT], "calendar": [SHIFTED_CALENDAR]}, selftest=True)


@pytest.fixture(scope="module")
def numeric_runs():
    return run_tieout(NUMERIC, {"ref": [REF], "day_count": [WRONG_DAY_COUNT]}, selftest=False)


def test_x5a_on_real_swaps_the_same_stack_twice_is_exactly_zero(runs):
    rep = runs.reports["ref_vs_twin"]
    assert runs.header["selftest"] == "passed" and rep.passed
    assert all(r.max_abs == 0.0 for r in rep.rows), [(r.level, r.quantity, r.max_abs) for r in rep.rows if r.max_abs]
    assert rep.max_abs("L1", "pv") == 0.0 and rep.max_abs("L2", "dv01") == 0.0 and {r.quantity for r in rep.rows if r.level == "L3"} >= {"carry", "roll", "delta", "convexity", "tay_delta", "tay_convexity"}
    assert runs.results["ref"].reconcile().ok and len(runs.results["ref"].positions) >= 4


def test_x5b_a_flipped_day_count_on_one_side_is_detected_at_l1_and_located(numeric_runs):
    rep = numeric_runs.reports["ref_vs_day_count"]
    assert not rep.passed
    by = {(r.level, r.quantity): r for r in rep.rows}
    assert by[("L0", "resolved_terms")].status == "exact" and by[("L0", "conventions_digest")].status == "exact" and by[("L0", "snapshot_digests")].status == "exact", (
        "the inputs, the resolved terms and the shared conventions block are identical: nothing but the library's reading of the day count differs")
    pv = by[("L1", "pv")]
    assert pv.status == "exceeds" and pv.max_rel > 1e-6 and "position=" in pv.where and "ts=" in pv.where, "detected at L1 and located"
    assert by[("L2", "dv01")].status == "exceeds", "the annuity uses the accrual fractions"
    assert by[("L4", "equity")].status == "exceeds"
    assert rep.offenders and rep.offenders[0].level in ("L1", "L2", "L3", "L4") and rep.offenders[0].diff > 0


def test_x5b_when_par_is_resolved_the_same_mutation_shows_first_in_the_resolved_terms(runs):
    rep = runs.reports["ref_vs_day_count"]
    assert not rep.passed
    terms = [r for r in rep.rows if r.quantity == "resolved_terms"][0]
    assert terms.status == "input" and "fixed_rate" in terms.where, "the par rate the library resolves already differs: L0 explains everything downstream"
    assert [r for r in rep.rows if r.level == "L1" and r.quantity == "pv"][0].status == "input"


def test_x5b_a_shifted_holiday_calendar_on_one_side_is_reported_first_at_l0_as_a_date_difference(runs):
    rep = runs.reports["ref_vs_calendar"]
    assert not rep.passed
    terms = [r for r in rep.rows if r.quantity == "resolved_terms"][0]
    assert terms.status == "input" and ("effective" in terms.where or "maturity" in terms.where), "differing resolved dates come FIRST (T4, X2)"
    assert [r for r in rep.rows if r.quantity == "snapshot_digests"][0].status == "exact", "the snapshots are the same; the library's use of the calendar is what differs"
    pv = [r for r in rep.rows if r.level == "L1" and r.quantity == "pv"][0]
    assert pv.status in ("input", "exact"), "any PV difference is attributed to the L0 date difference"


def test_a_convention_that_no_stack_can_honour_is_a_load_error_not_a_silent_difference():
    from pricebt.errors import ConfigError

    cfg = copy.deepcopy(BASE)
    cfg["instruments"]["ois"]["conventions"] = {**CONV, "compounding": "daily_average"}
    with pytest.raises(ConfigError, match="does not implement it"):
        run_stack(cfg, [REF])
