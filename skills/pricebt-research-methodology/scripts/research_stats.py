"""Small, dependency-light statistics for judging a backtest (used by the review and report skills).

Every function has a known-answer check in tests/skills/test_skill_research_stats.py, taken from
worked numbers in the reference books (see ../references/formulas.md for derivations and citations).
Inputs are plain floats / pandas Series of daily P&L in currency (no capital base needed).
"""
from __future__ import annotations

import math
from typing import Dict, Optional, Sequence

import numpy as np
import pandas as pd

__all__ = [
    "annualised_sharpe", "t_stat_from_sharpe", "se_sharpe", "years_needed", "prob_positive",
    "false_positive_prob", "bonferroni_threshold", "hit_rate_to_ic", "ic_to_hit_rate", "kelly_fraction",
    "levered_growth", "half_life", "max_drawdown", "implied_breadth", "sharpe_summary",
]


def annualised_sharpe(daily_pnl: pd.Series, periods_per_year: int = 252) -> float:
    """sqrt(N) * mean / std of per-period P&L (no risk-free rate: swap P&L is already excess over funding)."""
    x = pd.Series(daily_pnl, dtype=float).dropna()
    sd = x.std(ddof=1)
    if len(x) < 2 or not np.isfinite(sd) or sd == 0:
        return float("nan")
    return float(math.sqrt(periods_per_year) * x.mean() / sd)


def t_stat_from_sharpe(sharpe: float, years: float) -> float:
    """t of the mean P&L ~= annualised Sharpe (or IR) x sqrt(years of data)."""
    return float(sharpe * math.sqrt(years))


def se_sharpe(sharpe: float, years: float, periods_per_year: int = 252) -> float:
    """Standard error of an annualised Sharpe estimated from `years` of data, including the error in the
    volatility estimate: sqrt((1 + SR^2 * dt / 2) / years) with dt = 1/periods_per_year."""
    dt = 1.0 / periods_per_year
    return float(math.sqrt((1.0 + sharpe ** 2 * dt / 2.0) / years))


def years_needed(sharpe: float, t: float = 2.0) -> float:
    """Years of data for the mean to be significant at `t`: (t / SR)^2 (16 years for SR 0.5 at t=2)."""
    return float((t / sharpe) ** 2)


def prob_positive(sharpe: float, horizon_years: float) -> float:
    """Probability a strategy with true annualised Sharpe `sharpe` is up over `horizon_years` (normal P&L)."""
    return float(0.5 * (1.0 + math.erf(sharpe * math.sqrt(horizon_years) / math.sqrt(2.0))))


def false_positive_prob(n_trials: int, alpha: float = 0.05) -> float:
    """Chance at least one of n independent worthless variants looks significant at level alpha."""
    return float(1.0 - (1.0 - alpha) ** n_trials)


def bonferroni_threshold(n_trials: int, alpha: float = 0.05) -> float:
    """Two-sided z threshold that keeps the family-wise error at alpha across n trials (a floor, not a ceiling)."""
    from statistics import NormalDist
    return float(NormalDist().inv_cdf(1.0 - alpha / (2.0 * max(n_trials, 1))))


def ic_to_hit_rate(ic: float) -> float:
    """Directional hit rate implied by an information coefficient: (1 + IC) / 2."""
    return float((1.0 + ic) / 2.0)


def hit_rate_to_ic(p: float) -> float:
    return float(2.0 * p - 1.0)


def kelly_fraction(mean_excess: float, std: float) -> float:
    """Full-Kelly leverage f = m / s^2 for one strategy (use half of it in practice)."""
    return float(mean_excess / std ** 2)


def levered_growth(r_free: float, mean_excess: float, std: float, f: float) -> float:
    """Compounded growth at leverage f: r + f*m - f^2*s^2/2."""
    return float(r_free + f * mean_excess - 0.5 * f ** 2 * std ** 2)


def half_life(series: pd.Series) -> float:
    """Ornstein-Uhlenbeck half-life in observations from the regression dz_t = beta*(z_{t-1} - mean) + e.
    Returns NaN when beta >= 0 (no mean reversion: the half-life is undefined, not 'large')."""
    z = pd.Series(series, dtype=float).dropna()
    if len(z) < 3:
        return float("nan")
    lag = z.shift(1).iloc[1:]
    dz = z.diff().iloc[1:]
    x = lag - lag.mean()
    beta = float((x * (dz - dz.mean())).sum() / (x * x).sum())
    return float(-math.log(2.0) / beta) if beta < 0 else float("nan")


def max_drawdown(cum_pnl: pd.Series) -> Dict[str, object]:
    """Peak-to-trough fall of a cumulative P&L series in currency (additive P&L, no capital base),
    plus the peak, trough and the longest time under water (in index units)."""
    x = pd.Series(cum_pnl, dtype=float).dropna()
    if x.empty:
        return {"max_drawdown": float("nan"), "peak": None, "trough": None, "longest_underwater": 0}
    hwm = x.cummax()
    dd = x - hwm
    trough = dd.idxmin()
    peak = x.loc[:trough].idxmax()
    under = (dd < 0).astype(int)
    longest = run = 0
    for flag in under:
        run = run + 1 if flag else 0
        longest = max(longest, run)
    return {"max_drawdown": float(dd.min()), "peak": peak, "trough": trough, "longest_underwater": longest}


def implied_breadth(ir: float, ic: float) -> float:
    """Independent bets per year implied by the fundamental law IR = IC * sqrt(BR)."""
    return float((ir / ic) ** 2)


def sharpe_summary(daily_pnl: pd.Series, n_trials: int = 1, periods_per_year: int = 252) -> Dict[str, float]:
    """One dict with the numbers a reviewer needs to judge significance."""
    x = pd.Series(daily_pnl, dtype=float).dropna()
    years = len(x) / periods_per_year
    sr = annualised_sharpe(x, periods_per_year)
    t = t_stat_from_sharpe(sr, years) if np.isfinite(sr) else float("nan")
    return {
        "sharpe": sr,
        "years": years,
        "t_stat": t,
        "se_sharpe": se_sharpe(sr, years, periods_per_year) if years > 0 and np.isfinite(sr) else float("nan"),
        "years_needed_t2": years_needed(sr) if np.isfinite(sr) and sr > 0 else float("inf"),
        "n_trials": n_trials,
        "false_positive_prob": false_positive_prob(n_trials),
        "bonferroni_z": bonferroni_threshold(n_trials),
        "significant_after_trials": bool(np.isfinite(t) and abs(t) > bonferroni_threshold(n_trials)),
    }


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Significance summary for a daily P&L CSV (columns: date,pnl).")
    ap.add_argument("csv")
    ap.add_argument("--trials", type=int, default=1)
    a = ap.parse_args()
    s = pd.read_csv(a.csv, index_col=0).iloc[:, 0]
    print(json.dumps(sharpe_summary(s, a.trials), indent=2, default=str))
