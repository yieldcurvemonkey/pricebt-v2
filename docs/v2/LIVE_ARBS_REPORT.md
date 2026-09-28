# Live ARBS run report

**Status: deferred.** [`tests/test_live_arbs.py`](../../tests/test_live_arbs.py) (IMPLEMENTATION_PLAN.md
P5.2) was written but never run in autonomous mode: it imports ARBS the first time a test in it
actually executes, and ARBS can reach a production database, Excel/COM and the network, so the
first run needs your own approval. This file gives you the exact commands, explains what each of
the 6 checks verifies and why, and leaves the results as a template for you to fill in after you
run it. No numbers below are real yet — every `<TO BE FILLED IN BY THE USER AFTER RUNNING>`
placeholder is exactly that.

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

<!-- Fill in after running with PRICEBT_LIVE_ARBS=1. Do not fabricate numbers. -->

- **Date run:** `<TO BE FILLED IN BY THE USER AFTER RUNNING>`
- **Overall:** `<TO BE FILLED IN BY THE USER AFTER RUNNING>` (all 7 tests passed / list failures)

| Check | Result | Notes |
|---|---|---|
| 1. load_market reference dates + no-market cases | `<TO BE FILLED IN BY THE USER AFTER RUNNING>` | |
| 2. 10y ATM payer on 2024-05-20 (npv / dv01 / par_rate) | `<TO BE FILLED IN BY THE USER AFTER RUNNING>` | record the actual npv, dv01, par_rate values |
| 3. Seasoned mark on 2024-05-24 | `<TO BE FILLED IN BY THE USER AFTER RUNNING>` | record the actual seasoned npv |
| 3b. Maturity / maturity+1b pricing | `<TO BE FILLED IN BY THE USER AFTER RUNNING>` | note whether the fallback guard triggered |
| 4. delta_ladder sum vs. dv01 | `<TO BE FILLED IN BY THE USER AFTER RUNNING>` | record both values and the % difference |
| 5. Short 040304 run | `<TO BE FILLED IN BY THE USER AFTER RUNNING>` | runtime seconds; ledger row count; if empty, explain why nothing fired |
| 6. No-network / no-curve-store-write check | `<TO BE FILLED IN BY THE USER AFTER RUNNING>` | fixings-cache listing before/after (confirm only an empty today-dated folder appeared); curve-store listing+mtime before/after (confirm zero change) |

**P5.2 gate:** once every check above is filled in and green (or a failure is understood and
recorded), P5.2 is complete and the deferral in IMPLEMENTATION_PLAN.md §9 can be closed out.
