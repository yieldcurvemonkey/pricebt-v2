"""The provider time-mapping selector (`support.common.Selector`, the core `SnapshotIndex` over VISIBLE instants) on synthetic inputs: asof / exact / date,
`max_staleness` from the stamp, `eod_visible_at` only delays, no look-ahead, the visible instants, every miss with its reason. Ported from the old
`SnapshotSelector` known answers (no data needed)."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from pricebt.errors import ConfigError, MarketDataUnavailable, StaleSnapshot
from pricebt.pricer import SnapshotIndex, TimeMapping
from support.common import DataQualityError, LRU, Selector, check_request, time_mapping, utc_ns

pytestmark = pytest.mark.core

NY = "America/New_York"
DAYS = ["2024-03-04", "2024-03-05", "2024-03-06", "2024-03-07", "2024-03-08"]


def ny(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz=NY)


def eod_stamps(extra_1500: bool = True) -> np.ndarray:
    pts = [ny(f"{x} 17:00") for x in DAYS] + ([ny("2024-03-11 15:00")] if extra_1500 else [])
    return utc_ns(pd.DatetimeIndex(pts))


def selector(**time) -> Selector:
    return Selector(eod_stamps(), time_mapping(time or None, kind="eod"), eod=True)


# ------------------------------------------------------------------ the ns unit trap
def test_utc_ns_is_nanoseconds_even_for_a_microsecond_index():
    us = pd.DatetimeIndex(pd.date_range("2026-03-09 13:00", periods=61, freq="1min", tz="UTC")).as_unit("us")
    t = pd.Timestamp("2026-03-09 13:30", tz="UTC")
    assert us.asi8[30] * 1000 == t.value, "control: asi8 of a us index is microseconds while Timestamp.value is ns"
    assert int(np.searchsorted(us.asi8, t.value)) == 61, "control: mixing the units finds the wrong position"
    ns = utc_ns(us)
    assert int(np.searchsorted(ns, t.value)) == 30
    sel = Selector(ns, time_mapping({"max_staleness": "1min"}, kind="minute"), eod=False)
    assert sel.select(t).pos == 30 and sel.select(t).age == pd.Timedelta(0)


def test_naive_stamps_are_refused():
    with pytest.raises(ConfigError):
        utc_ns(pd.DatetimeIndex(["2024-03-04 17:00"]))


# ------------------------------------------------------------------ asof
def test_asof_boundary_is_inclusive_and_age_is_measured_from_the_stamp():
    s = selector()
    hit = s.select(ny("2024-03-05 17:00"))
    assert hit.pos == 1 and hit.age == pd.Timedelta(0) and hit.stamp == ny("2024-03-05 17:00")
    with pytest.raises(StaleSnapshot):
        s.select(ny("2024-03-05 16:59:59"))
    assert s.select(ny("2024-03-06 05:00")).pos == 1, "12h after the stamp is still inside the bound (inclusive)"
    with pytest.raises(StaleSnapshot):
        s.select(ny("2024-03-06 05:00:01"))
    loose = selector(max_staleness="1D")
    assert loose.select(ny("2024-03-05 16:59:59")).pos == 0


def test_a_stale_snapshot_carries_the_request_and_says_how_old_the_row_is():
    with pytest.raises(StaleSnapshot) as e:
        selector().select(ny("2024-03-10 09:00"), {"curve": "x"})
    assert e.value.request == {"curve": "x"} and "2024-03-08 17:00" in str(e.value) and "stale" in str(e.value)


def test_before_the_first_snapshot_is_unavailable_not_stale():
    with pytest.raises(MarketDataUnavailable, match="before_first_snapshot") as e:
        selector().select(ny("2024-03-04 16:59"))
    assert not isinstance(e.value, StaleSnapshot)


def test_an_empty_selector_serves_nothing():
    s = Selector(np.array([], dtype="int64"), time_mapping(None, kind="eod"), eod=True)
    assert len(s) == 0
    with pytest.raises(MarketDataUnavailable, match="before_first_snapshot"):
        s.select(ny("2024-03-04 17:00"))
    assert len(s.instants(ny("2024-03-01"), ny("2024-03-30"))) == 0


def test_unbounded_asof_needs_an_explicit_opt_in():
    with pytest.raises(ConfigError):
        time_mapping({"max_staleness": None}, kind="eod")
    s = selector(max_staleness=None, unbounded_ok=True)
    assert s.select(ny("2024-06-01 12:00")).pos == 5


def test_no_look_ahead_on_random_requests():
    s = selector(max_staleness=None, unbounded_ok=True)
    rng = np.random.default_rng(3)
    base = ny("2024-03-04 17:00").value
    stamps = eod_stamps()
    for off in rng.integers(0, 10 * 86400, 400):
        t = pd.Timestamp(base + int(off) * 10**9, tz="UTC")
        sel = s.select(t)
        assert sel.stamp <= t
        assert sel.stamp.value == stamps[stamps <= t.value].max(), "the newest stamp at or before ts, never a later one"


# ------------------------------------------------------------------ exact / date
def test_exact_mode():
    s = selector(mode="exact")
    assert s.select(ny("2024-03-06 17:00")).pos == 2
    with pytest.raises(MarketDataUnavailable, match="no_snapshot_at_exact_timestamp"):
        s.select(ny("2024-03-06 17:00:01"))


def test_date_mode_serves_the_day_only_once_it_is_visible():
    s = selector(mode="date")
    with pytest.raises(MarketDataUnavailable, match="no_snapshot_for_date 2024-03-06"):
        s.select(ny("2024-03-06 10:00"))
    assert s.select(ny("2024-03-06 17:00")).pos == 2
    assert s.select(ny("2024-03-06 23:00")).pos == 2
    with pytest.raises(MarketDataUnavailable):
        s.select(ny("2024-03-09 12:00"))


def test_a_naive_request_is_read_in_the_mapping_time_zone():
    assert selector().select(pd.Timestamp("2024-03-05 17:00")).pos == 1
    assert selector().select(pd.Timestamp("2024-03-05 21:30")).pos == 1, "read in New York, 21:30 is after the 17:00 row"
    assert selector(tz="UTC", max_staleness="1D").select(pd.Timestamp("2024-03-05 21:30")).pos == 0, "read as UTC, that is 16:30 in New York: before the 17:00 row"


# ------------------------------------------------------------------ eod_visible_at only delays
def test_eod_visible_at_delays_a_1500_stamp_to_1700():
    s = selector(eod_visible_at="17:00", max_staleness="4D")
    assert s.select(ny("2024-03-11 16:00")).pos == 4, "the 15:00 row is not visible before 17:00: Friday's close is served"
    hit = s.select(ny("2024-03-11 17:00"))
    assert hit.pos == 5 and hit.age == pd.Timedelta(hours=2) and hit.visible == ny("2024-03-11 17:00")
    with pytest.raises(StaleSnapshot):
        selector(eod_visible_at="17:00").select(ny("2024-03-11 16:00"))
    assert selector().select(ny("2024-03-11 16:00")).pos == 5, "control: from_row serves the 15:00 row at 16:00"


def test_eod_visible_at_never_advances_a_later_stamp():
    s = selector(eod_visible_at="15:00", max_staleness="2D")
    assert s.select(ny("2024-03-05 16:00")).pos == 0, "a 17:00 stamp stays invisible at 16:00 even with a 15:00 schedule"
    assert list(s.instants(ny("2024-03-04"), ny("2024-03-12"))) == [ny(f"{x} 17:00") for x in DAYS] + [ny("2024-03-11 15:00")]
    d = selector(eod_visible_at="15:00", mode="date")
    with pytest.raises(MarketDataUnavailable):
        d.select(ny("2024-03-05 16:00"))


def test_minute_data_ignores_eod_visible_at():
    """The `eod` flag gates the delay: a minute provider never delays a row, whatever the time block says."""
    m = time_mapping({"eod_visible_at": "17:00", "max_staleness": "1h"}, kind="minute")
    stamps = utc_ns(pd.DatetimeIndex([ny("2024-03-04 09:30"), ny("2024-03-04 09:31")]))
    s = Selector(stamps, m, eod=False)
    assert s.select(ny("2024-03-04 09:30")).visible == ny("2024-03-04 09:30") and list(s.visible_ns) == list(stamps)
    assert list(Selector(stamps, m, eod=True).visible_ns) == [ny("2024-03-04 17:00").value] * 2, "control: as end-of-day data the same block delays both rows"


def test_eod_visible_at_is_a_wall_clock_time_on_a_daylight_saving_day():
    """2024-03-10 is a 23-hour day in New York (the clocks moved at 02:00): 17:00 is 17:00 local, i.e. 21:00 UTC (EDT), not 22:00."""
    stamps = utc_ns(pd.DatetimeIndex([ny("2024-03-10 15:00"), ny("2024-03-08 17:00")]).sort_values())
    s = Selector(stamps, time_mapping({"eod_visible_at": "17:00", "max_staleness": "2D"}, kind="eod"), eod=True)
    assert list(s.instants(ny("2024-03-08"), ny("2024-03-11"))) == [ny("2024-03-08 17:00"), ny("2024-03-10 17:00")]
    assert s.select(ny("2024-03-10 17:00")).pos == 1 and s.select(ny("2024-03-10 16:59")).pos == 0


def test_the_core_index_reads_eod_visible_at_as_wall_clock_time_too():
    stamps = utc_ns(pd.DatetimeIndex([ny("2024-03-10 15:00"), ny("2024-03-08 17:00")]).sort_values())
    core = SnapshotIndex(pd.DatetimeIndex(stamps.view("datetime64[ns]")).tz_localize("UTC"))
    vis = core._visible_ns(TimeMapping(mode="asof", max_staleness=pd.Timedelta("2D"), eod_visible_at=dt.time(17, 0)))
    assert vis[1] == ny("2024-03-10 17:00").value


def test_instants_are_the_visible_instants_in_the_mapping_tz():
    s = selector(eod_visible_at="17:00")
    got = s.instants(ny("2024-03-05"), ny("2024-03-11 23:00"))
    assert list(got) == [ny(f"{x} 17:00") for x in DAYS[1:]] + [ny("2024-03-11 17:00")]
    assert str(got.tz) == NY
    for t in got:
        assert s.select(t).visible == t, "every listed instant is selectable and selects the row that becomes visible there"


def test_instants_include_both_bounds_and_nothing_outside():
    s = selector()
    assert list(s.instants(ny("2024-03-05 17:00"), ny("2024-03-06 17:00"))) == [ny("2024-03-05 17:00"), ny("2024-03-06 17:00")]
    assert len(s.instants(ny("2024-03-05 17:00:01"), ny("2024-03-06 16:59:59"))) == 0
    assert len(s.instants(ny("2024-03-05"), ny("2024-03-05 23:00"))) == 1, "a date range covers whole days when given as timestamps of that day"


def test_selector_rejects_unsorted_and_duplicate_stamps():
    with pytest.raises(DataQualityError):
        Selector(eod_stamps()[::-1], time_mapping(None, kind="eod"), eod=True)
    dup = eod_stamps().copy()
    dup[1] = dup[0]
    with pytest.raises(DataQualityError):
        Selector(dup, time_mapping(None, kind="eod"), eod=True)


# ------------------------------------------------------------------ config helpers
def test_time_mapping_defaults_and_validation():
    assert time_mapping(None, kind="eod").max_staleness == pd.Timedelta(hours=12)
    assert time_mapping(None, kind="minute").max_staleness == pd.Timedelta(minutes=5)
    assert time_mapping(None, kind="minute", mode="exact").mode == "exact"
    m = time_mapping({"mode": "date", "eod_visible_at": "from_row", "max_staleness": "2D"}, kind="eod")
    assert (m.mode, m.eod_visible_at, m.max_staleness) == ("date", None, pd.Timedelta(days=2))
    assert time_mapping({"eod_visible_at": "17:00"}, kind="eod").eod_visible_at == dt.time(17, 0)
    ready = TimeMapping(mode="exact", max_staleness=None, unbounded_ok=True)
    assert time_mapping(ready, kind="eod") is ready
    with pytest.raises(ConfigError, match="unknown time keys"):
        time_mapping({"when_not_visible": "error"}, kind="eod")
    with pytest.raises(ConfigError):
        time_mapping({"mode": "nearest"}, kind="eod")


def test_requests_with_unknown_keys_are_refused():
    check_request(None)
    check_request({"curve": "x"}, ("curve",))
    with pytest.raises(ConfigError):
        check_request({"curve_name": "x"})


def test_lru_is_bounded():
    c = LRU(2)
    for k in range(5):
        c.put(k, k)
    assert len(c) == 2 and 4 in c and 3 in c and c.get(0) is None
    c.get(3)
    c.put(5, 5)
    assert 3 in c and 4 not in c
    c.clear()
    assert len(c) == 0
    with pytest.raises(ConfigError):
        LRU(0)
