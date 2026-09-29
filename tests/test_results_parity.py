"""gs parity of the result objects and risk.core helpers (IR_RISK_DESIGN.md sections 1.8 and 5
items 1-6, 9-13, 15; research note R10 sections 2 and 5). Results are built by hand on real
`Portfolio`/`IRSwap` objects (never priced), with the values gs's own probes used in R10: the
nested book `Portfolio([EUR(5y=1, 10y=2), USD(5y=3, 10y=4), 7y=5])`.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

import pricebt.config
from pricebt.config import DisplayOptions
from pricebt.errors import NotSupportedError
from pricebt.instrument import IRSwap
from pricebt.markets.portfolio import Portfolio
from pricebt.risk import (
    DictWithInfo,
    DollarPrice,
    ErrorValue,
    IRDelta,
    Price,
    StringWithInfo,
    UnsupportedValue,
    aggregate_results,
    aggregate_risk,
    combine_risk_key,
    sort_risk,
    subtract_risk,
)
from pricebt.risk.results import (
    DataFrameWithInfo,
    FloatWithInfo,
    LazyFuture,
    MultipleRiskMeasureResult,
    PortfolioPath,
    PortfolioRiskResult,
    PricingFuture,
    RiskKey,
    SeriesWithInfo,
    make_bucketed_frame,
    make_table_frame,
)
from pricebt.risk.transform import GenericResultWithInfoTransformer, ResultWithInfoAggregator

pytestmark = pytest.mark.core

D0, D1 = dt.date(2024, 1, 2), dt.date(2024, 1, 3)
USD = {"USD": 1}


def _key(d=D0, m=Price):
    return RiskKey(None, d, None, None, None, m)


def _v(x, d=D0, m=Price, unit=USD):
    return FloatWithInfo(x, risk_key=_key(d, m), unit=unit)


def _swap(name, tenor="5y", notional=1_000_000):
    return IRSwap("Pay", tenor, "USD", notional, name=name)


def _ladder(points, d=D0, m=IRDelta):
    return make_bucketed_frame(points, labels={"mkt_type": "IR"}, risk_key=_key(d, m), unit=USD)


def _prr(portfolio, values, measures=(Price,)):
    """A PRR over `portfolio` whose futures follow its tree: a nested list for a sub-portfolio."""
    futures = []
    for child, v in zip(portfolio.priceables, values):
        futures.append(PricingFuture(_prr(child, v, measures) if isinstance(child, Portfolio) else v))
    return PortfolioRiskResult(portfolio, measures, futures)


@pytest.fixture
def nested():
    # the USD legs share the EUR legs' names (as in gs's 030009) but not their terms
    s1, s2, s3, s4, s5 = _swap("5y"), _swap("10y", "10y"), _swap("5y", notional=2), _swap("10y", "10y", 2), _swap("7y", "7y")
    eur, usd = Portfolio([s1, s2], name="EUR"), Portfolio([s3, s4], name="USD")
    top = Portfolio([eur, usd, s5])
    return _prr(top, [[_v(1.0), _v(2.0)], [_v(3.0), _v(4.0)], _v(5.0)]), (s1, s2, s3, s4, s5)


# ================================================================================ PortfolioPath
def test_portfolio_path_walks_portfolios_results_and_tuples(nested):
    prr, (s1, s2, s3, s4, s5) = nested
    p = PortfolioPath((1, 1))
    assert p(prr.portfolio) is s4 and p(prr).result() == 4.0 and p(prr.futures).result() == 4.0
    assert PortfolioPath(1) + PortfolioPath(1) == p and hash(p) == hash(PortfolioPath((1, 1)))
    assert repr(p) == "(1, 1)" and len(p) == 2 and list(p) == [1, 1] and p.path == (1, 1)
    deep = Portfolio([Portfolio([Portfolio([s1], name="L2")], name="L1")])
    renamed = PortfolioPath((0, 0))(deep, rename_to_parent=True)
    assert renamed.name == "L1" and deep.priceables[0].priceables[0].name == "L2"  # a renamed copy


# ================================================================================ PRR indexing / iteration
def test_iter_is_leaves_in_all_paths_order_len_is_direct_children(nested):
    prr, _ = nested
    assert list(prr) == [5.0, 1.0, 2.0, 3.0, 4.0]  # gs VERIFIED order (R10 section 2.2)
    assert len(prr) == 3
    assert repr(prr) == "(Price,) Results (3)" and repr(prr[0]) == "(Price,) Results for EUR (2)"


def test_getitem_name_and_instrument_first_match_at_any_depth(nested):
    prr, (s1, s2, s3, s4, s5) = nested
    assert prr["5y"] == 1.0  # first match (EUR's), though USD has a 5y too (gs quirk kept)
    assert prr["7y"] == 5.0 and prr[s4] == 4.0 and prr[["10y"]] == 2.0
    assert prr[PortfolioPath((1, 0))] == 3.0
    assert list(prr[0]) == [1.0, 2.0]
    with pytest.raises(KeyError):
        prr["nope"]


def test_a_portfolio_of_instruments_indexes_as_the_flat_subset_of_its_children(nested):
    """gs `is_iterable(item, InstrumentBase)`: a Portfolio iterates its direct children, so one of
    instruments -- a member sub-portfolio or not -- gives the unnamed flat subset of them."""
    prr, (s1, s2, s3, s4, s5) = nested
    loose = prr[Portfolio([s1, s5])]
    assert list(loose) == [1.0, 5.0] and loose.portfolio.name is None
    member = prr[prr.portfolio.priceables[1]]  # the USD sub-portfolio
    assert list(member) == [3.0, 4.0] and member.portfolio.name is None


def test_a_resolved_instrument_finds_only_a_leaf_priced_on_its_resolution_date():
    """gs `__paths`: looked up through its unresolved form, a resolved instrument matches only a
    result in its own resolution context (pricebt: its resolution date)."""
    a = _swap("a")
    resolved = a.clone()
    resolved._set_resolution({"fixed_rate": 0.03}, RiskKey(None, D0, None, None, None, None), None, a)
    assert _prr(Portfolio([a]), [_v(2.0, D0)])[resolved] == 2.0
    with pytest.raises(KeyError, match="resolved in a different pricing context"):
        _prr(Portfolio([a]), [_v(1.0, D1)])[resolved]


def test_result_takes_gs_timeout(nested):
    prr, _ = nested
    assert prr.result(timeout=5) is prr


def test_list_of_instruments_and_slice_give_subsets(nested):
    prr, (s1, s2, s3, s4, s5) = nested
    flat = prr[[s1, s5]]
    assert list(flat) == [1.0, 5.0] and flat.portfolio.priceables == (s1, s5)
    sliced = prr[1:3]
    assert list(sliced) == [5.0, 3.0, 4.0] and len(sliced) == 2
    # pricebt DEV-R7: a single path to a sub-portfolio is that nested result, futures aligned
    assert prr.subset([PortfolioPath(0)]) is prr.futures[0].result()


def test_contains_at_any_depth(nested):
    prr, (s1, s2, s3, s4, s5) = nested
    assert "10y" in prr and s3 in prr and Price in prr
    assert "nope" not in prr and IRDelta not in prr and 3 not in prr


def test_measure_list_slices_and_not_computed():
    s = _swap("a")
    mr = MultipleRiskMeasureResult(s, {Price: _v(1.0), DollarPrice: _v(2.0), IRDelta: _ladder({"1y": 3.0})})
    prr = PortfolioRiskResult(Portfolio([s]), (Price, DollarPrice, IRDelta), [PricingFuture(mr)])
    two = prr[[Price, IRDelta]]
    assert two.risk_measures == (Price, IRDelta) and set(two["a"]) == {Price, IRDelta}
    assert prr[DollarPrice]["a"] == 2.0
    with pytest.raises(ValueError, match="not computed"):
        prr[[Price, IRDelta(aggregation_level="Type")]]


def test_transform_keeps_the_nested_tree_dev_r7(nested):
    prr, _ = nested
    t = prr.transform(ResultWithInfoAggregator())
    assert t["7y"] == 5.0  # gs's flat rebuild returned 2.0 here (R10 BUG-R2)
    assert list(t) == [5.0, 1.0, 2.0, 3.0, 4.0] and len(t) == 3
    assert t.aggregate() == 15.0
    tenfold = prr.transform(GenericResultWithInfoTransformer(lambda leaves: [x * 10 for x in leaves]))
    assert tenfold[1]["10y"] == 40.0 and len(tenfold) == 3


# ================================================================================ to_frame
def test_to_frame_raw_records_are_labelled_from_their_own_paths_dev_r6(nested):
    prr, _ = nested
    raw = prr.to_frame(None, None, None)
    assert list(raw.columns) == ["portfolio_name_0", "instrument_name", "risk_measure", "value"]
    rows = list(zip(raw["portfolio_name_0"], raw["instrument_name"], raw["value"]))
    assert rows == [("EUR", "5y", 1.0), ("EUR", "10y", 2.0), ("USD", "5y", 3.0), ("USD", "10y", 4.0), ("N/A", "7y", 5.0)]


def test_to_frame_default_pivots_and_first_appearance_order(nested):
    prr, _ = nested
    eur, usd = prr.portfolio.priceables[:2]
    homo = _prr(Portfolio([eur, usd]), [[_v(1.0), _v(2.0)], [_v(3.0), _v(4.0)]])
    df = homo.to_frame()  # 030006's heat map: portfolio_name_0 x instrument_name
    assert list(df.index) == ["EUR", "USD"] and list(df.columns) == ["5y", "10y"]  # not sorted
    assert df.loc["USD", "10y"] == 4.0
    flat = _prr(Portfolio([_swap("b"), _swap("a")]), [_v(1.0), _v(2.0)])
    assert list(flat.to_frame().index) == ["b", "a"]
    pivot = flat.to_frame(values="value", index="instrument_name", columns="risk_measure")
    assert list(pivot.index) == ["b", "a"] and list(pivot.columns) == [Price]
    unnamed = _prr(Portfolio([IRSwap("Pay", "5y", "USD", 1), IRSwap("Pay", "7y", "USD", 1)]), [_v(1.0), _v(2.0)])
    assert list(unnamed.to_frame().index) == ["Swap_0", "Swap_1"]


def test_to_frame_bucketed_is_indexed_by_labels_and_empty_frames_need_show_na():
    a, b = _swap("a"), _swap("b")
    prr = _prr(Portfolio([a, b]), [_ladder({"5y": 1.0, "10y": 2.0}), _ladder({})], measures=(IRDelta,))
    df = prr.to_frame()
    assert df.index.names == ["instrument_name", "risk_measure"] and list(df["value"]) == [1.0, 2.0]
    summed = prr.to_frame(values="value", index="instrument_name", columns="risk_measure")
    assert list(summed.index) == ["a"] and summed.loc["a", IRDelta] == 3.0  # the empty ladder is dropped
    na = prr.to_frame(values="value", index="instrument_name", columns="risk_measure", display_options=DisplayOptions(show_na=True))
    assert na.loc["b", IRDelta] == 0.0
    assert _prr(Portfolio([a]), [_ladder({})], measures=(IRDelta,)).to_frame() is None
    # gs: None reads the module default at call time; anything but a DisplayOptions is a TypeError
    pricebt.config.display_options.show_na = True
    assert prr.to_frame(values="value", index="instrument_name", columns="risk_measure").loc["b", IRDelta] == 0.0
    with pytest.raises(TypeError, match="^display_options must be of type DisplayOptions$"):
        prr.to_frame(display_options=type("D", (), {"show_na": True})())


def test_to_frame_value_pivot_drops_table_measures_dev_r11():
    a = _swap("a")
    table = make_table_frame([{"payment_date": D1, "payment_amount": 7.0}], risk_key=_key(m=IRDelta))
    mr = MultipleRiskMeasureResult(a, {Price: _v(1.0), IRDelta: table})
    prr = PortfolioRiskResult(Portfolio([a]), (Price, IRDelta), [PricingFuture(mr)])
    pivot = prr.to_frame(values="value", index="instrument_name", columns="risk_measure")
    assert list(pivot.columns) == [Price] and pivot.loc["a", Price] == 1.0
    default = prr.to_frame()  # tables are shown, indexed by the labels (gs's cashflows branch)
    assert "payment_amount" in default.columns and len(default) == 2


def test_historical_to_frame_is_dates_by_instrument():
    a, b = _swap("a"), _swap("b")
    series = [SeriesWithInfo(pd.Series({D0: 1.0, D1: 1.5}), unit=USD), SeriesWithInfo(pd.Series({D0: 2.0, D1: 2.5}), unit=USD)]
    df = _prr(Portfolio([a, b]), series).to_frame()
    assert list(df.index) == [D0, D1] and list(df.columns) == ["a", "b"] and df.loc[D1, "b"] == 2.5


# ================================================================================ __add__ / __mul__
def test_add_stitches_single_date_results_into_a_history(nested):
    a, b = _swap("a"), _swap("b")
    port = Portfolio([a, b])
    total = _prr(port, [_v(1.0, D0), _v(2.0, D0)]) + _prr(port, [_v(1.5, D1), _v(2.5, D1)])
    assert total.dates == (D0, D1)
    assert list(total["a"]) == [1.0, 1.5] and isinstance(total["a"], SeriesWithInfo)
    assert dict(total.aggregate()) == {D0: 3.0, D1: 4.0}
    assert list(total.to_frame().index) == [D0, D1]
    assert total[D1]["b"] == 2.5 and total[D1]["b"].risk_key.date == D1


def test_add_same_date_overlap_raises_and_numbers_are_rejected_dev_r8(nested):
    prr, _ = nested
    with pytest.raises(ValueError, match="overlap"):
        prr + prr
    with pytest.raises(ValueError, match="Can only add instances of PortfolioRiskResult"):
        prr + 1


def test_add_and_dates_never_evaluate_a_lazy_future_r2_26():
    calls = []

    def lazy(d, q):
        return LazyFuture(lambda: calls.append(q) or _ladder({"1y": q}, d), ("asset", "mkt", d, None, "fn", "USD", ()), ("id", q), None)

    a, b = _swap("a"), _swap("b")
    p0 = PortfolioRiskResult(Portfolio([a]), (IRDelta,), [lazy(D0, 1.0)])
    p1 = PortfolioRiskResult(Portfolio([b]), (IRDelta,), [lazy(D0, 2.0)])
    total = p0 + p1
    assert total.dates == () and calls == []
    with pytest.raises(ValueError, match="overlap"):
        p0 + PortfolioRiskResult(p0.portfolio, (IRDelta,), [lazy(D0, 3.0)])  # same date via group_key[2]
    assert calls == []


def test_add_of_different_portfolios_and_measures_never_serves_one_measure_for_another_dev_r13():
    """gs `as_multiple_result_futures`: once the sum holds two measures, a leaf answers only for its
    own; a measure it lacks is a KeyError (gs), never the other measure's value. DEV-R13: gs's
    `set_value` fill-in from the other side is not ported."""
    s1, s2, s3 = _swap("s1"), _swap("s2", "7y"), _swap("s3", "10y")
    b = _prr(Portfolio([s2]), [_v(5.0, m=DollarPrice)], (DollarPrice,))
    c = _prr(Portfolio([s1]), [_v(1.0)]) + b
    assert c.risk_measures == (Price, DollarPrice)
    assert c[DollarPrice][s2] == 5.0 and c[Price][s1] == 1.0
    for measure, lacking in ((DollarPrice, s1), (Price, s2)):
        with pytest.raises(KeyError):
            c[measure][lacking]
        with pytest.raises(KeyError):
            c[measure].aggregate(True, True)
    assert list(c[DollarPrice].to_frame(None, None, None)["instrument_name"]) == ["s2"]  # gs get_records
    frame = c.to_frame()
    assert frame.loc["s1", Price] == 1.0 and frame.loc["s2", DollarPrice] == 5.0 and pd.isna(frame.loc["s1", DollarPrice])
    # gs's Portfolio == is one-way (Portfolio([s1]) == Portfolio([s1, s2])), so gs takes its
    # same-portfolio branch, zips one future and silently drops s2; pricebt needs equality both ways
    wider = _prr(Portfolio([s1, s2]), [_v(2.0, m=DollarPrice), _v(3.0, m=DollarPrice)], (DollarPrice,))
    assert Portfolio([s1]) == wider.portfolio and not wider.portfolio == Portfolio([s1])
    kept = _prr(Portfolio([s1]), [_v(1.0)]) + wider
    assert kept[DollarPrice][s2] == 3.0 and kept[Price][s1] == 1.0
    nested = _prr(Portfolio([s1, Portfolio([s3], name="sub")]), [_v(1.0), [_v(3.0)]]) + b
    with pytest.raises(ValueError, match="not computed"):
        nested[DollarPrice]  # the nested sub-result keeps its own measures (gs)
    raw = nested.to_frame(None, None, None)
    rows = list(zip(raw["instrument_name"], raw["risk_measure"], raw["value"]))
    assert rows == [("s1", Price, 1.0), ("s3", Price, 3.0), ("s2", DollarPrice, 5.0)]
    only_price = nested[Price].to_frame(None, None, None)  # a leaf lacking Price adds no row
    assert list(zip(only_price["instrument_name"], only_price["risk_measure"])) == [("s1", Price), ("s3", Price)]


def test_mul_scales_every_leaf_and_rejects_non_numbers_dev_r9(nested):
    prr, _ = nested
    doubled = prr * 2
    assert list(doubled) == [10.0, 2.0, 4.0, 6.0, 8.0]
    assert doubled["7y"].unit == USD and doubled["7y"].risk_key == _key()
    ladder = (_prr(Portfolio([_swap("a")]), [_ladder({"1y": 3.0})], (IRDelta,)) * 2)["a"]
    assert list(ladder["value"]) == [6.0] and list(ladder["mkt_type"]) == ["IR"]  # labels untouched
    with pytest.raises(ValueError, match="Can only multiply by an int or float"):
        prr * "x"


def test_scaling_a_table_scales_its_scale_columns_only_dev_r9():
    a = _swap("a")
    table = make_table_frame([{"payment_amount": 7.0, "currency": "USD"}], risk_key=_key(m=IRDelta), scale_columns=["payment_amount"])
    alone = _prr(Portfolio([a]), [table], (IRDelta,)) * 2
    assert list(alone["a"]["payment_amount"]) == [14.0] and list(alone["a"]["currency"]) == ["USD"]
    both = PortfolioRiskResult(Portfolio([a]), (Price, IRDelta), [PricingFuture(MultipleRiskMeasureResult(a, {Price: _v(1.0), IRDelta: table}))])
    doubled = (both * 2)["a"]
    assert doubled[Price] == 2.0 and list(doubled[IRDelta]["payment_amount"]) == [14.0] and doubled[IRDelta].pricebt_table
    with pytest.raises(ValueError, match="Cannot add a number to a table"):
        both["a"] + 1  # pricebt DEV-R9: + k would shift the amounts, which means nothing
    empty = make_table_frame(pd.DataFrame(columns=["currency"]), scale_columns=["payment_amount"])
    assert (_prr(Portfolio([a]), [empty], (IRDelta,)) * 2)["a"].empty  # no scale column to scale


# ================================================================================ aggregate contract
def test_aggregate_error_contract():
    a, b = _swap("a"), _swap("b")
    port = Portfolio([a, b])
    with pytest.raises(ValueError, match="Cannot aggregate results in error"):
        _prr(port, [_v(1.0), ErrorValue(_key(), "boom")]).aggregate()
    with pytest.raises(ValueError, match="Cannot aggregate heterogeneous types"):
        _prr(port, [_v(1.0), _ladder({"1y": 1.0}, m=Price)]).aggregate()
    with pytest.raises(ValueError, match="Cannot aggregate results with different units for Price"):
        _prr(port, [_v(1.0), _v(2.0, unit={"EUR": 1})]).aggregate()
    # pricebt DEV-R17: a dimensionless `{}` unit is a unit (gs's truthiness test sums it into USD)
    with pytest.raises(ValueError, match="Cannot aggregate results with different units"):
        aggregate_results([_v(1.0, unit={}), _v(2.0)], allow_mismatch_risk_keys=True)
    two_dates = _prr(port, [_v(1.0, D0), _v(2.0, D1)])
    with pytest.raises(ValueError, match="Cannot aggregate results with different pricing keys"):
        two_dates.aggregate()
    assert two_dates.aggregate(allow_mismatch_risk_keys=True) == 3.0
    # left to right, as gs's sum() over FloatWithInfo (builtin sum() of exact floats is compensated)
    three = _prr(Portfolio([a, b, _swap("c")]), [_v(8523.279968656101), _v(8502.483551650754), _v(8481.023412404958)])
    assert repr(float(three.aggregate())) == "25506.786932711817"


def test_aggregate_group_path_applies_the_same_checks():
    class Service:
        def group_aggregate(self, key, members):
            return _ladder({"1y": float(len(members))}, key[2])

    def lazy(d):
        return LazyFuture(lambda: None, ("asset", "mkt", d, None, "fn", "USD", ()), ("id",), Service())

    prr = PortfolioRiskResult(Portfolio([_swap("a"), _swap("b")]), (IRDelta,), [lazy(D0), lazy(D1)])
    with pytest.raises(ValueError, match="different pricing keys"):
        prr.aggregate()
    assert list(prr.aggregate(allow_mismatch_risk_keys=True)["value"]) == [2.0]


def test_aggregate_historical_bucketed_and_tables():
    a, b = _swap("a"), _swap("b")
    hist_a = DataFrameWithInfo.compose([_ladder({"1y": 1.0}, D0), _ladder({"1y": 2.0}, D1)])
    hist_b = DataFrameWithInfo.compose([_ladder({"1y": 10.0}, D0), _ladder({}, D1)])  # priced on D1, no rows
    prr = _prr(Portfolio([a, b]), [hist_a, hist_b], (IRDelta,))
    agg = prr.aggregate()
    assert list(agg["dates"]) == [D0, D1] and list(agg["value"]) == [11.0, 2.0]
    assert len(prr[D1]["b"]) == 0 and isinstance(prr[D1]["b"], DataFrameWithInfo)  # R2-27: empty, not a KeyError
    assert prr[D1].transform(ResultWithInfoAggregator()).aggregate() == 2.0
    tables = [make_table_frame([{"payment_amount": x}], risk_key=_key(m=IRDelta)) for x in (1.0, 2.0)]
    rows = _prr(Portfolio([a, b]), tables, (IRDelta,)).aggregate()
    assert rows.pricebt_table is True and list(rows["instrument_name"]) == ["a", "b"] and list(rows["payment_amount"]) == [1.0, 2.0]
    # pricebt DEV-R11: identical rows of two positions stay two rows (gs's aggregate_risk groupby
    # merges them into one)
    same = [make_table_frame([{"payment_date": D1, "payment_amount": 100.0}], risk_key=_key(m=IRDelta)) for _ in (a, b)]
    twin = _prr(Portfolio([a, b]), same, (IRDelta,)).aggregate()
    assert list(twin["payment_amount"]) == [100.0, 100.0] and list(twin["instrument_name"]) == ["a", "b"]


def test_a_date_the_history_was_not_priced_on_is_a_key_error_dev_r16():
    """Only a priced date whose ladder had no rows gives the R2-27 empty frame; a date outside the
    priced set is gs's KeyError (never a silent zero), also after stitching with `+`."""
    a = _swap("a")
    hist = DataFrameWithInfo.compose([_ladder({"1y": 1.0}, D0), _ladder({}, D1)])
    prr = _prr(Portfolio([a]), [hist], (IRDelta,))
    assert prr[D1]["a"].empty and prr[D1]["a"].risk_key.date == D1
    with pytest.raises(KeyError):
        prr[dt.date(2024, 1, 9)]
    with pytest.raises(KeyError):
        prr[[D0, dt.date(2024, 1, 9)]]
    assert list(prr[[D0, D1]]["a"]["value"]) == [1.0]
    d2 = dt.date(2024, 1, 4)
    stitched = prr + _prr(Portfolio([a]), [_ladder({}, d2)], (IRDelta,))
    assert stitched[d2]["a"].empty and stitched[D0]["a"]["value"].tolist() == [1.0]
    with pytest.raises(KeyError):
        stitched[dt.date(2024, 1, 9)]


def test_date_indexing_dispatches_per_leaf_so_an_all_empty_history_still_slices():
    """gs has no up-front `dates` check: ladders empty on every date (no dates) still slice by a
    priced date. A single-date frame is gs's `.loc` on its row index: a KeyError (so `get` gives
    the default), or the frame itself when empty; a single-date scalar raises RuntimeError."""
    a = _swap("a")
    hv = _prr(Portfolio([a]), [DataFrameWithInfo.compose([_ladder({}, D0), _ladder({}, D1)])], (IRDelta,))
    assert hv.dates == ()
    assert hv[D0]["a"].empty and hv[D0]["a"].risk_key.date == D0
    single = _prr(Portfolio([a]), [_ladder({"1y": 1.0})], (IRDelta,))
    with pytest.raises(KeyError):
        single[D0]
    assert single.get(D0, "dflt") == "dflt"
    empty = _ladder({})
    assert _prr(Portfolio([a]), [empty], (IRDelta,))[D0]["a"] is empty
    with pytest.raises(RuntimeError, match="Can only index by date on historical results"):
        _prr(Portfolio([a]), [_v(1.0)])[D0]


def test_a_table_is_historical_by_its_priced_dates_never_by_a_date_column():
    a = _swap("a")
    june = dt.date(2024, 6, 1)
    own = make_table_frame([{"date": june, "amount": 5.0}], risk_key=_key(m=IRDelta))  # a single-date table
    single = _prr(Portfolio([a]), [own], (IRDelta,))
    assert single.dates == ()
    with pytest.raises(KeyError, match="single-date result"):
        single[june]
    day = [make_table_frame([{"amount": x}], risk_key=_key(d, IRDelta), scale_columns=["amount"]) for x, d in ((1.0, D0), (2.0, D1))]
    hist = _prr(Portfolio([a]), [day[0]], (IRDelta,)) + _prr(Portfolio([a]), [day[1]], (IRDelta,))
    assert hist.dates == (D0, D1) and hist[D1]["a"]["amount"].tolist() == [2.0]
    assert list(hist["a"].raw_value.columns) == ["dates", "amount"]  # records carry `dates`, like a ladder
    assert (hist * 3)["a"]["amount"].tolist() == [3.0, 6.0]


# ================================================================================ MultipleRiskMeasureResult
def test_mrmr_constructor_dates_and_date_indexing():
    a = _swap("a")
    mr = MultipleRiskMeasureResult(a, {Price: SeriesWithInfo(pd.Series({D0: 1.0, D1: 1.5}), risk_key=_key(None), unit=USD)})
    assert mr.instrument is a and mr.dates == (D0, D1)
    one = mr[D1]
    assert isinstance(one, MultipleRiskMeasureResult) and one[Price] == 1.5 and one[Price].risk_key.date == D1
    doubled = mr * 2  # pricebt DEV-R9: gs raises AttributeError on a historical Series
    assert list(doubled[Price]) == [2.0, 3.0] and doubled.instrument is a
    with pytest.raises(ValueError):
        mr * None
    with pytest.raises(ValueError, match="Can only index by date"):
        MultipleRiskMeasureResult(a, {Price: _v(1.0)})[D0]


def test_mrmr_add_composes_and_rejects_other_instruments():
    a, b = _swap("a"), _swap("b")
    total = MultipleRiskMeasureResult(a, {Price: _v(1.0, D0)}) + MultipleRiskMeasureResult(a, {Price: _v(1.5, D1), IRDelta: _v(9.0, D1)})
    assert list(total) == [Price, IRDelta] and list(total[Price]) == [1.0, 1.5]
    with pytest.raises(ValueError, match="overlap"):
        MultipleRiskMeasureResult(a, {Price: _v(1.0)}) + MultipleRiskMeasureResult(a, {Price: _v(2.0)})
    with pytest.raises(NotSupportedError):
        MultipleRiskMeasureResult(a, {Price: _v(1.0)}) + MultipleRiskMeasureResult(b, {Price: _v(2.0)})


def test_mrmr_to_frame():
    mr = MultipleRiskMeasureResult(_swap("a"), {Price: _v(1.0), DollarPrice: _v(2.0)})
    df = mr.to_frame()
    assert list(df.columns) == [Price, DollarPrice] and df.iloc[0].tolist() == [1.0, 2.0]
    assert list(mr.to_frame(None, None, None)["risk_measure"]) == [Price, DollarPrice]


# ================================================================================ value classes
def test_float_with_info_arithmetic_follows_gs():
    a, b = _v(3.0), _v(1.0, D1)
    assert type(a * 2) is FloatWithInfo and (a * 2).unit == USD and (a * 2).risk_key == a.risk_key
    assert type(a - b) is float and type(a / 2) is float and type(-a) is float
    s = a + b
    assert s == 4.0 and s.risk_key.date is None and s.risk_key.risk_measure is Price  # combine_risk_key
    assert a.to_frame() is a


def test_float_with_info_keeps_its_type_under_sum_and_a_none_unit_adds_dev_r14():
    a = _v(3.0)
    assert type(a + 1.0) is FloatWithInfo and type(sum([a, a])) is FloatWithInfo and sum([a, a]).unit == USD
    assert (a + FloatWithInfo(1.0)).unit == USD and (FloatWithInfo(1.0) + a).unit == USD
    with pytest.raises(ValueError, match="FloatWithInfo unit mismatch"):
        a + _v(1.0, unit={"EUR": 1})
    # `k * x` keeps the unit too (gs: a plain float, which `+ usd` would then label USD)
    eur = _v(1.0, unit={"EUR": 1})
    assert type(2 * eur) is FloatWithInfo and (2 * eur).unit == {"EUR": 1} and (2 * eur).risk_key == eur.risk_key
    with pytest.raises(ValueError, match="FloatWithInfo unit mismatch"):
        2 * eur + a


def test_aggregate_of_no_leaves_is_zero_and_plain_floats_sum_dev_r15():
    empty = PortfolioRiskResult(Portfolio([]), (Price,), []).aggregate()
    assert type(empty) is FloatWithInfo and empty == 0.0
    assert type(aggregate_results([1.0, 2.0])) is FloatWithInfo and aggregate_results([1.0, 2.0]) == 3.0


def test_other_value_classes():
    e = ErrorValue(_key(), "boom")
    with pytest.raises(AttributeError, match="ErrorValue object has no attribute value.  Error was boom"):
        e.value
    u = UnsupportedValue(_key())
    assert u.raw_value == "Unsupported Value" and repr(u) == "Unsupported Value"
    assert _prr(Portfolio([_swap("a")]), [u]).to_frame(None, None, None) is None
    assert StringWithInfo("x", risk_key=_key()).raw_value == "x" and DictWithInfo({"k": 1}).raw_value == {"k": 1}
    hist = DataFrameWithInfo.compose([_ladder({"1y": 1.0}, D0), _ladder({"1y": 2.0}, D1)])
    assert hist.index.name == "date" and list(hist.raw_value["dates"]) == [D0, D1] and hist.to_frame() is hist


# ================================================================================ risk.core
def test_core_helpers():
    left, right = _ladder({"1y": 5.0, "2y": 1.0}), _ladder({"2y": 3.0, "1y": 2.0})
    diff = subtract_risk(left, right)  # pricebt DEV-R10: gs's asserts always fail
    assert list(diff["mkt_point"]) == ["1y", "2y"] and list(diff["value"]) == [3.0, -2.0]
    with pytest.raises(ValueError):
        subtract_risk(left, left[["mkt_point", "value"]])
    assert list(aggregate_risk([left, right], threshold=4.0)["value"]) == [7.0]
    df = pd.DataFrame({"value": [1.0, 2.0], "mkt_point": ["10y", "2y"], "date": [D1, D0]})
    out = sort_risk(df)  # pricebt DEV-R5: rows keep first appearance
    assert out.index.name == "date" and list(out.columns) == ["mkt_point", "value"] and list(out["mkt_point"]) == ["10y", "2y"]
    assert combine_risk_key(_key(D0), _key(D1)) == RiskKey(None, None, None, None, None, Price)
    assert aggregate_results([]) is None


# ================================================================================ engine: R2-27
_GAPPY = """
def ladder(trades, weights, d):
    live = [w for t, w in zip(trades, weights) if not t.get('gappy') or d.day % 2]
    return {'1y': float(sum(live))} if live else {}
"""


def test_hedge_on_a_ladder_that_is_empty_on_some_dates_r2_27():
    """The hedge leg's ladder is empty on even days: indexing its historical result by such a date
    gives an empty frame (not a KeyError), its risk is 0 and the hedge is skipped that day."""
    from pricebt.backtests.actions import HedgeAction
    from pricebt.backtests.generic_engine import GenericEngine
    from pricebt.backtests.strategy import Strategy
    from pricebt.backtests.triggers import PeriodicTrigger, PeriodicTriggerRequirements
    from pricebt.instrument import ConfigInstrument
    from pricebt.session import PricebtSession

    cfg = {
        "schema_version": 1,
        "asset": "gappy_ladder",
        "instrument": "ConfigInstrument",
        "currency": "USD",
        "code": _GAPPY,
        "market": {"expr": "1"},
        "trade": {"expr": "resolved"},
        "functions": {"pv": {"expr": "0.0", "unit": "ccy"}},
        "portfolio_functions": {"ladder": {"expr": "ladder(trades, weights, pricebt_date)", "unit": "ccy", "returns": "buckets"}},
        "risk_measures": {"Price": "pv", "IRDelta": {"bucketed": "ladder"}},
    }
    PricebtSession.use(assets=[cfg])
    d1, d2, d3 = dt.date(2024, 1, 2), dt.date(2024, 1, 3), dt.date(2024, 1, 4)
    hedge = ConfigInstrument(pricebt_asset="gappy_ladder", name="hedge", gappy=True)
    action = HedgeAction(IRDelta, hedge, trade_duration="1b", risk_transformation=ResultWithInfoAggregator(), name="Hedge")
    trigger = PeriodicTrigger(PeriodicTriggerRequirements(frequency="1b", end_date=d3), actions=[action])
    book = ConfigInstrument(pricebt_asset="gappy_ladder", name="book")
    bt = GenericEngine().run_backtest(Strategy([book], [trigger]), states=[d1, d2, d3], risks=[IRDelta], show_progress=False)
    net = {d: bt.results[d][IRDelta].transform(ResultWithInfoAggregator()).aggregate() for d in (d1, d2, d3)}
    assert net == {d1: 1.0, d2: 0.0, d3: 1.0}  # hedged only on the odd day
    assert [len(bt.portfolio_dict[d]) for d in (d1, d2, d3)] == [1, 2, 1]
