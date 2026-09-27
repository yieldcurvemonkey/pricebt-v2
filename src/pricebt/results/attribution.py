"""P&L attribution by layer, tag, template, action, kind, position, component and day.

Every table conserves the run: the ``net_total`` column of a grouped table sums to ``equity[-1] - initial_capital``
(``tag_mode="each"`` excepted, which is overlapping by construction).

Definitions per position (currency):

* ``modelled`` layers: the engine's per-position layer P&L (``layers_by_position``), gross of costs.
* ``unexplained = pnl_gross - sum(modelled)``: the reserved residual bucket. It is derived from the identity, so a
  position with NO layers (attribution off) is fully unexplained, and a layer interval not yet flushed falls in it too.
  With layers on it equals the engine's own ``unexplained`` row (checked by `reconcile`). Carry/financing that no layer
  models lands here (the engine reserves ``financing`` and never asks a pricable for it).
* ``transactions = -tcost`` and ``net_total = pnl_gross - tcost``.
* ``cash_interest``: interest on the cash balance, portfolio level, shown in the ``(cash)`` group.
"""
from __future__ import annotations

from typing import Any, List

import numpy as np
import pandas as pd

from ..types import BASELINE_LAYERS
from .errors import ResultError
from .ledger import layer_frame

GROUP_KEYS = ("position", "template", "action", "kind", "tag")
BY_CHOICES = ("layer", "component", "day", "baseline", *GROUP_KEYS)
CASH_ROW = "(cash)"
UNTAGGED = "(untagged)"


def modelled_layers(result: Any) -> List[str]:
    """Names of the modelled layers present in the run, in order of appearance: the engine's `unexplained` and the baseline decomposition (`tay_*`, an ALTERNATIVE
    decomposition of the same P&L, see `_by_baseline`) are not modelled layers."""
    frame = result.record.layers_by_position
    if frame.empty:
        return []
    return [n for n in dict.fromkeys(frame["layer"]) if n != "unexplained" and n not in BASELINE_LAYERS]


def position_attribution(result: Any) -> pd.DataFrame:
    """Per-position table: modelled layers, ``unexplained``, ``transactions``, ``net_total`` (see module docstring)."""
    pos = result.positions
    names = modelled_layers(result)
    lay = layer_frame(result.record.layers_by_position, pos.index).reindex(columns=names).fillna(0.0)
    out = lay.copy()
    out["unexplained"] = pos["pnl_gross"] - lay.sum(axis=1)
    out["transactions"] = -pos["tcost"]
    out["cash_interest"] = 0.0
    out["net_total"] = pos["pnl_net"]
    return out


def _interest(result: Any) -> float:
    return float(result.equity["interest_cum"].iloc[-1])


def _with_cash_row(table: pd.DataFrame, interest: float) -> pd.DataFrame:
    row = pd.DataFrame(0.0, index=[CASH_ROW], columns=table.columns)
    row.loc[CASH_ROW, "cash_interest"] = interest
    row.loc[CASH_ROW, "net_total"] = interest
    return row if table.empty else pd.concat([table, row])


def _by_layer(result: Any) -> pd.DataFrame:
    t = position_attribution(result)
    sums = t.drop(columns=["net_total"]).sum()
    sums["cash_interest"] = _interest(result)
    total = float(sums.sum())
    out = pd.DataFrame({"pnl": sums})
    out.loc["total"] = total
    out["share"] = out["pnl"] / total if total != 0.0 else np.nan
    out.index.name = "layer"
    return out


def _by_baseline(result: Any) -> pd.DataFrame:
    """The engine's baseline decomposition (spec L3: tay_delta, tay_convexity, tay_unexplained) with transactions and cash interest: the SAME total as the library layers."""
    frame = result.record.layers_by_position
    present = [n for n in BASELINE_LAYERS if not frame.empty and n in set(frame["layer"])]
    if not present:
        raise ResultError("the run has no baseline decomposition: set `attribution.baseline: true` (EngineSettings.baseline) before running")
    sums = frame[frame["layer"].isin(present)].groupby("layer")["pnl"].sum().reindex(present)
    rows = {**sums.to_dict(), "transactions": -float(result.positions["tcost"].sum()) if len(result.positions) else 0.0, "cash_interest": _interest(result)}
    out = pd.DataFrame({"pnl": pd.Series(rows)})
    out.loc["total"] = float(out["pnl"].sum())
    out["share"] = out["pnl"] / out.loc["total", "pnl"] if out.loc["total", "pnl"] != 0.0 else np.nan
    out.index.name = "layer"
    return out


def _by_component(result: Any) -> pd.DataFrame:
    p = result.positions
    rows = {
        "price": float(p["pnl_price"].sum()) if len(p) else 0.0,
        "cash_flows": float(p["flow_cash"].sum()) if len(p) else 0.0,
        "financing": float(p["financing"].sum()) if len(p) else 0.0,
        "transactions": -float(p["tcost"].sum()) if len(p) else 0.0,
        "cash_interest": _interest(result),
    }
    out = pd.DataFrame({"pnl": pd.Series(rows)})
    out.loc["total"] = float(out["pnl"].sum())
    out["share"] = out["pnl"] / out.loc["total", "pnl"] if out.loc["total", "pnl"] != 0.0 else np.nan
    out.index.name = "component"
    return out


def _by_group(result: Any, by: str, tag_mode: str) -> pd.DataFrame:
    t = position_attribution(result)
    pos = result.positions
    if by == "position":
        grouped = t.copy()
    elif by == "tag":
        if tag_mode not in ("split", "each"):
            raise ResultError(f"tag_mode must be 'split' or 'each', got {tag_mode!r}")
        tags = pos["tags"].fillna("").map(lambda s: [x for x in str(s).split(",") if x] or [UNTAGGED]) if len(pos) else pd.Series(dtype=object)
        n = tags.map(len).to_numpy(dtype=float) if len(pos) else np.array([])
        scaled = t.mul(1.0 / n if tag_mode == "split" else 1.0, axis=0) if len(pos) else t
        long = scaled.assign(_tag=tags).explode("_tag")
        grouped = long.groupby("_tag", sort=True).sum()
    else:
        grouped = t.groupby(pos[by].astype(str), sort=True).sum() if len(pos) else t
    grouped.index.name = by
    return _with_cash_row(grouped, _interest(result))


def _by_day(result: Any) -> pd.DataFrame:
    eq = result.equity
    days = pd.Index(eq.index.tz_localize(None).normalize(), name="day")
    layer_cols = [f"layer_{n}" for n in modelled_layers(result)]
    cols = {}
    for c in layer_cols:
        cols[c[len("layer_"):]] = eq[c].diff().fillna(eq[c]).groupby(days).sum() if c in eq.columns else 0.0
    out = pd.DataFrame(cols, index=days.unique()) if cols else pd.DataFrame(index=days.unique())
    net = eq["step_pnl"].groupby(days).sum()
    tcost = eq["tcost"].diff().fillna(eq["tcost"]).groupby(days).sum()
    interest = eq["interest_cum"].diff().fillna(eq["interest_cum"]).groupby(days).sum()
    out["unexplained"] = net - tcost - interest - (out.sum(axis=1) if cols else 0.0)
    out["transactions"] = tcost
    out["cash_interest"] = interest
    out["net_total"] = net
    return out


def attribution(result: Any, by: str = "layer", *, tag_mode: str = "split") -> pd.DataFrame:
    """P&L attribution table. `by` in ``layer`` (one column ``pnl`` + ``share``), ``baseline`` (the engine's own tay_* decomposition of the same total), ``component`` (price / cash_flows /
    financing / transactions / cash_interest), ``day`` (local date; layer P&L lands on the day its interval was flushed),
    or a position grouping ``position | template | action | kind | tag``. Grouped tables have one column per modelled
    layer, ``unexplained``, ``transactions``, ``cash_interest`` and ``net_total`` and a ``(cash)`` row for interest.
    `tag_mode="split"` divides a position's P&L equally over its tags (additive); ``"each"`` credits every tag in full."""
    if by == "layer":
        return _by_layer(result)
    if by == "baseline":
        return _by_baseline(result)
    if by == "component":
        return _by_component(result)
    if by == "day":
        return _by_day(result)
    if by in GROUP_KEYS:
        return _by_group(result, by, tag_mode)
    raise ResultError(f"unknown attribution key {by!r}; choose from {BY_CHOICES}")


def layer_series(result: Any) -> pd.DataFrame:
    """Cumulative portfolio layer P&L per timeline row (names without the ``layer_`` prefix, engine `unexplained` included)."""
    cols = [c for c in result.equity.columns if c.startswith("layer_")]
    out = result.equity[cols].copy()
    out.columns = [c[len("layer_"):] for c in cols]
    return out
