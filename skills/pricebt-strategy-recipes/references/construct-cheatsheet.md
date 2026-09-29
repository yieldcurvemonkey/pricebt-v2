# pricebt.backtests construct cheatsheet

Every trigger, action and friction model in `src/pricebt/backtests`, with its constructor signature (as in
the source) and one line of semantics. The API is gs_quant 2.1.17's; pricebt deviations are tagged with
their ID from `docs/v2/DEVIATIONS.md`.

**Calc type** decides *when* the engine evaluates a trigger or applies an action:

- `simple`: evaluated for every grid date up front, before any pricing; cannot see results.
- `semi_path_dependent`: placed up front, sized during the date loop (HedgeAction, AddWeightedTradeAction).
- `path_dependent`: evaluated date by date in the loop, after that date's book is priced (DEV-E1).

A trigger's calc type is its requirement's (Aggregate/Not: the most path-dependent child, DEV-T5). A
path-dependent action under a simple trigger is applied date by date on the dates the trigger fired.

**Info passed to actions.** A trigger returns `TriggerInfo(triggered, info_dict)`, where `info_dict` maps
an action *type* to its info. `next_schedule` feeds `trade_duration='next schedule'`; `scaling` multiplies
an `AddTradeAction`'s trade. The engine hands an action the info keyed by its exact type, else by an
`isinstance` match.

## Triggers (`pricebt.backtests.triggers`)

Each row is `XRequirements(...)` wrapped as `X(trigger_requirements, actions)`; `actions` is one action or
a list.

| Trigger / requirements (signature) | Calc type | Fires when | Info to actions |
|---|---|---|---|
| `PeriodicTrigger` / `PeriodicTriggerRequirements(start_date=None, end_date=None, frequency=None, calendar=None)` | simple | `d` is on `RelativeDateSchedule(frequency, start_date or backtest start (DEV-T4), end_date)`, weekends and `calendar` skipped | `next_schedule` = next schedule date (None after the last) for AddTrade / AddScaledTrade / Hedge |
| `DateTrigger` / `DateTriggerRequirements(dates, entire_day=False)` | simple | `d` in `dates` (`entire_day`: compare datetimes by date) | `next_schedule` = next listed date |
| `MktTrigger` / `MktTriggerRequirements(data_source=None, trigger_level=None, direction=None)` | simple | `data_source.get_data(d)` is `>` / `<` / `==` `trigger_level` (strict) | none |
| `MeanReversionTrigger` / `MeanReversionTriggerRequirements(data_source=None, z_score_bound=None, rolling_mean_window=None, rolling_std_window=None)` | simple, stateful | flat and `|z| > z_score_bound` (enter, scaling −1 above the mean, +1 below); long and value > mean, or short and value < mean (exit = opposite scaling). Window: the N points strictly before `d`, `ddof=1` | `AddTradeAction`: `scaling=±1, next_schedule=None` |
| `StrategyRiskTrigger` / `RiskTriggerRequirements(risk=None, trigger_level=None, direction=None, risk_transformation=None)` | path-dependent | the book's aggregated `risk` on `d` (optionally transformed) crosses the level; `risk` is added to the run's risks automatically | none |
| `AggregateTrigger` / `AggregateTriggerRequirements(triggers=None, aggregate_type=AggType.ALL_OF)` | max of children | `ALL_OF`: every child fires (short-circuits in list order); `ANY_OF`: any child (all evaluated). `triggers` takes requirements or Triggers (unwrapped); `None` raises `ValueError` (DEV-T8) | the children's `info_dict`s merged in order (a later child overrides); trigger times are the union (DEV-T7) |
| `NotTrigger` / `NotTriggerRequirements(trigger=None)` | the child's (DEV-T5) | the child does not fire | none |
| `TradeCountTrigger` / `TradeCountTriggerRequirements(trade_count=None, direction=None)` | path-dependent | `len(backtest.portfolio_dict[d])` (top-level entries of that day's book) vs `trade_count` | none |
| `PortfolioTrigger` / `PortfolioTriggerRequirements(data_source=None, trigger_level=None, direction=None)` | simple | only `data_source='len'`: `len(backtest.portfolio_dict)` (the number of *dates* with a book so far, gs parity) vs the level; evaluated up front, so it sees only trades placed by earlier triggers | none |
| `EventTrigger` / `EventTriggerRequirements(event_name=None, offset_days=0, country=None, currency=None, source=None, start=None, end=None, data_source=None)` | simple | `d` in the index dates of `data_source.get_data(None, eventName=..., ...)` + `offset_days`; `data_source` is required (DEV-T12) and must accept those keywords (a `GenericDataSource` does not) | `next_schedule` = next event date |
| `IntradayPeriodicTrigger` / `IntradayTriggerRequirements(start_time=None, end_time=None, frequency=None)` | simple | `state.time()` is on the intraday schedule (minutes); needs datetime states, so not usable on a daily grid | none |
| `OrdersGeneratorTrigger` | — | PredefinedAssetEngine only, which is a stub in pricebt: not usable with GenericEngine | — |

Enums: `TriggerDirection.ABOVE | BELOW | EQUAL`, `AggType.ALL_OF | ANY_OF`. Every stateful requirement is
`reset()` at the start of each run (DEV-T10), so a Strategy object can be run twice.

## Actions (`pricebt.backtests.actions`)

Common to the trade-adding actions: `trade_duration` is `None` (held to the end), a tenor (`'1m'`), a date,
a `timedelta` (DEV-T1), an instrument attribute name (`'termination_date'`; `'expiration_date'` for a
swaption, read from the resolved trade, so the asset config must map it under `attributes:`), `'next schedule'`, or a
`CustomDuration(durations, function)` (final date = `function(*final dates)`, e.g. `min`).
`transaction_cost_exit=None` means "same as `transaction_cost`". Positions are named
`<action name>_<instrument name>_<date>`; unnamed actions get a global `Action{N}` name.

| Action (signature) | Calc type | What it does |
|---|---|---|
| `AddTradeAction(priceables=None, trade_duration=None, name=None, transaction_cost=ConstantTransactionModel(0), transaction_cost_exit=None, holiday_calendar=None)` | simple | Resolves each priceable on the trigger date, scales by the trigger's `scaling` (if any), holds until the final date. |
| `AddScaledTradeAction(priceables=None, trade_duration=None, name=None, scaling_type=ScalingActionType.size, scaling_risk=None, scaling_level=1, transaction_cost=..., transaction_cost_exit=None, holiday_calendar=None, dated_priceables=None)` | simple | As AddTradeAction, then scales the whole order: `size` ×level; `risk_measure` ×(level / order's aggregated `scaling_risk`) — the level carries the sign; `NAV` spends `scaling_level` cash (reinvesting unwind proceeds into later orders). `scaling_level` may be a `{date: level}` dict (forward-filled, 0 before the first). `dated_priceables={date: [insts]}` overrides the priceables on those dates. Reads `next_schedule` but not `scaling`. |
| `AddWeightedTradeAction(priceables=None, trade_duration=None, name=None, scaling_risk=None, total_size=100000.0, transaction_cost=..., transaction_cost_exit=None, holiday_calendar=None)` | semi-path-dependent | Splits `total_size` (in the asset's `size_attribute` units) across the portfolio's instruments in proportion to each one's `|scaling_risk|` on the date. |
| `HedgeAction(risk=None, priceables=None, trade_duration=None, name=None, csa_term=None, scaling_parameter='notional_amount', transaction_cost=..., transaction_cost_exit=None, risk_transformation=None, holiday_calendar=None, risk_percentage=100)` | semi-path-dependent | Adds `−risk_percentage/100 × book risk / hedge unit risk` of the hedge (one instrument or a Portfolio), resolved under `csa_term`. All hedges on a date are sized against the same pre-hedge book risk. Skipped (and its costs removed, DEV-E3) when the hedge's risk is 0. `scaling_parameter` is deprecated. |
| `ExitTradeAction(priceable_names=None, name=None, transaction_cost=...)` | simple | Exits, at the trigger date's close, every held position (`None`) or those created from the named priceables (matched on `position_meta`, DEV-E5); hedges are never matched by name. |
| `ExitAllPositionsAction(priceable_names=None, name=None, transaction_cost=...)` | path-dependent | Exits every held position on the date, hedges included. |
| `RebalanceAction(priceable=None, size_parameter=None, method=None, transaction_cost=..., transaction_cost_exit=None, name=None)` | path-dependent | `method(state, backtest, info)` returns the target size; trades the difference from the current size. `priceable` must be resolved; `size_parameter` must equal the asset's `size_attribute`. |
| `EarlyExitPositionLimitScaledAction(... AddScaledTradeAction fields ..., early_exits=None, max_concurrent_pos=None)` | simple | AddScaledTradeAction that exits at the first `early_exits` date after entry, and skips a whole order date when it would exceed `max_concurrent_pos` open positions. |
| `EnterPositionQuantityScaledAction`, `ExitPositionAction` | — | API parity only: GenericEngine has no handler for them (as in gs). |

`ScalingActionType.size | risk_measure | NAV`.

## Frictions and financing (`pricebt.backtests.backtest_objects`)

| Model (signature) | Cost / accrual |
|---|---|
| `ConstantTransactionModel(cost=0)` | `cost` per trade, per side |
| `ScaledTransactionModel(scaling_type='notional_amount', scaling_level=0.0001)` | `scaling_level × |attribute or measure × quantity|`: a string reads an instrument attribute (the asset config's `attributes`; gs `Bond` has no `notional_amount` field, so a bond config must map one), a risk measure prices it on the trade date (converted to `result_ccy`, DEV-E18), e.g. `IRVegaParallel` for an option cost in bp of vega |
| `AggregateTransactionModel(transaction_models=(), aggregate_type=TransactionAggType.SUM)` | sum / max / min of the component costs |
| `ConstantCashAccrualModel(rate=0, annual=True)` | cash grows `(1 + rate/365)^days` between cash dates; `Strategy(cash_accrual=...)` |
| `DataCashAccrualModel(data_source=None, annual=True)` | as above with the rate read from `data_source` on each accrual start date |

Costs appear (negative) in `result_summary["Transaction Costs"]`; `Total = price + Cumulative Cash + Transaction Costs`.

## Strategy and engine

- `Strategy(initial_portfolio=None, triggers=None, cash_accrual=None)`: `initial_portfolio` (list, or
  `{date: [insts]}`) is entered at the start without transaction costs; `strategy.risks` collects every
  hedge / risk-trigger measure.
- `GenericEngine(action_impl_map=None, price_measure=Price).run_backtest(strategy, start=None, end=None, frequency='1m', states=None, risks=None, show_progress=True, csa_term=None, visible_to_gs=False, initial_value=0, result_ccy=None, holiday_calendar=None, market_data_location=None, is_batch=True, calc_risk_at_trade_exits=False, pnl_explain=None)`:
  the grid is `frequency` dates from `start` to `end` (or `states`) plus every trigger date in range; the
  engine always adds its `price_measure` to `risks`. `pnl_explain` takes a `PnlDefinition`
  (`ir_pnl_definition`, `swaption_pnl_definition`, `bond_pnl_definition` or `fx_pnl_definition` from
  `pricebt.backtests.backtest_objects`); its measures are added to `risks`, and `BackTest.pnl_explain_table()`
  then gives the per-step actual / cashflow / attributed / residual P&L.
- Signal data: `GenericDataSource(data_set=None, missing_data_strategy=MissingDataStrategy.fail)` over a pandas
  Series (`fill_forward` never looks ahead, DEV-T13); build the Series with
  `pricebt.data.measure_series(instrument, measure, start, end, frequency='1b')`.
