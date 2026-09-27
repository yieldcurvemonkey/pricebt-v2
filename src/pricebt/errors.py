"""pricebt's exception hierarchy. Every pricebt-raised error derives from PricebtError.

This module has no dependencies beyond the standard library, because it sits at the root of the
import DAG: everything else in pricebt imports it, and it imports nothing of pricebt's own.
"""
from __future__ import annotations

from typing import Any, Optional


class PricebtError(Exception):
    """Base class of every error pricebt raises."""


class ConfigError(PricebtError):
    """An asset or FX config is invalid: bad schema, an unknown key, an ambiguous match, or a bad
    unit conversion. `asset` and `key` name where in the config the problem was found, when known."""

    def __init__(self, message: str, asset: Optional[str] = None, key: Optional[str] = None):
        self.asset = asset
        self.key = key
        where = ":".join(p for p in (asset, key) if p)
        super().__init__(f"[{where}] {message}" if where else message)


class AssetEvaluationError(PricebtError):
    """Raised (`from exc`) when evaluating an asset's imports, code, or an expression fails.

    Carries the asset, the config key, the expression text, the pricing date and the CSA, so the
    message can name exactly what was being evaluated and when; `__cause__` (set by `raise ... from
    exc`) carries the original exception, whose type and message are folded into `str(self)`.
    """

    def __init__(self, asset: str, key: str, expr: str, date: Any, csa: Any):
        self.asset = asset
        self.key = key
        self.expr = expr[:200]
        self.date = date
        self.csa = csa
        super().__init__(f"asset {asset!r} key {key!r} expr {self.expr!r} date={date} csa={csa!r}")

    def __str__(self) -> str:
        base = super().__str__()
        cause = self.__cause__
        if cause is None:
            return base
        return f"{base}: {type(cause).__name__}: {cause}"


class MarketDataUnavailable(PricebtError):
    """A market expression returned `None` where a market was required, or an FX rate could not be
    evaluated. `asset` may instead be a currency pair (e.g. "EURUSD") for an FX lookup."""

    def __init__(self, asset: str, date: Any, csa: Any, reason: str = ""):
        self.asset = asset
        self.date = date
        self.csa = csa
        self.reason = reason
        msg = f"no market for {asset!r} on {date} (csa={csa!r})"
        super().__init__(f"{msg}: {reason}" if reason else msg)


class NotSupportedError(PricebtError):
    """Raised by a stub for a feature intentionally left out of pricebt (GS server-side machinery,
    or a later-phase feature not yet implemented). The message says what to use instead."""
