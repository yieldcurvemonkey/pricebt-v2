"""Neutral market snapshots (spec D1, D5): immutable, pickle-safe PLAIN DATA that any provider can emit and any library adapter can consume.

A snapshot holds only data: discount factors at node dates, published fixings, bond quotes with reference data and on-the-run aliases, calendars as
holiday sets. Everything else a pricing library needs (spot lag, day counts, calendar names, yield rules) is a CONVENTION and lives in the instrument's
`conventions` block, never here. The vocabulary of `value_kind` and `interpolation` is data: an adapter maps a tag to its native interpolation or errors.

`digest()` is a sha256 over a canonical byte encoding (big-endian IEEE-754 doubles, day ordinals, UTC nanoseconds, length-prefixed strings, names sorted): it
does not depend on `hash()` randomisation, mapping order or provenance, so two runs can prove they consumed identical inputs.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import math
import struct
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple

import pandas as pd

from .errors import ConfigError
from .pricer import PricerBase
from .timeutil import weekmask_names

VALUE_KINDS = ("discount_factor",)
#: ln(discount factor) is linear in calendar time between nodes (a constant instantaneous forward per segment), extended beyond the last node with the
#: last segment's forward; undefined before the reference date. Named by its mathematics, never by a library's tag.
INTERPOLATIONS = ("log_linear_df",)
FIXING_UNITS = ("percent", "decimal")
PRICE_CONVENTIONS = ("market", "unspecified")  # `market`: accrued interest is 0 on a coupon date


def _bad(msg: str) -> ConfigError:
    return ConfigError(msg, code="SNAPSHOT")


def _finite(x: Any, what: str) -> float:
    f = float(x)
    if not math.isfinite(f):
        raise _bad(f"{what} must be finite, got {x!r}")
    return f


# ------------------------------------------------------------------------------ canonical encoding
class _Enc:
    def __init__(self) -> None:
        self.h = hashlib.sha256()

    def s(self, v: str) -> "_Enc":
        b = v.encode("utf8")
        self.h.update(struct.pack(">I", len(b)) + b)
        return self

    def f(self, v: float) -> "_Enc":
        self.h.update(struct.pack(">d", float(v)))
        return self

    def of(self, v: Optional[float]) -> "_Enc":
        if v is None:
            self.h.update(b"\x00")
        else:
            self.h.update(b"\x01")
            self.f(v)
        return self

    def i(self, v: int) -> "_Enc":
        self.h.update(struct.pack(">q", int(v)))
        return self

    def d(self, v: dt.date) -> "_Enc":
        return self.i(v.toordinal())

    def t(self, v: pd.Timestamp) -> "_Enc":
        return self.i(v.tz_convert("UTC").value)

    def hexdigest(self) -> str:
        return self.h.hexdigest()


# ------------------------------------------------------------------------------ parts
@dataclass(frozen=True)
class CurveSnapshot:
    """Discount factors at node dates. `values[0]` is 1.0 at the reference date; no monotonicity is enforced (real data has rising steps)."""

    name: str
    reference_date: dt.date
    node_dates: Tuple[dt.date, ...]
    values: Tuple[float, ...]
    value_kind: str = "discount_factor"
    interpolation: str = "log_linear_df"
    provenance: Mapping[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        nd = tuple(self.node_dates)
        vs = tuple(_finite(v, "curve value") for v in self.values)
        object.__setattr__(self, "node_dates", nd)
        object.__setattr__(self, "values", vs)
        if not self.name:
            raise _bad("a curve needs a name")
        if self.value_kind not in VALUE_KINDS:
            raise _bad(f"value_kind {self.value_kind!r} is not supported; known: {list(VALUE_KINDS)}")
        if self.interpolation not in INTERPOLATIONS:
            raise _bad(f"interpolation {self.interpolation!r} is not a snapshot tag; known: {list(INTERPOLATIONS)} (tags are named by their maths, not by a library)")
        if len(nd) < 2 or len(nd) != len(vs):
            raise _bad(f"curve {self.name!r}: need >= 2 nodes and one value per node, got {len(nd)} dates and {len(vs)} values")
        if any(b <= a for a, b in zip(nd, nd[1:])):
            raise _bad(f"curve {self.name!r}: node dates must be strictly ascending")
        if nd[0] != self.reference_date:
            raise _bad(f"curve {self.name!r}: the first node {nd[0]} must be the reference date {self.reference_date}")
        if any(v <= 0 for v in vs):
            raise _bad(f"curve {self.name!r}: discount factors must be positive")
        if abs(vs[0] - 1.0) > 1e-12:
            raise _bad(f"curve {self.name!r}: the anchor value must be 1.0, got {vs[0]}")

    def _enc(self, e: _Enc) -> None:
        e.s("curve").s(self.name).d(self.reference_date).s(self.value_kind).s(self.interpolation).i(len(self.node_dates))
        for d, v in zip(self.node_dates, self.values):
            e.d(d).f(v)

    def digest(self) -> str:
        e = _Enc()
        self._enc(e)
        return e.hexdigest()


@dataclass(frozen=True)
class FixingsSeries:
    """Published fixings of a reference rate. `unit` is explicit; `proxied` lists dates whose value was filled by a provider policy (invented, not published)."""

    name: str
    dates: Tuple[dt.date, ...]
    values: Tuple[float, ...]
    unit: str = "percent"
    proxied: Tuple[dt.date, ...] = ()
    provenance: Mapping[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        ds, vs = tuple(self.dates), tuple(_finite(v, "fixing") for v in self.values)
        object.__setattr__(self, "dates", ds)
        object.__setattr__(self, "values", vs)
        object.__setattr__(self, "proxied", tuple(self.proxied))
        if not self.name:
            raise _bad("a fixings series needs a name")
        if self.unit not in FIXING_UNITS:
            raise _bad(f"fixings unit {self.unit!r} must be one of {list(FIXING_UNITS)}")
        if len(ds) != len(vs):
            raise _bad(f"fixings {self.name!r}: {len(ds)} dates but {len(vs)} values")
        if any(b <= a for a, b in zip(ds, ds[1:])):
            raise _bad(f"fixings {self.name!r}: dates must be strictly ascending (sorted, unique)")
        if not set(self.proxied) <= set(ds):
            raise _bad(f"fixings {self.name!r}: proxied dates {sorted(set(self.proxied) - set(ds))} are not in the series")

    def _enc(self, e: _Enc) -> None:
        e.s("fixings").s(self.name).s(self.unit).i(len(self.dates))
        for d, v in zip(self.dates, self.values):
            e.d(d).f(v)
        e.i(len(self.proxied))
        for d in sorted(self.proxied):
            e.d(d)

    def digest(self) -> str:
        e = _Enc()
        self._enc(e)
        return e.hexdigest()


@dataclass(frozen=True)
class CalendarData:
    """A business-day calendar as a holiday set plus a weekmask (so two libraries can be given the SAME holidays)."""

    name: str
    holidays: Tuple[dt.date, ...] = ()
    weekmask: str = "Mon Tue Wed Thu Fri"
    provenance: Mapping[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        hs = tuple(sorted(set(self.holidays)))
        object.__setattr__(self, "holidays", hs)
        object.__setattr__(self, "weekmask", weekmask_names(self.weekmask))  # one spelling, so the digest does not depend on how a provider wrote it
        if not self.name:
            raise _bad("a calendar needs a name")

    def _enc(self, e: _Enc) -> None:
        e.s("calendar").s(self.name).s(self.weekmask).i(len(self.holidays))
        for d in self.holidays:
            e.d(d)


@dataclass(frozen=True)
class Quote:
    """A market quote: a clean price per 100 face and/or a yield in PERCENT; optional bid/offer prices per 100 face."""

    clean: Optional[float] = None
    ytm: Optional[float] = None
    bid: Optional[float] = None
    offer: Optional[float] = None

    def __post_init__(self) -> None:
        if self.clean is None and self.ytm is None:
            raise _bad("a quote needs a clean price or a ytm")
        for k in ("clean", "ytm", "bid", "offer"):
            v = getattr(self, k)
            if v is not None:
                object.__setattr__(self, k, _finite(v, f"quote {k}"))


@dataclass(frozen=True)
class Security:
    """Reference data of a fixed-coupon bond. `coupon` is PERCENT."""

    id: str
    coupon: float
    issue_date: dt.date
    maturity_date: dt.date
    label: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "coupon", _finite(self.coupon, "coupon"))
        if self.maturity_date <= self.issue_date:
            raise _bad(f"security {self.id!r}: maturity must be after issue")


@dataclass(frozen=True)
class QuoteSet:
    """A bond panel: quotes, the reference data of every quoted security, and on-the-run aliases resolved by the provider as of `reference_date`."""

    reference_date: dt.date
    quotes: Mapping[str, Quote]
    securities: Mapping[str, Security]
    aliases: Mapping[str, str] = field(default_factory=dict)
    price_convention: str = "unspecified"
    provenance: Mapping[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "quotes", dict(self.quotes))
        object.__setattr__(self, "securities", dict(self.securities))
        object.__setattr__(self, "aliases", dict(self.aliases))
        if self.price_convention not in PRICE_CONVENTIONS:
            raise _bad(f"price_convention {self.price_convention!r} must be one of {list(PRICE_CONVENTIONS)}")
        unknown = sorted(set(self.quotes) - set(self.securities))
        if unknown:
            raise _bad(f"quotes for {unknown[:5]} have no reference data in `securities`")
        bad = {a: s for a, s in self.aliases.items() if s not in self.securities}
        if bad:
            raise _bad(f"aliases {bad} point at unknown securities")

    def _enc(self, e: _Enc) -> None:
        e.s("quotes").d(self.reference_date).s(self.price_convention).i(len(self.quotes))
        for k in sorted(self.quotes):
            q = self.quotes[k]
            e.s(k).of(q.clean).of(q.ytm).of(q.bid).of(q.offer)
        e.i(len(self.securities))
        for k in sorted(self.securities):
            s = self.securities[k]
            e.s(k).f(s.coupon).d(s.issue_date).d(s.maturity_date)
        e.i(len(self.aliases))
        for k in sorted(self.aliases):
            e.s(k).s(self.aliases[k])


@dataclass(frozen=True)
class MarketSnapshot:
    """Everything one pricer needs at one time. `ts` is the tz-aware stamp of the data; `reference_date` the business date it is anchored to."""

    ts: pd.Timestamp
    reference_date: dt.date
    curves: Mapping[str, CurveSnapshot] = field(default_factory=dict)
    fixings: Mapping[str, FixingsSeries] = field(default_factory=dict)
    quotes: Optional[QuoteSet] = None
    calendars: Mapping[str, CalendarData] = field(default_factory=dict)
    provenance: Mapping[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "curves", dict(self.curves))
        object.__setattr__(self, "fixings", dict(self.fixings))
        object.__setattr__(self, "calendars", dict(self.calendars))
        ts = pd.Timestamp(self.ts)
        if ts.tzinfo is None:
            raise _bad("a snapshot stamp must be tz-aware")
        object.__setattr__(self, "ts", ts)
        for kind, section in (("curve", self.curves), ("fixings", self.fixings), ("calendar", self.calendars)):
            for k, v in section.items():
                if v.name != k:
                    raise _bad(f"{kind} key {k!r} must equal its own name {v.name!r}")
        for c in self.curves.values():
            if c.reference_date != self.reference_date:
                raise _bad(f"curve {c.name!r} is anchored at {c.reference_date}, not the snapshot reference date {self.reference_date}")
        for f in self.fixings.values():
            if f.dates and f.dates[-1] >= self.reference_date:
                raise _bad(f"fixings {f.name!r} run to {f.dates[-1]}: only fixings strictly before the reference date {self.reference_date} exist at the snapshot")
        if self.quotes is not None and self.quotes.reference_date != self.reference_date:
            raise _bad(f"quotes are as of {self.quotes.reference_date}, not the snapshot reference date {self.reference_date}")

    def digest(self) -> str:
        """sha256 hex of the content (not provenance): equal digests mean identical inputs."""
        e = _Enc()
        e.s("snapshot").t(self.ts).d(self.reference_date)
        e.i(len(self.curves))
        for k in sorted(self.curves):
            self.curves[k]._enc(e)
        e.i(len(self.fixings))
        for k in sorted(self.fixings):
            self.fixings[k]._enc(e)
        e.i(len(self.calendars))
        for k in sorted(self.calendars):
            self.calendars[k]._enc(e)
        if self.quotes is None:
            e.s("noquotes")
        else:
            self.quotes._enc(e)
        return e.hexdigest()


class SnapshotPricer(PricerBase):
    """The library-free pricer of a snapshot: data only, no lookups. An adapter's `wrap` turns it into that library's pricer (once per digest)."""

    LOOKUPS = ()

    def __init__(self, snapshot: MarketSnapshot, *, ts: Optional[pd.Timestamp] = None):
        self.snapshot = snapshot
        self.ts = pd.Timestamp(ts) if ts is not None else snapshot.ts
        self.reference_date = snapshot.reference_date
        self.digest = snapshot.digest()

    def describe(self) -> Dict[str, Any]:
        s = self.snapshot
        return {"type": type(self).__name__, "ts": str(self.ts), "stamp": str(s.ts), "reference_date": str(self.reference_date), "digest": self.digest,
                "curves": sorted(s.curves), "fixings": sorted(s.fixings), "n_quotes": 0 if s.quotes is None else len(s.quotes.quotes), **dict(s.provenance)}
