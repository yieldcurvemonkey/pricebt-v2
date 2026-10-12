"""`pricebt.data.DataContext`: the port of gs `gs_quant.data.DataContext` (the date window data
requests default to), and `measure_series` reading it (docs/v2/MARKET_CONTEXT_DESIGN.md section 6).
"""
from __future__ import annotations

import datetime as dt
import inspect
from datetime import date
from pathlib import Path

import pytest

import pricebt.data as data_module
from pricebt.data import DataContext, measure_series
from pricebt.instrument import IRSwap, IRSwaption
from pricebt.risk import IRDelta, IRVega
from pricebt.session import PricebtSession
from test_market_provider import MARKETS, on_provider

ASSETS = Path(__file__).parent / "assets"
UTC = dt.timezone.utc


@pytest.fixture(autouse=True)
def _stack_must_be_empty_after_every_test():
    yield
    assert data_module._DATA_STACK == [], "this test leaked a DataContext onto the stack"


# ------------------------------------------------------------------------------------ the gs port


def test_signature_is_gs():
    assert list(inspect.signature(DataContext).parameters) == ["start", "end", "interval"]


def test_default_window_is_the_last_30_days_and_the_last_day():
    ctx = DataContext()
    today = date.today()
    assert (ctx.start_date, ctx.end_date, ctx.interval) == (today - dt.timedelta(days=30), today, None)
    now = dt.datetime.now(UTC)
    assert ctx.end_time.tzinfo is UTC and abs(ctx.end_time - now) < dt.timedelta(seconds=5)
    assert abs(ctx.start_time - (now - dt.timedelta(days=1))) < dt.timedelta(seconds=5)


def test_dates_datetimes_and_strings_are_all_accepted():
    """gs's own `__main__` examples."""
    dc = DataContext(date(2019, 1, 1), dt.datetime(2019, 2, 1, tzinfo=UTC))
    assert (dc.start_date, dc.end_date) == (date(2019, 1, 1), date(2019, 2, 1))
    assert (dc.start_time, dc.end_time) == (dt.datetime(2019, 1, 1, tzinfo=UTC), dt.datetime(2019, 2, 1, tzinfo=UTC))
    dc2 = DataContext(None, "2019-01-01T00:00:00Z")
    assert dc2.end_date == date(2019, 1, 1) and dc2.end_time == dt.datetime(2019, 1, 1, tzinfo=UTC)
    assert dc2.start_date == date.today() - dt.timedelta(days=30)
    assert DataContext("2019-03-05").start_date == date(2019, 3, 5)


@pytest.mark.parametrize("bad", [5, 1.5, ["2019-01-01"]])
def test_an_invalid_start_raises_gs_value_error(bad):
    with pytest.raises(ValueError, match="is not a valid date"):
        DataContext(bad).start_date
    with pytest.raises(ValueError, match="is not a valid date"):
        DataContext(end=bad).end_time


def test_interval_is_validated_as_in_gs():
    assert DataContext(interval="1m").interval == "1m" and DataContext(interval="999d").interval == "999d"
    with pytest.raises(TypeError, match="interval must be a str"):
        DataContext(interval=5)
    for bad in ("0m", "m", "1000d", "1M", "1 m", ""):
        with pytest.raises(ValueError, match="interval must be a valid str"):
            DataContext(interval=bad)


def test_current_is_the_innermost_entered_context_and_nothing_is_inherited():
    assert DataContext.current.start_date == date.today() - dt.timedelta(days=30)  # a fresh default
    with DataContext(date(2024, 1, 2), date(2024, 3, 29)) as outer:
        assert DataContext.current is outer and outer.is_entered
        with DataContext(end=date(2024, 2, 1)) as inner:
            assert DataContext.current is inner
            assert inner.start_date == date.today() - dt.timedelta(days=30)  # gs: no inheritance of start
        assert DataContext.current is outer
    assert not outer.is_entered and DataContext.current is not outer


def test_current_can_be_assigned_while_nothing_is_entered():
    ctx = DataContext(date(2024, 1, 2), date(2024, 1, 10))
    DataContext.current = ctx
    assert DataContext.current is ctx and not ctx.is_entered
    with DataContext(date(2023, 1, 2)) as entered:
        assert DataContext.current is entered
        with pytest.raises(ValueError, match="Cannot set current while in a nested context DataContext"):
            DataContext.current = DataContext()
    assert DataContext.current is ctx


def test_the_assigned_default_is_restored_by_the_isolation_fixture():
    assert data_module._DATA_DEFAULT is None  # runs after the test above in file order


def test_an_exception_inside_the_block_still_pops_the_stack():
    with pytest.raises(RuntimeError, match="boom"):
        with DataContext(date(2024, 1, 2)):
            raise RuntimeError("boom")


# ------------------------------------------------------------------------------------ measure_series


def _swap():
    return IRSwap(pay_or_receive="Pay", termination_date="10y", notional_currency="USD", notional_amount=1_000_000, name="s")


def test_measure_series_takes_its_window_from_the_current_data_context():
    PricebtSession.use(assets=[ASSETS / "toy_usd_irs.yaml"])
    explicit = measure_series(_swap(), "par_rate", date(2024, 1, 2), date(2024, 1, 10))
    with DataContext(date(2024, 1, 2), date(2024, 1, 10)):
        windowed = measure_series(_swap(), "par_rate")
        shorter = measure_series(_swap(), "par_rate", end=date(2024, 1, 4))  # an explicit bound wins
    assert list(windowed.index) == list(explicit.index) and list(windowed) == list(explicit)
    assert len(windowed) == 7 and list(shorter.index) == [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
    DataContext.current = DataContext("2024-01-02", "2024-01-10")
    assert list(measure_series(_swap(), "par_rate").index) == list(explicit.index)


def test_greek_series_of_provider_backed_assets_share_one_window():
    """dv01 of a rolling par swap and vega of a rolling ATM swaption, on market providers."""
    PricebtSession.use(markets=[MARKETS], assets=[on_provider("toy_usd_irs.yaml", "toy_usd_ois"), on_provider("toy_usd_swaption.yaml", "toy_usd_swaption_vol")])
    option = IRSwaption("Pay", "10y", "USD", expiration_date="1y", strike="ATM", notional_amount=1_000_000, buy_sell="Buy")
    with DataContext(date(2024, 1, 2), date(2024, 1, 31)):
        dv01 = measure_series(_swap(), IRDelta(aggregation_level="Type"))
        vega = measure_series(option, IRVega(aggregation_level="Type"))
    assert len(dv01) == 22 and list(dv01.index) == list(vega.index)
    assert (dv01 > 0).all() and (vega > 0).all() and dv01.attrs["unit"] == "USD" and vega.attrs["unit"] == "USD"
