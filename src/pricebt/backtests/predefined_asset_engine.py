"""gs_quant `PredefinedAssetEngine` stub: gs's order-based engine for GS predefined assets (GS data handlers)."""
from __future__ import annotations

from typing import Any

from ..errors import NotSupportedError

__all__ = ["PredefinedAssetEngine"]

_MSG = ("PredefinedAssetEngine trades GS predefined assets through GS data handlers and is not available in pricebt. Model the asset as a pricable "
        "over your own market and use GenericEngine; for order-level logic use OrdersGeneratorTrigger / CustomAction")


class PredefinedAssetEngine:
    def __init__(self, *args: Any, **kwargs: Any):
        raise NotSupportedError(_MSG)

    @classmethod
    def run_backtest(cls, *args: Any, **kwargs: Any) -> Any:
        raise NotSupportedError(_MSG)
