"""The gs pricing-context API over market providers (docs/v2/MARKET_CONTEXT_DESIGN.md sections 5
and 7): `market_data_location` reaches every market as `pricebt_location` with no value shared
between two locations (DEV-M3), the gs `PricingContext` properties read back as in gs, and the
manual backtest of the gs "Delta Hedging" notebook (resolve under a `HistoricalPricingContext`,
price each trade historically, unwind a lagged hedge) runs on provider-backed assets.
"""
from __future__ import annotations

import datetime as dt
from datetime import date

import pandas as pd
import pytest

import pricebt.markets as markets_module
import pricebt.progress as progress_module
import toylib.rates as tr
from pricebt.common import Currency, PayReceive
from pricebt.instrument import ConfigInstrument, IRSwap, IRSwaption
from pricebt.markets import CloseMarket, HistoricalPricingContext, PricingContext, market_location
from pricebt.markets.portfolio import Portfolio
from pricebt.risk import IRDelta, IRDeltaParallel, IRVega, Price
from pricebt.risk.results import SeriesWithInfo
from pricebt.session import PricebtSession
from test_market_provider import MARKETS, _asset, _provider, on_provider

D = date(2024, 3, 4)
_LEVEL = {"LDN": 1.0, "NYC": 2.0, "HKG": 3.0, "MY_SNAP": 4.0}


def _location_session():
    """One provider whose market is a number that depends on the location, and one inline asset
    that reads the location in its own market expression and in a function."""
    return PricebtSession.use(
        markets=[_provider("by_location", f"{_LEVEL!r}[pricebt_location] + pricebt_date.day / 100.0", imports="import toylib.rates as tr", code="tr.EVAL_COUNTS['boot'] += 1")],
        assets=[
            _asset("on_provider", {"provider": "by_location"}, functions={"f": {"expr": "market", "unit": "ccy"}}, portfolio_functions={"ladder": {"expr": '{"1Y": market * sum(weights)}', "unit": "ccy_per_bp", "returns": "buckets"}}, risk_measures={"Price": "f", "IRDelta": "ladder"}),
            _asset("inline", {"expr": "pricebt_location"}, functions={"f": {"expr": "len(market + pricebt_location)", "unit": "number"}}),
        ],
    )


def _inst(asset="on_provider"):
    return ConfigInstrument(pricebt_asset=asset, name=asset)


# ------------------------------------------------------------------------------------ location


def test_location_defaults_to_ldn_and_is_inherited():
    assert PricingContext().market_data_location == "LDN" and market_location() == "LDN"
    with PricingContext(market_data_location="NYC"):
        assert PricingContext.current.market_data_location == "NYC" and market_location() == "NYC"
        with PricingContext(pricing_date=D) as inner:
            assert inner.market_data_location == "NYC"
            with HistoricalPricingContext(dates=[D], market_data_location="HKG") as hist:
                assert hist.market_data_location == "HKG"
        assert CloseMarket(date=D, check=False).location == "NYC"  # gs market_location(): the context's
    assert CloseMarket(date=D, check=False).location == "LDN"
    assert market_location("HKG") == "HKG"


def test_a_provider_is_asked_for_the_market_of_the_contexts_location():
    _location_session()
    with PricingContext(D):
        default = _inst().price()
    with PricingContext(D, market_data_location="NYC"):
        nyc = _inst().price()
    with PricingContext(D, market_data_location="HKG"), PricingContext():
        hkg = _inst().price()
    assert [float(f.result()) for f in (default, nyc, hkg)] == [1.04, 2.04, 3.04]


def test_any_location_string_a_config_understands_is_accepted():
    _location_session()
    with PricingContext(D, market_data_location="MY_SNAP"):
        assert float(_inst().price().result()) == 4.04
        # a CloseMarket takes the context's location; with no timezone for it, the close rolls in London's
        assert CloseMarket(date=D).location == "MY_SNAP" and CloseMarket(date=D).date == D
        with pytest.raises(ValueError, match="not a valid PricingLocation"):
            CloseMarket(date=D, location="MY_SNAP")  # an explicit location must be one with a timezone (gs)


def test_an_inline_market_and_a_function_see_the_location_too():
    _location_session()
    with PricingContext(D, market_data_location="NYC"):
        assert float(_inst("inline").price().result()) == len("NYCNYC")


def test_the_default_set_by_assignment_carries_the_location():
    _location_session()
    PricingContext.current = PricingContext(D, market_data_location="NYC")
    assert float(_inst().price()) == 2.04


def test_a_close_market_override_brings_its_own_location():
    _location_session()
    with PricingContext(D, market=CloseMarket(date=date(2024, 3, 1), location="NYC", check=False)) as ctx:
        assert ctx.market_data_location == "NYC"
        price = _inst().price()
    assert float(price.result()) == 2.01  # the NYC market of the override's date
    with pytest.raises(ValueError, match="market.location and market_data_location cannot be different"):
        PricingContext(market=CloseMarket(date=D, location="NYC", check=False), market_data_location="LDN")
    with PricingContext(market_data_location="HKG"):
        # an override without a location takes the location current when the context is built (gs)
        assert PricingContext(market=CloseMarket(date=D, check=False)).market_data_location == "HKG"


def test_values_of_two_locations_never_share_a_cache():
    session = _location_session()
    default_service = session.pricing
    for _ in range(2):  # the second pass is served from each location's own caches
        with PricingContext(D):
            assert float(_inst().price().result()) == 1.04
        with PricingContext(D, market_data_location="NYC"):
            assert session.pricing is not default_service and session.pricing.location == "NYC"
            assert float(_inst().price().result()) == 2.04
    assert session.pricing is default_service and default_service.location == "LDN"
    assert (len(default_service._provider_market_cache), len(session._pricing_by_location["NYC"]._provider_market_cache)) == (1, 1)
    session.reset_caches()
    assert (len(default_service._provider_market_cache), len(session._pricing_by_location["NYC"]._provider_market_cache)) == (0, 0)


def test_namespaces_are_shared_across_locations_so_code_runs_once():
    _location_session()
    for location in ("LDN", "NYC", "HKG"):
        with PricingContext(D, market_data_location=location):
            _inst().price()
    assert tr.EVAL_COUNTS["boot"] == 1


def test_a_lazy_bucketed_value_keeps_the_location_it_was_requested_under():
    _location_session()
    with PricingContext(D, market_data_location="NYC"):
        ladder = Portfolio([_inst()]).calc(IRDelta)
    with PricingContext(D):
        ldn = Portfolio([_inst()]).calc(IRDelta)
    # both evaluated here, outside any context (the default location is LDN)
    assert float(ladder.aggregate()["value"].sum()) == 2.04 and float(ldn.aggregate()["value"].sum()) == 1.04


def test_a_historical_series_is_priced_on_the_contexts_location():
    _location_session()
    dates = [date(2024, 3, 4), date(2024, 3, 5)]
    with HistoricalPricingContext(dates=dates, market_data_location="NYC"):
        nyc = _inst().price()
    with PricingContext(market_data_location="HKG"), HistoricalPricingContext(dates=dates):
        hkg = _inst().price()
    assert list(nyc.result()) == [2.04, 2.05] and list(hkg.result()) == [3.04, 3.05]


def test_a_backtest_runs_on_the_location_of_the_context_around_it():
    from pricebt.backtests.actions import AddTradeAction
    from pricebt.backtests.generic_engine import GenericEngine
    from pricebt.backtests.strategy import Strategy
    from pricebt.backtests.triggers import PeriodicTrigger, PeriodicTriggerRequirements

    _location_session()

    def run():
        trigger = PeriodicTrigger(PeriodicTriggerRequirements(frequency="1b", end_date=date(2024, 3, 6)), [AddTradeAction(_inst(), "1b")])
        return GenericEngine().run_backtest(Strategy(None, [trigger]), start=date(2024, 3, 4), end=date(2024, 3, 6), frequency="1b", show_progress=False)

    ldn = run().result_summary[Price]
    with PricingContext(market_data_location="NYC"):
        nyc = run().result_summary[Price]
    assert list(ldn.round(2)) == [1.04, 1.05, 1.06] and list(nyc.round(2)) == [2.04, 2.05, 2.06]


# ------------------------------------------------------------------------------------ gs properties


def test_gs_properties_read_back_own_then_inherited_then_gs_default():
    ctx = PricingContext()
    assert (ctx.is_batch, ctx.use_cache, ctx.show_progress, ctx.use_server_cache, ctx.use_historical_diddles_only) == (False,) * 5
    assert (ctx.visible_to_gs, ctx.request_priority, ctx.timeout, ctx.provider) == (None,) * 4
    assert ctx.market_behaviour == "ContraintsBased" and ctx.set_parameters_only is False

    provider = object()
    outer = PricingContext(is_batch=True, use_cache=True, visible_to_gs=True, request_priority=3, timeout=30, show_progress=True, use_server_cache=True, market_behaviour="Calibrated", set_parameters_only=True, provider=provider)
    with outer, PricingContext(is_batch=False, market_behaviour=None) as inner:
        assert (inner.is_batch, inner.use_cache, inner.visible_to_gs, inner.request_priority, inner.timeout) == (True, True, True, 3, 30)
        assert (inner.show_progress, inner.use_server_cache, inner.market_behaviour, inner.provider) == (True, True, "Calibrated", provider)
        assert inner.set_parameters_only is False  # own only, as gs
    assert HistoricalPricingContext(dates=[D]).use_historical_diddles_only is True  # gs: always for historical


def test_active_context_is_the_outermost_entered_context_that_submits():
    lone = PricingContext()
    assert lone.active_context is lone and not lone.is_current
    with PricingContext(set_parameters_only=True) as params, PricingContext() as outer, PricingContext() as inner:
        assert inner.active_context is outer and outer.active_context is outer and params.active_context is params
        assert inner.is_current and not outer.is_current


def test_clone_copies_values_and_applies_overrides():
    ctx = PricingContext(pricing_date=D, market_data_location="NYC", csa_term="USD-SOFR", is_batch=True, timeout=5)
    twin = ctx.clone()
    assert type(twin) is PricingContext and twin is not ctx
    assert (twin.pricing_date, twin.market_data_location, twin.csa_term, twin.is_batch, twin.timeout) == (D, "NYC", "USD-SOFR", True, 5)
    other = ctx.clone(pricing_date=date(2024, 3, 5), market_data_location="HKG")
    assert (other.pricing_date, other.market_data_location, other.csa_term) == (date(2024, 3, 5), "HKG", "USD-SOFR")
    assert isinstance(PricingContext.default_value(), PricingContext)


# ------------------------------------------------------------------------------------ show_progress


class _SpyBar(progress_module.ProgressBar):
    made: list = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **{**kwargs, "show": False})
        self.closed = False
        _SpyBar.made.append(self)

    def __exit__(self, *exc):
        self.closed = True
        super().__exit__(*exc)


@pytest.fixture
def spy_bar(monkeypatch):
    import pricebt.assets.pricing as pricing_module

    _SpyBar.made = []
    monkeypatch.setattr(pricing_module, "ProgressBar", _SpyBar)
    return _SpyBar.made


def test_show_progress_draws_one_bar_for_the_outermost_context(spy_bar):
    _location_session()
    dates = [date(2024, 3, 4), date(2024, 3, 5), date(2024, 3, 6)]
    with PricingContext(is_batch=True, show_progress=True) as outer:
        for _ in range(2):
            with HistoricalPricingContext(dates=dates, is_async=True):
                _inst().price()
                _inst().resolve(in_place=False)
        with PricingContext(D):
            _inst().price()
        assert len(spy_bar) == 1 and outer._bar is spy_bar[0] and not spy_bar[0].closed
    bar = spy_bar[0]
    assert bar.closed and outer._bar is None
    assert (bar.total, bar._n) == (13, 13)  # 2 x (3 priced + 3 resolved) + 1 single-date calc
    assert progress_module.ProgressBar._depth == 0


def test_no_bar_without_show_progress_or_outside_an_entered_context(spy_bar):
    _location_session()
    with HistoricalPricingContext(dates=[D]):
        _inst().price()
    PricingContext.current = PricingContext(D, show_progress=True)  # nothing entered: no exit to close a bar on
    _inst().price()
    assert spy_bar == []


def test_a_bar_is_closed_when_the_block_raises(spy_bar):
    _location_session()
    with pytest.raises(RuntimeError, match="boom"):
        with HistoricalPricingContext(dates=[D], show_progress=True):
            _inst().price()
            raise RuntimeError("boom")
    assert len(spy_bar) == 1 and spy_bar[0].closed and progress_module.ProgressBar._depth == 0
    assert markets_module._STACK == []


def test_a_bar_inside_another_bar_stays_silent():
    with progress_module.ProgressBar(1, show=False) as outer, progress_module.ProgressBar(1, show=True) as inner:
        assert inner.show is False and outer._bar is not None
    assert progress_module.ProgressBar._depth == 0


# ------------------------------------------------------------------------------------ the gs manual backtest


def test_the_gs_delta_hedging_notebook_pattern_runs_on_market_providers():
    """gs_quant/content/made_with_gs_quant/4-Delta Hedging.ipynb, cells 4-15, on the toy assets:
    a swap on the curve provider and a swaption on the vol provider that requires it. Differences
    from the gs cells are the toy config's own (it resolves no `effective_date` on a swaption and
    knows 'ATM', not 'ATMF') and pandas 2's (`.items()`; a shifted object column holds None)."""
    PricebtSession.use(markets=[MARKETS], assets=[on_provider("toy_usd_irs.yaml", "toy_usd_ois"), on_provider("toy_usd_swaption.yaml", "toy_usd_swaption_vol")])
    use_batch = True
    start_date, end_date = dt.date(2024, 1, 2), dt.date(2024, 1, 19)
    today = dt.date(2024, 3, 15)

    # cell 4: create and resolve a new straddle on every day of the pricing context
    with HistoricalPricingContext(start=start_date, end=end_date, show_progress=False):
        f = IRSwaption(PayReceive.Straddle, "10y", Currency.USD, expiration_date="1m", notional_amount=1e8, buy_sell="Sell").resolve(in_place=False)
    result = f.result().items()
    portfolio = Portfolio([v[1] for v in sorted(result)])
    assert len(portfolio) == 14 and all(inst.resolved_terms is not None for inst in portfolio.all_instruments)

    # cell 6: the portfolio as a frame, with trade dates
    frame = portfolio.to_frame()
    frame.index = frame.index.droplevel(0)
    trade_dates = {value: key for key, value in result}
    frame["trade_date"] = frame.apply(lambda x: trade_dates[x.name], axis=1)
    assert list(frame.trade_date) == sorted(trade_dates.values()) and (frame.expiration_date > frame.trade_date).all()

    # cell 9: price and delta of each swaption from its trade date to its expiry
    frame["so_price_f"] = len(frame) * [None]
    frame["so_delta_f"] = len(frame) * [None]
    with PricingContext(is_batch=use_batch, show_progress=False):
        for inst, row in frame.iterrows():
            with HistoricalPricingContext(start=row.trade_date, end=min(row.expiration_date, today), is_async=True):
                so_price = inst.price()
                so_delta = inst.calc(IRDeltaParallel)
            frame.at[inst, "so_price_f"] = so_price
            frame.at[inst, "so_delta_f"] = so_delta

    # cell 11: a new ATM swap each day, matching the swaption's underlying
    frame["s_f"] = len(frame) * [None]
    frame["s_delta_f"] = len(frame) * [None]
    with PricingContext(is_batch=use_batch, show_progress=False):
        for inst, row in frame.iterrows():
            swap = IRSwap(PayReceive.Pay, row.termination_date, Currency.USD, effective_date=row.expiration_date, fixed_rate="ATM", notional_amount=1e8)
            with HistoricalPricingContext(start=row.trade_date, end=min(row.expiration_date, today), is_async=True):
                s = swap.resolve(in_place=False)
                s_delta = swap.calc(IRDeltaParallel)
            frame.at[inst, "s_f"] = s
            frame.at[inst, "s_delta_f"] = s_delta

    # cell 13: price each day's swap one day later, when it is unwound
    swaps = pd.concat([pd.Series(row.s_f.result(), name=row.name) for _, row in frame.iterrows()], axis=1, sort=True).shift(periods=1)
    g = {}
    with PricingContext(is_batch=use_batch, show_progress=False):
        for day, row in swaps.iterrows():
            with PricingContext(day, is_async=True):
                prices = {k: p if p is None or isinstance(p, float) else p.price() for k, p in row.items()}
            g[day] = prices
    swap_prices = pd.DataFrame(g).T

    # cell 15: one series per swaption
    for inst, row in frame.iterrows():
        unwound = pd.Series({k: v.result() for k, v in swap_prices[inst].items() if v is not None and not isinstance(v, float)})
        total = pd.concat([row.so_price_f.result(), row.so_delta_f.result(), unwound, row.s_delta_f.result()], axis=1, sort=True)
        total.columns = ["swaption_prices", "swaption_delta", "swap_bought_prices", "swap_sold_delta"]
        assert isinstance(row.so_price_f.result(), SeriesWithInfo)
        priced = total.loc[: min(row.expiration_date, today)]  # the last swap is unwound one business day later
        assert priced.index[0] == row.trade_date and len(priced) == len(row.so_price_f.result())
        assert priced[["swaption_prices", "swaption_delta", "swap_sold_delta"]].notna().all().all()
        assert (priced.swaption_prices < 0).all()  # a sold straddle
        assert (priced.swap_sold_delta > 0).all()  # a payer swap
        # the swap resolved at the money on day t has a non-zero value on day t+1, and none on day t
        assert pd.isna(total.swap_bought_prices.iloc[0]) and (total.swap_bought_prices.iloc[1:].abs() > 0).all()

    # one curve object per date for both assets: the swaption market never loaded its own
    assert tr.EVAL_COUNTS["market"] == tr.EVAL_COUNTS["vol_market"]


def test_greek_and_dv01_time_series_under_a_historical_context():
    """The time-series use: one resolved swaption's greeks and a rolling par swap's dv01, by date."""
    PricebtSession.use(markets=[MARKETS], assets=[on_provider("toy_usd_irs.yaml", "toy_usd_ois"), on_provider("toy_usd_swaption.yaml", "toy_usd_swaption_vol")])
    with PricingContext(date(2024, 1, 2)):
        option = IRSwaption("Pay", "10y", "USD", expiration_date="1y", strike="ATM", notional_amount=1e8, buy_sell="Buy").resolve(in_place=False).result()
    swap = IRSwap("Pay", "10y", "USD", notional_amount=1e8)  # unresolved: a new par swap on every date
    with HistoricalPricingContext(date(2024, 1, 2), date(2024, 1, 31)):
        greeks = option.calc((IRDeltaParallel, IRVega(aggregation_level="Type"), Price))
        dv01 = swap.calc(IRDeltaParallel)
    greeks, dv01 = greeks.result(), dv01.result()
    assert len(dv01) == 22 and list(dv01.index) == list(greeks[Price].index) and dv01.index[0] == date(2024, 1, 2)
    assert (greeks[IRVega(aggregation_level="Type")] > 0).all() and (dv01 > 0).all()
    assert dv01.nunique() > 1 and greeks[IRDeltaParallel].nunique() > 1  # the market moves every date
