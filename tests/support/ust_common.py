"""Bond quote-panel providers, shared part: the reference table (`Security` data), the on-the-run universe (`QuoteSet.aliases`) and the snapshot builder.

The on-the-run ranking is REFERENCE DATA computed here by the provider, never by pricebt or an adapter: a snapshot carries the resolved aliases
(`CT<n>`, `O<n>`, `OO<n>`, `OOO<n>`) for its reference date. Imports only stdlib, pandas and pricebt core.
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import pandas as pd

from pricebt.errors import ConfigError, MarketDataUnavailable
from pricebt.snapshot import CalendarData, MarketSnapshot, Quote, QuoteSet, Security
from pricebt.timeutil import Calendar

from .common import (
    BOND_CALENDAR, FIXINGS_NAME, SWAP_CALENDAR, CachedProvider, DataQualityError, FixingsHistory, FixturesMissing, PathLike, calendar_data,
    fixings_policy, load_fixings, mapping_config, resolve_dir, to_calendar,
)

#: the on-the-run rank -> alias prefix
RANK_TOKENS = ("CT", "O", "OO", "OOO")


def data_file(root: Path, name: PathLike) -> Path:
    p = Path(name)
    p = p if p.is_absolute() else root / p
    if not p.is_file():
        raise FixturesMissing(f"file not found: {p}")
    return p


class Universe:
    """On-the-run ranking over a fiscaldata-style table (cusip, oi, auction_date, issue_date, maturity_date, cpn[, label]).

    A bond is ELIGIBLE on `asof` when `roll_date < asof`, `maturity_date >= asof` and `issue_date < asof` (all as written: the comparisons are strict
    where shown), where `roll_date` is one business day after the auction (on the roll calendar) or, without an auction date, the issue date. Within an
    original-issue bucket (`oi`, e.g. '10-Year') eligible bonds are ranked by issue date, newest first (ties: table order); rank 0 is `CT<n>`, 1 `O<n>`,
    2 `OO<n>`, 3 `OOO<n>`. NOTE the effective behaviour: because `issue_date < asof` is strict and issue follows the auction by a few days, a new issue
    becomes `CT<n>` on the first business day after its ISSUE date, i.e. `asof > max(auction + 1 business day, issue_date)`.
    """

    def __init__(self, table: pd.DataFrame, calendar: Optional[Calendar] = None):
        need = {"cusip", "oi", "auction_date", "issue_date", "maturity_date", "cpn"}
        if not need <= set(table.columns):
            raise ConfigError(f"universe table lacks columns {sorted(need - set(table.columns))}", code="UNIVERSE")
        cal = calendar or Calendar.weekends_only()
        t = table.copy()
        for c in ("auction_date", "issue_date", "maturity_date"):
            t[c] = pd.to_datetime(t[c]).dt.date
        t["roll_date"] = [cal.add_business_days(a, 1) if pd.notna(a) else i for a, i in zip(t["auction_date"], t["issue_date"])]
        self.table = t.reset_index(drop=True)

    def ranked(self, asof: dt.date) -> pd.DataFrame:
        t = self.table
        e = t[(t["roll_date"] < asof) & (t["maturity_date"] >= asof) & (t["issue_date"] < asof)].copy()
        e["rank"] = e.groupby("oi")["issue_date"].rank(method="first", ascending=False).astype(int) - 1
        return e

    def aliases(self, asof: dt.date) -> Dict[str, str]:
        """Every on-the-run alias (ranks 0..3 of every `<n>-Year` bucket) that exists on `asof` -> cusip."""
        out: Dict[str, str] = {}
        for r in self.ranked(asof).itertuples():
            m = re.fullmatch(r"(\d+)-Year", str(r.oi))
            if m and 0 <= int(r.rank) < len(RANK_TOKENS):
                out[f"{RANK_TOKENS[int(r.rank)]}{int(m.group(1))}"] = str(r.cusip)
        return out


class Reference:
    """The reference table as `Security` data: every bond with a coupon and a valid life (a null coupon, not yet announced, cannot be represented)."""

    def __init__(self, table: pd.DataFrame):
        self.securities: Dict[str, Security] = {}
        for r in table.itertuples():
            if pd.isna(r.cpn) or pd.isna(r.issue_date) or pd.isna(r.maturity_date):
                continue
            iss, mat = pd.Timestamp(r.issue_date).date(), pd.Timestamp(r.maturity_date).date()
            if mat > iss:
                self.securities[str(r.cusip)] = Security(str(r.cusip), float(r.cpn), iss, mat, str(getattr(r, "label", "")))

    def get(self, cusip: str) -> Optional[Security]:
        return self.securities.get(cusip)


class QuoteProvider(CachedProvider):
    """Shared plumbing: reference table, universe, fixings history, funding curve, the snapshot builder, pickling (caches are dropped and rebuilt)."""

    _cache_attrs = ("_pricers", "_aliases")

    def _init_common(self, root: Optional[PathLike], reference: PathLike, calendar: str, calendars_root: Optional[PathLike], curve_mdp: Any,
                     max_cached_pricers: int, fixings: Optional[PathLike], fixings_unit: str, fixings_policy_cfg: Optional[Mapping[str, Any]]) -> None:
        self.root = resolve_dir(root, "ust")
        self.reference_path = data_file(self.root, reference)
        self.calendar = calendar
        self.calendars_root = Path(calendars_root) if calendars_root is not None else self.root.parent / "calendars"
        table = pd.read_parquet(self.reference_path)
        if table["cusip"].duplicated().any():
            raise DataQualityError(f"{self.reference_path.name}: duplicated CUSIPs")
        self.universe = Universe(table, to_calendar(calendar_data(calendar, self.calendars_root)))
        self.reference = Reference(table)
        self.curve_mdp = curve_mdp
        self._init_caches(max_cached_pricers)
        if fixings is not None and curve_mdp is not None:
            raise ConfigError("give `curve_mdp` (curve + its fixings) or `fixings`, not both", code="BOND")
        if isinstance(fixings, str) and fixings == "auto":
            fixings = self.root.parent / "fixings" / "USD-SOFR-1D.parquet"
        self.fixings_path = None if fixings is None else Path(fixings)
        self.fixings_unit = fixings_unit
        self.fixings_policy_cfg = dict(fixings_policy_cfg or {})
        self.policy = fixings_policy(self.fixings_policy_cfg)
        self.swap_calendar = calendar_data("nyc", self.calendars_root, as_name=SWAP_CALENDAR)
        self.bond_calendar = calendar_data("us_govt_bond", self.calendars_root, as_name=BOND_CALENDAR)
        self._fixings_raw = None if self.fixings_path is None else load_fixings(self.fixings_path, unit=fixings_unit)
        self.fixings = None  # the FixingsHistory needs the panel's time zone: built by `_finish_init`

    def _finish_init(self) -> None:
        if self._fixings_raw is not None:
            self.fixings = FixingsHistory(self._fixings_raw, to_calendar(self.swap_calendar), self.policy, self.time.tz)

    def _init_caches(self, max_cached_pricers: int) -> None:
        super()._init_caches(max_cached_pricers)
        self._aliases: Dict[dt.date, Dict[str, str]] = {}

    def aliases(self, day: dt.date) -> Dict[str, str]:
        hit = self._aliases.get(day)
        if hit is None:
            hit = self._aliases[day] = {a: c for a, c in self.universe.aliases(day).items() if self.reference.get(c) is not None}
        return hit

    def _funding(self, stamp: pd.Timestamp) -> Any:
        return None if self.curve_mdp is None else self.curve_mdp.get_pricer(stamp)

    def _quote_snapshot(self, stamp: pd.Timestamp, day: dt.date, quotes: Dict[str, Quote], prov: Dict[str, Any], flags: Optional[Mapping[str, Any]] = None) -> MarketSnapshot:
        """The snapshot of one panel: quotes + the securities they (and the on-the-run aliases) refer to + the fixings visible at `stamp` (+ the funding
        curve of `curve_mdp` when given: it must be anchored at the panel date)."""
        aliases = self.aliases(day)
        sec = {c: self.reference.securities[c] for c in {*quotes, *aliases.values()}}
        qprov: Dict[str, Any] = {} if flags is None else {"flags": {c: dict(f) for c, f in flags.items()}}
        qs = QuoteSet(day, quotes, sec, aliases, "market", qprov)
        curves: Mapping[str, Any] = {}
        fixings: Mapping[str, Any] = {}
        cals: Dict[str, CalendarData] = {SWAP_CALENDAR: self.swap_calendar, BOND_CALENDAR: self.bond_calendar}
        funding = self._funding(stamp)
        if funding is not None:
            if funding.reference_date != day:
                raise MarketDataUnavailable(stamp, {}, f"funding curve is anchored at {funding.reference_date}, the bond panel at {day}")
            fs = funding.snapshot
            curves, fixings, cals = dict(fs.curves), dict(fs.fixings), {**fs.calendars, **cals}
        elif self.fixings is not None:
            fixings = {FIXINGS_NAME: self.fixings.at(stamp, day)}
        return MarketSnapshot(stamp, day, curves=curves, fixings=fixings, quotes=qs, calendars=cals, provenance=prov)

    def _fixings_config(self) -> Dict[str, Any]:
        if self.fixings_path is None:
            return {}
        return {"fixings": str(self.fixings_path), "fixings_unit": self.fixings_unit, "fixings_policy_cfg": dict(self.fixings_policy_cfg)}

    def _describe(self, ts: Optional[pd.Timestamp]) -> Dict[str, Any]:
        if ts is not None:
            sel = self.select(ts)
            tz = self.time.tz
            return {
                "source": self.name, "requested": pd.Timestamp(ts).isoformat(), "snapshot_id": f"{self.name}:{sel.stamp.isoformat()}",
                "stamp": sel.stamp.tz_convert(tz).isoformat(), "visible_at": sel.visible.tz_convert(tz).isoformat(), "staleness_s": sel.age.total_seconds(),
            }
        vis = self._sel.instants(pd.Timestamp.min.tz_localize("UTC"), pd.Timestamp.max.tz_localize("UTC"))
        return {
            "source": self.name, "kind": self.kind, "root": str(self.root), "time": mapping_config(self.time), "served": len(self._sel),
            "first_visible": vis[0].isoformat() if len(vis) else None, "last_visible": vis[-1].isoformat() if len(vis) else None,
            "reference": str(self.reference_path), "calendar": self.calendar, "funding": None if self.curve_mdp is None else type(self.curve_mdp).__name__,
            "fixings": None if self.fixings is None else {"path": str(self.fixings_path), "first": str(self.fixings.dates[0]), "last": str(self.fixings.dates[-1])},
        }
