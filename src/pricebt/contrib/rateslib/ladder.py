"""The par-swap risk curve behind the delta ladder, the dv01 of a started swap and the par-space gamma (spec S4).

One risk curve per snapshot, instrument conventions and pillar set: nodes at the terminations of spot-start pillar swaps of the given tenors, seeded from the
snapshot's dense curve and SOLVED so that each pillar swap reprices at the dense curve's par rate. A position is re-priced on this curve (never on the dense one)
and the ladder is `currency per +1bp of each pillar's par rate`. The models are memoised on the pricer, so the calibration (about 80 ms) is paid once per snapshot.

`shift_bp` re-solves the same pillars at par rates moved by that many bp (parallel), which is how `gamma` is obtained. The builder is a plain function of the pricer,
the conventions and the tenors: it is bound by the swap kit (`delta_ladder: {method: delta_ladder, kwargs: {tenors: [...]}, keys: [...]}`), never looked up by name.
"""
from __future__ import annotations

import contextlib
import io
from dataclasses import dataclass
from typing import Any, Dict, Sequence, Tuple

from ...errors import ConfigError
from . import _compat as C
from ._compat import rl
from .conventions import SwapConv

DEFAULT_TENORS: Tuple[str, ...] = ("3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y", "20Y", "30Y")
FUNC_TOL, CONV_TOL = 1e-14, 1e-15  # tight: a re-solve at rateslib's looser default is noisy enough to spoil a second difference (gamma)


@dataclass(frozen=True)
class LadderModel:
    """A risk curve + solver calibrated on ONE pricer snapshot. `delta(instrument)` -> {bucket: ccy per +1bp}, holder-signed; `npv` values on the risk curve."""

    buckets: Tuple[str, ...]
    curve: Any
    solver: Any
    reference_date: Any

    def delta(self, instrument: Any) -> Dict[str, float]:
        d = instrument.delta(curves=self.curve, solver=self.solver)
        block = d.xs("instruments", level=0) if "instruments" in set(d.index.get_level_values(0)) else d
        s = block.iloc[:, 0].astype(float)
        s.index = s.index.get_level_values(-1)
        return {b: float(s.get(b, 0.0)) + 0.0 for b in self.buckets}  # + 0.0 turns -0.0 into 0.0

    def npv(self, instrument: Any) -> float:
        return C.finite(instrument.npv(curves=self.curve))


def clean_tenors(tenors: Sequence[str]) -> Tuple[str, ...]:
    out = tuple(str(t).strip().upper() for t in tenors)
    if not out:
        raise ConfigError("a delta ladder needs at least one tenor", code="LADDER")
    if len(set(out)) != len(out):
        raise ConfigError(f"ladder tenors {list(out)} contain duplicates", code="LADDER")
    return out


def pillar_swap(conv: SwapConv, cal: Any, effective: Any, termination: Any, *, notional: float = 1e6, **kw: Any) -> Any:
    """A swap under the instrument's conventions on the snapshot's calendar: the pillar swaps of the risk curve and, with its own dates and size, the position itself
    (one construction, so a pillar and a position of the same tenor are the same object)."""
    return rl.IRS(effective=effective, termination=termination, spec="usd_irs", calendar=cal, notional=notional, **conv.irs_kwargs(), **kw)


def build_par_swap_ladder(pricer: Any, conv: SwapConv, tenors: Tuple[str, ...], shift_bp: float = 0.0) -> LadderModel:
    """Calibrate the risk curve: nodes at the pillar terminations seeded from the dense curve, solved so each pillar swap reprices at the dense par rate + `shift_bp`."""
    cal = pricer.calendar(conv.calendar)
    eff = cal.add_bus_days(C.to_dt(pricer.reference_date), conv.spot_lag_days, True)
    dense = pricer.curve
    rid = f"{dense.id}-RISK"
    nodes = {C.to_dt(pricer.reference_date): 1.0}
    insts, pars = [], []
    for t in tenors:
        term = pillar_swap(conv, cal, eff, t.lower()).leg1.schedule.termination
        probe = pillar_swap(conv, cal, eff, term)
        pars.append(C.finite(probe.rate(curves=dense)))
        nodes[term] = float(dense[term])
        insts.append(pillar_swap(conv, cal, eff, term, fixed_rate=pars[-1], curves=rid))
    if len(nodes) != len(tenors) + 1:
        raise ConfigError(f"risk tenors {list(tenors)} map to duplicate maturities; use distinct, increasing tenors", code="LADDER")
    if list(nodes) != sorted(nodes):
        raise ConfigError(f"risk tenors {list(tenors)} must be in increasing order of maturity (a risk curve has ascending pillars)", code="LADDER")
    curve = rl.Curve(nodes=nodes, convention=dense.meta.convention, calendar=dense.meta.calendar, modifier=dense.meta.modifier, interpolation="log_linear", id=rid)
    with contextlib.redirect_stdout(io.StringIO()):
        solver = rl.Solver(curves=[curve], instruments=insts, s=[p + shift_bp / 100.0 for p in pars], instrument_labels=list(tenors), id=rid, func_tol=FUNC_TOL, conv_tol=CONV_TOL)
    if solver.result["status"] != "SUCCESS":
        raise ConfigError(f"risk-curve calibration failed: {solver.result}", code="LADDER")
    return LadderModel(tenors, curve, solver, pricer.reference_date)


def ladder_model(pricer: Any, conv: SwapConv, tenors: Sequence[str], shift_bp: float = 0.0) -> LadderModel:
    """The calibrated model for this snapshot, conventions and tenor set (memoised on the pricer)."""
    t = clean_tenors(tenors)
    return pricer.memo(("ladder", conv, t, float(shift_bp)), lambda: build_par_swap_ladder(pricer, conv, t, shift_bp))
