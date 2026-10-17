# Scenarios: gs scenario contexts, priced by your library

pricebt ports gs_quant's scenario contexts: `RollFwd`, `CarryScenario`, `CurveScenario`,
`IndexCurveShift`, `CurveOverlay`, `LiborFallbackScenario`, `MarketDataShockBasedScenario`
(with `MarketDataPattern` and `MarketDataShock`), `MarketDataVolShockScenario`, `MeasureScenario`,
`CompositeScenario` and `MultiScenario`. They have the gs 1.5.4 constructors and field order, so
gs code runs unchanged:

```python
from pricebt.risk import MarketDataPattern, MarketDataShock, MarketDataShockBasedScenario, MarketDataShockType, RollFwd

ir_up = MarketDataShockBasedScenario({MarketDataPattern("IR", "USD"): MarketDataShock(MarketDataShockType.Absolute, 50 / 10000)})
with ir_up:
    shocked = swaption.price()
with RollFwd(date="22b", holiday_calendar="NYC"), ir_up:   # roll 22 business days, then shock
    rolled_and_shocked = swaption.price()
```

In gs the GS server shocks the market data. **pricebt shocks nothing itself.** As with every other
number, the scenario reaches your library only through the asset config: an optional
`scenarios:` section maps each scenario type to a **market transform**, an expression that takes
the market your config built and returns the scenario's market. Nothing under `src/pricebt`
learns what a curve, a vol or a pattern means (MUST-1, MUST-5). Every behavioural difference
from gs is a `DEV-S*` row in [`DESIGN.md`](DESIGN.md) §11 and [`DEVIATIONS.md`](DEVIATIONS.md).

## 1. The asset-config section

```yaml
imports: |
  import my_lib.scenarios as scn
scenarios:                                   # optional; scenario_type -> market transform
  MarketDataShockBasedScenario: 'scn.apply_shocks(market, scenario)'
  CurveScenario: 'scn.reshape(market, scenario)'
  RollFwd: 'scn.roll(market, scenario, pricebt_from_date, pricebt_date)'
  CarryScenario: 'scn.roll(market, scenario, pricebt_from_date, pricebt_date)'
  MeasureScenario:                           # mapping form: expr + scope
    expr: 'scn.set_measure(market, scenario, resolved)'
    scope: trade                             # market (default) | trade
```

- **Keys** are leaf scenario types: the `scenario_type` of a class in `pricebt.risk.scenarios`, or
  of your own `Scenario` subclass (§6) if it is defined before the config loads. An unknown key is
  a load error with a "did you mean" hint. `CompositeScenario` and `MultiScenario` take no entry:
  pricebt applies their members one by one.
- **Values** are an expression string (scope `market`) or `{expr, scope}`.
- **`scope: market`** (the default): the transform runs once per asset market and date, and every
  trade of the asset shares the result.
- **`scope: trade`**: the transform runs once per trade and also gets `resolved` (the trade's
  resolved terms). Use it when the scenario depends on the trade, as `MeasureScenario` does ("set
  *this* trade's forward to 2.4%"). Each trade then has its own market, so bucketed portfolio
  functions are evaluated one trade per group.

### Injected names (a transform's whole world)

| Name | Value |
|---|---|
| `market` | the market so far: your `market.expr`'s result for the date, or the previous scenario's output |
| `scenario` | the scenario as plain data (below) |
| `pricebt_date` | the valuation date **after** this scenario (a roll's target; otherwise unchanged) |
| `pricebt_from_date` | the valuation date **before** this scenario (the date `market` is valued on) |
| `pricebt_timestamp`, `pricebt_datetime` | `pricebt_date` at the session's end-of-day time |
| `pricebt_csa`, `pricebt_asset`, `pricebt_currency` | as for every other expression ([`ASSET_CONFIG_GUIDE.md`](ASSET_CONFIG_GUIDE.md)) |
| `resolved` | `scope: trade` only: the trade's resolved terms (plain data) |

The transform **must return a new market** and must not mutate `market`: pricebt caches the
unshocked market and reuses it for other scenarios. Returning `None` is a `ConfigError`. A
transform that raises surfaces as an `AssetEvaluationError` naming the asset and the key
`scenarios.<type>`, with your exception as its cause.

### The `scenario` dict

Every field of the scenario, None when unset, with:

- enums given as their `.value` (`"Absolute"`, `"RFR"`);
- nested patterns, shocks and vol slices given as dicts;
- tuples given as lists;
- a risk measure given as its name (`"IRFwdRate"`);
- `scenario_type` included.

For example, `MarketDataShockBasedScenario({MarketDataPattern('IR', 'USD'): MarketDataShock('Absolute', 1e-4)})` arrives as:

```python
{"scenario_type": "MarketDataShockBasedScenario", "name": None,
 "shocks": [{"pattern": {"mkt_type": "IR", "mkt_asset": "USD", "mkt_class": None, "mkt_point": None, ...},
             "shock": {"shock_type": "Absolute", "value": 0.0001, "precision": None, "cap": None, ...},
             "name": None}]}
```

**Units are your transform's contract.** The gs notebooks pass shock values in decimals
(`50 / 10000` is 50bp, `1e-4` is 1bp of normal vol) and curve shifts in bp
(`CurveScenario(parallel_shift=10)` is 10bp). Write the transform for the units your users will
pass, and say which units in your config's `description:`. `MeasureScenario.value` is the
exception: it is in the unit your config's own mapping of that measure reports (DEV-S4), so
`MeasureScenario(IRFwdRate, swaption.calc(IRFwdRate) + 1)` is +1bp for a bp config.

**Pattern matching is the transform's job.** A `MarketDataPattern` that names data your market
does not hold must leave the market unchanged: an `('IR', 'EUR')` shock on a USD asset, an
`'FX'` or `'Eq'` shock on a rates asset, an `'IR Vol'` shock on a swap. That is correct behaviour,
not an error. An **active scenario type** the asset has no entry for is different: it raises
`NotSupportedError` naming the asset and the type (DEV-S1). pricebt never prices a book unshocked
silently, so an asset that is genuinely unaffected by a type should map it to `'market'`.

## 2. Semantics

**Order.** The entered scenarios apply outermost first, each one's market feeding the next, as
gs's `CompositeScenario(reversed(Scenario.path))`. `with a, b:` and `with CompositeScenario([a, b]):`
are the same scenario. Order matters whenever two scenarios do not commute: a proportional shock
then an absolute one is not the absolute then the proportional, and a roll then a reshape is not
a reshape then a roll.

**Valuation date (DEV-S2).** `RollFwd` and `CarryScenario` move the valuation date, and pricebt
computes the target:

- an absolute date or an ISO string is used as given;
- a relative rule (`'1m'`, `'22b'`, `'6m'`) is applied to the current valuation date with
  `pricebt.datetime` rules: weekends only, with a `holiday_calendar` location ignored with a
  warning (DEV-T3) and a list of holiday dates honoured;
- rolls compose: `with RollFwd(date='6m'), RollFwd(date='6m'):` rolls one year;
- a roll that moves back raises `ValueError`.

Every later transform and every function then sees `pricebt_date` = the rolled date, so trades
lifecycle: a swaption past its expiry is exercised or worthless, and `ExpiryInYears` counts from
the rolled date. Your roll transform decides what "roll" means for the market. `realise_fwd=True`
(`roll_to_fwds=True`) keeps the forwards, so the curve seen from the target date is the old curve
translated. `False` keeps the spot zero curve, re-anchored on the target date. Vols, fixings and
the decision of an expiry inside the roll are the transform's and your library's to define.

**Resolution (DEV-S1).** An *unresolved* instrument priced under a scenario resolves under it, on
the scenario market and valuation date. This is gs's documented behaviour: "if you don't resolve
the trade ... under the RollFwd scenario ... the trade will be a different trade". An `'ATM'`
strike re-strikes on the shocked curve, and a `'1y'` expiry pins from the rolled date. **Resolve
first** (`inst.resolve()` outside the scenario) when you want the same trade under every scenario,
which is almost always. Resolution applies only `scope: market` transforms, because a
`scope: trade` transform needs terms that do not exist yet. A `build_on: resolve_date` trade is
built on its resolution date's own market.

**Caching and keys.** The scenarios are in every market, trade, unit-value, portfolio-value and
group key, so a scenario value never shares a cache entry with the base or with another scenario.
A result's `risk_key.scenario` is gs's `MarketDataScenario`: one scenario, or a
`CompositeScenario` of the active ones. `aggregate_results` therefore refuses to add results
priced under different scenarios (`ValueError: Cannot aggregate results with different pricing keys`).

**Contexts.**

- Under a `HistoricalPricingContext` the scenarios apply on every date, as gs's.
- Under `PricingContext(market=CloseMarket(date=t))` the first transform gets `t`'s market, with
  `pricebt_from_date` = the pricing date. Re-anchor it there if your library values another
  date's market on the pricing date, as the toy configs' `npv` does.
- Futures taken inside the context keep their scenario: a lazy bucketed result evaluated after the
  `with` block has exited is still the scenario's value.
- The scenario stack is per process, as `PricingContext`'s is: do not enter scenarios from several
  threads at once.

**`subtract_base` (DEV-S5).** Under a scenario with `subtract_base=True` (gs `CurveOverlay`), a
scalar or bucketed result is the scenario value minus the value with no scenario. A table measure
raises.

**Not supported (each raises, never a silent base number).**

- A relative measure (`PnlExplain`) under a scenario (DEV-S1).
- `run_backtest` and `measure_series` under an active scenario (DEV-S7): the engine also reads
  markets directly, which a scenario would not reach.
- `CarryScenario(time_shift=...)`, which is GS server-defined (DEV-S2).

**FX (DEV-S8).** The FX config has no `scenarios:` section. Currency conversion under a scenario
uses the unshocked FX of the pricing date, or of the `CloseMarket` date, even under a roll. To
stress FX, shock it inside the asset's own market.

## 3. MultiScenario and its results

`MultiScenario(scenarios=[...])` prices once per member, in the MultiScenario's place in the
stack, and keys the results by member as gs's `MultipleScenarioResult`. The nesting is measure,
then scenario:

| You call | You get |
|---|---|
| `inst.price()` | `MultipleScenarioResult {member: FloatWithInfo}` |
| `inst.calc((Price, IRFwdRate))` | `MultipleRiskMeasureResult {measure: MultipleScenarioResult}` |
| `portfolio.calc(...)` | `PortfolioRiskResult` whose leaves are the above |
| `prr[member]` / `prr[[m1, m2]]` | the same tree with each leaf narrowed to those members (gs); an unknown member raises `ValueError('... not computed')` |
| `prr.aggregate()` | `MultipleScenarioResult {member: total}` (multi-measure: `{measure: {member: total}}`), each member's bucketed leaves grouped as usual (DEV-S3) |
| `result.to_frame()` | one column per member (gs's multi-scenario pivots), `scenario` in the raw records |
| `inst.resolve(in_place=False)` | `MultipleScenarioResult {member: resolved instrument}`; `in_place=True` raises gs's `RuntimeError` |

Members may be any scenario, including composites and a mix of types (DEV-S3; gs takes one type
per MultiScenario). One MultiScenario may be active at a time, and its members must differ: a
repeated member raises `ValueError` (gs prices it twice and keeps one result). For a two-dimensional grid, build
one MultiScenario of composites:

```python
grid = MultiScenario([CompositeScenario([rate(r), vol(v)], name=f"{r:+}bp/{v:+}v") for r in rates for v in vols])
```

Name the members (`name=`): the name is the scenario's repr, so it becomes the `to_frame` column
label. Unnamed scenarios sort and pivot too (DEV-S6), but their labels are long.

## 4. Recipe: terminal payoffs of a swaption straddle or fly

Roll to expiry, then shock the forward across a grid. The value of each member is the payoff at
expiry on that shocked curve:

```python
from pricebt.risk import MarketDataPattern, MarketDataShock, MarketDataShockBasedScenario, MultiScenario, RollFwd

def rate(bp):
    return MarketDataShockBasedScenario({MarketDataPattern("IR", "USD"): MarketDataShock("Absolute", bp / 1e4)}, name=f"{bp:+d}bp")

straddle = IRSwaption("Straddle", "10y", "USD", expiration_date="1y", strike="ATM", notional_amount=10e6)
straddle.resolve()                                   # pin strike and dates first: §2 Resolution
expiry = straddle.resolved_terms["expiration_date"]  # whatever your config's resolve names it

with RollFwd(date=expiry, realise_fwd=True):
    with MultiScenario([rate(bp) for bp in range(-100, 101, 10)]):
        payoff = straddle.price()                    # MultipleScenarioResult: a V with its point at the strike
payoff.to_frame()

fly = Portfolio([pay_k_minus_50, pay_k.clone(quantity_=-2), pay_k_plus_50])
fly.resolve()
with RollFwd(date=expiry), MultiScenario([rate(bp) for bp in range(-100, 101, 10)]):
    tent = fly.price().aggregate()                   # {member: book payoff}: a tent, zero beyond the wings
```

What makes the numbers right is your library, and the toy library shows the three things to
check:

1. The roll lands **on** the expiry date, and the swaption is valued at expiry (time value zero).
2. **The exercise decision is made on the scenario's curve, not on history.** A physically
   settled swaption that decides exercise from the realised world's fixings ignores the shock and
   the grid comes out flat. The toy marks a rolled curve with the date its scenario path starts,
   and decides any expiry inside the path on the scenario curve
   (`tests/toylib/swaption.py`, `_expiry_fwd`).
3. Payer plus receiver equals the straddle under every member, and the payoff at the strike is
   zero for an ATM straddle on a forward-realising roll.

`tests/test_scenarios.py` checks all three against payoffs built by hand, and
[`notebooks/scenarios_toy.ipynb`](../../notebooks/scenarios_toy.ipynb) plots them. The
[`pricebt-scenarios`](../../skills/pricebt-scenarios/SKILL.md) skill's `scenario_grid.py` builds
these grids, and checks a config's transforms, for any asset.

## 5. The classes

| Class | Fields (gs order) | What a transform usually does |
|---|---|---|
| `MarketDataShockBasedScenario(shocks, name)` | `shocks`: `{MarketDataPattern: MarketDataShock}` (or `MarketDataPatternAndShock` items) | for each shock whose pattern names your data: Absolute adds, Proportional multiplies by 1 + value, Override sets |
| `MarketDataPattern` | `mkt_type, mkt_asset, mkt_class, mkt_point, mkt_quoting_style, is_active, is_investment_grade, currency, country_code, gics_*` | a filter; `'IR'`/`'IR Vol'`/`'IR Basis'`/`'FX'`/`'Eq'`... per the gs 010003-010010 notebooks |
| `MarketDataShock` | `shock_type, value, precision, cap, floor, coordinate_cap, coordinate_floor` | |
| `CurveScenario` | `market_data_pattern, parallel_shift, curve_shift, pivot_point, tenor_start, tenor_end, shock_type` | parallel bp plus a linear curve shift between two tenors (years), zero at the pivot (default the midpoint): steepeners and flatteners (gs 0104) |
| `IndexCurveShift` | `market_data_pattern, freeze_pattern, annualised_parallel_shift, annualised_slope_shift, cutoff, floor, tenor, rate_option, bucket_shift, bucket_start, bucket_end` | shift a projection curve |
| `CurveOverlay` | `dates, discount_factors, denominated, csa_term, tenor, rate_option, curve_type, subtract_base` | replace a curve by user discount factors (dates accept ISO strings) |
| `RollFwd` | `date, realise_fwd=True, holiday_calendar` | move the market to `pricebt_date` |
| `CarryScenario` | `date, time_shift, roll_to_fwds=True, holiday_calendar` | as RollFwd (`time_shift` raises) |
| `LiborFallbackScenario` | `date, fallback_type=RFR, discounting=False, cash_flows=True` | switch IBOR projections to their fallback; identity for an RFR-only market |
| `MarketDataVolShockScenario` | `pattern, shock_type, vol_levels, ref_spot`; `from_dataframe(asset_ric, df, ref_spot, name)` | override vol slices (`MarketDataVolSlice(date, strikes, levels)`) |
| `MeasureScenario` | `risk_measure, value` | `scope: trade`: move the market until this trade's measure reads `value` (a root find on a parallel shift; set the vol directly) |
| `CompositeScenario` | `scenarios` | none: members apply in order |
| `MultiScenario` | `scenarios` | none: §3 |

Import paths follow gs:

- `pricebt.risk` exports every scenario class (gs `gs_quant.risk`);
- `pricebt.risk.scenarios` holds the classes;
- `pricebt.common` and `pricebt.target.common` also export them, as `gs_quant.common` exports
  `MeasureScenario`;
- `pricebt.base.Scenario` is the base class.

`tests/test_gs_api_parity.py` checks every field, default and constructor against the gs 1.5.4
snapshot. gs's JSON/schema helpers are not ported. `as_dict`, `to_dict`, `clone` and
`is_entered` are.

## 6. Your own scenario types

Subclass `Scenario` as a dataclass with a `scenario_type` default, and map that type in the
configs that support it:

```python
@dataclass(unsafe_hash=True, repr=False)
class Steepener(Scenario):
    bp_per_year: float = 0.0
    scenario_type: str = field(init=False, default="Steepener")
    name: str = None
```

It nests, composes and goes into a MultiScenario like a gs class. To move the valuation date,
override `_roll_target(self, current) -> date`.

## 7. Writing and checking transforms for your library

- **Return a new market object**, built by your library's own bump or rebuild call. Shock the
  instrument quotes the curve is built from (gs shocks quotes) and rebuild, or shift the zero
  curve: either is fine if your description says which.
- **Shared markets:** the scenario market is cached per asset (`market.key` sharing stops at the
  scenario layer), so two assets on one curve each run their own transform.
- **Rolls:** for `realise_fwd=True`, the curve seen from the target date is
  `DF'(x) = DF(x) / DF(target)` (forwards held). For `False`, keep the zero rates in tenor space.
  Move the vols and fixings as your desk defines carry.
- **Exercise inside a roll:** decide it on the scenario curve (§4, point 2).
- **Check with known answers** (the toy's tests do each of these; `scenario_grid.py check` runs
  them on your config):
  - a zero shock equals the base price;
  - (P(+1bp) - P(-1bp)) / (F(+1bp) - F(-1bp)) equals your own-rate `IRDelta` (gs 010204);
  - (P(+1bp vol) - P(-1bp vol)) / 2 equals `IRVegaParallel` (gs 010205);
  - a roll to the pricing date itself changes nothing;
  - a straddle's terminal payoff is a V and payer plus receiver equals it.

## 8. Where it lives

| Path | What |
|---|---|
| `src/pricebt/base.py` | `Scenario`: the context stack (`Scenario.path`, innermost first) |
| `src/pricebt/risk/scenarios.py` | the classes, `scenario_dict`, `scenario_specs` (the stack as leaf tuples, one per MultiScenario member) |
| `src/pricebt/assets/config.py` | the `scenarios:` section (`ScenarioSpec`) |
| `src/pricebt/assets/pricing.py` | `_scenario_state` (the transforms, the valuation date, the cache), `engine_calc`/`engine_resolve` (MultiScenario) |
| `src/pricebt/risk/results.py` | `MultipleScenarioResult`, scenario indexing, `aggregate`, `to_frame` |
| `tests/toylib/scenarios.py` | the toy library's transforms (a worked example of every type) |
| `tests/assets/toy_usd_swaption.yaml`, `tests/assets/toy_usd_irs_full.yaml` | toy configs with a `scenarios:` section |
| `tests/test_scenarios.py` | known-answer tests, gs notebook parity, refusals |
