"""RLBond: a fixed-coupon UST marked from a quote (clean price or ytm), with coupon cash sweep, repo financing and yield-space layers, and the `bond` Kit.

The factory takes the neutral terms (side, security, notional, extras `repo`) and the shared conventions block; the security (an id or an on-the-run alias such as
CT10) is resolved through the snapshot's quote set and reported as a resolved term (spec T4). Conventions: rateslib bond built with `spec='us_gb_tsy'`, `ex_div=0`, its
payment dates adjusted on the snapshot's calendar; ONE settlement (the pricer's reference date at midnight, naive datetime) for quote->ytm and ytm->price; a coupon
paid on D is inside V(D) (added back to the price, which excludes it on its own payment date) and outside V(D+1); cash window [t0, t1).
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, Mapping, Optional

import pandas as pd

from ...contracts.spec import Built, Kit
from ...errors import ConfigError, MarketDataUnavailable
from ...pricable import MarkContext, Valuation
from . import _compat as C
from ._compat import rl
from .conventions import bond_conventions
from .pricer import BondRef

_STATE = "rl_bond_state"


def make_ust(ref: BondRef, notional: float = -1e6, cal: Any = None) -> Any:
    """rateslib FixedRateBond for a UST. rateslib notional < 0 = long. `cal` is the snapshot's calendar object (payment dates are adjusted on it)."""
    extra = {} if cal is None else {"calendar": cal}
    return rl.FixedRateBond(
        effective=C.to_dt(ref.issue_date), termination=C.to_dt(ref.maturity_date), spec="us_gb_tsy", fixed_rate=float(ref.coupon), notional=notional, ex_div=0, **extra
    )


def accrual_ends(inst: Any) -> frozenset:
    """Unadjusted accrual end dates of the coupon periods (the final principal `Cashflow` has no period)."""
    return frozenset(C.to_date(p.period_params.end) for p in inst.leg1.periods if hasattr(p, "period_params"))


def ytm_from_clean(inst: Any, clean: float, settle: Any, *, market: bool = True) -> float:
    """ytm (percent) of a `make_ust` bond from a clean price at `settle`. `market=True` (market-convention clean prices such as
    FedInvest's) is correct ON coupon dates (see `RLBond.mark`): on an accrual end date accrued is 0, so the clean price is solved as the
    (coupon-excluding) dirty price. `market=False` reproduces rateslib's own clean convention."""
    on_boundary = market and C.to_date(settle) in accrual_ends(inst)
    return C.finite(inst.ytm(float(clean), C.to_dt(settle), dirty=on_boundary))


def market_clean(pricer: Any) -> bool:
    """True when the pricer declares that its clean quotes follow the MARKET convention (accrued 0 on a coupon date): attribute
    `CLEAN_CONVENTION == 'market'`, set by `wrap` for a quote panel whose provider quotes market clean prices. Other pricers (e.g. the synthetic market, which generates its
    clean prices with rateslib's `price(y, D, dirty=False)`) keep rateslib's convention."""
    return getattr(pricer, "CLEAN_CONVENTION", "rateslib") == "market"


class RLBond:
    """One unit = `notional` face of one UST. side 'long' pays repo and has dv01 < 0; 'short' earns repo. `value` is holder P&L.

    repo (the `extras.repo` term): {gc_rate: percent scalar | 'pricer', specialness_bps: 0, haircut: 0.0}; gc_rate None -> no
    financing. `gc_rate: pricer` reads the last PUBLISHED overnight fixing strictly before the pricer's reference date from
    `pricer.fixings` (a SOFR proxy for GC repo); a fixing older than `MAX_FIXING_AGE_DAYS` calendar days raises instead of being carried forward silently.
    Financing accrues on ELAPSED SECONDS (ACT/360) on the previous mark's dirty value; coupons are date-granular.
    """

    asset_class = "bond"
    LAYERS = ("carry", "roll", "delta", "convexity")
    GC_FROM_PRICER = "pricer"
    MAX_FIXING_AGE_DAYS = 10

    def __init__(self, ref: BondRef, sign: int, notional: float, *, repo: Optional[Mapping[str, Any]] = None, cal: Any = None):
        if sign not in (1, -1):
            raise ConfigError(f"sign must be +1 (long) or -1 (short), got {sign!r}", code="BOND")
        if notional <= 0:
            raise ConfigError("notional (face) is unsigned and must be > 0 (direction is `side`)", code="BOND")
        self.ref, self.notional, self.sign = ref, float(notional), int(sign)
        self.cusip = ref.cusip
        self.repo = dict(repo or {})
        unknown = set(self.repo) - {"gc_rate", "specialness_bps", "haircut"}
        if unknown:
            raise ConfigError(f"unknown repo keys {sorted(unknown)}; the rateslib adapter understands gc_rate, specialness_bps, haircut", code="CFG-EXTRAS")
        gc = self.repo.get("gc_rate")
        if isinstance(gc, str) and gc != self.GC_FROM_PRICER:
            raise ConfigError(f"repo gc_rate must be a percent number or {self.GC_FROM_PRICER!r}, got {gc!r}", code="BOND")
        self._inst = make_ust(ref, -self.sign * self.notional, cal)
        periods = list(self._inst.leg1.periods)
        self._pay = [(p.settlement_params.payment, p) for p in periods]
        self.last_payment = max(d for d, _ in self._pay)
        self._accrual_ends = accrual_ends(self._inst)

    # ------------------------------------------------------------------ quote pipeline
    def _settle(self, pricer: Any) -> dt.datetime:
        return C.to_dt(pricer.reference_date)

    def _priced(self, pricer: Any) -> bool:
        """True while a quote is needed: strictly before the last payment date (rateslib cannot price on/after it)."""
        return C.to_dt(pricer.reference_date) < self.last_payment

    def mark(self, pricer: Any) -> Dict[str, float]:
        """quote -> ytm -> dirty at ONE settlement. Prices per 100 face; on/after the last payment date there is nothing left to price
        (dirty 0, ytm NaN) and the final flows live in `_due`.

        Coupon dates: with ex_div=0, rateslib 2.7.1's `price(y, D, dirty=True)` at a settlement ON an accrual end date D already EXCLUDES
        the coupon paid on D (the seller keeps it), but `accrued(D)` returns the FULL coupon, so its clean price at D is one coupon too
        low and `ytm(clean, D, dirty=False)` is badly wrong (measured: 2.705% instead of 2.88% for the 2.75% Feb-2028 note on
        2018-08-15, making V(D) one coupon too high). For a pricer whose clean quotes follow the MARKET convention (`market_clean`:
        FedInvest/Webull quote pricers) accrued is therefore 0 on an accrual end date (the new period starts) and a clean quote is solved
        as a dirty price (clean + 0); every other date, and every rateslib-convention pricer (the synthetic market), is unchanged."""
        settle = self._settle(pricer)
        if settle >= self.last_payment:
            return {"ytm": float("nan"), "dirty": 0.0, "accrued": float("nan"), "clean": float("nan")}
        q = pricer.bond_quote(self.cusip)
        on_boundary = market_clean(pricer) and C.to_date(settle) in self._accrual_ends
        if q.clean is not None:
            y = C.finite(self._inst.ytm(q.clean, settle, dirty=on_boundary))
        else:
            y = float(q.ytm)
        dirty = C.finite(self._inst.price(y, settle, dirty=True))
        acc = 0.0 if on_boundary else C.finite(self._inst.accrued(settle))
        return {"ytm": y, "dirty": dirty, "accrued": acc, "clean": dirty - acc}

    def _mv(self, dirty: float) -> float:
        """Signed market value (holder view) of one unit."""
        return self.sign * self.notional / 100.0 * dirty

    def _due(self, pricer: Any) -> float:
        """Holder-signed flows paid ON the reference date. price(dirty) at a coupon-date settlement already excludes them (seller keeps
        the coupon), so V adds them back: a flow paid on D is inside V(D) and swept as cash at D+1, exactly like a swap."""
        d = C.to_dt(pricer.reference_date)
        return C.finite(sum(float(p.cashflow()) for pd_, p in self._pay if pd_ == d))

    def pv_of(self, pricer: Any, m: Optional[Dict[str, float]] = None) -> float:
        """Per-unit mark: priced dirty value plus flows due today."""
        m = m or self.mark(pricer)
        return self._mv(m["dirty"]) + self._due(pricer)

    def _matured(self, pricer: Any) -> bool:
        return C.to_dt(pricer.reference_date) > self.last_payment

    def _cash(self, t0: dt.datetime, t1: dt.datetime) -> float:
        """Holder-signed coupons/redemption with payment date in [t0, t1); rateslib notional < 0 (long) yields positive flows."""
        return C.finite(sum(float(p.cashflow()) for d, p in self._pay if t0 <= d < t1))

    def repo_rate(self, pricer: Any) -> float:
        """Percent, known at the start of an interval: last GC fixing strictly before the pricer's reference date, less specialness."""
        gc = self.repo.get("gc_rate")
        if gc is None:
            return 0.0
        if isinstance(gc, str):
            gc = self._pricer_fixing(pricer)
        return float(gc) - float(self.repo.get("specialness_bps", 0.0)) / 100.0

    def _pricer_fixing(self, pricer: Any) -> float:
        """gc_rate 'pricer': the newest fixing (percent) the pricer publishes that is dated strictly before its reference date."""
        fx = getattr(pricer, "fixings", None)
        ts = getattr(pricer, "ts", None)
        if fx is None or len(fx) == 0:
            raise MarketDataUnavailable(ts, {}, "repo gc_rate 'pricer': the pricer carries no published fixings (give the quote MDP `fixings: auto` or a curve_mdp)")
        ref = pd.Timestamp(C.to_dt(pricer.reference_date))
        s = fx[fx.index < ref]
        if len(s) == 0:
            raise MarketDataUnavailable(ts, {}, f"repo gc_rate 'pricer': no fixing before {ref.date()}")
        age = (ref - pd.Timestamp(s.index[-1])).days
        if age > self.MAX_FIXING_AGE_DAYS:
            raise MarketDataUnavailable(ts, {}, f"repo gc_rate 'pricer': newest fixing {s.index[-1].date()} is {age} days before {ref.date()} (stale)")
        return float(s.iloc[-1])

    def financing(self, prev: Any, ts: pd.Timestamp, prev_ts: pd.Timestamp) -> float:
        """Per-unit funding over (prev_ts, ts]: long pays, short earns, on the previous dirty value net of haircut."""
        if self.repo.get("gc_rate") is None or self._matured(prev):
            return 0.0
        seconds = (pd.Timestamp(ts) - pd.Timestamp(prev_ts)).total_seconds()
        if seconds < 0:
            raise ValueError("financing needs prev_ts <= ts")
        financed = self.notional / 100.0 * self.mark(prev)["dirty"] * (1.0 - float(self.repo.get("haircut", 0.0)))
        return -self.sign * financed * self.repo_rate(prev) / 100.0 * seconds / (360.0 * 86400.0)

    # ------------------------------------------------------------------ value
    def value(self, ctx: MarkContext) -> Valuation:
        p = ctx.pricer
        m = self.mark(p)
        pv, meta = self.pv_of(p, m), {k: m[k] for k in ("ytm", "clean", "dirty", "accrued")}
        cash = fin = 0.0
        prev = ctx.prev_pricer
        if prev is not None:
            t0, t1 = C.to_dt(prev.reference_date), C.to_dt(p.reference_date)
            if t0 < t1:
                cash = self._cash(t0, t1)
            fin = self.financing(prev, ctx.ts, ctx.prev_ts)
        return Valuation(ctx.ts, pv, cash, fin, meta)

    # ------------------------------------------------------------------ quote-arg schema methods
    def ytm(self, *, ctx: MarkContext) -> float:
        return self._state(ctx)["ytm"]

    def clean_price(self, *, ctx: MarkContext) -> float:
        return self._state(ctx)["clean"]

    def dirty_price(self, *, ctx: MarkContext) -> float:
        return self._state(ctx)["dirty"]

    def accrued(self, *, ctx: MarkContext) -> float:
        return self._state(ctx)["accrued"]

    def duration(self, *, ctx: MarkContext) -> float:
        """Modified duration in years."""
        s = self._state(ctx)
        return C.finite(self._inst.duration(s["ytm"], self._settle(ctx.pricer), "modified"))

    def dv01(self, *, ctx: MarkContext) -> float:
        """Currency P&L for a +1bp move in the bond's yield (negative for a long)."""
        s = self._state(ctx)
        return -self.sign * self.notional * self._risk(s["ytm"], self._settle(ctx.pricer)) / 1e4

    def gamma(self, *, ctx: MarkContext) -> float:
        """d2 PV / dbp^2 in currency."""
        s = self._state(ctx)
        return self.sign * self.notional / 100.0 * self._conv(s["ytm"], self._settle(ctx.pricer)) * 1e-4

    def cashflows(self, *, curves: Any = None, ctx: Optional[MarkContext] = None) -> pd.DataFrame:
        """Undiscounted holder-signed cash flows of one unit."""
        rows = [{"Type": type(p).__name__, "Payment": d, "Cashflow": float(p.cashflow())} for d, p in self._pay]
        return pd.DataFrame(rows, columns=["Type", "Payment", "Cashflow"])

    def _risk(self, y: float, settle: dt.datetime) -> float:
        return C.finite(self._inst.duration(y, settle, "risk"))

    def _conv(self, y: float, settle: dt.datetime) -> float:
        return C.finite(self._inst.convexity(y, settle, "risk"))

    def _state(self, ctx: MarkContext) -> Dict[str, float]:
        if not self._priced(ctx.pricer):
            raise ConfigError(f"{self.cusip} is on/after its last payment date; no quote-based measures", code="BOND")
        key = (_STATE, id(self))
        if key not in ctx.cache:
            ctx.cache[key] = self.mark(ctx.pricer)
        return ctx.cache[key]

    # ------------------------------------------------------------------ layers (per unit, interval P&L over (prev, ts])
    def _terms(self, ctx: MarkContext) -> Dict[str, float]:
        key = ("rl_bond_terms", id(self))
        if key in ctx.cache:
            return ctx.cache[key]
        prev, p = ctx.prev_pricer, ctx.pricer
        if prev is None:
            raise ConfigError("layers need a previous pricer (ctx.prev_pricer)", code="LAYER")
        t0, t1 = C.to_dt(prev.reference_date), C.to_dt(p.reference_date)
        if self._matured(prev):
            out = dict.fromkeys(("carry", "roll", "delta", "convexity"), 0.0)
        else:
            m0 = self.mark(prev)
            fin = self.financing(prev, ctx.ts, ctx.prev_ts)
            cash = self._cash(t0, t1) if t0 < t1 else 0.0
            if not (self._priced(prev) and self._priced(p)):
                out = {"carry": self.pv_of(p) + cash - self.pv_of(prev, m0) + fin, "roll": 0.0, "delta": 0.0, "convexity": 0.0}
            else:
                y0, y1 = m0["ytm"], self.mark(p)["ytm"]
                V0 = self.pv_of(prev, m0)
                V_cy = self._mv(C.finite(self._inst.price(y0, t1, dirty=True))) + self._due(p)
                risk, conv = self._risk(y0, t1), self._conv(y0, t1)
                y_hold = y0 + self._pulldown(prev, p)
                k = self.sign * self.notional / 100.0
                out = {
                    "carry": cash + V_cy - V0 + fin,
                    "roll": -k * risk * (y_hold - y0),
                    "delta": -k * risk * (y1 - y_hold),
                    "convexity": 0.5 * k * conv * (y1 - y0) ** 2,
                }
        ctx.cache[key] = out
        return out

    def _pulldown(self, prev: Any, p: Any) -> float:
        """Yield change (percent) from ageing down the pricer's ytm curve over the elapsed calendar time; 0 without a curve."""
        yc = prev.ytm_curve() if hasattr(prev, "ytm_curve") else None
        if yc is None:
            return 0.0
        ttm0 = (self.ref.maturity_date - prev.reference_date).days / 365.25
        dt_years = (p.reference_date - prev.reference_date).days / 365.25
        return float(yc(max(ttm0 - dt_years, 1e-3)) - yc(ttm0))

    def carry(self, *, ctx: MarkContext) -> float:
        return self._terms(ctx)["carry"]

    def roll(self, *, ctx: MarkContext) -> float:
        return self._terms(ctx)["roll"]

    def delta(self, *, ctx: MarkContext) -> float:
        return self._terms(ctx)["delta"]

    def convexity(self, *, ctx: MarkContext) -> float:
        return self._terms(ctx)["convexity"]


# ------------------------------------------------------------------------------ factory and kit
def bond_factory(pricer: Any, ts: Any, *, terms: Mapping[str, Any], conventions: Mapping[str, Any]) -> Built:
    """`(pricer, ts, *, terms, conventions) -> Built`: the security (id or alias) is resolved on the snapshot and reported (spec T4)."""
    from .pricer import RLCurvePricer

    if not isinstance(pricer, RLCurvePricer):
        raise ConfigError(f"the market pricer is a {type(pricer).__name__}, not a rateslib pricer: set `wrap` on the pricer role to pricebt.contrib.rateslib:wrap", code="CFG-WRAP")
    conv = bond_conventions(conventions)
    ref = pricer.bond(terms["security"])
    extras = dict(terms.get("extras") or {})
    unknown = sorted(set(extras) - {"repo"})
    if unknown:
        raise ConfigError(f"the rateslib adapter does not understand the bond extras {unknown}; it understands ['repo']", code="CFG-EXTRAS")
    b = RLBond(ref, int(terms["direction"]), float(terms["notional"]), repo=extras.get("repo"), cal=pricer.calendar(conv.calendar))
    return Built(b, {"security": ref.cusip, "coupon": ref.coupon, "maturity": ref.maturity_date, "notional": b.notional})


def _bind(method: str) -> Dict[str, Any]:
    return {"target": {"method": method}, "kwargs": {"ctx": "@ctx"}}


BOND_BIND: Dict[str, Any] = {name: _bind(method) for name, method in (
    ("value", "value"), ("dv01", "dv01"), ("gamma", "gamma"), ("rate", "ytm"), ("ytm", "ytm"), ("duration", "duration"), ("accrued", "accrued"),
    ("carry", "carry"), ("roll", "roll"), ("delta", "delta"), ("convexity", "convexity"))}

bond = Kit(factory=bond_factory, asset_class="bond", default_bind=BOND_BIND, cls=RLBond, doc="Fixed-coupon US Treasury on rateslib (module docstring: conventions and layer definitions).")
