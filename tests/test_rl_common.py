"""Shared helpers for the rateslib adapter tests (contains no tests): ONE world builder over plain-data snapshots.

A world is a `MarketSnapshot` (solver-fitted discount factors, seeded fixings, the library's NYC holidays as a calendar named `nyc`, optionally a bond quote
panel) wrapped by the adapter's `wrap`, so the shipped conventions blocks (`calendar: nyc`) need no override. The golden 3y payer world is not built here: it is the
frozen `tests/data_golden_swap.json` (`test_refstack_golden.golden_pricer`), re-wrapped by `golden_world` below.
"""
from __future__ import annotations

import datetime as dt
import functools
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("rateslib")

from pricebt.contracts.schema import SchemaRegistry  # noqa: E402
from pricebt.contracts.spec import Built, TradeTemplate, build_spec  # noqa: E402
from pricebt.contrib import rateslib as RL  # noqa: E402
from pricebt.contrib.rateslib import _compat as C  # noqa: E402
from pricebt.contrib.rateslib.pricer import RLCurvePricer  # noqa: E402
from pricebt.engine import Engine, EngineSettings  # noqa: E402
from pricebt.market import Binding, MarketData  # noqa: E402
from pricebt.pricable import MarkContext  # noqa: E402
from pricebt.snapshot import CalendarData, CurveSnapshot, FixingsSeries, MarketSnapshot, QuoteSet, SnapshotPricer  # noqa: E402
from pricebt.testing.synthetic import SyntheticMarket  # noqa: E402
from pricebt.timeutil import Clock, TimeContext  # noqa: E402
from support.known import FIXTURES, have_fixtures  # noqa: E402,F401  (re-exported for the fixture tests)

rl = C.rl
TZ = "America/New_York"
SCHEMAS = SchemaRegistry.default()
PAR = {"1M": 4.32, "3M": 4.30, "6M": 4.22, "1Y": 4.05, "2Y": 3.90, "3Y": 3.88, "5Y": 3.92, "7Y": 4.00, "10Y": 4.08, "15Y": 4.15, "20Y": 4.12, "30Y": 3.98}
SWAP_CONV = dict(RL.USD_SOFR_OIS_CONVENTIONS)  # calendar: nyc
BOND_CONV = dict(RL.UST_CONVENTIONS)
LAYERS = ("carry", "roll", "delta", "convexity")


def eod(d: str, hour: int = 17) -> pd.Timestamp:
    return pd.Timestamp(f"{d} {hour:02d}:00", tz=TZ)


# ------------------------------------------------------------------------------ curves and fixings (rateslib is the test's tool for fitting them, never the data)
def par_curve(asof: dt.datetime, short_bp: float = 0.0, long_bp: float = 0.0, cid: str = "sofr") -> Any:
    """Solver-fitted SOFR curve (12 par swaps, tilt from short_bp to long_bp), returned as a float curve."""
    tenors = list(PAR)
    par = {t: PAR[t] + (short_bp + (long_bp - short_bp) * i / (len(tenors) - 1)) / 100.0 for i, t in enumerate(tenors)}
    spot = C.calendar().add_bus_days(asof, 2, True)
    insts = {t: rl.IRS(effective=spot, termination=t.lower(), spec="usd_irs", curves=cid, fixed_rate=r) for t, r in par.items()}
    mats = {t: i.leg1.schedule.termination for t, i in insts.items()}
    order = sorted(insts, key=mats.get)
    crv = rl.Curve(nodes={asof: 1.0, **{mats[t]: 1.0 for t in order}}, id=cid, convention="act360", calendar="nyc", modifier="MF", interpolation="log_linear")
    sv = C.solve_quiet(curves=[crv], instruments=[insts[t] for t in order], s=[par[t] for t in order], id=cid, func_tol=1e-12, conv_tol=1e-12)
    assert sv.result["status"] == "SUCCESS"
    return C.to_float_curve(crv, cid)


@functools.lru_cache(maxsize=None)
def par_nodes(asof: dt.date, short_bp: float = 0.0, long_bp: float = 0.0) -> Tuple[Tuple[dt.date, float], ...]:
    """The node table (date, discount factor) of `par_curve`: plain data."""
    c = par_curve(C.to_dt(asof), short_bp, long_bp, "fit")
    return tuple((C.to_date(d), float(v)) for d, v in c.nodes.nodes.items())


@functools.lru_cache(maxsize=None)
def seeded_fixings(seed: int = 7, start: dt.datetime = dt.datetime(2023, 1, 3), end: dt.datetime = dt.datetime(2025, 3, 31)) -> pd.Series:
    """Percent SOFR random walk on every nyc business day (the series behind rateslib.md section 8's golden numbers)."""
    days = C.calendar().bus_date_range(start, end)
    rng = np.random.default_rng(seed)
    return pd.Series(4.30 + np.cumsum(rng.normal(0, 0.008, len(days))), index=pd.DatetimeIndex(days))


@functools.lru_cache(maxsize=None)
def nyc_data() -> CalendarData:
    """The library's built-in NYC weekday holidays as a snapshot calendar named `nyc` (the whole horizon: 10y swaps built in 2025 need 2035 and beyond)."""
    return CalendarData("nyc", tuple(sorted(C.nyc_calendar().holidays)))


# ------------------------------------------------------------------------------ snapshots and pricers
def snapshot(ref: dt.date, nodes: Optional[Sequence[Tuple[dt.date, float]]] = None, *, fixings: Optional[pd.Series] = None, quotes: Optional[QuoteSet] = None, hour: int = 17,
             short_bp: float = 0.0, long_bp: float = 0.0, curve: bool = True, calendars: Optional[Mapping[str, CalendarData]] = None) -> MarketSnapshot:
    """`fixings` are the published percent fixings (only those strictly before `ref` are kept: a snapshot holds nothing else)."""
    curves = {}
    if curve:
        nd = tuple(nodes) if nodes is not None else par_nodes(ref, float(short_bp), float(long_bp))
        curves = {"sofr": CurveSnapshot("sofr", ref, tuple(d for d, _ in nd), tuple(v for _, v in nd))}
    fx = {}
    if fixings is not None:
        f = fixings[fixings.index < pd.Timestamp(ref)]
        fx = {"f": FixingsSeries("f", tuple(t.date() for t in f.index), tuple(float(v) for v in f.values), "percent")}
    return MarketSnapshot(pd.Timestamp(dt.datetime(ref.year, ref.month, ref.day, hour), tz=TZ), ref, curves, fx, quotes, dict(calendars) if calendars is not None else {"nyc": nyc_data()})


def rl_pricer(d: str, short_bp: float = 0.0, long_bp: float = 0.0, fixings: Optional[pd.Series] = None, hour: int = 17, **kw: Any) -> RLCurvePricer:
    """The wrapped pricer of the world at date `d` (ISO string)."""
    ref = dt.date.fromisoformat(d)
    return RL.wrap(SnapshotPricer(snapshot(ref, fixings=fixings, hour=hour, short_bp=short_bp, long_bp=long_bp, **kw)))


GOLD_TERMS = {"side": "pay", "effective": dt.date(2024, 1, 8), "maturity": dt.date(2027, 1, 8), "notional": 50e6, "fixed_rate": 4.20}


def golden_world() -> Tuple[Any, RLCurvePricer, RLCurvePricer]:
    """The seasoned 3y payer 50mm of the verified research (fixed 4.20, effective 2024-01-08, termination 2027-01-08) on the frozen two-date world, coupon paid inside
    [2025-01-02, 2025-01-16); the snapshots are the ones `test_refstack_golden` uses, wrapped by the rateslib adapter. Returns (the RLSwap, pricer at t0, pricer at t1)."""
    from test_refstack_golden import golden_pricer

    p0, p1 = (RL.wrap(SnapshotPricer(golden_pricer(d, k).snapshot)) for d, k in ((dt.date(2025, 1, 2), "c0"), (dt.date(2025, 1, 16), "c1")))
    return build(p0, **GOLD_TERMS).obj, p0, p1


# ------------------------------------------------------------------------------ trades
def template(kit: Any, conventions: Mapping[str, Any], terms: Mapping[str, Any], name: str = "t", bind: Optional[Mapping[str, Any]] = None, layers: Sequence[str] = (),
             allow: Optional[Sequence[str]] = None) -> TradeTemplate:
    raw: Dict[str, Any] = {"factory": kit, "conventions": dict(conventions), "layers": list(layers)}
    if bind:
        raw["bind"] = dict(bind)
    kw = {} if allow is None else {"allow": allow}
    return TradeTemplate(name, build_spec("inst", raw, schemas=SCHEMAS, **kw), terms)


def swap_template(layers: Sequence[str] = (), name: str = "swap", **terms: Any) -> TradeTemplate:
    return template(RL.swap, SWAP_CONV, {"side": "pay", "maturity": "10Y", "notional": 1e7, **terms}, name, layers=layers)


def build(p: Any, **terms: Any) -> Built:
    """A swap of the shipped kit on pricer `p`: `.obj` is the RLSwap, `.terms` what the factory resolved."""
    return swap_template(**terms).build(p, p.ts)


def ctx(p: Any, prev: Any = None) -> MarkContext:
    return MarkContext.standalone(p, prev=prev)


# ------------------------------------------------------------------------------ engine runs (the production path: provider -> Binding(wrap) -> engine)
def synthetic(start: str = "2024-01-02", end: str = "2024-12-31", seed: int = 7, **kw: Any) -> SyntheticMarket:
    """The library-free synthetic market on the NYC calendar, whose snapshot calendar is named `nyc` (the shipped conventions blocks' name)."""
    return SyntheticMarket(start, end, calendar=C.nyc_calendar(), calendar_name="nyc", seed=seed, **kw)


def time_context(**kw: Any) -> TimeContext:
    return TimeContext(calendar=C.nyc_calendar(), **kw)


def engine(grid: Any, strategy: Any, mdp: Any, **settings: Any) -> Engine:
    """An engine over `mdp` whose primary role is wrapped by the adapter's `wrap` (what a config's `market.pricers.primary.wrap` does)."""
    settings.setdefault("show_progress", False)
    return Engine(grid, MarketData({"primary": Binding(mdp, wrap=RL.wrap)}, Clock()), strategy, EngineSettings(**settings))
