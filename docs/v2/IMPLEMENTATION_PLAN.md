# pricebt v2 — Implementation Plan

Audience: the implementing agent(s) (Sonnet, run through ultracode workflows). Read `docs/v2/README.md` first, then **all of `DESIGN.md`**, then the research sections each task cites. This plan says **what to build, in what order, who owns which files, and how to prove each step is done.** The *what* and *why* live in DESIGN.md. If this plan and DESIGN.md disagree, DESIGN.md wins: stop and report the conflict.

---

## 0. Ground rules (apply to every task, every agent)

### 0.1 The five MUSTs, restated (DESIGN §2.1)

1. **No external pricing or market-data dependency in `src/pricebt`.** No import, name, special case or shape of ARBS, rateslib, QuantLib, gs_quant or any vendor. Allowed: stdlib, numpy, pandas, PyYAML, python-dateutil, tqdm. The guards in `tests/guards/` must pass after every task.
2. **The API is 1:1 with gs_quant.backtests.** Same module paths, class names, constructor field order and defaults, enum values, result shapes and column names. The only allowed differences are the DEV-* rows of DESIGN §11. The parity test (`tests/test_gs_api_parity.py`) is the referee.
3. **One asset config file per asset.** pricebt never interprets instrument kwargs.
4. **Multi-currency through FX configs; risk in currency per bp; the `pnl_bps()` view.**
5. **No asset-class special-casing** in the engine, the pricing layer or the results. Swaptions later must be config-only.

### 0.2 DO NOT

- DO NOT import, or run, anything from `C:\Users\chris\clee\ARBS` except in Phase 5 task P5.2, and only after the user says yes (§7). ARBS can reach a production database, Excel/COM and the network.
- DO NOT modify anything outside `C:\Users\chris\clee\gsquant-temp-claude\pricebt-v2`. gs-quant, ARBS, site-packages, the primary checkout `..\pricebt`, `..\pricebt-baseline` and `..\pricebt-final-snapshot` are read-only.
- DO NOT reintroduce any v1 machinery: schemas, bindings, snapshots, `wrap`, Kits, conventions blocks, stack overlays, tie-out, reference stacks, P&L layers, `contrib/`.
- DO NOT invent a library method in a config. The ARBS config may use only the methods verified in `research/06-arbs-pricing-api.md` §3. The toy config may use only the functions in `tests/toylib`.
- DO NOT "fix" gs behaviour that DESIGN §11 does not list, and do not "keep" a gs bug that §11 lists as fixed.
- DO NOT weaken a test to make it pass (no deleting asserts, no widening tolerances, no `xfail` or `skip` without a written reason in `docs/v2/DEVIATIONS.md` and the user's approval).
- DO NOT use `git stash` (the stack is shared across worktrees). Use a WIP commit.
- DO NOT write files with heredocs when they contain backslashes or backticks. Use the Write tool (global CLAUDE.md).

### 0.3 Environment and commands

```powershell
# every command runs from the worktree root
Set-Location C:\Users\chris\clee\gsquant-temp-claude\pricebt-v2
$env:PYTHONPATH = "src;tests"
$PY = "C:\Users\chris\anaconda3\envs\stir\python.exe"          # pricebt + tests (+ ARBS in P5 only)
$PYGS = "C:\Users\chris\anaconda3\python.exe"                  # ONLY for tools/gs_api_snapshot.py (gs_quant 1.5.4)
& $PY -m pytest tests -o addopts= -p no:cacheprovider -q       # full suite
& $PY -m pytest tests/guards -o addopts= -p no:cacheprovider   # guards
git -C C:\Users\chris\clee\gsquant-temp-claude\pricebt-v2 status -sb | Select-Object -First 1   # must say: ## v2-redesign
```

`pytest.ini` has `addopts =` empty. Pass `-o addopts=` anyway, because a second `-q` would hide the summary line.

### 0.4 Git

- Branch `v2-redesign` in worktree `C:\Users\chris\clee\gsquant-temp-claude\pricebt-v2`. Always use `git -C <that path>`. Check the branch before and after every commit (global CLAUDE.md).
- **Commit at every phase gate**, one commit per phase, message `v2: phase N — <title>`, ending with the attribution lines required by the session. Do not push. Do not merge to `main`.

### 0.5 Porting convention (Phase 3)

Copy the 2.1.17 file from `C:\Users\chris\clee\gsquant-temp-claude\gs-quant\gs_quant\backtests\`. Keep the Apache header and add `# Ported to pricebt from gs_quant 2.1.17 (Apache-2.0); see NOTICE. Changes: DEV-..`. Apply DESIGN §9.1–§9.2 (import map, delete JSON/Tracer), then **only** the DEV-* rows assigned to that file. Mark each changed block with a comment `# pricebt DEV-XX: <one line>` so that a reviewer can find every deviation by grep.

### 0.6 Definition of done for any task

1. The files listed as owned exist and nothing outside the owned list was edited (shared files: see the task).
2. The task's acceptance command passes, **and** `tests/guards` passes.
3. New logic has tests that fail when the logic is broken. For each task, pick one line of the new logic, mutate it with `tools/mutcheck.py` (or by hand, then restore), and confirm that a named test fails. Record the mutation and the failing test in the task report.
4. The task report (the agent's final message) lists: files created/changed, the tests added, the acceptance output summary line, the mutation check result, and any open question. **A report is a claim. The phase gate re-runs everything.**

---

## 1. Phase overview and dependency graph

```
P0 strip + skeleton + guards ──► P1 foundations (6 parallel tasks) ──► P2 instruments + pricing (3 tasks) ──►
P3 backtests port (3 parallel tasks, then 1) ──► P4 integration + parity + notebooks (4 parallel tasks) ──►
P5 ARBS example + docs (2 tasks; P5.2 needs the user's yes) ──► P6 review + hardening
```

| Phase | Tasks | Parallel? | Gate (the main loop runs this; every line must pass) |
|---|---|---|---|
| P0 | P0.1 | single agent | `pytest tests/guards` green; `python -c "import pricebt"` ok; the `git ls-files` counts in §2 |
| P1 | P1.1–P1.6 | yes (disjoint files) | full suite green; RelativeDate + GenericDataSource + MR-standalone goldens pass; the gs snapshot JSON exists |
| P2 | P2.1, P2.2, P2.3 | P2.1 ∥ P2.3, then P2.2 | full suite green; the pricing cache and FX tests pass |
| P3 | P3.1 ∥ P3.2 ∥ P3.3, then P3.4 | partly | full suite green; the result-shape goldens and engine smoke test pass |
| P4 | P4.1–P4.4 | yes | full suite green incl. `-m notebook`; parity test green |
| P5 | P5.1, P5.2 | P5.1 first | docs build/links check; P5.2 live run report (if approved) |
| P6 | P6.1–P6.3 | review fan-out | everything green; review findings resolved or accepted by the user |

**Workflow recipe for ultracode (per phase):** (1) one implementation agent per task, in parallel where the table allows, each with its owned-file list and a pointer to this plan's task section; (2) for each finished task, one **adversarial verifier** agent that re-runs the acceptance command, reads the diff against the cited DESIGN/research sections, and tries to refute "done", looking specifically for MUST-1/MUST-2 violations, gs behaviours changed without a DEV id, and tests that cannot fail; (3) a fix loop until the verifier passes; (4) the main loop runs the phase gate itself (never trusting reports), then commits. Keep it to ≤10 agents per phase. Use `isolation: 'worktree'` only if two tasks must edit the same file (they should not).

---

## 2. Phase 0 — Strip, skeleton, guards (single agent)

### P0.1 Strip v1 and lay down the skeleton

**Read:** DESIGN §2, §3.2; `research/08-pricebt-v1-strip-inventory.md` §8 (top-level inventory), §4 (yamlio), §11.2 (guards).

**Delete** (with `git rm -r`; everything is recoverable from tag `v1-final`):
- `arbs_adapter/`, `skills/`, `configs/` (all current contents), `tasks/`, `results/`, `results_new/` (untracked; delete from disk), `data/` (fixtures are untracked; **ask before deleting `data/fixtures` from disk**; just remove it from the tree's concerns, since it is git-ignored), `docs/` **except** `docs/v2/`, `notebooks/` (all current), `tests/` (all), `tools/` **except** `tools/nb_build.py` and `tools/mutcheck.py`, `CONSTRAINTS.md`.
- Everything under `src/pricebt/` **except** these salvage files, which you move/keep:
  - `src/pricebt/errors.py` → keep, then trim to: `PricebtError`, `ConfigError(message, asset=None, key=None)`, `AssetEvaluationError`, `MarketDataUnavailable`, `NotSupportedError` (DESIGN §6.6). Delete the other classes.
  - `src/pricebt/config/yamlio.py` → move to `src/pricebt/assets/yamlio.py`; **disable `${ENV}` interpolation** (asset expressions must never be rewritten); keep duplicate-key detection and YAML 1.2 scalars.
  - `src/pricebt/engine/progress.py` → move to `src/pricebt/progress.py`; drop anything tied to the v1 engine; keep the notebook-kernel single-display-output behaviour and `quiet_tqdm()`.

**Create:**
- `pyproject.toml`: name `pricebt`, version `2.0.0`, `requires-python >=3.11`, dependencies `numpy`, `pandas>=2.0`, `pyyaml`, `python-dateutil`, `tqdm`; optional extras `test = [pytest, nbformat, nbclient, ipykernel]`; src layout.
- `pytest.ini`: `[pytest]` `testpaths = tests`, `addopts =`, `markers = core: no external deps (default) | notebook: executes a notebook | live_arbs: needs PRICEBT_LIVE_ARBS=1 and ARBS`, `filterwarnings = error::DeprecationWarning:pricebt`.
- `tests/conftest.py`: adds marker `core` to every unmarked test; skips `live_arbs` unless `os.environ.get("PRICEBT_LIVE_ARBS") == "1"`.
- The package skeleton from DESIGN §3.2 with **empty modules** (docstring only) so that imports resolve.
- `NOTICE`: "pricebt includes code ported from gs_quant (https://github.com/goldmansachs/gs-quant), Copyright 2019 Goldman Sachs, licensed under the Apache License, Version 2.0. Ported files carry the original header and a note of changes." Then the Apache-2.0 notice text pointer.
- `AGENTS.md`: already rewritten on this branch (points to docs/v2). Keep it.
- `README.md`: short placeholder ("pricebt v2 — see docs/v2/README.md"); P5.1 writes the real one.
- **`tests/guards/`** (DESIGN §12.2), fully working in this phase:
  - `test_import_scan.py`: AST scan of `src/pricebt/**/*.py`; allowed roots = `sys.stdlib_module_names ∪ {numpy, pandas, yaml, dateutil, tqdm, pricebt}`; plus a non-vacuity twin that runs the scanner on a temp file containing `import rateslib` and asserts it reports it.
  - `test_token_scan.py`: banned tokens (`\b(arbs|rateslib|quantlib|irswapsmdp|eris|bloomberg|refinitiv|marquee)\b`, case-insensitive) in code and strings of `src/pricebt/**`, skipping `#` comment lines and the leading license-header block; `gs_quant` is banned only in executable code (use `tokenize` to drop COMMENT tokens and module/class/function docstrings); with a non-vacuity twin.
  - `test_import_blocker.py`: a subprocess with a `sys.meta_path` blocker for `gs_quant, rateslib, QuantLib, MDP, Query, Caching, dataclasses_json` that imports every module under `pricebt`. It is extended in P4 to run the toy MR backtest.

**Acceptance:**
```powershell
& $PY -m pytest tests/guards -o addopts= -p no:cacheprovider      # all pass, including the non-vacuity twins
& $PY -c "import pricebt, pricebt.backtests, pricebt.assets; print(pricebt.__version__)"   # 2.0.0
git -C C:\Users\chris\clee\gsquant-temp-claude\pricebt-v2 ls-files src | Measure-Object -Line  # only skeleton + salvage files
```

---

## 3. Phase 1 — Foundations (6 tasks in parallel; disjoint files)

### P1.1 Enums and base helpers
**Owns:** `src/pricebt/base.py`, `src/pricebt/common.py`, `tests/test_common.py`.
**Read:** R04§4 (every enum's exact members and values), R01§2 (`EnumBase`), R04§3.1 (coercion).
**Build:**
- `EnumBase` mixin: case-insensitive `_missing_` on `.value`; `__str__`/`__repr__` return `.value`; `__lt__` compares by value.
- Enums as `class X(EnumBase, str, Enum)` (the **str mixin** lets configs compare to strings) with exactly the members and values in R04§4: `PayReceive` (with `_missing_` accepting `receive`/`receiver`), `BuySell`, `AggregationLevel`, `OptionType`, `OptionStyle`, `DayCountFraction`, `BusinessDayConvention`, `SwapClearingHouse`, `PrincipalExchange`, `SwapSettlement`, `AccrualConvention`, `UnderlierType`, `TradeAs`, `OptionSettlementMethod`, `OptionExerciseStyle`, `RiskMeasureUnit`, `AssetClass`, `AssetType` (the relevant subset in R04§4 is enough; add a note), `CurrencyName`, `PositionType`. **`Currency`**: accept any ISO-4217 code. Generate the members from a code list covering at least the G10 and EM codes in R04§4, with `name == value`, and `_missing_` upper-casing. Also `Currency._` = `''` as in gs.
- `base.py`: `Priceable` marker base class, `static_field(v)`, `field_metadata` (a `None` placeholder usable as `field(default=None)`), `exclude_none`, `get_enum_value(enum_type, value)` (lenient: an invalid value logs a warning and returns the raw value).
- `common.py` also re-exports `RiskMeasure`, `ParameterisedRiskMeasure` lazily from `pricebt.risk` (to avoid a cycle).
**Tests:** members and values against R04§4; `PayReceive('receiver') is PayReceive.Receive`; `str(PayReceive.Receive) == 'Rec'`; `PayReceive.Pay == 'Pay'`; `Currency('usd') is Currency.USD`; invalid → `ValueError`.
**Acceptance:** `& $PY -m pytest tests/test_common.py -o addopts=`

### P1.2 Dates: RelativeDate and business-day helpers
**Owns:** `src/pricebt/datetime/__init__.py`, `src/pricebt/datetime/relative_date.py`, `tests/test_relative_date.py`.
**Read:** R01§5.5, R04§8 (all of it), R08§7.2 (the three v1 mistakes to avoid).
**Build:** `RelativeDate(rule, base_date=None).apply_rule(currencies=None, exchanges=None, holiday_calendar=None, week_mask='1111100', **kwargs)`, `.as_dict()`; `RelativeDateSchedule(rule, base_date=None, end_date=None).apply_rule(...)`. Rules `b, d, w, m, y` (plus `k` = `y`, `e`, `x`, `v`, `J`, `A`, `r`, `u`, `g` if cheap; otherwise `NotImplementedError(f'Rule {rule} not implemented')`), compound rules left to right (`'1m+2b'`, `'-1y'`), **units lower-cased** (DEV-T15). `base_date=None` → `PricingContext.current.pricing_date` if a context is entered, else `date.today()` (import lazily to avoid a cycle). Currencies and exchanges are **ignored** with a one-time warning if non-empty (no calendars in core; DEV-T3). `holiday_calendar` = a list/tuple of dates. `business_day_offset(dates, offsets, roll='raise', calendars=(), week_mask=None)`, `is_business_day`, `prev_business_date`, `date_range` (all three begin/end forms, R04§8.1), `business_day_count`, `today(location=None)`. Use `numpy.busday_offset` with a `busdaycalendar(weekmask, holidays)`.
**Tests:** every row of R04§8.3 (both base dates, all rules, all schedules, the holiday case, the Saturday start kept) and R01§5.4's verified `get_final_date`-style results (`'3m'` from 2024-01-31 → 2024-04-30; `'1y'` → 2025-01-31; `'5b'` from Fri 2024-01-05 → 2024-01-12); `'1M'` == `'1m'`.
**Acceptance:** `& $PY -m pytest tests/test_relative_date.py -o addopts=`

### P1.3 Risk measures, result objects, transform
**Owns:** `src/pricebt/risk/__init__.py`, `src/pricebt/risk/results.py`, `src/pricebt/risk/transform.py`, `src/pricebt/target/common.py`, `src/pricebt/target/measures.py`, `tests/test_risk_measures.py`, `tests/test_risk_results.py`.
**Read:** DESIGN §8.1–§8.2; R03§14 (identity, measures, result objects); R04§9.
**Build:** the RiskMeasure dataclass family with gs identity, call and repr semantics, and every measure listed in DESIGN §3.2 (`risk/__init__.py`) with its gs class, `measure_type` and presets (`IRDeltaParallel` = `IRDelta` with `aggregation_level=Asset` and name `'IRDeltaParallel'`; `IRDeltaLocalCcy` = `currency='local'`; `IRVegaParallel`, `IRVegaLocalCcy` likewise). Result classes per DESIGN §8.2 **except** the lazy bucketed-aggregation hook, which is a constructor argument `bucket_aggregator: Optional[Callable]` that P2.2 supplies; with it absent, bucketed aggregation = concat+groupby+sum. `PricingFuture`. `Transformer` base with `apply(result)`, and `ResultWithInfoAggregator(risk_col='value', filter_coord=None)` collapsing a bucketed result to a `FloatWithInfo` sum. `target/measures.py` re-exports `Price`, `ResolvedInstrumentValues`; `target/common.py` re-exports the enums.
**Tests:** equality/hash/repr examples from R03§14.1 and R04§9 (`Price != Price(currency='USD')`, `repr(IRDelta(aggregation_level='Type', currency='USD')) == 'IRDelta(aggregation_level:Type, currency:USD)'`, sorting); `FloatWithInfo` unit-mismatch `ValueError`; `PortfolioRiskResult` indexing, `__add__` merge, `aggregate`, and `to_frame(values='value', index='instrument_name', columns='risk_measure')` summing buckets per instrument (R03§7, VERIFIED: IRDelta 50+800 → 850); a pandas Series passed to a measure's `__call__` returns the measure.
**Acceptance:** `& $PY -m pytest tests/test_risk_measures.py tests/test_risk_results.py -o addopts=`

### P1.4 Asset config loader, validator, namespace
**Owns:** `src/pricebt/assets/__init__.py`, `src/pricebt/assets/config.py`, `src/pricebt/assets/namespace.py`, `src/pricebt/assets/fx.py` (loader + namespace only; the FX cache lives in P2.2), `tests/test_asset_config.py`.
**Read:** DESIGN §4 (all), §6.6.
**Build:** `load_asset(source: str | Path | Mapping) -> AssetConfig` and `load_fx(source) -> FxConfig`: parse (yamlio), validate against the DESIGN §4.2 schema (required keys, types, unit set, `returns` set, `build_on` set, `risk_measures` shapes, `Price` present, the functions named by `risk_measures` exist, `size_attribute` ∈ `attributes`, schema_version == 1), and give every error the asset name, the key path and a did-you-mean (use `difflib.get_close_matches`). Compile every expression at load (syntax error → `ConfigError` with key and line). `AssetNamespace(cfg)`: lazy one-time `exec(imports)` then `exec(code)` into a private globals dict; `eval(key, **injected)` with the compiled code and a fresh locals dict; exceptions → `AssetEvaluationError` (chained); a failed `imports`/`code` is cached and re-raised. Frozen dataclasses: `AssetConfig`, `FunctionSpec`, `RiskMapping`, `FxConfig`. Also `validate_resolved(dict) -> dict` (plain-data check, DESIGN §4.4).
**Tests:** a minimal valid dict config; each validation error path (unknown key with a suggestion, bad unit, missing Price, dangling function name, wrong schema_version, bad `build_on`); a syntax error names the key; **lazy import** (a config whose `imports` would raise does not raise at load, but does on the first eval); namespace isolation (two assets each defining `f` get their own); `AssetEvaluationError` carries asset/key/expr/date; `validate_resolved` rejects a list or an object.
**Acceptance:** `& $PY -m pytest tests/test_asset_config.py -o addopts=`

### P1.5 Toy library and toy configs (tests only, never shipped)
**Owns:** `tests/toylib/__init__.py`, `tests/toylib/rates.py`, `tests/assets/toy_usd_irs.yaml`, `tests/assets/toy_eur_irs.yaml`, `tests/assets/toy_fx.yaml`, `tests/test_toylib.py`.
**Read:** DESIGN §4, §7, Appendix A (to mirror its shape), §12.4.
**Build:** a **deterministic closed-form** rates world. The market for date `d` is a `ToyCurve(ref_date, zero_rate)` with a flat continuously-compounded zero rate `r(d) = base + amp*sin(2π·n/period)` (per currency parameters; `n` = business-day index since 2020-01-01), so that mean reversion actually triggers. `build_swap(market, terms)` → a `ToySwap(effective, maturity, fixed_rate, notional)` with annual fixed payments against a floating leg valued as `N·(DF(eff) − DF(mat))`. Functions: `npv` (payer positive when rates rise), `pv01` (analytic: `N × annuity × 1e-4`), `par_rate`, `delta_ladder(market, trades, weights, tenors)` (bucket each trade's annual-payment PV01 to the nearest pillar tenor by maturity; linear in weights). Market returns `None` on a configurable list of "hole" dates (to test `missing_market`). `toy_fx.yaml`: `EURUSD = 1.10 + 0.01*sin(...)`, inverse supported. The configs follow DESIGN Appendix A's structure (imports `tests.toylib` by module name; `resolve` pins dates and ATM; `attributes`, `size_attribute`, `risk_measures` with `IRDelta: {scalar: dv01, bucketed: delta_ladder}`).
**Tests:** `npv` at par ≈ 0; the pv01 sign (payer > 0); `sum(delta_ladder) == pv01` for a single trade; ladder linearity; a deterministic rate path; the hole dates return `None`. **These tests call toylib directly**, not through pricebt (pricebt does not exist yet).
**Acceptance:** `& $PY -m pytest tests/test_toylib.py -o addopts=`

### P1.6 gs API snapshot tool
**Owns:** `tools/gs_api_snapshot.py`, `tests/data/gs_api_1_5_4.json`, `tests/data/gs_api_exceptions.yaml` (initially empty list with a comment).
**Read:** DESIGN §12.3; R01§4–§7, R02§2–§3, R03§1, §10–§12, R04§1–§9 (to know which symbols to include).
**Build:** a script that **must be run with `$PYGS`** (it asserts `gs_quant.__version__ == "1.5.4"`), introspects the symbol list of DESIGN §12.3 and writes the JSON (sorted keys, stable ordering, a header with the version and date). No network: do not create a GsSession; import only the modules listed.
**Acceptance:** `& $PYGS tools/gs_api_snapshot.py --out tests/data/gs_api_1_5_4.json` succeeds; the JSON contains `gs_quant.backtests.triggers.MeanReversionTriggerRequirements` with fields `[data_source, z_score_bound, rolling_mean_window, rolling_std_window, class_type]`, `gs_quant.instrument.IRSwap` with 31 init parameters starting `pay_or_receive, termination_date, notional_currency, notional_amount, effective_date`, and `GenericEngine.run_backtest` with the exact parameter list of R02§3.2.

**P1 gate:** full suite green; guards green; `git -C ... status -sb` on `v2-redesign`; commit `v2: phase 1 — foundations`.

---

## 4. Phase 2 — Instruments, portfolio, contexts, pricing service

### P2.1 Instruments and Portfolio
**Owns:** `src/pricebt/instrument/__init__.py`, `src/pricebt/instrument/_gs_fields.py`, `src/pricebt/markets/portfolio.py`, `tests/test_instrument.py`, `tests/test_portfolio.py`.
**Read:** DESIGN §5 (all), §9.4; R04§1–§3, §5.
**Build:** `_gs_fields.py`: for `IRSwap`, `IRSwaption`, `FXOption`, `FXForward`, `EqOption`, `InflationSwap`, `Cash`, the list `[(field_name, coerce_tag, default)]` in gs order (R04§1–§2), plus `asset_class`/`type_` constants. `coerce_tag` is the pricebt enum class name or `None`. `Instrument` and the generated classes per DESIGN §5.1–§5.4 (positional args map to the gs order; extra kwargs pass through; `asset=`, `quantity=`). **Pricing methods (`resolve`, `calc`, `price`, `dollar_price`) delegate to `pricebt.markets` helpers written in P2.2.** In this task, implement them as thin calls to `pricebt.markets._engine_calc(...)` / `_engine_resolve(...)`, with stubs that raise `NotImplementedError` until P2.2 lands, so that the two tasks meet at a named seam. `__getattr__` order per §5.1 (it calls `PricingService.attribute` when a session exists; otherwise resolved_terms/kwargs). `instrument_identity(inst)` helper (DEV-I3). `Portfolio` per R04§5 (constructor forms, `priceables`, `instruments`, `all_instruments` (dedup by identity **and name**, gs), `all_portfolios`, `__len__`, `__iter__`, `__getitem__`, `__contains__` (name or instrument), `paths`, `__eq__`, `__hash__`, `__add__`, `append/extend/pop`, `clone(clone_instruments=False)`, `scale(s, in_place=True)` (not in place → a flattened unnamed Portfolio), `to_frame()` (index `['portfolio','instrument']`; columns `asset_class`, `type`, then sorted terms + `quantity`), and `calc`/`resolve` delegating like Instrument).
**Tests:** `IRSwap('Pay', '10y', 'USD', 1e4)` positional mapping; camelCase; enum coercion + invalid → `ValueError`; eq/hash including name; `clone(name=...)` keeps resolution fields; `scale(-2)` on a resolved clone → quantity −2; unresolved + `check_resolved` → `RuntimeError`; `to_dict()` without name, `as_dict()` with; Portfolio `all_instruments` dedup; `scale(in_place=False)` flattening; `to_frame` columns.
**Acceptance:** `& $PY -m pytest tests/test_instrument.py tests/test_portfolio.py -o addopts=`

### P2.3 Session and data helpers (parallel with P2.1)
**Owns:** `src/pricebt/session.py`, `src/pricebt/data/__init__.py` (**without** `measure_series`, added by P4.3), `src/pricebt/assets/registry.py`, `tests/test_session.py`, `tests/test_registry.py`.
**Read:** DESIGN §5.3, §6.1, §10.
**Build:** `AssetRegistry` (add from path/dir/dict/AssetConfig; duplicate names → `ConfigError`; the `market.key` sharing check; `match(instrument_class_name, kwargs, explicit_asset)` → AssetConfig per §5.3 with exact error messages), `PricebtSession` (constructs a registry and a `PricingService`, which is a forward reference: import lazily inside `__init__` so that P2.3 does not depend on P2.2's file existing at import time; its tests may monkeypatch), `GsSession.use` no-op shim with the gs signature, `Environment`. `Dataset` stub raising `NotSupportedError`, `DataFrequency` enum.
**Tests:** registry add/dup/match (0/1/many, explicit `asset=`, enum-vs-string match), `market.key` conflict detection, session `use`/`current`/context-manager restore, `GsSession.use(client_id=None, client_secret=None, scopes=('run_analytics',))` runs and records.
**Acceptance:** `& $PY -m pytest tests/test_session.py tests/test_registry.py -o addopts=`

### P2.2 PricingService + contexts (after P2.1 and P2.3)
**Owns:** `src/pricebt/assets/pricing.py`, `src/pricebt/assets/fx.py` (add the cache and the evaluation; coordinate: P1.4 wrote the loader in the same file, so extend, do not rewrite), `src/pricebt/markets/__init__.py`, `tests/test_pricing_service.py`, `tests/test_pricing_cache.py`, `tests/test_contexts.py`, `tests/test_multi_currency_pricing.py`, `tests/test_ladder.py`.
**Shared edit (allowed):** replace the seam stubs in `src/pricebt/instrument/__init__.py` and `src/pricebt/markets/portfolio.py` with real calls (the smallest diff possible; note it in the report).
**Read:** DESIGN §4.3–§4.5, §5.3–§5.4, §6 (all), §7, §8.1–§8.2; R04§6 (context semantics, futures, historical results).
**Build:** `PricingService` exactly per DESIGN §6.2–§6.4 and §8.1 (the measure → function mapping incl. `DollarPrice`, `ResolvedInstrumentValues`, scalar/bucketed selection by `aggregation_level`, quantity application, FX conversion by unit, `FloatWithInfo`/`DataFrameWithInfo` construction with `risk_key` and `unit`, the lazy per-instrument bucketed values plus the group aggregator passed into `PortfolioRiskResult`). Caches per §6.3. `PricingContext`/`HistoricalPricingContext` per §6.5 (the gs signature; a stack; inherited `pricing_date`/`csa_term`; eager results; the future-when-entered convention). `Portfolio.calc`/`resolve` and `Instrument.calc`/`resolve` semantics incl. the historical `{date: Portfolio}` resolve result and `MultipleRiskMeasureResult`.
**Tests (on the toy configs):** resolve pins dates and ATM (`test_resolve_pins_dates`: a `'10y'` swap resolved on d1 keeps its maturity when valued on d2); market evaluated once per (key, date, csa) (count evaluations via a counter in the toy code block); a hedge unit and its scaled copy share the unit-value cache; `build_on` both modes; `csa` reaches `market.expr` as `pricebt_csa` and is part of the cache key; FX conversion and unit rules (a `bp` measure with a currency → `ConfigError`); the mixed-currency aggregate `ValueError`; `IRDelta(aggregation_level='Type')` → FloatWithInfo, `IRDelta` → DataFrameWithInfo with the gs columns and labels; group ladder == sum of per-trade ladders (1e-9 rel); the future returned inside a context, the value outside; HistoricalPricingContext calc/resolve shapes; `reset()` clears the caches.
**Acceptance:** `& $PY -m pytest tests -o addopts= -p no:cacheprovider -q` (the full suite, since the seam touches P2.1 files)

**P2 gate:** full suite green; guards green; commit `v2: phase 2 — instruments, contexts, pricing`.

---

## 5. Phase 3 — Port gs 2.1.17 backtests

Source directory (read-only): `C:\Users\chris\clee\gsquant-temp-claude\gs-quant\gs_quant\backtests\`. Follow §0.5 exactly.

### P3.1 Utils, core, strategy, data sources, triggers
**Owns:** `src/pricebt/backtests/{__init__,core,backtest_utils,strategy,data_sources,triggers}.py`, the stubs `src/pricebt/backtests/{equity_vol_engine,predefined_asset_engine,strategy_systematic,order,event,data_handler,execution_engine}.py`, `tests/test_backtest_utils.py`, `tests/test_generic_data_source.py`, `tests/test_triggers.py`, `tests/test_mean_reversion_golden.py`.
**Read:** R01 (all), DESIGN §9, §11 rows DEV-T1..T15.
**Port notes:** `strategy.py` imports `GenericEngine` lazily (as gs does). `GenericDataSource` is **rewritten** per R01§7.4 "v2 specification" (DEV-T13); keep its gs signature and `class_type`. `TriggerRequirements.reset()` hook (no-op) + `MeanReversionTriggerRequirements.reset()` (sets `current_position = 0`) (DEV-T10). `PeriodicTriggerRequirements`: accept `start_date=None` and let the engine fill it (DEV-T4): add a `_backtest_start: Optional[date]` set by the engine and used when `start_date` is None. Keep every "parity kept" quirk in DESIGN §11.
**Tests:** R01§6.5.9 golden table (run the trigger over the 19-point series through the new `GenericDataSource(s, MissingDataStrategy.fill_forward)`; assert the triggered/scaling/position columns row by row); zero std → fires; NaN → no fire; `reset()`; R01§7.4 ffill 2.0 (not 5.0), interpolate 3.0, `get_data_range` int/tuple rules, no mutation, `DatetimeIndex` vs `date` coercion; `check_barrier` strictness; Aggregate ALL_OF short-circuit vs ANY_OF; Not calc_type inheritance; Aggregate/Not `get_trigger_times` union; `make_list` table (R01§5.3); `get_final_date` rules incl. a timedelta and `'next schedule'` with and without info; the stubs raise `NotSupportedError`.
**Acceptance:** `& $PY -m pytest tests/test_backtest_utils.py tests/test_generic_data_source.py tests/test_triggers.py tests/test_mean_reversion_golden.py -o addopts=`

### P3.2 Actions and action handler interfaces
**Owns:** `src/pricebt/backtests/{actions,action_handler,backtest_engine}.py`, `tests/test_actions.py`.
**Read:** R02§2 (field tables, naming rules R1–R7), R02§9 (2.1.17 additions), DESIGN §11 DEV-E7, DEV-E10.
**Tests:** every action's positional field order and defaults (against the snapshot JSON too); naming rule R1 examples from R02§2.4; the `transaction_cost_exit` defaulting; `HedgeAction` wraps a single priceable into a Portfolio named after it; bad `HedgeAction` input → `ValueError` (DEV-E7); `ExitTradeAction('x').priceable_names == ['x']` (DEV-E10); the global `Action{N}` counter.
**Acceptance:** `& $PY -m pytest tests/test_actions.py -o addopts=`

### P3.3 BackTest result object and support classes
**Owns:** `src/pricebt/backtests/backtest_objects.py`, `tests/test_result_shapes.py`, `tests/test_transaction_costs.py`, `tests/test_cash_accrual.py`, `tests/test_pnl_bps.py`.
**Read:** R03 (all), DESIGN §7, §8.3, §8.4, §11 DEV-R1..R4, DEV-E11, DEV-E13.
**Build:** port per §9.3. `BackTest.missing_market_dates: list` attribute (default `[]`). `pnl_bps()` per DESIGN §8.4. `summary_stats(annualisation_factor=252)` from 2.1.17.
**Tests:** build `BackTest` state dicts by hand (no engine) with toy `FloatWithInfo` values, exactly as R03§9 does, and assert: Case A frames; Case B with DEV-R1 (`Total` on D3 == 2290); Case E (a direction-0 ledger row); the `strategy_as_time_series` MultiIndex shape; `risk_summary` zeros; the empty-results frame (DEV-R3); transaction-cost math (Constant not scaled by `additional_scaling`; Scaled = `level × |cost × scaling|`; Aggregate SUM/MAX/MIN) per R02§2.6 / R03§10; cash accrual `(1 + r/365)^days` per currency; `pnl_bps` hand computation and the NaN rule; the multi-currency `RuntimeError('Cannot aggregate cash in multiple currencies')` message plus the hint.
**Acceptance:** `& $PY -m pytest tests/test_result_shapes.py tests/test_transaction_costs.py tests/test_cash_accrual.py tests/test_pnl_bps.py -o addopts=`

### P3.4 GenericEngine and action impls (after P3.1–P3.3)
**Owns:** `src/pricebt/backtests/generic_engine.py`, `src/pricebt/backtests/generic_engine_action_impls.py`, `tests/test_engine_smoke.py`, `tests/test_missing_market.py`.
**Read:** R02 (all, especially §4 pseudocode, §5 handlers, §7 quirks), DESIGN §9.4–§9.6, §11 DEV-E1..E15, DEV-T2, DEV-T4, DEV-T10, DEV-T11.
**Port notes:** at `run_backtest` start: `PricebtSession.current.pricing.reset()`, clear the `get_final_date` cache (DEV-T2), call `reset()` on every trigger requirement (walk Aggregate/Not children) (DEV-T10), set `_backtest_start` on Periodic requirements with `start_date=None` (DEV-T4). Then the grid, then the **missing-market pre-scan** (DESIGN §9.5), then gs phases 2–8 with the DEV-E fixes. `csa_term`: `run_backtest(csa_term=...)` → the ambient `PricingContext(csa_term=...)` wrapping the whole run; `HedgeAction.csa_term` → the `HistoricalPricingContext(dates, csa_term=...)` around hedge resolution only (as gs). The progress bar per DESIGN §9.6.
**Tests:** `test_engine_smoke.py`: a toy USD swap, `PeriodicTrigger('1m')` + `AddTradeAction(swap, '1m')` over 3 months with `frequency='1b'`. Assert that the ledger names, open/close dates, `Trade PnL == Close Value + Open Value`, the `Total` identity on every row (`Total == Price + Cumulative Cash + Transaction Costs`), entry cash == −PV(create) and exit cash == +PV(final) (priced independently through `PricingService`), the holding window `create <= s < final`, and determinism (two runs → `pd.testing.assert_frame_equal`). `test_missing_market.py`: toy hole dates; `'drop'` warns, lists, and removes from the grid and triggers; `'raise'` raises `MarketDataUnavailable`.
**Acceptance:** `& $PY -m pytest tests -o addopts= -p no:cacheprovider -q`

**P3 gate:** full suite green; guards green; `grep -rn "pricebt DEV-" src/pricebt/backtests` lists every DEV id from DESIGN §11 that belongs to the backtests package (check it against the table); commit `v2: phase 3 — backtests port`.

---

## 6. Phase 4 — Integration, parity, notebooks (4 tasks in parallel)

### P4.1 Scenario tests (the notebook swap variants)
**Owns:** `tests/test_engine_periodic_roll.py`, `tests/test_engine_hedge.py`, `tests/test_engine_exit_trade.py`, `tests/test_engine_scaled_add.py`, `tests/test_engine_rebalance.py`, `tests/test_engine_risk_trigger.py`, `tests/test_multi_currency.py`, `tests/test_engine_holidays.py`.
**Read:** DESIGN §12.4; R05§4 ("swap variant" column), §7; R02§5.
**Build:** one test module per row of DESIGN §12.4 that is not yet covered, using the toy configs, with exact assertions (hand-computed from `PricingService` unit values, not magic numbers). Include: 040310 hedge (book IRDelta ≈ 0 after each hedge, `pricebt_csa='EUR-OIS'` observed only during hedge resolution, via a recorder in the toy code block); 040305-style `StrategyRiskTrigger` + `[ExitTradeAction(), AddTradeAction(...)]` that **fires again after the first rebalance** (DEV-E1); 040311 holidays (`holiday_calendar` passed to `run_backtest` and to the action); multi-currency per DESIGN §7 (both the error paths and the `result_ccy='USD'` numbers).
**Acceptance:** `& $PY -m pytest tests/test_engine_*.py tests/test_multi_currency.py -o addopts=`

### P4.2 API parity test
**Owns:** `tests/test_gs_api_parity.py`, `tests/data/gs_api_exceptions.yaml`.
**Read:** DESIGN §12.3.
**Build:** the comparison test. Every exception entry needs `{symbol, aspect, reason, dev_id}`; `reason` must not be empty. Anything missing from pricebt that is in scope → failure. The test must **not** import gs_quant (assert `"gs_quant" not in sys.modules` at the end).
**Acceptance:** `& $PY -m pytest tests/test_gs_api_parity.py -o addopts=`; then a non-vacuity check: temporarily swap two fields of `MeanReversionTriggerRequirements` and confirm the test fails (record it in the report; restore).

### P4.3 `measure_series` and the toy 040304 notebook
**Owns:** `src/pricebt/data/__init__.py` (add `measure_series`; P2.3 created the file), `notebooks/src/040304_mean_reversion_toy.py`, `notebooks/040304_mean_reversion_toy.ipynb` (built by `tools/nb_build.py`), `tests/test_measure_series.py`, `tests/test_040304_toy.py`, `tests/test_notebook_toy.py` (marker `notebook`).
**Read:** DESIGN §10, Appendix B; R05§6.
**Build:** `measure_series` per DESIGN §10; the toy notebook = Appendix B with `assets=["tests/assets/toy_usd_irs.yaml"]` and dates inside the toy world; `test_040304_toy.py` runs the same code as a script and asserts the R05§6.5 shapes (ledger rows = MR events, all `open`, `Close None`, `Long Short -1`; `result_summary` columns `[Price, 'Cumulative Cash', 'Transaction Costs', 'Total']`); `test_notebook_toy.py` executes the .ipynb with nbclient (timeout 600 s).
**Acceptance:** `& $PY -m pytest tests/test_measure_series.py tests/test_040304_toy.py -o addopts=`; `& $PY -m pytest -m notebook -o addopts=`

### P4.4 Guard extension: end-to-end with blocked libraries
**Owns:** `tests/guards/test_import_blocker.py` (extend).
**Build:** in the blocked subprocess, also run the toy MR backtest (the `test_040304_toy` code) and assert that it completes and that `result_summary` is non-empty.
**Acceptance:** `& $PY -m pytest tests/guards -o addopts=`

**P4 gate:** full suite incl. `-m notebook` green; parity green; guards green; commit `v2: phase 4 — integration, parity, notebooks`.

---

## 7. Phase 5 — ARBS example and user docs

### P5.1 Docs and the ARBS config file (no ARBS execution)
**Owns:** `configs/assets/usd_sofr_ois_interest_rate_swap.yaml` (copy DESIGN Appendix A verbatim), `configs/fx/README.md`, `README.md`, `docs/v2/ASSET_CONFIG_GUIDE.md`, `docs/v2/DEVIATIONS.md`, `notebooks/src/040304_mean_reversion_usd_sofr_arbs.py`, `notebooks/040304_mean_reversion_usd_sofr_arbs.ipynb` (built, **not executed**), `tests/test_arbs_config_static.py`.
**Build:**
- `ASSET_CONFIG_GUIDE.md`: the schema (DESIGN §4.2), the injected variables table, units, the evaluation rules, "resolve must pin", `build_on`, FX configs, a worked toy example, the swaption walkthrough (DESIGN §13), the security note (configs are trusted code), ARBS safety rules (R06§6.2: the env var before import, store-only reads, no Citi sources, NOJUMPS hazards).
- `DEVIATIONS.md`: every DEV row from DESIGN §11 with its test name, plus the "parity kept" list, plus the additions (`pnl_bps`, `measure_series`, `missing_market`, `PricebtSession`, `ConfigInstrument`, `quantity`).
- `README.md`: what pricebt is, the 10-line quick start (the toy), how to port a gs notebook (the 3 edits), links.
- `test_arbs_config_static.py` (**core tier; does not import ARBS**): `load_asset` on the ARBS file succeeds (validation + compile only; `imports`/`code` are lazy, so nothing is executed); assert that it maps `Price`, `IRDelta`, `IRDeltaParallel`, and that `market.expr` does not contain `NOJUMPS` or `get_pricer`.
**Acceptance:** `& $PY -m pytest tests/test_arbs_config_static.py -o addopts=`; every relative link in the docs resolves (a small link-check script or a test).

### P5.2 Live ARBS run (**STOP: ask the user first**, see §9)
**Owns:** `tests/test_live_arbs.py` (marker `live_arbs`), `docs/v2/LIVE_ARBS_REPORT.md`.
**Only after the user says yes:** with `$env:PRICEBT_LIVE_ARBS="1"`:
1. `load_market` for 5 known dates (2024-05-20..24): each returns a market with `reference_date == d`; a weekend returns `None`; `date.today()` returns `None`.
2. Price a 10y ATM payer on 2024-05-20: `npv` ≈ 0 (|npv| < 1 USD per 1mm), `dv01` > 0 and in the range 800–900 USD per 1mm, `par_rate` in bp between 300 and 600.
3. The seasoned mark: the same trade on 2024-05-24 has `npv ≠ 0`, and its maturity is unchanged (resolve pinned it).
4. `delta_ladder` for [payer 10y] sums to within 2% of `dv01`.
5. A short 040304: start 2024-01-02, end 2024-06-28, windows 30; completes; the ledger is non-empty or the report explains why no trigger fired; record the runtime.
6. Confirm no network or store writes: record the curve-store directory mtime before and after (read-only check) and state the result in `LIVE_ARBS_REPORT.md`.
**Acceptance:** `& $PY -m pytest -m live_arbs -o addopts=` green, and the report written with numbers.

**P5 gate:** docs committed; live run done or explicitly deferred by the user; commit `v2: phase 5 — ARBS example and docs`.

---

## 8. Phase 6 — Review and hardening (fan-out review, then fixes)

### P6.1 Adversarial review (parallel reviewers, each with one lens; report findings with file:line, no edits)
1. **MUST-1 lens**: any dependency, name or shape leak into `src/pricebt`, incl. docstrings that describe ARBS behaviour.
2. **MUST-2 lens**: diff each ported file against its 2.1.17 source. Every behavioural change must carry a `# pricebt DEV-` marker and a §11 row; report unmarked changes and §11 rows with no marker.
3. **MUST-5 lens**: any swap/IR special case in the engine, pricing layer or results (e.g. `if 'IR' in ...`, `notional` literals, tenor logic outside `datetime`).
4. **Correctness lens**: cash/ledger identities, the currency conversion paths, cache keys (a stale-value risk: is `csa` in every key that depends on it? is `build_on=resolve_date` keyed without the date?), and `reset()` coverage.
5. **Test-quality lens**: tests that cannot fail, magic numbers not derived from inputs, missing non-vacuity twins.

### P6.2 Fix loop
Fix the confirmed findings; each fix gets a test; re-run the whole suite. Findings the implementer disagrees with go to the user with evidence.

### P6.3 Final gate
Full suite (all tiers except `live_arbs`) green; parity green; guards green; mutation spot checks (§0.6) recorded for at least: the MR short-close comparison, DEV-R1 zeroing, the FX conversion direction, the `csa` cache key, and the `resolve` pinning; `docs/v2/DEVIATIONS.md` complete; commit `v2: phase 6 — review fixes`.

---

## 9. Stop and ask the user (do not proceed on your own)

- Before **any** execution that imports ARBS (P5.2), even read-only.
- Before deleting `data/fixtures` from disk (it is git-ignored third-party data).
- If a MUST cannot be met as written (e.g. a gs behaviour that cannot be reproduced without an asset-class special case).
- If DESIGN.md and this plan conflict, or a research note contradicts DESIGN.md on a fact (quote both).
- If a gs behaviour not covered by §11 looks like a bug worth fixing (propose a DEV row; do not implement it).
- If the parity test needs a new exception that is not explained by a DEV row or by the `asset`/`quantity`/`**kwargs` extension.
- If a test needs to be skipped, xfailed, deleted or loosened.
- Before any `git push`, merge, or history rewrite.

---

## 10. Final deliverables checklist

- [ ] `src/pricebt` per DESIGN §3.2; guards green; no forbidden imports or tokens.
- [ ] `pricebt.backtests` ported from 2.1.17 with DEV markers; parity test green against the 1.5.4 snapshot.
- [ ] Asset config loader + PricingService + FX + contexts; the toy configs in `tests/assets`.
- [ ] All DESIGN §12.4 tests; mutation checks recorded.
- [ ] `notebooks/040304_mean_reversion_toy.ipynb` executes in CI; the ARBS notebook is built, executed only with the user's approval.
- [ ] `configs/assets/usd_sofr_ois_interest_rate_swap.yaml`, `docs/v2/ASSET_CONFIG_GUIDE.md`, `docs/v2/DEVIATIONS.md`, `README.md`, `NOTICE`, `AGENTS.md`.
- [ ] One commit per phase on `v2-redesign`; nothing pushed.
