"""Tearsheet for a finished pricebt BackTest: metrics, charts, success-criteria verdict, and a
self-contained HTML + Markdown report with the CSV/JSON artefacts next to it.

In-process use (repository root as cwd, PYTHONPATH=src;tests):

    import sys; sys.path.insert(0, "skills/pricebt-tearsheet-report/scripts")
    import tearsheet
    paths = tearsheet.build_tearsheet(backtest, "reports/my-strategy", "My strategy", spec=spec,
                                      risk=IRDeltaParallel, signal=signal_series, spot_checks=results)

CLI: python skills/pricebt-tearsheet-report/scripts/tearsheet.py --demo OUT_DIR
     (toy USD 10y mean-reversion backtest, end to end, including the spot checks)
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import html
import io
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
SERIES, ACCENT, INK, MUTED, GRID, SURFACE = "#2a78d6", "#eb6834", "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
PRICEBT_CAVEATS = [
    "Coupons paid between marks are not booked as cash (gs parity): realised carry is missing from P&L.",
    "Signal and execution happen on the same close: no next-day fill, no slippage beyond the cost model.",
    "Costs and financing are exactly as modelled in the strategy (transaction-cost model, cash accrual); nothing else.",
    "Grid dates with no market data are dropped (missing_market='drop'); exits landing on them are rolled forward.",
]

# PNL_EXPLAIN_PLAN.md section 7: the "P&L attribution" section flags when residual_share is above a
# target. swap_pnl.py's own `RS_TARGET` (plan section 5.6's generic near-ATM-roll ceiling, 1e-3) is
# the SINGLE canonical default -- spot_check.py's `check_pnl_attribution` falls back to the same
# import, so the two don't drift into "two numbers meaning the same thing" (see that function's own
# docstring). Reached lazily, via the same cross-skill sys.path pattern this file already uses for
# pricebt-spot-checks in `main()` below, so importing tearsheet.py never requires swap_pnl.py's own
# imports to resolve. A tearsheet runs on an ARBITRARY strategy/book, which section 2.7 says can
# carry a much larger *inherent* first-order residual (off-market trades, moneyness) than a
# per-scenario calibration (RS_TARGET_TOY_ROLL/RS_TARGET_ARBS, both 2e-4) assumes -- this generic
# ceiling is deliberately looser than those, but tighter than the plan's ARBS acceptance floor
# (RS_TARGET_ARBS <= 1e-2), so a borderline run is more likely to be FLAGGED than silently passed.
def _swap_pnl_rs_target() -> float:
    """swap_pnl.py's `RS_TARGET`, reached with the same cross-skill sys.path pattern
    spot_check.py's own `_swap_pnl_rs_target()` uses for the same constant. Called lazily (only
    from inside `_blocks`, when a P&L attribution section is actually being rendered) so importing
    tearsheet.py itself never requires swap_pnl.py's own imports to resolve."""
    try:
        import swap_pnl
    except ImportError:
        sys.path.insert(0, str(REPO_ROOT / "skills" / "pricebt-strategy-recipes" / "scripts"))
        import swap_pnl
    return swap_pnl.RS_TARGET


# ------------------------------------------------------------------------------------------ metrics


def _num(v):
    """JSON-safe scalar: dates to ISO strings, numpy to float, NaN/inf to None."""
    if isinstance(v, (dt.date, pd.Timestamp)):
        return v.isoformat()[:10]
    if v is None or isinstance(v, str):
        return v
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f if math.isfinite(f) else None


def _sharpe(pnl: pd.Series, af: int) -> Optional[float]:
    return float(pnl.mean() / pnl.std() * math.sqrt(af)) if len(pnl) > 1 and pnl.std() > 0 else None


def compute_metrics(backtest, risk=None, annualisation_factor: int = 252, in_sample_end=None) -> Dict[str, Any]:
    """summary_stats() plus significance, trade, risk, cost and IS/OOS metrics. Definitions:
    skills/pricebt-tearsheet-report/references/metrics-definitions.md. Values are JSON-safe
    (None where not applicable)."""
    af = annualisation_factor
    m: Dict[str, Any] = {k: _num(v) for k, v in backtest.summary_stats(annualisation_factor=af).items()}
    rs = backtest.result_summary
    daily = rs[backtest.TOTAL_COLUMN].astype(float).diff().dropna()
    n = len(daily)
    m["Observations (days)"] = n
    m["Years"] = n / af
    m["t-stat (mean daily PnL)"] = _num(daily.mean() / (daily.std() / math.sqrt(n))) if n > 1 and daily.std() > 0 else None

    led = backtest.trade_ledger()
    closed = led[led["Status"] == "closed"] if len(led) else led
    pnl = closed["Trade PnL"].astype(float) if len(closed) else pd.Series(dtype=float)
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    m["Closed Trades"] = len(closed)
    m["Open Trades"] = len(led) - len(closed)
    m["Hit Rate (%)"] = _num(len(wins) / len(pnl) * 100) if len(pnl) else None
    m["Average Win"] = _num(wins.mean()) if len(wins) else None
    m["Average Loss"] = _num(losses.mean()) if len(losses) else None
    m["Profit Factor"] = _num(wins.sum() / -losses.sum()) if len(losses) else None
    hold = [(r["Close"] - r["Open"]).days for _, r in closed.iterrows()]
    m["Average Holding Period (days)"] = _num(np.mean(hold)) if hold else None
    cal_years = (rs.index[-1] - rs.index[0]).days / 365.25 if len(rs) else 0
    m["Trades per Year"] = _num(len(led) / cal_years) if cal_years > 0 else None

    total, tc = m.get("Total PnL") or 0.0, m.get("Total Transaction Costs") or 0.0
    gross = total - tc
    m["Gross PnL (before costs)"] = _num(gross)
    m["Cost Drag"] = _num(abs(tc) / abs(gross)) if gross else None

    for k in ("Max Abs Risk", "Mean Abs Risk", "Risk Turnover per Year", "Total PnL (bp)", "PnL / Mean Abs Risk (bp)"):
        m[k] = None
    if risk is not None:
        r = rs[risk].astype(float)
        m["Max Abs Risk"], m["Mean Abs Risk"] = _num(r.abs().max()), _num(r.abs().mean())
        m["Risk Turnover per Year"] = _num(r.diff().abs().sum() / m["Years"]) if m["Years"] else None
        m["Total PnL (bp)"] = _num(backtest.pnl_bps(risk)["Cumulative PnL (bps)"].iloc[-1])
        m["PnL / Mean Abs Risk (bp)"] = _num(total / r.abs().mean()) if r.abs().mean() > 0 else None

    m["In-Sample Sharpe"] = m["Out-of-Sample Sharpe"] = None
    if in_sample_end is not None:
        m["In-Sample Sharpe"] = _num(_sharpe(daily[daily.index <= in_sample_end], af))
        m["Out-of-Sample Sharpe"] = _num(_sharpe(daily[daily.index > in_sample_end], af))
    return m


def evaluate_success(metrics: Dict[str, Any], criteria: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Compare metrics with spec.success_criteria. Status PASS | FAIL | N/A (metric unavailable)."""
    rules = {
        "min_sharpe": ("Sharpe Ratio", ">="),
        "min_oos_sharpe": ("Out-of-Sample Sharpe", ">="),
        "max_drawdown": ("Max Drawdown", "|x| <="),
        "min_trades": ("Total Trades", ">="),
    }
    out = []
    for key, threshold in (criteria or {}).items():
        if threshold is None:
            continue
        metric, op = rules.get(key, (key, "?"))
        value = metrics.get(metric)
        if value is None or op == "?":
            status = "N/A"
        elif op == ">=":
            status = "PASS" if value >= threshold else "FAIL"
        else:
            status = "PASS" if abs(value) <= threshold else "FAIL"
        out.append({"criterion": key, "metric": metric, "rule": f"{op} {threshold}", "value": value, "status": status})
    return out


# ------------------------------------------------------------------------------------------ figures


def _ax(title, figsize=(8, 3.2)):
    fig, ax = plt.subplots(figsize=figsize, facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", color=INK, fontsize=11)
    ax.grid(True, color=GRID, linewidth=0.6)
    ax.tick_params(colors=MUTED, labelsize=8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    return fig, ax


def _ts(s: pd.Series) -> pd.Series:
    s = s.astype(float).copy()
    s.index = pd.to_datetime(s.index)
    return s


def make_figures(backtest, risk=None, signal: Optional[pd.Series] = None,
                 pnl_table: Optional[pd.DataFrame] = None) -> Dict[str, plt.Figure]:
    """Matplotlib figures keyed by name. risk: a scalar result_summary column; signal: a date-indexed
    series (e.g. the par rate or z-score the trigger reads), drawn with the ledger's entry dates.
    pnl_table: swap_pnl.explain_table(bt) output (skipped, no KeyError, when None)."""
    rs = backtest.result_summary
    total = _ts(rs[backtest.TOTAL_COLUMN])
    daily = total.diff().dropna()
    figs: Dict[str, plt.Figure] = {}

    fig, ax = _ax("Cumulative P&L (Total)")
    ax.plot(total.index, total.values, color=SERIES, linewidth=1.8)
    ax.axhline(0, color=MUTED, linewidth=0.8)
    figs["cumulative_pnl"] = fig

    fig, ax = _ax("Drawdown from running peak")
    dd = total - total.cummax()
    ax.fill_between(dd.index, dd.values, 0, color=SERIES, alpha=0.35, linewidth=0)
    ax.plot(dd.index, dd.values, color=SERIES, linewidth=1.2)
    figs["drawdown"] = fig

    fig, ax = _ax("Rolling 63-day Sharpe of daily P&L (annualised)")
    roll = daily.rolling(63, min_periods=63)
    ax.plot(daily.index, (roll.mean() / roll.std() * math.sqrt(252)).values, color=SERIES, linewidth=1.5)
    ax.axhline(0, color=MUTED, linewidth=0.8)
    figs["rolling_sharpe"] = fig

    if risk is not None:
        fig, ax = _ax(f"Risk over time: {risk} (ccy per bp)")
        r = _ts(rs[risk])
        ax.plot(r.index, r.values, color=SERIES, linewidth=1.5)
        ax.axhline(0, color=MUTED, linewidth=0.8)
        figs["risk"] = fig

    if signal is not None:
        fig, ax = _ax("Signal with trade entries")
        sig = _ts(pd.Series(signal))
        ax.plot(sig.index, sig.values, color=SERIES, linewidth=1.5, label="signal")
        led = backtest.trade_ledger()
        if len(led):
            opens = pd.to_datetime(sorted(set(led["Open"])))
            y = sig.reindex(sig.index.union(opens)).ffill().reindex(opens)
            ax.scatter(opens, y.values, color=ACCENT, s=36, zorder=3, edgecolors=SURFACE, linewidths=1.5, label="entry")
        ax.legend(frameon=False, fontsize=8, labelcolor=MUTED)
        figs["signal"] = fig

    monthly = daily.groupby([daily.index.year, daily.index.month]).sum().unstack()
    fig, ax = _ax("Monthly P&L", figsize=(8, 0.6 + 0.5 * max(1, len(monthly))))
    ax.grid(False)
    vmax = float(np.nanmax(np.abs(monthly.values))) or 1.0
    ax.imshow(monthly.values, cmap="RdBu", vmin=-vmax, vmax=vmax, aspect="auto")
    ax.set_xticks(range(len(monthly.columns)), [dt.date(2000, int(c), 1).strftime("%b") for c in monthly.columns])
    ax.set_yticks(range(len(monthly.index)), [str(y) for y in monthly.index])
    for (i, j), v in np.ndenumerate(monthly.values):
        if not np.isnan(v):
            ax.text(j, i, f"{v / 1e3:,.0f}k", ha="center", va="center", fontsize=7,
                    color="white" if abs(v) > 0.6 * vmax else INK)
    figs["monthly_pnl"] = fig

    fig, ax = _ax("Distribution of daily P&L")
    ax.hist(daily.values, bins=min(40, max(5, len(daily) // 5)), color=SERIES, edgecolor=SURFACE, linewidth=1)
    ax.axvline(0, color=MUTED, linewidth=0.8)
    figs["pnl_histogram"] = fig

    if pnl_table is not None:
        # Lines, not a stackplot: PNL_gamma and residual are routinely negative (plan section 2.7),
        # and matplotlib's stackplot baseline convention isn't meaningful for a mixed-sign stack --
        # it would draw a misleading shape rather than an honest one. Plain cumulative lines follow
        # the same "one Figure, ax.plot + legend" convention already used by the signal chart above.
        fig, ax = _ax("Cumulative P&L attribution")
        idx = pd.to_datetime(pnl_table.index)
        cols = ["PNL_delta", "PNL_gamma", "PNL_carry", "residual"]
        palette = [SERIES, ACCENT, "#4f9d69", "#8a5fbf"]  # extends the file's SERIES/ACCENT pair to 4 series
        for col, color in zip(cols, palette):
            ax.plot(idx, pnl_table[col].astype(float).cumsum().values, color=color, linewidth=1.5, label=col)
        ax.axhline(0, color=MUTED, linewidth=0.8)
        ax.legend(frameon=False, fontsize=8, labelcolor=MUTED, loc="upper left")
        figs["pnl_attribution"] = fig

    for f in figs.values():
        f.tight_layout()
    return figs


# ------------------------------------------------------------------------------------------ report


def _flatten(d, prefix=""):
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict) and v:
            yield from _flatten(v, key + ".")
        elif isinstance(v, list):
            yield key, ", ".join(str(x) for x in v) if v else "[]"
        else:
            yield key, "" if v is None else str(v).strip()


def _fmt(v):
    if isinstance(v, float):
        return f"{v:,.4g}" if abs(v) < 1e4 else f"{v:,.0f}"
    return "" if v is None else str(v)


def _frame(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True,
                              timeout=10, check=True).stdout.strip()
    except Exception:
        return "unavailable"


def _blocks(backtest, title, spec, metrics, criteria, risk, spot_checks, review_findings, caveats, notes, generated_at,
           pnl_table, pnl_stats):
    """The report as a list of (kind, payload) blocks; rendered to both HTML and Markdown."""
    B: List[tuple] = [("h1", title)]
    n_pass = sum(c["status"] == "PASS" for c in criteria)
    failed = [c["criterion"] for c in criteria if c["status"] != "PASS"]
    if criteria:
        verdict = (f"PASS: meets all {len(criteria)} success criteria." if not failed
                   else f"FAIL: {n_pass}/{len(criteria)} success criteria pass; missed: {', '.join(failed)}.")
    else:
        verdict = "NO CRITERIA: the spec has no success_criteria, so there is no pass/fail verdict."
    B += [("h2", "Verdict"), ("p", verdict),
          ("p", notes or "[Agent: replace with the one-paragraph verdict — see "
                         "skills/pricebt-tearsheet-report/references/report-template.md]")]
    if criteria:
        B.append(("table", _frame(criteria)))

    B.append(("h2", "Strategy spec"))
    if spec:
        B.append(("table", pd.DataFrame([{"field": k, "value": v} for k, v in _flatten(spec) if k != "assumptions"])))
        B.append(("h3", "Assumptions (defaults relied on)"))
        B.append(("list", spec.get("assumptions") or ["none recorded"]))
    else:
        B.append(("p", "No spec supplied."))

    B.append(("h2", "Key metrics"))
    B.append(("table", pd.DataFrame([{"metric": k, "value": _fmt(v)} for k, v in metrics.items()])))
    daily = backtest.result_summary[backtest.TOTAL_COLUMN].astype(float).diff().dropna()
    worst = daily.nsmallest(5)
    B += [("h3", "Worst five days"), ("table", pd.DataFrame({"date": [str(d) for d in worst.index], "P&L": worst.values}))]

    B.append(("h2", "Charts"))
    B += [("img", name) for name in ("cumulative_pnl", "drawdown", "rolling_sharpe", "signal", "monthly_pnl", "pnl_histogram")]

    B.append(("h2", "Trade ledger"))
    led = backtest.trade_ledger()
    if len(led) > 40:
        B += [("p", f"{len(led)} trades; first and last 20 shown (all in trades.csv)."),
              ("table", led.head(20).reset_index(names="trade")), ("table", led.tail(20).reset_index(names="trade"))]
    else:
        B += [("p", f"{len(led)} trades."), ("table", led.reset_index(names="trade") if len(led) else pd.DataFrame())]

    B.append(("h2", "Risk and P&L in bp"))
    if risk is not None:
        pb = backtest.pnl_bps(risk)["PnL (bps)"].dropna()
        B += [("p", f"Risk measure {risk} (currency per bp). P&L in bp = daily P&L / previous day's risk."),
              ("table", pd.DataFrame([
                  {"metric": "Max Abs Risk", "value": _fmt(metrics["Max Abs Risk"])},
                  {"metric": "Mean Abs Risk", "value": _fmt(metrics["Mean Abs Risk"])},
                  {"metric": "Total PnL (bp)", "value": _fmt(metrics["Total PnL (bp)"])},
                  {"metric": "Mean daily PnL (bp)", "value": _fmt(float(pb.mean()) if len(pb) else None)},
                  {"metric": "Worst day (bp)", "value": _fmt(float(pb.min()) if len(pb) else None)},
              ])), ("img", "risk")]
    else:
        B.append(("p", "No risk measure supplied (pass risk=IRDeltaParallel or similar)."))

    B.append(("h2", "P&L attribution"))
    if pnl_stats is None:
        B.append(("p", "P&L explain not enabled for this run (spec pnl_explain.enabled: false, or "
                       "primary is not an IRSwap). See docs/v2/PNL_EXPLAIN_PLAN.md."))
    else:
        totals = pnl_stats["totals"]
        B.append(("table", pd.DataFrame([{"component": c, "total": _fmt(totals.get(c))}
                                         for c in ("PNL_delta", "PNL_gamma", "PNL_carry", "residual", "economic")])))
        if pnl_table is not None:
            B.append(("img", "pnl_attribution"))
        rs, r2, target = pnl_stats["residual_share"], pnl_stats["r2"], _swap_pnl_rs_target()
        badge = "  ** WARN: residual share above target **" if rs > target else ""
        B.append(("p", f"Residual share: {rs:.4g} (target <= {target:.4g}). R2 (explained vs economic): "
                       f"{r2:.4g}.{badge}"))

    B.append(("h2", "Spot checks"))
    if spot_checks:
        B.append(("table", pd.DataFrame([{"check": r[0], "status": r[1], "detail": r[2]} for r in spot_checks])))
    else:
        B.append(("p", "NOT RUN. Run skills/pricebt-spot-checks before trusting any number above."))

    B.append(("h2", "Adversarial review findings"))
    if review_findings and all(isinstance(f, dict) for f in review_findings):
        B.append(("table", pd.DataFrame(review_findings)))
    else:
        B.append(("list", review_findings or ["NOT RUN."]))

    B += [("h2", "Caveats and known limitations"), ("list", PRICEBT_CAVEATS + list(caveats or []))]

    assets = (spec or {}).get("assets") or _session_assets()
    B += [("h2", "Reproducibility"), ("list", [
        f"Asset configs: {', '.join(map(str, assets)) or 'unknown'}",
        f"pricebt git commit: {_git_commit()}",
        f"Backtest window: {backtest.result_summary.index[0]} to {backtest.result_summary.index[-1]}",
        "Spec: reproduced in the Strategy spec section above; metrics.json, trades.csv and summary.csv sit next to this report.",
        f"Generated at: {generated_at}",
    ])]
    return B


def _session_assets():
    from pricebt.session import PricebtSession

    return PricebtSession.current.registry.names() if PricebtSession.current else []


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, facecolor=SURFACE, metadata={"Software": None})
    return buf.getvalue()


CSS = """
:root{--bg:#fcfcfb;--ink:#0b0b0b;--muted:#52514e;--rule:#e4e3df;--pass:#008300;--fail:#c0392b}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#1a1a19;--ink:#fff;--muted:#c3c2b7;--rule:#3a3a38}}
:root[data-theme="dark"]{--bg:#1a1a19;--ink:#fff;--muted:#c3c2b7;--rule:#3a3a38}
body{background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;max-width:980px;margin:0 auto;padding:24px 16px}
h1{font-size:26px;margin:0 0 8px}h2{font-size:19px;margin:32px 0 8px;border-bottom:1px solid var(--rule);padding-bottom:4px}h3{font-size:15px;color:var(--muted)}
table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0;display:block;overflow-x:auto}
th,td{text-align:left;padding:4px 10px;border-bottom:1px solid var(--rule);vertical-align:top}th{color:var(--muted);font-weight:600;white-space:nowrap}
td.PASS{color:var(--pass);font-weight:600}td.FAIL{color:var(--fail);font-weight:600}
img{max-width:100%;height:auto;margin:8px 0;border-radius:4px}
"""


def _html_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "<p>(empty)</p>"
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in df.columns)
    rows = []
    for _, r in df.iterrows():
        cells = "".join(
            f'<td class="{html.escape(str(v))}">{html.escape(_fmt(v))}</td>' if str(v) in ("PASS", "FAIL")
            else f"<td>{html.escape(_fmt(v))}</td>" for v in r.values)
        rows.append(f"<tr>{cells}</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def _md_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "(empty)"
    esc = lambda v: _fmt(v).replace("|", "/").replace("\n", " ")  # noqa: E731
    lines = ["| " + " | ".join(map(str, df.columns)) + " |", "|" + "---|" * len(df.columns)]
    lines += ["| " + " | ".join(esc(v) for v in r.values) + " |" for _, r in df.iterrows()]
    return "\n".join(lines)


def _add_attribution(blocks: List[tuple], pngs: Dict[str, bytes], table: Optional[pd.DataFrame]) -> None:
    """Optional "P&L attribution by greek" section, inserted before "Spot checks" when build_tearsheet gets
    attribution=backtest.pnl_explain_table() (skills/pricebt-pnl-attribution); a no-op otherwise.
    Component totals, the residual statistics graded by attribution.grade (grade_reason says why), and a
    cumulative chart stacked above and below zero with the economic P&L as a line."""
    if table is None:
        return
    path = str(REPO_ROOT / "skills/pricebt-pnl-attribution/scripts")
    if path not in sys.path:
        sys.path.insert(0, path)
    import attribution as att

    stats = att.explain_stats(table)
    status, totals = att.grade(stats), stats["totals"]
    attrs = [c for c in table.columns if c not in att.FIXED]
    econ = totals["economic_pnl"]
    components = [*attrs, "explained_pnl", "residual_pnl", "actual_pnl", "cashflow_pnl", "economic_pnl"]
    share = lambda v: v / econ if v is not None and econ else None  # noqa: E731
    section = [
        ("h2", "P&L attribution by greek"),
        ("p", f"Greeks x market moves per step (backtest.pnl_explain_table(), {stats['steps']} steps). "
              "economic = actual (sum of the held book's price change) + cashflow (coupons paid); "
              "residual = economic - explained."),
        ("table", pd.DataFrame([{"component": c, "total": totals[c], "share of economic P&L": share(totals[c])} for c in components])),
        ("table", pd.DataFrame([
            {"metric": "unexplained share (worst of the three below)", "value": stats["unexplained"], "status": status},
            {"metric": "residual variance share var(residual)/var(economic)", "value": stats["residual_share"], "status": ""},
            {"metric": "r2 (1 - SS residual / SS economic)", "value": stats["r2"], "status": ""},
            {"metric": "sum abs(residual) / sum abs(economic)","value": stats["abs_residual_ratio"], "status": ""},
            {"metric": "worst residual (date)", "value": f"{_fmt(stats['worst_residual'])} ({stats['worst_date']})", "status": ""},
        ])),
    ]
    if status in ("WARN", "FAIL"):
        why = att.grade_reason(stats)
        section.append(("p", f"{status}: {why}. "
                             "Diagnose with skills/pricebt-pnl-attribution/references/diagnosing-residuals.md "
                             "before reading the components."))
    if stats["finite"]:
        cum = table[attrs + ["residual_pnl"]].astype(float).cumsum()
        cum.index = pd.to_datetime(cum.index)
        colors = [SERIES, ACCENT, *plt.get_cmap("tab10").colors[2:]][: len(attrs)] + [MUTED]
        fig, ax = _ax("Cumulative P&L attribution (stacked above/below zero; line = economic P&L)")
        ax.stackplot(cum.index, cum.clip(lower=0).T.values, colors=colors, labels=list(cum.columns), alpha=0.85)
        ax.stackplot(cum.index, cum.clip(upper=0).T.values, colors=colors, alpha=0.85)
        ax.plot(cum.index, table["economic_pnl"].astype(float).cumsum().values, color=INK, linewidth=1.6, label="economic P&L")
        ax.axhline(0, color=MUTED, linewidth=0.8)
        ax.legend(frameon=False, fontsize=7, ncol=4, labelcolor=MUTED)
        fig.tight_layout()
        pngs["greek_attribution"] = _png(fig)
        plt.close(fig)
        section.append(("img", "greek_attribution"))
    i = blocks.index(("h2", "Spot checks"))
    blocks[i:i] = section


def build_tearsheet(backtest, out_dir, title, spec=None, risk=None, signal=None, review_findings=None,
                    spot_checks=None, caveats=None, notes=None, attribution=None, pnl_table=None,
                    pnl_stats=None) -> Dict[str, str]:
    """Write tearsheet.html (self-contained), tearsheet.md (+ PNGs), metrics.json, trades.csv and
    summary.csv into out_dir. Returns {artefact: path}.

    attribution: an optional backtest.pnl_explain_table() (skills/pricebt-pnl-attribution), rendered
    as a "P&L attribution by greek" section.
    pnl_table/pnl_stats: swap_pnl.explain_table(bt) / swap_pnl.explain_stats(table) (PNL_EXPLAIN_PLAN.md
    section 7). Both None (the default) renders a "not enabled" P&L attribution section instead."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    in_sample_end = ((spec or {}).get("dates") or {}).get("in_sample_end")
    metrics = compute_metrics(backtest, risk=risk, in_sample_end=in_sample_end)
    criteria = evaluate_success(metrics, (spec or {}).get("success_criteria"))
    figs = make_figures(backtest, risk=risk, signal=signal, pnl_table=pnl_table)
    pngs = {name: _png(f) for name, f in figs.items()}
    for f in figs.values():
        plt.close(f)
    generated_at = dt.datetime.now().isoformat(timespec="seconds")
    blocks = _blocks(backtest, title, spec, metrics, criteria, risk, spot_checks, review_findings, caveats, notes,
                     generated_at, pnl_table, pnl_stats)
    _add_attribution(blocks, pngs, attribution)

    h, md = [], []
    for kind, payload in blocks:
        if kind in ("h1", "h2", "h3"):
            h.append(f"<{kind}>{html.escape(payload)}</{kind}>")
            md.append("#" * int(kind[1]) + " " + payload)
        elif kind == "p":
            h.append(f"<p>{html.escape(payload)}</p>")
            md.append(payload)
        elif kind == "list":
            h.append("<ul>" + "".join(f"<li>{html.escape(str(x))}</li>" for x in payload) + "</ul>")
            md.append("\n".join(f"- {x}" for x in payload))
        elif kind == "table":
            h.append(_html_table(payload))
            md.append(_md_table(payload))
        elif kind == "img" and payload in pngs:
            b64 = base64.b64encode(pngs[payload]).decode("ascii")
            h.append(f'<img alt="{payload}" src="data:image/png;base64,{b64}">')
            (out / f"{payload}.png").write_bytes(pngs[payload])
            md.append(f"![{payload}]({payload}.png)")

    paths = {k: str(out / f) for k, f in (("html", "tearsheet.html"), ("md", "tearsheet.md"), ("metrics", "metrics.json"),
                                          ("trades", "trades.csv"), ("summary", "summary.csv"))}
    doc = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
           f'content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title><style>{CSS}</style></head>'
           f"<body>{''.join(h)}</body></html>")
    Path(paths["html"]).write_text(doc, encoding="utf-8")
    Path(paths["md"]).write_text("\n\n".join(md) + "\n", encoding="utf-8")
    Path(paths["metrics"]).write_text(json.dumps({"metrics": metrics, "success_criteria": criteria}, indent=2), encoding="utf-8")
    backtest.trade_ledger().to_csv(paths["trades"])
    summary = backtest.result_summary.copy()
    summary.columns = [str(c) for c in summary.columns]
    summary.to_csv(paths["summary"])
    return paths


# ------------------------------------------------------------------------------------------ demo


def demo_backtest(start=dt.date(2024, 1, 2), end=dt.date(2025, 1, 2)):
    """Toy USD 10y mean-reversion strategy (the 040304 notebook's rule, with dv01-scaled costs).
    Returns (backtest, spec, signal, rerun)."""
    import yaml

    from pricebt.backtests.actions import AddTradeAction
    from pricebt.backtests.backtest_objects import ScaledTransactionModel
    from pricebt.backtests.data_sources import GenericDataSource, MissingDataStrategy
    from pricebt.backtests.generic_engine import GenericEngine
    from pricebt.backtests.strategy import Strategy
    from pricebt.backtests.triggers import MeanReversionTrigger, MeanReversionTriggerRequirements
    from pricebt.data import measure_series
    from pricebt.instrument import IRSwap
    from pricebt.risk import IRDeltaParallel, Price
    from pricebt.session import PricebtSession

    spec = yaml.safe_load((REPO_ROOT / "skills/pricebt-strategy-intake/templates/strategy_spec.yaml").read_text(encoding="utf-8"))
    spec["name"] = "toy-usd-10y-mean-reversion"
    spec["dates"].update(start=start, end=end, in_sample_end=dt.date(2024, 9, 30))
    spec["assumptions"] = ["toy rates world (tests/toylib/rates.py), not market data", "costs 0.25bp of dv01 per side"]
    PricebtSession.use(assets=[str(REPO_ROOT / "tests/assets/toy_usd_irs.yaml")])
    signal = measure_series(IRSwap(termination_date="10y", notional_currency="USD"), "par_rate", start, end, frequency="1b")

    def run():
        swap = IRSwap(pay_or_receive="Pay", termination_date="10y", notional_currency="USD",
                      notional_amount=1e7, fixed_rate="ATM", name="swap_10y")
        action = AddTradeAction(swap, transaction_cost=ScaledTransactionModel(IRDeltaParallel, 0.25))
        req = MeanReversionTriggerRequirements(GenericDataSource(signal, MissingDataStrategy.fill_forward), 2, 30, 30)
        return GenericEngine().run_backtest(Strategy(None, MeanReversionTrigger(req, action)), start=start, end=end,
                                            frequency="1b", risks=[Price, IRDeltaParallel], show_progress=False)

    return run(), spec, signal, run


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--demo", metavar="OUT_DIR", help="run the toy mean-reversion backtest and write its tearsheet")
    args = ap.parse_args(argv)
    if not args.demo:
        ap.print_help()
        return None
    from pricebt.risk import IRDeltaParallel

    sys.path.insert(0, str(REPO_ROOT / "skills/pricebt-spot-checks/scripts"))
    import spot_check

    bt, spec, signal, rerun = demo_backtest()
    checks = spot_check.run_spot_checks(bt, rerun=rerun, risk=IRDeltaParallel, rate_measure=signal)
    paths = build_tearsheet(bt, args.demo, "Toy USD 10y mean reversion", spec=spec, risk=IRDeltaParallel, signal=signal,
                            spot_checks=checks, caveats=["Toy rates world: numbers illustrate the report, not a strategy."])
    for k, p in paths.items():
        print(f"{k}: {p}")
    return paths


if __name__ == "__main__":
    main()
