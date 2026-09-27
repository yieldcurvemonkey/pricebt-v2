"""Accounting identities of a run, checked from independently booked frames.

Every check computes an error array (0 when honest) and passes when ``max|error| <= atol + tol * scale`` with
``scale = max(1, |K|, max|equity|, max|cash|, max|positions_value|)``. NaN errors fail.

======================  =====================================================================================
``identity``            ``equity == K + cash + tcost + positions_value`` on every row (docs/DESIGN.md section 6)
``finite``              core equity columns finite, index tz-aware, strictly increasing
``step_pnl``            ``step_pnl == equity - equity.shift(1)`` with ``equity[-1] = K``
``cash_rollforward``    ``cash[t] == cumulative trade cash + flows_cum + financing_cum + interest_cum`` (ties the TRADES
                        frame and the per-position ledgers to the cash account)
``tcost_rollforward``   ``tcost[t] == -cumulative trades.tcost``
``positions_trades``    per position: ``sum(trades.quantity) == quantity`` (0 if closed), ``sum(trades.tcost) == tcost``,
                        ``sum(trades.cash) == trade_cash``
``pnl_sum``             ``sum(position pnl) + interest_cum[-1] == equity[-1] - K`` (positions' P&L sums to the equity change)
``positions_value``     ``positions_value[-1] == sum over open positions of (pnl + tcost - trade_cash - flows - financing)``
``n_positions``         ``n_positions[t] == #entry trades(ts <= t) - #positions with exit_ts <= t``
``layers``              per layered position ``sum(layer rows incl. unexplained) == gross P&L``; final ``equity.layer_*``
                        equals the per-position sums (holds when each layered position was flushed at its last mark)
``baseline_layers``     the same for the engine's Taylor rows (tay_delta + tay_convexity + tay_unexplained), which explain the same P&L on their own
``errors`` (warn)       no recorded engine errors
======================  =====================================================================================
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Tuple

import numpy as np
import pandas as pd

from ..types import BASELINE_LAYERS
from .errors import ReconcileError

CORE_COLUMNS = ("equity", "cash", "tcost", "positions_value", "n_positions", "step_pnl", "interest_cum", "financing_cum", "flows_cum")


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    max_abs_error: float
    n_violations: int
    first_violation: Any = None
    severity: str = "error"
    detail: str = ""


@dataclass(frozen=True)
class ReconcileReport:
    ok: bool
    checks: Tuple[Check, ...]
    tol: float
    atol: float
    scale: float

    @property
    def max_abs_error(self) -> float:
        errs = [c.max_abs_error for c in self.checks if c.severity == "error"]
        return float(np.nanmax(errs)) if errs else 0.0

    def __getitem__(self, name: str) -> Check:
        for c in self.checks:
            if c.name == name:
                return c
        raise KeyError(name)

    def failed(self) -> Tuple[Check, ...]:
        return tuple(c for c in self.checks if not c.passed and c.severity == "error")

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            [{"check": c.name, "passed": c.passed, "severity": c.severity, "max_abs_error": c.max_abs_error, "n_violations": c.n_violations,
              "first_violation": c.first_violation, "detail": c.detail} for c in self.checks]
        ).set_index("check")

    def raise_if_failed(self) -> None:
        bad = self.failed()
        if bad:
            names = ", ".join(f"{c.name} (max|err|={c.max_abs_error:.6g}, first={c.first_violation})" for c in bad)
            raise ReconcileError(f"reconcile failed: {names}", self)


def _cum_at(index: pd.DatetimeIndex, ts: pd.Series, values: np.ndarray) -> np.ndarray:
    """Cumulative sum of `values` over events with ``ts <= t`` evaluated at every `t` of `index`."""
    if len(values) == 0:
        return np.zeros(len(index))
    key = pd.DatetimeIndex(pd.to_datetime(ts, utc=True)).asi8
    order = np.argsort(key, kind="stable")
    csum = np.concatenate([[0.0], np.cumsum(np.asarray(values, dtype=float)[order])])
    return csum[np.searchsorted(key[order], index.asi8, side="right")]


def _check(name: str, err: np.ndarray, limit: float, index: Any = None, severity: str = "error", detail: str = "") -> Check:
    err = np.asarray(err, dtype=float)
    if err.size == 0:
        return Check(name, True, 0.0, 0, None, severity, detail)
    bad = ~(np.abs(err) <= limit)
    first = None
    if bad.any():
        k = int(np.flatnonzero(bad)[0])
        first = index[k] if index is not None else k
    return Check(name, not bad.any(), float(np.nanmax(np.abs(err))) if not np.all(np.isnan(err)) else float("nan"), int(bad.sum()), first, severity, detail)


def reconcile(result: Any, tol: float = 1e-10, atol: float = 1e-8, strict: bool = False) -> ReconcileReport:
    """Run every identity in the module docstring. With ``strict=True`` raise `ReconcileError` on the first failing report."""
    eq = result.equity
    rec = result.record
    K = float(result.initial_capital)
    idx = eq.index
    scale = max(1.0, abs(K), *(float(np.nanmax(np.abs(eq[c].to_numpy()))) for c in ("equity", "cash", "positions_value")))
    limit = atol + tol * scale
    checks = []

    missing = [c for c in CORE_COLUMNS if c not in eq.columns]
    finite = np.array([0.0 if np.isfinite(eq[c].to_numpy(dtype=float)).all() else 1.0 for c in CORE_COLUMNS if c in eq.columns])
    idx_ok = idx.tz is not None and idx.is_monotonic_increasing and idx.is_unique
    checks.append(Check("finite", not missing and bool(finite.sum() == 0) and idx_ok, float(finite.sum()), int(finite.sum()),
                        None, "error", f"missing={missing} index_ok={idx_ok}"))

    checks.append(_check("identity", eq["equity"] - (K + eq["cash"] + eq["tcost"] + eq["positions_value"]), limit, idx))
    prev = np.concatenate([[K], eq["equity"].to_numpy()[:-1]])
    checks.append(_check("step_pnl", eq["step_pnl"].to_numpy() - (eq["equity"].to_numpy() - prev), limit, idx))

    tr, pos = rec.trades, rec.positions
    if tr.empty:
        trade_cash = np.zeros(len(idx))
        trade_tcost = np.zeros(len(idx))
    else:
        trade_cash = _cum_at(idx, tr["ts"], tr["cash"].to_numpy())
        trade_tcost = _cum_at(idx, tr["ts"], tr["tcost"].to_numpy())
    checks.append(_check("cash_rollforward", eq["cash"].to_numpy() - (trade_cash + eq["flows_cum"].to_numpy() + eq["financing_cum"].to_numpy() + eq["interest_cum"].to_numpy()), limit, idx))
    checks.append(_check("tcost_rollforward", eq["tcost"].to_numpy() + trade_tcost, limit, idx))

    if pos.empty:
        checks.append(_check("positions_trades", np.array([]), limit))
        checks.append(_check("pnl_sum", np.array([eq["equity"].iloc[-1] - K - eq["interest_cum"].iloc[-1]]), limit))
        checks.append(_check("positions_value", np.array([eq["positions_value"].iloc[-1]]), limit))
        checks.append(_check("n_positions", eq["n_positions"].to_numpy(dtype=float), limit, idx))
    else:
        p = pos.set_index(pos["id"].astype(str))
        g = tr.groupby("position") if len(tr) else None
        qty = g["quantity"].sum().reindex(p.index).fillna(0.0) if g is not None else pd.Series(0.0, index=p.index)
        tc = g["tcost"].sum().reindex(p.index).fillna(0.0) if g is not None else pd.Series(0.0, index=p.index)
        cs = g["cash"].sum().reindex(p.index).fillna(0.0) if g is not None else pd.Series(0.0, index=p.index)
        err = np.concatenate([(qty - p["quantity"]).to_numpy(), (tc - p["tcost"]).to_numpy(), (cs - p["trade_cash"]).to_numpy()])
        checks.append(_check("positions_trades", err, limit))
        checks.append(_check("pnl_sum", np.array([p["pnl"].sum() + eq["interest_cum"].iloc[-1] - (eq["equity"].iloc[-1] - K)]), limit))
        opened = p["status"] == "open"
        value = (p["pnl"] + p["tcost"] - p["trade_cash"] - p["flow_cash"] - p["financing"])[opened].sum()
        checks.append(_check("positions_value", np.array([value - eq["positions_value"].iloc[-1]]), limit))
        entries = tr.loc[tr["kind"] == "open", "ts"] if len(tr) else pd.Series([], dtype=object)
        n_in = _cum_at(idx, entries, np.ones(len(entries)))
        exits = pd.to_datetime(p["exit_ts"].dropna(), utc=True).dt.tz_convert(idx.tz)
        n_out = _cum_at(idx, exits, np.ones(len(exits)))
        checks.append(_check("n_positions", eq["n_positions"].to_numpy(dtype=float) - (n_in - n_out), 0.5, idx))

    lay = rec.layers_by_position
    if lay.empty:
        checks.append(_check("layers", np.array([]), limit, detail="no layers recorded"))
    else:
        base = lay["layer"].isin(BASELINE_LAYERS)
        for name, part in (("layers", lay[~base]), ("baseline_layers", lay[base])):  # the engine's Taylor rows explain the same P&L as the library layers: each set adds up on its own
            if part.empty:
                continue
            by_pos = part.groupby("position")["pnl"].sum()
            by_pos.index = by_pos.index.astype(str)
            gross = (pos.set_index(pos["id"].astype(str))["pnl"] + pos.set_index(pos["id"].astype(str))["tcost"]).reindex(by_pos.index)
            by_layer = part.groupby("layer")["pnl"].sum()
            final = np.array([eq[f"layer_{k}"].iloc[-1] if f"layer_{k}" in eq.columns else np.nan for k in by_layer.index])
            checks.append(_check(name, np.concatenate([(by_pos - gross).to_numpy(), final - by_layer.to_numpy()]), limit))

    n_err = int(len(rec.errors))
    checks.append(Check("errors", n_err == 0, float(n_err), n_err, None, "warn", "recorded engine errors (on_error != raise)"))
    report = ReconcileReport(all(c.passed for c in checks if c.severity == "error"), tuple(checks), tol, atol, scale)
    if strict:
        report.raise_if_failed()
    return report
