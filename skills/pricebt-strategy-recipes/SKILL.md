---
name: pricebt-strategy-recipes
description: Turn a validated pricebt strategy spec into a runnable gs-style backtest (Strategy + triggers + actions + GenericEngine.run_backtest kwargs) with one recipe per archetype — periodic roll/carry, mean reversion, momentum, dv01-neutral curve trade, delta-hedged, risk band, event — plus a stop-loss overlay, cost and financing mapping. Use after pricebt-strategy-intake has produced a spec, or when you need the exact pricebt construct for a trading rule.
---

# pricebt strategy recipes

A spec (`skills/pricebt-strategy-intake/templates/strategy_spec.yaml`) says *what* to trade and *when*; this skill turns it into the pricebt objects that do it. pricebt's backtest API is gs_quant.backtests almost 1:1, so every recipe is plain gs constructs — `PeriodicTrigger`, `MeanReversionTrigger`, `AggregateTrigger`, `StrategyRiskTrigger`, `AddTradeAction`, `AddScaledTradeAction`, `HedgeAction`, `ExitAllPositionsAction` — assembled by `scripts/recipes.py`. The script also records every modelling choice and approximation it made (`built.notes`), so the report can state them. pricebt prices nothing itself: every number comes from the asset configs named in the spec.

## When to use / not use

- **Use** when you have a spec (from [`pricebt-strategy-intake`](../pricebt-strategy-intake/SKILL.md)) and need a `BackTest`.
- **Use** the catalogue ([references/archetype-catalogue.md](references/archetype-catalogue.md)) when you hand-write a strategy and want the right construct for a rule.
- **Do not use** to judge the result: run [`pricebt-spot-checks`](../pricebt-spot-checks/SKILL.md) and [`pricebt-adversarial-review`](../pricebt-adversarial-review/SKILL.md) next.
- **Do not use** for archetype `custom`: `build` refuses it. Write the strategy by hand following the catalogue patterns (compose triggers and actions directly).

## Inputs

- A spec: a YAML path or a dict. `build`/`run` apply the template defaults and validate it first (via `skills/pricebt-strategy-intake/scripts/spec.py`), and never mutate the dict you pass, so a robustness script can `copy.deepcopy(spec)`, change `costs.level`, `signal.params`, `signal.lookback`, `signal.lag` or `dates.end`, and rerun.
- The asset configs the spec lists (paths from the repository root; e.g. `tests/assets/toy_usd_irs.yaml`).

## Outputs

- `build(spec) -> Built(strategy, run_kwargs, signal, notes, spec)`:
  - `strategy`: a `pricebt.backtests.strategy.Strategy`;
  - `run_kwargs`: kwargs for `GenericEngine().run_backtest` (`start, end, frequency, risks, holiday_calendar, result_ccy, initial_value, show_progress=False`);
  - `signal`: the series the trigger reads (mean reversion: the measure; momentum: its change over the lookback), else `None`;
  - `notes`: the modelling choices and approximations, one line each.
- `run(spec) -> (BackTest, Built)`.
- `describe(built) -> str`: exactly which gs constructs were used, for the report.
- `signal_series(spec)`: the raw measure series (`pricebt.data.measure_series` of a fresh, unresolved copy of `signal.instrument`).
- The archetype builders, usable directly with instruments and keyword arguments; each returns `Parts(triggers, signal, notes)`.

## Procedure

1. **Validate the spec** (fix every error before going on):

   ```powershell
   $env:PYTHONPATH = "src;tests"; python skills/pricebt-strategy-intake/scripts/spec.py validate SPEC.yaml
   ```

2. **See what will be built** without running it:

   ```powershell
   python skills/pricebt-strategy-recipes/scripts/recipes.py describe SPEC.yaml
   ```

   Read every line of `modelling notes`. Each one is a statement the report must carry (for example "exits are offsetting trades held forever").

3. **Run it** in-process, so later skills get the live `BackTest`:

   ```python
   import sys; sys.path.insert(0, "skills/pricebt-strategy-recipes/scripts")
   import recipes
   backtest, built = recipes.run("SPEC.yaml")
   print(recipes.describe(built))
   backtest.result_summary, backtest.trade_ledger()
   ```

   Or from the shell: `python skills/pricebt-strategy-recipes/scripts/recipes.py run SPEC.yaml --out result_summary.csv`.

4. **Rerun for robustness** by editing a copy of the spec dict:

   ```python
   import copy
   spec = recipes.specmod.load_spec("SPEC.yaml")
   for level in (0.25, 0.5):
       s = copy.deepcopy(spec); s["costs"]["level"] = level
       bt, _ = recipes.run(s)
   lagged = copy.deepcopy(spec); lagged["signal"]["lag"] = 1   # look-ahead check: trade on yesterday's signal
   ```

5. **For an idea no archetype fits**, compose the constructs yourself: start from the closest catalogue entry, use the builders as parts (`Strategy(None, recipes.periodic_roll(swap, frequency="1m").triggers + my_triggers)`), and check every construct against [references/construct-cheatsheet.md](references/construct-cheatsheet.md).

## Archetypes at a glance

| archetype | trigger(s) | action(s) | key spec fields |
|---|---|---|---|
| `periodic_roll` | `PeriodicTrigger(rebalance.frequency)` | entry action(primary, `trade_duration`) | `rebalance.*` |
| `mean_reversion` | `MeanReversionTrigger(GenericDataSource(signal), z_entry, lookback, lookback)` | `AddTradeAction(primary)` | `signal.*` (type `par_rate_zscore`) |
| `momentum` | `AggregateTrigger(ALL_OF[Periodic, MktTrigger(change, ±threshold, ABOVE/BELOW)])` ×2 | entry action(primary / its opposite, `'next schedule'`) | `signal.*` (type `rate_momentum`), `rebalance.*` |
| `curve_trade` | `PeriodicTrigger` | one `AddScaledTradeAction(risk_measure)` per leg | `instruments.primary/second`, `sizing.dv01_target` |
| `delta_hedged` | `DateTrigger([start])` + `PeriodicTrigger` | entry action + `HedgeAction(IRDelta(Type), hedge)` | `instruments.hedge`, `rebalance.frequency` |
| `risk_band` | `PeriodicTrigger` + 2 × `StrategyRiskTrigger(IRDelta(Type), ±max)` | entry action + `HedgeAction` | `risk_limits.max_abs_dv01`, `instruments.hedge` |
| `event` | `DateTrigger(event_dates)` | entry action(primary, `trade_duration`) | `event_dates` |
| overlay | `StrategyRiskTrigger(Price, -stop_loss_mtm, BELOW)` | `ExitAllPositionsAction` | `risk_limits.stop_loss_mtm` |

"Entry action" is `AddTradeAction` for `sizing.method: notional`, `AddScaledTradeAction(scaling_type=risk_measure, scaling_risk=IRDelta(aggregation_level='Type'), scaling_level=±dv01_target)` for `dv01_target`, and `AddScaledTradeAction(scaling_type=NAV)` for `nav`. The full mapping, code and caveats per archetype are in [references/archetype-catalogue.md](references/archetype-catalogue.md).

### Frictions, financing, reporting

| spec | pricebt |
|---|---|
| `costs.model: none` | `ConstantTransactionModel(0)` |
| `costs.model: constant` | `ConstantTransactionModel(level)`: currency per trade, per side |
| `costs.model: notional_bp` | `ScaledTransactionModel('notional_amount', level * 1e-4)` |
| `costs.model: dv01_bp` | `ScaledTransactionModel(IRDelta(aggregation_level='Type'), level)`: `level × |dv01|` currency per side |
| `financing.cash_accrual_rate` | `Strategy(cash_accrual=ConstantCashAccrualModel(rate))` when > 0 |
| `risks_to_report` | names from `pricebt.risk` (`Price`, `IRDeltaParallel`, `IRDelta(aggregation_level='Type')`); the book dv01 is added when sizing or hedging uses it |
| `result_ccy` | every measure given to a trigger or hedge is rewritten to `measure(currency=result_ccy)` |
| `signal.lag` | the signal series is `series.shift(lag)` with leading NaNs dropped (robustness only) |
| `risk_limits.hedge_risk_percentage` | optional, not in the template: `HedgeAction.risk_percentage` for `risk_band` (default 100) |

The same cost model is applied to entries, exits and hedges. Costs are booked in the `Transaction Costs` column of `result_summary` (negative) and `Total = Price + Cumulative Cash + Transaction Costs`.

## Checks

- `spec.py validate` prints `OK`.
- `describe(built)` names the triggers and actions you expected, and you have read every note.
- On the run: `result_summary["Total"] == Price + "Cumulative Cash" + "Transaction Costs"` on every row, `Transaction Costs` is negative when `costs.level > 0`, and two runs of the same spec give identical frames.
- The archetype's own behaviour holds, as the tests in `tests/skills/test_skill_recipes.py` assert on the toy assets: monthly roll names; mean-reversion trades that never close; momentum exits on the next roll date; curve legs each at `±dv01_target` and netting to 0 on entry dates; hedged book dv01 ≈ 0; risk band respected on every date; trades exactly on the event dates; flat after a stop-loss.

## Pitfalls

- **Same-close signal and execution** (gs semantics): a trigger reads the signal at date `d`'s close and the trade is struck at that same close. Run `signal.lag: 1` as a look-ahead check.
- **Mean reversion never closes a trade.** The gs trigger's exit is an *offsetting* trade; both legs stay open. The trade ledger shows every row open, and `Trade PnL` is `None`. Read P&L from `result_summary`.
- **Mean reversion supports notional sizing only**: its ±1 direction travels in `AddTradeActionInfo.scaling`, which `AddScaledTradeAction` never reads. `validate_spec` rejects the combination.
- **`dv01_target` signs**: `AddScaledTradeAction` uses `scale = scaling_level / unit_risk`. A receiver's unit dv01 is negative (payer > 0), so its level must be negative too or the receiver silently turns into a payer. The recipes sign the level by `pay_or_receive`.
- **`'next schedule'`** exits at the next date of the trigger that fired: the next roll for Periodic/Aggregate, the next listed date for DateTrigger, never (held to the end) after the last one. MeanReversion supplies `next_schedule=None` (held to the end); a lone MktTrigger or a StrategyRiskTrigger supplies no info at all, and an action with `'next schedule'` under it raises `RuntimeError('Next schedule not supported by action')`.
- **HedgeAction** sizes every hedge placed on a date against the same pre-hedge book risk; two hedge actions on one date would each hedge the full risk.
- **Missing markets are dropped**, not filled: the default session policy `missing_market='drop'` removes grid dates with no market for any traded asset (listed on `backtest.missing_market_dates`); triggers never fire on those dates.
- **NAV sizing** divides the cash budget by the entry PV. Par (ATM) swaps have PV ≈ 0, so use it only for premium instruments such as swaptions.
- **Stop-loss is an approximation**: it reads the PV of the positions open that day, not realised P&L (gs has no P&L trigger), and later entry triggers can re-enter.
- **`stop_loss_mtm` with `result_ccy`** is rejected by validation: `run_backtest` de-duplicates its risks before the `result_ccy` rewrite, so the trigger's `Price(currency=...)` and the engine's own price column would appear twice.
- **Name every action** when composing by hand: unnamed actions take a process-global `Action{N}` name, so two builds of the same strategy name their trades differently.

## P&L explain (delta / gamma / carry)

`scripts/swap_pnl.py` adds a swap `PnlDefinition` on top of `bt.pnl_explain()` (see
[references/construct-cheatsheet.md](references/construct-cheatsheet.md)'s "P&L explain" section for
the full API). One-line wiring:

```python
import sys; sys.path.insert(0, "skills/pricebt-strategy-recipes/scripts")
import swap_pnl

backtest = GenericEngine().run_backtest(
    strategy, start=start, end=end, frequency="1b",
    risks=[swap_pnl.CashPaidToDate],              # optional: needed for the `cash` column
    pnl_explain=swap_pnl.swap_pnl_definition(),    # -> bt.pnl_explain() now returns PNL_delta/_gamma/_carry
)
table = swap_pnl.explain_table(backtest)           # actual_dpv, cash, economic, PNL_*, explained, residual
stats = swap_pnl.explain_stats(table)              # totals, r2, residual_share, worst-residual date
```

`run_backtest`'s own `pnl_explain=` kwarg takes the definition directly (it becomes `BackTest.pnl_explain_def`) --
no separate wiring step. Read `docs/v2/PNL_EXPLAIN_PLAN.md` sections 2 and 2.7 before trusting a residual: a
book that isn't near-ATM (a held-to-maturity trade, an off-market entry) carries a real, expected
first-order residual that is not a bug (`swap_pnl.exact_split(backtest)` is the diagnostic for it).
Only for `IRSwap` assets whose config maps `gamma`/`theta`/`year_fraction` (ASSET_CONFIG_GUIDE.md's
"P&L explain functions" section) -- an asset without them raises a clean `ConfigError` naming the
missing measure, or pass `gamma=False, carry=False` for delta-only attribution on any swap config.

## Related skills

- [`pricebt-strategy-intake`](../pricebt-strategy-intake/SKILL.md): writes the spec this skill consumes.
- [`pricebt-strategy-workflow`](../pricebt-strategy-workflow/SKILL.md): the end-to-end order of skills.
- [`pricebt-spot-checks`](../pricebt-spot-checks/SKILL.md): verify the run.
- [`pricebt-tearsheet-report`](../pricebt-tearsheet-report/SKILL.md): report it, including `describe(built)` and the notes.
- [`pricebt-port-gs-notebook`](../pricebt-port-gs-notebook/SKILL.md): when you start from an existing gs notebook instead of a spec.
