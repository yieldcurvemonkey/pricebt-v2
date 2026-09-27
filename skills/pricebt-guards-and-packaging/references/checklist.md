# Checklist and the dry-run record

This file is the evidence behind `SKILL.md`. A COPY of the repository was made and the checklist was applied to it for a fictional library and a tiny adapter package whose only job is to import it. There were two dry runs:

* **Run A** (sections 1 to 3.13): one spelling for everything, `zzlib` (import root, banned name, directory, distribution, marker `adapter_zzlib`). It hid an error in the test templates, which substituted one word for four different quantities.
* **Run B** (section 3.14): four DIFFERENT names, as with a real library: root `ZzLib`, banned name `zzlib`, directory and marker suffix `zz` (`pricebt.contrib.zz`, `adapter_zz`), distribution `zz-lib`. The templates of `references/test-templates.md`, the ordering of the steps, the failure signatures of `SKILL.md` and the packaging claims were re-run under it.

Every control below was run; every output is quoted as it printed, except that the last member of the partition tuple and the name of its marker are elided as `live_...` (a stand-in for the existing last name: NEVER type it, insert your marker next to `"adapter_quantlib"` and leave the rest of the line as it is). In run A `<lib>` = `zzlib`. Nothing in the real repository was touched; the copies were deleted afterwards.

All commands run from the repository root of the copy, interpreter with numpy, pandas, pyyaml, tqdm, pytest (rateslib and QuantLib also installed, irrelevant here), with `PYTHONPATH=src;tests`.

## 1. Setup of the dry run

```powershell
# copy without the data that is not needed (robocopy exit code 1 = files were copied = success); run it from PowerShell
robocopy <repo> <copy> /E /XD <repo>\data\fixtures <repo>\results <repo>\results_new /XF *.pyc /NFL /NDL /NJH /NP
# baseline of the copy BEFORE any change: every guard green
python -m pytest tests/guards -k "not gt_z1b" -o addopts= -p no:cacheprovider -q            # all passed, 3 deselected (about 30 s)
```

`-k "not gt_z1b"` deselects three tests: the nested run of the WHOLE core suite under the blocker (minutes) and the two GT-Z1b twins. To keep the twins use `-k "not test_gt_z1b_the_core"` instead.

(Runs A and B also excluded `notebooks` and listed `__pycache__` by bare name; the command above uses ABSOLUTE `/XD` paths only and `/XF *.pyc`, see section 3.16. The step 1 command of `SKILL.md` keeps it, because skills cite files under it and the skills-library test wants them to exist; section 3.14 has the check on a copy made with exactly that command.)

The stand-in library is one module (`VERSION`, one function). "Installed" must mean importable by CHILD processes: the guard children run with `PYTHONPATH=src` only (`tests/guards/test_guards.py::_child_env`), so a library made importable through the parent's `PYTHONPATH` is invisible to them (section 3.1). In the dry run "installed" = a top-level directory `zzlib/` inside `src` next to `pricebt`; "not installed" = rename it. In a real environment the library is installed into the interpreter's site-packages and none of this is needed.

## 2. What the naive checklist got wrong or missed (found only by the dry run)

The naive checklist was written first, from reading the guards and the worked example: ban entries, marker in `pytest.ini` and `PARTITIONS`, pinned tuple, extra, `OptionalDependencyError`, `importorskip`, offline/live, documents, change log.

| # | Naive expectation | What the dry run showed | Consequence in `SKILL.md` |
|---|---|---|---|
| 1 | Registering the marker in `pytest.ini` is enough | It is not: `PARTITIONS` decides. A test carrying only the new marker (registered in `pytest.ini`, absent from `PARTITIONS`) is refused as UNMARKED, exit 4. The reverse (in `PARTITIONS`, not in `pytest.ini`) only prints `PytestUnknownMarkWarning`: there is no `--strict-markers` (section 3.6) | step 8 edits both, plus the pinned tuple |
| 2 | Adding the ban entries can only make the guards stricter | An `import <root>` planted in core PASSES every guard while the entry is missing and the library is installed (section 3.2): the guard is exactly as strong as `banned.yaml`. And with the entries absent, the plant present and the library visible only through the parent's `PYTHONPATH`, GT-Z1a fails, but with a plain `ModuleNotFoundError` and no "blocked" text: that signature means "the ban entry is missing", not "install the library" (once the entry exists the message always says "blocked", section 3.14) | ban FIRST (step 5); the failure text tells blocked from missing |
| 3 | The ban entries need no check | A token that already occurs in core (a common word, a short name) turns the guards red on files nobody touched. `references/preflight_guards.py` runs the guards' own scan functions with your entries appended and lists the hits before you commit | step 4 |
| 4 | The overlay YAML can live anywhere | GT-Z2c scans every shipped config EXCEPT the ones under `configs/adapters` (and under an `examples` directory of `configs`, which does not exist yet; `scan.core_config_files`) for executable tokens, and the library name is one: an overlay directly in `configs` fails (section 3.3) | step 6 |
| 5 | Everything under `contrib/` is exempt from the guards | Its Python files are exempt from Z1/Z2/Z4. A YAML file `contrib/<lib>/schemas/*.yaml` is NOT: `core_schema_files` globs `schemas/*.yaml` under all of `src/pricebt`, so an adapter's extension schema is scanned by GT-Z2b (no library name in keys or values outside `doc`, no `curves`/`solver`/... as signature keys) (section 3.9) | step 6 |
| 6 | The extra in `pyproject.toml` is all that packaging needs | A YAML data file inside the adapter package is silently DROPPED from the wheel unless `[tool.setuptools.package-data]` lists it; there is no error (section 3.8). `pip install --dry-run` does not check an extra (it printed `Would install pricebt-0.1.0` for a non-existent extra); the wheel's `METADATA` does (`Provides-Extra`, `Requires-Dist`) | step 10 |
| 7 | `pytest.importorskip` at the top of an adapter test file is harmless | A module that skips at import produces no test items, so the partition hook never sees it: a missing marker there is invisible on a machine without the library. `pytest -m adapter_<lib>` on such a machine collects nothing and exits 5 (section 3.7) | step 9, CI note |
| 8 | Put the marker anywhere in `PARTITIONS` | `NEEDS_SOMETHING = PARTITIONS[1:]`: `core` must stay first. With the new marker first, every `core` test is reported as a partition conflict | step 8 |
| 9 | `test_the_five_partitions_are_the_ones_of_the_spec` is the only pinned place | It pins the tuple (and its name says "five"); do NOT rename it (a renamed baseline test needs a change-log row). Other places list the markers literally: the `partition.py` docstring, `CONSTRAINTS.md`, `docs/DESIGN.md`, ADR 008 and spec G-5 (a numbered requirement that enumerates the partitions, no upper-case MUST; edited first) (`references/documents.md`) | steps 3, 8, 14 |
| 10 | The hard-coded library lists need no attention | At least three tests carry their own literal lists of libraries that must not be in `sys.modules` (`tests/test_core_pricable.py`, `tests/test_support_guard.py`, `tests/guards/test_semantics.py`); they do not read `banned.yaml`. One token each (section 3.5). Redundant with the ban entries but a second, independent layer. Two more tests carry the pair `('rateslib', 'QuantLib'` for another purpose (`tests/test_gs_compat_api.py` sets `sys.modules[m] = None` in a child; `tests/test_synthetic_market.py` installs the blocker with every root of `banned.yaml` and only prints the pair): leave them | step 11 (optional hardening) |
| 11 | No per-library test is needed, the generic twins cover the mechanism | Nothing pins YOUR entries: deleting `zzlib` from `banned.yaml` leaves every existing test green (it is the "removing needs a spec change" rule, unenforced). A `core` test that asserts the entries and plants one violation of each kind pins them; its three mutations all fail it (section 3.4) | step 7 |
| 12 | The fresh-environment release check (spec 9.3) is `pip install pricebt` and `pytest -m core` | Seven `core` tests (named in section 3.12) need the `parquet` and `plot` extras (pyarrow, matplotlib) and FAIL in a fresh venv that has only the four core dependencies and pytest; with those two extras and no adapter library the core suite passes | GP5 (step 16), framework issue |
| 13 | A `quantlib` extra exists to copy | `pyproject.toml` has `parquet`, `plot`, `rateslib`, `dev`; there is NO `quantlib` extra although spec Z6 names `pricebt[quantlib]` (the README lists `QuantLib` as an optional adapter dependency without an extra name) | step 10: there is no precedent for the pin; ask |
| 14 | Line endings do not matter | Mixed, file by file (counted byte by byte, table in `references/documents.md`): CRLF in `tests/guards/banned.yaml`, `tests/guards/partition.py`, `pyproject.toml`, `tests/test_core_pricable.py`, `tests/guards/test_semantics.py`, the spec, the change log and `docs/guides/backtesting.md`; LF in `pytest.ini`, `tests/guards/test_partition.py`, `tests/test_support_guard.py`, `docs/DESIGN.md`, `README.md`, `CONSTRAINTS.md`, `NOTICE`, the ADR files and `tasks/tolerance_ledger.yaml`. The Edit tool preserved each file's endings; rewriting a file through PowerShell `Set-Content` would not | `references/documents.md` |
| 15 | The offline/live split is a convention | Not enforced anywhere: nothing stops an "offline" test from opening a socket. The `no_network` fixture with its non-vacuity test enforces it per file (section 3.11) | step 12 |

## 3. Commands and observed results

### 3.1 Adapter skeleton (files created; contrib needs no registration: `src/pricebt/contrib/__init__.py` is unchanged)

* `src/pricebt/contrib/<lib>/_compat.py`: a `try: import <root>` whose `except ImportError` raises `OptionalDependencyError` (the exact block is in `SKILL.md`, step 6); the only module of the package that imports the library. The pattern is the one of `src/pricebt/contrib/quantlib/_compat.py`.
* `src/pricebt/contrib/<lib>/__init__.py`: imports `_compat`, defines `wrap`.

```powershell
python -c "import pricebt.contrib.zzlib"          # library NOT installed
# pricebt.errors.OptionalDependencyError: pricebt.contrib.zzlib needs the optional extra `zzlib` (pip install zzlib)
python -c "import pricebt.contrib.zzlib as m; print(m.ZZPricer, m._compat.VERSION)"        # installed: <class 'pricebt.contrib.zzlib.ZZPricer'> 0.0.1
python -m pricebt validate configs/synthetic_swap_carry.yaml --stack configs/adapters/<lib>_swap.yaml    # installed: OK, exit 0
# library not installed: exit 2, printed:
# error: [CFG-IMPORT] market.pricers.primary.wrap: pricebt.contrib.zzlib needs the optional extra `zzlib` (pip install zzlib)
```

### 3.2 A planted import BEFORE the ban entries exist (naive item 2), then AFTER

Plant: append `import zzlib` to a core file (`src/pricebt/common.py` in the dry run), library installed.

```powershell
python -m pytest tests/guards -k "not test_gt_z1b_the_core" -o addopts= -p no:cacheprovider -q
# entries absent, plant present, library installed:   every guard PASSED        (the plant is invisible)
# entries still absent, library visible only via PYTHONPATH (children cannot see it):   FAILED test_gt_z1a_every_core_module_imports_with_the_blocker_installed
#   AssertionError: {'pricebt.backtests': "ModuleNotFoundError: No module named 'zzlib'", 'pricebt.common': "ModuleNotFoundError: No module named 'zzlib'", ...}   (no "blocked" text)
```

Then add the two entries (`tests/guards/banned.yaml`, CRLF file, edited with single-line anchors):

```diff
 import_roots:      (after the last root)
+  - zzlib
 executable_tokens: (after the last token)
+  - {token: zzlib, match: substring}
```

```powershell
python -m pytest tests/guards -k "not test_gt_z1b_the_core" -o addopts= -p no:cacheprovider -q       # plant present: exactly these three fail
# FAILED tests/guards/test_guards.py::test_gt_z1a_every_core_module_imports_with_the_blocker_installed
# FAILED tests/guards/test_guards.py::test_gt_z1_no_banned_import_in_core_at_any_depth[src/pricebt/common.py]
# FAILED tests/guards/test_guards.py::test_gt_z2a_no_banned_token_in_core_identifiers_or_string_literals[src/pricebt/common.py]
```

The three messages: `AssertionError: pricebt/common.py:<line of the plant> [import] 'zzlib' in 'import zzlib'`; `AssertionError: pricebt/common.py:<line of the plant> [name] 'zzlib' in 'zzlib'` (`[name]` for an identifier, `[literal]` for a string; the two texts differ when the token and the root are spelled differently, section 3.14); and GT-Z1a `{'pricebt.backtests': "ModuleNotFoundError: No module named 'zzlib' (blocked by the pricebt import blocker)", 'pricebt.common': ..., 'pricebt.instrument': ..., 'pricebt.risk': ...}`: it lists every module that imports the culprit, not only the culprit. Restore the file and re-run: every guard passes (the plant is gone: `git diff --no-index` of the file against the original printed nothing).

### 3.3 The overlay (naive item 4)

```powershell
# an overlay {market: {pricers: {primary: {wrap: "pricebt.contrib.zzlib:wrap"}}}} saved as configs/<name>.yaml (a shipped CORE config)
python -m pytest tests/guards -k gt_z2c -o addopts= -p no:cacheprovider -q --tb=line
# AssertionError: zzlib_overlay_misplaced.yaml:0 [yaml-value] 'zzlib' in 'market.pricers.primary.wrap -> pricebt.contrib.zzlib:wrap'    (1 failed: the new parametrised case for that file)
# the same file moved to configs/adapters/<lib>_swap.yaml:   all passed (that directory is not scanned)
```

### 3.4 The per-library twin file (`references/test-templates.md`, file 1) and its mutations

Run after the skeleton of 3.1 exists: with the adapter package moved away, 2 of the 5 tests fail (the file-set test and the `OptionalDependencyError` test; run B).

```powershell
python -m pytest tests/test_<lib>_guards.py -o addopts= -p no:cacheprovider -q                                 # 5 tests, all passed
python tests/guards/blocker.py tests/test_<lib>_guards.py -m core -o addopts= -p no:cacheprovider -q          # all passed under the blocker
# mutation M1: delete the import_roots line       -> 3 failed (the pin, the planted import, the blocker)
# mutation M2: delete the executable_tokens line  -> 2 failed (the pin, the planted literal)
# mutation M3: `except ImportError` -> `except KeyError` in the adapter's _compat.py -> the OptionalDependencyError test fails
# each mutation restored; all passed
```

### 3.5 The hard-coded probes (naive item 10), one token each

```diff
tests/test_core_pricable.py, the test_import_hygiene_* test (the `bad=[...]` list inside the child code string)
-  ('rateslib','QuantLib','gs_quant','MDP','Query','Caching','matplotlib')
+  ('rateslib','QuantLib','gs_quant','zzlib','MDP','Query','Caching','matplotlib')
tests/test_support_guard.py, the `CHILD` code string (the `libs = sorted(...)` line)
-  in ('rateslib', 'QuantLib', 'gs_quant'))
+  in ('rateslib', 'QuantLib', 'gs_quant', 'zzlib'))
tests/guards/test_semantics.py, the child script string (the `hit = sorted(...)` line)
-  in ('rateslib', 'QuantLib', 'gs_quant') or m.startswith('pricebt.contrib'))
+  in ('rateslib', 'QuantLib', 'gs_quant', 'zzlib') or m.startswith('pricebt.contrib'))
```

Non-vacuity of the first edit: with `import zzlib` planted in `src/pricebt/common.py`, the `test_import_hygiene_*` test of `tests/test_core_pricable.py` fails with `AssertionError: ['zzlib']`.

### 3.6 Partition (naive items 1, 8, 9)

```powershell
# marker registered in pytest.ini only; a test file marked adapter_zzlib; library installed
python -m pytest tests/test_<lib>_smoke.py -o addopts= -p no:cacheprovider -q ; echo $LASTEXITCODE
# ERROR: every test must carry one of the partition markers core, adapter_rateslib, adapter_quantlib, fixtures, live_... (spec G-5); unmarked: tests/test_<lib>_smoke.py::test_wrap_reads_the_snapshot_and_the_library     (exit 4)
```

Edits (three files, four lines; the pinned test keeps its name):

```diff
pytest.ini, `markers =` (after the adapter_quantlib line)
+    adapter_zzlib: needs the zzlib extra
tests/guards/partition.py, the module docstring (after the adapter_quantlib line)
+    adapter_zzlib     needs the zzlib extra
tests/guards/partition.py (the PARTITIONS tuple) and tests/guards/test_partition.py (test_the_five_partitions_are_the_ones_of_the_spec, keep the name):
the same edit in both, anchored on the text below; the rest of the line, including the last member, stays exactly as it is (never retype it)
-"adapter_quantlib", "fixtures"
+"adapter_quantlib", "adapter_zzlib", "fixtures"
```

The anchor occurs exactly once in each of the two files.

* Between the second and the third edit the pinned test fails: `assert partition.PARTITIONS == (...)`, `At index 3 diff: 'adapter_zzlib' != 'fixtures'`, `Left contains one more item`. After the third: `tests/guards/test_partition.py` all passed and the smoke test collects and passes.
* Marker in `PARTITIONS` but not in `pytest.ini`: the test passes with `PytestUnknownMarkWarning: Unknown pytest.mark.adapter_zzlib - is this a typo?`.
* `core` must stay first: with the adapter marker placed first `partition.conflicts([<a test marked only core>])` returns that test.
* An unmarked test in a file of its own: `python -m pytest <that file>` exits 4 with the message above; a different file run alone is unaffected (only collected paths are checked); `python -m pytest tests --collect-only -q` lists the tests and STILL exits 4: while such a file exists every run of the whole `tests` directory stops at collection. Delete it at once.
* A test marked `core` and `adapter_zzlib`: `ERROR: a test marked `core` must not also need a library, a fixture or a live checkout (partition conflict): tests/<file>::test_x`, exit 4.
* A `core` test that imports the library inside the test: passes under plain pytest (library installed: the control), FAILS under the blocker: `python tests/guards/blocker.py <file> -o addopts= -p no:cacheprovider -q --tb=short` printed `ModuleNotFoundError: No module named 'zzlib' (blocked by the pricebt import blocker)` at the `import zzlib` line, exit 1. A module-level import fails at collection: `ERROR tests/<file>` and `Interrupted: 1 error during collection`, exit 2.

### 3.7 Offline, live, library absent (naive item 7)

```powershell
python -m pytest tests -m adapter_zzlib -o addopts= -p no:cacheprovider -q -rs          # installed: the offline tests pass, the live test is skipped, everything else deselected
# SKIPPED [1] tests\test_<lib>_live.py:14: opt-in live test: set PRICEBT_LIVE_ZZLIB=1 (needs the service and its credentials in the environment)
$env:PRICEBT_LIVE_ZZLIB = "1"; python -m pytest tests/test_<lib>_live.py -o addopts= -p no:cacheprovider -q      # 1 passed
# library not installed:
python -m pytest tests -m adapter_zzlib -o addopts= -p no:cacheprovider -q ; echo $LASTEXITCODE
# every adapter test module skipped, the rest deselected      exit 5   <- "no tests collected": a CI job that runs only this marker fails on a machine without the library
# SKIPPED [1] tests\test_<lib>_smoke.py:8: could not import 'zzlib': No module named 'zzlib'
```

### 3.8 Packaging (naive items 6 and 13)

```diff
pyproject.toml, [project.optional-dependencies] (after the rateslib line)
+zzlib = ["zzlib>=0.0.1"]
pyproject.toml, [tool.setuptools.package-data] (only when the adapter ships a data file)
-pricebt = ["contracts/schemas/*.yaml"]
+pricebt = ["contracts/schemas/*.yaml", "contrib/zzlib/schemas/*.yaml"]
```

```powershell
python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['optional-dependencies'])"    # {'parquet': [...], 'plot': [...], 'rateslib': [...], 'zzlib': ['zzlib>=0.0.1'], 'dev': [...]}
python -m pip install --dry-run --no-deps --no-index --no-build-isolation -e ".[nosuchextra]"        # Would install pricebt-0.1.0 : it does NOT check extras
python -m pip wheel . --no-deps --no-index --no-build-isolation -w <dir>                             # then read the wheel:
# METADATA lines with the extra:   Provides-Extra: zzlib     Requires-Dist: zzlib>=0.0.1; extra == "zzlib"
# a YAML file schemas/zz_ext.yaml inside the adapter package:  absent from the wheel WITHOUT the package-data line, present WITH it
```

The build wrote an `egg-info` directory under `src` and a `build/` directory into the tree: delete both (they do not belong in a change).

### 3.9 An extension schema inside the adapter (naive item 5)

```powershell
# a file schemas/zz_ext.yaml inside the adapter package containing   asset_class: swap   -> the guards pass (one more GT-Z2b case, green)
# the same file with  measures: {zzlib_dv01: {type: float}}:
# AssertionError: pricebt/contrib/zzlib/schemas/zz_ext.yaml:0 [yaml-key] 'zzlib' in 'measures -> zzlib_dv01'      (test_gt_z2b_no_banned_token_in_schema_keys_or_values[...])
```

Name extension measures neutrally (the shipped ones are `dv01_zero`, `gamma_zero`).

### 3.10 Documents and the change-log rule (`references/documents.md` has the texts)

```powershell
python tools/floor_check.py                     # after the doc edits: baseline ... 0 UNEXPLAINED, exit 0
# the rule, demonstrated on a copy: rename one baseline test function away
#   -> baseline ... 1 UNEXPLAINED   UNEXPLAINED: tests/test_core_time.py::test_calendar_business_days_and_roll     exit 1
# add one row to section 2 of the change log naming `test_core_time::test_calendar_business_days_and_roll`
#   -> baseline ... 0 UNEXPLAINED, exit 0;  restored: 0 UNEXPLAINED
```

Edits applied (single-line anchors, each matched exactly once): `docs/DESIGN.md` four places, `README.md` two, `docs/design/adr/README.md` one row, a new ADR, ADR 008 one word list, `CONSTRAINTS.md` one row, the spec G-5 sentence, `NOTICE` one sentence, the change log rows of sections 2, 3, 5, 10, and a conventions document skeleton. The spec line, anchored on the shorter text that occurs once in the file (`live_...` in the real line is the existing last marker: leave it untouched):

```diff
-`adapter_quantlib`, `fixtures`
+`adapter_quantlib`, `adapter_zzlib`, `fixtures`
```

### 3.11 Offline enforcement

The `no_network` fixture of `references/test-templates.md` (file 2) was also run alone in an empty directory: `2 passed` (the non-vacuity test and a local computation); the same connect call without the fixture reaches the operating system (`TimeoutError` here).

### 3.12 Final verification of the copy, and the fresh environment

```powershell
python -m pytest tests/guards -k "not gt_z1b" -o addopts= -p no:cacheprovider -q                 # all passed (the count grew by 2 x number of new files under contrib: GT-Z5a and GT-Z5b are parametrised over them)
python -m pytest tests/guards -k twin_gt_z1b -o addopts= -p no:cacheprovider -q                  # the 2 twins the first command deselects: passed
python -m pytest tests --collect-only -q -o addopts= -p no:cacheprovider ; echo $LASTEXITCODE    # exit 0: no unmarked test, no conflict
python tools/floor_check.py ; echo $LASTEXITCODE                                                 # 0 UNEXPLAINED, exit 0
```

The guard case count grows because `test_gt_z5a_...` and `test_gt_z5b_...` are parametrised over `all_py_files`, which includes contrib.

Fresh-environment release check (spec 9.3): a new virtual environment with only `numpy pandas pyyaml tqdm pytest`, no rateslib, QuantLib, gs_quant, `zzlib`:

```powershell
python -m pytest -m core -o addopts= -p no:cacheprovider -q -k "not test_gt_z1b_the_core"
# these seven core tests failed: they need pyarrow (`parquet` extra) or matplotlib (`plot` extra):
#   test_config_loader::test_cli_validate_run_and_error_exit, test_config_review::test_the_tieout_command_passes_on_identical_stacks_and_fails_on_a_different_one,
#   test_engine_audit::test_audit_and_settings_survive_the_parquet_round_trip, test_results_compare_report::test_build_report_files_and_content,
#   test_tieout::test_the_report_is_saved_as_parquet_markdown_html_and_json, test_tieout::test_run_tieout_writes_its_output_directory,
#   test_tieout_cycle3::test_t5_the_cli_prints_the_disclosure_and_the_flag
#   e.g. error: [OptionalDependencyError] parquet persistence needs pyarrow (pip install pyarrow); it is an optional dependency of pricebt
pip install "pyarrow>=14" "matplotlib>=3.8"      # the two extras
python -m pytest <those six files> -m core -o addopts= -p no:cacheprovider -q                     # all passed
```

The other failures of that first run were the skills-library tests of the copy (`tests/test_skills_library.py`). Two different causes, only the first of which is specific to that copy: (1) the copy was taken while the other skills of the library were still being written, so their structure tests failed; (2) `test_every_repository_path_a_skill_cites_exists` requires every repository path a skill cites to exist, and the fixtures under `data` are git-ignored (`data/.gitignore` ignores everything) and were excluded from the copy: they are absent in any fresh clone too. Cause 2 is permanent for a clone; section 3.14 records it and what to do.

The run that GP5 of `SKILL.md` prescribes, executed on a second fresh virtual environment (no `PYTHONPATH` at all: `pytest.ini` has `pythonpath = . src tests`):

```powershell
python -m pip install -e ".[parquet,plot,dev]"        # the editable install of the copy, with the three extras, no adapter library, `zzlib` not importable
python -m pytest -m core -o addopts= -p no:cacheprovider -q -k "not test_gt_z1b_the_core"
# the WHOLE core suite: no failure except the skills-library tests of the copy described above (the failures were only those); every other test passed or was skipped with a reason
```

So the fresh-environment core run needs `pricebt[parquet,plot,dev]` and no adapter library. (The nested GT-Z1b run itself was not part of this run: it is the same selection, `-m core`, under the blocker. GP5 of `SKILL.md` now spells the `-k` of the recorded command; an earlier text omitted it.)

### 3.13 Facade and external-adapter facts used by the location table (executed against the worked example)

```powershell
# external adapter, base config WITHOUT registry.allow
python -m pricebt validate configs/synthetic_swap_carry.yaml --stack skills/pricebt-wire-external-library/example/config/acme_swap.yaml
# error: [CFG-ALLOW] market.pricers.primary.wrap: [CFG-ALLOW] module 'acme_adapter' is not under an allowed prefix ['pricebt']       exit 2
# ... with  --set "registry.allow=[acme_adapter]"                                                                                   OK exit 0
# an overlay that carries `registry`:
# error: [CFG-STACK] registry: a stack may not set 'registry': only instruments.<name>.factory / .bind and market.pricers.<role>.wrap (everything else is shared by every stack)   exit 2
```

```python
# fragment
import acme_adapter as A
from pricebt.session import PricebtSession
PricebtSession(market=object(), stack="acme_adapter:STACK")        # RegistryError [CFG-ALLOW] module 'acme_adapter' is not under an allowed prefix ['pricebt']
PricebtSession(market=object(), stack=A.STACK).instrument_spec("swap", "USD")
# ConfigError [CFG-ALLOW] bind.carry.target.function: [CFG-ALLOW] module 'acme_adapter.swap' is not under an allowed prefix ['pricebt']
```

(`PYTHONPATH=src;tests;skills\pricebt-wire-external-library\example`.) And what a run writes (section B of `references/licence-and-confidentiality.md`): `python -m pricebt run configs/synthetic_swap_carry.yaml --out <dir>` then `manifest.json` -> `engine_manifest.instruments.usd_sofr_ois` has the keys `asset_class, conventions, conventions_digest, factory, layers, roles`; `factory` printed `pricebt.testing.refstack:swap` and `conventions` the raw block.

### 3.14 Run B: four different names, and the reviewed claims re-run

Setup: stand-in library `ZzLib` (a `VERSION` and a `discount` function) kept in a directory outside `src` (visible to the PARENT through `PYTHONPATH` only) or copied into `src` ("installed": visible to the children, which run with `PYTHONPATH=src`); adapter skeleton `src/pricebt/contrib/<lib>/` with `<lib>` = `zz` (`_compat.py` as in `SKILL.md` step 6); entries `- ZzLib` and `- {token: zzlib, match: substring}`; marker `adapter_zz`; distribution `zz-lib`. Line endings of `banned.yaml` stayed CRLF after the Edit-tool edits.

**Why run B exists.** A review applied the old one-word substitution rule of the templates to a library whose four names differ (root `QzLib`, name `qzlib`, directory `qz`, distribution `qz-lib`) and got `4 failed, 1 passed` on the guard file. The templates now keep the four names in one line and are checked below with all four different.

**The five templates** were extracted from `references/test-templates.md` by a script and written to `tests/test_zz_*.py`:

| Check | Observed |
|---|---|
| guard file (file 1), library on the parent's `PYTHONPATH` only; then under `blocker.py -m core` | all 5 passed; all passed under the blocker |
| M1 delete the `ZzLib` root / M2 delete the `zzlib` token / M3 `except ImportError` -> `except KeyError` | 3 failed / 2 failed / 1 failed (the test of the mutated thing among them), each restored |
| the adapter package moved away (step order) | 2 failed: the file-set test and the `OptionalDependencyError` test; with it restored, all passed |
| smoke, no_infra, live, private_core_clean together | all passed, the live test skipped with its reason; with `PRICEBT_LIVE_ZZ=1` it passed |
| `-m adapter_zz -rs` | offline tests passed, live test skipped; `tests --collect-only` exit 0 |
| no_infra with the library invisible to everybody | skipped at import (`could not import 'ZzLib'`); with `import rateslib` planted in the adapter it fails, listing the `rateslib.*` modules |
| `no_network`, each of its four patches removed in turn (`connect`, `connect_ex`, `sendto`, `getaddrinfo`) | `test_the_no_network_fixture_is_not_vacuous` fails each time; the old two-patch fixture let a UDP `sendto` and `getaddrinfo("localhost", 80)` through |
| file 5 with the public `banned.yaml` untouched | both tests passed; `import ZzLib` planted in `src/pricebt/common.py` -> `[import] 'ZzLib' in 'import ZzLib'`; `X = "zzlib"` -> `[literal] 'zzlib' in 'zzlib'`; restored: passed |
| a `core` test that imports `pricebt.common` (with the plant) under `blocker.install(['ZzLib'])` then `blocker.main([...])` versus plain `blocker.py` | wrapper: `ModuleNotFoundError ... (blocked ...)`; plain command with the entries absent: passed (library on the parent path) |
| an unmarked test in a file OUTSIDE `tests/` (`--rootdir` the copy, `-c pytest.ini`) | ran and passed: the partition hook is `tests/conftest.py` and applies to tests collected under `tests/` only |

**Ban entries against the plant** (`import ZzLib` appended to `src/pricebt/common.py`, then restored):

| entries | library visible to the children | observed |
|---|---|---|
| absent | no | GT-Z1a fails with `ModuleNotFoundError: No module named 'ZzLib'` and NO "blocked" text; GT-Z1 and GT-Z2a pass |
| absent | yes (in `src`) | every guard passed: the plant is invisible |
| present | no | 3 failed: GT-Z1a `... No module named 'ZzLib' (blocked by the pricebt import blocker)`, GT-Z1 `pricebt/common.py:<n> [import] 'ZzLib' in 'import ZzLib'`, GT-Z2a `pricebt/common.py:<n> [name] 'zzlib' in 'ZzLib'` |
| present | yes | the same 3 failed |

So the plain `ModuleNotFoundError` is the signature of a MISSING ban entry; installing the library there only turns the check green while the import stays in core.

**Overlay control.** `configs/<misplaced>.yaml` (here `zz_misplaced.yaml`) with `wrap: "pricebt.contrib.zz:wrap"`: GT-Z2c silent (the text does not spell the token `zzlib`). With `pricebt.contrib.zzlib:wrap`: `FAILED test_gt_z2c_no_banned_token_in_shipped_core_configs[configs/<misplaced>.yaml]`, `[yaml-value] 'zzlib' in 'market.pricers.primary.wrap -> pricebt.contrib.zzlib:wrap'`; moved to `configs/adapters/<lib>_swap.yaml`: passes.

**Packaging.** A wheel built from a `pyproject.toml` with `zz = ["zz-lib>=0.0.1"]`, `zz_x = ["Zz.Lib>=0.0.1"]` and the `contrib/zz/schemas/*.yaml` package-data line: METADATA has `Provides-Extra: zz` / `Requires-Dist: zz-lib>=0.0.1; extra == "zz"` and `Provides-Extra: zz-x` / `Requires-Dist: Zz.Lib>=0.0.1; extra == "zz-x"` (the extra name is normalised, the distribution name is kept as written); the data file is in the wheel. In a FRESH virtual environment `python -m pip wheel . --no-deps --no-build-isolation -w <dir>` fails with `pip._vendor.pyproject_hooks._impl.BackendUnavailable: Cannot import 'setuptools.build_meta'`; after `pip install "setuptools>=68"` it builds.

**Tools.** `references/preflight_guards.py` from the repository: clean names exit 0 (`0 existing core hit(s)`); token `time` exit 1; root `yaml` exit 1 (`pricebt/contracts/schema.py:22 [import] 'yaml' in 'import yaml'`); a copy placed under `<repo>/.claude/skills/<x>/references/` and run from another directory finds the repository; run from a directory that is not in a checkout, or with a wrong `--repo`: `error: not a pricebt checkout (no tests/guards/scan.py found); pass --repo <repository root>`, exit 2. The secret-scan pattern of `references/licence-and-confidentiality.md` was run on eleven small files: it flags the eight secret shapes (and the harmless `token_count = 1234567890`) and leaves the environment-variable file and the plain file alone.

**Fresh environment, second run.** New virtual environment, `pip install -e ".[parquet,plot,dev]"`, `ZzLib`, rateslib and QuantLib not importable, no `PYTHONPATH`: `python -m pytest -m core -o addopts= -p no:cacheprovider -q -k "not test_gt_z1b_the_core" --deselect tests/test_skills_library.py::test_every_repository_path_a_skill_cites_exists` finished in a few minutes with failures ONLY in `tests/test_skills_library.py` (structure, link and python-block tests of sibling skills that were still being written when the copy was taken); every other core test passed or was skipped with a reason.

**The step 1 copy against the skills-library test, GT-Z1b and the whole suite** (a fresh copy made with exactly the step 1 command, current skills; the fixtures under `data` are excluded from it and are git-ignored in any clone): see 3.15.

### 3.15 Step 1 copy: skills-library test, GT-Z1b, whole suite

A fresh copy made with exactly the step 1 command (`robocopy ... /XD data\fixtures results results_new __pycache__`), with the skills of the moment, no adapter, interpreter with rateslib and QuantLib installed:

| Command | Observed |
|---|---|
| `<pt> tests/test_skills_library.py -k cites_exists` | FAILED: the assertion lists paths that begin with `data` and end in `fixtures` (cited by sibling skills), and two other missing paths of sibling skills. With this skill's current files copied in, no line names `pricebt-guards-and-packaging` (an earlier text of this skill cited that path and was listed, which is why it no longer does). The same test in the original repository, where the git-ignored fixtures exist, lists only the sibling skills' own dead paths |
| `<pt> tests/guards -k test_gt_z1b_the_core` (the nested `-m core` run under the blocker, `-x`) | FAILED after about two minutes: `1 failed, 1483 passed, ...` inside the child; the first failing core test was `test_frontmatter_and_required_sections[...]` of a sibling skill. The nested run includes `tests/test_skills_library.py` (marked `core`) and stops at the first failure, so ANY failing core test fails GT-Z1b; in a clone without the fixtures the path test above is one such test |
| `<pt> tests -k "not test_gt_z1b_the_core" --deselect tests/test_skills_library.py::test_every_repository_path_a_skill_cites_exists` (the whole default suite) | about seven minutes: `4 failed, 2502 passed, 114 skipped`; the four failures are all in `tests/test_skills_library.py` (two frontmatter tests, the relative-path test and the python-block test, all of sibling skills still being written); nothing else failed; the `fixtures`-marked tests passed or skipped without the fixtures |

So GP4 of `SKILL.md` is reachable on a clone or copy without the fixtures only with the deselections of GP4; every failure that remained was in the skills-library test and none was in the adapter, the guards or the packaging.

### 3.16 The step 1 copy re-run: bare names, absolute paths, partial fixtures, core under the blocker

Run for the cold-start report (a solver lost core `pricebt.results` and `notebooks/src` to a bare-name `/XD`, and met failures where the skill promised skips). Commands from the repository root, PowerShell for the copies; the numbers are those printed, they drift as tests are added.

| Setup and command | Observed |
|---|---|
| copy with ABSOLUTE `/XD` (`data\fixtures`, `results`, `results_new`) and `/XF *.pyc` | `src/pricebt/results` present, `notebooks/src` present, no `data/fixtures`, no root `results` or `results_new`; 22 `__pycache__` directories with no `.pyc` in them; robocopy exit code 1 |
| the control: bare `/XD results results_new` plus a RELATIVE `data\fixtures` | `src/pricebt/results` MISSING (a bare name matches at every depth), and `data/fixtures` copied whole (a relative path with a separator matches nothing); there `validate` and `run --dry-run` still print `OK`, but `run` and `tieout` print `error: [ModuleNotFoundError] No module named 'pricebt.results'` (exit 2) and `tests/guards/blocker.py -m core` exits 2 at collection (`from pricebt.results.errors import ResultError`) |
| `<pt> tests/test_support_curves.py tests/test_suite_swaps.py`, `data/fixtures` ABSENT | `40 passed, 43 skipped` (the `fixtures` tests skip with `data/fixtures missing (spec G6)`: `tests/support/known.py` looks for `data/fixtures/MANIFEST.json`) |
| the same, `data/fixtures` holding only `MANIFEST.json` | `29 failed, 40 passed, 4 skipped, 10 errors`, e.g. `support.common.FixturesMissing: directory not found: <copy>\data\fixtures\calendars` |
| `<pt> tests/test_skills_library.py`, `data/fixtures` ABSENT | all passed (the `data/fixtures/...` paths the skills cite are skipped) |
| the same, `data/fixtures` holding only `README.txt` | 1 failed: `library-as-data-source.md: data/fixtures/MANIFEST.json` (the directory exists, so its paths are checked); the `fixtures` tests skip (no manifest) |
| `python tests/guards/blocker.py -m core -o addopts= -q -p no:cacheprovider -k "not gt_z1b"`, copy with `data/fixtures` holding only `MANIFEST.json` | `2135 passed, 17 skipped, 164 deselected in 187.77s` (measured when the suite was smaller; the same command later, in a checkout with the full `data/fixtures`: `2256 passed, 17 skipped, 164 deselected in 288.88s`; the count grows with the suite, what matters is 0 failed) |
| the same with `--deselect tests/test_skills_library.py::test_every_repository_path_a_skill_cites_exists` | `2134 passed, 17 skipped, 165 deselected in 189.43s` |
| `<pt> tests/guards -k "not gt_z1b"` in the repository | `484 passed, 3 deselected in 29.20s` |

So: a copy is right when it has core and `notebooks/src` and NO `data/fixtures`; a partial `data/fixtures` is the only state that turns skips into failures; and the direct blocker run above replaces GT-Z1b (whose nested run fails on any `core` failure and does not see a `--deselect` of the parent run).

## 4. Cleanup

The copies, the pristine reference copy used for `git diff --no-index`, the stand-in library, the wheels and the throw-away environments were deleted.
