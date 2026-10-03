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
  changed from `NaN` to `fixed_rate*1e4` (T2-A). `dv01`, `npv` and `par_rate` on a LIVE trade: byte-for-
  byte unchanged.
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
