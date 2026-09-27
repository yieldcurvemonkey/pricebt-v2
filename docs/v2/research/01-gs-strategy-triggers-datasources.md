# 01 — gs_quant.backtests: Strategy, Triggers, Data Sources, Orders, Utils (reference for pricebt v2)

## Key facts

1. **Two reference versions disagree, and the installed one is broken for the user's example.** Installed `gs_quant` is **1.5.4** (`C:/Users/chris/anaconda3/Lib/site-packages/gs_quant/backtests`); the repo copy is **2.1.17** (`C:/Users/chris/clee/gsquant-temp-claude/gs-quant/gs_quant/backtests`). In 1.5.4, **every** `MeanReversionTrigger` fire raises `TypeError: AddTradeActionInfo.__new__() missing 1 required positional argument: 'next_schedule'` (verified by running it). **Rule for v2: copy the constructor signatures and the 2.1.17 behaviour.** The signatures are identical in both versions for every class in scope except the two listed in §10 (`EventTriggerRequirements` and `CustomDuration`). §12 lists every place where this note proposes departing from 2.1.17. 2.1.17 passes `next_schedule=None`, fixes the `_current_position` typo, uses `<` in the short-close branch, and lower-cases `frequency`/`duration` strings.
2. **pandas 2.3.1 trap:** `GenericDataSource.get_data_range` does `index < start`. If the series has a `DatetimeIndex` and `start` is a `dt.date`, this raises `TypeError: Invalid comparison between dtype=datetime64[ns] and date`. The mean-reversion notebook (040304) builds exactly that kind of series, so it fails as written in this environment. v2 must normalise the index to one key type (recommended: `dt.date` for daily data, tz-aware or naive `dt.datetime` for intraday data) when the source is built.
3. **Look-ahead bug in `MissingDataStrategy.fill_forward` (do NOT port).** With an object-dtype index of `dt.date`, the missing date is appended at the end and the `sort_index()` result is thrown away. `ffill` then copies the **last** value of the series (measured: returned 5.0 where 2.0 was correct). v2 must insert the date, sort, then fill.
4. **Trigger evaluation depends on `calc_type`.** Triggers with `simple` or `semi_path_dependent` are evaluated in a **pre-pass over all pricing dates, before any risk is computed**. Only `path_dependent` triggers (`RiskTriggerRequirements`, `TradeCountTriggerRequirements`, and an Aggregate that contains one) are evaluated day by day with results available. `MeanReversionTrigger`, `MktTrigger`, `PortfolioTrigger` and `NotTrigger` are all `simple`.
5. **What `scaling` means in `TriggerInfo.info_dict`:** it is a signed multiplier on the action's trade. `None` means no change (identity). `-1` flips the direction; for `IRSwap` that means `notional *= abs(s)` and pay/receive is flipped. A mean-reversion "close" is an **opposite trade added with no `trade_duration`, which nets the position**; nothing is removed. In v2, `scaling` must multiply the signed trade quantity, and pricebt must not interpret it any further.

---

## 0. Scope, sources, conventions

| Item | Value |
|---|---|
| Installed reference (primary line refs) | `C:/Users/chris/anaconda3/Lib/site-packages/gs_quant/backtests/*.py`, gs_quant `1.5.4` |
| Repo reference (drift) | `C:/Users/chris/clee/gsquant-temp-claude/gs-quant/gs_quant/backtests/*.py`, HEAD `f2a505a Chore: Make release 2.1.17` (CRLF line endings; ignore CR when diffing) |
| pandas in env | `2.3.1` |
| Line refs | `file.py:N` refers to the **installed** file unless prefixed `repo:` |
| "v2 disposition" column | **Port** = reproduce as-is; **Port-fix** = reproduce the signature, implement the corrected behaviour noted; **Stub** = keep the name/signature, raise `NotImplementedError` or no-op; **Out** = do not implement (depends on GS services) |

Every dataclass below is decorated `@dataclass_json @dataclass`. v2 does not need `dataclass_json`, but it **must keep the field order and defaults** so that positional construction works. The user's own notebook calls `MeanReversionTriggerRequirements(data_source, z_score_bound, rolling_mean_window, rolling_std_window)` and `Strategy(None, trigger)` positionally.

Field helpers used everywhere (`gs_quant/base.py`):

| Helper | Definition | Meaning for v2 |
|---|---|---|
| `field_metadata` | `config(exclude=exclude_none)` (base.py:123) | JSON serialisation only. Ignore it. |
| `static_field(val)` | `field(init=False, default=val)` (base.py:119) | This is a real dataclass field with `init=False`. It is **not** a constructor argument. v2 can keep `class_type: str = field(init=False, default='...')`. |
| Attributes with no annotation (e.g. `trigger_dates = []`, `current_position = 0`, `risks = None`) | plain class attributes, **not** dataclass fields | They are not constructor arguments. The instance shadows them on first assignment. |

---

## 1. `__init__.py` (17 lines)

```python
from .core import *
from .strategy_systematic import StrategySystematic
from gs_quant.target.backtests import DeltaHedgeParameters
```

`core.py` has no `__all__`, so `import *` re-exports every public name in it (`Backtest`, `BacktestResult`, `MarketModel`, `TimeWindow`, `TradeInMethod`, `ValuationFixingType`, `ValuationMethod`, and also `Enum`, `NamedTuple`, `Optional`, `Tuple`, `Union`, `dt`, `EnumBase`). Submodules are **not** imported eagerly. Users import from them directly, e.g. `from gs_quant.backtests.triggers import MeanReversionTrigger`.

| Name | v2 disposition |
|---|---|
| `from .core import *` names `TimeWindow`, `ValuationFixingType`, `ValuationMethod`, `MarketModel`, `TradeInMethod` | Port (plain enums/NamedTuples) |
| `Backtest`, `BacktestResult` (server-side Marquee objects) | Out |
| `StrategySystematic` (server-side) | Out |
| `DeltaHedgeParameters` (target model) | Out |

**v2 requirement:** keep the **submodule layout** (`pricebt.backtests.triggers`, `.strategy`, `.data_sources`, `.actions`, `.generic_engine`, `.backtest_utils`, `.order`, `.event`, `.data_handler`, `.core`). Then a gs_quant notebook ports by changing only the top-level package name.

---

## 2. `core.py` (55 lines)

| Symbol | Line | Exact definition | v2 |
|---|---|---|---|
| `TradeInMethod(EnumBase, Enum)` | core.py:28 | `FixedRoll = 'fixedRoll'` | Port (plain `Enum`) |
| `Backtest(__Backtest)` | core.py:32 | subclass of `gs_quant.target.backtests.Backtest` + `get_results(self) -> Tuple[BacktestResult, ...]` which calls `GsBacktestApi.get_results(backtest_id=self.id)` | Out |
| `MarketModel(EnumBase, Enum)` | core.py:39 | `STICKY_FIXED_STRIKE = "SFK"`, `STICKY_DELTA = "SD"` | Port (enum only; unused by the generic engine) |
| `TimeWindow(NamedTuple)` | core.py:44 | `start: Union[dt.time, dt.datetime] = None`, `end: Union[dt.time, dt.datetime] = None` | Port |
| `ValuationFixingType(EnumBase, Enum)` | core.py:49 | `PRICE = 'price'` | Port |
| `ValuationMethod(NamedTuple)` | core.py:53 | `data_tag: ValuationFixingType = ValuationFixingType.PRICE`, `window: Optional[TimeWindow] = None` | Port |

`EnumBase` (gs_quant/base.py:159) adds four things: case-insensitive `_missing_`, which matches on `value.lower()` so that `ValuationFixingType('PRICE')` works; `__lt__` comparing by value; `__str__`, which returns `self.value`; and `__repr__`, which returns `str(self)`. v2 should add a small `EnumBase` mixin with exactly these four methods so that the enums print and compare the same way.

---

## 3. `event.py` (45 lines) — PredefinedAssetEngine only

| Class | Line | `__init__` | Attributes set |
|---|---|---|---|
| `Event(object)` | event.py:20 | none | none (marker base) |
| `MarketEvent(Event)` | event.py:24 | `()` | `type = 'Market'` |
| `ValuationEvent(Event)` | event.py:29 | `()` | `type = 'Valuation'` |
| `OrderEvent(Event)` | event.py:34 | `(order: OrderBase)` | `type = 'Order'`, `order` |
| `FillEvent(Event)` | event.py:40 | `(order: OrderBase, filled_units: float, filled_price: float)` | `type = 'Fill'`, `order`, `filled_units`, `filled_price` |

v2: **Port** only if a PredefinedAssetEngine equivalent is in scope (see open questions). The GenericEngine does not use these.

---

## 4. `strategy.py` (66 lines)

```python
def _backtest_engines():                                  # strategy.py:29
    from gs_quant.backtests.equity_vol_engine import EquityVolEngine
    from gs_quant.backtests.generic_engine import GenericEngine
    from gs_quant.backtests.predefined_asset_engine import PredefinedAssetEngine
    return [GenericEngine(), PredefinedAssetEngine(), EquityVolEngine()]

@dataclass_json
@dataclass
class Strategy:                                           # strategy.py:39
    initial_portfolio: Optional[Union[Tuple[Priceable, ...], dict]] = field(default=None, ...)   # :44
    triggers: Union[Trigger, Iterable[Trigger]] = field(default=None, ...)                        # :47
    cash_accrual: CashAccrualModel = None                                                          # :50
    risks = None            # :51  class attribute, NOT a field / NOT a ctor arg

    def __post_init__(self):                               # :53
        if not isinstance(self.initial_portfolio, dict):
            self.initial_portfolio = make_list(self.initial_portfolio)
        self.triggers = make_list(self.triggers)
        self.risks = self.get_risks()

    def get_risks(self):                                   # :59
        risk_list = []
        for t in self.triggers:
            risk_list += t.risks if t.risks is not None else []
        return risk_list

    def get_available_engines(self):                       # :65
        return [engine for engine in _backtest_engines() if engine.supports_strategy(self)]
```

Signature (introspected): `Strategy(initial_portfolio: Union[Tuple[Priceable, ...], dict, NoneType] = None, triggers: Union[Trigger, Iterable[Trigger]] = None, cash_accrual: CashAccrualModel = None)`

| Behaviour | Exact semantics | v2 |
|---|---|---|
| `initial_portfolio=None` | becomes `[]` | Port |
| `initial_portfolio` is a single trade | becomes `[trade]` (`make_list`) | Port |
| `initial_portfolio` is a tuple/list/`Portfolio` | becomes `list(x)`. **A tuple becomes a list**; the type hint says Tuple but the value is a list after `__post_init__`. | Port |
| `initial_portfolio` is a `dict` | kept unchanged. Its shape is `{date: trade or [trades]}`. The GenericEngine (`generic_engine.py:875-899`) sorts the keys and holds the trades for each key from that date until the next key; the last key runs to the final pricing date. | Port |
| `triggers` | `make_list`: `None`→`[]`, single `Trigger`→`[t]`, iterable→`list` | Port |
| `risks` | set in `__post_init__` to the concatenation of `t.risks` over triggers, in trigger order. Duplicates are **not** removed here; the engine does `list(set(...))` later. | Port |
| `Trigger.risks` | `[a.risk for a in actions if a.risk is not None]`; `StrategyRiskTrigger` also appends `trigger_requirements.risk` (triggers.py:489, :522) | Port |
| `cash_accrual` | `CashAccrualModel` (backtest_objects): `ConstantCashAccrualModel(rate: float = 0, annual: bool = True)`, `DataCashAccrualModel(data_source: DataSource = None, annual: bool = True)` | Port (the engine doc covers the accrual maths) |
| `get_available_engines` | instantiates all 3 engines and filters them with `supports_strategy`. `GenericEngine.supports_strategy` (generic_engine.py:643) does `reduce(lambda x, y: x + y, map(lambda x: x.actions, strategy.triggers))` **with no initial value**, so a strategy with **zero triggers raises `TypeError`**. After that it tries `get_action_handler` on every action and returns False on `RuntimeError`. | Port-fix: return `[GenericEngine()]` when every action has a handler; handle zero triggers by returning the engine |

Field order is load-bearing: the notebook's `Strategy(None, trigger)` passes `initial_portfolio=None, triggers=trigger`.

---

## 5. `backtest_utils.py` (127 installed / 206 repo lines)

### 5.1 `CalcType(Enum)` — backtest_utils.py:32

| Member | Value | Engine meaning (generic_engine.py:909-1000) |
|---|---|---|
| `simple` | `'simple'` | Evaluated in the pre-pass over all pricing dates (`_build_simple_and_semi_triggers_and_actions`). Each action handler receives the list of triggered dates in one call. |
| `semi_path_dependent` | `'semi_path_dependent'` | Treated **exactly like `simple`** by the trigger code (the engine only tests `!= path_dependent`). Semi-path-dependence only matters for `HedgeAction`'s later scaling pass. |
| `path_dependent` | `'path_dependent'` | Evaluated **day by day** in `_process_triggers_and_actions_for_date(d, ...)`, after that day's risk results exist (`__ensure_risk_results`). |

Engine rule for mixed trigger/action types (generic_engine.py:985-1000):
- trigger `path_dependent` → on each date `d`: `if trigger.has_triggered(d, backtest): for action: ensure results; apply_action(d, backtest)` (**no `trigger_info` is passed**).
- trigger not path dependent **and** action `path_dependent` → the same daily loop, but only for those actions. `has_triggered` is called **again for each such action** (so stateful triggers get called more than once per date).
- trigger not path dependent **and** action not path dependent → pre-pass (see §6.9).

### 5.2 `CustomDuration` — backtest_utils.py:40

```python
@dataclass_json
@dataclass
class CustomDuration:
    durations: Tuple[Union[str, dt.date, dt.timedelta], ...]
    function: Callable[[Tuple[Union[str, dt.date, dt.timedelta], ...]], Union[str, dt.date, dt.timedelta]]
    def __hash__(self): return hash((self.durations, self.function))
```
Both fields are required in 1.5.4. In repo 2.1.17 `function` has `default=None` plus a callable encoder/decoder. `get_final_date` resolves each element of `durations` recursively and calls `function(*resolved_dates)`, e.g. `CustomDuration(('3m', 'next schedule'), min)`. v2: **Port**.

### 5.3 `make_list(thing)` — backtest_utils.py:48

| Input | Output |
|---|---|
| `None` | `[]` |
| `str` | `[thing]` (a string is never iterated) |
| non-iterable (int, dataclass, Trigger, Action, ...) | `[thing]` |
| iterable (list, tuple, set, generator, `Portfolio`, `dict`) | `list(thing)`; **a dict becomes a list of its keys** (verified: `make_list({'a':1}) == ['a']`) |

v2: **Port verbatim.**

### 5.4 `get_final_date(inst, create_date, duration, holiday_calendar=None, trigger_info=None)` — backtest_utils.py:65

Resolves an action's `trade_duration` into the date on which the trade is exited. Rules are checked in this order:

| # | Condition | Returns | Cached? |
|---|---|---|---|
| 0 | `(inst, create_date, duration, holiday_calendar)` is in the module-global `final_date_cache` | cached value | — |
| 1 | `duration is None` | `dt.date.max` (9999-12-31, i.e. held forever) | yes |
| 2 | `isinstance(duration, (dt.datetime, dt.date))` | `duration` itself | yes |
| 3 | `hasattr(inst, str(duration))` | `getattr(inst, str(duration))`, e.g. `'termination_date'`, `'expiration_date'` read from the **resolved** instrument | yes |
| 4 | `str(duration).lower() == 'next schedule'` | `trigger_info.next_schedule or dt.date.max` if `trigger_info` has `next_schedule`; otherwise `RuntimeError('Next schedule not supported by action')` | **no** |
| 5 | `isinstance(duration, CustomDuration)` | `duration.function(*(get_final_date(inst, create_date, d, holiday_calendar, trigger_info) for d in duration.durations))` | **no** |
| 6 | otherwise (a tenor string) | `RelativeDate(duration, create_date).apply_rule(holiday_calendar=holiday_calendar)`; repo uses `duration.lower()` | yes |

Verified results: `'3m'` from 2024-01-31 → 2024-04-30; `'1y'` → 2025-01-31; `'5b'` from Fri 2024-01-05 → 2024-01-12; `None` → 9999-12-31.

Gotchas:
- **`dt.timedelta` fails in both versions** even though `Duration = Union[str, dt.date, dt.timedelta, CustomDuration]` (actions.py:47) and the action docstrings say it is supported. Installed: `TypeError: object of type 'datetime.timedelta' has no len()`. Repo: `AttributeError` from `.lower()`. **v2 Port-fix:** `timedelta` → `create_date + duration`.
- The cache key needs hashable arguments (so `holiday_calendar` must be a tuple, not a list) and is never cleared, which leaks across runs. **v2:** cache per engine run, or skip caching.
- `holiday_calendar=None` makes business-day rules fetch `GsCalendar([])`. With an empty list this returns `()` (no network call), so the result is weekends-only (`week_mask='1111100'`). **v2:** `holiday_calendar=None` ⇒ weekends only, with no data access.

### 5.5 Tenor rules used by `RelativeDate` / `RelativeDateSchedule` (gs_quant/datetime/relative_date.py, rules.py)

pricebt v2 must implement a small **self-contained** equivalent (no gs_quant import). Only these rules matter to backtests:

| Rule letter | Meaning (`number = n`) | Roll when the result is a non-business day | rules.py |
|---|---|---|---|
| `d` | `+n` calendar days | none | :133 |
| `b` | `np.busday_offset(base, n, roll)`, where roll = `'forward'` if `n<=0` else `'preceding'` | built into busday_offset | :126 |
| `w` | `+n` weeks | `'forward'` if `n>=0` else `'backward'` | :252 |
| `m` | `+n` months (`relativedelta`, end-of-month clipping: Jan31+1m = Feb29) | **`'forward'`** (plain following, *not* modified-following: Mar 31 2024 (Sun) → Apr 1) | :185 |
| `y` | `+n` years, then step to the next weekday per week_mask | `'backward'` | :273 |

Compound rules (e.g. `'1m-1b'`) are split by `_get_rules` (relative_date.py:111) into successive rules. A leading `+` is stripped. Unknown letter → `NotImplementedError(f'Rule {rule} not implemented')`. **Case matters in 1.5.4:** `'1M'` means "Nth Monday of the month" (`MRule`), which gives nonsense schedules (verified). 2.1.17 lower-cases. **v2: lower-case the unit.**

`RelativeDateSchedule(rule, base_date, end_date).apply_rule(holiday_calendar=...)` (relative_date.py:234):
```
schedule = [base_date]                      # base date included UNADJUSTED (even if a weekend)
i = 1
loop: result = RelativeDate(f'{int(rule[:-1])*i}{rule[-1]}', base_date).apply_rule(...)
      if end_date is None or result > end_date: break
      schedule.append(result); i += 1
```
- Each point is `base + n*i` units counted **from the base date**; it is not stepped from the previous point.
- `end_date=None` ⇒ schedule is just `[base_date]`.
- `rule` must be `<int><single letter>`; `frequency=None` ⇒ `TypeError`.
- Verified: `'1w'` from Sat 2024-01-06 to 2024-02-10 → `[01-06, 01-15, 01-22, 01-29, 02-05]` (01-13 is a Saturday, rolled to 01-15). `'1m'` from 2024-01-31 to 2024-06-30 → `[01-31, 02-29, 04-01, 04-30, 05-31]`. `'1b'` from Fri 01-05 to 01-12 → `[01-05, 01-08, 01-09, 01-10, 01-11, 01-12]`.

### 5.6 Other helpers

| Function | Line | Semantics | v2 |
|---|---|---|---|
| `scale_trade(inst, ratio)` | :92 | `return inst.scale(ratio)`. `Instrument.scale(None)` returns `self` (identity); `in_place=True` by default. `IRSwap.scale_in_place(s)`: `None` or `1` → no-op; `notional_amount *= abs(s)`; if `s<0` flip `pay_or_receive` (target/instrument.py IRSwap). | Port-fix: generic `trade.scale(ratio)` that multiplies a signed quantity. The asset config defines what "quantity" means. |
| `map_ccy_name_to_ccy(currency_name)` | :97 | dict lookup from long name to ISO code (e.g. `'United States Dollar'→'USD'`, `'Euro'→'EUR'`, `'Pound Sterling'→'GBP'`, `'Yuan Renminbi (Onshore)'→'CHY'` [sic]). Returns `None` if unknown. Used when parsing gs risk units. | Out (pricebt configs carry ISO codes directly) |
| `interpolate_signal(signal: dict[date,float], method=Interpolate.STEP) -> pd.Series` | :122 | builds every calendar day from `min(keys)` to `max(keys)` and step-interpolates (value carried forward). Verified: `{1st:1, 2nd:2, 4th:4}` → `{1st:1, 2nd:2, 3rd:2, 4th:4}`. Used for dict-valued `AddScaledTradeAction.scaling_level`. | Port (a pandas `reindex(...).ffill()` gives the same result) |
| `parse_timedelta(value)` | repo only (repo:backtest_utils.py:168) | `None`→`None`; timedelta unchanged; int/float/numeric string → seconds; else regex `d/h/m/s` (e.g. `'1h30m'`); otherwise `ValueError`/`TypeError` | Optional port (intraday) |
| `encode_duration` / `decode_duration` | repo only | JSON helpers | Out |

---

## 6. `triggers.py` (620 installed / 641 repo lines)

### 6.1 Enums and `TriggerInfo`

| Symbol | Line | Exact |
|---|---|---|
| `TriggerDirection(Enum)` | :45 | `ABOVE = 1`, `BELOW = 2`, `EQUAL = 3` |
| `AggType(Enum)` | :51 | `ALL_OF = 1`, `ANY_OF = 2` |
| `TriggerInfo` (dataclass) | :79 | `triggered: bool` (required), `info_dict: Optional[dict] = None`. `__bool__` returns `self.triggered`. `__eq__(other)` returns `self.triggered is other`, so `TriggerInfo(True) == True` is True but **`TriggerInfo(True) == TriggerInfo(True)` is False** (verified). The engine only ever uses truthiness (`if t_info:`). |
| `check_barrier(direction, test_value, trigger_level) -> TriggerInfo` | :90 | ABOVE: `test_value > trigger_level`; BELOW: `test_value < trigger_level`; any other direction (incl. `EQUAL`, `None`): `test_value == trigger_level`. **Strict** inequalities. NaN compares False, so the trigger does not fire. Returns `TriggerInfo(True/False)` with `info_dict=None`. |

**`info_dict` contract:** `{ActionClass: ActionInfo}`. The key is an action **class**; the value is that action's info namedtuple (actions.py:174-178):

| Info type | Definition |
|---|---|
| `AddTradeActionInfo` | `namedtuple('AddTradeActionInfo', ['scaling', 'next_schedule'])`; **both fields are required** (this is what breaks MR in 1.5.4) |
| `AddScaledTradeActionInfo` | `namedtuple('AddScaledActionInfo', 'next_schedule')` |
| `HedgeActionInfo` | `namedtuple('HedgeActionInfo', 'next_schedule')` |

How the engine uses it (generic_engine.py:911-940, §6.9): `scaling` multiplies the added trade (`Portfolio.scale(ti.scaling, in_place=False)`, generic_engine.py:121). `next_schedule` feeds the `trade_duration='next schedule'` rule in `get_final_date`.

### 6.2 `TriggerRequirements` base — triggers.py:58

- No fields. Keeps a subclass registry (`__init_subclass__` → `sub_classes()`; for JSON only).
- `get_trigger_times(self) -> []`
- `calc_type` property → `CalcType.simple`
- `has_triggered` is **not** defined on the base; every subclass defines it.

### 6.3 `Trigger` base — triggers.py:452

```python
@dataclass_json
@dataclass
class Trigger:
    trigger_requirements: Optional[TriggerRequirements] = None
    actions: Union[Action, Iterable[Action]] = None
    def __post_init__(self): self.actions = make_list(self.actions)
    def has_triggered(self, state, backtest=None) -> TriggerInfo: return self.trigger_requirements.has_triggered(state, backtest)
    def get_trigger_times(self): return self.trigger_requirements.get_trigger_times()
    @property
    def calc_type(self): return self.trigger_requirements.calc_type
    @property
    def risks(self): return [x.risk for x in make_list(self.actions) if x.risk is not None]
```
Every concrete subclass only **re-annotates `trigger_requirements`** with its requirements type and adds `class_type: str = static_field('<name>')`. Resulting field order: `(trigger_requirements, actions, class_type[init=False])`, so the positional ctor is `XTrigger(requirements, actions)`. The requirements type is **not checked**; any requirements object works.

### 6.4 Trigger / requirements pairs (full table)

| Trigger class (line) | `class_type` | Requirements class (line) | Ctor signature (field order, defaults) | `calc_type` | `get_trigger_times()` | v2 |
|---|---|---|---|---|---|---|
| `PeriodicTrigger` (:495) | `'periodic_trigger'` (also has a stray class attr `_trigger_dates = None`) | `PeriodicTriggerRequirements` (:105) `'periodic_trigger_requirements'` | `(start_date: Optional[date]=None, end_date: Optional[date]=None, frequency: Optional[str]=None, calendar: Optional[Iterable[date]]=None)` | simple | `RelativeDateSchedule(frequency, start_date, end_date).apply_rule(holiday_calendar=calendar)`, memoised on `self.trigger_dates` | Port-fix (lower-case frequency) |
| `IntradayPeriodicTrigger` (:503) | `'intraday_periodic_trigger'` | `IntradayTriggerRequirements` (:142) `'intraday_trigger_requirements'` | `(start_time: Optional[time]=None, end_time: Optional[time]=None, frequency: Optional[float]=None)`; frequency in **minutes** | simple | `self._trigger_times` (list of `dt.time`) | Port-fix (guard midnight wrap) |
| `MktTrigger` (:510) | `'mkt_trigger'` | `MktTriggerRequirements` (:165) `'mkt_trigger_requirements'` | `(data_source: DataSource=None, trigger_level: float=None, direction: TriggerDirection=None)` | simple | `[]` | Port |
| `StrategyRiskTrigger` (:517) | `'strategy_risk_trigger'` | `RiskTriggerRequirements` (:184) `'risk_trigger_requirements'` | `(risk: RiskMeasure=None, trigger_level: float=None, direction: TriggerDirection=None, risk_transformation: Optional[Transformer]=None)` | **path_dependent** | `[]` | Port (risk = a named pricing function from the asset config) |
| `AggregateTrigger` (:528) | `'aggregate_trigger'` | `AggregateTriggerRequirements` (:211) `'aggregate_trigger_requirements'` | `(triggers: Iterable[TriggerRequirements]=None, aggregate_type: AggType=AggType.ALL_OF)` | worst of the children (see §6.5.5) | `[]` (**children's times are not merged in**) | Port-fix (see below) |
| `NotTrigger` (:535) | `'not_trigger'` | `NotTriggerRequirements` (:261) `'not_trigger_requirements'` | `(trigger: TriggerRequirements=None)` | simple (**no override**) | `[]` | Port-fix |
| `DateTrigger` (:542) | `'date_trigger'` | `DateTriggerRequirements` (:281) `'date_trigger_requirements'` | `(dates: Iterable[Union[datetime, date]]=None, entire_day: bool=False)` | simple | `self.dates_from_datetimes or self.dates` | Port |
| `PortfolioTrigger` (:549) | `'portfolio_trigger'` | `PortfolioTriggerRequirements` (:321) `'portfolio_trigger_requirements'` | `(data_source: str=None, trigger_level: float=None, direction: TriggerDirection=None)` | simple | `[]` | Port (keep the quirk, document it) |
| `MeanReversionTrigger` (:556) | `'mean_reversion_trigger'` | `MeanReversionTriggerRequirements` (:344) `'mean_reversion_trigger_requirements'` | `(data_source: DataSource=None, z_score_bound: float=None, rolling_mean_window: int=None, rolling_std_window: int=None)` | simple | `[]` | **Port-fix (2.1.17 behaviour)** |
| `TradeCountTrigger` (:563) | `'trade_count_trigger'` | `TradeCountTriggerRequirements` (:381) `'trade_count_requirements'` | `(trade_count: float=None, direction: TriggerDirection=None)` | **path_dependent** | `[]` | Port |
| `EventTrigger` (:570) | `'event_trigger'` | `EventTriggerRequirements` (:406) `'event_requirements'` | 1.5.4: `(event_name: str=None, offset_days: int=0, data_source: DataSource=None)`; repo adds `country, currency, source, start, end` (all `Optional=None`) **after** `data_source` | simple | dates from the data source + `offset_days` | Port with a **required user-supplied** `data_source` (default GS calendar is Out) |
| `OrdersGeneratorTrigger` (:577) | none (no `class_type`) | none (uses base `trigger_requirements: Optional = None`) | `(trigger_requirements=None, actions=None)` | simple (via requirements; with `None` requirements, `.calc_type` raises `AttributeError`) | abstract; raises `RuntimeError` | Port only with PredefinedAssetEngine |

`Trigger.sub_classes()` order (introspected): `PeriodicTrigger, IntradayPeriodicTrigger, MktTrigger, StrategyRiskTrigger, AggregateTrigger, NotTrigger, DateTrigger, PortfolioTrigger, MeanReversionTrigger, TradeCountTrigger, EventTrigger, OrdersGeneratorTrigger`.

### 6.5 `has_triggered(state, backtest=None)` for each requirements class (exact)

The "standard schedule info" referred to below is:
```python
{AddTradeAction: AddTradeActionInfo(scaling=None, next_schedule=next_state),
 AddScaledTradeAction: AddScaledTradeActionInfo(next_schedule=next_state),
 HedgeAction: HedgeActionInfo(next_schedule=next_state)}
```
Here `next_state` is the next date in the sorted schedule, or `None` if `state` is the last one.

#### 6.5.1 Periodic (:115-137)
```
if not self.trigger_dates: self.get_trigger_times()
if state in self.trigger_dates: return TriggerInfo(True, standard_schedule_info(next_state))
return TriggerInfo(False)
```
- `trigger_dates` is a class attribute `[]`; the first call assigns it on the instance (so it is not shared between instances; verified).
- Membership test is exact `date in list`. A `dt.datetime` state never matches.
- `start_date=None` ⇒ schedule base = today (or the ambient `PricingContext` date). **v2: `start_date=None` ⇒ the backtest start date.** This is the natural meaning, and pricebt has no ambient context. Mark it as a deliberate deviation.
- `calendar` becomes `holiday_calendar` in the business-day roll.

#### 6.5.2 Intraday (:148-160)
```
__post_init__: t = start_time; while t <= end_time: append(t); t = (combine(today, t) + timedelta(minutes=frequency)).time()
has_triggered: return TriggerInfo(state.time() in self._trigger_times)      # no info_dict
```
- A `dt.date` state raises `AttributeError`.
- **Infinite loop** if adding `frequency` wraps past midnight to a time `<= end_time` (e.g. start 23:00, end 23:59, freq 30 gives 23:00, 23:30, 00:00, ... forever). **v2:** stop the loop when the time wraps.
- Verified: 09:00–10:00 every 30 → `[09:00, 09:30, 10:00]`.

#### 6.5.3 Mkt (:173-179)
```
data_value = self.data_source.get_data(state)
try: return check_barrier(self.direction, data_value, self.trigger_level)
except TypeError: raise RuntimeError(f'unable to determine trigger state on {str(state)}, data value was {data_value}')
```
No `info_dict`, so an `AddTradeAction` gets `trigger_info=None` and the trade is not rescaled.

#### 6.5.4 Risk (:191-206)
```
if state not in backtest.results: return TriggerInfo(False)
risk_value = backtest.results[state][self.risk].aggregate()                        # no transformation
           | backtest.results[state][self.risk].transform(risk_transformation=...).aggregate(allow_mismatch_risk_keys=True)
return check_barrier(self.direction, risk_value, self.trigger_level)
```
- `aggregate()` is the sum over all positions held on `state` of the risk measure (in the result currency).
- path_dependent, so the value includes trades added earlier on the same path.
- **v2:** `risk` is the name of a portfolio-level pricing function (e.g. `'dv01'`, in reporting ccy per 1bp). `risk_transformation` is **Stub** (accept `None` only).

#### 6.5.5 Aggregate (:216-256)
- `__setattr__('triggers', value)`: if **all** elements are `Trigger` instances, replace them with `tuple(v.trigger_requirements for v in value)`; otherwise store unchanged (a mix of `Trigger` and requirements is **not** converted and fails later). The default `triggers=None` makes `all([... for v in None])` raise **`TypeError` at construction**, so `triggers` is effectively required.
- `ALL_OF`: iterate children in order; **return `TriggerInfo(False)` on the first falsy child (short-circuit)**; otherwise merge each child's `info_dict` with `dict.update` (later children overwrite earlier keys) and return `TriggerInfo(True, info_dict)`. Note that `info_dict` is `{}` (not `None`) when no child supplies one.
- `ANY_OF`: evaluate **every** child (no short-circuit), merge the `info_dict`s of the children that fired, return `TriggerInfo(True, merged)` if any fired, else `TriggerInfo(False)`.
- Any other `aggregate_type` → `RuntimeError(f'Unrecognised aggregation type: ...')`.
- `calc_type`: `path_dependent` if any child is; else `semi_path_dependent` if any child is; else `simple`.
- Short-circuiting matters for **stateful** children (MeanReversion): in `ALL_OF`, a child after the first False is not called, so its state does not advance.
- `get_trigger_times()` is the base `[]`. A `DateTrigger`/`PeriodicTrigger` inside an Aggregate therefore **does not add its dates to the pricing dates**. An `EventTriggerRequirements` inside an Aggregate never gets `get_trigger_times()` called, so it never fires. **v2 Port-fix:** Aggregate/Not `get_trigger_times()` = union of the children's times (for ALL_OF, adding extra evaluation dates is harmless). Mark it as a deliberate fix.

#### 6.5.6 Not (:265-276)
- `__setattr__`: when `key == 'trigger'`, unwraps a `Trigger` into its `trigger_requirements` and sets it. **The `super().__setattr__` call is indented inside the `if`, so assignments to every other attribute are silently dropped**; `class_type` survives only through the class default. **Do not port this bug.**
- `has_triggered`: `TriggerInfo(False)` if the child fires, else `TriggerInfo(True)` with **no `info_dict`**.
- `calc_type` is **not overridden**, so it is `simple` even when the child is path dependent. Consequence: `NotTrigger(RiskTriggerRequirements)` runs in the pre-pass when `backtest.results` is empty. The child returns False on every date, so the Not fires on **every** date. **v2 Port-fix:** `calc_type = self.trigger.calc_type`. Mark it as a deliberate fix.

#### 6.5.7 Date (:289-316)
```
__post_init__: dates_from_datetimes = [d.date() if datetime else d for d in dates] if entire_day else None
has_triggered:
  if entire_day: dates = sorted(dates_from_datetimes); state = state.date() if datetime
  else: dates = sorted(self.dates)
  if state in dates: return TriggerInfo(True, standard_schedule_info(next_state))
  return TriggerInfo(False)
get_trigger_times: return self.dates_from_datetimes or self.dates
```
- With `entire_day=False` and datetimes in `dates`, a daily engine (states are `dt.date`) never matches (`date != datetime`). The engine's filter `start <= t <= end` also raises `TypeError` when comparing a date with a datetime.
- `dates=None` ⇒ `sorted(None)` raises `TypeError` on the first call.
- `sorted()` and `.index()` run on every call (O(n) each); v2 may precompute.
- Verified: `dates=[datetime(2024,1,2,10), date(2024,1,5)], entire_day=True` fires on `date(2024,1,2)`; `get_trigger_times() == [date(2024,1,2), date(2024,1,5)]`.

#### 6.5.8 Portfolio (:327-339)
- Only `data_source == 'len'` is supported; any other value → always `TriggerInfo(False)`.
- `value = len(backtest.portfolio_dict)`, which is the **number of dates** that have an entry in `portfolio_dict` (a `defaultdict(Portfolio)`, backtest_objects.py:98, that **gains keys whenever it is read**). It is **not** the number of trades.
- Comparison as in `check_barrier` (strict for ABOVE/BELOW, `==` otherwise); no `info_dict`.
- `calc_type` is `simple` although it reads backtest state, so in the pre-pass it sees only the initial portfolio and whatever earlier triggers' pre-pass actions have added.
- **v2:** Port as specified (for parity) and document it; per-date counts belong to `TradeCountTrigger`.

#### 6.5.9 MeanReversion (:354-376), the user's example. **Implement 2.1.17 semantics.**

State: a class attribute `current_position = 0`, shadowed per instance on the first assignment. It takes values in `{-1, 0, +1}`.

```python
def has_triggered(self, state, backtest=None):
    rolling_mean = self.data_source.get_data_range(state, self.rolling_mean_window).mean()
    rolling_std  = self.data_source.get_data_range(state, self.rolling_std_window).std()    # pandas: ddof=1 (sample std)
    current_price = self.data_source.get_data(state)
    if self.current_position == 0:
        if abs((current_price - rolling_mean) / rolling_std) > self.z_score_bound:           # strict >
            if current_price > rolling_mean:
                self.current_position = -1
                return TriggerInfo(True, {AddTradeAction: AddTradeActionInfo(scaling=-1, next_schedule=None)})
            else:
                self.current_position = 1
                return TriggerInfo(True, {AddTradeAction: AddTradeActionInfo(scaling=1, next_schedule=None)})
    elif self.current_position == 1:
        if current_price > rolling_mean:              # long closes when price crosses ABOVE the mean
            self.current_position = 0                 # 1.5.4 typo: self._current_position = 0 (never resets)
            return TriggerInfo(True, {AddTradeAction: AddTradeActionInfo(scaling=-1, next_schedule=None)})
    elif self.current_position == -1:
        if current_price < rolling_mean:              # 1.5.4 bug: '>' here
            self.current_position = 0
            return TriggerInfo(True, {AddTradeAction: AddTradeActionInfo(scaling=1, next_schedule=None)})
    else:
        raise RuntimeWarning(f'unexpected current position: {self.current_position}')
    return TriggerInfo(False)
```

Exact semantics:

| Aspect | Rule |
|---|---|
| Window | `get_data_range(state, N)` returns the last **N points strictly before `state`** (`index < state`, then `.tail(N)`). **Today is excluded** from the mean and std; `current_price` is today's value. |
| Short window | If fewer than N earlier points exist, it uses what is there. 0 points → mean NaN; 1 point → std NaN. NaN z-score → `abs(nan) > b` is False → no trigger (verified on day 1 and day 2). |
| Zero std | numpy float division gives `±inf`, so the trigger **fires** (verified: `[1,1,1]` then `2` → fires, position -1). v2 should reproduce this (use numpy floats, not Python `ZeroDivisionError`). |
| Open | Only from flat (`0`). `|z| > z_score_bound` (strict). Price above mean → **sell** (`scaling=-1`, position -1). Price at or below mean → **buy** (`scaling=+1`, position +1). |
| Close | Long closes when `price > mean`; short closes when `price < mean`. **No z-score test on close**: crossing the rolling mean is enough. The close is an opposite `AddTradeAction` (`scaling` = −position), which nets the open trade because `AddTradeAction(trade_duration=None)` holds forever. |
| One event per call | Opening and closing never happen on the same date, and a close cannot reopen on the same date. |
| Which actions get info | Only the key `AddTradeAction`. For other action types, `trigger_info` is `None` (the engine does an exact-type lookup, then an `isinstance` fallback; §6.9). |
| Direction via scaling | The action's trade is cloned per date and scaled with `Portfolio.scale(scaling)`; `-1` flips a swap payer↔receiver. **v2:** `scaling` multiplies the signed quantity of the trade built from the asset config (e.g. notional `+1e4` payer becomes `-1e4`). |
| State persistence | `current_position` lives on the requirements **instance** and is **never reset**. Running the same `Strategy` twice starts the second run with the previous final position. **v2 Port-fix:** the engine resets trigger state at run start (e.g. a `reset()` hook on requirements; MR sets `current_position = 0`). |
| Evaluation order | `simple`, so called once per pricing date in ascending date order in the pre-pass. The pricing dates are the `frequency` schedule plus the trigger times (MR has none). |
| Data key type | The index must be comparable with `state`. In pandas 2.3.1 a `DatetimeIndex` plus a `dt.date` state raises `TypeError` in `get_data_range` (verified). |

**Worked example**: 2.1.17 code actually executed; `z_score_bound=1.5`, both windows 5, `GenericDataSource(date-indexed series, fill_forward)`. Use it as a golden test in v2:

| date | price | n | rolling_mean | rolling_std | z | triggered | scaling | current_position after |
|---|---|---|---|---|---|---|---|---|
| 2024-01-01 | 0.0 | 0 | nan | nan | nan | False | None | 0 |
| 2024-01-02 | 1.0 | 1 | 0.0000 | nan | nan | False | None | 0 |
| 2024-01-03 | 0.0 | 2 | 0.5000 | 0.7071 | -0.7071 | False | None | 0 |
| 2024-01-04 | 1.0 | 3 | 0.3333 | 0.5774 | 1.1547 | False | None | 0 |
| 2024-01-05 | 0.0 | 4 | 0.5000 | 0.5774 | -0.8660 | False | None | 0 |
| 2024-01-06 | 1.0 | 5 | 0.4000 | 0.5477 | 1.0954 | False | None | 0 |
| 2024-01-07 | 5.0 | 5 | 0.6000 | 0.5477 | 8.0333 | True | -1 | -1 |
| 2024-01-08 | 4.0 | 5 | 1.4000 | 2.0736 | 1.2538 | False | None | -1 |
| 2024-01-09 | 0.4 | 5 | 2.2000 | 2.1679 | -0.8303 | True | 1 | 0 |
| 2024-01-10 | 0.2 | 5 | 2.0800 | 2.2654 | -0.8299 | False | None | 0 |
| 2024-01-11 | -5.0 | 5 | 2.1200 | 2.2208 | -3.2060 | True | 1 | 1 |
| 2024-01-12 | -4.0 | 5 | 0.9200 | 3.9360 | -1.2500 | False | None | 1 |
| 2024-01-13 | 0.0 | 5 | -0.8800 | 3.6513 | 0.2410 | True | -1 | 0 |
| 2024-01-14 | 1.0 | 5 | -1.6800 | 2.6023 | 1.0299 | False | None | 0 |
| 2024-01-15 | 2.0 | 5 | -1.5600 | 2.7328 | 1.3027 | False | None | 0 |
| 2024-01-16 | -6.0 | 5 | -1.2000 | 3.1145 | -1.5412 | True | 1 | 1 |
| 2024-01-17 | -1.0 | 5 | -1.4000 | 3.4351 | 0.1164 | True | -1 | 0 |
| 2024-01-18 | 3.0 | 5 | -0.8000 | 3.1145 | 1.2201 | False | None | 0 |
| 2024-01-19 | 3.0 | 5 | -0.2000 | 3.5637 | 0.8979 | False | None | 0 |

(series: `[0,1,0,1,0,1,5,4,0.4,0.2,-5,-4,0,1,2,-6,-1,3,3]` on consecutive calendar days from 2024-01-01. To reproduce, run the 2.1.17 code on this series with the parameters given above; no other input is needed.)

Under **1.5.4** the same input raises `TypeError` on 01-07. The `current_position` assignment has already happened when the error is raised, so state is mutated even though the call failed.

#### 6.5.10 TradeCount (:386-401)
- `value = len(backtest.portfolio_dict.get(state, []))`, the number of instruments held on `state` (`.get`, so no key is created).
- Compared with `trade_count` like `check_barrier`; no `info_dict`; path_dependent.

#### 6.5.11 Event (:415-447)
- `__post_init__`: if `data_source is None`, set `GsDataSource(data_set='MACRO_EVENTS_CALENDAR', asset_id=None, value_header='eventName')`. **Out** for v2: `data_source` is required (raise `ValueError` if missing).
- `get_trigger_times()`: memoised; `[d.date() + timedelta(days=offset_days) for d in data_source.get_data(None, eventName=event_name).index]`. Repo 2.1.17 adds the filter kwargs `country`, `currency`, `source` (a string is wrapped as `[source]`), plus `start`/`end`, which are both passed if either is set.
- `has_triggered`: uses `self.trigger_dates` **without calling `get_trigger_times()`**; it relies on the engine calling `get_trigger_times()` first (generic_engine.py:784). Otherwise it is identical to Date (standard schedule info).
- `list_events(currency, start=..., end=..., **kwargs)` is a static method querying the GS dataset (note the bug: its defaults are the *types* `Optional[dt.datetime]`). **Out.**
- **v2:** `data_source` is any object with `get_data(None, **kwargs) -> Series indexed by datetime/date`, or more simply a `GenericDataSource` whose index holds the event dates. The v2 contract is: event dates = index of `data_source.get_data(None)`, shifted by `offset_days`. Filter kwargs are ignored unless the user source handles them.

#### 6.5.12 OrdersGeneratorTrigger (:577-612)
- `__post_init__`: if there are no actions, `actions = [Action()]`; then the base `make_list`.
- `get_trigger_times()` and `generate_orders(state, backtest)` both raise `RuntimeError` (abstract).
- `has_triggered(state: datetime, backtest)`: `False` if `state.time()` is not in `get_trigger_times()`; else `orders = generate_orders(...)`, returning `TriggerInfo(True, {type(a): orders for a in self.actions})` if `len(orders)` else `False`.
- PredefinedAssetEngine only.

### 6.6 JSON registry patch (triggers.py:615-620)
After all classes are defined, the decoder metadata of `AggregateTriggerRequirements.triggers` and `NotTriggerRequirements.trigger` is overwritten so that they decode polymorphically. This is JSON-only; **v2: Out** (unless v2 adds its own YAML serialisation of strategies).

### 6.7 Summary: which triggers put what in `info_dict`

| Requirements | `info_dict` when fired |
|---|---|
| Periodic, Date, Event | standard schedule info (`scaling=None`, `next_schedule=next`) for AddTrade / AddScaledTrade / Hedge |
| MeanReversion | `{AddTradeAction: AddTradeActionInfo(scaling=±1, next_schedule=None)}` |
| Aggregate | merge (`dict.update`, in child order) of the children that fired (ALL_OF: all of them; ANY_OF: only those that fired) |
| Mkt, Risk, Not, Portfolio, TradeCount, Intraday | `None` |
| OrdersGenerator | `{type(action): orders_list}` |

### 6.8 `trigger_info` routing inside the pre-pass (generic_engine.py:909-940), needed to understand triggers

```
for trigger in strategy.triggers:
  if trigger.calc_type != path_dependent:
    triggered_dates = []; trigger_infos = defaultdict(list)
    for d in strategy_pricing_dates:                 # ascending
        t_info = trigger.has_triggered(d, backtest)
        if t_info:
            triggered_dates.append(d)
            if t_info.info_dict:
                for k, v in t_info.info_dict.items(): trigger_infos[k].append(v)
    for action in trigger.actions:
        if action.calc_type != path_dependent:
            trigger_info = trigger_infos[type(action)] if type(action) in trigger_infos
                           else first v for (k, v) in trigger_infos.items() if isinstance(action, k)   # else None
            action_handler.apply_action(triggered_dates, backtest, trigger_info)
```
- `triggered_dates` and each `trigger_infos[k]` list are **zipped positionally** in the handler (`zip_longest`, generic_engine.py:114). If some fired dates carry no info (e.g. an ANY_OF of Periodic plus Mkt), the lists are misaligned and infos attach to the wrong dates. **v2 Port-fix:** store the infos keyed by date (`{date: info}`) instead of a parallel list. Mark it as a deliberate fix.
- `trigger_info=None` for a list of dates is broadcast to `[None]*len(dates)`, which means no scaling.
- Pricing dates = the `RelativeDateSchedule(frequency, start, end)` (or explicit `states`), **plus every trigger's `get_trigger_times()` within `[first, last]`**, deduplicated and sorted (generic_engine.py:771-790).

### 6.9 Trigger-level gotchas (checklist)

| # | Gotcha | v2 action |
|---|---|---|
| G1 | MR 1.5.4 raises TypeError; `_current_position` typo; wrong `>` | use 2.1.17 behaviour |
| G2 | MR state persists across runs | reset trigger state at run start |
| G3 | Not's `calc_type` is not inherited from its child | inherit it |
| G4 | Aggregate/Not do not propagate `get_trigger_times` | union the children's times |
| G5 | Aggregate ALL_OF short-circuits (stateful children skipped); ANY_OF does not | **keep**, for parity, and document it |
| G6 | `AggregateTriggerRequirements()` with no args → TypeError | raise a clear `ValueError('triggers required')` |
| G7 | Not `__setattr__` drops non-`trigger` attributes | do not port |
| G8 | Intraday midnight wrap → infinite loop | guard |
| G9 | Periodic `'1M'` means Nth Monday in 1.5.4 | lower-case |
| G10 | Periodic `end_date=None` ⇒ a single date | keep, or default to the backtest end; document the choice in the design doc |
| G11 | Portfolio `'len'` counts dates, and `portfolio_dict` grows on read | keep for parity; document |
| G12 | Positional info-list misalignment | key infos by date |
| G13 | `TriggerInfo.__eq__` identity comparison | keep (harmless) or implement proper equality; the engine uses only `bool()` |

---

## 7. `data_sources.py` (218 lines)

### 7.1 `MissingDataStrategy(Enum)` — data_sources.py:32
`fill_forward = 'fill_forward'`, `interpolate = 'interpolate'`, `fail = 'fail'`.

### 7.2 `DataSource` base — :40
No fields; subclass registry (`sub_classes()` → `(GsDataSource, GenericDataSource)`). `get_data(self, state, **kwargs)` and `get_data_range(self, start, end, **kwargs)` raise `RuntimeError("Implemented by subclass")`. **v2: Port**. This is the extension point for user data (e.g. a source built by evaluating an asset-config market expression). pricebt core must not ship any source that touches ARBS or GS.

### 7.3 `GsDataSource` — :60 — **Out**
Fields: `(data_set: str [required], asset_id: str [required], min_date: date=None, max_date: date=None, value_header: str='rate')`, `class_type='gs_data_source'`. It queries a Marquee `Dataset`. Its only relevance to v2: `EventTriggerRequirements` defaults to it (which v2 replaces with a required argument). Its `get_data_range` semantics are the same as Generic (int `end` → `tail(end)` of `index < start`; otherwise `start < index <= end`).

### 7.4 `GenericDataSource` — :98 (the one the user's example uses)

Signature: `GenericDataSource(data_set: pd.Series = None, missing_data_strategy: MissingDataStrategy = MissingDataStrategy.fail)`; `class_type='generic_data_source'` (init=False). Positional use in the notebook: `GenericDataSource(s, MissingDataStrategy.fill_forward)`.

| Method | Line | Exact behaviour (1.5.4 == 2.1.17) |
|---|---|---|
| `__post_init__` | :119 | `self._tz_aware = isinstance(data_set.index[0], dt.datetime) and data_set.index[0].tzinfo is not None`. An empty or `None` series raises (`IndexError`/`AttributeError`), so a series is effectively required. |
| `__eq__` | :114 | equal if `other` is a `GenericDataSource`, the strategies are equal, and `data_set.equals(other.data_set)` |
| `get_data(state)` | :122 | see the table below |
| `get_data_range(start, end)` | :160 | `end` is an `int` ⇒ `data_set.loc[index < start].tail(end)`, the last `end` points **strictly before** `start`. Otherwise ⇒ `data_set.loc[(start < index) & (index <= end)]`, i.e. **(start, end]**, left-open and right-closed. **`missing_data_strategy` is not applied.** No clock/look-ahead check here (that lives in `DataHandler`). |

`get_data(state)` decision sequence:

| Step | Condition | Result |
|---|---|---|
| 1 | `state is None` | the whole series |
| 2 | `isinstance(state, Iterable)` (list/tuple; a `str` is also Iterable, which is a quirk) | `[self.get_data(i) for i in state]` |
| 3 | series is tz-aware and `state` is naive | `state = state.replace(tzinfo=UTC)`. The naive time is **labelled UTC, not converted.** |
| 4 | `pd.Timestamp(state) in data_set` (index membership) | `data_set[pd.Timestamp(state)]` |
| 5 | `state in data_set` **or** strategy is `fail` | `data_set[state]`; with `fail` and a missing key ⇒ **`KeyError`** (verified) |
| 6 | missing and strategy is `fill_forward`/`interpolate` | **mutates `self.data_set` permanently**: insert `NaN` at `state` (DatetimeIndex: at `pd.to_datetime(state)`, then **re-assign** `sort_index()`; other index: at `state`, and **the `sort_index()` result is discarded**), then `data_set = data_set.interpolate()` (pandas default `method='linear'`, which **ignores index spacing** and treats points as equally spaced) or `data_set.ffill()`, then return the value at `state` |

Measured behaviour (pandas 2.3.1; series `{1st:1, 2nd:2, 4th:4, 5th:5}` of Jan 2024, lookup on the missing 3rd):

| index type | fill_forward | interpolate | fail |
|---|---|---|---|
| `DatetimeIndex` (Timestamp keys) | 2.0 ✔ | 3.0 ✔ | KeyError |
| object index of `dt.date` | **5.0 ✘** (look-ahead: NaN appended at the end, ffill from the last value) | **5.0 ✘** | KeyError |

Other measured facts:
- DatetimeIndex, lookup **before** the first point with fill_forward ⇒ `NaN` (no error). Lookup **after** the last point ⇒ the last value.
- DatetimeIndex series accepts both `dt.date` and naive `dt.datetime` in `get_data` (via `pd.Timestamp`).
- DatetimeIndex series with a `dt.date` passed to `get_data_range` ⇒ **`TypeError: Invalid comparison between dtype=datetime64[ns] and date`**. A `pd.Timestamp` or `dt.datetime` works.
- object-date-index series with a `dt.datetime` passed to `get_data` ⇒ inserts a datetime key, then `sort_index` raises `TypeError`, **leaving the series corrupted** (mixed key types).
- tz-aware series, naive `datetime(2024,1,2,10)` ⇒ found (labelled UTC). A missing naive 10:30 with fill_forward ⇒ the 10:00 value.

**v2 specification for `GenericDataSource`**: Port-fix. Same signature and same results on well-formed inputs; the defects are removed:
1. At construction, copy the series, sort the index, and normalise the keys. If every key is date-like with no time component and the series is not tz-aware, the index becomes `dt.date` objects; otherwise it becomes `pd.Timestamp` (tz preserved). Every incoming `state`/`start`/`end` is normalised to the same key type (date → date or Timestamp at midnight; naive datetime on a tz-aware series → labelled UTC, as in step 3).
2. `get_data`: exact hit → value. Miss with `fail` → `KeyError(state)`. Miss with `fill_forward` → the value at the **latest index ≤ state**, or `NaN` if there is none. Miss with `interpolate` → linear interpolation **by position**, with the missing key placed at its sorted position; this matches pandas default `interpolate()` on a sorted series; after the last point it returns the last value, and before the first point `NaN`. **Do not mutate the stored series** (the v1 mutation is an unobservable side effect for correct inputs; caching is optional).
3. `get_data_range`: exactly the (int → `tail` of strictly-before) / `(start, end]` rules, applied to the normalised index, returning a `pd.Series`.
4. `get_data(None)` → the whole series; `get_data(list)` → a list.

### 7.5 `DataManager` — :176 (PredefinedAssetEngine / DataHandler only)

| Method | Line | Semantics |
|---|---|---|
| `__post_init__` | :177 | `self._data_sources = {}` |
| `add_data_source(series: Union[pd.Series, DataSource], data_freq: DataFrequency, instrument: Instrument, valuation_type: ValuationFixingType)` | :180 | if `series` is not a `DataSource` and `len(series) == 0` → silently return. `instrument.name is None` → `RuntimeError('Please add a name identify your instrument')`. key = `(data_freq, instrument.name, valuation_type)`; a duplicate raises `RuntimeError('A dataset with this frequency instrument name and valuation type already added to Data Manager')`. Stores `GenericDataSource(series)` (so strategy `fail`) or the given `DataSource`. |
| `get_data(state, instrument, valuation_type)` | :198 | key = `(REAL_TIME if isinstance(state, dt.datetime) else DAILY, instrument.name.split('_')[-1], valuation_type)`; returns `self._data_sources[key].get_data(state)`. Note that `split('_')[-1]` takes the **last** underscore-separated segment of the name (a missing key gives `KeyError`). |
| `get_data_range(start, end, instrument, valuation_type)` | :206 | same key rule, using `start`'s type; `.get_data_range(start, end)` |

`DataFrequency` (gs_quant/data/core.py:29): `DAILY = 'daily'`, `REAL_TIME = 'realTime'`, `ANY = 'any'`. **v2:** Port (as `pricebt.backtests.data_sources.DataFrequency` or a re-export) only if PredefinedAssetEngine is in scope.

---

## 8. `data_handler.py` (82 lines): look-ahead guard (PredefinedAssetEngine only)

| Class/method | Line | Exact |
|---|---|---|
| `Clock.__init__` | :23 | `_time=None; reset()` |
| `Clock.reset` | :39 | `_time = datetime(1900,1,1, tzinfo=UTC)` |
| `Clock.update(time)` | :27 | if `time` is naive, compare against `_time` with tz stripped; `time < compare` ⇒ `RuntimeError(f'current time is {compare_time}, cannot run backwards to {time}')`; else `_time = time` |
| `Clock.time_check(state)` | :42 | datetime naive: lookahead = `state > _time.replace(tzinfo=None)`; datetime aware: `state > _time`; date: `state > _time.date()`; lookahead ⇒ `RuntimeError(f'accessing data at {state} not allowed, current time is {self._time}')` |
| `DataHandler.__init__(data_mgr: DataManager, tz: dt.timezone)` | :56 | `_data_mgr`, `_clock = Clock()`, `_tz` |
| `reset_clock()` / `update(state)` | :61/:64 | delegate to Clock |
| `_utc_time(state)` | :67 | naive datetime ⇒ `state.replace(tzinfo=self._tz).astimezone(UTC).replace(tzinfo=None)` (**interpreted in `tz`, converted to naive UTC**); a date or an aware datetime is unchanged |
| `get_data(state, *key)` | :74 | `time_check(state)`, then `data_mgr.get_data(_utc_time(state), *key)` |
| `get_data_range(start, end, *key)` | :78 | `time_check(end)`; `type(start) is not type(end)` ⇒ `RuntimeError('expect same type for start and end when asking for data range')`; then `data_mgr.get_data_range(_utc_time(start), _utc_time(end), *key)` |

**v2 recommendation (design input, not a gs feature):** the idea of a Clock-guarded handler is worth reusing in the GenericEngine equivalent. Market expressions and triggers should only see data at or before `pricebt_timestamp`. gs's GenericEngine has **no** such guard for triggers: `MeanReversion`/`Mkt` read the `DataSource` directly.

---

## 9. `order.py` (217 lines): PredefinedAssetEngine only

`OrderBase(metaclass=ABCMeta)` (order.py:25):

| Member | Line | Exact |
|---|---|---|
| `__init__(instrument: Instrument, quantity: float, generation_time: dt.datetime, source: str)` | :26 | sets `instrument, quantity, generation_time, source`, `executed_price=None` |
| `execution_end_time()` | :40 | `RuntimeError('The method execution_end_time is not implemented on OrderBase')` |
| `_execution_price(data_handler)` | :43 | `RuntimeError(...)` |
| `execution_price(data_handler)` | :46 | `price = self._execution_price(dh)`; `np.isnan(price)` ⇒ `RuntimeError('can not compute the execution price')` |
| `execution_quantity()` | :53 | `RuntimeError` (message wrongly says "execution_price") |
| `execution_notional(data_hander)` | :56 | `execution_price(dh) * execution_quantity()` |
| `_short_name()` | :59 | `RuntimeError` |
| `to_dict(data_hander)` | :62 | `{'Instrument': self.instrument.ric, 'Type': self._short_name(), 'Price': execution_price(dh), 'Quantity': execution_quantity()}` |

Subclasses. Execution prices are **memoised in `executed_price`** on first computation:

| Class | Line | `__init__` (positional order) | `execution_end_time()` | `_execution_price(dh)` | `execution_quantity()` | `_short_name()` |
|---|---|---|---|---|---|---|
| `OrderTWAP(OrderBase)` | :71 | `(instrument, quantity, generation_time, source, window: TimeWindow)` | `window.end` | `np.mean(dh.get_data_range(window.start, window.end, instrument, ValuationFixingType.PRICE))`, i.e. the mean of fixings in **(start, end]** | `quantity` | `'TWAP {start}:{end}'` |
| `OrderMarketOnClose(OrderBase)` | :100 | `(instrument, quantity, generation_time, execution_date: dt.date, source)` (**note: `execution_date` comes before `source`**) | `datetime.combine(execution_date, time(23,0,0))` | `dh.get_data(execution_date, instrument, PRICE)` (daily key) | `quantity` | `'MOC'` |
| `OrderCost(OrderBase)` | :127 | `(currency: str, quantity: float, source: str, execution_time: dt.datetime)`; instrument = `Cash(currency)`, `generation_time=execution_time` | `execution_time` | `0` | `quantity` | `'Cost'`; `to_dict` uses `'Instrument': self.instrument.currency` |
| `OrderAtMarket(OrderBase)` | :159 | `(instrument, quantity, generation_time, execution_datetime: dt.datetime, source)` | `execution_datetime` | `dh.get_data(execution_datetime, instrument, PRICE)` (real-time key) | `quantity` | `'Market'` |
| `OrderTwapBTIC(OrderTWAP)` | :188 | `(instrument, quantity, generation_time, source, window, btic_instrument: Instrument, future_underlying)` | `window.end` | `np.mean(btic fixings over window) + dh.get_data(window.end.date(), future_underlying)`. **Bug:** the `get_data` call passes no `valuation_type`, which `DataManager.get_data` requires (it would `TypeError`). | `quantity` | `'TwapBTIC'` |

**v2 disposition:** Port only with a PredefinedAssetEngine equivalent. If ported, `instrument.ric` becomes the asset-config trade's display name, and `Cash(currency)` becomes a pricebt cash asset. Fix the BTIC missing argument.

---

## 10. Version drift: installed 1.5.4 vs repo 2.1.17 (files in scope)

Diffed with `diff --strip-trailing-cr -w`. **No dataclass field order or default changed in any file except `EventTriggerRequirements` (new trailing fields) and `CustomDuration.function` (gained a default).**

| File | Change in 2.1.17 | Behavioural? | v2 follows |
|---|---|---|---|
| triggers.py | `PeriodicTriggerRequirements.get_trigger_times`: `RelativeDateSchedule(self.frequency.lower(), ...)` (repo:117) | **yes**: `'1M'` means month instead of Nth Monday | 2.1.17 |
| triggers.py | MR: `AddTradeActionInfo(scaling=±1, next_schedule=None)` in all 4 returns | **yes**: 1.5.4 raises TypeError | 2.1.17 |
| triggers.py | MR long branch: `self.current_position = 0` (1.5.4: `self._current_position = 0`) | **yes** | 2.1.17 |
| triggers.py | MR short branch: `if current_price < rolling_mean` (1.5.4: `>`) | **yes** | 2.1.17 |
| triggers.py | `EventTriggerRequirements` new fields after `data_source`: `country, currency, source, start, end` (all `Optional[...] = None`); forwarded as `get_data` kwargs (`source` str → `[source]`; `start`/`end` both passed if either set) | yes (additive) | 2.1.17 signature; semantics per §6.5.11 |
| triggers.py / data_sources.py / strategy.py / core.py | `List`/`Tuple` → builtin `list`/`tuple` hints; import reordering | no | — |
| backtest_utils.py | `get_final_date`: `RelativeDate(duration.lower(), create_date)` | yes (case) | 2.1.17 (+ timedelta fix) |
| backtest_utils.py | `CustomDuration.function` gets `default=None` + callable encoder/decoder | minor (ctor now allows omitting `function`) | 2.1.17 |
| backtest_utils.py | new `encode_duration`, `decode_duration`, `_TIMEDELTA_PATTERN`, `parse_timedelta` | additive | optional |
| order.py, data_handler.py, event.py, `__init__.py` | import order / blank lines only | no | — |

---

## 11. v2 disposition summary (one line per public symbol in scope)

| Module | Port | Port-fix | Stub | Out |
|---|---|---|---|---|
| `__init__` | submodule layout; core enums | — | — | `Backtest`, `BacktestResult`, `StrategySystematic`, `DeltaHedgeParameters` |
| `core` | `TimeWindow`, `ValuationFixingType`, `ValuationMethod`, `MarketModel`, `TradeInMethod` | — | — | `Backtest.get_results` |
| `event` | all 5 (if PredefinedAssetEngine is in scope) | — | — | — |
| `strategy` | `Strategy` fields/`__post_init__`/`get_risks` | `get_available_engines` (zero-trigger case) | — | engine list with EquityVol / Predefined (unless in scope) |
| `backtest_utils` | `CalcType`, `CustomDuration`, `make_list`, `interpolate_signal`, `scale_trade` (generic) | `get_final_date` (timedelta, case, per-run cache, no GS calendar) + a self-contained RelativeDate/Schedule (`d,b,w,m,y`, compound rules) | — | `map_ccy_name_to_ccy` |
| `triggers` | `TriggerDirection`, `AggType`, `TriggerInfo`, `check_barrier`, base classes, Periodic*, Mkt, Date, Portfolio, TradeCount, StrategyRisk (with named risk fn) | MeanReversion (2.1.17 + reset), Aggregate (times union, ctor error), Not (calc_type, setattr), Intraday (wrap guard), Event (required source), info keyed by date in the engine | `risk_transformation` (None only) | GS event calendar default, `list_events`, JSON registry patch |
| `data_sources` | `MissingDataStrategy`, `DataSource` | `GenericDataSource` (normalised index, correct ffill/interp, no mutation) | `DataManager` (if PredefinedAssetEngine is out) | `GsDataSource` |
| `data_handler` | `Clock`, `DataHandler` (if PredefinedAssetEngine is in scope; the concept is reusable as a look-ahead guard) | — | — | — |
| `order` | all (if PredefinedAssetEngine is in scope) | `OrderTwapBTIC` missing arg | — | — |

**Zero-dependency reminder:** every Port/Port-fix item above must be implemented inside pricebt, using only the stdlib plus numpy/pandas (plus python-dateutil if allowed; otherwise hand-roll month addition with end-of-month clipping). Do not import `gs_quant`, rateslib, QuantLib or ARBS, and do not reuse their names beyond the gs public API names listed here. Trade scaling, risk measures and prices come **only** from asset-config expressions.

---

## 12. Every proposed deviation from gs 2.1.17 (the design author must accept or reject each)

The user asked for "nearly 1:1". Everything else in this note is strict parity with 2.1.17. The items below are the only behavioural deviations it proposes. Each one fixes a crash, a look-ahead, or a dependency on GS infrastructure.

| # | Where | gs 2.1.17 behaviour | Proposed v2 behaviour | Why |
|---|---|---|---|---|
| D1 | `get_final_date` (§5.4) | `dt.timedelta` duration crashes (`AttributeError`) | `create_date + duration` | the `Duration` type and the docstrings promise it |
| D2 | `get_final_date` (§5.4) | module-global cache that is never cleared | per-run cache, or no cache | stale results across runs |
| D3 | business-day rules (§5.4/5.5) | `holiday_calendar=None` → `GsCalendar([])` | weekends only, no data access | zero-dependency rule |
| D4 | `PeriodicTriggerRequirements.start_date=None` (§6.5.1) | today / ambient PricingContext date | backtest start date | pricebt has no ambient context |
| D5 | `NotTriggerRequirements.calc_type` (§6.5.6) | always `simple` | = child's `calc_type` | Not(Risk) fires every day in gs |
| D6 | `NotTriggerRequirements.__setattr__` (§6.5.6) | drops every attribute except `trigger` | normal attributes | bug |
| D7 | Aggregate/Not `get_trigger_times` (§6.5.5) | `[]` | union of the children's times | child Date/Event dates are otherwise lost |
| D8 | `AggregateTriggerRequirements()` with no triggers (§6.5.5) | `TypeError` | `ValueError('triggers required')` | clearer error |
| D9 | Intraday schedule (§6.5.2) | infinite loop on midnight wrap | stop when the time wraps | bug |
| D10 | MR / stateful triggers (§6.5.9) | state persists across `run_backtest` calls | engine resets trigger state at run start | re-running a strategy gives different results |
| D11 | engine `trigger_info` routing (§6.8) | parallel lists zipped positionally | infos keyed by date | misattribution with ANY_OF |
| D12 | `EventTriggerRequirements.data_source=None` (§6.5.11) | GS `MACRO_EVENTS_CALENDAR` | `ValueError`; a source is required | zero-dependency rule |
| D13 | `GenericDataSource` (§7.4) | mutates the series; date-index ffill look-ahead; `DatetimeIndex` vs `date` TypeError | normalised sorted index, no mutation, correct ffill/interp, date/Timestamp coercion | correctness |
| D14 | `Strategy.get_available_engines` (§4) | zero triggers → `TypeError` in `reduce` | returns the engine | bug |
| D15 | `OrderTwapBTIC` (§9) | missing `valuation_type` argument | pass `PRICE` | bug (only if PredefinedAssetEngine is in scope) |

Parity is deliberately **kept** for these quirks, which should be documented in the v2 user docs: ALL_OF short-circuit / ANY_OF evaluates every child (G5); Portfolio `'len'` counts dates (G11); strict `>`/`<` in `check_barrier`; the MR window excludes today; sample std (ddof=1); zero std → `inf` → fires; `get_data_range` is (start, end] / int→tail strictly before; `interpolate` is by position, not by time.
