"""Tests for pricebt.risk's RiskMeasure family (IMPLEMENTATION_PLAN.md P1.3, DESIGN.md section 8.1).

Expected values are transcribed from the installed gs_quant 2.1.17
(C:\\Users\\chris\\clee\\gsquant-temp-claude\\gs-quant\\gs_quant\\target\\measures.py /
gs_quant\\risk\\measures.py), the read-only reference cited by the task. Two measures are classified
here as gs actually defines them, not as DESIGN.md section 8.1's prose happens to list them: `EqGamma`
is `RiskMeasureWithCurrencyParameter` (gs: `EqGamma = RiskMeasureWithCurrencyParameter(...)`, not
plain), and `FXGamma` is plain (gs: `FXGamma = RiskMeasure(...)`, not finite-difference). This is a
DESIGN-vs-primary-source conflict; IMPLEMENTATION_PLAN.md section 9 says the primary source wins on a
fact about gs behaviour. See the task report for both quotes.
"""
from __future__ import annotations

import pandas as pd
import pytest

import pricebt.common as common
import pricebt.risk as risk
import pricebt.target.backtests as target_backtests
import pricebt.target.common as target_common
import pricebt.target.measures as target_measures
from pricebt.risk import (
    Annuity,
    Cashflows,
    CurrencyParameter,
    DollarPrice,
    EqDelta,
    EqGamma,
    EqSpot,
    EqVega,
    FiniteDifferenceParameter,
    FXAnnualImpliedVol,
    FXDelta,
    FXDeltaLocalCcy,
    FXGamma,
    FXGammaLocalCcy,
    FXSpot,
    FXVega,
    FXVegaLocalCcy,
    IRAnnualImpliedVol,
    IRBasis,
    IRDailyImpliedVol,
    IRDelta,
    IRDeltaLocalCcy,
    IRDeltaParallel,
    IRFwdRate,
    IRGamma,
    IRGammaParallel,
    IRSpotRate,
    IRVega,
    IRVegaLocalCcy,
    IRVegaParallel,
    IRXccyDelta,
    InflationDelta,
    ParameterisedRiskMeasure,
    Price,
    ResolvedInstrumentValues,
    RiskMeasure,
    RiskMeasureWithCurrencyParameter,
    RiskMeasureWithFiniteDifferenceParameter,
)

pytestmark = pytest.mark.core


# ------------------------------------------------------------------------------- identity
def test_identity_currency_parameter_changes_equality():
    assert Price != Price(currency="USD")
    assert Price(currency="USD") == Price(currency="USD")
    assert hash(Price(currency="USD")) == hash(Price(currency="USD"))


def test_identity_name_and_parameters_both_matter():
    # same parameters, different name -> not equal (DESIGN.md section 8.1)
    assert IRDelta(aggregation_level="Asset") != IRDeltaParallel
    assert IRDelta(aggregation_level="Asset", name="IRDeltaParallel") == IRDeltaParallel


def test_identity_plain_measures():
    assert DollarPrice == DollarPrice
    assert DollarPrice != Price
    assert IRGamma != IRGammaParallel


# ------------------------------------------------------------------------------- repr (DESIGN.md 8.1 examples)
@pytest.mark.parametrize(
    "measure, expected",
    [
        (Price(currency="USD"), "Price(value:USD)"),
        (IRDelta(aggregation_level="Type", currency="USD"), "IRDelta(aggregation_level:Type, currency:USD)"),
        (IRDeltaParallel, "IRDeltaParallel(aggregation_level:Asset)"),
        (IRDeltaLocalCcy, "IRDeltaLocalCcy(currency:local)"),
        (Price, "Price"),
        (DollarPrice, "DollarPrice"),
    ],
)
def test_repr_examples(measure, expected):
    assert repr(measure) == expected


def test_repr_sorts_keys_case_insensitively():
    m = IRDelta(currency="USD", aggregation_level="Type")
    # 'aggregation_level' < 'currency' regardless of call order
    assert repr(m) == "IRDelta(aggregation_level:Type, currency:USD)"


# ------------------------------------------------------------------------------- base_name (pricebt addition)
def test_base_name_set_on_rename_only():
    assert IRDeltaParallel.base_name == "IRDelta"
    assert IRDeltaLocalCcy.base_name == "IRDelta"
    assert IRVegaParallel.base_name == "IRVega"
    # a plain call (no rename) does not set base_name
    assert IRDelta(currency="USD").base_name is None
    # base_name survives a further call that does not rename again
    assert IRDeltaParallel(currency="USD").base_name == "IRDelta"
    assert IRDeltaParallel(currency="USD").name == "IRDeltaParallel"


def test_base_name_does_not_affect_equality_or_repr():
    a = IRDelta(aggregation_level="Asset", name="IRDeltaParallel")
    b = IRDelta(aggregation_level="Asset", name="IRDeltaParallel")
    object.__setattr__(b, "base_name", "something else")
    assert a == b
    assert repr(a) == repr(b)


# ------------------------------------------------------------------------------- callable semantics
def test_unset_arguments_inherit():
    m = IRDelta(currency="USD")(aggregation_level="Type")
    assert m.currency == "USD"
    assert m.aggregation_level == common.AggregationLevel.Type


def test_string_aggregation_level_coerced_to_enum():
    m = IRDelta(aggregation_level="Type")
    assert m.aggregation_level == common.AggregationLevel.Type
    assert m.parameters.aggregation_level is common.AggregationLevel.Type


def test_call_equality_independent_of_call_order():
    assert IRDelta(aggregation_level="Type")(currency="EUR") == IRDelta(aggregation_level="Type", currency="EUR")


def test_pandas_series_call_hack_returns_self():
    s = pd.Series([1, 2, 3])
    assert Price(s) is Price
    df = pd.DataFrame({"a": [1]})
    assert IRDelta(df) is IRDelta


def test_plain_measure_is_not_a_parameterised_measure():
    assert not isinstance(DollarPrice, ParameterisedRiskMeasure)


# ------------------------------------------------------------------------------- sorting
def test_lt_sorts_by_name_then_parameters():
    measures = [IRVega, IRDelta, DollarPrice]
    assert sorted(measures, key=repr) == sorted(measures)  # name-only sort agrees with repr sort here
    assert (DollarPrice < IRDelta) is (repr(DollarPrice) < repr(IRDelta))


# ------------------------------------------------------------------------------- class membership (DESIGN.md 8.1)
@pytest.mark.parametrize(
    "measure",
    [Price, EqDelta, EqVega, EqGamma, Annuity, FXDeltaLocalCcy, FXGammaLocalCcy, FXVegaLocalCcy],
)
def test_currency_parameter_class_membership(measure):
    assert isinstance(measure, RiskMeasureWithCurrencyParameter)
    assert isinstance(measure(currency="USD").parameters, CurrencyParameter)


@pytest.mark.parametrize(
    "measure",
    [IRDelta, IRVega, IRBasis, IRXccyDelta, InflationDelta, FXDelta, FXVega],
)
def test_finite_difference_parameter_class_membership(measure):
    assert isinstance(measure, RiskMeasureWithFiniteDifferenceParameter)
    assert isinstance(measure(aggregation_level="Type").parameters, FiniteDifferenceParameter)


@pytest.mark.parametrize(
    "measure",
    [
        DollarPrice,
        IRGamma,
        IRGammaParallel,
        IRFwdRate,
        IRSpotRate,
        IRDailyImpliedVol,
        IRAnnualImpliedVol,
        FXSpot,
        FXAnnualImpliedVol,
        FXGamma,
        EqSpot,
        Cashflows,
        ResolvedInstrumentValues,
    ],
)
def test_plain_measure_class_membership(measure):
    assert type(measure) is RiskMeasure
    assert not isinstance(measure, ParameterisedRiskMeasure)


def test_percent_and_bps_units():
    assert IRFwdRate.unit == common.RiskMeasureUnit.Percent
    assert IRSpotRate.unit == common.RiskMeasureUnit.Percent
    assert IRAnnualImpliedVol.unit == common.RiskMeasureUnit.Percent
    assert FXAnnualImpliedVol.unit == common.RiskMeasureUnit.Percent
    assert IRDailyImpliedVol.unit == common.RiskMeasureUnit.BPS
    assert Price.unit is None


# ------------------------------------------------------------------------------- re-exports and target shims
def test_risk_reexports_result_classes():
    assert risk.FloatWithInfo is not None
    assert risk.SeriesWithInfo is not None
    assert risk.DataFrameWithInfo is not None
    assert risk.ErrorValue is not None


def test_common_lazily_reexports_riskmeasure_classes():
    assert common.RiskMeasure is RiskMeasure
    assert common.ParameterisedRiskMeasure is ParameterisedRiskMeasure


def test_target_measures_reexports_pricebt_risk():
    assert target_measures.Price is Price
    assert target_measures.IRDelta is IRDelta


def test_target_common_reexports_enums_and_riskmeasure():
    assert target_common.AggregationLevel is common.AggregationLevel
    assert target_common.RiskMeasure is RiskMeasure
    assert target_common.CurrencyParameter is CurrencyParameter
    assert target_common.FiniteDifferenceParameter is FiniteDifferenceParameter


def test_target_backtests_is_a_lazy_shim():
    # its two real names are resolved only on access (a function-level import), not at module load
    # time, per the task: pricebt.backtests.core is real code only from P3.1 onward.
    assert set(target_backtests.__all__) == {"BacktestTradingQuantityType", "DeltaHedgeParameters"}
    assert "__getattr__" in vars(target_backtests)
    with pytest.raises(AttributeError):
        target_backtests.NotARealName
