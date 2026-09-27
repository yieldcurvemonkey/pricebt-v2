"""Thin re-export shim for `gs_quant.target.measures` (some ported notebooks import risk-measure
instances from this path rather than from `gs_quant.risk`). Every name pricebt defines lives in
`pricebt.risk` (DESIGN.md section 8.1); this module just re-exports it under the gs path.
"""
from __future__ import annotations

from pricebt.risk import *  # noqa: F401,F403
