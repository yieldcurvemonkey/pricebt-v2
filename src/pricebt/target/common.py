"""Thin re-export shim for `gs_quant.target.common` (some ported notebooks import enums, RiskMeasure,
ParameterisedRiskMeasure or the parameter classes from this path). The enums live in `pricebt.common`
(P1.1); `RiskMeasure`, `ParameterisedRiskMeasure` and the parameter classes live in `pricebt.risk`
(DESIGN.md section 8.1). This module just re-exports both under the gs path.
"""
from __future__ import annotations

from pricebt.common import *  # noqa: F401,F403
from pricebt.common import ParameterisedRiskMeasure, RiskMeasure  # noqa: F401 (module __getattr__, not always in *)
from pricebt.risk import CurrencyParameter, FiniteDifferenceParameter  # noqa: F401
