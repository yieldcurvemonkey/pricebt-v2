---
name: pricebt-scenarios
description: Price under gs scenario contexts in pricebt (RollFwd, CarryScenario, MarketDataShockBasedScenario, CurveScenario, IndexCurveShift, CurveOverlay, MeasureScenario, CompositeScenario, MultiScenario) by wiring your own pricing library's market transforms into an asset config's `scenarios:` section; build terminal-payoff grids of swaption straddles and flies, rate x vol stress grids, carry/roll-forward ladders and delta/vega-by-shock checks; and verify a config's transforms (`scenario_grid.py`). Use for "scenario", "stress", "shock the curve", "roll forward", "terminal payoff", "payoff diagram", "MultiScenario", or a gs notebook under 01_scenarios_and_contexts.
---

# Scenarios: gs scenario contexts priced by your library

gs prices a scenario on its server. pricebt has no market data, so a scenario is plain data that
each asset config hands to **your library** through a market transform in its `scenarios:`
section. This skill wires those transforms, gets the grids people actually want (terminal payoffs,
stress grids, carry ladders), and checks the transforms against known answers. The full contract
is [`docs/v2/SCENARIOS.md`](../../docs/v2/SCENARIOS.md).

## When to use / not use

- **Use** to add `scenarios:` to an asset config, to port a gs notebook from
  `01_scenarios_and_contexts`, to build a payoff or stress grid, or when a price under a scenario
  looks wrong.
- **Do not use** for P&L attribution over a backtest (use
  [`pricebt-pnl-attribution`](../pricebt-pnl-attribution/SKILL.md)), or to stress a backtest:
  `run_backtest` under a scenario raises (DEV-S7). Stress the market in a separate study instead.

## Inputs

- An asset config that already prices: [`pricebt-connect-pricing-library`](../pricebt-connect-pricing-library/SKILL.md).
- Your library's calls that shift a curve's quotes or zero rates, bump a vol surface, rebuild a
  curve from discount factors, and move a market to a later date (forward-realised or spot).

## Outputs

- A `scenarios:` section in the config, one entry per scenario type you support.
- Grids as DataFrames: `scenario_grid.payoff_grid`, `scenario_grid.grid_2d`.
- A check table: `scenario_grid.check_transforms`, or the `check` CLI.

## Procedure

1. **Decide the units and the patterns.** Write in the config's `description:` what a shock value
   means. The gs notebooks pass decimals inside `MarketDataShock` (`50 / 10000` is 50bp) and bp for
   `CurveScenario` shifts. Decide which `MarketDataPattern`s your market holds, typically
   `('IR', '<ccy>')` and `('IR Vol', ...)`. Any other pattern must leave the market unchanged.
2. **Write the transforms in your library** (or a small adapter module the config imports). Each
   returns a *new* market and never mutates the one it gets. `tests/toylib/scenarios.py` has a
   worked example of every type. For a roll, `realise_fwd=True` means the forwards are held:
   `DF'(x) = DF(x) / DF(target)`.
3. **Map them in the config:**

   ```yaml
   imports: |
     import my_lib_adapter as scn
   scenarios:
     MarketDataShockBasedScenario: 'scn.apply_shocks(market, scenario)'
     CurveScenario: 'scn.reshape(market, scenario)'
     RollFwd: 'scn.roll(market, scenario, pricebt_from_date, pricebt_date)'
     CarryScenario: 'scn.roll(market, scenario, pricebt_from_date, pricebt_date)'
     MeasureScenario: {expr: 'scn.set_measure(market, scenario, resolved)', scope: trade}
   ```

   `scenario` is a plain dict: enums as values, nested patterns/shocks as dicts. The injected
   names are listed in [`docs/v2/SCENARIOS.md`](../../docs/v2/SCENARIOS.md) section 1. A type you
   do not map raises `NotSupportedError` when it is active, so an asset it genuinely does not
   affect should map it to `'market'`.
4. **Check the transforms:**

   ```powershell
   $env:PYTHONPATH = "src;tests"
   python skills/pricebt-scenarios/scripts/scenario_grid.py check --asset <your config> --instrument "IRSwaption('Straddle', '10y', 'USD', expiration_date='1y')" --date 2024-03-04
   ```

   Every row must be `True` (or `None` where your config has no such measure). The table covers:
   a zero shock is the base price; a roll to the pricing date changes nothing; another currency's
   shock changes nothing; the shock delta equals your `IRDelta` per bp of the own rate (gs 010204);
   the shock vega equals `IRVegaParallel` (gs 010205); nested `with` blocks equal a
   `CompositeScenario`.
5. **Build the grids** (in-process):

   ```python
   import sys; sys.path.insert(0, "skills/pricebt-scenarios/scripts")
   import scenario_grid as sg
   straddle.resolve()                                     # pin the strike first
   expiry = straddle.resolved_terms["expiration_date"]    # your resolve's own key
   v = sg.payoff_grid(straddle, range(-100, 101, 10), roll_to=expiry)    # a V
   tent = sg.payoff_grid(fly_portfolio, range(-100, 101, 10), roll_to=expiry)  # per leg + total
   stress = sg.grid_2d(book, rates_bp=[-50, -25, 0, 25, 50], vols_bp=[-20, 0, 20])
   carry = sg.roll_ladder(swap, ["0b", "22b", "44b", "66b"], realise_fwd=False)  # value rolled forward
   ```

   Or with the gs API directly: `with RollFwd(date=expiry), MultiScenario([...]): fly.price().aggregate()`.
6. **Port gs notebooks** by keeping their scenario code and adding the transforms. The gs
   `010202_rate_curve_shock` notebook passes `IndexCurveShift` fields to `CurveScenario`, which gs
   itself rejects. Use `IndexCurveShift` there.

## Checks

- `scenario_grid.py check` is all `True`/`None` on one instrument of each class the config prices.
- A straddle's terminal payoff is a V with its point at the strike, payer plus receiver equals the
  straddle under every member, and a fly is a tent that is zero beyond its wings. These are
  `tests/test_scenarios.py`'s known answers on the toy.
- Under a scenario, `result.risk_key.scenario` names it, and adding a base and a scenario result
  raises.

## Pitfalls

- **Unresolved trades re-strike.** Under a scenario an unresolved `'ATM'` resolves on the shocked
  curve, and `'1y'` pins from a rolled date (gs's documented behaviour, DEV-S1). Resolve first.
- **Exercise from history.** If your library decides a physically settled swaption's exercise from
  realised fixings, a roll to expiry under a shock ignores the shock and the payoff grid comes out
  flat. Decide an expiry inside the roll on the scenario curve.
- **Rolling past coupons.** If your `npv` keeps paid coupons in PV (total return) or drops them,
  a roll across a coupon shows it. gs shows the PV drop (gs 03 lifecycling notebook).
- **FX is not shocked** (DEV-S8): a cross-currency book converts at the unshocked FX.
- **Mutating `market`** corrupts every later scenario: the unshocked market is cached and shared.
- **Units:** `MeasureScenario(IRFwdRate, x)` takes `x` in your config's `IRFwdRate` unit (bp for
  the shipped configs; gs decimal, DEV-S4).

## Related skills

- [`pricebt-architecture`](../pricebt-architecture/SKILL.md): the rules (MUST-1: scenarios live in configs).
- [`pricebt-asset-config-cookbook`](../pricebt-asset-config-cookbook/SKILL.md): config patterns and the error catalogue.
- [`pricebt-risk-measures`](../pricebt-risk-measures/SKILL.md): what `IRDelta` and `IRVega` must mean for the shock equivalences to hold.
- [`pricebt-port-gs-notebook`](../pricebt-port-gs-notebook/SKILL.md): porting gs notebooks, these included.
