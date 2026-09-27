"""QLSwap: a USD SOFR-style overnight-indexed swap priced with QuantLib, and the `swap` Kit (factory + default bindings) for pricebt's `swap` schema.

An object here is PLAIN DATA (dates, percent rate, notional, validated conventions): every QuantLib instrument is rebuilt inside the call that needs it, under the
evaluation-date guard of `_compat`, so objects deep-copy and pickle and nothing QuantLib-specific survives a call.

Units: `fixed_rate` and `rate` are PERCENT (QuantLib wants decimals: converted inside `_ois.make_swap` and in `rate`); dv01 is currency per +1bp; gamma currency per bp^2;
both per unit as built with its notional, holder-signed (a payer of fixed is positive dv01).

Measures (definitions, see docs/design/11-quantlib-conventions.md sections 5, 6, 11)
* `dv01`: analytic PV01 of the fixed leg while the swap has NOT started; once started, the sum of its delta ladder over `tenors` (the analytic figure keeps the whole
  accrued coupon, which no longer moves with rates, and overstates the market dv01 by up to 25%).
* `gamma`: second derivative for a parallel +-1bp move of ALL pillar par rates of the par-swap risk curve (same variable as dv01). `dv01_zero` / `gamma_zero` are the
  same quantities for a parallel move of the continuously-compounded ZERO rates of the dense curve (the variable of the shipped rateslib adapter's `dv01_ad`/`convexity`).
* `delta_ladder`: currency per +1bp of each pillar's par rate, central +-0.5bp bumps of the bootstrapped risk curve, keys = `tenors` upper-cased.

Layers (per unit, interval (t0, t1]; `V` = value in the world of a curve anchored at its date, evaluation date = that date, fixings published by then)
* `carry`  = X_fwd - V0: the t0-world flows valued at t1 (flows paid in [t0, t1) at face), i.e. the market unchanged in DATE space, including flows paid in the interval.
* `roll`   = X_roll - X_fwd: the curve unchanged in TENOR space (anchored at t1, DF(t1 + tau) = DF0(t0 + tau)) with the realised t1 fixings, plus the cash paid.
* `delta`  = (V_base + cash - X_roll) + g.dz: resampling of the rolled curve on the t1 curve's node dates plus the first directional derivative along the realised move
  `dz` of the zero rates at those nodes; `convexity` = 1/2 dz' H dz, the second directional derivative along the same `dz`. Both derivatives come from two revaluations
  V(base +- 0.1 dz) (no Hessian is ever formed). The engine's `unexplained` receives the remainder.
"""
from __future__ import annotations

import bisect
import datetime as dt
import math
import re
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from ...contracts.spec import Built, Kit
from ...errors import ConfigError, MarketDataUnavailable
from ...pricable import MarkContext, Valuation
from . import _compat as C
from . import _ois, _risk, _schedule
from .conventions import SwapConv, swap_conventions
from .wrap import QLPricer

ql = C.ql

DEFAULT_TENORS = _risk.DEFAULT_TENORS
_TENOR_RE = re.compile(r"^\d+[MYmy]$")
_H = 0.1  # fraction of the realised move used for the directional derivatives
_TAU_DAYS = 365.0


def _ql(p: Any) -> QLPricer:
    if not isinstance(p, QLPricer):
        raise ConfigError(f"the market pricer is a {type(p).__name__}, not a QLPricer: set `wrap` on the pricer role to pricebt.contrib.quantlib:wrap", code="CFG-WRAP")
    return p


class _World:
    """A QuantLib swap in one world: (discount + projection curve, evaluation date, published fixings). Build and use INSIDE `C.guard(eval_date)`.

    The curve sits behind a relinkable handle so scenarios re-use the swap; every curve linked must be anchored at the evaluation date."""

    def __init__(self, sw: "QLSwap", pricer: QLPricer, curve: "ql.YieldTermStructure", eval_date: dt.date):
        self.sw, self.eval_date = sw, eval_date
        cal = pricer.calendar(sw.conv.calendar)
        self.handle = ql.RelinkableYieldTermStructureHandle()
        self.link(curve)
        dates, values = sw.fixings_for(pricer, cal, eval_date)
        self.index = C.make_index(cal, self.handle, dates, values)
        self.swap = _ois.make_swap(cal, self.index, sw.conv, sw.unadjusted, sw.sign, sw.notional, sw.fixed_rate)
        self.swap.setPricingEngine(_ois.engine(self.handle, eval_date))

    def link(self, curve: "ql.YieldTermStructure") -> None:
        if curve.referenceDate() != C.qd(self.eval_date):
            raise ConfigError(f"a curve anchored at {C.pd_(curve.referenceDate())} cannot price at {self.eval_date}", code="CFG-CURVE-DATE")
        self.handle.linkTo(curve)

    def npv(self) -> float:
        return float(self.swap.NPV())

    def flows(self, min_pay: dt.date) -> List[Tuple[dt.date, float]]:
        """Holder-signed (payment date, amount) of every flow paid on or after `min_pay` (amounts of paid coupons before it are never evaluated)."""
        s = self.sw.sign
        out = [(C.pd_(c.date()), -s * c.amount()) for c in self.swap.fixedLeg() if C.pd_(c.date()) >= min_pay]
        out += [(C.pd_(c.date()), s * c.amount()) for c in self.swap.overnightLeg() if C.pd_(c.date()) >= min_pay]
        return out

    def pay_dates(self) -> List[dt.date]:
        return sorted({C.pd_(c.date()) for leg in (self.swap.fixedLeg(), self.swap.overnightLeg()) for c in leg})


class QLSwap:
    """One unit = one swap of `notional` (unsigned). `sign` +1 = payer of fixed (positive dv01), -1 = receiver."""

    asset_class = "swap"

    def __init__(self, *, effective: dt.date, maturity: dt.date, unadjusted: Sequence[dt.date], sign: int, notional: float, fixed_rate: float, conv: SwapConv):
        if sign not in (1, -1):
            raise ConfigError(f"sign must be +1 (pay fixed) or -1 (receive fixed), got {sign!r}", code="SWAP")
        if not notional > 0:
            raise ConfigError("notional is unsigned and must be > 0 (direction is `side`)", code="SWAP")
        self.effective, self.maturity = C.to_date(effective), C.to_date(maturity)
        self.unadjusted = tuple(C.to_date(d) for d in unadjusted)
        self.sign, self.notional, self.fixed_rate, self.conv = int(sign), float(notional), float(fixed_rate), conv

    @property
    def notional_amount(self) -> float:
        return self.notional

    # ------------------------------------------------------------------ helpers
    def started(self, p: QLPricer) -> bool:
        return self.effective < p.reference_date

    def last_payment(self, cal: "ql.Calendar") -> dt.date:
        bdc = _ois.BDC[self.conv.business_day_convention]
        end = cal.adjust(C.qd(self.unadjusted[-1]), bdc)
        return C.pd_(cal.advance(end, self.conv.payment_lag_days, ql.Days, bdc))

    def matured(self, p: QLPricer) -> bool:
        return self.last_payment(p.calendar(self.conv.calendar)) < p.reference_date

    def fixings_for(self, p: QLPricer, cal: "ql.Calendar", eval_date: dt.date) -> Tuple[Sequence[dt.date], Sequence[float]]:
        """The published fixings (decimal) dated on/after the effective date, when the swap has started as of `eval_date`; the same guard as the reference adapter:
        a business day without a fixing between the effective date and the day before `eval_date` is an error (QuantLib would raise mid-pricing, or worse, differently)."""
        if not self.effective < eval_date:
            return (), ()
        fx = p.fixings_decimal()
        if fx is None or not fx[0]:
            raise MarketDataUnavailable(p.ts, {}, f"swap started {self.effective} but the pricer carries no fixings")
        dates, values = fx
        i = bisect.bisect_left(dates, self.effective)
        dates, values = dates[i:], values[i:]

        def check() -> None:
            if dates and dates[-1] >= eval_date:
                raise ValueError(f"fixings dated {dates[-1]} are not strictly before the evaluation date {eval_date}")
            first = C.pd_(cal.adjust(C.qd(self.effective), ql.Following))
            last = C.pd_(cal.advance(C.qd(eval_date), -1, ql.Days))
            if last >= first:
                have = set(dates)
                missing = [d for d in (C.pd_(x) for x in cal.businessDayList(C.qd(first), C.qd(last))) if d not in have]
                if missing:
                    raise MarketDataUnavailable(p.ts, {}, f"{len(missing)} SOFR fixings missing between {first} and {last}; first {missing[0]}")

        p.memo(("fixings_ok", self.conv.calendar, self.effective, eval_date), check)
        return dates, values

    def _risk(self, p: QLPricer, tenors: Sequence[str]) -> "_risk.RiskCurves":
        t = _risk.clean_tenors(tenors)
        return p.memo(("risk", self.conv, t), lambda: _risk.build_risk_curves(p, self.conv, t))

    # ------------------------------------------------------------------ value
    def value(self, *, ctx: MarkContext) -> Valuation:
        p, prev = _ql(ctx.pricer), ctx.prev_pricer
        ref = p.reference_date
        if self.matured(p):  # nothing left to value; the last flows (if paid in this interval) are swept as cash
            t0 = prev.reference_date if prev is not None else ref
            cash = self._cash(p, t0) if t0 < ref and self.last_payment(p.calendar(self.conv.calendar)) >= t0 else 0.0
            return Valuation(ctx.ts, 0.0, cash, 0.0, {"reference_date": ref})
        with C.guard(ref):
            w = _World(self, p, p.ql_curve(), ref)
            pv = w.npv()
            cash = 0.0
            if prev is not None and prev.reference_date < ref:
                t0 = prev.reference_date
                if any(t0 <= d < ref for d in w.pay_dates()):
                    cash = sum(a for d, a in w.flows(t0) if d < ref)
        return Valuation(ctx.ts, pv, cash, 0.0, {"reference_date": ref})

    def _cash(self, p: QLPricer, t0: dt.date) -> float:
        """Cash of a swap that has no flows left at the reference date: what was paid in [t0, ref)."""
        ref = p.reference_date
        with C.guard(ref):
            w = _World(self, p, p.ql_curve(), ref)
            cash = sum(a for d, a in w.flows(t0) if d < ref)
        return cash

    # ------------------------------------------------------------------ measures
    def rate(self, *, ctx: MarkContext) -> float:
        """Par rate (PERCENT) of the remaining swap; NaN once it has matured."""
        p = _ql(ctx.pricer)
        if self.matured(p):
            return float("nan")
        with C.guard(p.reference_date):
            w = _World(self, p, p.ql_curve(), p.reference_date)
            r = float(w.swap.fairRate()) * 100.0
        return r

    def dv01(self, *, ctx: MarkContext, tenors: Sequence[str] = DEFAULT_TENORS) -> float:
        p = _ql(ctx.pricer)
        if self.matured(p):
            return 0.0
        if self.started(p):
            return float(sum(self.delta_ladder(ctx=ctx, tenors=tenors).values()))
        with C.guard(p.reference_date):
            w = _World(self, p, p.ql_curve(), p.reference_date)
            v = -float(w.swap.fixedLegBPS())
        return v

    def delta_ladder(self, *, ctx: MarkContext, tenors: Sequence[str] = DEFAULT_TENORS) -> Dict[str, float]:
        p = _ql(ctx.pricer)
        rc = self._risk(p, tenors)
        key = ("ql_swap_ladder", id(self), rc.tenors)
        if key in ctx.cache:
            return dict(ctx.cache[key])
        if self.matured(p):
            out = {t: 0.0 for t in rc.tenors}
        else:
            with C.guard(p.reference_date):
                w = _World(self, p, rc.base, p.reference_date)
                out = {}
                for t, up, dn in zip(rc.tenors, rc.ups, rc.dns):
                    w.link(up)
                    v_up = w.npv()
                    w.link(dn)
                    out[t] = (v_up - w.npv()) / (2 * _risk.LADDER_BUMP / 1e-4)  # currency per +1bp of the pillar's par rate
        ctx.cache[key] = out
        return dict(out)

    def gamma(self, *, ctx: MarkContext, tenors: Sequence[str] = DEFAULT_TENORS) -> float:
        p = _ql(ctx.pricer)
        rc = self._risk(p, tenors)
        if self.matured(p):
            return 0.0
        with C.guard(p.reference_date):
            w = _World(self, p, rc.base, p.reference_date)
            v0 = w.npv()
            w.link(rc.parallel_up)
            up = w.npv()
            w.link(rc.parallel_dn)
            dn = w.npv()
        return (up + dn - 2 * v0) / (_risk.PARALLEL_BUMP / 1e-4) ** 2  # currency per bp^2

    def _zero_shift(self, ctx: MarkContext) -> Tuple[float, float]:
        p = _ql(ctx.pricer)
        key = ("ql_swap_zero", id(self))
        if key in ctx.cache:
            return ctx.cache[key]
        if self.matured(p):
            out = (0.0, 0.0)
        else:
            ref = p.reference_date
            dates, dfs = p.curve_nodes()
            days = [(d - ref).days for d in dates]

            def shifted(bp: float) -> "ql.DiscountCurve":
                return C.discount_curve(dates, [v * math.exp(-bp * 1e-4 * n / _TAU_DAYS) for v, n in zip(dfs, days)])

            with C.guard(ref):
                w = _World(self, p, p.ql_curve(), ref)
                v0 = w.npv()
                vals = {}
                for bp in (0.5, -0.5, 1.0, -1.0):
                    w.link(shifted(bp))
                    vals[bp] = w.npv()
            out = ((vals[0.5] - vals[-0.5]) / 1.0, vals[1.0] - 2 * v0 + vals[-1.0])
        ctx.cache[key] = out
        return out

    def dv01_zero(self, *, ctx: MarkContext) -> float:
        """PV change for +1bp of the continuously-compounded ZERO curve (dense curve), currency; central difference of full revaluations."""
        return self._zero_shift(ctx)[0]

    def gamma_zero(self, *, ctx: MarkContext) -> float:
        """d2 PV / dbp^2 for the same parallel zero-rate move as `dv01_zero`."""
        return self._zero_shift(ctx)[1]

    # ------------------------------------------------------------------ layers
    def decomposition(self, ctx: MarkContext) -> Dict[str, float]:
        """carry, roll, delta, convexity and the remainder (`residual`, the engine's `unexplained`), per unit, plus V0, V1 and the cash paid in the interval."""
        key = ("ql_swap_decomposition", id(self))
        if key in ctx.cache:
            return ctx.cache[key]
        p1, p0 = _ql(ctx.pricer), ctx.prev_pricer
        if p0 is None:
            raise ConfigError("layers need a previous pricer (ctx.prev_pricer)", code="LAYER")
        p0 = _ql(p0)
        t0, t1 = p0.reference_date, p1.reference_date
        if t1 < t0:
            raise ValueError(f"the layers need t1 >= t0, got {t0} -> {t1}")
        if self.last_payment(p1.calendar(self.conv.calendar)) < t0:  # nothing left at t0: nothing moves
            out = {k: 0.0 for k in ("carry", "roll", "delta", "convexity", "residual", "V0", "V1", "cash")}
            ctx.cache[key] = out
            return out
        with C.guard(t0):
            w0 = _World(self, p0, p0.ql_curve(), t0)
            v0 = w0.npv()
            c0 = p0.ql_curve()
            d1 = c0.discount(C.qd(t1))
            x_fwd = sum(a * c0.discount(C.qd(d)) / d1 if d >= t1 else a for d, a in w0.flows(t0))
        n = (t1 - t0).days
        dates0, dfs0 = p0.curve_nodes()
        dates1, dfs1 = p1.curve_nodes()
        with C.guard(t1):
            w = _World(self, p1, p1.ql_curve(), t1)
            v1 = w.npv()
            cash = sum(a for d, a in w.flows(t0) if d < t1)
            cr = C.discount_curve([t1] + [d + dt.timedelta(days=n) for d in dates0 if d > t0], [1.0] + [v for d, v in zip(dates0, dfs0) if d > t0])
            w.link(cr)
            x_roll = w.npv() + cash
            base = [cr.discount(C.qd(d)) for d in dates1]
            w.link(C.discount_curve(dates1, base))
            v_base = w.npv()
            tau = [(d - t1).days / _TAU_DAYS for d in dates1[1:]]
            dz = [-math.log(a) / t + math.log(b) / t for a, b, t in zip(dfs1[1:], base[1:], tau)]  # z1 - z_base at the t1 nodes

            def v_eps(eps: float) -> float:
                w.link(C.discount_curve(dates1, [1.0] + [b * math.exp(-eps * z * t) for b, z, t in zip(base[1:], dz, tau)]))
                return w.npv()

            up, dn = v_eps(_H), v_eps(-_H)
        first = (up - dn) / (2 * _H)
        second = (up + dn - 2 * v_base) / (2 * _H * _H)
        out = {
            "carry": x_fwd - v0, "roll": x_roll - x_fwd, "delta": (v_base + cash - x_roll) + first, "convexity": second, "residual": (v1 - v_base) - first - second,
            "V0": v0, "V1": v1, "cash": cash,
        }
        ctx.cache[key] = out
        return out

    def carry(self, *, ctx: MarkContext) -> float:
        return self.decomposition(ctx)["carry"]

    def roll(self, *, ctx: MarkContext) -> float:
        return self.decomposition(ctx)["roll"]

    def delta(self, *, ctx: MarkContext) -> float:
        return self.decomposition(ctx)["delta"]

    def convexity(self, *, ctx: MarkContext) -> float:
        return self.decomposition(ctx)["convexity"]


# ------------------------------------------------------------------------------ factory
def _resolve_effective(spec: Any, p: QLPricer, cal: "ql.Calendar", conv: SwapConv) -> dt.date:
    ref = p.reference_date
    spot = C.pd_(cal.advance(C.qd(ref), conv.spot_lag_days, ql.Days))
    if isinstance(spec, str) and spec.strip().lower() == "spot":
        return spot
    if isinstance(spec, str) and _TENOR_RE.match(spec.strip()):
        months = _schedule.tenor_months(spec)
        return C.pd_(cal.adjust(C.qd(_schedule.add_months(spot, months, spot.day)), _ois.BDC[conv.business_day_convention]))
    if isinstance(spec, str) and re.match(r"^\d+[DWdw]$", spec.strip()):
        raise ConfigError(f"effective {spec!r}: only month and year tenors are supported", code="CFG-TENOR")
    return C.to_date(spec)


def swap_factory(pricer: Any, ts: Any, *, terms: Mapping[str, Any], conventions: Mapping[str, Any]) -> Built:
    """`(pricer, ts, terms, conventions) -> Built(QLSwap, resolved terms)`: `effective` (date | 'spot' | tenor), `maturity` (date | tenor), `fixed_rate` (PERCENT number | 'par')
    are resolved here and returned as concrete dates and a percent rate (spec T4). `extras: {par_spread_bp: x}` with `fixed_rate: par` strikes x basis points off par."""
    conv = swap_conventions(conventions)
    p = _ql(pricer)
    extras = dict(terms.get("extras") or {})
    unknown = sorted(set(extras) - {"par_spread_bp"})
    if unknown:
        raise ConfigError(f"swap extras {unknown} are not supported; allowed: ['par_spread_bp']", code="CFG-EXTRAS")
    spread = extras.get("par_spread_bp")
    if spread is not None and (isinstance(spread, bool) or not isinstance(spread, (int, float)) or not math.isfinite(spread)):
        raise ConfigError(f"extras par_spread_bp must be a finite number of basis points, got {spread!r}", code="CFG-EXTRAS")
    cal = p.calendar(conv.calendar)
    bdc = _ois.BDC[conv.business_day_convention]
    effective = _resolve_effective(terms.get("effective", "spot"), p, cal, conv)
    mat = terms["maturity"]
    if isinstance(mat, str) and _TENOR_RE.match(mat.strip()):
        unadj = _schedule.from_tenor(effective, mat, conv.freq_months)
    elif isinstance(mat, str) and re.match(r"^\d+[DWdw]$", mat.strip()):
        raise ConfigError(f"maturity {mat!r}: only month and year tenors are supported", code="CFG-TENOR")
    else:
        unadj = _schedule.from_maturity(effective, C.to_date(mat), lambda d: C.pd_(cal.adjust(C.qd(d), bdc)), conv.freq_months)
    maturity = C.pd_(cal.adjust(C.qd(unadj[-1]), bdc))
    rate = terms.get("fixed_rate", "par")
    sign, notional = int(terms["direction"]), float(terms["notional"])
    if isinstance(rate, str):
        if rate.strip().lower() != "par":
            raise ConfigError(f"fixed_rate must be a number (PERCENT) or 'par', got {rate!r}", code="CFG-TERMS")
        if effective < p.reference_date:
            raise ConfigError(f"fixed_rate 'par' needs an effective date on or after the reference date ({effective} < {p.reference_date})", code="CFG-TERMS")
        probe = QLSwap(effective=effective, maturity=maturity, unadjusted=unadj, sign=sign, notional=notional, fixed_rate=0.0, conv=conv)
        with C.guard(p.reference_date):
            w = _World(probe, p, p.ql_curve(), p.reference_date)
            rate = float(w.swap.fairRate()) * 100.0 + (spread or 0.0) / 100.0  # par in PERCENT plus the spread in bp (1bp = 0.01 percent)
    elif spread is not None:
        raise ConfigError("extras par_spread_bp applies only to fixed_rate 'par'", code="CFG-EXTRAS")
    obj = QLSwap(effective=effective, maturity=maturity, unadjusted=unadj, sign=sign, notional=notional, fixed_rate=float(rate), conv=conv)
    return Built(obj, {"effective": effective, "maturity": maturity, "fixed_rate": float(rate)})


def _bind(method: str, **extra: Any) -> Dict[str, Any]:
    return {"target": {"method": method}, "kwargs": {"ctx": "@ctx", **extra}}


SWAP_BIND: Dict[str, Any] = {
    "value": _bind("value"),
    "dv01": _bind("dv01", tenors=list(DEFAULT_TENORS)),
    "gamma": _bind("gamma", tenors=list(DEFAULT_TENORS)),
    "rate": _bind("rate"),
    "delta_ladder": {**_bind("delta_ladder", tenors=list(DEFAULT_TENORS)), "reduce": "dict_of_floats"},
    "carry": _bind("carry"),
    "roll": _bind("roll"),
    "delta": _bind("delta"),
    "convexity": _bind("convexity"),
    "dv01_zero": _bind("dv01_zero"),
    "gamma_zero": _bind("gamma_zero"),
}

swap = Kit(
    factory=swap_factory, asset_class="swap", default_bind=SWAP_BIND, cls=QLSwap, extra={"dv01_zero": "measure", "gamma_zero": "measure"},
    doc="USD SOFR-style overnight-indexed swap on QuantLib (module docstring: measures and layer definitions).",
)
