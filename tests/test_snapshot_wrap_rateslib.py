"""`pricebt.contrib.rateslib.wrap`: a plain-data snapshot becomes the rateslib pricer the pricables consume. Hand-made snapshots pin the mapping (interpolation
tag, fixings installed as given, aliases through securities, market clean convention, the roll-layer yield curve, memoisation); the real fixtures pin the
parity oracles (10Y par rates computed by the maintainer's infrastructure, quote yields, seasoned swaps, the repo proxy)."""
import datetime as dt
import math
import pickle

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("rateslib")
import pricebt.contrib.rateslib as RL  # noqa: E402
from pricebt.contrib.rateslib import _compat as C  # noqa: E402
from pricebt.contrib.rateslib.bond import market_clean  # noqa: E402
from pricebt.contrib.rateslib.wrap import TAGS, WrappedCurvePricer, WrappedQuotePricer, YtmCurve, rl_interpolation, wrap  # noqa: E402
from pricebt.errors import ConfigError, MarketDataUnavailable  # noqa: E402
from pricebt.pricable import MarkContext  # noqa: E402
from pricebt.snapshot import CalendarData, CurveSnapshot, FixingsSeries, MarketSnapshot, Quote, QuoteSet, Security, SnapshotPricer  # noqa: E402
from pricebt.testing.synthetic import SyntheticMarket  # noqa: E402
from support.known import ARBS_PAR_10Y, EOD, FIXTURES, GSQ, MIN, needs_fixtures, ny  # noqa: E402
from test_rl_common import BOND_CONV, SWAP_CONV, template  # noqa: E402

pytestmark = pytest.mark.adapter_rateslib
D = dt.date
REF = D(2024, 6, 12)
TS = pd.Timestamp("2024-06-12 17:00", tz="America/New_York")
SWAP_CAL, BOND_CAL = "usd_fed", "us_govt"  # the calendar names of the snapshots below (hand-made ones carry `usd_fed` only) and of the test-support bond provider


def par(w, tenor, cal=SWAP_CAL):
    """The spot-starting `tenor` par rate (percent) of a wrapped snapshot: the swap factory resolves the token `par` on the snapshot's curve and calendar (spec T4)."""
    t = template(RL.swap, {**SWAP_CONV, "calendar": cal}, {"side": "pay", "maturity": tenor, "notional": 1e7, "fixed_rate": "par"})
    return t.build(w, w.ts).terms["fixed_rate"]


def rlswap(w, effective, maturity, rate, side="pay", notional=1e7, cal=SWAP_CAL):
    return template(RL.swap, {**SWAP_CONV, "calendar": cal}, {"side": side, "effective": effective, "maturity": maturity, "notional": notional, "fixed_rate": rate}).build(w, w.ts).obj


def rlbond(w, security, side="buy", notional=1e7, cal=SWAP_CAL, **repo):
    """The RLBond the `bond` kit builds for `security` (an id or an alias) on the wrapped pricer; `repo` are its repo terms."""
    terms = {"side": side, "security": security, "notional": notional, **({"extras": {"repo": repo}} if repo else {})}
    return template(RL.bond, {**BOND_CONV, "calendar": cal}, terms).build(w, w.ts).obj


# ============================================================================ hand-made snapshots
def curve(name="sofr", ref=REF, dfs=(1.0, 0.9965, 0.9830, 0.9640)):
    days = (0, 30, 182, 365)
    return CurveSnapshot(name, ref, tuple(ref + dt.timedelta(days=x) for x in days), dfs)


def fixings(dates=(D(2024, 6, 7), D(2024, 6, 10), D(2024, 6, 11)), values=(5.31, 5.32, 5.33), unit="percent", proxied=()):
    return FixingsSeries("USD-SOFR-1D", dates, values, unit, proxied)


def secs():
    return {
        "T2Y000001": Security("T2Y000001", 4.5, D(2024, 5, 31), D(2026, 5, 31), "T 4 1/2 May 26"),
        "T10000002": Security("T10000002", 4.25, D(2024, 5, 15), D(2034, 5, 15), "T 4 1/4 May 34"),
        "T10000001": Security("T10000001", 4.0, D(2024, 2, 15), D(2034, 2, 15), "T 4 Feb 34"),
        "SYN012029": Security("SYN012029", 3.875, D(2024, 2, 15), D(2029, 2, 15)),
    }


def qset(convention="market", **over):
    kw = dict(reference_date=REF, quotes={"T10000002": Quote(clean=99.5, bid=99.4, offer=99.6), "T2Y000001": Quote(ytm=4.8), "T10000001": Quote(clean=98.0, bid=97.9)},
              securities=secs(), aliases={"CT10": "T10000002", "O10": "T10000001", "CT2": "T2Y000001"}, price_convention=convention,
              provenance={"flags": {"T2Y000001": {"repeat": True, "run": 3}}})
    kw.update(over)
    return QuoteSet(**kw)


def snapshot(**over):
    kw = dict(ts=TS, reference_date=REF, curves={"sofr": curve()}, fixings={"USD-SOFR-1D": fixings()}, quotes=qset(),
              calendars={"usd_fed": CalendarData("usd_fed", (D(2024, 7, 4),))}, provenance={"source": "unit", "asset": "X"})
    kw.update(over)
    return MarketSnapshot(**kw)


def sp(**over):
    return SnapshotPricer(snapshot(**over))


def test_the_curve_is_built_from_the_snapshot_nodes_with_log_linear_interpolation():
    p = sp(quotes=None)
    w = wrap(p)
    assert isinstance(w, WrappedCurvePricer) and w.reference_date == REF and w.ts == TS
    c = curve()
    assert [k.date() for k in w.curve.nodes.nodes] == list(c.node_dates) and [float(v) for v in w.curve.nodes.nodes.values()] == list(c.values)
    assert w.curve.interpolator.local_name == "log_linear" and str(w.curve.meta.convention).lower() == "act360" and w.curve.id == "sofr"
    # independent oracle: ln DF is linear in CALENDAR days between nodes
    mid = REF + dt.timedelta(days=100)
    want = math.exp(math.log(0.9965) + (math.log(0.9830) - math.log(0.9965)) * (100 - 30) / (182 - 30))
    assert float(w.curve[C.to_dt(mid)]) == pytest.approx(want, abs=1e-14)
    assert w.curve is not None and w.curve.id == "sofr", "bindings reach the curve as the plain attribute `@pricer.curve`"


def test_an_interpolation_tag_without_a_rateslib_equivalent_is_an_error_never_a_substitute():
    assert TAGS == {"log_linear_df": "log_linear"} and rl_interpolation("log_linear_df") == "log_linear"
    for tag in ("flat_forward", "linear", "log_linear", ""):
        with pytest.raises(ConfigError, match="cannot honour"):
            rl_interpolation(tag)
    c = curve()
    object.__setattr__(c, "interpolation", "flat_forward")  # the snapshot itself refuses such a tag; a corrupted one must not slip through the adapter
    with pytest.raises(ConfigError, match="flat_forward"):
        wrap(sp(curves={"sofr": c}, quotes=None))


def test_the_swap_curve_of_a_panel_snapshot_is_the_snapshot_curve():
    w = wrap(sp())
    assert isinstance(w, WrappedQuotePricer) and w.has_curve and w.curve.id == "sofr"
    r = par(w, "1Y")
    assert 0.0 < r < 10.0 and r == par(wrap(sp(quotes=None)), "1Y"), "the panel pricer prices swaps on the very curve of the snapshot"


def test_fixings_are_installed_exactly_as_the_snapshot_carries_them():
    """The snapshot stamp is 06:00, before the fixing of 06-11 is public: the OLD pricer would have proxied it. The provider owns that rule, so the wrapped
    pricer must not apply it a second time."""
    early = pd.Timestamp("2024-06-12 06:00", tz="America/New_York")
    f = fixings()
    w = wrap(SnapshotPricer(snapshot(ts=early, fixings={"USD-SOFR-1D": f}, quotes=None)))
    assert list(w.fixings.values) == [5.31, 5.32, 5.33] and w.fixings.index[-1] == pd.Timestamp("2024-06-11")
    prox = wrap(SnapshotPricer(snapshot(ts=early, fixings={"USD-SOFR-1D": fixings(values=(5.31, 5.32, 5.32), proxied=(D(2024, 6, 11),))}, quotes=None)))
    assert list(prox.fixings.values) == [5.31, 5.32, 5.32] and not hasattr(prox, "policy"), "the pricer holds no publication rule of its own"


def test_fixings_arrive_in_percent_on_a_naive_midnight_index_whatever_the_snapshot_unit():
    w = wrap(sp(quotes=None))
    assert w.fixings.dtype == np.float64 and w.fixings.index.tz is None and w.fixings.index.dtype == np.dtype("datetime64[ns]") and (w.fixings.index == w.fixings.index.normalize()).all()
    dec = wrap(sp(fixings={"USD-SOFR-1D": fixings(values=(0.0531, 0.0532, 0.0533), unit="decimal")}, quotes=None))
    assert list(dec.fixings.values) == pytest.approx([5.31, 5.32, 5.33])
    none = wrap(sp(fixings={}, quotes=None))
    assert none.fixings is None and none.describe()["fixings_last"] is None
    empty = wrap(sp(fixings={"USD-SOFR-1D": FixingsSeries("USD-SOFR-1D", (), ())}, quotes=None))
    assert len(empty.fixings) == 0
    C.check_fixings(w.fixings[w.fixings.index >= pd.Timestamp("2024-06-07")], dt.datetime(2024, 6, 7), dt.datetime(2024, 6, 12))


def test_a_fixings_history_is_never_seen_beyond_the_reference_date():
    w = wrap(sp(quotes=None))
    assert w.fixings.index.max() < pd.Timestamp(w.reference_date) and w.fixings_for(D(2024, 6, 10)).index.min() == pd.Timestamp("2024-06-10")


# ------------------------------------------------------------------ bonds through the snapshot
def test_aliases_and_security_ids_resolve_through_the_snapshot():
    w = wrap(sp())
    assert w.bond("CT10") == w.bond("ct10") == w.bond(" CT10 ") == w.bond("T10000002")
    ref = w.bond("CT10")
    assert (ref.cusip, ref.coupon, ref.issue_date, ref.maturity_date, ref.label) == ("T10000002", 4.25, D(2024, 5, 15), D(2034, 5, 15), "T 4 1/4 May 34")
    assert w.bond("O10").cusip == "T10000001" and w.bond("CT2").cusip == "T2Y000001"
    assert w.bond("SYN012029").coupon == 3.875, "an id that is neither quoted nor an alias target still resolves: reference data"
    assert w.bond("CT010").cusip == "T10000002", "a token is read as bucket <n> whatever its zero padding"
    with pytest.raises(MarketDataUnavailable, match="no CT7"):
        w.bond("CT7")
    with pytest.raises(MarketDataUnavailable, match="OOO10"):
        w.bond("OOO10")
    with pytest.raises(MarketDataUnavailable, match="not in the reference table"):
        w.bond("912828ZZ9")
    with pytest.raises(ConfigError, match="unknown bond token"):
        w.bond("CTX")
    assert w.on_the_run("10-Year").cusip == "T10000002" and w.on_the_run(10, 1).cusip == "T10000001" and w.on_the_run("10", 0) == w.bond("CT10")
    with pytest.raises(MarketDataUnavailable, match="rank"):
        w.on_the_run(10, 4)
    with pytest.raises(MarketDataUnavailable):
        w.on_the_run(10, 3)


def test_a_snapshot_without_a_quote_panel_knows_no_bonds():
    w = wrap(sp(quotes=None))
    assert w.quoted_cusips() == []
    with pytest.raises(MarketDataUnavailable):
        w.bond("CT10")
    with pytest.raises(MarketDataUnavailable):
        w.bond_quote("T10000002")
    assert not hasattr(w, "CLEAN_CONVENTION") and "bid_offer" not in w.LOOKUPS


def test_quotes_reference_data_and_lookups_of_the_panel():
    w = wrap(sp())
    assert w.quoted_cusips() == ["T10000001", "T10000002", "T2Y000001"]
    q = w.bond_quote("T10000002")
    assert (q.cusip, q.clean, q.ytm) == ("T10000002", 99.5, None) and w.bond_quote("T2Y000001").ytm == 4.8 and w.bond_quote("T2Y000001").clean is None
    with pytest.raises(MarketDataUnavailable, match="no quote for SYN012029"):
        w.bond_quote("SYN012029")
    assert w.bid_offer("T10000002") == (99.4, 99.6) and w.bid_offer("T10000001") is None and w.bid_offer("T2Y000001") is None and w.bid_offer("nope") is None, "both sides or none"
    assert w.quote_flags("T2Y000001") == {"repeat": True, "run": 3} and w.quote_flags("T10000002") == {} and w.quote_flags("nope") == {}
    assert w.lookup("bond_quote", cusip="T10000001").clean == 98.0 and w.lookup("bond", alias_or_cusip="CT2").cusip == "T2Y000001"
    assert {"bid_offer", "quote_flags", "on_the_run", "quoted_cusips", "bond_ytm", "bond_clean", "repo_fixing"} <= set(w.LOOKUPS)
    with pytest.raises(LookupError):
        w.lookup("curve")


def test_the_clean_price_convention_follows_the_snapshot():
    m, u = wrap(sp(quotes=qset("market"))), wrap(sp(quotes=qset("unspecified")))
    assert m.CLEAN_CONVENTION == "market" and market_clean(m)
    assert not hasattr(u, "CLEAN_CONVENTION") and not market_clean(u)


def test_a_panel_without_a_curve_gets_a_placeholder_and_refuses_swap_pricing():
    w = wrap(sp(curves={}))
    assert isinstance(w, WrappedQuotePricer) and not w.has_curve and w.curve is None, "no curve to bind: `@pricer.curve` is None, never a stand-in"
    with pytest.raises(MarketDataUnavailable, match="funding curve"):
        par(w, "10Y")
    assert not hasattr(w, "par_rate"), "a pricer resolves no convention (D7): `par` is the swap factory's"
    assert w.describe()["curve"] == "none" and w.describe()["n_nodes"] == 0 and w.describe()["digest"] == w.digest and wrap(sp()).describe()["curve"] == "sofr"
    assert w.bond("CT10").cusip == "T10000002" and w.bond_quote("T10000002").clean == 99.5


def test_bond_ytm_and_clean_lookups_agree_with_the_rlbond_mark():
    w = wrap(sp(quotes=qset("market")))
    b = rlbond(w, "CT10")
    m = b.mark(w)
    assert w.lookup("bond_ytm", bond="CT10") == pytest.approx(m["ytm"], abs=1e-12) and w.lookup("bond_ytm", bond=b.cusip) == w.bond_ytm("CT10")
    assert w.lookup("bond_clean", bond="CT10") == 99.5
    assert w.lookup("bond_ytm", bond="CT2") == 4.8, "a yield quote is returned as it is"
    clean2 = w.lookup("bond_clean", bond="CT2")
    assert 90.0 < clean2 < 110.0
    q = wrap(sp(quotes=qset("market", quotes={"T2Y000001": Quote(ytm=4.8)})))
    back = rlbond(q, "CT2").mark(q)
    assert back["ytm"] == 4.8 and back["clean"] == pytest.approx(clean2, abs=1e-9)


def test_the_roll_layer_curve_interpolates_the_quoted_on_the_run_yields():
    w = wrap(sp(quotes=qset("market", quotes={"T10000002": Quote(ytm=4.0), "T2Y000001": Quote(ytm=5.0), "T10000001": Quote(ytm=3.0)})))
    yc = w.ytm_curve()
    t2, t10 = (D(2026, 5, 31) - REF).days / 365.25, (D(2034, 5, 15) - REF).days / 365.25
    assert isinstance(yc, YtmCurve) and yc.ttm == (t2, t10) and yc.ytm == (5.0, 4.0), "the CT bonds only: the older 10Y (O10) is not on the run"
    assert yc((t2 + t10) / 2) == pytest.approx(4.5) and yc(0.1) == 5.0 and yc(40.0) == 4.0, "linear between, flat outside"
    assert w.ytm_curve() is yc and w.lookup("ytm_curve") is yc
    one = wrap(sp(quotes=qset("market", quotes={"T10000002": Quote(ytm=4.0)})))
    assert one.ytm_curve() is None, "fewer than two points"
    unquoted = wrap(sp(quotes=qset("market", quotes={"T10000001": Quote(ytm=3.0), "T2Y000001": Quote(ytm=5.0)})))
    assert unquoted.ytm_curve() is None, "CT10 has no quote and O10 is not on the run"


def test_the_roll_layer_curve_solves_clean_quotes_at_the_reference_date_as_the_mark_does():
    w = wrap(sp(quotes=qset("market", quotes={"T10000002": Quote(clean=99.5), "T2Y000001": Quote(clean=100.25)})))
    yc = w.ytm_curve()
    for cusip, y in zip(("T2Y000001", "T10000002"), yc.ytm):
        b = rlbond(w, cusip, notional=1e6)
        assert y == pytest.approx(b.mark(w)["ytm"], abs=1e-12)


def test_the_roll_layer_curve_follows_the_clean_price_convention_on_a_coupon_date():
    """2024-08-15 is a coupon date of the 10y bond: under the market convention its clean quote is solved as a (coupon-excluding) dirty price, which is what
    `RLBond.mark` does; under the library convention it is not. The roll curve must agree with the mark in both cases."""
    ref = D(2024, 8, 15)
    ts = pd.Timestamp("2024-08-15 17:00", tz="America/New_York")
    got = {}
    for conv in ("market", "unspecified"):
        qs = qset(conv, reference_date=ref, quotes={"T10000001": Quote(clean=98.0), "T2Y000001": Quote(clean=100.25)}, aliases={"CT10": "T10000001", "CT2": "T2Y000001"})
        w = wrap(SnapshotPricer(snapshot(ts=ts, reference_date=ref, curves={"sofr": curve(ref=ref)}, quotes=qs)))
        yc = w.ytm_curve()
        mark = rlbond(w, "T10000001", notional=1e6).mark(w)["ytm"]
        assert yc.ytm[1] == pytest.approx(mark, abs=1e-12) and w.bond_ytm("CT10") == pytest.approx(mark, abs=1e-12), conv
        got[conv] = mark
    assert abs(got["market"] - got["unspecified"]) > 1e-4, "control: the two conventions differ on a coupon date, so agreeing with the mark is not automatic"


def test_a_panel_without_on_the_run_aliases_uses_every_quoted_bond_for_the_roll_curve():
    w = wrap(sp(quotes=qset("unspecified", aliases={}, quotes={"T10000002": Quote(ytm=4.0), "T2Y000001": Quote(ytm=5.0), "SYN012029": Quote(ytm=4.4)})))
    yc = w.ytm_curve()
    assert len(yc.ttm) == 3 and list(yc.ttm) == sorted(yc.ttm) and yc.ytm[0] == 5.0 and yc.ytm[-1] == 4.0


def test_the_repo_proxy_is_the_newest_published_fixing():
    w = wrap(sp())
    assert w.repo_fixing() == 5.33 and w.lookup("repo_fixing") == 5.33
    for s in (sp(fixings={}), sp(fixings={"USD-SOFR-1D": FixingsSeries("USD-SOFR-1D", (), ())})):
        with pytest.raises(MarketDataUnavailable, match="no fixings"):
            wrap(s).repo_fixing()


def test_describe_carries_the_digest_and_the_provider_provenance():
    p = sp()
    w = wrap(p)
    d = w.describe()
    assert d["digest"] == p.digest == p.snapshot.digest() and d["source"] == "unit" and d["asset"] == "X" and d["n_quotes"] == 3 and d["curve_id"] == "sofr"
    assert d["fixings_last"] == "2024-06-11" == d["fixings_through"] and d["reference_date"] == "2024-06-12" and d["n_nodes"] == 4 and d["type"] == "WrappedQuotePricer"
    assert wrap(sp(quotes=None)).describe()["type"] == "WrappedCurvePricer"
    assert wrap(sp(quotes=None)).calendars["usd_fed"].holidays == (D(2024, 7, 4),) and w.snapshot is p.snapshot


def test_wrapping_is_memoised_per_snapshot_pricer_and_the_wrapped_pricer_pickles():
    p = sp()
    w = wrap(p)
    assert wrap(p) is w and wrap(p, curve="sofr") is not w and wrap(sp()) is not w
    with pytest.raises(AttributeError, match="immutable"):
        w.ts = TS
    q = pickle.loads(pickle.dumps(w))
    assert q.digest == w.digest and q.bond("CT10") == w.bond("CT10") and par(q, "1Y") == par(w, "1Y") and q.CLEAN_CONVENTION == "market"
    assert q.describe() == w.describe() and q.fixings.equals(w.fixings) and q.ytm_curve() == w.ytm_curve()


def test_selecting_a_curve_and_fixings_by_name():
    two = sp(curves={"sofr": curve(), "ois": curve("ois", dfs=(1.0, 0.99, 0.97, 0.94))}, fixings={"USD-SOFR-1D": fixings(), "other": FixingsSeries("other", (D(2024, 6, 11),), (1.0,))})
    with pytest.raises(ConfigError, match="several curves"):
        wrap(two)
    with pytest.raises(ConfigError, match="several fixingss|several fixings"):
        wrap(sp(curves={"sofr": curve()}, fixings=two.snapshot.fixings, quotes=None))
    w = wrap(two, curve="ois", fixings="other")
    assert w.curve.id == "ois" and list(w.fixings.values) == [1.0] and float(w.curve[C.to_dt(REF + dt.timedelta(days=30))]) == pytest.approx(0.99, abs=1e-15)
    with pytest.raises(ConfigError, match="no curve 'nope'"):
        wrap(two, curve="nope")
    with pytest.raises(ConfigError, match="no fixings 'nope'"):
        wrap(two, curve="ois", fixings="nope")
    with pytest.raises(ConfigError, match="nothing to wrap"):
        wrap(sp(curves={}, quotes=None))


# ============================================================================ the synthetic market through wrap
def test_the_synthetic_market_prices_swaps_and_bonds_through_wrap():
    m = SyntheticMarket("2024-01-02", "2024-12-31", calendar=C.nyc_calendar(), seed=7)
    ts = pd.Timestamp("2024-06-12 17:00", tz="America/New_York")
    w = wrap(m.get_pricer(ts))
    assert 0.5 < par(w, "10Y") < 9.0 and w.describe()["source"] == "synthetic" and not hasattr(w, "CLEAN_CONVENTION")
    assert w.quoted_cusips() == ["SYN002027", "SYN012029", "SYN032032", "SYN042050"] and w.bond("SYN022034").issue_date == D(2024, 11, 15)
    b = rlbond(w, "SYN032032", notional=1e6)
    y = b.mark(w)["ytm"]
    assert y == w.bond_quote("SYN032032").ytm and y == pytest.approx(m.ytm_at(D(2024, 6, 12), (D(2032, 8, 15) - D(2024, 6, 12)).days / 365.25, ts) + m._spread["SYN032032"], abs=1e-12)
    assert isinstance(w.ytm_curve(), YtmCurve) and len(w.ytm_curve().ttm) == 4
    late = wrap(m.get_pricer(pd.Timestamp("2024-12-02 17:00", tz="America/New_York")))
    assert "SYN022034" in late.quoted_cusips()
    prev = wrap(m.get_pricer(pd.Timestamp("2024-06-11 17:00", tz="America/New_York")))
    sw = rlswap(prev, D(2024, 1, 8), D(2027, 1, 8), 3.9)
    v = sw.value(ctx=MarkContext.standalone(w, prev=prev))
    assert np.isfinite(v.pv) and abs(v.pv) > 1e3, "a seasoned swap needs the wrapped fixings"


# ============================================================================ real fixtures
@pytest.fixture(scope="module")
def stores():
    pytest.importorskip("pyarrow")
    from support.curves import CurveStore

    return {a: CurveStore(a) for a in (EOD, MIN, GSQ)}


@pytest.mark.fixtures
@needs_fixtures
@pytest.mark.parametrize("key", sorted(ARBS_PAR_10Y))
def test_10y_par_matches_the_reference_infrastructure_through_wrap(key, stores):
    asset, when = key
    rate = par(wrap(stores[asset].get_pricer(ny(when))), "10Y")
    assert abs(rate - ARBS_PAR_10Y[key]) < 1e-9, (key, rate)


@pytest.mark.fixtures
@needs_fixtures
def test_a_seasoned_swap_marks_on_the_wrapped_store_pricers(stores):
    eod = stores[EOD]
    p0, p1 = wrap(eod.get_pricer(ny("2026-08-04 17:00"))), wrap(eod.get_pricer(ny("2026-08-05 17:00")))
    sw = rlswap(p0, dt.date(2026, 1, 8), dt.date(2031, 1, 8), 3.9)
    v = sw.value(ctx=MarkContext.standalone(p1, prev=p0))
    assert np.isfinite(v.pv) and abs(v.pv) > 1e3
    assert p1.fixings.index[-1] == pd.Timestamp("2026-08-04") and p1.describe()["fixings_through"] == "2026-08-04"


@pytest.mark.fixtures
@needs_fixtures
def test_the_swap_calendar_of_the_snapshot_is_the_library_calendar(stores):
    snap = stores[EOD].get_pricer(ny("2026-08-05 17:00")).snapshot
    cd = snap.calendars["usd_fed"]
    lib = {h.date() for h in C.calendar().holidays if h.weekday() < 5}
    window = lambda s: {h for h in s if D(2018, 4, 2) <= h <= D(2035, 12, 31)}  # noqa: E731
    assert window(cd.holidays) == window(lib), "the fixture calendar IS the library calendar in the data window"


@pytest.mark.fixtures
@needs_fixtures
def test_quotes_reproduce_the_reference_yields_through_wrap():
    pytest.importorskip("pyarrow")
    from support.ust_eod import UstEod

    fi = UstEod(window=("2026-08-01", "2026-08-21"))
    for day, cusip, clean, want in (("2026-08-05", "91282CQQ7", 98.0625, 4.6220), ("2026-08-19", "91282CRF0", 99.84375, 4.644627)):
        w = wrap(fi.get_pricer(ny(f"{day} 17:00")))
        ref = w.bond("CT10")
        assert ref.cusip == cusip and w.bond_quote(cusip).clean == clean
        settle_t1 = C.add_bus_days(w.reference_date, 1)
        from pricebt.contrib.rateslib.bond import make_ust

        y_t1 = make_ust(ref).ytm(clean, settle_t1, dirty=False)
        assert abs(float(y_t1) - want) < (5e-5 if day == "2026-08-05" else 5e-7)
        m = rlbond(w, "CT10", cal=BOND_CAL).mark(w)
        yc = w.ytm_curve()
        assert isinstance(yc, YtmCurve) and len(yc.ttm) == 7 and yc((ref.maturity_date - w.reference_date).days / 365.25) == pytest.approx(m["ytm"], abs=1e-9), "same settlement as RLBond"
        assert w.CLEAN_CONVENTION == "market" and w.curve is None and w.describe()["curve"] == "none"
        with pytest.raises(MarketDataUnavailable, match="funding curve"):
            par(w, "10Y", cal=SWAP_CAL)


@pytest.mark.fixtures
@needs_fixtures
def test_a_clean_quote_on_a_coupon_date_marks_with_zero_accrued_and_a_continuous_yield():
    """The panel says its clean prices follow the market convention (accrued 0 on a coupon date): `RLBond` must solve the yield accordingly, else it is ~18bp off
    and V(D) double counts the coupon."""
    pytest.importorskip("pyarrow")
    from support.ust_eod import UstEod

    f = UstEod(window=("2018-08-01", "2018-08-31"))
    ps = {dd: wrap(f.get_pricer(ny(f"2018-08-{dd} 17:00"))) for dd in (14, 15, 16)}
    b = rlbond(ps[14], "9128283W8", cal=BOND_CAL)  # T 2 3/4 Feb-28, coupon 2018-08-15
    m = {dd: b.mark(p) for dd, p in ps.items()}
    assert market_clean(ps[15])
    assert ps[15].bond_quote(b.cusip).clean == 99.0 and m[15]["accrued"] == 0.0 and m[15]["dirty"] == pytest.approx(99.0, abs=1e-9)
    assert abs(m[15]["ytm"] - m[14]["ytm"]) < 0.04 and abs(m[16]["ytm"] - m[15]["ytm"]) < 0.04, {k: x["ytm"] for k, x in m.items()}
    assert b.pv_of(ps[15], m[15]) == pytest.approx(1e7 / 100 * 99.0 + 137500.0, abs=1e-3), "V(D) = clean value + the coupon paid on D, once"
    assert ps[15].lookup("bond_ytm", bond=b.cusip) == pytest.approx(m[15]["ytm"], abs=1e-12)


@pytest.mark.fixtures
@needs_fixtures
def test_the_repo_proxy_reads_the_last_published_fixing_and_refuses_when_stale_or_absent(tmp_path):
    pytest.importorskip("pyarrow")
    from support.common import load_fixings
    from support.ust_eod import UstEod

    fi = UstEod(window=("2018-05-01", "2018-06-30"), fixings="auto")
    p0, p1 = wrap(fi.get_pricer(ny("2018-06-04 17:00"))), wrap(fi.get_pricer(ny("2018-06-05 17:00")))
    fx = load_fixings(FIXTURES / "fixings" / "USD-SOFR-1D.parquet")
    want = float(fx[fx.index < pd.Timestamp("2018-06-04")].iloc[-1])
    assert p0.lookup("repo_fixing") == want and fx.index[fx.index < pd.Timestamp("2018-06-04")][-1] == pd.Timestamp("2018-06-01")
    b = rlbond(p0, "CT10", cal=BOND_CAL, gc_rate="pricer")
    assert b.repo_rate(p0) == want
    older = fx[fx.index < pd.Timestamp("2018-06-05")]
    assert b.repo_rate(p1) == float(older.iloc[-1]) == pytest.approx(1.80, abs=1e-12), "06-05 uses the 06-04 fixing (1.80)"
    assert b.repo_rate(p1) != float(older.iloc[-2]), "... not the 06-01 fixing (1.81)"
    v = b.value(ctx=MarkContext.standalone(p1, prev=p0))
    assert v.financing == pytest.approx(-1e7 / 100 * b.mark(p0)["dirty"] * want / 100 / 360, rel=1e-12)
    bare = wrap(UstEod(window=("2018-06-01", "2018-06-08")).get_pricer(ny("2018-06-05 17:00")))
    with pytest.raises(MarketDataUnavailable, match="no published fixings"):
        rlbond(bare, "CT10", cal=BOND_CAL, gc_rate="pricer").repo_rate(bare)
    import pandas as _pd

    fx0 = _pd.read_parquet(FIXTURES / "fixings" / "USD-SOFR-1D.parquet")
    fx0[_pd.to_datetime(fx0["date"]) <= "2018-05-15"].to_parquet(tmp_path / "old.parquet")
    stale = wrap(UstEod(window=("2018-06-01", "2018-06-08"), fixings=tmp_path / "old.parquet").get_pricer(ny("2018-06-05 17:00")))
    with pytest.raises(MarketDataUnavailable, match="stale"):
        b.repo_rate(stale)


@pytest.mark.fixtures
@needs_fixtures
def test_webull_yields_drive_rlbond_through_wrap():
    pytest.importorskip("pyarrow")
    from support.ust_minute import UstMinute

    wb = UstMinute(fixings="auto", outlier_bp=5.0)
    p = wrap(wb.get_pricer(ny("2026-03-09 10:00")))
    assert isinstance(p, WrappedQuotePricer) and p.bond("CT10").cusip == "91282CPZ8" and p.reference_date == dt.date(2026, 3, 9)
    b = rlbond(p, "CT10", cal=BOND_CAL)
    m = b.mark(p)
    assert m["ytm"] == p.bond_quote("91282CPZ8").ytm and 90.0 < m["clean"] < 110.0 and p.lookup("bond_ytm", bond="CT10") == m["ytm"]
    assert p.describe()["kind"] == "intraday" and pickle.loads(pickle.dumps(p)).bond_quote("91282CPZ8") == p.bond_quote("91282CPZ8")
    spike = wrap(wb.get_pricer(ny("2026-03-12 08:44")))
    assert spike.bond_quote("91282CPZ8").ytm == 4.225 and spike.quote_flags("91282CPZ8")["filtered"] is True
