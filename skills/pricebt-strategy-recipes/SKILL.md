---
name: pricebt-strategy-recipes
description: Turn a validated pricebt strategy spec into a runnable gs-style backtest (Strategy + triggers + actions + GenericEngine.run_backtest kwargs) with one recipe per archetype — periodic roll/carry, mean reversion, momentum, dv01-neutral curve trade, delta-hedged, risk band, event — plus a stop-loss overlay, cost and financing mapping, for swaps, swaptions and bonds (swaption rolled to expiry, delta-hedged short straddle / vol carry, bond carry and roll, bond vs swap). Use after pricebt-strategy-intake has produced a spec, or when you need the exact pricebt construct for a trading rule.
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
- The asset configs the spec lists (paths from the repository root; e.g. `tests/assets/toy_usd_irs.yaml`). For swaptions and bonds each config must answer the measures its recipe needs (the catalogue lists them per recipe); the load-time measure contract guarantees every one is mapped or declared unsupported, not that it is right: verify with [`pricebt-verify-asset-config`](../pricebt-verify-asset-config/SKILL.md) first.
- Ready specs for swaptions and bonds, run by the tests on the toy assets: `skills/pricebt-strategy-recipes/example/` (`toy_swaption_expiry_roll.yaml`, `toy_short_straddle_delta_hedged.yaml`, `toy_bond_carry_roll.yaml`, `toy_bond_asset_swap.yaml`). Copy one and point `assets:` at your configs.

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
| `costs.model: notional_bp` | `ScaledTransactionModel('notional_amount', level * 1e-4)`: reads the config attribute `notional_amount` (a Bond config must map one: gs `Bond` sizes by `size`) |
| `costs.model: dv01_bp` | `ScaledTransactionModel(IRDelta(aggregation_level='Type'), level)`: `level × |dv01|` currency per side |
| `financing.cash_accrual_rate` | `Strategy(cash_accrual=ConstantCashAccrualModel(rate))` when > 0 |
| `risks_to_report` | names from `pricebt.risk` (`Price`, `IRDeltaParallel`, `IRDelta(aggregation_level='Type')`, `IRVegaParallel`, `IRGammaParallel`, `Theta`, `Cashflows`, ...; unquoted numbers such as `bump_size=0.5` stay numbers); the book dv01 is added when sizing or hedging uses it |
| `risk_limits.hedge_measure` | optional: the measure `delta_hedged` / `risk_band` hedge and trigger on (default `IRDelta(aggregation_level='Type')`; e.g. `IRDeltaParallel`, or `IRVegaParallel` with a swaption hedge); `max_abs_dv01` is then in its unit |
| `signal.measure` | a gs measure name the config maps (`IRFwdRate`, `IRAnnualImpliedVol`, `ParSpread`) or a config function name (`par_rate`) |
| P&L decomposition | not a spec field yet: `GenericEngine().run_backtest(built.strategy, **built.run_kwargs, pnl_explain=swaption_pnl_definition())`, then `bt.pnl_explain_table()` (catalogue, "Swaption and bond recipes") |
| `result_ccy` | every measure given to a trigger or hedge is rewritten to `measure(currency=result_ccy)` |
| `signal.lag` | the signal series is `series.shift(lag)` with leading NaNs dropped (robustness only) |
| `risk_limits.hedge_risk_percentage` | optional, not in the template: `HedgeAction.risk_percentage` for `risk_band` (default 100) |

The same cost model is applied to entries, exits and hedges. Costs are booked in the `Transaction Costs` column of `result_summary` (negative) and `Total = Price + Cumulative Cash + Transaction Costs`.

### Swaptions and bonds

The archetypes are the same gs constructs for every class. Three things change, all handled by `skills/pricebt-strategy-intake/scripts/instrument_terms.py` (used by both `spec.py` and `recipes.py`):

| class | the position (`flipped` flips it) | the size (`sizing.notional` sets it) | unit `IRDelta` sign (`direction_sign`, signs dv01 sizing) |
|---|---|---|---|
| `IRSwap` | `pay_or_receive` | `notional_amount` | Pay +, Receive − |
| `IRSwaption` | `buy_sell` (`pay_or_receive` is the option type and never flips) | `notional_amount` | Buy payer +, Buy receiver −, Sell flips; Straddle: none (dv01 sizing refused) |
| `Bond` | `buy_sell` | `size` | Buy −, Sell + |

Recipes (each a tested spec in `example/`, each with the measures your config must answer, checks, caveats and its P&L decomposition in [the catalogue](references/archetype-catalogue.md#swaption-and-bond-recipes)):

| idea | archetype | the key spec choices |
|---|---|---|
| buy (or sell) options and hold each to expiry, rolled | `periodic_roll` | IRSwaption primary, `rebalance.trade_duration: expiration_date` |
| sell vol / vol carry / short straddle, delta-hedged | `delta_hedged` | sold `Straddle` primary, `IRSwap` hedge, `risk_limits.hedge_measure: IRDeltaParallel`, `rebalance.frequency: 1b` |
| gamma scalping | `delta_hedged` | the same with `buy_sell: Buy` |
| vega-neutral calendar | `delta_hedged` | swaption hedge of another expiry, `hedge_measure: IRVegaParallel` |
| fade rich / cheap vol | `mean_reversion` | bought straddle primary, `signal.measure: IRAnnualImpliedVol` |
| bond carry and roll-down | `periodic_roll` | Bond primary, `trade_duration: next schedule`, optional `financing.cash_accrual_rate` as repo |
| bond vs swaps (asset-swap-like), swap spread | `curve_trade` | bought Bond + payer IRSwap, `sizing.method: dv01_target` |

`build` adds a note per class to `built.notes` (swaption premium and expiry exits, bond coupons not booked, mixed-type delta additivity): the report must carry them.

## Checks

- `spec.py validate` prints `OK`.
- `describe(built)` names the triggers and actions you expected, and you have read every note.
- On the run: `result_summary["Total"] == Price + "Cumulative Cash" + "Transaction Costs"` on every row, `Transaction Costs` is negative when `costs.level > 0`, and two runs of the same spec give identical frames.
- The archetype's own behaviour holds, as the tests in `tests/skills/test_skill_recipes.py` assert on the toy assets: monthly roll names; mean-reversion trades that never close; momentum exits on the next roll date; curve legs each at `±dv01_target` and netting to 0 on entry dates; hedged book dv01 ≈ 0; risk band respected on every date; trades exactly on the event dates; flat after a stop-loss.
- Swaptions and bonds, on your configs as on the toys: every leg of a dv01-sized book has `quantity_ > 0` (a negative quantity means the level had the wrong sign and the position flipped); an option held to expiry closes on its resolved `expiration_date` with `Close Value >= 0` if bought; a hedged straddle book has `IRDeltaParallel ≈ 0`, `IRVegaParallel < 0` and `Theta > 0` every day; a long bond book has `IRDeltaParallel < 0`, and `pnl_explain_table()` shows each coupon in `cashflow_pnl`.

## Pitfalls

- **Same-close signal and execution** (gs semantics): a trigger reads the signal at date `d`'s close and the trade is struck at that same close. Run `signal.lag: 1` as a look-ahead check.
- **Mean reversion never closes a trade.** The gs trigger's exit is an *offsetting* trade; both legs stay open. The trade ledger shows every row open, and `Trade PnL` is `None`. Read P&L from `result_summary`.
- **Mean reversion supports notional sizing only**: its ±1 direction travels in `AddTradeActionInfo.scaling`, which `AddScaledTradeAction` never reads. `validate_spec` rejects the combination.
- **`dv01_target` signs**: `AddScaledTradeAction` uses `scale = scaling_level / unit_risk`. A receiver's, a bought receiver swaption's and a long bond's unit dv01 are negative, so their level must be negative too, or the position silently flips (a receiver turns into a payer, a long bond into a short). The recipes sign the level by `direction_sign`, i.e. by the contract's delta sign for the class. This relies on your config honouring that sign (payer swap > 0, bought payer swaption > 0, long bond < 0): check it before sizing by risk.
- **A swaption's `pay_or_receive` is the option type, not the position.** The opposite of a bought payer is a sold payer (`buy_sell` flipped), never a bought receiver. Hand-written strategies must flip `buy_sell` too (or negate `quantity_`).
- **Swaption premium and bond coupons never reach cash** except through Price: the entry cash is −Price (that is the premium: keep kwargs `premium`/`fee` at 0, which the validator enforces), and coupons paid while a bond is held are not booked (gs parity). Report `economic_pnl` from `pnl_explain_table()` or use a total-return `npv`.
- **Straddles have no delta sign**: dv01 sizing and dv01-neutral curve legs are refused for them; size by notional or nav.
- **Mixed instrument types** (swaption + swap hedge, bond + swap): own-rate deltas are per bp of different rates, so hedges, risk triggers and dv01 sizing across types are approximate (DEV-I12).
- **`'next schedule'`** exits at the next date of the trigger that fired: the next roll for Periodic/Aggregate, the next listed date for DateTrigger, never (held to the end) after the last one. MeanReversion supplies `next_schedule=None` (held to the end); a lone MktTrigger or a StrategyRiskTrigger supplies no info at all, and an action with `'next schedule'` under it raises `RuntimeError('Next schedule not supported by action')`.
- **HedgeAction** sizes every hedge placed on a date against the same pre-hedge book risk; two hedge actions on one date would each hedge the full risk.
- **Missing markets are dropped**, not filled: the default session policy `missing_market='drop'` removes grid dates with no market for any traded asset (listed on `backtest.missing_market_dates`); triggers never fire on those dates.
- **NAV sizing** divides the cash budget by the entry PV. Par (ATM) swaps have PV ≈ 0, so use it only for instruments with a material entry PV (swaptions, bonds).
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
- [`pricebt-verify-asset-config`](../pricebt-verify-asset-config/SKILL.md): prove the swaption / bond configs a recipe relies on (signs, units, expiry behaviour) before trusting a run.
- [`pricebt-pnl-attribution`](../pricebt-pnl-attribution/SKILL.md): decompose a swaption or bond book's P&L (`pnl_explain_table`) into delta, gamma, vega, vanna, volga, theta and coupons.
- [`pricebt-risk-measures`](../pricebt-risk-measures/SKILL.md): how your library produces each measure a recipe relies on (own-rate delta, chain-rule gamma, vega, per-day theta, Cashflows) and when to declare one unsupported.
- [`pricebt-port-gs-notebook`](../pricebt-port-gs-notebook/SKILL.md): when you start from an existing gs notebook instead of a spec.
