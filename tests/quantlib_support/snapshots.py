"""Snapshot builders for the QuantLib adapter tests. Pure pricebt contracts + numpy/pandas; no QuantLib and no rateslib import here."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from pricebt.contracts.schema import SchemaRegistry
from pricebt.contracts.spec import TradeTemplate, build_spec
from pricebt.snapshot import CalendarData, CurveSnapshot, FixingsSeries, MarketSnapshot, Quote, QuoteSet, Security, SnapshotPricer

TZ = "America/New_York"
FIXTURES = Path(__file__).resolve().parents[2] / "data" / "fixtures"
SCHEMAS = SchemaRegistry.default()


def have_fixtures() -> bool:
    return (FIXTURES / "MANIFEST.json").exists()


def stamp(d: dt.date, hour: int = 17) -> pd.Timestamp:
    return pd.Timestamp(dt.datetime(d.year, d.month, d.day, hour, 0), tz=TZ)


# ------------------------------------------------------------------------------ calendars
def _nth_weekday(y: int, m: int, wd: int, n: int) -> dt.date:
    d = dt.date(y, m, 1)
    d += dt.timedelta(days=(wd - d.weekday()) % 7 + 7 * (n - 1))
    return d


def _last_monday(y: int, m: int) -> dt.date:
    d = dt.date(y, m + 1, 1) - dt.timedelta(days=1)
    return d - dt.timedelta(days=d.weekday())


def _observed(d: dt.date) -> dt.date:
    return d + dt.timedelta(days=1) if d.weekday() == 6 else d - dt.timedelta(days=1) if d.weekday() == 5 else d


def synthetic_holidays(first_year: int = 2015, last_year: int = 2075) -> List[dt.date]:
    """A US-like weekday holiday set (fixed dates observed, Monday holidays, Thanksgiving). Not the real SIFMA calendar; a known set both libraries are given."""
    out = set()
    for y in range(first_year, last_year + 1):
        for d in (dt.date(y, 1, 1), dt.date(y, 6, 19), dt.date(y, 7, 4), dt.date(y, 11, 11), dt.date(y, 12, 25)):
            out.add(_observed(d))
        out.update({_nth_weekday(y, 1, 0, 3), _nth_weekday(y, 2, 0, 3), _last_monday(y, 5), _nth_weekday(y, 9, 0, 1), _nth_weekday(y, 10, 0, 2), _nth_weekday(y, 11, 3, 4)})
    return sorted(d for d in out if d.weekday() < 5)


def calendar_data(name: str = "nyc", holidays: Optional[Iterable[dt.date]] = None) -> CalendarData:
    return CalendarData(name, tuple(synthetic_holidays() if holidays is None else holidays))


def fixture_calendar(name: str = "nyc") -> CalendarData:
    j = json.loads((FIXTURES / "calendars" / f"{name}.json").read_text(encoding="utf8"))
    return CalendarData(name, tuple(dt.date.fromisoformat(x) for x in j["holidays"]), provenance={"source": j.get("source"), "last": j.get("last")})


def is_bday(d: dt.date, hol: Iterable[dt.date]) -> bool:
    return d.weekday() < 5 and d not in set(hol)


def bdays(start: dt.date, end: dt.date, hol: Iterable[dt.date]) -> List[dt.date]:
    hs = set(hol)
    return [start + dt.timedelta(days=i) for i in range((end - start).days + 1) if (start + dt.timedelta(days=i)).weekday() < 5 and (start + dt.timedelta(days=i)) not in hs]


def add_bdays(d: dt.date, n: int, hol: Iterable[dt.date]) -> dt.date:
    hs, step = set(hol), (1 if n >= 0 else -1)
    while n != 0:
        d += dt.timedelta(days=step)
        if d.weekday() < 5 and d not in hs:
            n -= step
    return d


# ------------------------------------------------------------------------------ curves and fixings
_TENOR_DAYS = (0, 7, 30, 91, 182, 365, 730, 1095, 1826, 2557, 3652, 5479, 7305, 10958, 14610)


def synthetic_nodes(ref: dt.date, level: float = 0.04, slope: float = 0.004, bump: float = 0.0) -> Tuple[List[dt.date], List[float]]:
    """Node dates and DFs of a smooth upward zero curve (continuous zero rate level + slope*(1 - exp(-t/6)) + bump), anchored at `ref`."""
    dates = [ref + dt.timedelta(days=n) for n in _TENOR_DAYS]
    dfs = [1.0 if n == 0 else float(np.exp(-(level + bump + slope * (1 - np.exp(-(n / 365.0) / 6.0))) * n / 365.0)) for n in _TENOR_DAYS]
    return dates, dfs


def curve_from_nodes(dates: Sequence[dt.date], dfs: Sequence[float], name: str = "sofr") -> CurveSnapshot:
    return CurveSnapshot(name=name, reference_date=dates[0], node_dates=tuple(dates), values=tuple(dfs))


def fixture_row(d: dt.date, asset: str = "USD-SOFR-1D-CITIVELOEXCEL") -> Optional[Tuple[List[dt.date], List[float]]]:
    """One fresh curve row (node dates, discount factors) of the fixtures for `d`, or None."""
    import pyarrow.parquet as pq

    p = FIXTURES / "curves" / f"asset={asset}" / f"date={d.isoformat()}"
    files = sorted(p.glob("*.parquet")) if p.is_dir() else []
    if not files:
        return None
    df = pd.concat([pq.read_table(f).to_pandas() for f in files], ignore_index=True)
    r = df.iloc[-1]
    nd = list(r["node_dates"])
    return (nd, [float(x) for x in r["discount_factors"]]) if nd[0] == d else None


def fixture_fixings() -> Dict[dt.date, float]:
    """The published SOFR fixings of the fixtures: date -> DECIMAL rate."""
    f = pd.read_parquet(FIXTURES / "fixings" / "USD-SOFR-1D.parquet")
    return {d: float(r) for d, r in zip(f["date"], f["rate"])}


def seeded_fixings(hol: Iterable[dt.date], start: dt.date, end: dt.date, seed: int = 7) -> Dict[dt.date, float]:
    """A percent random walk around 4.30 on every business day in [start, end] (the series behind the golden numbers): date -> PERCENT."""
    days = bdays(start, end, hol)
    rng = np.random.default_rng(seed)
    vals = 4.30 + np.cumsum(rng.normal(0, 0.008, len(days)))
    return {d: float(v) for d, v in zip(days, vals)}


def fixings_series(fx: Mapping[dt.date, float], ref: dt.date, unit: str = "percent", name: str = "sofr", proxied: Sequence[dt.date] = ()) -> FixingsSeries:
    """The fixings visible at `ref`: strictly before it."""
    ds = sorted(d for d in fx if d < ref)
    return FixingsSeries(name=name, dates=tuple(ds), values=tuple(fx[d] for d in ds), unit=unit, proxied=tuple(proxied))


# ------------------------------------------------------------------------------ snapshots
def snapshot(ref: dt.date, nodes: Optional[Tuple[Sequence[dt.date], Sequence[float]]] = None, *, fixings: Optional[Mapping[dt.date, float]] = None, fixings_unit: str = "percent",
             hol: Optional[Iterable[dt.date]] = None, calendars: Optional[Mapping[str, CalendarData]] = None, quotes: Optional[QuoteSet] = None, hour: int = 17,
             curve_name: str = "sofr") -> MarketSnapshot:
    cals = dict(calendars) if calendars is not None else {"nyc": calendar_data("nyc", hol)}
    curves = {curve_name: curve_from_nodes(*(nodes if nodes is not None else synthetic_nodes(ref)), name=curve_name)} if nodes is not False else {}
    fx = {} if fixings is None else {curve_name: fixings_series(fixings, ref, fixings_unit, curve_name)}
    return MarketSnapshot(ts=stamp(ref, hour), reference_date=ref, curves=curves, fixings=fx, quotes=quotes, calendars=cals)


def quote_set(ref: dt.date, securities: Sequence[Tuple[str, float, dt.date, dt.date]], quotes: Mapping[str, Mapping[str, float]], aliases: Optional[Mapping[str, str]] = None,
              price_convention: str = "market") -> QuoteSet:
    """securities: (id, coupon PERCENT, issue, maturity); quotes: id -> {'clean': .., 'ytm': ..}."""
    return QuoteSet(reference_date=ref, quotes={k: Quote(**v) for k, v in quotes.items()}, securities={i: Security(i, c, a, b) for i, c, a, b in securities},
                    aliases=dict(aliases or {}), price_convention=price_convention)


def pricer_of(snap: MarketSnapshot, ts: Optional[pd.Timestamp] = None) -> SnapshotPricer:
    return SnapshotPricer(snap, ts=ts)


# ------------------------------------------------------------------------------ trades
def template(kit: Any, asset_class: str, conventions: Mapping[str, Any], terms: Mapping[str, Any], name: str = "t", bind: Optional[Mapping[str, Any]] = None) -> TradeTemplate:
    raw: Dict[str, Any] = {"factory": kit, "conventions": dict(conventions)}
    if bind:
        raw["bind"] = dict(bind)
    return TradeTemplate(name, build_spec("inst", raw, schemas=SCHEMAS), terms)
