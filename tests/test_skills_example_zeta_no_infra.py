"""The service example (`skills/pricebt-wire-external-library/example-service/`): importing the zeta adapter pulls in no banned import root (another pricing library, the maintainer's data
infrastructure), and importing pricebt itself pulls in neither zeta nor the adapter (core works without the library). The list of roots is read from `tests/guards/banned.yaml`, so it grows with the
guards. The children get EXACTLY the PYTHONPATH the example documents (`src`, `tests`, the example-service directory), so this test also proves that one entry is enough to import the adapter.
Needs no pricing library (zeta is fictional): marker `core`."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
EXAMPLE = PROJECT / "skills" / "pricebt-wire-external-library" / "example-service"
for _p in (str(EXAMPLE), str(EXAMPLE / "zeta_lib")):  # not on pytest.ini's pythonpath: add them from this file's location, never from the working directory
    if _p not in sys.path:
        sys.path.insert(0, _p)

import zeta  # noqa: E402,F401  (the fictional service client: a missing one is a loud failure, not a skip)
from guards import scan  # noqa: E402

pytestmark = pytest.mark.core

OTHERS = sorted(set(scan.load_banned()["import_roots"]) - {"zeta"})
ENV = {**os.environ, "PYTHONPATH": os.pathsep.join(["src", "tests", str(EXAMPLE)])}  # what the README says, nothing more: zeta itself is found through the adapter's `_compat`


def _child(code):
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=PROJECT, env=ENV)


def test_the_adapter_imports_no_other_banned_root():
    assert OTHERS, "the banned list is empty: this test would prove nothing"
    out = _child("import sys, zeta_adapter; print([m for m in sys.modules if m.split('.')[0] in %r])" % OTHERS)
    assert out.stdout.strip() == "[]", out.stdout + out.stderr


def test_core_imports_neither_zeta_nor_the_adapter():
    out = _child("import sys, pricebt, pricebt.api, pricebt.tieout, pricebt.testing.refstack; print([m for m in sys.modules if m.split('.')[0] in ('zeta', 'zeta_adapter', 'zeta_mistakes') or m.startswith('pricebt.contrib')])")
    assert out.stdout.strip() == "[]", out.stdout + out.stderr


def test_twin_the_child_check_can_fail():
    """Non-vacuity of the two checks above: the same child code DOES report a root once it is imported (zeta_adapter itself is imported here, and `zeta` comes with it)."""
    out = _child("import sys, zeta_adapter; print(sorted(m for m in sys.modules if m.split('.')[0] in ('zeta', 'zeta_adapter') and '.' not in m))")
    assert out.stdout.strip() == "['zeta', 'zeta_adapter']", out.stdout + out.stderr
