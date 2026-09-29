"""Table (frame) results and per-row buckets (docs/v2/IR_RISK_DESIGN.md sections 0 R2-14/R2-15 and 3.3):
`make_table_frame` and the `pricebt_table` marker, the pricing layer's `returns: frame` path (quantity
scales only `scale_columns`, no FX, required columns checked by measure name, historical results
stacked by date), and `make_bucketed_frame`'s list-of-rows form through the lazy, group and
bucket-sum paths."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from pricebt.errors import ConfigError
from pricebt.instrument import ConfigInstrument
from pricebt.markets import HistoricalPricingContext, PricingContext
from pricebt.markets.portfolio import Portfolio
from pricebt.risk import Cashflows, IRDelta, Price, RiskMeasure
from pricebt.risk.results import DataFrameWithInfo, MultipleRiskMeasureResult, SeriesWithInfo, make_bucketed_frame, make_table_frame
from pricebt.session import PricebtSession

ASSETS = Path(__file__).parent / "assets"
D = date(2024, 3, 4)
SIX = ["mkt_type", "mkt_asset", "mkt_class", "mkt_point", "mkt_quoting_style", "value"]
FLOWS = "[{'payment_date': pricebt_date, 'payment_amount': 100.0, 'currency': 'USD', 'payment_type': 'Fixed', 'rate': 0.05}, {'payment_date': pricebt_date, 'payment_amount': 50.0, 'currency': 'USD', 'payment_type': 'Fixed', 'rate': 0.05}]"
FLOWS_DF = "pd.DataFrame({'payment_date': [pricebt_date], 'payment_amount': [100.0], 'currency': ['USD'], 'payment_type': ['Fixed'], 'rate': [0.05]})"


def _asset(name, cashflows_fn, **functions):
    return {
        "schema_version": 1,
        "asset": name,
        "instrument": "ConfigInstrument",
        "currency": "USD",
        "imports": "import pandas as pd",
        "market": {"expr": "1"},
        "functions": {
            "price_fn": {"expr": "1.0", "unit": "ccy"},
            "flows": {"expr": FLOWS, "unit": "ccy", "returns": "frame", "scale_columns": ["payment_amount"]},
            "flows_df": {"expr": FLOWS_DF, "unit": "ccy", "returns": "frame", "scale_columns": ["payment_amount"]},
            "no_flows": {"expr": "[]", "unit": "ccy", "returns": "frame", "scale_columns": ["payment_amount"]},
            # rows on even days, none on odd days (a historical run mixes both)
            "some_flows": {"expr": f"[] if pricebt_date.day % 2 else {FLOWS}", "unit": "ccy", "returns": "frame", "scale_columns": ["payment_amount"]},
            "no_currency": {"expr": "[{'payment_date': pricebt_date, 'payment_amount': 1.0, 'payment_type': 'Fixed'}]", "unit": "ccy", "returns": "frame", "scale_columns": ["payment_amount"]},
            "typo": {"expr": FLOWS, "unit": "ccy", "returns": "frame", "scale_columns": ["payment_amout"]},
            "a_number": {"expr": "1.0", "unit": "ccy", "returns": "frame", "scale_columns": ["payment_amount"]},
            # intensive (decimal: scale_with_quantity defaults to false), so its scale column never scales
            "rates_table": {"expr": "[{'point': '1y', 'rate': 0.05}]", "unit": "decimal", "returns": "frame", "scale_columns": ["rate"]},
            **functions,
        },
        "portfolio_functions": {
            "rows": {
                "expr": "[{'mkt_point': '1y', 'value': 1.0 * sum(weights)}, {'mkt_asset': 'EUR', 'mkt_point': '2y', 'value': 2.0 * sum(weights)}]",
                "unit": "ccy_per_bp",
                "returns": "buckets",
                "labels": {"mkt_type": "IR", "mkt_asset": "USD"},
            },
        },
        "risk_measures": {
            "Price": "price_fn",
            "Cashflows": cashflows_fn,
            "RatesTable": "rates_table",
            "IRDelta": {"bucketed": "rows"},
        },
    }


def _session(cashflows_fn="flows", fx=False):
    kw = {"fx": ASSETS / "toy_fx.yaml"} if fx else {}
    return PricebtSession.use(assets=[_asset("tables", cashflows_fn)], **kw)


def _inst(quantity_=1.0, name="x"):
    return ConfigInstrument(pricebt_asset="tables", name=name, quantity_=quantity_)


# ------------------------------------------------------------------------------------ make_table_frame


def test_make_table_frame_scales_only_scale_columns_and_marks_the_frame():
    rows = [{"payment_amount": 100.0, "rate": 0.05}, {"payment_amount": 50.0, "rate": 0.04}]
    df = make_table_frame(rows, unit={"USD": 1}, scale_columns=["payment_amount"], factor=2.5)
    assert isinstance(df, DataFrameWithInfo) and df.pricebt_table is True
    assert list(df["payment_amount"]) == [250.0, 125.0]
    assert list(df["rate"]) == [0.05, 0.04]
    assert df.unit == {"USD": 1}
    assert rows[0]["payment_amount"] == 100.0  # the caller's rows are untouched


def test_make_table_frame_never_mutates_a_dataframe_input():
    src = pd.DataFrame({"payment_amount": [100.0], "rate": [0.05]})
    df = make_table_frame(src, scale_columns=["payment_amount"], factor=3.0)
    assert float(df["payment_amount"].iloc[0]) == 300.0
    assert float(src["payment_amount"].iloc[0]) == 100.0


def test_table_marker_survives_slicing_and_is_false_on_bucketed_frames():
    df = make_table_frame([{"a": 1.0}, {"a": 2.0}])
    assert df.iloc[:1].pricebt_table is True
    assert df.copy().pricebt_table is True
    assert make_bucketed_frame({"1y": 1.0}).pricebt_table is False


def test_make_table_frame_rejects_non_frames_and_absent_scale_columns():
    with pytest.raises(TypeError, match="DataFrame or a list of dicts"):
        make_table_frame(1.0)
    with pytest.raises(TypeError):
        make_table_frame([1.0, 2.0])
    with pytest.raises(ValueError, match=r"scale_columns \['payment_amout'\]"):
        make_table_frame([{"payment_amount": 1.0}], scale_columns=["payment_amout"], factor=1.0)
    # no rows: nothing to scale, so an absent scale column is not an error
    assert len(make_table_frame(pd.DataFrame(columns=["currency"]), scale_columns=["payment_amount"], factor=2.0)) == 0


# ------------------------------------------------------------------------------------ make_bucketed_frame rows form


def test_bucketed_dict_form_is_unchanged():
    df = make_bucketed_frame({"1y": 1.0, ("2y", "5y"): 2.0}, labels={"mkt_type": "IR", "mkt_asset": "USD"})
    expected = pd.DataFrame(
        [["IR", "USD", "", "1y", "", 1.0], ["IR", "USD", "", "('2y', '5y')", "", 2.0]],
        columns=SIX,
    )
    pd.testing.assert_frame_equal(pd.DataFrame(df), expected)


def test_bucketed_rows_fill_missing_coordinates_from_labels():
    rows = [{"mkt_point": "1y", "value": 1.0}, {"mkt_asset": "EUR", "mkt_point": 2, "value": 2.0}]
    df = make_bucketed_frame(rows, labels={"mkt_type": "IR", "mkt_asset": "USD"})
    assert list(df.columns) == SIX
    assert list(df["mkt_asset"]) == ["USD", "EUR"]
    assert list(df["mkt_type"]) == ["IR", "IR"]
    assert list(df["mkt_point"]) == ["1y", "2"]
    assert list(df["mkt_class"]) == ["", ""]
    # the same buckets as a dict give the same frame
    same = make_bucketed_frame([{"mkt_point": "1y", "value": 1.0}], labels={"mkt_type": "IR"})
    pd.testing.assert_frame_equal(pd.DataFrame(same), pd.DataFrame(make_bucketed_frame({"1y": 1.0}, labels={"mkt_type": "IR"})))


@pytest.mark.parametrize("row", [{"mkt_point": "1y"}, {"mkt_point": "1y", "value": 1.0, "permissions": "x"}, ("1y", 1.0)])
def test_bucketed_rows_reject_unknown_keys_or_a_missing_value(row):
    with pytest.raises(ValueError, match="a bucket row must be a dict"):
        make_bucketed_frame([row])


# ------------------------------------------------------------------------------------ pricing: frames


def test_frame_measure_scales_only_scale_columns_by_quantity():
    session = _session()
    df = session.pricing.value(_inst(2.5), D, Cashflows, None)
    assert isinstance(df, DataFrameWithInfo) and df.pricebt_table is True
    assert list(df.columns) == ["payment_date", "payment_amount", "currency", "payment_type", "rate"]
    assert list(df["payment_amount"]) == [250.0, 125.0]
    assert list(df["rate"]) == [0.05, 0.05]
    assert df.unit == {"USD": 1} and df.risk_key.risk_measure == Cashflows and df.risk_key.date == D


def test_frame_scaling_never_corrupts_the_cached_raw_value():
    session = _session("flows_df")
    first = session.pricing.value(_inst(2.5), D, Cashflows, None)
    again = session.pricing.value(_inst(2.5), D, Cashflows, None)
    unit = session.pricing.value(_inst(1.0), D, Cashflows, None)
    assert list(first["payment_amount"]) == list(again["payment_amount"]) == [250.0]
    assert list(unit["payment_amount"]) == [100.0]
    (raw,) = session.pricing._unit_value_cache.values()
    assert float(raw["payment_amount"].iloc[0]) == 100.0


def test_an_empty_row_list_is_a_frame_with_the_measures_required_columns():
    df = _session("no_flows").pricing.value(_inst(2.0), D, Cashflows, None)
    assert df.pricebt_table is True and len(df) == 0
    assert list(df.columns) == ["payment_date", "payment_amount", "currency", "payment_type"]


def test_a_missing_required_column_is_a_config_error():
    with pytest.raises(ConfigError, match=r"Cashflows: frame is missing required column\(s\) \['currency'\]"):
        _session("no_currency").pricing.value(_inst(), D, Cashflows, None)


def test_a_scale_column_typo_or_a_non_frame_is_a_config_error_naming_the_function():
    with pytest.raises(ConfigError, match="function 'typo'") as info:
        _session("typo").pricing.value(_inst(), D, Cashflows, None)
    assert info.value.key == "functions.typo"
    with pytest.raises(ConfigError, match="function 'a_number'.*DataFrame or a list of dicts"):
        _session("a_number").pricing.value(_inst(), D, Cashflows, None)


def test_an_intensive_frame_is_not_scaled_and_custom_names_need_no_columns():
    df = _session().pricing.value(_inst(4.0), D, RiskMeasure(name="RatesTable"), None)
    assert df.pricebt_table is True and list(df["rate"]) == [0.05] and df.unit == {"decimal": 1}


def test_a_frame_never_converts_currency():
    # a currency-parameterised request of a frame function
    cfg = _asset("tables_price", "flows")
    cfg["risk_measures"]["Price"] = "flows"
    session = PricebtSession.use(assets=[cfg], fx=ASSETS / "toy_fx.yaml")
    inst = ConfigInstrument(pricebt_asset="tables_price", name="p")
    assert session.pricing.value(inst, D, Price, None).pricebt_table is True  # own currency: fine
    with pytest.raises(ConfigError, match="Price returns a frame \\(a table\\); it cannot be converted to EUR"):
        session.pricing.value(inst, D, Price(currency="EUR"), None)


def test_historical_frame_is_one_table_with_a_date_column_per_date():
    _session("some_flows")
    dates = [date(2024, 3, 4), date(2024, 3, 5), date(2024, 3, 6)]  # rows, none, rows
    with HistoricalPricingContext(dates=dates):
        df = _inst(2.0).calc(Cashflows).result()
    assert isinstance(df, DataFrameWithInfo) and df.pricebt_table is True
    assert list(df.columns)[:2] == ["date", "payment_date"]
    assert list(df["date"]) == [dates[0], dates[0], dates[2], dates[2]]
    assert list(df["payment_amount"]) == [200.0, 100.0, 200.0, 100.0]
    assert df.unit == {"USD": 1}

    _session("no_flows")
    with HistoricalPricingContext(dates=dates):
        empty = _inst(2.0).calc(Cashflows).result()
    assert empty.pricebt_table is True and len(empty) == 0
    assert list(empty.columns) == ["date", "payment_date", "payment_amount", "currency", "payment_type"]


def test_historical_multi_measure_keeps_series_for_numbers_and_a_table_for_frames():
    _session()
    dates = [date(2024, 3, 4), date(2024, 3, 5)]
    with HistoricalPricingContext(dates=dates):
        res = _inst().calc((Price, Cashflows)).result()
    assert isinstance(res, MultipleRiskMeasureResult)
    assert isinstance(res[Price], SeriesWithInfo) and list(res[Price].index) == dates
    assert res[Cashflows].pricebt_table is True and list(res[Cashflows]["date"]) == [dates[0], dates[0], dates[1], dates[1]]


# ------------------------------------------------------------------------------------ pricing: per-row buckets


def test_per_row_buckets_scale_value_by_quantity_on_the_lazy_path():
    df = _session().pricing.value(_inst(3.0), D, IRDelta, None).result()
    assert list(df.columns) == SIX
    assert list(df["mkt_asset"]) == ["USD", "EUR"] and list(df["mkt_type"]) == ["IR", "IR"]
    assert list(df["value"]) == [3.0, 6.0]


def test_per_row_buckets_sum_into_the_scalar_form():
    assert float(_session().pricing.value(_inst(3.0), D, IRDelta(aggregation_level="Type"), None)) == 9.0


def test_per_row_buckets_convert_value_by_fx():
    session = _session(fx=True)
    df = session.pricing.value(_inst(2.0), D, IRDelta(currency="EUR"), None).result()
    rate = session.pricing.fx("USD", "EUR", D)
    assert list(df["value"]) == pytest.approx([2.0 * rate, 4.0 * rate])
    assert list(df["mkt_asset"]) == ["USD", "EUR"]


def test_per_row_buckets_aggregate_by_coordinates_on_the_group_path():
    _session()
    with PricingContext(D):
        prr = Portfolio([_inst(1.0, "a"), _inst(3.0, "b")]).calc(IRDelta)
    agg = prr.aggregate()
    assert list(agg["mkt_asset"]) == ["USD", "EUR"]
    assert list(agg["value"]) == [4.0, 8.0]
