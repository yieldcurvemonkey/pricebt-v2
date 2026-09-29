---
name: pricebt-spot-checks
description: Verify a finished pricebt backtest before believing or reporting it — accounting identities, independent repricing of trades and of the book, cash roll-forward, P&L explain, frictions, missing data, determinism — plus the manual checks (reprice by hand, look-ahead shift, costs doubled). Use after every backtest run and before any tearsheet, or when a result "looks too good" or a number surprises you.
---

# pricebt spot checks

A backtest that runs is not a backtest that is right. This skill checks a finished `BackTest` in two passes: an **automated pass** (`spot_check.py`) that verifies pricebt's own bookkeeping against an independent reprice through the asset configs, and a **manual pass** that an agent must do by hand, because the dangerous mistakes (look-ahead, a sign flip in the idea, costs that are too small) are in the *strategy*, not in the engine. Nothing goes into a report until both passes are done and every FAIL is explained.

## When to use / not use

- **Use** after every `run_backtest`, before [`pricebt-tearsheet-report`](../pricebt-tearsheet-report/SKILL.md), and whenever a result is surprising (too smooth, too profitable, P&L that does not follow the rate).
- **Use** again after any change to the strategy, the asset config or the cost model: checks are cheap.
- **Do not use** to judge whether the strategy is *good*. That is the tearsheet and the adversarial review ([`pricebt-adversarial-review`](../pricebt-adversarial-review/SKILL.md)). Spot checks answer "are these numbers what the rules imply?", not "are the rules any good?".

## Inputs

- `backtest`: the `BackTest` returned by `GenericEngine().run_backtest(...)`.
- The `PricebtSession` that holds the asset configs the backtest used (defaults to `PricebtSession.current`).
- Optional, strongly recommended:
  - `rerun`: a zero-argument callable that rebuilds the strategy and returns a fresh `BackTest` (for the determinism check);
  - `risk`: a scalar currency-per-bp column that is in `result_summary`, e.g. `IRDeltaParallel` (pass it in `run_backtest(risks=[Price, IRDeltaParallel])`);
  - `rate_measure`: a bp series for the driving rate, e.g. `measure_series(IRSwap(termination_date="10y", notional_currency="USD"), IRFwdRate, start, end)` from `pricebt.data` (the own rate of any IR class: par rate, swaption forward, bond yield; multiply by 100 or 1e4 if `series.attrs["unit"]` is `pct` or `decimal`). A config function name such as `"par_rate"` also works.

## Outputs

A list of `CheckResult(name, status, detail)` with status `PASS | WARN | FAIL | INFO`, and `to_markdown(results)` for a table you paste into the tearsheet (`build_tearsheet(..., spot_checks=results)`).

## Procedure

1. **Run the automated pass** in the same process as the backtest:

   ```python
   import sys; sys.path.insert(0, "skills/pricebt-spot-checks/scripts")
   import spot_check
   from pricebt.risk import IRDeltaParallel
   results = spot_check.run_spot_checks(backtest, rerun=run, risk=IRDeltaParallel, rate_measure=rate)
   print(spot_check.to_markdown(results))
   ```

   To see what a healthy output looks like, run the toy demo first:

   ```powershell
   $env:PYTHONPATH = "src;tests"; python skills/pricebt-spot-checks/scripts/spot_check.py --demo
   ```

2. **Read every non-PASS line.** A FAIL means the reported numbers do not follow from the rules; stop and use the symptom table below. A WARN needs one sentence in the report explaining why it is acceptable, or a fix.
3. **Do the manual checks** in [references/manual-checks.md](references/manual-checks.md). Summary:
   1. **Reprice two trades by hand** with your pricing library directly (not through pricebt): one early, one late. Compare with the ledger's Open Value and Close Value.
   2. **First and last trade dates vs the signal**: the first trade cannot precede the signal's warm-up (lookback) window; the last must be explainable from the signal on that date.
   3. **Perturb one parameter** (threshold, lookback, notional) and confirm the direction of change is sensible (double notional: P&L doubles exactly; higher entry threshold: fewer trades).
   4. **Run with costs doubled**: net P&L falls by exactly the original cost total (for linear cost models). If the strategy only works with cheap costs, say so.
   5. **Shift the signal by one day** (use yesterday's signal to trade today). If the strategy claims timing skill, P&L must degrade but not collapse. If it *improves*, or collapses from great to nothing, suspect look-ahead.
4. **Record** the automated table and a one-line result for each manual check. They go into the tearsheet's spot-check section.

### What the automated checks do

| Check | Verifies | Catches |
|---|---|---|
| ledger identity | `Total == price + Cumulative Cash + Transaction Costs` on every row (1e-6 relative) | a post-processed frame, a wrong price column, a cost with the wrong sign |
| closed-trade PnL | `Trade PnL == Close Value + Open Value` | an edited ledger; exit cash booked to another trade name |
| trade repricing | on a seeded sample: `Open Value == -PV(open)`, `Close Value == +PV(close)`, repriced with **cold caches** (a `HedgeAction` row, `Scaled_<hedge name>_<date>`, is the booked hedge `Portfolio`: its instruments are summed) | entry/exit sign errors, `quantity_` not applied, exit priced on the wrong date |
| book repricing | on sampled grid dates: sum of held positions' PV == reported price | positions missing from or left in the book, stale ffilled PV |
| cash roll-forward | cash moves only on payment dates and equals initial + cumulative payments | cash on the wrong date, double-booked exits, accrual that is unexpectedly on |
| P&L explain | corr(daily ΔTotal, risk(t-1) × Δrate), residual variance share | risk sign flipped against P&L, rates in % not bp, P&L driven by something else. WARN below 0.5 for a directional book |
| attribution residual | when the run has a `PnlDefinition` (`run_backtest(pnl_explain=...)`): `attribution.grade` of `pnl_explain_table()`, the worst of the residual variance share, 1 − r2 and \|Σ residual\| / Σ\|economic\|. PASS ≤ 5%, WARN ≤ 25%, else FAIL; FAIL on any NaN or on a material residual whose signature names an attribute; INFO without a definition (`check_pnl_attribution_generic`) | a **material** greek error: a wrong sign or unit, per-year theta, a vol level in pct declared bp, coupons with no `Cashflows`, NaN levels. A greek that is small on this book (half gamma on a short run) can stay under 5%: the detail names it as immaterial, and `check_asset.py` tests each greek alone. Diagnose with [`pricebt-pnl-attribution`](../pricebt-pnl-attribution/SKILL.md) |
| missing market | `missing_market_dates` / `missing_market_moves` | a data hole thinning the grid (WARN above 2% of the grid), late exits |
| transaction costs / cash accrual | costs modelled at all (WARN if all zero), NaN costs inside the window (FAIL) | a frictionless backtest reported as tradable |
| determinism | a rerun gives an identical `result_summary` | wall-clock dependence, unseeded randomness, state leaking between runs |
| open at end | count and gross notional of trades still open at the end | mean-reversion "exits" that are really offsetting trades held forever |
| known limitation | always printed | coupons between marks are not booked; signal and execution on the same close |

The repricing checks use a fresh `PricingService` built on the session's registry, so they re-evaluate the asset configs rather than reading back the engine's cache. They still go through **your asset config**, so a pricing bug inside the config is invisible to them. That is what manual check 1 is for.

## Symptom → cause

| Symptom | Likely cause | What to do |
|---|---|---|
| ledger identity FAIL | you are reading a copied/filtered `result_summary`, or a `result_ccy` run is read with the local-currency price column | recompute from `backtest.result_summary`; use `backtest.price_measure` as the column |
| trade repricing FAIL on Close Value only | exit priced on a different date than the ledger's Close (holding window `create <= s < final`), or a rolled exit (`missing_market_moves`) | check the date in the detail against `missing_market_moves` |
| trade repricing FAIL on both, ratio constant | `quantity_` / notional scaling mismatch between the action and what you think you traded | inspect `backtest.portfolio_dict[d]` quantities |
| book repricing FAIL on dates after a trade closes | a position left in the book past its exit, or PV ffilled on a flat date | check `trade_duration`; report as an engine bug with a minimal repro if the strategy is right |
| cash roll-forward FAIL with no stray dates | a payment's `cash_paid` edited or double-counted | compare `backtest.cash_payments[d]` with `backtest.cash_dict[d]` |
| cash roll-forward FAIL, stray dates | cash accrual or `initial_value` you did not intend | check `strategy.cash_accrual` and `run_backtest(initial_value=...)` |
| P&L explain corr near -1 | risk sign convention opposite to P&L (receiver dv01 quoted positive) | fix the asset config's dv01 sign, not the P&L |
| P&L explain corr low on a directional book | rate series is not the one that drives the book (tenor, curve), in %, or the book is dominated by carry/roll | pass the right `rate_measure`; quantify carry separately |
| attribution residual WARN/FAIL | the attribution does not explain the P&L: see the residual taxonomy | follow `skills/pricebt-pnl-attribution/references/diagnosing-residuals.md`; the detail names the worst date |
| transaction costs WARN | defaults are frictionless | add a `transaction_cost=` model to the actions |
| transaction costs FAIL (NaN) | a `ScaledTransactionModel(RiskMeasure)` priced on a date with no value | check the cost date against the grid |
| determinism FAIL | `date.today()`, random numbers, or mutable state shared between runs | make every input explicit and seeded |
| many trades open at end | offsetting trades held forever (mean reversion with `AddTradeAction` and no duration) | report gross notional; consider a `trade_duration` |

## Checks (exit criteria)

You are done when all of these hold:

- no FAIL in the automated table (or each one is traced to a documented cause, and the backtest was rerun after a fix);
- every WARN has a one-line justification in the report;
- all five manual checks were run and their results written down;
- `determinism` is PASS (you passed `rerun`), and `P&L explain` ran (you passed `risk` and `rate_measure`) for any rates strategy.

## Pitfalls

- **Running the checks in a different session.** The repricing uses the session's asset configs. A session with other configs or another FX config reprices a different world.
- **Treating PASS as validation of the pricing.** The automated pass proves the engine's bookkeeping is consistent with the asset config. It says nothing about whether the config prices correctly.
- **Small samples.** `sample=5` by default. For a final report use `sample=20` or more; the seed makes it reproducible.
- **An ATM entry hides sign errors.** An at-the-money trade has PV ≈ 0 at entry, so an entry sign flip is invisible there. The exit check and the manual checks catch it.
- **Do not edit `src/pricebt` to make a check pass.** If you find an engine bug, write a minimal repro and report it.

## Related skills

- [`pricebt-architecture`](../pricebt-architecture/SKILL.md): the engine semantics behind these checks (holding window, same-close execution, coupons).
- [`pricebt-tearsheet-report`](../pricebt-tearsheet-report/SKILL.md): takes these results into the report.
- [`pricebt-adversarial-review`](../pricebt-adversarial-review/SKILL.md): attacks the strategy once the numbers are known to be right.
- [`pricebt-strategy-intake`](../pricebt-strategy-intake/SKILL.md): the spec that says what the backtest was supposed to do.
- [`pricebt-pnl-attribution`](../pricebt-pnl-attribution/SKILL.md): the P&L by greek behind the "attribution residual" row, and how to diagnose a large residual.
