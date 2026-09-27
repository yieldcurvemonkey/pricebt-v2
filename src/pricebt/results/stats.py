"""Performance statistics from an equity path.

Conventions (all statistics are computed on ONE observation path):

* Observation path: levels ``[K, L_1, ..., L_n]`` where ``K`` is the initial capital and ``L_i`` the equity at the LAST
  row of period ``i``. ``freq="session"`` (default) makes a period one local calendar date of the backtest timezone
  (so intraday grids are aggregated and overnight gaps land in the session that contains them); ``freq="bar"`` makes
  every timeline point a period. The base ``K`` is included, so a first-point cost is a real return.
* Return basis: ``nav`` (``initial_capital > 0``): ``r_i = L_i / L_{i-1} - 1``; ``pnl`` (``initial_capital == 0``):
  ``r_i = L_i - L_{i-1}`` in currency. ``basis="auto"`` picks by ``K``.
* ``mu = mean(r)``, ``sigma = std(r, ddof=1)``, ``A`` = periods per year (see `annualisation`).
* ``ann_return = mu * A``; ``ann_vol = sigma * sqrt(A)``; ``sharpe = (mu - rf/A) / sigma * sqrt(A)``;
  ``sortino = (mu - mar/A) / DD * sqrt(A)`` with ``DD = sqrt(mean(min(r - mar/A, 0)^2))`` over ALL periods.
* Drawdown of the path with running peak ``M_i = max_{j<=i} L_j``: ``nav``: ``L_i / M_i - 1``; ``pnl``: ``L_i - M_i``.
  ``max_drawdown`` is its minimum (<= 0). ``calmar = (cagr if nav else ann_return) / |max_drawdown|``.
* ``var``/``cvar``: historical, ``q = np.quantile(r, 1 - level)`` (linear interpolation), ``var = -q``,
  ``cvar = -mean(r[r <= q])``; positive numbers are losses; one-period horizon.
* ``profit_factor = sum(gains) / |sum(losses)|`` over period returns; ``inf`` if no losses and some gains, NaN if none.
* A series is DEGENERATE when ``sigma <= 1e-9 * max(|mu|, max|r|)``; ``sharpe``, ``sortino`` and ``calmar`` are NaN then
  (never ``inf`` and never the ~1e16 that ``sigma == 0`` tests produce from rounding noise).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
import pandas as pd

from ..timeutil import SECONDS_PER_YEAR
from .errors import StatsError

FREQS = ("session", "bar")
BASES = ("auto", "nav", "pnl")
_DEGENERATE = 1e-9

GS_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "total_pnl": "Total PnL",
        "total_tcost": "Total Transaction Costs",
        "n_trades": "Total Trades",
        "peak_pnl": "Peak PnL",
        "ann_return": "Annualised Return",
        "ann_vol": "Annualised Volatility",
        "sharpe": "Sharpe Ratio",
        "sortino": "Sortino Ratio",
        "max_drawdown": "Max Drawdown",
        "max_dd_duration_days": "Max Drawdown Duration (days)",
        "calmar": "Calmar Ratio",
        "current_drawdown": "Current Drawdown",
        "mean": "Average Daily PnL",
        "std": "Daily PnL Std Dev",
        "best": "Best Day",
        "worst": "Worst Day",
        "pct_positive": "% Positive Days",
        "skew": "Skewness",
        "kurt": "Kurtosis",
        "start": "Start Date",
        "end": "End Date",
    }
)


@dataclass(frozen=True)
class StatsConfig:
    """`annualisation=None` derives A from the grid; a float overrides it (gs `annualisation_factor`)."""

    freq: str = "session"
    basis: str = "auto"
    annualisation: Optional[float] = None
    rf: float = 0.0
    mar: float = 0.0
    var_level: float = 0.95
    rolling_window: int = 63

    def __post_init__(self) -> None:
        if self.freq not in FREQS:
            raise StatsError(f"freq must be one of {FREQS}, got {self.freq!r}")
        if self.basis not in BASES:
            raise StatsError(f"basis must be one of {BASES}, got {self.basis!r}")
        if self.annualisation is not None and not (self.annualisation > 0):
            raise StatsError(f"annualisation must be > 0, got {self.annualisation!r}")
        if not 0.0 < self.var_level < 1.0:
            raise StatsError(f"var_level must be in (0, 1), got {self.var_level!r}")
        if self.rolling_window < 2:
            raise StatsError("rolling_window must be >= 2")

    def resolve_basis(self, initial_capital: float) -> str:
        basis = self.basis if self.basis != "auto" else ("nav" if initial_capital > 0 else "pnl")
        if basis == "nav" and initial_capital <= 0:
            raise StatsError("basis='nav' needs initial_capital > 0")
        if basis == "pnl" and self.rf != 0.0:
            raise StatsError("rf != 0 is undefined on the currency-P&L basis; use initial_capital > 0 (nav)")
        return basis


@dataclass(frozen=True)
class Observations:
    """`ts[i]` is the end timestamp of period i+1; `levels` has one more entry (the base ``K``) than `ts`."""

    ts: pd.DatetimeIndex
    levels: np.ndarray
    freq: str


def observations(equity: pd.Series, initial_capital: float, freq: str = "session") -> Observations:
    """Equity levels per period with the initial capital as base."""
    if freq not in FREQS:
        raise StatsError(f"freq must be one of {FREQS}, got {freq!r}")
    if len(equity) == 0:
        raise StatsError("empty equity path")
    idx = pd.DatetimeIndex(equity.index)
    vals = equity.to_numpy(dtype=float)
    if freq == "session":
        keys = np.asarray(idx.date)
        last = np.append(keys[1:] != keys[:-1], True)
        idx, vals = idx[last], vals[last]
    return Observations(idx, np.concatenate([[float(initial_capital)], vals]), freq)


def _local_days(ts: pd.DatetimeIndex) -> np.ndarray:
    naive = ts.tz_localize(None) if ts.tz is not None else ts
    return np.unique(naive.values.astype("datetime64[D]"))


def annualisation(ts: pd.DatetimeIndex, freq: str = "session", override: Optional[float] = None) -> Tuple[float, str]:
    """Periods per year derived from the observation timestamps; returns ``(A, source)``.

    ``spy`` (days per year) is ``365.25 * 5/7`` for a weekday grid and ``365.25`` when more than 15% of the distinct
    dates are weekend days. ``freq="session"``: ``A = spy * (m - 1) / busday_count(first, last)`` with ``m`` distinct
    dates, i.e. ``spy`` over the mean business days per period (daily 260.9, weekly 52.2, monthly ~12).
    ``freq="bar"``: ``A = spy * N / (busday_count(first, last) + 1)`` = ``spy`` times the mean points per business day
    (1-minute bars of a 541-point session: ``541 * spy``). No hard-coded 252, no median-step rule (overnight gaps make
    ``1 year / median step`` wrong for intraday grids). Holidays are not known here, so a grid that skips them
    annualises slightly below ``spy`` (about 251 for US equities); pass ``annualisation=`` to override.
    """
    if override is not None:
        return float(override), "override"
    days = _local_days(ts)
    dnum = days.astype("int64")
    weekend_share = float(np.mean(((dnum + 3) % 7) >= 5)) if len(days) else 0.0
    seven = weekend_share > 0.15
    spy = 365.25 if seven else 365.25 * 5.0 / 7.0
    mask = "1111111" if seven else "1111100"
    if len(days) < 2:
        return spy, "grid"
    bdays = int(np.busday_count(days[0], days[-1], weekmask=mask))
    if bdays <= 0:
        return spy, "grid"
    if freq == "bar":
        return spy * len(ts) / (bdays + 1), "grid"
    return spy * (len(days) - 1) / bdays, "grid"


def period_returns(levels: np.ndarray, basis: str) -> np.ndarray:
    """Per-period returns of the level path (see module docstring); NaN where a NAV return is undefined."""
    if basis == "pnl":
        return np.diff(levels)
    prev = levels[:-1]
    out = np.full(len(prev), np.nan)
    np.divide(levels[1:], prev, out=out, where=prev > 0)
    return out - 1.0


def drawdown_path(levels: np.ndarray, basis: str) -> np.ndarray:
    """Drawdown at every level (base included): nav ``L/M - 1``, pnl ``L - M`` with ``M`` the running peak."""
    peak = np.maximum.accumulate(levels)
    if basis == "pnl":
        return levels - peak
    out = np.full(len(levels), np.nan)
    np.divide(levels, peak, out=out, where=peak > 0)
    return out - 1.0


def drawdown_duration(levels: np.ndarray, ts: pd.DatetimeIndex) -> Tuple[int, int]:
    """Longest drawdown episode as ``(points, days)``: from its peak observation to the first later observation with
    ``L >= peak`` (or the last observation if unrecovered). `ts` has one entry per level."""
    peak = np.maximum.accumulate(levels)
    under = levels < peak
    if not under.any():
        return 0, 0
    edges = np.diff(np.concatenate([[0], under.astype(int), [0]]))
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
    best_pts, best_days = 0, 0
    n = len(levels)
    for s, e in zip(starts, ends):
        rec = e if e < n else n - 1
        pts = int(rec - (s - 1))
        days = int((ts[rec] - ts[s - 1]).days)
        best_pts, best_days = max(best_pts, pts), max(best_days, days)
    return best_pts, best_days


def profit_factor(x: np.ndarray) -> float:
    """Sum of gains over the absolute sum of losses; inf if only gains, NaN if empty or all zero."""
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) == 0:
        return float("nan")
    gains, losses = float(x[x > 0].sum()), float(-x[x < 0].sum())
    if losses == 0.0:
        return float("inf") if gains > 0 else float("nan")
    return gains / losses


def var_cvar(r: np.ndarray, level: float) -> Tuple[float, float]:
    """Historical VaR and CVaR at `level` as positive loss numbers (module docstring)."""
    if len(r) == 0:
        return float("nan"), float("nan")
    q = float(np.quantile(r, 1.0 - level))
    return -q, -float(np.mean(r[r <= q]))


def _is_degenerate(r: np.ndarray, mu: float, sigma: float) -> bool:
    return not (sigma > _DEGENERATE * max(abs(mu), float(np.max(np.abs(r)))))


def compute_stats(equity: pd.Series, initial_capital: float, config: StatsConfig = StatsConfig()) -> Dict[str, Any]:
    """All path statistics of `equity` (a tz-aware Series that INCLUDES the initial capital) as a dict of floats."""
    K = float(initial_capital)
    basis = config.resolve_basis(K)
    obs = observations(equity, K, config.freq)
    A, source = annualisation(obs.ts, config.freq, config.annualisation)
    lv = obs.levels
    r_all = period_returns(lv, basis)
    r = r_all[np.isfinite(r_all)]
    n = len(r)
    ts_full = pd.DatetimeIndex([equity.index[0], *obs.ts])
    nan = float("nan")
    out: Dict[str, Any] = {
        "start": equity.index[0],
        "end": equity.index[-1],
        "basis": basis,
        "freq": config.freq,
        "periods_per_year": A,
        "annualisation_source": source,
        "n_periods": n,
        "total_pnl": float(equity.iloc[-1]) - K,
        "peak_pnl": float(lv.max()) - K,
    }
    mu = float(np.mean(r)) if n else nan
    sigma = float(np.std(r, ddof=1)) if n > 1 else nan
    degenerate = n < 2 or _is_degenerate(r, mu, sigma)
    out["mean"], out["std"] = mu, sigma
    out["ann_return"] = mu * A if n else nan
    out["ann_vol"] = sigma * math.sqrt(A) if n > 1 else nan
    rf_p, mar_p = config.rf / A, config.mar / A
    out["sharpe"] = nan if degenerate else (mu - rf_p) / sigma * math.sqrt(A)
    dd_semi = math.sqrt(float(np.mean(np.minimum(r - mar_p, 0.0) ** 2))) if n else 0.0
    out["sortino"] = nan if (degenerate or dd_semi == 0.0) else (mu - mar_p) / dd_semi * math.sqrt(A)
    dd = drawdown_path(lv, basis)
    out["max_drawdown"] = float(np.nanmin(dd))
    out["current_drawdown"] = float(dd[-1])
    out["max_dd_duration_points"], out["max_dd_duration_days"] = drawdown_duration(lv, ts_full)
    cagr = nan
    if basis == "nav":
        years = (equity.index[-1] - equity.index[0]).total_seconds() / SECONDS_PER_YEAR
        end = float(equity.iloc[-1])
        if years > 0 and end > 0:
            expo = math.log(end / K) / years
            cagr = math.expm1(expo) if expo < 700 else nan
    out["cagr"] = cagr
    numer = cagr if basis == "nav" else out["ann_return"]
    out["calmar"] = nan if (degenerate or out["max_drawdown"] == 0.0) else numer / abs(out["max_drawdown"])
    out["best"] = float(np.max(r)) if n else nan
    out["worst"] = float(np.min(r)) if n else nan
    out["hit_rate"] = float(np.mean(r > 0)) if n else nan
    out["pct_positive"] = 100.0 * out["hit_rate"] if n else nan
    out["profit_factor"] = profit_factor(r)
    out["skew"] = float(pd.Series(r).skew()) if n else nan
    out["kurt"] = float(pd.Series(r).kurt()) if n else nan
    out["var"], out["cvar"] = var_cvar(r, config.var_level)
    out["var_level"] = config.var_level
    return out


def returns(equity: pd.Series, initial_capital: float, config: StatsConfig = StatsConfig()) -> pd.Series:
    """Period returns (basis of `config`) indexed by the end timestamp of each period (NaN where undefined)."""
    basis = config.resolve_basis(float(initial_capital))
    obs = observations(equity, initial_capital, config.freq)
    return pd.Series(period_returns(obs.levels, basis), index=obs.ts, name=f"return_{basis}")


def drawdown(equity: pd.Series, initial_capital: float, config: StatsConfig = StatsConfig()) -> pd.Series:
    """Drawdown at every observation (base excluded), same units as `max_drawdown`."""
    basis = config.resolve_basis(float(initial_capital))
    obs = observations(equity, initial_capital, config.freq)
    return pd.Series(drawdown_path(obs.levels, basis)[1:], index=obs.ts, name=f"drawdown_{basis}")


def rolling_sharpe(equity: pd.Series, initial_capital: float, config: StatsConfig = StatsConfig(), window: Optional[int] = None) -> pd.Series:
    """Rolling annualised Sharpe over `window` periods (default `config.rolling_window`); NaN until the window fills."""
    w = window or config.rolling_window
    r = returns(equity, initial_capital, config)
    A, _ = annualisation(r.index, config.freq, config.annualisation)
    roll = r.rolling(w, min_periods=w)
    mu, sd = roll.mean(), roll.std(ddof=1)
    scale = r.abs().rolling(w, min_periods=w).max()
    ok = sd > _DEGENERATE * np.maximum(mu.abs(), scale)
    return ((mu - config.rf / A) / sd * math.sqrt(A)).where(ok).rename("rolling_sharpe")


def time_in_market(index: pd.DatetimeIndex, n_positions: pd.Series) -> float:
    """Time-weighted share of the span during which at least one position was held over the interval ending at each row."""
    if len(index) < 2:
        return float("nan")
    dt = np.diff(index.asi8).astype(float)
    held = (n_positions.to_numpy()[:-1] > 0).astype(float)
    total = dt.sum()
    return float((dt * held).sum() / total) if total > 0 else float("nan")
