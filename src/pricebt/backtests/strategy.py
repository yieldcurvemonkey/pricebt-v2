"""gs_quant `backtests.strategy.Strategy`: pricebt's Strategy has the same positional shape - `Strategy(initial_portfolio, triggers, cash_accrual)`.

`Strategy(None, trigger)`, `Strategy(instrument, trigger)`, `Strategy(None, [trigger_add, trigger_hedge])`. Triggers are deep-copied, so one
Strategy can be run any number of times with identical results.
"""
from __future__ import annotations

from ..strategy.strategy import PositionSpec, Strategy

__all__ = ["Strategy", "PositionSpec"]
