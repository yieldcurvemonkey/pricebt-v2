"""Automated robustness experiments for a spec-built pricebt strategy (skills/pricebt-adversarial-review).

    import sys; sys.path.insert(0, "skills/pricebt-adversarial-review/scripts")
    import robustness
    results = robustness.run_all("reports/<name>/strategy_spec.yaml")
    print(robustness.to_markdown(results))

Each experiment re-runs the backtest through the recipes skill (recipes.run) on a deep copy of the spec
with ONE change, and compares it with the base run. What each experiment detects and how to read it:
skills/pricebt-adversarial-review/references/experiments.md. Every experiment is a trial: report
them as robustness checks, never adopt a parameter because a sweep liked it.
"""
from __future__ import annotations

import copy
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Union

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve()
_SKILLS = _HERE.parents[2]
for _p in (_SKILLS / "pricebt-strategy-recipes" / "scripts", _SKILLS / "pricebt-strategy-intake" / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import recipes  # noqa: E402
import spec as specmod  # noqa: E402

PASS, WARN, FAIL, NA = "PASS", "WARN", "FAIL", "N/A"
SIGNAL_ARCHETYPES = ("mean_reversion", "momentum")
__all__ = ["ExperimentResult", "run_all", "to_markdown", "EXPERIMENTS"]


@dataclass
class ExperimentResult:
    name: str
    status: str
    detail: str
    table: Optional[pd.DataFrame] = field(default=None, repr=False)


def _daily(bt) -> pd.Series:
    return bt.result_summary[bt.TOTAL_COLUMN].astype(float).diff().dropna()


def _sharpe(pnl: pd.Series, af: int = 252) -> float:
    pnl = pnl.dropna()
    sd = pnl.std(ddof=1)
    return float(math.sqrt(af) * pnl.mean() / sd) if len(pnl) > 1 and sd > 0 else float("nan")


def _stats(bt) -> Dict[str, float]:
    d = _daily(bt)
    total = float(bt.result_summary[bt.TOTAL_COLUMN].iloc[-1]) if len(bt.result_summary) else 0.0
    return {"sharpe": _sharpe(d), "total_pnl": total, "trades": len(bt.trade_ledger())}


def _run(spec: dict):
    bt, _ = recipes.run(copy.deepcopy(spec))
    return bt


def _fmt(x: float) -> str:
    return "nan" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:,.3g}"


# ----------------------------------------------------------------------------------- experiments
def cost_ladder(spec: dict, base, multipliers: Sequence[float] = (0, 1, 2, 3)) -> ExperimentResult:
    costs = spec.get("costs") or {}
    level = float(costs.get("level") or 0.0)
    if costs.get("model", "none") == "none" or level == 0:
        return ExperimentResult("cost_ladder", WARN, "costs are off in the spec (model none or level 0): tradability untested")
    rows = []
    for m in multipliers:
        s = copy.deepcopy(spec)
        s["costs"]["level"] = level * m
        st = _stats(base) if m == 1 else _stats(_run(s))
        rows.append({"multiplier": m, "level": level * m, **st})
    t = pd.DataFrame(rows)
    # break-even multiplier: where total P&L crosses zero (linear interpolation on the ladder)
    be = None
    for (m0, p0), (m1, p1) in zip(t[["multiplier", "total_pnl"]].values[:-1], t[["multiplier", "total_pnl"]].values[1:]):
        if p0 > 0 >= p1:
            be = m0 + (m1 - m0) * p0 / (p0 - p1)
            break
    gross = t.loc[t.multiplier == 0, "total_pnl"].iloc[0]
    at1 = t.loc[t.multiplier == 1, "total_pnl"].iloc[0]
    if gross <= 0:
        return ExperimentResult("cost_ladder", WARN, f"no edge even at zero cost (P&L {_fmt(gross)})", t)
    detail = f"P&L at 0x {_fmt(gross)}, 1x {_fmt(at1)}; break-even at {_fmt(be * level) + ' ' + costs.get('model', '') if be else '> 3x'} cost"
    status = PASS if at1 > 0 and (be is None or be > 2) else WARN
    return ExperimentResult("cost_ladder", status, detail, t)


def parameter_sweep(spec: dict, base, multipliers: Sequence[float] = (0.5, 0.75, 1.25, 1.5)) -> ExperimentResult:
    if spec.get("archetype") not in SIGNAL_ARCHETYPES:
        return ExperimentResult("parameter_sweep", NA, f"archetype {spec.get('archetype')} has no signal parameters")
    base_sr = _stats(base)["sharpe"]
    rows = []
    targets = [("signal.lookback", None)] + [(f"signal.params.{k}", k) for k, v in (spec["signal"].get("params") or {}).items()
                                              if isinstance(v, (int, float)) and not isinstance(v, bool) and v != 0]
    for path, key in targets:
        for m in multipliers:
            s = copy.deepcopy(spec)
            if key is None:
                s["signal"]["lookback"] = max(3, int(round(spec["signal"]["lookback"] * m)))
                value = s["signal"]["lookback"]
            else:
                value = spec["signal"]["params"][key] * m
                s["signal"]["params"][key] = value
            try:
                st = _stats(_run(s))
            except Exception as e:  # an invalid neighbour is itself information
                st = {"sharpe": float("nan"), "total_pnl": float("nan"), "trades": 0, "error": f"{type(e).__name__}: {e}"}
            rows.append({"parameter": path, "multiplier": m, "value": value, **st})
    t = pd.DataFrame(rows)
    if not np.isfinite(base_sr):
        return ExperimentResult("parameter_sweep", WARN, "base Sharpe undefined (no P&L variation)", t)
    ok = t["sharpe"].apply(lambda x: np.isfinite(x) and np.sign(x) == np.sign(base_sr) and abs(x) >= 0.5 * abs(base_sr))
    share = float(ok.mean()) if len(ok) else 0.0
    status = PASS if share >= 0.75 and base_sr > 0 else WARN
    if base_sr <= 0:
        return ExperimentResult("parameter_sweep", WARN, f"base strategy loses (Sharpe {_fmt(base_sr)}); {share:.0%} of neighbours are similar: no parameter region works", t)
    return ExperimentResult("parameter_sweep", status, f"{share:.0%} of {len(t)} neighbours keep the sign and >= half of base Sharpe {_fmt(base_sr)} ({'plateau' if status == PASS else 'spike: likely fitted'})", t)


def truncation(spec: dict, base, drop_business_days: int = 60) -> ExperimentResult:
    start, end = spec["dates"]["start"], spec["dates"]["end"]
    days = pd.bdate_range(start, end)
    if len(days) <= drop_business_days + 20:
        return ExperimentResult("truncation", NA, "backtest too short to truncate")
    new_end = days[-drop_business_days - 1].date()
    s = copy.deepcopy(spec)
    s["dates"]["end"] = new_end
    if s["dates"].get("in_sample_end") and s["dates"]["in_sample_end"] > new_end:
        s["dates"]["in_sample_end"] = None
    short = _run(s)
    a = base.trade_ledger()
    b = short.trade_ledger()
    a = a[[o <= new_end for o in a["Open"]]] if len(a) else a
    diffs = []
    for name in sorted(set(a.index) | set(b.index)):
        if name not in b.index or name not in a.index:
            diffs.append(f"{name}: present in only one run")
            continue
        for col in ("Open", "Open Value"):
            va, vb = a.loc[name, col], b.loc[name, col]
            same = math.isclose(float(va), float(vb), rel_tol=1e-9, abs_tol=1e-6) if col == "Open Value" else va == vb
            if not same:
                diffs.append(f"{name}.{col}: {va} vs {vb}")
    if diffs:
        return ExperimentResult("truncation", FAIL, f"trades before {new_end} change when later data is removed (look-ahead): {diffs[:5]}")
    return ExperimentResult("truncation", PASS, f"all {len(a)} trades opened on or before {new_end} are identical without the last {drop_business_days} business days")


def signal_shift(spec: dict, base, lag: int = 1) -> ExperimentResult:
    if spec.get("archetype") not in SIGNAL_ARCHETYPES:
        return ExperimentResult("signal_shift", NA, f"archetype {spec.get('archetype')} has no signal")
    s = copy.deepcopy(spec)
    s["signal"]["lag"] = int(spec["signal"].get("lag") or 0) + lag
    b0, b1 = _stats(base), _stats(_run(s))
    t = pd.DataFrame([{"lag": spec["signal"].get("lag") or 0, **b0}, {"lag": s["signal"]["lag"], **b1}])
    if np.isfinite(b0["sharpe"]) and b0["sharpe"] > 0 and not (np.isfinite(b1["sharpe"]) and b1["sharpe"] > 0):
        return ExperimentResult("signal_shift", WARN, f"edge disappears with a {lag}-day lag (Sharpe {_fmt(b0['sharpe'])} -> {_fmt(b1['sharpe'])}): the result depends on trading at the observed close (checklist A3/B4)", t)
    return ExperimentResult("signal_shift", PASS, f"Sharpe {_fmt(b0['sharpe'])} -> {_fmt(b1['sharpe'])} with a {lag}-day lag", t)


def sub_periods(spec: dict, base, n: int = 3) -> ExperimentResult:
    d = _daily(base)
    if len(d) < 3 * n:
        return ExperimentResult("sub_periods", NA, "too few observations")
    bounds = np.linspace(0, len(d), n + 1).astype(int)
    chunks = [d.iloc[bounds[i]:bounds[i + 1]] for i in range(n)]
    rows = [{"period": f"{c.index[0]}..{c.index[-1]}", "pnl": float(c.sum()), "sharpe": _sharpe(c)} for c in chunks]
    t = pd.DataFrame(rows)
    total = float(d.sum())
    if total == 0:
        return ExperimentResult("sub_periods", WARN, "zero total P&L", t)
    same_sign = int((np.sign(t["pnl"]) == np.sign(total)).sum())
    top_share = float(t["pnl"].abs().max() / t["pnl"].abs().sum()) if t["pnl"].abs().sum() else 1.0
    status = PASS if same_sign > n / 2 and top_share <= 0.6 and total > 0 else WARN
    return ExperimentResult("sub_periods", status, f"{same_sign}/{n} periods share the sign of the total; largest period carries {top_share:.0%} of |P&L|", t)


def is_oos(spec: dict, base) -> ExperimentResult:
    ise = spec["dates"].get("in_sample_end")
    if not ise:
        return ExperimentResult("is_oos", WARN, "no dates.in_sample_end in the spec: no out-of-sample evidence")
    d = _daily(base)
    ins, oos = d[d.index <= ise], d[d.index > ise]
    t = pd.DataFrame([{"sample": "in", "days": len(ins), "sharpe": _sharpe(ins), "pnl": float(ins.sum())},
                      {"sample": "out", "days": len(oos), "sharpe": _sharpe(oos), "pnl": float(oos.sum())}])
    floor = (spec.get("success_criteria") or {}).get("min_oos_sharpe")
    sr = t.loc[1, "sharpe"]
    if floor is None:
        status = PASS if np.isfinite(sr) and sr > 0 else WARN
    else:
        status = PASS if np.isfinite(sr) and sr >= floor else WARN
    return ExperimentResult("is_oos", status, f"IS Sharpe {_fmt(t.loc[0, 'sharpe'])} ({len(ins)}d) vs OOS {_fmt(sr)} ({len(oos)}d); floor {floor}", t)


EXPERIMENTS: Dict[str, Callable] = {
    "cost_ladder": cost_ladder, "parameter_sweep": parameter_sweep, "truncation": truncation,
    "signal_shift": signal_shift, "sub_periods": sub_periods, "is_oos": is_oos,
}


def run_all(spec: Union[str, Path, dict], experiments: Optional[Sequence[str]] = None, base=None) -> List[ExperimentResult]:
    """Run the named experiments (default: all). `base` may be a BackTest already produced from this
    spec (saves one run). A crashing experiment becomes a FAIL row, never an exception."""
    s, _ = specmod.apply_defaults(spec)
    errors = specmod.validate_spec(s)
    if errors:
        raise ValueError("invalid spec: " + "; ".join(errors))
    base = base if base is not None else _run(s)
    out = []
    for name in experiments or EXPERIMENTS:
        try:
            out.append(EXPERIMENTS[name](s, base))
        except Exception as e:
            out.append(ExperimentResult(name, FAIL, f"experiment crashed: {type(e).__name__}: {e}"))
    return out


def to_markdown(results: List[ExperimentResult], tables: bool = False) -> str:
    lines = ["| Experiment | Status | Result |", "|---|---|---|"]
    lines += [f"| {r.name} | **{r.status}** | {r.detail.replace('|', '/')} |" for r in results]
    if tables:
        for r in results:
            if r.table is not None and len(r.table):
                lines += ["", f"**{r.name}**", "", r.table.to_markdown(index=False) if hasattr(r.table, "to_markdown") else r.table.to_string()]
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Robustness experiments for a strategy spec.")
    ap.add_argument("spec")
    ap.add_argument("--only", nargs="*", choices=sorted(EXPERIMENTS))
    a = ap.parse_args()
    res = run_all(a.spec, a.only)
    print(to_markdown(res, tables=True))
    raise SystemExit(1 if any(r.status == FAIL for r in res) else 0)
