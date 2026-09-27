"""Prototype QuantLib swap construction for the Phase 1 probes (becomes src/pricebt/contrib/quantlib/swap.py in Phase 2)."""
from __future__ import annotations

from common import *  # noqa

_N = [0]


def rl_calendar(holidays):
    """A rateslib Cal over EXACTLY the given holiday set (weekmask Sat/Sun): the same holidays QuantLib sees."""
    return rl.Cal(holidays=[fdt(h) for h in holidays], week_mask=[5, 6])


def make_index(cal, handle, name=None):
    _N[0] += 1
    return ql.OvernightIndex(name or f"PBTX{_N[0]}", 0, ql.USDCurrency(), cal, ql.Actual360(), handle)


def make_schedule(eff: dt.date, tenor: str, cal):
    unadj = qd(eff) + ql.Period(tenor)
    return ql.Schedule(qd(eff), unadj, ql.Period(ql.Annual), cal, ql.ModifiedFollowing, ql.ModifiedFollowing, ql.DateGeneration.Backward, False)


def make_ois(eff, tenor, sign, notional, rate_pct, cal, index, lag=2, telescopic=False):
    """sign +1 = payer of fixed. rate in PERCENT."""
    sched = make_schedule(eff, tenor, cal)
    typ = ql.Swap.Payer if sign > 0 else ql.Swap.Receiver
    return ql.OvernightIndexedSwap(typ, notional, sched, rate_pct / 100.0, ql.Actual360(), index, 0.0, lag, ql.ModifiedFollowing, cal, telescopic)


class ql_state:
    """Set evaluation date (+ includeReferenceDateEvents) for the duration of a with block, restoring both."""

    def __init__(self, d, include_ref=True):
        self.d, self.inc = d, include_ref

    def __enter__(self):
        s = ql.Settings.instance()
        self.old = (s.evaluationDate, s.includeReferenceDateEvents)
        s.evaluationDate = qd(self.d)
        s.includeReferenceDateEvents = self.inc
        return self

    def __exit__(self, *a):
        s = ql.Settings.instance()
        s.evaluationDate = self.old[0]
        s.includeReferenceDateEvents = self.old[1]


import calendar as _pycal


def _add_months(d: dt.date, n: int, roll: int) -> dt.date:
    y, m = divmod(d.year * 12 + (d.month - 1) + n, 12)
    m += 1
    return dt.date(y, m, min(roll, _pycal.monthrange(y, m)[1]))


def tenor_months(tenor: str) -> int:
    t = tenor.strip().upper()
    if t.endswith("Y"):
        return 12 * int(t[:-1])
    if t.endswith("M"):
        return int(t[:-1])
    raise ValueError(f"tenor {tenor!r} must be <n>M or <n>Y")


def unadjusted_dates(eff: dt.date, tenor: str, freq_months: int = 12):
    """Roll = the effective date's day of month (clamped to month length), stub at the FRONT (short front stub), like rateslib stub=shortfront eom=False."""
    roll = eff.day
    term = _add_months(eff, tenor_months(tenor), roll)
    out, k = [term], 1
    while True:
        d = _add_months(term, -freq_months * k, roll)
        if d <= eff:
            break
        out.append(d)
        k += 1
    out.append(eff)
    return out[::-1]


def make_schedule2(eff: dt.date, tenor: str, cal):
    dates = [cal.adjust(qd(d), ql.ModifiedFollowing) for d in unadjusted_dates(eff, tenor)]
    return ql.Schedule(dates, cal, ql.ModifiedFollowing)


def make_ois2(eff, tenor, sign, notional, rate_pct, cal, index, lag=2, telescopic=False):
    sched = make_schedule2(eff, tenor, cal)
    typ = ql.Swap.Payer if sign > 0 else ql.Swap.Receiver
    return ql.OvernightIndexedSwap(typ, notional, sched, rate_pct / 100.0, ql.Actual360(), index, 0.0, lag, ql.ModifiedFollowing, cal, telescopic)
