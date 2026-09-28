"""
Copyright 2019 Goldman Sachs.
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

  http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing,
software distributed under the License is distributed on an
"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND, either express or implied.  See the License for the
specific language governing permissions and limitations
under the License.
"""
# Ported to pricebt from gs_quant 2.1.17 (Apache-2.0); see NOTICE. Changes: none
#
# TradeInMethod, MarketModel, TimeWindow, ValuationFixingType, ValuationMethod are ported as-is
# (DESIGN.md section 9.3). Backtest/BacktestResult are gs's server-side, JSON-oriented classes of
# the same name (NOT pricebt's real backtest result, which is `backtest_objects.BackTest`, P3.2) --
# out of scope, so construction raises. BacktestTradingQuantityType and DeltaHedgeParameters are
# gs_quant.target.backtests names that DESIGN.md section 9.2's import map places here instead.
#
# No `__all__`: gs's core.py has none either, so `from .core import *` (backtests/__init__.py)
# re-exports every public name here, including the plain stdlib/typing imports below -- this
# matches gs's own behaviour exactly (research/01 section 1).
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import Enum
from typing import NamedTuple, Optional, Union

from ..base import EnumBase
from ..errors import NotSupportedError


class TradeInMethod(EnumBase, Enum):
    FixedRoll = 'fixedRoll'


class Backtest:
    """gs's server-side backtest handle (`gs_quant.target.backtests.Backtest` plus a
    `get_results()` that calls the GS backtest API). Out of scope: pricebt has no such server. Run
    a strategy with `GenericEngine.run_backtest()` instead, which returns a real
    `backtest_objects.BackTest`."""

    def __init__(self, *args, **kwargs):
        raise NotSupportedError("Backtest is GS-server-only / out of scope in pricebt v2; use GenericEngine")


class BacktestResult:
    """gs's server-side backtest result record (`gs_quant.target.backtests.BacktestResult`). Out
    of scope for the same reason as `Backtest`."""

    def __init__(self, *args, **kwargs):
        raise NotSupportedError("BacktestResult is GS-server-only / out of scope in pricebt v2; use GenericEngine")


class MarketModel(EnumBase, Enum):
    STICKY_FIXED_STRIKE = "SFK"
    STICKY_DELTA = "SD"


class TimeWindow(NamedTuple):
    start: Union[dt.time, dt.datetime] = None
    end: Union[dt.time, dt.datetime] = None


class ValuationFixingType(EnumBase, Enum):
    PRICE = 'price'


class ValuationMethod(NamedTuple):
    data_tag: ValuationFixingType = ValuationFixingType.PRICE
    window: Optional[TimeWindow] = None


class BacktestTradingQuantityType(EnumBase, Enum):
    """The trading quantity unit of a backtest strategy (ported: gs_quant.target.backtests, via
    DESIGN.md section 9.2's import map -- gs's own signature has no such enum in `core.py`, but
    that is where this port keeps it)."""

    notional = 'notional'
    quantity = 'quantity'
    vega = 'vega'
    gamma = 'gamma'
    NAV = 'NAV'
    premium = 'premium'
    vegaNotional = 'vegaNotional'


@dataclass
class DeltaHedgeParameters:
    """Field shape ported from gs_quant.target.backtests.DeltaHedgeParameters, for constructor
    parity (DESIGN.md MUST-2) with the notebooks that build it positionally. Only StrategySystematic
    / EquityVolEngine ever consume it, and both are GS-server-only stubs in pricebt v2, so
    construction always raises."""

    frequency: str = None
    fixing_time: Optional[str] = None
    notional: Optional[float] = None
    delta_type: Optional[str] = field(init=False, default='BlackScholes')
    name: Optional[str] = None

    def __post_init__(self):
        raise NotSupportedError("DeltaHedgeParameters is GS-server-only / out of scope in pricebt v2; use GenericEngine")
