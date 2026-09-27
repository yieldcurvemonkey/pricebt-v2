"""QLSwap and the `swap` kit: conformance, resolution of dates and `par`, units, signs, cash window, layers, ladder. QuantLib only (the rateslib tie-out is in test_quantlib_tieout.py)."""
import calendar
import copy
import datetime as dt
import math
import pickle

import pytest

pytest.importorskip("QuantLib")
pytestmark = pytest.mark.adapter_quantlib

from quantlib_support import snapshots as S  # noqa: E402

from pricebt.contracts.binding import Env, call_binding  # noqa: E402
from pricebt.contracts.spec import build_spec  # noqa: E402
from pricebt.contrib.quantlib import DEFAULT_TENORS, USD_SOFR_OIS_CONVENTIONS, swap, wrap  # noqa: E402
from pricebt.contrib.quantlib import _compat as C  # noqa: E402
from pricebt.errors import ConfigError, MarketDataUnavailable  # noqa: E402
from pricebt.pricable import MarkContext, Valuation  # noqa: E402

REF = dt.date(2024, 6, 12)
HOL = S.synthetic_holidays()
FX = S.seeded_fixings(HOL, dt.date(2022, 1, 3), dt.date(2025, 12, 31))  # percent
CONV = USD_SOFR_OIS_CONVENTIONS


def pr(ref=REF, nodes=None, fixings=FX, hour=17, **kw):
    return wrap(S.pricer_of(S.snapshot(ref, nodes, fixings=fixings, hol=HOL, hour=hour, **kw)))


def tpl(conv=CONV, **terms):
    terms = {"side": "pay", "maturity": "10Y", "notional": 1e7, **terms}
    return S.template(swap, "swap", conv, terms)


def build(p, **terms):
    return tpl(**terms).build(p, p.ts)


def ctx(p, prev=None):
    return MarkContext.standalone(p, prev=prev)


def flat(ref, r=0.04):
    """A curve anchored at `ref` whose forwards do not depend on `ref`: consecutive days give the SAME market (DF(t) = exp(-r (t - ref)/365))."""
    days = (0, 7, 30, 91, 182, 365, 730, 1095, 1826, 2557, 3652, 5479, 7305, 10958)
    return [ref + dt.timedelta(days=n) for n in days], [math.exp(-r * n / 365.0) for n in days]


# ------------------------------------------------------------------ independent date arithmetic (plain python over the same holiday set)
def add_months(d, n, roll):
    y, m0 = divmod(d.year * 12 + d.month - 1 + n, 12)
    return dt.date(y, m0 + 1, min(roll, calendar.monthrange(y, m0 + 1)[1]))


def modified_following(d):
    e = d
    while not S.is_bday(e, HOL):
        e += dt.timedelta(days=1)
    if e.month != d.month:
        e = d
        while not S.is_bday(e, HOL):
            e -= dt.timedelta(days=1)
    return e


# ------------------------------------------------------------------ conformance
def test_conforms_to_the_swap_schema_with_only_the_kits_default_block():
    spec = build_spec("s", {"factory": swap, "conventions": CONV, "layers": ["carry", "roll", "delta", "convexity"]}, schemas=S.SCHEMAS)
    for name in ("value", "dv01", "gamma", "rate", "delta_ladder", "carry", "roll", "delta", "convexity"):
        assert name in spec.bindings
    assert spec.asset_class == "swap" and spec.layers == ("carry", "roll", "delta", "convexity")


def test_every_default_binding_runs_and_returns_its_contract_type():
    p = pr()
    t = tpl(effective=dt.date(2024, 1, 8), maturity=dt.date(2027, 1, 8), fixed_rate=4.2)
    obj = t.build(p, p.ts).obj
    prev = pr(dt.date(2024, 6, 11))
    c = ctx(p, prev)
    env = Env(pricer=p, ctx=c, instrument=obj, terms={})
    out = {name: call_binding(b, env) for name, b in t.spec.bindings.items()}
    assert isinstance(out["value"], Valuation)
    for k in ("dv01", "gamma", "rate", "carry", "roll", "delta", "convexity", "dv01_zero", "gamma_zero"):
        assert isinstance(out[k], float) and math.isfinite(out[k]), k
    assert isinstance(out["delta_ladder"], dict) and list(out["delta_ladder"]) == list(DEFAULT_TENORS)
    assert all(isinstance(v, float) for v in out["delta_ladder"].values())


def test_a_user_binding_can_swap_gamma_for_the_zero_rate_gamma():
    p = pr()
    t = S.template(swap, "swap", CONV, {"side": "pay", "maturity": "5Y", "notional": 1e7}, bind={"gamma": {"target": {"method": "gamma_zero"}, "kwargs": {"ctx": "@ctx"}}})
    obj = t.build(p, p.ts).obj
    got = call_binding(t.spec.bindings["gamma"], Env(pricer=p, ctx=ctx(p), instrument=obj, terms={}))
    assert got == obj.gamma_zero(ctx=ctx(p)) != obj.gamma(ctx=ctx(p))


def test_a_pricer_that_is_not_wrapped_is_rejected_with_the_fix():
    p = S.pricer_of(S.snapshot(REF, hol=HOL))
    with pytest.raises(ConfigError, match="not a QLPricer"):
        tpl().build(p, p.ts)


# ------------------------------------------------------------------ resolution of dates, tenors, par
@pytest.mark.parametrize("ref", [dt.date(2024, 7, 2), dt.date(2024, 6, 12), dt.date(2024, 11, 27), dt.date(2024, 12, 20)])
@pytest.mark.parametrize("tenor", ["1Y", "5Y", "10Y", "18M"])
def test_spot_and_termination_dates_match_plain_python_over_the_snapshot_holidays(ref, tenor):
    p = pr(ref)
    b = build(p, maturity=tenor)
    spot = S.add_bdays(ref, 2, HOL)  # 2024-07-02 + 2 bd skips the July 4th holiday
    months = 12 * int(tenor[:-1]) if tenor.endswith("Y") else int(tenor[:-1])
    assert b.terms["effective"] == spot and isinstance(b.terms["effective"], dt.date)
    assert b.terms["maturity"] == modified_following(add_months(spot, months, spot.day)) and isinstance(b.terms["maturity"], dt.date)
    assert isinstance(b.terms["fixed_rate"], float) and b.terms["direction"] == 1


def test_a_forward_start_tenor_is_spot_rolled_forward():
    p = pr()
    b = build(p, effective="1Y", maturity="5Y")
    spot = S.add_bdays(REF, 2, HOL)
    assert b.terms["effective"] == modified_following(add_months(spot, 12, spot.day))
    assert b.terms["maturity"] == modified_following(add_months(b.terms["effective"], 60, b.terms["effective"].day))


def test_explicit_dates_pass_through_and_the_adjusted_maturity_round_trips_for_whole_year_tenors():
    p = pr()
    a = build(p, maturity="7Y")
    b = build(p, effective=a.terms["effective"], maturity=a.terms["maturity"], fixed_rate=a.terms["fixed_rate"])
    assert b.terms["effective"] == a.terms["effective"] and b.terms["maturity"] == a.terms["maturity"]
    assert a.obj.unadjusted == b.obj.unadjusted
    va, vb = a.obj.value(ctx=ctx(p)).pv, b.obj.value(ctx=ctx(p)).pv
    assert va == pytest.approx(vb, abs=1e-9)


def test_only_month_and_year_tenors_and_par_for_unstarted_swaps():
    p = pr()
    for terms, match in (({"maturity": "10D"}, "month and year"), ({"effective": "2W"}, "month and year")):
        with pytest.raises(ConfigError, match=match):
            build(p, **terms)
    with pytest.raises(ConfigError, match="par"):
        build(p, effective=dt.date(2024, 3, 4), maturity="5Y", fixed_rate="par")
    with pytest.raises(ConfigError, match="not supported"):
        build(p, extras={"spread": 1})
    with pytest.raises(ConfigError, match="only to fixed_rate 'par'"):
        build(p, fixed_rate=4.0, extras={"par_spread_bp": 25})
    for bad in ("25", True, float("nan")):
        with pytest.raises(ConfigError, match="par_spread_bp"):
            build(p, fixed_rate="par", extras={"par_spread_bp": bad})


def test_a_swap_needs_a_curve_in_the_snapshot():
    p = wrap(S.pricer_of(S.snapshot(REF, nodes=False, hol=HOL)))
    with pytest.raises(ConfigError, match="no curve"):
        build(p)


def test_par_is_resolved_to_a_percent_rate_that_prices_at_zero():
    p = pr()
    b = build(p, maturity="10Y", fixed_rate="par")
    r = b.terms["fixed_rate"]
    assert 0.5 < r < 20 and isinstance(r, float)  # percent, not a decimal
    assert abs(b.obj.value(ctx=ctx(p)).pv) < 1e-5
    assert b.obj.rate(ctx=ctx(p)) == pytest.approx(r, abs=1e-12)  # the `rate` measure is the same number in percent
    assert build(p, fixed_rate=4.0).terms["fixed_rate"] == 4.0


def test_a_spread_over_par_is_in_basis_points_and_moves_the_rate_by_hundredths_of_a_percent():
    """`IRSwap(fixed_rate='ATMF+25')` arrives as fixed_rate 'par' + extras {par_spread_bp: 25}: 25bp = 0.25 percent, and the payer then loses 25 x dv01."""
    p = pr()
    par = build(p, maturity="5Y", fixed_rate="par")
    wide = build(p, maturity="5Y", fixed_rate="par", extras={"par_spread_bp": 25.0})
    assert wide.terms["fixed_rate"] == pytest.approx(par.terms["fixed_rate"] + 0.25, abs=1e-12)
    assert wide.obj.value(ctx=ctx(p)).pv == pytest.approx(-25.0 * wide.obj.dv01(ctx=ctx(p)), rel=1e-9)
    assert build(p, maturity="5Y", fixed_rate="par", extras={"par_spread_bp": -10}).terms["fixed_rate"] == pytest.approx(par.terms["fixed_rate"] - 0.10, abs=1e-12)


# ------------------------------------------------------------------ units
def test_a_fixed_rate_term_of_4_prices_as_4_percent_not_400_and_not_0_04():
    """Payer NPV = notional * annuity * (par - K), and dv01 = notional * annuity * 1bp: so NPV = dv01 * (par - K) / 1bp exactly, whatever the curve."""
    p = pr()
    par = build(p, maturity="7Y", fixed_rate="par").terms["fixed_rate"]
    o = build(p, maturity="7Y", fixed_rate=4.0).obj
    v, d = o.value(ctx=ctx(p)).pv, o.dv01(ctx=ctx(p))
    assert v == pytest.approx(d * (par - 4.0) * 100.0, rel=1e-9)
    assert abs(par - 4.0) > 0.1 and v != 0.0
    assert d == pytest.approx(build(p, maturity="7Y", fixed_rate=3.99).obj.value(ctx=ctx(p)).pv - v, rel=1e-9)  # one bp of fixed rate is the dv01, per unit as built


def test_dv01_is_currency_per_bp_for_the_notional_as_built_and_scales_linearly():
    p = pr()
    a, b = build(p, notional=1e7).obj.dv01(ctx=ctx(p)), build(p, notional=3.6e7).obj.dv01(ctx=ctx(p))
    assert 5_000 < a < 12_000  # a 10mm 10Y swap is roughly 8-9k per bp
    assert b == pytest.approx(3.6 * a, rel=1e-12)


# ------------------------------------------------------------------ signs
def test_payer_and_receiver_are_exact_mirrors_on_value_measures_ladder_and_every_layer():
    p, prev = pr(REF, S.synthetic_nodes(REF, bump=0.0020)), pr(dt.date(2024, 6, 10), S.synthetic_nodes(dt.date(2024, 6, 10)))
    kw = dict(effective=dt.date(2023, 9, 6), maturity=dt.date(2028, 9, 6), fixed_rate=3.7, notional=2.5e7)
    pay, rec = build(p, side="pay", **kw).obj, build(p, side="receive", **kw).obj
    cp, cr = ctx(p, prev), ctx(p, prev)
    vp, vr = pay.value(ctx=cp), rec.value(ctx=cr)
    assert vp.pv == pytest.approx(-vr.pv, abs=1e-6) and vp.cash == pytest.approx(-vr.cash, abs=1e-6)
    for name in ("dv01", "gamma", "dv01_zero", "gamma_zero", "carry", "roll", "delta", "convexity"):
        assert getattr(pay, name)(ctx=cp) == pytest.approx(-getattr(rec, name)(ctx=cr), rel=1e-9, abs=1e-6), name
    lp, lr = pay.delta_ladder(ctx=cp), rec.delta_ladder(ctx=cr)
    assert all(lp[t] == pytest.approx(-lr[t], rel=1e-9, abs=1e-6) for t in lp)
    assert pay.rate(ctx=cp) == pytest.approx(rec.rate(ctx=cr), rel=1e-12)
    assert pay.dv01(ctx=cp) > 0 > rec.dv01(ctx=cr)  # the payer of fixed is the positive dv01


def test_the_sign_of_gamma_is_negative_for_a_payer_and_the_two_gammas_agree_within_ten_percent():
    p = pr()
    o = build(p, maturity="10Y", fixed_rate="par").obj
    g, gz = o.gamma(ctx=ctx(p)), o.gamma_zero(ctx=ctx(p))
    assert g < 0 and gz < 0 and abs(g / gz - 1) < 0.1


# ------------------------------------------------------------------ dv01, delta ladder
def test_dv01_of_a_fresh_swap_is_the_annuity_and_of_a_started_swap_the_ladder_sum():
    p = pr()
    fresh = build(p, maturity="10Y", fixed_rate="par").obj
    lad = fresh.delta_ladder(ctx=ctx(p))
    assert fresh.dv01(ctx=ctx(p)) == pytest.approx(sum(lad.values()), rel=3e-3) and fresh.dv01(ctx=ctx(p)) != sum(lad.values())  # analytic figure, not the ladder
    aged = build(p, effective=dt.date(2023, 6, 14), maturity=dt.date(2025, 6, 16), fixed_rate=3.9).obj  # a 2Y swap with a year left
    c = ctx(p)
    assert aged.dv01(ctx=c) == sum(aged.delta_ladder(ctx=c).values())
    for tenors in (["2Y", "5Y", "10Y"], ["1y", "2y", "3y"]):
        assert aged.dv01(ctx=c, tenors=tenors) == pytest.approx(sum(aged.delta_ladder(ctx=c, tenors=tenors).values()), rel=1e-12)


def test_the_analytic_pv01_overstates_the_market_dv01_of_an_aged_swap():
    """Independent of the ladder: one bp of fixed rate is the analytic PV01 of the whole remaining fixed leg (the accrued coupon no longer moves with rates)."""
    p = pr()
    kw = dict(effective=dt.date(2023, 6, 14), maturity=dt.date(2025, 6, 16), notional=1e7)
    a, b = build(p, fixed_rate=3.9, **kw).obj, build(p, fixed_rate=3.89, **kw).obj
    analytic = b.value(ctx=ctx(p)).pv - a.value(ctx=ctx(p)).pv
    market = a.dv01(ctx=ctx(p))
    assert analytic > 1.3 * market > 0


def test_ladder_keys_are_exactly_the_requested_tenors_in_order_and_case_normalised():
    p = pr()
    o = build(p, maturity="10Y").obj
    assert list(o.delta_ladder(ctx=ctx(p), tenors=["10y", "2Y", "30y"])) == ["10Y", "2Y", "30Y"]
    assert list(o.delta_ladder(ctx=ctx(p))) == list(DEFAULT_TENORS)
    with pytest.raises(ConfigError, match="duplicates"):
        o.delta_ladder(ctx=ctx(p), tenors=["5Y", "5y"])
    with pytest.raises(ConfigError, match="tenor"):
        o.delta_ladder(ctx=ctx(p), tenors=["5X"])
    with pytest.raises(ConfigError, match="at least one"):
        o.delta_ladder(ctx=ctx(p), tenors=[])


def test_a_par_swap_of_a_pillar_tenor_has_its_risk_in_that_bucket_and_the_ladder_scales_with_notional():
    p = pr()
    o = build(p, maturity="5Y", fixed_rate="par", notional=1e7).obj
    lad = o.delta_ladder(ctx=ctx(p))
    assert lad["5Y"] > 0.999 * sum(lad.values())
    big = build(p, maturity="5Y", fixed_rate="par", notional=2.5e7).obj.delta_ladder(ctx=ctx(p))
    assert all(big[t] == pytest.approx(2.5 * lad[t], rel=1e-9, abs=1e-6) for t in lad)


def test_an_off_pillar_swap_spreads_over_its_neighbours_and_the_buckets_sum_to_the_dv01():
    p = pr()
    o = build(p, maturity="4Y", fixed_rate="par").obj
    lad = o.delta_ladder(ctx=ctx(p))
    assert lad["3Y"] > 0 and lad["5Y"] > 0 and lad["3Y"] + lad["5Y"] > 0.99 * sum(lad.values())
    assert sum(lad.values()) == pytest.approx(o.dv01(ctx=ctx(p)), rel=3e-3)


# ------------------------------------------------------------------ fixings
def test_a_started_swap_needs_fixings_and_a_contiguous_history():
    kw = dict(effective=dt.date(2024, 1, 8), maturity=dt.date(2027, 1, 8), fixed_rate=4.2)
    p = pr()
    o = build(p, **kw).obj
    nofx = pr(fixings=None)
    with pytest.raises(MarketDataUnavailable, match="no fixings"):
        o.value(ctx=ctx(nofx))
    hole = {d: v for d, v in FX.items() if d != dt.date(2024, 3, 6)}
    with pytest.raises(MarketDataUnavailable, match="1 SOFR fixings missing between 2024-01-08 and 2024-06-11; first 2024-03-06"):
        o.value(ctx=ctx(pr(fixings=hole)))
    stale = {d: v for d, v in FX.items() if d < dt.date(2024, 6, 10)}  # the last two business days absent
    with pytest.raises(MarketDataUnavailable, match="2 SOFR fixings missing"):
        o.value(ctx=ctx(pr(fixings=stale)))


def test_fixings_move_the_value_of_a_started_swap_by_the_accrued_days_and_the_unit_does_not_matter():
    kw = dict(effective=dt.date(2024, 1, 8), maturity=dt.date(2027, 1, 8), fixed_rate=4.2, notional=5e7)
    p = pr()
    o = build(p, **kw).obj
    base = o.value(ctx=ctx(p)).pv
    up = {d: v + 0.01 for d, v in FX.items()}  # +1bp in every published fixing (percent)
    d_pv = o.value(ctx=ctx(pr(fixings=up))).pv - base
    # independent recomputation of the compounded current coupon: coupon = N (P F - 1), P = prod(1 + r_i d_i/360) over the fixed days, F = DF(ref)/DF(period end)
    start, end, pay = dt.date(2024, 1, 8), dt.date(2025, 1, 8), dt.date(2025, 1, 10)
    P, dS = 1.0, 0.0
    for d in S.bdays(start, REF - dt.timedelta(days=1), HOL):
        n, r = (S.add_bdays(d, 1, HOL) - d).days, FX[d] / 100.0
        P *= 1 + r * n / 360.0
        dS += (n / 360.0) / (1 + r * n / 360.0)
    cv = p.ql_curve()
    F = cv.discount(C.qd(REF)) / cv.discount(C.qd(end))
    assert d_pv == pytest.approx(5e7 * 1e-4 * dS * P * F * cv.discount(C.qd(pay)), rel=1e-4)
    dec = pr(fixings={d: v / 100 for d, v in FX.items()}, fixings_unit="decimal")
    assert o.value(ctx=ctx(dec)).pv == pytest.approx(base, abs=1e-8)


# ------------------------------------------------------------------ cash window and the payment date
def test_cash_is_swept_in_a_half_open_window_and_a_flow_paid_on_the_reference_date_is_inside_the_value():
    kw = dict(effective=dt.date(2024, 1, 8), maturity=dt.date(2027, 1, 8), fixed_rate=4.2, notional=5e7)
    days = [dt.date(2025, 1, 8), dt.date(2025, 1, 9), dt.date(2025, 1, 10), dt.date(2025, 1, 13)]  # first coupon is paid 2025-01-10 (accrual end 01-08 + 2 business days)
    ps = {d: pr(d, flat(d)) for d in days}
    o = build(ps[days[0]], **kw).obj
    val = {d: o.value(ctx=ctx(ps[d], ps[days[i - 1]] if i else None)) for i, d in enumerate(days)}
    assert val[days[1]].cash == 0.0 and val[days[2]].cash == 0.0  # a flow paid ON the reference date is not swept until the next mark
    assert val[days[3]].cash != 0.0 and abs(val[days[3]].cash) > 1e5
    # V(01-10) still holds the flow: no P&L jump at a stable market
    step = val[days[3]].pv + val[days[3]].cash - val[days[2]].pv
    assert abs(step) < 0.02 * abs(val[days[3]].cash), step
    assert abs(val[days[2]].pv - val[days[1]].pv) < 0.02 * abs(val[days[3]].cash)  # and none on 01-09 -> 01-10 either


def test_a_matured_swap_is_worth_zero_pays_its_last_flows_once_and_has_no_rate():
    kw = dict(effective=dt.date(2024, 1, 8), maturity=dt.date(2025, 1, 8), fixed_rate=4.0, notional=1e7)
    days = [dt.date(2025, 1, 10), dt.date(2025, 1, 13), dt.date(2025, 1, 14)]  # last payment 2025-01-10
    ps = {d: pr(d, flat(d)) for d in days}
    o = build(ps[days[0]], **kw).obj
    v = {d: o.value(ctx=ctx(ps[d], ps[days[i - 1]] if i else None)) for i, d in enumerate(days)}
    assert v[days[1]].pv == 0.0 and v[days[1]].cash != 0.0 and v[days[2]].cash == 0.0 and v[days[2]].pv == 0.0
    c = ctx(ps[days[2]], ps[days[1]])
    assert math.isnan(o.rate(ctx=c)) and o.dv01(ctx=c) == 0.0 and o.gamma(ctx=c) == 0.0 and set(o.delta_ladder(ctx=c).values()) == {0.0}
    assert all(o.decomposition(c)[k] == 0.0 for k in ("carry", "roll", "delta", "convexity"))


# ------------------------------------------------------------------ layers
def test_the_layers_need_a_previous_pricer():
    p = pr()
    o = build(p, effective=dt.date(2024, 1, 8), maturity=dt.date(2027, 1, 8), fixed_rate=4.2).obj
    with pytest.raises(ConfigError, match="previous pricer") as e:
        o.carry(ctx=ctx(p))
    assert e.value.code == "LAYER"


def test_the_layers_and_the_residual_add_up_to_the_interval_pnl_and_the_residual_is_tiny():
    d0, d1 = dt.date(2025, 1, 2), dt.date(2025, 1, 16)  # the seasoned swap's coupon is paid inside this interval
    p0, p1 = pr(d0, S.synthetic_nodes(d0)), pr(d1, S.synthetic_nodes(d1, bump=0.0009, slope=0.0046))
    o = build(p1, effective=dt.date(2024, 1, 8), maturity=dt.date(2027, 1, 8), fixed_rate=4.2, notional=5e7).obj
    c = ctx(p1, p0)
    d = o.decomposition(c)
    v0, v1 = o.value(ctx=ctx(p0)), o.value(ctx=c)
    assert d["V0"] == pytest.approx(v0.pv, abs=1e-6) and d["V1"] == pytest.approx(v1.pv, abs=1e-6) and d["cash"] == pytest.approx(v1.cash, abs=1e-6) and v1.cash != 0.0
    total = v1.pv + v1.cash - v0.pv
    assert d["carry"] + d["roll"] + d["delta"] + d["convexity"] + d["residual"] == pytest.approx(total, abs=1e-6)
    assert abs(d["residual"]) < 1e-3 * abs(total) and abs(d["convexity"]) > 1.0 and abs(d["delta"]) > 1e4
    assert (o.carry(ctx=c), o.roll(ctx=c), o.delta(ctx=c), o.convexity(ctx=c)) == (d["carry"], d["roll"], d["delta"], d["convexity"])


def test_full_revaluation_at_a_shocked_curve_equals_delta_plus_convexity_and_delta_alone_is_not_enough():
    """Same reference date (an intraday shock): carry and roll vanish and delta + convexity must reproduce the full revaluation; a 25bp shock exercises convexity."""
    p0 = pr(REF, S.synthetic_nodes(REF), hour=10)
    p1 = pr(REF, S.synthetic_nodes(REF, bump=0.0025), hour=11)
    o = build(p0, effective=dt.date(2024, 1, 8), maturity=dt.date(2034, 1, 8), fixed_rate=3.8, notional=1e8).obj
    c = ctx(p1, p0)
    fd = o.value(ctx=c).pv - o.value(ctx=ctx(p0)).pv
    d = o.decomposition(c)
    assert abs(d["carry"]) < 1e-6 and abs(d["roll"]) < 1e-6
    assert abs(fd - (d["delta"] + d["convexity"])) < 0.02 * abs(d["convexity"])  # the third-order remainder is under 1% of the second-order term
    assert abs(fd - d["delta"]) > 100.0 and abs(d["convexity"]) > 100.0, "the check must exercise convexity"


def test_carry_explains_a_market_that_did_not_move_and_roll_is_the_fixings_surprise():
    """Flat forwards + published fixings equal to the forward overnight rates: carry alone explains the interval and roll, delta, convexity vanish.
    Publish 4.0% instead and the whole difference lands in `roll` (realised minus assumed fixings), nowhere else."""
    d0, d1 = dt.date(2024, 6, 12), dt.date(2024, 6, 14)

    def fwd_fixings(shift_percent):
        """The forward overnight rate of the flat curve on every business day; `shift_percent` is added ONLY to the days that fix after t0."""
        out = {}
        for d in S.bdays(dt.date(2022, 1, 3), d1 - dt.timedelta(days=1), HOL):
            n = (S.add_bdays(d, 1, HOL) - d).days
            out[d] = (math.exp(0.04 * n / 365.0) - 1.0) * 360.0 / n * 100.0 + (shift_percent if d >= d0 else 0.0)
        return out

    def run(fx):
        p0, p1 = pr(d0, flat(d0), fixings=fx), pr(d1, flat(d1), fixings=fx)  # p0 only sees the fixings dated before t0
        o = build(p1, effective=dt.date(2023, 9, 6), maturity=dt.date(2028, 9, 6), fixed_rate=4.0, notional=5e7).obj
        c = ctx(p1, p0)
        total = o.value(ctx=c).pv + o.value(ctx=c).cash - o.value(ctx=ctx(p0)).pv
        return o.decomposition(c), total

    d, total = run(fwd_fixings(0.0))
    assert abs(total) > 10 and d["carry"] == pytest.approx(total, abs=1e-6)
    assert abs(d["roll"]) < 1e-6 and abs(d["delta"]) < 1e-6 and abs(d["convexity"]) < 1e-6
    d2, total2 = run(fwd_fixings(0.05))  # the realised overnight rate was 5bp above the forward on the days that fixed since t0
    assert d2["carry"] == pytest.approx(d["carry"], abs=1e-6) and d2["roll"] > 100 and d2["delta"] == pytest.approx(0.0, abs=1e-6)
    assert d2["roll"] == pytest.approx(5e7 * 0.05e-2 * 2 / 360.0 * (1 + 0.04 * 0.5), rel=0.02)  # 2 days x 5bp on 50mm, compounded on the rest of the period


# ------------------------------------------------------------------ zero-rate measures against full revaluation
def test_dv01_zero_and_gamma_zero_match_full_revaluation_under_a_parallel_zero_shift():
    p = pr()
    o = build(p, effective=dt.date(2024, 1, 8), maturity=dt.date(2029, 1, 8), fixed_rate=3.6, notional=5e7).obj
    dates, dfs = S.synthetic_nodes(REF)

    def pv(bp):
        sh = [v * math.exp(-bp * 1e-4 * (d - REF).days / 365.0) for d, v in zip(dates, dfs)]
        return o.value(ctx=ctx(pr(REF, (dates, sh)))).pv

    h = 0.5
    assert o.dv01_zero(ctx=ctx(p)) == pytest.approx((pv(h) - pv(-h)) / (2 * h), rel=1e-9)
    assert o.gamma_zero(ctx=ctx(p)) == pytest.approx(pv(1.0) - 2 * pv(0.0) + pv(-1.0), rel=1e-6)


# ------------------------------------------------------------------ started or not
def test_a_swap_that_has_not_started_needs_no_fixings_and_one_that_starts_today_is_not_started():
    nofx = pr(fixings=None)
    for kw in ({}, {"effective": REF, "maturity": dt.date(2029, 6, 12), "fixed_rate": 4.0}):
        o = build(nofx, **kw).obj
        c = ctx(nofx)
        assert math.isfinite(o.value(ctx=c).pv) and math.isfinite(o.dv01(ctx=c)) and math.isfinite(o.gamma(ctx=c)) and sum(o.delta_ladder(ctx=c).values()) != 0.0
        assert not o.started(nofx)
    started = build(nofx, effective=REF - dt.timedelta(days=1), maturity=dt.date(2029, 6, 12), fixed_rate=4.0).obj
    assert started.started(nofx)
    with pytest.raises(MarketDataUnavailable, match="no fixings"):
        started.value(ctx=ctx(nofx))


def test_a_fixed_rate_that_is_neither_a_number_nor_par_is_rejected_by_the_factory_itself():
    from pricebt.contrib.quantlib.swap import swap_factory

    p = pr()
    terms = {"side": "pay", "maturity": "5Y", "notional": 1e7, "direction": 1}
    with pytest.raises(ConfigError, match="number"):
        swap_factory(p, p.ts, terms={**terms, "fixed_rate": "abc"}, conventions=CONV)
    assert swap_factory(p, p.ts, terms={**terms, "fixed_rate": " PAR "}, conventions=CONV).terms["fixed_rate"] > 0.5


def test_the_last_payment_date_is_still_a_valued_date_and_the_next_is_matured():
    kw = dict(effective=dt.date(2024, 1, 8), maturity=dt.date(2025, 1, 8), fixed_rate=4.0, notional=1e7)
    last, after = dt.date(2025, 1, 10), dt.date(2025, 1, 13)
    o = build(pr(last, flat(last)), **kw).obj
    on_last, next_day = pr(last, flat(last)), pr(after, flat(after))
    assert o.value(ctx=ctx(on_last)).pv != 0.0 and not o.matured(on_last) and o.matured(next_day)


# ------------------------------------------------------------------ more edges found by mutation checking
def test_a_maturity_on_or_before_the_effective_date_is_rejected():
    p = pr()
    with pytest.raises(ConfigError, match="must be after") as e:
        build(p, effective=dt.date(2025, 1, 8), maturity=dt.date(2024, 1, 8), fixed_rate=4.0)
    assert e.value.code == "CFG-DATES"
    with pytest.raises(ConfigError, match="must be after"):
        build(p, effective=dt.date(2025, 1, 8), maturity=dt.date(2025, 1, 8), fixed_rate=4.0)


def test_two_tenors_that_land_on_the_same_maturity_are_rejected_for_the_ladder():
    p = pr()
    o = build(p, maturity="5Y").obj
    with pytest.raises(ConfigError, match="duplicate maturities") as e:
        o.delta_ladder(ctx=ctx(p), tenors=["12M", "1Y"])
    assert e.value.code == "LADDER"


def test_two_positions_sharing_one_mark_context_keep_their_own_ladder_and_layers():
    """`ctx.cache` is per position in the engine, but a shared one must not mix positions up either."""
    p, prev = pr(), pr(dt.date(2024, 6, 10))
    a = build(p, maturity="5Y", fixed_rate=3.9).obj
    b = build(p, maturity="10Y", fixed_rate=4.4, side="receive").obj
    shared = ctx(p, prev)
    la, lb = a.delta_ladder(ctx=shared), b.delta_ladder(ctx=shared)
    assert la != lb and la == a.delta_ladder(ctx=ctx(p, prev)) and lb == b.delta_ladder(ctx=ctx(p, prev))
    da, db = a.decomposition(shared), b.decomposition(shared)
    assert da != db and da == a.decomposition(ctx(p, prev)) and db == b.decomposition(ctx(p, prev))


def test_cash_over_a_window_with_two_payment_dates_stops_before_the_reference_date_when_a_flow_is_paid_on_it():
    """Semiannual swap paying 2024-07-10 and 2025-01-10: a mark ON the second payment date sweeps the first flow only."""
    conv = {**CONV, "frequency": "semiannual"}
    t0, on_pay, day_before = dt.date(2024, 7, 5), dt.date(2025, 1, 10), dt.date(2025, 1, 9)
    p0 = pr(t0, flat(t0))
    o = S.template(swap, "swap", conv, {"side": "pay", "effective": dt.date(2024, 1, 8), "maturity": dt.date(2027, 1, 8), "fixed_rate": 4.2, "notional": 5e7}).build(p0, p0.ts).obj
    c_on, c_before = ctx(pr(on_pay, flat(on_pay)), p0), ctx(pr(day_before, flat(day_before)), p0)
    first = o.value(ctx=c_before).cash
    assert abs(first) > 1e4 and o.value(ctx=c_on).cash == pytest.approx(first, abs=1e-9)


def test_a_flow_paid_on_the_end_of_the_layer_interval_is_not_cash_of_that_interval():
    kw = dict(effective=dt.date(2024, 1, 8), maturity=dt.date(2027, 1, 8), fixed_rate=4.2, notional=5e7)
    d0, d1 = dt.date(2025, 1, 9), dt.date(2025, 1, 10)  # the first coupon is paid on d1
    p0, p1 = pr(d0, flat(d0)), pr(d1, flat(d1))
    o = build(p1, **kw).obj
    c = ctx(p1, p0)
    d = o.decomposition(c)
    assert d["cash"] == 0.0 and o.value(ctx=c).cash == 0.0
    assert abs(d["carry"]) < 5000.0 and abs(d["roll"]) < 5000.0 and abs(d["delta"]) < 5000.0  # nothing of coupon size (~1e5) leaks into a layer


# ------------------------------------------------------------------ copies
def test_the_swap_is_plain_data_it_deep_copies_and_pickles_and_prices_the_same():
    p = pr()
    o = build(p, effective=dt.date(2024, 1, 8), maturity=dt.date(2027, 1, 8), fixed_rate=4.2).obj
    v = o.value(ctx=ctx(p)).pv
    for c in (copy.deepcopy(o), pickle.loads(pickle.dumps(o))):
        assert c.unadjusted == o.unadjusted and c.fixed_rate == 4.2 and c.conv == o.conv
        assert c.value(ctx=ctx(p)).pv == v


def test_unusual_conventions_are_honoured_or_refused_at_build_time():
    p = pr()
    with pytest.raises(ConfigError, match="linear intraday"):
        tpl({**CONV, "time_accrual": "linear"}).build(p, p.ts)
    with pytest.raises(ConfigError, match="no calendar 'lon'"):
        tpl({**CONV, "calendar": "lon"}).build(p, p.ts)
    o = tpl({**CONV, "payment_lag_days": 0}).build(p, p.ts).obj
    o2 = tpl().build(p, p.ts).obj
    assert o.value(ctx=ctx(p)).pv != o2.value(ctx=ctx(p)).pv  # the lag reaches the pricing
