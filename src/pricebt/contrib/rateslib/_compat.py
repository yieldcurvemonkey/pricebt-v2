"""The ONLY module that touches rateslib globals: import side effects, solver stdout, the process-global fixings store, AD stripping.

Everything else in `pricebt.contrib.rateslib` imports `rl` from here so the licence-warning filter and `MPLBACKEND` are set before
`import rateslib` runs.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import io
import math
import os
import warnings
from typing import Any, Mapping, Optional

import pandas as pd

from ...errors import OptionalDependencyError
from ...timeutil import Calendar

os.environ.setdefault("MPLBACKEND", "Agg")
warnings.filterwarnings("ignore", message=r"(?s).*Rateslib is source-available.*")
try:
    import rateslib as rl  # noqa: E402
except ImportError as e:  # pragma: no cover - exercised only without the optional extra
    raise OptionalDependencyError("pricebt.contrib.rateslib needs the optional extra `rateslib` (pip install rateslib)") from e

CAL_NAME = "nyc"
DEFAULT_FIXINGS_NAME = "PBT_SOFR"


# ------------------------------------------------------------------------------ dates and numbers
def to_dt(d: Any) -> dt.datetime:
    """date | datetime | Timestamp -> naive datetime at midnight (rateslib rejects date objects and tz-aware values)."""
    if isinstance(d, pd.Timestamp):
        d = d.date()
    if isinstance(d, dt.datetime):
        return dt.datetime(d.year, d.month, d.day)
    if isinstance(d, dt.date):
        return dt.datetime(d.year, d.month, d.day)
    raise TypeError(f"cannot convert {d!r} to a datetime")


def to_date(d: Any) -> dt.date:
    if isinstance(d, pd.Timestamp):
        return d.date()
    if isinstance(d, dt.datetime):
        return d.date()
    if isinstance(d, dt.date):
        return d
    raise TypeError(f"cannot convert {d!r} to a date")


def finite(x: Any) -> float:
    """Strip AD and assert the result is finite (rateslib returns NaN with a RuntimeWarning in several silent-failure modes)."""
    v = float(getattr(x, "real", x))
    if not math.isfinite(v):
        raise ValueError(f"non-finite rateslib result: {v!r}")
    return v


def calendar() -> Any:
    return rl.get_calendar(CAL_NAME)


def nyc_calendar() -> Calendar:
    """rateslib's `nyc` holidays as a pricebt Calendar (weekday holidays only), so grids and pricers agree on business days."""
    return Calendar([h.date() for h in calendar().holidays if h.weekday() < 5], name=CAL_NAME)


def add_bus_days(d: Any, n: int) -> dt.datetime:
    return calendar().add_bus_days(to_dt(d), n, True)


# ------------------------------------------------------------------------------ curves
def float_curve(nodes: Mapping[Any, float], cid: str = "sofr", interpolation: str = "log_linear") -> Any:
    """Node table {date: df} -> float (ad=0) rl.Curve with the USD SOFR conventions."""
    return rl.Curve(
        nodes={to_dt(d): float(v) for d, v in nodes.items()}, id=cid, convention="act360", calendar=CAL_NAME, modifier="MF", interpolation=interpolation
    )


def to_float_curve(c: Any, cid: Optional[str] = None) -> Any:
    """Rebuild any node-table curve as an ad=0 curve (never mutate a solver-owned curve; 21 us)."""
    return rl.Curve(
        nodes={k: float(v) for k, v in c.nodes.nodes.items()}, id=cid or c.id, convention=c.meta.convention, calendar=c.meta.calendar,
        modifier=c.meta.modifier, interpolation=c.interpolator.local_name,
    )


def curve_dates(c: Any) -> list:
    return list(c.nodes.nodes.keys())


def initial_date(c: Any) -> dt.date:
    return c.nodes.initial.date()


def ad_curve(c: Any, nodes: Mapping[Any, float], cid: str, order: int) -> Any:
    """Same conventions as `c`, DF nodes `nodes`, AD order 1 or 2 (variables are named f'{cid}{i}' over ALL nodes incl. node 0)."""
    return rl.Curve(
        nodes=dict(nodes), id=cid, ad=order, convention=c.meta.convention, calendar=c.meta.calendar, modifier=c.meta.modifier,
        interpolation=c.interpolator.local_name,
    )


def solve_quiet(**kw: Any) -> Any:
    """rl.Solver(**kw) with its unconditional SUCCESS print swallowed."""
    with contextlib.redirect_stdout(io.StringIO()):
        return rl.Solver(**kw)


# ------------------------------------------------------------------------------ fixings (process-global store)
def fixings_key(name: str) -> str:
    return f"{name}_1B"


def check_fixings(series: pd.Series, first_obs: Any, asof: Any, cal: Any = None) -> None:
    """Guard (rateslib.md R5): midnight naive date index, contiguous business days (of `cal`, the snapshot's calendar object) from `first_obs` to the
    last business day strictly before `asof`, nothing dated >= asof. rateslib silently misprices (or returns NaN) otherwise."""
    idx = pd.DatetimeIndex(series.index)
    if idx.tz is not None or (idx != idx.normalize()).any():
        raise ValueError("fixings index must be naive midnight dates")
    asof_dt = to_dt(asof)
    if len(idx) and idx.max() >= pd.Timestamp(asof_dt):
        raise ValueError(f"fixings dated {idx.max().date()} are not strictly before as-of {asof_dt.date()}")
    cal = cal if cal is not None else calendar()
    last = cal.lag_bus_days(asof_dt, -1, False)
    first = to_dt(first_obs)
    if last >= first:
        first_b = cal.lag_bus_days(first, 0, True) if not cal.is_bus_day(first) else first
        if last >= first_b:
            have = set(idx)
            missing = [d for d in cal.bus_date_range(first_b, last) if d not in have]
            if missing:
                raise ValueError(f"{len(missing)} SOFR fixings missing between {first_b.date()} and {last.date()}; first {missing[0].date()}")


def install_fixings(name: str, series: pd.Series) -> None:
    """Replace the global store entry `<name>_1B`. Call immediately before every rateslib pricing call that may need fixings."""
    key = fixings_key(name)
    if key in rl.fixings.loader.loaded:
        rl.fixings.pop(key)
    rl.fixings.add(key, series)


def clear_fixings(name: str) -> None:
    key = fixings_key(name)
    if key in rl.fixings.loader.loaded:
        rl.fixings.pop(key)


