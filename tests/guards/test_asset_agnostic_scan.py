"""DESIGN.md section 12.2 item 4 (MUST-5): assets/**, markets/**, risk/results.py and
risk/transform.py must not name a rates/swap vocabulary word -- the engine and the pricing layer
must not special-case any asset class. instrument/, risk/__init__.py and backtests/ are exempt:
gs names and the ported gs text legitimately live there."""
from __future__ import annotations

import pytest

from guards import scan
from guards.dag import PKG

pytestmark = pytest.mark.core


def test_no_asset_specific_vocabulary_in_the_asset_agnostic_layers():
    hits = []
    for p in scan.asset_agnostic_files(PKG):
        hits.extend(scan.scan_asset_agnostic_tokens(p, PKG.parent))
    assert hits == [], "\n".join(str(h) for h in hits)
