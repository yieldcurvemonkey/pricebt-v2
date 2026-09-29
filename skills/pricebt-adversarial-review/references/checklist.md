# Review checklist

Mark each item PASS, FAIL or N/A, and give evidence. Items are ordered by how often they change the conclusion. Book citation keys are as in [`../../pricebt-research-methodology/references/principles.md`](../../pricebt-research-methodology/references/principles.md).

## A. pricebt-specific traps (check these first)

| # | Check | How | Typical severity if it fails |
|---|---|---|---|
| A1 | **Resolve pins the trade.** A seasoned position keeps its maturity and strike | `python skills/pricebt-verify-asset-config/scripts/check_asset.py <config>` passes the pinning check. The ledger's static data shows absolute dates | Blocker |
| A2 | **Units and signs.** dv01 > 0 for a payer; rates in bp; ladder sum ≈ dv01 | the checker's rates pack; an ATM entry has `Open Value` ≈ 0 | Blocker |
| A3 | **Same-close execution.** The trigger observes date d and trades at d's close | read the signal construction. If the signal uses d's close, the result assumes a fill at the observed price. Run the signal-shift experiment | Major (Blocker if the edge disappears with a one-day shift) |
| A4 | **Coupons between marks are not booked** (gs parity) | does the strategy hold swaps through coupon dates? Is carry part of the thesis? | Major for carry and roll strategies. Disclose it always |
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
