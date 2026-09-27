"""Order instructions produced by actions and consumed by the engine; position selectors and metadata."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Tuple, Union

import pandas as pd

from .contracts.spec import TradeTemplate
from .costs import CostModel


@dataclass(frozen=True)
class PositionMeta:
    action: str = ""
    kind: str = "add"  # add | hedge | initial | rebalance | scaled
    trigger_idx: int = -1
    action_idx: int = -1
    template: str = ""
    tags: Tuple[str, ...] = ()
    parent: Optional[str] = None
    group: Optional[str] = None
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PositionSelector:
    """Selects positions by ids / tags (any-of) / templates / creating actions / kinds / predicate. Empty selector = all open positions."""

    ids: Optional[Tuple[str, ...]] = None
    tags: Optional[Tuple[str, ...]] = None
    templates: Optional[Tuple[str, ...]] = None
    actions: Optional[Tuple[str, ...]] = None
    kinds: Optional[Tuple[str, ...]] = None
    predicate: Optional[Callable[[Any], bool]] = None

    def matches(self, pos: Any) -> bool:
        m = pos.meta
        if self.ids is not None and pos.id not in self.ids:
            return False
        if self.tags is not None and not (set(self.tags) & set(pos.tags)):
            return False
        if self.templates is not None and m.template not in self.templates:
            return False
        if self.actions is not None and m.action not in self.actions:
            return False
        if self.kinds is not None and m.kind not in self.kinds:
            return False
        if self.predicate is not None and not self.predicate(pos):
            return False
        return True


Selector = PositionSelector
ALL = PositionSelector()


@dataclass(frozen=True)
class OpenOrder:
    """Open ONE position: build `template` at the fill pricer, hold `quantity` units (signed), optionally exit at `final_ts`."""

    template: TradeTemplate
    quantity: float = 1.0
    final_ts: Optional[pd.Timestamp] = None
    tags: Tuple[str, ...] = ()
    meta: PositionMeta = field(default_factory=PositionMeta)
    cost_entry: Optional[CostModel] = None
    cost_exit: Optional[CostModel] = None
    immediate: bool = False  # initial holdings: booked synchronously, fill_lag not applied


@dataclass(frozen=True)
class CloseOrder:
    selector: PositionSelector = ALL
    reason: str = "action"
    cost_model: Optional[CostModel] = None  # overrides the position's own exit model when not None


@dataclass(frozen=True)
class ResizeOrder:
    position_id: str = ""
    delta_quantity: float = 0.0
    reason: str = "resize"
    cost_model: Optional[CostModel] = None


Instruction = Union[OpenOrder, CloseOrder, ResizeOrder]
