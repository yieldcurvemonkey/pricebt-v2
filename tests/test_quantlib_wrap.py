"""wrap(SnapshotPricer) -> QLPricer: curve, calendars from holiday sets, fixings units, lookups, immutability, memoisation."""
import datetime as dt
import math
import pickle

import pytest

pytest.importorskip("QuantLib")
pytestmark = pytest.mark.adapter_quantlib

import QuantLib as ql  # noqa: E402
from quantlib_support import snapshots as S  # noqa: E402

from pricebt.contrib.quantlib import USD_SOFR_OIS_CONVENTIONS, swap, wrap  # noqa: E402
from pricebt.contrib.quantlib import _compat as C  # noqa: E402
from pricebt.contrib.quantlib.wrap import QLPricer  # noqa: E402
from pricebt.errors import ConfigError, MarketDataUnavailable  # noqa: E402
from pricebt.pricable import MarkContext  # noqa: E402
from pricebt.snapshot import SnapshotPricer  # noqa: E402

REF = dt.date(2024, 6, 12)
HOL = S.synthetic_holidays()


def pricer(**kw):
    return wrap(S.pricer_of(S.snapshot(REF, hol=HOL, **kw)))


# ------------------------------------------------------------------ the curve
def test_snapshot_round_trip_discount_factors_come_back_within_1e_14():
    dates, dfs = S.synthetic_nodes(REF)
    p = pricer(nodes=(dates, dfs))
    c = p.ql_curve()
    assert c.referenceDate() == C.qd(REF) and c.maxDate() == C.qd(dates[-1])
    assert max(abs(c.discount(C.qd(d)) - v) for d, v in zip(dates, dfs)) < 1e-14


def test_interpolation_is_log_linear_in_the_discount_factor_and_extends_the_last_forward():
    dates, dfs = S.synthetic_nodes(REF)
    c = pricer(nodes=(dates, dfs)).ql_curve()
    a, b = 4, 5  # 1Y .. 2Y nodes
    t = 0.3
    days = (dates[b] - dates[a]).days
    d = dates[a] + dt.timedelta(days=int(days * t))
    w = (d - dates[a]).days / days
    want = math.exp((1 - w) * math.log(dfs[a]) + w * math.log(dfs[b]))
    assert abs(c.discount(C.qd(d)) - want) < 1e-14
    # beyond the last node: the last segment's forward continues (log-linear extrapolation)
    last = (dates[-1] - dates[-2]).days
    f = math.log(dfs[-2] / dfs[-1]) / last
    d = dates[-1] + dt.timedelta(days=1000)
    assert abs(c.discount(C.qd(d)) - dfs[-1] * math.exp(-f * 1000)) < 1e-13


def test_only_the_log_linear_snapshot_tag_is_honoured():
    snap = S.snapshot(REF, hol=HOL)
    object.__setattr__(snap.curves["sofr"], "interpolation", "linear_zero")
    with pytest.raises(ConfigError, match="log_linear_df") as e:
        wrap(SnapshotPricer(snap))
    assert e.value.code == "CFG-INTERPOLATION"
    snap = S.snapshot(REF, hol=HOL)
    object.__setattr__(snap.curves["sofr"], "value_kind", "zero_rate")
    with pytest.raises(ConfigError, match="discount factors"):
        wrap(SnapshotPricer(snap))


def test_several_curves_need_a_name_and_an_unknown_name_is_an_error():
    a = S.curve_from_nodes(*S.synthetic_nodes(REF), name="a")
    b = S.curve_from_nodes(*S.synthetic_nodes(REF, level=0.05), name="b")
    snap = S.MarketSnapshot(ts=S.stamp(REF), reference_date=REF, curves={"a": a, "b": b}, calendars={"nyc": S.calendar_data("nyc", HOL)})
    with pytest.raises(ConfigError, match="name one"):
        wrap(SnapshotPricer(snap))
    with pytest.raises(ConfigError, match="no curve 'zzz'"):
        wrap(SnapshotPricer(snap), curve="zzz")
    assert wrap(SnapshotPricer(snap), curve="b").curve_name == "b"
    assert wrap(SnapshotPricer(snap), curve="b").ql_curve().discount(C.qd(a.node_dates[5])) == pytest.approx(b.values[5], abs=1e-14)


def test_wrap_needs_a_snapshot_pricer_and_is_idempotent():
    with pytest.raises(ConfigError, match="SnapshotPricer"):
        wrap(object())
    p = pricer()
    assert wrap(p) is p


# ------------------------------------------------------------------ calendars from the holiday set
def test_calendar_is_built_from_the_snapshot_holidays_and_agrees_with_plain_python_over_ten_years():
    p = pricer()
    cal = p.calendar("nyc")
    hs = set(HOL)
    d, bad = dt.date(2018, 1, 1), 0
    while d < dt.date(2028, 1, 1):
        bad += cal.isBusinessDay(C.qd(d)) != (d.weekday() < 5 and d not in hs)
        d += dt.timedelta(days=1)
    assert bad == 0


def test_a_custom_holiday_is_a_holiday_and_does_not_leak_into_any_named_calendar():
    odd = dt.date(2030, 3, 5)  # a Tuesday that no real calendar closes
    assert odd.weekday() == 1 and ql.UnitedStates(ql.UnitedStates.SOFR).isBusinessDay(C.qd(odd)) and ql.WeekendsOnly().isBusinessDay(C.qd(odd))
    snap = S.snapshot(REF, calendars={"nyc": S.calendar_data("nyc", HOL + [odd])})
    cal = wrap(SnapshotPricer(snap)).calendar("nyc")
    assert not cal.isBusinessDay(C.qd(odd))
    assert ql.WeekendsOnly().isBusinessDay(C.qd(odd)) and ql.UnitedStates(ql.UnitedStates.SOFR).isBusinessDay(C.qd(odd)) and ql.NullCalendar().isBusinessDay(C.qd(odd))
    # a second calendar of the SAME name with other holidays is independent
    other = C.calendar_from_holidays("nyc", [], "Mon Tue Wed Thu Fri")
    assert other.isBusinessDay(C.qd(odd)) and not cal.isBusinessDay(C.qd(odd))


def test_weekends_come_from_the_weekmask_not_from_a_hardcoded_saturday_sunday():
    cal = C.calendar_from_holidays("four_day", [], "Mon Tue Wed Thu")
    fri, sat, mon = dt.date(2024, 6, 14), dt.date(2024, 6, 15), dt.date(2024, 6, 17)
    assert not cal.isBusinessDay(C.qd(fri)) and not cal.isBusinessDay(C.qd(sat)) and cal.isBusinessDay(C.qd(mon))
    assert C.calendar_from_holidays("std", [], "Mon Tue Wed Thu Fri").isBusinessDay(C.qd(fri))
    with pytest.raises(ConfigError, match="weekmask"):
        C.calendar_from_holidays("bad", [], "Mon Foo")


def test_an_unknown_calendar_name_lists_the_snapshot_calendars():
    with pytest.raises(ConfigError, match="no calendar 'lon'") as e:
        pricer().calendar("lon")
    assert "nyc" in str(e.value)


def test_holidays_outside_the_quantlib_date_range_are_ignored_not_fatal():
    cal = C.calendar_from_holidays("wide", [dt.date(1970, 1, 1), dt.date(2200, 12, 25), dt.date(2030, 3, 5)], "Mon Tue Wed Thu Fri")
    assert not cal.isBusinessDay(C.qd(dt.date(2030, 3, 5)))


# ------------------------------------------------------------------ fixings units
def test_fixings_are_converted_to_decimal_whatever_the_snapshot_unit():
    fx = {dt.date(2024, 6, 10): 4.30, dt.date(2024, 6, 11): 4.31}
    pct = pricer(fixings=fx, fixings_unit="percent").fixings_decimal()
    dec = pricer(fixings={d: v / 100 for d, v in fx.items()}, fixings_unit="decimal").fixings_decimal()
    assert pct[0] == dec[0] == (dt.date(2024, 6, 10), dt.date(2024, 6, 11))
    assert pct[1] == pytest.approx((0.0430, 0.0431), abs=1e-15) and dec[1] == pytest.approx(pct[1], abs=1e-15)
    assert pricer().fixings_decimal() is None


def test_repo_proxy_returns_percent_whatever_the_snapshot_unit():
    fx = {dt.date(2024, 6, 7): 5.30, dt.date(2024, 6, 10): 5.31}
    a = pricer(fixings=fx, fixings_unit="percent").fixings_percent_before(REF)
    b = pricer(fixings={d: v / 100 for d, v in fx.items()}, fixings_unit="decimal").fixings_percent_before(REF)
    assert a == (dt.date(2024, 6, 10), 5.31) and b[0] == a[0] and b[1] == pytest.approx(5.31, abs=1e-12)
    assert pricer(fixings=fx).fixings_percent_before(dt.date(2024, 6, 7)) is None


# ------------------------------------------------------------------ bond reference data
def test_security_lookup_resolves_aliases_and_reports_unknowns():
    q = S.quote_set(REF, [("AAA000001", 4.0, dt.date(2020, 1, 15), dt.date(2030, 1, 15))], {"AAA000001": {"ytm": 4.2}}, aliases={"CT10": "AAA000001"})
    p = wrap(S.pricer_of(S.snapshot(REF, nodes=False, hol=HOL, quotes=q)))
    assert p.security("CT10").id == "AAA000001" and p.security("AAA000001").coupon == 4.0 and p.quote("AAA000001").ytm == 4.2
    with pytest.raises(MarketDataUnavailable, match="unknown security"):
        p.security("CT2")
    with pytest.raises(MarketDataUnavailable, match="no quote"):
        p.quote("BBB000002")
    with pytest.raises(MarketDataUnavailable, match="no bond quotes"):
        pricer().security("CT10")


# ------------------------------------------------------------------ immutability, pickling, memoisation
def test_the_wrapped_pricer_is_immutable_and_picklable_and_keeps_its_digest():
    p = pricer()
    with pytest.raises(AttributeError, match="immutable"):
        p.reference_date = dt.date(2020, 1, 1)
    p.ql_curve()  # build a QuantLib object (not picklable) into the private cache
    q = pickle.loads(pickle.dumps(p))
    assert isinstance(q, QLPricer) and q.digest == p.digest and q.reference_date == p.reference_date
    assert q.ql_curve().discount(C.qd(dt.date(2030, 6, 12))) == p.ql_curve().discount(C.qd(dt.date(2030, 6, 12)))


def test_derived_products_are_memoised_on_the_wrapped_pricer_not_on_the_snapshot():
    snap = S.snapshot(REF, hol=HOL)
    before = dict(vars(snap))
    p = wrap(SnapshotPricer(snap))
    t = S.template(swap, "swap", USD_SOFR_OIS_CONVENTIONS, {"side": "pay", "maturity": "5Y", "notional": 1e7})
    ctx = MarkContext.standalone(p)
    a = t.build(p, p.ts).obj
    b = S.template(swap, "swap", USD_SOFR_OIS_CONVENTIONS, {"side": "receive", "maturity": "7Y", "notional": 2e7}).build(p, p.ts).obj
    from unittest import mock

    from pricebt.contrib.quantlib import _risk

    with mock.patch.object(_risk, "build_risk_curves", wraps=_risk.build_risk_curves) as built:
        a.delta_ladder(ctx=ctx)
        b.delta_ladder(ctx=MarkContext.standalone(p))
        a.gamma(ctx=ctx)
        assert built.call_count == 1  # one bootstrap for two swaps and three measures on the same snapshot and pillar set
    assert len([k for k in p._lazy if k[0] == "risk"]) == 1
    assert dict(vars(snap)) == before and not hasattr(snap, "_lazy")
    other = wrap(SnapshotPricer(snap))  # another wrapper of the same snapshot owns its own memo
    assert not [k for k in other._lazy if k[0] == "risk"]
