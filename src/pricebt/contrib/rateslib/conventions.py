"""The shared `conventions:` block as this adapter reads it (spec T5; the vocabulary is data in the shipped swap and bond schemas).

One dict is handed verbatim to every adapter. The schema validates it (an unknown key, a wrong type, an unknown token, a missing key); this module then checks the
values against what THIS adapter honours and translates them into the library's arguments. Nothing has an implicit default: `USD_SOFR_OIS_CONVENTIONS` and
`UST_CONVENTIONS` are ready-made complete blocks (the ones the tie-out shares between stacks).

`calendar` is the NAME of a calendar in the market snapshot; `rl_calendar` builds the library's calendar object from that snapshot's holidays, so the library
never sees a holiday list of its own (spec 6.7: data, not a library's built-in table).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Dict, Mapping

from ...contracts.schema import SchemaRegistry
from ...errors import ConfigError
from ...snapshot import CalendarData
from ._compat import rl

USD_SOFR_OIS_CONVENTIONS: Mapping[str, Any] = {
    "calendar": "nyc", "spot_lag_days": 2, "day_count": "act360", "frequency": "annual", "business_day_convention": "modified_following", "payment_lag_days": 2,
    "compounding": "daily_compounded", "stub": "short_front", "end_of_month": False, "fixing_lag_days": 1, "time_accrual": "lump",
}
UST_CONVENTIONS: Mapping[str, Any] = {
    "calendar": "nyc", "settlement_lag_days": 0, "day_count": "actact_icma", "frequency": "semiannual", "business_day_convention": "unadjusted",
    "payment_business_day_convention": "following", "payment_lag_days": 0, "stub": "short_front", "end_of_month": True, "compounding": "semiannual",
    "yield_convention": "treasury", "ex_dividend_days": 0, "financing_day_count": "act360", "time_accrual": "lump",
}

_DAY = {"act360": "act360", "act365f": "act365f"}
_FREQ = {"annual": "a", "semiannual": "s", "quarterly": "q", "monthly": "m"}
_MODIFIER = {"modified_following": "MF", "following": "F", "preceding": "P", "unadjusted": "NONE"}
_SWAP_SUPPORTED: Dict[str, Any] = {
    "calendar": "*", "spot_lag_days": "*", "payment_lag_days": "*", "day_count": tuple(_DAY), "frequency": tuple(_FREQ), "business_day_convention": tuple(_MODIFIER),
    "compounding": ("daily_compounded",), "stub": ("short_front",), "end_of_month": (False,), "fixing_lag_days": (1,), "time_accrual": ("lump",),
}
_BOND_SUPPORTED: Dict[str, Any] = {
    "calendar": "*", "settlement_lag_days": (0,), "day_count": ("actact_icma",), "frequency": ("semiannual",), "business_day_convention": ("unadjusted",),
    "payment_business_day_convention": ("following",), "payment_lag_days": (0,), "stub": ("short_front",), "end_of_month": (True,), "compounding": ("semiannual",),
    "yield_convention": ("treasury",), "ex_dividend_days": (0,), "financing_day_count": ("act360",), "time_accrual": ("lump",),
}
_REASON = {
    ("bond", "yield_convention", "street"): "rateslib's `us_gb` street convention uses simple interest in the final period; the shipped bond marks with the treasury rule",
    ("swap", "time_accrual", "linear"): "linear intraday carry accrual is not implemented",
    ("bond", "time_accrual", "linear"): "linear intraday carry accrual is not implemented",
}
_SCHEMAS = SchemaRegistry.default()


def _check(asset_class: str, raw: Mapping[str, Any], supported: Mapping[str, Any]) -> Dict[str, Any]:
    block = _SCHEMAS.get(asset_class).check_conventions(raw, require_all=True)
    for key, v in block.items():
        ok = supported[key]
        if ok != "*" and v not in ok:
            why = _REASON.get((asset_class, key, v), "not implemented or not verified against the reference stack")
            raise ConfigError(f"convention {key}={v!r} is in the vocabulary but the rateslib adapter does not support it: {why}; supported: {list(ok)}", code="CFG-CONVENTION-UNSUPPORTED")
    return block


@dataclass(frozen=True)
class SwapConv:
    calendar: str
    spot_lag_days: int
    day_count: str
    frequency: str
    business_day_convention: str
    payment_lag_days: int

    def irs_kwargs(self) -> Dict[str, Any]:
        """The IRS arguments this block sets (the rest comes from the library's own overnight-swap spec); the calendar object is added by the caller."""
        return {"convention": _DAY[self.day_count], "frequency": _FREQ[self.frequency], "modifier": _MODIFIER[self.business_day_convention], "payment_lag": self.payment_lag_days,
                "eom": False, "leg2_fixing_method": "rfr_payment_delay"}


@dataclass(frozen=True)
class BondConv:
    calendar: str


def swap_conventions(raw: Mapping[str, Any]) -> SwapConv:
    b = _check("swap", raw, _SWAP_SUPPORTED)
    return SwapConv(b["calendar"], b["spot_lag_days"], b["day_count"], b["frequency"], b["business_day_convention"], b["payment_lag_days"])


def bond_conventions(raw: Mapping[str, Any]) -> BondConv:
    return BondConv(_check("bond", raw, _BOND_SUPPORTED)["calendar"])


def rl_calendar(cd: CalendarData) -> Any:
    """The library's calendar object for a snapshot calendar: its holidays (weekdays only matter) and its weekend days from the weekmask."""
    week = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    mask = {t[:3].lower() for t in cd.weekmask.replace(",", " ").split()} if not set(cd.weekmask) <= {"0", "1"} else {week[i] for i, c in enumerate(cd.weekmask) if c == "1"}
    weekend = [i for i, d in enumerate(week) if d not in mask]
    return rl.Cal(holidays=[dt.datetime(h.year, h.month, h.day) for h in cd.holidays if h.weekday() not in weekend], week_mask=weekend)
