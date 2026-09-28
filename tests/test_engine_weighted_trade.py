"""GenericEngine scenario: `AddWeightedTradeAction` over two toy swaps (DESIGN.md section 12.4 row
`test_engine_weighted_trade.py`). `quantity_i = |risk_i| / sum(|risk_j|) * total_size / unit_size_i`
is hand-derived from `PricingService` unit values (dv01 and PV), never read back from the engine's
own output; the resulting positions are named `Weighted_<name>` with independent per-instrument
cash entries.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from pricebt.backtests.actions import AddWeightedTradeAction
from pricebt.backtests.generic_engine import GenericEngine
from pricebt.backtests.strategy import Strategy
from pricebt.backtests.triggers import DateTrigger, DateTriggerRequirements
from pricebt.instrument import IRSwap
from pricebt.risk import IRDelta, Price
from pricebt.session import PricebtSession

ASSETS = Path(__file__).parent / "assets"

D1 = date(2024, 1, 2)
TOTAL_SIZE = 1_000_000.0
# Distinct tenors/spreads so dv01 (tenor-driven) and PV (fixed-rate-spread-driven) are independent
# and both nonzero -- 'ATM' would make PV trivially 0 and mask a scaling bug.
FIVE_Y = dict(pay_or_receive="Pay", termination_date="5y", notional_currency="USD", notional_amount=10_000_000, fixed_rate="ATM+25", name="fiveY")
TWENTY_Y = dict(pay_or_receive="Pay", termination_date="20y", notional_currency="USD", notional_amount=10_000_000, fixed_rate="ATM-15", name="twentyY")


def _session():
    return PricebtSession.use(assets=[ASSETS / "toy_usd_irs.yaml"])


def _swaps():
    return IRSwap(**FIVE_Y), IRSwap(**TWENTY_Y)


def test_quantities_split_by_dv01_weight_and_naming_and_cash_are_correct():
    _session()
    swap_a, swap_b = _swaps()
    action = AddWeightedTradeAction(
        [swap_a, swap_b], None, scaling_risk=IRDelta(aggregation_level="Type"), total_size=TOTAL_SIZE, name="Weighted"
    )
    trigger = DateTrigger(trigger_requirements=DateTriggerRequirements(dates=[D1]), actions=[action])
    strategy = Strategy(initial_portfolio=None, triggers=[trigger])
    # scaling_risk isn't auto-added to bt.risks (unlike a HedgeAction/StrategyRiskTrigger's OWN
    # `.risk`): the weighted-trade scaling machinery needs it explicitly requested, or its own
    # `sp.trades.calc(tuple(risks))` pre-pass never computes it and scaling silently no-ops.
    bt = GenericEngine().run_backtest(
        strategy, states=[D1], risks=[IRDelta(aggregation_level="Type")], show_progress=False
    )

    session = PricebtSession.current
    fresh_a, fresh_b = _swaps()
    resolved_a = session.pricing.resolve(fresh_a, D1, None)
    resolved_b = session.pricing.resolve(fresh_b, D1, None)
    dv01_a = abs(float(session.pricing.value(resolved_a, D1, IRDelta(aggregation_level="Type"), None)))
    dv01_b = abs(float(session.pricing.value(resolved_b, D1, IRDelta(aggregation_level="Type"), None)))
    total_risk = dv01_a + dv01_b
    unit_size_a = 10_000_000.0  # notional_amount attribute of ONE unit (quantity_=1)
    unit_size_b = 10_000_000.0
    expected_qty_a = (dv01_a / total_risk) * TOTAL_SIZE / unit_size_a
    expected_qty_b = (dv01_b / total_risk) * TOTAL_SIZE / unit_size_b

    trade_a = next(t for t in bt.portfolio_dict[D1] if "fiveY" in t.name)
    trade_b = next(t for t in bt.portfolio_dict[D1] if "twentyY" in t.name)
    assert trade_a.name.startswith("Weighted_")
    assert trade_b.name.startswith("Weighted_")
    assert trade_a.quantity_ == pytest.approx(expected_qty_a)
    assert trade_b.quantity_ == pytest.approx(expected_qty_b)

    # Per-instrument cash: each entry payment == -(unit PV * that instrument's own quantity_).
    unit_pv_a = float(session.pricing.value(resolved_a, D1, Price, None))
    unit_pv_b = float(session.pricing.value(resolved_b, D1, Price, None))
    ledger = bt.trade_ledger()
    row_a = ledger.loc[[n for n in ledger.index if "fiveY" in n][0]]
    row_b = ledger.loc[[n for n in ledger.index if "twentyY" in n][0]]
    assert row_a["Open Value"] == pytest.approx(-unit_pv_a * expected_qty_a)
    assert row_b["Open Value"] == pytest.approx(-unit_pv_b * expected_qty_b)


def test_zero_total_risk_skips_every_instrument_and_books_no_transaction_cost():
    """DEV-E3: when every instrument's dv01 happens to be (numerically) zero, the whole weighted
    trade is skipped -- nothing added to the book, and the already-booked entry/exit TCEs for it
    are removed rather than silently charging a cost for a position that never existed."""
    _session()
    zero_notional = dict(FIVE_Y, notional_amount=0.0, name="zeroFive")
    other_zero = dict(TWENTY_Y, notional_amount=0.0, name="zeroTwenty")
    action = AddWeightedTradeAction(
        [IRSwap(**zero_notional), IRSwap(**other_zero)],
        None,
        scaling_risk=IRDelta(aggregation_level="Type"),
        total_size=TOTAL_SIZE,
        name="ZeroWeighted",
    )
    trigger = DateTrigger(trigger_requirements=DateTriggerRequirements(dates=[D1]), actions=[action])
    strategy = Strategy(initial_portfolio=None, triggers=[trigger])
    bt = GenericEngine().run_backtest(
        strategy, states=[D1], risks=[IRDelta(aggregation_level="Type")], show_progress=False
    )

    assert len(bt.portfolio_dict.get(D1, ())) == 0
    assert len(bt.trade_ledger()) == 0
    assert sum(-tce.get_final_cost() for tces in bt.transaction_cost_entries.values() for tce in tces) == 0
