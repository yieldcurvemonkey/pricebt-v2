---
name: pricebt-adversarial-review
description: Adversarially review a pricebt backtest before anyone trusts it - a severity-ranked checklist of pricebt-specific traps (coupons not booked, same-close execution, offsetting MR exits, dropped market dates, frictionless defaults, unit/sign errors) and textbook pitfalls (look-ahead, data snooping, overfitting, costs, regime, bad marks), automated robustness experiments (cost ladder, parameter sweep, truncation and signal-shift look-ahead tests, sub-periods, in/out-of-sample), and a findings report. Use after every backtest and before any tearsheet.
---

# Adversarial review

Assume the backtest is wrong and try to prove it. A finding is a claim with evidence (a number, a file, a rerun); a pass is a check you actually ran. The output feeds the tearsheet's "Review findings" section.

## When to use / not use

- **Use** after every backtest a person might rely on, and again after any material change.
- **Do not** substitute it for the numeric spot checks ([`pricebt-spot-checks`](../pricebt-spot-checks/SKILL.md)). Run both: the review asks whether the *design* is sound, the spot checks whether the *numbers* are right.

## Inputs

- The frozen `strategy_spec.yaml`.
- The `BackTest` object, or the code that produced it.
- The asset config(s).
- The trial log.

## Outputs

`reports/<name>/review.md`. It holds a findings table (ID, severity, finding, evidence, fix or disposition), the list of checks run with PASS/FAIL, and a verdict: **ship**, **ship with caveats**, or **do not ship**.

## Severity

| Level | Meaning | Examples |
|---|---|---|
| **Blocker** | The headline number is wrong or unsupported | a sign or unit error; look-ahead; unpinned resolve; P&L driven by a mark glitch |
| **Major** | Materially misleading without a fix or caveat | costs off; carry strategy with coupons not booked; heavy parameter snooping; a single-regime result |
| **Minor** | Worth stating; does not change the conclusion | a short history; a few dropped dates; rounding |

## Procedure

1. **Read the spec and the code together.** Map every spec field to the construct used (recipes print this with `describe(built)`). Any field silently ignored is a finding.
2. **Walk the checklist** in [`references/checklist.md`](references/checklist.md), top to bottom. The **pricebt-specific section comes first** because those traps are invisible to generic reviewers. Record each item as PASS, FAIL (a finding) or N/A (with a reason).
3. **Run the robustness experiments** (automated when the strategy was built from a spec with the recipes skill):

   ```python
   import sys; sys.path.insert(0, "skills/pricebt-adversarial-review/scripts")
   import robustness
   results = robustness.run_all("reports/<name>/strategy_spec.yaml")   # cost ladder, parameter sweep, truncation, signal shift, sub-periods, IS/OOS
   print(robustness.to_markdown(results))
   ```

   For hand-written strategies, run the same experiments manually. What each one detects, and how to read it, is in [`references/experiments.md`](references/experiments.md).
4. **Judge significance** with [`pricebt-research-methodology`](../pricebt-research-methodology/SKILL.md): `research_stats.sharpe_summary(daily_pnl, n_trials=<trial count>)`. Is the result significant after the trials, and is the out-of-sample drop acceptable?
5. **Use independent lenses if you can spawn sub-agents.** Give each reviewer one lens and the same inputs: (a) look-ahead and data timing; (b) economics and units (signs, bp vs decimal, dv01 weights); (c) statistics (snooping, significance, regime); (d) implementation (spec vs code, gs semantics). A second agent should try to *refute* each finding before it is accepted. The prompts are in [`references/reviewer-prompts.md`](references/reviewer-prompts.md).
6. **Write `review.md`** from the template in [`references/review-template.md`](references/review-template.md) and give the verdict. Every Blocker must be fixed and the backtest re-run. Every Major is fixed or carried into the tearsheet as a caveat.

## Checks

- Every checklist item is marked, and N/A items have a reason.
- The robustness experiments ran, or the review explains why not.
- No Blocker is open. Each Major is fixed or disclosed.

## Pitfalls

- **Reviewing only the summary numbers.** Most bugs show up in the ledger: trade counts, holding periods, entry values that are not ≈ 0 for ATM trades.
- **Accepting "it's a known gs behaviour".** Parity explains the behaviour; it doesn't make the P&L realistic. Disclose it.
- **Moving the goalposts.** Success criteria are fixed in the spec; the review does not relax them after seeing results.

## Related skills

- [`pricebt-spot-checks`](../pricebt-spot-checks/SKILL.md) (numbers), [`pricebt-research-methodology`](../pricebt-research-methodology/SKILL.md) (standards), [`pricebt-tearsheet-report`](../pricebt-tearsheet-report/SKILL.md) (delivery).
