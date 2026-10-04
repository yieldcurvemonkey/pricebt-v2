"""Toy fixed-coupon bond world with repo financing, for tests only (never shipped; docs/v2/BOND_DESIGN.md
section 5, docs/v2/IR_RISK_DESIGN.md section 6.3). Import as `toylib.bond` (see toylib/__init__.py).

A tiny bond master keyed by identifier; the market is `SimpleNamespace(curve=ToyCurve, spread=s(d),
repo=gc(d))`: toylib.rates' flat USD curve (HOLES included), a deterministic sinusoidal spread
(~30bp +- 10bp) and the GC repo fixing gc(d) = zero rate - 15bp (decimal, simple ACT/360).
Business days are weekdays (no holiday calendar); standard settlement is T+1, settle(t) = the next
weekday after t.

Price is the holder-signed settlement-date value: the flows paid after s = settle(t), each worth
CF * DF(s, p) * exp(-spread * tau(s, p)), tau = days/365, not discounted back to t. A flow drops on
the first trade date whose settlement reaches it (the last weekday before it): Cashflows lists the
flows still in Price with that drop date as payment_date. The own rate is the yield y
(continuously compounded, ACT/365 from s, solved by Newton), in bp; on this flat world y = z + s.

Financing (pinned by `resolve_bond`): principal (1 - haircut) x Price(trade date), at overnight GC
minus the identifier's special (SPECIALS) or, for a term repo, that rate locked on the trade date;
simple interest ACT/360 on calendar days from settle(trade date).
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
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
SPECIALS = {"TOY 4.25 2034-11-15": 0.0020}  # identifier -> repo special (decimal below GC)
GC_SPREAD = 0.0015  # GC repo = the toy zero rate - 15bp
REPO_BASIS = 360.0  # USD repo: simple interest, ACT/360
REPO_TERMS = ("overnight", "term")
_SPREAD_PARAMS = {"USD": (0.0030, 0.0010, 200)}  # base, amplitude, period (business days)
CASHFLOW_COLUMNS = ir.CASHFLOW_COLUMNS + ("accrual_start_date", "accrual_end_date", "notional", "rate")
_DAY = timedelta(days=1)


def _spread(d: date, ccy: str) -> float:
    base, amp, period = _SPREAD_PARAMS[ccy]
    return base + amp * math.sin(2 * math.pi * tr.business_day_index(d) / period)


def gc(d: date, ccy: str = "USD") -> float:
    """The GC repo fixing of `d`, decimal, simple ACT/360."""
    return tr._zero_rate(d, ccy) - GC_SPREAD


def market(d: date, ccy: str, csa: Optional[str] = None):
    """SimpleNamespace(curve=ToyCurve, spread=float, repo=the GC fixing in force on d: d's own on a
    weekday, the last weekday's over a weekend), or None for a date in toylib.rates.HOLES."""
    curve = tr.market(d, ccy, csa)
    return None if curve is None else SimpleNamespace(curve=curve, spread=_spread(d, ccy), repo=gc(_weekday_on_or_before(d), ccy))


# --------------------------------------------------------------------- calendar (weekdays only)


def next_weekday(d: date) -> date:
    """The first weekday strictly after `d`."""
    d += _DAY
    while d.weekday() >= 5:
        d += _DAY
    return d


def settle(t: date) -> date:
    """Standard settlement of trade date `t`: T+1 weekday."""
    return next_weekday(t)


def _weekday_on_or_before(d: date) -> date:
    while d.weekday() >= 5:
        d -= _DAY
    return d


def drop_date(p: date) -> date:
    """The first trade date whose settlement reaches `p`: the last weekday strictly before it."""
    return _weekday_on_or_before(p - _DAY)


def horizon(s: date) -> date:
    """The carry horizon H: settlement + 1 calendar month, rolled forward to a weekday."""
    h = s + relativedelta(months=1)
    while h.weekday() >= 5:
        h += _DAY
    return h


# --------------------------------------------------------------------- resolve


def _as_date(d):
    if isinstance(d, str):
        return date.fromisoformat(d)
    return d.date() if isinstance(d, datetime) else d


def resolve_bond(market, kwargs: dict) -> dict:
    """Pin the identifier's terms, the trade date and its standard settlement; fold buy_sell x
    sign(size) into a signed face; pin the financing: haircut, repo term, the term rate (GC -
    special on the trade date) and the financed principal (1 - haircut) x Price(trade date) per
    unit trade, holder-signed. A settlement_date other than the standard one is refused."""
    ident = kwargs.get("identifier")
    if ident not in BONDS:
        raise ValueError(f"unknown toy bond identifier {ident!r}; known: {sorted(BONDS)}")
    bs = str(getattr(kwargs.get("buy_sell", "Buy"), "value", kwargs.get("buy_sell", "Buy"))).strip().lower()
    if bs not in ("buy", "sell"):
        raise ValueError(f"buy_sell must be Buy or Sell, got {kwargs.get('buy_sell')!r}")
    term = str(kwargs.get("repo_term")).strip().lower()
    if term not in REPO_TERMS:
        raise ValueError(f"repo_term must be one of {REPO_TERMS}, got {kwargs.get('repo_term')!r}")
    haircut = float(kwargs["repo_haircut"])
    if not 0.0 <= haircut < 1.0:
        raise ValueError(f"repo_haircut must be a decimal in [0, 1), got {haircut!r}")
    t = market.curve.ref_date
    s0 = settle(t)
    if kwargs.get("settlement_date") is not None and _as_date(kwargs["settlement_date"]) != s0:
        raise ValueError(f"non-standard settlement is not supported: settlement_date {kwargs['settlement_date']} != T+1 {s0}")
    size = float(kwargs["size"])
    coupon, maturity, freq = BONDS[ident]
    face = abs(size) * (1.0 if bs == "buy" else -1.0) * (1.0 if size >= 0 else -1.0)
    terms = {"identifier": ident, "coupon": coupon, "maturity": maturity, "frequency": freq, "face": face, "trade_date": t, "settle0": s0}
    terms.update(haircut=haircut, repo_term=term, term_rate=market.repo - SPECIALS.get(ident, 0.0))
    terms["principal"] = (1.0 - haircut) * _pv(market.curve, market.spread, terms)
    return terms


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


def _tau(a: date, b: date) -> float:
    return (b - a).days / 365.0


def _flow_pvs(curve, spread: float, trade):
    """Each flow still in Price, valued at settle(t) on `curve` (anchored at t) plus the spread."""
    s = settle(curve.ref_date)
    df_s = curve.discount_factor(s)
    return [a * curve.discount_factor(p) / df_s * math.exp(-spread * _tau(s, p)) for p, a, *_ in _flows(trade, s)]


def _pv(curve, spread: float, trade) -> float:
    return sum(_flow_pvs(curve, spread, trade))


def _at_yield(trade, s: date, y: float, power: int = 0) -> float:
    """sum over flows paid after s of tau^power CF exp(-y tau), tau from s: the settlement value at
    yield y (power 0) and its duration (1) and convexity (2) sums."""
    return sum(_tau(s, p) ** power * a * math.exp(-y * _tau(s, p)) for p, a, *_ in _flows(trade, s))


def _yield(curve, spread: float, trade) -> float:
    """y with Price = sum cf exp(-y tau(s, p)) (Newton). With no flow left, the yield of a one-day
    unit flow from s (z + s here), so the level stays finite and continuous after maturity (R2-7)."""
    s = settle(curve.ref_date)
    flows = [(_tau(s, p), a) for p, a, *_ in _flows(trade, s)]
    target = _pv(curve, spread, trade)
    if not flows:
        flows, target = [(1 / 365.0, 1.0)], curve.discount_factor(s + _DAY) / curve.discount_factor(s) * math.exp(-spread / 365.0)
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


def _on(market, value_date: Optional[date]):
    """`market` seen from `value_date` (the config passes `pricebt_date`): under a CloseMarket
    override, the override's curve re-anchored at the pricing date, so no coupon drops and nothing
    carries (toylib.irrisk.at)."""
    return SimpleNamespace(curve=ir.at(market.curve, value_date), spread=market.spread, repo=market.repo)


def _dead(market, trade) -> bool:
    return not _flows(trade, settle(market.curve.ref_date))


def _accrued_at(trade, x: date) -> float:
    """Holder-signed coupon accrued (ACT/ACT in the period) at settlement date `x`; 0 on a coupon
    date and after maturity."""
    periods = _periods(trade, x)
    if not periods:
        return 0.0
    start, end = periods[0]
    return trade["face"] * trade["coupon"] / trade["frequency"] * (x - start).days / (end - start).days


# --------------------------------------------------------------------- IR measure contract


def npv(market, trade, value_date: Optional[date] = None) -> float:
    """Price (and FairPremium): the settlement-date value, on `value_date`'s view of the market."""
    tr.EVAL_COUNTS["npv"] += 1
    m = _on(market, value_date)
    return _pv(m.curve, m.spread, trade)


def yield_bp(market, trade) -> float:
    """IRFwdRate and IRSpotRate: the yield to maturity in bp (pricebt DEV-I12)."""
    return _yield(market.curve, market.spread, trade) * 1e4


def delta(market, trade) -> float:
    """Total derivative along the parallel curve shift, spread fixed, per bp of yield (dy/dz = 1
    on this flat world, so it is the yield DV01). 0 when dead (every bumped Price is 0)."""
    return _yield_greeks(market, trade, _pv)[0]


def gamma(market, trade) -> float:
    return _yield_greeks(market, trade, _pv)[1]


def discount_delta(market, trade) -> float:
    # fixed coupons project nothing, so the discount-only bump is the whole-curve bump: `fwd` unused
    return ir.discount_delta_on(market.curve, lambda c, _fwd: _pv(c, market.spread, trade))


def yield_dv01(market, trade) -> float:
    """LightningDV01: analytic dPrice/dy per +1bp at the solved yield, tau from settlement."""
    s = settle(market.curve.ref_date)
    return -_at_yield(trade, s, _yield(market.curve, market.spread, trade), 1) * 1e-4


def theta_1d(market, trade) -> float:
    """Bond Theta (pricebt DEV-I15): over the step to the next weekday nb, at the SAME yield,
    [Price(nb) + flows Price drops in (t, nb] - Price(t)] / (nb - t).days, ccy per day."""
    t = market.curve.ref_date
    nb = next_weekday(t)
    s0, s1 = settle(t), settle(nb)
    y = _yield(market.curve, market.spread, trade)
    dropped = sum(a for p, a, *_ in _flows(trade, s0) if p <= s1)
    return (_at_yield(trade, s1, y) + dropped - _pv(market.curve, market.spread, trade)) / (nb - t).days


def expiry_in_years(market, trade) -> float:
    return ir.years(market.curve.ref_date, trade["maturity"])  # pricebt DEV-I17: to maturity


def spread_bp(market, trade) -> float:
    """LightningOAS (a bullet bond's Z-spread) and ParSpread: s in bp. On this flat single-curve toy
    the par spread is taken equal to the Z-spread."""
    return market.spread * 1e4


def annuity(market, trade) -> float:
    """PV of 1.0 p.a. on the coupon schedule still to pay, settlement-date value, per signed face."""
    c = market.curve
    s = settle(c.ref_date)
    return trade["face"] / trade["frequency"] * sum(c.discount_factor(p) / c.discount_factor(s) * math.exp(-market.spread * _tau(s, p)) for _start, p in _periods(trade, s))


def cashflows(market, trade) -> pd.DataFrame:
    """One row per flow still in Price; payment_date = the trade date Price drops it."""
    s = settle(market.curve.ref_date)
    rows = [(drop_date(p), a, market.curve.ccy, kind, st, e, trade["face"], trade["coupon"]) for p, a, kind, st, e in _flows(trade, s)]
    return pd.DataFrame(rows, columns=list(CASHFLOW_COLUMNS))


def premium_cents(market, trade, value_date: Optional[date] = None) -> float:
    """PremiumCents in pct: Price / |face| x 100. Intensive."""
    return 100.0 * npv(market, trade, value_date) / abs(trade["face"])


def local_annuity_in_cents(market, trade) -> float:
    """LocalAnnuityInCents, decimal: Annuity / |face|. Intensive."""
    return annuity(market, trade) / abs(trade["face"])


def compounded_fixed_rate(trade) -> float:
    """CompoundedFixedRate, bp: (1 + c/f)^f - 1. A trade term."""
    f = trade["frequency"]
    return ((1.0 + trade["coupon"] / f) ** f - 1.0) * 1e4


def crif_ir_curve(market, trade) -> pd.DataFrame:
    """CRIFIRCurve from this trade's own-rate delta ladder on the SIMM pillars; empty once dead."""
    return ir.crif_frame(market.curve.ccy, {} if _dead(market, trade) else delta_ladder(market, [trade], [1.0], ir.SIMM_IR_TENORS))


# --------------------------------------------------------------------- bond analytics (pricebt DEV-I20)


def accrued(market, trade, value_date: Optional[date] = None) -> float:
    """AccruedInterest: holder-signed accrued at the standard settlement date, ccy."""
    return _accrued_at(trade, settle(_on(market, value_date).curve.ref_date))


def dirty_price(market, trade, value_date: Optional[date] = None) -> float:
    return 100.0 * npv(market, trade, value_date) / trade["face"]


def clean_price(market, trade, value_date: Optional[date] = None) -> float:
    return dirty_price(market, trade, value_date) - 100.0 * accrued(market, trade, value_date) / trade["face"]


def _duration_sum(market, trade, power: int) -> float:
    price = _pv(market.curve, market.spread, trade)
    if price == 0.0:
        return 0.0
    s = settle(market.curve.ref_date)
    return _at_yield(trade, s, _yield(market.curve, market.spread, trade), power) / price


def modified_duration(market, trade) -> float:
    """sum tau CF e^{-y tau} / P, years (continuous compounding: Macaulay = modified); 0 dead."""
    return _duration_sum(market, trade, 1)


def convexity(market, trade) -> float:
    """sum tau^2 CF e^{-y tau} / P, years^2; 0 dead."""
    return _duration_sum(market, trade, 2)


def days_to_settlement(market, trade) -> float:
    t = market.curve.ref_date
    return float((settle(t) - t).days)


# --------------------------------------------------------------------- financing (pricebt DEV-I21)


def _repo(market, trade) -> float:
    """The funding rate in force on the pricing date, decimal: the pinned term rate, or overnight
    GC (market.repo) minus the identifier's special."""
    if trade["repo_term"] == "term":
        return trade["term_rate"]
    return market.repo - SPECIALS.get(trade["identifier"], 0.0)


def _fixing(trade, x: date, ccy: str) -> float:
    """The rate charged for calendar day `x`: the pinned term rate, or the overnight rate fixed on
    the last weekday on or before `x`."""
    if trade["repo_term"] == "term":
        return trade["term_rate"]
    return gc(_weekday_on_or_before(x), ccy) - SPECIALS.get(trade["identifier"], 0.0)


def repo_rate(market, trade) -> float:
    return _repo(market, trade) * 1e4


def repo_haircut(market, trade) -> float:
    return trade["haircut"]


def financing_to_date(market, trade) -> float:
    """-principal x sum over calendar days x in [settle0, min(settle(t), maturity)) of r(x) / 360:
    a long pays (<= 0), a short receives; 0 on the trade date."""
    end = min(settle(market.curve.ref_date), trade["maturity"])
    x, acc = trade["settle0"], 0.0
    while x < end:
        acc += _fixing(trade, x, market.curve.ccy) / REPO_BASIS
        x += _DAY
    return -trade["principal"] * acc


def forward_price(market, trade) -> float:
    """Price x (1 + r tau(s, H)) - sum over flows c in (s, H] of C x (1 + r tau(c, H)), tau ACT/360,
    r = RepoRate held flat to H; 0 when dead."""
    s = settle(market.curve.ref_date)
    flows = _flows(trade, s)
    if not flows:
        return 0.0
    h, r = horizon(s), _repo(market, trade)
    paid = sum(a * (1.0 + r * (h - p).days / REPO_BASIS) for p, a, *_ in flows if p <= h)
    return _pv(market.curve, market.spread, trade) * (1.0 + r * (h - s).days / REPO_BASIS) - paid


def carry(market, trade) -> float:
    """(Price - AI(s)) - (ForwardPrice - AI(H)); 0 when dead."""
    if _dead(market, trade):
        return 0.0
    s = settle(market.curve.ref_date)
    clean_now = _pv(market.curve, market.spread, trade) - _accrued_at(trade, s)
    return clean_now - (forward_price(market, trade) - _accrued_at(trade, horizon(s)))


def roll_down(market, trade) -> float:
    """Clean value at H on the rolled-down curve (each flow p discounted from H by the curve's own
    DF between t and t + (p - H), spread held) minus the clean value now; 0 when dead."""
    if _dead(market, trade):
        return 0.0
    c, sp = market.curve, market.spread
    t = c.ref_date
    s = settle(t)
    h = horizon(s)
    at_h = sum(a * c.discount_factor(t + (p - h)) / c.discount_factor(t) * math.exp(-sp * _tau(h, p)) for p, a, *_ in _flows(trade, h))
    return at_h - _accrued_at(trade, h) - (_pv(c, sp, trade) - _accrued_at(trade, s))


# --------------------------------------------------------------------- explain and ladders


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
    for i, (p, *_rest) in enumerate(_flows(trade, settle(t))):
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
