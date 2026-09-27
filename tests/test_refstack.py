"""The reference stack (spec A4) is the KNOWN ANSWER of the tie-out harness, so every formula it implements is re-derived here by hand, with plain python floats and
`math`, independent of the module's own code paths (spec X5c)."""
import datetime as dt
import math

import pandas as pd
import pytest

from pricebt.contracts.schema import SchemaRegistry
from pricebt.errors import ConfigError, MarketDataUnavailable
from pricebt.pricable import MarkContext
from pricebt.snapshot import CalendarData, CurveSnapshot, FixingsSeries, MarketSnapshot, SnapshotPricer
from pricebt.testing import refstack as R

pytestmark = pytest.mark.core

NY = "America/New_York"
REF = dt.date(2024, 1, 2)  # a Tuesday
HOLIDAY = dt.date(2025, 1, 6)  # a Monday: pushes a following/modified-following date on
RATE = 0.04  # continuously compounded, actual/365: DF(d) = exp(-RATE * days / 365)
CONV = dict(R.USD_SOFR_OIS_CONVENTIONS, calendar="wk")


def df_flat(d, ref=REF, rate=RATE):
    return math.exp(-rate * (d - ref).days / 365.0)


def snapshot(ref=REF, *, rate=RATE, nodes=(0, 30, 91, 182, 365, 730, 1461, 3653, 7305, 10958), fixings=None, holidays=(HOLIDAY,)):
    """A flat continuously-compounded curve on `nodes` (log-linear interpolation reproduces it EXACTLY between nodes), a weekend+holiday calendar named `wk`."""
    dates = [ref + dt.timedelta(days=int(n)) for n in nodes]
    curve = CurveSnapshot("c", ref, tuple(dates), tuple(df_flat(d, ref, rate) for d in dates))
    fx = {} if fixings is None else {"f": FixingsSeries("f", tuple(sorted(fixings)), tuple(fixings[d] for d in sorted(fixings)), "percent")}
    ts = pd.Timestamp(f"{ref} 17:00", tz=NY)
    return MarketSnapshot(ts, ref, {"c": curve}, fx, None, {"wk": CalendarData("wk", tuple(holidays))})


def pricer(**kw):
    sp = SnapshotPricer(snapshot(**kw))
    return R.wrap(sp)


def ctx(p, prev=None):
    return MarkContext(p.ts, p, {"primary": p}, None if prev is None else prev.ts, prev, p.ts if prev is None else prev.ts, p if prev is None else prev, {}, {})


def swap_terms(**kw):
    return {"side": "pay", "direction": 1, "effective": "spot", "maturity": "1Y", "notional": 1e7, "fixed_rate": 4.2, **kw}


def build(p, **kw):
    b = R.swap_factory(p, p.ts, terms=swap_terms(**kw), conventions=CONV)
    return b.obj, b.terms


# ------------------------------------------------------------------ the curve
def test_the_curve_is_log_linear_between_nodes_and_flat_forward_beyond_the_last():
    c = R.RefCurve(REF, [REF, REF + dt.timedelta(days=100), REF + dt.timedelta(days=300)], [1.0, 0.99, 0.95])
    assert c.df(REF + dt.timedelta(days=100)) == pytest.approx(0.99, abs=1e-15)
    assert c.df(REF + dt.timedelta(days=50)) == pytest.approx(math.sqrt(0.99), rel=1e-14), "the geometric mean of the two nodes: linear in the log at the mid-point"
    slope = math.log(0.95 / 0.99) / 200.0
    assert c.df(REF + dt.timedelta(days=400)) == pytest.approx(0.95 * math.exp(slope * 100.0), rel=1e-14), "beyond the last node: the last segment's forward continues"
    with pytest.raises(ConfigError, match="before the curve's reference date"):
        c.df(REF - dt.timedelta(days=1))
    with pytest.raises(ConfigError):
        R.RefCurve(REF, [REF, REF], [1.0, 1.0])
    with pytest.raises(ConfigError):
        R.RefCurve(REF, [REF + dt.timedelta(days=1), REF + dt.timedelta(days=2)], [1.0, 0.9])


# ------------------------------------------------------------------ dates
def test_add_months_clamps_to_the_month_and_keeps_the_roll_day():
    assert R.add_months(dt.date(2024, 1, 31), 1, 31) == dt.date(2024, 2, 29)
    assert R.add_months(dt.date(2024, 2, 29), 12, 29) == dt.date(2025, 2, 28)
    assert R.add_months(dt.date(2024, 2, 29), 24, 29) == dt.date(2026, 2, 28)
    assert R.add_months(dt.date(2024, 3, 10), -13, 10) == dt.date(2023, 2, 10)
    assert R.tenor_months("18M") == 18 and R.tenor_months("10y") == 120
    with pytest.raises(ConfigError):
        R.tenor_months("6W")


def test_a_one_year_swap_has_the_hand_derived_dates():
    p = pricer()
    s, resolved = build(p)
    # spot = 2 business days after Tue 2024-01-02; the unadjusted end Sat 2025-01-04 is moved to Mon 2025-01-06 (modified following)
    assert resolved["effective"] == dt.date(2024, 1, 4) and s.schedule.unadjusted == (dt.date(2024, 1, 4), dt.date(2025, 1, 4))
    assert s.schedule.ends == (dt.date(2025, 1, 7),), "2025-01-06 is the test holiday, so modified following goes on to Tuesday the 7th"
    assert s.schedule.pays == (dt.date(2025, 1, 9),), "two business days after the accrual end"
    assert s.schedule.taus[0] == pytest.approx((dt.date(2025, 1, 7) - dt.date(2024, 1, 4)).days / 360.0)


def test_multi_year_schedule_and_the_short_front_stub_of_an_eighteen_month_swap():
    p = pricer()
    s2, _ = build(p, maturity="2Y")
    assert s2.schedule.unadjusted == (dt.date(2024, 1, 4), dt.date(2025, 1, 4), dt.date(2026, 1, 4))
    assert s2.schedule.ends == (dt.date(2025, 1, 7), dt.date(2026, 1, 5)), "Sunday 2026-01-04 goes to Monday the 5th"
    s18, _ = build(p, maturity="18M")
    assert s18.schedule.unadjusted == (dt.date(2024, 1, 4), dt.date(2024, 7, 4), dt.date(2025, 7, 4)), "the odd 6-month period is at the FRONT"
    assert s18.schedule.starts[1] == dt.date(2024, 7, 4) and s18.schedule.taus[0] == pytest.approx(182 / 360.0)


def test_effective_dates_spot_forward_tenor_and_explicit():
    p = pricer()
    assert build(p)[1]["effective"] == dt.date(2024, 1, 4)
    assert build(p, effective="1y")[1]["effective"] == dt.date(2025, 1, 7), "spot plus a year on the spot's day-of-month, adjusted"
    assert build(p, effective=dt.date(2024, 2, 3))[1]["effective"] == dt.date(2024, 2, 5), "an explicit non-business date is adjusted"
    assert build(p, effective=pd.Timestamp("2024-02-05"))[1]["effective"] == dt.date(2024, 2, 5)
    with pytest.raises(ConfigError):
        build(p, effective="soon")


def test_an_explicit_maturity_date_keeps_the_effective_roll_when_it_is_consistent_and_its_own_otherwise():
    p = pricer()
    consistent, _ = build(p, maturity=dt.date(2026, 1, 5))  # 2026-01-04 (a Sunday) adjusted
    assert consistent.schedule.unadjusted == (dt.date(2024, 1, 4), dt.date(2025, 1, 4), dt.date(2026, 1, 4))
    odd, _ = build(p, maturity=dt.date(2026, 3, 15))
    assert odd.schedule.unadjusted == (dt.date(2024, 1, 4), dt.date(2024, 3, 15), dt.date(2025, 3, 15), dt.date(2026, 3, 15)), "not a whole number of periods: the maturity's own roll, short front stub"


# ------------------------------------------------------------------ hand-derived values
def hand_pv(p_dates, sign=1, notional=1e7, K=4.2 / 100, ref=REF, rate=RATE):
    """PV = sign * N * sum_i (DF(a_i)/DF(e_i) - 1 - K tau_i) * DF(pay_i) for periods that have not started."""
    total = 0.0
    for a, e, pay in p_dates:
        total += (df_flat(a, ref, rate) / df_flat(e, ref, rate) - 1.0 - K * (e - a).days / 360.0) * df_flat(pay, ref, rate)
    return sign * notional * total


PERIODS_1Y = [(dt.date(2024, 1, 4), dt.date(2025, 1, 7), dt.date(2025, 1, 9))]
PERIODS_2Y = [(dt.date(2024, 1, 4), dt.date(2025, 1, 7), dt.date(2025, 1, 9)), (dt.date(2025, 1, 7), dt.date(2026, 1, 5), dt.date(2026, 1, 7))]


def test_the_pv_of_a_single_period_swap_is_the_closed_form_and_a_receiver_is_its_mirror():
    p = pricer()
    pay, _ = build(p)
    rec, _ = build(p, side="receive", direction=-1)
    v = pay.value(ctx=ctx(p))
    assert v.pv == pytest.approx(hand_pv(PERIODS_1Y), rel=1e-12) and v.cash == 0.0 and v.financing == 0.0
    assert rec.value(ctx=ctx(p)).pv == pytest.approx(-v.pv, rel=1e-14)


def test_two_periods_the_par_rate_and_the_fixed_leg_pv01_by_hand():
    p = pricer()
    s, _ = build(p, maturity="2Y")
    assert s.value(ctx=ctx(p)).pv == pytest.approx(hand_pv(PERIODS_2Y), rel=1e-12)
    growth = [df_flat(a) / df_flat(e) - 1.0 for a, e, _ in PERIODS_2Y]
    ann = [(e - a).days / 360.0 * df_flat(pay) for a, e, pay in PERIODS_2Y]
    assert s.rate(ctx=ctx(p)) == pytest.approx(100.0 * sum(g * df_flat(pay) for g, (_, _, pay) in zip(growth, PERIODS_2Y)) / sum(ann), rel=1e-12)
    assert s.dv01(ctx=ctx(p)) == pytest.approx(1e7 * sum(ann) * 1e-4, rel=1e-12), "a payer's dv01 is positive: N * annuity * 1bp"
    s_par, resolved = build(p, maturity="2Y", fixed_rate="par")
    assert resolved["fixed_rate"] == pytest.approx(s.rate(ctx=ctx(p)), rel=1e-14) and s_par.value(ctx=ctx(p)).pv == pytest.approx(0.0, abs=1e-6), "a swap struck at par is worth zero"


def test_par_spread_bp_shifts_the_resolved_rate_and_other_extras_are_refused():
    p = pricer()
    par = build(p, maturity="2Y", fixed_rate="par")[1]["fixed_rate"]
    assert build(p, maturity="2Y", fixed_rate="par", extras={"par_spread_bp": 25})[1]["fixed_rate"] == pytest.approx(par + 0.25, rel=1e-13)
    with pytest.raises(ConfigError, match="extras"):
        build(p, extras={"mystery": 1})
    with pytest.raises(ConfigError, match="token 'par'"):
        build(p, fixed_rate="mid")


def test_a_swap_priced_at_a_different_flat_rate_matches_the_closed_form_again():
    p = pricer(rate=0.055)
    s, _ = build(p, maturity="2Y")
    assert s.value(ctx=ctx(p)).pv == pytest.approx(hand_pv(PERIODS_2Y, rate=0.055), rel=1e-12)


# ------------------------------------------------------------------ a started swap over published fixings
def business_days(a, b):
    d, out = a, []
    while d < b:
        if d.weekday() < 5 and d != HOLIDAY:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def fixings_between(start, end_exclusive, base=4.0, step=0.01):
    days = business_days(start, end_exclusive)
    return {d: base + step * i for i, d in enumerate(days)}


def next_business_day(d):
    d += dt.timedelta(days=1)
    while d.weekday() >= 5 or d == HOLIDAY:
        d += dt.timedelta(days=1)
    return d


def test_a_started_swap_compounds_the_published_fixings_then_the_forward_curve():
    ref = dt.date(2024, 3, 1)  # a Friday, eight weeks after the effective date
    fx = fixings_between(dt.date(2024, 1, 2), ref)
    p = pricer(ref=ref, fixings=fx)
    early = pricer()  # the trade date: build the swap there
    s, _ = build(early, maturity="1Y")
    started = [d for d in fx if d >= dt.date(2024, 1, 4)]
    growth = 1.0
    for d in started:
        growth *= 1.0 + fx[d] / 100.0 * (next_business_day(d) - d).days / 360.0
    a, e, pay = dt.date(2024, 1, 4), dt.date(2025, 1, 7), dt.date(2025, 1, 9)
    proj = df_flat(ref, ref) / df_flat(e, ref)
    tau = (e - a).days / 360.0
    expected = 1e7 * (growth * proj - 1.0 - 4.2 / 100 * tau) * df_flat(pay, ref)
    assert s.value(ctx=ctx(p)).pv == pytest.approx(expected, rel=1e-12)


def test_a_missing_fixing_is_an_error_naming_the_first_missing_day_and_no_fixings_at_all_too():
    ref = dt.date(2024, 3, 1)
    fx = fixings_between(dt.date(2024, 1, 2), ref)
    del fx[dt.date(2024, 2, 14)]
    s, _ = build(pricer(), maturity="1Y")
    with pytest.raises(MarketDataUnavailable, match=r"1 overnight fixings missing.*2024-02-14"):
        s.value(ctx=ctx(pricer(ref=ref, fixings=fx)))
    with pytest.raises(MarketDataUnavailable, match="no fixings"):
        s.value(ctx=ctx(pricer(ref=ref)))


def test_a_flow_paid_on_the_reference_date_is_inside_the_value_and_swept_as_cash_the_next_day():
    early = pricer()
    s, _ = build(early, maturity="1Y")
    pay_day = s.schedule.pays[0]  # 2025-01-09, a Thursday
    fx = fixings_between(dt.date(2024, 1, 2), pay_day)
    on_pay = pricer(ref=pay_day, fixings=fx)
    v_on = s.value(ctx=ctx(on_pay))
    assert v_on.pv != 0.0, "still inside the value on the payment date"
    nxt = next_business_day(pay_day)
    fx2 = fixings_between(dt.date(2024, 1, 2), nxt)
    after = pricer(ref=nxt, fixings=fx2)
    v_after = s.value(ctx=ctx(after, prev=on_pay))
    assert v_after.pv == 0.0 and v_after.cash == pytest.approx(v_on.pv / df_flat(pay_day, pay_day), rel=1e-12), "matured: zero value; the flow is swept as cash at face"
    assert s.rate(ctx=ctx(after)) != s.rate(ctx=ctx(after)) and s.dv01(ctx=ctx(after)) == 0.0, "no par rate and no risk once it has matured"


# ------------------------------------------------------------------ the ladder, dv01 of a started swap and gamma
def test_a_pillar_swap_has_all_of_its_risk_in_its_own_bucket():
    p = pricer()
    s, _ = build(p, maturity="5Y", fixed_rate="par")
    lad = s.delta_ladder(ctx=ctx(p))
    assert set(lad) == set(R.DEFAULT_TENORS) and lad["5Y"] > 0
    assert sum(abs(v) for k, v in lad.items() if k != "5Y") < 1e-6 * lad["5Y"], "a par 5Y swap reprices at any bump of the OTHER pillars (to the bootstrap's precision)"
    assert sum(lad.values()) == pytest.approx(s.dv01(ctx=ctx(p)), rel=2e-3), "the ladder's sum is the parallel dv01 (annuity vs par-curve response differ ~1e-3)"


def test_ladder_of_a_receiver_is_the_negative_and_off_pillar_risk_sits_in_the_neighbours():
    p = pricer()
    pay, _ = build(p, maturity="4Y", fixed_rate="par")
    rec, _ = build(p, maturity="4Y", fixed_rate="par", side="receive", direction=-1)
    lp, lr = pay.delta_ladder(ctx=ctx(p)), rec.delta_ladder(ctx=ctx(p))
    assert lr == {k: pytest.approx(-v, abs=1e-9) for k, v in lp.items()}
    assert lp["3Y"] > 0 and lp["5Y"] > 0 and lp["3Y"] + lp["5Y"] > 0.95 * sum(lp.values()), "4Y sits between the 3Y and 5Y pillars, which carry almost all of its risk"
    assert abs(lp["7Y"]) < 1e-9 * lp["5Y"] and abs(lp["10Y"]) < 1e-9 * lp["5Y"], "a pillar beyond the swap's last date cannot move its value"


def test_the_ladder_keys_are_exactly_the_requested_tenors_and_bad_tenors_are_refused():
    p = pricer()
    s, _ = build(p, maturity="5Y", fixed_rate="par")
    assert list(s.delta_ladder(ctx=ctx(p), tenors=["2y", "5Y"])) == ["2Y", "5Y"]
    for bad in ([], ["5Y", "5Y"], ["front"]):
        with pytest.raises(ConfigError):
            s.delta_ladder(ctx=ctx(p), tenors=bad)


def test_the_risk_curve_reprices_every_pillar_at_the_dense_par_rate():
    p = pricer(rate=0.03)
    rc = R._risk_curves(p, R.swap_conventions(CONV), R.DEFAULT_TENORS)
    for tenor, par in zip(rc.tenors, rc.pars):
        s, resolved = build(p, maturity=tenor, fixed_rate=par * 100.0)
        assert s.npv(p, rc.base, REF) == pytest.approx(0.0, abs=1e-5), tenor  # unit-notional 1e7: 1e-12 relative
    assert len(rc.ups) == len(rc.dns) == len(R.DEFAULT_TENORS)


def test_a_started_swaps_dv01_is_its_ladder_sum_not_the_annuity():
    ref = dt.date(2024, 10, 1)
    fx = fixings_between(dt.date(2024, 1, 2), ref)
    p = pricer(ref=ref, fixings=fx)
    s, _ = build(pricer(), maturity="2Y", fixed_rate=4.0)
    c = ctx(p)
    assert s.dv01(ctx=c) == pytest.approx(sum(s.delta_ladder(ctx=c).values()), rel=1e-14)
    assert s.dv01(ctx=c) != pytest.approx(s.annuity_dv01(p.curve, ref), rel=1e-3), "the annuity keeps the accrued coupon that no longer moves with rates"


def test_gamma_is_the_second_difference_of_a_parallel_par_bump_and_zero_measures_shift_the_dense_zeros():
    p = pricer()
    s, _ = build(p, maturity="10Y", fixed_rate=3.0)
    rc = R._risk_curves(p, R.swap_conventions(CONV), R.DEFAULT_TENORS)
    v = lambda c: s.npv(p, c, REF)  # noqa: E731
    assert s.gamma(ctx=ctx(p)) == pytest.approx(v(rc.parallel_up) + v(rc.parallel_dn) - 2 * v(rc.base), rel=1e-14)
    assert s.gamma(ctx=ctx(p)) < 0, "a payer of fixed is SHORT convexity in par-rate space: its annuity shrinks as rates rise (the golden payer's convexity layer is negative too)"
    rec, _ = build(p, maturity="10Y", fixed_rate=3.0, side="receive", direction=-1)
    assert rec.gamma(ctx=ctx(p)) == pytest.approx(-s.gamma(ctx=ctx(p)), rel=1e-13)
    z_shift = 1e-4
    up, dn = v(R._zero_shifted(p.curve, z_shift)), v(R._zero_shifted(p.curve, -z_shift))
    assert s.dv01_zero(ctx=ctx(p)) == pytest.approx(0.5 * (v(R._zero_shifted(p.curve, 0.5e-4)) - v(R._zero_shifted(p.curve, -0.5e-4))) * 2, rel=1e-14)
    assert s.gamma_zero(ctx=ctx(p)) == pytest.approx(up - 2 * v(p.curve) + dn, rel=1e-14)


# ------------------------------------------------------------------ layers
def two_worlds(rate0=0.04, rate1=0.0415):
    """Two snapshots six days apart (a Tuesday and the next Monday) with published fixings before each."""
    ref1 = dt.date(2024, 1, 9)
    fx = fixings_between(dt.date(2023, 12, 1), ref1)
    p0 = pricer(rate=rate0, fixings=fixings_between(dt.date(2023, 12, 1), REF))
    p1 = pricer(ref=ref1, rate=rate1, fixings=fx)
    return p0, p1


def test_the_layers_sum_with_the_residual_to_the_interval_pnl_for_a_started_swap():
    p0, p1 = two_worlds()
    s, _ = build(pricer(ref=dt.date(2023, 12, 20), fixings=fixings_between(dt.date(2023, 11, 1), dt.date(2023, 12, 20))), maturity="3Y", fixed_rate=3.5)
    c = ctx(p1, prev=p0)
    d = s.decomposition(c)
    v0, v1 = s.value(ctx=ctx(p0)).pv, s.value(ctx=c)
    assert d["V0"] == pytest.approx(v0, rel=1e-13) and d["V1"] == pytest.approx(v1.pv, rel=1e-13) and d["cash"] == pytest.approx(v1.cash, abs=1e-9)
    total = d["carry"] + d["roll"] + d["delta"] + d["convexity"] + d["residual"]
    assert total == pytest.approx(v1.pv + v1.cash - v0, rel=1e-12, abs=1e-6), "the layers plus the residual are exactly the interval P&L"
    assert abs(d["residual"]) < 1e-3 * abs(v1.pv - v0) + 1.0, "the directional Taylor expansion explains the move (residual is the third order)"
    assert abs(d["delta"]) > 100 * abs(d["convexity"]) > 0


def test_a_parallel_shift_of_the_curve_is_delta_and_convexity_and_nothing_else():
    """With an unchanged calendar date the carry and roll of a fresh, flat-curve swap are zero-ish, and a parallel zero shift is explained by delta plus convexity."""
    p0 = pricer(rate=0.04)
    p1 = R.wrap(SnapshotPricer(snapshot(rate=0.0425), ts=p0.ts + pd.Timedelta(hours=1)))  # same reference date: N = 0
    s, _ = build(p0, maturity="5Y", fixed_rate=4.0)
    d = s.decomposition(ctx(p1, prev=p0))
    assert d["carry"] == pytest.approx(0.0, abs=1e-6) and d["roll"] == pytest.approx(0.0, abs=1e-6)
    assert d["delta"] == pytest.approx(s.dv01_zero(ctx=ctx(p0)) * 25.0, rel=5e-3)
    assert d["convexity"] == pytest.approx(0.5 * s.gamma_zero(ctx=ctx(p0)) * 25.0 ** 2, rel=5e-2)
    assert abs(d["residual"]) < 2e-3 * abs(d["delta"])


def test_the_layers_of_a_matured_swap_are_zero_and_time_going_backwards_is_refused():
    p0, p1 = two_worlds()
    s, _ = build(pricer(), maturity="3M")
    ended = R.wrap(SnapshotPricer(snapshot(ref=dt.date(2024, 9, 3), fixings=fixings_between(dt.date(2024, 1, 2), dt.date(2024, 9, 3)))))
    ended2 = R.wrap(SnapshotPricer(snapshot(ref=dt.date(2024, 9, 4), fixings=fixings_between(dt.date(2024, 1, 2), dt.date(2024, 9, 4)))))
    d = s.decomposition(ctx(ended2, prev=ended))
    assert all(v == 0.0 for v in d.values())
    with pytest.raises(ValueError, match="t1 >= t0"):
        s.decomposition(ctx(p0, prev=p1))
    with pytest.raises(ConfigError, match="previous pricer"):
        s.decomposition(ctx(p0))


# ------------------------------------------------------------------ conventions and wiring
def test_the_kit_binds_every_required_schema_name_and_loads_under_the_core_contract():
    from pricebt.contracts.spec import build_spec

    spec = build_spec("ois", {"factory": R.swap, "conventions": CONV}, schemas=SchemaRegistry.default())
    assert {"value", "dv01", "gamma", "rate", "delta_ladder", "carry", "roll", "delta", "convexity"} <= set(spec.bindings)


def test_conventions_are_validated_by_the_schema_and_unsupported_values_name_what_is_supported():
    with pytest.raises(ConfigError, match="missing convention keys"):
        R.swap_conventions({"calendar": "wk"})
    with pytest.raises(ConfigError, match="unknown convention keys"):
        R.swap_conventions({**CONV, "moon": 1})
    with pytest.raises(ConfigError, match="unknown value|must be one of"):
        R.swap_conventions({**CONV, "day_count": "act_360"})
    with pytest.raises(ConfigError, match="does not implement it") as e:
        R.swap_conventions({**CONV, "compounding": "daily_average"})
    assert e.value.code == "CFG-CONVENTION-UNSUPPORTED"
    assert R.swap_conventions({**CONV, "day_count": "act365f"}).basis == 365.0


def test_the_pricer_is_data_wrapped_once_per_snapshot_and_needs_its_own_calendar():
    sp = SnapshotPricer(snapshot())
    assert R.wrap(sp) is R.wrap(sp) and R.wrap(sp).digest == sp.digest
    p = R.wrap(sp)
    with pytest.raises(ConfigError, match="no calendar 'nope'"):
        p.calendar("nope")
    with pytest.raises(ConfigError, match="not a RefPricer"):
        R.swap_factory(sp, sp.ts, terms=swap_terms(), conventions=CONV)


def test_a_snapshot_with_several_curves_needs_one_named_and_one_without_a_curve_cannot_be_priced():
    base = snapshot()
    c = base.curves["c"]
    two = MarketSnapshot(base.ts, REF, {"a": CurveSnapshot("a", REF, c.node_dates, c.values), "b": CurveSnapshot("b", REF, c.node_dates, c.values)}, {}, None, {"wk": CalendarData("wk")})
    with pytest.raises(ConfigError, match="several"):
        R.wrap(SnapshotPricer(two))
    assert R.wrap(SnapshotPricer(two), curve="b").curve is not None
    with pytest.raises(ConfigError, match="no curve 'zzz'"):
        R.wrap(SnapshotPricer(two), curve="zzz")
    empty = R.wrap(SnapshotPricer(MarketSnapshot(base.ts, REF, {}, {}, None, {"wk": CalendarData("wk")})))
    with pytest.raises(MarketDataUnavailable, match="no discount curve"):
        empty.need_curve()


def test_the_objects_are_plain_data_that_deep_copy_and_pickle():
    import copy
    import pickle

    p = pricer()
    s, _ = build(p, maturity="2Y")
    assert copy.deepcopy(s).value(ctx=ctx(p)).pv == s.value(ctx=ctx(p)).pv
    assert pickle.loads(pickle.dumps(s)).schedule == s.schedule
