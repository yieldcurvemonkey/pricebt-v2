# Report template

The narrative skeleton you fill after reading metrics.json and the charts. Keep it short: a reader should get the decision from the first paragraph and the reasons from the rest. Every number you quote must appear in metrics.json or in the tearsheet. Put the verdict paragraph into `build_tearsheet(..., notes=...)`; the whole narrative can go at the top of tearsheet.md.

Replace everything in `<angle brackets>`. Delete a section's hints once it is written.

---

## Verdict

<One paragraph, four sentences at most.>

- <PASS / FAIL against the success criteria, in the words of the verdict line: "Meets 3 of 4 criteria; misses min_oos_sharpe.">
- <The single most important reason, with its number: "Out-of-sample Sharpe 0.1 vs 1.4 in-sample: the edge did not survive the period it was not tuned on.">
- <Whether the numbers can be trusted: "Spot checks pass; manual repricing within 3 USD.">
- <The recommendation: act / do not act / test further, and what would change it.>

## What was tested

- **Idea and hypothesis:** <from the spec's `idea` and `hypothesis`, one sentence each>.
- **Rule:** <archetype, signal and thresholds, rebalance, sizing>. <Instruments and notionals.>
- **Period and data:** <start to end, frequency, in-sample end; asset configs; any missing-market dates dropped>.
- **Frictions:** <cost model and level; cash accrual; initial value>.
- **Assumptions:** <the spec's `assumptions`: every default relied on>.

## Results

- **Significance:** <Total PnL, Sharpe, t-stat, Years, trade count. Is it more than luck? With N variants tried, say N.>
- **Robustness:** <in-sample vs out-of-sample Sharpe; rolling Sharpe (stable, or one good stretch?); monthly P&L (concentrated in a few months?)>.
- **Cost:** <total costs, cost drag, trades per year; result of the costs-doubled check>.
- **Risk use:** <mean and max dv01, P&L in bp; P&L per unit of dv01>.
- **Trades:** <hit rate, average win/loss, profit factor, holding period, or "no closed trades: positions are held to the end" and what that means>.

## Risks: how it fails

- **Drawdown:** <max drawdown, its duration, when it happened and what the market did then>.
- **Worst days:** <the worst five days and the common move behind them>.
- **Regime:** <the regime named in the hypothesis where it should fail, and whether the sample contains it>.
- **Tail and crowding:** <skew/kurtosis; whether the trade is a known, crowded one>.

## Caveats

Always include the pricebt caveats (the tearsheet lists them), and say whether each one matters for *this* strategy:

- <Coupons paid between marks are not booked as cash: material for carry strategies and multi-year holds.>
- <Same-close execution: material for fast signals; see the one-day-lag look-ahead result.>
- <Costs and financing exactly as modelled: say what is missing (bid/ask on exit, funding, margin).>
- <Missing-market dates dropped: how many, and whether any fell in the drawdown.>
- <Anything from the adversarial review and every spot-check WARN.>

## Next steps

<Two to four concrete actions, each with the question it answers. Examples: "extend the sample back to 2015 to include a hiking cycle (robustness)"; "rerun at 0.5bp of dv01 costs (break-even cost)"; "add a 3-month trade_duration so positions close (hit-rate statistics)". Do not propose tuning on the out-of-sample period.>
