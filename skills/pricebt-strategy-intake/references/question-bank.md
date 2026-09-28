# Intake question bank

Questions are in priority order. **Must-ask** questions change what gets built. **Should-ask** questions change parameters. **Nice-to-ask** questions change the report. Each gives the spec field it fills, why it matters, and the default an autonomous agent uses. Sources are the methodology references (keys as in [`../../pricebt-research-methodology/references/principles.md`](../../pricebt-research-methodology/references/principles.md)).

## Must-ask (one message, grouped, with defaults shown)

1. **What is the edge, and when should it fail?** → `hypothesis`
   - *Why:* a mechanism-backed idea survives out of sample; stating the failure regime pre-registers the review. (FA ch. 1, ch. 12; GK ch. 12)
   - *Default:* write the most plausible mechanism, and mark it `assumed` in `assumptions`.
2. **What exactly is traded?** Currency, tenor(s), instrument, direction convention. → `instruments`, `assets`
   - *Why:* this decides which asset configs must exist.
   - *Default:* one instrument at the tenor named in the idea, ATM, spot-starting, `notional_amount` 10mm.
3. **What is the signal, and how is it measured?** Which rate, level or change, and the lookback window. → `signal.*`
   - *Why:* each window is a free parameter. Declare a coarse grid rather than tuning. (Chan ch. 3 p. 53; FA ch. 10)
   - *Default:* the asset's `par_rate` of the primary instrument; lookback 30 business days for z-scores, 60 for momentum.
4. **Entry, exit and holding period.** → `signal.params`, `rebalance`, `entry_exit` (in `archetype`)
   - *Why:* there are only four exit types (fixed period, target, opposite signal, stop). Stops suit momentum, not mean reversion. (Chan ch. 7 pp. 140–143)
   - *Default:* mean reversion enters at |z| > 2, with gs's offsetting-trade exit at the mean; momentum and carry rebalance monthly with `trade_duration: next schedule`.
5. **How big?** Notional, dv01 per trade or leg, or a cash (NAV) budget. → `sizing.*`
   - *Why:* size in risk units, especially across tenors. (GK ch. 5; Chan ch. 6)
   - *Default:* single-instrument ideas use notional 10mm; curve trades use a dv01 target of 10,000 per bp per leg.
6. **Limits.** Maximum dv01, a stop, maximum positions, a drawdown tolerance. → `risk_limits.*`
   - *Why:* limits change behaviour and must be fixed before seeing results. (FA ch. 3 pp. 34–35)
   - *Default:* none enforced; a max drawdown of 3× the expected annual P&L standard deviation is report-only.
7. **Period and out-of-sample split.** → `dates.*`
   - *Why:* the OOS period must come later in time, and the test window should be at least a third the length of the training window. (FA ch. 10 p. 76; Chan ch. 3 pp. 53–54)
   - *Default:* the full available history of the asset; `in_sample_end` at 70% of the period.
8. **Costs.** → `costs.*`
   - *Why:* costs decide tradability. Run both with and without them. (Chan ch. 3 pp. 60–65)
   - *Default:* `dv01_bp` 0.25 (a quarter of a bp of dv01 per side) for liquid swaps; 0.5 for long or off-the-run tenors.

## Should-ask (only if the answer changes the build)

9. **Rebalance cadence and pricing grid.** → `rebalance.frequency`, `dates.frequency`. *Default:* grid `1b`; rebalance at the idea's natural cadence (monthly for carry, daily for hedging).
10. **Hedging.** Keep dv01-neutral? With what? → `archetype: delta_hedged`, `instruments.hedge`. *Default:* no hedge unless the idea is relative value.
11. **Financing and cash.** Accrue cash? At what rate? Starting value? → `financing.cash_accrual_rate`, `initial_value`. *Default:* 0 and 0. `Total` is then cumulative P&L; a swap needs no cash.
12. **Currency of the report.** Is it a mixed-currency book? → `result_ccy`, `fx`. *Default:* the asset's local currency. A mixed book needs an FX config.
13. **Event calendar.** Which dates (central-bank meetings, auctions, month-ends)? → `event_dates`. *Default:* none; if the idea depends on events, this becomes must-ask.
14. **Benchmark.** → `benchmark`. *Default:* `none` (cash), since swap P&L is already excess of funding. For carry ideas, compare against the always-on version.
15. **Parameter budget.** How many variants are you willing to test? → `trial_budget` (write it under `assumptions`). *Default:* 5. Every run is logged, and the significance test is deflated by the count.

## Nice-to-ask (report only)

16. **Success criteria.** → `success_criteria.*`. *Default:* Sharpe ≥ 0.5, OOS Sharpe ≥ 0.3, at least 10 trades.
17. **What would make you stop trading it?** A kill rule, reported as a threshold. *Default:* the worst in-sample drawdown × 1.5.
18. **Correlation to existing books.** Report it if the user can supply their P&L series. *Default:* skip.
19. **Delivery.** Format and audience. → `deliverables`. *Default:* HTML + markdown tearsheet in `reports/<name>/`.

## Message template (interactive mode)

```
To build this backtest I need 8 quick answers - reply "defaults" to accept all of them.
1. Edge / failure regime: <my reading> (default)
2. Instrument: <ccy tenor instrument, direction> (default)
3. Signal: <measure, lookback> (default)
4. Entry / exit / holding: <rule> (default)
5. Size: <notional or dv01 target> (default)
6. Limits: <none / max dv01 / stop> (default)
7. Period and out-of-sample split: <start..end, IS to date> (default)
8. Costs: <0.25bp of dv01 per side> (default)
```
