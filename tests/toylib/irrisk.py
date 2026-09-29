"""IR measure-contract functions for the toy swap, plus the kernel toylib.swaption and toylib.bond
share (docs/v2/IR_RISK_DESIGN.md section 00 R2-1..R2-8, section 6.1). Test-only fixture, never
shipped. Built on toylib.rates, which is never edited here (decision 0.12: the in-flight
v2-pnl-explain branch owns it).

Conventions (every toy IR asset): rates and normal vols in bp; the own rate r is the instrument's
IRFwdRate; IRDelta scalar = the TOTAL derivative [PV(+h) - PV(-h)] / [r(+h) - r(-h)] along a
+-1bp parallel zero-rate shift; IRGammaParallel = the chain-rule second derivative on the same
bumps, per bp^2 of r (never d(pv01)/dr, R14's half-gamma trap); Theta = one calendar day on a
translated curve, ccy per day; ExpiryInYears = max(final - t, 0).days / 365.
"""
from __future__ import annotations

import dataclasses
from datetime import date, timedelta

import pandas as pd
from dateutil.relativedelta import relativedelta

from toylib import rates as tr

H = 1e-4  # the +-1bp zero-rate bump behind every own-rate delta/gamma
CASHFLOW_COLUMNS = ("payment_date", "payment_amount", "currency", "payment_type")


# --------------------------------------------------------------------- shared kernel


@dataclasses.dataclass(frozen=True)
class _TranslatedCurve:
    """`base` seen from `days` calendar days later with every forward held fixed:
    DF'(x) = DF(x) / DF(t + days). Never a roll (R2-4): rebuilding ToyCurve(d + 1, ...) would keep
    the zero rate, not the forwards, and would drop `csa`."""

    base: object
    days: int

    @property
    def ref_date(self) -> date:
        return self.base.ref_date + timedelta(days=self.days)

    @property
    def ccy(self) -> str:
        return self.base.ccy

    @property
    def csa(self):
        return self.base.csa

    def discount_factor(self, d: date) -> float:
        return self.base.discount_factor(d) / self.base.discount_factor(self.ref_date)


def bumped(curve, dz: float):
    """The same ToyCurve (csa kept) with its flat zero rate shifted by `dz` (decimal)."""
    return dataclasses.replace(curve, zero_rate=curve.zero_rate + dz)


def own_rate_greeks(pvs, rates):
    """(delta per bp, gamma per bp^2) of the own rate from values at the (-h, 0, +h) zero bumps.
    `rates` in bp. Linear in `pvs`, so per-flow or per-trade parts sum to the totals. pricebt DEV-I12:
    delta = [n+ - n-] / [r+ - r-]; gamma = [n+ + n- - 2n0 - delta (r+ + r- - 2r0)] / ((r+ - r-)/2)^2."""
    (n_dn, n_0, n_up), (r_dn, r_0, r_up) = pvs, rates
    dr = r_up - r_dn
    if dr == 0.0:
        return 0.0, 0.0
    delta = (n_up - n_dn) / dr
    return delta, (n_up + n_dn - 2.0 * n_0 - delta * (r_up + r_dn - 2.0 * r_0)) / (dr / 2.0) ** 2


def greeks_on(curve, pv, rate_bp):
    """`own_rate_greeks` of `pv(curve)` against `rate_bp(curve)` on the +-1bp zero bumps."""
    curves = (bumped(curve, -H), curve, bumped(curve, H))
    return own_rate_greeks([pv(c) for c in curves], [rate_bp(c) for c in curves])


def discount_delta_on(curve, pv) -> float:
    """PV change per +1bp parallel shift of the (only) discount curve (R2-3)."""
    return (pv(bumped(curve, H)) - pv(bumped(curve, -H))) / 2.0


def years(start: date, end: date) -> float:
    """max(end - start, 0).days / 365 (ACT/365F calendar days; pricebt DEV-I17, R2-5)."""
    return max((end - start).days, 0) / 365.0


def nearest_pillar(tenors, yrs: float) -> str:
    return min(tenors, key=lambda t: abs(tr._tenor_years(t) - yrs))


def ladder(ref_date: date, trades, weights, tenors, value, end) -> dict:
    """Each trade's weighted `value(trade)` to the pillar nearest its `end(trade)`, so the ladder
    sums exactly to the weighted scalars."""
    out = dict.fromkeys(tenors, 0.0)
    for trade, w in zip(trades, weights):
        out[nearest_pillar(tenors, years(ref_date, end(trade)))] += value(trade) * w
    return out


def spot_par_bp(curve, term: date) -> float:
    """Par rate (bp) of the swap starting on the curve date and ending on `term`: annual coupons
    rolled back from `term`, a short front stub. Past `term`, a one-day swap (finite, R2-7)."""
    t = curve.ref_date
    if term <= t:
        term = t + timedelta(days=1)
    ann, end, i = 0.0, term, 1
    while end > t:
        start = max(term - relativedelta(years=i), t)
        ann += curve.discount_factor(end) * (end - start).days / 365.0
        end, i = start, i + 1
    return (curve.discount_factor(t) - curve.discount_factor(term)) / ann * 1e4


def empty_cashflows() -> pd.DataFrame:
    """A total-return Price never drops a paid flow, so there is nothing left to drop (R2-6)."""
    return pd.DataFrame(columns=list(CASHFLOW_COLUMNS))


# --------------------------------------------------------------------- the toy swap (toylib.rates.ToySwap)


def _par_bp(curve, trade) -> float:
    return tr._par_rate(curve, trade.effective_date, trade.termination_date) * 1e4


def delta(market, trade) -> float:
    """IRDelta scalar: total derivative of npv w.r.t. the swap's own par rate, ccy per bp. Equals
    the annuity pv01 (`tr.pv01`) only at the money (R2-1)."""
    return greeks_on(market, lambda c: tr.npv(c, trade), lambda c: _par_bp(c, trade))[0]


def ir_gamma(market, trade) -> float:
    """IRGammaParallel: d2 npv / d par^2 per bp^2 by the chain rule (R14 gamma finding)."""
    return greeks_on(market, lambda c: tr.npv(c, trade), lambda c: _par_bp(c, trade))[1]


def discount_delta(market, trade) -> float:
    return discount_delta_on(market, lambda c: tr.npv(c, trade))


def theta_1d(market, trade) -> float:
    """pricebt DEV-I15: one calendar day, forwards fixed, ccy per day. The toy npv never drops a
    paid coupon (total return), so there is no cash term."""
    return tr.npv(_TranslatedCurve(market, 1), trade) - tr.npv(market, trade)


def expiry_in_years(market, trade) -> float:
    return years(market.ref_date, trade.termination_date)  # pricebt DEV-I17: a swap's final date


def spot_rate(market, trade) -> float:
    return spot_par_bp(market, trade.termination_date)


def annuity(market, trade) -> float:
    """N * A: PV of the fixed leg paying 1.0 p.a. (1e4 x tr.pv01), holder-signed."""
    return trade.notional * tr._annuity(market, trade.effective_date, trade.termination_date)


def delta_ladder(market, trades, weights, tenors) -> dict:
    return ladder(market.ref_date, trades, weights, tenors, lambda t: delta(market, t), lambda t: t.termination_date)


def gamma_ladder(market, trades, weights, tenors) -> dict:
    """pricebt DEV-I13: a diagonal ladder (the scalar at its nearest pillar), not gs's cross-gamma."""
    return ladder(market.ref_date, trades, weights, tenors, lambda t: ir_gamma(market, t), lambda t: t.termination_date)
