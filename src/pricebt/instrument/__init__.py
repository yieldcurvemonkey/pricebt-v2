"""Instrument, ConfigInstrument, instrument_identity, and the generated gs instrument classes
(IRSwap, IRSwaption, FXOption, FXForward, EqOption, InflationSwap, Cash, Bond); re-exports OptionStyle,
OptionType, Currency, PayReceive, BuySell, SwapClearingHouse, SwapSettlement.

The generated classes are built at import time from `_gs_fields.GS_FIELDS` (DESIGN.md section 5.1):
each gets a real `__init__`, built by `exec` of a small generated function body, whose
`inspect.signature` matches the gs constructor exactly (gs field order and defaults, `name` last,
then the keyword-only pricebt extensions `pricebt_asset`/`quantity_`, then `**kwargs`).

Per the import DAG (DESIGN.md section 3.2 item 4), this module imports `pricebt.session` and
`pricebt.markets` only inside method bodies, never at module top level.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, Optional

from pricebt.base import Priceable
from pricebt.common import (
    AccrualConvention,
    AssetClass,
    AssetType,
    BusinessDayConvention,
    BuySell,
    Currency,
    DayCountFraction,
    OptionExerciseStyle,
    OptionSettlementMethod,
    OptionStyle,
    OptionType,
    PayReceive,
    PrincipalExchange,
    SwapClearingHouse,
    SwapSettlement,
    TradeAs,
    UnderlierType,
)
from pricebt.errors import ConfigError, PricebtError
from pricebt.instrument._gs_fields import GS_FIELDS
from pricebt.risk import DollarPrice, Price

# ------------------------------------------------------------------------------------ enum coercion

# Every coerce_tag value that appears in GS_FIELDS, mapped to the actual pricebt enum class
# (DESIGN.md section 5.2: "A gs field whose coerce_tag names an enum is coerced to the pricebt
# enum with gs rules").
_ENUM_CLASSES: Dict[str, type] = {
    "AccrualConvention": AccrualConvention,
    "BusinessDayConvention": BusinessDayConvention,
    "BuySell": BuySell,
    "Currency": Currency,
    "DayCountFraction": DayCountFraction,
    "OptionExerciseStyle": OptionExerciseStyle,
    "OptionSettlementMethod": OptionSettlementMethod,
    "OptionStyle": OptionStyle,
    "OptionType": OptionType,
    "PayReceive": PayReceive,
    "PrincipalExchange": PrincipalExchange,
    "SwapClearingHouse": SwapClearingHouse,
    "SwapSettlement": SwapSettlement,
    "TradeAs": TradeAs,
    "UnderlierType": UnderlierType,
}

_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


def _to_snake(name: str) -> str:
    """gs `handle_camel_case_args`/`_get_underscore` (DESIGN.md section 5.2, R04 section 3.1): a
    kwarg that is not ALL-CAPS is converted to snake_case; an already-snake_case name is a no-op."""
    if name.isupper():
        return name
    return _CAMEL_BOUNDARY.sub("_", name).lower()


def _coerce(enum_map: Dict[str, type], key: str, value: Any) -> Any:
    cls = enum_map.get(key)
    if cls is None or value is None or isinstance(value, cls):
        return value
    return cls(value)  # strict: raises ValueError on an invalid value (EnumBase._missing_ + Enum)


def _merge_extra_kwargs(base: Dict[str, Any], extra_kwargs: Dict[str, Any], enum_map: Dict[str, type]) -> Dict[str, Any]:
    """Fold `extra_kwargs` (camelCase aliases of real fields, or true pass-through extras) into a
    copy of `base`. Raises ValueError when the same field is named twice under two different
    spellings (gs: "fooBar and foo_bar both specified")."""
    merged = dict(base)
    seen: Dict[str, str] = {}
    for orig_key, value in extra_kwargs.items():
        snake_key = _to_snake(orig_key)
        if snake_key in seen:
            raise ValueError(f"{seen[snake_key]} and {orig_key} both specified")
        seen[snake_key] = orig_key
        if snake_key in base and orig_key != snake_key:
            raise ValueError(f"{orig_key} and {snake_key} both specified")
        value = _coerce(enum_map, snake_key, value)
        if value is None:
            continue
        merged[snake_key] = value
    return merged


def _freeze(v: Any) -> Any:
    """DESIGN.md section 5.1 item 3's freeze rule: dict -> sorted tuple of items; list/tuple ->
    tuple (recursively); other hashable -> as is; else -> repr(v)."""
    if isinstance(v, dict):
        return tuple(sorted((k, _freeze(x)) for k, x in v.items()))
    if isinstance(v, (list, tuple)):
        return tuple(_freeze(x) for x in v)
    try:
        hash(v)
        return v
    except TypeError:
        return repr(v)


def _current_session():
    """Best-effort lookup of `PricebtSession.current`. `pricebt.session` is imported here (never
    at module top level, per the import DAG) and may not yet define `PricebtSession` while P2.3 is
    still in progress; that is treated the same as "no session"."""
    try:
        from pricebt.session import PricebtSession
    except ImportError:
        return None
    return PricebtSession.current


def _bind_instrument(self: "Instrument", field_values: Dict[str, Any], enum_map: Dict[str, type], name, pricebt_asset, quantity_, kwargs: Dict[str, Any]) -> None:
    """Shared `__init__` body for every generated class and `ConfigInstrument` (DESIGN.md section
    5.1): builds `_kwargs` from the real gs fields actually given (None dropped, enum-coerced) plus
    any extra/camelCase kwargs, and sets every other plain attribute of the object contract."""
    base: Dict[str, Any] = {}
    for k, v in field_values.items():
        if v is None:
            continue
        base[k] = _coerce(enum_map, k, v)
    self.name = name
    self.quantity_ = quantity_
    self.pricebt_asset = pricebt_asset
    self.position_meta = None
    self.resolved_terms = None
    self.resolution_key = None
    self.resolution_csa = None
    self.unresolved = None
    self._matched_asset = None
    self._kwargs = _merge_extra_kwargs(base, kwargs, enum_map)


# ------------------------------------------------------------------------------------ Instrument


class Instrument(Priceable):
    """Base of every pricebt instrument: the generated gs-shaped classes (IRSwap, ...) below, and
    `ConfigInstrument`. See DESIGN.md section 5.1 for the full object contract."""

    asset_class = None
    type_ = None

    # --- attribute lookup (DESIGN.md section 5.1 item 2) -----------------------------------------
    def __getattr__(self, field: str) -> Any:
        if field.startswith("_"):
            # Read nothing else: this is what makes deepcopy/pickle probes (`__deepcopy__`,
            # `__setstate__`, ...) safe before `_kwargs`/`resolved_terms` even exist.
            raise AttributeError(field)
        if self.resolved_terms is not None:
            session = _current_session()
            if session is not None:
                # DESIGN.md section 5.1 item 2: "step 1 is ... skipped" when the asset can't be
                # matched -- self.asset_config raises ConfigError in that case, so this falls
                # through to steps 2-4 instead of propagating.
                try:
                    asset = self.asset_config
                except ConfigError:
                    asset = None
                if asset is not None and field in asset.attributes:
                    return session.pricing.attribute(self, field)
            if field in self.resolved_terms:
                return self.resolved_terms[field]
        if field in self._kwargs:
            return self._kwargs[field]
        raise AttributeError(field)

    # --- properties --------------------------------------------------------------------------------
    @property
    def kwargs(self) -> Dict[str, Any]:
        return dict(self._kwargs)

    @property
    def instrument_quantity(self) -> float:
        return self.quantity_

    @property
    def asset_config(self):
        from pricebt.session import PricebtSession  # import DAG: instrument -> session only in method bodies

        session = PricebtSession.current
        if session is None:
            raise PricebtError("no PricebtSession: call PricebtSession.use(assets=[...]) first")
        cfg = session.registry.match(type(self).__name__, self.kwargs, self.pricebt_asset)
        self._matched_asset = cfg.name
        return cfg

    # --- eq/hash (DESIGN.md section 5.1 item 3) ------------------------------------------------
    def _identity_key(self):
        frozen_resolved = _freeze(self.resolved_terms) if self.resolved_terms is not None else None
        return (type(self), self.pricebt_asset, _freeze(self._kwargs), self.quantity_, self.name, frozen_resolved)

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Instrument):
            return NotImplemented
        return self._identity_key() == other._identity_key()

    def __hash__(self) -> int:
        return hash(self._identity_key())

    def __repr__(self) -> str:
        cls_name = type(self).__name__
        return f"{cls_name}({self.name})" if self.name else cls_name

    # --- clone / scale / flip (DESIGN.md section 5.1 items 4-5) --------------------------------
    def clone(self, **kw: Any) -> "Instrument":
        name = kw.pop("name", self.name)
        quantity_ = kw.pop("quantity_", self.quantity_)
        pricebt_asset = kw.pop("pricebt_asset", self.pricebt_asset)
        if kw:
            if self.resolved_terms is not None:
                raise ValueError("clone a resolved instrument only with name/quantity_")
            enum_map = _ENUM_MAP.get(type(self), {})
            new_kwargs = _merge_extra_kwargs(self._kwargs, kw, enum_map)
        else:
            new_kwargs = dict(self._kwargs)
        new = object.__new__(type(self))
        new.name = name
        new.quantity_ = quantity_
        new.pricebt_asset = pricebt_asset
        new._kwargs = new_kwargs
        new.resolved_terms = self.resolved_terms
        new.resolution_key = self.resolution_key
        new.resolution_csa = self.resolution_csa
        new.unresolved = self.unresolved
        new.position_meta = self.position_meta
        new._matched_asset = self._matched_asset
        return new

    def scale(self, scaling: Optional[float], in_place: bool = True, check_resolved: bool = True):
        if scaling is None:
            return self
        if self.resolved_terms is None and check_resolved:
            raise RuntimeError("Can only scale resolved instruments")
        # pricebt DEV-I1: scale multiplies quantity_ only; gs scaling edits size fields in place
        # (notional_amount, pay_or_receive, fee) and never touches kwargs here (DESIGN.md section 5.4).
        if in_place:
            self.quantity_ *= scaling
            return None
        new = copy.deepcopy(self)
        new.quantity_ *= scaling
        return new

    def flip(self, in_place: bool = True):
        return self.scale(-1, in_place)

    # --- resolution state (DESIGN.md section 5.1 item 4; the ONLY way to mark resolved) --------
    def _set_resolution(self, resolved_terms: Optional[Dict[str, Any]], resolution_key, resolution_csa, unresolved) -> None:
        self.resolved_terms = dict(resolved_terms) if resolved_terms is not None else None
        self.resolution_key = resolution_key
        self.resolution_csa = resolution_csa
        self.unresolved = unresolved

    # --- pricing (DESIGN.md section 6.5); imports the seams only inside the method body --------
    def resolve(self, in_place: bool = True):
        from pricebt.markets import _engine_resolve

        return _engine_resolve(self, in_place)

    def calc(self, risk_measure_or_iterable, fn=None):
        from pricebt.markets import _engine_calc

        return _engine_calc(self, risk_measure_or_iterable, fn)

    def price(self, currency: Optional[str] = None):
        return self.calc(Price(currency=currency) if currency else Price)

    def dollar_price(self):
        return self.calc(DollarPrice)

    # --- dict views (DESIGN.md section 5.1) -----------------------------------------------------
    def as_dict(self, as_camel_case: bool = False) -> Dict[str, Any]:
        d: Dict[str, Any] = {}
        if self.asset_class is not None:
            d["asset_class"] = self.asset_class
        if self.type_ is not None:
            d["type"] = self.type_
        if self.name is not None:
            d["name"] = self.name
        source = self.resolved_terms if self.resolved_terms is not None else self._kwargs
        for k, v in source.items():
            if v is not None:
                d[k] = v
        # pricebt DEV-I2: gs's own dict view has no quantity_ column; pricebt adds it here so
        # Portfolio.to_frame()/strategy_as_time_series static data shows position size (also
        # marked in markets/portfolio.py where to_frame consumes this dict).
        d["quantity_"] = self.quantity_
        if as_camel_case:
            d = {_to_camel(k): v for k, v in d.items()}
        return d

    def to_dict(self) -> Dict[str, Any]:
        d = self.as_dict()
        d.pop("name", None)
        return d


def _to_camel(name: str) -> str:
    first, *rest = name.split("_")
    return first + "".join(w.capitalize() for w in rest if w)


def instrument_identity(inst: Instrument):
    """DESIGN.md section 5.1 item 6: `(class, pricebt_asset or _matched_asset, frozen
    resolved_terms if resolved else frozen _kwargs, quantity_)`."""
    # pricebt DEV-I3: gs compares instruments by `to_dict()` set-membership, which is a dict and
    # therefore unhashable (a latent gs bug -- research/04 section 3). This hashable tuple is used
    # instead, matching the same name-excluding intent.
    asset = inst.pricebt_asset or inst._matched_asset
    terms = _freeze(inst.resolved_terms) if inst.resolved_terms is not None else _freeze(inst._kwargs)
    return (type(inst), asset, terms, inst.quantity_)


# ------------------------------------------------------------------------------------ generated classes

_ENUM_MAP: Dict[type, Dict[str, type]] = {}


def _build_class(cls_name: str, spec: Dict[str, Any]) -> type:
    fields = spec["fields"]
    enum_map = {f: _ENUM_CLASSES[tag] for f, _default, tag in fields if tag}
    params = ", ".join(f"{f}={default}" for f, default, _tag in fields)
    dict_items = ", ".join(f"{f!r}: {f}" for f, _default, _tag in fields if f != "name")
    src = f"def __init__(self, {params}, *, pricebt_asset=None, quantity_=1.0, **kwargs):\n" f"    _bind_instrument(self, {{{dict_items}}}, enum_map, name, pricebt_asset, quantity_, kwargs)\n"
    exec_globals: Dict[str, Any] = {"_bind_instrument": _bind_instrument, "enum_map": enum_map}
    exec_locals: Dict[str, Any] = {}
    exec(src, exec_globals, exec_locals)  # noqa: S102 -- generated source, no user input
    new_cls = type(cls_name, (Instrument,), {"__init__": exec_locals["__init__"], "asset_class": AssetClass(spec["asset_class"]), "type_": AssetType(spec["type_"])})
    _ENUM_MAP[new_cls] = enum_map
    return new_cls


for _cls_name, _spec in GS_FIELDS.items():
    globals()[_cls_name] = _build_class(_cls_name, _spec)
del _cls_name, _spec


class ConfigInstrument(Instrument):
    """The instrument class for assets gs has no class for (DESIGN.md section 5.1). Its
    `asset_class` and `type_` are `None`."""

    asset_class = None
    type_ = None

    def __init__(self, pricebt_asset: str, *, name: Optional[str] = None, quantity_: float = 1.0, **kwargs: Any) -> None:
        _bind_instrument(self, {}, {}, name, pricebt_asset, quantity_, kwargs)


__all__ = [
    "Bond",
    "BuySell",
    "Cash",
    "ConfigInstrument",
    "Currency",
    "EqOption",
    "FXForward",
    "FXOption",
    "InflationSwap",
    "IRSwap",
    "IRSwaption",
    "Instrument",
    "OptionStyle",
    "OptionType",
    "PayReceive",
    "SwapClearingHouse",
    "SwapSettlement",
    "instrument_identity",
]
