# Test templates for a new adapter

Five files (the fifth only for a confidential library), to be copied to `tests/test_<lib>_<name>.py`. They were run in a copy of the repository for a stand-in library whose four names are all DIFFERENT on purpose (an earlier version used one spelling for all four, which hid a substitution error: with real libraries the four differ, QuantLib being the shipped case: root `QuantLib`, token `quantlib`, directory `quantlib`, distribution `QuantLib`). The results are in `references/checklist.md`, section 3.14.

## What to substitute (per occurrence, not one global rename)

| Stand-in in the templates | Stands for | Notation of `SKILL.md` | Occurs in |
|---|---|---|---|
| `ZzLib` | the import root, exactly as in `import ZzLib` (case-sensitive) | `<root>` | `import` lines, `importorskip(...)`, `blocker.install([...])`, `find_spec(...)`, `banned["import_roots"]` assertions |
| `zzlib` | the lower-case name banned from core code and configs | `<name>` | the `executable_tokens` assertion, the planted literal and the expected hit of `scan_names` |
| `zz` | the directory / marker / file name | `<lib>` | `pricebt.contrib.zz`, `contrib/zz/_compat.py`, `pytest.mark.adapter_zz`, the file names |
| `zz-lib` | the name on the package index (`pip install zz-lib`) | `<dist>` | the text of `OptionalDependencyError` and the assertion on it |
| `ZZ` | `<lib>` in upper case | | `PRICEBT_LIVE_ZZ` |
| `zz_version`, `"0.0.1"` | anything your adapter really exposes | | the toy `wrap` test |

File 1 keeps the four names in ONE line (`ROOT, TOKEN, LIB, DIST = ...`) and file 5 keeps two (`ROOT, TOKEN`): edit that line and nothing else. In files 2 to 4 replace each stand-in by its own real value (the four stand-ins are distinct strings, so a find-and-replace of each one is unambiguous; do `zz-lib` before `zz`).

Rules that the templates encode:

* The guard file is marked `core`: it needs no library (it simulates absence with the import blocker in a child process). A test marked `core` that needs the library is a partition failure, and one that imports it fails under `tests/guards/blocker.py`.
* The guard file is green only after the adapter skeleton exists (`SKILL.md` step 6: its last two tests read `src/pricebt/contrib/<lib>/_compat.py`). Its first three tests need only the ban entries (step 5).
* Every other file starts with `pytest.importorskip("<root>")` BEFORE `pytestmark` and before any adapter import, then the marker, then the adapter imports (marked `# noqa: E402`), the order the shipped adapter tests use.
* A module that skips at import is invisible to the partition hook: a missing marker in it is only detected on a machine where the library is installed. Run `python -m pytest tests --collect-only -q -o addopts= -p no:cacheprovider` at least once with the library installed (`references/checklist.md`, section 3).

## 1. `tests/test_<lib>_guards.py` (marker `core`)

```python
"""The guard entries of a library's adapter: the library is banned from core by import root and by name, one planted violation of each kind is reported,
and the adapter says what is missing when the library is not installed.

`core` (needs no library): it reads the real tests/guards/banned.yaml, runs the same scan functions the guards run on temporary trees, and simulates the
absence of the library with the import blocker in a child process.
"""
import os
import subprocess
import sys

import pytest

from guards import blocker, scan

pytestmark = pytest.mark.core

# THE ONLY LINE TO EDIT. ROOT = `import ROOT` (case-sensitive); TOKEN = the lower-case name in banned.yaml `executable_tokens`;
# LIB = the directory name of the adapter package under contrib; DIST = the name in `pip install DIST`.
ROOT, TOKEN, LIB, DIST = "ZzLib", "zzlib", "zz", "zz-lib"

BANNED = scan.load_banned()


def test_the_library_is_banned_from_core_by_import_root_and_by_name():
    assert ROOT in BANNED["import_roots"]
    assert any(e["token"] == TOKEN for e in BANNED["executable_tokens"])


def test_twin_a_planted_import_and_a_planted_literal_are_reported_and_a_docstring_mention_is_not(tmp_path):
    pkg = tmp_path / "pricebt"
    pkg.mkdir()
    (pkg / "bad_import.py").write_text(f"def f():\n    import {ROOT}\n", encoding="utf8")
    (pkg / "bad_literal.py").write_text(f"NAME = '{TOKEN}'\n", encoding="utf8")
    (pkg / "ok.py").write_text(f"'''A docstring may name {TOKEN}.'''\nX = 1\n", encoding="utf8")
    assert [h.token for h in scan.scan_imports(pkg / "bad_import.py", tmp_path)] == [ROOT]
    assert [h.token for h in scan.scan_names(pkg / "bad_literal.py", tmp_path)] == [TOKEN]
    assert scan.scan_imports(pkg / "ok.py", tmp_path) == [] and scan.scan_names(pkg / "ok.py", tmp_path) == []


def test_the_blocker_refuses_the_library_and_its_submodules_and_leaves_the_adapter_alone():
    f = blocker.BannedRoots(blocker.default_roots())
    for name in (ROOT, ROOT + ".curves"):
        with pytest.raises(ModuleNotFoundError):
            f.find_spec(name)
    assert f.find_spec(f"pricebt.contrib.{LIB}") is None


def test_the_adapter_package_is_outside_the_core_file_set_and_inside_the_all_set():
    adapter = (scan.PROJECT / "src" / "pricebt" / "contrib" / LIB / "_compat.py").resolve()
    assert adapter in {p.resolve() for p in scan.all_py_files()}
    assert adapter not in {p.resolve() for p in scan.core_py_files()}


def test_importing_the_adapter_without_the_library_raises_optional_dependency_error_naming_the_extra():
    code = f"import sys; sys.path.insert(0, {str(scan.PROJECT / 'tests')!r}); from guards import blocker; blocker.install([{ROOT!r}]); import pricebt.contrib.{LIB}"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=scan.PROJECT, env={**os.environ, "PYTHONPATH": "src"})
    assert out.returncode != 0 and "OptionalDependencyError" in out.stderr and f"needs the optional extra `{DIST}`" in out.stderr, out.stderr[-800:]
```

For an EXTERNAL adapter package `<pkg>` (not under `src/pricebt`) two tests change: the import test imports `<pkg>` (put its directory on `sys.path` first, from `Path(__file__)`: `pytest.ini` does not have it), and the core-file-set test is dropped (nothing of the adapter is under `src/pricebt`); the ban assertions, the planted-violation twin and the blocker test stay as they are, because they guard core.

Mutation checks that were run (each must FAIL the file, then restore): delete the `ZzLib` line of `import_roots`; delete the `zzlib` token of `executable_tokens`; change `except ImportError` to `except KeyError` in the adapter's `_compat.py`; the observed counts are in `references/checklist.md` 3.14. A check that has never failed proves nothing.

## 2. `tests/test_<lib>_smoke.py` (marker `adapter_<lib>`, offline)

```python
"""Adapter OFFLINE tests: no network, no credentials, no clock. Needs the optional extra `zz-lib`."""
import datetime as dt
import socket
from types import SimpleNamespace

import pandas as pd
import pytest

pytest.importorskip("ZzLib")
pytestmark = pytest.mark.adapter_zz

from pricebt.contrib.zz import wrap  # noqa: E402


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Python-level network calls fail loudly: TCP connect, UDP sendto, DNS lookup.

    CEILING: a client written in native code (gRPC, a C extension, an agent process) or one that uses the raw `_socket.socket` (an immutable type: it cannot be
    patched) bypasses this. For such a library offline means "the client is never constructed in an offline test and the data comes from recorded snapshots";
    this fixture cannot prove it.
    """

    def refuse(*args, **kwargs):
        raise RuntimeError("an offline test touched the network")

    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


def test_the_no_network_fixture_is_not_vacuous():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for call in (lambda: s.connect(("127.0.0.1", 9)), lambda: s.connect_ex(("127.0.0.1", 9)), lambda: s.sendto(b"x", ("127.0.0.1", 9)), lambda: socket.getaddrinfo("localhost", 80)):
            with pytest.raises(RuntimeError, match="offline test touched the network"):
                call()
    finally:
        s.close()


def test_wrap_reads_the_snapshot_and_the_library():
    src = SimpleNamespace(ts=pd.Timestamp("2024-06-12 16:00", tz="America/New_York"), reference_date=dt.date(2024, 6, 12))
    p = wrap(src)
    assert p.reference_date == dt.date(2024, 6, 12) and p.zz_version == "0.0.1"
```

The body of the last test is the toy adapter of the dry run. A real adapter replaces it with tests on recorded or synthetic snapshots (`pricebt-wrap-and-pricer`, `pricebt-conformance-and-tieout`). The `no_network` fixture must stay in every offline file.

## 3. `tests/test_<lib>_no_infra.py` (marker `adapter_<lib>`)

Importing the adapter must not drag in another banned root (another pricing library, or the maintainer's data infrastructure). The list is read from `tests/guards/banned.yaml`, so it grows with the guards:

```python
"""Importing the adapter pulls in no OTHER banned import root (another pricing library, the maintainer's data infrastructure). Needs the optional extra `zz-lib`."""
import os
import subprocess
import sys

import pytest

pytest.importorskip("ZzLib")
pytestmark = pytest.mark.adapter_zz

from guards import scan  # noqa: E402

OTHERS = sorted(set(scan.load_banned()["import_roots"]) - {"ZzLib"})
# the child keeps the parent's PYTHONPATH: a library made importable that way (not by pip) must stay visible to it
ENV = {**os.environ, "PYTHONPATH": os.pathsep.join(["src", os.environ.get("PYTHONPATH", "")])}


def test_the_adapter_imports_no_other_banned_root():
    code = "import sys, pricebt.contrib.zz; print([m for m in sys.modules if m.split('.')[0] in %r])" % OTHERS
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=scan.PROJECT, env=ENV)
    assert out.stdout.strip() == "[]", out.stdout + out.stderr
```

The repository's own guard children run with `PYTHONPATH=src` ONLY (`tests/guards/test_guards.py::_child_env`), which is why `ENV` above is not copied from them.

## 4. `tests/test_<lib>_live.py` (marker `adapter_<lib>`, opt-in)

```python
"""Opt-in LIVE test of the adapter: needs the real service (network, credentials read from the environment, never from a file in the repo).

Skipped unless PRICEBT_LIVE_ZZ=1. Every offline test lives in the smoke file and needs none of this.
"""
import os

import pytest

pytest.importorskip("ZzLib")
pytestmark = [pytest.mark.adapter_zz,
              pytest.mark.skipif(os.environ.get("PRICEBT_LIVE_ZZ") != "1", reason="opt-in live test: set PRICEBT_LIVE_ZZ=1 (needs the service and its credentials in the environment)")]


def test_live_discount_matches_the_closed_form():
    import ZzLib

    assert ZzLib.discount(4.0, 1.0) == pytest.approx(1 / 1.04)
```

Do NOT put the `no_network` fixture in the live file. Read credentials inside the test from `os.environ`, never at import time (an import-time read would fail every collection where the variable is absent).

## 5. `tests/test_<lib>_private_core_clean.py` (marker `core`; ONLY for a confidential library)

When the library's name must not enter the public tree, leave `tests/guards/banned.yaml` untouched and add this one file to your PRIVATE copy. It appends your entries to the loaded data in memory and runs the guards' own scan functions over core, so a core file that imports or names the library is reported. It covers Z1 (imports at any depth), Z2a (identifiers and string literals), Z2b and Z2c (schema and config values); it does not replace the blocker run of GT-Z1a (which imports every core module with the ban installed). Never contribute the file upstream: it names the library.

```python
"""Confidential library: the public tree stays untouched (no entry in banned.yaml, no marker, no extra). This PRIVATE test guards core with the entries added in memory.

`core` (needs no library). Never contribute this file upstream: it names the library.
"""
import copy

import pytest

from guards import scan

pytestmark = pytest.mark.core

ROOT, TOKEN = "ZzLib", "zzlib"  # THE ONLY LINE TO EDIT (import root, lower-case name)


def banned_with_the_library():
    banned = copy.deepcopy(scan.load_banned())
    banned["import_roots"].append(ROOT)
    banned["executable_tokens"].append({"token": TOKEN, "match": "substring"})
    return banned


def test_core_imports_and_names_nothing_of_the_private_library():
    banned, src, cfg = banned_with_the_library(), scan.PROJECT / "src", scan.PROJECT / "configs"
    hits = []
    for p in scan.core_py_files(src):
        hits += scan.scan_imports(p, src, banned) + scan.scan_names(p, src, banned)
    for p in scan.core_schema_files(src):
        hits += scan.scan_yaml(p, src, banned)
    for p in scan.core_config_files(cfg):
        hits += scan.scan_yaml(p, cfg, banned)
    assert not hits, "\n".join(map(str, hits))


def test_twin_the_same_entries_report_a_planted_import_and_a_planted_literal(tmp_path):
    (tmp_path / "a.py").write_text(f"def f():\n    import {ROOT}\n", encoding="utf8")
    (tmp_path / "b.py").write_text(f"NAME = '{TOKEN}'\n", encoding="utf8")
    banned = banned_with_the_library()
    assert [h.token for h in scan.scan_imports(tmp_path / "a.py", tmp_path, banned)] == [ROOT]
    assert [h.token for h in scan.scan_names(tmp_path / "b.py", tmp_path, banned)] == [TOKEN]
```

Observed (`references/checklist.md` 3.14): both tests pass with the public `banned.yaml` untouched; `import ZzLib` appended to a core module fails the first test with `AssertionError: pricebt/common.py:<n> [import] 'ZzLib' in 'import ZzLib'`, and `X = "zzlib"` with `[literal] 'zzlib' in 'zzlib'`. The adapter tests of a confidential library live in your private package directory (not subject to the partition hook) or, in a private fork, in `tests/` with the marker of step 8.
