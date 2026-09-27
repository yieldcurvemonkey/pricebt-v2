"""Risk-measure classes and instances (DESIGN.md section 8.1), plus re-exports of FloatWithInfo,
SeriesWithInfo, DataFrameWithInfo, ErrorValue from risk.results.

Ported from gs_quant.common / gs_quant.target.measures / gs_quant.risk.measures (Apache-2.0; see
NOTICE): the RiskMeasure identity, call and repr semantics, and every measure instance the in-scope
2.1.17 backtests modules import. `base_name` is a pricebt addition (not in gs): `__call__(name=...)`
records the pre-rename name there, so a renamed preset (e.g. IRDeltaParallel) still falls back to
its parent's config mapping (DESIGN.md section 8.1, rule 3).
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields as _dc_fields, replace as _dc_replace
from typing import Any, Optional

import pandas as pd

from pricebt.common import AggregationLevel, AssetClass, RiskMeasureUnit
from pricebt.risk.results import DataFrameWithInfo, ErrorValue, FloatWithInfo, SeriesWithInfo  # noqa: F401

__all__ = [
    "FloatWithInfo",
    "SeriesWithInfo",
    "DataFrameWithInfo",
    "ErrorValue",
    "RiskMeasure",
    "ParameterisedRiskMeasure",
    "RiskMeasureWithCurrencyParameter",
    "RiskMeasureWithFiniteDifferenceParameter",
    "CurrencyParameter",
    "FiniteDifferenceParameter",
    "Price",
    "DollarPrice",
    "EqDelta",
    "EqGamma",
    "EqSpot",
    "EqVega",
    "Annuity",
    "Cashflows",
    "ResolvedInstrumentValues",
    "IRDelta",
    "IRDeltaParallel",
    "IRDeltaLocalCcy",
    "IRGamma",
    "IRGammaParallel",
    "IRVega",
    "IRVegaParallel",
    "IRVegaLocalCcy",
    "IRBasis",
    "IRXccyDelta",
    "InflationDelta",
    "IRFwdRate",
    "IRSpotRate",
    "IRDailyImpliedVol",
    "IRAnnualImpliedVol",
    "FXDelta",
    "FXGamma",
    "FXVega",
    "FXSpot",
    "FXAnnualImpliedVol",
    "FXDeltaLocalCcy",
    "FXGammaLocalCcy",
    "FXVegaLocalCcy",
]


# --------------------------------------------------------------------------------- parameter objects
@dataclass(frozen=True)
class CurrencyParameter:
    """The parameter object of a `RiskMeasureWithCurrencyParameter` call. Its one field is named
    `value` (not `currency`), which is why `repr(Price(currency='USD')) == 'Price(value:USD)'`."""

    value: Optional[str] = None
    parameter_type: str = "Currency"


@dataclass(frozen=True)
class FiniteDifferenceParameter:
    """The parameter object of a `RiskMeasureWithFiniteDifferenceParameter` call."""

    aggregation_level: Optional[AggregationLevel] = None
    currency: Optional[str] = None
    local_curve: Optional[bool] = None
    bump_size: Optional[float] = None
    finite_difference_method: Optional[str] = None
    scale_factor: Optional[float] = None
    mkt_marking_options: Optional[Any] = None
    parameter_type: str = "FiniteDifference"


def _params_repr(parameters) -> Optional[str]:
    """`k:v, ...` for every non-`parameter_type` field set on `parameters`, keys sorted
    case-insensitively; `None` if `parameters` is `None` or every field is unset."""
    if parameters is None:
        return None
    parts = {}
    for f in _dc_fields(parameters):
        if f.name == "parameter_type":
            continue
        v = getattr(parameters, f.name)
        if v is None:
            continue
        parts[f.name] = v.value if hasattr(v, "value") else v
    if not parts:
        return None
    keys = sorted(parts, key=str.lower)
    return ", ".join(f"{k}:{parts[k]}" for k in keys)


# --------------------------------------------------------------------------------- RiskMeasure identity
@dataclass(frozen=True)
class RiskMeasure:
    """A gs risk measure: `(asset_class, measure_type, unit, parameters, value, name)`, all six
    fields compared and hashed (`Price != Price(currency='USD')`). `base_name` does not take part
    in equality, hashing or `repr` (DESIGN.md section 8.1)."""

    asset_class: Optional[AssetClass] = None
    measure_type: Optional[str] = None
    unit: Optional[RiskMeasureUnit] = None
    parameters: Optional[Any] = None
    value: Optional[Any] = None
    name: Optional[str] = None
    base_name: Optional[str] = field(default=None, compare=False, repr=False)

    def __repr__(self) -> str:
        return self.name or (self.measure_type or "")

    def __lt__(self, other: "RiskMeasure") -> bool:
        if self.name != other.name:
            return (self.name or "") < (other.name or "")
        if self.parameters is not None:
            if other.parameters is None:
                return False
            return (_params_repr(self.parameters) or "") < (_params_repr(other.parameters) or "")
        return other.parameters is not None


class ParameterisedRiskMeasure(RiskMeasure):
    """A `RiskMeasure` subclass that is callable: calling it returns a clone with `parameters`
    merged (unset arguments inherit the existing value) and, if `name` is given, `base_name` set to
    the pre-rename name."""

    def _cloned(self, parameters, name: Optional[str]) -> "ParameterisedRiskMeasure":
        clone = _dc_replace(self, parameters=parameters, name=name or self.name)
        if name:
            object.__setattr__(clone, "base_name", self.name)
        return clone


class RiskMeasureWithCurrencyParameter(ParameterisedRiskMeasure):
    @property
    def currency(self) -> Optional[str]:
        return self.parameters.value if self.parameters else None

    def __call__(self, currency=None, name: Optional[str] = None) -> "RiskMeasureWithCurrencyParameter":
        if isinstance(currency, (pd.Series, pd.DataFrame)):
            return self
        if currency is None and self.parameters is not None:
            currency = self.parameters.value
        return self._cloned(CurrencyParameter(value=currency), name)


class RiskMeasureWithFiniteDifferenceParameter(ParameterisedRiskMeasure):
    @property
    def aggregation_level(self):
        return self.parameters.aggregation_level if self.parameters else None

    @property
    def currency(self) -> Optional[str]:
        return self.parameters.currency if self.parameters else None

    def __call__(
        self,
        aggregation_level=None,
        bump_size=None,
        currency=None,
        finite_difference_method=None,
        local_curve=None,
        mkt_marking_options=None,
        scale_factor=None,
        name: Optional[str] = None,
    ) -> "RiskMeasureWithFiniteDifferenceParameter":
        if isinstance(aggregation_level, (pd.Series, pd.DataFrame)):
            return self
        if isinstance(aggregation_level, str):
            aggregation_level = AggregationLevel(aggregation_level)
        p = self.parameters
        if aggregation_level is None and p is not None:
            aggregation_level = p.aggregation_level
        if bump_size is None and p is not None:
            bump_size = p.bump_size
        if currency is None and p is not None:
            currency = p.currency
        if finite_difference_method is None and p is not None:
            finite_difference_method = p.finite_difference_method
        if local_curve is None and p is not None:
            local_curve = p.local_curve
        if mkt_marking_options is None and p is not None:
            mkt_marking_options = p.mkt_marking_options
        if scale_factor is None and p is not None:
            scale_factor = p.scale_factor
        params = FiniteDifferenceParameter(
            aggregation_level=aggregation_level,
            bump_size=bump_size,
            currency=currency,
            finite_difference_method=finite_difference_method,
            local_curve=local_curve,
            mkt_marking_options=mkt_marking_options,
            scale_factor=scale_factor,
        )
        return self._cloned(params, name)


def _repr_parameterised(self) -> str:
    name = self.name or (self.measure_type or "")
    body = _params_repr(self.parameters)
    return f"{name}({body})" if body else name


RiskMeasureWithCurrencyParameter.__repr__ = _repr_parameterised
RiskMeasureWithFiniteDifferenceParameter.__repr__ = _repr_parameterised


# --------------------------------------------------------------------------------- measure instances
# Values verified against gs_quant 2.1.17 `gs_quant/target/measures.py` and `gs_quant/risk/measures.py`
# (read-only reference; DESIGN.md section 8.1). Two measures are classified here as gs actually
# defines them rather than as DESIGN.md section 8.1's prose lists them (a fact-vs-design conflict;
# see the P1.3 task report): `EqGamma` is `RiskMeasureWithCurrencyParameter`, and `FXGamma` is plain.

Price = RiskMeasureWithCurrencyParameter(name="Price", measure_type="PV")
EqDelta = RiskMeasureWithCurrencyParameter(name="EqDelta", asset_class=AssetClass.Equity, measure_type="Delta")
EqGamma = RiskMeasureWithCurrencyParameter(name="EqGamma", asset_class=AssetClass.Equity, measure_type="Gamma")
EqVega = RiskMeasureWithCurrencyParameter(name="EqVega", asset_class=AssetClass.Equity, measure_type="Vega")
Annuity = RiskMeasureWithCurrencyParameter(name="Annuity", asset_class=AssetClass.Rates, measure_type="AnnuityLocalCcy")
FXDeltaLocalCcy = RiskMeasureWithCurrencyParameter(name="FXDeltaLocalCcy", asset_class=AssetClass.FX, measure_type="FX Delta Local Ccy")
FXGammaLocalCcy = RiskMeasureWithCurrencyParameter(name="FXGammaLocalCcy", asset_class=AssetClass.FX, measure_type="FX Gamma Local Ccy")
FXVegaLocalCcy = RiskMeasureWithCurrencyParameter(name="FXVegaLocalCcy", asset_class=AssetClass.FX, measure_type="FX Vega Local Ccy")

IRDelta = RiskMeasureWithFiniteDifferenceParameter(name="IRDelta", asset_class=AssetClass.Rates, measure_type="Delta")
IRDeltaParallel = IRDelta(aggregation_level=AggregationLevel.Asset, name="IRDeltaParallel")
IRDeltaLocalCcy = IRDelta(currency="local", name="IRDeltaLocalCcy")
IRVega = RiskMeasureWithFiniteDifferenceParameter(name="IRVega", asset_class=AssetClass.Rates, measure_type="Vega")
IRVegaParallel = IRVega(aggregation_level=AggregationLevel.Asset, name="IRVegaParallel")
IRVegaLocalCcy = IRVega(currency="local", name="IRVegaLocalCcy")
IRBasis = RiskMeasureWithFiniteDifferenceParameter(name="IRBasis", asset_class=AssetClass.Rates, measure_type="Basis")
IRXccyDelta = RiskMeasureWithFiniteDifferenceParameter(name="IRXccyDelta", asset_class=AssetClass.Rates, measure_type="XccyDelta")
InflationDelta = RiskMeasureWithFiniteDifferenceParameter(name="InflationDelta", asset_class=AssetClass.Rates, measure_type="InflationDelta")
FXDelta = RiskMeasureWithFiniteDifferenceParameter(name="FXDelta", asset_class=AssetClass.FX, measure_type="Delta")
FXVega = RiskMeasureWithFiniteDifferenceParameter(name="FXVega", asset_class=AssetClass.FX, measure_type="Vega")

DollarPrice = RiskMeasure(name="DollarPrice", measure_type="Dollar Price")
IRGamma = RiskMeasure(name="IRGamma", asset_class=AssetClass.Rates, measure_type="Gamma")
IRGammaParallel = RiskMeasure(name="IRGammaParallel", asset_class=AssetClass.Rates, measure_type="ParallelGamma")
IRFwdRate = RiskMeasure(name="IRFwdRate", asset_class=AssetClass.Rates, measure_type="Forward Rate", unit=RiskMeasureUnit.Percent)
IRSpotRate = RiskMeasure(name="IRSpotRate", asset_class=AssetClass.Rates, measure_type="Spot Rate", unit=RiskMeasureUnit.Percent)
IRDailyImpliedVol = RiskMeasure(name="IRDailyImpliedVol", asset_class=AssetClass.Rates, measure_type="Daily Implied Volatility", unit=RiskMeasureUnit.BPS)
IRAnnualImpliedVol = RiskMeasure(name="IRAnnualImpliedVol", asset_class=AssetClass.Rates, measure_type="Annual Implied Volatility", unit=RiskMeasureUnit.Percent)
FXSpot = RiskMeasure(name="FXSpot", asset_class=AssetClass.FX, measure_type="Spot")
FXAnnualImpliedVol = RiskMeasure(name="FXAnnualImpliedVol", asset_class=AssetClass.FX, measure_type="Annual Implied Volatility", unit=RiskMeasureUnit.Percent)
FXGamma = RiskMeasure(name="FXGamma", asset_class=AssetClass.FX, measure_type="Gamma")
EqSpot = RiskMeasure(name="EqSpot", asset_class=AssetClass.Equity, measure_type="Spot")
Cashflows = RiskMeasure(name="Cashflows", measure_type="Cashflows")
ResolvedInstrumentValues = RiskMeasure(name="ResolvedInstrumentValues", measure_type="Resolved Instrument Values")
