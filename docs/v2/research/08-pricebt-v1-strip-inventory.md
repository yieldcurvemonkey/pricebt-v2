# 08 - pricebt v1 strip inventory (everything outside engine/ and strategy/)

## Key facts

1. **BLOCKER: `src/pricebt/results/` is MISSING from the v2 worktree.** `.gitignore` line 9 is the unanchored pattern `results/`, which also ignored the package `src/pricebt/results/` (10 files, 1,743 lines) when the baseline commit `c05d515` was made. `import pricebt.backtests` fails today at `backtests/generic_engine.py:31` (`from ..results import BacktestResult` -> `ModuleNotFoundError`). Fix first: change `.gitignore` to `/results/` and `/results_new/`, then copy `C:/Users/chris/clee/gsquant-temp-claude/pricebt/src/pricebt/results/*.py` (read-only source) into the worktree.
2. **v1 `timeutil.apply_tenor` is NOT identical to gs `RelativeDate`** in 3 verified cases (the `y` weekend roll, negative `w`, `0b` on a holiday or weekend); `TimeGrid.daily` also drops a start date that is not a business day, which gs keeps. v2 must fix these (section 7) if the API is to be "nearly 1:1".
3. v1 aimed at the **gs-quant repo checkout** (`C:/Users/chris/clee/gsquant-temp-claude/gs-quant`, which has `AddWeightedTradeAction`, `EarlyExitPositionLimitScaledAction`, `BackTest.summary_stats`). The **installed site-packages gs_quant 1.5.4** that the user names as the reference has none of the three. v2 has to choose one (see Open questions).
4. **What survives:** `timeutil.py` (fixed), `config/yamlio.py` (reusable as is for asset configs), the gs facade *shapes* (`backtests/*`, `risk.RiskMeasure`-as-`str`, `Backtest` views, gs transaction and cash-accrual models, `PricebtSession` lifecycle), `errors.py`, `tools/mutcheck.py`, `tools/nb_build.py`, the import-blocker guards, and the engine, strategy and results tests (after adaptation). **Deleted:** `contracts/`, `config/{loader,stacks,cli}.py`, `snapshot.py`, `market.py`'s wrap machinery, `pricer.py`'s snapshot index, `contrib/`, `testing/{refstack,synthetic,layer_conformance}.py`, `tieout/`, `api.py`, `arbs_adapter/`, `skills/`, `configs/`, `tasks/`, most of `tools/`, and 77 of the 123 test `.py` files.
5. **The seam to replace:** engine, strategy, orders and view import only these names from stripped modules: `TradeTemplate`, `Built`, `layer_ref` (contracts.spec), `Env` and `TENOR` (contracts.binding), `evaluate_value`, `evaluate_measure`, `evaluate_layer`, `scalar_of`, `vector_of`, `vector_add`, `vector_scale`, `tenor_sort_key`, `is_vector` (contracts.evaluate), `is_term_ref` (contracts.schema), `MarketData` (market), `MarkContext`, `Valuation`, `as_valuation` (pricable), `Pricer` (pricer). Also `registries.py:23` imports `testing.toys`, so deleting toys breaks `import pricebt.strategy`. The v2 asset layer must provide replacements for exactly this list (section 9).

Scope. This note covers `src/pricebt/{contracts,config,market.py,snapshot.py,pricer.py,instrument.py,session.py,risk.py,common.py,backtests/,contrib/,testing/,tieout/,api.py,__main__.py,__init__.py}`, the time utilities, and the top-level folders. `engine/`, `strategy/`, `orders.py`, `costs.py`, `accrual.py`, `view.py`, `results/` are covered in other notes; here they appear only where they touch the seam. All paths are relative to `C:/Users/chris/clee/gsquant-temp-claude/pricebt-v2/` unless absolute. "gs" = gs_quant. "gs-1.5.4" = `C:/Users/chris/anaconda3/Lib/site-packages/gs_quant` (version 1.5.4). "gs-repo" = `C:/Users/chris/clee/gsquant-temp-claude/gs-quant/gs_quant`.

Verdicts: **DELETE** = remove the file(s) in v2. **KEEP** = keep as is (small edits at most). **ADAPT** = keep the idea or most of the code, but rewrite the parts named in the row.

---

## 1. Blocker detail: the missing `results` package

| Item | Fact |
|---|---|
| Cause | `.gitignore` lines 8-10: `# run outputs (regenerable)`, `results/`, `results_new/`. A pattern without a leading `/` matches at any depth, so `src/pricebt/results/` was never committed. (`AGENTS.md` already warned: "a bare `results` also drops `src/pricebt/results`".) |
| Evidence | `git -C pricebt-v2 ls-files \| grep -c .` = 499 files; none under `src/pricebt/results/`. `PYTHONPATH=src python -c "import pricebt.backtests"` -> `ModuleNotFoundError: No module named 'pricebt.results'` at `backtests/generic_engine.py:31`. `import pricebt.engine, pricebt.strategy` works. |
| Source to copy | `C:/Users/chris/clee/gsquant-temp-claude/pricebt/src/pricebt/results/` (primary checkout; read-only for us): `__init__.py` 18, `attribution.py` 170, `compare.py` 98, `errors.py` 30, `io.py` 151, `ledger.py` 104, `reconcile.py` 179, `report.py` 159, `result.py` 212, `stats.py` 312, `tearsheet.py` 310 lines. |
| Who breaks without it | `backtests/generic_engine.py`, `api.py`, `config/loader.py`, and 8 test files (`test_results_*.py`) plus `test_engine_audit.py`, `test_engine_baseline.py`, `test_engine_cycle3.py`, `test_engine_observation.py`. |
| Fix (step 0 of the implementation plan) | 1) Edit `.gitignore`: `results/` -> `/results/`, `results_new/` -> `/results_new/`. 2) Copy the 11 files. 3) Commit on `v2-redesign`. 4) `PYTHONPATH=src python -c "import pricebt.backtests"` must succeed. |

---

## 2. Source inventory: `src/pricebt` (in-scope modules)

Line counts are `wc -l`.

### 2.1 Package-level files

| Path | Lines | Purpose (v1) | Verdict | Reasoning |
|---|---|---|---|---|
| `__init__.py` | 2 | docstring + `__version__ = "0.1.0"` | ADAPT | Keep; bump to `2.0.0`; rewrite the docstring (no "pricer/pricable-agnostic via bindings"). |
| `__main__.py` | 3 | `from .config.cli import main; raise SystemExit(main())` | DELETE | The CLI (`config/cli.py`) runs YAML *backtest* configs through `config/loader.py`, both deleted. v2's entry point is the gs-style Python API. Add a CLI only if asked. |
| `api.py` | 41 | `load` / `build` / `run` for a config path or dict, with `stack=` overlays | DELETE | Wraps `config.loader.build` and stack overlays. It has no gs equivalent; the gs entry point is `GenericEngine().run_backtest(...)`. |
| `common.py` | 144 | gs enums (`PayReceive`, `BuySell`, `AggregationLevel`, `OptionType`, `OptionStyle`, `BacktestTradingQuantityType`, `Currency`), `_GsEnum.coerce`, `check_currency`, lazy `RiskMeasure` re-export | ADAPT | Keep the enums and `_GsEnum.coerce` (lines 19-35: it accepts a member, a value or a name, case-insensitive, and a foreign enum through `.value`). **Rewrite `check_currency` (lines 119-136).** It enforces ONE reporting currency and raises `NotSupportedError` for any other ("multi-currency is out of scope, spec C7"), which contradicts the v2 multi-currency requirement. `Currency` (lines 90-116) lists 24 ISO codes; v2 should accept any 3-letter code. |
| `instrument.py` | 259 | gs-style constructors: `IRSwap(...)` returns an `InstrumentTemplate` (terms on a spec that a session Stack registered); `IRSwaption` and 16 other gs instruments are stubs raising `NotSupportedError` | ADAPT | Salvage and v1-only parts are in section 3.4. In v2 an instrument constructor becomes "asset config + opaque kwargs". |
| `risk.py` | 245 | gs risk measures as `str` subclasses mapped to pricebt measure names | ADAPT | See section 3.5. The `str` trick is the most valuable facade piece. `_vector_names()` reads the schema registry and must change. |
| `session.py` | 265 | `PricebtSession` (replaces `GsSession.use()`): market, tz, calendar, date policy, stack, instrument specs | ADAPT | See section 3.6. Keep the lifecycle and time context; delete stack, spec and wrap. |
| `market.py` | 126 | `Binding(mdp, request, wrap)`, `freeze`, `MarketData` (memo LRU keyed `(role, ts.value, freeze(request))`, look-ahead guard, wrap once per snapshot digest, audit rows) | ADAPT | The *guard* and the *memo* are exactly what a v2 "market expression evaluated at each timestamp" needs: never evaluate for `ts > clock.now`, memoise per `(asset market key, ts)`. Delete `Binding.wrap`, `_wrap`, `audit`, `available_timestamps`, and the digest logic. |
| `snapshot.py` | 328 | neutral plain-data snapshots (`CurveSnapshot`, `FixingsSeries`, `CalendarData`, `Quote`, `Security`, `QuoteSet`, `MarketSnapshot`, `SnapshotPricer`) with sha256 `digest()` | DELETE | v2 core never sees market *data*; the asset config's market expression returns an opaque object (for example an ARBS pricer). |
| `pricer.py` | 155 | `Pricer` Protocol (`ts`, `lookup`, `describe`), `PricerBase`, `MarketDataProvider` Protocol (`get_pricer(ts, request)`), `TimeMapping`, `SnapshotIndex` (asof/exact/date selection), `FunctionMDP` | DELETE (mostly) | `TimeMapping` and `SnapshotIndex` serve frame-backed snapshot providers, which v2 does not have. `Pricer` is imported by `engine/engine.py:25`, `engine/state.py:13` and `view.py:10` for type hints only; replace it with `Any` or a v2 `Market` alias. `FunctionMDP` (lines 142-155) is a 10-line adapter; drop it. |
| `pricable.py` | 54 | `Valuation(ts, pv, cash, financing)`, `as_valuation`, `MarkContext` | ADAPT (engine note owns it) | On the seam: the engine consumes `Valuation`. v2 asset pricing functions return floats; keep `Valuation` as the engine's internal record. |
| `registry.py` | 90 | `Registry` + `resolve_dotted(path, allow=("pricebt",))`, an allow-listed `pkg.mod:Attr` importer | DELETE | Only config text used it (factories, wraps, `type:`). In v2 an asset config carries literal import lines that pricebt executes, so a dotted-path allow-list protects nothing. |
| `registries.py` | 25 | shared name registries (`TRIGGERS`, `ACTIONS`, `SIGNALS`, `COSTS`, `CASH_ACCRUAL`, `MDPS`, `CALENDARS`) + `MDPS.register("toy", ToyMDP)` | ADAPT (strategy note owns it) | Strategy components register themselves here (the config loader resolved `type:` names through it). **Line 23 `from .testing import toys as _toys` must go**, or deleting toys breaks `import pricebt.strategy`. `CALENDARS` feeds `session.resolve_calendar`; keep it if named calendars stay. |
| `timeutil.py` | 367 | Calendar, tenor grammar, schedule, TimeContext, TimeGrid, Clock, TimelineContext | KEEP + FIX | Section 7. |
| `errors.py` | 87 | exception hierarchy (`PricebtError`, `ConfigError(message, path, code)`, `MarketDataUnavailable`, `LookAheadError`, `MeasureError`, `NotSupportedError`, ...) | KEEP | Every kept module uses it. `StaleSnapshot` and `OptionalDependencyError` become unused; delete them when nothing imports them. |
| `types.py` | 31 | `Phase` IntEnum, `RESERVED_LAYERS`, `BASELINE_LAYERS`, `as_list` | KEEP (engine note) | `as_list` is used by the facade (`generic_engine.py:37`). |

### 2.2 `contracts/` (1,493 py lines + 340 yaml lines): DELETE all

| Path | Lines | Purpose | Verdict | Notes for the implementer |
|---|---|---|---|---|
| `contracts/__init__.py` | 1 | docstring | DELETE | |
| `contracts/schema.py` | 588 | neutral asset-class schemas (`AssetSchema`, `NameSpec`, `TermSpec`, `Direction`, `SchemaRegistry`, strict YAML loader, `extends`, `check_term`, `normalise_terms`, `check_resolved`) | DELETE | v2 has no schemas: an asset config declares its own functions. `is_number` (line 59) and `is_term_ref` (line 44) are imported by `instrument.py` and `strategy/actions.py:16`; inline a local `is_number` in the facade. `is_term_ref` goes away together with `@signal`/`@param` term references (strategy note decides). |
| `contracts/schemas/{swap,bond,generic}.yaml` + `__init__.py` | 153 / 143 / 44 / 0 | the shipped schemas (terms, methods, measures, layers, conventions vocabulary) | DELETE | They are also listed in `pyproject.toml` package-data, which must be removed. |
| `contracts/binding.py` | 367 | bindings: schema name -> callable; closed `@pricer/@ctx/@instrument/@terms/@state` reference grammar; `scale/offset/sign/reduce/keys`; reducers (`series_to_tenor_dict`) | DELETE | Replaced by asset-config expressions. **Salvage `TENOR = re.compile(r"\d+[DWMY]")` (line 39)**, used by `strategy/signals.py:18` and by the ladder helpers. |
| `contracts/spec.py` | 384 | `Built`, `Kit`, `Stack`, `InstrumentSpec`, `build_spec`, `TradeTemplate` (`build(pricer, ts)` calls `factory(pricer, ts, *, terms, conventions)`), `conventions_digest`, `layer_ref` | DELETE | `TradeTemplate` is THE seam object (section 9). Its behaviour to reproduce in v2: `name`, `with_terms(**changes)`, `renamed(name)`, `build(market, ts) -> (obj, resolved_terms)`, and a `__deepcopy__` that shares the immutable spec (lines 376-384). |
| `contracts/evaluate.py` | 153 | evaluate `value`/measures/layers through bindings + plain-dict vector maths | DELETE the evaluation, **KEEP the vector helpers** | Move these into a small v2 module (for example `pricebt/ladder.py`): `tenor_sort_key` (lines 27-30; order key `_UNIT_ORDER = {"D":1,"W":7,"M":31,"Y":372}`), `check_ladder` (34-54: mapping of canonical tenor keys to finite floats, returned in tenor order), `scalar_of` (57-61: a dict collapses to the sum of its buckets), `vector_of` (64-67), `vector_add(a, b, scale)` (70-75: union of buckets, a's order first), `vector_scale` (78-79). They are library-free and are exactly the maths a v2 `{tenor: ccy per bp}` delta ladder needs. The engine imports them at `engine/engine.py:17`. |

### 2.3 `config/` (1,002 lines)

| Path | Lines | Purpose | Verdict | Reasoning |
|---|---|---|---|---|
| `config/__init__.py` | 0 | | KEEP (if yamlio stays here) | |
| `config/yamlio.py` | 188 | hardened YAML/JSON loader | **KEEP** | Section 4. |
| `config/loader.py` | 637 | YAML backtest config -> object graph (`TOP_KEYS = backtest, market, instruments, trades, params, signals, strategy, registry, outputs, meta, name, doc, extends, tieout`), `Built.run()`, `config_hash` | DELETE | A whole-backtest YAML DSL with no gs equivalent, tied to specs, stacks, registries and tie-out. The v2 YAML is the *asset* config only. |
| `config/stacks.py` | 86 | stack overlays (only `instruments.<n>.factory/.bind` and `market.pricers.<r>.wrap` may change) + `base_digest` | DELETE | The tie-out concept is stripped. |
| `config/cli.py` | 91 | `pricebt run|validate|describe|tieout` | DELETE | It dies with loader and tie-out; `pyproject.toml` `[project.scripts]` must drop `pricebt = "pricebt.config.cli:main"`. |

### 2.4 `backtests/` (913 lines): the gs facade, ADAPT (detail in section 3)

| Path | Lines | gs counterpart | Verdict |
|---|---|---|---|
| `backtests/__init__.py` | 35 | `gs_quant.backtests` re-exports | ADAPT: keep the import map; add the gs names still missing (section 3.1). |
| `backtests/strategy.py` | 10 | `backtests.strategy.Strategy` | KEEP (re-export of `strategy.strategy.Strategy`, `PositionSpec`). |
| `backtests/triggers.py` | 30 | `backtests.triggers` | KEEP (re-exports; the class bodies live in `strategy/`). |
| `backtests/actions.py` | 154 | `backtests.actions` | ADAPT: `_flatten`, `_gs_costs`, `_prep_entry` stay; `_gs_match` (lines 104-112) reads `spec.gs_name` and `m.template`, which must be re-pointed at the v2 trade object. |
| `backtests/backtest_objects.py` | 417 | `backtests.backtest_objects` | ADAPT: keep the transaction models, accrual models, `ResultSummary` and `Backtest`; add multi-currency reporting. |
| `backtests/generic_engine.py` | 162 | `backtests.generic_engine.GenericEngine` | ADAPT: keep the signature and `_as_state`/`_grid`/`_risks`; `result_ccy` becomes real (it is refused today); `_settings` stays. |
| `backtests/data_sources.py` | 49 | `backtests.data_sources` | KEEP. |
| `backtests/gs_fields.py` | 12 | (the gs `IRSwap` field names) | DELETE or REFERENCE: in v2 the gs kwargs pass through opaquely to the asset trade expression, so a refusal list has nothing to refuse. |
| `backtests/equity_vol_engine.py` | 24 | `EquityVolEngine` | KEEP (stub raising `NotSupportedError`). |
| `backtests/predefined_asset_engine.py` | 20 | `PredefinedAssetEngine` | KEEP (stub). |

### 2.5 `contrib/` (2,899 lines): DELETE all

| Path | Lines | Purpose | Verdict |
|---|---|---|---|
| `contrib/__init__.py` | 1 | docstring | DELETE |
| `contrib/rateslib/{__init__,_compat,bond,conventions,ladder,layers,pricer,swap,wrap}.py` | 36+152+316+95+90+152+145+312+243 = 1,541 | rateslib adapter: `wrap(SnapshotPricer)->RLCurvePricer`, `swap`/`bond` Kits, P&L layers, par-swap risk-curve ladder, `STACK`, registers the named calendar `nyc` | DELETE (user directive: no external pricing library in pricebt). |
| `contrib/quantlib/{__init__,_compat,_ois,_risk,_schedule,bond,conventions,swap,wrap}.py` | 26+137+39+105+67+321+113+404+145 = 1,357 | QuantLib adapter, same shape | DELETE |

### 2.6 `testing/` (1,786 lines)

| Path | Lines | Purpose | Verdict | Reasoning |
|---|---|---|---|---|
| `testing/__init__.py` | 0 | | KEEP | |
| `testing/toys.py` | 319 | pure-python toy market (`ToyPricer`, `ToyMDP`: a scalar RATE in percent plus a vol, moving deterministically) and toy instruments (`ToyForward`, `ToyZero`, `ToyOption`, `ToyFuture`) shipped as `Kit`s with default binding blocks; `toy_template(obj)`; `toy_schemas()` | **ADAPT** | It is the workhorse of every engine, strategy and results test (33 test files use `toys`/`ToyMDP`/`toy_tpl`). Keep the closed-form toy maths. Replace the Kit/bind/schema plumbing with a **toy asset config** (a YAML with `imports`, `market`, `trade`, `pricing` expressions pointing at `pricebt.testing.toys`). That config is also the smallest executable example of the v2 asset-config format, and it lets the whole engine suite run with no ARBS. |
| `testing/scripted.py` | 41 | `ScriptedStrategy`: submits prepared `orders.Instruction`s at given timestamps | KEEP (engine note) | No stripped dependency. |
| `testing/synthetic.py` | 213 | `SyntheticMarket` (Nelson-Siegel factor walks -> `MarketSnapshot`), `flat_swap_world`, `flat_bond_world` | DELETE | Emits `snapshot.py` types for the adapters. |
| `testing/refstack.py` | 1,028 | the dependency-free reference stack: OIS and bond priced from first principles (numpy only) as the tie-out known answer | DELETE | User directive ("reference stack" is stripped). Note: it is the only pure-numpy OIS pricer in the tree. If v2 ever wants a library-free *test* swap asset, write a much smaller flat-curve one under `tests/`, not this. |
| `testing/layer_conformance.py` | 185 | P&L layer identity checks per adapter | DELETE | Layers-through-bindings are stripped. |

### 2.7 `tieout/` (932 lines): DELETE all

| Path | Lines | Purpose |
|---|---|---|
| `tieout/__init__.py` | 10 | re-exports |
| `tieout/compare.py` | 453 | L0-L4 level-by-level comparison of two runs |
| `tieout/report.py` | 137 | markdown/HTML report |
| `tieout/runner.py` | 117 | run one base config under N stacks, self-test X5a |
| `tieout/tolerances.py` | 215 | per-level/asset tolerances |

Reason: the cross-library tie-out exists only because v1 ran one config under several libraries. v2 has one asset config per asset and no stacks. `engine/engine.py` has `audit` settings that fed the tie-out; the engine note decides whether to drop them (`test_engine_cycle3.py` imports `pricebt.tieout`).

---

## 3. The v1 gs facade in detail

### 3.1 Import map: v1 vs gs

`backtests/__init__.py` exports (`__all__`, lines 26-35): `Strategy, GenericEngine, EquityVolEngine, PredefinedAssetEngine, Backtest, BackTest`, 10 triggers + their requirements, `TriggerDirection, AggType`, 9 actions + `ScalingActionType`, `GenericDataSource, GsDataSource, MissingDataStrategy`, 5 transaction/accrual models + `TransactionAggType`.

| gs name | gs-1.5.4 | gs-repo | v1 | Note |
|---|---|---|---|---|
| `AddWeightedTradeAction` | absent | present (`actions.py:242`) | present | v1 follows gs-repo. |
| `EarlyExitPositionLimitScaledAction` | absent | present (`actions.py:499`) | present | v1 follows gs-repo. |
| `ExitPositionAction` | present (`actions.py:292`) | present | **absent** | v2: add it, or document it as a deviation. |
| `EnterPositionQuantityScaledAction` | present | present | stub (`NotSupportedError`) | EquityVolEngine only. |
| `BackTest.summary_stats` | absent | present (`backtest_objects.py:393`) | present | v1 follows gs-repo. |
| `OisFixingCashAccrualModel(start_date='-1y', end_date=today)` | present | present | stub | needs GS servers. |
| `PredefinedAssetBacktest(data_handler, initial_value)` | present | present | absent | PredefinedAssetEngine only. |
| `DataManager` (data_sources) | present | present | absent | |
| `TriggerInfo(triggered, info_dict=None)` | present | present | `TriggerInfo(triggered, info=None, *, info_dict=None, strict=True)` | Positionally compatible. |

### 3.2 Constructor field order: gs-1.5.4 vs v1 (introspected)

Obtained with `dataclasses.fields` on both packages (gs: `C:/Users/chris/anaconda3/python.exe`; v1: primary checkout, `python -B`). `*` = keyword-only in v1 (v1 adds keyword-only extensions *before* the gs fields in the dataclass MRO, but they are `kw_only`, so the gs positional order is kept).

| Class | gs-1.5.4 positional fields (defaults) | v1 differences |
|---|---|---|
| **every action with a `transaction_cost` field** (`AddTradeAction`, `AddScaledTradeAction`, `HedgeAction`, `ExitTradeAction`, `ExitAllPositionsAction`, `RebalanceAction`, `EnterPositionQuantityScaledAction`) | `transaction_cost=<factory>` = `default_transaction_cost()`, a zero `ConstantTransactionModel` | v1 default is `None` everywhere. v2 for 1:1: use the gs factory default |
| `AddTradeAction` | `priceables=None, trade_duration=None, name=None, transaction_cost=<factory>, transaction_cost_exit=None, holiday_calendar=None` | `transaction_cost` default is `None`, not gs's `default_transaction_cost()` factory (a zero `ConstantTransactionModel`); extras `tags*, phase*, dated_priceables*, pricer_request*, on_past_exit*='raise', quantity*=1.0` |
| `AddScaledTradeAction` | `priceables, trade_duration, name, scaling_type=ScalingActionType.size, scaling_risk=None, scaling_level=1, transaction_cost=<factory>, transaction_cost_exit, holiday_calendar` | same order; extras `after_last*='zero', on_zero_price*='raise'` + the common ones |
| `AddWeightedTradeAction` (gs-repo) | `priceables, trade_duration, name, scaling_risk=None, total_size=100000.0, transaction_cost, ...` | same; extra `weighting*='risk_proportional'` |
| `HedgeAction` | `risk, priceables, trade_duration, name, csa_term, scaling_parameter='notional_amount', transaction_cost, transaction_cost_exit, risk_transformation, holiday_calendar, risk_percentage=100` | `risk_percentage=100.0`; extras `mode*='add', target*=0.0, scope*, min_trade_risk*=0.0, on_zero_hedge_risk*='raise', vector*, ridge*=0.0, bucket_weights*` |
| `ExitTradeAction` | `priceable_names=None, name=None, transaction_cost=<factory>` | extras `match_tags*='any', actions*, kinds*, position_ids*, select*, reason*` |
| `ExitAllPositionsAction` | `priceable_names, name, transaction_cost` (subclass of `ExitTradeAction`) | same + extras |
| `RebalanceAction` | `priceable=None, size_parameter=None, method=None, transaction_cost, transaction_cost_exit, name` | **`size_parameter` default `'quantity'` (gs `None`)**; extras `mode*, measure*, target*, scope*, match_templates*, trade_duration*=INHERIT`; a trailing `holiday_calendar=None` not in gs |
| `PeriodicTriggerRequirements` | `start_date, end_date, frequency, calendar` | extras `time*=None, adjust_start*='following'` |
| `IntradayTriggerRequirements` | `start_time, end_time, frequency` | extras `tz*, wrap_midnight*=False, days*` |
| `MktTriggerRequirements` | `data_source, trigger_level, direction` | extras `tol*=0.0, on_missing*='raise'` |
| `RiskTriggerRequirements` | `risk, trigger_level, direction, risk_transformation` | extras `scope*='portfolio', when_flat*='skip', include_pending*=True, check_every*` |
| `DateTriggerRequirements` | `dates, entire_day=False` | extras `fire*='all', time*` |
| `AggregateTriggerRequirements` | `triggers=None, aggregate_type=AggType.ALL_OF` | `triggers=()` |
| `NotTriggerRequirements` | `trigger` | same |
| `MeanReversionTriggerRequirements` | `data_source, z_score_bound, rolling_mean_window, rolling_std_window` | extras `min_periods*, ddof*=1, exit_mode*='offset', exit_z*, allow_short*=True` |
| `PortfolioTriggerRequirements` | `data_source=None, trigger_level, direction` | `data_source='len'`; extras `scope*, tol*, include_pending*` |
| `TradeCountTriggerRequirements` | `trade_count, direction` | extras `count*='positions', scope*, include_pending*` |
| `EventTriggerRequirements` | gs-1.5.4: `event_name, offset_days=0, data_source` | v1 = gs-repo order: `event_name, offset_days, country, currency, source, start, end, data_source` (**a positional `data_source` lands in `country` under gs-1.5.4 semantics**) |
| `Trigger` | `trigger_requirements=None, actions=None` | extras `*, name=None, **req_kwargs` |
| `Strategy` | `initial_portfolio=None, triggers=None, cash_accrual=None` | extras `*, name='strategy', eval_mode='staged'` |
| `ConstantTransactionModel` | `cost=0` | same |
| `ScaledTransactionModel` | `scaling_type='notional_amount', scaling_level=0.0001` | same |
| `AggregateTransactionModel` | `transaction_models=(), aggregate_type=TransactionAggType.SUM` | same |
| `ConstantCashAccrualModel` | `rate=0, annual=True` | `rate=0.0` |
| `DataCashAccrualModel` | `data_source=None, annual=True` | same |
| `GenericEngine.__init__` | `(action_impl_map=None, price_measure=Price)` | `+ *, market=None, session=None, **session_kw`; a non-empty `action_impl_map` raises `NotSupportedError` |
| `GenericEngine.run_backtest` | `(strategy, start=None, end=None, frequency='1m', states=None, risks=None, show_progress=True, csa_term=None, visible_to_gs=False, initial_value=0, result_ccy=None, holiday_calendar=None, market_data_location=None, is_batch=True, calc_risk_at_trade_exits=False, pnl_explain=None)` | **identical signature**; `result_ccy` other than the session currency -> `NotSupportedError`; `calc_risk_at_trade_exits=True` and `pnl_explain` -> `NotSupportedError`; `csa_term`/`market_data_location` are forwarded as market request keys; `visible_to_gs`/`is_batch` are ignored |

### 3.3 `Backtest` result object: v1 vs gs

| gs `BackTest` member (gs-1.5.4) | v1 `Backtest` |
|---|---|
| `__init__(strategy, states, risks, price_measure, holiday_calendar=None, pnl_explain_def=None)` | `__init__(strategy, states, risks, price_measure, result, holiday_calendar=None, pnl_explain_def=None)` (**extra positional `result` before `holiday_calendar`**) |
| `CUMULATIVE_CASH_COLUMN, TRANSACTION_COSTS_COLUMN, TOTAL_COLUMN` | same (`'Cumulative Cash'`, `'Transaction Costs'`, `'Total'`) |
| `result_summary` | present: `Price` (live-position MTM) \| one column per extra risk (labelled by the `RiskMeasure` object) \| `Cumulative Cash` (= `initial_capital + cash`) \| `Transaction Costs` (cumulative, <= 0) \| `Total` (= `equity`). Invariant: Total == Price + Cumulative Cash + Transaction Costs on every row. Indexed by `dt.date` when the run has at most one point per local date (`daily` property), else by tz-aware Timestamp. |
| `risk_summary` | present (result_summary minus the three cash columns) |
| `trade_ledger()` | present: columns `Open, Close, Open Value, Close Value, Long Short, Status, Trade PnL`; index = gs trade name; formulas in `backtest_objects.py:307-335` (Open Value = `-entry_quantity * entry_pv`; Close Value = `pnl_gross - open_value` when closed, else 0; Trade PnL = `pnl_gross` when closed, else NaN; Long Short = +1/-1 by the sign of the quantity) |
| `summary_stats(annualisation_factor=252)` (gs-repo only) | present: the gs-repo labels and formulas on `result_summary['Total']` (lines 337-406): Start Date, End Date, Duration (days), Total PnL, Total Transaction Costs, Total Trades, Peak PnL, Annualised Return, Annualised Volatility, Sharpe Ratio, Sortino Ratio, Max Drawdown, Max Drawdown Duration (days), Calmar Ratio, Current Drawdown, Average Daily PnL, Daily PnL Std Dev, Best Day, Worst Day, % Positive Days, Skewness, Kurtosis |
| `pnl_explain()` | returns `None` |
| `strategy_as_time_series`, `portfolio_dict`, `cash_dict`, `cash_payments`, `hedges`, `transaction_costs`, `transaction_cost_entries`, `results`, `calculations`, `calc_calls`, `add_results`, `set_results`, `get_risk_summary_df`, `trade_exit_risk_results`, `to_dict`, `from_dict`, `to_json`, `from_json`, `schema`, `holiday_calendar`, `pnl_explain_def` | **absent** (only the last two exist, as attributes). v2 must decide which to add for "1:1" (note `03-gs-results-backtest-objects.md` covers the gs semantics). |
| - | v1 extras: `.result` (the full pricebt `BacktestResult`), `.record`, `.daily`, `ladder(risk)` (a vector risk as a frame, one column per bucket), `trade_name(positions=None)` (`'<Action>_<Instrument>_<date>'`; initial holdings `'<Instrument>_<date>'`; intraday stamps `%Y-%m-%dT%H%M%S`; duplicates get `_<pid>`) |

`ResultSummary(pd.DataFrame)` (lines 176-196): `df[Price]` and `df[DollarPrice]` return the `'Price'` column (via `RiskMeasure.label_in`); `df['Cash']` is derived on request from `Cumulative Cash` (`diff()`, first row = first value) for pre-2021 gs code.

### 3.4 `instrument.py`: salvage vs v1-only

| Item (line) | Behaviour | Verdict for v2 |
|---|---|---|
| `DEFAULT_NOTIONAL = 1e7` (41) | the gs default `notional_amount` | SALVAGE (as the swap asset config's default, not in core) |
| `_REL` regex (43) `^\s*(ATMF\|ATM\|PAR)\s*(?:([+-])\s*(\d+(?:\.\d+)?)\s*(?:BPS?)?)?\s*$` (re.I) | parses `'ATMF'`, `'ATMF+25'`, `'ATM-10bp'` | SALVAGE into the IRS asset's trade expression helper (it is IRS semantics, so it belongs in the asset module, not in core) |
| `_rate_or_relative` (132-145) | numbers are DECIMALS (0.04 = 4%); `abs(v) >= 1.0` refused; converts to percent for v1 terms | SALVAGE the gs-decimal rule; drop the percent conversion (the asset receives gs kwargs verbatim) |
| `_number` (103-113) | finite real, bools refused, numeric strings accepted | SALVAGE (a generic kwarg validator) |
| `_notional` (167-172) | negative notional flips the direction | SALVAGE into the IRS asset trade expression |
| `InstrumentTemplate.clone(**changes)` (60-62) | gs `Instrument.clone`: rebuilds from `inputs` with changes | SALVAGE the idea: v2 trade = (asset config, kwargs); `clone` = the same asset with `{**kwargs, **changes}` |
| `name=` -> `gs_name` (trade_ledger, `ExitTradeAction(priceable_names=...)`) | gs trade naming | SALVAGE |
| `_check_gs_kwargs` + `SWAP_GS_FIELDS` (148-164) | refuses gs convention fields (`fixed_rate_frequency`, `clearing_house`, ...) unless the Stack accepts the value | DELETE: in v2 these kwargs pass through opaquely to the asset's trade expression, which decides |
| `_date_arg` via `_swap_schema().check_term` (116-129) | date/tenor validation by the shipped schema | DELETE (the asset validates its own kwargs) |
| `_session_spec` / `_accepted_gs_values` (175-186) | spec lookup through the session Stack | DELETE |
| `IRSwaption` (224-226) | raises "out of scope" | v2: later workflow (new asset config); keep the stub until then |
| 16 stubs (`FXForward` ... `Bond`, 233-259) | raise `NotSupportedError` | KEEP as stubs, or replace with a generic `Instrument(asset="<config>", **kwargs)` constructor (design decision) |

### 3.5 `risk.py`: the RiskMeasure-as-`str` trick

| Item (line) | Behaviour | Verdict |
|---|---|---|
| `class RiskMeasure(str)` (42-122) | `__new__(measure, *, name, vector, params, family, supported, doc)`. The str VALUE is the pricebt measure name, so it hashes and compares equal to `'dv01'`, works as a dict key or a DataFrame column label, and can be passed anywhere a measure name is accepted. Attributes: `.name` (gs name), `.measure` (plain str), `.params`, `.aggregation_level`, `.currency`, `.vector`, `.is_price`, `.supported`. `__copy__`/`__deepcopy__` return self; `__reduce__` pickles. | **SALVAGE as is.** This is what lets `HedgeAction(risk=IRDeltaParallel)` and `result_summary[IRDeltaParallel]` work without gs objects. |
| `__call__` (86-98) | `IRDelta(aggregation_level=..., currency=...)` returns a new measure (gs `ParameterisedRiskMeasure`); called with one pandas object (pandas calls a callable key), returns the column label via `label_in` | SALVAGE |
| `label_in(obj)` (100-107) | `'Price'` for a price measure when the frame has a `Price` column but no `pv` | SALVAGE |
| `_FAMILIES` registry + `@_family` (38, 146-151) | name -> factory | SALVAGE |
| Measure map (`RISK_MEASURE_MAP`, 226) | `Price, DollarPrice -> pv`; `IRDelta(aggregation_level in {Type, Asset, Class}) -> dv01`, `IRDeltaParallel -> dv01`; bare `IRDelta`, `IRDelta(aggregation_level=Point)`, `IRDeltaLocalCcy -> delta_ladder` (vector); `IRGamma*, IRGammaParallel -> gamma`; `IRVega*, IRVegaParallel, IRVegaLocalCcy -> vega`; `Theta -> theta`; `FXDelta/FXGamma/FXVega -> fx_delta/fx_gamma/fx_vega` (supported=False); `EqDelta/EqGamma/EqVega -> eq_*` (supported=False) | SALVAGE the gs vocabulary. In v2 the RIGHT-hand names must match the pricing-function keys of the asset config (`npv`, `dv01`, `gamma`, `delta_ladder`, ...). Decide the canonical key names once and document them in the asset-config spec. |
| `_check_params` (135-139) | unknown gs params -> `NotSupportedError`; `name, value, unit, asset_class, measure_type` accepted and ignored | SALVAGE |
| `_vector_names()` (229-233) | a measure is a vector iff its SCHEMA return type starts with `dict` (reads `SchemaRegistry.default()`) | **REWRITE**: no schemas in v2. Vector-ness must come from the asset config (for example a portfolio-level expression that returns `{tenor: value}`) or from the returned value's type at run time. |
| `check_currency(currency, ..., allow_local=True)` in `_ir_delta` etc. | only the reporting currency or `'local'` | **REWRITE** for multi-currency. |
| `DollarPrice` doc "mark-to-market in USD (the only currency)" (186-189) | | REWRITE: in multi-currency, `DollarPrice` = PV converted to USD through the FX expression; `Price` = PV in the reporting currency (`result_ccy`). |

### 3.6 `session.py`: salvage vs v1-only

(Line numbers are inside `session.py`.)

| Item | Behaviour | Verdict |
|---|---|---|
| `DEFAULT_REPORTING_CURRENCY = "USD"` (33) | | KEEP as the default of `result_ccy`/reporting currency |
| `_GS_ONLY` (37-38) | `GsSession.use(...)` kwargs swallowed: `environment_or_domain, client_id, client_secret, scopes, api_version, application, http_adapter, use_mds, domain, token, is_gssso, is_marquee_login, csrf_token, application_version` | KEEP |
| `resolve_calendar(cal)` (73-94) | `None`/`'weekends'`/`'weekends_only'`/`'none'` -> weekends only; a name in `registries.CALENDARS`; a path ending in `.json` (`Calendar.from_json`); a `Calendar`; an iterable of holidays | KEEP |
| `PricebtSession.__init__(market, *, tz='America/New_York', calendar=None, date_policy='close', session_open=08:00, session_close=17:00, settings=None, stack=None, wrap=None, instruments=None, reporting_currency='USD')` (102-129) | | ADAPT: keep `tz, calendar, date_policy, session_open, session_close, settings, reporting_currency`; delete `stack, wrap, instruments`. `market` is no longer required: in v2 each asset config builds its own market. |
| `use()` / `get_current()` / `reset()` / `_activate` / `close()` / `__enter__` / `__exit__` (131-177) | the process-wide default, a context manager, a `_prev` chain that skips sessions closed out of order | KEEP |
| `daily_time` (180-183) | `{open: session_open, close: session_close, midnight: 00:00}[date_policy]`: the time a DATE maps to | KEEP |
| `time_context(holiday_calendar)` (185-188) | `TimeContext` with the run's extra holidays | KEEP |
| `market_data(request)` (190-205), `_wraps` (207-214) | builds `MarketData` from bindings and applies wraps | DELETE (replace with the v2 per-asset market cache) |
| `instrument_spec(asset_class, currency)` (217-244), `accepted_gs` (246-255), `_instrument_keys` (45-61) | the `'<asset_class>:<CCY>'` spec lookup | DELETE; the v2 analogue is "asset config by name", perhaps with a `(asset_class, ccy) -> config` default map (design decision) |
| `class GsSession(PricebtSession)` (260-261), `current_session()` (264-265) | alias | KEEP |

### 3.7 `backtest_objects.py` transaction and accrual models

(Line numbers are inside `backtest_objects.py`.)

| Item | Behaviour | Verdict |
|---|---|---|
| `TransactionAggType` (34-37) `SUM/MAX/MIN` | | KEEP |
| `ConstantTransactionModel(cost=0)` (49-58) | fixed cash per instrument traded -> `costs.ConstantCost(cost_per_leg=cost)` | KEEP |
| `ScaledTransactionModel(scaling_type='notional_amount', scaling_level=0.0001)` (62-77) | `'notional_amount'` -> measure `notional` (`_GS_SIZE_MEASURES`, line 31); a `RiskMeasure` -> `ScaledCost('measure:<m>', level)`; a vector risk is refused | ADAPT: `notional` must become a pricing function (or trade attribute) of the asset config |
| `AggregateTransactionModel` (81-93), `to_cost_model` (96-102) | | KEEP |
| `ConstantCashAccrualModel(rate=0.0, annual=True)` (110-119) | `balance * ((1 + r)**days - 1)`, `r = rate/365` if annual, `days = accrual.wall_days(t0, t1)` (calendar days, like gs `(date1 - date0).days`) | KEEP |
| `DataCashAccrualModel(data_source, annual=True)` (123-136) | rate read at the START of each interval | KEEP; multi-currency: one accrual per cash currency? (design decision) |
| `OisFixingCashAccrualModel` (139-147) | stub | KEEP (stub) |
| `PnlAttribute`, `PnlDefinition` (152-166) | accepted; `run_backtest(pnl_explain=...)` refuses | KEEP |

### 3.8 `generic_engine.py` helpers worth keeping verbatim

| Helper | Lines | Behaviour |
|---|---|---|
| `_as_state(x, ctx)` | 43-50 | a date -> that date's `daily_time`; a naive midnight datetime/Timestamp is treated as a DATE; any other datetime is localised to the session tz |
| `GenericEngine._grid` | 121-135 | `states` override start/end; else `parse_tenor(frequency)`; unit in `min/h/s` -> `TimeGrid.intraday`, else `TimeGrid.daily`; empty -> `ConfigError(code="GRID")`. **Missing vs gs: gs unions `trigger.get_trigger_times()` inside `[start, end]` into the grid (gs `generic_engine.py:783-789`); in v1 the engine does this separately (engine note).** |
| `GenericEngine._risks` | 137-150 | requested risks first, then `strategy.risks`; deduplicated by measure name, first-seen order; the price measure is the `Price` column; returns `(scalar names, vector names, labels)` |
| `GenericEngine._settings` | 152-162 | merges `session.settings` into `EngineSettings`; unknown keys -> `ConfigError` |

---

## 4. `config/yamlio.py`: reusable for v2 asset configs? YES (KEEP)

Dependencies: `yaml`, `json`, `os`, `re`, `copy`, `pathlib` and `..errors.ConfigError`. Nothing else.

| Function (line) | Behaviour | Use in v2 |
|---|---|---|
| `_Loader` (29-42) | `SafeLoader` with YAML-1.2 core scalars: `1e7` is a float (stock PyYAML reads it as a string); only `true/True/TRUE/false/False/FALSE` are bools (`yes/no/on/off` stay strings); ints without leading zeros; underscores allowed in numbers; `.inf/.nan` | good for `notional: 1e7` in asset configs |
| `_construct_mapping` (62-70) | **a duplicate key is an error** with `file:line:col` | important: a duplicated pricing-function name in an asset config must fail |
| `loads(text, *, name, fmt)` (79-89) | YAML or JSON (auto-detected when the text starts with `{`/`[` and parses as JSON) | as is |
| `interpolate(obj, env)` (111-131) | replaces `${VAR}` / `${VAR:-default}` in every string; a missing variable without default is an error | **CAUTION**: it runs on EVERY string, so a Python expression containing `${` would be rewritten. Rare in Python, but v2 should either not call `interpolate` on the expression fields or document that `$${` is not an escape (there is no escape). Recommended: apply `interpolate` only to non-expression fields (for example `source` strings), or not at all. |
| `deep_merge(base, over)` (134-141) | mappings merge recursively; lists and scalars are replaced | usable for an `extends:` between asset configs (for example `usd_sofr_ois_irs.yaml extends _irs_base.yaml`) |
| `load_file(path)` (144-159) | resolves top-level `extends: <path or list>` (relative to the file; base first; cycle detection); top level must be a mapping | as is; only the TOP-level `extends` key is special |
| `set_path` / `apply_overrides(cfg, sets)` (162-188) | `--set a.b.0.c=value` (value parsed as YAML) | optional |

Caveats the implementer must know:
1. The SafeLoader **timestamp resolver is still active** (only bool/int/float resolvers are replaced), so an unquoted `2024-01-02` becomes a `datetime.date` and `2024-01-02T10:00:00` a `datetime`. Tenors like `10Y` stay strings. Quote dates in expression strings (they are inside quoted strings anyway).
2. Error type: `ConfigError(message, path=..., code="CFG-YAML" | "CFG-ENV" | "CFG-EXTENDS" | "CFG-FILE" | "CFG-SET")`.
3. Test to keep: `tests/test_config_yamlio.py` (64 lines, 7 tests: YAML-1.2 scalar literals, a control showing stock PyYAML gets them wrong, JSON autodetect, duplicate-key position, env interpolation).
4. Suggested v2 location: keep `pricebt/config/yamlio.py`, or move it to `pricebt/yamlio.py` when `config/` is otherwise empty.

---

## 5. Where the v1 facade is tied to the v1 contracts (must change, not port)

| v1 coupling | Where | What v2 does instead |
|---|---|---|
| Instruments are `TradeTemplate(spec, terms)` built by `spec.factory(pricer, ts, *, terms, conventions)` | `contracts/spec.py:297-384`, `instrument.py:48-73` | a trade = (asset config, opaque gs kwargs); `build` evaluates the asset's `trade` expression with the market at `ts` |
| Measures are evaluated through bindings (`evaluate_measure(spec, name, env)`) | `contracts/evaluate.py:99-133`, `engine/engine.py:17` | evaluate the asset config's named pricing expression on `(market, trade)` |
| Vector-ness from the schema return type | `risk.py:229-233` | from the asset config (portfolio-level expressions) or the returned type |
| One reporting currency | `common.py:119-136`, `session.py:33`, `generic_engine.py:106-107`, `risk.py:186-189` | each asset has a `currency`; convert to `result_ccy` through the FX expression |
| Session Stack supplies specs and a `wrap` | `session.py:102-129, 190-255` | asset configs self-contained; no stack |
| `ScaledTransactionModel('notional_amount')` -> schema measure `notional` | `backtest_objects.py:31, 62-77` | a pricing function or trade attribute named in the asset config |
| `ExitTradeAction(priceable_names=...)` matches `spec.gs_name`, `m.template`, `m.action`, `extra['name']` | `backtests/actions.py:104-112` | match on the v2 trade's gs `name` and asset name |
| `HedgeAction.vector` taken from `RiskMeasure.vector` | `backtests/actions.py:99-100` | same, once vector-ness is re-sourced |
| `GenericDataSource` reads `current_session().tz/daily_time` | `backtests/data_sources.py:21-32` | KEEP (session survives) |

---

## 6. gs vocabulary worth carrying into v2 (reference list)

| Vocabulary | v1 location | Use |
|---|---|---|
| gs `IRSwap` field names (`pay_or_receive, termination_date, notional_currency, notional_amount, effective_date, fixed_rate, name` + the 24 convention fields in `SWAP_GS_FIELDS`) | `instrument.py:190-221`, `backtests/gs_fields.py:6-12` | the kwarg names the USD SOFR IRS asset config's `trade` expression should accept, so that `IRSwap('Pay', '10y', 'USD', fixed_rate='ATMF')`-style calls port 1:1 |
| gs result column names | `backtest_objects.py:169-173` | `Price`, `Cumulative Cash`, `Transaction Costs`, `Total`, ledger columns |
| gs trade name format | `backtest_objects.py:292-305` | `'<Action>_<Instrument>_<date>'` |
| gs enums | `common.py:38-116` | `PayReceive` (with `Payer/Receiver/Rec/Straddle`), `BuySell`, `AggregationLevel`, `OptionType`, `OptionStyle`, `BacktestTradingQuantityType` |
| `_GS_ONLY` session kwargs | `session.py:37-38` | `GsSession.use(...)` compatibility |

---

## 7. Time utilities v2 must keep (gs-compatible date grids)

### 7.1 What exists (`src/pricebt/timeutil.py`, 367 lines; all library-free, pandas + `dateutil.relativedelta`)

| Symbol | Lines | Behaviour | Verdict |
|---|---|---|---|
| `DEFAULT_TZ = "America/New_York"` | 16 | | KEEP |
| `SECONDS_PER_YEAR = 31_557_600.0` | 20 | Julian year used by statistics only | KEEP (results use it) |
| `Calendar(holidays=(), weekmask=(1,1,1,1,1,0,0), name="")` | 23-104 | `is_business_day`, `following`, `preceding`, `modified_following`, `adjust(d, convention in none/unadjusted/following/f/preceding/p/modified_following/mf)`, `add_business_days(d, n)` (a non-business start rolls preceding for n>0, following for n<0), `business_days(start, end)`, `extended(extra)`, `from_json(path)` (keys `holidays`, `weekmask`, `name`), `weekends_only()` | KEEP |
| `_parse_weekmask`, `weekmask_names` | 110-132 | `(1,1,1,1,1,0,0)` \| `'1111100'` \| `'Mon Tue Wed Thu Fri'` | KEEP |
| `to_date` / `_to_date` | 135-147 | Timestamp/datetime/date/ISO str -> date | KEEP |
| `parse_tenor(tenor)` | 153-164 | `'3m' -> (3,'m')`; units `b,d,w,m,y` (calendar), `min,h,s` (intraday); aliases `bd,bday->b`, `hr->h`, `mo->m`; `m` is ALWAYS months | KEEP |
| `apply_tenor(d, tenor, cal)` | 167-182 | "gs RelativeDate rules": `b` = `cal.add_business_days`; `d` = calendar days, no roll; `w`, `m` = then `cal.following`; `y` = then `cal.preceding` | **FIX** (section 7.2) |
| `schedule(start, end, freq, cal)` | 185-202 | `[start]` + `apply_tenor(start, k*N)` for k=1,2,... while `<= end` (multiples FROM start, not iterated; start not adjusted; keeps a date only if `> out[-1]`) | KEEP (it is gs `RelativeDateSchedule.apply_rule`, gs `relative_date.py:255-267`) |
| `TimeContext(tz, calendar, date_policy='close', session_open=08:00, session_close=17:00)` | 205-241 | `localize`, `at(d, time=None)` (a date -> Timestamp per date policy), `normalise`, `in_session` | KEEP |
| `TimeGrid(points, ctx)` | 244-316 | sorted unique tz-aware points; `daily(start, end, freq='1b', ctx, business_only=True)`, `intraday(start, end, freq='1min', ctx, include_close=True)` (DST gap/ambiguous -> dropped), `from_config`, `union`, `days`, `periods_per_year()` | KEEP; **FIX `daily` default** (section 7.2); delete `from_config` (config-loader only) |
| `Clock` | 324-336 | strictly monotone `advance`; the look-ahead guard compares against `now` | KEEP |
| `TimelineContext(start, end, time, grid_days)` | 339-367 | what triggers/actions see | KEEP |

Related (strategy note owns them, listed for completeness): `strategy/durations.py` `trade_duration` grammar (`resolve_exit`, lines 146-154: None \| tenor chain `'1m+1b'` \| `'@close'`/`'@10:30'` \| fixed `'15min'` \| date \| Timedelta \| `'next schedule'` \| a resolved term name such as `'maturity'` \| `CustomDuration`); `roll_tenor` (81-88: a `d` part additionally rolls forward); `periodic_schedule` (166-185); `_Scheduled` / `PeriodicTriggerRequirements` (`strategy/requirements.py:123-170`). gs equivalent: `backtest_utils.get_final_date(inst, create_date, duration, holiday_calendar, trigger_info)`: None -> `date.max`; date -> itself; `hasattr(inst, duration)` -> `getattr(inst, duration)`; `'next schedule'` -> `trigger_info.next_schedule`; `CustomDuration` -> `function(*durations)`; else `RelativeDate(duration, create_date).apply_rule(holiday_calendar=...)`. In v2 the "resolved term name" branch must read the attribute from the v2 trade (the asset's trade expression output), which matches gs's `getattr(inst, duration)`.

### 7.2 Verified deviations from gs RelativeDate (fix in v2)

gs source: `gs_quant/datetime/rules.py` (gs-1.5.4). `_apply_business_days_logic` (lines 90-98) = `np.busday_offset(result, offset, roll, holidays, weekmask)`. Probes were run with `RelativeDate(rule, base).apply_rule(holiday_calendar=[...])` against `pricebt.timeutil.apply_tenor(base, rule, Calendar(holidays))`.

| Rule | gs rule (rules.py) | v1 (`timeutil.py:167-182`) | Probe (base -> gs / v1) | Match? |
|---|---|---|---|---|
| `Nb` | `bRule` 126-130: `busday_offset(offset=N, roll='forward' if N <= 0 else 'preceding')` | `add_business_days`: n>0 rolls preceding first, n<0 following; **n=0 returns d unchanged** | `1b` 2024-03-02 (Sat) -> 2024-03-04 / 2024-03-04; `-1b` 2024-03-02 -> 2024-03-01 / 2024-03-01; **`0b` 2024-03-02 -> 2024-03-04 / 2024-03-02** | **NO for 0b** |
| `Nd` | `dRule` 133-135: `+ relativedelta(days=N)`, no roll | `d + timedelta(days=n)` | `3d` 2024-03-01 -> 2024-03-04 / 2024-03-04 | yes |
| `Nw` | `wRule` 252-257: `+ relativedelta(weeks=N)`, then roll `'forward' if N >= 0 else 'backward'` | always `cal.following` | `1w` 2024-03-04 -> 03-11 / 03-11; `-1w` 2024-03-04 -> 02-26 / 02-26; **`-1w` 2024-03-09 (Sat) -> 2024-03-01 / 2024-03-04** | **NO for negative weeks landing on a non-business day** |
| `Nm` | `mRule` 185-188: `+ relativedelta(months=N)`, roll `'forward'` | `cal.following` | `1m` 2024-01-31 -> 2024-02-29 / 2024-02-29; `-2m` 2024-03-30 -> 2024-01-30 / 2024-01-30 | yes |
| `Ny` | `yRule` 273-279: `+ relativedelta(years=N)`; **then `while weekmask[day] == '0': += 1 day` (weekend days roll FORWARD)**; then `busday_offset(0, 'backward')` (holidays roll BACKWARD) | always `cal.preceding` | **`1y` 2024-03-02 -> 2025-03-03 (Mon) / 2025-02-28 (Fri)**; **`-1y` 2025-03-02 -> 2024-03-04 / 2024-03-01**; `1y` 2024-03-04 with holiday 2025-03-04 -> 2025-03-03 / 2025-03-03 | **NO when landing on a weekend** |

Exact v2 rule set (implement in `apply_tenor`):
- `b`: if `n == 0`: return `following(d)` when `d` is not a business day, else `d`. Otherwise unchanged (`add_business_days`).
- `d`: unchanged.
- `w`: `x = d + weeks(n)`; return `following(x)` if `n >= 0` else `preceding(x)`.
- `m`: unchanged (`following`).
- `y`: `x = d + relativedelta(years=n)`; `while weekmask[x.weekday()] == 0: x += 1 day`; return `preceding(x)` (this rolls back over holidays only; if the preceding walk crosses a weekend it keeps going back, exactly like `busday_offset(..., 'backward')`).
- Add these 6 probes as regression tests in `tests/test_core_time.py` (the existing `test_tenor_rules_match_gs_relativedate_probes`, lines 22-29, covers only `2d` and `2m`). Obtain the expected values from gs itself, as in the table.

Grid deviations:
- `TimeGrid.daily(..., business_only=True)` drops `start` when it is not a business day. gs `RelativeDateSchedule.apply_rule` puts `base_date` in the schedule **unadjusted** (`relative_date.py:256`), and `GenericEngine.run_backtest` uses that list as is (gs `generic_engine.py:772-778`). v2 facade: call `TimeGrid.daily(..., business_only=False)` from `GenericEngine._grid` (or change the default), and add a test with a Saturday `start`.
- `business_only=True` filters EVERY non-business date, not only `start`. For `b/w/m/y` that matters only for `start` (the rule already lands on a business day), but gs `dRule` never rolls, so gs `RelativeDateSchedule('1d', start, end)` yields CALENDAR days, weekends included, while v1 `frequency='1d'` yields business days. v2 must decide and test which one it wants; `tests/test_strategy_triggers.py::test_periodic_defaults_to_backtest_window_and_1d_is_every_business_day_once` pins the v1 choice for `PeriodicTrigger`.
- gs `RelativeDateSchedule` uses `rule[:-1]` / `rule[-1]`, so it only supports single-letter units; v1's intraday `'15min'/'1h'` frequencies are a pricebt extension (keep them, and document them as such).
- gs unions `trigger.get_trigger_times()` inside `[start, end]` into the pricing dates (`generic_engine.py:783-789`); check that the v2 engine does the same (engine note).

---

## 8. Top-level inventory

| Path | Files / lines | Purpose | Verdict | Reasoning |
|---|---|---|---|---|
| `arbs_adapter/` | 6 files, 782 lines (`__init__` 28, `_compat` 63, `provider` 139, `risk_model` 91, `swap` 371, `wrap` 90) | the v1 adapter to ARBS (a `Kit` + `wrap` + provider over `IRSwapsMDP`) | DELETE (code) + READ (facts) | The v2 USD SOFR asset config calls ARBS directly. The *facts* this adapter established are listed in section 8.1 for the asset-config author. Never import or execute it. |
| `configs/` | 30 files, 1,257 lines: `adapters/*.yaml` (8 stack overlays), `suite/*.yaml` (16 suite configs + 4 `_base` files), `suite/calendars/usd_fed.json`, `synthetic_swap_carry.yaml` | v1 backtest configs | DELETE, except **KEEP `configs/suite/calendars/usd_fed.json`** (6,730 bytes; `{name, doc, weekmask: "Mon Tue Wed Thu Fri", first: 2000-01-01, last: 2035-12-31, holidays: [...]}`, readable by `Calendar.from_json`), which is useful as the USD calendar of the grid. Move it to e.g. `calendars/usd_fed.json`. | The configs use the deleted loader, schemas, stacks and refstack. v2 adds `configs/assets/usd_sofr_ois_irs.yaml` (outside `src/`, so the core guards stay meaningful). |
| `skills/` | 122 files, 13,397 lines: 14 skills + `README.md` + two worked examples (`example/` acmelib, `example-service/` zeta) | teach an AI agent to wire a library via bindings/Kits/wrap | DELETE | Entirely about the stripped machinery (user directive). |
| `tools/` | 37 files, 4,666 lines | scripts | see section 10 | |
| `docs/` | 19 files, 3,065 lines | v1 design/spec/ADRs/guides/research | see 8.2 | |
| `notebooks/` | `showcase_swap_book.ipynb` + `src/showcase_swap_book.py` (3,728 lines together) | rateslib-based ladder-hedge showcase | DELETE | Built on `pricebt.contrib.rateslib` and `tests/support/curves.py`. v2 replaces it with a port of gs's `040304_strategy_mean_reversion.ipynb` on the ARBS USD SOFR asset config (keep the percent-format source + `tools/nb_build.py` workflow). |
| `tasks/` | `baseline_test_ids.txt`, `suite_reproduction.md`, `tolerance_ledger.yaml` (1,043 lines) | v1 refactor floor / tie-out ledger | DELETE | Tied to the v1 test ids and the tie-out. |
| `tests/` | 185 files (123 `.py`, 62 other), 29,405 lines | | see section 11 | |
| `data/` | `.gitignore` only (`*` / `!.gitignore`); the fixtures (39 MB, 2,232 files) live only in the primary checkout `pricebt/data/fixtures` | fixtures for the snapshot providers | DELETE the fixture concept; KEEP the ignored `data/` folder if v2 wants a local cache location | v2's market comes from the asset config (ARBS). |
| `AGENTS.md` | 10 lines | points agents to `skills/` | ADAPT | Rewrite: point to the v2 design doc and the asset-config spec; keep the "run things" and "no weakened tests" lines. |
| `CONSTRAINTS.md` | 58 lines | the v1 refactor quality bar | ADAPT | Keep the floor rules (no skipped/weakened tests without a log entry; no new `# noqa`/`# type: ignore`; no stubs standing in for behaviour; ARBS and gs-quant read-only; checks run first on a known answer). Replace the Z3/tie-out/golden-number/notebook rows. |
| `NOTICE` | 10 lines | Apache-2.0 + gs attribution + rateslib licence note | ADAPT | Keep the gs attribution (v2 ports even more of gs); delete the rateslib paragraph. |
| `README.md` | 62 lines | v1 overview | ADAPT (rewrite) | Everything in it describes bindings, stacks, tie-out and contrib. |
| `pyproject.toml` | 27 lines | | ADAPT | Section 12. |
| `pytest.ini` | 13 lines | `pythonpath = . src tests`, markers `core, adapter_rateslib, adapter_quantlib, fixtures, live_arbs`, rateslib warning filters | ADAPT | Markers -> `core` (no ARBS, no network) and `live_arbs` (opt-in, needs `PRICEBT_LIVE_ARBS=1`); drop the two rateslib `filterwarnings`. Keep `addopts = -q -p no:cacheprovider`. |
| `.gitignore` | 10 lines | | **FIX** | `results/` -> `/results/`, `results_new/` -> `/results_new/` (section 1). |

### 8.1 Facts `arbs_adapter/` established (for the ARBS asset-config author; do not run it)

| Fact | Source |
|---|---|
| ARBS is a local checkout, not an installed package: `sys.path.insert(0, os.environ.get("PRICEBT_ARBS_ROOT", r"C:\Users\chris\clee\ARBS"))`, and ARBS is imported lazily inside functions | `arbs_adapter/_compat.py` (`arbs_root`, `ensure_arbs_on_path`) |
| `os.environ.setdefault("ARBS_SUPABASE_ENABLED", "0")` **before** any ARBS import (ARBS's own opt-out of its production database; the flag is read once at import) | `_compat.py` `irswaps_mdp()`; `docs/research/mdp-feasibility.md` section 5 item 1 |
| Imports: `from MDP.IRSwaps.IRSwapsMDP import IRSwapsMDP`; `from Query.IRSwaps.backends.rateslib.RLIRSwapCurve import RLIRSwapCurve`; `from Query.IRSwaps.backends.rateslib.rl_curve_definitions_map import RATESLIB_CURVE_DEFINITIONS` | `_compat.py` |
| Request shape: `IRSwapsMDP(source="ERIS_EOD_LIVE-RL_BASIC").get_pricer(request={"curve_name": "USD-SOFR-1D", "timestamp": <date>})` (v1 used source `ERIS_EOD_LIVE-RL_BASIC`; the user's v2 example uses `ERIS_EOD_LIVE-RL_BASIC-NOJUMPS`) | `provider.py` docstring |
| Trade build: `pricer.curve_handle.build_irswap(fwd=None, effective_date=..., tenor=..., fixed_rate=..., notional=...)`; **`fixed_rate=-0` is ARBS's "strike at par" sentinel** (a literal 0.0 collides with it) | `swap.py:137`, `swap.py:328-333`, `risk_model.py:60` |
| `build_irswap(fwd=...)` measures the forward start from the REFERENCE date; `effective_date=` passes straight through (v1 used `effective_date` for "tenor forward from spot") | `swap.py:293-295` |
| `curve_handle.fair_rate(swap)` returns a **DECIMAL** | `swap.py:172`, `risk_model.py:61` |
| `curve_handle.npv(swap)`, `curve_handle.pv01(swap)`; **`pv01` keeps the accrued coupon on a STARTED swap** (overstates once started; v1 used the ladder sum instead) | `swap.py:151, 179-181` |
| Notional sign: a PAYER carries a POSITIVE notional in ARBS and in pricebt (the same holder sign) | `swap.py:19` |
| Other ARBS read-outs: `carry_bps_running(swap, horizon)`, `roll_bps_running(...)`, `carry_and_roll_bps_running(...)`, `theta_components(...)`; `RLIRSwapCurve.dollar_carry` raises `NotImplementedError` on this backend | `swap.py:237-268` |
| `RLIRSwapCurve.npv()`/`.pv01()` always close over `self._rl_curve_handle` and cannot be pointed at a scenario curve | `swap.py:9-15` |
| ARBS risk-model ladder: par rates of the pillar swaps read with `build_irswap`/`fair_rate`, calibrated risk curve wrapped in `RLIRSwapCurve`, rateslib AD `delta` | `risk_model.py:1-61` (mirrors `ARBS/notebooks/pricers/irswap_pricer_and_risk_model.ipynb`) |
| **Side-effect warning**: the unpatched ARBS MDP layer can (a) open the production Postgres pooler, (b) launch Excel via `ShellExecuteW` (CitiVelo fixings path), (c) write into the ARBS repo / user caches (`_fetch_fixings` mkdirs today's cache dir and may pull NY Fed/FRED over HTTP), (d) return stale data silently. v1 needed five patches to run offline. A v2 market expression that calls `IRSwapsMDP(...)` directly inherits all of this; pricebt's default test suite must therefore never evaluate the ARBS asset config (opt-in `live_arbs` only) | `docs/research/mdp-feasibility.md` sections 1 and 5 |

### 8.2 `docs/`

| Path | Lines | Verdict | Reasoning |
|---|---|---|---|
| `docs/DESIGN.md` | 161 | DELETE | v1 as-built architecture (bindings, snapshots, stacks). |
| `docs/design/11-refactor-spec.md` | 528 | DELETE | v1 contract (Z/S/B/T/X requirement ids). |
| `docs/design/11-refactor-changelog.md` | 601 | DELETE | v1 history. |
| `docs/design/11-quantlib-conventions.md` | 420 | DELETE | QuantLib adapter evidence. |
| `docs/design/adr/001..010 + README` | 17-46 each, 320 total | DELETE | All about the stripped machinery; `010-dead-ends.md` (15 lines) is worth one read before deleting. |
| `docs/guides/backtesting.md` | 633 | ADAPT (seed) | Sections 3-9 (triggers, actions, GenericEngine parameters, results) are a gs-style user guide; rewrite the market/instrument parts (sections 0-2) for asset configs. |
| `docs/guides/gs-quant-deviations.md` | 79 (44 numbered rows) | ADAPT (seed) | The verified list of where v1 differs from gs (evaluation order date-major, exits before adds, trade-duration grammar, as-of signals, one calendar per run, ...). v2 should start its own deviations list from it: keep rows 1-37 that still hold after review; rows 38-44 (instruments, swap rates, session, engines, risk measures, time axis, import map) must be rewritten. |
| `docs/research/mdp-feasibility.md` | 160 | KEEP as reference (move to `docs/v2/research/` or leave) | The ARBS data-path safety research (section 8.1). |
| `docs/research/rateslib.md` | 163 | DELETE | rateslib adapter research. |

---

## 9. The seam: exactly what engine/strategy/orders/view import from stripped modules

`grep` of `src/pricebt/{engine,strategy,orders.py,costs.py,view.py,registries.py}`:

| Importer | Imported name(s) | From | v2 replacement |
|---|---|---|---|
| `engine/engine.py:16` | `Env` | contracts.binding | the v2 evaluation context (market, trade, ts) |
| `engine/engine.py:17` | `evaluate_layer, evaluate_measure, evaluate_value, scalar_of, tenor_sort_key, vector_add, vector_of, vector_scale` | contracts.evaluate | `evaluate_*` -> call the asset's named expressions; the vector helpers move to a kept module (section 2.2) |
| `engine/engine.py:18` | `TradeTemplate, layer_ref` | contracts.spec | the v2 trade class; `layer_ref` goes (layers are stripped unless the engine note keeps them) |
| `engine/engine.py:22` | `MarketData` | market | the v2 market cache (guard + memo, section 2.1) |
| `engine/engine.py:24` | `MarkContext, Valuation, as_valuation` | pricable | keep `Valuation`; `MarkContext` becomes the context passed to asset expressions |
| `engine/engine.py:25`, `engine/state.py:13`, `view.py:10` | `Pricer` | pricer | type alias (`Any`) |
| `engine/state.py:9`, `orders.py:9`, `strategy/actions.py:17` | `TradeTemplate` | contracts.spec | the v2 trade class |
| `engine/state.py:12` | `Valuation` | pricable | keep |
| `strategy/actions.py:15` | `is_vector, vector_scale` | contracts.evaluate | vector-ness from the asset config; `vector_scale` kept |
| `strategy/actions.py:16` | `is_term_ref` | contracts.schema | goes with term references (strategy note) |
| `strategy/signals.py:18` | `TENOR` | contracts.binding | move `re.compile(r"\d+[DWMY]")` to the kept ladder module |
| `strategy/strategy.py:122` | `MarketData` (lazy) | market | the v2 market cache |
| `registries.py:23` | `testing.toys` (`MDPS.register("toy", ToyMDP)`) | testing | delete the line (or register the v2 toy) |

---

## 10. `tools/` (37 files, 4,666 lines)

| Path | Lines | Purpose | Verdict | Reasoning |
|---|---|---|---|---|
| `tools/mutcheck.py` | 89 | apply one textual mutation to a file, run the named tests, expect FAIL, restore (`python tools/mutcheck.py spec.json`; keeps the caller's PYTHONPATH; accepts `"runs"`) | **KEEP** | Generic; implements the user's standing rule "mutate the code, confirm the test fails". Its test `tests/test_mutcheck_env.py` stays. |
| `tools/nb_build.py` | 99 | build and execute notebooks from percent-format sources (`nbformat`, `nbclient`; imports no pricebt) | **KEEP** | For the v2 mean-reversion notebook. |
| `tools/arbs_live.py` | 537 | opt-in read-only ARBS parity oracle (child process) | DELETE | v1 bridge; v2 calls ARBS from the asset config. |
| `tools/export_fixtures.py` | 663 | export test fixtures from the ARBS stores (read-only) | DELETE | Fixtures for snapshot providers. |
| `tools/floor_check.py` | 75 | every baseline test id still collected or explained in the change log | DELETE | Tied to `tasks/baseline_test_ids.txt` and the v1 change log. |
| `tools/make_golden_swap.py` | 32 | freeze the golden swap world with rateslib | DELETE | |
| `tools/probe_unexplained_ratio.py` | 35 | QuantLib vs reference `unexplained` ratio | DELETE | |
| `tools/run_suite_bonds.py` | 682 | rerun the UST suite on fixtures | DELETE | |
| `tools/run_suite_swaps.py` | 720 | rerun the swap suite on fixtures | DELETE | |
| `tools/swap_suite_support.py` | 140 | stack overlays for the swap suite | DELETE | |
| `tools/repro/compare.py`, `tools/repro/repro_table.py` | 133, 56 | compare new vs recorded run directories | DELETE | |
| `tools/ql_evidence/*.py` (25 files: `common.py`, `p1_bond.py`, `p1_bond2.py`, `p1_bond_sched_all.py`, `p1_dates.py`, `p1_dv01.py`, `p1_evalguard.py`, `p1_explicit.py`, `p1_horizon.py`, `p1_ladder.py`, `p1_ladder_control.py`, `p1_ladder_eom.py`, `p1_ladder_feb29.py`, `p1_layers.py`, `p1_misc.py`, `p1_npv.py`, `p1_paydate.py`, `p1_pillarsets.py`, `p1_sched.py`, `p1_started.py`, `p1_timing.py`, `qlbond.py`, `qlladder.py`, `qlswap.py`, `rlladder.py`) | 1,405 total | QuantLib convention probes | DELETE | |

---

## 11. `tests/` (185 files = 123 `.py` + 62 non-py; 29,405 lines)

Columns: lines / number of `def test_` / verdict. "needs results" = cannot import until section 1 is fixed. "toy -> asset" = replace `toy_tpl`/`TradeTemplate`/Kit usage by the v2 toy asset config.

### 11.1 Shared test infrastructure

| File | Lines | Tests | Purpose | Verdict |
|---|---|---|---|---|
| `conftest.py` | 42 | 0 | imports the partition hook, `Engine`, `MarketData`, `ScriptedStrategy`, `ToyMDP`, `TimeGrid`; fixtures `tctx`, `grid`, `mk`; `assert_identity` | ADAPT: new market cache + toy asset; keep `assert_identity` (equity == initial + cash + tcost + positions_value) |
| `toy_helpers.py` | 22 | 0 | `toy_tpl(cls, **kw)` builds a toy TradeTemplate | ADAPT (toy -> asset) |
| `test_strategy_support.py` | 80 | 0 | `FakeView`, `mk_ctx`, `fwd`, constants | ADAPT (toy -> asset) |
| `test_results_common.py` | 97 | 0 | `synth_result` builders | ADAPT (needs results; toy -> asset) |
| `spec_helpers.py` | 147 | 0 | mismatched-names swap double for contracts tests | DELETE |
| `suite_common.py` | 50 | 0 | real-data suite helpers | DELETE |
| `tieout_helpers.py` | 61 | 0 | tie-out doubles | DELETE |
| `test_rl_common.py` | 150 | 0 | rateslib world builder | DELETE |

### 11.2 Zero-dependence guards (`tests/guards/`)

| File | Lines | Tests | Purpose | Verdict |
|---|---|---|---|---|
| `guards/__init__.py` | 0 | 0 | | KEEP |
| `guards/blocker.py` | 53 | 0 | `sys.meta_path` finder that raises `ModuleNotFoundError` for banned import roots; `python tests/guards/blocker.py -m core` runs pytest with it installed | **KEEP**: the direct enforcement of "no external pricing library in core" |
| `guards/import_all.py` | 38 | 0 | child process: import every core module with the blocker installed | KEEP (update the module list; "core" = all of `src/pricebt`, since `contrib` is gone) |
| `guards/scan.py` | 306 | 0 | static scans Z1 (imports), Z2 (tokens in identifiers/literals), Z4 (convention tokens), Z5 (ARBS provenance), schema scans | ADAPT: keep Z1/Z2/Z4/Z5 over `src/pricebt`; delete the schema/Z3 scans |
| `guards/banned.yaml` | - | - | banned import roots (`rateslib, QuantLib, gs_quant, MDP, Query, Caching, TB, BT, RVUtils, SDRUtils, Simulation, definitions`), executable tokens, `schema_arg_names`, convention tokens | ADAPT: keep `import_roots`, `executable_tokens`, `convention_tokens`; delete `schema_arg_names` |
| `guards/partition.py` | 42 | 0 | collection hook: every test must carry a marker | ADAPT (marker set `core`, `live_arbs`) |
| `guards/test_guards.py` | 122 | 12 | GT-Z1..Z5 | ADAPT (drop Z3 cases) |
| `guards/test_partition.py` | 61 | 6 | the hook on known answers | ADAPT |
| `guards/test_twins.py` | 244 | 22 | non-vacuity twins (one planted violation each) | ADAPT (drop Z3 twins) |
| `guards/test_semantics.py` | 179 | 10 | Z3 bindings shape (unbound name rejected, mismatched-names toy) | DELETE |
| `guards/zero_toys.py` | 90 | 0 | Z3 toys | DELETE |

### 11.3 Contracts, config, registry: DELETE except yamlio

| File | Lines | Tests | Verdict |
|---|---|---|---|
| `test_binding.py` | 398 | 42 | DELETE |
| `test_evaluate.py` | 244 | 27 | DELETE, but **move the vector-maths tests** (tenor ordering, `check_ladder`, `vector_add`/`vector_scale`) along with the helpers |
| `test_schema_v1.py` | 275 | 34 | DELETE |
| `test_schema_review.py` | 214 | 20 | DELETE |
| `test_spec.py` | 318 | 31 | DELETE |
| `test_spec_review.py` | 271 | 21 | DELETE |
| `test_conventions_vocab.py` | 95 | 9 | DELETE |
| `test_registry_allow.py` | 66 | 7 | DELETE (registry.py goes) |
| `test_config_loader.py` | 169 | 12 | DELETE |
| `test_config_review.py` | 232 | 23 | DELETE |
| `test_stacks.py` | 89 | 7 | DELETE |
| `test_config_yamlio.py` | 64 | 7 | **KEEP** |

### 11.4 Core time, market, snapshot

| File | Lines | Tests | Verdict |
|---|---|---|---|
| `test_core_time.py` | 75 | 9 | **KEEP + extend** with the section 7.2 probes and a Saturday-start grid |
| `test_core_pricable.py` | 91 | 7 | ADAPT: keep `test_as_valuation_normalises` and `test_marketdata_guard_memo_and_lookahead` (re-pointed at the v2 market cache); delete `test_snapshot_index_modes`, the provider-snapshot test and the toy-option greek test if toys change |
| `test_market_wrap.py` | 98 | 6 | DELETE (wrap) |
| `test_snapshot.py` | 224 | 20 | DELETE |
| `test_synthetic_market.py` | 264 | 18 | DELETE |

### 11.5 Engine (engine note owns the details)

| File | Lines | Tests | Verdict |
|---|---|---|---|
| `test_engine.py` | 321 | 28 | ADAPT (toy -> asset) |
| `test_engine_audit.py` | 235 | 17 | ADAPT: keep the manifest parts; delete the audit trail / conventions digests / snapshot digests (tie-out inputs); needs results |
| `test_engine_baseline.py` | 189 | 14 | ADAPT or DELETE with the Taylor baseline decomposition (engine note); needs results |
| `test_engine_cycle3.py` | 367 | 16 | ADAPT (imports `pricebt.tieout`; drop those cases); needs results |
| `test_engine_observation.py` | 274 | 14 | ADAPT; needs results |
| `test_progress_notebook.py` | 172 | 12 | KEEP (toy -> asset) |
| `test_regressions.py` | 160 | 11 | ADAPT: drop the cases that go through `config.loader`; keep the engine/strategy ones |
| `test_ladder_sizing.py` | 55 | 5 | KEEP (plain dicts, `pricebt.strategy`) |
| `test_ladder_hedge_dust.py` | 79 | 9 | KEEP (`strategy.sizing` only) |

### 11.6 Strategy (strategy note owns the details)

| File | Lines | Tests | Verdict |
|---|---|---|---|
| `test_strategy_actions.py` | 426 | 29 | ADAPT (imports `contracts.spec.Built, TradeTemplate`) |
| `test_strategy_durations.py` | 141 | 16 | KEEP |
| `test_strategy_engine.py` | 522 | 39 | ADAPT (toy -> asset) |
| `test_strategy_infos.py` | 77 | 9 | KEEP |
| `test_strategy_runner.py` | 184 | 12 | KEEP (check `registries` usage) |
| `test_strategy_signals.py` | 392 | 29 | ADAPT (`ToyMDP`, `ToyPricer`) |
| `test_strategy_sizing.py` | 86 | 9 | KEEP |
| `test_strategy_triggers.py` | 494 | 40 | KEEP |

### 11.7 Results (needs the restored package)

| File | Lines | Tests | Verdict |
|---|---|---|---|
| `test_results_attribution.py` | 162 | 13 | ADAPT (layers) |
| `test_results_compare_report.py` | 124 | 9 | ADAPT (imports `synthetic`) |
| `test_results_io.py` | 97 | 7 | KEEP |
| `test_results_reconcile.py` | 160 | 9 | ADAPT (toy -> asset) |
| `test_results_result.py` | 187 | 17 | ADAPT (toy -> asset) |
| `test_results_stats.py` | 226 | 22 | KEEP |
| `test_results_tearsheet.py` | 120 | 9 | KEEP |

### 11.8 gs facade

| File | Lines | Tests | Purpose | Verdict |
|---|---|---|---|---|
| `test_gs_compat_guide.py` | 636 | 30 | the gs backtesting guide (`gs_quant/skills/gs-quant-overview/backtesting.md` sections 3-9) ported snippet by snippet | **ADAPT: this is the v1 north-star suite**; re-point the instruments at the v2 toy asset |
| `test_gs_compat_api.py` | 326 | 17 | import map, risk-measure map, enums, sessions, cost-model conversion, stubs | ADAPT (drops `contrib.rateslib`, `instrument` spec parts; add multi-currency cases) |
| `test_gs_compat_accrual.py` | 69 | 5 | calendar-day accrual like gs | KEEP |
| `test_facade_terms.py` | 269 | 17 | `IRSwap` -> terms on a registered spec | ADAPT (becomes "gs kwargs pass through to the asset's trade expression") |
| `test_facade_review.py` | 304 | 24 | argument checks before the session, spec lookup, refstack | ADAPT (keep the argument-validation cases; drop spec/stack/refstack) |
| `test_gs_compat_rates.py` | 279 | 11 | facade on the rateslib adapter over the synthetic market | DELETE; replace by an opt-in `live_arbs` test on the ARBS USD SOFR asset config |

### 11.9 Adapters, reference stack, tie-out, ARBS, skills, providers, suites: DELETE

| Group | Files (lines / tests) |
|---|---|
| rateslib adapter | `test_rl_bond.py` (313/20), `test_rl_compat.py` (137/12), `test_rl_engine.py` (255/10), `test_rl_pricer.py` (145/10), `test_rl_swap.py` (513/38), `test_ladder.py` (378/21), `test_snapshot_wrap_rateslib.py` (458/28) |
| QuantLib adapter | `test_quantlib_bond.py` (371/23), `test_quantlib_conventions.py` (106/10), `test_quantlib_globals.py` (173/10), `test_quantlib_steps.py` (68/1), `test_quantlib_swap.py` (518/39), `test_quantlib_tieout.py` (442/18), `test_quantlib_wrap.py` (192/15), `quantlib_support/__init__.py` (1/0), `quantlib_support/rl_reference.py` (118/0), `quantlib_support/snapshots.py` (157/0) |
| layers | `test_layer_conformance.py` (191/21), `test_layer_conformance_adapters.py` (61/3) |
| reference stack | `test_refstack.py` (398/27), `test_refstack_bond.py` (240/13), `test_refstack_edges.py` (137/12), `test_refstack_golden.py` (156/9), `test_refstack_precise.py` (23/1), `test_refstack_solver.py` (54/6), `data_golden_swap.json` |
| tie-out | `test_tieout.py` (238/21), `test_tieout_cycle3.py` (543/40), `test_tieout_review.py` (303/26), `test_tieout_selftest.py` (96/5) |
| ARBS | `test_arbs_adapter_swap.py` (212/11), `test_arbs_adapter_wrap.py` (142/6), `test_arbs_bridge.py` (797/18), `test_adapters_no_arbs.py` (20/1). **`test_no_arbs_dependency.py` (68/5): ADAPT, not delete**: "no ARBS import or token under `src/`" is still a v2 requirement (it overlaps guard Z5; merge it there) |
| skills | `test_skills_example_acme.py` (740/46), `test_skills_example_zeta_facts.py` (188/15), `test_skills_example_zeta_no_infra.py` (45/3), `test_skills_example_zeta_proofs.py` (310/21), `test_skills_example_zeta_swap.py` (276/20), `test_skills_example_zeta_wrap.py` (242/19), `test_skills_library.py` (336/20) |
| fixture providers (`tests/support`) | `support/__init__.py` (14), `support/common.py` (385), `support/curves.py` (298), `support/known.py` (74), `support/tiny.py` (99), `support/ust_common.py` (176), `support/ust_eod.py` (143), `support/ust_minute.py` (171); `test_support_common.py` (204/17), `test_support_curves.py` (448/29), `test_support_guard.py` (94/6), `test_support_selector.py` (230/22), `test_support_ust.py` (711/52) |
| real-data suites | `test_suite_bonds.py` (477/23), `test_suite_swaps.py` (535/24) |
| tools | `test_floor_check.py` (72/6) DELETE; **`test_mutcheck_env.py` (91/6) KEEP** |

### 11.10 Non-py test data (62 files)

| Files | Verdict |
|---|---|
| `tests/data_golden_swap.json` | DELETE (refstack golden world) |
| `tests/guards/banned.yaml` | ADAPT (section 11.2) |
| `tests/support/mutcheck/*.json` (60 specs): targets `tests/support/{common,curves,ust_common,ust_eod,ust_minute}.py`, `testing/synthetic.py`, `contrib/rateslib/wrap.py` (7 top-level); `core/` 27; `facade/` 4 (`instrument.py`, `contracts/schema.py`, `session.py`, `contracts/spec.py`); `quantlib/` 13; `rateslib/` 9 | DELETE all except these 5, which target surviving files: **`core/mut_dust.json` -> `strategy/sizing.py`, `core/mut_engine_obs.json` -> `engine/engine.py`, `core/mut_attr.json` -> `results/attribution.py`, `core/mut_guards_blocker.json` -> `tests/guards/blocker.py`, `core/mut_guards_scan2.json` -> `tests/guards/scan.py`**: ADAPT (re-check that each `find` string still occurs after the refactor). `facade/mut_session.json` and `facade/mut_instrument.json` target surviving files, but their mutations are in the stripped spec code: rewrite them. |

Counts (`.py` files). KEEP or ADAPT: 46 = shared 4 (`conftest`, `toy_helpers`, `test_strategy_support`, `test_results_common`) + guards 8 + yamlio 1 + time/market 2 + engine 9 + strategy 8 + results 7 + facade 5 + `test_mutcheck_env` 1 + `test_no_arbs_dependency` 1. DELETE: 77 = shared 4 + guards 2 + contracts/config 11 + core 3 + facade 1 + rateslib 7 + QuantLib 10 + layers 2 + refstack 6 + tie-out 4 + ARBS 4 + skills 7 + support 13 + suites 2 + `test_floor_check` 1. 46 + 77 = 123. The engine, strategy and results notes may move individual files between KEEP and ADAPT, but should not delete any of those 46.

---

## 12. `pyproject.toml`: v1 and v2

v1 (27 lines):

```toml
[project]
name = "pricebt"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["numpy>=1.26", "pandas>=2.1", "pyyaml>=6", "tqdm>=4.66"]
[project.optional-dependencies]
parquet = ["pyarrow>=14"]
plot = ["matplotlib>=3.8"]
rateslib = ["rateslib>=2.7"]
dev = ["pytest>=8"]
[project.scripts]
pricebt = "pricebt.config.cli:main"
[tool.setuptools.package-data]
pricebt = ["contracts/schemas/*.yaml"]
```

| Item | v2 action | Reason |
|---|---|---|
| `dependencies` | keep `numpy>=1.26, pandas>=2.1, pyyaml>=6, tqdm>=4.66`; **add `python-dateutil>=2.8`** | `timeutil.py:12` imports `dateutil.relativedelta` directly (today only a transitive dependency of pandas) |
| `rateslib` extra | DELETE | no pricing library in pricebt |
| `parquet` (`pyarrow`), `plot` (`matplotlib`) | KEEP | used by `results/io.py` and `results/tearsheet.py` |
| `dev` | KEEP `pytest>=8`; ADD `nbformat`, `nbclient` (and `ipykernel`) for `tools/nb_build.py` in a `notebook` extra | |
| `[project.scripts] pricebt = "pricebt.config.cli:main"` | DELETE | the CLI goes |
| package-data `contracts/schemas/*.yaml` | DELETE | schemas go. **Do not ship the ARBS asset config inside the package**: keep asset configs in a top-level `configs/assets/`, outside `src/`, so the core guards (Z1/Z2/Z5 scans of `src/pricebt`) stay meaningful |
| `version` | `2.0.0` | |
| `description` | rewrite | |

---

## 13. Suggested strip order for the implementer (each step keeps `pytest -m core` green)

1. Section 1: fix `.gitignore`, restore `src/pricebt/results/`, commit.
2. Delete what nothing kept imports: `contrib/`, `tieout/`, `testing/{refstack,synthetic,layer_conformance}.py`, `config/{loader,stacks,cli}.py`, `api.py`, `__main__.py`, `snapshot.py`, `registry.py` (after removing its imports from `session.py` and `strategy/`), `arbs_adapter/`, `skills/`, `configs/` (keep `usd_fed.json`), `tasks/`, the DELETE rows of `tools/`, `docs/`, `notebooks/`, and the DELETE test files (section 11).
3. Move the vector helpers and `TENOR` out of `contracts/` (section 2.2), then build the v2 trade + asset evaluation that replaces the seam (section 9), then delete `contracts/`.
4. Replace the Kit/bind plumbing of `testing/toys.py` with a toy asset config; delete `registries.py:23`.
5. Adapt the facade (sections 3 and 5), the guards (11.2), `pytest.ini`, `pyproject.toml`.
6. Fix `timeutil` (section 7.2) with gs-derived regression tests.
