"""Small shared enums and helpers."""
from __future__ import annotations

from enum import IntEnum
from typing import Any, Iterable, List


class Phase(IntEnum):
    """Action ordering key inside one timeline point."""

    INITIAL = -1
    EXIT = 0
    ADD = 1
    ADJUST = 2
    HEDGE = 3


#: the engine's own baseline decomposition (spec L3): a Taylor attribution from the bound `dv01`, `gamma` and `rate`; named apart from every library layer
BASELINE_LAYERS = ("tay_delta", "tay_convexity", "tay_unexplained")
RESERVED_LAYERS = frozenset({"total", "transactions", "cash_interest", "unexplained", "financing", *BASELINE_LAYERS})


def as_list(x: Any) -> List[Any]:
    """None -> [], scalar -> [scalar], iterable (not str/dict) -> list."""
    if x is None:
        return []
    if isinstance(x, (str, bytes, dict)):
        return [x]
    if isinstance(x, Iterable):
        return list(x)
    return [x]
