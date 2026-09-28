"""measure_series (IMPLEMENTATION_PLAN.md P4.3; DESIGN.md section 10): the fill rules (ffill drops
LEADING missing dates rather than back-filling, an interior gap forward-fills; fill=None drops every
missing point), the unit/missing_dates attrs, and a RiskMeasure vs a string function name both
resolving to the same value.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import toylib.rates as tr
from pricebt.data import measure_series
from pricebt.errors import ConfigError, NotSupportedError
from pricebt.instrument import IRSwap
from pricebt.risk import IRDelta, IRFwdRate, Price
from pricebt.session import PricebtSession

ASSETS = Path(__file__).parent / "assets"

START = date(2024, 1, 2)  # Tuesday
END = date(2024, 1, 10)  # Wednesday -- business days: 2,3,4,5,8,9,10


def _session():
    return PricebtSession.use(assets=[ASSETS / "toy_usd_irs.yaml"])


def _swap(name="s"):
    return IRSwap(pay_or_receive="Pay", termination_date="10y", notional_currency="USD", notional_amount=1_000_000, name=name)


# --------------------------------------------------------------------------------- fill rules


def test_ffill_drops_leading_missing_dates_rather_than_backfilling():
    _session()
    tr.HOLES.add(START)  # the FIRST schedule date has no market
    s = measure_series(_swap(), "par_rate", START, END, frequency="1b")
    assert START not in s.index
    assert list(s.index) == sorted(s.index)
    assert s.index[0] == date(2024, 1, 3)  # the next business day, not back-filled from anywhere
    assert s.attrs["missing_dates"] == [START]


def test_ffill_forward_fills_an_interior_gap():
    hole = date(2024, 1, 5)  # an interior schedule date, not the first or the last
    _session()
    tr.HOLES.add(hole)
    s = measure_series(_swap(), "par_rate", START, END, frequency="1b")
    assert hole in s.index

    # Independently derive the expected forward-filled value: the raw par_rate of the SAME fresh,
    # quantity-1 instrument on the previous available date (2024-01-04), read straight off the
    # PricingService -- not measure_series's own fill logic, and not a magic constant.
    prev_date = date(2024, 1, 4)
    session = PricebtSession.current
    expected = session.pricing.unit_value(_swap().clone(quantity_=1.0), prev_date, "par_rate", None)
    assert s[hole] == pytest.approx(expected)
    assert s[prev_date] == pytest.approx(expected)  # the source value itself is unaffected by the fill


def test_fill_none_drops_every_missing_point():
    hole = date(2024, 1, 5)
    _session()
    tr.HOLES.add(hole)
    s = measure_series(_swap(), "par_rate", START, END, frequency="1b", fill=None)
    assert hole not in s.index
    assert s.attrs["missing_dates"] == [hole]
    # every other schedule date still has a market, so nothing else is dropped
    assert len(s) == 6


# --------------------------------------------------------------------------------- unit / missing_dates attrs


def test_unit_and_missing_dates_attrs_for_a_function_name():
    _session()
    s = measure_series(_swap(), "par_rate", START, END, frequency="1b")
    assert s.attrs["unit"] == "bp"  # toy_usd_irs.yaml: functions.par_rate.unit
    assert s.attrs["missing_dates"] == []


def test_unknown_function_name_raises_config_error():
    _session()
    with pytest.raises(ConfigError, match="no function named 'not_a_function'"):
        measure_series(_swap(), "not_a_function", START, END, frequency="1b")


def test_unsupported_fill_value_raises():
    _session()
    with pytest.raises(ValueError, match="fill must be"):
        measure_series(_swap(), "par_rate", START, END, frequency="1b", fill="bfill")


# --------------------------------------------------------------------------------- string vs RiskMeasure


def test_risk_measure_and_function_name_agree():
    """IRFwdRate maps to `par_rate` in toy_usd_irs.yaml's risk_measures:, is unparameterised (no
    currency conversion applies) and 'bp' is not an extensive unit (not scaled by quantity), so both
    routes must produce identical series -- this exercises the section 8.1 RiskMeasure -> function
    mapping from inside measure_series, not just PricingService directly."""
    _session()
    by_name = measure_series(_swap(), "par_rate", START, END, frequency="1b")
    _session()  # a fresh PricingService, so the second call shares no cache with the first
    by_measure = measure_series(_swap(), IRFwdRate, START, END, frequency="1b")

    assert by_measure.attrs["unit"] == "bp"
    assert by_measure.attrs["missing_dates"] == []
    assert list(by_measure.index) == list(by_name.index)
    for d in by_name.index:
        assert by_measure[d] == pytest.approx(by_name[d])


def test_currency_denominated_risk_measure_reports_the_resolved_currency_as_unit():
    """toy_usd_irs.yaml maps Price -> npv, unit 'ccy'; measure_series documents that a
    currency-denominated RiskMeasure reports the resolved currency code, not the raw 'ccy' label
    (matching FloatWithInfo's own convention elsewhere in pricebt)."""
    _session()
    s = measure_series(_swap(), Price, START, END, frequency="1b")
    assert s.attrs["unit"] == "USD"


def test_currency_denominated_function_name_reports_the_resolved_currency_as_unit():
    """toy_usd_irs.yaml: functions.npv has unit 'ccy' and no explicit currency, so it falls back to
    the asset's own currency (USD). The function-name route must resolve that the same way the
    RiskMeasure route does (via `PricingService`'s `_unit_dict` rule) rather than reporting the raw
    'ccy' label verbatim -- 'npv' (function name) and `Price` (RiskMeasure) are the SAME underlying
    function in this asset config, so they must report the same unit."""
    _session()
    s = measure_series(_swap(), "npv", START, END, frequency="1b")
    assert s.attrs["unit"] == "USD"


def test_resolved_instrument_input_is_rejected():
    _session()
    resolved = PricebtSession.current.pricing.resolve(_swap(), START, None)
    with pytest.raises(ValueError, match="must be UNRESOLVED"):
        measure_series(resolved, "par_rate", START, END, frequency="1b")


def test_bucketed_risk_measure_raises_not_supported():
    _session()
    with pytest.raises(NotSupportedError, match="bucketed"):
        measure_series(_swap(), IRDelta, START, END, frequency="1b")  # no aggregation_level -> bucketed


def test_quantity_of_the_input_instrument_is_ignored():
    """DESIGN.md section 10 step 2: measure_series always evaluates for quantity 1, regardless of
    what quantity_ the caller's instrument carries. IRDelta(aggregation_level='Type') maps to the
    extensive-unit `dv01` function, which DOES scale by quantity_ inside PricingService.value -- the
    RiskMeasure route is what would leak a non-1 quantity_ if measure_series forgot to force it (the
    plain function-name route never scales by quantity_ at all, so it wouldn't catch this)."""
    _session()
    risk = IRDelta(aggregation_level="Type")
    q1 = measure_series(IRSwap("Pay", "10y", "USD", 1_000_000, name="s", quantity_=1.0), risk, START, END, frequency="1b")
    _session()  # a fresh PricingService, so the second call shares no cache with the first
    q5 = measure_series(IRSwap("Pay", "10y", "USD", 1_000_000, name="s", quantity_=5.0), risk, START, END, frequency="1b")
    for d in q1.index:
        assert q5[d] == pytest.approx(q1[d])
