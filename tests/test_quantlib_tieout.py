"""QuantLib adapter vs rateslib on the SAME snapshot: same node table, same holiday set, same fixings. rateslib is evaluated directly (rl.IRS / rl.FixedRateBond), not through an adapter.

Every tolerance below is looser than the measured agreement (docs/design/11-quantlib-conventions.md); the measured worst cases are quoted in the comments.
"""
import datetime as dt
import math

import pytest

pytest.importorskip("QuantLib")
pytest.importorskip("rateslib")
pytestmark = [pytest.mark.adapter_quantlib, pytest.mark.adapter_rateslib]

import pandas as pd  # noqa: E402
from quantlib_support import rl_reference as R  # noqa: E402
from quantlib_support import snapshots as S  # noqa: E402

from pricebt.contrib.quantlib import UST_CONVENTIONS, USD_SOFR_OIS_CONVENTIONS, bond, swap, wrap  # noqa: E402
from pricebt.pricable import MarkContext  # noqa: E402

needs_fixtures = pytest.mark.skipif(not S.have_fixtures(), reason="data/fixtures is not present (exported from the maintainer's data infrastructure)")
D = dt.datetime
HOL = R.nyc_weekday_holidays()  # rateslib's built-in `nyc` (weekday holidays to 2200): the snapshot calendar AND rateslib's calendar
RCAL = R.cal_of(HOL)


def qlp(ref, nodes, fixings=None, unit="percent", hol=HOL):
    return wrap(S.pricer_of(S.snapshot(ref, nodes, fixings=fixings, fixings_unit=unit, hol=hol)))


def swap_obj(p, conv=USD_SOFR_OIS_CONVENTIONS, **terms):
    t = S.template(swap, "swap", conv, {"side": "pay", "maturity": "10Y", "notional": 1e7, **terms})
    return t.build(p, p.ts)


def ctx(p, prev=None):
    return MarkContext.standalone(p, prev=prev)


def first_rows(anchors):
    out = []
    for a in anchors:
        d = a
        while S.fixture_row(d) is None:
            d += dt.timedelta(days=1)
        out.append(d)
    return out


ANCHORS = [dt.date(2019, 9, 3), dt.date(2020, 3, 3), dt.date(2021, 3, 2), dt.date(2022, 3, 1), dt.date(2023, 3, 1), dt.date(2024, 2, 27), dt.date(2025, 3, 4), dt.date(2026, 3, 3)]


# ------------------------------------------------------------------ dates and schedules (no fixtures needed)
@pytest.mark.parametrize("tenor", ["6M", "1Y", "18M", "30M", "5Y", "10Y", "30Y"])
def test_effective_termination_and_every_schedule_date_match_rateslib_over_a_year_of_reference_dates(tenor):
    days = [d for d in S.bdays(dt.date(2019, 1, 2), dt.date(2019, 12, 31), HOL)][::3]
    bad = 0
    for ref in days:
        p = qlp(ref, S.synthetic_nodes(ref))
        b = swap_obj(p, maturity=tenor)
        irs = R.irs(b.terms["effective"], tenor, 1, 1e6, 3.0, RCAL)
        sc = irs.leg1.schedule
        bad += (b.terms["effective"] != S.add_bdays(ref, 2, HOL) or sc.effective.date() != b.terms["effective"] or sc.termination.date() != b.terms["maturity"]
                or [x.date() for x in sc.uschedule] != list(b.obj.unadjusted))
    assert bad == 0


@pytest.mark.parametrize("spec", [("1Y", "5Y"), ("2Y", "18M")])
def test_forward_start_and_stub_schedules_match_rateslib(spec):
    fwd, tenor = spec
    for ref in S.bdays(dt.date(2023, 8, 1), dt.date(2023, 9, 29), HOL):
        p = qlp(ref, S.synthetic_nodes(ref))
        b = swap_obj(p, effective=fwd, maturity=tenor)
        irs = R.irs(b.terms["effective"], tenor, 1, 1e6, 3.0, RCAL)
        assert irs.leg1.schedule.termination.date() == b.terms["maturity"] and [x.date() for x in irs.leg1.schedule.uschedule] == list(b.obj.unadjusted)


def test_explicit_maturity_dates_are_read_the_way_rateslib_reads_them():
    """A DATE maturity: the roll comes from the effective day when the date is consistent with it (whole-year round trip), otherwise from the date with a short front stub."""
    cases = [(dt.date(2025, 1, 8), dt.date(2030, 3, 15)), (dt.date(2025, 1, 8), dt.date(2030, 1, 9)), (dt.date(2025, 1, 8), dt.date(2027, 1, 8)), (dt.date(2018, 7, 3), dt.date(2038, 7, 6)),
             (dt.date(2024, 2, 29), dt.date(2029, 2, 28)), (dt.date(2023, 8, 29), dt.date(2025, 2, 28)), (dt.date(2024, 3, 4), dt.date(2026, 9, 4)),
             (dt.date(2018, 4, 12), dt.date(2019, 10, 15)), (dt.date(2018, 4, 4), dt.date(2020, 10, 5)), (dt.date(2025, 1, 8), dt.date(2027, 4, 8))]
    p = qlp(dt.date(2024, 6, 12), S.synthetic_nodes(dt.date(2024, 6, 12)))
    for eff, mat in cases:
        b = swap_obj(p, effective=eff, maturity=mat, fixed_rate=4.0)
        irs = R.irs(eff, mat, 1, 1e6, 4.0, RCAL)
        assert [x.date() for x in irs.leg1.schedule.uschedule] == list(b.obj.unadjusted), (eff, mat)
        assert irs.leg1.schedule.termination.date() == b.terms["maturity"], (eff, mat)


@pytest.mark.parametrize("variant", [
    ({"frequency": "semiannual"}, {"frequency": "s"}), ({"payment_lag_days": 0}, {"payment_lag": 0}), ({"payment_lag_days": 1}, {"payment_lag": 1}),
    ({"business_day_convention": "following"}, {"modifier": "f"}), ({}, {})])
def test_the_supported_convention_variants_agree_with_rateslib(variant):
    conv_kw, rl_kw = variant
    conv = {**USD_SOFR_OIS_CONVENTIONS, **conv_kw}
    for ref in (dt.date(2024, 6, 12), dt.date(2024, 2, 27), dt.date(2024, 8, 28), dt.date(2024, 11, 29)):
        nodes = S.synthetic_nodes(ref)
        p = qlp(ref, nodes)
        rc = R.curve_of(*nodes, RCAL)
        for tenor in ("2Y", "5Y", "18M"):
            b = swap_obj(p, conv, maturity=tenor, fixed_rate=4.0)
            irs = R.irs(b.terms["effective"], tenor, 1, 1e7, 4.0, RCAL, **rl_kw)
            assert irs.leg1.schedule.termination.date() == b.terms["maturity"]
            assert b.obj.value(ctx=ctx(p)).pv == pytest.approx(float(irs.npv(curves=rc)), abs=1e-4)


@pytest.mark.parametrize("lag", [0, 1])
def test_other_spot_lags_agree_with_rateslib(lag):
    ref = dt.date(2024, 6, 12)
    nodes = S.synthetic_nodes(ref)
    p = qlp(ref, nodes)
    b = swap_obj(p, {**USD_SOFR_OIS_CONVENTIONS, "spot_lag_days": lag}, maturity="5Y", fixed_rate=4.0)
    assert b.terms["effective"] == S.add_bdays(ref, lag, HOL)
    assert b.obj.value(ctx=ctx(p)).pv == pytest.approx(float(R.irs(b.terms["effective"], "5Y", 1, 1e7, 4.0, RCAL).npv(curves=R.curve_of(*nodes, RCAL))), abs=1e-4)


def test_the_ladder_pillars_start_at_the_conventions_spot_lag():
    """The pillar swaps are spot-start: with a one-day spot lag the risk curve must be calibrated to swaps starting one business day out (rateslib: lag=1)."""
    ref = dt.date(2024, 6, 12)
    nodes = S.synthetic_nodes(ref)
    tenors = ("2Y", "5Y", "10Y", "30Y")
    p = qlp(ref, nodes)
    curve, solver = R.build_ladder(*nodes, RCAL, tenors, lag=1)
    for tenor in ("5Y", "10Y", "7Y"):
        b = swap_obj(p, {**USD_SOFR_OIS_CONVENTIONS, "spot_lag_days": 1}, maturity=tenor, fixed_rate=3.9)
        rl_lad = R.ladder_of(R.irs(b.terms["effective"], tenor, 1, 1e7, 3.9, RCAL), curve, solver, tenors)
        ql_lad = b.obj.delta_ladder(ctx=ctx(p), tenors=list(tenors))
        assert max(abs(ql_lad[t] - rl_lad[t]) for t in tenors) / max(abs(v) for v in rl_lad.values()) < 2e-5, tenor


# ------------------------------------------------------------------ real curves, at inception
@needs_fixtures
@pytest.mark.fixtures
@pytest.mark.parametrize("tenor", ["2Y", "5Y", "10Y", "30Y"])
def test_at_inception_swaps_on_real_curves_match_rateslib(tenor):
    """Measured over 320 swaps: NPV rel 6e-13, par 1e-14 percent, PV01 rel 4e-16."""
    for ref in first_rows(ANCHORS):
        nodes = S.fixture_row(ref)
        p = qlp(ref, nodes)
        rc = R.curve_of(*nodes, RCAL)
        b = swap_obj(p, maturity=tenor, fixed_rate=4.0)
        irs = R.irs(b.terms["effective"], tenor, 1, 1e7, 4.0, RCAL)
        assert irs.leg1.schedule.termination.date() == b.terms["maturity"]
        assert b.obj.value(ctx=ctx(p)).pv == pytest.approx(float(irs.npv(curves=rc)), rel=1e-9, abs=1e-4)
        assert b.obj.rate(ctx=ctx(p)) == pytest.approx(float(irs.rate(curves=rc)), abs=1e-10)
        assert b.obj.dv01(ctx=ctx(p)) == pytest.approx(float(irs.analytic_delta(curves=rc, leg=1)), rel=1e-9)
        par = swap_obj(p, maturity=tenor, fixed_rate="par").terms["fixed_rate"]
        assert par == pytest.approx(float(irs.rate(curves=rc)), abs=1e-10)


@needs_fixtures
@pytest.mark.fixtures
def test_started_swaps_with_published_fixings_match_rateslib_on_pv_par_flows_and_the_cash_window():
    """Measured over 344 started swaps: NPV rel 3e-13, every flow 2e-8 absolute on 50mm."""
    fx_dec = S.fixture_fixings()
    fx_pct = {d: v * 100 for d, v in fx_dec.items()}
    n = 0
    for ref in first_rows(ANCHORS[1:]):
        prev_ref = S.add_bdays(ref, -1, HOL)
        if S.fixture_row(prev_ref) is None:
            continue
        nodes, pnodes = S.fixture_row(ref), S.fixture_row(prev_ref)
        p, prev = qlp(ref, nodes, fx_dec, "decimal"), qlp(prev_ref, pnodes, fx_dec, "decimal")
        rc = R.curve_of(*nodes, RCAL)
        for back, tenor, sign in ((20, "5Y", 1), (200, "3Y", -1), (400, "10Y", 1), (1000, "5Y", -1)):
            eff = ref - dt.timedelta(days=back)
            while not S.is_bday(eff, HOL):
                eff += dt.timedelta(days=1)
            if eff < dt.date(2018, 4, 3):
                continue
            b = swap_obj(p, effective=eff, maturity=tenor, side="pay" if sign > 0 else "receive", fixed_rate=3.6, notional=5e7)
            with R.fixings({d: v for d, v in fx_pct.items() if d < ref}):
                irs = R.irs(eff, tenor, sign, 5e7, 3.6, RCAL, started=True)
                r_npv, r_par = float(irs.npv(curves=rc)), float(irs.rate(curves=rc))
                cf = irs.cashflows(curves=rc)
            assert irs.leg1.schedule.termination.date() == b.terms["maturity"]
            v = b.obj.value(ctx=ctx(p, prev))
            assert v.pv == pytest.approx(r_npv, rel=1e-9, abs=1e-4) and b.obj.rate(ctx=ctx(p)) == pytest.approx(r_par, abs=1e-9)
            # cash: rateslib flows (leg amounts) with payment in [prev_ref, ref)
            pay = pd.to_datetime(cf["Payment"])
            win = cf[(pay >= pd.Timestamp(prev_ref)) & (pay < pd.Timestamp(ref))]
            assert v.cash == pytest.approx(float(win["Cashflow"].sum()), abs=1e-4)
            n += 1
    assert n >= 10


# ------------------------------------------------------------------ dv01 and the ladder against rateslib's solver
@needs_fixtures
@pytest.mark.fixtures
@pytest.mark.parametrize("tenors", [("2Y", "5Y", "10Y", "30Y"), ("3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y", "20Y", "30Y")])
def test_the_delta_ladder_agrees_with_rateslibs_par_swap_ladder_bucket_by_bucket(tenors):
    """Measured over 155 positions: max bucket difference / max bucket 2.3e-6 (11 pillars), 2.0e-6 (4 pillars); sum of the ladder 4.3e-6."""
    fx_dec = S.fixture_fixings()
    fx_pct = {d: v * 100 for d, v in fx_dec.items()}
    worst_bucket = worst_sum = 0.0
    for ref in first_rows(ANCHORS[::2]):
        nodes = S.fixture_row(ref)
        p = qlp(ref, nodes, fx_dec, "decimal")
        curve, solver = R.build_ladder(*nodes, RCAL, tenors)
        spot = S.add_bdays(ref, 2, HOL)
        positions = [(spot, t, 1, 3.9) for t in ("2Y", "4Y", "10Y", "30Y")] + [(spot, "5Y", -1, 4.4)]
        e = ref - dt.timedelta(days=500)
        while not S.is_bday(e, HOL):
            e += dt.timedelta(days=1)
        if e >= dt.date(2018, 4, 3):
            positions.append((e, "5Y", 1, 3.6))
        for eff, tenor, sign, rate in positions:
            started = eff < ref
            b = swap_obj(p, effective=eff, maturity=tenor, side="pay" if sign > 0 else "receive", fixed_rate=rate, notional=1e7)
            with R.fixings({d: v for d, v in fx_pct.items() if d < ref}):
                irs = R.irs(eff, tenor, sign, 1e7, rate, RCAL, started=started)
                rl_lad = R.ladder_of(irs, curve, solver, tenors)
            ql_lad = b.obj.delta_ladder(ctx=ctx(p), tenors=list(tenors))
            assert list(ql_lad) == list(tenors)
            scale = max(abs(v) for v in rl_lad.values())
            worst_bucket = max(worst_bucket, max(abs(ql_lad[t] - rl_lad[t]) for t in tenors) / scale)
            worst_sum = max(worst_sum, abs(sum(ql_lad.values()) - sum(rl_lad.values())) / abs(sum(rl_lad.values())))
            if started:  # the market dv01 of a started swap is the ladder sum in both
                assert b.obj.dv01(ctx=ctx(p), tenors=list(tenors)) == pytest.approx(sum(rl_lad.values()), rel=1e-4)
    assert worst_bucket < 1e-4 and worst_sum < 1e-4, (worst_bucket, worst_sum)


@pytest.mark.parametrize("ref", [dt.date(2025, 2, 26), dt.date(2024, 2, 27), dt.date(2024, 8, 28), dt.date(2024, 5, 29)])
def test_the_ladder_is_right_when_the_spot_date_is_the_last_business_day_of_a_month(ref):
    """QuantLib's OIS helper turns end-of-month rolling on by itself when the start is a month's last business day (2025-02-28: the leap-year February 2028 rolls to the 29th): the adapter
    passes endOfMonth=False. Measured with the default on: 2.3e-3 relative bucket error; with the fix 8e-8."""
    nodes = S.synthetic_nodes(ref)
    spot = S.add_bdays(ref, 2, HOL)
    assert S.add_bdays(spot, 1, HOL).month != spot.month, "the fixture date must make the spot the last business day of its month"
    tenors = ("2Y", "5Y", "10Y", "30Y")
    p = qlp(ref, nodes)
    curve, solver = R.build_ladder(*nodes, RCAL, tenors)
    for tenor in ("2Y", "3Y", "5Y", "7Y", "10Y"):
        b = swap_obj(p, maturity=tenor, fixed_rate=3.9, notional=1e7)
        rl_lad = R.ladder_of(R.irs(spot, tenor, 1, 1e7, 3.9, RCAL), curve, solver, tenors)
        ql_lad = b.obj.delta_ladder(ctx=ctx(p), tenors=list(tenors))
        assert max(abs(ql_lad[t] - rl_lad[t]) for t in tenors) / max(abs(v) for v in rl_lad.values()) < 1e-5, (ref, tenor)


@needs_fixtures
@pytest.mark.fixtures
def test_gamma_agrees_with_rateslibs_parallel_par_bump_and_the_zero_rate_measures_with_a_zero_shift():
    ref = first_rows([dt.date(2023, 3, 1)])[0]
    nodes = S.fixture_row(ref)
    p = qlp(ref, nodes)
    rc = R.curve_of(*nodes, RCAL)
    tenors = ("3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y", "20Y", "30Y")
    spot = S.add_bdays(ref, 2, HOL)
    for tenor, rate in (("10Y", 3.9), ("5Y", 3.4)):
        b = swap_obj(p, maturity=tenor, fixed_rate=rate, notional=1e8)
        irs = R.irs(spot, tenor, 1, 1e8, rate, RCAL)
        g_rl = R.parallel_gamma(*nodes, RCAL, tenors, irs)
        # measured 1.3e-7; with the SHIPPED solver tolerance (func_tol 1e-9) the same second difference is off by 22% (solver noise), see the reference's tol=(1e-20, 1e-20)
        assert b.obj.gamma(ctx=ctx(p)) == pytest.approx(g_rl, rel=1e-5)
        # zero-rate parallel shift of the dense curve, the formula of the shipped adapter's dv01_ad / convexity
        t0 = D(ref.year, ref.month, ref.day)

        def pv(bp):
            c = R.curve_of(*nodes, RCAL)
            sh = {d: float(v) * math.exp(-bp * 1e-4 * (d - t0).days / 365.0) for d, v in c.nodes.nodes.items()}
            return float(irs.npv(curves=R.curve_of(list(k.date() for k in sh), list(sh.values()), RCAL)))

        assert b.obj.dv01_zero(ctx=ctx(p)) == pytest.approx((pv(0.5) - pv(-0.5)) / 1.0, rel=1e-9)
        assert b.obj.gamma_zero(ctx=ctx(p)) == pytest.approx(pv(1.0) - 2 * pv(0.0) + pv(-1.0), rel=1e-6)


# ------------------------------------------------------------------ the golden seasoned swap: the numbers of the reference adapter
def test_the_golden_seasoned_swap_reproduces_the_reference_numbers_through_the_whole_adapter():
    """3y payer 50mm eff 2024-01-08 at 4.20, coupon paid in [2025-01-02, 2025-01-16); curves solved by rateslib; V0/V1/cash/layers of the maintainers' verified research."""
    fx = S.seeded_fixings(HOL, dt.date(2023, 1, 3), dt.date(2025, 3, 31))
    c0, c1 = R.par_curve(D(2025, 1, 2), 0, 0, "c0"), R.par_curve(D(2025, 1, 16), 3, 15, "c1")
    t0, t1 = dt.date(2025, 1, 2), dt.date(2025, 1, 16)

    def snap(d, c):
        return qlp(d, ([k.date() for k in c.nodes.nodes], [float(v) for v in c.nodes.nodes.values()]), fx)

    p0, p1 = snap(t0, c0), snap(t1, c1)
    b = swap_obj(p1, effective=dt.date(2024, 1, 8), maturity=dt.date(2027, 1, 8), fixed_rate=4.20, notional=50e6)
    v0, v1 = b.obj.value(ctx=ctx(p0)), b.obj.value(ctx=ctx(p1, p0))
    assert v0.pv == pytest.approx(-369827.43, abs=0.01) and v1.pv == pytest.approx(-212114.86, abs=0.01) and v1.cash == pytest.approx(-86490.36, abs=0.01) and v0.cash == 0.0
    assert v1.pv - v0.pv + v1.cash == pytest.approx(71222.20, abs=0.01)
    d = b.obj.decomposition(ctx(p1, p0))
    assert d["carry"] == pytest.approx(-561.82, abs=0.01) and d["roll"] == pytest.approx(1532.07, abs=0.01)
    assert d["delta"] == pytest.approx(70302.16, abs=0.02) and d["convexity"] == pytest.approx(-50.23, abs=0.01) and d["residual"] == pytest.approx(0.02, abs=0.01)
    # the reference adapter's own evaluation of the same swap: independent of the QuantLib side
    with R.fixings({d_: v for d_, v in fx.items() if d_ < t1}):
        irs = R.irs(dt.date(2024, 1, 8), dt.date(2027, 1, 8), 1, 50e6, 4.20, RCAL, started=True)
        assert float(irs.npv(curves=R.curve_of([k.date() for k in c1.nodes.nodes], [float(v) for v in c1.nodes.nodes.values()], RCAL))) == pytest.approx(v1.pv, abs=1e-6)


# ------------------------------------------------------------------ the fixture calendar horizon (documented gap 9.1)
@needs_fixtures
@pytest.mark.fixtures
def test_a_calendar_that_ends_in_2035_moves_a_30y_swap_by_at_most_a_day_of_interest_and_a_10y_swap_not_at_all():
    """`nyc.json` stops at 2035-12-25: schedule dates after it see no holiday. Against rateslib's full-horizon calendar: 10Y exactly 0; 30Y typically ~1e-6 of notional, and up to one day's
    interest on a coupon whose date lands on a post-2035 holiday (514 on 10mm in this sample, bound: 10mm x 5% / 360 = 1,400)."""
    cal = S.fixture_calendar("nyc")
    diffs = {"10Y": 0.0, "30Y": 0.0}
    for ref in first_rows(ANCHORS):
        nodes = S.fixture_row(ref)
        full = qlp(ref, nodes)
        cut = wrap(S.pricer_of(S.snapshot(ref, nodes, calendars={"nyc": cal})))
        for tenor in diffs:
            a, b = swap_obj(full, maturity=tenor, fixed_rate=4.0).obj.value(ctx=ctx(full)).pv, swap_obj(cut, maturity=tenor, fixed_rate=4.0).obj.value(ctx=ctx(cut)).pv
            diffs[tenor] = max(diffs[tenor], abs(a - b))
    assert diffs["10Y"] == 0.0 and 0.1 < diffs["30Y"] < 1400.0, diffs


# ------------------------------------------------------------------ bonds
SYN_BONDS = [
    (dt.date(2024, 11, 15), dt.date(2034, 11, 15), 4.25), (dt.date(2023, 2, 28), dt.date(2028, 2, 29), 4.0), (dt.date(2022, 8, 31), dt.date(2027, 8, 31), 3.125),
    (dt.date(2024, 9, 3), dt.date(2029, 8, 31), 3.75), (dt.date(2020, 2, 15), dt.date(2030, 2, 15), 1.5), (dt.date(2021, 5, 17), dt.date(2031, 5, 15), 1.625),
    (dt.date(2023, 3, 31), dt.date(2025, 3, 31), 4.5), (dt.date(2024, 1, 31), dt.date(2026, 1, 31), 4.25), (dt.date(2015, 11, 16), dt.date(2045, 11, 15), 3.0),
    (dt.date(2024, 4, 30), dt.date(2029, 4, 30), 4.625), (dt.date(2019, 7, 1), dt.date(2049, 5, 15), 2.875), (dt.date(2024, 12, 2), dt.date(2026, 11, 30), 4.125),
    (dt.date(2023, 10, 31), dt.date(2028, 4, 30), 3.5), (dt.date(2024, 9, 30), dt.date(2029, 9, 30), 3.875),  # month-end maturities on a SUNDAY
    (dt.date(2023, 8, 29), dt.date(2025, 8, 29), 4.0),  # the last BUSINESS day of its month but not a month-end: with a business calendar QuantLib would roll the coupons to month-ends
]


def bond_case(issue, mat, cpn, settle, y):
    sid = "SYN000001"
    q = S.quote_set(settle, [(sid, cpn, issue, mat)], {sid: {"ytm": y}}, aliases={"CT10": sid})
    p = wrap(S.pricer_of(S.snapshot(settle, nodes=False, hol=HOL, quotes=q)))
    obj = S.template(bond, "bond", UST_CONVENTIONS, {"side": "long", "security": "CT10", "notional": 1e7}).build(p, p.ts).obj
    return p, obj


def test_bond_prices_flows_and_payment_dates_match_rateslib_us_gb_tsy_on_month_end_and_stub_bonds():
    """Measured over 250 real bonds / 11,552 points: dirty 6.3e-13 per 100, flows 1.1e-14, payment dates 0 differences."""
    worst = 0.0
    n = 0
    for issue, mat, cpn in SYN_BONDS:
        rb = R.bond(issue, mat, cpn, RCAL)
        first = max(issue + dt.timedelta(days=30), dt.date(2019, 1, 2))
        pay = [(pp.settlement_params.payment.date(), abs(float(pp.cashflow())) / 1e6 * 100) for pp in rb.leg1.periods]
        settles = {first + dt.timedelta(days=int(k)) for k in range(0, max(1, (mat - first).days - 1), max(1, (mat - first).days // 9))}
        for d, _ in pay[1:5]:
            settles |= {d - dt.timedelta(days=1), d, d + dt.timedelta(days=1)}
        for s in sorted(settles):
            if not (issue < s < mat) or not S.is_bday(s, HOL):
                continue
            for y in (0.7, 3.3, 5.9):
                p, obj = bond_case(issue, mat, cpn, s, y)
                worst = max(worst, abs(obj.mark(p)["dirty"] - float(rb.price(y, D(s.year, s.month, s.day), dirty=True))))
                n += 1
        p, obj = bond_case(issue, mat, cpn, first, 3.0)
        mine = obj._inst(p).pay
        assert [a for a, _ in mine] == [a for a, _ in pay] and max(abs(a - b) for (_, a), (_, b) in zip(mine, pay)) < 1e-9, (issue, mat)
    assert n > 300 and worst < 1e-9, (n, worst)


def test_bond_risk_and_convexity_match_rateslibs_analytic_measures_and_the_yield_solve_is_tighter():
    """Measured: risk 6.5e-12 relative (4th-order FD vs analytic), convexity 4e-8; rateslib's own ytm round trip is only good to ~1.7e-7 percent."""
    for issue, mat, cpn in SYN_BONDS[:6]:
        rb = R.bond(issue, mat, cpn, RCAL)
        s = max(issue + dt.timedelta(days=200), dt.date(2020, 1, 2))
        while not S.is_bday(s, HOL):
            s += dt.timedelta(days=1)
        if s >= mat - dt.timedelta(days=400):
            continue
        p, obj = bond_case(issue, mat, cpn, s, 3.7)
        c = ctx(p)
        risk_rl, cvx_rl = float(rb.duration(3.7, D(s.year, s.month, s.day), "risk")), float(rb.convexity(3.7, D(s.year, s.month, s.day), "risk"))
        assert obj.dv01(ctx=c) == pytest.approx(-1e7 * risk_rl / 1e4, rel=1e-9)
        assert obj.gamma(ctx=c) == pytest.approx(1e7 / 100 * cvx_rl * 1e-4, rel=1e-6)
        clean = obj.mark(p)["clean"]
        q = S.quote_set(s, [("SYN000001", cpn, issue, mat)], {"SYN000001": {"clean": clean}})
        pc = wrap(S.pricer_of(S.snapshot(s, nodes=False, hol=HOL, quotes=q)))
        y_ql = obj.mark(pc)["ytm"]
        assert abs(y_ql - 3.7) < 1e-9 and abs(float(rb.ytm(clean, D(s.year, s.month, s.day), dirty=False)) - 3.7) < 1e-5


def test_on_a_coupon_date_quantlib_accrues_zero_and_rateslib_2_7_1_accrues_the_whole_coupon():
    """The documented rateslib quirk (gap 9.3): dirty prices agree, clean prices differ by exactly the coupon."""
    issue, mat, cpn = dt.date(2024, 11, 15), dt.date(2034, 11, 15), 4.25
    s = dt.date(2025, 5, 15)
    rb = R.bond(issue, mat, cpn, RCAL)
    p, obj = bond_case(issue, mat, cpn, s, 4.5)
    m = obj.mark(p)
    assert m["accrued"] == 0.0 and m["dirty"] == m["clean"]
    assert float(rb.price(4.5, D(2025, 5, 15), dirty=True)) == pytest.approx(m["dirty"], abs=1e-9)
    assert float(rb.accrued(D(2025, 5, 15))) == pytest.approx(cpn / 2, abs=1e-12)


@needs_fixtures
@pytest.mark.fixtures
def test_real_treasuries_from_the_reference_table_match_rateslib():
    ref = pd.read_parquet(S.FIXTURES / "ust" / "reference_fiscaldata.parquet")
    ref = ref[(ref["cpn"].notna()) & (ref["cpn"] > 0)].copy()
    for c in ("issue_date", "maturity_date"):
        ref[c] = pd.to_datetime(ref[c]).dt.date
    ref = ref[(ref["issue_date"] > dt.date(2012, 1, 1)) & (ref["maturity_date"] < dt.date(2060, 1, 1)) & (ref["maturity_date"] > dt.date(2024, 1, 1))]
    sample = ref.iloc[:: max(1, len(ref) // 25)].head(25)
    assert len(sample) >= 20
    off_cycle = month_end = 0
    for r in sample.itertuples():
        issue, mat, cpn = r.issue_date, r.maturity_date, float(r.cpn)
        off_cycle += issue.day != mat.day
        month_end += (mat + dt.timedelta(days=1)).day == 1
        rb = R.bond(issue, mat, cpn, RCAL)
        s = max(issue + dt.timedelta(days=45), dt.date(2019, 1, 2))
        while not S.is_bday(s, HOL):
            s += dt.timedelta(days=1)
        if not s < mat - dt.timedelta(days=10):
            continue
        p, obj = bond_case(issue, mat, cpn, s, 4.1)
        assert obj.mark(p)["dirty"] == pytest.approx(float(rb.price(4.1, D(s.year, s.month, s.day), dirty=True)), abs=1e-9), r.cusip
        pay = [(pp.settlement_params.payment.date(), abs(float(pp.cashflow())) / 1e6 * 100) for pp in rb.leg1.periods]
        assert [a for a, _ in obj._inst(p).pay] == [a for a, _ in pay], r.cusip
    assert off_cycle > 0 and month_end > 0, "the sample must contain stub and month-end bonds"


# ------------------------------------------------------------------ both libraries in one process, in both orders
def test_alternating_quantlib_and_rateslib_in_both_orders_gives_identical_numbers_and_leaks_nothing():
    ref = dt.date(2024, 6, 12)
    nodes = S.synthetic_nodes(ref)
    fx = S.seeded_fixings(HOL, dt.date(2022, 1, 3), dt.date(2024, 6, 11))
    p = qlp(ref, nodes, fx)
    o = swap_obj(p, effective=dt.date(2024, 1, 8), maturity=dt.date(2027, 1, 8), fixed_rate=4.2).obj
    rc = R.curve_of(*nodes, RCAL)

    def ql_pv():
        return o.value(ctx=ctx(p)).pv

    def rl_pv():
        with R.fixings(fx):
            return float(R.irs(dt.date(2024, 1, 8), dt.date(2027, 1, 8), 1, 1e7, 4.2, RCAL, started=True).npv(curves=rc))

    import QuantLib as ql

    ql.Settings.instance().evaluationDate = ql.Date(3, 3, 2031)
    try:
        a_first = (ql_pv(), rl_pv(), ql_pv(), rl_pv())
        b_first_rl, b_first_ql = rl_pv(), ql_pv()
        b = (b_first_rl, b_first_ql, rl_pv(), ql_pv())
        assert a_first[0] == a_first[2] == b_first_ql == b[3] and a_first[1] == a_first[3] == b_first_rl == b[2]
        assert a_first[0] == pytest.approx(a_first[1], abs=1e-6)
        assert ql.Settings.instance().evaluationDate == ql.Date(3, 3, 2031)
        assert R.FIX_KEY not in R.rl.fixings.loader.loaded  # rateslib's store is left as we found it as well
    finally:
        ql.Settings.instance().evaluationDate = ql.Date(1, 1, 2000)
