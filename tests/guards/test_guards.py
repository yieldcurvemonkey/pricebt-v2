"""Zero-dependence guards GT-Z1..Z5 (spec 9.1). One parametrised case per core file, so the RED cases are the refactor worklist.

The non-vacuity twins (spec 9.2) live in test_twins.py and run the SAME scan functions against a temporary tree with one planted violation.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from guards import scan

pytestmark = pytest.mark.core

PROJECT = scan.PROJECT
SRC = PROJECT / "src"
BANNED = scan.load_banned()

CORE_PY = scan.core_py_files(SRC)
ALL_PY = scan.all_py_files(SRC)
SCHEMAS = scan.core_schema_files(SRC)
CONFIGS = scan.core_config_files(PROJECT / "configs")


def _ids(paths):
    return [p.relative_to(PROJECT).as_posix() for p in paths]


def _fmt(hits):
    return "\n".join(str(h) for h in hits)


def test_the_scanned_file_sets_are_not_empty():
    """A guard that scans nothing passes vacuously."""
    assert len(CORE_PY) > 20 and len(SCHEMAS) >= 3 and len(CONFIGS) >= 1


def test_banned_list_is_one_data_file_with_every_section():
    assert scan.BANNED_FILE.name == "banned.yaml"
    for key in ("import_roots", "executable_tokens", "schema_arg_names", "convention_tokens", "convention_divisors", "convention_allow", "prose_tokens"):
        assert BANNED[key], key
    assert {"rateslib", "QuantLib", "gs_quant"} <= set(BANNED["import_roots"])


# ---------------------------------------------------------------------------------------- Z1
def _child_env():
    return {**os.environ, "PYTHONPATH": str(SRC), "PYTHONDONTWRITEBYTECODE": "1"}


def test_gt_z1a_every_core_module_imports_with_the_blocker_installed():
    out = subprocess.run([sys.executable, str(scan.HERE / "import_all.py")], capture_output=True, text=True, cwd=PROJECT, env=_child_env())
    assert out.returncode == 0, out.stderr[-2000:]
    res = json.loads(out.stdout.strip().splitlines()[-1])
    assert res["imported"] > 20
    assert res["failed"] == {}, res["failed"]


@pytest.mark.parametrize("path", CORE_PY, ids=_ids(CORE_PY))
def test_gt_z1_no_banned_import_in_core_at_any_depth(path):
    """CORE_PY excludes contrib/, the only place an adapter MAY import its library (spec 1.3); ARBS roots are checked everywhere by Z5a."""
    hits = scan.scan_imports(path, SRC)
    assert not hits, _fmt(hits)


def test_gt_z1b_the_core_suite_passes_with_the_blocker_installed():
    """Nested run of `pytest -m core` with the blocker (spec 9.3). The child skips this very test to avoid recursion."""
    if os.environ.get("PRICEBT_CORE_ONLY_CHILD"):
        pytest.skip("child of the core-only run: recursion guard")  # the parent run proves the same thing once
    env = {**_child_env(), "PRICEBT_CORE_ONLY_CHILD": "1"}
    out = subprocess.run([sys.executable, str(scan.HERE / "blocker.py"), "-m", "core", "-o", "addopts=", "-q", "-p", "no:cacheprovider", "--no-header", "-x", "-k", "not gt_z1b"],
                         capture_output=True, text=True, cwd=PROJECT, env=env, timeout=1800)
    tail = (out.stdout + out.stderr)[-3000:]
    assert out.returncode == 0, tail
    assert " passed" in tail, tail


# ---------------------------------------------------------------------------------------- Z2
@pytest.mark.parametrize("path", CORE_PY, ids=_ids(CORE_PY))
def test_gt_z2a_no_banned_token_in_core_identifiers_or_string_literals(path):
    hits = scan.scan_names(path, SRC)
    assert not hits, _fmt(hits)


@pytest.mark.parametrize("path", SCHEMAS, ids=_ids(SCHEMAS))
def test_gt_z2b_no_banned_token_in_schema_keys_or_values(path):
    hits = scan.scan_yaml(path, SRC) + scan.scan_schema_args(path, SRC)
    assert not hits, _fmt(hits)


@pytest.mark.parametrize("path", CONFIGS, ids=_ids(CONFIGS))
def test_gt_z2c_no_banned_token_in_shipped_core_configs(path):
    hits = scan.scan_yaml(path, PROJECT / "configs")
    assert not hits, _fmt(hits)


# ---------------------------------------------------------------------------------------- Z4
@pytest.mark.parametrize("path", CORE_PY, ids=_ids(CORE_PY))
def test_gt_z4_no_pricing_convention_in_core(path):
    hits = scan.scan_conventions(path, SRC)
    assert not hits, _fmt(hits)


# ---------------------------------------------------------------------------------------- Z5
@pytest.mark.parametrize("path", ALL_PY, ids=_ids(ALL_PY))
def test_gt_z5a_no_src_module_imports_an_arbs_package(path):
    hits = [h for h in scan.scan_imports(path, SRC) if h.token in {"MDP", "Query", "Caching", "TB", "BT", "RVUtils", "SDRUtils", "Simulation", "definitions"}]
    assert not hits, _fmt(hits)


def test_gt_z5a_no_module_or_path_under_src_is_named_arbs():
    assert scan.package_names_with(SRC, "arbs") == []


@pytest.mark.parametrize("path", ALL_PY, ids=_ids(ALL_PY))
def test_gt_z5b_arbs_is_not_named_in_src_code_or_prose(path):
    """Executable content (identifiers, literals) AND prose (comments, docstrings): ARBS is a test case only (spec 10, G1)."""
    hits = [h for h in scan.scan_names(path, SRC) if h.token == "arbs"] + scan.scan_prose(path, SRC)
    assert not hits, _fmt(hits)
