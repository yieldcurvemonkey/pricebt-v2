"""pricebt must not depend on ARBS: no ARBS module is imported (or even named as a package) anywhere under src/, the ARBS bridge lives outside
the package (tools/arbs_live.py), and loading/running the default adapters never touches ARBS."""
import ast
import subprocess
import sys
from pathlib import Path

import pytest

from pricebt.config import yamlio
from pricebt.config.loader import build
from pricebt.errors import ConfigError

pytestmark = pytest.mark.core

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "pricebt"
ARBS_TOP_LEVEL = {"MDP", "Query", "Caching", "TB", "BT", "RVUtils", "SDRUtils", "Simulation", "definitions"}


def _imported_roots(path):
    tree = ast.parse(path.read_text(encoding="utf8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_no_module_under_src_imports_an_arbs_package():
    offenders = {str(f.relative_to(ROOT)): sorted(_imported_roots(f) & ARBS_TOP_LEVEL) for f in SRC.rglob("*.py")}
    assert {k: v for k, v in offenders.items() if v} == {}


def test_check_is_not_vacuous_it_sees_the_bridge_outside_the_package():
    assert _imported_roots(ROOT / "tools" / "arbs_live.py") & ARBS_TOP_LEVEL  # the only place ARBS code is ever imported (lazily, opt-in)


def test_package_has_no_arbs_named_module():
    assert not [p for p in SRC.rglob("*") if "arbs" in p.name.lower() and "__pycache__" not in p.parts]


def test_the_reference_stack_imports_without_arbs_on_the_path():
    code = ("import sys, pricebt.testing.refstack;"
            "bad=[m for m in sys.modules if m.split('.')[0] in %r];print(bad)" % sorted(ARBS_TOP_LEVEL))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, env={**__import__("os").environ, "PYTHONPATH": "src"})
    assert out.stdout.strip() == "[]", out.stdout + out.stderr


BASE = """
name: plugin
backtest: {tz: America/New_York, grid: {start: 2024-01-02, end: 2024-01-31, freq: 1b}, progress: {show: false}}
market: {mdps: {rates: {type: toy}}, pricers: {primary: {mdp: rates}}}
instruments: {fwd: {asset_class: toy, factory: "pricebt.testing.toys:forward", conventions: {strike: 4.0}}}
strategy: {triggers: []}
"""


def test_registry_import_plugin_seam_is_allow_listed():
    cfg = yamlio.loads(BASE)
    cfg["registry"] = {"import": ["pricebt.testing.toys"]}
    build(cfg)  # an allowed module imports fine
    cfg["registry"] = {"import": ["os"]}
    with pytest.raises(ConfigError) as e:
        build(cfg)
    assert e.value.code == "CFG-ALLOW"
