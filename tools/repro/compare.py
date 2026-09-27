"""Compare a new run directory with a recorded one (equity, trades, positions, layers). Used by the reproduction table.

    from compare import compare_dirs, selftest
    compare_dirs(old_dir, new_dir, end=None)  -> dict of metrics

`end` truncates the RECORDED frames to rows with ts <= end (a short-window run compared with the corresponding prefix of a full recorded run).
Row-aligned quantities are compared on the inner join of indices and the ROW COUNTS are reported (a different grid is a structural difference).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

LAYERS = ("carry", "roll", "delta", "convexity", "unexplained")


def load(d: Path) -> Dict[str, pd.DataFrame]:
    d = Path(d)
    out = {n: pd.read_parquet(d / f"{n}.parquet") for n in ("equity", "trades", "positions", "layers", "orders", "events", "errors")}
    return out


def _cut(df: pd.DataFrame, col: Optional[str], end: Optional[pd.Timestamp]) -> pd.DataFrame:
    if end is None or df.empty:
        return df
    s = df.index if col is None else pd.DatetimeIndex(df[col])
    return df[np.asarray(s <= end)]


def _mx(a: np.ndarray, b: np.ndarray, scale: float) -> Dict[str, float]:
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    both_nan = np.isnan(a) & np.isnan(b)
    d = np.abs(a - b)
    d[both_nan] = 0.0
    n_nan_mismatch = int((np.isnan(a) ^ np.isnan(b)).sum())
    mx = float(np.nanmax(d)) if d.size else 0.0
    return {"max_abs": mx, "max_rel_to_scale": mx / scale if scale else float("nan"), "nan_mismatch": n_nan_mismatch}


def compare_dirs(old: Path, new: Path, end: Optional[str] = None) -> Dict[str, Any]:
    O, N = load(old), load(new)
    endts = None if end is None else pd.Timestamp(end, tz="America/New_York") + pd.Timedelta(hours=23, minutes=59)
    O = {k: _cut(v, None if k == "equity" else ("ts" if k in ("trades", "orders", "events", "errors") else "entry_ts" if k == "positions" else None), endts) if k != "layers" else v for k, v in O.items()}
    res: Dict[str, Any] = {}
    oe, ne = O["equity"], N["equity"]
    res["rows_old"], res["rows_new"] = len(oe), len(ne)
    same_idx = len(oe) == len(ne) and bool((oe.index == ne.index).all())
    res["equity_index_identical"] = same_idx
    j = oe.index.intersection(ne.index)
    oe, ne = oe.loc[j], ne.loc[j]
    scale = max(float(oe["equity"].abs().max()), 1.0)
    res["equity_scale"] = scale
    for c in ("equity", "cash", "tcost", "positions_value", "step_pnl", "financing_cum", "flows_cum", "interest_cum", "measure_dv01"):
        if c in oe and c in ne:
            res[f"eq_{c}"] = _mx(oe[c].to_numpy(), ne[c].to_numpy(), scale if c != "measure_dv01" else max(float(oe[c].abs().max()), 1.0))
    if "n_positions" in oe:
        res["eq_n_positions_identical"] = bool((oe["n_positions"].to_numpy() == ne["n_positions"].to_numpy()).all())
    # layers per row (cumulative)
    lay = {}
    for k in LAYERS:
        c = f"layer_{k}"
        if c in oe and c in ne:
            lay[k] = _mx(oe[c].to_numpy(), ne[c].to_numpy(), scale)
    res["layers_rowwise"] = lay
    if all(f"layer_{k}" in oe and f"layer_{k}" in ne for k in LAYERS):
        ssum_o = sum(oe[f"layer_{k}"] for k in LAYERS if k != "unexplained")
        ssum_n = sum(ne[f"layer_{k}"] for k in LAYERS if k != "unexplained")
        res["layers_sum_incl_unexpl"] = _mx((ssum_o + oe["layer_unexplained"]).to_numpy(), (ssum_n + ne["layer_unexplained"]).to_numpy(), scale)
        rd_o, rd_n = oe["layer_roll"] + oe["layer_delta"], ne["layer_roll"] + ne["layer_delta"]
        res["roll_plus_delta"] = _mx(rd_o.to_numpy(), rd_n.to_numpy(), scale)
        cd = lambda e: e["layer_carry"] + e["layer_roll"] + e["layer_delta"] + e["layer_convexity"]
        res["carry_roll_delta_convexity"] = _mx(cd(oe).to_numpy(), cd(ne).to_numpy(), scale)
    res["final_equity_old"], res["final_equity_new"] = float(oe["equity"].iloc[-1]), float(ne["equity"].iloc[-1])
    res["final_layers_old"] = {k: float(oe[f"layer_{k}"].iloc[-1]) for k in LAYERS if f"layer_{k}" in oe}
    res["final_layers_new"] = {k: float(ne[f"layer_{k}"].iloc[-1]) for k in LAYERS if f"layer_{k}" in ne}
    # trades
    ot, nt = O["trades"].reset_index(drop=True), N["trades"].reset_index(drop=True)
    res["trades_old"], res["trades_new"] = len(ot), len(nt)
    if len(ot) == len(nt) and len(ot):
        keys = ["ts", "kind", "action", "reason"]
        res["trades_keys_identical"] = bool((ot[keys].astype(str).to_numpy() == nt[keys].astype(str).to_numpy()).all())
        res["trades_template_identical"] = bool((ot["template"].astype(str).to_numpy() == nt["template"].astype(str).to_numpy()).all())
        for c in ("quantity", "pv", "cash", "tcost"):
            a, b = (pd.to_numeric(x[c], errors="coerce").to_numpy(dtype=float) for x in (ot, nt))
            res[f"trades_{c}"] = _mx(a, b, max(float(np.nanmax(np.abs(a))) if np.isfinite(a).any() else 1.0, 1.0))
    else:
        res["trades_keys_identical"] = len(ot) == len(nt) == 0
    # positions
    op, np_ = O["positions"].reset_index(drop=True), N["positions"].reset_index(drop=True)
    res["positions_old"], res["positions_new"] = len(op), len(np_)
    if len(op) == len(np_) and len(op):
        res["positions_entry_exit_identical"] = bool((op["entry_ts"].astype(str).to_numpy() == np_["entry_ts"].astype(str).to_numpy()).all() and (op["exit_ts"].astype(str).to_numpy() == np_["exit_ts"].astype(str).to_numpy()).all())
        res["positions_reason_identical"] = bool((op["exit_reason"].astype(str).to_numpy() == np_["exit_reason"].astype(str).to_numpy()).all())
        for c in ("entry_quantity", "quantity", "entry_pv", "exit_pv", "pnl", "trade_cash", "flow_cash", "financing", "tcost"):
            if c in op and c in np_:
                a, b = (pd.to_numeric(x[c], errors="coerce").to_numpy(dtype=float) for x in (op, np_))
                res[f"pos_{c}"] = _mx(a, b, max(float(np.nanmax(np.abs(a))) if np.isfinite(a).any() else 1.0, 1.0))
    else:
        res["positions_entry_exit_identical"] = len(op) == len(np_) == 0
    res["errors_new"] = len(N["errors"])
    return res


def selftest() -> None:
    """Known answers: a dir compared with itself is exactly zero; a copy with one equity value perturbed is detected at that scale."""
    import shutil
    import tempfile

    src = Path(__file__).resolve().parents[2] / "results" / "s11_buy_hold_10y"
    r0 = compare_dirs(src, src)
    assert r0["eq_equity"]["max_abs"] == 0.0 and r0["layers_sum_incl_unexpl"]["max_abs"] == 0.0 and r0["trades_keys_identical"] and r0["equity_index_identical"], r0
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        for f in src.glob("*.parquet"):
            shutil.copy(f, t / f.name)
        eq = pd.read_parquet(t / "equity.parquet")
        eq.iloc[100, eq.columns.get_loc("equity")] += 123.0
        eq.iloc[200, eq.columns.get_loc("layer_delta")] += 55.0
        eq.to_parquet(t / "equity.parquet")
        pos = pd.read_parquet(t / "positions.parquet")
        pos.loc[0, "entry_pv"] += 1.0
        pos.to_parquet(t / "positions.parquet")
        r1 = compare_dirs(src, t)
        assert abs(r1["eq_equity"]["max_abs"] - 123.0) < 1e-9 and abs(r1["layers_rowwise"]["delta"]["max_abs"] - 55.0) < 1e-9 and abs(r1["pos_entry_pv"]["max_abs"] - 1.0) < 1e-9, r1
        # the truncation: the first 10 rows of the record compared with a directory holding only those 10 rows
    print("selftest OK")


if __name__ == "__main__":
    selftest()
