"""`support.curves.CurveStore` (curve-store partitions -> `CurveSnapshot` + fixings + calendar): every reference-data rule of the old store, on tiny trees
whose answers are known by construction and on the real fixtures (holiday rows, freshness rules, time mapping, DST, fixings policy, digests, caching)."""
import datetime as dt
import logging
import pickle

import numpy as np
import pandas as pd
import pytest

from pricebt.errors import ConfigError, MarketDataUnavailable, StaleSnapshot
from pricebt.market import MarketData
from pricebt.snapshot import CurveSnapshot, SnapshotPricer
from pricebt.timeutil import Clock
from support import tiny
from support.common import FixturesMissing, calendar_data, load_fixings
from support.curves import AssetProfile, CurveStore, snapshot_tag
from support.known import CURVE_COUNTS, EOD, FIXTURES, HOLIDAY_ROWS_2026, MIN, NY, Q12, d, needs_fixtures, ny

pytestmark = pytest.mark.fixtures
pyarrow = pytest.importorskip("pyarrow")
log = logging.getLogger("pricebt.timing")

FIXINGS_NAME = "USD-SOFR-1D"


# ============================================================================ tiny trees: every rule with a known answer
def store(tmp_path, rows, *, asset="TINY", profile=None, fixings=None, **kw):
    tiny.write_calendars(tmp_path)
    tiny.write_curve_store(tmp_path, asset, rows)
    return CurveStore(asset, root=tmp_path / "curves", profile=profile or AssetProfile("eod"), fixings=fixings, **kw)


def fresh(day, stamp=None, dfs=(0.997, 0.994), **kw):
    """A fresh EOD row: stamped 17:00 New York (21:00 UTC in summer) of `day`, anchored at `day`."""
    return tiny.curve_row(stamp or f"{day} 21:00", day, tiny.nodes(day, *dfs), **kw)


def stale(day, prev):
    """A holiday row: stamped on `day` but carrying the nodes of the previous business day."""
    return tiny.curve_row(f"{day} 21:00", day, tiny.nodes(prev, 0.997, 0.994))


def test_freshness_drops_holiday_rows_and_keeps_fresh_ones(tmp_path):
    s = store(tmp_path, [fresh("2024-06-17"), fresh("2024-06-18"), stale("2024-06-19", "2024-06-18"), fresh("2024-06-20")])
    assert len(s.dropped) == 1 and s.dropped.iloc[0]["reason"] == "stale_reference" and s.dropped.iloc[0]["trading_date"] == d("2024-06-19")
    assert [str(t.date()) for t in s.available_timestamps(ny("2024-06-01"), ny("2024-06-30"))] == ["2024-06-17", "2024-06-18", "2024-06-20"]
    with pytest.raises(StaleSnapshot):
        s.get_pricer(ny("2024-06-19 17:00"))
    p = s.get_pricer(ny("2024-06-20 17:00"))
    assert p.reference_date == d("2024-06-20") and p.ts == ny("2024-06-20 17:00") and p.snapshot.curves["sofr"].node_dates[0] == d("2024-06-20")
    loose = store(tmp_path / "loose", [fresh("2024-06-18"), stale("2024-06-19", "2024-06-18")], time={"max_staleness": "2D"})
    q = loose.get_pricer(ny("2024-06-19 17:00"))
    assert q.reference_date == d("2024-06-18") and q.ts == ny("2024-06-18 17:00"), "the previous close, never the holiday row's copy"


def test_the_freshness_rules_differ_on_exchange_dated_rows(tmp_path):
    """Sunday-evening row (Chicago 22:00 Sunday) anchored on the previous Friday, Monday row, and a row on the Juneteenth holiday."""
    rows = [
        tiny.curve_row("2024-06-17 03:00", "2024-06-17", tiny.nodes("2024-06-14", 0.997, 0.994)),  # Sun 22:00 CDT: the last business day is Fri 06-14
        tiny.curve_row("2024-06-17 14:00", "2024-06-17", tiny.nodes("2024-06-17", 0.997, 0.994)),  # Mon 09:00 CDT
        tiny.curve_row("2024-06-17 15:00", "2024-06-17", tiny.nodes("2024-06-14", 0.997, 0.994)),  # Mon 10:00 CDT still anchored on Friday: stale
        tiny.curve_row("2024-06-19 15:00", "2024-06-19", tiny.nodes("2024-06-18", 0.997, 0.994)),  # Juneteenth 10:00 CDT: the last business day is Tue 06-18
        tiny.curve_row("2024-06-19 16:00", "2024-06-19", tiny.nodes("2024-06-19", 0.997, 0.994)),  # anchored on the holiday itself: stale
    ]
    prof = {"kind": "minute", "freshness": "local_bd", "freshness_tz": "America/Chicago"}
    cme = store(tmp_path / "a", rows, profile=prof)
    assert [str(t) for t in cme.dropped["stamp"]] == ["2024-06-17 15:00:00+00:00", "2024-06-19 16:00:00+00:00"] and set(cme.dropped["reason"]) == {"stale_reference"}
    assert len(cme._selector()) == 3
    spine = store(tmp_path / "b", rows, profile={"kind": "minute", "freshness": "trading_date"})
    assert len(spine.dropped) == 3, "control: the trading-date rule also drops the Sunday row and the row anchored on the last business day before the holiday"
    none = store(tmp_path / "c", rows, profile={"kind": "minute", "freshness": "none"})
    assert len(none.dropped) == 0 and len(none._selector()) == 5


def test_equal_stamps_keep_the_last_row(tmp_path):
    a, b = fresh("2024-06-21", dfs=(0.990, 0.980)), fresh("2024-06-21", dfs=(0.995, 0.985))
    s = store(tmp_path, [a, b, fresh("2024-06-20")])
    assert len(s.dropped) == 1 and s.dropped.iloc[0]["reason"] == "duplicate_stamp" and len(s._selector()) == 2
    assert s.get_pricer(ny("2024-06-21 17:00")).snapshot.curves["sofr"].values[1] == 0.995, "the last of equal stamps"


def test_an_unfresh_duplicate_is_a_stale_reference_not_a_duplicate(tmp_path):
    s = store(tmp_path, [fresh("2024-06-20"), stale("2024-06-19", "2024-06-18"), stale("2024-06-19", "2024-06-18")])
    assert list(s.dropped["reason"]) == ["stale_reference", "stale_reference"]


def test_invalid_discount_factors_are_unavailable_never_a_curve(tmp_path):
    bad = {
        "2024-06-24": tiny.curve_row("2024-06-24 21:00", "2024-06-24", [("2024-06-24", 1.0000001), ("2024-07-24", 0.99)]),
        "2024-06-25": tiny.curve_row("2024-06-25 21:00", "2024-06-25", [("2024-06-25", 1.0), ("2024-07-25", -0.5)]),
        "2024-06-26": tiny.curve_row("2024-06-26 21:00", "2024-06-26", [("2024-06-26", 1.0), ("2024-07-26", float("nan"))]),
        "2024-06-27": tiny.curve_row("2024-06-27 21:00", "2024-06-27", [("2024-06-27", 1.0), ("2024-07-27", 0.0)]),
        "2024-06-28": tiny.curve_row("2024-06-28 21:00", "2024-06-28", [("2024-06-28", 1.0)]),
    }
    s = store(tmp_path, list(bad.values()) + [fresh("2024-06-21")])
    for day in bad:
        with pytest.raises(MarketDataUnavailable, match="bad_df"):
            s.get_pricer(ny(f"{day} 17:00"))
    assert s.get_pricer(ny("2024-06-21 17:00")).reference_date == d("2024-06-21")
    edge = store(tmp_path / "e", [tiny.curve_row("2024-06-24 21:00", "2024-06-24", [("2024-06-24", 1.0 + 1e-13), ("2024-07-24", 0.99)])])
    assert edge.get_pricer(ny("2024-06-24 17:00")).reference_date == d("2024-06-24"), "an anchor within 1e-12 of 1 is accepted"


def test_rising_discount_factors_and_dfs_above_one_are_real_data_and_served(tmp_path):
    s = store(tmp_path, [fresh("2024-06-27", dfs=(1.002, 0.99))])
    assert s.get_pricer(ny("2024-06-27 17:00")).snapshot.curves["sofr"].values == (1.0, 1.002, 0.99)


def test_spline_rows_and_unknown_interpolation_tags_are_errors_not_silent_substitutes(tmp_path):
    s = store(tmp_path, [fresh("2024-06-25", spline=["2024-07-25"]), fresh("2024-06-26", interpolation="flat_forward"), fresh("2024-06-27", interpolation=None),
                         fresh("2024-06-28", interpolation="log_linear")])
    with pytest.raises(ConfigError, match="spline"):
        s.get_pricer(ny("2024-06-25 17:00"))
    with pytest.raises(ConfigError, match="flat_forward"):
        s.get_pricer(ny("2024-06-26 17:00"))
    for day in ("2024-06-27", "2024-06-28"):
        assert s.get_pricer(ny(f"{day} 17:00")).snapshot.curves["sofr"].interpolation == "log_linear_df", "a missing tag is the store default"
    assert snapshot_tag("log_linear") == "log_linear_df" and snapshot_tag(None) == "log_linear_df" and snapshot_tag("") == "log_linear_df"
    with pytest.raises(ConfigError):
        snapshot_tag("linear")


def test_the_snapshot_holds_the_row_fixings_and_the_swap_calendar(tmp_path):
    days = pd.bdate_range("2024-06-10", "2024-06-20")
    days = days[days != pd.Timestamp("2024-06-19")]
    tiny.write_fixings(tmp_path, days, [0.0530 + 0.0001 * i for i in range(len(days))])
    s = store(tmp_path, [fresh("2024-06-20")], fixings="auto", curve_id="ois")
    p = s.get_pricer(ny("2024-06-20 17:00"))
    snap = p.snapshot
    assert list(snap.curves) == ["ois"] and snap.curves["ois"].name == "ois" and list(snap.fixings) == [FIXINGS_NAME]
    fs = snap.fixings[FIXINGS_NAME]
    assert fs.unit == "percent" and fs.dates[-1] == d("2024-06-18") and fs.dates[0] == d("2024-06-10") and fs.values[0] == pytest.approx(5.30) and fs.proxied == ()
    assert list(snap.calendars) == ["usd_fed"] and snap.calendars["usd_fed"].holidays == calendar_data("nyc", tmp_path / "calendars").holidays
    assert snap.reference_date == d("2024-06-20") and snap.ts == ny("2024-06-20 17:00") and p.digest == snap.digest()
    assert s.get_pricer(ny("2024-06-20 17:00"), {"curve": "ois"}) is p and s.get_pricer(ny("2024-06-20 17:00"), {"curve": "TINY"}) is p
    with pytest.raises(ConfigError):
        s.get_pricer(ny("2024-06-20 17:00"), {"curve": "other"})
    with pytest.raises(ConfigError):
        s.get_pricer(ny("2024-06-20 17:00"), {"curve_name": "TINY"})


def test_a_pre_publication_stamp_proxies_the_newest_fixing(tmp_path):
    days = pd.bdate_range("2024-06-10", "2024-06-20")
    days = days[days != pd.Timestamp("2024-06-19")]
    rates = [0.0530 + 0.0001 * i for i in range(len(days))]
    tiny.write_fixings(tmp_path, days, rates)
    rows = [tiny.curve_row("2024-06-20 11:00", "2024-06-20", tiny.nodes("2024-06-20", 0.997, 0.994)), tiny.curve_row("2024-06-20 12:00", "2024-06-20", tiny.nodes("2024-06-20", 0.997, 0.994))]
    s = store(tmp_path, rows, profile=AssetProfile("minute"), fixings="auto")
    early, later = s.get_pricer(ny("2024-06-20 07:00")), s.get_pricer(ny("2024-06-20 08:00"))
    fe, fl = early.snapshot.fixings[FIXINGS_NAME], later.snapshot.fixings[FIXINGS_NAME]
    assert fe.dates[-1] == fl.dates[-1] == d("2024-06-18") and fe.proxied == (d("2024-06-18"),) and fl.proxied == ()
    assert fe.values[-1] == fe.values[-2] and fl.values[-1] != fl.values[-2], "07:00 repeats the level of 06-17; 08:00 has 06-18's own"
    strict = store(tmp_path / "s", rows, profile=AssetProfile("minute"), fixings=tmp_path / "fixings" / "USD-SOFR-1D.parquet", fixings_policy_cfg={"pre_publish": "strict"})
    with pytest.raises(ValueError, match="not published"):
        strict.get_pricer(ny("2024-06-20 07:00"))
    assert strict.get_pricer(ny("2024-06-20 08:00")).reference_date == d("2024-06-20")


def test_minute_rows_use_the_5_minute_default_and_ignore_eod_visible_at(tmp_path):
    rows = [tiny.curve_row(f"2024-06-20 13:{m:02d}", "2024-06-20", tiny.nodes("2024-06-20", 0.997, 0.994)) for m in (0, 1, 2, 3)]
    s = store(tmp_path, rows, profile=AssetProfile("minute"), time={"eod_visible_at": "17:00"})
    assert s.time.max_staleness == pd.Timedelta(minutes=5)
    assert s.get_pricer(ny("2024-06-20 09:01:30")).ts == ny("2024-06-20 09:01"), "a minute row is visible at its own stamp, not delayed to 17:00"
    with pytest.raises(StaleSnapshot):
        s.get_pricer(ny("2024-06-20 09:08:01"))
    assert store(tmp_path / "e", [fresh("2024-06-20")], time={"eod_visible_at": "18:00", "max_staleness": "3h"}).available_timestamps(ny("2024-06-20"), ny("2024-06-21"))[0] == ny("2024-06-20 18:00")


def test_window_limits_the_partitions_and_an_empty_window_is_unavailable(tmp_path):
    s = store(tmp_path, [fresh("2024-06-17"), fresh("2024-06-18"), fresh("2024-06-20")], window=("2024-06-18", "2024-06-19"))
    assert s.partitions() == [d("2024-06-18")] and len(s._selector()) == 1
    empty = CurveStore("TINY", root=tmp_path / "curves", profile=AssetProfile("eod"), fixings=None, window=("2025-01-01", None))
    with pytest.raises(MarketDataUnavailable, match="no partitions"):
        empty.index


def test_unknown_asset_needs_a_profile_and_a_missing_store_is_fixtures_missing(tmp_path):
    tiny.write_calendars(tmp_path)
    (tmp_path / "curves" / "asset=XYZ").mkdir(parents=True)
    with pytest.raises(ConfigError, match="profile"):
        CurveStore("XYZ", root=tmp_path / "curves")
    assert CurveStore("XYZ", root=tmp_path / "curves", profile=AssetProfile("eod"), fixings=None).profile.kind == "eod"
    with pytest.raises(FixturesMissing):
        CurveStore("NOPE", root=tmp_path / "curves", fixings=None)
    with pytest.raises(ConfigError):
        AssetProfile("weekly")
    with pytest.raises(ConfigError):
        AssetProfile("eod", freshness="sometimes")


def test_the_provider_pickles_without_its_caches_and_serves_the_same_digest(tmp_path):
    s = store(tmp_path, [fresh("2024-06-17"), fresh("2024-06-18")], max_cached_pricers=1, max_cached_days=1)
    p = s.get_pricer(ny("2024-06-17 17:00"))
    s.get_pricer(ny("2024-06-18 17:00"))
    assert len(s._pricers) == 1 and len(s._days) == 1 and s.n_day_reads == 2
    assert s.get_pricer(ny("2024-06-17 17:00")) is not p, "evicted from the bounded cache"
    assert not {"_days", "_pricers"} & set(s.__getstate__())
    s2 = pickle.loads(pickle.dumps(s))
    assert len(s2._days) == 0 and len(s2._pricers) == 0 and s2.get_pricer(ny("2024-06-17 17:00")).digest == p.digest
    p2 = pickle.loads(pickle.dumps(p))
    assert p2.digest == p.digest and p2.snapshot == p.snapshot and isinstance(p2, SnapshotPricer)


def test_the_configuration_round_trips(tmp_path):
    s = store(tmp_path, [fresh("2024-06-17")], time={"max_staleness": "12h"}, window=("2024-06-01", None))
    cfg = s.to_config()
    assert cfg["type"] == "support.curves:CurveStore" and cfg["asset"] == "TINY" and cfg["time"]["max_staleness"] == "0 days 12:00:00" and cfg["window"] == ["2024-06-01", None]
    assert cfg["profile"]["kind"] == "eod" and cfg["fixings"] is None and cfg["curve_id"] == "sofr"
    assert s.describe()["served"] == 1 and s.describe()["window"] == ["2024-06-01", "None"] and s.describe(ny("2024-06-18 04:00"))["staleness_s"] == 11 * 3600


# ============================================================================ real fixtures
@pytest.fixture(scope="module")
def eod():
    return CurveStore(EOD)


@pytest.fixture(scope="module")
def mn():
    return CurveStore(MIN)


@needs_fixtures
def test_the_row_counts_of_every_asset_are_the_known_answers():
    for asset, (served, dropped) in CURVE_COUNTS.items():
        s = CurveStore(asset)
        assert (len(s._selector()), len(s.dropped)) == (served, dropped), asset
    assert set(CurveStore(EOD).dropped["reason"]) == {"stale_reference"}


@needs_fixtures
def test_eod_holiday_rows_are_dropped_and_never_served(eod):
    drop = eod.dropped
    assert len(drop) == 79 and set(drop["reason"]) == {"stale_reference"}
    assert {str(x) for x in drop["trading_date"]} >= set(HOLIDAY_ROWS_2026)
    days = {t.date() for t in eod.available_timestamps(ny("2026-01-01"), ny("2026-09-30"))}
    for h in HOLIDAY_ROWS_2026:
        assert d(h) not in days
        with pytest.raises(StaleSnapshot):
            eod.get_pricer(ny(f"{h} 17:00"))
    loose = CurveStore(EOD, time={"max_staleness": "2D"}, window=("2026-06-25", "2026-07-10"))
    p = loose.get_pricer(ny("2026-07-03 17:00"))
    assert p.reference_date == d("2026-07-02") and p.ts == ny("2026-07-02 17:00"), "the previous close, never the holiday row's copy"


@needs_fixtures
def test_eod_available_timestamps_follow_the_row_stamps_and_skip_holes(eod):
    got = list(eod.available_timestamps(ny("2026-08-24"), ny("2026-09-04 23:59")))
    want = ["08-24 17:00", "08-25 17:00", "08-26 15:00", "08-27 15:00", "08-28 15:00", "08-31 15:00", "09-01 15:00", "09-04 15:00"]
    assert got == [ny(f"2026-{w}") for w in want], "the stamp moved from 17:00 to 15:00 on 2026-08-26"
    with pytest.raises(StaleSnapshot):
        eod.get_pricer(ny("2026-09-02 17:00"))
    with pytest.raises(StaleSnapshot):
        eod.get_pricer(ny("2026-09-08 17:00"))


@needs_fixtures
def test_q12_uses_the_exchange_freshness_rule():
    q = CurveStore(Q12)
    assert len(q.dropped) == 90
    q_spine = CurveStore(Q12, profile={"kind": "minute", "freshness": "trading_date"})
    assert len(q_spine.dropped) == 2190, "control: the trading-date rule drops every overnight session"
    ref = [q.get_pricer(t).reference_date for t in q.available_timestamps(ny("2026-08-05 00:00"), ny("2026-08-05 06:00"))]
    assert ref and all(a <= b for a, b in zip(ref, ref[1:])), "reference dates never step backwards once the roll-lag rows are dropped"


# ------------------------------------------------------------------ the snapshot of a real row
@needs_fixtures
def test_a_real_row_becomes_a_curve_snapshot(eod):
    p = eod.get_pricer(ny("2026-09-04 15:00"))
    c = p.snapshot.curves["sofr"]
    assert isinstance(c, CurveSnapshot) and c.reference_date == d("2026-09-04") == c.node_dates[0] and c.values[0] == 1.0 and len(c.node_dates) == 45
    assert c.node_dates[1] == d("2026-09-10") and c.values[1] == pytest.approx(0.99939364, abs=1e-8)
    assert c.node_dates[-1] == d("2076-09-09") and c.values[-1] == pytest.approx(0.13583423, abs=1e-8)
    assert c.interpolation == "log_linear_df" and c.value_kind == "discount_factor" and c.provenance["source_variant"] == "CITIVELO_EXCEL_EOD"
    assert list(p.snapshot.calendars) == ["usd_fed"] and p.snapshot.calendars["usd_fed"] == calendar_data("nyc", as_name="usd_fed")
    ds = p.describe()
    assert ds["snapshot_id"] == f"{EOD}:2026-09-04T19:00:00+00:00" and ds["kind"] == "eod" and ds["trading_date"] == "2026-09-04" and ds["digest"] == p.digest
    early = eod.get_pricer(ny("2018-04-02 17:00")).snapshot.curves["sofr"]
    assert len(early.node_dates) == 26 and (early.node_dates[1] - early.node_dates[0]).days == 735, "the early grid: the first pillar is 2 years out"
    rising = eod.get_pricer(ny("2020-07-08 17:00")).snapshot.curves["sofr"]
    assert rising.values[0] == 1.0 and max(rising.values) > 1.0, "a discount factor above 1 is real data and is served"


@needs_fixtures
def test_digests_identify_the_input_not_the_provider(eod):
    t = ny("2026-08-05 17:00")
    p = eod.get_pricer(t)
    windowed = CurveStore(EOD, window=("2026-08-01", "2026-08-10"))
    assert windowed.get_pricer(t).digest == p.digest, "a window changes what is indexed, not what a row says"
    assert CurveStore(EOD, window=("2026-08-01", "2026-08-10"), fixings=None).get_pricer(t).digest != p.digest, "without fixings the input differs"
    assert eod.get_pricer(ny("2026-08-04 17:00")).digest != p.digest
    assert CurveStore(EOD, window=("2026-08-01", "2026-08-10"), curve_id="ois").get_pricer(t).digest != p.digest
    assert eod.get_pricer(t) is p


# ------------------------------------------------------------------ time mapping on real rows
@needs_fixtures
def test_asof_exact_date_on_the_eod_store(eod):
    p = eod.get_pricer(ny("2026-08-05 17:00"))
    assert p.ts == ny("2026-08-05 17:00") and p.reference_date == d("2026-08-05")
    with pytest.raises(StaleSnapshot):
        eod.get_pricer(ny("2026-08-05 16:59:59"))
    ex = CurveStore(EOD, time={"mode": "exact"}, window=("2026-08-03", "2026-08-07"))
    assert ex.get_pricer(ny("2026-08-05 17:00")).reference_date == d("2026-08-05")
    with pytest.raises(MarketDataUnavailable, match="exact"):
        ex.get_pricer(ny("2026-08-05 17:00:01"))
    dm = CurveStore(EOD, time={"mode": "date"}, window=("2026-08-03", "2026-08-07"))
    with pytest.raises(MarketDataUnavailable, match="no_snapshot_for_date"):
        dm.get_pricer(ny("2026-08-05 10:00"))
    assert dm.get_pricer(ny("2026-08-05 17:00")).reference_date == d("2026-08-05")
    assert dm.get_pricer(ny("2026-08-05 23:00")).reference_date == d("2026-08-05")


@needs_fixtures
def test_eod_visible_at_delays_the_1500_rows():
    kw = {"window": ("2026-08-20", "2026-09-04")}
    assert CurveStore(EOD, **kw).get_pricer(ny("2026-08-26 16:00")).reference_date == d("2026-08-26"), "control: from_row"
    late = CurveStore(EOD, time={"eod_visible_at": "17:00", "max_staleness": "24h"}, **kw)
    p = late.get_pricer(ny("2026-08-26 16:00"))
    assert p.reference_date == d("2026-08-25") and p.ts == ny("2026-08-25 17:00")
    assert late.get_pricer(ny("2026-08-26 17:00")).ts == ny("2026-08-26 15:00")
    with pytest.raises(StaleSnapshot):
        CurveStore(EOD, time={"eod_visible_at": "17:00"}, **kw).get_pricer(ny("2026-08-26 16:00"))
    assert late.select(ny("2026-08-26 17:00")).visible == ny("2026-08-26 17:00")


@needs_fixtures
def test_no_look_ahead_on_real_minutes(mn):
    """Independent oracle: the newest fresh raw row at or before ts, read with plain pandas."""
    raw = pd.concat([pd.read_parquet(f) for day in ("2026-08-05", "2026-08-06") for f in sorted((FIXTURES / "curves" / f"asset={MIN}" / f"date={day}").glob("*.parquet"))])
    stamps = pd.DatetimeIndex(raw["timestamp_utc"]).as_unit("ns")
    rng = np.random.default_rng(11)
    lo, hi = ny("2026-08-05 01:00").value, ny("2026-08-06 07:00").value
    served = stale_ = 0
    for v in rng.integers(lo, hi, 400):
        t = pd.Timestamp(int(v), tz="UTC").tz_convert(NY)
        want = stamps[stamps <= t].max()
        try:
            p = mn.get_pricer(t)
        except StaleSnapshot:
            assert t - want > pd.Timedelta(minutes=5)
            stale_ += 1
            continue
        assert p.ts <= t and p.ts == want
        served += 1
    assert served > 100 and stale_ > 5, "the oracle must see both outcomes"


@needs_fixtures
def test_minute_dst_days_have_tz_aware_keys_and_real_session_lengths(mn):
    nov = mn.available_timestamps(ny("2025-11-03"), ny("2025-11-03 23:59:59"))
    mar = mn.available_timestamps(ny("2026-03-09"), ny("2026-03-09 23:59:59"))
    assert len(nov) == 1058 and len(mar) == 1320
    assert nov[0] == ny("2025-11-03 01:00") and nov[0].utcoffset() == pd.Timedelta(hours=-5) and nov[0].tz_convert("UTC").hour == 6
    assert mar[0] == ny("2026-03-09 01:00") and mar[0].utcoffset() == pd.Timedelta(hours=-4) and mar[0].tz_convert("UTC").hour == 5
    assert mn.get_pricer(ny("2026-03-09 09:30")).ts.tz_convert("UTC") == pd.Timestamp("2026-03-09 13:30", tz="UTC")
    assert mn.get_pricer(ny("2026-03-06 09:30")).ts.tz_convert("UTC") == pd.Timestamp("2026-03-06 14:30", tz="UTC")
    session = [t for t in mn.available_timestamps(ny("2026-03-09"), ny("2026-03-09 23:59:59")) if dt.time(9, 30) <= t.time() <= dt.time(12, 30)]
    assert len(session) == 181


@needs_fixtures
def test_minute_holes_session_edges_and_dropped_partitions(mn):
    around = list(mn.available_timestamps(ny("2025-11-03 01:28"), ny("2025-11-03 01:33")))
    assert around == [ny(f"2025-11-03 01:{m}") for m in ("28", "29", "32", "33")]
    assert mn.get_pricer(ny("2025-11-03 01:31:30")).ts == ny("2025-11-03 01:29")
    tight = CurveStore(MIN, time={"max_staleness": "1min"}, window=("2025-11-03", "2025-11-03"))
    with pytest.raises(StaleSnapshot):
        tight.get_pricer(ny("2025-11-03 01:31:30"))
    for t in ("2026-03-08 18:00", "2026-02-16 10:00", "2026-08-05 23:30"):
        with pytest.raises(StaleSnapshot):
            mn.get_pricer(ny(t))


# ------------------------------------------------------------------ fixings on real rows
@needs_fixtures
def test_fixings_follow_the_publication_policy_at_the_row_stamp(eod, mn):
    fx = load_fixings(FIXTURES / "fixings" / "USD-SOFR-1D.parquet")
    p = eod.get_pricer(ny("2026-08-05 17:00"))
    f = p.snapshot.fixings[FIXINGS_NAME]
    assert f.dates[-1] == d("2026-08-04") and f.values[-1] == fx.loc["2026-08-04"] and f.proxied == () and f.unit == "percent"
    before = mn.get_pricer(ny("2026-08-05 07:59")).snapshot.fixings[FIXINGS_NAME]
    assert mn.get_pricer(ny("2026-08-05 07:59")).ts < ny("2026-08-05 08:00")
    assert before.dates[-1] == d("2026-08-04") and before.values[-1] == fx.loc["2026-08-03"] != fx.loc["2026-08-04"] and before.proxied == (d("2026-08-04"),)
    first_after = mn.available_timestamps(ny("2026-08-05 08:00"), ny("2026-08-05 08:10"))[0]
    after = mn.get_pricer(first_after).snapshot.fixings[FIXINGS_NAME]
    assert after.values[-1] == fx.loc["2026-08-04"] and after.proxied == (), "published at 08:00 the next business day"
    strict = CurveStore(MIN, fixings_policy_cfg={"pre_publish": "strict"}, window=("2026-08-05", "2026-08-05"))
    with pytest.raises(ValueError, match="not published"):
        strict.get_pricer(ny("2026-08-05 07:59"))
    for q in (p, mn.get_pricer(ny("2026-08-05 07:59")), mn.get_pricer(first_after)):
        assert q.snapshot.fixings[FIXINGS_NAME].dates[-1] < q.reference_date
    assert list(f.dates) == [t.date() for t in fx.index[fx.index < pd.Timestamp("2026-08-05")]]


# ------------------------------------------------------------------ contract, caching, pickling
@needs_fixtures
def test_pricer_contract_describe_and_request_keys(eod):
    p = eod.get_pricer(ny("2026-08-05 17:00"))
    assert isinstance(p, SnapshotPricer) and p.ts == ny("2026-08-05 17:00") and p.reference_date == d("2026-08-05")
    ds = p.describe()
    assert ds["snapshot_id"] == f"{EOD}:2026-08-05T21:00:00+00:00" and ds["kind"] == "eod" and ds["trading_date"] == "2026-08-05" and ds["curves"] == ["sofr"]
    assert "staleness_s" not in ds, "a shared snapshot carries no request-dependent staleness"
    prov = eod.describe(ny("2026-08-06 04:00"))
    assert prov["staleness_s"] == 11 * 3600 and prov["asset"] == EOD and prov["stamp"] == "2026-08-05T17:00:00-04:00" and prov["trading_date"] == "2026-08-05"
    assert eod.get_pricer(ny("2026-08-05 17:00"), {"curve": EOD}) is p
    with pytest.raises(ConfigError):
        eod.get_pricer(ny("2026-08-05 17:00"), {"curve_name": "USD-SOFR-1D"})
    md = MarketData({"primary": eod}, Clock())
    md.clock.advance(ny("2026-08-06 04:00"))
    assert md.pricer() is p, "one snapshot, one shared immutable pricer"
    assert eod.describe()["served"] == 2104 and eod.describe()["dropped"] == {"stale_reference": 79}


@needs_fixtures
def test_caches_are_bounded_and_pickling_drops_them():
    m = CurveStore(MIN, window=("2026-08-03", "2026-08-07"), max_cached_days=2, max_cached_pricers=3)
    for day in ("03", "04", "05", "06", "07"):
        m.get_pricer(ny(f"2026-08-{day} 10:00"))
    assert m.n_day_reads == 5 and len(m._days) == 2 and len(m._pricers) == 3
    m.get_pricer(ny("2026-08-07 10:01"))
    assert m.n_day_reads == 5, "same day: cached partition"
    p = m.get_pricer(ny("2026-08-07 10:00"))
    assert not {"_days", "_pricers"} & set(m.__getstate__()), "caches never travel with a pickled provider"
    m2 = pickle.loads(pickle.dumps(m))
    assert len(m2._days) == 0 and len(m2._pricers) == 0
    assert m2.get_pricer(ny("2026-08-07 10:00")).digest == p.digest
    p3 = pickle.loads(pickle.dumps(p))
    assert p3.digest == p.digest and p3.describe() == p.describe() and p3.snapshot.curves == p.snapshot.curves


@needs_fixtures
def test_snapshot_timings():
    """Cold = the first snapshot of a fresh provider (partition read + snapshot + digest); a repeat request is a cache hit."""
    import time

    for m, t0 in ((CurveStore(EOD, window=("2025-01-01", "2025-12-31")), ny("2025-03-03 17:00")), (CurveStore(MIN, window=("2026-08-03", "2026-08-07")), ny("2026-08-04 10:00"))):
        a = time.perf_counter()
        m.index
        b = time.perf_counter()
        m.get_pricer(t0)
        c = time.perf_counter()
        m.get_pricer(t0)
        e = time.perf_counter()
        log.info("index %.3fs, get_pricer cold %.2fms, warm %.3fms", b - a, 1e3 * (c - b), 1e3 * (e - c))
        assert e - c < 0.01
