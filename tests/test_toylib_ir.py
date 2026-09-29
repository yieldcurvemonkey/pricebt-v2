"""Known-answer tests for the toy IR measure-contract libraries and configs (docs/v2/IR_RISK_DESIGN.md
section 6.5, section 00 R2-1..R2-8): toylib.irrisk (swap), toylib.swaption, toylib.bond and the
three full-contract configs. Every expected value is computed here independently (finite
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
from pricebt.risk import contracts
from pricebt.session import PricebtSession

ASSETS = Path(__file__).parent / "assets"
CONFIGS = {"IRSwap": "toy_usd_irs_full.yaml", "IRSwaption": "toy_usd_swaption.yaml", "Bond": "toy_usd_bond.yaml"}
D = date(2024, 3, 4)  # a Monday
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
    return m, tb.resolve_bond(m, dict(identifier=identifier, size=size, buy_sell="Buy"))


def _smkt(m, dz=0.0, dsigma=0.0):
    return SimpleNamespace(curve=_bump(m.curve, dz), sigma=m.sigma + dsigma)


def _bmkt(m, dz=0.0):
    return SimpleNamespace(curve=_bump(m.curve, dz), spread=m.spread)


def _fwd_bp(curve, trade):
    return tr._par_rate(curve, trade["expiration_date"], trade["termination_date"]) * 1e4


@pytest.fixture
def frozen(monkeypatch):
    """Every toy level constant in time: the day-over-day change is pure carry."""
    monkeypatch.setitem(tr._CCY_PARAMS, "USD", (0.03, 0.0, 252))
    monkeypatch.setitem(ts._SIGMA_PARAMS, "USD", (0.008, 0.0, 252))
    monkeypatch.setitem(tb._SPREAD_PARAMS, "USD", (0.003, 0.0, 200))


# ------------------------------------------------------------------------------------ configs


@pytest.mark.parametrize("instrument, fname", CONFIGS.items())
def test_config_maps_the_whole_contract_without_declarations_or_warnings(instrument, fname):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        cfg = load_asset(ASSETS / fname)
    assert cfg.instrument == instrument
    assert cfg.unsupported_measures == {}


def _contract_requests(instrument):
    """Every contract row as a request: FD measures in their scalar (Type) and bucketed forms."""
    for req in contracts.contract_for(instrument):
        m = getattr(risk, req.measure)
        if isinstance(m, risk.RiskMeasureWithFiniteDifferenceParameter):
            yield from ([m(aggregation_level="Type")] if "scalar" in req.forms else []) + ([m] if "bucketed" in req.forms else [])
        else:
            yield m


@pytest.mark.parametrize("inst", [
    IRSwap("Pay", "10y", "USD", N, fixed_rate=0.035, name="s"),
    IRSwaption("Straddle", "10y", "USD", notional_amount=N, expiration_date="1y", name="o"),
    Bond(identifier="TOY 4.5 2054-02-15", size=N, name="b"),
], ids=["swap", "swaption", "bond"])
def test_every_contract_measure_prices_through_pricebt(inst):
    session = PricebtSession.use(assets=[ASSETS / CONFIGS[type(inst).__name__]])
    for m in _contract_requests(type(inst).__name__):
        v = session.pricing.value(inst, D, m, None)
        v = v.result() if hasattr(v, "result") else v
        if isinstance(v, pd.DataFrame):
            assert "value" in v.columns or set(contracts.FRAME_COLUMNS["Cashflows"]) <= set(v.columns), m
        else:
            assert math.isfinite(float(v)), m


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
    assert tri.discount_delta(m, s) == pytest.approx((tr.npv(_bump(m, H), s) - tr.npv(_bump(m, -H), s)) / 2, rel=1e-12)


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
    for fn in (ts.npv, ts.delta, ts.gamma, ts.theta_1d, ts.discount_delta, ts.vega, ts.vanna, ts.volga):
        assert fn(m, dead) == 0.0, fn.__name__
    for fn in (ts.vega, ts.vanna, ts.volga):
        assert fn(m, exercised) == 0.0, fn.__name__
    for trade in (exercised, dead):  # levels continue: the live forward, the vol at expiry
        assert ts.fwd_rate(m, trade) == pytest.approx(f_now * 1e4, rel=1e-12)
        assert ts.annual_vol(m, trade) == pytest.approx(ts._sigma(exp, "USD") * 1e4, rel=1e-12)
        assert ts.expiry_in_years(m, trade) == 0.0


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


# ------------------------------------------------------------------------------------ the bond


def test_bond_schedule_price_and_yield_round_trip():
    m, b = _bond("TOY 3.5 2027-05-15")
    cf = tb.cashflows(m, b)
    pays = [date(2024, 5, 15), date(2024, 11, 15), date(2025, 5, 15), date(2025, 11, 15), date(2026, 5, 15), date(2026, 11, 15), date(2027, 5, 15)]
    assert list(cf["payment_date"]) == pays + [date(2027, 5, 15)]
    assert list(cf["payment_amount"]) == [N * 0.0175] * 7 + [N]
    assert list(cf["payment_type"]) == ["Coupon"] * 7 + ["Principal"]
    y = tb.yield_bp(m, b) / 1e4
    assert y == pytest.approx(m.curve.zero_rate + m.spread, abs=1e-13)  # flat world: y = z + s
    taus = [(p - D).days / 365 for p in cf["payment_date"]]
    assert tb.npv(m, b) == pytest.approx(sum(a * math.exp(-y * tau) for a, tau in zip(cf["payment_amount"], taus)), rel=1e-12)
    accrued = N * 0.0175 * (D - date(2023, 11, 15)).days / (date(2024, 5, 15) - date(2023, 11, 15)).days
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
    y, cf = tb.yield_bp(m, b) / 1e4, tb.cashflows(m, b)
    pv = lambda yy: sum(a * math.exp(-yy * (p - D).days / 365) for p, a in zip(cf["payment_date"], cf["payment_amount"]))  # noqa: E731
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
    _, b = _bond("TOY 3.5 2027-05-15")
    m = tb.market(date(2027, 5, 15), "USD")  # the maturity date: every flow paid
    assert tb.npv(m, b) == 0.0 and tb.cashflows(m, b).empty and tb.annuity(m, b) == 0.0
    assert tb.delta(m, b) == 0.0 and tb.gamma(m, b) == 0.0 and tb.theta_1d(m, b) == 0.0
    assert tb.yield_bp(m, b) == pytest.approx((m.curve.zero_rate + m.spread) * 1e4, abs=1e-9)


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
    _, b = _bond("TOY 4.25 2034-11-15")
    for t in (D, date(2024, 5, 14)):  # an ordinary day; the day before the 2024-05-15 coupon
        m0, m1 = tb.market(t, "USD"), tb.market(t + timedelta(days=1), "USD")
        cash = N * 0.0425 / 2 if t == date(2024, 5, 14) else 0.0
        assert tb.theta_1d(m0, b) == pytest.approx(tb.npv(m1, b) + cash - tb.npv(m0, b), rel=1e-9)
    m0 = tb.market(date(2024, 5, 14), "USD")
    assert tb.npv(tb.market(date(2024, 5, 15), "USD"), b) < tb.npv(m0, b)  # the dirty PV drops the coupon


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
