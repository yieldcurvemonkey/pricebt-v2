"""The partition helpers on known answers, and the collection hook itself run in a child pytest (spec G-5): an unmarked test is an error."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest

from guards import partition, scan

pytestmark = pytest.mark.core


def item(nodeid, *marks):
    return SimpleNamespace(nodeid=nodeid, get_closest_marker=lambda n: object() if n in marks else None)


ITEMS = [item("a::core", "core"), item("b::rl", "adapter_rateslib"), item("c::none"), item("d::both", "core", "fixtures"), item("e::live", "live_arbs"), item("f::deprecated", "rateslib")]


def test_unmarked_reports_exactly_the_tests_without_a_partition_marker():
    assert partition.unmarked(ITEMS) == ["c::none", "f::deprecated"], "the deprecated `rateslib` alias is not a partition"


def test_conflicts_reports_core_tests_that_also_need_something():
    assert partition.conflicts(ITEMS) == ["d::both"]


def test_the_five_partitions_are_the_ones_of_the_spec():
    assert partition.PARTITIONS == ("core", "adapter_rateslib", "adapter_quantlib", "fixtures", "live_arbs")


def _child(tmp_path, body):
    """A child pytest run with the collection hook of `guards.partition` (the one `tests/conftest.py` imports) on a temporary test file."""
    (tmp_path / "test_child.py").write_text(textwrap.dedent(body), encoding="utf8")
    (tmp_path / "conftest.py").write_text("from guards.partition import pytest_collection_modifyitems  # noqa: F401\n", encoding="utf8")
    (tmp_path / "pytest.ini").write_text("[pytest]\nmarkers =\n    core\n    adapter_rateslib\n    adapter_quantlib\n    fixtures\n    live_arbs\n", encoding="utf8")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(scan.PROJECT / "src"), str(scan.PROJECT / "tests")])}
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(tmp_path / "test_child.py")], capture_output=True, text=True, cwd=tmp_path, env=env)


def test_the_collection_hook_rejects_an_unmarked_test_and_accepts_a_marked_one(tmp_path):
    bad = _child(tmp_path, "def test_x():\n    pass\n")
    out = bad.stdout + bad.stderr
    assert bad.returncode != 0 and "test_child.py::test_x" in out and "partition" in out.lower(), out
    good = _child(tmp_path, "import pytest\n\n@pytest.mark.core\ndef test_x():\n    pass\n")
    assert good.returncode == 0, good.stdout + good.stderr


def test_the_collection_hook_rejects_a_core_test_that_also_needs_a_library(tmp_path):
    bad = _child(tmp_path, "import pytest\n\n@pytest.mark.core\n@pytest.mark.adapter_rateslib\ndef test_x():\n    pass\n")
    out = bad.stdout + bad.stderr
    assert bad.returncode != 0 and "test_child.py::test_x" in out and "conflict" in out, out


def test_the_project_conftest_installs_the_hook():
    text = (scan.PROJECT / "tests" / "conftest.py").read_text(encoding="utf8")
    assert "from guards.partition import pytest_collection_modifyitems" in text
