"""DESIGN.md section 12.2 item 1: every import under src/pricebt/**/*.py must be stdlib, or one of
numpy, pandas, yaml (PyYAML), dateutil (python-dateutil), tqdm, or pricebt itself."""
from __future__ import annotations

import pytest

from guards import scan
from guards.dag import PKG

pytestmark = pytest.mark.core


def test_no_disallowed_import_root_anywhere_under_src_pricebt():
    hits = []
    for p in scan.all_py_files(PKG):
        hits.extend(scan.scan_import_roots(p, PKG.parent))
    assert hits == [], "\n".join(str(h) for h in hits)
