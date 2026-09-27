"""The deliberately WRONG pieces the negative controls use (tests/test_skills_example_acme.py, README "negative controls"). Never import this from an adapter.

Two of the mistakes are one-line configuration errors (config/mistakes/acme_swap_no_percent.yaml: a binding that forgot `scale: 100`; acme_swap_wrong_sign.yaml: a binding that
forgot `sign: -1`). The three below need code, so they live here and are named by dotted path in the overlays under `config/mistakes/`; the BASE config's `registry.allow` lists
this module for that reason.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Callable, Dict

import pandas as pd

import acme_adapter as A
from pricebt.snapshot import CalendarData, MarketSnapshot, SnapshotPricer

EXTRA_HOLIDAY = dt.date(2024, 5, 28)  # the day after Memorial Day: with it, the spot date of a trade struck on Friday 2024-05-24 is 2024-05-30 instead of 2024-05-29


def lowercase_last_key(series: pd.Series) -> Dict[str, float]:
    """A ladder reducer that upper-cases every label except the last one: '30Y' comes out as '30y'. pricebt's ladder keys are upper-case tenors, so the ladder is refused."""
    out = A.ladder_to_tenor_dict(series)
    last = list(out)[-1]
    out[last.lower()] = out.pop(last)
    return out


def _wrap_with_holidays(sp: SnapshotPricer, holidays: Callable[[tuple], tuple]) -> Any:
    snap = sp.snapshot
    cals = {n: CalendarData(c.name, holidays(c.holidays), c.weekmask) for n, c in snap.calendars.items()}
    return A.wrap(SnapshotPricer(MarketSnapshot(snap.ts, snap.reference_date, snap.curves, snap.fixings, snap.quotes, cals), ts=sp.ts))


def wrap_with_an_extra_holiday(sp: SnapshotPricer) -> Any:
    """A `wrap` that loads the snapshot's holidays PLUS one of another market's (a calendar with a holiday too many): dates resolved on it shift, and no fixing is demanded that
    the market does not publish, so the run completes and the tie-out reports the difference (the shape of the calendar mistake in tests/tieout_helpers.py)."""
    return _wrap_with_holidays(sp, lambda hols: (*hols, EXTRA_HOLIDAY))


def wrap_without_holidays(sp: SnapshotPricer) -> Any:
    """A `wrap` that loads the snapshot's WEEKENDS and none of its holidays (the classic: acmelib's own weekend-only calendar). A holiday inside a started swap's fixing window is then
    a business day that has no published fixing, and the run STOPS with `MarketDataUnavailable` instead of pricing on invented data."""
    return _wrap_with_holidays(sp, lambda hols: ())
