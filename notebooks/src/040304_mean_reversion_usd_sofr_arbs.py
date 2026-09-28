# %% [markdown]
# # 040304: mean-reversion strategy on a USD SOFR swap (ARBS)
#
# Ported from gs_quant's `040304_strategy_mean_reversion` notebook (DESIGN.md Appendix B), pricing
# against the live ARBS Eris curve store through
# `configs/assets/usd_sofr_ois_interest_rate_swap.yaml` instead of the toy rates world. From
# `action = ...` onwards the code is identical to the gs notebook and to the toy version
# (`notebooks/src/040304_mean_reversion_toy.py`): buy/sell a 10y USD payer whenever the 10y par
# rate strays from its own rolling mean.
#
# **This notebook is built but not executed** (`tools/nb_build.py --no-exec`;
# IMPLEMENTATION_PLAN.md P5.1): running it imports ARBS, which needs the user's approval (P5.2).
# The dates below are chosen from the config's documented coverage
# (`configs/assets/usd_sofr_ois_interest_rate_swap.yaml`'s description: 2020-07-01..2026-08-20,
# dense from 2021-01-04) so that a run, once approved, has real data across the whole window.

# %%
from datetime import date, datetime

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

PricebtSession.use(assets=["configs/assets/usd_sofr_ois_interest_rate_swap.yaml"])  # replaces GsSession.use(...)

# %% [markdown]
# `start_date` is the first date the config's ARBS source is dense (decision 0.2). `end_date` is
# `datetime.today().date()`, as in the gs notebook: dates past the config's `_LAST_SAFE`
# (2026-08-20) are dropped by `PricebtSession`'s `missing_market='drop'` policy, with a warning, and
# listed on `backtest.missing_market_dates` — the same way gs would drop a weekend or holiday.

# %%
start_date = date(2021, 1, 4)
end_date = datetime.today().date()

swap = IRSwap(pay_or_receive=PayReceive.Pay, termination_date="10y", notional_currency=Currency.USD,
              notional_amount=1e4, fixed_rate="ATM", name="swap_10y")

# %% [markdown]
# Replaces the GS `Dataset`: the 10y par rate (bp) of a fresh ATM 10y swap on every business day.

# %%
s = measure_series(IRSwap(termination_date="10y", notional_currency="USD"), "par_rate", start_date, end_date, frequency="1b")

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
backtest = GE.run_backtest(strategy, start=start_date, end=end_date, frequency="1b", show_progress=True)
backtest.trade_ledger()

# %%
backtest.result_summary

# %%
pd.DataFrame({"Generic backtester": backtest.result_summary["Cumulative Cash"] + backtest.result_summary[Price]}).plot(
    figsize=(10, 6), title="Performance")
