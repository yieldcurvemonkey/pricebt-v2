---
name: pricebt-strategy-workflow
description: The end-to-end pipeline for a strategy study in pricebt - plain-English idea -> follow-up questions and a frozen strategy spec -> implementation -> adversarial review -> spot checks -> tearsheet delivery - with a gate between stages and a one-command driver (run_study.py) that produces the automated parts, for swap, swaption and bond strategies. Use whenever someone says "backtest this", "test this idea", or "is this strategy any good".
---

# Strategy workflow: idea → tearsheet

This is the orchestrator. Each stage has its own skill, and this one says what order to run them in, what must be true before moving on, and what to hand over at the end.

```
0 PRECONDITION     asset configs exist and pass the checker      pricebt-connect-pricing-library, pricebt-verify-asset-config
1 INTAKE           idea -> questions -> frozen strategy_spec     pricebt-strategy-intake
2 IMPLEMENT        spec -> pricebt Strategy -> BackTest           pricebt-strategy-recipes
3 ADVERSARIAL      design review + robustness experiments        pricebt-adversarial-review
4 SPOT CHECKS      numeric verification of the run               pricebt-spot-checks
5 REPORT           tearsheet + summary for the requester         pricebt-tearsheet-report
                   (standards throughout: pricebt-research-methodology)
```

## When to use / not use

- **Use** for any request to evaluate a trading idea.
- **Not for** wiring a new pricing library. That is stage 0; do it first with [`pricebt-connect-pricing-library`](../pricebt-connect-pricing-library/SKILL.md).

## Inputs and outputs

- **Input:** the idea (plain English), plus the location of the asset configs.
- **Output:** `reports/<name>/`, containing:
  - `strategy_spec.yaml` (frozen);
  - `trials.csv`;
  - `review.md`, `spot_checks.md`, `robustness.md`;
  - `tearsheet.html`, `tearsheet.md`, `metrics.json`, `trades.csv`;
  - a short summary message for the requester.

## Fast path (one command after intake)

Try it first on the shipped example (toy market, no library needed): `skills/pricebt-strategy-workflow/example/toy_momentum_spec.yaml`.

```powershell
$env:PYTHONPATH = "src;tests"
python skills/pricebt-strategy-workflow/scripts/run_study.py reports/<name>/strategy_spec.yaml
```

The driver runs these stages in order and writes their outputs into `deliverables.out_dir`:
1. defaults and validation;
2. the backtest (via the recipes);
3. the robustness experiments;
4. the spot checks;
5. the tearsheet (with the review stub attached).

It does **not** do the judgment work: the adversarial review's checklist walk, the manual spot checks, and the verdict. Those stay with you, in stages 3–4 below.

## Procedure and gates

**Stage 0: precondition.**
- Every instrument in the idea has an asset config.
- `python skills/pricebt-verify-asset-config/scripts/check_asset.py <config>` exits 0 for each.
- *Gate:* no FAIL in the checker. Without this, every later number is suspect.

**Stage 1: intake** ([`pricebt-strategy-intake`](../pricebt-strategy-intake/SKILL.md)).
- Interactive: ask the must-ask questions in one message, with defaults shown.
- Autonomous: use the defaults and list them under `assumptions:`.
- Write the hypothesis, the out-of-sample split and the success criteria **before** the first run.
- *Gate:* `spec.py validate` exits 0, and the spec is frozen (copied into the report folder).

**Stage 2: implement** ([`pricebt-strategy-recipes`](../pricebt-strategy-recipes/SKILL.md)).
- Use `recipes.run(spec)` for a built-in archetype. For a custom idea, compose triggers and actions by following the archetype catalogue.
- Append every run to `trials.csv`.
- *Gate:* the run completes, and `Total == Price + Cumulative Cash + Transaction Costs` holds on every row.

**Stage 3: adversarial review** ([`pricebt-adversarial-review`](../pricebt-adversarial-review/SKILL.md)).
- Walk the checklist, pricebt traps first.
- Run the robustness experiments.
- Judge significance after deflating for the number of trials.
- Use independent reviewer agents if you can.
- *Gate:* no open Blocker. Each Major is fixed, or recorded as a caveat for the report.

**Stage 4: spot checks** ([`pricebt-spot-checks`](../pricebt-spot-checks/SKILL.md)).
- Run the automated checks: identity, trade repricing, cash roll-forward, P&L explain, determinism.
- Then do the manual checks: reprice two trades in the library directly; perturb one parameter; shift the signal by one day.
- *Gate:* no FAIL. Each WARN is explained.

**Stage 5: report** ([`pricebt-tearsheet-report`](../pricebt-tearsheet-report/SKILL.md)).
- Build the tearsheet with the spec, the review findings, the spot checks and the caveats attached.
- Write the verdict paragraph.
- *Deliver:* the tearsheet path, plus a five-line summary (template below).

### Swaptions and bonds through the pipeline

The stages are the same; these points differ from a swap study.

- **Stage 0.** A swap or swaption config loads only if every measure of its class's contract (`src/pricebt/risk/contracts.py`) is mapped; a bond config, if each is mapped or declared under `unsupported_measures:`. Loading proves nothing about signs, units or expiry behaviour, so still run [`pricebt-verify-asset-config`](../pricebt-verify-asset-config/SKILL.md). Before stage 2, confirm the config answers what the recipe needs, e.g. `Price` on the expiry date and an `expiration_date` attribute for options held to expiry, and `Cashflows` for bond coupons.
- **Stage 1.** Ask the instrument-specific questions (position vs option type, expiry, tail, strike, settlement, premium 0; bond identifier, `size`, coupons, repo) from the intake question bank. `spec.py validate` rejects unstated positions, unknown kwargs and non-zero premiums.
- **Stage 2.** Start from the matching spec in `skills/pricebt-strategy-recipes/example/`. Copy the class notes in `built.notes` (premium, expiry exits, coupons not booked, cross-type delta additivity) into the caveats.
- **P&L decomposition.** The spec has no `pnl_explain` field yet, and `run_study.py` does not pass one. For the attribution, run the backtest yourself: `built = recipes.build(spec)`, `built.run_kwargs["pnl_explain"] = attribution.definition_for(session)` (it reads the configs' units and refuses a book it cannot attribute), then `GenericEngine().run_backtest(built.strategy, **built.run_kwargs)`, and read `bt.pnl_explain_table()`. For a bond book, report its `economic_pnl` next to `Total`: the engine books no coupons. Reading the table and diagnosing residuals: [`pricebt-pnl-attribution`](../pricebt-pnl-attribution/SKILL.md).
- **Automated gaps in `run_study.py`.**
  - Its single-factor "P&L explain" spot check (`risk[t-1] × Δrate`) reads the signal instrument's `IRFwdRate` (swap par rate, swaption forward, bond yield; falling back to a `par_rate` function), converted to bp from the declared unit. For an option book it is only a first-order sanity check: vega, gamma and theta are real P&L, so use the decomposition above.
  - Its standard caveats cover swap archetypes only. Add the class caveats by hand.

If a gate fails, go back to the stage that owns the problem. A bug found in review is fixed in the code (stage 2), and the study re-runs from there. A changed spec is a new trial: log it, and keep the out-of-sample window untouched.

## Summary message template

```
<name>: <verdict: promising / inconclusive / reject> - <one clause why>.
Result: Sharpe <x> (t <t>, <years>y, <trials> trials), total P&L <ccy> <amount>, max drawdown <amount>, <n> trades.
Robustness: costs x2 -> Sharpe <x>; OOS Sharpe <x>; signal shifted 1d -> <x>.
Caveats: <top 2-3, e.g. coupons between marks not booked; option premium = entry PV, expiry at intrinsic; 2021-2024 only; assumed 0.25bp costs>.
Tearsheet: reports/<name>/tearsheet.html
```

## Autonomy rules

- **Interactive users:** ask once, at intake. Do not come back later with questions a default could answer.
- **Autonomous runs:** never block. Default, record the assumption, and surface the high-impact assumptions in the first line of the report.
- **Never** move the success criteria or the out-of-sample boundary after seeing results.

## Related skills

Every stage skill above, plus [`pricebt-research-methodology`](../pricebt-research-methodology/SKILL.md) for the standards and [`pricebt-start-here`](../pricebt-start-here/SKILL.md) for navigation.
