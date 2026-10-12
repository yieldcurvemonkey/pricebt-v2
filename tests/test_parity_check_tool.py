"""tools/parity_check.py on known answers: it reports nothing for identical set-ups and for a market
moved into a provider, and it reports a planted difference (a checker that cannot fail proves
nothing)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from pricebt.assets import yamlio
from pricebt.instrument import IRSwaption
from test_market_provider import MARKETS, on_provider

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "tests" / "assets"


@pytest.fixture
def parity(monkeypatch):
    """A fresh copy of the tool, with its repository-relative default paths made absolute."""
    spec = importlib.util.spec_from_file_location("parity_check", ROOT / "tools" / "parity_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.OLD = dict(assets=[ASSETS / "toy_usd_irs.yaml"])
    module.NEW = dict(assets=[ASSETS / "toy_usd_irs.yaml"])
    return module


def test_identical_set_ups_have_no_difference(parity, capsys):
    assert parity.main() == 0
    assert "216 values compared, 0 difference(s)" in capsys.readouterr().out  # the number the prompt quotes


def test_a_market_moved_into_a_provider_has_no_difference(parity):
    parity.NEW = dict(markets=[MARKETS], assets=[on_provider("toy_usd_irs.yaml", "toy_usd_ois")])
    assert parity.main() == 0


def test_a_planted_difference_is_reported(parity, capsys):
    broken = dict(yamlio.load_file(ASSETS / "toy_usd_irs.yaml"))
    broken["functions"] = {**broken["functions"], "dv01": {"expr": "tr.pv01(market, trade) * 1.0001", "unit": "ccy_per_bp"}}
    parity.NEW = dict(assets=[broken])
    assert parity.main() == 1
    out = capsys.readouterr().out
    assert "/functions.dv01: OLD" in out and "8 difference(s)" in out


def test_an_extra_resolved_term_is_a_difference_unless_ignored(parity):
    extra = dict(yamlio.load_file(ASSETS / "toy_usd_irs.yaml"))
    extra["resolve"] = {"expr": '{**tr.resolve_swap(market, kwargs), "curve": "sofr:cme"}'}
    parity.NEW = dict(assets=[extra])
    assert parity.main() == 1
    parity.IGNORED_RESOLVED_KEYS = {"curve"}
    assert parity.main() == 0


def test_frames_portfolio_functions_and_a_relative_function_are_compared(parity):
    """The swaption config has a Cashflows frame, three ladders and a PnlExplain function reading market_to."""
    make = lambda: IRSwaption("Pay", "10y", "USD", expiration_date="1y", strike="ATM", notional_amount=1e6, buy_sell="Buy")  # noqa: E731
    parity.OLD = dict(assets=[ASSETS / "toy_usd_swaption.yaml"])
    parity.NEW = dict(markets=[MARKETS], assets=[on_provider("toy_usd_swaption.yaml", "toy_usd_swaption_vol")])
    parity.CASES = [("payer swaption", make, make)]
    snap = parity.snapshot(parity.OLD, make, parity.DATES[0], parity.DATES[1])
    assert any(k.startswith("frame.Cashflows") for k in snap) and any(k.startswith("portfolio_functions.") for k in snap)
    assert not [k for k, v in snap.items() if isinstance(v, str) and v.startswith("ERROR")]
    assert parity.main() == 0
