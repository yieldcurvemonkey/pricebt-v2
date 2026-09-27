"""Rerun the SWAP strategy suite on REAL fixture curves, verify it, and write every artefact the report quotes.

    cd <project root>
    C:\\Users\\chris\\anaconda3\\envs\\stir\\python.exe tools\\run_suite_swaps.py                  # all runs (parallel), checks, tables, figures
    ... tools\\run_suite_swaps.py --only s2_steepener_2s10s,s4_meanrev_2s10s                  # a subset (checks needing absent runs are skipped)
    ... tools\\run_suite_swaps.py --no-run                                                    # recompute checks/tables from results_new\\ on disk
    ... tools\\run_suite_swaps.py --stack quantlib                                            # the same configs under another pricing library

The configs are library-neutral: ONE instrument spec `usd_sofr_ois` and the side / tenor / notional of every trade as terms of the action. `--stack` names
the overlay (`configs/adapters/<stack>_swap.yaml`: rateslib | quantlib | refstack, default rateslib) that sets the instrument's factory and the pricer's wrap.
Writes, for each run, `results_new/<name>/` (parquet frames + manifest, and for main runs `tearsheet.html` + `tearsheet.png`; robustness variants go to
`results_new/swap_suite_variants/<name>/`), then `results_new/swap_suite_summary.csv` (one row per run), `results_new/swap_suite_checks.csv` (one row per
check, PASS/FAIL/INFO), `results_new/swap_suite_tables.md` and figures under `results_new/swap_suite_figures/`. `results/` (the recorded outputs of the
pre-refactor suite) is never written; tasks/suite_reproduction.md compares the two. Every run goes through `pricebt.api.build(config, sets=..., stack=...)`
exactly like the CLI; variants are `--set` overrides of the suite configs, never separate files. The project root and `tests` must be importable (the
market provider is `support.curves:CurveStore`).
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import math
import os
import sys
import time
import traceback
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "src", ROOT / "tests", ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
os.environ.setdefault("MPLBACKEND", "Agg")
warnings.filterwarnings("ignore")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from tools.swap_suite_support import overlays, par_panel, shadow_ledger  # noqa: E402

CFG = ROOT / "configs" / "suite"
RESULTS = ROOT / "results_new"
VARIANTS = RESULTS / "swap_suite_variants"
FIGS = RESULTS / "swap_suite_figures"
NY = "America/New_York"
MODELLED = ("carry", "roll", "delta", "convexity")

AUG_DAYS = ("2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07")


def _dates(days: Sequence[str], hhmm: str) -> str:
    return "[" + ", ".join(f"{d} {hhmm}:00" for d in days) + "]"


@dataclass(frozen=True)
class Run:
    name: str
    config: str
    sets: Tuple[str, ...] = ()
    group: str = "main"  # main | variant
    note: str = ""
    tearsheet: bool = True

    @property
    def out(self) -> Path:
        return (RESULTS if self.group == "main" else VARIANTS) / self.name


def _cost_sets(prefixes: Sequence[str], level: float) -> Tuple[str, ...]:
    return tuple(f"{p}.transaction_cost.scaling_level={level}" for p in prefixes)


S2_COST = ("strategy.triggers.0.actions.0", "strategy.triggers.0.actions.1", "strategy.triggers.1.actions.0")
NO_LAYERS = "backtest.attribution.layers=[]"

RUNS: List[Run] = [
    # ---------------------------------------------------------------- main strategies (REAL curves)
    Run("s1_swap_carry_eod", "s1_swap_carry_eod.yaml", note="receive 10Y, monthly roll"),
    Run("s1m_swap_pay_mirror", "s1m_swap_pay_mirror.yaml", note="pay 10Y, monthly roll (mirror of S1)"),
    Run("s1f_swap_carry_full_history", "s1f_swap_carry_full_history.yaml", note="S1 over 2018-06..2026-09"),
    Run("s2_steepener_2s10s", "s2_steepener_2s10s.yaml", note="DV01-neutral 2s10s steepener, weekly re-neutralised"),
    Run("s3_fly_2s5s10s", "s3_fly_2s5s10s.yaml", note="pay-belly 2s5s10s fly, 50/50 DV01"),
    Run("s4_meanrev_2s10s", "s4_meanrev_2s10s.yaml", note="2s10s z-score mean reversion"),
    Run("s08_swap_intraday_min", "s08_swap_intraday_min.yaml", note="minute grid, DST week 2026-03-09..13"),
    Run("s08_swap_intraday_min_aug", "s08_swap_intraday_min.yaml", group="main", note="minute grid, 2026-08-03..07",
        sets=("backtest.grid.start=2026-08-03", "backtest.grid.end=2026-08-07", f"strategy.triggers.0.dates={_dates(AUG_DAYS, '09:30')}",
              f"strategy.triggers.2.dates={_dates(AUG_DAYS, '16:00')}", "name=s08_swap_intraday_min_aug")),
    Run("s08_swap_intraday_min_nov_dst", "s08_swap_intraday_min.yaml", note="minute grid, DST-end day 2025-11-03",
        sets=("backtest.grid.start=2025-11-03", "backtest.grid.end=2025-11-03", f"strategy.triggers.0.dates={_dates(['2025-11-03'], '09:30')}",
              f"strategy.triggers.2.dates={_dates(['2025-11-03'], '16:00')}", "name=s08_swap_intraday_min_nov_dst")),
    Run("s08b_swap_minute_hold_week", "s08b_swap_minute_hold_week.yaml", note="hold one payer across the DST week, layers every minute"),
    Run("s11_buy_hold_10y", "s11_buy_hold_10y.yaml", note="control: buy and hold"),
    Run("s12_always_flat", "s12_always_flat.yaml", note="control: always flat"),
    # ---------------------------------------------------------------- robustness / controls (variants of the same configs)
    Run("s1_fill_lag1", "s1_swap_carry_eod.yaml", ("backtest.fill_lag=1",), "variant", "S1 with fill_lag 1", False),
    Run("s2_fill_lag1", "s2_steepener_2s10s.yaml", ("backtest.fill_lag=1",), "variant", "S2 with fill_lag 1", False),
    Run("s11_fill_lag1", "s11_buy_hold_10y.yaml", ("backtest.fill_lag=1",), "variant", "S11 with fill_lag 1", False),
    Run("s1_notional_x2", "s1_swap_carry_eod.yaml", ("strategy.triggers.0.actions.0.priceables.terms.notional=2e7",), "variant", "S1 with notional 2e7", False),
    *[Run(f"s2_cost_{k}", "s2_steepener_2s10s.yaml", _cost_sets(S2_COST, lvl) + (NO_LAYERS,), "variant", f"S2 dv01 cost {lvl} per side, layers off", False)
      for k, lvl in (("0", 0.0), ("0p05", 0.05), ("0p1", 0.1), ("0p2", 0.2), ("0p4", 0.4))],
    *[Run(f"s1_notionalcost_{k}", "s1_swap_carry_eod.yaml",
          (f"strategy.triggers.0.actions.0.transaction_cost={{type: scaled, scaling_level: {lvl}}}", NO_LAYERS), "variant",
          f"S1 notional cost {lvl} x notional per side (ScaledCost default scaling_type=notional), layers off", False)
      for k, lvl in (("0", 0.0), ("1em5", 1e-5), ("2em5", 2e-5), ("4em5", 4e-5))],
    *[Run(f"s2_band_{b}", "s2_steepener_2s10s.yaml", (f"strategy.triggers.1.band={b}", NO_LAYERS), "variant", f"S2 rebalance band {b}", False)
      for b in (50, 100, 250)],
    Run("s1_shift1b", "s1_swap_carry_eod.yaml", ("strategy.triggers.0.start_date=2022-01-04",), "variant", "S1 with every roll one business day later", False),
    Run("s2_shift1b", "s2_steepener_2s10s.yaml", ("strategy.triggers.0.start_date=2022-01-04",), "variant", "S2 with every roll one business day later", False),
    *[Run(f"s4_z_{k}", "s4_meanrev_2s10s.yaml", (f"strategy.triggers.0.z_score_bound={z}", NO_LAYERS), "variant", f"S4 entry |z| > {z}", False)
      for k, z in (("1p0", 1.0), ("1p5", 1.5), ("2p0", 2.0))],
    Run("s4_truncated_2023", "s4_meanrev_2s10s.yaml", ("backtest.grid.end=2023-12-29", NO_LAYERS), "variant", "S4 truncated at 2023-12-29 (look-ahead check)", False),
]
BY_NAME = {r.name: r for r in RUNS}


# ============================================================================== worker
def _packages(pos: pd.DataFrame) -> pd.Series:
    """Net P&L per strategy cycle: positions grouped under the latest entry instant of an add/scaled (anchor) position; only cycles whose
    positions are all closed. Rebalance/hedge positions join the cycle they were opened in."""
    if pos.empty:
        return pd.Series(dtype=float)
    ent = pos["entry_ts"].map(lambda t: pd.Timestamp(t).value).to_numpy()
    anchors = np.unique(ent[pos["kind"].isin(["add", "scaled", "initial"]).to_numpy()])
    if len(anchors) == 0:
        return pd.Series(dtype=float)
    cyc = np.searchsorted(anchors, ent, side="right") - 1
    df = pd.DataFrame({"cycle": cyc, "pnl": pos["pnl_net"].to_numpy(dtype=float), "closed": (pos["status"] == "closed").to_numpy()})
    g = df.groupby("cycle")
    out = g["pnl"].sum()[g["closed"].all()]
    return out[out.index >= 0]


def unexplained_shares(r: Any) -> Dict[str, float]:
    att = r.attribution("layer")["pnl"]
    lay = [k for k in att.index if k in MODELLED]
    if not lay:
        return {"unexpl_cum_pct": float("nan"), "unexpl_daily_pct": float("nan")}
    gross_abs = float(sum(abs(att[k]) for k in lay) + abs(att["unexplained"]))
    day = r.attribution("day")
    gross_d = day["net_total"] - day["transactions"] - day["cash_interest"]
    act = float(day[[k for k in lay if k in day.columns]].abs().to_numpy().sum() + day["unexplained"].abs().sum())
    return {
        "unexpl_cum_pct": 100.0 * abs(float(att["unexplained"])) / gross_abs if gross_abs else float("nan"),
        "unexpl_daily_pct": 100.0 * float(day["unexplained"].abs().sum()) / float(gross_d.abs().sum()) if float(gross_d.abs().sum()) else float("nan"),
        "unexpl_activity_pct": 100.0 * float(day["unexplained"].abs().sum()) / act if act else float("nan"),
        "unexpl_daily_max": float(day["unexplained"].abs().max()),
    }


def summarize(r: Any, run: Run, elapsed: float) -> Dict[str, Any]:
    s = r.summary_stats()
    eq = r.equity
    pos = r.positions
    rep = r.reconcile(strict=False)
    strict_ok = True
    try:
        r.reconcile(strict=True)
    except Exception:  # noqa: BLE001 - recorded, the check table reports it
        strict_ok = False
    att = r.attribution("layer")["pnl"]
    days = r.returns()
    nz = days[days != 0]
    pk = _packages(pos)
    out: Dict[str, Any] = {
        "name": run.name, "group": run.group, "config": f"configs/suite/{run.config}", "sets": " ".join(run.sets), "note": run.note,
        "start": str(eq.index[0]), "end": str(eq.index[-1]), "n_points": int(len(eq)), "elapsed_s": round(elapsed, 2),
        "engine_s": round(float(r.record.manifest.get("elapsed_seconds", float("nan"))), 2),
        "ms_per_point": round(1e3 * float(r.record.manifest.get("elapsed_seconds", float("nan"))) / max(len(eq), 1), 2),
        "total_pnl": float(s["total_pnl"]), "total_tcost": float(s["total_tcost"]), "gross_pnl": float(s["total_pnl"] - s["total_tcost"]),
        "sharpe": float(s["sharpe"]), "ann_vol": float(s["ann_vol"]), "max_drawdown": float(s["max_drawdown"]),
        "day_hit_rate": float(s["hit_rate"]), "day_hit_rate_nonzero": float((nz > 0).mean()) if len(nz) else float("nan"),
        "n_positions": int(len(pos)), "n_packages": int(len(pk)), "package_hit_rate": float((pk > 0).mean()) if len(pk) else float("nan"),
        "n_rebalance": int((pos["kind"] == "rebalance").sum()) if len(pos) else 0, "n_hedge_positions": int((pos["kind"] == "hedge").sum()) if len(pos) else 0,
        "n_resizes": int(s["n_resizes"]), "n_errors": int(r.n_errors), "reconcile_ok": bool(rep.ok), "reconcile_strict_ok": strict_ok,
        "reconcile_max_err": float(rep.max_abs_error),
    }
    for k in (*MODELLED, "unexplained", "transactions"):
        out[f"layer_{k}"] = float(att[k]) if k in att.index else float("nan")
    out.update(unexplained_shares(r))
    if "measure_dv01" in eq.columns:
        out["max_abs_net_dv01"] = float(eq["measure_dv01"].abs().max())
    return out


def run_one(run: Run, stack: str = "rateslib") -> Dict[str, Any]:
    warnings.filterwarnings("ignore")
    from pricebt import api

    t0 = time.perf_counter()
    try:
        built = api.build(CFG / run.config, sets=[*run.sets, "backtest.progress.show=false", "outputs.dir=null"], stack=overlays(stack, "swap"))
        r = built.run()
        elapsed = time.perf_counter() - t0
        run.out.mkdir(parents=True, exist_ok=True)
        r.to_parquet(run.out)
        if run.tearsheet:
            r.tearsheet(run.out / "tearsheet", formats=("html", "png"), title=f"{run.name}: {run.note}")
        out = summarize(r, run, elapsed)
        out["config_hash"] = built.config_hash
        out["stack"] = stack
        out["ok"] = True
        return out
    except Exception as e:  # noqa: BLE001 - a failed run is reported, never hidden
        return {"name": run.name, "group": run.group, "ok": False, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-3000:],
                "elapsed_s": round(time.perf_counter() - t0, 2)}


# ============================================================================== independent market panel (tools/swap_suite_support.par_panel)
def ols(y: np.ndarray, X: np.ndarray, names: Sequence[str]) -> Dict[str, float]:
    A = np.column_stack([np.ones(len(y)), X])
    beta, *_ = np.linalg.lstsq(A, y, rcond=None)
    res = y - A @ beta
    r2 = 1.0 - float(res @ res) / float(((y - y.mean()) ** 2).sum())
    return {"const": float(beta[0]), **{n: float(b) for n, b in zip(names, beta[1:])}, "r2": r2, "n": int(len(y))}


# ============================================================================== checks
class Checks:
    def __init__(self) -> None:
        self.rows: List[Dict[str, Any]] = []

    def add(self, cid: str, status: str, value: Any, threshold: str, detail: str = "") -> None:
        self.rows.append({"check": cid, "status": status, "value": value, "threshold": threshold, "detail": detail})

    def gate(self, cid: str, ok: bool, value: Any, threshold: str, detail: str = "") -> None:
        self.add(cid, "PASS" if ok else "FAIL", value, threshold, detail)

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows)


def _load(name: str) -> Optional[Any]:
    from pricebt.results import BacktestResult

    run = BY_NAME[name]
    if not (run.out / "manifest.json").exists():
        return None
    return BacktestResult.from_parquet(run.out)


def _gross(r: Any) -> pd.Series:
    return r.equity["equity"] - r.equity["tcost"]


def run_checks(summary: pd.DataFrame) -> Tuple[Checks, Dict[str, Any]]:
    ck = Checks()
    extra: Dict[str, Any] = {}
    R = {n: _load(n) for n in BY_NAME}

    # --- every run: strict reconcile, no errors, finite
    for _, row in summary.iterrows():
        if not row.get("ok", False):
            ck.gate(f"run[{row['name']}]", False, row.get("error"), "runs without exception")
            continue
        ck.gate(f"reconcile_strict[{row['name']}]", bool(row["reconcile_strict_ok"]), f"{row['reconcile_max_err']:.2e}", "reconcile(strict=True) passes")
        ck.gate(f"no_engine_errors[{row['name']}]", int(row["n_errors"]) == 0, int(row["n_errors"]), "0 recorded errors")

    # --- S12 always flat: exactly zero
    r = R["s12_always_flat"]
    if r is not None:
        eq = r.equity
        flat = bool((eq["equity"].to_numpy() == 0.0).all()) and r.trades.empty and r.positions.empty and r.record.layers_by_position.empty
        ck.gate("S12_equity_exactly_zero", flat, f"max|equity|={float(eq['equity'].abs().max())}, trades={len(r.trades)}, positions={len(r.positions)}",
                "equity == 0.0 on every row; no trades/positions/layers")
        ev = r.events
        fired = ev[ev["kind"] == "trigger_fired"]["detail"].map(lambda d: d.get("trigger_idx")) if len(ev) else pd.Series(dtype=float)
        ck.gate("S12_zero_size_path_exercised", int((fired == 1).sum()) >= 40 and int((fired == 0).sum()) == 0,
                f"periodic fired {int((fired == 1).sum())}x, mkt fired {int((fired == 0).sum())}x",
                "the monthly zero-size add fires (sized to 0 -> no order); the constant-0 mkt trigger never fires")

    # --- S1 + S1m mirror on real data
    a, b = R["s1_swap_carry_eod"], R["s1m_swap_pay_mirror"]
    if a is not None and b is not None:
        ga, gb = _gross(a), _gross(b)
        scale = max(1.0, float(ga.abs().max()))
        err = float((ga + gb).abs().max())
        ck.gate("mirror_gross_sums_to_zero", err <= 1e-9 * scale, f"{err:.3e} (scale {scale:,.0f})", "max|gross_recv + gross_pay| <= 1e-9 * max|gross|")
        la, lb = a.layers, b.layers
        lerr = max(float((la[c] + lb[c]).abs().max()) for c in la.columns)
        ck.gate("mirror_layers_negate", lerr <= 1e-9 * scale, f"{lerr:.3e}", "every cumulative layer (incl. unexplained) negates, 1e-9 rel")
        cerr = float((a.equity["tcost"] - b.equity["tcost"]).abs().max())
        ck.gate("mirror_costs_equal", cerr <= 1e-9 * scale, f"{cerr:.3e}", "the two dv01 cost streams are equal (|dv01| identical)")
        extra["mirror"] = {"recv_net": float(a.pnl.iloc[-1]), "pay_net": float(b.pnl.iloc[-1]), "sum_net": float(a.pnl.iloc[-1] + b.pnl.iloc[-1]),
                           "sum_gross": float(ga.iloc[-1] + gb.iloc[-1]), "sum_tcost": float(a.equity["tcost"].iloc[-1] + b.equity["tcost"].iloc[-1])}

    # --- notional doubling
    x2 = R["s1_notional_x2"]
    if a is not None and x2 is not None:
        scale = max(1.0, float(a.pnl.abs().max()))
        err = float((x2.pnl - 2.0 * a.pnl).abs().max())
        lerr = max(float((x2.layers[c] - 2.0 * a.layers[c]).abs().max()) for c in a.layers.columns)
        ck.gate("notional_x2_doubles_pnl", err <= 1e-9 * scale, f"{err:.3e}; ratio={float(x2.pnl.iloc[-1] / a.pnl.iloc[-1]):.12f}", "pnl(2e7) == 2 * pnl(1e7) every row, 1e-9 rel")
        ck.gate("notional_x2_doubles_layers", lerr <= 1e-9 * 2 * scale, f"{lerr:.3e}", "every layer doubles, 1e-9 rel")

    # --- cost monotonicity (dv01 cost on S2, notional cost on S1)
    for fam, names, lvls in (("S2_dv01_cost", ["s2_cost_0", "s2_cost_0p05", "s2_cost_0p1", "s2_cost_0p2", "s2_cost_0p4"], [0, 0.05, 0.1, 0.2, 0.4]),
                             ("S1_notional_cost", ["s1_notionalcost_0", "s1_notionalcost_1em5", "s1_notionalcost_2em5", "s1_notionalcost_4em5"], [0, 1e-5, 2e-5, 4e-5])):
        rs = [R[n] for n in names]
        if any(x is None for x in rs):
            continue
        gross = [float(_gross(x).iloc[-1]) for x in rs]
        net = [float(x.pnl.iloc[-1]) for x in rs]
        tc = [float(-x.equity["tcost"].iloc[-1]) for x in rs]
        g_err = max(float((_gross(x) - _gross(rs[0])).abs().max()) for x in rs)
        ck.gate(f"{fam}_gross_invariant", g_err <= 1e-6, f"{g_err:.3e}", "gross P&L identical at every cost level (same decisions)")
        ck.gate(f"{fam}_net_strictly_decreasing", all(n1 > n2 for n1, n2 in zip(net, net[1:])), ", ".join(f"{v:,.0f}" for v in net), "net P&L strictly decreasing in the cost level")
        lin = [t / lvls[i] for i, t in enumerate(tc) if lvls[i] > 0]
        ck.gate(f"{fam}_cost_linear_in_level", max(lin) - min(lin) <= 1e-6 * max(abs(v) for v in lin), ", ".join(f"{v:,.2f}" for v in lin), "total cost / level constant")
        extra[fam] = {"levels": lvls, "net": net, "gross": gross, "tcost": tc}

    # --- fill lag
    for base, lag in (("s1_swap_carry_eod", "s1_fill_lag1"), ("s2_steepener_2s10s", "s2_fill_lag1"), ("s11_buy_hold_10y", "s11_fill_lag1")):
        r0, r1 = R[base], R[lag]
        if r0 is None or r1 is None:
            continue
        d = float(r1.pnl.iloc[-1] - r0.pnl.iloc[-1])
        extra.setdefault("fill_lag", {})[base] = {"lag0": float(r0.pnl.iloc[-1]), "lag1": float(r1.pnl.iloc[-1]), "diff": d,
                                                    "n_pos_lag0": int(len(r0.positions)), "n_pos_lag1": int(len(r1.positions)),
                                                    "unfilled_at_end": int((r1.events["kind"] == "unfilled_order_at_end").sum()) if len(r1.events) else 0}
        if base == "s11_buy_hold_10y":
            ck.gate("S11_terminal_equity_fill_lag_invariant", abs(d) <= 1e-6 * max(1.0, abs(float(r0.pnl.iloc[-1]))), f"{d:.3e}",
                    "buy-and-hold terminal equity identical under fill_lag 0 and 1")
        else:
            ck.add(f"fill_lag_effect[{base}]", "INFO", f"{d:,.0f}", "report only", f"lag0 {r0.pnl.iloc[-1]:,.0f} vs lag1 {r1.pnl.iloc[-1]:,.0f}")

    # --- independent par-rate panel for the EOD strategies
    stamps = set()
    for n in ("s2_steepener_2s10s", "s3_fly_2s5s10s", "s4_meanrev_2s10s", "s1_swap_carry_eod"):
        if R[n] is not None:
            stamps |= set(R[n].equity.index)
    panel = par_panel(sorted(stamps)) * 100.0 if stamps else pd.DataFrame()  # bp
    extra["panel_points"] = int(len(panel))

    # S2 steepener sign / factor regression
    r = R["s2_steepener_2s10s"]
    if r is not None and len(panel):
        pnl = r.step_pnl
        pnl_gross = pnl + r.equity["tcost"].diff().fillna(r.equity["tcost"]).mul(-1.0)
        pp = panel.reindex(r.equity.index)
        slope, level = (pp["10Y"] - pp["2Y"]).diff(), ((pp["10Y"] + pp["2Y"]) / 2).diff()
        m = pd.concat([pnl_gross.rename("y"), slope.rename("slope"), level.rename("level")], axis=1).dropna().iloc[1:]
        f = ols(m["y"].to_numpy(), m[["slope", "level"]].to_numpy(), ["slope", "level"])
        extra["s2_regression"] = f
        ck.gate("S2_is_a_steepener_beta_slope", 9000 <= f["slope"] <= 11000, f"{f['slope']:,.0f}", "beta on d(r10 - r2) in [+9,000, +11,000] ccy/bp (positive = steepener)")
        ck.gate("S2_level_neutral", abs(f["level"]) <= 1500, f"{f['level']:,.0f}", "|beta on d(level)| <= 1,500 ccy/bp")
        ck.gate("S2_regression_r2", f["r2"] >= 0.9, f"{f['r2']:.4f}", "R^2 >= 0.90")
        eq = r.equity
        extra["s2_dv01"] = {"max_abs_all_rows": float(eq["measure_dv01"].abs().max()), "n_rebalance": int((r.positions["kind"] == "rebalance").sum())}
        reb = r.positions[r.positions["kind"] == "rebalance"]
        if len(reb):
            at = eq.loc[pd.DatetimeIndex(reb["entry_ts"]).unique(), "measure_dv01"].abs()
            ck.gate("S2_rebalance_restores_neutrality", float(at.max()) <= 25.0, f"{float(at.max()):.3f}", "|net dv01| <= 25/bp on every rebalance row (fresh-leg sizing)")
        leak = r.positions[(r.positions["kind"] == "rebalance") & r.positions["exit_ts"].isna()]
        last_roll = r.positions.loc[r.positions["kind"] == "scaled", "entry_ts"].max()
        ck.gate("S2_rebalance_legs_expire_with_roll", bool((leak["entry_ts"] >= last_roll).all()), f"{len(leak)} open", "no top-up survives its monthly roll")

    # S3 fly regression
    r = R["s3_fly_2s5s10s"]
    if r is not None and len(panel):
        pnl_gross = r.step_pnl + r.equity["tcost"].diff().fillna(r.equity["tcost"]).mul(-1.0)
        pp = panel.reindex(r.equity.index)
        fly = (2 * pp["5Y"] - pp["2Y"] - pp["10Y"]).diff()
        slope, level = (pp["10Y"] - pp["2Y"]).diff(), pp[["2Y", "5Y", "10Y"]].mean(axis=1).diff()
        m = pd.concat([pnl_gross.rename("y"), fly.rename("fly"), slope.rename("slope"), level.rename("level")], axis=1).dropna().iloc[1:]
        f = ols(m["y"].to_numpy(), m[["fly", "slope", "level"]].to_numpy(), ["fly", "slope", "level"])
        extra["s3_regression"] = f
        ck.gate("S3_beta_fly", 4500 <= f["fly"] <= 5500, f"{f['fly']:,.0f}", "beta on d(2 r5 - r2 - r10) in [4,500, 5,500] ccy/bp")
        ck.gate("S3_level_slope_neutral", abs(f["level"]) <= 750 and abs(f["slope"]) <= 750, f"level {f['level']:,.0f}, slope {f['slope']:,.0f}", "|beta_level|, |beta_slope| <= 750")
        ck.gate("S3_regression_r2", f["r2"] >= 0.9, f"{f['r2']:.4f}", "R^2 >= 0.90")

    # S1 carry/roll sign vs slope (economics) and full history by year
    for n in ("s1_swap_carry_eod", "s1f_swap_carry_full_history"):
        r = R[n]
        if r is None:
            continue
        L = r.layers
        yr = L.index.year
        by_year = pd.DataFrame({"net_pnl": r.step_pnl.groupby(yr).sum(), **{k: L[k].diff().fillna(L[k]).groupby(yr).sum() for k in ("carry", "roll", "delta", "convexity", "unexplained") if k in L}})
        extra[f"{n}_by_year"] = by_year

    # S4: reference state machine from the independent panel (look-ahead / reproducibility) + truncation invariance
    r = R["s4_meanrev_2s10s"]
    if r is not None and len(panel):
        x = (panel["10Y"] - panel["2Y"]).reindex(r.equity.index)
        vals = x.to_numpy()
        idx = x.index
        pos_state, entries, exits, zs = 0, [], [], {}
        for i in range(60, len(vals)):
            prior = vals[i - 60:i]
            mean, sd = prior.mean(), prior.std(ddof=1)
            z = (vals[i] - mean) / sd
            zs[idx[i]] = z
            if pos_state == 1 and abs(z) < 0.3:
                pos_state = 0
                exits.append(idx[i])
            elif pos_state == -1 and abs(z) < 0.3:
                pos_state = 0
                exits.append(idx[i])
            elif pos_state == 0 and abs(z) > 1.5:
                pos_state = -1 if vals[i] > mean else 1
                entries.append((idx[i], pos_state))
        p = r.positions[r.positions["action"] == "mr_pay10y"]
        bt = [(pd.Timestamp(t), int(np.sign(q))) for t, q in zip(p["entry_ts"], p["entry_quantity"])]
        ref = [(pd.Timestamp(t), s) for t, s in entries]
        ck.gate("S4_entries_match_reference_zscore", bt == ref, f"{len(bt)} backtest vs {len(ref)} reference entries",
                "entry instants and sides equal a pandas/numpy re-implementation on par rates <= t (prior-60 window, ddof 1)")
        zexit = set(pd.DatetimeIndex(p.loc[p["exit_reason"] == "z_reverted", "exit_ts"]))
        ck.gate("S4_z_exits_are_reference_exits", zexit <= set(exits), f"{len(zexit)} z exits", "every z_reverted exit is a reference |z| < 0.3 instant")
        zent = [abs(zs[t]) for t, _ in bt if t in zs]
        ck.gate("S4_entry_abs_z_above_bound", bool(zent) and min(zent) > 1.5, f"min |z| at entry {min(zent) if zent else float('nan'):.3f}", "> 1.5")
        first_entry = min((t for t, _ in bt), default=None)
        ck.gate("S4_no_entry_in_warmup", first_entry is None or first_entry >= idx[60], str(first_entry), f">= 61st point {idx[60]}")
        extra["s4"] = {"entries": len(bt), "flattener": sum(1 for _, s in bt if s < 0), "steepener": sum(1 for _, s in bt if s > 0),
                       "exit_reasons": p["exit_reason"].value_counts().to_dict()}
        rt = R["s4_truncated_2023"]
        if rt is not None:
            cut = rt.equity.index[-1]
            full = p[p["entry_ts"] <= cut][["entry_ts", "entry_quantity"]].reset_index(drop=True)
            pt = rt.positions[rt.positions["action"] == "mr_pay10y"][["entry_ts", "entry_quantity"]].reset_index(drop=True)
            same = len(full) == len(pt) and bool((full["entry_ts"].to_numpy() == pt["entry_ts"].to_numpy()).all()) and bool(np.allclose(full["entry_quantity"], pt["entry_quantity"], rtol=1e-12))
            ck.gate("S4_truncation_invariant", same, f"{len(pt)} entries before {cut.date()}", "entries before the cut identical with and without later data")
        zc = {k: R[f"s4_z_{k}"] for k in ("1p0", "1p5", "2p0")}
        if all(v is not None for v in zc.values()):
            n = [int((v.positions["action"] == "mr_pay10y").sum()) for v in zc.values()]
            ck.gate("S4_entries_nonincreasing_in_z_bound", n[0] >= n[1] >= n[2], str(n), "entries(|z|>1.0) >= entries(1.5) >= entries(2.0)")
            extra["s4_zsweep"] = {"bounds": [1.0, 1.5, 2.0], "entries": n, "net": [float(v.pnl.iloc[-1]) for v in zc.values()]}

    # S2 band sweep
    bands = {b: R[f"s2_band_{b}"] for b in (50, 100, 250)}
    if all(v is not None for v in bands.values()):
        n = [int((v.positions["kind"] == "rebalance").sum()) for v in bands.values()]
        ck.gate("S2_rebalances_nonincreasing_in_band", n[0] >= n[1] >= n[2], str(n), "count(50) >= count(100) >= count(250)")
        extra["s2_bands"] = {"bands": [50, 100, 250], "rebalances": n, "net": [float(v.pnl.iloc[-1]) for v in bands.values()],
                             "tcost": [float(v.equity["tcost"].iloc[-1]) for v in bands.values()],
                             "max_abs_dv01": [float(v.equity["measure_dv01"].abs().max()) for v in bands.values()]}

    # execution-timing shift (fill_lag 1 cannot show it: it books at the decision-point price)
    for base, sh in (("s1_swap_carry_eod", "s1_shift1b"), ("s2_steepener_2s10s", "s2_shift1b")):
        r0, r1 = R[base], R[sh]
        if r0 is not None and r1 is not None:
            extra.setdefault("timing_shift_1b", {})[base] = {"base": float(r0.pnl.iloc[-1]), "rolls_1b_later": float(r1.pnl.iloc[-1]),
                                                             "diff": float(r1.pnl.iloc[-1] - r0.pnl.iloc[-1])}
            ck.add(f"timing_shift_1b[{base}]", "INFO", f"{float(r1.pnl.iloc[-1] - r0.pnl.iloc[-1]):,.0f}", "report only",
                   f"rolls one business day later: {r1.pnl.iloc[-1]:,.0f} vs {r0.pnl.iloc[-1]:,.0f}")

    # periodic rolls never overlap (the `next schedule` exit), per strategy the max number of simultaneously open anchor legs
    for n, legs in (("s1_swap_carry_eod", 1), ("s1m_swap_pay_mirror", 1), ("s1f_swap_carry_full_history", 1), ("s2_steepener_2s10s", 2), ("s3_fly_2s5s10s", 3),
                    ("s4_meanrev_2s10s", 2)):
        r = R[n]
        if r is None or r.positions.empty:
            continue
        p = r.positions[r.positions["kind"].isin(["add", "scaled"])]
        idx = r.equity.index
        ent = idx.searchsorted(pd.DatetimeIndex(p["entry_ts"]), side="left")
        ext = idx.searchsorted(pd.DatetimeIndex(p["exit_ts"].fillna(idx[-1] + pd.Timedelta(days=1))), side="left")
        cnt = np.zeros(len(idx) + 1)
        np.add.at(cnt, ent, 1)
        np.add.at(cnt, ext, -1)
        mx = int(np.cumsum(cnt)[:-1].max())
        ck.gate(f"no_overlapping_rolls[{n}]", mx <= legs, mx, f"<= {legs} anchor legs open at any point")

    # S11 independent shadow ledger (raw rateslib, curve rebuilt from the parquet row, fixings read from the parquet file)
    r = R["s11_buy_hold_10y"]
    if r is not None:
        sh = shadow_ledger(r)
        ctl = shadow_ledger(r, bump_bp=1.0)  # control: a shadow with the wrong strike must NOT match (the check is not vacuous)
        extra["s11_shadow"] = {**sh, "control_1bp_wrong_strike_max_abs_diff": ctl["max_abs_diff"]}
        ck.gate("S11_equity_equals_raw_rateslib_shadow", sh["max_abs_diff"] <= 1.0 and ctl["max_abs_diff"] > 50.0,
                f"{sh['max_abs_diff']:.2e} over {sh['n']} dates (control with strike +1bp: {ctl['max_abs_diff']:,.0f})",
                "|engine equity - (NPV_raw(t) - NPV_raw(t0) + raw cashflows paid)| <= 1 ccy; the +1bp-strike control differs by > 50")

    # S8 intraday machinery
    for n in ("s08_swap_intraday_min", "s08_swap_intraday_min_aug", "s08_swap_intraday_min_nov_dst"):
        r = R[n]
        if r is None:
            continue
        pos, eq = r.positions, r.equity
        if pos.empty:
            ck.gate(f"S8_has_entries[{n}]", False, 0, "one entry per session")
            continue
        ent = pd.DatetimeIndex(pos["entry_ts"]).tz_convert(NY)
        loc = pd.DatetimeIndex(eq.index).tz_convert(NY)
        first_pts = pd.Series(loc, index=loc.normalize()).groupby(level=0).min()
        want = [first_pts.loc[t.normalize()] for t in ent]
        ok_entry = all(t == w and (t.hour, t.minute) >= (9, 30) for t, w in zip(ent, want)) and len(ent) == len(first_pts)
        ck.gate(f"S8_entry_at_first_session_minute[{n}]", ok_entry, ", ".join(f"{t:%m-%d %H:%M %Z}" for t in ent),
                "one entry per session at 09:30 America/New_York (or the first minute the store has after it)")
        ex = pos[["exit_ts", "exit_reason", "entry_ts"]]
        t_ex = ex[ex["exit_reason"] == "time_exit_1600"]
        ok_t = bool(((pd.DatetimeIndex(t_ex["exit_ts"]).tz_convert(NY).hour == 16) & (pd.DatetimeIndex(t_ex["exit_ts"]).tz_convert(NY).minute == 0)).all())
        same_day = bool((pd.DatetimeIndex(ex["exit_ts"]).tz_convert(NY).date == pd.DatetimeIndex(ex["entry_ts"]).tz_convert(NY).date).all())
        ck.gate(f"S8_time_exits_at_1600_same_day[{n}]", ok_t and same_day and set(ex["exit_reason"]) <= {"time_exit_1600", "stop_loss"},
                ex["exit_reason"].value_counts().to_dict(), "exits at 16:00 or by the stop, never overnight")
        stops = pos[pos["exit_reason"] == "stop_loss"]
        stop_rows = []
        for _, p_ in stops.iterrows():
            e0 = eq.index.get_loc(p_["entry_ts"])
            k = eq.index.get_loc(p_["exit_ts"])
            base = eq["equity"].iloc[e0 - 1] if e0 > 0 else 0.0
            path = eq["equity"].iloc[e0:k] - base  # position pnl as recorded at the points before the exit point
            first = int(np.argmax(path.to_numpy() < -15000)) if (path < -15000).any() else -1
            stop_rows.append({"entry": str(p_["entry_ts"]), "exit": str(p_["exit_ts"]), "pnl_prev_mark": float(path.iloc[-1]),
                              "first_breach_is_prev_point": first == len(path) - 1, "pnl_net": float(p_["pnl_net"])})
        if stop_rows:
            ck.gate(f"S8_stop_fires_one_point_after_first_breach[{n}]", all(s["first_breach_is_prev_point"] for s in stop_rows),
                    "; ".join(f"{s['exit'][5:16]} prev-mark {s['pnl_prev_mark']:,.0f} -> booked {s['pnl_net']:,.0f}" for s in stop_rows),
                    "the stop reads the P&L recorded at the previous minute and exits at the next point")
        cr = max(float(eq[c].abs().max()) for c in ("layer_carry", "layer_roll") if c in eq)
        ck.gate(f"S8_carry_roll_zero_intraday[{n}]", cr == 0.0, cr, "carry and roll exactly 0.0 when no position spans a reference-date flip")
        marks = eq.loc[eq["n_positions"] > 0, "positions_value"]
        extra.setdefault("s8", {})[n] = {"points": int(len(eq)), "distinct_marks": int(marks.nunique()), "stops": stop_rows,
                                         "ms_per_point": float(summary.set_index("name")["ms_per_point"].get(n, float("nan")))}
        ck.gate(f"S8_minute_marks_move[{n}]", marks.nunique() > 0.5 * len(marks), f"{marks.nunique()} distinct of {len(marks)}", "> 50% of held minutes have a new mark")

    r = R["s08b_swap_minute_hold_week"]
    if r is not None:
        eq = r.equity
        days = pd.DatetimeIndex(eq.index).tz_convert(NY).normalize()
        first_of_day = np.r_[True, days[1:] != days[:-1]]
        out = {}
        for k in ("carry", "roll", "delta"):
            st = eq[f"layer_{k}"].diff().fillna(eq[f"layer_{k}"])
            out[k] = st
        intraday = max(float(out["carry"][~first_of_day].abs().max()), float(out["roll"][~first_of_day].abs().max()))
        lumps = pd.DataFrame({k: out[k][first_of_day] for k in ("carry", "roll", "delta")}).iloc[1:]
        ck.gate("S8b_carry_roll_zero_inside_sessions", intraday <= 1e-6, f"{intraday:.3e}", "|carry|, |roll| <= 1e-6 on every intraday interval")
        ck.gate("S8b_carry_roll_lump_at_reference_flip", bool((lumps[["carry", "roll"]].abs().sum(axis=1) > 1.0).all()), len(lumps),
                "carry+roll non-zero on the first interval of every later session")
        extra["s8b_lumps"] = lumps
        extra["s8b_unexpl"] = unexplained_shares(r)

    # unexplained share gate on every layered main run
    for _, row in summary.iterrows():
        if row.get("ok") and row["group"] == "main" and not math.isnan(row.get("unexpl_daily_pct", float("nan"))):
            ok = row["unexpl_daily_pct"] <= 2.0
            val = f"daily {row['unexpl_daily_pct']:.3f}% of |net gross| / {row['unexpl_activity_pct']:.3f}% of layer activity / cum {row['unexpl_cum_pct']:.3f}%"
            ck.gate(f"unexplained_small[{row['name']}]", ok, val, "sum|daily unexplained| <= 2% of sum|daily gross P&L|")
    return ck, extra


# ============================================================================== tables and figures
def fmt(v: Any, f: str = "{:,.0f}") -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    try:
        return f.format(v)
    except (ValueError, TypeError):
        return str(v)


def md_table(df: pd.DataFrame) -> str:
    cols = [str(c) for c in df.columns]
    lines = ["| " + " | ".join([df.index.name or ""] + cols) + " |", "|" + "---|" * (len(cols) + 1)]
    for i, row in df.iterrows():
        lines.append("| " + " | ".join([str(i)] + [str(v) for v in row.tolist()]) + " |")
    return "\n".join(lines)


def write_tables(summary: pd.DataFrame, checks: pd.DataFrame, extra: Dict[str, Any]) -> Path:
    s = summary[summary["ok"]].set_index("name")
    parts: List[str] = [f"# SWAP suite tables (generated {dt.datetime.now():%Y-%m-%d %H:%M} by tools/run_suite_swaps.py)", ""]
    head = pd.DataFrame({
        "window": [f"{a[:10]}..{b[:10]}" for a, b in zip(s["start"], s["end"])],
        "net P&L": [fmt(v) for v in s["total_pnl"]], "costs": [fmt(v) for v in s["total_tcost"]], "Sharpe": [fmt(v, "{:.2f}") for v in s["sharpe"]],
        "max DD": [fmt(v) for v in s["max_drawdown"]], "positions": s["n_positions"], "packages": s["n_packages"],
        "pkg hit": [fmt(v, "{:.0%}") for v in s["package_hit_rate"]], "day hit (non-0)": [fmt(v, "{:.0%}") for v in s["day_hit_rate_nonzero"]],
        "reconcile strict": s["reconcile_strict_ok"],
    }, index=s.index)
    head.index.name = "run"
    parts += ["## Headline statistics (all runs)", "", md_table(head), ""]
    lay = pd.DataFrame({k: [fmt(v) for v in s[f"layer_{k}"]] for k in (*MODELLED, "unexplained", "transactions")}, index=s.index)
    lay["unexpl % cum"] = [fmt(v, "{:.3f}") for v in s["unexpl_cum_pct"]]
    lay["unexpl % daily"] = [fmt(v, "{:.3f}") for v in s["unexpl_daily_pct"]]
    lay.index.name = "run"
    parts += ["## Attribution by layer (currency; unexplained % of gross)", "", md_table(lay), ""]
    tim = pd.DataFrame({"points": s["n_points"], "engine s": s["engine_s"], "wall s": s["elapsed_s"], "ms/point": s["ms_per_point"], "positions": s["n_positions"]}, index=s.index)
    tim.index.name = "run"
    parts += ["## Timing", "", md_table(tim), ""]
    ck = checks.copy()
    ck.index = ck.pop("check")
    ck.index.name = "check"
    parts += ["## Checks", "", md_table(ck), ""]
    for k, v in extra.items():
        parts += [f"## extra: {k}", ""]
        if isinstance(v, pd.DataFrame):
            parts += [md_table(v.round(2)), ""]
        else:
            parts += ["```", json.dumps(v, indent=1, default=str), "```", ""]
    out = RESULTS / "swap_suite_tables.md"
    out.write_text("\n".join(parts), encoding="utf8")
    return out


def figures(extra: Dict[str, Any]) -> List[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from pricebt.results.tearsheet import comparison_figure

    FIGS.mkdir(parents=True, exist_ok=True)
    out: List[Path] = []
    main = {n: _load(n) for n in ("s1_swap_carry_eod", "s2_steepener_2s10s", "s3_fly_2s5s10s", "s4_meanrev_2s10s", "s11_buy_hold_10y", "s12_always_flat")}
    main = {k: v for k, v in main.items() if v is not None}
    if main:
        p = FIGS / "eod_suite_cumulative_pnl.png"
        p.write_bytes(comparison_figure(main))
        out.append(p)
    r = _load("s2_steepener_2s10s")
    if r is not None:
        pp = par_panel(list(r.equity.index)) * 100.0
        slope = (pp["10Y"] - pp["2Y"]).diff()
        y = r.step_pnl
        fig, ax = plt.subplots(1, 2, figsize=(11, 4))
        ax[0].scatter(slope, y, s=5, alpha=0.5)
        f = extra.get("s2_regression", {})
        xs = np.linspace(float(slope.min()), float(slope.max()), 10)
        ax[0].plot(xs, f.get("const", 0) + f.get("slope", 0) * xs, color="C3", lw=1.2, label=f"beta {f.get('slope', float('nan')):,.0f}/bp, R2 {f.get('r2', float('nan')):.3f}")
        ax[0].set_xlabel("daily change of 10Y - 2Y par (bp)")
        ax[0].set_ylabel("S2 daily P&L")
        ax[0].legend()
        ax[1].plot(r.equity.index.tz_localize(None), r.pnl, label="S2 cumulative net P&L")
        ax2 = ax[1].twinx()
        ax2.plot(pp.index.tz_localize(None), pp["10Y"] - pp["2Y"], color="C1", lw=0.8, label="2s10s (bp, rhs)")
        ax[1].set_title("S2 steepener vs the 2s10s slope")
        ax[1].legend(loc="upper left")
        ax2.legend(loc="lower right")
        fig.tight_layout()
        p = FIGS / "s2_pnl_vs_slope.png"
        fig.savefig(p, dpi=110)
        plt.close(fig)
        out.append(p)
    r = _load("s08b_swap_minute_hold_week")
    if r is not None:
        eq = r.equity
        fig, ax = plt.subplots(2, 1, figsize=(11, 5), sharex=True)
        x = np.arange(len(eq))
        for k in ("carry", "roll"):
            ax[0].plot(x, eq[f"layer_{k}"].diff().fillna(0.0), lw=0.9, label=f"{k} per minute")
        ax[0].legend()
        ax[0].set_title("S8b: carry/roll per 1-minute interval (0 inside sessions, one lump at each reference-date flip)")
        ax[1].plot(x, eq["equity"], lw=0.9, label="cumulative P&L")
        ax[1].plot(x, eq["layer_delta"], lw=0.9, label="delta layer (cum)")
        days = pd.DatetimeIndex(eq.index).tz_convert(NY).normalize()
        starts = np.flatnonzero(np.r_[True, days[1:] != days[:-1]])
        for a_ in (ax[0], ax[1]):
            for s_ in starts[1:]:
                a_.axvline(s_, color="0.7", lw=0.6)
        ax[1].set_xticks(starts)
        ax[1].set_xticklabels([f"{d:%a %m-%d}" for d in days[starts]])
        ax[1].legend()
        fig.tight_layout()
        p = FIGS / "s08b_minute_layers.png"
        fig.savefig(p, dpi=110)
        plt.close(fig)
        out.append(p)
    return out


# ============================================================================== main
def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", default="", help="comma-separated run names (default: all)")
    ap.add_argument("--jobs", type=int, default=max(1, min(len(RUNS), (os.cpu_count() or 2) - 2)))
    ap.add_argument("--no-run", action="store_true", help="skip the backtests; recompute summary/checks from results on disk")
    ap.add_argument("--no-figures", action="store_true")
    ap.add_argument("--stack", default="rateslib", choices=["rateslib", "quantlib", "refstack"], help="the pricing stack: overlays configs/adapters/<stack>_swap.yaml")
    args = ap.parse_args(argv)
    todo = [BY_NAME[n] for n in args.only.split(",") if n] if args.only else list(RUNS)
    RESULTS.mkdir(exist_ok=True)
    rows: List[Dict[str, Any]] = []
    t0 = time.perf_counter()
    if not args.no_run:
        print(f"running {len(todo)} backtests with {args.jobs} worker processes ...", flush=True)
        with cf.ProcessPoolExecutor(max_workers=args.jobs) as ex:
            futs = {ex.submit(run_one, r, args.stack): r for r in todo}
            for f in cf.as_completed(futs):
                row = f.result()
                rows.append(row)
                if row.get("ok"):
                    print(f"  {row['name']:<34} {row['n_points']:>5} pts {row['elapsed_s']:>7.1f}s  net {row['total_pnl']:>14,.0f}  "
                          f"reconcile {row['reconcile_strict_ok']}  unexpl {row.get('unexpl_daily_pct', float('nan')):.3f}%", flush=True)
                else:
                    print(f"  {row['name']:<34} FAILED {row['error']}", flush=True)
        new = pd.DataFrame(rows)
        old_path = RESULTS / "swap_suite_summary.csv"
        if args.only and old_path.exists():
            old = pd.read_csv(old_path)
            new = pd.concat([old[~old["name"].isin(new["name"])], new], ignore_index=True)
        order = {r.name: i for i, r in enumerate(RUNS)}
        summary = new.assign(_o=new["name"].map(order)).sort_values("_o").drop(columns="_o").reset_index(drop=True)
        summary.to_csv(old_path, index=False)
    else:
        summary = pd.read_csv(RESULTS / "swap_suite_summary.csv")
    print(f"backtests done in {time.perf_counter() - t0:.0f}s; running checks ...", flush=True)
    ck, extra = run_checks(summary)
    checks = ck.frame()
    checks.to_csv(RESULTS / "swap_suite_checks.csv", index=False)
    tables = write_tables(summary, checks, extra)
    figs = [] if args.no_figures else figures(extra)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 30)
    ok = summary[summary["ok"]]
    print(ok[["name", "n_points", "total_pnl", "sharpe", "max_drawdown", "n_positions", "package_hit_rate", "layer_delta", "layer_roll",
              "layer_unexplained", "unexpl_daily_pct", "reconcile_strict_ok", "elapsed_s"]].to_string(index=False))
    print(checks.to_string(index=False, max_colwidth=90))
    print(f"wrote {RESULTS / 'swap_suite_summary.csv'}, {RESULTS / 'swap_suite_checks.csv'}, {tables}" + (f", {len(figs)} figures" if figs else ""))
    n_fail = int((checks["status"] == "FAIL").sum()) + int((~summary["ok"]).sum())
    print(f"{n_fail} FAILED checks/runs" if n_fail else "all gated checks PASS")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
