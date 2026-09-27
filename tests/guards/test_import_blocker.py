"""DESIGN.md section 12.2 item 3: a sys.meta_path finder blocks gs_quant, rateslib, QuantLib, MDP,
Query, Caching and dataclasses_json, and every pricebt module still imports cleanly under it."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from guards import dag

pytestmark = pytest.mark.core


def _run(code: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": f"{dag.SRC}{os.pathsep}{dag.HERE}", "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=str(dag.PROJECT), timeout=120)


def test_every_pricebt_module_imports_cleanly_under_the_blocker():
    modules = ["pricebt"] + [dag.full_name(m) for m in dag.ALL_MODULE_NAMES]
    code = "import blocker; blocker.install()\n" + "\n".join(f"import {m}" for m in modules) + "\nprint('OK')\n"
    p = _run(code)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "OK" in p.stdout


def test_the_blocker_raises_importerror_for_a_banned_root():
    """gs_quant 1.4.26 is genuinely importable in this environment (docs/v2/README.md), so this
    proves the finder actually intercepts it rather than the import failing on its own."""
    p = _run("import blocker; blocker.install()\nimport gs_quant\n")
    assert p.returncode != 0
    assert "blocked by the pricebt import blocker" in p.stdout + p.stderr


def test_the_blocker_sees_an_import_hidden_inside_a_try_block(tmp_path):
    (tmp_path / "lazy_probe.py").write_text(
        "def go():\n    try:\n        import rateslib\n        return True\n    except ImportError:\n        return False\n", encoding="utf8"
    )
    env = {**os.environ, "PYTHONPATH": f"{dag.HERE}{os.pathsep}{tmp_path}", "PYTHONDONTWRITEBYTECODE": "1"}
    code = "import blocker; f = blocker.install()\nimport lazy_probe\nprint(lazy_probe.go(), f.attempts)\n"
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=60)
    assert p.stdout.strip() == "False ['rateslib']", p.stdout + p.stderr
