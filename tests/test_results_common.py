"""Shared builders for the results tests (not a test module)."""
from __future__ import annotations

from typing import Sequence

import numpy as np
import pandas as pd

from conftest import make_engine
from pricebt.costs import ConstantCost
from pricebt.engine import EngineSettings
from pricebt.engine.state import RunRecord
from pricebt.orders import OpenOrder, PositionMeta, ResizeOrder
from toy_helpers import toy_tpl
from pricebt.contracts.spec import TradeTemplate
from pricebt.results import BacktestResult
from pricebt.testing.scripted import ScriptedStrategy
from pricebt.testing.toys import ToyForward, ToyFuture, ToyMDP, ToyOption, ToyZero
from pricebt.timeutil import TimeContext, TimeGrid

TZ = "America/New_York"
POS_COLS = ["id", "template", "instrument", "action", "kind", "tags", "entry_ts", "exit_ts", "exit_reason", "entry_quantity", "quantity", "entry_pv", "exit_pv",
            "status", "trade_cash", "flow_cash", "financing", "tcost", "pnl"]
TRADE_COLS = ["ts", "position", "kind", "quantity", "pv", "cash", "tcost", "action", "template", "reason", "instrument"]
D = lambda s: pd.Timestamp(f"{s} 17:00", tz=TZ)


def make_grid(start="2024-01-02", end="2024-02-29") -> TimeGrid:
    return TimeGrid.daily(start, end, "1b", TimeContext())


def synth_record(increments: Sequence[float], initial_capital: float = 0.0, index: pd.DatetimeIndex | None = None, name: str = "synth") -> RunRecord:
    """A RunRecord whose equity is ``K + cumsum(increments)`` with all other accounts flat: known answers for statistics."""
    inc = np.asarray(increments, dtype=float)
    if index is None:
        index = pd.date_range("2024-01-02 17:00", periods=len(inc), freq="B", tz=TZ, name="ts")
    equity = initial_capital + np.cumsum(inc)
    eq = pd.DataFrame(
        {"equity": equity, "cash": equity - initial_capital, "tcost": 0.0, "positions_value": 0.0, "n_positions": 0,
         "step_pnl": inc, "interest_cum": 0.0, "financing_cum": 0.0, "flows_cum": 0.0},
        index=index,
    )
    empty = pd.DataFrame
    return RunRecord(
        settings=EngineSettings(name=name, initial_capital=initial_capital, show_progress=False), equity=eq,
        positions=empty(columns=POS_COLS), trades=empty(columns=TRADE_COLS), orders=empty(), errors=empty(columns=["ts", "where", "position", "error"]),
        events=empty(), layers_by_position=empty(columns=["position", "layer", "pnl"]), manifest={"name": name},
    )


def synth_result(increments: Sequence[float], initial_capital: float = 0.0, **kw) -> BacktestResult:
    return BacktestResult(synth_record(increments, initial_capital, **kw))


def fwd(name="fwd", layers=(), **kw) -> TradeTemplate:
    return toy_tpl(name=name, target=ToyForward, kwargs={"strike": 4.0, **kw}, layers=tuple(layers))


def zero(name="z", layers=(), maturity="2025-01-10", **kw) -> TradeTemplate:
    return toy_tpl(name=name, target=ToyZero, kwargs={"maturity": D(maturity), "notional": 100.0, **kw}, layers=tuple(layers))


def run(script, grid=None, fn=None, mdp=None, **settings) -> BacktestResult:
    eng, _, _ = make_engine(grid or make_grid(), ScriptedStrategy(script, fn=fn), mdp=mdp, **settings)
    return BacktestResult(eng.run(), config_hash="abc123")


def forward_run(**settings) -> BacktestResult:
    """One 3-unit ToyForward with carry, tags, entry+exit cost, closed by schedule."""
    order = OpenOrder(fwd(notional=10.0, carry_bp_per_day=0.5, layers=("carry", "delta", "convexity")), quantity=3.0, final_ts=D("2024-02-07"),
                      tags=("macro", "core"), meta=PositionMeta(action="add_fwd"), cost_entry=ConstantCost(2.0), cost_exit=ConstantCost(1.0))
    settings.setdefault("cadence", "each")
    return run({D("2024-01-10"): [order]}, **settings)


def rich_run(**settings) -> BacktestResult:
    """Forward (closed), zero-coupon (open, financing, layers), option (open), a resize and cash accrual: exercises every frame."""
    from pricebt.costs import ConstantCashAccrual

    t1 = D("2024-01-10")
    script = {
        t1: [
            OpenOrder(fwd(notional=5.0, carry_bp_per_day=1.0), quantity=2.0, final_ts=D("2024-02-01"), tags=("a",), meta=PositionMeta(action="fwd")),
            OpenOrder(zero(layers=("roll", "delta", "convexity")), quantity=10.0, tags=("a", "b"), meta=PositionMeta(action="zero")),
            OpenOrder(toy_tpl(name="opt", target=ToyOption, kwargs={"strike": 4.0, "expiry": D("2024-02-28"), "notional": 1e3}),
                      quantity=1.0, meta=PositionMeta(action="opt")),
        ]
    }

    def fn(ts, view, submit):
        if ts == D("2024-01-17"):
            (p,) = view.positions("action:zero")
            submit(ResizeOrder(p.id, delta_quantity=-4.0, cost_model=ConstantCost(0.5)))

    settings.setdefault("cadence", "every_n:3")
    settings.setdefault("cash_accrual", ConstantCashAccrual(0.03, 365.0, "compound"))
    return run(script, fn=fn, **settings)
