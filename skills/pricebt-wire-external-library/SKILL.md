---
name: pricebt-wire-external-library
description: Master playbook. Use when you must connect an external pricing library (and possibly its market-data service), for example a bank's in-house rates platform, to pricebt so that one backtest runs on it and is tied out against the library-free reference stack (and optionally rateslib and QuantLib). Gives a fast path, the ordered phases, the skill to open at each, the gate that proves each phase, and two tested worked examples.
---

# Wire an external pricing library into pricebt

## Fast path

Read this section and the README of the example that matches your library, then open a skill only when its phase starts. A cold-start run wired a service-style library from these pages. Four references are optional lookups: `kit-skeleton.md`, `pricer-skeleton.md`, `binding-templates.md`, `snapshot-types.md`. Two are NOT: `pricebt-discover-the-library` makes `discovery-questionnaire.md` (answered BEFORE any code, its step 2) and `probe-to-test.md` (every probe becomes a test, its step 7) steps of phase 1.

**1. Pick the example that has your library's shape** (both are tested; each README is a step table with measured results):

| your library | example | README |
|---|---|---|
| in-process Python API (may also serve market data) | `skills/pricebt-wire-external-library/example/` (`acmelib`, adapter `acme_adapter`) | `example/README.md` |
| a service-style library: the platform prices only from a market you upload, and serves no data | `skills/pricebt-wire-external-library/example-service/` (`zeta`, adapter `zeta_adapter`) | `example-service/README.md` |

**2. Files to copy** (paths relative to the example directory; `<lib>_adapter` is your package outside `src/pricebt`; a package in `src/pricebt/contrib/<lib>/` has the same files):

| your file | `example/` | `example-service/` |
|---|---|---|
| `_compat.py` | `acme_adapter/_compat.py`: the ONLY module that touches the library's globals and exceptions | `zeta_adapter/_compat.py` (import guard, error translation) |
| `wrap.py` | `acme_adapter/wrap.py`: snapshot to library objects, memoised | `zeta_adapter/wrap.py`: uploads each snapshot as a market once, counted in `client.stats` |
| `conventions.py` | `acme_adapter/conventions.py`: vocabulary to library codes, refuses the rest | `zeta_adapter/conventions.py` |
| `swap.py`, `__init__.py` | `acme_adapter/swap.py`: class, factory, layers, ladder reducer, `SWAP_BIND`, `Kit` | `zeta_adapter/swap.py`: the same, layers on derived markets |
| base config, overlay | `config/acme_tieout_base.yaml` (`registry.allow`, market, trades), `config/acme_swap.yaml` (factory, wrap only) | `zeta_adapter/config/zeta_tieout_base.yaml`, `zeta_swap.yaml`, and `refstack_zeta_pillars.yaml` (the second overlay for the reference stack, only when the library's pillar set is fixed: decision 4) |
| negative controls | `config/mistakes/`, `acme_mistakes.py` | `zeta_adapter/config/mistakes/`, `zeta_mistakes.py` |

The example tests are not in the example directories: they live in the repository's `tests/` directory and find their example from their own location: `tests/test_skills_example_acme.py` (in-process), `tests/test_skills_example_zeta_{facts,wrap,swap,proofs,no_infra}.py` (service).

**3. The commands** (PowerShell, repository root of your COPY; each was run on the worked example, the checker on its known answers and on the QuantLib model document):

```powershell
robocopy $repo $copy /E /XD "$repo\data\fixtures" "$repo\results" "$repo\results_new" /XF *.pyc /NFL /NDL /NJH /NP   # exit 1 = files copied = success
$env:PYTHONPATH = "src;tests;<the directory that CONTAINS your adapter package>"; $env:PYTHONDONTWRITEBYTECODE = "1"
python -m pytest tests/guards -k "not gt_z1b" -o addopts= -q -p no:cacheprovider                  # START: green before AND after steps 1-8 of the guards skill
python -m pricebt validate <base> --stack <overlay>                                                # OK: the spec loads
python -m pricebt run <base> --stack <overlay> --dry-run                                           # OK: first pricer <YourPricer> at ...
python -m pytest tests/test_<lib>_*.py -o addopts= -q -p no:cacheprovider                          # your kit, facts, controls
python -m pricebt tieout <base> --stack reference=configs/adapters/refstack_swap.yaml --stack <lib>=<overlay> --reference reference   # example/ (acme): TIE-OUT PASSED
python -m pricebt tieout <base> --stack "reference=configs/adapters/refstack_swap.yaml,<second overlay>" --stack <lib>=<overlay> --reference reference   # example-service/ (zeta, FIXED risk pillars): TIE-OUT PASSED
python tests/guards/blocker.py -m core -o addopts= -q -p no:cacheprovider -k "not gt_z1b"         # END: core is library-free (about five minutes)
python skills/pricebt-discover-the-library/references/check_conventions_doc.py docs/design/11-<lib>-conventions.md   # END: the document is finished
```

Which tie-out: the first form when the library's risk pillars are the reference's (acme); the second, with the quotes, when the library's pillar set is FIXED and differs (decision 4: for zeta the second overlay is `example-service/zeta_adapter/config/refstack_zeta_pillars.yaml`). With ONE overlay `example-service` exits 1 (`L2.dv01`, `L3.tay_delta`, `L3.tay_unexplained`: two pillar sets, not a bug). Quote the comma list in PowerShell whenever it holds a `$variable` (CE-STACK-SYNTAX).

The ladder is not compared by that CLI: call `run_tieout(..., audit_measures=(..., "delta_ladder"))` (`pricebt-conformance-and-tieout`, step 9). `--stack` is spelled differently by `validate`, `run` and `tieout` (`references/common-errors.md`, CE-STACK-SYNTAX).

**4. The six decisions** (write each down before code; the answer is in the phase named):

1. Shape and data: in-process or service; does the library also serve the market data, or only price from what you send (phase 2, `pricebt-enterprise-platform-patterns`, `pricebt-market-data-snapshots`).
2. Location and name: adapter in `src/pricebt/contrib/<lib>/` or outside plus `registry.allow`; may the name enter an Apache-2.0 tree; an extra only if the distribution is on a package index (phase 2, `pricebt-guards-and-packaging`).
3. Conventions: each value of the shared vocabulary is supported (verified against the reference) or refused with a reason; nothing hidden in core (phase 1 and 6).
4. Risk basis: `dv01` of a started swap is the sum of the delta ladder; when the library's pillar set is fixed and differs from the reference's, keep its buckets and align the REFERENCE with a second overlay that changes only `tenors` (phase 7, `pricebt-layers-and-ladder`).
5. Layers: which primitives the library offers (value as of a date, a rolled or shocked market, cash flows); a service that only shocks in parallel needs derived markets (phase 7).
6. Tolerances: the shipped defaults are the goal; a difference of DEFINITION is pinned by a test, never declared; a widened key needs a measured mechanism, a `reason` and a ledger entry (phase 8).

## Purpose

pricebt runs one backtest on ANY pricing library without knowing it. You supply four small pieces (a snapshot provider, a `wrap`, an instrument Kit with bindings, a config overlay) and prove them with three checks (the layer conformance kit, known-answer controls, and the tie-out). This skill is the map: what to do in which order, which sibling skill has the detail, and the gate that must be green before the next phase. Two complete, tested examples live next to this file: `skills/pricebt-wire-external-library/example/` (`acmelib`, a fictional in-house library with a deliberately foreign API, in process) and `skills/pricebt-wire-external-library/example-service/` (a service-style library, where the platform prices only from a market you upload).

**The one rule.** Everything under `src/pricebt` except `contrib/` has zero dependence on any pricing library (import, name, shape, semantics). Your library is imported only by your adapter package, and reached only through schema name -> binding -> callable. Never edit core to make a library work: if core seems to need a change, stop and report it.

## Prerequisites

* The repository checked out; install `pip install -e ".[parquet,plot,dev]"` (quote the brackets; pyarrow is needed by the recorded-store reader, `to_parquet` and `--out`, matplotlib by the tearsheet; notebooks also need `nbformat`, `nbclient`, `ipykernel`). Run everything from the repository root.
* Work in a COPY or a git worktree of the repository (the spec makes some edits "ask first": `pricebt-guards-and-packaging`, step 3). The copy command, PowerShell (robocopy exit code 1 means files were copied: success):

  ```powershell
  robocopy $repo $copy /E /XD "$repo\data\fixtures" "$repo\results" "$repo\results_new" /XF *.pyc /NFL /NDL /NJH /NP
  ```

  **Exclude by ABSOLUTE path only.** A bare `/XD results` matches at every depth and also drops the core package `src/pricebt/results` (`validate` still prints `OK`; the first `run` or `tieout` fails with `error: [ModuleNotFoundError] No module named 'pricebt.results'`); a bare `notebooks` drops `notebooks/src`, which skills cite. What the copy legitimately lacks: the contents of `data/fixtures` (git-ignored; tests marked `fixtures` skip with a reason when `data/fixtures/MANIFEST.json` is absent) and the two output directories `results` and `results_new`. A PARTIAL `data/fixtures` (a `MANIFEST.json` without the files) does not skip: those tests FAIL with `FileNotFoundError`; delete the directory or copy it whole (`pricebt-guards-and-packaging`, GP4).
* `PYTHONPATH` has THREE entries: `src`, `tests`, and the directory that CONTAINS your adapter package (pricebt never adds a path; `pytest.ini` adds `. src tests` only, so a test outside those inserts its own). Windows separator `;`, Linux `:`. `python -m pricebt` also puts the working directory on `sys.path`, a script run as `python tools/<script>.py` does NOT (it gets its own directory): for a script always include the adapter's parent (`.` for an adapter at the repository root) (`references/common-errors.md`, CE-IMPORT). `python -m pytest tests -q -o addopts= -p no:cacheprovider` runs the suite (about 12 minutes: run single files while working).
* Your library importable there, its documentation, and a way to price a known trade. You answer the questions of `pricebt-discover-the-library` before writing code.
* pricebt ships everything else: the reference stack (`src/pricebt/testing/refstack.py`), a synthetic market (`src/pricebt/testing/synthetic.py`), the checks.

## Steps (in order; run the gate of a phase before starting the next)

| # | Phase | Open this skill | You produce | Gate |
|---|---|---|---|---|
| 0 | Orient | `pricebt-architecture-and-rules` | nothing (read) | you can say where the provider, wrap, Kit and config each live |
| 1 | Discover the library: API, conventions, units, signs, globals | `pricebt-discover-the-library` | facts table; the conventions document `docs/design/11-<lib>-conventions.md`, CREATED NOW from the untouched skeleton (sections 8 and 1-4 first; the checker runs only at phase 8) | probes ran on a KNOWN ANSWER first (a par swap is worth 0). Compare the library's conventions with what the reference stack supports (`skills/pricebt-instrument-kit/references/terms-and-conventions.md`, item 5): a convention outside that set cannot be tied out and core must not be edited: stop and report |
| 2 | Decide shape and location | `pricebt-enterprise-platform-patterns`, `pricebt-guards-and-packaging` (Q1-Q5) | written decisions: in-process or remote; does it also serve the market data; adapter in-repo (`src/pricebt/contrib/<lib>/`) or outside (`registry.allow`); packaging (an extra, or none because the distribution is not on an index); licence and confidentiality answers; the ask-first items | questions answered in writing. The enterprise skill's steps 2-10 belong to LATER phases: 2 (session) and 3 (remote environment) to phase 5, 4 (cost) and 6 (offline tests) to phases 4 and 8, 7 (errors) to phase 5; its checklist gates phase 8 |
| 2b | **START** of the guards work: register with the guards, before any adapter code | `pricebt-guards-and-packaging` (steps 1-8) | ban entries, `_compat.py` skeleton, per-library guard test, marker `adapter_<lib>` in THREE files (`pytest.ini`, `PARTITIONS`, the pinned tuple in `tests/guards/test_partition.py`) | `python -m pytest tests/guards -q -o addopts= -p no:cacheprovider -k "not gt_z1b"` green with the new entries (until they exist a stray `import <lib>` in core passes every guard, and a test with the new marker is refused as unmarked) |
| 3 | Map schema names to callables | `pricebt-map-library-to-schemas`, `pricebt-bindings-cookbook` | the mapping rows: sections 3-6 of the conventions document (one row per schema name: library callable, unit, sign, binding or code, gap; the layer rows are in section 11); the row template is `skills/pricebt-map-library-to-schemas/references/mapping-worksheet.md`, no separate file | every required name of `swap.yaml` has a filled row (the checker of phase 8 tests it) and a transform |
| 4 | Market data as snapshots, and the first configs | `pricebt-market-data-snapshots` (if the library serves the data: `skills/pricebt-market-data-snapshots/references/library-as-data-source.md`), `pricebt-run-config-and-reports` (`skills/pricebt-run-config-and-reports/references/recipe-minimal-end-to-end.md`) | a provider or a recorded store, or none (a service that prices only from what you upload uses the base's provider); a BASE config and an overlay (create them NOW, grow them through phases 5-7) | the provider's five-line check, and a reference-against-reference tie-out (the same overlay under two names) whose L0 rows are `exact` |
| 5 | `wrap`: snapshot -> library objects | `pricebt-wrap-and-pricer` | `<lib>_adapter/wrap.py`, `_compat.py` | memoised; holidays are the snapshot's; globals guarded and restored; library exceptions translated; for a service, one upload per snapshot proved with the client's own counters |
| 6 | The Kit: every required name bound (stubs allowed), conventions | `pricebt-instrument-kit`, `pricebt-bindings-cookbook` | `swap.py` (factory, class, `SWAP_BIND`, `Kit`), `conventions.py` | the spec LOADS: `python -m pricebt validate <base> --stack <overlay>` prints `OK` (strict mode refuses a Kit that leaves a required name or layer unbound) |
| 7 | Implement value, `dv01`, `gamma`, `rate`, the ladder and the four layers | `pricebt-layers-and-ladder` | working methods, the ladder reducer, the four layers | `check_default_block` (every default binding returns its declared type: `skills/pricebt-map-library-to-schemas/references/conformance-snippet.md`), the golden-case control, the dv01 consistency check and `risk_known_answers` (`skills/pricebt-layers-and-ladder/references/`), and `run_kit(...).assert_ok()`. The kit has teeth for `delta`, `convexity` and `carry` only: an unsigned `roll`, `dv01`, `gamma` or ladder passes it |
| 8 | Prove it: tests, negative controls, tie-out, the conventions document | `pricebt-conformance-and-tieout`, `pricebt-discover-the-library` (the checker) | tests, one negative-control overlay per wiring choice, declared tolerances with reasons, the finished conventions document | `python -m pricebt tieout <base> --stack reference=configs/adapters/refstack_swap.yaml --stack <lib>=<overlay> --reference reference` exits 0 (a fixed pillar set: the reference gets its second overlay, fast path 3); the ladder compared through `run_tieout(..., audit_measures=("dv01", "gamma", "rate", "delta_ladder"))`; the window has at least TWO consecutive business days and a payment date, and L3 has rows (`layer_rows` n > 0): with one grid point no layer is evaluated and the tie-out passes vacuously; `check_conventions_doc.py` exits 0. Optionally add `--stack rateslib=configs/adapters/rateslib_swap.yaml` and `--stack quantlib=configs/adapters/quantlib_swap.yaml` (extras installed; every pair is against the reference; expect QuantLib's declared `L3.unexplained` difference) |
| 9 | When a tie-out fails | `pricebt-debug-tieout-differences` | the cause and a regression test | the failing level and quantity pass; the test fails on the old code |
| 10 | **END** of the guards work: mark, package, document, verify | `pricebt-guards-and-packaging` (steps 9-16) | tests marked, extra or a recorded reason for none, documents (`skills/pricebt-guards-and-packaging/references/documents.md` has an external-adapter column), the tolerance ledger, change-log rows | the checks of that skill; `pytest -m core` under the blocker: `python tests/guards/blocker.py -m core -o addopts= -p no:cacheprovider -q -k "not gt_z1b"` |
| 11 | Run and report | `pricebt-run-config-and-reports` | a runnable config, a notebook | the run reconciles (`BacktestResult.reconcile()`), the tearsheet renders |

Do a VERTICAL SLICE first: one 5Y swap over three consecutive business days with a payment date inside, phases 3-8, every schema name bound. Only then widen (more tenors, seasoned swaps, bonds, the full window).

## Rules for the whole session

1. **A check that has never failed proves nothing.** Run every new check on a known answer first; for every wiring choice (a scale, a sign, a key case, a calendar) keep a NEGATIVE CONTROL, an overlay that breaks exactly that one thing and must fail the tie-out at a located level and quantity (`example/config/mistakes/`, `example/acme_mistakes.py`). A mutation check of your own adapter is `python tools/mutcheck.py <spec.json>`: it keeps your `PYTHONPATH` after `src`, and a spec may list several pytest calls as `"runs"` so a `-k` on one file does not filter the others (`pricebt-guards-and-packaging`, step 7).
2. **Never widen a tolerance to turn a run green.** Declare it in `tieout.tolerances` with a `reason`, and record the measured value in `tasks/tolerance_ledger.yaml`.
3. **Conventions are discovered, not assumed.** Every convention the schema names (day count, calendar, spot lag, frequency, fixings, compounding, stub, end of month) is either honoured and verified against the reference stack or refused with an error naming the supported set. Nothing is hidden in core "for convenience".
4. **Every stack consumes identical snapshots.** For a tie-out, export the library's curves once into a recorded snapshot store (git-ignored if confidential); the other stacks read the same store through `support.curves:CurveStore`, so `registry.allow` lists `support` as well as your package (`skills/pricebt-market-data-snapshots/references/library-as-data-source.md`). A library that serves no data is fed by the base's provider (a synthetic market or a recorded store) for every stack.
5. **A ladder bucket the library has and pricebt does not** (for example an overnight or 1-month pillar): the reducer drops it only if it is exactly 0.0 and REFUSES otherwise; a loaded bucket is agreed with the library owner and never dropped silently. When the library's whole pillar set is fixed and differs from the reference's, do not fold or drop: align the reference (`pricebt-layers-and-ladder`).
6. **Do not edit `src/pricebt` outside `contrib/`.** Report framework problems instead; a removed or weakened test needs a row in `docs/design/11-refactor-changelog.md`.
7. Write files with your editor tools (shell here-documents collapse backslashes on Windows); keep `PYTHONPATH` explicit; never put a secret, a proprietary quote or a token in a config, a fixture or a log.
8. Live tests use the SAME `adapter_<lib>` marker as the offline ones plus `skipif(<env var unset>, reason=...)`: one partition, three pinned edits (`pricebt-guards-and-packaging`).

## Checks (the definition of done)

- [ ] `python -m pytest tests/test_<lib>_*.py -q -o addopts= -p no:cacheprovider` green (kit, schema conformance, units and signs against the reference, negative controls).
- [ ] The tie-out of phase 8 exits 0 with the self-test passed, shipped tolerances or declared ones with a `reason` and a ledger entry, the ladder compared through the Python API, L3 rows present.
- [ ] `python -m pytest tests/guards -q -o addopts= -p no:cacheprovider -k "not gt_z1b"` green with the library named as banned in core; tests carry `adapter_<lib>`; the blocker run of phase 10 green.
- [ ] The conventions document passes `check_conventions_doc.py` (each supported value verified, each unsupported one with its reason, one mapping row per schema name), and the library's licence, thread-safety and data-horizon limits are recorded.
- [ ] The base config, the overlay and a notebook or command list a new person can run. `tests/test_skills_library.py` lists paths under the git-ignored `data/fixtures`; in a clone without the fixtures it skips those.

## Common failures

The ones every adapter meets are on one page with their fixes: `references/common-errors.md`.

| Symptom | Cause | Fix |
|---|---|---|
| `[CFG-IMPORT] ... No module named '<pkg>'` | the adapter's parent directory is not on `PYTHONPATH` | [CE-IMPORT](references/common-errors.md#ce-import) |
| `[CFG-ALLOW] module '<pkg>' is not under an allowed prefix ['pricebt']` | `registry.allow` is missing in the BASE config | [CE-ALLOW](references/common-errors.md#ce-allow) |
| `[CFG-STACK] a stack may not set 'registry'` | `registry` in an overlay | [CE-STACK-REGISTRY](references/common-errors.md#ce-stack-registry) |
| `[CFG-FILE] config file not found: a.yaml,b.yaml`, or `--stack 'x' is given twice` | `--stack` is spelled differently by `validate`/`run` and `tieout` | [CE-STACK-SYNTAX](references/common-errors.md#ce-stack-syntax) |
| a `TypeError` reads like an argument mismatch (`called with args=[] kwargs=['ctx']`) | the TypeError was raised INSIDE the bound callable | [CE-TYPEERROR](references/common-errors.md#ce-typeerror) |
| `PricebtSession` refuses the adapter | the facade builds specs without an allow-list | [CE-FACADE](references/common-errors.md#ce-facade) |
| the CLI tie-out passes and shows no `L2.delta_ladder` rows | the CLI does not compare the ladder | [CE-LADDER-CLI](references/common-errors.md#ce-ladder-cli) |
| `L2.rate exceeds`, relative difference about 0.99 | percent versus decimal | `scale: 100` on the `rate` binding |
| `L2.dv01 exceeds`, relative difference 2.0 | the library is receiver-signed | `sign: -1` on the binding |
| every `L2.delta_ladder.<bucket>` is `structure` | a key case or an extra bucket in the reducer | reducer returns exactly the bound `keys`, upper-case tenors |
| `L0.resolved_terms` is `input` with dates that differ by a day | the wrap loaded the wrong holiday set | load the SNAPSHOT's calendar into the library |
| `MarketDataUnavailable: ... overnight fixings missing` | the calendar lacks a holiday the market had | same; the run stops instead of producing a report unless `backtest.on_error: record` |

More in `example/README.md` sections 8 and 9, and in `pricebt-debug-tieout-differences`.

## Related skills

`pricebt-architecture-and-rules`, `pricebt-discover-the-library`, `pricebt-enterprise-platform-patterns`, `pricebt-map-library-to-schemas`, `pricebt-bindings-cookbook`,
`pricebt-market-data-snapshots`, `pricebt-wrap-and-pricer`, `pricebt-instrument-kit`, `pricebt-layers-and-ladder`, `pricebt-conformance-and-tieout`,
`pricebt-debug-tieout-differences`, `pricebt-guards-and-packaging`, `pricebt-run-config-and-reports`.
