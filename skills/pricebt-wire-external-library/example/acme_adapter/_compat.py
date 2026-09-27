"""The ONLY module of the adapter that touches acmelib's process-global state (the valuation date, the calendar registry) and acmelib's exception types.

acmelib keeps "today" in one process-global (`acmelib.set_valuation_date`) that EVERY valuation reads, and its calendars in one process-global registry keyed by name.
Both are shared by everything in the process, so:

* `guard(date)` sets the valuation date for the duration of one call and restores what was there (even "unset") in `finally`: no state survives a call, and a caller
  that had its own valuation date set finds it again.
* `load_calendar` registers a snapshot's calendar under a CONTENT-ADDRESSED name (`PBT.<name>.<hash of holidays + weekend>`): two snapshots with different holidays
  under the same snapshot name can be alive at once (the layers use two), and registering the same content twice is a no-op. Nothing is ever `replace`d.
* `translate` turns acmelib's own exceptions into pricebt's (a missing fixing is `MarketDataUnavailable`, an unknown calendar or a malformed input is `ConfigError`,
  anything else `MethodCallError`), so the engine and the tie-out see pricebt errors only.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
from typing import Any, Iterator, List

import pandas as pd

import acmelib as acme
from pricebt.errors import ConfigError, MarketDataUnavailable, MethodCallError
from pricebt.snapshot import CalendarData

_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def to_date(d: Any) -> dt.date:
    """A date from a date, a Timestamp or an ISO string."""
    if isinstance(d, pd.Timestamp):
        return d.date()
    if isinstance(d, dt.datetime):
        return d.date()
    if isinstance(d, dt.date):
        return d
    if isinstance(d, str):
        return dt.date.fromisoformat(d.strip()[:10])
    raise TypeError(f"cannot convert {d!r} to a date")


def iso(d: Any) -> str:
    """acmelib's date spelling."""
    return to_date(d).isoformat()


@contextlib.contextmanager
def guard(valuation_date: Any) -> Iterator[dt.date]:
    """acmelib's valuation date = `valuation_date` inside the block, and exactly what it was before (possibly unset) afterwards."""
    prev = acme.valuation_date()
    acme.set_valuation_date(iso(valuation_date))
    try:
        yield to_date(valuation_date)
    finally:
        acme.set_valuation_date(prev)


@contextlib.contextmanager
def translate(ts: Any = None) -> Iterator[None]:
    """acmelib's errors -> pricebt's."""
    try:
        yield
    except acme.MissingFixing as e:
        raise MarketDataUnavailable(ts, {}, str(e)) from e
    except acme.CalendarError as e:
        raise ConfigError(str(e), code="CFG-CALENDAR") from e
    except acme.BadInput as e:
        raise ConfigError(str(e), code="CFG-TERMS") from e
    except acme.AcmeError as e:
        raise MethodCallError(f"acmelib: {type(e).__name__}: {e}") from e


def load_calendar(cd: CalendarData) -> str:
    """Register a snapshot calendar in acmelib's global registry and return the name it is registered under."""
    mask = {t.strip().lower()[:3] for t in cd.weekmask.split()}
    weekend: List[str] = [d for d in _WEEKDAYS if d not in mask]
    holidays = sorted(iso(h) for h in cd.holidays)
    tag = hashlib.sha256(json.dumps([holidays, weekend]).encode("utf8")).hexdigest()[:8]
    name = f"PBT.{cd.name}.{tag}"
    with translate():
        acme.register_calendar(name, holidays, weekend)
    return name
