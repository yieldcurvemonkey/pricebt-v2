"""Acceptance test for Portfolio and results parity (IR_RISK_DESIGN.md section 5 "Acceptance",
decision 0.8): the code cells of gs `documentation/03_portfolios/examples/030000-030011` and
`tutorials/Portfolios.ipynb`, transcribed onto the toy assets, asserting the documented result
shapes. The cells of each notebook are listed in a comment above its test (cell index = position in
the notebook's `cells`, markdown included).

Transcription rules (DESIGN.md section 1):
- `gs_quant` -> `pricebt` in every import; `GsSession.use(...)` -> `PricebtSession.use(assets=[...])`
  (the autouse `toy_session` fixture); the notebooks' "today" -> TOY_TODAY through
  `PricingContext.current` (the gs tutorial's own setter; conftest restores it).
- Re-parameterised only where a toy asset needs it:
  - IRSwaption `Currency.EUR`/`'EUR'` -> USD: `toy_usd_swaption` is the only swaption asset (instrument
    names such as `'EUR-3m5y'` are kept verbatim);
  - 030002 strikes `'ATMF'`/`'ATMF+50'` -> `'ATM'`/`'ATM+50'`: the strike grammar is the config's job
    (DEV-I6) and the toy's is `ATM[+-bp]`;
  - IRSwap uses `toy_usd_irs_full` (USD) and the same config re-denominated in EUR (`_toy_eur_irs_full`),
    so both currencies price on the same functions (030009 asks every swap for `IRVegaParallel`,
    0.0 by convention, R2-8; written when `toy_eur_irs` still declared it unsupported, before the
    strict contract, IR_STRICT_CONTRACT R3-0); the two USD swap assets cannot be registered
    together (same `match:`);
  - 030007: the swap `'EUR'` -> `'USD'`: `PnlExplain` has no currency parameter, so pricebt reports it in
    each asset function's own currency (DEV-I5), and `aggregate()` refuses to add EUR and USD rows (gs's unit
    check); the notebook's own book is single-currency (EUR swap and swaption);
  - 030011: the CSV's `'27-Jan-15'` dates and `'50,000,000'` notionals are parsed by the GS server;
    the toy resolver takes dates and numbers (DEV-I6), so the `effective_date`, `termination_date` and
    `notional_amount` mappers parse them (the other seven mappers are verbatim).
- gs vs pricebt shape difference kept on purpose: gs returns an EMPTY frame for a swap's
  `IRVegaParallel` (R09 finding 6), which `to_frame()` drops; pricebt's R2-8 convention is 0.0, so in
  030009 cells 13 and 14 the swap rows stay and the two frames are equal.

SKIPPED cells:

| notebook | cell(s) | reason |
|---|---|---|
| every notebook | the `GsSession.use(...)` cell | replaced by the `toy_session` fixture (`PricebtSession.use`) |
| 030002 | 0 (`pd.options.display.float_format = ...`) | global pandas display state only; it would leak past the test |
| 030002 | 19 | empty |
| 030006 | 0 (matplotlib/seaborn imports), 5 | plotting only (seaborn heatmap); no pricebt call, not a pricebt dependency |
| 030007 | 5 | empty |
| 030008 | 3, 9 | commented out in the notebook (`pd.read_excel`, `Portfolio.from_csv`) |
| 030010 | 4, 5 | the cross-leg reference `strike="=[foo].strike + 5bp"` is resolved GS server-side; a config resolves one instrument at a time (R10 section 3), so resolve fails loudly (asserted) |
| 030011 | 2 (`IRSwap?`) | IPython help magic, no pricebt call |
| tutorials/Create New Portfolio, Pull Portfolio Factor Risk Data, Pull Portfolio Performance Data, Pull Portfolio Risk Data, Update Historical Portfolio | all | server-only (`PortfolioManager`, `Portfolio.save`, reports, Marquee APIs; R10 section 3); not in the acceptance set |
"""
from __future__ import annotations

import datetime as dt
import warnings
from pathlib import Path

import pandas as pd
import pytest
import yaml

import pricebt.risk as risk
from pricebt.common import Currency, PayReceive
from pricebt.errors import AssetEvaluationError
from pricebt.instrument import IRSwap, IRSwaption
from pricebt.markets import HistoricalPricingContext, PricingContext
from pricebt.markets.portfolio import Portfolio
from pricebt.risk import IRAnnualImpliedVol, IRDelta, IRDeltaParallel, IRFwdRate, IRVega, Price
from pricebt.risk.results import DataFrameWithInfo, FloatWithInfo, MultipleRiskMeasureResult, PortfolioRiskResult, SeriesWithInfo
from pricebt.session import PricebtSession

ASSETS = Path(__file__).parent / "assets"
DATA = Path(__file__).parent / "data"
TOY_TODAY = dt.date(2024, 1, 2)
MKT_COLUMNS = ["mkt_type", "mkt_asset", "mkt_class", "mkt_point", "mkt_quoting_style", "value"]


def _toy_eur_irs_full() -> dict:
    """toy_usd_irs_full re-denominated in EUR (the toy rates world has an EUR curve)."""
    return yaml.safe_load((ASSETS / "toy_usd_irs_full.yaml").read_text().replace("USD", "EUR").replace("usd", "eur"))


@pytest.fixture(autouse=True)
def toy_session():
    """`GsSession.use(Environment.PROD, client_id=None, ...)` -> the toy assets; "today" -> TOY_TODAY."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # every asset maps or declares its whole contract, no stale declaration
        session = PricebtSession.use(
            assets=[ASSETS / "toy_usd_swaption.yaml", ASSETS / "toy_usd_irs_full.yaml", _toy_eur_irs_full()],
            fx=ASSETS / "toy_fx.yaml",
        )
    PricingContext.current = PricingContext(pricing_date=TOY_TODAY)
    return session


def _by_point(frame) -> pd.Series:
    return pd.DataFrame(frame).set_index("mkt_point")["value"]


def _assert_bucketwise_sum(total, *leaves):
    """`aggregate()` of bucketed leaves: the same buckets, each value the sum over the leaves."""
    assert isinstance(total, DataFrameWithInfo) and list(total.columns) == MKT_COLUMNS
    expected = sum((_by_point(leaf) for leaf in leaves[1:]), _by_point(leaves[0]))
    pd.testing.assert_series_equal(_by_point(total).sort_index(), expected.sort_index(), check_names=False)


# ------------------------------------------------------------------------------ 030000_create_portfolio
# cells: 0 imports; 1 GsSession.use (fixture); 2 two IRSwaptions; 3 Portfolio((swaption1, swaption2))


def test_030000_create_portfolio():
    swaption1 = IRSwaption(PayReceive.Pay, '5y', Currency.USD, expiration_date='3m', name='EUR-3m5y')
    swaption2 = IRSwaption(PayReceive.Pay, '7y', Currency.USD, expiration_date='6m', name='EUR-6m7y')

    portfolio = Portfolio((swaption1, swaption2))

    assert portfolio.priceables == (swaption1, swaption2) and len(portfolio) == 2
    assert swaption1.pay_or_receive == PayReceive.Pay and swaption1.termination_date == '5y'
    assert swaption1.notional_currency == Currency.USD and swaption2.expiration_date == '6m'
    assert [i.name for i in portfolio.all_instruments] == ['EUR-3m5y', 'EUR-6m7y']


# ------------------------------------------------------------------------------ 030001_modify_instruments
# cells: 0 imports; 1 GsSession.use; 2 three IRSwaptions (one mid-curve); 3 Portfolio + print(instruments);
# 4 `portfolio.pricables = (...)` + print(instruments)


def test_030001_modify_instruments_the_pricables_typo_is_a_no_op():
    swaption1 = IRSwaption(PayReceive.Pay, '5y', Currency.USD, expiration_date='3m', name='EUR-3m5y Payer')
    swaption2 = IRSwaption(PayReceive.Pay, '7y', Currency.USD, expiration_date='9m', name='EUR-9m7y Payer')
    swaption_midcurve = IRSwaption(
        PayReceive.Receive, '10y', Currency.USD, effective_date='10y', expiration_date='6m', name='EUR-6m10y10y Receiver'
    )

    portfolio = Portfolio((swaption1, swaption2))
    assert portfolio.instruments == (swaption1, swaption2)

    portfolio.pricables = (swaption1, swaption2, swaption_midcurve)
    # gs's own typo (R10 section 3): an unrelated attribute, so the constituents are unchanged
    assert portfolio.instruments == (swaption1, swaption2)
    assert swaption_midcurve.effective_date == '10y'


# ------------------------------------------------------------------------------ 030002_extracting_instruments_and_results
# cells: 0 imports (+ pd.options display format, skipped); 1 GsSession.use; 3 trades in a
# PricingContext + trade2.resolve() + Portfolio; 4 lookup by index/object/name; 6 calc((IRVega,
# IRDelta, Price)); 7 results[0]; 8 four ways to one value; 9 a + b and aggregate(); 10
# pd.DataFrame(results[Price]); 11 results[IRDelta]['payer']; 12 .value.sum(); 13 .value[1] and
# aggregate(); 14 HistoricalPricingContext calc; 15 a leaf Series; 16 aggregate by date; 17 .at[date];
# 18 a date-indexed bucketed frame


def _030002_portfolio():
    with PricingContext(pricing_date=dt.date(2019, 4, 24)):
        trade1 = IRSwaption(
            pay_or_receive='Pay',
            notional_currency='USD',
            expiration_date='6m',
            termination_date='3y',
            strike='ATM',
            name='payer',
        )
        trade2 = IRSwaption(
            pay_or_receive='Receive',
            notional_currency='USD',
            expiration_date='6m',
            termination_date='3y',
            strike='ATM+50',
            name='receiver',
        )
        trade2.resolve()
    portfolio = Portfolio((trade1, trade2))
    return trade1, trade2, portfolio


def test_030002_extract_instruments_and_single_date_results():
    trade1, trade2, portfolio = _030002_portfolio()

    # cell 4
    assert portfolio[1] is trade2  # by index
    assert portfolio[trade2] is trade2  # by instrument object
    assert portfolio['receiver'] is trade2  # by instrument name

    # cell 6
    with PricingContext(pricing_date=dt.date(2019, 4, 24)):
        results = portfolio.calc((IRVega, IRDelta, Price))
        payer_delta = trade1.calc(IRDeltaParallel)  # independent: the scalar the toy ladder buckets (R2-2)
    assert type(results) is PortfolioRiskResult

    # cell 7: one trade's results, keyed by measure in request order
    assert isinstance(results[0], MultipleRiskMeasureResult)
    assert list(results[0]) == [IRVega, IRDelta, Price]
    assert isinstance(results[0][Price], FloatWithInfo) and isinstance(results[0][IRDelta], DataFrameWithInfo)

    # cell 8
    ways = (results[Price]['receiver'], results[Price][1], results[Price][trade2], results['receiver'][Price])
    assert len({'{:,.0f}'.format(v) for v in ways}) == 1 and len({float(v) for v in ways}) == 1

    # cell 9
    a = results[Price]['payer']
    b = results[Price]['receiver']
    assert '{:,.0f}'.format(a + b) == '{:,.0f}'.format(results[Price].aggregate())
    assert float(results[Price].aggregate()) == pytest.approx(float(a) + float(b))
    assert results[Price].aggregate().unit == {'USD': 1}

    # cell 10
    frame = pd.DataFrame(results[Price])
    assert frame.shape == (2, 1) and frame[0].tolist() == pytest.approx([float(a), float(b)])

    # cell 11
    payer_ladder = results[IRDelta]['payer']
    assert isinstance(payer_ladder, DataFrameWithInfo) and list(payer_ladder.columns) == MKT_COLUMNS
    assert payer_ladder.mkt_point.tolist() == ['2Y', '5Y', '10Y', '30Y']

    # cell 12
    assert results[IRDelta]['payer'].value.sum() == pytest.approx(float(payer_delta.result()))

    # cell 13
    assert '{:,.0f}'.format(results[IRDelta][0].value[1]) == '{:,.0f}'.format(payer_ladder.value[1])
    assert results[IRDelta][1].value[1] == results[IRDelta]['receiver'].value[1]
    _assert_bucketwise_sum(results[IRDelta].aggregate(), results[IRDelta][0], results[IRDelta][1])


def test_030002_historical_results():
    trade1, trade2, portfolio = _030002_portfolio()
    with PricingContext(pricing_date=dt.date(2019, 4, 24)):
        one_day = portfolio.calc((IRDelta, Price))

    # cell 14
    with HistoricalPricingContext(dt.date(2019, 4, 24), dt.date(2019, 5, 24)):
        hist_results = portfolio.calc((IRVega, IRDelta, Price))
    assert type(hist_results) is PortfolioRiskResult
    dates = [d.date() for d in pd.bdate_range('2019-04-24', '2019-05-24')]

    # cell 15: a Series of floats indexed by date
    series = hist_results[Price][trade1]
    assert isinstance(series, SeriesWithInfo) and list(series.index) == dates
    assert series[dates[0]] == pytest.approx(float(one_day[Price]['payer']))

    # cell 16: aggregated by date
    total = hist_results[Price].aggregate()
    assert isinstance(total, SeriesWithInfo) and list(total.index) == dates
    pd.testing.assert_series_equal(
        pd.Series(total), pd.Series(hist_results[Price][trade1]) + pd.Series(hist_results[Price][trade2]), check_names=False
    )

    # cell 17
    assert hist_results[Price].aggregate().at[dt.date(2019, 4, 30)] == pytest.approx(
        hist_results[Price][trade1][dt.date(2019, 4, 30)] + hist_results[Price][trade2][dt.date(2019, 4, 30)]
    )

    # cell 18: the per-date frames concatenated, indexed by date
    ladder = hist_results[IRDelta][0]
    assert isinstance(ladder, DataFrameWithInfo) and ladder.index.name == 'date'
    assert len(ladder) == 4 * len(dates) and sorted(set(ladder.index)) == dates
    pd.testing.assert_frame_equal(
        pd.DataFrame(ladder[ladder.index == dates[0]]).reset_index(drop=True), pd.DataFrame(one_day[IRDelta]['payer'])
    )


# ------------------------------------------------------------------------------ 030003_resolve_portfolio
# cells: 0 imports; 1 GsSession.use; 2 two IRSwaptions (strike 'atm+50', notional 5e7); 3 resolve +
# print(portfolio['EUR-3m5y'].as_dict())


def test_030003_resolve_portfolio():
    swaption1 = IRSwaption(PayReceive.Pay, '12m', Currency.USD, expiration_date='3m', strike='atm+50', name='EUR-3m5y')
    swaption2 = IRSwaption(PayReceive.Pay, '5y', Currency.USD, expiration_date='6m', notional_amount=5e7, name='EUR-6m5y')

    portfolio = Portfolio((swaption1, swaption2))
    portfolio.resolve()
    as_dict = portfolio['EUR-3m5y'].as_dict()  # resolved instrument in the portfolio

    assert as_dict['name'] == 'EUR-3m5y' and as_dict['quantity_'] == 1.0  # quantity_: DEV-I2
    assert as_dict['expiration_date'] == dt.date(2024, 4, 2) and as_dict['termination_date'] == dt.date(2025, 4, 2)
    fwd_bp = float(portfolio['EUR-3m5y'].calc(IRFwdRate))  # independent of the toy's par formula
    assert as_dict['strike'] * 1e4 - fwd_bp == pytest.approx(50.0)
    unit = IRSwaption(PayReceive.Pay, '5y', Currency.USD, expiration_date='6m', notional_amount=1e6)
    assert float(portfolio['EUR-6m5y'].price()) == pytest.approx(50 * float(unit.price()))


# ------------------------------------------------------------------------------ 030004_price_portfolio
# cells: 0 imports; 1 GsSession.use; 2 two IRSwaptions + Portfolio; 3 price() + aggregate(); 4 one price


def test_030004_price_portfolio():
    swaption1 = IRSwaption(PayReceive.Pay, '5y', Currency.USD, expiration_date='3m', name='EUR-3m5y')
    swaption2 = IRSwaption(PayReceive.Pay, '8y', Currency.USD, expiration_date='5w', name='EUR-5w8y')
    portfolio = Portfolio((swaption1, swaption2))

    price_result = portfolio.price()
    total = price_result.aggregate()  # aggregate portfolio price
    one = price_result['EUR-3m5y']  # price of specific instrument

    assert isinstance(price_result, PortfolioRiskResult) and isinstance(total, FloatWithInfo) and total.unit == {'USD': 1}
    assert float(one) == pytest.approx(float(swaption1.price()))
    assert float(total) == pytest.approx(float(swaption1.price()) + float(swaption2.price()))


# ------------------------------------------------------------------------------ 030005_calculate_portfolio_risk
# cells: 0 imports; 1 GsSession.use; 2 two IRSwaptions + calc((DollarPrice, IRDelta)); 3 IRDelta
# aggregate(); 4 one DollarPrice, both index orders


def test_030005_calculate_portfolio_risk():
    swaption1 = IRSwaption(PayReceive.Pay, '5y', Currency.USD, expiration_date='3m', name='EUR-3m5y')
    swaption2 = IRSwaption(PayReceive.Pay, '5y', Currency.USD, expiration_date='6m', name='EUR-6m5y')
    portfolio = Portfolio((swaption1, swaption2))
    result = portfolio.calc((risk.DollarPrice, risk.IRDelta))

    # cell 3: portfolio risk
    _assert_bucketwise_sum(result[risk.IRDelta].aggregate(), result[risk.IRDelta][0], result[risk.IRDelta][1])

    # cell 4: instrument risk
    by_measure, by_name = result[risk.DollarPrice]['EUR-3m5y'], result['EUR-3m5y'][risk.DollarPrice]
    assert float(by_measure) == float(by_name) == pytest.approx(float(swaption1.price()))  # USD: DollarPrice is Price


# ------------------------------------------------------------------------------ 030006_portfolio_grid_calc
# cells: 0 imports (matplotlib/seaborn skipped); 1 GsSession.use; 2 grid parameters; 3 nested
# Portfolio of 8 x 9 IRSwaptions + calc(IRAnnualImpliedVol); 4 to_frame pivot * 10000; 5 heatmap (skipped)


def test_030006_portfolio_grid_calc():
    tails = ['1y', '3y', '5y', '10y', '15y', '20y', '25y', '30y']
    expiries = ['3m', '6m', '9m', '1y', '18m', '2y', '3y', '4y', '5y']
    pay_rec = 'Pay'
    ccy = 'USD'
    moneyness = 25

    portfolios = Portfolio(
        [
            Portfolio(
                [
                    IRSwaption(
                        pay_or_receive=pay_rec,
                        notional_currency=ccy,
                        termination_date=t,
                        expiration_date=e,
                        strike='ATM+{}'.format(moneyness),
                        name=e,
                    )
                    for e in expiries
                ],
                name=t,
            )
            for t in tails
        ]
    )
    results = portfolios.calc(IRAnnualImpliedVol)

    frame = results.to_frame('value', 'portfolio_name_0', 'instrument_name') * 10000

    # first-appearance order, not sorted: '10y' after '5y', '18m' between '1y' and '2y'
    assert frame.index.name == 'portfolio_name_0' and list(frame.index) == tails
    assert frame.columns.name == 'instrument_name' and list(frame.columns) == expiries
    assert not frame.isna().any().any()
    alone = IRSwaption(pay_or_receive='Pay', notional_currency='USD', termination_date='10y', expiration_date='18m', strike='ATM+25')
    assert frame.loc['10y', '18m'] == pytest.approx(float(alone.calc(IRAnnualImpliedVol)) * 10000)


# ------------------------------------------------------------------------------ 030007_pnl_explain
# cells: 0 GsSession.use; 2 swap + swaption Portfolio, resolve(); 4 PnlExplain(CloseMarket) vs dollar
# prices (IR_RISK_DESIGN section 8); 5 empty


def test_030007_pnl_explain():
    swap = IRSwap(notional_currency='USD', termination_date='10y', pay_or_receive='Pay')
    swaption = IRSwaption(notional_currency='USD', termination_date='10y', expiration_date='1y', pay_or_receive='Receive')

    portfolio = Portfolio((swap, swaption))
    portfolio.resolve()

    from pricebt.datetime import business_day_offset  # gs: gs_quant.datetime.date (pricebt has no .date submodule)
    from pricebt.markets import CloseMarket, close_market_date
    from pricebt.risk import DollarPrice, PnlExplain

    to_date = close_market_date()

    # 5 business days ago
    from_date = business_day_offset(to_date, -5)

    # A risk measure for calculating PnlExplain from that date
    explain = PnlExplain(CloseMarket(date=to_date))

    # Calculate PnlExplain and dollar price from 1 week ago
    with PricingContext(pricing_date=from_date):
        result = portfolio.calc((DollarPrice, explain))

    # Calculate dollar price with the "to" market but "from" pricing date
    with PricingContext(pricing_date=from_date, market=CloseMarket(date=to_date)):
        to_market_price = portfolio.dollar_price()

    # Calculate dollar price with the "to" market and pricing date
    with PricingContext(pricing_date=to_date):
        to_price = portfolio.dollar_price()

    # Compute the time component (PnlExplain does not do this)
    time_value = to_price.aggregate() - to_market_price.aggregate()

    price_diff = to_price.aggregate() - result[DollarPrice].aggregate()
    explained = result[explain].aggregate().value.sum() + time_value

    # Show the PnlExplain breakdown
    explain_all = result[explain].aggregate()
    shown = explain_all[explain_all.value.abs() > 1.0].round(0)

    assert (from_date, to_date) == (dt.date(2023, 12, 26), TOY_TODAY)
    # the swap's and the swaption's rows, summed by factor (first appearance); CROSSES is the rest
    assert list(explain_all.mkt_type) == ['IR', 'CROSSES', 'IR VOL'] and set(shown.mkt_type) <= set(explain_all.mkt_type)
    # the toys value on pricebt_date, so time passes between the two prices (the swaption's time
    # value, the swap's carry): a time/market mix-up in the explain rows breaks the identity below
    assert abs(float(time_value)) > 1.0
    assert float(explained) == pytest.approx(float(price_diff), rel=1e-6)  # section 8.3: full revaluation by factor


# ------------------------------------------------------------------------------ 030008_portflio_from_frame
# cells: 1 GsSession.use; 3 read_excel (commented out, skipped); 5 the dummy frame; 7 mapper +
# from_frame + to_frame().reset_index(drop=True); 9 from_csv (commented out, skipped)


def test_030008_portfolio_from_frame():
    data = pd.DataFrame.from_dict(
        {
            'name': {0: 'my favourite swap', 1: 'my favourite swaption', 2: None, 3: None, 4: None, 5: None},
            'trade_type': {0: 'Swap', 1: 'Swaption', 2: 'Swaption', 3: 'Swaption', 4: 'Swaption', 5: 'Swaption'},
            'rate': {0: 0.01, 1: None, 2: None, 3: None, 4: None, 5: None},
            'strike': {0: None, 1: '2%', 2: '0.02', 3: '0.02', 4: '0.02', 5: '0.02'},
            'ccy': {0: 'EUR', 1: 'GBP', 2: 'GBP', 3: 'GBP', 4: 'GBP', 5: 'GBP'},
            'freq': {0: '3m/6m', 1: '3m/6m', 2: '3m/6m', 3: '3m/6m', 4: '3m/6m', 5: '3m/6m'},
            'index': {
                0: 'EURIBOR-TELERATE',
                1: 'LIBOR-BBA',
                2: 'LIBOR-BBA',
                3: 'LIBOR-BBA',
                4: 'LIBOR-BBA',
                5: 'LIBOR-BBA',
            },
            'expiration_date': {0: '30/06/2021', 1: '30/06/2021', 2: '3d', 3: '30/06/2021', 4: '30/06/2021', 5: '3m'},
            'asset_class': {0: 'rates', 1: 'rates', 2: 'rates', 3: 'rates', 4: 'rates', 5: 'rates'},
        }
    )

    mapper = {
        'type': 'trade_type',
        'fixed_rate': 'rate',
        'pay_ccy': 'ccy',
        'fixed_rate_frequency': lambda row: row['freq'][: row['freq'].index("/")],
        'floating_rate_frequency': lambda row: row['freq'][row['freq'].index("/") + 1 :],
        'floating_rate_option': lambda row: row['ccy'] + '-' + row['index'],
    }

    portfolio = Portfolio.from_frame(data, mappings=mapper)
    frame = portfolio.to_frame().reset_index(drop=True)

    assert [type(i) for i in portfolio] == [IRSwap] + [IRSwaption] * 5
    assert list(frame.index) == list(range(6))
    assert list(frame.columns[:2]) == ['asset_class', 'type'] and list(frame.columns[2:]) == sorted(frame.columns[2:])
    assert 'quantity_' in frame.columns  # DEV-I2
    assert [str(t) for t in frame['type']] == ['Swap'] + ['Swaption'] * 5
    assert frame['name'].tolist()[:2] == ['my favourite swap', 'my favourite swaption'] and frame['name'][2:].isna().all()
    assert frame['fixed_rate'][0] == 0.01 and frame['strike'].tolist()[1:] == ['2%', '0.02', '0.02', '0.02', '0.02']
    assert set(frame['fixed_rate_frequency']) == {'3m'} and set(frame['floating_rate_frequency']) == {'6m'}
    assert frame['floating_rate_option'].tolist() == ['EUR-EURIBOR-TELERATE'] + ['GBP-LIBOR-BBA'] * 5
    assert frame['expiration_date'].tolist()[1:] == ['30/06/2021', '3d', '30/06/2021', '30/06/2021', '3m']


# ------------------------------------------------------------------------------ 030009_portfolio_risk_result_to_frame
# cells: 0 imports; 1 GsSession.use; 2 nested portfolio with duplicate names; 3 eur/nested prices and
# nested IRVegaParallel; 5 default to_frame(); 6 to_frame(None, None, None); 7 custom pivot; 9 two swaps
# named '5y'; 10 same-name sum; 11 aggfunc='mean'; 13 vega to_frame(); 14 display_options show_na


def _030009_nested():
    swap_1 = IRSwap('Pay', '5y', 'EUR', fixed_rate=-0.005, name='5y')
    swap_2 = IRSwap('Pay', '10y', 'EUR', fixed_rate=-0.005, name='10y')
    swap_3 = IRSwap('Pay', '5y', 'USD', fixed_rate=-0.005, name='5y')
    swap_4 = IRSwap('Pay', '10y', 'USD', fixed_rate=-0.005, name='10y')
    swaption_1 = IRSwaption('Pay', '5y', 'USD', expiration_date='1y', name='5y')
    eur_port = Portfolio([swap_1, swap_2], name='EUR')
    usd_port = Portfolio([swap_3, swap_4], name='USD')
    nested_port = Portfolio([eur_port, usd_port, swaption_1])
    return (swap_1, swap_2, swap_3, swap_4, swaption_1), eur_port, nested_port


def test_030009_nested_to_frame_pivots():
    (swap_1, swap_2, swap_3, swap_4, swaption_1), eur_port, nested_port = _030009_nested()

    # cell 3
    eur_port_price = eur_port.price()
    nested_port_price = nested_port.price()
    values = [float(eur_port_price['5y']), float(eur_port_price['10y'])] + [float(i.price()) for i in (swap_3, swap_4, swaption_1)]
    assert len(set(values)) == 5  # distinct, so a mislabelled row cannot pass

    # cell 5: default pivot of a nested single-measure result (DEV-R6: every row labelled from its own path)
    default = nested_port_price.to_frame()
    assert default.index.name == 'portfolio_name_0' and list(default.index) == ['EUR', 'USD', 'N/A']
    assert default.columns.name == 'instrument_name' and list(default.columns) == ['5y', '10y']
    assert default.loc['EUR'].tolist() == pytest.approx(values[0:2]) and default.loc['USD'].tolist() == pytest.approx(values[2:4])
    assert default.loc['N/A', '5y'] == pytest.approx(values[4]) and pd.isna(default.loc['N/A', '10y'])

    # cell 6: no pivot, the raw records
    raw = nested_port_price.to_frame(values=None, columns=None, index=None)
    assert list(raw.columns) == ['portfolio_name_0', 'instrument_name', 'risk_measure', 'value']
    assert raw.portfolio_name_0.tolist() == ['EUR', 'EUR', 'USD', 'USD', 'N/A']
    assert raw.instrument_name.tolist() == ['5y', '10y', '5y', '10y', '5y']
    assert [str(m) for m in raw.risk_measure] == ['Price'] * 5 and raw.value.tolist() == pytest.approx(values)

    # cell 7: custom pivot parameters (the transpose of cell 5)
    custom = nested_port_price.to_frame(values='value', columns='portfolio_name_0', index='instrument_name')
    pd.testing.assert_frame_equal(custom, default.T)


def test_030009_same_name_rows_aggregate_with_aggfunc():
    # cell 9
    swap_5 = IRSwap('Pay', '5y', 'EUR', fixed_rate=-0.005, name='5y')
    swap_6 = IRSwap('Pay', '10y', 'EUR', fixed_rate=-0.005, name='5y')
    port = Portfolio([swap_5, swap_6])
    res = port.price()
    both = float(swap_5.price()) + float(swap_6.price())

    # cell 10: when instruments have the same name, the values are summed by default
    summed = res.to_frame()
    assert summed.index.name == 'instrument_name' and list(summed.index) == ['5y']
    assert [str(c) for c in summed.columns] == ['Price'] and summed.iloc[0, 0] == pytest.approx(both)

    # cell 11: change aggregation of values
    assert res.to_frame(aggfunc='mean').iloc[0, 0] == pytest.approx(both / 2)


def test_030009_vega_to_frame():
    (_s1, _s2, _s3, _s4, swaption_1), _eur, nested_port = _030009_nested()
    nested_port_vega = nested_port.calc(risk.IRVegaParallel)  # cell 3

    # cell 13: gs drops the swaps' empty vega frames; pricebt's swap vega is 0.0 (R2-8), so the rows stay
    frame = nested_port_vega.to_frame()
    assert list(frame.index) == ['EUR', 'USD', 'N/A'] and list(frame.columns) == ['5y', '10y']
    assert frame.loc[['EUR', 'USD']].to_numpy().tolist() == [[0.0, 0.0], [0.0, 0.0]]
    swaption_vega = float(swaption_1.calc(risk.IRVegaParallel))
    assert swaption_vega != 0.0 and frame.loc['N/A', '5y'] == pytest.approx(swaption_vega)


def test_030009_vega_to_frame_display_options_show_na():
    from pricebt.config import DisplayOptions  # cell 0

    _instruments, _eur, nested_port = _030009_nested()
    nested_port_vega = nested_port.calc(risk.IRVegaParallel)

    # cell 14: pass in display_options to show N/A values (none here: R2-8 swap vega is 0.0, not empty)
    shown = nested_port_vega.to_frame(display_options=DisplayOptions(show_na=True))
    pd.testing.assert_frame_equal(shown, nested_port_vega.to_frame())


# ------------------------------------------------------------------------------ 030010_portfolio_inter_leg_dependencies
# cells: 0 imports; 1 GsSession.use; 3 two IRSwaptions, one referencing the other's strike; 4 resolve
# and 5 the strikes (skipped: server-side cross-leg resolution)


def test_030010_inter_leg_reference_is_kept_verbatim_and_fails_loudly():
    swaption_1y5y = IRSwaption(PayReceive.Pay, '5y', Currency.USD, expiration_date='1y', strike="atm", name="foo")
    swaption_1y4y = IRSwaption(
        PayReceive.Pay, '4y', Currency.USD, expiration_date='1y', strike="=[foo].strike + 5bp", name="bar"
    )
    port = Portfolio((swaption_1y5y, swaption_1y4y))

    assert port['bar'].strike == "=[foo].strike + 5bp"
    with pytest.raises(AssetEvaluationError):  # never a silently wrong strike
        port.resolve()


# ------------------------------------------------------------------------------ 030011_portfolio_from_csv
# cells: 0 imports; 1 GsSession.use; 2 `IRSwap?` (skipped); 4 mappers; 5 from_csv + resolve(); 6 p[0].as_dict()


def _toy_date(text):
    return dt.datetime.strptime(text, '%d-%b-%y').date()


def test_030011_portfolio_from_csv():
    mappers = {
        'type': lambda row: IRSwap.type_.value,
        'asset_class': lambda row: IRSwap.asset_class.value,
        'effective_date': lambda row: _toy_date(row['EffectiveDate']),  # gs: 'EffectiveDate' (server-parsed)
        'pay_or_receive': lambda row: 'Pay' if float(row['Notional'].replace(',', '')) < 0 else 'Receive',
        'termination_date': lambda row: _toy_date(row['EndDate']),  # gs: 'EndDate'
        'fixed_rate': 'Coupon/Spread',
        'notional_amount': lambda row: float(row['Notional'].replace(',', '')),  # gs: 'Notional'
        'notional_currency': 'CCY1',
        'roll_convention': lambda row: 'IMM' if row['Roll Conv'] == 'IMM' else None,
        'fixed_rate_frequency': lambda row: '3m' if row['Frequency'] == 'QUARTERLY' else '6m',
    }

    p = Portfolio.from_csv(str(DATA / 'my_excel_portfolio.csv'), mappers)

    assert len(p) == 9 and all(type(i) is IRSwap for i in p)
    rows = [i.as_dict() for i in p]  # as_dict: an unset field is absent (reading it raises, DESIGN.md section 5.1 item 2)
    assert [r['pay_or_receive'] for r in rows] == [PayReceive.Receive] + [PayReceive.Pay] * 6 + [PayReceive.Receive, PayReceive.Pay]
    assert [r['fixed_rate_frequency'] for r in rows] == ['3m'] * 3 + ['6m'] * 4 + ['3m'] * 2
    assert [r.get('roll_convention') for r in rows] == [None] * 7 + ['IMM'] * 2
    assert not any('fixed_rate' in r for r in rows)  # the csv has no 'Coupon/Spread' column
    assert rows[0]['notional_currency'] == Currency.USD and rows[0]['notional_amount'] == 5e7

    p.resolve()
    as_dict = p[0].as_dict()

    assert as_dict['effective_date'] == dt.date(2015, 1, 27) and as_dict['termination_date'] == dt.date(2025, 1, 27)
    assert as_dict['quantity_'] == 1.0  # DEV-I2


# ------------------------------------------------------------------------------ tutorials/Portfolios
# cells: 1 GsSession.use; 5 two IRSwaptions; 7 Portfolio; 9 `portfolio.pricables = ...` (the gs typo);
# 11 lookup by position/object/name; 13 resolve + price().aggregate(); 14 calc((DollarPrice,
# IRDelta)); 16 one DollarPrice, both index orders; 18 aggregates


def test_tutorial_portfolios():
    swaption1 = IRSwaption(PayReceive.Pay, '5y', Currency.USD, expiration_date='3m', name='EUR-5y3m')
    swaption2 = IRSwaption(PayReceive.Pay, '5y', Currency.USD, expiration_date='6m', name='EUR-5y6m')

    portfolio = Portfolio((swaption1, swaption2))

    portfolio.pricables = (swaption1, swaption2)
    assert portfolio.priceables == (swaption1, swaption2)

    assert portfolio[0] is swaption1  # by position
    assert portfolio[swaption2] is swaption2  # by instrument object
    assert portfolio['EUR-5y6m'] is swaption2  # by instrument name

    portfolio.resolve()  # resolve
    total = portfolio.price().aggregate()  # price
    assert isinstance(total, FloatWithInfo) and total.unit == {'USD': 1}

    # calculate risk measures
    result = portfolio.calc((risk.DollarPrice, risk.IRDelta))

    by_measure = result[risk.DollarPrice]['EUR-5y3m']  # or
    by_name = result['EUR-5y3m'][risk.DollarPrice]
    assert float(by_measure) == float(by_name) == pytest.approx(float(portfolio['EUR-5y3m'].price()))

    price = result[risk.DollarPrice].aggregate()
    delta = result[risk.IRDelta].aggregate()
    assert float(price) == pytest.approx(float(total))  # USD: DollarPrice is Price
    _assert_bucketwise_sum(delta, result[risk.IRDelta][0], result[risk.IRDelta][1])
