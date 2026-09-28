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
# gs's own __init__.py is `from .core import *` + `from .strategy_systematic import
# StrategySystematic` + `from gs_quant.target.backtests import DeltaHedgeParameters` (research/01
# section 1). pricebt keeps the same shape; DeltaHedgeParameters lives in `.core` here (DESIGN.md
# section 9.2's import map), so `from .core import *` already re-exports it -- core.py has no
# `__all__`, matching gs, so this also re-exports TimeWindow/ValuationFixingType/ValuationMethod/
# MarketModel/TradeInMethod/BacktestTradingQuantityType/Backtest/BacktestResult and the plain
# stdlib/typing names core.py imports, exactly as gs's own `import *` does.
from .core import *  # noqa: F401,F403
from .strategy_systematic import StrategySystematic  # noqa: F401
