"""gs enums (IMPLEMENTATION_PLAN.md P1.1), ported from `gs_quant.common` / `gs_quant.target.common`
(Apache-2.0; see NOTICE). Members and values are copied verbatim from the installed gs_quant 1.5.4
(DESIGN.md decision 0.1); `Currency` and `AssetType` are deliberately partial subsets, per
IMPLEMENTATION_PLAN.md P1.1 and DESIGN.md section 2.1 decision 0.6.

`RiskMeasure`, `ParameterisedRiskMeasure` and `FieldValueMap` are re-exported through a module
`__getattr__` below (`RiskMeasure`/`ParameterisedRiskMeasure` lazily, to avoid an import cycle with
pricebt.risk, which does not exist as real code until P1.3 -- this module must be importable alone
before that).
"""
from __future__ import annotations

from enum import Enum

from pricebt.base import EnumBase


class PayReceive(EnumBase, str, Enum):
    """Pay or receive fixed."""

    Pay = "Pay"
    Receive = "Rec"
    Straddle = "Straddle"

    @classmethod
    def _missing_(cls, value):
        if isinstance(value, str) and value.lower() in ("receive", "receiver"):
            return cls.Receive
        return super()._missing_(value)


class BuySell(EnumBase, str, Enum):
    """Buy or sell side of contract."""

    Buy = "Buy"
    Sell = "Sell"


class AggregationLevel(EnumBase, str, Enum):
    """Aggregation level."""

    Type = "Type"
    Asset = "Asset"
    Class = "Class"
    Point = "Point"


class OptionType(EnumBase, str, Enum):
    """Option type."""

    Call = "Call"
    Put = "Put"
    Forward = "Forward"
    Binary_Call = "Binary Call"
    Binary_Put = "Binary Put"
    Digital_Call = "Digital Call"
    Digital_Put = "Digital Put"


class OptionStyle(EnumBase, str, Enum):
    """Option exercise style."""

    European = "European"
    American = "American"
    Bermudan = "Bermudan"
    Asian = "Asian"


class DayCountFraction(EnumBase, str, Enum):
    """Day count fraction."""

    ACT_OVER_360 = "ACT/360"
    ACT_OVER_360_ISDA = "ACT/360 ISDA"
    ACT_OVER_365_Fixed = "ACT/365 (Fixed)"
    ACT_OVER_365_Fixed_ISDA = "ACT/365 Fixed ISDA"
    ACT_OVER_365L_ISDA = "ACT/365L ISDA"
    ACT_OVER_ACT_ISDA = "ACT/ACT ISDA"
    ACT_OVER_ACT_ISMA = "ACT/ACT ISMA"
    _30_OVER_360 = "30/360"
    _30E_OVER_360 = "30E/360"


class BusinessDayConvention(EnumBase, str, Enum):
    """Business day convention."""

    Following = "Following"
    Modified_Following = "Modified Following"
    Previous = "Previous"
    Unadjusted = "Unadjusted"


class SwapClearingHouse(EnumBase, str, Enum):
    """Swap clearing house."""

    LCH = "LCH"
    EUREX = "EUREX"
    JSCC = "JSCC"
    CME = "CME"
    NONE = "NONE"


class PrincipalExchange(EnumBase, str, Enum):
    """How principal is exchanged."""

    _None = "None"
    Both = "Both"
    First = "First"
    Last = "Last"


class SwapSettlement(EnumBase, str, Enum):
    """Swap settlement type."""

    Phys_CLEARED = "Phys.CLEARED"
    Physical = "Physical"
    Cash_CollatCash = "Cash.CollatCash"
    Cash_PYU = "Cash.PYU"


class AccrualConvention(EnumBase, str, Enum):
    """Accrual convention."""

    Adjusted = "Adjusted"
    Unadjusted = "Unadjusted"


class UnderlierType(EnumBase, str, Enum):
    """Type of underlier."""

    BBID = "BBID"
    BID = "BID"
    CUSIP = "CUSIP"
    ISIN = "ISIN"
    SEDOL = "SEDOL"
    RIC = "RIC"
    Ticker = "Ticker"


class TradeAs(EnumBase, str, Enum):
    """Option trade as (listed, OTC, lookalike, ...)."""

    Listed = "Listed"
    Listed_Look_alike_OTC = "Listed Look alike OTC"
    Flex = "Flex"
    OTC = "OTC"


class OptionSettlementMethod(EnumBase, str, Enum):
    """How the option is settled."""

    Cash = "Cash"
    Physical = "Physical"
    ElectDfltCash = "ElectDfltCash"
    ElectDfltPhys = "ElectDfltPhys"
    NetShares = "NetShares"


class OptionExerciseStyle(EnumBase, str, Enum):
    """How the option is exercised."""

    Auto = "Auto"
    Manual = "Manual"


class RiskMeasureUnit(EnumBase, str, Enum):
    """Unit of change of the underlying in a risk computation."""

    Percent = "Percent"
    Dollar = "Dollar"
    BPS = "BPS"
    Pips = "Pips"


class FiniteDifferenceMethod(EnumBase, str, Enum):
    """Direction and dimension of finite difference."""

    Up = "Up"
    Centered = "Centered"
    Down = "Down"
    CenteredSecondOrder = "CenteredSecondOrder"


class AssetClass(EnumBase, str, Enum):
    """Asset classification of a security."""

    Cash = "Cash"
    Commod = "Commod"
    Credit = "Credit"
    Cross_Asset = "Cross Asset"
    Digital_Asset = "Digital Asset"
    Debt = "Debt"
    Econ = "Econ"
    Equity = "Equity"
    Fund = "Fund"
    FX = "FX"
    ListedDerivative = "ListedDerivative"
    Mortgage = "Mortgage"
    Rates = "Rates"
    Repo = "Repo"
    Loan = "Loan"
    Social = "Social"
    Cryptocurrency = "Cryptocurrency"


class AssetType(EnumBase, str, Enum):
    """Asset type: the R04 subset pricebt v2 needs right now (IMPLEMENTATION_PLAN.md P1.1;
    research/04 section 4), not gs's full 161-member enum. Extend by adding more (name, value)
    pairs as new asset configs need them."""

    Swap = "Swap"
    Swaption = "Swaption"
    Option = "Option"
    Forward = "Forward"
    Cash = "Cash"
    InflationSwap = "InflationSwap"
    Cap = "Cap"
    Floor = "Floor"
    FRA = "FRA"
    XccySwap = "XccySwap"
    Future = "Future"
    Bond = "Bond"


class CurrencyName(EnumBase, str, Enum):
    """Currency names."""

    United_States_Dollar = "United States Dollar"
    Australian_Dollar = "Australian Dollar"
    Canadian_Dollar = "Canadian Dollar"
    Swiss_Franc = "Swiss Franc"
    Yuan_Renminbi_Hong_Kong = "Yuan Renminbi (Hong Kong)"
    Czech_Republic_Koruna = "Czech Republic Koruna"
    Euro = "Euro"
    Pound_Sterling = "Pound Sterling"
    Japanese_Yen = "Japanese Yen"
    South_Korean_Won = "South Korean Won"
    Malasyan_Ringgit = "Malasyan Ringgit"
    Norwegian_Krone = "Norwegian Krone"
    New_Zealand_Dollar = "New Zealand Dollar"
    Polish_Zloty = "Polish Zloty"
    Russian_Rouble = "Russian Rouble"
    Swedish_Krona = "Swedish Krona"
    South_African_Rand = "South African Rand"
    Yuan_Renminbi_Onshore = "Yuan Renminbi (Onshore)"


class PositionType(Enum):
    """Position type. Unlike the enums above, gs does NOT derive this one from EnumBase
    (research/04 section 4), so it is a plain `Enum`: no case-insensitive construction, and
    `str(PositionType.OPEN)` is the default `Enum.__str__`, not `.value`."""

    OPEN = "open"
    CLOSE = "close"
    ANY = "any"


class Currency(EnumBase, str, Enum):
    """ISO-4217 currency code, `name == value`. gs's real enum has 341 members; this is a minimal
    subset covering research/04 section 4's list, as IMPLEMENTATION_PLAN.md P1.1 explicitly allows
    ("any ISO-4217 code from a code list covering at least the codes in R04 section 4"). Extend by
    adding more (name, value) pairs as new asset or FX configs need them. Case-insensitive
    construction (`Currency('usd')`) comes from the inherited `EnumBase._missing_`; no override is
    needed here."""

    _ = ""
    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"
    JPY = "JPY"
    CHF = "CHF"
    AUD = "AUD"
    CAD = "CAD"
    NZD = "NZD"
    SEK = "SEK"
    NOK = "NOK"
    DKK = "DKK"
    CNY = "CNY"
    CNH = "CNH"
    HKD = "HKD"
    SGD = "SGD"
    KRW = "KRW"
    INR = "INR"
    MXN = "MXN"
    BRL = "BRL"
    ZAR = "ZAR"
    PLN = "PLN"
    CZK = "CZK"
    HUF = "HUF"
    TRY = "TRY"
    ILS = "ILS"


FieldValueMap = dict  # gs's FieldValueMap is a dict subclass; pricebt just uses dict (DESIGN.md 9.2).

_LAZY = ("RiskMeasure", "ParameterisedRiskMeasure")


def __getattr__(name: str):
    if name in _LAZY:
        from pricebt import risk

        return getattr(risk, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
