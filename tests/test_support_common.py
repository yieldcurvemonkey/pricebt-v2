"""Calendar and fixings loaders of the test-support providers, and the fixings publication policy (`FixingsHistory`): file contracts, the calendars of the
fixtures as snapshot data, and known answers of the publication rule (proxy_last, strict, business-day lag, holidays)."""
import datetime as dt
import json

import pandas as pd
import pytest

from pricebt.errors import ConfigError
from pricebt.timeutil import Calendar
from support import common as sc
from support.common import DataQualityError, FixingsHistory, FixturesMissing, calendar_data, fixings_policy, load_calendar, load_fixings, to_calendar
from support.known import FIXTURES, needs_fixtures

pytestmark = pytest.mark.fixtures
D = dt.date
NY = "America/New_York"


def ny(s):
    return pd.Timestamp(s, tz=NY)


# ------------------------------------------------------------------ calendars
def test_calendar_data_parses_a_string_weekmask_and_keeps_provenance(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"name": "x", "source": "unit", "holidays": ["2024-07-04", "2024-01-01"], "weekmask": "Mon Tue Wed Thu Fri", "first": "2024-01-01", "last": "2024-07-04"}), encoding="utf8")
    cd = calendar_data(p)
    assert cd.name == "x" and cd.holidays == (D(2024, 1, 1), D(2024, 7, 4)) and cd.weekmask == "Mon Tue Wed Thu Fri"
    assert dict(cd.provenance) == {"source": "unit", "first": "2024-01-01", "last": "2024-07-04"}
    assert calendar_data(p, as_name="usd_fed").name == "usd_fed"
    cal = load_calendar(p)
    assert cal.weekmask == (1, 1, 1, 1, 1, 0, 0) and not cal.is_business_day(D(2024, 7, 4)) and cal.is_business_day(D(2024, 7, 5))
    p.write_text(json.dumps({"holidays": [], "weekmask": "Sun Mon Tue Wed Thu"}), encoding="utf8")
    assert load_calendar(p).weekmask == (1, 1, 1, 1, 0, 0, 1) and calendar_data(p).name == "x"


def test_calendar_data_accepts_a_list_weekmask_and_the_default(tmp_path):
    p = tmp_path / "y.json"
    p.write_text(json.dumps({"name": "y", "holidays": ["2024-12-25"], "weekmask": [1, 1, 1, 1, 0, 0, 0]}), encoding="utf8")
    cd = calendar_data(p)
    assert cd.weekmask == "Mon Tue Wed Thu" and to_calendar(cd).weekmask == (1, 1, 1, 1, 0, 0, 0)  # one canonical spelling (the digest and QuantLib both need it), whatever the file used
    p.write_text(json.dumps({"holidays": []}), encoding="utf8")
    assert calendar_data(p).weekmask == "Mon Tue Wed Thu Fri"


def test_a_missing_calendar_or_directory_is_fixtures_missing(tmp_path):
    with pytest.raises(FixturesMissing):
        calendar_data("nyc", tmp_path)
    with pytest.raises(FixturesMissing):
        calendar_data(tmp_path / "nope.json")
    with pytest.raises(FixturesMissing):
        sc.resolve_dir(tmp_path / "nope", "x")
    assert sc.resolve_dir(tmp_path, "x") == tmp_path


def test_the_fixtures_root_follows_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("PRICEBT_DATA", str(tmp_path))
    assert sc.default_fixtures_root() == tmp_path
    monkeypatch.setenv("PRICEBT_DATA", str(tmp_path / "missing"))
    with pytest.raises(FixturesMissing):
        sc.default_fixtures_root()
    monkeypatch.delenv("PRICEBT_DATA")
    monkeypatch.setenv("PRICEBT_FIXTURES", str(tmp_path))
    assert sc.default_fixtures_root() == tmp_path


@needs_fixtures
def test_the_fixture_calendars_as_snapshot_data():
    nyc, govt = calendar_data("nyc", as_name="usd_fed"), calendar_data("us_govt_bond", as_name="us_govt")
    assert (nyc.name, govt.name) == ("usd_fed", "us_govt") and nyc.weekmask == govt.weekmask == "Mon Tue Wed Thu Fri"
    assert len(nyc.holidays) == 397 and len(calendar_data("fed").holidays) == 352 and len(govt.holidays) == 393
    assert nyc.holidays[0] == D(2000, 1, 17) and nyc.holidays[-1] == D(2035, 12, 25) and dict(nyc.provenance)["first"] == "2000-01-01" and "rateslib" in dict(nyc.provenance)["source"]
    window = lambda c: {h for h in c.holidays if D(2018, 4, 2) <= h <= D(2026, 9, 4)}  # noqa: E731
    assert window(nyc) - window(govt) == {D(2021, 4, 2), D(2023, 4, 7), D(2026, 4, 3)} and not window(govt) - window(nyc), "the bond calendar works on 3 more Good Fridays"
    assert D(2026, 7, 3) in nyc.holidays and D(2026, 7, 4) not in nyc.holidays, "weekday holidays only"


@needs_fixtures
def test_the_weekdays_without_a_fixing_are_exactly_the_swap_calendar_holidays():
    pytest.importorskip("pyarrow")
    fx = load_fixings(FIXTURES / "fixings" / "USD-SOFR-1D.parquet")
    weekdays = pd.bdate_range(fx.index[0], fx.index[-1])
    missing = {t.date() for t in weekdays.difference(fx.index)}
    holidays = {h for h in calendar_data("nyc").holidays if fx.index[0].date() <= h <= fx.index[-1].date()}
    assert len(missing) == 94 and missing == holidays


# ------------------------------------------------------------------ fixings file
def test_load_fixings_contract(tmp_path):
    pytest.importorskip("pyarrow")
    days = pd.bdate_range("2024-01-02", periods=5).date
    p = tmp_path / "f.parquet"
    pd.DataFrame({"date": days, "rate": [0.0531, 0.0532, 0.0533, 0.0531, 0.0530]}).to_parquet(p)
    s = load_fixings(p)
    assert s.iloc[0] == pytest.approx(5.31) and s.index[0] == pd.Timestamp("2024-01-02") and s.index.tz is None and s.iloc[0] == 0.0531 * 100.0
    pd.DataFrame({"date": days[::-1], "rate": [0.05] * 5}).to_parquet(p)
    with pytest.raises(DataQualityError, match="ascending"):
        load_fixings(p)
    pd.DataFrame({"date": days, "rate": [5.31] * 5}).to_parquet(p)
    with pytest.raises(DataQualityError, match="outside"):
        load_fixings(p)
    assert load_fixings(p, unit="percent").iloc[0] == pytest.approx(5.31)
    pd.DataFrame({"date": days, "rate": [0.0, 0.05, 0.05, 0.05, 0.05]}).to_parquet(p)
    with pytest.raises(DataQualityError, match="outside"):
        load_fixings(p)
    pd.DataFrame({"date": days, "rate": [0.05, float("nan"), 0.05, 0.05, 0.05]}).to_parquet(p)
    with pytest.raises(DataQualityError, match="outside"):
        load_fixings(p)
    pd.DataFrame({"date": [days[0], days[0]], "rate": [0.05, 0.05]}).to_parquet(p)
    with pytest.raises(DataQualityError, match="duplicated"):
        load_fixings(p)
    with pytest.raises(FixturesMissing):
        load_fixings(tmp_path / "missing.parquet")
    with pytest.raises(ConfigError):
        load_fixings(p, unit="bp")


@needs_fixtures
def test_the_fixings_fixture_is_a_clean_decimal_history():
    pytest.importorskip("pyarrow")
    fx = load_fixings(FIXTURES / "fixings" / "USD-SOFR-1D.parquet")
    assert len(fx) == 2106 and fx.index[0] == pd.Timestamp("2018-04-02") and fx.index[-1] == pd.Timestamp("2026-09-04")
    assert fx.loc["2018-04-02"] == pytest.approx(1.80) and fx.loc["2026-09-04"] == pytest.approx(3.65) and fx.loc["2019-09-17"] == pytest.approx(5.25)
    assert fx.min() == pytest.approx(0.01) and fx.max() == pytest.approx(5.40) and fx.index.is_unique and fx.index.is_monotonic_increasing


def test_fixings_policy_maps_the_spine_form_to_the_history_kwargs():
    assert fixings_policy(None) == {"after_bdays": 1, "at": dt.time(8, 0), "pre_publish": "proxy_last"}
    got = fixings_policy({"visible_after": {"business_days": 2, "time": "09:15"}, "pre_publish": "strict"})
    assert got == {"after_bdays": 2, "at": dt.time(9, 15), "pre_publish": "strict"}
    assert fixings_policy({"after_bdays": 0, "at": dt.time(0, 0)}) == {"after_bdays": 0, "at": dt.time(0, 0), "pre_publish": "proxy_last"}
    with pytest.raises(ConfigError):
        fixings_policy({"visible_after": {"days": 1}})
    with pytest.raises(ConfigError):
        fixings_policy({"pre_publish": "ffill"})
    with pytest.raises(ConfigError):
        fixings_policy({"lag": 1})
    with pytest.raises(ConfigError):
        sc.to_time(8)


# ------------------------------------------------------------------ the publication rule (known answers on a small calendar)
CAL = Calendar([D(2024, 5, 27)], name="unit")  # Memorial Day
DATES = [D(2024, 5, 21), D(2024, 5, 22), D(2024, 5, 23), D(2024, 5, 24), D(2024, 5, 28), D(2024, 5, 29)]
VALUES = [5.30, 5.31, 5.32, 5.33, 5.35, 5.36]


def history(**policy):
    return FixingsHistory(pd.Series(VALUES, index=pd.DatetimeIndex(DATES)), CAL, fixings_policy(policy or None), NY, name="R")


def test_a_published_history_is_cut_before_the_reference_date():
    f = history().at(ny("2024-05-29 17:00"), D(2024, 5, 29))
    assert f.name == "R" and f.unit == "percent" and f.dates == tuple(DATES[:5]) and f.values == tuple(VALUES[:5]) and f.proxied == ()


def test_before_0800_the_newest_fixing_is_proxied_by_the_last_public_level():
    f = history().at(ny("2024-05-29 07:59"), D(2024, 5, 29))
    assert f.dates == tuple(DATES[:5]), "the series stays contiguous over business days"
    assert f.values == (5.30, 5.31, 5.32, 5.33, 5.33) and f.proxied == (D(2024, 5, 28),)
    at8 = history().at(ny("2024-05-29 08:00"), D(2024, 5, 29))
    assert at8.values[-1] == 5.35 and at8.proxied == (), "published at 08:00 sharp"


def test_the_lag_counts_business_days_across_a_holiday():
    """The fixing of Friday 05-24 is public from the next business day, Tuesday 05-28 (Monday is a holiday)."""
    assert history().at(ny("2024-05-28 07:59"), D(2024, 5, 28)).proxied == (D(2024, 5, 24),)
    f = history().at(ny("2024-05-28 08:00"), D(2024, 5, 28))
    assert f.dates[-1] == D(2024, 5, 24) and f.values[-1] == 5.33 and f.proxied == ()
    assert history().at(ny("2024-05-28 07:59"), D(2024, 5, 28)).values == (5.30, 5.31, 5.32, 5.32)


def test_strict_refuses_an_unpublished_fixing():
    with pytest.raises(ValueError, match="not published"):
        history(pre_publish="strict").at(ny("2024-05-29 07:59"), D(2024, 5, 29))
    assert history(pre_publish="strict").at(ny("2024-05-29 08:00"), D(2024, 5, 29)).proxied == ()


def test_a_longer_lag_and_a_later_time_move_the_publication_instant():
    two = history(visible_after={"business_days": 2, "time": "08:00"})
    assert two.at(ny("2024-05-29 17:00"), D(2024, 5, 29)).proxied == (D(2024, 5, 28),), "05-28's fixing is public only on 05-30"
    late = history(visible_after={"business_days": 1, "time": "09:15"})
    assert late.at(ny("2024-05-29 09:14"), D(2024, 5, 29)).proxied == (D(2024, 5, 28),) and late.at(ny("2024-05-29 09:15"), D(2024, 5, 29)).proxied == ()
    now = history(visible_after={"business_days": 0, "time": "00:00"})
    assert now.at(ny("2024-05-29 00:00"), D(2024, 5, 29)).proxied == ()


def test_no_history_before_the_reference_date_is_an_empty_series_even_under_strict():
    f = history(pre_publish="strict").at(ny("2024-05-21 03:00"), D(2024, 5, 21))
    assert f.dates == () and f.values == () and f.proxied == ()


def test_a_proxy_needs_something_to_repeat():
    only = FixingsHistory(pd.Series([5.3], index=pd.DatetimeIndex([DATES[3]])), CAL, fixings_policy(None), NY)
    f = only.at(ny("2024-05-28 07:59"), D(2024, 5, 28))
    assert f.dates == () and f.proxied == (), "the only fixing is the unpublished one: nothing to proxy with"


def test_the_series_never_reaches_the_reference_date_or_repeats_a_date():
    h = history()
    for ref, when in ((D(2024, 5, 24), "2024-05-24 12:00"), (D(2024, 5, 29), "2024-05-29 03:00"), (D(2024, 5, 28), "2024-05-28 08:00")):
        f = h.at(ny(when), ref)
        assert f.dates[-1] < ref and list(f.dates) == sorted(set(f.dates))
