"""PricebtSession, GsSession (a no-op shim with the gs signature), Environment (DESIGN.md
section 6.1).

`PricebtSession` builds its `AssetRegistry` and FX config eagerly (both are real code), but
constructs `PricingService` via a function-level import: `pricebt.assets.pricing` does not exist
as real code until task P2.2 (it is still a docstring stub while this task runs in parallel with
it), and a top-level import here would either fail immediately or set up an import cycle once
pricing.py needs `PricebtSession.current` itself. See `PricebtSession.__init__` below.
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, ClassVar, Dict, Mapping, Optional, Sequence, Tuple, Union

from .assets.config import AssetConfig
from .assets.fx import FxConfig, load_fx
from .assets.market import MarketConfig, MarketRegistry, MarketSource
from .assets.registry import AssetRegistry, AssetSource
from .base import EnumBase
from .markets import DEFAULT_MARKET_DATA_LOCATION, PricingContext

__all__ = ["Environment", "GsSession", "PricebtSession"]

FxSource = Union[str, Path, Mapping[str, Any], FxConfig]


class Environment(EnumBase, Enum):
    """gs environment selector -- a `GsSession.use(...)` argument only; pricebt makes no network
    call of its own (DESIGN.md section 6.1)."""

    DEV = 1
    QA = 2
    PROD = 3


class PricebtSession:
    """pricebt's session: a market provider registry, an asset registry (whose assets may name
    those providers), an optional FX config, and the pricing settings a backtest run reads
    (DESIGN.md section 6.1; docs/v2/MARKET_CONTEXT_DESIGN.md)."""

    current: ClassVar[Optional["PricebtSession"]] = None

    def __init__(
        self,
        assets: Sequence[AssetSource] = (),
        fx: Optional[FxSource] = None,
        *,
        markets: Sequence[MarketSource] = (),
        tz: str = "America/New_York",
        eod_time: str = "17:00",
        missing_market: str = "drop",
        reporting_currency: Optional[str] = None,
    ):
        # market providers first: an asset's `market.provider` is checked when the asset registers
        self.markets = MarketRegistry()
        for source in markets:
            self.markets.add(source)
        self.registry = AssetRegistry(self.markets)
        for source in assets:
            self.registry.add(source)
        self.fx: Optional[FxConfig] = fx if fx is None or isinstance(fx, FxConfig) else load_fx(fx)
        self.tz = tz
        self.eod_time = eod_time
        self.missing_market = missing_market
        self.reporting_currency = reporting_currency
        self._prev_current: Optional["PricebtSession"] = None

        # See the module docstring: PricingService(registry, fx, *, tz, eod_time) is P2.2's
        # contract (DESIGN.md section 6.2). Constructing it here fails loudly (AttributeError,
        # since `pricing.PricingService` does not exist yet) until P2.2 lands.
        from pricebt.assets import pricing as _pricing_mod

        self._pricing = _pricing_mod.PricingService(self.registry, self.fx, tz=self.tz, eod_time=self.eod_time)
        self._pricing_by_location: Dict[str, Any] = {}

    @property
    def pricing(self):
        """The `PricingService` of the current `PricingContext`'s market data location: one
        service, with its own caches, per location (MARKET_CONTEXT_DESIGN section 5), so a value
        priced on the NYC close is never served for the LDN close."""
        location = PricingContext.current.market_data_location
        if location == DEFAULT_MARKET_DATA_LOCATION:
            return self._pricing
        service = self._pricing_by_location.get(location)
        if service is None:
            service = self._pricing_by_location[location] = self._pricing.for_location(location)
        return service

    @classmethod
    def use(cls, assets: Sequence[AssetSource] = (), fx: Optional[FxSource] = None, **kwargs: Any) -> "PricebtSession":
        session = cls(assets, fx, **kwargs)
        cls.current = session
        return session

    def add_asset(self, source: AssetSource) -> AssetConfig:
        return self.registry.add_one(source)

    def add_market(self, source: MarketSource) -> MarketConfig:
        return self.markets.add_one(source)

    def reset_caches(self) -> None:
        for service in (self._pricing, *self._pricing_by_location.values()):
            service.reset()

    def __enter__(self) -> "PricebtSession":
        self._prev_current = PricebtSession.current
        PricebtSession.current = self
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        PricebtSession.current = self._prev_current
        return False


class GsSession:
    """gs-compatible shim so a notebook cell that calls `GsSession.use(...)` runs unchanged: it
    records the call's arguments and nothing else. No network, and it never constructs or touches
    a `PricebtSession` (DESIGN.md section 6.1)."""

    current: ClassVar[Optional[Dict[str, Any]]] = None

    @classmethod
    def use(
        cls,
        environment_or_domain: Any = Environment.PROD,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        scopes: Tuple[str, ...] = (),
        api_version: str = "v1",
        application: str = "gs-quant",
        http_adapter: Any = None,
        use_mds: bool = False,
        domain: str = "AppDomain",
    ) -> None:
        cls.current = {
            "environment_or_domain": environment_or_domain,
            "client_id": client_id,
            "client_secret": client_secret,
            "scopes": scopes,
            "api_version": api_version,
            "application": application,
            "http_adapter": http_adapter,
            "use_mds": use_mds,
            "domain": domain,
        }
