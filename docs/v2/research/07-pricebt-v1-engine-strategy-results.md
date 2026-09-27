# 07 - pricebt v1 engine, strategy and results: inventory and v2 verdicts

**Key facts**

1. **`src/pricebt/results/` is missing from the v2 worktree and branch.** The `.gitignore` line `results/` (line 9) also matches `src/pricebt/results/`, so the baseline commit `c05d515` left out 11 files (1,743 LOC). `pricebt.backtests.generic_engine`, `config.loader` and 16 test files import it, so they fail on this branch. Fix it before anything else: change the pattern to `/results/` and `/results_new/`, then copy `C:/Users/chris/clee/gsquant-temp-claude/pricebt/src/pricebt/results/` into the worktree.
2. About 5,500 of the ~7,560 LOC reviewed here are reusable. The rest is layers, baseline, audit, registries, template references and the pricing machinery. It is the event loop and ledger (`engine/engine.py` and `engine/state.py`), the progress bar (`engine/progress.py`), the strategy layer (`strategy/*`), `timeutil.py`, `orders.py`, `costs.py`, `accrual.py`, and the pure maths in `results/stats.py`, `results/ledger.py` and `results/reconcile.py`. These modules reach the v1 pricing machinery through only ~10 symbols: `contracts.spec.TradeTemplate`, `contracts.binding.Env` and `TENOR`, `contracts.evaluate.*`, `contracts.schema.is_term_ref` and `SchemaRegistry`, `pricable.MarkContext`, `pricer.Pricer` and `market.MarketData`. Section 5 lists every cut point.
3. v1 was written against **gs-quant 2.1.17**, the source checkout at `C:/Users/chris/clee/gsquant-temp-claude/gs-quant`. That is also where the requested mean-reversion notebook lives. The installed reference, `site-packages/gs_quant`, is **1.5.4**, and its API is smaller: it has no `AddWeightedTradeAction` and no `EarlyExitPositionLimitScaledAction`, and `EventTriggerRequirements` takes `(event_name, offset_days, data_source)`. The v2 spec must name one target version (open question Q1).
4. v1 already documents 44 deliberate gs deviations, each pinned by tests, in `docs/guides/gs-quant-deviations.md`. The biggest are these. v1 evaluates date by date; gs evaluates trigger by trigger. v1 runs actions in phases EXIT < ADD < ADJUST < HEDGE, and an exit only closes positions booked at an earlier point. v1 fixed or guarded several gs bugs: mean-reversion state, `PortfolioTrigger('len')`, the fill-forward look-ahead and a silent zero hedge. v1 accrues cash at every point rather than only between payment dates. Section 7 gives a v2 verdict for each deviation.
5. The v1-only extensions are ladder (vector) hedge, `RiskBandTrigger`/`RebalanceTrigger`, `CrossingTrigger`, `CustomTrigger`/`CustomAction`, the signals (`PricerSignal`, `MeasureSignal`, `BookMeasureSignal`, `BookLadderSignal`, `DerivedSignal`, `StepTable`), `fill_lag`/`fill_price`, `eval_mode`, attribution layers, the Taylor baseline, audit/tie-out, `measures`/`vector_measures` recording, `PositionSpec`, `mode="resize"` and `target`/`scope`. Section 8 classifies each one: **drop**, **keep as a labelled extension**, or **needed by v2**. Multi-currency needs new engine work. v1 has a single cash scalar and `common.check_currency` raises for any currency other than the reporting one. gs keeps `cash_dict[date][ccy]` and requires `result_ccy` when there is more than one currency (section 9).

---

## 0. Scope, sources and method

| Item | Location |
|---|---|
| v1 code (read-only) | `C:/Users/chris/clee/gsquant-temp-claude/pricebt-v2/src/pricebt/` (worktree, branch `v2-redesign`, HEAD `c05d515`) |
| v1 `results/` package (read from the primary checkout, because it is missing in v2) | `C:/Users/chris/clee/gsquant-temp-claude/pricebt/src/pricebt/results/` (byte-identical otherwise: `diff -rq pricebt/src pricebt-v2/src` reports only `Only in pricebt/src/pricebt: results`) |
| gs reference (installed) | `C:/Users/chris/anaconda3/Lib/site-packages/gs_quant/backtests/`, `gs_quant.__version__ == '1.5.4'` (introspected with `inspect.signature` / `dataclasses.fields`; no session and no network) |
| gs source checkout | `C:/Users/chris/clee/gsquant-temp-claude/gs-quant/` (git HEAD `f2a505a Chore: Make release 2.1.17`) |
| v1 deviation register | `pricebt-v2/docs/guides/gs-quant-deviations.md` (79 lines, 44 rows with test ids) |
| Tests | `pricebt-v2/tests/` |

LOC figures are `wc -l`, so they include docstrings and blank lines.

**Verdict vocabulary.** `KEEP` means copy the file unchanged apart from import paths. `ADAPT` means keep the logic and replace the named dependencies. `DELETE` means do not carry the file into v2.

---

## 1. Module inventory (summary table)

| Module | LOC | Purpose | Imports from pricebt (the ones marked **bold** are stripped packages) | Verdict |
|---|---|---|---|---|
| `engine/__init__.py` | 2 | re-exports `Engine, EngineSettings, RunRecord, Position` | `.engine`, `.state` | KEEP |
| `engine/engine.py` | 817 | event loop, order execution, marking, cash ledger, layers, baseline, audit, recording, `_View` | **`contracts.binding.Env`**, **`contracts.evaluate`** (`evaluate_layer, evaluate_measure, evaluate_value, scalar_of, tenor_sort_key, vector_add, vector_of, vector_scale`), **`contracts.spec`** (`TradeTemplate, layer_ref`), `costs`, `types.BASELINE_LAYERS`, `errors`, **`market.MarketData`**, `orders`, **`pricable`** (`MarkContext, Valuation, as_valuation`), **`pricer.Pricer`**, `timeutil`, `view.Scope`, `.progress`, `.state` | ADAPT (heavy) |
| `engine/state.py` | 146 | `Position`, `EngineSettings`, `RunRecord`, `POSITION_COLUMNS`, `term_columns` | **`contracts.spec.TradeTemplate`**, `costs`, `orders.PositionMeta`, **`pricable.Valuation`**, **`pricer.Pricer`** | ADAPT |
| `engine/progress.py` | 127 | single tqdm bar that works in notebooks, plus `quiet_tqdm()` to silence other bars | none (tqdm only) | KEEP |
| `strategy/__init__.py` | 58 | re-exports; registers triggers, actions and signals into the config registries | `registries` | ADAPT (delete the registry block, lines 4 and 23-58) |
| `strategy/strategy.py` | 271 | `Strategy`, `StrategyRun` (the per-point staged evaluator), `PositionSpec`, `StepReport` | `errors`, `orders`, `timeutil`, `types`, `.actions`, `.infos`, `.requirements`, `.signals`, `.triggers`; lazily `engine`, **`market`** in `backtest()` | ADAPT |
| `strategy/triggers.py` | 163 | `Trigger` base, 15 generated subclasses, `OrdersGeneratorTrigger`, `times_of` | `errors`, `orders`, `timeutil`, `types`, `.actions`, `.infos`, `.requirements`, `.signals` | KEEP (optionally drop some subclasses) |
| `strategy/requirements.py` | 926 | all trigger logic (the gs `*TriggerRequirements`) | `errors`, `orders`, `registry.resolve_dotted`, `timeutil`, `.durations`, `.infos`, `.signals` | ADAPT (light) |
| `strategy/actions.py` | 752 | actions producing `Instruction`s | **`contracts.evaluate`** (`is_vector, vector_scale`), **`contracts.schema.is_term_ref`**, **`contracts.spec.TradeTemplate`**, `costs.CostContext`, `errors`, `orders`, `registry.resolve_dotted`, `timeutil`, `types.Phase`, `.durations`, `.signals`, `.sizing` | ADAPT |
| `strategy/infos.py` | 221 | `TriggerInfo`, `ActionInfo` subclasses, key matching and merging | `errors` | KEEP |
| `strategy/durations.py` | 185 | `trade_duration` grammar, `CustomDuration`, `resolve_exit`, `periodic_schedule` | `errors`, `timeutil`, `.infos` | KEEP (`terms` input becomes trade attributes, section 3.8) |
| `strategy/sizing.py` | 153 | pure sizing maths: risk, NAV, weighted, hedge, rebalance, ladder least squares | `errors` | KEEP |
| `strategy/signals.py` | 614 | signal specs, `RingBuffer`, `SignalStore` | **`contracts.binding.TENOR`**, `errors`, `timeutil.DEFAULT_TZ` | ADAPT |
| `results/__init__.py` | 18 | re-exports | submodules | ADAPT |
| `results/result.py` | 212 | `BacktestResult` (read-only view over a `RunRecord`) | `engine.state.RunRecord`, `.attribution`, `.ledger`, `.stats`, `.errors`, `.reconcile` | ADAPT (becomes the engine side of the gs `BackTest` object) |
| `results/ledger.py` | 104 | positions table, closed trades, gs-style trade ledger, trade summary, layer pivot | `.stats.profit_factor` | KEEP (drop `layer_frame`) |
| `results/stats.py` | 312 | path statistics (`StatsConfig`, `compute_stats`, `GS_LABELS`) | `timeutil.SECONDS_PER_YEAR`, `.errors` | KEEP |
| `results/attribution.py` | 170 | P&L attribution by layer, component, day, baseline, tag and so on | `types.BASELINE_LAYERS`, `.errors`, `.ledger` | ADAPT or DELETE (Q6) |
| `results/reconcile.py` | 179 | accounting identity checks | `types.BASELINE_LAYERS`, `.errors` | ADAPT (drop the `layers`/`baseline_layers` checks; the identity becomes per currency) |
| `results/compare.py` | 98 | compare several results | `.errors`, `.stats` | KEEP as an extension (optional) |
| `results/report.py` | 159 | Markdown report bundle | `.compare`, `.errors`, `.stats`, `.tearsheet` | DELETE (Q7) |
| `results/tearsheet.py` | 310 | matplotlib tearsheet (lazy import) | `errors.OptionalDependencyError`, `.errors` | DELETE or keep as an extension (Q7) |
| `results/io.py` | 151 | parquet persistence of a result | `engine.state` (`EngineSettings, RunRecord`), `errors`, `.errors`, `.result` | ADAPT (optional; Q7) |
| `results/errors.py` | 30 | `ResultError`, `StatsError`, `ReconcileError`, `ResultIntegrityError`, `ResultWarning` | `errors.PricebtError` | KEEP |
| `view.py` | 69 | `EngineView` and `PositionView` protocols, `Scope` | `orders`, **`pricer.Pricer`**, `timeutil.Calendar` | ADAPT |
| `orders.py` | 87 | `PositionMeta`, `PositionSelector`, `OpenOrder`, `CloseOrder`, `ResizeOrder`, `Instruction` | **`contracts.spec.TradeTemplate`** (type only), `costs.CostModel` | ADAPT (retype `OpenOrder.template`) |
| `costs.py` | 84 | `CostContext`, `CostModel`, `ConstantCost`, `ScaledCost`, `AggregateCost`; re-exports accrual | `accrual`, `errors.ConfigError` | KEEP |
| `accrual.py` | 99 | `CashAccrualModel`, `ConstantCashAccrual`, `SeriesCashAccrual`, `CallableCashAccrual`, `wall_days` | `errors.ConfigError` | KEEP (plus a per-currency wrapper; section 9) |
| `timeutil.py` | 367 | `Calendar`, tenor grammar (gs RelativeDate rules), `TimeContext`, `TimeGrid`, `Clock`, `TimelineContext` | `errors` | KEEP |
| `types.py` | 31 | `Phase`, `as_list`, `BASELINE_LAYERS`, `RESERVED_LAYERS` | none | ADAPT (delete the two layer constants if layers are dropped) |
| `errors.py` | 87 | exception hierarchy | none | ADAPT (delete binding and snapshot errors, add asset-config errors) |
| `common.py` | 144 | gs enums (`PayReceive`, `BuySell`, `AggregationLevel`, `OptionType`, `OptionStyle`, `BacktestTradingQuantityType`, `Currency`) plus `check_currency` | `errors`; lazily `session`, `risk` | ADAPT (`check_currency` must allow multiple currencies) |
| `registry.py` | 90 | name registry and allow-listed `resolve_dotted('pkg.mod:Attr')` | `errors.RegistryError` | DELETE (Q5), or keep only `resolve_dotted` |
| `registries.py` | 25 | global `TRIGGERS/ACTIONS/SIGNALS/COSTS/CASH_ACCRUAL/MDPS/CALENDARS` | `registry`, `costs`, **`testing.toys`** (imports **contracts**) | DELETE |
| `pricable.py` | 54 | `Valuation`, `as_valuation`, `MarkContext` | **`pricer.Pricer`** | ADAPT (keep `Valuation` and `as_valuation`, delete `MarkContext`) |
| `risk.py` | 245 | gs `RiskMeasure` names mapped to pricebt measure names (a `str` subclass) | `common`, **`contracts.evaluate.is_vector`**, **`contracts.schema.SchemaRegistry`**, `errors` | ADAPT |

Not in scope but on the dependency path: `testing/scripted.py` (41 LOC, `ScriptedStrategy`) should be kept because the engine tests use it. `testing/toys.py` (319) must be rewritten because it imports `contracts`, and every engine test depends on it. `market.py` (126), `pricer.py` (155), `backtests/*` (the v1 gs facade, 913) and `session.py` are covered by other research notes.

---

## 2. What "minimal reusable core" means for v2

These pieces carry over. Each should be reimplemented by copying the v1 code named, not rewritten from scratch.

| Core piece | v1 source | Notes for v2 |
|---|---|---|
| **Event loop** | `engine/engine.py:103-178` (`Engine.run`, `_add_point`, `_step`) | Keep the step order in section 3.1 unchanged. The per-asset market-expression evaluation replaces `self.market.clock.advance` and `evict_before`. |
| **Position and ledger state** | `engine/state.py:29-106` (`Position`), `engine/engine.py:289-356` (`_entry_cost`, `_book_open`, `_close`, `_resize`), `380-408` (`_mark`), `587-608` (`_record`) | Keep the cash sign conventions exactly (section 3.1). |
| **Error policy** | `engine/engine.py:180-187` (`_guarded`, `on_error` raise/skip/record) | Keep. |
| **Timeline and exit scheduling** | `engine/engine.py:125-142`, `EngineSettings.exit_policy` | Keep `own_time` (the default). `next_grid` is optional. |
| **Read-only view** | `engine/engine.py:669-817` (`_View`), `view.py` | Adapt the measure and market plumbing (section 3.3). |
| **Triggers and actions** | `strategy/*` | Section 6 maps them to gs. |
| **Signal store** | `strategy/signals.py:50-104` (`RingBuffer`, `zscore_of`), `153-243` (`SeriesSource`), `472-614` (`SignalStore`) | `SeriesSource` is the implementation behind gs `GenericDataSource`. |
| **Results and statistics** | `results/stats.py`, `results/ledger.py`, `results/reconcile.py`, `results/result.py` | Wrap them in the gs-shaped `BackTest` result (section 6.7). |
| **Progress bar** | `engine/progress.py` | Keep unchanged. |
| **Time** | `timeutil.py` | Keep unchanged. |
| **Costs and accrual** | `costs.py`, `accrual.py` | Keep; the gs-named models sit on top. |

---

## 3. Per-module detail

### 3.1 `engine/engine.py` (817 LOC), verdict ADAPT

**Per-point step order** (`_step`, lines 145-178). Keep it exactly:

| # | Step | Code |
|---|---|---|
| 1 | `market.clock.advance(t)`; `(_prev_t, _t) = (_t, t)` | 147-148 |
| 1b | if there is a previous point: `market.evict_before(prev)`, then cash interest `intr = accrual.interest(cash_account, prev_t, t)` added to `cash_account` and `interest_cum`. The model comes from `settings.cash_accrual`, falling back to `strategy.cash_accrual`. Interest is skipped when the balance is 0. | 149-156 |
| 2 | scheduled exits: every open position with `final_ts <= t` is closed with reason `"scheduled"` | 158-159 |
| 3 | queued fills (`fill_lag >= 1`) | 161-162 |
| 4a | `signals.observe(t, peek_view)`, which only observes and never books a mark | 165-166 |
| 4b | `run.step(t, view, submit)`: the strategy submits orders | 167 |
| 5 | execute pending orders; `last = (i == len(points)-1)` | 169-172 |
| 6 | mark every open position, then `_maybe_layers` | 174-176 |
| 7 | `_record(t)` appends one equity row | 178 |

**Accounting rules** (exact; v2 must reproduce them):

| Event | Cash account | Position fields |
|---|---|---|
| open (`_book_open`, 294-313) | `cash += -q * entry_pv`; `tcost_account -= c` where `c = cost_entry.cost(CostContext(t, q, unit_measure))` | `trade_cash += -q*entry_pv`, `tcost += c`, id `P{seq:06d}`, `booked_point = i`. If `final_ts` is set it calls `_add_point(final_ts)`. |
| mark (`_mark`, 380-408) | `cash += q * (v.cash + v.financing)` | `flow_cash += q*v.cash`, `fin_cash += q*v.financing`, `interval_pnl += q*((v.pv - last_pv) + v.cash + v.financing)`, `last_pv = v.pv`. Idempotent per `(pos, t)`. |
| close (`_close`, 315-334) | `cash += q * px`; `tcost_account -= c` using `cost_model` if given, else `pos.cost_exit` | `quantity = 0`, `status = "closed"`, `exit_ts/exit_pv/exit_reason` set, removed from `_open` |
| resize (`_resize`, 336-356) | `cash += -dq * px`; cost uses `cost_model`, else `pos.cost_entry` | `quantity += dq`. If \|q\| < 1e-12 it closes with reason `"resize_to_zero"`. |
| record (`_record`, 587-608) | `equity = initial_capital + cash_account + tcost_account + sum(q * last_pv)` | columns below |

- `Position.pnl = q*last_pv + trade_cash + flow_cash + fin_cash - tcost` (`state.py:77-79`).
- **Exit visibility rule:** `_select` (line 222-223) returns only positions with `booked_point < self._i`, so an exit never closes a position booked at the same point.
- **Equity row columns** (lines 592-606): `ts, equity, cash, tcost, positions_value, n_positions, step_pnl, interest_cum, financing_cum, flows_cum`, then `layer_<name>`, `measure_<name>` and `signal_<name>`.
- **RunRecord frames:**
  - `trades`: `ts, position, kind(open|close|resize), quantity, pv, cash, tcost, action, template, reason, instrument, term_*`
  - `orders`: `ts, type(open|close|resize), action, template, quantity, final_ts`
  - `errors`: `ts, where, position, error`
  - `events`: `ts, kind, detail`
  - `layers_by_position`: `position, layer, pnl`
  - `vectors`: name to frame (index `ts`, bucket columns sorted by `tenor_sort_key`)
  - `audit`: `inputs`, `marks`, `layers`

**Functions that call stripped machinery** (each needs a v2 replacement):

| v1 function (line) | Stripped call | v2 replacement |
|---|---|---|
| `_bp_per_unit` (52) | `spec.schema.spec_of("rate").unit` | delete (baseline only) |
| `_roles` (263) | `template.roles()`, `market.pricer(t, name)` | evaluate the asset config's `market` expression at `t` (cached per asset and `t`) |
| `_make_position` (266-287) | `TradeTemplate.build(pricer, t)` returns `Built(obj, terms)`; `MarkContext(...)` | evaluate the asset config's trade-construction expression on the opaque kwargs plus the market; no `MarkContext` |
| `_env` (359) | `contracts.binding.Env` | the variable dict injected into asset-config expressions (`market`, `trade`, `pricebt_timestamp`, and so on) |
| `_call_value` (362-367) | `evaluate_value(spec, env)`, `as_valuation` | evaluate the asset config's `npv` function (and optional cash-flow and financing functions), then `as_valuation`. Keep the non-finite check. |
| `_peek_ctx` / `_unit_raw` peek path (369-378, 554-565) | `MarkContext`, `evaluate_measure` | the same peek semantics over the asset-config function evaluator (per `(pos, t)` cache) |
| `_audit_mark`, `_audit_marks_frame` (410-433) | `evaluate_measure` | delete (audit is a tie-out feature) |
| `_layers_for`, `_flush_layers`, `_maybe_layers`, `_cadence_due`, `_book_layer`, `_baseline_*` (436-551) | `spec.bindings`, `spec.layers`, `evaluate_layer`, `evaluate_measure` | delete (Q6) |
| `_unit_measure` / `_unit_vector` (567-571) | `scalar_of`, `vector_of` | move those 4 helpers from `contracts/evaluate.py:57-79` into a small `pricebt/vector.py`, copied verbatim |
| `_finalize` manifest (619-626) | `sp.asset_class`, `sp.conventions_digest`, `sp.conventions`, `sp.factory_path`, `sp.roles()`, `layer_ref` | manifest records the asset-config path and SHA-256, currency and function names |
| `_View.build`, `measure_of`, `_measure_of_raw`, `pricer` (774-796) | `TradeTemplate.build`, `MarkContext`, `Env`, `evaluate_measure`, `market.pricer` | asset-config evaluator |

**Parts that stay as they are:** `_guarded`, `_submit`, `_execute`, `_select`, `_queue`, `_book_queued`, `_book_open`, `_close`, `_resize`, the scheduled-exit loop, `_record` (minus layer columns), `_Standin`, `_order_matches`, `_order_row`, and the `_View` members `now, calendar, start, end, equity, cash, pending_orders, positions, n_positions, measure, measure_vector, _aggregate, signal, signal_window, action_cash, event`.

### 3.2 `engine/state.py` (146 LOC), verdict ADAPT

`Position` fields (`state.py:30-70`), with v2 action:

| Field(s) | v2 |
|---|---|
| `id, quantity, entry_quantity, entry_ts, entry_pv, final_ts, tags, meta, booked_point, cost_entry, cost_exit, status, state, last_ts, last_pv, last_val, trade_cash, flow_cash, fin_cash, tcost, interval_pnl, exit_ts, exit_pv, exit_reason` | keep |
| `pricable: Any` | rename to `trade` (the opaque object returned by the asset config's trade expression) |
| `template: TradeTemplate` | replace with the v2 trade spec (asset name plus the opaque kwargs as given) |
| `terms: Dict` | replace with the trade attributes the asset config exposes (optional). These feed `term_*` ledger columns and `trade_duration` attribute lookup. |
| `entry_pricer, last_pricer: Pricer` | replace with the market objects, or drop (only layers used `prev_pricer`) |
| `baseline_off, baseline_error, baseline_prev, layer_prev_ts, layer_prev_pricer, layer_pnl` | delete (with layers and baseline) |
| new: `currency: str` | the asset's currency (section 9) |

`EngineSettings` fields (`state.py:110-131`), with v2 action:

| Field | Default | v2 |
|---|---|---|
| `name` | `"backtest"` | keep |
| `initial_capital` | `0.0` | keep (gs `initial_value`, in the reporting currency) |
| `fill_lag` | `0` | keep as a labelled extension (default 0 = gs) |
| `on_error` | `"raise"` | keep (`raise`, `skip`, `record`) |
| `layers`, `cadence`, `strict_layers`, `layer_tol`, `baseline`, `audit`, `audit_measures` | | delete |
| `measures` | `()` | keep. This implements gs `run_backtest(risks=...)` scalar risks. |
| `vector_measures` | `()` | keep if bucketed risks such as the delta ladder are supported |
| `record_signals` | `()` | keep as an extension |
| `show_progress` | `True` | keep (gs `show_progress`) |
| `progress_desc` | `"BACKTESTING..."` | keep |
| `cash_accrual` | `None` | keep |
| `quiet_foreign_bars` | `True` | keep |
| `retain_pricers` | `2` | delete (only market.py and snapshot used it) |
| `exit_policy` | `"own_time"` | keep |
| `fill_price` | `"decision"` | keep with `fill_lag` |
| new: `reporting_ccy` | | section 9 |

`RunRecord` (134-148): keep `settings, equity, positions, trades, orders, errors, events, manifest, vectors`. Delete `layers_by_position` and `audit`, or keep them always empty if the results code is to stay untouched.

### 3.3 `view.py` (69 LOC) and `_View`, verdict ADAPT

In gs, `RebalanceAction.method(state, backtest, info)` and trigger `has_triggered(state, backtest)` receive the `BackTest` object. v1 passes the read-only `EngineView` instead (deviation row 22). `EngineView` members:

| Member | v1 meaning | v2 |
|---|---|---|
| `now, calendar, start, end, equity, cash, pending_orders` | as named | keep. With multiple currencies, `cash` becomes the reporting-currency total; add `cash_by_ccy`. |
| `positions(scope)`, `n_positions(scope, include_pending)` | open positions matching a scope (`"portfolio"`, `"all"`, `"tag:x"`, `"action:x"` or a `PositionSelector`) | keep |
| `measure(name, scope, include_pending, missing)` | quantity-weighted sum of a per-unit measure; a vector counts as its bucket sum | keep. The name is an asset-config pricing function name. The sum is in the reporting currency (section 9). |
| `measure_vector(...)`, `measure_of_vector(...)` | vector versions | keep if the delta ladder is kept |
| `measure_of(template, name, quantity)` | per-unit measure of an unbooked template at `now` | keep, over the asset config |
| `build(template, request)` | returns `Built(obj, terms)` | adapt to return the trade object and its attributes |
| `pricer(ts, role, request)` | market pricer | replace with `market(asset_name, ts=None)` |
| `signal(source, ts)`, `signal_window(source, n, inclusive)` | signal store | keep |
| `layer_pnl(layer, scope)` | | delete |
| `action_cash(action)` | `sum(trade_cash - tcost)` over the action's positions (used by NAV sizing) | keep |
| `event(kind, **detail)` | append to events | keep |

Open question Q4: should v2 also expose gs-shaped attributes (`portfolio_dict`, `results`, `cash_dict`, `states`) on the view, so that gs-written `method(state, backtest, info)` functions port unchanged?

### 3.4 `engine/progress.py` (127 LOC), verdict KEEP

`ProgressBar(total, *, desc, show, unit="step", postfix_every=200, mininterval=0.1)` supports `add_total(n)` (the timeline can grow with off-grid exits) and `update(n, **postfix)`. `quiet_tqdm()` monkeypatches `tqdm.std.tqdm.__init__` so that every other tqdm is disabled. In a notebook kernel, `_NotebookSink` draws through `display(..., display_id=True)` / `update`. Tests are in `tests/test_progress_notebook.py` (12 tests). The only dependency is tqdm.

### 3.5 `strategy/strategy.py` (271 LOC), verdict ADAPT

- `Strategy(initial_portfolio=None, triggers=None, cash_accrual=None, *, name="strategy", eval_mode="staged")` (line 111). The first three are positional, matching gs `Strategy(initial_portfolio=None, triggers=None, cash_accrual=None)`. The constructor deep-copies the triggers and initial portfolio, auto-names actions `Action1..n` **per strategy** (gs uses a module-global counter, `actions.py:44`), and rejects duplicate action names.
- `risks` property (137): the union of the measures that triggers and actions require. gs `Strategy.get_risks()` collects only `action.risk` (the HedgeAction risk).
- `StrategyRun.step` (286-329) evaluates the slots in this order:
  1. initial-portfolio orders (`immediate=True`)
  2. stage-0 (non-path-dependent) triggers are evaluated
  3. EXIT-phase actions of the fired stage-0 triggers
  4. ADD-phase actions of the fired stage-0 triggers
  5. for each trigger in list order: a stage-1 trigger (path-dependent) is evaluated now and all its actions run; a stage-0 trigger that fired runs its ADJUST actions
  6. HEDGE actions of the fired stage-0 triggers
  7. one `trigger_fired` event per fired trigger

  `eval_mode="snapshot"` makes every trigger stage 0.
- `_compile_initial` (216-231): a dated dict gives segments `(entry, next_key or None)`. A key before the start raises; a key after the end warns.
- Delete: `Strategy.backtest()` (176-188, a convenience over `MarketData`), `Strategy.periodic()` (191-196) and `engine_settings()`. Adapt `validate()` (150-160), which references `RiskBandTriggerRequirements`.
- `PositionSpec(item, quantity=1.0, name=None, tags=(), transaction_cost=None)` is v1-only. Keep it as an extension, because gs cannot size an initial holding except by instrument notional.

### 3.6 `strategy/triggers.py` (163 LOC), verdict KEEP

- `Trigger(trigger_requirements=None, actions=None, *, name=None, **req_kwargs)`. The flat-kwargs form `PeriodicTrigger(frequency='1m', actions=[...])` is a v1 extension; the gs positional form works as is.
- Subclasses are generated by `_make` (426-444): `PeriodicTrigger, IntradayPeriodicTrigger, DateTrigger, MktTrigger, CrossingTrigger*, StrategyRiskTrigger, PortfolioTrigger, TradeCountTrigger, AggregateTrigger, NotTrigger, MeanReversionTrigger, EventTrigger, CustomTrigger*, RiskBandTrigger*, RebalanceTrigger*` (* = v1-only).
- `OrdersGeneratorTrigger(actions=None, *, name=None)` (447-492). gs uses this class only with `PredefinedAssetEngine`, and its gs signature is `(trigger_requirements, actions)`. Recommend dropping it.
- `times_of(obj, ctx)` calls a user `get_trigger_times()` with or without `ctx`.

### 3.7 `strategy/requirements.py` (926 LOC), verdict ADAPT (light)

The logic is complete and independent of the pricing machinery. The only non-core import is `registry.resolve_dotted`, used by `CustomTriggerRequirements` for `'pkg.mod:fn'` strings. Section 6.1 has the class-by-class gs mapping. Helpers to keep: `check_barrier` (65), `_instants` (78), `_Scheduled` (123), `_freq_seconds` (180), `read_risk` (386; delete the `layer.`/`value.` branch), `_transform` (397), `_count_firings` (503), `_reqs` (539).

### 3.8 `strategy/actions.py` (752 LOC), verdict ADAPT

| v1 symbol | Stripped dependency | v2 change |
|---|---|---|
| `as_leg` (60-67) | `TradeTemplate` | accept the v2 trade spec |
| `resolve_template_refs` (70-104) | `is_term_ref`, `template.has_refs`, `template.ref_terms`, `schema.direction` | **delete.** `@signal.<n>` / `@trigger.scaling` references exist only for YAML config. In Python the user passes values. Replace calls with identity. |
| `_EntryAction._orders` (208-236) | `view.build(tpl, request=pricer_request)` when `needs_terms(duration)` | `view.build(spec)` returns the trade attributes for `resolve_exit(terms=...)` |
| `HedgeAction.__post_init__` (458-477) | `_risk_is_vector` reads `leg.template.spec.schema.measures[risk]` | vector-ness comes from the asset config's declared return kind, or from `RiskMeasure.vector` |
| `HedgeAction._apply_ladder` (516-560) | `vector_scale` | move to `pricebt/vector.py` |
| `CustomAction` (717-741) | `resolve_dotted` | accept callables only (Q5) |

Everything else in the file is independent of the pricing machinery.

### 3.9 `strategy/infos.py` (221 LOC), verdict KEEP

- gs `TriggerInfo(triggered, info_dict=None)` keys `info_dict` by **action class**, and the values are namedtuples: `AddTradeActionInfo(scaling, next_schedule)`, `HedgeActionInfo(next_schedule)`, `ExitTradeActionInfo(not_applicable)`, `RebalanceActionInfo(not_applicable)`, `AddScaledTradeActionInfo(next_schedule)` (gs 1.5.4 `actions.py:174-178`).
- v1 uses frozen dataclasses. The positional fields come first, in the same order as gs, followed by keyword-only `reason`, `data` and `skip`. So `AddTradeActionInfo(-1, None)` builds the same object in both.
- `InfoMap` normalises class keys through `key_of`, so gs-style `{AddTradeAction: AddTradeActionInfo(scaling=-1)}` works unchanged.
- `match_info` precedence is name, then kind, then parent kinds, then `"entry"`, with `"*"` defaults overlaid.
- `TriggerInfo(triggered, info=None, *, info_dict=None, strict=True)`: the second positional argument corresponds to gs `info_dict`.

### 3.10 `strategy/durations.py` (185 LOC), verdict KEEP

The grammar is in the module docstring (lines 3-5): `m` means months, `min` means minutes, `d` rolls forward to a business day, `@time`/`@open`/`@close` set the time of day, and the input can also be `next schedule`, a date, a timedelta, the name of a resolved date term, or a `CustomDuration`.
- `resolve_exit` (146-154): a result not strictly after the entry raises `PastExitError`; a result after the backtest end means "hold" (`None`).
- gs `get_final_date` (backtest_utils.py:63-91) uses `hasattr(inst, duration)`, for example `trade_duration='termination_date'`. In v2 an attribute name should resolve against the trade attributes that the asset config exposes (`needs_terms`, line 157). Open question Q3: exact mechanism (a `terms` expression in the asset config, or `getattr(trade, name)` on the opaque trade object).
- `periodic_schedule` (166-185) implements the gs RelativeDateSchedule rule "multiples k*N from base".

### 3.11 `strategy/sizing.py` (153 LOC), verdict KEEP

Pure functions, copied verbatim:
- `risk_scale(level, unit_risk)` = level / unit_risk. It raises when \|unit\| < 1e-12.
- `nav_scale(available, unit_price, cost_at)`: bisection for `s*price + cost(s) = pot`.
- `weighted_split(total, risks, weighting)`.
- `hedge_quantity(net, unit_risk, target, risk_percentage, min_trade_risk)`: `q = -(net-target)/unit * pct/100`.
- `rebalance_delta`.
- `ladder_hedge_quantities`: weighted least squares plus ridge.
- `ScalingActionType{risk_measure, size, NAV}`: same members and values as gs.

### 3.12 `strategy/signals.py` (614 LOC), verdict ADAPT

| Class | gs equivalent | v2 |
|---|---|---|
| `MissingDataStrategy{fail, fill_forward, interpolate}` | same (`data_sources.MissingDataStrategy`) | keep. `interpolate` raises `NotSupportedError` because it needs the next observation (look-ahead). |
| `SeriesSource(data_set, missing_data_strategy=fail, *, name, tz, date_time=17:00, lag, max_staleness, date_only)` | `GenericDataSource(data_set, missing_data_strategy)` | keep. The gs facade name `GenericDataSource` wraps it. `window_at(ts, n)` = the n values strictly before the newest visible one, which matches gs `get_data_range(state, n)` = `data_set.loc[index < state].tail(n)`. |
| `StepTable` | gs `interpolate_signal` (step; 0 outside the key range) | keep (used by `AddScaledTradeAction(scaling_level=dict)`) |
| `ConstantSource` | none | keep (trivial) |
| `PricerSignal(lookup, kwargs, role, request, scale, ...)` | none | **replace** with an asset-config-driven signal: the value of a named asset-config function or market expression at each point (for example the 10Y par rate from the ERIS USD SOFR curve). This is needed for the requested notebook port if the z-score source is the curve itself rather than a CSV. |
| `MeasureSignal(template, measure, quantity)` | none | keep, adapted to the v2 trade spec (the measure of a freshly built trade at each point) |
| `BookMeasureSignal`, `BookLadderSignal` | none | extension. Drop unless the ladder hedge stays. `BookLadderSignal` imports `contracts.binding.TENOR`; replace it with a local `re.compile(r"\d+[DWMY]")`. |
| `DerivedSignal(op, inputs, **params)` | none | extension (combo, spread, fly, ratio, diff, rolling_*, zscore, clip, lag). Keep: it is cheap and pure. |
| `RingBuffer`, `zscore_of`, `SignalContext`, `SignalStore` | none (gs keeps data in the source) | keep. `zscore_of` is used by `MeanReversionTriggerRequirements`. |

### 3.13 `orders.py` (87 LOC), verdict ADAPT

`PositionMeta(action="", kind="add", trigger_idx=-1, action_idx=-1, template="", tags=(), parent=None, group=None, extra={})`, `PositionSelector(ids, tags, templates, actions, kinds, predicate)` with `.matches(pos)`, `CloseOrder(selector=ALL, reason="action", cost_model=None)` and `ResizeOrder(position_id, delta_quantity, reason, cost_model)` are all kept unchanged. `OpenOrder(template: TradeTemplate, quantity=1.0, final_ts=None, tags=(), meta, cost_entry, cost_exit, immediate=False)` keeps its shape; only `template` is retyped to the v2 trade spec.

### 3.14 `costs.py` (84 LOC) and `accrual.py` (99 LOC), verdict KEEP

- `ConstantCost(cost_per_leg)` returns `fixed = abs(cost_per_leg)`. gs `ConstantTransactionModel(cost)` returns `cost` without `abs`; see deviation row 27.
- `ScaledCost(scaling_type="notional", scaling_level=1e-4)` returns `level * |unit_measure(name) * q * additional_scaling|`, where the measure comes from `CostContext.measure`.
- `AggregateCost(models, aggregate="sum"|"max"|"min")`.
- gs `ScaledTransactionModel(scaling_type='notional_amount', ...)` does `getattr(instrument, 'notional_amount')` (gs 1.5.4 `backtest_objects.py:410-420`). In v2 the gs name must map to a pricing function or trade attribute of the asset config (Q3).
- Accrual: `ConstantCashAccrual(rate, basis_days, compounding)`, `SeriesCashAccrual(rates, basis_days, compounding)` and `CallableCashAccrual(fn)`. Basis and compounding are required with no default (spec C1). `wall_days(t0, t1)` measures wall-clock days in t0's zone.
- The gs formula, `value*(1+rate/(365 if annual else 1))**days` with `days = (to_state-from_state).days` (gs 1.5.4 `backtest_objects.py:728-742`), lives in the v1 facade, `backtests/backtest_objects.py:110-136`. v2 must expose it under the gs names `ConstantCashAccrualModel(rate=0, annual=True)` and `DataCashAccrualModel(data_source=None, annual=True)`.

### 3.15 `timeutil.py` (367 LOC), verdict KEEP

- `Calendar(holidays, weekmask, name)` with `following`, `preceding`, `modified_following`, `adjust`, `add_business_days`, `business_days` and `extended`.
- `parse_tenor` and `apply_tenor` follow the gs RelativeDate rules: `b` = business days, `d` = calendar days with no roll, `w`/`m` roll forward, `y` rolls backward.
- `schedule(start, end, freq, cal)` follows gs RelativeDateSchedule (start is not adjusted).
- `TimeContext(tz="America/New_York", calendar, date_policy="close", session_open=08:00, session_close=17:00)`.
- `TimeGrid.daily(start, end, freq="1b", ctx, business_only=True)`, `TimeGrid.intraday(...)`, `TimeGrid.from_config`.
- `Clock` (a monotone guard) and `TimelineContext(start, end, time, grid_days)`.
- `SECONDS_PER_YEAR = 31_557_600.0` is used only by the statistics.

gs `run_backtest(start, end, frequency='1m', states=None, holiday_calendar=None)` maps to `TimeGrid` through `schedule` or an explicit list of states.

### 3.16 `types.py` (31), `errors.py` (87), `common.py` (144), `registry.py` (90), `registries.py` (25), `pricable.py` (54), `risk.py` (245)

- **types.py**: keep `Phase(INITIAL=-1, EXIT=0, ADD=1, ADJUST=2, HEDGE=3)` and `as_list`. Delete `BASELINE_LAYERS` and `RESERVED_LAYERS` if layers go (results `attribution.py` and `reconcile.py` import `BASELINE_LAYERS`).
- **errors.py**:
  - Keep: `PricebtError, ConfigError, LookAheadError, MeasureError, MeasureNotBound, StrategyError, TriggerError, SizingError, DurationError, SignalError, MissingDataError, NotSupportedError, EngineInvariantError, OptionalDependencyError`.
  - Delete: `RegistryError` (with registry.py), `MethodCallError` (bindings), `StaleSnapshot` (snapshots).
  - Keep `MarketDataUnavailable` as the error raised when a market expression fails. The engine treats it as a data gap under `on_error`.
  - Add `AssetConfigError(ConfigError)`.
  - `MeasureNotBound` should mean "the asset config defines no function of this name". `_View._aggregate` (756) relies on it: `missing="zero"` counts only that error as 0.
- **common.py**: keep the enums; users need them because the asset config receives the kwargs verbatim, including `PayReceive.Pay` and `Currency.USD`. Change `check_currency` (119-136). Today it raises `NotSupportedError` for any currency other than the session reporting currency. In v2 a currency is valid if some asset or FX expression knows it.
- **registry.py / registries.py**: these exist only for YAML strategy config (`config/loader.py`), which is stripped. `registries.py:257-259` also imports `testing.toys`, which imports `contracts`. DELETE both. If callables given as strings are kept, move `resolve_dotted` into `strategy/` (Q5).
- **pricable.py**: keep `Valuation(ts, pv, cash=0.0, financing=0.0, meta={})` and `as_valuation(x, ts)`, which accepts a Valuation, a tuple `(pv[, cash[, financing]])` or a number. Delete `MarkContext` and `standalone`.
- **risk.py**: `RiskMeasure(str)`, where the value is the pricebt measure name. The gs names map as follows:

  | gs name | pricebt measure |
  |---|---|
  | `Price`, `DollarPrice` | `pv` |
  | `IRDelta(aggregation_level=Type/Asset/Class)`, `IRDeltaParallel` | `dv01` |
  | bare `IRDelta` | `delta_ladder` (vector) |
  | `IRGamma`, `IRGammaParallel` | `gamma` |
  | `IRVega` | `vega` |
  | `Theta` | `theta` |
  | FX* / Eq* | names only (`supported=False`) |

  `RISK_MEASURE_MAP` holds the name mapping (539), and `as_risk_measure(x)` converts inputs (549).
  - ADAPT: `_vector_names()` (542-546) reads `SchemaRegistry.default()`. Replace it with the vector flag declared by the asset config's function map.
  - The `currency=` parameter (468-476, 481-486) currently only validates. In v2 it must mean "convert to this currency" (gs `result_ccy` does `r(currency=result_ccy)`, gs 1.5.4 `generic_engine.py:796-804`).
  - The v2 naming decision: does `Price` map to an asset function named `npv` or `pv`? The task text uses `npv`; v1 uses `pv` everywhere (Q2).

### 3.17 `results/*` (1,743 LOC; missing from the v2 branch)

- `BacktestResult(record, *, config_hash, grid_info, stats_config)` (`result.py:66-260`).
  - Properties: `equity, equity_curve, pnl, step_pnl, trades, orders, errors, events, n_errors, positions, layers, layers_by_position, measures, vectors, manifest`.
  - Methods: `closed_trades()`, `open_positions()`, `trade_ledger()`, `summary_stats(config, labels="snake"|"gs")`, `returns()`, `drawdown()`, `rolling_sharpe()`, `attribution(by, tag_mode)`, `reconcile(tol, atol, strict)`, `tearsheet()`, `to_parquet()`, `from_parquet()`.
  - `result_summary` columns: `Price = positions_value`, `Cumulative Cash = initial_capital + cash`, `Transaction Costs = tcost`, `Total = equity`.
- `ledger.POSITION_COLUMNS = (template, action, kind, tags, side, status, entry_ts, exit_ts, exit_reason, entry_quantity, quantity, entry_pv, exit_pv, hold_points, hold_seconds, hold_days, pnl_price, pnl_income, pnl_gross, tcost, pnl_net, trade_cash, flow_cash, financing)`.
- `trade_ledger` columns: `Open, Close, Open Value, Close Value, Long Short, Status, Trade PnL`. v1 indexes it by position id; gs indexes it by trade name `<Action>_<Instrument>_<date>`.
- `stats.compute_stats` definitions are in the docstring (`stats.py:1-21`).
  - `StatsConfig(freq="session", basis="auto", annualisation=None, rf=0.0, mar=0.0, var_level=0.95, rolling_window=63)`.
  - `GS_LABELS` maps snake-case keys to gs labels.
  - gs 1.5.4 `BackTest` has **no** `summary_stats`; the v1 facade added one (`backtests/backtest_objects.py:337`). Treat it as an extension.
- `reconcile` checks: `identity, finite, step_pnl, cash_rollforward, tcost_rollforward, positions_trades, pnl_sum, positions_value, n_positions, layers, baseline_layers, errors(warn)` (`reconcile.py:1-23`). In v2, run each check per currency plus in the reporting currency. Keep it: it is the cheapest regression net for the multi-currency ledger.

---

## 4. Engine settings, frames and invariants to carry into the v2 spec

- **Identity (every row):** `equity = initial_capital + cash + tcost + positions_value`. In v2, compute it per currency before FX conversion and again in the reporting currency after.
- **Step P&L:** `step_pnl[t] = equity[t] - equity[t-1]`, with `equity[-1] = initial_capital`.
- **Determinism:** running twice gives identical frames, because triggers are deep-copied and `reset()` is called per run (`strategy.py:205-212`) and position ids are sequential per run.
- **No look-ahead:** `Clock.advance` enforces strict monotonicity, `SignalStore._check_ts` raises `LookAheadError`, and `SeriesSource` makes date-only keys visible from 17:00 local time.
- **Non-finite guard:** `_call_value` raises `EngineInvariantError` on a non-finite `pv`, `cash` or `financing`.

---

## 5. Cut list: every stripped symbol used by kept modules

| Stripped symbol | Used in (file:line) | v2 replacement |
|---|---|---|
| `contracts.spec.TradeTemplate` | `engine/state.py:11`, `engine/engine.py:18,263-287,774-792`, `orders.py:78`, `strategy/actions.py:17,63` | a v2 trade-spec class. The minimum is `(asset: str, kwargs: dict, name: str)` plus `with_kwargs(**kw)` and `clone(**kw)` (gs `Instrument.clone`). |
| `contracts.spec.layer_ref` | `engine/engine.py:18,621` | delete |
| `contracts.binding.Env` | `engine/engine.py:16,360,791` | the dict of injected variables |
| `contracts.binding.TENOR` | `strategy/signals.py:18,360` | local regex `\d+[DWMY]` |
| `contracts.evaluate.evaluate_value/measure/layer` | `engine/engine.py:17,...` | asset-config evaluator (another note covers it) |
| `contracts.evaluate.scalar_of, vector_of, vector_add, vector_scale, tenor_sort_key, is_vector` | `engine/engine.py:17`, `strategy/actions.py:15`, `risk.py:341` | new `pricebt/vector.py`: copy `contracts/evaluate.py:20-31,57-79` verbatim; `is_vector` becomes an asset-config flag |
| `contracts.schema.is_term_ref` | `strategy/actions.py:16,78` | delete, together with `resolve_template_refs` |
| `contracts.schema.SchemaRegistry` | `risk.py:342,545` | asset-config registry |
| `pricable.MarkContext` | `engine/engine.py:24,...` | delete |
| `pricer.Pricer` | `engine/state.py:15`, `engine/engine.py:25`, `view.py:10`, `pricable.py:271` | the market object returned by the asset config's `market` expression (type `Any`) |
| `market.MarketData` | `engine/engine.py:22`, `strategy/strategy.py:180` | per-asset market cache in the engine (`(asset, ts) -> market object`, cleared on each step as `evict_before` does now) |
| `registry.resolve_dotted` | `strategy/requirements.py:21,787`, `strategy/actions.py:21,732` | callables only, or move the function (Q5) |
| `registries.*` | `strategy/__init__.py:4,23-58` | delete that block |
| `testing.toys` (contracts-based) | `registries.py:257`, most engine tests | new asset-config toy fixtures (a toy forward or zero-bond asset config with a synthetic market expression) |

---

## 6. Strategy layer vs gs_quant API

Positional field orders come from introspection of gs 1.5.4 (installed). Where gs 2.1.17 differs, it is marked **[2.1.17]**.

### 6.1 Triggers and requirements

| gs class (1.5.4 fields, positional order) | v1 class (positional, then `*` keyword-only extensions) | Match | Notes |
|---|---|---|---|
| `Trigger(trigger_requirements=None, actions=None)` | `Trigger(trigger_requirements=None, actions=None, *, name=None, **req_kwargs)` | yes, plus extension | gs `Trigger.risks` = `[a.risk for a in actions if a.risk]`; v1 also includes the requirement's measures |
| `PeriodicTriggerRequirements(start_date, end_date, frequency, calendar)` | same, then `*, time=None, adjust_start="following"` | yes | semantics differ (row 9): None start/end means the backtest window; start is rolled to a business day; `Nd` rolls |
| `IntradayTriggerRequirements(start_time, end_time, frequency)` | same, then `*, tz, wrap_midnight=False, days` | yes | frequency: gs minutes (float); v1 also accepts `"90s"`, `"5min"`, `"1h"` |
| `MktTriggerRequirements(data_source, trigger_level, direction)` | same, then `*, tol=0.0, on_missing="raise"` | yes | both are level-triggered. v1 raises on a non-finite value; gs `check_barrier` compares silently |
| `RiskTriggerRequirements(risk, trigger_level, direction, risk_transformation)` | `StrategyRiskTriggerRequirements` (alias `RiskTriggerRequirements`), same, then `*, scope="portfolio", when_flat="skip", include_pending=True, check_every=None` | yes | gs `risk_transformation` is a `Transformer` object; v1 accepts only `None`, `"abs"`, `"neg"` or a callable. gs reads `backtest.results[state][risk].aggregate()` and returns False if `state not in results`; v1 skips when flat (`when_flat="skip"`). v1 also accepts `risk="equity"`, `"drawdown"`, `"pnl"` and `"layer.x"` (extension). |
| `AggregateTriggerRequirements(triggers=None, aggregate_type=ALL_OF)` | `(triggers=(), aggregate_type=ALL_OF)` | yes | v1 evaluates every child (no short circuit), unions the children's times and merges infos field by field (row 14). An empty aggregate raises. |
| `NotTriggerRequirements(trigger)` | same | yes | v1 propagates calc_type from the child |
| `DateTriggerRequirements(dates, entire_day=False)` | same, then `*, fire="all", time=None` | yes | row 11 |
| `PortfolioTriggerRequirements(data_source, trigger_level, direction)` | `(data_source="len", trigger_level, direction)`, then `*, scope, tol, include_pending` | default differs (gs `None`) | gs `'len'` returns `len(backtest.portfolio_dict)`, the number of **date keys** (a bug). v1 counts open positions plus pending orders and adds `"positions"`, `"firings"`, `"net:m"` and `"gross:m"` (row 13). |
| `MeanReversionTriggerRequirements(data_source, z_score_bound, rolling_mean_window, rolling_std_window)` | same, then `*, min_periods, ddof=1, exit_mode="offset", exit_z, allow_short=True` | yes | gs bugs (1.5.4 `triggers.py:366-373`): the long exit sets `self._current_position` (typo, so the position is never reset), and the short exit tests `current_price > rolling_mean` instead of `<`. v1 fixes both, requires a warm-up and guards a zero std (row 15). Q8: replicate or fix. |
| `TradeCountTriggerRequirements(trade_count, direction)` | same, then `*, count="positions", scope, include_pending=True` | yes | gs `len(portfolio_dict.get(state, []))`, which includes trades added at `state` |
| `EventTriggerRequirements(event_name, offset_days=0, data_source)` **1.5.4**; **[2.1.17]** `(event_name, offset_days, country, currency, source, start, end, data_source)` | matches **2.1.17**, then `*, events, time, roll="none"` | **positional mismatch vs 1.5.4** (a 3rd positional argument lands in `country`) | gs defaults `data_source` to `GsDataSource('MACRO_EVENTS_CALENDAR')`; v1 needs exactly one of `events` / `data_source` |
| `OrdersGeneratorTrigger(trigger_requirements, actions)` | `OrdersGeneratorTrigger(actions=None, *, name=None)` | signature differs | gs uses it only with PredefinedAssetEngine. Recommend dropping it. |
| `TriggerDirection{ABOVE=1, BELOW=2, EQUAL=3}` | adds `ANY=4` (Crossing only) | superset | |
| `AggType{ALL_OF=1, ANY_OF=2}` | same | yes | |
| `CalcType{simple, semi_path_dependent, path_dependent}` (backtest_utils) | same enum (`requirements.py:56`) | yes | gs actions also carry a `calc_type`: `HedgeAction` semi, `RebalanceAction` and `ExitAllPositionsAction` path-dependent. v1 ignores the action's calc type and uses phases instead. |
| none | `CrossingTriggerRequirements`, `CustomTriggerRequirements`, `RiskBandTriggerRequirements`, `RebalanceTriggerRequirements`, `EventCalendar` | v1-only | section 8 |

### 6.2 Actions

| gs class (fields, positional order; default cost = `ConstantTransactionModel(0)`) | v1 core class (positional, then `*` keyword-only) | Notes |
|---|---|---|
| `AddTradeAction(priceables, trade_duration, name, transaction_cost, transaction_cost_exit, holiday_calendar)` | same; `transaction_cost=None`; then `*, tags, phase, dated_priceables, pricer_request, on_past_exit="raise", quantity=1.0` | gs `dated_priceables` is a property set by `set_dated_priceables(state, priceables)`; v1 takes a keyword field. gs renames priceables to `f'{action.name}_{p.name}'` and trades to `..._{date}` (the ExitTrade name matching relies on this). v1 `transaction_cost_exit=None` means "same as entry" (`engine.py:277`), as in gs. |
| `AddScaledTradeAction(priceables, trade_duration, name, scaling_type=size, scaling_risk=None, scaling_level=1, transaction_cost, transaction_cost_exit, holiday_calendar)` **[2.1.17 adds `dated_priceables`]** | same, then `*, quantity, after_last="zero", on_zero_price="raise"` | NAV sizing differs (row 18); a dict `scaling_level` becomes a `StepTable` (row 33) |
| **[2.1.17 only]** `AddWeightedTradeAction(priceables, trade_duration, name, scaling_risk, total_size=100000.0, transaction_cost, transaction_cost_exit, holiday_calendar)` | same, then `*, weighting="risk_proportional"` | **absent from 1.5.4** |
| **[2.1.17 only]** `EarlyExitPositionLimitScaledAction(AddScaledTradeAction + early_exits=None, max_concurrent_pos=None)` | same (keyword-only) | **absent from 1.5.4** |
| `EnterPositionQuantityScaledAction(priceables, trade_duration, name, trade_quantity=1, trade_quantity_type=quantity, ...)` | facade stub that raises `NotSupportedError` | EquityVolEngine only; keep the stub |
| `HedgeAction(risk, priceables, trade_duration, name, csa_term, scaling_parameter='notional_amount', transaction_cost, transaction_cost_exit, risk_transformation, holiday_calendar, risk_percentage=100)` | same, then `*, mode="add", target=0.0, scope, min_trade_risk=0.0, on_zero_hedge_risk="raise", vector, ridge=0.0, bucket_weights` | gs sizes `current_risk/hedge_risk*pct/100` against `results[d]`, which is not refreshed between hedges on the same date. A zero hedge risk is skipped (`continue`). Different currencies raise `RuntimeError('cannot hedge in a different currency')` (1.5.4 `generic_engine.py:1027-1030`). v1 raises on a zero hedge risk by default and raises `NotSupportedError` for `risk_transformation` / `scaling_parameter` (row 20). |
| `ExitTradeAction(priceable_names, name, transaction_cost)` | same, then `*, match_tags="any", actions, kinds, position_ids, select, reason` (plus the inherited `tags`, which act as **selection** tags) | gs matches `x.name.split('_')[-2] in priceable_names` and `trade_date <= s` (so same-day trades are included). v1 uses structured selectors and exits only earlier-booked positions (rows 2 and 21). |
| `ExitAllPositionsAction(priceable_names, name, transaction_cost)` | same (inherits) | gs calc_type is path-dependent |
| `RebalanceAction(priceable, size_parameter=None, method=None, transaction_cost, transaction_cost_exit, name)` | `(priceable, size_parameter="quantity", method, transaction_cost, transaction_cost_exit, name, holiday_calendar)`, then `*, mode="add", measure, target, scope, match_templates, trade_duration=INHERIT` | gs calls `method(state, backtest, info)`; v1 calls `method(ts, view, info)` (row 22). gs size is `getattr(trade, size_parameter)`. gs has a bug: it appends the builtin `exit` to the transaction cost entries (1.5.4 `generic_engine.py:591`). |
| `ExitPositionAction(name)` | none | PredefinedAssetEngine only; drop |
| none | `CustomAction(fn, name, *, kwargs, needs_measures, needs_signals)`, `SubmitOrdersAction` | v1-only |
| `ScalingActionType{risk_measure, size, NAV}` | same, with `.coerce` | yes |

### 6.3 Strategy and engine entry point

| gs | v1 | v2 requirement |
|---|---|---|
| `Strategy(initial_portfolio=None, triggers=None, cash_accrual=None)` | `Strategy(initial_portfolio, triggers, cash_accrual, *, name, eval_mode)` | keep the positional form. `eval_mode` is an extension (Q9: keep `staged` only?). |
| `GenericEngine(action_impl_map=None, price_measure=Price)` | facade `GenericEngine(action_impl_map=None, price_measure=Price, *, market=None, session=None, ...)`; refuses `action_impl_map` | same shape |
| `run_backtest(strategy, start=None, end=None, frequency='1m', states=None, risks=None, show_progress=True, csa_term=None, visible_to_gs=False, initial_value=0, result_ccy=None, holiday_calendar=None, market_data_location=None, is_batch=True, calc_risk_at_trade_exits=False, pnl_explain=None)` | facade `run_backtest` refuses `calc_risk_at_trade_exits`, `pnl_explain`, and any `result_ccy` other than the session currency (row 41) | **`result_ccy` must work in v2** (section 9) |

### 6.4 Transaction and accrual models (gs names that the v2 public API must expose)

| gs | Fields | v1 core |
|---|---|---|
| `ConstantTransactionModel` | `cost=0` | `ConstantCost(cost_per_leg)` (applies `abs`) |
| `ScaledTransactionModel` | `scaling_type='notional_amount', scaling_level=0.0001` | `ScaledCost(scaling_type, scaling_level)` |
| `AggregateTransactionModel` | `transaction_models=(), aggregate_type=TransactionAggType.SUM` | `AggregateCost(models, aggregate)` |
| `TransactionAggType` | `SUM='sum', MAX='max', MIN='min'` | strings |
| `ConstantCashAccrualModel` | `rate=0, annual=True` | facade `backtests/backtest_objects.py:110` |
| `DataCashAccrualModel` | `data_source=None, annual=True` | facade `:123` |
| `OisFixingCashAccrualModel` | `start_date='-1y', end_date=today` | facade stub (needs a GS IRSwap Cashflows calc) |

### 6.5 Data sources

gs `GenericDataSource(data_set, missing_data_strategy=fail)` maps to v1 `SeriesSource`. gs `GsDataSource(data_set, asset_id, min_date, max_date, value_header='rate')` is a stub in v1 (it needs GS Dataset). gs `DataSource.get_data(state)` and `get_data_range(start, end|int)` should be provided as methods on the v2 `GenericDataSource` for porting user code (v1's `SeriesSource.get` and `window_at` are the equivalents).

### 6.6 Evaluation-order model (the most important semantic difference)

- **gs** (1.5.4 `generic_engine.py:754-873` and `909-1061`):
  1. Build the date schedule, plus the trigger times inside [start, end].
  2. Resolve the initial portfolio.
  3. For each trigger whose calc_type is not path-dependent: evaluate `has_triggered` on **all dates**, then apply each non-path-dependent action over all triggered dates. Actions are looked up by exact class in `info_dict`, then by `isinstance`.
  4. Price everything.
  5. Date by date: path-dependent triggers and path-dependent actions run, then hedges are scaled.
  6. Price new trades.
  7. Cash: `cash_dict[d][ccy]`, accrued by `cash_accrual.get_accrued_value(current_value, d)` only from the last payment date.
- **v1**: a single date-major pass (sections 3.1 and 3.5).

For gs-built strategies whose simple triggers are pure functions of the date and the data, the two orders are equivalent. They differ in three places:
- the same-day exit/add interaction (row 2)
- stateful simple triggers
- hedges on the same date (gs does not refresh `results[d]` between them)

The v1 deviation doc pins each case with tests.

### 6.7 Result object

gs `BackTest` exposes these public members (introspected): `CUMULATIVE_CASH_COLUMN, TOTAL_COLUMN, TRANSACTION_COSTS_COLUMN, add_results, calc_calls, calculations, cash_dict, cash_payments, get_risk_summary_df, hedges, holiday_calendar, pnl_explain, pnl_explain_def, portfolio_dict, result_summary, results, risk_summary, set_results, strategy_as_time_series, trade_exit_risk_results, trade_ledger, transaction_cost_entries, transaction_costs` (plus the JSON helpers).

gs `result_summary` (1.5.4 `backtest_objects.py:210-236`):
- columns: one per risk measure (the RiskMeasure object as label), then `Cumulative Cash`, `Transaction Costs` (cumsum), `Total = price + cash + tcost`
- `ffill().fillna(0)`, sliced to `[:states[-1]]`
- **raises `RuntimeError('Cannot aggregate cash in multiple currencies')`** when `cash_dict` holds more than one currency

v1: `BacktestResult` is not gs-shaped. The facade `backtests/backtest_objects.py::Backtest` (lines 203-417) wraps it and provides `result_summary` (via `ResultSummary(pd.DataFrame)`, which accepts RiskMeasure keys), `risk_summary`, `ladder(risk)`, `trade_name`, `trade_ledger`, `summary_stats(annualisation_factor=252)` (extension) and a stub `pnl_explain`. For v2, recommend one gs-shaped result class built on `BacktestResult`. Keep `summary_stats`, `reconcile` and `equity` as labelled extensions.

---

## 7. Deviations (v1 register rows 1-44) with v2 recommendation

Legend. **KEEP-DEV** = keep the v1 behaviour and document it as a deviation. **GS** = revert to gs behaviour for 1:1 parity. **EXT** = keep, but only as an opt-in with the gs default. **N/A** = the row concerns stripped machinery.

| Row | Topic | v2 recommendation | Reason |
|---|---|---|---|
| 1 | date-major evaluation order | KEEP-DEV | equivalent for pure-date simple triggers; a single pass is what makes the event loop possible |
| 2 | exits only touch earlier-booked positions; EXIT before ADD | KEEP-DEV (flag to user) | the gs behaviour creates zero-length trades |
| 3 | staged path-dependent evaluation | KEEP-DEV; `eval_mode` EXT | |
| 4 | off-grid exits add a timeline point | KEEP-DEV | gs carries Price forward on those dates, so its Total is stale |
| 5 | deep copies, reset, per-strategy action numbering | KEEP-DEV | repeatable runs |
| 6 | no wall clock | KEEP-DEV | |
| 7 | ordered risk lists | KEEP-DEV | |
| 8 | `fill_lag` / `fill_price` | EXT (default 0 = gs) | |
| 9 | periodic schedule defaults | KEEP-DEV | gs `start_date=None` means today |
| 10 | intraday schedule arithmetic | KEEP-DEV | gs loops forever for some inputs |
| 11 | date trigger normalisation | KEEP-DEV | |
| 12 | non-finite barrier raises; Crossing added | KEEP-DEV; Crossing EXT | |
| 13 | Portfolio `'len'` counts positions | KEEP-DEV (the gs value is a bug) | |
| 14 | aggregate evaluates every child | KEEP-DEV | |
| 15 | mean reversion fixes | **Q8**. Recommend KEEP-DEV: the gs bugs make it re-enter forever after a long exit. | the requested notebook is exactly this trigger, so the user must decide |
| 16 | string info keys, one info per action | KEEP-DEV (gs class keys still accepted) | |
| 17 | trade duration grammar | KEEP-DEV; the attribute-name lookup moves to the asset config (Q3) | |
| 18 | NAV / risk sizing guards | KEEP-DEV | |
| 19 | weighted weighting names | KEEP-DEV if AddWeighted stays (Q1) | |
| 20 | hedge includes pending orders; zero risk raises | KEEP-DEV; `on_zero_hedge_risk` default: Q10 (gs skips silently) | |
| 21 | structured exit selectors | KEEP-DEV; the facade keeps `priceable_names` matching (instrument, leg, action or gs trade name) | |
| 22 | rebalance `method(ts, view, info)` | **Q4** | parity would mean passing a gs-shaped backtest view |
| 23 | initial portfolio segments | KEEP-DEV | |
| 24 | core accrual without a convention; gs formula in facade | KEEP-DEV; the gs names are the public API | |
| 25 | accrual every point | KEEP-DEV | |
| 26 | scaled cost uses a measure, not `getattr` | ADAPT: `notional_amount` goes to the asset config (Q3) | |
| 27 | constant cost uses `abs` | **GS?** (Q11) | gs allows a negative cost (a credit) |
| 28-33 | signals: no look-ahead, immutable, publication time, no interpolate, look-ahead guard, StepTable | KEEP-DEV | |
| 34 | no data services; EventTrigger needs a user calendar | KEEP-DEV | this is the zero-infrastructure requirement |
| 35 | a row on every point | KEEP-DEV | |
| 36 | statistics from the grid | EXT (gs BackTest 1.5.4 has no `summary_stats`) | |
| 37 | trade ledger `Long Short` = sign of quantity | **GS?** (Q11): gs reports the entry cash direction (-1) | |
| 38-40, 42 | IRSwap template, percent rates, PricebtSession, RiskMeasure = schema names | **N/A**: replaced by the asset-config design. Note: row 39 (percent vs decimal) disappears because pricebt no longer interprets kwargs. | |
| 41 | unsupported engines and objects | KEEP-DEV, except `result_ccy`, which must work | |
| 43 | one calendar, tz-aware axis | KEEP-DEV | |
| 44 | import map `pricebt.backtests.*` | KEEP (this is the 1:1 import requirement) | |

---

## 8. v1-only extensions: disposition

| Extension | Where | Tests | Disposition |
|---|---|---|---|
| Ladder (vector) hedge: `vector`, `ridge`, `bucket_weights`, `ladder_hedge_quantities` | `actions.py:516-560`, `sizing.py:507-559` | `test_ladder.py` (21, uses contrib.rateslib; DELETE), `test_ladder_sizing.py` (5), `test_ladder_hedge_dust.py` (9) | **keep as an extension** if the delta ladder, a portfolio-level `(market, trades) -> {tenor: ccy/bp}` expression, is in the v2 asset config. The maths is pure; only the plumbing changes. |
| `HedgeAction` `mode="resize"`, `target`, `scope`, `min_trade_risk`, `on_zero_hedge_risk` | `actions.py:449-573` | `test_strategy_actions.py` | extension (defaults are gs-like, except `on_zero_hedge_risk`) |
| `RiskBandTrigger` / `RebalanceTrigger` | `requirements.py:812-926` | `test_strategy_triggers.py`, `test_strategy_engine.py`, `test_strategy_runner.py` | extension (drop candidate) |
| `CrossingTrigger` | `requirements.py:320-380` | same | extension |
| `CustomTrigger` / `CustomAction` / `SubmitOrdersAction` | `requirements.py:771-809`, `actions.py:717-752` | same, plus `test_strategy_actions.py` | extension. The idiomatic gs route is subclassing, but these are cheap. |
| `RebalanceAction` `measure=`, `mode="resize"`, `match_templates` | `actions.py:645-714` | `test_strategy_actions.py` | extension |
| `EarlyExitPositionLimitScaledAction`, `AddWeightedTradeAction` | `actions.py:355-422` | `test_strategy_actions.py`, `test_strategy_engine.py` | gs 2.1.17 API. Keep if the target is 2.1.17 (Q1). |
| Signals: `PricerSignal`, `MeasureSignal`, `BookMeasureSignal`, `BookLadderSignal`, `DerivedSignal`, `ConstantSource`, `StepTable` | `signals.py` | `test_strategy_signals.py` (29), `test_engine_cycle3.py`, `test_engine_observation.py` | `StepTable` is needed (gs `scaling_level` dict). `PricerSignal` must be replaced by an asset-config signal (section 3.12). The rest are extensions. |
| `fill_lag` / `fill_price` | `engine.py:197-261` | `test_engine.py`, `test_regressions.py`, `test_strategy_engine.py` | extension |
| `eval_mode="snapshot"` | `strategy.py:111,213` | `test_strategy_runner.py`, `test_strategy_engine.py` | drop candidate (Q9) |
| attribution layers, `cadence`, `strict_layers` | `engine.py:436-551` | `test_engine*.py`, `test_results_attribution.py` | **DELETE**: they depend on per-instrument bound layer callables (v1 binding machinery). A v2 pnl-explain could later be a portfolio-level asset-config expression. |
| Taylor baseline (`tay_delta`, `tay_convexity`, `tay_unexplained`) | `engine.py:477-521` | `test_engine_baseline.py` (14) | DELETE (it reads the schema unit of `rate`) |
| audit and tie-out marks | `engine.py:410-433`, `RunRecord.audit` | `test_engine_audit.py` (17), `test_tieout*.py` | DELETE (the tie-out is stripped) |
| `measures` / `vector_measures` recording | `engine.py:599-606` | `test_engine_observation.py`, `test_engine_cycle3.py` | **needed**: this implements gs `risks=` and `risk_summary` |
| `record_signals` | `engine.py:602-603` | `test_engine_observation.py` | extension |
| `PositionSpec` | `strategy.py:89-97` | `test_strategy_engine.py`, `test_ladder.py` | extension |
| `exit_policy="next_grid"` | `engine.py:126-132` | `test_regressions.py` | extension |
| `on_past_exit`, `on_zero_price`, `after_last` | `actions.py` | `test_strategy_actions.py` | extension (opt-ins) |
| `Strategy.backtest`, `Strategy.periodic`, `engine_settings` | `strategy.py:169-196` | `test_strategy_engine.py` (some) | DELETE (conveniences over `MarketData`) |
| YAML registries | `strategy/__init__.py:23-58`, `registries.py` | `test_strategy_runner.py`, `test_strategy_signals.py`, `test_gs_compat_api.py`, `test_facade_review.py` | DELETE |

---

## 9. Multi-currency: what the v1 engine lacks and what gs does

| Aspect | gs 1.5.4 | v1 | v2 requirement |
|---|---|---|---|
| cash | `cash_dict[date][ccy]`, keyed by the unit of each priced trade (`generic_engine.py:1162-1171`) | one scalar `Engine.cash_account` (`engine.py:83`) | `cash_by_ccy: Dict[str, float]`. Book each trade's cash in its asset's currency. |
| accrual | `get_accrued_value` loops over currencies with one rate | one balance | apply the accrual per currency. A mapping `{ccy: model}` is an extension. |
| costs | a single `transaction_costs` number per date | one scalar `tcost_account` | in the asset currency, or in reporting (Q12) |
| risk aggregation | `results[d][risk].aggregate()`; `result_ccy` re-requests every risk with `currency=result_ccy` | `measure()` sums raw numbers (single currency assumed) | `_View._aggregate` must convert each position's measure to the reporting currency with the FX expression at `now` before summing. Risk is in currency per 1bp. |
| hedge | raises `RuntimeError('cannot hedge in a different currency')` if units differ | n/a | both sides in the reporting currency, or raise as gs does (Q10b) |
| result_summary | raises if more than one cash currency and `result_ccy` is None | single currency | same rule. With `result_ccy`, convert. Store per-currency cash columns (`Cumulative Cash USD`, ...) as an extension. |
| equity identity | n/a | scalar | hold per currency before conversion; after conversion, `Total = Price + Cumulative Cash + Transaction Costs` in the reporting currency |
| `common.check_currency` | n/a | raises for any currency but the reporting one (`common.py:119-136`) | rewrite |

---

## 10. Test coverage of the reviewed modules

Counts are `def test_` occurrences. "Imports" means the file imports the module directly; many modules are also exercised indirectly through the strategy and engine. **Survives v2** says whether the test logic applies once its fixtures are ported (toys to asset-config toys).

| Test file | Tests | Modules exercised | Depends on stripped code | Survives v2 |
|---|---|---|---|---|
| `test_engine.py` | 28 | engine, orders, costs, errors | `testing.toys` (contracts), `toy_helpers` (TradeTemplate) | yes, after porting fixtures (core ledger, fill_lag, exits, look-ahead) |
| `test_engine_audit.py` | 17 | engine audit | toys, market, pricer | no (audit deleted) |
| `test_engine_baseline.py` | 14 | engine baseline | toys, pricable | no |
| `test_engine_cycle3.py` | 16 | engine, results, signals | toys | partly (drop layer and audit cases) |
| `test_engine_observation.py` | 14 | engine measures and signals recording, results | toys | mostly |
| `test_progress_notebook.py` | 12 | engine.progress | toys, market (engine runs) | yes |
| `test_strategy_actions.py` | 29 | actions, costs, orders, types | `contracts.spec`, toys | yes, after porting fixtures |
| `test_strategy_durations.py` | 16 | durations, timeutil | `test_strategy_support` (contracts) | yes |
| `test_strategy_engine.py` | 39 | strategy end-to-end, engine | toys, pricable, market | yes, after porting fixtures (pins deviations rows 1-5, 13, 15, 20-23) |
| `test_strategy_infos.py` | 9 | infos | none | yes, unchanged |
| `test_strategy_runner.py` | 12 | strategy.triggers, StrategyRun, types, registries | registries | yes (drop registry cases) |
| `test_strategy_signals.py` | 29 | signals | toys, registries | mostly |
| `test_strategy_sizing.py` | 9 | sizing | none | yes, unchanged |
| `test_strategy_triggers.py` | 40 | requirements, triggers | `test_strategy_support` | yes |
| `test_strategy_support.py` | 0 (helpers) | | contracts, toys | rewrite |
| `test_ladder_sizing.py` | 5 | sizing (ladder) | none | yes |
| `test_ladder_hedge_dust.py` | 9 | sizing | none | yes |
| `test_ladder.py` | 21 | ladder hedge end-to-end | contrib.rateslib, config, contracts | no |
| `test_regressions.py` | 11 | engine, accrual, strategy | config.loader, toys | partly |
| `test_results_attribution.py` | 13 | results.attribution | none directly | if attribution kept |
| `test_results_common.py` | 0 (helpers) | | contracts, toys | rewrite |
| `test_results_compare_report.py` | 9 | results.compare, report | none | if kept |
| `test_results_io.py` | 7 | results.io | none | if kept |
| `test_results_reconcile.py` | 9 | results.reconcile | toys | yes |
| `test_results_result.py` | 17 | results.result, engine.state | toys | yes |
| `test_results_stats.py` | 22 | results.stats | none | yes, unchanged |
| `test_results_tearsheet.py` | 9 | results.tearsheet | none | if kept |
| `test_core_time.py` | 9 | timeutil | none | yes, unchanged |
| `test_gs_compat_accrual.py` | 5 | facade accrual | `backtests.backtest_objects` | yes |
| `test_gs_compat_api.py` | 17 | facade, costs, common, risk, registries | session, stacks | partly (rewrite for the asset config) |
| `test_registry_allow.py` | 7 | registry | none | no (registry deleted) |
| `test_core_pricable.py` | 7 | pricable, market, pricer | market, pricer, toys | partly (keep `test_as_valuation_normalises` and the import-hygiene test) |

Direct import counts across `tests/` (grep of `from pricebt.<m> import`):

| Module | Test files importing it |
|---|---|
| `view` | 0 (exercised through the engine) |
| `strategy.requirements` | 0 (exercised through the `pricebt.strategy` re-exports) |
| `strategy.strategy` | 0 (same) |
| `engine` | 11 |
| `results` | 16 |
| `orders` | 18 |
| `costs` | 7 |
| `accrual` | 1 |
| `timeutil` | 28 |
| `errors` | 76 |
| `common` | 6 |
| `risk` | 4 |
| `registry` | 3 |
| `registries` | 4 |
| `pricable` | 24 |

Most `pricable` importers are adapter and tie-out tests, which are deleted.

**Fixture blocker.** `tests/conftest.py` imports `pricebt.testing.toys.ToyMDP` and `pricebt.market.MarketData`, and `tests/toy_helpers.py` imports `contracts.spec.TradeTemplate`. Every engine and strategy test runs through these fixtures, so the v2 plan must port them first: a toy asset config (for example a forward with a synthetic market expression), a `make_engine` fixture, and `assert_identity`.

---

## 11. Recommended v2 layout for these pieces (for the design author)

```
pricebt/
  engine/{__init__,engine,state,progress}.py   # ADAPT / ADAPT / KEEP
  strategy/{__init__,strategy,triggers,requirements,actions,infos,durations,sizing,signals}.py
  results/{__init__,result,ledger,stats,reconcile,errors}.py  (+ compare/io/tearsheet only if kept)
  backtests/   # gs-named public API: triggers, actions, strategy, generic_engine, backtest_objects, data_sources
  timeutil.py orders.py costs.py accrual.py types.py errors.py common.py risk.py vector.py(new) valuation.py(from pricable)
```

Delete `registry.py`, `registries.py`, `pricable.MarkContext`, all of `contracts/`, `market.py` (replaced by the asset-config market cache), `pricer.py` (as a protocol), `tieout/`, `contrib/`, `config/stacks.py` and `testing/refstack.py`.

---

## 12. Open questions (referenced above)

| # | Question | Default if unanswered |
|---|---|---|
| Q1 | Which gs version is the API target? Installed 1.5.4 (the user said this is the reference) or the 2.1.17 source checkout (which has the mean-reversion notebook, `AddWeightedTradeAction`, `EarlyExitPositionLimitScaledAction` and the longer `EventTriggerRequirements` field order)? | 2.1.17, which is a superset. v1 already matches it, and the 1.5.4 positional forms still work except for `EventTriggerRequirements(event_name, offset_days, data_source)`. |
| Q2 | Does gs `Price` map to an asset-config function named `npv` (task wording) or `pv` (v1 name everywhere)? | `npv` in the asset config; `RiskMeasure` maps `Price` to it |
| Q3 | How does v2 resolve gs attribute reads on the opaque trade (`trade_duration='termination_date'`, `ScaledTransactionModel('notional_amount')`, `RebalanceAction(size_parameter='notional_amount')`)? Options: an asset-config `attributes` expression returning a dict, or `getattr(trade, name)`. | an asset-config `attributes` map with a `getattr` fallback |
| Q4 | Should the view passed to triggers and to `RebalanceAction.method` also offer gs `BackTest` attributes (`portfolio_dict`, `results`, `cash_dict`, `states`)? | no, keep deviation row 22, but document it prominently |
| Q5 | Keep `'pkg.mod:fn'` strings for `CustomTrigger`/`CustomAction` (via `resolve_dotted`) now that YAML strategy config is gone? | no, callables only |
| Q6 | Drop attribution layers and baseline entirely (and `results/attribution.py` by-layer and by-baseline)? | drop; keep `attribution(by=tag/template/action/day/component)` as an extension |
| Q7 | Keep `tearsheet.py`, `report.py`, `compare.py` and `io.py`? | keep `compare` and `io`; drop `report`; keep `tearsheet` only as an optional-dependency extension |
| Q8 | Mean reversion: replicate the gs 1.5.4/2.1.17 bugs for byte parity, or keep the v1 fixes? | keep the fixes (deviation row 15) and state it in the example notebook |
| Q9 | Keep `eval_mode="snapshot"`? | drop; `staged` only |
| Q10 | `HedgeAction.on_zero_hedge_risk` default: gs skips silently, v1 raises. (b) Cross-currency hedge: convert or raise like gs? | (a) keep `raise`; (b) convert to the reporting currency |
| Q11 | Revert rows 27 (`abs` on constant cost) and 37 (ledger `Long Short` sign) to gs behaviour for parity? | keep the v1 behaviour |
| Q12 | Which currency are transaction costs booked in: the traded asset's currency or the reporting currency? | the asset currency, converted like cash |
