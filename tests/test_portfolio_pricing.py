"""Portfolio pricing parity on the toy swap (IR_RISK_DESIGN.md section 5 items 3, 7, 17; R2-27):
`calc(fn=)` applied per instrument, the historical bucketed shape, gs 030006's Grid frame, and a
hedge whose leg's bucketed ladder is empty on some dates."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest
import yaml

import toylib.rates as tr
from pricebt.backtests.actions import HedgeAction
from pricebt.backtests.generic_engine import GenericEngine
from pricebt.backtests.strategy import Strategy
from pricebt.backtests.triggers import PeriodicTrigger, PeriodicTriggerRequirements
from pricebt.instrument import IRSwap
from pricebt.markets import HistoricalPricingContext, PricingContext
from pricebt.markets.portfolio import Grid, Portfolio
from pricebt.risk import IRDelta, Price
from pricebt.risk.results import DataFrameWithInfo, PortfolioPath, PortfolioRiskResult, SeriesWithInfo
from pricebt.risk.transform import ResultWithInfoAggregator
from pricebt.session import PricebtSession

ASSETS = Path(__file__).parent / "assets"
D1, D2, D3 = date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)


@pytest.fixture
def toy():
    return PricebtSession.use(assets=[ASSETS / "toy_usd_irs.yaml"])


def _book():
    # off-market (toy USD par stays above 2%): the payer is worth more than 0, the receiver less
    inner = Portfolio([IRSwap("Receive", "5y", "USD", 1e6, fixed_rate=0.01, name="r5")], name="inner")
    return Portfolio([IRSwap("Pay", "10y", "USD", 1e6, fixed_rate=0.01, name="p10"), inner], name="book")


# ------------------------------------------------------------------------------ calc(fn=) (item 7)


def test_calc_fn_is_applied_to_each_instrument_and_passed_down(toy):
    book = _book()
    with PricingContext(D1):
        plain = book.calc(Price)
        doubled = book.calc(Price, fn=lambda v: v * 2)
        both = book.calc((Price, IRDelta), fn=lambda mrmr: sorted(m.name for m in mrmr))
    assert isinstance(doubled, PortfolioRiskResult)  # gs: always a result, never fn(result)
    assert [2 * v for v in plain] == list(doubled)  # leaves, nested one included
    assert list(both) == [["IRDelta", "Price"]] * 2  # a multi-measure leaf's fn sees its own MRMR


def test_calc_fn_exception_is_stored_in_that_leaf_only(toy):
    def only_payers(v):
        if v < 0:
            raise ArithmeticError("receiver")
        return v

    book = _book()
    with PricingContext(D1):
        result = book.calc(Price, fn=only_payers)  # does not raise here
    assert result["p10"] > 0
    with pytest.raises(ArithmeticError, match="receiver"):
        result["r5"]


def test_calc_fn_under_a_historical_context_sees_each_instrument_series(toy):
    with HistoricalPricingContext(dates=[D1, D2]):
        result = _book().calc(Price, fn=lambda s: (type(s).__name__, tuple(s.index)))
    assert list(result) == [("SeriesWithInfo", (D1, D2))] * 2


# ------------------------------------------------------------------------------ historical shapes (item 3)


def test_historical_bucketed_value_is_one_date_indexed_frame(toy):
    swap = IRSwap("Pay", "10y", "USD", 1e6, name="p10")
    with HistoricalPricingContext(dates=[D1, D2]):
        ladder = swap.calc(IRDelta).result()
        by_leaf = Portfolio([swap]).calc(IRDelta)
    assert isinstance(ladder, DataFrameWithInfo) and ladder.index.name == "date"
    assert sorted(set(ladder.index)) == [D1, D2] and ladder.unit == {"USD": 1}
    assert list(ladder.raw_value.columns[:1]) == ["dates"]
    with PricingContext(D2):
        one_day = swap.calc(IRDelta).result()
    picked = by_leaf[D2]["p10"]
    assert isinstance(picked, DataFrameWithInfo) and picked.risk_key.date == D2
    pd.testing.assert_frame_equal(pd.DataFrame(picked), pd.DataFrame(one_day))
    with HistoricalPricingContext(dates=[D1, D2]):
        assert isinstance(swap.calc(Price).result(), SeriesWithInfo)  # scalars unchanged


# ------------------------------------------------------------------------------ Grid (item 17, gs 030006)


def test_grid_prices_and_pivots_like_gs_030006(toy):
    grid = Grid(IRSwap("Pay", None, "USD", 1e6), "termination_date", ["5y", "10y"], "fixed_rate", [0.01, 0.02])
    with PricingContext(D1):
        frame = grid.calc(Price).to_frame("value", "portfolio_name_0", "instrument_name")
    assert list(frame.index) == [0.01, 0.02] and list(frame.columns) == ["5y", "10y"]
    assert frame.loc[0.01, "10y"] > frame.loc[0.02, "10y"]  # a payer is worth more at a lower fixed rate


# ------------------------------------------------------------------------------ R2-27 hedge on a gappy ladder

_GAPPY_CODE = """
def gappy_ladder(market, trades, weights, day):
    # a trade maturing within ~3y drops out of the ladder on odd business days
    keep = [(t, w) for t, w in zip(trades, weights) if tr.business_day_index(day) % 2 == 0 or (t.termination_date - market.ref_date).days > 1100]
    if not keep:
        return {}
    return tr.delta_ladder(market, [t for t, _w in keep], [w for _t, w in keep], ("2Y", "5Y", "10Y", "30Y"))
"""


def _gappy_session():
    """toy_usd_irs with its delta ladder made empty for short trades on odd business days."""
    cfg = yaml.safe_load((ASSETS / "toy_usd_irs.yaml").read_text())
    cfg["asset"] = "toy_usd_irs_gappy"
    cfg["code"] = _GAPPY_CODE
    cfg["portfolio_functions"]["delta_ladder"]["expr"] = "gappy_ladder(market, trades, weights, pricebt_date)"
    return PricebtSession.use(assets=[cfg])


def _run_gappy_hedge(trade_duration):
    _gappy_session()
    hedge = Portfolio([IRSwap("Receive", "10y", "USD", 1e6, name="long"), IRSwap("Receive", "2y", "USD", 1e6, name="short")])
    action = HedgeAction(IRDelta, hedge, trade_duration=trade_duration, risk_transformation=ResultWithInfoAggregator(), name="Hedge")
    trigger = PeriodicTrigger(PeriodicTriggerRequirements(frequency="1b", end_date=D3), actions=[action])
    strategy = Strategy([IRSwap("Pay", "10y", "USD", 1e6, name="book")], [trigger])
    return GenericEngine().run_backtest(strategy, states=[D1, D2, D3], risks=[IRDelta], show_progress=False)


def test_hedge_with_a_leg_whose_ladder_is_empty_on_some_dates_r2_27():
    """Each 2b hedge is sized over a gap and a non-gap day: slicing its historical ladder to a gap
    day gives the short leg an empty frame (not a KeyError), and the book is still hedged."""
    gap_days = [d for d in (D1, D2, D3) if tr.business_day_index(d) % 2]
    assert gap_days and len(gap_days) < 3
    bt = _run_gappy_hedge("2b")
    for d in (D1, D2, D3):
        net = bt.results[d][IRDelta].transform(ResultWithInfoAggregator()).aggregate()
        assert float(net) == pytest.approx(0.0, abs=1e-6)  # hedged every day, gap or not
    checked = set()
    for hedges in bt.hedges.values():
        for h in hedges:
            for d in h.scaling_portfolio.dates:
                short = h.scaling_portfolio.results[d][IRDelta][PortfolioPath(1)]  # R2-27 per-date selection
                assert isinstance(short, DataFrameWithInfo) and short.empty == (d in gap_days) and short.risk_key.date == d
                checked.add(d)
    assert set(gap_days) <= checked and len(checked) == 3


@pytest.mark.xfail(strict=True, raises=ValueError, reason="HANDOFF: results._value_for_date returns an all-empty historical frame with an undated key")
def test_one_day_hedge_whose_leg_ladder_is_empty_on_its_only_date_r2_27():
    _run_gappy_hedge("1b")
