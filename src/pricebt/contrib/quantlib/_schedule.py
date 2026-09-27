"""Pure-Python schedule arithmetic (no QuantLib): unadjusted roll dates with the roll day kept, tenor grammar, explicit-maturity rule.

Why this exists: `ql.Schedule(..., DateGeneration.Backward)` subtracts whole periods from the TERMINATION date, so a roll day that was clamped in the termination
month (29/30/31 in February, or an 18M/30M tenor landing in February) is lost for the earlier periods; the reference library keeps the roll day. Measured: 7 of
5064 schedules differed. See docs/design/11-quantlib-conventions.md section 2.2.
"""
from __future__ import annotations

import calendar as _cal
import datetime as dt
import re
from typing import Callable, List

from ...errors import ConfigError

_TENOR = re.compile(r"^(\d+)([MY])$")


def tenor_months(tenor: str) -> int:
    """`10Y` -> 120, `18M` -> 18. Only whole months and years (day and week tenors are not schedule tenors)."""
    m = _TENOR.match(str(tenor).strip().upper())
    if m is None or int(m.group(1)) <= 0:
        raise ConfigError(f"tenor {tenor!r} must be <n>M or <n>Y with n >= 1", code="CFG-TENOR")
    n = int(m.group(1))
    return 12 * n if m.group(2) == "Y" else n


def add_months(d: dt.date, n: int, roll: int) -> dt.date:
    """`d` plus `n` months landing on the roll day, clamped to the length of the month."""
    y, m0 = divmod(d.year * 12 + (d.month - 1) + n, 12)
    m = m0 + 1
    return dt.date(y, m, min(roll, _cal.monthrange(y, m)[1]))


def unadjusted_dates(effective: dt.date, end: dt.date, roll: int, freq_months: int) -> List[dt.date]:
    """Roll dates from `effective` to the unadjusted termination `end`, generated backward from `end` on the roll day; an irregular first period is a short FRONT stub."""
    out = [end]
    k = 1
    while True:
        d = add_months(end, -freq_months * k, roll)
        if d <= effective:
            break
        out.append(d)
        k += 1
    out.append(effective)
    return out[::-1]


def from_tenor(effective: dt.date, tenor: str, freq_months: int) -> List[dt.date]:
    """Unadjusted schedule of a swap `tenor` long starting at `effective` (roll = the effective day)."""
    roll = effective.day
    return unadjusted_dates(effective, add_months(effective, tenor_months(tenor), roll), roll, freq_months)


def from_maturity(effective: dt.date, maturity: dt.date, adjust: Callable[[dt.date], dt.date], freq_months: int) -> List[dt.date]:
    """Unadjusted schedule for an explicit maturity DATE, the way the reference library reads one (measured, docs/design/11-quantlib-conventions.md 2.2).

    The roll is the effective day (placed in the maturity's month) when that date IS the maturity, or adjusts to it over a whole number of periods (so the adjusted
    termination returned as a resolved term round-trips for whole-year tenors); otherwise the date is the unadjusted end with its own roll. The odd period is a short FRONT stub.
    """
    if maturity <= effective:
        raise ConfigError(f"maturity {maturity} must be after the effective date {effective}", code="CFG-DATES")
    cand = add_months(dt.date(maturity.year, maturity.month, 1), 0, effective.day)
    months = (cand.year - effective.year) * 12 + (cand.month - effective.month)
    if months > 0 and (cand == maturity or (months % freq_months == 0 and adjust(cand) == maturity)):
        return unadjusted_dates(effective, cand, effective.day, freq_months)
    return unadjusted_dates(effective, maturity, maturity.day, freq_months)
