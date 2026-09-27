# Suite reproduction (spec 9.5, SC6): the migrated real-data suite against the recorded results

Written by the suite-migration agent (agent B). Everything below was measured, not assumed; the comparers are `tools/repro/compare.py` and `tools/repro/repro_table.py` (they compare two result
directories); the batch runners and the table generator were working scripts, removed in the final cleanup (change log section 13.4).

## 1. What was compared

* **Recorded**: `results/<name>/` (the frames `equity`, `trades`, `positions`, `layers`, `orders`, `events`, `errors` and the manifest) written by the pre-refactor suite tools. Never
  overwritten: every new run wrote to `results_new/<name>/` (the migrated configs' `outputs.dir` points there).
* **New**: the migrated configs in `configs/suite/` (ONE instrument spec per asset class, the side / tenor / notional / security of every trade as terms of its action, plain YAML
  costs and signals, the test-support providers `support.curves:CurveStore`, `support.ust_eod:UstEod`, `support.ust_minute:UstMinute`), run through the CLI path
  (`pricebt.api.build(config, sets=..., stack=[overlay files])`) under the **rateslib stack** (`configs/adapters/rateslib_swap.yaml`, `rateslib_bond.yaml`; s07 takes both), on **the same
  window as the recorded run** (the windows of the old `tools/run_suite_*.py`, including the s08 August / DST-end variants and the single-session s09).
* **Comparison** (`tools/repro/compare.py`): row-by-row on the equity frame (equity, cash, tcost, positions_value, step_pnl, financing, flows, dv01 and the five cumulative layer
  columns), the trade ledger (ts, kind, action, template, reason, quantity, pv, cash, tcost) and the position table (entry / exit instants and reasons, quantities, pv, pnl, cash, financing,
  tcost). The comparer was run on known answers first: a directory against itself (exactly 0 everywhere) and against a copy with one equity value moved by 123, one layer by 55 and one
  position pv by 1 (each detected at exactly that size); `python tools/repro/compare.py` repeats both.
* Windows, run counts and trade counts are those of the recorded runs; the swap suite's S10 (swaption straddle) is not reproduced, it was deleted (Q15, pre-approved).

## 2. Result: rateslib stack, full recorded windows

Tolerances (stated per quantity, not per config): **total P&L and equity 1e-6 relative to max abs equity; prices, marks, cash, financing and layers 1e-9 relative to max abs equity (the brief's
starting values, kept)**; trades, positions, entry / exit instants and exit reasons must be identical. Nothing was loosened: every quantity meets those tolerances (in almost every case with an exact 0.0) EXCEPT
the ones section 3 explains, which are not tolerated but explained and then shown to vanish (section 4).

### 2.1 Swap suite

| config | window | trades old/new | positions old/new | P&L old | P&L new | max abs diff (equity) | max rel diff (of max abs equity) | verdict | explanation |
|---|---|---|---|---|---|---|---|---|---|
| s1_swap_carry_eod | 2022-01-03..2025-12-31 | 95/95 | 48/48 | -2,284,573.87 | -2,284,126.44 | 447.4374 | 1.62e-04 | PASS, explained (exit costs / dv01 column) | exit costs only: the cost of a close is 0.1 (0.25 for the swap leg of s07) x |dv01| of the position, and the dv01 of a STARTED swap is defined differently (section 3); every open, cash, mark and layer is bit-identical |
| s1m_swap_pay_mirror | 2022-01-03..2025-12-31 | 95/95 | 48/48 | 2,124,415.71 | 2,124,863.14 | 447.4374 | 1.66e-04 | PASS, explained (exit costs / dv01 column) | exit costs only: the cost of a close is 0.1 (0.25 for the swap leg of s07) x |dv01| of the position, and the dv01 of a STARTED swap is defined differently (section 3); every open, cash, mark and layer is bit-identical; the mirror differs by the same amount |
| s1f_swap_carry_full_history | 2018-06-01..2026-09-04 | 199/199 | 100/100 | -1,511,722.90 | -1,510,906.48 | 816.4220 | 3.76e-04 | PASS, explained (exit costs / dv01 column) | exit costs only: the cost of a close is 0.1 (0.25 for the swap leg of s07) x |dv01| of the position, and the dv01 of a STARTED swap is defined differently (section 3); every open, cash, mark and layer is bit-identical; plus 1e-9 floating-point round-off in the marks of the 8-year run (last digit of a 1e6 number) |
| s2_steepener_2s10s | 2022-01-03..2025-12-31 | 238/341 | 120/172 | -1,677,471.97 | -1,693,899.49 | 36,137.2601 | 1.87e-02 | EXPLAINED (different trades) | the net-dv01 band of the rebalance trigger reads the dv01 of STARTED swaps (section 3): the recorded (analytic) figure barely drifts inside a month, the adapter's (ladder sum, the real parallel delta) drifts by the 2Y leg's roll-down, so the book re-neutralises 76 times instead of 24; different trades from the first rebalance (2022-02-01) on. With the recorded definition bound back the run is bit-identical (section 4) |
| s3_fly_2s5s10s | 2022-01-03..2025-12-31 | 285/285 | 144/144 | -564,480.87 | -562,467.19 | 2,013.6769 | 3.23e-03 | PASS, explained (exit costs / dv01 column) | exit costs only: the cost of a close is 0.1 (0.25 for the swap leg of s07) x |dv01| of the position, and the dv01 of a STARTED swap is defined differently (section 3); every open, cash, mark and layer is bit-identical |
| s4_meanrev_2s10s | 2021-10-01..2025-12-31 | 80/80 | 40/40 | 840,251.13 | 840,911.99 | 660.8552 | 6.79e-04 | PASS, explained (exit costs / dv01 column) | exit costs only: the cost of a close is 0.1 (0.25 for the swap leg of s07) x |dv01| of the position, and the dv01 of a STARTED swap is defined differently (section 3); every open, cash, mark and layer is bit-identical |
| s07_swap_spread | 2018-05-01..2025-12-31 | 366/366 | 184/184 | -615,890.03 | -614,138.60 | 1,751.4345 | 2.36e-03 | PASS, explained (exit costs / dv01 column) | exit costs only: the cost of a close is 0.1 (0.25 for the swap leg of s07) x |dv01| of the position, and the dv01 of a STARTED swap is defined differently (section 3); every open, cash, mark and layer is bit-identical; the bond leg is bit-identical |
| s08_swap_intraday_min | 2026-03-09..2026-03-13 | 10/10 | 5/5 | 61,437.80 | 61,437.80 | 0 | 0 | PASS, bit-identical | none: every column of the equity, trade and position frames is bit-identical |
| s08_swap_intraday_min_aug | 2026-08-03..2026-08-07 | 10/10 | 5/5 | -3,858.21 | -3,858.21 | 0 | 0 | PASS, bit-identical | none: every column of the equity, trade and position frames is bit-identical |
| s08_swap_intraday_min_nov_dst | 2025-11-03..2025-11-03 | 2/2 | 1/1 | -9,470.54 | -9,470.54 | 0 | 0 | PASS, bit-identical | none: every column of the equity, trade and position frames is bit-identical |
| s08b_swap_minute_hold_week | 2026-03-09..2026-03-13 | 1/1 | 1/1 | 83,587.02 | 83,587.02 | 0 | 0 | PASS, explained (exit costs / dv01 column) | none in P&L; `measure_dv01` column only (section 3) |
| s11_buy_hold_10y | 2022-01-03..2025-12-31 | 1/1 | 1/1 | -2,226,611.92 | -2,226,611.92 | 0 | 0 | PASS, explained (exit costs / dv01 column) | none in P&L; the recorded `measure_dv01` column of the started swap is the analytic PV01 (section 3) |
| s12_always_flat | 2022-01-03..2025-12-31 | 0/0 | 0/0 | 0.00 | 0.00 | 0 | 0 | PASS, bit-identical | none: every column of the equity, trade and position frames is bit-identical |

Max abs difference by component (0 = bit-identical; `wall s` = engine seconds of the new run):

| config | cash | tcost | positions_value | carry | roll | delta | convexity | unexplained | measure_dv01 | wall s (engine) |
|---|---|---|---|---|---|---|---|---|---|---|
| s1_swap_carry_eod | 0 | 447.4374 | 0 | 0 | 0 | 0 | 0 | 0 | 346.2098 | 149 |
| s1m_swap_pay_mirror | 0 | 447.4374 | 0 | 0 | 0 | 0 | 0 | 0 | 346.2098 | 162 |
| s1f_swap_carry_full_history | 4.66e-10 | 816.4220 | 4.66e-10 | 1.86e-09 | 1.40e-09 | 1.86e-09 | 0 | 1.45e-09 | 427.4927 | 283 |
| s2_steepener_2s10s | 34,272.2848 | 553.9349 | 16,399.7454 | 14.4922 | 20,117.0311 | 32,540.6505 | 458.2194 | 0.2887 | 301.4947 | 189 |
| s3_fly_2s5s10s | 0 | 2,013.6769 | 0 | 0 | 0 | 0 | 0 | 0 | 108.8473 | 215 |
| s4_meanrev_2s10s | 0 | 660.8552 | 0 | 0 | 0 | 0 | 0 | 0 | 563.0274 | 54 |
| s07_swap_spread | 0 | 1,751.4345 | 0 | 0 | 0 | 0 | 0 | 0 | 418.5718 | 283 |
| s08_swap_intraday_min | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 11 |
| s08_swap_intraday_min_aug | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 10 |
| s08_swap_intraday_min_nov_dst | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 2 |
| s08b_swap_minute_hold_week | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 57.9420 | 124 |
| s11_buy_hold_10y | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1,758.7667 | 156 |
| s12_always_flat | 0 | 0 | 0 |  |  |  |  |  | 0 | 0 |

### 2.2 Bond suite (bond and cross-product)

| config | window | trades old/new | positions old/new | P&L old | P&L new | max abs diff (equity) | max rel diff (of max abs equity) | verdict | explanation |
|---|---|---|---|---|---|---|---|---|---|
| s05_ust_ct10_financed | 2018-05-01..2025-12-31 | 183/183 | 92/92 | -1,066,384.75 | -1,066,384.75 | 0 | 0 | PASS, bit-identical | none: every column of the equity, trade and position frames is bit-identical |
| s06_ust_2s10s_steepener | 2018-05-01..2025-12-31 | 446/446 | 224/224 | -3,204,843.33 | -3,204,843.33 | 0 | 0 | PASS, bit-identical | none: every column of the equity, trade and position frames is bit-identical |
| s09_ust_intraday_mr | 2026-02-18..2026-03-13 | 160/160 | 80/80 | -151,594.88 | -151,594.88 | 0 | 0 | PASS, bit-identical | none: every column of the equity, trade and position frames is bit-identical |
| s09_ust_intraday_mr_20260309 | 2026-03-09..2026-03-09 | 10/10 | 5/5 | -5,384.54 | -5,384.54 | 0 | 0 | PASS, bit-identical | none: every column of the equity, trade and position frames is bit-identical |
| ctrl_s10b_ust_buy_and_hold | 2018-05-01..2025-12-31 | 1/1 | 1/1 | 162,804.84 | 162,804.84 | 0 | 0 | PASS, bit-identical | none: every column of the equity, trade and position frames is bit-identical |
| ctrl_s12b_ust_always_flat | 2018-05-01..2025-12-31 | 0/0 | 0/0 | 0.00 | 0.00 | 0 | 0 | PASS, bit-identical | none: every column of the equity, trade and position frames is bit-identical |

| config | cash | tcost | positions_value | carry | roll | delta | convexity | unexplained | measure_dv01 | wall s (engine) |
|---|---|---|---|---|---|---|---|---|---|---|
| s05_ust_ct10_financed | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 29 |
| s06_ust_2s10s_steepener | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 28 |
| s09_ust_intraday_mr | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 31 |
| s09_ust_intraday_mr_20260309 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 2 |
| ctrl_s10b_ust_buy_and_hold | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 25 |
| ctrl_s12b_ust_always_flat | 0 | 0 | 0 |  |  |  |  |  | 0 | 0 |

**Summary.** 19 runs compared. 10 are bit-identical in every column (equity, cash, marks, layers, dv01, ledger, positions): s12, s08 (3 variants), s05, s06, s09 (2), ctrl_s10b, ctrl_s12b.
2 are bit-identical except the `measure_dv01` column (s11, s08b). 6 differ ONLY by the cost of the exits and the dv01 column (s1, s1m, s1f, s3, s4, s07). 1 (s2) trades differently. Every
difference has one cause (section 3), which was isolated by bisection and removed (section 4). Marks, cash, financing, coupon flows and all layers are identical to the last bit
in ALL runs (s1f: 2e-9, floating-point round-off on numbers of 1e6).

## 3. The one difference and its cause: the dv01 of a STARTED swap (a difference between the recorded files and the pre-refactor CODE, not introduced by the refactor)

Every difference in section 2 comes from one definition. The recorded suite reported, as `dv01` of a swap that has already started, the **analytic PV01 of the fixed leg**
(`RLSwap._active.analytic_delta(leg=1)`). The pre-refactor `RLSwap.dv01` (baseline tree, `pricebt-baseline/src/pricebt/contrib/rateslib/swap.py`) and the current adapter both report, for a
started swap, **the sum of its delta ladder**, because the analytic figure keeps the accrued coupon, which no longer moves with rates (documented in the module docstrings and in
`docs/design/11-quantlib-conventions.md` 5, 6, 11). Evidence (s11, the receiver of 1e7 struck 2022-01-05):

| quantity on 2022-03-31 | value |
|---|---|
| recorded `measure_dv01` (`results/s11_buy_hold_10y`) | -9075.74666653814 |
| analytic fixed-leg PV01, current code | -9075.74666653814 (equal to the last digit: the recorded figure IS the analytic one) |
| **pre-refactor baseline code, re-run today** (`pricebt-baseline`, old config, old stores, `PYTHONDONTWRITEBYTECODE=1`, nothing written) | **-8529.0821** (its equity and layers equal the recorded ones: 0.0) |
| sum of the delta ladder, migrated suite | -8529.082119355166 |
| +-1bp parallel par-rate bump on the risk curve (independent finite difference) | -8529.084810577915 (agrees with the ladder sum to 3e-7) |

**Why the recorded files differ from the pre-refactor code**: they were written at 07:49-07:50 on 2026-09-26 (`results/s11_buy_hold_10y/equity.parquet` 07:49:49) and the started-swap rule of `RLSwap.dv01`
in the baseline tree was last edited at 07:59:48 (`swap.py`), ten minutes later and before the baseline snapshot of 09:10. The old engine DID pass `ctx` to `dv01` (`call_method` injects it
when the callable declares it), so this is not a dead branch: the recorded artefacts are simply older than the rule. The pre-refactor code would not reproduce the recorded s2 either. The
migrated suite agrees with the pre-refactor code: s2 over 2022-01-03..2022-03-31 (three rebalances) gives the SAME 15 trades and 9 positions, equity within 1.8e-3 (3e-9 of max abs equity) and
dv01 within 5e-5 (two short s2 runs of the recorded and of the migrated definition, not kept).

The new definition is the market dv01 (the spec's S7 dollar delta: "currency per +1bp of the instrument's own rate"); the recorded one overstates it (by 6.4% after three months on a 10Y, proportionally more on
a short swap). It affects exactly the places where the dv01 of a swap that is already open is read:

1. the `measure_dv01` column (s11, s08b: the only difference of those runs);
2. **the cost of every exit**: a `scaled` cost with `scaling_type: "measure:dv01"` charges 0.1 x |dv01| of the position on the close (0.25 for the swap leg of s07). Opens are identical to the
   last bit (the swap is not started at entry), closes are not: s1 total cost differs by 447.44 (all of it on the closes), s3 by 2,013.68, s4 by 660.86, s07 by 1,751.43;
3. **the rebalance trigger of s2**: `rebalance_trigger` on the book's net dv01 with a band of +-100 reads the dv01 of the open (started) legs. The 2Y leg's true dv01 loses about 1/24 of itself a month as it ages
   while the 10Y leg's loses 1/120, so the book measured with the market dv01 leaves the band far more often than with the recorded analytic figure (which is flat until the first coupon date):
   76 rebalances instead of 24, 172 positions / 341 trades instead of 120 / 238, maximum |net dv01| 370 instead of 216 (each on its own definition), total P&L -1,693,899 instead of -1,677,472
   (-0.98%: costs 400 lower, gross P&L 16,827 lower). The trades agree up to 2022-01-18, where the recorded book re-neutralises (its analytic dv01 left the band) and the new one does not
   until 2022-02-01; from there they diverge.

So this is neither a defect of the migration nor a change made by the refactor. It is a **stale-artefact** difference: `results/` was recorded with the analytic definition, the code (before and after
the refactor) uses the market dv01. It is not in the change-log's list of deliberate differences (sections 7 and 10 of `docs/design/11-refactor-changelog.md` mention "dv01 is a dollar delta" and the
layer fold, not this rule), so it should be recorded there (the rows are in the change log, section 12.2), and `results/` should be refreshed from the migrated suite once the numbers are accepted
(nothing in `results/` was touched). It sits in the adapter, not in core, so no config was stopped and nothing was patched.

## 4. Bisection: the recorded definition bound back reproduces the recorded results bit for bit

`configs/adapters/rateslib_swap_recorded_dv01.yaml` is a stack overlay that rebinds ONLY the swap's `dv01` to the recorded definition (`tools/swap_suite_support.py:recorded_dv01`, the analytic PV01;
the run needs `--set "registry.allow=[support, tools.swap_suite_support]"`). One hypothesis, one change; everything else (configs, providers, adapter, engine) is the same as in section 2:

| config | trades old/new | positions old/new | P&L old | P&L new (recorded dv01) | max abs diff over equity, cash, tcost, marks, layers, dv01 |
|---|---|---|---|---|---|
| s1_swap_carry_eod | 95/95 | 48/48 | -2,284,573.8726 | -2,284,573.8726 | 0 |
| s1m_swap_pay_mirror | 95/95 | 48/48 | 2,124,415.7071 | 2,124,415.7071 | 0 |
| s1f_swap_carry_full_history | 199/199 | 100/100 | -1,511,722.8995 | -1,511,722.8995 | 1.86e-09 |
| s2_steepener_2s10s | 238/238 | 120/120 | -1,677,471.9676 | -1,677,471.9676 | 0 |
| s3_fly_2s5s10s | 285/285 | 144/144 | -564,480.8692 | -564,480.8692 | 0 |
| s4_meanrev_2s10s | 80/80 | 40/40 | 840,251.1328 | 840,251.1328 | 0 |
| s07_swap_spread | 366/366 | 184/184 | -615,890.0313 | -615,890.0313 | 0 |
| s08b_swap_minute_hold_week | 1/1 | 1/1 | 83,587.0198 | 83,587.0198 | 0 |

Every quantity (equity, cash, tcost, marks, all layers, dv01 column, trades, positions) is identical to the last bit for s1, s1m, s2, s3, s4, s08b, s07 (s1f: 2e-9 round-off). So the migrated
configs, the snapshot providers, the `wrap` and the engine reproduce the recorded suite exactly, and the definition of a started swap's dv01 is the only difference. This is pinned by
`tests/test_suite_swaps.py::test_with_the_recorded_dv01_definition_s2_reproduces_the_recorded_run_exactly_and_without_it_the_rebalances_differ` (a 3-month s2 window with two recorded
rebalances; the default binding trades on other days) and, for what does not depend on the definition, by `test_short_windows_reproduce_the_recorded_pre_refactor_results`.

Speed note (for the adapter's owner): the ladder-sum dv01 calibrates a risk curve (~80 ms) per snapshot for every started swap, and the suite records `measure_dv01` at every point, so the swap runs are
1.8 to 2.8 times slower than with the analytic figure (wall seconds of the run, same machine and window: s1 154 vs 56, s2 194 vs 75, s3 220 vs 106, s4 59 vs 33); the recorded runs took 115, 144, 171 and 69 s.

## 5. The other deliberate differences the brief lists: what was observed

* **Layer definitions folded (roll includes realised fixings; delta = (V_base + cash - X_roll_act) + g dz): no observable effect.** The cumulative carry, roll, delta, convexity and unexplained columns
  and the per-position layers are bit-identical to the recorded ones in every run (s1f: 2e-9), including the seasoned swaps of s1f, s11 and s08b: whatever the fold changed in the
  definitions, it did not move a number of this suite, neither the sum nor the split.
* **`dv01` is a dollar delta**: the swap dv01 of an UNSTARTED swap is identical to the recorded one (the first rows of every `measure_dv01`); the bond dv01 is identical in all bond runs
  (dollar delta per unit as built with its face, negative for a long). The started-swap rule of section 3 is the only difference.
* **Four price-file CUSIPs are no longer quoted**: no effect on any suite run (s05, s06, s07, ctrl_s10b, ctrl_s12b are bit-identical, including the on-the-run resolution of CT2 / CT10 on all 1,912
  days and the five partial 2023 days).
* **Synthetic bond `roll` layer changed**: not applicable (no suite run uses the synthetic market; `configs/synthetic_swap_carry.yaml` has no recorded result and is a smoke test).

## 6. Translations that could have changed a result, and did not

* `pricer_spread` on the `par_rate` lookup (s4): the adapters no longer offer a `par_rate` lookup (D7), so the signal is the `rate` MEASURE of a freshly built 10Y and 2Y swap (a `measure` signal each, a
  `derived` combo with weights 100 / -100). Entries, sides, exits and reasons are identical (80 trades, 40 positions).
* `bond_ytm_signal` (s09): three plain `derived` signals with named inputs over the `rate` measure of a fresh CT10 (zscore window 30, clip +-10, rolling_std 30). 160 trades, 80 positions,
  bit-identical to the recorded run with the provider's bad-tick filter on (`outlier_bp: 5.0`; the 2026-03-12 08:43-08:45 spike lies before the 09:00 session start, so the tool's 08:00-session
  robustness variant that puts it on the grid was not repeated).
* `scaled_cost` / `dv01_cost` factories: plain `{type: scaled, scaling_type: notional | "measure:dv01", scaling_level: ...}`.
* `calendar: nyc` of the backtest: `backtest.calendar` cannot name a fixture file whose name is a banned token, so the swap configs read `calendars/usd_fed.json`, a copy of the calendar the curve store
  serves as the snapshot calendar `usd_fed` (a test asserts they are equal when the fixtures are present).
* Bond conventions name the calendar `us_govt` (the snapshot's Treasury calendar) where the recorded runs used the library's own `nyc`: no coupon date of any bond of the window falls on a day on which the two
  differ, and the results are identical.

## 7. Other stacks: the reference stack (and QuantLib on short windows)

The same base configs run under the dependency-free reference stack by naming its overlay (`configs/adapters/refstack_swap.yaml`, `refstack_bond.yaml`; the bases already name it as their default). Full recorded
windows, default binding, compared with the RECORDED results (so the started-swap dv01 difference of section 3 is included in these numbers):

| config | engine s (reference stack) | engine s (rateslib) | trades old/new | P&L old (recorded) | P&L reference stack | max abs diff (equity, vs recorded) | max abs diff of cash / marks / layers |
|---|---|---|---|---|---|---|---|
| s1_swap_carry_eod | 42.3 | 149.0 | 95/95 | -2,284,573.87 | -2,284,126.44 | 447.4369 | 7.73e-05 |
| s1m_swap_pay_mirror | 41.1 | 162.0 | 95/95 | 2,124,415.71 | 2,124,863.14 | 447.4369 | 7.73e-05 |
| s1f_swap_carry_full_history | 120.6 | 283.2 | 199/199 | -1,511,722.90 | -1,510,906.48 | 816.4199 | 4.55e-05 |
| s2_steepener_2s10s | 44.0 | 188.8 | 238/341 | -1,677,471.97 | -1,693,899.47 | 36,137.0949 | 34,272.1337 |
| s3_fly_2s5s10s | 45.4 | 215.1 | 285/285 | -564,480.87 | -562,467.19 | 2,013.6743 | 4.25e-05 |
| s4_meanrev_2s10s | 16.1 | 54.4 | 80/80 | 840,251.13 | 840,911.99 | 660.8534 | 1.09e-04 |
| s07_swap_spread | 106.7 | 282.7 | 366/366 | -615,890.03 | -614,138.60 | 1,751.4300 | 1.47e-04 |
| s08_swap_intraday_min | 4.5 | 10.6 | 10/10 | 61,437.80 | 61,437.80 | 1.31e-09 | 7.51e-05 |
| s08_swap_intraday_min_aug | 5.3 | 10.1 | 10/10 | -3,858.21 | -3,858.21 | 1.04e-09 | 4.54e-05 |
| s08_swap_intraday_min_nov_dst | 1.1 | 2.3 | 2/2 | -9,470.54 | -9,470.54 | 6.84e-10 | 4.42e-05 |
| s08b_swap_minute_hold_week | 40.3 | 123.8 | 1/1 | 83,587.02 | 83,587.02 | 4.69e-09 | 3.60e-05 |
| s11_buy_hold_10y | 48.1 | 156.4 | 1/1 | -2,226,611.92 | -2,226,611.92 | 3.38e-09 | 4.51e-05 |
| s12_always_flat | 0.0 | 0.0 | 0/0 | 0.00 | 0.00 | 0 | 0 |
| s05_ust_ct10_financed | 12.3 | 29.1 | 183/183 | -1,066,384.75 | -1,066,384.75 | 9.26e-05 | 1.46e-04 |
| s06_ust_2s10s_steepener | 18.4 | 28.4 | 446/446 | -3,204,843.33 | -3,204,843.33 | 0.0024 | 0.0025 |
| s09_ust_intraday_mr | 24.8 | 30.5 | 160/160 | -151,594.88 | -151,594.88 | 7.26e-08 | 1.09e-07 |
| s09_ust_intraday_mr_20260309 | 0.9 | 1.9 | 10/10 | -5,384.54 | -5,384.54 | 2.05e-08 | 2.05e-08 |
| ctrl_s10b_ust_buy_and_hold | 16.6 | 24.9 | 1/1 | 162,804.84 | 162,804.84 | 9.82e-05 | 9.62e-05 |
| ctrl_s12b_ust_always_flat | 0.1 | 0.0 | 0/0 | 0.00 | 0.00 | 0 | 0 |

Short-window cross-stack agreement (in `tests/test_suite_swaps.py` and `tests/test_suite_bonds.py`, one base config, only the factory and the wrap changed): s1 (2024-01-02..2024-02-15) under
rateslib, QuantLib and the reference stack: identical ledger, equity within 6.1e-6 currency (1.7e-11 of max abs equity), layers within 2e-8 (carry, roll), delta / convexity / unexplained within 0.12 for QuantLib
(bump-and-revalue). s05 (2018-05-01..2018-07-31): identical ledger, equity within 6.3e-6 (3.7e-11), financing within 1.7e-8, layers within 4e-5.

## 8. What was not done

* The robustness variants of `tools/run_suite_swaps.py` / `run_suite_bonds.py` (fill_lag 0/1, cost sweeps, band sweep, notional x2, one-day shifts, s4 truncation, zero-price-day demonstrations) were not re-run over
  their full windows: the variants are `--set` overrides of the same configs and their invariants are asserted on short windows by the tests; the tools run them (`python tools/run_suite_swaps.py`,
  `python tools/run_suite_bonds.py`, both with `--stack`). Subsets of both tools were run against the migrated configs and all their gated checks passed (S2 slope regression beta 9,961, S3 fly beta 4,935, S4
  reference z-score entries, S8 machinery, S11 raw-rateslib shadow ledger 0.0 difference; the bond tool's independent S5 recomputation 3.6e-8, factor regression beta -8,406.19 identical to the recorded).
* S10 (swaption straddle) and its variants: deleted, out of scope (Q15, SC16).
* `configs/synthetic_swap_carry.yaml` has no recorded result. It runs (`tests/test_suite_swaps.py::test_the_synthetic_swap_config_runs_and_the_reference_stack_agrees_with_rateslib`: same trades, equity
  within 1e-8 of max abs equity under rateslib and the reference stack) but NOT under QuantLib: `contrib/quantlib/_compat.py` rejects the 7-digit weekmask (`'1111100'`) that `pricebt/testing/synthetic.py`
  gives its calendar (`CFG-CALENDAR: weekmask '1111100' of calendar 'usd_fed' must list weekdays`); rateslib and the core `Calendar` accept both forms. `pricebt validate` passes (it stops before the first
  mark); the run is pinned by a non-strict `xfail` and reported to the owners of the adapter / the synthetic market.

## 9. Environment and the concurrent-writer caveat

Interpreter `C:\Users\chris\anaconda3\envs\stir\python.exe` (rateslib 2.7.1, QuantLib 1.41), `PYTHONPATH=src;tests;.`. The tree is not under version control and other writers edited `src/pricebt` while these runs
were in progress (the rateslib adapter's `swap.py`, `wrap.py`, `ladder.py`, `pricebt/strategy`, `engine`, `results`); the run logs (not kept) recorded a fingerprint of `src/pricebt` and `tests/support` before and after
each run. A run imports its modules at start, so a run is consistent with the source as it stood when it started; the recorded logs show which runs started before or after an edit. Because the results are
bit-identical to the recorded ones and every difference has a mechanism confirmed by the bisection, none of those edits changed a number in the runs above. One test run DID catch the adapter mid-edit: a
`pytest` run at about 13:49 (rateslib `bond.py` / `wrap.py` / `layers.py` were being edited between 13:45 and 13:53) showed the bond `roll` layer off from the recorded one by up to 1,562 (3 tests of
`test_suite_bonds.py`); the same runs repeated a few minutes later, on the settled files, are bit-identical again (roll diff 0.0) and the tests pass. The suite's short-window reproduction tests are what would
show such an edit changing a number.
