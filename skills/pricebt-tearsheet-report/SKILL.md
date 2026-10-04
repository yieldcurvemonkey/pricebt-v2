---
name: pricebt-tearsheet-report
description: Turn a checked pricebt backtest into a decision-ready tearsheet — metrics (Sharpe, t-stat, drawdown, hit rate, cost drag, dv01 use, P&L in bp, IS vs OOS), charts, success-criteria PASS/FAIL against the strategy spec, trade ledger, spot checks, review findings and caveats — written as self-contained HTML plus Markdown, JSON and CSV. Use when asked to "report", "write up", "summarise" or "make a tearsheet" for a backtest, after the spot checks.
---

# pricebt tearsheet report

A tearsheet exists to answer one question: **should anyone act on this strategy, and if not, why not?** `tearsheet.py` computes the metrics that map onto that decision, draws the charts, scores the result against the spec's `success_criteria`, and writes a self-contained report. The agent then writes the narrative (verdict, risks, next steps) from [references/report-template.md](references/report-template.md). The numbers are only as good as the spot checks run before them.

## When to use / not use

- **Use** once a backtest has passed [`pricebt-spot-checks`](../pricebt-spot-checks/SKILL.md), to produce the deliverable named in the spec's `deliverables:` block.
- **Use** to compare variants: build one tearsheet per variant, and compare the `metrics.json` files.
- **Do not use** before the spot checks. A beautiful report of a wrong backtest is worse than no report.
- **Do not use** as the place to tune parameters. Tune only on the in-sample period; the report shows in-sample and out-of-sample side by side.

## Inputs

- `backtest`: the finished `BackTest`.
- `spec`: the strategy spec dict (load the YAML filled in from `skills/pricebt-strategy-intake/templates/strategy_spec.yaml`). It supplies `success_criteria`, `dates.in_sample_end`, `assets` and `assumptions`.
- `risk`: a scalar currency-per-bp measure present in `result_summary`, normally `IRDeltaParallel`. Without it there is no risk or bp section.
- `signal`: the date-indexed series the trigger reads (optional; draws the signal chart with entry markers).
- `spot_checks`: the list from `spot_check.run_spot_checks(...)`.
- `review_findings`: from the adversarial review, as a list of strings or of dicts (e.g. `{"severity": ..., "finding": ...}`).
- `caveats`: extra caveats (the pricebt ones are always included); `notes`: your one-paragraph verdict.
- `attribution`: optional `backtest.pnl_explain_table()` of a run with a `PnlDefinition` ([`pricebt-pnl-attribution`](../pricebt-pnl-attribution/SKILL.md)). It adds a "P&L attribution" section before the spot checks: component totals and shares, the unexplained share graded PASS/WARN/FAIL (`attribution.grade`) (a WARN or FAIL line points to the residual diagnosis), and a cumulative chart stacked above and below zero with the economic P&L as a line. Without it the report is unchanged.

## Outputs

In `out_dir`: tearsheet.html (self-contained, images embedded), tearsheet.md with one PNG per chart next to it, metrics.json (metrics plus the success-criteria table), trades.csv (the trade ledger) and summary.csv (`result_summary`). `build_tearsheet` returns a dict of those paths. Everything is deterministic except the "Generated at" line.

## Procedure

1. **Run the spot checks first** and keep the results (`results = spot_check.run_spot_checks(...)`).
2. **Build the tearsheet** in the same process:

   ```python
   import sys; sys.path.insert(0, "skills/pricebt-tearsheet-report/scripts")
   import tearsheet, yaml
   from pricebt.risk import IRDeltaParallel
   spec = yaml.safe_load(open(spec_path, encoding="utf-8"))
   paths = tearsheet.build_tearsheet(backtest, spec["deliverables"]["out_dir"], spec["name"], spec=spec,
                                     risk=IRDeltaParallel, signal=signal, spot_checks=results,
                                     review_findings=findings)
   ```

   See the whole flow on the toy world first (mean reversion with costs, spot checks included):

   ```powershell
   $env:PYTHONPATH = "src;tests"; python skills/pricebt-tearsheet-report/scripts/tearsheet.py --demo results/tearsheet-demo
   ```

3. **Read `metrics.json` and the charts**, then write the narrative by filling [references/report-template.md](references/report-template.md): verdict, what was tested, results, risks, caveats, next steps.
4. **Rebuild with `notes=<your verdict paragraph>`** (and `caveats=[...]` for anything strategy-specific) so the HTML carries the narrative. For a longer write-up, edit tearsheet.md; the HTML stays the numbers-of-record.
5. **Hand over** the paths, the verdict line and the three most important caveats.

## What a good tearsheet contains, and why

Every section answers a decision question. If a section does not change what someone would do, it does not belong.

| Decision question | Where it is answered | Metrics (definitions in [references/metrics-definitions.md](references/metrics-definitions.md)) |
|---|---|---|
| **Is it significant?** Could this be luck? | Key metrics | t-stat of mean daily P&L (≈ Sharpe × √years), Years, Total Trades, Closed Trades. Below `min_trades`, or with a t-stat under ~2, the statistics are not evidence. |
| **Is it robust?** Does it survive data it was not tuned on? | Key metrics, verdict | In-Sample vs Out-of-Sample Sharpe (when `dates.in_sample_end` is set); rolling 63-day Sharpe chart; monthly P&L table. One good month or one regime is not a strategy. |
| **What does it cost?** | Key metrics | Total Transaction Costs, Gross PnL, Cost Drag (share of gross P&L eaten by costs), Trades per Year. High cost drag means the edge depends on the cost assumption. Run the costs-doubled check. |
| **How much risk does it use?** | Risk and P&L in bp | Max/Mean Abs Risk (dv01), Risk Turnover per Year, Total PnL (bp), PnL / Mean Abs Risk (bp). P&L in bp of rate move is comparable across notionals and currencies. |
| **How does it fail?** | Key metrics, charts | Max Drawdown and its duration, worst five days, Sortino, skew/kurtosis, drawdown chart, daily P&L histogram. The worst days tell you which market move breaks it. |
| **Is it tradeable as modelled?** | Caveats, spot checks | Same-close execution, costs and financing as modelled, coupons between marks not booked (swaps and swaptions only; a bond's are, below), missing-market dates dropped, open-at-end positions. |
| **What was it paid for?** | P&L attribution (when `attribution=` is given) | component totals (delta, gamma, vega, vanna, volga, theta, plus coupons and financing), the graded unexplained share and r2. A carry or vol thesis must show up in its own component, with a small residual next to it. |
| **What did funding cost?** (financed books) | your narrative, from `backtest.holding_cash` and the attribution's `cashflow_pnl` / `financing_pnl` totals | coupons received, repo interest paid, and the financed carry (`PNL_theta + financing_pnl`). A bond carry trade that only works before repo is not a carry trade. |

### Financed positions: holding cash and the Total identity

Every `Bond` config maps `FinancingToDate` (the strict Bond contract), so the engine books, on every date it marks or exits a bond position, the coupons the position dropped since its previous mark plus the change of `FinancingToDate`, as cash (DEV-E22). Swaps and swaptions are unchanged (gs parity: only entry and exit prices reach cash).

- **Where to read it.** `backtest.holding_cash[d][position] = (ccy, cashflow, financing)`: per date and per financed position, the coupons and the repo interest booked on d (`ccy` is `result_ccy` when the run sets one). Sum it over dates for the report's coupon and financing totals; with `attribution=`, the same numbers are the `cashflow_pnl` and `financing_pnl` component totals.
- **The identity to state.** For a book of financed positions bought at PV, with no transaction costs and no cash accrual: change in `Total` = ΔPV + coupons + ΔFinancingToDate, i.e. ΔPV + coupons − repo interest for a long (a short receives the repo interest). It holds exactly on any grid: `tests/test_holding_cash.py` proves it on a synthetic financed instrument, and the bond attribution demo (`attribution.py --demo bond`) shows `Σ economic_pnl` equal to the change in `Total` on the toy bond.
- **Report a `cash_accrual` model on a financed book as a defect.** The cash balance already holds the funding loan (−Price at entry), so the accrual charges the funding a second time. The engine warns once ("the funding is counted twice"); the report must not carry that number.
- **The default caveat list predates holding cash.** `tearsheet.PRICEBT_CAVEATS` still says coupons between marks are not booked and that financing is the cash accrual. For a book holding bonds, pass `caveats=[...]` saying coupons and repo financing are booked through holding cash, and state which repo (GC or special, overnight or term, haircut) the bond config uses.
| **Can someone reproduce it?** | Reproducibility | Spec, asset configs, pricebt git commit, generated-at timestamp, CSVs of the ledger and the daily frame. |

### Success criteria

`evaluate_success` compares the metrics with `spec.success_criteria`:

| Criterion | Metric | Rule |
|---|---|---|
| `min_sharpe` | Sharpe Ratio | ≥ threshold |
| `min_oos_sharpe` | Out-of-Sample Sharpe | ≥ threshold (N/A without `in_sample_end`) |
| `max_drawdown` | Max Drawdown | abs(drawdown) ≤ threshold (currency) |
| `min_trades` | Total Trades | ≥ threshold |

A `null` threshold is skipped; an unknown criterion or an unavailable metric is N/A. The verdict line is `PASS` only if every evaluated criterion passes. N/A counts as not passed.

## Checks

- The spot-check section is filled in (not "NOT RUN") and has no unexplained FAIL.
- The verdict line agrees with the success-criteria table, and your narrative paragraph agrees with both.
- When the spec sets `in_sample_end`, the out-of-sample Sharpe is reported next to the in-sample one.
- Total Trades is at least `min_trades`, or the report says the statistics are not meaningful.
- The caveats section contains the four pricebt caveats plus anything the adversarial review raised. For a book holding bonds, it also says coupons and repo are booked (holding cash), which repo the config uses, and the coupon and financing totals.
- For a financed book with no costs and no cash accrual, the change in `Total` equals ΔPV + Σ coupons + Σ financing from `backtest.holding_cash`.
- The HTML opens offline (no external resources) and shows at least the cumulative P&L, drawdown, rolling Sharpe, monthly P&L and histogram charts.

## Pitfalls

- **Total is P&L, not NAV.** With `initial_value=0` there is no capital base, so "returns" are currency P&L and the Sharpe ratio is the information ratio of the strategy against cash. Do not quote percentage returns.
- **P&L in bp is meaningless when risk crosses zero.** Dividing by a risk near zero produces huge values of either sign. Read it only for directional books; for hedged or curve books use currency P&L.
- **Day-one costs are outside the daily series.** The first row's transaction cost is in Total PnL but not in any daily P&L difference, so Sharpe slightly flatters a strategy that trades on day one.
- **Held-forever positions.** Mean-reversion strategies built from `AddTradeAction` with no duration never close. Hit rate, average win/loss and holding period are then computed on zero closed trades (reported as empty); use P&L and drawdown instead.
- **Financed books.** `Total` of a bond book already nets repo interest and includes coupons. Do not add carry from `Carry`/`RollDown` (forward-looking estimates) on top, and do not run a `cash_accrual` model with it.
- **Annualisation.** The default factor is 252 observations per year. Use the grid's real frequency (for example 12 for a monthly grid).
- **Do not hand-edit numbers** in the HTML. Rebuild it.

## Related skills

- [`pricebt-spot-checks`](../pricebt-spot-checks/SKILL.md): must pass before the report.
- [`pricebt-adversarial-review`](../pricebt-adversarial-review/SKILL.md): produces `review_findings`.
- [`pricebt-strategy-intake`](../pricebt-strategy-intake/SKILL.md): produces the spec, its `success_criteria` and its `assumptions`.
- [`pricebt-architecture`](../pricebt-architecture/SKILL.md): the engine semantics behind the caveats.
- [`pricebt-pnl-attribution`](../pricebt-pnl-attribution/SKILL.md): builds the `attribution=` input (P&L by greek).
