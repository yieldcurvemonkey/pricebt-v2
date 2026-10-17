"""skills/pricebt-scenarios/scripts/scenario_grid.py on the toy assets: the grids' known answers
and shapes, the transform checks (passing on the toy configs, failing on a config whose transform
ignores the shock), and the CLI."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "pricebt-scenarios" / "scripts"))

import scenario_grid as sg  # noqa: E402

from pricebt.assets import load_asset, yamlio  # noqa: E402
from pricebt.instrument import IRSwap, IRSwaption  # noqa: E402
from pricebt.markets import PricingContext  # noqa: E402
from pricebt.markets.portfolio import Portfolio  # noqa: E402
from pricebt.session import PricebtSession  # noqa: E402

SWAPTION = ROOT / "tests" / "assets" / "toy_usd_swaption.yaml"
SWAP = ROOT / "tests" / "assets" / "toy_usd_irs_full.yaml"
D = date(2024, 3, 4)


@pytest.fixture
def toy():
    PricebtSession.use(assets=[SWAPTION, SWAP])
    PricingContext.current = PricingContext(D)


def _swaption(kind, strike="ATM", name=None):
    inst = IRSwaption(kind, "10y", "USD", expiration_date="1y", strike=strike, notional_amount=1e6, name=name)
    inst.resolve()
    return inst


def test_payoff_grid_of_a_straddle_is_a_v(toy):
    straddle = _swaption("Straddle")
    expiry = straddle.resolved_terms["expiration_date"]
    grid = sg.payoff_grid(straddle, [-50, -25, 0, 25, 50], roll_to=expiry)
    v = list(grid["total"])
    assert v[2] == pytest.approx(0.0, abs=1e-6) and v[0] > v[1] > v[2] < v[3] < v[4]
    with sg.RollFwd(date=expiry), sg.rate_shock(25):
        assert grid.loc[25, "total"] == pytest.approx(float(straddle.price()), rel=1e-12)


def test_payoff_grid_of_a_fly_has_legs_and_a_tent_total(toy):
    lo, mid, hi = _swaption("Pay", "ATM-50", "lo"), _swaption("Pay", "ATM", "mid"), _swaption("Pay", "ATM+50", "hi")
    fly = Portfolio([lo, mid.clone(quantity_=-2), hi])
    grid = sg.payoff_grid(fly, [-75, -25, 0, 25, 75], roll_to=mid.resolved_terms["expiration_date"])
    assert list(grid.columns) == ["lo", "mid", "hi", "total"]
    assert grid["total"].idxmax() == 0 and grid.loc[-75, "total"] == pytest.approx(0.0, abs=1e-6) and grid.loc[75, "total"] == pytest.approx(0.0, abs=1e-6)
    assert grid.loc[25, "total"] == pytest.approx(grid.loc[25, ["lo", "mid", "hi"]].sum(), rel=1e-12)


def test_grid_2d_is_rate_by_vol(toy):
    rec = _swaption("Receive")
    g = sg.grid_2d(rec, rates_bp=[-25, 0, 25], vols_bp=[-10, 0, 10])
    assert g.shape == (3, 3) and g.loc[0, 0] == pytest.approx(float(rec.price()), rel=1e-12)
    assert (g.diff(axis=1).iloc[:, 1:] > 0).all().all()  # long vol: value rises with vol
    assert g.loc[-25, 0] > g.loc[0, 0] > g.loc[25, 0]   # a receiver gains when rates fall


def test_roll_ladder_lifecycles_an_option_through_expiry(toy):
    """gs 03 lifecycling: before expiry an OTM receiver keeps time value; after it, it is worth 0."""
    otm = IRSwaption("Receive", "10y", "USD", expiration_date="1m", strike="ATM-20", notional_amount=1e6)
    otm.resolve()
    ladder = sg.roll_ladder(otm, ["0b", "10b", "44b"])
    assert list(ladder.index) == ["0b", "10b", "44b"]
    assert ladder.loc["0b", "total"] == pytest.approx(float(otm.price()), rel=1e-12)
    assert ladder.loc["0b", "total"] > ladder.loc["10b", "total"] > 0 and ladder.loc["44b", "total"] == 0.0


@pytest.mark.parametrize("inst", ["IRSwaption('Straddle', '10y', 'USD', expiration_date='1y')", "IRSwap('Pay', '10y', 'USD')"])
def test_check_transforms_passes_on_the_toy_configs(toy, inst):
    rows = sg.check_transforms(eval(inst, {"IRSwaption": IRSwaption, "IRSwap": IRSwap}))
    assert [r for r in rows if r["ok"] is False] == []
    assert sum(r["ok"] is True for r in rows) >= 6


def test_check_transforms_catches_a_transform_that_ignores_the_shock():
    raw = dict(yamlio.load_file(SWAPTION))
    raw["asset"] = "lazy_swaption"
    raw["scenarios"] = {**raw["scenarios"], "MarketDataShockBasedScenario": "market"}
    PricebtSession.use(assets=[load_asset(raw)])
    PricingContext.current = PricingContext(D)
    rows = {r["check"]: r for r in sg.check_transforms(_swaption("Straddle"))}
    assert rows["a +10bp rate shock moves the price"]["ok"] is False
    assert rows["(P(+1bp) - P(-1bp)) / dF equals IRDelta (gs 010204)"]["ok"] is False


def test_cli(capsys):
    args = ["--asset", str(SWAPTION), "--instrument", "IRSwaption('Straddle', '10y', 'USD', expiration_date='1y')", "--date", "2024-03-04"]
    assert sg.main(["check", *args]) == 0
    assert "010204" in capsys.readouterr().out
    assert sg.main(["payoff", *args, "--roll-to", "expiration_date", "--shocks=-50:50:50"]) == 0
    out = capsys.readouterr().out
    assert "shock_bp" in out and "-50.0" in out
