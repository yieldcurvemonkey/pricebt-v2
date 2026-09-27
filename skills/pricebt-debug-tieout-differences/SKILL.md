---
name: pricebt-debug-tieout-differences
description: Triage playbook that turns a failing pricebt tie-out report into a cause, a fix and a regression test. Reads L0 to L4 top down, takes the ratio, looks up symptom to cause, localises to one position and one day and bisects with one rebinding. Use when a tie-out shows exceeds, input or structure rows, or a stack raises.
---

# pricebt-debug-tieout-differences

## Purpose

A tie-out row says WHERE two libraries differ (a level, a quantity, a position, a timestamp); this skill finds WHY. The method: the first failing level is the lead, the ratio names the family of causes, one hypothesis is tested with one change,
and the cause is pinned by a test that fails without the fix. The cause is always fixed in the adapter (wrap, conventions, factory, bindings), never by widening a tolerance and never in core.

## Prerequisites

* A tie-out that runs (`pricebt-conformance-and-tieout`): the self-test passes, the base is shared, and you use the Python API with `audit_measures=("dv01", "gamma", "rate", "delta_ladder")` so that `result.record.audit` and the ladder are available.
* Repository root as working directory; `PYTHONPATH=src;tests;<your adapter's parent directory>`; `python` = the interpreter with pricebt's dependencies.
* The reference stack (`configs/adapters/refstack_swap.yaml`) as the known answer, and `<LIB>`'s documentation for the questions in `references/symptom-table.md`, section 6. Nothing in this skill states how `<LIB>` behaves: every convention is probed on a known case.
* References: needed are `references/symptom-table.md` (the lookup), `references/localise-and-bisect.md` (sections 1 to 4) and `references/regression-test-template.md` (step 9). Optional: `references/worked-examples.md` (two solved cases, a model, not a step).

## Steps

1. **Make the failure reproducible and complete.** Run with `--out <dir>` (or `run_tieout(..., out=...)`) so the markdown, `.summary.parquet` and `.offenders.parquet` are kept. If a stack RAISES, the whole tie-out aborts with no report
   (`error: [MarketDataUnavailable] ...`). For a `PricebtError` raised while marking or executing (`MarketDataUnavailable`, `MethodCallError`, `MeasureError`) add `--set backtest.on_error=record` to get a report: the row is recorded and the run goes on.
   `record` does NOT help for a raw library exception (`error: [RuntimeError] library blew up`, exit 2 under `raise` and `record` alike: translate it in the adapter, `acme_adapter/_compat.py: translate`), nor for an error raised while the config is loaded or built
   (`[CFG-ALLOW]`, `[CFG-IMPORT]`, a binding that does not fit: [CE-ALLOW](../pricebt-wire-external-library/references/common-errors.md#ce-allow), [CE-IMPORT](../pricebt-wire-external-library/references/common-errors.md#ce-import)): read the CLI's `error: [...]` line and fix that. `record` is a diagnostic aid, not a fix.
2. **Read top down and stop at the first failing level** (`pricebt-conformance-and-tieout`, `skills/pricebt-conformance-and-tieout/references/tieout-reference.md`, section 4). L0 first: `snapshot_digests`, `resolved_terms` (dates listed first), `conventions_digest`. An L0 difference explains everything below it
   (`input` rows); in particular `L4.stats` and `L4.stats_traded` can stay `exceeds` inside an `input` cascade because they cannot be attributed. Then L1 marks (`marks_rows`, `pv`, `cash`, `financing`), L2 measures, L3 layers, L4 portfolio.
3. **Classify each failing row by status.** `input`: look at the L0 row that explains it. `structure`: different rows, keys or a NaN pattern; the cause is an exception the audit swallowed, so read `result.errors` (`errors[errors["where"] == "audit"]["error"]`), or a
   column one stack lacks. `exceeds`: a value difference; get the ratio (step 4).
4. **Get the ratio and the shape** with the two probes of `references/localise-and-bisect.md` section 2: `ratio(ref, other, "measure_dv01")` (constant, ratio = other / reference: -1 a sign, 0.01 or 100 percent vs decimal, 1e4 bp, the notional or 1e6 / notional a per-unit or per-million factor, 0.99 = 1 - h^2 a finite-difference step, on the `unexplained` layer only)
   and `first_divergence(ref, other, "pv")` (day 1 for every position: a definition or a unit; later: a date event such as a payment, a fixing or a roll; growing with age: a definition that changes once the instrument has started).
5. **Look the symptom up** in `references/symptom-table.md` (by level: L0 calendar, spot lag, schedule and stub, par units; L1 day count, fixings visibility, interpolation, per-million, global state, rounding; L2 sign, unit, bump size, the dv01 definition (also the library's own parallel dv01: only `L3.tay_unexplained` exceeds), pillar sets (also a pillar set the LIBRARY fixes), ladder keys; L3 layer definitions, the finite-difference step; L4 sizing) and state ONE hypothesis in a sentence.
6. **Localise.** Cut the base to one position and a short window with `--set` (`localise-and-bisect.md` section 1): `--set 'strategy.triggers.0.actions=[{...one add_trade...}]' --set backtest.grid.start=<d1> --set backtest.grid.end=<d2>`. A failing row that survives on one position and three days is a
   one-screen problem. For a date effect, narrow the window around `first_divergence`.
7. **Test the hypothesis with ONE change** (`localise-and-bisect.md` sections 3 and 4): (a) an overlay that differs from your current overlay in exactly one binding (a `bind` merges over the Kit's default by name), tied out against your current stack;
   (b) a convention flipped for BOTH stacks with `--set instruments.<n>.conventions.<key>=<token>` (the shared block); (c) a unit-level probe that resolves or prices the SAME snapshot through your factory and the reference's (`references/regression-test-template.md`, `resolved()`).
   Do a known answer first (a par swap is worth 0; a 5Y pillar swap has all its risk in the 5Y bucket; a payer and its mirror cancel).
8. **Fix it in the adapter.** wrap (calendars, fixings and their units, interpolation tags, global state), conventions map (`CFG-CONVENTION-UNSUPPORTED` for a token you cannot honour), factory (resolve `par` and the dates at the fill pricer, return `dt.date`),
   bindings (`sign`, `scale`, `offset`, `tenors`, `keys`, `reduce`), methods (per-unit numbers, direction sign, notional). See `pricebt-wrap-and-pricer`, `pricebt-instrument-kit`, `pricebt-bindings-cookbook`, `pricebt-layers-and-ladder`.
9. **Pin the cause** with `references/regression-test-template.md`: a unit test at the lowest level with a "teeth" test, and a negative-control overlay that fails at ONE located level and quantity after showing the correct wiring passes that row. Mutation-check the pin (`tools/mutcheck.py`: it keeps your `PYTHONPATH`, and a spec may give `runs`, one pytest call each, so a `-k` filter cannot weaken the other files: `references/regression-test-template.md`).
10. **If the residual is a real noise floor or a definition difference**, do not hide it: find the mechanism with a known-answer probe (`references/worked-examples.md`, examples 1 and 2), then declare `tieout.tolerances.<key>: {rel, floor, reason}` and a ledger entry (`pricebt-conformance-and-tieout`, `skills/pricebt-conformance-and-tieout/references/tolerances.md`).
    The reason and the ledger entry are policy that nothing enforces (the loader takes a bare number): write them, and keep the test that refuses a declaration without a reason.
11. **Re-run the whole tie-out with the shipped tolerances** (plus only the declared, explained keys), then remove every temporary `--set`. The rows that failed are `exact` or `noise`, and the negative control still fails where it should.

## Checks

| check | expected |
|---|---|
| `python -m pytest tests/test_<yourlib>_pin_<cause>.py -q -o addopts= -p no:cacheprovider` (`references/regression-test-template.md`, executed) | passes; the "teeth" test passes because the mistaken wiring is still caught |
| the one-position `--set` command of `localise-and-bisect.md` section 1 on the worked example (wrong-sign stack) | exit 1, `TIE-OUT FAILED: reference_vs_bad: L2.dv01; ...` (five failing rows: `L2.dv01`, `L3.tay_delta`, `L3.tay_unexplained` and the `/unit` twins of the two `L3` rows), `3 points, 1 positions`, `dv01 ... EXCEEDS`, `gamma` and `rate` exact |
| `first_divergence(ref, bad, "pv")` and `ratio(ref, bad, "measure_dv01")` on the wrong-sign stack | `None` and `{'count': 52.0, 'min': -1.0, '50%': -1.0, 'max': -1.0}` |
| the convention sweep of `localise-and-bisect.md` section 4 | `TIE-OUT PASSED` for each supported token; `error: [CFG-CONVENTION-UNSUPPORTED] ...` for an unsupported one |
| `python tools/probe_unexplained_ratio.py` (worked example 1; needs the `QuantLib` package, else ``OptionalDependencyError: pricebt.contrib.quantlib needs the optional extra `QuantLib` (pip install QuantLib)``: skip this row where it is not installed) | ratio 1.000000 for reference vs reference (the known answer), 0.99 for QuantLib vs reference |
| after the fix, the full tie-out | `TIE-OUT PASSED` with no undeclared tolerance; `rep.header["declared_tolerances"]` (the sorted list of declared keys) is empty or is exactly the set you meant to declare, each with a `reason` in the base (the template's `test_every_declared_tolerance_carries_a_reason` checks that) |

## Common failures (while debugging)

* `error: [CFG-UNKNOWN-KEY] strategy.triggers[0].actions[0].trade_durations: unknown key 'trade_durations' for AddTradeAction (did you mean 'trade_duration'?)`: a typo in a `--set` replacement of the actions; the loader names the path and the near miss.
* `error: [CFG-REF] strategy.triggers[0].actions[0].priceables.instrument: unknown instrument 'usd_sofr' (did you mean 'usd_sofr_ois'?)`: the instrument name of the base, not the asset class.
* `error: [CFG-SET] --set expects key=value, got 'backtest.tz'`: a `--set` needs `=`; keys are split on every dot, so a tolerance key such as `L2.dv01` cannot be set with `--set tieout.tolerances.L2.dv01.rel=...`
  (`[CFG-TIEOUT] tieout.tolerances.L2: malformed tolerance key 'L2'`): set the whole mapping, `--set 'tieout.tolerances={"L2.dv01": {rel: 1.0e-3, reason: "..."}}'`.
* `first_divergence` returns `None`: the column agrees within `rel` (default 1e-6, floor 1.0); look at the next quantity or level, or lower `rel` to the report's `tol_rel`.
* `KeyError: 'measure_delta_ladder.10Y_ref'`: the ladder was not audited: pass `audit_measures=(..., "delta_ladder")`; the column name is `measure_delta_ladder.<bucket>`.
* A one-position window in which the swap has matured: a matured swap has no `rate`, so the baseline records one error for the position and the `rate` audit measure one per audited mark (the worked example's base records five in all for its matured swap): recorded errors, not failures, identical in both stacks. Keep the swap alive over the window to avoid the noise.
* Every difference "goes away" when a tolerance is widened: it was hidden, not fixed (executed: `L2.dv01` at `rel: 3.0` turned a flipped sign into `within tolerance`). Only the baseline layers still failed.
* `L2.delta_ladder.*` `noise` for a bucket you know is wrong: the small-bucket floor is absolute (`rel * floor`, 0.1 for the shipped ladder). Compare the ladder directly against the reference in a unit test.
* The difference changes between runs, or the self-test fails: process-global state or a non-deterministic source (`symptom-table.md`, sections 2 and 5). Run the same position twice in one process before anything else.
* `binding '<name>': method '<name>' called with args=[] kwargs=['ctx']: ...`: the real error is at the END of the message, a `TypeError` inside your callable ([CE-TYPEERROR](../pricebt-wire-external-library/references/common-errors.md#ce-typeerror)).

## Related skills

`pricebt-conformance-and-tieout` (the proofs, the report, tolerances), `pricebt-discover-the-library` (probing `<LIB>` on known cases), `pricebt-map-library-to-schemas` (units and signs), `pricebt-bindings-cookbook` (`sign`, `scale`, `offset`, reducers),
`pricebt-wrap-and-pricer` (calendars, fixings, global state), `pricebt-instrument-kit` (conventions, factory), `pricebt-layers-and-ladder` (layer definitions), `pricebt-run-config-and-reports` (`--set`, overlays, `on_error`), `pricebt-guards-and-packaging` (test markers).
