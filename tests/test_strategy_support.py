"""Shared helpers for the strategy-layer tests (no tests here)."""
from __future__ import annotations

import pandas as pd

from toy_helpers import toy_tpl
from pricebt.contracts.spec import TradeTemplate
from pricebt.strategy.signals import SignalStore
from pricebt.testing.toys import ToyForward, ToyOption
from pricebt.timeutil import TimeContext, TimeGrid, TimelineContext

NY = "America/New_York"
D = lambda s: pd.Timestamp(f"{s} 17:00", tz=NY)  # daily grid stamp (date_policy close)


def mk_ctx(start: str, end: str, freq: str = "1b", days=None) -> TimelineContext:
    tc = TimeContext()
    g = TimeGrid.daily(start, end, freq, tc)
    return TimelineContext(g.start, g.end, tc, tuple(days) if days else g.days)


def fwd(name: str = "fwd", **kw) -> TradeTemplate:
    kw = {"strike": 4.0, **kw}
    return toy_tpl(name=name, target=ToyForward, kwargs=kw)


def opt(expiry: str, name: str = "opt", strike: float = 4.0, notional: float = 1e4) -> TradeTemplate:
    return toy_tpl(name=name, target=ToyOption, kwargs={"strike": strike, "expiry": D(expiry), "notional": notional}, resolved=("expiry",))


class FakeView:
    """Canned EngineView for unit tests of triggers and actions. `x` feeds signals; `measures` maps (name, scope-string) -> value."""

    def __init__(self, now=None, x=None, measures=None, units=None, n=0, equity=0.0, store=None):
        self.now = now
        self.x = x
        self.measures = measures or {}
        self.units = units or {}
        self.n = n
        self.equity = equity
        self.cash = 0.0
        self.pending_orders = ()
        self.store = store
        self.events = []
        self._cash = {}

    def signal(self, source, ts=None):
        if self.store is not None:
            return self.store.value(source, ts)
        return self.x

    def signal_window(self, source, n, *, inclusive=False):
        return self.store.window(source, n, inclusive=inclusive)

    def positions(self, scope="portfolio", *, include_pending=False):
        return tuple(range(self.n)) if self.n else ()

    def n_positions(self, scope="portfolio", *, include_pending=False):
        return self.n

    def measure(self, name, scope="portfolio", *, include_pending=False):
        v = self.measures[name]
        return v(scope) if callable(v) else v

    def measure_of(self, template, name, quantity=1.0):
        return quantity * self.units[(getattr(template, "name", template), name)]

    def build(self, template, *, request=None):
        return template

    def action_cash(self, action):
        return self._cash.get(action, 0.0)

    def layer_pnl(self, layer, scope="portfolio"):
        return 0.0

    def event(self, kind, **detail):
        self.events.append((kind, detail))


