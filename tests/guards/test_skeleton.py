"""DESIGN.md section 12.2 item 6: the set of src/pricebt/**/*.py paths equals the literal list in
DESIGN.md section 3.2 (plus target/__init__.py -- see docs/v2/DECISIONS_LOG.md)."""
from __future__ import annotations

import pytest

from guards import scan
from guards.dag import PKG, SKELETON_PATHS

pytestmark = pytest.mark.core


def test_skeleton_path_set_matches_design_section_3_2_exactly():
    on_disk = {p.relative_to(PKG).as_posix() for p in scan.all_py_files(PKG)}
    expected = set(SKELETON_PATHS)
    missing = expected - on_disk
    extra = on_disk - expected
    assert not missing, f"missing from disk: {sorted(missing)}"
    assert not extra, f"present on disk but not in the skeleton list: {sorted(extra)}"
