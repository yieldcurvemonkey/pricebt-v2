"""gs_quant-compatible enums (`gs_quant.common` names) for the backtest facade.

Only what the facade's instrument constructors and risk measures use. `Currency` lists the major ISO codes so gs code that names them
imports cleanly; only the session's reporting currency is supported (multi-currency is out of scope, spec C7): anything else raises NotSupportedError.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from .errors import ConfigError, NotSupportedError

__all__ = [
    "AggregationLevel", "BuySell", "Currency", "OptionStyle", "OptionType", "PayReceive", "BacktestTradingQuantityType", "RiskMeasure",
    "check_currency",
]


class _GsEnum(Enum):
    """Enum whose `coerce` accepts the member, its value or its name (case-insensitive), like gs_quant's EnumBase."""

    @classmethod
    def coerce(cls, x: Any) -> "_GsEnum":
        if isinstance(x, cls):
            return x
        if isinstance(x, Enum):  # another library's enum with the same member names (e.g. a gs_quant enum)
            x = x.value
        s = str(x).strip().lower()
        for m in cls:
            if s in (str(m.value).lower(), m.name.lower()):
                return m
        raise ConfigError(f"{x!r} is not a valid {cls.__name__}; expected one of {[m.value for m in cls]}", code="GS-ENUM")

    def __str__(self) -> str:
        return str(self.value)


class PayReceive(_GsEnum):
    """Pay or receive fixed: a payer or a receiver swap (`Straddle` is gs's option value; a swap does not take it)."""

    Pay = "Pay"
    Payer = "Payer"
    Receive = "Receive"
    Receiver = "Receiver"
    Straddle = "Straddle"
    Rec = "Rec"

    @property
    def is_pay(self) -> bool:
        return self in (PayReceive.Pay, PayReceive.Payer)

    @property
    def is_receive(self) -> bool:
        return self in (PayReceive.Receive, PayReceive.Receiver, PayReceive.Rec)


class BuySell(_GsEnum):
    Buy = "Buy"
    Sell = "Sell"


class AggregationLevel(_GsEnum):
    """gs_quant risk aggregation. Type / Asset / Class collapse a risk to one number per currency; Point keeps the buckets."""

    Type = "Type"
    Asset = "Asset"
    Class = "Class"
    Point = "Point"


class OptionType(_GsEnum):
    Call = "Call"
    Put = "Put"


class OptionStyle(_GsEnum):
    European = "European"
    American = "American"
    Bermudan = "Bermudan"


class BacktestTradingQuantityType(_GsEnum):
    """gs_quant.target.backtests.BacktestTradingQuantityType (only EnterPositionQuantityScaledAction used it; that action is a stub)."""

    quantity = "quantity"
    notional = "notional"
    NAV = "NAV"


class Currency(_GsEnum):
    """ISO 4217 codes (names only: the session's reporting currency is the one supported currency)."""

    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"
    JPY = "JPY"
    CHF = "CHF"
    CAD = "CAD"
    AUD = "AUD"
    NZD = "NZD"
    SEK = "SEK"
    NOK = "NOK"
    DKK = "DKK"
    CNY = "CNY"
    HKD = "HKD"
    SGD = "SGD"
    KRW = "KRW"
    INR = "INR"
    BRL = "BRL"
    MXN = "MXN"
    ZAR = "ZAR"
    PLN = "PLN"
    CZK = "CZK"
    HUF = "HUF"
    ILS = "ILS"
    TRY = "TRY"


def check_currency(ccy: Any, what: str, *, allow_local: bool = False, session: Any = None) -> str:
    """The reporting currency for None / that currency's code / its Currency member (and 'local' when allowed); NotSupportedError for any other currency.
    The reporting currency is that of `session`, else the current session (`PricebtSession(reporting_currency=...)`), else the gs_quant compatibility default."""
    from .session import DEFAULT_REPORTING_CURRENCY, current_session  # lazy: session imports the contracts, which do not import this module

    s_ = session if session is not None else current_session()
    reporting = s_.reporting_currency if s_ is not None else DEFAULT_REPORTING_CURRENCY
    if ccy is None:
        return reporting
    s = str(ccy.value if isinstance(ccy, Enum) else ccy).strip()
    if allow_local and s.lower() == "local":
        return "local"
    if s.upper() == reporting:
        return reporting
    raise NotSupportedError(
        f"{what}: currency {s!r} is not supported: results and instruments are in the session's reporting currency {reporting!r} (multi-currency is out of scope). "
        "Model other currencies with your own instruments and market (see docs/guides/backtesting.md, 'Plug in your own market and instruments')."
    )


def __getattr__(name: str) -> Any:  # gs_quant.common also exports RiskMeasure; import lazily (pricebt.risk imports this module)
    if name == "RiskMeasure":
        from .risk import RiskMeasure

        return RiskMeasure
    raise AttributeError(name)
