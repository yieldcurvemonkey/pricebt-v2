"""Tests for pricebt.session: PricebtSession, GsSession, Environment (DESIGN.md section 6.1,
IMPLEMENTATION_PLAN.md P2.3).

`PricebtSession.__init__` constructs `pricing.PricingService(registry, fx, tz=..., eod_time=...)`
via a function-level import of `pricebt.assets.pricing` (P2.2's module, still a docstring stub).
These tests inject a fake `PricingService` onto that module with `monkeypatch`, so construction
succeeds without any real pricing logic -- see `_fake_pricing_service` below, and
`test_construction_without_a_pricing_service_fails_today`, which documents today's real failure.
"""
from __future__ import annotations

import pricebt.assets.pricing as pricing_module
import pytest

from pricebt.data import DataFrequency, Dataset
from pricebt.errors import NotSupportedError
from pricebt.session import Environment, GsSession, PricebtSession


def _asset(name, currency="USD"):
    return {
        "schema_version": 1,
        "asset": name,
        "instrument": "ConfigInstrument",
        "currency": currency,
        "market": {"expr": "1"},
        "functions": {"f": {"expr": "1", "unit": "ccy"}},
        "risk_measures": {"Price": "f"},
    }


class _FakePricingService:
    """Stands in for P2.2's real `pricing.PricingService(registry, fx=None, *, tz, eod_time)`
    (DESIGN.md section 6.2) so `PricebtSession()` construction succeeds here."""

    def __init__(self, registry, fx=None, *, tz, eod_time):
        self.registry = registry
        self.fx = fx
        self.tz = tz
        self.eod_time = eod_time
        self.reset_calls = 0

    def reset(self):
        self.reset_calls += 1


@pytest.fixture
def fake_pricing_service(monkeypatch):
    """Inject the fake PricingService onto the (currently stub) pricebt.assets.pricing module."""
    monkeypatch.setattr(pricing_module, "PricingService", _FakePricingService, raising=False)


# ------------------------------------------------------------------------------------ construction


def test_construction_fails_loudly_when_pricing_service_is_missing(monkeypatch):
    """Exercises the function-level `from pricebt.assets import pricing` + attribute read in
    `__init__`: with no `PricingService` on that module (today's real state, and reproduced here
    explicitly so this test stays green once P2.2 adds the real class), construction raises
    AttributeError rather than silently building a half-usable session."""
    monkeypatch.delattr(pricing_module, "PricingService", raising=False)
    with pytest.raises(AttributeError):
        PricebtSession()


def test_construction_builds_pricing_service_from_registry_and_settings(fake_pricing_service):
    session = PricebtSession(assets=[_asset("a")], tz="UTC", eod_time="16:30")
    assert isinstance(session.pricing, _FakePricingService)
    assert session.pricing.registry is session.registry
    assert session.pricing.tz == "UTC"
    assert session.pricing.eod_time == "16:30"
    assert "a" in session.registry


def test_use_constructs_sets_current_and_returns_session(fake_pricing_service):
    session = PricebtSession.use(assets=[_asset("b")])
    assert PricebtSession.current is session
    assert "b" in session.registry


def test_fx_source_is_loaded_and_handed_to_the_pricing_service(fake_pricing_service):
    fx = {"schema_version": 1, "fx": "toy_table", "rate": "1.0"}
    session = PricebtSession(fx=fx)
    assert session.fx.name == "toy_table"
    assert session.pricing.fx is session.fx  # same loaded FxConfig object, not re-loaded


def test_fx_none_stays_none(fake_pricing_service):
    session = PricebtSession()
    assert session.fx is None
    assert session.pricing.fx is None


def test_add_asset_returns_the_loaded_config(fake_pricing_service):
    session = PricebtSession()
    cfg = session.add_asset(_asset("c"))
    assert cfg.name == "c"
    assert session.registry["c"] is cfg


def test_reset_caches_delegates_to_pricing_service(fake_pricing_service):
    session = PricebtSession()
    session.reset_caches()
    assert session.pricing.reset_calls == 1


# ------------------------------------------------------------------------------------ context-manager nesting


def test_context_manager_restores_previous_current(fake_pricing_service):
    outer = PricebtSession()
    PricebtSession.current = outer
    with PricebtSession() as inner:
        assert PricebtSession.current is inner
    assert PricebtSession.current is outer


def test_context_manager_nesting_restores_each_level(fake_pricing_service):
    outer = PricebtSession()
    PricebtSession.current = outer
    with PricebtSession() as inner:
        assert PricebtSession.current is inner
        with PricebtSession() as innermost:
            assert PricebtSession.current is innermost
        assert PricebtSession.current is inner  # inner restored after the nested block exits
    assert PricebtSession.current is outer


# ------------------------------------------------------------------------------------ GsSession


def test_gs_session_use_records_exact_arguments():
    GsSession.use(client_id=None, client_secret=None, scopes=("run_analytics",))
    assert GsSession.current["client_id"] is None
    assert GsSession.current["client_secret"] is None
    assert GsSession.current["scopes"] == ("run_analytics",)
    assert GsSession.current["environment_or_domain"] == Environment.PROD  # default
    assert GsSession.current["application"] == "gs-quant"  # default


def test_gs_session_use_never_creates_a_pricebt_session():
    PricebtSession.current = None
    GsSession.use()
    assert PricebtSession.current is None


# ------------------------------------------------------------------------------------ Environment


def test_environment_values():
    assert Environment.DEV.value == 1
    assert Environment.QA.value == 2
    assert Environment.PROD.value == 3


# ------------------------------------------------------------------------------------ Dataset / DataFrequency (pricebt.data)


def test_dataset_raises_not_supported_with_the_design_message():
    with pytest.raises(NotSupportedError) as exc:
        Dataset("SOME_GS_DATASET_ID")
    msg = str(exc.value)
    assert "gs Dataset reads GS server-side data" in msg
    assert "pricebt.data.measure_series(...)" in msg


def test_data_frequency_values():
    assert DataFrequency.DAILY == "daily"
    assert DataFrequency.REAL_TIME == "realTime"
    assert DataFrequency.ANY == "any"
