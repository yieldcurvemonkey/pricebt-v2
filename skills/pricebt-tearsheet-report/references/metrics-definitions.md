# Metric definitions

Every metric in metrics.json, as computed by `compute_metrics` in [`tearsheet.py`](../scripts/tearsheet.py). The first block comes unchanged from `BackTest.summary_stats()` (ported from gs_quant 2.1.17, see `src/pricebt/backtests/backtest_objects.py`); the rest are added by the tearsheet.

## Conventions

- **P&L, not returns.** `Total = price + Cumulative Cash + Transaction Costs` (DESIGN.md §8.3). With `initial_value = 0` (the default) there is no capital base: `Total` is cumulative P&L in currency, so every "return" below is a currency P&L. A percentage return would need a capital figure that the backtest does not have.
- **Sharpe of P&L = information ratio against cash.** With a zero risk-free rate and no capital base, `mean(daily P&L) / std(daily P&L) × √A` is the information ratio of the strategy against holding cash. It is invariant to notional: doubling the size doubles the mean and the standard deviation.
- **Daily P&L** `p_t = Total_t − Total_{t−1}` over consecutive rows of `result_summary`. There are N = rows − 1 observations. Off-grid rows (cash or cost dates between grid dates) are rows too. The first row's value (typically a day-one transaction cost) is in Total but in no `p_t`.
- **Annualisation factor A** = 252 by default (observations per year for a business-day grid). Use 12 for a monthly grid, 52 for weekly. `Years = N / A`.
- **Currency**: the price measure's currency (local by default; `result_ccy` if the run set one). **Risk** is in currency per 1bp; **rates** are in bp.
- **Sample statistics** use pandas defaults: `std` has `ddof = 1`, skewness is the adjusted Fisher-Pearson coefficient, kurtosis is excess kurtosis.
- A metric that cannot be computed (no closed trades, no risk measure, zero volatility) is `null` in metrics.json and blank in the report.

## From `summary_stats()`

| Metric | Formula | Units |
|---|---|---|
| Start Date, End Date | first and last row of `result_summary` | date |
| Duration (days) | End − Start | calendar days |
| Total PnL | `Total` on the last row | ccy |
| Total Transaction Costs | `Transaction Costs` on the last row (cumulative, ≤ 0) | ccy |
| Total Trades | rows of `trade_ledger()` | count |
| Peak PnL | max of the running maximum of Total | ccy |
| Annualised Return | mean(p) × A | ccy per year |
| Annualised Volatility | std(p) × √A | ccy per year |
| Sharpe Ratio | Annualised Return / Annualised Volatility | dimensionless |
| Sortino Ratio | Annualised Return / (√mean(p² over p < 0) × √A) | dimensionless |
| Max Drawdown | min over t of (Total_t − max_{s≤t} Total_s) | ccy (≤ 0) |
| Max Drawdown Duration (days) | longest run of rows below the running peak, first to last date of the run | calendar days |
| Calmar Ratio | Annualised Return / abs(Max Drawdown); NaN when there is no drawdown | 1/year |
| Current Drawdown | drawdown on the last row | ccy |
| Average Daily PnL, Daily PnL Std Dev | mean(p), std(p) | ccy |
| Best Day, Worst Day | max(p), min(p) | ccy |
| % Positive Days | share of p > 0 | % |
| Skewness, Kurtosis | of p | dimensionless |

## Added by the tearsheet

| Metric | Formula | Units | Notes |
|---|---|---|---|
| Observations (days) | N | count | |
| Years | N / A | years | the sample length the significance rests on |
| t-stat (mean daily PnL) | mean(p) / (std(p) / √N) | dimensionless | equals Sharpe × √Years exactly. \|t\| > 2 is the usual bar; with many variants tried, demand more |
| Closed Trades, Open Trades | ledger rows with Status closed / open | count | |
| Hit Rate (%) | share of closed trades with Trade PnL > 0 | % | closed trades only |
| Average Win, Average Loss | mean Trade PnL of winners / losers | ccy | closed trades only |
| Profit Factor | sum(wins) / abs(sum(losses)) | dimensionless | null if no losing trade |
| Average Holding Period (days) | mean(Close − Open) over closed trades | calendar days | |
| Trades per Year | Total Trades / (Duration / 365.25) | 1/year | |
| Gross PnL (before costs) | Total PnL − Total Transaction Costs | ccy | |
| Cost Drag | abs(Total Transaction Costs) / abs(Gross PnL) | fraction | share of gross P&L consumed by costs; above 1 means costs exceed gross P&L. Uses abs so the sign of gross P&L does not flip it |
| Max Abs Risk, Mean Abs Risk | max and mean of abs(risk column) over rows | ccy per bp | the dv01 the strategy uses; flat days count as 0 |
| Risk Turnover per Year | sum over rows of abs(risk_t − risk_{t−1}) / Years | ccy per bp per year | a turnover proxy: dv01 traded per year (entries, exits and drift) |
| Total PnL (bp) | last `Cumulative PnL (bps)` of `backtest.pnl_bps(risk)` | bp | sum of p_t / risk_{t−1}, skipping abs(risk) < 1e-9; meaningless if risk changes sign |
| PnL / Mean Abs Risk (bp) | Total PnL / Mean Abs Risk | bp | P&L expressed as a rate move on the average position |
| In-Sample Sharpe, Out-of-Sample Sharpe | Sharpe of p on rows ≤ / > `dates.in_sample_end` | dimensionless | null without `in_sample_end`; parameters may be tuned only in-sample |

## Charts

| Chart | What is plotted |
|---|---|
| Cumulative P&L | Total by date |
| Drawdown | Total − running max of Total |
| Rolling 63-day Sharpe | mean / std of p over 63 observations × √252; blank for the first 62 |
| Risk over time | the risk column (ccy per bp) |
| Signal with trade entries | the signal series; markers on each ledger `Open` date at the signal's value on that date (last value on or before it) |
| Monthly P&L | sum of p by calendar month, diverging colour scale symmetric around 0, values in thousands |
| Daily P&L histogram | distribution of p |

## Success criteria

| Criterion | Metric | PASS when |
|---|---|---|
| `min_sharpe` | Sharpe Ratio | value ≥ threshold |
| `min_oos_sharpe` | Out-of-Sample Sharpe | value ≥ threshold |
| `max_drawdown` | Max Drawdown | abs(value) ≤ threshold |
| `min_trades` | Total Trades | value ≥ threshold |

A null threshold is skipped. An unavailable metric or an unknown criterion is N/A, and N/A does not count as passing.
