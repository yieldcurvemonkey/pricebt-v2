"""acmelib calendars: a process-GLOBAL registry keyed by acmelib's own names, and the date arithmetic that reads it.

Foreign conventions on purpose: dates are ISO strings ('2024-03-04') in and out, tenors are lower-case ('10y', '3m', '2w', '5d'), business-day conventions are
acmelib's own codes ('U' unadjusted, 'F' following, 'MF' modified following, 'P' preceding), weekends are named ('sat', 'sun').
A name can be registered once: registering it again with different holidays is a `CalendarError` unless `replace=True` (which changes it for every user of the process).
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Dict, Iterable, Sequence, Tuple

from pricebt.testing.refstack import add_months  # calendar-month arithmetic reused from pricebt: acmelib is NOT an independent implementation (README)
from pricebt.timeutil import Calendar

from .errors import BadInput, CalendarError

BDC = {"U": "unadjusted", "F": "following", "MF": "modified_following", "P": "preceding"}
_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_REGISTRY: Dict[str, Tuple[frozenset, Tuple[str, ...], Calendar]] = {}  # name -> (holidays, weekend days, the calendar object)


def parse_date(iso: str) -> dt.date:
    if not isinstance(iso, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso):
        raise BadInput(f"acmelib dates are ISO strings 'YYYY-MM-DD', got {iso!r}")
    return dt.date.fromisoformat(iso)


def parse_tenor(tenor: str) -> Tuple[int, str]:
    m = re.fullmatch(r"(\d+)([ymwd])", tenor) if isinstance(tenor, str) else None
    if m is None:
        raise BadInput(f"acmelib tenors are lower-case '<n><y|m|w|d>' such as '10y' or '3m', got {tenor!r}")
    return int(m.group(1)), m.group(2)


def register_calendar(name: str, holidays: Iterable[str] = (), weekend: Sequence[str] = ("sat", "sun"), *, replace: bool = False) -> None:
    hol = frozenset(holidays)
    for h in hol:
        parse_date(h)
    bad = sorted(set(weekend) - set(_DAYS))
    if bad:
        raise BadInput(f"weekend days are three-letter lower-case names {list(_DAYS)}, got {bad}")
    wk = tuple(sorted(weekend))
    old = _REGISTRY.get(name)
    if old is not None and not replace:
        if (old[0], old[1]) != (hol, wk):
            raise CalendarError(f"calendar {name!r} is already registered with different holidays or weekend (pass replace=True to change it for the whole process)")
        return
    mask = tuple(0 if d in wk else 1 for d in _DAYS)
    _REGISTRY[name] = (hol, wk, Calendar([parse_date(h) for h in hol], mask, name))


def calendar(name: str) -> Calendar:
    try:
        return _REGISTRY[name][2]
    except KeyError:
        raise CalendarError(f"no calendar {name!r} is registered; registered: {sorted(_REGISTRY)}") from None


def _bdc(code: str) -> str:
    if code not in BDC:
        raise BadInput(f"business-day convention codes are {sorted(BDC)}, got {code!r}")
    return BDC[code]


def is_business_day(iso: str, name: str) -> bool:
    return calendar(name).is_business_day(parse_date(iso))


def add_business_days(iso: str, n: int, name: str) -> str:
    return calendar(name).add_business_days(parse_date(iso), n).isoformat()


def adjust(iso: str, bdc: str, name: str) -> str:
    return calendar(name).adjust(parse_date(iso), _bdc(bdc)).isoformat()


def add_tenor(iso: str, tenor: str) -> str:
    """`iso` plus a tenor: months and years keep the day-of-month (clamped to the month), weeks and days are plain day counts."""
    n, unit = parse_tenor(tenor)
    d = parse_date(iso)
    if unit in "ym":
        return add_months(d, n * (12 if unit == "y" else 1), d.day).isoformat()
    return (d + dt.timedelta(days=n * (7 if unit == "w" else 1))).isoformat()
