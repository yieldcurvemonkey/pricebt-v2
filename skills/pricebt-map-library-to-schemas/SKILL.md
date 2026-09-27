---
name: pricebt-map-library-to-schemas
description: Use when you have read your pricing library's documentation and must decide, for every name pricebt expects (terms, value, measures, layers, conventions), which library callable provides it and what unit, sign or shape conversion it needs. Produces one mapping row per schema name, kept as sections 3, 5, 6 and 11 of the conventions document, before any adapter code is written.
---

# pricebt-map-library-to-schemas

## Purpose

pricebt names what it needs (`value`, `dv01`, `gamma`, `rate`, `delta_ladder`, four layers, terms, conventions) with a unit, a sign and a return type. Your library has its own names, units and signs.
This skill turns that gap into one row per schema name: which callable, what unit, what sign, whether a binding or code does it, what is missing. The rows are not a separate file: they are sections 3, 5, 6 and 11 of
`docs/design/11-<lib>-conventions.md` (the conventions document `pricebt-discover-the-library` creates at the start), and they are the input of every later skill.

## Prerequisites

- Skill `pricebt-architecture-and-rules` read (schema, binding, snapshot, wrap; dollar delta and ladder contracts). Your library's documentation, code and examples at hand: this skill knows NOTHING about your library and poses questions instead.
- Probing results from skill `pricebt-discover-the-library` if you have them (units, signs, dates, globals, exceptions proven on known answers).
- References: needed are `references/mapping-worksheet.md` (the row template), `references/library-questions.md` (the questions behind section 0 of it) and, at step 8, `references/conformance-snippet.md`. Optional:
  `references/schema-cheatsheet.md` (the full vocabulary tables; the short table of step 2 is enough for a first pass).
- Repository root as working directory, `PYTHONPATH=src;tests` (PowerShell: `$env:PYTHONPATH = "src;tests"`), Python 3.11+ with numpy, pandas, pyyaml, pytest.

## Steps

1. **Paste the rows into the conventions document.** The document `docs/design/11-<lib>-conventions.md` exists (created at the start by `pricebt-discover-the-library`, from `conventions-doc-template.md`, which has the same six
   columns, `gap` included): paste the TABLES of `references/mapping-worksheet.md` into it and fill them. `value`, `rate` and `pv` go to its section 3, `dv01` and `gamma` to section 5, `delta_ladder` to section 6, the four layers to section 11, the
   conventions table to section 10 (D17: the worksheet is the row template, there is no second file to keep; `check_conventions_doc.py` fails the finished document while a schema name has no filled row).
   Answer section 0 of the worksheet (library facts) first, using `references/library-questions.md`: units, sign convention, risk basis, date types, globals, exceptions, shapes, pillar set, attribution primitives, data service.
   Every answer needs a source (a documentation page or a probe with a known answer: a par swap is worth 0; a mirror trade has the opposite sign). `UNKNOWN` is a valid answer until proven.

2. **Print the contract you are mapping to.** The full tables (terms, names, layers, conventions vocabulary, lookups) are in `references/schema-cheatsheet.md`, generated from the resolved schemas and checked against them; the generator at its end
   reprints them after any schema change. The short version (swap; bond differences below):

   | name | returns | unit | sign | required |
   |---|---|---|---|---|
   | `value` | `Valuation(ts, pv, cash, financing)` or float | currency, ONE unit as built | holder: an asset is positive | yes |
   | `dv01` | float | currency per +1bp, ONE unit as built with its notional (a 120mm ten-year payer is about +100,000) | payer positive | yes |
   | `gamma` | float | currency per bp^2, the SAME +1bp move as `dv01` | holder | yes |
   | `rate` | float | PERCENT (4.25, not 0.0425): par rate | | yes |
   | `delta_ladder` | `dict[str, float]`, upper-case tenor keys `<int><D\|W\|M\|Y>`, exactly the bound `keys` | currency per +1bp of that bucket; sums to `dv01` within tolerance | payer positive | yes |
   | `carry` `roll` `delta` `convexity` | float | currency, per unit, over the interval since the previous cadence point | holder | yes (`swap.carry@1` ...) |
   | `pv`, `notional` | | | | DERIVED by pricebt: no binding (a binding is `[CFG-UNKNOWN-BINDING] ... takes no binding`) |

   Bond: `side` is `buy`/`sell`, direction +1 for a buy; `dv01` is NEGATIVE for a long; `rate` is the yield in percent; optional measures `ytm` (percent), `duration` (years), `accrued` (per 100 face); layers `bond.*@1`.
   Terms (swap): `side effective maturity notional fixed_rate extras`; `fixed_rate` is PERCENT or `par`; `notional` unsigned. Terms (bond): `side security notional extras`.

3. **Map each row with the decision tree.** For every name N:

   ```
   Is there a library callable that returns N's quantity, with N's definition?
   |- YES, unit, sign and shape already right ............ bind it directly:   {target: {method: X}, kwargs: {...}}
   |- YES, a CONSTANT differs (decimals, sign, shift) ..... bind + scale / sign / offset
   |- YES, the shape differs (Series, DataFrame, object) .. bind + reduce (a registered reducer) + keys for a ladder
   |- YES, but the factor depends on the trade, needs a library global, several calls, or a Valuation
   |                                                        -> a METHOD on your instrument class (or a `function` target); the binding names it
   |- YES, but it lives on the pricer / a service .......... pricer_method (args: ["@instrument"])
   |- NO callable ........................................... build it from PRIMITIVES (a function over the library's value-as-of, shifted or rolled market, cashflows): layers, ladder, gamma
   `- NO and impossible ..................................... write it in the gap column; do NOT bind a constant or a zero
   ```

   Binding versus code, the full table and the pitfalls: skill `pricebt-bindings-cookbook`. Terms are not bindings: they are handled in the factory (skill `pricebt-instrument-kit`).

4. **Layers.** If the library has no attribution, build the four layers as functions over its primitives, with the ADR 005 definitions (`docs/design/adr/005-attribution.md`): `carry = X_fwd - V0`, `roll = X_roll_act - X_fwd`,
   `delta = (V_base + cash - X_roll_act) + g.dz` (directional derivative along the realised zero-rate move), `convexity = 1/2 dz'H dz`. The worked example does exactly this: `_decomposition` in
   `skills/pricebt-wire-external-library/example/acme_adapter/swap.py`. Bind each with a `function` target (`kwargs: {swap: "@instrument", ctx: "@ctx"}`) and the same `sign` as the measures. The engine computes
   `unexplained` itself. The layer conformance kit will tell you if a definition is wrong. Skill `pricebt-layers-and-ladder`.

5. **Ladder.** Return the library's own shape from a method, declare `keys` (the buckets you report) and a `reduce` that produces upper-case tenor keys. If the library has no bucketed risk, build it by bump-and-revalue per pillar
   (a +1bp move of the bucket's rate, everything else unchanged) and check that the buckets sum to `dv01`. Fix the `dv01` DEFINITION once, in section 5 of the conventions document (and if the library's pillar set is fixed and differs from the reference's, say so there: `pricebt-layers-and-ladder`): the worked example uses the analytic annuity before the swap starts
   and the ladder sum after it, because the tie-out compares `dv01` at a relative 1e-4 and only passes if the definition equals the reference's.

6. **Conventions.** For each key of the vocabulary, write the library setting, the values it supports and, for each value it cannot express, the REASON. The adapter maps the supported ones and raises `CFG-CONVENTION-UNSUPPORTED`
   (naming the reason and the supported set) for the rest; never a default, never a silent substitute. Model: `skills/pricebt-wire-external-library/example/acme_adapter/conventions.py`.
   The `calendar` convention is a NAME of a calendar in the snapshot, not a library calendar: the provider owns the holidays.

7. **Extension names.** A measure or layer your library offers beyond the schema (`<lib>_zero_dv01`): bind it like any other and declare it: `Kit(..., extra={"<lib>_zero_dv01": "measure"})`. Use an identifier prefixed with the library's
   short name (`dv01_zero`, `acme_zero_dv01` are the shipped ones). It must not collide with a schema or engine name. The CLI audits `dv01`, `gamma`, `rate` only; a tie-out compares an extension only if you list it in
   `run_tieout(..., audit_measures=(...))`, and then EVERY stack must bind it, the reference included: listing it while any stack lacks it stops the run at load with
   `[CFG-REF] backtest.audit.measures: ... measures ['acme_zero_dv01'] not defined by any instrument (known: [...])` and no report is written. To compare an extension, bind it in every stack (the reference stack binds `dv01_zero`
   and `gamma_zero`, as do both real adapters); otherwise leave it out of `audit_measures`: it is then simply not compared.

8. **Prove the mapping**, in two stages, using `references/conformance-snippet.md`.
   NOW (phase 3, before any adapter code): a row is MAPPED when it has a documented source (a page, or a probe on a known answer). Run the two scripts of the snippet as printed to see what correct looks like: they use the reference
   stack and need no code of yours.
   LATER (after phases 5-6 of `pricebt-wire-external-library` have produced your `wrap` and your Kit, factory and class): `check_default_block` builds the spec from your Kit's DEFAULT block alone and calls every binding at a real mark,
   then the three known answers on YOUR kit (a par swap is worth 0 and its resolved rate equals its `rate`; a payer above par has negative pv and positive dv01; the mirror trade is the exact opposite). A row is DONE when that passes.
   Then the layer conformance kit and the tie-out against the reference stack: skill `pricebt-conformance-and-tieout`.

9. **Freeze the gap column.** Every row that says `gap` becomes (a) a documented definition difference, (b) a refused convention, or (c) a tolerance declaration with a `reason` (`tasks/tolerance_ledger.yaml`). Nothing is widened silently.

## Checks

```powershell
$env:PYTHONPATH = "src;tests"
python -c "from pricebt.contracts.schema import SchemaRegistry as R; s = R.default(); print(sorted(s.get('swap').measures), sorted(s.get('bond').measures))"   # the names to map
python <your check_default_block script>          # phase 6 (needs your Kit and wrap): one line of floats per asset class (references/conformance-snippet.md prints the shape); no exception
```

Before your Kit exists, the same scripts of `references/conformance-snippet.md` run as printed against the reference stack. Expected for the reference stack (the known-answer run of that file): swap `dv01` about 4504 and `rate` about 4.03 for a 10mm 5Y payer struck at 4.2 on the flat test world, the long bond's `dv01` negative;
a par swap prints `pv 0.0` and `resolved fixed_rate == rate: True`; `mirror sum: 0.0 0.0`. For the worked example the same script runs with `allow=("pricebt", "acme_adapter")` and the example directory on `PYTHONPATH`.
A units or sign error in a row shows up here as a wrong sign or a factor of 100 or 1e6; only the tie-out sees a wrong definition.

## Common failures

| message | cause | fix |
|---|---|---|
| `[CFG-REQUIRED-BINDING] ... required names of asset class 'swap' are not bound: ['gamma'] (gamma: did you mean [...]?)` | a required row of the mapping has no callable | build it from primitives; never bind a placeholder that returns a constant |
| `[CFG-TERMS-UNRESOLVED] term 'fixed_rate' came back unresolved ('par'): the library must return a concrete number (T4)`; `term 'maturity' came back unresolved ('10Y'): the library must return a concrete date (T4)`; `term 'effective' came back unresolved ('spot')` | the factory did not resolve a token or a tenor | resolve `par`, `effective`, `maturity` at the fill pricer; return `dt.date` and a percent number |
| `[CFG-TERMS] asset class 'swap': the factory changed term 'notional' from 10000000.0 to 20000000.0; only relative dates and tokens are resolved` | the factory returned another `side` or another size | return the given `side` and `notional`; direction is `side`, never a signed notional |
| `[CFG-TERMS] asset class 'swap': unknown terms ['strike']. Terms: side(enum, required); ...` | a term the schema does not have | per-trade extras go under `extras` (standard key `par_spread_bp`); the factory refuses unknown extras |
| `[CFG-CONVENTION] unknown convention keys ['daycount'] ... (daycount: did you mean 'day_count'?)`; `convention 'day_count' must be one of ['act360', ...], got 'act/360'` | the shared block does not follow the schema vocabulary | fix the block (the vocabulary is the tables of `references/schema-cheatsheet.md`) |
| `[CFG-CONVENTION-UNSUPPORTED] convention day_count='thirty360' is in the vocabulary but this adapter does not support it: <reason>; supported: ['act360', 'act365f']` | your library cannot express a value; this is the CORRECT refusal | keep it; it is itself a tie-out finding |
| `[CFG-UNKNOWN-BINDING] ... binding 'zero_dv01' is not a name of asset class 'swap'; bindable: [...]` | an extension name in `default_bind` but not in `Kit.extra` | `extra={"<name>": "measure"}` |
| `MeasureError measure 'delta_ladder' must return exactly the bound tenors ['2Y', '10Y']: missing ['10Y']; extra []` | `keys` and the reducer's result differ | make the reducer return exactly the bound tenors |
| tie-out `L2.rate exceeds` with relative difference about 0.99; `L2.dv01 exceeds` with relative difference 2.0 | a missing `scale: 100` (decimals); a missing `sign: -1` (receiver's point of view). Both are the worked example's negative controls | fix the row; see `pricebt-debug-tieout-differences` |
| `check_resolved` accepts `'2024-06-14'` as a resolved date without complaint | `AssetSchema.check_resolved` validates a date string of ten characters as a date; the check does not force `dt.date` | convert your library's date strings to `dt.date` in the factory (the reference stack returns `dt.date`) |

Pitfalls found while building the worked example (facts with symbols):

- The shipped ladder floor hides small buckets: `tieout.tolerances.DEFAULT_TOLERANCES["L2.delta_ladder"]` is relative 1e-3 with an ABSOLUTE floor of 100 per bucket per unit, so a bucket error below 0.1 currency per unit (1e-3 x 100) is invisible: also compare the ladder directly in a unit test.
  A ladder tie-out has power only in the large buckets: also compare your ladder with the reference's directly, bucket by bucket, as `tests/test_skills_example_acme.py` does.
- The did-you-mean hint for an unbound `dv01` can suggest `delta` (a synonym of `dv01` in `swap.yaml` that is also the name of a layer). Read a hint, do not follow it.
- Extension names are identifiers: the collision message of `spec.build_spec` advises "namespaced (for example 'lib.name')", but `tieout.compare` splits an L2 quantity on `.` and `tieout.tolerances._check_key` parses dotted keys, so a dotted name is unproven.
- An extension measure that only YOUR stack binds cannot be compared: listing it in `audit_measures` aborts the run at load with `[CFG-REF] backtest.audit.measures ... not defined by any instrument`; not listing it leaves it out of the report.
  Separately, one failing stack aborts `run_tieout` for all stacks unless the base sets `backtest.on_error: record`.
- The tie-out report hides WHY a measure failed. A measure that raises becomes a NaN column and the row says `the measure column exists in reference only: it is missing or broken in the other run (<n> values are NaN on one side only, n = the marks of that run)`;
  the real `MeasureError` (for example `measure 'delta_ladder': key '30y' is not a canonical tenor ...`) is only in `run_tieout(...).results["<stack>"].errors` (a DataFrame with `ts`, `where`, `position`, `error`). Read it there, or reproduce with `check_default_block`.
- The CLI cannot compare the ladder; only `run_tieout(..., audit_measures=("dv01", "gamma", "rate", "delta_ladder"))` in Python can ([CE-LADDER-CLI](../pricebt-wire-external-library/references/common-errors.md#ce-ladder-cli)).
- A calendar row done wrong does not always look like a difference: a `wrap` that loads NO holidays stops the run with `MarketDataUnavailable: ... overnight fixings missing between ...` (the library demands a fixing on a market holiday); only a calendar with a holiday TOO MANY gives a comparable report (`L0.resolved_terms` `input`). Skill `pricebt-wrap-and-pricer`.

## Related skills

- `pricebt-architecture-and-rules`: the contracts (dollar delta, ladder) and the rules a mapping must respect.
- `pricebt-discover-the-library`: probing the library's units, signs, dates, globals and exceptions on known answers, and recording them like `docs/design/11-quantlib-conventions.md`.
- `pricebt-bindings-cookbook`: the binding grammar and the binding-versus-code table used in step 3.
- `pricebt-instrument-kit`: the factory (terms, resolved terms), the conventions mapping and the `Kit` that carry rows 1.1 and 1.4 of the worksheet.
- `pricebt-layers-and-ladder`: building the layers and the ladder when the library has none.
- `pricebt-market-data-snapshots`, `pricebt-wrap-and-pricer`: the market side of the mapping (curves as discount factors, calendars, fixings).
- `pricebt-conformance-and-tieout`, `pricebt-debug-tieout-differences`: proving the mapping and locating a wrong row from a failing quantity.
- `pricebt-wire-external-library`: the master playbook, with the tested worked example.
