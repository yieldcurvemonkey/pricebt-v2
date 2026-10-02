"""IR measure-contract functions for the toy swap, plus the kernel toylib.swaption and toylib.bond
share (docs/v2/IR_RISK_DESIGN.md section 00 R2-1..R2-8, section 6.1). Test-only fixture, never
shipped. Built on toylib.rates (whose npv, pv01 and delta_ladder the swap P&L recipe's
toy_usd_irs config keeps using; decision 0.12), plus IR_STRICT_CONTRACT R3-1's measures.

Conventions (every toy IR asset): rates and normal vols in bp; the own rate r is the instrument's
IRFwdRate; IRDelta scalar = the TOTAL derivative [PV(+h) - PV(-h)] / [r(+h) - r(-h)] along a
+-1bp parallel zero-rate shift; IRGammaParallel = the chain-rule second derivative on the same
bumps, per bp^2 of r (never d(pv01)/dr, R14's half-gamma trap); Theta = one calendar day on a
translated curve, ccy per day; ExpiryInYears = max(final - t, 0).days / 365.
"""
from __future__ import annotations

import dataclasses
from datetime import date, timedelta
from typing import Optional

import pandas as pd
from dateutil.relativedelta import relativedelta

from toylib import rates as tr

H = 1e-4  # the +-1bp zero-rate bump behind every own-rate delta/gamma
CASHFLOW_COLUMNS = ("payment_date", "payment_amount", "currency", "payment_type")
# The toy stands in for an external library, so it never imports pricebt; tests/test_toylib_ir.py
# asserts these equal pricebt.risk.contracts.FRAME_COLUMNS["CRIFIRCurve"] and SIMM_IR_TENORS.
CRIF_COLUMNS = ("RiskType", "Qualifier", "Bucket", "Label1", "Label2", "Amount", "AmountCurrency")
SIMM_IR_TENORS = ("2w", "1m", "3m", "6m", "1y", "2y", "3y", "5y", "10y", "15y", "20y", "30y")  # CRIF pillars, lower case


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


def at(curve, value_date: Optional[date]):
    """`curve` (a ToyCurve) re-anchored at `value_date`, its zero rate and csa kept: another date's
    market seen from the pricing date, no time passed. Under a CloseMarket override the market is
    another date's, so a value that must not carry (npv, PnlExplain) is taken on it re-anchored at
    `pricebt_date` (DEV-M1, IR_RISK_DESIGN section 8). None or the curve's own date: unchanged."""
    return curve if value_date is None else dataclasses.replace(curve, ref_date=value_date)


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


def explain_rows(ccy: str, parts: dict, total: float) -> list:
    """PnlExplain buckets as per-row dicts (IR_RISK_DESIGN R2-14): one row per risk factor
    (`mkt_type` -> value, `mkt_asset` the currency as gs's IR rows), then `CROSSES` = the rest of
    the full revaluation `total` (gs: no asset on that row, no time row at all)."""
    rows = [{"mkt_type": k, "mkt_asset": ccy, "value": v} for k, v in parts.items()]
    return rows + [{"mkt_type": "CROSSES", "value": total - sum(parts.values())}]


def empty_cashflows() -> pd.DataFrame:
    """A total-return Price never drops a paid flow, so there is nothing left to drop (R2-6)."""
    return pd.DataFrame(columns=list(CASHFLOW_COLUMNS))


def crif_frame(ccy: str, buckets: dict) -> pd.DataFrame:
    """CRIFIRCurve rows (IR_STRICT_CONTRACT R3-1) from a single-trade delta ladder `{pillar: ccy per
    +1bp}`, one row per pillar, so sum(Amount) == sum(ladder). `{}` (a dead instrument) gives an
    empty frame with the columns. The toy has one curve, a regular-vol currency: Bucket "1", OIS."""
    rows = [("Risk_IRCurve", ccy, "1", p, "OIS", float(a), ccy) for p, a in buckets.items()]
    return pd.DataFrame(rows, columns=list(CRIF_COLUMNS))


# --------------------------------------------------------------------- the toy swap (toylib.rates.ToySwap)


def _par_bp(curve, trade) -> float:
    return tr._par_rate(curve, trade.effective_date, trade.termination_date) * 1e4


def _dead(market, trade) -> bool:
    """On or after the final date every flow is paid: each sensitivity is 0.0 (IR_RISK_DESIGN
    section 2.2 dead-instrument rule); levels continue."""
    return market.ref_date >= trade.termination_date


def npv(market, trade, value_date: Optional[date] = None) -> float:
    """tr.npv valued on `value_date` (the config passes `pricebt_date`: see `at`)."""
    return tr.npv(at(market, value_date), trade)


def delta(market, trade) -> float:
    """IRDelta scalar: total derivative of npv w.r.t. the swap's own par rate, ccy per bp. Equals
    the annuity pv01 (`tr.pv01`) only at the money (R2-1)."""
    return 0.0 if _dead(market, trade) else greeks_on(market, lambda c: tr.npv(c, trade), lambda c: _par_bp(c, trade))[0]


def ir_gamma(market, trade) -> float:
    """IRGammaParallel: d2 npv / d par^2 per bp^2 by the chain rule (R14 gamma finding)."""
    return 0.0 if _dead(market, trade) else greeks_on(market, lambda c: tr.npv(c, trade), lambda c: _par_bp(c, trade))[1]


def discount_delta(market, trade) -> float:
    return 0.0 if _dead(market, trade) else discount_delta_on(market, lambda c: tr.npv(c, trade))


def theta_1d(market, trade) -> float:
    """pricebt DEV-I15: one calendar day, forwards fixed, ccy per day. The toy npv never drops a
    paid coupon (total return), so there is no cash term."""
    return 0.0 if _dead(market, trade) else tr.npv(_TranslatedCurve(market, 1), trade) - tr.npv(market, trade)


def expiry_in_years(market, trade) -> float:
    return years(market.ref_date, trade.termination_date)  # pricebt DEV-I17: a swap's final date


def spot_rate(market, trade) -> float:
    return spot_par_bp(market, trade.termination_date)


def annuity(market, trade) -> float:
    """N * A: PV of the fixed leg paying 1.0 p.a. (1e4 x tr.pv01), holder-signed."""
    return 0.0 if _dead(market, trade) else trade.notional * tr._annuity(market, trade.effective_date, trade.termination_date)


# IR_STRICT_CONTRACT R3-1 (DEV-I19). `value_date` as `npv`: a config passes the same argument to these
# as to its Price function, so FairPremium/ForwardPrice/PremiumCents stay identities of its Price.


def par_spread(market, trade) -> float:
    """ParSpread, bp: the floating-leg spread making npv 0. The toy floating leg is DF(eff) -
    DF(mat) on the fixed leg's annual schedule, so it is K - par. Same for payer and receiver;
    continues after the final date (the total-return par rate does)."""
    return trade.fixed_rate * 1e4 - _par_bp(market, trade)


def fair_premium(market, trade, value_date: Optional[date] = None) -> float:
    """FairPremium = npv / DF(premium settlement); the toy has no spot lag (settles on the pricing
    date, DF 1), so it is npv."""
    return npv(market, trade, value_date)


def forward_price(market, trade, value_date: Optional[date] = None) -> float:
    """ForwardPrice = npv / DF(final date), the date ExpiryInYears counts to; npv on or after it
    (a toy DF of a past date is > 1)."""
    curve = at(market, value_date)
    pv = tr.npv(curve, trade)
    return pv if curve.ref_date >= trade.termination_date else pv / curve.discount_factor(trade.termination_date)


def premium_cents(market, trade, value_date: Optional[date] = None) -> float:
    """PremiumCents, bp of |notional|: npv / |N| * 1e4. Intensive."""
    return npv(market, trade, value_date) / abs(trade.notional) * 1e4


def local_annuity_in_cents(market, trade) -> float:
    """LocalAnnuityInCents, decimal: Annuity / |N| (holder-signed, a 10y payer ~ +8.5). Intensive."""
    return annuity(market, trade) / abs(trade.notional)


def compounded_fixed_rate(market, trade) -> float:
    """CompoundedFixedRate, bp: the toy fixed leg is annual, so (1 + K)^1 - 1 = K."""
    return trade.fixed_rate * 1e4


def crif_ir_curve(market, trade) -> pd.DataFrame:
    """CRIFIRCurve from this trade's own-rate delta ladder on the SIMM pillars (the full configs'
    IRDelta bucketed); empty once dead."""
    return crif_frame(market.ccy, {} if _dead(market, trade) else delta_ladder(market, [trade], [1.0], SIMM_IR_TENORS))


def crif_ir_curve_pv01(market, trade) -> pd.DataFrame:
    """CRIFIRCurve from the annuity-pv01 ladder (`tr.delta_ladder`), for the configs whose IRDelta is
    the annuity dv01 (toy_usd_irs, toy_eur_irs), so sum(Amount) is their ladder's sum. Empty once
    dead (the contract's frame rule), although `tr.pv01` itself never goes to 0."""
    return crif_frame(market.ccy, {} if _dead(market, trade) else tr.delta_ladder(market, [trade], [1.0], SIMM_IR_TENORS))


def pnl_explain(market, market_to, trades, weights, value_date: Optional[date] = None) -> list:
    """PnlExplain by full revaluation from `market` to `market_to`, weighted, both on `value_date`
    (the pricing date: no time passes, `at`): the curve is the swap's only factor, so IR is the
    whole move and CROSSES is 0."""
    m0, m1 = at(market, value_date), at(market_to, value_date)
    total = sum(w * (tr.npv(m1, t) - tr.npv(m0, t)) for t, w in zip(trades, weights))
    return explain_rows(market.ccy, {"IR": total}, total)


def delta_ladder(market, trades, weights, tenors) -> dict:
    return ladder(market.ref_date, trades, weights, tenors, lambda t: delta(market, t), lambda t: t.termination_date)


def gamma_ladder(market, trades, weights, tenors) -> dict:
    """pricebt DEV-I13: a diagonal ladder (the scalar at its nearest pillar), not gs's cross-gamma."""
    return ladder(market.ref_date, trades, weights, tenors, lambda t: ir_gamma(market, t), lambda t: t.termination_date)
