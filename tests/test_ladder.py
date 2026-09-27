"""Bucketed (vector) values: the group ladder equals the sum of per-trade ladders, and the group
function is called exactly once per (asset, date) group -- including after a PRR is rebuilt from
`.futures` and after a `+` (IMPLEMENTATION_PLAN.md P2.2; DESIGN.md section 8.2)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import toylib.rates as tr
from pricebt.instrument import IRSwap
from pricebt.markets import PricingContext
from pricebt.markets.portfolio import Portfolio
from pricebt.risk import IRDelta
from pricebt.risk.results import PortfolioRiskResult
from pricebt.session import PricebtSession

ASSETS = Path(__file__).parent / "assets"
_BUCKET_COLS = ["mkt_type", "mkt_asset", "mkt_class", "mkt_point", "mkt_quoting_style"]


def _session():
    return PricebtSession.use(
        assets=[ASSETS / "toy_usd_irs.yaml", ASSETS / "toy_eur_irs.yaml", ASSETS / "toy_usd_swaption.yaml"],
        fx=ASSETS / "toy_fx.yaml",
    )


def _pair():
    a = IRSwap("Pay", "5y", "USD", 1_000_000, name="a", quantity_=2.5)
    b = IRSwap("Pay", "10y", "USD", 1_000_000, name="b", quantity_=-0.7)
    return a, b


def test_group_ladder_equals_sum_of_per_trade_ladders():
    session = _session()
    d = date(2024, 3, 4)
    a, b = _pair()

    with PricingContext(d):
        group_result = Portfolio([a, b]).calc(IRDelta)
    group_df = group_result.aggregate()

    solo_a = session.pricing.value(a, d, IRDelta, None).result()
    solo_b = session.pricing.value(b, d, IRDelta, None).result()
    manual = pd.concat([solo_a, solo_b]).groupby(_BUCKET_COLS, sort=False, as_index=False)["value"].sum()

    merged = group_df.merge(manual, on=_BUCKET_COLS, suffixes=("_group", "_manual"))
    assert len(merged) == len(group_df) == len(manual)
    for _, row in merged.iterrows():
        assert row["value_group"] == pytest.approx(row["value_manual"], rel=1e-9)


def test_delta_ladder_called_once_per_asset_date_across_rebuild_and_add():
    _session()
    d = date(2024, 3, 4)
    a, b = _pair()

    with PricingContext(d):
        prr = Portfolio([a, b]).calc(IRDelta)
    prr.aggregate()
    count = tr.EVAL_COUNTS["delta_ladder"]
    assert count == 1

    rebuilt = PortfolioRiskResult(prr.portfolio, prr.risk_measures, prr.futures)
    rebuilt.aggregate()
    assert tr.EVAL_COUNTS["delta_ladder"] == count  # same group members -> cache hit, no re-evaluation

    with PricingContext(d):
        prr_a = Portfolio([a]).calc(IRDelta)
        prr_b = Portfolio([b]).calc(IRDelta)
    summed = prr_a + prr_b
    summed.aggregate()
    assert tr.EVAL_COUNTS["delta_ladder"] == count  # [a, b] regrouped -> the SAME group key -> cache hit
