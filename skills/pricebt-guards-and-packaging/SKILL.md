---
name: pricebt-guards-and-packaging
description: Use when a pricebt adapter for a new pricing library is planned or written and the repository must be told about it, or when a guard, partition or packaging check fails after adding one. Gives the exact edits (guards, pytest marker, extra, tests, documents, change log) and the controls that prove them.
---

# pricebt-guards-and-packaging

## Purpose

Everything in the repository, outside your adapter code, that must change when a new library `<LIB>` is added, and how to prove each change works. Steps 1 to 8 are done at the START (before adapter code), steps 9 to 16 at the END.
Every edit below was applied to a copy of the repository for a stand-in library and every control was run: the record is `references/checklist.md`.

## Notation

* `<lib>` short lower-case name for directories, markers and files (`pricebt.contrib.<lib>`, `adapter_<lib>`, `tests/test_<lib>_*.py`).
* `<root>` the import root exactly as in `import <root>` (case-sensitive).
* `<name>` the lower-case name under which the library is written in code and configs; it goes into `executable_tokens` and is matched case-insensitively. Choose `<name>` = `<lib>` when the name can be a directory (the QuantLib precedent: `quantlib`): GT-Z2c only sees an overlay that spells the token (step 6).
* `<dist>` the distribution name on the package index (often, not always, equal to `<root>`); `<pt>` = `python -m pytest -o addopts= -p no:cacheprovider -q`.
* Precedent: root `QuantLib`, name and lib `quantlib`, dist `QuantLib`. The stand-in of the dry run has four DIFFERENT names on purpose: root `ZzLib`, name `zzlib`, lib `zz`, dist `zz-lib`, marker `adapter_zz`.

## When each step happens

* **START, before any adapter code** (phase 2b of `pricebt-wire-external-library`, right after its phase 2): steps 1 to 8, gate `<pt> tests/guards -k "not gt_z1b"` green with your entries. Until they are done a stray `import <root>` in core passes every guard (checklist 3.2) and a test carrying the new marker is refused as unmarked (exit 4).
* **END** (phase 10 of the master playbook): steps 9 to 16, once tests, tie-out and documents have something to say.

## Prerequisites

* `pricebt-architecture-and-rules` read (the one rule: nothing under `src/pricebt` except `contrib/` may depend on a pricing library); the adapter DESIGNED (`pricebt-wrap-and-pricer`, `pricebt-instrument-kit`), not necessarily written.
* Work in a COPY or a git worktree of the repository (spec 11.4: the tree may not be under version control), from its root, with `PYTHONPATH=src;tests` (Linux `:`), an interpreter
  with numpy, pandas, pyyaml, tqdm, pytest.
* Answers from YOUR library's documentation, never guessed: **Q1** the import root, spelled exactly. **Q2** the distribution name, the index it is on (public, internal, none) and the
  oldest version you tested. **Q3** may its name, API names and conventions appear in this Apache-2.0 repository; may its code be redistributed. **Q4** does pricing or data need a
  network, credentials, a licence server or a running service. **Q5** does importing it warn, start threads, read files or set process-global state.
* The ask-first items of step 3 put to the maintainer (each has a default if nobody can answer).

## Where the adapter lives (decide first)

| | in the repository: `src/pricebt/contrib/<lib>/` | outside: your package `<pkg>` plus `registry.allow` |
|---|---|---|
| paths in configs | `pricebt.contrib.<lib>:swap` resolves under the default allow-list (`DEFAULT_ALLOW = ("pricebt",)`, `src/pricebt/registry.py`) | `registry: {allow: [<pkg>]}` in the BASE config, or `--set "registry.allow=[<pkg>]"` per run; an overlay carrying `registry` is refused ([CE-STACK-REGISTRY](../pricebt-wire-external-library/references/common-errors.md#ce-stack-registry)); `<pkg>` must be on `sys.path` ([CE-IMPORT](../pricebt-wire-external-library/references/common-errors.md#ce-import)) |
| Python facade | `PricebtSession.use(..., stack="pricebt.contrib.<lib>:STACK")` works, function-target default bindings included | `stack="<pkg>:STACK"` is refused, and a `Stack` OBJECT fails at `instrument_spec` unless its default bindings are METHOD-only ([CE-FACADE](../pricebt-wire-external-library/references/common-errors.md#ce-facade)) |
| guards | its Python is exempt from Z1/Z2/Z4 (`scan.core_py_files` skips any path with a `contrib` part) but is in `scan.all_py_files`; a YAML under `schemas/` is scanned (Z2b) | core is guarded exactly the same (the ban entries); the adapter's own files are not scanned at all, so the per-library test of step 7 is their only guard |
| confidentiality | code, names, ADR and conventions document all enter an Apache-2.0 tree | the adapter code stays in your package, but the repo edits of steps 3(b), 5, 7, 8, 10, 11, 12 (the `filterwarnings` line), 13, 14, 15 still write its name (see "Confidential library") |
| release | ships with pricebt; the extra needs a pin decision | own release cycle |
| its tests | `tests/test_<lib>_*.py`, partition-checked | in `tests/` they are partition-checked too (`pytest.ini` has `pythonpath = . src tests`, so `sys.path.insert` the package directory from `Path(__file__)`; the nested core run GT-Z1b sets `PYTHONPATH=src` only); a suite in your own package directory is NOT subject to the partition hook, which lives in `tests/conftest.py` (verified: an unmarked test outside `tests/` runs) |

Rule: if Q3 says the name, API or conventions are confidential or the licence forbids redistribution, the adapter goes outside AND the edits that name it stay out of the public tree; otherwise inside.

**Confidential library.** Steps 1, 2, 4, 9 and 16 are unchanged and step 12 too, except its `filterwarnings` line. Step 6 does not apply (no `contrib` files). Steps 3(b), 5, 7, 8, 10, 11, 13, 14 and 15 each write the library's name (the spec G-5 marker, a ban entry, the pinned tuple, an extra, the lists of step 11, a NOTICE line, documents, change-log rows): make them in a PRIVATE fork or branch that is never contributed upstream (`references/licence-and-confidentiality.md`, D), or leave the public tree byte-identical and guard core with the ONE private test of `references/test-templates.md`, file 5 (entries added in memory; then skip all of them in the public tree and keep your own notes). A marker name derived from nothing (`adapter_<alias>`) is technically fine (no test compares a marker with a library name), but adding it to the public tree is still the spec G-5 edit.

## Steps

1. **Copy the repository** with ABSOLUTE exclusions only (PowerShell: `robocopy <repo> <copy> /E /XD <repo>\data\fixtures <repo>\results <repo>\results_new /XF *.pyc /NFL /NDL /NJH /NP`, exit code 1 = files copied = success; empty `__pycache__`
   directories may remain, harmless) or make a worktree, and record a baseline: `<pt> tests/guards -k "not gt_z1b"` must be all green before you change anything. A bare name in `/XD` matches at EVERY depth: `results`
   would also drop the core package `src/pricebt/results` (`validate` still says `OK`; the first `run` or `tieout` fails with `No module named 'pricebt.results'`) and `notebooks` would drop `notebooks/src`, which skills cite. The copy legitimately lacks only the contents of `data/fixtures` and the two output
   directories (GP4 says what that costs). (`-k "not gt_z1b"` skips the nested run of the whole core suite under the blocker, which takes minutes, and also the two GT-Z1b twins; `-k "not test_gt_z1b_the_core"` skips only the nested run.)
2. **Decide the location** with the table above and answer Q1-Q5. Read `references/licence-and-confidentiality.md` (licence, what `manifest.json` records, secrets).
3. **Ask first** (spec section 12), before editing; if nobody can answer, take the default and say so in the change log. (a) The extra and its version pin, `pyproject.toml`: adding a
   runtime dependency is ask-first and there is no precedent to copy (`quantlib` has no extra, only `rateslib` does); default: `>=` the oldest version you tested. (b) The text of spec
   G-5 (`docs/design/11-refactor-spec.md`), which enumerates the partitions. It has no upper-case MUST, so spec 12 ("changing a MUST") does not strictly apply, but the pinned test mirrors
   it and the spec's rule is spec first (spec 15): put the one-word insertion (`references/documents.md`, row 1) to the maintainer and make it BEFORE step 5. (c) Any tolerance you will
   widen (later, `pricebt-conformance-and-tieout`). Adding a ban entry needs no permission (`tests/guards/banned.yaml`: "adding a token is easy").
4. **Preflight the ban entries** so they cannot turn existing guards red:
   `python skills/pricebt-guards-and-packaging/references/preflight_guards.py --root <root> --token <name> --match substring` (modes `substring`, `word`, `prefix`; same meaning as in
   `banned.yaml`; it finds the repository above the script or the current directory, else pass `--repo`). It runs the guards' own scan functions with your entries appended to a copy;
   exit 0 = nothing existing is hit. A short or common name needs `--match word`. Also search `src` and `tests` for lines that import `<root>`: a test that imports it without being an
   adapter test will fail under the blocker.
5. **Ban the library from core**, `tests/guards/banned.yaml` (CRLF file: keep the endings): add `- <root>` to `import_roots` and `- {token: <name>, match: <mode>}` to
   `executable_tokens`. Do this BEFORE writing adapter code (checklist, 3.2). Import roots are case-sensitive (`QuantLib` is a root, `quantlib` is a token); tokens are
   case-insensitive. Never remove an entry (needs a spec change).
6. **Adapter skeleton (in-repo).** `src/pricebt/contrib/<lib>/_compat.py` is the ONLY module that imports `<root>`; everything else in the package imports it through `_compat`;
   `src/pricebt/contrib/__init__.py` needs no edit (pattern: `src/pricebt/contrib/quantlib/_compat.py`). Minimal `_compat.py`, with the stand-in names, is the block below the step
   (`pricebt.errors.OptionalDependencyError`); the package's `__init__.py` starts with `from . import _compat`; the real content comes later (`pricebt-wrap-and-pricer`).
   The overlay goes to `configs/adapters/<lib>_<kind>.yaml`: any other shipped config that names the library fails GT-Z2c, but only if the overlay's text spells `<name>`
   (`pricebt.contrib.zz:wrap` against token `zzlib` passes silently in `configs/`; `pricebt.contrib.zzlib:wrap` fails). A YAML inside the package (`schemas/`) is scanned by GT-Z2b: no
   `<name>` in keys or values outside `doc`, extension measures named neutrally. The guards never import contrib (`tests/guards/import_all.py` skips it), so step 7's import test is
   the only import-time test.

```python
# the whole of src/pricebt/contrib/<lib>/_compat.py at this stage
from ...errors import OptionalDependencyError

try:
    import ZzLib
except ImportError as e:
    raise OptionalDependencyError("pricebt.contrib.zz needs the optional extra `zz-lib` (pip install zz-lib)") from e
```

7. **Pin your entries** with `tests/test_<lib>_guards.py`, marker `core` (`references/test-templates.md`, file 1; one line to edit: `ROOT, TOKEN, LIB, DIST = ...`): it asserts both entries, plants an
   import and a literal in a temp tree and expects exactly those hits, checks the blocker refuses `<root>` and `<root>.sub`, checks the adapter is outside `scan.core_py_files`, and
   simulates the missing library with `blocker.install([...])` in a child process (expects `OptionalDependencyError`). The last two tests read the skeleton of step 6, so the file is
   green only after it, and it stays meaningful only while importing the package `pricebt.contrib.<lib>` still imports `_compat`. Mutate each entry away and the `_compat.py` guard: each must fail it. `python tools/mutcheck.py <spec.json>` does that
   (`{"file": "tests/guards/banned.yaml", "tests": ["tests/test_<lib>_guards.py"], "mutations": [{"name": "...", "find": "<text>", "replace": "<text>"}]}`). Two properties matter for an adapter outside `src`: the child pytest keeps YOUR `PYTHONPATH` after `src`
   (an adapter or library importable only through it is visible; before the fix every `importorskip` test skipped and the mutants "survived"), and a spec may give `"runs": [["tests/test_<lib>_wrap.py", "-k", "upload"], ["tests/test_<lib>_swap.py"]]` instead of `tests`: one pytest
   call per list, so a `-k` on one file does not filter the others (a single call with one `-k` weakened seven mutants in the cold-start run). The control run must be green (exit 2 otherwise); exit 0 only if every mutant was killed (`tests/test_mutcheck_env.py`).
8. **Partition** (four lines, three files; `core` must stay FIRST because `NEEDS_SOMETHING = PARTITIONS[1:]`): a line `adapter_<lib>: needs the <dist> extra` in `pytest.ini` `markers`;
   `"adapter_<lib>"` after `"adapter_quantlib"` in `PARTITIONS` (`tests/guards/partition.py`) and a line in its docstring; the same tuple in
   `tests/guards/test_partition.py::test_the_five_partitions_are_the_ones_of_the_spec` (keep its name). Anchor on `"adapter_quantlib", "fixtures"` and insert `"adapter_<lib>", ` between
   them: never retype the last member of the tuple. `pytest.ini` alone is NOT enough: a test carrying only the new marker is refused as unmarked.
9. **Mark every test** exactly: tests needing the library: `adapter_<lib>`, with `pytest.importorskip("<root>")` before `pytestmark` and before any adapter import; tests needing no library
   (the guard file, toys, the reference stack): `core`, never together with an adapter marker (a partition conflict); tests reading the git-ignored `fixtures` directory of `data`: also
   `fixtures`. Never mark a test `core` that imports the library, even lazily: it fails under `python tests/guards/blocker.py -m core`. A module that skips at import is invisible to the
   partition hook: also run `<pt> tests --collect-only` once with the library installed (exit 0 = every test marked). In CI, `pytest -m adapter_<lib>` on a machine without the library exits 5.
10. **Packaging**, `pyproject.toml`: a line `<lib> = ["<dist>>=<min>"]` (for example `zz = ["zz-lib>=0.0.1"]`) under `[project.optional-dependencies]` (the pin is the ask-first item); if the adapter
    ships a data file (default-binding YAML, extension schema), add `"contrib/<lib>/<dir>/*.yaml"` to `[tool.setuptools.package-data]`: the wheel silently drops it otherwise. Check
    the wheel, not pip: `python -m pip wheel . --no-deps --no-build-isolation -w <dir>` (needs setuptools>=68 in the interpreter: in a fresh virtual environment `pip install "setuptools>=68"` first,
    or drop `--no-build-isolation` when the index is reachable), then `METADATA` must contain `Provides-Extra: <extra>` and `Requires-Dist: <dist>...; extra == "<extra>"`, where `<extra>` is
    `<lib>` NORMALISED (lower case, runs of `-_.` become `-`: `zz_x` gives `zz-x`), and the data file must be listed; delete the `egg-info` and `build/` it leaves. If `<dist>` is not on a package index, add NO extra (and no wheel check): document how to install it
    and write the row `packaging: none, because <dist> is not on a package index` in the ADR (`references/documents.md`).
11. **Optional second layer.** At least three tests keep literal library lists that do not read `banned.yaml`; add `'<root>'` to each (one token): `tests/test_core_pricable.py`
    (the `test_import_hygiene_*` test), `tests/test_support_guard.py` (`CHILD`), `tests/guards/test_semantics.py` (the child script). Others carry the pair `('rateslib', 'QuantLib'` for their own
    purpose (`tests/test_gs_compat_api.py` blocks the libraries in a child, `tests/test_synthetic_market.py` already blocks every root of `banned.yaml`): leave them; grep for that tuple to find neighbours. Do not add your stack to
    `STACKS` in `tools/swap_suite_support.py` (it opts the library into the real-data suite tests, which need the git-ignored `data` fixtures, absent in a new environment).
12. **Offline vs live vs secrets.** Offline tests (default run, `adapter_<lib>`): no network, no credentials, no clock; put the autouse `no_network` fixture and its non-vacuity test in
    the file (`references/test-templates.md`, file 2). It blocks Python-level TCP connects, UDP sends and DNS lookups ONLY: a client in native code (gRPC, a C extension, an agent
    process) bypasses it, so for such a library the offline guarantee is "the client is never built in an offline test; the data is a recorded snapshot". Live tests: same marker plus
    `skipif(os.environ.get("PRICEBT_LIVE_<LIB>") != "1", ...)` with a reason naming the variable (file 4); no second partition. Secrets only from the environment or the library's own session;
    never in `conventions` (written verbatim to `manifest.json`), never in a file in the repo. Add a `filterwarnings` line to `pytest.ini` only if importing `<root>` emits a warning that reaches pytest (Q5).
13. **Licence and confidentiality**: answer L1-L6 (`references/licence-and-confidentiality.md`) in the ADR; add the `NOTICE` sentence only when L2 is "yes" (proprietary, source-available or non-commercial: the shipped precedent names rateslib only).
14. **Documents** (`references/documents.md`: exact anchors and texts; its last column says apply, skip or adapt for an adapter OUTSIDE `src/pricebt`, and its ADR template has a `packaging: none, because ...` row): spec G-5 (already done at step 3), `docs/DESIGN.md` (four places), `README.md`, a new ADR and its row in
    `docs/design/adr/README.md`, ADR 008's marker list, `CONSTRAINTS.md`, the conventions document `docs/design/11-<lib>-conventions.md` (model:
    `docs/design/11-quantlib-conventions.md`), `tasks/tolerance_ledger.yaml`, `NOTICE` (L2 only), `docs/guides/backtesting.md` (only if the adapter ships a `STACK` the facade can load), the change log.
15. **Change log** (`docs/design/11-refactor-changelog.md`; spec 11.3): a row in section 2 for every test you edited (the pinned tuple, the lists of step 11) and for any test you removed or
    weakened, in section 3 for every declared tolerance or "none needed", in section 5 for the guard entries, the marker and the overlay location, in section 6 for your mutation checks,
    in section 10 for the tests added. `python tools/floor_check.py` (exit 0) fails on a baseline test that vanished without a row.
16. **Verify** with the Checks below, then the definition of done. Do not "fix" core to suit the library: report a framework problem instead.

## Checks

Run after each step group; each expected outcome was observed in the dry run (`references/checklist.md`, sections 3 and 3.14).

| Command (repo root, `PYTHONPATH=src;tests`) | Expected |
|---|---|
| after step 5: `<pt> tests/guards -k "not gt_z1b"` and `<pt> tests/guards -k twin_gt_z1b` | all passed |
| plant control: append `import <root>` to any core file (for example `src/pricebt/common.py`), run `<pt> tests/guards -k "not test_gt_z1b_the_core"`, then restore the file | exactly GT-Z1a, GT-Z1 for that file and GT-Z2a for that file FAIL (GT-Z2a needs `<name>` to occur inside `<root>`; otherwise also append `X = "<name>"`); after the restore all pass |
| after steps 6 and 7 (the file reads the skeleton): `<pt> tests/test_<lib>_guards.py`; then `python tests/guards/blocker.py tests/test_<lib>_guards.py -m core -o addopts= -p no:cacheprovider -q` | all passed, also under the blocker |
| a temporary test file with no marker: `<pt> <that file>` (then delete it) | exit 4, `ERROR: every test must carry one of the partition markers ...; unmarked: <id>` |
| a temporary `core` test with `import <root>` inside it: `python tests/guards/blocker.py <that file> -o addopts= -p no:cacheprovider -q` (then delete it) | fails: `ModuleNotFoundError: No module named '<root>' (blocked by the pricebt import blocker)` |
| `<pt> tests --collect-only` with the library installed | exit 0 |
| `<pt> tests -m adapter_<lib> -rs` | offline tests pass, live test skipped with its reason |
| `python -m pricebt validate <base config> --stack configs/adapters/<lib>_<kind>.yaml` (in-repo) | `OK`; without the library: `error: [CFG-IMPORT] market.pricers.primary.wrap: pricebt.contrib.<lib> needs the optional extra ...`, exit 2 |
| `python tools/floor_check.py` | `0 UNEXPLAINED`, exit 0 |
| `python tools/mutcheck.py <spec.json>` (step 7; your `PYTHONPATH` is kept after `src`; `"runs"` for several pytest calls) | `control (unmutated): rc=0`, every mutant `[<name>] KILLED`, the last line ends `survivors: none`, exit 0 (a control that is not green aborts with exit 2, a survivor gives exit 1) |
| `python tests/guards/blocker.py -m core -o addopts= -q -p no:cacheprovider -k "not gt_z1b"` (GP4) | all passed or skipped with a reason, about five minutes |
| wheel `METADATA` (step 10; skip it when the packaging row of the ADR says none) | `Provides-Extra: <extra>` (normalised) and every data file present |

## Definition of done

* **GP1** guards and twins green (both `-k` runs above); **GP2** the plant control shown failing and passing, recorded; **GP3** `<pt> tests --collect-only` exit 0.
* **GP4** the WHOLE default suite once (`python -m pytest tests -q -o addopts= -p no:cacheprovider`; it is long and includes the nested core run GT-Z1b): no failure, only labelled skips. What the git-ignored `data/fixtures` does to it (measured on a copy, `references/checklist.md` 3.16):
  ABSENT (the copy of step 1): the tests marked `fixtures` SKIP with a reason (`data/fixtures missing (spec G6)`, decided by `data/fixtures/MANIFEST.json`) and the skills path test skips the `data/fixtures/...` paths it finds. PARTIAL is worse than absent: with `MANIFEST.json` but not the files
  the `fixtures` tests FAIL (`support.common.FixturesMissing: directory not found: <copy>\data\fixtures\calendars`; two files gave 29 failed and 10 errors), and without `MANIFEST.json` they skip but the skills path test FAILS naming `data/fixtures/MANIFEST.json`. So delete a partial
  `data/fixtures` or copy it whole; never edit a test. GT-Z1b fails whenever ANY `core` test fails (its nested run stops at the first failure, `-x`), and a `--deselect` given to the parent run does not reach the nested run. To show that core stands on its own, run the same selection directly under the blocker:
  `python tests/guards/blocker.py -m core -o addopts= -q -p no:cacheprovider -k "not gt_z1b"` (about five minutes; about `2250 passed, 17 skipped`: a run in a full checkout printed `2256 passed, 17 skipped, 164 deselected in 288.88s`; the count grows with the suite, what matters is 0 failed). If one `core` test still fails for a reason of the copy alone, add
  `--deselect <its node id>`, name it in the change log, and do not edit core or the skills to silence it.
* **GP5** fresh-environment release check (spec 9.3): a new virtual environment, `pip install -e ".[parquet,plot,dev]"`, no adapter library,
  `<pt> -m core -k "not test_gt_z1b_the_core"` passes. Without the two extras the `core` tests that persist parquet or render a
  tearsheet fail: a framework issue, named in `references/checklist.md` 3.12.
* **GP6** wheel metadata and data files verified; **GP7** heuristic secret scan empty (a floor, not proof), offline tests carry `no_network`, live tests are gated; **GP8** L1-L6 answered, `NOTICE` done when L2.
* **GP9** every document of step 14 updated, spec G-5 edited first; **GP10** change-log rows present, `tools/floor_check.py` exit 0; **GP11** mutation checks of
  the per-library test and the `_compat.py` guard recorded (`tools/mutcheck.py <spec.json>`: `PYTHONPATH` kept after `src`, `"runs"` for several pytest calls, step 7; or by hand); **GP12** no edit to core to accommodate the library.

## Common failures

| Message (real) | Cause | Fix |
|---|---|---|
| `ERROR: every test must carry one of the partition markers core, adapter_rateslib, adapter_quantlib, ..., live_... (spec G-5); unmarked: tests/<file>::<test>` (exit 4; the last name is elided here) | a test without a partition marker, or the new marker is in `pytest.ini` but not in `PARTITIONS` | mark it; edit `PARTITIONS` (step 8) |
| ``ERROR: a test marked `core` must not also need a library, a fixture or a live checkout (partition conflict): tests/<file>::<test>`` | `core` together with an adapter/fixtures marker, or `core` no longer first in `PARTITIONS` | drop `core`; keep `core` first |
| `assert partition.PARTITIONS == (...)`, `At index 3 diff: 'adapter_<lib>' != 'fixtures'` in `test_the_five_partitions_are_the_ones_of_the_spec` | the pinned tuple was not edited | edit it (step 8), and add its change-log row |
| `PytestUnknownMarkWarning: Unknown pytest.mark.adapter_<lib>` | marker not registered in `pytest.ini` (there is no `--strict-markers`, so nothing else fails) | register it |
| GT-Z1: `pricebt/<file>.py:<n> [import] '<root>' in 'import <root>'`; GT-Z2a: `[name] '<name>' in '<the identifier>'` (`[literal]` for a string) | core imports or names the library | move the code into the adapter; never edit `banned.yaml` to silence it |
| GT-Z1a: `{'pricebt.common': "ModuleNotFoundError: No module named '<root>' (blocked by the pricebt import blocker)", ...}` | a core module imports it; every module that imports THAT module is listed too | fix the first one |
| GT-Z1a: `ModuleNotFoundError: No module named '<root>'` WITHOUT `(blocked by ...)` | `<root>` is missing from `import_roots` (step 5) and a core module imports it, while the library is not visible to the child (`PYTHONPATH=src` only). With the entry present the message ALWAYS says "blocked", visible library or not. (A different module name in that message is a real missing dependency of core.) | add the ban entry, re-run (now "blocked" and GT-Z1, GT-Z2a name the file), then remove the import from core. Do NOT install the library to make it green: the plant stays invisible |
| the two last tests of `tests/test_<lib>_guards.py` fail: `test_the_adapter_package_is_outside_the_core_file_set...`, `test_importing_the_adapter_without_the_library...` | the skeleton `src/pricebt/contrib/<lib>/_compat.py` (step 6) does not exist yet, or `LIB` is spelled differently from the directory | create it / fix `LIB` |
| GT-Z2c: `<file>.yaml:0 [yaml-value] '<name>' in 'market.pricers.primary.wrap -> pricebt.contrib.<lib>:wrap'` | an overlay or config that names the library sits in a scanned directory | move it to `configs/adapters/` |
| an overlay in `configs/` naming only `pricebt.contrib.<lib>:...` passes GT-Z2c | `<name>` is not a substring of `<lib>` (token `zzlib`, lib `zz`) | move it anyway; prefer `<name>` = `<lib>` |
| GT-Z2b: `pricebt/contrib/<lib>/schemas/<f>.yaml:0 [yaml-key] '<name>' in 'measures -> <name>_dv01'` | an adapter's extension schema is scanned like a core schema | neutral names; library-specific detail under `doc:` |
| `pricebt.errors.OptionalDependencyError: pricebt.contrib.<lib> needs the optional extra ...` | library not installed | `pip install <dist>`; tests use `pytest.importorskip("<root>")` |
| a `core` test fails only under `blocker.py`: `... (blocked by the pricebt import blocker)` | the test imports the library | mark it `adapter_<lib>` |
| `<pt> tests -m adapter_<lib>` exits 5 | every adapter module skipped at import (library absent) | treat exit 5 as "nothing to run" in CI, or install the library |
| `UNEXPLAINED: tests/<file>::<test>` from `tools/floor_check.py` | a baseline test vanished (renamed, removed, weakened) without a section-2 row | add the row naming the test |
| `[CFG-ALLOW] module '<pkg>' is not under an allowed prefix ['pricebt']` (external adapter) | `registry.allow` missing in the BASE config | [CE-ALLOW](../pricebt-wire-external-library/references/common-errors.md#ce-allow) |
| `[CFG-ENV] environment variable '<VAR>' is not set and has no default` | `${VAR}` in a config, variable unset | export it, or `${VAR:-default}` for non-secrets |
| `pip._vendor.pyproject_hooks._impl.BackendUnavailable: Cannot import 'setuptools.build_meta'` (step 10) | `--no-build-isolation` in an interpreter without setuptools (a fresh virtual environment) | `pip install "setuptools>=68"`, or drop the flag |
| wheel lacks a YAML file (no error anywhere) | missing `package-data` line | step 10 |
| `test_every_repository_path_a_skill_cites_exists` fails and lists `data/fixtures/MANIFEST.json` | a PARTIAL `data/fixtures` (the directory exists, the manifest does not); an absent directory only skips | delete the directory or copy it whole (GP4); do not edit the test |
| tests marked `fixtures` FAIL with `support.common.FixturesMissing: directory not found: ...\data\fixtures\...` instead of skipping | a PARTIAL `data/fixtures`: `MANIFEST.json` is there, the files are not | same |
| `error: [ModuleNotFoundError] No module named 'pricebt.results'` in a copy, or a skill's `notebooks/src/...` path missing | the copy was made with a BARE `/XD results` or `/XD notebooks` | copy again with absolute paths (step 1) |
| a mutant of an adapter outside `src` "survives", or every test of the spec skips | an old `mutcheck.py` that replaced `PYTHONPATH`, or a `-k` that filters files it should not | keep your `PYTHONPATH` set (it is kept after `src`); use `"runs"` (step 7) |
| the check passes although you expected a failure | the check is vacuous: `pytest.importorskip` skipped the module, or a token entry is missing | run the plant control; run with the library installed |

## Related skills

* `pricebt-wire-external-library`: the master playbook; this skill is its guards, partition and packaging phase (steps 1 to 8 early, see above).
* `pricebt-architecture-and-rules`: the zero-dependence rule and the guard levels Z1-Z5.
* `pricebt-wrap-and-pricer`, `pricebt-instrument-kit`: what `_compat.py`, `wrap` and the Kit contain.
* `pricebt-conformance-and-tieout`: tolerances and their `reason`; `pricebt-debug-tieout-differences` when a run differs.
* `pricebt-enterprise-platform-patterns`: sessions, secrets, batching and offline tests for a remote platform.
* `pricebt-run-config-and-reports`: base configs, overlays, the CLI.
