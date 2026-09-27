"""Deterministic matplotlib tearsheet: PNG / PDF composite figure and a self-contained HTML page.

Rendering uses ``Figure`` + the Agg canvas (never ``pyplot``, so no backend state is touched) inside a fixed
``rc_context``; file metadata (timestamps, versions) is suppressed, so two renders of one result are byte-identical
on one machine. ``rc_context`` mutates process-global rcParams while active, so rendering is not thread-safe.
Panels without data are omitted, never drawn empty.
"""
from __future__ import annotations

import base64
import html as _html
import io
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd

from ..errors import OptionalDependencyError
from .errors import ResultError

PALETTE = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948")
INK, SECONDARY, MUTED, GRID = "#0b0b0b", "#52514e", "#898781", "#e1e0d9"
LAYER_COLORS = {"unexplained": MUTED, "transactions": SECONDARY, "cash_interest": "#c3c2b7", "total": INK}
PANELS = ("equity", "drawdown", "rolling_sharpe", "attribution", "layers", "exposure", "positions_value", "trades", "measures")
FORMATS = ("html", "png", "pdf")
MAX_MEASURE_ROWS = 4
DECIMATE_ABOVE = 8000
_RC = {
    "font.family": "DejaVu Sans", "font.size": 9, "axes.titlesize": 10, "axes.titleweight": "bold", "axes.titlelocation": "left",
    "axes.facecolor": "#fcfcfb", "figure.facecolor": "#fcfcfb", "axes.edgecolor": "#c3c2b7", "axes.grid": True, "grid.color": GRID,
    "grid.linewidth": 0.6, "axes.spines.top": False, "axes.spines.right": False, "axes.labelcolor": SECONDARY, "text.color": INK,
    "xtick.color": SECONDARY, "ytick.color": SECONDARY, "legend.frameon": False, "svg.hashsalt": "pricebt", "svg.fonttype": "none",
    "pdf.fonttype": 42, "path.simplify": False, "figure.dpi": 100,
}
_PNG_META = {"Software": None}
_PDF_META = {"Creator": None, "Producer": None, "CreationDate": None}
_STAT_ROWS = (
    "total_pnl", "total_tcost", "ann_return", "ann_vol", "sharpe", "sortino", "max_drawdown", "max_dd_duration_days", "calmar", "hit_rate",
    "profit_factor", "var", "cvar", "n_trades", "n_closed", "n_open", "trade_hit_rate", "avg_hold_days", "time_in_market",
    "periods_per_year", "annualisation_source", "basis", "n_periods",
)


@dataclass(frozen=True)
class TearsheetOutput:
    """`paths` written (empty when no path was given); `html` page text if requested; `figures`: format -> bytes."""

    paths: Tuple[Path, ...]
    html: Optional[str]
    figures: Mapping[str, bytes]


def _mpl() -> Tuple[Any, Any, Any, Any]:
    try:
        import matplotlib
        import matplotlib.dates as mdates
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
    except ImportError as e:
        raise OptionalDependencyError("the tearsheet needs matplotlib (pip install matplotlib)") from e
    return matplotlib, mdates, FigureCanvasAgg, Figure


def decimate(x: np.ndarray, y: np.ndarray, buckets: int = 2000) -> Tuple[np.ndarray, np.ndarray]:
    """Deterministic min/max decimation: keep first, last, min and max point of each of `buckets` equal index ranges."""
    n = len(y)
    if n <= 4 * buckets:
        return x, y
    keep = {0, n - 1}
    edges = np.linspace(0, n, buckets + 1).astype(int)
    for a, b in zip(edges[:-1], edges[1:]):
        if b > a:
            seg = y[a:b]
            keep.update((a, b - 1, a + int(np.argmin(seg)), a + int(np.argmax(seg))))
    sel = np.array(sorted(keep))
    return x[sel], y[sel]


def _available(result: Any) -> Dict[str, bool]:
    eq = result.equity
    n_obs = len(result.returns().dropna())
    layers = result.layers
    measure_cols = [c for c in eq.columns if c.startswith("measure_")]
    return {
        "equity": True,
        "drawdown": True,
        "rolling_sharpe": n_obs >= 6,
        "attribution": True,
        "layers": bool(len(layers.columns)) and bool((layers.abs().to_numpy() > 0).any()),
        "exposure": True,
        "positions_value": bool((eq["positions_value"].abs() > 0).any()),
        "trades": len(result.closed_trades()) > 0,
        "measures": bool(measure_cols),
    }


def _dates(ax: Any, mdates: Any) -> None:
    loc = mdates.AutoDateLocator(minticks=3, maxticks=7)
    ax.xaxis.set_major_locator(loc)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc))


def _xy(index: pd.DatetimeIndex, values: Any) -> Tuple[np.ndarray, np.ndarray]:
    x, y = index.tz_localize(None).to_numpy(), np.asarray(values, dtype=float)
    return decimate(x, y) if len(index) > DECIMATE_ABOVE else (x, y)


def _line(ax: Any, index: pd.DatetimeIndex, values: Any, color: str = PALETTE[0], label: Optional[str] = None, lw: float = 1.3, **kw: Any) -> None:
    x, y = _xy(index, values)
    ax.plot(x, y, color=color, lw=lw, label=label, **kw)


def _layer_color(name: str, k: int) -> str:
    return LAYER_COLORS.get(name, PALETTE[k % len(PALETTE)])


def _draw(name: str, axes: List[Any], result: Any, mdates: Any) -> None:
    eq, ax, K = result.equity, axes[0], result.initial_capital
    if name == "equity":
        _line(ax, eq.index, eq["equity"])
        if K > 0:
            ax.axhline(K, color=MUTED, lw=0.8)
        ax.set_title("Equity (NAV)" if K > 0 else "Cumulative P&L")
        _dates(ax, mdates)
    elif name == "drawdown":
        dd = result.drawdown(freq="bar")
        x, y = _xy(dd.index, dd.to_numpy())
        ax.fill_between(x, y, 0.0, color=PALETTE[7], alpha=0.25, linewidth=0)
        ax.plot(x, y, color=PALETTE[7], lw=1.0)
        basis = result.summary_stats()["basis"]
        ax.set_title("Drawdown (fraction of peak NAV)" if basis == "nav" else "Drawdown (currency)")
        _dates(ax, mdates)
    elif name == "rolling_sharpe":
        n_obs = len(result.returns().dropna())
        w = min(result.stats_config.rolling_window, max(5, n_obs // 2))
        rs = result.rolling_sharpe(window=w).dropna()
        _line(ax, rs.index, rs.to_numpy(), PALETTE[2])
        ax.axhline(0.0, color=MUTED, lw=0.8)
        ax.set_title(f"Rolling Sharpe ({w} periods)")
        _dates(ax, mdates)
    elif name == "attribution":
        by = result.attribution("layer").drop(index="total")
        colors = [_layer_color(n, k) for k, n in enumerate(by.index)]
        ax.barh(list(by.index), by["pnl"].to_numpy(), color=colors)
        ax.axvline(0.0, color=MUTED, lw=0.8)
        ax.invert_yaxis()
        ax.set_title("P&L attribution by layer (currency)")
    elif name == "layers":
        lay = result.layers
        for k, c in enumerate(lay.columns):
            _line(ax, lay.index, lay[c].to_numpy(), _layer_color(c, k), label=c, lw=1.1)
        ax.axhline(0.0, color=MUTED, lw=0.8)
        ax.legend(ncol=min(len(lay.columns), 4), loc="best")
        ax.set_title("Cumulative layer P&L")
        _dates(ax, mdates)
    elif name == "exposure":
        ax.step(eq.index.tz_localize(None).to_numpy(), eq["n_positions"].to_numpy(dtype=float), where="post", color=PALETTE[3], lw=1.3)
        ax.set_title("Open positions")
        _dates(ax, mdates)
    elif name == "positions_value":
        _line(ax, eq.index, eq["positions_value"].to_numpy(), PALETTE[4])
        ax.axhline(0.0, color=MUTED, lw=0.8)
        ax.set_title("Positions value (net, currency)")
        _dates(ax, mdates)
    elif name == "trades":
        ct = result.closed_trades()
        reasons = sorted(ct["exit_reason"].astype(str).unique())
        for k, reason in enumerate(reasons):
            sub = ct[ct["exit_reason"].astype(str) == reason]
            ax.scatter(sub["hold_points"], sub["pnl_net"], s=22, color=PALETTE[k] if k < len(PALETTE) else MUTED, label=reason if k < len(PALETTE) else "other", alpha=0.85)
        ax.axhline(0.0, color=MUTED, lw=0.8)
        ax.set_xlabel("holding period (timeline points)")
        ax.legend(loc="best")
        ax.set_title("Closed trades: net P&L vs holding period")
    elif name == "measures":
        cols = [c for c in eq.columns if c.startswith("measure_")][:MAX_MEASURE_ROWS]
        for k, (a, c) in enumerate(zip(axes, cols)):
            _line(a, eq.index, eq[c].to_numpy(), PALETTE[k % len(PALETTE)])
            a.set_title(c[len("measure_"):] if k else f"Measures: {c[len('measure_'):]}")
            _dates(a, mdates)


def render(result: Any, panels: Optional[Sequence[str]] = None, formats: Sequence[str] = ("png",), dpi: int = 110) -> Dict[str, bytes]:
    """Render the composite figure; returns ``{format: bytes}`` for `formats` in ``png``/``pdf`` (deterministic)."""
    bad = [f for f in formats if f not in ("png", "pdf")]
    if bad:
        raise ResultError(f"figure formats must be png or pdf, got {bad}")
    mpl, mdates, Canvas, Figure = _mpl()
    avail = _available(result)
    unknown = [p for p in (panels or ()) if p not in PANELS]
    if unknown:
        raise ResultError(f"unknown panels {unknown}; choose from {PANELS}")
    chosen = [p for p in (panels or PANELS) if avail[p]]
    rows = [min(MAX_MEASURE_ROWS, sum(c.startswith("measure_") for c in result.equity.columns)) if p == "measures" else 1 for p in chosen]
    out: Dict[str, bytes] = {}
    with mpl.rc_context(_RC):
        fig = Figure(figsize=(10.0, 2.5 * max(1, sum(rows)) + 0.6), layout="constrained")
        Canvas(fig)
        gs = fig.add_gridspec(max(1, sum(rows)), 1)
        r0 = 0
        for name, nrow in zip(chosen, rows):
            axes = [fig.add_subplot(gs[r0 + i, 0]) for i in range(nrow)]
            _draw(name, axes, result, mdates)
            r0 += nrow
        fig.suptitle(f"{result.name}", x=0.01, ha="left", fontsize=12, fontweight="bold")
        for fmt in formats:
            buf = io.BytesIO()
            fig.savefig(buf, format=fmt, dpi=dpi, metadata=_PNG_META if fmt == "png" else _PDF_META)
            out[fmt] = buf.getvalue()
    return out


def comparison_figure(results: Mapping[str, Any], dpi: int = 110) -> bytes:
    """PNG of every strategy's cumulative net P&L (``equity - initial_capital``) on one axis (deterministic)."""
    if not results:
        raise ResultError("comparison_figure needs at least one result")
    mpl, mdates, Canvas, Figure = _mpl()
    with mpl.rc_context(_RC):
        fig = Figure(figsize=(10.0, 3.6), layout="constrained")
        Canvas(fig)
        ax = fig.add_subplot(1, 1, 1)
        for k, (name, r) in enumerate(results.items()):
            _line(ax, r.equity.index, r.pnl.to_numpy(), PALETTE[k % len(PALETTE)], label=name)
        ax.axhline(0.0, color=MUTED, lw=0.8)
        ax.set_title("Cumulative net P&L (currency)")
        ax.legend(loc="best")
        _dates(ax, mdates)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=dpi, metadata=_PNG_META)
    return buf.getvalue()


def _table(df: pd.DataFrame, index: bool = True) -> str:
    return df.to_html(escape=True, border=0, index=index, na_rep="", float_format=lambda v: f"{v:,.4f}", classes="t")


def stats_frame(result: Any) -> pd.DataFrame:
    s = result.summary_stats()
    return pd.DataFrame({"value": [s[k] for k in _STAT_ROWS if k in s.index]}, index=[k for k in _STAT_ROWS if k in s.index])


_CSS = (
    "body{font:14px/1.45 system-ui,sans-serif;color:#0b0b0b;background:#fcfcfb;max-width:1000px;margin:2em auto;padding:0 1em}"
    "h1{font-size:1.5em}h2{font-size:1.1em;margin-top:1.8em;border-bottom:1px solid #e1e0d9}"
    "table.t{border-collapse:collapse;font-size:12px}table.t td,table.t th{padding:2px 10px;border-bottom:1px solid #e1e0d9;text-align:right}"
    "table.t th:first-child,table.t td:first-child{text-align:left}img{max-width:100%}details{margin:1em 0}html{color-scheme:light}"
)


def build_html(result: Any, png: Optional[bytes], title: Optional[str] = None, top_n: int = 10) -> str:
    """Self-contained page (no external asset, no JS): summary, figure, attribution, trades, reconcile, errors, manifest."""
    esc = _html.escape
    ttl = title or f"{result.name} tearsheet"
    parts = [f"<!doctype html><html><head><meta charset='utf-8'><title>{esc(ttl)}</title><style>{_CSS}</style></head><body><h1>{esc(ttl)}</h1>"]
    eq = result.equity
    parts.append(f"<p>{esc(str(eq.index[0]))} to {esc(str(eq.index[-1]))}, {len(eq)} points, initial capital {result.initial_capital:,.2f}, "
                 f"config hash {esc(str(result.config_hash))}</p>")
    parts.append("<h2>Summary statistics</h2>" + _table(stats_frame(result)))
    if png is not None:
        parts.append("<h2>Charts</h2><img alt='tearsheet' src='data:image/png;base64," + base64.b64encode(png).decode("ascii") + "'>")
    parts.append("<h2>Attribution by layer</h2>" + _table(result.attribution("layer")))
    ct = result.closed_trades()
    cols = ["template", "action", "side", "entry_ts", "exit_ts", "exit_reason", "hold_points", "pnl_price", "pnl_income", "tcost", "pnl_net"]
    if len(ct):
        parts.append(f"<h2>Best {top_n} closed trades</h2>" + _table(ct.sort_values("pnl_net", ascending=False, kind="stable").head(top_n)[cols]))
        parts.append(f"<h2>Worst {top_n} closed trades</h2>" + _table(ct.sort_values("pnl_net", kind="stable").head(top_n)[cols]))
        parts.append("<h2>By exit reason</h2>" + _table(ct.groupby("exit_reason")["pnl_net"].agg(["count", "sum", "mean"])))
    parts.append("<h2>Reconciliation</h2>" + _table(result.reconcile().to_frame()[["passed", "severity", "max_abs_error", "n_violations", "detail"]]))
    if result.n_errors:
        parts.append("<h2>Errors</h2>" + _table(result.errors, index=False))
    parts.append("<details><summary>Manifest</summary><pre>" + esc(json.dumps(result.manifest, indent=2, sort_keys=True, default=str)) + "</pre></details>")
    parts.append("</body></html>")
    return "".join(parts)


def tearsheet(result: Any, path: Union[str, os.PathLike, None] = None, formats: Optional[Sequence[str]] = None, *, title: Optional[str] = None,
              panels: Optional[Sequence[str]] = None, dpi: int = 110, top_n: int = 10) -> TearsheetOutput:
    """Render and optionally write the tearsheet.

    `path` with a ``.html/.png/.pdf`` suffix writes exactly that format; a path without suffix is a stem and every entry of
    `formats` (default ``("html",)``) writes ``<stem>.<fmt>``; ``None`` writes nothing and returns the bytes/text.
    """
    target = Path(path) if path is not None else None
    if target is not None and target.suffix.lower().lstrip(".") in FORMATS:
        fmts: Tuple[str, ...] = (target.suffix.lower().lstrip("."),)
        stem = target.with_suffix("")
    else:
        fmts = tuple(formats or ("html",))
        stem = target
    bad = [f for f in fmts if f not in FORMATS]
    if bad:
        raise ResultError(f"unknown tearsheet formats {bad}; choose from {FORMATS}")
    need = tuple(dict.fromkeys(("png" if ("png" in fmts or "html" in fmts) else None, "pdf" if "pdf" in fmts else None)))
    figs = render(result, panels, tuple(f for f in need if f), dpi)
    page = build_html(result, figs.get("png"), title, top_n) if "html" in fmts else None
    written: List[Path] = []
    if stem is not None:
        stem.parent.mkdir(parents=True, exist_ok=True)
        for f in fmts:
            p = stem.with_name(f"{stem.name}.{f}")
            if f == "html":
                p.write_text(page or "", encoding="utf8")
            else:
                p.write_bytes(figs[f])
            written.append(p)
    return TearsheetOutput(tuple(written), page, figs)
