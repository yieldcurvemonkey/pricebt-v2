# Cash bonds (US Treasuries first): the strict Bond contract and repo financing (revision 4)

Status: **implemented** on branch `v2-bonds` (worktree `pricebt-bond`), based on `v2-ir-required`. The live ARBS
tier for US Treasuries is **blocked** (§7). Author: Opus session,
2026-10-03, with full design and implementation authority from the user ("implement cash bonds (US Treasuries first)
... all math and pricing comes from the external library, through the asset config ... Bond becomes a strict contract
class ... repo and financing logic is REQUIRED in the config YAML").

This revision **extends** [`IR_RISK_DESIGN.md`](IR_RISK_DESIGN.md) and [`IR_STRICT_CONTRACT.md`](IR_STRICT_CONTRACT.md).
Where they conflict, this document wins. Every MUST of [`DESIGN.md`](DESIGN.md) §2.1 still holds:

- MUST-1: nothing under `src/pricebt` imports, names or assumes a pricing library, and `src` does no bond math;
- MUST-2: gs API parity, every difference a DEV id;
- MUST-3: one config per asset;
- MUST-4: currency and bp units;
- MUST-5: adding an asset is config-only, and the engine special-cases no asset class.

Evidence: three read-only research sweeps (gs bond semantics; ARBS bonds and repo; pricebt's extension points),
summarised in §1.

## 1. What the research found

**gs_quant (2.1.17, `C:\Users\chris\clee\gsquant-temp-claude\gs-quant`).**
- `Bond` (`target/instrument.py:81-90`) has `buy_sell, identifier, identifier_type, size, settlement_date,
  settlement_currency, name`: no coupon, maturity or price field. `size` is the only quantity field. `resolve()` is a
  server call with nothing bond-specific; `Bond.scale()` raises (DEV-I1 already covers that).
- **gs has no bond analytics measure.** Searching the catalogue for clean, dirty, yield, duration, convexity, accrued,
  Z-spread or ASW finds only `LightningOAS`. The bond analytics in gs live in `timeseries/measures_bonds.py`
  (dataset fields for constant-maturity benchmarks), which a backtest cannot use. `LightningDV01`/`LightningOAS` appear
  in no notebook, doc or test.
- **gs backtests book no coupons and have no repo leg.** Entry cash is `−Price(create)`, exit cash `+Price(final)`
  (`generic_engine_action_impls.py:112-127`). The only financing is `Strategy.cash_accrual`, one rate on the whole
  cash balance, re-sampled only on cash-payment dates. `Bond.settlement_date` is never read; cash lands on the trade
  date.
- The only repo object is `InstrumentsRepoIRDiscreteLock` (a bond forward from repo), used nowhere. `IRBondFuture`
  is used only by a PredefinedAssetEngine test on a supplied price series.

**pricebt (this branch's base).**
- `Bond` is the generated gs class; its contract (`contracts.CONTRACTS["Bond"]`) is the IR base plus `LightningDV01`,
  `LightningOAS`, `ParSpread`, and Bond is **not** strict (map or declare).
- The engine books only entry/exit prices (DESIGN §11 "kept on purpose"); `pnl_explain_table` reconciles coupons from
  `Cashflows` (IR_RISK_DESIGN decision 0.10). Engine coupon booking was listed as later work ("a deviation, so ask
  first"): the user has now asked for financed bond Totals.
- `tests/test_gs_api_parity.py::test_no_unexpected_extra_risk_measures` forbids any `pricebt.risk` measure outside the
  gs catalogue, so pricebt-only measures need a declared, tested extension list.
- The asset-agnostic guard scans `assets/**`, `markets/**`, `risk/results.py`, `risk/transform.py`, `risk/core.py` for
  rates words only (`swap`, `notional`, ...). Bond and financing words are not enforced anywhere, and the engine
  (`backtests/`) is not scanned.

**ARBS** (read-only): §7.

## 2. Decisions

| # | Decision | Why (alternatives rejected in §2.1) |
|---|---|---|
| 4.1 | **Bond is a strict class.** `STRICT_CLASSES = {Bond, IRSwap, IRSwaption}`: every Bond contract row must be mapped; declaring one is a load error ending with the mapping skeleton. `unsupported_measures:` stays legal only for classes without a contract (e.g. `ConfigInstrument`), where it still raises `UnsupportedMeasureError` on request. | The user's requirement, and the same reasoning as R3-0: a declared measure breaks a mixed book's P&L definition. |
| 4.2 | **The Bond contract is widened** to every gs measure that means something for a cash bond: the IR base, `LightningDV01`, `LightningOAS`, `ParSpread`, plus `FairPremium`, `ForwardPrice`, `PremiumCents`, `LocalAnnuityInCents`, `CompoundedFixedRate`, `CRIFIRCurve`, `PnlExplain`, each with **bond-specific contract text**. `DollarPrice` stays `EXCLUDED` (the engine maps it to `Price(currency='USD')`). | "Widen the Bond contract to every relevant gs measure"; the EXCLUDED completeness test now runs over the union of the three contracts. |
| 4.3 | **pricebt bond analytics** (gs has none), each a `pricebt.risk` measure with **DEV-I20**: `CleanPrice`, `DirtyPrice`, `AccruedInterest`, `ModifiedDuration`, `Convexity`, `DaysToSettlement`. The yield to maturity is `IRFwdRate` (DEV-I12), not a second measure. | A backtest of cash bonds needs the quote, the accrued and the settlement date; duplicating the yield would give two names for one number that could drift apart. |
| 4.4 | **The financing contract** (pricebt measures, **DEV-I21**): `RepoRate`, `RepoHaircut`, `FinancingToDate`, `Carry`, `RollDown`, and the Bond `ForwardPrice` text (§4). The config decides overnight vs term and GC vs special; the contract fixes what each number means. | "Repo and financing logic is REQUIRED in the config": a Bond config that does not map these does not load (4.1). |
| 4.5 | **`Price` of a Bond is the settlement-date market value**: (clean + accrued at standard settlement) × face / 100, holder-signed, never discounted back to the pricing date. It drops a flow on the first trade date whose settlement is on or after the flow's payment date, and `Cashflows.payment_date` is that drop date. So `FairPremium == Price`. | The market convention for marking a bond position; an undiscounted invoice keeps `Price`, `DirtyPrice`, `FairPremium` and the financed principal one number. |
| 4.6 | **Financing reaches backtest Totals through an asset-agnostic engine extension, "holding cash" (DEV-E22)**: for every held position whose asset maps `FinancingToDate`, the engine books, on each date it marks or exits the position, the `Cashflows` rows the position dropped since its previous mark plus the change of `FinancingToDate`, as cash. Assets that do not map `FinancingToDate` (every swap and swaption) are untouched, so gs parity holds for them. | §2.1. Makes a financed bond's `Total` = ΔPV + coupons − repo interest, exactly and independent of the grid. |
| 4.7 | **`pnl_explain_table` gains `financing_pnl`**, and books a financed position's coupons from the engine's record. `economic_pnl = actual + cashflow + financing`; `explained_pnl = Σ attributes + financing_pnl` (financing is known cash, not a market move), so the residual is unchanged and a financed book's `economic_pnl` sums to the change in `Total`. | "economic P&L = ΔPV + coupons − financing, with a financing/carry attribute". `pnl_explain()` (the gs loop) is untouched. |
| 4.8 | **Bond `ZERO_BY_CONVENTION`**: `IRVega`, `IRVanna`, `IRVolga`, `IRAnnualImpliedVol`, `IRAnnualATMImpliedVol`, `IRDailyImpliedVol` (a bullet bond has no optionality), `IRBasis` (one discount curve), `IRXccyDelta` (one currency). `ZERO_BY_CONVENTION` becomes `{class: {measure: reason}}` so every entry carries its reason. | "Anything zero by convention for a bullet Treasury goes in ZERO_BY_CONVENTION, with the reason." |
| 4.9 | **The asset-agnostic guard grows**: bond and financing words (`bond`, `coupon`, `repo`, `haircut`, `accrued`, `cusip`, `treasury`, `clean_price`, `dirty_price`) join its pattern, and `backtests/generic_engine.py` joins its scope. | The holding-cash extension lives in the engine; the guard should prove it names no asset class. |
| 4.10 | **pricebt-only measures are a declared list**: `pricebt.risk.PRICEBT_MEASURES` (a tuple of names). The parity test allows exactly these extras, each must cite a DEV id in its contract text, and none may shadow a gs name. | Keeps "the catalogue is gs's" checkable while allowing the measures the user asked for. |
| 4.11 | **Settlement is pinned.** A config pins the standard settlement date of the trade date in `resolve` and exposes it as `attributes: settlement_date` (the gs `Bond` field); a non-standard `settlement_date` kwarg is refused. `DaysToSettlement` is the per-date numeric measure. | gs parity for the field, and a direct T+1 assertion for the checker and live tests. |

### 2.1 How financing reaches results: the alternatives

| Option | What it does | Verdict |
|---|---|---|
| A. **Holding cash (chosen)** | The engine books `Cashflows` dropped + ΔFinancingToDate for positions whose asset maps `FinancingToDate`. | Exact (the flows and the cumulative financing telescope over any grid), asset-agnostic (two catalogue names; the decision is "does the asset map it"), opt-in by mapping, so swaps keep gs parity. Cost: one more engine pass and a DEV id. |
| B. Financed total-return `Price` | The config's `Price` = PV + coupons since entry − financing since entry. | Rejected. `Price` would stop being a market value (hedge sizing, `result_summary`, `PnlExplain`, `pnl_bps` all read it), would depend on path history the market object does not hold, and would break the contract's `Price`/`Cashflows`/`Theta` semantics. |
| C. Coupons from `Cashflows`, financing from `cash_accrual` | Book coupons; let a `DataCashAccrualModel` on the repo rate charge the negative cash balance. | Rejected. The rate would come from a data source, not the bond config (the requirement), one rate would apply to the whole book (no special repo per CUSIP, no haircut), and gs's accrual re-samples the rate only on cash-payment dates (a silent stale-rate trap). |
| D. A per-day financing measure × step days | Book `FinancingPerDay(t−1) × calendar days`. | Rejected. Not exact: repo accrues between settlement dates (T+1), so a Friday step and a Thursday step disagree with trade-date day counts; a coarse grid compounds the error. A cumulative level telescopes exactly. |

**Cash accrual models with financed positions.** The engine's cash balance still shows `−Price(entry)` for a financed
long (the repo loan), so a `cash_accrual` model would charge interest on it a second time. When a run has a
`cash_accrual` model and holds a financed position, the engine warns (`UserWarning`) once. A config that wants the
haircut capital funded at the cash rate models that in `FinancingToDate`.

## 3. The Bond contract (holder-signed, per unit trade; pricebt applies quantity)

Rows are in contract order. "as base" = the shared IR base text; the other rows have Bond text. Units are the kinds of
`contracts.KINDS`; one kind is new, `days` (`number`, intensive), and the existing `time` kind is reused for duration
and convexity (decimal or number, intensive).

| Measure | Kind | Forms | Bond contract text (summary; the code holds the exact text) |
|---|---|---|---|
| `Price` | value | s | **Bond text.** Settlement-date market value: (clean + accrued at standard settlement) × face / 100, holder-signed, not discounted to the pricing date (DEV-I20). It drops each flow on the first trade date whose settlement is on or after the flow's payment date (`Cashflows` lists the flows still to drop). |
| `IRDelta` … `IRDailyImpliedVol`, `ExpiryInYears`, `Annuity` | | | as base (own rate = the yield to maturity; vol rows 0 by convention, 4.8) |
| `Theta` | theta | s | **Bond text.** Carry per calendar day with the yield fixed over the step to the next business day nb: `[Price(nb, same yield) + flows dropped in (t, nb] − Price(t)] / (nb − t).days`. One calendar day moves a T+1 settlement by 0 or 3 days; spreading the step makes `Theta × step days` exact on a business-day grid (the DEV-I15 rule for the swap schedule roll). Financing is not in `Theta`. |
| `Cashflows` | table | frame | **Bond text.** One row per flow still in `Price`, holder-signed, `payment_date` = the trade date on which `Price` drops it (a T+1 bond: the business day before a business-day coupon date). |
| `LightningDV01`, `LightningOAS`, `ParSpread` | | | unchanged |
| `FairPremium` | value | s | the amount the holder pays at standard settlement for the position: `Price` (4.5). |
| `ForwardPrice` | value | s | the forward value at the **horizon H = settlement + 1 calendar month** (following business day): `Price·(1 + RepoRate·τ(s,H)) − Σ_{s<c≤H} C·(1 + RepoRate·τ(c,H))`, τ in the repo day count, `RepoRate` held flat to H, the whole `Price` financed. ccy. Dead: 0. (DEV-I21; gs's swap/swaption text forwards to expiry.) |
| `PremiumCents` | notional_level | s | `Price / |face|` in the declared unit (pct: the dirty price per 100). |
| `LocalAnnuityInCents` | notional_level | s | `Annuity / |face|`. |
| `CompoundedFixedRate` | rate | s | the coupon restated annually compounded, `(1 + c/f)^f − 1`. |
| `CRIFIRCurve` | table | frame | as the swap row: one SIMM row per ladder pillar, Σ `Amount` = Σ `IRDelta` ladder; dead: empty. |
| `PnlExplain` | value | b | Price(market_to) − Price(market) by risk factor (`IR`, and e.g. `CREDIT` for the spread). |
| `CleanPrice` | notional_level | s | DEV-I20. The quoted clean price per 100 face for standard settlement; `CleanPrice = DirtyPrice − 100 · AccruedInterest / face`. Dead (Price 0): 0. |
| `DirtyPrice` | notional_level | s | DEV-I20. `100 · Price / face` (signed face, so the same for long and short). |
| `AccruedInterest` | value | s | DEV-I20. The coupon accrued from the last coupon date to the settlement date in the bond's accrual convention (UST: ACT/ACT ICMA), holder-signed ccy. 0 on a coupon settlement date and after maturity. |
| `ModifiedDuration` | time | s | DEV-I20. `−(1/P)·dP/dy` in years per unit (decimal) yield, y the `IRFwdRate` convention, P the dirty price. 0 when dead. |
| `Convexity` | time | s | DEV-I20. `(1/P)·d²P/dy²` in years². 0 when dead. |
| `DaysToSettlement` | days | s | DEV-I20. Calendar days from the pricing date to standard settlement (UST T+1: 1, or 3 over a weekend). |
| `RepoRate` | rate | s | DEV-I21. The funding rate in force on the pricing date for this position: overnight GC or special, or a term rate locked at the trade date; the config decides. Simple interest in the config's repo day count (USD: ACT/360). Finite every held date. |
| `RepoHaircut` | rate | s | DEV-I21. The fraction of the settlement value not financed (decimal 0.02 = 2%). |
| `FinancingToDate` | value | s | DEV-I21. Cumulative repo interest on the funding leg, from the settlement of the trade date to the settlement of the pricing date (or maturity, if earlier), holder-signed: a long pays (≤ 0), a short lends the cash and receives (≥ 0). Principal `(1 − RepoHaircut) · Price(trade date)`, pinned at resolve; simple interest at each calendar day's `RepoRate` (the last business day's fixing over weekends and holidays). 0 on the trade date. **The engine books its change as cash (DEV-E22).** |
| `Carry` | value | s | DEV-I21. Clean value now minus clean forward value at H: `(Price − AccruedInterest) − (ForwardPrice − AI(H))` = coupon income over (s, H] minus financing at `RepoRate`. Dead: 0. |
| `RollDown` | value | s | DEV-I21. Clean value at H on the library's reference curve rolled down (unchanged in time to maturity, spread held) minus clean value now. On a flat curve: the pull to par at constant yield. `Carry + RollDown` = the P&L to H with the whole `Price` financed and coupons reinvested at `RepoRate`, if the curve does not move. Dead: 0. |

**Identities** (the toy tests and the checker rows): `CleanPrice + 100·AccruedInterest/face = DirtyPrice`;
`DirtyPrice·face/100 = Price = FairPremium`; `PremiumCents` (pct) `= sign(face)·DirtyPrice` (`PremiumCents` divides by |face|, so it carries the holder's sign; `DirtyPrice` does not); price ↔ yield round trip;
`ModifiedDuration ≈ −1e4·IRDelta/Price`; `Convexity` ≈ the finite-difference second derivative in yield;
`ForwardPrice` parity; `Carry + RollDown = 0` on a flat curve when the repo matches the yield (same compounding, a
horizon with no coupon); `ΔFinancingToDate` over a step = `−(1 − h)·Price(t₀)·Σ RepoRate(day)/basis` over the calendar days between the two settlement dates (each day at the rate fixed on its last business day). `ForwardPrice` and `Carry` finance the whole `Price` (a forward price does not depend on the haircut), so `Carry + RollDown` matches a backtest's financed P&L only for a zero haircut and no coupon in (s, H] (`ForwardPrice` reinvests a coupon at the repo rate; the backtest holds it as cash).

## 4. The engine extension (DEV-E22) and the table (4.7)

`PricingService.maps(inst, risk) -> bool`: whether the instrument's asset has a mapping that serves `risk` (the same
lookup `value` uses). Asset-agnostic.

In `GenericEngine._handle_cash`, inside the cash walk, after DEV-R1's off-grid pricing of date `d`:
1. `present` = the instruments in `results[d]`; `exiting` = the trades of the exit payments (`direction == 1`) on `d`.
2. For each one whose asset maps `FinancingToDate` (memoised per asset): value `FinancingToDate` and, if mapped,
   `Cashflows`, on `d` (a `Portfolio(...).calc` under `PricingContext(d)`; the pricing caches make repeats cheap).
3. If it has a previous mark `p`: book `Σ payment_amount` of `Cashflows(p)` rows with `p < payment_date ≤ d`
   (`_cash_due`) plus `FinancingToDate(d) − FinancingToDate(p)` into `cash_dict[d]` in its currency (converted to
   `result_ccy` with the FX config when set), record it in `backtest.holding_cash[d][position] = (ccy, cashflow,
   financing)`, and move the accrual anchor (`current_value`) to `d` as a cash payment does.
4. A present position's mark becomes `d`; an exiting one's is dropped.

So a position held over grid dates t₀ < t₁ < … < tₙ with exit e books, on each tᵢ and on e, exactly what it paid since
the previous mark. Under `result_ccy`, each flow converts at its own payment date's FX (so the converted flows do not
depend on the grid either) and the financing change at the mark date's FX. Flows and financing in two currencies
without `result_ccy` raise `ValueError`. The record is keyed by the position object (two positions may share a name). Positions held to the end of the run stop at the last date. The ledger (`trade_ledger`) is
unchanged (it pairs the price legs).

`pnl_explain_table`: per held instrument, if `holding_cash[cur_date]` has its name, `cashflow_pnl += cashflow` and
`financing_pnl += financing`; otherwise the existing `Cashflows`-in-risks path. Columns:
`actual_pnl, cashflow_pnl, financing_pnl, economic_pnl, <attributes>, explained_pnl, residual_pnl`.

## 5. The toy (`tests/toylib/bond.py`, `tests/assets/toy_usd_bond.yaml`; tests only)

- Standard settlement **T+1 weekday** (the toy has no holiday calendar). `Price` = invoice at settlement:
  `Σ_{p > s} CF · DF(s, p) · e^{−spread·τ(s,p)}` with `DF(s,p) = DF(t,p)/DF(t,s)`; the yield y (continuous, ACT/365)
  solves `Price = Σ CF e^{−y τ(s,p)}`, so on the flat world `y = z + spread` exactly.
- Repo: GC fixing `r(d) = zero_rate(d) − 15bp` (decimal, simple ACT/360); a special spread per identifier
  (`TOY 4.25 2034-11-15` trades 20bp special). `RepoRate` = GC − special (overnight) or the trade-date rate (term).
- Financing terms are kwargs with config defaults: `repo_term` (`overnight` | `term`) and `repo_haircut` (decimal);
  `resolve` pins the trade date, its settlement date, the financed principal per unit and the term rate.
- Known-answer tests (each against an independent computation in the test): price/yield round trip; accrued over a
  coupon date; duration/convexity vs finite differences; forward parity; zero carry + roll when the repo equals the
  yield on a flat curve; financed Totals over a coupon date (`Total` = ΔPrice + coupons − Σ repo interest computed in
  the test); quantity scaling; long vs short symmetry.

## 6. File ownership, phases and gates

| Phase | Owner | Files |
|---|---|---|
| S: src contract and financing | orchestrator | `src/pricebt/risk/contracts.py`, `src/pricebt/risk/__init__.py`, `src/pricebt/assets/pricing.py` (`maps`), `src/pricebt/backtests/generic_engine.py`, `src/pricebt/backtests/backtest_objects.py`, `tests/guards/scan.py` (+ twins), `tests/test_contracts.py`, `tests/test_unsupported_measures.py`, `tests/test_gs_api_parity.py`, new `tests/test_holding_cash.py`, `tests/test_docs_contract_tables.py` region |
| T: toy | agent | `tests/toylib/bond.py`, `tests/assets/toy_usd_bond.yaml`, `tests/test_toylib_ir.py`, `tests/test_pnl_ir.py`, other non-skills tests building Bond configs, new `tests/test_toylib_bond.py`, `notebooks/**` if a bond appears |
| A: ARBS | orchestrator | `configs/assets/usd_ust_bond.yaml`, `tests/test_arbs_bond_config_static.py`, `tests/test_live_arbs_bond.py`, `docs/v2/LIVE_ARBS_REPORT.md` |
| K: skills | agent(s) | `skills/**`, `tests/skills/**`, `.claude/skills/**` |
| D: docs | orchestrator | `docs/v2/**`, `AGENTS.md`, `README.md` |

Gates: the full non-live suite green; `tools/sync_agent_skills.py --check`; guards green; `check_asset.py` no FAIL on
every Bond config; the live ARBS bond file green or documented as blocked; every new rule has a named mutation that
fails a named test (listed in §8); LF line endings; no new absolute user paths outside `configs/assets`.

## 7. ARBS (US Treasuries): blocked

**There is no store-only access path through ARBS for Treasury prices or for repo**, so, as the task instructs, the
live tier is blocked and no ARBS bond config ships. Every `FixedRateBondsMDP` price path refreshes the fiscaldata
reference data keyed on today's date: it runs `mkdir` inside the ARBS repository, downloads on a miss and deletes
older cached directories, before any requested date is read. `bulk_get_data` has no store-only flag. ARBS has no
repo series (no TGCR/BGCR; the Citi repo store's file does not exist). The full write-up, with file and line
citations and the unblock path that needs the user's approval (a direct immutable read of the stores, bypassing the
MDP), is [`LIVE_ARBS_REPORT.md`](LIVE_ARBS_REPORT.md) "US Treasury bonds". Everything else (contract, financing,
engine, toy, skills) is built and tested on the toy library.

## 8. Mutations run

Each one-line mutation was applied, the named test run and seen to fail, and the mutation reverted.

**Phase S (src contract and financing, commit `37bf3d5`):**

| # | Mutation | Failing test |
|---|---|---|
| M1 | `STRICT_CLASSES = frozenset({"IRSwap", "IRSwaption"})` (Bond not strict) | `tests/test_contracts.py::test_a_declaration_never_satisfies_a_strict_row[Bond]` |
| M2 | a contract-measure declaration allowed on a Bond (`if base in reqs and instrument != "Bond"`) | `tests/test_contracts.py::test_strict_class_rejects_every_declaration_of_a_contract_measure` |
| M3 | financing change not booked (`out[ccy] += 0.0`) | `tests/test_holding_cash.py::test_financed_total_is_pv_change_plus_flows_plus_financing` |
| M4 | flows read from the frame on `d` instead of the previous mark | `tests/test_holding_cash.py::test_each_mark_books_exactly_what_the_position_paid_since_the_previous_one` |
| M5 | the mark not moved after a booking | same |
| M6 | `PricingService.maps` always True | `tests/test_holding_cash.py::test_maps_is_the_opt_in` |
| M7 | `financing_pnl` left out of `explained_pnl` | `tests/test_holding_cash.py::test_pnl_explain_table_economic_pnl_sums_to_the_total_change` |
| M8 | no `result_ccy` conversion of holding cash | `tests/test_holding_cash.py::test_result_ccy_converts_each_booking_at_its_date` |
| M9 | `repo` dropped from the asset-agnostic guard | `tests/guards/test_twins.py::test_twin_asset_agnostic_scan_must_fail_every_listed_word[repo]` |
| M10 | `generic_engine.py` dropped from the guard's scope | `tests/guards/test_twins.py::test_twin_asset_agnostic_file_set_is_scoped_to_assets_markets_three_risk_files_and_the_engine` |
| M11 | `"Price"` added to `PRICEBT_MEASURES` (a pricebt measure shadowing gs) | `tests/test_gs_api_parity.py::test_no_unexpected_extra_risk_measures` |
| M12 | `Carry` added to `ZERO_BY_CONVENTION["Bond"]` | `tests/test_contracts.py::test_strict_classes_and_shared_constants` |
| M13 | the `days` kind made non-intensive | `tests/test_contracts.py::test_new_bond_rows_units_and_intensivity` |

**Phase S review fixes (commit `156554a`):**

| # | Mutation | Failing test |
|---|---|---|
| M14 | holding-cash record keyed by the position's name | `tests/test_holding_cash.py::test_two_positions_with_the_same_name_keep_their_own_records` |
| M15 | flows converted at the mark date, not their payment date | `tests/test_holding_cash.py::test_converted_flows_do_not_depend_on_the_grid` |
| M16 | flows and financing in two currencies silently added | `tests/test_holding_cash.py::test_flows_and_financing_in_two_currencies_need_result_ccy` |

**Phase T (toy, commits `ef5a2d5`, `d90970b`; all in `tests/toylib/bond.py` or the toy config):**

| # | Mutation | Failing test (`tests/test_toylib_bond.py::` unless named) |
|---|---|---|
| T1 | settle T+0 | `test_accrued_over_a_coupon_date_and_clean_plus_accrued_is_dirty`, `test_price_is_the_settlement_value_at_the_yield_and_the_yield_is_z_plus_spread` |
| T2 | Price discounted to t, not to settlement | `test_price_is_the_settlement_value_at_the_yield_and_the_yield_is_z_plus_spread` |
| T3 | the special dropped | `test_financed_totals_over_a_coupon_date`, `test_forward_price_parity`, `test_repo_terms_and_financing_to_date_day_loop` |
| T4 | ACT/365 repo | `test_financed_totals_over_a_coupon_date`, `test_forward_price_parity` |
| T5 | coupon reinvestment dropped from ForwardPrice | `test_forward_price_parity` |
| T6 | duration τ from t | `test_duration_and_convexity_are_finite_differences_of_price_from_yield` |
| T7 | RollDown without AI(H) | `test_carry_plus_roll_down_is_zero_when_the_repo_matches_the_yield_on_a_flat_curve` |
| T8 | Theta per step, not per day | `test_theta_times_step_days_is_the_constant_yield_change_to_the_next_weekday` |
| T9 | Theta without the dropped flows | same |
| T10 | weekend repo at its own date's fixing | `test_repo_terms_and_financing_to_date_day_loop` |
| T11 | principal without the haircut | `test_repo_terms_and_financing_to_date_day_loop`, `test_financed_totals_over_a_coupon_date` |
| T12 | unsigned principal | `test_long_and_short_are_symmetric` |
| T13 | `Cashflows.payment_date` = the coupon date | `test_financed_totals_over_a_coupon_date`, `tests/test_pnl_ir.py::test_b_bond_cashflow_pnl_is_the_coupon_on_exactly_the_coupon_steps` |
| T14 | accrued at t, not settlement | `test_accrued_over_a_coupon_date_and_clean_plus_accrued_is_dirty` |
| T15 | `FinancingToDate` not scaled by quantity (yaml) | `test_quantity_scales_amounts_and_not_levels` |
| T16 | RollDown keeps coupons paid in (s, H] | `test_forward_price_parity` |
| T17 | RollDown on the forward curve, not the rolled curve | `test_roll_down_on_a_sloped_curve_rolls_the_curve_not_the_forwards` |
| T18 | no maturity cap on financing | `test_financing_stops_at_maturity` |
| T19 | accrued at t + 1 calendar day | `test_price_is_the_settlement_value_at_the_yield_and_the_yield_is_z_plus_spread` (Friday case) |
| T20 | principal left out of the flows paid before H | `test_forward_price_when_the_bond_matures_before_the_horizon` |

**Phase K (skills):** 36 mutations of the checker, `measures.py`, `attribution.py`, `spot_check.py`, `spec.py`,
`tearsheet.py`, the bond template and the recipes, each failing a named test in `tests/skills/`. Two examples:
the bond clean/dirty row comparing clean with itself fails
`test_skill_check_asset_ir.py::test_broken_ir_fixture_fails_its_row[bad_bond_clean_dirty]`, and `financing_pnl`
treated as an attribute fails `test_skill_pnl_attribution.py::test_financed_bond_books_its_coupon_and_repo_as_cash_and_explains_them`.
The four bond fixtures (`bad_bond_{dv01_positive,financing_sign,clean_dirty,forward_parity}.yaml`) each FAIL only
their own row; `tests/skills/fixtures/check_asset/regenerate_bond_fixtures.py` rebuilds them from the toy config.
