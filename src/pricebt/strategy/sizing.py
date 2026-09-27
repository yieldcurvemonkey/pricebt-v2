"""Pure sizing maths (no engine): risk scaling, NAV pot solve, weighted split, hedge and rebalance quantities."""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

import numpy as np

from ..errors import SizingError


class ScalingActionType(Enum):
    risk_measure = "risk_measure"
    size = "size"
    NAV = "NAV"

    @classmethod
    def coerce(cls, x: Any) -> "ScalingActionType":
        if isinstance(x, cls):
            return x
        s = str(x)
        for m in cls:
            if s.lower() in (m.name.lower(), m.value.lower()):
                return m
        raise ValueError(f"unknown scaling_type {x!r}")


def risk_scale(level: float, unit_risk: float, *, eps: float = 1e-12) -> float:
    """level / unit_risk, signed."""
    if not math.isfinite(unit_risk) or abs(unit_risk) < eps:
        raise SizingError(f"cannot scale to a risk level: unit risk is {unit_risk}")
    return level / unit_risk


def nav_scale(available: float, unit_price: float, cost_at: Callable[[float], float], *, tol: float = 1e-12, max_iter: int = 200) -> float:
    """Root s of s*unit_price + cost_at(s) = available on [0, available/unit_price] by bisection.

    `cost_at(s)` is the total cost of an order of scale s and must include its quantity-independent part at s = 0. Returns 0 when the
    pot does not cover that fixed part."""
    if not (unit_price > 1e-12) or not math.isfinite(unit_price):
        raise SizingError(f"NAV sizing needs a positive unit price, got {unit_price}")
    if available <= cost_at(0.0):
        return 0.0
    lo, hi = 0.0, available / unit_price
    if hi * unit_price + cost_at(hi) <= available:
        return hi
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        if mid * unit_price + cost_at(mid) > available:
            hi = mid
        else:
            lo = mid
        if hi - lo <= tol * max(hi, 1.0):
            break
    return lo


def weighted_split(total: float, risks: Sequence[float], *, weighting: str = "risk_proportional") -> Tuple[float, ...]:
    """Leg quantities with sum == total. risk_proportional: |r_i|/sum|r|; inverse_risk: equal risk contribution; equal."""
    n = len(risks)
    if n == 0:
        return ()
    a = [abs(float(r)) for r in risks]
    if weighting == "equal":
        w = [1.0] * n
    elif weighting == "risk_proportional":
        if sum(a) == 0:
            raise SizingError("all leg risks are zero: cannot split risk-proportionally")
        w = a
    elif weighting == "inverse_risk":
        if any(x == 0 for x in a):
            raise SizingError("a leg has zero risk: cannot split inverse-risk")
        w = [1.0 / x for x in a]
    else:
        raise SizingError(f"unknown weighting {weighting!r}")
    s = sum(w)
    return tuple(total * x / s for x in w)


def hedge_quantity(net: float, unit_risk: float, *, target: float = 0.0, risk_percentage: float = 100.0, min_trade_risk: float = 0.0,
                   eps: float = 1e-12, rel_tol: float = 1e-9) -> Tuple[float, Optional[str]]:
    """(q, skip_reason): q = -(net - target)/unit_risk * pct/100 so that net + q*unit_risk = target at 100%."""
    if not math.isfinite(net) or not math.isfinite(unit_risk):
        raise SizingError(f"non-finite hedge inputs: net={net}, unit_risk={unit_risk}")
    if abs(unit_risk) < eps:
        raise SizingError("the hedge instrument has (numerically) zero unit risk in the hedged measure")
    q = -(net - target) / unit_risk * risk_percentage / 100.0
    if abs(q * unit_risk) <= max(min_trade_risk, rel_tol * max(abs(net), abs(target), 1.0)):
        return q, "zero_residual_risk"
    return q, None


def rebalance_delta(current: float, target: float, unit: float, *, eps: float = 1e-12) -> float:
    if not math.isfinite(unit) or abs(unit) < eps:
        raise SizingError(f"cannot rebalance: unit size/measure is {unit}")
    return (target - current) / unit


@dataclass(frozen=True)
class LadderHedge:
    """Result of `ladder_hedge_quantities`: quantities per hedge leg, the book ladder before/after (plain dicts), and a skip reason when the trade is not worth doing."""

    quantities: Tuple[float, ...]
    before: Dict[str, float]
    after: Dict[str, float]
    skip: Optional[str]


def l1(v: Mapping[str, float]) -> float:
    """Sum of absolute bucket values: the size of a ladder."""
    return float(sum(abs(x) for x in v.values()))


def ladder_hedge_quantities(net: Mapping[str, float], units: Sequence[Mapping[str, float]], *, target: Optional[Mapping[str, float]] = None, weights: Optional[Mapping[str, float]] = None,
                            ridge: float = 0.0, risk_percentage: float = 100.0, min_trade_risk: float = 0.0, eps: float = 1e-12, rel_tol: float = 1e-9) -> LadderHedge:
    """Weighted least-squares hedge of a bucketed risk vector: q = argmin || W (net - target + sum_j q_j U_j) ||^2 + ridge ||q||^2, scaled by risk_percentage/100.

    `net`, each `units[j]` (one per hedge leg, per UNIT of the leg as written) and `target` are plain dicts bucket -> value (a bucket missing from one is 0);
    `weights` scale buckets in the fit (default 1) and enter the loss SQUARED. With as many independent hedge legs as buckets the residual is 0; with fewer it is
    the least-squares residual. A target bucket that no leg and no book position touches cannot be fitted and is an error (typically a typo such as '10y').

    `min_trade_risk` (0 = off): a leg whose own trade risk (the sum over buckets of |its risk x its quantity|, after `risk_percentage`) is at most this is not traded, so a leg the
    target does not need does not become a dust trade; the whole hedge is skipped (`skip`) when its total trade risk is at most it."""
    if not units:
        raise SizingError("a ladder hedge needs at least one hedge leg")
    tgt = dict(target or {})
    touched = set(net) | {b for u in units for b in u}
    stray = sorted(b for b in tgt if b not in touched)
    if stray:
        raise SizingError(f"target buckets {stray} are touched by no hedge leg and no position (a typo? buckets in play: {sorted(touched)})")
    buckets = list(dict.fromkeys([*net, *(b for u in units for b in u), *tgt]))
    r0 = np.array([float(net.get(b, 0.0)) - float(tgt.get(b, 0.0)) for b in buckets])
    A = np.column_stack([np.array([float(u.get(b, 0.0)) for b in buckets]) for u in units])
    if not (np.isfinite(r0).all() and np.isfinite(A).all()):
        raise SizingError("non-finite ladder inputs")
    if np.abs(A).max() < eps:
        raise SizingError("the hedge instruments have (numerically) zero risk in every bucket of the hedged ladder")
    w = np.ones(len(buckets)) if not weights else np.array([float(weights.get(b, 1.0)) for b in buckets])
    Aw, rw = A * w[:, None], r0 * w
    if ridge > 0:
        q = np.linalg.solve(Aw.T @ Aw + ridge * np.eye(A.shape[1]), -Aw.T @ rw)
    else:
        q = np.linalg.lstsq(Aw, -rw, rcond=None)[0]
    q = q * risk_percentage / 100.0
    if min_trade_risk > 0:  # dust: a leg whose own trade risk is at most the minimum is not traded (its share stays in the residual)
        q = np.where(np.abs(A * q[None, :]).sum(axis=0) <= min_trade_risk, 0.0, q)
    before = dict(zip(buckets, (float(x) for x in r0)))
    after = dict(zip(buckets, (float(x) for x in (r0 + A @ q))))
    trade_risk = float(np.abs(A * q[None, :]).sum())
    skip = "zero_residual_risk" if trade_risk <= max(min_trade_risk, rel_tol * max(float(np.abs(r0).sum()), 1.0)) else None
    return LadderHedge(tuple(float(x) for x in q), before, after, skip)
