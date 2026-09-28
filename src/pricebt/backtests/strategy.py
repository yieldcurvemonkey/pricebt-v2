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
# Ported to pricebt from gs_quant 2.1.17 (Apache-2.0); see NOTICE. Changes: DEV-T14
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Union

from ..base import Priceable
from .backtest_objects import CashAccrualModel
from .backtest_utils import make_list
from .triggers import Trigger


def _backtest_engines():
    from .equity_vol_engine import EquityVolEngine
    from .generic_engine import GenericEngine
    from .predefined_asset_engine import PredefinedAssetEngine

    return [GenericEngine(), PredefinedAssetEngine(), EquityVolEngine()]


@dataclass
class Strategy:
    """
    A strategy object on which one may run a backtest
    """

    initial_portfolio: Optional[Union[tuple[Priceable, ...], dict]] = None
    triggers: Union[Trigger, Iterable[Trigger]] = None
    cash_accrual: CashAccrualModel = None
    risks = None

    def __post_init__(self):
        if not isinstance(self.initial_portfolio, dict):
            self.initial_portfolio = make_list(self.initial_portfolio)
        self.triggers = make_list(self.triggers)
        self.risks = self.get_risks()

    def get_risks(self):
        risk_list = []
        for t in self.triggers:
            risk_list += t.risks if t.risks is not None else []
        return risk_list

    def get_available_engines(self):
        if not self.triggers:
            # pricebt DEV-T14: gs's `reduce(lambda x, y: x + y, map(lambda x: x.actions,
            # strategy.triggers))` has no initial value, so a strategy with zero triggers (e.g. a
            # static buy-and-hold initial_portfolio with no triggers at all -- a legal Strategy)
            # raises `TypeError: reduce() of empty iterable with no initial value` inside
            # GenericEngine.supports_strategy before it even gets a chance to answer. Return the
            # general-purpose engine directly instead of crashing. Lazy import, same pattern as
            # gs's own _backtest_engines() above: GenericEngine (P3.5) will import Strategy for type
            # hints, so importing it at this module's top level would be circular.
            from .generic_engine import GenericEngine

            return [GenericEngine()]
        return [engine for engine in _backtest_engines() if engine.supports_strategy(self)]
