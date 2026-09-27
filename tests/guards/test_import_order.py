"""DESIGN.md section 12.2 item 5: each pricebt module, imported ALONE in a fresh subprocess, must
succeed, and sys.modules must contain no pricebt module from a strictly later import-DAG tier
(guards/dag.py). This must stay non-vacuous once P1-P3 fill the stub modules in (IMPLEMENTATION_PLAN
P0.1 trap 2): the assertion checks the actual DAG position of every module sys.modules picked up,
not just that the import did not crash. Two more tests check the DAG text's own named MUST clauses
for `markets`, `instrument` and `risk.results`, which are stronger than the generic tier rule.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from guards import dag

pytestmark = pytest.mark.core


def _import_alone(module_suffix: str) -> subprocess.CompletedProcess:
    full = dag.full_name(module_suffix)
    code = "import sys, json\n" f"import {full}\n" "print(json.dumps(sorted(n for n in sys.modules if n == 'pricebt' or n.startswith('pricebt.'))))\n"
    env = {**os.environ, "PYTHONPATH": str(dag.SRC), "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=str(dag.PROJECT), timeout=60)


def _loaded_suffixes(stdout: str) -> set:
    names = json.loads(stdout.strip().splitlines()[-1])
    return {("" if n == "pricebt" else n[len("pricebt.") :]) for n in names}


@pytest.mark.parametrize("module_suffix", [""] + dag.ALL_MODULE_NAMES)
def test_module_imports_alone_and_pulls_in_nothing_from_a_later_dag_tier(module_suffix):
    p = _import_alone(module_suffix)
    assert p.returncode == 0, f"import of {dag.full_name(module_suffix)} failed:\n{p.stderr}"
    loaded = _loaded_suffixes(p.stdout)
    unknown = loaded - set(dag.TIER_OF)
    assert not unknown, f"loaded module(s) missing from the skeleton's DAG: {sorted(unknown)}"
    my_tier = dag.TIER_OF[module_suffix]
    too_late = {s for s in loaded if dag.TIER_OF[s] > my_tier}
    assert not too_late, f"importing {dag.full_name(module_suffix)} alone pulled in later-tier module(s): {sorted(too_late)}"


@pytest.mark.parametrize("module_suffix", sorted(dag.FORBIDDEN_TOP_LEVEL))
def test_named_must_not_import_at_top_level_clauses(module_suffix):
    """DESIGN.md section 3.2's own explicit clauses: `markets` must not top-level-import portfolio,
    instrument, session or assets; `instrument` imports session and markets only inside method
    bodies; `risk.results` must not import `risk.transform`. These are stronger than the generic
    tier rule, which alone would allow an earlier-tier sibling to be pulled in eagerly."""
    p = _import_alone(module_suffix)
    assert p.returncode == 0, p.stderr
    loaded = _loaded_suffixes(p.stdout)
    forbidden = dag.FORBIDDEN_TOP_LEVEL[module_suffix]
    hit = loaded & forbidden
    assert not hit, f"{dag.full_name(module_suffix)} alone must not import {sorted(forbidden)}; got {sorted(hit)} in sys.modules"
