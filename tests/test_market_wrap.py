"""MarketData applies a role's adapter `wrap` once per snapshot digest (spec D3): swapping the library is one line, and each snapshot is wrapped once."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from pricebt.errors import LookAheadError
from pricebt.market import Binding, MarketData
from pricebt.pricer import FunctionMDP
from pricebt.snapshot import CurveSnapshot, MarketSnapshot, SnapshotPricer
from pricebt.timeutil import Clock

pytestmark = pytest.mark.core

NY = "America/New_York"


def snapshot(day: int, df: float = 0.99) -> MarketSnapshot:
    ref = dt.date(2024, 1, day)
    c = CurveSnapshot("c", ref, (ref, dt.date(2025, 1, day)), (1.0, df))
    return MarketSnapshot(ts=pd.Timestamp(f"2024-01-{day:02d} 17:00", tz=NY), reference_date=ref, curves={"c": c})


class Wrapped:
    def __init__(self, inner):
        self.inner, self.ts, self.reference_date, self.digest = inner, inner.ts, inner.reference_date, inner.digest


def mdp(dfs=None):
    dfs = dfs or {}
    calls = []

    def fn(ts, request):
        calls.append(ts)
        return SnapshotPricer(snapshot(ts.day, dfs.get(ts.day, 0.99)))

    return FunctionMDP(fn), calls


def market(wrap, **kw):
    m, calls = mdp(**kw)
    clock = Clock()
    md = MarketData({"primary": Binding(m, {}, wrap)}, clock)
    clock.advance(pd.Timestamp("2024-01-10 17:00", tz=NY))
    return md, calls


def test_a_wrapped_role_returns_the_wrappers_pricer_and_wraps_each_snapshot_once():
    seen = []
    md, _ = market(lambda p: seen.append(p.digest) or Wrapped(p))
    t = pd.Timestamp("2024-01-03 17:00", tz=NY)
    a = md.pricer(t)
    b = md.pricer(t, request={"other": 1})  # a different request key: the provider is asked again, but the SAME snapshot digest comes back
    assert isinstance(a, Wrapped) and a is b and len(seen) == 1 and md.n_wraps == 1


def test_a_different_snapshot_is_wrapped_separately():
    md, _ = market(lambda p: Wrapped(p))
    a = md.pricer(pd.Timestamp("2024-01-03 17:00", tz=NY))
    b = md.pricer(pd.Timestamp("2024-01-04 17:00", tz=NY))
    assert a is not b and md.n_wraps == 2 and a.digest != b.digest


def test_the_role_without_a_wrap_returns_the_provider_pricer_untouched():
    md, _ = market(None)
    p = md.pricer(pd.Timestamp("2024-01-03 17:00", tz=NY))
    assert isinstance(p, SnapshotPricer) and md.n_wraps == 0


def test_the_look_ahead_guard_still_applies_to_the_snapshot_before_wrapping():
    def late(ts, request):
        return SnapshotPricer(snapshot(9), ts=pd.Timestamp("2024-01-09 17:00", tz=NY))

    clock = Clock()
    md = MarketData({"primary": Binding(FunctionMDP(late), {}, lambda p: Wrapped(p))}, clock)
    clock.advance(pd.Timestamp("2024-01-10 17:00", tz=NY))
    with pytest.raises(LookAheadError):
        md.pricer(pd.Timestamp("2024-01-05 17:00", tz=NY))  # the provider returned a snapshot stamped after the request


def test_the_wrap_memo_is_bounded_and_evicts_the_oldest():
    md, _ = market(lambda p: Wrapped(p))
    md.maxsize = 2
    for d in (2, 3, 4, 5):
        md.pricer(pd.Timestamp(f"2024-01-{d:02d} 17:00", tz=NY))
    assert len(md._wrapped) == 2 and md.n_wraps == 4


def test_two_roles_have_their_own_wrappers_even_for_the_same_snapshot():
    m, _ = mdp()
    clock = Clock()
    md = MarketData({"a": Binding(m, {}, lambda p: ("A", p.digest)), "b": Binding(m, {}, lambda p: ("B", p.digest))}, clock)
    clock.advance(pd.Timestamp("2024-01-10 17:00", tz=NY))
    t = pd.Timestamp("2024-01-03 17:00", tz=NY)
    pa, pb = md.pricer(t, "a"), md.pricer(t, "b")
    assert pa[0] == "A" and pb[0] == "B" and pa[1] == pb[1]
