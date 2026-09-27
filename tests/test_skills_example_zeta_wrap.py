"""The service example (`skills/pricebt-wire-external-library/example-service/`): tests that pin the zeta `wrap` and `_compat` (the wrap tests of `pricebt-wrap-and-pricer`, adapted to a SERVICE): the
conversion of a snapshot into a zeta market, the once-per-snapshot upload proved with `client.stats`, immutability and pickling, the refusals, the calendar, and the translation of zeta's error dicts.
Needs no pricing library (zeta is fictional): marker `core`; the example directories are put on sys.path from Path(__file__)."""
import copy
import datetime as dt
import math
import pickle
import socket
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "skills" / "pricebt-wire-external-library" / "example-service"
for _p in (str(EXAMPLE), str(EXAMPLE / "zeta_lib")):  # the example is NOT on pytest.ini's pythonpath: add it from this file's location, never from the working directory
    if _p not in sys.path:
        sys.path.insert(0, _p)

import zeta  # noqa: E402  (the fictional service client: a wrong path fails loudly here instead of skipping every test)

pytestmark = pytest.mark.core

import zeta_adapter as A  # noqa: E402
from zeta_adapter import _compat as C  # noqa: E402
from pricebt import api  # noqa: E402
from pricebt.errors import ConfigError, MarketDataUnavailable, MethodCallError  # noqa: E402
from pricebt.market import Binding, MarketData  # noqa: E402
from pricebt.pricer import FunctionMDP  # noqa: E402
from pricebt.snapshot import CalendarData, CurveSnapshot, FixingsSeries, MarketSnapshot, SnapshotPricer  # noqa: E402
from pricebt.testing.layer_conformance import flat_swap_world  # noqa: E402
from pricebt.timeutil import Clock  # noqa: E402

HOL = (dt.date(2024, 3, 29), dt.date(2024, 5, 27), dt.date(2024, 7, 4))
REF = dt.date(2024, 6, 12)
BASE, OVERLAY = EXAMPLE / "zeta_adapter/config/zeta_tieout_base.yaml", EXAMPLE / "zeta_adapter/config/zeta_swap.yaml"


@pytest.fixture(autouse=True)
def own_client(monkeypatch):
    """Offline (zeta is in-process, but a live client would be a network one: nothing here may touch the network) and a FRESH client per test, so `client.stats` is this test's."""
    def refuse(*a, **k):
        raise RuntimeError("an offline test touched the network")

    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    cli = zeta.Client()
    C.set_client(cli)
    C.UPLOADS.update(snapshot=0, derived=0)
    yield cli
    C.set_client(None)


def snap(ref=REF, holidays=HOL):
    return SnapshotPricer(flat_swap_world("cal", holidays=holidays)(ref, 0.0))


def test_the_wrapped_pricer_carries_the_protocol_and_the_digest():
    sp = snap()
    p = A.wrap(sp)
    assert (p.ts, p.reference_date, p.digest) == (sp.ts, REF, sp.digest) and p.describe()["digest"] == sp.digest and p.describe()["calendar"] == "cal"


def test_wrap_is_memoised_per_snapshot_pricer_and_idempotent():
    sp = snap()
    p = A.wrap(sp)
    assert A.wrap(sp) is p and A.wrap(p) is p and A.wrap(snap()) is not p


def test_the_wrapped_pricer_is_immutable():
    with pytest.raises(AttributeError, match="immutable"):
        A.wrap(snap()).ts = None


def test_wrap_needs_a_snapshot_pricer():
    with pytest.raises(ConfigError, match="wrap needs a SnapshotPricer"):
        A.ZetaPricer(object())


def test_the_zeta_market_is_the_snapshot_in_zetas_units():
    """DF -> continuously compounded zero rate in BASIS POINTS on ACT/365, fixings percent -> bp, holidays as ISO strings, asof as an ISO string."""
    sp = snap()
    m = A.wrap(sp).need_market()
    c = sp.snapshot.curves["curve"]
    assert m["asof"] == REF.isoformat() and m["curve"]["day_count"] == "ACT/365" and m["calendar"]["holidays"] == [h.isoformat() for h in HOL]
    for (days, z), d, df in zip(m["curve"]["nodes"], c.node_dates[1:], c.values[1:]):
        assert isinstance(days, int) and days == (d - REF).days  # zeta refuses 7.0 (Z101): whole days
        assert math.exp(-z * 1e-4 * days / 365.0) == pytest.approx(df, rel=0, abs=1e-14)  # the round trip zeta will do
    f = sp.snapshot.fixings["fixings"]
    assert m["fixings"]["SOFR"][f.dates[-1].isoformat()] == pytest.approx(f.values[-1] * 100.0)
    assert 390.0 < m["fixings"]["SOFR"][f.dates[-1].isoformat()] < 410.0  # a 4% fixing is ~400 bp, not 4.0 and not 0.04


@pytest.mark.parametrize("unit", ["percent", "decimal"])
def test_fixings_reach_zeta_in_basis_points_whatever_the_snapshot_unit(unit):
    s = snap().snapshot
    f = s.fixings["fixings"]
    vals = tuple(v * (1.0 if unit == "percent" else 0.01) for v in f.values)  # the same rates, spelled in `unit`
    p = A.wrap(SnapshotPricer(MarketSnapshot(s.ts, REF, s.curves, {"fixings": FixingsSeries("fixings", f.dates, vals, unit)}, None, s.calendars)))
    assert p.need_market()["fixings"]["SOFR"][f.dates[-1].isoformat()] == pytest.approx(f.values[-1] * 100.0, rel=1e-12)


def test_a_tag_zeta_cannot_honour_is_refused_at_wrap_time_never_substituted():
    sp = snap()
    c = sp.snapshot.curves["curve"]
    object.__setattr__(c, "interpolation", "cubic_spline")  # the snapshot itself refuses to be BUILT with another tag: force it
    try:
        with pytest.raises(ConfigError, match="honours only 'log_linear_df'"):
            A.ZetaPricer(sp)
    finally:
        object.__setattr__(c, "interpolation", "log_linear_df")


def test_several_curves_need_a_name_and_an_unknown_name_lists_what_there_is():
    s = snap().snapshot
    c = s.curves["curve"]
    two = SnapshotPricer(MarketSnapshot(s.ts, REF, {"curve": c, "other": CurveSnapshot("other", REF, c.node_dates, c.values)}, s.fixings, None, s.calendars))
    with pytest.raises(ConfigError, match="several curves"):
        A.ZetaPricer(two)
    assert A.ZetaPricer(two, curve="other").curve_name == "other"
    with pytest.raises(ConfigError, match=r"no curve 'nope'; it has \['curve', 'other'\]"):
        A.ZetaPricer(two, curve="nope")


def test_a_weekmask_zeta_cannot_express_is_refused():
    s = snap().snapshot
    weird = MarketSnapshot(s.ts, REF, s.curves, s.fixings, None, {"cal": CalendarData("cal", HOL, "Sun Mon Tue Wed Thu")})
    with pytest.raises(ConfigError, match="knows only Saturday and Sunday"):
        A.ZetaPricer(SnapshotPricer(weird))


def _start_of(p):
    (r,) = p.price(p.market_id(), p.reference_date, [{"product": "OIS", "leg_fixed": "PAY", "notional_mm": 1.0, "start": {"spot_lag": 2}, "end": {"tenor_years": 1}, "fixed_rate_bp": "PAR"}], ["NPV"])
    return r["resolved"]["start"]


def test_the_snapshots_holidays_are_the_calendar_zeta_sees_and_two_markets_coexist():
    """2024-05-24 is a Friday: spot is Tue 05-28 unless 05-27 (Memorial Day) is a holiday, then Wed 05-29. Two snapshots with different holidays under one name are alive at once."""
    d = dt.date(2024, 5, 24)
    with_hol, without = A.wrap(snap(d, holidays=HOL)), A.wrap(snap(d, holidays=()))
    assert _start_of(with_hol) == "2024-05-29" and _start_of(without) == "2024-05-28"
    assert _start_of(with_hol) == "2024-05-29"  # uploading the second did not replace the first
    with pytest.raises(ConfigError, match=r"conventions.calendar is 'nyse' but the snapshot's zeta market carries the calendar 'cal'"):
        A.swap.factory(with_hol, with_hol.ts, terms={"side": "pay", "direction": 1, "maturity": "5Y", "notional": 1e7}, conventions={**A.USD_SOFR_OIS_CONVENTIONS, "calendar": "nyse"})


def test_pickle_and_deepcopy_keep_the_digest_and_drop_the_market_id_so_it_is_uploaded_again_lazily():
    p = A.wrap(snap())
    mid = p.market_id()
    assert mid.encode() not in pickle.dumps(p), "the memo (market ids, price answers) is a cache of THIS client: it is not pickled"
    for clone in (pickle.loads(pickle.dumps(p)), copy.deepcopy(p)):
        assert clone.digest == p.digest and clone._memo == {} and clone.need_market() == p.need_market()
        assert clone.market_id() and mid  # a market id belongs to the client that uploaded it: nothing travels, the clone uploads its own on first use
    assert C.UPLOADS["snapshot"] == 3  # the original, and one lazy upload per clone that asked


def test_a_snapshot_is_uploaded_lazily_and_ONCE_whatever_is_asked_of_it(own_client):
    p = A.wrap(snap())
    assert own_client.stats["markets_uploaded"] == 0, "wrap uploads nothing: a snapshot that is never priced costs nothing"
    ids = {p.market_id() for _ in range(5)}
    assert len(ids) == 1 and own_client.stats["markets_uploaded"] == 1 and C.UPLOADS == {"snapshot": 1, "derived": 0}


def test_marketdata_wraps_each_snapshot_once_and_zeta_holds_one_market_per_snapshot(own_client):
    n, clock = [], Clock()
    md = MarketData({"primary": Binding(FunctionMDP(lambda ts, req: snap(ts.date())), {}, lambda x: n.append(1) or A.wrap(x))}, clock)
    clock.advance(pd.Timestamp("2024-06-20 17:00", tz="America/New_York"))
    t = pd.Timestamp("2024-06-12 17:00", tz="America/New_York")
    p = md.pricer(t)
    assert p is md.pricer(t, request={"k": 1}) and len(n) == 1
    for terms in ({"maturity": "5Y"}, {"maturity": "10Y"}, {"maturity": "2Y", "fixed_rate": 3.0}):  # three trades built and every measure asked: still ONE market on the service
        b = A.swap.factory(p, p.ts, terms={"side": "pay", "direction": 1, "notional": 1e7, **terms}, conventions={**A.USD_SOFR_OIS_CONVENTIONS, "calendar": "cal"})
        ctx = type("Ctx", (), {"pricer": p, "prev_pricer": None, "ts": p.ts, "cache": {}})()
        for m in ("rate", "dv01", "gamma", "delta_ladder"):
            getattr(b.obj, m)(ctx=ctx)
    assert own_client.stats["markets_uploaded"] == 1


def test_a_real_backtest_uploads_one_market_per_snapshot_and_a_pair_of_snapshots_adds_only_its_derived_worlds(own_client):
    """The proof the playbook asks for, on the tie-out base (4 positions, 19 grid points), layers OFF then ON. `client.stats["markets_uploaded"]` is the sum of the two counters the adapter keeps."""
    off_cfg = api.load(BASE)
    off_cfg["backtest"]["attribution"] = {"layers": []}
    b = api.build(off_cfg, stack=[api.load(OVERLAY)])
    b.run()
    n_snapshots = b.market.n_wraps
    assert n_snapshots == 19 and b.market.n_fetches >= n_snapshots
    assert own_client.stats["markets_uploaded"] == n_snapshots == C.UPLOADS["snapshot"] and C.UPLOADS["derived"] == 0, (own_client.stats, C.UPLOADS)
    assert own_client.stats["requests"] < 19 * 3 * 4, "a handful of price() requests per position and snapshot, all on the same market"  # the bound is 3.0 per position-point (228 over 76); 2.3 measured (178)


def test_with_the_layers_on_the_snapshots_are_still_uploaded_once_and_each_derived_world_once(own_client):
    b = api.build(BASE, stack=[OVERLAY])
    b.run()
    n = b.market.n_wraps
    assert C.UPLOADS["snapshot"] == n == 19
    # per (previous, current) pair: ONE rolled world (the resampled base is the same market: the node grids agree) and four shocked worlds (h and h/2, up and down), and ONE cash world only when t1 is more than
    # the payment lag (2 business days) after t0: never at cadence eod (the base), so 5 x (n - 1) is exact HERE; at every_n:5 the same base uploads 5 x 4 + 4 = 24 derived worlds (cost-budget.md, section 5)
    assert C.UPLOADS["derived"] == 5 * (n - 1), C.UPLOADS
    assert own_client.stats["markets_uploaded"] == C.UPLOADS["snapshot"] + C.UPLOADS["derived"]


# ---------------------------------------------------------------------------------- errors: zeta answers a dict, `_compat` turns it into pricebt's
def _ok_trade(**kw):
    return {"product": "OIS", "leg_fixed": "PAY", "notional_mm": 1.0, "start": {"spot_lag": 2}, "end": {"tenor_years": 1}, "fixed_rate_bp": "PAR", **kw}


def test_a_missing_fixing_is_market_data_unavailable_and_names_the_date():
    sp = snap()
    s = sp.snapshot
    f = s.fixings["fixings"]
    holed = FixingsSeries("fixings", f.dates[:-5], f.values[:-5], "percent")  # the last five fixings are missing
    p = A.wrap(SnapshotPricer(MarketSnapshot(s.ts, REF, s.curves, {"fixings": holed}, None, s.calendars)))
    with pytest.raises(MarketDataUnavailable, match=r"Z530.*no SOFR fixing dated"):
        p.price(p.market_id(), REF, [_ok_trade(start=(REF - dt.timedelta(days=30)).isoformat(), fixed_rate_bp=400.0)], ["NPV"])


def test_a_bad_field_and_an_unsupported_tenor_are_config_errors_with_zetas_code():
    p = A.wrap(snap())
    with pytest.raises(ConfigError, match=r"CFG-ZETA.*Z101"):
        p.price(p.market_id(), REF, [_ok_trade(leg_fixed="pay")], ["NPV"])  # lower case: Z101
    with pytest.raises(ConfigError, match=r"CFG-ZETA.*Z204"):
        p.price(p.market_id(), REF, [_ok_trade(end={"tenor_weeks": 6})], ["NPV"])
    with pytest.raises(ConfigError, match="whole months or years"):
        A.swap.factory(p, p.ts, terms={"side": "pay", "direction": 1, "maturity": "6W", "notional": 1e7}, conventions={**A.USD_SOFR_OIS_CONVENTIONS, "calendar": "cal"})


def test_a_closed_client_and_an_unknown_market_are_method_call_errors(own_client):
    p = A.wrap(snap())
    mid = p.market_id()
    with pytest.raises(MethodCallError, match="Z412"):
        p.price("mkt-9999", REF, [_ok_trade()], ["NPV"])
    own_client.close()
    with pytest.raises(MethodCallError, match="Z412"):
        p.price(mid, REF, [_ok_trade()], ["NPV"])
    with pytest.raises(MethodCallError, match="zeta client is closed"):
        A.wrap(snap(dt.date(2024, 6, 13))).market_id()


def test_a_date_object_is_never_sent_to_zeta():
    assert C.iso(dt.date(2024, 6, 12)) == "2024-06-12" and C.iso(pd.Timestamp("2024-06-12 17:00", tz="America/New_York")) == "2024-06-12" and C.iso("2024-06-12T00:00") == "2024-06-12"
