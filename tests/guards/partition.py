"""Test partition (spec G-5): every test carries exactly the markers that say what it needs, and an unmarked test is an ERROR in the collection hook.

    core              toys and the library-free reference stack only; must pass with rateslib, QuantLib, ARBS and gs_quant unavailable (`pytest -m core` under the blocker)
    adapter_rateslib  needs the rateslib extra
    adapter_quantlib  needs the QuantLib extra
    fixtures          needs data/fixtures exported from the maintainer's data infrastructure
    live_arbs         needs the live checkout and store (opt-in, PRICEBT_LIVE_ARBS=1)

A test that needs SOME of these and is also marked `core` would run in the core-only job and fail there: `conflicts` reports it.
"""
from __future__ import annotations

from typing import Any, Iterable, List, Tuple

PARTITIONS: Tuple[str, ...] = ("core", "adapter_rateslib", "adapter_quantlib", "fixtures", "live_arbs")
NEEDS_SOMETHING: Tuple[str, ...] = PARTITIONS[1:]


def marked(item: Any, names: Iterable[str]) -> List[str]:
    return [m for m in names if item.get_closest_marker(m) is not None]


def unmarked(items: Iterable[Any]) -> List[str]:
    """Node ids of the tests that carry none of the partition markers."""
    return [i.nodeid for i in items if not marked(i, PARTITIONS)]


def conflicts(items: Iterable[Any]) -> List[str]:
    """Node ids of the tests marked `core` AND as needing a library, a fixture or a live checkout (they would fail in the core-only run)."""
    return [i.nodeid for i in items if marked(i, ("core",)) and marked(i, NEEDS_SOMETHING)]


def pytest_collection_modifyitems(config: Any, items: List[Any]) -> None:
    """The collection hook (imported by `tests/conftest.py`): an unmarked test, or a `core` test that needs something, stops the run."""
    import pytest

    bad = unmarked(items)
    if bad:
        raise pytest.UsageError("every test must carry one of the partition markers " + ", ".join(PARTITIONS) + " (spec G-5); unmarked: " + ", ".join(bad[:20]) + (f" ... and {len(bad) - 20} more" if len(bad) > 20 else ""))
    clash = conflicts(items)
    if clash:
        raise pytest.UsageError("a test marked `core` must not also need a library, a fixture or a live checkout (partition conflict): " + ", ".join(clash[:20]))
