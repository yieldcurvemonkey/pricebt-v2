"""Position and trade views built from a RunRecord: the positions table, closed-trade log, gs-style trade ledger."""
from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from .stats import profit_factor

POSITION_COLUMNS = (
    "template", "action", "kind", "tags", "side", "status", "entry_ts", "exit_ts", "exit_reason", "entry_quantity", "quantity",
    "entry_pv", "exit_pv", "hold_points", "hold_seconds", "hold_days", "pnl_price", "pnl_income", "pnl_gross", "tcost", "pnl_net",
    "trade_cash", "flow_cash", "financing",
)


def _ts_col(s: pd.Series, tz: Any) -> pd.Series:
    return pd.to_datetime(s, utc=True).dt.tz_convert(tz)


def positions_table(positions: pd.DataFrame, index: pd.DatetimeIndex) -> pd.DataFrame:
    """One row per position, indexed by position id, with the accounting split of its P&L.

    ``pnl_gross = value + trade_cash + flow_cash + financing`` (price leg plus carry/financing realised during the hold,
    before costs); ``pnl_income = flow_cash + financing``; ``pnl_price = pnl_gross - pnl_income``; ``tcost`` is the amount
    paid (>= 0) and ``pnl_net = pnl_gross - tcost`` is the engine's position P&L. ``hold_points`` counts timeline points
    ``t`` with ``entry_ts < t <= exit_ts`` (last point for open positions); ``hold_days``/``hold_seconds`` are elapsed time.
    """
    if positions.empty:
        return pd.DataFrame({c: pd.Series(dtype=object) for c in POSITION_COLUMNS}, index=pd.Index([], name="position"))
    p = positions.copy()
    p.index = pd.Index(p.pop("id").astype(str), name="position")
    tz = index.tz
    entry, exit_ = _ts_col(p["entry_ts"], tz), _ts_col(p["exit_ts"], tz)
    end = exit_.fillna(index[-1])
    p["entry_ts"], p["exit_ts"] = entry, exit_
    p["side"] = np.where(p["entry_quantity"] > 0, "long", "short")
    p["hold_points"] = (index.searchsorted(end.to_numpy(), side="right") - index.searchsorted(entry.to_numpy(), side="right")).astype(int)
    secs = (end - entry).dt.total_seconds()
    p["hold_seconds"], p["hold_days"] = secs, secs / 86400.0
    p["pnl_net"] = p["pnl"]
    p["pnl_gross"] = p["pnl"] + p["tcost"]
    p["pnl_income"] = p["flow_cash"] + p["financing"]
    p["pnl_price"] = p["pnl_gross"] - p["pnl_income"]
    return p[[c for c in POSITION_COLUMNS if c in p.columns] + [c for c in p.columns if c.startswith("layer_")]]


def closed_trades(pos: pd.DataFrame) -> pd.DataFrame:
    """Closed positions sorted by exit time: holding period (points, seconds, days), exit reason, gross/net P&L."""
    out = pos[pos["status"] == "closed"] if len(pos) else pos
    return out.sort_values(["exit_ts"], kind="stable") if len(out) else out


def trade_ledger(pos: pd.DataFrame) -> pd.DataFrame:
    """gs_quant `trade_ledger()` vocabulary: Open, Close, Open Value, Close Value, Long Short, Status, Trade PnL.

    Open Value is the cash paid at entry (negative for a purchase); Close Value is all other trade cash (exit proceeds net of
    any resizes; for open positions the current mark); ``Trade PnL`` is the gross position P&L including carry/financing realised (gs: cash
    based, excludes costs), so it differs from gs's ``Close Value + Open Value`` when the position paid or resized.
    """
    cols = ["Open", "Close", "Open Value", "Close Value", "Long Short", "Status", "Trade PnL"]
    if pos.empty:
        return pd.DataFrame(columns=cols, index=pd.Index([], name="position"))
    open_value = -pos["entry_quantity"] * pos["entry_pv"]
    is_closed = pos["status"] == "closed"
    mark = pos["pnl_gross"] - pos["trade_cash"] - pos["flow_cash"] - pos["financing"]
    close_value = np.where(is_closed, pos["trade_cash"] - open_value, mark)
    return pd.DataFrame(
        {"Open": pos["entry_ts"], "Close": pos["exit_ts"], "Open Value": open_value, "Close Value": close_value,
         "Long Short": np.where(pos["side"] == "long", "Long", "Short"), "Status": pos["status"], "Trade PnL": pos["pnl_gross"]},
        index=pos.index,
    )


def trade_summary(pos: pd.DataFrame) -> Dict[str, Any]:
    """Counts and closed-trade statistics: hit rate and profit factor of ``pnl_net``, expectancy, payoff, mean hold."""
    closed = closed_trades(pos)
    nan = float("nan")
    net = closed["pnl_net"].to_numpy(dtype=float) if len(closed) else np.array([])
    wins, losses = net[net > 0], net[net < 0]
    return {
        "n_trades": int(len(pos)),
        "n_closed": int(len(closed)),
        "n_open": int(len(pos) - len(closed)),
        "trade_hit_rate": float(np.mean(net > 0)) if len(net) else nan,
        "trade_profit_factor": profit_factor(net),
        "expectancy": float(np.mean(net)) if len(net) else nan,
        "payoff_ratio": float(np.mean(wins) / abs(np.mean(losses))) if len(wins) and len(losses) else nan,
        "avg_hold_points": float(closed["hold_points"].mean()) if len(closed) else nan,
        "avg_hold_days": float(closed["hold_days"].mean()) if len(closed) else nan,
    }


def layer_frame(layers_by_position: pd.DataFrame, index: Optional[pd.Index] = None) -> pd.DataFrame:
    """Per-position layer P&L pivot (index position id, one column per layer, engine `unexplained` included)."""
    if layers_by_position.empty:
        return pd.DataFrame(index=index if index is not None else pd.Index([], name="position"))
    piv = layers_by_position.pivot_table(index="position", columns="layer", values="pnl", aggfunc="sum")
    piv.columns.name = None
    piv.index = piv.index.astype(str)
    if index is not None:
        piv = piv.reindex(index)
    return piv
