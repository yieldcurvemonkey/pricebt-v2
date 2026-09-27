"""pricebt: an event-driven backtester whose public API is a near 1:1 port of gs_quant.backtests.
pricebt has no pricing or market data of its own; every value comes from a user-supplied asset
config (see docs/v2/DESIGN.md). This module imports nothing eagerly except `errors`."""
from __future__ import annotations

from . import errors  # noqa: F401

__version__ = "2.0.0"
