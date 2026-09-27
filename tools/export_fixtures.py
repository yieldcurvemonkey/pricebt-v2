#!/usr/bin/env python
"""Export the pricebt test fixtures from the ARBS data stores.  READ-ONLY on ARBS; imports NOTHING from ARBS.

    python -B tools/export_fixtures.py --out data/fixtures            # everything
    python -B tools/export_fixtures.py --only fixings,reference       # subset
    python -B tools/export_fixtures.py --selftest                     # safety-rule self test, no ARBS access
    python -B tools/export_fixtures.py --verify data/fixtures         # re-hash every file against files.sha256

Safety rules (each is enforced in code below, not just documented):
  S1  the output directory may not resolve inside the ARBS repo, %LOCALAPPDATA%\\ARBS, or any source root (symlinks resolved).
  S2  sqlite caches are opened ONLY as file:...?mode=ro&immutable=1 (no diskcache: its LRU get() UPDATEs access times).
  S3  the curve store is never walked: one os.scandir of `asset=<A>/` per planned asset, then named partitions only.
  S4  no ARBS module is imported (asserted at exit); no network; no subprocess except `git status` on the ARBS repo.
  S5  `git --no-optional-locks -C <ARBS> status --porcelain` must be identical before and after (exit code 3 otherwise).
  S6  curve partitions are BYTE copies (sha256 recorded, source re-hashed after the copy); every other dataset is rewritten
      deterministically (sorted, zstd, fixed row groups).
Requires: pandas, pyarrow; rateslib (calendars + Webull candidate ranking); QuantLib optional (us_govt_bond calendar).
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
import pickle
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

sys.dont_write_bytecode = True

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

TOOL_VERSION = "1.0.0"
SCHEMA_VERSION = 1
LOCAL = Path(os.environ.get("LOCALAPPDATA", r"C:\Users\chris\AppData\Local"))
ARBS_REPO = Path(r"C:\Users\chris\clee\ARBS")
ARBS_APPDATA = LOCAL / "ARBS"
DEFAULT_STORE = ARBS_APPDATA / "Cache" / "curve_store" / "raw"
DEFAULT_FIXINGS = ARBS_APPDATA / "MDP" / "IRSwaps" / "ARBS" / "MDP" / "IRSwaps" / "Cache" / "fixings_cache" / "USD-SOFR-1D_fixings"
DEFAULT_DISKCACHE = ARBS_APPDATA / "Cache" / "diskcache" / "dump"
DEFAULT_REFERENCE = ARBS_REPO / "MDP" / "FixedRateBonds" / "reference_data_cache" / "ust_reference_data" / "fiscaldata"
FORBIDDEN_MODULE_PREFIXES = ("MDP", "Query", "Caching", "TB", "BT", "RVUtils")

EOD_START = "2018-04-02"  # first SOFR fixing: a swap cannot be seasoned earlier
FEDINVEST_START = dt.date(2010, 1, 1)
WEBULL_WINDOW = (dt.date(2026, 2, 16), dt.date(2026, 3, 15))
VENDOR_NOTICE = (
    "These files are derived from third-party market data (Citi Velocity curves, GS Quant curves, Barchart futures, FedInvest, "
    "Webull-derived yields, NY Fed SOFR, Treasury fiscaldata). They exist for local testing of pricebt only. Do not redistribute "
    "the Citi/GS/Barchart/Webull subsets; the FedInvest, NY Fed and fiscaldata subsets are US-government public data."
)


@dataclass(frozen=True)
class CurvePlan:
    asset: str
    windows: tuple[tuple[str, str], ...]  # inclusive ISO dates; "9999-12-31" means "to the last partition"
    role: str


CURVE_PLAN: tuple[CurvePlan, ...] = (
    CurvePlan("USD-SOFR-1D-CITIVELOEXCEL", ((EOD_START, "9999-12-31"),), "eod_long"),
    CurvePlan(
        "USD-SOFR-1D-CITIVELOEXCELMIN",
        (("2025-11-03", "2025-11-03"), ("2026-02-16", "2026-03-13"), ("2026-08-03", "2026-08-07")),
        "minute: DST-end day, Webull overlap window (contains the DST-start day 2026-03-09), Aug-2026 week",
    ),
    CurvePlan("USD-SOFR-1D-Q12STIRT", (("2026-08-03", "2026-08-07"),), "minute, CME trading-date roll (evening sessions)"),
    CurvePlan("USD-SOFR-1D", (("2026-08-03", "2026-08-07"),), "gsquant eod, 18k-node curves"),
)
WEBULL_BUCKETS = ("2-Year", "3-Year", "5-Year", "7-Year", "10-Year", "20-Year", "30-Year")
PRICE_BAND = (20.0, 250.0)  # the lowest legitimate positive FedInvest print is 43.6 (1.25% 2050 bond, Oct 2023): a [50, 250] gate is WRONG


# ----------------------------------------------------------------------------------------------- safety helpers
class SafetyError(RuntimeError):
    pass


def _resolved(p: Path) -> Path:
    return Path(os.path.realpath(p))


def assert_not_under(out: Path, forbidden: list[Path]) -> None:
    """S1: `out` (symlinks resolved) must not be inside, or equal to, any forbidden root."""
    o = _resolved(out)
    for f in forbidden:
        fr = _resolved(f)
        if o == fr or fr in o.parents:
            raise SafetyError(f"refusing to write into {o}: inside protected root {fr}")


def ro_sqlite(path: Path) -> sqlite3.Connection:
    """S2: read-only + immutable: no journal, no WAL replay, no LRU updates, cannot write."""
    uri = "file:" + quote(Path(path).as_posix(), safe="/:") + "?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True)


@contextlib.contextmanager
def open_shard(db: Path, max_copy_bytes: int = 300_000_000):
    """Yield (connection, mode).  Small shards: copy cache.db + cache.db-wal into a TEMP dir outside ARBS and open the copy normally,
    so un-checkpointed WAL frames are replayed on the copy (never on the original).  Large shards: S2 immutable read, WAL ignored
    (the caller records the WAL size).  The original files are only ever opened for reading."""
    wal = db.parent / (db.name + "-wal")
    if db.stat().st_size <= max_copy_bytes:
        with tempfile.TemporaryDirectory(prefix="pricebt_shard_") as td:
            dst = Path(td) / db.name
            shutil.copyfile(db, dst)
            if wal.exists():
                shutil.copyfile(wal, Path(td) / wal.name)
            con = sqlite3.connect(dst)
            try:
                yield con, "copy+wal"
            finally:
                con.close()
    else:
        con = ro_sqlite(db)
        try:
            yield con, "immutable"
        finally:
            con.close()


class walk_guard:
    """S3 tripwire: while active, os.walk / Path.rglob / Path.glob('**...') on a path under a protected root raise SafetyError."""

    def __init__(self, protected: list[Path]):
        self.protected = [_resolved(p) for p in protected]

    def _hit(self, p) -> bool:
        r = _resolved(Path(p))
        return any(r == q or q in r.parents for q in self.protected)

    def __enter__(self):
        import pathlib

        self._walk, self._rglob, self._glob = os.walk, pathlib.Path.rglob, pathlib.Path.glob
        guard = self

        def walk(top, *a, **k):
            if guard._hit(top):
                raise SafetyError(f"os.walk({top}) on a protected store")
            return guard._walk(top, *a, **k)

        def rglob(self_, pattern, *a, **k):
            if guard._hit(self_):
                raise SafetyError(f"rglob on protected path {self_}")
            return guard._rglob(self_, pattern, *a, **k)

        def glob(self_, pattern, *a, **k):
            if "**" in str(pattern) and guard._hit(self_):
                raise SafetyError(f"recursive glob on protected path {self_}")
            return guard._glob(self_, pattern, *a, **k)

        os.walk, pathlib.Path.rglob, pathlib.Path.glob = walk, rglob, glob
        return self

    def __exit__(self, *exc):
        import pathlib

        os.walk, pathlib.Path.rglob, pathlib.Path.glob = self._walk, self._rglob, self._glob


def arbs_status(repo: Path) -> list[str] | None:
    """S5: porcelain status of the ARBS repo. --no-optional-locks: `git status` must not refresh (write) the index."""
    try:
        r = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True, timeout=180
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.splitlines() if r.returncode == 0 else None


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def copy_verified(src: Path, dst: Path) -> tuple[str, int]:
    """S6: byte copy; returns (sha256, size).  The destination hash must equal the source hash."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        for chunk in iter(lambda: fi.read(1 << 20), b""):
            h.update(chunk)
            fo.write(chunk)
    got = sha256_file(dst)
    if got != h.hexdigest():
        raise SafetyError(f"copy of {src} corrupted in transit")
    return got, dst.stat().st_size


def write_parquet(df: pd.DataFrame, path: Path, schema: pa.Schema | None = None, row_group_size: int = 200_000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    t = pa.Table.from_pandas(df, schema=schema, preserve_index=False)
    pq.write_table(t, path, compression="zstd", compression_level=9, row_group_size=row_group_size, use_dictionary=True)


# ----------------------------------------------------------------------------------------------- calendars
def make_calendars(first_year: int = 2000, last_year: int = 2035) -> dict[str, dict]:
    import rateslib as rl

    days = pd.bdate_range(f"{first_year}-01-01", f"{last_year}-12-31")
    out: dict[str, dict] = {}
    for name in ("nyc", "fed"):
        cal = rl.get_calendar(name)
        hol = [d.date().isoformat() for d in days if not cal.is_bus_day(rl.dt(d.year, d.month, d.day))]
        out[name] = {"source": f"rateslib {rl.__version__} get_calendar('{name}')", "holidays": hol}
    try:
        import QuantLib as ql

        qc = ql.UnitedStates(ql.UnitedStates.GovernmentBond)
        hol = [d.date().isoformat() for d in days if qc.isHoliday(ql.Date(d.day, d.month, d.year))]
        out["us_govt_bond"] = {"source": f"QuantLib {ql.__version__} UnitedStates(GovernmentBond)", "holidays": hol}
    except ImportError:
        pass
    for v in out.values():
        v.update(weekmask="Mon Tue Wed Thu Fri", first=f"{first_year}-01-01", last=f"{last_year}-12-31")
    return out


def next_bday_fn(holidays: set[dt.date]):
    def f(d: dt.date) -> dt.date:
        x = d + dt.timedelta(days=1)
        while x.weekday() >= 5 or x in holidays:
            x += dt.timedelta(days=1)
        return x

    return f


# ----------------------------------------------------------------------------------------------- UST reference + ranking
def load_reference(path: Path) -> pd.DataFrame:
    ref = pd.read_parquet(path)
    for c in ("auction_date", "issue_date", "maturity_date"):
        ref[c] = pd.to_datetime(ref[c]).dt.date
    return ref


def rank_snapshot(ref: pd.DataFrame, roll: pd.Series, as_of: dt.date) -> pd.DataFrame:
    """ARBS _filter_and_rank_ref_df: roll_date < as_of, issue_date < as_of, maturity >= as_of; rank 0 = newest issue per `oi`."""
    el = (roll < as_of) & (ref["maturity_date"] >= as_of) & (ref["issue_date"] < as_of)
    t = ref[el].copy()
    t["rank"] = t.groupby("oi")["issue_date"].rank(method="first", ascending=False).astype(int) - 1
    return t


def webull_candidates(ref: pd.DataFrame, fed_holidays: set[dt.date], window: tuple[dt.date, dt.date]) -> list[str]:
    nbd = next_bday_fn(fed_holidays)
    roll = ref["auction_date"].map(lambda a: nbd(a) if pd.notna(a) else pd.NaT)
    roll = roll.where(roll.notna(), ref["issue_date"])
    seen: dict[str, None] = {}
    for d in pd.bdate_range(*window):
        t = rank_snapshot(ref, roll, d.date())
        t = t[(t["rank"] <= 1) & t["oi"].isin(WEBULL_BUCKETS)]
        for c in t["cusip"]:
            seen[str(c)] = None
    return sorted(seen)


# ----------------------------------------------------------------------------------------------- exporters
def export_curves(store: Path, out: Path, files: dict[str, str], only_assets: set[str] | None = None) -> dict:
    report: dict[str, dict] = {}
    for plan in CURVE_PLAN:
        if only_assets and plan.asset not in only_assets:
            continue
        adir = store / f"asset={plan.asset}"
        with os.scandir(adir) as it:  # S3: one level, one asset
            days = sorted(e.name[5:] for e in it if e.name.startswith("date="))
        chosen = [d for d in days if any(a <= d <= b for a, b in plan.windows)]
        rows = nbytes = 0
        stale: list[str] = []
        stale_rows: dict[str, int] = {}
        hours: dict[str, int] = {}
        node_counts: dict[str, int] = {}
        per_day_rows: dict[str, int] = {}
        stamp_ny: dict[str, dict[str, str]] = {}
        for d in chosen:
            pdir = adir / f"date={d}"
            for src in sorted(pdir.glob("*.parquet")):
                rel = f"curves/asset={plan.asset}/date={d}/{src.name}"
                dst = out / rel
                sha, size = copy_verified(src, dst)
                if sha256_file(src) != sha:
                    raise SafetyError(f"source {src} changed during export")
                files[rel] = sha
                nbytes += size
                t = pq.read_table(dst, columns=["timestamp_utc", "trading_date", "node_dates"]).to_pandas()
                rows += len(t)
                per_day_rows[d] = per_day_rows.get(d, 0) + len(t)
                td = pd.to_datetime(t["trading_date"]).dt.date
                first = t["node_dates"].map(lambda a: pd.Timestamp(a[0]).date() if len(a) else None)
                n_stale = int((first != td).sum())
                if n_stale:
                    stale.append(d)
                    stale_rows[d] = stale_rows.get(d, 0) + n_stale
                ts_utc = pd.to_datetime(t["timestamp_utc"], utc=True)
                for h, n in ts_utc.dt.hour.value_counts().items():
                    hours[str(int(h))] = hours.get(str(int(h)), 0) + int(n)
                if plan.role.startswith("eod"):
                    for hm in ts_utc.dt.tz_convert("America/New_York").dt.strftime("%H:%M").unique():
                        rec = stamp_ny.setdefault(str(hm), {"first_day": d, "last_day": d})
                        rec["last_day"] = d
                for k, n in t["node_dates"].map(len).value_counts().items():
                    node_counts[str(int(k))] = node_counts.get(str(int(k)), 0) + int(n)
        expected: set[str] = set()
        for a, b in plan.windows:
            lo, hi = max(a, days[0]), min(b, days[-1])
            if lo <= hi:
                expected |= {x.date().isoformat() for x in pd.bdate_range(lo, hi)}
        report[plan.asset] = {
            "role": plan.role,
            "windows": [list(w) for w in plan.windows],
            "partitions": len(chosen),
            "first_day": chosen[0] if chosen else None,
            "last_day": chosen[-1] if chosen else None,
            "rows": rows,
            "bytes": nbytes,
            "stale_partitions": stale,
            "stale_row_count": sum(stale_rows.values()),
            "stale_rows_by_partition": stale_rows,
            "weekdays_without_partition": sorted(expected - set(chosen)),
            "utc_hour_histogram": dict(sorted(hours.items(), key=lambda kv: int(kv[0]))),
            "node_count_histogram": dict(sorted(node_counts.items(), key=lambda kv: int(kv[0]))),
            "rows_per_day": per_day_rows if plan.asset != "USD-SOFR-1D-CITIVELOEXCEL" else None,
            "eod_stamp_ny_wallclock": stamp_ny or None,
        }
    return report


def export_fixings(root: Path, out: Path, files: dict[str, str]) -> dict:
    newest = sorted(root.glob("*/fixings.csv"))[-1]
    raw = pd.read_csv(newest)
    date_col, val_col = raw.columns[0], "Fixing"
    d = pd.to_datetime(raw[date_col], errors="coerce").dt.normalize()
    s = pd.DataFrame({"date": d.dt.date, "rate": raw[val_col].astype("float64")}).dropna()
    descending = bool(s["date"].is_monotonic_decreasing)
    s = s.drop_duplicates("date", keep="first").sort_values("date").reset_index(drop=True)
    if not (s["rate"].between(0.0, 0.2).all()):
        raise SafetyError("fixings out of the decimal range [0, 0.2]: unit assumption broken")
    if len(s) < 2000:
        raise SafetyError(f"only {len(s)} fixings rows in {newest}")
    schema = pa.schema([("date", pa.date32()), ("rate", pa.float64())])
    rel = "fixings/USD-SOFR-1D.parquet"
    write_parquet(s, out / rel, schema)
    files[rel] = sha256_file(out / rel)
    return {
        "source": str(newest),
        "source_sha256": sha256_file(newest),
        "source_dir_name": newest.parent.name,
        "source_descending": descending,
        "rows": len(s),
        "first": str(s["date"].iloc[0]),
        "last": str(s["date"].iloc[-1]),
        "unit": "decimal (0.0432 = 4.32%)",
        "max_gap_days": int(pd.Series(pd.to_datetime(s["date"])).diff().dt.days.max()),
    }


def export_fedinvest(diskcache_root: Path, out: Path, files: dict[str, str]) -> dict:
    frames, dropped_stray, wal, modes = [], [], {}, {}
    for shard in range(8):
        db = diskcache_root / "FedInvest_Prices_Cache" / f"{shard:03d}" / "cache.db"
        wal[db.parent.name] = (db.parent / "cache.db-wal").stat().st_size if (db.parent / "cache.db-wal").exists() else 0
        with open_shard(db) as (con, read_mode):
            modes[db.parent.name] = read_mode
            for key, raw, mode, val in con.execute("SELECT key, raw, mode, value FROM Cache"):
                if val is None or mode != 4:
                    raise SafetyError("FedInvest value stored out-of-line or not pickled: unsupported by a sqlite-only read")
                k = key if raw else pickle.loads(key)
                day = pd.Timestamp(str(k)[:10]).date()
                if day < FEDINVEST_START:
                    dropped_stray.append(str(day))
                    continue
                df = pickle.loads(val)
                if df is None or len(df) == 0:
                    continue
                df = df[df["type"].isin(["MARKET BASED NOTE", "MARKET BASED BOND"])].copy()
                df.insert(0, "date", day)
                frames.append(df[["date", "cusip", "type", "coupon", "offer_price", "bid_price", "eod_price"]])
    fi = pd.concat(frames, ignore_index=True).sort_values(["date", "cusip"]).reset_index(drop=True)
    for c in ("coupon", "offer_price", "bid_price", "eod_price"):
        fi[c] = fi[c].astype("float64")
    fi["eod_valid"] = fi["eod_price"].between(PRICE_BAND[0], PRICE_BAND[1])
    schema = pa.schema(
        [("date", pa.date32()), ("cusip", pa.string()), ("type", pa.string()), ("coupon", pa.float64()), ("offer_price", pa.float64()),
         ("bid_price", pa.float64()), ("eod_price", pa.float64()), ("eod_valid", pa.bool_())]
    )
    rel = "ust/fedinvest_2010_2026.parquet"
    write_parquet(fi, out / rel, schema)
    files[rel] = sha256_file(out / rel)
    bad = fi.assign(z=(fi["eod_price"] == 0)).groupby("date")["z"].mean()
    return {
        "rows": len(fi),
        "days": int(fi["date"].nunique()),
        "cusips": int(fi["cusip"].nunique()),
        "first": str(fi["date"].min()),
        "last": str(fi["date"].max()),
        "zero_eod_dates": [str(d) for d in bad[bad > 0.5].index],
        "eod_zero_rows": int((fi["eod_price"] == 0).sum()),
        "eod_invalid_rows": int((~fi["eod_valid"]).sum()),
        "offer_zero_rows": int((fi["offer_price"] == 0).sum()),
        "bid_zero_rows": int((fi["bid_price"] == 0).sum()),
        "stray_days_dropped_before_2010": sorted(set(dropped_stray)),
        "eod_valid_band": list(PRICE_BAND),
        "min_positive_eod_price": float(fi.loc[fi["eod_price"] > 0, "eod_price"].min()),
        "shard_read_mode": modes,
        "wal_bytes_on_disk": wal,
    }


def export_webull(diskcache_root: Path, cusips: list[str], out: Path, files: dict[str, str]) -> dict:
    rx = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2}T[\d:]+[+\-]\d{2}:\d{2})-(?P<cusip>[0-9A-Z]{9})-USTS_WEBULL_WSJ_LIVE-RL$")
    rows, wal, modes = [], {}, {}
    for shard in range(8):
        db = diskcache_root / "FixedRateBondPricer_Cache" / f"{shard:03d}" / "cache.db"
        wal[db.parent.name] = (db.parent / "cache.db-wal").stat().st_size if (db.parent / "cache.db-wal").exists() else 0
        with open_shard(db) as (con, read_mode):
            modes[db.parent.name] = read_mode
            for c in cusips:
                for key, val in con.execute("SELECT key, value FROM Cache WHERE key LIKE ?", (f"%-{c}-USTS_WEBULL_WSJ_LIVE-RL",)):
                    m = rx.match(key)
                    if m:
                        rows.append((m.group("ts"), c, pickle.loads(val).get("ytm")))
    wb = pd.DataFrame(rows, columns=["ts", "cusip", "ytm_pct"])
    wb["ts_utc"] = pd.to_datetime(wb.pop("ts"), utc=True)  # mixed -05:00/-04:00 offsets: parse as UTC instants (pandas would give object dtype)
    lo = pd.Timestamp(WEBULL_WINDOW[0], tz="America/New_York")
    hi = pd.Timestamp(WEBULL_WINDOW[1] + dt.timedelta(days=1), tz="America/New_York")
    n_all = len(wb)
    wb = wb[(wb["ts_utc"] >= lo) & (wb["ts_utc"] < hi)]
    wb = wb.drop_duplicates(["cusip", "ts_utc"]).sort_values(["cusip", "ts_utc"]).reset_index(drop=True)
    wb = wb[["ts_utc", "cusip", "ytm_pct"]]
    wb["ytm_pct"] = wb["ytm_pct"].astype("float64")
    schema = pa.schema([("ts_utc", pa.timestamp("ns", tz="UTC")), ("cusip", pa.string()), ("ytm_pct", pa.float64())])
    rel = "ust/webull_minute.parquet"
    write_parquet(wb, out / rel, schema)
    files[rel] = sha256_file(out / rel)
    g = wb.groupby("cusip")["ts_utc"].agg(["size", "min", "max"])
    same = wb.groupby("cusip")["ytm_pct"].apply(lambda s: float(s.diff().eq(0).mean()))
    return {
        "rows": len(wb),
        "candidates": len(cusips),
        "found": int(len(g)),
        "unit": "percent (4.622 = 4.622%)",
        "window_et": [str(WEBULL_WINDOW[0]), str(WEBULL_WINDOW[1])],
        "rows_before_window_filter": n_all,
        "unchanged_share_overall": float(wb.sort_values(["cusip", "ts_utc"]).groupby("cusip")["ytm_pct"].diff().eq(0).mean()),
        "per_cusip": {c: {"rows": int(r["size"]), "first": str(r["min"]), "last": str(r["max"]), "unchanged_share": round(float(same[c]), 4)} for c, r in g.iterrows()},
        "shard_read_mode": modes,
        "wal_bytes_on_disk": wal,
    }


def export_reference(root: Path, out: Path, files: dict[str, str]) -> dict:
    src = sorted(root.glob("*/*.parquet"))[-1]  # read-only glob of one directory level pair; never update_reference_data
    rel = "ust/reference_fiscaldata.parquet"
    sha, size = copy_verified(src, out / rel)
    files[rel] = sha
    ref = load_reference(out / rel)
    return {"source": str(src), "rows": len(ref), "bytes": size, "issue_first": str(ref["issue_date"].min()), "issue_last": str(ref["issue_date"].max()),
            "auction_last": str(ref["auction_date"].dropna().max())}


def export_calendars(out: Path, files: dict[str, str]) -> dict:
    cals = make_calendars()
    rep = {}
    for name, body in cals.items():
        rel = f"calendars/{name}.json"
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"name": name, **body}, indent=1) + "\n", encoding="utf-8")
        files[rel] = sha256_file(p)
        rep[name] = {"holidays": len(body["holidays"]), "source": body["source"]}
    return rep


# ----------------------------------------------------------------------------------------------- orchestration
def write_files_sha256(out: Path, files: dict[str, str]) -> None:
    lines = [f"{files[k]}  {k}" for k in sorted(files)]
    (out / "files.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")


def verify(out: Path) -> int:
    bad = 0
    listed = {}
    for line in (out / "files.sha256").read_text(encoding="utf-8").splitlines():
        h, rel = line.split("  ", 1)
        listed[rel] = h
        p = out / rel
        if not p.is_file() or sha256_file(p) != h:
            print("MISMATCH", rel)
            bad += 1
        elif rel.startswith("curves/") and Path(rel).stem != h:  # ARBS store files are content-addressed: <sha256 of bytes>.parquet
            print("NOT CONTENT-ADDRESSED", rel)
            bad += 1
    on_disk = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()} - {"files.sha256", "MANIFEST.json", "README.txt"}
    extra = sorted(on_disk - set(listed))
    if extra:
        print("UNLISTED FILES", extra[:5], len(extra))
        bad += len(extra)
    print("verify:", "OK" if not bad else f"{bad} problem(s)", f"({len(listed)} files)")
    return 0 if not bad else 1


def selftest() -> int:
    """Run every safety rule against a KNOWN-BAD input; each must be refused.  No ARBS access."""
    ok = True

    def expect(label: str, fn, exc):
        nonlocal ok
        try:
            fn()
            print("FAIL (not refused):", label)
            ok = False
        except exc as e:
            print("ok  ", label, "->", type(e).__name__)

    expect("S1 out inside ARBS repo", lambda: assert_not_under(ARBS_REPO / "data" / "x", [ARBS_REPO]), SafetyError)
    expect("S1 out inside %LOCALAPPDATA%\\ARBS", lambda: assert_not_under(ARBS_APPDATA / "Cache" / "x", [ARBS_APPDATA]), SafetyError)
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "t.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE Cache(key TEXT, value BLOB)")
        con.execute("INSERT INTO Cache VALUES ('a', x'00')")
        con.commit()
        con.close()
        ro = ro_sqlite(db)
        expect("S2 write through the read-only immutable connection", lambda: ro.execute("INSERT INTO Cache VALUES ('b', x'01')"), sqlite3.OperationalError)
        n = ro.execute("SELECT COUNT(*) FROM Cache").fetchone()[0]
        ro.close()
        if n != 1:
            print("FAIL: read-only read returned", n)
            ok = False
        else:
            print("ok   S2 read works (control)")
        outside = Path(td) / "fine"
        try:
            assert_not_under(outside, [ARBS_REPO, ARBS_APPDATA])
            print("ok   S1 control: a temp dir is allowed")
        except SafetyError:
            print("FAIL: control refused a legitimate path")
            ok = False
    orig_walk = os.walk
    with tempfile.TemporaryDirectory() as td:
        protected, free = Path(td) / "store", Path(td) / "free"
        for d in (protected, free):
            (d / "a").mkdir(parents=True)
            (d / "a" / "f.txt").write_text("x")
        with walk_guard([protected]):
            expect("S3 os.walk on the protected store", lambda: list(os.walk(protected)), SafetyError)
            expect("S3 rglob on the protected store", lambda: list(protected.rglob("*")), SafetyError)
            expect("S3 glob('**') on the protected store", lambda: list(protected.glob("**/*.txt")), SafetyError)
            if [p.name for p in (protected / "a").glob("*.txt")] != ["f.txt"] or len(list(os.walk(free))) != 2 or len(list(free.rglob("*"))) != 2:
                print("FAIL: walk_guard broke legitimate reads (control)")
                ok = False
            else:
                print("ok   S3 control: one-level glob on the store and full walks elsewhere still work")
    if os.walk is not orig_walk:
        print("FAIL: walk_guard did not restore os.walk")
        ok = False
    else:
        print("ok   S3 guard restored os.walk on exit")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    here = Path(__file__).resolve().parent.parent
    ap.add_argument("--out", type=Path, default=here / "data" / "fixtures")
    ap.add_argument("--store", type=Path, default=DEFAULT_STORE)
    ap.add_argument("--fixings-root", type=Path, default=DEFAULT_FIXINGS)
    ap.add_argument("--diskcache-root", type=Path, default=DEFAULT_DISKCACHE)
    ap.add_argument("--reference-root", type=Path, default=DEFAULT_REFERENCE)
    ap.add_argument("--arbs-repo", type=Path, default=ARBS_REPO)
    ap.add_argument("--only", default="curves,fixings,fedinvest,reference,calendars,webull")
    ap.add_argument("--assets", default="", help="comma list of curve assets (default: the whole plan)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--verify", type=Path, default=None)
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if a.verify:
        return verify(a.verify)

    steps = [s.strip() for s in a.only.split(",") if s.strip()]
    out = a.out
    assert_not_under(out, [a.arbs_repo, ARBS_APPDATA, a.store, a.diskcache_root])
    before = arbs_status(a.arbs_repo)
    out.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    prev = out / "MANIFEST.json"
    manifest: dict = json.loads(prev.read_text(encoding="utf-8")) if prev.exists() else {"datasets": {}}
    if prev.exists() and (out / "files.sha256").exists():
        for line in (out / "files.sha256").read_text(encoding="utf-8").splitlines():
            h, rel = line.split("  ", 1)
            files[rel] = h
    ds = manifest.setdefault("datasets", {})
    guard = walk_guard([a.arbs_repo, ARBS_APPDATA, a.store, a.diskcache_root]).__enter__()
    if "curves" in steps:
        ds["curves"] = export_curves(a.store, out, files, set(filter(None, a.assets.split(","))) or None)
    if "fixings" in steps:
        ds["fixings"] = export_fixings(a.fixings_root, out, files)
    if "fedinvest" in steps:
        ds["fedinvest"] = export_fedinvest(a.diskcache_root, out, files)
    if "reference" in steps:
        ds["reference"] = export_reference(a.reference_root, out, files)
    if "calendars" in steps:
        ds["calendars"] = export_calendars(out, files)
    if "webull" in steps:
        ref = load_reference(out / "ust" / "reference_fiscaldata.parquet")
        fed = {dt.date.fromisoformat(x) for x in json.loads((out / "calendars" / "fed.json").read_text(encoding="utf-8"))["holidays"]}
        cands = webull_candidates(ref, fed, WEBULL_WINDOW)
        ds["webull"] = export_webull(a.diskcache_root, cands, out, files)
    guard.__exit__(None, None, None)
    after = arbs_status(a.arbs_repo)
    bad_mods = sorted(m for m in sys.modules if m.split(".")[0] in FORBIDDEN_MODULE_PREFIXES)
    manifest.update(
        schema_version=SCHEMA_VERSION,
        tool={"name": "export_fixtures.py", "version": TOOL_VERSION, "sha256": sha256_file(Path(__file__))},
        created_utc=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        environment={"python": platform.python_version(), "pandas": pd.__version__, "pyarrow": pa.__version__, "platform": platform.platform()},
        sources={"curve_store": str(a.store), "fixings": str(a.fixings_root), "diskcache": str(a.diskcache_root), "reference": str(a.reference_root)},
        arbs_status={"lines_before": None if before is None else len(before), "lines_after": None if after is None else len(after),
                     "identical": before == after},
        imported_arbs_modules=bad_mods,
        vendor_notice=VENDOR_NOTICE,
    )
    write_files_sha256(out, files)
    (out / "README.txt").write_text(
        "pricebt test fixtures (generated by tools/export_fixtures.py; do not edit).\n\n" + VENDOR_NOTICE + "\n\nSee MANIFEST.json and files.sha256.\n",
        encoding="utf-8",
    )
    manifest["total_bytes"] = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    manifest["files"] = len(files)
    prev.write_text(json.dumps(manifest, indent=1, sort_keys=False, default=str) + "\n", encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ("total_bytes", "files", "arbs_status", "imported_arbs_modules")}, indent=1))
    if bad_mods:
        print("FATAL: ARBS modules were imported:", bad_mods)
        return 4
    if before != after:
        print("FATAL: ARBS git status changed during the export")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
