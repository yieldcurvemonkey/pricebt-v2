# pricebt v2 — Requirements and Design

Status: **design, approved for implementation once the user signs off the decisions in §0.**
Branch: `v2-redesign` in the worktree `C:\Users\chris\clee\gsquant-temp-claude\pricebt-v2`.
The v1 tree is preserved at git tag `v1-final` (`main`). Nothing in v1 needs to be kept "just in case": it is recoverable.

Companion documents:

| File | What it is |
|---|---|
| `docs/v2/README.md` | Entry point and reading order for the implementer |
| `docs/v2/DESIGN.md` | This file: requirements, architecture, contracts |
| `docs/v2/IMPLEMENTATION_PLAN.md` | Phased task list, file ownership, acceptance commands, gates |
| `docs/v2/research/01..08-*.md` | Evidence. Exact gs_quant signatures, engine pseudocode, result shapes, ARBS API, v1 inventory. **Cited by section number throughout; read the cited section before implementing the thing it describes.** |

Notation. **MUST** = requirement; a change needs the user's approval. **SHOULD** = strong default; deviate only with a written reason in `docs/v2/DEVIATIONS.md`. `R§x` = research note section, e.g. `R02§4` = `research/02-gs-actions-and-generic-engine.md` section 4. "gs" = gs_quant. "1.5.4" = the installed gs_quant (`C:\Users\chris\anaconda3\Lib\site-packages\gs_quant`). "2.1.17" = the repo checkout (`C:\Users\chris\clee\gsquant-temp-claude\gs-quant\gs_quant`), which is newer.

---

## 0. Decisions the user must confirm before hand-off

These are the only open choices. Everything else in this document is decided. The implementer MUST NOT start Phase 3 until 0.1–0.3 are confirmed. The defaults below are what the plan assumes.

| # | Decision | Default (assumed by the plan) | Why | Alternative |
|---|---|---|---|---|
| 0.1 | **Which gs version is the reference.** | **Public API signatures = 1.5.4** (the version the user named), **behaviour = 2.1.17** wherever 1.5.4 crashes or is buggy, and **2.1.17's additive features are included** (`AddWeightedTradeAction`, `EarlyExitPositionLimitScaledAction`, `BackTest.summary_stats()`, `AddScaledTradeAction.dated_priceables`, the trailing `EventTriggerRequirements` fields). The **port source code is 2.1.17**. | 1.5.4 cannot run the reference notebook 040304: every `MeanReversionTrigger` fire raises `TypeError` (R01§6.5.9, R02§7 Q7). 2.1.17 is a strict superset of 1.5.4's signatures except `EventTriggerRequirements`, whose new fields are *appended*, so 1.5.4 positional calls still work (R01§10). | Strict 1.5.4 (then 040304 cannot run) |
| 0.2 | **The ARBS Eris source for the example.** | `IRSwapsMDP(source="ERIS_EOD_LIVE-RL_BASIC")`, read **store-only** through `bulk_get_data(..., ignore_cache_miss=True)`: no network access, no writes. 1,423 days, 2020-07-01..2026-08-20, dense from 2021-01-04 (R06§1.3, §1.4b). | The source the user wrote, `"ERIS_EOD_LIVE-RL_BASIC-NOJUMPS"`, **never reads the local curve store**: only 26 dates are cached. Every other date downloads from files.erisfutures.com, turns off TLS verification for the whole process, and **overwrites the shared curve-store partition** `USD-SOFR-1D/date=<d>` (R06§1.3, §6.1). | NOJUMPS after a deliberate network warm-up that the user authorises (it clobbers store partitions); the example config keeps it as a commented line. |
| 0.3 | **The deviation list (§11).** | Accept every row of §11. Each fixes a crash, a look-ahead, a silent wrong number, or a GS-infrastructure dependency. | "Nearly 1:1": exact parity on well-formed inputs, and no reproduction of gs defects. | Strict gs parity row by row: say which rows to drop |
| 0.4 | Market data missing on a grid date (holiday gap, store hole, or `end=today` beyond the last stored day) | `missing_market='drop'`: the date is removed from the backtest grid, a `UserWarning` names the count and the first and last dropped dates, and they are listed on `backtest.missing_market_dates`. | gs silently prices every weekday on server data. `end_date = datetime.today().date()` in the notebooks would otherwise fail after 2026-08-20. | `'raise'` |
| 0.5 | Currency of a currency-bearing risk that has **no `currency` parameter** (e.g. `IRDelta(aggregation_level='Type')`) | **The asset's local currency**, the same as `Price` | Matches the notebook comments ("results will be in local ccy", R05§7.2) and avoids hidden FX conversion. | USD, following the gs `IRDelta` docstring |

---

## 1. What pricebt v2 is (one paragraph)

pricebt v2 is an **event-driven backtester whose public API is a near 1:1 port of `gs_quant.backtests`** (Strategy, Triggers, Actions, GenericEngine, BackTest results). A gs backtest notebook ports by changing `gs_quant` to `pricebt` in its imports, replacing `GsSession.use(...)` with `PricebtSession.use(assets=[...])`, and replacing any Marquee `Dataset` call with a local series. **pricebt itself contains no pricing and no market data.** Every number (market objects, trade resolution, PV, risk, FX) comes from **one self-contained YAML asset config per tradable asset**. That config holds Python import lines, a helper-code block, and **Python expression strings** that pricebt evaluates with injected variables (`pricebt_date`, `pricebt_timestamp`, `market`, `kwargs`, `trade`, …). An external library (the user's ARBS, or anything else) is named **only inside those config files**, never in pricebt's source.

---

## 2. Requirements

### 2.1 The five MUSTs (the implementer must re-read these before every phase)

> **MUST-1: Zero external pricing or market-data dependency in pricebt.**
> Nothing under `src/pricebt/` may import, name, special-case, or assume the shape of **any** pricing library or market-data infrastructure: ARBS (`MDP`, `Query`, `Caching`, `IRSwapsMDP`, …), rateslib, QuantLib, gs_quant, Bloomberg, or anything else. Allowed runtime dependencies are the **stdlib, numpy, pandas, PyYAML, python-dateutil and tqdm**. No `dataclasses_json` either. pricebt calls strings from config files and is agnostic to what they compute. The **only** exception is attribution: the gs_quant name may appear in comments, docstrings, license headers and `NOTICE`, because ported files keep their Apache-2.0 headers. **Tests enforce this rule** (§12.2). ARBS may appear only in `configs/assets/*.yaml`, `notebooks/`, `docs/`, and tests marked `live_arbs`.

> **MUST-2: The backtest API and capabilities are near 1:1 with gs_quant.**
> Same module paths (`pricebt.backtests.triggers`, `pricebt.backtests.actions`, `pricebt.backtests.generic_engine`, `pricebt.backtests.strategy`, `pricebt.backtests.data_sources`, `pricebt.backtests.backtest_objects`, `pricebt.backtests.backtest_utils`, `pricebt.instrument`, `pricebt.risk`, `pricebt.common`, `pricebt.markets`, `pricebt.markets.portfolio`, `pricebt.datetime`, `pricebt.session`, `pricebt.data`). Same class names, **same constructor parameter names, order and defaults** (dataclass field order is load-bearing: notebooks construct positionally), same enum members and values, same result object API (`result_summary` and `risk_summary` are *properties*; `trade_ledger()`, `strategy_as_time_series()`, `pnl_explain()` and `summary_stats()` are *methods*), same table shapes and column names, same trade-naming scheme, same engine semantics. Differences are allowed **only** where §11 lists them. A machine check compares pricebt against a JSON snapshot of 1.5.4's signatures (§12.3).

> **MUST-3: One self-contained config file per asset.**
> Everything pricebt needs to trade an asset lives in one YAML file: which gs instrument class it serves, the matching rule, the currency, the import lines, a helper-code block, the market expression, trade resolution and construction, per-trade pricing functions with units, portfolio functions (e.g. a delta ladder over a collection of trades), attribute reads, and the gs risk-measure mapping. **pricebt never interprets the instrument kwargs.** It passes them to the config verbatim (§5).

> **MUST-4: Multi-currency, and bp units.**
> Every asset declares its currency. Values are computed in that local currency and converted to another currency **only** through an FX config (the same expression mechanism, §7) when a risk measure carries a `currency` parameter or `run_backtest(result_ccy=...)` is set. Rate-sensitivity risk is in **currency per 1bp**. Rate-like measures reported by the shipped configs are in **bp**. Strategy inputs in bp (e.g. `fixed_rate='ATM+25'`) pass through untouched. The result object gains a **bp-normalised P&L view** (`BackTest.pnl_bps()`, §8.4).

> **MUST-5: Adding an asset means writing a config, not changing code.**
> A later asset type (the next planned one is **swaptions**) must be addable by writing a new YAML asset config. At most, an optional ≤10-line gs instrument field list is added if gs has a class pricebt does not yet mirror. The engine, the pricing layer and the result objects MUST NOT special-case swaps. §13 walks through the swaption case as the design test.

### 2.2 SHOULDs

- S1. Ported files keep gs's structure, function names and comments, so that a reader can diff pricebt against gs 2.1.17 file by file.
- S2. Deterministic: two runs of the same strategy give identical frames. There are no set-ordered columns (§11 DEV-E14), and trigger state is reset per run (DEV-T10).
- S3. Errors name the asset, the config key, the expression text and the pricing date (§6.6).
- S4. Performance: each market is evaluated once per (market key, date, csa); each unit measure once per (asset, resolved terms, date, function, csa) (§6.3). A 5-year daily backtest of a few hundred ARBS swaps should take minutes, not hours.
- S5. The examples are runnable: the converted 040304 notebook runs in CI on the toy library and, opt-in, on ARBS.

### 2.3 Non-goals (not in v2)

- Serialisation of strategies or results to JSON (`dataclass_json`, `to_json`, `from_dict` round trips of strategies). `Instrument.to_dict()`/`as_dict()` are kept because the engine uses them.
- `PredefinedAssetEngine`, `EquityVolEngine`, `StrategySystematic`, `order.py`, `event.py`, `data_handler.py`, `execution_engine.py`, `OrdersGeneratorTrigger`: the importable names exist and **raise `NotSupportedError`** when used (§9.6).
- `GsDataSource`, `Dataset`, `OisFixingCashAccrualModel`, the GS event calendar: they exist and raise `NotSupportedError` with a message pointing to the local replacement.
- P&L attribution layers, tie-out, reference stacks, snapshots, bindings and schemas (all v1 machinery). **Stripped.**
- A ladder-hedge *action* (hedging a vector risk with several instruments). The ladder *measure* is in scope; hedging with it is a later item (§14).
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
 pricebt.instrument (Instrument, IRSwap, IRSwaption, ..., ConfigInstrument)
 pricebt.risk (RiskMeasure objects, results: FloatWithInfo, DataFrameWithInfo, PortfolioRiskResult, PricingFuture)
        │  every value request goes to ONE place:
        ▼
 pricebt.assets.pricing.PricingService   ← caches; FX; risk-measure → function mapping; units
        │
        ▼
 pricebt.assets (AssetConfig loader + validator, AssetNamespace = exec(imports, code) + eval(expressions))
        │  eval() of strings written by the user
        ▼
 external library (ARBS, …) — ONLY reachable through the config's own import lines
```

**The central idea.** The gs engine code talks to `PricingContext`, `HistoricalPricingContext`, `Portfolio.calc`, `Portfolio.resolve`, `Instrument.resolve/scale/clone` and `PortfolioRiskResult`. pricebt re-implements **exactly that surface** locally (eager computation, already-completed futures), backed by the `PricingService`. The ported engine files then need **only their imports changed**, plus the listed deviations. The engine never learns that asset configs exist.

### 3.2 Package layout (target state after Phase 0–5)

```
pricebt-v2/
  pyproject.toml                  deps: numpy, pandas, pyyaml, python-dateutil, tqdm; extras: test (pytest, nbformat, nbclient, ipykernel)
  pytest.ini                      markers: core, live_arbs, notebook; addopts empty; testpaths = tests
  NOTICE                          Apache-2.0 attribution for code ported from gs_quant (Goldman Sachs)
  AGENTS.md                       points to docs/v2/README.md
  README.md                       user-facing: what pricebt is, quick start, asset config primer
  configs/
    assets/usd_sofr_ois_interest_rate_swap.yaml     ARBS example (MUST-1: the only ARBS-naming file besides notebooks/docs)
    fx/README.md                                    how to write an FX config
  docs/v2/                         DESIGN.md, IMPLEMENTATION_PLAN.md, README.md, DEVIATIONS.md, ASSET_CONFIG_GUIDE.md, research/
  notebooks/
    040304_mean_reversion_toy.ipynb           CI version (toy asset)
    040304_mean_reversion_usd_sofr_arbs.ipynb opt-in version (ARBS)
  src/pricebt/
    __init__.py                   __version__ = "2.0.0"; nothing imported eagerly except errors
    errors.py                     PricebtError, ConfigError, AssetEvaluationError, MarketDataUnavailable, NotSupportedError
    base.py                       EnumBase, Base helpers (field_metadata/static_field shims), Priceable marker
    common.py                     gs enums (PayReceive, BuySell, Currency, AggregationLevel, OptionType, OptionStyle,
                                  DayCountFraction, BusinessDayConvention, SwapClearingHouse, PrincipalExchange, SwapSettlement,
                                  AccrualConvention, UnderlierType, TradeAs, AssetClass, AssetType, CurrencyName, RiskMeasureUnit,
                                  PositionType); RiskMeasure, ParameterisedRiskMeasure re-exported from risk
    progress.py                   salvaged v1 notebook-safe tqdm bar (single display output in a kernel)
    datetime/__init__.py          business_day_offset, is_business_day, prev_business_date, date_range, business_day_count, today
    datetime/relative_date.py     RelativeDate, RelativeDateSchedule (gs rules b,d,w,m,y,+compound; lower-case units)
    risk/__init__.py              measures: Price, DollarPrice, IRDelta, IRDeltaParallel, IRDeltaLocalCcy, IRGamma, IRGammaParallel,
                                  IRVega, IRVegaParallel, IRVegaLocalCcy, IRBasis, IRXccyDelta, InflationDelta, IRFwdRate, IRSpotRate,
                                  IRAnnualImpliedVol, IRDailyImpliedVol, FXDelta, FXGamma, FXVega, FXSpot, EqDelta, EqGamma, EqVega,
                                  EqSpot, Cashflows, ResolvedInstrumentValues; RiskMeasure classes
    risk/results.py               RiskKey, ResultInfo, FloatWithInfo, SeriesWithInfo, DataFrameWithInfo, ErrorValue,
                                  MultipleRiskMeasureResult, PortfolioRiskResult, PricingFuture
    risk/transform.py             Transformer (base), ResultWithInfoAggregator
    instrument/__init__.py        Instrument, IRSwap, IRSwaption, FXOption, FXForward, EqOption, InflationSwap, Cash,
                                  ConfigInstrument; re-exports OptionStyle, OptionType, Currency, PayReceive, BuySell,
                                  SwapClearingHouse, SwapSettlement
    instrument/_gs_fields.py      the gs field lists (name, type-coercion tag, default) per instrument class — DATA ONLY
    markets/__init__.py           PricingContext, HistoricalPricingContext
    markets/portfolio.py          Portfolio
    session.py                    PricebtSession, GsSession (no-op shim with gs signature), Environment
    data/__init__.py              DataFrequency, Dataset (stub raising), measure_series (pricebt extension)
    target/common.py, target/measures.py   thin re-export shims (gs import paths used by notebooks)
    assets/__init__.py            load_asset, load_fx, AssetConfig, FxConfig
    assets/yamlio.py              salvaged v1 hardened YAML loader (duplicate keys are errors; NO ${ENV} interpolation)
    assets/config.py              AssetConfig/FunctionSpec/RiskMapping dataclasses, schema validation, expression compilation
    assets/namespace.py           AssetNamespace: exec(imports)+exec(code) once, eval(compiled expr, injected vars), error wrapping
    assets/registry.py            AssetRegistry: name → config; instrument → config matching
    assets/fx.py                  FxConfig evaluation + cache
    assets/pricing.py             PricingService (the only place values are produced)
    backtests/__init__.py         gs re-exports (core names)
    backtests/core.py             TradeInMethod, MarketModel, TimeWindow, ValuationFixingType, ValuationMethod; Backtest/BacktestResult stubs
    backtests/backtest_utils.py   CalcType, CustomDuration, make_list, get_final_date, interpolate_signal, scale_trade
    backtests/data_sources.py     MissingDataStrategy, DataSource, GenericDataSource (fixed), GsDataSource (stub), DataManager (stub)
    backtests/triggers.py         all triggers/requirements (2.1.17 behaviour + §11 fixes)
    backtests/actions.py          all actions + *ActionInfo (2.1.17)
    backtests/action_handler.py   ActionHandler, ActionHandlerBaseFactory
    backtests/backtest_engine.py  BacktestBaseEngine
    backtests/backtest_objects.py BackTest (+pnl_bps), ScalingPortfolio, WeightedScalingPortfolio, transaction models,
                                  TransactionCostEntry, CashPayment, Hedge, WeightedTrade, cash accrual models, PnlAttribute/Definition
    backtests/generic_engine_action_impls.py   2.1.17 action impls (port)
    backtests/generic_engine.py   GenericEngine (port) + missing-market pre-scan + progress bar
    backtests/strategy.py         Strategy
    backtests/equity_vol_engine.py, predefined_asset_engine.py, strategy_systematic.py, order.py, event.py,
              data_handler.py, execution_engine.py      stubs: importable names, NotSupportedError on use
  tests/
    guards/                       zero-dependency guards (MUST-1)
    toylib/                       tiny closed-form rates "library" used ONLY by tests (never shipped)
    assets/                       toy asset configs (toy_usd_irs.yaml, toy_eur_irs.yaml, toy_fx.yaml)
    data/gs_api_1_5_4.json        signature snapshot (generated by tools/gs_api_snapshot.py)
    data/gs_api_exceptions.yaml   documented exceptions to the snapshot, each with a reason
    test_*.py
  tools/
    gs_api_snapshot.py            run with the BASE anaconda python (has gs_quant 1.5.4) to regenerate the snapshot
    nb_build.py                   salvaged v1: builds .ipynb from a percent-format .py source
    mutcheck.py                   salvaged v1: mutation check (a test must fail when the code is broken)
```

### 3.3 Runtime environment

| What | Value |
|---|---|
| Python for pricebt and ARBS | `C:\Users\chris\anaconda3\envs\stir\python.exe` (3.13.5, pandas 2.3.1, numpy 2.2.6, tqdm 4.67.1). ARBS also runs in this env (R06§1.1). |
| Python for the gs signature snapshot **only** | `C:\Users\chris\anaconda3\python.exe` (base; gs_quant **1.5.4**). The `stir` env has gs_quant 1.4.26, **which is not the reference**: never snapshot from it. |
| Run tests | from `C:\Users\chris\clee\gsquant-temp-claude\pricebt-v2`: `$env:PYTHONPATH="src;tests"; C:\Users\chris\anaconda3\envs\stir\python.exe -m pytest tests -o addopts= -p no:cacheprovider` |
| gs source to port (read-only) | `C:\Users\chris\clee\gsquant-temp-claude\gs-quant\gs_quant\backtests\*.py` (2.1.17) |
| gs reference install (read-only) | `C:\Users\chris\anaconda3\Lib\site-packages\gs_quant` (1.5.4) |
| ARBS (read-only; NEVER import it from src/ or from normal tests) | `C:\Users\chris\clee\ARBS` |

---

## 4. The asset config (the user-facing contract)

### 4.1 File format

YAML, loaded by `pricebt.assets.yamlio` (duplicate keys are an error; `${ENV}` interpolation is **off**, so expression strings are never rewritten). One asset per file. Unknown top-level keys are a `ConfigError` with a did-you-mean suggestion. All expressions are **compiled at load time** (`compile(src, f"<asset {name}:{key}>", "eval")`), so a syntax error fails the load and names the asset, the key and the line.

### 4.2 Schema

```yaml
schema_version: 1                     # required, must be 1
asset: usd_sofr_ois_interest_rate_swap  # required unique id; defaults to the file stem when omitted
description: "..."                    # optional free text
instrument: IRSwap                    # required: the pricebt.instrument class name this asset serves
                                      #   (IRSwap, IRSwaption, FXOption, FXForward, EqOption, InflationSwap, or ConfigInstrument)
match: {notional_currency: USD}       # optional: equality rules on the instrument's kwargs (after defaults) used to pick
                                      #   this asset when the user writes IRSwap(...) without asset=... (§5.3)
currency: USD                         # required ISO-4217 code: the currency every `ccy`-unit function returns
defaults:                             # optional: applied to the instrument kwargs BEFORE resolve, for keys the user left None
  pay_or_receive: Receive             #   (gs applies these server-side; R04§1 "GS default" column)
  termination_date: 10y
  notional_currency: USD
  fixed_rate: ATM
  notional_amount: 1.0e6
imports: |                            # optional Python SOURCE, exec'd ONCE (first use) into this asset's private namespace
  import os, sys
code: |                               # optional Python SOURCE, exec'd ONCE after imports, same namespace: helpers, singletons
  def helper(m): ...
market:
  expr: 'load_market(pricebt_date)'   # required: evaluated once per (market key, date, csa); returning None = unavailable
  key: usd_sofr_eris_rlbasic          # optional sharing key (default: the asset name); assets that share a key MUST have
                                      #   byte-identical market.expr (checked at registration) and share market objects
resolve:
  expr: 'resolve_swap(market, kwargs)'   # optional: (market at the trade date, kwargs) -> dict of PLAIN resolved terms
                                          #   (str/int/float/bool/None/date/datetime/tuple). Absent -> resolved = kwargs.
trade:
  expr: 'build_swap(market, resolved)'   # optional: (market, resolved) -> the library's trade object. Absent -> trade = resolved.
  build_on: each_market               # each_market (default) | resolve_date   (§6.4)
functions:                            # required: per-trade functions. Evaluated for ONE unit of the instrument (quantity 1).
  npv:      {expr: 'market.npv(trade)', unit: ccy}
  dv01:     {expr: 'market.pv01(trade)', unit: ccy_per_bp}
  par_rate: {expr: 'market.fair_rate(remark(market, trade)) * 1e4', unit: bp}
portfolio_functions:                  # optional: functions over a COLLECTION of trades of this asset on one market
  delta_ladder:
    expr: 'delta_ladder(market, trades, weights, ("2Y","5Y","10Y","30Y"))'
    unit: ccy_per_bp
    returns: buckets                  # buckets -> dict[str, float] keyed by bucket label | scalar -> float
    labels: {mkt_type: IR, mkt_asset: USD-SOFR-1D, mkt_class: OIS}   # constant columns of the gs ladder frame (§8.2)
attributes:                           # optional: values the gs engine reads with getattr() on an instrument
  termination_date: 'resolved["termination_date"]'                  # trade_duration='termination_date'
  notional_amount:  'abs(resolved["notional"]) * abs(quantity)'     # ScaledTransactionModel('notional_amount')
size_attribute: notional_amount       # optional: which attribute RebalanceAction(size_parameter=...) scales (§9.4)
risk_measures:                        # required: gs risk-measure NAME -> function name (or a mapping for vector measures)
  Price: npv                          #   Price is REQUIRED (the engine books cash with it)
  IRDeltaParallel: dv01
  IRDelta: {scalar: dv01, bucketed: delta_ladder}   # scalar used for aggregation_level Type/Asset/Class; bucketed otherwise
  IRFwdRate: par_rate
```

**Units** (`unit:`), which is the closed set pricebt understands:

| unit | meaning | FX-converted by a `currency` parameter / `result_ccy`? | FloatWithInfo `.unit` |
|---|---|---|---|
| `ccy` | amount in the asset currency | yes | `{"<CCY>": 1}` |
| `ccy_per_bp` | currency per +1bp | yes | `{"<CCY>": 1}` (the gs convention: the currency is the unit) |
| `ccy_per_bp2` | currency per bp² | yes | `{"<CCY>": 1}` |
| `bp` | basis points | no | `{"bp": 1}` |
| `pct` | percent | no | `{"pct": 1}` |
| `decimal` | plain rate (0.0425) | no | `{"decimal": 1}` |
| `number` | dimensionless | no | `{}` |
| `date` | a date (attributes only) | no | n/a |

Requesting a `currency` conversion of a non-currency unit raises `ConfigError("measure X has unit bp; it cannot be converted to EUR")`.

### 4.3 Injected variables (the ONLY names pricebt puts into an evaluation)

| Name | Type | Available in | Meaning |
|---|---|---|---|
| `pricebt_date` | `datetime.date` | all | the pricing date |
| `pricebt_timestamp` | tz-aware `pandas.Timestamp` | all | `pricebt_date` at the session's EOD time in the session tz (default `17:00`, `America/New_York`) |
| `pricebt_datetime` | naive `datetime.datetime` | all | `pricebt_timestamp` without tz |
| `pricebt_csa` | `str` or `None` | all | the CSA in force: `HedgeAction.csa_term` during hedge resolution, else `run_backtest(csa_term=...)`, else `None`. Opaque to pricebt. |
| `pricebt_asset` | `str` | all | the asset name |
| `pricebt_currency` | `str` | all | the asset currency |
| `market` | whatever `market.expr` returned | resolve, trade, functions, portfolio_functions, attributes (if a market exists) | the market object for (key, date, csa) |
| `kwargs` | `dict` (a fresh copy) | resolve (and trade/functions/attributes when `resolve` is absent) | the instrument's kwargs after `defaults`, **exactly as the user wrote them** except for gs enum coercion (§5.2) |
| `resolved` | `dict` (a fresh copy) | trade, functions, attributes | the output of `resolve` (plain data) |
| `trade` | the output of `trade.expr` | functions | the library trade object for ONE unit |
| `trades`, `weights` | `list`, `list[float]` | portfolio_functions | trade objects of this asset on this market, and their position quantities (same length, same order) |
| `quantity` | `float` | attributes | the instrument's signed quantity multiplier (§5.4) |

Names defined by `imports`/`code` are visible to every expression of that asset. Injected names shadow config names of the same spelling; do not define helpers named `market`, `trade`, etc.

### 4.4 Evaluation rules

- `imports` and `code` run **lazily, once per process per asset** on the first evaluation of that asset's expressions (not at load time), so loading a config never imports the external library. If `imports`/`code` raise, the error is wrapped (`AssetEvaluationError`, key `imports` or `code`) and re-raised on every later use, without re-executing.
- Every expression is evaluated with `eval(code_obj, namespace_globals, injected_locals)`; `namespace_globals` is the asset's private dict (it contains `__builtins__`), and `injected_locals` is a fresh dict per call. Asset namespaces are **isolated**: two assets never see each other's helpers.
- **Market:** `None` means "no market for this date". Any exception is an error (wrapped, not swallowed). A config that wants "exception means unavailable" catches the exception in its `code` helper and returns `None`. ARBS's `bulk_get_data(..., ignore_cache_miss=True).get(d)` already returns `None` on a miss (R06§1.4b).
- **Resolve** must return a `dict` whose values are hashable plain data (`str`, `int`, `float`, `bool`, `None`, `date`, `datetime`, `tuple` of those). It is validated, and anything else is a `ConfigError`. **Resolve MUST pin every relative term** (tenor to absolute date, `'ATM'` to a number) at the trade date. A `'10y'` left unpinned is re-read as a rolling 10y on every later date and silently corrupts P&L. The toy library test `test_resolve_pins_dates` guards this in pricebt's own examples.
- **Functions** return a `float` (or an `int`, converted). A `NaN` is allowed and propagates. `returns: buckets` portfolio functions return `dict[str, float]`.
- **Security:** configs are **trusted code** (they `exec`). Say so in `ASSET_CONFIG_GUIDE.md`. pricebt never loads a config from an untrusted location on its own. Paths come only from `PricebtSession.use(assets=...)`/`load_asset(...)`.

### 4.5 FX config

```yaml
schema_version: 1
fx: user_fx_table                          # required id
imports: |
  import pandas as pd
code: |
  _T = pd.read_csv(r"C:\data\fx_fixings.csv", index_col=0, parse_dates=True)    # columns like EURUSD, GBPUSD
  def rate(base, quote, d):
      col = f"{base}{quote}"
      if col in _T: return float(_T[col].asof(pd.Timestamp(d)))
      inv = f"{quote}{base}"
      if inv in _T: return 1.0 / float(_T[inv].asof(pd.Timestamp(d)))
      return None
rate: 'rate(base, quote, pricebt_date)'    # required: units of QUOTE currency per 1 unit of BASE currency; None = unavailable
```

Injected: `base`, `quote` (ISO strings), `pricebt_date`, `pricebt_timestamp`, `pricebt_datetime`. `PricingService.fx(from_ccy, to_ccy, date)` returns `1.0` when the currencies are equal (no evaluation), otherwise the cached evaluation of `rate` with `base=from_ccy, quote=to_ccy`. A `None` or non-positive result raises `MarketDataUnavailable(f"no FX {from}{to} on {date}")`. Converting `x` from `A` to `B` is `x * fx(A, B, d)`. ARBS has no offline FX history (R06§5), so the FX config for real runs reads a user file. Tests use `tests/assets/toy_fx.yaml`.

---

## 5. Instruments (the gs facade objects) — `pricebt.instrument`

### 5.1 Class shape

Every gs instrument class that pricebt mirrors is a subclass of `Instrument` with **the gs init fields in gs positional order** (from `_gs_fields.py`, which is pure data taken from R04§1–2). They also accept arbitrary **extra keyword arguments** (pass-through, e.g. `bpv=`, `effective_tenor=`) and two pricebt keywords: `asset=` (explicit asset name) and `quantity=` (default `1.0`).

```python
class Instrument(Priceable):                      # pricebt.instrument
    # construction
    def __init__(self, *args, name=None, asset=None, quantity=1.0, **kwargs): ...
    # identity
    name: Optional[str]
    kwargs -> dict            # read-only copy: gs fields + extras, camelCase→snake_case, enum-coerced (§5.2); None-valued fields omitted
    asset -> str | None       # explicit asset name if given
    quantity -> float         # signed multiplier (§5.4)
    asset_config -> AssetConfig   # resolved lazily through the current session registry (§5.3)
    unresolved -> Instrument | None      # None until resolved; then the pre-resolution instrument (gs contract, R04§3.2)
    resolved_terms -> dict | None        # output of the asset's resolve (plain data) once resolved
    resolution_key -> RiskKey | None     # RiskKey(date=resolution date, ...)
    # gs methods
    clone(**kw) -> Instrument            # dataclasses.replace semantics; carries unresolved/resolved_terms/resolution_key
    resolve(in_place=True)               # under PricingContext(d): resolve on d; in place returns None; HistoricalPricingContext: see §6.5
    calc(risk_measure_or_iterable, fn=None)   # value outside a context; completed PricingFuture inside (§6.5)
    price(currency=None)                 # calc(Price) / calc(Price(currency=...))
    dollar_price()                       # calc(DollarPrice)
    scale(scaling, in_place=True, check_resolved=True)   # quantity *= scaling; None -> no-op; unresolved + check_resolved -> RuntimeError (gs)
    flip(in_place=True)                  # scale(-1)
    to_dict() -> dict                    # gs: no name (see DEV-I3 for hashing)
    as_dict(as_camel_case=False) -> dict # gs: includes name
    __getattr__(field)                   # 1) asset `attributes` expr (if a market or resolved terms exist) 2) resolved_terms 3) kwargs 4) AttributeError
    __eq__/__hash__                      # over (class, asset, frozen kwargs, quantity, name, frozen resolved_terms) — gs includes name
    __repr__                             # gs: f'{ClassName}({name})' if name else ClassName
    # class-level
    asset_class, type_                   # gs read-only class attributes (e.g. Rates / Swap) from _gs_fields
```

`ConfigInstrument(asset: str, *, name=None, quantity=1.0, **kwargs)` is the generic class for assets gs has no class for. Its `asset` is required.

### 5.2 kwargs handling (opaque, with gs coercion only)

- camelCase kwargs are converted to snake_case (gs `handle_camel_case_args`, R04§3.1). `fooBar` and `foo_bar` together raise `ValueError`.
- A gs field whose gs type is an enum (`pay_or_receive: PayReceive`, `notional_currency: Currency`, `buy_sell: BuySell`, `option_type`, …; the tag is in `_gs_fields.py`) is coerced to the **pricebt enum** with gs rules (case-insensitive on value; `PayReceive` accepts `'receive'`/`'receiver'`). An invalid value raises `ValueError` at construction, as in gs. **pricebt enums subclass `str`**, so a config can write `kwargs["pay_or_receive"] == "Pay"`. Note that `PayReceive.Receive == "Rec"` (the gs value).
- **Everything else is passed through untouched**: `'10y'`, `'ATM+25'`, `'100k'`, `date(...)`, numbers, and unknown extra kwargs. pricebt never parses them. The asset's `resolve` does.
- `defaults` from the asset config fill keys that are absent or `None` **at resolve time** (not at construction, because the asset is looked up lazily).

### 5.3 Instrument → asset matching

At the first pricing request, `instrument.asset_config` is determined as follows:
1. `asset=` given → the registry entry of that name, else `ConfigError("unknown asset ...; registered: [...]")`.
2. Otherwise, the registered assets whose `instrument:` equals the class name **and** whose `match:` rules all hold on `{**asset.defaults, **kwargs}` (string comparison after `str()`; enums compare by value). Exactly one → that asset. Zero → `ConfigError` listing the candidates and why each failed. More than one → `ConfigError("ambiguous: ... pass asset=...")`.
3. The chosen asset name is cached on the instrument (and carried by `clone`).

### 5.4 Quantity replaces gs field-editing scale

gs scales a trade by editing its size fields (`notional_amount *= |s|`, flip `pay_or_receive` if `s<0`, `fee *= -1`; R04§1). pricebt instead multiplies a **signed `quantity`** on the instrument: `scale(s)` → `quantity *= s`. **Every measure the engine sees = `quantity × unit_value`**, where `unit_value` is the config function evaluated on the unscaled trade. This is exact for every product that is linear in its size (swaps; swaptions are linear in notional too, and a sold swaption is quantity −1 of the bought one). Configs never see `quantity` except in `attributes` (so `notional_amount` can report `|notional| × |quantity|` as gs would). Portfolio functions receive the quantities as `weights`.

---

## 6. The pricing layer — `pricebt.assets` + `pricebt.markets` + `pricebt.risk.results`

### 6.1 `PricebtSession` (`pricebt.session`)

```python
class PricebtSession:
    def __init__(self, assets=(), fx=None, *, tz="America/New_York", eod_time="17:00",
                 missing_market="drop", reporting_currency=None): ...
    @classmethod
    def use(cls, assets=(), fx=None, **kwargs) -> "PricebtSession"   # constructs, sets PricebtSession.current, returns it
    current: ClassVar[Optional["PricebtSession"]]
    registry: AssetRegistry       # from `assets`: paths to .yaml files, directories (all *.yaml inside), dicts, or AssetConfig objects
    pricing: PricingService
    fx: Optional[FxConfig]
    def add_asset(self, source) -> AssetConfig
    def reset_caches(self) -> None
    def __enter__/__exit__        # context-manager form: restores the previous current session on exit

class GsSession:                   # gs-compatible shim so `GsSession.use(...)` cells run unchanged
    @classmethod
    def use(cls, environment_or_domain=Environment.PROD, client_id=None, client_secret=None, scopes=(),
            api_version='v1', application='gs-quant', http_adapter=None, use_mds=False, domain='AppDomain') -> None
        # no network: records the arguments on GsSession.current; does NOT create a PricebtSession
class Environment(EnumBase, Enum): DEV = 1; QA = 2; PROD = 3
```

Pricing without a current `PricebtSession` raises `PricebtError("no PricebtSession: call PricebtSession.use(assets=[...]) first")`.

### 6.2 `PricingService` (`pricebt.assets.pricing`): the only producer of values

```python
class PricingService:
    def __init__(self, registry, fx=None, *, tz, eod_time): ...
    def asset_for(self, inst) -> AssetConfig
    def market(self, asset: AssetConfig, d: date, csa: Optional[str]) -> Any            # cached; None -> MarketDataUnavailable
    def has_market(self, asset: AssetConfig, d: date, csa: Optional[str]) -> bool       # evaluates (and caches) the market
    def resolve(self, inst: Instrument, d: date, csa: Optional[str]) -> Instrument      # a resolved CLONE (name kept, quantity kept)
    def unit_value(self, inst: Instrument, d: date, function: str, csa: Optional[str]) -> float
    def portfolio_value(self, asset, d, function, insts: Sequence[Instrument], csa) -> float | dict[str, float]
    def attribute(self, inst, field) -> Any
    def fx(self, from_ccy: str, to_ccy: str, d: date) -> float
    def value(self, inst, d, risk: RiskMeasure, csa) -> FloatWithInfo | DataFrameWithInfo | Instrument   # §8.1 mapping, quantity applied
    def reset(self) -> None
```

### 6.3 Caches (all per `PricingService`, cleared by `reset()`; the engine calls `reset()` at the start of every `run_backtest`)

| Cache | Key | Value |
|---|---|---|
| market | `(market_key, date, csa)` | the market object, or a sentinel for `None` |
| trade | `build_on=each_market`: `(asset, frozen resolved, date, csa)`; `build_on=resolve_date`: `(asset, frozen resolved)` | the library trade object |
| unit value | `(asset, frozen resolved, date, function, csa)` | float |
| portfolio value | `(asset, date, function, csa, tuple((frozen resolved, quantity) ...))` | float or dict |
| fx | `(from, to, date)` | float |

`frozen resolved` = `tuple(sorted(resolved.items()))`. It is hashable because resolve returns plain data (§4.4). **Unit values depend only on economic terms**, so a hedge's unit trade and its scaled copy share cache entries, and two positions with identical terms are priced once.

### 6.4 Trade construction: `build_on`

- `each_market` (default, safest): the trade object is rebuilt from `resolved` on each date's market. Use it when the library's trade object captures its curve at build time (for example a rateslib `IRS(curves=...)` whose own `.npv()` would silently use the old curve).
- `resolve_date`: the trade object is built once, on the resolve-date market, and reused on later markets. The functions receive today's `market` and the old `trade`. The config is responsible for re-marking where needed (ARBS: `npv` and `pv01` are safe on a later market, while `fair_rate`, carry and roll need `remark(market, trade)`; R06§3.4). The ARBS example uses `resolve_date` because each `build_irswap` costs 12–23 ms (R06§1.7).

### 6.5 gs-shaped context and future objects (`pricebt.markets`, `pricebt.risk.results`)

These mimic gs closely enough that the ported engine code runs unchanged. **Computation is eager**: a value is computed when requested, and "futures" are already completed.

```python
class PricingContext:                # pricebt.markets
    def __init__(self, pricing_date=None, market_data_location=None, is_async=None, is_batch=None, use_cache=None,
                 visible_to_gs=None, request_priority=None, csa_term=None, timeout=None, market=None, show_progress=None,
                 use_server_cache=None, market_behaviour='ContraintsBased', set_parameters_only=False,
                 use_historical_diddles_only=None, provider=None): ...     # gs signature (R04§6); only pricing_date, csa_term are used
    current: ClassVar  # the innermost entered context, or a default context (pricing_date = today)
    pricing_date -> date     # own value, else inherited from the enclosing context, else date.today()
    csa_term -> str | None   # own value, else inherited
    is_entered -> bool
    __enter__/__exit__       # a stack; nothing is deferred

class HistoricalPricingContext(PricingContext):
    def __init__(self, start=None, end=None, calendars=(), dates=None, is_async=None, is_batch=None, use_cache=None,
                 visible_to_gs=None, request_priority=None, csa_term=None, market_data_location=None, timeout=None,
                 show_progress=None, use_server_cache=None, provider=None): ...   # gs signature; `start` xor `dates` (gs errors)
    dates -> tuple[date, ...]     # `dates` as given; or date_range(start, end or today) (int start = last N business days, DESCENDING, gs)

class PricingFuture:                 # pricebt.risk.results
    def __init__(self, result=None, exception=None): ...
    def result(self): ...            # returns the value or raises the stored exception
    def done(self) -> bool: return True
```

**Return convention (gs):** `Instrument.calc/resolve` and `Portfolio.calc/resolve` return a `PricingFuture` **when a PricingContext is entered** (`PricingContext.current.is_entered`), and the plain value otherwise. `PortfolioRiskResult` is itself future-like: `.result()` returns `self`, `.done()` is `True`. A gs call site that does `orders[s].result()` or `p.result()` therefore works.

**Historical results:** under `HistoricalPricingContext(dates=D)`, `Portfolio.calc(risks)` returns a `PortfolioRiskResult` covering all dates in `D`. `result[d]` returns the single-date `PortfolioRiskResult`. `Portfolio.resolve(in_place=False)` returns (a future of) `{d: Portfolio(resolved children, name=...)}`. `Instrument.resolve(in_place=True)` under a historical context raises `RuntimeError('Cannot resolve in place under a HistoricalPricingContext')` (gs).

**Resolution measure:** `ResolvedInstrumentValues` as a calc measure returns the **resolved instrument clone** per instrument. With several measures, `result[inst]` is a `MultipleRiskMeasureResult` (a dict keyed by measure) and `new_inst[ResolvedInstrumentValues]` is the resolved clone (the 2.1.17 AddScaled path, R02§5.3).

### 6.6 Errors (`pricebt.errors`)

| Class | Raised when | Message MUST include |
|---|---|---|
| `ConfigError(message, asset=None, key=None)` | schema/validation problems, unknown asset, ambiguous match, bad unit conversion | asset name, config key, the offending value, a did-you-mean where applicable |
| `AssetEvaluationError(asset, key, expr, date, csa)` | any exception raised while evaluating imports/code/an expression (`raise ... from exc`) | asset, key, the first 200 chars of the expression, date, csa, the original exception type and message |
| `MarketDataUnavailable(asset, date, csa)` | market expression returned `None` and the caller needed a market; FX unavailable | asset or pair, date |
| `NotSupportedError` | stubs (§9.6), unsupported gs features | what to use instead |

---

## 7. Currency model (MUST-4)

1. **Local by default (gs parity).** `Price` with no `currency` parameter → the asset's local currency; `FloatWithInfo.unit = {"EUR": 1}` for a EUR asset. The same holds for every currency-bearing measure with no `currency` parameter (decision 0.5). `currency='local'` also means local.
2. **Converted on request.** A measure with `currency='USD'` → the unit value × quantity × `fx(asset.currency, 'USD', d)`; unit `{"USD": 1}`. `DollarPrice` ≡ `Price(currency='USD')`.
3. **`run_backtest(result_ccy='USD')`** → exactly the gs rule (R02§3.2): every risk `r` in the run becomes `r(currency='USD')`. A risk that is not a `ParameterisedRiskMeasure` raises `RuntimeError(f'Unparameterised risk: {r}')`. The price measure becomes `Price(currency='USD')`. Cash is then booked from the converted price (at the payment date's FX), so `cash_dict` has a single key.
4. **Mixed book without `result_ccy`** → the gs errors, unchanged: `ValueError('Cannot aggregate results with different units for Price')` when two currencies are held on one date, and `RuntimeError('Cannot aggregate cash in multiple currencies')` in `result_summary` (R03§4.2, §15). The error message gains the hint `" — pass result_ccy=... (pricebt converts with your FX config)"`.
5. **Hedging across currencies** uses the gs check: `current_risk.unit != hedge_risk.unit` → `RuntimeError('cannot hedge in a different currency')`. It passes when the hedge risk measure has a `currency` parameter, because both sides are then converted into it.
6. **Cash** is kept per currency (`cash_dict[d][ccy]`, gs), keyed by **ISO code from the value's unit** (DEV-E13), not through `map_ccy_name_to_ccy`. The cash accrual model applies per currency (gs `get_accrued_value` already loops over currencies).
7. **`initial_value`** is seeded on the **first grid date** in `result_ccy` if given, else in the currency of the first asset priced (DEV-E11).

---

## 8. Risk measures and results

### 8.1 Risk-measure objects (`pricebt.risk`)

Reimplement gs's identity semantics exactly (R03§14.1, R04§9):
- `RiskMeasure` is a frozen, hashable dataclass `(asset_class, measure_type, unit, parameters, value, name)`. Equality and hashing cover all fields, so `Price != Price(currency='USD')`.
- `ParameterisedRiskMeasure` subclasses are **callable**: `IRDelta(aggregation_level='Type', currency='USD')` returns a clone with the parameters merged (unset arguments inherit). A pandas Series/DataFrame first argument returns `self` (the gs `.loc` hack). String `aggregation_level` is coerced to `AggregationLevel`.
- `repr` is `name` when there are no parameters, else `Name(k:v, ...)` with keys sorted case-insensitively (`Price(value:USD)`, `IRDelta(aggregation_level:Type, currency:USD)`).
- `__lt__` sorts by name, then by parameters.
- Classes: `RiskMeasureWithCurrencyParameter` (`Price`, `EqDelta`, `EqVega`, `Annuity`) with `__call__(currency=None, name=None)`; `RiskMeasureWithFiniteDifferenceParameter` (`IRDelta`, `IRDeltaParallel` = preset `aggregation_level=Asset` + name, `IRDeltaLocalCcy` = preset `currency='local'`, `IRVega*`, `IRBasis`, `IRXccyDelta`, `InflationDelta`, `FXDelta`, …) with `__call__(aggregation_level=None, bump_size=None, currency=None, finite_difference_method=None, local_curve=None, mkt_marking_options=None, scale_factor=None, name=None)`; plain `RiskMeasure` (`DollarPrice`, `IRGamma`, `IRGammaParallel`, `IRFwdRate`, `IRSpotRate`, `IRDailyImpliedVol`, `IRAnnualImpliedVol`, `Cashflows`, `ResolvedInstrumentValues`).

**Measure → config function (the mapping rule, generic for every asset class):**
1. `ResolvedInstrumentValues` → `PricingService.resolve` (not a function lookup).
2. `DollarPrice` → the `Price` mapping with `currency='USD'`.
3. Otherwise, look up `asset.risk_measures[measure.name]`. Missing → `ConfigError(f"asset {a} has no mapping for risk measure {measure.name}; add it under risk_measures:")`.
4. If the mapping is a string → that function (a `functions:` entry, or a `portfolio_functions:` entry evaluated with this single trade).
5. If the mapping is `{scalar: f, bucketed: g}`: `aggregation_level` in `{Type, Asset, Class}` → `f` (scalar). `aggregation_level` `None` or `Point` → `g` (bucketed). If only one of the two keys is given, the other form is derived: scalar = sum of the buckets; requesting a bucketed form with no `bucketed` key → `ConfigError`.
6. Apply `quantity` and, if the measure has a `currency` parameter that differs from local, FX (§7).

### 8.2 Result objects (`pricebt.risk.results`): the subset the engine and notebooks use

| Class | Must provide |
|---|---|
| `RiskKey` | namedtuple `(provider, date, market, params, scenario, risk_measure)`; pricebt fills `date` and `risk_measure`, the rest `None` |
| `FloatWithInfo(float)` | `.risk_key`, `.unit` (dict), `.error` (None), `.raw_value`; `+` with an equal unit → `FloatWithInfo`, unequal → `ValueError('FloatWithInfo unit mismatch')`; `repr` `1500.0 (USD)` |
| `DataFrameWithInfo(pd.DataFrame)` | `_metadata = ['risk_key', 'unit', 'error']`; `.raw_value`; a bucketed IR result has columns **`mkt_type, mkt_asset, mkt_class, mkt_point, value`** (labels from the portfolio function's `labels:`; `mkt_point` = the bucket key), sorted with gs `point_sort_order` semantics (tenor order) |
| `SeriesWithInfo(pd.Series)` | same metadata; historical scalar results indexed by date |
| `ErrorValue` | `(risk_key, error)`; `.raw_value = None` |
| `MultipleRiskMeasureResult(dict)` | keyed by measure |
| `PortfolioRiskResult` | `(portfolio, risk_measures, futures_or_values)` constructor (gs order, used by ExitTradeActionImpl); `.portfolio`, `.risk_measures`, `.dates`; `__getitem__`: a RiskMeasure → a single-measure view; an `Instrument` or a name `str` → that instrument's value(s); a `date` → a single-date view; an `int` → by position; `__iter__` → per-instrument values (for a single measure, e.g. resolved instruments); `__contains__`; `__len__`; `__add__` (merge by instrument name; the right side wins on conflict; gs `add_results`); `.aggregate(allow_mismatch_risk_keys=False, allow_heterogeneous_types=False)` → a sum over instruments (FloatWithInfo, or an aggregated DataFrameWithInfo = concat + groupby(all but `value`) + sum); `.transform(risk_transformation=None)` → `self` when None, else `risk_transformation.apply(self)`; `.to_frame(values='default', index='default', columns='default', aggfunc='sum')` supporting at least `values='value', index='instrument_name', columns='risk_measure'` (bucketed values summed per instrument, R03§7); `.result()` → self; `.done()` → True |

**Vector (bucketed) aggregation uses the portfolio function directly.** `PortfolioRiskResult[bucketed_risk].aggregate()` groups the held instruments by (asset, market key) and calls the asset's portfolio function **once per group** with all of the group's trades and `weights = quantities`, instead of summing per-instrument calls. Per-instrument bucketed values (needed only by `to_frame`/`strategy_as_time_series`/`result[inst]`) are computed **lazily**, by calling the portfolio function with that single trade. This honours the user's requirement ("a delta ladder that takes a curve and a portfolio/collection of swaps") and keeps a daily run from recalibrating once per trade. Because ladders are linear, both routes agree (a test asserts sum(per-trade) == group call to 1e-9 relative on the toy).

### 8.3 `BackTest` views

Port 2.1.17 `backtest_objects.BackTest` (`result_summary`, `risk_summary`, `get_risk_summary_df`, `trade_ledger()`, `strategy_as_time_series()`, `pnl_explain()`, `summary_stats()`, properties). Shapes and column names are exactly those in R03§4–§8, with the fixes in §11 (DEV-R*). Key invariants:
- `result_summary` columns: `[*risk columns in deterministic order: the user's run_backtest risks first, then strategy risks, then the price measure if missing], 'Cumulative Cash', 'Transaction Costs', 'Total'`. Risk column labels are the RiskMeasure objects. `Total = price_measure + Cumulative Cash + Transaction Costs`.
- `trade_ledger()` has exactly the columns `Open, Close, Open Value, Close Value, Long Short, Status, Trade PnL`, object dtype, indexed by trade name.
- `strategy_as_time_series()` has row MultiIndex `('Pricing Date', 'Instrument Name')` and column groups `'Static Instrument Data' | 'Risk Measures' | 'Cash Payments'`. The static data columns are `asset_class`, `type`, then the sorted resolved terms (after `attributes` for `size_attribute`), plus `quantity` (DEV-I2).

### 8.4 `BackTest.pnl_bps()`: pricebt extension (MUST-4)

```python
def pnl_bps(self, risk: RiskMeasure = IRDeltaParallel, min_abs_risk: float = 1e-9) -> pd.DataFrame:
    """P&L expressed in bp of rate move: daily P&L divided by the PREVIOUS row's risk (currency per bp).
    Columns: 'PnL' (Total.diff()), 'Risk' (result_summary[risk] shifted by one row), 'PnL (bps)', 'Cumulative PnL (bps)'.
    'PnL (bps)' = PnL / Risk when |Risk| >= min_abs_risk, else NaN; cumulative = the running sum with NaN treated as 0.
    Raises ValueError(f"{risk} is not a column of result_summary; add it to run_backtest(risks=[...])") when absent."""
```

It is meaningful for directional books (a flat-hedged book has risk ≈ 0, which gives NaN). It is currency-neutral: a P&L in EUR divided by a EUR/bp risk. Document it in `ASSET_CONFIG_GUIDE.md` and in `DEVIATIONS.md` (as an addition).

---

## 9. The backtest port — `pricebt.backtests`

### 9.1 Porting rule

For every in-scope file, **copy the 2.1.17 source**, keep its Apache-2.0 header, and add a second header line: `# Ported to pricebt from gs_quant 2.1.17 (Apache-2.0); see NOTICE. Changes: <list DEV ids>`. Then:
1. Replace imports according to the import map (§9.2).
2. Delete JSON/serialisation plumbing: `@dataclass_json`, `config(...)` field metadata, `encode_*`/`decode_*`, `dc_decode`, the JSON-registry patch at the end of triggers.py, and `Tracer` (replace `self._trace(...)` with a `contextlib.nullcontext()`).
3. Apply the deviations listed for that file in §11, and **no other behavioural change**.
4. Keep class names, field order, defaults, method names and comments. Keep `class_type` static fields (they are harmless and part of the dataclass shape).

### 9.2 Import map (gs → pricebt)

| gs import | pricebt replacement |
|---|---|
| `gs_quant.backtests.*` | `pricebt.backtests.*` (same submodule) |
| `gs_quant.markets.PricingContext`, `HistoricalPricingContext` | `pricebt.markets` |
| `gs_quant.markets.portfolio.Portfolio` | `pricebt.markets.portfolio.Portfolio` |
| `gs_quant.instrument.Instrument, Cash, IRSwap, EqOption, EqVarianceSwap` | `pricebt.instrument` (`EqVarianceSwap`: omit; it is only used by EquityVolEngine) |
| `gs_quant.risk.*` (measures), `gs_quant.target.measures.ResolvedInstrumentValues` | `pricebt.risk` |
| `gs_quant.risk.results.PortfolioRiskResult, PricingFuture` | `pricebt.risk.results` |
| `gs_quant.risk.transform.Transformer` | `pricebt.risk.transform` |
| `gs_quant.common.RiskMeasure, ParameterisedRiskMeasure, Currency, CurrencyName, AssetClass, BuySell, OptionType, TradeAs` | `pricebt.common` |
| `gs_quant.base.Priceable, EnumBase, Base, field_metadata, static_field, exclude_none, get_enum_value` | `pricebt.base` (`field_metadata` → `None`-safe `field(default=None)` helper; `static_field(v)` → `field(init=False, default=v)`) |
| `gs_quant.datetime.relative_date.RelativeDate, RelativeDateSchedule` | `pricebt.datetime.relative_date` |
| `gs_quant.datetime.business_day_offset, is_business_day, prev_business_date` | `pricebt.datetime` |
| `gs_quant.timeseries.interpolate, Interpolate` | inline: `interpolate_signal` reimplemented with `reindex(daily).ffill()` (R01§5.6) |
| `gs_quant.data.Dataset, DataFrequency` | `pricebt.data` (Dataset is a stub) |
| `gs_quant.context_base.nullcontext` | `contextlib.nullcontext` |
| `gs_quant.tracing.Tracer` | delete (see 9.1.2) |
| `gs_quant.errors.MqValueError` | `ValueError` |
| `gs_quant.target.backtests.BacktestTradingQuantityType` | `pricebt.backtests.core.BacktestTradingQuantityType` (enum: notional, quantity, vega, gamma, NAV, premium, vegaNotional) |
| `gs_quant.api.*`, `gs_quant.json_convertors*`, `dataclasses_json` | delete |

### 9.3 Scope per file

| pricebt file | Source (2.1.17) | Scope |
|---|---|---|
| `backtests/backtest_utils.py` | `backtest_utils.py` | full port; `get_final_date` per DEV-T1/T2/T3; `map_ccy_name_to_ccy` kept for API, unused by the engine |
| `backtests/core.py` | `core.py` | enums and NamedTuples; `Backtest`/`BacktestResult` → stubs raising NotSupportedError |
| `backtests/strategy.py` | `strategy.py` | full; `get_available_engines` per DEV-T14 (returns `[GenericEngine()]` when supported) |
| `backtests/data_sources.py` | `data_sources.py` | `MissingDataStrategy`, `DataSource`, `GenericDataSource` rewritten per DEV-T13 (spec R01§7.4 "v2 specification"); `GsDataSource`, `DataManager` → stubs |
| `backtests/triggers.py` | `triggers.py` | all triggers/requirements; MR = 2.1.17 + reset; fixes DEV-T4..T12 |
| `backtests/actions.py` | `actions.py` | all actions incl. `AddWeightedTradeAction`, `EarlyExitPositionLimitScaledAction`; `EnterPositionQuantityScaledAction`/`ExitPositionAction` classes exist (API) but GenericEngine has no handler (gs) |
| `backtests/action_handler.py`, `backtest_engine.py` | same | full |
| `backtests/backtest_objects.py` | `backtest_objects.py` | `BackTest` (+ `pnl_bps`), `ScalingPortfolio`, `WeightedScalingPortfolio`, transaction models, `TransactionCostEntry`, `CashPayment`, `Hedge`, `WeightedTrade`, `CashAccrualModel`, `ConstantCashAccrualModel`, `DataCashAccrualModel`, `PnlAttribute`, `PnlDefinition`; `OisFixingCashAccrualModel` and `PredefinedAssetBacktest` → stubs |
| `backtests/generic_engine_action_impls.py` | same | full, with DEV-E fixes |
| `backtests/generic_engine.py` | same | full, with DEV-E fixes + the missing-market pre-scan (§9.5) + the progress bar |
| stubs | `equity_vol_engine.py`, `predefined_asset_engine.py`, `strategy_systematic.py`, `order.py`, `event.py`, `data_handler.py`, `execution_engine.py` | module with the public class names; constructors raise `NotSupportedError("... is GS-server-only / out of scope in pricebt v2; use GenericEngine")` |

### 9.4 Engine points where gs reads instrument fields (resolved generically)

| gs code | pricebt behaviour |
|---|---|
| `get_final_date`: `hasattr(inst, str(duration))` → `getattr` (e.g. `'termination_date'`) | `Instrument.__getattr__` → the asset `attributes:` expression, else resolved terms, else kwargs. Returning a `date`/`datetime` makes it a final date. |
| `ScaledTransactionModel(scaling_type='notional_amount')` → `getattr(instrument, 'notional_amount')` | same attribute lookup (the ARBS config defines `notional_amount = |notional| × |quantity|`) |
| `ScaledTransactionModel(scaling_type=<RiskMeasure>)` → `with PricingContext(state): instrument.calc(risk)` | works unchanged through §6.5 |
| `RebalanceAction(priceable, size_parameter, method)`: `current_size = Σ getattr(t, size_parameter)`; new position `priceable.clone(**{size_parameter: new-current})` | `current_size` via attribute lookup; the new position is `priceable.clone(quantity=(new_size - current_size) / unit_size, name=...)` with `unit_size = getattr(priceable.clone(quantity=1.0), size_parameter)`; `size_parameter` MUST equal the asset's `size_attribute`, else `ConfigError`. A numeric `size_parameter` (gs allows `float`) → `NotSupportedError`. |
| `Portfolio.scale(s)` / `Instrument.scale(s)` | quantity multiply (§5.4) |
| `t.to_dict()` set-membership in ExitTradeActionImpl (a gs bug: unhashable) | compare `instrument_identity(t)` = `(class, asset, frozen resolved_terms or kwargs, quantity)` (DEV-I3) |
| `map_ccy_name_to_ccy(next(iter(value.unit)))` | `next(iter(value.unit))` is already the ISO code (DEV-E13) |
| `dt.date.today()` guards (hedge exit payment, NAV unwind pricing, ScaledTransactionModel `state > today → nan`) | compare with the **backtest end date** (`backtest.states[-1]`) instead of today (DEV-E12) |

### 9.5 Missing-market pre-scan (decision 0.4)

In `GenericEngine.run_backtest`, after the grid (`strategy_pricing_dates`) is built and **before** Phase 2:
1. Collect the assets the strategy can trade: the initial-portfolio instruments, every action's `priceables`/`priceable` (flattening Portfolios, incl. HedgeAction/AddWeighted), with asset matching per §5.3.
2. For each grid date `d` and each asset `a`: `pricing.has_market(a, d, csa)` (csa = `run_backtest(csa_term)`; a HedgeAction's own `csa_term` is checked too, for its asset).
3. `missing_market='raise'` → `MarketDataUnavailable` listing the first 10 missing (asset, date) pairs. `'drop'` → remove those dates from the grid and from every trigger's date lists (filter `get_trigger_times()` results through the kept set), `warnings.warn(f"pricebt: dropped {n} dates with no market data ({first}..{last}); see backtest.missing_market_dates")`, and store the sorted list on `backtest.missing_market_dates` (a new attribute, empty list when nothing was dropped).
4. The markets evaluated here stay in the cache (no double work).

### 9.6 Progress bar

`run_backtest(show_progress=True)` shows **one** tqdm bar over the pricing phases (`desc="pricebt"`), advanced once per (phase, date) of the Phase 4 batch pricing, the Phase 5 date loop and the cash walk. Use the salvaged `pricebt.progress` (its notebook-kernel path updates a single display output; v1 `tests/test_progress_notebook.py` shows the pattern). `show_progress=False` → no bar.

---

## 10. Data helpers — `pricebt.data`

- `Dataset(...)` → `NotSupportedError("Marquee datasets are GS infrastructure; build a pandas Series from your own data or use pricebt.data.measure_series(...)")`.
- `DataFrequency` enum: `DAILY='daily', REAL_TIME='realTime', ANY='any'`.
- **`measure_series(instrument, measure, start, end, frequency='1b', holiday_calendar=None, csa=None, fill='ffill') -> pd.Series`** (pricebt extension, needed by the 040304 port, R05§6.4):
  1. `dates = RelativeDateSchedule(frequency, start, end).apply_rule(holiday_calendar=holiday_calendar)`.
  2. For each `d`: if there is no market → a missing point; else **resolve a fresh copy of the (unresolved) instrument on `d`** and evaluate `measure` (a function name `str` of the asset config, or a `RiskMeasure` through §8.1) for quantity 1.
  3. Index = `datetime.date`; the value unit is the function's declared unit (bp for the ARBS `par_rate`).
  4. Missing points: `fill='ffill'` → forward-filled from the previous available date (leading missing dates are dropped, not back-filled). `fill=None` → dropped. Log the count at INFO level.
  5. The result has `series.attrs["unit"]` = the unit string and `series.attrs["missing_dates"]` = the list.

  Building on the full weekday schedule with forward fill means `GenericDataSource` never takes its insertion path, which is exactly the gs semantics (R05§6.2).

---

## 11. Deviations from gs (decision 0.3) — each gets a row in `docs/v2/DEVIATIONS.md` and a test

IDs are referenced from code headers and tests. "gs" means 2.1.17 unless stated otherwise.

**Triggers / utils / data (R01§12)**

| ID | Where | gs behaviour | pricebt behaviour |
|---|---|---|---|
| DEV-T1 | `get_final_date` | a `timedelta` duration crashes | `create_date + duration` |
| DEV-T2 | `get_final_date` | module-global cache, never cleared | cache cleared at the start of every `run_backtest` |
| DEV-T3 | business-day rules | `holiday_calendar=None` → `GsCalendar` lookup | weekends only (`week_mask='1111100'`), no data access |
| DEV-T4 | `PeriodicTriggerRequirements.start_date=None` | today / ambient context | the backtest start date (set by the engine before trigger evaluation) |
| DEV-T5 | `NotTriggerRequirements.calc_type` | always `simple` | the child's `calc_type` |
| DEV-T6 | `NotTriggerRequirements.__setattr__` | drops every attribute except `trigger` | normal attributes |
| DEV-T7 | Aggregate/Not `get_trigger_times` | `[]` | union of the children's times |
| DEV-T8 | `AggregateTriggerRequirements()` with no triggers | `TypeError` | `ValueError('triggers required')` |
| DEV-T9 | intraday schedule | infinite loop on midnight wrap | stop at the wrap |
| DEV-T10 | stateful triggers (MeanReversion) | state persists across `run_backtest` calls | the engine calls `reset()` on every trigger requirement at run start (MR: `current_position = 0`); the base `TriggerRequirements.reset()` is a no-op |
| DEV-T11 | trigger_info routing | parallel lists zipped positionally | infos keyed by date (`{date: info}`); the action impls look up by date |
| DEV-T12 | `EventTriggerRequirements.data_source=None` | GS event calendar | `ValueError('data_source required')` |
| DEV-T13 | `GenericDataSource` | mutates the series; date-index ffill look-ahead; `DatetimeIndex` vs `date` TypeError | the R01§7.4 v2 specification: normalised sorted index, no mutation, correct ffill/interpolate |
| DEV-T14 | `Strategy.get_available_engines` | zero triggers → `TypeError` | `[GenericEngine()]` |
| DEV-T15 | `PeriodicTriggerRequirements` frequency, `get_final_date` tenor | 1.5.4: `'1M'` = Nth Monday | lower-cased (as 2.1.17) |

**Engine (R02§7)**

| ID | Where | gs behaviour | pricebt behaviour |
|---|---|---|---|
| DEV-E1 | per-date loop | a path-dependent trigger is evaluated **before** the day's risks are ensured (stale results after a rebalance) | `ensure(d)` first, then `has_triggered(d)` |
| DEV-E2 | per-date loop | a simple trigger is re-evaluated once per path-dependent action | evaluate once per date, cache the `TriggerInfo` |
| DEV-E3 | hedge skipped (`hedge_risk == 0` or no results) | its transaction costs are still booked | skipped hedge → its TCEs are removed |
| DEV-E4 | initial portfolio exit payment | uses the un-renamed, unresolved instrument → spurious ledger row `Open 9999-12-31` | uses the renamed, resolved instrument; no spurious row |
| DEV-E5 | `ExitTradeAction(priceable_names)` | splits names on `_`; crashes when hedges are held; underscores in names break it | each position stores structured metadata `(action_name, priceable_name, create_date)`; matching uses it; display names unchanged |
| DEV-E6 | `RebalanceActionImpl` | `append(exit)` (builtin) → crash | appends the exit TCE |
| DEV-E7 | `HedgeAction.__post_init__` | `if not Portfolio` never fires; bad input fails late | `ValueError('HedgeAction requires a priceable or Portfolio')` |
| DEV-E8 | `backtest.results[d]` reads | defaultdict creates `[]` entries | `.get(d)` |
| DEV-E9 | hedge `trade_duration` attribute | looked up on the Portfolio (fails) | looked up on the single hedge instrument when the portfolio has one leaf |
| DEV-E10 | `ExitTradeAction.priceable_names` | a bare string is substring-matched; `priceables_names` typo | `make_list(priceable_names)` |
| DEV-E11 | `initial_value` | appears only from the first cash-payment date, in that payment's currency | seeded on the first grid date, in `result_ccy` or the first asset's currency |
| DEV-E12 | `dt.date.today()` guards | server has no future market | compared with the backtest end date |
| DEV-E13 | cash currency | `map_ccy_name_to_ccy` of the unit's long name (unknown → `None`) | ISO code from the unit (from the asset config) |
| DEV-E14 | risk list | `list(set(...))`, non-deterministic column order | ordered de-duplication: user `risks`, then `strategy.risks`, then pnl risks, then the price measure |
| DEV-E15 | multi-currency | only via server-side `result_ccy` | `result_ccy` converts through the FX config; errors otherwise as gs (§7) |

**Results (R03§4.4)**

| ID | Where | gs behaviour | pricebt behaviour |
|---|---|---|---|
| DEV-R1 | `result_summary` on a date with no holdings | `ffill` copies the previous PV while cash already has the exit proceeds → `Total` double counts | dates in `cash_dict` with no holdings get PV 0 and risk 0 before the ffill (the `zero_on_empty_dates` semantics), so `Total` is correct |
| DEV-R2 | `get_risk_summary_df` cache | computed once, never invalidated | recomputed on each call (no cache) |
| DEV-R3 | no results at all | `TypeError` on slicing a RangeIndex | an empty frame with the correct columns |
| DEV-R4 | bucketed cells | ffilled stale ladders on flat dates | follow DEV-R1 (zero) |

**Instruments / risk**

| ID | Where | gs behaviour | pricebt behaviour |
|---|---|---|---|
| DEV-I1 | scaling | edits size fields (`notional_amount`, `pay_or_receive`, `fee`) | signed `quantity` multiplier (§5.4); kwargs never edited |
| DEV-I2 | `strategy_as_time_series` static data | resolved gs fields | resolved terms + `quantity` column |
| DEV-I3 | `to_dict()` identity in ExitTradeAction | unhashable dict → latent crash | `instrument_identity()` tuple |
| DEV-I4 | `IRDelta(aggregation_level=Type/Asset/Class)` result type | `IRDelta` with Type returns a small DataFrame (only `IRDeltaParallel` collapses to a float) | a `FloatWithInfo` for Type/Asset/Class; a bucketed `DataFrameWithInfo` for None/Point |
| DEV-I5 | unparameterised currency-bearing risk | IRDelta docstring: USD | local currency (decision 0.5) |
| DEV-I6 | relative/instrument strings (`'100k'`, `'ATM+25'`, `'=solvefor(...)'`) | parsed server-side | not parsed by pricebt; the asset `resolve` expression decides; unsupported → the config raises |
| DEV-I7 | rate measure units (`IRFwdRate` percent in gs) | percent | whatever unit the asset function declares; shipped configs use bp (MUST-4) |

**Kept on purpose (document them in DEVIATIONS.md as "parity kept"):** ALL_OF short-circuit / ANY_OF evaluates every child; Portfolio `'len'` counts dates; strict `>`/`<` in `check_barrier`; the MR window excludes today; sample std (ddof=1); zero std → inf → fires; `get_data_range` is `(start, end]` / an int means the last N strictly before; hedges on one date are sized against the same pre-hedge risk; the global `Action{N}` counter; the holding window `create_date <= s < final_date`; entry cash `−PV(create)` and exit cash `+PV(final)`; `trade_ledger` `Long Short` = the direction of the opening payment (always −1, or 0 for same-day).

---

## 12. Verification strategy

### 12.1 Test tiers (pytest markers)

| Marker | Needs | Runs in CI | Content |
|---|---|---|---|
| `core` (default for all unmarked tests via conftest) | nothing external | yes | unit + scenario tests on the toy library |
| `notebook` | nbclient, ipykernel | yes (slow) | executes `notebooks/040304_mean_reversion_toy.ipynb` |
| `live_arbs` | `PRICEBT_LIVE_ARBS=1` and the ARBS checkout | **no**; opt-in, and the first run needs user approval (IMPLEMENTATION_PLAN §Stop-and-ask) | loads the ARBS asset config, prices a few dates, runs a short 040304 |

### 12.2 Zero-dependency guards (MUST-1): `tests/guards/`

1. **Import scan (AST):** every `import`/`from` in `src/pricebt/**/*.py` has a root in `{stdlib (sys.stdlib_module_names), numpy, pandas, yaml, dateutil, tqdm, pricebt}`. Anything else fails, naming the file and line.
2. **Token scan:** a case-insensitive regex with word boundaries over `src/pricebt/**` (code and strings, **excluding** comment lines and the Apache header block) for `\b(arbs|rateslib|quantlib|irswapsmdp|eris|bloomberg|refinitiv|marquee)\b`, plus `gs_quant` in code (not in comments or docstrings). Each hit fails with file:line.
3. **Import blocker:** a subprocess test installs a `sys.meta_path` finder that raises `ImportError` for `gs_quant`, `rateslib`, `QuantLib`, `MDP`, `Query`, `Caching`, `dataclasses_json`, then imports every pricebt module and runs the toy MR backtest end to end.
4. **Non-vacuity twins:** each guard has a test that feeds it a synthetic offending file and asserts it fails (the global CLAUDE.md rule: verify the checker on a known answer).

### 12.3 gs API parity snapshot (MUST-2)

- `tools/gs_api_snapshot.py`, run with **the base python** (gs 1.5.4), writes `tests/data/gs_api_1_5_4.json`. For each symbol in the parity list (every public class/function of `gs_quant.backtests.{strategy,triggers,actions,data_sources,backtest_objects,backtest_utils,generic_engine,core}` that is in scope per §9.3, plus `gs_quant.instrument.{IRSwap,IRSwaption,FXOption,FXForward,EqOption,InflationSwap,Cash}`, `gs_quant.risk` measures used in §8.1, `gs_quant.common` enums in §3.2, `gs_quant.markets.{PricingContext,HistoricalPricingContext}`, `gs_quant.markets.portfolio.Portfolio`, `gs_quant.datetime` functions, `gs_quant.session.GsSession.use`, `gs_quant.session.Environment`) it records: the kind; for callables the `inspect.signature` parameters `(name, kind, default repr)` in order; for dataclasses the fields `(name, init, default repr)` in order; for classes the public method and property names; for enums `[(name, value)]`; for risk measures `(class name, name, measure_type)`.
- `tests/test_gs_api_parity.py` (tier `core`; **never imports gs_quant**) loads the JSON and compares the pricebt symbol at the mapped path. Allowed differences are listed in `tests/data/gs_api_exceptions.yaml` as `{symbol, aspect, reason, dev_id}`; any unlisted difference fails. Expected exceptions: the extra `asset`/`quantity`/`**kwargs` on instruments, stubs, and the fields 2.1.17 appended.
- The implementer MUST regenerate the JSON only with the base python. The file header records `gs_quant.__version__` and the date.

### 12.4 Golden and scenario tests (toy library; exact numbers asserted)

| Test | Asserts |
|---|---|
| `test_relative_date.py` | every row of R04§8.3 and R01§5.5 (rules, schedules, holiday case, weekend start kept) |
| `test_mean_reversion_golden.py` | the 19-row table of R01§6.5.9 (triggered, scaling, position), fed through `GenericDataSource` + `MeanReversionTriggerRequirements` |
| `test_generic_data_source.py` | R01§7.4 v2 spec: ffill = previous value (2.0 not 5.0), interpolate by position, no mutation, index normalisation, `get_data_range` rules |
| `test_result_shapes.py` | R03§9 cases A, B (with DEV-R1: `Total` 2290 not 3790), E: exact frames |
| `test_engine_periodic_roll.py` | 040300 swap variant: ledger names `Action{N}_10y_{date}`, open/close dates, `Trade PnL = Close + Open`, Total identity on every row |
| `test_engine_hedge.py` | 040310 variant (toy EUR, `HedgeAction(IRDelta(aggregation_level='Type'), hedge, csa_term='EUR-OIS')`): the book `IRDelta` ≈ 0 after each daily hedge; `pricebt_csa` reaches the hedge's resolve and not later pricing |
| `test_engine_exit_trade.py` | `ExitTradeAction` by priceable name incl. names with underscores (DEV-E5), same-day in/out → direction 0 row |
| `test_engine_scaled_add.py` | `AddScaledTradeAction` size / risk_measure / NAV on the toy |
| `test_engine_rebalance.py` | `RebalanceAction` with `size_parameter=size_attribute` |
| `test_multi_currency.py` | USD + EUR toy assets + toy FX: mixed book without `result_ccy` raises the gs errors with the hint; with `result_ccy='USD'` all columns are in USD and equal hand-computed conversions; `IRDelta(currency='USD')` hedging across currencies |
| `test_pnl_bps.py` | `pnl_bps()` = hand computation; NaN when risk ≈ 0; error when the column is absent |
| `test_ladder.py` | group call == sum of per-trade calls; the DataFrame cell shape and labels; bucket order |
| `test_missing_market.py` | `'drop'` warns, lists dates, removes them from the grid and trigger dates; `'raise'` raises |
| `test_asset_config.py` | schema validation errors (unknown key → did-you-mean; bad unit; missing Price mapping), compile-time syntax errors, lazy import (no exec at load), isolated namespaces, `AssetEvaluationError` content, resolve plain-data validation, `test_resolve_pins_dates` |
| `test_instrument.py` | positional gs order, camelCase, enum coercion errors, eq/hash, clone carries resolution, scale guard, attributes lookup order, matching (0/1/many) |
| `test_pricing_cache.py` | market evaluated once per (key, date, csa); unit values shared between a hedge unit and its scaled copy; `reset()` clears |
| `test_040304_toy.py` | the converted notebook's code (cells from `AddTradeAction` on) run as a script on the toy asset; frame shapes as R05§6.5 |

### 12.5 Mutation check

At each phase gate the implementer runs `tools/mutcheck.py` on at least 3 hand-picked one-line mutations per phase (e.g. flip `<` in the MR short close, drop DEV-R1's zeroing, use `today` instead of the end date) and confirms that a named test fails for each (global CLAUDE.md: "mutate the code it covers and confirm the test actually fails").

---

## 13. Extensibility test: adding swaptions later (MUST-5)

Nothing in §4–§11 is swap-specific. Adding swaptions will need:
1. `IRSwaption` already exists in `pricebt.instrument` with gs's field order (from `_gs_fields.py`, R04§2). **No code change.**
2. Write `configs/assets/usd_sofr_swaption.yaml`: `instrument: IRSwaption`, `match: {notional_currency: USD}`; a `market` that returns the curve **and** a vol surface (e.g. a tuple or a small object built in `code`; sharing the curve with the swap asset via `market.key` is optional); a `resolve` that pins `expiration_date`, `termination_date` and the strike (`'ATM'`, `'A-50'`, …); functions `npv`, `dv01`, `vega` (`unit: ccy_per_bp`), `gamma`; `attributes: {expiration_date: 'resolved["expiration_date"]'}` so that `AddTradeAction(option, 'expiration_date')` works; `risk_measures: {Price: npv, IRDelta: {scalar: dv01}, IRVega: {scalar: vega}}`.
3. Sign: a sold swaption is `quantity = -1` (gs `buy_sell='Sell'` handled in `resolve`, or pricebt's scaling does it; the config chooses one convention and documents it).
4. Notebooks 040303, 040305, 040307 and `Backtesting.ipynb` then port (R05§4).

A design review MUST fail any change that makes the engine, pricing layer or results aware of an asset class.

---

## 14. Later work (explicitly not in this plan)

- Swaption asset config (the §13 walkthrough).
- A ladder-hedge action (vector hedge over several instruments using the `bucketed` portfolio function).
- EUR/GBP/JPY real-data configs. ARBS serves these only via `GSQUANT-RL`, which imports gs_quant inside ARBS and ends 2026-08-03 (R06§2). That is acceptable inside a user config, but it is a separate hazard review.
- Coupon cash between marks (gs books only entry and exit PVs, so a coupon paid while a swap is held falls out of PV without reaching cash; gs has the same property). If wanted: an optional `cashflows` function in the config plus a pricebt cash hook. This is a deviation, so ask first.
- A PredefinedAssetEngine equivalent; JSON serialisation.

---

## Appendix A — The ARBS example asset config (`configs/assets/usd_sofr_ois_interest_rate_swap.yaml`)

Every ARBS method below is verified in R06§3; signs and units are verified in R06§3.5. The implementer copies this file, then runs the live checks in IMPLEMENTATION_PLAN Phase 5 **only after the user approves** the first live run.

```yaml
schema_version: 1
asset: usd_sofr_ois_interest_rate_swap
description: >
  USD SOFR OIS swap priced by ARBS on Eris EOD curves (store-only; never reaches the network, never writes the store).
  Instrument: pricebt.instrument.IRSwap with gs fields. Units: npv USD; dv01 USD per +1bp (payer > 0); rates in bp.
instrument: IRSwap
match: {notional_currency: USD}
currency: USD
defaults:
  pay_or_receive: Receive          # gs server default (R04 s1)
  termination_date: 10y
  notional_currency: USD
  fixed_rate: ATM
  notional_amount: 1.0e6           # gs does not document one; pricebt makes it explicit

imports: |
  import os, sys, re, contextlib, io
  os.environ["ARBS_SUPABASE_ENABLED"] = "0"          # MUST precede ANY ARBS import (R06 s6.2): no production database
  if r"C:\Users\chris\clee\ARBS" not in sys.path:
      sys.path.insert(0, r"C:\Users\chris\clee\ARBS")
  import datetime as _dt
  import pandas as pd
  import rateslib as rl
  from MDP.IRSwaps.IRSwapsMDP import IRSwapsMDP
  from Query.IRSwaps.backends.rateslib.RLIRSwapCurve import RLIRSwapCurve

code: |
  _MDP = IRSwapsMDP(source="ERIS_EOD_LIVE-RL_BASIC")   # store-backed Eris, 2020-07-01..2026-08-20 (R06 s1.3). Construct ONCE.
  # _MDP = IRSwapsMDP(source="ERIS_EOD_LIVE-RL_BASIC-NOJUMPS")   # DO NOT enable without user approval: network + overwrites
  #                                                              # curve-store partitions USD-SOFR-1D/date=<d> (R06 s1.3, s6.1)
  _CURVE = "USD-SOFR-1D"
  _ATM = re.compile(r"^\s*(atm|a)\s*(?:([+-])\s*(\d+(?:\.\d+)?))?\s*$", re.I)   # 'ATM', 'ATM+25', 'a-100' (bp)

  def load_market(d):
      # store-only read: None on a miss, no network, no store write (R06 s1.4b). A fresh dict per call (ARBS pops keys).
      if d >= _dt.date.today():
          return None                                          # ARBS turns a timestamp == today into "live" = NETWORK (R06 s1.4b)
      if d.weekday() >= 5:
          return None                                          # bulk_get_data drops non-business days; a list of only those raises
      m = _MDP.bulk_get_data(dict(curve_name=_CURVE, timestamps=[d], ignore_cache_miss=True)).get(d)
      if m is None:
          return None
      if m.reference_date().date() != d:                       # validate the serve, never trust it (R06 s1.6)
          raise ValueError(f"ARBS served reference date {m.reference_date().date()} for {d}")
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
      s = str(por)
      if s == "Pay": return 1.0
      if s == "Rec": return -1.0                                 # pricebt PayReceive.Receive has value 'Rec' (gs)
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

  def alive(m, t):
      return m.maturity_date(t).date() > m.reference_date().date()

  def remark(m, t):                        # the same swap rebuilt on market m (m's fixings); memo per market (R06 s3.4)
      memo = m.__dict__.setdefault("_pricebt_remark", {})
      hit = memo.get(id(t))
      if hit is not None and hit[0] is t:
          return hit[1]
      r = m.build_irswap(effective_date=m.effective_date(t), maturity_date=m.maturity_date(t),
                         fixed_rate=m.fixed_rate(t), notional=m.notional(t))
      memo[id(t)] = (t, r)
      return r

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
  expr: 'load_market(pricebt_date)'        # -> RLIRSwapCurve, or None when the store has no row for that date
  key: usd_sofr_eris_rlbasic
resolve:
  expr: 'resolve_swap(market, kwargs)'
trade:
  expr: 'build_swap(market, resolved)'
  build_on: resolve_date                   # npv/pv01 are safe on later markets; other functions use remark() (R06 s3.4)
functions:
  npv:        {expr: '0.0 if not alive(market, trade) else market.npv(trade)', unit: ccy}
  dv01:       {expr: '0.0 if not alive(market, trade) else market.pv01(remark(market, trade))', unit: ccy_per_bp}
  par_rate:   {expr: 'float("nan") if not alive(market, trade) else market.fair_rate(remark(market, trade)) * 1e4', unit: bp}
  fixed_rate: {expr: 'market.fixed_rate(trade) * 1e4', unit: bp}
  carry_1m:   {expr: '0.0 if not alive(market, trade) else market.carry_bps_running(remark(market, trade), "1m")', unit: bp}
  roll_1m:    {expr: '0.0 if not alive(market, trade) else market.roll_bps_running(remark(market, trade), "1m")', unit: bp}
portfolio_functions:
  delta_ladder:
    expr: 'delta_ladder(market, trades, weights, ("2Y", "5Y", "10Y", "30Y"))'
    unit: ccy_per_bp
    returns: buckets
    labels: {mkt_type: IR, mkt_asset: USD-SOFR-1D, mkt_class: OIS}
attributes:
  effective_date:   'resolved["effective_date"]'
  termination_date: 'resolved["termination_date"]'
  notional_amount:  'abs(resolved["notional"]) * abs(quantity)'
size_attribute: notional_amount
risk_measures:
  Price: npv
  IRDeltaParallel: dv01
  IRDelta: {scalar: dv01, bucketed: delta_ladder}
  IRDeltaLocalCcy: {scalar: dv01, bucketed: delta_ladder}
  IRFwdRate: par_rate
```

Known traps this config avoids (R06§3.2, §7): `dv01`, `gamma` and `dollar_carry` on `RLIRSwapCurve` raise `NotImplementedError`; `resolve_pricable` flips a payer into a receiver; `build_irswap(fwd=<tenor>)` counts from the reference date, not spot; `fair_rate` on an un-remarked trade from an earlier market uses stale fixings; a zero strike means par.

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

# replaces the Marquee Dataset: the 10y par rate (bp) of a fresh ATM 10y swap on every business day
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

Everything from `action = ...` on is byte-identical to the gs notebook (R05§6.3). The toy version differs only in the asset path and the dates.
