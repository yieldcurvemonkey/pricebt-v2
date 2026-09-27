"""pricebt.results: BacktestResult, statistics, attribution, reconcile, tearsheet, persistence, comparison, report.

matplotlib (tearsheet, report figures) and pyarrow (parquet) are imported lazily by the functions that need them.
"""
from .attribution import attribution, layer_series
from .compare import DEFAULT_COMPARISON_STATS, Comparison, combined_equity, compare, comparison_table, correlation, pnl_matrix
from .errors import ReconcileError, ResultError, ResultIntegrityError, ResultWarning, StatsError
from .reconcile import Check, ReconcileReport, reconcile
from .report import ReportBundle, build_report, df_to_markdown, limitations
from .result import BacktestResult
from .stats import StatsConfig, annualisation, compute_stats

__all__ = [
    "BacktestResult", "StatsConfig", "compute_stats", "annualisation", "attribution", "layer_series", "reconcile", "ReconcileReport", "Check",
    "compare", "Comparison", "comparison_table", "pnl_matrix", "correlation", "combined_equity", "DEFAULT_COMPARISON_STATS",
    "build_report", "ReportBundle", "df_to_markdown", "limitations",
    "ResultError", "StatsError", "ReconcileError", "ResultIntegrityError", "ResultWarning",
]
