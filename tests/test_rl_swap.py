"""RLSwap and the `swap` kit on plain-data snapshots: the verified golden numbers, layer identities, units and signs, cash window, fixings guard, the factory's
resolution of dates and `par`, conformance of the kit's default binding block. rateslib only (the reference stack and QuantLib have their own files)."""
import calendar as pycal
import copy
import datetime as dt
import pickle

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("rateslib")
pytestmark = pytest.mark.adapter_rateslib

from quantlib_support import rl_reference as REF  # noqa: E402
from test_rl_common import GOLD_TERMS, SWAP_CONV, SCHEMAS, build, ctx, golden_world, nyc_data, par_nodes, rl_pricer, seeded_fixings, swap_template, template  # noqa: E402

from pricebt.contracts.binding import Env, call_binding  # noqa: E402
from pricebt.contracts.spec import build_spec  # noqa: E402
from pricebt.contrib import rateslib as RL  # noqa: E402
from pricebt.contrib.rateslib import DEFAULT_TENORS, RLSwap  # noqa: E402
from pricebt.contrib.rateslib import _compat as C  # noqa: E402
from pricebt.contrib.rateslib.conventions import swap_conventions  # noqa: E402
from pricebt.contrib.rateslib.layers import shifted_roll_curve  # noqa: E402
from pricebt.errors import ConfigError, MarketDataUnavailable  # noqa: E402
from pricebt.pricable import Valuation  # noqa: E402
from pricebt.snapshot import CalendarData  # noqa: E402

D = dt.datetime
GOLD = {"carry": -561.82, "roll": 12159.98, "fixings": -10627.91, "resample": 111.52, "delta": 70190.64, "convexity": -50.23, "residual": 0.02}
GOLD_TOTAL = 71222.20
FX = seeded_fixings()
HOL = nyc_data().holidays


@pytest.fixture(scope="module")
def world():
    """The seasoned 3y payer 50mm of the verified research (rateslib.md section 8), coupon paid inside [2025-01-02, 2025-01-16)."""
    return golden_world()


# ------------------------------------------------------------------ golden numbers and identities
def test_decomposition_reproduces_the_verified_research_numbers(world):
    sw, p0, p1 = world
    d = sw.decomposition(ctx(p1, p0))
    for k, v in GOLD.items():
        assert d.raw[k] == pytest.approx(v, abs=0.01), k
    assert d.total == pytest.approx(GOLD_TOTAL, abs=0.01)
    assert d.V0 == pytest.approx(-369827.43, abs=0.01) and d.V1 == pytest.approx(-212114.86, abs=0.01) and d.cash == pytest.approx(-86490.36, abs=0.01)
    f = d.folded()
    assert f["carry"] == pytest.approx(-561.82, abs=0.01) and f["roll"] == pytest.approx(1532.07, abs=0.01)
    assert f["delta"] == pytest.approx(70302.16, abs=0.01) and f["convexity"] == pytest.approx(-50.23, abs=0.01) and f["residual"] == pytest.approx(0.02, abs=0.01)


def test_the_kits_layer_bindings_return_the_folded_and_the_raw_golden_numbers(world):
    """Through `build_spec` + `call_binding`: the standard names carry the folded layers (roll includes the realised-versus-assumed fixings, delta the resampling),
    the adapter's extension layers the raw pieces (spec L2)."""
    _, p0, p1 = world
    t = template(RL.swap, SWAP_CONV, GOLD_TERMS, layers=("carry", "roll", "delta", "convexity", "rateslib.carry_fwd", "rateslib.roll_static", "rateslib.fixings", "rateslib.resample"))
    obj = t.build(p0, p0.ts).obj
    env = Env(pricer=p1, ctx=ctx(p1, p0), instrument=obj, terms={})
    got = {name: call_binding(t.spec.bindings[name], env) for name in t.spec.layers}
    want = {"carry": -561.82, "roll": 1532.07, "delta": 70302.16, "convexity": -50.23, "rateslib.carry_fwd": -561.82, "rateslib.roll_static": 12159.98, "rateslib.fixings": -10627.91,
            "rateslib.resample": 111.52}
    for k, v in want.items():
        assert got[k] == pytest.approx(v, abs=0.01), k
    assert got["roll"] == pytest.approx(got["rateslib.roll_static"] + got["rateslib.fixings"], abs=1e-9)
    assert got["delta"] == pytest.approx(70190.64 + got["rateslib.resample"], abs=0.01)


def test_value_path_and_decomposition_path_agree_on_total(world):
    sw, p0, p1 = world
    v0, v1 = sw.value(ctx=ctx(p0)), sw.value(ctx=ctx(p1, p0))
    assert isinstance(v1, Valuation) and v1.financing == 0.0
    assert v0.cash == 0.0 and v1.cash == pytest.approx(-86490.36, abs=0.01)
    assert v1.pv - v0.pv + v1.cash == pytest.approx(GOLD_TOTAL, abs=0.01)
    assert v0.pv == pytest.approx(-369827.43, abs=0.01) and v1.pv == pytest.approx(-212114.86, abs=0.01)


def test_carry_equals_cashflow_table_forward_value(world):
    """Independent route to X_fwd: discount the t0 cash-flow table to t1 by hand and add the face cash paid in the window."""
    sw, p0, p1 = world
    t0, t1 = D(2025, 1, 2), D(2025, 1, 16)
    sw._prep(p0)
    tab = sw._flows(p0.curve)
    after = tab[tab.Payment >= t1]
    cash = float(tab[(tab.Payment >= t0) & (tab.Payment < t1)].Cashflow.sum())
    x_fwd = float(sum(cf * float(p0.curve[p]) for cf, p in zip(after.Cashflow, after.Payment))) / float(p0.curve[t1]) + cash
    d = sw.decomposition(ctx(p1, p0))
    assert x_fwd == pytest.approx(d.raw["carry"] + d.V0, abs=1e-6)


def test_the_identity_check_fails_when_cash_is_corrupted(world):
    sw, p0, p1 = world
    t0, t1 = D(2025, 1, 2), D(2025, 1, 16)
    sw._prep(p0)
    tab = sw._flows(p0.curve)
    bad = tab.copy()
    bad.loc[bad.Payment < t1, "Cashflow"] += 1000.0

    def x(t):
        after = t[t.Payment >= t1]
        return float(sum(cf * float(p0.curve[p]) for cf, p in zip(after.Cashflow, after.Payment))) / float(p0.curve[t1]) + float(t[(t.Payment >= t0) & (t.Payment < t1)].Cashflow.sum())

    d = sw.decomposition(ctx(p1, p0))
    truth = d.raw["carry"] + d.V0
    assert abs(x(tab) - truth) < 1e-6 and abs(x(bad) - truth) > 500


def test_zero_risk_matches_finite_difference_revaluation(world):
    """delta + convexity vs FULL revaluation at bumped zero rates (independent of the AD path); the third-order remainder is tiny."""
    sw, p0, p1 = world
    zr = sw.decomposition(ctx(p1, p0)).zero_risk
    t1 = D(2025, 1, 16)
    nodes = {t1: 1.0, **dict(zip(zr.dates, zr.df))}
    rng = np.random.default_rng(3)
    dz = 25e-4 + 10e-4 * np.linspace(0, 1, len(zr.dates)) + 3e-4 * rng.standard_normal(len(zr.dates))
    bumped = {t1: 1.0, **{d: float(df * np.exp(-z * tau)) for d, df, z, tau in zip(zr.dates, zr.df, dz, zr.tau)}}
    sw._prep(p1)
    fd = sw._npv(C.float_curve(bumped, "bb")) - sw._npv(C.float_curve(nodes, "bb"))
    first = float(zr.grad @ dz)
    second = 0.5 * float(dz @ zr.hess @ dz)
    assert abs(second) > 100.0, "the test must exercise convexity"
    assert abs(fd - (first + second)) < 3.0  # third-order remainder ~1.3 vs a 716 second-order term
    assert abs(fd - first) > 100.0, "first order alone must NOT be enough (checker discriminates)"


def test_fast_roll_curve_equals_rateslib_roll_translate(world):
    _, p0, _ = world
    c0, t0 = p0.curve, D(2025, 1, 2)
    rng = np.random.default_rng(1)
    for n in (1, 3, 14):
        t1 = t0 + dt.timedelta(days=n)
        fast, ref = shifted_roll_curve(c0, t0, t1), c0.roll(n).translate(t1)
        for k in rng.integers(1, 365 * 28, 60):
            d = t1 + dt.timedelta(days=int(k))
            assert float(fast[d]) == pytest.approx(float(ref[d]), abs=1e-12)


@pytest.mark.parametrize("d0,d1,n", [("2025-01-10", "2025-01-13", 3), ("2025-01-13", "2025-01-14", 1)])
def test_roll_uses_calendar_days_between_the_two_snapshots(d0, d1, n):
    """Friday -> Monday: N=3 calendar days (roll('1b') would shift by one day and contaminate the roll layer by 3x); Monday -> Tuesday: N=1, a one-day roll is a roll."""
    p0, p1 = rl_pricer(d0, 0, 0, FX), rl_pricer(d1, 0, 0, FX)
    sw = build(p0, effective=dt.date(2025, 3, 5), maturity=dt.date(2030, 3, 5), notional=50e6, fixed_rate=3.95).obj
    d = sw.decomposition(ctx(p1, p0))
    sw._prep(p0)
    t1 = D.fromisoformat(d1)

    def x(curve):
        return sw._npv(curve, forward=t1)

    c0 = p0.curve
    want = x(c0.roll(n)) - x(c0)
    assert d.raw["roll"] == pytest.approx(want, abs=1e-6) and abs(want) > 10.0
    if n > 1:
        assert abs(want - (x(c0.roll(1)) - x(c0))) > 10.0, "control: rolling by business days (1b) would give another number"


def test_intraday_same_reference_date_has_no_time_layers():
    p0, p1 = rl_pricer("2025-01-08", 0, 0, FX, hour=10), rl_pricer("2025-01-08", 2, 4, FX, hour=11)
    sw = build(p0, effective=dt.date(2025, 1, 10), maturity=dt.date(2030, 1, 10), notional=50e6, fixed_rate=3.95).obj
    d = sw.decomposition(ctx(p1, p0))
    assert d.raw["carry"] == pytest.approx(0.0, abs=1e-8) and d.raw["roll"] == pytest.approx(0.0, abs=1e-8)
    assert d.total == pytest.approx(sum(d.raw.values()), abs=1e-6)
    assert d.raw["delta"] > 0 and d.total > 0  # payer gains when the curve shifts up


def test_payer_receiver_mirror_on_every_layer_and_measure(world):
    _, p0, p1 = world
    pay, rec = (build(p0, **{**GOLD_TERMS, "side": s}).obj for s in ("pay", "receive"))
    cp, cr = ctx(p1, p0), ctx(p1, p0)
    vp, vr = pay.value(ctx=cp), rec.value(ctx=cr)
    assert vp.pv == pytest.approx(-vr.pv, abs=1e-6) and vp.cash == pytest.approx(-vr.cash, abs=1e-6)
    dp, dr = pay.decomposition(cp), rec.decomposition(cr)
    for k in GOLD:
        assert dp.raw[k] == pytest.approx(-dr.raw[k], abs=1e-6), k
    assert pay.dv01(ctx=cp) == pytest.approx(-rec.dv01(ctx=cr), rel=1e-12) and pay.dv01(ctx=cp) > 0
    assert pay.dv01_zero(ctx=cp) == pytest.approx(-rec.dv01_zero(ctx=cr), rel=1e-9)
    assert pay.gamma_zero(ctx=cp) == pytest.approx(-rec.gamma_zero(ctx=cr), rel=1e-9)
    assert pay.gamma(ctx=cp) == pytest.approx(-rec.gamma(ctx=cr), rel=1e-9)
    for name in ("carry", "roll", "delta", "convexity"):
        assert getattr(pay, name)(ctx=cp) == pytest.approx(-getattr(rec, name)(ctx=cr), abs=1e-6), name
    lp, lr = pay.delta_ladder(ctx=cp), rec.delta_ladder(ctx=cr)
    assert all(lp[t] == pytest.approx(-lr[t], rel=1e-9, abs=1e-6) for t in lp)
    assert pay.rate(ctx=cp) == pytest.approx(rec.rate(ctx=cr), rel=1e-12)


def test_dv01_of_a_fresh_swap_is_the_value_of_one_bp_of_fixed_rate():
    p = rl_pricer("2025-01-16", 3, 15)
    a, b = (build(p, maturity="5Y", notional=50e6, fixed_rate=k).obj for k in (4.20, 4.19))
    assert b.value(ctx=ctx(p)).pv - a.value(ctx=ctx(p)).pv == pytest.approx(a.dv01(ctx=ctx(p)), rel=1e-9)
    assert a.dv01(ctx=ctx(p)) > 0


def test_ad_dv01_and_gamma_of_the_zero_curve_match_a_parallel_zero_bump(world):
    sw, _, p1 = world
    c = ctx(p1)
    curve = p1.curve
    t = D(2025, 1, 16)
    sw._prep(p1)

    def v(h_bp):
        nodes = {d: float(x) * np.exp(-h_bp * 1e-4 * (d - t).days / 365.0) for d, x in curve.nodes.nodes.items()}
        return sw._npv(C.float_curve(nodes, "pp"))

    h = 0.5
    assert sw.dv01_zero(ctx=c) == pytest.approx((v(h) - v(-h)) / (2 * h), rel=1e-6)
    assert sw.gamma_zero(ctx=c) == pytest.approx((v(1.0) - 2 * v(0.0) + v(-1.0)) / 1.0, rel=2e-3)
    assert sw.dv01_zero(ctx=c) == pytest.approx(sw.dv01(ctx=c), rel=0.05)  # the zero-space and the par-space dv01 agree to a few percent


def ref_gamma(nd, position, tol, h=1.0):
    """Second difference of `position`'s PV for a parallel +-h bp move of every pillar par rate, on an independently built par-swap risk curve (`rl_reference`)."""
    dates, dfs, cal = [d for d, _ in nd], [v for _, v in nd], REF.cal_of(HOL)
    pv = lambda s: float(position.npv(curves=REF.build_ladder(dates, dfs, cal, list(DEFAULT_TENORS), shift_bp=s, tol=tol)[0]))  # noqa: E731
    return (pv(h) + pv(-h) - 2 * pv(0.0)) / h**2


def test_gamma_is_the_second_difference_of_a_parallel_par_rate_move_on_the_risk_curve():
    """`gamma` is d2 PV / dbp^2 for +-1bp on every pillar's par rate, re-solved. Pinned twice: against an independent construction at the SHIPPED solver tolerance
    (same algorithm, nothing shared: rel 1e-9), and against one at a tolerance far below it (the shipped tolerance is noisy by 2e-4 in the second difference)."""
    p = rl_pricer("2025-01-16", 3, 15)
    o = build(p, maturity="10Y", fixed_rate=4.0, notional=1e7).obj
    o._prep(p)
    nd = par_nodes(dt.date(2025, 1, 16), 3.0, 15.0)
    g = o.gamma(ctx=ctx(p))
    assert g == pytest.approx(ref_gamma(nd, o._active, (1e-14, 1e-15)), rel=1e-9)
    assert g == pytest.approx(REF.parallel_gamma([d for d, _ in nd], [v for _, v in nd], REF.cal_of(HOL), list(DEFAULT_TENORS), o._active, 1.0), rel=1e-3)
    assert g < 0, "a payer's gamma is negative on this curve"
    assert g != pytest.approx(ref_gamma(nd, o._active, (1e-14, 1e-15), h=25.0), rel=1e-5), "control: a 25bp second difference is a different number, so the 1bp size is pinned"


# ------------------------------------------------------------------ cash sweep
def test_cash_window_is_half_open_and_matches_the_cashflow_table():
    days = ["2025-01-08", "2025-01-09", "2025-01-10", "2025-01-13"]
    ps = {d: rl_pricer(d, 0, 0, FX) for d in days}
    sw = build(ps[days[0]], **GOLD_TERMS).obj
    pay_day = [p for p in sw._pay_dates if p.year == 2025][0]  # first coupon
    assert pay_day == D(2025, 1, 10)
    cash = {b: sw.value(ctx=ctx(ps[b], ps[a])).cash for a, b in zip(days[:-1], days[1:])}
    assert cash["2025-01-09"] == 0.0 and cash["2025-01-10"] == 0.0, "a flow paid on D is inside V(D): not swept until D+1"
    sw._prep(ps["2025-01-10"])
    tab = sw._flows(ps["2025-01-10"].curve)
    assert cash["2025-01-13"] == pytest.approx(float(tab[tab.Payment == pay_day].Cashflow.sum()), abs=1e-6) and cash["2025-01-13"] != 0.0
    pv10, pv13 = sw.value(ctx=ctx(ps["2025-01-10"])).pv, sw.value(ctx=ctx(ps["2025-01-13"], ps["2025-01-10"]))
    assert abs(pv13.pv + pv13.cash - pv10) < 0.02 * abs(cash["2025-01-13"]), "no P&L jump across the payment date at a stable market"


def test_forward_start_swap_is_par_at_build_and_needs_no_fixings():
    p1 = rl_pricer("2025-01-16", 3, 15)  # no fixings on this pricer
    b = build(p1, maturity="5Y", effective="1Y", notional=25e6, fixed_rate="par")
    sw = b.obj
    assert b.terms["effective"] > p1.reference_date and isinstance(b.terms["fixed_rate"], float)
    v = sw.value(ctx=ctx(p1))
    assert abs(v.pv) < 25e6 * 1e-7 and v.cash == 0.0
    assert sw.rate(ctx=ctx(p1)) == pytest.approx(b.terms["fixed_rate"], abs=1e-12), "the resolved par rate IS the swap's own par rate"


def test_a_swap_effective_on_the_reference_date_has_not_started_and_needs_no_fixings():
    p = rl_pricer("2025-01-21")  # no fixings: a swap that starts today has none to read
    o = build(p, effective=dt.date(2025, 1, 21), maturity="5Y", notional=1e7, fixed_rate=4.0).obj
    assert o.value(ctx=ctx(p)).cash == 0.0 and o.dv01(ctx=ctx(p)) == pytest.approx(float(o._inst_fwd.analytic_delta(curves=p.curve, leg=1)), rel=1e-12)
    assert not o._started


def test_started_swap_requires_fixings_and_a_contiguous_history():
    sw = build(rl_pricer("2025-01-02", 0, 0, FX), **GOLD_TERMS).obj
    with pytest.raises(MarketDataUnavailable, match="no fixings"):
        sw.value(ctx=ctx(rl_pricer("2025-01-02")))
    short = rl_pricer("2025-01-02", 0, 0, FX[FX.index >= pd.Timestamp("2024-06-03")])
    with pytest.raises(ValueError, match="SOFR fixings missing between"):
        sw.value(ctx=ctx(short))
    stale = rl_pricer("2025-01-16", 3, 15, FX[FX.index < pd.Timestamp("2025-01-13")])  # feed hole: last two business days absent
    with pytest.raises(ValueError, match="SOFR fixings missing between"):
        sw.value(ctx=ctx(stale))  # without the guard rateslib would silently return a ~10mm error (see test_rl_compat)


def test_matured_swap_is_worth_zero_and_pays_its_last_flows():
    p_prev, p_now = rl_pricer("2025-01-10", 0, 0, FX), rl_pricer("2025-01-13", 0, 0, FX)
    sw = build(p_prev, effective=dt.date(2024, 1, 8), maturity=dt.date(2025, 1, 8), notional=10e6, fixed_rate=4.0).obj
    assert max(sw._pay_dates) == D(2025, 1, 10)
    v = sw.value(ctx=ctx(p_now, p_prev))
    assert v.pv == 0.0 and v.cash != 0.0
    assert sw.value(ctx=ctx(p_prev)).pv != 0.0, "ON the last payment date the final flows are still inside the value (swept as cash the day after)"
    assert sw.value(ctx=ctx(rl_pricer("2025-01-14", 0, 0, FX), p_now)).cash == 0.0
    c = ctx(p_now, p_prev)
    assert np.isnan(sw.rate(ctx=c)) and sw.dv01(ctx=c) == 0.0 and sw.gamma(ctx=c) == 0.0 and sw.dv01_zero(ctx=c) == 0.0
    assert set(sw.delta_ladder(ctx=c).values()) == {0.0}


# ------------------------------------------------------------------ units and signs
def test_a_fixed_rate_term_of_4_prices_as_4_percent_not_400_and_not_0_04():
    """Payer NPV = notional * annuity * (par - K) and dv01 = notional * annuity * 1bp: so NPV = dv01 * (par - K) / 1bp exactly, whatever the curve."""
    p = rl_pricer("2025-01-16", 3, 15)
    par = build(p, maturity="7Y", fixed_rate="par").terms["fixed_rate"]
    o = build(p, maturity="7Y", fixed_rate=4.0).obj
    v, d = o.value(ctx=ctx(p)).pv, o.dv01(ctx=ctx(p))
    assert v == pytest.approx(d * (par - 4.0) * 100.0, rel=1e-9)
    assert abs(par - 4.0) > 0.1 and v != 0.0


def test_dv01_is_currency_per_bp_for_the_notional_as_built_and_scales_linearly():
    p = rl_pricer("2025-01-16")
    a, b = build(p, notional=1e7).obj.dv01(ctx=ctx(p)), build(p, notional=3.6e7).obj.dv01(ctx=ctx(p))
    assert 5_000 < a < 12_000  # a 10mm 10Y swap is roughly 8-9k per bp
    assert b == pytest.approx(3.6 * a, rel=1e-12)


# ------------------------------------------------------------------ dates, tenors and par: the factory resolves them on the snapshot calendar (T4)
def add_bdays(d, n):
    step = 1 if n >= 0 else -1
    while n:
        d += dt.timedelta(days=step)
        if d.weekday() < 5 and d not in HOL:
            n -= step
    return d


def add_months(d, n):
    y, m0 = divmod(d.year * 12 + d.month - 1 + n, 12)
    return dt.date(y, m0 + 1, min(d.day, pycal.monthrange(y, m0 + 1)[1]))


def modified_following(d):
    e = d
    while e.weekday() >= 5 or e in HOL:
        e += dt.timedelta(days=1)
    if e.month != d.month:
        e = d
        while e.weekday() >= 5 or e in HOL:
            e -= dt.timedelta(days=1)
    return e


@pytest.mark.parametrize("ref", ["2024-07-02", "2025-01-02", "2025-01-16", "2024-12-30", "2025-01-29"])
@pytest.mark.parametrize("tenor,months", [("3M", 3), ("1Y", 12), ("5Y", 60), ("10Y", 120)])
def test_spot_and_termination_dates_match_plain_python_over_the_snapshot_holidays(ref, tenor, months):
    r = dt.date.fromisoformat(ref)
    b = build(rl_pricer(ref), maturity=tenor)
    spot = add_bdays(r, 2)  # T+2 on the snapshot calendar: 2025-01-16 skips a weekend and MLK day, 2024-12-30 the New Year holiday
    assert b.terms["effective"] == spot and isinstance(b.terms["effective"], dt.date)
    assert b.terms["maturity"] == modified_following(add_months(spot, months)) and isinstance(b.terms["maturity"], dt.date)
    assert isinstance(b.terms["fixed_rate"], float) and b.terms["direction"] == 1 and b.terms["notional"] == 1e7


def test_the_schedule_does_not_roll_to_month_ends():
    """`end_of_month: false` (the shared block): an effective date of 2025-02-28 gives 2028-02-28, not the leap day 2028-02-29 an end-of-month roll would."""
    b = build(rl_pricer("2025-01-16"), effective=dt.date(2025, 2, 28), maturity="3Y")
    assert b.terms["maturity"] == modified_following(add_months(dt.date(2025, 2, 28), 36)) == dt.date(2028, 2, 28)


def test_a_forward_start_tenor_is_spot_rolled_forward_and_month_ends_clamp():
    p = rl_pricer("2025-01-29")  # spot is Friday 2025-01-31
    b = build(p, effective="1M", maturity="5Y")
    assert b.terms["effective"] == dt.date(2025, 2, 28), "01-31 + 1M clamps to the last day of February"
    assert b.terms["maturity"] == modified_following(add_months(b.terms["effective"], 60))
    assert b.obj.effective == b.terms["effective"] and b.obj.termination == b.terms["maturity"]


def test_explicit_dates_pass_through_and_the_resolved_terms_round_trip():
    p = rl_pricer("2025-01-16")
    a = build(p, maturity="7Y")
    b = build(p, effective=a.terms["effective"], maturity=a.terms["maturity"], fixed_rate=a.terms["fixed_rate"])
    assert b.terms["effective"] == a.terms["effective"] and b.terms["maturity"] == a.terms["maturity"]
    assert a.obj.value(ctx=ctx(p)).pv == pytest.approx(b.obj.value(ctx=ctx(p)).pv, abs=1e-9)


def test_par_is_resolved_to_a_percent_rate_that_prices_at_zero_and_matches_the_fitted_quotes():
    p = rl_pricer("2025-01-02")
    b = build(p, maturity="5Y", fixed_rate="par")
    assert b.terms["fixed_rate"] == pytest.approx(3.92, abs=1e-6), "the curve was solver-fitted to 3.92 at 5Y (independent of the adapter)"
    assert build(p, maturity="10Y", fixed_rate="par").terms["fixed_rate"] == pytest.approx(4.08, abs=1e-6)
    assert abs(b.obj.value(ctx=ctx(p)).pv) < 1e-4 * 1e7 * 1e-4  # < 0.01 bp of notional
    assert build(p, maturity="5Y", effective="1Y", fixed_rate="par").terms["fixed_rate"] != pytest.approx(b.terms["fixed_rate"], abs=1e-4), "forward-starting differs"
    assert build(p, maturity="5Y", fixed_rate=4.0).terms["fixed_rate"] == 4.0


def test_a_spread_over_par_is_in_basis_points_and_moves_the_rate_by_hundredths_of_a_percent():
    p = rl_pricer("2025-01-16")
    par = build(p, maturity="5Y", fixed_rate="par")
    wide = build(p, maturity="5Y", fixed_rate="par", extras={"par_spread_bp": 25.0})
    assert wide.terms["fixed_rate"] == pytest.approx(par.terms["fixed_rate"] + 0.25, abs=1e-12)
    assert wide.obj.value(ctx=ctx(p)).pv == pytest.approx(-25.0 * wide.obj.dv01(ctx=ctx(p)), rel=1e-9)
    assert build(p, maturity="5Y", fixed_rate="par", extras={"par_spread_bp": -10}).terms["fixed_rate"] == pytest.approx(par.terms["fixed_rate"] - 0.10, abs=1e-12)


def test_the_factory_refuses_what_it_cannot_resolve():
    p = rl_pricer("2025-01-16", 0, 0, FX)
    with pytest.raises(ConfigError, match="par needs an effective date on or after the reference date") as e:
        build(p, effective=dt.date(2024, 3, 4), fixed_rate="par")
    assert e.value.code == "TERMS"
    with pytest.raises(ConfigError, match="understands") as e:
        build(p, extras={"spread": 1})
    assert e.value.code == "CFG-EXTRAS"
    with pytest.raises(ConfigError, match="notional"):
        build(p, notional=-1e6)  # the direction is `side`, never the sign of the size
    with pytest.raises(ConfigError, match="effective"):
        build(p, effective="tomorrow")
    with pytest.raises(ConfigError, match="fixed_rate"):
        build(p, fixed_rate="cheap")
    conv = swap_conventions(SWAP_CONV)
    cal = p.calendar("nyc")
    kw = dict(effective=dt.date(2025, 3, 3), maturity=dt.date(2030, 3, 3), fixed_rate=4.0, conv=conv, cal=cal)
    with pytest.raises(ConfigError, match="sign"):
        RLSwap(sign=2, notional=1e6, **kw)
    with pytest.raises(ConfigError, match="unsigned"):
        RLSwap(sign=1, notional=-1e6, **kw)


def test_a_locked_numeric_rate_is_a_float_and_the_terminal_date_is_the_adjusted_one():
    p = rl_pricer("2025-01-16")
    b = build(p, maturity=dt.date(2031, 1, 21), effective=dt.date(2025, 1, 21), fixed_rate=4.5)
    assert isinstance(b.terms["fixed_rate"], float) and b.terms["fixed_rate"] == 4.5 and b.obj.termination == dt.date(2031, 1, 21) and b.terms["maturity"] == dt.date(2031, 1, 21)


def test_locked_rate_does_not_restrike_when_the_curve_moves():
    p1 = rl_pricer("2025-01-16", 3, 15)
    sw = build(p1, maturity="5Y", notional=10e6, fixed_rate="par").obj
    moved = rl_pricer("2025-01-16", 50, 50)
    assert abs(sw.value(ctx=ctx(moved)).pv) > 10e6 * 0.005  # +50bp payer: clearly non-zero (an unpriced IRS would re-par to 0)


def test_a_swap_needs_a_curve_in_the_snapshot():
    """A quote-panel snapshot without a funding curve wraps (bonds do not need one) but refuses a swap, as the old pricer refused `par_rate`, with a clear error."""
    from pricebt.snapshot import Quote, QuoteSet, Security

    sec = Security("S1", 4.0, dt.date(2024, 5, 15), dt.date(2034, 5, 15))
    p = rl_pricer("2025-01-16", curve=False, quotes=QuoteSet(dt.date(2025, 1, 16), {"S1": Quote(ytm=4.5)}, {"S1": sec}, {}, "market"))
    assert p.curve is None
    with pytest.raises(MarketDataUnavailable, match="funding curve"):
        build(p, fixed_rate=4.0)
    with pytest.raises(MarketDataUnavailable, match="funding curve"):
        build(p, fixed_rate="par")
    ok = build(rl_pricer("2025-01-16"), fixed_rate=4.0).obj
    with pytest.raises(MarketDataUnavailable, match="funding curve"):
        ok.value(ctx=ctx(p))


# ------------------------------------------------------------------ the kit: conformance, defaults, overrides, wrapped-pricer requirement
def test_the_kit_conforms_to_the_swap_schema_with_only_its_default_block():
    spec = build_spec("s", {"factory": RL.swap, "conventions": SWAP_CONV, "layers": ["carry", "roll", "delta", "convexity"]}, schemas=SCHEMAS)
    for name in ("value", "dv01", "gamma", "rate", "delta_ladder", "carry", "roll", "delta", "convexity"):
        assert name in spec.bindings
    assert spec.asset_class == "swap" and spec.layers == ("carry", "roll", "delta", "convexity")
    assert set(spec.extra) == {"dv01_zero", "gamma_zero", "rateslib.carry_fwd", "rateslib.roll_static", "rateslib.fixings", "rateslib.resample"}


def test_every_default_binding_runs_and_returns_its_contract_type(world):
    _, p0, p1 = world
    t = template(RL.swap, SWAP_CONV, GOLD_TERMS)
    obj = t.build(p0, p0.ts).obj
    env = Env(pricer=p1, ctx=ctx(p1, p0), instrument=obj, terms={})
    out = {name: call_binding(b, env) for name, b in t.spec.bindings.items()}
    assert isinstance(out["value"], Valuation)
    for k in ("dv01", "gamma", "rate", "carry", "roll", "delta", "convexity", "dv01_zero", "gamma_zero", "rateslib.carry_fwd", "rateslib.roll_static", "rateslib.fixings", "rateslib.resample"):
        assert isinstance(out[k], float) and np.isfinite(out[k]), k
    assert isinstance(out["delta_ladder"], dict) and list(out["delta_ladder"]) == list(DEFAULT_TENORS)
    assert all(isinstance(v, float) for v in out["delta_ladder"].values())
    assert sum(out["delta_ladder"].values()) == out["dv01"], "started swap: dv01 IS the ladder sum"
    c = ctx(p1, p0)
    methods = {"dv01": "dv01", "gamma": "gamma", "rate": "rate", "carry": "carry", "roll": "roll", "delta": "delta", "convexity": "convexity", "dv01_zero": "dv01_zero", "gamma_zero": "gamma_zero",
               "rateslib.carry_fwd": "carry_fwd", "rateslib.roll_static": "roll_static", "rateslib.fixings": "fixings_layer", "rateslib.resample": "resample"}
    for name, method in methods.items():
        assert out[name] == getattr(obj, method)(ctx=c), f"the default binding of {name!r} is the method {method!r}"
    assert out["gamma"] != out["gamma_zero"] and out["dv01"] != out["dv01_zero"], "the par-space and the zero-space figures are different numbers"


def test_a_user_binding_can_swap_gamma_for_the_zero_rate_gamma_and_dv01_for_the_zero_rate_dv01():
    p = rl_pricer("2025-01-16")
    t = template(RL.swap, SWAP_CONV, {"side": "pay", "maturity": "5Y", "notional": 1e7},
                 bind={"gamma": {"target": {"method": "gamma_zero"}, "kwargs": {"ctx": "@ctx"}}, "dv01": {"target": {"method": "dv01_zero"}, "kwargs": {"ctx": "@ctx"}}})
    obj = t.build(p, p.ts).obj
    env = Env(pricer=p, ctx=ctx(p), instrument=obj, terms={})
    assert call_binding(t.spec.bindings["gamma"], env) == obj.gamma_zero(ctx=ctx(p)) != obj.gamma(ctx=ctx(p))
    assert call_binding(t.spec.bindings["dv01"], env) == obj.dv01_zero(ctx=ctx(p)) != obj.dv01(ctx=ctx(p))


def test_a_binding_that_names_a_missing_method_is_refused_at_load_with_a_suggestion():
    with pytest.raises(ConfigError, match="did you mean") as e:
        template(RL.swap, SWAP_CONV, {"side": "pay", "maturity": "5Y", "notional": 1e7}, bind={"gamma": {"target": {"method": "gama"}, "kwargs": {"ctx": "@ctx"}}})
    assert e.value.code == "CFG-BINDING"


def test_a_pricer_that_is_not_wrapped_is_rejected_with_the_fix():
    from pricebt.snapshot import SnapshotPricer
    from test_rl_common import snapshot

    sp = SnapshotPricer(snapshot(dt.date(2025, 1, 16)))
    with pytest.raises(ConfigError, match="pricebt.contrib.rateslib:wrap") as e:
        swap_template().build(sp, sp.ts)
    assert e.value.code == "CFG-WRAP"


def test_a_calendar_missing_from_the_snapshot_is_named():
    p = rl_pricer("2025-01-16", calendars={"other": CalendarData("other")})
    with pytest.raises(ConfigError, match="no calendar 'nyc'") as e:
        build(p)
    assert e.value.code == "CFG-CONVENTION"


def test_cashflows_are_a_frame_of_the_remaining_flows(world):
    sw, _, p1 = world
    cf = sw.cashflows(ctx=ctx(p1))
    assert list(cf.columns) == ["Type", "Payment", "Cashflow", "DF", "NPV"] and len(cf) > 4
    assert cf["NPV"].sum() == pytest.approx(sw.value(ctx=ctx(p1)).pv, abs=1e-6)


def test_swap_is_picklable_and_deepcopyable():
    p = rl_pricer("2025-01-16")
    sw = build(p, effective=dt.date(2025, 3, 3), maturity=dt.date(2030, 3, 3), notional=1e6, fixed_rate=4.0).obj
    for c in (copy.deepcopy(sw), pickle.loads(pickle.dumps(sw))):
        assert c.fixed_rate == 4.0 and c._pay_dates == sw._pay_dates and c.sign == 1
        assert c.value(ctx=ctx(p)).pv == sw.value(ctx=ctx(p)).pv
