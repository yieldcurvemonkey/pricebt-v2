"""Snapshots (spec D1, D5): immutable plain-data market state with a stable digest; providers emit them, adapter wrappers consume them."""
from __future__ import annotations

import copy
import datetime as dt
import os
import pickle
import subprocess
import sys

import pandas as pd
import pytest

from pricebt.errors import ConfigError
from pricebt.snapshot import CalendarData, CurveSnapshot, FixingsSeries, MarketSnapshot, Quote, QuoteSet, Security, SnapshotPricer

pytestmark = pytest.mark.core

REF = dt.date(2024, 1, 2)
TS = pd.Timestamp("2024-01-02 17:00", tz="America/New_York")
NODES = (REF, dt.date(2024, 2, 1), dt.date(2025, 1, 2), dt.date(2034, 1, 2))
DFS = (1.0, 0.9955, 0.9560, 0.7000)


def curve(**over):
    kw = dict(name="usd-sofr", reference_date=REF, node_dates=NODES, values=DFS)
    kw.update(over)
    return CurveSnapshot(**kw)


def fixings(**over):
    kw = dict(name="usd-sofr", dates=(dt.date(2023, 12, 28), dt.date(2023, 12, 29)), values=(5.31, 5.33), unit="percent")
    kw.update(over)
    return FixingsSeries(**kw)


def quotes(**over):
    kw = dict(reference_date=REF, quotes={"AAA": Quote(clean=99.5), "BBB": Quote(ytm=4.2, bid=99.0, offer=99.2)},
              securities={"AAA": Security("AAA", 4.0, dt.date(2020, 1, 15), dt.date(2030, 1, 15), "T 4 Jan 30"), "BBB": Security("BBB", 3.5, dt.date(2021, 2, 15), dt.date(2031, 2, 15))},
              aliases={"CT10": "AAA"}, price_convention="market")
    kw.update(over)
    return QuoteSet(**kw)


def snap(**over):
    kw = dict(ts=TS, reference_date=REF, curves={"usd-sofr": curve()}, fixings={"usd-sofr": fixings()}, quotes=quotes(),
              calendars={"c": CalendarData("c", (dt.date(2024, 1, 1),), "Mon Tue Wed Thu Fri")})
    kw.update(over)
    return MarketSnapshot(**kw)


# ------------------------------------------------------------------------------ validation
@pytest.mark.parametrize("over", [
    {"node_dates": NODES[:1], "values": DFS[:1]},  # need >= 2 nodes
    {"values": DFS[:3]},  # length mismatch
    {"node_dates": (REF, REF, dt.date(2025, 1, 2), dt.date(2034, 1, 2))},  # not strictly ascending
    {"node_dates": (dt.date(2024, 1, 3), dt.date(2024, 2, 1), dt.date(2025, 1, 2), dt.date(2034, 1, 2))},  # first node is not the reference date
    {"values": (1.0, 0.99, -0.5, 0.7)},  # non-positive
    {"values": (1.0, 0.99, float("nan"), 0.7)},
    {"values": (0.99, 0.98, 0.95, 0.7)},  # anchor DF must be 1
    {"value_kind": "zero_rate"},  # only discount factors exist
    {"interpolation": "flat_forward"},  # never a library's tag name: named by its maths
    {"name": ""},
])
def test_curve_snapshot_rejects_malformed_input(over):
    with pytest.raises(ConfigError) as ei:
        curve(**over)
    assert ei.value.code == "SNAPSHOT"


def test_a_curve_with_a_rising_discount_factor_is_allowed_because_real_data_has_them():
    assert curve(values=(1.0, 0.99, 0.995, 0.7)).values[2] == 0.995  # DF > previous is not rejected (124 real rows have one)


def test_curve_snapshot_normalises_sequences_to_tuples_and_is_frozen():
    c = curve(node_dates=list(NODES), values=list(DFS))
    assert isinstance(c.node_dates, tuple) and isinstance(c.values, tuple)
    with pytest.raises(Exception):
        c.name = "other"


def test_fixings_validation_unit_order_uniqueness_and_finiteness():
    for bad in ({"unit": "bp"}, {"values": (5.31,)}, {"dates": (dt.date(2023, 12, 29), dt.date(2023, 12, 28))}, {"dates": (dt.date(2023, 12, 28), dt.date(2023, 12, 28))},
                {"values": (5.31, float("inf"))}, {"proxied": (dt.date(2020, 1, 1),)}):
        with pytest.raises(ConfigError) as ei:
            fixings(**bad)
        assert ei.value.code == "SNAPSHOT"
    f = fixings(proxied=(dt.date(2023, 12, 29),))
    assert f.proxied == (dt.date(2023, 12, 29),) and f.unit == "percent"


def test_quote_needs_a_price_or_a_yield_and_the_set_needs_known_securities_and_a_valid_convention():
    with pytest.raises(ConfigError):
        Quote()
    with pytest.raises(ConfigError):
        quotes(aliases={"CT10": "ZZZ"})  # alias to an unknown security
    with pytest.raises(ConfigError):
        quotes(price_convention="street")
    with pytest.raises(ConfigError):
        quotes(quotes={"AAA": Quote(clean=99.5), "ZZZ": Quote(clean=1.0)})  # a quote for a security with no reference data
    assert quotes().securities["AAA"].coupon == 4.0


def test_market_snapshot_requires_a_tz_aware_stamp_and_consistent_dates():
    with pytest.raises(ConfigError):
        snap(ts=pd.Timestamp("2024-01-02 17:00"))
    with pytest.raises(ConfigError):
        snap(curves={"usd-sofr": curve(reference_date=dt.date(2024, 1, 3), node_dates=(dt.date(2024, 1, 3),) + NODES[1:])})  # curve anchored on another date
    with pytest.raises(ConfigError):
        snap(fixings={"usd-sofr": fixings(dates=(dt.date(2024, 1, 2),), values=(5.3,))}, curves={"usd-sofr": curve()})  # a fixing not strictly before the reference date
    with pytest.raises(ConfigError):
        snap(curves={"other": curve()})  # key must equal the curve's own name
    with pytest.raises(ConfigError):
        snap(quotes=quotes(reference_date=dt.date(2024, 1, 3)))  # a bond panel as of another date


# ------------------------------------------------------------------------------ digest (D5)
def test_equal_content_gives_equal_digests_and_a_sha256_hex():
    a, b = snap(), snap()
    assert a is not b and a.digest() == b.digest() and len(a.digest()) == 64 and int(a.digest(), 16) >= 0


@pytest.mark.parametrize("change", [
    lambda: snap(curves={"usd-sofr": curve(values=(1.0, 0.9955, 0.9560, 0.7001))}),
    lambda: snap(curves={"usd-sofr": curve(node_dates=(REF, dt.date(2024, 2, 2), dt.date(2025, 1, 2), dt.date(2034, 1, 2)))}),
    lambda: snap(fixings={"usd-sofr": fixings(values=(5.31, 5.34))}),
    lambda: snap(fixings={"usd-sofr": fixings(unit="decimal", values=(0.0531, 0.0533))}),
    lambda: snap(fixings={"usd-sofr": fixings(proxied=(dt.date(2023, 12, 29),))}),
    lambda: snap(quotes=quotes(quotes={"AAA": Quote(clean=99.6), "BBB": Quote(ytm=4.2, bid=99.0, offer=99.2)})),
    lambda: snap(quotes=quotes(price_convention="unspecified")),
    lambda: snap(quotes=quotes(aliases={"CT10": "BBB"})),
    lambda: snap(quotes=None),
    lambda: snap(ts=pd.Timestamp("2024-01-02 17:00:01", tz="America/New_York")),
    lambda: snap(calendars={"c": CalendarData("c", (dt.date(2024, 1, 1), dt.date(2024, 1, 15)), "Mon Tue Wed Thu Fri")}),
])
def test_any_change_of_content_changes_the_digest(change):
    assert change().digest() != snap().digest()


def test_the_fixings_unit_alone_changes_the_digest():
    assert snap(fixings={"usd-sofr": fixings(unit="decimal")}).digest() != snap().digest()  # same numbers, different unit: a different input


def test_provenance_is_carried_but_is_not_part_of_the_digest():
    a = snap(curves={"usd-sofr": curve(provenance={"source": "vendor A"})})
    b = snap(curves={"usd-sofr": curve(provenance={"source": "vendor B"})})
    assert a.curves["usd-sofr"].provenance["source"] == "vendor A" and a.digest() == b.digest()


def test_mapping_order_does_not_change_the_digest():
    q1 = quotes(quotes={"AAA": Quote(clean=99.5), "BBB": Quote(ytm=4.2)})
    q2 = quotes(quotes={"BBB": Quote(ytm=4.2), "AAA": Quote(clean=99.5)})
    assert snap(quotes=q1).digest() == snap(quotes=q2).digest()


def test_the_digest_does_not_depend_on_python_hash_randomisation():
    code = ("import sys, datetime as dt, pandas as pd; sys.path[:0] = ['src', 'tests']; from test_snapshot import snap; print(snap().digest())")
    outs = set()
    for seed in ("1", "2", "12345"):
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONDONTWRITEBYTECODE": "1"})
        assert p.returncode == 0, p.stderr[-500:]
        outs.add(p.stdout.strip())
    assert len(outs) == 1 and outs == {snap().digest()}


def test_an_absent_optional_quote_field_is_not_the_same_as_zero():
    a = snap(quotes=quotes(quotes={"AAA": Quote(clean=99.5), "BBB": Quote(ytm=4.2)}))
    b = snap(quotes=quotes(quotes={"AAA": Quote(clean=99.5, bid=0.0), "BBB": Quote(ytm=4.2)}))
    c = snap(quotes=quotes(quotes={"AAA": Quote(clean=99.5), "BBB": Quote(ytm=4.2, offer=0.0)}))
    assert len({a.digest(), b.digest(), c.digest()}) == 3


def test_dates_and_stamps_enter_the_digest_at_full_resolution():
    assert snap(curves={"usd-sofr": curve(node_dates=(REF, dt.date(2024, 2, 1), dt.date(2025, 1, 2), dt.date(2034, 1, 3)))}).digest() != snap().digest()  # same year, different day
    assert snap(ts=pd.Timestamp("2024-01-02 17:00:00.000000001", tz="America/New_York")).digest() != snap().digest()  # one nanosecond


def test_digest_is_bit_exact_on_floats_a_one_ulp_change_is_visible():
    import numpy as np

    v = np.nextafter(0.9955, 1.0)
    assert snap(curves={"usd-sofr": curve(values=(1.0, float(v), 0.9560, 0.7000))}).digest() != snap().digest()


# ------------------------------------------------------------------------------ pricer and copies
def test_snapshot_pricer_exposes_ts_reference_date_digest_and_describe():
    s = snap()
    p = SnapshotPricer(s)
    assert p.ts == TS and p.reference_date == REF and p.snapshot is s and p.digest == s.digest()
    d = p.describe()
    assert d["digest"] == s.digest() and d["reference_date"] == str(REF) and d["curves"] == ["usd-sofr"] and d["type"] == "SnapshotPricer"


def test_snapshot_pricer_has_no_lookups_it_is_data_only():
    with pytest.raises(LookupError):
        SnapshotPricer(snap()).lookup("par_rate")


def test_snapshots_pickle_and_deepcopy_with_identical_digests():
    s = snap()
    for clone in (pickle.loads(pickle.dumps(s)), copy.deepcopy(s), pickle.loads(pickle.dumps(SnapshotPricer(s))).snapshot):
        assert clone.digest() == s.digest() and clone.curves["usd-sofr"].values == DFS


def test_the_pricer_keeps_the_requested_stamp_when_given_one_for_a_snapshot_served_late():
    later = pd.Timestamp("2024-01-02 17:05", tz="America/New_York")
    assert SnapshotPricer(snap(), ts=later).ts == later  # the pricer's ts is the time it describes; the snapshot keeps its own row stamp
    assert SnapshotPricer(snap()).snapshot.ts == TS


def test_a_weekmask_is_canonical_whatever_form_the_provider_wrote_it_in():
    """A provider may write the weekmask as names or as a 7-bit string; every adapter reads the ONE canonical form (names), and the digest does not depend on the spelling."""
    from pricebt.snapshot import CalendarData

    names = CalendarData("c", (), "Mon Tue Wed Thu Fri")
    bits = CalendarData("c", (), "1111100")
    seq = CalendarData("c", (), (1, 1, 1, 1, 1, 0, 0))
    assert bits.weekmask == seq.weekmask == names.weekmask == "Mon Tue Wed Thu Fri"
    gulf = CalendarData("g", (), "0111110")
    assert gulf.weekmask == "Tue Wed Thu Fri Sat"
    with pytest.raises(Exception, match="weekmask|weekday"):
        CalendarData("bad", (), "Mon Foo")
    with pytest.raises(Exception, match="weekmask|weekday|7"):
        CalendarData("bad", (), "11111")
