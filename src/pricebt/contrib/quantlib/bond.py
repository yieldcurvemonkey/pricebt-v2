"""QLBond: a fixed-coupon US Treasury marked from a quote with QuantLib, and the `bond` Kit for pricebt's `bond` schema.

Conventions (docs/design/11-quantlib-conventions.md section 7): semi-annual coupons on UNADJUSTED month-end/short-front-stub dates (`Schedule(..., Backward, eom=True)` on a `NullCalendar`),
ACT/ACT ICMA, the Treasury yield rule `SimpleThenCompounded`, ONE settlement (the pricer's reference date) for quote -> yield and yield -> price. Coupons are PAID on the next business
day of the snapshot calendar; that date is adapter arithmetic OUTSIDE the yield math (QuantLib's yield math discounts at the payment date). A coupon paid on D is inside V(D) (the
dirty price already excludes it, `due` adds it back) and is swept as cash at the next mark; cash window [t0, t1).

Units: coupon, yield (`rate`, `ytm`) PERCENT; prices per 100 face; dv01 currency per +1bp, gamma currency per bp^2, per unit as built with its face amount; a long bond has NEGATIVE dv01.
`accrued` is 0 on a coupon date: QuantLib implements the market clean convention natively (`accruedAmount(coupon date) == 0`).

Layers (per unit over (t0, t1], yield space; k = side * face / 100, y0/y1 the quoted yields, risk/conv at (y0, t1)):
carry = cash + V(y0 held, settle t1) - V0 + financing; roll = -k * risk * (y_hold - y0) with y_hold the yield after ageing down the on-the-run curve (0 without one); delta = -k * risk * (y1 - y_hold);
convexity = 1/2 k conv (y1 - y0)^2. Financing is ACT/360 on ELAPSED SECONDS on the previous mark's dirty value.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any, Callable, Dict, Mapping, Optional

import numpy as np
import pandas as pd

from ...contracts.spec import Built, Kit
from ...errors import ConfigError, MarketDataUnavailable
from ...pricable import MarkContext, Valuation
from . import _compat as C
from .conventions import BondConv, bond_conventions
from .wrap import QLPricer

ql = C.ql

MAX_FIXING_AGE_DAYS = 10
_H_RISK = 0.005   # percent (0.5bp): 4th-order central stencil
_H_CONV = 0.01    # percent (1bp)
_REPO_KEYS = {"gc_rate", "specialness_bps", "haircut"}
_CT = re.compile(r"^CT(\d+)$")


def _ql(p: Any) -> QLPricer:
    if not isinstance(p, QLPricer):
        raise ConfigError(f"the market pricer is a {type(p).__name__}, not a QLPricer: set `wrap` on the pricer role to pricebt.contrib.quantlib:wrap", code="CFG-WRAP")
    return p


class _Inst:
    """The QuantLib bond of one call plus its date tables. Never stored on a pricable."""

    def __init__(self, b: "QLBond", cal: "ql.Calendar"):
        conv = b.conv
        sched = ql.Schedule(C.qd(b.issue), C.qd(b.maturity), ql.Period(ql.Semiannual), ql.NullCalendar(), ql.Unadjusted, ql.Unadjusted, ql.DateGeneration.Backward, conv.end_of_month)
        self.dc = ql.ActualActual(ql.ActualActual.ISMA, sched)
        self.bond = ql.FixedRateBond(0, 100.0, sched, [b.coupon / 100.0], self.dc, ql.Unadjusted, 100.0, C.qd(b.issue))
        flows = [(C.pd_(c.date()), float(c.amount())) for c in self.bond.cashflows()]  # per 100 face; dates are the UNADJUSTED coupon dates
        self.ends = frozenset(d for d, _ in flows)
        self.pay = [(C.pd_(cal.adjust(C.qd(d), ql.Following)), a) for d, a in flows]  # paid on the next business day: adapter arithmetic, outside the yield math
        self.last_payment = max(d for d, _ in self.pay)

    def dirty(self, y_percent: float, settle: dt.date) -> float:
        return float(self.bond.dirtyPrice(y_percent / 100.0, self.dc, ql.SimpleThenCompounded, ql.Semiannual, C.qd(settle)))

    def accrued(self, settle: dt.date) -> float:
        return float(self.bond.accruedAmount(C.qd(settle)))

    def yield_from_clean(self, clean: float, settle: dt.date) -> float:
        return 100.0 * float(self.bond.bondYield(ql.BondPrice(clean, ql.BondPrice.Clean), self.dc, ql.SimpleThenCompounded, ql.Semiannual, C.qd(settle), 1e-13, 200, 0.04))

    def risk(self, y: float, settle: dt.date) -> float:
        """-dP/dy per 100 face per 1 percentage point of yield (4th-order central difference of the dirty price)."""
        h, p = _H_RISK, self.dirty
        return -(8.0 * (p(y + h, settle) - p(y - h, settle)) - (p(y + 2 * h, settle) - p(y - 2 * h, settle))) / (12.0 * h)

    def conv(self, y: float, settle: dt.date) -> float:
        """d2P/dy2 per 100 face per (1 percentage point)^2."""
        h, p = _H_CONV, self.dirty
        return (-p(y + 2 * h, settle) + 16.0 * p(y + h, settle) - 30.0 * p(y, settle) + 16.0 * p(y - h, settle) - p(y - 2 * h, settle)) / (12.0 * h * h)


class QLBond:
    """One unit = `notional` face of one security. `sign` +1 = long (buy). `repo`: {gc_rate: percent | 'pricer', specialness_bps, haircut}; without `gc_rate` there is no financing."""

    asset_class = "bond"

    def __init__(self, *, security: str, coupon: float, issue: dt.date, maturity: dt.date, sign: int, notional: float, repo: Optional[Mapping[str, Any]], conv: BondConv):
        if sign not in (1, -1):
            raise ConfigError(f"sign must be +1 (buy) or -1 (sell), got {sign!r}", code="BOND")
        if not notional > 0:
            raise ConfigError("notional (face) is unsigned and must be > 0 (direction is `side`)", code="BOND")
        self.security, self.coupon = str(security), float(coupon)
        self.issue, self.maturity = C.to_date(issue), C.to_date(maturity)
        self.sign, self.notional, self.conv = int(sign), float(notional), conv
        self.repo = dict(repo or {})
        unknown = set(self.repo) - _REPO_KEYS
        if unknown:
            raise ConfigError(f"unknown repo keys {sorted(unknown)}; allowed {sorted(_REPO_KEYS)}", code="BOND")
        gc = self.repo.get("gc_rate")
        if gc is not None and not (isinstance(gc, str) and gc == "pricer") and (isinstance(gc, bool) or not isinstance(gc, (int, float))):
            raise ConfigError(f"repo gc_rate must be a percent number or 'pricer', got {gc!r}", code="BOND")

    @property
    def notional_amount(self) -> float:
        return self.notional

    # ------------------------------------------------------------------ pieces
    def _inst(self, p: QLPricer) -> _Inst:
        return p.memo(("ql_bond", self.security, self.coupon, self.issue, self.maturity, self.conv), lambda: _Inst(self, p.calendar(self.conv.calendar)))

    def _priced(self, p: QLPricer) -> bool:
        """True while a quote is needed: strictly before the last payment date."""
        return p.reference_date < self._inst(p).last_payment

    def _matured(self, p: QLPricer) -> bool:
        return p.reference_date > self._inst(p).last_payment

    def mark(self, p: QLPricer) -> Dict[str, float]:
        """quote -> yield -> dirty at ONE settlement (the reference date). Prices per 100 face; nothing left to price on/after the last payment date (dirty 0, yield NaN)."""
        inst, settle = self._inst(p), p.reference_date
        if settle >= inst.last_payment:
            return {"ytm": float("nan"), "dirty": 0.0, "accrued": float("nan"), "clean": float("nan")}
        q = p.quote(self.security)
        acc = inst.accrued(settle)  # 0 on a coupon date: the market convention
        if q.clean is not None:
            qs = p.snapshot.quotes
            if settle in inst.ends and qs is not None and qs.price_convention != "market":
                raise ConfigError(
                    f"{self.security}: a clean price on the coupon date {settle} needs the snapshot's price_convention 'market' (accrued 0 on a coupon date), got {qs.price_convention!r}", code="PRICE-CONVENTION")
            dirty = float(q.clean) + acc
            y = inst.yield_from_clean(float(q.clean), settle)
        else:
            y = float(q.ytm)
            dirty = inst.dirty(y, settle)
        return {"ytm": y, "dirty": dirty, "accrued": acc, "clean": dirty - acc}

    def _mv(self, dirty: float) -> float:
        return self.sign * self.notional / 100.0 * dirty

    def _due(self, p: QLPricer) -> float:
        """Holder-signed flows paid ON the reference date (the dirty price excludes them; V adds them back)."""
        d = p.reference_date
        return self.sign * self.notional / 100.0 * sum(a for pd_, a in self._inst(p).pay if pd_ == d)

    def _cash(self, p: QLPricer, t0: dt.date, t1: dt.date) -> float:
        """Holder-signed coupons/redemption with payment date in [t0, t1)."""
        return self.sign * self.notional / 100.0 * sum(a for pd_, a in self._inst(p).pay if t0 <= pd_ < t1)

    def pv_of(self, p: QLPricer, m: Optional[Dict[str, float]] = None) -> float:
        m = m or self.mark(p)
        return self._mv(m["dirty"]) + self._due(p)

    def repo_rate(self, p: QLPricer) -> float:
        """PERCENT, known at the start of an interval: the quoted GC rate, or the newest fixing dated strictly before the reference date (a fixing older than 10 days raises), less specialness."""
        gc = self.repo.get("gc_rate")
        if gc is None:
            return 0.0
        if isinstance(gc, str):
            got = p.fixings_percent_before(p.reference_date)
            if got is None:
                raise MarketDataUnavailable(p.ts, {}, f"repo gc_rate 'pricer': no published fixing before {p.reference_date}")
            age = (p.reference_date - got[0]).days
            if age > MAX_FIXING_AGE_DAYS:
                raise MarketDataUnavailable(p.ts, {}, f"repo gc_rate 'pricer': newest fixing {got[0]} is {age} days before {p.reference_date} (stale)")
            gc = got[1]
        return float(gc) - float(self.repo.get("specialness_bps", 0.0)) / 100.0

    def financing(self, prev: QLPricer, ts: Any, prev_ts: Any) -> float:
        """Per-unit funding over (prev_ts, ts]: a long pays, a short earns, ACT/360 on ELAPSED SECONDS on the previous dirty value net of haircut."""
        if self.repo.get("gc_rate") is None or self._matured(prev):
            return 0.0
        seconds = (pd.Timestamp(ts) - pd.Timestamp(prev_ts)).total_seconds()
        if seconds < 0:
            raise ValueError("financing needs prev_ts <= ts")
        financed = self.notional / 100.0 * self.mark(prev)["dirty"] * (1.0 - float(self.repo.get("haircut", 0.0)))
        return -self.sign * financed * self.repo_rate(prev) / 100.0 * seconds / (360.0 * 86400.0)

    # ------------------------------------------------------------------ value
    def value(self, *, ctx: MarkContext) -> Valuation:
        p = _ql(ctx.pricer)
        m = self.mark(p)
        pv = self.pv_of(p, m)
        cash = fin = 0.0
        prev = ctx.prev_pricer
        if prev is not None:
            t0, t1 = prev.reference_date, p.reference_date
            if t0 < t1:
                cash = self._cash(p, t0, t1)
            fin = self.financing(_ql(prev), ctx.ts, ctx.prev_ts)
        return Valuation(ctx.ts, pv, cash, fin, {k: m[k] for k in ("ytm", "clean", "dirty", "accrued")})

    # ------------------------------------------------------------------ measures
    def _state(self, ctx: MarkContext) -> Dict[str, float]:
        p = _ql(ctx.pricer)
        if not self._priced(p):
            raise ConfigError(f"{self.security} is on/after its last payment date; no quote-based measures", code="BOND")
        key = ("ql_bond_state", id(self))
        if key not in ctx.cache:
            ctx.cache[key] = self.mark(p)
        return ctx.cache[key]

    def ytm(self, *, ctx: MarkContext) -> float:
        return self._state(ctx)["ytm"]

    def accrued(self, *, ctx: MarkContext) -> float:
        return self._state(ctx)["accrued"]

    def duration(self, *, ctx: MarkContext) -> float:
        """Modified duration in years: -(1/P) dP/dy for y as a decimal."""
        s = self._state(ctx)
        p = _ql(ctx.pricer)
        return 100.0 * self._inst(p).risk(s["ytm"], p.reference_date) / s["dirty"]

    def dv01(self, *, ctx: MarkContext) -> float:
        """Currency P&L per +1bp of the bond's yield (negative for a long)."""
        s, p = self._state(ctx), _ql(ctx.pricer)
        return -self.sign * self.notional * self._inst(p).risk(s["ytm"], p.reference_date) / 1e4

    def gamma(self, *, ctx: MarkContext) -> float:
        """d2 PV / dbp^2 in currency."""
        s, p = self._state(ctx), _ql(ctx.pricer)
        return self.sign * self.notional / 100.0 * self._inst(p).conv(s["ytm"], p.reference_date) * 1e-4

    # ------------------------------------------------------------------ layers
    def _terms(self, ctx: MarkContext) -> Dict[str, float]:
        key = ("ql_bond_terms", id(self))
        if key in ctx.cache:
            return ctx.cache[key]
        p1, p0 = _ql(ctx.pricer), ctx.prev_pricer
        if p0 is None:
            raise ConfigError("layers need a previous pricer (ctx.prev_pricer)", code="LAYER")
        p0 = _ql(p0)
        t0, t1 = p0.reference_date, p1.reference_date
        if self._matured(p0):
            out = dict.fromkeys(("carry", "roll", "delta", "convexity"), 0.0)
        else:
            m0 = self.mark(p0)
            fin = self.financing(p0, ctx.ts, ctx.prev_ts)
            cash = self._cash(p1, t0, t1) if t0 < t1 else 0.0
            if not (self._priced(p0) and self._priced(p1)):
                out = {"carry": self.pv_of(p1) + cash - self.pv_of(p0, m0) + fin, "roll": 0.0, "delta": 0.0, "convexity": 0.0}
            else:
                inst = self._inst(p1)
                y0, y1 = m0["ytm"], self.mark(p1)["ytm"]
                v0 = self.pv_of(p0, m0)
                v_cy = self._mv(inst.dirty(y0, t1)) + self._due(p1)
                risk, conv = inst.risk(y0, t1), inst.conv(y0, t1)
                y_hold = y0 + self._pulldown(p0, p1)
                k = self.sign * self.notional / 100.0
                out = {"carry": cash + v_cy - v0 + fin, "roll": -k * risk * (y_hold - y0), "delta": -k * risk * (y1 - y_hold), "convexity": 0.5 * k * conv * (y1 - y0) ** 2}
        ctx.cache[key] = out
        return out

    def _pulldown(self, prev: QLPricer, p: QLPricer) -> float:
        """Yield change (percent) from ageing down the on-the-run yield curve over the elapsed calendar time; 0 without a curve."""
        yc = ytm_curve(prev, self.conv)
        if yc is None:
            return 0.0
        ttm0 = (self.maturity - prev.reference_date).days / 365.25
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


def ytm_curve(p: QLPricer, conv: BondConv) -> Optional[Callable[[float], float]]:
    """Yield (percent) by time to maturity through the quoted on-the-run (`CT<n>`, rank 0) securities of the snapshot: `np.interp`, flat outside; None with fewer than 2 points."""
    def build() -> Optional[Callable[[float], float]]:
        qs = p.snapshot.quotes
        if qs is None:
            return None
        pts: Dict[float, float] = {}
        for alias, sid in qs.aliases.items():
            if not _CT.match(alias) or sid not in qs.quotes:
                continue
            sec, q = qs.securities[sid], qs.quotes[sid]
            if q.clean is None and q.ytm is None:
                continue
            probe = QLBond(security=sid, coupon=sec.coupon, issue=sec.issue_date, maturity=sec.maturity_date, sign=1, notional=1.0, repo=None, conv=conv)
            y = float(q.ytm) if q.clean is None else probe._inst(p).yield_from_clean(float(q.clean), p.reference_date)
            pts[(sec.maturity_date - p.reference_date).days / 365.25] = y
        if len(pts) < 2:
            return None
        ks = sorted(pts)
        xs, ys = tuple(ks), tuple(pts[k] for k in ks)
        return lambda t: float(np.interp(float(t), xs, ys))

    return p.memo(("ytm_curve", conv), build)


def bond_factory(pricer: Any, ts: Any, *, terms: Mapping[str, Any], conventions: Mapping[str, Any]) -> Built:
    """`(pricer, ts, terms, conventions) -> Built(QLBond, resolved terms)`: `security` (an id or an alias such as CT10) is resolved through the snapshot and returned as the security id."""
    conv = bond_conventions(conventions)
    p = _ql(pricer)
    extras = terms.get("extras") or {}
    unknown = sorted(set(extras) - {"repo"})
    if unknown:
        raise ConfigError(f"bond extras {unknown} are not supported; allowed: ['repo']", code="CFG-EXTRAS")
    p.calendar(conv.calendar)  # fail now, not at the first mark, when the calendar is not in the snapshot
    sec = p.security(str(terms["security"]))
    obj = QLBond(security=sec.id, coupon=sec.coupon, issue=sec.issue_date, maturity=sec.maturity_date, sign=int(terms["direction"]), notional=float(terms["notional"]),
                 repo=extras.get("repo"), conv=conv)
    return Built(obj, {"security": sec.id, "coupon": sec.coupon, "maturity": sec.maturity_date, "notional": obj.notional})


def _bind(method: str) -> Dict[str, Any]:
    return {"target": {"method": method}, "kwargs": {"ctx": "@ctx"}}


BOND_BIND: Dict[str, Any] = {name: _bind(method) for name, method in (
    ("value", "value"), ("dv01", "dv01"), ("gamma", "gamma"), ("rate", "ytm"), ("ytm", "ytm"), ("duration", "duration"), ("accrued", "accrued"),
    ("carry", "carry"), ("roll", "roll"), ("delta", "delta"), ("convexity", "convexity"))}

bond = Kit(factory=bond_factory, asset_class="bond", default_bind=BOND_BIND, cls=QLBond, doc="Fixed-coupon US Treasury on QuantLib (module docstring: conventions and layer definitions).")
