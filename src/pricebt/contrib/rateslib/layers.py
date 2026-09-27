"""Scenario-curve P&L attribution and zero-rate-space AD risk (productionised from a research probe).

Per scenario k: X_k = PV at t1 of flows paid >= t1 under the scenario curve + face cash paid in [t0, t1).
    carry     = X(c0.translate(t1)) - V0                forwards realised in date space
    roll      = X(c0.roll(N).translate(t1)) - X_carry   curve static in tenor space, N = calendar days (never roll('1b'))
    fixings   = X_roll(actual t1 fixings) - X_roll(t0 world)
    resample  = roll curve re-expressed on c1's node dates
    delta     = sum_j dV/dz_j dz_j                       zero-rate space at c1's node dates (z = -ln DF / tau)
    convexity = 1/2 dz' Gamma dz
    residual  = V1 - V_base - delta - convexity
Canonical fold (docs/design/adr/005-attribution.md): carry, roll+fixings, delta+resample, convexity; the engine's `unexplained` receives `residual`.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Tuple

import numpy as np

from . import _compat as C
from ._compat import rl

TAU_DAYS = 365.0


@dataclass(frozen=True)
class ZeroRisk:
    """Gradient/Hessian of a PV with respect to continuously-compounded zero rates at the curve's nodes (node 0 excluded)."""

    dates: Tuple[dt.datetime, ...]
    tau: np.ndarray
    df: np.ndarray
    grad: np.ndarray  # dV/dz_j  (per 1.00 = 100%)
    hess: np.ndarray  # d2V/dz_i dz_j

    @property
    def dv01(self) -> float:
        """Parallel +1bp zero-rate shift, currency."""
        return float(self.grad.sum()) * 1e-4

    @property
    def convexity(self) -> float:
        """d2 PV / dbp^2 of the parallel zero-rate shift."""
        return float(np.ones(len(self.grad)) @ self.hess @ np.ones(len(self.grad))) * 1e-8


def zero_risk(npv_fn: Callable[[Any], Any], template: Any, nodes: Mapping[dt.datetime, float], t_ref: dt.datetime, cid: str = "pbad") -> ZeroRisk:
    """`npv_fn(curve)` evaluated on an ad=2 curve with `template`'s conventions and DF `nodes`; chain rule DF -> zero rate."""
    dates = list(nodes.keys())
    cad = C.ad_curve(template, nodes, cid, 2)
    v2 = npv_fn(cad)
    n = len(dates) - 1
    tau = np.array([(d - t_ref).days / TAU_DAYS for d in dates[1:]])
    df = np.array([float(nodes[d]) for d in dates[1:]])
    g, H = np.zeros(n), np.zeros((n, n))
    if hasattr(v2, "vars"):
        vars_ = list(v2.vars)
        idx = [int(v[len(cid):]) - 1 for v in vars_]
        keep = [k for k, i in enumerate(idx) if i >= 0]
        if keep:
            vk = [vars_[k] for k in keep]
            ik = np.array([idx[k] for k in keep])
            gk = np.array(rl.gradient(v2, vk, order=1))
            Hk = np.array(rl.gradient(v2, vk, order=2))
            g[ik] = gk
            H[np.ix_(ik, ik)] = Hk
    J = -tau * df
    return ZeroRisk(tuple(dates[1:]), tau, df, g * J, H * np.outer(J, J) + np.diag(g * tau**2 * df))


@dataclass(frozen=True)
class Decomposition:
    """Raw scenario chain over [t0, t1] for one swap (holder-signed currency)."""

    raw: Mapping[str, float]
    V0: float
    V1: float
    cash: float
    total: float
    zero_risk: ZeroRisk

    def folded(self) -> Dict[str, float]:
        r = self.raw
        return {
            "carry": r["carry"], "roll": r["roll"] + r["fixings"], "delta": r["delta"] + r["resample"], "convexity": r["convexity"],
            "residual": r["residual"],
        }


def shifted_roll_curve(c0: Any, t0: dt.datetime, t1: dt.datetime, cid: str = "pbroll") -> Any:
    """c0.roll(N).translate(t1) as a node table (10x faster than the wrapper chain): nodes {t1: 1} + {d + N: DF0(d)}."""
    n = (t1 - t0).days
    nodes = {t1: 1.0}
    for d, v in c0.nodes.nodes.items():
        if d > t0:
            nodes[d + dt.timedelta(days=n)] = float(v)
    return rl.Curve(nodes=nodes, id=cid, convention=c0.meta.convention, calendar=c0.meta.calendar, modifier=c0.meta.modifier, interpolation=c0.interpolator.local_name)


def decompose_swap(swap: Any, p0: Any, p1: Any) -> Decomposition:
    """Full attribution of `swap` between the market snapshots p0 (earlier) and p1. `swap` supplies `_npv(curve, forward=None)`, `_npv_ad(curve)`,
    `_prep(pricer)` (installs that pricer's fixings) and `_flows(curve)` (DataFrame Payment/Cashflow)."""
    c0, c1 = p0.curve, p1.curve
    t0, t1 = C.to_dt(p0.reference_date), C.to_dt(p1.reference_date)
    if t1 < t0:
        raise ValueError(f"decompose_swap needs t1 >= t0, got {t0.date()} -> {t1.date()}")
    N = (t1 - t0).days

    def paid_window(tab: Any) -> Any:
        return tab[(tab["Payment"] >= t0) & (tab["Payment"] < t1)]

    def x_value(curve: Any) -> float:
        """PV at t1 of scenario `curve` (initial node t0, t0 world) with in-window flows replaced by face cash."""
        v = swap._npv(curve, forward=t1)
        if not swap.has_payment_in(t0, t1):
            return v
        w = paid_window(swap._flows(curve))
        d1 = float(curve[t1])
        return v - float(sum(cf * (float(curve[p]) / d1 - 1.0) for cf, p in zip(w["Cashflow"], w["Payment"])))

    swap._prep(p0)
    V0 = swap._npv(c0)
    X_fwd = x_value(c0)
    c_roll0 = c0.roll(N) if N > 0 else c0
    X_roll = x_value(c_roll0)

    swap._prep(p1)
    V1 = swap._npv(c1)
    cash = float(paid_window(swap._flows(c1))["Cashflow"].sum()) if swap.has_payment_in(t0, t1) else 0.0
    c_roll1 = shifted_roll_curve(c0, t0, t1)
    X_roll_act = swap._npv(c_roll1) + cash

    base_nodes = {d: float(c_roll1[d]) for d in C.curve_dates(c1)}
    c_base = C.float_curve(base_nodes, "pbbase", c1.interpolator.local_name)
    V_base = swap._npv(c_base)
    zr = zero_risk(swap._npv_ad, c1, base_nodes, t1)
    z1 = -np.log(np.array([float(c1.nodes.nodes[d]) for d in zr.dates])) / zr.tau
    zb = -np.log(zr.df) / zr.tau
    dz = z1 - zb
    delta = float(zr.grad @ dz)
    convexity = 0.5 * float(dz @ zr.hess @ dz)
    raw = {
        "carry": X_fwd - V0,
        "roll": X_roll - X_fwd,
        "fixings": X_roll_act - X_roll,
        "resample": V_base + cash - X_roll_act,
        "delta": delta,
        "convexity": convexity,
        "residual": (V1 - V_base) - delta - convexity,
    }
    return Decomposition(raw, V0, V1, cash, V1 + cash - V0, zr)
