"""Toy fixed-coupon bond world, for tests only (never shipped; docs/v2/IR_RISK_DESIGN.md section 6.3,
section 00 R2-1..R2-8). Import as `toylib.bond` (see toylib/__init__.py).

A tiny bond master keyed by identifier; the market is `SimpleNamespace(curve=ToyCurve,
spread=s(d))`, toylib.rates' flat USD curve (HOLES included) plus a deterministic sinusoidal
spread (~30bp +- 10bp). Price is the holder-signed dirty PV of the flows still to be paid
(payment_date > t), each discounted at DF(t_i) * exp(-s * tau_i), tau = days/365; paid flows drop
out, so Cashflows lists them and Theta carries the day's coupon as cash. The own rate is the yield
y (continuously compounded, ACT/365, solved by Newton), in bp. On this flat world y = z + s exactly.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Optional

import pandas as pd
from dateutil.relativedelta import relativedelta

from toylib import irrisk as ir
from toylib import rates as tr

# identifier -> (annual coupon, maturity, coupons per year)
BONDS = {
    "TOY 3.5 2027-05-15": (0.035, date(2027, 5, 15), 2),
    "TOY 4.25 2034-11-15": (0.0425, date(2034, 11, 15), 2),
    "TOY 4.5 2054-02-15": (0.045, date(2054, 2, 15), 2),
}
_SPREAD_PARAMS = {"USD": (0.0030, 0.0010, 200)}  # base, amplitude, period (business days)
CASHFLOW_COLUMNS = ir.CASHFLOW_COLUMNS + ("accrual_start_date", "accrual_end_date", "notional", "rate")


def _spread(d: date, ccy: str) -> float:
    base, amp, period = _SPREAD_PARAMS[ccy]
    return base + amp * math.sin(2 * math.pi * tr.business_day_index(d) / period)


def market(d: date, ccy: str, csa: Optional[str] = None):
    """SimpleNamespace(curve=ToyCurve, spread=float), or None for a date in toylib.rates.HOLES."""
    curve = tr.market(d, ccy, csa)
    return None if curve is None else SimpleNamespace(curve=curve, spread=_spread(d, ccy))


def resolve_bond(market, kwargs: dict) -> dict:
    """Pin the identifier's terms and the trade date; fold buy_sell x sign(size) into a signed face."""
    ident = kwargs.get("identifier")
    if ident not in BONDS:
        raise ValueError(f"unknown toy bond identifier {ident!r}; known: {sorted(BONDS)}")
    bs = str(getattr(kwargs.get("buy_sell", "Buy"), "value", kwargs.get("buy_sell", "Buy"))).strip().lower()
    if bs not in ("buy", "sell"):
        raise ValueError(f"buy_sell must be Buy or Sell, got {kwargs.get('buy_sell')!r}")
    size = float(kwargs["size"])
    coupon, maturity, freq = BONDS[ident]
    face = abs(size) * (1.0 if bs == "buy" else -1.0) * (1.0 if size >= 0 else -1.0)
    return {"identifier": ident, "coupon": coupon, "maturity": maturity, "frequency": freq, "face": face, "trade_date": market.curve.ref_date}


# --------------------------------------------------------------------- schedule and pricing


def _periods(trade, t: date):
    """(accrual start, payment date) of every coupon period paying after `t`, rolled back from maturity."""
    mat, step, out, i = trade["maturity"], 12 // trade["frequency"], [], 0
    while mat - relativedelta(months=step * i) > t:
        out.append((mat - relativedelta(months=step * (i + 1)), mat - relativedelta(months=step * i)))
        i += 1
    return out[::-1]


def _flows(trade, t: date):
    """(payment_date, holder-signed amount, payment_type, accrual start, accrual end) with payment_date > t."""
    face, cpn = trade["face"], trade["coupon"] / trade["frequency"]
    rows = [(end, face * cpn, "Coupon", start, end) for start, end in _periods(trade, t)]
    return rows + [(trade["maturity"], face, "Principal", *rows[-1][3:])] if rows else []


def _flow_pvs(curve, spread: float, trade):
    t = curve.ref_date
    return [a * curve.discount_factor(p) * math.exp(-spread * (p - t).days / 365.0) for p, a, *_ in _flows(trade, t)]


def _pv(curve, spread: float, trade) -> float:
    return sum(_flow_pvs(curve, spread, trade))


def _yield(curve, spread: float, trade) -> float:
    """y with PV = sum cf exp(-y tau) (Newton). With no flow left, the yield of a one-day unit flow
    (z + s here), so the level stays finite and continuous after maturity (R2-7)."""
    t = curve.ref_date
    flows = [((p - t).days / 365.0, a) for p, a, *_ in _flows(trade, t)]
    target = _pv(curve, spread, trade)
    if not flows:
        flows, target = [(1 / 365.0, 1.0)], curve.discount_factor(t + timedelta(days=1)) * math.exp(-spread / 365.0)
    y = 0.03
    for _ in range(50):
        f = sum(a * math.exp(-y * tau) for tau, a in flows) - target
        step = f / -sum(tau * a * math.exp(-y * tau) for tau, a in flows)
        y -= step
        if abs(step) < 1e-15:
            break
    return y


def _yield_greeks(market, trade, pvs):
    """own_rate_greeks of `pvs(curve)` against the yield (bp) on the +-1bp curve bumps, spread fixed."""
    s = market.spread
    return ir.greeks_on(market.curve, lambda c: pvs(c, s, trade), lambda c: _yield(c, s, trade) * 1e4)


# --------------------------------------------------------------------- IR measure contract


def npv(market, trade, value_date: Optional[date] = None) -> float:
    """Valued on `value_date` (the config passes `pricebt_date`): under a CloseMarket override,
    the override's curve and spread seen from the pricing date, so no coupon drops and nothing
    carries (toylib.irrisk.at)."""
    tr.EVAL_COUNTS["npv"] += 1
    return _pv(ir.at(market.curve, value_date), market.spread, trade)


def yield_bp(market, trade) -> float:
    """IRFwdRate and IRSpotRate: the yield to maturity in bp (pricebt DEV-I12)."""
    return _yield(market.curve, market.spread, trade) * 1e4


def delta(market, trade) -> float:
    """Total derivative along the parallel curve shift, spread fixed, per bp of yield (dy/dz = 1
    on this flat world, so it is the yield DV01)."""
    return _yield_greeks(market, trade, _pv)[0]


def gamma(market, trade) -> float:
    return _yield_greeks(market, trade, _pv)[1]


def discount_delta(market, trade) -> float:
    return ir.discount_delta_on(market.curve, lambda c: _pv(c, market.spread, trade))


def yield_dv01(market, trade) -> float:
    """LightningDV01: analytic dPV/dy per +1bp at the solved yield."""
    t, y = market.curve.ref_date, _yield(market.curve, market.spread, trade)
    return -sum((p - t).days / 365.0 * a * math.exp(-y * (p - t).days / 365.0) for p, a, *_ in _flows(trade, t)) * 1e-4


def theta_1d(market, trade) -> float:
    """pricebt DEV-I15 (R2-4 bond rule): PV(t+1d) at the SAME yield + flows paid in (t, t+1d] - PV(t)."""
    t = market.curve.ref_date
    t1, y = t + timedelta(days=1), _yield(market.curve, market.spread, trade)
    later = sum(a * math.exp(-y * (p - t1).days / 365.0) for p, a, *_ in _flows(trade, t1))
    cash = sum(a for p, a, *_ in _flows(trade, t) if p <= t1)
    return later + cash - _pv(market.curve, market.spread, trade)


def expiry_in_years(market, trade) -> float:
    return ir.years(market.curve.ref_date, trade["maturity"])  # pricebt DEV-I17: to maturity


def spread_bp(market, trade) -> float:
    """LightningOAS (a bullet bond's Z-spread) and ParSpread: s in bp. On this flat single-curve toy
    the par spread is taken equal to the Z-spread."""
    return market.spread * 1e4


def annuity(market, trade) -> float:
    """PV of 1.0 p.a. on the coupon schedule, per signed face."""
    c, t = market.curve, market.curve.ref_date
    return trade["face"] / trade["frequency"] * sum(c.discount_factor(p) * math.exp(-market.spread * (p - t).days / 365.0) for _s, p in _periods(trade, t))


def cashflows(market, trade) -> pd.DataFrame:
    rows = [(p, a, market.curve.ccy, kind, s, e, trade["face"], trade["coupon"]) for p, a, kind, s, e in _flows(trade, market.curve.ref_date)]
    return pd.DataFrame(rows, columns=list(CASHFLOW_COLUMNS))


def accrued(market, trade) -> float:
    """Holder-signed accrued coupon (ACT/ACT in the period), ccy."""
    t = market.curve.ref_date
    periods = _periods(trade, t)
    if not periods:
        return 0.0
    start, end = periods[0]
    return trade["face"] * trade["coupon"] / trade["frequency"] * (t - start).days / (end - start).days


def dirty_price(market, trade) -> float:
    return 100.0 * _pv(market.curve, market.spread, trade) / trade["face"]


def clean_price(market, trade) -> float:
    return dirty_price(market, trade) - 100.0 * accrued(market, trade) / trade["face"]


def pnl_explain(market, market_to, trades, weights, value_date: Optional[date] = None) -> list:
    """PnlExplain by full revaluation, weighted, every value on `value_date` (the pricing date, as
    `npv`: no coupon drops, no carry): IR = the curve moved with the spread held, CREDIT = the
    spread moved with the curve held, CROSSES = the rest of the whole move."""
    c0, s0, c1, s1 = ir.at(market.curve, value_date), market.spread, ir.at(market_to.curve, value_date), market_to.spread
    parts, total = {"IR": 0.0, "CREDIT": 0.0}, 0.0
    for trade, w in zip(trades, weights):
        base = _pv(c0, s0, trade)
        parts["IR"] += w * (_pv(c1, s0, trade) - base)
        parts["CREDIT"] += w * (_pv(c0, s1, trade) - base)
        total += w * (_pv(c1, s1, trade) - base)
    return ir.explain_rows(c0.ccy, parts, total)


def _flow_ladder(market, trade, tenors, which: int) -> dict:
    """Per-flow own-rate delta (which=0) or diagonal gamma (1, pricebt DEV-I13) at the pillar
    nearest each flow; linear in the flow PVs, so the buckets sum to the scalar."""
    c, s, t = market.curve, market.spread, market.curve.ref_date
    curves = (ir.bumped(c, -ir.H), c, ir.bumped(c, ir.H))
    pvs = [_flow_pvs(x, s, trade) for x in curves]
    ys = [_yield(x, s, trade) * 1e4 for x in curves]
    out = dict.fromkeys(tenors, 0.0)
    for i, (p, *_rest) in enumerate(_flows(trade, t)):
        out[ir.nearest_pillar(tenors, ir.years(t, p))] += ir.own_rate_greeks([v[i] for v in pvs], ys)[which]
    return out


def _book_ladder(market, trades, weights, tenors, which: int) -> dict:
    out = dict.fromkeys(tenors, 0.0)
    for trade, w in zip(trades, weights):
        for k, v in _flow_ladder(market, trade, tenors, which).items():
            out[k] += v * w
    return out


def delta_ladder(market, trades, weights, tenors) -> dict:
    return _book_ladder(market, trades, weights, tenors, 0)


def gamma_ladder(market, trades, weights, tenors) -> dict:
    return _book_ladder(market, trades, weights, tenors, 1)
