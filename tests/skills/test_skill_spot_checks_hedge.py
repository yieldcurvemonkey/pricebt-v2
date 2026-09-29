"""spot_check.py "trade repricing" on a HedgeAction strategy: the ledger names the scaled hedge
Portfolio ('Scaled_<hedge name>_<date>', gs parity) while portfolio_dict holds its instruments, so the check
reprices the booked Portfolio instead of FAILing with "not in portfolio_dict"."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

from pricebt.backtests.backtest_objects import BackTest
from pricebt.session import PricebtSession

ROOT = Path(__file__).resolve().parents[2]
for _s in ("pricebt-spot-checks", "pricebt-strategy-recipes"):
    sys.path.insert(0, str(ROOT / "skills" / _s / "scripts"))
import recipes  # noqa: E402
import spot_check  # noqa: E402

_SWAP = {"notional_currency": "USD", "notional_amount": 10_000_000}
SPEC = {
    "name": "toy-hedged-repricing",
    "assets": ["tests/assets/toy_usd_irs.yaml"],
    "instruments": {
        "primary": {"class": "IRSwap", "kwargs": {"pay_or_receive": "Pay", "termination_date": "10y", "fixed_rate": "ATM", **_SWAP}},
        # off-market fixed rate, so every hedge has a non-zero Open Value to reprice
        "hedge": {"class": "IRSwap", "kwargs": {"pay_or_receive": "Receive", "termination_date": "5y", "fixed_rate": 0.03, **_SWAP}},
    },
    "dates": {"start": date(2024, 1, 2), "end": date(2024, 1, 10), "frequency": "1b"},
    "archetype": "delta_hedged",
    "signal": {"type": "none"},
    "rebalance": {"frequency": "1b", "trade_duration": "next schedule"},
    "sizing": {"method": "notional", "notional": None},
    "costs": {"model": "constant", "level": 10.0},
    "risks_to_report": ["Price", "IRDeltaParallel"],
}


@pytest.fixture(scope="module")
def hedged():
    prev = PricebtSession.current  # module scope runs outside conftest's per-test isolation: restore it
    bt, _ = recipes.run(SPEC)
    yield bt, spot_check._fresh_pricing(PricebtSession.current)
    PricebtSession.current = prev


def test_hedge_rows_reprice_through_the_booked_portfolio(hedged):
    bt, pricing = hedged
    led = bt.trade_ledger()
    hedges = [n for n in led.index if n.startswith("Scaled_hedge_")]
    assert hedges
    for name in hedges:
        row = led.loc[name]
        port = spot_check._find(bt, name, row["Open"])
        assert port is not None and hasattr(port, "all_instruments")
        pv = spot_check._pv(pricing, port, row["Open"], bt.price_measure)
        assert abs(pv) > 1.0 and float(row["Open Value"]) == pytest.approx(-pv, rel=1e-6)
    res = spot_check.check_trade_repricing(bt, pricing, sample=len(led), seed=0)
    assert res.status == "PASS", res.detail


def test_a_wrong_hedge_open_value_still_fails(hedged, monkeypatch):
    bt, pricing = hedged
    led = bt.trade_ledger()
    name = next(n for n in led.index if n.startswith("Scaled_hedge_"))
    bad = led.copy()
    bad.loc[name, "Open Value"] = -float(bad.loc[name, "Open Value"])
    monkeypatch.setattr(BackTest, "trade_ledger", lambda self: bad)
    res = spot_check.check_trade_repricing(bt, pricing, sample=len(bad), seed=0)
    assert res.status == "FAIL" and name in res.detail
