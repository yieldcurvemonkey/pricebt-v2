"""Non-vacuity twins (spec 9.2): each guard is run against a temporary tree with ONE planted violation and must report exactly that violation.

Every twin also runs the same guard on the CLEAN tree (control) and on the allowed variants (docstring mention, allow-listed path), so a guard that
flags everything, or nothing, fails here.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

from guards import scan

pytestmark = pytest.mark.core

BANNED = scan.load_banned()
SRC_REAL = scan.PROJECT / "src"


def tree(root: Path, files: Dict[str, str]) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text), encoding="utf8")
    return root


def sig(hits: List[scan.Hit]) -> List[Tuple[str, int, str]]:
    return [(h.file, h.line, h.token) for h in hits]


CLEAN = """
    '''A neutral module. Docstring prose may mention rateslib or QuantLib as provenance.'''
    import math

    def f(x):
        return math.sqrt(x)
"""


# ---------------------------------------------------------------------------------------- Z1 static
def test_twin_z1_static_sees_an_import_hidden_inside_a_function_and_a_try_block(tmp_path):
    src = tree(tmp_path, {"pricebt/__init__.py": "", "pricebt/clean.py": CLEAN, "pricebt/bad.py": "def price():\n    try:\n        import rateslib\n    except ImportError:\n        pass\n"})
    assert scan.scan_imports(src / "pricebt" / "clean.py", src) == []
    assert sig(scan.scan_imports(src / "pricebt" / "bad.py", src)) == [("pricebt/bad.py", 3, "rateslib")]


def test_twin_z1_static_sees_from_imports_importlib_and_dunder_import(tmp_path):
    src = tree(tmp_path, {"pricebt/__init__.py": "", "pricebt/a.py": "from QuantLib import Date\n", "pricebt/b.py": "import importlib\nm = importlib.import_module('gs_quant.markets')\n",
                          "pricebt/c.py": "m = __import__('rateslib')\n"})
    assert sig(scan.scan_imports(src / "pricebt" / "a.py", src)) == [("pricebt/a.py", 1, "QuantLib")]
    assert sig(scan.scan_imports(src / "pricebt" / "b.py", src)) == [("pricebt/b.py", 2, "gs_quant")]
    assert sig(scan.scan_imports(src / "pricebt" / "c.py", src)) == [("pricebt/c.py", 1, "rateslib")]


def test_twin_z1_static_a_relative_import_of_a_local_module_named_like_a_library_is_not_an_external_import(tmp_path):
    src = tree(tmp_path, {"pricebt/__init__.py": "", "pricebt/a.py": "from .rateslib import x\n"})
    assert scan.scan_imports(src / "pricebt" / "a.py", src) == []  # (its NAME is caught by Z2, not Z1)
    assert sig(scan.scan_names(src / "pricebt" / "a.py", src)) == [("pricebt/a.py", 1, "rateslib")]


# ---------------------------------------------------------------------------------------- Z1a runtime blocker
def _run_import_all(src: Path) -> dict:
    env = {**os.environ, "PYTHONPATH": str(src), "PYTHONDONTWRITEBYTECODE": "1"}
    out = subprocess.run([sys.executable, str(scan.HERE / "import_all.py")], capture_output=True, text=True, env=env, cwd=src)
    assert out.returncode == 0, out.stderr[-1500:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_twin_gt_z1a_reports_a_core_module_that_imports_a_banned_library_at_import_time(tmp_path):
    src = tree(tmp_path, {"pricebt/__init__.py": "", "pricebt/ok.py": CLEAN, "pricebt/bad.py": "import rateslib\n", "pricebt/contrib/__init__.py": "",
                          "pricebt/contrib/adapter.py": "import rateslib\n"})
    res = _run_import_all(src)
    assert set(res["failed"]) == {"pricebt.bad"}, res  # contrib is exempt, ok.py imports
    assert "ModuleNotFoundError" in res["failed"]["pricebt.bad"]
    assert res["imported"] == 1  # only pricebt.ok


def test_twin_gt_z1a_control_a_clean_package_has_no_failures(tmp_path):
    src = tree(tmp_path, {"pricebt/__init__.py": "", "pricebt/ok.py": CLEAN})
    assert _run_import_all(src)["failed"] == {}


def test_twin_blocker_really_blocks_a_lazy_import_inside_a_try_block(tmp_path):
    """The blocker sees an import a `sys.modules` inspection would miss: the failure is swallowed by the module's own except, but it was attempted."""
    src = tree(tmp_path, {"pricebt/__init__.py": "", "pricebt/lazy.py": "def go():\n    try:\n        import rateslib\n        return True\n    except ImportError:\n        return False\n"})
    code = ("import sys; sys.path.insert(0, %r); import blocker; f = blocker.install(); import pricebt.lazy as m; print(m.go(), f.attempts)" % str(scan.HERE))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(src)}, cwd=src)
    assert out.stdout.strip() == "False ['rateslib']", out.stdout + out.stderr


def test_twin_blocker_finder_matches_submodules_and_leaves_other_names_alone():
    sys.path.insert(0, str(scan.HERE))
    try:
        import blocker
    finally:
        sys.path.remove(str(scan.HERE))
    f = blocker.BannedRoots(["rateslib"])
    for name in ("rateslib", "rateslib.curves.base"):
        with pytest.raises(ModuleNotFoundError):
            f.find_spec(name)
    assert f.find_spec("rateslibx") is None and f.find_spec("pandas") is None and f.find_spec("pricebt.contrib.rateslib") is None
    assert f.attempts == ["rateslib", "rateslib.curves.base"]


# ---------------------------------------------------------------------------------------- Z1b runtime core-only run
def _core_only_child(tmp_path: Path, test_body: str) -> subprocess.CompletedProcess:
    ini = tmp_path / "pytest.ini"
    ini.write_text("[pytest]\nmarkers =\n    core: x\naddopts =\n", encoding="utf8")
    (tmp_path / "test_child.py").write_text("import pytest\npytestmark = pytest.mark.core\n" + textwrap.dedent(test_body), encoding="utf8")
    return subprocess.run([sys.executable, str(scan.HERE / "blocker.py"), "-c", str(ini), "--rootdir", str(tmp_path), "-m", "core", "-q", "-p", "no:cacheprovider", str(tmp_path / "test_child.py")],
                          capture_output=True, text=True, cwd=tmp_path, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, timeout=300)


def test_twin_gt_z1b_a_core_test_that_needs_rateslib_fails_under_the_blocker(tmp_path):
    out = _core_only_child(tmp_path, "def test_needs_lib():\n    import rateslib\n")
    assert out.returncode != 0
    assert "blocked by the pricebt import blocker" in out.stdout + out.stderr


def test_twin_gt_z1b_control_a_toy_only_core_test_passes_under_the_blocker(tmp_path):
    out = _core_only_child(tmp_path, "def test_toy():\n    assert 1 + 1 == 2\n")
    assert out.returncode == 0, out.stdout + out.stderr


# ---------------------------------------------------------------------------------------- Z2a names and literals
def test_twin_z2a_flags_identifiers_and_literals_but_not_docstrings(tmp_path):
    src = tree(tmp_path, {"pricebt/__init__.py": "", "pricebt/clean.py": CLEAN,
                          "pricebt/name.py": "def rl_swap():\n    return 1\n",
                          "pricebt/lit.py": "SPEC = 'usd_irs'\n",
                          "pricebt/word.py": "carbs = 1\nx = 'arbs'\n"})
    assert scan.scan_names(src / "pricebt" / "clean.py", src) == []
    assert ("pricebt/name.py", 1, "rl_") in sig(scan.scan_names(src / "pricebt" / "name.py", src))
    assert sig(scan.scan_names(src / "pricebt" / "lit.py", src)) == [("pricebt/lit.py", 1, "usd_irs")]
    assert sig(scan.scan_names(src / "pricebt" / "word.py", src)) == [("pricebt/word.py", 2, "arbs")]  # `carbs` is not the word arbs


def test_twin_z2a_flags_a_keyword_argument_name_and_a_default_value(tmp_path):
    src = tree(tmp_path, {"pricebt/__init__.py": "", "pricebt/k.py": "def f(x, spec='usd_irs'):\n    return g(rl_curve=x)\n", "pricebt/ok.py": "def f(curves):\n    return g(curves=curves)\n"})
    got = sig(scan.scan_names(src / "pricebt" / "k.py", src))
    assert ("pricebt/k.py", 1, "usd_irs") in got and ("pricebt/k.py", 2, "rl_") in got
    assert scan.scan_names(src / "pricebt" / "ok.py", src) == []  # `curves` is an ordinary word for snapshot data (only banned as a schema signature key)


# ---------------------------------------------------------------------------------------- Z2b / Z2c yaml
def test_twin_z2b_flags_a_library_argument_required_by_a_schema_and_ignores_doc_text(tmp_path):
    good = "asset_class: swap\nmethods:\n  value:\n    args: {ctx: MarkContext}\n    doc: 'rateslib users: curves come from the solver'\n"
    bad = "asset_class: swap\nmethods:\n  npv:\n    args: {curves: any}\n"
    tree(tmp_path, {"schemas/good.yaml": good, "schemas/bad.yaml": bad, "schemas/tok.yaml": "asset_class: swap\nmethods:\n  rl_npv: {}\n"})
    assert scan.scan_yaml(tmp_path / "schemas" / "good.yaml", tmp_path) == [] and scan.scan_schema_args(tmp_path / "schemas" / "good.yaml", tmp_path) == []
    assert [h.token for h in scan.scan_schema_args(tmp_path / "schemas" / "bad.yaml", tmp_path)] == ["curves"]
    assert [h.token for h in scan.scan_yaml(tmp_path / "schemas" / "tok.yaml", tmp_path)] == ["rl_"]


def test_twin_z2c_flags_a_config_value_but_not_a_doc_value(tmp_path):
    tree(tmp_path, {"configs/ok.yaml": "instruments:\n  x: {doc: 'built by rl_swap', factory: my_lib:build}\n", "configs/bad.yaml": "instruments:\n  x: {factory: rl_swap}\n"})
    assert scan.scan_yaml(tmp_path / "configs" / "ok.yaml", tmp_path / "configs") == []
    got = scan.scan_yaml(tmp_path / "configs" / "bad.yaml", tmp_path / "configs")
    assert [h.token for h in got] == ["rl_"]


def test_twin_z2c_file_selection_excludes_adapters_and_examples_only(tmp_path):
    tree(tmp_path, {"configs/a.yaml": "x: 1\n", "configs/suite/b.yaml": "x: 1\n", "configs/adapters/c.yaml": "x: 1\n", "configs/examples/d.yaml": "x: 1\n"})
    names = sorted(p.relative_to(tmp_path / "configs").as_posix() for p in scan.core_config_files(tmp_path / "configs"))
    assert names == ["a.yaml", "suite/b.yaml"]


# ---------------------------------------------------------------------------------------- Z4 conventions
def test_twin_z4_flags_a_day_count_division_and_a_convention_literal_but_not_the_allow_listed_path(tmp_path):
    src = tree(tmp_path, {"pricebt/__init__.py": "", "pricebt/clean.py": CLEAN, "pricebt/div.py": "def yf(a, b):\n    return (b - a) / 360\n",
                          "pricebt/lit.py": "DC = 'ACT/360'\n", "pricebt/backtests/compat.py": "def accrue(r, d):\n    return (1 + r / 365.0) ** d\n"})
    assert scan.scan_conventions(src / "pricebt" / "clean.py", src) == []
    assert ("pricebt/div.py", 2, "/360") in sig(scan.scan_conventions(src / "pricebt" / "div.py", src))
    assert ("pricebt/lit.py", 1, "act/360") in sig(scan.scan_conventions(src / "pricebt" / "lit.py", src))
    assert scan.scan_conventions(src / "pricebt" / "backtests" / "compat.py", src) == []  # labelled compat module is allow-listed
    # the same code OUTSIDE the allow-listed path is caught
    (src / "pricebt" / "compat_copy.py").write_text((src / "pricebt" / "backtests" / "compat.py").read_text(), encoding="utf8")
    assert [h.token for h in scan.scan_conventions(src / "pricebt" / "compat_copy.py", src)] == ["/365.0"]


def test_twin_z4_word_delimiting_a_numpy_busday_count_is_not_a_day_count_convention(tmp_path):
    src = tree(tmp_path, {"pricebt/__init__.py": "", "pricebt/a.py": "import numpy as np\nn = np.busday_count('2024-01-01', '2024-02-01')\n",
                          "pricebt/b.py": "def f(day_count):\n    return day_count\n"})
    assert scan.scan_conventions(src / "pricebt" / "a.py", src) == []
    assert {h.token for h in scan.scan_conventions(src / "pricebt" / "b.py", src)} == {"day_count"}


# ---------------------------------------------------------------------------------------- Z5
def test_twin_z5a_flags_an_arbs_import_root(tmp_path):
    src = tree(tmp_path, {"pricebt/__init__.py": "", "pricebt/a.py": "from MDP import get_pricer\n"})
    assert sig(scan.scan_imports(src / "pricebt" / "a.py", src)) == [("pricebt/a.py", 1, "MDP")]


def test_twin_z5a_flags_a_module_or_path_named_arbs(tmp_path):
    tree(tmp_path, {"pricebt/__init__.py": "", "pricebt/arbs_bridge.py": "", "pricebt/fine.py": ""})
    assert scan.package_names_with(tmp_path, "arbs") == ["pricebt/arbs_bridge.py"]


def test_twin_z5b_flags_prose_and_code_mentions_of_the_word_arbs(tmp_path):
    src = tree(tmp_path, {"pricebt/__init__.py": "", "pricebt/clean.py": CLEAN, "pricebt/c.py": "x = 1  # mirrors the ARBS pattern\n", "pricebt/d.py": "'''Docstring naming ARBS.'''\n"})
    assert scan.scan_prose(src / "pricebt" / "clean.py", src) == []
    assert sig(scan.scan_prose(src / "pricebt" / "c.py", src)) == [("pricebt/c.py", 1, "arbs")]
    assert sig(scan.scan_prose(src / "pricebt" / "d.py", src)) == [("pricebt/d.py", 1, "arbs")]


# ---------------------------------------------------------------------------------------- file sets
def test_twin_core_file_set_excludes_contrib_and_the_all_set_includes_it(tmp_path):
    tree(tmp_path, {"pricebt/__init__.py": "", "pricebt/a.py": "", "pricebt/sub/b.py": "", "pricebt/contrib/__init__.py": "", "pricebt/contrib/x.py": ""})
    core = sorted(p.relative_to(tmp_path).as_posix() for p in scan.core_py_files(tmp_path))
    allf = sorted(p.relative_to(tmp_path).as_posix() for p in scan.all_py_files(tmp_path))
    assert core == ["pricebt/__init__.py", "pricebt/a.py", "pricebt/sub/b.py"]
    assert set(core) <= set(allf) and set(allf) - set(core) == {"pricebt/contrib/__init__.py", "pricebt/contrib/x.py"}


# ---------------------------------------------------------------------------------------- the banned list itself
def test_twin_a_banned_token_is_easy_to_add_and_the_scan_picks_it_up(tmp_path):
    import copy

    b = copy.deepcopy(BANNED)
    b["executable_tokens"].append({"token": "zzz_new_token", "match": "substring"})
    src = tree(tmp_path, {"pricebt/__init__.py": "", "pricebt/a.py": "x = 'zzz_new_token'\n"})
    assert scan.scan_names(src / "pricebt" / "a.py", src) == []  # not banned in the real list
    assert sig(scan.scan_names(src / "pricebt" / "a.py", src, b)) == [("pricebt/a.py", 1, "zzz_new_token")]


def test_twin_z4_flags_a_day_count_hidden_in_a_product_or_a_named_constant(tmp_path):
    src = tree(tmp_path, {
        "pricebt/__init__.py": "",
        "pricebt/product.py": "def years(seconds):\n    return seconds / (365.25 * 86400)\n",
        "pricebt/named.py": "DAYS = 365.0\n\n\ndef years(days):\n    return days / DAYS\n",
        "pricebt/named_product.py": "TAU = 365\n\n\ndef years(secs):\n    return secs / (TAU * 24 * 3600)\n",
        "pricebt/chained.py": "def f(x):\n    return x / 2 / 360\n",
        "pricebt/benign.py": "SECONDS = 86400\nHALF = 2.0\n\n\ndef f(x, y):\n    return x / SECONDS + y / HALF + x / (y * 24)\n",
    })
    for name, tok in (("product", "/365.25"), ("named", "/365.0"), ("named_product", "/365"), ("chained", "/360")):
        got = scan.scan_conventions(src / "pricebt" / f"{name}.py", src)
        assert [h.token for h in got] == [tok], (name, got)
    assert scan.scan_conventions(src / "pricebt" / "benign.py", src) == [], "divisors that are not day counts are left alone"
