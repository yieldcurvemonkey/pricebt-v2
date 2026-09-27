"""DESIGN.md section 12.2 item 2: no vendor/library-name token, and no non-docstring gs_quant
mention, anywhere under src/pricebt/**/*.py (MUST-1)."""
from __future__ import annotations

import pytest

from guards import scan
from guards.dag import PKG

pytestmark = pytest.mark.core


def test_no_vendor_token_anywhere_under_src_pricebt():
    hits = []
    for p in scan.all_py_files(PKG):
        hits.extend(scan.scan_vendor_tokens(p, PKG.parent))
    assert hits == [], "\n".join(str(h) for h in hits)


def test_no_non_docstring_gs_quant_mention_under_src_pricebt():
    hits = []
    for p in scan.all_py_files(PKG):
        hits.extend(scan.scan_gs_quant_tokens(p, PKG.parent))
    assert hits == [], "\n".join(str(h) for h in hits)
