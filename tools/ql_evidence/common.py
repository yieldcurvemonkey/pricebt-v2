"""Shared helpers for the Phase 1 convention probes (evidence for docs/design/11-quantlib-conventions.md). Run from the project root with PYTHONPATH=src."""
from __future__ import annotations

import datetime as dt
import json
import os
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
os.environ.setdefault("MPLBACKEND", "Agg")
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import QuantLib as ql
import rateslib as rl

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "data" / "fixtures"
ASSET = "USD-SOFR-1D-CITIVELOEXCEL"


def load_row(d, asset=ASSET):
    """One curve row (node_dates, discount_factors) of the fixtures for date d (a dt.date); None when absent or stale."""
    p = FIX / "curves" / f"asset={asset}" / f"date={d.isoformat()}"
    files = sorted(p.glob("*.parquet")) if p.is_dir() else []
    if not files:
        return None
    df = pd.concat([pq.read_table(f).to_pandas() for f in files], ignore_index=True)
    r = df.iloc[-1]
    nd = [x for x in r["node_dates"]]
    if nd[0] != d:
        return None
    return nd, [float(x) for x in r["discount_factors"]]


def load_cal(name="nyc"):
    j = json.loads((FIX / "calendars" / f"{name}.json").read_text())
    return [dt.date.fromisoformat(x) for x in j["holidays"]]


def load_fixings():
    f = pd.read_parquet(FIX / "fixings" / "USD-SOFR-1D.parquet")
    return [(d, float(r)) for d, r in zip(f["date"], f["rate"])]  # decimal


def qd(d):
    return ql.Date(d.day, d.month, d.year)


def pd_(q):
    return dt.date(q.year(), int(q.month()), q.dayOfMonth())


def ql_calendar(holidays, name="bespoke"):
    """A ql.Calendar built ONLY from a holiday set (Sat/Sun weekend). BespokeCalendar owns its impl, so addHoliday does not leak to a named calendar."""
    c = ql.BespokeCalendar(name)
    c.addWeekend(ql.Saturday)
    c.addWeekend(ql.Sunday)
    for h in holidays:
        c.addHoliday(qd(h))
    return c


def rl_curve(nodes, cid="c"):
    return rl.Curve(nodes={dt.datetime(d.year, d.month, d.day): v for d, v in nodes}, id=cid, convention="act360", calendar="nyc", modifier="MF", interpolation="log_linear")


def ql_curve(nodes):
    c = ql.DiscountCurve([qd(d) for d, _ in nodes], [v for _, v in nodes], ql.Actual360())
    c.enableExtrapolation()
    return c


def fdt(d):
    return dt.datetime(d.year, d.month, d.day)


def sample_dates(start=dt.date(2019, 7, 1), end=dt.date(2026, 9, 4), n=40, seed=1):
    """Weekday reference dates with a fresh fixture row, spread over the history (deterministic)."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(start, end)
    out = []
    for i in rng.permutation(len(days)):
        d = days[i].date()
        r = load_row(d)
        if r is not None:
            out.append(d)
        if len(out) >= n:
            break
    return sorted(out)
