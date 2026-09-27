# pricebt Backtesting Guide (gs_quant-compatible)

This is gs_quant's backtesting guide (`gs_quant/skills/gs-quant-overview/backtesting.md`) rewritten for pricebt. pricebt ships a
gs_quant-compatible facade (`pricebt.backtests`, `pricebt.risk`, `pricebt.common`, `pricebt.instrument`, `pricebt.session`) over its own
event-driven engine, with **no data infrastructure and no pricing library in the core**. A gs_quant backtest ports with three changes:

1. **Imports**: replace the import root `gs_quant` with `pricebt` (`from pricebt.backtests.triggers import PeriodicTrigger`).
2. **Market and stack instead of `GsSession.use()`**: supply a market (any provider that emits snapshots) and the pricing **stack** once:
   `PricebtSession.use(market=<your MarketDataProvider>, stack="pricebt.contrib.rateslib:STACK")` (or `pricebt.contrib.quantlib:STACK`).
   The stack is the adapter that turns the market's snapshots into a library's objects and ships the instrument specs.
3. **Instruments**: build them with `pricebt.instrument` (`IRSwap`) or pass your own trade templates. `IRSwap` builds **terms** only; the spec
   registered for (asset class, currency) resolves them (par strikes, dates) on the market you supplied, at trade time, with the library's
   own conventions.

Every snippet below is executed by the test suite (`tests/test_gs_compat_guide.py` on the toy market, `tests/test_gs_compat_rates.py` on
the rateslib stack + `SyntheticMarket`), with independent checks of the outcome. The neutral architecture behind the facade (schemas,
bindings, snapshots, stacks, the tie-out of one backtest across libraries) is in `docs/DESIGN.md` and `docs/design/adr/`.

## What changed vs gs_quant

| topic | gs_quant | pricebt |
|---|---|---|
| imports | `gs_quant.backtests.*`, `gs_quant.risk`, `gs_quant.common`, `gs_quant.instrument` | `pricebt.backtests.*`, `pricebt.risk`, `pricebt.common`, `pricebt.instrument` (same module and class names) |
| session | `GsSession.use(client_id, client_secret, ...)` | `PricebtSession.use(market=..., stack=..., calendar=..., tz=...)`; `GsSession.use()` without `market=` raises `NotSupportedError` |
| market data | GS servers | whatever `MarketDataProvider` you pass: `get_pricer(ts, request) -> Pricer` whose pricers wrap plain snapshots (`pricebt.testing.synthetic.SyntheticMarket`, your own; the fixture readers are test support) |
| instruments | resolved and priced by GS | `pricebt.instrument.IRSwap` builds terms; the spec the stack registers resolves them at trade time on your market with a pricing library of your choice; any other product is your own trade template |
| risk measures | GS risk service | `RiskMeasure` is a `str` equal to a pricebt measure name (`Price == 'pv'`, `IRDelta(aggregation_level=Type) == 'dv01'`); see the table in section 7 |
| engines | `GenericEngine`, `EquityVolEngine`, `PredefinedAssetEngine` | `GenericEngine` only; the other two are stubs raising `NotSupportedError` |
| evaluation order | trigger-major batches | date-major: every trigger once per point in list order; actions by phase EXIT < ADD < ADJUST < HEDGE |
| result | `BackTest` | `Backtest` (alias `BackTest`) with gs's `result_summary`, `trade_ledger()`, `summary_stats()`; the full pricebt result is `backtest.result` |

**Not supported** (clear `NotSupportedError` with what to use instead): `EquityVolEngine`, `PredefinedAssetEngine`,
`EnterPositionQuantityScaledAction`, `GsDataSource`, `OisFixingCashAccrualModel`, FX / equity / credit / inflation instruments
(`FXForward`, `FXOption`, `FXBinary`, `EqOption`, `EqVarianceSwap`, `IRCap`, `IRXccySwap`, ...), non-USD currencies in the shipped
instruments, `run_backtest(calc_risk_at_trade_exits=True)`, `run_backtest(pnl_explain=...)`, `GenericEngine(action_impl_map=...)`,
swaptions and volatility (`IRSwaption` raises `NotSupportedError`).

---

## 1. Architecture Overview

| Concept | Description |
|---|---|
| **Strategy** | Combines an optional initial portfolio with one or more `Trigger` objects. |
| **Trigger** | Defines *when* to act: on a schedule, risk threshold, market data level, etc. Each trigger holds one or more `Action` objects. |
| **Action** | Defines *what* to do when the trigger fires: add a trade, hedge, exit, rebalance. |
| **Market** | *(pricebt)* the MarketDataProvider you supply once via `PricebtSession.use(market=...)`: it replaces GS's data infrastructure. |

A backtest **Engine** runs the strategy over a date range, building instruments on your market, computing risks and the P&L time series.

### Import Map

```python
from pricebt.backtests.strategy import Strategy
from pricebt.backtests.triggers import (
    PeriodicTrigger,
    PeriodicTriggerRequirements,
    StrategyRiskTrigger,
    RiskTriggerRequirements,
    MktTrigger,
    MktTriggerRequirements,
    AggregateTrigger,
    AggregateTriggerRequirements,
    DateTrigger,
    DateTriggerRequirements,
    MeanReversionTrigger,
    MeanReversionTriggerRequirements,
    PortfolioTrigger,
    PortfolioTriggerRequirements,
    NotTrigger,
    NotTriggerRequirements,
    TriggerDirection,
    AggType,
)
from pricebt.backtests.actions import (
    AddTradeAction,
    AddScaledTradeAction,
    HedgeAction,
    ExitTradeAction,
    ExitAllPositionsAction,
    EnterPositionQuantityScaledAction,   # stub: raises NotSupportedError
    RebalanceAction,
)
from pricebt.backtests.generic_engine import GenericEngine
from pricebt.backtests.equity_vol_engine import EquityVolEngine          # stub
from pricebt.backtests.data_sources import GenericDataSource, GsDataSource, MissingDataStrategy   # GsDataSource is a stub

# pricebt: the market (replaces GsSession) and the instruments
from pricebt.session import PricebtSession
from pricebt.instrument import IRSwap
```

---

## 2. Engines

| Engine | Best For | Instruments |
|---|---|---|
| **GenericEngine** | Any strategy on any market you supply | `IRSwap` (terms on the stack's spec) and any trade template you write |
| **EquityVolEngine** | *stub* (GS server-side) | write an equity-option pricable and use `GenericEngine` |
| **PredefinedAssetEngine** | *stub* (GS data handlers) | model the asset as a pricable; order-level logic via `OrdersGeneratorTrigger` / `CustomAction` |

```python
from pricebt.session import PricebtSession
from pricebt.testing.synthetic import SyntheticMarket

PricebtSession.use(market=SyntheticMarket(start='2023-01-03', end='2024-12-31', calendar_name='nyc'),
                   stack='pricebt.contrib.rateslib:STACK', calendar='nyc')
GE = GenericEngine()                         # uses the session's market
GE2 = GenericEngine(market=my_other_market)  # or a market for this engine only
```

`calendar` sets the business days of the grid, trigger schedules and trade durations. **Use a calendar your market has data for**: name it
by a registered name (importing an adapter registers its calendars: `pricebt.contrib.rateslib` registers `'nyc'`), a path to a calendar json,
or a list of holidays (the default is weekends only). The snapshot's own calendar (`calendar_name`) is what the stack's conventions refer to.
`PricebtSession.use(...)` also works as a context manager (`with PricebtSession.use(market=m): ...`) and accepts `tz`, `date_policy`
(`'close'`: a date means `session_close`, 17:00 by default), `session_open/session_close` and `settings` (extra `EngineSettings`).

---

## 3. Strategy Construction

```python
strategy = Strategy(None, trigger)  # empty starting portfolio
strategy = Strategy(initial_instrument, trigger)  # start with an instrument
strategy = Strategy(None, [trigger_add, trigger_hedge])  # multiple triggers
```

`Strategy(initial_portfolio, triggers, cash_accrual)` has gs's positional shape. Triggers are deep-copied, so a strategy can be run any
number of times with identical results.

---

## 4. Triggers

### 4.1 PeriodicTrigger - Trade on a Schedule

```python
from pricebt.backtests.triggers import PeriodicTrigger, PeriodicTriggerRequirements

trig_req = PeriodicTriggerRequirements(
    start_date=start_date,
    end_date=end_date,
    frequency='1m',  # '1b' (daily), '1w', '1m', '3m', '1y'
)
trigger = PeriodicTrigger(trig_req, action)
```

Schedules follow gs `RelativeDate` rules on the session calendar: `b` business days, `w`/`m` roll forward, `y` rolls back, multiples are
taken from the start date (`'1m'` from 2024-01-02: 01-02, 02-02, 03-04, 04-02, ...).

### 4.2 StrategyRiskTrigger - Trigger on Risk Breach

```python
from pricebt.backtests.triggers import StrategyRiskTrigger, RiskTriggerRequirements, TriggerDirection
from pricebt.risk import FXDelta
from pricebt.common import AggregationLevel

hedge_risk = FXDelta(aggregation_level=AggregationLevel.Type, currency='USD')
trig_req = RiskTriggerRequirements(
    risk=hedge_risk,
    trigger_level=50_000,
    direction=TriggerDirection.ABOVE,
)
trigger = StrategyRiskTrigger(trig_req, hedge_action)
```

`FXDelta` is the measure name `'fx_delta'`: no shipped instrument has it, so bind it on your own pricable
(an instrument spec whose `bind:` maps the schema name to your callable, see section 12). The rates equivalent is
`IRDelta(aggregation_level=AggregationLevel.Type)` (`'dv01'`). The risk is read after this point's earlier orders.

### 4.3 MktTrigger - Trigger on Market Data

```python
from pricebt.backtests.triggers import MktTrigger, MktTriggerRequirements
from pricebt.backtests.data_sources import GenericDataSource, MissingDataStrategy

data_source = GenericDataSource(pandas_series, MissingDataStrategy.fill_forward)
trig_req = MktTriggerRequirements(
    data_source=data_source,
    trigger_level=100.0,
    direction=TriggerDirection.BELOW,
)
trigger = MktTrigger(trig_req, action)
```

`GenericDataSource` is immutable and look-ahead free: a date-only observation becomes visible at that date's grid time (the session's
daily time), `fill_forward` uses the newest visible value, `MissingDataStrategy.fail` (gs's default) raises `MissingDataError` on a
date without an observation, and `interpolate` (needs the next observation) is refused.

### 4.4 DateTrigger - Trigger on Specific Dates

```python
from pricebt.backtests.triggers import DateTrigger, DateTriggerRequirements

trig_req = DateTriggerRequirements(
    dates=[date(2024, 3, 15), date(2024, 6, 15), date(2024, 9, 15)],
)
trigger = DateTrigger(trig_req, action)
```

As in gs, trigger dates are added to the evaluation dates (a weekend date adds a weekend point, so your market must answer for it);
dates outside `[start, end]` are ignored.

### 4.5 AggregateTrigger - Combine with AND/OR

```python
from pricebt.backtests.triggers import AggregateTrigger, AggregateTriggerRequirements, AggType

agg_req = AggregateTriggerRequirements(
    triggers=[periodic_trigger, risk_trigger],
    aggregate_type=AggType.ALL_OF,  # ALL_OF (AND) or ANY_OF (OR)
)
trigger = AggregateTrigger(agg_req, action)
```

Children may be Trigger objects (their requirements are used; their own actions are ignored, as in gs) or requirements. Every child is
evaluated at every point (no short-circuit), so stateful children never miss a point.

### 4.6 NotTrigger - Invert a Trigger

```python
from pricebt.backtests.triggers import NotTrigger, NotTriggerRequirements

not_req = NotTriggerRequirements(trigger=some_trigger_requirements)
trigger = NotTrigger(not_req, action)
```

---

## 5. Actions

### 5.1 AddTradeAction - Add a Trade

```python
action = AddTradeAction(
    priceables=instrument,
    trade_duration='1m',  # tenor, date, 'maturity' (a resolved date term), 'next schedule', or None (forever)
    name='my_trade',
)
```

**`trade_duration` options:** `None` (hold forever), tenor string (`'1m'`, `'3m'`; rolled from the ENTRY date on the session calendar),
`'maturity'` (any date among the RESOLVED terms of the built instrument), `'next schedule'` (auto-rolling: exit at the
trigger's next date), explicit `datetime.date`, or `datetime.timedelta`. An exit beyond the backtest end means "held".

### 5.2 HedgeAction - Delta Hedge

```python
from pricebt.backtests.actions import HedgeAction
from pricebt.risk import FXDelta

hedge_risk = FXDelta(aggregation_level='Type', currency='USD')
action = HedgeAction(
    risk=hedge_risk,
    priceables=FXForward(pair='EURUSD', settlement_date='1y', name='hedge_fwd'),   # stub: use your own FX pricable
    trade_duration='1m',
)
```

The hedge runs after this point's adds (any trigger order) and trades `q = -(net risk) / (unit risk of the hedge) x risk_percentage/100`
so the book's measure is flat. With the rates facade: `HedgeAction(IRDelta(aggregation_level=AggregationLevel.Type, currency='local'),
IRSwap(PayReceive.Pay, '10y', 'USD', notional_amount=1e8), '1b')`. A bucketed risk (bare `IRDelta`) solves one quantity per hedge leg
(a ladder hedge over the swap's `delta_ladder` buckets).

### 5.3 AddScaledTradeAction - Scale a Trade

```python
from pricebt.backtests.actions import AddScaledTradeAction, ScalingActionType

action = AddScaledTradeAction(
    priceables=instrument,
    trade_duration='1m',
    scaling_type=ScalingActionType.size,
    scaling_level=1_000_000,
)
```

`size`: the template times `scaling_level` (dated levels via a `{date: level}` dict); `risk_measure`: `scaling_level / unit risk` with
`scaling_risk=<RiskMeasure>`; `NAV`: spend `scaling_level` plus the action's realised cash, costs included.

### 5.4 EnterPositionQuantityScaledAction - Quantity-Based Entry

```python
from pricebt.backtests.actions import EnterPositionQuantityScaledAction, BacktestTradingQuantityType   # gs: gs_quant.target.backtests

action = EnterPositionQuantityScaledAction(   # raises NotSupportedError: EquityVolEngine-only in gs
    priceables=eq_option,
    trade_duration='1m',
    trade_quantity=1000,
    trade_quantity_type=BacktestTradingQuantityType.quantity,
)
```

Use `AddScaledTradeAction(priceables, trade_duration, scaling_type=ScalingActionType.size, scaling_level=1000)` with your own
equity-option pricable instead.

### 5.5 ExitTradeAction / ExitAllPositionsAction

```python
exit_named = ExitTradeAction(priceable_names='my_trade')
exit_all = ExitAllPositionsAction()
```

`priceable_names` matches a position's instrument name (the gs `name=`, shared by the legs of a named bundle), its leg name, its
creating ACTION's name (so the section 5.1 / 5.5 pair works as written) or its gs trade name (`'<Action>_<Instrument>_<date>'`, the
`trade_ledger()` index). Only positions booked at an earlier point are exited.

### 5.6 Multiple Actions on a Single Trigger

Order matters in gs (they execute in sequence). In pricebt, actions run by phase: exits, then adds, then adjustments, then hedges, so the
exit-before-add result is the same whichever order you list them in:

```python
trigger = StrategyRiskTrigger(trig_req, [exit_action, add_action])
```

---

## 6. GenericEngine Parameters

| Parameter | Description | Default | pricebt |
|---|---|---|---|
| `strategy` | The Strategy object | *required* | |
| `start` / `end` | Backtest date range | None | required unless `states` is given |
| `frequency` | Evaluation frequency: `'1b'`, `'1w'`, `'1m'` | `'1m'` | also intraday: `'15min'`, `'1h'` (session hours) |
| `states` | Explicit date list (overrides start/end/frequency) | None | dates or datetimes; trigger dates inside the range are added, as in gs |
| `risks` | Additional risk measures to compute | None | recorded every point; the strategy's own risks (hedge / trigger measures) are added too, as in gs |
| `show_progress` | Show progress bar | True | one tqdm bar |
| `initial_value` | Starting cash value | 0 | in `Cumulative Cash` and `Total` |
| `result_ccy` | Currency for results | None | `None` or `'USD'` only |
| `holiday_calendar` | Holiday dates for date maths | None | added to the session calendar (grid, schedules, durations) |
| `csa_term` | CSA for discounting | None | forwarded to your market as `request['csa_term']` |
| `market_data_location` | `'LDN'`, `'NYC'`, `'HKG'` | None | forwarded as `request['market_data_location']` (your market may use or ignore it) |
| `is_batch`, `visible_to_gs` | GS request plumbing | True, False | accepted and ignored |
| `calc_risk_at_trade_exits` | risks at trade exits (gs PnL explain) | False | `True` raises `NotSupportedError`: use P&L layers |
| `pnl_explain` | a `PnlDefinition` | None | a definition raises `NotSupportedError`: use `EngineSettings.layers` + `backtest.result.attribution()` |

---

## 7. Extracting Results

### result_summary - Main P&L DataFrame

```python
summary = backtest.result_summary
```

Columns: `Price` (MTM of live instruments), one column per additional risk, `Cumulative Cash` (initial value + all trade cash, coupons and
option settlements), `Transaction Costs` (cumulative, <= 0), `Total` (Price + Cash + Costs, on every row). The index holds `datetime.date`
values when there is one point per day (as gs), timestamps otherwise.

```python
backtest.result_summary['Total'].plot(title='Strategy Performance')
```

`backtest.result_summary[Price]` returns the `Price` column; the pre-2021 gs column `'Cash'` (per-point cash change) is derived on request,
so the notebook idiom `backtest.result_summary['Cash'].cumsum() + backtest.result_summary[Price]` works.

### trade_ledger - Trade History

```python
ledger = backtest.trade_ledger()
```

gs columns `Open, Close, Open Value, Close Value, Long Short, Status, Trade PnL`, indexed by `'<Action>_<Instrument>_<date>'`
(initial holdings: `'<Instrument>_<date>'`). `Open Value` is the cash paid at entry, `Close Value` all cash received afterwards (exit
proceeds, coupons, option cash settlement), `Trade PnL = Open Value + Close Value` for closed trades (before costs) and `NaN` while open
(`Close` None, `Close Value` 0). `Long Short` is the sign of the quantity held (gs reports the entry cash direction, always -1). A bundle of
legs is one row per leg.

### summary_stats - Performance Statistics

```python
stats = backtest.summary_stats()
```

Returns exactly gs's labels and formulas on `result_summary['Total']`: Start Date, End Date, Duration (days), Total PnL, Total Transaction
Costs, Total Trades, Peak PnL, Annualised Return, Annualised Volatility, Sharpe Ratio, Sortino Ratio, Max Drawdown, Max Drawdown Duration
(days), Calmar Ratio, Current Drawdown, Average Daily PnL, Daily PnL Std Dev, Best Day, Worst Day, % Positive Days, Skewness, Kurtosis.
`annualisation_factor` defaults to 252 (the richer pricebt statistics are `backtest.result.summary_stats()`).

```python
# Compare two backtests
import pandas as pd

pd.DataFrame({'A': backtest_a.summary_stats(), 'B': backtest_b.summary_stats()})
```

### Additional Risks

```python
from pricebt.risk import Price, FXDelta
from pricebt.common import AggregationLevel

backtest = GE.run_backtest(
    strategy,
    start=start_date,
    end=end_date,
    frequency='1b',
    risks=[Price, FXDelta(aggregation_level=AggregationLevel.Type, currency='USD')],
)
delta_series = backtest.result_summary[FXDelta(aggregation_level=AggregationLevel.Type, currency='USD')]
```

A risk column is labelled by its `RiskMeasure`, which is a `str` equal to the pricebt measure name: `result_summary[IRDeltaParallel]`,
`result_summary[IRDelta(aggregation_level=AggregationLevel.Type)]` and `result_summary['dv01']` are the same column. A position without
the measure contributes 0. A bucketed risk (bare `IRDelta`) stores one ladder (a `Series` by pillar) per cell; `backtest.ladder(IRDelta)`
returns them as a frame (rows = points, columns = pillars).

**gs risk measure -> pricebt measure**

| gs_quant | pricebt measure | kind | notes |
|---|---|---|---|
| `Price`, `DollarPrice` | `pv` | scalar | the `Price` column |
| `IRDelta(aggregation_level=Type / Asset / Class)`, `IRDeltaParallel` | `dv01` | scalar | the dollar delta: ccy per +1bp, a payer swap positive (the meaning is the schema's; how the library computes it is its binding's business) |
| `IRDelta`, `IRDelta()`, `IRDelta(aggregation_level=Point)`, `IRDeltaLocalCcy` | `delta_ladder` | vector | a plain dict tenor -> ccy per +1bp in that bucket (`3M`..`30Y`), bound per instrument; sums to ~ the parallel dv01 |
| `IRGamma(...)`, `IRGammaParallel` | `gamma` | scalar | parallel only |
| `IRVega(...)`, `IRVegaParallel`, `IRVegaLocalCcy` | `vega` | scalar | names only: volatility is out of scope, no shipped instrument binds it |
| `Theta` | `theta` | scalar | names only, as above |
| `FXDelta`, `FXGamma`, `FXVega` | `fx_delta`, `fx_gamma`, `fx_vega` | scalar | names only: no shipped FX instrument |
| `EqDelta`, `EqGamma`, `EqVega` | `eq_delta`, `eq_gamma`, `eq_vega` | scalar | names only: no shipped equity instrument |

`currency` may be `None`, `'local'` or `'USD'` (others raise `NotSupportedError`); gs's `mkt_type`/`mkt_asset`/... selectors are not
supported. Measures with the same pricebt name are the same column (`IRDeltaParallel` and `IRDelta(aggregation_level=Type)`).

---

## 8. Transaction Costs

Actions accept `transaction_cost` and `transaction_cost_exit` parameters (`transaction_cost_exit=None` = the entry model, as in gs).

### ConstantTransactionModel - Fixed Cost

```python
from pricebt.backtests.backtest_objects import ConstantTransactionModel

action = AddTradeAction(
    instrument,
    trade_duration='1m',
    transaction_cost=ConstantTransactionModel(500),
    transaction_cost_exit=ConstantTransactionModel(250),
)
```

### ScaledTransactionModel - Proportional Cost

Scale by instrument attribute or risk measure:

```python
from pricebt.backtests.backtest_objects import ScaledTransactionModel
from pricebt.risk import Price

# By notional: cost = notional_amount x 0.0001 (1bp)
action = AddTradeAction(
    instrument,
    trade_duration='1m',
    transaction_cost=ScaledTransactionModel(scaling_type='notional_amount', scaling_level=0.0001),
)

# By risk measure: cost = |Price| x 0.01 (1% of the value)
action = AddTradeAction(
    instrument,
    trade_duration='1m',
    transaction_cost=ScaledTransactionModel(scaling_type=Price, scaling_level=0.01),
)
```

A string `scaling_type` names a gs field (`notional_amount` is the schema's `notional`, read from the resolved terms); a `RiskMeasure` is
evaluated per unit at the trade (pricebt `ScaledCost('measure:<name>')`); the cost is `level x |metric x quantity|` per leg (the legs of a
bundle are charged separately, which equals gs's netted sum when the legs have the same sign).

### AggregateTransactionModel - Combine Models

```python
from pricebt.backtests.backtest_objects import (
    AggregateTransactionModel,
    ConstantTransactionModel,
    ScaledTransactionModel,
)

# Total cost = fixed $100 + 0.5bp of notional
combined = AggregateTransactionModel(
    transaction_models=(
        ConstantTransactionModel(100),
        ScaledTransactionModel('notional_amount', 0.00005),
    ),
    # aggregate_type defaults to TransactionAggType.SUM; also supports MAX, MIN
)
action = AddTradeAction(instrument, trade_duration='1m', transaction_cost=combined)
```

Transaction costs appear in `backtest.result_summary` as the `'Transaction Costs'` column (cumulative) and are included in `'Total'`.
Cash accrual: `Strategy(None, triggers, cash_accrual=ConstantCashAccrualModel(0.05))` or `DataCashAccrualModel(GenericDataSource(...))`.

---

## 9. Best Practices

- **Supply a calendar your market covers** (a registered name, a calendar json or a holiday list); it drives the grid, schedules and durations.
- **Match trade_duration to trigger frequency** for roll strategies (e.g. `'1m'` + `'1m'`). A tenor duration rolls from each ENTRY date,
  so month-end rolls can overlap by a day or two; `'next schedule'` (or exit-before-add) rolls exactly on the trigger dates.
- **Use `'maturity'`** (a date among the resolved terms) to hold a trade to its end.
- **Order triggers**: entry before hedge, `Strategy(None, [entry_trigger, hedge_trigger])`. pricebt runs hedges after adds whatever the
  order, so a hedge always sees the same point's entries.
- **Order actions within a trigger**: exit before add, `PeriodicTrigger(trig_req, [exit_action, add_action])` (pricebt runs exits first anyway).
- **Name your instruments** - names appear in `trade_ledger()` and select positions in `ExitTradeAction`.
- **`frequency='1b'`** for daily P&L; trigger frequency and evaluation frequency are independent.

---

## 10. EquityVolEngine

gs runs equity option / variance swap backtests on its servers. In pricebt `EquityVolEngine`, `EqOption`, `EqVarianceSwap` and
`EnterPositionQuantityScaledAction` raise `NotSupportedError`:

```python
from pricebt.instrument import EqOption, OptionType, OptionStyle           # EqOption(...) raises NotSupportedError
from pricebt.backtests.equity_vol_engine import EquityVolEngine            # EquityVolEngine() raises NotSupportedError
```

Instead, write an equity-option pricable (`value(ctx)` returning the option value from a pricer that holds spot and vol, plus `eq_delta`
/ `vega` measures), a market that serves those pricers, and run `GenericEngine` with `AddTradeAction` / `AddScaledTradeAction` (section 12).

---

## 11. Quick Reference Template

```python
from datetime import date
from pricebt.session import PricebtSession
from pricebt.testing.synthetic import SyntheticMarket
from pricebt.instrument import IRSwap
from pricebt.common import Currency
from pricebt.backtests.triggers import PeriodicTrigger, PeriodicTriggerRequirements
from pricebt.backtests.actions import AddTradeAction
from pricebt.backtests.generic_engine import GenericEngine
from pricebt.backtests.strategy import Strategy
from pricebt.risk import Price

# instead of GsSession.use(): the market once, and the adapter that prices it (its instrument specs are looked up by IRSwap)
PricebtSession.use(market=SyntheticMarket(start='2023-01-03', end='2024-12-31', calendar_name='nyc'), stack='pricebt.contrib.rateslib:STACK')

start_date = date(2023, 1, 3)
end_date = date(2024, 12, 31)

# 1. Define the instrument
instrument = IRSwap('Pay', '10y', Currency.USD, name='10y')

# 2. Define trigger + action
trig_req = PeriodicTriggerRequirements(start_date=start_date, end_date=end_date, frequency='6m')
action = AddTradeAction(instrument, trade_duration='6m')
trigger = PeriodicTrigger(trig_req, action)

# 3. Build strategy
strategy = Strategy(None, trigger)

# 4. Run
GE = GenericEngine()
backtest = GE.run_backtest(strategy, start=start_date, end=end_date, frequency='1b', show_progress=False)

# 5. View results
backtest.result_summary['Total'].plot(title='Performance')
backtest.trade_ledger()
backtest.summary_stats()
```

To run the same strategy under QuantLib change ONE argument: `stack='pricebt.contrib.quantlib:STACK'`. To compare the two level by level, use the
tie-out harness (`docs/DESIGN.md` section 11).

### The delta-hedging notebook's generic backtester (gs `4-Delta Hedging.ipynb`), on swaps

```python
from pricebt.backtests.triggers import PeriodicTrigger, PeriodicTriggerRequirements
from pricebt.backtests.actions import AddTradeAction, HedgeAction
from pricebt.backtests.generic_engine import GenericEngine
from pricebt.backtests.strategy import Strategy
from pricebt.common import AggregationLevel, PayReceive
from pricebt.instrument import IRSwap
from pricebt.risk import IRDelta, Price

trig_req = PeriodicTriggerRequirements(start_date=start_date, end_date=end_date, frequency='1b')
swap_book = IRSwap(PayReceive.Pay, '5y', 'USD', fixed_rate='ATMF', notional_amount=1e8, name='5y_payer')
swap_hedge = IRSwap(PayReceive.Receive, '10y', 'USD', fixed_rate='ATMF', notional_amount=1e8, name='10y_hedge')
action_trade = AddTradeAction(swap_book, '1m')
action_hedge = HedgeAction(IRDelta(aggregation_level=AggregationLevel.Type, currency='local'), swap_hedge, '1b')
triggers = PeriodicTrigger(trig_req, [action_trade, action_hedge])
strategy = Strategy(None, triggers)
backtest = GenericEngine().run_backtest(strategy, start=start_date, end=end_date, frequency='1b', show_progress=True)
backtest.result_summary['Cash'].cumsum() + backtest.result_summary[Price]     # the notebook's comparison series
```

The book is a monthly 5y payer, and the whole book's dollar delta is hedged every day with one 10y receiver that is replaced the next day.
A ladder hedge (one quantity per hedge leg, to a target ladder such as `{"10Y": 1_000_000}`) is the config form
`{type: hedge, risk: delta_ladder, target: {...}, priceables: [...]}` shown in `notebooks/showcase_swap_book.ipynb`.

---

## 12. Plug in your own market and instruments

Everything the stripped data infrastructure did is a handful of small interfaces (`docs/DESIGN.md` sections 1, 3, 4 and 10):

- **Market**: any object with `get_pricer(ts, request) -> Pricer`, where `ts` is a tz-aware `pd.Timestamp` and the pricer wraps an immutable
  **snapshot** (`pricebt.snapshot.SnapshotPricer(MarketSnapshot(...))`: discount factors at node dates, published fixings, bond quotes,
  calendars as holiday sets; plain data, no library). Ready-made: `pricebt.testing.synthetic.SyntheticMarket`, `pricebt.pricer.FunctionMDP(fn)`
  for a function, and the fixture readers of `tests/support` (test support, not part of the package). Several markets:
  `PricebtSession.use(market={'primary': rates, 'funding': repo}, stack=...)`.
- **Stack**: the adapter that turns snapshots into a library's objects and ships the instrument specs `IRSwap(...)` resolves:
  `pricebt.contrib.rateslib:STACK`, `pricebt.contrib.quantlib:STACK`, or your own `pricebt.contracts.spec.Stack(name, wrap, instruments, defaults)`.
- **Instrument**: an instrument spec: `asset_class`, a `factory` (a Kit: the factory plus its default bindings), `conventions` (data, passed to the
  library untouched) and `bind:` mapping each schema name (`value`, `dv01`, `gamma`, `rate`, `delta_ladder`, the layers) to a callable of the built
  object with exactly the arguments it takes. The trade terms come from the action. A library with its own names is used by binding its methods:
  nothing is looked up by name and nothing is injected by parameter name. Pass a template wherever gs takes an instrument
  (`AddTradeAction(my_template, '1m')`, `HedgeAction(FXDelta, my_hedge_template, '1m')`, `Strategy(my_template, triggers)`).
- **Measures used by risks**: a gs measure name maps to a schema name (`IRDelta -> dv01`); an instrument that does not bind it contributes 0 to the
  recorded column and is refused by a trigger or hedge that needs it.

`pricebt.testing.toys` (a scalar-rate market with a forward, zero, Bachelier option and future) is a complete worked example, and
`tests/guards/zero_toys.py` shows a swap whose method names deliberately cross the schema's; sections 3-9 of this guide run on the toys in
`tests/test_gs_compat_guide.py`.

## 13. Deviations from gs_quant (deliberate)

- Evaluation is date-major: every trigger is evaluated once per point in list order; within a point actions run EXIT < ADD < ADJUST < HEDGE
  (gs ran each trigger over all dates before the next one). Exits only touch positions booked at an earlier point.
- The `IRSwap` constructor returns a template of TERMS (not a gs `Instrument` object): `isinstance(x, IRSwap)` does not work, `clone()` does.
  Defaults: `notional_amount=1e7`, `pay_or_receive=Pay`, `buy_sell=Buy`; positional order follows gs (`IRSwap(pay_or_receive,
  termination_date, notional_currency, notional_amount, effective_date)`), everything else by keyword. gs decimals (`fixed_rate=0.04`) become
  percent (4.0) in the facade; `'ATMF'` is the token `par`, which the stack's library resolves at trade time.
- `trade_ledger()` `Long Short` is the sign of the quantity; `Close Value` includes coupons and option settlements.
- `RebalanceAction.method(ts, view, info)` receives the read-only engine view instead of gs's backtest object.
- `BacktestTradingQuantityType` lives in `pricebt.common` / `pricebt.backtests.actions` (gs: `gs_quant.target.backtests`).
- gs `result_summary` could not hold `IRDelta` ladders sensibly; pricebt stores one `Series` per cell and offers `backtest.ladder(...)`.
