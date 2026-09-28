# Robustness experiments: what each detects and how to read it

[`../scripts/robustness.py`](../scripts/robustness.py) runs these automatically for any spec the recipes skill can build: `robustness.run_all(spec_path_or_dict)`. Each experiment reruns the backtest with one change and compares the result with the base run.

| Experiment | Change | Detects | Pass when |
|---|---|---|---|
| `cost_ladder` | `costs.level` × 0, 1, 2, 3 | a strategy that only works frictionless; the break-even cost | Sharpe at 1× still meets the success criterion; the break-even cost is comfortably above realistic bid/offer |
| `parameter_sweep` | each numeric signal parameter (lookback, z_entry, threshold) at 0.5×, 0.75×, 1.25×, 1.5× | a result sitting on a spike | the neighbours are within about 50% of the base Sharpe and have the same sign |
| `truncation` | end date moved 60 business days earlier | look-ahead: future data changing past decisions | every trade opened before the new end date is identical (name, open date, open value) |
| `signal_shift` | the signal series lagged by one business day | same-close dependence or leakage | Sharpe decays gradually; a collapse to ≤ 0 means the edge needed same-bar information (flag A3 / B4) |
| `sub_periods` | the backtest split into contiguous thirds (or years) | a single-regime result | P&L has the same sign in most sub-periods; no single period carries more than about 60% of the total |
| `is_oos` | separate statistics before and after `dates.in_sample_end` | overfitting | OOS Sharpe ≥ `success_criteria.min_oos_sharpe` and the drop is not severe |

Reading the results:
- **Every experiment is a trial.** The report states the experiments ran as robustness checks, not as tuning. Never adopt a parameter because the sweep liked it better; that is a new trial on the in-sample data.
- **Signal-based archetypes only.** The shift and sweep experiments apply to archetypes with a signal (mean reversion, momentum). For others they report N/A with a reason.
- **Determinism.** Each run uses the same spec except for the change, so any difference is caused by the change.
