"""Known-answer tests for the toy IR measure-contract libraries and configs (docs/v2/IR_RISK_DESIGN.md
section 6.5, section 00 R2-1..R2-8; docs/v2/IR_STRICT_CONTRACT.md R3-1): toylib.irrisk (swap),
toylib.swaption, toylib.bond, the chain-rule toylib.rates.gamma, and every toy IR config. Every expected value is computed here independently (finite
differences with other step sizes, closed forms, hand-built schedules), never read back from the
function under test. Frozen-world tests monkeypatch the toy parameter tables to constants."""
from __future__ import annotations

import dataclasses
import math
import warnings
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import pricebt.risk as risk
import toylib.bond as tb
import toylib.irrisk as tri
import toylib.rates as tr
import toylib.swaption as ts
from pricebt.assets import load_asset
from pricebt.instrument import Bond, IRSwap, IRSwaption
from pricebt.markets import CloseMarket, PricingContext
from pricebt.risk import contracts
from pricebt.session import PricebtSession

ASSETS = Path(__file__).parent / "assets"
CONFIGS = {"IRSwap": "toy_usd_irs_full.yaml", "IRSwaption": "toy_usd_swaption.yaml", "Bond": "toy_usd_bond.yaml"}
# every toy swap/swaption config (strict, R3-0): each maps the whole contract on its own. The strict
# Bond config (toy_usd_bond.yaml) has its own quantity, direction and identity tests in test_toylib_bond.py
STRICT_CONFIGS = {"toy_usd_irs.yaml": "IRSwap", "toy_eur_irs.yaml": "IRSwap", "toy_usd_irs_full.yaml": "IRSwap", "toy_usd_swaption.yaml": "IRSwaption"}
D = date(2024, 3, 4)  # a Monday
D2 = date(2024, 7, 15)
N = 1e6
H = 1e-4


# ------------------------------------------------------------------------------------ helpers


def _bump(curve, dz):
    return dataclasses.replace(curve, zero_rate=curve.zero_rate + dz)


def _swap(fixed_rate="ATM", term="10y", pay_or_receive="Pay", d=D):
    m = tr.market(d, "USD")
    return m, tr.build_swap(m, tr.resolve_swap(m, dict(pay_or_receive=pay_or_receive, termination_date=term, notional_amount=N, fixed_rate=fixed_rate)))


def _swaption(pay_or_receive="Pay", strike="ATM", expiration_date="1y", d=D):
    m = ts.market(d, "USD")
    return m, ts.resolve_swaption(m, dict(pay_or_receive=pay_or_receive, expiration_date=expiration_date, termination_date="10y", notional_amount=N, strike=strike))


def _bond(identifier="TOY 4.25 2034-11-15", d=D, size=N):
    m = tb.market(d, "USD")
    return m, tb.resolve_bond(m, dict(identifier=identifier, size=size, buy_sell="Buy", repo_term="overnight", repo_haircut=0.02))


def _smkt(m, dz=0.0, dsigma=0.0):
    return SimpleNamespace(curve=_bump(m.curve, dz), sigma=m.sigma + dsigma)


def _bmkt(m, dz=0.0):
    return SimpleNamespace(curve=_bump(m.curve, dz), spread=m.spread, repo=m.repo)


def _fwd_bp(curve, trade):
    return tr._par_rate(curve, trade["expiration_date"], trade["termination_date"]) * 1e4


@pytest.fixture
def frozen(monkeypatch):
    """Every toy level constant in time: the day-over-day change is pure carry."""
    monkeypatch.setitem(tr._CCY_PARAMS, "USD", (0.03, 0.0, 252))
    monkeypatch.setitem(ts._SIGMA_PARAMS, "USD", (0.008, 0.0, 252))
    monkeypatch.setitem(tb._SPREAD_PARAMS, "USD", (0.003, 0.0, 200))


# ------------------------------------------------------------------------------------ configs


@pytest.mark.parametrize("instrument, fname", list(CONFIGS.items()) + [(i, f) for f, i in STRICT_CONFIGS.items() if f not in CONFIGS.values()])
def test_config_maps_the_whole_contract_without_declarations_or_warnings(instrument, fname):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        cfg = load_asset(ASSETS / fname)
    assert cfg.instrument == instrument
    assert cfg.unsupported_measures == {}


def _contract_requests(instrument, d=D):
    """Every contract row as a request: FD measures in their scalar (Type) and bucketed forms;
    PnlExplain towards the close a week after `d`."""
    for req in contracts.contract_for(instrument):
        m = getattr(risk, req.measure)
        if m is risk.PnlExplain:
            yield m(CloseMarket(date=d + timedelta(days=7)))
        elif isinstance(m, risk.RiskMeasureWithFiniteDifferenceParameter):
            yield from ([m(aggregation_level="Type")] if "scalar" in req.forms else []) + ([m] if "bucketed" in req.forms else [])
        else:
            yield m


_INSTS = {
    "toy_usd_irs.yaml": IRSwap("Pay", "10y", "USD", N, fixed_rate=0.035, name="s"),
    "toy_eur_irs.yaml": IRSwap("Receive", "10y", "EUR", N, fixed_rate=0.025, name="s"),
    "toy_usd_irs_full.yaml": IRSwap("Pay", "10y", "USD", N, fixed_rate=0.035, name="s"),
    "toy_usd_swaption.yaml": IRSwaption("Straddle", "10y", "USD", notional_amount=N, expiration_date="1y", strike="ATM+25", name="o"),
    "toy_usd_bond.yaml": Bond(identifier="TOY 4.5 2054-02-15", size=N, name="b"),
}


@pytest.mark.parametrize("fname", _INSTS)
def test_every_contract_measure_prices_through_pricebt(fname):
    """Every row of the contract (R3-1's included) prices finite on two dates; frames have their
    required columns, buckets a value column."""
    inst = _INSTS[fname]
    session = PricebtSession.use(assets=[ASSETS / fname])
    for d in (D, D2):
        for m in _contract_requests(type(inst).__name__, d):
            v = session.pricing.value(inst, d, m, None)
            v = v.result() if hasattr(v, "result") else v
            if isinstance(v, pd.DataFrame):
                if "value" in v.columns:
                    assert v["value"].map(lambda x: math.isfinite(float(x))).all(), (d, m)
                else:
                    required = contracts.FRAME_COLUMNS[m.name]
                    assert set(required) <= set(v.columns), (d, m)
                    if "Amount" in v.columns:
                        assert len(v) == len(contracts.SIMM_IR_TENORS) and v["Amount"].map(math.isfinite).all(), (d, m)
            else:
                assert math.isfinite(float(v)), (d, m)


# ------------------------------------------------------------------------------------ own-rate delta and gamma (R2-1)


def test_swaption_delta_is_the_total_derivative_in_the_forward():
    m, t = _swaption(strike="ATM+30")
    h = 0.5e-4  # another step than the library's 1bp
    fd = (ts.npv(_smkt(m, h), t) - ts.npv(_smkt(m, -h), t)) / (_fwd_bp(_bump(m.curve, h), t) - _fwd_bp(_bump(m.curve, -h), t))
    assert ts.delta(m, t) == pytest.approx(fd, rel=1e-5)  # O(h^2) apart
    assert ts.delta(m, t) > 0  # a bought payer


def test_gamma_is_the_derivative_of_the_total_delta_in_the_own_rate():
    """R2-1 check: Gamma / [d(delta)/dr] = 1 for all three classes (never the half-gamma)."""
    ms, s = _swap(fixed_rate=0.02)
    ratio = (tri.delta(_bump(ms, H), s) - tri.delta(_bump(ms, -H), s)) / (tr.par_rate(_bump(ms, H), s) - tr.par_rate(_bump(ms, -H), s)) / tri.ir_gamma(ms, s)
    assert ratio == pytest.approx(1.0, abs=1e-5)
    mo, o = _swaption(strike="ATM+50")
    ratio = (ts.delta(_smkt(mo, H), o) - ts.delta(_smkt(mo, -H), o)) / (ts.fwd_rate(_smkt(mo, H), o) - ts.fwd_rate(_smkt(mo, -H), o)) / ts.gamma(mo, o)
    assert ratio == pytest.approx(1.0, abs=1e-4)
    mb, b = _bond("TOY 4.5 2054-02-15")
    ratio = (tb.delta(_bmkt(mb, H), b) - tb.delta(_bmkt(mb, -H), b)) / (tb.yield_bp(_bmkt(mb, H), b) - tb.yield_bp(_bmkt(mb, -H), b)) / tb.gamma(mb, b)
    assert ratio == pytest.approx(1.0, abs=1e-5)


@pytest.mark.parametrize("term", ["2y", "10y", "30y"])
def test_swap_atm_gamma_is_twice_the_annuity_pv01_slope(term):
    """Known answer (R14 section 5.2): at the money d2npv/dpar2 = 2 dpv01/dpar, so the half-gamma
    ratio is 0.5 at every tenor; the in-flight recipe without the chain-rule term gives 0.75 at 2y."""
    m, s = _swap(term=term)
    dpv01 = (tr.pv01(_bump(m, H), s) - tr.pv01(_bump(m, -H), s)) / (tr.par_rate(_bump(m, H), s) - tr.par_rate(_bump(m, -H), s))
    assert dpv01 / tri.ir_gamma(m, s) == pytest.approx(0.5, abs=1e-6)
    assert tri.ir_gamma(m, s) < 0  # a payer is short convexity
    assert tri.delta(m, s) == pytest.approx(tr.pv01(m, s), rel=1e-5)  # the annuity pv01 is exact at the money


def test_swap_off_market_delta_is_not_the_annuity_pv01():
    m, s = _swap(fixed_rate=0.02)
    fd = (tr.npv(_bump(m, 0.5e-4), s) - tr.npv(_bump(m, -0.5e-4), s)) / (tr.par_rate(_bump(m, 0.5e-4), s) - tr.par_rate(_bump(m, -0.5e-4), s))
    assert tri.delta(m, s) == pytest.approx(fd, rel=1e-6)
    assert abs(tri.delta(m, s) / tr.pv01(m, s) - 1) > 1e-2  # the N (F-K) dA/dF term (R2-1)


# ------------------------------------------------------------------------------------ discount-only delta (R2-3)


def _hand_legs(curve, dz, eff, mat):
    """(float leg, annuity) per unit notional on a hand-built annual schedule (whole years): every
    period's forward from `curve` (held), every flow discounted at `curve`'s zero rate + dz."""
    pays = [date(eff.year + k, eff.month, eff.day) for k in range(1, mat.year - eff.year + 1)]
    df_disc = lambda d: math.exp(-(curve.zero_rate + dz) * (d - curve.ref_date).days / 365.0)  # noqa: E731
    periods = list(zip([eff] + pays[:-1], pays))
    flt = sum(df_disc(p) * (_df(curve, q) / _df(curve, p) - 1.0) for q, p in periods)
    return flt, sum(df_disc(p) * (p - q).days / 365.0 for q, p in periods)


@pytest.mark.parametrize("fixed_rate", ["ATM", 0.02, 0.045])
def test_swap_discount_delta_bumps_the_discount_curve_only(fixed_rate):
    """IRDiscountDeltaParallel holds the projection forwards and bumps only the discounting: ~0 at
    the money (a whole-curve bump gives ~+844 there), -sign(Price) off the money (K = 2%: -83.66),
    receiver = -payer. Mutation: bump the whole curve in toylib.irrisk.discount_delta_on -> fails."""
    m, payer = _swap(fixed_rate=fixed_rate)
    _, receiver = _swap(fixed_rate=fixed_rate, pay_or_receive="Receive")
    K, eff, mat, h = payer.fixed_rate, payer.effective_date, payer.termination_date, 0.5e-4
    pv = lambda dz: N * (_hand_legs(m, dz, eff, mat)[0] - K * _hand_legs(m, dz, eff, mat)[1])  # noqa: E731
    assert pv(0.0) == pytest.approx(tr.npv(m, payer), rel=1e-9, abs=1e-6)  # the hand legs are the toy's
    dd = tri.discount_delta(m, payer)
    assert dd == pytest.approx((pv(h) - pv(-h)) / (2 * h * 1e4), rel=1e-5, abs=1e-6)
    assert tri.discount_delta(m, receiver) == pytest.approx(-dd, rel=1e-12, abs=1e-12)
    if fixed_rate == "ATM":
        assert abs(dd) < 0.01
    else:
        assert abs(dd) > 10.0 and dd * tr.npv(m, payer) < 0  # discounting a PV harder shrinks it
    if fixed_rate == 0.02:
        assert dd == pytest.approx(-83.66, abs=0.01)


def test_two_curve_float_leg_telescopes_on_one_curve():
    """A seasoned swap (effective in the past): with disc == fwd the float leg is DF(eff) - DF(mat),
    so the two-curve npv is toylib.rates.npv exactly."""
    m = tr.market(D, "USD")
    s = tr.ToySwap(date(2022, 6, 15), date(2029, 6, 15), 0.031, -N)
    assert tri.float_leg(m, m, s.effective_date, s.termination_date) == pytest.approx(_df(m, s.effective_date) - _df(m, s.termination_date), rel=1e-12)
    assert tri.npv_two_curve(m, m, s) == pytest.approx(tr.npv(m, s), rel=1e-10)


@pytest.mark.parametrize("strike", ["ATM", "ATM+50"])
def test_swaption_discount_delta_bumps_the_discount_curve_only(strike):
    """Bachelier on the underlying with its forwards held and its discounting (annuity, F's weights)
    bumped, written out here; payer - receiver = the forward swap's discount-only delta (parity).
    Mutation: drop `fwd` in toylib.swaption._value (whole-curve F) -> fails."""
    m, payer = _swaption("Pay", strike=strike)
    _, receiver = _swaption("Receive", strike=strike)
    c, exp, term, K = m.curve, payer["expiration_date"], payer["termination_date"], payer["strike"]
    sd = m.sigma * math.sqrt((exp - c.ref_date).days / 365.0)

    def pv(dz, call):
        flt, ann = _hand_legs(c, dz, exp, term)
        x = (flt / ann - K) * (1.0 if call else -1.0)
        return N * ann * (x * 0.5 * (1.0 + math.erf(x / sd / math.sqrt(2.0))) + sd * math.exp(-0.5 * (x / sd) ** 2) / math.sqrt(2 * math.pi))

    h = 0.5e-4
    for trade, call in ((payer, True), (receiver, False)):
        assert pv(0.0, call) == pytest.approx(ts.npv(m, trade), rel=1e-9)
        dd = ts.discount_delta(m, trade)
        assert dd == pytest.approx((pv(h, call) - pv(-h, call)) / (2 * h * 1e4), rel=1e-5)
        assert dd < 0  # a bought option is worth > 0: discounting it harder lowers it
    parity = ts.discount_delta(m, payer) - ts.discount_delta(m, receiver)
    assert parity == pytest.approx(tri.discount_delta(c, tr.ToySwap(exp, term, K, N)), rel=1e-6, abs=1e-6)


# ------------------------------------------------------------------------------------ swaption structure


def test_put_call_parity():
    mp, payer = _swaption("Pay", strike="ATM+30")
    _, receiver = _swaption("Receive", strike="ATM+30")
    c = mp.curve
    fwd_swap = N * tr._annuity(c, payer["expiration_date"], payer["termination_date"]) * (tr._par_rate(c, payer["expiration_date"], payer["termination_date"]) - payer["strike"])
    assert ts.npv(mp, payer) - ts.npv(mp, receiver) == pytest.approx(fwd_swap, rel=1e-9)
    assert ts.vega(mp, payer) == pytest.approx(ts.vega(mp, receiver), rel=1e-12)
    assert ts.prob_exercise(mp, payer) + ts.prob_exercise(mp, receiver) == pytest.approx(1.0, abs=1e-12)


def test_straddle_is_payer_plus_receiver():
    m, straddle = _swaption("Straddle", strike="ATM+30")
    _, payer = _swaption("Pay", strike="ATM+30")
    _, receiver = _swaption("Receive", strike="ATM+30")
    for fn in (ts.npv, ts.vega, ts.delta, ts.gamma, ts.vanna, ts.volga, ts.theta_1d, ts.discount_delta):
        assert fn(m, straddle) == pytest.approx(fn(m, payer) + fn(m, receiver), rel=1e-9, abs=1e-9), fn.__name__
    assert ts.prob_exercise(m, straddle) == pytest.approx(1.0, abs=1e-12)


def test_vega_vanna_volga_against_finite_differences():
    m, t = _swaption(strike="ATM+50")
    hs, hz = 0.5e-4, 0.5e-4  # other steps than the library's 1bp
    vega_fd = (ts.npv(_smkt(m, dsigma=hs), t) - ts.npv(_smkt(m, dsigma=-hs), t)) / (2 * hs * 1e4)
    assert ts.vega(m, t) == pytest.approx(vega_fd, rel=1e-5)
    n = lambda dz, ds: ts.npv(_smkt(m, dz, ds), t)  # noqa: E731
    dF = _fwd_bp(_bump(m.curve, hz), t) - _fwd_bp(_bump(m.curve, -hz), t)
    vanna_fd = (n(hz, hs) - n(hz, -hs) - n(-hz, hs) + n(-hz, -hs)) / (2 * hs * 1e4) / dF
    assert ts.vanna(m, t) == pytest.approx(vanna_fd, rel=1e-4)
    # closed form: d(vega)/d(sigma) = N A sqrt(T) phi(d) d^2 / sigma, per bp^2
    c, sigma = m.curve, m.sigma
    T = (t["expiration_date"] - c.ref_date).days / 365.0
    F = tr._par_rate(c, t["expiration_date"], t["termination_date"])
    d = (F - t["strike"]) / (sigma * math.sqrt(T))
    volga = N * tr._annuity(c, t["expiration_date"], t["termination_date"]) * math.sqrt(T) * math.exp(-d * d / 2) / math.sqrt(2 * math.pi) * d * d / sigma * 1e-8
    assert ts.volga(m, t) == pytest.approx(volga, rel=2e-3)


def test_levels_and_ladders_of_the_swaption():
    m, t = _swaption(strike="ATM+30")
    assert ts.fwd_rate(m, t) == pytest.approx(_fwd_bp(m.curve, t), rel=1e-12)
    assert ts.annual_vol(m, t) == ts.atm_vol(m, t) == pytest.approx(m.sigma * 1e4)
    assert ts.daily_vol(m, t) == pytest.approx(m.sigma * 1e4 / math.sqrt(252))
    assert ts.expiry_in_years(m, t) == 1.0  # 2024-03-04 -> 2025-03-04: 365 days
    assert ts.annuity(m, t) == pytest.approx(N * tr._annuity(m.curve, t["expiration_date"], t["termination_date"]))
    assert list(ts.cashflows(m, t).columns) == list(contracts.FRAME_COLUMNS["Cashflows"]) and ts.cashflows(m, t).empty
    _, t2 = _swaption("Receive", strike="ATM-20", expiration_date="3m")
    trades, w = [t, t2], [2.0, -0.5]
    for ladder_fn, fn in ((ts.delta_ladder, ts.delta), (ts.gamma_ladder, ts.gamma)):
        buckets = ladder_fn(m, trades, w, ("2Y", "5Y", "10Y", "30Y"))
        assert sum(buckets.values()) == pytest.approx(sum(x * fn(m, tt) for tt, x in zip(trades, w)), rel=1e-12)
    cube = ts.vega_cube(m, trades, w, ("1M", "3M", "6M", "1Y", "2Y"), ("1Y", "5Y", "10Y"))
    assert cube == pytest.approx({"10Y;1Y": 2.0 * ts.vega(m, t), "10Y;3M": -0.5 * ts.vega(m, t2)})


# ------------------------------------------------------------------------------------ swaption after expiry (R2-7)


def test_after_expiry_physical_settlement():
    """Exercise is decided by F at expiration_date; the strike sits strictly between F(expiry) and
    the live F, so deciding on the live forward would flip both answers."""
    _, probe = _swaption(expiration_date="2m")
    exp, term = probe["expiration_date"], probe["termination_date"]
    later = exp + timedelta(days=21)
    f_exp = tr._par_rate(tr.ToyCurve(exp, "USD", tr._zero_rate(exp, "USD")), exp, term)
    m = ts.market(later, "USD")
    f_now = tr._par_rate(m.curve, exp, term)
    assert abs(f_now - f_exp) > 2e-4
    K = (f_exp + f_now) / 2
    payer = dict(probe, strike=K, pay_or_receive="Pay")
    receiver = dict(probe, strike=K, pay_or_receive="Receive")
    exercised, dead = (payer, receiver) if f_exp > K else (receiver, payer)
    sign = 1.0 if exercised is payer else -1.0
    swap = tr.ToySwap(exp, term, K, sign * N)  # the underlying, as a toy swap
    assert ts.npv(m, exercised) == pytest.approx(tr.npv(m.curve, swap), rel=1e-12)
    assert ts.delta(m, exercised) == pytest.approx(tri.delta(m.curve, swap), rel=1e-9)
    assert ts.gamma(m, exercised) == pytest.approx(tri.ir_gamma(m.curve, swap), rel=1e-6)
    assert ts.theta_1d(m, exercised) == pytest.approx(tri.theta_1d(m.curve, swap), rel=1e-9)
    assert ts.prob_exercise(m, exercised) == 1.0 and ts.prob_exercise(m, dead) == 0.0
    for fn in (ts.npv, ts.delta, ts.gamma, ts.theta_1d, ts.discount_delta, ts.vega, ts.vanna, ts.volga, ts.annuity, ts.local_annuity_in_cents):
        assert fn(m, dead) == 0.0, fn.__name__
    assert ts.annuity(m, exercised) != 0.0  # the exercised leg is the live underlying swap
    for fn in (ts.vega, ts.vanna, ts.volga):
        assert fn(m, exercised) == 0.0, fn.__name__
    for trade in (exercised, dead):  # levels continue: the live forward, the vol at expiry
        assert ts.fwd_rate(m, trade) == pytest.approx(f_now * 1e4, rel=1e-12)
        assert ts.annual_vol(m, trade) == pytest.approx(ts._sigma(exp, "USD") * 1e4, rel=1e-12)
        assert ts.expiry_in_years(m, trade) == 0.0


def test_exercised_swaption_past_its_underlyings_end_is_dead_with_finite_levels():
    """A payer exercised into a 1y underlying, seen on and after the underlying's final date: every
    sensitivity, Annuity and each ladder is 0 and the CRIF empty (so sum(CRIF) == sum(IRDelta
    ladder) == 0); the levels stay finite and Price continuous with the day before, when it is still
    live. Mutation: drop the `_dead` guard in toylib.swaption.delta -> fails (ladder ~ -100)."""
    m0 = ts.market(D, "USD")
    o = ts.resolve_swaption(m0, dict(pay_or_receive="Pay", expiration_date="2m", termination_date="1y", notional_amount=N, strike=0.0))
    term = o["termination_date"]
    assert ts._exercised(m0.curve, o, True)  # struck at 0: in the money at expiry
    live = ts.market(term - timedelta(days=1), "USD")
    assert ts.delta(live, o) != 0.0 and ts.annuity(live, o) != 0.0
    for d in (term, term + timedelta(days=30)):
        m = ts.market(d, "USD")
        for fn in (ts.delta, ts.gamma, ts.discount_delta, ts.theta_1d, ts.vega, ts.vanna, ts.volga, ts.annuity, ts.local_annuity_in_cents):
            assert fn(m, o) == 0.0, (d, fn.__name__)
        for ladder_fn in (ts.delta_ladder, ts.gamma_ladder):
            assert set(ladder_fn(m, [o], [1.0], ("2Y", "5Y", "10Y", "30Y")).values()) == {0.0}, (d, ladder_fn.__name__)
        assert ts.vega_cube(m, [o], [1.0], ("1M", "1Y"), ("1Y", "10Y")) == {"1Y;1M": 0.0}
        frame = ts.crif_ir_curve(m, o)
        assert frame.empty and list(frame.columns) == list(contracts.FRAME_COLUMNS["CRIFIRCurve"]), d
        for fn in (ts.npv, ts.fair_premium, ts.forward_price, ts.premium_cents, ts.fwd_rate, ts.spot_rate, ts.par_spread, ts.annual_vol):
            assert math.isfinite(fn(m, o)), (d, fn.__name__)
    assert ts.npv(ts.market(term, "USD"), o) == pytest.approx(ts.npv(live, o), rel=1e-3) != 0.0


def test_expiry_in_years_for_all_three_classes():
    ms, s = _swap(term="10y")
    assert tri.expiry_in_years(ms, s) == (date(2034, 3, 4) - D).days / 365
    mo, o = _swaption(expiration_date="6m")
    assert ts.expiry_in_years(mo, o) == (date(2024, 9, 4) - D).days / 365
    mb, b = _bond("TOY 3.5 2027-05-15")
    assert tb.expiry_in_years(mb, b) == (date(2027, 5, 15) - D).days / 365
    dead_b = tb.market(date(2027, 6, 1), "USD")
    assert tb.expiry_in_years(dead_b, b) == 0.0


# ------------------------------------------------------------------------------------ the swap


def test_swap_levels_and_ladders():
    m, s = _swap(fixed_rate=0.035)
    assert tri.annuity(m, s) == pytest.approx(1e4 * tr.pv01(m, s), rel=1e-12)
    assert tri.spot_rate(m, s) == pytest.approx(tr.par_rate(m, s), rel=1e-12)  # spot-starting 10y swap: same swap
    _, fwd_start = _swap(term="5y")
    assert tri.spot_rate(m, fwd_start) == pytest.approx(tr.par_rate(m, fwd_start), rel=1e-12)
    assert list(tri.empty_cashflows().columns) == list(contracts.FRAME_COLUMNS["Cashflows"])
    _, s2 = _swap(term="2y", pay_or_receive="Receive")
    trades, w = [s, s2], [1.5, 3.0]
    for ladder_fn, fn in ((tri.delta_ladder, tri.delta), (tri.gamma_ladder, tri.ir_gamma)):
        buckets = ladder_fn(m, trades, w, ("2Y", "5Y", "10Y", "30Y"))
        assert sum(buckets.values()) == pytest.approx(sum(x * fn(m, t) for t, x in zip(trades, w)), rel=1e-12)
        assert buckets["2Y"] == pytest.approx(3.0 * fn(m, s2), rel=1e-12)


def test_swap_after_its_final_date_is_dead_with_finite_levels():
    """IR_RISK_DESIGN section 2.2: every sensitivity 0.0 on and after the final date (a 1y swap
    traded 2023-03-01), levels finite. The total-return toy Price itself (toylib.rates.npv) does
    not go to 0: it never drops a paid flow (R2-6)."""
    _, s = _swap(term="1y", d=date(2023, 3, 1))
    for d in (s.termination_date, date(2024, 6, 3)):
        m = tr.market(d, "USD")
        for fn in (tri.delta, tri.ir_gamma, tr.gamma, tri.discount_delta, tri.theta_1d, tri.annuity):
            assert fn(m, s) == 0.0, (d, fn.__name__)  # tr.gamma: toy_usd_irs's IRGammaParallel = its IRGamma ladder sum
        for ladder_fn in (tri.delta_ladder, tri.gamma_ladder):
            assert set(ladder_fn(m, [s], [1.0], ("2Y", "5Y")).values()) == {0.0}
        assert math.isfinite(tri.spot_rate(m, s)) and tri.expiry_in_years(m, s) == 0.0
    live = tr.market(s.termination_date - timedelta(days=1), "USD")
    assert tri.delta(live, s) != 0.0 and tri.annuity(live, s) != 0.0 and tr.gamma(live, s) != 0.0


# ------------------------------------------------------------------------------------ the bond


def test_bond_schedule_price_and_yield_round_trip():
    """BOND_DESIGN 4.5: Price is the settlement-date (T+1, s = Tue 2024-03-05) value, and each
    Cashflows payment_date is the trade date Price drops the flow: the last weekday before it."""
    m, b = _bond("TOY 3.5 2027-05-15")
    s = date(2024, 3, 5)  # D is a Monday
    cf = tb.cashflows(m, b)
    pays = [date(2024, 5, 15), date(2024, 11, 15), date(2025, 5, 15), date(2025, 11, 15), date(2026, 5, 15), date(2026, 11, 15), date(2027, 5, 15)]
    # Wed, Fri, Thu, Sat, Fri, Sun, Sat -> the weekday before each
    drops = [date(2024, 5, 14), date(2024, 11, 14), date(2025, 5, 14), date(2025, 11, 14), date(2026, 5, 14), date(2026, 11, 13), date(2027, 5, 14)]
    assert list(cf["payment_date"]) == drops + [date(2027, 5, 14)]
    assert list(cf["payment_amount"]) == [N * 0.0175] * 7 + [N]
    assert list(cf["payment_type"]) == ["Coupon"] * 7 + ["Principal"]
    y = tb.yield_bp(m, b) / 1e4
    assert y == pytest.approx(m.curve.zero_rate + m.spread, abs=1e-13)  # flat world: y = z + s
    taus = [(p - s).days / 365 for p in pays + [date(2027, 5, 15)]]
    assert tb.npv(m, b) == pytest.approx(sum(a * math.exp(-y * tau) for a, tau in zip(cf["payment_amount"], taus)), rel=1e-12)
    accrued = N * 0.0175 * (s - date(2023, 11, 15)).days / (date(2024, 5, 15) - date(2023, 11, 15)).days  # at settlement
    assert tb.accrued(m, b) == pytest.approx(accrued, rel=1e-12)
    assert tb.clean_price(m, b) == pytest.approx(tb.dirty_price(m, b) - 100 * accrued / N, rel=1e-12)
    ann = sum(0.5 * math.exp(-y * tau) for tau in taus[:7]) * N
    assert tb.annuity(m, b) == pytest.approx(ann, rel=1e-12)
    assert tb.spread_bp(m, b) == pytest.approx(m.spread * 1e4)
    _, short = _bond("TOY 3.5 2027-05-15", size=-N)
    assert tb.npv(m, short) == pytest.approx(-tb.npv(m, b), rel=1e-12)
    with pytest.raises(ValueError, match="unknown toy bond identifier"):
        tb.resolve_bond(m, dict(identifier="TOY 9 2099-01-01", size=N))


def test_bond_dv01_and_ladders():
    m, b = _bond("TOY 4.5 2054-02-15")
    y, s = tb.yield_bp(m, b) / 1e4, date(2024, 3, 5)  # settlement of Monday D
    flows = [(date(yr, mo, 15), N * 0.0225) for yr in range(2024, 2055) for mo in (2, 8) if s < date(yr, mo, 15) <= date(2054, 2, 15)]
    flows.append((date(2054, 2, 15), N))
    pv = lambda yy: sum(a * math.exp(-yy * (p - s).days / 365) for p, a in flows)  # noqa: E731
    assert pv(y) == pytest.approx(tb.npv(m, b), rel=1e-12)  # the hand schedule is the toy's
    fd = (pv(y + 0.5e-4) - pv(y - 0.5e-4)) / 1.0
    assert tb.yield_dv01(m, b) == pytest.approx(fd, rel=1e-6)
    assert tb.delta(m, b) == pytest.approx(fd, rel=1e-5) and tb.delta(m, b) < 0  # a long bond
    assert tb.gamma(m, b) == pytest.approx((pv(y + 1e-4) + pv(y - 1e-4) - 2 * pv(y)), rel=1e-5) and tb.gamma(m, b) > 0
    assert tb.discount_delta(m, b) == pytest.approx(tb.delta(m, b), rel=1e-6)
    _, b2 = _bond("TOY 3.5 2027-05-15")
    trades, w = [b, b2], [1.0, -2.0]
    for ladder_fn, fn in ((tb.delta_ladder, tb.delta), (tb.gamma_ladder, tb.gamma)):
        buckets = ladder_fn(m, trades, w, ("2Y", "5Y", "10Y", "30Y"))
        assert sum(buckets.values()) == pytest.approx(sum(x * fn(m, t) for t, x in zip(trades, w)), rel=1e-9)
        assert all(v != 0.0 for v in buckets.values())  # a 30y coupon bond touches every pillar


def test_bond_after_maturity_is_dead_with_finite_levels():
    """Dead once settlement reaches maturity (Sat 2027-05-15): from Friday 2027-05-14 on."""
    _, b = _bond("TOY 3.5 2027-05-15")
    assert tb.npv(tb.market(date(2027, 5, 13), "USD"), b) != 0.0  # Thursday: settles Friday, still live
    for d in (date(2027, 5, 14), date(2027, 5, 17)):
        m = tb.market(d, "USD")
        assert tb.npv(m, b) == 0.0 and tb.cashflows(m, b).empty and tb.annuity(m, b) == 0.0, d
        assert tb.delta(m, b) == 0.0 and tb.gamma(m, b) == 0.0 and tb.theta_1d(m, b) == 0.0, d
        assert tb.yield_bp(m, b) == pytest.approx((m.curve.zero_rate + m.spread) * 1e4, abs=1e-9), d


# ------------------------------------------------------------------------------------ Theta (R2-4): the frozen-world identity


def test_frozen_world_theta_identity_swap_and_swaption(frozen):
    t0, t1 = D, D + timedelta(days=1)
    s = _swap(fixed_rate=0.025)[1]
    assert tri.theta_1d(tr.market(t0, "USD"), s) == pytest.approx(tr.npv(tr.market(t1, "USD"), s) - tr.npv(tr.market(t0, "USD"), s), rel=1e-9)
    for strike in ("ATM", "ATM+40"):
        _, o = _swaption("Straddle", strike=strike)
        m0, m1 = ts.market(t0, "USD"), ts.market(t1, "USD")
        assert ts.theta_1d(m0, o) == pytest.approx(ts.npv(m1, o) - ts.npv(m0, o), rel=1e-9)
    _, o = _swaption("Pay", strike=0.02, expiration_date="1m")  # exercised: past expiry it is the swap
    m0 = ts.market(o["expiration_date"] + timedelta(days=3), "USD")
    m1 = ts.market(m0.curve.ref_date + timedelta(days=1), "USD")
    assert ts.npv(m0, o) != 0.0
    assert ts.theta_1d(m0, o) == pytest.approx(ts.npv(m1, o) - ts.npv(m0, o), rel=1e-9)


def test_swap_theta_translates_the_curve_and_never_rolls_it():
    """Not frozen, so tomorrow's toy market (a roll) differs from today's curve translated one day
    (R2-4): the toy npv is linear in DFs, so the translation gives npv * (1/DF(t+1) - 1)."""
    m, s = _swap(fixed_rate=0.025)
    t1 = D + timedelta(days=1)
    theta = tri.theta_1d(m, s)
    assert theta == pytest.approx(tr.npv(m, s) * (1 / m.discount_factor(t1) - 1), rel=1e-9)
    assert abs(theta - (tr.npv(tr.market(t1, "USD"), s) - tr.npv(m, s))) > 1.0
    assert tri._TranslatedCurve(tr.market(D, "USD", "CSA-X"), 1).csa == "CSA-X"


def test_swaption_theta_onto_expiry_exercises_on_the_frozen_forward():
    """R2-4 holds F fixed, so the step onto expiry pays intrinsic on today's F, even where the toy
    world's own expiry F sits on the other side of the strike (the post-expiry decision is kept)."""
    _, o = _swaption()
    exp, term = o["expiration_date"], o["termination_date"]
    t = exp - timedelta(days=1)
    m = ts.market(t, "USD")
    f_live, f_exp = tr._par_rate(m.curve, exp, term), tr._par_rate(tr.market(exp, "USD"), exp, term)
    assert abs(f_live - f_exp) > 1e-5
    k = (f_live + f_exp) / 2
    ann_t1 = tr._annuity(m.curve, exp, term) / m.curve.discount_factor(exp)  # the curve translated onto expiry
    for pay_or_receive, intrinsic in (("Pay", max(f_live - k, 0.0)), ("Receive", max(k - f_live, 0.0))):
        trade = dict(o, pay_or_receive=pay_or_receive, strike=k)
        expected = trade["notional"] * ann_t1 * intrinsic - ts.npv(m, trade)
        assert ts.theta_1d(m, trade) == pytest.approx(expected, rel=1e-9)


def test_frozen_world_theta_identity_bond_including_a_coupon_step(frozen):
    """Bond Theta x step days = Price(next weekday) + dropped flows - Price(t) when nothing moves.
    The 2024-05-15 (Wed) coupon drops on Tue 05-14, so the Mon -> Tue step carries it as cash."""
    _, b = _bond("TOY 4.25 2034-11-15")
    for t, nb in ((D, D + timedelta(days=1)), (date(2024, 5, 13), date(2024, 5, 14)), (date(2024, 5, 10), date(2024, 5, 13))):
        m0, m1 = tb.market(t, "USD"), tb.market(nb, "USD")
        cash = N * 0.0425 / 2 if nb == date(2024, 5, 14) else 0.0
        days = (nb - t).days  # 3 over the weekend
        assert tb.theta_1d(m0, b) * days == pytest.approx(tb.npv(m1, b) + cash - tb.npv(m0, b), rel=1e-9), t
    m0 = tb.market(date(2024, 5, 13), "USD")
    assert tb.npv(tb.market(date(2024, 5, 14), "USD"), b) < tb.npv(m0, b)  # the settlement value drops the coupon


def test_frozen_world_pnl_explain_and_override_price_have_no_time_component(frozen):
    """PnlExplain (IR_RISK_DESIGN section 8) is market moves only, and so is npv under a
    CloseMarket override (DEV-M1): valued on the pricing date F. With every level frozen the
    target market moves nothing, so every row is 0 and npv on the target's market is npv on F's --
    across the bond's 2024-05-15 coupon too (valued on the target's own date, the coupon would
    drop and the discounting would carry)."""
    f, t = date(2024, 5, 13), date(2024, 5, 16)  # the bond's coupon drops on Tue 05-14 (T+1 settlement)
    _, s = _swap(fixed_rate=0.02, term="5y", d=f)
    _, o = _swaption(d=f)
    _, b = _bond("TOY 4.25 2034-11-15", d=f)
    for mod, market, trade in ((tri, tr.market, s), (ts, ts.market, o), (tb, tb.market, b)):
        m0, m1 = market(f, "USD"), market(t, "USD")
        rows = mod.pnl_explain(m0, m1, [trade], [1.0], f)
        assert all(r["value"] == pytest.approx(0.0, abs=1e-9 * N) for r in rows), (mod.__name__, rows)
        assert mod.npv(m1, trade, f) == pytest.approx(mod.npv(m0, trade, f), abs=1e-9 * N), mod.__name__


# ------------------------------------------------------------------------------------ one-step Taylor (R2-18 spirit)


@pytest.mark.parametrize("pay_or_receive, strike", [("Pay", "ATM"), ("Receive", "ATM+50"), ("Straddle", "ATM-50")])
def test_swaption_one_step_taylor_residual(pay_or_receive, strike):
    m0, o = _swaption(pay_or_receive, strike=strike)
    m1 = ts.market(D + timedelta(days=1), "USD")
    dpv = ts.npv(m1, o) - ts.npv(m0, o)
    dF = ts.fwd_rate(m1, o) - ts.fwd_rate(m0, o)
    ds = ts.annual_vol(m1, o) - ts.annual_vol(m0, o)
    assert abs(dF) > 0.5 and abs(ds) > 0.1  # a real move
    delta_only = dpv - ts.delta(m0, o) * dF
    explained = (ts.delta(m0, o) * dF + 0.5 * ts.gamma(m0, o) * dF ** 2 + ts.vega(m0, o) * ds + ts.vanna(m0, o) * dF * ds
                 + 0.5 * ts.volga(m0, o) * ds ** 2 + ts.theta_1d(m0, o) * 1)
    assert abs(dpv - explained) * 10 <= abs(delta_only)


# ------------------------------------------------------------------------------------ chain-rule toylib.rates.gamma (MERGE_NOTES section 4)


def test_rates_gamma_is_the_chain_rule_second_derivative():
    """tr.gamma (toy_usd_irs / toy_eur_irs IRGammaParallel) is the contract's chain-rule gamma: the
    MERGE_NOTES section 4 number (-0.817 per bp^2, the uncorrected recipe gave -0.738), the
    derivative of the own-rate delta in the par rate, and toylib.irrisk.ir_gamma's value."""
    m, s = _swap(d=date(2024, 1, 3))
    assert tr.gamma(m, s) == pytest.approx(-0.8166, abs=1e-4)
    for fixed_rate, term, pay_or_receive in (("ATM", "10y", "Pay"), (0.02, "2y", "Receive"), (0.05, "30y", "Pay")):
        m, s = _swap(fixed_rate=fixed_rate, term=term, pay_or_receive=pay_or_receive)
        h = 0.5e-4  # another step than the 1bp inside tr.gamma
        dd = (tri.delta(_bump(m, h), s) - tri.delta(_bump(m, -h), s)) / (tr.par_rate(_bump(m, h), s) - tr.par_rate(_bump(m, -h), s))
        assert tr.gamma(m, s) == pytest.approx(dd, rel=1e-4), (fixed_rate, term)
        assert tr.gamma(m, s) == pytest.approx(tri.ir_gamma(m, s), rel=1e-12), (fixed_rate, term)
    m, s = _swap()
    dpv01 = (tr.pv01(_bump(m, H), s) - tr.pv01(_bump(m, -H), s)) / (tr.par_rate(_bump(m, H), s) - tr.par_rate(_bump(m, -H), s))
    assert dpv01 / tr.gamma(m, s) == pytest.approx(0.5, abs=1e-6)  # T-GAMMA-2's half-gamma ratio, exact at the money


# ------------------------------------------------------------------------------------ R3-1 measures (IR_STRICT_CONTRACT.md, DEV-I19)


R3_SIGNED = ("fair_premium", "forward_price", "premium_cents", "local_annuity_in_cents")
R3_DIRECTIONLESS = ("par_spread", "compounded_fixed_rate")


def _df(curve, d):
    """The toy's flat continuously-compounded DF, written out (ACT/365)."""
    return math.exp(-curve.zero_rate * (d - curve.ref_date).days / 365.0)


def _hand_par_and_annuity(curve, eff, mat):
    """Par rate and annuity of annual coupons eff -> mat, the schedule spelt out (whole years)."""
    pays = [date(eff.year + k, eff.month, eff.day) for k in range(1, mat.year - eff.year + 1)]
    ann = sum(_df(curve, p) * (p - q).days / 365.0 for q, p in zip([eff] + pays[:-1], pays))
    return (_df(curve, eff) - _df(curve, mat)) / ann, ann


def _crif_ok(frame, ccy):
    assert list(frame.columns) == list(contracts.FRAME_COLUMNS["CRIFIRCurve"])
    assert list(frame["Label1"]) == list(contracts.SIMM_IR_TENORS)
    assert set(frame["RiskType"]) == {"Risk_IRCurve"} and set(frame["Bucket"]) == {"1"} and set(frame["Label2"]) == {"OIS"}
    assert set(frame["Qualifier"]) == {ccy} == set(frame["AmountCurrency"])


@pytest.mark.parametrize("pay_or_receive", ["Pay", "Receive"])
@pytest.mark.parametrize("fixed_rate", [0.02, 0.045])
def test_swap_r3_measures_known_answers(pay_or_receive, fixed_rate):
    m, s = _swap(fixed_rate=fixed_rate, pay_or_receive=pay_or_receive)
    par, ann = _hand_par_and_annuity(m, s.effective_date, s.termination_date)
    sign = 1.0 if pay_or_receive == "Pay" else -1.0
    price = sign * N * (_df(m, s.effective_date) - _df(m, s.termination_date) - fixed_rate * ann)
    assert tr.npv(m, s) == pytest.approx(price, rel=1e-12)  # the hand schedule is the toy's
    assert tri.par_spread(m, s) == pytest.approx((fixed_rate - par) * 1e4, rel=1e-10)  # K - par
    assert tri.fair_premium(m, s) == pytest.approx(price, rel=1e-12)  # no spot lag: Price
    assert tri.forward_price(m, s) * _df(m, s.termination_date) == pytest.approx(price, rel=1e-12)
    assert tri.premium_cents(m, s) == pytest.approx(price / N * 1e4, rel=1e-12)
    assert tri.local_annuity_in_cents(m, s) == pytest.approx(sign * ann, rel=1e-12)
    assert 7.5 < sign * tri.local_annuity_in_cents(m, s) < 9.0  # a 10y annuity per unit notional
    assert tri.compounded_fixed_rate(m, s) == pytest.approx(fixed_rate * 1e4, rel=1e-14)  # annual leg: K


def test_swap_r3_payer_receiver_symmetry():
    m, payer = _swap(fixed_rate=0.03, pay_or_receive="Pay")
    _, receiver = _swap(fixed_rate=0.03, pay_or_receive="Receive")
    for name in R3_DIRECTIONLESS:
        fn = getattr(tri, name)
        assert fn(m, receiver) == fn(m, payer) != 0.0, name
    for name in R3_SIGNED:
        fn = getattr(tri, name)
        assert fn(m, receiver) == pytest.approx(-fn(m, payer), rel=1e-12) and fn(m, payer) != 0.0, name
    for crif in (tri.crif_ir_curve, tri.crif_ir_curve_pv01):
        assert list(crif(m, receiver)["Amount"]) == pytest.approx([-a for a in crif(m, payer)["Amount"]], rel=1e-12)


def test_swap_crif_rows_and_sum_identity():
    """Each CRIF is built from the ladder its configs map as IRDelta bucketed: sum(Amount) is that
    ladder's sum, on the config's own pillars. Off the money the two ladders differ (R2-1)."""
    m, s = _swap(fixed_rate=0.02)
    _, s2 = _swap(term="2y", pay_or_receive="Receive")
    pillars = ("2Y", "5Y", "10Y", "30Y")
    for crif, ladder in ((tri.crif_ir_curve, tri.delta_ladder), (tri.crif_ir_curve_pv01, tr.delta_ladder)):
        for trade in (s, s2):
            frame = crif(m, trade)
            _crif_ok(frame, "USD")
            assert frame["Amount"].sum() == pytest.approx(sum(ladder(m, [trade], [1.0], pillars).values()), rel=1e-12), crif.__name__
    own, pv01 = tri.crif_ir_curve(m, s).set_index("Label1")["Amount"], tri.crif_ir_curve_pv01(m, s).set_index("Label1")["Amount"]
    assert own["10y"] == pytest.approx(tri.delta(m, s), rel=1e-12) and pv01["10y"] == pytest.approx(tr.pv01(m, s), rel=1e-12)
    assert abs(own["10y"] / pv01["10y"] - 1) > 1e-2 and (own.drop("10y") == 0.0).all()
    me = tr.market(D, "EUR")
    _crif_ok(tri.crif_ir_curve(me, s), "EUR")


def test_swap_r3_measures_on_and_after_the_final_date():
    """Dead (R2-7): the own-rate CRIF is an empty frame with its columns; the pv01 CRIF follows the
    never-dead total-return pv01 ladder, so sum(CRIF) == sum(IRDelta ladder) still holds for
    toy_usd_irs / toy_eur_irs (mutation: empty it once dead -> fails); ForwardPrice is Price; the
    levels and trade terms stay finite."""
    _, s = _swap(term="1y", d=date(2023, 3, 1))
    for d in (s.termination_date, date(2024, 6, 3)):
        m = tr.market(d, "USD")
        frame = tri.crif_ir_curve(m, s)
        assert frame.empty and list(frame.columns) == list(contracts.FRAME_COLUMNS["CRIFIRCurve"]), d
        frame = tri.crif_ir_curve_pv01(m, s)
        _crif_ok(frame, "USD")
        ladder = sum(tr.delta_ladder(m, [s], [1.0], ("2Y", "5Y", "10Y", "30Y")).values())
        assert frame["Amount"].sum() == pytest.approx(ladder, rel=1e-12) and ladder == pytest.approx(tr.pv01(m, s), rel=1e-12) != 0.0, d
        assert tri.forward_price(m, s) == tr.npv(m, s) != 0.0
        assert tri.local_annuity_in_cents(m, s) == 0.0  # Annuity / |N|, and Annuity is dead
        assert math.isfinite(tri.par_spread(m, s)) and tri.compounded_fixed_rate(m, s) == pytest.approx(s.fixed_rate * 1e4)
    live = tr.market(s.termination_date - timedelta(days=1), "USD")
    assert len(tri.crif_ir_curve(live, s)) == len(contracts.SIMM_IR_TENORS)


@pytest.mark.parametrize("pay_or_receive", ["Pay", "Receive", "Straddle"])
def test_swaption_r3_measures_known_answers(pay_or_receive):
    m, o = _swaption(pay_or_receive, strike="ATM+30")
    c, exp, K = m.curve, o["expiration_date"], o["strike"]
    par, ann = _hand_par_and_annuity(c, exp, o["termination_date"])
    price = ts.npv(m, o)
    assert ts.par_spread(m, o) == pytest.approx((K - par) * 1e4, rel=1e-9)
    assert ts.par_spread(m, o) == pytest.approx(30.0, rel=1e-9)  # struck ATM+30 on this market
    assert ts.fair_premium(m, o) == pytest.approx(price, rel=1e-12)
    assert ts.forward_price(m, o) * _df(c, exp) == pytest.approx(price, rel=1e-12)  # to expiration_date
    assert ts.premium_cents(m, o) == pytest.approx(price / N * 1e4, rel=1e-12)
    assert ts.local_annuity_in_cents(m, o) == pytest.approx(ann, rel=1e-12)  # bought: + for any leg
    assert ts.compounded_fixed_rate(m, o) == pytest.approx(K * 1e4, rel=1e-14)
    frame = ts.crif_ir_curve(m, o)
    _crif_ok(frame, "USD")
    assert frame["Amount"].sum() == pytest.approx(sum(ts.delta_ladder(m, [o], [1.0], ("2Y", "5Y", "10Y", "30Y")).values()), rel=1e-12)
    assert frame.set_index("Label1")["Amount"]["10y"] == pytest.approx(ts.delta(m, o), rel=1e-12)


def test_swaption_r3_parity_and_buy_sell_symmetry():
    m, payer = _swaption("Pay", strike="ATM+30")
    _, receiver = _swaption("Receive", strike="ATM+30")
    c, exp, K = m.curve, payer["expiration_date"], payer["strike"]
    par, ann = _hand_par_and_annuity(c, exp, payer["termination_date"])
    # put-call parity forward-valued to expiry: N A (F - K) / DF(expiry)
    assert ts.forward_price(m, payer) - ts.forward_price(m, receiver) == pytest.approx(N * ann * (par - K) / _df(c, exp), rel=1e-9)
    for name in R3_DIRECTIONLESS:
        assert getattr(ts, name)(m, payer) == getattr(ts, name)(m, receiver), name
    sold = ts.resolve_swaption(m, dict(pay_or_receive="Pay", buy_sell="Sell", expiration_date="1y", termination_date="10y", notional_amount=N, strike=K))
    for name in R3_DIRECTIONLESS:
        assert getattr(ts, name)(m, sold) == getattr(ts, name)(m, payer), name
    for name in R3_SIGNED:
        fn = getattr(ts, name)
        assert fn(m, sold) == pytest.approx(-fn(m, payer), rel=1e-12) and fn(m, payer) != 0.0, name
    assert list(ts.crif_ir_curve(m, sold)["Amount"]) == pytest.approx([-a for a in ts.crif_ir_curve(m, payer)["Amount"]], rel=1e-12)


def test_swaption_r3_measures_after_expiry():
    """Physical settlement (R2-7): the exercised leg's ForwardPrice is its Price and its CRIF the
    underlying swap's delta; the unexercised leg is dead (empty CRIF); levels continue."""
    _, probe = _swaption(expiration_date="2m")
    exp, term = probe["expiration_date"], probe["termination_date"]
    later = exp + timedelta(days=21)
    f_exp = tr._par_rate(tr.ToyCurve(exp, "USD", tr._zero_rate(exp, "USD")), exp, term)
    m = ts.market(later, "USD")
    f_now = tr._par_rate(m.curve, exp, term)
    K = (f_exp + f_now) / 2
    payer, receiver = dict(probe, strike=K, pay_or_receive="Pay"), dict(probe, strike=K, pay_or_receive="Receive")
    exercised, dead = (payer, receiver) if f_exp > K else (receiver, payer)
    swap = tr.ToySwap(exp, term, K, (1.0 if exercised is payer else -1.0) * N)
    assert ts.forward_price(m, exercised) == ts.npv(m, exercised) != 0.0
    assert ts.premium_cents(m, exercised) == pytest.approx(tr.npv(m.curve, swap) / N * 1e4, rel=1e-12)
    assert ts.crif_ir_curve(m, exercised)["Amount"].sum() == pytest.approx(tri.delta(m.curve, swap), rel=1e-9)
    frame = ts.crif_ir_curve(m, dead)
    assert frame.empty and list(frame.columns) == list(contracts.FRAME_COLUMNS["CRIFIRCurve"])
    assert ts.forward_price(m, dead) == 0.0 and ts.premium_cents(m, dead) == 0.0
    for trade in (exercised, dead):
        assert ts.par_spread(m, trade) == pytest.approx((K - f_now) * 1e4, rel=1e-9)
        assert ts.compounded_fixed_rate(m, trade) == pytest.approx(K * 1e4, rel=1e-14)


# ------------------------------------------------------------------------------------ R3-1 through pricebt: scaling and identities


_R3_EXTENSIVE = (risk.Price, risk.FairPremium, risk.ForwardPrice)
_R3_INTENSIVE = (risk.ParSpread, risk.PremiumCents, risk.LocalAnnuityInCents, risk.CompoundedFixedRate)


def _calc(inst, measures, market=None):
    with PricingContext(pricing_date=D, market=market):
        futures = {m: inst.calc(m) for m in measures}
    return {m: f.result() for m, f in futures.items()}


@pytest.mark.parametrize("fname", STRICT_CONFIGS)
def test_r3_measures_scale_with_quantity_as_declared(fname):
    """quantity 2.5: Price, FairPremium, ForwardPrice and the CRIF Amount scale, the levels per unit
    notional and the trade terms do not (each config's unit / scale_with_quantity declarations)."""
    PricebtSession.use(assets=[ASSETS / fname])
    inst = _INSTS[fname]
    measures = _R3_EXTENSIVE + _R3_INTENSIVE + (risk.CRIFIRCurve,)
    one, big = _calc(inst, measures), _calc(inst.clone(quantity_=2.5), measures)
    for m in _R3_EXTENSIVE:
        assert float(big[m]) == pytest.approx(2.5 * float(one[m]), rel=1e-12) and float(one[m]) != 0.0, m
    for m in _R3_INTENSIVE:
        assert float(big[m]) == pytest.approx(float(one[m]), rel=1e-12) and float(one[m]) != 0.0, m
    a, b = one[risk.CRIFIRCurve], big[risk.CRIFIRCurve]
    assert list(b["Amount"]) == pytest.approx([2.5 * x for x in a["Amount"]], rel=1e-12) and a["Amount"].abs().sum() > 0
    assert list(b["Label1"]) == list(a["Label1"]) == list(contracts.SIMM_IR_TENORS)


@pytest.mark.parametrize("fname", STRICT_CONFIGS)
@pytest.mark.parametrize("override", [None, D + timedelta(days=7)], ids=["own", "close_override"])
def test_r3_identities_hold_on_each_config_including_a_close_market_override(fname, override):
    """FairPremium == Price and PremiumCents == Price / |N| * 1e4 as each config prices them, also
    under a CloseMarket override (both must value on the same date as its Price, DEV-M1)."""
    PricebtSession.use(assets=[ASSETS / fname])
    v = _calc(_INSTS[fname], (risk.Price, risk.FairPremium, risk.PremiumCents), CloseMarket(date=override) if override else None)
    price = float(v[risk.Price])
    assert float(v[risk.FairPremium]) == pytest.approx(price, rel=1e-12)
    assert float(v[risk.PremiumCents]) == pytest.approx(price / N * 1e4, rel=1e-12)


@pytest.mark.parametrize("fname, ccy", [("toy_usd_irs.yaml", "USD"), ("toy_eur_irs.yaml", "EUR")])
def test_pv01_configs_crif_sums_to_the_delta_ladder_on_a_dead_date(fname, ccy):
    """toy_usd_irs / toy_eur_irs (IRDelta = the never-dead annuity pv01): on a date past the
    swap's final date sum(CRIFIRCurve Amount) == sum(IRDelta ladder) != 0, as pricebt prices them."""
    session = PricebtSession.use(assets=[ASSETS / fname])
    inst = IRSwap("Pay", date(2024, 3, 1), ccy, N, effective_date=date(2023, 3, 1), fixed_rate=0.03, name="dead")
    ladder, crif = (session.pricing.value(inst, D, m, None) for m in (risk.IRDelta, risk.CRIFIRCurve))
    ladder, crif = (v.result() if hasattr(v, "result") else v for v in (ladder, crif))
    assert crif["Amount"].sum() == pytest.approx(ladder["value"].sum(), rel=1e-12) and crif["Amount"].sum() != 0.0


def test_toylib_crif_constants_match_the_contract():
    """toylib never imports pricebt (it stands in for an external library); its CRIF constants are copies."""
    from pricebt.risk import contracts
    from toylib import irrisk
    assert irrisk.CRIF_COLUMNS == contracts.FRAME_COLUMNS["CRIFIRCurve"]
    assert irrisk.SIMM_IR_TENORS == contracts.SIMM_IR_TENORS
