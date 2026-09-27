"""Multi-strategy comparison: statistics table, session-P&L correlation matrix and a combined equity curve."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from .errors import ResultError
from .stats import StatsConfig

DEFAULT_COMPARISON_STATS = (
    "total_pnl", "ann_return", "ann_vol", "sharpe", "sortino", "max_drawdown", "calmar", "hit_rate", "profit_factor",
    "n_trades", "trade_hit_rate", "total_tcost", "n_periods",
)


@dataclass(frozen=True)
class Comparison:
    """`table`: one row per strategy; `pnl`: session P&L matrix; `correlation`: its pairwise correlation;
    `equity`: each strategy's equity on the union of timestamps (forward-filled inside its own window, NaN outside)."""

    table: pd.DataFrame
    pnl: pd.DataFrame
    correlation: pd.DataFrame
    equity: pd.DataFrame
    combined: pd.DataFrame


def _check(results: Mapping[str, Any]) -> None:
    if not results:
        raise ResultError("compare needs at least one result")


def comparison_table(results: Mapping[str, Any], stats: Sequence[str] = DEFAULT_COMPARISON_STATS, config: Optional[StatsConfig] = None) -> pd.DataFrame:
    """Rows = strategy names, columns = `stats` (snake names of `BacktestResult.summary_stats`)."""
    _check(results)
    rows = {n: r.summary_stats(config) for n, r in results.items()}
    unknown = [s for s in stats if s not in next(iter(rows.values())).index]
    if unknown:
        raise ResultError(f"unknown statistics {unknown}")
    return pd.DataFrame({n: s[list(stats)] for n, s in rows.items()}).T.apply(pd.to_numeric, errors="coerce")


def pnl_matrix(results: Mapping[str, Any]) -> pd.DataFrame:
    """Net P&L per local calendar date per strategy on the union of dates.

    NaN outside a strategy's own [first date, last date] window; 0.0 for a date inside the window on which it had no
    timeline point (no data is not a loss). Minute and daily strategies align because both reduce to dates first."""
    _check(results)
    series = {}
    for name, r in results.items():
        days = pd.Index(r.equity.index.tz_localize(None).normalize())
        series[name] = r.step_pnl.groupby(days).sum()
    union = sorted(set().union(*[s.index for s in series.values()]))
    out = pd.DataFrame(index=pd.DatetimeIndex(union, name="day"), columns=list(series), dtype=float)
    for name, s in series.items():
        inside = (out.index >= s.index.min()) & (out.index <= s.index.max())
        out[name] = np.where(inside, s.reindex(out.index).fillna(0.0), np.nan)
    return out


def correlation(results: Mapping[str, Any], method: str = "pearson", min_periods: int = 3) -> pd.DataFrame:
    """Pairwise-complete correlation of `pnl_matrix`; NaN where fewer than `min_periods` dates overlap."""
    return pnl_matrix(results).corr(method=method, min_periods=min_periods)


def combined_equity(results: Mapping[str, Any], weights: Optional[Mapping[str, float]] = None) -> pd.DataFrame:
    """``equity = sum_i w_i K_i + cumsum(sum_i w_i pnl_i,t)`` on the union of dates (NaN P&L counts as 0); default ``w = 1``.

    Columns ``equity``, ``step_pnl``, ``n_active``. Weights are user-specified only (ex-post weights would be look-ahead)."""
    _check(results)
    w = {n: 1.0 for n in results}
    if weights:
        bad = [n for n in weights if n not in results]
        if bad:
            raise ResultError(f"weights name unknown strategies {bad}; valid: {list(results)}")
        w.update(weights)
    m = pnl_matrix(results)
    step = sum(w[n] * m[n].fillna(0.0) for n in results)
    capital = sum(w[n] * results[n].initial_capital for n in results)
    return pd.DataFrame({"equity": capital + step.cumsum(), "step_pnl": step, "n_active": m.notna().sum(axis=1)})


def equity_matrix(results: Mapping[str, Any]) -> pd.DataFrame:
    _check(results)
    frame = pd.DataFrame({n: r.equity_curve for n, r in results.items()})
    for n, r in results.items():
        inside = (frame.index >= r.equity.index[0]) & (frame.index <= r.equity.index[-1])
        frame.loc[inside, n] = frame.loc[inside, n].ffill()
    return frame


def compare(results: Mapping[str, Any], *, stats: Sequence[str] = DEFAULT_COMPARISON_STATS, config: Optional[StatsConfig] = None,
            weights: Optional[Mapping[str, float]] = None) -> Comparison:
    """Comparison table, P&L correlation matrix, per-strategy and combined equity for a ``{name: BacktestResult}`` dict."""
    return Comparison(comparison_table(results, stats, config), pnl_matrix(results), correlation(results), equity_matrix(results), combined_equity(results, weights))
