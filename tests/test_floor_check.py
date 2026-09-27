"""`tools/floor_check.py` on known answers: a checker that reports success on everything hides exactly what it was built to find."""
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.core

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import floor_check as F  # noqa: E402

LOG = """# log
## 1. Baseline
| test_alpha.py::test_in_section_one | this row is NOT section 2 |
## 2. Tests removed or changed
| `test_beta::test_gone` | DELETED | reason | B8 |
| `test_gamma.py` (all 3 tests) | REWRITTEN | reason | F-3 |
| `test_delta::test_renamed` | RENAMED | reason | S2 |
## 3. Tolerances
| test_alpha.py::test_after_section_two | not section 2 either |
## 12. Reviews
| `test_epsilon::test_in_twelve` | DELETED | a row of section 12 counts |
"""
BASE = [
    "tests/test_alpha.py::test_kept", "tests/test_alpha.py::test_in_section_one", "tests/test_alpha.py::test_after_section_two",
    "tests/test_beta.py::test_gone", "tests/test_beta.py::test_lost_silently", "tests/test_epsilon.py::test_in_twelve",
    "tests/test_gamma.py::test_a", "tests/test_gamma.py::test_b[param1]", "tests/test_delta.py::test_renamed", "tests/test_delta.py::test_renamed_but_prefix",
]


def run(current):
    return F.classify(BASE, current, LOG)


def test_present_explained_and_unexplained_are_told_apart():
    r = run(["tests/test_alpha.py::test_kept", "tests/test_alpha.py::test_new"])
    assert r["present"] == ["tests/test_alpha.py::test_kept"]
    assert r["explained"] == ["tests/test_beta.py::test_gone", "tests/test_delta.py::test_renamed", "tests/test_epsilon.py::test_in_twelve", "tests/test_gamma.py::test_a", "tests/test_gamma.py::test_b"]
    assert r["unexplained"] == ["tests/test_alpha.py::test_after_section_two", "tests/test_alpha.py::test_in_section_one", "tests/test_beta.py::test_lost_silently",
                                "tests/test_delta.py::test_renamed_but_prefix"]


def test_only_section_two_of_the_change_log_counts():
    r = run([])
    assert "tests/test_alpha.py::test_in_section_one" in r["unexplained"] and "tests/test_alpha.py::test_after_section_two" in r["unexplained"]


def test_a_test_name_that_is_only_a_prefix_of_a_logged_name_is_not_explained():
    assert "tests/test_delta.py::test_renamed_but_prefix" in run([])["unexplained"]


def test_parametrised_ids_count_as_their_base_test():
    r = F.classify(["tests/test_x.py::test_p[1]", "tests/test_x.py::test_p[2]"], ["tests/test_x.py::test_p[a]"], LOG)
    assert r["present"] == ["tests/test_x.py::test_p"] and not r["unexplained"]


def test_everything_present_is_clean_and_a_log_without_section_two_is_an_error():
    assert not run(BASE)["unexplained"] and not run(BASE)["explained"]
    with pytest.raises(ValueError, match="section 2"):
        F.classify(BASE, BASE, "# nothing here\n")


def test_main_exits_one_on_an_unexplained_test_and_zero_when_none(tmp_path):
    base, cur, log = tmp_path / "b.txt", tmp_path / "c.txt", tmp_path / "log.md"
    base.write_text("tests/test_x.py::test_one\ntests/test_x.py::test_two\n")
    log.write_text("## 2. Tests removed or changed\n| `test_x::test_two` | DELETED |\n## 3. next\n")
    cur.write_text("tests/test_x.py::test_one\n")
    args = ["--baseline", str(base), "--changelog", str(log), "--current", str(cur)]
    assert F.main(args) == 0
    cur.write_text("")
    log.write_text("## 2. Tests removed or changed\n## 3. next\n")
    assert F.main(args) == 1
