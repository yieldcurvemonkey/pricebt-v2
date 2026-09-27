"""The shared `conventions:` vocabulary as this adapter reads it: pricebt's tokens -> acmelib's own codes, and what acmelib cannot honour.

The VOCABULARY (keys, types, tokens) lives in the shipped `swap` schema and is validated by `AssetSchema.check_conventions`: an unknown key, a wrong type, an unknown
token and a missing key are `ConfigError`s (`CFG-CONVENTION`). On top of that this module says what THIS adapter honours: a token that is in the vocabulary but that acmelib
cannot express is a `ConfigError` (`CFG-CONVENTION-UNSUPPORTED`) that names the reason and the supported set. There are no implicit defaults.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping

from pricebt.contracts.schema import AssetSchema, SchemaRegistry
from pricebt.errors import ConfigError

# pricebt token -> acmelib code (acmelib says 'A360', '1y', 'MF' where pricebt says act360, annual, modified_following)
_BASIS = {"act360": "A360", "act365f": "A365"}
_FREQ = {"monthly": "1m", "quarterly": "3m", "semiannual": "6m", "annual": "1y"}
_BDC = {"unadjusted": "U", "following": "F", "modified_following": "MF", "preceding": "P"}

# key -> the values this adapter honours ('*' = whatever the schema accepts)
_SUPPORTED: Dict[str, Any] = {
    "calendar": "*", "spot_lag_days": "*", "payment_lag_days": "*", "day_count": tuple(_BASIS), "frequency": tuple(_FREQ), "business_day_convention": tuple(_BDC),
    "compounding": ("daily_compounded",), "stub": ("short_front",), "end_of_month": (False,), "fixing_lag_days": (1,), "time_accrual": ("lump",),
}
_REASON = {
    ("day_count", None): "acmelib has only ACT/360 and ACT/365 bases",
    ("business_day_convention", None): "acmelib has no modified-preceding code",
    ("compounding", "daily_average"): "acmelib's overnight leg only compounds",
    ("stub", None): "acmelib always puts the odd period at the front, short",
    ("end_of_month", True): "acmelib has no end-of-month roll",
    ("time_accrual", "linear"): "acmelib accrues in whole days: carry and roll happen at the date change",
    ("frequency", None): "acmelib's overnight leg pays monthly, quarterly, semiannually or annually",
    ("fixing_lag_days", None): "acmelib publishes each fixing one business day later",
}
_SCHEMA: List[AssetSchema] = []


def _schema() -> AssetSchema:
    if not _SCHEMA:
        _SCHEMA.append(SchemaRegistry.default().get("swap"))
    return _SCHEMA[0]


@dataclass(frozen=True)
class AcmeSettings:
    """The shared block translated into acmelib's vocabulary. `calendar` is still the SNAPSHOT's calendar name: the pricer maps it to the name acmelib has it under."""

    calendar: str
    spot_lag: int
    basis: str
    freq: str
    bdc: str
    pay_lag: int

    def swap_kwargs(self, acme_calendar: str) -> Dict[str, Any]:
        """The keyword arguments of `acmelib.Swap` (given the name acmelib registered the calendar under)."""
        return {"calendar": acme_calendar, "basis": self.basis, "freq": self.freq, "bdc": self.bdc, "pay_lag": self.pay_lag, "spot_lag": self.spot_lag}


def swap_conventions(raw: Mapping[str, Any]) -> AcmeSettings:
    clean = _schema().check_conventions(raw, require_all=True)  # vocabulary: keys, types, tokens, completeness
    for key, v in clean.items():
        ok = _SUPPORTED[key]
        if ok != "*" and v not in ok:
            why = _REASON.get((key, v)) or _REASON.get((key, None)) or "not implemented by acmelib"
            raise ConfigError(f"convention {key}={v!r} is in the vocabulary but this adapter does not support it: {why}; supported: {list(ok)}", code="CFG-CONVENTION-UNSUPPORTED")
    return AcmeSettings(clean["calendar"], clean["spot_lag_days"], _BASIS[clean["day_count"]], _FREQ[clean["frequency"]], _BDC[clean["business_day_convention"]],
                        clean["payment_lag_days"])


#: a complete block for a US overnight-rate swap. `usd_fed` is the NAME of a calendar in the market snapshot (the provider owns the holidays, see wrap.py).
USD_SOFR_OIS_CONVENTIONS: Mapping[str, Any] = {
    "calendar": "usd_fed", "spot_lag_days": 2, "day_count": "act360", "frequency": "annual", "business_day_convention": "modified_following", "payment_lag_days": 2,
    "compounding": "daily_compounded", "stub": "short_front", "end_of_month": False, "fixing_lag_days": 1, "time_accrual": "lump",
}
SWAP_KEYS = {k: (list(c.values) if c.values else c.type) for k, c in _schema().conventions.items()}
