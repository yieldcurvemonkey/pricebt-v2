import subprocess
import sys

import pandas as pd
import pytest

from pricebt.errors import ConfigError, LookAheadError, StaleSnapshot
from pricebt.market import MarketData
from pricebt.pricable import MarkContext, Valuation, as_valuation
from pricebt.pricer import FunctionMDP, SnapshotIndex, TimeMapping
from pricebt.testing.toys import ToyMDP, ToyOption, ToyPricer
from pricebt.timeutil import Clock

pytestmark = pytest.mark.core

T0 = pd.Timestamp("2024-03-01 17:00", tz="America/New_York")


def test_as_valuation_normalises():
    v = as_valuation(3, T0)
    assert isinstance(v, Valuation) and v.pv == 3.0
    v = as_valuation((1.0, 2.0, 3.0), T0)
    assert (v.pv, v.cash, v.financing) == (1.0, 2.0, 3.0)


def test_marketdata_guard_memo_and_lookahead():
    clock = Clock()
    md = MarketData({"primary": ToyMDP()}, clock)
    clock.advance(T0)
    p = md.pricer(T0)
    assert md.pricer(T0) is p and md.n_fetches == 1 and md.n_hits == 1
    with pytest.raises(LookAheadError):
        md.pricer(T0 + pd.Timedelta(minutes=1))
    with pytest.raises(ConfigError):
        md.pricer(T0, "nope")


def test_provider_snapshot_from_future_is_lookahead():
    clock = Clock()
    fut = FunctionMDP(lambda ts, req: ToyPricer(ts + pd.Timedelta(hours=1), 4.0))
    md = MarketData({"primary": fut}, clock)
    clock.advance(T0)
    with pytest.raises(LookAheadError):
        md.pricer(T0)


def test_snapshot_index_modes():
    stamps = [pd.Timestamp("2024-03-01 09:00", tz="America/New_York"), pd.Timestamp("2024-03-01 09:01", tz="America/New_York"), pd.Timestamp("2024-03-04 15:00", tz="America/New_York")]
    idx = SnapshotIndex(stamps)
    q = pd.Timestamp("2024-03-01 09:03", tz="America/New_York")
    assert idx.select(q, TimeMapping("asof", pd.Timedelta(minutes=5))) == 1
    with pytest.raises(StaleSnapshot):
        idx.select(q + pd.Timedelta(hours=1), TimeMapping("asof", pd.Timedelta(minutes=5)))
    assert idx.select(q, TimeMapping("exact")) is None
    assert idx.select(stamps[1], TimeMapping("exact")) == 1
    with pytest.raises(ConfigError):
        TimeMapping("asof", None)  # asof needs a bound
    # date mode: EOD stamp becomes visible only from eod_visible_at
    import datetime as dt

    m = TimeMapping("date", eod_visible_at=dt.time(16, 0))
    assert idx.select(pd.Timestamp("2024-03-04 15:30", tz="America/New_York"), m) is None
    assert idx.select(pd.Timestamp("2024-03-04 16:30", tz="America/New_York"), m) == 2


def test_toy_option_greeks_match_a_central_bump():
    o = ToyOption(strike=4.0, expiry=T0 + pd.Timedelta(days=90), notional=100.0)
    p = ToyPricer(T0, 4.0, vol=80.0)
    ctx = MarkContext.standalone(p)
    d = o.dv01(ctx)  # per bp
    up = ToyPricer(T0, 4.01, vol=80.0)
    dn = ToyPricer(T0, 3.99, vol=80.0)
    fd = (o.value(MarkContext.standalone(up)).pv - o.value(MarkContext.standalone(dn)).pv) / 2.0
    assert d == pytest.approx(fd, rel=1e-4)  # 1bp = 0.01 percent


def test_import_hygiene_core_does_not_pull_rateslib_or_arbs():
    code = ("import sys, pricebt, pricebt.engine, pricebt.market, pricebt.contracts.spec, pricebt.session, pricebt.instrument, pricebt.backtests, pricebt.testing.toys;"
            "bad=[m for m in ('rateslib','QuantLib','gs_quant','MDP','Query','Caching','matplotlib') if m in sys.modules];" "print(bad)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=".", env={**__import__("os").environ, "PYTHONPATH": "src"})
    assert out.stdout.strip() == "[]", out.stdout + out.stderr


@pytest.mark.parametrize("day,utc_hour", [("2024-03-08", 22), ("2024-03-10", 21), ("2024-03-11", 21), ("2024-11-02", 21), ("2024-11-03", 22), ("2024-11-04", 22)])
def test_eod_visible_at_is_wall_clock_time_across_daylight_saving_changes(day, utc_hour):
    """17:00 New York is 22:00 UTC in winter and 21:00 UTC in summer, including on the 23-hour and 25-hour days."""
    import datetime as dt

    idx = SnapshotIndex([pd.Timestamp(f"{day} 09:00", tz="America/New_York")])
    vis = idx._visible_ns(TimeMapping(mode="asof", max_staleness=pd.Timedelta("2D"), eod_visible_at=dt.time(17, 0)))
    assert pd.Timestamp(int(vis[0]), tz="UTC") == pd.Timestamp(f"{day} {utc_hour}:00", tz="UTC")
