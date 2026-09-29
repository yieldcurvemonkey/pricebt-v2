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

    def __reduce__(self):
        # the constructor's arguments, not `args` (one message string), so copy/pickle work
        return type(self), (self.asset, self.key, self.expr, self.date, self.csa)

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

    def __reduce__(self):
        return type(self), (self.asset, self.date, self.csa, self.reason)


class NotSupportedError(PricebtError):
    """Raised by a stub for a feature intentionally left out of pricebt (GS server-side machinery,
    or a later-phase feature not yet implemented). The message says what to use instead."""


class UnsupportedMeasureError(ConfigError, NotSupportedError):
    """A risk measure (or one form of it) was requested that the asset config declares under
    `unsupported_measures:` (docs/v2/IR_RISK_DESIGN.md section 2.4; pricebt DEV-I11). Both a
    `ConfigError` (the config decides it) and a `NotSupportedError` (nothing can compute it here).
    `form` is "scalar", "bucketed", "frame", or "*" for every form."""

    def __init__(self, asset: str, measure: str, form: str, reason: str):
        self.measure = measure
        self.form = form
        self.reason = reason
        shown = "every form" if form == "*" else form
        key = f"unsupported_measures.{measure}" + ("" if form == "*" else f".{form}")
        # "has no mapping for risk measure X" is the generic no-mapping text, kept so a caller
        # matching it (the v2-pnl-explain branch's T-MISSING test) still matches (R14 section 8 #3)
        super().__init__(
            f"asset {asset} has no mapping for risk measure {measure}: {measure!r} is declared unsupported ({shown}): {reason}. "
            f"Map it under risk_measures: once the asset's library can compute it.",
            asset=asset,
            key=key,
        )

    def __reduce__(self):
        return type(self), (self.asset, self.measure, self.form, self.reason)
