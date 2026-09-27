"""RLSwap: a SOFR-style overnight-indexed swap priced by rateslib, and the `swap` Kit (factory + default bindings) of pricebt's `swap` schema.

The factory takes the NEUTRAL terms (side, effective, maturity, notional, fixed_rate) and the shared conventions block; the schedule, the spot date and the `par` rate are
resolved by rateslib on the snapshot's calendar and returned as resolved terms (spec T4). The object is then a holder-signed unit: `value` is the holder's P&L,
`dv01` the dollar delta (currency per +1bp of the swap's own par rate, per unit as built with its notional; positive for a payer), `gamma` its second derivative for the
same move, `delta_ladder` the bucketed dv01 as a plain `dict[str, float]`.

Measures (definitions: docs/design/11-quantlib-conventions.md sections 5, 6, 11, shared with every adapter)
* `dv01`: analytic PV01 of the fixed leg while the swap has NOT started; once started, the sum of its delta ladder over the bound `tenors` (the analytic figure keeps the
  accrued coupon, which no longer moves with rates, and overstates the market dv01 by up to 25%).
* `gamma`: the second difference of a parallel +-1bp move of ALL pillar par rates of the risk curve (the same variable as `dv01`). `dv01_zero` / `gamma_zero` are the
  same quantities for a parallel move of the continuously-compounded ZERO rates (automatic differentiation on the dense curve).
* `delta_ladder`: currency per +1bp of each pillar's par rate on the calibrated risk curve, keys = the bound `tenors`, upper-cased.

Layers (per unit, over the interval since the previous cadence point): `carry`, `roll`, `delta`, `convexity` folded from the scenario-curve chain of `layers.py`
(carry = forwards realised in date space; roll = tenor-space roll-down plus realised-versus-assumed fixings; delta = first order in zero-rate space plus the resampling;
convexity = second order); the raw pieces are the extension layers `rateslib.carry_fwd`, `rateslib.roll_static`, `rateslib.fixings`, `rateslib.resample`.

The library's global fixings store is owned by `_compat`; a started swap installs its pricer's published fixings (guarded) before every valuation.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any, Dict, Mapping, Optional, Sequence

import pandas as pd

from ...contracts.spec import Built, Kit
from ...errors import ConfigError, MarketDataUnavailable
from ...pricable import MarkContext, Valuation
from . import _compat as C
from ._compat import rl
from .conventions import SwapConv, swap_conventions
from .ladder import DEFAULT_TENORS, clean_tenors, ladder_model, pillar_swap
from .layers import Decomposition, decompose_swap, zero_risk

_DECOMP_KEY = "rl_swap_decomposition"
_TENOR = re.compile(r"^\d+[YyMmWwDd]$")
_H_BP = 1.0  # the parallel par bump of `gamma`, bp per side


def _rl(p: Any) -> Any:
    from .pricer import RLCurvePricer

    if not isinstance(p, RLCurvePricer):
        raise ConfigError(f"the market pricer is a {type(p).__name__}, not a rateslib pricer: set `wrap` on the pricer role to pricebt.contrib.rateslib:wrap", code="CFG-WRAP")
    if p.curve is None:  # a quote-panel snapshot without a funding curve wraps (bonds need none), but rateslib would fail on `curves=None` with an unrelated TypeError
        raise MarketDataUnavailable(p.ts, {}, "the snapshot carries no discount curve: a swap needs a funding curve (give the provider one)")
    return p


class RLSwap:
    """One unit = one swap of `notional` (unsigned). `sign` +1 = payer of fixed (positive dv01), -1 = receiver. `value` is the holder's P&L.

    Build it with `swap_factory` (the kit); direct construction takes resolved dates and a percent rate. `maturity` is the tenor (or date) the schedule was built from.
    """

    asset_class = "swap"

    def __init__(self, *, effective: dt.date, maturity: Any, sign: int, notional: float, fixed_rate: float, conv: SwapConv, cal: Any, fixings_name: str = C.DEFAULT_FIXINGS_NAME):
        if sign not in (1, -1):
            raise ConfigError(f"sign must be +1 (pay fixed) or -1 (receive fixed), got {sign!r}", code="SWAP")
        if not notional > 0:
            raise ConfigError("notional is unsigned and must be > 0 (direction is `side`)", code="SWAP")
        self.effective = C.to_date(effective)
        self.sign, self.notional, self.fixed_rate, self.conv, self.fixings_name = int(sign), float(notional), float(fixed_rate), conv, fixings_name
        term = maturity.lower() if isinstance(maturity, str) else C.to_dt(maturity)
        kw = dict(fixed_rate=self.fixed_rate, notional=self.sign * self.notional)
        self._inst = pillar_swap(conv, cal, C.to_dt(self.effective), term, **kw, leg2_rate_fixings=fixings_name)
        self._inst_fwd = pillar_swap(conv, cal, C.to_dt(self.effective), term, **kw)  # forward-starting marks skip the per-call fixings lookup (~30x cheaper)
        self.termination = C.to_date(self._inst.leg1.schedule.termination)
        self._started = False
        self._pay_dates = sorted({p.settlement_params.payment for leg in (self._inst.leg1, self._inst.leg2) for p in leg.periods})

    @property
    def notional_amount(self) -> float:
        return self.notional

    # ------------------------------------------------------------------ rateslib plumbing
    def has_payment_in(self, t0: dt.datetime, t1: dt.datetime) -> bool:
        return any(t0 <= p < t1 for p in self._pay_dates)

    @property
    def last_payment(self) -> dt.date:
        return C.to_date(self._pay_dates[-1])

    def matured(self, pricer: Any) -> bool:
        return self.last_payment < pricer.reference_date

    @property
    def _active(self) -> Any:
        return self._inst if self._started else self._inst_fwd

    def _prep(self, pricer: Any) -> None:
        """Select the instrument for this market (fixings-aware once the swap has started), install the pricer's published fixings (guarded) into the global store
        and clear the instrument's cached fixings."""
        ref = pricer.reference_date
        started = self.effective < ref
        self._started = started
        fx = getattr(pricer, "fixings", None)
        if started:
            if fx is None or len(fx) == 0:
                raise MarketDataUnavailable(pricer.ts, {}, f"swap started {self.effective} but the pricer carries no fixings")
            C.check_fixings(fx[fx.index >= pd.Timestamp(C.to_dt(self.effective))], self.effective, ref, pricer.calendar(self.conv.calendar))
            C.install_fixings(self.fixings_name, fx)
            self._inst.reset_fixings()

    def _npv(self, curve: Any, forward: Optional[dt.datetime] = None) -> float:
        return C.finite(self._active.npv(curves=curve) if forward is None else self._active.npv(curves=curve, forward=forward))

    def _npv_ad(self, curve: Any) -> Any:
        """Unstripped npv (Dual/Dual2 on AD curves), for zero-rate-space risk."""
        return self._active.npv(curves=curve)

    def _flows(self, curve: Any) -> pd.DataFrame:
        cf = self._active.cashflows(curves=curve)
        return cf[["Type", "Payment", "Cashflow", "DF", "NPV"]].reset_index(drop=True)

    # ------------------------------------------------------------------ value
    def value(self, *, ctx: MarkContext) -> Valuation:
        p = _rl(ctx.pricer)
        prev = ctx.prev_pricer
        if self.matured(p):  # nothing left to value: the last flows (if paid in this interval) are swept as cash
            cash = 0.0
            if prev is not None and prev.reference_date < p.reference_date and self.last_payment >= prev.reference_date:
                cash = self._cash(p, C.to_dt(prev.reference_date), C.to_dt(p.reference_date))
            return Valuation(ctx.ts, 0.0, cash, 0.0, {"reference_date": p.reference_date})
        self._prep(p)
        pv = self._npv(p.curve)
        cash = 0.0
        if prev is not None and prev.reference_date < p.reference_date:
            cash = self._cash(p, C.to_dt(prev.reference_date), C.to_dt(p.reference_date))
        return Valuation(ctx.ts, pv, cash, 0.0, {"reference_date": p.reference_date})

    def _cash(self, p: Any, t0: dt.datetime, t1: dt.datetime) -> float:
        if not self.has_payment_in(t0, t1):
            return 0.0
        self._prep(p)
        w = self._flows(p.curve)
        w = w[(w["Payment"] >= t0) & (w["Payment"] < t1)]
        return C.finite(w["Cashflow"].sum())

    # ------------------------------------------------------------------ measures
    def rate(self, *, ctx: MarkContext) -> float:
        """Par rate (percent) of the remaining swap; NaN once it has matured (rateslib divides by zero)."""
        p = _rl(ctx.pricer)
        if self.matured(p):
            return float("nan")
        self._prep(p)
        return C.finite(self._active.rate(curves=p.curve))

    def dv01(self, *, ctx: MarkContext, tenors: Sequence[str] = DEFAULT_TENORS) -> float:
        p = _rl(ctx.pricer)
        if self.matured(p):
            return 0.0
        self._prep(p)
        if self._started:
            return float(sum(self.delta_ladder(ctx=ctx, tenors=tenors).values()))
        return C.finite(self._active.analytic_delta(curves=p.curve, leg=1))

    def delta_ladder(self, *, ctx: MarkContext, tenors: Sequence[str] = DEFAULT_TENORS) -> Dict[str, float]:
        p = _rl(ctx.pricer)
        t = clean_tenors(tenors)
        key = ("rl_swap_ladder", id(self), t)
        if key in ctx.cache:
            return dict(ctx.cache[key])
        if self.matured(p):
            out = {b: 0.0 for b in t}
        else:
            self._prep(p)
            out = ladder_model(p, self.conv, t).delta(self._active)
        ctx.cache[key] = out
        return dict(out)

    def gamma(self, *, ctx: MarkContext, tenors: Sequence[str] = DEFAULT_TENORS) -> float:
        """d2 PV / dbp^2 for a parallel move of every pillar's par rate: (V(+1bp) + V(-1bp) - 2 V(0)) / (1bp)^2 on the re-solved risk curves."""
        p = _rl(ctx.pricer)
        if self.matured(p):
            return 0.0
        t = clean_tenors(tenors)
        self._prep(p)
        up, dn, base = (ladder_model(p, self.conv, t, s) for s in (_H_BP, -_H_BP, 0.0))
        return (up.npv(self._active) + dn.npv(self._active) - 2.0 * base.npv(self._active)) / _H_BP ** 2

    def cashflows(self, *, ctx: MarkContext) -> pd.DataFrame:
        p = _rl(ctx.pricer)
        self._prep(p)
        return self._flows(p.curve)

    def _risk(self, ctx: MarkContext) -> Any:
        key = ("rl_swap_zero_risk", id(self))
        if key not in ctx.cache:
            p = _rl(ctx.pricer)
            c = p.curve
            self._prep(p)
            nodes = {d: float(v) for d, v in c.nodes.nodes.items()}
            ctx.cache[key] = zero_risk(self._npv_ad, c, nodes, C.to_dt(p.reference_date))
        return ctx.cache[key]

    def dv01_zero(self, *, ctx: MarkContext) -> float:
        """PV change for a +1bp parallel shift of the continuously-compounded zero curve (AD gradient), currency."""
        return 0.0 if self.matured(_rl(ctx.pricer)) else self._risk(ctx).dv01

    def gamma_zero(self, *, ctx: MarkContext) -> float:
        """d2 PV / dbp^2 for the same parallel zero-rate shift as `dv01_zero`."""
        return 0.0 if self.matured(_rl(ctx.pricer)) else self._risk(ctx).convexity

    # ------------------------------------------------------------------ layers (per unit, interval P&L over (prev, ts])
    def decomposition(self, ctx: MarkContext) -> Decomposition:
        if ctx.prev_pricer is None:
            raise ConfigError("layers need a previous pricer (ctx.prev_pricer)", code="LAYER")
        key = (_DECOMP_KEY, id(self))
        if key not in ctx.cache:
            ctx.cache[key] = decompose_swap(self, _rl(ctx.prev_pricer), _rl(ctx.pricer))
        return ctx.cache[key]

    def carry(self, *, ctx: MarkContext) -> float:
        return self.decomposition(ctx).folded()["carry"]

    def roll(self, *, ctx: MarkContext) -> float:
        return self.decomposition(ctx).folded()["roll"]

    def delta(self, *, ctx: MarkContext) -> float:
        return self.decomposition(ctx).folded()["delta"]

    def convexity(self, *, ctx: MarkContext) -> float:
        return self.decomposition(ctx).folded()["convexity"]

    def carry_fwd(self, *, ctx: MarkContext) -> float:
        return self.decomposition(ctx).raw["carry"]

    def roll_static(self, *, ctx: MarkContext) -> float:
        return self.decomposition(ctx).raw["roll"]

    def fixings_layer(self, *, ctx: MarkContext) -> float:
        return self.decomposition(ctx).raw["fixings"]

    def resample(self, *, ctx: MarkContext) -> float:
        return self.decomposition(ctx).raw["resample"]


# ------------------------------------------------------------------------------ factory and kit
def _effective(spec: Any, pricer: Any, cal: Any, conv: SwapConv) -> dt.datetime:
    """The effective date: `spot` (the spot lag on the snapshot calendar), a forward tenor from spot, or a date (adjusted)."""
    ref = C.to_dt(pricer.reference_date)
    spot = cal.add_bus_days(ref, conv.spot_lag_days, True)
    mod = conv_modifier(conv)
    if isinstance(spec, str):
        s = spec.strip().lower()
        if s == "spot":
            return spot
        if _TENOR.match(s):
            return rl.add_tenor(spot, s, mod, cal)
        raise ConfigError(f"effective {spec!r}: use 'spot', a forward tenor such as '1y' or a date", code="TERMS")
    return cal.roll(C.to_dt(spec), mod, True)


def conv_modifier(conv: SwapConv) -> str:
    return conv.irs_kwargs()["modifier"]


def swap_factory(pricer: Any, ts: Any, *, terms: Mapping[str, Any], conventions: Mapping[str, Any]) -> Built:
    """`(pricer, ts, *, terms, conventions) -> Built`: resolves the dates and `par` with rateslib on the snapshot's calendar and returns what it resolved (spec T4)."""
    p = _rl(pricer)
    conv = swap_conventions(conventions)
    cal = p.calendar(conv.calendar)
    extras = dict(terms.get("extras") or {})
    unknown = sorted(set(extras) - {"par_spread_bp"})
    if unknown:
        raise ConfigError(f"the rateslib adapter does not understand the swap extras {unknown}; it understands ['par_spread_bp']", code="CFG-EXTRAS")
    eff = _effective(terms.get("effective", "spot"), p, cal, conv)
    maturity = terms["maturity"]
    probe = pillar_swap(conv, cal, eff, maturity.lower() if isinstance(maturity, str) else C.to_dt(maturity))
    rate = terms.get("fixed_rate", "par")
    if isinstance(rate, str):
        if rate != "par":
            raise ConfigError(f"fixed_rate {rate!r}: a number (percent) or the token 'par'", code="TERMS")
        if C.to_dt(p.reference_date) > eff:
            raise ConfigError(f"par needs an effective date on or after the reference date ({eff.date()} < {p.reference_date})", code="TERMS")
        rate = C.finite(probe.rate(curves=p.curve)) + float(extras.get("par_spread_bp", 0.0)) / 100.0
    swap = RLSwap(effective=eff.date(), maturity=maturity, sign=int(terms["direction"]), notional=float(terms["notional"]), fixed_rate=float(rate), conv=conv, cal=cal)
    return Built(swap, {"effective": swap.effective, "maturity": swap.termination, "fixed_rate": float(rate), "notional": swap.notional})


def _bind(method: str, **extra: Any) -> Dict[str, Any]:
    return {"target": {"method": method}, "kwargs": {"ctx": "@ctx", **extra}}


SWAP_BIND: Dict[str, Any] = {
    "value": _bind("value"),
    "dv01": _bind("dv01", tenors=list(DEFAULT_TENORS)),
    "gamma": _bind("gamma", tenors=list(DEFAULT_TENORS)),
    "rate": _bind("rate"),
    "delta_ladder": {**_bind("delta_ladder", tenors=list(DEFAULT_TENORS)), "keys": list(DEFAULT_TENORS), "reduce": "dict_of_floats"},
    "carry": _bind("carry"),
    "roll": _bind("roll"),
    "delta": _bind("delta"),
    "convexity": _bind("convexity"),
    "dv01_zero": _bind("dv01_zero"),
    "gamma_zero": _bind("gamma_zero"),
    "rateslib.carry_fwd": _bind("carry_fwd"),
    "rateslib.roll_static": _bind("roll_static"),
    "rateslib.fixings": _bind("fixings_layer"),
    "rateslib.resample": _bind("resample"),
}

swap = Kit(
    factory=swap_factory, asset_class="swap", default_bind=SWAP_BIND, cls=RLSwap,
    extra={"dv01_zero": "measure", "gamma_zero": "measure", "rateslib.carry_fwd": "layer", "rateslib.roll_static": "layer", "rateslib.fixings": "layer", "rateslib.resample": "layer"},
    doc="USD SOFR-style overnight-indexed swap on rateslib (module docstring: measures and layer definitions).",
)
