"""`PricebtSession`: the replacement of `GsSession.use()` - where the stripped data infrastructure is plugged back in.

gs_quant resolves instruments and computes risk on GS servers after `GsSession.use(...)`. pricebt has no data infrastructure and no pricing library: you
supply a MARKET once (any MarketDataProvider - `get_pricer(ts, request) -> Pricer` - or {role: provider}), the time context (tz, business-day calendar, the
time of day a date maps to) and, for the gs-style instrument constructors, a STACK: the adapter that turns the market's snapshots into a library's pricers and
ships the instrument specs `IRSwap('Pay', '10y', 'USD')` looks up (spec F-2, F-6). Every `GenericEngine()` created without arguments then runs on it:

    from pricebt.session import PricebtSession
    PricebtSession.use(market=my_provider, stack=STACK, calendar="weekends")                        # one call, in place of GsSession.use()
    PricebtSession.use(market=my_provider, stack=STACK, instruments={"swap:USD": "usd_sofr_ois"})   # pick a spec by name for one asset class and currency

`STACK` is the adapter's `Stack` object, imported by you; a dotted path ('pricebt.<package>:STACK') works only for a package under `pricebt`, because config-style
paths are import-allow-listed (an adapter of your own is passed as the object).

`use()` makes the session the process-wide default immediately and returns it; it also works as a context manager, restoring the previous
default on exit (`with PricebtSession.use(market=m): ...`). `GenericEngine(market=...)` overrides the default for one engine.
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any, ClassVar, Dict, Iterable, Mapping, Optional

from .contracts.schema import SchemaRegistry
from .contracts.spec import InstrumentSpec, Stack, build_spec
from .errors import ConfigError, NotSupportedError
from .market import Binding, MarketData
from .registries import CALENDARS
from .registry import resolve_dotted
from .timeutil import DEFAULT_TZ, Calendar, Clock, TimeContext

DEFAULT_REPORTING_CURRENCY = "USD"  # gs_quant compatibility default: the currency results are reported in when a session does not say otherwise

__all__ = ["PricebtSession", "GsSession", "resolve_calendar", "current_session"]

_GS_ONLY = ("environment_or_domain", "client_id", "client_secret", "scopes", "api_version", "application", "http_adapter", "use_mds", "domain",
            "token", "is_gssso", "is_marquee_login", "csrf_token", "application_version")


_KEY = re.compile(r"\s*([A-Za-z][A-Za-z0-9_]*)\s*:\s*([A-Za-z]{3})\s*")
_CCY = re.compile(r"[A-Z]{3}")


def _instrument_keys(instruments: Any) -> Dict[str, Any]:
    """The session's `instruments` with every key canonical (`<asset_class>:<CCY>`: asset class lower-case, currency upper-case, the form `instrument_spec` looks up),
    so a key written 'swap:usd' overrides the stack default instead of being ignored. A malformed key, or two keys that are one once normalised, is an error."""
    if instruments is None:
        return {}
    if not isinstance(instruments, Mapping):
        raise ConfigError(f"instruments must be a mapping '<asset_class>:<CCY>' -> spec, got {type(instruments).__name__}", code="SESSION")
    out: Dict[str, Any] = {}
    for k, v in instruments.items():
        m = _KEY.fullmatch(k) if isinstance(k, str) else None
        if m is None:
            raise ConfigError(f"instruments key {k!r} is not '<asset_class>:<CCY>': an asset class, a colon and a three-letter currency code, for example 'swap:USD'", code="SESSION")
        key = f"{m.group(1).lower()}:{m.group(2).upper()}"
        if key in out:
            raise ConfigError(f"instruments key {key!r} is given twice (keys are read case-insensitively): {sorted(map(str, instruments))}", code="SESSION")
        out[key] = v
    return out


def _reporting_currency(x: Any) -> str:
    if x is None:
        return DEFAULT_REPORTING_CURRENCY
    s = str(getattr(x, "value", x)).strip().upper()
    if not _CCY.fullmatch(s):
        raise ConfigError(f"reporting_currency must be a three-letter currency code (None means {DEFAULT_REPORTING_CURRENCY}), got {x!r}", code="SESSION")
    return s


def resolve_calendar(cal: Any) -> Calendar:
    """None | 'weekends' -> weekends only; a name registered in `pricebt.registries.CALENDARS` (adapters register theirs when imported); a path to a
    calendar json; a pricebt Calendar; or an iterable of holiday dates."""
    if cal is None:
        return Calendar.weekends_only()
    if isinstance(cal, Calendar):
        return cal
    if isinstance(cal, (str, Path)):
        s = str(cal)
        key = s.replace("-", "_").lower()  # how a Registry keys a name
        if key in ("weekends", "weekends_only", "none"):
            return Calendar.weekends_only()
        if key in CALENDARS.names():
            named = CALENDARS.resolve(s)
            return resolve_calendar(named() if callable(named) and not isinstance(named, Calendar) else named)
        if s.lower().endswith(".json") and Path(s).exists():
            return Calendar.from_json(s)
        raise ConfigError(f"unknown calendar {s!r}: use 'weekends', a registered name {CALENDARS.names()}, a calendar .json path, a pricebt Calendar or a list of holidays "
                          "(a named calendar is registered by the adapter that owns it: pass its stack to the session first)", code="SESSION")
    if isinstance(cal, Iterable):
        return Calendar(list(cal))
    raise ConfigError(f"cannot interpret {cal!r} as a calendar", code="SESSION")


class PricebtSession:
    """A market plus its time context. `settings` are extra `EngineSettings` fields (e.g. {'on_error': 'record', 'fill_lag': 1})."""

    _current: ClassVar[Optional["PricebtSession"]] = None

    def __init__(self, market: Any = None, *, tz: str = DEFAULT_TZ, calendar: Any = None, date_policy: str = "close", session_open: dt.time = dt.time(8, 0),
                 session_close: dt.time = dt.time(17, 0), settings: Optional[Mapping[str, Any]] = None, stack: Any = None, wrap: Any = None,
                 instruments: Optional[Mapping[str, Any]] = None, reporting_currency: str = DEFAULT_REPORTING_CURRENCY):
        """`stack`: an adapter's `Stack` object, or a dotted path to one under `pricebt` ('pricebt.<package>:STACK'; any other prefix is refused by the import
        allow-list); its `wrap` applies to the primary market role unless `wrap=` (a callable, a dotted path or {role: callable}) says otherwise. `instruments`:
        {'<asset_class>:<CCY>': name-in-the-stack | raw instrument spec | InstrumentSpec} overriding the stack's defaults; the keys are read case-insensitively
        ('swap:usd' is 'swap:USD') and a malformed key is refused. `reporting_currency`: the one currency (a three-letter code, default USD) results and the shipped
        constructors support (multi-currency is out of scope)."""
        if market is None:
            raise ConfigError("PricebtSession needs a market: a MarketDataProvider (get_pricer(ts, request) -> Pricer) or {role: provider}", code="SESSION")
        if date_policy not in ("open", "close", "midnight"):
            raise ConfigError(f"date_policy must be open|close|midnight, got {date_policy!r}", code="SESSION")
        self.market = market
        self.stack: Optional[Stack] = resolve_dotted(stack) if isinstance(stack, str) else stack
        if self.stack is not None and not isinstance(self.stack, Stack):
            raise ConfigError(f"stack must be a pricebt Stack or a dotted path to one, got {type(self.stack).__name__}", code="SESSION")
        self.wrap = wrap if wrap is not None else (self.stack.wrap if self.stack is not None else None)
        self.reporting_currency = _reporting_currency(reporting_currency)
        self._instruments = _instrument_keys(instruments)
        self._specs: Dict[str, InstrumentSpec] = {}
        self.tz = tz
        self.calendar = resolve_calendar(calendar)
        self.date_policy = date_policy
        self.session_open = session_open
        self.session_close = session_close
        self.settings: Dict[str, Any] = dict(settings or {})
        self._prev: Optional[PricebtSession] = None
        self._active = False

    # ---- the process-wide default
    @classmethod
    def use(cls, market: Any = None, **kw: Any) -> "PricebtSession":
        """Make a session the default for `GenericEngine()` and return it (usable as a context manager)."""
        gs = sorted(k for k in kw if k in _GS_ONLY)
        if market is None:
            raise NotSupportedError(
                "pricebt has no GS session: supply the market once instead, e.g. PricebtSession.use(market=<provider>, stack=<adapter Stack>, calendar=<name or holidays>) "
                f"(any MarketDataProvider works){'; gs-only arguments given: ' + str(gs) if gs else ''}"
            )
        for k in gs:
            kw.pop(k)
        s = cls(market, **kw)
        s._activate()
        return s

    @classmethod
    def get_current(cls) -> Optional["PricebtSession"]:
        return PricebtSession._current

    @classmethod
    def reset(cls) -> None:
        """Clear the default session (tests)."""
        PricebtSession._current = None

    def _activate(self) -> None:
        if PricebtSession._current is self:
            return
        self._prev = PricebtSession._current
        PricebtSession._current = self
        self._active = True

    def close(self) -> None:
        if PricebtSession._current is self:
            prev = self._prev
            while prev is not None and not prev._active:  # sessions closed out of order are skipped, never made current again
                prev = prev._prev
            PricebtSession._current = prev
        self._active = False

    def __enter__(self) -> "PricebtSession":
        self._activate()
        return self

    def __exit__(self, *exc: Any) -> bool:
        self.close()
        return False

    # ---- what the engine needs
    @property
    def daily_time(self) -> dt.time:
        """Time of day a DATE maps to (grid points, trigger dates, date-only data)."""
        return {"open": self.session_open, "close": self.session_close, "midnight": dt.time(0, 0)}[self.date_policy]

    def time_context(self, holiday_calendar: Optional[Iterable[Any]] = None) -> TimeContext:
        extra = list(holiday_calendar) if holiday_calendar is not None else []
        cal = self.calendar.extended(extra) if extra else self.calendar
        return TimeContext(tz=self.tz, calendar=cal, date_policy=self.date_policy, session_open=self.session_open, session_close=self.session_close)

    def market_data(self, request: Optional[Mapping[str, Any]] = None) -> MarketData:
        """A fresh MarketData (own clock and memo) over the session's providers; `request` is merged into every provider request and the session's `wrap`
        is applied to every role that has none of its own."""
        m = self.market
        if isinstance(m, MarketData):
            bindings: Dict[str, Any] = dict(m.bindings)
        elif isinstance(m, Mapping):
            bindings = dict(m)
        else:
            bindings = {"primary": m}
        wraps = self._wraps(list(bindings))
        out: Dict[str, Binding] = {}
        for role, b in bindings.items():
            b = b if isinstance(b, Binding) else Binding(b)
            out[role] = Binding(b.mdp, {**dict(b.request), **dict(request or {})}, b.wrap or wraps.get(role))
        return MarketData(out, Clock())

    def _wraps(self, roles: Iterable[str]) -> Dict[str, Any]:
        w = self.wrap
        if w is None:
            return {}
        if isinstance(w, Mapping):
            return {r: resolve_dotted(f) if isinstance(f, str) else f for r, f in w.items()}
        fn = resolve_dotted(w) if isinstance(w, str) else w
        return {"primary": fn} if "primary" in set(roles) else {r: fn for r in roles}

    # ---- the gs-style constructors' instrument lookup (spec F-2)
    def instrument_spec(self, asset_class: str, currency: str) -> InstrumentSpec:
        """The instrument spec registered for (asset class, currency): the session's own `instruments` entry, else the stack's default."""
        asset_class = str(asset_class).strip().lower()
        key = f"{asset_class}:{str(currency).strip().upper()}"
        if key in self._specs:
            return self._specs[key]
        entry = self._instruments.get(key)
        if entry is None and self.stack is not None:
            entry = self.stack.defaults.get(key)
        if entry is None:
            known = sorted({*self._instruments, *(self.stack.defaults if self.stack else ())})
            raise NotSupportedError(f"no instrument spec is registered for {key!r} (registered: {known}). Register one once: PricebtSession.use(market=..., stack=<an adapter's Stack>) "
                                    f"or instruments={{{key!r}: <spec name or raw spec>}}")
        if isinstance(entry, InstrumentSpec):
            spec = entry
        else:
            if isinstance(entry, str):
                pool = dict(self.stack.instruments) if self.stack is not None else {}
                if entry not in pool:
                    raise ConfigError(f"instrument {entry!r} (for {key!r}) is not shipped by the session's stack (it ships {sorted(pool)})", code="SESSION")
                name, raw = entry, pool[entry]
            else:
                name, raw = key.replace(":", "_").lower(), entry
            spec = build_spec(name, raw, schemas=SchemaRegistry.default())
        if spec.asset_class != asset_class:
            raise ConfigError(f"the spec registered for {key!r} has asset class {spec.asset_class!r}", code="SESSION")
        self._specs[key] = spec
        return spec

    def accepted_gs(self, asset_class: str, currency: str) -> Mapping[str, Any]:
        """The gs constructor values (`Stack.accepts_gs`) the spec in force for (asset class, currency) already implies. The stack vouches for the specs it ships (its
        default, or one it ships that the session picks by name) but not for a raw spec or an InstrumentSpec the session substitutes: that one's conventions are the
        session's own, so nothing is accepted on its behalf."""
        key = f"{str(asset_class).strip().lower()}:{str(currency).strip().upper()}"
        entry = self._instruments.get(key)
        if self.stack is None or (entry is not None and not isinstance(entry, str)):
            return {}
        return self.stack.accepts_gs

    def __repr__(self) -> str:
        return f"PricebtSession(market={type(self.market).__name__}, tz={self.tz!r}, calendar={self.calendar!r}, date_policy={self.date_policy!r})"


class GsSession(PricebtSession):
    """Alias kept so `from pricebt.session import GsSession` imports. `GsSession.use()` needs `market=`: there is no GS session."""


def current_session() -> Optional[PricebtSession]:
    return PricebtSession._current
