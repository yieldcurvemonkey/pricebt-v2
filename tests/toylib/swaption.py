"""Toy swaption world: Bachelier (normal) pricing with a flat vol, on toylib.rates' curves.
Test-only fixture, never shipped (IMPLEMENTATION_PLAN.md P1.5). Import as `toylib.swaption` --
see toylib/__init__.py for why.

The market is `SimpleNamespace(curve=ToyCurve, sigma=...)`; sigma(d) is another deterministic
sinusoid, one flat normal vol per day (decimal, e.g. 0.008 = 80bp).

The IR measure contract (docs/v2/IR_RISK_DESIGN.md section 00 R2-1..R2-8, section 6.2) is below
`vega`: the own rate is the underlying's forward swap rate F (bp); delta/gamma are the total /
chain-rule derivatives on +-1bp zero bumps with sigma fixed (toylib.irrisk); 'Straddle' prices as
payer + receiver. At and after expiry the swaption is physically settled: a leg is exercised iff
it was in the money at expiration_date in the deterministic toy world, and then it IS the
underlying swap (value and sensitivities); an unexercised leg is worth 0 with 0 sensitivities.
Levels continue after expiry: the live F, the vol at expiry (R2-7).
"""
from __future__ import annotations

import math
from datetime import date
from types import SimpleNamespace
from typing import Optional

from toylib import irrisk as ir
from toylib import rates as tr

_SIGMA_PARAMS = {"USD": (0.0080, 0.0020, 252), "EUR": (0.0070, 0.0015, 180)}
H_VOL = 1e-4  # +-1bp normal-vol bump behind vanna and volga


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


def _legs(pay_or_receive) -> tuple:
    """is_call (payer) flag of each leg: a straddle is a payer plus a receiver (R11 section 4.2)."""
    if str(getattr(pay_or_receive, "value", pay_or_receive)).strip().lower() == "straddle":
        return (True, False)
    return (tr._is_payer(pay_or_receive),)


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
    _legs(pay_or_receive)  # DESIGN §13.3: reject un-priceable pay_or_receive at resolve time
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


def _fwd(curve, trade) -> float:
    return tr._par_rate(curve, trade["expiration_date"], trade["termination_date"])


def _expiry_fwd(curve, trade) -> float:
    """F at expiration_date in the deterministic toy world -- never the pricing market's, so the
    exercise decision is fixed once made (physical settlement)."""
    exp = trade["expiration_date"]
    return _fwd(tr.ToyCurve(exp, curve.ccy, tr._zero_rate(exp, curve.ccy)), trade)


def _exercised(curve, trade, is_call: bool) -> bool:
    F, K = _expiry_fwd(curve, trade), trade["strike"]
    return F > K if is_call else F < K


def _value(curve, sigma: float, trade: dict, exercise_on_curve: bool = False) -> float:
    """`exercise_on_curve`: at T == 0 decide exercise on this curve's F (intrinsic value) rather
    than the toy world's expiry F -- theta's frozen-F step onto expiry (R2-4)."""
    exp, term, K = trade["expiration_date"], trade["termination_date"], trade["strike"]
    ann, F = tr._annuity(curve, exp, term), _fwd(curve, trade)
    T = (exp - curve.ref_date).days / 365.0
    total = 0.0
    for is_call in _legs(trade["pay_or_receive"]):
        if T > 0.0 or (T == 0.0 and exercise_on_curve):
            total += _unit_price(F, K, sigma, T, is_call)
        elif _exercised(curve, trade, is_call):  # physical: the leg is now the underlying swap
            total += (F - K) if is_call else (K - F)
    return trade["notional"] * ann * total


def npv(market, trade: dict) -> float:
    tr.EVAL_COUNTS["npv"] += 1
    return _value(market.curve, market.sigma, trade)


def vega(market, trade: dict) -> float:
    """Per bp of normal vol (same for payer and receiver; a straddle is both). Zero at/after
    expiry: an expired swaption has no time value left to be sensitive to."""
    tr.EVAL_COUNTS["vega"] += 1
    c = market.curve
    exp, term = trade["expiration_date"], trade["termination_date"]
    T = (exp - c.ref_date).days / 365.0
    if T <= 0.0:
        return 0.0
    ann = tr._annuity(c, exp, term)
    F = tr._par_rate(c, exp, term)
    d = (F - trade["strike"]) / (market.sigma * math.sqrt(T))
    return len(_legs(trade["pay_or_receive"])) * trade["notional"] * ann * math.sqrt(T) * _phi(d) * 1e-4


# --------------------------------------------------------------------- IR measure contract


def _greeks(curve, sigma: float, trade: dict):
    return ir.greeks_on(curve, lambda c: _value(c, sigma, trade), lambda c: _fwd(c, trade) * 1e4)


def delta(market, trade: dict) -> float:
    """IRDelta scalar: total derivative w.r.t. F, sigma fixed, ccy per bp (pricebt DEV-I12)."""
    return _greeks(market.curve, market.sigma, trade)[0]


def gamma(market, trade: dict) -> float:
    return _greeks(market.curve, market.sigma, trade)[1]


def discount_delta(market, trade: dict) -> float:
    return ir.discount_delta_on(market.curve, lambda c: _value(c, market.sigma, trade))


def vanna(market, trade: dict) -> float:
    """d(IRDelta scalar)/d sigma per bp x bp, on +-1bp sigma bumps (exactly 0 after expiry)."""
    c, s = market.curve, market.sigma
    return (_greeks(c, s + H_VOL, trade)[0] - _greeks(c, s - H_VOL, trade)[0]) / 2.0


def volga(market, trade: dict) -> float:
    """d2 PV / d sigma^2 per bp^2 of normal vol (exactly 0 after expiry)."""
    c, s = market.curve, market.sigma
    return _value(c, s + H_VOL, trade) + _value(c, s - H_VOL, trade) - 2.0 * _value(c, s, trade)


def theta_1d(market, trade: dict) -> float:
    """pricebt DEV-I15: one calendar day on the translated curve, F and sigma fixed (T - 1/365),
    ccy per day; premium 0 and nothing dropped, so no cash term. A step landing on expiry exercises
    on the frozen F; after expiry the decision already made stands."""
    c = market.curve
    return _value(ir._TranslatedCurve(c, 1), market.sigma, trade, exercise_on_curve=True) - _value(c, market.sigma, trade)


def fwd_rate(market, trade: dict) -> float:
    return _fwd(market.curve, trade) * 1e4


def spot_rate(market, trade: dict) -> float:
    return ir.spot_par_bp(market.curve, trade["termination_date"])


def annual_vol(market, trade: dict) -> float:
    """sigma in bp; after expiry the last live value, sigma(expiration_date) (R2-7)."""
    if trade["expiration_date"] > market.curve.ref_date:
        return market.sigma * 1e4
    return _sigma(trade["expiration_date"], market.curve.ccy) * 1e4


atm_vol = annual_vol  # one flat vol per day: the ATM vol is the strike vol


def daily_vol(market, trade: dict) -> float:
    return annual_vol(market, trade) / math.sqrt(252.0)


def expiry_in_years(market, trade: dict) -> float:
    return ir.years(market.curve.ref_date, trade["expiration_date"])


def prob_exercise(market, trade: dict) -> float:
    """Phi(d) payer, Phi(-d) receiver (summed over a straddle's legs); 1/0 once decided."""
    c = market.curve
    T = (trade["expiration_date"] - c.ref_date).days / 365.0
    total = 0.0
    for is_call in _legs(trade["pay_or_receive"]):
        if T > 0.0:
            d = (_fwd(c, trade) - trade["strike"]) / (market.sigma * math.sqrt(T))
            total += _big_phi(d if is_call else -d)
        else:
            total += float(_exercised(c, trade, is_call))
    return total


def annuity(market, trade: dict) -> float:
    """N * A of the underlying swap, holder-signed."""
    return trade["notional"] * tr._annuity(market.curve, trade["expiration_date"], trade["termination_date"])


def cashflows(market, trade: dict):
    return ir.empty_cashflows()


def _end(trade):
    return trade["termination_date"]


def delta_ladder(market, trades, weights, tenors) -> dict:
    return ir.ladder(market.curve.ref_date, trades, weights, tenors, lambda t: delta(market, t), _end)


def gamma_ladder(market, trades, weights, tenors) -> dict:
    """pricebt DEV-I13: diagonal (the scalar at its nearest pillar)."""
    return ir.ladder(market.curve.ref_date, trades, weights, tenors, lambda t: gamma(market, t), _end)


def vega_cube(market, trades, weights, expiries, tails) -> dict:
    """Each trade's weighted vega at mkt_point '<tail>;<expiry>' (gs order, e.g. '10Y;1Y'), the
    pillars nearest its underlying tenor and its time to expiry."""
    t0, out = market.curve.ref_date, {}
    for trade, w in zip(trades, weights):
        exp = trade["expiration_date"]
        point = f"{ir.nearest_pillar(tails, ir.years(exp, trade['termination_date']))};{ir.nearest_pillar(expiries, ir.years(t0, exp))}"
        out[point] = out.get(point, 0.0) + vega(market, trade) * w
    return out
