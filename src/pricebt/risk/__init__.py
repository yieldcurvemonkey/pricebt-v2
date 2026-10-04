"""Risk-measure classes and instances (DESIGN.md section 8.1), plus re-exports of the value classes
from risk.results and of the risk.core helpers (aggregate_risk, aggregate_results, subtract_risk,
sort_risk, combine_risk_key), as gs's `gs_quant.risk` re-exports its `core`.

Ported from gs_quant.common / gs_quant.target.measures / gs_quant.risk.measures (Apache-2.0; see
NOTICE): the RiskMeasure identity, call and repr semantics, and the whole 2.1.17 measure catalogue
(every instance and preset gs_quant.risk exposes; IR_RISK_DESIGN.md section 1). The measures are
data: pricebt computes none of them itself, an asset config maps each one to a function.
`base_name` is a pricebt addition (not in gs): `__call__(name=...)` records the pre-rename name
there, so a renamed preset (e.g. IRDeltaParallel) still falls back to its parent's config mapping
(DESIGN.md section 8.1, rule 3).
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields as _dc_fields, replace as _dc_replace
from typing import Any, Optional

import pandas as pd

from pricebt.common import AggregationLevel, AssetClass, FiniteDifferenceMethod, RiskMeasureUnit
from pricebt.errors import NotSupportedError
from pricebt.risk.results import (  # noqa: F401
    DataFrameWithInfo,
    DictWithInfo,
    ErrorValue,
    FloatWithInfo,
    SeriesWithInfo,
    StringWithInfo,
    UnsupportedValue,
)
from pricebt.risk.core import aggregate_results, aggregate_risk, combine_risk_key, sort_risk, subtract_risk  # noqa: F401,E402 -- after results (core imports it)

__all__ = [
    "FloatWithInfo",
    "SeriesWithInfo",
    "DataFrameWithInfo",
    "ErrorValue",
    "StringWithInfo",
    "DictWithInfo",
    "UnsupportedValue",
    "aggregate_risk",
    "aggregate_results",
    "subtract_risk",
    "sort_risk",
    "combine_risk_key",
    "RiskMeasure",
    "ParameterisedRiskMeasure",
    "RiskMeasureWithCurrencyParameter",
    "RiskMeasureWithFiniteDifferenceParameter",
    "CurrencyParameter",
    "FiniteDifferenceParameter",
    "MarketParameter",
    "PnlExplain",
    "PnlExplainClose",
    "PnlExplainLive",
    "PnlPredictLive",
    "Annuity",
    "BaseCPI",
    "CDATMSpread",
    "CDDelta",
    "CDFwdSpread",
    "CDGamma",
    "CDIForward",
    "CDIIndexDelta",
    "CDIIndexVega",
    "CDIOptionPremium",
    "CDIOptionPremiumFlatFwd",
    "CDIOptionPremiumFlatVol",
    "CDISpot",
    "CDISpreadDV01",
    "CDIUpfrontPrice",
    "CDImpliedVolatility",
    "CDIndexVega",
    "CDTheta",
    "CDVega",
    "CRIFIRCurve",
    "Cashflows",
    "CommodDelta",
    "CommodImpliedVol",
    "CommodTheta",
    "CommodVega",
    "CompoundedFixedRate",
    "Cross",
    "CrossMultiplier",
    "Description",
    "DollarPrice",
    "EqAnnualImpliedVol",
    "EqDelta",
    "EqForwardSpot",
    "EqGamma",
    "EqSpot",
    "EqTheta",
    "EqVega",
    "ExpiryInYears",
    "FX25DeltaButterflyVolatility",
    "FX25DeltaRiskReversalVolatility",
    "FXAnnualATMImpliedVol",
    "FXAnnualImpliedVol",
    "FXBlackScholes",
    "FXBlackScholesPct",
    "FXCalcDelta",
    "FXCalcDeltaNoPremAdj",
    "FXDelta",
    "FXDeltaHedge",
    "FXDeltaHedgeLocalCcy",
    "FXDeltaLocalCcy",
    "FXDiscountFactorOver",
    "FXDiscountFactorUnder",
    "FXFwd",
    "FXGamma",
    "FXGammaLocalCcy",
    "FXImpliedCorrelation",
    "FXPoints",
    "FXPremium",
    "FXPremiumPct",
    "FXPremiumPctFlatFwd",
    "FXQuotedDelta",
    "FXQuotedDeltaNoPremAdj",
    "FXQuotedVega",
    "FXQuotedVegaBps",
    "FXSpot",
    "FXSpotVal",
    "FXStrikePts",
    "FXThetaLocalCcy",
    "FXVega",
    "FXVegaLocalCcy",
    "FairPremium",
    "FairPremiumInPercent",
    "FairPrice",
    "FairVarStrike",
    "FairVolStrike",
    "ForwardPrice",
    "IRAnnualATMImpliedVol",
    "IRAnnualImpliedVol",
    "IRBasis",
    "IRBasisParallel",
    "IRDailyImpliedVol",
    "IRDelta",
    "IRDeltaLocalCcy",
    "IRDeltaParallel",
    "IRDiscountDeltaParallel",
    "IRDiscountDeltaParallelLocalCcy",
    "IRFwdRate",
    "IRGamma",
    "IRGammaParallel",
    "IRGammaParallelLocalCcy",
    "IRSpotRate",
    "IRVanna",
    "IRVega",
    "IRVegaLocalCcy",
    "IRVegaParallel",
    "IRVolga",
    "IRXccyDelta",
    "IRXccyDeltaParallel",
    "InflDeltaParallelLocalCcyInBps",
    "InflMaturityCPI",
    "Infl_CompPeriod",
    "InflationDelta",
    "InflationDeltaParallel",
    "LightningDV01",
    "LightningOAS",
    "LocalAnnuityInCents",
    "Market",
    "MarketData",
    "MarketDataAssets",
    "NonUSDOisDomRate",
    "OisFXSprExSpkRate",
    "OisFXSprRate",
    "ParSpread",
    "PremiumCents",
    "PremiumSummary",
    "Price",
    "PricePips",
    "ProbabilityOfExercise",
    "RFRFXRate",
    "RFRFXSprExSpkRate",
    "RFRFXSprRate",
    "ResolvedInstrumentValues",
    "Theta",
    "USDOisDomRate",
    # pricebt DEV-I20/DEV-I21 measures (PRICEBT_MEASURES; not gs)
    "PRICEBT_MEASURES",
    "AccruedInterest",
    "Carry",
    "CleanPrice",
    "Convexity",
    "DaysToSettlement",
    "DirtyPrice",
    "FinancingToDate",
    "ModifiedDuration",
    "RepoHaircut",
    "RepoRate",
    "RollDown",
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
    finite_difference_method: Optional[FiniteDifferenceMethod] = None
    scale_factor: Optional[float] = None
    mkt_marking_options: Optional[Any] = None
    parameter_type: str = "FiniteDifference"


@dataclass(frozen=True)
class MarketParameter:
    """pricebt DEV-M2: the target market of a relative measure (`PnlExplain`), as the `date` and
    `location` given to its `CloseMarket` (`date=None` = the pricing date's own close, resolved when
    priced). gs keeps the target outside the measure's fields, so two targets compare equal there;
    here it is part of equality, hashing, ordering and every cache key."""

    date: Optional[Any] = None
    location: Optional[Any] = None
    parameter_type: str = "Market"


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
        if isinstance(finite_difference_method, str):
            # gs coerces through its Base field typing: case-insensitive, invalid -> ValueError
            finite_difference_method = FiniteDifferenceMethod(finite_difference_method)
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


# --------------------------------------------------------------------------------- relative measures
# gs_quant/risk/measures.py's PnlExplain family: the change in value from the pricing context's
# market to `to_market`, by risk factor, with no time component. An asset config maps `PnlExplain`
# to a portfolio function that also receives `market_to` and `pricebt_to_date` (IR_RISK_DESIGN.md
# section 8). pricebt DEV-M2: the result is always the bucketed frame (gs turns one of at most two
# rows of a single mkt_type into a float).
class PnlExplain(RiskMeasure):
    """Pnl Explained"""

    # pricebt DEV-M2: the target is in the repr, e.g. 'PnlExplain(date:2024-01-09, location:LDN)',
    # so two targets get two to_frame labels (gs: 'PnlExplain' for every target)
    __repr__ = _repr_parameterised

    def __init__(self, to_market):
        from pricebt.markets import CloseMarket  # IR_RISK_DESIGN R2-22: markets sits after risk

        if not isinstance(to_market, CloseMarket):
            raise NotSupportedError(f"PnlExplain(to_market={to_market!r}): pricebt explains only to a CloseMarket(date=...), each asset config's own market for that date")
        # pricebt DEV-M2: the target is the measure's parameters (gs: a private attribute)
        target = MarketParameter(None if to_market._date is None else to_market.date, to_market.location)
        super().__init__(measure_type="PnlExplain", name="PnlExplain", parameters=target)


class PnlExplainClose(PnlExplain):
    def __init__(self):
        from pricebt.markets import CloseMarket

        super().__init__(CloseMarket())


class PnlExplainLive(PnlExplain):
    def __init__(self):
        raise NotSupportedError("PnlExplainLive explains to the live market, which is GS server-side; use PnlExplain(CloseMarket(date=...))")


class PnlPredictLive(RiskMeasure):
    """Pnl Predicted"""

    def __init__(self):
        raise NotSupportedError("PnlPredictLive predicts against the live market, which is GS server-side; use PnlExplain(CloseMarket(date=...))")


# --------------------------------------------------------------------------------- measure instances
# Values verified against gs_quant 2.1.17 `gs_quant/target/measures.py` and `gs_quant/risk/measures.py`
# (read-only reference; DESIGN.md section 8.1, IR_RISK_DESIGN.md section 1), in gs's own (sorted)
# order. Two measures are classified here as gs actually defines them rather than as DESIGN.md
# section 8.1's prose lists them (a fact-vs-design conflict; see the P1.3 task report): `EqGamma` is
# `RiskMeasureWithCurrencyParameter`, and `FXGamma` is plain. gs's `__doc__` strings are not ported.

Annuity = RiskMeasureWithCurrencyParameter(name="Annuity", asset_class=AssetClass.Rates, measure_type="AnnuityLocalCcy")
BaseCPI = RiskMeasure(name="BaseCPI", measure_type="BaseCPI")
CDATMSpread = RiskMeasure(name="CDATMSpread", asset_class=AssetClass.Credit, measure_type="ATM Spread")
CDDelta = RiskMeasure(name="CDDelta", asset_class=AssetClass.Credit, measure_type="Delta")
CDFwdSpread = RiskMeasure(name="CDFwdSpread", asset_class=AssetClass.Credit, measure_type="Forward Spread")
CDGamma = RiskMeasure(name="CDGamma", asset_class=AssetClass.Credit, measure_type="Gamma")
CDIForward = RiskMeasure(name="CDIForward", asset_class=AssetClass.Credit, measure_type="CDIForward")
CDIIndexDelta = RiskMeasure(name="CDIIndexDelta", asset_class=AssetClass.Credit, measure_type="CDIIndexDelta")
CDIIndexVega = RiskMeasure(name="CDIIndexVega", asset_class=AssetClass.Credit, measure_type="CDIIndexVega")
CDIOptionPremium = RiskMeasure(name="CDIOptionPremium", asset_class=AssetClass.Credit, measure_type="CDIOptionPremium")
CDIOptionPremiumFlatFwd = RiskMeasure(name="CDIOptionPremiumFlatFwd", asset_class=AssetClass.Credit, measure_type="CDIOptionPremiumFlatFwd")
CDIOptionPremiumFlatVol = RiskMeasure(name="CDIOptionPremiumFlatVol", asset_class=AssetClass.Credit, measure_type="CDIOptionPremiumFlatVol")
CDISpot = RiskMeasure(name="CDISpot", asset_class=AssetClass.Credit, measure_type="CDISpot")
CDISpreadDV01 = RiskMeasure(name="CDISpreadDV01", asset_class=AssetClass.Credit, measure_type="CDISpreadDV01")
CDIUpfrontPrice = RiskMeasure(name="CDIUpfrontPrice", asset_class=AssetClass.Credit, measure_type="CDIUpfrontPrice")
CDImpliedVolatility = RiskMeasure(name="CDImpliedVolatility", asset_class=AssetClass.Credit, measure_type="Implied Volatility")
CDIndexVega = RiskMeasure(name="CDIndexVega", asset_class=AssetClass.Credit, measure_type="Vega")
CDTheta = RiskMeasure(name="CDTheta", asset_class=AssetClass.Credit, measure_type="Theta")
CDVega = RiskMeasure(name="CDVega", asset_class=AssetClass.Credit, measure_type="Vega")
CRIFIRCurve = RiskMeasure(name="CRIFIRCurve", measure_type="CRIF IRCurve")
Cashflows = RiskMeasure(name="Cashflows", measure_type="Cashflows")
CommodDelta = RiskMeasure(name="CommodDelta", asset_class=AssetClass.Commod, measure_type="Delta")
CommodImpliedVol = RiskMeasure(name="CommodImpliedVol", asset_class=AssetClass.Commod, measure_type="Volatility")
CommodTheta = RiskMeasure(name="CommodTheta", asset_class=AssetClass.Commod, measure_type="Theta")
CommodVega = RiskMeasure(name="CommodVega", asset_class=AssetClass.Commod, measure_type="Vega")
CompoundedFixedRate = RiskMeasure(name="CompoundedFixedRate", measure_type="Compounded Fixed Rate")
Cross = RiskMeasure(name="Cross", asset_class=AssetClass.FX, measure_type="Cross")
CrossMultiplier = RiskMeasure(name="CrossMultiplier", measure_type="Cross Multiplier")
Description = RiskMeasure(name="Description", measure_type="Description")
DollarPrice = RiskMeasure(name="DollarPrice", measure_type="Dollar Price")
EqAnnualImpliedVol = RiskMeasure(name="EqAnnualImpliedVol", asset_class=AssetClass.Equity, measure_type="Annual Implied Volatility", unit=RiskMeasureUnit.Percent)
EqDelta = RiskMeasureWithCurrencyParameter(name="EqDelta", asset_class=AssetClass.Equity, measure_type="Delta")
EqForwardSpot = RiskMeasure(name="EqForwardSpot", asset_class=AssetClass.Equity, measure_type="Forward Price")
EqGamma = RiskMeasureWithCurrencyParameter(name="EqGamma", asset_class=AssetClass.Equity, measure_type="Gamma")
EqSpot = RiskMeasure(name="EqSpot", asset_class=AssetClass.Equity, measure_type="Spot")
EqTheta = RiskMeasureWithCurrencyParameter(name="EqTheta", asset_class=AssetClass.Equity, measure_type="Theta")
EqVega = RiskMeasureWithCurrencyParameter(name="EqVega", asset_class=AssetClass.Equity, measure_type="Vega")
ExpiryInYears = RiskMeasure(name="ExpiryInYears", measure_type="ExpiryInYears")
FX25DeltaButterflyVolatility = RiskMeasure(name="FX25DeltaButterflyVolatility", asset_class=AssetClass.FX, measure_type="FX BF 25 Vol")
FX25DeltaRiskReversalVolatility = RiskMeasure(name="FX25DeltaRiskReversalVolatility", asset_class=AssetClass.FX, measure_type="FX RR 25 Vol")
FXAnnualATMImpliedVol = RiskMeasure(name="FXAnnualATMImpliedVol", asset_class=AssetClass.FX, measure_type="Annual ATM Implied Volatility", unit=RiskMeasureUnit.Percent)
FXAnnualImpliedVol = RiskMeasure(name="FXAnnualImpliedVol", asset_class=AssetClass.FX, measure_type="Annual Implied Volatility", unit=RiskMeasureUnit.Percent)
FXBlackScholes = RiskMeasure(name="FXBlackScholes", asset_class=AssetClass.FX, measure_type="BSPrice")
FXBlackScholesPct = RiskMeasure(name="FXBlackScholesPct", asset_class=AssetClass.FX, measure_type="BSPricePct")
FXCalcDelta = RiskMeasure(name="FXCalcDelta", asset_class=AssetClass.FX, measure_type="FX Calculated Delta")
FXCalcDeltaNoPremAdj = RiskMeasure(name="FXCalcDeltaNoPremAdj", asset_class=AssetClass.FX, measure_type="FX Calculated Delta No Premium Adjustment")
FXDelta = RiskMeasureWithFiniteDifferenceParameter(name="FXDelta", asset_class=AssetClass.FX, measure_type="Delta")
FXDeltaHedge = RiskMeasure(name="FXDeltaHedge", asset_class=AssetClass.FX, measure_type="FX Hedge Delta")
FXDeltaHedgeLocalCcy = RiskMeasureWithCurrencyParameter(name="FXDeltaHedgeLocalCcy", asset_class=AssetClass.FX, measure_type="FX Hedge Delta Local Ccy")
FXDeltaLocalCcy = RiskMeasureWithCurrencyParameter(name="FXDeltaLocalCcy", asset_class=AssetClass.FX, measure_type="FX Delta Local Ccy")
FXDiscountFactorOver = RiskMeasure(name="FXDiscountFactorOver", asset_class=AssetClass.FX, measure_type="FX Discount Factor Over")
FXDiscountFactorUnder = RiskMeasure(name="FXDiscountFactorUnder", asset_class=AssetClass.FX, measure_type="FX Discount Factor Under")
FXFwd = RiskMeasure(name="FXFwd", asset_class=AssetClass.FX, measure_type="Forward Rate")
FXGamma = RiskMeasure(name="FXGamma", asset_class=AssetClass.FX, measure_type="Gamma")
FXGammaLocalCcy = RiskMeasureWithCurrencyParameter(name="FXGammaLocalCcy", asset_class=AssetClass.FX, measure_type="FX Gamma Local Ccy")
FXImpliedCorrelation = RiskMeasure(name="FXImpliedCorrelation", asset_class=AssetClass.FX, measure_type="Correlation")
FXPoints = RiskMeasure(name="FXPoints", asset_class=AssetClass.FX, measure_type="Points")
FXPremium = RiskMeasure(name="FXPremium", asset_class=AssetClass.FX, measure_type="FX Premium")
FXPremiumPct = RiskMeasure(name="FXPremiumPct", asset_class=AssetClass.FX, measure_type="FX Premium Pct")
FXPremiumPctFlatFwd = RiskMeasure(name="FXPremiumPctFlatFwd", asset_class=AssetClass.FX, measure_type="FX Premium Pct Flat Fwd")
FXQuotedDelta = RiskMeasure(name="FXQuotedDelta", asset_class=AssetClass.FX, measure_type="QuotedDelta")
FXQuotedDeltaNoPremAdj = RiskMeasure(name="FXQuotedDeltaNoPremAdj", asset_class=AssetClass.FX, measure_type="FX Quoted Delta No Premium Adjustment")
FXQuotedVega = RiskMeasure(name="FXQuotedVega", asset_class=AssetClass.FX, measure_type="FX Quoted Vega")
FXQuotedVegaBps = RiskMeasure(name="FXQuotedVegaBps", asset_class=AssetClass.FX, measure_type="FX Quoted Vega Bps")
FXSpot = RiskMeasure(name="FXSpot", asset_class=AssetClass.FX, measure_type="Spot")
FXSpotVal = RiskMeasure(name="FXSpotVal", asset_class=AssetClass.FX, measure_type="FXSpotVal")
FXStrikePts = RiskMeasure(name="FXStrikePts", asset_class=AssetClass.FX, measure_type="StrikePts")
FXThetaLocalCcy = RiskMeasureWithCurrencyParameter(name="FXThetaLocalCcy", asset_class=AssetClass.FX, measure_type="FX Theta Local Ccy")
FXVega = RiskMeasureWithFiniteDifferenceParameter(name="FXVega", asset_class=AssetClass.FX, measure_type="Vega")
FXVegaLocalCcy = RiskMeasureWithCurrencyParameter(name="FXVegaLocalCcy", asset_class=AssetClass.FX, measure_type="FX Vega Local Ccy")
FairPremium = RiskMeasureWithCurrencyParameter(name="FairPremium", measure_type="FairPremium")
FairPremiumInPercent = RiskMeasure(name="FairPremiumInPercent", asset_class=AssetClass.FX, measure_type="FairPremiumPct", unit=RiskMeasureUnit.Percent)
FairPrice = RiskMeasure(name="FairPrice", asset_class=AssetClass.Commod, measure_type="Fair Price")
FairVarStrike = RiskMeasure(name="FairVarStrike", measure_type="FairVarStrike")
FairVolStrike = RiskMeasure(name="FairVolStrike", measure_type="FairVolStrike")
ForwardPrice = RiskMeasure(name="ForwardPrice", measure_type="Forward Price", unit=RiskMeasureUnit.BPS)
IRAnnualATMImpliedVol = RiskMeasure(name="IRAnnualATMImpliedVol", asset_class=AssetClass.Rates, measure_type="Annual ATMF Implied Volatility", unit=RiskMeasureUnit.Percent)
IRAnnualImpliedVol = RiskMeasure(name="IRAnnualImpliedVol", asset_class=AssetClass.Rates, measure_type="Annual Implied Volatility", unit=RiskMeasureUnit.Percent)
IRBasis = RiskMeasureWithFiniteDifferenceParameter(name="IRBasis", asset_class=AssetClass.Rates, measure_type="Basis")
IRDailyImpliedVol = RiskMeasure(name="IRDailyImpliedVol", asset_class=AssetClass.Rates, measure_type="Daily Implied Volatility", unit=RiskMeasureUnit.BPS)
IRDelta = RiskMeasureWithFiniteDifferenceParameter(name="IRDelta", asset_class=AssetClass.Rates, measure_type="Delta")
IRDiscountDeltaParallel = RiskMeasure(name="IRDiscountDeltaParallel", asset_class=AssetClass.Rates, measure_type="ParallelDiscountDelta")
IRDiscountDeltaParallelLocalCcy = RiskMeasure(name="IRDiscountDeltaParallelLocalCcy", asset_class=AssetClass.Rates, measure_type="ParallelDiscountDeltaLocalCcy")
IRFwdRate = RiskMeasure(name="IRFwdRate", asset_class=AssetClass.Rates, measure_type="Forward Rate", unit=RiskMeasureUnit.Percent)
IRGamma = RiskMeasure(name="IRGamma", asset_class=AssetClass.Rates, measure_type="Gamma")
IRGammaParallel = RiskMeasure(name="IRGammaParallel", asset_class=AssetClass.Rates, measure_type="ParallelGamma")
IRGammaParallelLocalCcy = RiskMeasure(name="IRGammaParallelLocalCcy", asset_class=AssetClass.Rates, measure_type="ParallelGammaLocalCcy")
IRSpotRate = RiskMeasure(name="IRSpotRate", asset_class=AssetClass.Rates, measure_type="Spot Rate", unit=RiskMeasureUnit.Percent)
# pricebt DEV-I9: IRVanna/IRVolga are finite-difference measures as in 2.1.17 (plain RiskMeasure,
# not callable, in 1.5.4), so gs's own vanna/volga notebook's IRVanna(aggregation_level=Type) works.
IRVanna = RiskMeasureWithFiniteDifferenceParameter(name="IRVanna", asset_class=AssetClass.Rates, measure_type="Vanna")
IRVega = RiskMeasureWithFiniteDifferenceParameter(name="IRVega", asset_class=AssetClass.Rates, measure_type="Vega")
IRVolga = RiskMeasureWithFiniteDifferenceParameter(name="IRVolga", asset_class=AssetClass.Rates, measure_type="Volga")
IRXccyDelta = RiskMeasureWithFiniteDifferenceParameter(name="IRXccyDelta", asset_class=AssetClass.Rates, measure_type="XccyDelta")
InflDeltaParallelLocalCcyInBps = RiskMeasure(name="InflDeltaParallelLocalCcyInBps", asset_class=AssetClass.Rates, measure_type="Inflation Delta in Bps")
InflMaturityCPI = RiskMeasure(name="InflMaturityCPI", asset_class=AssetClass.Rates, measure_type="FinalCPI")
Infl_CompPeriod = RiskMeasure(name="Infl_CompPeriod", asset_class=AssetClass.Rates, measure_type="Inflation Compounding Period")
InflationDelta = RiskMeasureWithFiniteDifferenceParameter(name="InflationDelta", asset_class=AssetClass.Rates, measure_type="InflationDelta")
LightningDV01 = RiskMeasure(name="LightningDV01", measure_type="DV01")
LightningOAS = RiskMeasure(name="LightningOAS", measure_type="OAS")
LocalAnnuityInCents = RiskMeasure(name="LocalAnnuityInCents", asset_class=AssetClass.Rates, measure_type="Local Currency Accrual in Cents")
Market = RiskMeasure(name="Market", measure_type="Market")
MarketData = RiskMeasure(name="MarketData", measure_type="Market Data")
MarketDataAssets = RiskMeasure(name="MarketDataAssets", measure_type="Market Data Assets")
NonUSDOisDomRate = RiskMeasure(name="NonUSDOisDomRate", asset_class=AssetClass.FX, measure_type="NonUSDOisDomesticRate")
OisFXSprExSpkRate = RiskMeasure(name="OisFXSprExSpkRate", asset_class=AssetClass.FX, measure_type="OisFXSpreadRateExcludingSpikes")
OisFXSprRate = RiskMeasure(name="OisFXSprRate", asset_class=AssetClass.FX, measure_type="OisFXSpreadRate")
ParSpread = RiskMeasure(name="ParSpread", asset_class=AssetClass.Rates, measure_type="Spread")
PremiumCents = RiskMeasure(name="PremiumCents", asset_class=AssetClass.Rates, measure_type="Premium In Cents")
PremiumSummary = RiskMeasure(name="PremiumSummary", asset_class=AssetClass.Commod, measure_type="Premium")
Price = RiskMeasureWithCurrencyParameter(name="Price", measure_type="PV")
PricePips = RiskMeasureWithCurrencyParameter(name="PricePips", measure_type="Price", unit=RiskMeasureUnit.Pips)
ProbabilityOfExercise = RiskMeasure(name="ProbabilityOfExercise", measure_type="Probability Of Exercise")
RFRFXRate = RiskMeasure(name="RFRFXRate", asset_class=AssetClass.FX, measure_type="RFRFXRate")
RFRFXSprExSpkRate = RiskMeasure(name="RFRFXSprExSpkRate", asset_class=AssetClass.FX, measure_type="RFRFXSpreadRateExcludingSpikes")
RFRFXSprRate = RiskMeasure(name="RFRFXSprRate", asset_class=AssetClass.FX, measure_type="RFRFXSpreadRate")
ResolvedInstrumentValues = RiskMeasure(name="ResolvedInstrumentValues", measure_type="Resolved Instrument Values")
Theta = RiskMeasure(name="Theta", measure_type="Theta")
USDOisDomRate = RiskMeasure(name="USDOisDomRate", asset_class=AssetClass.FX, measure_type="USDOisDomesticRate")

# gs_quant/risk/measures.py's parameterised presets (the call records base_name = the parent's name).
IRBasisParallel = IRBasis(aggregation_level=AggregationLevel.Asset, name="IRBasisParallel")
InflationDeltaParallel = InflationDelta(aggregation_level=AggregationLevel.Type, name="InflationDeltaParallel")
IRDeltaParallel = IRDelta(aggregation_level=AggregationLevel.Asset, name="IRDeltaParallel")
IRDeltaLocalCcy = IRDelta(currency="local", name="IRDeltaLocalCcy")
IRXccyDeltaParallel = IRXccyDelta(aggregation_level=AggregationLevel.Type, name="IRXccyDeltaParallel")
IRVegaParallel = IRVega(aggregation_level=AggregationLevel.Asset, name="IRVegaParallel")
IRVegaLocalCcy = IRVega(currency="local", name="IRVegaLocalCcy")

# --------------------------------------------------------------------------------- pricebt measures
# pricebt DEV-I20 (bond analytics) and DEV-I21 (financing): gs has no clean price, accrued, duration,
# convexity, settlement, repo, financing or carry measure (docs/v2/BOND_DESIGN.md section 1). These
# are pricebt's own, data like the rest (an asset config maps each one); PRICEBT_MEASURES lists them
# and tests/test_gs_api_parity.py allows exactly these extras. Amounts take a currency parameter.
AccruedInterest = RiskMeasureWithCurrencyParameter(name="AccruedInterest", measure_type="Accrued Interest")
Carry = RiskMeasureWithCurrencyParameter(name="Carry", measure_type="Carry")
CleanPrice = RiskMeasure(name="CleanPrice", measure_type="Clean Price")
Convexity = RiskMeasure(name="Convexity", measure_type="Convexity")
DaysToSettlement = RiskMeasure(name="DaysToSettlement", measure_type="Days To Settlement")
DirtyPrice = RiskMeasure(name="DirtyPrice", measure_type="Dirty Price")
FinancingToDate = RiskMeasureWithCurrencyParameter(name="FinancingToDate", measure_type="Financing To Date")
ModifiedDuration = RiskMeasure(name="ModifiedDuration", measure_type="Modified Duration")
RepoHaircut = RiskMeasure(name="RepoHaircut", measure_type="Repo Haircut")
RepoRate = RiskMeasure(name="RepoRate", measure_type="Repo Rate")
RollDown = RiskMeasureWithCurrencyParameter(name="RollDown", measure_type="Roll Down")
PRICEBT_MEASURES = (
    "AccruedInterest", "Carry", "CleanPrice", "Convexity", "DaysToSettlement", "DirtyPrice",
    "FinancingToDate", "ModifiedDuration", "RepoHaircut", "RepoRate", "RollDown",
)

# pricebt DEV-I16: pricebt prices every measure in the instrument's own currency (DESIGN.md decision
# 0.5), so the two plain LocalCcy measures are their base measure; base_name lets a config mapping
# of the base measure serve them (IR_RISK_DESIGN.md section 1.3).
object.__setattr__(IRGammaParallelLocalCcy, "base_name", "IRGammaParallel")
object.__setattr__(IRDiscountDeltaParallelLocalCcy, "base_name", "IRDiscountDeltaParallel")
