# Worked example 2: wiring a pricing SERVICE into pricebt

`zeta` is a small FICTIONAL rates-pricing **service**: it prices USD SOFR overnight-indexed swaps only from a market you have uploaded to it, refers to that market by id, answers
every request with a plain `dict` (a failure is `{"status": "ERROR", "code": ...}`, never an exception), and has no attribution, no calendar of its own and no setting to change.
`zeta_adapter` is the pricebt adapter for it. Everything here was executed; the numbers below are the ones the commands printed. This is the second worked example of the skill
`pricebt-wire-external-library`: the first, [example/README.md](../example/README.md) (`acmelib`), is an **in-process** library with a process-global valuation date and its own
calendar registry; zeta has neither, and a service has other problems (uploading, request cost, error dicts, a fixed risk grid, no scenarios pricebt's layers can use).

**One rule.** The core (everything under `src/pricebt` except `contrib/`) has zero dependence on any pricing library. The adapter is the only place `zeta` is imported
(`zeta_adapter/_compat.py`), and the library is reached only through **schema name -> binding -> callable**. Nothing in this example edits `src/`, `docs/` or any other test or config.

**Honest caveat.** zeta's arithmetic core (`zeta_lib/zeta/_engine.py`) REUSES `pricebt.testing.refstack` (schedules, discounting over fixings, the bootstrapped par-swap risk curves), so zeta
agrees with the reference stack to round-off by construction: the tie-out below is "exact or noise" (pv 3e-13 relative, layers at most 2.3e-6 currency). A real service is independent:
expect real noise floors, some declared differences (each with a `reason`) and a conventions document. What this example proves is the WIRING: units, signs, shapes, the upload rule, error
dicts, a fixed risk grid, derived worlds for the layers, the overlays. The adapter was first written by an agent that had only the skills (a cold-start test) and then adapted to this layout
(paths, the import fallback of section 10, five tests).

## 0. Run it (PowerShell, from the repository root)

```powershell
$env:PYTHONPATH = "src;tests;skills\pricebt-wire-external-library\example-service"      # the adapter's parent directory; zeta itself is found by _compat (section 10)
$env:PYTHONDONTWRITEBYTECODE = "1"
$ex = "skills\pricebt-wire-external-library\example-service\zeta_adapter\config"

python -m pricebt validate $ex\zeta_tieout_base.yaml --stack $ex\zeta_swap.yaml           # OK           (validate and run repeat --stack: --stack a.yaml --stack b.yaml)
python -m pricebt run $ex\zeta_tieout_base.yaml --stack $ex\zeta_swap.yaml --dry-run     # OK: first pricer ZetaPricer at 2024-05-24 17:00:00-04:00 (reference_date 2024-05-24)

# the tie-out: exit 0, "harness self-test: passed", "TIE-OUT PASSED" (7 s). Note the SECOND reference overlay (section 6) and that tieout takes ONE --stack per stack with a COMMA between overlays
# (quote it in PowerShell: unquoted, PowerShell does not expand `$ex` inside a token that contains a comma, so the run stops with "[CFG-FILE] config file not found: ...\$ex\refstack_zeta_pillars.yaml", exit 2: measured)
python -m pricebt tieout $ex\zeta_tieout_base.yaml --stack "reference=configs\adapters\refstack_swap.yaml,$ex\refstack_zeta_pillars.yaml" --stack zeta=$ex\zeta_swap.yaml --reference reference

# the same against the reference's DEFAULT pillars: exit 1, "TIE-OUT FAILED: reference_vs_zeta: L2.dv01; ...: L3.tay_delta; ...: L3.tay_unexplained" (the pinned gap, section 6, NOT a bug)
python -m pricebt tieout $ex\zeta_tieout_base.yaml --stack reference=configs\adapters\refstack_swap.yaml --stack zeta=$ex\zeta_swap.yaml --reference reference

# a negative control (section 8): the rate binding without scale 0.01: exit 1, "TIE-OUT FAILED: reference_vs_bad: L2.rate; ...L3.tay_convexity; ...L3.tay_delta; ...L3.tay_unexplained"
python -m pricebt tieout $ex\zeta_tieout_base.yaml --stack "reference=configs\adapters\refstack_swap.yaml,$ex\refstack_zeta_pillars.yaml" --stack bad=$ex\mistakes\zeta_swap_no_percent.yaml --reference reference
```

`python -m pricebt` also puts the working directory on `sys.path`; a script run as `python path\to\script.py` puts only the script's own directory there. So an adapter that sits at the repository
root imports from the CLI without any `PYTHONPATH` entry and fails from a script: always name the adapter's parent directory in `PYTHONPATH` (here `example-service`; `.` for a root-level adapter).

The CLI cannot compare the ladder (it audits `dv01`, `gamma`, `rate`: `DEFAULT_AUDIT_MEASURES` in `src/pricebt/tieout/runner.py`); the Python API can, and the tests use it:

```powershell
python skills\pricebt-wire-external-library\example-service\probes\p5_tieout_positions.py
# selftest: passed | passed: True | failures: [] | n_positions: 4     then the twelve ladder rows, exit 0
```

The tests (marker `core`: zeta is fictional, no pricing library is needed; each file finds the example from its own location, so the working directory does not matter):

```powershell
$env:PYTHONPATH = "src;tests"
python -m pytest tests/test_skills_example_zeta_facts.py tests/test_skills_example_zeta_wrap.py tests/test_skills_example_zeta_swap.py tests/test_skills_example_zeta_no_infra.py tests/test_skills_example_zeta_proofs.py -q -o addopts= -p no:cacheprovider
# 111 passed: facts 15, wrap 20, swap 42, no_infra 3, proofs 31 (proofs alone about 100 s: it runs the tie-outs and the CLI)
$env:PYTHONPATH = "src"; python tests\guards\blocker.py tests/test_skills_example_zeta_facts.py tests/test_skills_example_zeta_wrap.py tests/test_skills_example_zeta_swap.py tests/test_skills_example_zeta_no_infra.py tests/test_skills_example_zeta_proofs.py -o addopts= -p no:cacheprovider -q
# the same with every banned vendor import root made unimportable: 111 passed
```

## 1. Files

| file | lines | what it is |
|---|---|---|
| `zeta_lib/zeta/` (`client`, `_check`, `_engine`, `__init__`) and [zeta_lib/ZETA_DOCS.md](zeta_lib/ZETA_DOCS.md) | 378 + 139 | the fictional service (in-process, deterministic, simulated cost: 120 ms per upload, 25 ms + 3 ms per trade per request) and its developer reference (the cold-start run wrote the adapter from this document alone, without reading the package source) |
| `zeta_adapter/_compat.py` | 104 | the only module that imports zeta: the client, the upload counter `UPLOADS`, `upload`, `price`, the translation of error dicts |
| `zeta_adapter/wrap.py` | 151 | snapshot -> zeta market (zero rates in bp, fixings in bp, the snapshot's holidays), uploaded lazily and once, derived worlds content-addressed |
| `zeta_adapter/conventions.py` | 65 | shared vocabulary -> what zeta fixes: ONE supported value per key, every refusal names its reason and the supported set |
| `zeta_adapter/swap.py` | 318 | `ZetaSwap`, the factory, `SWAP_BIND`, the ladder reducer, the four layers, the `swap` Kit |
| `zeta_adapter/__init__.py` | 29 | exports `swap`, `wrap`, `STACK` |
| `zeta_adapter/config/zeta_swap.yaml` | 9 | the stack overlay (factory + wrap only) |
| `zeta_adapter/config/zeta_tieout_base.yaml` | 55 | the BASE config (`registry.allow`, four swaps over a month with two holidays, layers on) |
| `zeta_adapter/config/refstack_zeta_pillars.yaml` | 17 | the SECOND overlay of the reference stack (section 6) |
| `zeta_adapter/config/mistakes/*.yaml` (10), `zeta_mistakes.py` | 10 + 65 | the negative controls: overlays that break exactly one thing, and the five pieces of code the last five of them need |
| `probes/p1_units_signs.py` | 101 | the discovery probe (hypothesis -> number, a control first); its helpers `market_of`, `price`, `trade` are what the facts tests use |
| `probes/p5..p8` | 19 + 62 + 60 + 54 | the tie-out with the ladder audited, and the known-answer scripts of `pricebt-layers-and-ladder` (golden case, dv01 in every state, dv01 and gamma against revaluation) with the `LIB` lines changed |
| `probes/p9_pillar_gap.py` | 82 | the numbers of section 6 on one market (dv01 gap by age, fold or drop of the 1M bucket, the tie-out against the default reference with the ladder audited); exit 0 when they match; not run by a test |
| `tests/test_skills_example_zeta_*.py` (5) | 188 + 242 + 276 + 45 + 310 | 111 tests, marker `core`: `facts`, `wrap`, `swap`, `no_infra`, `proofs` |

## 2. The foreign API, and where each mismatch is converted

| zeta does | pricebt wants | converted in | proven by |
|---|---|---|---|
| dates are ISO **strings** only (a `datetime.date` is `Z101`); `as_of` may not precede the market's `asof` | `dt.date` | `_compat.iso`, `to_date`; the factory returns dates | `test_a_date_object_is_never_sent_to_zeta`, `test_fact_dates_are_iso_strings_only...` |
| the curve is continuously compounded ZERO rates in bp, `[days, z]` nodes, a declared day count; the snapshot holds discount factors at node dates | tag `log_linear_df` | `wrap.zero_nodes`: `z = -ln(DF) * 365 / days * 1e4`, declared ACT/365; any other tag is `ConfigError` | `test_the_zeta_market_is_the_snapshot_in_zetas_units`, `test_a_tag_zeta_cannot_honour_is_refused...` |
| fixings in BASIS POINTS keyed by ISO date; a fixing dated D is used only when `as_of > D` | the snapshot's `unit` (percent or decimal) | `wrap._fixings` (`_BP`) | `test_fixings_reach_zeta_in_basis_points...`, control 8 |
| ONE calendar per market (holidays + Saturday and Sunday), its name is a label | the snapshot's holidays | `wrap._calendar`; a weekmask that is not Monday-Friday is refused | `test_the_snapshots_holidays_are_the_calendar_zeta_sees_and_two_markets_coexist`, controls 6 and 7 |
| rates in bp (`fixed_rate_bp`, `PAR_BP`), notional in MILLIONS (`notional_mm`) | percent, currency | the factory (`percent * 100`, `notional / 1e6`); binding `rate: {scale: 0.01}` | `test_units_and_signs_agree_with_the_reference...` (both sides, two notionals), control 1 |
| risk per +10bp (`RISK_10BP`, the ladder) and per (10bp)^2 (`CONVEXITY_10BP`) | per +1bp, per bp^2 | bindings `dv01: {scale: 0.1}`, `gamma: {scale: 0.01}`, `delta_ladder: {scale: 0.1}` | controls 2 and 3 |
| already HOLDER-signed (`PAY` gains when rates rise; `RECEIVE` is the exact negative) | payer-positive | nothing: no `sign` on `value`, `dv01`, `gamma`, `rate` or any layer. ONE exception: `LADDER_10BP` has the OPPOSITE sign to `RISK_10BP`, so `delta_ladder` carries `sign: -1` | `test_every_unit_and_sign_conversion_is_one_visible_line...`, `test_fact_the_ladder_has_the_OPPOSITE_sign...`, control 4, the kit teeth test |
| the ladder is a LIST of `{pillar_months, risk}`, always TWELVE pillars (1M ... 30Y) | `dict[str, float]`, upper-case tenors, exactly the bound `keys` | reducer `zeta_ladder_to_tenor_dict`, registered by name; a pillar it does not know is dropped only when it carries no risk | `test_the_ladder_reducer_maps_zetas_twelve_pillars...` |
| a service error is an ERROR DICT; a closed client raises `RuntimeError` on upload | pricebt errors | `_compat._raise`: `Z530` -> `MarketDataUnavailable`, `Z101`/`Z204`/`Z301` -> `ConfigError`, `Z412`/`Z500`/`RuntimeError` -> `MethodCallError` | `test_a_missing_fixing_is_market_data_unavailable...`, `test_a_bad_field...`, `test_a_closed_client...` |
| uploading is the expensive step; a market id belongs to the client that uploaded it | one upload per snapshot | `wrap.ZetaPricer.market_id` (lazy, memoised); the id is dropped on pickle | section 4, control 10 |
| NO attribution; a scenario is `parallel_bp` or `roll`, one key | four layers, ADR 005 definitions | `swap.py::decomposition`: date-space roll from `as_of`, the rest in DERIVED markets | section 4, the layer kit, the golden case |
| every convention is fixed (USD, spot 2, ACT/360, annual, modified following, payment lag 2, daily-compounded SOFR, short front stub) | the shared vocabulary | `conventions.swap_conventions`: one supported value per key, else `CFG-CONVENTION-UNSUPPORTED` naming the reason | `test_a_convention_zeta_cannot_express_is_refused_never_approximated` |
| `RISK_10BP` is a +-1bp PARALLEL par difference, the ladder a +-0.5bp per-pillar one: they differ by 2e-7 | one `dv01` definition | see section 3 | `test_dv01_has_one_definition_in_every_state_and_equals_the_references`, control 9 |

## 3. How each pricebt concept maps to a service (each row is executed by a test)

* **No provider of its own.** zeta serves no market data, so the base config's provider (here the library-free `SyntheticMarket`, or a recorded store) feeds EVERY stack, and the L0 row
  `snapshot_digests` is `exact` by construction. The upload belongs in `wrap`. There is no exporter and no `CurveStore` in this example (the platform that also serves data needs both:
  `pricebt-market-data-snapshots`).
* **`wrap` uploads once per digest.** `wrap(SnapshotPricer)` is memoised per snapshot pricer and the `ZetaPricer` memoises its market id, so a snapshot is uploaded LAZILY (never if never priced)
  and ONCE; `MarketData` wraps each snapshot once, so 19 snapshots are 19 uploads (`test_a_real_backtest_uploads_one_market_per_snapshot...`). Proven with `client.stats["markets_uploaded"]`,
  not with a mock. A clone (deep copy, pickle) drops the id and uploads its own on first use.
* **Derived worlds for the layers.** Roll, the resampled base and the shocked worlds along the realised zero-rate move need curves zeta's scenarios cannot express, so they are markets
  of their own: `ZetaPricer.world_id` uploads each DISTINCT content once (content-addressed by a hash of the market dict). The carry is the only layer zeta can price by itself (the t0 market
  priced `as_of` t1). Section 5 has the accounting.
* **The ladder has twelve pillars and a registered reducer.** `SWAP_BIND["delta_ladder"]` names the reducer by its REGISTERED name (no dotted path, so no allow-list entry) and lists the
  twelve `keys`; nothing is folded or dropped, so the ladder sums to `dv01`. A default block whose targets are all methods (no `function` target) is also what lets the Python facade take the
  adapter's `Stack` OBJECT: `test_the_gs_style_facade_builds_an_irswap_on_the_zeta_stack_object` (the dotted string `"zeta_adapter:STACK"` is still refused with `CFG-ALLOW`).
* **Signs and units are single visible lines.** Four `scale` lines and ONE `sign` line (the ladder). `value` (a `Valuation`) is not touched by `scale` or `sign`; zeta needs neither.
* **`dv01` has ONE definition** (`pricebt-layers-and-ladder`): matured 0; started (`start < reference date`) the SUM OF THE LADDER (`-sum(LADDER_10BP)`); not started the ANALYTIC ANNUITY, obtained
  exactly from zeta as `NPV(K - 1bp) - NPV(K)` (the fixed leg is linear in K). Even though zeta offers a parallel `RISK_10BP`, it is NOT bound: it agrees with the ladder sum to 1.9e-7
  relative, and the engine's baseline `tay_unexplained` (a difference of large numbers) amplified that to 1.17e-3 against its tolerance of 1e-3 (cold-start measurement, before the change).
* **`cash` needs the t1 fixings when the step is longer than the payment lag.** A flow paid before t1 can depend on a fixing published inside the interval, which the t0 market would
  IMPLY from its curve. `ZetaSwap._cash` prices from the t0 market when t1 is within two business days of t0 and from a t0 market REBUILT with the t1 fixings (a derived world) otherwise;
  `test_the_layers_conform_over_a_coarse_step_where_a_flow_can_depend_on_a_fixing_published_inside_the_interval` runs the kit over such a step.
* **Errors are checked once.** `_compat.price` tests `resp["status"]`; nothing downstream sees a dict.
* **The mapping worksheet is not a separate file** (decision D17): the row per schema name (library callable, unit, sign, binding or code, gap) is the table of section 2 and the docstring of
  `zeta_adapter/swap.py`; a real adapter keeps those rows in sections 3-6 of its conventions document, pasted from the row template `skills/pricebt-map-library-to-schemas/references/mapping-worksheet.md`.

## 4. The steps that differ for a service (which file, which check proves it)

| # | what you did | file | check that proves it |
|---|---|---|---|
| 1 | probe the SERVICE on known answers: a par swap is worth 0 and its mirror is the exact negative; which side gains; the unit of each number; what a bad request answers; what an upload costs | `probes/p1_units_signs.py` | 15 fact tests (`test_fact_*`), each asserting a FACT the numbers rest on (a sign, a ratio, an error code), never a market number: a library upgrade that changes a convention fails a NAMED test |
| 2 | one module owns the client, the upload counter and the error translation; there is nothing global to guard | `zeta_adapter/_compat.py` | `test_a_missing_fixing_is_market_data_unavailable...`, `test_a_closed_client_and_an_unknown_market_are_method_call_errors` |
| 3 | snapshot -> market, lazily and once; derived worlds content-addressed | `zeta_adapter/wrap.py` | `test_a_snapshot_is_uploaded_lazily_and_ONCE...`, `test_marketdata_wraps_each_snapshot_once...`, `test_with_the_layers_on_the_snapshots_are_still_uploaded_once_and_each_derived_world_once` |
| 4 | the fixed conventions: one supported value per key | `zeta_adapter/conventions.py` | `test_every_key_of_the_vocabulary_has_a_stated_decision...` |
| 5 | the factory resolves `par`, the effective date and the maturity at the fill pricer by asking zeta (a forward-start tenor costs two requests), and returns concrete dates and a percent rate | `swap.py::swap_factory` | `test_dates_resolve_like_the_reference_on_the_snapshots_holidays` (9 cases), `test_par_resolves_to_a_percent_rate...` |
| 6 | measures in pricebt's units and signs; the ladder reducer; ONE dv01 definition | `SWAP_BIND`, `ZetaSwap.dv01` | `test_units_and_signs_agree_with_the_reference...`, `test_dv01_has_one_definition...`, `test_the_annuity_definition_matters_off_par...` |
| 7 | the four layers from repeated `price()` calls | `ZetaSwap.decomposition` | the layer conformance kit on a fresh swap, a seasoned swap across a payment date and a coarse step; the kit CATCHES a delta or convexity bound with a sign (`test_the_kit_has_teeth_on_this_adapter`); `p6_golden_control.py` (nine numbers to 0.011) |
| 8 | the overlay, the base with `registry.allow`, the reference stack on zeta's pillars (section 6) | `config/` | the CLI tie-out (exit 0) and its non-vacuity twin (exit 1 on the default pillars and on a control) |
| 9 | negative controls | `config/mistakes/`, `zeta_mistakes.py` | section 8: each first shown NOT to fire on the correct wiring |
| 10 | the service rule: count uploads with `client.stats`, not with a mock | `tests/test_skills_example_zeta_wrap.py` | section 5 |

## 5. The upload accounting

A snapshot is uploaded once whatever is asked of it. What the layers add is a set of DERIVED markets, one upload per distinct content, per pair of consecutive snapshots. Measured over the base
config (19 snapshots, 18 intervals, 4 swaps, `cadence: eod`); `client.stats` is the service's own counter, `UPLOADS` the adapter's split of it:

| run | uploads | of which snapshot / derived | requests | trade evaluations | simulated service time |
|---|---:|---|---:|---:|---:|
| layers off | 19 | 19 / 0 | 178 | 203 | 7.3 s |
| layers on | 109 | 19 / 90 | 970 | 995 | 40.3 s |
| layers off, wrap that re-uploads on every request (control 10) | 177 | (the counter does not see it) | 178 | | 26.3 s |

The rule: uploads = snapshots + 5 x intervals + cash worlds. Per interval, five worlds for the layers with Richardson at two steps (the rolled world, which is also the resampled base when the two grids agree, and four shocked worlds:
90 = 5 x 18 at `eod`), plus ONE cash world for each interval whose `t1` is more than the payment lag (2 business days) after `t0`. At `eod` no step exceeds the lag, so `5 x intervals` is exact there (it is what
`test_with_the_layers_on_the_snapshots_are_still_uploaded_once_and_each_derived_world_once` asserts, on that cadence). At `every_n:5` the four steps are 4, 5, 5 and 4 business days (two holidays), all above the lag: 43 = 19 + 5 x 4 + 4 uploads (24 derived: 20 + 4 cash worlds);
at `every_n:3` five of the seven steps are 3 business days: 59 = 19 + 5 x 7 + 5 (40 derived: 35 + 5). Both were measured with the script of `skills/pricebt-enterprise-platform-patterns/references/cost-budget.md` section 5, and the intervals
and the steps counted from the engine's own (previous, current) pairs. One more world per interval is needed when the curve node grids of the two snapshots differ (a resampled base): the base does not exercise it, and the
only measurement is the golden case of `probes/p6_golden_control.py` (different node grids, a 14-day step: 7 derived worlds = 6 + 1 cash world). Requests: the five measures of a trade travel in ONE request per snapshot (memoised on the wrapped pricer), and the layers add about ten requests per position per cadence
point (970 against 178 here). `attribution.cadence` is the lever. Both counters are asserted by `test_a_real_backtest_uploads_one_market_per_snapshot_and_a_pair_of_snapshots_adds_only_its_derived_worlds` and
`test_with_the_layers_on_the_snapshots_are_still_uploaded_once_and_each_derived_world_once`; the wrap tests install a FRESH `zeta.Client()` per test (an autouse fixture) so `client.stats` is that
test's own, and refuse network access.

## 6. The pillar-set caveat: the library's risk grid is fixed and differs from the reference's

zeta's par-swap risk curve has TWELVE pillars (1M 3M 6M 1Y 2Y 3Y 5Y 7Y 10Y 15Y 20Y 30Y) and is not configurable; the reference stack's default ladder has ELEVEN (no 1M). A risk number depends on
the pillar set, so two stacks on different sets report two different measures. Decision:

1. **Keep the library's native buckets.** The ladder has twelve keys; nothing is folded into a neighbour and nothing is dropped (on the seasoned 2Y over the 19 grid days, `probes/p9_pillar_gap.py`:
   folding 1M into 3M leaves the largest bucket 0.07 to 0.21 currency from the reference's, 0.21 on the first day, above the ladder's absolute floor of 0.1; dropping it leaves 11 to 32, 31.6 on the first day, and breaks `sum(ladder) == dv01`).
2. **Align the REFERENCE, not the adapter,** with a second overlay that changes only the `tenors` kwarg of the reference's `dv01`, `gamma` and `delta_ladder` bindings
   (`zeta_adapter/config/refstack_zeta_pillars.yaml`); the reference's arithmetic is untouched. The second overlay comes after the shipped one: `--stack reference=a.yaml,b.yaml`.
3. **Keep a test that pins the gap against the default reference,** and do NOT declare it as a tolerance: it is a property of two risk curves, not noise to widen.

```yaml
instruments:
  usd_sofr_ois:
    bind:
      dv01: {target: {method: dv01}, kwargs: {ctx: "@ctx", tenors: [1M, 3M, 6M, 1Y, 2Y, 3Y, 5Y, 7Y, 10Y, 15Y, 20Y, 30Y]}}
      gamma: {target: {method: gamma}, kwargs: {ctx: "@ctx", tenors: [1M, 3M, 6M, 1Y, 2Y, 3Y, 5Y, 7Y, 10Y, 15Y, 20Y, 30Y]}}
      delta_ladder:
        target: {method: delta_ladder}
        kwargs: {ctx: "@ctx", tenors: [1M, 3M, 6M, 1Y, 2Y, 3Y, 5Y, 7Y, 10Y, 15Y, 20Y, 30Y]}
        keys: [1M, 3M, 6M, 1Y, 2Y, 3Y, 5Y, 7Y, 10Y, 15Y, 20Y, 30Y]
        reduce: dict_of_floats
```

Measured (`run_tieout(..., audit_measures=("dv01", "gamma", "rate"))` against the DEFAULT reference overlay, shipped tolerances, nothing declared): the tie-out FAILS on exactly `L2.dv01`,
`L3.tay_delta` and `L3.tay_unexplained` (each with its `/unit` twin), and nothing else moves (value, cash, rate, the four layers, L0, L1, L4). With the LADDER audited as well (Python API only; the pin test audits `dv01`, `gamma`, `rate`, so this part is printed by
`probes/p9_pillar_gap.py`, not pinned) the failing set gains two rows: `L2.delta_ladder.1M` is `structure` (the reference has no such bucket) and `L2.delta_ladder.3M` `exceeds` (`max_rel` 0.32, `max_abs` 31.6).

All figures of this section are on ONE market, the base's synthetic market at 2024-05-24 (20mm payers struck 3.9; a 1Y swap for the N-months-left rows), and `probes/p9_pillar_gap.py` (about 10 s) prints them:

| where | dv01, default 11 pillars against zeta's 12 | note |
|---|---|---|
| the base's seasoned 2Y payer, first day (`position=P000003`) | 1.046e-4 relative | the shipped `L2.dv01` tolerance is 1e-4: just over |
| a swap with 6 months left | 1.8e-4 | the reference stack alone (`dv01(tenors=...)`: zeta equals it on twelve pillars to round-off); computed by `p9_pillar_gap.py`, not pinned by a test |
| a swap with 1 month left | 6.6e-3 | the near-dated flows sit between the 1M and 3M pillars |
| a swap with 2 weeks left | 6.6e-3 | its 3M bucket is 79.93 on 11 pillars, 0.00 on 12 (the 1M bucket takes 80.46) |

The pin is `test_against_the_references_default_eleven_pillars_only_dv01_and_what_reads_it_move` (the failing set, `1.0e-4 < max_rel < 3e-4` on `P000003`, the other rows exact or noise); its
CLI twin is `test_the_cli_says_failed_with_exit_one_on_the_default_pillars_and_on_a_negative_control`. Against the SECOND overlay every row is `exact` or `noise` with the shipped tolerances.

## 7. Measured results

Base: four swaps on the synthetic market (a 10Y receiver 10mm, a 5Y payer 5mm, a SEASONED 2Y payer 20mm struck 3.9 that started 2023-06-12, a receiver 15mm starting in 6 months struck 4.6, i.e. forward
starting and 60bp off the market), 2024-05-24 .. 2024-06-21, 19 points, two holidays. `run_tieout(..., audit_measures=(dv01, gamma, rate, delta_ladder))`, reference on twelve pillars, SHIPPED
tolerances, nothing declared, harness self-test passed. The CLI (`dv01`, `gamma`, `rate`) reports 41 quantities, all passing; the Python API with the ladder reports 53 rows: 21 `exact`, 32 `noise`, 0 anything else.

| level | result |
|---|---|
| L0 inputs | snapshot digests, resolved terms, conventions digest: exact |
| L1 marks | `pv` 2.9e-13 relative (1.5e-9 currency), `cash` exact (a payment falls inside the window, asserted by a test, so the cash path was compared), `financing`, position size: exact |
| L2 measures | `dv01` 4.0e-13 relative, `gamma` 5.7e-10, `rate` 4.4e-16, twelve ladder buckets at most 4.7e-9 currency (4 exact, 8 noise) |
| L3 layers | max absolute difference: carry 6.2e-9, roll 6.2e-9, delta 8.0e-8, convexity 2.3e-6, `unexplained` 2.3e-6 |
| L4 portfolio | exact |

The known-answer scripts (run by `test_the_layers_skill_known_answer_scripts_pass_on_this_adapter`, each a child process with the documented `PYTHONPATH`): `p6` the golden case (a seasoned 3Y payer
50mm across 14 days, curves with DIFFERENT node grids, so the resampling and the rebuilt-fixings paths run) all nine numbers to 0.011; `p7` dv01 in every state against the reference on zeta's twelve pillars (`LIB_TENORS`: at most 5.2e-14; printed beside it, against the reference's default eleven: 2.0e-05 to 8.0e-05 on the started rows), 14 rows ok; `p8`
dv01 and gamma against full revaluation in a flat world (signs and scales), 2 rows ok. From the cold-start run, whose sweep scripts are not shipped: dates, par rate and annuity against the
reference over 520 trades gave 0 date differences, par rate 6.6e-16 relative, NPV 2.1e-9.

A caution on what the shipped floors can see: a bucket smaller than the ladder floor (100 per unit) is compared absolutely (`1e-3 x 100`), so small buckets hide small errors; the tests therefore
also compare every bound measure and the ladder directly with the reference (relative 1e-6, both directions, two notionals) and `dv01` to 1e-9 in every state (unstarted, started, matured).

## 8. Negative controls: what the harness says for each mistake

Each control is first shown NOT to fire on the correct wiring (the same row is `exact` or `noise` in `reference_vs_good`), then to fire at the expected level and quantity with a located difference.
`max_rel` is the harness's number. Controls 1-5 are one-line overlays of a binding; 6-10 name a wrong piece of code in `zeta_mistakes.py` (a `wrap` or a `dv01` function).

| # | overlay (`config/mistakes/zeta_swap_...`) | mistake | level.quantity | status | located / size | what else moves |
|---|---|---|---|---|---|---|
| 1 | `no_percent` | `rate` binding without `scale: 0.01` (bp read as percent) | `L2.rate` | `exceeds` | `P000001`, max_rel 99 (402.5 against 4.025) | `L3.tay_delta`, `tay_convexity`, `tay_unexplained`; L0, L1, L4 exact |
| 2 | `dv01_per_10bp` | `dv01` binding without `scale: 0.1` | `L2.dv01` | `exceeds` | `P000001`, max_rel 9 (a factor of 10) | `L3.tay_delta`, `tay_unexplained` |
| 3 | `gamma_per_10bp_sq` | `gamma` binding without `scale: 0.01` | `L2.gamma` | `exceeds` | `P000001`, max_rel 99 (a factor of 100) | `L3.tay_convexity`, `tay_unexplained`; `dv01`, `rate` exact |
| 4 | `ladder_no_sign` | `delta_ladder` binding without `sign: -1` | `L2.delta_ladder.5Y` (and every bucket with risk: 9 in all) | `exceeds` | `P000002`, max_rel 2.0 | `dv01` stays exact: it sums the RAW ladder in code (needs `delta_ladder` in the audit measures: Python API) |
| 5 | `delta_layer_wrong_sign` | `delta` layer bound with a sign (zeta is holder-signed: a layer needs none) | `L3.delta` | `exceeds` | `P000003, layer=delta`, max_rel 2.0 | `L3.unexplained`; L2 and L1 exact |
| 6 | `wrong_calendar` | the wrap loads one holiday too many (2024-05-28) | `L0.resolved_terms` | `input` | `P000001 effective: 2024-05-29 vs 2024-05-30` (dates first) | the rows downstream follow (20 in all: `L1.pv`, `L1.cash`, ladder buckets ...); snapshot digests and conventions exact |
| 7 | `no_holidays` | the wrap loads NO holidays (a service's default calendar) | the run STOPS: `MarketDataUnavailable` | | `zeta Z530: trades[0]: no SOFR fixing dated 2024-05-27 in the market` | no report: no number is produced from invented data |
| 8 | `fixings_in_percent` | the wrap sends the fixings in percent where zeta wants bp | `L1.pv` | `exceeds` | `P000003` (the seasoned 2Y: its floating leg accrues on fixings 100 times too small), max_rel 5.9e3 | 17 more rows: `cash`, `dv01`, `rate`, carry, roll; the unstarted swaps and L0 exact |
| 9 | `dv01_ladder_sum` | `dv01` is the ladder sum in EVERY state (the annuity nowhere) | `L2.dv01` | `exceeds` | `P000004` (forward-starting, 60bp off the market), max_rel 0.0117 | `L3.tay_delta`, `tay_unexplained`. Exactly 0 on the started swap, 1e-4 .. 2e-3 at par (why the base holds an OFF-PAR unstarted swap) |
| 10 | `reuploading` | the wrap uploads the market again on every request | NONE: the tie-out PASSES (identical numbers) | | only `client.stats["markets_uploaded"]`: 177 uploads against 19 | nothing a tie-out row can see: the reason the service rule is asserted with a counter |

## 9. Traps a service adapter hits (each is asserted by a test or stated in the library's own reference)

1. `client.price` never raises for a service error: forgetting to test `resp["status"]` hands a dict where a number is expected. Check once, in `_compat.price`
   (`test_fact_a_service_error_is_an_error_dict_never_an_exception`, `test_a_bad_field_and_an_unsupported_tenor_are_config_errors_with_zetas_code`).
2. Uploading validates nothing (`zeta_lib/ZETA_DOCS.md` section 1): a malformed market is reported by the FIRST `price()`, not by `upload_market`, so an upload that "worked" proves nothing about the market.
3. `"PAR"` sent together with a `parallel_bp` scenario is re-resolved on the shocked market and prices to 0: send a NUMERIC strike (`test_fact_the_scenario_moves_ZERO_rates...`).
4. zeta wants a fixing for every business day from a swap's start to the day before `as_of`, EVEN AFTER the swap has ended (`test_fact_zeta_wants_a_fixing_for_every_business_day...`): a truncated
   fixings history makes a long-matured swap an error in zeta, where the reference returns 0 without looking. Give the provider the history the oldest swap needs.
5. `RISK_10BP` moves the PAR curve and `parallel_bp` moves ZERO rates: for a spot-start 5Y swap they differ by percents (`test_fact_the_scenario_moves_ZERO_rates...`). dv01 and gamma are par-curve risks
   (`RISK_10BP`/ladder/`CONVEXITY_10BP`); the layers' delta and convexity follow the zero-rate move, so they are priced on worlds built from zero rates.
6. A modified-following start on a holiday is not `spot_lag`: `spot_lag` counts BUSINESS days after the request's `as_of`, an explicit start date is adjusted (`test_fact_spot_lag_and_dates_roll...`).
7. A tenor is measured from the ADJUSTED start and only in whole months or years (`Z204`): re-price a booked trade with its booked START DATE, its strike and its ORIGINAL end
   (`ZetaSwap.trade`), never `resolved.end` for spans that are not whole years.
8. The stack of a run that raises aborts `run_tieout` for every stack: control 7 is asserted with `run_stack`, not with a tie-out.
9. `acme`'s traps that also apply here (the allow-list belongs in the BASE config, `--stack` syntax, a `TypeError` inside a callable is reported as bad arguments) are in the first example's
   README and in `references/common-errors.md` of the skill.

## 10. Honest limits, and what a real service adapter must additionally do

* **A fictional library on the reference arithmetic** (see the caveat above): agreement to round-off is by construction. Real noise floors and any declared tolerance (with a `reason`) come from a real
  service; the wiring, the rules and the tests are what transfer.
* **`_compat.py` has an example-only import fallback.** The documented `PYTHONPATH` has ONE entry for the example (`example-service`), but `zeta` sits one directory deeper (`zeta_lib`); so when
  `import zeta` fails, `_compat` appends `zeta_lib` (next to the adapter) to `sys.path` and retries. A real adapter has no such lines: the platform's client is installed, or its directory
  is on `PYTHONPATH`, and a missing client is the `OptionalDependencyError` right below. Copy the `_compat.py` of a real adapter, not this fallback. (`probes/p1_units_signs.py` imports the library
  itself and needs the extra `zeta_lib` entry when run as a script; the tests add it from their own location.)
* **A simulated service.** In-process, deterministic, nothing sleeps; a real service adds authentication, timeouts, retries, rate limits, batching limits and thread safety, none of which the
  adapter models (one module client, no lock, nothing of zeta's committed). Licence, confidentiality and the service's version belong in the conventions document and beside any result you keep.
* **One product** (USD SOFR OIS), every convention fixed by zeta; forward-start tenors and maturities in whole months or years only (a week or a day is refused before zeta is asked); no bonds.
* **Guards and packaging are NOT done in this example**, because each is an edit to a repository-wide file that a worked example must not make (`pricebt-guards-and-packaging` lists them): the
  library's name in `tests/guards/banned.yaml` (`import_roots`, `executable_tokens`), a pytest partition marker `adapter_<lib>` registered in three files (here the tests are `core`
  because zeta is fictional and needs nothing), a `pyproject` extra (none is right for a client that is not on a package index: say so in the decision record), a tolerance-ledger entry
  (nothing is declared here), change-log rows, a conventions document and an ADR, the fresh-environment release check, `NOTICE`, live tests. `tests/test_skills_example_zeta_no_infra.py` is
  the part that IS here: importing the adapter pulls in no other banned root, and core imports neither zeta nor the adapter.
* **Mutation checks.** The cold-start run mutated 46 single lines of `swap.py`, `wrap.py`, `_compat.py` and `conventions.py` with its own runner, and every mutant was killed after one test was
  sharpened. `tools/mutcheck.py` now does the same job for an adapter outside `src`: it passes the caller's `PYTHONPATH` on to the child pytest (`src` first) and accepts `"runs"`, a list of pytest
  argument lists, one pytest call per entry, so a `-k` on one file does not filter the others.
* **What a real service adapter also needs that this one lacks:** live tests behind a marker, a record-and-replay of real answers (sanitised), and the data horizon of the service's calendars.
