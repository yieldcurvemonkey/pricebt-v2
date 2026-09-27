"""RLCurvePricer: an immutable market snapshot as the rateslib adapter's instruments see it: a float SOFR-style discount curve, published fixings (percent), the
snapshot's calendars, and an optional bond quote panel. Built by `wrap` from a plain-data snapshot (spec D3); it exposes objects as attributes, which the
bindings reference (`@pricer.curve`), and offers no method that resolves a convention (spec D7)."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional

import pandas as pd

from ...errors import ConfigError, MarketDataUnavailable
from ...pricer import PricerBase
from ...snapshot import CalendarData
from . import _compat as C
from .conventions import rl_calendar


@dataclass(frozen=True)
class BondQuote:
    """One bond's market quote: clean price (per 100 face) and/or yield to maturity (percent). At least one is required."""

    cusip: str
    clean: Optional[float] = None
    ytm: Optional[float] = None

    def __post_init__(self) -> None:
        if self.clean is None and self.ytm is None:
            raise ConfigError(f"quote for {self.cusip} needs a clean price or a ytm", code="QUOTE")


@dataclass(frozen=True)
class BondRef:
    """Static reference data of a fixed-coupon bond."""

    cusip: str
    coupon: float
    issue_date: dt.date
    maturity_date: dt.date
    label: str = ""


class RLCurvePricer(PricerBase):
    """Snapshot at `ts` of a float node-table curve (DF nodes) anchored at `reference_date`.

    fixings: the SOFR-style history in PERCENT (midnight index) exactly as the provider published it at `ts` (the provider owns the publication policy).
    bonds / quotes: reference table {cusip: BondRef}, quote panel {cusip: BondQuote}; `universe` resolves on-the-run aliases.
    calendars: the snapshot's calendars by name; `calendar(name)` is the library's calendar object built from them (memoised).
    """

    LOOKUPS = ("fixings_for", "bond", "bond_quote", "ytm_curve", "quoted_cusips")

    def __init__(
        self,
        ts: pd.Timestamp,
        curve: Any,
        *,
        reference_date: Optional[dt.date] = None,
        fixings: Optional[pd.Series] = None,
        bonds: Optional[Mapping[str, BondRef]] = None,
        quotes: Optional[Mapping[str, BondQuote]] = None,
        universe: Any = None,
        ytm_curve: Optional[Callable[[float], float]] = None,
        calendars: Optional[Mapping[str, CalendarData]] = None,
        source: str = "",
        tz: str = "America/New_York",
    ):
        ts = pd.Timestamp(ts)
        if ts.tzinfo is None:
            raise ConfigError("RLCurvePricer.ts must be tz-aware", code="PRICER")
        init = C.initial_date(curve)
        if reference_date is not None and C.to_date(reference_date) != init:
            raise ConfigError(f"reference_date {reference_date} != curve initial node {init}", code="PRICER")
        object.__setattr__(self, "_frozen", False)
        self.ts = ts
        self.reference_date = init
        self.curve = curve
        self.tz = tz
        self.fixings = fixings
        self._bonds = dict(bonds or {})
        self._quotes = dict(quotes or {})
        self.universe = universe
        self._ytm_curve = ytm_curve
        self.calendars: Dict[str, CalendarData] = dict(calendars or {})
        self._rl_calendars: Dict[str, Any] = {}
        self._memo: Dict[Any, Any] = {}
        self.source = source
        object.__setattr__(self, "_frozen", True)

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_frozen", False):
            raise AttributeError(f"{type(self).__name__} is immutable")
        object.__setattr__(self, name, value)

    def calendar(self, name: str) -> Any:
        """The library's calendar object of the snapshot calendar `name`."""
        cal = self._rl_calendars.get(name)
        if cal is None:
            cd = self.calendars.get(name)
            if cd is None:
                raise ConfigError(f"the snapshot has no calendar {name!r}; it has {sorted(self.calendars)}", code="CFG-CONVENTION")
            cal = self._rl_calendars[name] = rl_calendar(cd)
        return cal

    def memo(self, key: Any, fn: Callable[[], Any]) -> Any:
        """Per-snapshot cache of derived objects (calibrated risk curves): a pricer is immutable data, so it can never go stale."""
        if key not in self._memo:
            self._memo[key] = fn()
        return self._memo[key]

    # ------------------------------------------------------------------ Pricer contract
    def describe(self) -> Mapping[str, Any]:
        f = self.fixings
        return {
            "type": type(self).__name__, "source": self.source, "ts": str(self.ts), "reference_date": str(self.reference_date),
            "n_nodes": int(self.curve.nodes.n), "curve_id": self.curve.id, "staleness_s": None,
            "fixings_last": None if f is None or len(f) == 0 else str(f.index[-1].date()), "n_quotes": len(self._quotes),
        }

    # ------------------------------------------------------------------ lookups
    def fixings_for(self, start: Any) -> pd.Series:
        if self.fixings is None:
            raise MarketDataUnavailable(self.ts, {}, "this pricer carries no fixings")
        return self.fixings[self.fixings.index >= pd.Timestamp(C.to_dt(start))]

    def bond(self, alias_or_cusip: str) -> BondRef:
        key = alias_or_cusip.strip()
        if self.universe is not None:
            return self.universe.resolve(key, self.reference_date)
        if key not in self._bonds:
            raise MarketDataUnavailable(self.ts, {"bond": key}, f"unknown bond {key!r}; known: {sorted(self._bonds)[:10]}")
        return self._bonds[key]

    def bond_quote(self, cusip: str) -> BondQuote:
        q = self._quotes.get(cusip)
        if q is None:
            raise MarketDataUnavailable(self.ts, {"cusip": cusip}, f"no quote for {cusip} at {self.ts}")
        return q

    def ytm_curve(self) -> Optional[Callable[[float], float]]:
        """Optional callable ttm_years -> par-ish ytm (percent) used only by the bond `roll` layer (None: roll = 0)."""
        return self._ytm_curve

    def quoted_cusips(self) -> List[str]:
        return sorted(self._quotes)
