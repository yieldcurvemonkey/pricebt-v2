"""PricingContext, HistoricalPricingContext, and the seam functions _engine_calc / _engine_resolve.

gs-shaped context managers (DESIGN.md section 6.5). Only `pricing_date` and `csa_term` actually do
anything here; every other constructor parameter is accepted, for signature parity, and ignored.
`PricingContext.current` is the innermost entered context on a shared stack, or else a fresh
default context (`pricing_date = date.today()`). An un-set `pricing_date`/`csa_term` is inherited
from the context that was current when this one was entered.

`_engine_calc`/`_engine_resolve` are thin seams: each function-level-imports `pricebt.assets.pricing`
(that module does not exist until P2.2) and delegates. Nothing in Phase 1 calls them.
"""
from __future__ import annotations

from datetime import date
from typing import List, Optional, Tuple

from pricebt.datetime import date_range

__all__ = ["PricingContext", "HistoricalPricingContext"]

_STACK: List["PricingContext"] = []


class _ContextMeta(type):
    @property
    def current(cls) -> "PricingContext":
        return _STACK[-1] if _STACK else PricingContext()


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
        self._pricing_date = pricing_date
        self._csa_term = csa_term
        self._is_async = is_async
        self._is_entered = False
        self._parent: Optional["PricingContext"] = None

    @property
    def pricing_date(self) -> date:
        if self._pricing_date is not None:
            return self._pricing_date
        if self._parent is not None:
            return self._parent.pricing_date
        return date.today()

    @property
    def csa_term(self):
        if self._csa_term is not None:
            return self._csa_term
        if self._parent is not None:
            return self._parent.csa_term
        return None

    @property
    def is_entered(self) -> bool:
        return self._is_entered

    @property
    def is_async(self) -> bool:
        return bool(self._is_async)

    def __enter__(self) -> "PricingContext":
        self._parent = _STACK[-1] if _STACK else None
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
