# The zero-dependence guards in practice

Source of truth: `tests/guards/` and `docs/design/adr/008-zero-dependence-guards.md`. The requirement ids (Z1..Z6, G-1..G-5) are in `docs/design/11-refactor-spec.md` sections 2, 6.1 and 9.

## Files

| file | what it is |
|---|---|
| `tests/guards/banned.yaml` | the ONE data file of banned things. Sections: `import_roots` (Z1: library roots), `executable_tokens` (Z2: vendor tokens with a `match` mode `substring`, `word` or `prefix`), `schema_arg_names` (Z2: library kwarg names a schema may not require), `convention_tokens`, `convention_divisors`, `convention_allow` (Z4: conventions and the labelled files exempt from it, each with a `reason`), `prose_tokens` (Z5). Adding a token is easy; removing one needs a spec change first |
| `tests/guards/scan.py` | the static scans: `scan_imports` (import statements at any depth, `importlib.import_module` / `__import__` with a literal), `scan_names`, `scan_yaml`, `scan_schema_args`, `scan_conventions`, `scan_prose`. Each takes the tree as an argument so a twin can point it at a temporary tree. `core_py_files` = everything under `src/pricebt` except `contrib/` |
| `tests/guards/blocker.py` | a `sys.meta_path` finder that raises `ModuleNotFoundError` for a banned root, installed first: it sees an import hidden in a function or a `try`. `python tests/guards/blocker.py [pytest args]` runs pytest with it installed (the "core-only" run) |
| `tests/guards/import_all.py` | imports every core module in a fresh interpreter under the blocker (test `test_gt_z1a_...`) |
| `tests/guards/test_guards.py` | one parametrised case per core file for Z1, Z2, Z4, Z5; the nested core run (`test_gt_z1b_...`, slow: skip it with `-k "not gt_z1b"`) |
| `tests/guards/test_semantics.py`, `zero_toys.py` | Z3: a toy whose method names cross the schema's, decoys that raise if reached by name, arguments named `zzz`/`qqq`, run through a real backtest |
| `tests/guards/test_twins.py` | the non-vacuity twin of every guard: the same scan on a temporary tree with ONE planted violation must report exactly it, and must stay silent on the clean and allowed variants |
| `tests/guards/partition.py`, `test_partition.py` | the test partition: every test carries one of the markers `core`, `adapter_rateslib`, `adapter_quantlib`, `fixtures`, plus an opt-in live marker (registered in `pytest.ini`); the collection hook (imported by `tests/conftest.py`) errors on an unmarked test and on a `core` test that also needs a library |

## What each guard reports (executed)

The script plants one violation per file in a throw-away tree and runs the same scan functions the tests run.

```python
import subprocess
import sys
import tempfile
from pathlib import Path

from guards import scan                      # tests/guards/scan.py: the SAME scans the guard tests run

root = Path(tempfile.mkdtemp())              # a throw-away tree with one planted violation per file
pkg = root / "pricebt"
pkg.mkdir()
(pkg / "__init__.py").write_text("", encoding="utf8")
planted = {
    "z1_import.py": "def price():\n    import rateslib\n",
    "z2_name.py": "def f():\n    return {'factory': 'rl_swap'}\n",
    "z4_divisor.py": "def accrue(x, days):\n    return x * days / 360\n",
    "z4_token.py": "DAY_COUNT = 'act360'\n",
    "clean.py": "import math\n\ndef f(x):\n    return math.sqrt(x)\n",
}
for name, text in planted.items():
    (pkg / name).write_text(text, encoding="utf8")

for name in planted:
    print("---", name)
    for label, fn in (("Z1", scan.scan_imports), ("Z2", scan.scan_names), ("Z4", scan.scan_conventions)):
        for hit in fn(pkg / name, root):
            print(f"   {label}: {hit}")

schema = root / "bad_schema.yaml"
schema.write_text("asset_class: x\nmethods:\n  value:\n    args: {curves: {type: object}}\n    factory: rl_swap\n", encoding="utf8")
for hit in scan.scan_yaml(schema, root) + scan.scan_schema_args(schema, root):
    print("   YAML:", hit)

# the runtime blocker: the same banned roots, refused at import time wherever the import hides
code = "import sys; sys.path.insert(0, 'tests'); from guards import blocker; blocker.install()\ntry:\n    import rateslib\nexcept ModuleNotFoundError as e:\n    print('blocked:', e)\n"
print(subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout.strip())
print("scanned as core:", len(scan.core_py_files()) > 20, "| contrib excluded:", not any("contrib" in p.parts for p in scan.core_py_files()))
```

Run from the repository root with `PYTHONPATH=src;tests`. Output (`clean.py` prints nothing; the two YAML lines follow it):

```
--- z1_import.py
   Z1: pricebt/z1_import.py:2 [import] 'rateslib' in 'import rateslib'
   Z2: pricebt/z1_import.py:2 [name] 'rateslib' in 'rateslib'
--- z2_name.py
   Z2: pricebt/z2_name.py:2 [literal] 'rl_' in 'rl_swap'
--- z4_divisor.py
   Z4: pricebt/z4_divisor.py:2 [convention] '/360' in 'x * days / 360'
--- z4_token.py
   Z4: pricebt/z4_token.py:1 [convention] 'act360' in 'act360'
   Z4: pricebt/z4_token.py:1 [convention] 'day_count' in 'DAY_COUNT'
--- clean.py
   YAML: bad_schema.yaml:0 [yaml-value] 'rl_' in 'methods.value.factory -> rl_swap'
   YAML: bad_schema.yaml:0 [schema-arg] 'curves' in 'methods.value.args.curves'
blocked: No module named 'rateslib' (blocked by the pricebt import blocker)
scanned as core: True | contrib excluded: True
```

A hit reads `<file>:<line> [<kind>] '<banned token>' in '<offending text>'`; in the real test the file is `src/pricebt/...` and the failing test id names the file
(`test_gt_z1_no_banned_import_in_core_at_any_depth[src/pricebt/<file>.py]`).

## How to fix each violation

| you wrote | fix |
|---|---|
| an `import <library>` in core (any depth, also lazily, also `importlib.import_module("<library>...")`) | the import belongs in `src/pricebt/contrib/<lib>/` or in your own adapter package, never in core. A core module that needs something from the library asks for it through a binding |
| a vendor token in an identifier, a string literal, a schema key, a non-`doc` YAML value or a shipped core config (`rl_swap`, a factory name, a library argument name such as `curves` under a schema `args`) | delete it. A name reaches pricebt from config (`factory: "<pkg>:swap"`), never from core text. Docstrings and `doc:` values may mention a library as provenance |
| a day count, a divisor 360 / 365 / 365.25, `compounding`, `settlement`, `payment_lag` ... in core code | it is a convention: it belongs in the instrument's `conventions` block (schema vocabulary) and in the adapter that honours it. Core may only own scheduling (tz, business-day predicate, session, grid), the ledger identity and pricebt's own unit and sign contracts. An exemption is a `convention_allow` entry with a stated `reason`, and needs a spec change |
| a method found by its schema name (`getattr(obj, "gamma")`), an argument injected by parameter name, a config text-sniffed to pick an adapter | bind it: `schema name -> binding -> callable`. The toy proves the failure mode: `AssertionError: the decoy `value` was called: a method is reached only through a binding, never because its name matches a schema name` |
| a test with no partition marker, or a `core` test that needs a library | mark it (`pytest.mark.core` only if it passes with every library unimportable; an adapter test carries the adapter's marker); run `python tests/guards/blocker.py -m core ...` |

## What the guards do NOT cover, and the consequence for your adapter

- `scan.core_py_files` excludes `contrib/`, and your adapter package (a folder outside `src/pricebt`) is not scanned at all: importing the library there is the point. Keep the
  dependency one-way: config (a dotted path) -> your adapter -> your library. Core never imports your adapter; only a dotted path in a config does, under `registry.allow`.
- For a NEW library the extra registrations are: add its import root to `banned.yaml` `import_roots` (so core and the blocker refuse it) and its name to `executable_tokens`, scan the adapter package with a
  non-vacuity twin (as `tests/test_skills_example_acme.py` does for the fictional library), register a partition marker. Steps: skill `pricebt-guards-and-packaging`.
- `import pricebt` must keep working with no adapter library installed (Z6): `python -c "import sys, pricebt, pricebt.api, pricebt.tieout; print([m for m in sys.modules if m.startswith('pricebt.contrib')])"` prints `[]`.
  An adapter raises `OptionalDependencyError` at import when its library is missing (`pricebt.contrib.<lib>._compat`).

## Commands

```powershell
$env:PYTHONPATH = "src;tests"
python -m pytest tests/guards -q -o addopts= -p no:cacheprovider -k "not gt_z1b"        # every static and semantic guard and every twin (about half a minute)
python -m pytest tests/guards/test_semantics.py -q -o addopts= -p no:cacheprovider       # the crossed-names toy in a real backtest (seconds)
python tests/guards/blocker.py tests/<a core test file>.py -q -o addopts= -p no:cacheprovider   # any core test file with every banned import root made impossible
```

The nested core run (`test_gt_z1b_the_core_suite_passes_with_the_blocker_installed`) runs the WHOLE `core` suite under the blocker and takes minutes: run it once at the end, not while working.
