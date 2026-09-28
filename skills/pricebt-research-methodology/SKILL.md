---
name: pricebt-research-methodology
description: The research standard every pricebt strategy study is held to, distilled from four quant-research books (Grinold & Kahn; Chan; Tulchinsky et al. "Finding Alphas"; Dunis, Laws & Naim) and mapped to rates backtests - hypothesis first, trial logging, breadth and significance (t ~ Sharpe x sqrt(years)), out-of-sample design, costs, sizing (half-Kelly, risk budgets), and a significance calculator. Use when designing a study, judging whether a result is real, or writing the methodology section of a report.
---

# Research methodology (what "good" looks like)

A backtest is evidence, not proof. This skill sets the bar that [`pricebt-strategy-intake`](../pricebt-strategy-intake/SKILL.md), [`pricebt-adversarial-review`](../pricebt-adversarial-review/SKILL.md) and [`pricebt-tearsheet-report`](../pricebt-tearsheet-report/SKILL.md) enforce. It condenses four books into rules you can apply mechanically. Full principles with citations are in [`references/principles.md`](references/principles.md); formulas and their known answers are in [`references/formulas.md`](references/formulas.md); the rates-specific translation is in [`references/rates-mapping.md`](references/rates-mapping.md).

## When to use / not use

- **Use** when you design a study (before any backtest), when you decide whether a result is significant, and when you write the methodology and limitations sections of a report.
- **Do not use** it to learn the pricebt API. See [`pricebt-strategy-recipes`](../pricebt-strategy-recipes/SKILL.md).

## The ten rules

1. **Hypothesis first.** Write one sentence on *why* the idea should earn money and *who is on the other side*, plus where the effect should be strong, weak and absent. Do this before looking at any backtest. (Tulchinsky et al., ch. 1, 12, 25; Grinold & Kahn, ch. 12)
2. **Log every trial.** Record each variant you run (parameters, dates, result) in the spec's trial log. With 20 worthless variants, the chance that one looks significant at 5% is about 64%. Report the trial count next to every Sharpe. (Grinold & Kahn, ch. 12, p. 337; Tulchinsky et al., ch. 10)
3. **Few parameters, fixed in advance.**
   - Count every free choice, qualitative ones included (lookbacks, thresholds, holding period, rebalance day), and aim for ≤ 5.
   - Have about 252 daily observations per parameter.
   - Prefer a plateau of good settings to a single optimum.

   (Chan, ch. 3, p. 53, 60)
4. **Out-of-sample means later in time.** Set `in_sample_end` in the spec before the first run, and never tune on the data after it. Use an earlier period only as an extra robustness check, never as the out-of-sample test. The test window should be at least a third the length of the training window. (Tulchinsky et al., ch. 10; Chan, ch. 3, pp. 53–55)
5. **Significance takes years.**
   - The t-stat is about Sharpe × √years, and the standard error of a Sharpe estimate is about 1/√years.
   - A Sharpe of 0.5 needs about 16 years to reach t = 2.
   - One losing year says little about a Sharpe-0.5 strategy.

   Report the t-stat, the standard error and the years of data, not just the Sharpe. (Grinold & Kahn, ch. 5, 12, 17)
6. **Implausible means buggy.** Treat a daily-P&L Sharpe above 2 on public data, or a directional hit rate far above 55%, as a bug until proven otherwise. An information coefficient (IC) of 0.0577 is only a 52.9% hit rate. (Grinold & Kahn, ch. 6, 12)
7. **Breadth counts independent bets.**
   - Apply the fundamental law, IR ≈ IC × √breadth, to *independent* bets only.
   - Tenors on one curve are not independent, and neither is a monthly rebalance of a yearly view.
   - Count principal-component factors (level, slope, curvature) or currencies instead.

   (Grinold & Kahn, ch. 6, p. 158)
8. **Costs are first-order.**
   - Run at zero cost (to measure signal quality) and at realistic cost (to measure tradability).
   - Report the break-even cost and a cost ladder at 0×, 1×, 2× and 3×.
   - Contrarian (mean-reversion) rules trade more and pay more.

   (Chan, ch. 3, pp. 60–65; Dunis, Laws & Naim, ch. 11; Grinold & Kahn, ch. 16)
9. **Size by risk and cap it.**
   - Size in dv01 or risk units, not notional.
   - If you size by edge, use half-Kelly (f = m/s²) at most, capped by the tail loss you can tolerate.
   - Keep portfolio size under control at all times; most blow-ups are over-leverage, not a wrong model.

   (Chan, ch. 6)
10. **Replicate and reconcile.**
    - An independent re-implementation, or independent re-pricing of sample trades, must reproduce the headline numbers.
    - Compare trades one to one, not just the totals.

    (Chan, ch. 3, 5, 6; see [`pricebt-spot-checks`](../pricebt-spot-checks/SKILL.md))

## Procedure (inside the strategy workflow)

1. **During intake**, write the hypothesis, the out-of-sample split, the parameter list and the success criteria into `strategy_spec.yaml`. Estimate the trial budget up front.
2. **During implementation**, append every run to a trial log (`reports/<name>/trials.csv`: timestamp, parameters, IS Sharpe, OOS Sharpe).
3. **During review**, compute significance with the calculator:

   ```python
   import sys; sys.path.insert(0, "skills/pricebt-research-methodology/scripts")
   import research_stats as rs
   daily = backtest.result_summary["Total"].diff().dropna()
   rs.sharpe_summary(daily, n_trials=len(trials))   # sharpe, t_stat, se_sharpe, years_needed_t2, bonferroni_z, significant_after_trials
   rs.half_life(signal)                             # mean-reversion horizon; NaN = not mean-reverting
   ```

4. **In the report**, state the capital definition (a swap P&L has no capital base, so percentage metrics need one), the annualisation factor (252), whether a risk-free rate was subtracted (not for swap P&L), and the trial count.

## Checks

- The spec holds a hypothesis, an OOS split, a parameter count and the success criteria, all written *before* the first backtest.
- The report shows the t-stat, SE(Sharpe), the years of data, the trial count, IS vs OOS, and a cost ladder or at least cost-on vs cost-off.

## Pitfalls

- Reporting CAGR or a percentage return on an unfunded swap book without a stated capital base.
- Annualising overlapping multi-day P&L as if it were independent. Also report weekly or monthly non-overlapping Sharpe.
- Mistaking a structural premium (carry, the vol risk premium) for skill. Compare against the always-on version of the trade.

## Related skills

- [`pricebt-strategy-intake`](../pricebt-strategy-intake/SKILL.md), [`pricebt-adversarial-review`](../pricebt-adversarial-review/SKILL.md), [`pricebt-tearsheet-report`](../pricebt-tearsheet-report/SKILL.md).

## Sources (paraphrased; page conventions in `references/principles.md`)

- R. Grinold & R. Kahn, *Active Portfolio Management*, 2nd ed., McGraw-Hill, 1999.
- E. Chan, *Quantitative Trading: How to Build Your Own Algorithmic Trading Business*, Wiley, 2009.
- I. Tulchinsky et al., *Finding Alphas: A Quantitative Approach to Building Trading Strategies*, Wiley, 2015.
- C. Dunis, J. Laws & P. Naïm (eds.), *Applied Quantitative Methods for Trading and Investment*, Wiley, 2003.
