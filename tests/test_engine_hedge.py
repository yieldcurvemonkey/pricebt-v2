"""GenericEngine scenario: a 040310-style toy-EUR variant of `HedgeAction(IRDelta(aggregation_
level='Type'), hedge, csa_term='EUR-OIS')` (DESIGN.md section 12.4 row `test_engine_hedge.py`;
research/05 section 7.1). The book's IRDelta must be (exactly, by the hedge algebra: h = -r/u ->
r + h*u = 0) approximately 0 after each daily hedge; `pricebt_csa` (via `toylib.rates.CSA_SEEN`)
must be seen ONLY while the hedge is being resolved, never during later risk/price evaluation. Also
covers the `risk_transformation=ResultWithInfoAggregator()` variant.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import toylib.rates as tr
from pricebt.backtests.actions import HedgeAction
from pricebt.backtests.generic_engine import GenericEngine
from pricebt.backtests.strategy import Strategy
from pricebt.backtests.triggers import DateTrigger, DateTriggerRequirements, PeriodicTrigger, PeriodicTriggerRequirements
from pricebt.instrument import IRSwap
from pricebt.risk import IRDelta
from pricebt.risk.transform import ResultWithInfoAggregator
from pricebt.session import PricebtSession

ASSETS = Path(__file__).parent / "assets"

D1, D2, D3 = date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)
BOOK_IRDELTA = IRDelta(aggregation_level="Type")


def _session():
    return PricebtSession.use(assets=[ASSETS / "toy_eur_irs.yaml"])


def _book_swap():
    return IRSwap("Pay", "10y", "EUR", 10_000_000, name="book")


def _hedge_swap():
    return IRSwap("Receive", "5y", "EUR", 10_000_000, name="hedge")


def _run(risk_transformation=None):
    session = _session()
    trigger = PeriodicTrigger(
        trigger_requirements=PeriodicTriggerRequirements(frequency="1b", end_date=D3),
        actions=[
            HedgeAction(
                BOOK_IRDELTA, _hedge_swap(), csa_term="EUR-OIS", risk_transformation=risk_transformation, name="Hedge"
            )
        ],
    )
    strategy = Strategy(initial_portfolio=[_book_swap()], triggers=[trigger])
    return GenericEngine().run_backtest(strategy, states=[D1, D2, D3], risks=[BOOK_IRDELTA], show_progress=False)


def test_book_irdelta_is_approximately_zero_after_each_daily_hedge():
    bt = _run()
    for d in (D1, D2, D3):
        total_dv01 = float(bt.results[d][BOOK_IRDELTA].aggregate())
        # Hand fact, not a run-derived number: the hedge sizing IS current_risk / hedge_unit_risk,
        # so the combined post-hedge total is current_risk - current_risk == 0 exactly, up to
        # floating point noise on dollar-scale (thousands) sensitivities.
        assert total_dv01 == pytest.approx(0.0, abs=1e-2)


def test_risk_transformation_result_with_info_aggregator_variant_still_neutralises_the_book():
    bt = _run(risk_transformation=ResultWithInfoAggregator())
    for d in (D1, D2, D3):
        total_dv01 = float(bt.results[d][BOOK_IRDELTA].aggregate())
        assert total_dv01 == pytest.approx(0.0, abs=1e-2)


def test_pricebt_csa_is_seen_only_while_the_hedge_is_being_resolved():
    """research/05 section 7.1: "the later risk and price evaluation of the hedge does NOT use
    EUR-OIS ... only the strike does." Isolated with a ONE-SHOT hedge (fired only on D1, held
    unchanged through D2/D3 via trade_duration=None).

    `toylib.rates.CSA_SEEN` only instruments `market`/`resolve` (never a pricing function like
    npv/pv01), and DESIGN.md section 9.5 step 2's own missing-market pre-scan legitimately checks
    `has_market` under a HedgeAction's csa_term on EVERY grid date (not a bug) -- so the market
    CACHE genuinely holds an 'EUR-OIS' entry for every date. The verifiable, non-vacuous claims are
    therefore: (1) `resolve` itself -- which runs exactly once, when the hedge is actually
    constructed -- carries 'EUR-OIS' ONLY on its creation date, D1; and (2) D2/D3's later daily
    revaluation of the SAME held hedge genuinely evaluates its own SEPARATE market entry under the
    ambient (None) csa, rather than silently depending on the pre-scan's EUR-OIS-keyed one.
    """
    _session()
    trigger = DateTrigger(
        trigger_requirements=DateTriggerRequirements(dates=[D1]),
        actions=[HedgeAction(BOOK_IRDELTA, _hedge_swap(), csa_term="EUR-OIS", name="OneShotHedge")],
    )
    strategy = Strategy(initial_portfolio=[_book_swap()], triggers=[trigger])
    GenericEngine().run_backtest(strategy, states=[D1, D2, D3], risks=[BOOK_IRDELTA], show_progress=False)

    resolve_dates_under_hedge_csa = {d for fn, d, csa in tr.CSA_SEEN if fn == "resolve" and csa == "EUR-OIS"}
    assert resolve_dates_under_hedge_csa == {D1}
    assert ("market", D2, None) in tr.CSA_SEEN
    assert ("market", D3, None) in tr.CSA_SEEN
