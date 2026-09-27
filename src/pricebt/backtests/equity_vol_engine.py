"""gs_quant `EquityVolEngine` stub: gs runs equity option / variance swap backtests on its servers (with GS vol surfaces)."""
from __future__ import annotations

from typing import Any

from ..errors import NotSupportedError

__all__ = ["EquityVolEngine"]

_MSG = ("EquityVolEngine is a server-side GS engine and is not available in pricebt. Write an equity-option pricable (value(ctx) + measures) and a "
        "market that holds spot/vol, then use GenericEngine with AddTradeAction / AddScaledTradeAction (docs/guides/backtesting.md section 10)")


class EquityVolEngine:
    def __init__(self, *args: Any, **kwargs: Any):
        raise NotSupportedError(_MSG)

    @classmethod
    def supports_strategy(cls, strategy: Any) -> bool:
        return False

    @classmethod
    def run_backtest(cls, *args: Any, **kwargs: Any) -> Any:
        raise NotSupportedError(_MSG)
