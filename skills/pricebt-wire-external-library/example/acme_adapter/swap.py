"""AcmeSwap: a USD SOFR-style overnight-indexed swap priced with acmelib, and the `swap` Kit (factory + default bindings) for pricebt's `swap` schema.

What is where (the split every adapter has to make):

* SIGN. acmelib reports everything from the RECEIVER's point of view; pricebt is payer-positive and holder-signed. The methods return the receiver's number times the
  holder's `direction` (payer +1), and every default binding carries `sign: -1` (RECEIVER_TO_PAYER) to finish the conversion, so the receiver-to-payer flip is ONE visible,
  overridable line of configuration. A binding cannot post-process a `Valuation`, so `value` (pv AND cash) applies the whole factor in code.
* UNITS. acmelib's rates are decimals: `rate` is bound with `scale: 100` (percent). acmelib's risk is per ONE MILLION notional: a binding's `scale` is a constant and cannot
  read the trade's notional, so the methods multiply by `notional / 1e6` themselves.
* SHAPE. acmelib's ladder is a pandas Series with lower-case labels and an extra 'on' bucket: the methods return it as it is and the reducer `acme_ladder_to_tenor_dict`
  (registered below, named in the binding) turns it into `dict[str, float]` keyed by upper-case tenors. A reducer, not a `function` target, because it needs nothing but the
  method's result; a `function` target is the tool when the conversion needs the instrument, the context or more than one call (the four layers below are that case).
* LAYERS. acmelib has no attribution. The four layers are FUNCTIONS over acmelib's primitives (`pv(..., as_of=)`, `cashflows`, `Market.rolled`, `Market.shifted`,
  `ZeroCurve.resampled`), bound with `function` targets; they follow docs/design/adr/005-attribution.md exactly (`carry = X_fwd - V0`, `roll = X_roll - X_fwd`,
  `delta = (V_base + cash - X_roll) + g.dz`, `convexity = 1/2 dz'H dz`, directional derivatives along the realised zero-rate move by Richardson extrapolation).
* MEASURE DEFINITIONS (the tie-out needs them equal to the reference's): `dv01` is the analytic annuity while the swap has not started and the sum of its ladder once it
  has; `gamma` is the second difference for a parallel bump of all ladder pillars; `delta_ladder` is per pillar par rate. `acme_zero_dv01` is an EXTENSION measure
  (declared in `Kit.extra`): the change for +1bp of acmelib's own curve variable, the zero rates.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, Mapping, Sequence

import pandas as pd

import acmelib as acme
from pricebt.contracts.binding import REDUCERS, register_reducer
from pricebt.contracts.spec import Built, Kit
from pricebt.errors import ConfigError, MethodCallError
from pricebt.pricable import Valuation

from . import _compat as C
from .conventions import swap_conventions
from .wrap import AcmePricer

DEFAULT_TENORS = ("3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y", "20Y", "30Y")
RECEIVER_TO_PAYER = -1.0  # acmelib's numbers are the receiver's; pricebt's are payer-positive
_H = 0.1  # fraction of the realised move used for the directional derivatives of the layers
_TENOR = re.compile(r"\d+[A-Za-z]")


def _acme(p: Any) -> AcmePricer:
    if not isinstance(p, AcmePricer):
        raise ConfigError(f"the market pricer is a {type(p).__name__}, not an AcmePricer: set `wrap` on the pricer role to acme_adapter:wrap", code="CFG-WRAP")
    return p


def _paid_before(flows: pd.DataFrame, d: Any) -> float:
    """Sum of the flows with pay date before `d` (ISO strings sort like dates)."""
    return float(flows.loc[flows["pay_date"] < C.iso(d), "amount"].sum())


# ------------------------------------------------------------------------------ the ladder reducer
def ladder_to_tenor_dict(series: pd.Series) -> Dict[str, float]:
    """acmelib's ladder Series ('3m', '6m', ..., plus 'on') -> {'3M': ..., '6M': ...}. The 'on' bucket has no pricebt name: it is dropped ONLY when it is empty, because dropping
    a bucket that carries risk would silently break `sum(ladder) == dv01`. Case and the tenor grammar are then checked by pricebt's own `series_to_tenor_dict`."""
    extra = [k for k in series.index if str(k) == "on"]
    for k in extra:
        if float(series[k]) != 0.0:
            raise MethodCallError(f"acmelib ladder bucket {k!r} carries {float(series[k])!r}: pricebt's ladder has no such bucket, so it cannot be dropped silently")
    return REDUCERS["series_to_tenor_dict"](series.drop(extra))


register_reducer("acme_ladder_to_tenor_dict", ladder_to_tenor_dict)  # idempotent for this function; a different function under this name is a CFG-REDUCER error


# ------------------------------------------------------------------------------ the instrument
class AcmeSwap:
    """One unit = one swap of `notional` (unsigned). `direction` +1 = payer of fixed (pricebt's sign). Plain data: the acmelib contract, the direction."""

    asset_class = "swap"

    def __init__(self, *, contract: acme.Swap, direction: int):
        if direction not in (1, -1):
            raise ConfigError(f"direction must be +1 (pay fixed) or -1 (receive fixed), got {direction!r}", code="SWAP")
        self.contract, self.direction = contract, int(direction)

    @property
    def notional_amount(self) -> float:
        return self.contract.notional

    # ---- helpers
    def started(self, p: AcmePricer) -> bool:
        return C.to_date(self.contract.start) < p.reference_date

    def expired(self, p: AcmePricer) -> bool:
        return C.to_date(self.contract.last_payment) < p.reference_date

    def _per_unit(self, per_mm: Any) -> Any:
        """acmelib's per-million risk (receiver's sign) -> this position's: notional / 1e6, times the direction. The binding's `sign` finishes the receiver-to-payer flip."""
        return per_mm * (self.direction * self.contract.notional / acme.MM)

    def _ladder(self, ctx: Any, tenors: Sequence[str]) -> pd.Series:
        p = _acme(ctx.pricer)
        key = ("acme_swap_ladder", id(self), tuple(tenors))
        if key not in ctx.cache:
            with C.translate(p.ts), C.guard(p.reference_date):
                ctx.cache[key] = self._per_unit(acme.bucket_risk(self.contract, p.need_market(), [t.lower() for t in tenors]))
        return ctx.cache[key].copy()

    # ---- value
    def value(self, *, ctx: Any) -> Valuation:
        p, prev = _acme(ctx.pricer), ctx.prev_pricer
        ref = p.reference_date
        with C.translate(p.ts), C.guard(ref):
            m = p.need_market()
            pv, cash = acme.pv(self.contract, m), 0.0
            if prev is not None and prev.reference_date < ref:
                cash = _paid_before(acme.cashflows(self.contract, m, since=C.iso(prev.reference_date)), ref)  # flows paid in [prev, ref)
        k = self.direction * RECEIVER_TO_PAYER  # a Valuation cannot go through a binding's `sign`: pv and cash convert here
        return Valuation(ctx.ts, k * pv, k * cash, 0.0, {"reference_date": ref})

    # ---- measures
    def rate(self, *, ctx: Any) -> float:
        """Par rate of the remaining swap as acmelib gives it: a DECIMAL (the binding scales it to percent). NaN once it has expired."""
        p = _acme(ctx.pricer)
        if self.expired(p):
            return float("nan")
        with C.translate(p.ts), C.guard(p.reference_date):
            return acme.par_rate(self.contract, p.need_market())

    def dv01(self, *, ctx: Any, tenors: Sequence[str] = DEFAULT_TENORS) -> float:
        p = _acme(ctx.pricer)
        if self.expired(p):
            return 0.0
        if self.started(p):  # the analytic annuity keeps the whole accrued coupon, which no longer moves with rates: a started swap's market dv01 is its ladder's sum
            return float(self._ladder(ctx, tenors).sum())
        with C.translate(p.ts), C.guard(p.reference_date):
            return float(self._per_unit(acme.pv01(self.contract, p.need_market())))

    def gamma(self, *, ctx: Any, tenors: Sequence[str] = DEFAULT_TENORS) -> float:
        p = _acme(ctx.pricer)
        with C.translate(p.ts), C.guard(p.reference_date):
            return float(self._per_unit(acme.gamma(self.contract, p.need_market(), [t.lower() for t in tenors])))

    def delta_ladder(self, *, ctx: Any, tenors: Sequence[str] = DEFAULT_TENORS) -> pd.Series:
        """acmelib's own shape, scaled to this position: a Series with lower-case labels and an 'on' bucket (the reducer converts it)."""
        return self._ladder(ctx, tenors)

    def acme_zero_dv01(self, *, ctx: Any) -> float:
        """EXTENSION measure: change in value for +1bp of every zero rate of acmelib's curve (central difference of full revaluations), position-scaled, receiver's sign."""
        p = _acme(ctx.pricer)
        with C.translate(p.ts), C.guard(p.reference_date):
            m = p.need_market()
            return self.direction * (acme.pv(self.contract, m.shifted(0.5e-4)) - acme.pv(self.contract, m.shifted(-0.5e-4)))


# ------------------------------------------------------------------------------ the layers: functions over acmelib's primitives
def _decomposition(sw: AcmeSwap, ctx: Any) -> Dict[str, float]:
    """carry, roll, delta, convexity and `residual` (what the engine reports as `unexplained`), per unit, in the receiver's sign times the direction (the binding's `sign` completes it)."""
    key = ("acme_swap_decomposition", id(sw))
    if key in ctx.cache:
        return ctx.cache[key]
    p1, p0 = _acme(ctx.pricer), ctx.prev_pricer
    if p0 is None:
        raise ConfigError("layers need a previous pricer (ctx.prev_pricer)", code="LAYER")
    p0 = _acme(p0)
    t0, t1 = p0.reference_date, p1.reference_date
    if t1 < t0:
        raise ValueError(f"the layers need t1 >= t0, got {t0} -> {t1}")
    c = sw.contract
    if C.to_date(c.last_payment) < t0:  # nothing left at t0: nothing moves
        out = {k: 0.0 for k in ("carry", "roll", "delta", "convexity", "residual")}
    else:
        with C.translate(p1.ts):
            m0, m1 = p0.need_market(), p1.need_market()
            with C.guard(t0):  # the t0 world: V0, and the t0 flows valued at t1 (flows paid in [t0, t1) at face)
                v0 = acme.pv(c, m0)
                x_fwd = acme.pv(c, m0, as_of=C.iso(t1)) + _paid_before(acme.cashflows(c, m0, since=C.iso(t0)), t1)
            with C.guard(t1):  # the t1 world
                v1 = acme.pv(c, m1)
                cash = _paid_before(acme.cashflows(c, m1, since=C.iso(t0)), t1)
                rolled = m0.rolled(C.iso(t1), fixings=m1.fixings)  # unchanged in TENOR space, the realised fixings
                x_roll = acme.pv(c, rolled) + cash
                base = acme.Market(rolled.curve.resampled(m1.curve.pillars), m1.fixings)  # the rolled curve read at the t1 curve's pillars
                v_base = acme.pv(c, base)
                dz = [z1 - zb for z1, zb in zip(m1.curve.zeros, base.curve.zeros)]  # the realised move of the zero rates

                def v_eps(eps: float) -> float:
                    return acme.pv(c, base.shifted([eps * d for d in dz]))

                d_a, d_b = [(v_eps(h) - v_eps(-h)) / (2 * h) for h in (_H, _H / 2)]
                s_a, s_b = [(v_eps(h) + v_eps(-h) - 2.0 * v_base) / (2 * h * h) for h in (_H, _H / 2)]
        first, second = (4.0 * d_b - d_a) / 3.0, (4.0 * s_b - s_a) / 3.0  # Richardson: the directional derivatives are smooth
        out = {"carry": x_fwd - v0, "roll": x_roll - x_fwd, "delta": (v_base + cash - x_roll) + first, "convexity": second, "residual": (v1 - v_base) - first - second}
    out = {k: sw.direction * v for k, v in out.items()}
    ctx.cache[key] = out
    return out


def carry(swap: AcmeSwap, ctx: Any) -> float:
    return _decomposition(swap, ctx)["carry"]


def roll(swap: AcmeSwap, ctx: Any) -> float:
    return _decomposition(swap, ctx)["roll"]


def delta(swap: AcmeSwap, ctx: Any) -> float:
    return _decomposition(swap, ctx)["delta"]


def convexity(swap: AcmeSwap, ctx: Any) -> float:
    return _decomposition(swap, ctx)["convexity"]


# ------------------------------------------------------------------------------ factory
def _resolve_effective(spec: Any, ref: str, cal: str, s: Any) -> str:
    spot = acme.add_business_days(ref, s.spot_lag, cal)
    if isinstance(spec, str) and spec.strip().lower() == "spot":
        return spot
    if isinstance(spec, str) and _TENOR.fullmatch(spec.strip()):
        return acme.adjust(acme.add_tenor(spot, spec.strip().lower()), s.bdc, cal)  # a forward start: a tenor from spot
    return acme.adjust(C.iso(spec), s.bdc, cal)  # a date is adjusted to a business day


def swap_factory(pricer: Any, ts: Any, *, terms: Mapping[str, Any], conventions: Mapping[str, Any]) -> Built:
    """`(pricer, ts, *, terms, conventions) -> Built(AcmeSwap, resolved terms)`: `effective` (date | 'spot' | tenor), `maturity` (date | tenor) and `fixed_rate` (PERCENT number
    | 'par') are resolved HERE, at the fill pricer, and returned as concrete dates and a percent rate. `extras: {par_spread_bp: x}` with `fixed_rate: par` strikes x bp off par."""
    p, s = _acme(pricer), swap_conventions(conventions)
    extras = dict(terms.get("extras") or {})
    unknown = sorted(set(extras) - {"par_spread_bp"})
    if unknown:
        raise ConfigError(f"swap extras {unknown} are not supported; allowed: ['par_spread_bp']", code="CFG-EXTRAS")
    spread = extras.get("par_spread_bp")
    if spread is not None and (isinstance(spread, bool) or not isinstance(spread, (int, float)) or not math.isfinite(spread)):
        raise ConfigError(f"extras par_spread_bp must be a finite number of basis points, got {spread!r}", code="CFG-EXTRAS")
    rate, notional, direction = terms.get("fixed_rate", "par"), float(terms["notional"]), int(terms["direction"])
    if not isinstance(rate, str) and spread is not None:
        raise ConfigError("extras par_spread_bp applies only to fixed_rate 'par'", code="CFG-EXTRAS")
    mat = terms["maturity"]
    maturity = mat.strip().lower() if isinstance(mat, str) and _TENOR.fullmatch(mat.strip()) else C.iso(mat)  # acmelib: lower-case tenors, ISO dates
    with C.translate(p.ts):
        cal = p.calendar(s.calendar)
        kw = s.swap_kwargs(cal)
        effective = _resolve_effective(terms.get("effective", "spot"), C.iso(p.reference_date), cal, s)
        if isinstance(rate, str):
            if rate.strip().lower() != "par":
                raise ConfigError(f"fixed_rate must be a number (PERCENT) or 'par', got {rate!r}", code="CFG-TERMS")
            probe = acme.Swap(effective, maturity, notional, 0.0, **kw)  # par does not depend on the strike
            with C.guard(p.reference_date):
                rate = acme.par_rate(probe, p.need_market()) * 100.0 + (spread or 0.0) / 100.0  # acmelib's decimal -> percent; a spread in bp is 0.01 percent
        contract = acme.Swap(effective, maturity, notional, float(rate) / 100.0, **kw)  # percent -> acmelib's decimal
    return Built(AcmeSwap(contract=contract, direction=direction),
                 {"effective": C.to_date(contract.start), "maturity": C.to_date(contract.end), "fixed_rate": float(rate), "notional": notional})


# ------------------------------------------------------------------------------ the default bindings
def _method(name: str, **extra: Any) -> Dict[str, Any]:
    return {"target": {"method": name}, "kwargs": {"ctx": "@ctx", **extra}}


def _layer(fn: str) -> Dict[str, Any]:
    """A layer is a FUNCTION of the instrument and the context: `function` targets are dotted paths, so `registry.allow` must name `acme_adapter`."""
    return {"target": {"function": f"acme_adapter.swap:{fn}"}, "kwargs": {"swap": "@instrument", "ctx": "@ctx"}, "sign": RECEIVER_TO_PAYER}


SWAP_BIND: Dict[str, Any] = {
    "value": _method("value"),  # a Valuation: sign and scale cannot apply, `value` converts in code
    "dv01": {**_method("dv01", tenors=list(DEFAULT_TENORS)), "sign": RECEIVER_TO_PAYER},
    "gamma": {**_method("gamma", tenors=list(DEFAULT_TENORS)), "sign": RECEIVER_TO_PAYER},
    "rate": {**_method("rate"), "scale": 100.0},  # acmelib's decimals -> the schema's percent
    "delta_ladder": {**_method("delta_ladder", tenors=list(DEFAULT_TENORS)), "keys": list(DEFAULT_TENORS), "reduce": "acme_ladder_to_tenor_dict", "sign": RECEIVER_TO_PAYER},
    "carry": _layer("carry"),
    "roll": _layer("roll"),
    "delta": _layer("delta"),
    "convexity": _layer("convexity"),
    "acme_zero_dv01": {**_method("acme_zero_dv01"), "sign": RECEIVER_TO_PAYER},
}

swap = Kit(factory=swap_factory, asset_class="swap", default_bind=SWAP_BIND, cls=AcmeSwap, extra={"acme_zero_dv01": "measure"},
           doc="USD SOFR-style overnight-indexed swap on acmelib (module docstring: where each conversion lives, measure and layer definitions).")
