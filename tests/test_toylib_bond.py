"""Known-answer tests for the toy bond with repo financing (docs/v2/BOND_DESIGN.md section 5:
tests/toylib/bond.py, tests/assets/toy_usd_bond.yaml).

Every expected value is computed HERE from first principles: the bond schedules are written out,
settlement (T+1 weekday), price-from-yield, accrued, forward, carry and the repo interest are
re-implemented in a few lines below and fed only the toy world's levels (zero rate, spread). The
toy function under test is never called with other inputs to produce an expected value.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import pricebt.risk as risk
import toylib.bond as tb
import toylib.rates as tr
from pricebt.backtests.actions import AddTradeAction
from pricebt.backtests.backtest_objects import bond_pnl_definition
from pricebt.backtests.generic_engine import GenericEngine
from pricebt.backtests.strategy import Strategy
from pricebt.backtests.triggers import DateTrigger, DateTriggerRequirements
from pricebt.instrument import Bond
from pricebt.markets import PricingContext
from pricebt.session import PricebtSession

ASSETS = Path(__file__).parent / "assets"
N = 1e6
D = date(2024, 3, 4)  # a Monday
SPECIAL_ID, GC_ID = "TOY 4.25 2034-11-15", "TOY 4.5 2054-02-15"
SPECIAL = 0.0020  # TOY 4.25 2034-11-15 trades 20bp special to GC (BOND_DESIGN section 5)
GC_SPREAD = 0.0015  # GC = the toy zero rate - 15bp
HAIRCUT = 0.02  # the config default
# identifier -> (annual coupon, maturity, coupon months; every coupon on the 15th), written out
SCHEDULES = {
    SPECIAL_ID: (0.0425, date(2034, 11, 15), (5, 11)),
    GC_ID: (0.045, date(2054, 2, 15), (2, 8)),
}
SHORT_ID = "TOY 3.5 2027-05-15"  # matures inside the tests' horizons; GC (no special)
SCHEDULES_SHORT = {SHORT_ID: (0.035, date(2027, 5, 15), (5, 11))}


# ------------------------------------------------------------------------------------ the test's own bond maths


def _next_wd(d):
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _wd_on_or_before(d):
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _coupon_dates(ident):
    _c, mat, months = {**SCHEDULES, **SCHEDULES_SHORT}[ident]
    return [date(y, m, 15) for y in range(2020, mat.year + 1) for m in months if date(y, m, 15) <= mat]


def _flows(ident, face):
    c, mat, _months = {**SCHEDULES, **SCHEDULES_SHORT}[ident]
    return [(p, face * c / 2) for p in _coupon_dates(ident)] + [(mat, face)]


def _price(flows, s, y):
    """Settlement-date value at yield y (continuous, ACT/365 from s) of the flows paid after s."""
    return sum(a * math.exp(-y * (p - s).days / 365) for p, a in flows if p > s)


def _accrued(ident, face, x):
    c, mat, _months = {**SCHEDULES, **SCHEDULES_SHORT}[ident]
    if x >= mat:
        return 0.0
    pays = _coupon_dates(ident)
    prev, nxt = max(p for p in pays if p <= x), min(p for p in pays if p > x)
    return face * c / 2 * (x - prev).days / (nxt - prev).days


def _y(d):
    """The own rate on the flat toy world: zero rate + spread (the market's levels)."""
    m = tb.market(d, "USD")
    return m.curve.zero_rate + m.spread


def _repo(d, special=SPECIAL):
    """The overnight rate fixed on weekday d: GC - special, decimal ACT/360."""
    return tr._zero_rate(d, "USD") - GC_SPREAD - special


def _interest(principal, first, last, special=SPECIAL):
    """principal x sum over calendar days x in [first, last) of the rate fixed on the last weekday <= x / 360."""
    days = [first + timedelta(days=k) for k in range((last - first).days)]
    return principal * sum(_repo(_wd_on_or_before(x), special) / 360 for x in days)


def _resolve(ident=SPECIAL_ID, d=D, size=N, buy_sell="Buy", term="overnight", haircut=HAIRCUT, m=None):
    m = m or tb.market(d, "USD")
    return m, tb.resolve_bond(m, dict(identifier=ident, size=size, buy_sell=buy_sell, repo_term=term, repo_haircut=haircut))


# ------------------------------------------------------------------------------------ 1. price and yield


@pytest.mark.parametrize("t, s", [(D, date(2024, 3, 5)), (date(2024, 3, 8), date(2024, 3, 11))], ids=["monday", "friday"])
@pytest.mark.parametrize("ident", list(SCHEDULES))
def test_price_is_the_settlement_value_at_the_yield_and_the_yield_is_z_plus_spread(ident, t, s):
    """Price = sum over flows after s = settle(t) of CF exp(-y tau(s, p)), never discounted back to t;
    on the flat world the yield is z + spread exactly. Mutation: tau from t instead of s in
    toylib.bond._flow_pvs -> fails; settle T+0 -> fails."""
    m, b = _resolve(ident, d=t)
    y = _y(t)
    assert tb.npv(m, b) == pytest.approx(_price(_flows(ident, N), s, y), rel=1e-12)
    assert tb.yield_bp(m, b) == pytest.approx(y * 1e4, abs=1e-9)
    assert tb.days_to_settlement(m, b) == (s - t).days
    # accrued at settlement, not t + 1 (a Friday settles on Monday). Mutation: accrued at t + 1 -> fails
    assert tb.accrued(m, b) == pytest.approx(_accrued(ident, N, s), rel=1e-12)
    assert tb.clean_price(m, b) == pytest.approx(100 * (_price(_flows(ident, N), s, y) - _accrued(ident, N, s)) / N, rel=1e-12)
    _, short = _resolve(ident, d=t, buy_sell="Sell")
    assert tb.npv(m, short) == pytest.approx(-_price(_flows(ident, N), s, y), rel=1e-12)
    assert tb.yield_bp(m, short) == pytest.approx(y * 1e4, abs=1e-9)


# ------------------------------------------------------------------------------------ 2. accrued over a coupon date


def test_accrued_over_a_coupon_date_and_clean_plus_accrued_is_dirty():
    """The 2024-05-15 (Wed) coupon: on Mon 05-13 settlement (Tue) is one day before it; on Tue
    05-14 (the drop date) settlement IS the coupon date, accrued 0 and the coupon gone from Price;
    on Wed 05-15 one day of the new period. Mutations: settle T+0, or accrued at t instead of
    settlement -> fails."""
    _, b = _resolve(d=date(2024, 5, 1))
    flows, coupon = _flows(SPECIAL_ID, N), N * 0.0425 / 2
    expected = {
        date(2024, 5, 13): coupon * (date(2024, 5, 14) - date(2023, 11, 15)).days / (date(2024, 5, 15) - date(2023, 11, 15)).days,
        date(2024, 5, 14): 0.0,
        date(2024, 5, 15): coupon * 1 / (date(2024, 11, 15) - date(2024, 5, 15)).days,
    }
    for t, ai in expected.items():
        m, s = tb.market(t, "USD"), _next_wd(t)
        assert tb.accrued(m, b) == pytest.approx(ai, rel=1e-12, abs=1e-12), t
        dirty = 100 * _price(flows, s, _y(t)) / N
        assert tb.dirty_price(m, b) == pytest.approx(dirty, rel=1e-12), t
        assert tb.clean_price(m, b) == pytest.approx(dirty - 100 * ai / N, rel=1e-12), t
        assert tb.clean_price(m, b) + 100 * tb.accrued(m, b) / N == pytest.approx(tb.dirty_price(m, b), rel=1e-14), t
    m13, m14 = tb.market(date(2024, 5, 13), "USD"), tb.market(date(2024, 5, 14), "USD")
    assert tb.cashflows(m13, b)["payment_date"].iloc[0] == date(2024, 5, 14)  # still to drop, on 05-14
    assert tb.cashflows(m14, b)["payment_date"].iloc[0] == date(2024, 11, 14)  # dropped; next: Thu before Fri 11-15


# ------------------------------------------------------------------------------------ 3. duration and convexity


@pytest.mark.parametrize("ident", list(SCHEDULES))
def test_duration_and_convexity_are_finite_differences_of_price_from_yield(ident):
    """Mutation: tau from t instead of s in toylib.bond._at_yield -> fails."""
    m, b = _resolve(ident)
    s, y, h = date(2024, 3, 5), _y(D), 2e-5
    p = lambda yy: _price(_flows(ident, N), s, yy)  # noqa: E731
    assert tb.modified_duration(m, b) == pytest.approx(-(p(y + h) - p(y - h)) / (2 * h) / p(y), rel=1e-6)
    assert tb.convexity(m, b) == pytest.approx((p(y + h) + p(y - h) - 2 * p(y)) / h**2 / p(y), rel=1e-6)
    assert tb.yield_dv01(m, b) == pytest.approx(p(y + 0.5e-4) - p(y - 0.5e-4), rel=1e-6)  # per +1bp
    # BOND_DESIGN's "ModifiedDuration ~ -1e4 IRDelta / Price": IRDelta is a +-1bp central difference,
    # O(h^2) off the analytic derivative (about 1e-6 relative on the 30y), hence the looser bound
    assert tb.modified_duration(m, b) == pytest.approx(-1e4 * tb.delta(m, b) / tb.npv(m, b), rel=1e-5)


# ------------------------------------------------------------------------------------ 4. forward price parity


@pytest.mark.parametrize("t, horizon, coupon_inside", [(D, date(2024, 4, 5), False), (date(2024, 5, 1), date(2024, 6, 3), True)])
def test_forward_price_parity(t, horizon, coupon_inside):
    """ForwardPrice = Price (1 + r tau(s, H)) - sum C (1 + r tau(c, H)) over coupons c in (s, H],
    tau ACT/360, r = GC - special; H = s + 1 month (2024-06-02 is a Sunday: rolled to Monday 06-03).
    Carry = clean now - clean forward. Mutations: drop the special, ACT/365 repo, or drop the coupon
    reinvestment term (r tau(c, H)) -> fails."""
    m, b = _resolve(d=t)
    s, r, flows = _next_wd(t), _repo(t), _flows(SPECIAL_ID, N)
    assert tb.horizon(s) == horizon
    price = _price(flows, s, _y(t))
    inside = [(p, a) for p, a in flows if s < p <= horizon]
    assert bool(inside) == coupon_inside
    fwd = price * (1 + r * (horizon - s).days / 360) - sum(a * (1 + r * (horizon - p).days / 360) for p, a in inside)
    assert tb.forward_price(m, b) == pytest.approx(fwd, rel=1e-12)
    assert tb.repo_rate(m, b) == pytest.approx(r * 1e4, rel=1e-12)
    carry = (price - _accrued(SPECIAL_ID, N, s)) - (fwd - _accrued(SPECIAL_ID, N, horizon))
    assert tb.carry(m, b) == pytest.approx(carry, rel=1e-9)
    # flat curve: the rolled value at H is the flows after H at the same yield; coupons paid in
    # (s, H] are not in it. Mutation: keep them in the horizon value -> fails
    y = _y(t)
    pull = (_price(flows, horizon, y) - _accrued(SPECIAL_ID, N, horizon)) - (price - _accrued(SPECIAL_ID, N, s))
    assert tb.roll_down(m, b) == pytest.approx(pull, rel=1e-9)


def test_roll_down_on_a_sloped_curve_rolls_the_curve_not_the_forwards():
    """On a sloped curve the rolled value at H discounts each flow p by the TODAY curve's DF over
    the time to maturity left at H, DF(t, t + (p - H)), with the spread held: not by the forward
    curve DF(H, p). Mutation: forward-curve discounting in toylib.bond.roll_down -> fails."""
    z, slope, sp = 0.03, 0.002, 0.0025
    m = SimpleNamespace(curve=tr.ToyCurve(D, "USD", z, None, slope), spread=sp, repo=0.03)
    _, b = _resolve(GC_ID, m=m)
    s, horizon, flows = date(2024, 3, 5), date(2024, 4, 5), _flows(GC_ID, N)

    def df(x):  # the test's own sloped discount factor from D
        tau = (x - D).days / 365
        return math.exp(-(z + slope * tau) * tau)

    now = sum(a * df(p) / df(s) * math.exp(-sp * (p - s).days / 365) for p, a in flows if p > s)
    rolled = sum(a * df(D + (p - horizon)) * math.exp(-sp * (p - horizon).days / 365) for p, a in flows if p > horizon)
    want = (rolled - _accrued(GC_ID, N, horizon)) - (now - _accrued(GC_ID, N, s))
    forward_curve = sum(a * df(p) / df(horizon) * math.exp(-sp * (p - horizon).days / 365) for p, a in flows if p > horizon)
    assert abs(rolled - forward_curve) > 1000  # the two conventions really differ here
    assert tb.roll_down(m, b) == pytest.approx(want, rel=1e-9)


def test_forward_price_when_the_bond_matures_before_the_horizon():
    """TOY 3.5 2027-05-15 on Mon 2027-05-03: settles 05-04, H = 06-04, maturity 05-15 inside:
    the final coupon and the principal are both reinvested to H. Mutation: principal left out of
    the paid flows -> fails."""
    t = date(2027, 5, 3)
    m, b = _resolve(SHORT_ID, d=t)
    s, horizon, r, flows = date(2027, 5, 4), date(2027, 6, 4), _repo(t, special=0.0), _flows(SHORT_ID, N)
    assert tb.horizon(s) == horizon
    price = _price(flows, s, _y(t))
    paid = [(p, a) for p, a in flows if s < p <= horizon]
    assert len(paid) == 2  # the final coupon and the principal
    fwd = price * (1 + r * (horizon - s).days / 360) - sum(a * (1 + r * (horizon - p).days / 360) for p, a in paid)
    assert tb.forward_price(m, b) == pytest.approx(fwd, rel=1e-9, abs=1e-6)
    assert tb.carry(m, b) == pytest.approx((price - _accrued(SHORT_ID, N, s)) - fwd, rel=1e-9, abs=1e-6)  # AI(H) = 0 after maturity


# ------------------------------------------------------------------------------------ 5. zero carry + roll


def test_carry_plus_roll_down_is_zero_when_the_repo_matches_the_yield_on_a_flat_curve():
    """A flat curve, no special (TOY 4.5 2054: Feb/Aug coupons, none in (s, H]), and a repo r with
    1 + r tau360(s, H) = exp(y tau365(s, H)): the financed P&L to H at an unchanged curve is 0.
    10bp more repo costs Price x 10bp x tau360. RollDown = the pull to par at constant yield.
    Mutation: drop AI(H) in toylib.bond.roll_down -> fails."""
    t, s, horizon = D, date(2024, 3, 5), date(2024, 4, 5)
    z, sp = 0.031, 0.0025
    y, t365, t360 = z + sp, (horizon - s).days / 365, (horizon - s).days / 360
    r = (math.exp(y * t365) - 1) / t360
    m = SimpleNamespace(curve=tr.ToyCurve(t, "USD", z), spread=sp, repo=r)
    _, b = _resolve(GC_ID, m=m)
    flows = _flows(GC_ID, N)
    assert not [p for p, _a in flows if s < p <= horizon]
    carry, roll = tb.carry(m, b), tb.roll_down(m, b)
    assert abs(carry) > 100 and abs(roll) > 100  # each is a real number on its own
    assert carry + roll == pytest.approx(0.0, abs=1e-6)
    pull = (_price(flows, horizon, y) - _accrued(GC_ID, N, horizon)) - (_price(flows, s, y) - _accrued(GC_ID, N, s))
    assert roll == pytest.approx(pull, rel=1e-10)
    dear = SimpleNamespace(curve=m.curve, spread=sp, repo=r + 0.001)
    assert tb.carry(dear, b) + tb.roll_down(dear, b) == pytest.approx(-_price(flows, s, y) * 0.001 * t360, rel=1e-9)


# ------------------------------------------------------------------------------------ financing terms


def test_repo_terms_and_financing_to_date_day_loop():
    """Traded Wed 2024-03-06 (settles Thu 03-07), priced Tue 03-12 (settles Wed 03-13): six repo days,
    Sat and Sun at Friday's fixing. Overnight: each day's GC - special; term: the trade date's rate.
    Principal (1 - haircut) x Price(trade date); a long pays, a short receives; 0 on the trade date.
    Mutations: fix the weekend at its own date, drop the haircut, drop the special, ACT/365 -> fails."""
    td, t = date(2024, 3, 6), date(2024, 3, 12)
    m0, m = tb.market(td, "USD"), tb.market(t, "USD")
    principal = (1 - HAIRCUT) * _price(_flows(SPECIAL_ID, N), date(2024, 3, 7), _y(td))
    for term in ("overnight", "term"):
        _, long_ = _resolve(d=td, term=term)
        _, short = _resolve(d=td, term=term, buy_sell="Sell")
        assert tb.financing_to_date(m0, long_) == 0.0
        if term == "overnight":
            expected = -_interest(principal, date(2024, 3, 7), date(2024, 3, 13))
            assert tb.repo_rate(m, long_) == pytest.approx(_repo(t) * 1e4, rel=1e-12)
            saturday = tb.market(date(2024, 3, 9), "USD")  # over a weekend: Friday's fixing is in force
            assert tb.repo_rate(saturday, long_) == pytest.approx(_repo(date(2024, 3, 8)) * 1e4, rel=1e-12)
        else:
            expected = -principal * _repo(td) * 6 / 360
            assert tb.repo_rate(m, long_) == pytest.approx(_repo(td) * 1e4, rel=1e-12)
        assert tb.financing_to_date(m, long_) == pytest.approx(expected, rel=1e-12) and expected < 0, term
        assert tb.financing_to_date(m, short) == pytest.approx(-expected, rel=1e-12), term
        assert tb.repo_haircut(m, long_) == HAIRCUT
    _, gc_bond = _resolve(GC_ID, d=td)
    assert tb.repo_rate(m, gc_bond) == pytest.approx((_repo(t) + SPECIAL) * 1e4, rel=1e-12)  # no special: GC
    with pytest.raises(ValueError, match="non-standard settlement"):
        tb.resolve_bond(m0, dict(identifier=SPECIAL_ID, size=N, repo_term="overnight", repo_haircut=HAIRCUT, settlement_date=date(2024, 3, 8)))
    same = tb.resolve_bond(m0, dict(identifier=SPECIAL_ID, size=N, repo_term="overnight", repo_haircut=HAIRCUT, settlement_date=date(2024, 3, 7)))
    assert same["settle0"] == date(2024, 3, 7)
    for bad in (dict(repo_term="weekly", repo_haircut=HAIRCUT), dict(repo_term="term", repo_haircut=1.0)):
        with pytest.raises(ValueError, match="repo_"):
            tb.resolve_bond(m0, dict(identifier=SPECIAL_ID, size=N, **bad))


# ------------------------------------------------------------------------------------ 6. financed Totals in a backtest


def test_financed_totals_over_a_coupon_date():
    """A real GenericEngine run: a long TOY 4.25 bought Mon 2024-05-06, sold Mon 05-20, held across
    the 05-15 coupon (dropped Tue 05-14). The engine books the coupon and the repo interest as
    holding cash (DEV-E22), so Total(end) - Total(start) = Price(exit) - Price(entry) + coupon -
    repo interest from settle(entry) to settle(exit), all computed here; the table's economic_pnl
    sums to the same, its financing_pnl to the FinancingToDate change, and its cashflow_pnl is the
    coupon on exactly the drop step. Mutations: drop the special, ACT/365 repo, Cashflows
    payment_date = the coupon date -> fails."""
    start, exit_, end = date(2024, 5, 6), date(2024, 5, 20), date(2024, 5, 24)
    PricebtSession.use(assets=[ASSETS / "toy_usd_bond.yaml"])
    b = Bond(identifier=SPECIAL_ID, size=N, buy_sell="Buy", settlement_currency="USD", name="bond")
    trigger = DateTrigger(DateTriggerRequirements(dates=[start]), [AddTradeAction(b, exit_, name="Add")])
    bt = GenericEngine().run_backtest(
        Strategy(None, [trigger]), start=start, end=end, frequency="1b", pnl_explain=bond_pnl_definition(), show_progress=False
    )
    flows, coupon = _flows(SPECIAL_ID, N), N * 0.0425 / 2
    entry, exit_price = _price(flows, _next_wd(start), _y(start)), _price(flows, _next_wd(exit_), _y(exit_))
    interest = _interest((1 - HAIRCUT) * entry, _next_wd(start), _next_wd(exit_))
    expected = exit_price - entry + coupon - interest
    total = bt.result_summary["Total"]
    assert total[start] == pytest.approx(0.0, abs=1e-6)
    assert total[end] - total[start] == pytest.approx(expected, rel=1e-10)
    assert interest > 100.0  # two weeks of repo on ~1.1m

    table = bt.pnl_explain_table()
    assert table["economic_pnl"].sum() == pytest.approx(total[end] - total[start], rel=1e-10)
    assert table["financing_pnl"].sum() == pytest.approx(-interest, rel=1e-10)
    terms = bt.results[start].portfolio.all_instruments[0].resolved_terms
    assert tb.financing_to_date(tb.market(exit_, "USD"), terms) == pytest.approx(-interest, rel=1e-10)
    paid = table["cashflow_pnl"]
    assert list(paid[paid != 0].index) == [date(2024, 5, 14)] and paid[date(2024, 5, 14)] == pytest.approx(coupon, rel=1e-12)


# ------------------------------------------------------------------------------------ 7/8. quantity and direction through pricebt


EXTENSIVE = (
    risk.Price, risk.FairPremium, risk.ForwardPrice, risk.AccruedInterest, risk.FinancingToDate, risk.Carry, risk.RollDown,
    risk.Annuity, risk.Theta, risk.IRDeltaParallel, risk.IRGammaParallel, risk.IRDiscountDeltaParallel, risk.LightningDV01,
)
SIGNED_LEVELS = (risk.PremiumCents, risk.LocalAnnuityInCents)  # per |face|: intensive, holder-signed
LEVELS = (
    risk.IRFwdRate, risk.IRSpotRate, risk.CleanPrice, risk.DirtyPrice, risk.ModifiedDuration, risk.Convexity,
    risk.DaysToSettlement, risk.RepoRate, risk.RepoHaircut, risk.CompoundedFixedRate, risk.LightningOAS, risk.ParSpread,
    risk.ExpiryInYears,
)
FRAMES = {risk.Cashflows: "payment_amount", risk.CRIFIRCurve: "Amount"}


def _values(buy_sell="Buy", quantity=1.0):
    """Every measure above via pricebt: resolved on D, priced on Tue 2024-03-12 (so FinancingToDate != 0)."""
    PricebtSession.use(assets=[ASSETS / "toy_usd_bond.yaml"])
    inst = Bond(identifier=SPECIAL_ID, size=N, buy_sell=buy_sell, settlement_currency="USD", name="b")
    with PricingContext(pricing_date=D):
        inst = inst.resolve(in_place=False).result().clone(quantity_=quantity)
    with PricingContext(pricing_date=date(2024, 3, 12)):
        futures = {m: inst.calc(m) for m in EXTENSIVE + SIGNED_LEVELS + LEVELS + tuple(FRAMES)}
    return {m: f.result() for m, f in futures.items()}


def test_quantity_scales_amounts_and_not_levels():
    """2.5x: every amount (FinancingToDate, Carry, RollDown included) and each frame's amount column
    scale; the levels per unit face do not. Mutation: FinancingToDate's unit decimal in the config -> fails."""
    one, big = _values(), _values(quantity=2.5)
    for m in EXTENSIVE:
        assert float(big[m]) == pytest.approx(2.5 * float(one[m]), rel=1e-12) and float(one[m]) != 0.0, m
    for m in SIGNED_LEVELS + LEVELS:
        assert float(big[m]) == pytest.approx(float(one[m]), rel=1e-12) and float(one[m]) != 0.0, m
    for m, col in FRAMES.items():
        assert list(big[m][col]) == pytest.approx([2.5 * x for x in one[m][col]], rel=1e-12) and one[m][col].abs().sum() > 0, m


def test_long_and_short_are_symmetric():
    """Sell: every amount negated (FinancingToDate: a short lends the cash and receives; Carry and
    RollDown flip), PremiumCents and LocalAnnuityInCents negated; the levels equal. Mutation: an
    unsigned financed principal (abs of Price) in toylib.bond.resolve_bond -> fails."""
    buy, sell = _values(), _values("Sell")
    assert float(buy[risk.FinancingToDate]) < 0 < float(sell[risk.FinancingToDate])
    for m in EXTENSIVE + SIGNED_LEVELS:
        assert float(sell[m]) == pytest.approx(-float(buy[m]), rel=1e-12) and float(buy[m]) != 0.0, m
    for m in LEVELS:
        assert float(sell[m]) == pytest.approx(float(buy[m]), rel=1e-12), m
    for m, col in FRAMES.items():
        assert list(sell[m][col]) == pytest.approx([-x for x in buy[m][col]], rel=1e-12), m


# ------------------------------------------------------------------------------------ 9. Theta x step days


@pytest.mark.parametrize(
    "t, nb, dropped",
    [(date(2024, 3, 7), date(2024, 3, 8), False), (date(2024, 3, 8), date(2024, 3, 11), False), (date(2024, 5, 13), date(2024, 5, 14), True)],
    ids=["thu-fri", "fri-mon", "coupon-drop"],
)
def test_theta_times_step_days_is_the_constant_yield_change_to_the_next_weekday(t, nb, dropped):
    """Theta x (nb - t).days = Price(nb at today's yield) + flows dropped - Price(t). Thu -> Fri moves
    settlement 3 days in 1 calendar day, Fri -> Mon 1 day in 3: Theta spreads the next-weekday step
    over its calendar days. Mutation: divide by 1 instead of (nb - t).days in toylib.bond.theta_1d -> fails."""
    _, b = _resolve(d=date(2024, 3, 1))
    m = tb.market(t, "USD")
    y, flows, s0, s1 = _y(t), _flows(SPECIAL_ID, N), _next_wd(t), _next_wd(nb)
    cash = sum(a for p, a in flows if s0 < p <= s1)
    assert (cash > 0) == dropped
    assert tb.theta_1d(m, b) * (nb - t).days == pytest.approx(_price(flows, s1, y) + cash - _price(flows, s0, y), rel=1e-10)


def test_theta_telescopes_over_thu_fri_mon_when_nothing_moves(monkeypatch):
    """Frozen world (every level constant): Theta(Thu) x 1 + Theta(Fri) x 3 = Price(Mon) - Price(Thu)."""
    monkeypatch.setitem(tr._CCY_PARAMS, "USD", (0.03, 0.0, 252))
    monkeypatch.setitem(tb._SPREAD_PARAMS, "USD", (0.003, 0.0, 200))
    _, b = _resolve(d=date(2024, 3, 1))
    thu, fri, mon = date(2024, 3, 7), date(2024, 3, 8), date(2024, 3, 11)
    carried = tb.theta_1d(tb.market(thu, "USD"), b) * 1 + tb.theta_1d(tb.market(fri, "USD"), b) * 3
    assert carried == pytest.approx(_price(_flows(SPECIAL_ID, N), _next_wd(mon), 0.033) - _price(_flows(SPECIAL_ID, N), _next_wd(thu), 0.033), rel=1e-10)


# ------------------------------------------------------------------------------------ dead


def test_dead_bond_sensitivities_zero_levels_finite():
    """TOY 4.25 seen on Thu 2034-11-16 (settles after maturity): every sensitivity and amount 0,
    the CRIF empty, the levels finite; the day before maturity's drop date it is still live."""
    _, b = _resolve()
    m = tb.market(date(2034, 11, 16), "USD")
    for fn in (tb.npv, tb.delta, tb.gamma, tb.discount_delta, tb.yield_dv01, tb.theta_1d, tb.annuity, tb.accrued, tb.dirty_price,
               tb.clean_price, tb.modified_duration, tb.convexity, tb.forward_price, tb.carry, tb.roll_down, tb.premium_cents):
        assert fn(m, b) == 0.0, fn.__name__
    assert tb.crif_ir_curve(m, b).empty and tb.cashflows(m, b).empty
    for fn in (tb.yield_bp, tb.repo_rate, tb.financing_to_date, tb.spread_bp):
        assert math.isfinite(fn(m, b)), fn.__name__
    assert tb.npv(tb.market(date(2034, 11, 13), "USD"), b) != 0.0  # Mon: settles Tue, before the Wed maturity


def test_financing_stops_at_maturity():
    """TOY 3.5 2027-05-15 (a Saturday) bought 2027-04-01, seen 2027-05-20: interest runs from the
    trade's settlement to maturity, not to the pricing date's settlement. Mutation: no maturity cap
    in toylib.bond.financing_to_date -> fails."""
    t0 = date(2027, 4, 1)
    _, b = _resolve(SHORT_ID, d=t0)
    s0 = _next_wd(t0)
    principal = (1 - HAIRCUT) * _price(_flows(SHORT_ID, N), s0, _y(t0))
    want = -_interest(principal, s0, date(2027, 5, 15), special=0.0)
    for seen in (date(2027, 5, 20), date(2027, 6, 30)):
        assert tb.financing_to_date(tb.market(seen, "USD"), b) == pytest.approx(want, rel=1e-12), seen
