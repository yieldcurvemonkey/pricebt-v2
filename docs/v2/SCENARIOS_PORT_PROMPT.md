# Prompt: port gs scenario contexts into your internal pricebt branch

A hand-off prompt for an AI coding agent working in your internal branch, where pricebt prices on
your desk's pricing library and market-data infrastructure. That branch has drifted too far from
the public repository to merge or cherry-pick the public scenario work. So the agent implements the
feature directly in the internal branch. It uses the public implementation as a read-only
reference: it reads it, runs its tests, and ports its behaviour.

How this prompt differs from [`SCENARIOS_LIBRARY_PROMPT.md`](SCENARIOS_LIBRARY_PROMPT.md), which
only added `scenarios:` sections to configs on top of the public `main`:

- **The agent changes pricebt's own code** (the scenario classes, the pricing layer, the results
  layer) as well as your configs.
- **Behaviour is fixed; mechanism is the agent's call.** Section 4 lists what must behave as in
  the reference, each item with the reference test that proves it. Section 5 lists what the agent
  decides, because it can see your library and the drift of your branch, which the reference
  author could not. Examples: where the transforms live, how a curve is shocked, how a roll
  is done, whether a `MultiScenario` is priced in one batch.
- **The order of work is the order the reference was built in**, risk first. A map of your
  branch against the reference comes first. Your library's calls come only after the engine
  passes known-answer tests.
- **Checked here:** the reference's 62 scenario tests pass on public `main` (`fbdd9f6`). Three bugs
  planted in the reference engine are each caught by named tests (section 6.1): a cache key without
  the scenarios, a roll that ignores its date, and scenarios applied innermost first. The fetch
  commands in section 1 were run as written.

Fill in section 0, then give the agent everything below the line.

---

# Task: implement gs scenario contexts in this internal pricebt branch

## 0. Filled in by the user (ask once if any line is blank)

- Internal repository path: `<path>`. Branch to start from: `<branch>`
- How to run Python and the tests here (interpreter, `PYTHONPATH`, anything the library needs):
  `<command>`
- Reference access, one of:
  - (a) this machine can reach `https://github.com/yieldcurvemonkey/pricebt-v2`; or
  - (b) no GitHub access: a copy of the public repository at commit `fbdd9f6` or later is at
    `<directory>`.
- MUST-1 in this branch ("nothing under `src/pricebt` imports the pricing library; the library is
  reached only through config expressions"): `kept` / `dropped` / `don't know`
- Asset configs in scope (default: every IRSwap and IRSwaption config): `<files>`
- Market provider configs, if any: `<directory or none>`
- Market data location the configs answer to (`LDN` if never set): `<location>`
- Currency of each config: `<CCY>`
- `D1`: a Monday to Thursday with market data, on which the test trades pay and fix nothing the
  next day: `<date>`
- `D3`: a recent business date with market data (D3 + 1 year has no market data yet): `<date>`
- Where the library's source or documentation can be read (a path, a package name, or `none`):
  `<where>`
- Swaption settlement (physical, cash, or "don't know"): `<settlement>`

`<CCY>`, `<location>`, `<path>` and the rest below stand for these values.

## 1. Set up (do this first)

Every git command names its tree: `git -C <path> ...`. Never rely on `cd`.

1. **State of the checkout.** Record `git -C <path> branch --show-current`,
   `git -C <path> log --oneline -1` and `git -C <path> status --porcelain`. The first two are the
   user's rollback point. If `status` shows modified tracked files (any line that does not start
   with `??`), stop and ask: that is the user's work in progress.
2. **Your branch.** Decide where to work:
   - If every in-scope config is tracked (`git -C <path> ls-files --error-unmatch <config>`
     succeeds for each), make a worktree:
     `git -C <path> worktree add ../<short-name> -b scenarios-port <branch>`. Use a short sibling
     path, because Windows path limits break long ones. From then on `<path>` means the worktree.
   - If any in-scope config is untracked, a worktree would not contain it. Then the only option
     is `git -C <path> switch -c scenarios-port <branch>` in the user's own checkout; the
     untracked files stay where they are. This changes the branch the user's checkout is on, so
     ask the user once before you run it.
   - Write down which one you chose and why.
3. **Fetch the reference, read-only.** Never add the public repository as a remote, and never
   merge, rebase or cherry-pick from it.
   - Option (a): fetch by URL. This creates two read-only refs and no remote, so nothing can be
     pushed there by accident:

     ```
     git -C <path> fetch --no-tags https://github.com/yieldcurvemonkey/pricebt-v2.git main:refs/remotes/pricebt-public/main v2-scenarios:refs/remotes/pricebt-public/v2-scenarios
     git -C <path> merge-base --is-ancestor fbdd9f6 pricebt-public/main && echo ok
     git -C <path> worktree add --detach ../<short-name>-ref pricebt-public/main
     ```

     The second command must print `ok`.
   - Option (b): use the directory from section 0 as the reference tree.

   Call the reference tree `<ref>`. You read it, grep it and run its tests. You never edit it.
4. **Prove the reference runs here.** In `<ref>`, with `PYTHONPATH=src;tests` (POSIX
   `src:tests`), run:
   `python -m pytest tests/test_scenarios.py -o addopts= -p no:cacheprovider -q`
   It must report `62 passed`. This is your known-good engine. Whenever you are unsure what the
   reference does, run it here and look; do not guess. This command is for `<ref>` only. In
   `<path>`, always use the test command from section 0.
5. **The reference change set.** The scenario work is the diff `0ca3110..fbdd9f6` (43 files). In
   option (a) list it with `git -C <path> diff --stat 0ca3110 fbdd9f6`, and read each file with
   `git -C <path> diff 0ca3110 fbdd9f6 -- <file>`. Diffing the merge commit `fbdd9f6` alone shows
   nothing useful. In option (b) read the files in `<ref>`.
6. **How far this branch has drifted.** `git -C <path> merge-base HEAD pricebt-public/main`. If
   it prints a commit, the branch shares history with the public repository, and
   `git -C <path> diff --stat <that commit> HEAD -- src/pricebt` shows what your branch changed in
   pricebt. If it prints nothing, the histories are unrelated: compare the files by hand.
7. **Baseline.** Create `reports/scenarios/` (check that `reports/` is git-ignored here; if it is
   not, ask where reports go). Save:
   - the full test suite result of the starting branch, as `baseline_tests.txt`;
   - base prices of a fixed case list, as `baseline_prices.json`. The cases: a payer and a
     receiver swap (at the money and 50bp off), and a payer, a receiver and a straddle swaption
     (`'ATM'` and K+50bp, in your config's strike spelling), each at D1 and D3, for `Price` and
     every measure the configs map. Write the script that produces it as
     `reports/scenarios/base_prices.py`. It reruns after every slice, and the numbers must stay
     **bit-identical**. Prove the script can fail: rerun it with one config function multiplied
     by `1.0001` in a temporary copy, and confirm it reports a difference.

**Git rules for the whole task:**

- Commit on `scenarios-port` after each slice whose gate passes, as a checkpoint.
- Stage files by explicit path only, never `git add -A` or `git add .`: credentials, recordings
  and reports may be untracked here.
- Never push anywhere, and never touch another branch.
- Rollback, for the user: `git -C <path> switch <the branch from step 1>` (or remove the worktree).

## 2. Goal and definition of done

gs scenario code runs unchanged in this branch and prices through your library:

```python
from pricebt.risk import (CompositeScenario, MarketDataPattern, MarketDataShock, MarketDataShockBasedScenario,
                          MultiScenario, RollFwd)

def rate(bp):
    return MarketDataShockBasedScenario({MarketDataPattern("IR", "<CCY>"): MarketDataShock("Absolute", bp / 1e4)}, name=f"{bp:+d}bp")

straddle = IRSwaption("Straddle", "10y", "<CCY>", expiration_date="1y", strike="ATM", notional_amount=10e6)
straddle.resolve()                                    # pin the strike and dates first
expiry = straddle.resolved_terms["<expiry key>"]
with RollFwd(date=expiry), MultiScenario([rate(bp) for bp in range(-100, 101, 10)]):
    payoff = straddle.price()                         # MultipleScenarioResult: a V with its point at the strike
fly = Portfolio([pay_k_minus_50, pay_k.clone(quantity_=-2), pay_k_plus_50]); fly.resolve()
with RollFwd(date=expiry), MultiScenario([rate(bp) for bp in range(-100, 101, 10)]):
    tent = fly.price().aggregate()                    # {member: book payoff}: zero beyond the wings
```

Done means all of the following, each shown with command output in the final report:

1. **P0 (the use case):** the classes and the stack, `MarketDataShockBasedScenario` (absolute
   `'IR'` and `'IR Vol'`), `RollFwd`, `CarryScenario` (with `date=`), `CompositeScenario`,
   `MultiScenario` and its results, on every in-scope config. Every section-4 rule marked P0
   holds.
2. **Engine tests:** your port of the reference's scenario tests passes. It runs on the toy library
   if this branch has one, else on stub transforms (step 5). It fails for each engine mutation of
   step 10.
3. **Library gates:** the gate script (section 6.2) exits 0 for every currency, and exits 1 for
   each library mutation of step 10.
4. **Nothing else moved:** `base_prices.py` is bit-identical to the baseline, and the full suite
   has no new failures against `baseline_tests.txt`.
5. **Payoff tables:** `reports/scenarios/straddle_payoff_<CCY>.csv` and `fly_payoff_<CCY>.csv`.
6. **Records:** `reports/scenarios/decisions.md` (section 5) and the docs of step 11.

P1, when the library supports it: `CurveScenario`, `CurveOverlay` with `subtract_base`, and
`RollFwd(realise_fwd=False)`.
P2: `IndexCurveShift`, `MeasureScenario`, `MarketDataVolShockScenario`, `LiborFallbackScenario`.
A P1 or P2 type you do not support stays unmapped and raises `NotSupportedError` when used; list
it in the report with the reason.

## 3. Read first, in this order (all in `<ref>`)

1. `docs/v2/SCENARIOS.md`, all of it. It is the contract, and section 4 below condenses it.
2. `docs/v2/DESIGN.md` §11, rows DEV-S1 to DEV-S8, and `docs/v2/DECISIONS_LOG.md`, the entry
   "2026-10-17 — v2-scenarios". That entry gives the reasons behind the design; read the reasons,
   not only the rules.
3. `src/pricebt/risk/scenarios.py` and the `Scenario` class in `src/pricebt/base.py`: the
   classes, the stack, `scenario_specs`, `scenario_dict`, `market_data_scenario`,
   `require_no_scenario`.
4. `git diff 0ca3110 fbdd9f6 -- src/pricebt/assets/pricing.py`: how the active scenarios are
   threaded through pricing. Focus on `_scenario_state`, `resolve`, `_trade_for`,
   `_function_env`, `_eval_unit_cached`, `_portfolio_value_from_entries`, `_minus_base`, `value`,
   `_lazy_value`, `group_aggregate`, `_calc_under`, `engine_calc` and `engine_resolve`.
5. `git diff 0ca3110 fbdd9f6 -- src/pricebt/risk/results.py`: `MultipleScenarioResult`,
   `_MultiScenarioFuture`, and scenario indexing, `aggregate` and `to_frame` on
   `PortfolioRiskResult`.
6. `src/pricebt/assets/config.py`, `_parse_scenarios`, and the `scenarios:` blocks of
   `tests/assets/toy_usd_swaption.yaml` and `tests/assets/toy_usd_irs_full.yaml`.
7. `tests/toylib/scenarios.py`: one transform per scenario type, on the toy library. Then
   `tests/toylib/swaption.py`, `_expiry_fwd`, and the `shape` and `path_start` fields of
   `ToyCurve` in `tests/toylib/rates.py`.
8. `tests/test_scenarios.py`, all of it: the known answers, and the helpers `intrinsic`,
   `toy_npv` and `curve_at`.
9. `skills/pricebt-scenarios/SKILL.md` and `skills/pricebt-scenarios/scripts/scenario_grid.py`.
10. From `pricebt-public/v2-scenarios`: `docs/v2/SCENARIOS_LIBRARY_PROMPT.md`, sections 5 to 8:
    the config-level contract, the gate script `verify_scenarios.py`, the library inventory and
    the pitfalls. Read it with
    `git -C <path> show pricebt-public/v2-scenarios:docs/v2/SCENARIOS_LIBRARY_PROMPT.md`.
11. In **this** branch: `AGENTS.md`/`CLAUDE.md`, the pricing layer, the results layer, the config
    loader, `tests/guards/` if present, and the in-scope configs top to bottom.

## 4. Behaviour that must match the reference

These are not design choices. If this branch cannot meet one, stop and report why with evidence;
do not ship a variant silently. "Test" names the reference test in `tests/test_scenarios.py` that
proves the rule. Port that test, adapted to this branch, as the rule's check.

### 4.1 Classes and the stack (P0)

1. The classes, with gs 1.5.4 constructors, field order and defaults: `RollFwd`,
   `CarryScenario`, `CurveScenario`, `IndexCurveShift`, `CurveOverlay`, `LiborFallbackScenario`,
   `MarketDataShockBasedScenario` (taking a `{MarketDataPattern: MarketDataShock}` mapping or
   `MarketDataPatternAndShock` items), `MarketDataVolShockScenario` (with `from_dataframe`),
   `MeasureScenario`, `CompositeScenario`, `MultiScenario`, `MarketDataPattern`,
   `MarketDataShock`, `MarketDataPatternAndShock`, `MarketDataVolSlice`, `MarketDataScenario`;
   the enums `MarketDataShockType`, `FallbackType` and `ScenarioType`. Tests:
   `test_gs_api_parity.py` (copy the 17 scenario entries from the reference
   `tests/data/gs_api_1_5_4.json`; do not regenerate the file, because that needs gs_quant and
   re-dates unrelated entries), `test_shock_based_scenario_takes_a_mapping_or_items`,
   `test_vol_shock_scenario_from_dataframe`.
2. Hashable, because scenarios are cache keys: lists become tuples, enum strings become enums
   (leniently), ISO strings become dates in date fields. Test:
   `test_lists_become_tuples_strings_become_enums_and_scenarios_hash`.
3. Import paths as gs: `pricebt.risk` exports every class; `pricebt.risk.scenarios` holds them;
   `pricebt.common` (lazily, to keep the import order acyclic) and `pricebt.target.common` export
   them too; `pricebt.base.Scenario` is the base. Test: `test_gs_import_paths`.
4. A scenario is a context manager:
   - `Scenario.path` lists the entered scenarios, innermost first;
   - `Scenario.current` is the innermost, or None when nothing is entered (DEV-S6);
   - `is_entered` is per object;
   - the exit is exception-safe;
   - the stack is per process.

   The test fixture must restore the stack after every test (the reference `tests/conftest.py`
   does), or one failing test leaks its scenario into the next. Test:
   `test_scenarios_are_gs_context_managers_innermost_first`.
5. `repr` is the `name` when set, else the type and the fields set. `__lt__` compares reprs, so
   unnamed scenarios still sort (DEV-S6). Test:
   `test_repr_is_the_name_or_the_fields_and_the_order_is_total`.
6. A transform receives the scenario as **plain data**: every field, None when unset, with enums
   as `.value`, nested objects as dicts, tuples as lists, a risk measure as its name, and
   `scenario_type` included. Test: `test_the_transform_sees_plain_data`.

### 4.2 Applying scenarios (P0)

7. **Order.** The active scenarios, composites flattened, apply **outermost first**; each
   transform's market feeds the next. `with a, b:` equals `with CompositeScenario([a, b]):`.
   Tests: `test_composites_apply_outermost_first`,
   `test_scenario_specs_flatten_outermost_first_and_expand_the_multiscenario_in_place`.
8. **Never priced unshocked silently.** An active scenario type that an asset cannot apply raises
   `NotSupportedError` naming the asset and the type, also when it is one asset of a book. A
   pattern naming data the market does not hold is different: it leaves the market unchanged (an
   EUR shock on a USD asset, `'IR Vol'` on a swap, `'FX'`). Tests:
   `test_an_asset_without_the_scenario_type_raises_naming_it`,
   `test_a_book_with_an_unmapped_asset_raises`, `test_data_the_market_does_not_hold_is_left_alone`.
9. **The transform contract.** A transform returns a **new** market and never mutates its input,
   because the base market is cached and shared. Returning None is a `ConfigError`. An exception in
   a transform surfaces naming the asset and the scenario type, with the original exception as its
   cause. Tests: `test_a_transform_must_return_a_market`,
   `test_index_curve_shift_parallel_plus_slope_to_a_cutoff` (the cause is surfaced).
10. **The scenarios are part of every key.** The tuple of leaf scenarios is in the key of every
    cache that can hold a value computed under a scenario: market or scenario state, resolve,
    trade build, unit value, portfolio value, and lazy group. A scenario value never shares an
    entry with the base or with another scenario. Tests:
    `test_values_never_share_a_cache_with_the_base_or_each_other` (it also checks that the group
    key keeps the date and the params in the positions other code reads; find the readers in
    this branch).
11. **Results carry their scenario.** `risk_key.scenario` is gs's `MarketDataScenario`: None
    without a scenario, the one scenario, or a `CompositeScenario` of the active ones.
    `aggregate_results` therefore refuses to mix them. Test:
    `test_results_under_different_scenarios_never_aggregate`.
12. **Captured at calc time.** A future (async, or a lazy bucketed result) taken inside the `with`
    block keeps its scenario when it is evaluated after the block has exited. Read the scenarios
    when `calc` is called, never when a future resolves. Test:
    `test_futures_taken_inside_the_context_keep_their_scenario`.
13. **Contexts.** Under a `HistoricalPricingContext` the scenarios apply on every date. Under
    `PricingContext(market=CloseMarket(date=t))` the first transform gets `t`'s market, with
    `pricebt_from_date` = the pricing date. A portfolio equals the sum of its instruments under the
    same scenario. Tests: `test_a_historical_context_applies_the_scenario_per_date`,
    `test_a_close_market_override_composes_with_a_scenario`,
    `test_a_portfolio_under_one_scenario_matches_its_instruments`.

### 4.3 Rolls and resolution (P0)

14. **pricebt computes the roll target, once, for the whole book**, never inside a config: it is
    the scenario's `_roll_target(current)`.
    - An absolute date or ISO string is used as given.
    - A relative rule (`'1m'`, `'22b'`) is applied to the current valuation date with this
      branch's relative-date rules (in the reference: weekends only, and a location
      `holiday_calendar` ignored with a one-time warning).
    - Rolls compose.
    - A roll back raises `ValueError`.
    - `CarryScenario(time_shift=...)` raises `NotSupportedError`.

    Tests: `test_roll_targets`, `test_rollfwd_moves_the_valuation_date`.
15. **After a roll, everything sees the new date.** Every later transform, and every config
    function, sees `pricebt_date` = the rolled date. The roll transform also gets
    `pricebt_from_date`. So trades lifecycle: past expiry a swaption is exercised or worthless, and
    `ExpiryInYears` counts from the rolled date. Tests: `test_rollfwd_lifecycles_a_swaption_03`,
    `test_the_transform_receives_the_dates_and_the_plain_scenario`.
16. **An unresolved instrument resolves under the scenario** (gs's documented behaviour). An
    `'ATM'` strike re-strikes on the shocked curve, and `'1y'` pins from the rolled date.
    Resolution applies only the transforms that do not need the trade's terms (`scope: market`).
    A trade built on its resolution date (`build_on: resolve_date`) is built on that date's own
    market. `resolve(in_place=True)` under a `MultiScenario` raises `RuntimeError`; with
    `in_place=False` it returns one resolved instrument per member. Tests:
    `test_an_unresolved_instrument_resolves_under_the_scenario`,
    `test_resolution_skips_a_per_trade_transform`, `test_resolve_under_a_multiscenario`.

### 4.4 MultiScenario and its results (P0)

17. **Pricing.**
    - One pricing per member, in the MultiScenario's place in the stack.
    - Members may be any scenario, composites and mixed types included (DEV-S3).
    - One MultiScenario at a time: a second one, or one nested in another, raises.
    - An empty member list raises, and so does a repeated member (`ValueError`). gs prices a
      repeat twice and keeps one result, so a grid built with a bug would come out silently short.

    Tests: `test_mixed_scenario_types_in_one_multiscenario`, the specs test of rule 7.
18. **Result shapes**, nesting measure then scenario:

    | You call | You get |
    |---|---|
    | `inst.price()` | `MultipleScenarioResult {member: value}` |
    | `inst.calc((m1, m2))` | `MultipleRiskMeasureResult {measure: MultipleScenarioResult}` |
    | `portfolio.calc(...)` | a `PortfolioRiskResult` with those leaves |
    | `prr[member]`, `prr[[m1, m2]]` | the same tree narrowed to those members; an unknown member raises `ValueError('... not computed')` |
    | `prr.aggregate()` | `{member: total}`, or for several measures `{measure: {member: total}}`; each member's bucketed leaves grouped as usual |
    | `to_frame()` | one column per member, and `scenario` in the raw records |

    `fn=` gets the scenario result. Under a `HistoricalPricingContext`, `res[member]` is a series
    and `res[date]` is a `MultipleScenarioResult`. Tests: `test_multiscenario_result_shapes`,
    `test_multiscenario_ladders_aggregate_per_member`,
    `test_multiscenario_over_a_historical_context`,
    `test_fn_under_a_multiscenario_gets_the_scenario_result`.
19. **The terminal payoffs** (the use case), on the toy library or your own:
    - Roll to expiry, then shock. The straddle equals `N x A x |F - K|` on each shocked curve:
      a V, zero at the strike for ATM.
    - Payer plus receiver equals the straddle under every member.
    - The fly is a tent: peak `N x A x 50bp` at K, zero beyond the wings.

    Tests: `test_terminal_payoff_of_a_straddle`,
    `test_straddle_is_payer_plus_receiver_under_every_member`,
    `test_terminal_payoff_of_a_fly_is_a_tent`.

### 4.5 Refusals and the rest

20. **Refusals (P0).** `run_backtest` and `measure_series` under an active scenario raise
    `NotSupportedError`, because the engine reads markets directly and a scenario would not reach
    those reads (DEV-S7). A relative measure (`PnlExplain`) under a scenario raises. Tests:
    `test_backtests_and_measure_series_refuse_an_active_scenario`,
    `test_pnl_explain_under_a_scenario_raises`.
21. **`subtract_base` (P1).** Under a scenario with `subtract_base=True` (gs `CurveOverlay`), a
    scalar or bucketed result is the scenario value minus the no-scenario value, and a table
    measure raises. Test: `test_subtract_base_gives_the_change`.
22. **Per-trade scenarios (P2).** A scenario that depends on the trade (`MeasureScenario`: "set
    *this* trade's forward to 2.4%") gives each trade its own market, so a bucketed portfolio
    function is evaluated one trade per group. `MeasureScenario.value` is in the unit the config's
    own mapping of that measure reports (DEV-S4). Tests:
    `test_measure_scenario_sets_the_trades_own_rate_010902`,
    `test_measure_scenario_groups_each_trade_on_its_own_market`.

## 5. What you decide (and what decides it)

You can see this branch and the library; the reference author could not. For each item below,
choose, then write in `reports/scenarios/decisions.md` what you chose, the alternatives, the
evidence (file:line), and which section-4 rules it touches. The reference's choice is the default
whenever you have no evidence against it.

1. **Where the transforms live.**
   - Reference: an optional `scenarios:` section in each asset config, mapping a `scenario_type`
     to an expression with injected names (`market`, `scenario`, `pricebt_from_date`,
     `pricebt_date`, `pricebt_currency`, `resolved` for `scope: trade`).
   - If MUST-1 is `kept` here, or `tests/guards/` enforces it, keep that design: pricebt code
     names no library.
   - If MUST-1 is `dropped` and the branch already calls the library from `src/`, you may put the
     transforms in the branch's existing adapter layer instead. Still keep one lookup by
     `scenario_type` per asset, so that rule 8 (unmapped raises) holds.
2. **How a market is shocked.** Shock the instrument quotes and rebuild, or shift the zero curve,
   or use the library's own scenario API. The test is rule 9 and the delta identity (section 6.2):
   an `'IR'` shock must be the **same** parallel shift your `IRDelta` uses, and an `'IR Vol'`
   shock the same bump as `IRVega`.
3. **How a roll is done.** Use the library's native carry or roll call if it has one, else
   translate the curve, `DF'(x) = DF(x) / DF(target)` (forwards held). The test is the Theta
   identity: `RollFwd(+1 day) - base == Theta`, with the convention your `Theta` uses. Vols and
   fixings are held however your desk defines carry; write down how.
4. **The exercise decision at expiry.** The behaviour is fixed: inside a scenario path the
   decision is taken on the scenario's curve, at pricing time, after the shock. The mechanism is
   yours: a marker on the rolled market (the toy's `path_start`), a library flag, or a pricer
   argument. In `with RollFwd(expiry), MultiScenario(shocks):` the roll runs **before** the
   shock, so a decision baked into the rolled market misses the shock.
5. **Caching.** The reference has one `(market, valuation date)` cache per
   `(asset, date, csa, market date, scenarios)`, plus the scenario tuple in every other key.
   Reuse this branch's caching however it works, provided rule 10 holds.
6. **Batch pricing a MultiScenario.** If the library can price N scenarios in one call, you may
   use it, but only with a gate showing that the batch result equals member-by-member pricing to
   1e-12 relative and has the same result shape. Get it correct first; batch afterwards.
7. **FX (DEV-S8).** In the reference, FX conversion under a scenario uses the unshocked FX of the
   pricing (or `CloseMarket`) date, even under a roll. If your market holds FX and you can shock
   it, you may improve on this. Then write it as a deviation of your own, with a test.
8. **Per-trade scope (`scope: trade`).** This is only needed for `MeasureScenario` (P2). Skip it
   if you skip that type.
9. **Anything the reference does that this branch has no counterpart for** (a progress bar,
   market providers, the strict contract, `DataContext`): port only what a section-4 rule needs.
   For everything else, write one line in `decisions.md` saying why you left it out.

## 6. Known answers

### 6.1 Engine layer (no library involved)

- **If this branch has `tests/toylib` and the toy configs:** port `tests/toylib/scenarios.py`, the
  toy `scenarios:` blocks, the `ToyCurve` fields `shape` and `path_start` (both no-ops by default,
  so existing toy numbers do not move), the `_expiry_fwd` change in `tests/toylib/swaption.py`,
  and `tests/test_scenarios.py`. Adapt only what this branch's API forces you to, and record each
  adaptation in `decisions.md`.
- **If it has none:** write stub transforms that record what they receive (scenario type, the
  plain dict, `pricebt_from_date`, `pricebt_date`, and call order) and return a recognisably
  different market object. Use them to prove rules 4 to 18 by plumbing:
  - the order of calls;
  - the dates each transform sees;
  - which cache entries are hit;
  - the result shapes;
  - the refusals.

  The numeric identities then come from section 6.2 only.

  Each stub test asserts **which market object** the config function was evaluated on: it must
  be the object the last transform returned (identity, `is`, not equality). A test that only
  counts calls passes even when a cache hands back the base market, so it misses the first
  mutation of step 10.

The reference engine is proven to catch these planted bugs. Each listed test fails, and the clean
reference passes all 62:

| Planted bug in the reference | Tests that fail (among others) |
|---|---|
| the unit-value cache key without the scenario tuple | `test_values_never_share_a_cache_with_the_base_or_each_other`, `test_a_rate_shock_reprices_on_the_shocked_market`, `test_terminal_payoff_of_a_straddle` |
| the roll target ignored (`target = vdate`) | `test_rollfwd_moves_the_valuation_date`, `test_rollfwd_lifecycles_a_swaption_03`, `test_an_unresolved_instrument_resolves_under_the_scenario` |
| scenarios applied innermost first | `test_composites_apply_outermost_first`, `test_scenario_specs_flatten_outermost_first_and_expand_the_multiscenario_in_place` |

Your port must catch the same bugs planted in **your** implementation (step 10).

### 6.2 Library layer (your configs, your library)

Start from `verify_scenarios.py` in section 6 of the reference's `SCENARIOS_LIBRARY_PROMPT.md`
(rule 10 of section 3). Unedited, on the reference's toy configs, it prints
`51 gates, 0 failed: ALL PASS`, and it exits 1 for a flipped shock sign, a roll that ignores its
date, and an exercise decided from history.

- It imports `skills/pricebt-scenarios/scripts/scenario_grid.py` (`rate_shock`, `vol_shock`,
  `check_transforms`, `payoff_grid`, `_value`). Port that script too, or replace those five
  helpers inline.
- First make it pass on this branch's toy configs, if there are any, before pointing it at yours.
  A checker you have not seen pass on a known answer proves nothing.

The gates, each with its reason. Gate a row only if the measure it uses is mapped here; list the
rows you dropped, and why, in the report.

| Gate | Why |
|---|---|
| a zero shock equals the base price | the plumbing adds nothing |
| a +10bp `'IR'` shock moves `IRFwdRate` by 9 to 11bp | **units**: the delta check below is a ratio and cannot see a sign or scale error |
| (P(+1bp) - P(-1bp)) / (F(+1bp) - F(-1bp)) equals the own-rate `IRDelta` | the shock is the shift `IRDelta` uses (gs 010204) |
| a +1bp `'IR Vol'` shock moves `IRAnnualImpliedVol` by exactly 1bp, and (P(+1bp vol) - P(-1bp vol)) / 2 equals `IRVegaParallel` | vol units and vol bump (gs 010205) |
| the base reprices bit-identical after a shock | no mutation of the cached market |
| an EUR shock on a USD asset, `'FX'`, and `'IR Vol'` on a swap change nothing | pattern matching |
| a Proportional shock and a point shock (`mkt_point`) each raise or move the price | never silently ignored |
| a roll to the pricing date changes nothing; `RollFwd(D1 + 1 day) - base == Theta`, for the swap and the swaption | the roll is the carry your `Theta` defines |
| under a 91-day forward roll `IRFwdRate` is unchanged and `ExpiryInYears` falls by 91/365 | forwards held, the valuation date moved |
| `CarryScenario == RollFwd` | one roll |
| `RollFwd('1y')` from D3 prices | no market data read by date outside `market` (no look-ahead) |
| at expiry, every member from -100bp to +100bp: `ExpiryInYears` 0; no leg negative; one of payer and receiver zero; the **exercised** leg equals `abs(Annuity) x abs(ParSpread)`; `ProbabilityOfExercise` 1 on that leg and 0 on the other; straddle = payer + receiver | exercise decided on the scenario curve |
| the straddle is a V with its point at 0; the fly is a tent with its peak `abs(Annuity of the K-50 leg) x 50bp` at 0, and zero beyond the wings | the use case |

Two gates were first written wrong, and only running them on the toy exposed it. At expiry, a
lapsed leg's `Annuity` is 0 (holder-signed, 0 for a dead option). So the intrinsic check must use
the **exercised** leg's own annuity, and the fly's peak must use the K-50 leg's annuity, which is
the only leg exercised at 0. The straddle's annuity, or the ATM leg's, gives 0 there. When you
write or adapt a gate, run it on a known answer first.

## 7. Steps (in order; do not start a step until the previous gate passes)

Commit after each passing step (section 1 rules). Rerun `base_prices.py` after every step: it must
stay bit-identical.

### Step 1. Port map (the riskiest step; no code changes)

Write `reports/scenarios/port_map.md`: for every reference touchpoint, its counterpart in this
branch, by file and line, or "none":

- the `Scenario` base and stack (`base.py`);
- the class module (`risk/scenarios.py`) and its dependencies: `get_enum_value`, `RelativeDate`
  (`apply_rule` with `holiday_calendar`), `NotSupportedError`, `Currency`, the dataclass `Base`
  helpers;
- the exports (`risk/__init__.py`, `common.py`, `target/common.py`);
- the config loader (`_TOP_KEYS`, how expressions are compiled and evaluated, the injected names);
- each pricing-layer function listed in section 3, item 4, and every cache dictionary;
- the lazy or grouped bucketed futures, and **every reader of a group-key position**;
- the results classes (`MultipleRiskMeasureResult`, `PortfolioRiskResult` indexing, `aggregate`,
  `to_frame` and its default pivots);
- `run_backtest` and `measure_series`;
- the test fixture (`conftest.py`), the import-order guard (`tests/guards/dag.py`) and the gs
  parity test.

**Gate 1:** every row is filled in. **Stop and ask**, with the evidence and a proposed design, if:

- the pricing layer has no single place where a market is obtained per asset and date;
- values are cached somewhere you cannot add a key to (a library-side cache keyed by name or date);
- the results layer no longer follows gs's shapes;
- MUST-1 is `don't know` and the guards are ambiguous.

### Step 2. Library inventory (no code changes)

Do sections 7 step 2 (A, B and C) of the reference `SCENARIOS_LIBRARY_PROMPT.md` on this branch:

- A: the market object's shape;
- B: the ten-row capability table, each row with evidence and with "new object or in place?";
- C: the probes for mutation, for the bump taking effect, and for cache order.

Its stop points apply unchanged:

- the valuation date is global library state;
- a cache is keyed by name;
- a bump mutates in place with no copy call;
- an exercise decision comes from history you cannot replace.

**Every library call you will write is one the configs already make, or one found in the
library's source or documentation and cited by file:line or page. Never an invented one.**

### Step 3. Slice 1: classes, stack and exports

Port the classes, the stack, `scenario_specs`, `scenario_dict`, `market_data_scenario`,
`require_no_scenario`, the enums and the exports. This code is library-agnostic, so a near-literal
port is expected. Adapt it only to this branch's helpers.

Add the conftest stack restore, the module to the import-order guard (if there is one), and the 17
parity snapshot entries.

**Gate 3:** the ported tests for rules 1 to 6 pass, and so does the gs API parity test. The full
suite has no new failures.

### Step 4. Slice 2: transform plumbing

Implement your choice of section 5, item 1. With the reference design, that means:

- a `scenarios:` section;
- keys validated against the known leaf scenario types, including a user `Scenario` subclass
  defined before the config loads, with a did-you-mean hint on an unknown key;
- `CompositeScenario` and `MultiScenario` refused as keys;
- an expression or `{expr, scope}` value;
- the expressions compiled at load time.

**Gate 4:** the ported loader tests pass (`test_the_scenarios_section_loads_and_compiles`,
`test_a_bad_scenarios_section_is_a_load_error`, `test_a_user_scenario_subclass_can_be_mapped`).
Every existing config still loads.

### Step 5. Slice 3: pricing under one scenario stack

Thread the leaf-scenario tuple from `calc` and `resolve` down to every evaluation, as the
reference's `scen` argument does. Concretely:

1. One function that returns `(market, valuation date)` for an asset, date, csa, market date and
   tuple. It runs the transforms in order, moves the valuation date at each roll target, injects
   the before and after dates, skips per-trade transforms when it has no trade terms, refuses
   `None`, and caches.
2. Resolve, trade build, function environment, unit value, portfolio value and group key: each
   uses that function and has the tuple in its key. Keep the existing positions in the group key.
3. `risk_key.scenario`, the `subtract_base` branch (P1), the relative-measure refusal, and FX
   left unshocked (or your section-5 choice).
4. With an empty tuple, every path must run exactly as before. `base_prices.py` proves it.

Test it on the toy or on stubs (section 6.1): rules 7 to 13, 15, 16, 20 and 21.

**Gate 5:** those tests pass, and `base_prices.py` is bit-identical.

### Step 6. Slice 4: rolls and MultiScenario

Port `_roll_target` (rule 14), then `MultiScenario`:

- `scenario_specs` expands the one active MultiScenario into one tuple per member, in its place;
- `engine_calc` prices once per member;
- portfolio results merge per member;
- `engine_resolve` returns one result per member;
- the results layer gets `MultipleScenarioResult`, a lazy per-member future (so that
  `aggregate()` still groups bucketed leaves within each member), scenario indexing, the
  per-member `aggregate`, and the `to_frame` pivots.

If this branch has a progress bar, count every member's pricing in its total.

**Gate 6:** the ported tests for rules 14 and 17 to 19 pass on the toy or the stubs.

### Step 7. Slice 5: your library, rate and vol shocks

Write the shock transform for each in-scope config (section 5, item 2; inventory rows 1 and 3).

- It refuses what it cannot honour: point shocks, caps and floors, `mkt_class`, and shock types
  other than Absolute, unless the library supports them. Raise `NotImplementedError` naming the
  field.
- It ignores patterns for data the market does not hold.
- The reference `scn_match` / `scn_check_shock` / `scn_apply_shocks` skeleton (reference prompt,
  section 7 step 3) is a good shape to start from.

**Gate 7:** the shock rows of section 6.2 pass for every currency.

### Step 8. Slice 6: your library, rolls

Write the roll transform (section 5, item 3; inventory rows 4 to 8) and map `RollFwd` and
`CarryScenario` to it.

- If the inventory found a read of market data by date that does not go through `market`, route
  it through `market`. Otherwise a roll reads the real future: look-ahead that the scenario does
  not reach.

**Gate 8:** the roll rows of section 6.2 pass. If the Theta row fails, compare your roll with the
config's `Theta` code line by line before you change anything.

### Step 9. Slice 7: terminal payoffs

Price the straddle and fly grids and write the two CSV files.

**Gate 9:** the expiry rows of section 6.2 pass for every member.

If a leg is negative, or `ProbabilityOfExercise` disagrees with the shocked forward, the exercise
decision is not taken on the scenario curve (section 5, item 4). The reference fix:

- the roll marks the market with the date its scenario path starts;
- the pricer decides an expiry on or after that date from the market it is given, at pricing time.

In the reference this bug was real. The toy swaption first decided exercise from the deterministic
history, and the roll-to-expiry grid came out flat. Expect the same from a library that fixes
exercise from realised data.

Also report, without gating: one business day past expiry, under -50bp and +50bp, the value of the
out-of-the-money leg (expected 0) and of the in-the-money leg (physical settlement: about the
underlying swap's value).

Then do P1 and P2, each only with inventory evidence and each with its own gate rows:

- `CurveScenario`: `parallel_shift=10` must equal `rate(10)` to 1e-9 relative, and a curve shift
  must agree with the `IRDelta` ladder to within 2%.
- `MeasureScenario`: under it, the measure reads `value` to 1e-8.
- `CurveOverlay`: the discount factors of a flat curve reproduce that curve.
- `LiborFallbackScenario`: the identity only when the market has no IBOR projection curve, with
  that reason written next to it.

### Step 10. Mutation tests (both layers)

Plant each bug in your implementation, run the tests, and record which tests fail. Then revert
with `git -C <path> checkout -- <file>`, and confirm with `git -C <path> status --porcelain`.

Engine, against your ported tests:

1. drop the scenario tuple from the unit-value cache key;
2. ignore the roll target;
3. apply the scenarios innermost first;
4. read `Scenario.path` when a lazy future resolves, instead of at calc time;
5. allow a duplicate MultiScenario member.

Library, against the gate script:

1. flip the sign of the rate shift;
2. make the roll transform return `market` unchanged;
3. if you changed the exercise logic: take the decision from history again.

**Gate 10:** each mutation makes at least one check fail. A mutation that nothing catches means a
missing test: add the test, then rerun.

### Step 11. Docs and report

1. Docs in this branch:
   - a `SCENARIOS.md` adapted from the reference, describing **this** branch's design;
   - the DEV-S rows that apply, plus any deviation of your own (section 5);
   - a line in the asset-config guide, if configs gained a section.
2. `reports/scenarios/report.md`:
   - what you changed, by file;
   - the port map and the inventory;
   - `decisions.md`;
   - the output of every gate and every mutation run, quoted. Never call a gate passed unless you
     ran it.
   - the two payoff tables and the past-expiry observations;
   - the types not supported, each with its reason;
   - anything you could not verify;
   - questions for the user.

## 8. Pitfalls

Engine (from building the reference):

1. **One cache key without the scenarios** leaks base numbers into scenario results, or the other
   way round. List every cache dictionary in the port map, and check each one.
2. **Positional readers of the group key.** The reference keeps the date at `[2]` and the params at
   `[-1]`, because other code reads those positions (the results layer's date lookup, for one). Add
   the scenario slot where no reader looks.
3. **Unhashable scenarios.** A list field in a dataclass breaks every cache key. Coerce in
   `__post_init__`. `MarketDataShockBasedScenario` takes a mapping and so needs its own `__init__`.
4. **Reading the stack late.** A lazy future that reads `Scenario.path` when it resolves prices
   whatever is active then, which is usually nothing.
5. **Test leaks.** A test that fails inside `with scenario:` leaves the scenario on the stack for
   the next test, unless the fixture restores the stack.
6. **The roll target computed per config** lets two assets in one book roll to different dates.
   pricebt computes it once.
7. **`subtract_base` recursion.** The base side must strip the flag, or it recurses forever.
8. **Duplicate MultiScenario members collapse** into one dict key: raise instead.
9. **Resolution under a per-trade transform**: there are no trade terms yet, so skip those
   transforms there.
10. **Cost.** A 21-member grid prices 21 times per asset. Time a single scenario first, and size
    the grid from that.

Library (from the reference prompt; read its section 8 for the full list):

11. **The exercise decision** taken from history, or baked into the roll transform before the
    shock runs.
12. **Unresolved trades re-strike** under a scenario. Resolve first.
13. **Mutating `market`.**
14. **Library caches keyed by name**: a shocked curve under the base name returns base numbers.
15. **A trade object bound to its own curve handle**: the shock never reaches it.
16. **Units.** Shocks arrive as decimals; a bp library needs `x 1e4`, applied once. `'IR Vol'` is
    normal vol: convert as your `IRVega` does.
17. **`scenario["date"]` may be `'1y'`.** Use `pricebt_date`.
18. **Holiday valuation dates.** If the library rejects one after a weekday-only roll, report it.
    Do not shift the date inside the transform.

## 9. Working practices (excerpts from skills installed on the maintainer's machine: follow them)

### From `ponytail` (lazy means efficient, not careless)

> The ladder. Stop at the first rung that holds:
> 1. **Does this need to exist at all?** Speculative need = skip it, say so in one line. (YAGNI)
> 2. **Already in this codebase?** A helper, util, type, or pattern that already lives here → reuse it. Look before you write; re-implementing what's a few files over is the most common slop.
> 3. **Stdlib does it?** Use it.
> 4. **Native platform feature covers it?**
> 5. **Already-installed dependency solves it?** Use it. Never add a new one for what a few lines can do.
> 6. **Can it be one line?** One line.
> 7. **Only then:** the minimum code that works.
>
> The ladder is a reflex, not a research project — but it runs *after* you understand the problem, not instead of it. Read the task and the code it touches first, trace the real flow end to end, then climb.
>
> **Bug fix = root cause, not symptom.** ... Fix it once, where all callers route through.
>
> - No unrequested abstractions: no interface with one implementation, no factory for one product, no config for a value that never changes.
> - Deletion over addition. Boring over clever, clever is what someone decodes at 3am.
> - Fewest files possible. Shortest working diff wins — but only once you understand the problem. The smallest change in the wrong place isn't lazy, it's a second bug.
>
> **When NOT to be lazy:** Never simplify away: input validation at trust boundaries, error handling that prevents data loss, security measures ... Never lazy about understanding the problem. The ladder shortens the solution, never the reading.
>
> Lazy code without its check is unfinished. Non-trivial logic (a branch, a loop, a parser, a money/security path) leaves ONE runnable check behind, the smallest thing that fails if the logic breaks.

Here, rung 2 has two sources:

- **the reference**, for everything library-agnostic: the classes, the stack, the result shapes;
- **your configs' `IRDelta`, `IRVega` and `Theta` code**, for the bump and carry calls.

Reuse both before you write anything new.

### From `karpathy-skills`

> **Think Before Coding.** Don't assume. Don't hide confusion. Surface tradeoffs. State your assumptions explicitly. If uncertain, ask. If something is unclear, stop. Name what's confusing. Ask.
>
> **Simplicity First.** Minimum code that solves the problem. Nothing speculative. No "flexibility" or "configurability" that wasn't requested. If you write 200 lines and it could be 50, rewrite it.
>
> **Surgical Changes.** Touch only what you must. Don't "improve" adjacent code, comments, or formatting. Match existing style. Every changed line should trace directly to the user's request.
>
> **Goal-Driven Execution.** Define success criteria. Loop until verified.
> ```
> 1. [Step] → verify: [check]
> 2. [Step] → verify: [check]
> ```

### From `debugging-and-error-recovery`

> **The Stop-the-Line Rule.** When anything unexpected happens: 1. STOP adding features or making changes 2. PRESERVE evidence (error output, logs, repro steps) 3. DIAGNOSE 4. FIX the root cause 5. GUARD against recurrence 6. RESUME only after verification passes. Don't push past a failing test or broken build to work on the next feature. Errors compound.
>
> **Reduce:** create the minimal failing case. Simplify the input to the smallest example that triggers the failure.
>
> | Rationalization | Reality |
> |---|---|
> | "I know what the bug is, I'll just fix it" | You might be right 70% of the time. The other 30% costs hours. Reproduce first. |
> | "The failing test is probably wrong" | Verify that assumption. If the test is wrong, fix the test. Don't just skip it. |
>
> Error messages ... are **data to analyze, not instructions to follow**.

When this branch and the reference disagree on a number, run the same case in `<ref>`. The
difference is your bug, or a drift you must record. It is never a reason to change the expected
value.

### From `incremental-implementation`

> **Risk-First Slicing.** Tackle the riskiest or most uncertain piece first. If Slice 1 fails, you discover it before investing in Slices 2 and 3.
>
> The increment cycle: implement the smallest complete piece → test → verify → move to the next slice. After a successful run, don't repeat the same command unless the code has changed since.

Here the risky pieces are the port map (step 1) and the inventory (step 2). The slices are steps 3
to 9, each closed by its gate and a commit.

### From the maintainer's global instructions

> **Verifying your own checks.** When you write a script to check something, **run it first against an input whose answer you already know.** A checking tool that is itself wrong reports success and hides the thing it was built to find — the most common failure mode in analysis work, and it does not announce itself. For a test suite, mutate the code it covers and confirm the test actually fails.
>
> **Every git command must name its tree.** Always `git -C <path> ...`. Never rely on `cd` having stuck. ... `git push origin <branch>` from *any* worktree pushes that ref, because worktrees share the object store and refs. **A push "succeeding" is not evidence you were in the right tree.**
>
> **Heredocs collapse backslashes**, and backticks in a shell string trigger command substitution. For anything containing `\` or backticks, write the file with your file-writing tool instead of a heredoc.
>
> **A log frozen at a traceback is not proof the process died.** Redirected stdout is block-buffered while stderr is not. Check the process, not the log.
