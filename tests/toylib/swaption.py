"""Toy swaption world: Bachelier (normal) pricing with a flat vol, on toylib.rates' curves.
Test-only fixture, never shipped (IMPLEMENTATION_PLAN.md P1.5). Import as `toylib.swaption` --
see toylib/__init__.py for why.

The market is `SimpleNamespace(curve=ToyCurve, sigma=...)`; sigma(d) is another deterministic
sinusoid, one flat normal vol per day (decimal, e.g. 0.008 = 80bp).
"""
from __future__ import annotations

import math
from datetime import date
from types import SimpleNamespace
from typing import Optional

from toylib import rates as tr

_SIGMA_PARAMS = {"USD": (0.0080, 0.0020, 252), "EUR": (0.0070, 0.0015, 180)}


def _sigma(d: date, ccy: str) -> float:
    base, amp, period = _SIGMA_PARAMS[ccy]
    n = tr.business_day_index(d)
    return base + amp * math.sin(2 * math.pi * n / period)


def market(d: date, ccy: str, csa: Optional[str] = None):
    """SimpleNamespace(curve=ToyCurve, sigma=float), or None for a date in toylib.rates.HOLES."""
    tr.EVAL_COUNTS["market"] += 1
    tr.CSA_SEEN.append(("market", d, csa))
    if d in tr.HOLES:
        return None
    return SimpleNamespace(curve=tr.ToyCurve(d, ccy, tr._zero_rate(d, ccy), csa), sigma=_sigma(d, ccy))


def _notional_sign(buy_sell, notional_amount) -> float:
    bs = str(getattr(buy_sell, "value", buy_sell)).strip().lower()
    if bs not in ("buy", "sell"):
        raise ValueError(f"buy_sell must be Buy or Sell, got {buy_sell!r}")
    return (1.0 if bs == "buy" else -1.0) * (1.0 if float(notional_amount) >= 0 else -1.0)


def resolve_swaption(market, kwargs: dict) -> dict:
    """Pin expiration_date, termination_date and the strike; fold buy_sell x sign(notional_amount)
    into one signed notional."""
    tr.EVAL_COUNTS["resolve"] += 1
    tr.CSA_SEEN.append(("resolve", market.curve.ref_date, market.curve.csa))
    exp = tr._pin_date(market.curve.ref_date, kwargs["expiration_date"])
    term = tr._pin_date(exp, kwargs["termination_date"])
    notional = abs(float(kwargs["notional_amount"])) * _notional_sign(kwargs.get("buy_sell", "Buy"), kwargs["notional_amount"])
    strike = kwargs.get("strike", "ATM")
    if isinstance(strike, str):
        m = tr._ATM.match(strike)
        if not m:
            raise ValueError(f"unsupported strike {strike!r} (use 'ATM', 'ATM-50'/'A-50' in bp, or a decimal)")
        spread_bp = float(m.group(2) or 0.0) * (-1.0 if m.group(1) == "-" else 1.0)
        k = tr._par_rate(market.curve, exp, term) + spread_bp / 1e4
    else:
        k = float(strike)
    pay_or_receive = kwargs.get("pay_or_receive", "Pay")
    tr._sign(pay_or_receive)  # DESIGN §13.3: reject un-priceable pay_or_receive at resolve time
    return {
        "pay_or_receive": pay_or_receive,
        "expiration_date": exp,
        "termination_date": term,
        "strike": k,
        "notional": notional,
    }


def _phi(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _big_phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _unit_price(F: float, K: float, sigma: float, T: float, is_call: bool) -> float:
    """Bachelier price per unit of (notional * annuity). At/after expiry (T <= 0) it is intrinsic
    value -- DESIGN.md section 13 prices a swaption on its own expiration_date (an AddTradeAction
    exit date), so T == 0 is a real, not just theoretical, input."""
    if T <= 0.0:
        return max(F - K, 0.0) if is_call else max(K - F, 0.0)
    d = (F - K) / (sigma * math.sqrt(T))
    if is_call:
        return (F - K) * _big_phi(d) + sigma * math.sqrt(T) * _phi(d)
    return (K - F) * _big_phi(-d) + sigma * math.sqrt(T) * _phi(-d)


def npv(market, trade: dict) -> float:
    tr.EVAL_COUNTS["npv"] += 1
    c = market.curve
    exp, term = trade["expiration_date"], trade["termination_date"]
    ann = tr._annuity(c, exp, term)
    F = tr._par_rate(c, exp, term)
    T = (exp - c.ref_date).days / 365.0
    unit = _unit_price(F, trade["strike"], market.sigma, T, tr._is_payer(trade["pay_or_receive"]))
    return trade["notional"] * ann * unit


def vega(market, trade: dict) -> float:
    """Per bp of normal vol (same for payer and receiver). Zero at/after expiry: an expired
    swaption has no time value left to be sensitive to."""
    tr.EVAL_COUNTS["vega"] += 1
    c = market.curve
    exp, term = trade["expiration_date"], trade["termination_date"]
    T = (exp - c.ref_date).days / 365.0
    if T <= 0.0:
        return 0.0
    ann = tr._annuity(c, exp, term)
    F = tr._par_rate(c, exp, term)
    d = (F - trade["strike"]) / (market.sigma * math.sqrt(T))
    return trade["notional"] * ann * math.sqrt(T) * _phi(d) * 1e-4
