"""QuantLib process-global state: the evaluation date and the fixings histories never leak into or out of an adapter call (spec A1, R4)."""
import datetime as dt

import pytest

pytest.importorskip("QuantLib")
pytestmark = pytest.mark.adapter_quantlib

import QuantLib as ql  # noqa: E402
from quantlib_support import snapshots as S  # noqa: E402

from pricebt.contrib.quantlib import USD_SOFR_OIS_CONVENTIONS, swap, wrap  # noqa: E402
from pricebt.contrib.quantlib import _compat as C  # noqa: E402
from pricebt.contrib.quantlib.swap import _World  # noqa: E402
from pricebt.errors import ConfigError, MarketDataUnavailable  # noqa: E402
from pricebt.pricable import MarkContext  # noqa: E402

HOL = S.synthetic_holidays()
FX = S.seeded_fixings(HOL, dt.date(2022, 1, 3), dt.date(2025, 12, 31))
REF = dt.date(2024, 6, 12)
KW = dict(effective=dt.date(2024, 1, 8), maturity=dt.date(2027, 1, 8), fixed_rate=4.2, notional=5e7)


def pr(ref=REF, fixings=FX, bump=0.0, hour=17):
    return wrap(S.pricer_of(S.snapshot(ref, S.synthetic_nodes(ref, bump=bump), fixings=fixings, hol=HOL, hour=hour)))


def obj(p, **kw):
    t = S.template(swap, "swap", USD_SOFR_OIS_CONVENTIONS, {"side": "pay", "maturity": "10Y", "notional": 1e7, **{**KW, **kw}})
    return t.build(p, p.ts).obj


def eval_date():
    return ql.Settings.instance().evaluationDate


@pytest.fixture(autouse=True)
def clean_state():
    C.reset()
    yield
    C.reset()


def all_operations(p, prev):
    """Every public path that sets the evaluation date or installs fixings."""
    o = obj(p)
    c = MarkContext.standalone(p, prev=prev)
    o.value(ctx=c), o.rate(ctx=c), o.dv01(ctx=c), o.gamma(ctx=c), o.delta_ladder(ctx=c), o.dv01_zero(ctx=c), o.carry(ctx=c)
    S.template(swap, "swap", USD_SOFR_OIS_CONVENTIONS, {"side": "pay", "maturity": "5Y", "notional": 1e7}).build(p, p.ts)  # `par` resolution


def test_the_evaluation_date_is_set_inside_the_guard_and_restored_after_every_operation():
    other = ql.Date(3, 3, 2031)
    ql.Settings.instance().evaluationDate = other
    try:
        p, prev = pr(), pr(dt.date(2024, 6, 10))
        with C.guard(REF) as d:
            assert d == REF and eval_date() == C.qd(REF)
        assert eval_date() == other
        all_operations(p, prev)
        assert eval_date() == other
    finally:
        ql.Settings.instance().evaluationDate = ql.Date(1, 1, 2000)


def test_the_reference_date_events_switch_is_on_inside_the_guard_and_restored_after():
    """With the default a swap whose last flow is paid TODAY is `isExpired()` and QuantLib returns 0 before any engine flag is consulted."""
    st = ql.Settings.instance()
    for before in (False, True):
        st.includeReferenceDateEvents = before
        with C.guard(REF):
            assert st.includeReferenceDateEvents is True
        assert st.includeReferenceDateEvents is before
    st.includeReferenceDateEvents = False


def test_the_evaluation_date_is_restored_when_the_call_raises():
    other = ql.Date(3, 3, 2031)
    ql.Settings.instance().evaluationDate = other
    p = pr(fixings={d: v for d, v in FX.items() if d != dt.date(2024, 3, 6)})
    o = obj(p)
    with pytest.raises(MarketDataUnavailable, match="missing"):
        o.value(ctx=MarkContext.standalone(p))
    assert eval_date() == other and not C.has_leaked_fixings()
    with pytest.raises(RuntimeError, match="boom"):
        with C.guard(REF):
            raise RuntimeError("boom")
    assert eval_date() == other and not C.has_leaked_fixings()


def test_no_fixings_history_survives_a_call():
    p, prev = pr(), pr(dt.date(2024, 6, 10))
    assert not C.has_leaked_fixings()
    all_operations(p, prev)
    assert not C.has_leaked_fixings()
    with C.guard(REF):
        idx = C.make_index(ql.WeekendsOnly(), ql.YieldTermStructureHandle(), [dt.date(2024, 6, 10)], [0.05])
        assert idx.hasHistoricalFixing(C.qd(dt.date(2024, 6, 10))) and C.has_leaked_fixings()
    assert not C.has_leaked_fixings()


def test_a_fixing_left_behind_by_a_crashed_call_cannot_poison_the_next_one():
    p = pr()
    o = obj(p)
    base = o.value(ctx=MarkContext.standalone(p)).pv
    # plant a wildly wrong fixing dated TODAY under our index name: QuantLib would use it if it were still there
    C.make_index(ql.WeekendsOnly(), ql.YieldTermStructureHandle(), [REF], [0.50])
    assert C.has_leaked_fixings()
    assert o.value(ctx=MarkContext.standalone(p)).pv == pytest.approx(base, abs=1e-9)


def test_time_moving_backwards_gives_the_numbers_of_a_fresh_process():
    """Later run first, then an earlier date with different fixings: identical to the earlier run alone."""
    early, late = dt.date(2024, 6, 12), dt.date(2025, 2, 12)
    fx_a = {d: v for d, v in FX.items()}
    fx_b = {d: v + 0.25 for d, v in FX.items()}

    def value(ref, fx):
        p = pr(ref, fixings=fx)
        return obj(p).value(ctx=MarkContext.standalone(p)).pv

    fresh = value(early, fx_a)
    value(late, fx_b)
    assert value(early, fx_a) == fresh
    value(late, fx_a)
    assert value(early, fx_b) != fresh and value(early, fx_a) == fresh


def test_other_users_of_quantlib_keep_their_fixings_and_their_evaluation_date():
    mine = ql.OvernightIndex("SOMEONEELSE", 0, ql.USDCurrency(), ql.WeekendsOnly(), ql.Actual360())
    mine.addFixings([ql.Date(3, 6, 2024)], [0.0123], True)
    all_operations(pr(), pr(dt.date(2024, 6, 10)))
    assert mine.pastFixing(ql.Date(3, 6, 2024)) == pytest.approx(0.0123, abs=1e-15)
    ql.IndexManager.instance().clearHistory(mine.name())


def test_a_live_relative_date_helper_of_someone_else_does_not_break_the_guard():
    """OISRateHelper re-initialises its dates when the evaluation date changes and raises from the notification; the guard sets and restores the date anyway."""
    cal = ql.WeekendsOnly()
    s = ql.Settings.instance()
    s.evaluationDate = ql.Date(12, 6, 2024)
    idx = ql.OvernightIndex("X", 0, ql.USDCurrency(), cal, ql.Actual360())
    h = ql.OISRateHelper(2, ql.Period("5Y"), ql.QuoteHandle(ql.SimpleQuote(0.04)), idx, ql.YieldTermStructureHandle(), False, 2, ql.ModifiedFollowing, ql.Annual, cal, ql.Period(0, ql.Days), 0.0,
                         pillar=ql.Pillar.CustomDate, customPillarDate=ql.Date(14, 6, 2029))
    curve = ql.PiecewiseLogLinearDiscount(ql.Date(12, 6, 2024), [h], ql.Actual360())
    with pytest.raises(RuntimeError):  # proof that the observer really does raise on a date change (the premise of the tolerance)
        s.evaluationDate = ql.Date(2, 1, 2030)
    s.evaluationDate = ql.Date(12, 6, 2024)
    assert curve is not None
    with C.guard(dt.date(2030, 1, 2)):
        assert eval_date() == ql.Date(2, 1, 2030)
    assert eval_date() == ql.Date(12, 6, 2024)


def test_the_adapter_leaves_no_evaluation_date_observer_behind():
    """Risk-curve bootstrap objects observe the date: if the adapter kept one alive the next date change would raise."""
    p = pr()
    o = obj(p)
    o.delta_ladder(ctx=MarkContext.standalone(p))
    s = ql.Settings.instance()
    old = s.evaluationDate
    s.evaluationDate = ql.Date(2, 1, 2030)  # would raise from a surviving OISRateHelper (pillar date before the new earliest date)
    s.evaluationDate = old


def test_a_curve_anchored_at_another_date_cannot_price_at_the_evaluation_date():
    p = pr()
    o = obj(p)
    other = pr(dt.date(2024, 6, 13)).ql_curve()
    with C.guard(REF):
        with pytest.raises(ConfigError, match="cannot price at") as e:
            _World(o, p, other, REF)
    assert e.value.code == "CFG-CURVE-DATE"
