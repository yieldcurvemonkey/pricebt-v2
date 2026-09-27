"""Shared pytest configuration (IMPLEMENTATION_PLAN.md sections 0.6 and 2, task P0.1).

- An unmarked test gets the `core` marker, so `-m core` selects every test unless it opts into
  `notebook` or `live_arbs`.
- `live_arbs` tests are skipped unless PRICEBT_LIVE_ARBS=1 (autonomous mode never sets this).
- `isolation` is an autouse fixture: later tasks extend it (P1.5, P2.3, P3.1, P3.3) to save and
  restore process-global state between tests, per section 0.6. P1.5 adds toylib.rates.reset_recorders().
"""
from __future__ import annotations

import os

import pytest

import toylib.rates as _toylib_rates
from pricebt.session import GsSession, PricebtSession


def pytest_collection_modifyitems(config: pytest.Config, items: list) -> None:
    for item in items:
        if not any(item.iter_markers(name=m) for m in ("core", "notebook", "live_arbs")):
            item.add_marker(pytest.mark.core)


def pytest_runtest_setup(item: pytest.Item) -> None:
    if any(item.iter_markers(name="live_arbs")) and os.environ.get("PRICEBT_LIVE_ARBS") != "1":
        pytest.skip("live_arbs: set PRICEBT_LIVE_ARBS=1 to run")


@pytest.fixture(autouse=True)
def isolation():
    """Save/restore process-global state between tests (IMPLEMENTATION_PLAN.md section 0.6).
    Extended by later tasks (P3.1, P3.3); P1.5 adds toylib.rates.reset_recorders(), P2.3 adds
    PricebtSession.current / GsSession.current."""
    _toylib_rates.reset_recorders()
    prev_pricebt_session = PricebtSession.current
    prev_gs_session = GsSession.current
    yield
    _toylib_rates.reset_recorders()
    PricebtSession.current = prev_pricebt_session
    GsSession.current = prev_gs_session
