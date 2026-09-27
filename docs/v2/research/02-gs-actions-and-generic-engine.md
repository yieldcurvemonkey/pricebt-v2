# 02 — gs_quant `actions.py`, `action_handler.py`, `generic_engine.py` (+ `backtest_engine.py`, `execution_engine.py`)

## Key facts

1. **Reference:** gs_quant **1.5.4** installed at `C:/Users/chris/anaconda3/Lib/site-packages/gs_quant/backtests/` (named by the user as the API reference). **The repo checkout `C:/Users/chris/clee/gsquant-temp-claude/gs-quant` is a newer release (2.1.17)** that holds the user's reference notebook and differs a lot (§9). The mean-reversion trigger crashes on 1.5.4 and is fixed in 2.1.17. Fields marked `[introspected]` were printed by `inspect`/`dataclasses.fields` on 1.5.4; the rest is `[code-read, not executed]` (running it needs a GS session).
2. **GenericEngine is a 7-phase batch engine, not a pure day-by-day loop:** (1) build date grid → (2) resolve initial portfolio → (3) apply *all* non-path-dependent triggers/actions for *all* dates at once → (4) price every day's portfolio in one batch → (5) per-date loop only for path-dependent triggers/actions and hedge sizing → (6) price any positions still unpriced → (7) book cash (entry/exit) and transaction costs.
3. **Trades are priced on the same day's close market** (`PricingContext(pricing_date=d)`): entry cash is booked at `-PV(create_date)`, exit cash at `+PV(final_date)`; the trade is held for `create_date <= s < final_date`, so it is **in** the PV on entry day and **not** in the PV on exit day. `fixed_rate='ATM'` etc. is resolved server-side via `ResolvedInstrumentValues` on the create date.
4. **Every number comes from GS servers** (resolve, PV, risks, hedge ratios, risk-based transaction costs, OIS cash accrual, holiday calendars when none given); the engine itself is only dict/list bookkeeping keyed by **instrument name**, and names carry semantics (`<Action>_<Priceable>_<YYYY-MM-DD>`).
5. **Multi-currency is NOT supported natively:** cash is kept per currency but `BackTest.result_summary` raises `RuntimeError('Cannot aggregate cash in multiple currencies')` if more than one appears; the only escape is `result_ccy=` which re-parameterises every risk measure (`Price(currency=ccy)`) so the server converts. HedgeAction raises `'cannot hedge in a different currency'` if units differ.

Line references: `actions.py:N`, `generic_engine.py:N`, `backtest_objects.py:N`, `backtest_utils.py:N`, `triggers.py:N` are line numbers in the installed files.

---

## 1. Module map

| File | Lines | What it contains | Relevance to v2 |
|---|---|---|---|
| `action_handler.py` | 49 | `ActionHandler(action)` with abstract `apply_action(state, backtest, trigger_info) -> Any` (line 25–41); `ActionHandlerBaseFactory.get_action_handler(action)` (46–49); `TActionHandler` TypeVar | Port 1:1 (pure interface). |
| `backtest_engine.py` | 26 | `BacktestBaseEngine` with abstract `get_action_handler(action)` (line 23–26) | Port 1:1 (pure interface). |
| `execution_engine.py` | 52 | `ExecutionEngine` (empty), `SimulatedExecutionEngine(data_handler)` with `submit_order(order: OrderEvent)` (keeps `orders` sorted by `order.execution_end_time()`) and `ping(state: dt.datetime) -> list[FillEvent]` (pops every order whose `execution_end_time() <= state`, fills at `order.execution_price(data_handler)`, units `order.execution_quantity()`) | **Used only by `PredefinedAssetEngine`**, not by GenericEngine. Not needed for GenericEngine parity. |
| `actions.py` | 437 | Action dataclasses, `ScalingActionType`, `*ActionInfo` namedtuples | Public API — port 1:1. |
| `generic_engine.py` | 1175 | `OrderBasedActionImpl`, `AddTradeActionImpl`, `AddScaledTradeActionImpl`, `HedgeActionImpl`, `ExitTradeActionImpl`, `RebalanceActionImpl`, `GenericEngineActionFactory`, `GenericEngine` | Engine semantics — port behaviour; replace every pricing call. |
| `backtest_objects.py` (support) | 798 | `BackTest`, `ScalingPortfolio`, `TransactionModel`s, `TransactionCostEntry`, `CashPayment`, `Hedge`, `CashAccrualModel`s, `PnlDefinition` | Needed to understand the loop; documented here only as far as the engine touches it. |
| `backtest_utils.py` (support) | 127 | `CalcType`, `CustomDuration`, `make_list`, `get_final_date`, `map_ccy_name_to_ccy`, `interpolate_signal` | Port 1:1 (pure logic except `RelativeDate`). |

---

## 2. Actions (`actions.py`)

### 2.1 Module-level objects

| Object | Definition (exact) | Line |
|---|---|---|
| `action_count` | module global int, starts at `1` | 44 |
| `Duration` | `Union[str, dt.date, dt.timedelta, CustomDuration]` | 47 |
| `default_transaction_cost()` | `return ConstantTransactionModel(0)` | 50–51 |
| `ScalingActionType(Enum)` | `risk_measure = 'risk_measure'`, `size = 'size'`, `NAV = 'NAV'` | 54–57 |
| `AddTradeActionInfo` | `namedtuple('AddTradeActionInfo', ['scaling', 'next_schedule'])` | 174 |
| `HedgeActionInfo` | `namedtuple('HedgeActionInfo', 'next_schedule')` | 175 |
| `ExitTradeActionInfo` | `namedtuple('ExitTradeActionInfo', 'not_applicable')` | 176 |
| `RebalanceActionInfo` | `namedtuple('RebalanceActionInfo', 'not_applicable')` | 177 |
| `AddScaledTradeActionInfo` | `namedtuple('AddScaledActionInfo', 'next_schedule')` (note: typename differs from variable name) | 178 |
| `TAction` | `TypeVar('TAction', bound='Action')` | 113 |

`CalcType` (`backtest_utils.py:32`): `simple = 'simple'`, `semi_path_dependent = 'semi_path_dependent'`, `path_dependent = 'path_dependent'`.

`CustomDuration` (`backtest_utils.py:38–45`): dataclass `durations: Tuple[Union[str, dt.date, dt.timedelta], ...]`, `function: Callable[[...], Union[str, dt.date, dt.timedelta]]`; `__hash__ = hash((durations, function))`. `get_final_date` calls `function(*[get_final_date(d) for d in durations])` — e.g. `CustomDuration(('1m', 'expiration_date'), min)`.

### 2.2 `Action` base (line 60–110)

Class attributes (NOT dataclass fields — they are plain class attrs without annotations):

| Attr | Default | Meaning |
|---|---|---|
| `_needs_scaling` | `False` | unused by GenericEngine |
| `_calc_type` | `CalcType.simple` | how the engine schedules the action (see §4.4) |
| `_risk` | `None` | exposed via `.risk`; `Trigger.risks` collects `x.risk for x in actions if x.risk is not None` (`triggers.py:490`). For `HedgeAction` the dataclass field `risk` shadows this, so the hedge risk is added to `strategy.risks`. |
| `_transaction_cost` | `ConstantTransactionModel(0)` | backing for property `transaction_cost` |
| `_transaction_cost_exit` | `None` | backing for property `transaction_cost_exit` |
| `name` | `None` | |
| `__sub_classes` | `ClassVar[List[type]] = []` | every subclass registers itself via `__init_subclass__`; `Action.sub_classes()` returns tuple (used for JSON decoding of `Trigger.actions`). |

Methods: `__post_init__` → `self.set_name(self.name)`. `set_name`: if `self.name is None`: `self.name = f'Action{action_count}'; action_count += 1` (global counter, so auto names depend on construction order across the whole process). Properties `calc_type`, `risk`, `transaction_cost` (+setter), `transaction_cost_exit` (+setter).

All subclasses are `@dataclass_json @dataclass`. `class_type` fields are `static_field(...)` (a fixed string used for JSON; not user-settable in practice).

### 2.3 Action classes — exact fields in declaration order `[introspected]`

**Engine support:** GenericEngine factory (`generic_engine.py:615–623`) maps exactly: `AddTradeAction→AddTradeActionImpl`, `HedgeAction→HedgeActionImpl`, `ExitTradeAction→ExitTradeActionImpl`, `ExitAllPositionsAction→ExitTradeActionImpl`, `RebalanceAction→RebalanceActionImpl`, `AddScaledTradeAction→AddScaledTradeActionImpl`, plus any user `action_impl_map` entries (which override). Lookup is by **exact `type(action)`** (no isinstance), else `RuntimeError(f'Action {type(action)} not supported by engine')`. A **new handler instance is created on every `get_action_handler` call** (no state survives between calls).

#### `AddTradeAction` (line 116–171) — `_calc_type = simple`

| # | Field | Type | Default |
|---|---|---|---|
| 1 | `priceables` | `Union[Instrument, Iterable[Instrument]]` | `None` |
| 2 | `trade_duration` | `Duration` | `None` |
| 3 | `name` | `str` | `None` |
| 4 | `transaction_cost` | `TransactionModel` | `default_factory=default_transaction_cost` → `ConstantTransactionModel(0)` |
| 5 | `transaction_cost_exit` | `Optional[TransactionModel]` | `None` |
| 6 | `holiday_calendar` | `Iterable[dt.date]` | `None` |
| 7 | `class_type` | `str` | `'add_trade_action'` (static) |

`__post_init__` (149–164): `super().__post_init__()` (assigns `ActionN` name if none); `self._dated_priceables = {}`; renames priceables (rule R1 below); if `transaction_cost is None` → `ConstantTransactionModel(0)`; if `transaction_cost_exit is None` → `= transaction_cost` (same object).
Extra API: `set_dated_priceables(state, priceables)` stores `make_list(priceables)` under `state`; property `dated_priceables` → dict. `OrderBasedActionImpl.get_base_orders_for_states` uses `dated_priceables.get(s) or self.action.priceables` (per-date override of what to trade). Nothing inside gs_quant calls `set_dated_priceables`; it is user-facing API.
Positional usage in examples: `AddTradeAction(swap)`, `AddTradeAction(option, 'expiration_date')`, `AddTradeAction(ndx_opt, '2m', name='Action1')`.

#### `AddScaledTradeAction` (line 181–235) — `_calc_type = simple`

| # | Field | Type | Default |
|---|---|---|---|
| 1 | `priceables` | `Union[Priceable, Iterable[Priceable]]` | `None` |
| 2 | `trade_duration` | `Duration` | `None` |
| 3 | `name` | `str` | `None` |
| 4 | `scaling_type` | `ScalingActionType` | `ScalingActionType.size` |
| 5 | `scaling_risk` | `RiskMeasure` | `None` |
| 6 | `scaling_level` | `Union[float, dict[dt.date, Union[float, int]]]` | `1` |
| 7 | `transaction_cost` | `TransactionModel` | `ConstantTransactionModel(0)` (factory) |
| 8 | `transaction_cost_exit` | `Optional[TransactionModel]` | `None` |
| 9 | `holiday_calendar` | `Iterable[dt.date]` | `None` |
| 10 | `class_type` | `str` | `'add_scaled_trade_action'` |

`__post_init__` (223–235): rename rule R1; `transaction_cost_exit = transaction_cost` if None. (Does **not** null-guard `transaction_cost`; no `_dated_priceables` attribute — `getattr(..., 'dated_priceables', {})` returns `{}`.)

#### `EnterPositionQuantityScaledAction` (line 238–287) — **not supported by GenericEngine** (EquityVolEngine only; deprecation warning there says use `AddScaledTradeAction`)

| # | Field | Type | Default |
|---|---|---|---|
| 1 | `priceables` | `Union[Priceable, Iterable[Priceable]]` | `None` |
| 2 | `trade_duration` | `Duration` | `None` |
| 3 | `name` | `str` | `None` |
| 4 | `trade_quantity` | `Union[float, dict[dt.date, Union[float, int]]]` | `1` |
| 5 | `trade_quantity_type` | `BacktestTradingQuantityType` | `BacktestTradingQuantityType.quantity` (enum members: `notional, quantity, vega, gamma, NAV, premium, vegaNotional`) |
| 6 | `transaction_cost` | `TransactionModel` | `ConstantTransactionModel(0)` |
| 7 | `transaction_cost_exit` | `Optional[TransactionModel]` | `None` |
| 8 | `class_type` | `str` | `'enter_position_quantity_scaled_action'` |

Note: the GS example notebook `040312` runs this through `GenericEngine`; per the factory map that raises `RuntimeError` `[code-read]`.

#### `ExitPositionAction` (line 290–294) — **not supported by GenericEngine** (EquityVolEngine, deprecated)

| # | Field | Type | Default |
|---|---|---|---|
| 1 | `name` | `str` | `None` |
| 2 | `class_type` | `str` | `'exit_position_action'` (plain default, not `static_field`) |

#### `ExitTradeAction` (line 297–309) — `_calc_type = simple` (!)

| # | Field | Type | Default |
|---|---|---|---|
| 1 | `priceable_names` | `Union[str, Iterable[str]]` | `None` |
| 2 | `name` | `str` | `None` |
| 3 | `transaction_cost` | `TransactionModel` | `ConstantTransactionModel(0)` |
| 4 | `class_type` | `str` | `'exit_trade_action'` |

`__post_init__`: sets `self.priceables_names = make_list(self.priceable_names)` (typo'd attribute, **never read**; the handler reads `priceable_names` which stays as given — a bare `str` then does substring `in` checks, see §5.5). `transaction_cost` is **not used** by the handler: exits reuse the TCE attached to the moved exit CashPayment (i.e. the *entry* action's `transaction_cost_exit`).

#### `ExitAllPositionsAction(ExitTradeAction)` (line 312–323) — `_calc_type = path_dependent`

Same fields as `ExitTradeAction` in same order; `class_type = 'exit_all_positions_action'`. `__post_init__` sets `_calc_type = CalcType.path_dependent`. Handled by `ExitTradeActionImpl` (with `priceable_names=None` → exit everything held on the trigger date).

#### `HedgeAction` (line 326–407) — `_calc_type = semi_path_dependent`

| # | Field | Type | Default |
|---|---|---|---|
| 1 | `risk` | `RiskMeasure` | `None` |
| 2 | `priceables` | `Optional[Priceable]` | `None` |
| 3 | `trade_duration` | `Duration` | `None` |
| 4 | `name` | `str` | `None` |
| 5 | `csa_term` | `str` | `None` |
| 6 | `scaling_parameter` | `str` | `'notional_amount'` (deprecated; any other value → `DeprecationWarning`, otherwise unused) |
| 7 | `transaction_cost` | `TransactionModel` | `ConstantTransactionModel(0)` |
| 8 | `transaction_cost_exit` | `Optional[TransactionModel]` | `None` |
| 9 | `risk_transformation` | `Transformer` | `None` |
| 10 | `holiday_calendar` | `Iterable[dt.date]` | `None` |
| 11 | `risk_percentage` | `float` | `100` |
| 12 | `class_type` | `str` | `'hedge_action'` |

`__post_init__` (369–403):
1. `_calc_type = CalcType.semi_path_dependent`.
2. `portfolio = priceables` if it is a `Portfolio`; else if it is a `Priceable` → `Portfolio(priceables.clone(name=None), name=priceables.name)` (single instrument wrapped; **the instrument's name is cleared and becomes the portfolio's name**); else `None`.
3. `if not Portfolio: raise ...` — bug: tests the class, never true; a `None`/list `priceables` crashes later at `enumerate(None)` with `TypeError`.
4. Rename each instrument in the portfolio with rule R1; `self.priceables = Portfolio(named, name=portfolio.name)`.
5. `transaction_cost_exit = transaction_cost` if None.
Property `priceable` → `self.priceables` (a `Portfolio`).
Positional usage: `HedgeAction(IRDelta(aggregation_level='Type', currency=Currency.USD), swap_hedge)`.

#### `RebalanceAction` (line 410–437) — `_calc_type = path_dependent`

| # | Field | Type | Default |
|---|---|---|---|
| 1 | `priceable` | `Priceable` | `None` |
| 2 | `size_parameter` | `Union[str, float]` | `None` (name of the instrument size attribute, e.g. `'number_of_options'`, `'notional_amount'`) |
| 3 | `method` | `Callable` | `None` — signature `method(state, backtest, trigger_info) -> new_size` |
| 4 | `transaction_cost` | `TransactionModel` | `ConstantTransactionModel(0)` |
| 5 | `transaction_cost_exit` | `Optional[TransactionModel]` | `None` |
| 6 | `name` | `str` | `None` |

No `class_type` field. `__post_init__`: `_calc_type = path_dependent`; `if self.priceable.unresolved is None: raise ValueError("Please specify a resolved priceable to rebalance.")` (the priceable must have been resolved beforehand, e.g. `with PricingContext(start): opt.resolve()`); rename: no name → `f'{name}_Priceable0'`, else **always** `f'{name}_{priceable.name}'` (no startswith check, unlike R1); `transaction_cost_exit` defaulting.

### 2.4 Naming rules (names are load-bearing)

All bookkeeping (results lookup, exit matching, cash lookup, rebalance matching) is by `instrument.name`. `str(date)` for a `dt.date` is `YYYY-MM-DD`.

| Rule | Where | Transformation |
|---|---|---|
| **R1** action construction | `AddTradeAction`, `AddScaledTradeAction`, `EnterPositionQuantityScaledAction`, `HedgeAction` `__post_init__` | for `i, p in enumerate(priceables)`: `p.name is None` → `clone(name=f'{action.name}_Priceable{i}')`; `p.name.startswith(action.name)` → keep; else → `clone(name=f'{action.name}_{p.name}')` |
| **R2** AddTrade order | `generic_engine.py:119` | resolved instrument `t` → `t.clone(name=f'{t.name}_{d}')` where `d` = create date |
| **R3** AddScaled order | `generic_engine.py:320` | `new_inst.name = f'{new_inst.name}_{d}'` (mutates the resolved object) |
| **R4** Hedge order | `generic_engine.py:405–408` | outer hedge portfolio: `f'{portfolio.name}_{create_date:%Y-%m-%d}'`; each inner instrument: `f'{outer_new_name}_{instrument.name}'` |
| **R5** Hedge scaled copy | `generic_engine.py:1038–1040` | deep copy; portfolio and every instrument get prefix `'Scaled_'` |
| **R6** initial portfolio | `generic_engine.py:891–893` | `clone(name=f'{old_name}_{strategy_start_date:%Y-%m-%d}')` — `old_name` may be `None` → `'None_2021-06-01'` |
| **R7** Rebalance order | `generic_engine.py:571–573` | `priceable.clone(**{size_parameter: delta, 'name': f'{priceable.name}_{state}'})` |

Worked examples (action auto-named `Action1`/`Action2`, create date 2021-06-01):

| Input | Held-position name |
|---|---|
| `AddTradeAction(IRSwap(..., name='swap_10y'))` | `Action1_swap_10y_2021-06-01` |
| `AddTradeAction(IRSwap(...))` (no name) | `Action1_Priceable0_2021-06-01` |
| `AddTradeAction(IRSwap(name='Action1x'))` with action name `Action1` | `Action1x_2021-06-01` (startswith → not prefixed) |
| `HedgeAction(risk, IRSwap(name='30yhedge'))` action `Action2` | inner instrument after ctor: `Action2_Priceable0` in portfolio `30yhedge`; held: portfolio `Scaled_30yhedge_2021-06-01`, instrument `Scaled_30yhedge_2021-06-01_Action2_Priceable0` |
| `HedgeAction(risk, IRSwap())` (no name) | `Scaled_None_2021-06-01_Action2_Priceable0` |
| `Strategy(initial_portfolio=IRSwap(name='s'))`, start 2021-06-01 | `s_2021-06-01` |
| `RebalanceAction(opt(name='spx'), 'number_of_options', f, name='Rb')` on 2021-06-08 | action priceable `Rb_spx`; order `Rb_spx_2021-06-08` |

**ExitTradeAction name parsing** (`generic_engine.py:475–489`): "We expect tradable names to be defined as `<ActionName>_<TradeName>_<TradeDate>`". With `priceable_names` given, a held instrument `x` is removed iff `strptime(x.name.split('_')[-1], '%Y-%m-%d').date() <= s` **and** `x.name.split('_')[-2] in priceable_names`. Consequences:
- The user must pass the **priceable's own name** (the token before the date), e.g. `'swap_10y'` does **not** work (split gives `'10y'`), `'Priceable0'` works for unnamed priceables. Any underscore in the user's priceable name breaks matching.
- Hedge instruments end in `..._Action2_Priceable0` → `strptime('Priceable0')` raises `ValueError` → **any `ExitTradeAction(priceable_names=...)` crashes if a hedge position is held** on a date it scans.
- Rebalance matching (`generic_engine.py:566, 586`) uses `priceable.name.split('_')[-1] in trade.name` — **substring** match on the last token (e.g. `'spx'` matches any name containing `spx`).

### 2.5 `trade_duration` → final (exit) date: `get_final_date(inst, create_date, duration, holiday_calendar=None, trigger_info=None)` (`backtest_utils.py:65–89`)

Evaluated in this exact order; result memoised in module-global `final_date_cache` keyed `(inst, create_date, duration, holiday_calendar)` (**`trigger_info` not in the key**, and 'next schedule'/CustomDuration results are not cached):

| # | Condition | Result |
|---|---|---|
| 1 | `duration is None` | `dt.date.max` (held forever; exit cash never booked because `date.max > end`) |
| 2 | `isinstance(duration, (dt.datetime, dt.date))` | `duration` itself |
| 3 | `hasattr(inst, str(duration))` | `getattr(inst, str(duration))` — instrument attribute of the **resolved** instrument, e.g. `'expiration_date'`, `'termination_date'` |
| 4 | `str(duration).lower() == 'next schedule'` | `trigger_info.next_schedule or dt.date.max` if `trigger_info` has attr `next_schedule`; else `RuntimeError('Next schedule not supported by action')` |
| 5 | `isinstance(duration, CustomDuration)` | `duration.function(*(get_final_date(inst, create_date, d, holiday_calendar, trigger_info) for d in duration.durations))` |
| 6 | otherwise (tenor string like `'1m'`, `'2w'`, `'1b'`; a `timedelta` also falls here even though `Duration` and the docstring list it — whether `RelativeDate` accepts it is `[not verified]`; in 2.1.17 the call is `RelativeDate(duration.lower(), ...)`, which a `timedelta` cannot survive) | `RelativeDate(duration, create_date).apply_rule(holiday_calendar=holiday_calendar)` |

`holiday_calendar` here is the **action's** `holiday_calendar` (`OrderBasedActionImpl.get_instrument_final_date`, line 97–98). For the initial portfolio it is `run_backtest`'s `holiday_calendar`.

Holding window: the position is appended to `portfolio_dict[s]` for every backtest state with **`create_date <= s < final_date`** (AddTrade/AddScaled line 151, Hedge line 410, Rebalance line 600 uses `unwind.effective_date > s >= state`).

RelativeDate tenor semantics needed for durations and the date grid (`gs_quant/datetime/rules.py`; `holidays` = `holiday_calendar` if given, **else `GsCalendar` network lookup, falling back to `[]` on failure**; `week_mask='1111100'`):

| Unit | Rule |
|---|---|
| `Nb` | `np.busday_offset(base, N, roll='preceding' if N>0 else 'forward', holidays)` (a weekend base first rolls back to Friday, then +N business days) |
| `Nd` | `base + N calendar days` (no adjustment) |
| `Nw` | `base + N weeks`, then roll `'forward'` (N≥0) to a business day |
| `Nm` | `base + N months` (relativedelta, end-of-month clipped), then roll `'forward'` |
| `Ny` | `base + N years`, step forward over weekend days, then roll `'backward'` |

### 2.6 Transaction cost models (`backtest_objects.py:386–556`)

| Class | Fields (order, default) | `get_unit_cost(state, info, instrument)` |
|---|---|---|
| `TransactionModel` (frozen dataclass) | — | `pass` → `None` |
| `ConstantTransactionModel` | `cost: Union[float,int] = 0`, `class_type='constant_transaction_model'` | `self.cost` (a flat cash amount **per instrument per transaction**, not scaled by size) |
| `ScaledTransactionModel` | `scaling_type: Union[str, RiskMeasure] = 'notional_amount'`, `scaling_level: Union[float,int] = 0.0001`, `class_type='scaled_transaction_model'` | if `scaling_type` is `str` → `getattr(instrument, scaling_type)` (RuntimeError if missing); else if `state > today` → `np.nan`; else **server calc** `with PricingContext(state): instrument.calc(scaling_type)` (future) |
| `AggregateTransactionModel` | `transaction_models: tuple = ()`, `aggregate_type: TransactionAggType = SUM` | empty → 0; SUM/MAX/MIN of sub-model unit costs |

`TransactionAggType(Enum)`: `SUM='sum'`, `MAX='max'`, `MIN='min'`.

`TransactionCostEntry(date, instrument, transaction_model)` — stateful wrapper (line 442–556):

| Member | Semantics |
|---|---|
| `_date` (+`date` setter) | booking date; ExitTrade moves it to the exit trigger date |
| `_additional_scaling = 1` (+setter) | multiplier applied **only to Scaled models** (hedge ratio, NAV factor) |
| `all_instruments` | `instrument.all_instruments` if Portfolio else `(instrument,)` |
| `all_transaction_models` | sub-models if Aggregate else `(model,)` |
| `cost_aggregation_func` | `sum`/`max`/`min` per Aggregate type; `sum` otherwise |
| `no_of_risk_calcs` | count of sub-models that are `ScaledTransactionModel` with a `RiskMeasure` scaling_type (only used for `calc_calls`/`calculations` counters) |
| `calculate_unit_cost()` | for each model m, each instrument i: `_unit_cost_by_model_by_inst[m][i] = m.get_unit_cost(self._date, None, i)` — uses the **date at the time it is called** |
| `get_final_cost()` | per model: `cost = Σ_i resolved(unit_cost[m][i])` (PortfolioRiskResult→`.aggregate()`, PricingFuture→`.result()`); for Scaled: `cost = scaling_level * abs(cost * additional_scaling)`; Constant: `cost` (NOT multiplied by additional_scaling, NOT abs); return `agg_func(costs)` or 0 |
| `get_cost_by_component()` | returns `(fixed, scaled)` split for NAV solving: scaled = `abs(cost*scaling_level*additional_scaling)`; with MIN/MAX the losing component is 0 |

Booking: `BackTest.transaction_costs = {d: -Σ tce.get_final_cost() for d, tces in transaction_cost_entries.items()}` (`generic_engine.py:867–870`) — negative numbers, includes dates beyond `end` (even `date.max`); `result_summary` takes `cumsum` then slices to `states[-1]`.

Important: `ConstantTransactionModel(1)` on an instrument of any notional costs exactly 1 on entry and 1 on exit. For a hedge portfolio of K instruments the cost is K (Σ over instruments). With `ScaledTransactionModel('notional_amount', 0.0001)` cost = `0.0001 * |notional * additional_scaling|` (1bp of notional).

---

## 3. `GenericEngine` public API

### 3.1 Constructor `[introspected]`

```python
GenericEngine(action_impl_map=None, price_measure=Price)
```
- `action_impl_map`: `{ActionType: HandlerClass}` merged over the defaults (user wins).
- `price_measure`: the risk measure used as "PV" for cash payments and `result_summary` Total. `Price = RiskMeasureWithCurrencyParameter(name="Price", measure_type=RiskMeasureType("PV"))` (`target/measures.py:529`) — present value, callable with `currency=` to get a converted measure.
- Instance state: `_pricing_context_params=None`, `_initial_pricing_context=None`, `_tracing_enabled=False`.
- `get_action_handler(action)` → `GenericEngineActionFactory(self.action_impl_map).get_action_handler(action)` (new factory + new handler every call).
- `supports_strategy(strategy)` → flattens `t.actions` over `strategy.triggers` and returns `False` if any action has no handler (raises `TypeError` from `reduce` if `strategy.triggers` is empty).
- `new_pricing_context()` → `PricingContext(set_parameters_only=True, show_progress, csa_term, market_data_location, request_priority=5, is_batch (default True), use_historical_diddles_only=True)`, with `_max_concurrent=1500`, `_dates_per_batch=200`. Server-only; no v2 equivalent needed.

### 3.2 `run_backtest` — exact signature `[introspected]`

```python
def run_backtest(self, strategy: Strategy, start: Optional[dt.date] = None, end: Optional[dt.date] = None,
                 frequency: Optional[str] = '1m', states: Optional[Iterable[dt.date]] = None,
                 risks: Optional[Iterable[RiskMeasure]] = None, show_progress: bool = True,
                 csa_term: Optional[str] = None, visible_to_gs: bool = False, initial_value: float = 0,
                 result_ccy: Optional[Union[str, Currency]] = None, holiday_calendar: Optional[str] = None,
                 market_data_location: Optional[str] = None, is_batch: bool = True,
                 calc_risk_at_trade_exits: bool = False, pnl_explain: Optional[PnlDefinition] = None) -> BackTest
```

| Param | Default | Exact meaning |
|---|---|---|
| `strategy` | required | `Strategy(initial_portfolio=None, triggers=None, cash_accrual=None)`; `strategy.risks` = concat of each trigger's `.risks` (action risks + StrategyRiskTrigger's risk). |
| `start` | `None` | base date of the `RelativeDateSchedule`; `None` → today (or ambient PricingContext date). **Always included** in the grid even if it is a weekend/holiday. |
| `end` | `None` | schedule stops when the next generated date `> end`. `None` → schedule is just `[start]`. |
| `frequency` | `'1m'` | tenor string `'<int><unit>'`; generated dates are `RelativeDate(f'{k*n}{u}', start)` for k=1,2,… (each computed from `start`, not chained). All GS examples pass `'1b'`. |
| `states` | `None` | explicit list of dates; if given, **replaces** start/end/frequency. The list object is sorted and extended **in place** (caller's list mutated). |
| `risks` | `None` | extra risk measures to compute every day for every held position. Final set = `list(set(make_list(risks) + strategy.risks + pnl_risks + [price_measure]))` — **deduped via `set`, order not deterministic**. |
| `show_progress` | `True` | server progress bar only. |
| `csa_term` | `None` | discounting CSA for all server calcs (ambient). HedgeAction's own `csa_term` overrides for hedge resolution. |
| `visible_to_gs` | `False` | stored in `_pricing_context_params` but **not used** by `new_pricing_context`. |
| `initial_value` | `0` | initial cash; seeded into `cash_dict` **only on the first cash-payment date**, under the currency of the first payment processed there (§4.8). |
| `result_ccy` | `None` | if set: every risk `r` must be a `ParameterisedRiskMeasure` and is replaced by `r(currency=result_ccy)`; else `RuntimeError(f'Unparameterised risk: {r}')`. `price_measure` likewise → `price_risk`. This is the only multi-currency mechanism. |
| `holiday_calendar` | `None` | typed `Optional[str]` but actually used as a **list of dates** for the grid (`apply_rule(holiday_calendar=...)`) and initial-portfolio durations; stored on `BackTest.holiday_calendar`. Actions use their own `holiday_calendar`. |
| `market_data_location` | `None` | server market location (affects close-market date/timezone). |
| `is_batch` | `True` | server transport (websockets). |
| `calc_risk_at_trade_exits` | `False` | if True, positions exiting on a date are also priced for all `risks` on the exit date into `backtest.trade_exit_risk_results[date]` (for PnL explain). Forced `True` when `pnl_explain` is given. |
| `pnl_explain` | `None` | `PnlDefinition(attributes=[PnlAttribute(attribute_name, attribute_metric, market_data_metric, scaling_factor, second_order=False)])`; adds both metrics of every attribute to `risks`. |

**Discrepancies vs the task brief:** there is **no** `calc_risk_at_trade` parameter (it is `calc_risk_at_trade_exits`), and **`cash_accrual` is not a `run_backtest` parameter** — it is `Strategy.cash_accrual: CashAccrualModel = None` (`strategy.py:177`), passed internally to `_handle_cash`.

Returns a `BackTest`. Fields the engine fills: `portfolio_dict: defaultdict(Portfolio)` (date → held positions), `results: defaultdict(list)` (date → `PortfolioRiskResult`), `cash_payments: defaultdict(list)` (date → `[CashPayment]`), `transaction_cost_entries: defaultdict(list)`, `transaction_costs: dict`, `hedges: defaultdict(list)` (create date → `[Hedge]`), `cash_dict: dict` (date → `{ccy: cash}`), `trade_exit_risk_results`, `calc_calls`, `calculations` (counters only). Constructor: `BackTest(strategy, states, risks, price_measure, holiday_calendar=None, pnl_explain_def=None)`; `__post_init__` **deep-copies the strategy**.

### 3.3 Support objects the loop manipulates

| Object | Constructor | Fields |
|---|---|---|
| `CashPayment` (`backtest_objects.py:559`) | `CashPayment(trade, effective_date=None, direction=1, transaction_cost_entry=None)` | `trade` (instrument or Portfolio), `effective_date`, `direction` (`-1` entry = pay PV, `+1` exit = receive PV, `0` = netted), `cash_paid: defaultdict(float)` (ccy → amount, filled in `_handle_cash`), `transaction_cost_entry` |
| `ScalingPortfolio` (`:373`) | `ScalingPortfolio(trade, dates, risk, csa_term=None, risk_transformation=None, risk_percentage=100)` | + `results = None` (filled with hedge-instrument risks over `dates`) |
| `Hedge` (`:576`) | `Hedge(scaling_portfolio, entry_payment, exit_payment)` | `exit_payment` may be `None` |
| `ConstantCashAccrualModel` (`:726`) | `rate: float = 0`, `annual: bool = True` | `get_accrued_value((cash_dict, from_date), to_date)` → each ccy `value * (1 + rate/(365 if annual else 1)) ** (to - from).days` |
| `DataCashAccrualModel` (`:743`) | `data_source: DataSource = None`, `annual: bool = True` | same formula with `rate = data_source.get_data(from_state)` |
| `OisFixingCashAccrualModel` (`:765`) | `start_date='-1y'`, `end_date=today` | **server**: prices an OIS `IRSwap` `Cashflows` to get fixings per currency, then `DataCashAccrualModel` (note: returns inside the loop → only the first currency is accrued) |

---

## 4. The engine event loop (exact pseudocode of `run_backtest` → `__run`, `generic_engine.py:679–873`)

Notation: `D` = sorted date grid; `S` = strategy; `B` = BackTest; `P(d, trades, measures)` = "price `trades` on the close market of date `d` for `measures`" (server). "Same-day market" everywhere: `PricingContext(pricing_date=d)` → `CloseMarket(date=close_market_date(location, d))` which is `d` itself unless `d` is today and the close has not rolled yet (then previous business day). **There is no next-day execution anywhere in GenericEngine.**

### Phase 0 — setup (679–733)
0.1 Store server context params; open `new_pricing_context()` (server only) and call `__run(...)` inside it.

### Phase 1 — date grid and risk list (771–814)
1.1 `D = RelativeDateSchedule(frequency, start, end).apply_rule(holiday_calendar=holiday_calendar)` if `states is None` else `states`. Schedule = `[start] + [RelativeDate(f'{k*n}{unit}', start).apply_rule(...) for k = 1, 2, ... while result <= end]`.
1.2 `D.sort()`; `start_d = D[0]`; `end_d = D[-1]`.
1.3 For each trigger in `S.triggers` (in order): `D += [t for t in trigger.get_trigger_times() if start_d <= t <= end_d]`. (Periodic trigger times come from their own `RelativeDateSchedule(frequency, start_date, end_date)` with the trigger's `calendar`; Date trigger → its dates; Event trigger → server dataset; most others → `[]`.)
1.4 `D = sorted(set(D))`.
1.5 If `pnl_explain`: `calc_risk_at_trade_exits = True`, `pnl_risks = pnl_explain.get_risks()`, else `[]`.
1.6 `risks = list(set(make_list(risks) + S.risks + pnl_risks + [price_measure]))`.
1.7 If `result_ccy`: `risks = [r(currency=result_ccy) ...]` (raise if unparameterised); `price_risk = price_measure(currency=result_ccy)` (raise if unparameterised). Else `price_risk = price_measure`.
1.8 `B = BackTest(S, D, risks, price_risk, holiday_calendar, pnl_explain)`.

### Phase 2 — initial portfolio (`_resolve_initial_portfolio`, 875–907)
2.1 If `S.initial_portfolio` is a **dict** `{date: priceable(s)}`: sort keys; for key `k_i` recurse with `portfolio = make_list(value)`, `strategy_start_date = k_i`, `duration = k_{i+1}` (or `D[-1]` for the last key). (Keys are not clipped to `[start_d, end_d]` here.)
2.2 Else (list, possibly empty): for each instrument `inst_j`:
   a. `renamed = inst_j.clone(name=f'{inst_j.name}_{start:%Y-%m-%d}')` (R6).
   b. Append `CashPayment(renamed, effective_date=start, direction=-1)` to `B.cash_payments[start]` (**no transaction cost entry**).
   c. `final = get_final_date(renamed, start, duration, holiday_calendar)` (duration `None` → `date.max`).
   d. Append `CashPayment(inst_j, effective_date=final)` (the **original, un-renamed, unresolved** instrument, direction +1) to `B.cash_payments[final]`.
2.3 `init_port = Portfolio(renamed list)`; **server**: `with PricingContext(start): init_port.resolve()` (in place; ATM strikes/rates fixed on `start`).
2.4 For every `d in D`: if `duration is None` **or** (`d >= start` and (`d < duration` or `duration == D[-1]`)): `B.portfolio_dict[d].append(init_port.instruments)`. (List case with `duration=None` adds to **every** grid date.)

### Phase 3 — build simple & semi-path-dependent triggers/actions (`_build_simple_and_semi_triggers_and_actions`, 909–940)
For each `trigger` in `S.triggers` **in list order**, if `trigger.calc_type != path_dependent`:
3.1 `triggered_dates = []`; `trigger_infos = defaultdict(list)`.
3.2 For each `d in D` in ascending order: `t_info = trigger.has_triggered(d, B)`; if truthy: append `d` to `triggered_dates`; if `t_info.info_dict`: for each `(ActionType, info)` append `info` to `trigger_infos[ActionType]`. (Stateful triggers — e.g. MeanReversion — therefore see dates strictly in order, once each. `B.results` is still empty here, so a simple trigger must not depend on risk.)
3.3 For each `action` in `trigger.actions` **in list order**, if `action.calc_type != path_dependent` (i.e. simple or semi):
   - `trigger_info = trigger_infos[type(action)]` if present, else the first entry whose key `isinstance`-matches, else `None`.
   - `get_action_handler(action).apply_action(triggered_dates, B, trigger_info)` — **one call with the full list of triggered dates**.
   - Alignment: the info list is zipped with `triggered_dates` via `zip_longest`; if some triggered dates carried no `info_dict` the list is shorter and **mis-aligned** (earlier dates get later infos; the tail gets `None`).
3.4 Path-dependent actions under a non-path trigger are skipped here (handled in Phase 5).

After Phase 3 (825–836): `B.portfolio_dict` and `B.hedges` are filtered to keys in `[start_d, end_d]` (portfolio_dict re-wrapped as `defaultdict(Portfolio, ...)`).

### Phase 4 — price everything known so far (`_price_semi_det_triggers`, 942–959) — **server**
4.1 In one batch: for every `day, portfolio` in `B.portfolio_dict` (only `dt.date` keys): `B.add_results(day, P(day, portfolio, tuple(risks)))`.
4.2 For every hedge list in `B.hedges`, for every `ScalingPortfolio p`: `p.results = HistoricalPricingContext(dates=p.dates)` calc of `p.trade` (as Portfolio) for `tuple(risks)` — i.e. the **unscaled** hedge instrument priced on every day it will be held.

### Phase 5 — per-date loop for path-dependent logic and hedges (`_process_triggers_and_actions_for_date`, 981–1061)
For each `d in D` ascending:
5.1 For each `trigger` in `S.triggers` in order:
   - If `trigger.calc_type == path_dependent`: if `trigger.has_triggered(d, B)` (**evaluated before any fresh pricing for d**): for each action in order: `ensure(d)`; `get_action_handler(action).apply_action(d, B)` — **no trigger_info passed** (so `scaling` from info is lost and `'next schedule'` duration raises).
   - Else: for each action with `action.calc_type == path_dependent` (ExitAllPositions, Rebalance): `if trigger.has_triggered(d, B)` (**re-evaluated per action**): `ensure(d)`; `apply_action(d, B)`.
5.2 If `d not in B.hedges`: continue to next date.
5.3 For each hedge in `B.hedges[d]` with `sp.results is None`: compute as 4.2 (server).
5.4 If `B.hedges[d]` non-empty:
   a. `ensure(d)` — prices any position in `portfolio_dict[d]` not yet in `results[d]` (this is where hedges added on earlier dates and path-dependent trades get priced for `d`).
   b. If `d not in B.results`: continue (no risk to hedge → **hedges for d are silently dropped**).
   c. For each `hedge` in `B.hedges[d]` (in creation order) — **all sized against the same `results[d]`**, hedges created on `d` do not see each other:
      1. `current_risk = results[d][p.risk].transform(p.risk_transformation).aggregate(allow_mismatch_risk_keys=True)` — total portfolio risk on `d` (all held positions incl. earlier hedges still alive).
      2. `hedge_risk = p.results[d][p.risk].transform(p.risk_transformation).aggregate()` — risk of one unit of the hedge portfolio on `d`.
      3. If `hedge_risk == 0`: skip this hedge.
      4. If `current_risk.unit != hedge_risk.unit`: `RuntimeError('cannot hedge in a different currency')`.
      5. `scaling_factor = current_risk / hedge_risk * p.risk_percentage / 100`.
      6. `entry_payment.transaction_cost_entry.additional_scaling = scaling_factor`; same for exit payment TCE if exit payment exists.
      7. `p.trade` must be a `Portfolio` (always true for HedgeAction) else `RuntimeError('Hedge trade instrument must be a Portfolio')`. `scaled = deepcopy(p.trade)`; rename R5; `scaled.scale(scaling_factor * -1)` (**opposite direction**; IRSwap scaling: `notional_amount *= |f|`, flip `pay_or_receive` if `f<0`, `fee *= -1` if `f<0`).
      8. For every `day in p.dates`: `B.portfolio_dict[day] += deepcopy(scaled)` (Portfolio `__add__` concatenates top-level priceables → the scaled **instruments** are added as leaves).
      9. `entry_payment.trade = deepcopy(scaled)`; `exit_payment.trade = deepcopy(scaled)` if exists.
      10. Append `entry_payment` to `B.cash_payments[entry.effective_date]` and `exit_payment` (if any) to `B.cash_payments[exit.effective_date]`.

`ensure(d)` = `__ensure_risk_results([d], B, risks)` (961–979): collect `t in portfolio_dict[d]` with `not results[d]` or `t.name not in results[d].portfolio`; if any: `B.add_results(d, P(d, those, tuple(risks)))`.

### Phase 6 — price newly added positions (`_calc_new_trades`, 1063–1091) — **server**
6.1 For every `day, portfolio` in `B.portfolio_dict` with non-empty portfolio: leaves = top-level priceables whose `name not in results[day].portfolio` (or all if no result). Batch `P(day, leaves, tuple(risks))`; then `B.add_results(day, ...)` (merged into existing `PortfolioRiskResult` via `+`).

### Phase 7 — cash (`_handle_cash`, 1093–1175)
7.1 Collect extra pricing needs: for each CashPayment `cp` (all dates), for each instrument `trade` in `cp.trade` (flattened if Portfolio): if `cp.effective_date` and `cp.effective_date <= end_d` and (`effective_date not in results` or `trade not in results[effective_date]`): add to `cash_trades_by_date[effective_date]`; if `calc_risk_at_trade_exits and cp.direction == 1`: also to `exited_cash_trades_by_date`.
   (Entry payments normally find their trade in `results[create_date]`; exit payments normally do not — the trade is not held on its exit date — so they are priced here.)
7.2 **Server**: for each date: `cash_results[date] = P(date, trades, price_risk)`; if exit risks wanted: `B.trade_exit_risk_results[date] = P(date, expiring_trades, risks)`.
7.3 Walk `for d in sorted(set(D + list(B.cash_payments.keys())))` with `d <= end_d`, `current_value = None` initially:
   a. If `current_value is not None`: `B.cash_dict[d] = current_value[0]` if `cash_accrual is None` else `cash_accrual.get_accrued_value(current_value, d)` — where `current_value = (cash dict snapshot, date of the last cash payment)`, so accrual compounds from the **last payment date**, not the previous grid date.
   b. If `d in B.cash_payments`: for each `cp` in order, for each instrument `trade` in `cp.trade`:
      - `value = cash_results[cp.effective_date][price_risk][trade.name]` if present, else `B.results[cp.effective_date][price_risk][trade.name]`; `KeyError/ValueError` or non-float → `RuntimeError(f'failed to get cash value for {trade.name} on {date} received value of {value}')`.
      - `ccy = map_ccy_name_to_ccy(next(iter(value.unit)))` (maps long names like `'United States Dollar'`→`'USD'`; an unmapped key yields `None`). **Unverifiable offline** whether the server's unit key is the long name or ISO code.
      - If `d not in B.cash_dict`: `B.cash_dict[d] = {ccy: initial_value}` (only possible on the first payment date). If `ccy` missing: set 0.
      - `cp.cash_paid[ccy] += value * cp.direction`.
      Then for each `(ccy, amt)` in `cp.cash_paid`: `B.cash_dict[d][ccy] += amt`.
      After all payments on `d`: `current_value = (B.cash_dict[d], d)`.
   c. `current_value = deepcopy(current_value)`.
   Consequences: no `cash_dict` entries before the first payment date; if there are no payments at all, `initial_value` never appears.

### Phase 8 — transaction costs (866–870)
`B.transaction_costs = {d: -Σ tce.get_final_cost() for d, tces in B.transaction_cost_entries.items()}`.

### Output assembly (`BackTest.result_summary`, `backtest_objects.py:209–235`)
- Risk columns: per date with results, per risk: `results[d][risk].aggregate(True, True)` (sum over all held positions).
- `'Cumulative Cash'` column from `cash_dict` (`RuntimeError('Cannot aggregate cash in multiple currencies')` if >1 ccy key anywhere).
- `'Transaction Costs'` = cumulative sum of `transaction_costs` sorted by date.
- `concat(...).ffill().fillna(0)`; `'Total' = df[price_measure] + df['Cumulative Cash'] + df['Transaction Costs']`; sliced `df[:states[-1]]`.
- `risk_summary` = same risk table with zeros on cash-only dates; `trade_ledger()` builds `{name: Open, Close, Open Value, Close Value, Long Short, Status, Trade PnL}` from cash payments (direction 0 → closed with zeros).

### 4.4 Scheduling matrix (which phase runs an action)

| Trigger `calc_type` \ Action `calc_type` | simple (AddTrade, AddScaled, ExitTrade) | semi_path_dependent (Hedge) | path_dependent (ExitAllPositions, Rebalance) |
|---|---|---|---|
| simple / semi (Periodic, Date, Mkt, MeanReversion, Event, Portfolio, Not, Aggregate-of-simple) | Phase 3, once, all triggered dates, with trigger_info | Phase 3 creates `Hedge` objects; Phase 4/5 price and size them | Phase 5 per date, `has_triggered` re-evaluated per action, no trigger_info |
| path_dependent (StrategyRisk, TradeCount, Aggregate containing one) | Phase 5 per date, no trigger_info | Phase 5 per date (creates Hedge for `d`; sized later the same day in 5.4 because `B.hedges[d]` is appended before 5.2 runs) | Phase 5 per date |

Trigger `calc_type` values: `TriggerRequirements.calc_type` default `simple`; `RiskTriggerRequirements` and `TradeCountTriggerRequirements` → `path_dependent`; `AggregateTriggerRequirements` → max over children (path > semi > simple). `Trigger.calc_type` delegates to `trigger_requirements`. Trigger info producers: `PeriodicTriggerRequirements`, `DateTriggerRequirements`, `EventTriggerRequirements` return `info_dict = {AddTradeAction: AddTradeActionInfo(scaling=None, next_schedule=next), AddScaledTradeAction: AddScaledTradeActionInfo(next_schedule=next), HedgeAction: HedgeActionInfo(next_schedule=next)}` where `next` = the following trigger date or `None` for the last; `MeanReversionTriggerRequirements` intends to return `{AddTradeAction: AddTradeActionInfo(scaling=±1)}`, but `AddTradeActionInfo` has no field defaults, so that constructor call raises `TypeError: AddTradeActionInfo.__new__() missing 1 required positional argument: 'next_schedule'` the first time the trigger fires `[verified by executing the namedtuple constructor on 1.5.4; see Q7]`. Full trigger coverage belongs in the triggers research note.

---

## 5. Action handlers in detail (`generic_engine.py`)

### 5.1 `OrderBasedActionImpl` (82–98)
- `__init__`: `self._order_valuations = [ResolvedInstrumentValues]`.
- `get_base_orders_for_states(states, **kwargs)`: **server**; in one batch, for each `s`: `orders[s] = Portfolio(dated_priceables.get(s) or action.priceables).calc(tuple(_order_valuations))` under `PricingContext(pricing_date=s)`. `ResolvedInstrumentValues` returns the instrument with every "resolvable" field fixed on `s`'s close market: `fixed_rate='ATM'` → par rate, relative dates (`termination_date='10y'`, `effective_date`, `expiration_date='1m'`) → absolute dates, strikes, premium/fee, etc. The resolved instrument keeps `.unresolved` (copy of the original) and `.resolution_key` (`base.py:619–634`).
- `get_instrument_final_date(inst, order_date, info)` → `get_final_date(inst, order_date, action.trade_duration, action.holiday_calendar, info)`.

### 5.2 `AddTradeActionImpl` (101–162)
`_raise_order(state, trigger_info)`:
1. `state_list = make_list(state)`; if `trigger_info` is `None` or a single `AddTradeActionInfo` → replicate per state. `ti_by_state = dict(zip_longest(state_list, trigger_info))`.
2. `orders = get_base_orders_for_states(state_list)` (resolve on each create date).
3. For each `d, p`: `new_port = Portfolio([t.clone(name=f'{t.name}_{d}') for t in p.result()])`; `final_orders[d] = (new_port.scale(None if ti is None else ti.scaling, in_place=False), ti)` — `scale(None)` is identity; `scale(-1)` flips direction (MeanReversion).

`apply_action(state, backtest, trigger_info=None)`:
For each `create_date, (portfolio, info)`; for each `inst in portfolio.all_instruments`:
1. `tc_enter = TransactionCostEntry(create_date, inst, action.transaction_cost)`; `cash_payments[create_date].append(CashPayment(inst, effective_date=create_date, direction=-1, transaction_cost_entry=tc_enter))`; `transaction_cost_entries[create_date].append(tc_enter)`.
2. `final_date = get_instrument_final_date(inst, create_date, info)`.
3. `tc_exit = TransactionCostEntry(final_date, inst, action.transaction_cost_exit)`; `cash_payments[final_date].append(CashPayment(inst, effective_date=final_date, transaction_cost_entry=tc_exit))`; `transaction_cost_entries[final_date].append(tc_exit)` — booked **even when `final_date = date.max`** (ignored later by the `<= end` filters).
4. For `s in backtest.states` with `final_date > s >= create_date`: `portfolio_dict[s].append(inst)`.
Then (server, batch) `tce.calculate_unit_cost()` for all entries created.

### 5.3 `AddScaledTradeActionImpl` (165–375)
`__init__`: `_scaling_level_signal = interpolate_signal(scaling_level)` if `scaling_level` is a dict, else `None`. `interpolate_signal` builds a daily calendar-day series from min to max key with **STEP** interpolation (carry last known value forward).
`_scaling_level_for_date(d)`: signal present → `signal[d]` if `d` in the signal's range else **0**; else the scalar `scaling_level`.

`_raise_order(state_list, price_measure, trigger_infos)`:
1. If `scaling_type == risk_measure`: `_order_valuations.append(scaling_risk)` → resolve **and** compute `scaling_risk` of the unscaled resolved trade in the same call.
2. `orders = get_base_orders_for_states(state_list)`.
3. For each date `d`: for each `inst in action.priceables`: `new_inst = res[inst]` (→ `[ResolvedInstrumentValues]` if two measures); `new_inst.name = f'{new_inst.name}_{d}'`; `final_orders[d] = Portfolio(new_port)`.
4. `daily_risk[d] = res[scaling_risk].aggregate()` if risk scaling (sum across the priceables).
5. `_scale_order(final_orders, daily_risk, price_measure, trigger_infos)`:

| `scaling_type` | Rule (in-place `portfolio.scale`) |
|---|---|
| `size` | `portfolio.scale(_scaling_level_for_date(day))` — multiply size (notional/quantity) by level; dict levels outside the signal range → scale 0 (a zero-size trade is still booked) |
| `risk_measure` | `portfolio.scale(_scaling_level_for_date(day) / daily_risk[day])` — whole portfolio scaled so that Σ `scaling_risk` = level |
| `NAV` | `_nav_scale_orders` (below) |
| other | `RuntimeError(f'Scaling Type {scaling_type} not supported by engine')` |

`_nav_scale_orders(orders, price_measure, trigger_infos)` (206–280) — spend a cash budget, then recycle unwind proceeds:
1. For every order date and instrument: entry TCE at create date, exit TCE at its final date; `final_days_orders[final_date] += [inst]`.
2. Server batch: `unscaled_prices_by_day[day] = P(day, portfolio, price_measure)`; `unscaled_unwind_prices_by_day[d] = P(d, unwind insts, price_measure)` **only if `d <= today`**; `calculate_unit_cost()` on all TCEs.
3. `available_cash = action.scaling_level` (must be scalar). For each order day in ascending order:
   - `scale = __portfolio_scaling_for_available_cash(...)`: `f1 = (cash - fixed_tc) / (Σ unscaled price + scaled_tc)`; if `f1 == 0` → 0; set each entry TCE `additional_scaling = f1`, recompute components; `f2 = max(cash - fixed_tc, 0) / (Σ price * f1 + scaled_tc)`; return `f1 * f2`.
   - record factor per day and per instrument; `available_cash = 0`.
   - If there is a next order day: for every final day `fd` with `cur < fd <= next`: for each inst: `available_cash += unwind_price[fd][inst] * factor[inst]`; set its exit TCE `additional_scaling = factor[inst]`; `available_cash -= exit_tce.get_final_cost()`. Then `available_cash = max(available_cash, 0)`.
4. Days with factor 0 are **deleted** from `orders`; others `orders[day].scale(factor)`.

`apply_action(state, backtest, trigger_info=None)`: identical cash/TCE/portfolio booking to AddTrade (§5.2 steps 1–4) using `backtest.price_measure` for NAV; then server batch `calculate_unit_cost()` (note: NAV-scaled TCEs are recreated fresh here, so `additional_scaling` from NAV solving is **not** carried over — scaled costs are recomputed on the already-scaled instrument, which is equivalent).

### 5.4 `HedgeActionImpl` (378–447)
`get_base_orders_for_states(states)`: **server** — `with HistoricalPricingContext(dates=states, csa_term=action.csa_term): f = Portfolio(action.priceable).resolve(in_place=False)`; returns `f.result()` = `{date: Portfolio}` where each value's `priceables[0]` is the resolved hedge Portfolio (the action's Portfolio nested in a wrapper Portfolio).

`apply_action(state, backtest, trigger_info=None)`:
1. Normalise `trigger_infos` as AddScaled; `calc_calls += 1`; `calculations += len(states)`.
2. `orders = get_base_orders_for_states(state_list)`.
3. For each `create_date, portfolio`:
   a. `hedge_trade = portfolio.priceables[0]`; rename R4.
   b. `final_date = get_instrument_final_date(hedge_trade, create_date, info)` (attribute durations are looked up **on the Portfolio**, so `'termination_date'` etc. do not work — `hasattr` fails and it falls through to `RelativeDate('termination_date')` → error).
   c. `active_dates = [s for s in backtest.states if create_date <= s < final_date]`; if empty → nothing booked for that date.
   d. `ScalingPortfolio(trade=hedge_trade, dates=active_dates, risk=action.risk, csa_term=action.csa_term, risk_transformation=action.risk_transformation, risk_percentage=action.risk_percentage)`.
   e. `tc_enter = TCE(create_date, hedge_trade, transaction_cost)`; `entry_payment = CashPayment(hedge_trade, create_date, direction=-1, tce=tc_enter)`; `transaction_cost_entries[create_date].append(tc_enter)`.
   f. `tc_exit = TCE(final_date, hedge_trade, transaction_cost_exit)`; `exit_payment = CashPayment(hedge_trade, final_date, tce=tc_exit)` **only if `final_date <= today`**, else `None`; `transaction_cost_entries[final_date].append(tc_exit)` **always**.
   g. `backtest.hedges[create_date].append(Hedge(sp, entry_payment, exit_payment))`.
4. Server batch `calculate_unit_cost()`.
**Cash payments are NOT appended here** — they are appended in Phase 5.4c.10 only if the hedge is actually sized. TCEs **are** appended here → a dropped/skipped hedge still books its transaction costs at `additional_scaling = 1` (Constant model: full cost; Scaled model: cost of one unscaled hedge unit).

**Hedge algorithm summary:** on each hedge create date `d`, hedge ratio `h = (Σ_{held positions on d} risk) / (risk of 1 unit of hedge instrument on d) × risk_percentage/100`; the hedge position `−h × hedge_instrument` (resolved on `d`, e.g. ATM swap) is added to every state in `[d, final_date)`; entry cash `+h·PV_hedge(d)` (direction −1 on the scaled/flipped trade), exit cash on `final_date`. With `trade_duration=None` hedges accumulate (each new one neutralises the residual including older hedges); with `'next schedule'` each hedge is unwound on the next trigger date — **on the same date a new hedge is created, the old hedge is already out of the portfolio** (held `s < final_date`), so the new hedge is sized to the unhedged risk.

### 5.5 `ExitTradeActionImpl` (450–549)
For each `s` in `make_list(state)`:
1. If `priceable_names is None`: `current_trade_names = [i.name for i in portfolio_dict[s].all_instruments]`.
2. For each `port_date` in `backtest.states` with `port_date >= s` and `type(port_date) is dt.date`:
   - `pos_fut = list(portfolio_dict[port_date].all_instruments)`; if `results[port_date]` truthy: `res_fut`, `res_futures` from the existing result (accessing `results[port_date]` on the defaultdict **creates an empty `[]` entry**).
   - Indexes to remove: names mode → trade date `<= s` and `split('_')[-2] in priceable_names`; all mode → `name in current_trade_names`.
   - Delete from positions (collect removed instruments into `trades_to_remove`, duplicates possible) and from results; rewrite `portfolio_dict[port_date] = Portfolio(tuple(pos_fut))` and, if needed, `results[port_date] = PortfolioRiskResult(Portfolio(res_fut), risk_measures, res_futures)`.
3. Move future cash payments: for every `cp_date > s`, for each cp whose `trade.name` is in the removed names: if a cp for the same name already exists on `s` → `existing.direction += cp.direction` (entry −1 + exit +1 = **0**, i.e. same-day in/out nets to nothing); else `cp.effective_date = s` and append to `cash_payments[s]`. Move its TCE from `transaction_cost_entries[cp_date]` to `[s]` and set `tce.date = s`. Delete the cp from `cp_date`; delete empty date keys.
4. For each removed trade with no cp on `s` yet (e.g. initial-portfolio positions, whose exit cp holds the un-renamed instrument): find a TCE on `s` whose instrument dicts (`to_dict()` omits name) match, and append `CashPayment(trade, effective_date=s, transaction_cost_entry=tce_or_None)`.
Net effect: exit on trigger date `s`: the trade leaves the portfolio from `s` (inclusive), and receives `+PV(s)` cash on `s` priced in Phase 7.

Because `ExitTradeAction` is **simple**, under a simple trigger it runs in Phase 3 over all triggered dates **before any pricing** and only sees positions added by earlier triggers/actions in list order. `ExitAllPositionsAction` runs in Phase 5 per date after `ensure(d)`.

### 5.6 `RebalanceActionImpl` (552–610) — path-dependent, per date
1. `new_size = action.method(state, backtest, trigger_info)` (trigger_info is `None` in Phase 5).
2. `current_size = Σ getattr(trade, size_parameter)` over `portfolio_dict[state]` trades whose name contains `priceable.name.split('_')[-1]`.
3. If `new_size - current_size == 0` → return.
4. `pos = priceable.clone(size_parameter=new_size - current_size, name=f'{priceable.name}_{state}')` (clone of the pre-resolved priceable; no re-resolve on `state`).
5. Entry TCE + `CashPayment(pos, state, direction=-1)`.
6. Find the **latest** cash-payment date `d` holding a direction-+1 payment whose trade name contains the token; book `CashPayment(pos, effective_date=d, tce=tc_exit)` there (bug: `transaction_cost_entries[d].append(exit)` appends the Python builtin `exit`, not `tc_exit`, → later `get_final_cost` on it raises `AttributeError`). None found → `ValueError("Found no final cash payment to rebalance for trade.")`.
7. Add `pos` to `portfolio_dict[s]` for `state <= s < d`.
8. Server batch `calculate_unit_cost()`.

---

## 6. Server-side vs local

### 6.1 Every GS server touch-point (must be replaced in v2 by asset-config expressions)

| Site | Call | What is computed | v2 replacement concept |
|---|---|---|---|
| `generic_engine.py:90–94` | `PricingContext(pricing_date=s)`; `Portfolio.calc(ResolvedInstrumentValues[, scaling_risk])` | resolve trade on create date (ATM rate, dates) [+ scaling risk] | trade-construction expression on `market(s)` + named risk function |
| `:155–160, 368–373, 440–445, 603–608` | `TransactionCostEntry.calculate_unit_cost()` → `ScaledTransactionModel.get_unit_cost` → `PricingContext(state): instrument.calc(risk)` (`backtest_objects.py:416–420`) | risk-based transaction cost | named pricing function on `(market(state), trade)` |
| `:227–240` | `PricingContext(pricing_date=day)` `portfolio.calc(price_measure)` | NAV scaling prices (entry & unwind) | `npv` function |
| `:383–385` | `HistoricalPricingContext(dates, csa_term)` `Portfolio.resolve(in_place=False)` | resolve hedge instrument on each create date | trade construction |
| `:773` | `RelativeDateSchedule.apply_rule` → `GsCalendar` **if `holiday_calendar` is None** | business-day calendar | local calendar list (default: weekends only) |
| `backtest_utils.py:88` | `RelativeDate.apply_rule` (same calendar fallback) | tenor durations | local date arithmetic |
| `:901–902` | `PricingContext(start)` `init_port.resolve()` | resolve initial portfolio | trade construction |
| `:944–959` | `PricingContext(day)` `portfolio.calc(risks)`; `HistoricalPricingContext(dates)` `calc(risks)` | daily PV + risks of all positions; unit hedge risks | per-trade `npv`/risk functions, per-date market |
| `:973–976` | `ensure` | same, incremental | same |
| `:1004–1007` | hedge results lazily | same | same |
| `:1066–1086` | `_calc_new_trades` | same | same |
| `:1123–1131` | cash pricing `calc(price_risk)`, exit risks `calc(risks)` | exit PVs | `npv` on exit date market |
| `:797–812` | `r(currency=result_ccy)` | server FX conversion of every measure | FX expression (reporting ccy) |
| `backtest_objects.py:790–791` | `IRSwap(...).calc(Cashflows)` | OIS fixings for cash accrual | data source / expression |
| `triggers.py` | `MktTrigger` with `GsDataSource`, `EventTrigger` (`MACRO_EVENTS_CALENDAR`) | trigger data | local `GenericDataSource` |
| `:664–677` | `new_pricing_context` | batching/priority/CSA/location | none |

Also server-dependent semantics: `PortfolioRiskResult.aggregate`, `.transform(risk_transformation)`, `.unit` (currency of a result), `Instrument.scale` (per-instrument-type size field and direction flip).

### 6.2 Pure local logic (port as-is)
Date-grid union/sort; trigger evaluation order and trigger-info plumbing; action dataclasses and naming; `get_final_date` (except calendar source); holding-window membership (`create <= s < final`); cash-payment creation, moving/netting on exit, ledger; hedge ratio arithmetic (`current/hedge × pct/100`, opposite sign); NAV scaling solve; transaction cost aggregation (`get_final_cost`, `get_cost_by_component`); cash walk with accrual; `result_summary`/`risk_summary`/`trade_ledger`/`pnl_explain` table assembly; counters.

---

## 7. Quirks and parity decisions (code-read; each needs a replicate-vs-fix decision by the design author)

| # | Where | Observed behaviour | Recommendation (not a decision) |
|---|---|---|---|
| Q1 | `generic_engine.py:988, 995` | Path-dependent `has_triggered(d)` runs **before** `ensure(d)`, so a `StrategyRiskTrigger` sees `results[d]` from Phase 4 only — positions added path-dependently on earlier dates are missing, and positions exited earlier are pruned. In the `040305` pattern (Exit all + Add on risk breach) after the first breach `results[d]` for later dates no longer contains the new trade, so the trigger likely never fires again. | Fix: price day `d` before evaluating path-dependent triggers (document as deviation). |
| Q2 | `:991, 997` | Phase 5 calls `apply_action(d, backtest)` without trigger_info → `scaling` lost, `'next schedule'` raises `RuntimeError`. **Fixed in 2.1.17** (passes the day's info). | Fix: pass the day's trigger info (matches 2.1.17). |
| Q3 | `:995` | For simple triggers, `has_triggered` is re-evaluated **once per path-dependent action** in Phase 5 (after also being evaluated in Phase 3) → stateful triggers (MeanReversion) advance extra times. | Fix: evaluate each trigger once per date, cache the TriggerInfo. |
| Q4 | `:932–940` | Trigger-info list is not keyed by date; mis-aligns when only some triggered dates carry info. | Fix: key infos by date. |
| Q5 | `:1017`, `:421–434` | Hedge skipped when `d not in results` or `hedge_risk == 0`, but its TCEs were already booked (unscaled). Hedges on the same day are sized against the same pre-hedge risk. | Replicate same-day independence (document); drop TCE for skipped hedges. |
| Q6 | `:898` | Dict/list initial portfolio exit payment uses the **original un-renamed unresolved** instrument (server re-resolves it on the exit date → e.g. ATM swap worth ≈0 at exit). | Fix: use the resolved renamed instrument. |
| Q7 | `triggers.py:362–373` (user's reference notebook `040304`) | `MeanReversionTriggerRequirements`: (a) `AddTradeActionInfo(scaling=±1)` omits required `next_schedule` → `TypeError` on first fire (verified: `AddTradeActionInfo._field_defaults == {}`), so the reference notebook cannot run on installed 1.5.4 as written; (b) the long-exit branch sets `self._current_position = 0` (typo) so the position stays `1` and every later day with price > mean emits another `scaling=-1` trade; (c) the short-exit branch tests `current_price > rolling_mean` (should be `<`). Trades have `trade_duration=None` so exits are implemented as opposite trades, never unwinds. **All three are fixed in repo 2.1.17** (`triggers.py:354–376` there: `next_schedule=None` passed, `self.current_position = 0`, short exit tests `<`). | Implement the 2.1.17 semantics (it is the only runnable ground truth); cross-check with the triggers note. |
| Q8 | `:475–489` | `ExitTradeAction(priceable_names=...)` parses names by `_`; crashes if hedge positions are held; user-name underscores break matching. | Fix: store action/priceable/date as structured position metadata; keep the name format for display. |
| Q9 | `:591` | Rebalance appends builtin `exit` to TCE list → crash on `get_final_cost`. | Fix. |
| Q10 | `actions.py:380` | `if not Portfolio` bug → bad HedgeAction input fails late with TypeError. | Fix with a clear `ValueError`. |
| Q11 | `backtest_objects.py:219–220` | Multi-currency cash raises unless `result_ccy` given. | v2 requirement: keep per-ccy cash and convert via FX expression to reporting ccy. |
| Q12 | `:1163–1164` | `initial_value` appears only from the first cash-payment date and under that payment's currency; lost if no payments. | Fix: seed on `D[0]` in reporting ccy. |
| Q13 | `:466, 471` | `backtest.results[port_date]` on defaultdict creates `[]` entries → later `d in results` checks become true with an empty list (`RiskTrigger`/hedge `[] [risk]` → TypeError). | Fix: use `.get`. |
| Q14 | `:1162` | Cash currency derived from server result `unit` via long-name map; unmapped → key `None`. | v2: currency comes from the asset config. |
| Q15 | `:795` | Risk list deduped via `set` → column order non-deterministic. | Fix: ordered dedupe. |
| Q16 | `:431` / `:232` | Hedge exit payment and NAV unwind prices only if date `<= today` (server has no future market). | Replicate as "date has market data" check. |
| Q17 | `:405`, `backtest_utils.py:76` | Hedge `trade_duration` attribute names are looked up on the Portfolio, so instrument attributes don't work for hedges. | Fix: look up on the (single) hedge instrument. |
| Q18 | `actions.py:309` | `ExitTradeAction.priceables_names` typo; a bare string `priceable_names` is used with `in` (substring match). | Fix: `make_list` the real field. |

---

## 8. Facts the v2 design must accommodate (no design here, just constraints)

1. Public names/fields/defaults/positional order of the six GenericEngine-supported actions, `ScalingActionType`, `*ActionInfo` namedtuples, `GenericEngine(action_impl_map=None, price_measure=Price)`, and the full `run_backtest` signature above must be reproducible (server-only params can be accepted and ignored: `show_progress`, `visible_to_gs`, `market_data_location`, `is_batch`, `csa_term` may map to an asset-config discount choice).
2. Scaling in gs mutates the instrument (size ×|f|, direction flip on negative). v2 trades built from config expressions are opaque, so scaling must be representable without knowing the instrument (e.g. a position quantity multiplier applied to every pricing output — PV and risks are linear in size for the instruments in scope).
3. Result/ledger logic keys everything by name; the `<Action>_<Priceable>_<YYYY-MM-DD>` and `Scaled_...` formats appear in user-visible output (`trade_ledger`, `strategy_as_time_series`).
4. Risk measures are objects used as DataFrame column keys (`df[Price]`); `result_summary` columns: risk measures, `'Cumulative Cash'`, `'Transaction Costs'`, `'Total'` (class constants `CUMULATIVE_CASH_COLUMN`, `TRANSACTION_COSTS_COLUMN`, `TOTAL_COLUMN`).
5. HedgeAction needs a scalar per-date risk for the whole book and for one unit of hedge — in v2 that is a named risk function aggregated over positions (e.g. DV01 in ccy per bp), plus an optional `risk_transformation` callable, with a same-currency check.
6. Transaction costs: `ScaledTransactionModel(scaling_type=str)` reads an instrument attribute (`notional_amount`) — v2 needs either a trade attribute accessor or a named function; `RiskMeasure` scaling needs a named pricing function.
7. Market for every calc is **the close of the same date**; the engine never looks ahead except `date <= today` guards.

---

## 9. Delta: repo checkout 2.1.17 vs installed 1.5.4 (read-only diff, `diff -w`)

Repo: `C:/Users/chris/clee/gsquant-temp-claude/gs-quant/gs_quant/backtests/` (HEAD `f2a505a Chore: Make release 2.1.17`). Changed-line counts vs 1.5.4: `actions.py` 107, `generic_engine.py` 703 (the action impls moved out), `backtest_objects.py` 250, `backtest_utils.py` 97, `triggers.py` 61, `strategy.py` 8 (typing only), `action_handler.py`/`execution_engine.py` 2 (imports only), `backtest_engine.py` 0.

| Area | 2.1.17 change | Effect on this note |
|---|---|---|
| Module layout | Action impl classes moved to new `generic_engine_action_impls.py` (716 lines); `generic_engine.py` is 730 lines | Same class names; line numbers in §5 refer to 1.5.4 |
| New action `AddWeightedTradeAction` | fields in order: `priceables: Portfolio=None`, `trade_duration: Duration=None`, `name=None`, `scaling_risk: RiskMeasure=None`, `total_size: float=100000.0`, `transaction_cost=ConstantTransactionModel(0)`, `transaction_cost_exit=None`, `holiday_calendar=None`, `class_type='add_weighted_trade_action'`; `_calc_type = semi_path_dependent`; new `AddWeightedTradeActionInfo = namedtuple('AddWeightedActionInfo', 'next_schedule')` | Handled like a hedge: resolve on create dates (`HistoricalPricingContext`), store `WeightedTrade(WeightedScalingPortfolio(trades, dates, risk, total_size), entry_payments, exit_payments)` in `backtest.weighted_trades[create_date]`; in the per-date loop each instrument gets size `|risk_i| / Σ|risk_j| × total_size` on the create date, named `Weighted_<name>`, added for all active dates, with its own cash payments |
| New action `EarlyExitPositionLimitScaledAction(AddScaledTradeAction)` | extra fields `early_exits: Optional[Iterable[dt.date]] = None`, `max_concurrent_pos: Optional[int] = None`, `class_type='early_exit_position_limit_scaled_action'` | final date = `min(normal final date, first early_exit > order date)`; order days that would push open positions above `max_concurrent_pos` are dropped |
| `AddScaledTradeAction` | new dataclass field `dated_priceables: dict[dt.date, Priceable] = None` (between `holiday_calendar` and `class_type`), honoured per date in `_raise_order` | per-date priceable override now also for scaled trades |
| `RebalanceAction.method`, `CustomDuration.function` | JSON encoder/decoder for callables; `CustomDuration.function` now defaults to `None` | serialisation only |
| `trade_duration` fields | `encode_duration`/`decode_duration` (supports date, timedelta, CustomDuration dicts) | serialisation only |
| `get_final_date` | `RelativeDate(duration.lower(), create_date)` | tenor strings case-insensitive |
| `run_backtest` grid | `RelativeDateSchedule(frequency.lower(), ...)`; `PeriodicTriggerRequirements` also lowercases | case-insensitive frequency |
| Per-date loop | path-dependent triggers: `t_info = has_triggered(d)` then `apply_action(d, backtest, trigger_infos.get(type(action)))`; simple trigger + path-dependent action: passes `t_info.info_dict.get(type(action))` | **Q2 fixed.** Q1 (trigger evaluated before `ensure(d)`) and Q3 (re-evaluation per action) remain |
| Factory | defaults dict merged with `action_impl_map` via dict-union (user entries win), plus the two new actions | same override semantics |
| `MeanReversionTriggerRequirements` | passes `next_schedule=None`, sets `self.current_position = 0` on long exit, short exit tests `current_price < rolling_mean` | **Q7 fixed**: enter short when z > bound and price > mean (scaling −1), enter long when z > bound and price ≤ mean (+1); close a long when price > mean (−1), close a short when price < mean (+1) |
| `EventTriggerRequirements` | new optional filters `country`, `currency`, `source`, `start`, `end` | server data only |
| `BackTest` | new `weighted_trades` store; `result_summary` returns empty df safely; new `summary_stats(annualisation_factor=252) -> pd.Series` (Total PnL, Total Transaction Costs, Total Trades, Start/End Date, Duration, Annualised Return/Volatility, Sharpe, Sortino, Max Drawdown (+duration), Calmar, Average Daily PnL, Daily PnL Std Dev, Best/Worst Day, % Positive Days, Skewness, Kurtosis, Peak PnL, Current Drawdown) computed from the `Total` column | pure local; cheap to port if 2.1.17 is the target |
| Unchanged bugs in 2.1.17 | `if not Portfolio` (actions.py:437), `priceables_names` typo (actions.py:366), Rebalance `append(exit)` (impls:567), multi-ccy cash raise (backtest_objects.py:237), `'cannot hedge in a different currency'` (generic_engine.py:512) | Q9, Q10, Q11, Q18 still apply |
