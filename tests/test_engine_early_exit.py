"""GenericEngine scenario: `EarlyExitPositionLimitScaledAction` (DESIGN.md section 12.4 row
`test_engine_early_exit.py`). `early_exits` caps a position's final date (only for a future order,
`order_date < cap`); `max_concurrent_pos` drops an ENTIRE order day once the running position count
would exceed the limit. This exercises fresh scenarios distinct from P3.5's own narrow regression
tests in test_engine_smoke.py (which guard the specific bug this action's implementation fixed).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from pricebt.backtests.actions import EarlyExitPositionLimitScaledAction, ScalingActionType
from pricebt.backtests.generic_engine import GenericEngine
from pricebt.backtests.strategy import Strategy
from pricebt.backtests.triggers import PeriodicTrigger, PeriodicTriggerRequirements
from pricebt.instrument import IRSwap
from pricebt.session import PricebtSession

ASSETS = Path(__file__).parent / "assets"


def _session():
    return PricebtSession.use(assets=[ASSETS / "toy_usd_irs.yaml"])


def _swap(name="ee"):
    return IRSwap("Pay", "10y", "USD", 1_000_000, name=name)


def test_early_exits_caps_the_final_date_of_a_future_position_but_not_an_on_or_after_position():
    _session()
    d1 = date(2024, 1, 2)  # order before the cap -> capped
    d2 = date(2024, 1, 9)  # order ON the cap date -> NOT capped (cap must be strictly AFTER order_date)
    cap = date(2024, 1, 9)
    action = EarlyExitPositionLimitScaledAction(
        _swap(), trade_duration="1m", scaling_type=ScalingActionType.size, scaling_level=1, early_exits=[cap]
    )
    trigger = PeriodicTrigger(
        trigger_requirements=PeriodicTriggerRequirements(frequency="1w", end_date=d2), actions=[action]
    )
    strategy = Strategy(initial_portfolio=None, triggers=[trigger])
    bt = GenericEngine().run_backtest(strategy, states=[d1, d2], show_progress=False)

    ledger = bt.trade_ledger()
    d1_row = ledger.loc[next(n for n in ledger.index if n.endswith(f"_{d1}"))]
    d2_row = ledger.loc[next(n for n in ledger.index if n.endswith(f"_{d2}"))]
    assert d1_row["Close"] == cap  # capped: cap (2024-01-09) < its natural ~1m exit (~2024-02-02)
    assert d2_row["Close"] != cap or d2_row["Close"] is None  # not capped: order_date == cap, not < cap
    assert d2_row["Close"] is None  # duration='1m' from d2 lands well past this 2-date backtest window


def test_max_concurrent_pos_drops_the_entire_order_day_once_the_limit_would_be_exceeded():
    _session()
    d1, d2, d3 = date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)  # Tue/Wed/Thu
    action = EarlyExitPositionLimitScaledAction(
        _swap(),
        trade_duration=None,  # never naturally exits -> positions purely accumulate
        scaling_type=ScalingActionType.size,
        scaling_level=1,
        max_concurrent_pos=2,
    )
    trigger = PeriodicTrigger(
        trigger_requirements=PeriodicTriggerRequirements(frequency="1b", end_date=d3), actions=[action]
    )
    strategy = Strategy(initial_portfolio=None, triggers=[trigger])
    bt = GenericEngine().run_backtest(strategy, states=[d1, d2, d3], show_progress=False)

    ledger = bt.trade_ledger()
    # d1 and d2 fill the limit (2); d3's whole order is dropped rather than only partially booked.
    assert len(ledger) == 2
    assert any(n.endswith(f"_{d1}") for n in ledger.index)
    assert any(n.endswith(f"_{d2}") for n in ledger.index)
    assert not any(n.endswith(f"_{d3}") for n in ledger.index)
    assert len(bt.portfolio_dict[d3]) == 2  # still just the two positions carried over from d1/d2
