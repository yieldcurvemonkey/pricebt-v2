"""GenericEngine scenario: USD + EUR toy assets + toy FX (DESIGN.md section 12.4 row
`test_multi_currency.py`; section 7, MUST-4). A mixed-currency book with no `result_ccy` raises the
gs errors unchanged plus the DESIGN section 7 point 4 hint; with `result_ccy='USD'` every result
column equals a hand-computed FX conversion; cross-currency hedging works via
`IRDelta(currency='USD')`; `initial_value` seeding follows section 7 point 7's currency order.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from pricebt.backtests.actions import AddTradeAction, HedgeAction
from pricebt.backtests.generic_engine import GenericEngine
from pricebt.backtests.strategy import Strategy
from pricebt.backtests.triggers import DateTrigger, DateTriggerRequirements, PeriodicTrigger, PeriodicTriggerRequirements
from pricebt.instrument import IRSwap
from pricebt.risk import IRDelta, Price
from pricebt.session import PricebtSession

ASSETS = Path(__file__).parent / "assets"

D1, D2, D3 = date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)
SCALAR_DV01 = IRDelta(aggregation_level="Type")


def _session(**kwargs):
    return PricebtSession.use(
        assets=[ASSETS / "toy_usd_irs.yaml", ASSETS / "toy_eur_irs.yaml"], fx=ASSETS / "toy_fx.yaml", **kwargs
    )


def _usd_swap(name="usd", fixed_rate="ATM+30"):
    return IRSwap("Pay", "10y", "USD", 1_000_000, fixed_rate=fixed_rate, name=name)


def _eur_swap(name="eur", fixed_rate="ATM-20"):
    return IRSwap("Pay", "10y", "EUR", 1_000_000, fixed_rate=fixed_rate, name=name)


def test_mixed_book_without_result_ccy_raises_the_gs_errors_with_the_design_hint():
    _session()
    # Two currencies held on the SAME date -> the aggregate-level ValueError (gs: 'Cannot aggregate
    # results with different units for Price'; pricebt's own message per DESIGN.md section 8.2 is
    # 'FloatWithInfo unit mismatch' -- still the SAME error class/behaviour: it raises, unconverted).
    strategy_same_day = Strategy(initial_portfolio=[_usd_swap(), _eur_swap()], triggers=[])
    bt_same_day = GenericEngine().run_backtest(strategy_same_day, states=[D1], show_progress=False)
    with pytest.raises(ValueError):
        bt_same_day.result_summary

    # Two currencies held on DIFFERENT dates (never mixed within one date's risk aggregation, so the
    # ValueError above never fires) still raises in result_summary's cash step, WITH the hint --
    # DESIGN.md section 7 point 4's second bullet.
    _session()
    usd_leg = _usd_swap(name="usdswap")
    eur_leg = _eur_swap(name="eurswap")
    t1 = DateTrigger(
        trigger_requirements=DateTriggerRequirements(dates=[D1]),
        actions=[AddTradeAction(usd_leg, timedelta(days=1), name="USDLeg")],  # exits before D2
    )
    t2 = DateTrigger(
        trigger_requirements=DateTriggerRequirements(dates=[D2]), actions=[AddTradeAction(eur_leg, None, name="EURLeg")]
    )
    strategy = Strategy(initial_portfolio=None, triggers=[t1, t2])
    bt = GenericEngine().run_backtest(strategy, states=[D1, D2, D3], show_progress=False)
    assert not any(len(bt.portfolio_dict.get(d, ())) > 1 for d in (D1, D2, D3))  # never mixed on one date
    with pytest.raises(RuntimeError, match=r"Cannot aggregate cash in multiple currencies") as exc:
        bt.result_summary
    assert "pass result_ccy=" in str(exc.value)


def test_result_ccy_usd_matches_hand_computed_fx_conversion_on_every_column():
    session = _session()
    usd = _usd_swap()
    eur = _eur_swap()
    strategy = Strategy(initial_portfolio=[usd, eur], triggers=[])
    bt = GenericEngine().run_backtest(
        strategy, states=[D1], result_ccy="USD", risks=[SCALAR_DV01], show_progress=False
    )

    resolved_usd = session.pricing.resolve(usd, D1, None)
    resolved_eur = session.pricing.resolve(eur, D1, None)
    fx_eur_usd = session.pricing.fx("EUR", "USD", D1)
    usd_pv = float(session.pricing.value(resolved_usd, D1, Price, None))
    eur_pv_local = float(session.pricing.value(resolved_eur, D1, Price, None))
    usd_dv01 = float(session.pricing.value(resolved_usd, D1, SCALAR_DV01, None))
    eur_dv01_local = float(session.pricing.value(resolved_eur, D1, SCALAR_DV01, None))
    expected_price = usd_pv + eur_pv_local * fx_eur_usd
    expected_dv01 = usd_dv01 + eur_dv01_local * fx_eur_usd
    assert expected_price != pytest.approx(0.0)  # off-ATM on purpose: not a degenerate zero check

    row = bt.result_summary.loc[D1]
    price_risk = Price(currency="USD")
    dv01_risk = SCALAR_DV01(currency="USD")
    assert row[price_risk] == pytest.approx(expected_price)
    assert row[dv01_risk] == pytest.approx(expected_dv01)
    # Entry cash is the negative of the same converted PV; Total ties out to 0 on the entry date.
    assert row["Cumulative Cash"] == pytest.approx(-expected_price)
    assert row["Total"] == pytest.approx(0.0, abs=1e-6)


def test_cross_currency_hedging_with_irdelta_currency_usd():
    _session()
    eur_book = IRSwap("Pay", "10y", "EUR", 10_000_000, name="book")
    usd_hedge = IRSwap("Receive", "5y", "USD", 10_000_000, name="hedge")
    risk = IRDelta(aggregation_level="Type", currency="USD")
    trigger = PeriodicTrigger(
        trigger_requirements=PeriodicTriggerRequirements(frequency="1b", end_date=D3),
        actions=[HedgeAction(risk, usd_hedge, name="XHedge")],
    )
    strategy = Strategy(initial_portfolio=[eur_book], triggers=[trigger])
    bt = GenericEngine().run_backtest(strategy, states=[D1, D2, D3], show_progress=False)

    for d in (D1, D2, D3):
        # Same hedge algebra as test_engine_hedge.py (h = -r/u -> r + h*u == 0 exactly), now with
        # both sides FIRST converted into USD -- proving the currency parameter, not just the
        # measure, flows correctly into HedgeActionImpl's unit-matching/scaling.
        assert float(bt.results[d][risk].aggregate()) == pytest.approx(0.0, abs=1e-6)


def test_initial_value_currency_selection_order():
    # ATM (PV == 0 by construction) on purpose here: isolates the seeded initial_value in
    # cash_dict[D1] from any entry-cash contribution, which the FX-conversion test above already
    # covers with off-ATM swaps.
    atm_usd = lambda name: IRSwap("Pay", "10y", "USD", 1_000_000, fixed_rate="ATM", name=name)
    atm_eur = lambda name: IRSwap("Pay", "10y", "EUR", 1_000_000, fixed_rate="ATM", name=name)

    # (a) result_ccy given -> that currency, regardless of what's held.
    _session()
    strategy_a = Strategy(initial_portfolio=[atm_usd("usd_a"), atm_eur("eur_a")], triggers=[])
    bt_a = GenericEngine().run_backtest(strategy_a, states=[D1], result_ccy="USD", initial_value=500.0, show_progress=False)
    assert dict(bt_a.cash_dict[D1]) == {"USD": 500.0}

    # (b) no result_ccy, but PricebtSession(reporting_currency=...) set -> that currency.
    _session(reporting_currency="EUR")
    strategy_b = Strategy(initial_portfolio=[atm_usd("usd_b"), atm_eur("eur_b")], triggers=[])
    bt_b = GenericEngine().run_backtest(strategy_b, states=[D1], initial_value=500.0, show_progress=False)
    assert bt_b.cash_dict[D1]["EUR"] == 500.0

    # (c) neither given, but every tradable asset shares ONE currency -> that currency.
    PricebtSession.use(assets=[ASSETS / "toy_usd_irs.yaml"])
    strategy_c = Strategy(initial_portfolio=[atm_usd("usd_c")], triggers=[])
    bt_c = GenericEngine().run_backtest(strategy_c, states=[D1], initial_value=500.0, show_progress=False)
    assert dict(bt_c.cash_dict[D1]) == {"USD": 500.0}

    # (d) neither given, and assets span more than one currency -> raise.
    _session()
    strategy_d = Strategy(initial_portfolio=[atm_usd("usd_d"), atm_eur("eur_d")], triggers=[])
    with pytest.raises(ValueError, match="initial_value needs result_ccy or PricebtSession"):
        GenericEngine().run_backtest(strategy_d, states=[D1], initial_value=500.0, show_progress=False)
