"""ZetaSwap: a USD SOFR overnight-indexed swap priced by the zeta service, and the `swap` Kit (factory + default bindings) for pricebt's `swap` schema.

What is where (the split every adapter has to make):

* SIGN. zeta is HOLDER-signed already: `PAY` is the payer of fixed, a `RECEIVE` trade's amounts are the negative of the `PAY` trade's, `RISK_10BP` is positive for a payer. So `value`, the risks and
  the four layers need NO direction handling here. ONE exception: `LADDER_10BP` is reported with the OPPOSITE sign to `RISK_10BP` (a payer's ladder is negative), so the `delta_ladder` binding
  carries `sign: -1`. That is the only `sign` in the block.
* UNITS. zeta's numbers are per +10bp (`RISK_10BP`, the ladder), per (10bp)^2 (`CONVEXITY_10BP`) and in basis points (`PAR_BP`); the methods return them AS zeta reports them and the bindings
  convert, one visible line each: `dv01` `scale: 0.1`, `gamma` `scale: 0.01` (second difference for +-10bp = gamma x 100), `rate` `scale: 0.01` (bp -> percent), `delta_ladder`
  `scale: 0.1`. The notional is per million in the REQUEST (`notional_mm`), so the trade carries `notional / 1e6` and every answer is already "per unit as built with its notional".
* SHAPE. zeta's ladder is a list of `{"pillar_months", "risk"}` for twelve pillars (1M, 3M, ..., 30Y). The reducer `zeta_ladder_to_tenor_dict` (registered below, named in the binding) turns it into
  `dict[str, float]` keyed by upper-case tenors, one key per pillar (`1M` ... `30Y`, twelve): nothing is folded or dropped, the ladder sums to dv01.
* DV01 DEFINITION (the tie-out needs it equal to the reference's, `pricebt-layers-and-ladder`): matured 0; started (`start < reference date`) the market dv01 = the SUM OF THE LADDER
  (`-sum(LADDER_10BP)`: zeta's ladder uses the same +-0.5bp per-pillar recipe as the reference, so the sum is the same number to round-off; `RISK_10BP` is a +-1bp PARALLEL difference and
  differs from it by 2e-7 relative, which the engine's baseline `tay_unexplained` amplifies past its tolerance); not started the ANALYTIC ANNUITY = the fixed leg's PV01, obtained EXACTLY from zeta
  as `NPV(K - 1bp) - NPV(K)` (the fixed leg is linear in K). At par the two agree to 1e-7, off par they differ by percents: the reference answers the annuity, so this stack does too.
* LAYERS. zeta has no attribution ("assemble what you need from repeated price() calls with different as_of and scenario"). The four layers follow docs/design/adr/005-attribution.md exactly
  (`carry = X_fwd - V0`, `roll = X_roll - X_fwd`, `delta = (V_base + cash - X_roll) + g.dz`, `convexity = 1/2 dz'H dz`, directional derivatives by Richardson extrapolation). X_fwd is
  the t0 market priced `as_of` t1 (zeta's date-space roll: forwards realised, implied fixings). The tenor-space roll, the resampled base and the shocked worlds along the realised zero-rate
  move `dz` need a curve zeta's scenarios cannot express (`scenario` is only `parallel_bp` or `roll`, one key), so they are DERIVED MARKETS uploaded once each (`wrap.ZetaPricer.world_id`, content
  addressed). The rolled world is exactly zeta's `roll` scenario, built by hand because it must carry the REALISED t1 fixings (the scenario would imply them from the t0 curve).
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from pricebt.contracts.binding import register_reducer
from pricebt.contracts.spec import Built, Kit
from pricebt.errors import ConfigError, MethodCallError
from pricebt.pricable import Valuation
from pricebt.timeutil import Calendar

from . import _compat as C
from .conventions import swap_conventions
from .wrap import ZetaPricer

#: zeta's twelve risk pillars (months 1, 3, 6, 12, 24, 36, 60, 84, 120, 180, 240, 360) as pricebt tenors: the ladder buckets this stack reports. The reference stack's default ladder starts at 3M
#: (eleven pillars); a tie-out that compares risk numbers puts the reference on THESE twelve (`config/refstack_zeta_pillars.yaml`, next to this package's other configs), see the README of the example.
ZETA_TENORS: Tuple[str, ...] = ("1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y", "20Y", "30Y")
_PILLAR_TENOR = dict(zip((1, 3, 6, 12, 24, 36, 60, 84, 120, 180, 240, 360), ZETA_TENORS))
_MEASURES = ["NPV", "RISK_10BP", "CONVEXITY_10BP", "PAR_BP", "LADDER_10BP"]
_H = 0.1  # fraction of the realised move used for the directional derivatives of the layers (Richardson at h and h/2)
_PAY_LAG_BD = 2  # zeta pays 2 business days after the accrual end (a fixed convention): the interval over which a paid flow can still depend on a fixing published inside it
_TENOR = re.compile(r"(\d+)([YyMm])")
_ANY_TENOR = re.compile(r"\d+[A-Za-z]")


# ------------------------------------------------------------------------------ the ladder reducer
def ladder_to_tenor_dict(rows: Sequence[Mapping[str, Any]]) -> Dict[str, float]:
    """zeta's `[{"pillar_months": 1, "risk": x}, ...]` -> `{"1M": x, "3M": ...}`: upper-case tenors, all twelve pillars, none folded or dropped. A pillar zeta adds that this table does not know is
    dropped ONLY when it carries no risk: dropping one that does would silently break `sum(ladder) == dv01`."""
    out = {t: 0.0 for t in ZETA_TENORS}
    for r in rows:
        m, x = int(r["pillar_months"]), float(r["risk"])
        tenor = _PILLAR_TENOR.get(m)
        if tenor is None:
            if x != 0.0:
                raise MethodCallError(f"zeta ladder pillar of {m} months carries {x!r}: pricebt's ladder has no such bucket, so it cannot be dropped silently")
            continue
        out[tenor] += x
    return out


register_reducer("zeta_ladder_to_tenor_dict", ladder_to_tenor_dict)  # idempotent for this function; a different function under this name is a CFG-REDUCER error


def _zeta(p: Any) -> ZetaPricer:
    if not isinstance(p, ZetaPricer):
        raise ConfigError(f"the market pricer is a {type(p).__name__}, not a ZetaPricer: set `wrap` on the pricer role to zeta_adapter:wrap", code="CFG-WRAP")
    return p


def _interp_z(days: np.ndarray, z: np.ndarray, at: np.ndarray) -> np.ndarray:
    """Zero rates (bp, ACT/365) of a zeta curve at other day offsets: ln DF linear in days between nodes (day 0 = DF 1), the last segment's forward beyond the last node (what zeta does)."""
    d = np.concatenate(([0.0], days))
    ln = np.concatenate(([0.0], -z * 1e-4 * days / 365.0))
    out = np.interp(at, d, ln)
    beyond = at > d[-1]
    if beyond.any():
        fwd = (ln[-1] - ln[-2]) / (d[-1] - d[-2])
        out[beyond] = ln[-1] + fwd * (at[beyond] - d[-1])
    return -out * 365.0 / at * 1e4


# ------------------------------------------------------------------------------ the instrument
class ZetaSwap:
    """One unit = one swap of `notional` (unsigned). `direction` +1 = payer of fixed (pricebt's sign). Plain data: the zeta trade (a start DATE, the end as booked: a tenor or a date, the
    strike in bp) and the direction. It deep-copies and pickles and never keeps the pricer."""

    asset_class = "swap"

    def __init__(self, *, start: Any, end: Any, rate_bp: float, notional: float, direction: int):
        if direction not in (1, -1):
            raise ConfigError(f"direction must be +1 (pay fixed) or -1 (receive fixed), got {direction!r}", code="SWAP")
        self.start, self.end, self.rate_bp, self.notional, self.direction = C.to_date(start), end, float(rate_bp), float(notional), int(direction)

    @property
    def notional_amount(self) -> float:
        return self.notional

    # ---- the zeta trade
    def trade(self, rate_bp: Optional[float] = None) -> Dict[str, Any]:
        """The request's trade dict. Re-priced after inception with the booked START DATE, the booked strike and the ORIGINAL end (a tenor is then measured from the same start): ZETA_DOCS section 4."""
        return {"product": "OIS", "leg_fixed": "PAY" if self.direction == 1 else "RECEIVE", "notional_mm": self.notional / 1e6, "start": self.start.isoformat(),
                "end": dict(self.end) if isinstance(self.end, dict) else self.end, "fixed_rate_bp": self.rate_bp if rate_bp is None else rate_bp}

    @property
    def key(self) -> Tuple[Any, ...]:
        return (self.start, tuple(sorted(self.end.items())) if isinstance(self.end, dict) else self.end, self.rate_bp, self.notional, self.direction)

    def started(self, p: ZetaPricer) -> bool:
        return self.start < p.reference_date

    def _res(self, p: ZetaPricer) -> Dict[str, Any]:
        """The result of ONE request per snapshot and trade: every measure at `as_of == asof`, the price answers memoised on the pricer (a pricer is immutable data)."""
        return p.memo(("measures", self.key), lambda: p.price(p.market_id(), p.reference_date, [self.trade()], _MEASURES)[0])

    def _annuity(self, p: ZetaPricer) -> float:
        """The fixed leg's PV01 (currency per +1bp, holder-signed): exactly `NPV(K - 1bp) - NPV(K)`, the fixed leg being linear in K."""
        def go() -> float:
            a, b = p.price(p.market_id(), p.reference_date, [self.trade(), self.trade(self.rate_bp - 1.0)], ["NPV"])
            return b["measures"]["NPV"] - a["measures"]["NPV"]
        return p.memo(("annuity", self.key), go)

    def _value_in(self, p: ZetaPricer, market: Mapping[str, Any]) -> float:
        """V (NPV plus the flow paid on the valuation date, pricebt's convention) in a market DERIVED from snapshots, valued at its own asof."""
        r = p.price(p.world_id(market), market["asof"], [self.trade()], ["NPV"])[0]
        return r["measures"]["NPV"] + r["paid_on_as_of"]

    # ---- value
    def value(self, *, ctx: Any) -> Valuation:
        p, prev = _zeta(ctx.pricer), ctx.prev_pricer
        ref = p.reference_date
        r = self._res(p)
        cash = 0.0
        if prev is not None and prev.reference_date < ref:
            cash = self._cash(_zeta(prev), p)
        return Valuation(ctx.ts, r["measures"]["NPV"] + r["paid_on_as_of"], cash, 0.0, {"reference_date": ref})

    def _cash(self, p0: ZetaPricer, p1: ZetaPricer) -> float:
        """Flows paid in [t0, t1) with the amounts of the t1 world. zeta's CASH_TO_DATE covers (asof, as_of], so the flow paid ON t0 comes from the t0 answer and the flow paid ON t1 is taken out.
        A flow paid before t1 needs the fixings up to two business days before it: when t1 is no later than t0 + 2 business days they are all published at t0, so the t0 market is enough. Beyond
        that a flow can depend on a fixing published inside the interval, and the t0 market would IMPLY it from its curve: the market is then rebuilt with the t1 fixings (a derived world)."""
        def go() -> float:
            t0, t1 = p0.reference_date, p1.reference_date
            cal = Calendar(p1.snapshot.calendars[p1.calendar_name].holidays) if p1.calendar_name else Calendar.weekends_only()
            if t1 <= cal.add_business_days(t0, _PAY_LAG_BD):
                mid, owner = p0.market_id(), p0
            else:
                owner = p0
                mid = p0.world_id({**p0.need_market(), "fixings": p1.need_market()["fixings"]})
            r = owner.price(mid, t1, [self.trade()], ["CASH_TO_DATE"])[0]
            return self._res(p0)["paid_on_as_of"] + r["measures"]["CASH_TO_DATE"] - r["paid_on_as_of"]
        return p1.memo(("cash", self.key, p0.digest), go)

    # ---- measures (in zeta's units: the bindings convert)
    def rate(self, *, ctx: Any) -> float:
        """Par rate of the remaining swap in BASIS POINTS (the binding scales it to percent). NaN once no flow is left (zeta answers null)."""
        par = self._res(_zeta(ctx.pricer))["measures"]["PAR_BP"]
        return float("nan") if par is None else float(par)

    def dv01(self, *, ctx: Any) -> float:
        """Currency per +10bp (the binding scales it to +1bp): matured 0; started the market dv01 = the ladder sum (LADDER_10BP carries the opposite sign to RISK_10BP); not started the analytic annuity."""
        p = _zeta(ctx.pricer)
        r = self._res(p)["measures"]
        if r["PAR_BP"] is None:
            return 0.0
        return -float(sum(x["risk"] for x in r["LADDER_10BP"])) if self.started(p) else 10.0 * self._annuity(p)

    def gamma(self, *, ctx: Any) -> float:
        """Currency per (10bp)^2 for a parallel move of the par curve (the binding scales it to per bp^2)."""
        return float(self._res(_zeta(ctx.pricer))["measures"]["CONVEXITY_10BP"])

    def delta_ladder(self, *, ctx: Any) -> List[Dict[str, Any]]:
        """zeta's own shape (twelve `{pillar_months, risk}` rows, per +10bp, OPPOSITE sign to RISK_10BP); the reducer converts it, the binding signs and scales it."""
        return [dict(x) for x in self._res(_zeta(ctx.pricer))["measures"]["LADDER_10BP"]]

    # ---- layers
    def decomposition(self, ctx: Any) -> Dict[str, float]:
        """carry, roll, delta, convexity and `residual` (what the engine reports as `unexplained`), per unit, holder-signed (zeta signs by leg)."""
        key = ("zeta_swap_decomposition", id(self))
        if key in ctx.cache:
            return ctx.cache[key]
        p1, p0 = _zeta(ctx.pricer), ctx.prev_pricer
        if p0 is None:
            raise ConfigError("layers need a previous pricer (ctx.prev_pricer)", code="LAYER")
        p0 = _zeta(p0)
        t0, t1 = p0.reference_date, p1.reference_date
        if t1 < t0:
            raise ValueError(f"the layers need t1 >= t0, got {t0} -> {t1}")
        r0 = self._res(p0)
        if r0["measures"]["PAR_BP"] is None:  # nothing left at t0 (no flow paid on or after it): nothing moves
            out = {k: 0.0 for k in ("carry", "roll", "delta", "convexity", "residual")}
            ctx.cache[key] = out
            return out
        v0, paid0 = r0["measures"]["NPV"] + r0["paid_on_as_of"], r0["paid_on_as_of"]
        # carry: the t0 market priced as_of t1 (date space: forwards realised, fixings implied by the t0 curve), flows paid in [t0, t1) at face
        if t1 == t0:
            x_fwd = v0
        else:
            f = p0.price(p0.market_id(), t1, [self.trade()], ["NPV", "CASH_TO_DATE"])[0]
            x_fwd = f["measures"]["NPV"] + f["measures"]["CASH_TO_DATE"] + paid0  # CASH_TO_DATE = (t0, t1] at face, incl. the flow paid on t1; the flow paid on t0 is paid0
        r1 = self._res(p1)
        v1 = r1["measures"]["NPV"] + r1["paid_on_as_of"]
        cash = self._cash(p0, p1) if t1 > t0 else 0.0
        # the worlds anchored at t1, all with the t1 calendar and the REALISED t1 fixings
        m1, m0 = p1.need_market(), p0.need_market()
        nodes0 = np.array(m0["curve"]["nodes"], dtype=float)  # [days from t0, zero bp]: the rolled curve has the SAME offsets from t1 and the same zero rates (tenor space)
        nodes1 = np.array(m1["curve"]["nodes"], dtype=float)
        days1 = nodes1[:, 0]
        same_grid = nodes0.shape == nodes1.shape and bool(np.array_equal(nodes0[:, 0], days1))
        zb = nodes0[:, 1].copy() if same_grid else _interp_z(nodes0[:, 0], nodes0[:, 1], days1)  # the rolled curve read at the t1 curve's node offsets (V_base): the same numbers when the grids agree
        dz = nodes1[:, 1] - zb  # the realised move of the zero rates (bp), what the layers' directional derivatives follow

        def world(nodes: np.ndarray) -> Dict[str, Any]:
            return {**m1, "curve": {**m1["curve"], "nodes": [[int(d), float(z)] for d, z in zip(days1, nodes)]}}

        x_roll = self._value_in(p1, {**m1, "curve": {**m1["curve"], "nodes": [[int(d), float(z)] for d, z in nodes0]}}) + cash
        v_base = self._value_in(p1, world(zb))

        def v_eps(eps: float) -> float:
            return self._value_in(p1, world(zb + eps * dz))

        d_a, d_b = [(v_eps(h) - v_eps(-h)) / (2 * h) for h in (_H, _H / 2)]
        s_a, s_b = [(v_eps(h) + v_eps(-h) - 2.0 * v_base) / (2 * h * h) for h in (_H, _H / 2)]
        first, second = (4.0 * d_b - d_a) / 3.0, (4.0 * s_b - s_a) / 3.0  # Richardson: the directional derivatives are smooth
        out = {"carry": x_fwd - v0, "roll": x_roll - x_fwd, "delta": (v_base + cash - x_roll) + first, "convexity": second, "residual": (v1 - v_base) - first - second}
        ctx.cache[key] = out
        return out

    def carry(self, *, ctx: Any) -> float:
        return self.decomposition(ctx)["carry"]

    def roll(self, *, ctx: Any) -> float:
        return self.decomposition(ctx)["roll"]

    def delta(self, *, ctx: Any) -> float:
        return self.decomposition(ctx)["delta"]

    def convexity(self, *, ctx: Any) -> float:
        return self.decomposition(ctx)["convexity"]


# ------------------------------------------------------------------------------ factory
def _end_spec(mat: Any) -> Any:
    """`maturity` (a tenor or a date) -> zeta's `end`: `{"tenor_years": n}`, `{"tenor_months": n}` or an ISO date string. zeta measures a tenor in WHOLE months or years only (Z204)."""
    if isinstance(mat, str) and _ANY_TENOR.fullmatch(mat.strip()):
        m = _TENOR.fullmatch(mat.strip())
        if m is None:
            raise ConfigError(f"maturity {mat!r}: zeta measures a tenor in whole months or years only (Y, M)", code="CFG-TERMS")
        return {"tenor_years" if m.group(2) in "Yy" else "tenor_months": int(m.group(1))}
    return C.iso(mat)


def swap_factory(pricer: Any, ts: Any, *, terms: Mapping[str, Any], conventions: Mapping[str, Any]) -> Built:
    """`(pricer, ts, *, terms, conventions) -> Built(ZetaSwap, resolved terms)`: `effective` (date | 'spot' | tenor), `maturity` (date | tenor) and `fixed_rate` (PERCENT number | 'par') are resolved
    HERE, at the fill pricer, by ONE zeta request (two for a forward-starting tenor), and returned as concrete dates and a percent rate. `extras: {par_spread_bp: x}` with `fixed_rate: par`
    strikes x bp off par."""
    p, s = _zeta(pricer), swap_conventions(conventions)
    if p.calendar_name is None or s.calendar != p.calendar_name:
        raise ConfigError(f"conventions.calendar is {s.calendar!r} but the snapshot's zeta market carries the calendar {p.calendar_name!r} (a zeta market holds one calendar)", code="CFG-CALENDAR")
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
    if isinstance(rate, str) and rate.strip().lower() != "par":
        raise ConfigError(f"fixed_rate must be a number (PERCENT) or 'par', got {rate!r}", code="CFG-TERMS")
    end = _end_spec(terms["maturity"])
    eff = terms.get("effective", "spot")
    mid, leg = p.market_id(), ("PAY" if direction == 1 else "RECEIVE")
    probe = {"product": "OIS", "leg_fixed": leg, "notional_mm": notional / 1e6, "start": {"spot_lag": 2}, "end": end, "fixed_rate_bp": "PAR" if isinstance(rate, str) else float(rate) * 100.0}
    if isinstance(eff, str) and eff.strip().lower() == "spot":
        pass
    elif isinstance(eff, str) and _ANY_TENOR.fullmatch(eff.strip()):  # a forward start: a tenor from spot; zeta measures a tenor from the (adjusted) start, so the END of a spot-start trade of that tenor is it
        fwd = _end_spec(eff)
        (r,) = p.price(mid, p.reference_date, [{**probe, "end": fwd, "fixed_rate_bp": "PAR"}], ["NPV"])
        probe["start"] = r["resolved"]["end"]
    else:
        probe["start"] = C.iso(eff)  # a date: zeta adjusts it modified following
    (r,) = p.price(mid, p.reference_date, [probe], ["NPV"])
    res = r["resolved"]
    rate_bp = float(rate) * 100.0 if not isinstance(rate, str) else float(res["fixed_rate_bp"]) + float(spread or 0.0)
    rate_pct = float(rate) if not isinstance(rate, str) else rate_bp / 100.0
    sw = ZetaSwap(start=res["start"], end=end, rate_bp=rate_bp, notional=notional, direction=direction)
    return Built(sw, {"effective": C.to_date(res["start"]), "maturity": C.to_date(res["end"]), "fixed_rate": rate_pct, "notional": notional})


# ------------------------------------------------------------------------------ the default bindings
def _method(name: str, **extra: Any) -> Dict[str, Any]:
    return {"target": {"method": name}, "kwargs": {"ctx": "@ctx"}, **extra}


ZETA_TO_LADDER_SIGN = -1.0  # LADDER_10BP is reported with the OPPOSITE sign to RISK_10BP (ZETA_DOCS section 6)

SWAP_BIND: Dict[str, Any] = {
    "value": _method("value"),  # a Valuation, already holder-signed by zeta
    "dv01": _method("dv01", scale=0.1),  # per +10bp -> per +1bp
    "gamma": _method("gamma", scale=0.01),  # per (10bp)^2 -> per bp^2
    "rate": _method("rate", scale=0.01),  # basis points -> percent
    "delta_ladder": _method("delta_ladder", reduce="zeta_ladder_to_tenor_dict", keys=list(ZETA_TENORS), sign=ZETA_TO_LADDER_SIGN, scale=0.1),
    "carry": _method("carry"),
    "roll": _method("roll"),
    "delta": _method("delta"),
    "convexity": _method("convexity"),
}

swap = Kit(factory=swap_factory, asset_class="swap", default_bind=SWAP_BIND, cls=ZetaSwap,
           doc="USD SOFR overnight-indexed swap on the zeta service (module docstring: where each conversion lives, measure and layer definitions).")
