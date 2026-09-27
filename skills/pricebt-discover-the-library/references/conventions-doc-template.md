# Conventions document template: `docs/design/11-<lib>-conventions.md`

The model is `docs/design/11-quantlib-conventions.md`: read its headline table (section 0) and section 1 first. That document is what a maintainer reads to decide whether a difference between
two stacks is a bug or a convention, so every number in it names the script that produced it, every supported value was verified against an independent implementation, and every value that is
NOT supported says why. Write yours the same way. Start it BEFORE the adapter: its process-global state, the probed facts and the mapping rows are the specification the adapter is built and tested against; the sections that need a working adapter
are written from the measured evidence at the end. Correct it when building finds you wrong (mark each correction as the QuantLib document does: "corrected by what building the adapter found").

Copy the skeleton below to `docs/design/11-<lib>-conventions.md` at the START (step 1 of the skill), UNTOUCHED (placeholders and all), and fill it section by section as the phases produce the facts: "How to fill it" says which
sections when. Most sections (layers, golden case, tie-out numbers, mutation evidence, contract gaps) cannot be written before phases 6 to 8, so DO NOT run the checker until the end: it fails on the untouched skeleton by design. Delete no section:
write "not applicable: because ..." instead. A `TBD`, an unreplaced placeholder, a missing section heading or a schema name without a mapping row is a failed check:
`python skills/pricebt-discover-the-library/references/check_conventions_doc.py docs/design/11-<lib>-conventions.md` must exit 0 (it reads THIS file for the placeholders and the 14 headings `## 0.` to `## 13.`).

The placeholder test is a plain substring test, and the list of placeholders is EVERY `<...>` token of the skeleton below (`<LIB>`, `<lib>`, `<n>`, `<name>`, `<x>`, `<call>`, `<unit>`, ... 30 in all), wherever it appears in your document, prose and code spans
included. So write the real name everywhere (`adapter_zeta`, `tests/test_zeta_*.py` for a library named zeta, never `adapter_<lib>`; the copies inside the service example are named `tests/test_skills_example_zeta_*.py`), and when you must show a pattern use a spelling that is not in the skeleton (`{lib}`, `<pkg>`).

The mapping rows (D17): sections 3, 5, 6 and 11 hold ONE ROW PER SCHEMA NAME (`value rate pv`, `dv01 gamma`, `delta_ladder`, `carry roll delta convexity`), columns library callable, unit, sign, binding or code, gap. They ARE the mapping worksheet of phase 3
(`skills/pricebt-map-library-to-schemas/references/mapping-worksheet.md` is the fuller row template: paste its rows here instead of keeping a second file). The checker reads the schema (`required_names()`, `required_layers()` of
`src/pricebt/contracts/schema.py`) and fails a name that has no row or a row with an empty cell.

```markdown
# 11 - <LIB> convention translation and the shared convention vocabulary

Status: <Phase 1 complete YYYY-MM-DD>; the adapter is `src/pricebt/contrib/<lib>/` (or `<lib>_adapter/`); sections <n> were corrected by what building it found.
Everything below was measured, not assumed: every number names the script in `tools/<lib>_evidence/` that produced it (run from the repository root with `PYTHONPATH=src;.`, `<python>`; a script gets only its own directory on `sys.path`, so `.` is what finds an adapter at the repository root).
Library versions: <LIB> <version>, pricebt reference stack <git/tag>. Environment: <profile or host class, NOT a host name>. Curves / fixings / calendars used: <where they came from; provenance>.
Every comparison uses the SAME node table, the SAME holiday set and the SAME fixings on both sides. Where a difference survives it is listed in section 9 with its size and cause.

## 0. Headline
| Quantity | Sample | Worst disagreement <LIB> vs the reference stack | Script |
|---|---|---|---|
| calendar: business-day set, spot date | <n> days; <n> reference dates | <0 days; 0 of n> | `p1_dates.py` |
| swap schedule: termination, accrual dates, payment dates (both legs) | <n> dates x <n> tenors | <n differences> | `p1_sched.py` |
| at-inception swap: NPV, par rate, leg PVs, PV01 | <n> swaps | <rel ...> | `p1_npv.py` |
| started swap (published fixings, current coupon, all flows) | <n> swaps, payer and receiver | <abs ... on notional ...> | `p1_started.py` |
| payment ON the reference date | a swap with a flow paid on D | <0.0 once <switch> is set> | `p1_paydate.py` |
| golden seasoned 3y payer 50mm | the golden case (`docs/DESIGN.md` section 7) | V0/V1/cash to 0.01; carry, roll, delta, convexity, residual | `p1_layers.py` |
| dv01: fresh and aged | <n> swaps | <rel ...> | `p1_dv01.py` |
| delta ladder | <n> positions | <max bucket diff / max bucket ...> | `p1_ladder.py` |
| cost | per valuation / per ladder / per curve build | <ms>, <library calls> | `p1_timing.py` |
One sentence of verdict: which quantities agree to machine precision, which to a noise floor you can explain, and what remains (section 9).

## 1. Method and controls
Each script first runs on an input whose answer is known: <a 1Y payer at its own par has NPV 0; an 18M schedule with a short front stub is exactly [d1, d2, d3]; a 5Y pillar par swap has all of
its risk in the 5Y bucket; ...>. List the controls, and the measurements that were WRONG for a reason the controls could not catch (each with its section), so nobody repeats them.

## 2. Dates, calendar, schedule
2.1 The calendar: how it is built FROM THE SNAPSHOT's holidays and weekmask (never a named library calendar); leak checks (named calendars global?); agreement over the data window; the HORIZON:
    the last date the holiday data covers and what the library does after it.
2.2 Spot date, tenor addition, termination: a table `step | reference | <LIB> construction that matches | measured`. Stub side, roll day, explicit maturity dates (which roll results), month-end.

## 3. At-inception swap
Construction (the exact calls); a table `quantity | max abs | max rel | note` (NPV at a fixed rate, par rate, fixed leg PV, floating leg PV, NPV at the agreed par, PV01); the settings that closed
each gap (each measured); sign convention of the library and where it is converted; the unit of every rate.
Mapping rows (unit: what the callable returns, for example decimal, percent or bp, per unit or per million notional; sign: whose point of view; binding or code: the `scale` / `sign` / `reduce` / `keys` line, or the method that does it; gap: what is missing or different and what you decided, `none` if nothing):
| schema name | library callable | unit | sign | binding or code | gap |
|---|---|---|---|---|---|
| `value` | <call> | <unit> | <sign> | <binding or code> | <gap> |
| `rate` | <call> | <unit> | <sign> | <binding or code> | <gap> |
| `pv` | derived by pricebt from `value`: nothing to call | none | none | none: pricebt derives it, bind nothing | none |

## 4. A started swap with published fixings
Fixings: how supplied, unit, visibility (only fixings STRICTLY BEFORE the reference date; the one of the valuation date itself must not be present), the store and its scope (process-global?).
Payment on the reference date; what a missing fixing does (message quoted) and the guard that makes it a `MarketDataUnavailable`; a swap that has matured is worth 0.

## 5. dv01
A table `definition | <LIB> | reference | disagreement`: fresh swap (analytic annuity), market dv01 (the SUM OF THE DELTA LADDER over the bound `tenors`), aged swap. State the ONE definition
(`pricebt-layers-and-ladder`, `dv01-one-definition.md`), the sign, and the pillar set; the annuity of a started swap overstates by up to <x>%.
| schema name | library callable | unit | sign | binding or code | gap |
|---|---|---|---|---|---|
| `dv01` | <call> | <unit> | <sign> | <binding or code> | <gap> |
| `gamma` | <call> | <unit> | <sign> | <binding or code> | <gap> |

## 6. The delta ladder
Construction (pillars, the bumped variable, bump size and direction, re-bootstrap), the essential settings (each with the wrong default and its measured effect), a table `pillar set | max bucket
difference / max bucket | sum of the ladder | bootstrap residual`, and the cost split (per snapshot vs per position).
| schema name | library callable | unit | sign | binding or code | gap |
|---|---|---|---|---|---|
| `delta_ladder` | <call> | <unit> | <sign> | <binding or code> | <gap> |

## 7. Bonds (only if the library prices them; else "not applicable")

## 8. Process-global state (what the guard must own)
| state | how it is set | who restores it | thread safety | measured behaviour |
|---|---|---|---|---|
| valuation / evaluation date | <call> | `_compat.guard` restores in `finally` (even "unset") | <yes/no: source> | <what happens if it changes while an object is alive> |
| fixings store | | | | |
| calendar registry | | | | |
| switches (flows on the reference date, ...) | | | | |
| licence banner / logging | | | | |
Adapter policy: <what the guard sets and restores>; error translation: `<LIB error> -> <pricebt error>` for every class of section C4 of the questionnaire; objects are plain data (deep copy, pickle).

## 9. Gaps that remain, each characterised
### 9.1 <name> (data, not convention)
<cause; measured size (absolute and relative) against the reference; the decision: fix / declare / refuse with a ConfigError; the ledger line if a tolerance is widened>.
(one subsection per gap, for example the calendar horizon, a resolved-terms ambiguity, a solver tolerance of the reference side, a value the library cannot express)

## 10. Convention vocabulary (the shared block, verbatim)
One dict is handed to every adapter and to the reference stack; tokens are neutral spellings. `AssetSchema.check_conventions(raw, require_all=True)` enforces keys, types, tokens and completeness;
the adapter then applies its own supported-subset check. There are NO implicit defaults.
| key | vocabulary | reference construction | <LIB> construction | supported by the adapter | verified by |
|---|---|---|---|---|---|
| `calendar` | the NAME of a `CalendarData` in the snapshot | | | yes (required) | |
| `spot_lag_days` | int | | | any n >= 0 (0, 1, 2 verified) | |
| `day_count` | act360 act365f actact_icma actact_isda thirty360 | | | <values> | <test> |
| `frequency` | monthly quarterly semiannual annual | | | | |
| `business_day_convention` | unadjusted following modified_following preceding modified_preceding | | | | |
| `payment_lag_days` | int | | | | |
| `compounding` | daily_compounded daily_average | | | | |
| `stub` | short_front long_front short_back long_back | | | | |
| `end_of_month` | bool | | | | |
| `fixing_lag_days` | int | | | | |
| `time_accrual` | lump linear | | | | |
For every value that is in the vocabulary but NOT supported: the REASON, exactly as it appears in the adapter's `_REASON` table (it is printed in the `CFG-CONVENTION-UNSUPPORTED` message).
The complete ready-made block: `USD_SOFR_OIS_CONVENTIONS = {...}` (a constant of the adapter; the Kit has no default-conventions field).

## 11. Layer definitions of the adapter
Which of the six library calls (`pricebt-layers-and-ladder`, `layer-definitions-and-assembly.md`) map to which <LIB> primitive; the derivative step `h` or Richardson; the golden case result
(carry, roll, delta, convexity, residual, total) and the difference from the reference; what the remainder ratio is (`1 - h^2`).
| schema name | library callable | unit | sign | binding or code | gap |
|---|---|---|---|---|---|
| `carry` | <call> | <unit> | <sign> | <binding or code> | <gap> |
| `roll` | <call> | <unit> | <sign> | <binding or code> | <gap> |
| `delta` | <call> | <unit> | <sign> | <binding or code> | <gap> |
| `convexity` | <call> | <unit> | <sign> | <binding or code> | <gap> |

## 12. Contract gaps found (for the maintainers of pricebt; do not fix them in core)
Numbered list: each is something the pricebt contract could not express or a message that did not say what to do, with the file and symbol. Example of the shape (from the QuantLib document, its
section 12): "`CalendarData` has no coverage fields (`first`, `last`): a schedule date beyond the last holiday is silently treated as a business day".

## 13. Evidence and licence
Tests (marker `adapter_<lib>`, files), mutation checks (`tools/mutcheck.py` specs, killed / survived / equivalent with the reason for each equivalent one), the library's licence terms for CI
and for committed data, and the sanitising rules for fixtures (`pricebt-enterprise-platform-patterns`).
```

## How to fill it, in order

The file exists from the first hour (untouched skeleton); each phase of `pricebt-wire-external-library` fills its own sections, and the checker runs ONCE, at the end.

| when | sections | from |
|---|---|---|
| phase 1, before any adapter code | 8 (process-global state) and the questionnaire's group C first: they decide whether the adapter can run in one process (`pricebt-wrap-and-pricer`); then 1 (controls), 2, 3 and 4 with the probes of `probe-skeleton.md` and `sweep-against-reference.md`: control first, then the sweep, always the same node table, holidays and fixings on both sides | the probes |
| phase 3, before the Kit | the mapping rows of sections 3, 5, 6 and 11 (one per schema name, cells from your documentation or a probe; `UNKNOWN` is allowed here and not later) | `pricebt-map-library-to-schemas` |
| phase 6, before the adapter's `conventions.py` | section 10: a value belongs in "supported" only when a test verified it against the reference (`test_<lib>_tieout`-style); everything else gets its reason | `pricebt-instrument-kit` |
| phase 7, after the dv01 and ladder checks | the prose of sections 5 and 6 (the ONE dv01 definition, the pillar set, the measured tables); section 11 after the golden case | `pricebt-layers-and-ladder` |
| phase 8, last | 0 (the headline table), 9 (gaps), 12 (contract gaps), 13 (evidence: tests, mutation checks); mark each correction of an earlier section as such (the QuantLib document marks "(corrected by ...)" in its header) | the tie-out and the tests |

Then run the checker (`--selftest` first). It reports four lists: `TBD`, unfilled placeholders, missing sections, schema names without a mapping row; all four must be empty.

A filled miniature (from the worked example, `skills/pricebt-wire-external-library/example/acme_adapter/conventions.py`, executed): for acmelib the reasons are
`day_count`: "acmelib has only ACT/360 and ACT/365 bases"; `stub`: "acmelib always puts the odd period at the front, short"; `end_of_month=True`: "acmelib has no end-of-month roll"; `time_accrual=linear`:
"acmelib accrues in whole days: carry and roll happen at the date change"; and the adapter answers `[CFG-CONVENTION-UNSUPPORTED] convention day_count='thirty360' is in the vocabulary but this adapter does not support it:
acmelib has only ACT/360 and ACT/365 bases; supported: ['act360', 'act365f']`.
