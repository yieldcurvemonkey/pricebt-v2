"""Tests for pricebt.risk.results / pricebt.risk.transform (IMPLEMENTATION_PLAN.md P1.3, DESIGN.md
section 8.2), on the smallest duck-typed stand-ins `PortfolioRiskResult` needs:

- an "instrument" is a `pricebt.base.Priceable` with a `.name` (identity/equality is Python's default);
- a "portfolio" needs `.priceables` (its direct children, in order -- what the futures align
  against), `.all_instruments` (its leaves, used by `__add__`'s overlap check), `.name`, a
  `(priceables, name=None)` constructor (`subset`) and `__add__` (two different portfolios).

Nested portfolios, gs parity of indexing/iteration/to_frame/aggregate and the risk.core helpers are
in tests/test_results_parity.py.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

import pricebt.risk as risk
from pricebt.base import Priceable
from pricebt.risk.core import aggregate_risk
from pricebt.risk.results import (
    DataFrameWithInfo,
    ErrorValue,
    FloatWithInfo,
    LazyFuture,
    MultipleRiskMeasureResult,
    PortfolioRiskResult,
    PricingFuture,
    RiskKey,
    SeriesWithInfo,
    make_bucketed_frame,
)
from pricebt.risk.transform import ResultWithInfoAggregator, Transformer

pytestmark = pytest.mark.core


# ------------------------------------------------------------------------------- duck-typed stand-ins
class FakeInstrument(Priceable):
    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return f"FakeInstrument({self.name!r})"


class FakePortfolio:
    def __init__(self, instruments, name=None):
        self.priceables = list(instruments)
        self.name = name

    @property
    def all_instruments(self):
        out = []
        for c in self.priceables:
            out.extend(c.all_instruments if isinstance(c, FakePortfolio) else [c])
        return out

    def __add__(self, other):
        return FakePortfolio(self.priceables + other.priceables)


def _usd(value):
    return FloatWithInfo(value, unit={"USD": 1})


@pytest.fixture
def three_instrument_result():
    insts = [FakeInstrument("a"), FakeInstrument("b"), FakeInstrument("c")]
    portfolio = FakePortfolio(insts)
    values = [_usd(1.0), _usd(2.0), _usd(3.0)]
    r = PortfolioRiskResult(portfolio, (risk.Price,), [PricingFuture(v) for v in values])
    return r, insts, values


# ------------------------------------------------------------------------------- RiskKey
def test_risk_key_fields():
    k = RiskKey(provider=None, date=dt.date(2024, 1, 2), market=None, params=None, scenario=None, risk_measure=risk.Price)
    assert k.date == dt.date(2024, 1, 2)
    assert k.risk_measure is risk.Price
    assert k.provider is None and k.market is None and k.params is None and k.scenario is None


# ------------------------------------------------------------------------------- FloatWithInfo
def test_float_with_info_add_and_repr():
    a = _usd(1500.0)
    assert repr(a) == "1500.0 (USD)"
    assert (a + _usd(500.0)) == 2000.0
    assert isinstance(a + _usd(500.0), FloatWithInfo)


def test_float_with_info_unit_mismatch_raises():
    with pytest.raises(ValueError, match="unit mismatch"):
        _usd(1.0) + FloatWithInfo(2.0, unit={"EUR": 1})


def test_error_value_raw_value_is_none():
    e = ErrorValue(risk_key=None, error=RuntimeError("boom"))
    assert e.raw_value is None
    assert e.error.args == ("boom",)


# ------------------------------------------------------------------------------- bucketed frame shape (DEV-R5)
def test_bucketed_frame_has_all_six_columns_and_empty_string_labels():
    frame = make_bucketed_frame({"1y;10y": 5.0, "30y": -2.0}, labels={"mkt_type": "IR", "mkt_asset": "USD"})
    assert list(frame.columns) == ["mkt_type", "mkt_asset", "mkt_class", "mkt_point", "mkt_quoting_style", "value"]
    # missing labels (mkt_class, mkt_quoting_style) are '', never NaN
    assert frame["mkt_class"].tolist() == ["", ""]
    assert frame["mkt_quoting_style"].tolist() == ["", ""]
    assert not frame["mkt_class"].isna().any()
    # mkt_point carries the config's own ';'-joined key verbatim, never parsed
    assert frame["mkt_point"].tolist() == ["1y;10y", "30y"]
    # order kept: the dict's insertion order, not sorted
    assert frame["value"].tolist() == [5.0, -2.0]


def test_bucketed_frame_empty_still_has_six_columns():
    frame = make_bucketed_frame({})
    assert list(frame.columns) == ["mkt_type", "mkt_asset", "mkt_class", "mkt_point", "mkt_quoting_style", "value"]
    assert len(frame) == 0


def test_aggregate_risk_sums_and_keeps_first_appearance_order():
    f1 = make_bucketed_frame({"5y": 1.0, "10y": 2.0}, labels={"mkt_type": "IR", "mkt_asset": "USD"})
    f2 = make_bucketed_frame({"10y": 0.5, "5y": 0.25}, labels={"mkt_type": "IR", "mkt_asset": "USD"})
    combined = aggregate_risk([f1, f2])
    assert combined["mkt_point"].tolist() == ["5y", "10y"]  # first-appearance order, from f1
    assert combined["value"].tolist() == [1.25, 2.5]


# ------------------------------------------------------------------------------- PortfolioRiskResult basics
def test_len_bool_iter(three_instrument_result):
    r, insts, values = three_instrument_result
    assert len(r) == 3
    assert bool(r) is True
    assert list(r) == values
    empty = PortfolioRiskResult(FakePortfolio([]), (risk.Price,), [])
    assert len(empty) == 0
    assert bool(empty) is False


def test_getitem_by_name_and_instrument(three_instrument_result):
    r, insts, values = three_instrument_result
    assert r["a"] == values[0]
    assert r[insts[1]] == values[1]
    assert r[0] == values[0]
    with pytest.raises(KeyError):
        r["missing"]


def test_getitem_by_measure(three_instrument_result):
    r, insts, values = three_instrument_result
    assert r[risk.Price] is r  # exactly one computed measure -> self
    with pytest.raises(ValueError, match="not computed"):
        r[risk.IRDelta]


def test_getitem_by_date_on_non_historical_raises(three_instrument_result):
    r, insts, values = three_instrument_result
    with pytest.raises(RuntimeError, match="historical"):
        r[dt.date(2024, 1, 2)]


def test_rebuild_after_deleting_an_index(three_instrument_result):
    r, insts, values = three_instrument_result
    all_insts = list(r.portfolio.all_instruments)
    futures = list(r.futures)
    del all_insts[1]
    del futures[1]
    r2 = PortfolioRiskResult(FakePortfolio(all_insts), r.risk_measures, futures)
    assert len(r2) == 2
    assert r2[insts[0].name] == values[0]
    assert r2[insts[2].name] == values[2]
    with pytest.raises(KeyError):
        r2[insts[1].name]


def test_get_default_on_missing_instrument(three_instrument_result):
    r, insts, values = three_instrument_result
    assert r.get(risk.Price, {}).get("missing", {}) == {}


def test_get_default_on_uncomputed_measure(three_instrument_result):
    r, insts, values = three_instrument_result
    assert r.get(risk.IRDelta, "d") == "d"


def test_contains(three_instrument_result):
    r, insts, values = three_instrument_result
    assert "a" in r
    assert "missing" not in r
    assert risk.Price in r
    assert risk.IRDelta not in r


# ------------------------------------------------------------------------------- __add__
def test_add_overlap_raises(three_instrument_result):
    r, insts, values = three_instrument_result
    with pytest.raises(ValueError, match="overlap"):
        r + r


def test_add_disjoint_measures_keeps_order_not_deduped_as_set(three_instrument_result):
    r, insts, values = three_instrument_result
    portfolio = r.portfolio
    r_delta = PortfolioRiskResult(portfolio, (risk.IRDeltaParallel,), [PricingFuture(_usd(9.0)) for _ in insts])
    added = r + r_delta
    assert added.risk_measures == (risk.Price, risk.IRDeltaParallel)  # order kept, self's first
    assert added["a"] == {risk.Price: values[0], risk.IRDeltaParallel: _usd(9.0)}
    # adding the other way round: the union is still ordered, not a set
    added_rev = r_delta + r
    assert added_rev.risk_measures == (risk.IRDeltaParallel, risk.Price)


def test_add_with_int_and_type_error_on_non_prr(three_instrument_result):
    r, insts, values = three_instrument_result
    with pytest.raises(ValueError):
        r + 1


# ------------------------------------------------------------------------------- transform / aggregate
def test_transform_none_returns_self(three_instrument_result):
    r, insts, values = three_instrument_result
    assert r.transform(None) is r


def test_transform_then_aggregate_gives_a_float(three_instrument_result):
    r, insts, values = three_instrument_result
    transformed = r.transform(ResultWithInfoAggregator())
    aggregated = transformed.aggregate()
    assert isinstance(aggregated, float)
    assert aggregated == 6.0


def test_aggregate_scalar_sum(three_instrument_result):
    r, insts, values = three_instrument_result
    agg = r.aggregate()
    assert isinstance(agg, FloatWithInfo)
    assert agg == 6.0


def test_aggregate_unit_mismatch_raises():
    portfolio = FakePortfolio([FakeInstrument("a"), FakeInstrument("b")])
    r = PortfolioRiskResult(
        portfolio,
        (risk.Price,),
        [PricingFuture(_usd(1.0)), PricingFuture(FloatWithInfo(2.0, unit={"EUR": 1}))],
    )
    with pytest.raises(ValueError, match="Cannot aggregate results with different units for"):
        r.aggregate()


def test_result_with_info_aggregator_returns_a_list():
    agg = ResultWithInfoAggregator()
    out = agg.apply([_usd(1.0), _usd(2.0)])
    assert isinstance(out, list)
    assert out == [1.0, 2.0]


def test_result_with_info_aggregator_sums_a_bucketed_frame():
    frame = make_bucketed_frame({"5y": 1.0, "10y": 2.0}, unit={"USD": 1})
    out = ResultWithInfoAggregator().apply([frame])
    assert out == [3.0]
    assert isinstance(out[0], FloatWithInfo)
    assert out[0].unit == {"USD": 1}


def test_transformer_apply_is_abstract():
    with pytest.raises(NotImplementedError):
        Transformer().apply([])


# ------------------------------------------------------------------------------- to_frame
def test_to_frame_sums_buckets_per_instrument():
    portfolio = FakePortfolio([FakeInstrument("a"), FakeInstrument("b")])
    frame_a = make_bucketed_frame({"5y": 1.0, "10y": 2.0})
    frame_b = make_bucketed_frame({"5y": 0.5})
    r = PortfolioRiskResult(portfolio, (risk.IRDeltaParallel,), [PricingFuture(frame_a), PricingFuture(frame_b)])
    out = r.to_frame(values="value", index="instrument_name", columns="risk_measure")
    assert out.loc["a", risk.IRDeltaParallel] == 3.0
    assert out.loc["b", risk.IRDeltaParallel] == 0.5


def test_to_frame_scalar(three_instrument_result):
    r, insts, values = three_instrument_result
    out = r.to_frame(values="value", index="instrument_name", columns="risk_measure")
    assert out.loc["a", risk.Price] == 1.0
    assert out.loc["c", risk.Price] == 3.0


# ------------------------------------------------------------------------------- LazyFuture / group aggregation
class FakeService:
    def __init__(self):
        self.calls = []

    def group_aggregate(self, group_key, members):
        self.calls.append((group_key, tuple(members)))
        total = sum(m[1] for m in members)
        return make_bucketed_frame({"10y": total})


def _lazy(service, group_key, quantity):
    return LazyFuture(
        thunk=lambda: make_bucketed_frame({"10y": quantity}),
        group_key=group_key,
        member=("id", quantity),
        service=service,
    )


def test_lazy_future_memoises_and_is_always_done():
    calls = []

    def thunk():
        calls.append(1)
        return 42.0

    f = LazyFuture(thunk=thunk, group_key="k", member="m", service=None)
    assert f.done() is True
    assert f.result() == 42.0
    assert f.result() == 42.0
    assert calls == [1]  # thunk ran exactly once


def test_group_aggregate_called_once_per_group():
    service = FakeService()
    group_key = ("usd_asset", "mkey", dt.date(2024, 1, 2), None, "delta_ladder", "USD")
    portfolio = FakePortfolio([FakeInstrument("x"), FakeInstrument("y")])
    futures = [_lazy(service, group_key, 2.5), _lazy(service, group_key, -0.7)]
    r = PortfolioRiskResult(portfolio, (risk.IRDelta,), futures)

    agg = r.aggregate()

    assert len(service.calls) == 1
    called_key, called_members = service.calls[0]
    assert called_key == group_key
    assert sorted(m[1] for m in called_members) == sorted([2.5, -0.7])
    assert agg["value"].tolist() == [pytest.approx(1.8)]


def test_group_aggregate_equals_sum_of_per_trade_ladders():
    # linearity: aggregating via the group path must equal summing the per-trade thunks directly.
    service = FakeService()
    group_key = ("usd_asset", "mkey", dt.date(2024, 1, 2), None, "delta_ladder", "USD")
    quantities = [2.5, -0.7]
    portfolio = FakePortfolio([FakeInstrument("x"), FakeInstrument("y")])
    futures = [_lazy(service, group_key, q) for q in quantities]
    r = PortfolioRiskResult(portfolio, (risk.IRDelta,), futures)

    per_trade_sum = sum(f.result()["value"].sum() for f in futures)
    agg = r.aggregate()

    assert agg["value"].sum() == pytest.approx(per_trade_sum, rel=1e-9)


def test_group_aggregate_called_once_after_rebuild_from_futures():
    service = FakeService()
    group_key = ("usd_asset", "mkey", dt.date(2024, 1, 2), None, "delta_ladder", "USD")
    insts = [FakeInstrument("x"), FakeInstrument("y"), FakeInstrument("z")]
    portfolio = FakePortfolio(insts)
    futures = [_lazy(service, group_key, q) for q in (2.5, -0.7, 1.0)]
    r = PortfolioRiskResult(portfolio, (risk.IRDelta,), futures)

    remaining_insts = list(insts)
    remaining_futures = list(futures)
    del remaining_insts[0]
    del remaining_futures[0]
    r2 = PortfolioRiskResult(FakePortfolio(remaining_insts), r.risk_measures, remaining_futures)

    r2.aggregate()

    assert len(service.calls) == 1
    assert len(service.calls[0][1]) == 2


def test_group_aggregate_called_once_after_add():
    service = FakeService()
    group_key = ("usd_asset", "mkey", dt.date(2024, 1, 2), None, "delta_ladder", "USD")
    portfolio = FakePortfolio([FakeInstrument("x"), FakeInstrument("y")])
    delta_futures = [_lazy(service, group_key, 2.5), _lazy(service, group_key, -0.7)]
    r_delta = PortfolioRiskResult(portfolio, (risk.IRDelta,), delta_futures)
    r_price = PortfolioRiskResult(portfolio, (risk.Price,), [PricingFuture(_usd(1.0)), PricingFuture(_usd(2.0))])

    combined = r_delta + r_price

    combined[risk.IRDelta].aggregate()

    assert len(service.calls) == 1
    assert len(service.calls[0][1]) == 2
