"""Markdown experiment report from a ``{name: BacktestResult}`` dict: tables, figure files and an honest-limitations block."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Mapping, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd

from .compare import compare
from .errors import ResultError
from .stats import StatsConfig, compute_stats
from .tearsheet import comparison_figure, render, stats_frame

SMALL_SAMPLE = 60
LARGE_UNEXPLAINED = 0.25


@dataclass(frozen=True)
class ReportBundle:
    markdown: Path
    figures: Tuple[Path, ...]
    tables: Tuple[Path, ...]


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "run"


def _cell(v: Any, floatfmt: str) -> str:
    if v is None or v is pd.NaT or (isinstance(v, (float, np.floating)) and math.isnan(v)):
        return ""
    if isinstance(v, (bool, np.bool_)):
        return str(bool(v))
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    if isinstance(v, (float, np.floating)):
        return "inf" if math.isinf(v) and v > 0 else "-inf" if math.isinf(v) else floatfmt.format(float(v))
    return str(v)


def df_to_markdown(df: pd.DataFrame, *, floatfmt: str = "{:,.4f}", index: bool = True, formats: Optional[Mapping[str, str]] = None) -> str:
    """Pipe table without external dependencies: NaN -> empty, ``|`` escaped, per-column float formats in `formats`."""
    formats = formats or {}
    cols = [str(c) for c in df.columns]
    head = ([str(df.index.name or "")] if index else []) + cols
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    for label, row in zip(df.index, df.itertuples(index=False)):
        cells = ([str(label)] if index else []) + [_cell(v, formats.get(c, floatfmt)) for c, v in zip(cols, row)]
        lines.append("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
    return "\n".join(lines)


def limitations(results: Mapping[str, Any], extra: Sequence[str] = ()) -> List[str]:
    """Data-driven limitation statements for these results, followed by the caller's own `extra` lines."""
    out: List[str] = []
    for name, r in results.items():
        s = r.summary_stats()
        if s["n_periods"] < SMALL_SAMPLE:
            out.append(f"{name}: only {s['n_periods']} return periods; volatility, Sharpe, Sortino and VaR are illustrative, not estimates.")
        if s["annualisation_source"] == "grid":
            out.append(f"{name}: annualisation factor {s['periods_per_year']:.1f} periods/year is derived from the grid without a holiday list "
                       f"(weekday grids give about 260.9); pass `annualisation` to force another convention.")
        if s["basis"] == "pnl":
            out.append(f"{name}: initial capital is 0, so returns are currency P&L per period; ann_return, ann_vol, VaR and drawdown are in currency, not percent.")
        if r.record.layers_by_position.empty:
            out.append(f"{name}: no modelled P&L layers were recorded, so the whole gross P&L sits in the 'unexplained' bucket (the price / cash / financing split is exact).")
        else:
            att = r.attribution("layer")
            gross = float(att["pnl"].drop(["total", "transactions", "cash_interest"], errors="ignore").abs().sum())
            share = abs(float(att.loc["unexplained", "pnl"])) / gross if gross > 0 else 0.0
            if share > LARGE_UNEXPLAINED:
                out.append(f"{name}: unexplained P&L is {share:.0%} of gross attributed P&L; the layers are a weak description of this run.")
        if r.n_errors:
            out.append(f"{name}: {r.n_errors} engine errors were recorded (on_error != raise); affected marks or orders are missing.")
        if s["n_trades"] and s["total_tcost"] == 0.0:
            out.append(f"{name}: no transaction costs were charged; net results equal gross results.")
        if s["n_open"]:
            out.append(f"{name}: {s['n_open']} positions were still open at the end; their P&L is an unrealised mark.")
        rep = r.reconcile()
        if not rep.ok:
            out.append(f"{name}: reconcile FAILED ({', '.join(c.name for c in rep.failed())}); do not trust these numbers.")
    out.append("Fills are booked at the pricer mark of the fill point plus the configured cost model; no market impact, liquidity, queueing or margin effects are modelled.")
    out.append("Each result is one in-sample simulation of one configuration; nothing here is an out-of-sample or parameter-robustness estimate.")
    out.extend(extra)
    return out


def _md(df: pd.DataFrame, **kw: Any) -> str:
    return df_to_markdown(df, **kw) + "\n"


def build_report(results: Mapping[str, Any], out_dir: Union[str, Path], *, title: str = "pricebt results", intro: str = "",
                 extra_limitations: Sequence[str] = (), figures: bool = True, config: Optional[StatsConfig] = None, top_n: int = 10) -> ReportBundle:
    """Write ``report.md`` (+ ``figures/*.png`` and ``tables/*.csv``) under `out_dir`; returns the written paths."""
    if not results:
        raise ResultError("build_report needs at least one result")
    slugs = {n: slug(n) for n in results}
    if len(set(slugs.values())) != len(slugs):
        raise ResultError(f"result names collide after slugging: {slugs}")
    out = Path(out_dir)
    (out / "tables").mkdir(parents=True, exist_ok=True)
    fig_paths: List[Path] = []
    tab_paths: List[Path] = []

    def table(name: str, df: pd.DataFrame) -> None:
        p = out / "tables" / f"{name}.csv"
        df.to_csv(p)
        tab_paths.append(p)

    def figure(name: str, data: bytes) -> str:
        (out / "figures").mkdir(parents=True, exist_ok=True)
        p = out / "figures" / name
        p.write_bytes(data)
        fig_paths.append(p)
        return f"figures/{name}"

    cmp = compare(results, config=config)
    table("comparison", cmp.table)
    lines = [f"# {title}", ""]
    if intro:
        lines += [intro, ""]
    lines += ["## Summary", "", _md(cmp.table)]
    if len(results) > 1:
        table("pnl_correlation", cmp.correlation)
        comb = cmp.combined
        cs = compute_stats(comb["equity"], sum(r.initial_capital for r in results.values()), config or StatsConfig())
        lines += ["## Comparison", "", "Correlation of daily net P&L (pairwise complete):", "", _md(cmp.correlation),
                  f"Equal-weight combined book: total P&L {cs['total_pnl']:,.2f}, Sharpe {cs['sharpe']:.3f}, max drawdown {cs['max_drawdown']:,.4f}.", ""]
        if figures:
            lines += [f"![combined P&L]({figure('comparison_pnl.png', comparison_figure(results))})", ""]
    for name, r in results.items():
        sl = slugs[name]
        lines += [f"## {name}", "", _md(stats_frame(r), formats={"value": "{:,.4f}"}), "### Attribution by layer", "", _md(r.attribution("layer"))]
        table(f"attribution_layer_{sl}", r.attribution("layer"))
        for by in ("template", "tag"):
            a = r.attribution(by)
            if len(a) > 1:
                lines += [f"By {by}:", "", _md(a)]
                table(f"attribution_{by}_{sl}", a)
        ct = r.closed_trades()
        if len(ct):
            cols = ["template", "side", "exit_reason", "hold_points", "hold_days", "pnl_price", "pnl_income", "tcost", "pnl_net"]
            lines += [f"### Closed trades (best and worst {top_n})", "", _md(pd.concat([ct.sort_values("pnl_net", ascending=False, kind="stable").head(top_n),
                                                                                     ct.sort_values("pnl_net", kind="stable").head(top_n)]).drop_duplicates()[cols])]
            table(f"closed_{sl}", ct)
        rep = r.reconcile().to_frame()[["passed", "severity", "max_abs_error", "n_violations"]]
        table(f"reconcile_{sl}", rep)
        lines += ["### Integrity (reconcile)", "", _md(rep, formats={"max_abs_error": "{:.3e}"})]
        if figures:
            png = render(r, formats=("png",))["png"]
            lines += [f"![{name} tearsheet]({figure(f'{sl}_tearsheet.png', png)})", ""]
    lines += ["## Limitations", ""] + [f"- {x}" for x in limitations(results, extra_limitations)] + [""]
    md = out / "report.md"
    md.write_text("\n".join(lines), encoding="utf8")
    return ReportBundle(md, tuple(fig_paths), tuple(tab_paths))
