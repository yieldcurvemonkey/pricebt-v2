"""The ONLY module that touches ARBS's import path and modules directly.

ARBS (`C:\\Users\\chris\\clee\\ARBS`, overridable with `PRICEBT_ARBS_ROOT`) is a local checkout, not a package on the Python path, so every function here
inserts it lazily (idempotent) and imports ARBS INSIDE the function body -- never at module import time, so `import arbs_adapter` (and every other module
of this package) never requires ARBS to be present. Nothing here writes to the ARBS repo or its caches, and nothing calls anything that could launch
Excel or COM: these are EOD curve reads only (`IRSwapsMDP(source="ERIS_EOD_LIVE-RL_BASIC").get_pricer(...)`), exactly what a human runs from the
ARBS notebook `ARBS/notebooks/pricers/irswap_pricer_and_risk_model.ipynb`.

`float_curve` is the one exception: it needs no ARBS at all, only rateslib, so it delegates to `pricebt.contrib.rateslib._compat.float_curve` (generic
rateslib plumbing already shipped in `contrib`, reused rather than reimplemented).
"""
from __future__ import annotations

import os
import sys
from typing import Any, Mapping

DEFAULT_ARBS_ROOT = r"C:\Users\chris\clee\ARBS"


def arbs_root() -> str:
    return os.environ.get("PRICEBT_ARBS_ROOT", DEFAULT_ARBS_ROOT)


def ensure_arbs_on_path() -> None:
    """Insert the ARBS checkout onto `sys.path` if it is not already there. Idempotent: safe to call on every access."""
    root = arbs_root()
    if root not in sys.path:
        sys.path.insert(0, root)


def irswaps_mdp() -> type:
    """`MDP.IRSwaps.IRSwapsMDP.IRSwapsMDP`, imported lazily."""
    ensure_arbs_on_path()
    os.environ.setdefault("ARBS_SUPABASE_ENABLED", "0")  # ARBS's own opt-out of its production database, before any further ARBS import
    from MDP.IRSwaps.IRSwapsMDP import IRSwapsMDP

    return IRSwapsMDP


def rl_irswap_curve() -> type:
    """`Query.IRSwaps.backends.rateslib.RLIRSwapCurve.RLIRSwapCurve`, imported lazily."""
    ensure_arbs_on_path()
    from Query.IRSwaps.backends.rateslib.RLIRSwapCurve import RLIRSwapCurve

    return RLIRSwapCurve


def rateslib_curve_definitions() -> Mapping[str, Any]:
    """`Query.IRSwaps.backends.rateslib.rl_curve_definitions_map.RATESLIB_CURVE_DEFINITIONS`, imported lazily."""
    ensure_arbs_on_path()
    from Query.IRSwaps.backends.rateslib.rl_curve_definitions_map import RATESLIB_CURVE_DEFINITIONS

    return RATESLIB_CURVE_DEFINITIONS


def float_curve(nodes: Mapping[Any, float], id: str = "sofr") -> Any:
    """Node table {date: discount factor} -> a plain (ad=0) rateslib `Curve`, log_linear interpolation, USD SOFR conventions (act360 / nyc / modified
    following) -- no ARBS needed. Delegates to `pricebt.contrib.rateslib._compat.float_curve` (reuse, not reinvention: this is generic rateslib
    plumbing pricebt already ships)."""
    from pricebt.contrib.rateslib._compat import float_curve as _float_curve

    return _float_curve(dict(nodes), id, "log_linear")
