"""Cash accrual models (IMPLEMENTATION_PLAN.md P3.2; research/03 section 12).

get_accrued_value(current_value, to_state) takes current_value = (cash_by_ccy: dict, from_state:
date) and returns a new {ccy: value}; the formula is v * (1 + rate/(365 if annual else 1)) **
days, applied per currency (so a multi-currency cash_dict accrues each currency independently at
the model's rate).
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from pricebt.backtests.backtest_objects import (
    CashAccrualModel,
    ConstantCashAccrualModel,
    DataCashAccrualModel,
    OisFixingCashAccrualModel,
)
from pricebt.backtests.data_sources import GenericDataSource, MissingDataStrategy
from pricebt.errors import NotSupportedError

D1 = dt.date(2024, 1, 1)
D2 = dt.date(2024, 1, 11)  # 10 days later


def test_constant_cash_accrual_model_default_rate_is_zero_and_annual():
    model = ConstantCashAccrualModel()
    assert model.rate == 0
    assert model.annual is True
    assert model.class_type == "cash_accrual_model"


def test_constant_cash_accrual_model_annual_compounding_hand_computation():
    model = ConstantCashAccrualModel(rate=0.05, annual=True)
    out = model.get_accrued_value(({"USD": 1000.0}, D1), D2)
    expect = 1000.0 * (1 + 0.05 / 365) ** 10
    assert out["USD"] == pytest.approx(expect)


def test_constant_cash_accrual_model_non_annual_uses_days_not_365():
    model = ConstantCashAccrualModel(rate=0.05, annual=False)
    out = model.get_accrued_value(({"USD": 1000.0}, D1), D2)
    expect = 1000.0 * (1 + 0.05) ** 10
    assert out["USD"] == pytest.approx(expect)


def test_constant_cash_accrual_model_accrues_every_currency_independently():
    """A multi-currency cash_dict entry: each currency compounds at the SAME model rate,
    independently, over the same day count."""
    model = ConstantCashAccrualModel(rate=0.02, annual=True)
    out = model.get_accrued_value(({"USD": 1000.0, "EUR": 500.0}, D1), D2)
    assert out["USD"] == pytest.approx(1000.0 * (1 + 0.02 / 365) ** 10)
    assert out["EUR"] == pytest.approx(500.0 * (1 + 0.02 / 365) ** 10)


def test_constant_cash_accrual_model_zero_days_is_a_no_op():
    model = ConstantCashAccrualModel(rate=0.05, annual=True)
    out = model.get_accrued_value(({"USD": 1000.0}, D1), D1)
    assert out["USD"] == pytest.approx(1000.0)


# ------------------------------------------------------------------------------------- DataCashAccrualModel


def test_data_cash_accrual_model_reads_rate_from_data_source_at_from_state():
    series = pd.Series({D1: 0.03, dt.date(2024, 1, 5): 0.04})
    source = GenericDataSource(series, MissingDataStrategy.fill_forward)
    model = DataCashAccrualModel(source, annual=True)
    out = model.get_accrued_value(({"USD": 1000.0}, D1), D2)
    # the rate used is data_source.get_data(from_state=D1) == 0.03, not the later 0.04 point
    expect = 1000.0 * (1 + 0.03 / 365) ** 10
    assert out["USD"] == pytest.approx(expect)


def test_data_cash_accrual_model_defaults():
    model = DataCashAccrualModel()
    assert model.data_source is None
    assert model.annual is True
    assert model.class_type == "cash_accrual_model"


# --------------------------------------------------------------------------------- OisFixingCashAccrualModel


def test_ois_fixing_cash_accrual_model_is_a_stub():
    """DESIGN.md section 2.3: the GS OIS-fixing API is out of scope; the name must exist (it is a
    CashAccrualModel subclass, like gs's) but construction raises."""
    with pytest.raises(NotSupportedError):
        OisFixingCashAccrualModel()
    assert issubclass(OisFixingCashAccrualModel, CashAccrualModel)
