"""PricingContext, HistoricalPricingContext, and the seam functions _engine_calc / _engine_resolve.

gs-shaped context managers (DESIGN.md section 6.5; docs/v2/MARKET_CONTEXT_DESIGN.md section 5).
`pricing_date`, `csa_term`, `market`, `market_data_location` and `show_progress` do something
here: `market` must be None or a `CloseMarket` (DEV-M1), whose date then overrides the date every
market is evaluated on; `market_data_location` is the location whose market every asset config and
market provider is asked for (`pricebt_location`, DEV-M3). Every other constructor parameter is
kept and read back through gs's own property (inherited as in gs), and changes nothing: pricebt
has no server to batch, cache or prioritise on. `PricingContext.current` is the innermost entered
context on a shared stack, else the default set by assigning `PricingContext.current = ...` (gs),
else a fresh default context (`pricing_date = date.today()`). An un-set value is inherited from
the context that was current when this one was entered.

`_engine_calc`/`_engine_resolve` are thin seams: each function-level-imports `pricebt.assets.pricing`
(that module does not exist until P2.2) and delegates. Nothing in Phase 1 calls them.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from inspect import signature
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

from pricebt.datetime import _LOCATION_TZ, date_range, prev_business_date
from pricebt.errors import NotSupportedError

__all__ = ["PricingContext", "HistoricalPricingContext", "CloseMarket", "close_market_date", "market_location", "DEFAULT_MARKET_DATA_LOCATION"]

DEFAULT_MARKET_DATA_LOCATION = "LDN"  # gs: PricingLocation.LDN, the default of PricingContext.market_data_location

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


def market_location(location=None) -> str:
    """gs `market_location`: `location`, else the current context's `market_data_location` (LDN by
    default). pricebt DEV-M1: the code string, not a `PricingLocation`."""
    if location is None:
        return PricingContext.current.market_data_location
    return getattr(location, "value", location)


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
    """gs `CloseMarket`: the close of `date` (default: the pricing date) in `location` (default:
    the current context's `market_data_location`, LDN unless set). Equal and hashed on
    `(date, location)`. pricebt DEV-M1: the location is its code string (gs: a `PricingLocation`).
    It picks the timezone of the close roll, and a `PricingContext(market=CloseMarket(...))` takes
    its `market_data_location` from it (gs), the location configs see as `pricebt_location`."""

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
        return market_location(self._location)

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
        market_data_location = getattr(market_data_location, "value", market_data_location)
        if market is not None and market_data_location and market.location != market_data_location:
            raise ValueError("market.location and market_data_location cannot be different")
        if not market_data_location and market is not None:
            market_data_location = market.location  # gs: the market's location is the context's
        self._market = market
        self._pricing_date = pricing_date
        self._csa_term = csa_term
        self._is_async = is_async
        self._market_data_location = market_data_location or None
        self._is_batch = is_batch
        self._use_cache = use_cache
        self._visible_to_gs = visible_to_gs
        self._request_priority = request_priority
        self._timeout = timeout
        self._show_progress = show_progress
        self._use_server_cache = use_server_cache
        self._market_behaviour = market_behaviour
        self._set_parameters_only = set_parameters_only
        self._use_historical_diddles_only = use_historical_diddles_only
        self._provider = provider
        self._is_entered = False
        self._parent: Optional["PricingContext"] = None
        self._bar = None  # the show_progress bar the pricing layer opened under this context, if any

    def _inherited(self, field: str, truthy: bool = False):
        """This context's own `field`, else the nearest parent's. The walk stops at a context it has
        seen: `with PricingContext.current:` after assigning it (or entering the default below a
        context that inherits from it) makes a context its own ancestor (gs guards with
        `current is not self`). `truthy`: a falsy own value inherits too, as gs's `x if x else
        inherited` properties do (`is_batch=False` under a batch context is still batch)."""
        ctx, seen = self, set()
        while ctx is not None and id(ctx) not in seen:
            value = getattr(ctx, field)
            if value if truthy else value is not None:
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

    @property
    def market_data_location(self) -> str:
        """gs: the location for sourcing market data, own or inherited, LDN by default. pricebt
        DEV-M3: the code string (gs: a `PricingLocation`), any string a config understands; every
        asset config and market provider reads it as `pricebt_location`, and values priced under
        two locations never share a cache (one `PricingService` per location)."""
        return self._inherited("_market_data_location") or DEFAULT_MARKET_DATA_LOCATION

    # gs properties with no effect in pricebt (no server to batch, cache or prioritise on): own
    # value, else inherited, else gs's default -- so code that reads them back runs unchanged.
    @property
    def is_batch(self) -> bool:
        return self._inherited("_is_batch", truthy=True) or False

    @property
    def use_cache(self) -> bool:
        return self._inherited("_use_cache", truthy=True) or False

    @property
    def visible_to_gs(self) -> Optional[bool]:
        return self._inherited("_visible_to_gs", truthy=True)

    @property
    def request_priority(self) -> Optional[int]:
        return self._inherited("_request_priority", truthy=True)

    @property
    def timeout(self) -> Optional[int]:
        return self._inherited("_timeout", truthy=True)

    @property
    def show_progress(self) -> bool:
        """gs: a tqdm progress bar. pricebt draws one over the dates of a
        `HistoricalPricingContext` calculation (`assets.pricing.engine_calc`)."""
        return self._inherited("_show_progress", truthy=True) or False

    @property
    def use_server_cache(self) -> bool:
        return self._inherited("_use_server_cache") or False

    @property
    def market_behaviour(self) -> str:
        return self._inherited("_market_behaviour", truthy=True) or "ContraintsBased"

    @property
    def use_historical_diddles_only(self) -> bool:
        return self._inherited("_use_historical_diddles_only") or False

    @property
    def provider(self):
        return self._inherited("_provider")

    @property
    def set_parameters_only(self) -> bool:
        return self._set_parameters_only

    @property
    def active_context(self) -> "PricingContext":
        """gs: the outermost entered context that is not `set_parameters_only` (the one whose exit
        submits the batch), never one below this context on the stack; else this context."""
        for ctx in _STACK:
            if ctx is self:
                break
            if ctx.is_entered and not ctx.set_parameters_only:
                return ctx
        return self

    @property
    def is_current(self) -> bool:
        return self is PricingContext.current

    @classmethod
    def default_value(cls) -> "PricingContext":
        return cls()

    def clone(self, **kwargs):
        """gs: a new context of this class from this one's (own or inherited) values, `kwargs`
        overriding. As in gs, a constructor parameter with no same-named attribute clones as None."""
        clone_kwargs = {k: getattr(self, k, None) for k in signature(self.__init__).parameters}
        clone_kwargs.update(kwargs)
        return self.__class__(**clone_kwargs)

    def calc(self, instrument, risk_measure):
        """gs: the future of `risk_measure` of `instrument` under this context (gs's own advice:
        use `instrument.calc(...)` inside the context instead)."""
        if self.is_entered and self.is_current:
            return _engine_calc(instrument, risk_measure)
        with self:
            return _engine_calc(instrument, risk_measure)

    def __enter__(self) -> "PricingContext":
        self._parent = _STACK[-1] if _STACK else _DEFAULT
        _STACK.append(self)
        self._is_entered = True
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        _STACK.pop()
        self._is_entered = False
        bar, self._bar = self._bar, None
        if bar is not None:
            bar.__exit__(exc_type, exc, tb)
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
