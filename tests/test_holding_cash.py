"""pricebt DEV-E22, holding cash (docs/v2/BOND_DESIGN.md section 4): for a position whose asset maps
`FinancingToDate`, GenericEngine books, on each date it marks or exits the position, the `Cashflows`
rows dropped since the previous mark plus the change of `FinancingToDate`, as cash; an asset that
does not map it books nothing (gs parity). Engine mechanics only, on a synthetic ConfigInstrument
whose every number is a closed form this file recomputes (the bond toy's economics are in
tests/test_toylib_bond.py).
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path

import pytest

from pricebt.backtests.actions import AddTradeAction
from pricebt.backtests.backtest_objects import ConstantCashAccrualModel, PnlDefinition
from pricebt.backtests.generic_engine import GenericEngine
from pricebt.backtests.strategy import Strategy
from pricebt.backtests.triggers import DateTrigger, DateTriggerRequirements
from pricebt.instrument import ConfigInstrument
from pricebt.risk import FinancingToDate
from pricebt.session import PricebtSession

pytestmark = pytest.mark.core

ASSETS = Path(__file__).parent / "assets"
START, EXIT, END = date(2024, 3, 4), date(2024, 4, 3), date(2024, 4, 12)  # a Monday; EXIT a Wednesday (a flow day)
FACE = 1_000_000.0
EPOCH = date(2024, 1, 1)
STEP_RATE, FLOW, FUND = 0.001, 0.002, 0.0003  # price drift per day, flow per Wednesday, funding per calendar day

_CODE = """
import datetime as dt
import pandas as pd
EPOCH = dt.date(2024, 1, 1)
def price(t, face):
    return face * (1.0 + 0.001 * (t - EPOCH).days)
def flows(t, face):
    rows, d = [], t + dt.timedelta(days=1)
    while len(rows) < 6:
        if d.weekday() == 2:
            rows.append((d, face * 0.002, "USD", "Coupon"))
        d += dt.timedelta(days=1)
    return pd.DataFrame(rows, columns=["payment_date", "payment_amount", "currency", "payment_type"])
def financing(t, start, face):
    return -face * 0.0003 * (t - start).days
"""


def _asset(name="carry_note", financed=True):
    risk_measures = {"Price": "npv", "Cashflows": "cf"}
    if financed:
        risk_measures["FinancingToDate"] = "fin"
    return {
        "schema_version": 1, "asset": name, "instrument": "ConfigInstrument", "currency": "USD",
        "code": _CODE, "market": {"expr": "pricebt_date"},
        "resolve": {"expr": '{"face": float(kwargs["face"]), "start": pricebt_date}'},
        "functions": {
            "npv": {"expr": 'price(pricebt_date, resolved["face"])', "unit": "ccy"},
            "cf": {"expr": 'flows(pricebt_date, resolved["face"])', "unit": "ccy", "returns": "frame", "scale_columns": ["payment_amount"]},
            "fin": {"expr": 'financing(pricebt_date, resolved["start"], resolved["face"])', "unit": "ccy"},
        },
        "risk_measures": risk_measures,
    }


# ------------------------------------------------------------------------------------ closed forms (independent)


def _price(t, q=1.0):
    return q * FACE * (1.0 + STEP_RATE * (t - EPOCH).days)


def _flows_in(a, b, q=1.0):
    """Flows paid in (a, b]: one per Wednesday."""
    return q * FACE * FLOW * sum(1 for k in range(1, (b - a).days + 1) if (a + timedelta(days=k)).weekday() == 2)


def _financing(a, b, start=START, q=1.0):
    return -q * FACE * FUND * ((b - start).days - (a - start).days)


def _run(asset=None, frequency="1b", q=1.0, result_ccy=None, pnl=False, cash_accrual=None, fx=None):
    PricebtSession.use(assets=[asset or _asset()], fx=fx)
    note = ConfigInstrument(pricebt_asset=(asset or _asset())["asset"], face=FACE, name="note", quantity_=q)
    trigger = DateTrigger(DateTriggerRequirements(dates=[START]), actions=AddTradeAction(note, trade_duration=EXIT, name="add"))
    strategy = Strategy(initial_portfolio=None, triggers=[trigger], cash_accrual=cash_accrual)
    return GenericEngine().run_backtest(strategy, start=START, end=END, frequency=frequency, show_progress=False, result_ccy=result_ccy,
                                        # with pnl, a second measure keeps results[d][inst] a multi-measure result
                                        risks=[FinancingToDate] if pnl else None, pnl_explain=PnlDefinition(attributes=[]) if pnl else None)


def _total(bt, d):
    return float(bt.result_summary["Total"].loc[d])


# ------------------------------------------------------------------------------------ tests


def test_financed_total_is_pv_change_plus_flows_plus_financing():
    bt = _run()
    want = _price(EXIT) - _price(START) + _flows_in(START, EXIT) + _financing(START, EXIT)
    assert _total(bt, EXIT) - _total(bt, START) == pytest.approx(want, rel=1e-12)
    assert _total(bt, END) == pytest.approx(_total(bt, EXIT), rel=1e-12)  # flat after the exit: nothing more is booked


def test_each_mark_books_exactly_what_the_position_paid_since_the_previous_one():
    bt = _run()
    dates = sorted(bt.holding_cash)
    assert dates[0] > START and dates[-1] == EXIT  # nothing on the entry date; the exit date is booked
    prev = START
    for d in dates:
        ((name, (ccy, cash, financing)),) = bt.holding_cash[d].items()
        assert ccy == "USD" and name.startswith("add")
        assert cash == pytest.approx(_flows_in(prev, d), abs=1e-9), d
        assert financing == pytest.approx(_financing(prev, d), abs=1e-9), d
        prev = d
    # and the cash column moved by exactly that on a date with no payment
    mid = dates[len(dates) // 2]
    before = max(k for k in bt.cash_dict if k < mid)
    assert sum(bt.cash_dict[mid].values()) - sum(bt.cash_dict[before].values()) == pytest.approx(sum(bt.holding_cash[mid][n][1] + bt.holding_cash[mid][n][2] for n in bt.holding_cash[mid]))


def test_the_booked_total_does_not_depend_on_the_grid():
    daily, weekly = _run(frequency="1b"), _run(frequency="1w")
    assert len(weekly.holding_cash) < len(daily.holding_cash)
    for bt in (daily, weekly):
        booked = sum(c + f for day in bt.holding_cash.values() for _ccy, c, f in day.values())
        assert booked == pytest.approx(_flows_in(START, EXIT) + _financing(START, EXIT), rel=1e-12)
        assert _total(bt, EXIT) == pytest.approx(_price(EXIT) - _price(START) + _flows_in(START, EXIT) + _financing(START, EXIT), rel=1e-12)


def test_an_unfinanced_asset_books_nothing_gs_parity():
    bt = _run(asset=_asset("plain_note", financed=False))
    assert dict(bt.holding_cash) == {}
    assert _total(bt, EXIT) - _total(bt, START) == pytest.approx(_price(EXIT) - _price(START), rel=1e-12)  # coupons not booked (gs)


def test_maps_is_the_opt_in():
    session = PricebtSession.use(assets=[_asset(), _asset("plain_note", financed=False)])
    assert session.pricing.maps(ConfigInstrument(pricebt_asset="carry_note"), FinancingToDate)
    assert not session.pricing.maps(ConfigInstrument(pricebt_asset="plain_note"), FinancingToDate)


def test_quantity_scales_the_holding_cash():
    one, scaled = _run(), _run(q=2.5)
    for d, day in one.holding_cash.items():
        ((_n, (_c, cash, fin)),) = day.items()
        ((_n2, (_c2, cash2, fin2)),) = scaled.holding_cash[d].items()
        assert (cash2, fin2) == pytest.approx((2.5 * cash, 2.5 * fin))


def test_pnl_explain_table_economic_pnl_sums_to_the_total_change():
    bt = _run(pnl=True)
    table = bt.pnl_explain_table()
    assert list(table.columns) == ["actual_pnl", "cashflow_pnl", "financing_pnl", "economic_pnl", "explained_pnl", "residual_pnl"]
    assert table["economic_pnl"].sum() == pytest.approx(_total(bt, EXIT) - _total(bt, START), rel=1e-12)
    assert table["financing_pnl"].sum() == pytest.approx(_financing(START, EXIT), rel=1e-12)
    assert table["cashflow_pnl"].sum() == pytest.approx(_flows_in(START, EXIT), rel=1e-12)
    # financing is explained (known cash), so the residual is the price change plus the flows
    assert (table["explained_pnl"] == table["financing_pnl"]).all()
    assert (table["residual_pnl"] - table["actual_pnl"] - table["cashflow_pnl"]).abs().max() < 1e-6


def test_result_ccy_converts_each_booking_at_its_date():
    bt = _run(result_ccy="EUR", fx=ASSETS / "toy_fx.yaml")

    def usd_eur(d):  # tests/assets/toy_fx.yaml: EURUSD = 1.10 + 0.01 sin(2 pi n / 252), n business days since 2020-01-01
        n = sum(1 for k in range((d - date(2020, 1, 1)).days) if (date(2020, 1, 1) + timedelta(days=k)).weekday() < 5)
        return 1.0 / (1.10 + 0.01 * math.sin(2 * math.pi * n / 252))

    prev, booked = START, 0.0
    for d in sorted(bt.holding_cash):
        ((_n, (ccy, cash, fin)),) = bt.holding_cash[d].items()
        assert ccy == "EUR"
        assert cash + fin == pytest.approx((_flows_in(prev, d) + _financing(prev, d)) * usd_eur(d), rel=1e-12)
        booked += (_flows_in(prev, d) + _financing(prev, d)) * usd_eur(d)
        prev = d
    want = _price(EXIT) * usd_eur(EXIT) - _price(START) * usd_eur(START) + booked
    assert _total(bt, EXIT) == pytest.approx(want, rel=1e-12)


def test_a_cash_accrual_model_with_a_financed_position_warns():
    with pytest.warns(UserWarning, match="counted twice"):
        _run(cash_accrual=ConstantCashAccrualModel(0.01))
