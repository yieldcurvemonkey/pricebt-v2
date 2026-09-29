"""`CloseMarket` market-date override and the `PnlExplain` relative measure (docs/v2/IR_RISK_DESIGN.md
section 8, R2-30, R2-32; pricebt DEV-M1, DEV-M2).

- `PricingContext(pricing_date=F, market=CloseMarket(date=T))`: functions and `each_market` trades
  see the market (and FX) of T while `pricebt_date` stays F; resolution uses F's own market; T is in
  every cache key and in the results' `risk_key.market`.
- `PnlExplain(CloseMarket(date=T))` maps to a `returns: buckets` portfolio function that also gets
  `market_to` and `pricebt_to_date`; the toys revalue by risk factor (rows `IR`, `IR VOL`/`CREDIT`,
  then `CROSSES` = the rest), so the rows sum to Price on T's market minus Price, pricing date held
  (only the swaption toy has time value to hold: it values on `pricebt_date`).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
import yaml

import pricebt.markets as markets_module
from pricebt.errors import NotSupportedError
from pricebt.instrument import Bond, ConfigInstrument, IRSwap, IRSwaption
from pricebt.datetime import prev_business_date
from pricebt.markets import CloseMarket, PricingContext, close_market_date
from pricebt.markets.portfolio import Portfolio
from pricebt.risk import DataFrameWithInfo, IRDelta, IRFwdRate, IRSpotRate, MarketParameter, PnlExplain, PnlExplainClose, PnlExplainLive, PnlPredictLive, Price, aggregate_results
from pricebt.session import PricebtSession

ASSETS = Path(__file__).parent / "assets"
F, T1, T2 = date(2024, 1, 2), date(2024, 1, 9), date(2024, 2, 1)

# A probe asset whose market IS its date: every value below is a day count, so the dates each
# evaluation saw are read off exactly.
DATES = {
    "schema_version": 1,
    "asset": "dates",
    "instrument": "ConfigInstrument",
    "currency": "USD",
    "market": {"expr": "pricebt_date"},
    "resolve": {"expr": "{'resolved_on': market}"},
    "trade": {"expr": "market", "build_on": "each_market"},
    "functions": {
        "gap": {"expr": "float((market - pricebt_date).days)", "unit": "ccy"},
        "trade_gap": {"expr": "float((trade - pricebt_date).days)", "unit": "ccy_per_bp"},
        "resolved_gap": {"expr": "float((resolved['resolved_on'] - pricebt_date).days)", "unit": "bp"},
    },
    "portfolio_functions": {
        "ladder": {"expr": "{'gap': float((market - pricebt_date).days) * sum(weights)}", "unit": "ccy_per_bp", "returns": "buckets"},
        "explain": {
            "expr": "[{'mkt_type': 'FROM', 'value': float((market - pricebt_date).days)}, {'mkt_type': 'TO', 'value': float((market_to - pricebt_date).days)}, {'mkt_type': 'TO_DATE', 'value': float((pricebt_to_date - pricebt_date).days)}]",
            "unit": "ccy",
            "returns": "buckets",
        },
        "blind": {"expr": "{'x': sum(weights)}", "unit": "ccy", "returns": "buckets"},
    },
    "risk_measures": {"Price": "gap", "IRFwdRate": "trade_gap", "IRSpotRate": "resolved_gap", "IRDelta": {"bucketed": "ladder"}, "PnlExplain": "explain"},
}


def _toys():
    eur = yaml.safe_load((ASSETS / "toy_usd_irs_full.yaml").read_text().replace("USD", "EUR").replace("usd", "eur"))
    return PricebtSession.use(
        assets=[ASSETS / "toy_usd_irs_full.yaml", ASSETS / "toy_usd_swaption.yaml", ASSETS / "toy_usd_bond.yaml", eur],
        fx=ASSETS / "toy_fx.yaml",
    )


def _resolved(inst, d=F):
    with PricingContext(pricing_date=d):
        return inst.resolve(in_place=False).result()


def _toy_trades():
    return {
        "swap": _resolved(IRSwap(notional_currency="USD", termination_date="10y", pay_or_receive="Pay")),
        "swaption": _resolved(IRSwaption(notional_currency="USD", termination_date="10y", expiration_date="1y", pay_or_receive="Receive")),
        "bond": _resolved(Bond()),
    }


def _calc(inst, measure, d=F, market=None):
    with PricingContext(pricing_date=d, market=market):
        return inst.calc(measure).result()


def _rows(frame) -> pd.Series:
    return pd.DataFrame(frame).set_index("mkt_type")["value"]


# ------------------------------------------------------------------------------ DEV-M1: the override


def _fix_now(monkeypatch, now: datetime):
    """`pricebt.markets`'s "now" pinned to the aware instant `now` (the real clock never decides)."""

    class _Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz)

    monkeypatch.setattr(markets_module, "datetime", _Fixed)


def test_close_market_date_rolls_until_the_close_is_in(monkeypatch):
    """gs: a date's close is in once "now" in the location (default LDN) passes date + 24:00. D is
    a Tuesday; 18:00 and 21:00 New York are 23:00 D and 02:00 D+1 London."""
    d, ny = date(2026, 9, 29), ZoneInfo("America/New_York")
    _fix_now(monkeypatch, datetime(2026, 9, 29, 18, 0, tzinfo=ny))
    assert close_market_date(date=d) == prev_business_date(d) == date(2026, 9, 28) == CloseMarket(date=d).date
    _fix_now(monkeypatch, datetime(2026, 9, 29, 21, 0, tzinfo=ny))
    assert close_market_date(date=d) == d == CloseMarket(date=d).date  # London's D close is in
    assert close_market_date("NYC", d) == date(2026, 9, 28) == CloseMarket(date=d, location="NYC").date  # New York's is not
    assert CloseMarket(date=date(2026, 9, 30), check=False).date == date(2026, 9, 30)
    assert close_market_date(date=F) == F == CloseMarket(date=F).date
    with PricingContext(pricing_date=F):
        assert close_market_date() == F == CloseMarket().date
    assert CloseMarket(date=F) == CloseMarket(date=F) != CloseMarket(date=T1) and len({CloseMarket(date=F), CloseMarket(date=F)}) == 1


def test_close_market_location_is_ldn_by_default():
    """gs: the location defaults to LDN (market_location()) and shows in repr and to_dict; an
    unknown one is gs's ValueError. pricebt DEV-M1: the location is its code string."""
    assert CloseMarket(date=F).location == "LDN" and CloseMarket(date=F) == CloseMarket(date=F, location="LDN") != CloseMarket(date=F, location="NYC")
    assert repr(CloseMarket(date=F)) == "2024-01-02 (LDN)"
    assert CloseMarket(date=F).to_dict() == {"date": F, "location": "LDN", "marketType": "CloseMarket"}
    for call in (lambda: CloseMarket(date=F, location="XYZ"), lambda: close_market_date("XYZ", F)):
        with pytest.raises(ValueError, match="'XYZ' is not a valid PricingLocation"):
            call()


def test_future_dated_market_raises():
    """gs: PricingContext refuses a market dated after today (use a RollFwd scenario)."""
    with pytest.raises(ValueError, match="market dated in the future"):
        PricingContext(pricing_date=F, market=CloseMarket(date=date.today() + timedelta(days=400)))


def test_close_market_override_prices_on_that_dates_market():
    PricebtSession.use(assets=[DATES])
    inst = ConfigInstrument(pricebt_asset="dates")
    # functions and an each_market trade see T1's market; pricebt_date stays F
    assert float(_calc(inst, Price, market=CloseMarket(date=T1))) == (T1 - F).days
    assert float(_calc(inst, IRFwdRate, market=CloseMarket(date=T1))) == (T1 - F).days
    assert float(_calc(inst, Price)) == 0.0
    # an override on the pricing date itself is no override
    assert float(_calc(inst, Price, market=CloseMarket(date=F))) == 0.0

    _toys()
    for name, trade in _toy_trades().items():
        override = float(_calc(trade, Price, market=CloseMarket(date=T1)))
        assert override != pytest.approx(float(_calc(trade, Price)), abs=1.0), name
        if name == "swaption":
            # valued on pricebt_date (F): a week more time value than on T1 itself
            assert override - float(_calc(trade, Price, d=T1)) > 1.0
        else:
            # the swap and bond toys never read pricebt_date: T1's market is T1
            assert override == float(_calc(trade, Price, d=T1)), name
    # FX is market data too: a EUR swap's USD price uses T1's EURUSD
    eur = _resolved(IRSwap(notional_currency="EUR", termination_date="10y", pay_or_receive="Pay"))
    assert float(_calc(eur, Price(currency="USD"), market=CloseMarket(date=T1))) == pytest.approx(float(_calc(eur, Price(currency="USD"), d=T1)), rel=1e-12)


def _ladder_usd(priceable, d, market=None) -> float:
    with PricingContext(pricing_date=d, market=market):
        r = priceable.calc(IRDelta(currency="USD"))
        return float((r.aggregate() if isinstance(priceable, Portfolio) else r.result()).value.sum())


def test_bucketed_fx_uses_the_override_date():
    """The lazy per-instrument path and the group path convert with the override date's FX too."""
    _toys()
    eur = _resolved(IRSwap(notional_currency="EUR", termination_date="10y", pay_or_receive="Pay"))
    assert _ladder_usd(eur, F, CloseMarket(date=T1)) == pytest.approx(_ladder_usd(eur, T1), rel=1e-12)
    port = Portfolio((eur, eur.clone(name="b")))
    assert _ladder_usd(port, F, CloseMarket(date=T1)) == pytest.approx(_ladder_usd(port, T1), rel=1e-12)


def test_results_on_different_markets_never_aggregate():
    """gs: RiskKey.market takes part in the aggregation check. pricebt DEV-M1: it is the override
    date (None without one), on the scalar, lazy and group paths."""
    _toys()
    swap = _toy_trades()["swap"]
    over = CloseMarket(date=T1)
    plain, moved = _calc(swap, Price), _calc(swap, Price, market=over)
    assert (plain.risk_key.market, moved.risk_key.market, _calc(swap, Price, market=CloseMarket(date=F)).risk_key.market) == (None, T1, None)
    with pytest.raises(ValueError, match="different pricing keys"):
        aggregate_results([plain, moved])
    assert float(aggregate_results([plain, moved], allow_mismatch_risk_keys=True)) == pytest.approx(float(plain) + float(moved))
    with PricingContext(pricing_date=F):
        p_plain = Portfolio((swap,)).calc((Price, IRDelta))
    with PricingContext(pricing_date=F, market=over):
        p_moved = Portfolio((swap.clone(name="x"),)).calc((Price, IRDelta))
        ladder = swap.calc(IRDelta).result()
        group = Portfolio((swap, swap.clone(name="y"))).calc(IRDelta)
    assert ladder.risk_key.market == T1 and group[0].risk_key.market == T1 and group.aggregate().risk_key.market == T1
    for m in (Price, IRDelta):
        with pytest.raises(ValueError, match="different pricing keys"):
            (p_plain + p_moved)[m].aggregate()


def test_resolve_ignores_the_override():
    PricebtSession.use(assets=[DATES])
    inst = ConfigInstrument(pricebt_asset="dates")
    with PricingContext(pricing_date=F, market=CloseMarket(date=T1)):
        assert inst.resolve(in_place=False).result().resolved_terms == {"resolved_on": F}
        assert float(inst.calc(Price).result()) == (T1 - F).days  # priced on T1's market
        assert float(inst.calc(IRSpotRate).result()) == 0.0  # ... after resolving on F's own market

    _toys()
    swap = IRSwap(notional_currency="USD", termination_date="10y", pay_or_receive="Pay")
    with PricingContext(pricing_date=F, market=CloseMarket(date=T1)):
        under_override = swap.resolve(in_place=False).result().resolved_terms
    assert under_override == _resolved(swap).resolved_terms != _resolved(swap, T1).resolved_terms


def test_override_dates_never_share_a_cached_value():
    """Unit-value, trade, portfolio-value and group caches: one session, the plain date last again."""
    PricebtSession.use(assets=[DATES])
    inst = ConfigInstrument(pricebt_asset="dates")
    markets = (None, CloseMarket(date=T1), CloseMarket(date=T2), None)
    expected = [0.0, (T1 - F).days, (T2 - F).days, 0.0]
    assert [float(_calc(inst, Price, market=m)) for m in markets] == expected
    assert [float(_calc(inst, IRFwdRate, market=m)) for m in markets] == expected  # the each_market trade
    ladders = []
    for m in markets:
        with PricingContext(pricing_date=F, market=m):
            ladders.append(float(Portfolio((inst, inst)).calc(IRDelta).aggregate().value.sum()))
    assert ladders == [2 * v for v in expected]


# ------------------------------------------------------------------------------ DEV-M2: PnlExplain


def test_relative_function_sees_both_markets():
    PricebtSession.use(assets=[DATES])
    inst = ConfigInstrument(pricebt_asset="dates")
    # from T1's market (override) to T2's close, pricing date F held
    rows = _rows(_calc(inst, PnlExplain(CloseMarket(date=T2)), market=CloseMarket(date=T1)))
    assert rows.to_dict() == {"FROM": (T1 - F).days, "TO": (T2 - F).days, "TO_DATE": (T2 - F).days}


@pytest.mark.parametrize(
    "name, labels",
    [("swap", ["IR", "CROSSES"]), ("swaption", ["IR", "IR VOL", "CROSSES"]), ("bond", ["IR", "CREDIT", "CROSSES"])],
)
def test_rows_sum_to_the_price_change_holding_the_pricing_date(name, labels):
    """Rows = Price(F on T1's market) - Price(F): market moves only, no time. Only the swaption toy
    has time value to hold (it values on pricebt_date); the swap and bond toys are carry-free there
    (they value on the market's date), so for them Price(F on T1's market) == Price(T1)."""
    _toys()
    trade = _toy_trades()[name]
    frame = _calc(trade, PnlExplain(CloseMarket(date=T1)))
    rows = _rows(frame)
    assert list(rows.index) == labels and set(frame.mkt_asset[:-1]) == {"USD"} and frame.mkt_asset.iloc[-1] == ""
    on_t1_market = float(_calc(trade, Price, market=CloseMarket(date=T1)))
    assert rows.sum() == pytest.approx(on_t1_market - float(_calc(trade, Price)), rel=1e-12)
    assert rows.drop("CROSSES").abs().min() > 1.0  # every factor row moves (T1 is a week later)
    if name == "swap":
        assert rows["CROSSES"] == 0.0  # one factor, nothing crosses
    if name == "swaption":
        # the time component is outside the rows: the whole change to T1 differs from them by it
        assert float(_calc(trade, Price, d=T1)) - on_t1_market < -1.0


def test_two_targets_in_one_calc_never_share_a_value():
    a, b = PnlExplain(CloseMarket(date=T1)), PnlExplain(CloseMarket(date=T2))
    assert a != b and hash(a) != hash(b) and sorted([b, a]) == [a, b]
    # pricebt DEV-M2: the target is in the repr (gs: 'PnlExplain' for both), so two labels
    assert (repr(a), repr(b)) == ("PnlExplain(date:2024-01-09, location:LDN)", "PnlExplain(date:2024-02-01, location:LDN)")
    assert a == PnlExplain(CloseMarket(date=T1)) == PnlExplain(CloseMarket(date=T1, location="LDN")) and a.parameters == MarketParameter(T1, "LDN")

    _toys()
    port = Portfolio(tuple(_toy_trades().values()))
    with PricingContext(pricing_date=F):
        both = port.calc((a, b))
        joint = [both[m].aggregate() for m in (a, b)]
        pivot = both.to_frame(values="value", index="instrument_name", columns="risk_measure")
    assert list(pivot.columns) == [a, b] and len({str(c) for c in pivot.columns}) == 2  # two labels, not two "PnlExplain"
    solo = []
    for m in (a, b):
        _toys()  # a fresh session: nothing cached from the other target
        with PricingContext(pricing_date=F):
            solo.append(Portfolio(tuple(_toy_trades().values())).calc(m).aggregate())
    for j, s in zip(joint, solo):
        pd.testing.assert_series_equal(_rows(j), _rows(s))
    assert _rows(joint[0])["IR"] != pytest.approx(_rows(joint[1])["IR"], abs=1.0)


def test_quantity_scales_and_portfolio_aggregates_the_rows():
    _toys()
    trades = _toy_trades()
    explain = PnlExplain(CloseMarket(date=T1))
    unit = _rows(_calc(trades["swaption"], explain))
    scaled = trades["swaption"].clone(quantity_=2.5)
    pd.testing.assert_series_equal(_rows(_calc(scaled, explain)), 2.5 * unit)

    leaves = [*trades.values(), scaled]
    with PricingContext(pricing_date=F):
        total = Portfolio(tuple(leaves)).calc(explain).aggregate()  # the group path: one call per asset
    by_leaf = pd.concat([_rows(_calc(x, explain)) for x in leaves]).groupby(level=0, sort=False).sum()
    pd.testing.assert_series_equal(_rows(total).groupby(level=0, sort=False).sum(), by_leaf, rtol=1e-12)


def test_function_must_read_the_target():
    cfg = {**DATES, "risk_measures": {**DATES["risk_measures"], "PnlExplain": "blind"}}
    PricebtSession.use(assets=[cfg])
    with pytest.raises(NotSupportedError, match="function 'blind' references neither market_to nor pricebt_to_date"):
        _calc(ConfigInstrument(pricebt_asset="dates"), PnlExplain(CloseMarket(date=T1)))


def test_one_factor_explain_stays_a_frame():
    """pricebt DEV-M2: gs turns at most two rows of one mkt_type into a float; pricebt keeps the frame."""
    one_row = {"expr": "[{'mkt_type': 'IR', 'value': float((market_to - market).days)}]", "unit": "ccy", "returns": "buckets"}
    cfg = {**DATES, "portfolio_functions": {**DATES["portfolio_functions"], "explain": one_row}}
    PricebtSession.use(assets=[cfg])
    frame = _calc(ConfigInstrument(pricebt_asset="dates"), PnlExplain(CloseMarket(date=T1)))
    assert isinstance(frame, DataFrameWithInfo) and _rows(frame).to_dict() == {"IR": (T1 - F).days}


def test_pnl_explain_close_needs_an_override():
    PricebtSession.use(assets=[DATES])
    inst = ConfigInstrument(pricebt_asset="dates")
    assert PnlExplainClose().parameters == MarketParameter(None, "LDN") and repr(PnlExplainClose()) == "PnlExplain(location:LDN)" and PnlExplainClose() == PnlExplainClose()
    with pytest.raises(NotSupportedError, match="pricing date's own close"):
        _calc(inst, PnlExplainClose())
    # from T1's market back to F's own close
    rows = _rows(_calc(inst, PnlExplainClose(), market=CloseMarket(date=T1)))
    assert rows.to_dict() == {"FROM": (T1 - F).days, "TO": 0.0, "TO_DATE": 0.0}


def test_live_measures_raise():
    for live in (PnlExplainLive, PnlPredictLive):
        with pytest.raises(NotSupportedError, match="GS server-side"):
            live()
    with pytest.raises(NotSupportedError, match="only to a CloseMarket"):
        PnlExplain("LDN live")
    with pytest.raises(NotSupportedError, match="market"):
        PricingContext(market="an OverlayMarket")
