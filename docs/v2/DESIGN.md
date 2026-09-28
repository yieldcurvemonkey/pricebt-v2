# pricebt v2: requirements and design

Status: **design (revision 2, after an adversarial review).** Implementation may start once the user signs off the decisions in §0.
Branch: `v2-redesign`, in the worktree `C:\Users\chris\clee\gsquant-temp-claude\pricebt-v2`.
The v1 tree is preserved at git tag `v1-final` (branch `main`). Nothing from v1 needs to be kept "just in case"; it is all recoverable.

Companion documents:

| File | What it is |
|---|---|
| `docs/v2/README.md` | Entry point and reading order for the implementer |
| `docs/v2/DESIGN.md` | This file: requirements, architecture, contracts |
| `docs/v2/IMPLEMENTATION_PLAN.md` | Phased task list, file ownership, acceptance commands, gates |
| `docs/v2/research/01..08-*.md` | Evidence: exact gs_quant signatures, engine pseudocode, result shapes, the ARBS API, the v1 inventory. **Cited by section throughout. Read the cited section before implementing what it describes.** |

Notation:
- **MUST**: a requirement. Changing it needs the user's approval.
- **SHOULD**: a strong default. Deviate only with a written reason in `docs/v2/DEVIATIONS.md`.
- `R02§4` means `research/02-gs-actions-and-generic-engine.md` section 4; `R01`..`R08` refer to the eight research notes the same way.
- "gs" means gs_quant. "1.5.4" is the installed gs_quant (`C:\Users\chris\anaconda3\Lib\site-packages\gs_quant`). "2.1.17" is the newer repo checkout (`C:\Users\chris\clee\gsquant-temp-claude\gs-quant\gs_quant`). File:line references to gs code are to 2.1.17 unless marked "1.5.4".

---

## 0. Decisions the user must confirm before hand-off

**STATUS: ALL CONFIRMED by the user on 2026-09-27. The defaults below are final.** On 0.1 the user added: *"port the logic and design of gs_quant, do not integrate it"*. pricebt re-implements the open-source gs code and has no runtime dependency on gs_quant. The installed gs_quant 1.5.4 is used only by the offline dev tool `tools/gs_api_snapshot.py`, which introspects signatures for the parity test. No pricebt module and no test imports gs_quant.

| # | Decision | Default (assumed by the plan) | Why | Alternative |
|---|---|---|---|---|
| 0.1 | **Which gs version is the reference** | **Public API signatures = 1.5.4** (the version the user named). **Behaviour = 2.1.17** wherever 1.5.4 crashes or is buggy. **2.1.17's additive features are included**: `AddWeightedTradeAction`, `EarlyExitPositionLimitScaledAction`, `BackTest.summary_stats()`, `BackTest.fx_pnl_definition()`, `AddScaledTradeAction.dated_priceables`, and the trailing `EventTriggerRequirements` fields. **The port source code is 2.1.17.** | 1.5.4 cannot run the reference notebook 040304: every `MeanReversionTrigger` fire raises `TypeError` (R01§6.5.9, R02§7 Q7). 2.1.17's signatures are a superset of 1.5.4's. The only change to an existing signature is `EventTriggerRequirements`, whose new fields are *appended*, so 1.5.4 positional calls still work (R01§10). | Strict 1.5.4 (then 040304 cannot run) |
| 0.2 | **The ARBS Eris source for the example** | `IRSwapsMDP(source="ERIS_EOD_LIVE-RL_BASIC")`, read **store-only** through `bulk_get_data(..., ignore_cache_miss=True)`, for dates up to **2026-08-20**. This means no network access and no curve-store writes; the only side effect is an empty, today-dated folder that ARBS creates in its fixings cache. Coverage is 2020-07-01..2026-08-20, dense from 2021-01-04 (R06§1.3, §1.4b). | The source the user wrote, `"ERIS_EOD_LIVE-RL_BASIC-NOJUMPS"`, **never reads the local curve store**; only 26 dates are cached. For every other date it downloads from files.erisfutures.com, turns TLS verification off for the whole process, and **overwrites the shared curve-store partition** `USD-SOFR-1D/date=<d>` (R06§1.3, §6.1). Separately, ARBS downloads SOFR fixings (NY Fed / FRED) for any date whose previous business day is missing from its fixings cache (newest cached fixing: 2026-09-04). The config therefore returns `None` for dates after 2026-08-20 (Appendix A, `_LAST_SAFE`). | NOJUMPS after a network warm-up that the user authorises, which clobbers store partitions. The example config keeps NOJUMPS as a commented-out line. |
| 0.3 | **The deviation list (§11)** | Accept every row of §11. Each row fixes a crash, a look-ahead, a silently wrong number, or a dependency on GS infrastructure. | "Nearly 1:1" means exact parity on well-formed inputs, without reproducing gs defects. | Strict gs parity row by row: say which rows to drop |
| 0.4 | What happens when a grid date has no market data (holiday gap, store hole, or `end=today` beyond the last stored day) | `missing_market='drop'`, set on `PricebtSession`. Grid dates are removed with a `UserWarning` and listed on `backtest.missing_market_dates`. Valuation dates outside the grid (exits, `next schedule`) that lack a market are rolled to the next kept grid date (§9.5, DEV-E16). If *every* grid date would be dropped, pricebt raises instead. | gs silently prices every weekday on server data. Without this, `end_date = datetime.today().date()` in the notebooks would fail after 2026-08-20. | `'raise'` |
| 0.5 | Currency of a currency-bearing risk measure that has **no `currency` parameter** (e.g. `IRDelta(aggregation_level='Type')`) | **The function's own currency**: its `currency:`, else the asset currency. `Price` follows the same rule. | This matches the notebook comments ("results will be in local ccy", R05§7.2) and avoids hidden FX conversion. | USD, following the gs `IRDelta` docstring |
| 0.6 | Wording of MUST-5 ("adding an asset = config only") | Every gs instrument class that pricebt mirrors is **generated data** (`tools/gen_gs_fields.py` reads a gs 1.5.4 snapshot). Adding a gs class later means adding its name to the generator's list and regenerating. That is a data change, with no hand-written code. v2 generates 7 classes. | The earlier text allowed "a ≤10-line field list" written by hand. Generating from the snapshot removes hand-transcription errors in enum coercion tags (review F04). | Hand-written field lists |

---

## 1. What pricebt v2 is

pricebt v2 is an **event-driven backtester whose public API is a near 1:1 port of `gs_quant.backtests`**: Strategy, Triggers, Actions, GenericEngine, and the BackTest results. A gs backtest notebook ports with three edits:
1. change `gs_quant` to `pricebt` in its imports;
2. replace `GsSession.use(...)` with `PricebtSession.use(assets=[...])`;
3. replace any GS `Dataset` call with a local series.

**pricebt itself contains no pricing and no market data.** Every number (market objects, trade resolution, PV, risk, FX) comes from **one self-contained YAML asset config per tradable asset**. The config holds Python import lines, a block of helper code, and **Python expression strings**. pricebt evaluates those strings with injected variables such as `pricebt_date`, `pricebt_timestamp`, `market`, `kwargs` and `trade`. An external library (the user's ARBS, or anything else) is named **only inside those config files**, never in pricebt's source.

---

## 2. Requirements

### 2.1 The five MUSTs (re-read these before every phase)

> **MUST-1: No dependency on any external pricing or market-data library.**
> Nothing under `src/pricebt/` may import, name, special-case, or assume the shape of **any** pricing library or market-data infrastructure. That includes ARBS (`MDP`, `Query`, `Caching`, `IRSwapsMDP`, `bulk_get_data`, …), rateslib, QuantLib, gs_quant, Bloomberg, or anything else. The allowed runtime dependencies are the **stdlib, numpy, pandas, PyYAML, python-dateutil and tqdm**; `dataclasses_json` is not allowed either.
> pricebt calls strings from config files and does not know what they compute. The **only** exception is attribution: the gs_quant name may appear in comments, docstrings, license headers and `NOTICE`, because ported files keep their Apache-2.0 headers. **Tests enforce this rule** (§12.2).
> ARBS may appear only in `configs/assets/*.yaml`, `notebooks/`, `docs/`, and tests marked `live_arbs`.

> **MUST-2: The backtest API and capabilities are near 1:1 with gs_quant.**
> - **Same module paths:** `pricebt.backtests.triggers`, `.actions`, `.generic_engine`, `.strategy`, `.data_sources`, `.backtest_objects`, `.backtest_utils`, `.core`, and `pricebt.instrument`, `pricebt.risk`, `pricebt.common`, `pricebt.markets`, `pricebt.markets.portfolio`, `pricebt.datetime`, `pricebt.session`, `pricebt.data`.
> - **Same class names, and the same constructor parameter names, order and defaults.** Dataclass field order matters, because notebooks construct objects positionally. Instrument constructors have **real signatures identical to gs's** (§5.1).
> - **Same enum members and values.**
> - **Same result object API.** `result_summary` and `risk_summary` are *properties*; `trade_ledger()`, `strategy_as_time_series()`, `pnl_explain()` and `summary_stats()` are *methods*. Table shapes and column names are unchanged.
> - **Same trade-naming scheme and the same engine semantics.**
>
> Differences are allowed **only** where §11 lists them. A machine check compares pricebt against a JSON snapshot of 1.5.4's signatures (§12.3).

> **MUST-3: One self-contained config file per asset.**
> Everything pricebt needs to trade an asset lives in one YAML file:
> - which gs instrument class it serves, the matching rule and the currency;
> - the import lines and a block of helper code;
> - the market expression;
> - trade resolution and construction;
> - per-trade pricing functions, with units;
> - portfolio functions, e.g. a delta ladder over a collection of trades;
> - attribute reads;
> - the mapping from gs risk measures to functions.
>
> **pricebt never interprets instrument kwargs.** It passes them to the config verbatim, apart from gs enum coercion (§5.2).

> **MUST-4: Multi-currency, and bp units.**
> Every function has a currency: its own `currency:` or the asset's. Values are converted to another currency **only** through an FX config, which uses the same expression mechanism (§4.5). A conversion happens only when a risk measure carries a `currency` parameter or `run_backtest(result_ccy=...)` is set. Rate-sensitivity risk is in **currency per 1bp**. Rate-like measures reported by the shipped configs are in **bp**. Strategy inputs in bp (e.g. `fixed_rate='ATM+25'`) pass through untouched. The result object gains a **bp-normalised P&L view**, `BackTest.pnl_bps(risk)` (§8.4).

> **MUST-5: Adding an asset means writing a config, not code.**
> A later asset type (the next planned one is **swaptions**) must be addable by writing a new YAML asset config. If gs has an instrument class pricebt does not yet mirror, the class is added as generated data (decision 0.6). The engine, the pricing layer and the result objects MUST NOT special-case any asset class. §13 walks through the swaption case as the design test, and a toy swaption runs in CI (§12.4).

### 2.2 SHOULDs

- S1. Ported files keep gs's structure, function names and comments, so a reader can diff pricebt against gs 2.1.17 file by file.
- S2. Results are deterministic: two runs of the same strategy give identical frames. No column order comes from a set (DEV-E14), and trigger state is reset per run (DEV-T10).
- S3. Errors name the asset, the config key, the expression text and the pricing date (§6.6).
- S4. Performance: each market is evaluated once per (market key, date, csa), and each unit measure once per its cache key (§6.3). A 5-year daily backtest of a few hundred ARBS swaps should take minutes, not hours.
- S5. The examples are runnable: the converted 040304 notebook runs in CI on the toy library and, opt-in, on ARBS.

### 2.3 Non-goals (not in v2)

- Serialising strategies or results to JSON (`dataclass_json`, `to_json`, `from_dict` round trips of strategies). `Instrument.to_dict()` and `as_dict()` are kept, because the engine uses them.
- `PredefinedAssetEngine`, `EquityVolEngine`, `StrategySystematic`, and the modules `order.py`, `event.py`, `data_handler.py`, `execution_engine.py` and `OrdersGeneratorTrigger`. Their importable names exist and **raise `NotSupportedError`** when constructed (§9.3).
- `GsDataSource`, `Dataset`, `OisFixingCashAccrualModel`, and the GS event calendar. These also exist and raise `NotSupportedError`, with a message pointing to the local replacement.
- P&L attribution layers, tie-out, reference stacks, snapshots, bindings and schemas: all the v1 machinery is **stripped**.
- A ladder-hedge *action*, i.e. hedging a vector risk with several instruments. The ladder *measure* is in scope; hedging with it is a later item (§14).
- Coupon cash between marks (gs has the same property; §14).
- Parallel or multi-process pricing.

---

## 3. Architecture

### 3.1 Layering

```
 user notebook / script (gs-style code)
        │  from pricebt.backtests... import ...     PricebtSession.use(assets=[...yaml], fx=...yaml)
        ▼
 pricebt.backtests.*        ← PORT of gs 2.1.17 backtests (strategy, triggers, actions, generic_engine,
        │                      generic_engine_action_impls, backtest_objects, backtest_utils, data_sources, core)
        │  calls ONLY gs-shaped local objects:
        ▼
 pricebt.markets (PricingContext, HistoricalPricingContext)     pricebt.markets.portfolio.Portfolio
 pricebt.instrument (Instrument + generated gs classes IRSwap, IRSwaption, ..., ConfigInstrument)
 pricebt.risk (RiskMeasure objects) + pricebt.risk.results (FloatWithInfo, DataFrameWithInfo, PortfolioRiskResult, PricingFuture, LazyFuture)
        │  every value request goes to ONE place:
        ▼
 pricebt.assets.pricing.PricingService   ← caches; FX; risk-measure → function mapping; units; resolution
        │
        ▼
 pricebt.assets (AssetConfig loader + validator, AssetNamespace = exec(imports, code) + eval(expressions))
        │  eval() of strings written by the user
        ▼
 external library (ARBS, …): reachable ONLY through the config's own import lines
```

**The central idea.** The gs engine code talks to `PricingContext`, `HistoricalPricingContext`, `Portfolio.calc`, `Portfolio.resolve`, `Instrument.resolve/scale/clone` and `PortfolioRiskResult`. pricebt re-implements **exactly that surface** locally, backed by the `PricingService`. Values are computed eagerly and returned as already-completed futures, except the per-instrument values of bucketed measures, which are lazy (§8.2). The ported engine files then need **only their imports changed**, plus the deviations listed in §11. The engine never learns that asset configs exist.

### 3.2 Package layout (target state after Phases 0–5)

```
pricebt-v2/
  pyproject.toml                  deps: numpy, pandas>=2, pyyaml, python-dateutil, tqdm; extra "test": pytest, nbformat, nbclient, ipykernel
  pytest.ini                      markers: core, notebook, live_arbs; addopts empty; testpaths = tests
  NOTICE                          Apache-2.0 attribution for code ported from gs_quant
  AGENTS.md                       points to docs/v2/README.md
  README.md                       user-facing: what pricebt is, quick start, asset config primer
  configs/
    assets/usd_sofr_ois_interest_rate_swap.yaml     ARBS example (Appendix A)
    fx/README.md                                    how to write an FX config
  docs/v2/                         DESIGN.md, IMPLEMENTATION_PLAN.md, README.md, DEVIATIONS.md, ASSET_CONFIG_GUIDE.md, research/
  notebooks/
    src/*.py                                  percent-format sources built by tools/nb_build.py
    040304_mean_reversion_toy.ipynb           CI version (toy asset)
    040304_mean_reversion_usd_sofr_arbs.ipynb opt-in version (ARBS)
  src/pricebt/
    __init__.py                   __version__ = "2.0.0"; imports nothing eagerly except errors
    errors.py                     PricebtError, ConfigError, AssetEvaluationError, MarketDataUnavailable, NotSupportedError
    base.py                       EnumBase, Priceable marker, static_field, field_metadata, exclude_none, get_enum_value
    common.py                     gs enums (see P1.1); RiskMeasure & ParameterisedRiskMeasure re-exported via module __getattr__
    progress.py                   salvaged v1 notebook-safe tqdm bar
    datetime/__init__.py          business_day_offset, is_business_day, prev_business_date, date_range, business_day_count, today
    datetime/relative_date.py     RelativeDate, RelativeDateSchedule
    risk/__init__.py              risk-measure classes and instances (§8.1) + re-exports of FloatWithInfo, SeriesWithInfo,
                                  DataFrameWithInfo, ErrorValue from risk.results
    risk/results.py               RiskKey, ResultInfo, FloatWithInfo, SeriesWithInfo, DataFrameWithInfo, ErrorValue,
                                  MultipleRiskMeasureResult, PricingFuture, LazyFuture, PortfolioRiskResult
    risk/transform.py             Transformer (base), ResultWithInfoAggregator
    markets/__init__.py           PricingContext, HistoricalPricingContext, and the seams _engine_calc / _engine_resolve
    markets/portfolio.py          Portfolio
    instrument/__init__.py        Instrument, ConfigInstrument, instrument_identity, and the generated gs classes
                                  (IRSwap, IRSwaption, FXOption, FXForward, EqOption, InflationSwap, Cash); re-exports
                                  OptionStyle, OptionType, Currency, PayReceive, BuySell, SwapClearingHouse, SwapSettlement
    instrument/_gs_fields.py      GENERATED by tools/gen_gs_fields.py from gs 1.5.4 (do not edit)
    assets/__init__.py            load_asset, load_fx, AssetConfig, FxConfig
    assets/yamlio.py              salvaged v1 hardened YAML loader (duplicate keys are errors; NO ${ENV} interpolation)
    assets/config.py              AssetConfig/FunctionSpec/RiskMapping/FxConfig dataclasses, schema validation, compile
    assets/namespace.py           AssetNamespace: exec(imports)+exec(code) once; eval(compiled expr, injected vars)
    assets/fx.py                  FxConfig loading and FX evaluation
    assets/registry.py            AssetRegistry: name → config; instrument → config matching; market-key sharing rules
    assets/pricing.py             PricingService (the only place values are produced)
    session.py                    PricebtSession, GsSession (no-op shim with the gs signature), Environment
    data/__init__.py              DataFrequency, Dataset (stub), measure_series (pricebt extension)
    target/common.py, target/measures.py, target/backtests.py   thin re-export shims (gs import paths used by notebooks)
    backtests/__init__.py         gs re-exports (core names, StrategySystematic stub, DeltaHedgeParameters stub)
    backtests/core.py             TradeInMethod, MarketModel, TimeWindow, ValuationFixingType, ValuationMethod,
                                  BacktestTradingQuantityType, DeltaHedgeParameters (stub); Backtest/BacktestResult (stubs)
    backtests/backtest_utils.py   CalcType, CustomDuration, make_list, get_final_date (+clear_final_date_cache), interpolate_signal,
                                  scale_trade, map_ccy_name_to_ccy
    backtests/data_sources.py     MissingDataStrategy, DataSource, GenericDataSource (fixed), GsDataSource (stub), DataManager (stub)
    backtests/backtest_objects.py BackTest (+pnl_bps, missing_market_dates), ScalingPortfolio, WeightedScalingPortfolio,
                                  transaction models, TransactionCostEntry, CashPayment, Hedge, WeightedTrade, cash accrual models,
                                  PnlAttribute/PnlDefinition, fx_pnl_definition, PredefinedAssetBacktest (stub),
                                  OisFixingCashAccrualModel (stub), _BACKTEST_END ContextVar
    backtests/actions.py          all actions + *ActionInfo (2.1.17)
    backtests/action_handler.py, backtest_engine.py
    backtests/triggers.py         all triggers/requirements (2.1.17 behaviour + §11 fixes)
    backtests/strategy.py         Strategy
    backtests/generic_engine_action_impls.py   2.1.17 action impls (port)
    backtests/generic_engine.py   GenericEngine (port) + missing-market handling + progress bar
    backtests/{equity_vol_engine,predefined_asset_engine,strategy_systematic,order,event,data_handler,execution_engine}.py   stubs
  tests/
    conftest.py                   markers; autouse isolation fixture (sessions, action counter, caches, toy recorders)
    guards/                       zero-dependency, asset-agnostic, import-order and skeleton guards
    toylib/                       tiny closed-form "library" used ONLY by tests (never shipped): rates.py, swaption.py
    assets/                       toy asset configs: toy_usd_irs.yaml, toy_eur_irs.yaml, toy_usd_swaption.yaml, toy_fx.yaml
    data/gs_api_1_5_4.json        signature snapshot (tools/gs_api_snapshot.py)
    data/gs_instruments_1_5_4.json  instrument field snapshot (same tool)
    data/gs_api_exceptions.yaml   documented exceptions to the snapshot, each with a reason
    test_*.py
  tools/
    gs_api_snapshot.py            run with the BASE anaconda python (gs_quant 1.5.4)
    gen_gs_fields.py              run with the stir python; writes src/pricebt/instrument/_gs_fields.py
    nb_build.py, mutcheck.py      salvaged v1 tools
```

**Import DAG (MUST).** Top-level imports only go downward in this order:
1. `errors` → `base` → `common`. `common` exposes the `RiskMeasure` re-export through a module `__getattr__`, not at import time.
2. → `datetime` → `risk` (measures) → `risk.results` → `risk.transform`. `transform` imports the result classes; `risk.results` MUST NOT import `transform`, and `PortfolioRiskResult.transform` duck-types `risk_transformation.apply(...)`.
3. → `markets`. It MUST NOT import `portfolio`, `instrument`, `session` or `assets` at top level. `markets` only defines the contexts and the two seam functions, whose bodies import `pricebt.assets.pricing` at call time (§6.2).
4. → `instrument`. It imports `session` and `markets` only inside method bodies.
5. → `markets.portfolio` → `assets.{yamlio, config, namespace, fx, registry}` → `assets.pricing` → `session` → `data` → `backtests.*`.

`risk.results` dispatches on `isinstance(x, pricebt.base.Priceable)` and duck typing (`.all_instruments`, `.paths`). It never imports `instrument` or `portfolio`. A guard test imports every module in a fresh subprocess, in reverse DAG order (§12.2).

### 3.3 Runtime environment

| What | Value |
|---|---|
| Python for pricebt and ARBS | `C:\Users\chris\anaconda3\envs\stir\python.exe` (3.13.5, pandas 2.3.1, numpy 2.2.6, tqdm 4.67.1); ARBS also runs here (R06§1.1) |
| Python for the gs snapshot **only** | `C:\Users\chris\anaconda3\python.exe` (base; gs_quant **1.5.4**). The `stir` env has gs_quant 1.4.26, **which is not the reference**. |
| Run tests | from `C:\Users\chris\clee\gsquant-temp-claude\pricebt-v2`: `$env:PYTHONPATH="src;tests"; C:\Users\chris\anaconda3\envs\stir\python.exe -m pytest tests -o addopts= -p no:cacheprovider` |
| gs source to port (read-only) | `C:\Users\chris\clee\gsquant-temp-claude\gs-quant\gs_quant\backtests\*.py` (2.1.17) |
| gs reference install (read-only) | `C:\Users\chris\anaconda3\Lib\site-packages\gs_quant` (1.5.4) |
| ARBS (read-only; NEVER import it from `src/` or from normal tests) | `C:\Users\chris\clee\ARBS` |

---

## 4. The asset config: the user-facing contract

### 4.1 File format

- YAML, loaded by `pricebt.assets.yamlio`. A duplicate key is an error. `${ENV}` interpolation is **off**, so expression strings are never rewritten.
- One asset per file.
- An unknown key at any level is a `ConfigError` with a did-you-mean suggestion (`difflib.get_close_matches`).
- All expressions are **compiled at load time** with `compile(src, f"<asset {name}:{key}>", "eval")`. A syntax error fails the load and names the asset, the key and the line.
- `imports`/`code` are compiled with mode `"exec"` at load but **executed lazily** (§4.4).

### 4.2 Schema

```yaml
schema_version: 1                     # required, must be 1
asset: usd_sofr_ois_interest_rate_swap  # unique id; defaults to the file stem
description: "..."                    # optional
instrument: IRSwap                    # required: a class name in pricebt.instrument (a generated gs class, or ConfigInstrument)
match: {notional_currency: USD}       # optional: equality rules on kwargs (after defaults) to select this asset (§5.3)
currency: USD                         # required ISO-4217: the default currency of every ccy-unit function
defaults:                             # optional: fill kwargs that are absent/None, at resolve time; enum fields coerced (§5.2)
  pay_or_receive: Receive
  termination_date: 10y
  notional_currency: USD
  fixed_rate: ATM
  notional_amount: 1.0e6
imports: |                            # optional Python SOURCE, exec'd once (lazily) into this asset's private namespace
  import os, sys
code: |                               # optional Python SOURCE, exec'd once after imports, same namespace
  def helper(m): ...
market:
  expr: 'load_market(pricebt_date)'   # required: evaluated once per (market key, date, csa); None = unavailable
  key: usd_sofr_eris_rlbasic          # optional sharing key (default: the asset name), see §4.4 "Market sharing"
resolve:
  expr: 'resolve_swap(market, kwargs)'   # optional: (market at the TRADE date, kwargs) -> dict of plain resolved terms
trade:
  expr: 'build_swap(market, resolved)'   # optional: (market, resolved) -> the library's trade object (default: resolved)
  build_on: each_market               # each_market (default) | resolve_date   (§6.4)
functions:                            # required: per-trade functions, evaluated for ONE unit (quantity 1)
  npv:      {expr: 'market.npv(trade)', unit: ccy}
  dv01:     {expr: 'market.pv01(trade)', unit: ccy_per_bp}
  par_rate: {expr: '...', unit: bp}
  #  optional per entry: currency: EUR          (literal ISO code; default = asset currency)
  #                      scale_with_quantity: true|false   (default by unit, §5.4)
portfolio_functions:                  # optional: functions over a COLLECTION of this asset's trades on one market
  delta_ladder:
    expr: 'delta_ladder(market, trades, weights, ("2Y","5Y","10Y","30Y"))'
    unit: ccy_per_bp
    returns: buckets                  # buckets -> dict[str, float] | scalar -> float
    labels: {mkt_type: IR, mkt_asset: USD-SOFR-1D, mkt_class: OIS}   # optional: any of mkt_type, mkt_asset, mkt_class,
                                      #   mkt_quoting_style; a missing label is ''. Bucket keys are str mkt_point labels,
                                      #   never parsed by pricebt; a multi-dimensional point is one ';'-joined str ("1y;10y").
    #  optional: currency (literal), scale_with_quantity
attributes:                           # optional: values the gs engine reads with getattr() on a RESOLVED instrument
  termination_date: 'resolved["termination_date"]'
  notional_amount:  'abs(resolved["notional"]) * pricebt_quantity'   # MUST be linear and signed in quantity (§9.4)
size_attribute: notional_amount       # optional: the attribute RebalanceAction(size_parameter=...) may name
risk_measures:                        # required: gs risk-measure NAME -> function (see §8.1 for the rules)
  Price: npv                          #   Price is REQUIRED (the engine books cash with it)
  IRDeltaParallel: dv01
  IRDelta: {scalar: dv01, bucketed: delta_ladder}
  IRFwdRate: par_rate
```

**Units** (`unit:`). This is the closed set pricebt understands:

| unit | meaning | extensive: × quantity by default (§5.4) | FX-converted by a `currency` parameter / `result_ccy` | FloatWithInfo `.unit` |
|---|---|---|---|---|
| `ccy` | amount in the function's currency | yes | yes | `{"<CCY>": 1}` |
| `ccy_per_bp` | currency per +1bp | yes | yes | `{"<CCY>": 1}` (gs convention) |
| `ccy_per_bp2` | currency per bp² | yes | yes | `{"<CCY>": 1}` |
| `bp` | basis points | **no** | no | `{"bp": 1}` |
| `pct` | percent | **no** | no | `{"pct": 1}` |
| `decimal` | plain rate (0.0425) | **no** | no | `{"decimal": 1}` |
| `number` | dimensionless | yes | no | `{}` |
| `date` | a date (attributes only) | no | no | n/a |

Requesting a currency conversion of a non-currency unit raises `ConfigError("measure X has unit bp; it cannot be converted to EUR")`.

### 4.3 Injected variables (the ONLY names pricebt puts into an evaluation)

| Name | Type | Available in | Meaning |
|---|---|---|---|
| `pricebt_date` | `datetime.date` | all | the pricing date. In `resolve`, this is the trade/resolution date. In `attributes`, it is the instrument's resolution date. |
| `pricebt_timestamp` | tz-aware `pandas.Timestamp` | all | `pricebt_date` at the session's EOD time in the session timezone (default `17:00` `America/New_York`) |
| `pricebt_datetime` | naive `datetime.datetime` | all | `pricebt_timestamp` without tz |
| `pricebt_csa` | `str` or `None` | all | the CSA in force: `HedgeAction.csa_term` while a hedge is being resolved, else `run_backtest(csa_term=...)`, else `None`. Opaque to pricebt. |
| `pricebt_asset` | `str` | all **except `market.expr`** | the asset name |
| `pricebt_currency` | `str` | all **except `market.expr`** | the asset currency |
| `market` | the value `market.expr` returned | resolve, trade, functions, portfolio_functions, attributes | the market object for (key, date, csa) |
| `kwargs` | `dict` (a fresh copy) | resolve (and trade/functions if `resolve` is absent) | the instrument's kwargs after `defaults`, exactly as the user wrote them apart from gs enum coercion (§5.2) |
| `resolved` | `dict` (a fresh copy) | trade, functions, attributes | the output of `resolve` (plain data) |
| `trade` | the value `trade.expr` returned | functions | the library trade object for ONE unit |
| `trades`, `weights` | `list`, `list[float]` | portfolio_functions | trade objects of this asset on this market, and their weights (same length and order) |
| `pricebt_quantity` | `float` | attributes | the instrument's signed quantity multiplier (§5.4) |
| `base`, `quote` | `str` | FX config `rate` only | ISO codes |

`market.expr` receives only `pricebt_date`, `pricebt_timestamp`, `pricebt_datetime` and `pricebt_csa`. Names defined by `imports` and `code` are visible to every expression of that asset. Injected names shadow config names of the same spelling, so do not define helpers named `market`, `trade`, `kwargs`, and so on.

**Attribute evaluation date.** `attributes` expressions are evaluated only on a **resolved** instrument, with `pricebt_date` = its resolution date, `pricebt_csa` = its resolution csa, and `market` = that date's market. The market is injected only if the compiled expression's `co_names` contain `market`, so a market is never fetched needlessly. The result is cached per (asset, frozen resolved terms, field, quantity). On an unresolved instrument, attributes are not evaluated, and `__getattr__` falls back to `kwargs` (§5.1).

### 4.4 Evaluation rules

- **Lazy execution.** `imports` and then `code` run **lazily, once per process per asset**, on the first evaluation of any of that asset's expressions. They never run at load time, so loading a config never imports the external library. If `imports` or `code` raises, the error is wrapped as `AssetEvaluationError` (key `imports` or `code`), cached, and re-raised on every later use without re-executing.
- **Evaluation.** Each expression is evaluated with `eval(code_obj, namespace_globals, injected_locals)`. `namespace_globals` is the asset's private dict (it contains `__builtins__`), and `injected_locals` is a fresh dict per call. Asset namespaces are **isolated**: two assets never see each other's helpers.
- **Market.** `None` means "no market for this date". Any exception is an error: it is wrapped, not swallowed. A config that wants "exception means unavailable" catches the exception in its `code` helper and returns `None` (the ARBS config does this for US holidays, where ARBS raises; see Appendix A). A market with several parts (curve + vol cube) is returned as one object (a tuple, dict or `SimpleNamespace` built in `code`), and MUST be `None` if any required part is missing.
- **Market sharing.** Assets with the same `market.key` MUST have byte-identical `imports`, `code` and `market.expr`. This is checked at registration: `ConfigError("market key K: <field> differs between assets A and B")`. A shared market is evaluated in the namespace of the **first-registered** asset with that key, once per (key, date, csa). A different asset that merely wants the same curve (e.g. a swaption) uses its own key.
- **Resolve.** `resolve` must return a `dict` whose values are hashable plain data: `str`, `int`, `float`, `bool`, `None`, `date`, `datetime`, `pandas.Timestamp`, or a `tuple` of those. The result is validated (`validate_resolved`), and anything else is a `ConfigError`. When `resolve` is absent, `kwargs` (after defaults) goes through the same validation, so it can be used as a cache key. **Resolve MUST pin every relative term at the trade date**: tenors become absolute dates, and `'ATM'` becomes a number. A `'10y'` left unpinned is re-read as a rolling 10y on every later date and silently corrupts P&L (test `test_resolve_pins_dates`). In `resolve`, `pricebt_date` is the trade date. A config MAY pin other trade-date facts (e.g. `entry_price` for a margined future).
- **Functions.** Functions return a `float`; an `int` is converted. `NaN` is allowed and propagates. Portfolio functions with `returns: buckets` return `dict[str, float]`.
- **Security.** Configs are **trusted code**: they `exec`. Say so in `ASSET_CONFIG_GUIDE.md`. pricebt loads configs only from paths the user gives (`PricebtSession.use(assets=...)`, `load_asset(...)`).

### 4.5 FX config

```yaml
schema_version: 1
fx: user_fx_table                          # required id
imports: |
  import pandas as pd
code: |
  _T = pd.read_csv(r"C:\data\fx_fixings.csv", index_col=0, parse_dates=True)    # columns like EURUSD
  def rate(base, quote, d):
      col, inv = f"{base}{quote}", f"{quote}{base}"
      if col in _T: return float(_T[col].asof(pd.Timestamp(d)))
      if inv in _T: return 1.0 / float(_T[inv].asof(pd.Timestamp(d)))
      return None
rate: 'rate(base, quote, pricebt_date)'    # required: QUOTE currency units per 1 BASE unit; None = unavailable
```

- `PricingService.fx(from_ccy, to_ccy, date)` returns `1.0` when the two currencies are equal, without evaluating anything. Otherwise it returns the cached evaluation of `rate` with `base=from_ccy`, `quote=to_ccy`.
- A `None` or non-positive result raises `MarketDataUnavailable(f"no FX {from}{to} on {date}")`.
- Converting an amount `x` from currency `A` to currency `B` gives `x * fx(A, B, d)`, where `d` is the pricing date of the value being converted.
- ARBS has no offline FX history (R06§5), so real runs read a user file. Tests use `tests/assets/toy_fx.yaml`.

---

## 5. Instruments (the gs facade objects): `pricebt.instrument`

### 5.1 Class shape and object contract

`_gs_fields.py` is **generated** (decision 0.6). For each mirrored gs class it holds:
- `asset_class`, `type_`;
- the init fields in gs order as `(name, default_repr, coerce_tag)`, where `coerce_tag` is a `pricebt.common` enum class name or `None`.

`pricebt.instrument` builds one class per entry. Each class has a **real `__init__`** (built by `exec` of generated source, or an equivalent binder plus `__signature__`) whose `inspect.signature` matches the gs one exactly:
- every gs init field is POSITIONAL_OR_KEYWORD, in gs order, with the gs default;
- **`name` is last, as in gs**;
- after `name` come the keyword-only pricebt extensions `pricebt_asset=None` and `quantity_=1.0`, then `**kwargs` for extra pass-through.

The names `pricebt_asset` and `quantity_` never collide with gs fields: gs uses `quantity` and `asset` as fields on other classes, and `quantity_` is gs's own position-multiplier name (base.py:577). A test asserts that no generated class has a field named `pricebt_asset`, `quantity_`, `instrument_quantity`, `kwargs`, `asset_config`, `resolved_terms` or `position_meta`.

```python
class Instrument(Priceable):
    # --- plain instance attributes (mutable; stored in __dict__; deep-copy safe) ---
    name: Optional[str]                  # the engine assigns it directly
    quantity_: float                     # signed multiplier (§5.4); default 1.0
    pricebt_asset: Optional[str]         # explicit asset name, if given
    resolved_terms: Optional[dict]       # plain-data output of the asset's resolve; None = unresolved
    resolution_key: Optional[RiskKey]    # RiskKey(date=resolution date, ...) once resolved
    resolution_csa: Optional[str]        # csa in force when resolved
    unresolved: Optional["Instrument"]   # the pre-resolution instrument (gs contract, R04§3.2)
    position_meta: Optional[tuple]       # (action_name, priceable_name, create_date) — DEV-E5; excluded from eq/hash/dicts
    _kwargs: dict                        # gs fields + extras (None-valued omitted), camelCase→snake_case, enum-coerced
    _matched_asset: Optional[str]        # cache of §5.3 matching; excluded from eq/hash
    # --- properties ---
    kwargs -> dict                       # a copy of _kwargs
    instrument_quantity -> float         # alias of quantity_ (the gs property name)
    asset_config -> AssetConfig          # §5.3, via the current session registry
    # --- gs methods ---
    clone(**kw) -> Instrument            # dataclasses.replace semantics (see below)
    resolve(in_place=True)               # §6.5
    calc(risk_measure_or_iterable, fn=None)   # §6.5
    price(currency=None)                 # calc(Price) / calc(Price(currency=...))
    dollar_price()                       # calc(DollarPrice)
    scale(scaling, in_place=True, check_resolved=True)   # see below
    flip(in_place=True)                  # scale(-1, in_place)
    to_dict() -> dict                    # as_dict() without 'name'
    as_dict(as_camel_case=False) -> dict # {'asset_class','type','name', **(resolved_terms or kwargs), 'quantity_'} (None dropped)
    _set_resolution(resolved_terms, resolution_key, resolution_csa, unresolved) -> None   # the ONLY way to mark resolved
    __getattr__(field)                   # contract below
    __eq__/__hash__                      # contract below
    __repr__                             # gs: f'{ClassName}({name})' if name else ClassName
    asset_class, type_                   # class attributes from _gs_fields (read-only)
```

**Object contract** (the engine call sites depend on it):

1. **Attributes.** `name`, `quantity_` and `position_meta` are plain mutable attributes, and the engine assigns `name` directly (generic_engine.py:522, 593; impls:299, 383, 386). The instrument stores **only plain data**. It never holds a reference to the session, an `AssetConfig`, a market or a trade object, so `copy.deepcopy` and pickle are safe and cheap.
2. **`__getattr__(field)`:**
   - (0) if `field` starts with `_`, raise `AttributeError` immediately, reading nothing else (this makes deepcopy and pickle probes safe);
   - (1) if the instrument is resolved **and** a session exists **and** its asset declares `field` under `attributes:`, evaluate it (§4.3) and return the value. An exception inside that declared expression propagates as `AssetEvaluationError`, so it is loud rather than silently false;
   - (2) if resolved and `field in resolved_terms`, return that value;
   - (3) if `field in _kwargs`, return that value;
   - (4) raise `AttributeError(field)`. This also happens when no session exists or the asset cannot be matched, because step 1 is then skipped.

   So `hasattr(inst, '1m')` is False, which `get_final_date` needs (backtest_utils.py:101).
3. **`__eq__`/`__hash__`** cover the tuple (class, `pricebt_asset` [the explicit argument only, never `_matched_asset`], frozen `_kwargs`, `quantity_`, `name`, frozen `resolved_terms`). They exclude `position_meta`, `resolution_key`, `resolution_csa`, `unresolved` and `_matched_asset`. The hash therefore never changes when an instrument is first priced. The freeze rule is: a dict becomes a sorted tuple of items, a list or tuple becomes a tuple (recursively), any other hashable value is kept as is, and anything else becomes `repr(v)`.
4. **`clone(**kw)`.** `kw` may contain gs fields and extras (which update `_kwargs`), `name`, `quantity_` and `pricebt_asset`. The clone carries the resolution state (`resolved_terms`, `resolution_key`, `resolution_csa`, `unresolved`), `position_meta` and `_matched_asset`. Changing a gs field or extra on a **resolved** instrument raises `ValueError("clone a resolved instrument only with name/quantity_")`; the engine never does this (RebalanceAction uses `quantity_`, §9.4).
5. **`scale(scaling, in_place=True, check_resolved=True)`:**
   - `scaling is None` returns `self`;
   - an unresolved instrument with `check_resolved` raises `RuntimeError('Can only scale resolved instruments')` (gs);
   - `in_place=True` does `quantity_ *= scaling` and returns `None`;
   - `in_place=False` returns `deepcopy(self)` with `quantity_ *= scaling`.
6. **`instrument_identity(inst)`** returns `(class, pricebt_asset or _matched_asset, frozen resolved_terms if resolved else frozen _kwargs, quantity_)`. It is used where gs compares `to_dict()` sets (DEV-I3).

`ConfigInstrument(pricebt_asset: str, *, name=None, quantity_=1.0, **kwargs)` is the class for assets gs has no class for. Its `asset_class` and `type_` are `None`.

### 5.2 kwargs handling (opaque, with gs coercion only)

- camelCase kwargs are converted to snake_case (gs `handle_camel_case_args`, R04§3.1). Passing both `fooBar` and `foo_bar` raises `ValueError`.
- A gs field whose `coerce_tag` names an enum is coerced to the **pricebt enum** with gs rules: case-insensitive on the value, and `PayReceive` also accepts `'receive'`/`'receiver'`. An invalid value raises `ValueError` at construction, as in gs. **pricebt enums subclass `str`, with `EnumBase` first in the MRO**, so `str(PayReceive.Receive) == 'Rec'` and a config can compare `kwargs["pay_or_receive"] == "Pay"`. Note that `PayReceive.Receive == "Rec"`, which is the gs value.
- Everything else is **passed through untouched**: `'10y'`, `'ATM+25'`, `'100k'`, `date(...)`, numbers and unknown extras. Only the asset's `resolve` parses them.
- `defaults` from the asset config fill keys that are absent or `None` **at resolve time**, because the asset is looked up lazily. **Default values for enum-tagged fields are coerced with the same rules**, so `pay_or_receive: Receive` reaches the config as `PayReceive.Receive`.

### 5.3 Matching an instrument to an asset

At the first pricing or attribute request, the asset is determined as follows:
1. If `pricebt_asset` is given, use the registry entry with that name. If there is none, raise `ConfigError("unknown asset ...; registered: [...]")`.
2. Otherwise, consider the registered assets whose `instrument:` equals the class name **and** whose `match:` rules all hold on `{**defaults, **kwargs}`. Values are compared with `str(getattr(v, 'value', v)).upper()`.
   - Exactly one match: use that asset.
   - No match: raise `ConfigError` listing the candidates and why each failed.
   - More than one match: raise `ConfigError("ambiguous: ... pass pricebt_asset=...")`.
3. The chosen name is cached in `_matched_asset`. It is excluded from equality and is carried by `clone`.

### 5.4 Quantity replaces gs field-editing scale

gs scales a trade by editing its size fields: `notional_amount *= |s|`, flip `pay_or_receive` if `s<0`, and `fee *= -1` (R04§1). pricebt instead multiplies a **signed `quantity_`**: `scale(s)` does `quantity_ *= s`.

- A value with an **extensive** unit (`ccy`, `ccy_per_bp`, `ccy_per_bp2`, `number`) is reported as `quantity_ × unit_value`.
- **Intensive** units (`bp`, `pct`, `decimal`, `date`) are NOT multiplied. They describe one unit of the instrument, whatever its size or sign: in gs, the `IRFwdRate` of a scaled or flipped swap is the same rate.
- A function may override its unit's default with `scale_with_quantity: true|false`.
- `unit_value` is the config function evaluated on the unscaled trade.

This is exact for every product that is linear in its size: swaps, and swaptions (linear in notional). Configs never see `quantity_`, except as `pricebt_quantity` in `attributes`. Portfolio functions receive quantities as `weights` (§8.2).

---

## 6. The pricing layer: `pricebt.assets`, `pricebt.markets`, `pricebt.risk.results`

### 6.1 `PricebtSession` (`pricebt.session`)

```python
class PricebtSession:
    def __init__(self, assets=(), fx=None, *, tz="America/New_York", eod_time="17:00",
                 missing_market="drop", reporting_currency=None): ...
    @classmethod
    def use(cls, assets=(), fx=None, **kwargs) -> "PricebtSession"   # construct, set PricebtSession.current, return it
    current: ClassVar[Optional["PricebtSession"]]
    registry: AssetRegistry      # from `assets`: .yaml paths, directories (every *.yaml inside), dicts, or AssetConfig objects
    pricing: PricingService
    fx: Optional[FxConfig]
    missing_market: Literal["drop", "raise"]        # read by GenericEngine.run_backtest (§9.5)
    reporting_currency: Optional[str]               # used ONLY to seed initial_value when result_ccy is None (§7.7)
    def add_asset(self, source) -> AssetConfig
    def reset_caches(self) -> None
    def __enter__ / __exit__     # context-manager form: restores the previous PricebtSession.current on exit

class GsSession:                  # gs-compatible shim so that `GsSession.use(...)` cells run unchanged
    current: ClassVar[Optional[dict]]
    @classmethod
    def use(cls, environment_or_domain=Environment.PROD, client_id=None, client_secret=None, scopes=(),
            api_version='v1', application='gs-quant', http_adapter=None, use_mds=False, domain='AppDomain') -> None
        # no network: stores the use() arguments in a dict on GsSession.current; does NOT create a PricebtSession.
        # MUST NOT copy gs session attribute names (gs has e.g. an is_*_login attribute that would trip the token scan).
class Environment(EnumBase, Enum): DEV = 1; QA = 2; PROD = 3
```

Pricing without a current `PricebtSession` raises `PricebtError("no PricebtSession: call PricebtSession.use(assets=[...]) first")`.

### 6.2 `PricingService` (`pricebt.assets.pricing`): the only producer of values

```python
class PricingService:
    def __init__(self, registry, fx=None, *, tz, eod_time): ...
    def asset_for(self, inst) -> AssetConfig
    def market(self, asset, d: date, csa: Optional[str]) -> Any                 # cached; None -> MarketDataUnavailable
    def has_market(self, asset, d: date, csa: Optional[str]) -> bool            # evaluates (and caches) the market
    def resolve(self, inst, d: date, csa: Optional[str]) -> Instrument          # a resolved CLONE (name, quantity_, meta kept)
    def unit_value(self, inst, d: date, function: str, csa) -> float
    def portfolio_value(self, asset, d, function, insts: Sequence[Instrument], csa) -> float | dict[str, float]
    def attribute(self, inst, field: str) -> Any                               # at inst.resolution_key.date (§4.3)
    def fx(self, from_ccy: str, to_ccy: str, d: date) -> float
    def value(self, inst, d, risk: RiskMeasure, csa) -> FloatWithInfo | LazyFuture | Instrument   # §8.1; quantity/FX applied
    def reset(self) -> None
```

The seams `pricebt.markets._engine_calc` and `pricebt.markets._engine_resolve` (§6.5) are thin functions whose bodies do `from pricebt.assets import pricing` (a function-level import, so the DAG in §3.2 holds) and delegate to `pricing.engine_calc(...)` / `pricing.engine_resolve(...)`, which use `PricebtSession.current.pricing`. `PricingService.group_aggregate(group_key, members)` serves the bucketed group calls of §8.2.

**Resolution-state rules** (these apply to `value`, `unit_value`, `portfolio_value` and `attribute`):
1. An instrument is resolved iff `resolved_terms is not None`. `resolve(inst, d, csa)` on a resolved instrument returns a clone with the **same** `resolved_terms`, `resolution_key` and `resolution_csa`. It is idempotent and never re-evaluates `resolve` from `kwargs`: gs resolved fields are concrete values, so re-resolving them changes nothing.
2. `value`, `unit_value` and `portfolio_value` on an **unresolved** instrument first resolve it on `(d, csa)` and price the resulting terms. This resolution is cached under `(asset, frozen kwargs-after-defaults, d, csa)` and the caller's object is not mutated, which matches the gs server behaviour (a user calling `IRSwap(...).price()`).
3. `attribute(inst, field)` requires a resolved instrument (§4.3); on an unresolved instrument the caller's `__getattr__` never calls it (§5.1 step 1).

### 6.3 Caches (per `PricingService`; cleared by `reset()`, which the engine calls at the start of every `run_backtest`)

| Cache | Key | Value |
|---|---|---|
| market | `(market_key, date, csa)` | the market object, or a sentinel for `None` |
| resolution of unresolved instruments | `(asset, frozen kwargs-after-defaults, date, csa)` | resolved terms |
| trade (`build_on=each_market`) | `(asset, frozen resolved, date, csa)` | the library trade object |
| trade (`build_on=resolve_date`) | `(asset, frozen resolved, resolution date, resolution csa)`; always built on `market(resolution date, resolution csa)`, **never** on the requesting date's market | the library trade object |
| unit value | `(asset, frozen resolved, date, function, csa)`, plus `(resolution date, resolution csa)` for `resolve_date` assets | float |
| portfolio value | `(asset, date, function, csa, tuple((frozen resolved, [res date, res csa,] weight) ...))` | float or dict |
| attribute | `(asset, frozen resolved, field, quantity_, resolution date, resolution csa)` | value |
| fx | `(from, to, date)` | float |

`frozen resolved` = `tuple(sorted(resolved.items()))`, which is hashable because of §4.4. **Unit values depend only on economic terms** (and on the build market for `resolve_date` assets), so a hedge's unit trade and its scaled copy share cache entries.

### 6.4 Trade construction: `build_on`

- `each_market` (default; the safe choice): the trade object is rebuilt from `resolved` on each date's market. Use this when the library's trade object captures its curve at build time. For example, a rateslib `IRS(curves=...)` whose own `.npv()` would silently use the old curve.
- `resolve_date`: the trade object is built once, on the resolution-date market (under the resolution csa), and reused on later markets. Functions receive today's `market` and the old `trade`, and the config re-marks where needed. With ARBS, `npv` and `pv01` are safe on a later market, while `fair_rate`, carry and roll need `remark(market, trade)` (R06§3.4). The ARBS example uses `resolve_date`, because each `build_irswap` call costs 12–23 ms (R06§1.7).

### 6.5 gs-shaped contexts, return conventions and futures

```python
class PricingContext:                # pricebt.markets — gs signature (R04§6); only pricing_date and csa_term are used
    def __init__(self, pricing_date=None, market_data_location=None, is_async=None, is_batch=None, use_cache=None,
                 visible_to_gs=None, request_priority=None, csa_term=None, timeout=None, market=None, show_progress=None,
                 use_server_cache=None, market_behaviour='ContraintsBased', set_parameters_only=False,
                 use_historical_diddles_only=None, provider=None): ...
    current: ClassVar                # the innermost entered context, else a default context (pricing_date = date.today())
    pricing_date -> date             # own value, else inherited from the enclosing context, else date.today()
    csa_term -> Optional[str]        # own value, else inherited
    is_entered -> bool; is_async -> bool
    __enter__/__exit__               # a stack; nothing is deferred

class HistoricalPricingContext(PricingContext):
    def __init__(self, start=None, end=None, calendars=(), dates=None, is_async=None, is_batch=None, use_cache=None,
                 visible_to_gs=None, request_priority=None, csa_term=None, market_data_location=None, timeout=None,
                 show_progress=None, use_server_cache=None, provider=None): ...
    date_range -> tuple[date, ...]   # `dates` as given; else date_range(start, end or today); an int start = last N business days,
                                     #   DESCENDING (gs). Both or neither of start/dates -> gs ValueErrors. Property is named
                                     #   `date_range`, matching gs exactly (MUST-2) -- only the constructor kwarg is `dates`.

def _engine_calc(priceable, measures, fn=None): ...     # seam: body = from pricebt.assets import pricing; return pricing.engine_calc(...)
def _engine_resolve(priceable, in_place: bool): ...     # seam: body = from pricebt.assets import pricing; return pricing.engine_resolve(...)
# priceable: Instrument or Portfolio; measures: RiskMeasure, tuple or list. Both read PricingContext.current for
# (pricing_date, csa_term, historical dates) and apply the return convention below.
```

**Return convention (gs).**
- `Portfolio.calc(...)` **always returns a `PortfolioRiskResult`**, whether or not a context is entered, and never wraps it in a future (1.5.4 portfolio.py:585-595). A PRR is itself future-like: `.result()` returns `self`, and `.done()` returns `True`.
- `calc` accepts a single RiskMeasure, a tuple or a list.
- `Instrument.calc(...)`, `Instrument.resolve(in_place=False)` and `Portfolio.resolve(in_place=False)` return a completed `PricingFuture` when `PricingContext.current.is_entered or PricingContext.current.is_async`, and the plain value otherwise.
- `resolve(in_place=True)` returns `None` outside a context, and a completed `PricingFuture` whose result is `None` inside one (gs core.py:114).
- `resolve(in_place=True)` **mutates the receiving objects**:
  - `Instrument.resolve(in_place=True)` calls `self._set_resolution(...)` on the same object, storing a copy of the pre-resolution self as `unresolved`.
  - `Portfolio.resolve(in_place=True)` calls `resolve(in_place=True)` on every leaf it holds and never replaces a leaf with a clone. CashPayments already hold those objects: the engine builds `CashPayment(renamed_inst)` before `init_port.resolve()` (generic_engine.py:355-367).
- The resolution date is `PricingContext.current.pricing_date`, and the resolution csa is `PricingContext.current.csa_term`.

**Historical results.**
- Under `HistoricalPricingContext(dates=D)`, `Portfolio.calc(risks)` returns one `PortfolioRiskResult` covering every date in `D`, and `result[d]` gives the single-date PRR.
- `Portfolio.resolve(in_place=False)` returns (a future of) `{d: Portfolio([r_1(d), r_2(d), ...], name=self.name)}`, where `r_i(d)` is the resolution of `self.priceables[i]` on `d`:
  - a leaf becomes a **fresh** resolved Instrument clone;
  - a nested Portfolio becomes, recursively, `Portfolio(<its resolved children>, name=<its name>)`;
  - **nesting is never flattened**, and **each date gets fresh objects**, shared neither across dates nor with the input. HedgeActionImpl and AddScaledTradeActionImpl rename them in place (impls:361-386).

  Example: `Portfolio(Portfolio([swap], name='hedge')).resolve(in_place=False).result()[d].priceables[0]` is a Portfolio named `'hedge'`. `Portfolio(p)` where `p` is a Portfolio wraps it as a single child (gs).
- `Instrument.resolve(in_place=True)` under a historical context raises `RuntimeError('Cannot resolve in place under a HistoricalPricingContext')` (gs).

**Resolution measure.** `ResolvedInstrumentValues` used as a calc measure returns the **resolved instrument clone** for each instrument. With several measures, `result[inst]` is a `MultipleRiskMeasureResult` (a dict keyed by measure), and `new_inst[ResolvedInstrumentValues]` is the resolved clone (the 2.1.17 AddScaled path, impls:296-299).

### 6.6 Errors (`pricebt.errors`)

| Class | Raised when | The message MUST include |
|---|---|---|
| `ConfigError(message, asset=None, key=None)` | schema/validation problems, unknown asset, ambiguous match, bad unit conversion, market-key conflicts | asset name, config key, the offending value, and a did-you-mean where applicable |
| `AssetEvaluationError(asset, key, expr, date, csa)` | any exception while evaluating imports, code or an expression (`raise ... from exc`) | asset, key, the first 200 chars of the expression, date, csa, and the original exception type and message |
| `MarketDataUnavailable(asset, date, csa, reason="")` | a market expression returned `None` where a market was required; FX unavailable | asset or pair, date, and the reason (e.g. `'exit date'`) |
| `NotSupportedError` | stubs (§9.3), unsupported gs features or parameters | what to use instead |

Messages that pricebt authors MUST pass the token scan (§12.2). Write "GS server-side" or "GS infrastructure", and never a platform or vendor name.

---

## 7. Currency model (MUST-4)

1. **Local by default (gs parity).** A currency-bearing measure with no `currency` parameter (e.g. `Price`) is in the **function's currency**: its own `currency:`, else the asset currency (decision 0.5). Its `FloatWithInfo.unit` is then `{"<that ccy>": 1}`. `currency='local'` means the same thing.
2. **Converted on request.** A measure with `currency='USD'` evaluates to unit value × quantity_ × `fx(function currency, 'USD', d)`, with unit `{"USD": 1}`. `DollarPrice` is equivalent to `Price(currency='USD')`.
3. **`run_backtest(result_ccy='USD')`** applies exactly the gs rule (R02§3.2):
   - every risk `r` in the run becomes `r(currency='USD')`, including a `ScaledTransactionModel`'s `RiskMeasure` `scaling_type` (DEV-E18) — otherwise a transaction cost could silently stay in the measure's own currency while `Total` sums it in as if converted;
   - a risk that is not a `ParameterisedRiskMeasure` raises `RuntimeError(f'Unparameterised risk: {r}')` — except a non-parameterised `scaling_type`, which DEV-E18 leaves unconverted rather than raising (DECISIONS_LOG.md 2026-09-28);
   - the price measure becomes `Price(currency='USD')`;
   - cash is then booked from the converted price at the payment date's FX, so `cash_dict` has one key.
4. **A mixed book without `result_ccy`** gets the gs errors unchanged, plus a hint:
   - two currencies held on one date: `ValueError('Cannot aggregate results with different units for Price')`;
   - in `result_summary`: `RuntimeError('Cannot aggregate cash in multiple currencies')` (R03§4.2, §15).

   The message gains `" — pass result_ccy=... (pricebt converts with your FX config)"`.
5. **Hedging across currencies** uses gs's check: `current_risk.unit != hedge_risk.unit` raises `RuntimeError('cannot hedge in a different currency')`. It passes when the hedge risk measure has a `currency` parameter, because both sides are then converted into that currency.
6. **Cash** is kept per currency (`cash_dict[d][ccy]`, as in gs), keyed by the **ISO code taken from the value's unit** (DEV-E13), not through `map_ccy_name_to_ccy`. Cash accrual applies per currency; gs's `get_accrued_value` already loops over currencies.
7. **`initial_value`** (DEV-E11). When `initial_value != 0`, before the cash walk, set `cash_dict[D[0]] = {ccy0: initial_value}` and `current_value = (that dict, D[0])`. The currency `ccy0` is chosen in this order:
   - `result_ccy`, if given;
   - else `PricebtSession.current.reporting_currency`, if set;
   - else the single currency of all the assets collected by the pre-scan (§9.5 step 1).

   If those assets span more than one currency and neither of the first two is set, raise `ValueError('initial_value needs result_ccy or PricebtSession(reporting_currency=...) for a multi-currency strategy')`. When `initial_value == 0`, nothing is seeded, so the frames are identical to gs. In the ported walk, the gs line `backtest.cash_dict[d] = {ccy: initial_value}` becomes `{ccy: 0.0}` (`# pricebt DEV-E11`).

---

## 8. Risk measures and results

### 8.1 Risk-measure objects (`pricebt.risk`) and the measure → function rule

pricebt reimplements gs's identity semantics exactly (R03§14.1, R04§9):
- `RiskMeasure` is a frozen, hashable dataclass `(asset_class, measure_type, unit, parameters, value, name)`. Equality and hashing cover all of these fields, so `Price != Price(currency='USD')`.
- It also has `base_name: str = field(default=None, compare=False, repr=False)`. `__call__(..., name=X)` sets it to the pre-rename name, which gives presets a generic fallback.
- `ParameterisedRiskMeasure` subclasses are **callable**. `IRDelta(aggregation_level='Type', currency='USD')` returns a clone with the parameters merged, and unset arguments inherit. If the first argument is a pandas Series or DataFrame, the call returns `self` (the gs `.loc` hack). A string `aggregation_level` is coerced to `AggregationLevel`.
- `repr` is the `name` when there are no parameters, else `Name(k:v, ...)` with keys sorted case-insensitively (`Price(value:USD)`, `IRDelta(aggregation_level:Type, currency:USD)`). `__lt__` sorts by name, then by parameters.
- Classes:
  - `RiskMeasureWithCurrencyParameter`: `Price`, `EqDelta`, `EqVega`, `Annuity`, and the 2.1.17 `FXDeltaLocalCcy`, `FXGammaLocalCcy`, `FXVegaLocalCcy` (asset_class FX; measure_type `'FX Delta Local Ccy'` etc.; 2.1.17 target/measures.py:370/385/430). `__call__(currency=None, name=None)`; the parameter is stored as `CurrencyParameter(value=...)`, so its key is `value`.
  - `RiskMeasureWithFiniteDifferenceParameter`: `IRDelta`; `IRDeltaParallel` (preset `aggregation_level=Asset`, name `'IRDeltaParallel'`); `IRDeltaLocalCcy` (preset `currency='local'`); `IRVega`, `IRVegaParallel`, `IRVegaLocalCcy`, `IRBasis`, `IRXccyDelta`, `InflationDelta`, `FXDelta`, `FXGamma`, `FXVega`. `__call__(aggregation_level=None, bump_size=None, currency=None, finite_difference_method=None, local_curve=None, mkt_marking_options=None, scale_factor=None, name=None)`.
  - plain `RiskMeasure`: `DollarPrice`, `IRGamma`, `IRGammaParallel`, `IRFwdRate` (unit Percent), `IRSpotRate`, `IRDailyImpliedVol`, `IRAnnualImpliedVol`, `FXSpot`, `FXAnnualImpliedVol` (measure_type `'Annual Implied Volatility'`, unit Percent), `EqSpot`, `EqGamma`, `Cashflows`, `ResolvedInstrumentValues`.
- Include **every measure imported by the in-scope 2.1.17 backtests files** (grep `from gs_quant.risk import` in backtest_objects.py, generic_engine.py and triggers.py). `pricebt.risk` also re-exports `FloatWithInfo`, `SeriesWithInfo`, `DataFrameWithInfo` and `ErrorValue`, as gs does.

**Measure → config function (generic for every asset class; no measure names are hard-coded in `assets/pricing.py`):**
1. `ResolvedInstrumentValues` → `PricingService.resolve`.
2. `DollarPrice` → the `Price` mapping with `currency='USD'`.
3. Look up `asset.risk_measures[measure.name]`. If it is missing and `measure.base_name` is set, look up `asset.risk_measures[measure.base_name]`. This covers presets such as `IRDeltaParallel` → `IRDelta` with its preset parameters. If it is still missing, raise `ConfigError(f"asset {a} has no mapping for risk measure {measure.name}; add it under risk_measures:")`.
   - 3a. If the measure has a parameter with a non-None value, other than the currency parameter (`currency`, or `value` on currency-parameter measures) and `aggregation_level`, raise `NotSupportedError(f"asset {a}: {measure!r} sets {p}; pricebt passes only aggregation_level and currency to asset configs")`. Nothing is silently ignored.
4. Normalise a string mapping: a `functions:` entry or a `returns: scalar` portfolio function becomes `{scalar: f}`, and a `returns: buckets` portfolio function becomes `{bucketed: g}`.
5. Choose the form:
   - the measure has an `aggregation_level` parameter set to Type, Asset or Class → scalar;
   - it is set to None or Point → bucketed;
   - the measure has no such parameter → scalar if a `scalar` form is mapped, else bucketed.

   A missing scalar form is computed as the sum of the buckets. A missing bucketed form raises `ConfigError`.
6. A portfolio function used for one instrument is evaluated with `trades=[trade]` and `weights=[1.0]`, which gives the unit value. Then apply `quantity_` if the unit is extensive (§5.4), and FX if the measure's currency differs from the function's currency (§7).

### 8.2 Result objects (`pricebt.risk.results`): what the engine and notebooks use

| Class | Must provide |
|---|---|
| `RiskKey` | namedtuple `(provider, date, market, params, scenario, risk_measure)`; pricebt fills `date` and `risk_measure`, the rest are `None` |
| `FloatWithInfo(float)` | `.risk_key`, `.unit` (dict), `.error` (None), `.raw_value`. `+` with an equal unit gives a `FloatWithInfo`; an unequal unit raises `ValueError('FloatWithInfo unit mismatch')`. `repr` is `1500.0 (USD)`. |
| `DataFrameWithInfo(pd.DataFrame)` | `_metadata = ['risk_key', 'unit', 'error']`; `.raw_value`. A bucketed result has **exactly the columns `mkt_type, mkt_asset, mkt_class, mkt_point, mkt_quoting_style, value`**, all six always present. A missing label is `''`, never NaN. Row order is the order of the portfolio function's returned dict (DEV-R5). |
| `SeriesWithInfo(pd.Series)` | the same metadata; historical scalar results are indexed by date |
| `ErrorValue` | `(risk_key, error)`; `.raw_value = None` |
| `MultipleRiskMeasureResult(dict)` | keyed by measure; `.transform(t)` applies per measure |
| `PricingFuture(result=None, exception=None)` | `.result()` returns the value or raises the stored exception; `.done()` is True |
| `LazyFuture(PricingFuture)` | `(thunk, group_key, member, service)`. `.result()` calls `thunk()` once and memoises the value; `.done()` is True. It is used only for per-instrument bucketed values (below). |

**`PortfolioRiskResult` contract** (gs results.py:600-973 semantics, with ordered risk measures):
- **Constructor** `PortfolioRiskResult(portfolio, risk_measures, futures)`:
  - `portfolio` is a shallow `clone()` of the Portfolio that `calc` was called on. For a resolve calc, its leaves are therefore the UNRESOLVED priceables.
  - `risk_measures` is stored as a tuple **in the order given**.
  - `futures` holds exactly one future per **direct child** of `portfolio`, in `portfolio.priceables` order. For an Instrument child, `future.result()` is the value (for one measure) or a `MultipleRiskMeasureResult` (for several). For a nested Portfolio child, it is a nested PRR. Plain values passed in are wrapped as `PricingFuture(value)`.
  - The constructor MUST accept any list with that alignment, because ExitTradeActionImpl rebuilds results from sliced `futures` (impls:447-485).
- `.portfolio`, `.risk_measures`, `.futures` (a tuple), and `.dates` (the result dates; one for a non-historical result).
- `__len__` → `len(self.futures)`; `__bool__` follows `__len__`; `__iter__` yields the per-child results (for a single measure, e.g. the resolved instruments).
- `__getitem__(item)`, following the gs error contract:
  - a RiskMeasure not in `risk_measures` raises `ValueError(f'{item} not computed')`;
  - with exactly one computed measure, `self[measure]` returns `self`; otherwise it returns a single-measure view;
  - an `Instrument` or a name `str` returns that instrument's value(s); if the item is not in `.portfolio`, it raises `KeyError(str(item))`. Indexing by a resolved Instrument that is not found falls back to `item.unresolved` (results.py:925-940);
  - a `date` returns the single-date view; on a non-historical result this raises `RuntimeError('Can only index by date on historical results')`;
  - an `int` indexes by position.
- `.get(item, default)` → `self[item]`, or `default` if that raises `KeyError` or `ValueError` (results.py:968; used by generic_engine.py:700).
- `__contains__`: by instrument, name or measure.
- `__add__(other)`: gs semantics (results.py:731-775).
  - If the measures, the dates and the instrument sets all overlap: `ValueError('Results overlap on risk measures, instruments or dates')`.
  - If the portfolios are equal: merge futures pairwise.
  - Otherwise: portfolio = `self.portfolio + other.portfolio` and futures = `self.futures + other.futures`.
  - `risk_measures` of the sum is an **ordered** union: self's measures first, then other's new ones (DEV-E14; gs uses a set).
- `.transform(risk_transformation=None)`:
  - with `None`, return `self`;
  - with several measures, return a `MultipleRiskMeasureResult` of per-measure transforms;
  - with one measure, compute `vals = risk_transformation.apply(tuple(self))` (the per-instrument results, in leaf order) and return `PortfolioRiskResult(self.portfolio, self.risk_measures, [PricingFuture(v) for v in vals])` (results.py:820-837).
- `.aggregate(allow_mismatch_risk_keys=False, allow_heterogeneous_types=False)`:
  - scalars: a `FloatWithInfo` sum, where unequal units raise gs's `ValueError`;
  - bucketed: see below.
- `.to_frame(values='default', index='default', columns='default', aggfunc='sum')`: at least `values='value', index='instrument_name', columns='risk_measure'` MUST work, with bucketed values summed per instrument (R03§7).
- `.result()` → self; `.done()` → True.

**`ResultWithInfoAggregator(risk_col='value', filter_coord=None).apply(results)`** returns a **list** with one entry per input result:
- a float stays a float;
- a FloatWithInfo stays as is;
- a SeriesWithInfo or DataFrameWithInfo becomes `FloatWithInfo(df[risk_col].sum(), unit=df.unit, risk_key=df.risk_key)`, with rows filtered by `filter_coord` when it is given.

`Transformer.apply(data, *args, **kwargs)` is abstract.

**Bucketed (vector) values are lazy and group-aggregated.**
- For a bucketed measure, `PricingService.value` returns one `LazyFuture` per instrument, with:
  - `group_key = (asset_name, market_key, date, csa, function, target_ccy)`;
  - `member = (instrument_identity, frozen resolved, quantity_)`;
  - a thunk that evaluates the single-trade ladder (`weights=[1.0]`, then × quantity_ and FX);
  - `service` = the `PricingService` instance that created it.

  The thunk and the group call use that captured service and the captured (date, csa). They never read `PricebtSession.current`, so a view computed after a `reset()` or after the session block has exited re-evaluates correctly, as a cache miss rather than an error.
- `PortfolioRiskResult.aggregate()` for a single bucketed measure first collects the leaf futures without evaluating them. If they are all `LazyFuture`s with a non-None `group_key`, it groups them by `group_key` and calls `first_future.service.group_aggregate(group_key, members)` **once per group** (the service captured by the futures; no module-level hook). That call evaluates the portfolio function with all of the group's trades and `weights = [quantities]` **if the function's unit is extensive** (§5.4), with no second multiplication; for an **intensive** unit, `weights = [1.0, ...]` instead (same rule the single-instrument thunk applies via `_eval_unit_cached`/`_scale_bucket` above), so quantity_ never reaches an intensive-unit portfolio function's `weights` by either route.
- Otherwise (e.g. values materialised by user code), aggregation is `pd.concat(...)` followed by `groupby([mkt_type, mkt_asset, mkt_class, mkt_point, mkt_quoting_style], sort=False, dropna=False, as_index=False)["value"].sum()`.
- Row order is first appearance: groups in asset-registration order, then buckets in returned-dict order (DEV-R5).
- The group metadata travels **with the futures**, so any PRR that gs code rebuilds from `.futures` (ExitTradeActionImpl, `__add__`, slices) still aggregates by group.
- Because ladders are linear, both routes agree; a test asserts that the sum of per-trade values equals the group call to 1e-9 relative, with quantities 2.5 and −0.7.

### 8.3 `BackTest` views

Port 2.1.17 `backtest_objects.BackTest`: `result_summary`, `risk_summary`, `get_risk_summary_df`, `trade_ledger()`, `strategy_as_time_series()`, `pnl_explain()`, `summary_stats()`, `fx_pnl_definition()` and the properties. Shapes and column names are exactly those in R03§4–§8, with the fixes in §11 (DEV-R*). Key invariants:
- **`result_summary`** has the columns `[*risk columns in self.risks order], 'Cumulative Cash', 'Transaction Costs', 'Total'`. `self.risks` is the ordered list from DEV-E14, and `get_risk_summary_df` reindexes its columns to that order. Risk column labels are the RiskMeasure objects. `Total = price_measure + Cumulative Cash + Transaction Costs`.
- **`trade_ledger()`** has exactly the columns `Open, Close, Open Value, Close Value, Long Short, Status, Trade PnL`, object dtype, indexed by trade name.
- **`strategy_as_time_series()`** has the row MultiIndex `('Pricing Date', 'Instrument Name')` and the column groups `'Static Instrument Data' | 'Risk Measures' | 'Cash Payments'`. The static data comes from `Portfolio.to_frame()`, which is built from `as_dict()`, so it **includes a `name` column** that BackTest renames to `'Instrument Name'` (backtest_objects.py:334-336), plus `quantity_` (DEV-I2).

### 8.4 `BackTest.pnl_bps(risk)`: a pricebt extension (MUST-4)

```python
def pnl_bps(self, risk: RiskMeasure, min_abs_risk: float = 1e-9) -> pd.DataFrame:
    """P&L expressed in bp of rate move: each row's P&L divided by the PREVIOUS row's risk (currency per bp).
    Example: backtest.pnl_bps(IRDeltaParallel).
    Columns: 'PnL' (Total.diff()), 'Risk' (result_summary[risk].shift(1)), 'PnL (bps)', 'Cumulative PnL (bps)'.
    'PnL (bps)' = PnL / Risk when |Risk| >= min_abs_risk, else NaN; cumulative = running sum treating NaN as 0.
    If `risk` is not a column and self.price_measure carries a currency parameter (result_ccy run), risk(currency=that) is tried.
    Raises ValueError(f"{risk} is not a column of result_summary; add it to run_backtest(risks=[...])") if still absent,
    and ValueError(f"{risk} is not a scalar measure; use e.g. IRDeltaParallel") if non-zero cells are not scalars."""
```

The view is meaningful for directional books; a flat-hedged book has risk ≈ 0, which gives NaN. It is currency-neutral: EUR P&L divided by EUR per bp. There is no default `risk`, because the results layer stays asset-agnostic.

---

## 9. The backtest port: `pricebt.backtests`

### 9.1 Porting rule

For every in-scope file:
1. **Copy the 2.1.17 source.** Keep its Apache-2.0 header and add the line `# Ported to pricebt from gs_quant 2.1.17 (Apache-2.0); see NOTICE. Changes: <DEV ids>`.
2. Replace imports according to the import map (§9.2).
3. Delete the JSON/serialisation plumbing: `@dataclass_json`, `config(...)` field metadata, `encode_*`/`decode_*`, `dc_decode`, and the JSON-registry patch at the end of triggers.py. Delete `Tracer` too, replacing `self._trace(...)` with `contextlib.nullcontext()`.
4. Apply the deviations assigned to that file in §11, and **make no other behavioural change**. Mark each changed block with `# pricebt DEV-XX: <one line>`.
5. Keep class names, field order, defaults, method names, comments and `class_type` static fields.

### 9.2 Import map (gs → pricebt)

| gs import | pricebt replacement |
|---|---|
| `gs_quant.backtests.*` | `pricebt.backtests.*` (same submodule) |
| `gs_quant.markets.PricingContext`, `HistoricalPricingContext` | `pricebt.markets` |
| `gs_quant.markets.portfolio.Portfolio` | `pricebt.markets.portfolio.Portfolio` |
| `gs_quant.instrument.Instrument, Cash, IRSwap, EqOption, EqVarianceSwap` | `pricebt.instrument` (`EqVarianceSwap`: omit; only EquityVolEngine uses it) |
| `gs_quant.risk.*` (measures and result classes such as `ErrorValue`), `gs_quant.target.measures.ResolvedInstrumentValues` | `pricebt.risk` |
| `gs_quant.risk.results.PortfolioRiskResult, PricingFuture` | `pricebt.risk.results` |
| `gs_quant.risk.transform.Transformer` | `pricebt.risk.transform` |
| `gs_quant.common.RiskMeasure, ParameterisedRiskMeasure, Currency, CurrencyName, AssetClass, BuySell, OptionType, TradeAs, FieldValueMap` | `pricebt.common` (`FieldValueMap`: a plain dict alias) |
| `gs_quant.base.Priceable, EnumBase, Base, field_metadata, static_field, exclude_none, get_enum_value` | `pricebt.base` (`static_field(v)` = `field(init=False, default=v)`; `field_metadata` = `None`, meaning "no metadata") |
| `gs_quant.datetime.relative_date.RelativeDate, RelativeDateSchedule` | `pricebt.datetime.relative_date` |
| `gs_quant.datetime.business_day_offset, is_business_day, prev_business_date` | `pricebt.datetime` |
| `gs_quant.timeseries.interpolate, Interpolate` | inline: `interpolate_signal` is reimplemented as `reindex(daily).ffill()` (R01§5.6) |
| `gs_quant.data.Dataset, DataFrequency` | `pricebt.data` (`Dataset` is a stub) |
| `gs_quant.context_base.nullcontext` | `contextlib.nullcontext` |
| `gs_quant.tracing.Tracer` | delete (§9.1 step 3) |
| `gs_quant.errors.MqValueError` | `ValueError` |
| `gs_quant.target.backtests.BacktestTradingQuantityType, DeltaHedgeParameters` | `pricebt.backtests.core` (the enum is `notional, quantity, vega, gamma, NAV, premium, vegaNotional`; `DeltaHedgeParameters` is a stub dataclass whose construction raises NotSupportedError) |
| `gs_quant.api.*`, `gs_quant.json_convertors*`, `dataclasses_json` | delete |

### 9.3 Scope per file

| pricebt file | Source (2.1.17) | Scope |
|---|---|---|
| `backtests/backtest_utils.py` | `backtest_utils.py` | full port. `get_final_date` per DEV-T1/T2/T16; `clear_final_date_cache()`; `map_ccy_name_to_ccy` kept for the API but unused by the engine |
| `backtests/core.py` | `core.py` | enums and NamedTuples, plus `BacktestTradingQuantityType` and `DeltaHedgeParameters` (stub); `Backtest`/`BacktestResult` → stubs |
| `backtests/data_sources.py` | `data_sources.py` | `MissingDataStrategy`, `DataSource`, and `GenericDataSource` rewritten per DEV-T13 (spec: R01§7.4 "v2 specification"); `GsDataSource`, `DataManager` → stubs |
| `backtests/backtest_objects.py` | `backtest_objects.py` | `BackTest` (+ `pnl_bps`, `missing_market_dates`), `ScalingPortfolio`, `WeightedScalingPortfolio`, transaction models, `TransactionCostEntry`, `CashPayment`, `Hedge`, `WeightedTrade`, `CashAccrualModel`, `ConstantCashAccrualModel`, `DataCashAccrualModel`, `PnlAttribute`, `PnlDefinition`, `fx_pnl_definition()` (ported verbatim), the `_BACKTEST_END` ContextVar (DEV-E12); `OisFixingCashAccrualModel` and `PredefinedAssetBacktest` → stubs (triggers.py imports `PredefinedAssetBacktest` by name) |
| `backtests/actions.py` | `actions.py` | every action, including `AddWeightedTradeAction` and `EarlyExitPositionLimitScaledAction`. The `EnterPositionQuantityScaledAction` and `ExitPositionAction` classes exist (API), but GenericEngine has no handler for them (as in gs) |
| `backtests/action_handler.py`, `backtest_engine.py` | same | full |
| `backtests/triggers.py` | `triggers.py` | every trigger/requirement; MeanReversion = 2.1.17 behaviour + `reset()`; DEV-T4..T10, T12, T15 |
| `backtests/strategy.py` | `strategy.py` | full; DEV-T14 |
| `backtests/generic_engine_action_impls.py` | same | full, with the DEV-E fixes |
| `backtests/generic_engine.py` | same | full, with the DEV-E fixes, the missing-market handling (§9.5) and the progress bar (§9.6) |
| stubs | `equity_vol_engine.py`, `predefined_asset_engine.py`, `strategy_systematic.py`, `order.py`, `event.py`, `data_handler.py`, `execution_engine.py` | module with the public class names. **Each stub MUST define every name another in-scope module imports from it** (`data_handler.DataHandler`, `event.FillEvent`, `order.OrderBase`, `order.OrderCost`, `strategy_systematic.StrategySystematic`, …). Constructors raise `NotSupportedError("... is GS-server-only / out of scope in pricebt v2; use GenericEngine")` |

### 9.4 Engine points where gs reads instrument fields, resolved generically

| gs code | pricebt behaviour |
|---|---|
| `get_final_date`: `hasattr(inst, str(duration))` → `getattr` (e.g. `'termination_date'`) | `Instrument.__getattr__` (§5.1): the asset's `attributes` expression, else resolved terms, else kwargs. A `date`/`datetime` return value makes it a final date. |
| `ScaledTransactionModel(scaling_type='notional_amount')` → `getattr(instrument, 'notional_amount')` | the same attribute lookup. The ARBS config defines `notional_amount` = `|notional| × quantity_`, which is signed; gs's cost formula takes `abs()` afterwards. |
| `ScaledTransactionModel(scaling_type=<RiskMeasure>)` → `with PricingContext(state): instrument.calc(risk)` | works unchanged through §6.5 |
| `ScaledTransactionModel.get_unit_cost`: `state > dt.date.today()` → `np.nan` | compares with `_BACKTEST_END.get()` when set (DEV-E12) |
| `RebalanceAction(priceable, size_parameter, method)` | The priceable is resolved; gs `__post_init__` raises otherwise (actions.py:486). `current_size = Σ getattr(t, size_parameter)` over held positions. `unit_size = getattr(priceable.clone(quantity_=1.0), size_parameter)`; `unit_size == 0` raises `ValueError`. The new position is `priceable.clone(quantity_=(new_size - current_size) / unit_size, name=f'{priceable.name}_{state}')`. `size_parameter` MUST equal the asset's `size_attribute`, else `ConfigError`. That attribute MUST be linear and signed in quantity (`attr(q) == q × attr(1)`), so that downsizing positions net correctly. A numeric `size_parameter` (gs allows `float`) raises `NotSupportedError`. |
| `Portfolio.scale(s)` / `Instrument.scale(s)` | quantity multiply (§5.1, §5.4) |
| `t.to_dict()` set-membership in ExitTradeActionImpl (a gs bug: dicts are unhashable) | compare `instrument_identity(t)` (DEV-I3) |
| `map_ccy_name_to_ccy(next(iter(value.unit)))` | `next(iter(value.unit))` is already the ISO code (DEV-E13) |
| `dt.date.today()` guards in the impls: NAV unwind (impls:209), hedge exit payment (impls:409), weighted-trade exit payment (impls:654) | compare with `backtest.states[-1]` (DEV-E12) |

### 9.5 Missing-market handling (decision 0.4)

`GenericEngine.run_backtest` reads `policy = PricebtSession.current.missing_market`. After the grid (`strategy_pricing_dates`) has been built **and extended with the trigger times** (generic_engine.py:229-243), and **before** Phase 2:

1. **Collect the tradable assets**:
   - the initial-portfolio instruments (list form, or every value of the dict form);
   - every action's `priceables` / `priceable` / `dated_priceables` values, flattening Portfolios, and including HedgeAction, AddWeightedTradeAction and RebalanceAction.

   Match each to its asset per §5.3.
2. **Check the grid.** For each grid date `d` and asset `a`, call `pricing.has_market(a, d, run csa)`. For a HedgeAction's asset, also call `has_market(a, d, action.csa_term)`.
3. **Apply the policy to missing grid dates.**
   - `'raise'`: raise `MarketDataUnavailable`, listing the first 10 missing (asset, date) pairs.
   - `'drop'`: remove those dates from the grid; call `warnings.warn(f"pricebt: dropped {n} dates with no market data ({first}..{last}); see backtest.missing_market_dates")`; store the sorted list on `backtest.missing_market_dates` (an empty list when nothing is dropped).
   - **If `'drop'` would remove every grid date**, raise `MarketDataUnavailable("no market data on any of the N grid dates for asset A; check the market expression / data source")` instead.
4. **Recompute the bounds after the drop.** Set `strategy_start_date = D[0]` and `strategy_end_date = D[-1]`, and use the kept `D` as `BackTest.states`.
   - The list-form initial portfolio is resolved and entered on the new `D[0]`.
   - DEV-T4's default Periodic start is the new `D[0]`.
   - A dict-form initial-portfolio key with no market moves to the first kept grid date ≥ that key; it is ignored if there is no such date ≤ the end.
5. **Trigger info.** A dropped trigger date never fires, because triggers are evaluated only on kept grid dates. The engine post-processes every `TriggerInfo.info_dict`: each `next_schedule` is mapped to the first kept grid date ≥ it, or to `None` if there is none ≤ `strategy_end_date`. The engine does **not** edit trigger internals.
6. **Off-grid valuation dates** (DEV-E16). Every date produced by `get_final_date` or by the NAV/unwind logic that is ≤ `strategy_end_date` and has **no market** for the position's asset is mapped by `_roll_to_market_date(asset, d, csa)` to the first kept grid date after it. A date that has a market is kept unchanged.
   - This covers exit CashPayments and their TCEs, ScaledTransactionModel(RiskMeasure) exit costs, and NAV unwind pricing.
   - The holding window `create <= s < final` is unchanged, because no kept grid date lies between the two dates.
   - Dates after `strategy_end_date` are left untouched and never booked (as in gs).
   - Under `'raise'`, each such date raises `MarketDataUnavailable(asset, date, reason='exit date' | 'next_schedule' | 'initial portfolio')`.
   - Each mapping is recorded on `backtest.missing_market_moves` as a list of `(trade name, original date, used date)`.
7. **Caching.** Markets evaluated here stay in the cache, so nothing is evaluated twice.

### 9.6 Progress bar

`run_backtest(show_progress=True)` shows **one** tqdm bar (`desc="pricebt"`). It advances once per date of the Phase 4 batch pricing, once per date of the Phase 5 date loop, and once per date of the cash walk. It uses the salvaged `pricebt.progress`, whose notebook-kernel path updates a single display output. `show_progress=False` shows no bar.

---

## 10. Data helpers: `pricebt.data`

- `Dataset(...)` raises `NotSupportedError("gs Dataset reads GS server-side data, which pricebt does not have; build a pandas Series from your own data or use pricebt.data.measure_series(...)")`.
- `DataFrequency` is an enum: `DAILY='daily', REAL_TIME='realTime', ANY='any'`.
- **`measure_series(instrument, measure, start, end, frequency='1b', holiday_calendar=None, csa=None, fill='ffill') -> pd.Series`** is a pricebt extension used by the 040304 port (R05§6.4):
  1. Build the dates: `RelativeDateSchedule(frequency, start, end).apply_rule(holiday_calendar=holiday_calendar)`.
  2. For each date `d`: with no market, the point is missing. Otherwise, **resolve a fresh copy of the (unresolved) instrument on `d`** and evaluate `measure` for quantity 1. The measure is either a function name of the asset config (`str`) or a `RiskMeasure` (resolved through §8.1).
  3. The index is `datetime.date`; the value unit is the function's declared unit (bp for the ARBS `par_rate`).
  4. Missing points: `fill='ffill'` forward-fills from the previous available date and drops leading missing dates rather than back-filling them; `fill=None` drops them. The count is logged at INFO level.
  5. `series.attrs["unit"]` holds the unit string and `series.attrs["missing_dates"]` the list of missing dates.

  Building the series on the full weekday schedule with forward fill means `GenericDataSource` never takes its insertion path, which gives exactly the gs semantics (R05§6.2).

---

## 11. Deviations from gs (decision 0.3)

Each row gets an entry in `docs/v2/DEVIATIONS.md` and a test. The "File (task)" column says where the deviation lives, and the P3 gate checks the `# pricebt DEV-` markers against it. "gs" in this table means 2.1.17.

**Triggers, utils, data (R01§12)**

| ID | File (task) | gs behaviour | pricebt behaviour |
|---|---|---|---|
| DEV-T1 | backtest_utils (P3.1) | a `timedelta` duration crashes | `create_date + duration` |
| DEV-T2 | backtest_utils (P3.1) + generic_engine (P3.5 calls it) | module-global `final_date_cache`, never cleared | `clear_final_date_cache()` is called at the start of every `run_backtest` |
| DEV-T3 | datetime (P1.2) | `holiday_calendar=None` → `GsCalendar` lookup | weekends only (`week_mask='1111100'`), no data access; currency and exchange codes are ignored with a one-time warning |
| DEV-T4 | triggers (P3.4: `_backtest_start` field) + generic_engine (P3.5 sets it) | `PeriodicTriggerRequirements.start_date=None` means today | the backtest start. It is set after the grid is built and before the `get_trigger_times()` loop, and updated after the §9.5 drop. |
| DEV-T5 | triggers (P3.4) | `NotTriggerRequirements.calc_type` is always `simple` | the child's `calc_type` |
| DEV-T6 | triggers (P3.4) | `NotTriggerRequirements.__setattr__` drops every attribute except `trigger` | normal attributes |
| DEV-T7 | triggers (P3.4) | Aggregate/Not `get_trigger_times` returns `[]` | the union of the children's times |
| DEV-T8 | triggers (P3.4) | `AggregateTriggerRequirements()` with no triggers raises `TypeError` | `ValueError('triggers required')` |
| DEV-T9 | triggers (P3.4) | intraday schedule loops forever on a midnight wrap | stop at the wrap |
| DEV-T10 | triggers (P3.4: `reset()`) + generic_engine (P3.5 calls it) | stateful trigger state persists across `run_backtest` calls | the engine calls `reset()` on every trigger requirement at run start, walking Aggregate/Not children. `MeanReversionTriggerRequirements.reset()` sets `current_position = 0`; `PeriodicTriggerRequirements.reset()` sets `trigger_dates = []`; `EventTriggerRequirements.reset()` clears its memo; the base `reset()` is a no-op |
| DEV-T11 | generic_engine + impls (P3.5) | Phase 3 zips parallel info lists positionally; the Phase 5 path-dependent branch accumulates the infos of every fired trigger into one list | Phase 3 uses `trigger_infos: dict[ActionType, dict[date, info]]` and passes that dict. Phase 5 passes only **this** trigger's `t_info.info_dict.get(type(action))`. Every impl accepts a `{date: info}` dict, a single info, or `None`; `dict(zip_longest(...))` is used only for a list |
| DEV-T12 | triggers (P3.4) | `EventTriggerRequirements.data_source=None` → GS event calendar | `ValueError('data_source required')` |
| DEV-T13 | data_sources (P3.1) | `GenericDataSource` mutates the series, looks ahead on a date index when forward-filling, and raises TypeError comparing a `DatetimeIndex` with a `date` | the R01§7.4 v2 specification |
| DEV-T14 | strategy (P3.4) | `get_available_engines` with zero triggers raises `TypeError` | `[GenericEngine()]` |
| DEV-T15 | datetime (P1.2) + triggers + backtest_utils | 1.5.4: `'1M'` means Nth Monday | units are lower-cased (as 2.1.17) |
| DEV-T16 | backtest_utils + actions + generic_engine | a list `holiday_calendar` raises `TypeError: unhashable` in the `get_final_date` cache | `run_backtest` and every action's `__post_init__` normalise `holiday_calendar` to a `tuple` |

**Engine (R02§7)**

| ID | File (task) | gs behaviour | pricebt behaviour |
|---|---|---|---|
| DEV-E1 | generic_engine (P3.5) | a path-dependent trigger is evaluated before the day's risks are ensured | `ensure(d)`, then `has_triggered(d)` |
| DEV-E2 | generic_engine (P3.5) | a simple trigger is re-evaluated once per path-dependent action | evaluated once per date; the `TriggerInfo` is cached |
| DEV-E3 | generic_engine + impls (P3.5) | a skipped hedge or skipped weighted-trade instrument (`hedge_risk == 0`, `d not in results`, zero total risk, zero scaling factor) still books its TCEs | its entry and exit TCEs are removed |
| DEV-E4 | generic_engine (P3.5) | the initial portfolio's exit payment uses the un-renamed, unresolved instrument, which adds a spurious ledger row with `Open 9999-12-31` | it uses the renamed, resolved instrument; no spurious row |
| DEV-E5 | actions (P3.3 sets meta) + impls (P3.5 dates + matching) | `ExitTradeAction(priceable_names)` splits names on `_`; it crashes when hedges are held, and underscores in names break it | `position_meta = (action_name, priceable_name, create_date)`. Each action's `__post_init__` sets `(action.name, <original priceable name or f'Priceable{i}'>, None)` on every renamed priceable. Wherever an impl appends `_{date}` to a name, it sets the third element. With `priceable_names`, x is removed iff `x.position_meta` is not None, `x.position_meta[1] in priceable_names` and `x.position_meta[2] <= s`. Positions with no meta (hedges) are never matched by name. Display names are unchanged |
| DEV-E6 | impls (P3.5) | `RebalanceActionImpl` appends the builtin `exit` and crashes | it appends the exit TCE |
| DEV-E7 | actions (P3.3) | `if not Portfolio:` never fires | `if portfolio is None: raise RuntimeError('hedge action only accepts one trade or one portfolio')` (the check gs intended) |
| DEV-E8 | generic_engine + impls (P3.5) | `backtest.results[d]` on the defaultdict creates `[]` entries | `.get(d)` |
| DEV-E9 | impls (P3.5) | a hedge `trade_duration` attribute is looked up on the Portfolio, which fails | looked up on the single hedge instrument when the portfolio has one leaf |
| DEV-E10 | actions (P3.3) | `ExitTradeAction.priceable_names` as a bare string is substring-matched; typo `priceables_names` | `make_list(priceable_names)` |
| DEV-E11 | generic_engine (P3.5) | `initial_value` appears only from the first cash-payment date, in that payment's currency | §7.7 |
| DEV-E12 | impls (P3.5) + backtest_objects (P3.2: `_BACKTEST_END`) | `dt.date.today()` guards | compared with the backtest end date: `backtest.states[-1]` in impls:209/409/654, and `_BACKTEST_END.get()` (set by `run_backtest` for the run, reset in `finally`) in `ScaledTransactionModel.get_unit_cost` |
| DEV-E13 | generic_engine (P3.5) | cash currency via `map_ccy_name_to_ccy` of the unit's long name (unknown → `None`) | the ISO code from the unit |
| DEV-E14 | generic_engine (P3.5) + risk/results (P1.3) + backtest_objects (P3.2) | risk list and PRR `risk_measures` built from `set(...)`, so the column order is non-deterministic | ordered de-duplication: user `risks`, then `strategy.risks`, then pnl risks, then the price measure. `PortfolioRiskResult.__add__` keeps the order; `get_risk_summary_df` reindexes to `self.risks` |
| DEV-E15 | generic_engine (P3.5) + assets/pricing (P2.2) | multi-currency only through server-side `result_ccy` | `result_ccy` converts through the FX config; otherwise the gs errors, with a hint (§7) |
| DEV-E16 | generic_engine + impls (P3.5) | gs prices every weekday server-side | missing-market handling for grid and off-grid dates (§9.5) |
| DEV-E17 | impls (P3.5, ExitTradeActionImpl) | the exited position's TCE relocation (`transaction_cost_entries[s].append/remove`, `cp.transaction_cost_entry.date = s`) runs unconditionally; crashes with `list.remove(x): x not in list` when an initial_portfolio position (whose CashPayments carry `transaction_cost_entry=None`, DEV-E4's `_resolve_initial_portfolio`) is exited via ExitTradeAction/ExitAllPositionsAction | skipped when `cp.transaction_cost_entry is None` — nothing to relocate |
| DEV-E18 | backtest_objects (`ScaledTransactionModel.get_unit_cost`, P3.2) + generic_engine (P3.5 sets `_RESULT_CCY`) | `result_ccy`'s server-side conversion (gs's only multi-currency mechanism) never reaches a transaction-cost model's `scaling_type`, so a `ScaledTransactionModel(scaling_type=<RiskMeasure>)` prices in the measure's own currency even when `run_backtest(result_ccy=...)` is set, and `Total` silently sums it in with the converted Price/Cash | mirrors the `risks`-list rewrite (DEV-E15): while a run has `result_ccy` set, a `ParameterisedRiskMeasure` scaling_type is rewritten to `scaling_type(currency=result_ccy)` before pricing, via a `_RESULT_CCY` ContextVar set for the run's duration (a plain, non-parameterised `RiskMeasure` scaling_type is left in its own currency, same as the `'notional_amount'`-style string case) |

**Results (R03§4.4)**

| ID | File (task) | gs behaviour | pricebt behaviour |
|---|---|---|---|
| DEV-R1 | backtest_objects (P3.2) + generic_engine `_handle_cash` (P3.5) | `result_summary` ffills the previous PV onto a date with no holdings while cash already includes the exit proceeds, so `Total` double counts | A row date `d` is *flat* iff no position is held on it: for a grid date, `portfolio_dict[d]` is empty; for a non-grid cash/TC date, no position in `portfolio_dict[p]` (p = the last grid date < d) has a final date > d. Flat dates get PV 0 and risk 0 before the ffill. On a non-grid date that is **not** flat, `_handle_cash` also prices the continuing positions (those in `portfolio_dict[p]` with final date > d) for all `risks` into `results[d]`, so the row shows their true PV and `strategy_as_time_series` gains rows for them |
| DEV-R2 | backtest_objects (P3.2) | `get_risk_summary_df` is computed once and never invalidated | recomputed on each call |
| DEV-R4 | backtest_objects (P3.2) | bucketed cells are ffilled on flat dates | follow DEV-R1 (zero) |
| DEV-R5 | risk/results (P1.3) | bucketed frames are ordered by `sort_risk`/`point_sort_order` (asset-class regexes, relative to today) | the config's bucket order, first appearance across groups; no point parsing |

(DEV-R3 from revision 1 was removed: 2.1.17 already returns an empty frame when there are no results.)

**Instruments / risk**

| ID | File (task) | gs behaviour | pricebt behaviour |
|---|---|---|---|
| DEV-I1 | instrument (P2.1) | scaling edits size fields (`notional_amount`, `pay_or_receive`, `fee`) | signed `quantity_` multiplier (§5.4); kwargs never edited |
| DEV-I2 | instrument/portfolio (P2.1) | `strategy_as_time_series` static data shows the resolved gs fields | resolved terms plus a `quantity_` column |
| DEV-I3 | instrument (P2.1) + impls (P3.5) | `to_dict()` identity in ExitTradeAction: an unhashable dict, a latent crash | `instrument_identity()` tuple |
| DEV-I4 | assets/pricing (P2.2) | `IRDelta(aggregation_level=Type)` returns a small DataFrame | a `FloatWithInfo` for Type/Asset/Class; a bucketed `DataFrameWithInfo` for None/Point (§8.1 rule 5) |
| DEV-I5 | assets/pricing (P2.2) | an unparameterised currency-bearing risk is in USD (per the IRDelta docstring) | the function's currency (decision 0.5) |
| DEV-I6 | none (docs) | instrument strings (`'100k'`, `'ATM+25'`, `'=solvefor(...)'`) are parsed server-side | not parsed by pricebt; the asset's `resolve` decides |
| DEV-I7 | assets/pricing (P2.2) | `IRFwdRate` is in percent | whatever unit the asset function declares; the shipped configs use bp (MUST-4); intensive units are not multiplied by quantity (§5.4) |
| DEV-I8 | assets/pricing (P2.2) | measure parameters beyond currency and aggregation level are honoured server-side | `NotSupportedError` (§8.1 rule 3a) |

**Kept on purpose** (documented in DEVIATIONS.md as "parity kept"):
- ALL_OF short-circuits, while ANY_OF evaluates every child.
- Portfolio `'len'` counts dates.
- `check_barrier` uses strict `>`/`<`.
- The MR window excludes today, uses the sample std (ddof=1), and a zero std gives inf, which fires.
- `get_data_range` is `(start, end]`; an int means the last N points strictly before.
- Hedges on one date are all sized against the same pre-hedge risk.
- The global `Action{N}` counter.
- The holding window `create_date <= s < final_date`.
- Entry cash `−PV(create)` and exit cash `+PV(final)`; coupons between marks are not booked.
- `trade_ledger`'s `Long Short` is the direction of the opening payment.

---

## 12. Verification strategy

### 12.1 Test tiers (pytest markers)

| Marker | Needs | Runs in CI | Content |
|---|---|---|---|
| `core` (the default for unmarked tests, via conftest) | nothing external | yes | unit and scenario tests on the toy library |
| `notebook` | nbclient, ipykernel | yes (slow) | executes `notebooks/040304_mean_reversion_toy.ipynb` |
| `live_arbs` | `PRICEBT_LIVE_ARBS=1` and the ARBS checkout | **no**: opt-in, and the first run needs the user's approval | loads the ARBS config, prices a few dates, runs a short 040304 |

### 12.2 Guards (`tests/guards/`)

1. **Import scan (AST).** Every `import`/`from` in `src/pricebt/**/*.py` must have a root in `sys.stdlib_module_names ∪ {numpy, pandas, yaml, dateutil, tqdm, pricebt}`.
2. **Token scan (tokenize-based).** Read each `src/pricebt/**/*.py` with `tokenize` and drop COMMENT tokens, both whole-line and trailing.
   - Scan every NAME, STRING and FSTRING_MIDDLE token (Python ≥ 3.12 splits f-strings), docstrings included, with `re.compile(r"(?<![A-Za-z])(arbs|rateslib|quantlib|irswapsmdp|mdp|rlirswapcurve|bulk_get_data|build_irswap|ignore_cache_miss|supabase|nojumps|eris|erisfutures|bloomberg|refinitiv|marquee)(?![A-Za-z])", re.I)`. It uses **letter look-arounds, not `\b`**, so `ARBS_SUPABASE_ENABLED` is caught while `Series` is not.
   - Scan NAME, non-docstring STRING and FSTRING_MIDDLE tokens for `gs_quant`.
   - Each hit fails the guard with file:line.
3. **Import blocker.** A subprocess installs a `sys.meta_path` finder that raises `ImportError` for `gs_quant`, `rateslib`, `QuantLib`, `MDP`, `Query`, `Caching` and `dataclasses_json`. It then imports every pricebt module, and (from P4) runs the toy MR backtest end to end.
4. **Asset-agnostic scan (MUST-5).** Tokenize-scan (as in item 2) **only** `src/pricebt/assets/**`, `src/pricebt/markets/**`, `src/pricebt/risk/results.py` and `src/pricebt/risk/transform.py`. Fail on `(?i)(?<![a-z])(notional|tenor|swaption|swap|fixed_rate|termination_date|expiration_date|pay_or_receive|strike|dv01|pv01|par_rate|sofr|libor|estr)(?![a-z])`. `instrument/`, `risk/__init__.py` and `backtests/` are not scanned, because gs names and the ported gs text legitimately live there.
5. **Import order.** Each pricebt module is imported alone, in a fresh subprocess, in reverse DAG order (§3.2).
6. **Skeleton.** The set of `src/pricebt/**/*.py` paths equals a literal list copied from §3.2.
7. **Non-vacuity twins.** Every scanning guard has twins fed with synthetic files:
   - must fail: `import rateslib`; `os.environ["ARBS_SUPABASE_ENABLED"]`; `key = "usd_sofr_eris_rlbasic"`; `getattr(m, "bulk_get_data")`; `raise ValueError(f"ARBS served {d}")`; `terms['notional']` (asset-agnostic scan);
   - must pass: `pd.Series`; `class OisFixingCashAccrualModel`; `ois_fixings = {}`; `x = 1  # uses ARBS` (a trailing comment).

   This applies the global CLAUDE.md rule: verify the checker on a known answer.

### 12.3 gs API parity snapshot (MUST-2)

- **`tools/gs_api_snapshot.py`** runs with **the base python** (gs 1.5.4) and writes two files:
  - `tests/data/gs_api_1_5_4.json`. For each in-scope symbol it records the kind, the `inspect.signature` parameters `(name, kind, default repr)` in order, the dataclass fields `(name, init, default repr)` in order, the public methods and properties, enum `[(name, value)]`, and, for risk measures, `(class name, name, measure_type)`. The in-scope symbols are:
    - every public class/function of `gs_quant.backtests.{strategy, triggers, actions, data_sources, backtest_objects, backtest_utils, generic_engine, core}` that is in scope per §9.3;
    - the 7 generated instrument classes;
    - the `gs_quant.risk` measures in §8.1;
    - the `gs_quant.common` enums;
    - `gs_quant.markets.{PricingContext, HistoricalPricingContext}` and `gs_quant.markets.portfolio.Portfolio`;
    - the `gs_quant.datetime` functions;
    - `gs_quant.session.GsSession.use` and `gs_quant.session.Environment`.
  - `tests/data/gs_instruments_1_5_4.json`: for every dataclass in `gs_quant.target.instrument`, its `asset_class`, `type_` and `[(field, init, default_repr, annotation_str)]`, feeding `tools/gen_gs_fields.py`.
- **`tests/test_gs_api_parity.py`** (tier `core`; **it never imports gs_quant**) compares each pricebt symbol with the snapshot. Allowed differences are listed in `tests/data/gs_api_exceptions.yaml` as `{symbol, aspect, reason, dev_id}`, and any unlisted difference fails. Expected exceptions:
  - the trailing keyword-only `pricebt_asset`, `quantity_` and `**kwargs` on instrument constructors;
  - the stubs;
  - the trailing fields 2.1.17 appended;
  - the 2.1.17-only measures `FXDeltaLocalCcy`/`FXGammaLocalCcy`/`FXVegaLocalCcy`, which are absent from 1.5.4 and so are extra symbols, not mismatches.
- The JSON is regenerated only with the base python, and its header records `gs_quant.__version__` and the date.

### 12.4 Golden and scenario tests (on the toy library; exact numbers derived from inputs)

| Test | What it asserts |
|---|---|
| `test_relative_date.py` | every row of R04§8.3 and R01§5.5: rules, schedules, the holiday case, a weekend start kept |
| `test_contexts.py` | the PricingContext stack, inheritance of pricing_date and csa_term, `HistoricalPricingContext.date_range` rules |
| `test_mean_reversion_golden.py` | the 19-row table of R01§6.5.9 (triggered, scaling, position), fed through `GenericDataSource` + `MeanReversionTriggerRequirements` |
| `test_generic_data_source.py` | the R01§7.4 v2 spec: fill_forward returns the previous value (2.0, not 5.0), interpolate by position, no mutation, index normalisation, the `get_data_range` rules |
| `test_risk_results.py` | the PRR contract (§8.2): `.get`, `.futures` rebuild after deleting an index, the `__getitem__` errors, `__add__` overlap and ordered union, `transform`, `to_frame` bucket sums, the 6-column bucketed frame, ';'-keyed points, order preserved |
| `test_result_shapes.py` | R03§9 Cases A, B (with DEV-R1: `Total` = 2290, not 3790) and E, as exact frames, built by hand (never through GenericEngine); Case B's non-grid date is *flat* (DEV-R1's zeroing half only) |
| `test_engine_smoke.py` | a periodic '1m' roll: ledger names, dates, `Trade PnL = Close + Open`, the `Total` identity on every row, entry/exit cash = ∓PV priced independently, the holding window, determinism; a weekly grid with a mid-week off-grid exit while another trade is held (DEV-R1's non-grid, non-flat "price the continuing position fresh" rule), driven through the real engine |
| `test_engine_periodic_roll.py` | the 040300 swap variant |
| `test_engine_hedge.py` | 040310 variant (toy EUR, `HedgeAction(IRDelta(aggregation_level='Type'), hedge, csa_term='EUR-OIS')`): the book's IRDelta ≈ 0 after each daily hedge; `pricebt_csa` is seen only while the hedge is resolved; the `risk_transformation=ResultWithInfoAggregator()` variant |
| `test_engine_risk_trigger.py` | 040305-style `StrategyRiskTrigger` + `[ExitTradeAction(), AddTradeAction(...)]` fires again after the first rebalance (DEV-E1) |
| `test_engine_exit_trade.py` | `ExitTradeAction` by priceable name, including names with underscores and held hedges (DEV-E5); same-day in/out gives direction 0 |
| `test_engine_scaled_add.py` | `AddScaledTradeAction` size / risk_measure / NAV; `dated_priceables` |
| `test_engine_weighted_trade.py` | `AddWeightedTradeAction` over two toy swaps: quantity = |risk_i|/Σ|risk| × total_size / unit size, `Weighted_<name>` names, per-instrument cash |
| `test_engine_early_exit.py` | `EarlyExitPositionLimitScaledAction`: `early_exits` caps the final date; `max_concurrent_pos` drops order days |
| `test_engine_rebalance.py` | `RebalanceAction` with `size_parameter=size_attribute`; the sequence 100 → 50 → 50 books nothing on the third rebalance, and the held size is 50 |
| `test_engine_holidays.py` | 040311: `holiday_calendar` as a tuple **and** as a list (DEV-T16) on `run_backtest` and on the action |
| `test_engine_swaption.py` | the toy swaption (MUST-5 check): `AddTradeAction(swaption, 'expiration_date')` closes on each resolved expiry; the `Sell` price is minus the `Buy` price; `'A-50'` strike pinned at resolve |
| `test_multi_currency.py` | USD + EUR toy assets + toy FX: a mixed book without `result_ccy` raises the gs errors with the hint; with `result_ccy='USD'`, every column equals hand-computed conversions; cross-currency hedging with `IRDelta(currency='USD')`; `initial_value` seeding rules (§7.7) |
| `test_pnl_bps.py` | a hand computation; NaN when the risk ≈ 0; the error when the column is absent; the retry with the `result_ccy` currency; the non-scalar error |
| `test_ladder.py` | the group call equals the sum of per-trade calls (quantities 2.5 and −0.7); the portfolio function is called once per (asset, date) after an ExitTradeAction-style rebuild and a `+` merge; the frame shape and labels; order preserved |
| `test_missing_market.py` | `'drop'` warns, lists and removes grid dates; recomputed start and end; a weekend `start` with an initial portfolio; an exit on a toy hole date is rolled forward (DEV-E16) and recorded; `next schedule` on a hole; a final date past the end on a hole is not booked and does not error; all dates missing → raise; `'raise'` raises |
| `test_asset_config.py` | schema validation errors (unknown key with a suggestion; bad unit; missing Price; dangling function; `market.key` conflicts), compile-time syntax errors, lazy import, isolated namespaces, `AssetEvaluationError` content, plain-data validation of `resolve`, `test_resolve_pins_dates` |
| `test_instrument.py` | real gs signatures (the first 31 IRSwap params equal the snapshot's); camelCase; enum coercion and errors; eq/hash stable across first pricing; clone rules; scale rules; `copy.deepcopy`/`pickle` with no session; `hasattr(swap, '1m') is False`; `to_dict`/`as_dict`; no extension-name collisions |
| `test_pricing_service.py` / `test_pricing_cache.py` | the resolution-state rules; market evaluated once per (key, date, csa); hedge unit and scaled copy share the unit-value cache; `build_on` both modes, including the resolve_date trade built on the resolution market; csa in cache keys; FX direction; intensive units not scaled; `reset()` |
| `test_040304_toy.py` | the converted notebook code run as a script on the toy asset, with the frame shapes of R05§6.5 |

### 12.5 Mutation check

At each phase gate, the implementer runs `tools/mutcheck.py` on at least 3 hand-picked one-line mutations per phase and confirms that a named test fails for each. Examples: flip the `<` in the MR short close, drop DEV-R1's zeroing, reverse the FX direction, remove `csa` from the market cache key, or skip pinning in the toy resolve.

---

## 13. Extensibility test: adding swaptions later (MUST-5)

Nothing in §4–§11 is swap-specific, and the CI toy swaption (§12.4) proves it on a small scale. The real USD swaption needs:
1. **The class.** `IRSwaption` already exists in `pricebt.instrument` as generated data, with gs's field order. **No code change.**
2. **The config.** Write `configs/assets/usd_sofr_swaption.yaml` with:
   - `instrument: IRSwaption` and `match: {notional_currency: USD}`;
   - a `market` that returns the curve **and** a vol surface as one object (e.g. a `SimpleNamespace` built in `code`), under the swaption asset's own market key, because its expression differs from the swap asset's;
   - a `resolve` that pins `expiration_date`, `termination_date` and the strike;
   - functions `npv`, `dv01`, `vega` (`unit: ccy_per_bp`, per bp of normal vol) and `gamma`;
   - `attributes: {expiration_date: 'resolved["expiration_date"]'}`, so that `AddTradeAction(option, 'expiration_date')` works;
   - `risk_measures: {Price: npv, IRDelta: {scalar: dv01}, IRVega: {scalar: vega}}`.

   A bucketed IRVega needs a `returns: buckets` portfolio function with `';'`-joined `'expiry;tenor'` keys (§8.2).
3. **Sign and strike.** pricebt never reads `buy_sell`.
   - The swaption `resolve` MUST fold it into a signed resolved notional: `sign = -1 if buy_sell == 'Sell' else +1`, times the sign of `notional_amount` (040307 sells with a negative notional).
   - It MUST reject `pay_or_receive` values it does not price (e.g. `Straddle`, unless it prices it as payer + receiver).
   - The engine's `quantity_` stays a pure position multiplier.
   - The strike grammar (`'ATM'`, `'A-50'` = ATM − 50bp, decimals; `'=solvefor(...)'` → raise) is parsed only in `resolve`, which pins the strike as a decimal.
4. **Notebooks.** Notebooks 040303, 040305, 040307 and `Backtesting.ipynb` then port (R05§4).

A design review MUST fail any change that makes the engine, the pricing layer or the results aware of an asset class. The asset-agnostic guard (§12.2 item 4) catches the obvious cases.

---

## 14. Later work (explicitly not in this plan)

- The real swaption asset config (the §13 walkthrough).
- A ladder-hedge action: a vector hedge over several instruments, using the `bucketed` portfolio function.
- Multi-curve ladder labels, i.e. a per-row coordinate override for trades with risk on several curves.
- EUR/GBP/JPY real-data configs. ARBS serves these only through `GSQUANT-RL`, which imports gs_quant inside ARBS and ends 2026-08-03 (R06§2). That is acceptable inside a user config, but it needs its own hazard review.
- Coupon cash between marks. gs books only entry and exit PVs, so a coupon paid while a trade is held leaves the PV without reaching cash, and gs has the same property. If wanted, add an optional `cashflows` function plus a pricebt cash hook; that is a deviation, so ask first. Config-only workarounds already work: a total-return `npv`, or pinning `entry_price` in `resolve` for margined futures.
- A PredefinedAssetEngine equivalent, and JSON serialisation.

---

## Appendix A — The ARBS example asset config (`configs/assets/usd_sofr_ois_interest_rate_swap.yaml`)

Every ARBS method below is verified in R06§3, and signs and units in R06§3.5. The review re-checked this appendix against the ARBS source. The implementer copies this file, then runs the live checks in IMPLEMENTATION_PLAN P5.2, **only after the user approves** the first live run.

```yaml
schema_version: 1
asset: usd_sofr_ois_interest_rate_swap
description: >
  USD SOFR OIS swap priced by ARBS on Eris EOD curves. Reads the ARBS curve store only: no curve-store writes and no
  network for dates <= _LAST_SAFE (2026-08-20); each call creates an empty today-dated folder in ARBS's fixings cache.
  Instrument: pricebt.instrument.IRSwap (gs fields). Units: npv USD; dv01 USD per +1bp (payer > 0); rates in bp.
instrument: IRSwap
match: {notional_currency: USD}
currency: USD
defaults:
  pay_or_receive: Receive          # gs server default (R04 s1); coerced to PayReceive.Receive (value 'Rec')
  termination_date: 10y
  notional_currency: USD
  fixed_rate: ATM
  notional_amount: 1.0e6           # gs does not document one; pricebt makes it explicit

imports: |
  import os, sys, re, contextlib, io, warnings
  os.environ["ARBS_SUPABASE_ENABLED"] = "0"          # MUST precede ANY ARBS import (R06 s6.2): no production database
  if r"C:\Users\chris\clee\ARBS" not in sys.path:
      sys.path.insert(0, r"C:\Users\chris\clee\ARBS")
  import datetime as _dt
  import pandas as pd
  with warnings.catch_warnings():
      warnings.simplefilter("ignore")                  # rateslib LicenceNotice on import
      import rateslib as rl
  from MDP.IRSwaps.IRSwapsMDP import IRSwapsMDP
  from Query.IRSwaps.backends.rateslib.RLIRSwapCurve import RLIRSwapCurve
  import Caching.supabase_engine as _arbs_se
  if _arbs_se.SUPABASE_ENABLED:
      raise RuntimeError("ARBS was imported before ARBS_SUPABASE_ENABLED=0 took effect; restart the kernel")

code: |
  _MDP = IRSwapsMDP(source="ERIS_EOD_LIVE-RL_BASIC")   # store-backed Eris, 2020-07-01..2026-08-20 (R06 s1.3). Construct ONCE.
  # _MDP = IRSwapsMDP(source="ERIS_EOD_LIVE-RL_BASIC-NOJUMPS")   # DO NOT enable without user approval: network + overwrites
  #                                                              # curve-store partitions USD-SOFR-1D/date=<d> (R06 s1.3, s6.1)
  _CURVE = "USD-SOFR-1D"
  _LAST_SAFE = _dt.date(2026, 8, 20)   # (1) last stored RL_BASIC curve; (2) bulk_get_data reads SOFR fixings for d BEFORE the
                                       # store lookup and ignore_cache_miss does not protect it: any d whose previous US business
                                       # day is missing from ARBS's fixings cache (newest 2026-09-04) triggers a NY Fed/FRED download
                                       # and rewrites the fixings cache. Raise only after a deliberate refresh approved by the user.
  _ATM = re.compile(r"^\s*(atm|a)\s*(?:([+-])\s*(\d+(?:\.\d+)?))?\s*$", re.I)   # 'ATM', 'ATM+25', 'a-100' (bp)

  def load_market(d):
      if d >= _dt.date.today() or d > _LAST_SAFE:
          return None                  # today -> 'live' (network); after _LAST_SAFE -> fixings download (network + writes)
      if d.weekday() >= 5:
          return None
      try:
          got = _MDP.bulk_get_data(dict(curve_name=_CURVE, timestamps=[d], ignore_cache_miss=True))   # fresh dict (ARBS pops keys)
      except ValueError as e:
          if "resolved to an empty collection" in str(e):
              return None              # US GovernmentBond holiday (QuantLib calendar): ARBS drops the date, then raises
          raise
      m = got.get(d)
      if m is None:
          return None                  # store hole / sanity-quarantined day
      if m.reference_date().date() != d:
          raise ValueError(f"served reference date {m.reference_date().date()} for {d}")   # validate the serve (R06 s1.6)
      return m

  def _tenor(x):
      return isinstance(x, str) and re.fullmatch(r"\d+[dwmyDWMY]", x.strip()) is not None

  def _effective(m, eff):
      if eff is None or (isinstance(eff, str) and eff.strip().lower() in ("spot", "0b")):
          return m.spot_date()                                   # reference + 2b (R06 s3.2)
      if isinstance(eff, str):
          return m.calendar_advance(m.spot_date(), eff)          # forward start counted FROM SPOT (not build_irswap(fwd=))
      return pd.Timestamp(eff).to_pydatetime()

  def _sign(por):
      s = str(getattr(por, "value", por)).strip().lower()        # pricebt enum (value 'Rec'), or a raw string
      if s == "pay": return 1.0
      if s in ("rec", "receive", "receiver"): return -1.0
      raise ValueError(f"pay_or_receive must be Pay or Receive, got {por!r}")

  def resolve_swap(m, kw):
      eff = _effective(m, kw.get("effective_date"))
      term = kw["termination_date"]
      notional = float(kw["notional_amount"]) * _sign(kw["pay_or_receive"])   # '100k'-style strings are rejected by float()
      if _tenor(term):
          probe = m.build_irswap(effective_date=eff, tenor=term.upper(), notional=notional)   # struck at par (sentinel)
      else:
          probe = m.build_irswap(effective_date=eff, maturity_date=pd.Timestamp(term).to_pydatetime(), notional=notional)
      fr = kw.get("fixed_rate", "ATM")
      mm = _ATM.match(fr) if isinstance(fr, str) else None
      if mm:
          spread_bp = float(mm.group(3) or 0.0) * (-1.0 if mm.group(2) == "-" else 1.0)
          k = float(m.fair_rate(probe)) + spread_bp / 1e4          # decimal
      elif isinstance(fr, str):
          raise ValueError(f"unsupported fixed_rate {fr!r} (use 'ATM', 'ATM+x', 'a-x' in bp, or a decimal)")
      else:
          k = float(fr)                                              # decimal, gs convention (0.0325 = 3.25%)
      if k == 0.0:
          raise ValueError("a 0.0 strike collides with the ARBS par sentinel (R06 s3.3)")
      return {
          "effective_date": m.effective_date(probe).date(),
          "termination_date": m.maturity_date(probe).date(),
          "fixed_rate": k,                                           # decimal
          "notional": notional,                                      # signed, + = payer
      }

  def build_swap(m, r):
      return m.build_irswap(effective_date=pd.Timestamp(r["effective_date"]).to_pydatetime(),
                            maturity_date=pd.Timestamp(r["termination_date"]).to_pydatetime(),
                            fixed_rate=r["fixed_rate"], notional=r["notional"])

  def alive(m, t):                          # rate/risk functions: the swap still has accrual periods ahead
      return m.maturity_date(t).date() > m.reference_date().date()

  def unsettled(m, t):                      # npv only: the final coupon pays at maturity + PaymentLag 2b (R06 s2)
      return pd.Timestamp(m.calendar_advance(m.maturity_date(t), "2b")).date() > m.reference_date().date()

  def remark(m, t):                        # the same swap rebuilt on market m (m's fixings); memo per market (R06 s3.4)
      memo = m.__dict__.setdefault("_pricebt_remark", {})
      hit = memo.get(id(t))
      if hit is not None and hit[0] is t:
          return hit[1]
      r = m.build_irswap(effective_date=m.effective_date(t), maturity_date=m.maturity_date(t),
                         fixed_rate=m.fixed_rate(t), notional=m.notional(t))
      memo[id(t)] = (t, r)
      return r

  def _carry_ok(m, t, h):                  # mirrors ARBS's own raise conditions (_carry_roll.py:167-183)
      start = pd.Timestamp(m.calendar_advance(m.spot_date(), h))
      return pd.Timestamp(m.effective_date(t)) >= start or pd.Timestamp(m.maturity_date(t)) > start

  def _roll_ok(m, t, h):                   # _carry_roll.py:140-162
      spot = pd.Timestamp(m.spot_date())
      aged_eff = max(pd.Timestamp(m.calendar_advance(m.effective_date(t), "-" + h)), spot)
      return pd.Timestamp(m.calendar_advance(m.maturity_date(t), "-" + h)) > aged_eff

  def _risk_model(m, tenors):              # par-swap risk curve + Solver, memoised per (market, tenors) (R06 s4 option B)
      memo = m.__dict__.setdefault("_pricebt_risk", {})
      if tenors in memo:
          return memo[tenors]
      dense, spot, ref = m.handle(), m.spot_date(), m.reference_date()
      nodes, pars = {ref: 1.0}, []
      for b in tenors:
          p = m.build_irswap(effective_date=spot, tenor=b, notional=1_000_000)
          pars.append(float(m.fair_rate(p)))
          mat = m.maturity_date(p)
          nodes[mat] = float(dense[mat])
      risk = rl.Curve(nodes=dict(sorted(nodes.items())), convention=dense.meta.convention,
                      calendar=dense.meta.calendar, interpolation="log_linear", id=f"{m.id()}-RISK")
      meta = dict(m.meta()); meta["id"] = risk.id
      rh = RLIRSwapCurve(rl_curve_id=risk.id, rl_curve_handle=risk, fixings=m.index(), meta_data=meta)
      insts = [rh.build_irswap(effective_date=spot, tenor=b, fixed_rate=k, notional=1_000_000) for b, k in zip(tenors, pars)]
      with contextlib.redirect_stdout(io.StringIO()):
          sv = rl.Solver(curves=[risk], instruments=insts, s=[k * 100.0 for k in pars],
                         instrument_labels=list(tenors), id=risk.id, func_tol=1e-8, conv_tol=1e-10)
      if sv.result["status"] != "SUCCESS":
          raise RuntimeError(f"risk curve calibration failed: {sv.result}")
      memo[tenors] = (rh, sv)
      return memo[tenors]

  def delta_ladder(m, trades, weights, tenors):
      tenors = tuple(tenors)
      live = [(t, w) for t, w in zip(trades, weights) if alive(m, t) and w != 0.0]
      if not live:
          return {b: 0.0 for b in tenors}
      rh, sv = _risk_model(m, tenors)
      rebuilt = [rh.build_irswap(effective_date=m.effective_date(t), maturity_date=m.maturity_date(t),
                                 fixed_rate=m.fixed_rate(t), notional=m.notional(t) * w) for t, w in live]
      d = rl.Portfolio(rebuilt).delta(solver=sv)
      blk = d.xs("instruments", level=0) if "instruments" in set(d.index.get_level_values(0)) else d
      s = blk.iloc[:, 0].astype(float); s.index = s.index.get_level_values(-1)
      return {b: float(s.get(b, 0.0)) + 0.0 for b in tenors}   # USD per +1bp of each pillar par rate, payer > 0

market:
  expr: 'load_market(pricebt_date)'        # -> RLIRSwapCurve, or None (weekend, US holiday, store hole, beyond _LAST_SAFE)
  key: usd_sofr_eris_rlbasic
resolve:
  expr: 'resolve_swap(market, kwargs)'
trade:
  expr: 'build_swap(market, resolved)'
  build_on: resolve_date                   # npv/pv01 are safe on later markets; other functions use remark() (R06 s3.4)
functions:
  npv:        {expr: '0.0 if not unsettled(market, trade) else market.npv(trade)', unit: ccy}
  dv01:       {expr: '0.0 if not alive(market, trade) else market.pv01(trade)', unit: ccy_per_bp}   # pv01 safe as-is (R06 s3.4)
  par_rate:   {expr: 'float("nan") if not alive(market, trade) else market.fair_rate(remark(market, trade)) * 1e4', unit: bp}
  fixed_rate: {expr: 'market.fixed_rate(trade) * 1e4', unit: bp}
  carry_1m:   {expr: 'float("nan") if not (alive(market, trade) and _carry_ok(market, trade, "1m")) else market.carry_bps_running(remark(market, trade), "1m")', unit: bp}
  roll_1m:    {expr: 'float("nan") if not (alive(market, trade) and _roll_ok(market, trade, "1m")) else market.roll_bps_running(remark(market, trade), "1m")', unit: bp}
portfolio_functions:
  delta_ladder:
    expr: 'delta_ladder(market, trades, weights, ("2Y", "5Y", "10Y", "30Y"))'
    unit: ccy_per_bp
    returns: buckets
    labels: {mkt_type: IR, mkt_asset: USD-SOFR-1D, mkt_class: OIS}
attributes:
  effective_date:   'resolved["effective_date"]'
  termination_date: 'resolved["termination_date"]'
  notional_amount:  'abs(resolved["notional"]) * pricebt_quantity'   # signed by quantity: rebalance netting (s9.4)
size_attribute: notional_amount
risk_measures:
  Price: npv
  IRDelta: {scalar: dv01, bucketed: delta_ladder}      # IRDeltaParallel / IRDeltaLocalCcy fall back here (s8.1 rule 3)
  IRFwdRate: par_rate
```

Notes:
- `carry_1m` and `roll_1m` are per-unit rate measures, receiver-positive whatever the trade's direction (ARBS ignores the notional sign). They are intensive (bp), so pricebt does not multiply them by quantity.
- Traps this config avoids (R06§3.2, §7):
  - `dv01`, `gamma` and `dollar_carry` on `RLIRSwapCurve` raise `NotImplementedError`;
  - `resolve_pricable` turns a payer into a receiver;
  - `build_irswap(fwd=<tenor>)` counts from the reference date, not spot;
  - `fair_rate` on an un-remarked trade from an earlier market uses stale fixings;
  - a zero strike means par;
  - `bulk_get_data` raises on US holidays;
  - fixings downloads happen after the cache's newest date.

## Appendix B — The converted 040304 notebook (acceptance text)

```python
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

PricebtSession.use(assets=["configs/assets/usd_sofr_ois_interest_rate_swap.yaml"])    # replaces GsSession.use(...)

start_date = date(2021, 6, 1)
end_date = datetime.today().date()          # dates past the last stored curve are dropped with a warning (decision 0.4)

swap = IRSwap(pay_or_receive=PayReceive.Pay, termination_date='10y', notional_currency=Currency.USD,
              notional_amount=1e4, fixed_rate='ATM', name='swap_10y')

# replaces the GS Dataset: the 10y par rate (bp) of a fresh ATM 10y swap on every business day
s = measure_series(IRSwap(termination_date='10y', notional_currency='USD'), 'par_rate', start_date, end_date, frequency='1b')

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
backtest.result_summary
pd.DataFrame({'Generic backtester': backtest.result_summary['Cumulative Cash'] + backtest.result_summary[Price]}).plot(
    figsize=(10, 6), title='Performance')
```

Everything from `action = ...` onwards is identical to the gs notebook (R05§6.3). The toy version differs only in the asset path and the dates.
