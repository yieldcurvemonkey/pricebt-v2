"""The test-support providers stay library-free (spec D2, SC7): the AST import scanner finds no banned import in any file of `tests/support`, and a child interpreter
under the import blocker imports every module of the package without even ATTEMPTING a banned import. Both checks prove they can fail (planted violations).
The dotted class paths that configs will name must also be free of the banned executable tokens."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from guards import scan

pytestmark = pytest.mark.core

PROJECT = scan.PROJECT
SUPPORT = Path(__file__).resolve().parent / "support"
FILES = sorted(SUPPORT.glob("*.py"))
CHILD = (
    "import importlib, json, sys\n"
    "sys.path[:0] = [{src!r}, {tests!r}] + {extra!r}\n"
    "from guards import blocker\n"
    "f = blocker.install()\n"
    "mods = {mods!r}\n"
    "for m in mods:\n"
    "    importlib.import_module(m)\n"
    "libs = sorted(m for m in sys.modules if m.split('.')[0] in ('rateslib', 'QuantLib', 'gs_quant'))\n"
    "print(json.dumps({{'imported': len(mods), 'attempts': f.attempts, 'libs': libs}}))\n"
)


def child(mods, extra=()):
    code = CHILD.format(src=str(PROJECT / "src"), tests=str(PROJECT / "tests"), mods=list(mods), extra=[str(e) for e in extra])
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(PROJECT), env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_the_scanned_package_is_not_empty():
    """A guard that scans nothing passes vacuously."""
    assert {p.stem for p in FILES} >= {"__init__", "common", "curves", "ust_common", "ust_eod", "ust_minute", "known", "tiny"}


@pytest.mark.parametrize("path", FILES, ids=[p.name for p in FILES])
def test_no_banned_import_in_the_support_package_at_any_depth(path):
    hits = scan.scan_imports(path, PROJECT)
    assert not hits, "\n".join(str(h) for h in hits)


def test_the_import_scanner_sees_every_kind_of_planted_import(tmp_path):
    planted = {
        "a.py": "import rateslib\n",
        "b.py": "try:\n    import QuantLib as ql\nexcept ImportError:\n    ql = None\n",
        "c.py": "def f():\n    from gs_quant.markets import x\n    return x\n",
        "d.py": "import importlib\nm = importlib.import_module('MDP')\n",
        "e.py": "from rateslib.curves import Curve\n",
    }
    for name, text in planted.items():
        (tmp_path / name).write_text(text, encoding="utf8")
        hits = scan.scan_imports(tmp_path / name, tmp_path)
        assert len(hits) == 1 and hits[0].file == name, (name, hits)
    (tmp_path / "ok.py").write_text("import numpy\nimport pandas as pd\nfrom pricebt.snapshot import CurveSnapshot\n", encoding="utf8")
    assert scan.scan_imports(tmp_path / "ok.py", tmp_path) == []


def test_every_module_of_the_package_imports_under_the_blocker_without_attempting_a_banned_import():
    mods = ["support"] + [f"support.{p.stem}" for p in FILES if p.stem != "__init__"]
    got = child(mods)
    assert got["imported"] == len(mods) >= 8 and got["attempts"] == [] and got["libs"] == []


def test_the_blocker_child_reports_a_planted_hidden_import(tmp_path):
    """Non-vacuity: a module that imports a banned library inside a try/except is still caught, because the blocker sees the ATTEMPT."""
    (tmp_path / "zbad_support.py").write_text("try:\n    import rateslib\nexcept ImportError:\n    rateslib = None\n", encoding="utf8")
    got = child(["zbad_support"], extra=[tmp_path])
    assert got["attempts"] == ["rateslib"] and got["libs"] == []


def test_the_dotted_paths_configs_will_name_are_free_of_banned_executable_tokens():
    from support.common import BOND_CALENDAR, FIXINGS_NAME, SWAP_CALENDAR
    from support.curves import CurveStore
    from support.ust_eod import UstEod
    from support.ust_minute import UstMinute

    banned = scan.load_banned()
    pats = scan._compiled(banned["executable_tokens"])
    for cls in (CurveStore, UstEod, UstMinute):
        text = f"{cls.__module__}:{cls.__name__}"
        assert text.startswith("support.")
        assert not [t for t, p in pats if p.search(text)], text
    for text in (SWAP_CALENDAR, BOND_CALENDAR, FIXINGS_NAME):
        assert not [t for t, p in pats if p.search(text)], text
    assert (SWAP_CALENDAR, BOND_CALENDAR) == ("usd_fed", "us_govt")
    assert [t for t, p in pats if p.search("support.ust_eod:FedInvestPanel")] == ["fedinvest"], "control: the token matcher would flag a vendor name"
