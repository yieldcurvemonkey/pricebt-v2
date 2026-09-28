"""GenericEngine scenario: the toy swaption, the MUST-5 extensibility check (DESIGN.md section 12.4
row `test_engine_swaption.py`; section 13). `AddTradeAction(swaption, 'expiration_date')` closes the
position on EACH resolved expiry date (a per-instance date, not one global exit); a `Sell` price is
the negative of the equivalent `Buy` price; an `'A-50'` strike is pinned as a decimal AT RESOLVE
TIME, never re-read live on a later pricing date. Nothing here special-cases swaptions in the
engine -- the toy_usd_swaption.yaml config alone (already present for P1.5/P2.2) is what makes this
work, which is the point of MUST-5.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from dateutil.relativedelta import relativedelta

import toylib.rates as tr
import toylib.swaption as ts
from pricebt.backtests.actions import AddTradeAction
from pricebt.backtests.generic_engine import GenericEngine
from pricebt.backtests.strategy import Strategy
from pricebt.backtests.triggers import PeriodicTrigger, PeriodicTriggerRequirements
from pricebt.instrument import IRSwaption
from pricebt.risk import Price
from pricebt.session import PricebtSession

ASSETS = Path(__file__).parent / "assets"

D1 = date(2024, 1, 2)


def _session():
    return PricebtSession.use(assets=[ASSETS / "toy_usd_swaption.yaml"])


def _swaption(buy_sell, name, strike="A-50"):
    return IRSwaption(
        pay_or_receive="Pay",
        buy_sell=buy_sell,
        expiration_date="1y",
        termination_date="10y",
        notional_currency="USD",
        strike=strike,
        notional_amount=1_000_000,
        name=name,
    )


def test_sell_price_is_the_negative_of_the_equivalent_buy_price():
    session = _session()
    buy_resolved = session.pricing.resolve(_swaption("Buy", "buy"), D1, None)
    sell_resolved = session.pricing.resolve(_swaption("Sell", "sell"), D1, None)
    buy_pv = float(session.pricing.value(buy_resolved, D1, Price, None))
    sell_pv = float(session.pricing.value(sell_resolved, D1, Price, None))
    assert buy_pv != pytest.approx(0.0)  # not a degenerate ATM-style zero (this uses A-50, off market)
    assert sell_pv == pytest.approx(-buy_pv)


def test_a_minus_50_strike_is_pinned_as_a_decimal_at_resolve_time_not_re_read_live():
    session = _session()
    resolved = session.pricing.resolve(_swaption("Buy", "pinned"), D1, None)

    # Hand-derived (toylib formulas, never the engine's own output): the SAME expiration_date/
    # termination_date resolve() itself computes, minus 50bp.
    mkt = ts.market(D1, "USD", None)
    exp = D1 + relativedelta(years=1)
    term = exp + relativedelta(years=10)
    expected_strike = tr._par_rate(mkt.curve, exp, term) - 0.0050
    assert resolved.strike == pytest.approx(expected_strike)

    # Pricing the SAME resolved instrument on a LATER date must use this frozen strike, not a fresh
    # 'A-50' computed against that later date's own par rate (a different number, since the curve
    # moves day to day) -- proven by an independent hand computation from the frozen resolved_terms.
    later = date(2024, 3, 4)
    later_mkt = ts.market(later, "USD", None)
    later_par = tr._par_rate(later_mkt.curve, exp, term)
    assert later_par != pytest.approx(float(resolved.strike))  # the curve genuinely moved
    expected_later_pv = ts.npv(later_mkt, resolved.resolved_terms)
    actual_later_pv = float(session.pricing.value(resolved, later, Price, None))
    assert actual_later_pv == pytest.approx(expected_later_pv)


def test_add_trade_action_closes_each_position_on_its_own_resolved_expiration_date():
    session = _session()
    d1, d2 = D1, date(2024, 1, 3)
    later = date(2024, 3, 10)  # past both ~2-month expiries, so their off-grid exits get priced
    template = IRSwaption(
        pay_or_receive="Pay",
        buy_sell="Buy",
        expiration_date="2m",
        termination_date="10y",
        notional_currency="USD",
        strike="ATM",
        notional_amount=1_000_000,
        name="swopt",
    )
    trigger = PeriodicTrigger(
        trigger_requirements=PeriodicTriggerRequirements(frequency="1b", end_date=d2),
        actions=[AddTradeAction(template, "expiration_date")],
    )
    strategy = Strategy(initial_portfolio=None, triggers=[trigger])
    bt = GenericEngine().run_backtest(strategy, states=[d1, d2, later], show_progress=False)

    ledger = bt.trade_ledger()
    assert len(ledger) == 2
    row1 = ledger.loc[next(n for n in ledger.index if n.endswith(f"_{d1}"))]
    row2 = ledger.loc[next(n for n in ledger.index if n.endswith(f"_{d2}"))]

    resolved1 = session.pricing.resolve(template, d1, None)
    resolved2 = session.pricing.resolve(template, d2, None)
    # Each position's own expiration_date is resolved independently (a genuinely different date
    # per create date), not one shared global exit.
    assert resolved1.expiration_date == d1 + relativedelta(months=2)
    assert resolved2.expiration_date == d2 + relativedelta(months=2)
    assert resolved1.expiration_date != resolved2.expiration_date
    assert row1["Close"] == resolved1.expiration_date
    assert row2["Close"] == resolved2.expiration_date

    expected_exit_1 = float(session.pricing.value(resolved1, resolved1.expiration_date, Price, None))
    expected_exit_2 = float(session.pricing.value(resolved2, resolved2.expiration_date, Price, None))
    assert row1["Close Value"] == pytest.approx(expected_exit_1)
    assert row2["Close Value"] == pytest.approx(expected_exit_2)
