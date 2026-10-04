# Prompts for independent reviewer agents

Give every reviewer the same inputs:
- `reports/<name>/strategy_spec.yaml`;
- the strategy code, or the recipes `describe(built)` output;
- the asset config path(s);
- `reports/<name>/trials.csv`;
- the `result_summary` and `trade_ledger()` exports (CSV);
- `skills/pricebt-adversarial-review/references/checklist.md`.

Each reviewer returns findings as rows: `id | severity | finding | evidence | proposed fix`. Run the lenses in parallel.

## Lens 1: look-ahead and data timing

> You are reviewing a pricebt backtest for look-ahead bias. pricebt trades at the close of the date its trigger observes. For every input series, determine when it would have been known and whether the strategy uses it earlier. Check for full-sample estimates, centred or two-sided windows, back-filled data, and a snapshot-time mismatch between legs. Run or inspect the truncation and signal-shift experiments. Checklist sections A3, A13, A14 and B. Report only findings with evidence.

## Lens 2: economics, units and signs

> You are reviewing a pricebt backtest for economic correctness. Verify:
> - dv01 sign (payer > 0) and units (currency per bp);
> - that rates are in bp;
> - that ATM entries have near-zero PV;
> - that curve legs are dv01-weighted and the signal's weights equal the traded weights;
> - that costs are charged per side;
> - that carry-dependent swap P&L is disclosed as missing coupons between marks (gs parity);
> - for options: vega per bp of normal vol, the theta sign (a bought option loses time value), expiry at intrinsic, a point-in-time vol surface, and vega concentration in one expiry;
> - for bonds: clean vs dirty price; the T+1 settlement and the coupon drop dates; the repo (general collateral vs special, overnight vs term, the haircut) behind the holding cash the engine books, and that no `cash_accrual` model charges the funding loan a second time; the carry horizon and the roll-down curve; and on-the-run roll survivorship.
>
> Recompute two trades' entry values independently with the pricing library if you can. Checklist sections A1, A2, A4, A5, A7, A9, A10, A16 to A21 and D.

## Lens 3: statistics and overfitting

> You are reviewing a pricebt backtest for data snooping and false significance. Count the free parameters (qualitative ones included) and the trials. Compute t ≈ Sharpe × √years, SE(Sharpe), and the Bonferroni-deflated threshold (`skills/pricebt-research-methodology/scripts/research_stats.py`). Check the parameter sweep for a plateau, the in-sample vs out-of-sample drop, the sub-period stability, and whether the trade count supports the statistics. Checklist section C, plus D4.

## Lens 4: implementation fidelity

> You are reviewing whether the pricebt code implements the spec. Map every spec field to a construct (trigger, action, sizing, cost model, risk list, dates). Flag fields that are ignored or approximated. Check gs semantics that change results: offsetting mean-reversion exits, hedges sized on pre-hedge risk, `'next schedule'` durations, `missing_market='drop'`, and frictionless defaults. Checklist sections A5, A6, A8, A11 and A12.

## Refuter (one per lens, after the findings)

> Here are review findings about a pricebt backtest. For each one, try to refute it from the evidence: is it real, is the evidence accurate, is the severity right, and would the fix work? Default to "refuted" when the finding is unsupported. Return: `id | verdict (confirmed / refuted / partially) | reason`.
