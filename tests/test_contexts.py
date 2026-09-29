"""PricingContext / HistoricalPricingContext tests (IMPLEMENTATION_PLAN.md P1.2, DESIGN.md section 6.5)."""
from __future__ import annotations

import sys
import types
from datetime import date

import pytest

from pricebt import markets as markets_module
from pricebt.datetime import business_day_offset, date_range
from pricebt.markets import HistoricalPricingContext, PricingContext


@pytest.fixture(autouse=True)
def _stack_is_balanced():
    """Local safety net (not the shared conftest fixture, which P1.2 does not own): every test must
    leave the module's context stack exactly as it found it."""
    assert markets_module._STACK == [], "a previous test leaked a context onto the stack"
    yield
    assert markets_module._STACK == [], "this test leaked a context onto the stack"


def test_pricing_context_signature_matches_design_section_6_5():
    import inspect

    assert list(inspect.signature(PricingContext).parameters) == [
        "pricing_date",
        "market_data_location",
        "is_async",
        "is_batch",
        "use_cache",
        "visible_to_gs",
        "request_priority",
        "csa_term",
        "timeout",
        "market",
        "show_progress",
        "use_server_cache",
        "market_behaviour",
        "set_parameters_only",
        "use_historical_diddles_only",
        "provider",
    ]
    assert inspect.signature(PricingContext).parameters["market_behaviour"].default == "ContraintsBased"  # gs's own spelling


def test_historical_pricing_context_signature_matches_design_section_6_5():
    import inspect

    assert list(inspect.signature(HistoricalPricingContext).parameters) == [
        "start",
        "end",
        "calendars",
        "dates",
        "is_async",
        "is_batch",
        "use_cache",
        "visible_to_gs",
        "request_priority",
        "csa_term",
        "market_data_location",
        "timeout",
        "show_progress",
        "use_server_cache",
        "provider",
    ]


def test_current_with_nothing_entered_is_a_fresh_default_context():
    ctx = PricingContext.current
    assert ctx.pricing_date == date.today()
    assert ctx.csa_term is None
    assert ctx.is_entered is False
    assert ctx.is_async is False


def test_entering_sets_current_and_exiting_restores_the_default():
    with PricingContext(pricing_date=date(2024, 3, 4), csa_term="X") as ctx:
        assert PricingContext.current is ctx
        assert PricingContext.current.pricing_date == date(2024, 3, 4)
        assert PricingContext.current.csa_term == "X"
        assert ctx.is_entered is True
    assert PricingContext.current.pricing_date == date.today()
    assert ctx.is_entered is False


def test_nested_context_inherits_unset_pricing_date_and_csa_term():
    with PricingContext(pricing_date=date(2024, 3, 4), csa_term="X"):
        with PricingContext() as inner:
            assert inner.pricing_date == date(2024, 3, 4)
            assert inner.csa_term == "X"
        with PricingContext(pricing_date=date(2024, 5, 6)) as inner2:
            assert inner2.pricing_date == date(2024, 5, 6)  # own value wins
            assert inner2.csa_term == "X"  # still inherited from the outer context


def test_is_async_defaults_false_and_reflects_its_own_value():
    assert PricingContext().is_async is False
    assert PricingContext(is_async=True).is_async is True


def test_exception_inside_the_block_still_pops_the_stack():
    with pytest.raises(RuntimeError, match="boom"):
        with PricingContext(pricing_date=date(2024, 1, 1)):
            raise RuntimeError("boom")
    assert PricingContext.current.pricing_date == date.today()


def test_market_data_location_and_other_unused_params_are_accepted_and_ignored():
    # signature parity only (DESIGN.md section 6.5); must not raise
    PricingContext(market_data_location="NYC", is_batch=True, use_cache=True, timeout=30, provider=object)


# ---------------------------------------------------------------------------- HistoricalPricingContext


def test_both_start_and_dates_raises_gs_value_error():
    with pytest.raises(ValueError, match="Must supply start or dates, not both"):
        HistoricalPricingContext(start=date(2024, 1, 1), dates=[date(2024, 1, 2)])


def test_neither_start_nor_dates_raises_gs_value_error():
    with pytest.raises(ValueError, match=r"^Must supply start or dates$"):
        HistoricalPricingContext()


def test_start_computes_dates_eagerly_at_construction_not_on_first_read():
    # a weekend `start` trips the ported gs date_range quirk (ValueError) as soon as the range
    # needs to step past it (see test_relative_date.py::test_date_range_weekend_begin_raises); gs
    # raises this from HistoricalPricingContext.__init__ itself, not on first `.date_range` read, so
    # this must raise even though `.date_range` is never touched below.
    with pytest.raises(ValueError):
        HistoricalPricingContext(start=date(2024, 6, 1))  # Saturday


def test_dates_given_are_used_exactly_as_given():
    given = [date(2024, 1, 3), date(2024, 1, 1), date(2024, 1, 2)]  # deliberately unsorted
    assert HistoricalPricingContext(dates=given).date_range == tuple(given)


def test_start_and_end_dates_are_ascending():
    ctx = HistoricalPricingContext(start=date(2024, 5, 30), end=date(2024, 6, 4))
    assert ctx.date_range == (date(2024, 5, 30), date(2024, 5, 31), date(2024, 6, 3), date(2024, 6, 4))


def test_start_as_int_is_the_last_n_business_days_descending():
    ctx = HistoricalPricingContext(start=3, end=date(2024, 6, 4))
    assert ctx.date_range == (date(2024, 6, 4), date(2024, 6, 3), date(2024, 5, 31))
    assert ctx.date_range[0] > ctx.date_range[-1]


def test_end_none_defaults_to_today_date_form():
    # a business day, not a raw calendar offset: date_range's (date, date) form raises on a
    # non-business-day `start` once it steps past it (the ported gs quirk covered in
    # test_relative_date.py::test_date_range_weekend_begin_raises), so a plain `- timedelta(10)`
    # would make this test flaky depending on which weekday it runs on.
    start = business_day_offset(date.today(), -10, roll="preceding")
    ctx = HistoricalPricingContext(start=start)
    assert ctx.date_range == tuple(date_range(start, date.today()))


def test_end_none_defaults_to_today_int_form():
    ctx = HistoricalPricingContext(start=5)
    assert ctx.date_range == tuple(date_range(5, date.today()))


def test_historical_context_is_a_pricing_context_and_can_be_entered():
    with HistoricalPricingContext(dates=[date(2024, 1, 1)], csa_term="Y") as ctx:
        assert isinstance(ctx, PricingContext)
        assert PricingContext.current is ctx
        assert PricingContext.current.csa_term == "Y"


# ---------------------------------------------------------------------------------- engine seams


def _install_fake_pricing_module(monkeypatch) -> dict:
    """A fake `pricebt.assets.pricing` (and its parent package), installed directly into
    sys.modules so `from pricebt.assets import pricing` resolves to it without importing the real
    `pricebt.assets` package -- which lets this test prove the seams' delegation is wired correctly
    regardless of whether the parallel P1.4 task has filled in assets/config.py yet."""
    calls: dict = {}

    def engine_calc(priceable, measures, fn):
        calls["engine_calc"] = (priceable, measures, fn)
        return "calc-result"

    def engine_resolve(priceable, in_place):
        calls["engine_resolve"] = (priceable, in_place)
        return "resolve-result"

    fake_pricing = types.ModuleType("pricebt.assets.pricing")
    fake_pricing.engine_calc = engine_calc
    fake_pricing.engine_resolve = engine_resolve

    fake_assets_pkg = types.ModuleType("pricebt.assets")
    fake_assets_pkg.pricing = fake_pricing

    monkeypatch.setitem(sys.modules, "pricebt.assets", fake_assets_pkg)
    monkeypatch.setitem(sys.modules, "pricebt.assets.pricing", fake_pricing)
    return calls


def test_engine_calc_seam_delegates_to_pricing_engine_calc(monkeypatch):
    calls = _install_fake_pricing_module(monkeypatch)
    from pricebt.markets import _engine_calc

    priceable, measures = object(), object()
    assert _engine_calc(priceable, measures) == "calc-result"
    assert calls["engine_calc"] == (priceable, measures, None)  # fn defaults to None

    fn = object()
    _engine_calc(priceable, measures, fn)
    assert calls["engine_calc"] == (priceable, measures, fn)


def test_engine_resolve_seam_delegates_to_pricing_engine_resolve(monkeypatch):
    calls = _install_fake_pricing_module(monkeypatch)
    from pricebt.markets import _engine_resolve

    priceable = object()
    assert _engine_resolve(priceable, True) == "resolve-result"
    assert calls["engine_resolve"] == (priceable, True)


def test_engine_seams_raise_without_a_session(monkeypatch):
    # pricebt DEV note (P2.2): before P2.2, pricebt.assets.pricing had no engine_calc/engine_resolve
    # at all, so calling a seam for real always failed with ImportError/AttributeError -- this test
    # documented that placeholder state. Now that P2.2 has filled the module in, the seams exist and
    # delegate correctly (proven above); calling one for real with no PricebtSession raises pricebt's
    # own PricebtError instead (DESIGN.md section 6.1), which this test now asserts.
    from pricebt.markets import _engine_calc, _engine_resolve
    from pricebt.errors import PricebtError
    from pricebt.session import PricebtSession

    monkeypatch.setattr(PricebtSession, "current", None)
    with pytest.raises(PricebtError):
        _engine_calc(object(), object())
    with pytest.raises(PricebtError):
        _engine_resolve(object(), True)


# ------------------------------------------------------------------ market= and the current setter (R2-30)


def test_any_market_raises_not_supported_dev_m1():
    from pricebt.errors import NotSupportedError

    with pytest.raises(NotSupportedError, match="market"):
        PricingContext(market="any market object")
    PricingContext(market=None)  # the default is fine


def test_current_setter_is_the_default_while_no_context_is_entered():
    """gs Pricing_Context tutorial: `PricingContext.current = PricingContext(...)`. The conftest
    isolation fixture restores the previous default after every test."""
    ctx = PricingContext(pricing_date=date(2024, 3, 4), csa_term="X")
    PricingContext.current = ctx
    assert PricingContext.current is ctx and not ctx.is_entered
    with PricingContext() as inner:  # an entered context inherits from the default
        assert inner.pricing_date == date(2024, 3, 4) and inner.csa_term == "X"
    assert PricingContext.current is ctx


def test_entering_the_default_context_itself_never_makes_it_its_own_ancestor():
    """`with PricingContext.current:` after assigning it (gs tutorial idiom), directly or below a
    context that inherits from it: un-set fields read without recursing."""
    ctx = PricingContext(pricing_date=date(2024, 3, 4))
    PricingContext.current = ctx
    with PricingContext.current as entered:
        assert entered is ctx and ctx.pricing_date == date(2024, 3, 4) and ctx.csa_term is None
    with PricingContext() as outer:  # inherits from the default ...
        with ctx:  # ... which is now entered below it
            assert ctx.csa_term is None and outer.csa_term is None and outer.pricing_date == date(2024, 3, 4)
    other = PricingContext(csa_term="Y")
    with other:
        with ctx:
            assert ctx.csa_term == "Y"
    with other:  # ctx kept no parent from that exit, so `other` still inherits the default's date
        assert other.pricing_date == date(2024, 3, 4)


def test_current_cannot_be_set_inside_an_entered_context():
    with PricingContext(pricing_date=date(2024, 3, 4)):
        with pytest.raises(ValueError, match="Cannot set current while in a nested context"):
            PricingContext.current = PricingContext()


def test_current_default_is_restored_by_the_isolation_fixture():
    # runs after the setter test in file order; the default is the fresh one again
    assert PricingContext.current.pricing_date == date.today()
    assert markets_module._DEFAULT is None
