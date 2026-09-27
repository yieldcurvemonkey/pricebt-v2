"""The ONLY module that touches QuantLib process-global state: the evaluation date and the fixings histories (`IndexManager`).

QuantLib keeps "today" in `Settings.instance().evaluationDate` and every index's published fixings in `IndexManager`, keyed by the index NAME. Both are shared by everything
in the process (including the rateslib adapter running in the same interpreter, and other users of QuantLib). `guard(eval_date)` therefore

* sets the evaluation date for the duration of one call and restores the previous value in `finally`,
* clears the history of OUR index family on entry and on exit (never `clearHistories()`, which would wipe every index in the process): no state survives a call, so
  time moving backwards (a new run in the same process, a test after another) can never see a later run's fixings,
* sets `Settings.includeReferenceDateEvents` (and restores it): with the default a swap whose LAST flow is paid on the evaluation date is `isExpired()` and QuantLib returns NPV 0
  before the engine (whose own `includeSettlementDateFlows` flag is passed explicitly) is even asked, so the final flows would drop out of V(D) one mark early,
* tolerates the exception QuantLib raises from an observer notification when the date changes (the date IS set; measured), and asserts it.

Rule for the rest of the package: no QuantLib object that observes the evaluation date (bootstrap helpers, piecewise curves) may outlive the `guard` it was created in.
`RelinkableHandle`, `DiscountCurve` with explicit dates, swaps and indexes are safe to hold.
"""
from __future__ import annotations

import contextlib
import datetime as dt
from typing import Any, Iterator, Sequence

import pandas as pd

from ...errors import ConfigError, OptionalDependencyError

try:
    import QuantLib as ql  # noqa: E402
except ImportError as e:  # pragma: no cover - exercised only without the optional extra
    raise OptionalDependencyError("pricebt.contrib.quantlib needs the optional extra `QuantLib` (pip install QuantLib)") from e

INDEX_FAMILY = "PBTSOFR"
_WEEKDAYS = {"mon": ql.Monday, "tue": ql.Tuesday, "wed": ql.Wednesday, "thu": ql.Thursday, "fri": ql.Friday, "sat": ql.Saturday, "sun": ql.Sunday}


# ------------------------------------------------------------------------------ dates
def to_date(d: Any) -> dt.date:
    if isinstance(d, pd.Timestamp):
        return d.date()
    if isinstance(d, dt.datetime):
        return d.date()
    if isinstance(d, dt.date):
        return d
    if isinstance(d, str):
        return dt.date.fromisoformat(d.strip()[:10])
    raise TypeError(f"cannot convert {d!r} to a date")


def qd(d: Any) -> "ql.Date":
    d = to_date(d)
    return ql.Date(d.day, d.month, d.year)


def pd_(q: "ql.Date") -> dt.date:
    return dt.date(q.year(), int(q.month()), q.dayOfMonth())


# ------------------------------------------------------------------------------ calendars and curves (private objects, no global state)
def calendar_from_holidays(name: str, holidays: Sequence[dt.date], weekmask: str) -> "ql.Calendar":
    """A calendar built ONLY from a holiday set + weekmask. `BespokeCalendar` owns its implementation; `addHoliday` on a named calendar mutates a process-global singleton."""
    cal = ql.BespokeCalendar(name)
    days = {t.strip().lower()[:3] for t in weekmask.replace(",", " ").split() if t.strip()}
    bad = sorted(days - set(_WEEKDAYS))
    if bad or not days:
        raise ConfigError(f"weekmask {weekmask!r} of calendar {name!r} must list weekdays (Mon Tue ...), got unknown {bad}", code="CFG-CALENDAR")
    for tok, wd in _WEEKDAYS.items():
        if tok not in days:
            cal.addWeekend(wd)
    for h in holidays:
        if 1901 <= h.year <= 2199:  # QuantLib dates span 1901-2199: a holiday outside can never matter to a QuantLib computation
            cal.addHoliday(qd(h))
    return cal


def discount_curve(dates: Sequence[dt.date], dfs: Sequence[float]) -> "ql.DiscountCurve":
    """Log-linear in the discount factor between nodes and beyond the last node (the snapshot tag `log_linear_df`); reference date = the first node."""
    c = ql.DiscountCurve([qd(d) for d in dates], [float(v) for v in dfs], ql.Actual360())
    c.enableExtrapolation()
    return c


# ------------------------------------------------------------------------------ indexes and fixings (process-global history keyed by name)
def make_index(cal: "ql.Calendar", handle: "ql.YieldTermStructureHandle", dates: Sequence[dt.date] = (), values_decimal: Sequence[float] = ()) -> "ql.OvernightIndex":
    """The generic overnight index on OUR calendar (`ql.Sofr` hard-wires a named calendar) with the given published fixings (DECIMAL) stored under our family name."""
    idx = ql.OvernightIndex(INDEX_FAMILY, 0, ql.USDCurrency(), cal, ql.Actual360(), handle)
    if len(dates):
        idx.addFixings([qd(d) for d in dates], [float(v) for v in values_decimal], True)
    return idx


def _index_name() -> str:
    return ql.OvernightIndex(INDEX_FAMILY, 0, ql.USDCurrency(), ql.WeekendsOnly(), ql.Actual360()).name()


def clear_fixings() -> None:
    ql.IndexManager.instance().clearHistory(_index_name())


def has_leaked_fixings() -> bool:
    return bool(ql.IndexManager.instance().hasHistory(_index_name()))


# ------------------------------------------------------------------------------ the evaluation date
def _set_eval(d: dt.date) -> None:
    s = ql.Settings.instance()
    q = qd(d)
    try:
        s.evaluationDate = q
    except RuntimeError:
        # an observer that lives beyond its call (a relative-date helper of someone else) failed to re-initialise; the date is set anyway
        if s.evaluationDate != q:
            raise


@contextlib.contextmanager
def guard(eval_date: Any) -> Iterator[dt.date]:
    """Evaluation date = `eval_date` inside the block, restored afterwards; our fixings history cleared on entry, exit and when time moves backwards."""
    d = to_date(eval_date)
    s = ql.Settings.instance()
    prev, prev_events = s.evaluationDate, s.includeReferenceDateEvents
    clear_fixings()
    _set_eval(d)
    s.includeReferenceDateEvents = True
    try:
        yield d
    finally:
        clear_fixings()
        s.includeReferenceDateEvents = prev_events
        try:
            s.evaluationDate = prev
        except RuntimeError:
            if s.evaluationDate != prev:
                raise


def reset() -> None:
    """Drop any fixings we left behind (tests, new runs)."""
    clear_fixings()
