# pricebt: design and implementation (as built)

A config-driven, event-driven backtester in the spirit of Goldman Sachs' `gs_quant.backtests` (Strategy / Trigger / Action / signals / engine), whose **core has no dependence on any pricing library, market-data infrastructure or vendor SDK**. Any instrument that can be *valued at a timestamp by something that holds market data* can be backtested; a pricing library is plugged in by configuration: a schema name is bound to a callable, market data arrives as plain snapshots, and an adapter turns a snapshot into the library's own objects. The purpose is to run ONE backtest across rateslib, QuantLib and a dependency-free reference stack and tie the results out level by level.

This is the as-built reference. The requirements it was built to are `docs/design/11-refactor-spec.md`, the decisions are recorded in `docs/design/adr/`, and what changed and why in `docs/design/11-refactor-changelog.md`. The pre-refactor design sections, their reviews and the report of the pre-refactor suite runs were removed in the final cleanup (a full copy is in `..\pricebt-final-snapshot`); the two research notes that remain (`docs/research/rateslib.md`, `docs/research/mdp-feasibility.md`) are cited by the code that relies on them. Where prose disagrees with the code, the code wins.

## 1. The one rule, and the four ideas

**Core** (everything under `src/pricebt` except `contrib/`) has zero dependence on any pricing library or vendor (rateslib, QuantLib, ARBS, gs_quant, anything else) at import, name, shape and semantic level. External code is reached only through **schema name -> binding -> callable**. Guard tests enforce it (section 14).

```
schema    the neutral names pricebt expects (value, dv01, gamma, rate, delta_ladder, carry, roll, delta, convexity ...), with units and signs      (ADR 002)
binding   {target: {method: ...}, kwargs: {...}, reduce, scale, offset, sign}: which callable provides one schema name, with exactly its arguments (ADR 001)
snapshot  plain, immutable market data (discount factors, fixings, quotes, calendars) emitted by ANY provider; its digest proves what a run consumed   (ADR 004)
wrap      the pricer role's adapter function: the one place a snapshot becomes a library's objects; a stack overlay swaps it                         (ADR 006)
```

* **Instrument spec vs terms.** An instrument spec is defined once (asset class, `factory` = the adapter's Kit, `conventions`, `bind`); an action supplies the **terms** of each trade (`{instrument: usd_sofr_ois, terms: {side: receive, maturity: 10Y, notional: 1e7, fixed_rate: par}}`). The library resolves dates and `par` at the fill pricer and the engine records what it resolved (ADR 003).
* **Pricer** = one snapshot at one timestamp, wrapped by the adapter. **MarketDataProvider** = `get_pricer(ts, request) -> Pricer`; `ts` is a tz-aware `pd.Timestamp`; a `TimeMapping` (`asof` with `max_staleness`, `exact`, `date` with `eod_visible_at`) says how a timestamp reaches a stored snapshot. The package ships no provider (D6): the fixture-backed ones are test support (`tests/support`) and the library-free `testing/synthetic.py`.
* **Layers** are schema names bound like any method (`carry`, `roll`, `delta`, `convexity`, each with an id and a version); the engine owns `unexplained` (ADR 005).

## 2. Architecture and layering

```
config   -> strategy, engine, market, contracts   (builds the object graph from YAML/JSON; CLI: run | validate | describe | tieout)
tieout   -> results                                (compare two runs level by level; library-free)
results  -> engine                                 (BacktestResult, stats, attribution, reconcile, tearsheet, parquet)
strategy -> engine.view (read-only)                (triggers, actions, signals, Strategy)
engine   -> market, contracts                      (event loop, positions, ledger, execution, costs, audit, progress)
market   -> snapshot                               (MarketData: named provider bindings, memo, look-ahead guard, wrap once per snapshot)
contracts-> stdlib, numpy, pandas, pyyaml          (schemas, bindings, instrument specs, Kits, evaluation)
contrib.rateslib, contrib.quantlib -> core (+ their library)     testing -> core (toys, reference stack, synthetic market, layer conformance kit)
```

`import pricebt` never imports rateslib, QuantLib, matplotlib, ARBS or gs_quant (enforced). The libraries are optional extras (rateslib's licence is non-commercial unless a commercial licence is registered).

| package | modules (src/pricebt) | role |
|---|---|---|
| contracts | `contracts/{schema,binding,spec,evaluate}` + `schemas/{generic,swap,bond}.yaml` | the neutral vocabulary and how it is called |
| core | `errors, types, common, timeutil, snapshot, pricer, market, pricable, orders, costs, accrual, risk, view, registry, registries, session, instrument` | time model, snapshots, facade |
| engine | `engine/{engine,state,progress}` | the event loop |
| strategy | `strategy/{strategy,triggers,requirements,actions,infos,signals,durations,sizing}` | gs_quant port + ladder hedge + book signals |
| config | `config/{yamlio,loader,stacks,cli}`, `api.py`, `__main__.py` | YAML/JSON -> objects; stack overlays; `pricebt run|validate|describe|tieout` |
| results | `results/{result,stats,attribution,reconcile,tearsheet,io,compare,report,ledger,errors}` | outputs |
| tieout | `tieout/{runner,compare,tolerances,report}` | X1-X6 |
| backtests | `backtests/*` | the labelled gs_quant compatibility layer (gs field vocabulary, the gs `1 + r/365` cash accrual) |
| contrib | `contrib/rateslib/*`, `contrib/quantlib/*` | adapters: kits (factory + default bindings), `wrap`, conventions blocks, `STACK` |
| testing | `testing/{toys,scripted,synthetic,refstack,layer_conformance}` | toys with deliberately mismatched names, the reference stack, a synthetic market, the layer kit |

## 3. Contracts: schemas, bindings, specs

**Schemas** (`contracts/schemas/*.yaml`, `AssetSchema`): data files saying what pricebt expects, never how a library provides it. Per asset class: `terms` (with the direction sign of `side`), `methods`, `measures`, `layers` (id + version), a `conventions` **vocabulary** (keys, types, tokens; validated by `check_conventions`; pricebt applies none of them) and optional `pricer_capabilities` (lookups). Unknown keys are errors; `extends:` merges entry by entry. `dv01`, `gamma` and `delta_ladder` are **dollar deltas** (currency per +1bp; a payer swap is positive, a long bond negative); `delta_ladder` is a plain `dict[str, float]` keyed by tenor (`<int><D|W|M|Y>`), required on `swap`.

**Bindings** (`contracts/binding.py`): one schema name -> one callable, declaring exactly its arguments. Target kinds `method`, `function` (allow-listed dotted path), `pricer_method`, `attribute`; references from a closed grammar (`@pricer`, `@ctx`, `@instrument`, `@terms.<n>`, `@state.<k>`); post-processing `sign * (scale * reduce(x) + offset)`; a ladder declares its result `keys`. Strict mode: every required name (methods, measures AND layers) must be bound or the load fails with a did-you-mean; nothing is ever resolved by a same-named method and nothing is injected by parameter name.

**Instrument specs and kits** (`contracts/spec.py`): `Kit(factory, asset_class, default_bind, cls, extra, schema, doc)`; a spec merges the kit's default bindings with the user's `bind:` by name. `factory(pricer, ts, *, terms, conventions) -> Built(obj, resolved_terms)`. `TradeTemplate(name, spec, terms)` is what actions carry; terms may hold `@signal.<n>`, `@param.<n>`, `@trigger.scaling`. `Stack(name, wrap, instruments, defaults, accepts_gs)` is an adapter's bundle for the facade and for overlays.

## 4. Market data: snapshots and `wrap`

A `MarketSnapshot` (`snapshot.py`) is frozen, pickle-safe plain data: `CurveSnapshot` (node dates and discount factors, an interpolation TAG), `FixingsSeries`, `QuoteSet` (bond quotes with reference data and on-the-run aliases), calendars as holiday sets. `digest()` is a sha256 over a canonical byte encoding, independent of `hash()` randomisation, mapping order and provenance. A provider returns a `SnapshotPricer`; `MarketData` applies the role's `wrap` **once per snapshot and request time** (memo key `(role, digest, stamp)`) and records the digest BEFORE the wrap when auditing. Spot lags, day counts, calendar names and yield rules are conventions in the instrument spec, never in a snapshot.

## 5. Time

Everything is a tz-aware `pandas.Timestamp` (default `America/New_York`). `TimeContext` (tz, one `Calendar`, `date_policy` open|close|midnight, session open/close) is shared by the grid, trigger schedules and trade durations. `TimeGrid` builders: daily (`1b`, `1w`, `1m`, ... with gs_quant `RelativeDate` rules), intraday (`1min`, `15min`, `1h`, DST-safe) or from the provider's timestamps. The engine timeline is `grid U trigger times U scheduled exit times`. A calendar resolves through the `CALENDARS` registry, a JSON path or a list of holidays; core knows no calendar by name. The year statistics annualise by is one named constant (`timeutil.SECONDS_PER_YEAR`, a Julian year).

## 6. Engine semantics (`engine/engine.py`)

Per timeline point: (1) `clock.advance`, cash interest on the previous balance (`CashAccrualModel`); (2) scheduled exits due; (3) fills queued by `fill_lag >= 1`; (4) signals observe (they **peek** at the positions: no mark is booked; the signal store is handed only a peeking view, so no way of evaluating a signal can mark); (5) the strategy step; (6) execution of pending orders; (7) marks of all open positions (idempotent per position and point), then layers at cadence and measures; (8) one recorded row and one progress tick.

* Ledger identity (asserted by `BacktestResult.reconcile()`): `equity = initial_capital + cash_account + tcost_account + sum(qty * pv)`. Entry books cash `-qty*pv`, exit `+qty*pv`; realised `cash`/`financing` accumulate into the cash account; costs go to their own account. With `initial_capital = 0`, equity is cumulative P&L.
* `fill_lag=0`: orders are priced and booked at the point with that point's pricer. `fill_lag>=1`: priced at the decision point, booked (cash, costs) and visible from the next point; a lagged close/resize is valued and layer-flushed at the decision point and booked at the locked price; orders on the last point never fill. `fill_price: next` re-prices a lagged order at the booking point.
* A trigger or action that reads P&L sees it **as of the previous mark**; a strategy asking for a measure marks the positions at that moment, exactly as it always did. A **signal only peeks**: a book signal evaluates the measures at this point's market without booking a mark, so recording a signal cannot change `view.cash`, the P&L a trigger sees or any trade (observation-only, tested).
* `backtest.exit_policy: own_time | next_grid`; `view.measure(name, scope, missing='raise'|'zero')`; exits/resizes only touch positions booked at an earlier point; targeting is by structured `PositionSelector`.
* Errors: `on_error='raise'` (default, fail fast) or `'record'/'skip'` (an `ErrorRecord` per failure). **Observation-only switches never change what a run does**: `attribution.baseline`, `audit` and `record_signals` add rows and columns, never a trade, a fill, a cash figure, whether the run completes, or a layer's flush dates. A baseline that cannot be evaluated never fails a run, under any `on_error`: one recorded error per position (a swap held past its maturity has no `rate`); after a mid-life failure the rest of that position's P&L is booked as `tay_unexplained` so the layers still reconcile, an entry-time failure gives no baseline rows. An audit measure that fails is a NaN cell and one recorded error (only a measure the instrument does not bind is a silent NaN). The `every_n` cadence counts library-layer positions only, so the baseline cannot move a library layer's flush date.
* Look-ahead: `MarketData.pricer(ts)` raises `LookAheadError` if `ts` exceeds the clock or the provider returns a snapshot stamped after `ts`.
* Progress: exactly one `tqdm` bar; `quiet_tqdm()` forces every other bar created during a run to `disable=True`. In a terminal it is tqdm's console bar (redrawn in place with a carriage return). Inside a notebook KERNEL the same bar is drawn into ONE display output that every refresh replaces (`update_display_data`), because a frontend that does not fold carriage returns across messages (VS Code) prints one line per step; a saved notebook keeps a single final bar (`engine/progress.py`, `tests/test_progress_notebook.py`). Determinism: no wall-clock in logic, sequential position ids `P000001`; two runs are bit-identical; a second run of the same engine starts clean (`MarketData.reset`).

## 7. Attribution (ADR 005)

Layers are computed at `cadence` = `each | eod | every_n:<k>` (off by default; also flushed at every close/resize) as **per-unit P&L over the interval since the previous cadence point**, by binding. The engine computes `unexplained = interval P&L - sum(layers)`; `total, transactions, cash_interest, unexplained, financing` and the baseline names are reserved.

Definitions (both adapters and the reference stack): `carry = X_fwd - V0` (the t0 world's flows valued at t1), `roll = X_roll_act - X_fwd` (the curve unchanged in tenor space anchored at t1, including realised fixings), `delta = (V_base + cash - X_roll_act) + g.dz` (directional derivative along the realised zero-rate move), `convexity = 1/2 dz'H dz`. Golden case (seasoned 3y payer, 50mm): carry -561.82, roll 12,159.98 raw (1,532.07 folded with the realised fixings), delta 70,190.64 raw (70,302.17 folded), convexity -50.23, residual 0.02, total 71,222.20.

**Baseline** (`attribution.baseline: true`; needs `dv01`, `gamma`, `rate` bound, checked at load): the engine's own library-independent Taylor decomposition `tay_delta = dv01(start) x d rate_bp`, `tay_convexity = 1/2 gamma(start) x d rate_bp^2`, `tay_unexplained` = interval P&L minus both; the bp factor comes from the unit the schema declares for `rate`. It is an ALTERNATIVE decomposition of the same P&L: `attribution(by="layer")` shows the library layers, `attribution(by="baseline")` the baseline, both summing to the total.

**Audit** (`backtest.audit: {measures: [...]}`): the snapshot digest per fetch, per position and point the pv, cash, financing and requested measures (a vector measure one column per bucket), per position and flush the layer amounts; persisted as `audit_<name>.parquet`. It feeds the tie-out.

**Layer conformance kit** (`testing/layer_conformance.py`): a parallel shock at one date equals `delta + convexity` by full revaluation; a payer and its mirror have equal and opposite value, dv01, gamma and every layer; across a payment date the value drop equals the cash booked; the unexplained share is bounded. Every adapter passes it.

## 8. Strategy layer (gs_quant port, `pricebt.strategy`)

`Strategy(initial_portfolio, triggers, cash_accrual)` and `Trigger(trigger_requirements, actions)` keep gs_quant's shape and positional field orders; `PeriodicTrigger(frequency='1m', actions=...)` also accepts the flat form.

* Triggers: Periodic, IntradayPeriodic, Date, Mkt (level), Crossing (edge), StrategyRisk (measure / layer / pnl / drawdown barrier), Portfolio, TradeCount, Aggregate (ALL/ANY), Not, MeanReversion, Event, Custom/OrdersGenerator, and RiskBand/Rebalance (a measure against a band with hysteresis and cooldown).
* Actions: AddTrade, AddScaledTrade (`size | risk_measure | NAV`), AddWeightedTrade, **Hedge**, Exit / ExitAll, Rebalance, Custom. `trade_duration` grammar: `None | tenor | date | instrument attribute | 'next schedule' | CustomDuration | Timedelta`.
* **Ladder hedge**: `{type: hedge, risk: delta_ladder, target: {"10Y": 1_000_000}, priceables: [<instrument + terms per leg>], min_trade_risk: 100}` measures the book's `delta_ladder` (a vector measure is recognised by its SCHEMA return type, never by a name), solves one quantity per hedge leg by weighted least squares (`ridge`, `bucket_weights`, `risk_percentage`) and trades to close the deviation from the target ladder; `min_trade_risk` drops legs whose own trade risk is below it. `mode: resize` keeps one live position per leg.
* Ordering: each trigger is evaluated once per point in list order; actions run in phase order EXIT < ADD < ADJUST < HEDGE by `(phase, trigger_idx, action_idx)`.
* Signals: `SeriesSource`, `PricerSignal` (a pricer lookup), `MeasureSignal`, `DerivedSignal` (spread/zscore/rolling/clip; named inputs work in YAML), `StepTable`, `ConstantSource`, and the book signals `BookMeasureSignal` (`type: book_measure`, e.g. the book's dollar delta) and `BookLadderSignal` (`type: book_ladder`, one bucket); an engine-owned `SignalStore` with ring buffers. `backtest.record_signals: [names]` records them as `signal_<name>` columns.

## 9. Config

YAML or JSON with the same model. `config/yamlio.py` is a hardened loader (YAML 1.2 scalars, duplicate keys are errors, `${ENV}`, `extends:`, `--set a.b.c=v`). `config/loader.py` builds `Built(grid, market, strategy, settings, signals, instruments)`; errors carry the path and a stable code with did-you-mean. Dotted paths (`package.module:Attr`) are imported only under an allow-listed prefix (`pricebt` + `registry.allow`), and **only a dotted path imports anything**: the text of a config never does. Typed and validated at load, with the path and a stable code: attribution keys and booleans, cadence, `on_error`, `fill_price`, `exit_policy`, `fill_lag`, `initial_capital`, `progress`, measure/layer/audit names against the instruments' schemas AND what their kits bind beyond it (an adapter's own measures and layers), signal names, the baseline's measures, the type of every top-level block, `tieout.tolerances` (keys, numbers, `reason`). `audit: {}` is on.

Top-level keys: `backtest` (tz, calendar, session, grid, initial_capital, fill_lag, fill_price, exit_policy, on_error, progress, attribution, measures, vector_measures, record_signals, audit, cash_accrual), `market` (`mdps` with a `time:` mapping, `pricers` = named roles with `mdp`, `request`, `wrap`), `instruments`, `trades`, `params`, `signals`, `strategy`, `registry`, `outputs`, `tieout` (`tolerances`).

```yaml
instruments:
  usd_sofr_ois:
    asset_class: swap
    factory: "pricebt.contrib.rateslib:swap"       # a Kit: the factory and its default bindings
    conventions: {calendar: usd_fed, spot_lag_days: 2, day_count: act360, ...}     # opaque to pricebt, hashed into the manifest
    bind: {dv01: {target: {method: dv01_zero}, kwargs: {ctx: "@ctx"}}}          # optional: override one binding by name
market:
  mdps: {eod: {type: "pkg.mod:Provider", kwargs: {...}, time: {mode: asof, max_staleness: 12h}}}
  pricers: {primary: {mdp: eod, wrap: "pricebt.contrib.rateslib:wrap"}}
strategy:
  triggers:
    - {type: periodic, frequency: 1m, actions: [{type: add_trade, priceables: {instrument: usd_sofr_ois, terms: {side: receive, maturity: 10Y, notional: 1e7, fixed_rate: par}}, trade_duration: 3m}]}
```

**Stack overlays**: `--stack overlay.yaml` (or `loader.build(cfg, stack=[...])`) may set only `instruments.<n>.factory`, `instruments.<n>.bind` and `market.pricers.<r>.wrap`; anything else is rejected with its path; `base_digest` ignores exactly those keys (ADR 006).

CLI: `pricebt run|validate|describe cfg.yaml [--set k=v] [--stack overlay.yaml] [--out dir] [--tearsheet] [--dry-run]`, and `pricebt tieout cfg.yaml --stack NAME=overlay.yaml ... [--reference NAME] [--out dir]` (exit 0 passed, 1 the tie-out FAILED, 2 any error: a config error or any exception). A config that names a provider outside the package (the fixture-backed ones of `tests/support`) needs that directory on the path: `PYTHONPATH=src;tests`. Python: `pricebt.api.run(path_or_dict)` returns a `BacktestResult`.

## 10. Adapters

An adapter is a package under `contrib/` exposing `swap` and `bond` **Kits**, `wrap`, its shared **conventions blocks** and a `STACK`. It imports its library, checks the conventions block against what it supports (an unsupported value is an error naming the supported set) and provides the layer definitions of section 7.

* **`contrib.rateslib`**: `_compat.py` owns every rateslib global (licence-warning filter, the process-global fixings store, float-curve normalisation); `RLCurvePricer` is a snapshot of a discount-factor curve with published-only fixings; `RLSwap` (strike resolved at par at the fill pricer, `dv01`, `gamma`, `rate`, a bootstrapped `delta_ladder`, the four layers, extension names `dv01_zero`, `gamma_zero`), `RLBond` (treasury price/yield rule, coupon sweep, repo financing, yield-space layers). The golden numbers reproduce exactly.
* **`contrib.quantlib`**: the same kits on QuantLib (`DiscountCurve`, `OvernightIndexedSwap`, `FixedRateBond`), with QuantLib's global evaluation date guarded per call.
* **`testing.refstack`**: the dependency-free reference stack (ADR 007): closed-form schedule, PV, par rate, bootstrapped ladder, layers, treasury bond. It is the known answer of the tie-out.

To add an asset class or a library, follow `skills/README.md` (an ordered playbook with two tested worked examples: `skills/pricebt-wire-external-library/example/` for an in-process library and `example-service/` for a service that prices only from a market uploaded to it). In short: write the factory `(pricer, ts, *, terms, conventions) -> Built(obj, resolved_terms)` and a class whose methods the bindings name, a default bind block, a `wrap` from `SnapshotPricer` to your pricer, and run `testing.layer_conformance.run_kit` on it. To plug in foreign infrastructure whose names do not match (a pricer that owns the method `npv`, a request-dict data source): bind `pricer_method` / `function` targets and wrap the data source in test-side code that emits snapshots; nothing in core changes (`tests/test_arbs_bridge.py` shows an ARBS-shaped stub; the guard toy in `tests/guards/zero_toys.py` shows crossed names).

## 11. Tie-out (`pricebt.tieout`, ADR 006)

`run_tieout(base, {name: [overlays]}, ...)` runs one base under several stacks with the audit trail on, verifies the base is shared (X1), runs the **self-test** (the reference stack twice must differ by exactly zero at every level) and compares each stack with the reference at five levels: **L0** inputs (snapshot digests, resolved terms with dates first, conventions digest), **L1** marks (`pv`, `cash`, `financing`, size per position and point), **L2** measures (`dv01`, `gamma`, `rate`, ladder buckets), **L3** layers (by name, the layer definitions, `reconcile()` of each run, the baseline), **L4** portfolio (equity, cash, the trade ledger's structure and values, summary statistics). Every quantity has a status: `exact`, `noise` (within tolerance), `expected` (a declared definition difference), `info` (nothing to compare, or definitions differ), `exceeds`, `input` (every violating value is at a position or timestamp an L0 difference already explains), `structure`. Tolerances are declared per level, quantity and asset class (`tieout.tolerances` or an argument; an explicit argument replaces every declared key it covers); a wildcard never widens an invariant (a position size); a declaration carries a `reason`, which the report prints, and none is widened without one (`tasks/tolerance_ledger.yaml`). Measures are compared over the UNION of the runs' columns: a ladder bucket that one stack lacks is a failing `structure` row, never an omitted one. `turnover` is its own group (`L4.stats_traded`, absolute below one currency unit: for par swaps it is numerical zero). The shipped absolute floors assume templates sized like the shipped ones (the report prints each floor with its unit); a run of much smaller units declares its own. The report discloses each stack's factory, wrap and effective bindings (a `bind` can carry a unit conversion or a literal argument), and an `input` label is given only where the differing snapshot is in a market role the instrument reads. Measured noise floors are in the ledger and the report: over the real fixtures a financed bond ties out across reference, rateslib and QuantLib to better than 1e-7 relative at every level with the shipped defaults; the swap does too except QuantLib's `unexplained`, whose directional derivatives use a step of 0.1 of the move, making its remainder 0.99 of the reference's (declared with that reason).

## 12. The gs_quant facade

`pricebt.instrument.IRSwap('Pay', '10y', 'USD', ...)` builds **terms** and does nothing else; the spec registered for (asset class, currency) is looked up in the session and its library resolves dates and par when the trade is filled. `PricebtSession.use(market=..., stack="pricebt.contrib.rateslib:STACK")` replaces `GsSession.use()`; `instruments={"swap:USD": name | raw spec}` overrides one default. gs decimals become pricebt percent in the facade only; the gs field vocabulary is in one place (`backtests/gs_fields.py`); the gs `1 + r/365` cash accrual lives in `backtests/`. `IRSwaption` and every other gs instrument raise `NotSupportedError` (ADR 009).

## 13. Results (`pricebt.results`)

`BacktestResult` wraps the engine's `RunRecord`: `equity` (equity, cash, tcost, positions_value, n_positions, step_pnl, `layer_<name>`, `measure_<name>`, `signal_<name>`), `positions`, `trades` (with `instrument` and `term_<name>` = the resolved terms), `closed_trades`, `orders`, `errors`, `events`, `layers`, `vectors` (recorded vector measures, one column per bucket), `manifest` (settings, counts, config hash, per instrument the asset class, factory, conventions and `<id>@<version>` per layer). `summary_stats()` derives the annualisation from the grid; `attribution(by=layer|baseline|component|day|position|template|action|kind|tag)`; `reconcile()` checks the ledger identity on every row, the cash and cost roll-forwards, per-position ledgers, layers against total (library layers and baseline layers separately); `tearsheet(path)` renders PNG/PDF and a self-contained HTML; `to_parquet/from_parquet`; `compare({...})`; `build_report(...)`.

## 14. Testing and verification

* `tests/guards/` (ADR 008): the import blocker (`sys.meta_path`), AST and scan checks for banned imports, tokens, schema keys, shipped configs, conventions and ARBS; the semantic guards on a toy whose method names cross the schema's; a non-vacuity twin for every guard; the partition collection hook.
* Markers (spec G-5): `core` (toys and the reference stack only; must pass with rateslib, QuantLib, ARBS and gs_quant unavailable: `pytest -m core`, and `python tests/guards/blocker.py -m core` with the libraries made unimportable), `adapter_rateslib`, `adapter_quantlib`, `fixtures` (needs `data/fixtures`; skips with a reason when absent), `live_arbs` (opt-in, `PRICEBT_LIVE_ARBS=1`). An unmarked test is an error at collection.
* The adapters are tested against independent identities (the layer kit), the golden numbers, the reference stack, and each other (`tests/test_quantlib_tieout.py`). `tools/mutcheck.py <spec.json>` applies one textual mutation, expects the named tests to fail and restores the file; survivors are killed or recorded as equivalent (change log section 6). `tools/floor_check.py` checks that every pre-refactor test is still collected or named in the change log.
* Real-data suites (`configs/suite/`) and their reproduction of the recorded results are in `tasks/suite_reproduction.md`; the showcase notebook `notebooks/showcase_swap_book.ipynb` runs top to bottom on the fixtures and carries its own assertion cells.

## 15. Limitations (honest list)

* Layers are off by default (a full swap decomposition costs tens of milliseconds per position per cadence point); intraday carry/roll lump at the reference-date flip because store curves have a date-only clock (`time_accrual: lump`); `linear` pro-rating is not implemented.
* Swaptions and volatility are out of scope (`IRSwaption` raises `NotSupportedError`). STIR/bond futures: a toy variation-margin future only.
* A ladder hedge with a hedge set that does not span the book's buckets leaves a residual in the buckets it cannot reach (aged positions drift into neighbouring buckets); the showcase reports the residual and asserts the target buckets only.
* Bond aliases beyond `CT<n>`/`O<n>`/`OO<n>`/`OOO<n>`/CUSIP (`MMYY`, CTD) are not implemented.
* Pre-start signal warm-up for pricer-backed windowed signals is not implemented (`min_periods` handles cold starts); `on_error='record'` covers marking/execution errors but not exceptions raised inside triggers/actions.
* The tie-out compares the buckets a stack reports; per-bucket ladder noise is dominated by each library's solver tolerance (measured in the ledger).
