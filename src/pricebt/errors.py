"""Exception hierarchy. Every pricebt-raised error derives from PricebtError."""
from __future__ import annotations

from typing import Any, Mapping, Optional


class PricebtError(Exception):
    """Base class of all pricebt errors."""


class ConfigError(PricebtError):
    """Invalid configuration. `path` is the YAML/JSON path, `code` a stable identifier."""

    def __init__(self, message: str, *, path: str = "", code: str = "CFG"):
        self.path = path
        self.code = code
        super().__init__(f"[{code}] {path}: {message}" if path else f"[{code}] {message}")


class RegistryError(ConfigError):
    """Unknown or disallowed registry name / dotted path."""


class MarketDataUnavailable(PricebtError):
    """No usable snapshot for (ts, request). Never replaced by a silent empty result."""

    def __init__(self, ts: Any = None, request: Optional[Mapping[str, Any]] = None, reason: str = ""):
        self.ts = ts
        self.request = dict(request) if request else {}
        self.reason = reason
        super().__init__(f"market data unavailable at {ts}: {reason}")


class StaleSnapshot(MarketDataUnavailable):
    """A snapshot exists but is older than the configured staleness bound."""


class LookAheadError(PricebtError):
    """Something at or after the clock was requested (future data)."""


class MethodCallError(PricebtError):
    """call_method could not bind or invoke a pricable method."""


class MeasureError(PricebtError):
    """A standard measure could not be evaluated for a pricable."""


class MeasureNotBound(MeasureError):
    """The instrument's spec binds no callable for this schema name (a recorded portfolio measure counts such a position as 0; every other MeasureError is a failure)."""


class StrategyError(PricebtError):
    """Structurally invalid strategy."""


class TriggerError(StrategyError):
    """A trigger misbehaved or was mis-specified."""


class SizingError(StrategyError):
    """Position sizing / hedge sizing could not be computed."""


class DurationError(StrategyError):
    """A trade_duration could not be resolved."""


class SignalError(PricebtError):
    """A signal could not be evaluated."""


class MissingDataError(SignalError):
    """Signal data missing under the `fail` policy."""


class NotSupportedError(PricebtError):
    """Requested feature is intentionally not supported."""


class EngineInvariantError(PricebtError):
    """An internal engine invariant was violated (a bug or a misbehaving component)."""


class OptionalDependencyError(PricebtError):
    """An optional extra (rateslib, QuantLib, matplotlib, pyarrow) is not installed."""
