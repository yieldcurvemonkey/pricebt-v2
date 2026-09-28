---
name: pricebt-port-gs-notebook
description: Port a gs_quant backtesting notebook or script (GenericEngine, triggers, actions, result_summary / trade_ledger) to pricebt with the three edits - imports, session, datasets - plus the checks for instruments without an asset config, currencies, unsupported engines and the documented behavioural deviations. Use when a user has existing gs_quant backtest code, or when an idea is easiest to express by adapting a gs example notebook.
---

# Port a gs_quant backtest to pricebt

pricebt's public API mirrors `gs_quant.backtests` (1.5.4 signatures, 2.1.17 behaviour). gs code that uses only the GenericEngine ports with three edits. The rest of this skill covers what does *not* port by itself.

## When to use / not use

- **Use** for gs_quant notebooks or scripts built on `GenericEngine`.
- **Not for** `EquityVolEngine`, `PredefinedAssetEngine` or `StrategySystematic`. These are GS server-side only and raise `NotSupportedError` in pricebt; rewrite them against `GenericEngine`.

## Procedure

1. **Imports.** Replace `gs_quant` with `pricebt` in every import (`from pricebt.backtests.triggers import ...`, `from pricebt.instrument import IRSwap`, `from pricebt.risk import Price, IRDelta`, …). Module paths and class names are identical.
2. **Session.** Replace `GsSession.use(...)` with `PricebtSession.use(assets=[<asset config per instrument type and currency>], fx=<fx config if several currencies>)`. `GsSession.use` still exists as a harmless no-op, so leaving it in does no harm, but it prices nothing.
3. **Datasets.** Replace `Dataset(...).get_data(...)` with either your own `pandas.Series` (indexed by `datetime.date`) or `pricebt.data.measure_series(instrument, "<function>", start, end, frequency="1b")` for a series derived from the pricing library (e.g. a par-rate history). Build series on the full business-day schedule so that `GenericDataSource` never has to fill gaps.
4. **Instruments need asset configs.** Each instrument class and currency in the notebook must match exactly one registered asset (`match:` in the config). An `IRSwap(..., notional_currency='EUR')` needs a EUR swap config. If none exists, go to [`pricebt-connect-pricing-library`](../pricebt-connect-pricing-library/SKILL.md). `IRSwaption`, `FXOption` and the other gs classes exist, but each needs its own config.
5. **gs-only kwarg grammar.** `fixed_rate='ATM+25'` works if the config's `resolve` parses it (the shipped configs do). `strike='=solvefor(...)'` and `notional_amount='100k'` are server-side grammar; replace them with numbers or add parsing to your config.
6. **Risk measures.** Each measure the notebook uses (`IRDelta(aggregation_level='Type')`, `IRDeltaParallel`, `Price`, …) must be mapped under the config's `risk_measures:`. `aggregation_level` Type/Asset/Class returns a float; a bare `IRDelta` returns the bucketed ladder frame. `bump_size` and other finite-difference parameters raise `NotSupportedError`.
7. **Currencies.** A mixed-currency book needs `run_backtest(..., result_ccy='USD')` plus an FX config. Without them, pricebt raises gs's own error, with a hint.
8. **Run and compare.** Result objects are the gs ones: `result_summary` (property), `trade_ledger()`, `strategy_as_time_series()`. The pricebt additions are `summary_stats()`, `pnl_bps(risk)` and `missing_market_dates`.

## Behavioural differences to expect

The full list, with a test for each, is in [`docs/v2/DEVIATIONS.md`](../../docs/v2/DEVIATIONS.md). The ones users notice:

- **`MeanReversionTrigger` works** (the installed gs 1.5.4 crashes on every fire; pricebt uses the 2.1.17 fix).
- **`result_summary` is correct on flat dates.** gs double-counts an exited trade's PV there (DEV-R1).
- **Trigger state resets at the start of every run.** gs carries it across runs (DEV-T10).
- **Missing market dates are dropped with a warning.** gs prices every weekday on server data (DEV-E16).
- **Scaling is a signed `quantity_`,** not an edit of `notional_amount` / `pay_or_receive` (DEV-I1). The ledger names and cash are the same.
- **`initial_value` is seeded on the first grid date** (DEV-E11).

## Known defects in gs's own example notebooks

Fix these while porting (from `docs/v2/research/05-notebook-coverage.md` §8):
- Some notebooks use `PricingContext` without importing it. Add `from pricebt.markets import PricingContext`.
- 040310's title belongs to the inflation notebook.
- 040307 indexes `result_summary` columns by position (`iloc[5, 1]`). Use the RiskMeasure key instead.
- 040311 passes `holiday_calendar` as a tuple. That works; a list is also accepted (DEV-T16).

## Checks

- The notebook runs top to bottom on a toy or real asset, with no `gs_quant` import (`python -c "import sys; ...; assert 'gs_quant' not in sys.modules"` after running).
- The ledger's trade names and dates match the gs logic (the `Action<N>_<name>_<date>` naming is preserved).

## Worked examples in this repository

- gs 040304 (mean reversion) → `notebooks/src/040304_mean_reversion_toy.py` (toy) and `notebooks/src/040304_mean_reversion_usd_sofr_arbs.py` (a real bank library).
- The notebook-by-notebook porting table: `docs/v2/research/05-notebook-coverage.md` §4.

## Related skills

- [`pricebt-architecture`](../pricebt-architecture/SKILL.md) (semantics), [`pricebt-connect-pricing-library`](../pricebt-connect-pricing-library/SKILL.md) (missing configs), [`pricebt-strategy-workflow`](../pricebt-strategy-workflow/SKILL.md) (turn the port into a reviewed study).
