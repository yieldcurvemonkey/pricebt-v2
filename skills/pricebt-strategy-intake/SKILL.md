---
name: pricebt-strategy-intake
description: Turn a plain-English trading idea into a complete, validated strategy_spec.yaml by asking the portfolio-management follow-up questions (hypothesis, instruments, signal and lookback windows, rebalance and holding period, sizing, risk limits, costs, financing, dates and out-of-sample split, success criteria) - interactively, or autonomously with stated defaults - for swaps, swaptions (expiry, tail, strike, straddles, buy/sell) and bonds (identifier, size, coupons, financing). Use at the start of any "backtest this idea" request, before writing strategy code.
---

# Strategy intake: idea → strategy spec

Every strategy study starts here. The output is one file, `strategy_spec.yaml`, which the implementation, review and report skills all read. The template, with every field documented and defaulted, is [`templates/strategy_spec.yaml`](templates/strategy_spec.yaml). The helper [`scripts/spec.py`](scripts/spec.py) fills defaults, records the assumptions it made, and validates the spec.

## When to use / not use

- **Use** whenever a user describes an idea in words ("fade big moves in 10y", "carry trade on the belly", "steepener when the Fed pauses").
- **Skip** only if the user hands you a complete spec file. Then just run `python skills/pricebt-strategy-intake/scripts/spec.py validate <spec>`.

## Inputs

- The idea, verbatim.
- The asset configs available: `configs/assets/*.yaml`, or the user's private configs. For a dry run, use the toy assets in `tests/assets/`.

## Outputs

`reports/<name>/strategy_spec.yaml`. It is complete and valid, and it lists under `assumptions:` every default relied on.

## Procedure

1. **Restate the idea** in one sentence, and classify it into an archetype: `periodic_roll` (carry/roll-down), `mean_reversion`, `momentum`, `curve_trade`, `delta_hedged`, `risk_band`, `event`, or `custom`. See [`pricebt-strategy-recipes`](../pricebt-strategy-recipes/SKILL.md). Swaption and bond ideas use the same archetypes (table below); start from the matching spec in `skills/pricebt-strategy-recipes/example/`.
2. **Draft the spec.** Copy the template and fill in everything the idea states outright.
3. **Ask the follow-up questions.** Take them from [`references/question-bank.md`](references/question-bank.md), in its priority order.
   - **Interactive mode:** ask the **must-ask** questions in a single message, at most 8, grouped and each with the default you would use. That way the user can answer "defaults are fine". Ask the *should-ask* questions only if the answer changes the build.
   - **Autonomous mode** (the user is not available): do not ask. Use the defaults and write each one as a line under `assumptions:`, e.g. `sizing: notional 10mm per trade (default; idea gave no size)`. Flag high-impact assumptions (dates, costs, sizing) in the report's first paragraph.
4. **Write the hypothesis and success criteria before any backtest** ([`pricebt-research-methodology`](../pricebt-research-methodology/SKILL.md) rules 1, 2 and 4):
   - `hypothesis:` gives why it should work and when it should fail;
   - set `dates.in_sample_end` for the out-of-sample split;
   - set the `success_criteria:` thresholds;
   - estimate the parameter count (≤ 5).
5. **Fill defaults and validate:**

   ```powershell
   python skills/pricebt-strategy-intake/scripts/spec.py defaults reports/<name>/strategy_spec.yaml   # prints the assumptions it added
   python skills/pricebt-strategy-intake/scripts/spec.py validate reports/<name>/strategy_spec.yaml   # exit 0 = valid
   ```

   `validate` checks every `instruments.<name>.kwargs` against the generated gs class (`IRSwap`, `IRSwaption`, `Bond`, ...): an unknown name (gs would silently ignore a typo such as `expiry`), a bad enum value, an unstated position (`buy_sell` for swaptions and bonds, the option type `pay_or_receive` for swaptions, a bond's `identifier`), and a non-zero `premium`/`fee`. It also checks that curve-trade legs are opposite positions for any class, and that dv01-sized legs have a delta sign (a Straddle has none). The rules live in `skills/pricebt-strategy-intake/scripts/instrument_terms.py`.
6. **Check that the instruments exist.** Each `instruments.<name>` must match exactly one registered asset. If no config exists for an instrument, stop the strategy work and go to [`pricebt-connect-pricing-library`](../pricebt-connect-pricing-library/SKILL.md). For a swaption or bond, also confirm the config answers the measures the recipe needs (the recipes catalogue lists them per recipe), e.g. a swaption held to expiry needs `Price` on the expiry date and an `expiration_date` attribute.
7. **Freeze the spec.** Commit it, or copy it into the report folder, before the first run. Later changes are new trials and go in the trial log.

## Translating PM language into spec fields

| The user says | Spec |
|---|---|
| "fade", "revert", "rich/cheap", "stretched" | `archetype: mean_reversion`, `signal.type: par_rate_zscore`, `signal.params.z_entry` |
| "trend", "breakout", "follow the move" | `archetype: momentum`, `signal.type: rate_momentum`, `signal.lookback` |
| "carry", "roll-down", "harvest" | `archetype: periodic_roll`, `rebalance.frequency`, `rebalance.trade_duration: next schedule` |
| "steepener / flattener", "2s10s", "fly" | `archetype: curve_trade`, two or three named instruments, `sizing.method: dv01_target` |
| "keep it hedged", "delta-neutral" | `archetype: delta_hedged` with an `instruments.hedge` |
| "stay within X per bp" | `risk_limits.max_abs_dv01` (`archetype: risk_band` if it drives the trading) |
| "cut it if it loses X" | `risk_limits.stop_loss_mtm`. This is an approximation: gs has no P&L trigger, so it acts on open-position PV |
| "around FOMC / auctions / month-end" | `archetype: event`, `event_dates` |
| "$X per bp", "10k dv01" | `sizing.method: dv01_target`, `sizing.dv01_target` |
| "costs of a quarter of a bp" | `costs.model: dv01_bp`, `costs.level: 0.25` |
| "buy payers / receivers and hold to expiry", "roll 1m options" | `archetype: periodic_roll`, IRSwaption primary, `rebalance.trade_duration: expiration_date` |
| "sell vol", "short straddle", "vol carry", "theta harvest" | `archetype: delta_hedged`, IRSwaption `pay_or_receive: Straddle`, `buy_sell: Sell`, IRSwap hedge, `risk_limits.hedge_measure: IRDeltaParallel` |
| "gamma scalp", "long vol hedged" | the same with `buy_sell: Buy` |
| "vega-neutral", "calendar" | `delta_hedged` with a swaption hedge and `risk_limits.hedge_measure: IRVegaParallel` |
| "vol is rich / cheap" | `archetype: mean_reversion`, `signal.measure: IRAnnualImpliedVol`, a bought straddle primary |
| "bond carry and roll-down", "own the 10y" | `archetype: periodic_roll`, Bond primary, `trade_duration: next schedule`; repo from the bond config (GC or special, overnight or term, haircut under `assumptions`), `financing.cash_accrual_rate: 0.0` |
| "asset swap", "bonds vs swaps", "swap spread" | `archetype: curve_trade`, Bond `buy_sell: Buy` + IRSwap `pay_or_receive: Pay`, `sizing.method: dv01_target` |

## Checks

- `spec.py validate` exits 0.
- `assumptions:` is non-empty whenever any default was used, and each line says what was assumed and why.
- The hypothesis, out-of-sample split and success criteria were written before the first backtest.

## Pitfalls

- **Unstated execution timing.** pricebt trades at the close its trigger observes. If the idea is "trade the next morning", record that in `assumptions` and flag it in the review.
- **Sizing by notional across tenors.** A 10mm 2y and a 10mm 10y are very different risks. Size curve trades by dv01.
- **Unlimited lookback tuning.** Pick the lookback from a coarse grid declared in advance (e.g. 20/60/120 business days), and log every one you try.
- **Swaption direction.** "Short payers" is `pay_or_receive: Pay`, `buy_sell: Sell`, not `pay_or_receive: Receive`. The option type and the position are separate kwargs, and both must be stated.
- **Signal measure names.** Prefer the gs level measure (`signal.measure: IRFwdRate`: a swap's par rate, a swaption's forward, a bond's yield) over a config function name such as `par_rate`, which only some configs define. The name is resolved through each asset config's `risk_measures:`.
- **Bond coupons and financing.** Every Bond config maps the financing contract (`RepoRate`, `RepoHaircut`, `FinancingToDate`), and the engine books a held bond's coupons and repo interest as cash (holding cash, DEV-E22), so `Total` is the financed P&L. The repo terms live in the config, not the spec: ask GC or special, overnight or term, and the haircut, choose the config that matches, and write them under `assumptions` ([question bank](references/question-bank.md)). Keep `financing.cash_accrual_rate` at 0: a cash-accrual model charges the funding a second time (the engine warns). Swaps and swaptions are unchanged: the engine books no coupons for them (gs parity).

## Related skills

- Next: [`pricebt-strategy-recipes`](../pricebt-strategy-recipes/SKILL.md).
- The whole pipeline: [`pricebt-strategy-workflow`](../pricebt-strategy-workflow/SKILL.md).
- Why these questions: [`pricebt-research-methodology`](../pricebt-research-methodology/SKILL.md).
- What a swaption or bond config must compute before a spec can trade it: [`pricebt-risk-measures`](../pricebt-risk-measures/SKILL.md).
