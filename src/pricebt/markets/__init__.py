"""PricingContext, HistoricalPricingContext, and the seam functions _engine_calc / _engine_resolve.

gs-shaped context managers (DESIGN.md section 6.5). Only `pricing_date`, `csa_term` and `market`
actually do anything here; `market` must be None or a `CloseMarket` (DEV-M1), whose date then
overrides the date every market is evaluated on; every other constructor parameter is accepted,
for signature parity, and ignored. `PricingContext.current` is the innermost entered context on a
shared stack, else the default set by assigning `PricingContext.current = ...` (gs), else a fresh
default context (`pricing_date = date.today()`). An un-set `pricing_date`/`csa_term` is inherited
from the context that was current when this one was entered.

`_engine_calc`/`_engine_resolve` are thin seams: each function-level-imports `pricebt.assets.pricing`
(that module does not exist until P2.2) and delegates. Nothing in Phase 1 calls them.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

from pricebt.datetime import _LOCATION_TZ, date_range, prev_business_date
from pricebt.errors import NotSupportedError

__all__ = ["PricingContext", "HistoricalPricingContext", "CloseMarket", "close_market_date"]

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


def _location_code(location) -> str:
    """The location's code string (a `.value` or the string itself); an unknown one raises gs's
    `ValueError`. pricebt DEV-M1: None is LDN (gs's `close_market_date` raises on None; its
    `CloseMarket` falls back to LDN); there is no `PricingLocation` enum, so the code is a `str`."""
    code = getattr(location, "value", location) or "LDN"
    if code not in _LOCATION_TZ:
        raise ValueError(f"{location!r} is not a valid PricingLocation")
    return code


def close_market_date(location=None, date=None, roll_hr_and_min=(24, 0)) -> date:
    """gs `close_market_date`: `date` (default: the current pricing date), rolled to the previous
    business day while "now" in the location's timezone (default LDN) is before `date` plus
    `roll_hr_and_min` -- the day's close is not in yet."""
    date = date or PricingContext.current.pricing_date
    now = datetime.now(ZoneInfo(_LOCATION_TZ[_location_code(location)])).replace(tzinfo=None)
    if now < datetime(date.year, date.month, date.day) + timedelta(hours=roll_hr_and_min[0], minutes=roll_hr_and_min[1]):
        date = prev_business_date(date)
    return date


class CloseMarket:
    """gs `CloseMarket`: the close of `date` (default: the pricing date) in `location` (default
    LDN). Equal and hashed on `(date, location)`. pricebt DEV-M1: the location is its code string
    (gs: a `PricingLocation`), and it only picks the timezone of the close roll (each asset config's
    market is the market of a date)."""

    roll_hr_and_min = (24, 0)

    def __init__(self, date=None, location=None, check=True):
        self._date = date
        self._location = None if location is None else _location_code(location)
        self.check = check

    @property
    def date(self) -> date:
        if self._date is not None and not self.check:
            return self._date
        return close_market_date(self._location, self._date, self.roll_hr_and_min)

    @property
    def location(self) -> str:
        # gs market_location(): the context's market_data_location (pricebt ignores it), else LDN
        return self._location or "LDN"

    def to_dict(self) -> dict:
        return {"date": self.date, "location": self.location, "marketType": "CloseMarket"}

    def __repr__(self) -> str:
        return f"{self.date} ({self.location})"

    def __hash__(self):
        return hash((self.date, self.location))

    def __eq__(self, other):
        return isinstance(other, CloseMarket) and self.date == other.date and self.location == other.location


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
        if market is not None and not isinstance(market, CloseMarket):
            # pricebt DEV-M1: gs prices on any Market object; pricebt only knows the market of a
            # date (a CloseMarket), and pricing the context's own market instead would be a silent
            # wrong number
            raise NotSupportedError(f"PricingContext(market={market!r}) is not supported: pass a CloseMarket(date=...) (each asset config's own market for that date) or None")
        if market is not None and market.date > date.today():
            raise ValueError("The PricingContext does not support a market dated in the future. Please use the RollFwd Scenario to roll the pricing_date to a future date")
        self._market = market
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
    def market(self) -> Optional[CloseMarket]:
        """The given `CloseMarket` (own only, never inherited, as gs), else None. pricebt DEV-M1: gs
        builds a default `CloseMarket` at the pricing date's close; pricebt's default is each asset
        config's own market for the pricing date, so pricing today never rolls to yesterday."""
        return self._market

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
