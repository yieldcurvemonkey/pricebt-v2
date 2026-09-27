"""PricingService caches: the section 6.3 table, exercised via toylib.rates.EVAL_COUNTS/CSA_SEEN
(IMPLEMENTATION_PLAN.md P2.2; DESIGN.md sections 6.3, 6.4)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import toylib.rates as tr
from pricebt.instrument import IRSwap
from pricebt.risk.results import RiskKey
from pricebt.session import PricebtSession

ASSETS = Path(__file__).parent / "assets"


def _session():
    return PricebtSession.use(
        assets=[ASSETS / "toy_usd_irs.yaml", ASSETS / "toy_eur_irs.yaml", ASSETS / "toy_usd_swaption.yaml"],
        fx=ASSETS / "toy_fx.yaml",
    )


def _usd_swap(name="s"):
    return IRSwap("Pay", "10y", "USD", 1_000_000, name=name)


def test_market_evaluated_once_per_key_date_csa():
    session = _session()
    d = date(2024, 3, 4)
    swap = _usd_swap()

    session.pricing.unit_value(swap, d, "npv", None)
    session.pricing.unit_value(swap, d, "dv01", None)  # same (market_key, date, csa) -> no re-eval
    assert tr.EVAL_COUNTS["market"] == 1

    session.pricing.unit_value(swap, d, "npv", "CSA_X")  # different csa -> a distinct market entry
    assert tr.EVAL_COUNTS["market"] == 2
    session.pricing.unit_value(swap, d, "npv", "CSA_X")
    assert tr.EVAL_COUNTS["market"] == 2  # cached once evaluated


def test_hedge_unit_trade_and_scaled_copy_share_the_unit_value_cache():
    session = _session()
    d = date(2024, 3, 4)
    hedge = _usd_swap(name="hedge")

    session.pricing.unit_value(hedge, d, "npv", None)
    count_before = tr.EVAL_COUNTS["npv"]

    scaled = hedge.clone(quantity_=2.5)
    session.pricing.unit_value(scaled, d, "npv", None)
    assert tr.EVAL_COUNTS["npv"] == count_before  # unit value depends only on economic terms, not quantity_


def test_resolve_date_trade_is_built_on_the_resolution_market_even_when_first_requested_later():
    session = _session()
    asset = session.registry["toy_eur_irs"]  # build_on: resolve_date
    d0 = date(2024, 1, 2)  # the resolution date -- deliberately never priced directly
    d1 = date(2024, 6, 3)  # the FIRST requesting date

    swap = IRSwap("Pay", "10y", "EUR", 1_000_000, name="s")
    resolved_terms = {
        "effective_date": d0,
        "termination_date": date(2034, 1, 2),
        "fixed_rate": 0.02,
        "notional": 1_000_000.0,
    }
    swap._set_resolution(resolved_terms, RiskKey(None, d0, None, None, None, None), None, None)

    session.pricing.unit_value(swap, d1, "npv", None)
    assert ("market", d0, None) in tr.CSA_SEEN  # the trade was built on the RESOLUTION date's market
    assert ("market", d1, None) in tr.CSA_SEEN  # ... and npv still reads TODAY's market for `market`
    assert tr.EVAL_COUNTS["trade"] == 1

    market_count = tr.EVAL_COUNTS["market"]
    d2 = date(2024, 9, 1)
    session.pricing.unit_value(swap, d2, "npv", None)
    assert tr.EVAL_COUNTS["market"] == market_count + 1  # only d2's market is new; d0's trade is reused
    assert tr.EVAL_COUNTS["trade"] == 1  # trade cache hit: still keyed on (resolution date, resolution csa)
    assert ("market", d0, None) not in tr.CSA_SEEN[len(tr.CSA_SEEN) - 1 :]  # not re-fetched for d2's call

    _ = asset  # asset config identified for documentation; unit_value re-derives it via the instrument


def test_csa_reaches_market_expr_and_is_part_of_every_relevant_cache_key():
    session = _session()
    d = date(2024, 3, 4)
    swap = _usd_swap()

    session.pricing.unit_value(swap, d, "npv", "CSA_A")
    assert ("market", d, "CSA_A") in tr.CSA_SEEN
    count_before = tr.EVAL_COUNTS["market"]

    session.pricing.unit_value(swap, d, "npv", "CSA_B")
    assert tr.EVAL_COUNTS["market"] == count_before + 1  # a distinct csa is a distinct cache entry
    assert ("market", d, "CSA_B") in tr.CSA_SEEN

    resolved_a = session.pricing.resolve(swap, d, "CSA_A")
    resolved_b = session.pricing.resolve(swap, d, "CSA_B")
    assert resolved_a.resolved_terms == resolved_b.resolved_terms  # same economics
    assert resolved_a.resolution_csa != resolved_b.resolution_csa  # but csa travels with the resolution
