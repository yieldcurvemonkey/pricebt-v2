# Live ARBS run report

**Status: run, 2026-09-28, with the user's explicit approval.** All 7 tests in
[`tests/test_live_arbs.py`](../../tests/test_live_arbs.py) (IMPLEMENTATION_PLAN.md P5.2) passed. This
file gives the exact commands, explains what each of the 6 checks verifies and why, and records the
actual results below (Results section).

## Before you run it

- The test file itself never touches ARBS at import time (see its module docstring) — only inside
  a test function body, and every test in it is skipped unless `PRICEBT_LIVE_ARBS=1`. Collecting it
  normally (no env var set) is always safe; running it with the env var set is the one time this
  branch actually imports ARBS.
- It only *reads* the ARBS curve store and fixings cache for dates on or before `_LAST_SAFE`
  (2026-08-20, per `configs/assets/usd_sofr_ois_interest_rate_swap.yaml`). It never raises
  `_LAST_SAFE`, never enables the NOJUMPS source, and never writes to the curve store.
- Run it from the worktree root, in PowerShell, with `PYTHONPATH` set so `pricebt` and the test
  helpers import:

```powershell
cd C:\Users\chris\clee\gsquant-temp-claude\pricebt-v2
$env:PYTHONPATH = "src;tests"
$env:PRICEBT_LIVE_ARBS = "1"
& C:\Users\chris\anaconda3\envs\stir\python.exe -m pytest -m live_arbs -o addopts= -v
```

Add `-s` if you want check 5's printed runtime and check 3b's fallback note to show up in the
terminal (pytest captures stdout by default without it).

To confirm afterwards that nothing outside this file was affected:

```powershell
$env:PYTHONPATH = "src;tests"
& C:\Users\chris\anaconda3\envs\stir\python.exe -m pytest tests -o addopts= -p no:cacheprovider -q
```

## What each check verifies, and why

1. **`load_market` reference dates and the 5 no-market cases.** Confirms the config's `load_market`
   returns a market whose own `reference_date()` matches the date asked for (catches a served-vs-
   requested date mismatch) on 5 consecutive business days, and returns cleanly with no market
   (never raising) on a weekend, a US holiday, a market-closed day the exchange calendar doesn't
   share with a plain weekend/holiday check (Good Friday), today (the "would need live/network
   data" case), and a date past `_LAST_SAFE` (the "would need a fixings refresh" case). This is the
   config's main safety boundary — the only line standing between a normal backtest and an
   unapproved network call.
2. **A 10y ATM payer on 2024-05-20.** Sanity-checks the whole pricing path end to end against
   numbers anyone who has priced a USD swap would recognize as plausible: an ATM trade should have
   ~0 PV, a 10y swap's DV01 per $1mm should be a few hundred dollars, and the 2024-era 10y par rate
   should be a few percent (300-600bp).
3. **The seasoned mark (2024-05-24) and the maturity+1b edge (3b).** Confirms resolution really
   pins the trade date's terms (the maturity does not silently drift to a later date's would-be
   fresh maturity) and that pricing continues to work once the swap is a few days seasoned. 3b
   checks the boundary condition right at maturity, where ARBS's own pricer may behave
   differently than mid-life; if it errors there, the test falls back to the maturity-based
   `alive()`/`dv01==0` guard instead of failing outright, and notes that the final coupon (paid at
   maturity + 2 business days) was not independently verified in that case.
4. **`delta_ladder` vs. scalar `dv01`.** The bucketed delta ladder and the scalar DV01 are two
   different functions in the config (`delta_ladder` calibrates a par-swap risk curve; `dv01` calls
   ARBS's `pv01` directly) — they should agree to within a small tolerance, catching a bug in
   either one.
5. **A short 040304 run.** The actual strategy this config exists to support (mean-reversion on the
   10y par rate) over a 6-month window, timed. This is the closest thing to an end-to-end
   smoke test against the live data.
6. **No network, and no curve-store writes.** Proves both the "no network for dates ≤ `_LAST_SAFE`"
   claim and the "no curve-store writes" claim (DESIGN.md decision 0.2) in the config's own
   description are real: with `socket.socket` patched to raise, a `load_market` call for a date past
   `_LAST_SAFE` must still return cleanly (no market, no exception), and a call for a date within
   range must still succeed from the local curve store. Separate, disjoint directories are
   snapshotted (a recursive per-file mtime+size listing, not just names — that also catches an
   in-place overwrite or a deletion, not only an addition) before and after: the fixings cache
   (`%LOCALAPPDATA%\ARBS\MDP\IRSwaps\ARBS\MDP\IRSwaps\Cache\fixings_cache\USD-SOFR-1D_fixings`) and
   the two curve-store asset partitions this config's read path (or its commented-out NOJUMPS
   sibling) could plausibly write —
   `%LOCALAPPDATA%\ARBS\Cache\curve_store\raw\asset=USD-SOFR-1D-RLBASIC` and
   `...\raw\asset=USD-SOFR-1D` (not the whole `curve_store\raw` tree: on this machine that holds
   ~20 unrelated currencies/venues and a full recursive listing of it did not finish in 120 seconds,
   while these two dirs together take about 9 seconds). ARBS is expected to create one empty,
   today-dated folder in the fixings cache per process even on a pure cache hit (R06 §6.2/§1.3) —
   anything else there (a populated folder, an overwritten or deleted file) means it reached the
   network or the cache after all. The curve store gets **no** exception: any change there at all —
   new, overwritten, or deleted — is a failure, since this source must never write the curve store.

## Results

- **Date run:** 2026-09-28
- **Overall:** all 7 tests passed (`7 passed, 968 deselected, 3 warnings in 27.13s`)
- **Swap used for checks 2-4:** payer 10y USD ATM swap, notional $10,000 (the test helper's own size);
  values below are also shown scaled to a $1mm notional, matching the plan's own bands.

| Check | Result | Notes |
|---|---|---|
| 1. load_market reference dates + no-market cases | PASS | 5 consecutive business days (2024-05-20..24) each returned a market whose `reference_date()` matched the requested date; a weekend, Memorial Day (2024-05-27), Good Friday (2024-03-29), `date.today()`, and 2026-09-15 (past `_LAST_SAFE`) all returned `None` with no exception |
| 2. 10y ATM payer on 2024-05-20 (npv / dv01 / par_rate) | PASS | npv = 0.000000 (ATM, exactly par as expected); dv01 = 812.56 USD/$1mm (in the 800-900 band); par_rate = 406.89 bp (in the 300-600 band) |
| 3. Seasoned mark on 2024-05-24 | PASS | resolved termination_date pinned at 2034-05-22 at resolution (2024-05-20), unchanged on the later mark; seasoned npv on 2024-05-24 = 31.83 (USD/$10k notional, i.e. ≈3,183 USD/$1mm) — non-zero as expected, confirming the seasoned mark actually moves off the resolution-date par value |
| 3b. Maturity / maturity+1b pricing | PASS | completed via the primary path; the maturity-based fallback guard was not needed |
| 4. delta_ladder sum vs. dv01 | PASS | ladder sum = 812.74 USD/$1mm vs. scalar dv01 = 812.56 USD/$1mm — 0.022% apart, well inside the 2% tolerance. All risk fell in the 10Y bucket (8.127); 2Y/5Y/30Y were ~0, as expected for a fresh 10y swap |
| 5. Short 040304 run | PASS | runtime = 15.4s; ledger had 9 rows (non-empty — the mean-reversion trigger fired multiple times over 2024-01-02..2024-06-28); 5 grid dates were dropped for missing market data (2024-01-15..2024-06-19 range, `missing_market='drop'` working as designed) |
| 6. No-network / no-curve-store-write check | PASS | with `socket.socket` patched to raise, both the past-`_LAST_SAFE` and in-range `load_market` calls succeeded with no network access; fixings-cache diff showed only one new entry (an empty today-dated folder, nothing written inside it); both curve-store asset partitions and `curve_store/raw`'s own mtime were byte-identical/unchanged before and after |

No failures, no fallback paths triggered, no safety-check violations. P5.2 is complete.

**P5.2 gate:** every check above is filled in and green — P5.2 is complete and the deferral in
IMPLEMENTATION_PLAN.md §9 is closed out.

---

## P&L explain (T2: `docs/v2/PNL_EXPLAIN_PLAN.md` §5.5, rows A-IDENT..A-TIME)

**Status: run 2026-09-29, with the user's explicit authorization for this tier's live reads.** All 20
tests in [`tests/test_live_arbs_pnl.py`](../../tests/test_live_arbs_pnl.py) passed, 20 passed in 284.04s
(0:04:44), well inside the 15-minute budget. Every number below is what the tests themselves printed
(`-s`), not re-derived after the fact. Full details, mutation/diagnosis rules and file ownership:
`docs/v2/PNL_EXPLAIN_PLAN.md` §5.5 and `docs/v2/DECISIONS_LOG.md`'s 2026-09-29 T2-A..T2-E entries.

### Run it

```powershell
cd C:\Users\chris\clee\gsquant-temp-claude\pricebt-pnl
$env:PYTHONPATH = "src;tests"
$env:PRICEBT_LIVE_ARBS = "1"
& C:\Users\chris\anaconda3\envs\stir\python.exe -m pytest tests/test_live_arbs_pnl.py -o addopts= -p no:cacheprovider -v
```

Add `-s` to see every test's printed numbers (the ones this section reproduces).

### What each row verifies, and what it found

| ID | Result | Numbers |
|---|---|---|
| A-IDENT | PASS | 10y ATM payer, 249 business dates through 2024. `PV == pv01*(par-K)` held on every date; worst tolerance overage was **-1.81** (i.e. comfortably inside tolerance) on 2024-04-25. |
| A-GAMMA-FD | PASS (5/5 dates) | Config `gamma` vs an independent second difference (public `Curve.shift(±1)` + `build_irswap`/`.npv`/`.fair_rate`, never this repo's `gamma`/`_bump_curves`): **0.0000% relative difference on every one of the 5 sampled dates** (2024-01-03/03-05/05-20/08-01/11-04) — e.g. `config=-0.772266` vs `independent=-0.772266` on 2024-01-03. Payer gamma < 0 and receiver = -payer to 1e-6 on every date. Secondary check (`rl.Portfolio([...]).gamma(solver=sv)`, plan's 5% tolerance) ran successfully on all 5 dates and is recorded (printed per-pillar cross-gamma frame) but not parsed/asserted — see DECISIONS_LOG.md's capped-effort note. |
| A-THETA | PASS (5/5 dates) | Payer/receiver theta negate exactly (e.g. `1346.1478` / `-1346.1478` on 2024-03-05). Par under the 1-day constant-forward translation matched the untranslated par to **0.000000bp on every date** (well inside the 0.05bp no-rolldown-leak bound) — e.g. 2024-05-20: `par_now=413.542725bp`, `par_after=413.542725bp`. `carry_1m` cross-check (recorded, not asserted, per the plan's own text): same sign as `-carry_1m*dv01` on every date, but roughly 20-40x smaller in magnitude (e.g. 2024-05-20: `theta*(30/365)=236.11` vs `-carry_1m*dv01=6796.26`, ratio `0.0347`) — the expected static-curve-vs-constant-forward gap the plan anticipated, evidently larger than "30%" for this trade/dates; recorded, not chased further (T2-B doesn't cover this ratio; it is a `carry_1m` vs `theta` convention gap, not a P&L-explain correctness question). |
| A-CASH | PASS | 10y payer 2024-01-03 → 2025-03-31. Coupon step **2025-01-09**: `cash_paid_to_date` jump `18106.4971`, independently re-summed from both legs' live `Cashflow` (`Payment < 2025-01-09`): **18106.4971** (exact match). Hand-checked fixed-leg coupon `N*K*tau = 35567.7595` vs live `Cashflow = 35567.7595` (exact). Residual reconciles EXACTLY to `exact_split` both with and without cash (1e-6 relative) — see T2-C for why the naive "residual ≈ -coupon within 2%" number (`resid_no_cash=-7034.22` vs `-coupon=-18106.50`, a **61.2%** gap) is not a bug: a real ~84bp-off-market trade's `dv01` genuinely steps down ~12% the moment the coupon settles (verified: `dv01` `847.93`→`746.77`, `par` jumps `-14.20bp`, 2025-01-08→2025-01-09), which is exactly §2.7's documented moneyness residual, landing on the coupon date by coincidence of this window. |
| A-DAILY (diagnostic) | PASS | Single 10y ATM payer, daily 2024, n=249. Hard check: residual reconciles to `exact_split` exactly (1e-6 relative) — held. Recorded (not asserted): `r2=0.999558`, `residual_share=0.00044218`, `abs_residual_ratio=1.8268%`, worst residual date `2024-04-10`. Residual/economic by moneyness quartile bucket rose monotonically with moneyness as §2.7 predicts: `0.39%` → `1.03%` → `1.92%` → `2.63%`. |
| A-GAMMA-USE | PASS | 30y and 10y ATM payers, daily 2024, 20 largest-|Δpar| days each. RMS residual **with** gamma beat **without** gamma on both tenors: 10y `204.44` vs `240.02`; 30y `864.20` vs `1014.97`. |
| A-ROLL | PASS | Periodic monthly ATM-10y roll, daily 2024, n=229, 11 trade-ledger rows. `r2=0.999902` (≥ `R2_TARGET_ARBS=0.9998`), `residual_share=9.7276e-05` (≤ `RS_TARGET_ARBS=2e-4`) — both calibrated at 2x observed headroom per §5.6 (T2-E). Ledger tie-out held to 1e-6 relative. |
| A-MATURE | PASS | 1y payer 2024-01-03 → 2025-03-31. No NaN anywhere. Maturity step is `2025-01-03 → 2025-01-06` (the last date `alive()` is still true, per §2.5's strict `maturity_date > reference_date` — one day earlier than the naive "date == maturity" guess, see T2 test notes): `pv_prev=4487.3252`, `PNL_delta=-4487.3252` — exact `-PV(t-1)`. Cumulative series finite through the window's end. |
| A-FWD | PASS | 1y-forward 5y payer (effective_date 2025-01-06). No exceptions on any of 3 sampled dates (2024-01-03, 2024-06-03, 2024-12-02). `cash_paid_to_date == 0.0` on every sampled date (all before the first payment). Sample: 2024-06-03: `npv=32129.86`, `gamma=-0.251657`, `theta=1726.4989`. |
| A-SYM | PASS | Receiver vs payer, the A-DAILY run (10y ATM payer, daily 2024): every `explain_table` column negates to 1e-6 relative/absolute. |
| A-CHECK | PASS | `check_asset.py configs/assets/usd_sofr_ois_interest_rate_swap.yaml --date 2024-01-03 --date 2024-02-05`: **PASS=56 WARN=0 FAIL=0 SKIP=1** (the SKIP is `fx_round_trip`, expected for a USD-only asset). Every new row present and PASS: `swap_pv_identity` (`d1` exact `5.8e-11` vs tol `1.84`; `d2` exact `1.8e-11` vs tol `1.83`), `swap_gamma` (payer `-0.772266`, receiver `0.772266`, half-gamma probe `2024-02-01→2024-02-13` (gap 8bd) `dpar=45.67bp`, `Gamma/Gamma_est=0.939` → PASS), `swap_theta` (payer `1411.29`, receiver `-1411.29`), `year_fraction` (intensive, exact), `cash_paid_to_date` (fresh-ATM `0`, receiver `-payer`). Getting a clean run here required a real bugfix — see T2-D: the half-gamma probe's own candidate-date search had no `has_market` guard and crashed on 2024-01-15 (MLK Day), a real US bond-market holiday the toy config has no equivalent of. |
| A-TIME | PASS | Whole-file wall time (excluding A-TIME itself): **283.9s** (< 900s budget). Heaviest single tests: `test_a_ident...` 11.06s (249 sequential per-date pricing calls with no market caching across dates), `test_a_check...` 3.94s (a full `run_checks` smoke backtest + ~50 checks). Every A-GAMMA-FD/A-THETA date < 0.6s (per-market memoisation in `_bump_curves`/`_translated_curve` working as intended). No per-test change was needed to hit the budget. |

### Files touched, live-side

- `configs/assets/usd_sofr_ois_interest_rate_swap.yaml`: new `gamma`/`theta`/`year_fraction`/
  `cash_paid_to_date` functions and their `risk_measures:` mappings; `par_rate`'s dead-trade branch
  changed from `NaN` to `fixed_rate*1e4` (T2-A). **Superseded:** R3 makes the dead-trade par continuous
  (the final period's own par; `docs/v2/DECISIONS_LOG.md` 2026-10-01 "ARBS dead-trade par is continuous;
  T2-A superseded", and "Strict contract (R3)" decision 3 below). `dv01`, `npv` and `par_rate` on a LIVE
  trade: byte-for-byte unchanged.
- `tests/test_live_arbs_pnl.py` (new, `live_arbs`): the 20 tests above (12 plan IDs, 3 of them
  parametrized over 5 dates).
- `skills/pricebt-verify-asset-config/scripts/check_asset.py`: the half-gamma-probe `has_market` guard
  (T2-D) — the only non-T2-owned file this tier touched, and only because the task's own acceptance
  command could not otherwise pass; `tests/skills/test_skill_check_asset.py` (toy-only, T1's suite) is
  still 30/30 green.
- `docs/v2/DECISIONS_LOG.md`: five new entries (T2-A..T2-E) covering the dead-trade-rate choice, the
  fixings-gap patch and coupon-day cash correction inside `theta`, the coupon-date moneyness-residual
  finding behind A-CASH's redesigned assertions, the `check_asset.py` bugfix, and the R2/residual-share
  calibration.

### Side effects observed

Exactly the documented one: an empty, today-dated folder appears in ARBS's fixings cache once per
process. No network call, no curve-store write, on any of the live runs performed for this tier (three
full-file runs plus several targeted single-test/diagnostic runs during development, all deliberate, none
in a loop or retry harness). Nothing crossed a rail; nothing unexpected happened.

---

## Strict contract (R3)

**Status: run 2026-10-01** (user authorisation for live store-only reads, `docs/v2/PNL_EXPLAIN_PLAN.md` §1.1
rails unchanged). The ARBS config now maps **every** strict-contract measure of `IRSwap` (the 19 base
rows plus the 8 R3-1 rows, `docs/v2/IR_STRICT_CONTRACT.md`) and has **no** `unsupported_measures:`
block. It loads under owner A's strict check with no warning.

**Rerun 2026-10-02 at HEAD (73f6914 plus the coupon-day Theta fix, decision 2):**
- [`tests/test_live_arbs_contract.py`](../../tests/test_live_arbs_contract.py),
  [`tests/test_live_arbs_pnl.py`](../../tests/test_live_arbs_pnl.py) and [`tests/test_live_arbs.py`](../../tests/test_live_arbs.py)
  in one run: **41 of 41 passed in 444.7s** (451s wall). That is 14 contract tests (13 plus the new weekend
  coupon-step test), 20 pnl and 7 base. The orchestrator's run at 73f6914, before the fix, was 40 of 40 in 413s.
  A-TIME, the whole pnl file without itself, was 377.2s (283.9s at T2; budget 900s). The tree was the
  shared worktree at 73f6914 with other owners' uncommitted edits present (`src/pricebt/risk/contracts.py`,
  `src/pricebt/assets/registry.py`, `tests/toylib/*` and others), so the run exercised those too. No traceback
  in the log. The 11 warnings are rateslib's `RuntimeWarning: invalid value encountered in divide`
  (`rateslib/data/fixings.py:3426`) and pricebt's "dropped N dates with no market data" (US holidays).
- `check_asset.py configs/assets/usd_sofr_ois_interest_rate_swap.yaml --date 2024-01-03 --date 2024-02-05`
  (CLI, live): **PASS=175 WARN=4 FAIL=0 SKIP=2 INFO=2** in 10s. `ir_cashflow_drop` is **WARN 8.7%**; see
  "check_asset" below.
- `tests/test_arbs_config_static.py` (no ARBS, parses the YAML only): 8 of 8 passed.

The `ir_cashflow_drop` failure recorded in an earlier draft came from an intermediate checker version that
measured the step's move with `IRSpotRate`. The shipped row uses `IRFwdRate`, the level `Theta` holds and
`ir_pnl_definition` attributes against, plus a market-only correction that does not apply to ARBS
(`docs/v2/DECISIONS_LOG.md` 2026-10-01).

### Run it

```powershell
cd C:\Users\chris\clee\gsquant-temp-claude\pricebt-req
$env:PYTHONPATH = "src;tests"
$env:PRICEBT_LIVE_ARBS = "1"
& C:\Users\chris\anaconda3\envs\stir\python.exe -m pytest tests/test_live_arbs_contract.py tests/test_live_arbs_pnl.py tests/test_live_arbs.py -o addopts= -p no:cacheprovider -q -rfE -s
& C:\Users\chris\anaconda3\envs\stir\python.exe skills/pricebt-verify-asset-config/scripts/check_asset.py configs/assets/usd_sofr_ois_interest_rate_swap.yaml --date 2024-01-03 --date 2024-02-05
```

### Functions added or changed (`configs/assets/usd_sofr_ois_interest_rate_swap.yaml`)

| Measure | Function | How |
|---|---|---|
| `IRDiscountDeltaParallel` | `discount_delta` | ±1bp on the **discount** curve only, with the projection curve held: rateslib `npv(curves=[forecast, discount])`. Verified that the list order is `[forecast, discount]`, and that discount-only plus projection-only adds up to the full parallel shift to 1e-7 relative. |
| `IRGamma` (bucketed) | `gamma_ladder` | The **diagonal** of `rl.Portfolio(...).gamma(solver=sv)` on the existing `_risk_model` risk curve. The pillars are the delta ladder's (`_PILLARS` = 2Y/5Y/10Y/30Y). Units are USD per bp². This is the full Hessian, not half of it: its total over every cell equals the chain-rule `IRGammaParallel` to 0.07% (see the recorded comparisons below). |
| `IRGammaParallel` | `gamma` (changed) | The **chain-rule** second derivative (MERGE_NOTES §4). On a 10y ATM payer on 2024-05-20 it gives −0.8168, against −0.7365 from the old bare second difference (10.9% low). |
| `Theta` | `theta_1d` (new) | USD per **calendar day**. It reuses T2's translated curve, holds the own par fixed, and adds the flows npv drops in (t, t+1d]. The one-off own-par jump on a payment date is spread over the n calendar days to the next business day (see decision 2). |
| `IRTheta` (custom) | `theta` (changed) | `365 × theta_1d`: one definition, so `Theta·365 == IRTheta` holds exactly. |
| `IRFwdRate` | `par_rate` → `par_bp` | The live branch is unchanged. Only the dead-trade branch changed (see decision 3). |
| `IRSpotRate` | `spot_rate` | The par rate of a swap starting at spot and ending on this trade's termination date. Once termination ≤ spot, it is the zero-length limit of that swap: the overnight forward at spot. This stays finite and continuous (432.6 → 431.3 → 430.1bp across the 1y swap's maturity). |
| `Cashflows` | `cashflows` | Every flow still inside npv (`Payment >= reference date`), holder-signed, both legs, with `payment_date = Payment + 1 calendar day` (see decision 1). |
| `ExpiryInYears` | `expiry_in_years` | (termination − reference date).days / 365, floored at 0 |
| `Annuity` | `annuity` | `1e4 × pv01`, holder-signed (payer > 0); 0 once the swap is dead |
| `ParSpread` | `par_spread` | rateslib `IRS.spread()`, the float-leg spread that makes npv 0. It equals K − par to 1e-13bp for these matching annual legs. Dead swap: K − the continued par. |
| `FairPremium` | `fair_premium` | `Price / DF(spot)` (USD spot = reference date + 2b) |
| `ForwardPrice` | `forward_price` | `Price / DF(termination)`; on or after termination, `Price` |
| `PremiumCents` | `premium_cents` | `Price / abs(N) × 1e4`, unit bp, intensive |
| `LocalAnnuityInCents` | `local_annuity_in_cents` | `Annuity / abs(N)`, unit decimal, intensive |
| `CompoundedFixedRate` | `compounded_fixed_rate` | `(1 + K/f)^f − 1`, with f read from the trade's fixed-leg schedule (`periods_per_annum`). usd_irs is annual, so the result is K. |
| `CRIFIRCurve` | `crif_ir_curve` | One row per pillar of this trade's own delta ladder. `Risk_IRCurve`, Qualifier `USD`, Bucket `"1"`, Label1 the pillar in lower case, Label2 `OIS`, AmountCurrency `USD`. A dead swap gives an empty frame. |
| `PnlExplain` | `pnl_explain` (portfolio, buckets) | One `IR` row = Σ w·(npv(`market_to`) − npv(`market`)) (see decision 4) |
| `IRVega` / `IRVanna` / `IRVolga` / the three vol levels / `IRBasis` / `IRXccyDelta` | `zero_per_bp`, `zero_per_bp2`, `zero_vol`, `vega_cube` (`{}`) | 0.0 **by convention**. The swap is single-curve (USD-SOFR-1D both discounts and projects), single-currency, and has no vol exposure. Each of these measures is in `contracts.ZERO_BY_CONVENTION["IRSwap"]`. No other measure is a constant (the static test asserts this). |
| (helper) `unsettled` | changed `>` to `>=` | See decision 1. It changes npv only on the final coupon's payment date. |
| `IRDelta` scalar | `dv01`, **unchanged** | Still the annuity pv01. It is the own-rate delta exactly only at the money (R2-1, documented in the config). The 040304 notebook sizes on it. |

### Decisions

1. **Cashflows: the date a flow drops.** I verified live on the 10y payer resolved 2024-01-03, whose first
   Payment is 2025-01-08. ARBS npv keeps the coupon **on** its Payment date (71,142.83 → 71,595.66) and drops
   it the next day (52,448.50). So `payment_date` is the date Price drops the flow, which is Payment + 1
   calendar day. The checker's and `pnl_explain_table`'s (t0, t1] rule then counts the flow on exactly the
   step on which npv loses it, and on which `cash_paid_to_date` (its rule is `Payment < ref`) gains it.
   The contract's "payment_date > pricing date" then holds while the flow is still inside npv. Side
   finding: the old `unsettled` guard (`maturity + 2b > ref`) zeroed npv a day **before** the final coupon
   paid. On a 1y payer that left a −4,489.71 / +4,489.71 residual pair on 2025-01-08 and 2025-01-09.
   Fixed to `>=`, the final coupon now drops like every other coupon. `tests/test_arbs_config_static.py::test_npv_keeps_the_final_coupon_on_its_payment_date`
   pins the `>=` offline (mutation: back to `>` fails it). Deriving the final payment date from the
   cashflows' `Payment` column, as `cash_paid_to_date` does, was considered and not done: `unsettled` runs
   on every `Price` call (the 040304 sizing path), and a `cashflows()` call there is not low-risk. The
   `maturity + 2b` rule equals the final Payment for this swap (PaymentLag 2b; the 1y live test asserts
   npv 4,489.7097 on Payment and 0 the day after).
2. **Theta holds the own par fixed** (contract: "holding the own IRFwdRate fixed"; R3-3: "own par fixed").
   On a normal day the translated curve already does this: the par moved 0.000000bp, and Theta agreed with
   T2's formula to 1e-10. On a **coupon day** the paid period leaves the remaining swap, and its par jumps
   (−12.73bp under the one-day translation on 2025-01-08). That jump is time, not a market move, so
   `theta_1d` removes it at the translated pv01: `npv_T − pv01_T·(par_T − par_0) + coupon_today − npv_0`.
   On 2025-01-08 the config gives 9,506.25/day, where T2's formula gave 6.39/day. Evidence, from the
   canonical `pnl_explain_table` (`ir_pnl_definition(vega=False, vanna=False, volga=False)` with
   `Cashflows`; 10y payer 2024-01-03 → 2025-01-31):

   | Theta | coupon-step residual (2025-01-09) | run Σ abs(residual) |
   |---|---|---|
   | own par held (shipped) | **1,571.24** (8.7% of the 18,106.50 flow) | 20,360.51 |
   | T2 formula (par not held) | 11,071.09 (61.1%) | 29,858.82 |

   The 1,571 left over is −a·Δpar. The delta term uses the pre-drop annuity, while the paid period's
   annuity `a` has already left; that is a first-order limit of an own-par risk factor on a
   period-dropping Price.

   **The jump is spread over the step (fix, 2026-10-02, from adversarial review).** The par-holding term
   `−pv01_T·(par_T − par_0)` is a one-off (the T2 formula without it is an ordinary 6.39/day), but the engine
   books `PNL_theta = Theta(t0) × step calendar days` (`ir_pnl_definition`: `Theta` against `ExpiryInYears`
   with factor −365). On a step of 3 days (a Friday payment) or 4 (before a holiday) it was counted 3 or 4
   times. `theta_1d` now divides that term by `n = (calendar_advance(reference_date, "1b") − reference_date).days`,
   so `Theta × n` contains it once on a business-day grid (DEV-I15: a config that removes the jump from the
   own-rate move spreads it over the calendar days to the next business day; on a coarser grid the excess
   lands in the residual). Measured on a 10y payer effective 2023-03-13, struck ATM on 2024-03-12, whose
   first Payment is **Friday 2024-03-15** (coupon step 2024-03-15 → 03-18, net flow 13,714.56):

   | Theta | Theta(2024-03-15) | PNL_theta | coupon-step residual |
   |---|---|---|---|
   | jump spread over n = 3 (shipped) | 4,120.30/day | 12,360.90 | **1,365.22** (9.95% of the flow) |
   | jump per day (before) | 12,361.73/day | 37,085.18 (the jump 3×) | −23,359.06 (170.3%) |

   The 1-day case (Wednesday 2025-01-08 → 01-09) is unchanged at 1,571.24 (8.68%), because n = 1.
   `test_coupon_step_over_a_weekend_counts_the_own_par_jump_once` asserts the 1-day ratio < 15% and the
   weekend ratio < 2 × the 1-day ratio. With the `/ n` removed it fails at 170.3% (mutation run).
   The curve calendar agrees with the backtest grid: `calendar_advance(ref, "1b")` equalled the next date
   with a market on all seven 2024 US-holiday probes (Good Friday 03-28 → 04-01, Memorial Day 05-24 → 05-28,
   07-03 → 07-05, Columbus Day 10-11 → 10-15, Veterans Day 11-08 → 11-12, Thanksgiving 11-27 → 11-29,
   12-24 → 12-26). So the residual caveat does not bite on this config's own daily grid.
   Side effect: on a normal Friday the tiny translated par move (about 5e-5bp on the 10y) is also divided by 3. Theta on
   2025-01-03 moved 22.742 → 22.652 (0.09 USD per step), and on the 1y payer's last alive day (also a Friday)
   the maturity-step residual moved 0.0030 → 0.0936 USD (decision 3).
3. **Dead-trade par (R2-7): continuous, the final period's own par.** On a dead date, `par_bp` returns
   `K × (−float_cf / fixed_cf)` of the final accrual period, from the remarked trade's own `cashflows()`.
   Both legs pay on one date, so the discount factor cancels. This **equals** `fair_rate` on every
   dead-but-unsettled date: 526.5049687718308 against 526.5049687718309 on 2025-01-06..08. It stays finite
   after the payment, where `fair_rate` raises `ZeroDivisionError`. It is continuous with the last live
   value: 526.507952 → 526.504969bp across maturity. Evidence, from `pnl_explain_table` (as above) on a 1y
   payer 2024-01-03 → 2025-03-31:

   | Dead par | last-alive → maturity step residual | run Σ abs(residual) |
   |---|---|---|
   | final-period par (shipped) | **0.003** (PNL_delta −0.30); 0.094 after the decision-2 jump spread | 91.41; 92.19 after |
   | `fixed_rate × 1e4` (merged T2 convention) | 4,518.71 (PNL_delta −4,487.33 ≈ −PV) | 4,610.12 (13,588.47 before the `unsettled` fix) |

   `ParSpread` continues the same way, as K − par (−44.04bp after maturity).
4. **PnlExplain includes time on ARBS.** An ARBS market object carries its own valuation date. The toys
   re-anchor; ARBS cannot. So the IR row is npv(market_to) − npv(market): the market move **plus** the
   carry between the two dates, and any coupon npv drops in between. For example, across the coupon,
   2025-01-08 → 01-09 gives IR = −19,147.17, which includes the −18,106.50 coupon. This is the spec's
   "IR row = npv(market_to) − npv(market)"; it is recorded here as a deliberate difference from the toys.
5. **IRSpotRate after spot ≥ termination** uses the overnight forward at spot, the zero-length limit of the
   spot-starting swap. I did not fall back to the own par: that would jump 432 → 526bp on a 1y swap's last
   days.

### Results (`tests/test_live_arbs_contract.py`, every number as printed with `-s`)

| Test | Result | Numbers |
|---|---|---|
| coverage: all 27 rows mapped, finite on 2 dates | PASS | 10y payer (resolved 2024-01-03). On 2024-05-20: Price 53,344.34, IRDelta 825.14, IRDiscountDeltaParallel −17.68, IRGammaParallel −0.7832, IRFwdRate 413.54, IRSpotRate 406.91, Theta 7.870/day, ExpiryInYears 9.6356, Annuity 8,251,357, ParSpread −64.65bp, FairPremium 53,360.08, ForwardPrice 78,568.48, PremiumCents 533.44bp, LocalAnnuityInCents 8.2514, CompoundedFixedRate 348.89bp. On 2024-11-04: Price 39,932.65, IRDelta 854.57, Theta 5.155/day, ParSpread −46.73bp. PnlExplain gives one `IR` row. |
| identities, payer + receiver + quantity 2.5 | PASS (both dates) | `PremiumCents == Price/abs(N)·1e4`, `LocalAnnuityInCents == Annuity/abs(N)`, `Annuity == 1e4·dv01`, `ForwardPrice·DF(term) == Price` (DF(term) 0.678953 / 0.707153), and `FairPremium·DF(spot) == Price` (DF(spot) 0.99970499 / 0.99974186) all hold to 1e-9. `ParSpread` equals K − par exactly (−64.649172 / −46.728405bp; the bound is 0.5bp). `CompoundedFixedRate` = K = 348.893553bp. `Σ CRIF Amount == Σ IRDelta ladder` to 1e-9 (766.153934 / 759.184314). Receiver: the signed measures negate and the levels are equal. Quantity 2.5 multiplies the extensive measures by 2.5 and leaves the intensive ones unchanged. |
| IRSpotRate on a fresh spot swap's trade date | PASS | 406.88922628 == IRFwdRate 406.88922628 |
| Cashflows across a coupon date | PASS | 10y: flow 18,106.4971 equals the hand both-leg `Cashflow`; listed (payment_date 2025-01-09) through 2025-01-08 and gone on 01-09 (rows 20 → 18); npv 71,142.83 / 71,595.66 / 52,448.50 on 01-07/08/09; cash jump 18,106.4971. 1y final coupon: npv 4,489.7097 on its Payment date, 0 the next day, dnpv + flow = 0.0000 exactly. |
| PnlExplain IR row == Δnpv | PASS | 2024-05-20 → 06-03: IR −2,191.991600 == Δnpv −2,191.991600. ×2.5 at quantity 2.5; the receiver negates. Across the coupon: −19,147.1663 == Δnpv. |
| IRGamma diagonal vs IRGammaParallel (recorded) | PASS | The config ladder equals the independently calibrated Hessian's diagonal to 1e-9. Fresh 10y on 2024-01-03: diagonal sum −0.481748 vs IRGammaParallel −0.855965 (ratio 0.563); full-Hessian sum −0.855348 (ratio 0.9993, asserted within 1%). Fresh on 2024-05-20: −0.462401 vs −0.816778 (0.566); Hessian 1.0000. Seasoned (traded 2024-01-03, on 2024-11-04): −0.271336 vs −0.788942 (0.344); Hessian 0.9105. The 2Y/5Y cross terms carry the rest; for a seasoned trade between pillars the 4-pillar risk curve also differs from the dense curve. Recorded, not asserted. |
| IRDiscountDeltaParallel vs dv01 (recorded) | PASS | ATM on its trade date: 4.95 vs dv01 838.02 (ratio 0.0059) and 6.11 vs 812.56 (0.0075). Seasoned (npv 39,932.65): −11.05 vs 854.57 (−0.0129). Discount plus projection-only matches the full parallel shift to 1e-7 relative (872.5710 vs 872.5711). Payer struck ATM−50 (npv 40,628.10): −15.31, which is < 0 as it must be. |
| Theta·365 vs IRTheta (recorded) | PASS | Equal by construction (1881.6296 on 2024-11-04). Coupon day 2025-01-08 (n = 1): config 9,506.2458/day vs T2's formula 6.3944/day. The translated par moved −12.733141bp; coupon today 18,106.4971. On 2025-01-07 both give 8.5022/day. The test's independent expectation divides the par term by n from `calendar_advance` (n = 1 on both dates). |
| coupon step over a weekend (new) | PASS | `pnl_explain_table` coupon step, `ir_pnl_definition(vega=False, vanna=False, volga=False)` with `Cashflows`. 1-day, 2025-01-08 → 01-09: coupon 18,106.4971, Theta(t0) 9,506.2458/day, PNL_theta 9,506.2458, PNL_delta −12,042.0596, residual 1,571.2431 (8.68%). Weekend, 2024-03-15 → 03-18: coupon 13,714.5648, Theta(t0) 4,120.2987/day, PNL_theta 12,360.8962 (= 3 × Theta), PNL_delta −10,913.2859, residual 1,365.2170 (9.95%). Before the fix: Theta 12,361.7260/day, PNL_theta 37,085.1780, residual −23,359.0649 (170.32%). |
| dead par across maturity | PASS | 1y payer: t_a 2025-01-03 (a Friday), t_m 2025-01-06, PV(t_a) 4,487.33, par 526.507952 → 526.504969bp. Maturity step: economic 1.3119, PNL_delta −0.3039, PNL_theta 1.5221, residual 0.0936 (before the decision-2 jump spread: PNL_theta 1.6128, residual 0.0030; bound 1e-3 × PV = 4.49). Drop step 2025-01-09: actual −4,489.7097, cash +4,489.7097, residual 0.000000. Run Σ abs(residual) 92.19, max 4.01. |
| `check_asset` no FAIL, every `contract[...]` row PASS | PASS | In-test (`run_checks`, warm caches): PASS=176 WARN=3 FAIL=0 SKIP=2 INFO=2. All 27 `contract[...]` rows PASS, along with `contract_declarations`, `swap_*`, `ir_gamma_ratio`, `ir_taylor` (1.6%) and `ir_cashflows`. WARN: `ir_cashflow_drop` (8.7%), `ir_theta` and `ir_ladder_sum[IRGamma]`; see "check_asset" below. SKIP: `ir_dead_levels` (the 10y probe ends in 2034) and `fx_round_trip`. |

### check_asset (CLI, 2026-10-02)

`check_asset.py configs/assets/usd_sofr_ois_interest_rate_swap.yaml --date 2024-01-03 --date 2024-02-05`, under
`PRICEBT_LIVE_ARBS=1`: **PASS=175 WARN=4 FAIL=0 SKIP=2 INFO=2**, 10s. The CLI's one extra WARN against the in-test
run is `performance` (a cold `market` load of 1.3s).

- **`ir_cashflow_drop`: WARN, 8.7% of the flow.** Verbatim: "2025-01-08 -> 2025-01-09 across payment date
  2025-01-09: Price change 19,147.2 with flow -18,106.5; residual after delta 12,042.1 + gamma 76.0987 + theta
  -9,506.25 = -1,571.24 (8.7% of the flow)". The probe is the receiver, so the signs are the payer's negated. It
  is the same −a·Δpar residual as the `pnl_explain_table` 1-day step (decision 2). The row measures the move
  with `IRFwdRate`. Its market-only correction (`IRFwdRate` on t1 priced on t0's market through a `CloseMarket`
  override) does not apply to ARBS: an ARBS market values from its own date, so the override gives the whole move
  back. The row therefore stays WARN and cannot turn into a PASS here (`docs/v2/DECISIONS_LOG.md` 2026-10-01).
  The probe step has n = 1, so the decision-2 fix does not change it.
- `ir_theta`: WARN, ratio 0.0375 (Theta −3.86654/day vs implied carry −103.033/day on 2024-02-05 → 02-06). The
  implied carry absorbs the annuity `dv01`'s off-market error, as the row itself explains (R2-1).
- `ir_ladder_sum[IRGamma]`: WARN, 45.88% (diagonal ladder 0.445836 vs `IRGammaParallel` 0.823719): the cross
  terms the diagonal drops (decision on `IRGamma` above).
- `ir_taylor` PASS (1.6%), `ir_gamma_ratio` PASS, `swap_theta` PASS (payer 1,411.29, receiver −1,411.29).

### T2 tests re-baselined to R3 (not loosened)

- **A-GAMMA-FD:** the independent finite difference now uses the contract's chain-rule formula. It matches
  `gamma` at 0.0000% on all 5 dates; for example −0.855965 on 2024-01-03, where the old formula gave
  −0.772266.
- **A-MATURE:** `PNL_delta == −PV(t−1)` asserted the old jump of par to fixed_rate. It now asserts that the
  maturity step's residual and PNL_delta are each < 1e-3·PV (measured: 0.0030 and −0.3039 against PV
  4,487.33; 0.0936 and −0.3039 after the decision-2 jump spread). It also asserts that the final coupon leaves npv on the step where `cash_paid_to_date` gains
  it, with economic < 1 USD. The old `unsettled` guard would fail this check: decision 1 measured its
  −4,489.71 / +4,489.71 pair.
- **Other changes, from the same rerun:**
  - **A-CASH:** the residual with cash is now 1,571.24 (8.7% of the coupon); at T2 it was 11,072 (61%),
    which T2-C had explained as a moneyness residual.
  - **A-ROLL:** r2 0.999905, residual_share 9.44e-5.
  - **A-GAMMA-USE:** with gamma vs without, 202.07 vs 240.02 (10y) and 862.13 vs 1,014.97 (30y).
  - **A-IDENT:** worst overage −1.81, unchanged.
- **2026-10-02 rerun, after the decision-2 jump spread** (Friday Thetas move by about 0.1 USD per step):
  A-CASH residual with cash 1,571.24 (unchanged: n = 1); A-ROLL r2 0.999905, residual_share 9.44688e-5;
  A-GAMMA-USE 202.05 vs 240.02 (10y) and 862.10 vs 1,014.97 (30y); A-DAILY r2 0.999560, residual_share
  4.39376e-4; A-MATURE residual 0.0936; A-IDENT worst overage −1.81.

### Side effects observed

Exactly the documented one: today's (2026-10-01) fixings-cache folder exists and is **empty**. There was
no network attempt: every scratch probe ran with outbound `connect` patched to raise, and none raised.
`test_no_network_load_market_and_fixings_cache_unchanged` passed in the rerun. Nothing was written to the
curve store. Live reads in this tier:
- the contract file, run once;
- the pnl and base files, run once;
- two direct-call mutation runs (the dead-par and coupon-day Theta tests against scratch variants of the
  config with the old conventions; both tests failed, as they must);
- about a dozen single-shot scratch probes.

All were deliberate, none in a loop. A gamma ladder computed on a market leaves that market's shared
`_risk_model` solver unchanged: a delta ladder computed after it is identical (max difference 0.0).

**2026-10-02 (the Theta jump fix):** the same single side effect. Today's fixings-cache folder
`USD-SOFR-1D_fixings/2026-10-02` exists and is **empty**, and so is 2026-10-01's. Both scratch probes ran with
non-loopback `connect` patched to raise; none raised. (The patch must let loopback through: ARBS's
`FixingsFetcher` builds an asyncio `ProactorEventLoop` at import, and its self-pipe is a `127.0.0.1`
socketpair.) Live reads in this session:
- two scratch probes, before and after the fix: short backtests around the two coupon steps, plus the
  calendar-vs-grid check (a first attempt crashed at ARBS import, on the loopback socketpair, before any
  read);
- the two targeted tests (`-k "weekend or coupon_day"`), run once;
- one mutation run (the `/ n` removed) of the weekend test, which failed at 170.3% as it must;
- the full three-file run, once;
- the `check_asset.py` CLI, once.

All were deliberate, none in a loop. No date after 2026-08-20 was read. The ARBS repository was not
written. Its `git status` shows changes that predate this work and are outside `MDP/IRSwaps`: `CLAUDE.md`,
`MDP/CitiVelocityExcel/catalog/*.json`, and deletions under `MDP/FixedRateBonds/reference_data_cache`. The earliest dates were the 2023
fixings of the seasoned weekend-case swap, which came from the served market's own fixings series; no 2023
market was loaded.

**Final rerun at `e757865`** (after the skills review fixes changed `ir_cashflow_drop`'s stage-2 roll term and moved the SIMM lists into `pricebt.risk.contracts`), 2026-10-02: `tests/test_live_arbs_contract.py` + `tests/test_live_arbs_pnl.py` + `tests/test_live_arbs.py` **41 passed in 400.9s**, including `test_check_asset_no_fail_and_every_contract_row_passes` and A-CHECK (no FAIL on this config).

## US Treasury bonds (revision 4, branch `v2-bonds`): BLOCKED, no store-only access path

**Status, 2026-10-03: the live tier for US Treasuries is blocked, and no ARBS bond config ships.**
The task's rule was explicit: find a store-only access path and a safe date cap for prices and for repo, and "if
no store-only path exists for prices or for repo, do not work around it. Document it, keep the live tier blocked,
and finish everything else." Neither exists through ARBS's own API, so nothing here imported, constructed or ran
any ARBS bond object. The findings come from reading the source (`MDP/FixedRateBonds`, `Query/FixedRateBonds`,
`definitions/FixedRateBonds.py`, `MDP/USMoneyMarkets`, the fixings fetcher) and from four probes that read files
only (an immutable read-only SQLite open of a diskcache shard, a `read_parquet`, a QuantLib enum read, greps). No
ARBS module was imported and no store or cache was written. The bond pricing and financing contract, the engine's
holding cash and every identity are built and tested on the toy library instead
([`BOND_DESIGN.md`](BOND_DESIGN.md) §5).

### Prices: every FedInvest read refreshes reference data keyed on today

- `FixedRateBondsMDP` (default source `USTS_FEDINVEST_WSJ_LIVE-QL`) calls
  `update_reference_data(source="fiscaldata")` on every request path: `get_data` (`FixedRateBondsMDP.py:1539`),
  `bulk_get_data`'s per-timestamp worker (`:2432`, eight threads, no lock), and the intraday and cash-spline paths
  (`:847`, `:1119`, `:1268`, `:1405`, `:2107`, `:2283`, `:2872`).
- `update_reference_data` (`reference_data_cache/ust_reference_data.py:71-123`) keys the cache on **today's** last
  US government business day (`:82`), never on the requested date. It then:
  1. runs `mkdir` inside the ARBS repository (`:20`, `:89`);
  2. on a miss, downloads from `api.fiscaldata.treasury.gov` (`_fetch_fiscaldata`, `:99`);
  3. writes a parquet (`:117`);
  4. deletes every dated directory but the newest three (`_cleanup_old_cache_dirs`, `:24-38`, `:121`).
  The newest stored parquet is 2026-09-04, so the next call on any date would take the network path. The ARBS
  checkout already shows deletions under `MDP/FixedRateBonds/reference_data_cache` that predate this work
  (noted in the R3 section above): that is this cleanup at work.
- Even with outbound sockets blocked, step 1 writes into the read-only ARBS repository before the download is
  attempted, and the date class (`live`/`buffer`/historical) moves with the wall clock (`buffer` = within three US
  business days of today, `:2072-2073`). So a network-blocked probe of the MDP is not safe either, and none was run.
- `bulk_get_data(timestamps, cusips, *, show_tqdm, force_refresh, max_workers)` (`:2330-2338`) has no
  `ignore_cache_miss`-style flag. `offline` exists only on the Citi Velocity (Excel) branches. Historical
  `bulk_get_data` never reads the pricer cache; on a FedInvest cache miss `FedInvestFetcher.runner` posts to
  `savingsbonds.gov` (with `verify=False`) and writes the cache. Every diskcache read also writes an LRU access
  time.

### Repo: no repo series in ARBS

- `MDP/USMoneyMarkets` holds H.4.1 reserves, ON RRP volume, the TGA and GDP (`h41.py`, `bea_gdp.py`,
  `dbnomics_fetcher.py`), no repo rate.
- The fixings fetcher serves USD-SOFR-1D and EFFR; TGCR and BGCR are not served (the Citi Velocity fixings note
  says so explicitly). The Citi repo store (`MDP/CitiVelocityExcel/repo/store.py`) reads a parquet that does not
  exist, and its refresh opens Excel.
- ARBS's own bond carry (`Query/FixedRateBonds/carry_roll.py`) finances at the SOFR fixing (EFFR fallback), not at a
  repo rate. A SOFR proxy for general collateral is reachable store-only through the swap config's path
  (`IRSwapsMDP`'s market `index()`, within `_LAST_SAFE`), but it does not unblock prices.

### What would unblock it (needs the user's approval; not done)

A reader that bypasses the MDP, which the IRS research note (R06 §8 item 3) already classed as needing approval:
- prices: the FedInvest diskcache shards (`%LOCALAPPDATA%\ARBS\Cache\diskcache\dump\FedInvest_Prices_Cache\00{0..7}\cache.db`,
  key `pd.Timestamp(date)`, value a frame `cusip, type, coupon, offer_price, bid_price, eod_price`; 4,182 stored dates,
  2006-01-31..2026-08-21, dense from 2010), opened `?mode=ro&immutable=1`, dropping `eod_price <= 0` rows (4,457 such
  note/bond rows exist and the historical paths do not guard them);
- reference data: the 2026-09-04 fiscaldata parquet (coupon, issue and maturity by CUSIP; notes and bonds only);
- pricing: `QLFixedRateBondPricer` (ACT/ACT ISMA, US government calendar, T+1, semiannual). Use `clean_price()` +
  `accured()`, not its `dirty_price()`/`npv()`, which pass `ql.Semiannual` where QuantLib expects a compounding
  (`ql.Semiannual == ql.Continuous == 2`); its `pv01()` without a notional raises;
- repo: the SOFR fixings CSV as a general-collateral proxy (no special repo exists in ARBS);
- date cap: 2026-08-21, the newest stored FedInvest date (and `_LAST_SAFE` 2026-08-20 for the SOFR path).

With that approval, the config (`configs/assets/usd_ust_bond.yaml`, keyed by CUSIP) would map the 40-row Bond
contract from those primitives exactly as `skills/pricebt-connect-pricing-library/references/config-template-bond.yaml`
lays out, and the opt-in `tests/test_live_arbs_bond.py` would carry the checks the task lists (clean + accrued = dirty,
yield ↔ price, duration and convexity against bumps, a coupon date, a financed one-month carry backtest, and
`check_asset.py` with no FAIL).
