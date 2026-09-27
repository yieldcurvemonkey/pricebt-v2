"""`support.ust_eod.UstEod` / `support.ust_minute.UstMinute` (bond quote panels -> `QuoteSet` snapshots) and the on-the-run universe: every reference-data rule of
the old FedInvest / Webull readers (zero-price and thin days, price band, partial days, bid/offer pairs, session, flat days, yield band, the bad-tick filter,
forward-fill flags) on tiny trees with known answers and on the real fixtures (on-the-run known answers, the 13 zero-price days, the 5 partial panels, the
2026-03-12 spike)."""
import datetime as dt
import pickle

import pandas as pd
import pytest

from pricebt.errors import ConfigError, MarketDataUnavailable, StaleSnapshot
from pricebt.snapshot import Quote, QuoteSet
from pricebt.timeutil import Calendar
from support import tiny
from support.common import DataQualityError, FixturesMissing, calendar_data
from support.curves import AssetProfile, CurveStore
from support.known import (
    EOD, FEDINVEST_PARTIAL_DAYS_2023, FEDINVEST_ROWS_OUT_OF_BAND, FEDINVEST_SERVED, FEDINVEST_SERVED_PARTIAL, FEDINVEST_UNREFERENCED, FEDINVEST_ZERO_DAYS, FIXTURES, ON_THE_RUN,
    WEBULL_FILTERED_COUNTS, WEBULL_FLAT, WEBULL_SERVED_MINUTES, d, needs_fixtures, ny,
)
from support.ust_common import Reference, Universe
from support.ust_eod import UstEod
from support.ust_minute import UstMinute

pytestmark = pytest.mark.fixtures
pytest.importorskip("pyarrow")
D = dt.date
FED = Calendar([D.fromisoformat(h) for h in tiny.FED_HOLIDAYS], name="fed")


# ============================================================================ the on-the-run universe (pure rules)
def table(*rows):
    """(cusip, oi, auction, issue, maturity, cpn) rows as a reference DataFrame."""
    return pd.DataFrame([{"cusip": r[0], "oi": r[1], "auction_date": None if r[2] is None else D.fromisoformat(r[2]), "issue_date": D.fromisoformat(r[3]),
                          "maturity_date": D.fromisoformat(r[4]), "cpn": r[5], "label": f"L {r[0]}"} for r in rows])


UNI = Universe(table(*tiny.UNIVERSE), FED)


def test_a_new_issue_is_on_the_run_from_the_business_day_after_its_issue_date():
    """Auction Thu 2024-05-09, issue Wed 05-15: the effective rule is `asof > max(auction + 1 business day, issue)`, not 'the day after the auction'."""
    assert UNI.aliases(D(2024, 5, 13)) == {"CT10": "TEN000001", "O10": "TEN000003", "CT2": "TWO000001"}
    assert UNI.aliases(D(2024, 5, 15))["CT10"] == "TEN000001", "on its issue date the new bond is not yet on the run (issue_date < asof is strict)"
    a16 = UNI.aliases(D(2024, 5, 16))
    assert a16["CT10"] == "TEN000002" and a16["O10"] == "TEN000001" and a16["OO10"] == "TEN000003"


def test_the_roll_date_is_one_business_day_after_the_auction_on_the_roll_calendar():
    u = Universe(table(("B1", "10-Year", "2024-06-18", "2024-06-28", "2034-06-15", 4.0), ("B2", "10-Year", "2024-05-09", "2024-05-13", "2034-05-15", 4.0)), FED)
    roll = dict(zip(u.table["cusip"], u.table["roll_date"]))
    assert roll["B1"] == D(2024, 6, 20), "the day after the auction (06-19) is a holiday of the roll calendar"
    assert roll["B2"] == D(2024, 5, 10)
    weekends = Universe(table(("B1", "10-Year", "2024-06-18", "2024-06-28", "2034-06-15", 4.0)), Calendar.weekends_only())
    assert weekends.table["roll_date"][0] == D(2024, 6, 19)
    assert Universe(table(("B1", "10-Year", "2024-06-18", "2024-06-28", "2034-06-15", 4.0))).table["roll_date"][0] == D(2024, 6, 19), "default: weekends only"


def test_the_roll_date_falls_back_to_the_issue_date_without_an_auction():
    u = Universe(table(("B1", "10-Year", None, "2024-06-03", "2034-06-15", 4.0)), FED)
    assert u.table["roll_date"][0] == D(2024, 6, 3)
    assert u.aliases(D(2024, 6, 3)) == {} and u.aliases(D(2024, 6, 4)) == {"CT10": "B1"}


def test_eligibility_bounds_maturity_inclusive_issue_and_roll_strict():
    u = Universe(table(("B1", "10-Year", "2024-01-09", "2024-01-15", "2024-06-14", 4.0)), FED)
    assert u.aliases(D(2024, 1, 15)) == {} and u.aliases(D(2024, 1, 16)) == {"CT10": "B1"}
    assert u.aliases(D(2024, 6, 14)) == {"CT10": "B1"}, "a bond maturing on asof is still eligible"
    assert u.aliases(D(2024, 6, 15)) == {}
    late_roll = Universe(table(("B1", "10-Year", "2024-01-09", "2024-01-05", "2034-06-14", 4.0)), FED)  # issued BEFORE the auction date + 1 (data quirk)
    assert late_roll.table["roll_date"][0] == D(2024, 1, 10)
    assert late_roll.aliases(D(2024, 1, 10)) == {} and late_roll.aliases(D(2024, 1, 11)) == {"CT10": "B1"}


def test_ranks_are_by_issue_date_newest_first_ties_in_table_order_and_capped_at_four():
    rows = [(f"X{i}", "5-Year", "2023-01-01", f"2023-0{i}-15", "2030-01-01", 3.0) for i in range(1, 7)] + [("Y1", "5-Year", "2023-01-01", "2023-06-15", "2030-01-01", 3.0)]
    u = Universe(table(*rows), FED)
    a = u.aliases(D(2024, 1, 2))
    assert a == {"CT5": "X6", "O5": "Y1", "OO5": "X5", "OOO5": "X4"}, "X6 and Y1 share an issue date: the earlier table row ranks first ('first' method), and only 4 ranks exist"
    e = u.ranked(D(2024, 1, 2))
    assert sorted(e["rank"]) == list(range(7))


def test_only_year_buckets_get_aliases_and_each_bucket_ranks_on_its_own():
    u = Universe(table(("T1", "10-Year", "2024-01-09", "2024-01-15", "2034-01-15", 4.0), ("T2", "30-Year", "2024-01-11", "2024-01-15", "2054-01-15", 4.5),
                       ("T3", "TIPS", "2024-01-11", "2024-01-15", "2034-01-15", 1.0), ("T4", "2-Year", "2024-01-09", "2024-01-15", "2026-01-15", 3.0),
                       ("T5", "10-Year TIPS", "2024-01-12", "2024-01-15", "2034-01-15", 1.0), ("T6", "26-Week", "2024-01-12", "2024-01-15", "2024-07-15", 0.0)), FED)
    assert u.aliases(D(2024, 2, 1)) == {"CT10": "T1", "CT30": "T2", "CT2": "T4"}, "a bucket must be exactly '<n>-Year': TIPS, bills and 'Year TIPS' buckets are not on-the-run buckets"


def test_a_universe_needs_its_columns():
    with pytest.raises(ConfigError, match="lacks columns"):
        Universe(table(*tiny.UNIVERSE).drop(columns=["cpn"]))


def test_the_reference_holds_securities_with_a_coupon_and_a_valid_life():
    t = table(("A", "10-Year", "2024-01-09", "2024-01-15", "2034-01-15", 4.0), ("B", "10-Year", "2024-01-09", "2024-01-15", "2034-01-15", None),
              ("C", "10-Year", "2024-01-09", "2024-01-15", "2024-01-15", 4.0))
    t.loc[0, "label"] = None
    r = Reference(t)
    assert list(r.securities) == ["A"] and r.get("B") is None and r.get("C") is None
    s = r.get("A")
    assert (s.id, s.coupon, s.issue_date, s.maturity_date, s.label) == ("A", 4.0, D(2024, 1, 15), D(2034, 1, 15), "None"), "the label is str() of the table cell, as before"


# ============================================================================ tiny end-of-day panels
def prow(day, cusip, eod, bid=0.0, off=0.0):
    return (day, cusip, eod, bid, off)


def eod_provider(tmp_path, rows, universe=tiny.UNIVERSE, **kw):
    tiny.write_calendars(tmp_path)
    tiny.write_reference(tmp_path, universe)
    tiny.write_prices(tmp_path, rows)
    kw.setdefault("min_valid_rows", 2)
    return UstEod(root=tmp_path / "ust", prices="prices.parquet", **kw)


NORMAL = [prow("2024-05-13", "TEN000001", 99.0, 98.9, 99.1), prow("2024-05-13", "TEN000003", 95.0, 0.0, 95.1), prow("2024-05-13", "TWO000001", 100.0, 99.9, 100.1),
          prow("2024-05-13", "NOREF0001", 90.0, 89.9, 90.1)]
ZERO = [prow("2024-05-14", c, 0.0) for c in ("TEN000001", "TEN000003", "TWO000001")]
THIN = [prow("2024-05-15", "TEN000001", 99.5), prow("2024-05-15", "TEN000003", 10.0), prow("2024-05-15", "TWO000001", 10.0)]
DAY16 = [prow("2024-05-16", "TEN000001", 99.2), prow("2024-05-16", "TEN000002", 101.0, 100.9, 101.1), prow("2024-05-16", "TWO000001", 100.0)]
DAY17 = [prow("2024-05-17", "TEN000002", 101.5), prow("2024-05-17", "TEN000001", 99.4), prow("2024-05-17", "TEN000003", 95.5)]


def test_zero_price_and_thin_days_are_removed_never_served(tmp_path):
    p = eod_provider(tmp_path, NORMAL + ZERO + THIN + DAY16)
    assert [(str(r.date), r.valid_rows, r.reason) for r in p.dropped_days.itertuples()] == [("2024-05-14", 0, "zero_eod_day"), ("2024-05-15", 1, "thin_day(1)")]
    assert [str(t.date()) for t in p.available_timestamps(ny("2024-05-01"), ny("2024-05-31"))] == ["2024-05-13", "2024-05-16"]
    for day in ("2024-05-14", "2024-05-15"):
        with pytest.raises(StaleSnapshot):
            p.get_pricer(ny(f"{day} 17:00"))
    assert p.n_rows_out_of_band == 3 + 2
    by_date = eod_provider(tmp_path / "d", NORMAL + ZERO + DAY16, time={"mode": "date"})
    with pytest.raises(MarketDataUnavailable, match="no_snapshot_for_date"):
        by_date.get_pricer(ny("2024-05-14 17:00"))
    loose = eod_provider(tmp_path / "l", NORMAL + ZERO + DAY16, time={"max_staleness": "3D"})
    assert loose.get_pricer(ny("2024-05-14 17:00")).reference_date == d("2024-05-13"), "control: a loose bound serves the previous day, never zeros"


def test_the_minimum_of_valid_rows_decides_thin_days(tmp_path):
    rows = NORMAL + THIN
    assert len(eod_provider(tmp_path / "a", rows, min_valid_rows=1).dropped_days) == 0
    assert list(eod_provider(tmp_path / "b", rows, min_valid_rows=2).dropped_days["reason"]) == ["thin_day(1)"]
    assert list(eod_provider(tmp_path / "c", rows, min_valid_rows=4).dropped_days["reason"]) == ["thin_day(1)"], "05-13 has 4 valid rows, and the unreferenced one counts"
    assert list(eod_provider(tmp_path / "e", rows, min_valid_rows=5).dropped_days["reason"]) == ["thin_day(4)", "thin_day(1)"]


def test_the_price_band_is_inclusive_and_rows_outside_it_are_not_quotes(tmp_path):
    rows = [prow("2024-05-13", "TEN000001", 20.0), prow("2024-05-13", "TEN000003", 250.0), prow("2024-05-13", "TWO000001", 19.99), prow("2024-05-13", "TEN000002", 250.01),
            prow("2024-05-13", "NOREF0001", 100.0)]
    p = eod_provider(tmp_path, rows, min_valid_rows=1)
    q = p.get_pricer(ny("2024-05-13 17:00")).snapshot.quotes
    assert sorted(q.quotes) == ["TEN000001", "TEN000003"] and p.n_rows_out_of_band == 2
    tight = eod_provider(tmp_path / "t", rows, min_valid_rows=1, price_band=(50.0, 250.0))
    assert tight.n_rows_out_of_band == 3 and sorted(tight.get_pricer(ny("2024-05-13 17:00")).snapshot.quotes.quotes) == ["TEN000003"]
    for bad in ((0.0, 10.0), (10.0, 10.0), (20.0, 10.0), (-1.0, 5.0)):
        with pytest.raises(ConfigError, match="price_band"):
            eod_provider(tmp_path / "x", rows, price_band=bad)


def test_a_day_is_a_quote_panel_of_referenced_bonds_with_bid_offer_pairs(tmp_path):
    p = eod_provider(tmp_path, NORMAL + DAY16)
    sp = p.get_pricer(ny("2024-05-13 17:00"))
    qs = sp.snapshot.quotes
    assert isinstance(qs, QuoteSet) and qs.price_convention == "market" and qs.reference_date == d("2024-05-13") == sp.reference_date
    assert sorted(qs.quotes) == ["TEN000001", "TEN000003", "TWO000001"], "the price file's NOREF0001 has no reference data: no quote"
    assert qs.quotes["TEN000001"] == Quote(clean=99.0, bid=98.9, offer=99.1)
    assert qs.quotes["TEN000003"] == Quote(clean=95.0), "a zero bid means a missing side: neither side is kept"
    assert qs.quotes["TWO000001"].bid == 99.9 and qs.quotes["TWO000001"].offer == 100.1
    assert qs.aliases == {"CT10": "TEN000001", "O10": "TEN000003", "CT2": "TWO000001"}
    assert set(qs.securities) == {"TEN000001", "TEN000003", "TWO000001"} and qs.securities["TEN000001"].coupon == 4.0 and qs.securities["TEN000001"].label == "L TEN000001"
    assert qs.provenance == {} and sp.snapshot.fixings == {} and sp.snapshot.curves == {}
    assert list(sp.snapshot.calendars) == ["usd_fed", "us_govt"] and sp.snapshot.calendars["us_govt"].holidays == calendar_data("us_govt_bond", tmp_path / "calendars").holidays


def test_an_alias_target_without_a_quote_is_still_a_security_of_the_snapshot(tmp_path):
    """05-16: CT10 is TEN000002 (quoted) but O10/OO10 are quoted only when the panel has them; a target absent from the panel stays resolvable."""
    rows = [prow("2024-05-16", "TEN000002", 101.0), prow("2024-05-16", "TWO000001", 100.0)]
    qs = eod_provider(tmp_path, rows).get_pricer(ny("2024-05-16 17:00")).snapshot.quotes
    assert qs.aliases == {"CT10": "TEN000002", "O10": "TEN000001", "OO10": "TEN000003", "CT2": "TWO000001"}
    assert sorted(qs.quotes) == ["TEN000002", "TWO000001"] and {"TEN000001", "TEN000003"} <= set(qs.securities)


def test_an_on_the_run_bond_without_a_coupon_has_no_alias_and_does_not_break_the_snapshot(tmp_path):
    """A not yet announced coupon cannot be reference data: the bond is left out (no alias, no security), the other ranks are unchanged."""
    uni = tuple(r for r in tiny.UNIVERSE if r[0] != "TEN000002") + (("TEN0000NC", "10-Year", "2024-05-09", "2024-05-15", "2034-05-15", None),)
    p = eod_provider(tmp_path, [prow("2024-05-16", "TEN000001", 99.2), prow("2024-05-16", "TEN000003", 95.0), prow("2024-05-16", "TWO000001", 100.0)], universe=uni)
    qs = p.get_pricer(ny("2024-05-16 17:00")).snapshot.quotes
    assert qs.aliases == {"O10": "TEN000001", "OO10": "TEN000003", "CT2": "TWO000001"} and "TEN0000NC" not in qs.securities and sorted(qs.quotes) == ["TEN000001", "TEN000003", "TWO000001"]
    assert Universe(table(*uni), FED).aliases(D(2024, 5, 16))["CT10"] == "TEN0000NC", "control: the universe itself ranks it first"


def test_the_ten_year_roll_over_the_issue_date_in_the_snapshots(tmp_path):
    p = eod_provider(tmp_path, NORMAL + [prow("2024-05-14", "TEN000001", 99.1), prow("2024-05-14", "TEN000003", 95.1), prow("2024-05-15", "TEN000001", 99.2), prow("2024-05-15", "TEN000003", 95.2)] + DAY16)
    got = {day: p.get_pricer(ny(f"{day} 17:00")).snapshot.quotes.aliases["CT10"] for day in ("2024-05-13", "2024-05-14", "2024-05-15", "2024-05-16")}
    assert got == {"2024-05-13": "TEN000001", "2024-05-14": "TEN000001", "2024-05-15": "TEN000001", "2024-05-16": "TEN000002"}


def test_the_stamp_is_the_stamp_time_local_and_visibility_follows_it(tmp_path):
    p = eod_provider(tmp_path, NORMAL + DAY16)
    sp = p.get_pricer(ny("2024-05-13 17:00"))
    assert sp.ts == ny("2024-05-13 17:00") and sp.ts.tz_convert("UTC").hour == 21 and sp.snapshot.provenance["visible_at"] == "2024-05-13T17:00:00-04:00"
    with pytest.raises(MarketDataUnavailable, match="before_first_snapshot"):
        p.get_pricer(ny("2024-05-13 16:59"))
    lenient = eod_provider(tmp_path / "v", NORMAL + DAY16, time={"max_staleness": "4D"})
    assert lenient.get_pricer(ny("2024-05-16 16:59")).reference_date == d("2024-05-13") and lenient.get_pricer(ny("2024-05-16 17:00")).reference_date == d("2024-05-16")
    early = eod_provider(tmp_path / "e", NORMAL, stamp_time="15:30")
    assert early.get_pricer(ny("2024-05-13 15:30")).ts == ny("2024-05-13 15:30")
    winter = eod_provider(tmp_path / "w", [prow("2024-01-16", "TEN000001", 99.0), prow("2024-01-16", "TWO000001", 99.0)])
    assert winter.get_pricer(ny("2024-01-16 17:00")).ts.tz_convert("UTC").hour == 22, "17:00 New York in winter is 22:00 UTC"


def test_partial_days_are_dropped_by_the_neighbour_rule_only_when_asked(tmp_path):
    """Neighbouring served days a, b, c: b is partial when at least N CUSIPs quoted on BOTH a and c are absent from b."""
    days = ["2024-05-13", "2024-05-14", "2024-05-16", "2024-05-17"]
    full = {"A": 99.0, "B": 98.0, "C": 97.0}
    rows = []
    for day, cusips in zip(days, (full, {"A": 99.0}, full, full)):
        rows += [prow(day, c, v) for c, v in cusips.items()]
    uni = tuple((c, "10-Year", "2023-01-01", "2023-01-15", "2040-01-15", 4.0) for c in "ABC")
    off = eod_provider(tmp_path / "off", rows, universe=uni, min_valid_rows=1)
    assert len(off.dropped_days) == 0
    on = eod_provider(tmp_path / "on", rows, universe=uni, min_valid_rows=1, partial_day_min_missing=2)
    assert [(str(r.date), r.reason) for r in on.dropped_days.itertuples()] == [("2024-05-14", "partial_day(2 missing)")]
    assert [str(t.date()) for t in on.available_timestamps(ny("2024-05-01"), ny("2024-05-31"))] == ["2024-05-13", "2024-05-16", "2024-05-17"]
    assert len(eod_provider(tmp_path / "three", rows, universe=uni, min_valid_rows=1, partial_day_min_missing=3).dropped_days) == 0, "2 missing is below a threshold of 3"
    assert len(eod_provider(tmp_path / "one", rows, universe=uni, min_valid_rows=1, partial_day_min_missing=1).dropped_days) == 1
    for bad in (0, -1):
        with pytest.raises(ConfigError, match="partial_day_min_missing"):
            eod_provider(tmp_path / "bad", rows, universe=uni, partial_day_min_missing=bad)


def test_the_partial_day_rule_uses_the_original_served_neighbours(tmp_path):
    """A day dropped as partial still counts as the previous day of the next triple: a, b, c, d with {A}, {C}, {A}, {C} drops BOTH b and c."""
    rows = [prow("2024-05-13", "A", 99.0), prow("2024-05-14", "C", 97.0), prow("2024-05-16", "A", 99.0), prow("2024-05-17", "C", 97.0)]
    uni = tuple((c, "10-Year", "2023-01-01", "2023-01-15", "2040-01-15", 4.0) for c in "AC")
    p = eod_provider(tmp_path, rows, universe=uni, min_valid_rows=1, partial_day_min_missing=1)
    assert [str(r.date) for r in p.dropped_days.itertuples()] == ["2024-05-14", "2024-05-16"]
    assert [str(t.date()) for t in p.available_timestamps(ny("2024-05-01"), ny("2024-05-31"))] == ["2024-05-13", "2024-05-17"]


def test_an_invalid_row_is_not_part_of_the_neighbour_sets(tmp_path):
    """Only VALIDLY quoted CUSIPs count: an out-of-band print on the outer days does not make the middle day partial."""
    rows = [prow("2024-05-13", "A", 99.0), prow("2024-05-13", "B", 5.0), prow("2024-05-14", "A", 99.0), prow("2024-05-16", "A", 99.0), prow("2024-05-16", "B", 5.0)]
    uni = tuple((c, "10-Year", "2023-01-01", "2023-01-15", "2040-01-15", 4.0) for c in "AB")
    assert len(eod_provider(tmp_path, rows, universe=uni, min_valid_rows=1, partial_day_min_missing=1).dropped_days) == 0


def test_duplicate_rows_and_missing_files_are_refused(tmp_path):
    with pytest.raises(DataQualityError, match="duplicated"):
        eod_provider(tmp_path, NORMAL + [prow("2024-05-13", "TEN000001", 99.5)])
    with pytest.raises(FixturesMissing):
        UstEod(root=tmp_path / "ust", prices="nope.parquet", min_valid_rows=2)
    with pytest.raises(FixturesMissing):
        UstEod(root=tmp_path / "nothing")
    tiny.write_reference(tmp_path / "dup", list(tiny.UNIVERSE) + [tiny.UNIVERSE[0]])
    tiny.write_calendars(tmp_path / "dup")
    tiny.write_prices(tmp_path / "dup", NORMAL)
    with pytest.raises(DataQualityError, match="duplicated CUSIPs"):
        UstEod(root=tmp_path / "dup" / "ust", prices="prices.parquet")


def test_requests_and_windows(tmp_path):
    p = eod_provider(tmp_path, NORMAL + DAY16 + DAY17, window=("2024-05-14", "2024-05-16"))
    assert [str(t.date()) for t in p.available_timestamps(ny("2024-05-01"), ny("2024-05-31"))] == ["2024-05-16"]
    with pytest.raises(ConfigError):
        p.get_pricer(ny("2024-05-16 17:00"), {"curve": "x"})
    assert p.get_pricer(ny("2024-05-16 17:00"), {}) is p.get_pricer(ny("2024-05-16 17:00"))
    with pytest.raises(MarketDataUnavailable):
        p.panel(D(2024, 5, 17))
    assert list(p.panel(D(2024, 5, 16))["cusip"]) == ["TEN000001", "TEN000002", "TWO000001"] and p.panel(D(2024, 5, 16))["valid"].all()


def test_fixings_and_a_funding_curve_come_from_the_snapshot_providers(tmp_path):
    days = pd.bdate_range("2024-05-06", "2024-05-16")
    tiny.write_fixings(tmp_path, days, [0.0530 + 0.0001 * i for i in range(len(days))])
    p = eod_provider(tmp_path, NORMAL + DAY16, fixings="auto")
    sp = p.get_pricer(ny("2024-05-13 17:00"))
    f = sp.snapshot.fixings["USD-SOFR-1D"]
    assert f.dates[-1] == d("2024-05-10") and f.unit == "percent" and f.values[0] == pytest.approx(5.30) and f.proxied == ()
    tiny.write_curve_store(tmp_path, "TINYC", [tiny.curve_row("2024-05-13 21:00", "2024-05-13", tiny.nodes("2024-05-13", 0.997, 0.994)),
                                                tiny.curve_row("2024-05-16 21:00", "2024-05-15", tiny.nodes("2024-05-15", 0.997, 0.994))])
    cs = CurveStore("TINYC", root=tmp_path / "curves", profile=AssetProfile("eod"), fixings=tmp_path / "fixings" / "USD-SOFR-1D.parquet")
    funded = eod_provider(tmp_path / "f", NORMAL + DAY16, curve_mdp=cs)
    sp = funded.get_pricer(ny("2024-05-13 17:00"))
    assert list(sp.snapshot.curves) == ["sofr"] and sp.snapshot.curves["sofr"].reference_date == d("2024-05-13") and sp.snapshot.fixings["USD-SOFR-1D"].dates[-1] == d("2024-05-10")
    with pytest.raises(MarketDataUnavailable, match="funding curve is anchored"):
        eod_provider(tmp_path / "g", NORMAL + DAY16, curve_mdp=cs, time={"max_staleness": "3D"}).get_pricer(ny("2024-05-16 17:00"))
    with pytest.raises(ConfigError, match="not both"):
        eod_provider(tmp_path / "h", NORMAL, curve_mdp=cs, fixings="auto")
    with pytest.raises(ConfigError, match="no config form"):
        funded.to_config()


def test_the_eod_provider_pickles_without_caches_and_describes_itself(tmp_path):
    p = eod_provider(tmp_path, NORMAL + ZERO + DAY16, partial_day_min_missing=5, max_cached_pricers=1)
    a = p.get_pricer(ny("2024-05-13 17:00"))
    p.get_pricer(ny("2024-05-16 17:00"))
    assert len(p._pricers) == 1 and p.get_pricer(ny("2024-05-13 17:00")) is not a
    m = pickle.loads(pickle.dumps(p))
    assert len(m._pricers) == 0 and m._aliases == {} and m.get_pricer(ny("2024-05-13 17:00")).digest == a.digest
    assert pickle.loads(pickle.dumps(a)).digest == a.digest
    ds = p.describe()
    assert ds["served"] == 2 and ds["dropped_days"] == {"2024-05-14": "zero_eod_day"} and ds["rows_out_of_band"] == 3 and ds["partial_day_min_missing"] == 5
    assert p.describe(ny("2024-05-14 04:00"))["staleness_s"] == 11 * 3600
    cfg = p.to_config()
    assert cfg["type"] == "support.ust_eod:UstEod" and cfg["partial_day_min_missing"] == 5 and cfg["price_band"] == [20.0, 250.0] and cfg["stamp_time"] == "17:00"


# ============================================================================ tiny minute panels
NYT = "America/New_York"
FOUR = ("TEN000001", "TEN000002", "TEN000003", "TWO000001")


def minute_provider(tmp_path, rows, **kw):
    tiny.write_calendars(tmp_path)
    tiny.write_reference(tmp_path, tiny.UNIVERSE)
    tiny.write_minutes(tmp_path, rows)
    return UstMinute(root=tmp_path / "ust", quotes="minutes.parquet", **kw)


def series(day, start, cusip_values, step=1):
    """Rows for consecutive minutes from local `day start`: {cusip: [yields]} (a value of None skips that CUSIP's row)."""
    t0 = pd.Timestamp(f"{day} {start}", tz=NYT)
    rows = []
    for c, vals in cusip_values.items():
        for k, v in enumerate(vals):
            if v is not None:
                rows.append(((t0 + pd.Timedelta(minutes=step * k)).tz_convert("UTC").isoformat(), c, v))
    return rows


def ramp(n, base, inc=0.001):
    return [round(base + inc * k, 6) for k in range(n)]


def four(day, start, n, spike=None, inc=0.001):
    """Four moving CUSIPs; `spike` = (cusip, first minute index, list of added yields) adds a bad print to one of them."""
    vals = {c: ramp(n, 4.0 + i * 0.1, inc) for i, c in enumerate(FOUR)}
    if spike:
        c, k0, adds = spike
        for j, a in enumerate(adds):
            vals[c][k0 + j] = round(vals[c][k0 + j] + a, 6)
    return series(day, start, vals)


def test_minute_quotes_are_yields_at_the_local_date_with_market_convention(tmp_path):
    p = minute_provider(tmp_path, four("2024-06-04", "10:00", 20))
    sp = p.get_pricer(ny("2024-06-04 10:05"))
    qs = sp.snapshot.quotes
    assert sp.reference_date == d("2024-06-04") == qs.reference_date and sp.ts == ny("2024-06-04 10:05") and qs.price_convention == "market"
    assert qs.quotes["TEN000001"] == Quote(ytm=4.005) and qs.quotes["TWO000001"].ytm == pytest.approx(4.305) and qs.quotes["TEN000001"].clean is None
    assert qs.aliases == {"CT10": "TEN000002", "O10": "TEN000001", "OO10": "TEN000003", "CT2": "TWO000001"}
    assert sorted(qs.securities) == sorted(FOUR)
    assert p.time.mode == "exact" and p.kind == "minute"
    with pytest.raises(MarketDataUnavailable, match="no_snapshot_at_exact_timestamp"):
        p.get_pricer(ny("2024-06-04 10:05:30"))
    assert sp.snapshot.provenance["n_quotes"] == 4 and sp.snapshot.provenance["kind"] == "intraday"


def test_a_minute_yield_of_a_cusip_without_reference_data_is_not_a_quote(tmp_path):
    rows = four("2024-06-04", "10:00", 5) + series("2024-06-04", "10:00", {"NOREF0001": ramp(5, 4.7)})
    p = minute_provider(tmp_path, rows)
    sp = p.get_pricer(ny("2024-06-04 10:02"))
    assert sorted(sp.snapshot.quotes.quotes) == sorted(FOUR) and "NOREF0001" not in sp.snapshot.quotes.provenance["flags"] and p.describe()["cusips"] == 5


def test_the_local_date_of_a_minute_not_its_utc_date_is_the_reference_date(tmp_path):
    late = series("2024-06-04", "23:58", {c: [4.0 + i * 0.1, 4.1 + i * 0.1, 4.2 + i * 0.1] for i, c in enumerate(FOUR)})  # 23:58-00:00 local
    p = minute_provider(tmp_path, late, session=("00:00", "23:59"), drop_flat_days=False)
    assert p.get_pricer(ny("2024-06-04 23:59")).reference_date == d("2024-06-04") and p.get_pricer(ny("2024-06-05 00:00")).reference_date == d("2024-06-05")


def test_the_session_is_inclusive_at_both_ends(tmp_path):
    p = minute_provider(tmp_path, four("2024-06-04", "06:58", 85))
    ts = p.available_timestamps(ny("2024-06-04"), ny("2024-06-04 23:59"))
    assert ts[0] == ny("2024-06-04 07:00") and ts[-1] == ny("2024-06-04 08:22") and len(p._sel) == 83
    full = minute_provider(tmp_path / "f", four("2024-06-04", "06:58", 730))
    ts = full.available_timestamps(ny("2024-06-04"), ny("2024-06-04 23:59"))
    assert (ts[0], ts[-1]) == (ny("2024-06-04 07:00"), ny("2024-06-04 17:00")) and len(ts) == 601
    custom = minute_provider(tmp_path / "c", four("2024-06-04", "06:58", 730), session=("09:00", "16:00"))
    ts = custom.available_timestamps(ny("2024-06-04"), ny("2024-06-04 23:59"))
    assert (ts[0], ts[-1]) == (ny("2024-06-04 09:00"), ny("2024-06-04 16:00")) and len(ts) == 421


def test_flat_days_are_dropped_and_a_single_mover_keeps_the_day(tmp_path):
    flat = {c: [4.0 + i * 0.1] * 15 for i, c in enumerate(FOUR)}
    one = dict(flat)
    one["TWO000001"] = ramp(15, 4.3)
    rows = series("2024-06-04", "10:00", flat) + series("2024-06-05", "10:00", one) + series("2024-06-06", "10:00", {**flat, "TEN000001": [4.0] * 7 + [None] * 8})
    p = minute_provider(tmp_path, rows)
    assert p.flat_days == (d("2024-06-04"), d("2024-06-06")) and len(p._sel) == 15
    keep = minute_provider(tmp_path / "k", rows, drop_flat_days=False)
    assert keep.flat_days == () and len(keep._sel) == 45
    with pytest.raises(MarketDataUnavailable):
        p.get_pricer(ny("2024-06-04 10:00"))
    assert keep.get_pricer(ny("2024-06-04 10:00")).reference_date == d("2024-06-04")
    only_absent = series("2024-06-07", "10:00", {c: [4.0] * 3 for c in FOUR[:2]})
    assert minute_provider(tmp_path / "o", only_absent).flat_days == (d("2024-06-07"),), "a CUSIP that is absent all day does not count as moving"


def test_the_yield_band_is_inclusive_and_out_of_band_yields_are_not_quotes(tmp_path):
    vals = {"TEN000001": [25.0, 25.01, -2.0, -2.01], "TEN000002": [4.0, 4.1, 4.2, 4.3], "TEN000003": [4.5, 4.6, 4.7, 4.8], "TWO000001": [4.9, 5.0, 5.1, 5.2]}
    p = minute_provider(tmp_path, series("2024-06-04", "10:00", vals))
    got = [sorted(p.get_pricer(ny(f"2024-06-04 10:0{k}")).snapshot.quotes.quotes) for k in range(4)]
    assert "TEN000001" in got[0] and "TEN000001" not in got[1] and "TEN000001" in got[2] and "TEN000001" not in got[3]
    narrow = minute_provider(tmp_path / "n", series("2024-06-04", "10:00", vals), ytm_band=(4.0, 4.5))
    assert sorted(narrow.get_pricer(ny("2024-06-04 10:00")).snapshot.quotes.quotes) == ["TEN000002", "TEN000003"]


def test_forward_fill_flags_count_repeats_over_the_unfiltered_grid(tmp_path):
    """A CUSIP unchanged over 06:59, 07:00, 07:01 has run 2 at 07:01: the run started BEFORE the session opened."""
    v = {"TEN000001": [4.0, 4.0, 4.0, 4.1, 4.1, 4.2], "TEN000002": ramp(6, 4.0), "TEN000003": ramp(6, 4.5), "TWO000001": ramp(6, 5.0)}
    p = minute_provider(tmp_path, series("2024-06-04", "06:59", v))
    flags = {k: p.get_pricer(ny(f"2024-06-04 {t}")).snapshot.quotes.provenance["flags"]["TEN000001"] for k, t in enumerate(("07:00", "07:01", "07:02", "07:03", "07:04"))}
    assert flags == {0: {"repeat": True, "run": 1}, 1: {"repeat": True, "run": 2}, 2: {"repeat": False, "run": 0}, 3: {"repeat": True, "run": 1}, 4: {"repeat": False, "run": 0}}
    df = p.quote_flags(ny("2024-06-04 07:01"))
    assert df.index.name == "cusip" and bool(df.loc["TEN000001", "repeat"]) and int(df.loc["TEN000001", "run"]) == 2 and not df.loc["TEN000002", "repeat"]
    assert list(df.columns) == ["ytm_pct", "repeat", "run", "filtered"] and not df["filtered"].any()
    assert "filtered" not in p.get_pricer(ny("2024-06-04 07:01")).snapshot.quotes.provenance["flags"]["TEN000001"], "no filter, no `filtered` flag"


def test_the_bad_tick_filter_replaces_a_short_spike_by_the_last_accepted_yield(tmp_path):
    rows = four("2024-06-04", "10:00", 20, spike=("TEN000001", 10, [0.5, 0.5]))
    raw = minute_provider(tmp_path / "r", rows)
    assert raw.filtered_counts == {} and raw.get_pricer(ny("2024-06-04 10:10")).snapshot.quotes.quotes["TEN000001"].ytm == pytest.approx(4.51)
    flt = minute_provider(tmp_path / "f", rows, outlier_bp=5.0)
    assert flt.filtered_counts == {"TEN000001": 2}
    for k in range(8, 13):
        # minutes 10:10 and 10:11 carry the last accepted level (the 10:09 print, 4.009); before and after the spike the prints are served as they are
        want = 4.009 if k in (10, 11) else 4.0 + 0.001 * k
        sp = flt.get_pricer(ny(f"2024-06-04 10:{k:02d}"))
        assert sp.snapshot.quotes.quotes["TEN000001"].ytm == pytest.approx(want, abs=1e-9), k
        assert sp.snapshot.quotes.provenance["flags"]["TEN000001"]["filtered"] is (k in (10, 11)), k
    assert flt.get_pricer(ny("2024-06-04 10:10")).snapshot.provenance["n_filtered"] == 1
    assert flt.get_pricer(ny("2024-06-04 10:10")).snapshot.quotes.provenance["flags"]["TEN000001"]["repeat"] is True, "the substituted quote repeats the previous one"
    assert bool(flt.quote_flags(ny("2024-06-04 10:10")).loc["TEN000001", "filtered"]) and not flt.quote_flags(ny("2024-06-04 10:12")).loc["TEN000001", "filtered"]


def test_the_threshold_is_in_basis_points_of_the_deviation_from_the_median_move(tmp_path):
    rows = four("2024-06-04", "10:00", 20, spike=("TEN000001", 10, [0.06]))
    assert minute_provider(tmp_path / "a", rows, outlier_bp=5.0).filtered_counts == {"TEN000001": 1}, "6bp above the median move is a bad tick"
    assert minute_provider(tmp_path / "b", rows, outlier_bp=7.0).filtered_counts == {}, "but not at a 7bp threshold"
    edge = four("2024-06-04", "10:00", 20, spike=("TEN000001", 10, [0.04]))
    assert minute_provider(tmp_path / "c", edge, outlier_bp=5.0).filtered_counts == {}
    common = {c: ramp(20, 4.0 + i * 0.1, 0.001) for i, c in enumerate(FOUR)}
    for c in FOUR:
        for k in range(10, 20):
            common[c][k] = round(common[c][k] + 0.3, 6)  # EVERY CUSIP jumps by 30bp together: a market move, not a bad tick
    assert minute_provider(tmp_path / "d", series("2024-06-04", "10:00", common), outlier_bp=5.0).filtered_counts == {}


def test_a_deviation_that_persists_is_accepted_as_a_level_change(tmp_path):
    rows = four("2024-06-04", "10:00", 20, spike=("TEN000001", 5, [0.5] * 8))
    p = minute_provider(tmp_path, rows, outlier_bp=5.0)
    flags = [p.get_pricer(ny(f"2024-06-04 10:{k:02d}")).snapshot.quotes.provenance["flags"]["TEN000001"]["filtered"] for k in range(5, 13)]
    assert flags[:4] == [True] * 4 and flags[4:8] == [False, False, False, False], "the 5th consecutive minute is accepted (outlier_max_run = 5)"
    short = minute_provider(tmp_path / "s", rows, outlier_bp=5.0, outlier_max_run=2)
    assert short.filtered_counts["TEN000001"] < p.filtered_counts["TEN000001"], "a shorter patience accepts the new level sooner"
    for bad in (0, -1):
        with pytest.raises(ConfigError, match="outlier_max_run"):
            minute_provider(tmp_path / "x", rows, outlier_bp=5.0, outlier_max_run=bad)
    for bad in (0.0, -1.0):
        with pytest.raises(ConfigError, match="outlier_bp"):
            minute_provider(tmp_path / "y", rows, outlier_bp=bad)


def test_fewer_than_three_cusips_form_no_cross_section_and_nothing_is_filtered(tmp_path):
    rows = four("2024-06-04", "10:00", 20, spike=("TEN000001", 10, [0.5, 0.5]))
    two = [r for r in rows if r[1] in ("TEN000001", "TEN000002")]
    assert minute_provider(tmp_path / "two", two, outlier_bp=5.0).filtered_counts == {}
    three = [r for r in rows if r[1] in ("TEN000001", "TEN000002", "TEN000003")]
    assert minute_provider(tmp_path / "three", three, outlier_bp=5.0).filtered_counts == {"TEN000001": 2}


def test_a_missing_print_is_not_a_move_and_duplicates_are_refused(tmp_path):
    vals = {c: ramp(10, 4.0 + i * 0.1) for i, c in enumerate(FOUR)}
    vals["TEN000001"][4] = None
    p = minute_provider(tmp_path, series("2024-06-04", "10:00", vals), outlier_bp=5.0)
    assert p.filtered_counts == {}
    assert "TEN000001" not in p.get_pricer(ny("2024-06-04 10:04")).snapshot.quotes.quotes and "TEN000001" in p.get_pricer(ny("2024-06-04 10:05")).snapshot.quotes.quotes
    rows = four("2024-06-04", "10:00", 5)
    with pytest.raises(DataQualityError, match="duplicated"):
        minute_provider(tmp_path / "d", rows + [rows[0]])


def test_the_minute_provider_pickles_and_describes_itself(tmp_path):
    p = minute_provider(tmp_path, four("2024-06-04", "10:00", 20, spike=("TEN000001", 10, [0.5, 0.5])), outlier_bp=5.0, max_cached_pricers=2)
    a = p.get_pricer(ny("2024-06-04 10:10"))
    for k in (11, 12, 13):
        p.get_pricer(ny(f"2024-06-04 10:{k}"))
    assert len(p._pricers) == 2
    m = pickle.loads(pickle.dumps(p))
    assert len(m._pricers) == 0 and m.get_pricer(ny("2024-06-04 10:10")).digest == a.digest and m.filtered_counts == p.filtered_counts
    assert pickle.loads(pickle.dumps(a)).snapshot.quotes.provenance == a.snapshot.quotes.provenance
    ds = p.describe()
    assert ds["cusips"] == 4 and ds["minutes_total"] == 20 and ds["outlier_bp"] == 5.0 and ds["filtered_counts"] == {"TEN000001": 2} and ds["session"] == ["07:00", "17:00"]
    cfg = p.to_config()
    assert cfg["type"] == "support.ust_minute:UstMinute" and cfg["outlier_bp"] == 5.0 and cfg["ytm_band"] == [-2.0, 25.0] and cfg["time"]["mode"] == "exact"
    assert "outlier_bp" not in minute_provider(tmp_path / "n", four("2024-06-04", "10:00", 5)).to_config()


# ============================================================================ real fixtures
@pytest.fixture(scope="module")
def fi():
    return UstEod()


@pytest.fixture(scope="module")
def wb():
    return UstMinute()


def eod17(s: str) -> pd.Timestamp:
    return ny(f"{s} 17:00")


@needs_fixtures
def test_zero_price_days_are_removed_never_served(fi):
    assert sorted(str(x) for x in fi.dropped_days["date"]) == sorted(FEDINVEST_ZERO_DAYS)
    assert set(fi.dropped_days["reason"]) == {"zero_eod_day"} and fi.n_rows_out_of_band == FEDINVEST_ROWS_OUT_OF_BAND
    days = {t.date() for t in fi.available_timestamps(ny("2010-01-01"), ny("2026-12-31"))}
    assert len(days) == FEDINVEST_SERVED and not days & {d(x) for x in FEDINVEST_ZERO_DAYS}
    for z in FEDINVEST_ZERO_DAYS:
        with pytest.raises(StaleSnapshot):
            fi.get_pricer(eod17(z))
    by_date = UstEod(time={"mode": "date"}, window=("2026-08-01", "2026-08-21"))
    with pytest.raises(MarketDataUnavailable, match="no_snapshot_for_date"):
        by_date.get_pricer(eod17("2026-08-07"))
    loose = UstEod(time={"max_staleness": "3D"}, window=("2026-08-01", "2026-08-21"))
    assert loose.get_pricer(eod17("2026-08-07")).reference_date == d("2026-08-06"), "control: a loose bound serves the previous day, never zeros"


@needs_fixtures
def test_the_five_partial_2023_panels_are_dropped_with_the_filter_on():
    f = UstEod(window=("2023-01-01", "2023-12-31"), partial_day_min_missing=10)
    part = f.dropped_days[f.dropped_days["reason"].str.startswith("partial_day")]
    assert [str(x) for x in part["date"]] == list(FEDINVEST_PARTIAL_DAYS_2023)
    assert list(part["reason"]) == ["partial_day(16 missing)", "partial_day(26 missing)", "partial_day(30 missing)", "partial_day(48 missing)", "partial_day(29 missing)"]
    days = {t.date() for t in f.available_timestamps(ny("2023-01-01"), ny("2023-12-31 23:00"))}
    assert d("2023-08-01") not in days and d("2023-07-31") in days
    assert len(UstEod(partial_day_min_missing=10)._days) == FEDINVEST_SERVED_PARTIAL
    raw = UstEod(window=("2023-07-25", "2023-08-08"))
    qs = raw.get_pricer(eod17("2023-08-01")).snapshot.quotes
    assert qs.aliases["CT10"] == "91282CHC8" and "91282CHC8" in qs.securities and "91282CHC8" not in qs.quotes, "the on-the-run 10-year has no quote that day: resolvable, not quoted"


@needs_fixtures
def test_price_band_keeps_the_real_43_625_print_and_drops_it_under_50(fi):
    q = fi.get_pricer(eod17("2023-10-19")).snapshot.quotes
    assert q.quotes["912810SN9"].clean == 43.625
    tight = UstEod(price_band=(50.0, 250.0), window=("2023-10-18", "2023-10-20"))
    assert "912810SN9" not in tight.get_pricer(eod17("2023-10-19")).snapshot.quotes.quotes
    assert all(20.0 <= v.clean <= 250.0 for v in q.quotes.values())


@needs_fixtures
def test_eod_stamp_and_visibility(fi):
    p = fi.get_pricer(eod17("2026-08-19"))
    assert p.ts == eod17("2026-08-19") and p.reference_date == d("2026-08-19")
    with pytest.raises(StaleSnapshot):
        fi.get_pricer(ny("2026-08-19 16:59"))
    assert list(fi.available_timestamps(ny("2026-08-17"), ny("2026-08-21 23:00"))) == [eod17("2026-08-17"), eod17("2026-08-19")]


@needs_fixtures
def test_on_the_run_ranking_known_answers(fi):
    ranked = fi.universe.ranked(d("2025-01-02"))
    row = ranked[ranked.cusip == "91282CLL3"].iloc[0]
    assert row["rank"] == 3 and fi.universe.aliases(d("2025-01-02"))[f"OOO{int(row['oi'].split('-')[0])}"] == "91282CLL3"
    a = fi.get_pricer(eod17("2026-03-09")).snapshot.quotes.aliases
    assert [a[k] for k in ("CT2", "CT3", "CT5", "CT7", "CT10", "CT30")] == ["91282CQB0", "91282CQA2", "91282CQD6", "91282CQC8", "91282CPZ8", "912810UR7"]
    for (day, alias), want in ON_THE_RUN.items():
        assert fi.universe.aliases(d(day))[alias] == want, f"{day} {alias}: ranking is reference data, it answers on zero-price days too"
        if fi.available(eod17(day)):
            assert fi.get_pricer(eod17(day)).snapshot.quotes.aliases[alias] == want
    a19 = fi.get_pricer(eod17("2026-08-19")).snapshot.quotes.aliases
    assert [a19[k] for k in ("CT10", "O10", "OO10", "OOO10")] == ["91282CRF0", "91282CQQ7", "91282CPZ8", "91282CPJ4"]
    assert [a19[k] for k in ("CT2", "CT5", "CT30")] == ["91282CRB9", "91282CRA1", "912810UW6"] and len(a19) == 28
    n = fi.universe.ranked(d("2026-08-19")).groupby("oi").size().to_dict()
    assert n == {"10-Year": 40, "2-Year": 23, "20-Year": 25, "3-Year": 36, "30-Year": 83, "5-Year": 58, "7-Year": 84} and sum(n.values()) == 349


@needs_fixtures
def test_the_quotes_of_a_real_day_reproduce_the_reference_prices(fi):
    for day, cusip, clean in (("2026-08-05", "91282CQQ7", 98.0625), ("2026-08-19", "91282CRF0", 99.84375)):
        qs = fi.get_pricer(eod17(day)).snapshot.quotes
        assert qs.aliases["CT10"] == cusip and qs.quotes[cusip].clean == clean and qs.securities[cusip].maturity_date.year in (2035, 2036)
    assert qs.securities["91282CRF0"].label == "T 4 5/8 Aug 36" and qs.securities["91282CRF0"].coupon == 4.625 and qs.securities["91282CRF0"].issue_date == d("2026-08-17")
    assert len(fi.get_pricer(eod17("2026-08-19")).snapshot.quotes.quotes) == 349


@needs_fixtures
def test_price_file_cusips_without_reference_data_are_never_quotes(fi):
    raw = pd.read_parquet(FIXTURES / "ust" / "fedinvest_2010_2026.parquet", columns=["date", "cusip", "eod_price"])
    hit = raw[raw.cusip.isin(FEDINVEST_UNREFERENCED) & (raw.eod_price > 20)]
    assert len(hit) > 0 and set(hit.cusip) <= set(FEDINVEST_UNREFERENCED)
    day = hit.iloc[0]["date"]
    assert fi.available(eod17(str(day)))
    assert hit.iloc[0]["cusip"] in set(fi.panel(day)["cusip"]), "the price file has the row"
    assert not set(fi.get_pricer(eod17(str(day))).snapshot.quotes.quotes) & set(FEDINVEST_UNREFERENCED)


@needs_fixtures
def test_bid_offer_is_a_spread_signal_and_missing_sides_are_none(fi):
    day = d("2026-08-19")
    panel = fi.panel(day)
    q = fi.get_pricer(eod17("2026-08-19")).snapshot.quotes.quotes
    both = panel[(panel.bid_price > 0) & (panel.offer_price > 0) & panel.valid & panel.cusip.isin(q)].iloc[0]
    assert (q[both.cusip].bid, q[both.cusip].offer) == (both.bid_price, both.offer_price) and both.bid_price <= both.offer_price
    one_sided = panel[(panel.offer_price == 0) & panel.valid & panel.cusip.isin(q)]
    assert len(one_sided) > 0 and q[one_sided.iloc[0].cusip].bid is None and q[one_sided.iloc[0].cusip].offer is None


@needs_fixtures
def test_a_funding_curve_and_fixings_come_from_the_curve_provider():
    win = ("2026-08-01", "2026-08-10")
    curve = CurveStore(EOD, window=win)
    funded = UstEod(curve_mdp=curve, window=win)
    q = funded.get_pricer(eod17("2026-08-05"))
    assert list(q.snapshot.curves) == ["sofr"] and q.snapshot.curves["sofr"] == curve.get_pricer(eod17("2026-08-05")).snapshot.curves["sofr"]
    assert q.snapshot.fixings["USD-SOFR-1D"].dates[-1] == d("2026-08-04") and q.snapshot.quotes.quotes["91282CQQ7"].clean == 98.0625
    plain = UstEod(window=win, fixings="auto").get_pricer(eod17("2026-08-05")).snapshot
    assert plain.fixings["USD-SOFR-1D"] == q.snapshot.fixings["USD-SOFR-1D"] and plain.curves == {}


@needs_fixtures
def test_the_fedinvest_snapshot_pickles_and_has_a_stable_digest(fi):
    p = fi.get_pricer(eod17("2026-08-19"))
    p2 = pickle.loads(pickle.dumps(p))
    assert p2.digest == p.digest and p2.snapshot.quotes.aliases == p.snapshot.quotes.aliases
    m = pickle.loads(pickle.dumps(fi))
    assert len(m._pricers) == 0 and m.get_pricer(eod17("2026-08-19")).digest == p.digest
    assert UstEod(window=("2026-08-01", "2026-08-21")).get_pricer(eod17("2026-08-19")).digest == p.digest
    assert fi.get_pricer(eod17("2026-08-17")).digest != p.digest


# ------------------------------------------------------------------ minutes
@needs_fixtures
def test_webull_flat_days_dropped_and_sessions_follow_dst(wb):
    assert tuple(str(x) for x in wb.flat_days) == WEBULL_FLAT
    assert wb.describe()["served"] == WEBULL_SERVED_MINUTES
    mar9 = wb.available_timestamps(ny("2026-03-09"), ny("2026-03-09 23:59"))
    mar6 = wb.available_timestamps(ny("2026-03-06"), ny("2026-03-06 23:59"))
    assert len(mar9) == len(mar6) == 601
    assert (mar9[0], mar9[-1]) == (ny("2026-03-09 07:00"), ny("2026-03-09 17:00")) and mar9[0].tz_convert("UTC").hour == 11
    assert mar6[0].tz_convert("UTC").hour == 12
    assert len(wb.available_timestamps(ny("2026-03-07"), ny("2026-03-08 23:59"))) == 0
    keep = UstMinute(drop_flat_days=False)
    assert len(keep.available_timestamps(ny("2026-03-07"), ny("2026-03-07 23:59"))) == 601, "control"


@needs_fixtures
def test_webull_exact_minute_mapping(wb):
    assert wb.get_pricer(ny("2026-03-09 10:00")).ts == ny("2026-03-09 10:00")
    for t in ("2026-03-09 10:00:30", "2026-03-07 10:00", "2026-03-09 17:01"):
        with pytest.raises(MarketDataUnavailable):
            wb.get_pricer(ny(t))
    asof = UstMinute(time={"mode": "asof", "max_staleness": "2min"})
    assert asof.get_pricer(ny("2026-03-09 17:01")).ts == ny("2026-03-09 17:00")
    with pytest.raises(StaleSnapshot):
        asof.get_pricer(ny("2026-03-09 17:03"))


@needs_fixtures
def test_webull_quotes_and_forward_fill_flags_match_the_raw_file(wb):
    raw = pd.read_parquet(FIXTURES / "ust" / "webull_minute.parquet")
    s = raw[raw.cusip == "91282CPZ8"].set_index("ts_utc")["ytm_pct"].sort_index()
    rep = s.eq(s.shift(1))
    run = rep.astype(int).groupby((~rep).cumsum()).cumsum()
    for hhmm in ("07:00", "09:31", "10:00", "13:17", "16:59"):
        t = ny(f"2026-03-09 {hhmm}")
        key = t.tz_convert("UTC")
        qs = wb.get_pricer(t).snapshot.quotes
        assert qs.quotes["91282CPZ8"].ytm == s.loc[key]
        assert qs.provenance["flags"]["91282CPZ8"] == {"repeat": bool(rep.loc[key]), "run": int(run.loc[key])}, hhmm
        qf = wb.quote_flags(t).loc["91282CPZ8"]
        assert bool(qf["repeat"]) == qs.provenance["flags"]["91282CPZ8"]["repeat"] and int(qf["run"]) == qs.provenance["flags"]["91282CPZ8"]["run"]
    assert wb.quote_flags(ny("2026-03-09 07:00")).loc["91282CPZ8", "run"] > 600, "Monday 07:00 repeats the weekend's forward fill"


@needs_fixtures
def test_webull_snapshot_is_a_yield_panel_with_the_on_the_run_aliases(wb):
    sp = wb.get_pricer(ny("2026-03-09 10:00"))
    qs = sp.snapshot.quotes
    assert sp.reference_date == d("2026-03-09") and qs.aliases["CT10"] == "91282CPZ8" and qs.price_convention == "market" and len(qs.quotes) == 21
    assert all(q.clean is None and q.ytm is not None and 2.0 < q.ytm < 6.0 for q in qs.quotes.values()) and sp.describe()["kind"] == "intraday"
    assert pickle.loads(pickle.dumps(sp)).digest == sp.digest
    assert sp.snapshot.fixings == {} and set(qs.provenance["flags"]) == set(qs.quotes)


@needs_fixtures
def test_webull_bad_tick_filter_removes_the_2026_03_12_ten_year_spike():
    raw, flt = UstMinute(), UstMinute(outlier_bp=5.0)
    assert raw.get_pricer(ny("2026-03-12 08:44")).snapshot.quotes.quotes["91282CPZ8"].ytm == 3.331, "control: the raw file carries the spike"
    for hhmm, want, filtered in (("08:42", 4.225, False), ("08:43", 4.225, True), ("08:44", 4.225, True), ("08:45", 4.225, True), ("08:46", 4.218, False)):
        qs = flt.get_pricer(ny(f"2026-03-12 {hhmm}")).snapshot.quotes
        assert qs.quotes["91282CPZ8"].ytm == want and qs.provenance["flags"]["91282CPZ8"]["filtered"] is filtered, hhmm
    assert flt.filtered_counts == WEBULL_FILTERED_COUNTS and flt.filtered_counts["91282CPZ8"] == 3 and sum(flt.filtered_counts.values()) < 60
    assert raw.filtered_counts == {} and "filtered" not in raw.get_pricer(ny("2026-03-12 08:44")).snapshot.quotes.provenance["flags"]["91282CPZ8"]


@needs_fixtures
def test_webull_fixings_before_0800_are_proxied_in_the_snapshot():
    w = UstMinute(fixings="auto")
    early, late = w.get_pricer(ny("2026-03-09 07:30")).snapshot.fixings["USD-SOFR-1D"], w.get_pricer(ny("2026-03-09 08:00")).snapshot.fixings["USD-SOFR-1D"]
    assert early.proxied == (d("2026-03-06"),) and late.proxied == () and early.dates == late.dates and early.dates[-1] == d("2026-03-06")
    assert early.values[-1] == early.values[-2] and early.values[:-1] == late.values[:-1]
    assert (w.get_pricer(ny("2026-03-09 07:30")).snapshot.fixings["USD-SOFR-1D"].dates[-1]) < d("2026-03-09")
