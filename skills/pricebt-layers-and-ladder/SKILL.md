---
name: pricebt-layers-and-ladder
description: Use when you must implement or debug dollar delta (dv01, gamma, rate), the delta ladder, or the carry/roll/delta/convexity layers of a pricebt adapter, or when a tie-out or the conformance kit shows L2/L3 differences. Gives the exact folded definitions, how to assemble them from a library's primitives, the golden case as a control, and the pitfalls.
---

# Dollar delta, the delta ladder and the four layers

## Purpose

Your library will not hand you pricebt's risk numbers or its P&L attribution; you build them from its primitives, in pricebt's units and signs, and prove them on a known answer.
This skill fixes the contracts (`dv01`, `gamma`, `rate`, `delta_ladder`, `carry`, `roll`, `delta`, `convexity`), shows how to assemble each from what a library offers, and gives the control
(the golden case) that says whether you got them right.

## Prerequisites

* `pricebt-map-library-to-schemas` done (you know which library call answers each schema name, with its unit and sign), `pricebt-wrap-and-pricer` (a `wrap` that gives you a market object per snapshot) and
  `pricebt-instrument-kit` (a loadable Kit: the checks below build a spec from it). The four layers are REQUIRED bindings: a Kit that leaves them unbound does not load (`ConfigError [CFG-REQUIRED-BINDING]
  instruments.x.bind: instrument 'x': required names of asset class 'swap' are not bound: ['carry', 'convexity', 'delta', 'roll']`), so bind all four, even from a first draft, before running any check of this skill.
  Read `src/pricebt/contracts/schemas/swap.yaml` (`measures:`, `layers:`) and `docs/design/adr/005-attribution.md`.
* Run everything from the repository root with `PYTHONPATH` = `src;tests` (Windows separator `;`, `:` on Linux) and, for the worked example, `;skills/pricebt-wire-external-library/example`. Commands are in bash form,
  `PYTHONPATH="src;tests" python x.py`; in PowerShell set it first: `$env:PYTHONPATH = "src;tests"; python x.py`.
* Models to read: `src/pricebt/contrib/quantlib/swap.py` (`QLSwap`), `skills/pricebt-wire-external-library/example/acme_adapter/swap.py` (`AcmeSwap`, `_decomposition`) and the library-free
  `src/pricebt/testing/refstack.py` (`RefSwap`). For a SERVICE that prices only from a market you send: `skills/pricebt-wire-external-library/example-service/zeta_adapter/swap.py` (layers as derived markets).
* References: needed are `references/dv01-one-definition.md` and `references/golden-case-control.md` (they hold the check scripts of steps 2 and 8), `references/layer-definitions-and-assembly.md`
  sections 1 to 3 and `references/kit-blind-spots.md` (the other two check scripts). Optional: `references/ladder-contract-and-hedge.md` sections 4 and 5 (only when a strategy sizes hedges from the ladder).

## Steps

1. **Fix the contract of the three risk numbers** (all per ONE unit as built, with its notional, holder-signed; `direction` +1 = payer of fixed, `side: pay`):

   | name | unit | sign | note |
   |---|---|---|---|
   | `dv01` | currency per +1bp of the swap's own market rate | payer positive (a 120mm 10Y payer is about +100,000) | NOT per unit notional, NOT per million |
   | `gamma` | currency per bp^2, second derivative in the SAME variable as `dv01` | holder (flips with the direction, like the value) | |
   | `rate` | PERCENT (4.0 means 4%) | none | the par rate of the REMAINING swap; the shipped stacks return NaN after maturity, and the engine then records `MeasureError: measure 'rate' is not finite (nan)` once for the baseline of that position (which is then switched off) and once per audited mark when `rate` is audited; both stacks must behave the same for a tie-out to agree |

   A library that reports decimals binds `rate` with `scale: 100`; one that reports the receiver's view puts `sign: -1` on `dv01`/`gamma`; one whose risk is per million multiplies by
   `notional / 1e6` IN CODE (`scale` is a constant, it cannot read the trade). Write these as methods of your instrument class taking `ctx` (`AcmeSwap._per_unit` is the pattern).

2. **Give `dv01` ONE definition, in every state of the swap and in every stack** (`references/dv01-one-definition.md`, with the story from `tasks/suite_reproduction.md` section 3):
   matured -> `0.0`; started (`effective < reference_date`) -> the SUM OF THE DELTA LADDER over the bound `tenors`; not started -> the analytic annuity, which is what the reference stack answers (it is the market dv01
   only for a swap struck at par: off par it differs from the ladder sum by percents, so a ladder sum there needs a declared tolerance sized on such trades). Never the annuity after the start: it keeps the accrued
   coupon and overstates the risk (4 percent a month in, 100 percent a year in on a 2Y). Pass ONE `tenors` list to the `dv01`, `gamma` and `delta_ladder` bindings. Check with the script of that file.
   **Bind the ladder sum for a started swap even when your library offers its own parallel dv01**: a +-1bp parallel difference and a sum of +-0.5bp per-pillar differences differ by about 2e-7 relative, `L2.dv01`
   calls that `noise`, but the baseline's `tay_unexplained` is a small residual, so the same absolute error is about 1e-3 of it (measured 1.17e-3 against the shipped 1e-3: `L3.tay_unexplained` exceeds alone).
   If your library's pillar set is fixed and differs from the reference's, see "Your library's pillar set is fixed and differs from the reference's" below.

3. **Build the ladder** (`references/ladder-contract-and-hedge.md`): a method returns the library's own shape; a REDUCER turns it into `dict[str, float]` with UPPER-CASE tenor keys
   (`register_reducer("<lib>_ladder_to_tenor_dict", fn)`, wrapping `REDUCERS["series_to_tenor_dict"]`); the binding declares `keys` and `tenors`:

   ```python
   # fragment
   "delta_ladder": {**_method("delta_ladder", tenors=list(DEFAULT_TENORS)), "keys": list(DEFAULT_TENORS), "reduce": "acme_ladder_to_tenor_dict", "sign": RECEIVER_TO_PAYER},
   ```
   (`skills/pricebt-wire-external-library/example/acme_adapter/swap.py`, `SWAP_BIND`). A bucket the swap does not touch is `0.0`, not absent; an extra bucket of the library that carries risk must be REFUSED, not dropped.

4. **Assemble the four layers from the library's primitives** (`references/layer-definitions-and-assembly.md`). With `V(curve, date, fixings)` the position's value in the world of a curve
   anchored at `date`, and `cash` the flows paid in `[t0, t1)`:

   ```
   carry     = X_fwd - V0                                   X_fwd: the t0 world's flows valued at t1 (market unchanged in DATE space)
   roll      = X_roll - X_fwd                               X_roll: the market rolled in TENOR space, realised t1 fixings, + cash
   delta     = (V_base + cash - X_roll) + g.dz              V_base: the rolled curve resampled on the t1 nodes; g.dz along the realised zero-rate move dz
   convexity = 1/2 dz' H dz                                 the second directional derivative along the same dz
   unexplained (the ENGINE's) = (V1 - V_base) - g.dz - 1/2 dz'H dz
   ```
   The six library calls: t0 value and forward value; t1 value and cash; the rolled market; the resampled base and `dz`; two (or four) shocked valuations; assembly and direction. If the library
   lacks a call, section 4 of the reference gives the fallback. Return ONE dict from a cached `decomposition(ctx)` (`ctx.cache[(name, id(self))]`) and make the four layers thin readers of it.
   `cash` is priced in the t1 world, and once `t1 - t0` exceeds the payment lag a flow paid before t1 can depend on a fixing published inside the interval: a library that answers "flows paid up to t1" from
   the OLD market implies that fixing from the old curve, so rebuild the old market with the t1 fixings (`references/layer-definitions-and-assembly.md` section 3; check: step 9). A service whose scenarios cannot
   express the rolled, resampled and shocked worlds needs derived markets: the upload accounting is `pricebt-enterprise-platform-patterns` step 3.

5. **Choose the derivative recipe and say so.** `g.dz = (V(+h) - V(-h)) / (2h)` and `1/2 dz'H dz = (V(+h) + V(-h) - 2 V_base) / (2h^2)` with `h = 0.1` (two shocked valuations, what the
   QuantLib adapter does; its remainder is then `1 - h^2` = 0.99 times the true one, a DECLARED tolerance), or the same at `h` and `h/2` with Richardson `(4 d_b - d_a) / 3` (the reference stack and the
   example; exact to round-off). Do not shrink `h` below 0.05: round-off wins (table in the reference).

6. **Bind the layers** with the direction handled once: `{target: {method: carry}, kwargs: {ctx: "@ctx"}}` (methods), or `{target: {function: "<pkg>.swap:carry"}, kwargs: {swap: "@instrument", ctx: "@ctx"}, sign: -1}`
   (functions; the base config must list `<pkg>` under `registry.allow`). A receiver's-view library needs `sign: -1` on all four. Layers need `ctx.prev_pricer`; the engine passes it from the second cadence
   point on. Layer names `total, transactions, cash_interest, unexplained, financing, tay_*` are reserved (`RESERVED_LAYERS`).

7. **Know what the engine adds.** `unexplained` (per position and flush: interval P&L minus your layers) and, with `backtest.attribution: {baseline: true}`, the library-independent baseline
   `tay_delta = dv01(start) x d rate_bp`, `tay_convexity = 1/2 gamma(start) x d rate_bp^2`, `tay_unexplained` (`Engine._baseline_rows`; the bp size comes from the unit of `rate`). `reconcile()` checks your layers
   and the baseline separately. You never compute either. `attribution: {strict: true, tol: 1e-6}` turns a large `unexplained` into an `EngineInvariantError`.

8. **Run the golden case through your BOUND layers** (`references/golden-case-control.md`): the seasoned 3-year payer of `docs/DESIGN.md` section 7, from `tests/data_golden_swap.json`.
   Golden numbers: `V0` -369,827.43, `V1` -212,114.86, `cash` -86,490.36, `carry` -561.82, `roll` 1,532.07 (raw 12,159.98), `delta` 70,302.17 (raw 70,190.64; ADR 005 says 70,302.17, unrounded 70302.166, and the older tests
   assert 70,302.16 within 0.01 to 0.02, so the script's tolerance is 0.011), `convexity` -50.23, remainder 0.02, total 71,222.20.
   "Raw" and "folded" differ by the realised fixings (in `roll`) and the resampling (in `delta`); ADR 005 fixes the FOLDED one.

9. **Run the layer conformance kit** (`pricebt-conformance-and-tieout`): a parallel shock at one date equals `delta + convexity` by full revaluation (and `carry`, `roll` are zero); a payer and its mirror have
   equal and opposite value, dv01, gamma and layers; across a payment date the value drop equals the cash booked; the unexplained share is bounded. Know its blind spots (measured on the example, `references/kit-blind-spots.md`
   has the full table): a `delta` layer without its sign fails four `fd_vs_delta_convexity/shock` rows and `unexplained_share (moved market)`; `convexity` fails two `+-25bp` rows; `carry` fails only
   `unexplained_share (static market)`; `roll`, `dv01`, `gamma` and `delta_ladder` without their sign fail NOTHING (the mirror check is antisymmetric, a uniform sign error cancels). Which local check covers what:
   the golden case (step 8) the four layers; the dv01 check (step 2) `dv01` and the ladder; `risk_known_answers.py` (`references/kit-blind-spots.md`) `dv01` and `gamma`, the only check before the tie-out
   that sees a `gamma` of the wrong sign or scale. Also run the kit over a COARSE step (`t1 = t0 + 9 days` with `payment_window`), and pin `cash` with `cash_window_check.py` of the same file: the kit's
   flat world publishes the fixings the old curve implies, so it cannot tell an old-market `cash` from the right one; that script can (section "The coarse step and the cash window" of the file).

10. **Prove the ladder is consumed correctly**: tie out with the ladder audited (`run_tieout(..., audit_measures=("dv01", "gamma", "rate", "delta_ladder"))`, the CLI cannot) and run the ladder-hedge
    action through your stack overlay (`HedgeAction`, `ladder_hedge_quantities`): the trades must equal the reference stack's (`references/ladder-contract-and-hedge.md` sections 4 and 5).

## Your library's pillar set is fixed and differs from the reference's

Your library reports par risk on ITS pillars and does not let you choose them (the example service: 1M 3M 6M 1Y 2Y 3Y 5Y 7Y 10Y 15Y 20Y 30Y; the reference's default is `DEFAULT_TENORS` of `src/pricebt/testing/refstack.py`:
eleven, no 1M). Two risk numbers on two pillar sets are two different measures, so the tie-out differs by construction, not by a wiring mistake. Measured on the example service, ONE market (the base config's synthetic market at 2024-05-24; 20mm payers struck 3.9; the reference on its default eleven
against the same reference on the library's twelve, which equals the library's own dv01 to round-off; `example-service/probes/p9_pillar_gap.py` prints every figure of this section): a seasoned 2Y payer 1.05e-4 relative (the shipped `L2.dv01`
tolerance is 1e-4, so the CLI fails); a swap with 6 months left 1.8e-4, with 1 month left 6.6e-3, with 2 weeks left 6.6e-3 (0.66 percent); with the ladder audited (the CLI cannot) the 1M bucket is `structure` (the reference has none) and
3M `exceeds` (`max_rel` 0.32, `max_abs` 31.6); `L3.tay_delta` and `L3.tay_unexplained` follow. Another market gives other digits (seed 11: 1.06e-4, 6.7e-3), the same picture.

1. **Keep the library's native buckets**: `keys` and `tenors` of your three bindings are the library's list, verbatim. Do not fold the extra bucket into a neighbour (measured on the seasoned 2Y over the base's 19 grid days: the largest bucket
   difference from the reference's is 0.07 to 0.21 currency once 1M is folded into 3M, 0.21 on the first day, above the ladder's absolute floor of 0.1) and do not drop it (11 to 32 currency, 31.6 on the first day, and `sum(delta_ladder) == dv01` breaks).
2. **Align the REFERENCE, not yours**, through a SECOND overlay of the reference stack that changes only the `tenors` kwarg (and the ladder `keys`) of its `dv01`, `gamma` and `delta_ladder` bindings; the reference
   accepts any distinct, increasing list. Copy of `skills/pricebt-wire-external-library/example-service/zeta_adapter/config/refstack_zeta_pillars.yaml`, with your list:

   ```yaml
   # SECOND overlay for the REFERENCE stack: use it after the shipped one. It changes NOTHING of the reference's arithmetic: it only passes the library's pillar list to dv01, gamma and delta_ladder.
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
3. **Tie out with both overlays** (the comma is `tieout` only; `validate` and `run` take a repeated `--stack`: [CE-STACK-SYNTAX](../pricebt-wire-external-library/references/common-errors.md#ce-stack-syntax)). Executed on the example service, PowerShell:

   ```powershell
   $ex = "skills/pricebt-wire-external-library/example-service"
   $env:PYTHONPATH = "src;tests;$ex;$ex/zeta_lib"
   python -m pricebt tieout $ex/zeta_adapter/config/zeta_tieout_base.yaml --stack "reference=configs/adapters/refstack_swap.yaml,$ex/zeta_adapter/config/refstack_zeta_pillars.yaml" --stack zeta=$ex/zeta_adapter/config/zeta_swap.yaml --reference reference
   # exit 0, TIE-OUT PASSED; with only the shipped reference overlay: exit 1, TIE-OUT FAILED: L2.dv01, L3.tay_delta, L3.tay_unexplained (and their /unit twins)
   ```
4. **Keep a test that pins the gap**: tie out against the DEFAULT reference and assert exactly which rows move (`L2.dv01` at `1e-4 < max_rel < 3e-4` on the seasoned position, and what reads it) and that L0, `pv`, `cash`,
   `rate` and the four layers stay `exact` or `noise` (model: `tests/test_skills_example_zeta_proofs.py`).
5. **Do not declare it as a tolerance**: nothing is wrong, two measures differ. Write the pillar list and the sizes above into the conventions document (sections 5 and 6) and leave `tieout.tolerances` empty.

## Checks

| what | command (repository root) | expected |
|---|---|---|
| golden case | the block of `references/golden-case-control.md`, saved as `golden_control.py`: `python golden_control.py` | nine `ok` rows, exit 0; a layer bound without its sign or a wrong `scale` prints `BAD` rows |
| dv01 definition | the block of `references/dv01-one-definition.md`, saved as `dv01_consistency.py` | every row `ok`, exit 0 (started: dv01 equals the ladder sum to 1e-12; every row equals the reference stack's dv01 to 1e-4; unstarted `fixed 3.0` rows show a percent-level `gap to ladder`) |
| dv01 and gamma, sign and scale | the block of `references/kit-blind-spots.md`, saved as `risk_known_answers.py` | two `ok` rows (dv01 ratio 0.9 to 1.1, gamma 0.5 to 2.0 against full revaluation), exit 0 |
| the kit over a coarse step; `cash` across it | the two python blocks of `references/kit-blind-spots.md`, section "The coarse step and the cash window": the first saved as `coarse_step_kit.py`, the second as `cash_window_check.py` | two `ok` rows each (fine and coarse step), exit 0; only the second fails a `cash` read from the old market (`BAD` on its coarse row) |
| the kit | `report = run_kit(Setup(...)); report.assert_ok()` (snippet in `skills/pricebt-wire-external-library/example/README.md` section 6) | no `AssertionError`; `unexplained_share` printed and small |
| the kit has teeth (for the layers) | `kit_mutants.py` of `references/kit-blind-spots.md` (the Kit rebuilt with one default binding minus its `sign`) | `delta` unsigned: five failed rows (four `fd_vs_delta_convexity/shock ...` and the moved-market share); `dv01`, `gamma`, `roll`, `delta_ladder` unsigned: NO failed row |
| tie-out incl. ladder | `run_tieout(base, {"reference": [...], "<lib>": [...]}, audit_measures=("dv01","gamma","rate","delta_ladder"))` | `passed` true; every `L2.delta_ladder.<bucket>` `exact` or `noise`, none `structure` |
| hedge | the YAML of `references/ladder-contract-and-hedge.md` section 5 under your overlay | same trades as the reference stack, `errors 0`, `reconcile().ok` |

## Common failures

* `MeasureError: measure 'delta_ladder': key '3m' is not a canonical tenor (upper-case <int><D|W|M|Y>, e.g. '3M', '10Y')`: your reducer kept the library's labels. Wrap `REDUCERS["series_to_tenor_dict"]`.
* `MeasureError: measure 'delta_ladder' must return exactly the bound tenors ['3M', ...]: missing ['30Y']; extra []`: the reducer dropped or added a bucket; `keys` and `tenors` must be the same list.
* `MeasureError: measure 'delta_ladder' must return a dict[str, float] keyed by tenor, got Series (bind a reducer such as series_to_tenor_dict)`: no `reduce:` on a library that returns a Series.
* `ConfigError: [LAYER] layers need a previous pricer (ctx.prev_pricer)`: a layer was evaluated at the first point (no previous pricer). Only `Engine._flush_layers` calls layers, from the second point on;
  in your own tests build the context with `prev=` (see `golden_control.py`).
* `ConfigError: [CFG-ALLOW] bind.carry.target.function: ... not under an allowed prefix ['pricebt']`: function-target layers need `registry.allow` in the BASE config and the Python facade cannot load such a Kit at
  all ([CE-ALLOW](../pricebt-wire-external-library/references/common-errors.md#ce-allow), [CE-FACADE](../pricebt-wire-external-library/references/common-errors.md#ce-facade)); method-only default bindings avoid both.
* `[CFG-REDUCER] reducer 'acme_ladder_to_tenor_dict' is already registered`: the module was reloaded (`importlib.reload`); the same name for a different function object is refused. Do not reload adapters.
* An extension measure for the OTHER risk variable (`Kit(..., extra={"<lib>_zero_dv01": "measure"})`): the load error `kit extension 'carry' collides with a reserved row or a name of asset class 'swap'; extension names must
  be namespaced (for example 'lib.name')` (`CFG-UNKNOWN-BINDING`) fires ONLY on a collision with a reserved row or a schema name (executed with `extra={"carry": "measure"}`); `acme_zero_dv01` loads. The message
  suggests a dot, but the shipped names carry none (`dv01_zero`, `acme_zero_dv01`) and `tieout.tolerances` keys want an identifier (`_NAME` in `src/pricebt/tieout/tolerances.py`): use a library-prefixed IDENTIFIER.
  An extension listed in `audit_measures` while any stack lacks it stops the run at load with `[CFG-REF]`: leave it out of the audit and check it in a test.
* `AttributeError: 'Kit' object has no attribute 'SWAP_BIND'` (or `... 'ladder_to_tenor_dict'`): `import <pkg>.swap as m` yields the KIT, not the module, because the package exports the Kit under the module's name.
  Use `importlib.import_module("<pkg>.swap")`, `from <pkg>.swap import SWAP_BIND` or `sys.modules["<pkg>.swap"]` in tests and reducers (executed on the worked example).
* `MethodCallError: binding 'value': method 'value' called with args=[] kwargs=['ctx']: ...`: a `TypeError` inside your callable, worded like a signature mismatch; read the tail ([CE-TYPEERROR](../pricebt-wire-external-library/references/common-errors.md#ce-typeerror)).
* The kit fails `fd_vs_delta_convexity/shock -25bp: 115442.851 vs expected -116857.9099 (tolerance 0.002)` (numbers of the executed run with `delta` unsigned): a layer without its sign. Add `sign: -1` to every
  binding of a receiver's-view library. A wrong sign on `dv01` or `gamma` is invisible to the kit and to the golden case: `dv01` is seen by the dv01 check and `risk_known_answers.py` (and by the tie-out: `L2.dv01`
  exceeds by a relative 2.0), `gamma` ONLY by `risk_known_answers.py` and the tie-out (`L2.gamma`, `L3.tay_convexity`, `L3.tay_unexplained`).
* Layers agree with the golden case but `L3.unexplained` differs by about 1 percent from the reference: the step `h` (step 5). Declare `L3.unexplained` with a `reason` and a ledger line
  (`tasks/tolerance_ledger.yaml`, `swap_book`); the mechanism is verified, the ratio is `1 - h^2`.
* `L2.dv01` exceeds and `L3.tay_delta`, `L3.tay_unexplained` follow while pv, cash and the layers are exact: `dv01` has another definition or sign than the reference (steps 1 and 2). If `L2.rate` exceeds
  by a factor of 100 and `tay_*` follow: `rate` is not in percent.
* ONLY `L3.tay_unexplained` (and its `/unit` twin) exceeds, about 1e-3 relative, while `L2.dv01` is `noise` (about 2e-7) and `L3.tay_delta` too: a started swap's `dv01` is the library's parallel risk, not the ladder sum
  (step 2; executed on the example service: 1.87e-7 and 1.174e-3). Bind the ladder sum.
* `L2.dv01` exceeds by about 1e-4 on the SEASONED positions only (with the ladder audited: `L2.delta_ladder.<first bucket>` `structure`, the next `exceeds`), `pv`, `cash`, `rate` and the layers exact: two pillar sets, not
  a wiring error (section "Your library's pillar set is fixed and differs from the reference's").
* A ladder or dv01 row that says `NaN on both sides: nothing was compared (the measure is not bound for these instruments, or was never computed)` or `the measure column exists in <stack> only: it is missing or
  broken in the other run (...)`: the audited measure RAISED on one side, the engine turned it into a NaN cell, and the real `MeasureError` is only in `result.errors` (`where: audit`). Read `run_tieout(...).results[<stack>].errors`.
* A ladder tie-out that never fails: the floor of `L2.delta_ladder` (rel 1e-3, floor 100 per bucket per unit) hides any bucket error below 0.1 currency per unit; on the example base a +2 percent error in a
  2.3-currency bucket is `noise`, +5 percent is `exceeds`, +2 percent in the 2,250 bucket is `exceeds`. The CLI does not compare the ladder at all ([CE-LADDER-CLI](../pricebt-wire-external-library/references/common-errors.md#ce-ladder-cli)).
* The ladder-hedge sizes wrongly with no error: `ladder_hedge_quantities` trusts your buckets. `SizingError: target buckets ['10y'] are touched by no hedge leg and no position (a typo? ...)` is the only guard
  (a lower-case key). A hedge set that does not span the buckets an aged book drifts into leaves a residual (`docs/DESIGN.md` section 15).
* A flow paid ON the reference date belongs to `V(D)`, not to `cash` (the window is `[t0, t1)`); the kit's cash-sweep check needs `Setup.payment_window` around a payment date to see it.
* `cash` differs from the reference's by the accrual of a few days of a fixing (1.1e3 on a 20mm payer) only when `t1 - t0` exceeds the payment lag, and every daily tie-out and the kit pass: the library answered the
  cash of the interval from the OLD market, which implies the fixings published inside it; rebuild the old market with the t1 fixings (`references/layer-definitions-and-assembly.md` section 3, `cash_window_check.py`).
* A finite-difference step that is too small: `h = 0.01` gave a convexity off by 3.9e-5 against 3.3e-7 at `h = 0.1` on the golden case (round-off in the second difference).

## Related skills

`pricebt-map-library-to-schemas` (which call answers which name), `pricebt-bindings-cookbook` (`sign`, `scale`, `reduce`, `keys`), `pricebt-wrap-and-pricer` (worlds from snapshots, global state),
`pricebt-instrument-kit` (Kit, `registry.allow`, the facade limit), `pricebt-conformance-and-tieout` (the kit and the tie-out, declaring tolerances), `pricebt-debug-tieout-differences` (level by level),
`pricebt-enterprise-platform-patterns` (the cost of a layer flush on a remote platform), `pricebt-wire-external-library` (the playbook and the worked example).
