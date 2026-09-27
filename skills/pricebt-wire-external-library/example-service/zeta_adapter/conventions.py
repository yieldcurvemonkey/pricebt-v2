"""The shared `conventions:` vocabulary as this adapter reads it: what zeta can honour, and what it cannot.

zeta's conventions are FIXED, "nothing is configurable" (ZETA_DOCS.md section 10): USD, spot 2 business days, Monday-Friday less the market's holidays, modified following, annual fixed and floating
legs paid 2 business days after the accrual end, ACT/360, daily-compounded SOFR published one business day later, short first period, no end-of-month rule. So this table has ONE supported value
per key (each verified against the reference stack by `tests/test_skills_example_zeta_swap.py`: dates, pv, par rate and dv01 agree to round-off); every other token of the vocabulary is
`[CFG-CONVENTION-UNSUPPORTED]`, naming the reason and the supported set. There are no implicit defaults. `calendar` is the NAME of a calendar in the snapshot; a zeta market holds ONE
calendar, so the pricer checks at build time that the name is the one it carries (the label is not what matters, the holidays are).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping

from pricebt.contracts.schema import AssetSchema, SchemaRegistry
from pricebt.errors import ConfigError

_FIXED = "zeta's conventions are fixed (ZETA_DOCS.md section 10)"
_SUPPORTED: Dict[str, Any] = {
    "calendar": "*", "spot_lag_days": (2,), "payment_lag_days": (2,), "day_count": ("act360",), "frequency": ("annual",), "business_day_convention": ("modified_following",),
    "compounding": ("daily_compounded",), "stub": ("short_front",), "end_of_month": (False,), "fixing_lag_days": (1,), "time_accrual": ("lump",),
}
_REASON = {
    ("spot_lag_days", None): "zeta's spot is 2 business days and its risk pillars are spot-start OIS of that spot: a trade's `spot_lag` could differ but its risk would not follow (not verified)",
    ("payment_lag_days", None): "zeta pays 2 business days after the accrual end, nothing else",
    ("day_count", None): "zeta accrues ACT/360 on both legs, nothing else",
    ("frequency", None): "zeta's fixed and floating legs both pay annually",
    ("business_day_convention", None): "zeta rolls modified following, nothing else",
    ("compounding", "daily_average"): "zeta's floating leg is SOFR compounded daily",
    ("stub", None): "zeta generates backward from the end date with a short first period",
    ("end_of_month", True): "zeta has no end-of-month rule",
    ("fixing_lag_days", None): "zeta publishes a fixing on the next business day",
    ("time_accrual", "linear"): "zeta values at a date: carry and roll happen at the date change",
}
_SCHEMA: List[AssetSchema] = []


def _schema() -> AssetSchema:
    if not _SCHEMA:
        _SCHEMA.append(SchemaRegistry.default().get("swap"))
    return _SCHEMA[0]


@dataclass(frozen=True)
class ZetaSettings:
    """The shared block as this adapter needs it: only the calendar NAME varies (everything else is one fixed value)."""

    calendar: str


def swap_conventions(raw: Mapping[str, Any]) -> ZetaSettings:
    clean = _schema().check_conventions(raw, require_all=True)  # vocabulary: keys, types, tokens, completeness
    for key, v in clean.items():
        ok = _SUPPORTED[key]
        if ok != "*" and v not in ok:
            why = _REASON.get((key, v)) or _REASON.get((key, None)) or _FIXED
            raise ConfigError(f"convention {key}={v!r} is in the vocabulary but this adapter does not support it: {why}; supported: {list(ok)}", code="CFG-CONVENTION-UNSUPPORTED")
    return ZetaSettings(clean["calendar"])


#: a complete block for a US overnight-rate swap. `usd_fed` is the NAME of a calendar in the market snapshot (the provider owns the holidays).
USD_SOFR_OIS_CONVENTIONS: Mapping[str, Any] = {
    "calendar": "usd_fed", "spot_lag_days": 2, "day_count": "act360", "frequency": "annual", "business_day_convention": "modified_following", "payment_lag_days": 2,
    "compounding": "daily_compounded", "stub": "short_front", "end_of_month": False, "fixing_lag_days": 1, "time_accrual": "lump",
}
SWAP_KEYS = {k: (list(c.values) if c.values else c.type) for k, c in _schema().conventions.items()}
