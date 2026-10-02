"""One-command driver for the automated parts of a strategy study (skills/pricebt-strategy-workflow).

    $env:PYTHONPATH = "src;tests"
    python skills/pricebt-strategy-workflow/scripts/run_study.py reports/<name>/strategy_spec.yaml [--out DIR] [--no-robustness]

Stages (outputs go to spec.deliverables.out_dir, or --out):
  1. defaults + validation         -> strategy_spec.yaml (filled, with `assumptions:`)
  2. backtest via the recipes      -> trials.csv (one row appended per run: the trial log)
  3. robustness experiments        -> robustness.md
  4. spot checks                   -> spot_checks.md
  5. significance                  -> significance.json
  6. review stub + tearsheet       -> review.md (automated sections; the checklist walk and the verdict
                                      remain the agent's job), tearsheet.html/.md, metrics.json, trades.csv
Exit code: 0 when no spot check or experiment FAILs, 1 otherwise, 2 for an invalid spec.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

_SKILLS = Path(__file__).resolve().parents[2]
for _sub in ("pricebt-strategy-intake", "pricebt-strategy-recipes", "pricebt-adversarial-review",
             "pricebt-spot-checks", "pricebt-tearsheet-report", "pricebt-research-methodology"):
    _p = str(_SKILLS / _sub / "scripts")
    if _p not in sys.path:
        sys.path.insert(0, _p)

import recipes  # noqa: E402
import research_stats  # noqa: E402
import robustness  # noqa: E402
import spec as specmod  # noqa: E402
import spot_check  # noqa: E402
import swap_pnl  # noqa: E402 -- PNL_EXPLAIN_PLAN.md section 7; pricebt-strategy-recipes/scripts is
                  # already on sys.path from the loop above (recipes.py lives there too)
import tearsheet  # noqa: E402

STANDARD_CAVEATS_BY_ARCHETYPE = {
    "periodic_roll": "Carry strategy: coupons paid between marks are not booked as cash (gs parity), so realised carry is understated for positions held across coupon dates.",
    "mean_reversion": "gs mean-reversion exits are offsetting trades held forever: gross notional grows with every signal; judge net risk, not gross.",
    "curve_trade": "Legs are dv01-neutral at entry only; neutrality drifts until the next rebalance.",
}


def _report_risk(bt) -> Optional[Any]:
    from pricebt.risk import IRDelta, IRDeltaParallel

    cols = list(bt.result_summary.columns)
    for cand in (IRDeltaParallel, IRDelta(aggregation_level="Type")):
        if cand in cols:
            return cand
    ccy_cols = [c for c in cols if getattr(c, "name", None) in ("IRDeltaParallel", "IRDelta") and getattr(c, "aggregation_level", None) is not None]
    return ccy_cols[0] if ccy_cols else None


_BP_PER_UNIT = {"bp": 1.0, "pct": 100.0, "decimal": 1e4}


def _rate_series(spec: dict):
    """A bp rate series for the P&L-explain check: the signal instrument's own rate (IRFwdRate: swap
    par rate, swaption forward, bond yield), else its `par_rate` function, converted from the declared unit."""
    from pricebt.data import measure_series
    from pricebt.risk import IRFwdRate
    import pricebt.instrument as inst_mod

    for measure in (IRFwdRate, "par_rate"):
        try:
            entry = spec["instruments"][(spec.get("signal") or {}).get("instrument") or "primary"]
            fresh = getattr(inst_mod, entry["class"])(**(entry.get("kwargs") or {}))
            s = measure_series(fresh, measure, spec["dates"]["start"], spec["dates"]["end"], frequency=spec["dates"]["frequency"])
        except Exception:
            continue
        if s.attrs.get("unit") in _BP_PER_UNIT:
            return s * _BP_PER_UNIT[s.attrs["unit"]]
    return None


def _append_trial(path: Path, spec: dict, metrics: Dict[str, Any]) -> int:
    new = not path.exists()
    row = {
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "archetype": spec["archetype"],
        "signal": json.dumps(spec.get("signal"), default=str),
        "sizing": json.dumps(spec.get("sizing"), default=str),
        "costs": json.dumps(spec.get("costs"), default=str),
        "dates": json.dumps(spec.get("dates"), default=str),
        "sharpe": metrics.get("Sharpe Ratio"),
        "is_sharpe": metrics.get("In-Sample Sharpe"),
        "oos_sharpe": metrics.get("Out-of-Sample Sharpe"),
        "total_pnl": metrics.get("Total PnL"),
    }
    with path.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)
    with path.open(encoding="utf-8") as fh:
        return sum(1 for _ in fh) - 1


def _review_stub(spec, sig, robust, spots, notes) -> str:
    lines = [
        f"# Review: {spec['name']}", "",
        "- **Verdict:** PENDING - walk skills/pricebt-adversarial-review/references/checklist.md and fill in the findings.",
        f"- **Backtest:** {spec['dates']['start']}..{spec['dates']['end']}, grid {spec['dates']['frequency']}, trials so far {sig['n_trials']}", "",
        "## Findings", "", "| ID | Severity | Finding | Evidence | Fix / disposition |", "|---|---|---|---|---|", "| R1 | | | | |", "",
        "## Robustness experiments (automated)", "", robustness.to_markdown(robust) if robust else "not run", "",
        "## Significance (automated)", "",
        f"Sharpe {sig['sharpe']:.3g}, t {sig['t_stat']:.3g}, SE {sig['se_sharpe']:.3g}, {sig['years']:.2f} years, "
        f"{sig['n_trials']} trials, Bonferroni z {sig['bonferroni_z']:.2f}, significant after trials: {sig['significant_after_trials']}", "",
        "## Spot checks (automated)", "", spot_check.to_markdown(spots), "",
        "## Implementation notes (recipes)", "", *[f"- {n}" for n in notes], "",
        "## Assumptions (defaults relied on)", "", *[f"- {a}" for a in spec.get("assumptions") or ["none"]], "",
    ]
    return "\n".join(lines)


def red_flags(sig: Dict[str, Any], metrics: Dict[str, Any], spec: dict) -> list:
    """Automatic red flags from the research standard (skills/pricebt-research-methodology)."""
    flags = []
    sr = sig.get("sharpe")
    if sr is not None and sr == sr and abs(sr) > 2:
        flags.append({"experiment": "red flag: implausible Sharpe", "status": "WARN",
                      "result": f"|Sharpe| {sr:.3g} > 2: treat as a bug or a data artefact until explained (Grinold & Kahn ch. 12)"})
    trades = metrics.get("Total Trades") or 0
    floor = (spec.get("success_criteria") or {}).get("min_trades") or 0
    if trades < floor:
        flags.append({"experiment": "red flag: too few trades", "status": "WARN",
                      "result": f"{int(trades)} trades < min_trades {floor}: statistics are not meaningful"})
    if not sig.get("significant_after_trials", False):
        flags.append({"experiment": "significance", "status": "INFO",
                      "result": f"t {sig.get('t_stat', float('nan')):.3g} is not significant after {sig.get('n_trials')} trial(s) (Bonferroni z {sig.get('bonferroni_z', float('nan')):.2f})"})
    return flags


def run_study(spec_path, out: Optional[str] = None, with_robustness: bool = True) -> Dict[str, Any]:
    spec, _ = specmod.apply_defaults(spec_path)
    errors = specmod.validate_spec(spec)
    if errors:
        raise ValueError("invalid spec:\n  " + "\n  ".join(errors))
    out_dir = Path(out or spec["deliverables"]["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    specmod.dump_spec(spec, out_dir / "strategy_spec.yaml")

    bt, built = recipes.run(spec)

    # P&L explain (PNL_EXPLAIN_PLAN.md section 7): bt.pnl_explain_def is only set when recipes.build()
    # wired one in (spec pnl_explain.enabled and an IRSwap primary -- see recipes.py). cash= mirrors
    # what was actually requested/computed: explain_table() would report an all-zero cash column
    # either way, but this makes the intent visible rather than silently relying on that fallback.
    if bt.pnl_explain_def is not None:
        pnl_table = swap_pnl.explain_table(bt, cash=swap_pnl.CashPaidToDate in bt.risks)
        pnl_stats = swap_pnl.explain_stats(pnl_table)
        pnl_table.to_csv(out_dir / "pnl_explain.csv")
        (out_dir / "pnl_explain.json").write_text(json.dumps(pnl_stats, indent=2, default=str), encoding="utf-8")
    else:
        pnl_table = pnl_stats = None

    from pricebt.session import PricebtSession

    session = PricebtSession.current
    risk = _report_risk(bt)
    metrics = tearsheet.compute_metrics(bt, risk=risk, in_sample_end=spec["dates"].get("in_sample_end"))
    n_trials = _append_trial(out_dir / "trials.csv", spec, metrics)

    robust = robustness.run_all(spec, base=bt) if with_robustness else []
    # robustness re-runs open new sessions: restore the base run's session for repricing checks
    PricebtSession.current = session
    (out_dir / "robustness.md").write_text(robustness.to_markdown(robust, tables=True) if robust else "not run\n", encoding="utf-8")

    spots = spot_check.run_spot_checks(bt, session=session, rerun=lambda: recipes.run(spec)[0], risk=risk,
                                       rate_measure=_rate_series(spec), pnl_stats=pnl_stats)
    PricebtSession.current = session
    (out_dir / "spot_checks.md").write_text(spot_check.to_markdown(spots) + "\n", encoding="utf-8")

    daily = bt.result_summary[bt.TOTAL_COLUMN].astype(float).diff().dropna()
    sig = research_stats.sharpe_summary(daily, n_trials=n_trials)
    (out_dir / "significance.json").write_text(json.dumps(sig, indent=2, default=str), encoding="utf-8")

    (out_dir / "review.md").write_text(_review_stub(spec, sig, robust, spots, built.notes), encoding="utf-8")
    findings = [{"experiment": r.name, "status": r.status, "result": r.detail} for r in robust] or ["Robustness not run."]
    findings += red_flags(sig, metrics, spec)
    findings.append({"experiment": "adversarial review (checklist)", "status": "PENDING", "result": "see review.md; replace this row with the findings table"})
    caveats = [c for a, c in STANDARD_CAVEATS_BY_ARCHETYPE.items() if a == spec["archetype"]] + [f"Assumed: {a}" for a in spec.get("assumptions") or []][:12]
    paths = tearsheet.build_tearsheet(bt, out_dir, spec["name"], spec=spec, risk=risk, signal=built.signal,
                                      review_findings=findings, spot_checks=spots, caveats=caveats,
                                      pnl_table=pnl_table, pnl_stats=pnl_stats)
    if pnl_table is not None:
        paths["pnl_explain"] = str(out_dir / "pnl_explain.csv")
        paths["pnl_stats"] = str(out_dir / "pnl_explain.json")
    failed = [r.name for r in spots if r.status == "FAIL"] + [r.name for r in robust if r.status == "FAIL"]
    return {"out_dir": str(out_dir), "paths": paths, "failed": failed, "significance": sig, "metrics": metrics, "backtest": bt}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the automated stages of a pricebt strategy study.")
    ap.add_argument("spec")
    ap.add_argument("--out")
    ap.add_argument("--no-robustness", action="store_true")
    a = ap.parse_args(argv)
    try:
        res = run_study(a.spec, a.out, not a.no_robustness)
    except ValueError as e:
        print(e)
        return 2
    m, s = res["metrics"], res["significance"]
    print(f"{res['out_dir']}: Sharpe {s['sharpe']:.3g} (t {s['t_stat']:.3g}, {s['years']:.2f}y, {s['n_trials']} trials), "
          f"total P&L {m.get('Total PnL')}, trades {m.get('Total Trades')}")
    print("FAILED checks: " + (", ".join(res["failed"]) if res["failed"] else "none"))
    print(f"tearsheet: {res['paths']['html']}  |  review stub: {Path(res['out_dir']) / 'review.md'}")
    return 1 if res["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
