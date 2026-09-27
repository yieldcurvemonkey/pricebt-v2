"""Thin re-export shim for `gs_quant.target.backtests` (`BacktestTradingQuantityType`,
`DeltaHedgeParameters`), used by some ported notebooks. Both names live in `pricebt.backtests.core`
(DESIGN.md section 3.2), which is real code only from P3.1 onward -- so the import is lazy
(PEP 562 module `__getattr__`), resolved only when one of these names is actually accessed, not at
module load time (Import DAG, DESIGN.md section 3.2: `target.*` sits in the last tier, after
`backtests.*`, precisely so this module can depend on it without being imported before it exists).
"""
from __future__ import annotations

__all__ = ["BacktestTradingQuantityType", "DeltaHedgeParameters"]


def __getattr__(name: str):
    if name in __all__:
        from pricebt.backtests.core import BacktestTradingQuantityType, DeltaHedgeParameters

        return {"BacktestTradingQuantityType": BacktestTradingQuantityType, "DeltaHedgeParameters": DeltaHedgeParameters}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
