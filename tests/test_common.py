"""Tests for pricebt.base and pricebt.common (IMPLEMENTATION_PLAN.md P1.1).

Expected members/values are transcribed from the installed gs_quant 1.5.4
(C:\\Users\\chris\\anaconda3\\Lib\\site-packages\\gs_quant\\target\\common.py /
gs_quant\\common.py), cross-checked against research/04-gs-instrument-risk-session.md section 4.
"""
from __future__ import annotations

import pytest

from pricebt import base, common
from pricebt.common import (
    AccrualConvention,
    AggregationLevel,
    AssetClass,
    AssetType,
    BusinessDayConvention,
    BuySell,
    Currency,
    CurrencyName,
    DayCountFraction,
    FiniteDifferenceMethod,
    OptionExerciseStyle,
    OptionSettlementMethod,
    OptionStyle,
    OptionType,
    PayReceive,
    PositionType,
    PrincipalExchange,
    RiskMeasureUnit,
    SwapClearingHouse,
    SwapSettlement,
    TradeAs,
    UnderlierType,
)

pytestmark = pytest.mark.core

# name -> value, for every EnumBase-derived enum pricebt.common defines (Currency and PositionType
# are handled separately below: Currency has 341 members in gs, so pricebt keeps a subset;
# PositionType is not EnumBase-derived in gs).
ENUM_MEMBERS = {
    PayReceive: {"Pay": "Pay", "Receive": "Rec", "Straddle": "Straddle"},
    BuySell: {"Buy": "Buy", "Sell": "Sell"},
    AggregationLevel: {"Type": "Type", "Asset": "Asset", "Class": "Class", "Point": "Point"},
    OptionType: {
        "Call": "Call",
        "Put": "Put",
        "Forward": "Forward",
        "Binary_Call": "Binary Call",
        "Binary_Put": "Binary Put",
        "Digital_Call": "Digital Call",
        "Digital_Put": "Digital Put",
    },
    OptionStyle: {"European": "European", "American": "American", "Bermudan": "Bermudan", "Asian": "Asian"},
    DayCountFraction: {
        "ACT_OVER_360": "ACT/360",
        "ACT_OVER_360_ISDA": "ACT/360 ISDA",
        "ACT_OVER_365_Fixed": "ACT/365 (Fixed)",
        "ACT_OVER_365_Fixed_ISDA": "ACT/365 Fixed ISDA",
        "ACT_OVER_365L_ISDA": "ACT/365L ISDA",
        "ACT_OVER_ACT_ISDA": "ACT/ACT ISDA",
        "ACT_OVER_ACT_ISMA": "ACT/ACT ISMA",
        "_30_OVER_360": "30/360",
        "_30E_OVER_360": "30E/360",
    },
    BusinessDayConvention: {
        "Following": "Following",
        "Modified_Following": "Modified Following",
        "Previous": "Previous",
        "Unadjusted": "Unadjusted",
    },
    SwapClearingHouse: {"LCH": "LCH", "EUREX": "EUREX", "JSCC": "JSCC", "CME": "CME", "NONE": "NONE"},
    PrincipalExchange: {"_None": "None", "Both": "Both", "First": "First", "Last": "Last"},
    SwapSettlement: {
        "Phys_CLEARED": "Phys.CLEARED",
        "Physical": "Physical",
        "Cash_CollatCash": "Cash.CollatCash",
        "Cash_PYU": "Cash.PYU",
    },
    AccrualConvention: {"Adjusted": "Adjusted", "Unadjusted": "Unadjusted"},
    UnderlierType: {
        "BBID": "BBID",
        "BID": "BID",
        "CUSIP": "CUSIP",
        "ISIN": "ISIN",
        "SEDOL": "SEDOL",
        "RIC": "RIC",
        "Ticker": "Ticker",
    },
    TradeAs: {
        "Listed": "Listed",
        "Listed_Look_alike_OTC": "Listed Look alike OTC",
        "Flex": "Flex",
        "OTC": "OTC",
    },
    OptionSettlementMethod: {
        "Cash": "Cash",
        "Physical": "Physical",
        "ElectDfltCash": "ElectDfltCash",
        "ElectDfltPhys": "ElectDfltPhys",
        "NetShares": "NetShares",
    },
    OptionExerciseStyle: {"Auto": "Auto", "Manual": "Manual"},
    RiskMeasureUnit: {"Percent": "Percent", "Dollar": "Dollar", "BPS": "BPS", "Pips": "Pips"},
    FiniteDifferenceMethod: {"Up": "Up", "Centered": "Centered", "Down": "Down", "CenteredSecondOrder": "CenteredSecondOrder"},
    AssetClass: {
        "Cash": "Cash",
        "Commod": "Commod",
        "Credit": "Credit",
        "Cross_Asset": "Cross Asset",
        "Digital_Asset": "Digital Asset",
        "Debt": "Debt",
        "Econ": "Econ",
        "Equity": "Equity",
        "Fund": "Fund",
        "FX": "FX",
        "ListedDerivative": "ListedDerivative",
        "Mortgage": "Mortgage",
        "Rates": "Rates",
        "Repo": "Repo",
        "Loan": "Loan",
        "Social": "Social",
        "Cryptocurrency": "Cryptocurrency",
    },
    AssetType: {
        "Swap": "Swap",
        "Swaption": "Swaption",
        "Option": "Option",
        "Forward": "Forward",
        "Cash": "Cash",
        "InflationSwap": "InflationSwap",
        "Cap": "Cap",
        "Floor": "Floor",
        "FRA": "FRA",
        "XccySwap": "XccySwap",
        "Future": "Future",
        "Bond": "Bond",
    },
    CurrencyName: {
        "United_States_Dollar": "United States Dollar",
        "Australian_Dollar": "Australian Dollar",
        "Canadian_Dollar": "Canadian Dollar",
        "Swiss_Franc": "Swiss Franc",
        "Yuan_Renminbi_Hong_Kong": "Yuan Renminbi (Hong Kong)",
        "Czech_Republic_Koruna": "Czech Republic Koruna",
        "Euro": "Euro",
        "Pound_Sterling": "Pound Sterling",
        "Japanese_Yen": "Japanese Yen",
        "South_Korean_Won": "South Korean Won",
        "Malasyan_Ringgit": "Malasyan Ringgit",
        "Norwegian_Krone": "Norwegian Krone",
        "New_Zealand_Dollar": "New Zealand Dollar",
        "Polish_Zloty": "Polish Zloty",
        "Russian_Rouble": "Russian Rouble",
        "Swedish_Krona": "Swedish Krona",
        "South_African_Rand": "South African Rand",
        "Yuan_Renminbi_Onshore": "Yuan Renminbi (Onshore)",
    },
}

CURRENCY_CODES = [
    "USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD", "SEK", "NOK", "DKK", "CNY", "CNH",
    "HKD", "SGD", "KRW", "INR", "MXN", "BRL", "ZAR", "PLN", "CZK", "HUF", "TRY", "ILS",
]


@pytest.mark.parametrize("enum_cls,expected", list(ENUM_MEMBERS.items()), ids=[c.__name__ for c in ENUM_MEMBERS])
def test_enum_members_and_values(enum_cls, expected):
    assert {m.name: m.value for m in enum_cls} == expected
    for member in enum_cls:
        assert str(member) == member.value  # EnumBase.__str__
        assert enum_cls(member.value) is member  # round-trips through the constructor


@pytest.mark.parametrize("enum_cls", list(ENUM_MEMBERS) + [Currency])
def test_invalid_value_raises(enum_cls):
    with pytest.raises(ValueError):
        enum_cls("not-a-real-value")


def test_enum_base_generic_case_insensitive_missing():
    # No enum-specific _missing_ override involved: this is EnumBase's own case-insensitive match.
    assert AggregationLevel("type") is AggregationLevel.Type
    assert OptionStyle("EUROPEAN") is OptionStyle.European


def test_enum_base_lt_compares_by_value():
    assert PayReceive.Pay < PayReceive.Receive  # 'Pay' < 'Rec'
    assert AggregationLevel.Asset < AggregationLevel.Class  # 'Asset' < 'Class'
    # discriminates value-order from name-order: by value '30/360' < 'ACT/360' (True); by member
    # name '_30_OVER_360' > 'ACT_OVER_360' ('_' sorts after 'A') -- would be False if __lt__
    # compared names instead of values.
    assert DayCountFraction._30_OVER_360 < DayCountFraction.ACT_OVER_360


def test_enum_base_repr_is_value():
    assert repr(PayReceive.Receive) == "Rec"


def test_enum_base_supports_non_str_enum_values():
    # DESIGN.md line 497 uses EnumBase on an int-valued enum (`Environment`, built in P2.3):
    # `class Environment(EnumBase, Enum): DEV = 1; QA = 2; PROD = 3`. EnumBase must not assume
    # every member's .value is a str.
    from enum import Enum as _Enum

    class _IntEnum(base.EnumBase, _Enum):
        A = 1
        B = 2

    assert str(_IntEnum.A) == "1"
    assert repr(_IntEnum.A) == "1"
    assert _IntEnum(1) is _IntEnum.A
    assert _IntEnum("1") is _IntEnum.A  # case-insensitive _missing_, coerced through str()
    with pytest.raises(ValueError):
        _IntEnum("not-a-real-value")


def test_payreceive_aliases():
    assert PayReceive("receiver") is PayReceive.Receive
    assert PayReceive("receive") is PayReceive.Receive
    assert PayReceive("RECEIVER") is PayReceive.Receive


def test_payreceive_str_and_equality():
    assert str(PayReceive.Receive) == "Rec"
    assert PayReceive.Pay == "Pay"


def test_currency_members():
    assert {m.name for m in Currency} == {"_"} | set(CURRENCY_CODES)
    for code in CURRENCY_CODES:
        member = Currency(code)
        assert member.name == code
        assert member.value == code
    assert Currency("").name == "_"
    assert Currency("").value == ""


def test_currency_case_insensitive_lookup():
    assert Currency("usd") is Currency.USD
    assert Currency("Usd") is Currency.USD


def test_positiontype_is_plain_enum_not_enumbase():
    # gs does not derive PositionType from EnumBase (research/04 section 4): no case-insensitive
    # construction, and it is not a str.
    assert not isinstance(PositionType.OPEN, base.EnumBase)
    assert PositionType("open") is PositionType.OPEN
    with pytest.raises(ValueError):
        PositionType("OPEN")  # plain Enum: exact value match only, not case-insensitive


def test_field_value_map_is_a_dict_alias():
    assert common.FieldValueMap is dict
    assert common.FieldValueMap(a=1) == {"a": 1}


def test_module_getattr_raises_for_unknown_name():
    with pytest.raises(AttributeError):
        common.no_such_attribute


# --- base.py ---

def test_priceable_is_a_plain_marker():
    assert isinstance(base.Priceable(), base.Priceable)


def test_static_field_is_init_false_with_default():
    f = base.static_field("swap")
    assert f.init is False
    assert f.default == "swap"


def test_field_metadata_is_none():
    assert base.field_metadata is None


def test_exclude_none():
    assert base.exclude_none(None) is True
    assert base.exclude_none(0) is False
    assert base.exclude_none("") is False


def test_get_enum_value_none_passthrough():
    assert base.get_enum_value(PayReceive, None) is None


def test_get_enum_value_valid_member_passthrough():
    assert base.get_enum_value(PayReceive, PayReceive.Pay) is PayReceive.Pay


def test_get_enum_value_valid_string_coerces():
    assert base.get_enum_value(PayReceive, "Pay") is PayReceive.Pay


def test_get_enum_value_invalid_is_lenient(caplog):
    with caplog.at_level("WARNING"):
        result = base.get_enum_value(PayReceive, "not-a-real-value")
    assert result == "not-a-real-value"  # returned as-is, no raise
    assert any("not-a-real-value" in r.message for r in caplog.records)


def test_get_enum_value_strict_direct_construction_raises():
    # get_enum_value is lenient; direct construction is not (R04 section 4).
    with pytest.raises(ValueError):
        PayReceive("not-a-real-value")
