"""A US-Treasury-like calendar for the toy bond, for check_asset tests only: a private copy of
tests/toylib/bond.py whose calendar has holidays and whose coupons are paid on the following business
day (accrual dates unadjusted), as a real Treasury library's are. The toy itself is weekdays-only with
unadjusted coupon dates, which is why weekday-only checker logic passed it.

Business days: weekdays that are not in HOLIDAYS; the market is None on every other date. Every toy
function (settlement T+1, the drop dates, the carry horizon, the repo fixings, Theta, ForwardPrice,
Carry, ...) uses this calendar, because the copy's calendar helpers are replaced before any is called.
Use it as `tb` in a copy of tests/assets/toy_usd_bond.yaml (imports: `import holiday_bond_lib as tb`).
"""
from __future__ import annotations

import importlib.util
from datetime import date, timedelta

import toylib.bond as _toy

_spec = importlib.util.spec_from_file_location("holiday_bond_lib_toy", _toy.__file__)
_bond = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bond)

# US Treasury market holidays 2026-2027 (SIFMA-style; enough for the tests' dates)
HOLIDAYS = frozenset({
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3), date(2026, 5, 25), date(2026, 6, 19),
    date(2026, 7, 3), date(2026, 9, 7), date(2026, 10, 12), date(2026, 11, 11), date(2026, 11, 26), date(2026, 12, 25),
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26), date(2027, 5, 31),
})
_DAY = timedelta(days=1)


def is_business_day(d: date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS


def next_weekday(d: date) -> date:
    """The first business day strictly after `d` (the toy's name for it)."""
    d += _DAY
    while not is_business_day(d):
        d += _DAY
    return d


def _weekday_on_or_before(d: date) -> date:
    while not is_business_day(d):
        d -= _DAY
    return d


def _following(d: date) -> date:
    return d if is_business_day(d) else next_weekday(d)


def horizon(s: date) -> date:
    """Settlement + 1 calendar month on the following business day."""
    from dateutil.relativedelta import relativedelta

    return _following(s + relativedelta(months=1))


_unadjusted_flows = _bond._flows


def _flows(trade, t: date):
    """The toy's flows with each payment on the following business day (accrual dates unadjusted)."""
    rows = [(_following(p), a, kind, st, en) for p, a, kind, st, en in _unadjusted_flows(trade, t - timedelta(days=7))]
    return [r for r in rows if r[0] > t]


_toy_market = _bond.market


def market(d: date, ccy: str, csa=None):
    return _toy_market(d, ccy, csa) if is_business_day(d) else None


for _name in ("next_weekday", "_weekday_on_or_before", "horizon", "_flows", "market"):
    setattr(_bond, _name, globals()[_name])

# re-export the patched module's public and private names, so `tb.<name>` works as with toylib.bond
globals().update({k: v for k, v in vars(_bond).items() if not k.startswith("__") and k not in globals()})
