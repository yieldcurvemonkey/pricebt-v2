"""Shared helpers of the real-data suite tests (`test_suite_swaps.py`, `test_suite_bonds.py`); contains no tests.

The suite configs are library-neutral (one instrument spec per asset class, the trades as terms) and name the dependency-free reference stack as their default:
a test that prices with a library passes the STACK OVERLAY files of that library (`overlays(stack, "swap")`, `overlays(stack, "bond")`, both for s07). The market data
comes from the test-support providers (`tests/support`), which read `data/fixtures` (or `$PRICEBT_DATA`); a missing directory SKIPS the dependent tests with an explicit
reason (spec G6).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Sequence

import pytest

from support.known import EOD, FIXTURES, MIN, NY, have_fixtures, ny  # noqa: F401  (re-exported for the suite tests)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # `tools.swap_suite_support` (the overlays and the raw-library re-computations)
    sys.path.insert(0, str(ROOT))

from tools.swap_suite_support import STACKS, overlays  # noqa: E402,F401

CFG = ROOT / "configs" / "suite"
RECORDED = ROOT / "results"  # the recorded outputs of the pre-refactor suite (never written by any test)
needs_fixtures = pytest.mark.skipif(not have_fixtures(), reason=f"real-data fixtures not found at {FIXTURES} (spec G6: set PRICEBT_DATA or run tools/export_fixtures.py)")
needs_recorded = pytest.mark.skipif(not (RECORDED / "s11_buy_hold_10y" / "equity.parquet").is_file(), reason=f"the recorded suite results are not present under {RECORDED}")

_LIBS = {"rateslib": "rateslib", "quantlib": "QuantLib", "refstack": None}


def require_lib(stack: str = "rateslib") -> None:
    """Skip unless the stack's library (and pyarrow, which the providers read parquet with) is installed; the reference stack needs no library."""
    pytest.importorskip("pyarrow")
    lib = _LIBS[stack]
    if lib is not None:
        pytest.importorskip(lib)


def build_config(name: str, sets: Sequence[str] = (), *, kinds: Sequence[str] = ("swap",), stack: str = "rateslib", cfg_dir: Path = CFG) -> Any:
    """`pricebt.api.build` of a suite config under a stack, exactly the CLI path (`--set` overrides, one overlay file per instrument kind); the progress bar is off."""
    from pricebt import api

    return api.build(cfg_dir / name, sets=[*sets, "backtest.progress.show=false"], stack=overlays(stack, *kinds))


def d(s: str):
    import datetime as dt

    return dt.date.fromisoformat(s)
