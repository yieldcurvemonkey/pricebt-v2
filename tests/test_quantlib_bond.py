"""QLBond and the `bond` kit: conformance, the golden UST numbers, yield rule, coupon dates, cash sweep, financing, layers. QuantLib only."""
import copy
import datetime as dt
import math
import pickle

import pytest

pytest.importorskip("QuantLib")
pytestmark = pytest.mark.adapter_quantlib

import QuantLib as ql  # noqa: E402
from quantlib_support import snapshots as S  # noqa: E402

from pricebt.contracts.binding import Env, call_binding  # noqa: E402
from pricebt.contracts.spec import build_spec  # noqa: E402
from pricebt.contrib.quantlib import UST_CONVENTIONS, bond, wrap  # noqa: E402
from pricebt.errors import ConfigError, MarketDataUnavailable  # noqa: E402
from pricebt.pricable import MarkContext, Valuation  # noqa: E402

HOL = S.synthetic_holidays()
ID = "TEST00001"
SEC = [(ID, 4.25, dt.date(2024, 11, 15), dt.date(2034, 11, 15))]
CONV = UST_CONVENTIONS
LAYERS = ("carry", "roll", "delta", "convexity")


def pr(d, y=None, clean=None, *, price_convention="market", hour=17, securities=SEC, extra=None, aliases=None, fixings=None, unit="percent"):
    quotes = {ID: {"ytm": y} if clean is None else {"clean": clean}}
    quotes.update(extra or {})
    q = S.quote_set(d, securities, quotes, aliases=aliases, price_convention=price_convention)
    return wrap(S.pricer_of(S.snapshot(d, nodes=False, hol=HOL, quotes=q, hour=hour, fixings=fixings, fixings_unit=unit)))


def tpl(**terms):
    terms = {"side": "long", "security": ID, "notional": 10e6, **terms}
    return S.template(bond, "bond", CONV, terms)


def make(p, **terms):
    return tpl(**terms).build(p, p.ts).obj


def cx(p, prev=None):
    return MarkContext.standalone(p, prev=prev)


def layers(b, c):
    return {k: getattr(b, k)(ctx=c) for k in LAYERS}


# ------------------------------------------------------------------ conformance
def test_conforms_to_the_bond_schema_with_only_the_kits_default_block():
    spec = build_spec("b", {"factory": bond, "conventions": CONV, "layers": list(LAYERS)}, schemas=S.SCHEMAS)
    for name in ("value", "dv01", "gamma", "rate", "ytm", "duration", "accrued") + LAYERS:
        assert name in spec.bindings
    assert spec.asset_class == "bond"


def test_every_default_binding_runs_and_the_rate_measure_is_the_yield_in_percent():
    p0, p1 = pr(dt.date(2025, 5, 12), 4.55), pr(dt.date(2025, 5, 20), 4.62)
    t = tpl(extras={"repo": {"gc_rate": 4.3}})
    obj = t.build(p1, p1.ts).obj
    env = Env(pricer=p1, ctx=cx(p1, p0), instrument=obj, terms={})
    out = {n: call_binding(b, env) for n, b in t.spec.bindings.items()}
    assert isinstance(out["value"], Valuation) and out["rate"] == out["ytm"] == 4.62
    assert all(isinstance(out[k], float) and math.isfinite(out[k]) for k in ("dv01", "gamma", "duration", "accrued") + LAYERS)


# ------------------------------------------------------------------ the golden numbers (10mm long, 4.25 Nov-34, 2025-05-12 y 4.55 -> 2025-05-20 y 4.62, coupon paid 05-15, repo 4.30)
@pytest.fixture(scope="module")
def golden():
    p0, p1 = pr(dt.date(2025, 5, 12), 4.55), pr(dt.date(2025, 5, 20), 4.62)
    b = make(p1, extras={"repo": {"gc_rate": 4.30}})
    return b, p0, p1, cx(p1, p0)


def test_marks_flows_and_financing_reproduce_the_independent_calculation(golden):
    b, p0, p1, c = golden
    v0, v1 = b.value(ctx=cx(p0)), b.value(ctx=c)
    assert v0.pv == pytest.approx(9979417.302, abs=0.01) and v1.pv == pytest.approx(9724039.126, abs=0.01)
    assert v1.cash == pytest.approx(212500.0, abs=1e-6) and v0.cash == 0.0 and v0.financing == 0.0
    assert v1.financing == pytest.approx(-9535.8876, abs=0.001)
    assert v1.pv - v0.pv + v1.cash + v1.financing == pytest.approx(-52414.064, abs=0.01)


def test_yield_space_layers_reproduce_the_independent_calculation(golden):
    b, p0, p1, c = golden
    L = layers(b, c)
    assert L["carry"] == pytest.approx(136.580, abs=0.01) and L["delta"] == pytest.approx(-52718.946, abs=0.01) and L["convexity"] == pytest.approx(168.692, abs=0.01)
    assert L["roll"] == 0.0  # no on-the-run curve in this snapshot
    v0, v1 = b.value(ctx=cx(p0)), b.value(ctx=c)
    total = v1.pv - v0.pv + v1.cash + v1.financing
    assert total - sum(L.values()) == pytest.approx(-0.3896, abs=0.01)


def test_full_revaluation_at_the_new_yield_equals_delta_plus_convexity_and_delta_alone_is_not_enough(golden):
    b, p0, p1, c = golden
    L = layers(b, c)
    at_old_yield = pr(dt.date(2025, 5, 20), 4.55)
    fd = b.value(ctx=c).pv - b.value(ctx=cx(at_old_yield)).pv
    assert abs(fd - (L["delta"] + L["convexity"])) < 1.0
    assert abs(fd - L["delta"]) > 100.0, "delta alone is not enough: the check must exercise convexity"


# ------------------------------------------------------------------ units and the yield rule
def treasury_dirty(y, coupon, settle, prev, nxt, n, simple_first=True):
    """The Treasury price/yield rule in closed form: simple interest over the first fractional period, compounding after (per 100 face)."""
    c, v = coupon / 2.0, 1.0 / (1.0 + y / 200.0)
    w = (nxt - settle).days / (nxt - prev).days
    v1 = 1.0 / (1.0 + w * y / 200.0) if simple_first else v**w
    return v1 * sum((c + (100.0 if i == n - 1 else 0.0)) * v**i for i in range(n))


def test_dirty_price_follows_the_treasury_rule_and_not_the_street_rule():
    d = dt.date(2025, 3, 12)
    m = make(pr(d, 4.5)).mark(pr(d, 4.5))
    want = treasury_dirty(4.5, 4.25, d, dt.date(2024, 11, 15), dt.date(2025, 5, 15), 20)
    assert m["dirty"] == pytest.approx(want, abs=1e-9)
    assert abs(treasury_dirty(4.5, 4.25, d, dt.date(2024, 11, 15), dt.date(2025, 5, 15), 20, simple_first=False) - want) > 1e-3  # the street rule is a different number
    assert m["accrued"] == pytest.approx(2.125 * (d - dt.date(2024, 11, 15)).days / (dt.date(2025, 5, 15) - dt.date(2024, 11, 15)).days, abs=1e-12)


def test_a_yield_and_a_coupon_in_percent_a_par_bond_on_a_coupon_date_is_worth_its_face():
    d = dt.date(2025, 5, 15)  # a coupon date
    p = pr(d, 4.25)
    b = make(p)
    assert b.mark(p)["dirty"] == pytest.approx(100.0, abs=1e-9)
    assert b.value(ctx=cx(p)).pv == pytest.approx(10e6 + 212500.0, abs=1e-6)  # face plus the coupon that is due (paid) today
    assert make(pr(d, 4.25)).mark(pr(d, 4.25))["ytm"] == 4.25  # the quote is used as given: percent
    q = pr(dt.date(2025, 5, 14), 4.25)
    assert make(q).value(ctx=cx(pr(dt.date(2025, 5, 16), 4.25), q)).cash == pytest.approx(212500.0, abs=1e-6)  # 4.25 percent / 2 of 10mm, not 0.0425 / 2 or 4.25 / 2


def test_a_clean_quote_and_a_yield_quote_give_the_same_mark_and_the_yield_comes_back():
    d = dt.date(2025, 3, 12)
    py = pr(d, 4.5)
    by = make(py)
    m = by.mark(py)
    pc = pr(d, clean=m["clean"])
    mc = by.mark(pc)
    assert mc["ytm"] == pytest.approx(4.5, abs=1e-9) and mc["dirty"] == pytest.approx(m["dirty"], abs=1e-9) and mc["dirty"] - mc["clean"] == pytest.approx(mc["accrued"], abs=1e-12)
    assert 0 < mc["accrued"] < 2.2


def test_accrued_is_zero_on_a_coupon_date_so_clean_is_dirty_and_a_clean_quote_solves_as_a_dirty_price():
    d = dt.date(2025, 5, 15)
    p = pr(d, clean=99.0, price_convention="market")
    m = make(p).mark(p)
    assert m["accrued"] == 0.0 and m["dirty"] == 99.0 and m["clean"] == 99.0
    y = m["ytm"]
    assert make(pr(d, y)).mark(pr(d, y))["dirty"] == pytest.approx(99.0, abs=1e-9)


def test_a_clean_quote_on_a_coupon_date_needs_the_market_convention_to_be_declared():
    d = dt.date(2025, 5, 15)
    p = pr(d, clean=99.0, price_convention="unspecified")
    with pytest.raises(ConfigError, match="price_convention") as e:
        make(p).mark(p)
    assert e.value.code == "PRICE-CONVENTION"
    off = pr(dt.date(2025, 5, 14), clean=99.0, price_convention="unspecified")  # off a coupon date the two conventions agree
    assert make(off).mark(off)["dirty"] > 99.0
    yq = pr(d, 4.4, price_convention="unspecified")  # a yield quote has no convention to declare
    assert make(yq).mark(yq)["ytm"] == 4.4


# ------------------------------------------------------------------ cash window, coupon dates, payment on the reference date
def steps_over(days, y=4.5):
    ps = {d: pr(d, y) for d in days}
    b = make(ps[days[0]])
    v = {d: b.value(ctx=cx(ps[d], ps[days[i - 1]] if i else None)) for i, d in enumerate(days)}
    return b, ps, v


def test_a_coupon_paid_on_D_is_inside_V_D_and_swept_at_the_next_mark_with_no_pnl_jump():
    days = [dt.date(2025, 5, 13), dt.date(2025, 5, 14), dt.date(2025, 5, 15), dt.date(2025, 5, 16), dt.date(2025, 5, 19)]
    _, _, v = steps_over(days)
    assert [v[d].cash for d in days[1:]] == pytest.approx([0.0, 0.0, 212500.0, 0.0], abs=1e-6)
    steps = [v[d].pv - v[days[i]].pv + v[d].cash for i, d in enumerate(days[1:])]
    assert all(1000.0 < s < 4000.0 for s in steps), steps  # smooth accrual (~1.2k a day, ~3.5k over the weekend), never ~212k
    assert v[days[2]].pv - v[days[3]].pv == pytest.approx(212500.0 - steps[2], abs=1e-6)
    assert abs(v[days[3]].pv - v[days[2]].pv) > 200000.0, "without the cash sweep the equity curve would drop by the coupon"


def test_a_coupon_falling_on_a_weekend_is_paid_on_the_next_business_day():
    """2025-11-15 is a Saturday: the accrual ends then, the coupon is PAID Monday 11-17, so it is inside V(11-17) and swept at 11-18; QuantLib's own yield math never sees the payment date."""
    days = [dt.date(2025, 11, 13), dt.date(2025, 11, 14), dt.date(2025, 11, 17), dt.date(2025, 11, 18)]
    assert dt.date(2025, 11, 15).weekday() == 5
    _, _, v = steps_over(days)
    assert v[days[1]].cash == 0.0 and v[days[2]].cash == 0.0 and v[days[3]].cash == pytest.approx(212500.0, abs=1e-6)
    assert abs(v[days[2]].pv - v[days[1]].pv) < 6000.0  # no drop of the coupon between Friday and Monday: V(Monday) still holds it as a flow due today
    assert v[days[3]].pv - v[days[2]].pv < -200000.0 and abs(v[days[3]].pv + v[days[3]].cash - v[days[2]].pv) < 3000.0


def test_the_bond_matures_pays_principal_and_last_coupon_then_is_worth_zero():
    sec = [("TEST00002", 4.0, dt.date(2023, 5, 15), dt.date(2025, 5, 15))]
    days = [dt.date(2025, 5, 14), dt.date(2025, 5, 15), dt.date(2025, 5, 16), dt.date(2025, 5, 19)]

    def at(d):
        q = S.quote_set(d, sec, {"TEST00002": {"ytm": 4.0}}) if d <= dt.date(2025, 5, 15) else S.quote_set(d, sec, {})
        return wrap(S.pricer_of(S.snapshot(d, nodes=False, hol=HOL, quotes=q)))

    ps = {d: at(d) for d in days}
    b = tpl(security="TEST00002").build(ps[days[0]], ps[days[0]].ts).obj
    v = {d: b.value(ctx=cx(ps[d], ps[days[i - 1]] if i else None)) for i, d in enumerate(days)}
    assert b.value(ctx=cx(ps[days[1]])).pv > 10e6 and v[days[1]].cash == 0.0  # the redemption is due today: inside V(05-15)
    assert math.isnan(v[days[1]].meta["ytm"]) and v[days[1]].meta["dirty"] == 0.0  # nothing left to price on the last payment date
    with pytest.raises(ConfigError, match="last payment"):
        b.dv01(ctx=cx(ps[days[1]], ps[days[0]]))  # and no quote-based measure either
    assert v[days[2]].pv == 0.0 and v[days[2]].cash == pytest.approx(10e6 * (1 + 0.04 / 2), abs=1e-6)
    assert v[days[3]].cash == 0.0 and v[days[3]].pv == 0.0 and v[days[3]].financing == 0.0
    with pytest.raises(ConfigError, match="last payment"):
        b.dv01(ctx=cx(ps[days[3]], ps[days[2]]))
    assert layers(b, cx(ps[days[2]], ps[days[1]]))["delta"] == 0.0


# ------------------------------------------------------------------ financing
def test_financing_sign_scaling_haircut_specialness_and_the_elapsed_seconds():
    d0, d1 = dt.date(2025, 3, 12), dt.date(2025, 3, 13)
    p0, p1 = pr(d0, 4.5), pr(d1, 4.5)
    dirty0 = make(p0).mark(p0)["dirty"]
    day = 1e6 / 100 * dirty0 * 4.30 / 100 / 360

    def fin(side, repo, a=p0, b=p1):
        return make(a, side=side, notional=1e6, extras={"repo": repo}).value(ctx=cx(b, a)).financing

    assert fin("long", {"gc_rate": 4.30}) == pytest.approx(-day, rel=1e-12) and fin("short", {"gc_rate": 4.30}) == pytest.approx(day, rel=1e-12)
    hour = fin("long", {"gc_rate": 4.30}, pr(d0, 4.5, hour=17), pr(d0, 4.5, hour=18))
    assert hour == pytest.approx(-day / 24, rel=1e-12), "financing runs on elapsed seconds (ACT/360)"
    assert fin("long", {"gc_rate": 4.30, "haircut": 0.1, "specialness_bps": 50}) == pytest.approx(-day * 0.9 * (4.30 - 0.50) / 4.30, rel=1e-12)
    assert make(p0, notional=1e6).value(ctx=cx(p1, p0)).financing == 0.0  # no repo, no financing


def test_a_gc_rate_read_from_the_snapshot_fixings_is_percent_whatever_the_unit_and_a_stale_or_missing_series_raises():
    d0, d1 = dt.date(2025, 3, 12), dt.date(2025, 3, 13)
    fx = {dt.date(2025, 3, 7): 4.1, dt.date(2025, 3, 10): 4.2, dt.date(2025, 3, 11): 4.4}
    for unit, series in (("percent", fx), ("decimal", {d: v / 100 for d, v in fx.items()})):
        a, b = pr(d0, 4.5, fixings=series, unit=unit), pr(d1, 4.5, fixings=series, unit=unit)
        dirty0 = make(a).mark(a)["dirty"]
        f = make(a, notional=1e6, extras={"repo": {"gc_rate": "pricer"}}).value(ctx=cx(b, a)).financing
        assert f == pytest.approx(-1e6 / 100 * dirty0 * 4.4 / 100 / 360, rel=1e-9), unit  # the newest fixing dated before the START of the interval
    old = pr(dt.date(2025, 3, 25), 4.5, fixings=fx)
    with pytest.raises(MarketDataUnavailable, match="stale"):
        make(old, notional=1e6, extras={"repo": {"gc_rate": "pricer"}}).value(ctx=cx(pr(dt.date(2025, 3, 26), 4.5, fixings=fx), old))
    with pytest.raises(MarketDataUnavailable, match="no published fixing"):
        make(pr(d0, 4.5), extras={"repo": {"gc_rate": "pricer"}}).value(ctx=cx(pr(d1, 4.5), pr(d0, 4.5)))


# ------------------------------------------------------------------ mirror, measures
def test_long_and_short_are_exact_mirrors_on_value_flows_layers_and_measures(golden):
    b, p0, p1, c = golden
    s = make(p1, side="sell", extras={"repo": {"gc_rate": 4.30}})
    cs = cx(p1, p0)
    v, w = b.value(ctx=c), s.value(ctx=cs)
    assert w.pv == pytest.approx(-v.pv, abs=1e-6) and w.cash == pytest.approx(-v.cash) and w.financing == pytest.approx(-v.financing)
    Lb, Ls = layers(b, c), layers(s, cs)
    assert all(Ls[k] == pytest.approx(-Lb[k], abs=1e-6) for k in LAYERS)
    assert s.dv01(ctx=cs) == pytest.approx(-b.dv01(ctx=c)) and s.gamma(ctx=cs) == pytest.approx(-b.gamma(ctx=c))
    assert b.dv01(ctx=c) < 0 < s.dv01(ctx=cs), "a long bond LOSES when yields rise"
    assert b.gamma(ctx=c) > 0


def test_dv01_gamma_and_duration_match_full_revaluation_of_the_holder_value(golden):
    b, _, _, c = golden
    d = dt.date(2025, 5, 20)

    def pv(y):
        return b.value(ctx=cx(pr(d, y))).pv

    h = 0.01  # 1bp in percent
    assert b.dv01(ctx=c) == pytest.approx((pv(4.62 + h) - pv(4.62 - h)) / 2.0, rel=1e-6)
    assert b.gamma(ctx=c) == pytest.approx(pv(4.62 + h) - 2 * pv(4.62) + pv(4.62 - h), rel=1e-5)
    dirty = b.mark(c.pricer)["dirty"]
    assert b.duration(ctx=c) == pytest.approx(-b.dv01(ctx=c) * 1e6 / (b.notional * dirty), rel=1e-12) and 7 < b.duration(ctx=c) < 8


# ------------------------------------------------------------------ the on-the-run curve and the roll layer
def test_roll_layer_from_an_on_the_run_curve_only_moves_value_between_roll_and_delta():
    sec = SEC + [("CT2XXXXXX", 3.0, dt.date(2024, 3, 15), dt.date(2027, 3, 15)), ("CT10XXXXX", 4.0, dt.date(2025, 11, 15), dt.date(2035, 11, 15))]
    aliases = {"CT2": "CT2XXXXXX", "CT10": "CT10XXXXX"}

    def at(d, curve=True):
        extra = {"CT2XXXXXX": {"ytm": 3.4}, "CT10XXXXX": {"ytm": 5.0}}  # an upward curve: 3.4% at ~2y, 5.0% at ~10.7y, the bond sits at ~9.7y
        return pr(d, 4.5, securities=sec, extra=extra, aliases=aliases if curve else None)

    a0, a1, n0, n1 = at(dt.date(2025, 3, 12)), at(dt.date(2025, 3, 19)), at(dt.date(2025, 3, 12), False), at(dt.date(2025, 3, 19), False)
    b = make(a0)
    La, Ln = layers(b, cx(a1, a0)), layers(b, cx(n1, n0))
    assert Ln["roll"] == 0.0 and La["roll"] > 0, "a long bond on an upward curve rolls down"
    assert La["roll"] + La["delta"] == pytest.approx(Ln["delta"], rel=1e-12)
    assert La["carry"] == pytest.approx(Ln["carry"]) and La["convexity"] == pytest.approx(Ln["convexity"])


def test_the_roll_layer_follows_the_previous_curve_only_the_curve_points_and_365_25_day_years():
    """roll = -k * risk(y0, t1) * (yc0(ttm0 - dt) - yc0(ttm0)) with yc0 the on-the-run curve of the PREVIOUS snapshot (np.interp, ttm = days / 365.25, flat outside)."""
    import numpy as np

    sec = SEC + [("CT2XXXXXX", 3.0, dt.date(2024, 3, 15), dt.date(2027, 3, 15)), ("CT10XXXXX", 4.0, dt.date(2025, 11, 15), dt.date(2035, 11, 15)),
                 ("OLD10XXXX", 4.0, dt.date(2020, 2, 15), dt.date(2030, 2, 15))]
    aliases = {"CT2": "CT2XXXXXX", "CT10": "CT10XXXXX", "O10": "OLD10XXXX"}
    d0, d1 = dt.date(2025, 3, 12), dt.date(2025, 3, 19)

    def at(d, y2, y10, off_the_run=4.4):
        return pr(d, 4.5, securities=sec, aliases=aliases, extra={"CT2XXXXXX": {"ytm": y2}, "CT10XXXXX": {"ytm": y10}, "OLD10XXXX": {"ytm": off_the_run}})

    b = make(at(d0, 3.4, 5.0))
    p1 = at(d1, 3.0, 5.4)  # a different curve NOW: it must not be the one used for the pull-down
    roll_a = b.roll(ctx=cx(p1, at(d0, 3.4, 5.0)))
    roll_b = b.roll(ctx=cx(p1, at(d0, 3.0, 5.4)))
    assert roll_a > 0 and roll_b > roll_a
    t2, t10, tb = ((dt.date(2027, 3, 15) - d0).days / 365.25, (dt.date(2035, 11, 15) - d0).days / 365.25, (dt.date(2034, 11, 15) - d0).days / 365.25)
    step = (d1 - d0).days / 365.25

    def pull(y2, y10):
        return float(np.interp(tb - step, [t2, t10], [y2, y10]) - np.interp(tb, [t2, t10], [y2, y10]))

    risk = -b.dv01(ctx=cx(pr(d1, 4.5))) * 1e4 / b.notional  # -dP/dy per 100 face at (y0, t1)
    assert roll_a == pytest.approx(-(b.notional / 100.0) * risk * pull(3.4, 5.0), rel=1e-9)
    assert roll_b == pytest.approx(-(b.notional / 100.0) * risk * pull(3.0, 5.4), rel=1e-9)
    # an OFF-the-run alias (O10) is not part of the curve: a wild quote on it changes nothing
    assert b.roll(ctx=cx(p1, at(d0, 3.4, 5.0, off_the_run=9.0))) == pytest.approx(roll_a, rel=1e-12)


# ------------------------------------------------------------------ factory, resolution, errors
def test_an_alias_is_resolved_to_the_security_id_and_reported_as_a_resolved_term():
    p = pr(dt.date(2025, 3, 12), 4.5, aliases={"CT10": ID})
    b = tpl(security="CT10").build(p, p.ts)
    assert b.terms["security"] == ID and b.obj.security == ID and b.obj.coupon == 4.25 and b.terms["direction"] == 1
    with pytest.raises(MarketDataUnavailable, match="unknown security"):
        tpl(security="CT2").build(p, p.ts)
    assert tpl(side="short").build(p, p.ts).terms["direction"] == -1


def test_bad_extras_repo_keys_calendar_and_unwrapped_pricers_are_rejected_at_build_time():
    p = pr(dt.date(2025, 3, 12), 4.5)
    with pytest.raises(ConfigError, match=r"\['coupon'\]"):
        tpl(extras={"coupon": 1}).build(p, p.ts)
    with pytest.raises(ConfigError, match="unknown repo keys"):
        tpl(extras={"repo": {"rate": 4.0}}).build(p, p.ts)
    with pytest.raises(ConfigError, match="gc_rate"):
        tpl(extras={"repo": {"gc_rate": "libor"}}).build(p, p.ts)
    with pytest.raises(ConfigError, match="no calendar 'lon'"):
        S.template(bond, "bond", {**CONV, "calendar": "lon"}, {"side": "long", "security": ID, "notional": 1e6}).build(p, p.ts)
    with pytest.raises(ConfigError, match="does not support it"):
        S.template(bond, "bond", {**CONV, "yield_convention": "street"}, {"side": "long", "security": ID, "notional": 1e6}).build(p, p.ts)
    with pytest.raises(ConfigError, match="not a QLPricer"):
        tpl().build(S.pricer_of(S.snapshot(dt.date(2025, 3, 12), nodes=False, hol=HOL)), None)


def test_the_bond_is_plain_data_and_does_not_depend_on_the_global_evaluation_date():
    p = pr(dt.date(2025, 3, 12), 4.5)
    b = make(p, extras={"repo": {"gc_rate": 4.3}})
    v = b.value(ctx=cx(p)).pv
    for c in (copy.deepcopy(b), pickle.loads(pickle.dumps(b))):
        assert c.value(ctx=cx(p)).pv == v and c.repo == {"gc_rate": 4.3}
    s = ql.Settings.instance()
    old = s.evaluationDate
    s.evaluationDate = ql.Date(1, 1, 2030)
    try:
        assert make(p).value(ctx=cx(pr(dt.date(2025, 3, 12), 4.5))).pv == pytest.approx(v, abs=1e-9)
        assert s.evaluationDate == ql.Date(1, 1, 2030)  # and the bond did not touch it
    finally:
        s.evaluationDate = old


def test_the_resolved_terms_include_the_coupon_maturity_and_notional_like_the_other_stacks():
    """found by the L0 `resolved_terms` row of the bond tie-out through the CLI: this adapter reported only the security id, so 'reference: 2033-11-15 vs quantlib: None'."""
    p = pr(dt.date(2025, 3, 12), 4.5, aliases={"CT10": ID})
    b = tpl(security="CT10").build(p, p.ts)
    sec = p.security(ID)
    assert (b.terms["coupon"], b.terms["maturity"], b.terms["notional"]) == (sec.coupon, sec.maturity_date, b.obj.notional)
