"""The gs scenario classes and the helpers the pricing layer uses to apply them.

Ported from gs_quant (Apache-2.0; see NOTICE): the dataclasses of `gs_quant.target.common` that
`gs_quant.risk` and `gs_quant.common` export (1.5.4 field order and defaults), and
`gs_quant.risk.scenarios` (`MarketDataShockBasedScenario`'s mapping constructor,
`MarketDataVolShockScenario.from_dataframe`). gs sends a scenario to its server; pricebt has no
server and no market data, so a scenario here is plain data: each asset config's `scenarios:`
section maps a `scenario_type` to a market transform that the config's own library implements
(docs/v2/SCENARIOS.md). Nothing in this module knows what a curve or a vol is.

`scenario_specs()` reads `Scenario.path` (pricebt.base) into the tuples of leaf scenarios the
pricing layer prices under: composites flattened outermost first, one entry per member of an
active `MultiScenario`.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import warnings
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional, Tuple, Union

import pandas as pd

from pricebt.base import DataContract, Scenario, get_enum_value
from pricebt.common import Currency, FallbackType, MarketDataShockType
from pricebt.datetime.relative_date import RelativeDate
from pricebt.errors import NotSupportedError

__all__ = [
    "Scenario",
    "MarketDataPattern",
    "MarketDataShock",
    "MarketDataPatternAndShock",
    "MarketDataVolSlice",
    "MarketDataScenario",
    "MarketDataShockType",
    "RollFwd",
    "CarryScenario",
    "CurveScenario",
    "IndexCurveShift",
    "CurveOverlay",
    "LiborFallbackScenario",
    "MarketDataShockBasedScenario",
    "MarketDataVolShockScenario",
    "MeasureScenario",
    "CompositeScenario",
    "MultiScenario",
]

_ENUM_FIELDS = {"shock_type": MarketDataShockType, "fallback_type": FallbackType, "currency": Currency}
_DATE_FIELDS = {"bucket_start", "bucket_end"}  # plain date fields that also accept an ISO string


def _as_date(v):
    if isinstance(v, str):
        return dt.date.fromisoformat(v)
    if isinstance(v, dt.datetime):
        return v.date()
    return v


def _coerce(obj) -> None:
    """gs `Base` coercion the scenarios need to be hashable cache keys: a list becomes a tuple, an
    enum field's string becomes the enum (leniently, gs `get_enum_value`), an ISO date string in a
    date field becomes a date."""
    for f in dataclasses.fields(obj):
        v = getattr(obj, f.name)
        if isinstance(v, list):
            v = tuple(v)
        enum = _ENUM_FIELDS.get(f.name)
        if enum is not None and v is not None:
            v = get_enum_value(enum, v)
        if f.name in _DATE_FIELDS and v is not None:
            v = _as_date(v)
        object.__setattr__(obj, f.name, v)


class _Data(DataContract):
    """Compact repr for the plain data classes: only the fields that are set."""

    def __repr__(self) -> str:
        set_ = ", ".join(f"{f.name}={getattr(self, f.name)!r}" for f in dataclasses.fields(self) if getattr(self, f.name) is not None)
        return f"{type(self).__name__}({set_})"


# ====================================================================================== data


@dataclass(unsafe_hash=True, repr=False)
class MarketDataPattern(_Data):
    """A filter over market data coordinates (`MarketDataPattern('IR', 'USD')`, `('IR Vol')`, ...).
    pricebt never matches it against anything: the asset config's transform decides what it means
    for that config's market."""

    mkt_type: Optional[str] = None
    mkt_asset: Optional[str] = None
    mkt_class: Optional[str] = None
    mkt_point: Optional[Tuple[str, ...]] = None
    mkt_quoting_style: Optional[str] = None
    is_active: Optional[bool] = None
    is_investment_grade: Optional[bool] = None
    currency: Optional[Currency] = None
    country_code: Optional[str] = None
    gics_sector: Optional[str] = None
    gics_industry_group: Optional[str] = None
    gics_industry: Optional[str] = None
    gics_sub_industry: Optional[str] = None
    name: Optional[str] = None

    def __post_init__(self):
        if isinstance(self.mkt_point, str):
            self.mkt_point = (self.mkt_point,)
        _coerce(self)


@dataclass(unsafe_hash=True, repr=False)
class MarketDataShock(_Data):
    """How to move the data a pattern matches: `MarketDataShock(MarketDataShockType.Absolute, 1e-4)`."""

    shock_type: MarketDataShockType = None
    value: float = None
    precision: Optional[float] = None
    cap: Optional[float] = None
    floor: Optional[float] = None
    coordinate_cap: Optional[float] = None
    coordinate_floor: Optional[float] = None
    name: Optional[str] = None

    def __post_init__(self):
        _coerce(self)


@dataclass(unsafe_hash=True, repr=False)
class MarketDataPatternAndShock(_Data):
    pattern: MarketDataPattern = None
    shock: MarketDataShock = None
    name: Optional[str] = None


@dataclass(unsafe_hash=True, repr=False)
class MarketDataVolSlice(_Data):
    """One expiry's vol levels at absolute strikes (`MarketDataVolShockScenario.vol_levels`)."""

    date: dt.date = None
    strikes: Tuple[float, ...] = None
    levels: Tuple[float, ...] = None
    name: Optional[str] = None

    def __post_init__(self):
        _coerce(self)
        self.date = _as_date(self.date)


@dataclass(unsafe_hash=True, repr=False)
class MarketDataScenario(_Data):
    """The scenario a result was priced under (`RiskKey.scenario`): one scenario, or a
    `CompositeScenario` of the active ones, outermost first (gs `PricingContext._scenario`)."""

    scenario: Scenario = None
    subtract_base: Optional[bool] = False
    name: Optional[str] = None


# ====================================================================================== date roll

_LOCATION_WARNED = False


def _holidays(holiday_calendar):
    """A `holiday_calendar` as the holiday dates a relative rule skips. gs passes a
    PricingLocation (a GS server calendar); pricebt has none (DEV-T3: weekends only), so a location
    is ignored with a one-time warning and an iterable of dates is used as given."""
    global _LOCATION_WARNED
    if holiday_calendar is None:
        return None
    if isinstance(holiday_calendar, (str, Enum)):
        if not _LOCATION_WARNED:
            warnings.warn(f"holiday_calendar {holiday_calendar!r} names a GS server calendar; pricebt rolls relative dates over weekends only (DEV-T3)", UserWarning, stacklevel=4)
            _LOCATION_WARNED = True
        return None
    return tuple(_as_date(d) for d in holiday_calendar)


def _roll(spec, holiday_calendar, current: dt.date, what: str) -> dt.date:
    """The valuation date `spec` rolls `current` to: an absolute date (or ISO string), else a
    relative rule ('1m', '22b', '6m') applied to `current` (pricebt.datetime rules)."""
    if spec is None:
        raise ValueError(f"{what} needs a date: an absolute date or a relative one such as '1m' or '22b'")
    if isinstance(spec, (dt.date, dt.datetime)):
        target = _as_date(spec)
    elif isinstance(spec, str):
        try:
            target = dt.date.fromisoformat(spec)
        except ValueError:
            target = RelativeDate(spec, current).apply_rule(holiday_calendar=_holidays(holiday_calendar))
    else:
        raise TypeError(f"{what}: date must be a date or a relative date string, got {spec!r}")
    if target < current:
        raise ValueError(f"{what}(date={spec!r}) rolls back from {current} to {target}; a roll only moves forward")
    return target


# ====================================================================================== scenarios


@dataclass(unsafe_hash=True, repr=False)
class RollFwd(Scenario):
    """Move the valuation date forward to `date` (absolute, or relative to the pricing date, e.g.
    '1m', '22b'): the asset config rolls its market (`realise_fwd`: forwards realised, else spot
    rates held) and every function sees `pricebt_date` = the rolled date, so trades lifecycle
    (expiries, matured legs). pricebt DEV-S2."""

    date: Optional[Union[dt.date, str]] = None
    realise_fwd: Optional[bool] = True
    holiday_calendar: Optional[str] = None
    scenario_type: Optional[str] = field(init=False, default="RollFwd")
    name: Optional[str] = None

    def __post_init__(self):
        _coerce(self)

    def _roll_target(self, current):
        return _roll(self.date, self.holiday_calendar, current, "RollFwd")


@dataclass(unsafe_hash=True, repr=False)
class CarryScenario(Scenario):
    """Roll the valuation date forward like `RollFwd`, `roll_to_fwds` choosing forwards or spot.
    pricebt DEV-S2: `time_shift` is GS server-defined and raises."""

    date: Optional[Union[dt.date, str]] = None
    time_shift: Optional[int] = None
    roll_to_fwds: Optional[bool] = True
    holiday_calendar: Optional[str] = None
    scenario_type: Optional[str] = field(init=False, default="CarryScenario")
    name: Optional[str] = None

    def __post_init__(self):
        _coerce(self)

    def _roll_target(self, current):
        if self.time_shift is not None:
            raise NotSupportedError("CarryScenario(time_shift=...) is defined GS server-side; pass date= (absolute or relative, e.g. '1m')")
        return _roll(self.date, self.holiday_calendar, current, "CarryScenario")


@dataclass(unsafe_hash=True, repr=False)
class CurveScenario(Scenario):
    """Reshape a curve: `parallel_shift` (bp) moves every point; `curve_shift` (bp) is the net
    change between `tenor_start` and `tenor_end` (years), zero at `pivot_point` (default their
    midpoint). The asset config's transform applies it."""

    market_data_pattern: Optional[MarketDataPattern] = None
    parallel_shift: Optional[float] = None
    curve_shift: Optional[float] = None
    pivot_point: Optional[float] = None
    tenor_start: Optional[float] = None
    tenor_end: Optional[float] = None
    shock_type: Optional[MarketDataShockType] = None
    scenario_type: Optional[str] = field(init=False, default="CurveScenario")
    name: Optional[str] = None

    def __post_init__(self):
        _coerce(self)


@dataclass(unsafe_hash=True, repr=False)
class IndexCurveShift(Scenario):
    """Shift an index (projection) curve: annualised parallel and slope shifts, an optional bucket
    shift between two dates, a cutoff and a floor."""

    market_data_pattern: Optional[MarketDataPattern] = None
    freeze_pattern: Optional[MarketDataPattern] = None
    annualised_parallel_shift: Optional[float] = None
    annualised_slope_shift: Optional[float] = None
    cutoff: Optional[float] = None
    floor: Optional[float] = None
    tenor: Optional[str] = None
    rate_option: Optional[str] = None
    bucket_shift: Optional[float] = None
    bucket_start: Optional[dt.date] = None
    bucket_end: Optional[dt.date] = None
    scenario_type: Optional[str] = field(init=False, default="IndexCurveShift")
    name: Optional[str] = None

    def __post_init__(self):
        _coerce(self)


@dataclass(unsafe_hash=True, repr=False)
class CurveOverlay(Scenario):
    """Replace a curve by user discount factors on `dates`. With `subtract_base`, results are the
    overlay value minus the unshocked one (pricebt DEV-S5)."""

    dates: Optional[Tuple[dt.date, ...]] = None
    discount_factors: Optional[Tuple[float, ...]] = None
    denominated: Optional[str] = None
    csa_term: Optional[str] = None
    tenor: Optional[str] = None
    rate_option: Optional[str] = None
    curve_type: Optional[str] = None
    subtract_base: Optional[bool] = None
    scenario_type: Optional[str] = field(init=False, default="CurveOverlay")
    name: Optional[str] = None

    def __post_init__(self):
        _coerce(self)
        if self.dates is not None:
            self.dates = tuple(_as_date(d) for d in self.dates)
        if self.discount_factors is not None:
            self.discount_factors = tuple(float(x) for x in self.discount_factors)
        if self.dates is not None and self.discount_factors is not None and len(self.dates) != len(self.discount_factors):
            raise ValueError(f"CurveOverlay: {len(self.dates)} dates but {len(self.discount_factors)} discount factors")


@dataclass(unsafe_hash=True, repr=False)
class LiborFallbackScenario(Scenario):
    date: Optional[dt.date] = None
    fallback_type: Optional[FallbackType] = FallbackType.RFR
    discounting: Optional[bool] = False
    cash_flows: Optional[bool] = True
    scenario_type: Optional[str] = field(init=False, default="LiborFallbackScenario")
    name: Optional[str] = None

    def __post_init__(self):
        _coerce(self)
        self.date = _as_date(self.date)


@dataclass(unsafe_hash=True, repr=False, init=False)
class MarketDataShockBasedScenario(Scenario):
    """Bespoke market data shocks: `MarketDataShockBasedScenario({MarketDataPattern('IR', 'USD'):
    MarketDataShock(MarketDataShockType.Absolute, 1e-4)})` (gs `gs_quant.risk.scenarios`). A tuple
    of `MarketDataPatternAndShock` is accepted too (the gs dataclass field)."""

    shocks: Tuple[MarketDataPatternAndShock, ...] = None
    scenario_type: Optional[str] = field(init=False, default="MarketDataShockBasedScenario")
    name: Optional[str] = None

    def __init__(self, shocks: Mapping, name: Optional[str] = None):
        if isinstance(shocks, Mapping):
            shocks = tuple(MarketDataPatternAndShock(p, s) for p, s in shocks.items())
        else:
            shocks = tuple(shocks)
            bad = [s for s in shocks if not isinstance(s, MarketDataPatternAndShock)]
            if bad:
                raise TypeError(f"shocks must be a {{MarketDataPattern: MarketDataShock}} mapping or MarketDataPatternAndShock items, got {bad[0]!r}")
        self.shocks = shocks
        self.scenario_type = "MarketDataShockBasedScenario"
        self.name = name


@dataclass(unsafe_hash=True, repr=False)
class MarketDataVolShockScenario(Scenario):
    """Override (or shock) a vol surface by expiry slices of absolute strikes."""

    pattern: MarketDataPattern = None
    shock_type: MarketDataShockType = None
    vol_levels: Tuple[MarketDataVolSlice, ...] = None
    ref_spot: float = None
    scenario_type: Optional[str] = field(init=False, default="MarketDataVolShockScenario")
    name: Optional[str] = None

    def __post_init__(self):
        _coerce(self)

    @classmethod
    def from_dataframe(cls, asset_ric: str, df: pd.DataFrame, ref_spot: float = None, name=None):
        """gs `from_dataframe`: the latest timestamp's rows of `df` (indexed by date/time, columns
        expirationDate, absoluteStrike, impliedVolatility), one slice per expiry, strikes ascending."""
        last = max(list(df.index))
        rows = df.loc[df.index == last]
        slices = []
        for key, group in rows.groupby("expirationDate"):
            ordered = group.sort_values("absoluteStrike")
            expiry = key.date() if hasattr(key, "date") and callable(key.date) else key
            slices.append(MarketDataVolSlice(expiry, tuple(ordered.absoluteStrike), tuple(ordered.impliedVolatility)))
        return cls(MarketDataPattern("Eq Vol", asset_ric), MarketDataShockType.Override, tuple(slices), ref_spot, name=name)


@dataclass(unsafe_hash=True, repr=False)
class MeasureScenario(Scenario):
    """Price as if `risk_measure` of each instrument were `value` (gs 0109 notebooks:
    `MeasureScenario(IRFwdRate, x)`). Per instrument, so the asset config maps it with
    `scope: trade`. pricebt DEV-S4: `value` is in the unit the asset's own mapping of that measure
    reports (bp for the shipped rate configs; gs: decimal)."""

    risk_measure: Any = None
    value: float = None
    scenario_type: Optional[str] = field(init=False, default="MeasureScenario")
    name: Optional[str] = None


@dataclass(unsafe_hash=True, repr=False)
class CompositeScenario(Scenario):
    """Several scenarios applied in order (first first). Nested `with` blocks build one."""

    scenarios: Optional[Tuple[Scenario, ...]] = None
    scenario_type: Optional[str] = field(init=False, default="CompositeScenario")
    name: Optional[str] = None

    def __post_init__(self):
        _coerce(self)


@dataclass(unsafe_hash=True, repr=False)
class MultiScenario(Scenario):
    """Price under each of `scenarios` separately: `calc` returns a `MultipleScenarioResult` keyed
    by scenario. pricebt DEV-S3: any mix of scenario types (gs: one type)."""

    scenarios: Optional[Tuple[Scenario, ...]] = None
    scenario_type: Optional[str] = field(init=False, default="MultiScenario")
    name: Optional[str] = None

    def __post_init__(self):
        _coerce(self)


_STRUCTURAL = (CompositeScenario, MultiScenario)


def scenario_types() -> Tuple[str, ...]:
    """Every `scenario_type` an asset config may map: each leaf scenario class's, including a
    user subclass of `Scenario` defined (and imported) before the config loads."""
    seen, stack = {}, [Scenario]
    while stack:
        cls = stack.pop()
        for sub in cls.__subclasses__():
            stack.append(sub)
            default = next((f.default for f in dataclasses.fields(sub) if f.name == "scenario_type"), None) if dataclasses.is_dataclass(sub) else getattr(sub, "scenario_type", None)
            if isinstance(default, str) and not issubclass(sub, _STRUCTURAL):
                seen[default] = None
    return tuple(sorted(seen))


# ====================================================================================== plain views


def _plain(v):
    if isinstance(v, Enum):
        return v.value
    if hasattr(type(v), "measure_type") and hasattr(type(v), "asset_class"):
        return v.name  # a RiskMeasure (duck-typed: pricebt.risk imports this module)
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return scenario_dict(v)
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, Mapping):
        return {k: _plain(x) for k, x in v.items()}
    return v


def scenario_dict(obj) -> dict:
    """The plain-data view an asset config's transform receives as `scenario`: every field (None
    when unset) with enums as their `.value`, nested patterns/shocks/slices as dicts, tuples as
    lists, a risk measure as its name; a scenario's `scenario_type` included."""
    out = {f.name: _plain(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, Scenario):
        out["scenario_type"] = obj.scenario_type
    return out


# ====================================================================================== the active stack


def _walk(scenario, out: list, allow_multi: bool) -> None:
    if isinstance(scenario, CompositeScenario):
        for s in scenario.scenarios or ():
            _walk(s, out, allow_multi)
    elif isinstance(scenario, MultiScenario):
        if not allow_multi:
            raise NotSupportedError("a MultiScenario inside a MultiScenario is not supported; list the combinations in one MultiScenario")
        out.append(scenario)
    elif isinstance(scenario, Scenario):
        out.append(scenario)
    else:
        raise TypeError(f"not a Scenario: {scenario!r}")


def scenario_specs(path: Optional[tuple] = None) -> List[Tuple[Optional[Scenario], Tuple[Scenario, ...]]]:
    """The scenario tuples to price under, from `path` (default `Scenario.path`, innermost first):
    `[(None, leaves)]` without a MultiScenario, else one `(member, leaves)` per member of the one
    active MultiScenario, its leaves in its place. Leaves are outermost first, composites flattened."""
    items: list = []
    for s in reversed(Scenario.path if path is None else path):
        _walk(s, items, allow_multi=True)
    multis = [i for i, s in enumerate(items) if isinstance(s, MultiScenario)]
    if not multis:
        return [(None, tuple(items))]
    if len(multis) > 1:
        raise NotSupportedError("more than one MultiScenario is active; build one MultiScenario of the combinations instead")
    i = multis[0]
    members = items[i].scenarios or ()
    if not members:
        raise ValueError("MultiScenario has no scenarios")
    out = []
    for member in members:
        inner: list = []
        _walk(member, inner, allow_multi=False)
        out.append((member, tuple(items[:i]) + tuple(inner) + tuple(items[i + 1:])))
    return out


def active_scenarios() -> Tuple[Scenario, ...]:
    """The leaves of the current path; a MultiScenario raises (it needs the calc layer)."""
    specs = scenario_specs()
    if specs[0][0] is not None:
        raise NotSupportedError("a MultiScenario is active: price through calc()/price(), which return a MultipleScenarioResult")
    return specs[0][1]


def market_data_scenario(leaves: Tuple[Scenario, ...]) -> Optional[MarketDataScenario]:
    """gs `PricingContext._scenario` for `RiskKey.scenario`: None, the one scenario, or a composite."""
    if not leaves:
        return None
    return MarketDataScenario(scenario=leaves[0] if len(leaves) == 1 else CompositeScenario(scenarios=leaves))


def require_no_scenario(what: str) -> None:
    """Raise when a scenario is active around something that does not price through calc()."""
    if Scenario.path:
        raise NotSupportedError(f"{what} under an active scenario ({Scenario.path[0]!r}) is not supported: scenarios apply to calc()/price()/resolve() only (pricebt DEV-S7)")
