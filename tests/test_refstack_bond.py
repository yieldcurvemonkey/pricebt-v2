"""The reference stack's bond (spec A4), every value re-derived by hand with plain python (spec X5c). Helpers come from `test_refstack`."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from pricebt.contracts.schema import SchemaRegistry
from pricebt.errors import ConfigError, MarketDataUnavailable
from pricebt.snapshot import CalendarData, FixingsSeries, MarketSnapshot, Quote, QuoteSet, Security, SnapshotPricer
from pricebt.testing import refstack as R
from test_refstack import NY, ctx, pricer

pytestmark = pytest.mark.core

BOND_CONV = dict(R.UST_CONVENTIONS, calendar="wk")


def bond_snapshot(ref, securities, quotes, aliases=None, fixings=None, holidays=()):
    """A quote panel (yields or clean prices) and a weekend calendar; a bond needs no curve."""
    qs = QuoteSet(ref, quotes, {s.id: s for s in securities}, aliases or {}, "market")
    fx = {} if fixings is None else {"f": FixingsSeries("f", tuple(sorted(fixings)), tuple(fixings[d] for d in sorted(fixings)), "percent")}
    return MarketSnapshot(pd.Timestamp(f"{ref} 17:00", tz=NY), ref, {}, fx, qs, {"wk": CalendarData("wk", tuple(holidays))})


def bpricer(ref, securities, quotes, **kw):
    return R.wrap(SnapshotPricer(bond_snapshot(ref, securities, quotes, **kw)))


def bbuild(p, token, sign=1, notional=1e6, **extras):
    terms = {"security": token, "direction": sign, "notional": notional, **({"extras": extras} if extras else {})}
    return R.bond_factory(p, p.ts, terms=terms, conventions=BOND_CONV).obj


def zero_bond(ref=dt.date(2024, 7, 15), y=4.0):
    sec = Security("ZERO", 0.0, dt.date(2024, 1, 15), dt.date(2025, 1, 15))
    return sec, bpricer(ref, [sec], {"ZERO": Quote(ytm=y)})


def test_a_zero_coupon_bond_prices_by_hand_in_every_position_of_the_settlement():
    sec, p = zero_bond()  # exactly one period left: 100 / (1 + y/2)
    b = bbuild(p, "ZERO")
    assert b.mark(p)["dirty"] == pytest.approx(100.0 / 1.02, rel=1e-14) and b.mark(p)["accrued"] == 0.0
    p2 = bpricer(dt.date(2024, 10, 15), [sec], {"ZERO": Quote(ytm=4.0)})  # half a period left: the first fractional period is SIMPLE interest, w = 92/184
    assert bbuild(p2, "ZERO").mark(p2)["dirty"] == pytest.approx(100.0 / (1.0 + 0.02 * 0.5), rel=1e-14)
    p3 = bpricer(dt.date(2024, 4, 15), [sec], {"ZERO": Quote(ytm=4.0)})  # two periods: w = 91/182 for the first, then one full compounded period
    assert bbuild(p3, "ZERO").mark(p3)["dirty"] == pytest.approx(100.0 / ((1.0 + 0.02 * 0.5) * 1.02), rel=1e-14)


def test_a_coupon_bond_on_a_coupon_date_and_the_clean_price_round_trip():
    sec = Security("C5", 5.0, dt.date(2020, 5, 15), dt.date(2030, 5, 15))
    z = 0.06 / 2  # a 6% yield: semiannual periods of y/2
    ref = dt.date(2024, 5, 15)
    p = bpricer(ref, [sec], {"C5": Quote(ytm=6.0)})
    hand = sum(2.5 / (1 + z) ** j for j in range(1, 13)) + 100.0 / (1 + z) ** 12
    m = bbuild(p, "C5").mark(p)
    assert m["dirty"] == pytest.approx(hand, rel=1e-13) and m["accrued"] == 0.0 and m["clean"] == pytest.approx(hand, rel=1e-13), "the coupon paid on the settlement date is not in the price"
    pc = bpricer(ref, [sec], {"C5": Quote(clean=hand)})
    assert bbuild(pc, "C5").mark(pc)["ytm"] == pytest.approx(6.0, abs=1e-10), "a clean quote on a coupon date is solved as a dirty price (accrued is 0)"
    off = bpricer(dt.date(2024, 8, 15), [sec], {"C5": Quote(ytm=6.0)})
    assert bbuild(off, "C5").mark(off)["accrued"] == pytest.approx(2.5 * 92 / 184, rel=1e-14)


def test_a_short_first_period_accrues_over_its_notional_regular_period():
    stub = Security("STUB", 5.0, dt.date(2024, 2, 1), dt.date(2027, 5, 15))
    b = bbuild(bpricer(dt.date(2024, 3, 1), [stub], {"STUB": Quote(ytm=5.0)}), "STUB")
    assert b.starts[0] == dt.date(2024, 2, 1) and b.ends[0] == dt.date(2024, 5, 15) and b.ref_starts[0] == dt.date(2023, 11, 15)
    assert b.amounts[0] == pytest.approx(2.5 * 104 / 182, rel=1e-14), "the short first coupon is a fraction of the regular one"
    assert b.accrued(dt.date(2024, 3, 1)) == pytest.approx(2.5 * 29 / 182, rel=1e-14)
    with pytest.raises(ConfigError, match="before the issue date"):
        b.accrued(dt.date(2024, 1, 15))


def test_month_end_maturities_keep_month_end_coupons_and_payments_move_to_the_next_business_day():
    sec = Security("ME", 4.0, dt.date(2024, 2, 29), dt.date(2027, 2, 28))
    b = bbuild(bpricer(dt.date(2024, 3, 4), [sec], {"ME": Quote(ytm=4.0)}), "ME")
    assert b.ends == (dt.date(2024, 8, 31), dt.date(2025, 2, 28), dt.date(2025, 8, 31), dt.date(2026, 2, 28), dt.date(2026, 8, 31), dt.date(2027, 2, 28))
    assert b.pays[0] == dt.date(2024, 9, 2), "2024-08-31 is a Saturday: paid on Monday the 2nd"
    assert b.pays[2] == dt.date(2025, 9, 1) and b.pays[4] == dt.date(2026, 8, 31), "Sunday 2025-08-31 is paid on Monday the 1st; Monday 2026-08-31 is paid the same day"


def test_risk_and_convexity_match_the_analytic_derivatives_of_the_pricing_formula():
    sec = Security("C5", 5.0, dt.date(2020, 5, 15), dt.date(2030, 5, 15))
    p = bpricer(dt.date(2024, 5, 15), [sec], {"C5": Quote(ytm=6.0)})
    b = bbuild(p, "C5", notional=5e6)
    z = 0.03
    cf = {j: 2.5 + (100.0 if j == 12 else 0.0) for j in range(1, 13)}
    d1 = sum(-c * j / 200.0 * (1 + z) ** (-j - 1) for j, c in cf.items())  # dP/dy per percentage point
    d2 = sum(c * j * (j + 1) / 200.0 ** 2 * (1 + z) ** (-j - 2) for j, c in cf.items())
    c = ctx(p)
    k = 5e6 / 100.0
    assert b.dv01(ctx=c) == pytest.approx(k * d1 / 100.0, rel=1e-8), "per +1bp, negative for a long"
    assert b.gamma(ctx=c) == pytest.approx(k * d2 * 1e-4, rel=1e-6)
    price = sum(v * (1 + z) ** (-j) for j, v in cf.items())
    assert b.duration(ctx=c) == pytest.approx(-d1 * 100.0 / price, rel=1e-8), "modified duration in years"
    assert bbuild(p, "C5", sign=-1, notional=5e6).dv01(ctx=c) == pytest.approx(-b.dv01(ctx=c), rel=1e-14)


def test_the_coupon_paid_today_is_inside_the_value_and_swept_as_cash_the_next_day():
    sec = Security("C5", 5.0, dt.date(2020, 5, 15), dt.date(2030, 5, 15))
    q = {"C5": Quote(ytm=6.0)}
    p0, p1, p2 = bpricer(dt.date(2024, 11, 14), [sec], q), bpricer(dt.date(2024, 11, 15), [sec], q), bpricer(dt.date(2024, 11, 18), [sec], q)  # Thu, Fri (pay day), Mon
    b = bbuild(p1, "C5")
    v1 = b.value(ctx=ctx(p1, prev=p0))
    assert v1.pv == pytest.approx(1e6 / 100 * (b.mark(p1)["dirty"] + 2.5), rel=1e-14) and v1.cash == 0.0, "the price excludes today's coupon; the value adds it back"
    assert b.value(ctx=ctx(p2, prev=p1)).cash == pytest.approx(1e6 / 100 * 2.5, rel=1e-14), "swept as cash once, at face, the next day"
    assert b.value(ctx=ctx(p2, prev=p0)).cash == pytest.approx(1e6 / 100 * 2.5, rel=1e-14), "a longer interval sweeps it once too"


def test_the_last_flow_matures_the_bond():
    sec = Security("C5", 5.0, dt.date(2020, 5, 15), dt.date(2030, 5, 15))
    p_before = bpricer(dt.date(2030, 5, 14), [sec], {"C5": Quote(ytm=6.0)})
    p_on = bpricer(dt.date(2030, 5, 15), [sec], {"C5": Quote(clean=100.0)})
    p_after = bpricer(dt.date(2030, 5, 16), [sec], {"C5": Quote(clean=100.0)})
    b = bbuild(p_before, "C5")
    v_on = b.value(ctx=ctx(p_on, prev=p_before))
    assert v_on.pv == pytest.approx(1e6 / 100 * 102.5, rel=1e-14), "on the last payment date the flows are the whole value and no quote is needed"
    v_after = b.value(ctx=ctx(p_after, prev=p_on))
    assert v_after.pv == 0.0 and v_after.cash == pytest.approx(1e6 / 100 * 102.5, rel=1e-14)
    with pytest.raises(ConfigError, match="last payment"):
        b.ytm(ctx=ctx(p_on))
    assert all(v == 0.0 for v in (b.carry(ctx=ctx(bpricer(dt.date(2030, 5, 17), [sec], {"C5": Quote(clean=100.0)}), prev=p_after)),)), "a matured bond has no layers"


def test_repo_financing_by_hand_and_the_pricer_fixing_form():
    sec = Security("C5", 5.0, dt.date(2020, 5, 15), dt.date(2030, 5, 15))
    q = {"C5": Quote(ytm=6.0)}
    p0 = bpricer(dt.date(2024, 6, 3), [sec], q, fixings={dt.date(2024, 5, 30): 5.29, dt.date(2024, 5, 31): 5.30})
    p1 = bpricer(dt.date(2024, 6, 4), [sec], q, fixings={dt.date(2024, 6, 3): 5.31})
    b = bbuild(p0, "C5", repo={"gc_rate": 5.25, "specialness_bps": 10.0, "haircut": 0.02})
    fin = b.value(ctx=ctx(p1, prev=p0)).financing
    seconds = (p1.ts - p0.ts).total_seconds()
    dirty0 = b.mark(p0)["dirty"]
    assert fin == pytest.approx(-1e6 / 100 * dirty0 * 0.98 * (5.25 - 0.10) / 100 * seconds / (360 * 86400), rel=1e-13)
    short = bbuild(p0, "C5", sign=-1, repo={"gc_rate": 5.25, "specialness_bps": 10.0, "haircut": 0.02})
    assert short.value(ctx=ctx(p1, prev=p0)).financing == pytest.approx(-fin, rel=1e-14), "a short earns what a long pays"
    px = bbuild(p0, "C5", repo={"gc_rate": "pricer"})
    assert px.repo_rate(p0) == 5.30, "the newest published fixing before the reference date"
    assert px.financing(p0, p1.ts, p0.ts) == pytest.approx(-1e6 / 100 * dirty0 * 5.30 / 100 * seconds / (360 * 86400), rel=1e-13)
    assert bbuild(p0, "C5").value(ctx=ctx(p1, prev=p0)).financing == 0.0, "no repo, no financing"


def test_repo_rate_errors_are_explicit():
    sec = Security("C5", 5.0, dt.date(2020, 5, 15), dt.date(2030, 5, 15))
    q = {"C5": Quote(ytm=6.0)}
    stale = bpricer(dt.date(2024, 6, 3), [sec], q, fixings={dt.date(2024, 5, 1): 5.3})
    with pytest.raises(MarketDataUnavailable, match="stale"):
        bbuild(stale, "C5", repo={"gc_rate": "pricer"}).repo_rate(stale)
    none = bpricer(dt.date(2024, 6, 3), [sec], q)
    with pytest.raises(MarketDataUnavailable, match="no published fixing"):
        bbuild(none, "C5", repo={"gc_rate": "pricer"}).repo_rate(none)
    with pytest.raises(ConfigError, match="gc_rate"):
        bbuild(none, "C5", repo={"gc_rate": "always"})
    with pytest.raises(ConfigError, match="unknown repo keys"):
        bbuild(none, "C5", repo={"rate": 5.0})
    with pytest.raises(ConfigError, match="bond extras"):
        bbuild(none, "C5", haircut=1)
    with pytest.raises(ConfigError, match="sign"):
        R.RefBond(security=sec, sign=0, notional=1.0, repo=None, cal=none.calendar("wk"))
    with pytest.raises(ConfigError, match="notional"):
        R.RefBond(security=sec, sign=1, notional=0.0, repo=None, cal=none.calendar("wk"))


def panel(ref):
    secs = [Security("T2", 4.0, dt.date(2022, 5, 15), dt.date(2026, 5, 15)), Security("T5", 4.25, dt.date(2022, 5, 15), dt.date(2029, 5, 15)),
            Security("T10", 4.5, dt.date(2022, 5, 15), dt.date(2034, 5, 15)), Security("O10", 3.5, dt.date(2020, 5, 15), dt.date(2030, 5, 15))]
    quotes = {"T2": Quote(ytm=4.5), "T5": Quote(ytm=4.4), "T10": Quote(ytm=4.3), "O10": Quote(ytm=4.35)}
    return bpricer(ref, secs, quotes, aliases={"CT2": "T2", "CT5": "T5", "CT10": "T10", "O10": "O10"})


def test_aliases_resolve_through_the_snapshot_and_unknown_tokens_say_what_is_wrong():
    p = panel(dt.date(2024, 6, 3))
    assert R.resolve_security(p, "CT10").id == "T10" and R.resolve_security(p, "ct10").id == "T10" and R.resolve_security(p, "O10").id == "O10"
    assert R.resolve_security(p, "T5").id == "T5"
    resolved = R.bond_factory(p, p.ts, terms={"security": "CT5", "direction": 1, "notional": 1e6}, conventions=BOND_CONV)
    assert resolved.terms["security"] == "T5" and resolved.terms["coupon"] == 4.25 and resolved.terms["maturity"] == dt.date(2029, 5, 15), "the alias is resolved and reported (T4)"
    with pytest.raises(MarketDataUnavailable, match="no OO10"):
        R.resolve_security(p, "OO10")
    with pytest.raises(MarketDataUnavailable, match="not in the reference table"):
        R.resolve_security(p, "912810AB1")
    with pytest.raises(ConfigError, match="unknown bond token"):
        R.resolve_security(p, "the ten year")
    with pytest.raises(MarketDataUnavailable, match="no bond quotes"):
        R.resolve_security(pricer(), "CT10")
    two = [Security("T2", 4.0, dt.date(2022, 5, 15), dt.date(2026, 5, 15)), Security("X", 4.0, dt.date(2022, 5, 15), dt.date(2026, 5, 15))]
    quoteless = bpricer(dt.date(2024, 6, 3), two, {"T2": Quote(ytm=4.0)})
    with pytest.raises(MarketDataUnavailable, match="no quote for X"):
        bbuild(quoteless, "X").mark(quoteless)
    assert bbuild(p, "T2").mark(p)["ytm"] == 4.5


def test_layers_add_up_to_the_interval_pnl_and_the_roll_follows_the_quoted_yield_curve():
    p0, p1 = panel(dt.date(2024, 6, 3)), panel(dt.date(2024, 6, 4))
    moved = bpricer(dt.date(2024, 6, 4), list(p1.quotes.securities.values()), {k: Quote(ytm=q.ytm + 0.05) for k, q in p1.quotes.quotes.items()}, aliases=dict(p1.quotes.aliases))
    b = bbuild(p0, "T10", notional=2e7)
    c = ctx(moved, prev=p0)
    v0, v1 = b.value(ctx=ctx(p0)), b.value(ctx=c)
    d = {k: getattr(b, k)(ctx=c) for k in ("carry", "roll", "delta", "convexity")}
    total = v1.pv + v1.cash + v1.financing - v0.pv
    assert sum(d.values()) == pytest.approx(total, rel=2e-3), "carry + roll + delta + convexity explain the move up to the third order"
    assert d["delta"] < 0 and d["convexity"] > 0, "yields rose: a long loses in delta and gains in convexity"
    yc = R.ytm_curve(p0)
    ttm0 = (b.security.maturity_date - p0.reference_date).days / 365.25
    y0 = b.mark(p0)["ytm"]
    d1 = b._derivs(y0, p1.reference_date)[0]
    pull = float(np.interp(max(ttm0 - 1 / 365.25, 1e-3), yc[0], yc[1]) - np.interp(ttm0, yc[0], yc[1]))
    assert d["roll"] == pytest.approx(2e7 / 100 * d1 * pull, rel=1e-12) and d["roll"] != 0.0
    flat = bpricer(dt.date(2024, 6, 3), [s for s in p0.quotes.securities.values() if s.id == "T10"], {"T10": Quote(ytm=4.3)}, aliases={"CT10": "T10"})
    assert R.ytm_curve(flat) is None and bbuild(flat, "T10").roll(ctx=ctx(moved, prev=flat)) == 0.0, "without two quoted points there is no yield curve to age down: roll is 0"
    with pytest.raises(ConfigError, match="previous pricer"):
        b.carry(ctx=ctx(p0))


def test_the_bond_kit_loads_under_the_core_contract_and_the_stack_ships_no_market_specific_spec():
    from pricebt.contracts.spec import build_spec

    spec = build_spec("ust", {"factory": R.bond, "conventions": BOND_CONV}, schemas=SchemaRegistry.default())
    assert {"value", "dv01", "gamma", "rate", "carry", "roll", "delta", "convexity"} <= set(spec.bindings)
    assert not R.STACK.instruments and not R.STACK.defaults and R.STACK.wrap is R.wrap, "no market-specific default: a spec needs a calendar name, which is the snapshot data"
    with pytest.raises(ConfigError, match="does not implement it"):
        R.bond_conventions({**BOND_CONV, "yield_convention": "street"})
    with pytest.raises(ConfigError, match="missing"):
        R.bond_conventions({"calendar": "wk"})


def test_a_session_on_the_reference_stack_takes_its_spec_from_the_caller():
    from pricebt.errors import NotSupportedError
    from pricebt.instrument import IRSwap
    from pricebt.session import PricebtSession

    PricebtSession.reset()
    try:
        PricebtSession.use(market=object(), stack="pricebt.testing.refstack:STACK", instruments={"swap:USD": {"factory": R.swap, "conventions": {**R.USD_SOFR_OIS_CONVENTIONS, "calendar": "wk"}}})
        t = IRSwap("Pay", "5y", "USD", notional_amount=1e7)
        assert t.spec.factory is R.swap and t.spec.conventions["calendar"] == "wk" and t.terms["maturity"] == "5y" and t.direction == +1
        PricebtSession.use(market=object(), stack="pricebt.testing.refstack:STACK")
        with pytest.raises(NotSupportedError, match="swap:USD"):
            IRSwap("Pay", "5y", "USD")
    finally:
        PricebtSession.reset()
