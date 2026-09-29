"""PricingContext, HistoricalPricingContext, and the seam functions _engine_calc / _engine_resolve.

gs-shaped context managers (DESIGN.md section 6.5). Only `pricing_date` and `csa_term` actually do
anything here; `market` must be None (DEV-M1); every other constructor parameter is accepted, for
signature parity, and ignored. `PricingContext.current` is the innermost entered context on a
shared stack, else the default set by assigning `PricingContext.current = ...` (gs), else a fresh
default context (`pricing_date = date.today()`). An un-set `pricing_date`/`csa_term` is inherited
from the context that was current when this one was entered.

`_engine_calc`/`_engine_resolve` are thin seams: each function-level-imports `pricebt.assets.pricing`
(that module does not exist until P2.2) and delegates. Nothing in Phase 1 calls them.
"""
from __future__ import annotations

from datetime import date
from typing import List, Optional, Tuple

from pricebt.datetime import date_range
from pricebt.errors import NotSupportedError

__all__ = ["PricingContext", "HistoricalPricingContext"]

_STACK: List["PricingContext"] = []
_DEFAULT: Optional["PricingContext"] = None  # `PricingContext.current = ...`, used while _STACK is empty


class _ContextMeta(type):
    @property
    def current(cls) -> "PricingContext":
        return _STACK[-1] if _STACK else _DEFAULT or PricingContext()

    @current.setter
    def current(cls, current: "PricingContext") -> None:
        global _DEFAULT
        if _STACK:
            raise ValueError(f"Cannot set current while in a nested context {cls.__name__}")
        _DEFAULT = current


class PricingContext(metaclass=_ContextMeta):
    def __init__(
        self,
        pricing_date=None,
        market_data_location=None,
        is_async=None,
        is_batch=None,
        use_cache=None,
        visible_to_gs=None,
        request_priority=None,
        csa_term=None,
        timeout=None,
        market=None,
        show_progress=None,
        use_server_cache=None,
        market_behaviour="ContraintsBased",
        set_parameters_only=False,
        use_historical_diddles_only=None,
        provider=None,
    ):
        if market is not None:
            # pricebt DEV-M1: gs prices on the given market; pricebt has no market objects yet, and
            # pricing the context's own market instead would be a silent wrong number
            raise NotSupportedError(f"PricingContext(market={market!r}) is not supported: pricebt prices on each asset config's own market for the pricing date")
        self._pricing_date = pricing_date
        self._csa_term = csa_term
        self._is_async = is_async
        self._is_entered = False
        self._parent: Optional["PricingContext"] = None

    def _inherited(self, field: str):
        """This context's own `field`, else the nearest parent's. The walk stops at a context it has
        seen: `with PricingContext.current:` after assigning it (or entering the default below a
        context that inherits from it) makes a context its own ancestor (gs guards with
        `current is not self`)."""
        ctx, seen = self, set()
        while ctx is not None and id(ctx) not in seen:
            value = getattr(ctx, field)
            if value is not None:
                return value
            seen.add(id(ctx))
            ctx = ctx._parent
        return None

    @property
    def pricing_date(self) -> date:
        found = self._inherited("_pricing_date")
        return found if found is not None else date.today()

    @property
    def csa_term(self):
        return self._inherited("_csa_term")

    @property
    def is_entered(self) -> bool:
        return self._is_entered

    @property
    def is_async(self) -> bool:
        return bool(self._is_async)

    def __enter__(self) -> "PricingContext":
        self._parent = _STACK[-1] if _STACK else _DEFAULT
        _STACK.append(self)
        self._is_entered = True
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        _STACK.pop()
        self._is_entered = False
        return False


class HistoricalPricingContext(PricingContext):
    def __init__(
        self,
        start=None,
        end=None,
        calendars=(),
        dates=None,
        is_async=None,
        is_batch=None,
        use_cache=None,
        visible_to_gs=None,
        request_priority=None,
        csa_term=None,
        market_data_location=None,
        timeout=None,
        show_progress=None,
        use_server_cache=None,
        provider=None,
    ):
        if start is not None:
            if dates is not None:
                raise ValueError("Must supply start or dates, not both")
        elif dates is None:
            raise ValueError("Must supply start or dates")
        super().__init__(
            is_async=is_async,
            is_batch=is_batch,
            use_cache=use_cache,
            visible_to_gs=visible_to_gs,
            request_priority=request_priority,
            csa_term=csa_term,
            market_data_location=market_data_location,
            timeout=timeout,
            show_progress=show_progress,
            use_server_cache=use_server_cache,
            use_historical_diddles_only=True,
            provider=provider,
        )
        # gs computes the date list eagerly in __init__ (it raises there too, e.g. on a weekend
        # `start` in the (date, date) form): match that rather than deferring to first `.date_range`
        # read.
        if dates is not None:
            self._dates: Tuple[date, ...] = tuple(dates)
        else:
            self._dates = tuple(date_range(start, end if end is not None else date.today(), calendars=calendars))

    @property
    def date_range(self) -> Tuple[date, ...]:
        return self._dates


def _engine_calc(priceable, measures, fn=None):
    from pricebt.assets import pricing  # DESIGN.md section 6.2: keeps the import DAG intact

    return pricing.engine_calc(priceable, measures, fn)


def _engine_resolve(priceable, in_place):
    from pricebt.assets import pricing  # DESIGN.md section 6.2: keeps the import DAG intact

    return pricing.engine_resolve(priceable, in_place)
