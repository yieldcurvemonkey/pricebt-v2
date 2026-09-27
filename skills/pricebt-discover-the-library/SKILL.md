---
name: pricebt-discover-the-library
description: Use when you face an unfamiliar pricing library or platform and must learn, before writing any adapter code, what its API offers for each pricebt schema name and which conventions it uses (units, signs, dates, calendars, fixings, curves, risk definitions, global state, errors, cost). Gives the method, a questionnaire, probe scripts that run on known answers first, and the conventions document to write.
---

# Discover an unfamiliar pricing library

## Purpose

An adapter is only as right as your knowledge of the library's conventions, and a convention you assumed is a number that is silently wrong. This skill is the method for turning "I have the library's
documentation" into a written, tested facts table and a conventions document, before any adapter code exists. Nothing is known here about your library: you get the questions and the pricebt-side
consequence of each answer.

## Prerequisites

* The library importable (or its service reachable) in an environment where you can run small scripts, its documentation, and at least one example that prices a swap.
* Repository root as the working directory; `PYTHONPATH` = `src;tests` (Windows separator `;`, `:` on Linux) so `pricebt` (snapshots, the reference stack, the layer kit) is importable. Commands are in bash form,
  `PYTHONPATH="src;tests" python x.py`; in PowerShell set it first: `$env:PYTHONPATH = "src;tests"; python x.py`. Python with numpy, pandas, pyyaml. Your library and your adapter's parent directory must be on `PYTHONPATH` too: `python -m pricebt` also
  puts the working directory on `sys.path`, but a script run as `python tools/<lib>_evidence/p1_<topic>.py` gets only ITS OWN directory, so it needs `.` in `PYTHONPATH` for an adapter at the repository root ([CE-IMPORT](../pricebt-wire-external-library/references/common-errors.md#ce-import)).
* Read first: `src/pricebt/contracts/schemas/swap.yaml` (the names, units, signs and the `conventions:` vocabulary), `docs/design/11-quantlib-conventions.md` (the model of what you will write) and
  `skills/pricebt-wire-external-library/example/README.md` section 2 (a filled mismatch table for a fictional library).

## Steps

1. **Create the conventions document and inventory the API surface, one row per schema name.** Copy the skeleton of `references/conventions-doc-template.md` (its fenced `markdown` block) to
   `docs/design/11-<lib>-conventions.md` NOW and leave it untouched (placeholders and all): every phase writes into it, most sections (layers, golden case, tie-out numbers, mutation evidence, contract gaps) cannot be written before phases 6 to 8, and the
   checker fails on the untouched skeleton by design, so it runs only at the end (step 6). Fill sections 8, then 1 to 4 as the probes answer; the template's table says which section when. Open `swap.yaml` and list every name under `methods:`, `measures:`, `layers:`, `pricer_capabilities:` and the `terms:` and `conventions:` keys
   (add `src/pricebt/contracts/schemas/bond.yaml` if the library prices bonds). For each, write in that document the candidate library call(s), the signature, what it returns (type, unit, whose sign), what
   it needs (market object, valuation date, session) and what one call costs. Group A of `references/discovery-questionnaire.md` has the question per name; A6 lists the six primitives the layers need
   (`pricebt-layers-and-ladder`). A name with no candidate is a gap to record now, not a surprise later.

2. **Answer the questionnaire from the library's documentation BEFORE writing code** (`references/discovery-questionnaire.md`, groups A to D). Tag each answer `DOC`, `CODE`, `PROBE` or `UNKNOWN`.
   Every `UNKNOWN`, and every `DOC` answer that decides a number (a sign, a unit, a default), becomes a probe. Ask the owner of the library for what neither docs nor code tell you (group F lists the
   stop conditions).

3. **Probe conventions on KNOWN ANSWERS, one hypothesis at a time** (`references/probe-skeleton.md`). Write the hypothesis as a sentence, run a small script whose FIRST action is a control (a swap struck at
   its own par is worth 0; a 1Y annual schedule has two dates; a 5Y pillar swap has all its risk in 5Y), print the number with the value you expected, then act on it. One script per topic, kept in
   `tools/<lib>_evidence/p1_<topic>.py`, run from the repository root, exactly as `tools/ql_evidence/*.py` do. Probe in this order: process-global state and error classes FIRST (they decide whether the adapter can run in one process, section 8 of the document, and the skeleton's first call
   is already `setup()`, a global), then units and signs, dates and calendars, the schedule, an at-inception swap, a started swap with fixings, the payment on the reference date, dv01 and the ladder, cost. After each probe, BREAK it on purpose (flip a sign, use the wrong
   unit) and see it fail: a probe that never failed is not shown to work.

4. **Compare against an independent side on the SAME inputs** (`references/sweep-against-reference.md`): the same discount-factor nodes, the same holidays, the same fixings, taken from a pricebt snapshot
   (`pricebt.testing.synthetic.SyntheticMarket` needs no data), priced by the reference stack (`pricebt.testing.refstack`) and by your library. Print the worst absolute and relative difference per
   quantity over dates x tenors x directions and the time per swap. Bisect any difference by changing ONE input at a time.

5. **Record the facts table** (group E of the questionnaire): units, signs, date and tenor formats, calendars and their holiday-data horizon, day counts, fixings and their visibility, interpolation, curve
   inputs and builders, bump sizes and risk definitions, process-global state, thread safety, exceptions, cost per call, licence, version and environment. One line each, with the evidence (script or doc
   page) and the pricebt consequence (a binding `scale`/`sign`, code in `_compat.py`, an UNSUPPORTED value). Decide for every convention of the swap vocabulary: supported (verified against the
   reference), or unsupported with a REASON that will be printed in `[CFG-CONVENTION-UNSUPPORTED]`.

6. **Finish the conventions document** started in step 1 (`docs/design/11-<lib>-conventions.md`, template `references/conventions-doc-template.md`, in the order its "How to fill it" gives), modelled on `docs/design/11-quantlib-conventions.md`: headline table with the script
   of every number, method and controls, dates and calendar, at-inception, started swap, dv01, ladder, process-global state, the gaps that remain (each characterised: cause, size, decision), the
   vocabulary support table and the layer definitions. Sections 5, 6 and 11 wait for the dv01 and ladder checks and the golden case (`pricebt-layers-and-ladder`), sections 0, 9, 12 and 13 for the tie-out. Its sections 3 to 6 and 11 also hold the MAPPING ROWS, one per schema
   name (library callable, unit, sign, binding or code, gap): they are the mapping worksheet of `pricebt-map-library-to-schemas` (nothing else to keep). Correct the document as building the adapter teaches you, and mark the correction. Run the checker last (Checks).

7. **Turn every probe into a permanent test** (`references/probe-to-test.md`): one `test_fact_...` per fact row, marker `adapter_<lib>` and `pytest.importorskip("<lib>")`, asserting the FACT (a sign, a
   ratio, an exception class), never a market number. Mutate the library (a sign flip, a percent, a per-unit) and see the named test fail.

8. **Hand over**: the facts table decides `pricebt-map-library-to-schemas` (which call, which transform), `pricebt-wrap-and-pricer` (globals, calendars, memo), `pricebt-instrument-kit` (conventions map),
   `pricebt-enterprise-platform-patterns` (if the library is remote or serves data). Do not start the adapter with an open `UNKNOWN` on a sign, a unit or a calendar.

## Checks

| what | command | expected |
|---|---|---|
| the probe skeleton runs and its control passes | extract the block of `references/probe-skeleton.md` to `probe_units_signs.py`, then `python probe_units_signs.py` (on acmelib: `PYTHONPATH="src;skills/pricebt-wire-external-library/example"`; PowerShell: `$env:PYTHONPATH = ...` first) | `control ok`, then one line per fact with its number; exit 0 (a `BAD` fact row after the control does not change the exit code) |
| the probe is not vacuous | edit the control to a wrong input (par + 10bp), rerun | `the control failed: fix the probe before believing any number`, exit non-zero |
| the sweep against the reference | the block of `references/sweep-against-reference.md` | `controls ok`, then max abs / max rel per quantity, and a time per swap |
| the facts are tests | `python -m pytest tests/test_<lib>_facts.py -q -o addopts= -p no:cacheprovider` | all pass with the library; SKIPPED with a reason without it; a mutant of the library fails the named test |
| the document is finished (run it ONCE, at the end) | `python skills/pricebt-discover-the-library/references/check_conventions_doc.py docs/design/11-<lib>-conventions.md` (`--selftest` first: it runs on known answers; needs `src` above it or the working directory) | `TBD: 0; unfilled placeholders: []; missing sections: []; schema names without a row: []`, exit 0. It rejects EVERY `<...>` placeholder of the template wherever it appears, prose and code spans included (write `adapter_zeta`, never `adapter_<lib>`), and a schema name of `required_names()` / `required_layers()` without a filled table row (sections 3-6, layers in 11). On the untouched template it exits 1; `docs/design/11-quantlib-conventions.md` predates the rows and passes with `--no-names` |
| nothing was assumed | every row of the facts table has evidence | no `UNKNOWN` left on a sign, unit, calendar or fixing rule |

## Common failures

What probes found before (each is a fact with a reference; expect your library to hold its own versions):

* A named calendar is a process-global singleton: `addHoliday` on it leaks into every later instance (`docs/design/11-quantlib-conventions.md` section 2.1). The fix is a calendar built from the SNAPSHOT's holidays.
* A helper's silent default: an end-of-month flag that turns on when the spot date is the last business day of a month (2.3e-3 on a ladder), a pillar defaulting to the last PAYMENT date (1.2e-2)
  (section 6 and 9.8). Set every such flag explicitly and probe the default.
* A convention the library cannot express (`yield_convention: street`, a "simple in the last period" compounding) must be REFUSED, not approximated: `[CFG-CONVENTION-UNSUPPORTED] convention day_count='act365f'
  is in the vocabulary but this adapter does not support it: not implemented or not verified against the reference adapter; supported: ['act360']` (real message of the QuantLib adapter). The reason
  belongs in the adapter's `_REASON` table and in section 10 of the conventions document.
* A calendar that ends before the trade does: a 30Y swap pays to 2056 and the holiday data stops in 2035; dates after it are silently business days (section 9.1: up to one day of interest on a coupon).
  Ask B4 of the questionnaire for the last covered date.
* A library-side format constraint found late: the QuantLib adapter rejects a 7-digit weekmask with `[CFG-CALENDAR] weekmask '1111100' of calendar 'usd_fed' must list weekdays (Mon Tue ...), got unknown ['111']`
  (`tasks/suite_reproduction.md` section 8). Probe the input formats the snapshot will hand you (`CalendarData.weekmask`, ISO dates, tz-aware stamps).
* A probe that agrees for the wrong reason: two of the first measurements in the QuantLib work were wrong for a reason the controls could not catch (the calendar horizon and the end-of-month default,
  section 1). A control proves the tool, not the whole comparison: also vary dates across month ends, leap years and holidays.
* Your first probe of the risk unit disagrees by a few percent: check the VARIABLE before the unit. On the worked example the library's risk against a parallel zero-curve bump printed `0.9748`; the
  library's risk is par-rate space, and only the strike-bump identity (`probe-skeleton.md` H4) pins the unit. Par and zero space are different measures: the zero variant is an extension measure.
* The library's own exception is not a pricebt error: `acmelib.NoValuationDate: no valuation date: call acmelib.set_valuation_date('YYYY-MM-DD') first` on the worked example. Record every exception
  class in the facts table now; `_compat.translate` needs the list (`pricebt-enterprise-platform-patterns` for what happens if one escapes).
* A forgotten holiday looks like a crash later: with a weekend-only calendar the run stops on a market holiday with `MarketDataUnavailable: market data unavailable at 2024-06-20 17:00:00-04:00: 1 overnight fixings missing
  between 2024-05-28 and 2024-06-19; first 2024-06-19` (worked example, negative control). Probe "which dates does the library think are business days" against the snapshot's calendar early (D12).
* A holiday control that cannot fail: `SyntheticMarket`'s default calendar has no holidays, so a probe that gives the library the wrong holidays passes. Give the market a calendar with holidays
  (`SyntheticMarket(..., calendar=Calendar([...]))`, `flat_swap_world("cal", holidays=...)`) and pick reference dates whose spot date lands ON one (`references/sweep-against-reference.md`).
* The library returns its dates as strings: the pricebt schema check accepts an ISO string as a resolved date without complaint (`AssetSchema.check_resolved` -> `_coerce` returns the string unchanged), so a factory that returns the library's
  strings passes validation and breaks later. Convert to `datetime.date` in the factory and probe the type (B5).
* Nothing says the numbers are right until a second, independent side agrees: a library-vs-itself check (a bump against its own analytic risk) can share one wrong convention. Use the reference stack
  or another library on the same inputs (step 4).

## Related skills

`pricebt-wire-external-library` (the playbook), `pricebt-architecture-and-rules` (what pricebt does and does not know), `pricebt-map-library-to-schemas` (the next step), `pricebt-wrap-and-pricer` (globals
and calendars in code), `pricebt-instrument-kit` (the conventions map), `pricebt-layers-and-ladder` (dv01 definition, ladder, layers), `pricebt-enterprise-platform-patterns` (remote and data-serving
platforms, cost, offline tests), `pricebt-conformance-and-tieout` (after the adapter exists), `pricebt-debug-tieout-differences`.
