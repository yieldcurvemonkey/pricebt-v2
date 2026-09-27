"""Multi-currency pricing: FX, per-function currency, the bp-unit conversion error, and the
mixed-currency aggregate error (IMPLEMENTATION_PLAN.md P2.2; DESIGN.md section 7)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from pricebt.errors import ConfigError
from pricebt.instrument import ConfigInstrument, IRSwap
from pricebt.markets import PricingContext
from pricebt.markets.portfolio import Portfolio
from pricebt.risk import Annuity, DollarPrice, Price
from pricebt.session import PricebtSession

ASSETS = Path(__file__).parent / "assets"


def _session():
    return PricebtSession.use(
        assets=[ASSETS / "toy_usd_irs.yaml", ASSETS / "toy_eur_irs.yaml", ASSETS / "toy_usd_swaption.yaml"],
        fx=ASSETS / "toy_fx.yaml",
    )


def _config_only_session(cfg):
    return PricebtSession.use(assets=[cfg])


def test_fx_both_directions_and_same_currency_shortcut():
    session = _session()
    d = date(2024, 3, 4)
    eurusd = session.pricing.fx("EUR", "USD", d)
    usdeur = session.pricing.fx("USD", "EUR", d)
    assert eurusd == pytest.approx(1.0 / usdeur)
    assert session.pricing.fx("USD", "USD", d) == 1.0  # no evaluation needed, and none happens


def test_price_is_local_by_default_converts_on_request_dollarprice_matches():
    session = _session()
    d = date(2024, 3, 4)
    swap = IRSwap("Pay", "10y", "USD", 1_000_000, name="s")

    local = session.pricing.value(swap, d, Price, None)
    assert local.unit == {"USD": 1}

    same_as_local = session.pricing.value(swap, d, Price(currency="local"), None)
    assert float(same_as_local) == pytest.approx(float(local))

    eur = session.pricing.value(swap, d, Price(currency="EUR"), None)
    assert eur.unit == {"EUR": 1}
    assert float(eur) == pytest.approx(float(local) * session.pricing.fx("USD", "EUR", d))

    dollar = session.pricing.value(swap, d, DollarPrice, None)
    assert dollar.unit == {"USD": 1}
    assert float(dollar) == pytest.approx(float(local))  # already USD


def test_per_function_currency_overrides_the_asset_currency():
    cfg = {
        "schema_version": 1,
        "asset": "per_fn_ccy",
        "instrument": "ConfigInstrument",
        "currency": "USD",
        "market": {"expr": "1"},
        "functions": {"f": {"expr": "2.0", "unit": "ccy", "currency": "EUR"}},
        "risk_measures": {"Price": "f"},
    }
    _config_only_session(cfg)
    inst = ConfigInstrument(pricebt_asset="per_fn_ccy", name="x")
    val = PricebtSession.current.pricing.value(inst, date(2024, 1, 2), Price, None)
    assert val.unit == {"EUR": 1}  # the function's own currency:, not the asset's
    assert float(val) == pytest.approx(2.0)


def test_currency_request_on_a_bp_unit_measure_raises_config_error():
    cfg = {
        "schema_version": 1,
        "asset": "bp_ccy_test",
        "instrument": "ConfigInstrument",
        "currency": "USD",
        "market": {"expr": "1"},
        "functions": {
            "f": {"expr": "1.0", "unit": "ccy"},
            "rate": {"expr": "1.0", "unit": "bp"},
        },
        "risk_measures": {"Price": "f", "Annuity": "rate"},
    }
    _config_only_session(cfg)
    inst = ConfigInstrument(pricebt_asset="bp_ccy_test", name="x")
    with pytest.raises(ConfigError):
        PricebtSession.current.pricing.value(inst, date(2024, 1, 2), Annuity(currency="EUR"), None)


def test_mixed_currency_aggregate_raises_the_gs_value_error():
    session = _session()  # noqa: F841 -- constructs PricebtSession.current, used implicitly by .calc
    d = date(2024, 3, 4)
    usd_swap = IRSwap("Pay", "10y", "USD", 1_000_000, name="u")
    eur_swap = IRSwap("Pay", "10y", "EUR", 1_000_000, name="e")
    with PricingContext(d):
        result = Portfolio([usd_swap, eur_swap]).calc(Price)
    with pytest.raises(ValueError):
        result.aggregate()
