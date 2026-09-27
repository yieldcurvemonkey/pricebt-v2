"""Edge cases of the reference stack that the mutation checks showed to be uncovered (each test names the rule it pins)."""
import datetime as dt

import pandas as pd
import pytest

from pricebt.errors import ConfigError, MarketDataUnavailable
from pricebt.snapshot import CalendarData, FixingsSeries, MarketSnapshot, Quote, QuoteSet, Security, SnapshotPricer
from pricebt.testing import refstack as R
from test_refstack import CONV, HOLIDAY, NY, REF, build, ctx, df_flat, fixings_between, pricer, snapshot
from test_refstack_bond import BOND_CONV, bbuild, bpricer

pytestmark = pytest.mark.core


# ------------------------------------------------------------------ explicit maturity dates
def test_a_consistent_roll_needs_a_whole_number_of_periods():
    p = pricer()
    # 2026-07-04 is a Saturday: it adjusts to Monday 2026-07-06 == the date given, but 30 months is not a whole number of ANNUAL periods -> the date's own roll (6)
    s, _ = build(p, maturity=dt.date(2026, 7, 6))
    assert s.schedule.unadjusted == (dt.date(2024, 1, 4), dt.date(2024, 7, 6), dt.date(2025, 7, 6), dt.date(2026, 7, 6))


def test_a_whole_number_of_periods_is_not_enough_the_date_must_also_adjust_from_the_efffective_roll():
    p = pricer()
    # 24 months, but 2026-01-04 (Sunday) adjusts to the 5th, not to the 8th given: the date's own roll (8)
    s, _ = build(p, maturity=dt.date(2026, 1, 8))
    assert s.schedule.unadjusted == (dt.date(2024, 1, 4), dt.date(2024, 1, 8), dt.date(2025, 1, 8), dt.date(2026, 1, 8)), "a 4-day short front stub, then annual periods on the 8th"


# ------------------------------------------------------------------ day count
def test_act365f_changes_the_accrual_fractions_and_the_pv_by_hand():
    conv = {**CONV, "day_count": "act365f"}
    p = pricer()
    b = R.swap_factory(p, p.ts, terms={"side": "pay", "direction": 1, "effective": "spot", "maturity": "1Y", "notional": 1e7, "fixed_rate": 4.2}, conventions=conv)
    s = b.obj
    a, e, pay = dt.date(2024, 1, 4), dt.date(2025, 1, 7), dt.date(2025, 1, 9)
    tau = (e - a).days / 365.0
    assert s.schedule.taus[0] == pytest.approx(tau)
    assert s.value(ctx=ctx(p)).pv == pytest.approx(1e7 * (df_flat(a) / df_flat(e) - 1.0 - 0.042 * tau) * df_flat(pay), rel=1e-12)


def test_act365f_compounds_the_published_fixings_on_the_same_basis():
    ref = dt.date(2024, 3, 1)
    fx = fixings_between(dt.date(2024, 1, 2), ref)
    p = pricer(ref=ref, fixings=fx)
    conv = {**CONV, "day_count": "act365f"}
    s = R.swap_factory(pricer(), pricer().ts, terms={"side": "pay", "direction": 1, "effective": "spot", "maturity": "1Y", "notional": 1e7, "fixed_rate": 4.2}, conventions=conv).obj
    growth = 1.0
    d = dt.date(2024, 1, 4)
    while d < ref:
        if d.weekday() < 5 and d != HOLIDAY:
            nxt = d + dt.timedelta(days=1)
            while nxt.weekday() >= 5 or nxt == HOLIDAY:
                nxt += dt.timedelta(days=1)
            growth *= 1.0 + fx[d] / 100.0 * (nxt - d).days / 365.0
        d += dt.timedelta(days=1)
    a, e, pay = dt.date(2024, 1, 4), dt.date(2025, 1, 7), dt.date(2025, 1, 9)
    expected = 1e7 * (growth * df_flat(ref, ref) / df_flat(e, ref) - 1.0 - 0.042 * (e - a).days / 365.0) * df_flat(pay, ref)
    assert s.value(ctx=ctx(p)).pv == pytest.approx(expected, rel=1e-12)


# ------------------------------------------------------------------ fixings
def test_the_missing_fixing_check_covers_the_day_before_the_reference_date():
    ref = dt.date(2024, 3, 1)
    fx = fixings_between(dt.date(2024, 1, 2), ref)
    del fx[dt.date(2024, 2, 29)]
    s, _ = build(pricer(), maturity="1Y")
    with pytest.raises(MarketDataUnavailable, match=r"1 overnight fixings missing.*2024-02-29"):
        s.value(ctx=ctx(pricer(ref=ref, fixings=fx)))


def test_a_decimal_fixings_series_is_read_as_decimals():
    ref = dt.date(2024, 3, 1)
    pct = fixings_between(dt.date(2024, 1, 2), ref)
    base = snapshot(ref=ref, fixings=pct)
    dec = FixingsSeries("f", tuple(sorted(pct)), tuple(pct[d] / 100.0 for d in sorted(pct)), "decimal")
    as_decimal = MarketSnapshot(base.ts, ref, base.curves, {"f": dec}, None, base.calendars)
    s, _ = build(pricer(), maturity="1Y")
    assert s.value(ctx=ctx(R.wrap(SnapshotPricer(as_decimal)))).pv == pytest.approx(s.value(ctx=ctx(pricer(ref=ref, fixings=pct))).pv, rel=1e-13)


def test_a_flow_paid_on_the_reference_date_is_in_the_value_and_not_in_the_cash_window():
    s, _ = build(pricer(), maturity="1Y")
    pay_day = s.schedule.pays[0]
    before = dt.date(2025, 1, 8)
    on = R.wrap(SnapshotPricer(snapshot(ref=pay_day, fixings=fixings_between(dt.date(2024, 1, 2), pay_day))))
    prev = R.wrap(SnapshotPricer(snapshot(ref=before, fixings=fixings_between(dt.date(2024, 1, 2), before))))
    v = s.value(ctx=ctx(on, prev=prev))
    assert v.pv != 0.0 and v.cash == 0.0, "it is paid ON the reference date: inside the value now, swept as cash at the next point"


# ------------------------------------------------------------------ ladder tenors
def test_bad_ladder_tenor_sets_say_why():
    p = pricer()
    s, _ = build(p, maturity="5Y", fixed_rate="par")
    with pytest.raises(ConfigError, match="duplicates"):
        s.delta_ladder(ctx=ctx(p), tenors=["5Y", "5Y"])
    with pytest.raises(ConfigError, match="duplicate or unordered"):
        s.delta_ladder(ctx=ctx(p), tenors=["10Y", "5Y"])


# ------------------------------------------------------------------ bond
def test_a_clean_quote_between_coupons_is_solved_with_the_accrued_interest_added_back():
    sec = Security("C5", 5.0, dt.date(2020, 5, 15), dt.date(2030, 5, 15))
    ref = dt.date(2024, 8, 15)
    at_yield = bpricer(ref, [sec], {"C5": Quote(ytm=6.0)})
    m = bbuild(at_yield, "C5").mark(at_yield)
    assert m["accrued"] > 1.0
    clean = bpricer(ref, [sec], {"C5": Quote(clean=m["clean"])})
    assert bbuild(clean, "C5").mark(clean)["ytm"] == pytest.approx(6.0, abs=1e-10)


def test_an_impossible_clean_price_has_no_yield():
    sec = Security("C5", 5.0, dt.date(2020, 5, 15), dt.date(2030, 5, 15))
    p = bpricer(dt.date(2024, 8, 15), [sec], {"C5": Quote(clean=1e9)})
    with pytest.raises(ConfigError, match="no yield"):
        bbuild(p, "C5").mark(p)


def test_financing_over_a_negative_interval_is_refused():
    sec = Security("C5", 5.0, dt.date(2020, 5, 15), dt.date(2030, 5, 15))
    p = bpricer(dt.date(2024, 6, 3), [sec], {"C5": Quote(ytm=6.0)})
    b = bbuild(p, "C5", repo={"gc_rate": 5.0})
    with pytest.raises(ValueError, match="prev_ts <= ts"):
        b.financing(p, p.ts - pd.Timedelta(days=1), p.ts)


def test_the_bond_carry_includes_the_coupon_cash_and_the_funding_when_the_yield_is_unchanged():
    sec = Security("C5", 5.0, dt.date(2020, 5, 15), dt.date(2030, 5, 15))
    q = {"C5": Quote(ytm=6.0)}
    p0, p1 = bpricer(dt.date(2024, 11, 13), [sec], q), bpricer(dt.date(2024, 11, 18), [sec], q)  # the 15th falls inside
    b = bbuild(p0, "C5", notional=1e7, repo={"gc_rate": 5.0})
    c = ctx(p1, prev=p0)
    v = b.value(ctx=c)
    assert v.cash == pytest.approx(1e7 / 100 * 2.5) and v.financing < 0
    assert b.carry(ctx=c) == pytest.approx(v.pv - b.value(ctx=ctx(p0)).pv + v.cash + v.financing, rel=1e-12)
