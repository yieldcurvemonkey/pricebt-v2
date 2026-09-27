"""Read-only protocols the strategy layer sees. Triggers/actions/signals never receive the engine itself."""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Protocol, Tuple, Union, runtime_checkable

import numpy as np
import pandas as pd

from .orders import Instruction, PositionMeta, PositionSelector
from .pricer import Pricer
from .timeutil import Calendar

Scope = Union[str, PositionSelector]  # "portfolio" or a selector


@runtime_checkable
class PositionView(Protocol):
    id: str
    pricable: Any
    quantity: float  # signed units of the template as written; 0 once closed
    entry_quantity: float
    tags: Tuple[str, ...]
    entry_ts: pd.Timestamp
    final_ts: Optional[pd.Timestamp]
    status: str  # 'open' | 'closed'
    meta: PositionMeta

    @property
    def pnl(self) -> float: ...

    @property
    def value(self) -> float: ...


@runtime_checkable
class EngineView(Protocol):
    now: pd.Timestamp
    calendar: Calendar
    start: pd.Timestamp
    end: pd.Timestamp
    equity: float  # value at the previous point until step 7 of this one
    cash: float
    pending_orders: Tuple[Instruction, ...]

    def positions(self, scope: Scope = "portfolio", *, include_pending: bool = False) -> Tuple[PositionView, ...]: ...

    def n_positions(self, scope: Scope = "portfolio", *, include_pending: bool = False) -> int: ...

    def measure(self, name: str, scope: Scope = "portfolio", *, include_pending: bool = False) -> float: ...

    def measure_of(self, template: Any, name: str, quantity: float = 1.0) -> float: ...

    def measure_vector(self, name: str, scope: Scope = "portfolio", *, include_pending: bool = False, missing: str = "raise") -> Dict[str, float]: ...

    def measure_of_vector(self, template: Any, name: str, quantity: float = 1.0) -> Dict[str, float]: ...

    def build(self, template: Any, *, request: Optional[Mapping[str, Any]] = None) -> Any: ...

    def pricer(self, ts: Optional[pd.Timestamp] = None, role: str = "primary", request: Optional[Mapping[str, Any]] = None) -> Pricer: ...

    def signal(self, source: Any, ts: Optional[pd.Timestamp] = None) -> float: ...

    def signal_window(self, source: Any, n: int, *, inclusive: bool = False) -> np.ndarray: ...

    def layer_pnl(self, layer: str, scope: Scope = "portfolio") -> float: ...

    def action_cash(self, action: str) -> float: ...

    def event(self, kind: str, **detail: Any) -> None: ...
