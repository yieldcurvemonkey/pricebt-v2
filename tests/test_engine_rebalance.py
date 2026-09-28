"""GenericEngine scenario: `RebalanceAction` with `size_parameter=size_attribute` (DESIGN.md section
12.4 row `test_engine_rebalance.py`). The sequence 100k -> 50k -> 50k books NOTHING on the third
rebalance (already at target size), and the held size stays 50k. Every expected value is
hand-derived from the rebalance algorithm itself (DESIGN.md section 9.4 / research/02 section 5.6),
never read back from the engine's own output.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from pricebt.backtests.actions import RebalanceAction
from pricebt.backtests.generic_engine import GenericEngine
from pricebt.backtests.strategy import Strategy
from pricebt.backtests.triggers import PeriodicTrigger, PeriodicTriggerRequirements
from pricebt.instrument import IRSwap
from pricebt.session import PricebtSession

ASSETS = Path(__file__).parent / "assets"

D1, D2, D3 = date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)  # Tue/Wed/Thu, no weekend gaps
UNIT_NOTIONAL = 100_000.0  # the size of ONE unit of the rebalanced priceable (quantity_=1)


def _session():
    return PricebtSession.use(assets=[ASSETS / "toy_usd_irs.yaml"])


def _resolved_reb_swap(session):
    """RebalanceAction requires an already-resolved priceable (`priceable.unresolved is None` ->
    raise). Resolved on D1 with a notional matching UNIT_NOTIONAL, so `unit_size` (the
    `notional_amount` of a quantity_=1 clone) is exactly UNIT_NOTIONAL for the whole run."""
    swap = IRSwap("Pay", "10y", "USD", UNIT_NOTIONAL, name="reb")
    return session.pricing.resolve(swap, D1, None)


def _seed_placeholder(name="reb"):
    """A zero-size placeholder with the SAME base name, held via `initial_portfolio`, purely so
    RebalanceActionImpl (generic_engine_action_impls.py) has an existing direction=+1 cash payment
    to attach each rebalance position's own eventual exit to -- without one it raises
    'Found no final cash payment to rebalance for trade.' Its 0 notional means it never
    contributes to `current_size`, so it doesn't perturb the 100k -> 50k -> 50k sequence."""
    return IRSwap("Pay", "10y", "USD", 0.0, name=name)


def test_100k_then_50k_then_50k_books_nothing_on_the_third_rebalance():
    session = _session()
    resolved_priceable = _resolved_reb_swap(session)
    targets = {D1: 100_000.0, D2: 50_000.0, D3: 50_000.0}
    action = RebalanceAction(
        resolved_priceable, "notional_amount", (lambda state, bt, ti: targets[state]), name="Rb"
    )
    trigger = PeriodicTrigger(
        trigger_requirements=PeriodicTriggerRequirements(frequency="1b", end_date=D3), actions=[action]
    )
    strategy = Strategy(initial_portfolio=[_seed_placeholder()], triggers=[trigger])
    bt = GenericEngine().run_backtest(strategy, states=[D1, D2, D3], show_progress=False)

    ledger = bt.trade_ledger()
    d1_name = next(n for n in ledger.index if n.endswith(f"_{D1}"))
    d2_name = next(n for n in ledger.index if n.endswith(f"_{D2}"))
    # D1: current_size starts at 0 (only the 0-notional seed is held) -> delta = +100k -> a fresh
    # full-size position (quantity_ = 100k / UNIT_NOTIONAL = 1.0).
    d1_trade = next(t for t in bt.portfolio_dict[D1] if t.name == d1_name)
    assert d1_trade.quantity_ == pytest.approx(100_000.0 / UNIT_NOTIONAL)
    # D2: current_size is now 100k (D1's position still held) -> delta = 50k - 100k = -50k ->
    # quantity_ = -50k / UNIT_NOTIONAL, a SECOND, separate slice (D1's position is never removed).
    d2_trade = next(t for t in bt.portfolio_dict[D2] if t.name == d2_name)
    assert d2_trade.quantity_ == pytest.approx(-50_000.0 / UNIT_NOTIONAL)
    # D3: current_size is 100k - 50k = 50k, already == target(D3) -> new_size - current_size == 0
    # -> nothing booked: no third ledger row, and the SAME two positions are still what's held.
    assert not any(n.endswith(f"_{D3}") for n in ledger.index)
    d3_names = {t.name for t in bt.portfolio_dict[D3] if t.name != "reb_2024-01-02"}
    assert d3_names == {d1_name, d2_name}

    # Hand-verify the held size on every date via the SAME size_parameter the action itself reads.
    for d, expected_size in targets.items():
        held = [t for t in bt.portfolio_dict[d] if t.name != "reb_2024-01-02"]
        total = sum(getattr(t, "notional_amount") for t in held)
        assert total == pytest.approx(expected_size)
