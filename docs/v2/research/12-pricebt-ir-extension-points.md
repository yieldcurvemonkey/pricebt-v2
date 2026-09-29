# 12 — pricebt internals: extension points for the IR pricing-and-risk port

> **Key facts**
> 1. **Baseline suite (branch `v2-ir-risk`, HEAD `a4535bd`):** `1187 passed, 7 skipped, 34 warnings in 69.83s` (pytest), 73.2 s wall clock, exit 0. The 7 skips are the 7 `live_arbs` tests in `tests/test_live_arbs.py` (`PRICEBT_LIVE_ARBS` unset). No failures.
> 2. **Every value comes from one method.** `PricingService.value` (`src/pricebt/assets/pricing.py:390-457`) maps a measure to a config function by **name** (`risk.name`, then `risk.base_name`), with no measure names hard-coded. A new gs measure needs **no pricing-layer change** to be mappable. It needs only an instance in `src/pricebt/risk/__init__.py` plus a config entry. The work is in the parity snapshot, the result forms, and the parameters.
> 3. **Three hard gates for a big measure catalogue:**
>    - `tests/test_gs_api_parity.py:393-399` (`test_no_unexpected_extra_risk_measures`) fails on any `pricebt.risk.__all__` measure that is absent from the 1.5.4 snapshot. So the snapshot's `RISK_MEASURE_NAMES` (`tools/gs_api_snapshot.py:117-124`) must be extended and regenerated with the **base** python.
>    - The snapshot records only `class`/`name`/`measure_type` (`tools/gs_api_snapshot.py:186-189`). A wrong `asset_class` or `unit` on a new measure is therefore invisible to the parity test.
>    - Every gs 2.1.17 measure instance is one of **three** classes: 117 in `target/measures.py` = 95 plain + 13 Currency + 9 FiniteDifference, plus the 7 presets in `risk/measures.py:79-85`. The Double/String/Map/ListOf* parameter classes (`target/measures.py:48-160`) are defined but used by **no** instance, so pricebt need not port them.
> 4. **FD pass-through today:** `_check_no_extra_parameters` (`pricing.py:356-367`, marker DEV-I8) raises `NotSupportedError` for any non-None FD field except `aggregation_level` and `currency`. Passing `bump_size`, `finite_difference_method`, `local_curve` and `scale_factor` through needs four things:
>    - (a) new injected names in two injection sites (`pricing.py:231-241` and `:279-286`);
>    - (b) the frozen parameters added to **four** keys: the unit-value cache `:224`, the portfolio-value cache `:273`, the LazyFuture `group_key` `:463` and `member` `:471`. Otherwise `IRDelta(bump_size=1)` and `IRDelta(bump_size=10)` share one cached value and one group call;
>    - (c) `mkt_marking_options` stays unsupported (it is GS server-side);
>    - (d) gs types `finite_difference_method` as the enum `FiniteDifferenceMethod` (gs `target/common.py:4004-4011`, `:5794`), while pricebt stores a bare `str` (`risk/__init__.py:84`).
> 5. **A bare FD measure always means "bucketed":** `pricing.py:409-417`. `IRVega` on the toy swaption raises `ConfigError: ... IRVega has no bucketed mapping` (probe 1). This applies to the whole class: IRVanna, IRVolga, IRBasis, IRXccyDelta, InflationDelta, FXDelta and FXVega all inherit it. A contract that requires "IRVega" for IRSwaption therefore requires a **bucketed** vega function, unless rule 5 gains a scalar-to-one-bucket fallback. No existing test evaluates IRVega in any form on the toy swaption.
> 6. **An unmapped measure kills a mixed book eagerly.** `PricingService.value` raises `ConfigError` at `pricing.py:402-403` as soon as any held instrument's asset lacks a mapping (probes 3 and 6). The engine prices **every** measure in `risks` for **every** held instrument at every calc site. A swap + swaption book with `risks=[IRVegaParallel]` cannot run today. Per-class contracts must define what "not applicable" means (see §2.2).
> 7. **Only two result forms exist: a scalar float and a `{mkt_point: float}` bucket dict** (`config.py:24`, `pricing.py:288`, `results.py:116-139`). A Cashflows-like frame breaks four consumers:
>    - `combine_bucketed_frames`: `KeyError: 'mkt_type'`;
>    - `PortfolioRiskResult.aggregate`: `KeyError`;
>    - `to_frame`: `TypeError`;
>    - `get_risk_summary_df` catches only `TypeError` (`backtest_objects.py:255-258`), so the KeyError crashes `result_summary`.
>
>    gs aggregates **any** frame by grouping on every non-`value` column and summing (`gs risk/core.py:556-558`). That is the parity target that would generalise pricebt's hard-coded 5-column groupby (`results.py:152`).
> 8. **The asset-agnostic guard** (`tests/guards/scan.py:133-159`) scans `assets/**`, `markets/**`, `risk/results.py` and `risk/transform.py`: names, strings and docstrings, but **not comments**. A literal `'expiry;tenor'`, a Cashflows column named `notional`, or `strike` in any of those files fails the build (probe table §3.1). `IRSwap`/`IRSwaption` pass the regex (a letter before `S`), but DESIGN §13 (`DESIGN.md:1052`) forbids class awareness there anyway. A per-class contract table belongs in `instrument/` or `risk/__init__.py`, which the guard does not scan.
> 9. **Adding `Bond` is a data change:**
>    - the class is already in `tests/data/gs_instruments_1_5_4.json`, with fields identical in 1.5.4 and 2.1.17;
>    - append it to `CLASS_LIST` (`tools/gen_gs_fields.py:41`), regenerate `_gs_fields.py`, and add `"Bond"` to `instrument/__init__.py:365-383 __all__`;
>    - add it to `INSTRUMENT_CLASS_NAMES` (`tools/gs_api_snapshot.py:113-114`) so the parity test checks its signature.
>
>    Bond has **no** `notional_amount`, `termination_date` or `expiration_date` field. The engine's default `ScaledTransactionModel(scaling_type='notional_amount')` (`backtest_objects.py:668,673-677`) and duration strings (`backtest_utils.py:101-103`) therefore need Bond config `attributes:`.
> 10. **`pnl_explain` is a verbatim gs port** (`backtest_objects.py:404-451` = gs `backtest_objects.py:344-391`) and has four constraints:
>     - it works on **scalars only** (`prev_date_risk == 0` at `:429`);
>     - it **cannot combine with `result_ccy`**: every gs market-data metric is a plain `RiskMeasure`, so the rewrite at `generic_engine.py:427-434` raises `Unparameterised risk`;
>     - a missing exit date gives `TypeError`, not `KeyError`, because `trade_exit_risk_results` is a `defaultdict(list)` (`:130`);
>     - gs `scaling_factor` values do not port, because the market-data unit is whatever the config declares (DEV-I7).
>
>     No test runs `pnl_explain` through `GenericEngine`. The only test, `tests/test_result_shapes.py:300-325`, builds its BackTest by hand.

Scope: pricebt's *internal* seams, not gs semantics. Read in full:

- `src/pricebt/assets/{pricing,config,registry,namespace}.py`;
- `src/pricebt/risk/{__init__,results,transform}.py`;
- `src/pricebt/common.py`;
- `src/pricebt/instrument/__init__.py` (+ `_gs_fields.py`);
- `src/pricebt/markets/portfolio.py`;
- `src/pricebt/backtests/backtest_objects.py`, `generic_engine.py`;
- `src/pricebt/data/__init__.py`;
- every file in `tests/guards/`, `tests/conftest.py`, `tests/toylib/*`, `tests/assets/*`;
- `configs/assets/*.yaml`;
- `docs/v2/{ASSET_CONFIG_GUIDE,DEVIATIONS}.md`, the relevant parts of `DESIGN.md` and `DECISIONS_LOG.md`.

gs references: 2.1.17 source at `C:\Users\chris\clee\gsquant-temp-claude\gs-quant\gs_quant` (written "gs2" below) and 1.5.4 at `C:\Users\chris\anaconda3\Lib\site-packages\gs_quant` ("gs1"). Every "probe N" is reproduced in the Appendix (a scratch script outside the repo, run against the toy configs).

---

## 0. Baseline test suite

Command, run from `C:\Users\chris\clee\gsquant-temp-claude\pricebt-ir` (PowerShell, `PRICEBT_LIVE_ARBS` explicitly removed):

```
$env:PYTHONPATH="src;tests"; C:\Users\chris\anaconda3\envs\stir\python.exe -m pytest tests -o addopts= -p no:cacheprovider -q
```

Results:

- `1187 passed, 7 skipped, 34 warnings in 69.83s (0:01:09)`; Stopwatch wall clock 73.16 s; exit code 0.
- The 7 skips: `tests/test_live_arbs.py` has exactly 7 `def test_`, all marker `live_arbs` (`tests/conftest.py:30-32`).

The warnings are pre-existing:

- `FutureWarning` at `backtest_objects.py:308` (`.ffill().fillna(0)` downcasting object dtype). Bucketed/object cells make it worse.
- `FutureWarning` at `backtest_objects.py:370` (concat of empty frames).
- `RuntimeWarning` divide-by-zero at `triggers.py:416`.
- the missing-market drop `UserWarning` from a skills test.

The suite also includes `tests/skills/**` (agent-skills tests added in commit `c442ce5`), which the task text does not list. They exercise `skills/pricebt-verify-asset-config/scripts/check_asset.py`, which has its own assumptions about units and measure forms (§4.4).

---

## 1. How a risk measure is computed today (the call chain)

| Step | Where | What it does |
|---|---|---|
| user/engine | `Portfolio.calc` `markets/portfolio.py:239-240`; `Instrument.calc` `instrument/__init__.py:276-279` | → seam `pricebt.markets._engine_calc` |
| seam | `assets/pricing.py:588-614` `engine_calc` | historical → `_historical_portfolio_result` `:569-576` / `_historical_instrument_value` `:535-566` (**eager**: `_unwrap` LazyFutures `:531-532`); single date → `_calc_portfolio_one_date` `:521-528` |
| per-child future | `_instrument_future` `:515-518` → `_single_measure_future` `:510-512` | one `PricingFuture` per (instrument, measure); several measures → `_MultiMeasureFuture` (`results.py:234-254`) |
| measure→function | `PricingService.value` `:390-457` | step 1 `ResolvedInstrumentValues` → `resolve` (`:392-393`); step 2 `DollarPrice` → `Price(currency="USD")` (`:395-396`); name lookup then `base_name` (`:399-403`, else **ConfigError**); `_check_no_extra_parameters` (`:404`, `:356-367`); form choice (`:406-433`); spec + currency (`:435-444`); resolve (`:446`) |
| form choice | `:409-433` | `has_agg_level = isinstance(risk, RiskMeasureWithFiniteDifferenceParameter)`; `want_bucketed = has_agg_level and agg in (None, Point)`. Bucketed wanted but no `bucketed` mapping → `ConfigError` (`:416-417`). Scalar wanted but only bucketed mapped: an FD measure **sums** the buckets (`:424-428`); a plain/currency measure **selects** the bucketed form (`:429-433`). |
| scalar value | `_eval_unit_cached` `:218-244` → `_scale_scalar` `:369-378` → `FloatWithInfo(val, risk_key, unit=_unit_dict)` `:452-457` | per unit trade (quantity 1); × `quantity_` iff `spec.scale_with_quantity`; × fx iff target ccy ≠ function ccy |
| bucketed value | `_lazy_value` `:459-482` → `LazyFuture(thunk, group_key, member, service)` | `group_key = (asset, market_key, d, csa, fname, target_ccy)` `:463`; `member = (identity, frozen_resolved, quantity_, res_date, res_csa, risk)` `:471` |
| aggregation | `PortfolioRiskResult.aggregate` `results.py:471-488` | all-LazyFuture leaves → `service.group_aggregate` per group (`pricing.py:484-496`) → `_portfolio_value_from_entries` `:255-290` (weights = quantities) → `make_bucketed_frame` → `combine_bucketed_frames` (`results.py:142-153`); else scalars `_aggregate_scalars` (`:266-278`) or frames `combine_bucketed_frames` (`:486-487`) |
| units | `_unit_dict` `pricing.py:52-60`; `_CONVERTIBLE_UNITS` `:47`; `UNITS`/`EXTENSIVE_UNITS` `config.py:22-23` | `ccy*` → `{CCY: 1}`; `number` → `{}`; others → `{unit: 1}` |

**Engine calc sites.** Every measure in `risks` is computed for every held instrument at each of these:

- `generic_engine.py:604` (`_price_semi_det_triggers`);
- `:614` and `:622` (hedge and weighted-trade `HistoricalPricingContext`);
- `:642` (`__ensure_risk_results`);
- `:708` and `:773` (lazy hedge/weighted);
- `:875` (`_calc_new_trades`);
- `:918` (exit risks, `calc_risk_at_trade_exits`);
- `:948` (DEV-R1 continuing positions on non-grid dates).

Cash uses only `price_risk` (`:915`).

**How `risks` is built:**

- `risks = list(dict.fromkeys([*user risks, *strategy.risks, *pnl_risks, price_measure]))` (`generic_engine.py:420`).
- `pnl_explain` forces `calc_risk_at_trade_exits = True` and appends `PnlDefinition.get_risks()` (`:413-417`).
- With `result_ccy`, every risk becomes `r(currency=result_ccy)`, and a non-`ParameterisedRiskMeasure` raises `RuntimeError("Unparameterised risk: …")` (`:421-434`). The price measure gets the same rewrite (`:436-442`).
- The rewrite keeps every other parameter, because `__call__` inherits unset arguments (`risk/__init__.py:186-200`).

**Per-date storage.** `backtest.add_results(d, prr)` merges with `PortfolioRiskResult.__add__` (`backtest_objects.py:196-200`, `results.py:418-445`). `get_risk_summary_df` computes `results[risk].aggregate(True, True)` per date (`backtest_objects.py:254-259`), catches **only `TypeError`**, and reindexes the columns to `self.risks` (`:278`).

---

## 2. Extension-point map

### 2.1 (1) A large catalogue of new gs risk measures

| What | Where (file:line) | Notes / constraint |
|---|---|---|
| Measure instances | `src/pricebt/risk/__init__.py:229-262` (instances), `:20-63` (`__all__`) | Add `X = <Class>(name=..., asset_class=AssetClass..., measure_type=..., unit=RiskMeasureUnit...)`. Presets use `Base(aggregation_level=..., name='XParallel')`, which sets `base_name` (`:142-146`), so a preset falls back to its parent's config mapping (`pricing.py:400-401`). **Not scanned** by the asset-agnostic guard (`tests/guards/scan.py:152-159`). |
| Measure classes | `risk/__init__.py:110-134` `RiskMeasure`; `:137-146` `ParameterisedRiskMeasure`; `:149-159` Currency; `:162-210` FD; `:213-220` repr | The three classes cover every gs2 instance (95 plain / 13 Currency / 9 FD, counted from `gs2 target/measures.py`). Currency instances: Annuity, EqDelta, EqGamma, EqTheta, EqVega, FXDeltaHedgeLocalCcy, FXDeltaLocalCcy, FXGammaLocalCcy, FXThetaLocalCcy, FXVegaLocalCcy, FairPremium, Price, PricePips. FD instances: FXDelta, FXVega, IRBasis, IRDelta, IRVanna, IRVega, IRVolga, IRXccyDelta, InflationDelta. |
| gs presets not yet in pricebt | gs2 `risk/measures.py:79-85` | `IRBasisParallel` (Asset), `InflationDeltaParallel` (**Type**), `IRXccyDeltaParallel` (**Type**). pricebt already has IRDeltaParallel, IRDeltaLocalCcy, IRVegaParallel and IRVegaLocalCcy. |
| IR-relevant gs2 measures absent from pricebt (all plain unless marked) | gs2 `target/measures.py` (e.g. `:451` IRAnnualATMImpliedVol, `:466/469` IRDiscountDeltaParallel(+LocalCcy), `:481` IRGammaParallelLocalCcy, `:487/493` IRVanna/IRVolga (**FD**), `:520/523/526` Market/MarketData/MarketDataAssets, `:538` ParSpread, `:568` Theta) | Also BaseCPI, CRIFIRCurve, CompoundedFixedRate, CrossMultiplier, Description, ExpiryInYears, FairPremium (**Currency**), ForwardPrice (unit BPS), InflDeltaParallelLocalCcyInBps, InflMaturityCPI, Infl_CompPeriod, LightningDV01, LightningOAS, LocalAnnuityInCents, PremiumCents, ProbabilityOfExercise. gs has **no** bond yield/clean/dirty/duration measure (grep of gs2 `target/measures.py`). |
| 1.5.4 vs 2.1.17 | gs1 has 111 instances; gs2 has 117 | Only in 2.1.17: EqForwardSpot, FXDeltaHedgeLocalCcy, FXDeltaLocalCcy, FXGammaLocalCcy, FXThetaLocalCcy, FXVegaLocalCcy. None is IR. |
| Pricing layer | `assets/pricing.py` | **No change needed** to map a new name: the lookup is generic (`:399-403`). `config.py` accepts any `risk_measures:` key (`:325-328`); it only validates the target *functions* (`:181-218`). |
| gs import-path shims | `src/pricebt/target/measures.py` (`from pricebt.risk import *`) | Picks up new names automatically via `__all__`. |
| **Parity gate** | `tests/test_gs_api_parity.py:393-399` | `actual - scope == set()`, where `scope` = snapshot `gs_quant.risk.*` ∪ `requested_not_found`. **Any new instance in `__all__` fails** until the snapshot lists it. |
| Snapshot tool | `tools/gs_api_snapshot.py:113-124` (`INSTRUMENT_CLASS_NAMES`, `RISK_MEASURE_NAMES`), `:213-222`, `:272-282` | Run with **base** python (gs 1.5.4, DESIGN.md:227). It rewrites **both** `tests/data/gs_api_1_5_4.json` and `tests/data/gs_instruments_1_5_4.json`. The risk descriptor is only `{class, name, measure_type}` (`:186-189`): `asset_class` and `unit` are unchecked, so add a pricebt-side table test. |
| Snapshot hygiene | `tests/test_gen_gs_fields.py:38-48` (no `at 0x…` reprs); `test_gs_api_parity.py:455-464` (every exception row must still be consulted) | A regenerated snapshot must not make an existing `tests/data/gs_api_exceptions.yaml` row unused. `DECISIONS_LOG.md` (2026-09-28 Phase 4 gate) handled the date-default drift with `ignore_default: [dates]`, so regenerating is safe from that. |
| Tests listing measures | `tests/test_risk_measures.py:22-61` (imports), `:163-201` (class-membership parametrize), `:204-210` (units) | These are hand lists, not exhaustive. New measures don't break them, but they should be added. |
| `measure_type` | pricebt: plain `str` (`risk/__init__.py:113`); gs: `RiskMeasureType` enum; `repr` fallback `self.name or self.measure_type` (`:125`) vs gs `self.measure_type.name` (gs2 `common.py:76-77`) | Parity compares `str(measure_type)` only. `gs_quant.common.RiskMeasureType` is in the snapshot, if ever needed. |
| gs relative measures | gs2 `risk/measures.py:26-75` (`PnlExplain`, `PnlExplainClose/Live`, `PnlPredictLive`, which take a `to_market`) | These re-price under a `RelativeMarket` (GS server-side). They have no pricebt analogue: pricebt has no market objects. |

### 2.2 (2) Per-instrument-class measure contracts enforced at config load

**Hook points:**

| Candidate | Where | Pros / cons |
|---|---|---|
| In `load_asset`, after `risk_measures` is parsed | `assets/config.py:325-328` (right after the only "required" rule today, `Price`, at `:326-327`) | Fails at load with `ConfigError(asset, key)`. `load_asset` is also used standalone by 9 tests with **`instrument: ConfigInstrument`** and made-up measure names (`test_asset_config.py:20-31` `_minimal`; `:64-75` "SomeDelta"; `:102-120`; `:171-174` "Other"; `:215-263` "SomeMeasure"; `test_pricing_service.py:124-137` "IRGamma"/"IRDelta" on ConfigInstrument). **The contract must exempt `ConfigInstrument`.** |
| `AssetRegistry._register` | `assets/registry.py:74-88` | Runs for every source form (path, dict, `AssetConfig`: `:39-48`). An `AssetConfig` built directly bypasses `load_asset`, so this is the only hook that catches it. |
| `PricebtSession.__init__` | `session.py:51-53` | Too late and too wide. |

**Where the class → measures table lives:**

- Not in `assets/**`. The asset-agnostic regex passes `IRSwap`/`IRSwaption` (probe table) but fails on field vocabulary (`notional`, `strike`, `tenor`, …), and `DESIGN.md:1052` says "A design review MUST fail any change that makes the engine, the pricing layer or the results aware of an asset class."
- Candidates:
  - (a) a hand-written table or class attribute in `instrument/__init__.py`, e.g. set in `_build_class` `:335-346`. Never in `_gs_fields.py`: it is GENERATED, and `tests/test_gen_gs_fields.py:51-56` asserts it is byte-identical to the generator output;
  - (b) `risk/__init__.py`.
- `assets/config.py` may import `pricebt.instrument` at top level: DAG tier 8 < tier 10 (`tests/guards/dag.py:86-123`). The import closure (`instrument` → `base`, `common`, `errors`, `_gs_fields`, `risk`, `risk.results`) is all earlier tiers, so `test_import_order.py:34-43` stays green.
- Bonus: `config.py:250` only checks that `instrument:` is a non-empty string. A typo (`IRSwapp`) loads fine and then never matches (`registry.py:114`). A contract lookup by class name would finally reject it.

**What a contract must decide.** These are design gaps, with evidence:

1. **"Required IRVega" implies a bucketed function.** A bare FD measure is bucketed (`pricing.py:409-417`), so `IRVega` with `{scalar: vega}` only raises ConfigError (probe 1), while `IRVegaParallel` works (probe 2). The rule holds for every FD measure. Either the contract requires `bucketed:` for FD measures, or rule 5 (`DESIGN.md:655-660`) gets a new fallback (scalar → a one-row frame). The fallback is a DEV.
2. **Not-applicable semantics.** An unmapped measure raises **eagerly** in `value` (`pricing.py:402-403`); probes 3 and 6 show a mixed swap + swaption `Portfolio.calc((Price, IRVegaParallel))` failing on the swap. Choices:
   - (a) every config maps every catalogued measure, returning zero itself;
   - (b) `value` synthesises a typed zero.

   A synthesised zero needs a **form** (FloatWithInfo vs an empty/zero bucket frame) and a **unit** (`_unit_dict`). `PortfolioRiskResult.aggregate` chooses its path from `values[0]`'s type (`results.py:483-488`), and only `TypeError` is caught upstream (`backtest_objects.py:255-258`). What happens to a mixed float + frame column therefore depends on order (probe 10):
   - **float first** → `TypeError` → caught → an `ErrorValue` cell;
   - **frame first** → `ValueError: DataFrame constructor not properly called!` → **uncaught**, and `result_summary` crashes.

   Either way, the zero must match the column's form.
3. **Form and unit checks.** Today nothing ties a measure to a unit family: `IRDelta: npv` loads fine. A contract can check the form (scalar/bucketed) and the unit (e.g. IRDelta → `ccy_per_bp`, gamma → `ccy_per_bp2`, implied vol → an intensive unit).
4. **Existing error-key assertions** will move if validation order changes: `test_asset_config.py:108-119` asserts `err.key == "risk_measures.Other.scalar"` / `".bucketed"`, and `:171-174` asserts `"risk_measures.Other"`.

### 2.3 (3) Passing through `bump_size`, `finite_difference_method`, `local_curve`, `scale_factor`

**Where it is blocked now:**

- `assets/pricing.py:356-367` `_check_no_extra_parameters`: skips `parameter_type`, `aggregation_level` and the currency field (`value` on Currency measures, `currency` on FD measures, `:352-354`), and raises `NotSupportedError(f"asset {name}: {risk!r} sets {f.name}; pricebt passes only aggregation_level and currency to asset configs")`.
- Probe 4: `IRDelta(bump_size=1)`. Probe 5: `IRDelta(aggregation_level="Type", local_curve=True)`.

**Docs and tests that encode DEV-I8:**

- `DESIGN.md:653` (rule 3a) and `:934` (§11 row);
- `DEVIATIONS.md:76`;
- `tests/test_pricing_service.py:110-114` (`test_irdelta_bump_size_raises_not_supported`);
- `skills/pricebt-asset-config-cookbook/references/error-catalogue.md:45`;
- `skills/pricebt-port-gs-notebook/SKILL.md:22`.

`DEV-I8` is **not** cited in `tests/data/gs_api_exceptions.yaml` (grep), so `test_gs_api_parity.py:438-442` (the dev_id must be a DESIGN §11 row) does not constrain renaming it. **Narrow DEV-I8, don't retire it:** `mkt_marking_options` (gs `MktMarkingOptions(mode)`, gs2 `target/common.py:5222-5224`) is server-only and should keep raising.

**Parameter object.** `FiniteDifferenceParameter` (`risk/__init__.py:76-87`) has the same field order as gs (gs2 `target/common.py:5789-5798`), but:

- gs `finite_difference_method: Optional[FiniteDifferenceMethod]` is an enum with Up/Centered/Down/CenteredSecondOrder (gs2 `target/common.py:4004-4011`); pricebt's is `Optional[str]` with no coercion.
- To add the enum:
  - add `FiniteDifferenceMethod` to `common.py`. `gs_quant.common.FiniteDifferenceMethod` is already in the snapshot, so `test_common_enum_parity` (`test_gs_api_parity.py:402-407`) checks its members;
  - **also** add the name to `tools/gen_gs_fields.py:45-52 PRICEBT_ENUM_NAMES`. `test_enum_literal_list_matches_pricebt_common` (`test_gen_gs_fields.py:77-107`) asserts set equality with pricebt.common's enums. The generated `_gs_fields.py` does not change, because no mirrored field is tagged with it;
  - coerce in `RiskMeasureWithFiniteDifferenceParameter.__call__` (`risk/__init__.py:171-210`), the way `aggregation_level` already is (`:184-185`).

**Injection points.** `value()` must thread the parameters through:

| Site | Line | Current injected keys | Note |
|---|---|---|---|
| `_eval_unit_cached` (functions and single-trade portfolio functions) | `pricing.py:231-241` | `pricebt_*`, `market`, `trade`, `resolved` (+`kwargs`), or `trades`/`weights` | Shared with `unit_value` (`:246-251`, used by `measure_series` `data/__init__.py:101` and the skills checker), which has **no** measure. Always inject the new names (None when absent) so an expression that reads them never raises `NameError`. |
| `_portfolio_value_from_entries` (group and portfolio calls) | `pricing.py:279-286` | same, with `trades`/`weights` | Shared with the public `portfolio_value` (`:292-298`, no measure). |
| `attribute` | `:321-327` | no measure; unaffected | |
| `_trade_for` | `:186-214` | trade objects are measure-independent | Keep the parameters **out** of the trade key, unless a config rebuilds trades per bump. |

**Cache and group keys that must include the frozen parameters** (all currently parameter-blind):

- unit-value key `(asset, frozen, d, function, csa[, res_date, res_csa])`: `pricing.py:224-226`;
- portfolio-value key `(asset, d, function, csa, entries)`: `:273`;
- `group_key = (asset, market_key, d, csa, fname, target_ccy)`: `:463`. `PortfolioRiskResult.aggregate` groups by it (`results.py:477-480`);
- `member[5]` is the risk; `group_aggregate` reads `members[0][5]` (`pricing.py:494`). If two bump sizes land in one group, the whole group is evaluated with the first member's parameters and no error is raised.

**Naming.** Injected names **shadow** config names (`DESIGN.md:331`; `ASSET_CONFIG_GUIDE.md:73-76`), so use a `pricebt_` prefix (e.g. one `pricebt_measure_params` dict, or `pricebt_bump_size`, …). Update the injected-variable tables at `DESIGN.md:313-331` and `ASSET_CONFIG_GUIDE.md:53-71`.

**Asset-agnostic regex.** `bump_size`, `finite_difference_method`, `local_curve` and `scale_factor` all pass it (probe table §3.1).

**Semantics pricebt must decide.** These are not given by the code:

- does `scale_factor` get applied generically by pricebt as a pure multiplier, or passed to the config?
- does `local_curve=True` interact with pricebt's currency handling (`target_ccy`, `pricing.py:440-444`)?

**Repr and column identity.** `_params_repr` (`risk/__init__.py:90-106`) already prints every non-None field. `IRDelta(bump_size:1)` and `IRDelta(bump_size:10)` are distinct `result_summary` columns, and equality and hash include the parameters (frozen dataclass, `risk/__init__.py:110-122`).

### 2.4 (4) New result forms: Cashflows (DataFrame), vega cube `expiry;tenor`, MarketData

**Today's closed set:**

- `functions:` entries return a float (`config.py:37` has no `returns` key; `float(raw)` at `pricing.py:251/370`).
- `portfolio_functions:` return a float or a bucket dict (`config.py:24` `RETURNS_VALUES = {"scalar","buckets"}`; `pricing.py:288`).
- A bucket dict becomes a **6-column** `DataFrameWithInfo`: `mkt_type, mkt_asset, mkt_class, mkt_point, mkt_quoting_style, value` (`results.py:23`, `:116-139`; docstring "Always has exactly the six columns", `:88-89`).
- Labels are **static per function** (`config.py:174-177` `LABEL_KEYS`; `results.py:126-136`).

| Form | gs shape (gs2) | pricebt gap | Extension points |
|---|---|---|---|
| **Cashflows** | `cashflows_handler` (`risk/result_handlers.py:83-103`): 14 columns in this order: `currency, payment_date, set_date, accrual_start_date, accrual_end_date, payment_amount, notional, payment_type, floating_rate_option, floating_rate_designated_maturity, day_count_fraction, spread, rate, discount_factor`; the 4 date columns are parsed; rows unsorted (`__dataframe_handler_unsorted` `:67-80`). Cashflows is a **plain** `RiskMeasure` (`target/measures.py:283`). | Nothing returns a frame. The probes show that a hand-made cashflows frame (probe 8) breaks `combine_bucketed_frames` with `KeyError: 'mkt_type'` (`results.py:152`) and `PRR.aggregate` with the same KeyError (`results.py:486-487`); `PRR.to_frame` raises `TypeError` (`results.py:498-499`, `val.sum().sum()` over dates). `ResultWithInfoAggregator` needs a `value` column (`transform.py:42`). `get_risk_summary_df` catches only TypeError (`backtest_objects.py:255-258`), so the KeyError crashes `result_summary`, and `strategy_as_time_series` calls `to_frame` (`:376-381`). | a new return kind (e.g. `returns: table` on functions/portfolio functions): `config.py:22-39` (constants), `:47-56` (`FunctionSpec`), `:153-178` (parser), `:181-218` (`_risk_target_kind`, `_parse_risk_measure`, whose "scalar"/"bucketed" slot logic assumes two kinds), `:59-66` (`RiskMapping`); `pricing.py:406-433` (form choice), `:288` (coercion), `:369-388` (**scaling must be column-aware**: `_scale_bucket` multiplies every value, while a cashflows frame scales `payment_amount`/`notional` only; rate, discount_factor and dates must not scale); `results.py` (a frame builder, and `combine_*`/`aggregate`/`to_frame` made generic). **The column name `notional` is banned** in `risk/results.py` and `assets/**` (probe table). Keep gs's column list in `risk/__init__.py`, or have the config supply it. |
| **Vega cube `expiry;tenor`** | vector results: `risk_vector_handler` (`result_handlers.py:207-226`), same 6 columns; `mdapi_table_handler` joins a list point with `';'` (`:358-360`) | Works **today** if the config returns `';'`-joined **string** keys (`DESIGN.md:283-285`; tested `test_risk_results.py:103-105` with `"1y;10y"`). A **tuple** key is `str()`'d to `"('1y', '10y')"` (`results.py:132`; probe 7). Labels are static per function, so a cube whose `mkt_asset`/`mkt_class` varies per row needs per-row coordinates. DESIGN lists "Multi-curve ladder labels, i.e. a per-row coordinate override" as later work (`DESIGN.md:1060`). IRVega bucketed needs a `returns: buckets` portfolio function (`config.py:215-217`). | `make_bucketed_frame` `results.py:116-139`: `';'.join` tuple keys (asset-agnostic), and/or accept per-row coordinate dicts. **Never write the literal `'expiry;tenor'` or `tenor`** in `results.py` or `assets/**`, docstrings included (probe table). Group aggregation stays exact because it is linear in weights (`DESIGN.md:722-726`). |
| **2nd-order vectors** (IRGamma bucketed / cross gamma) | `mdapi_second_order_table_handler` (`result_handlers.py:321-352`): 12 columns, `inner_*`/`outer_*` coordinates, `value`, `permissions`; a single-row parallel gamma collapses to a float (`:312-318`, `:324-325`) | Only a 6-column frame is possible, so a 2-D gamma would be flattened to a joined point. That is a DEV. | same as Cashflows (a new frame kind) |
| **MarketData** | `MarketData` is plain (`target/measures.py:523`); `mdapi_table_handler` (`result_handlers.py:355-377`) gives **7** columns (6 + `permissions`), sorted by `__dataframe_handler` (`:41-64`). `Market` → `StringWithInfo` (`:482-485`). | No per-row `mkt_type`/`mkt_asset`/`mkt_class` (static labels). `combine_bucketed_frames` silently drops extra columns such as `permissions` (probe 9). Market-data values are intensive, so the function must declare an intensive unit or `scale_with_quantity: false`, or `weights` would scale it (`pricing.py:267-277`). pricebt has no `StringWithInfo` for `Market`. | per-row coordinates in `make_bucketed_frame`; an optional pass-through of extra columns. gs parity for aggregation = group by every non-`value` column and sum (`gs2 risk/core.py:556-558` `aggregate_risk`): generalise `combine_bucketed_frames`' hard-coded `_BUCKET_COLUMNS` groupby (`results.py:152`). Note that gs's sort (`sort_risk`, `core.py:663`) differs from DEV-R5's order. |

**Other consumers to keep in mind for any new frame kind:**

- `measure_series` is scalar only: a `LazyFuture` gives `NotSupportedError` (`data/__init__.py:104-108`), and a frame would fail at `float(value)` (`:111`).
- `pnl_bps` rejects DataFrame cells (`backtest_objects.py:484-485`).
- The hedge path does `.aggregate()` and then `hedge_risk == 0` (`generic_engine.py:726-732`), so a frame there raises "truth value is ambiguous"; `ResultWithInfoAggregator` needs a `value` column.
- The historical path is eager and builds an object `Series` of frames (`pricing.py:550-566`). `PortfolioRiskResult._series_item` returns a frame cell as is (`results.py:356-358`).

### 2.5 (5) New generated instrument class `Bond`

**gs `Bond`.** Identical in gs1 and gs2 (`target/instrument.py:81-90` in both):

- init fields `buy_sell: BuySell`, `identifier: str`, `identifier_type: UnderlierType`, `size: float`, `settlement_date: date|str`, `settlement_currency: Currency`, `name`;
- `asset_class = Cross Asset`, `type_ = Bond`;
- all defaults `None`, so the generator's enum-default and factory caveats (`tools/gen_gs_fields.py:16-24`) do not apply.

It is already present in `tests/data/gs_instruments_1_5_4.json` (checked: `'Bond' in dataclasses` → True, with the fields above). Every enum it needs exists in pricebt: `AssetClass.Cross_Asset` `common.py:180`, `AssetType.Bond` `:212`, `UnderlierType` `:127-136`, `BuySell` `:32-36`, `Currency` `:248-281`, and the `_ENUM_CLASSES` coerce map `instrument/__init__.py:48-64`.

| Step | File:line | Test that proves it |
|---|---|---|
| 1. Add `"Bond"` to `CLASS_LIST` | `tools/gen_gs_fields.py:41` | — |
| 2. Regenerate `_gs_fields.py` (stir python) | `src/pricebt/instrument/_gs_fields.py` (GENERATED; never hand-edit) | `tests/test_gen_gs_fields.py:51-56` byte-identical |
| 3. The class exists automatically | `instrument/__init__.py:349-351` (the loop over `GS_FIELDS`) | — |
| 4. Export it | `instrument/__init__.py:365-383` `__all__`, plus the module docstring `:1-3` | — |
| 5. Parity snapshot | `tools/gs_api_snapshot.py:113-114` `INSTRUMENT_CLASS_NAMES` += `"Bond"`, regenerated with base python | `test_gs_api_parity.py:380-383` + the exact-tail signature rule `:311-319` (`pricebt_asset`, `quantity_`, `**kwargs` after gs fields) |
| 6. Docs | `DESIGN.md:167-169` (§3.2 lists "the generated gs classes (IRSwap, IRSwaption, FXOption, FXForward, EqOption, InflationSwap, Cash)"), `DESIGN.md:982` ("the 7 generated instrument classes"), `tools/gs_api_snapshot.py:48` docstring | `test_instrument.py:302-307` (extension-name collisions; `size`, `identifier` are fine) |

**Engine attribute reads that a Bond config must satisfy through `attributes:`** (`Instrument.__getattr__` `instrument/__init__.py:161-182`):

- `ScaledTransactionModel` defaults to `scaling_type='notional_amount'` (`backtest_objects.py:668`). `getattr(bond, 'notional_amount')` then raises `RuntimeError('notional_amount not recognised for instrument Bond')` (`:673-677`), unless the config defines a `notional_amount` attribute or the user passes `scaling_type='size'`.
- `get_final_date` with a string duration does `hasattr`/`getattr` (`backtest_utils.py:101-103`). `AddTradeAction(bond, 'termination_date')` needs `attributes: {termination_date: …}`, the bond's maturity.
- `RebalanceAction` and `AddWeightedTradeAction` need `size_attribute` (`generic_engine.py:824-831`), e.g. `size`, linear and signed in quantity (`DESIGN.md:810`).

**Sign.** pricebt never reads `buy_sell`, so the Bond `resolve` must fold `buy_sell` × `sign(size)` into a signed resolved size, exactly as the swaption does (`DESIGN.md:1045-1048`; toy `tests/toylib/swaption.py:35-39`).

**Units for bond analytics:**

- duration in years has no unit; `number` is **extensive** by default (`config.py:23`, `:165`), so declare `scale_with_quantity: false`;
- price per 100 → `pct`; yield → `pct`/`bp`/`decimal` (intensive).

**Custom bond measures.** Since gs has no bond yield/price measures, a config can map a custom `RiskMeasure(name='BondYield')` by name (config keys are free-form, `config.py:325-328`). It must **not** be added to `pricebt.risk.__all__`, or `test_no_unexpected_extra_risk_measures` fails.

### 2.6 (6) P&L explain definitions for swaptions and bonds

**Code:**

- `PnlAttribute` (`backtest_objects.py:87-96`) and `PnlDefinition` (`:99-104`);
- `BackTest.pnl_explain` (`:404-451`), identical to gs2 `backtests/backtest_objects.py:344-391`;
- `fx_pnl_definition` (`:962-992`), the only predefined definition, ported verbatim;
- engine wiring `generic_engine.py:413-417` and `:907-918`.

**The formula, per attribute and per consecutive date pair `(prev, cur)`:**

- For each instrument in `results[prev].portfolio.all_instruments`, take `r = results[prev][inst][attribute_metric]`. If `r == 0`, skip it.
- Take `m_prev = results[prev][inst][market_data_metric]`. Take `m_cur` from `results[cur]` when the instrument is still there, else from `trade_exit_risk_results[cur]`.
- Add `scaling_factor * r * (m_cur - m_prev)`, or `0.5 * scaling_factor * r * (m_cur - m_prev)^2` when `second_order`, to a cumulative total.

Constraints the swaption and bond definitions must respect:

1. **Scalar attribute metric only.** `if prev_date_risk == 0` (`backtest_objects.py:429`) on a `DataFrameWithInfo` raises "truth value of a DataFrame is ambiguous". Use `IRDeltaParallel`, `IRVegaParallel` or `X(aggregation_level='Type')` (FloatWithInfo), never bare `IRDelta`/`IRVega`. The same applies to the market-data metric: a frame difference is not a number.
2. **Incompatible with `result_ccy`.**
   - All gs market-data metrics are plain `RiskMeasure`s (FXSpot, FXAnnualImpliedVol, IRFwdRate, IRAnnualImpliedVol, IRDailyImpliedVol, IRSpotRate), so the run-start rewrite raises `RuntimeError("Unparameterised risk: …")` (`generic_engine.py:427-434`).
   - Even with parameterised metrics, `pnl_explain` indexes results by the **un-rewritten** `attribute.attribute_metric`/`market_data_metric` (`backtest_objects.py:428,431,433,435`), while `results[d]` holds `r(currency=…)`. The result would be `KeyError`.
   - This is gs behaviour (verbatim port).
3. **A missing exit date gives `TypeError`, not `KeyError`.** `_trade_exit_risk_results = defaultdict(list)` (`backtest_objects.py:130`), so `exit_risk_results[cur][inst]` on a date with no exit calc gives `[][inst]`. Exit risks are computed only for `cp.direction == 1` payments not already in `results[cp.effective_date]` (`generic_engine.py:904-908`) and only for dates ≤ the strategy end (`:904`).
4. **`scaling_factor` depends on the config's units (DEV-I7).**
   - In gs, `IRFwdRate` is percent and implied vols are percent. `fx_pnl_definition`'s `VegaPnL` uses `scaling_factor=100.0` against `FXAnnualImpliedVol` (percent) (`backtest_objects.py:984-989`).
   - In pricebt the market-data unit is whatever the function declares: `par_rate` is `bp` in both toy configs and ARBS (`tests/assets/toy_usd_irs.yaml:29`; `configs/assets/usd_sofr_ois_interest_rate_swap.yaml:182`), and the toy vega is `ccy_per_bp` of normal vol (`tests/assets/toy_usd_swaption.yaml:27`, `tests/toylib/swaption.py:101-113`). So delta (ccy/bp) × Δpar (bp) needs `scaling_factor=1`.
   - A gs-shaped IR definition copied verbatim would be off by 100×.
5. **Second-order terms** need a gamma in ccy per bp² (unit `ccy_per_bp2` exists, `config.py:22`) paired with a bp market-data metric, for `0.5·Γ·Δ²` to be in currency.
6. **Market-data metrics are per instrument.** They are evaluated from each instrument's own asset config, e.g. a swaption's own implied vol, or a bond's own yield. That is generic and works today, but the function must be **intensive** (or `scale_with_quantity: false`); otherwise `m_cur - m_prev` scales with position size and the attribution is quadratic in quantity.
7. **Expiry and maturity.** The toy vega is 0 at `T <= 0` (`tests/toylib/swaption.py:107-109`), so an expiring swaption contributes nothing on its last step. A config whose risk functions return NaN after maturity (the ARBS config's `par_rate` does, `usd_sofr_ois_interest_rate_swap.yaml:182`) propagates NaN into the cumulative sum, because the `== 0` check does not catch NaN.
8. **No time or carry term.** The formula has only first- and second-order market moves. Theta, accrual and roll-down (important for bonds) need a market-data metric that advances with time. A config-only possibility, untested: a function returning a day count from `pricebt_date` (intensive, `scale_with_quantity: false`) paired with a `Theta`-mapped attribute in ccy per day.
9. **No end-to-end coverage.** `tests/test_result_shapes.py:300-325` builds a `BackTest` by hand. No test runs `pnl_explain` through `GenericEngine`, so `calc_risk_at_trade_exits` (`generic_engine.py:907-918`) is unexercised.
10. **Where new definitions go.** An `ir_pnl_definition()`/`swaption_pnl_definition()` helper would be a pricebt addition. It belongs in `backtests/backtest_objects.py` next to `fx_pnl_definition`: `backtests/` is exempt from the asset-agnostic guard (`scan.py:152-159`), but the token scan still applies.

---

## 3. Guards: what each one forbids

### 3.1 Asset-agnostic scan (`tests/guards/scan.py:133-159`, `test_asset_agnostic_scan.py:15-18`)

- **Scope:** `assets/**` (every file), `markets/**` (every file), `risk/results.py` and `risk/transform.py`. **Not** scanned: `instrument/`, `risk/__init__.py`, `backtests/`, `data/`, `session.py`, `common.py`, `target/`. The scope is verified by the twin at `tests/guards/test_twins.py:106-123`.
- **Tokens:** NAME, STRING and FSTRING_MIDDLE, **docstrings included**. **Comments are dropped** (`scan.py:81-95`).
- **Regex** (case-insensitive; letter look-arounds; `_` and digits are *not* letters):

```
(?<![a-z])(notional|tenor|swaption|swap|fixed_rate|termination_date|expiration_date|pay_or_receive|strike|dv01|pv01|par_rate|sofr|libor|estr)(?![a-z])
```

Probe results (`guards.scan.ASSET_AGNOSTIC_RE` run on each string):

| Fails | Passes |
|---|---|
| `Swaption`, `swap_rate`, `expiry;tenor`, `tenor`, `notional`, `strike`, `dv01`, `par_rate`, `fixed_rate`, `estr`/`ESTR`/`estr_ois`, `sofr_curve`, `USD-SOFR-1D` | `IRSwap`, `IRSwaption`, `LightningDV01`, `IRDeltaParallel`, `bump_size`, `finite_difference_method`, `local_curve`, `mkt_point`, `Cashflows`, `payment_amount`, `floating_rate_option`, `discount_factor`, `accrual_start_date`, `Bond`, `bond_yield`, `clean_price`, `yield`, `spread`, and plurals such as `swaps`, `strikes`, `tenors`, `fixed_rates`, `termination_dates` (the look-ahead forbids a trailing letter) |

### 3.2 Token (vendor) scan (`scan.py:102-129`, `test_token_scan.py:13-24`)

- Scope: all of `src/pricebt/**`.
- Banned anywhere, docstrings included: `arbs, rateslib, quantlib, irswapsmdp, mdp, rlirswapcurve, bulk_get_data, build_irswap, ignore_cache_miss, supabase, nojumps, eris, erisfutures, bloomberg, refinitiv, marquee`.
- `gs_quant` is allowed only in docstrings.
- Relevant here: do not name a data vendor (e.g. "Bloomberg" identifier types in a Bond docstring) in `src`. Error messages must say "GS server-side" (`DESIGN.md:605`).

### 3.3 Import scan and blocker

- **Import scan** (`scan.py:19-62`): allowed roots are stdlib ∪ `{numpy, pandas, yaml, dateutil, tqdm, pricebt}`.
- **Blocker** (`blocker.py:11`): blocks `gs_quant, rateslib, QuantLib, MDP, Query, Caching, dataclasses_json`.
  - `test_import_blocker.py:24-29` imports **every module in `dag.ALL_MODULE_NAMES`** under the blocker.
  - `:50-78` runs `notebooks/src/040304_mean_reversion_toy.py` end to end under it.

### 3.4 Skeleton and import DAG (`tests/guards/dag.py`)

- `SKELETON_PATHS` (`dag.py:19-66`) must equal the on-disk `src/pricebt/**/*.py` set (`test_skeleton.py:13-19`). **Any new source file** (e.g. `risk/measures.py`, `instrument/contracts.py`) must be added here, **and** to `DAG_TIERS` (`dag.py:86-123`).
- `_check_skeleton_matches_dag()` runs **at import of `guards.dag`** (`dag.py:145-151`). A mismatch makes every guard test fail at collection, not just one.
- Tier order: errors < base < common < progress < datetime < {risk, risk.results} < risk.transform < markets < {instrument, _gs_fields} < markets.portfolio < {assets.*} < assets.pricing < session < data < backtests.* < target.*.
- `test_import_order.py:34-43` imports each module alone and fails if a **later-tier** module appears in `sys.modules`.
- `FORBIDDEN_TOP_LEVEL` (`dag.py:132-136`): `markets` ↛ {portfolio, instrument, session, assets}; `instrument` ↛ {session, markets}; `risk.results` ↛ `risk.transform`.
- Consequences:
  - a module in the `risk` tier may not import `instrument` or `assets` at top level;
  - `risk/results.py` must stay duck-typed (`results.py:210-214`);
  - `assets/config.py` may import `pricebt.instrument` (earlier tier).
- DESIGN's own literal list is `DESIGN.md:152-197`; keep it in sync (DECISIONS_LOG "P0.1: `target/__init__.py` added to the skeleton" is the precedent).

### 3.5 Non-vacuity twins (`tests/guards/test_twins.py`)

If a regex or scope is widened (e.g. adding `bond` to the asset-agnostic list), add a must-fail and a must-pass twin in the same style (`:95-103`).

---

## 4. Tests and docs that assert things likely to change

### 4.1 Tests

| Test (file:line) | Asserts | Changes when |
|---|---|---|
| `tests/test_pricing_service.py:110-114` | `IRDelta(bump_size=5)` → `NotSupportedError` | FD pass-through (invert it; keep a `mkt_marking_options` → NotSupportedError test) |
| `tests/test_gs_api_parity.py:393-399` | no measure in `pricebt.risk.__all__` beyond snapshot ∪ 3 FX*LocalCcy | any new measure (regenerate the snapshot) |
| `tests/test_gs_api_parity.py:385-390` | the 2.1.17-only measures (`requested_not_found`) exist | the snapshot's `requested_not_found` changes |
| `tests/test_gs_api_parity.py:380-383`, `:455-464` | per-symbol parity; every exception row consulted | the snapshot is regenerated (new symbols such as `gs_quant.instrument.Bond`) |
| `tests/test_gs_api_parity.py:402-407` | each `pricebt.common` enum matches gs1 members | adding `FiniteDifferenceMethod` |
| `tests/test_gen_gs_fields.py:51-56` | `_gs_fields.py` == generator output | `CLASS_LIST` += Bond (regenerate) |
| `tests/test_gen_gs_fields.py:77-107` | `PRICEBT_ENUM_NAMES` == `pricebt.common` enum set | any new enum in `common.py` |
| `tests/test_risk_measures.py:163-210` | class membership and units (hand lists) | extend for new measures |
| `tests/test_risk_results.py:103-118` | bucketed frame has **exactly** the 6 columns (also empty) | a MarketData `permissions` column or a Cashflows frame kind (keep as a separate builder) |
| `tests/test_risk_results.py:122-130` | `combine_bucketed_frames` sums in first-appearance order | generalising the groupby to "all non-`value` columns" |
| `tests/test_asset_config.py:20-31, 64-75, 102-120, 171-174, 215-263` | ConfigInstrument configs with made-up measure names; error `key`s | per-class contracts (exempt ConfigInstrument); a new `returns:` kind; a new unit |
| `tests/test_asset_config.py:95-99` | a bad unit is rejected | new units (e.g. years, vol units) |
| `tests/test_pricing_service.py:117-155` | plain measure with bucketed-only mapping selects bucketed; FD measure sums | rule-5 changes (e.g. scalar → one-bucket fallback for bare FD measures) |
| `tests/test_measure_series.py:146` | a bucketed measure → `NotSupportedError(match="bucketed")` | if measure_series gains frame/bucket support |
| `tests/test_result_shapes.py:292-335` | `pnl_explain` hand computation; `fx_pnl_definition` shape | new IR definitions (add engine-level tests) |
| `tests/test_instrument.py:302-307` | no GS_FIELDS name collides with pricebt extensions | Bond (passes: `size`, `identifier`, …) |
| `tests/skills/test_skill_check_asset.py` (+ `skills/pricebt-verify-asset-config/scripts/check_asset.py:86-87,139-148,305-324`) | its own unit sets `_EXTENSIVE_UNITS`/`_INTENSIVE_UNITS`; `_risk(name)` = `getattr(pricebt.risk, name)`; `check_risk_measures` does `float(v)` or `df['value'].sum()` | new units; frame-returning measures (Cashflows) would break `check_risk_measures`; new measure names become "known" |
| `tests/guards/dag.py:19-123` | skeleton and DAG | any new `src/pricebt` file |

### 4.2 Docs and copies to update together

- **Units:** the closed set is in `config.py:22-23` and `pricing.py:47`, the tables at `DESIGN.md:298-311` and `ASSET_CONFIG_GUIDE.md:78-96`, and `check_asset.py:86-87`.
- **Injected variables:** `DESIGN.md:313-331`, `ASSET_CONFIG_GUIDE.md:53-76`.
- **Measure → function rules:** `DESIGN.md:649-661`.
- **Result objects:** `DESIGN.md:663-726` (6-column invariant at `:669`).
- **Deviations:** `DESIGN.md:864-947` (§11), mirrored in `DEVIATIONS.md`. Every new behavioural difference needs a `DEV-*` id in §11 **and** a `# pricebt DEV-..` marker (AGENTS.md MUST-2).

---

## 5. Pitfalls (with evidence)

1. **Cache and group keys are parameter-blind** (`pricing.py:224, 273, 463, 471, 494`). This is the single most likely silent-wrong-number bug when FD parameters go live.
2. **Bare FD measure = bucketed** (`pricing.py:409-417`; probe 1). "Add IRVega to the swaption config as `{scalar: vega}`" does **not** make `risks=[IRVega]` work.
3. **Eager ConfigError on a mixed book** (probes 3 and 6). The engine asks every held instrument for every measure (§1), so a catalogue measure added to `risks` must be answerable by every asset in the book.
4. **Heterogeneous forms in one column depend on order** (probe 10). `aggregate` branches on `values[0]` (`results.py:483-488`), and `get_risk_summary_df` catches only `TypeError` (`backtest_objects.py:255-258`).
   - float first → `TypeError` → `ErrorValue`, silently;
   - frame first → uncaught `ValueError`, and `result_summary` crashes.
5. **`make_bucketed_frame` stringifies keys** (`results.py:132`). Tuple keys become `"('1y', '10y')"` (probe 7); only a config's own `';'.join` gives gs-shaped cube points.
6. **Static labels.** One `{mkt_type, mkt_asset, mkt_class, mkt_quoting_style}` per portfolio function (`config.py:174-177`). Multi-curve delta, a vol cube across classes, and MarketData over several curves all need per-row coordinates (`DESIGN.md:1060`).
7. **The `number` unit is extensive** (`config.py:23`). Durations, `ExpiryInYears`, probabilities and CPI levels must set `scale_with_quantity: false`. The `date` unit is accepted on functions (`config.py:22`), but `float(raw)` would fail.
8. **`scale_with_quantity` also governs the group path.** An intensive function gets `weights=[1.0,…]`, not quantities (`pricing.py:259-277`). A MarketData or implied-vol portfolio function relies on this.
9. **FX conversion applies only to `ccy*` units** (`pricing.py:443-444`). A vega or cashflow frame under `result_ccy` needs column-aware conversion. `_scale_bucket` multiplies every value (`:380-388`).
10. **`result_ccy` forbids plain measures** (`generic_engine.py:427-434`): `IRFwdRate`, `IRGamma`, `Cashflows`, `MarketData`, implied vols, `DollarPrice`. This is gs parity, but it means an IR risk report under `result_ccy` can include only Currency/FD measures.
11. **`pnl_explain` breaks with `result_ccy` and bucketed metrics**, and raises `TypeError` for a missing exit date (§2.6 items 1-3).
12. **Snapshot regeneration rewrites two JSONs** (`tools/gs_api_snapshot.py:272-282`) and must use **base** python (gs 1.5.4). The stir env's gs_quant is 1.4.26 (`DESIGN.md:227`). Order:
    1. extend `RISK_MEASURE_NAMES`/`INSTRUMENT_CLASS_NAMES`;
    2. regenerate (base python);
    3. extend `CLASS_LIST`;
    4. regenerate `_gs_fields.py` (stir python);
    5. run `test_gen_gs_fields.py` and `test_gs_api_parity.py`.
13. **Snapshot blind spots.** Measure `asset_class`/`unit` are not compared (`tools/gs_api_snapshot.py:186-189`), so add a pricebt-side table test transcribed from gs1 `target/measures.py`.
14. **`FiniteDifferenceMethod` enum trap** (§2.3): adding it to `common.py` without adding it to `tools/gen_gs_fields.py:45-52` fails `test_gen_gs_fields.py:77-107`.
15. **Guard vocabulary lives in docstrings too.** A helpful docstring in `risk/results.py` or `assets/pricing.py` that says "vega cube (expiry;tenor)" or "notional column" fails the asset-agnostic scan. Comments are exempt (`scan.py:87-88`).
16. **New `src` files need three edits:** `dag.SKELETON_PATHS`, `dag.DAG_TIERS`, and `DESIGN.md` §3.2. Otherwise `guards.dag` fails at import (`dag.py:151`) and every guard test errors at collection.
17. **The Bond engine-attribute gap** (§2.5): the default `ScaledTransactionModel('notional_amount')` and duration strings like `'termination_date'` need Bond `attributes:`.
18. **Module-scoped fixtures leak session state.** A new engine-level test with a module-scoped fixture that calls `PricebtSession.use` must restore `PricebtSession.current`, `GsSession.current` and `actions.action_count` itself. `conftest.py:35-50` isolation is per-test only (DECISIONS_LOG 2026-09-28 "Phase 4 gate", items 1-4).
19. **`_check_no_extra_parameters` is shared by every measure class.** For `RiskMeasureWithCurrencyParameter` it skips `value` (`pricing.py:352-354`). Any new parameter class (e.g. a Double/String parameter measure) would have its payload rejected unless it is listed there.

---

## 6. Open design questions the implementer must decide (not answerable from code)

1. **Not-applicable measures** on a class (contract-mandated zeros in config vs a pricebt-synthesised typed zero), and whether a synthesised zero is a DEV.
2. **Whether a bare FD measure with only a scalar mapping** should fall back to a one-row bucket frame (a rule-5 change, a DEV) or stay a ConfigError.
3. **The shape of the frame result kind:** a new `returns:` value vs a separate section; who owns the column list (the config, or `risk/__init__.py` per measure); and the per-column scale/FX rules.
4. **`scale_factor`:** applied generically by pricebt, or passed to the config? What does `local_curve` mean for currency conversion?
5. **Whether `combine_bucketed_frames`** should adopt gs's "group by every non-`value` column" (a behaviour change for 6-column frames too: identical results, but a different code path and sort).
6. **Whether to ship predefined IR P&L definitions** (pricebt additions) whose `scaling_factor`s assume specific config units, or only document the recipe.

---

## Appendix — probe script and verbatim output

Script (scratch, not in repo). It was run with `PYTHONPATH=<pricebt-ir>\src;<pricebt-ir>\tests` and the stir python:

```python
from datetime import date
from pathlib import Path
import pandas as pd
from pricebt.instrument import IRSwap, IRSwaption
from pricebt.markets import PricingContext
from pricebt.markets.portfolio import Portfolio
from pricebt.risk import IRVega, IRVegaParallel, IRDelta, Price
from pricebt.risk.results import combine_bucketed_frames, make_bucketed_frame, PortfolioRiskResult, PricingFuture, DataFrameWithInfo
from pricebt.session import PricebtSession
A = Path(r"...\pricebt-ir\tests\assets")
s = PricebtSession.use(assets=[A / "toy_usd_irs.yaml", A / "toy_usd_swaption.yaml"])
d = date(2024, 3, 4)
swo = IRSwaption("Pay", "10y", "USD", name="swo"); swp = IRSwap("Pay", "10y", "USD", 1_000_000, name="swp")
# 1 s.pricing.value(swo, d, IRVega, None)            2 ... IRVegaParallel
# 3 s.pricing.value(swp, d, IRVegaParallel, None)     4 ... IRDelta(bump_size=1)   5 ... IRDelta(aggregation_level="Type", local_curve=True)
# 6 with PricingContext(d): Portfolio([swp, swo]).calc((Price, IRVegaParallel))
# 7 make_bucketed_frame({("1y", "10y"): 1.0, "2y;5y": 2.0})["mkt_point"]
# 8 cf = DataFrameWithInfo({"payment_date": [date(2025,1,1)], "payment_amount": [100.0], "currency": ["USD"]})
#   8a combine_bucketed_frames([cf, cf])  8b PortfolioRiskResult(Portfolio([swp]), (Price,), [PricingFuture(cf)]).aggregate()  8c .to_frame()
# 9 combine_bucketed_frames([md, md]) for a 6-column frame + 'permissions'
```

Output:

```
1 bare IRVega on toy swaption: ConfigError: [toy_usd_swaption] asset toy_usd_swaption: risk measure IRVega has no bucketed mapping
2 IRVegaParallel on toy swaption: OK -> FloatWithInfo 311.4717272939734 (USD)
3 IRVegaParallel on toy swap (no mapping): ConfigError: [toy_usd_irs] asset toy_usd_irs has no mapping for risk measure IRVegaParallel; add it under risk_measures:
4 IRDelta(bump_size=1) (DEV-I8): NotSupportedError: asset toy_usd_irs: IRDelta(bump_size:1) sets bump_size; pricebt passes only aggregation_level and currency to asset configs
5 IRDelta(local_curve=True): NotSupportedError: asset toy_usd_irs: IRDelta(aggregation_level:Type, local_curve:True) sets local_curve; pricebt passes only aggregation_level and currency to asset configs
6 mixed portfolio calc with IRVegaParallel: ConfigError: [toy_usd_irs] asset toy_usd_irs has no mapping for risk measure IRVegaParallel; add it under risk_measures:
7 tuple key mkt_point -> ["('1y', '10y')", '2y;5y']
8a combine_bucketed_frames(cashflows-like): KeyError: 'mkt_type'
8b PRR.aggregate(cashflows-like): KeyError: 'mkt_type'
8c PRR.to_frame(cashflows-like): TypeError: unsupported operand type(s) for +: 'datetime.date' and 'float'
9 combine MarketData-like: OK -> list ['mkt_type', 'mkt_asset', 'mkt_class', 'mkt_point', 'mkt_quoting_style', 'value']
```

Probe 10 (mixed forms in one measure). `PortfolioRiskResult(Portfolio([a, b]), (Price,), [PricingFuture(v) for v in futs]).aggregate(True, True)` with `futs = [FloatWithInfo(1.0), make_bucketed_frame({"5y": 1.0})]` and the reverse order:

```
float-first TypeError float() argument must be a string or a real number, not 'DataFrameWithInfo'
frame-first ValueError DataFrame constructor not properly called!
```

The regex probe for §3.1 imported `guards.scan.ASSET_AGNOSTIC_RE` / `VENDOR_RE` from `tests/` and ran `finditer` on each listed string; no string in the table hit `VENDOR_RE`.
