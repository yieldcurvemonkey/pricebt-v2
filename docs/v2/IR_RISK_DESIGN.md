# pricebt v2: interest-rate pricing and risk (swaps, swaptions, bonds): design and plan

Status: **design + implementation plan, revision 1.** Branch `v2-ir-risk` (worktree `pricebt-ir`), based on `v2-skills` with `v2-redesign` merged.
Author: Opus session, 2026-09-29, with full design authority from the user ("port all interest-rate related functionality from gs_quant's pricing and risk for backtest-related functionality ... require that the external library defines how to calculate all the IR derivative measures gs supports ... nearly 1:1 ... treat Portfolios with extra care ... swaptions and bonds").

This document **extends** `DESIGN.md`; every MUST there still holds (MUST-1 no library in `src`, MUST-2 gs API parity with DEV ids, MUST-3 one config per asset, MUST-4 currency and bp units, MUST-5 adding an asset is config-only). Where this document changes a rule of `DESIGN.md`, it says so and gives the DEV id.

Evidence: research notes R09-R15 in `docs/v2/research/` (cite by section, e.g. R09§4):

| Note | Contents |
|---|---|
| R09 `09-gs-ir-risk-measures.md` | every gs measure: class, measure_type, unit, semantics, result shape from 155 recorded server responses; helper functions; gs bugs |
| R10 `10-gs-portfolio-and-results.md` | gs `Portfolio`, `PortfolioRiskResult`, `MultipleRiskMeasureResult`, `PortfolioPath`, `to_frame` vs pricebt; notebook inventory 030000-030011; prioritised gaps §4 |
| R11 `11-gs-instruments-swaption-bond-contexts.md` | `IRSwaption`, `Bond` fields and rules; steps to add a generated class; contexts, markets, scenarios |
| R12 `12-pricebt-ir-extension-points.md` | pricebt internals: where each change goes, guard constraints, cache-key hazards, probes |
| R13 `13-skills-library-inventory.md` | the skills library, check_asset.py structure, update list U1-U36 |
| R14 `14-inflight-pnl-branch-alignment.md` | the concurrent `v2-pnl-explain` branch: names, conventions, conflict hotspots, the gamma-formula bias |
| R15 `15-gs-backtest-notebooks-swaption-bond.md` | gs swaption backtest notebooks, exact `pnl_explain` semantics, proposed definitions and unit pitfalls |

---

## 00. Revision 2: review resolutions (these OVERRIDE any conflicting text below)

A three-lens adversarial review (parity/guards, quant semantics, implementability/merge) ran on revision 1. Its findings are resolved as follows. Implementers: read this section first; where it conflicts with §0-§12, this section wins.

**Semantics (contract text)**
- **R2-1 IRDelta scalar = total own-rate derivative** (replaces §0.4's "annuity" justification and the §2.2 row). `IRDelta` s := the **total** derivative of `Price` with respect to the instrument's own rate r (`IRFwdRate`) along the library's parallel curve shift, **own-strike vol held fixed** (sticky strike): `[PV(+h) − PV(−h)] / [r(+h) − r(−h)]`, per bp of r. A fixed-annuity ∂PV/∂r (e.g. the shipped swap configs' annuity pv01) conforms only at the money; off-market it leaves a documented first-order residual `N·(F−K)·ΔA` (R14). The toy swaption and toy bond implement the total derivative; the existing toy/ARBS swap `dv01` mappings are unchanged (merge rule) and documented as at-the-money-exact. `IRGammaParallel` := the chain-rule second derivative on the same bumps: `Γ = [n₊+n₋−2n₀ − ((n₊−n₋)/(r₊−r₋))·(r₊+r₋−2r₀)] / ((r₊−r₋)/2)²` with r in the declared `IRFwdRate` unit (bp) so Γ is per bp². `IRVanna` := `d(IRDelta_s)/dσ` per bp·bp; `IRVolga` := ∂²PV/∂σ² per bp². "Exact to second order" is withdrawn: time cross terms (charm, veta, ½Θ_t·Δt²) and non-parallel curve moves stay in the residual.
- **R2-2 Additivity caveat** (DEV-I12 text): own-rate deltas of instruments quoted on different rates are not strictly additive, so `HedgeAction`/`aggregate`/risk triggers summing `IRDeltaParallel` across *different* instrument types are approximate (exact for a hedge of the same type). With a par-rate shift primitive `dr/ds ≈ 1` and the error is small. Σ bucketed `IRDelta` ≈ the parallel curve delta, which differs from the scalar by `dr/ds`; toy ladders bucket the scalar so they sum exactly.
- **R2-3 `IRDiscountDeltaParallel`** := parallel shift of the discount curve only; it is **not** in general equal to the IRDelta scalar.
- **R2-4 `Theta`** := one calendar day of carry **holding the instrument's own `IRFwdRate` and `IRAnnualImpliedVol` fixed**: `Theta = Price(t+1d; r_t, σ_t fixed) + (cashflows Price drops in (t, t+1d]) − Price(t)`, ccy per day. Swaps/swaptions between coupon dates: the translated curve `DF'(x) = DF(x)/DF(t+1d)` (never a roll; toy code must translate, keeping `csa`, not rebuild `ToyCurve(d+1, ...)`) satisfies it; bonds: reprice at the same yield. **DEV-I15** (per day, total return, own rate/vol fixed). `IRTheta` (in-flight, per year) = 365 × `Theta`; never map `Theta` to a per-year function.
- **R2-5 `ExpiryInYears`** := `max(final_or_expiry − t, 0).days / 365` (calendar days, ACT/365F) for every class (**DEV-I17**: swaps/bonds use the final date). The −365 theta scaling relies on it.
- **R2-6 `Cashflows`** := the flows **still included in `Price` that `Price` will drop on their payment date** (`payment_date > pricing date`), holder-signed. An asset whose `Price` never drops paid flows (total return, e.g. the toy swap) returns an **empty** frame. This keeps `Theta`'s cashflow term and `pnl_explain_table`'s `cashflow_pnl` consistent under both price conventions with no new schema key.
- **R2-7 Levels continue past death**: a level must be continuous with its last live value wherever a sensitivity on the previous date was non-zero. Swaption at/after expiry: the live forward of its underlying (physically-settled convention: an ITM swaption after expiry has the underlying swap's measures, OTM → 0; vol levels continue at the last value). `IRFwdRate`, `IRAnnualImpliedVol`, `IRAnnualATMImpliedVol`, `ExpiryInYears` must be finite on every held date including the exit date. These rules are **not** load-checked (the checker skill tests them); the ARBS config's dead-trade `par_rate = NaN` is a known non-compliance recorded in MERGE_NOTES (the in-flight T2 fixes it).
- **R2-8 Vol measures on swaps/bonds**: `IRVega`, `IRVanna`, `IRVolga` = 0.0 and vol levels = 0.0 by convention (so mixed books work). Declaring them unsupported is allowed but documented as breaking mixed books with vol attribution.

**Contract mechanics**
- **R2-9 Mapping wins over a stale declaration** (part of **DEV-I11**): a measure/form that is both mapped and declared unsupported loads, uses the mapping, and emits a `UserWarning` naming the stale declaration (so the in-flight branch's mappings merge cleanly over our declarations).
- **R2-10 Preset keys count**: `contracts.check` maps preset/fallback names used as mapping keys (`IRDeltaParallel`, `IRVegaParallel`, `IRBasisParallel`, `IRXccyDeltaParallel`, `IRGammaParallelLocalCcy`, ...) to their base measure and form (scalar) via the catalogue's `base_name`.
- **R2-11 `KINDS` intensive flag**: `True` = the function must be intensive (`scale_with_quantity` false); `False` = no constraint (so a checker fixture with an extensive function marked non-scaling still loads and fails at its checker row).
- **R2-12 TODO reasons**: the loader accepts any non-empty reason (so the paste-ready block with `"TODO: ..."` loads); the checker skill flags reasons starting with `TODO`.
- **R2-13 Bare FD measures** with a scalar-only contract (`IRVanna`, `IRVolga`, `IRBasis`, `IRXccyDelta`): the rule 5 "bare → bucketed" stays; the "no bucketed mapping" `ConfigError` text adds "request `X(aggregation_level='Type')` for the scalar form".
- **R2-14 Buckets with per-row coordinates**: a `returns: buckets` function may return either `{mkt_point: value}` (as today) or a **list of dicts** with keys ⊆ `{mkt_type, mkt_asset, mkt_class, mkt_point, mkt_quoting_style, value}` (the function's `labels` fill missing coordinates). Needed by `PnlExplain` (rows by `mkt_type`) and multi-curve ladders. Quantity/FX scaling applies to `value`.
- **R2-15 Frames**: `returns:` is allowed on `functions:` entries with values `scalar|frame`; portfolio functions keep `scalar|buckets` (no frame). Historical frame results: one `DataFrameWithInfo` with a `date` column prepended per date (concatenated). **DEV-R11** covers every view: `get_risk_summary_df`/`result_summary`/`risk_summary` (all three internal places, including `reindex`), `strategy_as_time_series`, `summary_stats`, `pnl_bps` skip table measures; `PRR.to_frame(values='value', ...)` drops table measures; `PRR.aggregate()` of a table measure concatenates rows (adding `instrument_name`), never sums. `make_table_frame` marks frames (attribute `pricebt_table = True` via `_metadata`).

**P&L**
- **R2-16 `PnlAttribute.market_data_unit: Optional[str] = None`** (appended after `cross_market_data_metric`, **DEV-E21**): when set, `pnl_explain` raises `ValueError(f"{attribute_name}: {measure} on {instrument} has unit {got}; the definition expects {u}")` if a level it reads is a `FloatWithInfo` whose `.unit != {u: 1}` (also checked for the cross metric, which uses the vol unit via `cross_market_data_unit: Optional[str] = None`). The `ir_*` helpers fill them; theta leaves them None. `get_risks()` omits None metrics.
- **R2-17 One step iterator**: `pnl_explain()` and `pnl_explain_table()` share a private `_explain_steps()` (same held set, same exit-results lookup, same skip/raise behaviour). Table formulas: `actual_pnl = Σ ΔPrice(self.price_measure)`, `cashflow_pnl` per R2-6, `economic_pnl = actual + cashflow`, `explained_pnl = Σ attributes`, `residual_pnl = economic − explained`. `pnl_explain()`'s numeric output must stay identical to the gs port (tests compare against a verbatim copy of the old loop).
- **R2-18 Residual bound for tests**: per step, with every derivative computed by the *test* via finite differences at t−1: `|resid| ≤ 2·(|V_rrr|·|Δr|³/6 + |V_rrσ|·Δr²|Δσ|/2 + |V_rσσ|·|Δr|·Δσ²/2 + |V_σσσ|·|Δσ|³/6 + |charm|·|Δr|·Δt + |veta|·|Δσ|·Δt + ½|Θ_t|·Δt²) + 1e-9·|N|`; steps ending within 2 calendar days of expiry are excluded; tests assert expiry > holding end. Simpler aggregate targets are fine where the bound is impractical, but never a bare "small".
- **R2-19 Test cost**: new P&L test files ≤ 15 s total; hedged books use `trade_duration='1b'` hedges (or monthly hedges ≤ 3 months); one module-scoped run shared by the assertions. Phase B may memoise `Instrument._identity_key` on resolved instruments (profiling showed `pnl_explain`'s `in portfolio` checks dominate).

**Toy/merge**
- **R2-20 `toy_usd_irs.yaml` gets ONLY an `unsupported_measures:` block** (placed right after `currency:`; no new functions). The full-contract swap used by our tests is a **new** `tests/assets/toy_usd_irs_full.yaml` (asset `toy_usd_irs_full`, its own market key, `pricebt_asset=` or its own `match:` so it never collides with `toy_usd_irs`), built on `toylib.rates` + `toylib.irrisk`. `toy_eur_irs.yaml` likewise gets only a declaration block after `currency:`. The ARBS config gets only a declaration block after `currency:` (Phase A, so `test_arbs_config_static` stays green). R2-9 makes the in-flight mappings win after merge.
- **R2-21 Phase ownership rule**: each phase owns, for every change it makes, the `gs_api_exceptions.yaml` rows, the DESIGN §11 + `DEVIATIONS.md` rows of the DEV ids it introduces (the parity test requires every `dev_id` in DESIGN §11), the `tests/guards/dag.py` entries (`SKELETON_PATHS`, `DAG_TIERS`) and DESIGN §3.2 lines for modules it creates, and **every test/skill/fixture file its change breaks** (e.g. Phase A: `tests/skills/fixtures/check_asset/*.yaml`, `skills/pricebt-connect-pricing-library/{example/*.yaml,example/mistakes/*.yaml,references/config-template.yaml}`, `tests/skills/test_skill_check_asset.py` (the `par_rate.unit=ccy` mutation moves to an unmapped copy of the function), `tests/skills/test_skill_spec.py` (its "not a pricebt class" example `Bond`), `tests/test_registry.py`; Phase B: `tests/test_portfolio.py`, every test constructing `MultipleRiskMeasureResult`, `tests/test_risk_results.py`, `tests/conftest.py`).
- **R2-22 DAG tiers**: `risk.core` and `risk.contracts` join the tier of `risk`/`risk.results`; `results → core` and `PnlExplain → markets` imports are lazy (function-level).

**Parity tooling**
- **R2-23 Snapshot check** is structural (load old and new JSON; every old key keeps its descriptor except `generated_date`, the `prev_business_date` and `OisFixingCashAccrualModel.end_date` defaults (each a `date.today()` gs evaluates at import) and the new `asset_class`/`unit` fields; everything else is new keys), not `git diff --stat`. Both the tool and the test render `str(obj.asset_class)`, `str(obj.unit)`. Add the gs 1.5.4 `gs_quant.risk` functions (`aggregate_risk`, `aggregate_results`, `subtract_risk`, `sort_risk`, `combine_risk_key`) and classes (`PnlExplain`, `PnlExplainClose`, `PnlExplainLive`, `PnlPredictLive`) to the snapshot (Phase A), each with an `all` exception row `pending: Phase B` / `pending: Phase E` that excuses its absence from pricebt and fails once pricebt has it (so the implementing phase deletes the row and gets the real comparison); pin preset parameters in a unit test.
- **R2-24 Measure exception aspect**: the parity test gains an aspect `risk_measure` with `expect: {class: ...}` (consulted by the `kind == "risk_measure"` branch, marked used); DEV-I9 uses it for `IRVanna`/`IRVolga`; a test proves a wrong `expect` fails.
- **R2-25 Signatures**: every `Portfolio` method pricebt adds that the 1.5.4 snapshot lists (including server-only stubs: `get(portfolio_id=None, portfolio_name=None, query_instruments=False)`, `save(overwrite=False)`, `from_portfolio_id(portfolio_id)`, `to_csv(csv_file, mappings=None, ignored_cols=None)`, `subset(paths, name=None)`, ...) copies the snapshot signature exactly, and implemented names leave the exceptions' `missing` lists. `PnlAttribute`'s appended fields get `signature` + `fields` rows (`dev_id: DEV-E19`/`DEV-E21`); `BackTest.pnl_explain_table` joins the `extra` methods row. `CloseMarket(date=None, location=None, check=True)`.

**Results/Portfolio**
- **R2-26 `PRR.__add__`/`dates` never evaluate a `LazyFuture`**: its date is `group_key[2]`; a `_MultiMeasureFuture` uses its first sub-future. `test_ladder.py`'s evaluation-count assertions must keep passing.
- **R2-27 Historical bucketed per-date selection** is `df[df.index == d]` (always a `DataFrameWithInfo`, possibly empty), dates carried explicitly; run the full engine suite after this change as well as after `__iter__` and `__add__`; test a hedge where one leaf's ladder is empty on some dates.
- **R2-28 `__iter__` (leaves) and nested `transform` (DEV-R7) land in the same step.**
- **R2-29 DEV-I14 `__setattr__`**: normalise camelCase to snake_case first (gs); a gs field **other than `name`** is coerced and written to `_kwargs` (None deletes), or raises on a resolved instrument; `asset_class`/`type_` raise `ValueError('<key> cannot be set')` (gs); everything else is a plain attribute; `__setattr__` never reads `_kwargs`/`resolved_terms` for non-field names (they may not exist yet during `__init__`/`clone`).
- **R2-30 `PricingContext.current` setter** stores a module-level default used only when the context stack is empty; `tests/conftest.py`'s isolation fixture saves/restores it. Phase B raises `NotSupportedError` for **any** non-None `market`; Phase E relaxes it for `CloseMarket`.
- **R2-31 `Portfolio.from_frame`/`from_csv`**: rows map to classes by `(asset_class, type)` over the generated classes (`Bond` = `('Cross Asset', 'Bond')`), round-trip `quantity_` and `pricebt_asset`, and build `ConfigInstrument` rows from `pricebt_asset`; 2.1.17 row filter (`data.notnull().any(axis=1)`).
- **R2-32 `PnlExplain` identity** (Phase E): `to_market` lives in the measure's `parameters` (a frozen `MarketParameter(date, location)`), so equality, hashing, DEV-E14 ordering and every cache key distinguish targets (**DEV-M2**). `E` runs strictly after `C` (both touch toys).

**DEV id list (final)**: DEV-I9 (vanna/volga FD), I10 (parameter pass-through), I11 (contracts + declarations, mapping-wins warning), I12 (own-rate IRDelta/IRGammaParallel/IRFwdRate incl. bond YTM; additivity caveat), I13 (diagonal IRGamma ladder), I14 (`__setattr__`), I15 (Theta per day, total return, own rate/vol fixed), I16 (LocalCcy fallbacks), I17 (`ExpiryInYears` for swaps/bonds); DEV-R6 (to_frame labels), R7 (nested transform), R8 (PRR + number), R9 (MRMR * k), R10 (subtract_risk), R11 (table measures in views), R12 (FloatWithInfo constructor order kept); DEV-P1 (recursive all_portfolios); DEV-E19 (cross metric), E20 (off-grid exits attribute; with a test), E21 (market-data unit check); DEV-M1 (`PricingContext(market=)` raises unless `CloseMarket`, which overrides the market date), M2 (`PnlExplain` target in parameters). (R15's own "DEV-R6" label for off-grid exits is E20 here.) `DEVIATIONS.md` gains sections for the new prefixes P and M.

---

## 0. Decisions (final; the user delegated them)

| # | Decision | Why |
|---|---|---|
| 0.1 | **Port the whole gs measure catalogue** (every 2.1.17 measure instance and preset, R09§3) into `pricebt.risk` as data, with gs names, `measure_type` strings, asset classes, units and parameter classes. | 1:1 importability; the catalogue is data, the numbers come from configs. |
| 0.2 | **Per-instrument measure contracts, enforced when a config loads.** Every asset whose `instrument:` is `IRSwap`, `IRSwaption` or `Bond` MUST, for every measure (and form) in its class's contract, **either map it to a config function with an allowed unit, or declare it under `unsupported_measures:` with a non-empty reason.** Anything else is a `ConfigError` that lists every gap at once and prints a paste-ready `unsupported_measures:` block. Requesting a declared-unsupported measure raises `UnsupportedMeasureError` (a `ConfigError` *and* a `NotSupportedError`) naming the measure and the reason. | The user: "assert that the external library needs to define how to calculate all these measurements". An explicit, reasoned declaration is the honest way for a library that genuinely cannot compute something (e.g. no vol cube) to say so; a silent NaN or fake zero is not allowed. |
| 0.3 | **One uniform IR contract** for the three classes (so mixed books work), plus class-specific extras. | gs itself answers every IR measure on every IR instrument (a swap's vega is 0). `pnl_explain` needs every held instrument to answer every measure in the definition. |
| 0.4 | **"Own-rate" semantics** (DEV-I12): the scalar `IRDelta`, `IRGammaParallel` and the level `IRFwdRate` all refer to the **instrument's own quoted rate**: a swap's par rate, a swaption's underlying forward swap rate, a bond's yield to maturity. `IRGammaParallel` is the full second derivative of `Price` per bp² of that rate (never `d(pv01)/d(rate)`, the "half-gamma trap"). | Makes `Price(t) - Price(t-1) ≈ Δ·Δr + ½Γ·Δr² + vega·Δσ + ... + Θ·Δt` exact to second order per instrument, which is what P&L decomposition needs. It matches pricebt's shipped swap configs (annuity pv01 = sensitivity to own par rate) and the in-flight branch (R14). |
| 0.5 | **Parameters pass through** (DEV-I10, narrows DEV-I8): `bump_size`, `finite_difference_method`, `scale_factor` and `local_curve` reach a config function as the injected names `pricebt_bump_size`, `pricebt_finite_difference_method`, `pricebt_scale_factor`, `pricebt_local_curve`. A function **supports** a parameter iff its compiled expression names that variable (`co_names`); requesting a parameter a function does not reference raises `NotSupportedError`. `mkt_marking_options` always raises. Parameters are part of every cache and group key. | Flexible (library decides bump sizes) and loud (never silently ignored); no new schema keys. |
| 0.6 | **Frame results**: a `functions:` entry may declare `returns: frame` (a pandas DataFrame or list of dicts per unit trade) with `scale_columns: [...]`. Used by `Cashflows`. Frame measures are excluded from `result_summary`/`risk_summary` (DEV-R11) and remain in `backtest.results`. | gs `Cashflows` is a 14-column table (R09§8, R11§3). |
| 0.7 | **`Bond` becomes a generated class** (R11§1: 10 steps). `IRBondFuture`, `IRBondOption`, `IRCap`, `IRFloor`, `IRCapFloor` are **not** added (no contract, no notebook; later work). | User scope: swaps, swaptions, bonds. |
| 0.8 | **Portfolio and results parity is a first-class phase** with a notebook-conversion acceptance test (every code cell of gs `03_portfolios` examples 030000-030011 and the Portfolios tutorial, server-only cells skipped with a reason). | User: "treat Portfolios with extra care". |
| 0.9 | **P&L decomposition for swaptions and bonds lives in `src`** as gs-style module functions next to `fx_pnl_definition()`: `ir_pnl_definition`, `swaption_pnl_definition`, `bond_pnl_definition`, plus `BackTest.pnl_explain_table()` (per-step actual vs attributed vs residual, with `Cashflows`-based coupon cash). `PnlAttribute` gains an appended optional field `cross_market_data_metric` (DEV-E19) so vanna is expressible. The swap definition stays with the in-flight branch (`swap_pnl_definition` in skills); `ir_pnl_definition(vega=False, vanna=False, volga=False)` also works for swaps. | Uses only gs measure names; asset-agnostic; testable; no overlap with the in-flight file set (R14). |
| 0.10 | **No engine coupon booking.** gs books only entry/exit PVs (DESIGN §11 "kept on purpose"). `pnl_explain_table` reconciles *economic* P&L (ΔPV + cashflows paid) separately; configs that want coupon-inclusive backtest Totals use a total-return `npv` (documented pattern). | Keeps gs parity; engine cash walk untouched. Listed as later work. |
| 0.11 | **`PricingContext(market=...)`**: raise `NotSupportedError` for any market other than `CloseMarket` (was silently ignored: a wrong-number path). `CloseMarket(date=d)` is implemented as a **market-date override** (functions see `pricebt_date` = pricing date and `market` = the market of `d`), and `PnlExplain(to_market=CloseMarket(...))` becomes a **relative measure** mapped to a config function that receives both markets (Phase E). Scenarios (`RollFwd`, `CurveScenario`, `MarketDataShockBasedScenario`) stay later work (§12). | Removes a silent-wrong path now; enables gs 030007 and instrument-level decomposition between two dates. |
| 0.12 | **Merge-friendliness with `v2-pnl-explain`** (R14): never edit `tests/toylib/rates.py`; toy swap extras go in a new `tests/toylib/irrisk.py`; `toy_usd_irs.yaml` edits are append-only with distinct function names (`ir_gamma`, `theta_1d`, ...); `toy_eur_irs.yaml` gains only an `unsupported_measures:` block (it stays their "explain not supported" negative case, and `UnsupportedMeasureError` is a `ConfigError` naming the measure); `IRTheta`, `YearFraction`, `CashPaidToDate` are **not** added to `src`. `docs/v2/MERGE_NOTES_pnl_explain.md` records the rest. | The two branches must merge by union. |

---

## 1. The measure catalogue (`pricebt.risk`)

1. Add every gs 2.1.17 measure instance and preset missing from `pricebt.risk` (R09§3 lists 117 instances + 7 presets; pricebt has 32). Same Python name, `name`, `asset_class`, `measure_type` string, `unit` and parameter class as gs 2.1.17. Presets as in gs (R09 "Presets"): `IRBasisParallel`, `IRDeltaParallel`, `IRVegaParallel` = `aggregation_level=Asset`; `IRXccyDeltaParallel`, `InflationDeltaParallel` = `Type`; `IRDeltaLocalCcy`, `IRVegaLocalCcy` = `currency='local'`.
2. **DEV-I9**: `IRVanna` and `IRVolga` are `RiskMeasureWithFiniteDifferenceParameter` (2.1.17 behaviour; plain in 1.5.4). gs's own vanna/volga notebook calls `IRVanna(aggregation_level=Type)`, which only works this way. Exception row in `tests/data/gs_api_exceptions.yaml`.
3. **DEV-I16** (fallbacks): `IRGammaParallelLocalCcy` has `base_name='IRGammaParallel'`; `IRDiscountDeltaParallelLocalCcy` has `base_name='IRDiscountDeltaParallel'`. A config mapping the base measure serves the LocalCcy variant (pricebt's local-currency rule, decision 0.5 of DESIGN, makes them identical).
4. Parameter classes: keep `CurrencyParameter` and `FiniteDifferenceParameter`. Add `FiniteDifferenceMethod` (`Up`, `Centered`, `Down`, `CenteredSecondOrder`) to `pricebt.common`; `finite_difference_method` given as a string is coerced to it (case-insensitive), an invalid value raises `ValueError` (gs). The Double/ListOf/Map/String parameter classes are **not** ported: no gs instance uses them and gs's own versions are broken (R09§7).
5. `measure_type` stays a plain string (existing pricebt choice; `RiskMeasureType` is not ported).
6. The `PnlExplain` family (`PnlExplain`, `PnlExplainClose`, `PnlExplainLive`, `PnlPredictLive`) and `CloseMarket` land in Phase E (§8).
7. **Snapshot parity.** Extend `tools/gs_api_snapshot.py` so its `RISK_MEASURE_NAMES` covers the whole ported catalogue **and records `asset_class` and `unit` for each measure**; add `Bond` to `INSTRUMENT_CLASS_NAMES`; regenerate both JSON files **with the base python** (`C:\Users\chris\anaconda3\python.exe`, gs 1.5.4) and confirm with `git diff --stat` that only additions changed. Extend `tests/test_gs_api_parity.py` to compare `asset_class` and `unit`. 2.1.17-only measures stay "extra symbols" (existing mechanism).
8. `pricebt.risk.core` (**new module**, gs path `gs_quant.risk.core`): `aggregate_risk(results, threshold=None, allow_heterogeneous_types=False)`, `aggregate_results(results, allow_mismatch_risk_keys=False, allow_heterogeneous_types=False)`, `subtract_risk(left, right)` (**DEV-R10**: gs's asserts always fail; pricebt implements the intent `aggregate_risk((left, -right))` with identical bucket columns), `sort_risk(df, by=...)` (DEV-R5 ordering: first appearance, no point parsing), `combine_risk_key(key_1, key_2)`. Re-exported from `pricebt.risk` as in gs. Phase B.

## 2. Measure contracts (`pricebt.risk.contracts`, new module)

### 2.1 Shape

```python
@dataclass(frozen=True)
class MeasureRequirement:
    measure: str                    # gs measure name, e.g. "IRDelta"
    kind: str                       # one of KINDS (below)
    forms: tuple                    # subset of ("scalar", "bucketed", "frame")
    doc: str                        # one-line semantics (the contract text shown in errors and docs)

KINDS = {   # kind -> (allowed units, intensive?)   intensive = scale_with_quantity must be False
    "value":  ({"ccy"}, False),
    "sens1":  ({"ccy_per_bp"}, False),
    "sens2":  ({"ccy_per_bp2"}, False),
    "theta":  ({"ccy"}, False),
    "annuity": ({"ccy"}, False),
    "rate":   ({"bp", "pct", "decimal"}, True),
    "vol":    ({"bp", "pct", "decimal"}, True),
    "time":   ({"decimal", "number"}, True),
    "prob":   ({"decimal", "number"}, True),
    "table":  (None, None),          # returns: frame; required columns in FRAME_COLUMNS
}
CONTRACTS: dict[str, tuple[MeasureRequirement, ...]]   # instrument class name -> requirements
FRAME_COLUMNS = {"Cashflows": ("payment_date", "payment_amount", "currency", "payment_type")}

def contract_for(instrument: str) -> tuple[MeasureRequirement, ...]      # () for classes without a contract
def check(instrument, mapped, unsupported) -> list[str]                  # problems, [] if satisfied
def unsupported_block(instrument, missing, reason="TODO: why your library cannot compute this") -> str  # paste-ready YAML
def validate_frame(measure_name, frame) -> None                           # required columns; called by pricing
```

`mapped` is a plain summary built by `assets/config.py` (so `contracts.py` never imports `assets`): `{measure: {"scalar": (unit, scale_with_quantity, returns) | None, "bucketed": (...) | None}}`. `unsupported` is `{measure: {form_or_"*": reason}}`.

A form requirement is **satisfied** iff the mapping provides that form with an allowed unit (and intensive when the kind requires it; `frame` iff the function `returns: frame`), **or** `unsupported_measures` declares the whole measure or that form. A measure mapped **and** declared unsupported (same form) is an error. Classes without a contract (`FXOption`, `EqOption`, `InflationSwap`, `Cash`, `FXForward`, `ConfigInstrument`) keep today's rule (only `Price` is required). **Measure names outside the contract are unrestricted** (custom names such as the in-flight branch's `IRTheta`, `YearFraction`, `CashPaidToDate` load and resolve by name exactly as today; no unit checks on them).

### 2.2 The IR contract (IRSwap, IRSwaption, Bond)

Base set (all three classes). "s" = scalar form, "b" = bucketed form.

| Measure | Kind | Forms | Contract semantics (holder-signed; per the DESIGN §5.4 unit trade, pricebt applies quantity) |
|---|---|---|---|
| `Price` | value | s | PV in the function currency. Excludes cashflows with `payment_date <= pricing date` (already paid). |
| `IRDelta` | sens1 | s, b | s: PV change for +1bp in the instrument's **own quoted rate** (`IRFwdRate`). b: curve ladder, ccy per +1bp at each pillar, `labels.mkt_type: IR`. `IRDeltaParallel`/`IRDeltaLocalCcy` resolve here (base_name). Pay-fixed swap > 0, payer swaption > 0, long bond < 0. |
| `IRDiscountDeltaParallel` | sens1 | s | PV change for +1bp parallel shift of the discount curve only (single-curve libraries: equal to the IRDelta scalar). `...LocalCcy` falls back here. |
| `IRGammaParallel` | sens2 | s | ∂²Price/∂r² per bp², r = own quoted rate. **Not** d(IRDelta)/dr when IRDelta is an annuity (that is half the gamma at the money, R14 "gamma finding"). |
| `IRGamma` | sens2 | b | Diagonal gamma ladder (ccy per bp² at each pillar), 6-column bucketed frame (**DEV-I13**: gs returns a 12-column cross-gamma frame). |
| `IRVega` | sens1 | s, b | s: PV change for +1bp of normal implied vol (`IRAnnualImpliedVol`). b: vol cube, `mkt_point = "<tail>;<expiry>"` (gs order, e.g. `"5Y;1Y"`, R09§3), `labels.mkt_type: IR VOL`. Swaps/bonds: 0 / empty. `IRVegaParallel`/`IRVegaLocalCcy` resolve here. |
| `IRVanna` | sens2 | s | ∂²Price/∂r∂σ per bp·bp (rate bp × normal-vol bp). Request as `IRVanna(aggregation_level='Type')` (FD measure, DEV-I9). |
| `IRVolga` | sens2 | s | ∂²Price/∂σ² per bp² of normal vol. Request as `IRVolga(aggregation_level='Type')`. |
| `IRBasis` | sens1 | s | Sensitivity to +1bp of the basis (projection-vs-discount) spread; single-curve libraries: 0. `IRBasisParallel` resolves here. |
| `IRXccyDelta` | sens1 | s | Cross-currency basis delta; single-currency instruments: 0. `IRXccyDeltaParallel` resolves here. |
| `IRFwdRate` | rate | s | The own quoted rate: swap par rate; swaption forward swap rate of the underlying; bond yield to maturity (**DEV-I12** for bonds). Intensive. **Finite on every date the instrument is held, including its exit date.** |
| `IRSpotRate` | rate | s | The par rate of the spot-starting equivalent (swap/swaption underlying of the same final date); bond: its yield. |
| `IRAnnualImpliedVol` | vol | s | Annualised **normal** implied vol at the instrument's strike. Swaps/bonds (no vol exposure): the constant `0.0` by convention so mixed books work. |
| `IRAnnualATMImpliedVol` | vol | s | ATM-forward normal vol for the same expiry/tail. Swaps/bonds: `0.0`. |
| `IRDailyImpliedVol` | vol | s | `IRAnnualImpliedVol / sqrt(252)`. Swaps/bonds: `0.0`. |
| `Theta` | theta | s | **One calendar day of carry, total return**: `Price(t+1d) + cashflows paid in (t, t+1d] − Price(t)`, with forward rates **and** vols held constant (curve translated, not rolled). ccy **per day** (gs `EqTheta`/`CDTheta` are per day; gs's IR `Theta` is undocumented, DEV-I12). ≠ the in-flight `IRTheta` (per year): `IRTheta = 365 × Theta`. |
| `ExpiryInYears` | time | s | Swaption: `max(expiration_date − t, 0).days/365`. Swap/bond: years to the final date (**DEV-I12**, gs defines it for options only). Intensive; used as the time coordinate of the theta attribute. |
| `Annuity` | annuity | s | PV of the fixed leg paying 1.0 per annum (i.e. `1e4 × pv01`), ccy. Bond: PV of 1.0 per annum on its schedule. |
| `Cashflows` | table | frame | Every cashflow with `payment_date > pricing date`, holder-signed, one row per flow. Required columns `payment_date, payment_amount, currency, payment_type`; the other gs columns (`set_date, accrual_start_date, accrual_end_date, notional, floating_rate_option, floating_rate_designated_maturity, day_count_fraction, spread, rate, discount_factor`) are optional. `scale_columns` MUST include `payment_amount`. |

Class extras:

| Class | Measure | Kind | Forms | Semantics |
|---|---|---|---|---|
| IRSwaption | `ProbabilityOfExercise` | prob | s | Probability (0..1) of finishing in the money under the annuity measure. |
| Bond | `LightningDV01` | sens1 | s | Yield DV01: Price change for +1bp of yield (= the IRDelta scalar for a bond). |
| Bond | `LightningOAS` | rate | s | Option-adjusted spread over the library's reference curve (a bullet bond: its Z-spread). |
| Bond | `ParSpread` | rate | s | Par asset-swap spread (or the library's par spread) in the declared unit. |

Rules that apply across the table:
- **Dead instruments** (matured, expired OTM, fully paid): every sensitivity is `0.0`, levels stay finite (e.g. the last par rate or the strike), `Cashflows` is empty. `pnl_explain` skips a risk that is exactly 0 and never guards NaN (R15), so one NaN poisons every later cumulative value.
- **Units must agree across the assets of one book** for each level measure (`IRFwdRate` in bp for one asset and decimal for another makes attribution meaningless). The shipped/toy configs use **bp** for rates and normal vols.

### 2.3 Config schema additions (`assets/config.py`)

```yaml
unsupported_measures:            # optional; top level
  IRVanna: "no vol-of-vol model in <library>"            # the whole measure (every form)
  IRDelta: {bucketed: "no curve ladder in <library>"}     # one form only
functions:
  cashflows: {expr: 'lib.cashflows(market, trade)', unit: ccy, returns: frame, scale_columns: [payment_amount, notional]}
```

- `returns:` on a `functions:` entry: `scalar` (default) or `frame`. `scale_columns:` only with `returns: frame` (list of str; required non-empty when the unit is extensive and `scale_with_quantity` is true).
- `unsupported_measures:` values: a non-empty string (whole measure) or a mapping `{scalar|bucketed|frame: non-empty string}`.
- `instrument:` is validated: a class exported by `pricebt.instrument` (generated gs classes or `ConfigInstrument`), else `ConfigError` with a did-you-mean.
- After parsing, `contracts.check(...)`; any problem → one `ConfigError` whose message lists **every** problem and ends with the paste-ready block from `contracts.unsupported_block(...)` for the missing ones. Tests assert the block, pasted into the config, makes it load.
- `AssetConfig` gains `unsupported_measures: Dict[str, Dict[str, str]]` (form or `"*"` → reason).

### 2.4 Errors (`pricebt.errors`)

`class UnsupportedMeasureError(ConfigError, NotSupportedError)`: `asset {a} has no mapping for risk measure {measure}: {measure!r} is declared unsupported ({form}): {reason}. ...` (the generic "no mapping for risk measure X" phrase is kept: R14 §8 #3, the in-flight T-MISSING test matches it). Raised by `PricingService.value` before the "no mapping" branch. Picklable (`__reduce__`), as are `AssetEvaluationError` and `MarketDataUnavailable`.

## 3. Pricing-layer changes (`assets/pricing.py`, asset-agnostic)

1. **Unsupported**: after resolving the mapping name (with `base_name` fallback, then the preset/fallback key `contracts.provided_forms` counted toward the base in the requested form, R2-10; DESIGN §8.1 rule 3), if the requested form is declared unsupported → `UnsupportedMeasureError`.
2. **Parameters (DEV-I10)**: `_measure_params(risk) -> tuple(sorted((name, value)))` for the four pass-through parameters that are not None (a `FiniteDifferenceMethod` value is passed as the enum, which is a `str`). `mkt_marking_options` set → `NotSupportedError`. For each pass-through parameter set, if `pricebt_<name>` is not in the function's `co_names` → `NotSupportedError(f"asset {a}: {risk!r} sets {p}; function {f!r} does not reference pricebt_{p}")`. The four names are **always injected** (None when absent) into `functions:` and `portfolio_functions:` evaluations. The frozen params tuple is appended to the unit-value cache key, the portfolio-value cache key, the lazy `group_key` and the member tuple (R12§3 lists the four sites). `DEV-I8` narrows to "`mkt_marking_options`, and parameters a function does not reference".
3. **Frames**: a scalar-slot function with `returns: frame` produces a `DataFrameWithInfo` (via `risk.results.make_table_frame(rows, risk_key, unit, scale_columns, factor)`), with `contracts.validate_frame(measure_name, df)`. Quantity scaling multiplies only `scale_columns` (when extensive). A currency conversion of a frame raises `ConfigError` (plain `Cashflows` never converts; under `result_ccy` it raises gs's "Unparameterised risk").
4. **Historical shapes and `calc(fn=)`** move to Phase B (§5).

## 4. Instruments

1. `Bond` generated (R11§1 steps 1-10). `Bond` has `size`, not `notional_amount`: a Bond config supplies `attributes: notional_amount` (the default `ScaledTransactionModel` reads it), `termination_date` (duration strings), and `size_attribute`. Its `resolve` folds `buy_sell` × sign(size) into a signed face amount. DEV-I1's text gains: "pricebt also scales classes gs cannot (gs `Bond.scale()` raises)".
2. **DEV-I14 `Instrument.__setattr__`** (R11§2): assigning a **gs field of that class** (per `_gs_fields`) on an unresolved instrument coerces it (enum rules of DESIGN §5.2) and writes `_kwargs` (None deletes the key); on a resolved instrument it raises `ValueError("set a field on a resolved instrument ...")` (the `clone` rule). Every other attribute (`name`, `quantity_`, `position_meta`, `resolved_terms`, `_`-prefixed, ...) is set normally. camelCase reads (`swap.fixedRate`) resolve to the snake_case field.
3. `Portfolio` gs API completion is Phase B (§5).

## 5. Portfolio and results parity (Phase B; R10§4 is the checklist)

P0 (all required):
1. **`PortfolioPath`** (gs results.py:548-590 semantics), `Portfolio.all_paths`, `Portfolio.paths(key)` returning **paths** (own matches first, then sub-portfolios'), `Portfolio.__getitem__(PortfolioPath)`, `Portfolio.subset(paths, name=None)`. Internal callers switch to paths.
2. **PRR indexing on nested results**: str/instrument → first match (gs quirk kept), list of instruments → `subset`, slice → `subset`, `PortfolioPath`, list of measures, `dates`/date lists. `__iter__` yields **leaf values in `all_paths` order** (gs; DESIGN §8.2's "per-child" text is corrected). `__contains__`.
3. **Historical shapes**: scalar → `SeriesWithInfo` indexed by date (already); **bucketed → one `DataFrameWithInfo` indexed by `date`** (gs `compose`), `raw_value` moves the index to a `dates` column; `aggregate()` of historical scalars → `SeriesWithInfo` summed per date; historical bucketed aggregate groups by `['dates', mkt_*]`; `result[date]` slicing (`_value_for_date`).
4. **`to_frame` full gs semantics** (R10§2.5): records depth-first in `futures` order with each leaf's **own path labels** (`portfolio_name_{k}`, `instrument_name`; **DEV-R6** fixes gs's mislabelling), `risk_measure`, `dates` for historical, default pivot rules, bucketed/table branch `set_index(other_cols)`, first-appearance order (no alphabetical sort), `display_options` with `show_na`, `None` when empty. The engine's explicit `to_frame(values='value', index='instrument_name', columns='risk_measure')` result must be unchanged.
5. **`__add__` date composition**: a single-date PRR's date is its first leaf's `risk_key.date` (not `(None,)`), so `Σ_d p(d)` over one portfolio stitches a historical PRR (gs). DEV-E14's ordered measures kept.
6. **`aggregate()` contract**: error value → `ValueError('Cannot aggregate results in error')`; heterogeneous types → gs message unless `allow_heterogeneous_types`; unit mismatch → gs message; different dates/keys → `ValueError` unless `allow_mismatch_risk_keys`. The lazy group path applies the same checks on group metadata. The engine's calls (`aggregate(True, True)`, `aggregate(allow_mismatch_risk_keys=True)`) must keep working.
7. **`calc(fn=...)`** applies `fn` per instrument (gs), exceptions stored in that leaf's future.
8. **`PricingContext(market=...)`**: not None and not a `CloseMarket` → `NotSupportedError` (Phase B); `CloseMarket` support in Phase E. `PricingContext.current` gets a setter (gs tutorial uses it).

P1 (all required):
9. `MultipleRiskMeasureResult(instrument, dict_values)` gs constructor (pricebt call sites updated), `.instrument`, `dates`, date indexing, `to_frame`, `+` (compose), scalar `*` (**DEV-R9**: multiply Series directly; raise on non-number).
10. `FloatWithInfo.__mul__`/`__truediv__`/`__neg__` keep metadata where gs does (R10§8), `to_frame()`; keep pricebt's `(value, risk_key=...)` constructor order (**DEV-R12**, documented) because gs's order is `(risk_key, value)` and pricebt call sites are value-first.
11. `PRR * k` scales every leaf; `PRR + number` raises `ValueError('Can only add instances of PortfolioRiskResult')` (**DEV-R8**).
12. `PRR.transform` on nested results keeps the tree (**DEV-R7**).
13. `pricebt.risk.core` helpers (§1.8).

P2 (all required unless marked):
14. `Portfolio.from_frame(data, mappings=None)`, `from_csv(csv_file, mappings=None)`, `to_csv(csv_file, ...)`, `from_dicts` is **not** gs (skip). 2.1.17 row filter.
15. `Portfolio.__repr__`/`PRR.__repr__` with counts; `UnsupportedValue`, `StringWithInfo`, `DictWithInfo`; `ErrorValue.__getattr__`.
16. Server-only names raise `NotSupportedError("... GS server-side ...")`: `Portfolio.get`, `Portfolio.from_portfolio_id`/`from_quote`/`from_asset_id`/`from_book`/..., `save`, `save_as_quote`, `save_to_shadowbook`, `market()`; `Portfolio.id`/`quote_id` return `None`.
17. `Grid(Portfolio)` (030006) — port if it fits in the phase; otherwise stub with a reason.
18. **DEV-P1**: `all_portfolios` recurses (already; add the marker).

Acceptance: `tests/test_portfolio_notebooks.py` runs the code cells of gs `03_portfolios/examples/030000-030011` and `tutorials/Portfolios.ipynb`, transcribed onto toy assets (imports changed per DESIGN §1, `GsSession` → `PricebtSession`), asserting the documented shapes; server-only cells are listed with the reason they are skipped. Run the **full engine suite after each of** the `__iter__` change and the `__add__` change (they are engine-visible).

## 6. Toy library and configs (Phase C; tests only, never shipped)

1. `tests/toylib/irrisk.py` (**new**; never edit `rates.py`): swap contract functions on top of `toylib.rates`:
   - `ir_gamma(market, trade)`: ∂²npv/∂par² per bp² via the **chain rule** on ±1bp zero-rate shifts: `Γ = [n₊+n₋−2n₀ − ((n₊−n₋)/(p₊−p₋))·(p₊+p₋−2p₀)] / ((p₊−p₋)/2)²` (R14 "gamma finding"; known answer: at the money `Γ = 2·Δpv01/Δpar`).
   - `gamma_ladder(market, trades, weights, tenors)` (diagonal, nearest pillar), `theta_1d` (flat toy: `npv(ToyCurve(d+1, ccy, z)) − npv(market)`; toy swaps never pay coupons), `expiry_in_years` (to termination), `spot_rate` (spot-starting par to the same maturity), `annuity` (`N·A`), `zero(...)` helpers.
   - Toy swap `Cashflows` is **declared unsupported** in `toy_usd_irs.yaml` ("toy npv never drops paid coupons (total return by construction); a cashflow schedule would contradict Price").
2. `tests/toylib/swaption.py` (extend, same file): delta (`N·A·Φ(±d)·1e-4`), gamma, vega (exists), vanna, volga, theta_1d (curve translated one day, σ constant, T − 1/365), fwd_rate/annual_vol/atm_vol/daily_vol in bp, expiry_in_years, prob_exercise, annuity, spot_rate, empty cashflows frame, delta_ladder, gamma_ladder, vega_cube (`"<tail>;<expiry>"`), `Straddle` = payer + receiver. Sensitivities are 0 at/after expiry; levels stay finite.
3. `tests/toylib/bond.py` (**new**): a tiny bond master (identifier → coupon, maturity, frequency), market `SimpleNamespace(curve=ToyCurve, spread=s(d))` under its own market key, dirty PV excluding paid flows, continuously-compounded yield (Newton), yield DV01/convexity (analytic), key-rate ladder, theta_1d total return, cashflows frame with gs columns, accrued/clean price (extra functions for `measure_series`), expiry_in_years to maturity, OAS = spread.
4. Configs: `toy_usd_irs.yaml` (append-only: `import toylib.irrisk as tri`, new functions/mappings); `toy_usd_swaption.yaml` (full contract); `tests/assets/toy_usd_bond.yaml` (**new**); `toy_eur_irs.yaml` (only `unsupported_measures:`); `configs/assets/usd_sofr_ois_interest_rate_swap.yaml` (only `unsupported_measures:` for what it does not map, reasons "not wired for ARBS yet; see PNL_EXPLAIN_PLAN T2" etc.; **no ARBS code, no live tests**); skills fixtures/examples/templates (Phase D or A as needed for load).
5. Known-answer tests `tests/test_toylib_ir.py`: every toy measure against an independent computation (finite differences in the test, closed forms), Taylor-order checks, put-call parity, straddle = payer + receiver, bond price/yield round trip, `Theta` frozen-world identity, dead-instrument zeros.

## 7. P&L decomposition (Phase C; `backtests/backtest_objects.py`)

1. **DEV-E19** `PnlAttribute.cross_market_data_metric: Optional[RiskMeasure] = None` (appended; positional gs calls unchanged). When set, the step contribution is `scaling_factor · risk(t−1) · Δm₁ · Δm₂` (no ½), `m₂` read exactly like `m₁` (exit results on an exit date). `get_risks()` includes it. `second_order` with a cross metric → `ValueError`.
2. Unit factors `{"bp": 1.0, "pct": 100.0, "decimal": 1e4}`.
   ```python
   def ir_pnl_definition(rate_unit="bp", vol_unit="bp", *, delta=True, gamma=True, vega=True, vanna=True, volga=True, theta=True) -> PnlDefinition
   def swaption_pnl_definition(rate_unit="bp", vol_unit="bp") -> PnlDefinition       # all six
   def bond_pnl_definition(rate_unit="bp") -> PnlDefinition                          # delta, gamma, theta
   ```
   | attribute_name | attribute_metric | market_data_metric | cross | 2nd | scaling |
   |---|---|---|---|---|---|
   | `PNL_delta` | `IRDeltaParallel` | `IRFwdRate` | – | no | f_r |
   | `PNL_gamma` | `IRGammaParallel` | `IRFwdRate` | – | yes | f_r² |
   | `VegaPnL` (gs name) | `IRVegaParallel` | `IRAnnualImpliedVol` | – | no | f_v |
   | `PNL_vanna` | `IRVanna(aggregation_level=Type)` | `IRFwdRate` | `IRAnnualImpliedVol` | no | f_r·f_v |
   | `PNL_volga` | `IRVolga(aggregation_level=Type)` | `IRAnnualImpliedVol` | – | yes | f_v² |
   | `PNL_theta` | `Theta` | `ExpiryInYears` | – | no | −365 |
3. **`BackTest.pnl_explain_table()`** → `pd.DataFrame` indexed by the `pnl_explain` step dates (or `None` without a definition). Independent of `pnl_explain()`'s loop but the same held-set rule (instruments in `results[t−1]`, level/PV at t from `results[t]` or `trade_exit_risk_results[t]`). Columns: `actual_pnl` (ΣΔPrice of the held book), `cashflow_pnl` (Σ `payment_amount` of `Cashflows` at t−1 with `t−1 < payment_date ≤ t`; 0 if `Cashflows` not among the risks), `economic_pnl`, one column per attribute (per-step differences of `pnl_explain()`'s cumulative dicts), `explained_pnl`, `residual_pnl`.
4. **DEV-R11**: frame-valued (`returns: frame`) measures are skipped by `get_risk_summary_df`/`result_summary`/`risk_summary` (listed nowhere in those frames) and stay in `backtest.results[d]`.
5. **DEV-E20** (doc only): because of DEV-R1, off-grid exits attribute correctly in `pnl_explain` where gs raises `KeyError` (R15).
6. Tests (`tests/test_pnl_ir.py`, real `GenericEngine` runs): swaption bought and held (daily, 6m) → per-step `|residual| ≤` a Taylor bound derived from the step's moves; delta-hedged swaption book (swaption + daily `HedgeAction` swaps) → the swap legs contribute delta/gamma/theta, vega terms come only from the swaption; bond held across two coupon dates → `cashflow_pnl` equals the coupon (`face·c/f`) on those steps and the residual stays within the Taylor bound; scaled (`AddScaledTradeAction` quantity 2.5) = 2.5 × unit; payer vs receiver symmetry; no NaN anywhere; `pnl_explain_table` cumulative attribution equals `pnl_explain()` exactly; a definition on `toy_eur_irs` raises `UnsupportedMeasureError` naming the measure.

## 8. `CloseMarket` and `PnlExplain` (Phase E)

1. `pricebt.markets.CloseMarket(date=None, location=None)` (gs signature per R11§4); `PricingContext(market=CloseMarket(date=d))` makes the service evaluate **markets** at `d` while `pricebt_date` stays the pricing date. The cache keys carry the market date. Trades built `each_market` use the override market; `resolve` always uses the pricing date's own market.
2. `PnlExplain(to_market)` (class, gs signature), `PnlExplainClose()` (to_market = `CloseMarket()` at the pricing date: raises `NotSupportedError` unless a context date differs), `PnlExplainLive`/`PnlPredictLive` → `NotSupportedError` (live market). Config: `risk_measures: {PnlExplain: pnl_explain}` where the function is a `returns: buckets` **portfolio function** receiving the extra injected `market_to` and `pricebt_to_date`; buckets are risk-factor rows (`mkt_type` values such as `IR`, `IR VOL`, `CROSSES`), ccy. Not in the contract (optional measure).
3. Toys implement it (full revaluation by factor: rates, vol, cross); test on a swaption and the gs 030007 notebook in `test_portfolio_notebooks.py`.

## 9. Skills library (Phase D)

Assume the agent knows the external library it is wiring in. Deliverables (details in R13§8, U1-U36):
- **New skill `pricebt-risk-measures`**: the catalogue, the contract (table + semantics + units + signs + dead-instrument rule), how to implement each measure with *your* library (bump recipes, chain-rule gamma, translated-curve theta, vol-unit conversions, vega-cube keys, frames), parameter pass-through, Portfolio/PRR usage (`to_frame`, `aggregate`, paths, historical), and a capability-matrix worksheet.
- **New skill `pricebt-pnl-attribution`**: `ir_pnl_definition`/`swaption_pnl_definition`/`bond_pnl_definition`, scaling factors from declared units, `pnl_explain_table`, diagnosing residuals (moneyness, coupons, gamma trap, per-day vs per-year theta, NaN), mixed books; points to the in-flight `swap_pnl_definition` for swaps.
- Update: `pricebt-start-here`, `pricebt-architecture`, `pricebt-connect-pricing-library` (contract-driven discovery, swaption/bond templates, paste-ready unsupported block), `pricebt-asset-config-cookbook` (patterns + error catalogue: contract errors, UnsupportedMeasureError, params, frames), `pricebt-verify-asset-config` (`check_asset.py`: contract row, pack registry with `--pack`, swaption pack, bond pack, FD-parameter probe, new fixtures — new code in **new functions** with a two-line dispatch, R14 conflict note), `pricebt-port-gs-notebook` (swaption and portfolio notebooks, decimal-vs-bp units), `pricebt-strategy-recipes` (swaption/bond archetypes; fix the swaption direction bug: flip `buy_sell`, not `pay_or_receive`), `pricebt-strategy-intake` (swaption/bond instruments, curve-trade rule), `pricebt-tearsheet-report`, `pricebt-spot-checks`, `pricebt-adversarial-review`, `README.md`; regenerate `.claude/skills` stubs (`python tools/sync_agent_skills.py`) and keep `tests/skills` green.

## 10. Docs (every phase updates what it changes; Phase F closes)

`DESIGN.md` (§3.2 package layout + import DAG for `risk/contracts.py` and `risk/core.py`; §8.1 rule 3a/5 pointers; §8.2 `__iter__` and historical shapes; §11 new DEV rows; §13 swaption walk-through now real; §14 later work), `DEVIATIONS.md` (every new DEV id), `ASSET_CONFIG_GUIDE.md` (contracts, `unsupported_measures`, `returns: frame`, parameters, units table), `DECISIONS_LOG.md` (append), `README.md`, `docs/v2/MERGE_NOTES_pnl_explain.md` (new).

New DEV ids: DEV-I9, I10, I12, I13, I14, I16; DEV-R6, R7, R8, R9, R10, R11, R12; DEV-P1; DEV-E19, E20; DEV-M1 (`PricingContext(market=...)` raises unless `CloseMarket`, which overrides the market date). (DEV-I11 is the contract itself: gs silently returns `UnsupportedValue` for inapplicable measures; pricebt requires a mapping or a declaration and raises on request.)

## 11. Plan: phases, owners, gates

Global rules for every implementer: work only in `C:\Users\chris\clee\gsquant-temp-claude\pricebt-ir`; every git command is `git -C <that path>`; do not commit (the orchestrator commits per phase); never touch other worktrees; never import ARBS/rateslib/gs_quant from `src` or tests (live ARBS tests are out of scope — do not add or run any); guards stay green; run tests with
`$env:PYTHONPATH="src;tests"; C:\Users\chris\anaconda3\envs\stir\python.exe -m pytest tests -o addopts= -p no:cacheprovider -q` (PowerShell) or `PYTHONPATH="src;tests" /c/Users/chris/anaconda3/envs/stir/python.exe -m pytest ...` (bash).
**Mutation rule (global CLAUDE.md):** every new check/rule gets a named one-line mutation that must make a named test fail; the implementer runs it, reverts it, and lists it in the phase report.

| Phase | Scope | Files (owner) | Gate |
|---|---|---|---|
| A | catalogue (§1.1-1.7), contracts + schema + errors (§2), pricing params/frames/unsupported (§3), Bond (§4.1), existing configs made contract-valid by declarations | `risk/__init__.py`, `common.py`, `risk/contracts.py` (new), `assets/config.py`, `assets/pricing.py`, `risk/results.py` (`make_table_frame` only), `errors.py`, `instrument/__init__.py`, `instrument/_gs_fields.py` (regenerated), tools, `tests/data/*`, guards (`SKELETON_PATHS`, `DAG_TIERS`), every IR yaml (declarations), `tests/test_registry.py` helper, new tests | full suite green; snapshot diff = additions only; mutations |
| B | Portfolio/results parity (§5), `risk/core.py`, instrument `__setattr__` (§4.2), `PricingContext(market=)` raise | `markets/portfolio.py`, `risk/results.py`, `risk/transform.py`, `risk/core.py` (new), `assets/pricing.py` (historical + fn), `markets/__init__.py`, `instrument/__init__.py`, `tests/test_portfolio_notebooks.py` (new) + unit tests | full suite green after each engine-visible change; notebook test green |
| C | toys + configs (§6), P&L (§7) | `tests/toylib/irrisk.py`, `swaption.py`, `bond.py`, toy yamls, `backtests/backtest_objects.py`, `backtests/generic_engine.py` (only if `get_risks` plumbing needs it), tests | full suite green; residual bounds; mutations |
| E | CloseMarket + PnlExplain (§8) | `markets/__init__.py`, `risk/__init__.py`, `assets/pricing.py`, toys, tests | full suite green |
| D | skills (§9) | `skills/**`, `tests/skills/**`, `.claude/skills/**`, `tools/sync_agent_skills.py` (only if needed) | skills tests + `sync_agent_skills.py --check` green |
| F | docs (§10) + merge notes | `docs/**`, `README.md` | `test_docs_links.py` green |
| G | adversarial review (lenses: gs parity, MUST/guards, P&L math, Portfolio semantics, merge-compat), fixes, final suite, push `v2-ir-risk` | – | all green; pushed |

Commit after A, B, C+E, D, F+G, with the session's attribution lines. Push `v2-ir-risk` at the end; do **not** merge into `v2-redesign`, `v2-skills` or `v2-pnl-explain`.

## 12. Later work (not built here)

- **Scenarios** (`RollFwd`, `CurveScenario`, `IndexCurveShift`, `MarketDataShockBasedScenario`, `CurveOverlay`, `CarryScenario`): an optional config section mapping a gs scenario class to a market-transform expression; replace the csa slot of every cache key with an opaque `(csa, location, market date, active scenarios)` value; `RollFwd` also moves the valuation date (R11§4).
- Engine coupon booking from `Cashflows` (opt-in, a deviation; decision 0.10).
- `IRBondFuture`, `IRBondOption`, `IRCap`, `IRFloor`, `IRCapFloor` generated classes and contracts; inflation and cross-currency contracts.
- Cross-trade strike references (`'=solvefor([name]...)'`, gs 030010).
- A ladder-hedge action (DESIGN §14).
