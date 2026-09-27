"""The reproduction table. For every tag with a directory in results_new AND results, compare and print one row of numbers per tag (json + markdown).

    repro_table.py [tags...] > out
Columns: window, rows old/new, trades old/new, positions old/new, P&L old/new (final equity), max |diff| of equity and its size relative to max|equity|, the largest
max|diff| over the four layers, whether equity+cash+tcost are bit-identical, whether the ledger keys (ts, kind, action, reason, template) are identical.
"""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compare  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
NEW, OLD = ROOT / "results_new", ROOT / "results"


def row(tag: str, new_dir: Path = None, old_dir: Path = None) -> dict:
    new_dir, old_dir = new_dir or NEW / tag, old_dir or OLD / tag
    r = compare.compare_dirs(old_dir, new_dir)
    o = compare.load(old_dir)
    oe = o["equity"]
    win = f"{oe.index[0].date()}..{oe.index[-1].date()}" if len(oe) else ""
    lay = r.get("layers_rowwise", {})
    out = {
        "tag": tag, "window": win, "rows": f"{r['rows_old']}/{r['rows_new']}", "index_identical": r["equity_index_identical"],
        "trades": f"{r['trades_old']}/{r['trades_new']}", "positions": f"{r['positions_old']}/{r['positions_new']}",
        "pnl_old": r["final_equity_old"], "pnl_new": r["final_equity_new"], "pnl_diff": r["final_equity_new"] - r["final_equity_old"],
        "eq_max_abs": r.get("eq_equity", {}).get("max_abs"), "eq_max_rel": r.get("eq_equity", {}).get("max_rel_to_scale"),
        "cash_max_abs": r.get("eq_cash", {}).get("max_abs"), "tcost_max_abs": r.get("eq_tcost", {}).get("max_abs"),
        "pv_max_abs": r.get("eq_positions_value", {}).get("max_abs"),
        "layer_sum_max_abs": r.get("layers_sum_incl_unexpl", {}).get("max_abs"),
        "layers_max_abs": {k: v["max_abs"] for k, v in lay.items()},
        "dv01_max_abs": r.get("eq_measure_dv01", {}).get("max_abs"), "dv01_max_rel": r.get("eq_measure_dv01", {}).get("max_rel_to_scale"),
        "ledger_identical": bool(r.get("trades_keys_identical")) and bool(r.get("positions_entry_exit_identical")),
        "trades_pv_max_abs": r.get("trades_pv", {}).get("max_abs"), "trades_qty_max_abs": r.get("trades_quantity", {}).get("max_abs"),
        "final_layers_old": r["final_layers_old"], "final_layers_new": r["final_layers_new"], "errors_new": r["errors_new"],
        "elapsed": json.loads((new_dir / "manifest.json").read_text()).get("engine_manifest", {}).get("elapsed_seconds"),
    }
    return out


def main() -> None:
    tags = sys.argv[1:] or sorted(p.name for p in NEW.iterdir() if p.is_dir() and (OLD / p.name).is_dir() and not p.name.startswith("scratch"))
    rows = [row(t) for t in tags]
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    df = pd.DataFrame(rows).set_index("tag")
    print(df[["window", "rows", "trades", "positions", "pnl_old", "pnl_new", "pnl_diff", "eq_max_abs", "eq_max_rel", "layer_sum_max_abs", "dv01_max_abs", "ledger_identical"]].to_string(float_format=lambda v: f"{v:,.6g}"))
    (NEW / "repro_table.json").write_text(json.dumps(rows, indent=1, default=str))  # next to the runs it describes


if __name__ == "__main__":
    main()
