# 11 - Refactor spec: a backtester with ZERO dependence on any pricing or data-infrastructure library

Status: DRAFT for review. Written 2026-09-26 against the code snapshot in section 3.1. Nothing in this document has been implemented.
Audience: the maintainer and any implementing agent. Companion documents: `docs/DESIGN.md` (as built), `docs/design/00-spine.md` (original binding decisions), `docs/research/*` (evidence).

## 0. How to read this document

- **MUST / MUST NOT** are requirements. They define what "done" means and are verified by the tests in section 9. Do not weaken one to make a run pass; if a MUST is wrong, change the spec first and record why (section 13).
- **SHOULD / SHOULD NOT** are strong defaults; deviating needs a written reason in the change log.
- **MAY** is permission.
- Blocks labelled **"One acceptable design"** are illustrations. The requirement is the behaviour, not the syntax. Any design that satisfies the requirements and passes the acceptance tests is acceptable.
- Requirement ids (`Z1`, `S2`, ...) are stable; tests, PRs and the change log refer to them.

### 0.1 What this document supersedes

This spec overrides the following spine and design statements wherever they conflict. `DESIGN.md` is the as-built reference and is updated after implementation, not by this spec.

| Older statement | Where | Replaced by |
|---|---|---|
| "expected schema ... modelled on rateslib's instrument API" | spine R10 | S1-S6: schemas define pricebt's own contract; no library API is the model |
| `call_method` injects `pricer.api_kwargs()` by matching parameter names | spine 4.4, DESIGN 4 | B1-B7: explicit bindings; no inference |
| schema `measures: {x: {method: <name>, reducer: <name>}}` | spine 4.5, `schemas/*.yaml` | S3, B2: the schema names *what*, the config binds *how* |
| default `dv01_ad`, `dv01_solver` delta names | spine 12.3 | S2: standard measure names are library-neutral; extra definitions live in adapter extension schemas |
| layer resolution falls back to a same-named attribute | spine 4.3, `pricable.resolve_layer` | B6 |
| `calendar='nyc'` resolves through rateslib | `session.py` | C2 |
| `ConstantCashAccrual` gs_quant formula in core | `costs.py` | C1 |

### 0.2 Decisions recorded from review (2026-09-26)

| Question | Decision |
|---|---|
| Q1 same-name convenience | Adapters ship default binding blocks; strict mode is the default; no implicit same-name resolution (B6) |
| Q2 relative dates | Date handling comes from the external pricing library; pricebt passes dates and relative dates through and records the resolved terms (T4) |
| Q9 config key | Rename `pricables:` to `instruments:`; no alias and no compatibility layer (11.2) |
| Q13 delta ladder | Keep `delta_ladder` required on the `swap` schema; expected shape `dict[str, float]`, tenor string to currency per +1bp, needed for ladder-driven triggers and delta hedging (S4) |
| Q3 side aliases | Aliases (`pay/receive`, `buy/sell`, `long/short`) map onto one direction sign (T2) |
| Q4 `par` | The adapter's factory resolves `par` and returns the resolved rate; pricebt only carries the token (T4) |
| Q5 legacy bridge | Fully replaced, NO legacy support of any kind (11.2). `PricerPricable`, `LegacyPricer` and the `RequestMDP` wrapper are removed; foreign stacks plug in through bindings only (B8) |
| Q6 scope | Linear swaps and bonds only. Swaptions and volatility snapshots are out of scope and stripped for now (S6, 0.3) |
| Q7 tolerances | The Appendix B placeholders are accepted; the first successful run sets the real values (X3) |
| Q8 baseline decomposition | Uses the library's own bound dollar delta (`dv01`, scaled by position size) and its `rate` measure; pricebt chooses no driver variable and computes no rate (S7, L3) |
| Q10 data infrastructure | ALL external data infrastructure is removed from the package: no stores, no vendor-format readers (D6) |
| Q11 time accrual | `time_accrual` (lump or linear) is an adapter-level convention (C3) |
| Q12 facade stubs | The `EquityVolEngine` and `PredefinedAssetEngine` stubs stay as they are (F-5) |
| Q14 backtest-side dates | Optional delegation of trade durations and exit dates to a bound `advance_date`; the backtest calendar is the fallback (C2) |
| Q15 swaptions | Strip ALL swaption-related code, schema, configs, tests and docs for now; a later workflow re-introduces options. Focus: linear instruments implemented correctly and robustly (0.3) |
| Q16 notebooks and scripts | Notebooks and `tools/` scripts that use removed APIs are DELETED and recreated later; the showcase notebook (6.9) is the one new deliverable |
| Q17 test-support provider | `tests/support/` (test code, never shipped) (D6) |

### 0.3 Consequences of these decisions (read before approving)

1. **No backward compatibility.** This is a large rewrite. Old config keys (`pricables:`, `methods:`, `measures:` method names, `risk_models:`), `pricebt.legacy`, `RequestMDP`, the ARBS-shaped legacy stub and the tests that exercise them are removed, not translated. Notebooks under `notebooks/src` and the `tools/` scripts that use removed APIs are DELETED and recreated later (Q16); the one new notebook is the showcase (6.9).
2. **The package ships no data readers.** `contrib/stores` (curve store, FedInvest, Webull) is deleted (D6). The real-data swap and bond suites and the tie-out run on a test-support provider under `tests/support/` that reads `data/fixtures`. Their configs reference it by dotted path.
3. **Swaptions and volatility are stripped (Q15).** `schemas/swaption.yaml`, `contrib/rateslib/swaption.py`, the volatility pricer parts, suite S10, swaption tests, the facade's `IRSwaption` constructor (it raises `NotSupportedError` pointing to the later options workflow) and the swaption recipes in `docs/guides/backtesting.md` are removed from `src`, `tests`, `configs` and the shipped docs. One archive copy MAY be kept OUTSIDE `src`, `tests` and `configs` (for example `archive/swaptions/`), excluded from the guards and from the suite, so the later workflow can restore it; the tree is not under version control, so this is the only recovery path. Consequence: gs_quant's own quick-start template uses `IRSwaption`, so the F-6 north star holds for swap and bond recipes only until that workflow.
4. **`dv01` means dollar delta** (S7): currency per +1bp for the instrument as built with its notional, per unit quantity. This is what schemas, the ladder, triggers and hedges consume.
5. **Dollar-delta and ladder contracts drive the showcase** (S4, S7, 6.9).

## 1. Objective

### 1.1 The goal

pricebt is a config-driven, event-driven backtester in the spirit of `gs_quant.backtests`. Its purpose is to answer one question: **given the same strategy and the same market data, do two independent pricing libraries produce the same backtest?** The first two libraries are **rateslib** and **QuantLib**. A backtester that already contains rateslib idioms cannot answer that question honestly: a difference between the two runs could be the backtester's own convention, not the libraries'.

The user's design intent, in their words: pricebt should be fed by "a yaml/json config that is very long/rich passing in all the kwargs pricebt expects", and "should have ZERO dependence on any external pricing library like ARBS". rateslib was to be "only somewhat an inspiration".

### 1.2 The one rule

> **pricebt has ZERO dependence, at every level defined in section 2, on any pricing library, market-data infrastructure library or vendor SDK. It knows only its own contracts. Every external function is reached through a name the user writes in config.**

This rule is not a nice-to-have. Every other requirement in this document exists to make it true or to exploit it (tie-out). If a design choice conflicts with it, the rule wins.

### 1.3 Definitions

- **External library**: any package that prices instruments, builds curves, provides calendars/conventions/fixings or supplies market data. Named examples: rateslib, QuantLib, ARBS (the maintainer's own infrastructure), gs_quant (as a *runtime dependency*, not as design heritage), any vendor SDK. The list is illustrative, not exhaustive.
- **Core**: everything under `src/pricebt` except `contrib/`. Includes `config`, `engine`, `strategy`, `results`, `market`, `testing`, the schema YAMLs, and the gs_quant-compatible facade.
- **Adapter**: code that lets one external library satisfy pricebt's contracts. Lives under `src/pricebt/contrib/<lib>/` or in a separate package. The only place an external library may be imported.
- **Schema**: a data file (YAML) declaring, per asset class, the names pricebt expects (terms, methods, measures, layers), with types, units, signs and definitions. Contains no library names.
- **Binding**: config that maps a schema name to a concrete callable and its arguments.
- **Instrument spec**: a reusable definition of *what kind of instrument* (asset class, convention set, bindings), independent of any one trade.
- **Terms**: the per-trade parameters (direction, dates, notional, ...) supplied at the entry point.
- **Snapshot**: an immutable, plain-data description of market state at one timestamp (section 6.4).

## 2. Zero dependence: the requirements, level by level

There are four levels. "No `import rateslib`" alone is level Z1 and is not sufficient: it can be satisfied while the core still hardcodes rateslib's argument names, units and conventions. Each level MUST have a test (section 9.1), and each test MUST prove it can fail (non-vacuity, section 9.2).

### Z1 - Import level
Core modules MUST NOT import, directly or transitively, at import time or lazily inside a function, any external library. Concretely: no `rateslib`, `QuantLib`, ARBS package (`MDP`, `Query`, `Caching`, `TB`, `BT`, `RVUtils`, `SDRUtils`, `Simulation`, `definitions`), `gs_quant`, or any other pricing/data SDK.
- Enforcement MUST use an import blocker (a `sys.meta_path` finder that raises on banned names), not only a `sys.modules` inspection after the fact. A `sys.modules` check misses imports hidden in `try/except` or inside functions.
- The complete core test suite MUST pass with the blocker installed and with the libraries genuinely absent (section 9.3, the "core-only" run).
- Third-party dependencies of core are limited to numpy, pandas, pyyaml and tqdm. Optional and lazy: pyarrow, matplotlib, jsonschema. No new core dependency without approval.

### Z2 - Name level
No external library's vocabulary appears in core **executable content**: identifiers, non-docstring string literals, YAML keys and non-`doc` YAML values, registered names, default arguments, and error messages that direct the user to a library-named thing.
- Banned tokens (illustrative; the maintained list lives in the guard test and Appendix A): `rateslib`, `quantlib`, `arbs`, `rl_`, `ql_`, `usd_irs`, `us_gb_tsy`, `sum_instruments`, `analytic_delta`, `delta_usd`, `nyc` as a calendar name, and any specific library argument name hardcoded as a required core concept (`curves`, `solver`, `fx`, `vol` as *kwargs pricebt itself passes*).
- Docstrings and comments MAY name a library as **provenance** ("this scheme is compatible with X"), but SHOULD prefer neutral wording. ARBS SHOULD NOT be named in `src/` prose at all (section 10).
- The gs_quant *heritage* (class names such as `Strategy`, `Trigger`, `AddTradeAction`, and the compatibility facade) is design vocabulary, not a dependency, and is allowed. Importing `gs_quant` is not (Z1).

### Z3 - Shape level (no inference)
pricebt MUST NOT decide how to call external code by inspecting names. Specifically it MUST NOT:
- match a pricer's provided objects to a callable's parameters by parameter name;
- locate a method by a hardcoded library method name;
- text-sniff a config to decide which adapter to import (`loader.py` currently does this for `"rl_"`, `"synthetic_rates"`, `"contrib.rateslib"`);
- silently fall back to a same-named attribute when a bound name is missing.

Every call from pricebt into user or library code is: schema name -> binding -> callable -> exactly the arguments the binding declares. Argument *validation* against a signature at load time is allowed; argument *injection* by name is not. See section 6.2.

### Z4 - Semantic level (no conventions)
Core MUST NOT contain any pricing or market convention: day counts, compounding, business-day adjustment for pricing, settlement or payment lags, fixing formats and publication rules, curve interpolation, volatility conventions, currency-specific specs, unit conventions of a library (percent vs decimal), or the sign convention of a library (for example "positive notional = pay fixed").

The only conventions core may own are:
1. **Scheduling**: timezone, a business-day *predicate* (data supplied by the user: weekmask + holidays, or a callable), session open/close, the date policy, and the time grid grammar (gs_quant `RelativeDate` rules). These decide *when the backtester evaluates*, not what an instrument is worth.
2. **The ledger identity**: `equity = initial_capital + cash_account + tcost_account + sum(qty * pv)`.
3. **pricebt's own contract units and signs**, declared as data in the schema (S2) and converted to and from a library's by the binding (B2), never by core code.

Everything else - OIS compounding, day counts, calendars for pricing, repo accrual, fixings, settlement - is returned by the external `value` function as `Valuation(pv, cash, financing)` or is an input to an adapter. This is the user's stated principle: "OIS conventions are NOT needed in pricebt because the external pricing library should do that within the value functions."

### Z5 - Provenance: ARBS is a test case only
ARBS is the maintainer's own pricing and data infrastructure. It MAY appear only as (a) a *test subject* (a foreign stack that plugs in through config, proving genericity), (b) the *origin of fixture data* under `data/fixtures` (documented as provenance), (c) an opt-in live-parity oracle, and (d) the `tools/` exporter. It MUST NOT influence core design vocabulary. Details in section 10.

### Z6 - Optional extras stay optional
Every adapter's library is an optional extra (`pricebt[rateslib]`, `pricebt[quantlib]`). `pip install pricebt` with none of them installed MUST yield a fully functioning backtester that runs the toy stack and the config CLI. rateslib's licence is non-commercial unless a commercial licence is registered; QuantLib is BSD-style. Neither licence obligation may propagate into core.

## 3. Baseline

### 3.1 Snapshot this spec describes

Code snapshot as of 2026-09-26 08:05 local. Files under `src/pricebt` were still being modified by another writer at 07:59 (see section 12, risk R1), and the tree is **not a git repository**, so this spec describes the following, not a commit.

- Core packages: `errors, types, timeutil, pricer, market, pricable, schema, orders, costs, view, registry, registries, legacy, api, __main__, common, risk, session, instrument`, `config/{yamlio,loader,cli}`, `engine/{engine,state,progress}`, `strategy/{strategy,triggers,requirements,actions,infos,signals,durations,sizing}`, `results/*`, `schemas/{generic,swap,bond,swaption}.yaml`, `testing/{toys,scripted,legacy_stub}`.
- gs_quant-compatible facade, added 2026-09-26: `backtests/{__init__,actions,backtest_objects,data_sources,equity_vol_engine,generic_engine,predefined_asset_engine,strategy,triggers}`, `session.py` (`PricebtSession`), `instrument.py` (`IRSwap`, `IRSwaption`), `risk.py`, `common.py`, plus `docs/guides/backtesting.md` and tests `test_gs_compat_{api,guide,rates}.py`.
- Adapters: `contrib/rateslib/{_compat,pricer,swap,bond,swaption,layers,ladder,synthetic}`, `contrib/stores/{common,curves,ust,_pricers}`.
- Tests: 742 passed, 1 skipped (236 s), measured 2026-09-26 against this snapshot while another session was still editing (630 at the v1 report). Record the baseline again immediately before work starts (9.5). The suite configs under `configs/suite/` drive real-data runs (37 swap, 27 bond).

### 3.2 Findings (evidence for each requirement)

| # | Finding | Evidence | Requirement |
|---|---|---|---|
| F1 | `import pricebt` is clean of external libraries, but only import-level is tested | `tests/test_core_pricable.py:218` checks `sys.modules` for a fixed list | Z1, section 9.1 |
| F2 | The config loader text-sniffs for `"rl_"` and imports the rateslib adapter | `config/loader.py:507` | Z3 |
| F3 | Schemas encode rateslib's API: `curves`, `solver`, `fx`, `vol`, `leg`, `analytic_delta`, a `sum_instruments` reducer for rateslib DataFrames | `schemas/swap.yaml`, `schemas/swaption.yaml`, `schema.py:160` | S1-S3 |
| F4 | The swap schema REQUIRES a `delta_ladder` measure, and it is satisfied through a pricebt-side `risk_model: {ladder: rl_par_swap_ladder, tenors}` construct that names a rateslib ladder builder. The *requirement* is a maintainer decision (the maintainer asked for it) and this spec does not overturn it; the *coupling* is the problem: a non-rateslib swap can only conform by imitating that builder | `schemas/swap.yaml` (`required: true`), `contrib/rateslib/ladder.py`, `config/loader.py` `risk_models:` | S4 |
| F5 | The pricer-to-callable mechanism is parameter-name matching against `api_kwargs()` | `pricable.call_method`, `pricer.py:27,41` | Z3, B1-B7 |
| F6 | Layer resolution falls back to a same-named method | `pricable.resolve_layer` | B6 |
| F7 | Every real config names a rateslib factory (`rl_swap`) and creates one pricable spec per direction and tenor (`recv10y`, `pay10y`, `recv5y`, ...) | `configs/suite/_swap_base_eod.yaml` | T1-T4 |
| F8 | `ConstantCashAccrual` hardcodes `(1 + r/365) ** days - 1` | `costs.py` | C1 |
| F9 | `calendar='nyc'` resolves by importing the rateslib adapter | `session.py:41-44` | C2 |
| F10 | `IRSwap`/`IRSwaption` hardcode USD SOFR, rateslib spec `usd_irs`, and rateslib's percent units | `instrument.py` | F1-F3 (facade) |
| F11 | The market-data stores build **rateslib pricers**, so the data layer is library-specific and two libraries cannot consume the same market data | `contrib/stores/_pricers.py` imports `..rateslib`; `curves.py` returns `RLCurvePricer` | D1-D4 |
| F12 | Layer definitions (carry, roll, delta, convexity) are implemented only for rateslib; the glossary is written in rateslib terms (`curve.translate`, `curve.roll`) | `contrib/rateslib/layers.py`, `schemas/swap.yaml` glossary | L1-L5 |
| F13 | ARBS-derived naming (`risk_model`, `ladder`, `carry_bps_running`, "the ARBS risk model pattern") appears in core config keys and adapter docs | `config/loader.py:316`, `contrib/rateslib/ladder.py:1`, `contrib/stores/*` docstrings | Z5, G1-G3 |
| F14 | No QuantLib adapter, no tie-out test, no report comparing two stacks | `grep quantlib` finds only the import-guard test | A3, X1-X6 |
| F15 | Schema text embeds conventions ("ACT/360 on elapsed seconds") | `schemas/bond.yaml:27` | Z4, C5 |

### 3.3 What is already right and MUST be preserved

- The pricable/pricer/MDP split; `Valuation(pv, cash, financing)` returned by the external `value`; the engine-owned ledger and its identity; per-unit valuation.
- The event loop semantics (spine sections 5 and 13; DESIGN section 6), the strategy layer, signals, results, `reconcile()`.
- One `tqdm` bar, look-ahead guard, determinism, `on_error` policy.
- Layers named in config, computed at a cadence, engine-owned `unexplained`.
- The gs_quant-compatible facade *shape* (`GenericEngine.run_backtest`, `Strategy`, `IRSwap(...)`).
- The idea of adapting foreign objects by config, now expressed only through bindings (B2). `RequestMDP`, `PricerPricable` and `LegacyPricer` themselves are removed (11.2).

## 4. Capability map

The request bundles several independently testable capabilities. Each is a module with a stable id. Dependency arrows point one way.

| Module id | Responsibility | Depends on |
|---|---|---|
| `guards` | Zero-dependence enforcement: import blocker, name/shape/semantic scans, non-vacuity twins, the core-only run | - |
| `contracts` | Library-neutral schemas, the binding mechanism, the call path, load-time validation, conformance kit | `guards` |
| `terms` | Instrument spec vs trade terms; terms schema; reference resolution; resolved-terms recording | `contracts` |
| `conventions` | Removal of convention logic from core: cash accrual, calendars, session names, units, compat isolation | `guards` |
| `snapshot` | Plain-data market snapshot; removal of the shipped stores (D6); adapter wrapping of snapshots | `guards` |
| `attribution` | Layer glossary as data, conformance tests, engine-computed baseline decomposition | `contracts` |
| `adapters` | rateslib adapter refit; new QuantLib adapter; dependency-free reference stack; conformance per adapter | `contracts`, `terms`, `snapshot`, `attribution` |
| `tieout` | Harness, report, tolerances, harness self-test | `adapters` |
| `facade` | gs_quant-compatible facade made library-neutral; docs; `DESIGN.md` update | `terms`, `adapters` |
| `showcase` | The end-to-end proof: an executed notebook running a monthly ladder-rebalanced swap book through the whole pipeline (6.9) | `adapters`, `facade`, `attribution` |

Build order: `guards` -> (`contracts` || `conventions` || `snapshot`) -> `terms` -> `attribution` -> `adapters` (rateslib and the reference stack first, QuantLib second) -> `tieout` -> `facade` -> `showcase` -> docs.
Any module MAY be specified in more detail on its own (`SPEC-<id>.md`) before it is built; the requirements below are the contract those specs must satisfy.

## 5. Assumptions (correct me now)

1. pricebt stays a Python package; the `stir` conda env (3.13, pandas 2.3, numpy 2.2, rateslib 2.7.1, QuantLib 1.41 - verified installed) is the reference environment.
2. "Tie out" means numerical agreement within *declared, justified tolerances* on PV, cash, financing, measures and layers - not bit-identity. Two libraries interpolate, compound and schedule differently at the 1e-8 to 1e-5 relative level.
3. Swaps (USD SOFR OIS) and fixed-rate government bonds are the only asset classes in scope (linear products). Swaptions and volatility are out of scope of this refactor (0.3).
4. The existing tests (hundreds; see 3.1) and the suite configs are valuable and should be migrated, not discarded, but MAY be modified with a recorded justification (section 11).
5. Multi-currency is out of scope (single reporting currency, as now).
6. Backwards compatibility of config syntax is desirable but is secondary to Z1-Z6.
7. The maintainer wants the strict, testable enforcement of the zero-dependence rule, not just a convention.

## 6. Requirements by capability

### 6.1 `guards` - enforcing the rule

**G-1.** A test module `tests/guards/` MUST implement Z1-Z4 checks (section 9.1) and be part of the default suite.
**G-2.** Each guard MUST have a non-vacuity twin (section 9.2).
**G-3.** A CI-runnable "core-only" job MUST run the core suite with the blocker and without adapter libraries importable (section 9.3).
**G-4.** The banned-token list MUST be a single data file (`tests/guards/banned.yaml`), reviewed like a schema. Adding a token is easy; removing one requires a spec change.
**G-5.** Tests are partitioned by pytest marker: `core` (toys only, must pass core-only), `adapter_rateslib`, `adapter_quantlib`, `fixtures`, `live_arbs` (opt-in). An unmarked test is an error in the collection hook.

### 6.2 `contracts` - schemas, bindings, the call path

#### Schemas (S)

**S1.** Schema files are data. They MUST contain no library name (Z2), no method name of any library, and no reducer that only makes sense for a specific library's return type. They MAY contain human `doc:` text.
**S2.** A schema entry declares, for each name pricebt expects: `kind` (`term | method | measure | layer | lookup | convention_key`), a neutral signature (named arguments with pricebt types), the return type (`float`, `Valuation`, `Series`, `bool`, ...), the **unit**, the **sign convention**, whether it is `required`, and a `doc`. Standard measure names are library-neutral: `pv, dv01, gamma, vega, theta, rate, notional, duration, ytm, accrued, delta_ladder`. Units and signs are pricebt's contract; a binding converts (B2).
**S3.** A schema MUST NOT say *which* method implements a name. The current `measures: {dv01: {method: dv01}}` and `reducer: sum_instruments` are removed. Reducers are a property of a binding (B2). Library-specific reducers are registered by the adapter that needs them.
**S4.** `required` marks names the engine, triggers or hedges genuinely cannot work without. The bucketed **`delta_ladder` measure REMAINS REQUIRED on the core `swap` schema** (maintainer decision, recorded in 0.2): gs_quant-style delta hedging and ladder-driven triggers need it. Its **expected shape is a plain `dict[str, float]`**: keys are tenor strings in pricebt's tenor grammar (`<int><D|W|M|Y>`, e.g. `"3M"`, `"2Y"`, `"10Y"`), values are currency per +1bp move of that bucket's rate, signed for the holder (a payer has positive entries). The sum of the values equals the parallel `dv01` within a declared tolerance. The measure takes the requested `tenors` list as an ordinary bound kwarg and MUST return exactly those keys (a missing or extra key is a load-time or first-mark error naming the difference); with no `tenors` requested it MUST return keys that all parse as tenors. A library that returns a `Series`, a DataFrame or a ladder object satisfies the contract through a bound **reducer** (B2) that converts to the dict; pricebt's own vector-measure code MUST consume the dict and MUST NOT depend on a pandas type or a `LadderModel` class from an adapter. What MUST NOT live in core is the machinery that produces a ladder for one library: the `risk_model: {ladder: <builder>, tenors}` construct, `rl_par_swap_ladder`, and the loader's `risk_models:` block. Ladder builders ship in the adapter that owns them (with an extension schema, for example `contrib/rateslib/schemas/swap_ladder.yaml`, for adapter-specific detail) and are bound like any other measure.
**S5.** Schemas support `extends:`, are versioned (`schema_version`), and load through one loader that validates them. Unknown keys are errors.
**S6.** Shipped core schemas: `generic`, `swap`, `bond`. The current `swaption` schema is out of scope and stripped (0.3). `bill`, `stir_future`, `bond_future` MAY be added later. Each schema MUST include: `terms`, `methods`, `measures`, `layers` (with glossary, section 6.5), `conventions` vocabulary (T5), and `pricer_capabilities` (optional lookups, never conventions the core computes).
**S7.** (Decided, 0.2.) **`dv01` is a dollar delta**: currency per +1bp for the instrument **as built with its notional**, per unit quantity, signed for the holder. A 120mm 10-year payer swap has a `dv01` of roughly +100,000 (currency per bp); a position of `qty` such units has `qty` times that. It is NOT a per-1-notional or per-100 measure. The same applies to `gamma` (currency per bp squared) and to the values of `delta_ladder`. The external library implements it (a binding may rescale, B2). The engine sums quantity-weighted dollar deltas for portfolio risk, triggers and hedge sizing.

#### Binding (B)

**B1.** Every call pricebt makes into external code MUST be: schema name -> binding -> callable. No `getattr(obj, "npv")`, no hardcoded library method names, anywhere in core.
**B2.** A binding is declared in config, per instrument spec (and MAY be inherited from an adapter-shipped default block). It MUST be able to express:
- the **target**: a method on the built instrument object, a function by dotted path, a method on the pricer, or an attribute;
- the **arguments**: literals and references (B3), passed by keyword;
- **what context to pass**: the mark context, the pricer, the instrument, or nothing (explicit);
- **post-processing**: a reducer name (float, real-part, sum-of-column, vector, series-to-scalar, series-to-tenor-dict) or a dotted-path callable, and unit conversion (`scale`, `offset`) so a library returning decimals can satisfy a percent contract;
- an optional **sign** override where the library's sign differs from the schema's.
**B3.** References are a small closed grammar resolved by pricebt, with no `eval`: `@pricer`, `@pricer.<attr>`, `@ctx.ts`, `@ctx.prev_pricer`, `@terms.<name>`, `@state.<key>`, `@instrument`. Dotted-path imports remain guarded by the allow-list (`registry.allow`).
**B4.** At config load (strict mode, the default): an unbound `required` name is an error `CFG-REQUIRED-BINDING` with did-you-mean suggestions drawn from the target object's public method names (this is how `gamma` gets suggested for `convexity`). A binding for a name the schema does not declare is an error. A bound callable is validated by attempting `inspect.signature(...).bind(**declared_kwargs)`; failure is a load error. This is validation, not injection.
**B5.** Proof by construction: a toy pricable whose methods and parameters are deliberately named unlike the schema (`gamma` bound to `convexity`, arguments named arbitrarily) MUST satisfy the swap schema check and run in an engine backtest. A twin MUST show that the same toy, unbound, is rejected and receives nothing implicitly.
**B6.** (Decided, 0.2.) **Adapters ship default binding blocks; strict mode is the default.** Each adapter (and the toy/testing stack, and the reference stack) packages a default `bind` block per instrument it provides; it is merged automatically when an instrument names that adapter's factory, and a user `bind:` entry overrides it name by name. After merging, every `required` schema name MUST be bound or the load fails (B4). There is **no implicit same-name resolution**: a user-written class that is not backed by an adapter must bind explicitly (a one-line `bind` per name is the intended cost of keeping pricebt library-agnostic). The engine MUST NOT fall back to `getattr(obj, schema_name)`, and MUST NOT use fuzzy or signature-based guessing. A non-strict mode MAY exist for exploration and MUST be opt-in per config, never the default.
**B7.** `Pricer.api_kwargs()` MUST cease to be a core mechanism. A pricer MAY expose objects as attributes; bindings reference them (`kwargs: {curves: "@pricer.curve"}`). An explicit splat (`"**": "@pricer.api"`) MAY be offered; signature filtering, if offered, MUST be an explicit opt-in flag on that binding.
**B8.** (Decided, 0.2.) **No legacy support.** `pricebt.legacy` (`PricerPricable`, `LegacyPricer`), the `RequestMDP` request-dict wrapper and the ARBS-shaped stub that served them are REMOVED, with their tests. Methods that live on the pricer (for example `pricer.npv(instrument)`) are reached by a binding target of kind `pricer_method` (B2). A request-dict data source is adapted by ordinary user code implementing `get_pricer(ts, request)`.

One acceptable design (illustrative, non-binding):

```yaml
instruments:
  usd_sofr_ois:
    asset_class: swap
    factory: my_lib.pricing:build_swap         # (pricer, ts, **terms, **conventions) -> object
    conventions: {...}                          # section 6.3 (opaque to pricebt)
    bind:
      value:     {target: {method: npv},  kwargs: {curves: "@pricer.curve"}, pass: none}
      dv01:      {target: {method: pv01}, unit: {scale: 1.0}}
      convexity: {target: {method: gamma}}
      carry:     {target: {function: my_lib.attrib:carry}, pass: ctx}
      rate:      {target: {method: par}, unit: {scale: 100.0}}    # library decimals -> schema percent
```

### 6.3 `terms` - instrument specs versus trade terms

The current config creates one pricable spec per direction and tenor (`recv10y`, `pay10y`, `recv5y`, ...). Direction, dates and notional are properties of a **trade**; the definition of "a USD SOFR OIS swap" is a property of the **instrument**.

**T1.** The config MUST distinguish an **instrument spec** (defined once: asset class, factory, conventions, bindings, pricer role) from **trade terms** (supplied by the action at the entry point). A strategy trading pay and receive at several tenors MUST need exactly one instrument spec.
**T2.** Each asset-class schema MUST declare canonical terms with types, required-ness and aliases:
- swap: `side` (`pay | receive`, aliased `buy | sell`), `effective`, `maturity` (a date or a relative tenor such as `spot`, `10Y`), `notional`, `fixed_rate` (a number, the token `par`, or absent), `extras` (opaque mapping);
- bond: `side` (`buy | sell`, aliased `long | short`), `security`, `notional`;
**Units of numeric terms are pricebt's, and are stated once: rates, yields and strikes in terms (`fixed_rate` and similar) are PERCENT** (spine 13.8 stands: rates, yields and barrier levels are percent at every boundary; shocks and spreads are bp). An adapter MUST convert to its library's unit through its binding or factory and MUST NOT assume another unit; a units test (a 4.0 term must price as 4%, not 400% or 0.04%) is part of A5.
The schema fixes the **direction sign** once per asset class: `+1` = long the asset's primary exposure (a payer swap and a bought bond are `+1`; `dv01` sign follows). Aliases map onto `+/-1`. pricebt normalises `side` to a direction and hands the binding both the canonical term and the direction; how the library encodes it (for example a signed notional, a `pay`/`receive` flag) is the binding's or adapter's job.
**T3.** Terms are supplied by the trading action (`add_trade: {instrument: ..., terms: {...}}`) and by Python (`add_trade(instrument, terms={...})`; the facade's `IRSwap('Pay', '10y', 'USD')` is sugar for terms on a registered spec, F-2). Term values MAY be literals or references (`@signal.<name>`, `@param.<name>`, `@trigger.scaling`), so side and tenor can be signal-driven or scanned without editing pricables. Missing required terms and unknown terms are load or trade-time errors that list the schema.
**T4.** (Decided, 0.2: date handling belongs to the external library.) Relative dates (`spot`, `10Y`, `3M`), explicit dates and the token `par` MUST pass through untouched to the factory. pricebt MUST NOT compute instrument dates, schedule dates or par rates (Z4); spot lag, calendars, business-day adjustment and schedule generation are the library's. The factory MUST return the **resolved terms** (concrete dates, resolved strike/rate); the engine MUST store them on the position and write them to the trade ledger. This is mandatory for tie-out: differing resolved dates is the most likely cause of a PV difference, so the harness compares them first (X2, level L0). The adapter's factory resolves `par` and returns the resolved rate (decided, 0.2).
**T5.** An instrument spec carries an opaque `conventions` block passed verbatim to the factory and hashed into the run manifest. The schema MAY define a *vocabulary* of convention keys (data only, for example `day_count`, `calendar`, `payment_lag_days`, `business_day_convention`, `fixing_lag_days`, `compounding`, `settlement_lag_days`), so two adapters can be given the *same* convention block. An adapter MUST error on a convention key it does not understand (never ignore it) and SHOULD publish the keys it supports. pricebt applies none of them.
**T6.** Valuation stays **per unit**; `notional` is a term; `qty` scales. (Unchanged.)
**T7.** Existing pre-baked pricables (`recv10y`) MAY remain as sugar: a named instrument-plus-fixed-terms entry. New and migrated configs SHOULD use one instrument spec plus per-action terms.

One acceptable design:

```yaml
actions:
  - {type: add_trade, instrument: usd_sofr_ois,
     terms: {side: receive, effective: spot, maturity: 10Y, notional: 1e7, fixed_rate: par}}
  - {type: add_trade, instrument: usd_sofr_ois,
     terms: {side: "@signal.direction", maturity: "@param.tenor", notional: 1e7}}
```

### 6.4 `snapshot` and market data

**D1.** pricebt MUST define neutral, immutable, pickle-safe, plain-data market types in core. At minimum: `CurveSnapshot` (`name`, `reference_date`, `node_dates`, `values`, `value_kind` in `discount_factor | zero_rate | ...`, `interpolation` tag, provenance), `FixingsSeries` (`name`, dates, values, unit, publication metadata), `QuoteSet` (bond quotes: id, clean price or yield, unit). There is no volatility snapshot: options are out of scope (0.3). The vocabulary of `interpolation` tags and `value_kind` is data, not logic; an adapter maps a tag to its native interpolation or **errors** when it cannot honour it.
**D2.** Market-data providers (supplied by the user or by test support) MUST produce and depend on snapshots only. They MUST NOT import an external pricing library, and MUST NOT construct a library curve. Today `contrib/stores/_pricers.py` imports `..rateslib` and returns `RLCurvePricer` (F11); the stores are removed (D6) and the rateslib pricer is built only by the adapter's `wrap`.
**D3.** Library objects are built by **adapter-provided wrappers**: `adapter.wrap(snapshot, ...) -> Pricer`, lazily, memoised per snapshot. Config selects the wrapper on the pricer role. One acceptable design: `market.pricers.primary: {mdp: sofr, wrap: "pkg:wrap_fn"}`. Swapping the library for a tie-out run MUST require changing this line and the instrument's `factory` and `bind` block, and nothing else (the shared `conventions` block stays as is, X1).
**D4.** Providers keep the time-mapping and look-ahead semantics as today (`asof | exact | date`, `max_staleness`, `eod_visible_at`, `LookAheadError`). Those are generic and stay in core.
**D5.** Every snapshot MUST be hashable to a stable digest, recorded per mark in an optional audit trail so the tie-out harness can assert that both stacks consumed **identical inputs** (X2, L0).
**D6.** (Decided, 0.2.) **All external data infrastructure is removed from the package.** `contrib/stores` (`curve_store`, `fedinvest`, `webull_minute`, and the parquet layouts, vendor formats and reference-data rules embedded in them) MUST be deleted from `src/pricebt`. pricebt reads no vendor format and no parquet store; market data reaches it only through a user-written `MarketDataProvider.get_pricer(ts, request) -> Pricer` returning snapshot-backed pricers (D1). Real-data runs (the swap and bond suites, the tie-out) use a **test-support provider** under `tests/support/` that reads `data/fixtures` into snapshots; it is test code, is never shipped, and configs reference it by dotted path under `registry.allow`. Reference-data rules the deleted readers embedded (on-the-run ranking, bond calendars) become data on the snapshot or bound functions supplied by the test-support provider or an adapter, never pricebt logic. The exporter `tools/export_fixtures.py` stays outside the package (G1).
**D8.** The synthetic market (`SyntheticMDP`: Nelson-Siegel factor random walks, seeded fixings, a bond quote panel) MUST move from `contrib/rateslib` to `pricebt.testing` as a library-free provider emitting snapshots, so both adapters and the reference stack run on identical synthetic data.
**D7.** The pricer protocol in core MUST be minimal: `ts`, `describe()`, `lookup(name, **kw)`, and optionally `reference_date`. Lookups whose meaning is a convention (`calendar_advance`, `spot_date`, `par_rate`) are declared in schemas as **optional capabilities** an adapter pricer may offer; core code paths MUST NOT require them.

### 6.5 `attribution` - P&L decomposition

P&L decomposition is a first-class requirement. The mechanics that exist today stay: layers named in config, computed at a cadence, per-unit interval P&L, engine-owned `unexplained`, flush on close/resize, strict mode.

**L1.** Layers are requested by **schema name** and bound like any method (B1-B2). The engine computes `unexplained = interval P&L - sum(layers)` and remains the only author of that name. Reserved engine names stay: `total, transactions, cash_interest, unexplained, financing`.
**L2.** The schema MUST define every layer **precisely as data**: what it includes and excludes, sign, unit, per-unit or per-position, which market state it needs (`prev_pricer`, fixings, ...), and the asset-class glossary. Different definitions MUST have different names (as spine 12.3). Required minimum: swap `carry, roll, delta, convexity`; bond `carry, roll, delta, convexity` plus the `financing` accounting row. Library-native refinements (for example a fixings sub-layer) are **extension layers**, declared in an adapter extension schema, namespaced so they never collide with a core name.
**L3.** The core MAY offer an **engine-computed baseline decomposition** that needs no pricing library and no `prev_pricer`: a Taylor attribution from two bound measures the library already supplies, `dv01` (the dollar delta, S7) and `rate` (the instrument's own market rate: a par rate for a swap, a yield for a bond, whatever the library's `rate` returns). `tay_delta` = the start-of-interval `dv01` times the change in `rate` in bp; `tay_convexity` = half the bound `gamma` times that change squared. pricebt chooses no driver variable and computes no rate itself. It is optional, computed at cadence, named distinctly from library layers (both may be produced), and documented as a parallel-shift approximation that is coarser than a multi-node decomposition. Its purpose: both stacks get an **identical, definition-controlled** decomposition, so any difference is in the measures, not the layer maths.
**L4.** Every adapter MUST pass a **layer conformance kit** (`pricebt.testing.layer_conformance`) using independent identities, because a small `unexplained` only proves the layers are complete, not that they are right:
1. finite-difference full revaluation versus delta + convexity for small shocks;
2. mirror symmetry (a payer and the matching receiver have equal and opposite `pv`, `dv01`, `gamma` and every layer, to the documented tolerance);
3. cash-sweep continuity across a payment date (the pv drop equals the cash booked);
4. `sum(layers) + unexplained == interval P&L` (necessary, not sufficient);
5. `unexplained` share within a per-asset threshold on real steps, reported rather than only asserted.
**L5.** The run manifest MUST record layer definition ids and versions (`swap.carry@1`) so results remain interpretable after a definition changes.
**L6.** Cadence, off-by-default, lump behaviour of intraday carry/roll, and the ~15 ms per position-point cost are unchanged; they MUST remain documented limitations.
**L7.** Option-related attribution gaps (for example the swaption annuity term) are out of scope with swaptions themselves (0.3).

### 6.6 `conventions` - moving convention logic out of core

**C1.** Cash accrual MUST be an interface `interest(balance, t0, t1) -> float` implemented by a caller-supplied callable or series. Shipped implementations: `callable`, `series` (rate per date from user data), and a `constant` helper whose day count and compounding are explicit parameters with **no default** (or a default clearly labelled gs_quant-compat and isolated in the facade). There MUST NOT be an OIS-fixings model in core. A rate MAY come from a pricer lookup or bound function, so the library owns the convention.
**C2.** Calendars: core `Calendar` is data (weekmask + holidays, or a predicate callable) used for scheduling only. Named calendars MUST NOT resolve inside core by importing an adapter. Names resolve through a registry populated by adapters or config (`registry.calendars`), or a JSON file. Default: weekends only. `nyc` handling in `session.py` MUST be removed from core. (Decided, 0.2: trade durations and exit dates MAY be delegated to an optional bound `advance_date(date, tenor)` function of the external library, with the backtest calendar as the fallback; the time grid and trigger schedules remain scheduling on the backtest calendar.)
**C3.** Fixings policy, settlement, day count, interpolation: adapters and pricers only. `time_accrual` (intraday carry, lump or linear) is an adapter-level convention (decided, 0.2).
**C4.** Units: schemas declare units; bindings convert. Core MUST NOT contain "percent vs decimal" code for a library's sake (`instrument.py` currently converts decimals to rateslib percent).
**C5.** Repo/financing and carry are returned in `Valuation.financing` and layers by the external value function. Schema and doc text MUST NOT state a library's day count (F15).
**C6.** The compat module MAY keep gs_quant behaviours (for example the `1 + r/365` accrual) so ported gs_quant strategies reproduce, provided they are isolated in `pricebt.backtests` and labelled.
**C7.** Multi-currency remains out of scope; `result_ccy` other than the reporting currency stays `NotSupportedError`.

### 6.7 `adapters`

**A1.** An adapter is a package registering, lazily and idempotently: `wrap(snapshot) -> Pricer`; instrument factories per asset class; a **default binding block** per instrument (a YAML shipped with the adapter, overridable); reducers; calendars; an extension schema if any. It owns *all* library global state (like `_compat.py` today: licence filters, evaluation-date globals, fixings stores) and MUST guard it (reset when time moves backwards, no leakage between runs).
**A2.** **rateslib adapter (refit).** `rl_swap` and `rl_bond` become instrument factories taking the neutral terms (T2) and conventions (T5). The `usd_irs` default belongs to the adapter's default convention block for `usd_sofr_ois`, not to core. The ladder and `risk_models` machinery moves behind the extension schema (S4). The current golden numbers (seasoned 3y payer 50mm: carry -561.82, roll 12,159.98, fixings -10,627.91, resample 111.52, delta 70,190.64, convexity -50.23, residual 0.02, total 71,222.20) MUST still be reproduced. The existing `rl_swaption` adapter is stripped (0.3).
**A3.** **QuantLib adapter (new).** Required: fixed-rate government bond and SOFR OIS swap (linear products only, no options). It consumes the same snapshot (D1), the same terms (T2) and the same convention block (T5), and supplies `value` (`pv`, `cash`, `financing`), measures (`dv01`, `gamma`, `rate`, ...) and layers to the same glossary (L2), using bump-and-revalue where QuantLib has no automatic differentiation. It MUST guard QuantLib global state (`Settings.instance().evaluationDate`) and MUST NOT leak between runs or tests. One acceptable design: build a discount curve from the snapshot node dates and DFs with the interpolation tag mapped to `ql.LogLinearInterpolation`, and price via QuantLib's overnight-indexed-swap machinery; but any correct construction that honours the convention block is acceptable.
**A4.** **Reference stack (dependency-free), MUST.** A minimal closed-form pricer in numpy shipped in `pricebt.testing`, covering at least a zero-coupon bond and a single-period deposit or FRA, i.e. instruments whose value can be derived by hand from the snapshot. It is the **known-answer** for the harness self-test (X5c) and a third leg with no external library at all, so it cannot be wrong for the same reason both libraries might be. It implements the same schema, terms and binding contracts as any adapter.
**A5.** Each adapter MUST pass: the schema conformance check on bound instruments, the layer conformance kit (L4), a snapshot-ingestion test (a snapshot round-trips to the library curve and back within tolerance), and a mismatched-names binding test.
**A6.** Adapters MUST NOT call each other. rateslib code MUST NOT import the QuantLib adapter or vice versa.

### 6.8 `tieout` - the harness

**X1.** A harness (`pricebt tieout base.yaml --stacks a.yaml b.yaml`, and a Python API) runs **one** base config under N stacks. A stack overlay MAY change only three things: an instrument's `factory`, its `bind` block, and `market.pricers.*.wrap`. The **`conventions` block, the terms, and everything else (strategy, triggers, grid, signals, costs) are shared and MUST be identical across stacks**; the harness MUST reject an overlay that touches them. This is deliberate: if each stack could carry its own conventions, a PV difference could be a day-count choice rather than a library difference, which is the ambiguity tie-out exists to remove. If one library cannot honour a shared convention key it MUST error (T5), and that error is itself a tie-out finding. Enforcement: hash the base config with the three overlay-owned keys removed and require equal hashes across stacks.
**X2.** Comparison levels, each reported separately:
- **L0 inputs**: snapshot digests equal (D5); resolved terms equal (T4), dates first;
- **L1 marks**: `pv`, `cash`, `financing` per position per timestamp;
- **L2 measures**: `dv01`, `gamma`, `rate`, ...;
- **L3 layers**: by schema name; plus invariants that hold regardless of definition (`sum(layers)+unexplained == interval P&L`), plus the baseline decomposition (L3 above);
- **L4 portfolio**: equity, cash, trade ledger (entries, exits, counts, timestamps), summary statistics.
**X3.** Tolerances are declared per level and per asset class in config (`tieout.tolerances`), with defaults in Appendix B. The defaults are *placeholders*: the first successful run establishes the empirical noise floor per level, the spec is updated with those numbers and the reasons any level is looser than expected. A tolerance MUST NOT be widened to turn a run green without a written explanation in the report (section 11). (Decided, 0.2: the Appendix B placeholders are accepted; the first successful run sets the real values.)
**X4.** Output: a machine-readable result (parquet) and a human report (markdown/HTML) giving, per level, the maximum absolute and relative difference, **where** it occurs (timestamp, position, name), a pass/fail against the tolerance, and an attribution of the difference: input difference (L0), model/convention difference, numerical noise. The largest offenders MUST be listed.
**X5.** Harness self-test (mandatory, before any real result is trusted): (a) the same stack run twice produces exactly zero difference at every level; (b) a deliberately mutated convention (for example flipped day count or a shifted calendar) on one side is detected at L1 and reported at the right place; (c) the reference stack (A4) agrees with a hand-derived answer to machine precision. This is the user's standing rule: run a checking tool on an input whose answer is known before using it.
**X6.** Known definition differences between stacks (for example carry as forwards-realised versus accrual-net) are expected at L3. The report MUST separate expected definition differences (documented in the glossary) from unexpected ones, and MUST NOT hide either.

### 6.9 `showcase` - the end-to-end proof (a notebook)

The refactor is not finished until one executed notebook demonstrates the whole pipeline on real fixture data, from passing in the external library to the tearsheet.

**W1.** The deliverable is `notebooks/showcase_swap_book.ipynb` (source under `notebooks/src/`, built and executed with saved outputs; runnable top to bottom from a clean kernel on `data/fixtures`, with no live ARBS and no network). It MUST show, in this order:
1. **Passing in the external library.** The instrument spec `usd_sofr_ois` with its `conventions` block, the adapter's shipped default binding block (printed), a user override of one binding, and the pricer role's `wrap` line. The notebook imports only `pricebt` and `pricebt.contrib.<adapter>`; the only library-specific step is naming the adapter.
2. **Market data as snapshots.** The test-support provider (D6) supplying snapshots, the time mapping (`asof`, `max_staleness`) and one printed snapshot digest (D5).
3. **The engine.** Daily EOD grid from the provider's data, initial capital, fill lag, layer cadence, the recorded measures, one progress bar.
4. **Signals.** At least a measure signal on the book's dollar delta and one per-bucket ladder signal, plotted against the target.
5. **Trigger and actions.** A monthly `PeriodicTrigger` with a ladder-hedge action, defined once with `terms` per action (T3).
6. **Results.** The tearsheet rendered inline and written to disk: equity and drawdown, P&L attribution by layer including `unexplained` (L1), book dollar delta against the target over time, the ladder at selected dates, the trade ledger with resolved terms (T4), and summary statistics; `reconcile()` output shown passing.
**W2.** The strategy is a simple monthly rebalance of a linear swap book. **Target: total book dollar delta of 1,000,000 per bp (S7), expressed as a 10y-equivalent target ladder**, by default `{"10Y": 1_000_000}` with every other bucket 0, configurable. The hedge set is USD SOFR OIS swaps at 2Y, 5Y, 10Y and 30Y (configurable), receive-fixed by default (`side` is a per-action term). The book starts empty; on each monthly trigger the ladder hedge action measures the book's `delta_ladder` (a `dict[str, float]`, S4), computes the deviation from the target ladder, sizes the hedge-set trades by weighted least squares, and trades to close it. Positions age between rebalances, so the deviation is real. One instrument spec plus per-action terms; no per-tenor or per-direction pricables. The phrase "1mm 10y equivalent" was interpreted as above after review; the notebook MUST state the interpretation in its first cell.
**W3.** The notebook MUST contain assertion cells that pass: `reconcile()` is clean; the book's dollar delta after each rebalance is within a stated tolerance of the target ladder (default 1% of total dv01 at the target buckets, tightened or loosened only with a change-log entry); exactly one enabled progress bar; the `unexplained` share of P&L is reported and within the per-asset threshold of L4. If the QuantLib adapter and tie-out harness are complete, a short section reruns the same base config under QuantLib and shows the tie-out summary (X2, X4).
**W4.** The notebook MUST NOT import rateslib, QuantLib or ARBS directly and MUST NOT mention ARBS. If the ladder least-squares proves ill-conditioned on real data (for example the 2Y/5Y/10Y/30Y hedge set with neighbouring buckets), the implementer MAY change the solver regularisation or the hedge set, recording the change and the evidence.

## 7. Requirement traceability (module -> findings)

| Module | Closes findings |
|---|---|
| `guards` | F1, F2 (detect), F13 (detect) |
| `contracts` | F2, F3, F4, F5, F6, F15 |
| `terms` | F7, F10 |
| `conventions` | F8, F9, F15 |
| `snapshot` | F11 |
| `attribution` | F12 |
| `adapters` | F3, F11, F12, F14 |
| `tieout` | F14 |
| `facade` | F10 |
| `showcase` | end-to-end proof that F1-F14 are closed on real fixture data |

## 8. The gs_quant-compatible facade (`facade`)

**F-1.** The facade (`pricebt.backtests`, `pricebt.risk`, `pricebt.common`, `pricebt.session`, `pricebt.instrument`) is a gs_quant *interface* and stays in core. It MUST NOT import an external library (Z1) and MUST NOT hardcode conventions or units (Z4).
**F-2.** `pricebt.instrument.IRSwap('Pay', '10y', 'USD')` MUST become a constructor that produces **terms** and looks up the instrument spec registered on the session for (asset class, currency), for example `PricebtSession.use(market=..., instruments={"swap:USD": "usd_sofr_ois"})`. No `usd_irs` and no rateslib import in the facade. Two different unit boundaries exist and MUST NOT be confused: (1) **gs_quant to pricebt terms**: gs_quant's numeric `fixed_rate` and `strike` are decimals (0.04), pricebt terms are percent (4.0); this conversion is gs-compat behaviour, is allowed under C6, and lives in the facade only; (2) **pricebt schema to library**: the binding converts schema units into whatever the library expects (C4). The facade never converts to a library's units.
**F-3.** The existing tests `test_gs_compat_{api,guide,rates}.py` MUST keep passing, updated only to construct the session with a registered spec; each change to those tests is listed in the change log.
**F-4.** `docs/guides/backtesting.md` MUST be updated: the "instruments" row and the session example no longer claim rateslib or `calendar='nyc'`.
**F-5.** Unsupported gs_quant items stay `NotSupportedError` with a pointer (as today). The `EquityVolEngine` and `PredefinedAssetEngine` stubs stay as they are (decided, 0.2).
**F-6.** The maintainer's **north star for the facade is preserved**: every recipe in `gs_quant/skills/gs-quant-overview/backtesting.md` runs after changing imports from `gs_quant` to `pricebt` and supplying a market once in place of `GsSession.use()`. The instrument registration of F-2 MUST NOT add a mandatory step beyond that one session call: a session (or the adapter's own registration on import) supplies default instrument specs for the shipped currencies, and `IRSwap('Pay', '10y', 'USD')` works as it does today. `tests/test_gs_compat_guide.py` is the acceptance test for this and MUST stay green. Exception: swaption recipes are not supported until the later options workflow (0.3, Q15).

## 9. Testing strategy

### 9.1 Guard tests (default suite)

| Id | Checks | Method |
|---|---|---|
| GT-Z1a | Importing every core module in a fresh interpreter with the blocker installed succeeds | subprocess + `sys.meta_path` finder raising on banned names |
| GT-Z1b | The full `core`-marked suite passes with the blocker and the libraries absent | pytest run in a subprocess (section 9.3) |
| GT-Z2a | No banned token in core identifiers or non-docstring string literals | AST walk over `src/pricebt` minus `contrib` |
| GT-Z2b | No banned token in schema YAML keys and non-`doc` values | YAML walk |
| GT-Z2c | No banned token in shipped core configs and example configs (except explicit adapter/example configs under `configs/adapters/`, `configs/examples/`) | YAML walk |
| GT-Z3a | An unbound required name is rejected; an unrelated method with the right *name* but no binding is not called | toy with mismatched names |
| GT-Z3b | A binding runs with arguments named arbitrarily (`zzz`) and receives exactly the declared kwargs | toy that records what it received |
| GT-Z3c | The loader never imports an adapter due to text in the config | config containing the string `rl_swap` in a comment/description loads without importing any adapter |
| GT-Z4 | No convention token in core (`act/360`, `act/365`, `/365`, `/360`, `day_count` logic, `compound`, `settle`, `modifier`, `payment_lag`) outside the schema *vocabulary* and the labelled compat module | AST/string scan with an allow-list of file paths |
| GT-Z5a | No `src/` module imports an ARBS package; no module or path contains `arbs` | extends `tests/test_no_arbs_dependency.py` |
| GT-Z5b | No executable ARBS token in `src/` | scan |

### 9.2 Non-vacuity

Per the maintainer's standing rule, every guard MUST prove it can fail. Each `GT-*` test has a twin that copies the tree (or builds a temporary package) with **one planted violation** (an `import rateslib` inside a function; `curves` as a required schema argument; a `/360` in the engine; a `.yaml` value `rl_swap` in a core config) and asserts the guard reports exactly that violation. The existing `test_check_is_not_vacuous_it_sees_the_bridge_outside_the_package` is the pattern. The whole suite MUST also be mutation-checked as in DESIGN section 13 (each new test: break the covered code, confirm failure).

### 9.3 Core-only run

A job (and a documented command) runs `pytest -m core` in an interpreter where the blocker refuses `rateslib`, `QuantLib`, ARBS names and `gs_quant`. The core suite is toys only (as spine 12.11). It MUST pass. It MUST also be run once in a fresh virtual environment that never installed the adapter libraries (release check).

### 9.4 Adapter and tie-out tests

- rateslib and QuantLib adapters each pass A5. Both run the same conformance kit on the same snapshots.
- The harness self-test (X5) runs in the default suite where both libraries are present and skips with a clear reason otherwise.
- A recorded tie-out run over the real-data fixtures (a swap strategy and a bond strategy) produces the report in section 11.

### 9.5 Regression

- The pre-existing tests are the regression baseline. Before the refactor starts, **run the suite and record the baseline pass/fail/skip counts** in the change log. Tests that must change (because they asserted the old coupling) are listed with their reason; none is deleted or weakened silently.
- Real-data suite outputs (`results/`) for the existing swap and bond strategies MUST be reproduced by the migrated configs to within a stated tolerance, or the difference explained.

## 10. ARBS as a test case only

**G1.** ARBS MAY appear only in: `tools/` (the read-only fixture exporter and the opt-in live bridge), `tests/` (opt-in live parity, the test-support provider that reads fixtures, the foreign-stack stub), `configs/examples/`, `docs/`, and the provenance note in `data/fixtures/README.txt`. It MUST NOT appear in `src/pricebt` executable content, and SHOULD NOT appear in `src/` prose (F13).
**G2.** ARBS's role is threefold and nothing more: (a) **a foreign stack** used to prove that arbitrary external infrastructure plugs in through config alone (a request-dict data source wrapped by test-side code, and the pricer-owned-method pattern reached through `pricer_method` bindings, run against an ARBS-shaped stub in the default suite and against real ARBS only when `PRICEBT_LIVE_ARBS=1`); (b) **a source of real market data** exported once into `data/fixtures` (curves, fixings, UST tables, calendars) and consumed through the test-support provider as neutral snapshots (D1, D6); (c) **an opt-in parity oracle** (same trade valued by ARBS's own classes and by an adapter).
**G3.** ARBS-derived *ideas* that are useful in general MUST be re-expressed as neutral capabilities under neutral names: "pricer-owned methods" becomes the binding target kind `pricer_method`; "delta risk ladder" becomes the required `delta_ladder` measure of shape `dict[str, float]` (S4); "request-dict MDP" is not kept: it is user code (B8). Names such as `risk_model`, `ladder`, `carry_bps_running` MUST NOT be core config keys.
**G4.** ARBS (`C:\Users\chris\clee\ARBS`) and gs-quant are READ-ONLY. The live bridge MUST NOT launch Excel or write caches (guarded, as in `docs/research/mdp-feasibility.md`).
**G5.** Fixtures derive from third-party vendor data and MUST NOT be redistributed; `data/` stays git-ignored; the README records provenance.
**G6.** Removing the ARBS checkout and `data/fixtures` MUST NOT fail the default suite: dependent tests skip with an explicit reason. `test_arbs_bridge.py` already does so for live tests; confirm the fixture-dependent suites (`test_suite_swaps`, `test_suite_bonds`, and the tests of the test-support provider) do the same.

## 11. Migration, change log and success criteria

### 11.1 What breaks

Configs using `methods:`, `measures: {x: {method: y}}`, `risk_models:`, `risk_model:`, `factory: rl_*`, per-direction pricables, `calendar: nyc`; anything calling `Pricer.api_kwargs()` or relying on same-name resolution; adapter authors; the stores (removed), the legacy bridge and request-dict wrapper (removed), swaption code, schema, facade constructor and suite S10 (stripped); `pricebt.instrument` users; the schema YAMLs themselves; a large share of the existing tests, including the compat tests, to the extent they encode the old coupling.

### 11.2 Compatibility policy

**No compatibility layer.** (Decided, 0.2: this is a large rewrite and no legacy support is wanted.) There is no deprecation translator, no `pricables:` alias, no translation of `methods:`/`measures:` keys, no legacy factory names and no same-name fallback. Old configs, notebooks and tests that use removed features are rewritten or deleted, each recorded in the change log (11.3). `rl_*` factory names exist only as whatever names the rateslib adapter registers.

### 11.3 Change log discipline

`docs/design/11-refactor-changelog.md` records: the pre-refactor baseline test counts; every test changed, with the reason and the requirement id; every tolerance set and every tolerance changed, with the reason; every deviation from a SHOULD. A MUST is never edited in a change log; it is edited here, first.

### 11.4 Because the tree is not under version control

Before any code changes, the implementer MUST snapshot the tree (initialise a repository or copy the directory), and MUST check whether another writer is active on `src/pricebt`. Per the maintainer's working practice, new work goes in a fresh sibling worktree or copy; if the project is put under git, that is a `git -C <repo> worktree add ../pricebt-refactor -b refactor/neutral-core`.

### 11.5 Success criteria (all must hold)

1. **SC1** The guard tests GT-Z1..Z5 pass, and each non-vacuity twin fails as designed.
2. **SC2** `pytest -m core` passes with the import blocker active and rateslib, QuantLib, ARBS and gs_quant unavailable (Z1, Z6).
3. **SC3** No banned token in core executable content, in the shipped core schemas, or in shipped core configs (Z2).
4. **SC4** The mismatched-names toy (`gamma` bound to `convexity`, arguments named arbitrarily) satisfies the swap schema and runs in an engine backtest; its unbound twin is rejected (B5, Z3).
5. **SC5** A non-rateslib swap conforms to the `swap` schema by binding alone: it supplies `delta_ladder` as a bound callable returning a plain `dict[str, float]` keyed by tenor strings (via a reducer if its library returns another type), with no `risk_model` construct, no solver, no curves object and no rateslib-shaped return type; a ladder-driven hedge runs on it (S4).
6. **SC6** One instrument spec `usd_sofr_ois` plus per-action terms replaces all per-direction pricables in the swap suite; the migrated suite reproduces the recorded results within the stated tolerance (T1, 9.5).
7. **SC7** The package ships no market-data provider, store or vendor reader (D6); every provider used in tests emits snapshots and imports no pricing library; one snapshot is consumed by both the rateslib and QuantLib wrappers (D2, D3).
8. **SC8** The same base config, with an identical `conventions` block, runs under rateslib and QuantLib, with only each instrument's `factory` and `bind` and the pricer `wrap` line changed; the harness rejects an overlay that changes anything else, including `conventions` and terms (X1).
9. **SC9** A tie-out report exists for a swap strategy and a bond strategy over real fixtures, with per-level maxima and locations, tolerances met or each exceedance explained (X2-X4, X6).
10. **SC10** The harness self-test passes: zero difference on identical stacks, a mutated convention detected, the reference stack matches a hand-derived answer (X5).
11. **SC11** Both adapters pass the layer conformance kit; the rateslib golden numbers still reproduce (A2, A5, L4).
12. **SC12** The gs_quant facade imports no external library and hardcodes no convention; `test_gs_compat_*` pass with the change log entries (F-1..F-4).
13. **SC13** Removing ARBS and `data/fixtures` leaves the default suite green with labelled skips (G6).
14. **SC14** `DESIGN.md`, `docs/guides/backtesting.md` and the README describe the neutral architecture and no longer state that schemas are "modelled on rateslib".
15. **SC15** The showcase notebook (W1-W4) executes from a clean kernel with saved outputs, its assertion cells pass, and it exercises: an external library passed in through binding, snapshot data, the engine, a signal, a trigger with a ladder-hedge action driven by `terms`, and the tearsheet (6.9).
16. **SC16** No swaption-related code, schema, config, test or shipped doc remains in `src`, `tests`, `configs` or `docs` (Q15); `IRSwaption` raises `NotSupportedError`.

## 12. Boundaries

- **Always:** run the guard tests and the core-only run after each module; validate any check first on an input with a known answer; keep ARBS and gs-quant read-only; use `logging`, not `print`; keep deterministic behaviour; snapshot the tree before starting.
- **Ask first:** adding a runtime dependency (including the QuantLib extra's version pin); changing any recorded real-data result beyond tolerance; deleting or rewriting a config in `configs/suite`; widening a tie-out tolerance; changing a MUST in this spec.
- **Never:** import an external library from core or the facade; name a library in a schema; infer a binding by name; hide a convention in core "for convenience"; write to ARBS or gs-quant; commit unless asked; remove or weaken a failing test without a change log entry; run live ARBS code that can launch Excel.

## 13. Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | **Another session is actively working in `src/pricebt`** (facade, ladder hedging, notebooks under `notebooks/src`, suite reruns writing `scratch/` and `results/`; edits observed 07:25-07:59 and later) and the tree has no version control | Snapshot first; get that work to a stable point before this refactor starts; one writer per file; this spec names the baseline (3.1) |
| R2 | The snapshot decoupling (D2) touches every store and the rateslib pricer, which the 64 real-data runs depend on | Do `snapshot` behind a compatibility wrapper first; reproduce results before removing the old path |
| R3 | QuantLib and rateslib will not agree to machine precision (interpolation, schedule generation, compounding, fixing timing) | Declared tolerances, empirical noise floor first, definition differences documented (X3, X6) |
| R4 | Global state (`Settings.evaluationDate`, rateslib fixings store) leaks between runs or tests | Adapter-owned guards and reset; a test that runs both stacks in one process in alternating order |
| R5 | Over-generalising the binding mini-language into an unreadable DSL | Keep the reference grammar closed (B3); prefer a dotted-path callable for anything unusual |
| R6 | The Taylor baseline is coarser than a multi-node decomposition and could be mistaken for the "real" attribution | Distinct names (`tay_*`), documented as coarse, never substituted silently |
| R7 | Usage-limit interruptions of long multi-agent runs (seen before) | Modules are independently deliverable; keep parallel work to 2-3 workers with file ownership and a "tests pass before returning" rule |
| R8 | Terms migration changes trade ledger contents (resolved dates) and breaks report comparisons | Ledger gains columns only; existing columns unchanged |

## 14. Open questions

None. Q1-Q17 were resolved by review on 2026-09-26 and are recorded in 0.2. New questions discovered during implementation MUST be recorded here with evidence before the affected requirement is changed (section 15).

## 15. Latitude and change control

This spec is strict where it says MUST and deliberately loose elsewhere.
- **Fixed:** every MUST and MUST NOT, above all the zero-dependence rule (section 2), the strict-binding rule (B1-B7), the dollar-delta and ladder contracts (S4, S7), the harness rules (X1, X5) and the success criteria (11.5). Nothing is skipped, weakened or reinterpreted to make a test pass.
- **Free:** every "One acceptable design" block, every SHOULD and MAY, file names, class names, the config syntax, module internals, and the order of work inside the build order. The implementer is expected to explore the codebase fully (including what the other session added), and to change the approach when testing on the live fixture data shows the assumed one is wrong (interpolation mismatches, convention surprises, ill-conditioned ladder solves, unexpected snapshot shapes).
- **How a MUST is changed:** if evidence shows a MUST is wrong or unachievable, the implementer records the evidence in `docs/design/11-refactor-changelog.md`, proposes the amended requirement, updates this spec FIRST, and only then changes the code. A MUST is never edited in the change log only, and a tolerance is never widened without a written explanation (X3).

## Appendix A - Banned-token list (seed for `tests/guards/banned.yaml`)

Import roots: `rateslib`, `QuantLib`, `gs_quant`, `MDP`, `Query`, `Caching`, `TB`, `BT`, `RVUtils`, `SDRUtils`, `Simulation`, `definitions` (ARBS top-level modules).
Executable-content tokens (case-insensitive, whole-word where it matters): `rateslib`, `quantlib`, `arbs`, `rl_swap`, `rl_bond`, `rl_swaption`, `rl_par_swap_ladder`, `usd_irs`, `us_gb_tsy`, `sum_instruments`, `analytic_delta`, `delta_usd`, `SyntheticMDP` (adapter-specific), `citivelocity`, `fedinvest`/`webull` (allowed only under `contrib`).
Convention tokens (Z4, path allow-list): `act/360`, `act/365`, `act360`, `act365`, `/365`, `/360`, `day_count`, `compounding`, `modifier`, `payment_lag`, `settlement`.
Allowed provenance: `gs_quant` in docstrings and `NOTICE`.

## Appendix B - Placeholder tie-out tolerances (to be replaced by the measured noise floor)

| Level | Quantity | Placeholder relative | Note |
|---|---|---|---|
| L0 | snapshot digest, resolved dates | exact | any difference here explains everything downstream |
| L1 | swap `pv` per unit | 1e-6 | forward-curve construction, fixing timing |
| L1 | bond `pv` (dirty) | 1e-7 | settlement and accrual conventions |
| L1 | `cash` on payment dates | 1e-8 | amounts are near-deterministic once dates agree |
| L1 | `financing` | 1e-8 | closed form |
| L2 | `dv01`, `gamma` | 1e-4 / 1e-2 | bump size and method differ across stacks |
| L3 | layers by name | declared per layer | definitions differ (X6); compare invariants and the baseline decomposition strictly |
| L4 | equity, ledger counts and timestamps | 1e-5 / exact | ledger structure must match exactly |

## Appendix C - Illustrative file-level change list (non-binding)

| Area | Change |
|---|---|
| `config/loader.py` | remove the adapter text-sniff (F2); add `instruments`, `bind`, terms; remove `methods:`, method-keyed `measures:`, `risk_models:` and `pricables:` (no alias) |
| `pricable.py` | replace `call_method` injection with binding execution; keep `MarkContext`, `Valuation` |
| `pricer.py` | minimal protocol (D7); `api_kwargs` optional/deprecated |
| `schema.py`, `schemas/*.yaml` | neutral schemas (S1-S6); terms; glossary as data; remove reducers |
| `costs.py` | callable-based cash accrual (C1) |
| `session.py` | remove `nyc`; calendar registry (C2); instruments registry (F-2) |
| `instrument.py` | terms constructors only (F-2) |
| `contrib/rateslib/*` | factories take neutral terms; default binding YAML; extension schema for ladders; `wrap(snapshot)` |
| `contrib/stores/*` | delete (D6); replaced by a test-support provider in `tests/support/` |
| `legacy.py`, `RequestMDP`, `testing/legacy_stub.py`, `test_legacy_bridge.py` | delete (B8) |
| swaption schema, `contrib/rateslib/swaption.py`, suite S10, `IRSwaption`, swaption tests and doc recipes | strip from `src`, `tests`, `configs`, `docs`; optional archive outside them (0.3) |
| `SyntheticMDP` | move to `pricebt.testing`, snapshot-emitting (D8) |
| `contrib/quantlib/*` | new adapter (A3) |
| `testing/` | `refstack` (A4), `layer_conformance` (L4), toys with mismatched names (B5) |
| `tieout/` (new) | harness, report, self-test |
| `tests/guards/` (new) | GT-Z*, twins, banned list |
| `configs/suite/*` | one instrument spec plus per-action terms |
| `docs/` | `DESIGN.md`, `guides/backtesting.md`, README, this spec's change log |
