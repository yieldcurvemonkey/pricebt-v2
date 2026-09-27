"""BacktestResult: the object users hold after a run."""
from __future__ import annotations

import os
from dataclasses import replace
from typing import Any, Dict, Mapping, Optional, Sequence, Union

import pandas as pd

from ..engine.state import RunRecord
from .attribution import attribution as _attribution, layer_series as _layer_series
from . import ledger as _ledger
from . import stats as _stats
from .errors import ResultError
from .reconcile import ReconcileReport, reconcile as _reconcile


class BacktestResult:
    """Read-only view over a `RunRecord`.

    ``equity`` is NAV when ``initial_capital > 0`` and cumulative P&L when it is 0. Positions are indexed by position id
    (``P000001``); ``pnl_net`` is the engine's position P&L (after costs), ``pnl_gross`` excludes costs, ``pnl_price``
    is the price leg and ``pnl_income`` the carry / cash / financing realised during the hold.
    """

    def __init__(self, record: RunRecord, *, config_hash: Optional[str] = None, grid_info: Optional[Mapping[str, Any]] = None,
                 stats_config: Optional[_stats.StatsConfig] = None):
        eq = record.equity
        if eq is None or len(eq) == 0:
            raise ResultError("cannot build a result from an empty equity frame (the run recorded no timeline points)")
        if not isinstance(eq.index, pd.DatetimeIndex) or eq.index.tz is None:
            raise ResultError("equity must be indexed by tz-aware timestamps")
        self.record = record
        self.config_hash = config_hash
        self.stats_config = stats_config or _stats.StatsConfig()
        self._positions = _ledger.positions_table(record.positions, eq.index)
        self.grid_info: Dict[str, Any] = dict(grid_info) if grid_info is not None else self._derive_grid_info()

    @classmethod
    def from_record(cls, record: RunRecord, **kw: Any) -> "BacktestResult":
        return cls(record, **kw)

    def _derive_grid_info(self) -> Dict[str, Any]:
        idx = self.equity.index
        days = pd.Index(idx.tz_localize(None).normalize()).nunique()
        step = pd.Series(idx).diff().median() if len(idx) > 1 else pd.NaT
        return {"n_points": int(len(idx)), "start": str(idx[0]), "end": str(idx[-1]), "n_sessions": int(days),
                "points_per_session": float(len(idx) / days), "median_step_seconds": None if pd.isna(step) else float(step.total_seconds())}

    # ---------------------------------------------------------------- frames
    @property
    def name(self) -> str:
        return self.record.settings.name

    @property
    def initial_capital(self) -> float:
        return float(self.record.settings.initial_capital)

    @property
    def equity(self) -> pd.DataFrame:
        return self.record.equity

    @property
    def equity_curve(self) -> pd.Series:
        return self.equity["equity"]

    @property
    def pnl(self) -> pd.Series:
        """Cumulative net P&L in currency (``equity - initial_capital``)."""
        return (self.equity["equity"] - self.initial_capital).rename("pnl")

    @property
    def step_pnl(self) -> pd.Series:
        return self.equity["step_pnl"]

    @property
    def trades(self) -> pd.DataFrame:
        return self.record.trades

    @property
    def orders(self) -> pd.DataFrame:
        return self.record.orders

    @property
    def errors(self) -> pd.DataFrame:
        return self.record.errors

    @property
    def events(self) -> pd.DataFrame:
        return self.record.events

    @property
    def n_errors(self) -> int:
        return int(len(self.record.errors))

    @property
    def positions(self) -> pd.DataFrame:
        return self._positions

    @property
    def layers(self) -> pd.DataFrame:
        """Cumulative portfolio layer P&L per row (empty columns when no layer was requested)."""
        return _layer_series(self)

    @property
    def layers_by_position(self) -> pd.DataFrame:
        return _ledger.layer_frame(self.record.layers_by_position, self._positions.index)

    @property
    def measures(self) -> pd.DataFrame:
        cols = [c for c in self.equity.columns if c.startswith("measure_")]
        out = self.equity[cols].copy()
        out.columns = [c[len("measure_"):] for c in cols]
        return out

    @property
    def vectors(self) -> Dict[str, pd.DataFrame]:
        """Recorded vector measures (settings.vector_measures): name -> frame indexed by ts, one column per bucket (e.g. the book's delta ladder)."""
        return dict(self.record.vectors)

    @property
    def manifest(self) -> Dict[str, Any]:
        m = dict(self.record.manifest)
        m["config_hash"] = self.config_hash
        m["grid"] = dict(self.grid_info)
        return m

    # ---------------------------------------------------------------- trade views
    def closed_trades(self) -> pd.DataFrame:
        """Closed positions: holding period (``hold_points``, ``hold_days``), ``exit_reason``, ``pnl_gross`` / ``pnl_net``
        including the carry and financing realised during the hold, ``pnl_price`` and ``pnl_income`` separately."""
        return _ledger.closed_trades(self._positions)

    def open_positions(self) -> pd.DataFrame:
        p = self._positions
        return p[p["status"] != "closed"] if len(p) else p

    def trade_ledger(self) -> pd.DataFrame:
        """gs_quant vocabulary (Open, Close, Open Value, Close Value, Long Short, Status, Trade PnL)."""
        return _ledger.trade_ledger(self._positions)

    @property
    def result_summary(self) -> pd.DataFrame:
        """gs_quant vocabulary: ``Price`` (positions value) + ``Cumulative Cash`` (initial capital + cash account) +
        ``Transaction Costs`` (cumulative, <= 0) = ``Total`` (equity) on every row."""
        eq = self.equity
        return pd.DataFrame(
            {"Price": eq["positions_value"], "Cumulative Cash": self.initial_capital + eq["cash"], "Transaction Costs": eq["tcost"], "Total": eq["equity"]}
        )

    # ---------------------------------------------------------------- statistics
    def _config(self, config: Optional[_stats.StatsConfig], overrides: Mapping[str, Any]) -> _stats.StatsConfig:
        base = config or self.stats_config
        return replace(base, **overrides) if overrides else base

    def returns(self, config: Optional[_stats.StatsConfig] = None, **overrides: Any) -> pd.Series:
        return _stats.returns(self.equity_curve, self.initial_capital, self._config(config, overrides))

    def drawdown(self, config: Optional[_stats.StatsConfig] = None, **overrides: Any) -> pd.Series:
        return _stats.drawdown(self.equity_curve, self.initial_capital, self._config(config, overrides))

    def rolling_sharpe(self, window: Optional[int] = None, config: Optional[_stats.StatsConfig] = None, **overrides: Any) -> pd.Series:
        return _stats.rolling_sharpe(self.equity_curve, self.initial_capital, self._config(config, overrides), window)

    def summary_stats(self, config: Optional[_stats.StatsConfig] = None, *, labels: str = "snake", **overrides: Any) -> pd.Series:
        """Path statistics (`pricebt.results.stats` docstring) plus trade statistics of the closed-trade log, total
        transaction costs (P&L-signed, <= 0), fill counts and time in market. ``labels="gs"`` renames the gs_quant subset."""
        if labels not in ("snake", "gs"):
            raise ResultError("labels must be 'snake' or 'gs'")
        cfg = self._config(config, overrides)
        d = _stats.compute_stats(self.equity_curve, self.initial_capital, cfg)
        d["total_tcost"] = float(self.equity["tcost"].iloc[-1])
        d.update(_ledger.trade_summary(self._positions))
        tr = self.trades
        d["n_fills"] = int(len(tr))
        d["n_resizes"] = int((tr["kind"] == "resize").sum()) if len(tr) else 0
        d["turnover"] = float((tr["quantity"].abs() * tr["pv"].abs()).sum()) if len(tr) else 0.0
        d["time_in_market"] = _stats.time_in_market(self.equity.index, self.equity["n_positions"])
        d["n_errors"] = self.n_errors
        if labels == "gs":
            level = f"{cfg.var_level:.0%}"
            names = {**_stats.GS_LABELS, "var": f"VaR {level}", "cvar": f"CVaR {level}"}
            d = {names.get(k, k): v for k, v in d.items()}
        return pd.Series(d, dtype=object)

    # ---------------------------------------------------------------- attribution / integrity
    def attribution(self, by: str = "layer", *, tag_mode: str = "split") -> pd.DataFrame:
        return _attribution(self, by, tag_mode=tag_mode)

    def reconcile(self, tol: float = 1e-10, atol: float = 1e-8, strict: bool = False) -> ReconcileReport:
        return _reconcile(self, tol, atol, strict)

    # ---------------------------------------------------------------- output
    def tearsheet(self, path: Union[str, os.PathLike, None] = None, formats: Optional[Sequence[str]] = None, **kw: Any) -> Any:
        from .tearsheet import tearsheet

        return tearsheet(self, path, formats, **kw)

    def to_parquet(self, directory: Union[str, os.PathLike]) -> Any:
        from .io import to_parquet

        return to_parquet(self, directory)

    @classmethod
    def from_parquet(cls, directory: Union[str, os.PathLike]) -> "BacktestResult":
        from .io import from_parquet

        return from_parquet(directory)

    def __repr__(self) -> str:
        e = self.equity
        return f"BacktestResult({self.name!r}, {len(e)} points {e.index[0]} -> {e.index[-1]}, pnl={float(self.pnl.iloc[-1]):,.4f}, {len(self._positions)} positions)"
