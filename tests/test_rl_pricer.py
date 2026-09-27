"""RLCurvePricer: the rateslib adapter's immutable view of one snapshot (a float discount curve, published fixings, calendars, bond quotes and reference data). It exposes
objects as attributes and offers NO convention lookup (spec D7): the resolution of spot dates, tenors and `par` lives in the swap factory (test_rl_swap.py)."""
import datetime as dt
import pickle

import pandas as pd
import pytest

pytest.importorskip("rateslib")
pytestmark = pytest.mark.adapter_rateslib

from support.known import EOD, needs_fixtures, ny  # noqa: E402
from test_rl_common import SWAP_CONV, eod, par_curve, rl_pricer, seeded_fixings, template  # noqa: E402

import pricebt.contrib.rateslib as RL  # noqa: E402
from pricebt.contrib.rateslib import BondQuote, BondRef, RLCurvePricer  # noqa: E402
from pricebt.errors import ConfigError, MarketDataUnavailable  # noqa: E402
from pricebt.pricer import Pricer  # noqa: E402

CURVE_DATE = dt.datetime(2025, 1, 2)


@pytest.fixture(scope="module")
def curve():
    return par_curve(CURVE_DATE)


@pytest.fixture(scope="module")
def fx():
    f = seeded_fixings()
    return f[f.index < pd.Timestamp("2025-01-02")]


def make(curve, fixings=None, d="2025-01-02", hour=17, **kw):
    return RLCurvePricer(eod(d, hour), curve, fixings=fixings, **kw)


def test_reference_date_is_the_curve_initial_node_and_mismatch_raises(curve):
    p = make(curve)
    assert p.reference_date == dt.date(2025, 1, 2) and p.ts == eod("2025-01-02")
    with pytest.raises(ConfigError, match="reference_date"):
        RLCurvePricer(eod("2025-01-02"), curve, reference_date=dt.date(2025, 1, 3))
    with pytest.raises(ConfigError, match="tz-aware"):
        RLCurvePricer(pd.Timestamp("2025-01-02 17:00"), curve)


def test_pricer_is_immutable_and_picklable(curve, fx):
    p = make(curve, fx)
    with pytest.raises(AttributeError, match="immutable"):
        p.ts = eod("2025-01-03")
    q = pickle.loads(pickle.dumps(p))
    assert q.reference_date == p.reference_date and float(q.curve[dt.datetime(2026, 1, 2)]) == float(p.curve[dt.datetime(2026, 1, 2)]) and q.fixings.equals(p.fixings)


def test_the_pricer_meets_the_core_contract_and_exposes_the_curve_as_a_plain_attribute(curve):
    """Spec D7 / B7: `ts`, `describe()`, `lookup(name, **kw)` and `reference_date`; the curve is an attribute a binding reaches as `@pricer.curve`, not an injected kwarg."""
    p = make(curve)
    assert isinstance(p, Pricer) and p.curve is curve
    assert not [n for n in dir(p) if n.endswith("_kwargs")], "no kwargs-supplying method: signature injection by parameter name is gone (B7)"


def test_the_pricer_offers_no_convention_lookup(curve):
    """Spot dates, tenor arithmetic and par rates are the swap factory's (T4), never a pricer lookup (D7)."""
    p = make(curve)
    for name in ("spot_date", "calendar_advance", "par_rate", "curve", "vol"):
        with pytest.raises(LookupError):
            p.lookup(name)
        assert name == "curve" or not hasattr(p, name)


def test_fixings_are_held_exactly_as_the_provider_published_them(curve, fx):
    """The publication policy is applied ONCE, by the provider that owns the data (snapshot.py, support.common, synthetic.py); the pricer neither filters nor proxies."""
    p = make(curve, fx)
    assert p.fixings.equals(fx) and p.fixings.index.max() < pd.Timestamp(p.reference_date)
    assert make(curve).fixings is None
    with pytest.raises(MarketDataUnavailable):
        make(curve).fixings_for(dt.date(2024, 1, 1))
    assert p.fixings_for(dt.date(2024, 12, 1)).index.min() >= pd.Timestamp("2024-12-01") and len(p.fixings_for(dt.date(2024, 12, 1))) > 10
    assert p.fixings_for(dt.date(2024, 12, 2)).index.min() == pd.Timestamp("2024-12-02"), "the start date itself is included (a Monday with a fixing)"
    with pytest.raises(TypeError):
        RLCurvePricer(eod("2025-01-02", 6), curve, fixings=fx, fixings_policy={"pre_publish": "strict"})
    early = make(curve, fx, hour=6)
    assert early.fixings.equals(fx), "no second application of a publication rule at an early stamp"


def test_bond_lookups_and_missing_quotes(curve):
    ref = BondRef("T00000001", 4.25, dt.date(2024, 11, 15), dt.date(2034, 11, 15))
    p = make(curve, bonds={ref.cusip: ref}, quotes={ref.cusip: BondQuote(ref.cusip, clean=99.5)})
    assert p.bond("T00000001") is ref and p.bond_quote("T00000001").clean == 99.5 and p.quoted_cusips() == ["T00000001"]
    assert p.lookup("bond", alias_or_cusip="T00000001") is ref and p.lookup("bond_quote", cusip="T00000001").clean == 99.5
    with pytest.raises(MarketDataUnavailable):
        p.bond("NOPE")
    with pytest.raises(MarketDataUnavailable):
        p.bond_quote("NOPE")
    with pytest.raises(ConfigError):
        BondQuote("X")


def test_a_universe_resolves_aliases_and_the_pricer_hands_it_the_reference_date(curve):
    seen = []

    class Universe:
        def resolve(self, token, asof):
            seen.append((token, asof))
            return BondRef("A00000002", 4.5, dt.date(2025, 2, 15), dt.date(2035, 2, 15))

    p = make(curve, universe=Universe())
    assert p.bond(" CT10 ").cusip == "A00000002" and seen == [("CT10", dt.date(2025, 1, 2))]


def test_describe_reports_provenance(curve, fx):
    d = make(curve, fx, source="unit").describe()
    assert d["source"] == "unit" and d["n_nodes"] == curve.nodes.n and d["reference_date"] == "2025-01-02" and d["type"] == "RLCurvePricer"
    assert d["fixings_last"] < d["reference_date"] and d["curve_id"] == curve.id and d["n_quotes"] == 0
    assert "has_vol" not in d, "options and volatility are out of scope (spec 0.3)"


def test_the_wrapped_pricer_of_a_snapshot_is_the_same_kind_of_object():
    """Through `wrap` (the way the engine and the facade build it): the same attributes, immutable, and the snapshot's calendar object is the library's."""
    p = rl_pricer("2025-01-16", 0, 0, seeded_fixings())
    assert isinstance(p, Pricer) and isinstance(p, RLCurvePricer) and p.reference_date == dt.date(2025, 1, 16)
    with pytest.raises(AttributeError, match="immutable"):
        p.ts = eod("2025-01-17")
    cal = p.calendar("nyc")
    assert cal.is_bus_day(dt.datetime(2025, 1, 17)) and not cal.is_bus_day(dt.datetime(2025, 1, 20)), "MLK day is a holiday of the snapshot calendar"
    assert p.calendar("nyc") is cal, "memoised per pricer"
    with pytest.raises(ConfigError, match="no calendar 'nope'"):
        p.calendar("nope")


# ------------------------------------------------------------------ real fixtures (test-support provider -> snapshot -> wrap)
@pytest.mark.fixtures
@needs_fixtures
def test_real_eod_row_builds_a_sane_pricer():
    pytest.importorskip("pyarrow")
    from support.curves import CurveStore

    store = CurveStore(EOD)
    p = RL.wrap(store.get_pricer(ny("2025-01-15 17:00")))
    assert p.reference_date == dt.date(2025, 1, 15) and p.curve.nodes.n >= 26
    t = template(RL.swap, {**SWAP_CONV, "calendar": "usd_fed"}, {"side": "pay", "maturity": "10Y", "notional": 1e7, "fixed_rate": "par"})  # the fixture snapshot's calendar is `usd_fed`
    assert 3.0 < t.build(p, p.ts).terms["fixed_rate"] < 6.0
    assert p.fixings.index[-1] < pd.Timestamp("2025-01-15")
    with pytest.raises(MarketDataUnavailable):
        store.get_pricer(ny("2025-01-20 17:00"))  # MLK holiday: the row carries the prior day's nodes and is dropped, never served
