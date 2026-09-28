# %% [markdown]
# # 040304: mean-reversion strategy on a toy USD swap
#
# Ported from gs_quant's `040304_strategy_mean_reversion` notebook (DESIGN.md Appendix B), pricing
# against the deterministic toy USD rates world (`tests/toylib/rates.py`) instead of a live ARBS
# market, so it is self-contained and runs in CI. From `action = ...` onwards the code is the same
# as the gs notebook: buy/sell 10y USD payers whenever the toy par rate strays from its own rolling
# mean.

# %%
from datetime import date

import pandas as pd

from pricebt.backtests.actions import AddTradeAction
from pricebt.backtests.data_sources import GenericDataSource, MissingDataStrategy
from pricebt.backtests.generic_engine import GenericEngine
from pricebt.backtests.strategy import Strategy
from pricebt.backtests.triggers import MeanReversionTrigger, MeanReversionTriggerRequirements
from pricebt.common import Currency, PayReceive
from pricebt.data import measure_series
from pricebt.instrument import IRSwap
from pricebt.risk import Price
from pricebt.session import PricebtSession

PricebtSession.use(assets=["tests/assets/toy_usd_irs.yaml"])    # replaces GsSession.use(...)

# %% [markdown]
# Fixed dates rather than `datetime.today()`: the toy rates world has no notion of "today", and a
# notebook committed to the repo must produce the same result every time it is run.

# %%
start_date = date(2024, 1, 2)
end_date = date(2025, 1, 2)

swap = IRSwap(pay_or_receive=PayReceive.Pay, termination_date='10y', notional_currency=Currency.USD,
              notional_amount=1e4, fixed_rate='ATM', name='swap_10y')

# %% [markdown]
# Replaces the GS `Dataset`: the 10y par rate (bp) of a fresh ATM 10y swap on every business day.

# %%
s = measure_series(IRSwap(termination_date='10y', notional_currency='USD'), 'par_rate', start_date, end_date, frequency='1b')

# %%
action = AddTradeAction(swap)
data_source = GenericDataSource(s, MissingDataStrategy.fill_forward)
z_score_bound = 2
rolling_mean_window = 30
rolling_std_window = 30
trig_req = MeanReversionTriggerRequirements(data_source, z_score_bound, rolling_mean_window, rolling_std_window)
trigger = MeanReversionTrigger(trig_req, action)
strategy = Strategy(None, trigger)

GE = GenericEngine()
backtest = GE.run_backtest(strategy, start=start_date, end=end_date, frequency='1b', show_progress=True)
backtest.trade_ledger()

# %%
backtest.result_summary

# %%
pd.DataFrame({'Generic backtester': backtest.result_summary['Cumulative Cash'] + backtest.result_summary[Price]}).plot(
    figsize=(10, 6), title='Performance')
