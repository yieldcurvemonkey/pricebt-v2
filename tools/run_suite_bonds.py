"""Rerun the US Treasury / cross-product strategy suite on the REAL fixtures, verify it and write the outputs.

    cd <project root>
    C:\\Users\\chris\\anaconda3\\envs\\stir\\python.exe tools/run_suite_bonds.py [--only s05,s07] [--skip-robustness] [--no-tearsheet] [--stack rateslib|quantlib|refstack]

Main runs (configs/suite): s05_ust_ct10_financed, s06_ust_2s10s_steepener, s07_swap_spread, s09_ust_intraday_mr (+ its 2026-03-09
single session), controls ctrl_s10b_ust_buy_and_hold and ctrl_s12b_ust_always_flat. Each writes results_new/<name>/ (parquet + manifest +
tearsheet.html/.png); `results/` (the recorded outputs of the pre-refactor suite) is never written and tasks/suite_reproduction.md compares the two.
The configs are library-neutral (ONE bond instrument spec `ust`, the bond token and side as terms; s07 adds the swap spec `usd_sofr_ois`); `--stack` names the
overlays (`configs/adapters/<stack>_bond.yaml`, plus `<stack>_swap.yaml` for s07) that set the factory and the pricer wrap (default rateslib).
Robustness variants (fill_lag 0/1, cost scale, notional and DV01 scaling, long/short mirror, a zero-price day under
on_error raise|record, the unfiltered bad tick, 2022 buy-and-hold) and the verification checks (independent P&L re-computation from the
price parquet, factor regressions, coupon sweep, intraday financing) write results_new/bond_suite/*.csv, results_new/bond_suite/checks.json
and results_new/bond_suite/tables.md. results_new/bond_suite_summary.csv has one row per run. Prints a summary table.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "src", ROOT / "tests", ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from pricebt import api  # noqa: E402
from pricebt.config.loader import build as build_cfg  # noqa: E402
from pricebt.results.report import df_to_markdown  # noqa: E402
from tools.swap_suite_support import overlays, par_panel  # noqa: E402

CFG = ROOT / "configs" / "suite"
RES = ROOT / "results_new"
OUT = RES / "bond_suite"
FIX = ROOT / "data" / "fixtures"
NY = "America/New_York"
FACE_COST = 1.5625e-4  # 1/64 point per side, per unit of face
PARTIAL_DAYS = ("2023-02-10", "2023-06-15", "2023-08-01", "2023-11-20", "2023-12-04")
UST_CALENDAR = "us_govt"  # the calendar of the snapshots the quote panels emit (the `calendar` of the shared conventions block)
SWAP_CONFIGS = ("s07_swap_spread.yaml",)  # configs that also define the swap instrument (and its pricer role): they take the swap overlay too


# ============================================================================ running
@dataclass
class Spec:
    name: str
    config: str
    sets: Tuple[str, ...] = ()
    kind: str = "main"  # main | control | robustness
    note: str = ""
    save: bool = False  # parquet + tearsheet under results/<name>
    expect_error: bool = False  # the run is EXPECTED to raise (data-unavailability demonstrations)


@dataclass
class Run:
    spec: Spec
    result: Any = None
    elapsed: float = float("nan")
    error: Optional[str] = None
    error_type: Optional[str] = None


def execute(spec: Spec, stack: str = "rateslib") -> Run:
    path = CFG / spec.config
    sets = ["backtest.progress.show=false", "outputs.dir=null", *spec.sets]
    cfg = api.load(path, sets=sets)
    kinds = ("swap", "bond") if spec.config in SWAP_CONFIGS else ("bond",)
    t0 = time.perf_counter()
    try:
        built = build_cfg(cfg, base_dir=path.parent, stack=[api.load(o) for o in overlays(stack, *kinds)])
        res = built.run()
    except Exception as e:  # noqa: BLE001  (a robustness variant may be EXPECTED to raise; recorded, never swallowed silently)
        return Run(spec, None, time.perf_counter() - t0, f"{type(e).__name__}: {e}", type(e).__name__)
    return Run(spec, res, time.perf_counter() - t0)


def save(run: Run, tearsheet: bool) -> Dict[str, str]:
    d = RES / run.spec.name
    d.mkdir(parents=True, exist_ok=True)
    run.result.to_parquet(d)
    out = {"dir": str(d.relative_to(ROOT))}
    if tearsheet:
        run.result.tearsheet(d / "tearsheet", formats=("html", "png"))
        out.update({"tearsheet_html": str((d / "tearsheet.html").relative_to(ROOT)), "tearsheet_png": str((d / "tearsheet.png").relative_to(ROOT))})
    return out


# ============================================================================ per-run numbers
def layer_numbers(res: Any) -> Dict[str, float]:
    att = res.attribution("layer")["pnl"]
    comp = res.attribution("component")["pnl"]
    lay = {k: float(att.get(k, 0.0)) for k in ("carry", "roll", "delta", "convexity", "unexplained")}
    gross = float(res.positions["pnl_gross"].sum()) if len(res.positions) else 0.0
    abs_layers = sum(abs(lay[k]) for k in ("carry", "roll", "delta", "convexity")) + abs(lay["unexplained"])
    eq = res.equity
    step_unexpl = eq["layer_unexplained"].diff().fillna(eq["layer_unexplained"]).abs().sum() if "layer_unexplained" in eq else 0.0
    step_gross = (eq["step_pnl"] - eq["tcost"].diff().fillna(eq["tcost"])).abs().sum()
    return {
        **{f"layer_{k}": v for k, v in lay.items()},
        "gross_pnl": gross, "financing": float(comp.get("financing", 0.0)), "cash_flows": float(comp.get("cash_flows", 0.0)),
        "price_component": float(comp.get("price", 0.0)),
        "unexpl_pct_gross": 100.0 * abs(lay["unexplained"]) / abs(gross) if gross else float("nan"),
        "unexpl_pct_abs_layers": 100.0 * abs(lay["unexplained"]) / abs_layers if abs_layers else float("nan"),
        "unexpl_pct_step_abs": 100.0 * step_unexpl / step_gross if step_gross else float("nan"),
    }


def headline(run: Run) -> Dict[str, Any]:
    row: Dict[str, Any] = {"run": run.spec.name, "kind": run.spec.kind, "config": f"configs/suite/{run.spec.config}", "sets": "; ".join(run.spec.sets),
                           "note": run.spec.note, "elapsed_s": round(run.elapsed, 2)}
    if run.result is None:
        row.update({"error": run.error})
        return row
    res = run.result
    s = res.summary_stats()
    eq = res.equity
    rep = res.reconcile()
    try:
        res.reconcile(strict=True)
        strict_ok = True
    except Exception:  # noqa: BLE001
        strict_ok = False
    row.update({
        "start": str(eq.index[0]), "end": str(eq.index[-1]), "n_points": len(eq), "ms_per_point": round(1e3 * run.elapsed / max(len(eq), 1), 2),
        "n_trades": int(s["n_trades"]), "n_closed_trades": int(s["n_closed"]), "total_pnl": float(s["total_pnl"]), "sharpe": float(s["sharpe"]),
        "max_drawdown": float(s["max_drawdown"]), "hit_rate": float(s["hit_rate"]), "trade_hit_rate": float(s["trade_hit_rate"]),
        "total_tcost": float(s["total_tcost"]), "n_errors": res.n_errors, "reconcile_ok": bool(rep.ok), "reconcile_strict_ok": strict_ok,
        "reconcile_max_err": float(rep.max_abs_error),
    })
    if len(res.positions):
        row.update(layer_numbers(res))
    return row


# ============================================================================ independent re-computations (no pricebt pricing code)
def fed_calendar() -> Tuple[set, np.ndarray]:
    obj = json.loads((FIX / "calendars" / "fed.json").read_text(encoding="utf8"))
    hol = {pd.Timestamp(h).date() for h in obj.get("holidays", ())}
    return hol, np.array(sorted(hol), dtype="datetime64[D]")


def add_bdays(d: dt.date, n: int, hol: set) -> dt.date:
    x = d
    k = 0
    while k < n:
        x += dt.timedelta(days=1)
        if x.weekday() < 5 and x not in hol:
            k += 1
    return x


def ct_resolve(ref: pd.DataFrame, bucket: str, asof: dt.date, hol: set) -> pd.Series:
    """The ARBS on-the-run rule re-implemented: roll = auction + 1 fed business day; eligible roll < asof, issue < asof, maturity >= asof;
    rank 0 = newest issue in the original-issue bucket."""
    t = ref[ref["oi"] == bucket].copy()
    t["roll"] = [add_bdays(a, 1, hol) if pd.notna(a) else i for a, i in zip(t["auction_date"], t["issue_date"])]
    e = t[(t["roll"] < asof) & (t["maturity_date"] >= asof) & (t["issue_date"] < asof)]
    return e.sort_values("issue_date", ascending=False, kind="stable").iloc[0]


def coupon_dates(issue: dt.date, maturity: dt.date) -> List[dt.date]:
    """Unadjusted semi-annual coupon dates generated backward from maturity (regular periods), plus the issue date as the first start."""
    out, k = [], 0
    while True:
        d = (pd.Timestamp(maturity) - pd.DateOffset(months=6 * k)).date()
        if d <= issue:
            break
        out.append(d)
        k += 1
    return sorted(out)


def accrued_actact(cpn: float, issue: dt.date, maturity: dt.date, settle: dt.date) -> float:
    """ACT/ACT (ICMA) accrued interest per 100 face at `settle` for a semi-annual bullet; 0 on a coupon date."""
    cds = coupon_dates(issue, maturity)
    nxt = next(d for d in cds if d > settle)
    k = cds.index(nxt)
    prev = cds[k - 1] if k > 0 else (pd.Timestamp(nxt) - pd.DateOffset(months=6)).date()
    return cpn / 2.0 * (settle - prev).days / (nxt - prev).days


def independent_s5(res: Any, n_positions: int = 3) -> Dict[str, Any]:
    """Re-compute the first `n_positions` monthly CT10 holds of S5 straight from the FedInvest parquet, the fiscaldata reference table and
    the SOFR fixings parquet: bond identity (own on-the-run rule), dirty = clean + own ACT/ACT accrued (settlement = trade date, as the
    engine), coupons paid in [entry, exit), financing -N/100 * dirty(prev) * SOFR(last fixing before prev date) * days/360 on each step."""
    px = pd.read_parquet(FIX / "ust" / "fedinvest_2010_2026.parquet", columns=["date", "cusip", "eod_price"])
    px["date"] = pd.to_datetime(px["date"]).dt.date
    ref = pd.read_parquet(FIX / "ust" / "reference_fiscaldata.parquet")
    for c in ("auction_date", "issue_date", "maturity_date"):
        ref[c] = pd.to_datetime(ref[c]).dt.date
    fx = pd.read_parquet(FIX / "fixings" / "USD-SOFR-1D.parquet")
    fx = pd.Series(fx["rate"].to_numpy(dtype=float) * 100.0, index=pd.to_datetime(fx["date"]).dt.date)
    hol, _ = fed_calendar()
    grid = [t.date() for t in res.equity.index]
    pos = res.positions.sort_values("entry_ts").head(n_positions)
    rows = []
    notional = 1e7
    last = res.equity.index[-1]
    for pid, p in pos.iterrows():
        d0 = pd.Timestamp(p["entry_ts"]).tz_convert(NY).date()
        d1 = (pd.Timestamp(p["exit_ts"]) if pd.notna(p["exit_ts"]) else last).tz_convert(NY).date()  # an open position: its last mark
        bond = ct_resolve(ref, "10-Year", d0, hol)
        cpn = float(bond["cpn"])
        days = [d for d in grid if d0 <= d <= d1]
        clean = px[(px.cusip == bond["cusip"]) & (px.date.isin(days))].set_index("date")["eod_price"].reindex(days)
        if clean.isna().any():
            raise AssertionError(f"missing clean price for {bond['cusip']} on {list(clean[clean.isna()].index)}")
        dirty = {d: float(clean[d]) + accrued_actact(cpn, bond["issue_date"], bond["maturity_date"], d) for d in days}
        fin = 0.0
        for a, b in zip(days[:-1], days[1:]):
            r = float(fx[fx.index < a].iloc[-1])
            fin -= notional / 100.0 * dirty[a] * r / 100.0 * (b - a).days / 360.0
        pays = [add_bdays(c - dt.timedelta(days=1), 1, hol) for c in coupon_dates(bond["issue_date"], bond["maturity_date"])]
        coupons = sum(notional * cpn / 200.0 for c in pays if d0 <= c < d1)
        price = notional / 100.0 * (dirty[d1] - dirty[d0])
        indep = price + coupons + fin
        rows.append({"position": pid, "cusip": bond["cusip"], "coupon": cpn, "entry": str(d0), "exit": str(d1), "n_steps": len(days) - 1,
                     "entry_pv_engine": float(p["entry_pv"]), "entry_pv_indep": notional / 100.0 * dirty[d0], "price_indep": price, "coupons_indep": coupons,
                     "financing_indep": fin, "financing_engine": float(p["financing"]), "pnl_indep": indep, "pnl_engine_gross": float(p["pnl_gross"]),
                     "diff": float(p["pnl_gross"]) - indep})
    t = pd.DataFrame(rows).set_index("position")
    tot_e, tot_i = float(t["pnl_engine_gross"].sum()), float(t["pnl_indep"].sum())
    return {"table": t, "total_engine": tot_e, "total_indep": tot_i, "abs_diff": abs(tot_e - tot_i), "rel_diff": abs(tot_e - tot_i) / max(abs(tot_i), 1.0),
            "max_abs_diff_per_position": float(t["diff"].abs().max()), "entry_pv_max_abs_diff": float((t["entry_pv_engine"] - t["entry_pv_indep"]).abs().max())}


# ============================================================================ factor regressions (sign / exposure checks)
class Yields:
    """Held-bond yields and swap par rates recomputed from the providers (NOT from the engine's marks): the quote panel's yield through the rateslib adapter's
    wrapped pricer (a `bond_ytm` lookup), the par rate through RAW rateslib on the curve snapshot (tools.swap_suite_support.par_panel)."""

    def __init__(self, start: str, end: str):
        from pricebt.contrib.rateslib import wrap
        from support.ust_eod import UstEod

        self.fi = UstEod(window=(start, end), partial_day_min_missing=10)
        self.cs = None
        self._cs_args = (start, end)
        self._wrap = wrap

    def curve(self) -> Any:
        if self.cs is None:
            from support.curves import CurveStore

            self.cs = CurveStore("USD-SOFR-1D-CITIVELOEXCEL", window=self._cs_args, time={"mode": "asof", "max_staleness": "12h"})
        return self.cs

    def ytm(self, ts: pd.Timestamp, cusip: str) -> float:
        return float(self._wrap(self.fi.get_pricer(ts)).lookup("bond_ytm", bond=cusip))

    def par10(self, ts: pd.Timestamp) -> float:
        return float(par_panel([ts], ("10Y",), store=self.curve()).iloc[0]["10Y"])


ROLL_ACTIONS = ("roll_ct10", "long_2y", "long_ct10")  # the monthly entry actions of S5 / S6 / S7 (re-hedges are NOT rolls)


def roll_times(res: Any) -> List[pd.Timestamp]:
    tr = res.trades
    return sorted(set(tr.loc[(tr["kind"] == "open") & tr["action"].isin(ROLL_ACTIONS), "ts"]))


def held_cusips(res: Any, token: str, yl: Yields) -> pd.Series:
    """For every step ending at t: the CUSIP of `token` resolved at the last monthly roll strictly before t."""
    opens = roll_times(res)
    idx = res.equity.index
    out = {}
    for t in idx[1:]:
        roll = max((o for o in opens if o < t), default=None)
        out[t] = None if roll is None else yl.fi.aliases(roll.date())[token]
    return pd.Series(out)


def regress(y: np.ndarray, X: np.ndarray) -> Dict[str, Any]:
    A = np.column_stack([np.ones(len(y)), X])
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    fit = A @ coef
    ss_res = float(((y - fit) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    return {"intercept": float(coef[0]), "betas": [float(c) for c in coef[1:]], "r2": 1.0 - ss_res / ss_tot if ss_tot else float("nan"), "n": int(len(y))}


def factor_check(res: Any, kind: str, yl: Yields) -> Dict[str, Any]:
    """Regress each step's P&L (ex costs, roll dates dropped) on bp changes of the held bonds' yields / the 10Y swap par rate."""
    eq = res.equity
    step = (eq["step_pnl"] - eq["tcost"].diff().fillna(eq["tcost"])).iloc[1:]
    opens = set(roll_times(res))
    keep = [t for t in step.index if t not in opens]  # a roll step mixes two positions
    prev = dict(zip(eq.index[1:], eq.index[:-1]))
    rows = []
    c10 = held_cusips(res, "CT10", yl)
    c2 = held_cusips(res, "CT2", yl) if kind == "s06" else None
    for t in keep:
        t0 = prev[t]
        if c10.get(t) is None:
            continue
        dy10 = 100.0 * (yl.ytm(t, c10[t]) - yl.ytm(t0, c10[t]))
        if kind == "s05":
            rows.append((step[t], dy10))
        elif kind == "s06":
            dy2 = 100.0 * (yl.ytm(t, c2[t]) - yl.ytm(t0, c2[t]))
            rows.append((step[t], dy10 - dy2, 0.5 * (dy10 + dy2)))
        else:
            ds = 100.0 * (yl.par10(t) - yl.par10(t0))
            rows.append((step[t], ds - dy10, 0.5 * (ds + dy10)))
    a = np.array(rows, dtype=float)
    fit = regress(a[:, 0], a[:, 1:])
    names = {"s05": ["d_y10_bp"], "s06": ["d_slope_bp (y10-y2)", "d_level_bp"], "s07": ["d_spread_bp (s10-y10)", "d_level_bp"]}[kind]
    fit["factors"] = names
    return fit


# ============================================================================ strategy-specific checks
def coupon_check(res: Any) -> Dict[str, Any]:
    """Buy-and-hold: every coupon is swept as cash exactly once, on the first point after its payment date, for cpn/2 * face; the step
    P&L and the carry layer show no coupon-sized jump on those steps (the mark is smooth through the coupon)."""
    eq = res.equity
    flows = eq["flows_cum"].diff().fillna(eq["flows_cum"])
    hits = flows[flows.abs() > 1e-6]
    carry = eq["layer_carry"].diff().fillna(eq["layer_carry"])
    med = float(carry.abs().median())
    step = eq["step_pnl"]
    fin = eq["financing_cum"].diff().fillna(0.0)
    delta = eq["layer_delta"].diff().fillna(eq["layer_delta"])
    conv = eq["layer_convexity"].diff().fillna(eq["layer_convexity"])
    rows = []
    for t, amt in hits.items():
        rows.append({"ts": str(t), "cash": float(amt), "step_pnl": float(step[t]), "step_ex_delta_conv": float(step[t] - delta[t] - conv[t]),
                     "carry_layer_step": float(carry[t]), "financing_step": float(fin[t])})
    return {"n_coupon_flows": int(len(hits)), "amounts": sorted({round(float(a), 6) for a in hits}), "median_abs_carry_step": med,
            "max_abs_carry_on_coupon_steps": float(carry[hits.index].abs().max()) if len(hits) else 0.0, "rows": rows}


def s09_checks(res: Any) -> Dict[str, Any]:
    from support.common import load_fixings

    eq = res.equity
    idx = eq.index
    loc = idx.tz_convert(NY)
    out: Dict[str, Any] = {}
    out["grid_weekend_points"] = int(sum(t.weekday() >= 5 for t in loc))
    out["grid_time_range"] = [str(min(t.time() for t in loc)), str(max(t.time() for t in loc))]
    out["n_sessions"] = int(len(set(t.date() for t in loc)))
    pos = res.positions
    ent = pd.to_datetime(pos["entry_ts"]).dt.tz_convert(NY)
    ext = pd.to_datetime(pos["exit_ts"]).dt.tz_convert(NY)
    out["all_flat_by_1600_same_day"] = bool(((ext.dt.date == ent.dt.date) & (ext.dt.time <= dt.time(16, 0))).all())
    out["n_positions"] = int(len(pos))
    out["exit_reasons"] = pos["exit_reason"].value_counts().to_dict()
    out["n_open_at_end"] = int((eq["n_positions"].iloc[-1]))
    # financing on elapsed seconds: per minute step with one position held over (t0, t]: fin = -pv_prev * r * seconds/(360*86400)
    fx = load_fixings(FIX / "fixings" / "USD-SOFR-1D.parquet")
    fin = eq["financing_cum"].diff()
    pv = eq["positions_value"]
    n = eq["n_positions"]
    errs, same_day_carry_err, moved, held = [], [], 0, 0
    carry = eq["layer_carry"].diff() if "layer_carry" in eq else None
    trade_ts = set(res.trades["ts"])
    for k in range(1, len(idx)):
        t0, t1 = idx[k - 1], idx[k]
        if n.iloc[k - 1] != 1 or n.iloc[k] != 1 or t1 in trade_ts or t0 in trade_ts or loc[k].date() != loc[k - 1].date():
            continue
        held += 1
        moved += int(pv.iloc[k] != pv.iloc[k - 1])
        r = float(fx[fx.index < pd.Timestamp(loc[k - 1].date())].iloc[-1])
        want = -pv.iloc[k - 1] * r / 100.0 * (t1 - t0).total_seconds() / (360.0 * 86400.0)
        errs.append(abs(fin.iloc[k] - want) / max(abs(want), 1e-12))
        if carry is not None:
            same_day_carry_err.append(abs(carry.iloc[k] - fin.iloc[k]))
    out["held_minute_steps"] = held
    out["share_minute_steps_mark_moved"] = moved / held if held else float("nan")
    out["financing_seconds_max_rel_err"] = float(max(errs)) if errs else float("nan")
    out["intraday_carry_minus_financing_max_abs"] = float(max(same_day_carry_err)) if same_day_carry_err else float("nan")
    return out


def date_granular_accrual_check() -> Dict[str, Any]:
    """The minute-panel pricers of one session share the reference date: RLBond accrued is identical at 09:30 and 15:30 while ytm/dirty move."""
    from pricebt.contrib import rateslib as RL
    from support.ust_minute import UstMinute

    wb = UstMinute(fixings="auto", outlier_bp=5.0)
    p1, p2 = RL.wrap(wb.get_pricer(pd.Timestamp("2026-03-09 09:30", tz=NY))), RL.wrap(wb.get_pricer(pd.Timestamp("2026-03-09 15:30", tz=NY)))
    conv = {**RL.UST_CONVENTIONS, "calendar": UST_CALENDAR}
    b = RL.bond.factory(p1, p1.ts, terms={"side": "buy", "direction": 1, "security": "CT10", "notional": 1e7, "extras": {"repo": {"gc_rate": "pricer"}}}, conventions=conv).obj
    m1, m2 = b.mark(p1), b.mark(p2)
    p3 = RL.wrap(wb.get_pricer(pd.Timestamp("2026-03-10 09:30", tz=NY)))
    m3 = b.mark(p3)
    return {"accrued_0930": m1["accrued"], "accrued_1530": m2["accrued"], "accrued_next_day_0930": m3["accrued"], "ytm_0930": m1["ytm"], "ytm_1530": m2["ytm"],
            "dirty_0930": m1["dirty"], "dirty_1530": m2["dirty"], "repo_fixing_0930": p1.lookup("repo_fixing")}


def mirror_check(a: Any, b: Any) -> Dict[str, Any]:
    s = a.equity["equity"] + b.equity["equity"]
    fin = a.equity["financing_cum"] + b.equity["financing_cum"]
    return {"max_abs_sum_equity": float(s.abs().max()), "max_abs_long_equity": float(a.equity["equity"].abs().max()),
            "rel": float(s.abs().max() / max(a.equity["equity"].abs().max(), 1.0)), "max_abs_sum_financing": float(fin.abs().max()),
            "long_financing": float(a.equity["financing_cum"].iloc[-1]), "short_financing": float(b.equity["financing_cum"].iloc[-1])}


def scaling_check(base: Any, big: Any, k: float) -> Dict[str, Any]:
    d = big.equity["equity"] - k * base.equity["equity"]
    return {"factor": k, "max_abs_dev": float(d.abs().max()), "rel": float(d.abs().max() / max(base.equity["equity"].abs().max(), 1.0)),
            "pnl_base": float(base.pnl.iloc[-1]), "pnl_scaled": float(big.pnl.iloc[-1]), "tcost_base": float(base.equity["tcost"].iloc[-1]),
            "tcost_scaled": float(big.equity["tcost"].iloc[-1])}


def pnl_between(res: Any, a: str, b: str) -> float:
    eq = res.pnl
    loc = eq.index.tz_convert(NY)
    before = eq[loc.date < pd.Timestamp(a).date()]
    upto = eq[loc.date <= pd.Timestamp(b).date()]
    return float(upto.iloc[-1] - (before.iloc[-1] if len(before) else 0.0))


def mdtable(df: pd.DataFrame, fmt: str = "{:,.2f}", **kw: Any) -> str:
    return df_to_markdown(df, floatfmt=fmt, **kw)


# ============================================================================ specs
def specs(only: Optional[Sequence[str]], robustness: bool) -> List[Spec]:
    cost0 = "strategy.triggers.0.actions.0.transaction_cost.scaling_level"
    terms0 = "strategy.triggers.0.actions.0.priceables.terms"
    main = [
        Spec("s05_ust_ct10_financed", "s05_ust_ct10_financed.yaml", save=True),
        Spec("s06_ust_2s10s_steepener", "s06_ust_2s10s_steepener.yaml", save=True),
        Spec("s07_swap_spread", "s07_swap_spread.yaml", save=True),
        Spec("s09_ust_intraday_mr", "s09_ust_intraday_mr.yaml", save=True),
        Spec("s09_ust_intraday_mr_20260309", "s09_ust_intraday_mr.yaml", ("backtest.grid.start=2026-03-09", "backtest.grid.end=2026-03-09"), save=True,
             note="single session (first EDT session after the DST change)"),
        Spec("ctrl_s10b_ust_buy_and_hold", "ctrl_s10b_ust_buy_and_hold.yaml", kind="control", save=True),
        Spec("ctrl_s12b_ust_always_flat", "ctrl_s12b_ust_always_flat.yaml", kind="control", save=True),
    ]
    rob = [
        Spec("s05_fill_lag1", "s05_ust_ct10_financed.yaml", ("backtest.fill_lag=1",), "robustness", "fill_lag 1"),
        Spec("s06_fill_lag1", "s06_ust_2s10s_steepener.yaml", ("backtest.fill_lag=1",), "robustness", "fill_lag 1"),
        *[Spec(f"s05_cost_x{k:g}", "s05_ust_ct10_financed.yaml", (f"{cost0}={FACE_COST * k!r}",), "robustness", f"cost scale {k:g}") for k in (0.0, 0.5, 2.0)],
        *[Spec(f"s09_cost_x{k:g}", "s09_ust_intraday_mr.yaml", (f"{cost0}={7.8125e-5 * k!r}", f"strategy.triggers.1.actions.0.transaction_cost.scaling_level={7.8125e-5 * k!r}"),
               "robustness", f"cost scale {k:g}") for k in (0.0, 2.0)],
        Spec("s05_notional_x2", "s05_ust_ct10_financed.yaml", (f"{terms0}.notional=2e7",), "robustness", "face 20mm"),
        Spec("s06_dv01_x2", "s06_ust_2s10s_steepener.yaml", ("strategy.triggers.0.actions.1.scaling_level=-20000", "strategy.triggers.1.band=600"), "robustness",
             "DV01 target -20k and band 600"),
        Spec("s05_long_nocost", "s05_ust_ct10_financed.yaml", (f"{cost0}=0.0",), "robustness", "mirror leg: long, no cost"),
        Spec("s05_short_nocost", "s05_ust_ct10_financed.yaml", (f"{cost0}=0.0", f"{terms0}.side=sell"), "robustness", "mirror leg: short, no cost"),
        Spec("s05_zero_price_raise", "s05_ust_ct10_financed.yaml",
             ("backtest.grid.start=2026-06-01", "backtest.grid.end=2026-08-17", "market.mdps.ust.kwargs.window=[2026-05-01, 2026-08-21]",
              "strategy.triggers.0.start_date=2026-06-09"), "robustness", "roll on the zero-price day 2026-07-09, on_error raise", expect_error=True),
        Spec("s05_zero_price_record", "s05_ust_ct10_financed.yaml",
             ("backtest.grid.start=2026-06-01", "backtest.grid.end=2026-08-17", "market.mdps.ust.kwargs.window=[2026-05-01, 2026-08-21]",
              "strategy.triggers.0.start_date=2026-06-09", "backtest.on_error=record", "backtest.measures=[]"), "robustness",
             "same, on_error record (measures off: see the next run)"),
        Spec("s05_zero_price_record_measures", "s05_ust_ct10_financed.yaml",
             ("backtest.grid.start=2026-06-01", "backtest.grid.end=2026-08-17", "market.mdps.ust.kwargs.window=[2026-05-01, 2026-08-21]",
              "strategy.triggers.0.start_date=2026-06-09", "backtest.on_error=record"), "robustness",
             "on_error record WITH the dv01 measure column: records the errors and continues (Engine._record is guarded)"),
        Spec("s05_partial_day_off", "s05_ust_ct10_financed.yaml",
             ("backtest.grid.start=2023-07-03", "backtest.grid.end=2023-08-31", "market.mdps.ust.kwargs.partial_day_min_missing=null",
              "strategy.triggers.0.calendar=null"), "robustness", "partial-day filter OFF: the held CT10 has no quote on 2023-08-01", expect_error=True),
        Spec("s09_20260312_0800_filtered", "s09_ust_intraday_mr.yaml", ("backtest.grid.start=2026-03-12", "backtest.grid.end=2026-03-12",
                                                                        "backtest.session.open=08:00"), "robustness",
             "session from 08:00 so the 08:43 spike is on the grid; bad-tick filter ON (default)"),
        Spec("s09_20260312_0800_unfiltered", "s09_ust_intraday_mr.yaml", ("backtest.grid.start=2026-03-12", "backtest.grid.end=2026-03-12",
                                                                          "backtest.session.open=08:00", "market.mdps.ust.kwargs.outlier_bp=null"), "robustness",
             "same, bad-tick filter OFF"),
        Spec("ctrl_s10b_2022_unfinanced", "ctrl_s10b_ust_buy_and_hold.yaml",
             ("backtest.grid.start=2022-01-03", "backtest.grid.end=2022-12-30", "strategy.triggers.0.dates=[2022-01-03]", f"{terms0}.extras.repo={{}}"),
             "robustness", "CT10 bought 2022-01-03, held to 2022-12-30, no financing (total return)"),
        Spec("ctrl_s10b_2022_financed", "ctrl_s10b_ust_buy_and_hold.yaml",
             ("backtest.grid.start=2022-01-03", "backtest.grid.end=2022-12-30", "strategy.triggers.0.dates=[2022-01-03]"), "robustness", "same, financed at SOFR"),
        Spec("ctrl_s10b_fill_lag1", "ctrl_s10b_ust_buy_and_hold.yaml", ("backtest.fill_lag=1",), "robustness", "fill_lag 1 (terminal equity must not change)"),
    ]
    out = main + (rob if robustness else [])
    if only:
        out = [s for s in out if any(s.name.startswith(o) for o in only)]
    return out


# ============================================================================ main
def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", default="", help="comma-separated run-name prefixes")
    ap.add_argument("--skip-robustness", action="store_true")
    ap.add_argument("--no-tearsheet", action="store_true")
    ap.add_argument("--skip-checks", action="store_true", help="skip the (slow) factor regressions")
    ap.add_argument("--stack", default="rateslib", choices=["rateslib", "quantlib", "refstack"], help="the pricing stack: overlays configs/adapters/<stack>_bond.yaml (+ _swap for s07)")
    a = ap.parse_args(argv)
    only = [x.strip() for x in a.only.split(",") if x.strip()]
    OUT.mkdir(parents=True, exist_ok=True)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)

    runs: Dict[str, Run] = {}
    rows = []
    files: Dict[str, Any] = {}
    for sp in specs(only, not a.skip_robustness):
        print(f"[run] {sp.name} ...", flush=True)
        r = execute(sp, a.stack)
        runs[sp.name] = r
        if r.result is not None and sp.save:
            files[sp.name] = save(r, not a.no_tearsheet)
        row = headline(r)
        rows.append(row)
        tail = f"pnl={row.get('total_pnl', float('nan')):,.0f} reconcile={row.get('reconcile_ok')} errors={row.get('n_errors')}" if r.result is not None else f"ERROR {r.error}"
        print(f"      {r.elapsed:6.1f}s  {tail}", flush=True)

    summary = pd.DataFrame(rows).set_index("run")
    tag = "" if not only else "_partial"  # a filtered rerun never overwrites the full-suite outputs
    summary.to_csv(RES / f"bond_suite_summary{tag}.csv")
    checks: Dict[str, Any] = {"files": files}
    tables: List[str] = ["# Bond suite tables (generated by tools/run_suite_bonds.py)", ""]

    def have(*names: str) -> bool:
        return all(n in runs and runs[n].result is not None for n in names)

    # ---- headline + attribution tables for the main runs
    main_names = [n for n, r in runs.items() if r.spec.kind in ("main", "control") and r.result is not None]
    if main_names:
        cols = ["start", "end", "n_points", "n_trades", "n_closed_trades", "total_pnl", "sharpe", "max_drawdown", "hit_rate", "trade_hit_rate",
                "total_tcost", "financing", "unexpl_pct_gross", "unexpl_pct_abs_layers", "reconcile_strict_ok", "elapsed_s", "ms_per_point"]
        head = summary.loc[main_names, [c for c in cols if c in summary.columns]]
        tables += ["## Headline", "", mdtable(head), ""]
        for n in main_names:
            res = runs[n].result
            att = res.attribution("layer")
            comp = res.attribution("component")
            att.to_csv(OUT / f"attribution_layer_{n}.csv")
            comp.to_csv(OUT / f"attribution_component_{n}.csv")
            tables += [f"## {n}", "", "Attribution by layer (share = of net total):", "", mdtable(att, "{:,.4f}"), "",
                       "By component:", "", mdtable(comp, "{:,.4f}"), ""]
            for by in ("tag", "template"):
                t = res.attribution(by)
                if len(t) > 2:
                    tables += [f"By {by}:", "", mdtable(t), ""]
            if res.n_errors:
                tables += ["Errors:", "", mdtable(res.errors.astype(str), index=False), ""]

    # ---- controls
    if have("ctrl_s12b_ust_always_flat"):
        r = runs["ctrl_s12b_ust_always_flat"].result
        eq = r.equity
        checks["s12b_flat"] = {"equity_all_exactly_zero": bool((eq["equity"] == 0.0).all()), "cash_all_zero": bool((eq["cash"] == 0.0).all()),
                               "tcost_all_zero": bool((eq["tcost"] == 0.0).all()), "n_trades": int(len(r.trades)), "n_orders": int(len(r.orders)),
                               "n_positions": int(len(r.positions)), "n_points": int(len(eq))}
    if have("ctrl_s10b_ust_buy_and_hold"):
        checks["s10b_coupons"] = coupon_check(runs["ctrl_s10b_ust_buy_and_hold"].result)
        pd.DataFrame(checks["s10b_coupons"]["rows"]).to_csv(OUT / "s10b_coupon_steps.csv", index=False)
    if have("ctrl_s10b_ust_buy_and_hold", "ctrl_s10b_fill_lag1"):
        a0, a1 = runs["ctrl_s10b_ust_buy_and_hold"].result, runs["ctrl_s10b_fill_lag1"].result
        checks["s10b_fill_lag"] = {"pnl_lag0": float(a0.pnl.iloc[-1]), "pnl_lag1": float(a1.pnl.iloc[-1]), "diff": float(a1.pnl.iloc[-1] - a0.pnl.iloc[-1])}

    # ---- robustness
    rob_rows = []
    for base, var in (("s05_ust_ct10_financed", "s05_fill_lag1"), ("s06_ust_2s10s_steepener", "s06_fill_lag1")):
        if have(base, var):
            b, v = runs[base].result, runs[var].result
            fin0, fin1 = float(b.equity["financing_cum"].iloc[-1]), float(v.equity["financing_cum"].iloc[-1])
            fl0, fl1 = float(b.equity["flows_cum"].iloc[-1]), float(v.equity["flows_cum"].iloc[-1])
            rob_rows.append({"check": f"fill_lag 0 vs 1 ({base})", "lag0_pnl": float(b.pnl.iloc[-1]), "lag1_pnl": float(v.pnl.iloc[-1]),
                             "diff": float(v.pnl.iloc[-1] - b.pnl.iloc[-1]), "financing_diff": fin1 - fin0, "cash_flows_diff": fl1 - fl0,
                             "tcost_diff": float(v.equity["tcost"].iloc[-1] - b.equity["tcost"].iloc[-1]), "lag0_fills": int(len(b.trades)),
                             "lag1_fills": int(len(v.trades)), "unfilled_at_end": int((v.events["kind"] == "unfilled_order_at_end").sum()) if len(v.events) else 0})
    checks["fill_lag"] = rob_rows
    if rob_rows:
        tables += ["## fill_lag 0 vs 1", "", mdtable(pd.DataFrame(rob_rows).set_index("check")), ""]

    cost_rows = []
    for fam, base, scales in (("s05", "s05_ust_ct10_financed", (0.0, 0.5, 1.0, 2.0)), ("s09", "s09_ust_intraday_mr", (0.0, 1.0, 2.0))):
        for k in scales:
            n = base if k == 1.0 else f"{fam}_cost_x{k:g}"
            if have(n):
                res = runs[n].result
                cost_rows.append({"family": fam, "scale": k, "net_pnl": float(res.pnl.iloc[-1]), "tcost": float(res.equity["tcost"].iloc[-1]),
                                  "gross_pnl": float(res.pnl.iloc[-1] - res.equity["tcost"].iloc[-1]), "n_positions": int(len(res.positions))})
    if cost_rows:
        ct = pd.DataFrame(cost_rows)
        mono = {}
        for fam, g in ct.groupby("family"):
            g = g.sort_values("scale")
            mono[fam] = {"net_strictly_decreasing": bool((np.diff(g["net_pnl"].to_numpy()) < 0).all()),
                         "gross_identical": bool(np.ptp(g["gross_pnl"].to_numpy()) <= 1e-6 * max(1.0, g["gross_pnl"].abs().max())),
                         "gross_ptp": float(np.ptp(g["gross_pnl"].to_numpy()))}
        checks["cost_sensitivity"] = {"rows": cost_rows, "monotonic": mono}
        tables += ["## Transaction-cost sensitivity", "", mdtable(ct.set_index(["family", "scale"])), "", f"monotonic: `{json.dumps(mono)}`", ""]

    if have("s05_ust_ct10_financed", "s05_notional_x2"):
        checks["s05_notional_x2"] = scaling_check(runs["s05_ust_ct10_financed"].result, runs["s05_notional_x2"].result, 2.0)
    if have("s06_ust_2s10s_steepener", "s06_dv01_x2"):
        checks["s06_dv01_x2"] = scaling_check(runs["s06_ust_2s10s_steepener"].result, runs["s06_dv01_x2"].result, 2.0)
    if have("s05_long_nocost", "s05_short_nocost"):
        checks["s05_mirror"] = mirror_check(runs["s05_long_nocost"].result, runs["s05_short_nocost"].result)
    sc = [{"check": k, **{kk: vv for kk, vv in v.items() if not isinstance(vv, (list, dict))}} for k, v in checks.items() if k in ("s05_notional_x2", "s06_dv01_x2", "s05_mirror")]
    if sc:
        tables += ["## Scaling and mirror", "", mdtable(pd.DataFrame(sc).set_index("check"), "{:,.6g}"), ""]

    zp = {}
    for n in ("s05_zero_price_raise", "s05_zero_price_record", "s05_zero_price_record_measures", "s05_partial_day_off"):
        if n in runs:
            r = runs[n]
            d = {"raised": r.error_type, "message": (r.error or "")[:300]}
            if r.result is not None:
                res = r.result
                d.update({"n_errors": res.n_errors, "errors": res.errors.astype(str).to_dict("records")[:8], "pnl": float(res.pnl.iloc[-1]),
                          "positions": res.positions[["entry_ts", "exit_ts", "exit_reason"]].astype(str).to_dict("records"),
                          "grid_has_zero_days": any(str(t.date()) in ("2026-07-09", "2026-07-10", "2026-07-13") for t in res.equity.index.tz_convert(NY))})
            zp[n] = d
    checks["zero_price_and_partial_days"] = zp

    if have("s09_20260312_0800_filtered", "s09_20260312_0800_unfiltered"):
        f, u = runs["s09_20260312_0800_filtered"].result, runs["s09_20260312_0800_unfiltered"].result
        win = lambda r: r.trades[(r.trades["ts"] >= pd.Timestamp("2026-03-12 08:40", tz=NY)) & (r.trades["ts"] <= pd.Timestamp("2026-03-12 09:30", tz=NY))]  # noqa: E731
        eqw = lambda r: r.equity.loc[pd.Timestamp("2026-03-12 08:41", tz=NY):pd.Timestamp("2026-03-12 08:48", tz=NY), ["equity", "step_pnl", "n_positions"]]  # noqa: E731
        checks["s09_bad_tick"] = {"pnl_filtered": float(f.pnl.iloc[-1]), "pnl_unfiltered": float(u.pnl.iloc[-1]), "n_pos_filtered": int(len(f.positions)),
                                  "n_pos_unfiltered": int(len(u.positions)), "filtered_trades_0840_0930": win(f).astype(str).to_dict("records"),
                                  "unfiltered_trades_0840_0930": win(u).astype(str).to_dict("records"),
                                  "unfiltered_equity_0841_0848": eqw(u).reset_index().astype(str).to_dict("records")}
    if have("ctrl_s10b_2022_unfinanced", "ctrl_s10b_2022_financed"):
        u, f = runs["ctrl_s10b_2022_unfinanced"].result, runs["ctrl_s10b_2022_financed"].result
        entry = float(u.positions["entry_pv"].iloc[0])
        checks["sanity_2022"] = {"ct10_entry_value": entry, "pnl_unfinanced": float(u.pnl.iloc[-1]), "total_return_unfinanced_pct": 100.0 * float(u.pnl.iloc[-1]) / entry,
                                 "pnl_financed": float(f.pnl.iloc[-1]), "excess_return_financed_pct": 100.0 * float(f.pnl.iloc[-1]) / entry}
    if have("s05_ust_ct10_financed"):
        s5 = runs["s05_ust_ct10_financed"].result
        checks.setdefault("sanity_2022", {})["s05_pnl_2022"] = pnl_between(s5, "2022-01-01", "2022-12-31")
        checks["s05_by_year"] = {str(y): pnl_between(s5, f"{y}-01-01", f"{y}-12-31") for y in range(2018, 2026)}
        ind = independent_s5(s5, 3)
        ind["table"].to_csv(OUT / "s05_independent_crosscheck.csv")
        tables += ["## Independent cross-check (S5 first 3 monthly holds)", "", mdtable(ind["table"].T, "{:,.4f}"), "",
                   f"total engine {ind['total_engine']:,.4f} vs independent {ind['total_indep']:,.4f}: |diff| {ind['abs_diff']:.6f} ({ind['rel_diff']:.2e} rel)", ""]
        checks["s05_independent"] = {k: v for k, v in ind.items() if k != "table"}
    for n in ("s09_ust_intraday_mr", "s09_ust_intraday_mr_20260309"):
        if have(n):
            checks[f"{n}_checks"] = s09_checks(runs[n].result)
    checks["date_granular_accrual"] = date_granular_accrual_check()

    if not a.skip_checks:
        for kind, n in (("s05", "s05_ust_ct10_financed"), ("s06", "s06_ust_2s10s_steepener"), ("s07", "s07_swap_spread")):
            if have(n):
                print(f"[check] factor regression {n} ...", flush=True)
                res = runs[n].result
                yl = Yields(str(res.equity.index[0].date() - dt.timedelta(days=40)), str(res.equity.index[-1].date()))
                checks[f"{kind}_regression"] = factor_check(res, kind, yl)
        reg = [{"run": k, "factors": ", ".join(v["factors"]), "betas": ", ".join(f"{b:,.1f}" for b in v["betas"]), "r2": v["r2"], "n": v["n"]}
               for k, v in checks.items() if k.endswith("_regression")]
        if reg:
            tables += ["## Factor regressions of step P&L (ex costs, roll steps dropped)", "", mdtable(pd.DataFrame(reg).set_index("run"), "{:,.4f}"), ""]

    # ---- comparison figure
    try:
        from pricebt.results.tearsheet import comparison_figure

        figs = {n: runs[n].result for n in ("s05_ust_ct10_financed", "s06_ust_2s10s_steepener", "s07_swap_spread", "ctrl_s10b_ust_buy_and_hold") if have(n)}
        if figs:
            (OUT / f"comparison_pnl{tag}.png").write_bytes(comparison_figure(figs))
            checks["files"]["comparison_png"] = str((OUT / f"comparison_pnl{tag}.png").relative_to(ROOT))
    except Exception as e:  # noqa: BLE001
        checks["files"]["comparison_png_error"] = repr(e)

    (OUT / f"checks{tag}.json").write_text(json.dumps(checks, indent=2, default=str), encoding="utf8")
    (OUT / f"tables{tag}.md").write_text("\n".join(tables), encoding="utf8")
    show = [c for c in ("kind", "n_points", "total_pnl", "sharpe", "max_drawdown", "n_trades", "hit_rate", "total_tcost", "unexpl_pct_gross",
                        "reconcile_strict_ok", "n_errors", "elapsed_s", "ms_per_point", "error") if c in summary.columns]
    print(summary[show].to_string(float_format=lambda v: f"{v:,.2f}"))
    print(f"\nwrote {RES / f'bond_suite_summary{tag}.csv'}, {OUT / f'checks{tag}.json'}, {OUT / f'tables{tag}.md'}")
    failed = [n for n, r in runs.items() if (r.result is None) != r.spec.expect_error]
    bad_rec = list(summary.index[summary["reconcile_strict_ok"].eq(False)]) if "reconcile_strict_ok" in summary.columns else []
    if failed or bad_rec:
        print(f"UNEXPECTED: runs whose raise/no-raise outcome is wrong {failed}, reconcile failures {bad_rec}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
