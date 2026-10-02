# pricebt v2: requirements and design

Status: **implemented** (design revision 2, after an adversarial review; the decisions in §0 were confirmed on 2026-09-27). Extended by the interest-rate pricing and risk work on branch `v2-ir-risk` ([`IR_RISK_DESIGN.md`](IR_RISK_DESIGN.md), which says where it changes a rule here and gives the DEV id).
Branch: `v2-redesign`, in the worktree `C:\Users\chris\clee\gsquant-temp-claude\pricebt-v2`.
The v1 tree is preserved at git tag `v1-final` (branch `main`). Nothing from v1 needs to be kept "just in case"; it is all recoverable.

Companion documents:

| File | What it is |
|---|---|
| `docs/v2/README.md` | Entry point and reading order for the implementer |
| `docs/v2/DESIGN.md` | This file: requirements, architecture, contracts |
| `docs/v2/IMPLEMENTATION_PLAN.md` | Phased task list, file ownership, acceptance commands, gates |
| `docs/v2/IR_RISK_DESIGN.md` | The IR pricing-and-risk extension: measure catalogue, per-instrument measure contracts, Portfolio/results parity, `Bond`, swaption/bond P&L decomposition, `CloseMarket`/`PnlExplain` (evidence: `research/09..15`) |
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
| 0.6 | Wording of MUST-5 ("adding an asset = config only") | Every gs instrument class that pricebt mirrors is **generated data** (`tools/gen_gs_fields.py` reads a gs 1.5.4 snapshot). Adding a gs class later means adding its name to the generator's list and regenerating. That is a data change, with no hand-written code. v2 generates 8 classes (`Bond` added by the IR risk work, `IR_RISK_DESIGN.md` §4.1). | The earlier text allowed "a ≤10-line field list" written by hand. Generating from the snapshot removes hand-transcription errors in enum coercion tags (review F04). | Hand-written field lists |

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
> A later asset type must be addable by writing a new YAML asset config. If gs has an instrument class pricebt does not yet mirror, the class is added as generated data (decision 0.6). The engine, the pricing layer and the result objects MUST NOT special-case any asset class. **Done for swaptions and bonds:** §13 was the design test, and the toy swaption (`tests/assets/toy_usd_swaption.yaml`) and toy bond (`tests/assets/toy_usd_bond.yaml`) map their whole measure contracts and run in CI with no swaption- or bond-specific code in pricebt (§12.4, IR_RISK_DESIGN §6).

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
  docs/v2/                         DESIGN.md, IMPLEMENTATION_PLAN.md, README.md, DEVIATIONS.md, ASSET_CONFIG_GUIDE.md,
                                   IR_RISK_DESIGN.md, MERGE_NOTES_pnl_explain.md, DECISIONS_LOG.md, research/
  notebooks/
    src/*.py                                  percent-format sources built by tools/nb_build.py
    040304_mean_reversion_toy.ipynb           CI version (toy asset)
    040304_mean_reversion_usd_sofr_arbs.ipynb opt-in version (ARBS)
    ir_pricing_and_risk_toy.ipynb             IR pricing, risk, Portfolio and P&L decomposition on the toy assets
  src/pricebt/
    __init__.py                   __version__ = "2.0.0"; imports nothing eagerly except errors
    errors.py                     PricebtError, ConfigError, AssetEvaluationError, MarketDataUnavailable, NotSupportedError,
                                  UnsupportedMeasureError (IR_RISK_DESIGN §2.4)
    base.py                       EnumBase, Priceable marker, static_field, field_metadata, exclude_none, get_enum_value
    config/__init__.py            DisplayOptions(show_na=False) and the module default display_options (gs gs_quant.config)
    common.py                     gs enums (see P1.1; plus FiniteDifferenceMethod); RiskMeasure & ParameterisedRiskMeasure
                                  re-exported via module __getattr__
    progress.py                   salvaged v1 notebook-safe tqdm bar
    datetime/__init__.py          business_day_offset, is_business_day, prev_business_date, date_range, business_day_count, today
    datetime/relative_date.py     RelativeDate, RelativeDateSchedule
    risk/__init__.py              risk-measure classes and instances (§8.1) + re-exports of FloatWithInfo, SeriesWithInfo,
                                  DataFrameWithInfo, ErrorValue from risk.results, and of the risk.core helpers
    risk/results.py               RiskKey, FloatWithInfo, StringWithInfo, DictWithInfo, SeriesWithInfo, DataFrameWithInfo,
                                  ErrorValue, UnsupportedValue, MultipleRiskMeasureResult, PricingFuture, LazyFuture,
                                  PortfolioPath, PortfolioRiskResult
    risk/core.py                  aggregate_risk, aggregate_results, subtract_risk, sort_risk, combine_risk_key
                                  (gs gs_quant.risk.core); re-exported from risk
    risk/transform.py             Transformer (base), ResultWithInfoAggregator, GenericResultWithInfoTransformer
    risk/contracts.py             per-instrument measure contracts (IR_RISK_DESIGN §2, DEV-I11): MeasureRequirement, KINDS,
                                  CONTRACTS, FRAME_COLUMNS, contract_for, check, unsupported_block, validate_frame, base_measure
    markets/__init__.py           PricingContext, HistoricalPricingContext, and the seams _engine_calc / _engine_resolve
    markets/portfolio.py          Portfolio
    instrument/__init__.py        Instrument, ConfigInstrument, instrument_identity, and the generated gs classes
                                  (IRSwap, IRSwaption, FXOption, FXForward, EqOption, InflationSwap, Cash, Bond); re-exports
                                  OptionStyle, OptionType, Currency, PayReceive, BuySell, SwapClearingHouse, SwapSettlement
    instrument/_gs_fields.py      GENERATED by tools/gen_gs_fields.py from gs 1.5.4 (do not edit)
    assets/__init__.py            load_asset, load_fx, AssetConfig, FxConfig
    assets/yamlio.py              salvaged v1 hardened YAML loader (duplicate keys are errors; NO ${ENV} interpolation)
    assets/config.py              AssetConfig/FunctionSpec/RiskMapping/FxConfig dataclasses, schema validation, compile;
                                  unsupported_measures, returns: frame / scale_columns, instrument validation, contract
                                  check at load (IR_RISK_DESIGN §2.3)
    assets/namespace.py           AssetNamespace: exec(imports)+exec(code) once; eval(compiled expr, injected vars)
    assets/fx.py                  FxConfig loading and FX evaluation
    assets/registry.py            AssetRegistry: name → config; instrument → config matching; market-key sharing rules
    assets/pricing.py             PricingService (the only place values are produced)
    session.py                    PricebtSession, GsSession (no-op shim with the gs signature), Environment
    data/__init__.py              DataFrequency, Dataset (stub), measure_series (pricebt extension)
    target/__init__.py, target/common.py, target/measures.py, target/backtests.py   thin re-export shims (gs import paths)
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
    toylib/                       tiny closed-form "library" used ONLY by tests (never shipped): rates.py, swaption.py,
                                  irrisk.py (IR contract measures on top of rates.py), bond.py
    assets/                       toy asset configs: toy_usd_irs.yaml, toy_eur_irs.yaml, toy_usd_swaption.yaml, toy_fx.yaml,
                                  toy_usd_irs_full.yaml, toy_usd_bond.yaml
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
1. `errors` → `base` → `config` → `common`. `common` exposes the `RiskMeasure` re-export through a module `__getattr__`, not at import time. `config` imports nothing; `risk.results` reads `config.display_options` at call time.
2. → `datetime` → `risk` (measures) → `risk.results` → `risk.transform`. `transform` imports the result classes; `risk.results` MUST NOT import `transform`, and `PortfolioRiskResult.transform` duck-types `risk_transformation.apply(...)`. `risk.contracts` sits in the `risk`/`risk.results` tier (IR_RISK_DESIGN R2-22): it imports `risk`, and `risk/__init__` never imports it. `risk.core` sits in the same tier; `risk/__init__` imports it after `results`; it imports `risk.results` at top level, and `risk.results` imports it only inside function bodies (R2-22).
3. → `markets`. It MUST NOT import `portfolio`, `instrument`, `session` or `assets` at top level. `markets` only defines the contexts and the two seam functions, whose bodies import `pricebt.assets.pricing` at call time (§6.2).
4. → `instrument`. It imports `session` and `markets` only inside method bodies.
5. → `markets.portfolio` → `assets.{yamlio, config, namespace, fx, registry}` → `assets.pricing` → `session` → `data` → `backtests.*`. `assets.config` imports `instrument` (to validate `instrument:`) and `risk.contracts` at top level.

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
unsupported_measures:                 # optional; Bond only: contract measures (or forms) this library cannot compute, each
  IRDiscountDeltaParallel: "single-curve pricing; no discount-only bump"   #   with a reason (DEV-I11). An IRSwap/IRSwaption like
  IRDelta: {bucketed: "no curve ladder"}                                   #   this one cannot declare: it maps every contract measure (IR_STRICT_CONTRACT R3-0)
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
  cashflows: {expr: 'market.flows(trade)', unit: ccy, returns: frame, scale_columns: [payment_amount]}
  #  optional per entry: currency: EUR          (literal ISO code; default = asset currency)
  #                      scale_with_quantity: true|false   (default by unit, §5.4)
  #                      returns: scalar (default) | frame  (a table: DataFrame or list of row dicts, DEV-R11)
  #                      scale_columns: [...]   (returns: frame only: the columns quantity scales)
portfolio_functions:                  # optional: functions over a COLLECTION of this asset's trades on one market
  delta_ladder:
    expr: 'delta_ladder(market, trades, weights, ("2Y","5Y","10Y","30Y"))'
    unit: ccy_per_bp
    returns: buckets                  # buckets -> dict[str, float] or a list of row dicts (R2-14) | scalar -> float
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
  Cashflows: cashflows
```

**Measure contracts, `unsupported_measures:`, `returns: frame` / `scale_columns`** (IRSwap and IRSwaption configs must map every contract measure; Bond configs map or declare each; DEV-I11, [`IR_STRICT_CONTRACT.md`](IR_STRICT_CONTRACT.md)): the rules are [`IR_RISK_DESIGN.md`](IR_RISK_DESIGN.md) §2 and §00; the contract tables, generated from `src/pricebt/risk/contracts.py`, are in [`ASSET_CONFIG_GUIDE.md`](ASSET_CONFIG_GUIDE.md) "Measure contracts". `instrument:` must name a class exported by `pricebt.instrument` (else `ConfigError` with a did-you-mean).

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
| `pricebt_bump_size`, `pricebt_finite_difference_method`, `pricebt_local_curve`, `pricebt_scale_factor` | the measure's parameter value, or `None` when unset (`finite_difference_method` is a `FiniteDifferenceMethod`, a `str`) | functions, portfolio_functions | the requested measure's pass-through parameters (DEV-I10, §8.1 rule 3a) |
| `market_to`, `pricebt_to_date` | the market of the target date (the context's csa) and that date, or `None` for a measure that is not relative | functions, portfolio_functions | a relative measure's target (`PnlExplain(CloseMarket(date=...))`, DEV-M2); the mapped function must name one of them. Under `PricingContext(market=CloseMarket(date=t))`, `market` is the market of `t` and `pricebt_date` stays the pricing date (DEV-M1) |
| `base`, `quote` | `str` | FX config `rate` only | ISO codes |

`market.expr` receives only `pricebt_date`, `pricebt_timestamp`, `pricebt_datetime` and `pricebt_csa`. Names defined by `imports` and `code` are visible to every expression of that asset. Injected names shadow config names of the same spelling, so do not define helpers named `market`, `trade`, `kwargs`, and so on. Injected names are visible in nested scopes of the expression (§4.4), not inside `code` helpers.

**Attribute evaluation date.** `attributes` expressions are evaluated only on a **resolved** instrument, with `pricebt_date` = its resolution date, `pricebt_csa` = its resolution csa, and `market` = that date's market. The market is injected only if the compiled expression's `co_names` contain `market`, so a market is never fetched needlessly. The result is cached per (asset, frozen resolved terms, field, quantity). On an unresolved instrument, attributes are not evaluated, and `__getattr__` falls back to `kwargs` (§5.1).

### 4.4 Evaluation rules

- **Lazy execution.** `imports` and then `code` run **lazily, once per process per asset**, on the first evaluation of any of that asset's expressions. They never run at load time, so loading a config never imports the external library. If `imports` or `code` raises, the error is wrapped as `AssetEvaluationError` (key `imports` or `code`), cached, and re-raised on every later use without re-executing.
- **Evaluation.** Each expression is evaluated with `eval(code_obj, {**namespace_globals, **injected})`: a fresh per-call copy of the asset's private dict (it contains `__builtins__`) updated with the injected names, which win over config names of the same spelling. Because the injected names are *globals* of the evaluation, a generator, comprehension or lambda inside an expression sees them (`'sum(lib.pv(market, t) * w for t, w in zip(trades, weights))'`; as eval locals they were invisible to generators and lambdas). The copy never mutates the shared namespace, and helpers defined in `code` keep that namespace as their `__globals__`, so they never see injected names: pass them as arguments. The copy costs about 1 µs per call for a 160-name namespace (nothing measurable on the toy configs). Asset namespaces are **isolated**: two assets never see each other's helpers. Discovery includes nested scopes too: pricebt finds a pass-through parameter (`pricebt_bump_size`, ..., §8.1 rule 3a), `market_to`/`pricebt_to_date` and an attribute's `market` among every name the compiled expression reads, its nested code objects included (`co_names` walked through `co_consts`), so a name used only inside a lambda or generator counts.
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
   - (4) raise `AttributeError(field)`, also for a gs field that was never set (DEV-I18; gs returns None). This also happens when no session exists or the asset cannot be matched, because step 1 is then skipped.

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
class PricingContext:                # pricebt.markets — gs signature (R04§6); only pricing_date, csa_term and market are used
    def __init__(self, pricing_date=None, market_data_location=None, is_async=None, is_batch=None, use_cache=None,
                 visible_to_gs=None, request_priority=None, csa_term=None, timeout=None, market=None, show_progress=None,
                 use_server_cache=None, market_behaviour='ContraintsBased', set_parameters_only=False,
                 use_historical_diddles_only=None, provider=None): ...
    current: ClassVar                # the innermost entered context, else a default context (pricing_date = date.today())
    pricing_date -> date             # own value, else inherited from the enclosing context, else date.today()
    csa_term -> Optional[str]        # own value, else inherited
    market -> Optional[CloseMarket]  # own value only (never inherited, gs); None or a CloseMarket, anything else raises
                                     #   NotSupportedError (DEV-M1). CloseMarket(date=t): every market (and FX) of functions,
                                     #   portfolio_functions and each_market trades is evaluated on t, pricebt_date stays the
                                     #   pricing date, resolve uses the pricing date's own market; t is in every cache/group key
                                     #   and is the results' risk_key.market; a t after date.today() raises ValueError (gs)
    is_entered -> bool; is_async -> bool
    __enter__/__exit__               # a stack; nothing is deferred

class HistoricalPricingContext(PricingContext):
    def __init__(self, start=None, end=None, calendars=(), dates=None, is_async=None, is_batch=None, use_cache=None,
                 visible_to_gs=None, request_priority=None, csa_term=None, market_data_location=None, timeout=None,
                 show_progress=None, use_server_cache=None, provider=None): ...
    date_range -> tuple[date, ...]   # `dates` as given; else date_range(start, end or today); an int start = last N business days,
                                     #   DESCENDING (gs). Both or neither of start/dates -> gs ValueErrors. Property is named
                                     #   `date_range`, matching gs exactly (MUST-2) -- only the constructor kwarg is `dates`.

class CloseMarket:                   # pricebt.markets — gs CloseMarket(date=None, location=None, check=True)
    date -> date                     # own date if given and not check, else close_market_date(location, own date)
    location -> str; check; to_dict(); __eq__/__hash__ on (date, location)   # location: the code string, default 'LDN' (DEV-M1);
                                     #   it picks only the close-roll timezone; an unknown one raises ValueError (gs)

def close_market_date(location=None, date=None, roll_hr_and_min=(24, 0)) -> date:
    ...                              # gs: date (default: the current pricing date), the previous business day while now in
                                     #   the location's timezone (default LDN, DEV-M1) is before date + roll_hr_and_min

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
- **The catalogue.** `pricebt.risk` ports every gs 2.1.17 measure instance and preset (117 instances + 7 presets; IR_RISK_DESIGN §1) with gs's Python name, `name`, `asset_class`, `measure_type` string, `unit` and parameter class: `RiskMeasureWithCurrencyParameter` (`__call__(currency=None, name=None)`; the parameter is a `CurrencyParameter(value=...)`, so its key is `value`), `RiskMeasureWithFiniteDifferenceParameter` (`__call__(aggregation_level=None, bump_size=None, currency=None, finite_difference_method=None, local_curve=None, mkt_marking_options=None, scale_factor=None, name=None)`; presets such as `IRDeltaParallel` = `aggregation_level=Asset`, `IRDeltaLocalCcy` = `currency='local'`), or plain `RiskMeasure`. `IRVanna`/`IRVolga` are finite-difference measures (DEV-I9); `IRGammaParallelLocalCcy`/`IRDiscountDeltaParallelLocalCcy` fall back to their base (DEV-I16); `PnlExplain` and its family are relative measures (DEV-M2). The snapshot parity test (§12.3) pins each measure's class, `measure_type`, `asset_class` and `unit`. **Which measures an IRSwap, IRSwaption or Bond config must provide**, and what each one means, is `pricebt.risk.contracts.CONTRACTS` (IR_RISK_DESIGN §2; tables in ASSET_CONFIG_GUIDE "Measure contracts").
- Include **every measure imported by the in-scope 2.1.17 backtests files** (grep `from gs_quant.risk import` in backtest_objects.py, generic_engine.py and triggers.py). `pricebt.risk` also re-exports `FloatWithInfo`, `SeriesWithInfo`, `DataFrameWithInfo` and `ErrorValue`, as gs does.

**Measure → config function (generic for every asset class; no measure names are hard-coded in `assets/pricing.py`):**
1. `ResolvedInstrumentValues` → `PricingService.resolve`.
2. `DollarPrice` → the `Price` mapping with `currency='USD'`.
3. Look up `asset.risk_measures[measure.name]`. If it is missing and `measure.base_name` is set, look up `asset.risk_measures[measure.base_name]`. This covers presets such as `IRDeltaParallel` → `IRDelta` with its preset parameters. If it is still missing, use the key that `risk.contracts.provided_forms` counted toward the base measure (`base_name`, else `name`) at load time, in the form the request needs (IR_RISK_DESIGN R2-10; `AssetConfig.provided_forms`): an aggregation level of None/Point → bucketed; Type/Asset/Class → scalar, else bucketed (summed, rule 5); no aggregation level → the scalar slot (a number or a frame), else bucketed. So a preset or LocalCcy key (`IRDeltaParallel`, `IRDeltaLocalCcy`, `IRGammaParallelLocalCcy`) that satisfies a contract row also prices the base measure. If it is still missing (rule 3b first), raise `ConfigError(f"asset {a} has no mapping for risk measure {measure.name}; add it under risk_measures:")`.
   - 3a. (DEV-I10, narrowing DEV-I8; checked once the form's function `f` is chosen in rule 5.) A non-None pass-through parameter (`bump_size`, `finite_difference_method`, `local_curve`, `scale_factor`) reaches `f` as the injected `pricebt_<p>` (§4.3) and is part of every cache and group key; `f` supports it iff its compiled expression reads `pricebt_<p>` (its `co_names`, nested scopes included, §4.4), otherwise `NotSupportedError(f"asset {a}: {measure!r} sets {p}; function {f!r} does not reference pricebt_{p}")`. `mkt_marking_options` always raises `NotSupportedError(f"asset {a}: {measure!r} sets mkt_marking_options; it is honoured GS server-side and pricebt cannot pass it to an asset config")`. The currency parameter (`currency`, or `value` on currency-parameter measures) and `aggregation_level` are handled by rules 5-6. Nothing is silently ignored.
   - 3b. (DEV-I11.) If the mapping slot the request needs is empty and the measure (by `name`, then `base_name`) is declared under `unsupported_measures:` for that form or as a whole (`*`), raise `UnsupportedMeasureError` naming the measure, form and reason, before the "no mapping" `ConfigError`. A mapped slot always wins over a declaration (R2-9). For a measure with no `aggregation_level` and no mapping, any declared form counts.
4. Normalise a string mapping: a `functions:` entry or a `returns: scalar` portfolio function becomes `{scalar: f}`, and a `returns: buckets` portfolio function becomes `{bucketed: g}`.
5. Choose the form:
   - the measure has an `aggregation_level` parameter set to Type, Asset or Class → scalar;
   - it is set to None or Point → bucketed;
   - the measure has no such parameter → scalar if a `scalar` form is mapped, else bucketed.

   A missing scalar form is computed as the sum of the buckets, unless the scalar form is declared unsupported (rule 3b: the declaration wins over the sum). A missing bucketed form raises `UnsupportedMeasureError` if declared, else `ConfigError(... "has no bucketed mapping; request X(aggregation_level='Type') for the scalar form")` (IR_RISK_DESIGN R2-13).
   A `functions:` entry with `returns: frame` (IR_RISK_DESIGN R2-15) produces a table `DataFrameWithInfo` (`make_table_frame`, `pricebt_table = True`): quantity scales only its `scale_columns`, a currency conversion raises `ConfigError`, and `contracts.validate_frame` checks the measure's required columns (`[]` becomes an empty frame with them). A `returns: buckets` function may also return a list of per-row dicts (keys ⊆ the six bucketed columns, `value` required; R2-14).
6. A portfolio function used for one instrument is evaluated with `trades=[trade]` and `weights=[1.0]`, which gives the unit value. Then apply `quantity_` if the unit is extensive (§5.4), and FX if the measure's currency differs from the function's currency (§7).

### 8.2 Result objects (`pricebt.risk.results`): what the engine and notebooks use

| Class | Must provide |
|---|---|
| `RiskKey` | namedtuple `(provider, date, market, params, scenario, risk_measure)`; pricebt fills `date` and `risk_measure`, and `market` with a `CloseMarket` override's date (DEV-M1; `None` without one); the rest are `None` |
| `FloatWithInfo(float)` | `.risk_key`, `.unit` (dict), `.error` (None), `.raw_value`. `+` with an equal unit gives a `FloatWithInfo`; an unequal unit raises `ValueError('FloatWithInfo unit mismatch')`; a `None` unit adds to any unit, and `+ number` and `sum()` stay `FloatWithInfo` (DEV-R14; gs gives plain floats and raises unless the units are equal). `* k` and `k * x` keep `risk_key`/`unit` (`k * x`: DEV-R14); `-`, `/` and unary `-` give a plain float (gs). `repr` is `1500.0 (USD)`. The constructor takes the value first, `FloatWithInfo(value, risk_key=None, unit=None, error=None)` (DEV-R12); `StringWithInfo`, `DictWithInfo` likewise. |
| `DataFrameWithInfo(pd.DataFrame)` | `_metadata = ['risk_key', 'unit', 'error']`; `.raw_value`. A bucketed result has **exactly the columns `mkt_type, mkt_asset, mkt_class, mkt_point, mkt_quoting_style, value`**, all six always present. A missing label is `''`, never NaN. Row order is the order of the portfolio function's returned dict (DEV-R5). A **historical bucketed** result is one `DataFrameWithInfo` indexed by `date` (gs `compose`); its `raw_value` moves the index to a `dates` column; per-date selection is `df[df.index == d]`, a `DataFrameWithInfo` carrying `d` in its risk key, empty for a priced date whose ladder had no rows (DEV-R16, IR_RISK_DESIGN R2-27); `pricebt_dates` carries the priced dates, and a date outside them raises `KeyError` (gs). A **table** (`returns: frame`, `pricebt_table = True`, DEV-R11) has its own columns and keeps its `pricebt_scale_columns`; historically it is one table with a `date` column first, marked historical by its `pricebt_dates` (never by a column name). |
| `SeriesWithInfo(pd.Series)` | the same metadata; historical scalar results are indexed by date |
| `ErrorValue` | `(risk_key, error)`; `.raw_value = None` |
| `MultipleRiskMeasureResult(dict)` | gs constructor `MultipleRiskMeasureResult(instrument, dict_values)`, keyed by measure in the order given; `.instrument`, `.dates`, date indexing (non-historical raises `ValueError('Can only index by date on historical results')`), `to_frame`, `+` composes dates of the same instrument, `* k` and `+ k` (a table refuses `+ k`) (DEV-R9); `.transform(t)` applies per measure |
| `PricingFuture(result=None, exception=None)` | `.result()` returns the value or raises the stored exception; `.done()` is True |
| `LazyFuture(PricingFuture)` | `(thunk, group_key, member, service)`. `.result()` calls `thunk()` once and memoises the value; `.done()` is True. It is used only for per-instrument bucketed values (below). |

**`PortfolioRiskResult` contract** (gs results.py:600-973 semantics, with ordered risk measures):
- **Constructor** `PortfolioRiskResult(portfolio, risk_measures, futures)`:
  - `portfolio` is a shallow `clone()` of the Portfolio that `calc` was called on. For a resolve calc, its leaves are therefore the UNRESOLVED priceables.
  - `risk_measures` is stored as a tuple **in the order given**.
  - `futures` holds exactly one future per **direct child** of `portfolio`, in `portfolio.priceables` order. For an Instrument child, `future.result()` is the value (for one measure) or a `MultipleRiskMeasureResult` (for several). For a nested Portfolio child, it is a nested PRR. Plain values passed in are wrapped as `PricingFuture(value)`.
  - The constructor MUST accept any list with that alignment, because ExitTradeActionImpl rebuilds results from sliced `futures` (impls:447-485).
- `.portfolio`, `.risk_measures`, `.futures` (a tuple), and `.dates` (the result dates; one for a non-historical result).
- `__len__` → `len(self.futures)` (direct children); `__bool__` follows `__len__`; `__iter__` yields the **leaf** values in `all_paths` order, walking nested results (gs; e.g. the resolved instruments of a resolve calc).
- `__getitem__(item)`, following the gs error contract:
  - a RiskMeasure not in `risk_measures` raises `ValueError(f'{item} not computed')`;
  - with exactly one computed measure, `self[measure]` returns `self`; otherwise it returns a single-measure view;
  - an `Instrument` or a name `str` returns the **first** match's value(s) at any depth (gs quirk kept); if the item is not in `.portfolio`, it raises `KeyError(str(item))`. Indexing by a resolved Instrument that is not found falls back to `item.unresolved`, keeping only leaves priced on its resolution date (gs compares the whole resolution key but the measure), else `KeyError('Cannot slice ... resolved in a different pricing context')` (results.py:925-940);
  - a list of instruments, a `Portfolio` of instruments (gs: it iterates its direct children; a member sub-portfolio or not) or a slice returns `subset(...)`, flat and unnamed; a list of measures a view over those measures; a `PortfolioPath` the member at that path (DEV-R7);
  - a `date` returns the single-date view, dispatched per child as gs (no up-front `dates` check); a non-historical scalar leaf raises `RuntimeError('Can only index by date on historical results')`, a single-date frame `KeyError` (gs `.loc`; returned as is when empty), a date the leaf was not priced on `KeyError`;
  - an `int` indexes by position.
- `.get(item, default)` → `self[item]`, or `default` if that raises `KeyError` or `ValueError` (results.py:968; used by generic_engine.py:700).
- `__contains__`: by instrument, name or measure, at any depth (DEV-P1).
- `subset(paths, name=None)`: gs; a single path to a sub-portfolio returns that nested result (DEV-R7).
- `__add__(other)`: gs semantics (results.py:731-775).
  - If the measures, the dates and the instrument sets all overlap: `ValueError('Results overlap on risk measures, instruments or dates')`.
  - If the portfolios are equal both ways: merge futures pairwise (gs's `==` is one-way, so gs merges `Portfolio([a])` with `Portfolio([a, b])` and drops `b`; DEV-R13).
  - Otherwise: portfolio = `self.portfolio + other.portfolio` and futures = `self.futures + other.futures`; when the sum holds several measures, each leaf of a single-measure side is wrapped as `{measure: future}` (gs `as_multiple_result_futures`), so a measure a leaf lacks is a `KeyError` on read, never another measure's value. gs's `set_value` fill-in is not ported (DEV-R13).
  - `risk_measures` of the sum is an **ordered** union: self's measures first, then other's new ones (DEV-E14; gs uses a set).
  - A single-date result's date is its first leaf's `risk_key.date` (read from `group_key[2]` of a `LazyFuture`, never by evaluating it, R2-26), so `Σ_d p(d)` over one portfolio stitches a historical result (gs `_compose`).
  - Adding a number raises `ValueError('Can only add instances of PortfolioRiskResult')` (DEV-R8).
- `* k` scales every leaf (only a bucketed frame's `value` column, a table's scale columns); a non-number raises `ValueError` (DEV-R9).
- `result(timeout=None)` returns `self` (gs signature).
- `.transform(risk_transformation=None)`:
  - with `None`, return `self`;
  - with several measures, return a `MultipleRiskMeasureResult` of per-measure transforms;
  - with one measure, compute `vals = risk_transformation.apply(...)` over the leaf results and rebuild the same tree: one nested result per sub-portfolio, one `PricingFuture(v)` per instrument child (DEV-R7; gs rebuilds a flat list, results.py:820-837).
- `.aggregate(allow_mismatch_risk_keys=False, allow_heterogeneous_types=False)`:
  - gs's error contract on both the plain and the grouped (lazy) path: an error value → `ValueError('Cannot aggregate results in error')`; mixed types → `ValueError('Cannot aggregate heterogeneous types: ...')` unless `allow_heterogeneous_types`; unequal units → `ValueError('Cannot aggregate results with different units for ...')`; different dates/keys → `ValueError('Cannot aggregate results with different pricing keys')` unless `allow_mismatch_risk_keys` (the engine passes it);
  - scalars: a `FloatWithInfo` sum (left to right, as gs); historical scalars: a `SeriesWithInfo` summed per date;
  - bucketed: see below; historical bucketed groups by `dates` and the five `mkt_*` columns; tables are concatenated (adding `instrument_name`), never summed (DEV-R11);
  - no leaves: `FloatWithInfo(0.0)`; plain-float leaves (a transformer's output) sum like `FloatWithInfo`s (DEV-R15).
- `.to_frame(values='default', index='default', columns='default', aggfunc='sum', display_options=None)`: the gs layouts (R10§2.5): records depth-first in `futures` order, each labelled from its own path (`portfolio_name_{k}`, `instrument_name`; DEV-R6), plus `risk_measure` and, for historical results, `dates`; the gs default pivots; a bucketed/table branch indexed by its label columns; rows and columns in **first-appearance** order (no alphabetical sort); a `value` pivot leaves table measures out (DEV-R11); empty frames and `UnsupportedValue`s appear only with `show_na` (a `pricebt.config.DisplayOptions`; None reads `pricebt.config.display_options`; anything else raises gs's `TypeError`); `None` when there are no records. The engine's `values='value', index='instrument_name', columns='risk_measure'` sums bucketed values per instrument (R03§7).
- `.result()` → self; `.done()` → True.

**`ResultWithInfoAggregator(risk_col='value', filter_coord=None).apply(results)`** returns a **list** with one entry per input result:
- a float stays a float;
- a FloatWithInfo stays as is;
- a SeriesWithInfo or DataFrameWithInfo becomes `FloatWithInfo(df[risk_col].sum(), unit=df.unit, risk_key=df.risk_key)`, with rows filtered by `filter_coord` when it is given.

`Transformer.apply(data, *args, **kwargs)` is abstract.

**Bucketed (vector) values are lazy and group-aggregated.**
- For a bucketed measure, `PricingService.value` returns one `LazyFuture` per instrument, with:
  - `group_key = (asset_name, market_key, date, csa, function, target_ccy, params)`;
  - `member = (instrument_identity, frozen resolved, quantity_, res_date, res_csa, risk, params)`;
  - both are 7-tuples with the measure's pass-through `params` last (DEV-I10), so the date stays at `group_key[2]`;
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
| DEV-E19 | backtest_objects (Phase C: `PnlAttribute`, `pnl_explain`) | a `PnlAttribute` has one market metric, and its step P&L is `k·R(t−1)·Δm` or `½·k·R(t−1)·Δm²`, so a cross term (vanna, `R·ΔF·Δσ`) cannot be expressed | `PnlAttribute.cross_market_data_metric: Optional[RiskMeasure] = None` is appended (positional gs calls unchanged). When it is set, the step P&L is `k·R(t−1)·Δm₁·Δm₂` (no ½), with `m₂` read like `m₁` (from the exit results on an exit date). `get_risks()` includes it and leaves out `None` metrics; `second_order` together with a cross metric raises `ValueError` (IR_RISK_DESIGN §7.1) |
| DEV-E20 | backtest_objects (Phase C: `BackTest._explain_steps`) + generic_engine (DEV-R1's off-grid pricing) | `pnl_explain` raises `KeyError` when a trade exits on an off-grid date while other positions continue: those positions have no result on that date (R15 S3) | DEV-R1 prices the continuing positions into `results[d]`, so the step attributes correctly; the `pnl_explain` loop itself is still gs's (IR_RISK_DESIGN §7.5) |
| DEV-E21 | backtest_objects (Phase C: `PnlAttribute`, `pnl_explain`) | nothing checks the unit of a level, so a definition written for one level unit silently mis-scales the P&L (by 10⁴ between bp and decimal levels, R15 §4) | `PnlAttribute.market_data_unit` and `cross_market_data_unit: Optional[str] = None` are appended after `cross_market_data_metric`. When one is set, a level read that is a `FloatWithInfo` whose `.unit != {u: 1}` raises `ValueError(f"{attribute_name}: {measure} on {instrument} has unit {got}; the definition expects {u}")`. `ir_pnl_definition`/`swaption_pnl_definition`/`bond_pnl_definition` fill them from their unit arguments (theta leaves them None) (IR_RISK_DESIGN R2-16) |

**Results (R03§4.4)**

| ID | File (task) | gs behaviour | pricebt behaviour |
|---|---|---|---|
| DEV-R1 | backtest_objects (P3.2) + generic_engine `_handle_cash` (P3.5) | `result_summary` ffills the previous PV onto a date with no holdings while cash already includes the exit proceeds, so `Total` double counts | A row date `d` is *flat* iff no position is held on it: for a grid date, `portfolio_dict[d]` is empty; for a non-grid cash/TC date, no position in `portfolio_dict[p]` (p = the last grid date < d) has a final date > d. Flat dates get PV 0 and risk 0 before the ffill. On a non-grid date that is **not** flat, `_handle_cash` also prices the continuing positions (those in `portfolio_dict[p]` with final date > d) for all `risks` into `results[d]`, so the row shows their true PV and `strategy_as_time_series` gains rows for them |
| DEV-R2 | backtest_objects (P3.2) | `get_risk_summary_df` is computed once and never invalidated | recomputed on each call |
| DEV-R4 | backtest_objects (P3.2) | bucketed cells are ffilled on flat dates | follow DEV-R1 (zero) |
| DEV-R5 | risk/results (P1.3) | bucketed frames are ordered by `sort_risk`/`point_sort_order` (asset-class regexes, relative to today) | the config's bucket order, first appearance across groups; no point parsing |
| DEV-R6 | risk/results (Phase B) | `PortfolioRiskResult.to_frame` pairs depth-first leaf records with breadth-first portfolio labels, so rows are mislabelled when a level mixes leaves and sub-portfolios | every record is labelled from its own path (`portfolio_name_{k}`, `instrument_name`), depth-first in `futures` order |
| DEV-R7 | risk/results (Phase B) | `PortfolioRiskResult.transform` on a nested result rebuilds one future per leaf, misaligned with the nested portfolio; `subset` of a single sub-portfolio path pairs it with one future; `prr[PortfolioPath]` raises `KeyError` | the tree is kept (one nested result per sub-portfolio); a single sub-portfolio path returns that nested result; `prr[PortfolioPath]` returns the member at that path |
| DEV-R8 | risk/results (Phase B) | `PortfolioRiskResult + number` raises `RuntimeError('... cannot be composed')` | `ValueError('Can only add instances of PortfolioRiskResult')` |
| DEV-R9 | risk/results (Phase B) | `MultipleRiskMeasureResult * k` (and `+ k`) on a historical Series raises `AttributeError`; `PortfolioRiskResult`/`MultipleRiskMeasureResult * non-number` *returns* a `ValueError` instead of raising it; `PortfolioRiskResult * k` also multiplies string label columns; `MultipleRiskMeasureResult + MultipleRiskMeasureResult` of different instruments builds a `PortfolioRiskResult` over `Portfolio((i1, i2))` | a Series is multiplied directly; a non-number raises `ValueError`; only a bucketed frame's `value` column is scaled, and a table's `pricebt_scale_columns`; different instruments raise `NotSupportedError`, because `risk.results` may not import `Portfolio` (§3.2); `MultipleRiskMeasureResult + k` adds k to every value (a bucketed frame's `value` column) and refuses a table with `ValueError` (adding to its amounts means nothing) |
| DEV-R10 | risk/core (Phase B) | `subtract_risk` asserts `'value' in left.columns.names`, so it always raises `AssertionError` | the intent, `aggregate_risk((left, -right))`; the two frames need identical columns including `value`, else `ValueError` |
| DEV-R11 | risk/results + risk/core + assets/pricing (Phase B) + backtest_objects (Phase C: the `BackTest` views, IR_RISK_DESIGN R2-15) | gs 2.1.17's `Cashflows` is frame-valued (`result_handlers.cashflows_handler`) and the default `to_frame()` special-cases it (`has_cashflows`), but nothing else treats a table as one: `aggregate()` runs it through `aggregate_risk`, whose groupby silently merges identical rows of two positions into one; `to_frame(values='value', ...)` raises `KeyError: 'value'`; `PortfolioRiskResult * k` raises `TypeError` and `MultipleRiskMeasureResult * k` `AttributeError` | a table is a `DataFrameWithInfo` with `pricebt_table = True`. `PortfolioRiskResult.to_frame` pivoted on `value` leaves tables out; the default `to_frame` shows them indexed by their own columns; `aggregate()`/`aggregate_results` concatenate tables (adding `instrument_name`) and never sum or merge them (identical rows of two positions stay two rows); a historical table is one table with a `date` column first, rows concatenated in date order (`assets/pricing._date_indexed`), marked historical by its `pricebt_dates` (never by a column name) and emitting `dates` in records like a ladder. The `BackTest` views skip table measures, found by the `pricebt_table` marker on their results (never by name): `get_risk_summary_df` leaves them out of its cells, its flat-date and `zero_on_empty_dates` zero fills and its reindex to the risks, so `result_summary`, `risk_summary` and `summary_stats` do too; `strategy_as_time_series` leaves them out through its `value` pivot; `pnl_bps` of a table measure raises `ValueError('... is a table measure ...')`. With no results at all nothing identifies a table, and the empty frame keeps every risk as a column |
| DEV-R12 | risk/results (Phase B) | `FloatWithInfo(risk_key, value, unit, error, request_id)` | value first: `FloatWithInfo(value, risk_key=None, unit=None, error=None)`, because every pricebt producer passes the value positionally; `StringWithInfo` and `DictWithInfo` follow the same order |
| DEV-R13 | risk/results (Phase B) | `PortfolioRiskResult + PortfolioRiskResult` over different portfolios with several measures fills each leaf's missing measure from the other result where it holds the same instrument (`set_value`), mutating a multi-measure input's results in place; and since gs's `Portfolio ==` is one-way (`Portfolio([a]) == Portfolio([a, b])`), `+` of such results takes the same-portfolio branch and its `zip` silently drops `b` | leaves are wrapped per measure as gs (`as_multiple_result_futures`), but nothing is filled in: reading a measure a leaf lacks raises `KeyError`, never another measure's value; the same-portfolio branch needs the portfolios equal both ways, else they add as different portfolios (no instrument dropped) |
| DEV-R14 | risk/results (Phase B) | `FloatWithInfo + number`, `sum([x, y])` and `k * x` give plain floats; `+` of two `FloatWithInfo`s raises `ValueError('FloatWithInfo unit mismatch')` unless the units are equal | `+ number` and `sum()` keep a `FloatWithInfo` (the left unit and key); a `None` unit adds to any unit (e.g. an empty `aggregate()`'s `0.0`); unequal non-None units still raise; `k * x` keeps the type, key and unit like `x * k` (so `2 * eur + usd` raises instead of being labelled USD) |
| DEV-R15 | risk/results + risk/core (Phase B) | `PortfolioRiskResult.aggregate()` with no leaves returns `None`; `aggregate_results` of plain floats raises `AttributeError` reading `.error` | `FloatWithInfo(0.0)`; plain floats (a transformer's output) sum like `FloatWithInfo`s, left to right |
| DEV-R16 | risk/results + assets/pricing (Phase B) | a historical frame has no row for a date whose ladder was empty (`compose` drops it), so `result[date]` raises `KeyError`; an all-empty result is returned undated | a composed frame carries its priced dates (`pricebt_dates`): a priced date with no rows gives an empty `DataFrameWithInfo` carrying the date (so a hedge sized on it aggregates to 0, IR_RISK_DESIGN R2-27); a date outside the priced set raises `KeyError` as gs |
| DEV-R17 | risk/core (Phase B) | `aggregate_results` checks units only when truthy, so a dimensionless `{}` unit sums with `USD` and the total is labelled `USD` | any unit that is not None counts: `{}` against `USD` raises `ValueError('Cannot aggregate results with different units ...')` |

(DEV-R3 from revision 1 was removed: 2.1.17 already returns an empty frame when there are no results.)

**Instruments / risk**

| ID | File (task) | gs behaviour | pricebt behaviour |
|---|---|---|---|
| DEV-I1 | instrument (P2.1) | scaling edits size fields (`notional_amount`, `pay_or_receive`, `fee`) | signed `quantity_` multiplier (§5.4); kwargs never edited. pricebt also scales classes gs cannot (gs `Bond.scale()` raises) |
| DEV-I2 | instrument/portfolio (P2.1) | `strategy_as_time_series` static data (and `Portfolio.to_frame`) shows the resolved gs fields | the leaf's gs kwargs overlaid by its resolved terms (the library's own; the kwargs keep direction and size, which resolved terms need not carry, so `from_frame` rebuilds the same trade, IR_RISK_DESIGN R2-31), plus a `quantity_` column and, for a leaf that names its asset, `pricebt_asset`; `Portfolio.from_frame` has no `$type` rows and raises `ValueError('Neither asset_class/type nor pricebt_asset specified')` (gs: `... nor $type specified`) |
| DEV-I3 | instrument (P2.1) + impls (P3.5) | `to_dict()` identity in ExitTradeAction: an unhashable dict, a latent crash | `instrument_identity()` tuple |
| DEV-I4 | assets/pricing (P2.2) | `IRDelta(aggregation_level=Type)` returns a small DataFrame | a `FloatWithInfo` for Type/Asset/Class; a bucketed `DataFrameWithInfo` for None/Point (§8.1 rule 5) |
| DEV-I5 | assets/pricing (P2.2) | an unparameterised currency-bearing risk is in USD (per the IRDelta docstring) | the function's currency (decision 0.5) |
| DEV-I6 | none (docs) | instrument strings (`'100k'`, `'ATM+25'`, `'=solvefor(...)'`) are parsed server-side | not parsed by pricebt; the asset's `resolve` decides |
| DEV-I7 | assets/pricing (P2.2) | `IRFwdRate` is in percent | whatever unit the asset function declares; the shipped configs use bp (MUST-4); intensive units are not multiplied by quantity (§5.4) |
| DEV-I8 | assets/pricing (P2.2; narrowed by DEV-I10) | measure parameters beyond currency and aggregation level are honoured server-side | `NotSupportedError` for `mkt_marking_options`, and for a pass-through parameter the chosen function does not reference (§8.1 rule 3a) |
| DEV-I9 | risk (Phase A) | 1.5.4: `IRVanna`/`IRVolga` are plain, non-callable `RiskMeasure` | `RiskMeasureWithFiniteDifferenceParameter` (2.1.17 behaviour), so `IRVanna(aggregation_level=Type)` works as in gs's vanna/volga notebook; `risk_measure` exception rows in `gs_api_exceptions.yaml` |
| DEV-I10 | assets/pricing (Phase A) | `bump_size`, `finite_difference_method`, `local_curve`, `scale_factor` are sent to the GS server | injected as `pricebt_<name>` into `functions:`/`portfolio_functions:` (None when unset); a function supports one iff its expression names it (`co_names`), else `NotSupportedError`; part of every unit-value, portfolio-value and group key (§8.1 rule 3a, §8.2) |
| DEV-I11 | risk/contracts + assets/config + assets/pricing + errors (Phase A; amended by IR_STRICT_CONTRACT R3-0) | any measure is answered; an inapplicable one silently returns `UnsupportedValue` | IRSwap/IRSwaption/Bond configs must map every contract measure/form with an allowed unit or declare it under `unsupported_measures:` with a reason (one `ConfigError` at load listing every gap, with a paste-ready block); **for IRSwap and IRSwaption a mapping is required (no declarations)**: declaring a contract measure, a form of one, or a preset/fallback resolving to one is a load error, and the error ends with a paste-ready mapping skeleton (`contracts.mapping_skeleton`) instead of a declaration block, so `UnsupportedMeasureError` arises only for Bond; mapped and declared loads with a `UserWarning` and the mapping wins; requesting a declared form whose mapping slot is empty raises `UnsupportedMeasureError` before "no mapping" (IR_RISK_DESIGN §2, §8.1 rule 3b); a preset or fallback key the contract counts toward a base measure (e.g. `IRDeltaParallel` → `IRDelta` scalar) also serves the base's request (§8.1 rule 3, R2-10); a declaration the contract cannot count (a preset name, a form outside the row, a name neither in the contract nor in `pricebt.risk`) loads with a `UserWarning` |
| DEV-I12 | risk/contracts (Phase A: contract text; numbers from asset configs) | IR delta/gamma are curve sensitivities; `IRFwdRate` is defined for swaps/swaptions only | own-rate semantics: the `IRDelta` scalar is the total derivative of `Price` w.r.t. the instrument's own quoted rate (`IRFwdRate`: swap par rate, swaption forward rate, bond yield to maturity), `IRGammaParallel` the chain-rule second derivative on the same bumps. Additivity caveat: summing `IRDeltaParallel` across different instrument types is approximate (exact for a hedge of the same type) (IR_RISK_DESIGN R2-1, R2-2). The shipped toy and ARBS swap configs map the `IRDelta` scalar to a fixed-annuity pv01, which is **at-the-money-exact** only: off-market it misses the first-order term `N·(F−K)·ΔA` (R14), and the load check cannot detect that |
| DEV-I13 | risk/contracts (Phase A: contract text; ladders from asset configs) | `IRGamma` returns a 12-column cross-gamma frame | `IRGamma` (bucketed only) is a **diagonal** gamma ladder: the 6-column bucketed frame, ccy per bp² at each pillar (IR_RISK_DESIGN §2.2) |
| DEV-I14 | instrument (Phase B) | `Base.__setattr__` coerces a field and writes it on the dataclass, resolved or not | the name is normalised camelCase → snake_case first; a gs field of the class other than `name`, or a name already in `_kwargs` (a `ConfigInstrument` term, an extra kwarg), is coerced (§5.2) and written to `_kwargs`, the terms pricing reads (None deletes the key); on a resolved instrument it raises `ValueError` (the `clone` rule); `asset_class`/`type_` raise `ValueError('<key> cannot be set')` as in gs; every other name is a plain attribute (IR_RISK_DESIGN R2-29) |
| DEV-I15 | risk/contracts (Phase A: contract text) | IR `Theta` is undocumented | `Theta` = one calendar day of carry, total return, own `IRFwdRate` and `IRAnnualImpliedVol` held fixed (curve translated, never rolled), ccy **per day**; a per-year `IRTheta` = 365 × `Theta` (IR_RISK_DESIGN R2-4) |
| DEV-I16 | risk (Phase A) | `IRGammaParallelLocalCcy` / `IRDiscountDeltaParallelLocalCcy` need their own mapping | `base_name='IRGammaParallel'` / `'IRDiscountDeltaParallel'`: a mapping of the base measure serves the LocalCcy variant (decision 0.5 makes them identical) |
| DEV-I17 | risk/contracts (Phase A: contract text) | `ExpiryInYears` is defined for options only | `max(final_or_expiry − t, 0).days / 365` for every class: a swaption's expiry, a swap's or bond's final date (IR_RISK_DESIGN R2-5) |
| DEV-I18 | instrument (Phase B) | reading a gs field that was never set returns `None` (a dataclass default) | raises `AttributeError(field)` (§5.1 item 2 step 4), so a typo or an unset term is loud and `hasattr` is False |
| DEV-I19 | risk/contracts (IR_STRICT_CONTRACT R3-1: contract text; numbers from asset configs) | `FairPremium`, `ForwardPrice`, `PremiumCents`, `LocalAnnuityInCents`, `CompoundedFixedRate`, `CRIFIRCurve` are server-defined and loosely documented (`ForwardPrice` declares unit `BPS` but documents a local-currency price at expiry; `CRIFIRCurve` returns the full CRIF schema) | defined in the strict IRSwap/IRSwaption contract: `FairPremium` = `Price / DF(premium settlement)` (the swaption's `premium_payment_date` if supported and set, else the spot date; no spot lag → `Price`); `ForwardPrice` = `Price / DF(expiry)` in ccy, forward to the date `ExpiryInYears` counts to (swaption expiry, swap termination: chosen for consistency with DEV-I17), `Price` on or after it; `PremiumCents` = `Price / |notional|` and `LocalAnnuityInCents` = `Annuity / |notional|`, intensive notional levels (`bp`/`pct`/`decimal`/`number`); `CompoundedFixedRate` = `(1 + K/f)^f − 1`, intensive; `CRIFIRCurve` = a frame with the required subset `RiskType, Qualifier, Bucket, Label1, Label2, Amount, AmountCurrency` (`Amount` scales with quantity; Σ `Amount` = Σ `IRDelta` ladder) |

**Portfolio**

| ID | File (task) | gs behaviour | pricebt behaviour |
|---|---|---|---|
| DEV-P1 | markets/portfolio + risk/results (Phase B) | `Portfolio.all_portfolios` returns only the direct sub-portfolios; `Portfolio.__contains__` looks in itself and those only, with bare `==` | `all_portfolios` recurses and de-duplicates; `Portfolio.__contains__` and `PortfolioRiskResult.__contains__` match at any depth, and also through `.unresolved` (as gs `paths` does) |

**Markets**

| ID | File (task) | gs behaviour | pricebt behaviour |
|---|---|---|---|
| DEV-M1 | markets (Phase B, Phase E) + assets/pricing | `PricingContext(market=...)` prices on any `Market` object, and `resolve` runs under it too (`calc(ResolvedInstrumentValues)` keyed on the context's market, so an ATM rate resolves on the override's curves); with none, `PricingContext.market` is a default `CloseMarket` at the pricing date's close (yesterday's when pricing today) | a `market` other than None or a `CloseMarket` raises `NotSupportedError` at construction (it was silently ignored: a wrong-number path). `CloseMarket(date=t)` is a **market-date override**: every market (and FX) of `functions`/`portfolio_functions` and of `each_market` trades is evaluated on `t` while `pricebt_date` stays the pricing date; `resolve` always uses the pricing date's own market; `t` is in every cache and group key. With no `market`, `PricingContext.market` is None and each asset config's own market for the pricing date is used, never rolled. A result's `risk_key.market` is the override date (`None` without one; gs: the `Market`), so results on different markets never aggregate (gs). `CloseMarket.location` is the location's code string (gs: a `PricingLocation`), `'LDN'` by default (gs: the context's `market_data_location`, which pricebt ignores, else LDN), and `close_market_date(location=None)` uses LDN (gs raises `ValueError` on `PricingLocation(None)`; the gs 030007 notebook calls it with no location) (IR_RISK_DESIGN §8, R2-30) |
| DEV-M2 | risk + assets/pricing (Phase E) | `PnlExplain(to_market)` keeps the target in a private attribute: equality and hashing ignore it, and the server prices it in a `RelativeMarket` context (`pricing_context`) | the target is the measure's `parameters`, a frozen `MarketParameter(date, location)`, so equality, hashing, DEV-E14 ordering and every cache/group key distinguish targets; no `pricing_context`. The mapped (portfolio) function also receives `market_to` (the market of the target date, the context's csa) and `pricebt_to_date`, and must name one of them. `PnlExplainClose()` (target: the pricing date's own close) raises `NotSupportedError` unless a `CloseMarket` override makes the from-market another date; `PnlExplainLive`/`PnlPredictLive` raise at construction (live markets are GS server-side). The repr names the target (`PnlExplain(date:2024-01-09, location:LDN)`; gs: `PnlExplain` for every target), so two targets get two `to_frame` labels. The result is always the six-column bucketed frame (gs: `risk_by_class_handler`'s three unsorted columns `mkt_type, mkt_asset, value`, and a result of at most two rows of a single `mkt_type` becomes a float) (IR_RISK_DESIGN §8, R2-32) |

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
4. **Asset-agnostic scan (MUST-5).** Tokenize-scan (as in item 2) **only** `src/pricebt/assets/**`, `src/pricebt/markets/**`, `src/pricebt/risk/results.py`, `src/pricebt/risk/transform.py` and `src/pricebt/risk/core.py`. Fail on `(?i)(?<![a-z])(notional|tenor|swaption|swap|fixed_rate|termination_date|expiration_date|pay_or_receive|strike|dv01|pv01|par_rate|sofr|libor|estr)(?![a-z])`. `instrument/`, `risk/__init__.py` and `backtests/` are not scanned, because gs names and the ported gs text legitimately live there.
5. **Import order.** Each pricebt module is imported alone, in a fresh subprocess, in reverse DAG order (§3.2).
6. **Skeleton.** The set of `src/pricebt/**/*.py` paths equals a literal list copied from §3.2.
7. **Non-vacuity twins.** Every scanning guard has twins fed with synthetic files:
   - must fail: `import rateslib`; `os.environ["ARBS_SUPABASE_ENABLED"]`; `key = "usd_sofr_eris_rlbasic"`; `getattr(m, "bulk_get_data")`; `raise ValueError(f"ARBS served {d}")`; `terms['notional']` (asset-agnostic scan);
   - must pass: `pd.Series`; `class OisFixingCashAccrualModel`; `ois_fixings = {}`; `x = 1  # uses ARBS` (a trailing comment).

   This applies the global CLAUDE.md rule: verify the checker on a known answer.

### 12.3 gs API parity snapshot (MUST-2)

- **`tools/gs_api_snapshot.py`** runs with **the base python** (gs 1.5.4) and writes two files:
  - `tests/data/gs_api_1_5_4.json`. For each in-scope symbol it records the kind, the `inspect.signature` parameters `(name, kind, default repr)` in order, the dataclass fields `(name, init, default repr)` in order, the public methods and properties, enum `[(name, value)]`, and, for risk measures, `(class name, name, measure_type, asset_class, unit)`. The in-scope symbols are:
    - every public class/function of `gs_quant.backtests.{strategy, triggers, actions, data_sources, backtest_objects, backtest_utils, generic_engine, core}` that is in scope per §9.3;
    - the 8 generated instrument classes;
    - the `gs_quant.risk` measures: every 2.1.17 measure instance and preset (IR_RISK_DESIGN §1; those absent from 1.5.4 are recorded as requested-not-found), plus the functions `aggregate_risk`, `aggregate_results`, `subtract_risk`, `sort_risk`, `combine_risk_key` and the classes `PnlExplain`, `PnlExplainClose`, `PnlExplainLive`, `PnlPredictLive` (IR_RISK_DESIGN R2-23);
    - the `gs_quant.common` enums;
    - `gs_quant.markets.{PricingContext, HistoricalPricingContext}` and `gs_quant.markets.portfolio.Portfolio`;
    - the `gs_quant.datetime` functions;
    - `gs_quant.session.GsSession.use` and `gs_quant.session.Environment`.
  - `tests/data/gs_instruments_1_5_4.json`: for every dataclass in `gs_quant.target.instrument`, its `asset_class`, `type_` and `[(field, init, default_repr, annotation_str)]`, feeding `tools/gen_gs_fields.py`.
- **`tests/test_gs_api_parity.py`** (tier `core`; **it never imports gs_quant**) compares each pricebt symbol with the snapshot. Allowed differences are listed in `tests/data/gs_api_exceptions.yaml` as `{symbol, aspect, reason, dev_id}`, and any unlisted difference fails. Expected exceptions:
  - the trailing keyword-only `pricebt_asset`, `quantity_` and `**kwargs` on instrument constructors;
  - the stubs;
  - the trailing fields 2.1.17 appended;
  - the 2.1.17-only measures (`FXDeltaLocalCcy`/`FXGammaLocalCcy`/`FXVegaLocalCcy` and the others listed in the exceptions header), which are absent from 1.5.4 and so are extra symbols, not mismatches;
  - `IRVanna`/`IRVolga`'s class (aspect `risk_measure` with an `expect: {class: ...}` that must match exactly; DEV-I9);
  - a symbol a later phase adds (aspect `all` with `pending: <phase>`): it excuses only the symbol's absence, and fails once pricebt has it, so the implementing phase deletes the row (IR_RISK_DESIGN R2-23).
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
| IR pricing and risk (`v2-ir-risk`) | `test_contracts.py`, `test_unsupported_measures.py`, `test_pricing_params.py`, `test_table_results.py` (contracts, declarations, parameters, frames); `test_toylib_ir.py` (every toy measure against an independent computation); `test_results_parity.py`, `test_portfolio.py`, `test_portfolio_pricing.py`, `test_portfolio_notebooks.py` (Portfolio/results gs parity, gs 03_portfolios notebooks); `test_pnl_ir.py` (swaption/bond P&L decomposition, residual bounds); `test_pnl_explain_measure.py` (`CloseMarket`, `PnlExplain`); `test_namespace_scopes.py` (§4.4 nested scopes); `test_docs_contract_tables.py` (the guide's contract tables = `CONTRACTS`); `test_ir_pricing_and_risk_demo.py` (the demo notebook source run as a script) |

### 12.5 Mutation check

At each phase gate, the implementer runs `tools/mutcheck.py` on at least 3 hand-picked one-line mutations per phase and confirms that a named test fails for each. Examples: flip the `<` in the MR short close, drop DEV-R1's zeroing, reverse the FX direction, remove `csa` from the market cache key, or skip pinning in the toy resolve.

---

## 13. Extensibility test: adding swaptions (MUST-5)

**Status: done at toy scale.** `tests/assets/toy_usd_swaption.yaml` (on `tests/toylib/swaption.py`) is a working swaption config that maps the whole IRSwaption contract, and `tests/assets/toy_usd_bond.yaml` does the same for `Bond`; neither needed a line of swaption- or bond-specific code in pricebt (the asset-agnostic guard, §12.2 item 4, enforces it). `skills/pricebt-connect-pricing-library/references/config-template-swaption.yaml` is the template for a real library. The walkthrough below is what a real USD swaption config needs:
1. **The class.** `IRSwaption` already exists in `pricebt.instrument` as generated data, with gs's field order. **No code change.**
2. **The config.** Write `configs/assets/usd_sofr_swaption.yaml` with:
   - `instrument: IRSwaption` and `match: {notional_currency: USD}`;
   - a `market` that returns the curve **and** a vol surface as one object (e.g. a `SimpleNamespace` built in `code`), under the swaption asset's own market key, because its expression differs from the swap asset's;
   - a `resolve` that pins `expiration_date`, `termination_date` and the strike;
   - functions `npv`, `dv01`, `vega` (`unit: ccy_per_bp`, per bp of normal vol) and `gamma`;
   - `attributes: {expiration_date: 'resolved["expiration_date"]'}`, so that `AddTradeAction(option, 'expiration_date')` works;
   - `risk_measures: {Price: npv, IRDelta: {scalar: dv01}, IRVega: {scalar: vega}}`, plus every other measure of the IRSwaption contract, each mapped or declared under `unsupported_measures:` with a reason (DEV-I11, IR_RISK_DESIGN §2.2); the CI toy `tests/assets/toy_usd_swaption.yaml` maps the whole contract.

   A bucketed IRVega needs a `returns: buckets` portfolio function with `';'`-joined `'<tail>;<expiry>'` keys in gs order, e.g. `'10Y;1Y'` (§8.2; IR_RISK_DESIGN §2.2).
3. **Sign and strike.** pricebt never reads `buy_sell`.
   - The swaption `resolve` MUST fold it into a signed resolved notional: `sign = -1 if buy_sell == 'Sell' else +1`, times the sign of `notional_amount` (040307 sells with a negative notional).
   - It MUST reject `pay_or_receive` values it does not price (e.g. `Straddle`, unless it prices it as payer + receiver).
   - The engine's `quantity_` stays a pure position multiplier.
   - The strike grammar (`'ATM'`, `'A-50'` = ATM − 50bp, decimals; `'=solvefor(...)'` → raise) is parsed only in `resolve`, which pins the strike as a decimal.
4. **Notebooks.** Notebooks 040303, 040305, 040307 and `Backtesting.ipynb` then port (R05§4).

A design review MUST fail any change that makes the engine, the pricing layer or the results aware of an asset class. The asset-agnostic guard (§12.2 item 4) catches the obvious cases.

---

## 14. Later work (explicitly not in this plan)

- A real-library swaption or bond asset config (the §13 walkthrough; the toys and the connect-skill templates exist).
- A ladder-hedge action: a vector hedge over several instruments, using the `bucketed` portfolio function.
- From IR_RISK_DESIGN §12: gs **scenarios** (`RollFwd`, `CurveScenario`, `IndexCurveShift`, `MarketDataShockBasedScenario`, `CurveOverlay`, `CarryScenario`) as a config section mapping a scenario class to a market transform, with an opaque `(csa, location, market date, active scenarios)` cache-key slot; **engine coupon booking** from `Cashflows` (opt-in, a deviation); **more generated classes and contracts** (`IRBondFuture`, `IRBondOption`, `IRCap`, `IRFloor`, `IRCapFloor`; inflation and cross-currency contracts); **cross-trade strike references** (`'=solvefor([name]...)'`, gs 030010).
- (Done on `v2-ir-risk`: per-row bucket coordinates for multi-curve ladders, a `returns: buckets` function returning a list of row dicts, IR_RISK_DESIGN R2-14.)
- EUR/GBP/JPY real-data configs. ARBS serves these only through `GSQUANT-RL`, which imports gs_quant inside ARBS and ends 2026-08-03 (R06§2). That is acceptable inside a user config, but it needs its own hazard review.
- Coupon cash between marks. gs books only entry and exit PVs, so a coupon paid while a trade is held leaves the PV without reaching cash, and gs has the same property. `Cashflows` (a table measure) exists and `BackTest.pnl_explain_table()` reconciles economic P&L with it (IR_RISK_DESIGN decision 0.10); booking it as engine cash is a deviation, so ask first. Config-only workarounds already work: a total-return `npv`, or pinning `entry_price` in `resolve` for margined futures.
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
