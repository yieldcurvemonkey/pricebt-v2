"""Builders of TINY fixture trees (calendars, curve-store partitions, bond panels, fixings) written under a tmp directory, in the layouts the providers read.

They let the rule tests run in milliseconds on inputs whose answers are known by construction, without `data/fixtures`. Needs pyarrow (through pandas).
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

D = dt.date
#: a small weekday-holiday set of the swap calendar (2024): New Year, MLK, Presidents, Good Friday, Memorial, Juneteenth, July 4, Labor, Thanksgiving, Christmas
NYC_HOLIDAYS = ("2024-01-01", "2024-01-15", "2024-02-19", "2024-03-29", "2024-05-27", "2024-06-19", "2024-07-04", "2024-09-02", "2024-11-28", "2024-12-25")
FED_HOLIDAYS = tuple(h for h in NYC_HOLIDAYS if h != "2024-03-29")
GOVT_HOLIDAYS = NYC_HOLIDAYS


def write_calendars(root: Path, nyc: Sequence[str] = NYC_HOLIDAYS, fed: Sequence[str] = FED_HOLIDAYS, govt: Sequence[str] = GOVT_HOLIDAYS) -> Path:
    cal = Path(root) / "calendars"
    cal.mkdir(parents=True, exist_ok=True)
    for name, hol in (("nyc", nyc), ("fed", fed), ("us_govt_bond", govt)):
        (cal / f"{name}.json").write_text(json.dumps({"name": name, "source": "tiny", "holidays": list(hol), "weekmask": "Mon Tue Wed Thu Fri", "first": hol[0], "last": hol[-1]}), encoding="utf8")
    return cal


def write_fixings(root: Path, dates: Iterable[Any], rates_decimal: Sequence[float]) -> Path:
    p = Path(root) / "fixings" / "USD-SOFR-1D.parquet"
    p.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"date": [pd.Timestamp(d).date() for d in dates], "rate": list(rates_decimal)}).to_parquet(p)
    return p


def curve_row(stamp: str, trading_date: str, nodes: Sequence[Tuple[str, float]], *, interpolation: Optional[str] = "log_linear", spline: Optional[List[str]] = None, variant: str = "TINY") -> Dict[str, Any]:
    """One curve-store row; `stamp` is UTC."""
    return {
        "timestamp_utc": pd.Timestamp(stamp, tz="UTC"), "trading_date": D.fromisoformat(trading_date), "node_dates": [D.fromisoformat(d) for d, _ in nodes],
        "discount_factors": [float(v) for _, v in nodes], "interpolation": interpolation, "source_variant": variant,
        "spline_knots": None if spline is None else [D.fromisoformat(x) for x in spline],
    }


def write_curve_store(root: Path, asset: str, rows: Sequence[Dict[str, Any]]) -> Path:
    """Partition the rows by trading date under `<root>/curves/asset=<asset>/date=<d>/<n>.parquet` (one file per row group of a date)."""
    base = Path(root) / "curves"
    by_day: Dict[dt.date, List[Dict[str, Any]]] = {}
    for r in rows:
        by_day.setdefault(r["trading_date"], []).append(r)
    for day, rs in by_day.items():
        p = base / f"asset={asset}" / f"date={day.isoformat()}"
        p.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(rs)
        df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True)
        df.to_parquet(p / "part-0.parquet")
    return base


def nodes(ref: str, *dfs: float) -> List[Tuple[str, float]]:
    """Node table anchored at `ref` (DF 1.0) with the given later DFs at +30, +60, ... days."""
    d0 = D.fromisoformat(ref)
    return [(ref, 1.0)] + [((d0 + dt.timedelta(days=30 * (i + 1))).isoformat(), v) for i, v in enumerate(dfs)]


def write_reference(root: Path, rows: Sequence[Sequence[Any]]) -> Path:
    """Reference table rows: (cusip, oi, auction, issue, maturity, coupon[, label])."""
    p = Path(root) / "ust" / "reference_fiscaldata.parquet"
    p.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([{"cusip": r[0], "oi": r[1], "auction_date": None if r[2] is None else D.fromisoformat(r[2]), "issue_date": D.fromisoformat(r[3]),
                        "maturity_date": D.fromisoformat(r[4]), "cpn": r[5], "label": r[6] if len(r) > 6 else f"L {r[0]}"} for r in rows])
    df.to_parquet(p)
    return p


def write_prices(root: Path, rows: Sequence[Sequence[Any]], name: str = "prices.parquet") -> Path:
    """End-of-day rows: (date, cusip, eod, bid, offer)."""
    p = Path(root) / "ust" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"date": D.fromisoformat(r[0]), "cusip": r[1], "type": "MARKET BASED NOTE", "coupon": 4.0, "offer_price": r[4], "bid_price": r[3], "eod_price": r[2], "eod_valid": True}
                  for r in rows]).to_parquet(p)
    return p


def write_minutes(root: Path, rows: Sequence[Sequence[Any]], name: str = "minutes.parquet") -> Path:
    """Minute rows: (utc stamp, cusip, ytm_pct)."""
    p = Path(root) / "ust" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"ts_utc": pd.to_datetime([r[0] for r in rows], utc=True), "cusip": [r[1] for r in rows], "ytm_pct": [float(r[2]) for r in rows]}).to_parquet(p)
    return p


#: a small on-the-run universe: two 10-Year issues and a 2-Year (cusip, oi, auction, issue, maturity, coupon)
UNIVERSE = (
    ("TEN000001", "10-Year", "2024-02-08", "2024-02-15", "2034-02-15", 4.0),
    ("TEN000002", "10-Year", "2024-05-09", "2024-05-15", "2034-05-15", 4.5),
    ("TEN000003", "10-Year", "2023-11-02", "2023-11-15", "2033-11-15", 3.5),
    ("TWO000001", "2-Year", "2024-04-24", "2024-04-30", "2026-04-30", 4.8),
)
