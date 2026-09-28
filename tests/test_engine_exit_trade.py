"""GenericEngine scenario: `ExitTradeAction` by priceable name (DESIGN.md section 12.4 row
`test_engine_exit_trade.py`; DEV-E5, section 11). Matching is on `position_meta`, not gs's
crash-prone `name.split('_')`, so a name containing underscores must not confuse it, and a
held HedgeAction leg (no `position_meta`) must never be matched by name. A same-day in/out nets to
direction 0 in the trade ledger.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from pricebt.backtests.actions import AddTradeAction, ExitTradeAction, HedgeAction
from pricebt.backtests.generic_engine import GenericEngine
from pricebt.backtests.strategy import Strategy
from pricebt.backtests.triggers import DateTrigger, DateTriggerRequirements, PeriodicTrigger, PeriodicTriggerRequirements
from pricebt.instrument import IRSwap
from pricebt.risk import IRDelta, Price
from pricebt.session import PricebtSession

ASSETS = Path(__file__).parent / "assets"


def _session():
    return PricebtSession.use(assets=[ASSETS / "toy_usd_irs.yaml"])


def _swap(name, tenor="10y", notional=1_000_000):
    return IRSwap("Pay", tenor, "USD", notional, name=name)


def test_exit_by_name_with_underscores_closes_only_the_named_trade():
    _session()
    d1, d2, d3 = date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)
    swap_5y = _swap("rates_5y_payer", tenor="5y")
    swap_10y = _swap("rates_10y_payer", tenor="10y")
    entry = DateTrigger(
        trigger_requirements=DateTriggerRequirements(dates=[d1]),
        actions=[AddTradeAction([swap_5y, swap_10y], None, name="Enter")],
    )
    exit_ = DateTrigger(
        trigger_requirements=DateTriggerRequirements(dates=[d2]),
        # DEV-E5: matches on the ORIGINAL priceable name (position_meta[1]), underscores and all --
        # never gs's `name.split('_')[-2]`, which this exact multi-underscore name would break.
        actions=[ExitTradeAction(["rates_5y_payer"], name="Exit")],
    )
    strategy = Strategy(initial_portfolio=None, triggers=[entry, exit_])
    bt = GenericEngine().run_backtest(strategy, states=[d1, d2, d3], show_progress=False)

    ledger = bt.trade_ledger()
    five_y = ledger.loc[[n for n in ledger.index if "rates_5y_payer" in n][0]]
    ten_y = ledger.loc[[n for n in ledger.index if "rates_10y_payer" in n][0]]
    assert five_y["Status"] == "closed"
    assert five_y["Close"] == d2
    assert ten_y["Status"] != "closed"  # untouched: not named in priceable_names
    session = PricebtSession.current
    resolved = session.pricing.resolve(swap_5y, d1, None)
    expected_exit_pv = float(session.pricing.value(resolved, d2, Price, None))
    assert five_y["Close Value"] == pytest.approx(expected_exit_pv)


def test_exit_by_name_does_not_touch_a_simultaneously_held_hedge():
    _session()
    d1, d2, d3 = date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)
    book_swap = _swap("book_swap", tenor="10y", notional=10_000_000)
    entry = DateTrigger(
        trigger_requirements=DateTriggerRequirements(dates=[d1]),
        actions=[AddTradeAction(book_swap, None, name="Enter")],
    )
    hedge_trigger = PeriodicTrigger(
        trigger_requirements=PeriodicTriggerRequirements(frequency="1b", end_date=d3),
        actions=[HedgeAction(IRDelta(aggregation_level="Type"), _swap("the_hedge", tenor="10y"), name="HedgeIt")],
    )
    exit_ = DateTrigger(
        trigger_requirements=DateTriggerRequirements(dates=[d2]),
        actions=[ExitTradeAction(["book_swap"], name="Exit")],
    )
    strategy = Strategy(initial_portfolio=None, triggers=[entry, hedge_trigger, exit_])
    bt = GenericEngine().run_backtest(strategy, states=[d1, d2, d3], show_progress=False)

    ledger = bt.trade_ledger()
    book_row = ledger.loc[[n for n in ledger.index if n.startswith("book_swap") or "_book_swap_" in n][0]]
    assert book_row["Status"] == "closed"
    assert book_row["Close"] == d2
    # The hedge is never matched by name (position_meta is None on a hedge leg, DEV-E5): it must
    # still be present and actively re-hedging on d3, after the exit fired on d2.
    hedge_names = [n for n in ledger.index if "the_hedge" in n]
    assert len(hedge_names) == 3  # one fresh hedge resolved/rebooked on each of d1, d2, d3
    assert d3 in bt.portfolio_dict
    d3_hedge_legs = [t for t in bt.portfolio_dict[d3] if "the_hedge" in t.name]
    assert len(d3_hedge_legs) >= 1


def test_exit_trade_action_on_an_initial_portfolio_position_does_not_crash_dev_e17():
    # DEV-E17: an initial_portfolio position's own CashPayments carry no TransactionCostEntry
    # (DEV-E4's _resolve_initial_portfolio), unlike every action-created trade. Exiting one via
    # ExitTradeAction previously crashed with `list.remove(x): x not in list` when the impl tried
    # to unconditionally relocate a TCE that never existed.
    _session()
    d1, d2 = date(2024, 1, 2), date(2024, 1, 3)
    seed = _swap("seed")
    exit_ = DateTrigger(
        trigger_requirements=DateTriggerRequirements(dates=[d2]),
        actions=[ExitTradeAction(name="Exit")],  # no priceable_names -> exits everything held
    )
    strategy = Strategy(initial_portfolio=[seed], triggers=[exit_])
    bt = GenericEngine().run_backtest(strategy, states=[d1, d2], show_progress=False)  # must not raise

    ledger = bt.trade_ledger()
    row = ledger.loc[[n for n in ledger.index if "seed" in n][0]]
    assert row["Status"] == "closed"
    assert row["Close"] == d2
    session = PricebtSession.current
    resolved = session.pricing.resolve(seed, d1, None)
    expected_exit_pv = float(session.pricing.value(resolved, d2, Price, None))
    assert row["Close Value"] == pytest.approx(expected_exit_pv)


def test_same_day_entry_and_exit_gives_direction_zero_in_ledger():
    _session()
    d1 = date(2024, 1, 2)
    swap = _swap("same_day_swap", tenor="10y")
    trigger = DateTrigger(
        trigger_requirements=DateTriggerRequirements(dates=[d1]),
        # Both simple actions of ONE trigger, in list order: entered, then exited, on the SAME date.
        actions=[
            AddTradeAction(swap, None, name="Action1"),
            ExitTradeAction(["same_day_swap"], name="Action2"),
        ],
    )
    strategy = Strategy(initial_portfolio=None, triggers=[trigger])
    bt = GenericEngine().run_backtest(strategy, states=[d1], show_progress=False)

    ledger = bt.trade_ledger()
    row_name = next(n for n in ledger.index if "same_day_swap" in n)
    row = ledger.loc[row_name]
    assert row["Open"] == d1
    assert row["Close"] == d1
    assert row["Open Value"] == 0
    assert row["Close Value"] == 0
    assert row["Trade PnL"] == 0
    assert row["Status"] == "closed"
    # It never appears as a HELD position on d1 either: entry(-1) and exit(+1) cancel to 0 before
    # any pricing/holding bookkeeping treats it as live.
    assert not any("same_day_swap" in t.name for t in bt.portfolio_dict.get(d1, ()))
