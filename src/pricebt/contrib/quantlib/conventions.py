"""The shared `conventions:` vocabulary as this adapter reads it (spec T5; docs/design/11-quantlib-conventions.md section 10).

The VOCABULARY (keys, types, tokens) lives in the shipped `swap` / `bond` schemas and is validated by `AssetSchema.check_conventions`: an unknown key, a wrong type, an unknown
token and a missing key are all `ConfigError`s (`CFG-CONVENTION`). On top of that this module states what THIS adapter honours: a vocabulary token it does not support (with the
reason) is a `ConfigError` too (`CFG-CONVENTION-UNSUPPORTED`). There are no implicit defaults; `USD_SOFR_OIS_CONVENTIONS` and `UST_CONVENTIONS` are ready-made complete blocks.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Union

from ...contracts.schema import AssetSchema, SchemaRegistry
from ...errors import ConfigError

# What THIS adapter honours: key -> allowed values ('*' = any value the schema accepts). Everything else in the vocabulary is rejected with the reason. Each supported value that
# is not the shipped default was verified against rateslib (tests/test_quantlib_tieout.py: frequency, payment lag, business-day convention, spot lag).
_SWAP_SUPPORTED: Dict[str, Any] = {
    "calendar": "*", "spot_lag_days": "*", "payment_lag_days": "*", "day_count": ("act360",), "frequency": ("annual", "semiannual"),
    "business_day_convention": ("modified_following", "following"), "compounding": ("daily_compounded",), "stub": ("short_front",),
    "end_of_month": (False,), "fixing_lag_days": (1,), "time_accrual": ("lump",),
}
_BOND_SUPPORTED: Dict[str, Any] = {
    "calendar": "*", "settlement_lag_days": (0,), "day_count": ("actact_icma",), "frequency": ("semiannual",), "business_day_convention": ("unadjusted",),
    "payment_business_day_convention": ("following",), "payment_lag_days": (0,), "stub": ("short_front",), "end_of_month": (True,), "compounding": ("semiannual",),
    "yield_convention": ("treasury",), "ex_dividend_days": (0,), "financing_day_count": ("act360",), "time_accrual": ("lump",),
}
_REASON = {
    ("bond", "yield_convention", "street"): "QuantLib has no compounded-except-final-period-simple yield convention (the reference `street` rule differs by up to 8.8e-3 per 100 in the final coupon period)",
    ("swap", "time_accrual", "linear"): "linear intraday carry accrual is not implemented",
    ("bond", "time_accrual", "linear"): "linear intraday carry accrual is not implemented",
    ("bond", "settlement_lag_days", None): "the mark is the value at the reference date; a later settlement date would need a forward value",
}
_REGISTRY: List[SchemaRegistry] = []


def _schema(asset_class: str) -> AssetSchema:
    if not _REGISTRY:
        _REGISTRY.append(SchemaRegistry.default())
    return _REGISTRY[0].get(asset_class)


@dataclass(frozen=True)
class SwapConv:
    calendar: str
    spot_lag_days: int
    day_count: str
    frequency: str
    business_day_convention: str
    payment_lag_days: int
    compounding: str
    stub: str
    end_of_month: bool
    fixing_lag_days: int
    time_accrual: str

    @property
    def freq_months(self) -> int:
        return {"annual": 12, "semiannual": 6}[self.frequency]


@dataclass(frozen=True)
class BondConv:
    calendar: str
    settlement_lag_days: int
    day_count: str
    frequency: str
    business_day_convention: str
    payment_business_day_convention: str
    payment_lag_days: int
    stub: str
    end_of_month: bool
    compounding: str
    yield_convention: str
    ex_dividend_days: int
    financing_day_count: str
    time_accrual: str


def _check(asset_class: str, supported: Mapping[str, Any], raw: Mapping[str, Any]) -> Dict[str, Any]:
    clean = _schema(asset_class).check_conventions(raw, require_all=True)  # vocabulary: keys, types, tokens, completeness
    for key, v in clean.items():
        ok = supported[key]
        if ok != "*" and v not in ok:
            why = _REASON.get((asset_class, key, v)) or _REASON.get((asset_class, key, None)) or "not implemented or not verified against the reference adapter"
            raise ConfigError(f"convention {key}={v!r} is in the vocabulary but this adapter does not support it: {why}; supported: {list(ok)}", code="CFG-CONVENTION-UNSUPPORTED")
    return clean


def swap_conventions(raw: Mapping[str, Any]) -> SwapConv:
    return SwapConv(**_check("swap", _SWAP_SUPPORTED, raw))


def bond_conventions(raw: Mapping[str, Any]) -> BondConv:
    return BondConv(**_check("bond", _BOND_SUPPORTED, raw))


USD_SOFR_OIS_CONVENTIONS: Mapping[str, Any] = {
    "calendar": "nyc", "spot_lag_days": 2, "day_count": "act360", "frequency": "annual", "business_day_convention": "modified_following", "payment_lag_days": 2,
    "compounding": "daily_compounded", "stub": "short_front", "end_of_month": False, "fixing_lag_days": 1, "time_accrual": "lump",
}
UST_CONVENTIONS: Mapping[str, Any] = {
    "calendar": "nyc", "settlement_lag_days": 0, "day_count": "actact_icma", "frequency": "semiannual", "business_day_convention": "unadjusted",
    "payment_business_day_convention": "following", "payment_lag_days": 0, "stub": "short_front", "end_of_month": True, "compounding": "semiannual",
    "yield_convention": "treasury", "ex_dividend_days": 0, "financing_day_count": "act360", "time_accrual": "lump",
}


def _keys(asset_class: str) -> Dict[str, Union[List[Any], str]]:
    return {k: (list(c.values) if c.values else c.type) for k, c in _schema(asset_class).conventions.items()}


SWAP_KEYS = _keys("swap")
BOND_KEYS = _keys("bond")
