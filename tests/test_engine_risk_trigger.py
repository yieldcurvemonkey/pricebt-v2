"""GenericEngine scenario: a 040305-style `StrategyRiskTrigger` + `[ExitTradeAction(),
AddTradeAction(...)]` (DESIGN.md section 12.4 row `test_engine_risk_trigger.py`; DEV-E1, section
11). A path-dependent trigger must be evaluated AFTER the day's risks are ensured, or a second fire
within the same run is silently skipped: after the first rebalance replaces the held swap with a
fresh one, the SAME trigger must still see that fresh swap's own risk on the NEXT date -- not a
stale/empty result -- and fire again.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from pricebt.backtests.actions import AddTradeAction, ExitTradeAction
from pricebt.backtests.generic_engine import GenericEngine
from pricebt.backtests.strategy import Strategy
from pricebt.backtests.triggers import RiskTriggerRequirements, StrategyRiskTrigger, TriggerDirection
from pricebt.instrument import IRSwap
from pricebt.risk import IRDelta
from pricebt.session import PricebtSession

ASSETS = Path(__file__).parent / "assets"

D1, D2, D3 = date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)
SCALAR_DV01 = IRDelta(aggregation_level="Type")


def _session():
    return PricebtSession.use(assets=[ASSETS / "toy_usd_irs.yaml"])


def _swap(name):
    return IRSwap("Pay", "10y", "USD", 5_000_000, name=name)


def test_the_trigger_fires_again_on_every_date_after_the_first_rebalance():
    session = _session()
    # Hand-derived threshold: each day's fresh 5mm 10y payer swap has a dv01 of roughly 4,080-4,090
    # USD/bp (computed directly from PricingService, not read back from a run); half of the
    # SMALLEST of the three days' values is comfortably below all three, so a book holding exactly
    # one such swap trips the ABOVE trigger on every one of D1/D2/D3.
    daily_dv01 = [
        float(session.pricing.value(session.pricing.resolve(_swap("probe"), d, None), d, SCALAR_DV01, None))
        for d in (D1, D2, D3)
    ]
    threshold = min(daily_dv01) * 0.5

    trigger = StrategyRiskTrigger(
        trigger_requirements=RiskTriggerRequirements(risk=SCALAR_DV01, trigger_level=threshold, direction=TriggerDirection.ABOVE),
        actions=[ExitTradeAction(), AddTradeAction(_swap("swap"), None, name="Rebalance")],
    )
    strategy = Strategy(initial_portfolio=[_swap("seed")], triggers=[trigger])
    bt = GenericEngine().run_backtest(strategy, states=[D1, D2, D3], show_progress=False)

    ledger = bt.trade_ledger()
    # The seed is exited same-day on D1 (direction 0: entered by initial_portfolio, exited by the
    # trigger's ExitTradeAction on the very same date) -- test_engine_exit_trade.py owns that
    # property in depth; here it just confirms the seed is gone.
    seed_row = ledger.loc[next(n for n in ledger.index if n.startswith("seed"))]
    assert seed_row["Open"] == seed_row["Close"] == D1

    # D1, D2 AND D3 each produced their OWN fresh 'Rebalance_swap_<date>' position: this is the
    # DEV-E1 proof -- D2 and D3 only fire because the engine ensures each date's risk (including
    # the PREVIOUS day's newly-added swap) before evaluating the trigger, not after.
    rebalance_rows = [n for n in ledger.index if n.startswith("Rebalance_swap_")]
    assert len(rebalance_rows) == 3
    for d in (D1, D2, D3):
        assert any(n.endswith(f"_{d}") for n in rebalance_rows)
    # D1's and D2's rebalance swaps are each closed the NEXT day (exited by the following firing);
    # D3's is still open at the backtest end.
    d1_name = next(n for n in rebalance_rows if n.endswith(f"_{D1}"))
    d2_name = next(n for n in rebalance_rows if n.endswith(f"_{D2}"))
    d3_name = next(n for n in rebalance_rows if n.endswith(f"_{D3}"))
    assert ledger.loc[d1_name, "Close"] == D2
    assert ledger.loc[d2_name, "Close"] == D3
    assert ledger.loc[d3_name, "Close"] is None
