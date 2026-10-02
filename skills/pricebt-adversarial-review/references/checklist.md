# Review checklist

Mark each item PASS, FAIL or N/A, and give evidence. Items are ordered by how often they change the conclusion. Book citation keys are as in [`../../pricebt-research-methodology/references/principles.md`](../../pricebt-research-methodology/references/principles.md).

## A. pricebt-specific traps (check these first)

| # | Check | How | Typical severity if it fails |
|---|---|---|---|
| A1 | **Resolve pins the trade.** A seasoned position keeps its maturity and strike | `python skills/pricebt-verify-asset-config/scripts/check_asset.py <config>` passes the pinning check. The ledger's static data shows absolute dates | Blocker |
| A2 | **Units and signs.** dv01 > 0 for a payer swap or payer swaption, < 0 for a long bond; rates in bp; ladder sum ≈ dv01; vega per bp of normal vol, > 0 for a bought option; `Theta` per day, not per year | the checker's instrument pack (swap, swaption or bond) and its contract rows; an ATM swap entry has `Open Value` ≈ 0; a bought option's `Open Value` is minus its premium | Blocker |
| A3 | **Same-close execution.** The trigger observes date d and trades at d's close | read the signal construction. If the signal uses d's close, the result assumes a fill at the observed price. Run the signal-shift experiment | Major (Blocker if the edge disappears with a one-day shift) |
| A4 | **Coupons between marks are not booked** (gs parity) | does the strategy hold swaps or bonds through coupon dates? Is carry part of the thesis? For a bond, does its `Price` drop paid coupons (then `Total` understates carry; attribute with `Cashflows`)? Options: is the expiry exit at intrinsic, and is the vol surface point-in-time? | Major for carry and roll strategies. Disclose it always |
| A5 | **Mean-reversion exits are offsetting trades held forever** | ledger rows are all `open`; gross notional grows with each signal. Report net dv01 and gross notional | Minor to Major (the cost of carrying offsetting trades, and inflated gross) |
| A6 | **Dropped market dates** (`missing_market='drop'`) | `backtest.missing_market_dates` and `backtest.missing_market_moves`: how many, and where? Do they cluster around the events the strategy trades? | Minor; Major if more than 2% of the grid, or clustered |
| A7 | **Frictionless defaults** | `Transaction Costs` column all zero? No cash accrual? `initial_value` 0 (Total is P&L, not NAV)? | Major if costs are off |
| A8 | **Hedges are sized on pre-hedge risk.** Several hedges on one date don't see each other | count hedge actions per date | Minor |
| A9 | **Intensive vs extensive units.** A rate measure is not multiplied by position size | `IRFwdRate` of a scaled position equals the unit value | Blocker if a custom config mislabels units |
| A10 | **Currency.** A mixed book needs `result_ccy` and an FX config; is the FX direction right? | `tests/test_multi_currency.py` pattern; the report currency is stated | Blocker if converted wrongly |
| A11 | **Spec vs code.** Every spec field is implemented or explicitly N/A | the recipes' `describe(built)` output vs the spec | Major |
| A12 | **Stop-loss approximation.** A `Price`-based stop uses open-position PV, not realised P&L | only if `risk_limits.stop_loss_mtm` is set | Minor (disclose) |
| A13 | **Market snapshot timing.** EOD stamp, timezone, and the served-date check in `load_market` | read the asset config | Major if the served date is not validated |
| A14 | **Data envelope.** Does the backtest end where the data ends, or were tail dates dropped silently? | compare `dates.end` with the last kept grid date | Minor |
| A15 | **P&L attribution residual.** Explain residual large? | Check moneyness (`docs/v2/PNL_EXPLAIN_PLAN.md` §2.7), coupon cash, and gamma units before trusting the attribution | Major if unexplained (only when `pnl_explain.enabled: true`) |

## B. Look-ahead and data timing

| # | Check | How | Source |
|---|---|---|---|
| B1 | No input is used before it was available | audit each series' availability time vs the decision time | FA ch. 14 p. 99; GK ch. 17 pp. 498–499 |
| B2 | Estimated quantities (means, sds, hedge ratios) use past data only | read the code for full-sample fits, centred windows, `bfill`, two-sided filters | Chan ch. 3 p. 51; AQM ch. 6 p. 200 |
| B3 | **Truncation test**: dropping the last N days leaves every earlier trade identical | the robustness experiment `truncation` | Chan ch. 3 pp. 51–52, 59 |
| B4 | **Shift test**: lagging the signal one day degrades gradually, not catastrophically | the robustness experiment `signal_shift` | GK ch. 13; FA ch. 14 |
| B5 | All legs of a spread use the same snapshot time | read the asset configs | AQM ch. 2 pp. 54–63 |

## C. Overfitting and statistics

| # | Check | How | Source |
|---|---|---|---|
| C1 | Parameter count ≤ 5, including qualitative choices | count them in the spec | Chan ch. 3 p. 53 |
| C2 | About 252 daily observations per parameter | years × 252 / parameter count | Chan ch. 3 p. 53 |
| C3 | Trial count logged, and significance deflated by it | `research_stats.sharpe_summary(..., n_trials=N)` | GK ch. 12 p. 337; FA ch. 10 p. 75 |
| C4 | The parameter sweep shows a plateau, not a spike | the robustness experiment `parameter_sweep` | Chan ch. 3 p. 60; FA ch. 32 p. 239 |
| C5 | The OOS drop is acceptable (the spec's `min_oos_sharpe`) | the robustness experiment `is_oos` | FA ch. 4 pp. 43–44; Chan ch. 3 p. 58 |
| C6 | t ≈ Sharpe × √years is reported; SE(Sharpe) is stated | `research_stats` | GK ch. 5, ch. 17 |
| C7 | Implausible results are investigated (Sharpe > 2, hit rate > 55%) | if found, treat as a bug until explained | GK ch. 6 p. 154; ch. 12 p. 338 |
| C8 | Enough trades (≥ the spec's `min_trades`; flag cells with fewer than about 30) | the ledger count | AQM ch. 4 pp. 148–149 |

## D. Economics, costs and regime

| # | Check | How | Source |
|---|---|---|---|
| D1 | Cost ladder at 0×, 1×, 2× and 3×, and the break-even cost | the robustness experiment `cost_ladder` | Chan ch. 3 pp. 60–65 |
| D2 | A flip costs two sides; cost = one-way × Σ\|Δposition\| | the ledger vs the Transaction Costs column | AQM ch. 1 p. 36 |
| D3 | Compare against the always-on or naive version (e.g. always receive carry) | a second run with the signal removed | AQM ch. 1 p. 32; ch. 4 pp. 134–135 |
| D4 | Sub-period and regime table; P&L not dominated by one episode | the robustness experiment `sub_periods`; top-5 days' share of P&L | GK ch. 12 pp. 328–329; FA ch. 6 p. 52 |
| D5 | Mark glitches: one-day P&L spikes that reverse | list days with \|ΔP&L\| > 4 sd and check whether the next day reverses | GK ch. 12 p. 321; Chan ch. 3 pp. 42–43 |
| D6 | Curve and spread trades are weighted by dv01, and the signal's weights equal the traded weights | leg dv01s on each date | Chan ch. 3 pp. 57–58 (book example gets this wrong) |
| D7 | Capacity and liquidity are plausible for the tenors and sizes | size vs typical market depth (judgment) | FA ch. 12 p. 88 |
| D8 | A structural premium (carry, vol premium) is not presented as skill | a long vs short split and the always-on benchmark | AQM ch. 4 pp. 134–135 |

## E. P&L attribution and the measure contract

Run these when the backtest was attributed (`run_backtest(pnl_explain=...)`), or when the thesis rests on a greek (carry, vol, convexity). The procedure and the residual taxonomy are in [`../../pricebt-pnl-attribution/SKILL.md`](../../pricebt-pnl-attribution/SKILL.md).

| # | Check | How | Typical severity if it fails |
|---|---|---|---|
| E1 | **The attribution residual is small, or its cause is named** | `spot_check.check_pnl_attribution_generic(bt)` is PASS. For WARN or FAIL, use the diagnosing-residuals taxonomy of the attribution skill. Most common causes: moneyness with a fixed-annuity delta; coupons with no `Cashflows` in `risks=`; gamma units or half gamma; a per-year `Theta`; a unit mismatch (a `ValueError`) | Major; Blocker if the thesis is the component the residual co-moves with |
| E2 | **The components match the thesis** | a "carry" strategy earns `PNL_theta`; a "vol" strategy earns `VegaPnL`/`PNL_gamma`, not `PNL_delta` | Major (the P&L is not the claimed premium) |
| E3 | **Every `unsupported_measures:` declaration has a real reason** (Bond configs only: an IRSwap/IRSwaption config cannot declare, and `check_asset.py`'s `ir_fake_constant` must PASS on it -- no placeholder `0.0` outside `contracts.ZERO_BY_CONVENTION`) | read each reason in the asset configs, and run `measures.py matrix --strict` on each (it exits 1 on a declaration every library can avoid). "TODO", "not needed" or "later" is not a reason: the library must truly lack the measure. Every attribute dropped because of one is named in the report | Major (a hidden gap; its term sits in the residual) |
| E4 | **The measures the attribution multiplies are verified** | `python skills/pricebt-verify-asset-config/scripts/check_asset.py <config>` passes its instrument pack (units, signs, bands) for every asset in the book; `IRDelta` is the total own-rate derivative, not an annuity pv01, if the book goes off-market | Blocker if a sign or unit is wrong |
| E5 | **Mixed books use one unit per level, and map the 0.0 vol convention for swaps and bonds** | `python skills/pricebt-pnl-attribution/scripts/attribution.py --definition <configs>` prints a definition, not a refusal | Blocker (attribution is refused, or meaningless) |
| E6 | **Near-expiry and long steps are read as such** | residual steps within days of expiry, or on weekly or monthly grids, are time cross terms and third-order terms, not skill. A swaption held past expiry leaves its exercised swap's carry in the residual (`PNL_theta` stops at expiry). A daily grid is used for attribution | Minor (disclose) |
