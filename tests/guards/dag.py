"""The literal package skeleton and its import-DAG tiers (DESIGN.md section 3.2, "Package layout"
and "Import DAG (MUST)"). Single source of truth for test_skeleton.py, test_import_order.py and
test_import_blocker.py, so the three guards can never quietly drift apart.

Every path below is relative to `src/pricebt/`, exactly as DESIGN.md section 3.2 lists it (plus
`target/__init__.py`, needed to import `target/common.py` etc. as a package but missing from that
section's literal list -- see docs/v2/DECISIONS_LOG.md).
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
SRC = PROJECT / "src"
PKG = SRC / "pricebt"

SKELETON_PATHS: List[str] = [
    "__init__.py",
    "errors.py",
    "base.py",
    "common.py",
    "progress.py",
    "datetime/__init__.py",
    "datetime/relative_date.py",
    "risk/__init__.py",
    "risk/results.py",
    "risk/transform.py",
    "risk/contracts.py",
    "markets/__init__.py",
    "markets/portfolio.py",
    "instrument/__init__.py",
    "instrument/_gs_fields.py",
    "assets/__init__.py",
    "assets/yamlio.py",
    "assets/config.py",
    "assets/namespace.py",
    "assets/fx.py",
    "assets/registry.py",
    "assets/pricing.py",
    "session.py",
    "data/__init__.py",
    "target/__init__.py",
    "target/common.py",
    "target/measures.py",
    "target/backtests.py",
    "backtests/__init__.py",
    "backtests/core.py",
    "backtests/backtest_utils.py",
    "backtests/data_sources.py",
    "backtests/backtest_objects.py",
    "backtests/actions.py",
    "backtests/action_handler.py",
    "backtests/backtest_engine.py",
    "backtests/triggers.py",
    "backtests/strategy.py",
    "backtests/generic_engine_action_impls.py",
    "backtests/generic_engine.py",
    "backtests/equity_vol_engine.py",
    "backtests/predefined_asset_engine.py",
    "backtests/strategy_systematic.py",
    "backtests/order.py",
    "backtests/event.py",
    "backtests/data_handler.py",
    "backtests/execution_engine.py",
]


def path_to_module(path: str) -> str:
    """'assets/yamlio.py' -> 'assets.yamlio'; 'datetime/__init__.py' -> 'datetime'; '__init__.py' -> ''
    (the root package itself)."""
    stem = path[: -len(".py")]
    parts = [p for p in stem.split("/") if p != "__init__"]
    return ".".join(parts)


# Import-DAG tiers: DESIGN.md section 3.2 "Import DAG (MUST)" lists a sequential order, but two of
# its own package __init__ files eagerly import their own children (risk/__init__.py re-exports
# from risk.results; assets/__init__.py exposes load_asset/AssetConfig/FxConfig, which live in
# assets.config/assets.fx). A strict linear "nothing after M" rule is unsatisfiable by the design's
# own target state, so a tier groups a package __init__ with the submodule(s) its own row of the
# section 3.2 table says it re-exports; nothing in a later tier may be imported by an earlier one.
# `target.*` is not in the DAG text at all; it is placed after `backtests.*` because
# `target/backtests.py` is a "thin re-export shim" for gs import paths and so must depend on
# `backtests.*` existing (see DECISIONS_LOG.md).
DAG_TIERS: List[List[str]] = [
    ["", "errors"],  # pricebt/__init__.py imports errors eagerly, by design (section 3.2's own text)
    ["base"],
    ["common"],
    ["progress"],  # not in the DAG text either; self-contained (tqdm only), placed at its section 3.2 table position
    ["datetime", "datetime.relative_date"],
    ["risk", "risk.results", "risk.contracts"],  # contracts: IR_RISK_DESIGN R2-22 (risk/__init__ must not import it)
    ["risk.transform"],
    ["markets"],
    ["instrument", "instrument._gs_fields"],
    ["markets.portfolio"],
    ["assets", "assets.yamlio", "assets.config", "assets.namespace", "assets.fx", "assets.registry"],
    ["assets.pricing"],
    ["session"],
    ["data"],
    [
        "backtests",
        "backtests.core",
        "backtests.backtest_utils",
        "backtests.data_sources",
        "backtests.backtest_objects",
        "backtests.actions",
        "backtests.action_handler",
        "backtests.backtest_engine",
        "backtests.triggers",
        "backtests.strategy",
        "backtests.generic_engine_action_impls",
        "backtests.generic_engine",
        "backtests.equity_vol_engine",
        "backtests.predefined_asset_engine",
        "backtests.strategy_systematic",
        "backtests.order",
        "backtests.event",
        "backtests.data_handler",
        "backtests.execution_engine",
    ],
    ["target", "target.common", "target.measures", "target.backtests"],
]

TIER_OF: Dict[str, int] = {name: i for i, tier in enumerate(DAG_TIERS) for name in tier}

# Explicit MUST-NOT-import-at-top-level pairs the DAG text spells out by name, stronger than the
# generic tier rule (which would otherwise allow an earlier-tier module to be pulled in eagerly):
#   "markets... MUST NOT import portfolio, instrument, session or assets at top level."
#   "instrument. It imports session and markets only inside method bodies."
#   "risk.results MUST NOT import transform[.]"
FORBIDDEN_TOP_LEVEL = {
    "markets": {"markets.portfolio", "instrument", "session", "assets"},
    "instrument": {"session", "markets"},
    "risk.results": {"risk.transform"},
}

ALL_MODULE_NAMES = sorted(m for tier in DAG_TIERS for m in tier if m)


def full_name(m: str) -> str:
    return "pricebt" if not m else f"pricebt.{m}"


def _check_skeleton_matches_dag() -> None:
    from_paths = {path_to_module(p) for p in SKELETON_PATHS} | {""}
    from_dag = set(TIER_OF)
    assert from_paths == from_dag, f"SKELETON_PATHS and DAG_TIERS disagree: {from_paths ^ from_dag}"


_check_skeleton_matches_dag()
