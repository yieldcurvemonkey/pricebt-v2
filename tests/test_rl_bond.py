"""RLBond and the `bond` kit on plain-data snapshots: the independent golden numbers, the quote pipeline and its single settlement, coupon cash sweep, repo financing, the
yield-space layers, maturity, the factory and the conformance of the kit's default binding block. rateslib only."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("rateslib")
pytestmark = pytest.mark.adapter_rateslib

from test_rl_common import BOND_CONV, LAYERS, SCHEMAS, ctx, rl, rl_pricer, seeded_fixings, snapshot, template  # noqa: E402

import pricebt.contrib.rateslib as RL  # noqa: E402
from pricebt.contracts.binding import Env, call_binding  # noqa: E402
from pricebt.contracts.spec import build_spec  # noqa: E402
from pricebt.contrib.rateslib import RLBond  # noqa: E402
from pricebt.errors import ConfigError, MarketDataUnavailable  # noqa: E402
from pricebt.pricable import Valuation  # noqa: E402
from pricebt.snapshot import Quote, QuoteSet, Security, SnapshotPricer  # noqa: E402

D = dt.datetime
SEC = Security("TEST00001", 4.25, dt.date(2024, 11, 15), dt.date(2034, 11, 15))
SHORT = Security("TEST00002", 4.0, dt.date(2023, 5, 15), dt.date(2025, 5, 15))


def bpricer(d, y=None, clean=None, sec=SEC, hour=17, others=(), convention="market", fixings=None):
    """A snapshot with a quote panel and NO curve (a bond needs none) at date `d`: `sec` quoted at yield `y` or clean price `clean`, plus the `others` [(security, ytm)]
    with on-the-run aliases CT<years> so the roll layer has a yield curve to age down (with one quoted bond it has none)."""
    ref = dt.date.fromisoformat(d)
    secs = {sec.id: sec, **{s.id: s for s, _ in others}}
    quotes = {sec.id: Quote(ytm=y, clean=clean), **{s.id: Quote(ytm=v) for s, v in others}}
    aliases = {f"CT{round((s.maturity_date - ref).days / 365.25)}": s.id for s in [sec, *(s for s, _ in others)]} if others else {}
    return RL.wrap(SnapshotPricer(snapshot(ref, curve=False, quotes=QuoteSet(ref, quotes, secs, aliases, convention), hour=hour, fixings=fixings)))


def terms(**kw):
    return {"side": "buy", "security": SEC.id, "notional": 1e7, **kw}


def bbuild(p, **kw):
    return template(RL.bond, BOND_CONV, terms(**kw)).build(p, p.ts)


def bond(p, **kw):
    return bbuild(p, **kw).obj


def layers_of(b, c):
    return {k: getattr(b, k)(ctx=c) for k in LAYERS}


REPO = {"repo": {"gc_rate": 4.30}}


# ------------------------------------------------------------------ golden numbers (independent script, spec us_gb_tsy)
@pytest.fixture(scope="module")
def golden():
    """10mm long UST 4.25 Nov-34, 2025-05-12 (y 4.55) -> 2025-05-20 (y 4.62), coupon paid 05-15, repo 4.30."""
    p0, p1 = bpricer("2025-05-12", 4.55), bpricer("2025-05-20", 4.62)
    b = bond(p0, notional=10e6, extras=REPO)
    c = ctx(p1, p0)
    return b, p0, p1, c, b.value(ctx=ctx(p0)), b.value(ctx=c)


def test_marks_and_flows_match_the_independent_calculation(golden):
    b, p0, p1, c, v0, v1 = golden
    assert isinstance(v1, Valuation)
    assert v0.pv == pytest.approx(9979417.302, abs=0.01) and v1.pv == pytest.approx(9724039.126, abs=0.01)
    assert v1.cash == 212500.0 and v0.cash == 0.0 and v0.financing == 0.0
    assert v1.financing == pytest.approx(-9535.8876, abs=0.001)
    assert v1.pv - v0.pv + v1.cash + v1.financing == pytest.approx(-52414.064, abs=0.01)


def test_yield_space_layers_match_the_independent_calculation(golden):
    b, _, _, c, v0, v1 = golden
    L = layers_of(b, c)
    assert L["carry"] == pytest.approx(136.580, abs=0.01), "carry = coupon + pull-to-par - financing (engine has no separate financing layer)"
    assert L["delta"] == pytest.approx(-52718.946, abs=0.01) and L["convexity"] == pytest.approx(168.692, abs=0.01) and L["roll"] == 0.0
    total = v1.pv - v0.pv + v1.cash + v1.financing
    assert total - sum(L.values()) == pytest.approx(-0.3896, abs=0.01), "third-order residual left to the engine's `unexplained`"


def test_the_pinned_calc_mode_is_us_gb_tsy_not_us_gb(golden):
    """The research golden (-52,389.01) used spec us_gb; us_gb_tsy differs by 25 on this position. The pin is deliberate."""
    b, *_ = golden
    loose = rl.FixedRateBond(effective=D(2024, 11, 15), termination=D(2034, 11, 15), spec="us_gb", fixed_rate=4.25, notional=-10e6, ex_div=0)
    a = b._inst.price(4.55, D(2025, 5, 12), dirty=True)
    assert float(loose.price(4.55, D(2025, 5, 12), dirty=True)) != pytest.approx(float(a), abs=1e-6)
    assert b._inst.kwargs.meta["calc_mode"].kwargs["v1"] != loose.kwargs.meta["calc_mode"].kwargs["v1"]


def test_full_revaluation_at_the_new_yield_equals_delta_plus_convexity(golden):
    b, _, _, c, _, _ = golden
    L = layers_of(b, c)
    settle = D(2025, 5, 20)
    k = b.notional / 100.0
    fd = k * (float(b._inst.price(4.62, settle, dirty=True)) - float(b._inst.price(4.55, settle, dirty=True)))
    assert abs(fd - (L["delta"] + L["convexity"])) < 1.0
    assert abs(fd - L["delta"]) > 100.0, "delta alone is not enough: the check must exercise convexity"


def test_long_short_mirror_on_value_flows_layers_and_measures(golden):
    b, p0, p1, c, v0, v1 = golden
    s = bond(p0, side="sell", notional=10e6, extras=REPO)
    cs = ctx(p1, p0)
    w0, w1 = s.value(ctx=ctx(p0)), s.value(ctx=cs)
    assert w1.pv == pytest.approx(-v1.pv, abs=1e-6) and w1.cash == pytest.approx(-v1.cash) and w1.financing == pytest.approx(-v1.financing)
    Ls, Lb = layers_of(s, cs), layers_of(b, c)
    for k in LAYERS:
        assert Ls[k] == pytest.approx(-Lb[k], abs=1e-6), k
    assert s.dv01(ctx=cs) == pytest.approx(-b.dv01(ctx=c)) and s.gamma(ctx=cs) == pytest.approx(-b.gamma(ctx=c))
    assert b.dv01(ctx=c) < 0 < s.dv01(ctx=cs), "a long bond LOSES when yields rise"
    assert b.gamma(ctx=c) > 0


def test_dv01_and_gamma_match_finite_differences_of_the_price(golden):
    b, _, p1, c, _, _ = golden
    settle = D(2025, 5, 20)
    k = b.notional / 100.0
    px = lambda y: k * float(b._inst.price(y, settle, dirty=True))  # noqa: E731
    h = 0.005  # 0.5bp in percent
    assert b.dv01(ctx=c) == pytest.approx((px(4.62 + h) - px(4.62 - h)) / 1.0, rel=1e-5)
    assert b.gamma(ctx=c) == pytest.approx((px(4.62 + 0.01) - 2 * px(4.62) + px(4.62 - 0.01)) / 1.0, rel=2e-3)
    assert b.duration(ctx=c) == pytest.approx(-b.dv01(ctx=c) * 1e4 / (px(4.62)), rel=0.02)  # modified duration ~ -dv01_per_bp*1e4/MV


# ------------------------------------------------------------------ quote pipeline and settlement
def test_ytm_and_clean_quotes_give_the_same_mark():
    y = 4.5
    b = bond(bpricer("2025-03-12", y), notional=1e6)
    dirty = float(b._inst.price(y, D(2025, 3, 12), dirty=True))
    clean = float(b._inst.price(y, D(2025, 3, 12), dirty=False))
    m_y = b.mark(bpricer("2025-03-12", y=y))
    m_c = b.mark(bpricer("2025-03-12", clean=clean))
    assert m_c["ytm"] == pytest.approx(y, abs=1e-8) and m_y["ytm"] == y
    assert m_c["dirty"] == pytest.approx(dirty, abs=1e-8) == pytest.approx(m_y["dirty"], abs=1e-8)
    assert m_c["dirty"] - m_c["clean"] == pytest.approx(m_c["accrued"], abs=1e-12) and 0 < m_c["accrued"] < 2.2


def test_one_settlement_for_quote_to_ytm_and_ytm_to_price():
    """Solving ytm at one settlement and pricing at another creates a one-day accrued jump each step; the adapter uses ONE."""
    b = bond(bpricer("2025-03-12", 4.5), notional=1e6)
    clean = float(b._inst.price(4.5, D(2025, 3, 12), dirty=False))
    y_other = float(b._inst.ytm(clean, D(2025, 3, 13), dirty=False))
    mismatch = float(b._inst.price(y_other, D(2025, 3, 12), dirty=True)) - float(b._inst.price(4.5, D(2025, 3, 12), dirty=True))
    assert abs(mismatch) > 1e-4, "mixing settlements moves the dirty mark (per 100 face); the adapter must not"
    assert b.mark(bpricer("2025-03-12", clean=clean))["ytm"] == pytest.approx(4.5, abs=1e-8)


def test_coupon_paid_on_D_is_inside_V_D_and_swept_at_D_plus_1():
    """Bond quoted at a constant ytm across the 2025-05-15 coupon: no P&L jump, cash booked exactly when the flow leaves the mark."""
    days = ["2025-05-13", "2025-05-14", "2025-05-15", "2025-05-16", "2025-05-19"]
    ps = {d: bpricer(d, 4.5) for d in days}
    b = bond(ps[days[0]], notional=10e6)
    vals = {c: b.value(ctx=ctx(ps[c], ps[a])) for a, c in zip(days[:-1], days[1:])}
    v13 = b.value(ctx=ctx(ps["2025-05-13"]))
    assert [vals[d].cash for d in days[1:]] == [0.0, 0.0, 212500.0, 0.0]
    steps = {c: vals[c].pv - (v13.pv if c == "2025-05-14" else vals[days[days.index(c) - 1]].pv) + vals[c].cash for c in days[1:]}
    assert all(1000.0 < s < 4000.0 for s in steps.values()), steps  # smooth accrual (~1.2k a day, 3.5k over the weekend), never ~212k
    assert vals["2025-05-15"].pv - vals["2025-05-16"].pv == pytest.approx(212500.0 - steps["2025-05-16"], abs=1e-6)
    no_cash = vals["2025-05-16"].pv - vals["2025-05-15"].pv
    assert abs(no_cash) > 200000.0, "without the cash sweep the equity curve would drop by the coupon"


def test_carry_is_the_whole_pnl_at_a_constant_yield_including_a_coupon_paid_on_the_new_reference_date():
    """Nothing but time passes (the yield is unchanged): every layer other than `carry` is zero and `carry` is the step P&L, also when the coupon is paid ON the new
    reference date (inside V(D)) and on the day after (swept as cash)."""
    days = ["2025-05-14", "2025-05-15", "2025-05-16"]
    ps = {d: bpricer(d, 4.5) for d in days}
    b = bond(ps[days[0]], notional=10e6, extras=REPO)
    for a, c in zip(days[:-1], days[1:]):
        cx = ctx(ps[c], ps[a])
        v0, v1 = b.value(ctx=ctx(ps[a])), b.value(ctx=cx)
        total = v1.pv - v0.pv + v1.cash + v1.financing
        L = layers_of(b, cx)
        assert L["carry"] == pytest.approx(total, rel=1e-12, abs=1e-6), (a, c)
        assert L["roll"] == 0.0 and L["delta"] == 0.0 and L["convexity"] == 0.0
    assert b.value(ctx=ctx(ps["2025-05-15"], ps["2025-05-14"])).cash == 0.0 and b.value(ctx=ctx(ps["2025-05-16"], ps["2025-05-15"])).cash == 212500.0


def test_financing_sign_scaling_haircut_specialness_and_the_published_fixing():
    p0, p1 = bpricer("2025-03-12", 4.5), bpricer("2025-03-13", 4.5)
    dirty0 = bond(p0, notional=1e6).mark(p0)["dirty"]
    day = 1e6 / 100 * dirty0 * 4.30 / 100 / 360
    fin = lambda side="buy", **repo: bond(p0, side=side, notional=1e6, extras={"repo": {"gc_rate": 4.30, **repo}}).value(ctx=ctx(p1, p0)).financing  # noqa: E731
    assert fin() == pytest.approx(-day, rel=1e-12) and fin("sell") == pytest.approx(+day, rel=1e-12)
    hour = bond(p0, notional=1e6, extras=REPO).value(ctx=ctx(bpricer("2025-03-12", 4.5, hour=18), bpricer("2025-03-12", 4.5, hour=17)))
    assert hour.financing == pytest.approx(-day / 24, rel=1e-12), "financing runs on elapsed seconds (ACT/360)"
    assert fin(haircut=0.1, specialness_bps=50) == pytest.approx(-day * 0.9 * (4.30 - 0.50) / 4.30, rel=1e-12)
    assert bond(p0, notional=1e6).value(ctx=ctx(p1, p0)).financing == 0.0, "no repo terms: no financing"
    # gc_rate 'pricer': the last PUBLISHED fixing strictly before the interval start date (03-12), from the snapshot the interval starts on
    ser = pd.Series([4.0, 4.2], index=pd.DatetimeIndex(["2025-03-10", "2025-03-11"]))
    q0 = bpricer("2025-03-12", 4.5, fixings=ser)
    f = bond(q0, notional=1e6, extras={"repo": {"gc_rate": "pricer"}}).value(ctx=ctx(p1, q0)).financing
    assert f == pytest.approx(-day * 4.2 / 4.30, rel=1e-12), "last fixing strictly before 03-12 -> 4.2 (not 4.0)"
    empty = bpricer("2025-03-12", 4.5, fixings=ser.iloc[:0])
    with pytest.raises(MarketDataUnavailable, match="no published fixings"):
        bond(empty, notional=1e6, extras={"repo": {"gc_rate": "pricer"}}).value(ctx=ctx(p1, empty))
    stale = bpricer("2025-03-12", 4.5, fixings=ser.iloc[:1].set_axis(pd.DatetimeIndex(["2025-02-20"])))
    with pytest.raises(MarketDataUnavailable, match="stale"):
        bond(stale, notional=1e6, extras={"repo": {"gc_rate": "pricer"}}).value(ctx=ctx(p1, stale))


def test_roll_layer_from_a_ytm_curve_only_moves_value_between_roll_and_delta():
    """With an on-the-run yield curve (two quoted CT bonds) an upward slope makes a long bond roll DOWN the curve: value moves from `delta` into `roll`."""
    kw = dict(others=[(SHORT, 3.5)])
    a0, a1 = bpricer("2025-03-12", 4.5, **kw), bpricer("2025-03-19", 4.5, **kw)
    n0, n1 = bpricer("2025-03-12", 4.5), bpricer("2025-03-19", 4.5)
    assert a0.ytm_curve() is not None and n0.ytm_curve() is None
    b = bond(n0, notional=10e6)
    La, Ln = layers_of(b, ctx(a1, a0)), layers_of(b, ctx(n1, n0))
    assert Ln["roll"] == 0.0 and La["roll"] > 0, "long bond on an upward curve rolls down"
    assert La["roll"] + La["delta"] == pytest.approx(Ln["delta"], rel=1e-12)
    # by hand: the curve is the two quoted CT points (years to maturity / 365.25, yield); the bond ages by the elapsed calendar days
    t_short, t_long = (SHORT.maturity_date - dt.date(2025, 3, 12)).days / 365.25, (SEC.maturity_date - dt.date(2025, 3, 12)).days / 365.25
    pull = float(np.interp(t_long - 7 / 365.25, [t_short, t_long], [3.5, 4.5]) - np.interp(t_long, [t_short, t_long], [3.5, 4.5]))
    risk = float(b._inst.duration(4.5, D(2025, 3, 19), "risk"))
    assert La["roll"] == pytest.approx(-(10e6 / 100.0) * risk * pull, rel=1e-12) and pull < 0
    assert La["carry"] == pytest.approx(Ln["carry"]) and La["convexity"] == pytest.approx(Ln["convexity"])


def test_maturity_pays_principal_and_coupon_then_is_worth_zero():
    days = ["2025-05-14", "2025-05-15", "2025-05-16", "2025-05-19"]
    ps = {d: bpricer(d, y=4.0, sec=SHORT) if d <= "2025-05-15" else rl_pricer(d) for d in days}
    b = bond(ps[days[0]], security=SHORT.id, notional=10e6)
    v = {c: b.value(ctx=ctx(ps[c], ps[a])) for a, c in zip(days[:-1], days[1:])}
    assert b.value(ctx=ctx(ps["2025-05-15"])).pv > 10e6 and v["2025-05-15"].cash == 0.0
    assert v["2025-05-16"].pv == 0.0 and v["2025-05-16"].cash == pytest.approx(10e6 * (1 + 0.04 / 2))
    assert v["2025-05-19"].cash == 0.0 and v["2025-05-19"].pv == 0.0 and v["2025-05-19"].financing == 0.0
    with pytest.raises(ConfigError, match="last payment"):
        b.dv01(ctx=ctx(ps["2025-05-19"], ps["2025-05-16"]))
    assert layers_of(b, ctx(ps["2025-05-16"], ps["2025-05-15"]))["delta"] == 0.0


# ------------------------------------------------------------------ construction: the factory resolves the security and validates the terms
def test_construction_validation_and_the_resolved_terms():
    p = bpricer("2025-03-12", 4.5)
    ref = SEC
    with pytest.raises(ConfigError, match="sign"):
        RLBond(ref, 2, 1e6)
    with pytest.raises(ConfigError, match="unsigned"):
        RLBond(ref, 1, -1e6)
    with pytest.raises(ConfigError, match="repo keys") as e:
        bbuild(p, extras={"repo": {"rate": 4}})
    assert e.value.code == "CFG-EXTRAS"
    with pytest.raises(ConfigError, match="repo gc_rate"):
        bbuild(p, extras={"repo": {"gc_rate": "overnight"}})
    with pytest.raises(ConfigError, match="understands") as e:
        bbuild(p, extras={"spread": 1})
    assert e.value.code == "CFG-EXTRAS"
    with pytest.raises(ConfigError, match="security"):
        template(RL.bond, BOND_CONV, {"side": "buy", "notional": 1e6}).build(p, p.ts)  # the security is a required term


def test_the_financing_day_count_is_a_convention_and_only_act360_is_supported():
    """The old `repo: {day_count: ACT/365}` is refused as an unknown repo key; the day count of the financing accrual is now the shared conventions block's."""
    p = bpricer("2025-03-12", 4.5)
    with pytest.raises(ConfigError, match="financing_day_count") as e:
        template(RL.bond, {**BOND_CONV, "financing_day_count": "act365f"}, terms()).build(p, p.ts)
    assert e.value.code == "CFG-CONVENTION-UNSUPPORTED"
    with pytest.raises(ConfigError, match="repo keys"):
        bbuild(p, extras={"repo": {"day_count": "ACT/365"}})


def test_the_factory_resolves_an_alias_on_the_snapshot_and_reports_it():
    p = bpricer("2025-03-12", 4.5, others=[(SHORT, 3.5)])
    b = bbuild(p, security="CT10", side="sell", notional=2e6)
    assert b.obj.cusip == "TEST00001" and b.obj.sign == -1 and b.obj.notional == 2e6
    assert b.terms["security"] == "TEST00001" and b.terms["coupon"] == 4.25 and b.terms["maturity"] == dt.date(2034, 11, 15) and b.terms["direction"] == -1
    assert bbuild(p, side="long").obj.sign == 1, "`long` is an alias of `buy`"
    with pytest.raises(MarketDataUnavailable, match="no CT30"):
        bbuild(p, security="CT30")
    with pytest.raises(ConfigError, match="not a rateslib pricer") as e:
        template(RL.bond, BOND_CONV, terms()).build(SnapshotPricer(p.snapshot), p.ts)
    assert e.value.code == "CFG-WRAP"


# ------------------------------------------------------------------ the kit: conformance and the default block
def test_the_kit_conforms_to_the_bond_schema_with_only_its_default_block():
    spec = build_spec("b", {"factory": RL.bond, "conventions": BOND_CONV, "layers": list(LAYERS)}, schemas=SCHEMAS)
    for name in ("value", "dv01", "gamma", "rate", "ytm", "duration", "accrued", *LAYERS):
        assert name in spec.bindings
    assert spec.asset_class == "bond" and spec.layers == LAYERS


def test_every_default_binding_runs_and_returns_its_contract_type(golden):
    b, p0, p1, c, v0, v1 = golden
    t = template(RL.bond, BOND_CONV, terms(notional=10e6, extras=REPO))
    obj = t.build(p0, p0.ts).obj
    env = Env(pricer=p1, ctx=ctx(p1, p0), instrument=obj, terms={})
    out = {name: call_binding(bd, env) for name, bd in t.spec.bindings.items()}
    assert isinstance(out["value"], Valuation) and out["value"].pv == pytest.approx(v1.pv)
    for k in ("dv01", "gamma", "rate", "ytm", "duration", "accrued", *LAYERS):
        assert isinstance(out[k], float) and np.isfinite(out[k]), k
    assert out["rate"] == out["ytm"] == 4.62, "the bond's own market rate IS its yield (percent)"


def test_the_measures_of_the_quote_pipeline_are_consistent_and_the_flows_are_the_bonds(golden):
    b, _, _, c, _, _ = golden
    assert b.clean_price(ctx=c) + b.accrued(ctx=c) == pytest.approx(b.dirty_price(ctx=c))
    assert b.ytm(ctx=c) == 4.62
    cf = b.cashflows(ctx=c)
    assert list(cf.columns) == ["Type", "Payment", "Cashflow"] and cf.Cashflow.iloc[0] == 212500.0 and cf.Cashflow.iloc[-1] == 10e6 and cf.Cashflow.iloc[-2] == 212500.0
    assert cf.Cashflow.sum() == pytest.approx(10e6 + 20 * 212500.0)
    assert bond(bpricer("2025-05-20", 4.62), notional=1e6).notional == 1e6


def test_the_bond_is_priced_off_the_quote_alone_a_curve_is_not_needed():
    p = bpricer("2025-03-12", 4.5)
    assert p.curve is None
    b = bond(p, notional=1e6)
    assert b.value(ctx=ctx(p)).pv > 0
