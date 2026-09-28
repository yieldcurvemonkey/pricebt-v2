"""GenericEngine scenario: `AddScaledTradeAction` sized by `size`, by a `risk_measure`, and by NAV,
plus the `dated_priceables` override (DESIGN.md section 12.4 row `test_engine_scaled_add.py`).
Every expected quantity/value is hand-derived from `PricingService` unit values, never read back
from the engine's own output.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from pricebt.backtests.actions import AddScaledTradeAction, ScalingActionType
from pricebt.backtests.generic_engine import GenericEngine
from pricebt.backtests.strategy import Strategy
from pricebt.backtests.triggers import DateTrigger, DateTriggerRequirements
from pricebt.instrument import IRSwap
from pricebt.risk import IRDelta, Price
from pricebt.session import PricebtSession

ASSETS = Path(__file__).parent / "assets"


def _session():
    return PricebtSession.use(assets=[ASSETS / "toy_usd_irs.yaml"])


def _swap(name="scaled", notional=1_000_000, fixed_rate="ATM"):
    return IRSwap("Pay", "10y", "USD", notional, fixed_rate=fixed_rate, name=name)


def _run(action, d1):
    trigger = DateTrigger(trigger_requirements=DateTriggerRequirements(dates=[d1]), actions=[action])
    strategy = Strategy(initial_portfolio=None, triggers=[trigger])
    return GenericEngine().run_backtest(strategy, states=[d1], show_progress=False)


def test_scaled_by_size_multiplies_notional_by_the_scaling_level():
    _session()
    d1 = date(2024, 1, 2)
    # Off-ATM on purpose: an ATM entry's PV is 0 by construction, which would make ANY scaling
    # level (including a wrong one) produce the same -0.0 Open Value and hide a scaling bug.
    swap = _swap(fixed_rate="ATM+50")
    action = AddScaledTradeAction(
        swap, None, scaling_type=ScalingActionType.size, scaling_level=2.5, name="Sized"
    )
    bt = _run(action, d1)

    session = PricebtSession.current
    resolved = session.pricing.resolve(swap, d1, None)
    unit_pv = float(session.pricing.value(resolved, d1, Price, None))
    ledger = bt.trade_ledger()
    assert len(ledger) == 1
    # 2.5x size -> 2.5x the entry cash outflow of one unit.
    assert ledger.iloc[0]["Open Value"] == pytest.approx(-unit_pv * 2.5)


def test_scaled_by_risk_measure_sizes_so_total_dv01_hits_the_level():
    _session()
    d1 = date(2024, 1, 2)
    target_dv01 = 1_500.0  # USD per 1bp -- a level chosen independent of any run's own output
    scalar_dv01 = IRDelta(aggregation_level="Type")  # scalar form (DESIGN.md section 8.1 rule 5)
    action = AddScaledTradeAction(
        _swap(),
        None,
        scaling_type=ScalingActionType.risk_measure,
        scaling_risk=scalar_dv01,
        scaling_level=target_dv01,
        name="RiskSized",
    )
    trigger = DateTrigger(trigger_requirements=DateTriggerRequirements(dates=[d1]), actions=[action])
    strategy = Strategy(initial_portfolio=None, triggers=[trigger])
    # scalar_dv01 isn't auto-added to bt.risks (only a HedgeAction/StrategyRiskTrigger's OWN `.risk`
    # is): request it explicitly so the book's post-trade dv01 is actually computed into results.
    bt = GenericEngine().run_backtest(strategy, states=[d1], risks=[scalar_dv01], show_progress=False)

    session = PricebtSession.current
    resolved = session.pricing.resolve(_swap(), d1, None)
    unit_dv01 = float(session.pricing.value(resolved, d1, scalar_dv01, None))
    expected_quantity = target_dv01 / unit_dv01

    ledger = bt.trade_ledger()
    name = ledger.index[0]
    trade = next(t for t in bt.portfolio_dict[d1] if t.name == name)
    assert trade.quantity_ == pytest.approx(expected_quantity)
    # And the book's OWN measured dv01 on d1 really does equal the requested level (not just
    # arithmetically implied): proves the engine actually applied that scale, not merely echoed it.
    measured_dv01 = float(bt.results[d1][scalar_dv01].aggregate())
    assert measured_dv01 == pytest.approx(target_dv01, rel=1e-6)


def test_scaled_by_nav_spends_the_whole_cash_budget_on_entry():
    """A single order date with trade_duration=None (final date beyond the backtest, so no unwind
    is ever priced) and zero transaction costs makes `_nav_scale_orders`'s two-pass algorithm exact
    algebra: scale = cash / unit_price, so the entry PV == the NAV budget exactly."""
    _session()
    d1 = date(2024, 1, 2)
    cash_budget = 250_000.0
    # Receive + an above-market fixed rate -> a strictly positive unit PV (an ATM swap's PV is 0 by
    # construction, which would make cash/price divide-by-zero -- deliberately avoided).
    swap = IRSwap("Receive", "10y", "USD", 1_000_000, fixed_rate="ATM+50", name="nav_swap")
    action = AddScaledTradeAction(
        swap, None, scaling_type=ScalingActionType.NAV, scaling_level=cash_budget, name="NavSized"
    )
    bt = _run(action, d1)

    ledger = bt.trade_ledger()
    assert len(ledger) == 1
    assert ledger.iloc[0]["Open Value"] == pytest.approx(-cash_budget)


def test_dated_priceables_overrides_the_default_priceable_per_order_date():
    _session()
    d1, d2 = date(2024, 1, 2), date(2024, 1, 3)
    default_swap = _swap(name="default_swap", notional=1_000_000)
    d1_swap = _swap(name="d1_swap", notional=2_000_000)
    d2_swap = _swap(name="d2_swap", notional=5_000_000)
    action = AddScaledTradeAction(
        default_swap,
        None,
        scaling_type=ScalingActionType.size,
        scaling_level=1,
        name="Dated",
        dated_priceables={d1: [d1_swap], d2: [d2_swap]},
    )
    trigger = DateTrigger(trigger_requirements=DateTriggerRequirements(dates=[d1, d2]), actions=[action])
    strategy = Strategy(initial_portfolio=None, triggers=[trigger])
    bt = GenericEngine().run_backtest(strategy, states=[d1, d2], show_progress=False)

    ledger = bt.trade_ledger()
    assert any("d1_swap" in n for n in ledger.index)
    assert any("d2_swap" in n for n in ledger.index)
    assert not any("default_swap" in n for n in ledger.index)  # the override replaces it, every date
