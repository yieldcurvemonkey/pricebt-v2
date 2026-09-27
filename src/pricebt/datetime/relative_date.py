"""RelativeDate, RelativeDateSchedule: gs-shaped relative-date rules, backed by
`numpy.busday_offset` and `dateutil.relativedelta` only (no pricing/market-data dependency; see
DESIGN.md section 6.5 and IMPLEMENTATION_PLAN.md P1.2).

Rule letters (DESIGN.md section 6.5 / research 04 section 8.2; every formula below was cross-checked
against the real rule classes in gs_quant/datetime/rules.py 2.1.17, read-only reference): `b, d, w,
m, y` are required; `k, e, x, v, j, a, r, u, g` are optional extras, added here because they are
cheap. A digit-less letter is valid (`number` defaults to 0, as gs's own `__handle_rule` does) --
`e`, `x`, `J`/`j` and a bare `v`/`g`/`r`/`u`/`k`/`a` all ignore or no-op on a zero number, so that
is how gs itself is used. Any other letter raises `NotImplementedError`. A rule string is a
left-to-right sequence of `<sign><digits?><letter>` tokens (e.g. `'1m-1b'`).

# pricebt DEV-T15: units are always lower-cased before dispatch. gs 1.5.4 is case-sensitive per
letter (`'1M'` means "next 1st Monday", an unrelated rule, and lower-case `'a'` does not exist at
all -- only `'A'` does); 2.1.17 lower-cases the *duration* string in its own callers
(`backtest_utils.get_final_date`), not inside `RelativeDate` itself. pricebt generalises that fix to
every rule letter, including the optional ones, rather than special-casing `m`/`M` alone.
"""
from __future__ import annotations

import re
import warnings
from datetime import date, datetime, timedelta
from typing import Iterable, List, Optional

import numpy as np
from dateutil.relativedelta import relativedelta

__all__ = ["RelativeDate", "RelativeDateSchedule"]

_DEFAULT_WEEK_MASK = "1111100"
_TOKEN_RE = re.compile(r"([+-]?)(\d*)([A-Za-z])")  # digits are optional: a bare letter means number=0

_warned_currencies_exchanges = False  # pricebt DEV-T3: one-time warning, see _warn_ignored() below


def _warn_ignored() -> None:
    # pricebt DEV-T3: currencies/exchanges would need a GS-infrastructure holiday lookup, which
    # pricebt has no access to; pass holiday_calendar= instead. Warn once per process.
    global _warned_currencies_exchanges
    if not _warned_currencies_exchanges:
        warnings.warn(
            "RelativeDate: currencies/exchanges are ignored (no GS infrastructure holiday lookup "
            "available); pass holiday_calendar=[...] for holidays.",
            stacklevel=3,
        )
        _warned_currencies_exchanges = True


def _as_date(d: date) -> date:
    # gs converts a datetime/pandas.Timestamp base_date with .date(); pandas.Timestamp subclasses
    # datetime.datetime, so this one check covers both without pricebt.datetime importing pandas.
    return d.date() if isinstance(d, datetime) else d


def _current_pricing_date() -> date:
    # Function-level import: pricebt.markets is a LATER import-DAG tier than pricebt.datetime
    # (DESIGN.md section 3.2), so this cannot be a top-level import without creating a cycle.
    from pricebt.markets import PricingContext

    return PricingContext.current.pricing_date


def _busdaycal(week_mask: Optional[str], holiday_calendar) -> np.busdaycalendar:
    holidays = tuple(np.datetime64(d.isoformat()) for d in (holiday_calendar or ()))
    return np.busdaycalendar(weekmask=week_mask or _DEFAULT_WEEK_MASK, holidays=holidays)


def _offset(d: date, n: int, roll: str, cal: np.busdaycalendar) -> date:
    return np.busday_offset(d, n, roll=roll, busdaycal=cal).astype("datetime64[D]").astype(object)


def _rule_b(d: date, n: int, cal, week_mask: str) -> date:
    roll = "forward" if n <= 0 else "preceding"
    return _offset(d, n, roll, cal)


def _rule_u(d: date, n: int, cal, week_mask: str, sign: str) -> date:
    # A gs business-day-offset variant (research 04 section 8.2): distinguishes '-0u' from '0u'.
    if sign == "-" and n == 0:
        roll = "preceding"
    elif n <= 0:
        roll = "forward"
    else:
        roll = "preceding"
    return _offset(d, n, roll, cal)


def _rule_d(d: date, n: int, cal, week_mask: str) -> date:
    return d + timedelta(days=n)


def _rule_w(d: date, n: int, cal, week_mask: str) -> date:
    x = d + timedelta(weeks=n)
    roll = "forward" if n >= 0 else "backward"
    return _offset(x, 0, roll, cal)


def _rule_g(d: date, n: int, cal, week_mask: str) -> date:
    x = d + timedelta(weeks=n)
    return _offset(x, 0, "backward", cal)


def _rule_m(d: date, n: int, cal, week_mask: str) -> date:
    x = d + relativedelta(months=n)  # relativedelta clips to month end (Jan31 + 1m = Feb29)
    return _offset(x, 0, "forward", cal)


def _year_step(d: date, n: int, cal, week_mask: str) -> date:
    x = d + relativedelta(years=n)
    while week_mask[x.weekday()] == "0":
        x += timedelta(days=1)
    return _offset(x, 0, "backward", cal)


_rule_y = _year_step
_rule_k = _year_step  # k: same as y (research 04 section 8.2)


def _end_of_month(d: date) -> date:
    return d + relativedelta(day=31)


def _rule_e(d: date, n: int, cal, week_mask: str) -> date:
    return _end_of_month(d)  # end of the current month, no roll


def _rule_x(d: date, n: int, cal, week_mask: str) -> date:
    return _offset(_end_of_month(d), 0, "backward", cal)


def _rule_v(d: date, n: int, cal, week_mask: str) -> date:
    return _offset(_end_of_month(d + relativedelta(months=n)), 0, "backward", cal)


def _rule_j(d: date, n: int, cal, week_mask: str) -> date:
    return d.replace(day=1)  # first day of the current month, no roll


def _rule_a(d: date, n: int, cal, week_mask: str) -> date:
    # real gs ARule.handle(): self.result.replace(month=1, day=1) + relativedelta(year=self.number).
    # relativedelta(year=...) is a dateutil *absolute* set for a truthy value but a no-op for
    # n=0 (falsy), which is why the bare/'0a' form returns Jan 1 of the base date's own year rather
    # than raising `ValueError: year 0 is out of range` the way a literal `date(n, 1, 1)` would.
    return d.replace(month=1, day=1) + relativedelta(year=n)


def _rule_r(d: date, n: int, cal, week_mask: str) -> date:
    return date(d.year, 12, 31) + relativedelta(years=n)  # no roll


_RULES = {
    "b": _rule_b,
    "d": _rule_d,
    "w": _rule_w,
    "m": _rule_m,
    "y": _rule_y,
    "k": _rule_k,
    "e": _rule_e,
    "x": _rule_x,
    "v": _rule_v,
    "j": _rule_j,
    "a": _rule_a,
    "r": _rule_r,
    "g": _rule_g,
}
_RULES_WITH_SIGN = {"u": _rule_u}


def _parse_tokens(rule: str):
    tokens = []
    pos = 0
    for m in _TOKEN_RE.finditer(rule):
        if m.start() != pos:
            raise ValueError(f"Invalid Rule {rule!r}")
        sign = m.group(1) or "+"
        n = int(sign + (m.group(2) or "0"))
        tokens.append((sign, n, m.group(3).lower()))  # pricebt DEV-T15: dispatch is case-insensitive
        pos = m.end()
    if not tokens or pos != len(rule):
        raise ValueError(f"Invalid Rule {rule!r}")
    return tokens


def _apply_tokens(base: date, rule: str, holiday_calendar, week_mask: str) -> date:
    cal = _busdaycal(week_mask, holiday_calendar)
    d = base
    for sign, n, letter in _parse_tokens(rule):
        if letter in _RULES_WITH_SIGN:
            d = _RULES_WITH_SIGN[letter](d, n, cal, week_mask, sign)
        elif letter in _RULES:
            d = _RULES[letter](d, n, cal, week_mask)
        else:
            raise NotImplementedError(f"Rule {n}{letter} not implemented")
    return d


class RelativeDate:
    """A single rule applied to a base date. Parity surface for the gs `RelativeDate` used by
    backtest durations and triggers (DESIGN.md section 6.5)."""

    def __init__(self, rule: str, base_date: Optional[date] = None):
        self.rule = rule
        self.base_date_passed_in = base_date is not None
        self.base_date = _as_date(base_date) if base_date is not None else _current_pricing_date()

    def apply_rule(
        self,
        currencies=None,
        exchanges=None,
        holiday_calendar: Optional[Iterable[date]] = None,
        week_mask: str = "1111100",
        **kwargs,
    ) -> date:
        if currencies or exchanges:
            _warn_ignored()
        return _apply_tokens(self.base_date, self.rule, holiday_calendar, week_mask)

    def as_dict(self) -> dict:
        out = {"rule": self.rule}
        if self.base_date_passed_in:
            out["baseDate"] = str(self.base_date)
        return out


class RelativeDateSchedule:
    """A schedule built by repeating a single `<int><letter>` rule, each point counted from
    `base_date` (not from the previous point). Parity surface for gs `RelativeDateSchedule`
    (DESIGN.md section 6.5)."""

    def __init__(self, rule: str, base_date: Optional[date] = None, end_date: Optional[date] = None):
        self.rule = rule
        self.base_date_passed_in = base_date is not None
        self.base_date = _as_date(base_date) if base_date is not None else _current_pricing_date()
        self.end_date = _as_date(end_date) if end_date is not None else None

    def apply_rule(
        self,
        currencies=None,
        exchanges=None,
        holiday_calendar: Optional[Iterable[date]] = None,
        week_mask: str = "1111100",
        **kwargs,
    ) -> List[date]:
        if currencies or exchanges:
            _warn_ignored()
        num_str, letter = self.rule[:-1], self.rule[-1]
        i = 1
        schedule = [self.base_date]
        while True:
            token = f"{int(num_str) * i}{letter}"
            result = _apply_tokens(self.base_date, token, holiday_calendar, week_mask)
            if self.end_date is None or result > self.end_date:
                break
            schedule.append(result)
            i += 1
        return schedule
