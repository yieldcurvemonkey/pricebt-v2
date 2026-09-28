# Review: <strategy name>

- **Spec:** `reports/<name>/strategy_spec.yaml` (frozen at <commit or time>)
- **Backtest:** <start>..<end>, grid <frequency>, <N> trades, trial count <T>
- **Reviewers:** <agent / lenses>
- **Verdict:** **ship** | **ship with caveats** | **do not ship**. <One sentence on why.>

## Findings

| ID | Severity | Finding | Evidence | Fix / disposition |
|---|---|---|---|---|
| R1 | Blocker/Major/Minor | … | number, file:line, experiment name | fixed in run <id> / disclosed as caveat / accepted because … |

## Checklist

| Section | Item | Result | Note |
|---|---|---|---|
| A. pricebt traps | A1 … A14 | PASS/FAIL/N/A | … |
| B. Look-ahead | B1 … B5 | | |
| C. Statistics | C1 … C8 | | |
| D. Economics | D1 … D8 | | |

## Robustness experiments

Paste `robustness.to_markdown(results)` here, or the manual equivalent:

| Experiment | Result | Reading |
|---|---|---|
| cost_ladder | Sharpe at 0×/1×/2×/3×: … ; break-even ≈ … bp | |
| parameter_sweep | plateau / spike | |
| truncation | identical / differs at … | |
| signal_shift | Sharpe base … → shifted … | |
| sub_periods | … | |
| is_oos | IS … / OOS … | |

## Significance

`sharpe_summary`: Sharpe …, t …, SE …, years …, trials …, significant after deflation: yes / no.

## Caveats carried to the tearsheet

- Coupons paid between marks are not booked (gs parity); relevant here because …
- …
