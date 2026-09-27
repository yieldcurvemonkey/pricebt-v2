"""ARBS's OWN par-swap risk model for `delta_ladder`, `gamma` and a started swap's `dv01` (the ladder's sum): mirrors the "Risk Model" section of
ARBS's own notebook (`ARBS/notebooks/pricers/irswap_pricer_and_risk_model.ipynb`) -- pillar par rates read off ARBS's `build_irswap` / `fair_rate`
(not pricebt's `pillar_swap` / `.rate()`), and the calibrated risk curve wrapped in ARBS's own `RLIRSwapCurve` before rateslib's generic AD
`delta` / `npv` takes over. Deliberately NOT `pricebt.contrib.rateslib.ladder`: the REQUIRED schema layers (`carry`/`roll`/`delta`/`convexity`) still
go through pricebt's own `layers.py` (a distinct, separately-verified mechanism; see `swap.py`'s module docstring for why that part is unaffected).

Pillar set: pricebt's own `DEFAULT_TENORS` (2Y-30Y calendar tenors), not the notebook's FOMC/IMM-dated set -- deliberately: this Kit prices plain
calendar-tenor swaps only, so a plain calendar pillar set is enough and simpler.

The generic `Query.IRSwaps.IRSwapQuery` / `.resolve_package` dispatch is NOT used here: for a plain calendar tenor, its OUTRIGHT structure resolves
through the SAME curve method this module calls directly (`_IRSwapGenericCurve.build_pricable` is exactly that hook onto `build_irswap`) -- going
through the Query layer would add tenor-token parsing (FOMC / IMM / invoice-swap structures this Kit never builds) without adding fidelity.
`fair_rate` / `build_irswap` here are still genuinely ARBS's own value functions, called exactly as `swap.py` calls them elsewhere.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Tuple

from pricebt.contrib.rateslib import _compat as RLC
from pricebt.contrib.rateslib._compat import rl
from pricebt.contrib.rateslib.ladder import clean_tenors
from pricebt.errors import ConfigError

from . import _compat as C

FUNC_TOL, CONV_TOL = 1e-8, 1e-10  # ARBS's own notebook calibration tolerances (looser than pricebt's ladder.py's 1e-14/1e-15: ARBS's own choice, kept as is)


@dataclass(frozen=True)
class ArbsRiskModel:
    """A par-swap risk curve calibrated through ARBS's own `build_irswap` / `fair_rate`, wrapped in ARBS's `RLIRSwapCurve`. `delta(instrument)` ->
    {bucket: ccy per +1bp}, holder-signed (the instrument's own signed notional carries the sign); `npv` values a plain `rl.IRS` on the risk curve."""

    buckets: Tuple[str, ...]
    curve: Any
    handle: Any
    solver: Any
    reference_date: Any

    def delta(self, instrument: Any) -> Dict[str, float]:
        d = instrument.delta(curves=self.curve, solver=self.solver)
        block = d.xs("instruments", level=0) if "instruments" in set(d.index.get_level_values(0)) else d
        s = block.iloc[:, 0].astype(float)
        s.index = s.index.get_level_values(-1)
        return {b: float(s.get(b, 0.0)) + 0.0 for b in self.buckets}  # + 0.0 turns -0.0 into 0.0

    def npv(self, instrument: Any) -> float:
        return RLC.finite(instrument.npv(curves=self.curve))


def _build(pricer: Any, tenors: Tuple[str, ...], shift_bp: float) -> ArbsRiskModel:
    ch = pricer.curve_handle
    dense = pricer.curve
    ref_dt = RLC.to_dt(pricer.reference_date)
    spot = ch.spot_date()
    nodes: Dict[Any, float] = {ref_dt: 1.0}
    pillars = []
    for t in tenors:
        probe = ch.build_irswap(fwd=None, effective_date=spot, tenor=t.lower(), fixed_rate=-0, notional=1_000_000)  # ARBS's own par sentinel
        par = RLC.finite(ch.fair_rate(probe))  # ARBS's own fair_rate: DECIMAL
        mat = probe.leg1.schedule.termination
        nodes[mat] = float(dense[mat])  # seeded from the dense curve, like pricebt.contrib.rateslib.ladder.build_par_swap_ladder
        pillars.append((t, mat, par))
    if len(nodes) != len(tenors) + 1:
        raise ConfigError(f"risk tenors {list(tenors)} map to duplicate maturities; use distinct, increasing tenors", code="LADDER")
    if list(nodes) != sorted(nodes):
        raise ConfigError(f"risk tenors {list(tenors)} must be in increasing order of maturity (a risk curve has ascending pillars)", code="LADDER")
    risk_curve = rl.Curve(
        nodes=nodes, convention=dense.meta.convention, calendar=dense.meta.calendar, modifier=dense.meta.modifier, interpolation="log_linear",
        id=f"{dense.id}-ARBS-RISK",
    )
    RLIRSwapCurve = C.rl_irswap_curve()
    risk_meta = dict(ch.meta())
    risk_meta["id"] = risk_curve.id
    risk_handle = RLIRSwapCurve(rl_curve_id=risk_curve.id, rl_curve_handle=risk_curve, fixings=ch.index(), meta_data=risk_meta)
    insts, s_vals = [], []
    for t, _mat, par in pillars:
        decimal_rate = par + shift_bp * 1e-4  # +1bp = +1e-4 decimal
        insts.append(risk_handle.build_irswap(fwd=None, effective_date=spot, tenor=t.lower(), fixed_rate=decimal_rate, notional=1_000_000))
        s_vals.append(decimal_rate * 100.0)  # rl.Solver's `s=` is PERCENT
    solver = RLC.solve_quiet(curves=[risk_curve], instruments=insts, s=s_vals, instrument_labels=list(tenors), id=risk_curve.id, func_tol=FUNC_TOL, conv_tol=CONV_TOL)
    if solver.result["status"] != "SUCCESS":
        raise ConfigError(f"ARBS risk-curve calibration failed: {solver.result}", code="LADDER")
    return ArbsRiskModel(tenors, risk_curve, risk_handle, solver, pricer.reference_date)


def risk_model(pricer: Any, tenors: Any, shift_bp: float = 0.0) -> ArbsRiskModel:
    """The calibrated model for this snapshot and tenor set (memoised on the pricer, like `pricebt.contrib.rateslib.ladder.ladder_model`)."""
    t = clean_tenors(tenors)
    return pricer.memo(("arbs_risk_model", t, float(shift_bp)), lambda: _build(pricer, t, shift_bp))
