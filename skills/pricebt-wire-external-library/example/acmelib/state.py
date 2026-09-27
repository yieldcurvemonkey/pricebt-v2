"""The ONE piece of process-global state every acmelib valuation depends on: the valuation date.

`acmelib.pv(...)`, `par_rate(...)` and the risk functions all read it; none takes a date argument for "today". Whoever sets it owns restoring it.
"""
from __future__ import annotations

from typing import Optional

from .errors import NoValuationDate

_VALUATION_DATE: Optional[str] = None


def set_valuation_date(iso: Optional[str]) -> None:
    """Set (an ISO string) or clear (None) the process-global valuation date."""
    global _VALUATION_DATE
    _VALUATION_DATE = iso


def valuation_date() -> Optional[str]:
    """The current valuation date as an ISO string, or None when unset."""
    return _VALUATION_DATE


def require_valuation_date() -> str:
    if _VALUATION_DATE is None:
        raise NoValuationDate("no valuation date: call acmelib.set_valuation_date('YYYY-MM-DD') first")
    return _VALUATION_DATE
