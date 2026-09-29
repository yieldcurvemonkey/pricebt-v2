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


# ------------------------------------------------------------------------------- the ported catalogue (IR_RISK_DESIGN.md section 1)
# gs_quant/risk/measures.py's presets, transcribed from gs 2.1.17: name -> (parent, parameters). The
# snapshot records no parameters, so this table is the presets' only parity check (R2-23).
PRESETS = {
    "IRBasisParallel": ("IRBasis", {"aggregation_level": common.AggregationLevel.Asset}),
    "InflationDeltaParallel": ("InflationDelta", {"aggregation_level": common.AggregationLevel.Type}),
    "IRDeltaParallel": ("IRDelta", {"aggregation_level": common.AggregationLevel.Asset}),
    "IRDeltaLocalCcy": ("IRDelta", {"currency": "local"}),
    "IRXccyDeltaParallel": ("IRXccyDelta", {"aggregation_level": common.AggregationLevel.Type}),
    "IRVegaParallel": ("IRVega", {"aggregation_level": common.AggregationLevel.Asset}),
    "IRVegaLocalCcy": ("IRVega", {"currency": "local"}),
}
# pricebt DEV-I16: plain LocalCcy measures that fall back to their base measure's mapping.
LOCAL_CCY_FALLBACKS = {"IRGammaParallelLocalCcy": "IRGammaParallel", "IRDiscountDeltaParallelLocalCcy": "IRDiscountDeltaParallel"}


@pytest.mark.parametrize("name", sorted(PRESETS))
def test_preset_parameters_and_base_name(name):
    parent_name, params = PRESETS[name]
    m, parent = getattr(risk, name), getattr(risk, parent_name)
    assert m.name == name
    assert m.base_name == parent_name
    assert m.parameters == FiniteDifferenceParameter(**params)
    assert type(m) is type(parent) is RiskMeasureWithFiniteDifferenceParameter
    assert (m.asset_class, m.measure_type, m.unit, m.value) == (parent.asset_class, parent.measure_type, parent.unit, parent.value)


@pytest.mark.parametrize("name, base", sorted(LOCAL_CCY_FALLBACKS.items()))
def test_local_ccy_fallback_base_name(name, base):
    m = getattr(risk, name)
    assert m.base_name == base
    assert type(m) is RiskMeasure and m.parameters is None
    assert m != getattr(risk, base)  # base_name is not part of identity


def test_only_presets_and_local_ccy_fallbacks_carry_a_base_name():
    carrying = {n for n in risk.__all__ if isinstance(getattr(risk, n), RiskMeasure) and getattr(risk, n).base_name}
    assert carrying == set(PRESETS) | set(LOCAL_CCY_FALLBACKS)


# The 2.1.17-only measures are absent from the 1.5.4 snapshot, so the parity test only checks that
# they exist; their identity is pinned here (gs 2.1.17 gs_quant/target/measures.py).
TWO_1_17_ONLY = {
    "EqForwardSpot": (RiskMeasure, common.AssetClass.Equity, "Forward Price", None),
    "FXDeltaHedgeLocalCcy": (RiskMeasureWithCurrencyParameter, common.AssetClass.FX, "FX Hedge Delta Local Ccy", None),
    "FXDeltaLocalCcy": (RiskMeasureWithCurrencyParameter, common.AssetClass.FX, "FX Delta Local Ccy", None),
    "FXGammaLocalCcy": (RiskMeasureWithCurrencyParameter, common.AssetClass.FX, "FX Gamma Local Ccy", None),
    "FXThetaLocalCcy": (RiskMeasureWithCurrencyParameter, common.AssetClass.FX, "FX Theta Local Ccy", None),
    "FXVegaLocalCcy": (RiskMeasureWithCurrencyParameter, common.AssetClass.FX, "FX Vega Local Ccy", None),
}


@pytest.mark.parametrize("name", sorted(TWO_1_17_ONLY))
def test_2_1_17_only_measure_identity(name):
    cls, asset_class, measure_type, unit = TWO_1_17_ONLY[name]
    m = getattr(risk, name)
    assert (type(m), m.name, m.asset_class, m.measure_type, m.unit, m.parameters) == (cls, name, asset_class, measure_type, unit, None)


def test_vanna_volga_are_finite_difference_measures():
    # pricebt DEV-I9 (2.1.17 behaviour): gs's vanna/volga notebook calls IRVanna(aggregation_level=Type)
    for m in (risk.IRVanna, risk.IRVolga):
        typed = m(aggregation_level="Type")
        assert typed.aggregation_level is common.AggregationLevel.Type
        assert repr(typed) == f"{m.name}(aggregation_level:Type)"


# ------------------------------------------------------------------------------- finite_difference_method
@pytest.mark.parametrize("given", ["Centered", "centered", "CENTERED", common.FiniteDifferenceMethod.Centered])
def test_finite_difference_method_coerced_case_insensitively(given):
    m = IRDelta(finite_difference_method=given)
    assert m.parameters.finite_difference_method is common.FiniteDifferenceMethod.Centered
    assert m == IRDelta(finite_difference_method="Centered")
    assert repr(m) == "IRDelta(finite_difference_method:Centered)"


def test_finite_difference_method_inherited_on_recall():
    m = IRDelta(finite_difference_method="up")(bump_size=2)
    assert m.parameters.finite_difference_method is common.FiniteDifferenceMethod.Up
    assert m.parameters.bump_size == 2


def test_invalid_finite_difference_method_raises():
    with pytest.raises(ValueError, match="Sideways"):
        IRDelta(finite_difference_method="Sideways")
