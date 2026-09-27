"""gs_quant-compatible risk measures (`gs_quant.risk` names) mapped onto pricebt STANDARD MEASURE names.

A `RiskMeasure` is a `str` whose value is the pricebt measure name (`Price == 'pv'`, `IRDeltaParallel == 'dv01'`), so it can be passed
anywhere pricebt accepts a measure name (HedgeAction(risk=...), RiskTriggerRequirements(risk=...), EngineSettings.measures, cost models),
hashes and compares equal to that name, and works as a DataFrame column label (`backtest.result_summary[IRDelta(...)]`). It also carries the
gs attributes: `name` (the gs name), `params` (aggregation_level, currency, ...), `vector` (True for a bucketed ladder) and `measure` (plain str).

Parameterised measures are callable like gs: `IRDelta(aggregation_level=AggregationLevel.Type, currency='local')` returns a new RiskMeasure.

| gs_quant                                                   | pricebt measure | kind   | notes                                                    |
|------------------------------------------------------------|-----------------|--------|----------------------------------------------------------|
| Price, DollarPrice                                         | pv              | scalar | the `Price` column of result_summary                     |
| IRDelta(aggregation_level=Type/Asset/Class), IRDeltaParallel | dv01          | scalar | dollar delta: ccy per +1bp (schema S7)                   |
| IRDelta, IRDelta(), IRDelta(aggregation_level=Point), IRDeltaLocalCcy | delta_ladder | vector | dict[str, float] by tenor (schema S4)            |
| IRGamma(...), IRGammaParallel                              | gamma           | scalar | always parallel (no bucketed gamma)                      |
| IRVega(...), IRVegaParallel, IRVegaLocalCcy                | vega            | scalar | names only: no shipped instrument has a vol dimension   |
| Theta                                                      | theta           | scalar | names only                                               |
| FXDelta / FXGamma / FXVega                                 | fx_delta / fx_gamma / fx_vega | scalar | names only: no shipped FX instrument; bind them on your pricable |
| EqDelta / EqGamma / EqVega                                 | eq_delta / eq_gamma / eq_vega | scalar | names only: no shipped equity instrument          |
"""
from __future__ import annotations

import functools
from enum import Enum
from typing import Any, Callable, Dict, FrozenSet, Mapping, Optional, Tuple

from .common import AggregationLevel, check_currency
from .contracts.evaluate import is_vector
from .contracts.schema import SchemaRegistry
from .errors import NotSupportedError

__all__ = [
    "RiskMeasure", "as_risk_measure", "RISK_MEASURE_MAP", "Price", "DollarPrice", "IRDelta", "IRDeltaParallel", "IRDeltaLocalCcy", "IRGamma",
    "IRGammaParallel", "IRVega", "IRVegaParallel", "IRVegaLocalCcy", "Theta", "FXDelta", "FXGamma", "FXVega", "EqDelta", "EqGamma", "EqVega",
]

_SCALAR_LEVELS = frozenset({AggregationLevel.Type, AggregationLevel.Asset, AggregationLevel.Class})
_FAMILIES: Dict[str, Callable[..., "RiskMeasure"]] = {}
_COMMON_IGNORED = ("name", "value", "unit", "asset_class", "measure_type")  # gs RiskMeasure metadata fields: accepted, no effect on the number


class RiskMeasure(str):
    """A pricebt measure name with gs_quant attributes. Immutable; `copy`/`deepcopy` return the same object."""

    name: str
    vector: bool
    supported: bool

    def __new__(cls, measure: str, *, name: Optional[str] = None, vector: bool = False, params: Optional[Mapping[str, Any]] = None,
                family: Optional[str] = None, supported: bool = True, doc: str = "") -> "RiskMeasure":
        obj = super().__new__(cls, measure)
        obj.name = name or family or measure
        obj.vector = bool(vector)
        obj.supported = bool(supported)
        obj._params = dict(params or {})
        obj._family = family
        obj.__doc__ = doc or None
        return obj

    # ---- gs attributes
    @property
    def measure(self) -> str:
        """The pricebt standard measure name as a plain str."""
        return str.__str__(self)

    @property
    def params(self) -> Dict[str, Any]:
        return dict(self._params)

    @property
    def aggregation_level(self) -> Any:
        return self._params.get("aggregation_level")

    @property
    def currency(self) -> Any:
        return self._params.get("currency")

    @property
    def is_price(self) -> bool:
        return self.measure == "pv"

    @property
    def is_parameterised(self) -> bool:
        return self._family is not None

    def __call__(self, *args: Any, **params: Any) -> Any:
        """gs ParameterisedRiskMeasure: `IRDelta(aggregation_level=AggregationLevel.Type, currency='local')` returns a new measure.

        pandas calls a callable key with the frame (`df[Price]`, `df.loc[:, IRDelta(...)]`): given one pandas object, return the column label
        this measure is stored under ('Price' for a price measure in a result_summary, else the measure name)."""
        if args:
            obj = args[0]
            if len(args) == 1 and not params and _is_pandas(obj):
                return self.label_in(obj)
            raise TypeError(f"{self.name}() takes keyword parameters only")
        if self._family is None:
            raise TypeError(f"{self.name} is not a parameterised risk measure")
        return _FAMILIES[self._family](**params)

    def label_in(self, obj: Any) -> str:
        """The label of this measure in a pandas object: 'Price' for a price measure when the object has a 'Price' column/entry but no 'pv'."""
        labels = getattr(obj, "columns", None)
        if labels is None:
            labels = obj.index
        if self.is_price and "Price" in labels and self.measure not in labels:
            return "Price"
        return self.measure

    # ---- value semantics
    def __repr__(self) -> str:
        shown = {k: (v.value if isinstance(v, Enum) else v) for k, v in self._params.items() if v is not None}
        inner = ", ".join(f"{k}={v}" for k, v in shown.items())
        return f"{self.name}({inner})" if inner else self.name

    def __copy__(self) -> "RiskMeasure":
        return self

    def __deepcopy__(self, memo: Any) -> "RiskMeasure":
        return self

    def __reduce__(self) -> Tuple[Any, ...]:
        return (_rebuild, (self.measure, self.name, self.vector, dict(self._params), self._family, self.supported))


def _is_pandas(obj: Any) -> bool:
    import pandas as pd

    return isinstance(obj, (pd.DataFrame, pd.Series, pd.Index))


def _rebuild(measure: str, name: str, vector: bool, params: Dict[str, Any], family: Optional[str], supported: bool) -> RiskMeasure:
    return RiskMeasure(measure, name=name, vector=vector, params=params, family=family, supported=supported)


def _check_params(family: str, params: Mapping[str, Any], allowed: Tuple[str, ...]) -> None:
    bad = sorted(k for k, v in params.items() if v is not None and k not in allowed and k not in _COMMON_IGNORED)
    if bad:
        raise NotSupportedError(f"{family}: parameters {bad} are not supported by pricebt (supported: {sorted(allowed)}); a pricebt measure is one number "
                                f"(or one ladder) per position in the instrument's currency")


def _level(x: Any) -> Optional[AggregationLevel]:
    return None if x is None else AggregationLevel.coerce(x)


def _family(name: str) -> Callable[[Callable[..., RiskMeasure]], Callable[..., RiskMeasure]]:
    def deco(fn: Callable[..., RiskMeasure]) -> Callable[..., RiskMeasure]:
        _FAMILIES[name] = fn
        return fn

    return deco


@_family("IRDelta")
def _ir_delta(aggregation_level: Any = None, currency: Any = None, name: Optional[str] = None, **kw: Any) -> RiskMeasure:
    _check_params("IRDelta", kw, ())
    lvl = _level(aggregation_level)
    if currency is not None:
        check_currency(currency, "IRDelta", allow_local=True)
    params = {"aggregation_level": lvl, "currency": currency}
    if lvl in _SCALAR_LEVELS:
        return RiskMeasure("dv01", name=name or "IRDelta", params=params, family="IRDelta", doc="parallel IR delta: ccy per +1bp")
    return RiskMeasure("delta_ladder", name=name or "IRDelta", vector=True, params=params, family="IRDelta", doc="bucketed IR delta ladder")


def _scalar_family(family: str, measure: str, *, supported: bool = True, allow_local: bool = True, doc: str = "") -> Callable[..., RiskMeasure]:
    @_family(family)
    def make(aggregation_level: Any = None, currency: Any = None, name: Optional[str] = None, **kw: Any) -> RiskMeasure:
        _check_params(family, kw, ())
        if currency is not None:
            check_currency(currency, family, allow_local=allow_local)
        return RiskMeasure(measure, name=name or family, params={"aggregation_level": _level(aggregation_level), "currency": currency},
                           family=family, supported=supported, doc=doc)

    return make


@_family("Price")
def _price(currency: Any = None, name: Optional[str] = None, **kw: Any) -> RiskMeasure:
    _check_params("Price", kw, ())
    if currency is not None:
        check_currency(currency, "Price")
    return RiskMeasure("pv", name=name or "Price", params={"currency": currency}, family="Price", doc="mark-to-market of the held positions")


@_family("DollarPrice")
def _dollar_price(currency: Any = None, name: Optional[str] = None, **kw: Any) -> RiskMeasure:
    _check_params("DollarPrice", kw, ())
    return RiskMeasure("pv", name=name or "DollarPrice", params={}, family="DollarPrice", doc="mark-to-market in USD (the only currency)")


_scalar_family("IRGamma", "gamma", doc="parallel IR gamma: d2PV/dbp^2")
_scalar_family("IRVega", "vega", doc="Black vega per +1 vol point")
_scalar_family("Theta", "theta", doc="ccy per calendar day")
_scalar_family("FXDelta", "fx_delta", supported=False)
_scalar_family("FXGamma", "fx_gamma", supported=False)
_scalar_family("FXVega", "fx_vega", supported=False)
_scalar_family("EqDelta", "eq_delta", supported=False)
_scalar_family("EqGamma", "eq_gamma", supported=False)
_scalar_family("EqVega", "eq_vega", supported=False)

Price = _FAMILIES["Price"]()
DollarPrice = _FAMILIES["DollarPrice"]()
IRDelta = _FAMILIES["IRDelta"]()
IRDeltaParallel = _FAMILIES["IRDelta"](aggregation_level=AggregationLevel.Asset, name="IRDeltaParallel")
IRDeltaLocalCcy = _FAMILIES["IRDelta"](currency="local", name="IRDeltaLocalCcy")
IRGamma = _FAMILIES["IRGamma"]()
IRGammaParallel = _FAMILIES["IRGamma"](aggregation_level=AggregationLevel.Asset, name="IRGammaParallel")
IRVega = _FAMILIES["IRVega"]()
IRVegaParallel = _FAMILIES["IRVega"](aggregation_level=AggregationLevel.Asset, name="IRVegaParallel")
IRVegaLocalCcy = _FAMILIES["IRVega"](currency="local", name="IRVegaLocalCcy")
Theta = _FAMILIES["Theta"]()
FXDelta = _FAMILIES["FXDelta"]()
FXGamma = _FAMILIES["FXGamma"]()
FXVega = _FAMILIES["FXVega"]()
EqDelta = _FAMILIES["EqDelta"]()
EqGamma = _FAMILIES["EqGamma"]()
EqVega = _FAMILIES["EqVega"]()

_BY_NAME: Dict[str, RiskMeasure] = {
    m.name: m for m in (Price, DollarPrice, IRDelta, IRDeltaParallel, IRDeltaLocalCcy, IRGamma, IRGammaParallel, IRVega, IRVegaParallel, IRVegaLocalCcy,
                        Theta, FXDelta, FXGamma, FXVega, EqDelta, EqGamma, EqVega)
}

#: gs_quant name -> pricebt standard measure name (the documented mapping; vector measures are the bucketed ladders)
RISK_MEASURE_MAP: Mapping[str, str] = {k: v.measure for k, v in _BY_NAME.items()}


@functools.lru_cache(maxsize=None)
def _vector_names() -> FrozenSet[str]:
    """Standard measure names whose schema return type is a dict (a bucketed vector): the schemas decide, never the spelling of the name."""
    reg = SchemaRegistry.default()
    return frozenset(n for ac in reg.names() for n, m in reg.get(ac).measures.items() if is_vector(m))


def as_risk_measure(x: Any) -> RiskMeasure:
    """RiskMeasure | gs name ('IRDeltaParallel') | pricebt measure name ('dv01') -> RiskMeasure. A standard measure whose schema returns a dict is a vector;
    any other unknown name becomes a plain scalar measure (an instrument may bind any measure name)."""
    if isinstance(x, RiskMeasure):
        return x
    if isinstance(x, str):
        if x in _BY_NAME:
            return _BY_NAME[x]
        return RiskMeasure(x, vector=x in _vector_names())
    raise TypeError(f"expected a RiskMeasure or a measure name, got {type(x).__name__}: {x!r}")
