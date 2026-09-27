# Documents to update when an adapter is added

Every edit below was applied to a copy of the repository for a fictional library (`zzlib`, marker `adapter_zzlib`); the anchors are the exact lines that were matched (each matched exactly once). Substitute `<lib>`, `<root>`, `<name>`, `<dist>` as in `SKILL.md`. Preserve each file's line endings. The Edit tool of the harness preserved them; a PowerShell `Set-Content` rewrite does not. Counted byte by byte:

* CRLF: `tests/guards/banned.yaml`, `tests/guards/partition.py`, `pyproject.toml`, `tests/test_core_pricable.py`, `tests/guards/test_semantics.py`, `docs/design/11-refactor-spec.md`, `docs/design/11-refactor-changelog.md`, `docs/design/11-quantlib-conventions.md`, `docs/guides/backtesting.md`.
* LF: `pytest.ini`, `tests/guards/test_partition.py`, `tests/test_support_guard.py`, `docs/DESIGN.md`, `README.md`, `CONSTRAINTS.md`, `NOTICE`, `docs/design/adr/README.md`, `docs/design/adr/008-zero-dependence-guards.md`, `tasks/tolerance_ledger.yaml`.
* Any other file: check before and after each edit, it prints the CRLF count and the LF-only count and neither may change kind: `python -c "b=open(r'<file>','rb').read(); print(b.count(b'\r\n'), b.count(b'\n')-b.count(b'\r\n'))"`.

Where a diff or an anchor below shows `live_...`, that is the existing last marker of the partition tuple: never type it, anchor on the shorter text `` `adapter_quantlib`, `fixtures` `` and leave the rest of the line as it is.

## Order

Spec first (spec section 15: the spec is edited before the code), then code and tests, then the rest. Every row in the change log names its requirement id (spec 11.3).

| # | File | Edit (anchor -> change) | Why | External adapter (outside `src/pricebt`) |
|---|---|---|---|---|
| 1 | `docs/design/11-refactor-spec.md` | G-5: anchor `` `adapter_quantlib`, `fixtures` `` (occurs once) becomes `` `adapter_quantlib`, `adapter_<lib>`, `fixtures` `` | G-5 is a numbered requirement that enumerates the partitions (no upper-case MUST, so spec 12's "changing a MUST" does not strictly apply); the pinned test mirrors it. Put it to the maintainer, and make the edit BEFORE `SKILL.md` step 5. | **apply**: the marker exists either way, spec first |
| 2 | `docs/DESIGN.md` | (a) section 2 layering line: `contrib.rateslib, contrib.quantlib -> core (+ their library)` gains `, contrib.<lib>`; (b) the `contrib` row of the package table gains `` `contrib/<lib>/*` ``; (c) section 10: a new bullet after the line that starts `` * **`contrib.quantlib`**: ``; (d) section 14 markers bullet: `` `adapter_quantlib`, `fixtures` `` gains `` `adapter_<lib>`, `` | the as-built reference; "where prose disagrees with the code, the code wins" | **adapt**: (d) apply; (a) and (b) skip (they list `contrib` packages); (c) one bullet that names the external package, where it lives, `registry.allow` and where its tests are |
| 3 | `README.md` | the "What is in the box" table gets a row after the `` `pricebt.contrib.rateslib`, `pricebt.contrib.quantlib` `` row; the "Optional:" sentence lists `` `<dist>` (adapter) `` after `` `QuantLib` (adapter), `` | the front page lists the adapters and the extras | **adapt**: the table row names the external package and its location; the "Optional:" sentence is skipped when there is no extra |
| 4 | `docs/design/adr/011-<lib>-adapter.md` and `docs/design/adr/README.md` | a new ADR (next free number: list `docs/design/adr/`) and one row in the README table after the row of ADR 010 (the row whose first cell is `[010]`) | one decision per ADR; the template is below | **apply**, with the `packaging` row of the template below (`none, because ...` when the distribution is not on an index) |
| 5 | `docs/design/adr/008-zero-dependence-guards.md` | the Partition bullet: `` `adapter_quantlib`, `fixtures` or `` gains `` `adapter_<lib>`, `` | the ADR names the markers | **apply** |
| 6 | `CONSTRAINTS.md` | the Partition row: `adapter_quantlib / fixtures /` gains `adapter_<lib> /` | the written bar | **apply** |
| 7 | `docs/design/11-refactor-changelog.md` | rows in sections 2, 3, 5, 6, 10, 11 (texts below) | spec 11.3; `tools/floor_check.py` reads section 2 (and 12) | **apply**; the section 5 overlay row still holds for an overlay in `configs/adapters/`; a base config and negative controls kept in the package (outside `configs/`) are not scanned, say so in the row |
| 8 | `docs/design/11-<lib>-conventions.md` | the conventions document (skeleton below) | each supported convention value verified, each unsupported one with its reason | **apply**: the mapping rows are its sections 3-6 and 11 (`pricebt-discover-the-library`) |
| 9 | `tasks/tolerance_ledger.yaml` | an entry per tie-out run, a `declared_in_base_config` line per declared tolerance (template below); a run that needs none still gets an entry, with `declared_in_base_config: []` (the shipped `suite_s05_bond` does) | spec X3: a tolerance is never widened without a written reason | **apply**: an entry even when nothing is declared |
| 10 | `NOTICE` | one sentence when the library has its own licence (`references/licence-and-confidentiality.md`, L2) | shipped precedent for rateslib | **skip** unless L2 says the library has its own licence |
| 11 | `docs/guides/backtesting.md` | if the adapter ships a `STACK` the facade can load: the three places that list `contrib.rateslib:STACK` / `contrib.quantlib:STACK` (search `contrib.quantlib`) | the user guide lists the shipped stacks | **skip**: the facade cannot load an adapter outside `pricebt` (`skills/pricebt-wire-external-library/references/common-errors.md`, CE-FACADE) |

## Change-log rows (`docs/design/11-refactor-changelog.md`)

Insert each row at the END of the table of its section (the row before the next `## ` heading).

Section 2, tests removed or changed (the rule: nothing is removed or weakened without a row; `tools/floor_check.py` fails on an unexplained missing baseline test, and a row must name the test function or, for a whole file, the file):

```
| `tests/guards/test_partition.py::test_the_five_partitions_are_the_ones_of_the_spec`, `tests/test_core_pricable.py` (the `test_import_hygiene_*` test), `tests/test_support_guard.py`, `tests/guards/test_semantics.py` | EDITED (assertions unchanged in kind; the pinned partition tuple gains `adapter_<lib>`, the literal library list of each of the three tests named gains `<root>`) | a new adapter is registered with the partition and the import probes; nothing was removed or weakened | G-5, Z1 |
```

Section 3, tolerances (`| Level | Quantity | Value | Reason | Date |`): one row per declared key, or

```
| none needed | <lib> vs the reference stack, <strategy> | shipped defaults | every row `exact` or `noise` with the shipped tolerances | <date> |
```

Section 5, guard decisions (`| Decision | Reason |`), three rows:

```
| Added `<root>` to `tests/guards/banned.yaml` `import_roots` and `<token>` to `executable_tokens` (match: <mode>) | core must never import or name the new library; a per-library twin plants one violation of each kind and pins the entry |
| Partition marker `adapter_<lib>` (pytest.ini, `PARTITIONS`, the pinned tuple, spec G-5) | the tests of the adapter need the extra; `core` stays exclusive and first |
| The shipped overlay of the adapter is `configs/adapters/<lib>_<kind>.yaml` | GT-Z2c scans every shipped config except those under `configs/adapters` (and under an `examples` directory of `configs`) for executable tokens, and the library name is one |
```

Section 6, mutation checks (`| Target | Result |`): one row for the mutations you ran (`tools/mutcheck.py <spec.json>`, or by hand: remove each ban entry, break the `_compat.py` guard) with killed and survivors. `mutcheck.py` keeps your `PYTHONPATH` after `src`, so an adapter or library outside `src` is visible to it, and a spec may list several pytest calls as `"runs"` (one argument list each) so that a `-k` on one file does not filter the others.

Section 10, additions and tests ADDED (`| Change | Reason / requirement | Tests |`):

```
| <lib> adapter (`src/pricebt/contrib/<lib>`, extra `<lib>`, marker `adapter_<lib>`) | A1, A5, A6, Z6 | `tests/test_<lib>_guards.py` (core), `tests/test_<lib>_smoke.py`, `tests/test_<lib>_no_infra.py`, `tests/test_<lib>_live.py` (opt-in) (ADDED) |
```

Section 11, defects (`| Defect | Found by | Fix | Test |`): any defect of pricebt itself that the integration exposed, with the test that now pins it. Do not fix core to suit the library (`pricebt-architecture-and-rules`); report it.

## ADR template (`docs/design/adr/011-<lib>-adapter.md`)

```
# ADR 011: The <lib> adapter lives in <where> and is registered with the guards and the partition

Status: accepted. Requirements A1, A6, G-4, G-5, Z6.

## Decision

| question | decision | enforced by |
|---|---|---|
| where does the adapter live | <in-repo contrib package, or external package plus registry.allow>: <reason: licence, confidentiality, release cycle> | `tests/test_<lib>_guards.py` |
| how core is kept clean | `<root>` banned import root and token; the overlay is in `configs/adapters/` | GT-Z1, GT-Z1a, GT-Z2a/c and the twins |
| how its tests are partitioned | marker `adapter_<lib>`; live tests gated by `PRICEBT_LIVE_<LIB>=1` | `tests/guards/partition.py` |
| how it is packaged | optional extra `<lib>` (pin decided by the maintainer); `OptionalDependencyError` at import | `_compat.py`, `pytest.importorskip` |
| how it is packaged (the distribution is not on a package index) | packaging: none, because <dist> is not on a package index; the README says how to put its directory on `PYTHONPATH`; `OptionalDependencyError` at import; no wheel check | `_compat.py`, `pytest.importorskip` |

## Rejected

* <alternative and why>
```

## Conventions document skeleton (`docs/design/11-<lib>-conventions.md`)

The model is `docs/design/11-quantlib-conventions.md`; the probing method is `pricebt-discover-the-library`. Headings: `0. Headline` (a table: quantity, sample, worst disagreement against the reference stack, the script that produced it), `1. Method and controls` (each script first runs on an input whose answer is known), `2..n` one section per item (calendar, schedule, at-inception swap, started swap, DV01, ladder, bonds), `Process-global state` (what `_compat.py` guards), `Gaps that remain, each characterised` (calendar data horizon, thread-safety, licence), `Proposed vocabulary values` (each supported value of `src/pricebt/contracts/schemas/swap.yaml` conventions verified, each unsupported one with its reason), `Contract gaps found`, `Evidence for the adapter` (tests and mutation checks). Every number names the script or test that produced it; that script must exist.

## Tolerance ledger entry (`tasks/tolerance_ledger.yaml`, under `runs:`)

```yaml
<lib>_<strategy>:
  what: "the strategy, the window, the stacks and the command that produced the numbers"
  self_test: "the reference stack run twice: exactly zero difference at every level (X5a) - passed"
  measured:
    L0: exact
    L1: {pv: [1.0e-9]}
  declared_in_base_config:
    - {key: "L3.unexplained", value: {rel: 1.5e-2, floor: 1.0}, reason: "the mechanism, verified with a known-answer control, and the measured value"}
  verdict: "what passes with the declared tolerances and what fails without them"
```

The numbers above are placeholders: fill them from the tie-out output. A declaration in a base config's `tieout.tolerances` takes `{rel, floor, expected, strict, reason}` and a `reason` is mandatory for a widened key (`pricebt-conformance-and-tieout`).
