"""ArbsSwap: a USD SOFR-style OIS priced through ARBS's OWN `RLIRSwapCurve` value functions, and the `swap` Kit (factory + default bindings) of
pricebt's `swap` schema.

Split of responsibilities (the point of this adapter, spec Z5):

* `value`, `rate`, `dv01` of an UNSTARTED swap go through ARBS's own value functions on ARBS's own curve wrapper: `curve_handle.build_irswap`,
  `.npv`, `.fair_rate`, `.pv01`. This is genuinely ARBS pricing the trade, not a rebuild of its logic.
* `delta_ladder`, `gamma`, `carry`, `roll`, `delta`, `convexity` (and `dv01` of a STARTED swap, which is the ladder's sum) go through pricebt's own
  rateslib layer/ladder plumbing (`pricebt.contrib.rateslib.ladder`, `.layers`) on a plain `rl.IRS`, because ARBS's `RLIRSwapCurve.npv()` / `.pv01()`
  ALWAYS close over `self._rl_curve_handle` and cannot be pointed at an arbitrary SCENARIO curve (verified by reading `RLIRSwapCurve.npv`/`.pv01` in
  `ARBS/Query/IRSwaps/backends/rateslib/RLIRSwapCurve.py`: both rebuild a throwaway instrument bound to `self._rl_curve_handle` and ignore whatever
  curve the caller's own instrument carries). The layer machinery needs to price the SAME swap on curves that are not any real fetched snapshot
  (rolled, resampled, zero-shifted), so it is built as a plain `rl.IRS`, exactly like `pricebt.contrib.rateslib.swap.RLSwap` does -- same effective
  date, same tenor/termination, same fixed rate and signed notional as the ARBS instrument, so the two represent the identical economic swap
  (cross-checked in GATE 2 and in `test_the_bare_and_arbs_instruments_agree_on_pv01`).

Units and signs (one visible line each, per this project's convention):
* `rate`: ARBS's `fair_rate` is DECIMAL; the binding's `scale: 100.0` converts to the schema's percent.
* `value` / `dv01` / `pv01`: ARBS's own notional sign already matches pricebt's holder sign (a PAYER carries a POSITIVE notional in both --
  verified on a real trade: a payer struck below the market par rate gains, exactly as a positive-notional `rl.IRS` does under rateslib's own
  convention). No sign flip is applied anywhere in `ARBS_SWAP_BIND`.
* `dv01` of a STARTED swap: pricebt's documented rule (`skills/pricebt-layers-and-ladder/references/dv01-one-definition.md`) is the SUM of the delta
  ladder, never ARBS's raw `pv01()` (which keeps the accrued coupon, no longer moving with rates, and overstates once a swap has started).

See `wrap.py`'s module docstring for the long-end spline-vs-log-linear difference of definition this Kit inherits from the closed snapshot
vocabulary; it affects any tenor whose cashflows sit beyond the curve's densely-noded region, not the plumbing here.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any, Dict, Mapping, Optional, Sequence

import pandas as pd

from pricebt.contracts.spec import Built, Kit
from pricebt.contrib.rateslib import _compat as RLC
from pricebt.contrib.rateslib.conventions import SwapConv, swap_conventions
from pricebt.contrib.rateslib.ladder import DEFAULT_TENORS, clean_tenors, pillar_swap
from pricebt.contrib.rateslib.layers import Decomposition, decompose_swap
from pricebt.errors import ConfigError, MarketDataUnavailable
from pricebt.pricable import MarkContext, Valuation

from .risk_model import risk_model
from .wrap import ArbsCurvePricer

_DECOMP_KEY = "arbs_swap_decomposition"
_TENOR = re.compile(r"^\d+[YyMmWwDd]$")
_H_BP = 1.0  # the parallel par bump of `gamma`, bp per side (same convention as pricebt.contrib.rateslib.swap.RLSwap)


def _arbs(p: Any) -> ArbsCurvePricer:
    if not isinstance(p, ArbsCurvePricer):
        raise ConfigError(f"the market pricer is a {type(p).__name__}, not an ArbsCurvePricer: set `wrap` on the pricer role to arbs_adapter:wrap", code="CFG-WRAP")
    return p


class ArbsSwap:
    """One unit = one swap of `notional` (unsigned). `sign` +1 = payer (pricebt's direction, matching ARBS's own). `value` is the holder's P&L.

    Build it with `swap_factory` (the kit); direct construction takes resolved dates and a percent rate, exactly like `RLSwap`.
    """

    asset_class = "swap"

    def __init__(
        self, *, effective: dt.date, maturity: Any, sign: int, notional: float, fixed_rate: float, conv: SwapConv, cal: Any,
        fixings_name: str = RLC.DEFAULT_FIXINGS_NAME,
    ):
        if sign not in (1, -1):
            raise ConfigError(f"sign must be +1 (pay fixed) or -1 (receive fixed), got {sign!r}", code="SWAP")
        if not notional > 0:
            raise ConfigError("notional is unsigned and must be > 0 (direction is `side`)", code="SWAP")
        self.effective = RLC.to_date(effective)
        self.sign, self.notional, self.fixed_rate, self.conv, self.fixings_name = int(sign), float(notional), float(fixed_rate), conv, fixings_name
        self._maturity_tenor: Optional[str] = maturity.lower() if isinstance(maturity, str) else None
        self._maturity_date: Optional[dt.date] = None if isinstance(maturity, str) else RLC.to_date(maturity)
        term = self._maturity_tenor if self._maturity_tenor is not None else RLC.to_dt(self._maturity_date)
        kw = dict(fixed_rate=self.fixed_rate, notional=self.sign * self.notional)
        # the layer-facing instrument: a PLAIN rl.IRS, same effective/tenor/rate/notional as the ARBS instrument built lazily below
        self._inst = pillar_swap(conv, cal, RLC.to_dt(self.effective), term, **kw, leg2_rate_fixings=fixings_name)
        self._inst_fwd = pillar_swap(conv, cal, RLC.to_dt(self.effective), term, **kw)  # forward marks skip the per-call fixings lookup
        self.termination = RLC.to_date(self._inst.leg1.schedule.termination)
        self._started = False
        self._pay_dates = sorted({p.settlement_params.payment for leg in (self._inst.leg1, self._inst.leg2) for p in leg.periods})

    @property
    def notional_amount(self) -> float:
        return self.notional

    # ------------------------------------------------------------------ rateslib plumbing (identical to RLSwap; see its docstring)
    def has_payment_in(self, t0: dt.datetime, t1: dt.datetime) -> bool:
        return any(t0 <= p < t1 for p in self._pay_dates)

    @property
    def last_payment(self) -> dt.date:
        return RLC.to_date(self._pay_dates[-1])

    def matured(self, pricer: Any) -> bool:
        return self.last_payment < pricer.reference_date

    @property
    def _active(self) -> Any:
        return self._inst if self._started else self._inst_fwd

    def _prep(self, pricer: Any) -> None:
        ref = pricer.reference_date
        started = self.effective < ref
        self._started = started
        fx = getattr(pricer, "fixings", None)
        if started:
            if fx is None or len(fx) == 0:
                raise MarketDataUnavailable(pricer.ts, {}, f"swap started {self.effective} but the pricer carries no fixings")
            RLC.check_fixings(fx[fx.index >= pd.Timestamp(RLC.to_dt(self.effective))], self.effective, ref, pricer.calendar(self.conv.calendar))
            RLC.install_fixings(self.fixings_name, fx)
            self._inst.reset_fixings()

    def _npv(self, curve: Any, forward: Optional[dt.datetime] = None) -> float:
        return RLC.finite(self._active.npv(curves=curve) if forward is None else self._active.npv(curves=curve, forward=forward))

    def _npv_ad(self, curve: Any) -> Any:
        return self._active.npv(curves=curve)

    def _flows(self, curve: Any) -> pd.DataFrame:
        cf = self._active.cashflows(curves=curve)
        return cf[["Type", "Payment", "Cashflow", "DF", "NPV"]].reset_index(drop=True)

    # ------------------------------------------------------------------ ARBS's own instrument (curve_handle differs per pricer/date)
    def _arbs_swap(self, ctx: MarkContext, pricer: ArbsCurvePricer) -> Any:
        key = ("arbs_swap_irs", id(self), id(pricer))
        if key not in ctx.cache:
            kw: Dict[str, Any] = dict(effective_date=self.effective, fixed_rate=self.fixed_rate / 100.0, notional=self.sign * self.notional)
            if self._maturity_tenor is not None:
                kw["tenor"] = self._maturity_tenor
            else:
                kw["maturity_date"] = self._maturity_date
            ctx.cache[key] = pricer.curve_handle.build_irswap(fwd=None, **kw)
        return ctx.cache[key]

    # ------------------------------------------------------------------ value: PV through ARBS; cash-sweep through the bare rl.IRS (ARBS has no such primitive)
    def value(self, *, ctx: MarkContext) -> Valuation:
        p = _arbs(ctx.pricer)
        prev = ctx.prev_pricer
        if self.matured(p):
            cash = 0.0
            if prev is not None and prev.reference_date < p.reference_date and self.last_payment >= prev.reference_date:
                cash = self._cash(p, RLC.to_dt(prev.reference_date), RLC.to_dt(p.reference_date))
            return Valuation(ctx.ts, 0.0, cash, 0.0, {"reference_date": p.reference_date})
        self._prep(p)
        sw = self._arbs_swap(ctx, p)
        pv = RLC.finite(p.curve_handle.npv(sw))
        cash = 0.0
        if prev is not None and prev.reference_date < p.reference_date:
            cash = self._cash(p, RLC.to_dt(prev.reference_date), RLC.to_dt(p.reference_date))
        return Valuation(ctx.ts, pv, cash, 0.0, {"reference_date": p.reference_date})

    def _cash(self, p: ArbsCurvePricer, t0: dt.datetime, t1: dt.datetime) -> float:
        if not self.has_payment_in(t0, t1):
            return 0.0
        self._prep(p)
        w = self._flows(p.curve)
        w = w[(w["Payment"] >= t0) & (w["Payment"] < t1)]
        return RLC.finite(w["Cashflow"].sum())

    # ------------------------------------------------------------------ measures
    def rate(self, *, ctx: MarkContext) -> float:
        """Par rate of the remaining swap, ARBS's own `fair_rate`: a DECIMAL (the binding scales to percent). NaN once matured."""
        p = _arbs(ctx.pricer)
        if self.matured(p):
            return float("nan")
        self._prep(p)
        return RLC.finite(p.curve_handle.fair_rate(self._arbs_swap(ctx, p)))

    def dv01(self, *, ctx: MarkContext, tenors: Sequence[str] = DEFAULT_TENORS) -> float:
        p = _arbs(ctx.pricer)
        if self.matured(p):
            return 0.0
        self._prep(p)
        if self._started:  # ARBS's analytic pv01 keeps the accrued coupon: a started swap's dv01 is the ladder's sum (pricebt's own rule)
            return float(sum(self.delta_ladder(ctx=ctx, tenors=tenors).values()))
        return RLC.finite(p.curve_handle.pv01(self._arbs_swap(ctx, p)))

    def delta_ladder(self, *, ctx: MarkContext, tenors: Sequence[str] = DEFAULT_TENORS) -> Dict[str, float]:
        """ARBS's OWN risk model (`risk_model.py`), not pricebt's `ladder.py` -- the coordinator's correction (see `risk_model.py`'s module docstring)."""
        p = _arbs(ctx.pricer)
        t = clean_tenors(tenors)
        key = ("arbs_swap_ladder", id(self), t)
        if key in ctx.cache:
            return dict(ctx.cache[key])
        if self.matured(p):
            out = {b: 0.0 for b in t}
        else:
            self._prep(p)
            out = risk_model(p, t).delta(self._active)
        ctx.cache[key] = out
        return dict(out)

    def gamma(self, *, ctx: MarkContext, tenors: Sequence[str] = DEFAULT_TENORS) -> float:
        """ARBS's OWN risk model, same parallel +-1bp bump idea as pricebt.contrib.rateslib.swap.RLSwap.gamma, on ARBS-calibrated pillars."""
        p = _arbs(ctx.pricer)
        if self.matured(p):
            return 0.0
        t = clean_tenors(tenors)
        self._prep(p)
        up, dn, base = (risk_model(p, t, s) for s in (_H_BP, -_H_BP, 0.0))
        return (up.npv(self._active) + dn.npv(self._active) - 2.0 * base.npv(self._active)) / _H_BP**2

    def cashflows(self, *, ctx: MarkContext) -> pd.DataFrame:
        p = _arbs(ctx.pricer)
        self._prep(p)
        return self._flows(p.curve)

    # ------------------------------------------------------------------ layers (per unit, interval P&L over (prev, ts])
    def decomposition(self, ctx: MarkContext) -> Decomposition:
        if ctx.prev_pricer is None:
            raise ConfigError("layers need a previous pricer (ctx.prev_pricer)", code="LAYER")
        key = (_DECOMP_KEY, id(self))
        if key not in ctx.cache:
            ctx.cache[key] = decompose_swap(self, _arbs(ctx.prev_pricer), _arbs(ctx.pricer))
        return ctx.cache[key]

    def carry(self, *, ctx: MarkContext) -> float:
        return self.decomposition(ctx).folded()["carry"]

    def roll(self, *, ctx: MarkContext) -> float:
        return self.decomposition(ctx).folded()["roll"]

    def delta(self, *, ctx: MarkContext) -> float:
        return self.decomposition(ctx).folded()["delta"]

    def convexity(self, *, ctx: MarkContext) -> float:
        return self.decomposition(ctx).folded()["convexity"]

    # ------------------------------------------------------------------ EXTENSION measures: ARBS's own carry/roll/theta view, genuinely ARBS's
    # (Query.IRSwaps._carry_roll, backends.rateslib.rl_theta), exposed as-is -- NOT the required layers above, which pricebt's own engine needs
    # dollar, holder-signed, forwards-realised P&L (a contract these do not satisfy: `_carry_roll` is static-curve and receiver-signed by its own
    # docstring, `RLIRSwapCurve.dollar_carry` is `NotImplementedError` on this backend). Useful in the notebook as ARBS's own read of the same trade.
    def arbs_carry_bps_running(self, *, ctx: MarkContext, horizon: str = "1m") -> float:
        """ARBS's static-curve carry, bp of RATE, RECEIVER-signed (`Query.IRSwaps._carry_roll`'s own convention -- distinct from the holder sign
        used by every other measure in this Kit). Zero once matured."""
        p = _arbs(ctx.pricer)
        if self.matured(p):
            return 0.0
        self._prep(p)
        return RLC.finite(p.curve_handle.carry_bps_running(self._arbs_swap(ctx, p), horizon))

    def arbs_roll_bps_running(self, *, ctx: MarkContext, horizon: str = "1m") -> float:
        """ARBS's static-curve roll, bp of RATE, RECEIVER-signed. Zero once matured."""
        p = _arbs(ctx.pricer)
        if self.matured(p):
            return 0.0
        self._prep(p)
        return RLC.finite(p.curve_handle.roll_bps_running(self._arbs_swap(ctx, p), horizon))

    def arbs_carry_and_roll_bps_running(self, *, ctx: MarkContext, horizon: str = "1m") -> float:
        """`arbs_carry_bps_running` + `arbs_roll_bps_running`, exactly (ARBS computes it the same way)."""
        p = _arbs(ctx.pricer)
        if self.matured(p):
            return 0.0
        self._prep(p)
        return RLC.finite(p.curve_handle.carry_and_roll_bps_running(self._arbs_swap(ctx, p), horizon))

    def _theta(self, ctx: MarkContext, horizon: str) -> Dict[str, float]:
        p = _arbs(ctx.pricer)
        key = ("arbs_swap_theta", id(self), horizon, id(p))
        if key not in ctx.cache:
            self._prep(p)
            ctx.cache[key] = p.curve_handle.theta_components(self._arbs_swap(ctx, p), horizon)
        return ctx.cache[key]

    def arbs_theta_cashflows(self, *, ctx: MarkContext, horizon: str = "1b") -> float:
        """The cash leaving the PV over `horizon`, valued at the horizon date (ARBS's own `theta_components`; see its module docstring for the
        sign and units: currency, holder-signed via the instrument's own signed notional). Zero once matured."""
        return 0.0 if self.matured(_arbs(ctx.pricer)) else RLC.finite(self._theta(ctx, horizon)["cashflows"])

    def arbs_theta_forwarding(self, *, ctx: MarkContext, horizon: str = "1b") -> float:
        """The funding accretion of the mark over `horizon` (ARBS's own `theta_components`)."""
        return 0.0 if self.matured(_arbs(ctx.pricer)) else RLC.finite(self._theta(ctx, horizon)["forwarding"])

    def arbs_theta_rolldown(self, *, ctx: MarkContext, horizon: str = "1b") -> float:
        """The forwarded-curve-to-rolled-curve piece of PV decay over `horizon` (ARBS's own `theta_components`)."""
        return 0.0 if self.matured(_arbs(ctx.pricer)) else RLC.finite(self._theta(ctx, horizon)["rolldown"])

    def arbs_theta(self, *, ctx: MarkContext, horizon: str = "1b") -> float:
        """PV(start of day) - PV(end of day) over `horizon`: ARBS's own total theta (cashflows + forwarding + rolldown + option)."""
        return 0.0 if self.matured(_arbs(ctx.pricer)) else RLC.finite(self._theta(ctx, horizon)["theta"])


# ------------------------------------------------------------------------------ factory and kit
def _effective(spec: Any, pricer: ArbsCurvePricer, conv: SwapConv) -> dt.date:
    """`spot` | a forward tenor | a date -> the resolved effective date, via ARBS's OWN `spot_date` / `calendar_advance` (never rateslib's `add_tenor`
    directly -- `pricebt.contrib.rateslib.swap._effective`'s contract, replicated here on ARBS's own date arithmetic). A forward tenor is measured
    from SPOT, matching the schema's own doc ("a tenor forward from spot"): ARBS's `build_irswap(fwd=...)` measures from the REFERENCE date instead,
    so the tenor is applied to `spot_date()`, not passed as `fwd`. A literal date is NOT business-day-adjusted, mirroring ARBS's own
    `build_irswap(effective_date=...)`, which passes it straight through unmodified."""
    ch = pricer.curve_handle
    if isinstance(spec, str):
        s = spec.strip().lower()
        if s == "spot":
            return RLC.to_date(ch.spot_date())
        if _TENOR.match(s):
            return RLC.to_date(ch.calendar_advance(ch.spot_date(), s))
        raise ConfigError(f"effective {spec!r}: use 'spot', a forward tenor such as '1y' or a date", code="TERMS")
    return RLC.to_date(spec)


def swap_factory(pricer: Any, ts: Any, *, terms: Mapping[str, Any], conventions: Mapping[str, Any]) -> Built:
    """`(pricer, ts, *, terms, conventions) -> Built`: resolves the dates and `par` with ARBS's own `build_irswap`/`fair_rate` and returns what it
    resolved (spec T4)."""
    p = _arbs(pricer)
    conv = swap_conventions(conventions)
    ch = p.curve_handle
    extras = dict(terms.get("extras") or {})
    unknown = sorted(set(extras) - {"par_spread_bp"})
    if unknown:
        raise ConfigError(f"the ARBS adapter does not understand the swap extras {unknown}; it understands ['par_spread_bp']", code="CFG-EXTRAS")
    eff = _effective(terms.get("effective", "spot"), p, conv)
    maturity = terms["maturity"]
    tenor = maturity.lower() if isinstance(maturity, str) else None
    rate = terms.get("fixed_rate", "par")
    if isinstance(rate, str):
        if rate != "par":
            raise ConfigError(f"fixed_rate {rate!r}: a number (percent) or the token 'par'", code="TERMS")
        if p.reference_date > eff:
            raise ConfigError(f"par needs an effective date on or after the reference date ({eff} < {p.reference_date})", code="TERMS")
        probe_kw: Dict[str, Any] = dict(effective_date=eff, fixed_rate=-0, notional=1_000_000)
        probe_kw["tenor"] = tenor if tenor is not None else RLC.to_dt(maturity)
        probe = ch.build_irswap(fwd=None, **probe_kw)
        rate = RLC.finite(ch.fair_rate(probe)) * 100.0 + float(extras.get("par_spread_bp", 0.0)) / 100.0
    else:
        rate = float(rate)
        if rate == 0.0:  # ARBS's par sentinel is `fixed_rate == -0`, and `0.0 == -0` in Python: a literal 0% strike would be silently repriced at par
            raise ConfigError("a literal fixed_rate of 0.0 collides with ARBS's par sentinel (build_irswap treats fixed_rate == -0 as 'strike at par'); use 'par' or a nonzero rate", code="CFG-TERMS")
    swap = ArbsSwap(effective=eff, maturity=maturity, sign=int(terms["direction"]), notional=float(terms["notional"]), fixed_rate=float(rate), conv=conv, cal=p.calendar(conv.calendar))
    return Built(swap, {"effective": swap.effective, "maturity": swap.termination, "fixed_rate": float(rate), "notional": swap.notional})


def _bind(method: str, **extra: Any) -> Dict[str, Any]:
    return {"target": {"method": method}, "kwargs": {"ctx": "@ctx", **extra}}


ARBS_SWAP_BIND: Dict[str, Any] = {
    "value": _bind("value"),
    "dv01": _bind("dv01", tenors=list(DEFAULT_TENORS)),
    "gamma": _bind("gamma", tenors=list(DEFAULT_TENORS)),
    "rate": {**_bind("rate"), "scale": 100.0},  # ARBS's fair_rate is DECIMAL -> the schema's percent
    "delta_ladder": {**_bind("delta_ladder", tenors=list(DEFAULT_TENORS)), "keys": list(DEFAULT_TENORS), "reduce": "dict_of_floats"},
    "carry": _bind("carry"),
    "roll": _bind("roll"),
    "delta": _bind("delta"),
    "convexity": _bind("convexity"),
    # EXTENSION measures: ARBS's OWN carry/roll/theta view (see the methods' docstrings for units and signs) -- not the required layers above.
    "arbs.carry_bps_running": _bind("arbs_carry_bps_running", horizon="1m"),
    "arbs.roll_bps_running": _bind("arbs_roll_bps_running", horizon="1m"),
    "arbs.carry_and_roll_bps_running": _bind("arbs_carry_and_roll_bps_running", horizon="1m"),
    "arbs.theta_cashflows": _bind("arbs_theta_cashflows", horizon="1b"),
    "arbs.theta_forwarding": _bind("arbs_theta_forwarding", horizon="1b"),
    "arbs.theta_rolldown": _bind("arbs_theta_rolldown", horizon="1b"),
    "arbs.theta": _bind("arbs_theta", horizon="1b"),
}

swap = Kit(
    factory=swap_factory, asset_class="swap", default_bind=ARBS_SWAP_BIND, cls=ArbsSwap,
    extra={
        "arbs.carry_bps_running": "measure", "arbs.roll_bps_running": "measure", "arbs.carry_and_roll_bps_running": "measure",
        "arbs.theta_cashflows": "measure", "arbs.theta_forwarding": "measure", "arbs.theta_rolldown": "measure", "arbs.theta": "measure",
    },
    doc="USD SOFR-style OIS priced through ARBS's own RLIRSwapCurve value functions for value/rate/dv01 (module docstring: split of responsibilities, "
        "units and signs, and the long-end spline caveat); delta_ladder/gamma through ARBS's own risk model (risk_model.py); ARBS's own carry/roll/theta "
        "exposed as extension measures (arbs.*).",
)
