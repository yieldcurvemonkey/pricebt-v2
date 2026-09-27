"""Tiny strategies for engine tests (no triggers/actions needed): submit prepared instructions at given timestamps or via a callable."""
from __future__ import annotations

from typing import Any, Callable, List, Mapping, Optional, Sequence

import pandas as pd

from ..orders import Instruction
from ..timeutil import TimelineContext


class _Run:
    def __init__(self, script: Mapping[pd.Timestamp, Sequence[Instruction]], fn: Optional[Callable[..., None]], extras: Sequence[pd.Timestamp]):
        self._script = {pd.Timestamp(k): list(v) for k, v in script.items()}
        self._fn = fn
        self._extras = list(extras)
        self.steps: List[pd.Timestamp] = []

    def timeline_extras(self) -> Sequence[pd.Timestamp]:
        return list(self._script) + self._extras

    def step(self, ts: pd.Timestamp, view: Any, submit: Callable[[Instruction], None]) -> None:
        self.steps.append(ts)
        for o in self._script.get(ts, ()):
            submit(o)
        if self._fn is not None:
            self._fn(ts, view, submit)


class ScriptedStrategy:
    """`ScriptedStrategy({ts: [OpenOrder(...), ...]}, fn=None)`; `fn(ts, view, submit)` runs after the scripted orders each point."""

    def __init__(self, script: Optional[Mapping[pd.Timestamp, Sequence[Instruction]]] = None, fn: Optional[Callable[..., None]] = None, extras: Sequence[pd.Timestamp] = ()):
        self.script = dict(script or {})
        self.fn = fn
        self.extras = list(extras)
        self.last_run: Optional[_Run] = None

    def start(self, ctx: TimelineContext) -> _Run:
        self.last_run = _Run(self.script, self.fn, self.extras)
        return self.last_run
