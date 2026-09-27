# 05 — gs_quant backtesting notebook coverage for pricebt v2

## Key facts

1. The 24 notebooks under `gs-quant/gs_quant/documentation/04_backtesting` use 3 engines: `GenericEngine` (17 notebooks), `EquityVolEngine` (6, runs on the GS server) and `PredefinedAssetEngine` (1). Only `GenericEngine` is in scope for pricebt v2.
2. With only an IRSwap asset, 3 notebooks port as they are: **040304 mean reversion, 040310 varying-CSA hedge, 040311 holidays**. 4 more need a swaption asset (040303, 040305, 040307, `Backtesting.ipynb`). 6 need FX, 1 needs inflation, 3 need equity options, and 7 use a non-Generic engine. Every GenericEngine notebook has a *swap-substitution variant* that exercises the same trigger/action/result API (§4).
3. Every result call the notebooks make comes down to 4 things. First, `result_summary`, whose columns are keyed by **RiskMeasure instances**. Second, `trade_ledger()`, which has exactly 7 columns. Third, `strategy_as_time_series()`, a MultiIndex frame. Fourth, `portfolio_dict`. Their exact shapes are pinned in §5. The notebooks also use `result_summary['Cumulative Cash'] + result_summary[Price]` as "Performance".
4. 040304's trigger calls `get_data_range(state, N)`, which returns up to N points **strictly before** `state` (pandas `std`, ddof=1). The installed 1.5.4 `MeanReversionTriggerRequirements` has 2 bugs that the repo (2.1.17) fixes. The installed `GenericDataSource` also has two data problems. `get_data_range` raises `TypeError` on a `DatetimeIndex` under pandas 2.3.1, and on a `dt.date` index the fill_forward path looks ahead. v2 must therefore build the par-rate series on the full weekday schedule. See §6.
5. **Version caveat.** The reference is the installed `gs_quant` 1.5.4 (`C:/Users/chris/anaconda3/Lib/site-packages/gs_quant/backtests`). The notebooks and the skills doc come from the repo checkout (git tag `f2a505a` "release 2.1.17"). Every `backtests/*.py` file except `decorator.py` differs between the two, and the repo adds `generic_engine_action_impls.py` and `BackTest.summary_stats()`. File:line references below are to the **installed** tree unless marked "repo".

Abbreviations: **GE** = `GenericEngine`, **EVE** = `EquityVolEngine`, **PAE** = `PredefinedAssetEngine`. Paths are shortened as `bo.py` = `backtests/backtest_objects.py`, `ge.py` = `backtests/generic_engine.py`, `trg.py` = `backtests/triggers.py`, `act.py` = `backtests/actions.py`, `ds.py` = `backtests/data_sources.py`, `bu.py` = `backtests/backtest_utils.py`.

---

## 1. Sources read

| Folder | Notebook | Cells | Engine |
|---|---|---|---|
| examples/01_PredefinedAssetEngine | 040100_simple_example | 9 | PAE |
| examples/02_EquityVolEngine | 040200_strategy_simple | 8 | EVE |
| | 040201_strategy_delta_hedged | 8 | EVE |
| | 040202_strategy_pnl_decomposition | 8 | EVE |
| | 040203_strategy_with_signals | 9 | EVE |
| | 040204_strategy_market_model | 12 | EVE |
| | 040205_strategy_scaled_add_action | 19 | EVE |
| examples/03_GenericEngine | 040300_strategy_periodic_trigger | 11 | GE |
| | 040301_strategy_risk_trigger | 13 | GE |
| | 040302_strategy_delta_hedge_FX | 12 | GE |
| | 040303_strategy_delta_hedge_Rates | 10 | GE |
| | 040304_strategy_mean_reversion | 12 | GE |
| | 040305_strategy_exit_trade_action | 10 | GE |
| | 040306_strategy_mkt_trigger | 13 | GE |
| | 040307_strategy_seagull_bullish | 9 | GE |
| | 040308_rebalance_action | 18 | GE |
| | 040309_inflation_hedging_strategy | 11 | GE |
| | 040310_initial_swap_hedging_strategy_varying_CSA | 11 | GE |
| | 040311_handling_holidays | 15 | GE |
| | 040312_strategy_scaled_add_action | 37 | GE |
| | 040313_chained_actions | 16 | GE |
| | 040314_gradual_entries | 10 | GE |
| tutorials | Backtesting | 21 | GE |
| | Basic backtest walkthrough | 37 | GE (**the only notebook with saved outputs**) |

Skills: `gs_quant/skills/gs-quant-overview/backtesting.md`, the backtesting guide. `SKILL.md` covers only session and resolve. `backtesting.md` §6 lists the GE parameters. §7 says `result_summary` columns are "`Price`, `Cumulative Cash`, `Transaction Costs`, `Total`, plus any additional risk columns". It also documents `summary_stats()`, which exists **only in the repo**, not in installed 1.5.4.

Method: I parsed every `.ipynb` as JSON and parsed its code cells with `ast`. The script computed the import and call counts in §3. I checked it against 040304, which I had read by hand: 13 imported names, 1 `Dataset(`, 1 `.trade_ledger(`, 3 `.result_summary`.

---

## 2. Coverage matrix

### 2a. Instruments, triggers, actions

| NB | Instruments (fields as written) | Risk measures | Triggers | Actions |
|---|---|---|---|---|
| 040100 | `FXOption(buy_sell='Buy', option_type='Put', pair='EURUSD', notional_amount='10m', name='EURUSD')`. The instrument is a label only; prices come from the DataManager. | none | `DateTrigger(DateTriggerRequirements(trigger_times))` with **datetimes** (intraday) | `AddTradeAction(asset, pd.Timedelta(minutes=30))` |
| 040200 | `EqOption('SX5E', underlier_type=UnderlierType.BBID, expirationDate='3m', strikePrice='ATM', optionType=Call, optionStyle=European)` | `FlowVolBacktestMeasure.PNL` | `PeriodicTrigger(PeriodicTriggerRequirements(start_date, end_date, frequency='1m'))` | `EnterPositionQuantityScaledAction(priceables, trade_duration='1m', trade_quantity=1000, trade_quantity_type=BacktestTradingQuantityType.quantity)` |
| 040201 | the same EqOption; `long_call`/`short_put` EqOptions with `buy_sell`; `Portfolio(name='SynFwd', priceables=[...])` | `EqDelta` (hedge), FlowVol PNL | 2× Periodic (`'1m'`, `'1b'`) | EnterPositionQuantityScaled; `HedgeAction(EqDelta, priceables=hedge_portfolio, trade_duration='1b')` |
| 040202 | EqOption | FlowVol `PNL`, `PNL_carry`, `PNL_vol` | Periodic `'1m'` | EnterPositionQuantityScaled |
| 040203 | EqOption | FlowVol PNL | `AggregateTrigger(AggregateTriggerRequirements([DateTriggerRequirements(dates=...), PortfolioTriggerRequirements('len', 0, TriggerDirection.EQUAL)]))`, and a second one with `ABOVE` | EnterPositionQuantityScaled; `ExitTradeAction()` |
| 040204 | EqOption | FlowVol `PNL`, `delta`, `gamma`, `vega` | Periodic `'1m'` | EnterPositionQuantityScaled |
| 040205 | `EqOption('.STOXX50E', expiration_date='1m', strike_price='ATM', option_type, option_style, name)` ×2 in `Portfolio` | `risk.EqVega`; FlowVol PNL | Periodic `'1m'` | `AddTradeAction(portfolio,'1m',name='act')`; `AddScaledTradeAction(scaling_type=ScalingActionType.size, scaling_risk=None, scaling_level=2)`; the same with `ScalingActionType.risk_measure, scaling_risk=risk.EqVega, scaling_level=100`; `EnterPositionQuantityScaledAction(..., trade_quantity=100, trade_quantity_type=BacktestTradingQuantityType.NAV)` |
| 040300 | `FXOption(buy_sell=Buy, option_type=Call, pair='USDJPY', strike_price='ATMF', expiration_date='2y', name='2y_call', premium=0)` | `Price` | Periodic `'1m'` | `AddTradeAction(call, '1m')` |
| 040301 | `FXOption(... pair='EURUSD', expiration_date='1y', notional_amount=1e5, premium=0)`; `FXForward(pair='EURUSD', settlement_date='1y', notional_amount=1e5, name='1y_forward')` | `Price`, `FXDelta(aggregation_level=AggregationLevel.Type, currency='USD')`, and the same with `aggregation_level='Type'` as a string | Periodic `'1m'`; `StrategyRiskTrigger(RiskTriggerRequirements(risk=hedge_risk, trigger_level=50e3, direction=ABOVE))` | AddTradeAction(call,'1m'); `HedgeAction(hedge_risk, fwd_hedge)` (no duration) |
| 040302 | FXOption USDJPY 2y `notional_amount=1e6`; `FXForward(pair='USDJPY', settlement_date='2y')` | Price, `FXDelta(currency='USD', aggregation_level='Type')` | Periodic `'1m'` with **2 actions** | AddTradeAction; `HedgeAction(hedge_risk, fwd_hedge, '1m')` |
| 040303 | `IRSwaption(expiration_date='1m', termination_date='30y', notional_currency=Currency.EUR, buy_sell='Sell')`; `IRSwap(termination_date='30y', notional_currency=EUR, notional_amount=100e6, name='30yhedge')` | Price, `IRDelta(aggregation_level='Type', currency=Currency.USD)` | Periodic `'1m'`, 2 actions | `AddTradeAction(option, 'expiration_date')`; `HedgeAction(hedge_risk, swap_hedge)` |
| 040304 | `IRSwap(pay_or_receive=PayReceive.Pay, termination_date='10y', notional_currency=Currency.EUR, notional_amount=1e4, fixed_rate='ATM', name='swap_10y')` | Price | `MeanReversionTrigger(MeanReversionTriggerRequirements(data_source, 2, 30, 30))` | `AddTradeAction(swap)` (no duration) |
| 040305 | `IRSwaption(expiration_date='6m', termination_date='10y', notional_currency=USD, buy_sell=Sell, strike='=solvefor(25e3,bp)', name='swaption10y')` | Price, `IRDelta(aggregation_level=AggregationLevel.Type, currency='USD')` | `StrategyRiskTrigger(RiskTriggerRequirements(risk, trigger_level=1e3, direction=ABOVE))` | `[ExitTradeAction(), AddTradeAction(swaption)]`, applied in that order; `Strategy(swaption, ...)` sets an initial portfolio |
| 040306 | `FXOption(Buy, Put, 'USDCNH', 'ATMF', '1m', premium=0)` | Price | `MktTrigger(MktTriggerRequirements(data_source, 6.42, TriggerDirection.BELOW))` | AddTradeAction(put) |
| 040307 | 3× `IRSwaption(pay_or_receive=['Receiver','Receive','Pay'][i], termination_date='10y', notional_currency='USD', expiration_date='1y', notional_amount=±n, strike=['A-50','A-25','A+50'][i], floating_rate_option='USD-LIBOR-BBA')` in `Portfolio(port, name='Seagull')` | Price, `IRDelta(currency='local', aggregation_level=Type)`; after the run, `IRFwdRate`, `IRDailyImpliedVol`, `DollarPrice` | Periodic `'1w'` | `AddTradeAction(seagull, '4w')` |
| 040308 | `EqOption('.SPX'/'.NDX', expiration_date='2m', strike_price='ATM', ...)`; `spx_opt.resolve()` inside `PricingContext(start_date)` | `gs_quant.target.measures.Price` | Periodic `'1b'` and `'1w'` | `AddTradeAction(ndx_opt,'2m',name='Action1')`; `RebalanceAction(spx_opt, 'number_of_options', match_ndx_holding)` |
| 040309 | `InflationSwap(pay_or_receive, termination_date='30y'/'50y', notional_currency='EUR', notional_amount=100e6, name)` | Price, `InflationDelta(aggregation_level='Type')` | Periodic `'1b'` | `[AddTradeAction(infl,'1m'), HedgeAction(InflationDelta(...), infl_hedge, '1b')]` |
| 040310 | `IRSwap(pay_or_receive='Receive', termination_date='30y', notional_currency='EUR', notional_amount=100e6, fixed_rate='ATM', name='30yhedge')`; the same with `'Pay'`, `'50y'`, `name='50y'` | Price, `IRDelta(aggregation_level='Type')` | Periodic `'1b'` | `HedgeAction(IRDelta(aggregation_level='Type'), swap_hedge, csa_term='EUR-OIS')`; `Strategy([swap], ...)` |
| 040311 | `IRSwap('Pay', '10y', 'USD', notional_amount=10000, name='10y')` (positional) | (Price) | `PeriodicTriggerRequirements(..., frequency='1b', calendar=(date(2024,5,27),))` | `AddTradeAction(irswap)`; `AddTradeAction(irswap, '1b')`; `AddTradeAction(irswap, '1b', holiday_calendar=holiday)` |
| 040312 | the same EqOption portfolio as 040205 | Price, `risk.EqVega` | Periodic `'1m'` | the same 4 actions as 040205, plus `AddScaledTradeAction(..., transaction_cost=ConstantTransactionModel(1))` |
| 040313 | `FXOption(pair='EURUSD', expiration_date='3m', option_type='Call', name='call')` | (Price) | `PeriodicTriggerRequirements(..., frequency='1w', calendar=hol_cal)` | `AddTradeAction(call, '1w', name=..., holiday_calendar=hol_cal)`; `trade_duration='next schedule'` |
| 040314 | `EqOption(underlier='.VIX', expiration_date='3m', option_type='put')` | `EqVega` | `AggregateTrigger(AggregateTriggerRequirements([MktTriggerRequirements(ds, 6500, ABOVE), TradeCountTriggerRequirements(10, BELOW)], AggType.ALL_OF))`; `MktTrigger(... BELOW)` | `AddScaledTradeAction(opt, '3m', scaling_type=risk_measure, scaling_risk=EqVega, scaling_level=100000)`; `ExitAllPositionsAction(name='ExitAction')` |
| Backtesting | `IRSwaption(PayReceive.Straddle, '10y', Currency.USD, expiration_date='1m', notional_amount=1e8, buy_sell='Sell')` | Price | Periodic `'1b'` | `AddTradeAction(straddle, 'expiration_date')` |
| Walkthrough | `FXOption(pair='EURUSD', expiration_date='3m', strike_price='25d', option_type='Call', premium=0[, name='EURUSD call'])`; `FXForward(pair='EURUSD', settlement_date='2b'/'1w'[, name='spot hedge'])` | `FXDelta(aggregation_level='Type')` | Periodic `'1b'`/`'1w'`; `StrategyRiskTrigger(RiskTriggerRequirements(FXDelta(...), 4e6, ABOVE))` and `(-4e6, BELOW)` | AddTradeAction(`'1w'`); `HedgeAction(risk=, priceables=, trade_duration='1b', name='hedge action', transaction_cost=t_cost)`; `ScaledTransactionModel('notional_amount', 0.00005)` |

### 2b. Engine call, results, data, GS-server dependencies

| NB | Engine + `run_backtest` args | Result API used | Data sources | GS-server-specific |
|---|---|---|---|---|
| 040100 | `PredefinedAssetEngine(data_manager).run_backtest(strategy, start=, end=, frequency='D')` | `bt.performance.plot()`, `bt.trade_ledger()` | `DataManager().add_data_source(series, DataFrequency.REAL_TIME/DAILY, asset, ValuationFixingType.PRICE)` using random prices | none for pricing (the session is still created) |
| 0402xx | `EquityVolEngine.run_backtest(strategy, start=, end=[, market_model='SFK'/'SVR'])` (a class method, no instance) | `backtest.get_measure_series(FlowVolBacktestMeasure.X)` | – | the whole engine runs on the server; underliers are BBID/RIC |
| 040205 | EVE ×4 | get_measure_series; `portfolio.calc(risk.EqVega)` in `PricingContext` | – | pricing; `PricingContext` is not imported (NameError, §8) |
| 040300 | `GE.run_backtest(strategy, start=, end=, frequency='1b', show_progress=True)` | `trade_ledger()`, `result_summary`, `result_summary[Price]`, `result_summary['Cumulative Cash'] + result_summary[Price]`, `.plot(figsize=(10,6), title=...)` | – | resolves ATMF strike; `end=today` |
| 040301 | `run_backtest(..., risks=[Price, FXDelta(...)])`; run 2 has no `risks` (FXDelta comes in through `strategy.risks`) | `result_summary[FXDelta(aggregation_level=AggregationLevel.Type, currency='USD')]`, `pd.concat` of 2 runs | – | pricing |
| 040302 | `'1b'` | ledger, summary, Price, Performance, FXDelta column | – | pricing |
| 040303 | `'1b'` (a comment mentions `result_ccy`) | ledger, summary, `df['Performance'] = df[Price] + df['Cumulative Cash']`; `df.plot(...).legend(bbox_to_anchor=(1,1))` | – | swaption vol, resolution |
| 040304 | `'1b'` | ledger, summary, Performance | `Dataset('SWAPRATES_STANDARD').get_data(start_date, assetId=['MA5WM2QWRVMYKDK0'])` filtered by `data['tenor'] == '10y'`, column `'rate'` → `GenericDataSource(s, MissingDataStrategy.fill_forward)` | Marquee Dataset, `read_product_data` scope, ATM resolution |
| 040305 | `'1b'` | summary, ledger, Price, Performance | – | `strike='=solvefor(25e3,bp)'` is a **server-side solver** |
| 040306 | `'1b'` | ledger, summary, Performance, `s.plot()` | `Dataset('FXSPOT_PREMIUM').get_data(start_date, assetId=['MAEFRJZ9NYGDDR41'])['spot']` | Dataset |
| 040307 | `run_backtest(..., frequency=freq ('1w'), risks=[Price, myParallelDelta], show_progress=True, csa_term='USD-1')` | `result_summary`, `backtest_summary.index`, `backtest_summary.iloc[5, 1]` (**positional column index**), `['Cumulative Cash']` | `HistoricalPricingContext(dates=..., show_progress=True)` + `seagull[0].calc([IRFwdRate, IRDailyImpliedVol])`, `seagull.calc([DollarPrice])`, `.result().to_frame()` | csa string, LIBOR index, `business_day_offset` |
| 040308 | `run_backtest(strategy, start=, risks=[Price], end=, frequency='1b', show_progress=True)` | `backtest.portfolio_dict[state].all_instruments` (inside the rebalance method), `strategy_as_time_series()` with `ts.index.get_level_values('Instrument Name')`, `groupby('Pricing Date').agg({('Static Instrument Data','number_of_options'): ['sum']})`, Price, Performance | – | `resolve()`; `PricingContext` not imported |
| 040309 | `'1b'` | ledger, summary, Price, Performance | – | inflation curves |
| 040310 | `'1b'` | ledger, summary, Price, Performance | – | `csa_term='EUR-OIS'` |
| 040311 | `run_backtest(..., holiday_calendar=holiday)` where `holiday` is a tuple of dates. The installed signature annotates `Optional[str]`, but the value is passed on to `RelativeDateSchedule.apply_rule(holiday_calendar: List[date])` | `result_summary`, `list(trade_ledger()['Open'])`, `['Close']` | – | – |
| 040312 | `'1b'`, `risks=risk.EqVega` (**a bare measure, not a list**) | summary ×7, ledger ×5, `result_summary['Total']` | – | `PricingContext` not imported |
| 040313 | `'1b'` | ledger | – | `dt` arrives through a star import |
| 040314 | `'1b'` | ledger, `s.plot()` | `Dataset('TREOD').get_data(start_date, assetId=['MA4B66MW5E27U8P32SB'])['closePrice']` | Dataset; `scopes=('run_analytics')` is a string, not a tuple |
| Backtesting | `ge = GenericEngine(); ge.run_backtest(strategy, start=, end=, frequency='1b', show_progress=True)`; bare `backtest` repr | `strategy.get_available_engines()`, `ge.supports_strategy(strategy)`, ledger, summary, Price, Performance | – | pricing |
| Walkthrough | `ge.run_backtest(strategy=, start=, end=, frequency='1b'[, risks=[FXDelta(aggregation_level='Type')]])` (keyword `strategy=`) | `portfolio_dict` (repr `defaultdict(Portfolio, {date: Portfolio(n instrument(s))})`), `result_summary`, `trade_ledger()`, `result_summary['Total'].plot()` | – | pricing; cell 29 NameError |

---

## 3. Union of the gs API surface (computed by script)

Count = number of notebooks (out of 24) that use the name. "(via \*)" means the name arrives through `from gs_quant.backtests.{actions,equity_vol_engine,triggers} import *`; I verified the star-exported names against installed 1.5.4.

| Module | Name: #NB |
|---|---|
| `gs_quant.backtests.strategy` | `Strategy`: 24 |
| `gs_quant.backtests.generic_engine` | `GenericEngine`: 17 |
| `gs_quant.backtests.actions` | `AddTradeAction`: 13 (+4 via \*), `HedgeAction`: 6 (+1), `ExitTradeAction`: 1 (+1), `AddScaledTradeAction`: 1 (+2), `ExitAllPositionsAction`: 1, `ScalingActionType`: 1 (+2); via \* only: `EnterPositionQuantityScaledAction`: 7, `RebalanceAction`: 1 |
| `gs_quant.backtests.triggers` | `PeriodicTrigger` / `PeriodicTriggerRequirements`: 10 (+8 via \*), `TriggerDirection`: 5 (+1), `RiskTriggerRequirements` / `StrategyRiskTrigger`: 3, `MktTrigger` / `MktTriggerRequirements`: 2, `MeanReversionTrigger` / `MeanReversionTriggerRequirements`: 1, `DateTrigger`: 1, `DateTriggerRequirements`: 1 (+1), `AggregateTrigger` / `AggregateTriggerRequirements`: 1 (+1), `AggType`: 1, `TradeCountTriggerRequirements`: 1; via \* only: `PortfolioTriggerRequirements`: 1 |
| `gs_quant.backtests.data_sources` | `GenericDataSource`: 3, `MissingDataStrategy`: 3, `DataManager`: 1 |
| `gs_quant.backtests.backtest_objects` | `ScaledTransactionModel`: 1; `ConstantTransactionModel`: 1 (via \*) |
| `gs_quant.backtests.core` | `ValuationFixingType`: 1 |
| `gs_quant.backtests.predefined_asset_engine` | `PredefinedAssetEngine`: 1 |
| `gs_quant.backtests.equity_vol_engine` | `EquityVolEngine` (via \*): 6; `FlowVolBacktestMeasure` (via \*): 6 |
| `gs_quant.target.backtests` | `BacktestTradingQuantityType` (via \*): 7 |
| `gs_quant.instrument` | `OptionStyle`: 8, `FXOption`: 7, `EqOption`: 6 (+3 via \*), `OptionType`: 5, `IRSwap`: 4, `IRSwaption`: 4, `FXForward`: 3, `InflationSwap`: 1 |
| `gs_quant.risk` | `Price`: 12 (+1 as `gs_quant.target.measures.Price`), `IRDelta`: 4, `FXDelta`: 3, `import gs_quant.risk as risk` (for `risk.EqVega`): 2, `EqVega`: 1, `InflationDelta`: 1, `DollarPrice`: 1, `IRFwdRate`: 1, `IRDailyImpliedVol`: 1; `EqDelta` (via \*): 1 |
| `gs_quant.common` | `BuySell`: 5 (+1), `AggregationLevel`: 4, `Currency`: 4, `OptionType`: 4 (+3), `PayReceive`: 2 |
| `gs_quant.target.common` | `UnderlierType`: 5 |
| `gs_quant.markets` / `.portfolio` | `HistoricalPricingContext`: 1; `Portfolio`: 1 (+3 via \*); `PricingContext`: used in 4 but **imported by none** (§8) |
| `gs_quant.data` | `Dataset`: 3, `DataFrequency`: 1 |
| `gs_quant.datetime` | `business_day_offset`: 1 |
| `gs_quant.session` | `GsSession`: 24, `Environment`: 5 |
| non-gs via \* | `pd` (040308, 040312), `dt` (040313) |

Result-object calls (occurrences summed over notebooks): `.result_summary` 73, `.run_backtest(` 44, `.trade_ledger(` 26, `.get_measure_series(` 18, `.portfolio_dict` 2, `.strategy_as_time_series(` 1, `.performance` 1 (PAE), `get_available_engines(` 1, `supports_strategy(` 1. `summary_stats(` is used by **no** notebook; it appears only in the skills doc.

**Surface pricebt v2 must provide for the GE notebooks** (strictly what is used; the design adds `NotTrigger`, `DateTrigger`, `PortfolioTrigger`, `TradeCountTrigger`, `ExitAllPositionsAction`, `AggregateTransactionModel` and the rest from the installed signatures):
`Strategy(initial_portfolio, triggers, cash_accrual=None)`; `GenericEngine()` with `.run_backtest(strategy, start, end, frequency='1m', states, risks, show_progress, csa_term, initial_value, result_ccy, holiday_calendar, ...)`, `.supports_strategy`; `Strategy.get_available_engines`; all triggers and requirements listed above; `AddTradeAction`, `AddScaledTradeAction`, `HedgeAction`, `ExitTradeAction`, `ExitAllPositionsAction`, `RebalanceAction`, `EnterPositionQuantityScaledAction`; `ScalingActionType`; `ConstantTransactionModel`, `ScaledTransactionModel`; `GenericDataSource`, `MissingDataStrategy`; `Price` and parameterised risk measures with `aggregation_level` / `currency`; `AggregationLevel`, `TriggerDirection`, `AggType`; and `BackTest.result_summary`, `.trade_ledger()`, `.strategy_as_time_series()`, `.portfolio_dict`.

---

## 4. Runnability in pricebt v2 with only an IRSwap asset

Tags: **RUN** = ports with swaps as they are (the `Dataset` call is replaced by an asset-derived series). **SWAPTION**, **FX** (FX option and/or forward), **EQ**, **INFL** = the notebook needs that asset config. **ENGINE** = uses a non-GE engine. "Swap variant" = keep the trigger/action/result code and swap in an IRSwap.

| NB | Tag | Blocking elements | Swap variant (tests the same pricebt feature) |
|---|---|---|---|
| 040100 | ENGINE (PAE) | intraday datetimes, DataManager, `bt.performance` | out of scope |
| 0402xx (6) | ENGINE (EVE) + EQ | runs on the server, `FlowVolBacktestMeasure`, `market_model` | out of scope |
| 040300 | FX | FXOption | monthly roll of a USD 10y SOFR payer: `AddTradeAction(swap,'1m')` (§7.3) |
| 040301 | FX | FXOption, FXForward, FXDelta | `StrategyRiskTrigger` on the swap book's `IRDelta(aggregation_level='Type', currency='USD')` > level, hedged with a swap |
| 040302 | FX | FXOption, FXForward | Periodic add + `HedgeAction(IRDelta, swap_hedge, '1m')` |
| 040303 | SWAPTION | IRSwaption | a EUR 10y swap rolled monthly and hedged with a EUR 30y swap, using `IRDelta(currency=USD)` (§7.2) |
| **040304** | **RUN** | Dataset → asset par-rate series | USD 10y SOFR (§6) |
| 040305 | SWAPTION | IRSwaption, `strike='=solvefor(25e3,bp)'` | initial portfolio of a USD swap; `StrategyRiskTrigger` → `[ExitTradeAction(), AddTradeAction(swap)]` |
| 040306 | FX | FXOption; Dataset FX spot | `MktTrigger` on the swap par-rate series (units matter, §6.6) |
| 040307 | SWAPTION | 3-leg swaption Portfolio, IRFwdRate, IRDailyImpliedVol, DollarPrice, `csa_term='USD-1'` | a Portfolio of swaps with weekly add, `risks=[Price, IRDelta(currency='local', aggregation_level=Type)]` |
| 040308 | EQ | EqOption `number_of_options`, `resolve()` | `RebalanceAction(swap, <size param>, fn)`. Open question: v2 has no field semantics (§9) |
| 040309 | INFL | InflationSwap, InflationDelta | the HedgeAction pattern with IRSwap/IRDelta (this is the same as 040310) |
| **040310** | **RUN** (needs a EUR curve) | EUR market expression; `csa_term='EUR-OIS'` routing (§7.1) | as written, with EUR swaps |
| **040311** | **RUN** | – (a USD 10y swap as written) | as written, with the USD SOFR asset |
| 040312 | EQ | EqOption portfolio, EqVega, NAV | AddScaled `size` / `risk_measure` with `IRDelta`; the NAV action on swaps (PV ≈ 0 makes NAV sizing degenerate); `ConstantTransactionModel(1)` |
| 040313 | FX | FXOption | as written with a swap: `'1w'` + `calendar` + `'next schedule'` |
| 040314 | EQ | EqOption, EqVega, Dataset | AggregateTrigger(Mkt on par rate + TradeCount) → AddScaled(risk_measure=IRDelta) / ExitAllPositions |
| Backtesting | SWAPTION | IRSwaption straddle, `'expiration_date'` | a swap with `trade_duration='termination_date'`, or `'1m'` |
| Walkthrough | FX | FXOption, FXForward, FXDelta | a swap roll + daily IRDelta hedge + `ScaledTransactionModel('notional_amount', 0.00005)` + risk-band triggers |

Count: RUN 3; SWAPTION 4; FX 6 (040300, 040301, 040302, 040306, 040313, the walkthrough); EQ 3 GE notebooks (040308, 040312, 040314); INFL 1; ENGINE 7 (PAE 1 + EVE 6). Total 24.

---

## 5. Result-object and engine contracts the notebooks depend on (installed 1.5.4)

### 5.1 `BackTest.result_summary` (bo.py:209-235)

- `summary = get_risk_summary_df()` (bo.py:185-207). For each date that has results, and for each `risk in results.risk_measures`, the value is `results[risk].aggregate(True, True)`. The DataFrame is `pd.DataFrame(dict).T.sort_index()`, so the index is the dates and the **column keys are the RiskMeasure objects themselves**.
- Cash: `self._cash_dict[date][ccy]` gives columns `f'Cumulative Cash {ccy}'`. **If there is more than one currency, it raises `RuntimeError('Cannot aggregate cash in multiple currencies')` (bo.py:219-220).** A single currency is renamed to `'Cumulative Cash'`. With no cash it is an empty column.
- `Transaction Costs` = `pd.Series(self.transaction_costs).sort_index().cumsum()`. It is ≤ 0 because the engine stores `-sum(tce.get_final_cost())` (ge.py:866-870).
- `df = pd.concat([summary, cash, tc], axis=1, sort=True).ffill().fillna(0)`, then `df['Total'] = df[price_measure] + df['Cumulative Cash'] + df['Transaction Costs']`, then `return df[: self.states[-1]]`.
- Class constants: `CUMULATIVE_CASH_COLUMN = "Cumulative Cash"`, `TRANSACTION_COSTS_COLUMN = "Transaction Costs"`, `TOTAL_COLUMN = "Total"` (bo.py:86-88).
- Which risks become columns: `risks = list(set(make_list(risks) + strategy.risks + pnl_risks + [self.price_measure]))` (ge.py:795). `strategy.risks` = the `.risk` of every action on every trigger (strategy.py `get_risks`; trg.py:489-490). In practice that means **every HedgeAction's risk appears as a column** even when `risks=` is not passed (040301 run 2, 040303, 040310). Because the risks go through `set(...)`, **column order is not guaranteed**. 040307 reads `iloc[5, 1]` positionally anyway, which is fragile.
- `result_ccy` re-parameterises every risk with `r(currency=result_ccy)` and raises for unparameterised risks (ge.py:796-812).
- The index is the union of the pricing dates and any cash-payment dates ≤ `states[-1]`. `strategy_pricing_dates` = the run schedule ∪ each trigger's `get_trigger_times()` within [start, end] (ge.py:771-789).
- **Identity requirement.** Notebooks index with a *freshly constructed* measure: `result_summary[FXDelta(aggregation_level=AggregationLevel.Type, currency='USD')]`. In gs, measures compare and hash by value, and `'Type' == AggregationLevel.Type` (verified: `IRDelta(aggregation_level='Type') == IRDelta(aggregation_level=AggregationLevel.Type)` is `True`, and the hashes are equal). `str()` renders `IRDelta(aggregation_level:Type)`, `FXDelta(aggregation_level:Type, currency:USD)`, `IRDelta(aggregation_level:Type, currency:local)`, and a bare `Price` renders as `Price`. The walkthrough output shows `FXDelta(aggregation_level:Type)` as the column header.
- Observed output (walkthrough cell 9, a single-risk run): the columns are `Price`, `Cumulative Cash`, `Transaction Costs`, `Total`. With a hedge risk (cell 14): `Price`, `FXDelta(aggregation_level:Type)`, `Cumulative Cash`, `Transaction Costs`, `Total`.

### 5.2 `BackTest.trade_ledger()` (bo.py:244-281)

- The index is the trade name; `pd.DataFrame(ledger).T.sort_index()`, so rows are **sorted by name**.
- The columns are exactly: `Open`, `Close`, `Open Value`, `Close Value`, `Long Short`, `Status`, `Trade PnL`.
- The first CashPayment seen for a name (iterating dates in sorted order) sets `Open=date`, `Close=None`, `Open Value=sum(cash_paid.values())`, `Close Value=0`, `Long Short=cash.direction`, `Status='open'`, `Trade PnL=None`. A later payment with non-empty `cash_paid` sets `Close`, `Close Value += ...`, `Trade PnL = Close Value + Open Value`, `Status='closed'`. A payment with `direction == 0` (netted) produces `Open=Close=date`, both values 0, `Status='closed'`, `Trade PnL=0`.
- `Long Short` is the **entry payment's direction**, which is always `-1` for entries (walkthrough output shows `-1` for every row). It is **not** the position sign.
- Open trades show `Close None`, `Close Value 0`, `Trade PnL None`. Held-forever trades (`trade_duration=None`) never close.

### 5.3 `BackTest.strategy_as_time_series()` (bo.py:283-321)

- Index: MultiIndex `('Pricing Date', 'Instrument Name')`.
- Column groups (level 0): `'Static Instrument Data'` (from `portfolio.to_frame()`, with the instrument fields as level 1, e.g. `number_of_options`), `'Risk Measures'` (level 1 = `str(risk)`), and `'Cash Payments'` (level 1 = `'Cash Ccy'`, `'Cash Amount'`; `CashPayment.to_frame`, bo.py:569-573).
- In v2, trades are opaque kwargs. The `Static Instrument Data` group must therefore be built from the trade-construction kwargs (and/or a named asset "static" expression). See §9.

### 5.4 `BackTest.portfolio_dict`

A `defaultdict(Portfolio)` keyed by date (bo.py:98, 116-122). 040308 reads `backtest.portfolio_dict[state].all_instruments` and `x.number_of_options`. The walkthrough prints the repr.

### 5.5 Trade naming (drives the ledger index and `ExitTradeAction(priceable_names=...)`)

- An action's `name` defaults to `'Action{N}'` from a **process-global counter** `action_count` (act.py:44, 90-94). The walkthrough output shows `Action1`, `Action2`, `Action6`.
- `AddTradeAction.__post_init__` (act.py:149-160) names each priceable: `None` becomes `f'{action.name}_Priceable{i}'`; a name that already starts with the action name is kept; any other name becomes `f'{action.name}_{p.name}'`.
- On each trade date the name becomes `f'{t.name}_{d}'`, where `d` is a `date`, so `str(d)` is `YYYY-MM-DD` (ge.py:119).
- Resulting names include `Action1_Priceable0_2025-08-04` and `add action_EURUSD call_2025-08-04` (from walkthrough output).
- HedgeAction: the walkthrough output shows `hedge action_spot hedge_2025-08-04`. **Caveat:** the installed ge.py:404-408 renames `portfolio.priceables[0]` (a `Portfolio` named after the priceable) to `f'{name}_{YYYY-MM-DD}'` and then prefixes each instrument name. It does not obviously produce that exact string. The output came from repo 2.1.17, so treat the observed format as the target and flag the difference.
- `ExitTradeAction(priceable_names=...)` parses names as `<Action>_<TradeName>_<YYYY-MM-DD>` (`split('_')[-2]` and `[-1]`, ge.py:475-489). **Names that contain `_`, such as `swap_10y`, break the `[-2]` match.**
- Initial portfolio items are renamed `f'{name}_{start:%Y-%m-%d}'` (ge.py:889-894).

### 5.6 `get_final_date` (bu.py `get_final_date`) — trade_duration semantics

| `trade_duration` | Exit date |
|---|---|
| `None` | `dt.date.max`: held forever, and the exit cash is never booked because it is > end |
| `date`/`datetime` | that date |
| a string naming an instrument attribute (`'expiration_date'`, `'termination_date'`) | `getattr(inst, duration)` on the **resolved** instrument |
| `'next schedule'` (case-insensitive) | `trigger_info.next_schedule or date.max`; raises if the action gets no info |
| `CustomDuration` | `function(*final dates of durations)` |
| tenor (`'1m'`, `'1b'`, `'1w'`) | `RelativeDate(duration, create_date).apply_rule(holiday_calendar=holiday_calendar)` (`gs_quant/datetime/relative_date.py:34,75`) |
| `pd.Timedelta` (040100, PAE only) | handled by PAE |

The schedule itself comes from `RelativeDateSchedule(rule, base_date, end_date).apply_rule(currencies=None, exchanges=None, holiday_calendar=None, week_mask='1111100')` (relative_date.py:197-234). With no calendar, `'1b'` means **every weekday**.

### 5.7 Engine flow facts that matter for porting (ge.py)

- `_build_simple_and_semi_triggers_and_actions(strategy, ...)` (ge.py:909-940) evaluates every **non-path-dependent** trigger once over all pricing dates, **in date order**, before any pricing. It then calls each non-path-dependent action with the list of triggered dates and the per-date trigger_info list. It is passed the **user's `strategy` object** (ge.py:823), not the deep copy in `backtest.strategy`, so state held on the trigger (for example `MeanReversionTriggerRequirements.current_position`) **persists across re-runs** of the same objects.
- Path-dependent triggers (`StrategyRiskTrigger`/`RiskTriggerRequirements`, `TradeCountTriggerRequirements`, an Aggregate that contains one) and path-dependent actions (`ExitAllPositionsAction` act.py:323, `RebalanceAction` act.py:428) run per date in `_process_triggers_and_actions_for_date` (ge.py:981-1061). Before applying each one, the engine makes sure that date's risks are computed. Any action on a path-dependent trigger is also applied per date there, whatever its own calc type (e.g. `ExitTradeAction` + `AddTradeAction` in 040305). `HedgeAction` is semi-path-dependent (act.py:371). Every other action, including `AddTradeAction`, `AddScaledTradeAction`, `EnterPositionQuantityScaledAction` and `ExitTradeAction`, keeps the default `CalcType.simple` (act.py:64). On a non-path-dependent trigger, those actions are applied upfront to the list of triggered dates. (The repo 2.1.17 may differ; I did not check it.)
- HedgeAction (semi-path-dependent):
  - The hedge instrument is resolved on the create date with `HistoricalPricingContext(dates=states, csa_term=self.action.csa_term)` (ge.py:382-385). **`csa_term` affects only resolution**, for example ATM strikes; the later risk calc on the hedge uses `HistoricalPricingContext(dates=p.dates)` with no csa (ge.py:956, 1004).
  - `scaling_factor = current_risk / hedge_risk * risk_percentage/100`, and the hedge is scaled by `scaling_factor * -1` (ge.py:1021-1044).
  - If `hedge_risk == 0` the hedge is skipped. If `current_risk.unit != hedge_risk.unit` it raises `RuntimeError('cannot hedge in a different currency')` (ge.py:1029-1030).
  - The hedge must be a Portfolio internally (ge.py:1056).
  - The exit payment is created only if `final_date <= dt.date.today()` (ge.py:429-433).
- `_resolve_initial_portfolio` (ge.py:875-907) **does book an entry CashPayment** (direction -1) on the start date for each initial instrument, plus an exit payment at `get_final_date(..., duration=None)` = `date.max`. So the ledger **does** include the initial portfolio. That contradicts the 040305 comment ("excluding starting portfolio"). **There is also a spurious extra row.** The exit payment is built with the **un-renamed** `initial_portfolio[index]` (ge.py:898), and `trade_ledger` iterates *all* cash-payment keys, including `date.max` (bo.py:250). So a second row keyed by the bare name (`'50y'` in 040310, `'swaption10y'` in 040305) appears with `Open = 9999-12-31`, `Open Value 0`, `Close None`, `Long Short 1`, `Status 'open'`, `Trade PnL None`. `ExitTradeAction` does not move it either, because it matches renamed names only (ge.py:510-513). A 1:1 port reproduces this row. Recommend listing it as a deliberate deviation if pricebt drops it.
- Cash (ge.py:1093-1175): each CashPayment's value = the price of the trade on `cp.effective_date` × `direction`. The value is taken from the day's results, or from an extra pricing on cash-only dates. `ccy = map_ccy_name_to_ccy(next(iter(value.unit)))`. Cash accumulates per ccy, with `initial_value` seeded in that ccy. An exit on a non-schedule date is still priced on that date.
- `AddTradeAction` sizes a trade by `Portfolio.scale(ti.scaling)` (ge.py:121). For IRSwap, `scale_in_place` does `notional_amount *= abs(s)`, flips `pay_or_receive` if `s < 0`, and sets `fee *= -1` (`gs_quant/target/instrument.py:2576-2593`). Because pricing is linear, this equals a **quantity multiplier** on every measure.
- `TriggerDirection`: `ABOVE=1`, `BELOW=2`, `EQUAL=3`. `check_barrier` uses strict `>` / `<` / `==` (trg.py:45-100). `AggType`: `ALL_OF=1`, `ANY_OF=2`.
- `PortfolioTriggerRequirements('len', ...)` tests `len(backtest.portfolio_dict)`, i.e. **the number of dates** (trg.py:328-329). `TradeCountTriggerRequirements` tests `len(backtest.portfolio_dict.get(state, []))`, the positions on that date (trg.py:387). Port both as they are.
- Risk-measure docstrings (`gs_quant/target/measures.py`):
  - `Price`: "Present Value".
  - `IRDelta`: "Change in Dollar Price (USD present value) due to individual 1bp moves in the interest rate instruments used to build the underlying discount curve". It accepts `(aggregation_level, bump_size, currency, finite_difference_method, local_curve, mkt_marking_options, scale_factor, name)`.
  - `InflationDelta`: "Change in Price due to 1bp move in inflation curve".
  - `FXDelta`: "dSpot * FXDelta = PnL".
  - So IRDelta is **ccy per +1bp**, which is the user's "in bps" requirement. Under this definition a payer swap has positive IRDelta.

---

## 6. Deep dive — 040304_strategy_mean_reversion.ipynb

### 6.1 Cell by cell

| Cell | gs content | What it needs | pricebt v2 version (USD 10y SOFR) |
|---|---|---|---|
| 0 | imports: `AddTradeAction`, `GenericDataSource`, `MissingDataStrategy`, `GenericEngine`, `Strategy`, `MeanReversionTrigger`, `MeanReversionTriggerRequirements`, `Currency`, `PayReceive`, `Dataset`, `IRSwap`, `Price`, plus `datetime`, `pandas` | module paths | The same class names from pricebt modules that **mirror `gs_quant.backtests.*`** (PROPOSED: `pricebt.backtests.actions`, `.data_sources`, `.generic_engine`, `.strategy`, `.triggers`; `Price` from a pricebt risk module). `Dataset`, `IRSwap`, `Currency` are not imported; the instrument comes from the asset config. `PayReceive` is only needed if the trade kwargs are enums, and those kwargs are opaque to pricebt. |
| 1 | `GsSession.use(..., scopes=('run_analytics','read_product_data'))` | auth | **Deleted.** There is no session. The asset config's `imports:` block loads the external library lazily when the market expression is first evaluated. |
| 2 | markdown "Buy 10y EUR payers" | – | "Buy 10y USD SOFR payers" |
| 3 | `start_date = date(2021, 6, 1)`; `end_date = datetime.today().date()` | – | The same. **Clamp `end_date`** to the last date the market source can serve (open question §9); gs silently prices today with live data. |
| 4 | `IRSwap(pay_or_receive=PayReceive.Pay, termination_date='10y', notional_currency=Currency.EUR, notional_amount=1e4, fixed_rate='ATM', name='swap_10y')` | resolution of `'ATM'` to the par rate on the trade date | A trade built from the `usd_sofr_ois_interest_rate_swap` asset config with the **same opaque kwargs**: `pay_or_receive='Pay', termination_date='10y', notional_amount=1e4, fixed_rate='ATM', name='swap_10y'`. `notional_currency` is implied by the asset (`currency: USD`). pricebt passes the kwargs straight to the config's trade expression and never interprets them. The trade expression must receive **the market at the trade date** so that it can resolve `'ATM'`. PROPOSED spelling: `swap = <asset>.instrument(**kwargs)` or `Instrument('usd_sofr_ois_interest_rate_swap', **kwargs)`; the design picks one. |
| 5 | markdown: Marquee dataset link | – | markdown: "par-rate series derived from the swap asset config" |
| 6 | `ds = Dataset('SWAPRATES_STANDARD'); data = ds.get_data(start_date, assetId=['MA5WM2QWRVMYKDK0']); data_10y = data.loc[data['tenor']=='10y']; data_10y.head()` | a history of the 10y swap rate, column `rate` (decimal), index = dataset dates | Replaced by an **asset-derived par-rate series** (§6.4): a `pd.Series` indexed by `dt.date`, holding the `par_rate` pricing function of the standard 10y trade evaluated on the asset's market at each date. `.head()` becomes `s.head()`. |
| 7 | `action = AddTradeAction(swap)`; `s = pd.Series(data_10y['rate'].to_dict())`; `data_source = GenericDataSource(s, MissingDataStrategy.fill_forward)`; `trig_req = MeanReversionTriggerRequirements(data_source, 2, 30, 30)`; `trigger = MeanReversionTrigger(trig_req, action)`; `strategy = Strategy(None, trigger)`; `GE.run_backtest(strategy, start=start_date, end=end_date, frequency='1b', show_progress=True)` | the trigger semantics in §6.2; pricing of each added swap on every later date (Price) | **Identical text** apart from where `s` comes from. |
| 8 | `backtest.trade_ledger()` | §5.2 | Identical. Expected shape in §6.5. |
| 9 | `backtest.result_summary` | §5.1 | Identical; the columns are `Price`, `Cumulative Cash`, `Transaction Costs`, `Total`, in USD. |
| 10 | `pd.DataFrame({'Generic backtester': result_summary['Cumulative Cash'] + result_summary[Price]}).plot(figsize=(10, 6), title='Performance')` | `Price` key identity | Identical. |
| 11 | empty | – | – |

### 6.2 Trigger semantics that must be reproduced

Per state `d`, evaluated once and in order over all pricing dates (`CalcType.simple`, ge.py:909-940):

```
rolling_mean = data_source.get_data_range(d, rolling_mean_window).mean()
rolling_std  = data_source.get_data_range(d, rolling_std_window).std()     # pandas, ddof=1
current      = data_source.get_data(d)
```

- `GenericDataSource.get_data_range(start, end:int)` = `data_set.loc[data_set.index < start].tail(end)`. That is **up to N points strictly before `d`**, counted from wherever the series starts (ds.py `get_data_range`). If fewer than 2 points exist, `std` is NaN, the comparison is False, and **nothing triggers**. The series in the notebook starts at `start_date`, so for the first ~30 business days the window is *shorter than 30*. A faithful port must start the par-rate series at `start_date` as well. **Prepending a 30-day lookback is a behavioural deviation** and must be an explicit, documented option if the design wants it.
- `GenericDataSource.get_data(d)` with `fill_forward`, when `d` is missing from the series (installed ds.py:140-155; the repo has the identical code at data_sources.py:145-148):
  - **`DatetimeIndex`**: it writes `NaN` at `d`, re-sorts (`self.data_set = self.data_set.sort_index()`), ffills, and returns the **previous** value. This is correct. The insertion is permanent, so later windows include the filled point.
  - **`dt.date` index (or any non-Datetime index)**: it does `self.data_set.at[d] = NaN`, which **appends at the end**. The next line, `self.data_set.sort_index()`, **discards its result**. `ffill()` then fills the appended NaN from the **last row of the whole series, which is a look-ahead**. Every later `get_data_range(...)` (`index < start` then `.tail(N)`) keeps positional order, so the appended out-of-order points are picked up **first**. I verified this against installed 1.5.4 with the series `{6/1: 1, 6/2: 2, 6/4: 4, 6/7: 7}`: `get_data(6/3)` returns **7.0** (the DatetimeIndex version returns 2.0), and `get_data_range(6/8, 3)` returns `{6/4: 4, 6/7: 7, 6/3: 7}`.
  - The window for `d` is computed *before* the insertion for `d`.
- **Index type.** `Dataset.get_data` usually returns a `DatetimeIndex`. Under the installed pandas 2.3.1, `DatetimeIndex < datetime.date` raises `TypeError: Invalid comparison between dtype=datetime64[ns] and date` inside `get_data_range` (I checked this with a synthetic series). **Consequence: with installed 1.5.4 + pandas 2.3.1 there is no index type for which a gappy series gives correct mean-reversion behaviour.** A DatetimeIndex crashes; a date index gives look-ahead fills. The only clean case is a series with **no gaps on schedule dates**, where no insertion ever happens.
- **Requirement for v2 (1:1 route):** build the asset-derived series on the **full `'1b'` weekday schedule**, forward-filling chronologically at construction (§6.4 step 6), with a `dt.date` index. Then `GenericDataSource` never inserts and gs semantics hold exactly. Separately, the pricebt v2 `GenericDataSource` should (a) normalise daily indices to `dt.date` and (b) insert-sort-ffill correctly. That is a documented deviation from gs that only matters for user-supplied gappy series.

State machine: installed 1.5.4 (trg.py:354-376) versus repo 2.1.17 (repo trg.py:354-376):

| `current_position` | Condition | Installed 1.5.4 | Repo 2.1.17 |
|---|---|---|---|
| 0 | `abs((cur-mean)/std) > z` and `cur > mean` | set -1; fire `AddTradeActionInfo(scaling=-1)` (the notebook comment calls this "Sell") | same, with `next_schedule=None` |
| 0 | same and `cur <= mean` | set +1; fire `scaling=+1` ("Buy") | same |
| +1 | `cur > mean` | fire `scaling=-1`, **but sets `self._current_position = 0` (typo, line 368)**, so the position stays +1 and it **fires again on every later date with `cur > mean`** | sets `current_position = 0`, fires once |
| -1 | installed: `cur > mean` (line 371); repo: `cur < mean` | installed: fires `scaling=+1` and resets when price is *still above* the mean, which is usually the very next date, so the short lasts about 1 day | repo: exits when the price crosses back **below** the mean, which is the correct mean-reversion exit |
| other | – | `raise RuntimeWarning` | same |

`current_position` is a **class attribute** (no annotation, so it is not a dataclass field). The first assignment creates an instance attribute. Together with ge.py:823 this means **state carries over when a strategy object is re-run**.

**Recommendation (design decision):** implement the **repo 2.1.17 semantics** and list them as a deliberate deviation from installed 1.5.4, citing the two line numbers above. Decide explicitly whether `current_position` resets at the start of each `run_backtest`. 1:1 behaviour is not to reset; the safer choice is to reset. Record whichever is chosen.

Effect on the book:
- "Exit" is **not** a close. It is a second `AddTradeAction` with the opposite scaling and `trade_duration=None`. Both trades stay **open forever**: `Close None`, `Trade PnL None`, and no exit cash, because the exit date is `date.max` > end. The net PV of the two offsetting ATM swaps, struck at different dates, is the locked-in P&L (≈ notional × annuity × Δpar), and it keeps being marked to market.
- `scaling=-1` on a Pay swap is a Receive swap with the same notional (§5.7). In v2 it should be a **quantity of -1** on the position, not an edit to the kwargs.
- `fixed_rate='ATM'` is resolved on the trade date (`get_base_orders_for_states` prices `ResolvedInstrumentValues` inside `PricingContext(pricing_date=s)`, ge.py:87-95). So the entry PV ≈ 0 and `Open Value` ≈ 0.

### 6.3 How the v2 notebook would read (names marked PROPOSED are placeholders for the design doc)

```python
from datetime import date, datetime
import pandas as pd

from pricebt.backtests.actions import AddTradeAction                      # PROPOSED path, gs-identical class
from pricebt.backtests.data_sources import GenericDataSource, MissingDataStrategy
from pricebt.backtests.generic_engine import GenericEngine
from pricebt.backtests.strategy import Strategy
from pricebt.backtests.triggers import MeanReversionTrigger, MeanReversionTriggerRequirements
from pricebt.risk import Price                                            # PROPOSED path

start_date = date(2021, 6, 1)
end_date = datetime.today().date()          # clamp policy: see open questions

usd_irs = <load asset 'usd_sofr_ois_interest_rate_swap'>                 # PROPOSED: one YAML file
swap = <usd_irs trade>(pay_or_receive='Pay', termination_date='10y',     # opaque kwargs, passed through
                       notional_amount=1e4, fixed_rate='ATM', name='swap_10y')

s = <par-rate series of usd_irs>(trade_kwargs=dict(termination_date='10y'),   # PROPOSED helper, see 6.4
                                  start=start_date, end=end_date, frequency='1b')

action = AddTradeAction(swap)
data_source = GenericDataSource(s, MissingDataStrategy.fill_forward)
trig_req = MeanReversionTriggerRequirements(data_source, 2, 30, 30)
trigger = MeanReversionTrigger(trig_req, action)
strategy = Strategy(None, trigger)

GE = GenericEngine()
backtest = GE.run_backtest(strategy, start=start_date, end=end_date, frequency='1b', show_progress=True)
backtest.trade_ledger()
backtest.result_summary
pd.DataFrame({'Generic backtester': backtest.result_summary['Cumulative Cash'] + backtest.result_summary[Price]}).plot(
    figsize=(10, 6), title='Performance')
```

Everything from `action = ...` onwards is **byte-identical** to the gs notebook. That is the 1:1 acceptance test.

### 6.4 Replacing `Dataset` with an asset-derived par-rate series (description, not library calls)

For each date `d` in `RelativeDateSchedule('1b', start_date, end_date)` (weekdays):

1. Set the injected variable `pricebt_timestamp` for `d`. How a date maps to an EOD timestamp is an open question.
2. Evaluate the asset config's `market` expression. For USD this is the user-given string `'IRSwapsMDP(source="ERIS_EOD_LIVE-RL_BASIC-NOJUMPS").get_pricer(request=dict(curve_name="USD-SOFR-1D", timestamp=pricebt_timestamp))'`, evaluated only after the config's own `imports:` lines. pricebt core imports nothing.
3. Evaluate the config's trade-construction expression with the kwargs `termination_date='10y'` plus any defaults (spot-starting, notional 1), and the market from step 2 (needed for any `'ATM'` resolution).
4. Evaluate the config's named pricing function `par_rate` on `(market, trade)` to get a float.
5. Collect the results into a `pd.Series` indexed by **`dt.date`**, in the **decimal units the function returns**. The z-score is scale-invariant, so decimal, percent or bp all give identical triggers. `MktTrigger` levels (040306-style) are not scale-invariant, so the design must fix the unit of `par_rate` in the asset contract.
6. Missing market on `d` (a source holiday or error): **forward-fill at construction** from the previous available date, in chronological order, so that the series covers every schedule date. Do **not** omit the point. If it were omitted, `GenericDataSource.get_data` would take its insertion path, which looks ahead on a `dt.date` index (§6.2). Where the series starts with a gap, drop the leading dates rather than back-fill. Log how many dates were filled.

The series is independent of the backtest and can be cached per `(asset, trade_kwargs, date)`. The engine's own pricing on `d` evaluates the same market expression, so one market object per date should be shared between the series and the engine.

### 6.5 Expected v2 outputs (shape, not numbers)

- `trade_ledger()`: one row per trigger event, index `Action{N}_swap_10y_{YYYY-MM-DD}` (N from the global counter, `Action1` in a fresh kernel). `Open` = the event date, `Close None`, `Open Value` ≈ 0 (the ATM entry, times the scaling), `Close Value 0`, `Long Short -1`, `Status 'open'`, `Trade PnL None`. Rows are sorted by name. The 2021 dates appear in chronological order because the date is the only varying suffix.
- `result_summary`: columns `Price` (the net PV of all accumulated swaps, in USD), `Cumulative Cash` (≈ 0: the sum of -PV at entry for ATM trades), `Transaction Costs` (0), `Total`. The index is weekdays from the first date that has any position or cash. Before the first trigger there are no results and no cash, so those dates are absent: `get_risk_summary_df` only includes dates with results, and `_cash_dict` starts at the first payment.
- Performance ≈ `Price`.

---

## 7. Shorter deep dives

### 7.1 040310_initial_swap_hedging_strategy_varying_CSA

| Cell | gs | v2 |
|---|---|---|
| 0-1 | imports `HedgeAction`, `GenericEngine`, `Strategy`, `PeriodicTrigger(Requirements)`, `IRSwap`, `IRDelta`, `Price`; GsSession | the same class names from pricebt; no session |
| 2 | markdown "Inflation strategy hedging a 50y inflation swap with a 30y…" (**copy-paste error** from 040309) | fix the title: "50y EUR payer hedged daily with 30y receivers under a different CSA" |
| 3 | `start_date = date(2024, 1, 3)`, `end_date = today` | same (clamp) |
| 4 | `swap_hedge = IRSwap('Receive', '30y', 'EUR', 100e6, fixed_rate='ATM', name='30yhedge')`; `swap = IRSwap('Pay', '50y', 'EUR', 100e6, fixed_rate='ATM', name='50y')` | two trades from a **EUR swap asset config** (curve and source are an open question), with the same kwargs |
| 5 | `HedgeAction(IRDelta(aggregation_level='Type'), swap_hedge, csa_term='EUR-OIS')` on Periodic `'1b'`; `Strategy([swap], triggers)`; `run_backtest(... '1b')` | identical text; `csa_term` must be routed somewhere (below) |
| 6-9 | ledger, summary, `result_summary[Price]` plot, Performance plot | identical |

Semantics to reproduce:
- The initial 50y swap is renamed `50y_2024-01-03`, resolved on the start date, and **booked in cash** (entry direction -1, exit at `date.max`).
- Each business day a new hedge swap is resolved **under `csa_term='EUR-OIS'`**, so its ATM rate uses OIS discounting. It is held forever (no `trade_duration`) and scaled so that the **whole book's** `IRDelta(aggregation_level='Type')` after the hedge is 0. The book includes all earlier hedges, so the hedges are incremental.
- The hedge risk has no `currency`. The sources disagree on what that means. The `IRDelta` docstring says "Change in Dollar Price (USD present value)", while the 040303 comment says "The results will be in local ccy, EUR in this case". The design must fix the default currency of an unparameterised risk. My recommendation is the asset's own currency, which matches the notebook comment.
- The later risk and price evaluation of the hedge does **not** use `EUR-OIS` (ge.py:956/1004). Only the strike does. So each hedge has a non-zero `Open Value` under the engine's pricing CSA, and that is the point of the notebook.
- `result_summary` columns: `Price`, `IRDelta(aggregation_level:Type)` (≈ 0 every day, auto-added from `strategy.risks`), `Cumulative Cash`, `Transaction Costs`, `Total`, all in EUR.
- Ledger: `50y_2024-01-03`, plus the spurious `50y` row with `Open 9999-12-31` (§5.7), plus one `Action{N}_30yhedge_{date}`-style row per day, all `open` (subject to the §5.5 hedge-naming caveat).

v2 mapping: in v2 "CSA" means *which discount curve/market is used to strike the hedge*. pricebt must not interpret the string. Options for the design:
- (a) Forward `csa_term` as an injected variable (e.g. `pricebt_csa_term`) into the asset's market and/or trade expressions, with the asset deciding what `'EUR-OIS'` means.
- (b) Express "a different CSA" as a different asset config for the hedge.

Either way, run_backtest's own `csa_term` (engine default) and HedgeAction's `csa_term` (resolution only) must stay distinct, as in gs.

### 7.2 040303_strategy_delta_hedge_Rates

- As written it needs **a swaption asset**: `IRSwaption(expiration_date='1m', termination_date='30y', notional_currency=EUR, buy_sell='Sell')` held until `'expiration_date'`. That attribute lookup on the resolved trade (`getattr`, §5.6) must come from a named date expression in the asset config.
- The hedge is `IRSwap(termination_date='30y', notional_currency=EUR, notional_amount=100e6, name='30yhedge')`. It has no `pay_or_receive` and no fixed rate, so it relies on gs resolution defaults. The v2 EUR asset must define defaults for omitted kwargs, or the notebook must spell them out.
- `HedgeAction(IRDelta(aggregation_level='Type', currency=Currency.USD), swap_hedge)`: monthly, **held forever**, so hedges accumulate.
- Periodic `'1m'` with actions `[add, hedge]` in that order.
- `result_summary` columns: `Price` (EUR, "local ccy", per the notebook comment), `IRDelta(aggregation_level:Type, currency:USD)` (USD), `Cumulative Cash` (EUR), `Transaction Costs`, `Total`. **The frame mixes currencies across columns.** v2 must support a per-risk `currency` parameter, with conversion via the asset/FX expression. The hedge ratio is unchanged because the portfolio and hedge risks are converted at the same FX rate.
- Cell 8 adds a `'Performance'` column to the returned frame and plots every column.
- Swap-only variant (runnable now): replace the swaption with `AddTradeAction(<EUR 10y payer ATM>, '1m')` and keep the hedge and the `currency=USD` risk. This exercises multi-currency risk conversion.

### 7.3 040300_strategy_periodic_trigger

- As written it needs an FX option asset: `FXOption(Buy, Call, 'USDJPY', strike_price='ATMF', expiration_date='2y', premium=0)`.
- Swap variant: `AddTradeAction(<USD 10y SOFR payer, fixed_rate='ATM', name='10y'>, '1m')` on `PeriodicTriggerRequirements(start_date=date(2021,6,1), end_date=today, frequency='1m')`, run with `frequency='1b', show_progress=True`.
- Per trade: entry on the monthly trigger date, and exit at `RelativeDate('1m', entry).apply_rule()`. The exit date may fall outside the `'1b'` schedule, in which case it is priced as a cash-only date and appears in the `result_summary` index.
- Ledger rows `Action{N}_10y_{date}`: `Open Value` ≈ 0 (ATM), `Close Value` = PV at exit, `Trade PnL = Close + Open`, `Status 'closed'`. The last trade is open.
- `result_summary[Price]` = the PV of the live swap (starting near 0 each month). Performance = `Cumulative Cash + Price` is a step-plus-drift curve.
- Unlike the option case (premium paid in cash), `Cumulative Cash` here is almost entirely realised exits.

---

## 8. Notebook defects (do not "fix" these by changing pricebt semantics)

| NB | Defect |
|---|---|
| 040205, 040308, 040312 | Use `PricingContext(...)` without importing it. Star imports from installed 1.5.4 do not export it (checked), and the repo `backtests` modules don't import it at top level either, so this is a latent `NameError`. |
| Walkthrough cell 29 | Uses `initial_hedge_action` but defines `weekly_hedge_action`, so `NameError`. |
| 040310 | The markdown title is copy-pasted from 040309 (inflation). |
| 040314 | `scopes=('run_analytics')` is a str, not a tuple. |
| 040313 | Uses `dt.date` with `dt` arriving only through `from gs_quant.backtests.equity_vol_engine import *`. |
| 040305 | The comment "trade ledger ... excluding starting portfolio" is wrong; the initial portfolio is booked (§5.7). |
| 040307 | `backtest_summary.iloc[5, 1]` depends on set-ordered column position. |
| 040311 | Passes a tuple of dates to `holiday_calendar`, which the installed signature annotates as `Optional[str]`. It works because the value goes to `apply_rule(holiday_calendar=List[date])`. |
| skills `backtesting.md` | Documents `summary_stats()`, which is absent in installed 1.5.4 (repo `bo.py:393` only). |
| installed trg.py:368, :371 | MeanReversion typo and wrong exit direction (§6.2); fixed in the repo. |
| installed ds.py:140-155 (repo :145-148, same code) | `GenericDataSource.get_data` fill_forward on a non-Datetime index appends the NaN out of order, discards the `sort_index()` result, and ffills from the last row, which is a look-ahead (§6.2). **Not fixed in the repo.** |
| installed ge.py:898 + bo.py:250 | The initial portfolio produces a spurious ledger row keyed by the un-renamed name with `Open 9999-12-31` (§5.7). |

---

## 9. Items pricebt core cannot do generically (inputs for the design doc)

These are listed in `open_questions` as well. Each one is a place where gs reads instrument *fields*, which v2 treats as opaque kwargs:

1. **Scaling.** gs scales by editing fields (`notional_amount`, `pay_or_receive`, `fee`). I recommend a pricebt-level **quantity multiplier** on positions for `AddTradeActionInfo.scaling`, HedgeAction and AddScaled. Measures are then `quantity × f(market, trade)`. This is exact for linear products; confirm it is acceptable for swaptions later.
2. **`RebalanceAction(priceable, size_parameter='number_of_options', method)`** sets a field on a clone. v2 needs either quantity semantics (ignore `size_parameter`, or map it) or a declared "size kwarg" in the asset config.
3. **`ScaledTransactionModel('notional_amount', level)`** reads an instrument attribute. v2 needs the asset to expose named attributes (kwarg lookup or expression). `ScaledTransactionModel(Price, ...)` maps to a named pricing function.
4. **`trade_duration='expiration_date'` / `'termination_date'`** use `getattr` on the resolved instrument. v2 needs named date attributes from the asset config (the resolved trade's dates), which swaptions will need too.
5. **`HedgeAction(csa_term=...)`** affects only hedge resolution; run_backtest's `csa_term` affects pricing. How does each reach the market/trade expressions (an injected variable versus a separate asset)?
6. **`Static Instrument Data`** in `strategy_as_time_series` comes from `portfolio.to_frame()`. In v2, which fields: the trade kwargs, or a declared `static` expression?
7. **Currency.** `result_summary` raises if cash exists in >1 ccy unless `result_ccy` is set. v2 multi-ccy must choose between replicating the raise or auto-converting. `result_ccy` re-parameterises every risk (`r(currency=...)`), and a per-risk `currency=` parameter mixes currencies across columns (040303). The FX expression contract is needed for both.
8. **Hedge unit check.** gs raises `'cannot hedge in a different currency'` when the units differ. v2 should compare risk units after conversion to the risk's `currency` parameter.
9. **`'ATM'` resolution** needs the market at the trade date inside the trade-construction expression (injected market variable).
10. **Market availability.** What `pricebt_timestamp` is for a date (EOD time and timezone); how weekdays with no source data are treated in the `'1b'` schedule (gs prices every weekday); and the `end=today` clamp.
11. **MeanReversion.** Adopt the repo 2.1.17 fixes? Reset `current_position` per run, or persist it (gs persists because ge.py:823 passes the user's strategy)?
12. **Series start.** The asset-derived par-rate series starts at `start_date` for 1:1 behaviour. Should an optional lookback exist?
13. **Trade naming.** Replicate the process-global `Action{N}` counter and the `_`-split parsing in `ExitTradeAction(priceable_names)`, which breaks on names containing `_`? Also the hedge-name difference between installed and repo (§5.5).
14. **Units of `par_rate`** (decimal, percent or bp) in the asset contract. The mean-reversion trigger does not care, but `MktTrigger` levels do.
15. **EUR (and other currencies') curve source** for 040310 and the 040303 variant. The user only named the USD SOFR ERIS source.
16. **`GenericDataSource` fill_forward on a date index** looks ahead (§6.2). Either replicate it (1:1) or fix it (insert, sort, ffill chronologically) as a documented deviation. Either way, build the asset-derived series on the full schedule so the insertion path is never hit.
17. **Spurious initial-portfolio ledger row** (`Open 9999-12-31`, §5.7): replicate or drop?
18. **Default currency of an unparameterised risk** (`IRDelta(aggregation_level='Type')`): the docstring says USD PV, the 040303 comment says local ccy.
