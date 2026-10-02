"""Deterministic closed-form toy rates world, for tests only (never shipped).

One flat curve per currency whose level moves day to day: r(d) = base + amp * sin(2*pi*n/period),
where n is the number of business days since 2020-01-01 (IMPLEMENTATION_PLAN.md P1.5). Discounting
is continuously compounded, ACT/365. Swaps have annual fixed coupons; the floating leg is valued
as notional * (DF(effective) - DF(maturity)) -- the same shape the real asset config uses
(DESIGN.md Appendix A), so toy and real configs exercise the pricing layer identically.

Also holds the recorder contract of IMPLEMENTATION_PLAN.md section 0.6: EVAL_COUNTS, CSA_SEEN,
HOLES and reset_recorders(). These are module-level, shared with any code that does
`import toylib.rates` -- including toy asset configs' `imports:` blocks.
"""
from __future__ import annotations

import collections
import dataclasses
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

import numpy as np
from dateutil.relativedelta import relativedelta

# --------------------------------------------------------------------- recorders (section 0.6)
EVAL_COUNTS: "collections.Counter[str]" = collections.Counter()
CSA_SEEN: list = []
HOLES: set = set()


def reset_recorders() -> None:
    """Clear EVAL_COUNTS, CSA_SEEN and HOLES. The conftest isolation fixture calls this before and
    after every test (IMPLEMENTATION_PLAN.md section 0.6)."""
    EVAL_COUNTS.clear()
    CSA_SEEN.clear()
    HOLES.clear()


# --------------------------------------------------------------------- the rate curve
_EPOCH = date(2020, 1, 1)
# base, amplitude, period (business days) per currency -- picked so each series visibly
# mean-reverts around its base over a period on the order of a year.
_CCY_PARAMS = {
    "USD": (0.0300, 0.0100, 252),
    "EUR": (0.0200, 0.0080, 180),
}


def business_day_index(d: date) -> int:
    """Number of business days since 2020-01-01 (weekends excluded; no holiday calendar)."""
    return int(np.busday_count(_EPOCH, d))


def _zero_rate(d: date, ccy: str) -> float:
    base, amp, period = _CCY_PARAMS[ccy]
    n = business_day_index(d)
    return base + amp * math.sin(2 * math.pi * n / period)


@dataclass(frozen=True)
class ToyCurve:
    ref_date: date
    ccy: str
    zero_rate: float
    csa: Optional[str] = None
    slope: float = 0.0  # PNL_EXPLAIN_PLAN.md 3.1: sloped-world tests only; 0.0 default is a no-op.

    def discount_factor(self, d: date) -> float:
        t = (d - self.ref_date).days / 365.0
        return math.exp(-(self.zero_rate + self.slope * t) * t)


def market(d: date, ccy: str, csa: Optional[str] = None) -> Optional[ToyCurve]:
    """The market for date `d`: a flat ToyCurve, or None for a date in HOLES."""
    EVAL_COUNTS["market"] += 1
    CSA_SEEN.append(("market", d, csa))
    if d in HOLES:
        return None
    return ToyCurve(d, ccy, _zero_rate(d, ccy), csa)


def market_sloped(d: date, ccy: str, csa: Optional[str] = None, slope: float = 0.0) -> Optional[ToyCurve]:
    """Same level as `market()` (the same `_zero_rate(d, ccy)`), plus an explicit curve slope.
    `_zero_rate` gives the level, `slope` gives the shape. Not wired into `toy_usd_irs.yaml`;
    PNL_EXPLAIN_PLAN.md 3.1 says the sloped-world tests reach it through a test-only config variant.
    Tagged as a "market" eval like `market()`, since it is the same market-construction role."""
    EVAL_COUNTS["market"] += 1
    CSA_SEEN.append(("market", d, csa))
    if d in HOLES:
        return None
    return ToyCurve(d, ccy, _zero_rate(d, ccy), csa, slope)


# --------------------------------------------------------------------- swap resolution and construction
_ATM = re.compile(r"^\s*(?:atm|a)\s*(?:([+-])\s*(\d+(?:\.\d+)?))?\s*$", re.I)
_TENOR = re.compile(r"^\s*(\d+)\s*([dwmy])\s*$", re.I)
_TENOR_KW = {"d": "days", "w": "weeks", "m": "months", "y": "years"}


def _sign(pay_or_receive) -> float:
    s = str(getattr(pay_or_receive, "value", pay_or_receive)).strip().lower()
    if s == "pay":
        return 1.0
    if s in ("rec", "receive", "receiver"):
        return -1.0
    raise ValueError(f"pay_or_receive must be Pay or Receive, got {pay_or_receive!r}")


def _is_payer(pay_or_receive) -> bool:
    return _sign(pay_or_receive) > 0.0


def _pin_date(base: date, spec) -> date:
    """A tenor string ('3m', '10y'), 'spot' (== base), or a date/datetime -> an absolute date."""
    if spec is None or (isinstance(spec, str) and spec.strip().lower() == "spot"):
        return base
    if isinstance(spec, str):
        m = _TENOR.match(spec)
        if not m:
            raise ValueError(f"unsupported date/tenor {spec!r}")
        n, unit = int(m.group(1)), m.group(2).lower()
        return base + relativedelta(**{_TENOR_KW[unit]: n})
    if isinstance(spec, datetime):
        return spec.date()
    return spec  # already a date


def _annuity(curve: ToyCurve, eff: date, mat: date) -> float:
    """Sum of DF(coupon date) * accrual for annual coupons from `eff` to `mat`.
    # ponytail: whole-year tenors only (matches the toy configs' 'Ny' tenors); a maturity that
    # isn't an integer number of years past `eff` drops its final stub period.
    """
    total, prev, i = 0.0, eff, 1
    while True:
        cpn = eff + relativedelta(years=i)
        if cpn > mat:
            return total
        total += curve.discount_factor(cpn) * ((cpn - prev).days / 365.0)
        prev, i = cpn, i + 1


def _par_rate(curve: ToyCurve, eff: date, mat: date) -> float:
    ann = _annuity(curve, eff, mat)
    return (curve.discount_factor(eff) - curve.discount_factor(mat)) / ann


def resolve_swap(market: ToyCurve, kwargs: dict) -> dict:
    """Pin dates and the ATM rate at the trade date. Signed notional: payer > 0."""
    EVAL_COUNTS["resolve"] += 1
    CSA_SEEN.append(("resolve", market.ref_date, market.csa))
    eff = _pin_date(market.ref_date, kwargs.get("effective_date"))
    term = _pin_date(eff, kwargs["termination_date"])
    notional = float(kwargs["notional_amount"]) * _sign(kwargs["pay_or_receive"])
    fr = kwargs.get("fixed_rate", "ATM")
    if isinstance(fr, str):
        m = _ATM.match(fr)
        if not m:
            raise ValueError(f"unsupported fixed_rate {fr!r} (use 'ATM', 'ATM+x'/'ATM-x' in bp, or a decimal)")
        spread_bp = float(m.group(2) or 0.0) * (-1.0 if m.group(1) == "-" else 1.0)
        k = _par_rate(market, eff, term) + spread_bp / 1e4
    else:
        k = float(fr)
    return {"effective_date": eff, "termination_date": term, "fixed_rate": k, "notional": notional}


@dataclass(frozen=True)
class ToySwap:
    effective_date: date
    termination_date: date
    fixed_rate: float
    notional: float


def build_swap(market: ToyCurve, resolved: dict) -> ToySwap:
    EVAL_COUNTS["trade"] += 1
    return ToySwap(resolved["effective_date"], resolved["termination_date"], resolved["fixed_rate"], resolved["notional"])


# --------------------------------------------------------------------- per-trade and portfolio functions
def npv(market: ToyCurve, trade: ToySwap) -> float:
    """Payer positive when rates rise: notional * (float leg PV - fixed leg PV)."""
    EVAL_COUNTS["npv"] += 1
    ann = _annuity(market, trade.effective_date, trade.termination_date)
    float_pv = market.discount_factor(trade.effective_date) - market.discount_factor(trade.termination_date)
    return trade.notional * (float_pv - trade.fixed_rate * ann)


def pv01(market: ToyCurve, trade: ToySwap) -> float:
    EVAL_COUNTS["pv01"] += 1
    return trade.notional * _annuity(market, trade.effective_date, trade.termination_date) * 1e-4


def par_rate(market: ToyCurve, trade: ToySwap) -> float:
    EVAL_COUNTS["par_rate"] += 1
    return _par_rate(market, trade.effective_date, trade.termination_date) * 1e4


class TranslatedCurve:
    """A curve translated forward `days` calendar days: forward rates held fixed (PNL_EXPLAIN_PLAN.md
    2.2), as opposed to a *rolled* curve (static shape in tenor space). Same duck-typed interface as
    ToyCurve -- `discount_factor`, plus `ref_date`/`ccy`/`csa` for parity -- so it drops straight into
    `npv`/`pv01`/`par_rate`/`_annuity`, all of which only ever call `.discount_factor(d)` on a curve."""

    def __init__(self, base: ToyCurve, days: int):
        self.base = base
        self.ref_date = base.ref_date + timedelta(days=days)
        self.ccy = base.ccy
        self.csa = base.csa

    def discount_factor(self, d: date) -> float:
        return self.base.discount_factor(d) / self.base.discount_factor(self.ref_date)


def gamma(market: ToyCurve, trade: ToySwap) -> float:
    """PNL_EXPLAIN_PLAN.md 2.1: second derivative of npv in this trade's own par rate (never the
    derivative of dv01 -- that gives half the gamma, see T-GAMMA-2's half-gamma trap), on the same
    +/-1bp zero shift, by the CHAIN RULE (contracts IRGammaParallel, MERGE_NOTES_pnl_explain.md
    section 4; the same formula as toylib.irrisk.own_rate_greeks): the par rate's own convexity
    in the shift is taken out, else the result is ~10% low at 10y ATM. Payer < 0; receiver = -payer.
    0.0 on and after the final date, as toylib.irrisk.ir_gamma (toy_usd_irs's IRGamma ladder), so
    the scalar and the ladder agree on a matured swap."""
    EVAL_COUNTS["gamma"] += 1
    if market.ref_date >= trade.termination_date:
        return 0.0
    up = dataclasses.replace(market, zero_rate=market.zero_rate + 1e-4)
    down = dataclasses.replace(market, zero_rate=market.zero_rate - 1e-4)
    npv_up, npv_down, npv_mid = npv(up, trade), npv(down, trade), npv(market, trade)
    par_up, par_down, par_mid = par_rate(up, trade), par_rate(down, trade), par_rate(market, trade)
    dpar = par_up - par_down
    delta = (npv_up - npv_down) / dpar
    return (npv_up + npv_down - 2.0 * npv_mid - delta * (par_up + par_down - 2.0 * par_mid)) / (dpar / 2.0) ** 2


def theta(market: ToyCurve, trade: ToySwap) -> float:
    """PNL_EXPLAIN_PLAN.md 2.2, EXACTLY: one-calendar-day forward difference on a TRANSLATED (not
    rolled) curve, in currency per YEAR. Roll-down that actually happens is real Δpar and belongs to
    delta, not here (see T-SLOPE-2's double-count test)."""
    EVAL_COUNTS["theta"] += 1
    return (npv(TranslatedCurve(market, 1), trade) - npv(market, trade)) * 365.0


def year_fraction(market: ToyCurve) -> float:
    """PNL_EXPLAIN_PLAN.md 2.3: an INTENSIVE time coordinate -- `unit: decimal`, never `unit: number`,
    or pricebt would scale it by quantity_ (T-YF's mutation)."""
    EVAL_COUNTS["year_fraction"] += 1
    return (market.ref_date - date(2000, 1, 1)).days / 365.0


def cash_paid_to_date(market: ToyCurve, trade: ToySwap) -> float:
    """PNL_EXPLAIN_PLAN.md 2.4: always 0.0 on the toy. This is correct, not a stub -- the toy's
    `_annuity`/`npv` never drop a past coupon out of PV, so no cash ever left it (T-CASH)."""
    EVAL_COUNTS["cash_paid_to_date"] += 1
    return 0.0


def _tenor_years(t: str) -> float:
    m = _TENOR.match(t)
    if not m:
        raise ValueError(f"unsupported ladder tenor {t!r}")
    n, unit = int(m.group(1)), m.group(2).lower()
    return n * {"d": 1 / 365.0, "w": 7 / 365.0, "m": 1 / 12.0, "y": 1.0}[unit]


def delta_ladder(market: ToyCurve, trades, weights, tenors) -> dict:
    """Each trade's weighted PV01 goes to its nearest pillar by time to maturity; exactly linear
    in `weights`."""
    EVAL_COUNTS["delta_ladder"] += 1
    tenors = tuple(tenors)
    pillar_years = {t: _tenor_years(t) for t in tenors}
    buckets = {t: 0.0 for t in tenors}
    for trade, w in zip(trades, weights):
        ttm = (trade.termination_date - market.ref_date).days / 365.0
        nearest = min(tenors, key=lambda t: abs(pillar_years[t] - ttm))
        buckets[nearest] += pv01(market, trade) * w
    return buckets
