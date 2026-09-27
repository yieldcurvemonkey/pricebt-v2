"""`wrap(snapshot pricer) -> RLCurvePricer`: the rateslib adapter's view of a plain-data market snapshot (spec D2, D3).

A provider emits a `SnapshotPricer` (discount factors, published fixings, a quote panel with reference data and on-the-run aliases, calendars); this module
is the only place that turns it into rateslib objects. Nothing is computed from the data here except what the library needs: the curve is built from the
snapshot nodes (interpolation tag `log_linear_df` -> rateslib `log_linear`, any other tag is an error), the fixings arrive as the percent series the
snapshot already carries, bonds resolve through the snapshot's securities and aliases, and a yield curve for the bond roll layer is interpolated through the
quoted on-the-run yields.

Fixings: the provider already applied the publication policy (only fixings visible at the snapshot stamp are in the snapshot, a proxied date is marked).
`RLCurvePricer.__init__` would apply the policy a second time, so the wrapped classes call it WITHOUT fixings and then install the series directly on the
(otherwise frozen) pricer; the policy is therefore applied exactly once, by the provider that owns the data.
"""
from __future__ import annotations

import datetime as dt
import re
import weakref
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as np
import pandas as pd

from ...errors import ConfigError, MarketDataUnavailable
from ...snapshot import CurveSnapshot, FixingsSeries, MarketSnapshot, QuoteSet, SnapshotPricer
from . import _compat as C
from .bond import make_ust, market_clean, ytm_from_clean
from .pricer import BondQuote, BondRef, RLCurvePricer

#: snapshot interpolation tag -> rateslib interpolation name
TAGS = {"log_linear_df": "log_linear"}
PLACEHOLDER_ID = "no_curve"
_ALIAS = re.compile(r"(CT|OOO|OO|O)(\d+)")


def rl_interpolation(tag: str) -> str:
    """The rateslib interpolation of a snapshot tag; a tag with no native equivalent is an error (never a silent substitute)."""
    if tag not in TAGS:
        raise ConfigError(f"rateslib cannot honour the snapshot interpolation {tag!r}; supported: {sorted(TAGS)}", code="WRAP")
    return TAGS[tag]


def _curve_of(c: CurveSnapshot) -> Any:
    return C.float_curve(dict(zip(c.node_dates, c.values)), c.name, rl_interpolation(c.interpolation))


def _placeholder(reference_date: dt.date) -> Any:
    """A flat curve for a quote panel without a funding curve (the pricer then refuses `par_rate` and offers no `curves`)."""
    return C.float_curve({reference_date: 1.0, reference_date + dt.timedelta(days=366 * 60): 1.0}, PLACEHOLDER_ID)


def _series_of(f: FixingsSeries) -> pd.Series:
    """Fixings in PERCENT on a naive midnight ns index (rateslib's form)."""
    if f.unit not in ("percent", "decimal"):
        raise ConfigError(f"unknown fixings unit {f.unit!r}", code="WRAP")
    scale = 100.0 if f.unit == "decimal" else 1.0
    idx = pd.DatetimeIndex(np.array(f.dates, dtype="datetime64[ns]"))
    return pd.Series(np.array(f.values, dtype=float) * scale, index=idx)


def _ref(s: Any) -> BondRef:
    return BondRef(s.id, float(s.coupon), s.issue_date, s.maturity_date, s.label)


class SnapshotUniverse:
    """Bond resolution over a `QuoteSet`: a security id resolves through `securities`, `CT<n>` / `O<n>` / `OO<n>` / `OOO<n>` through the provider's `aliases`."""

    def __init__(self, qs: QuoteSet):
        self._sec, self._aliases = dict(qs.securities), dict(qs.aliases)

    def resolve(self, token: str, asof: dt.date) -> BondRef:
        tok = token.strip()
        for cand in (tok, tok.upper()):
            if cand in self._sec:
                return _ref(self._sec[cand])
        up = tok.upper()
        m = _ALIAS.fullmatch(up)
        if m:
            hit = self._aliases.get(f"{m.group(1)}{int(m.group(2))}")
            if hit is None:
                raise MarketDataUnavailable(None, {"bond": token, "asof": str(asof)}, f"no {token} in the reference table on {asof}")
            return _ref(self._sec[hit])
        if len(up) == 9 and up.isalnum():
            raise MarketDataUnavailable(None, {"bond": token}, f"cusip {up} not in the reference table")
        raise ConfigError(f"unknown bond token {token!r}; use a security id, CT<n>, O<n>, OO<n> or OOO<n>", code="UNIVERSE")


@dataclass(frozen=True)
class YtmCurve:
    """Piecewise-linear ytm (percent) by time to maturity (years) through on-the-run points; flat outside. Used by the RLBond roll layer."""

    ttm: Tuple[float, ...]
    ytm: Tuple[float, ...]

    def __call__(self, t: float) -> float:
        return float(np.interp(float(t), self.ttm, self.ytm))


class WrappedCurvePricer(RLCurvePricer):
    """An `RLCurvePricer` built from a snapshot: the digest and the provenance ride along in `describe()`; fixings are installed as given (see module doc)."""

    def __init__(self, ts: pd.Timestamp, curve: Any, *, snapshot: MarketSnapshot, digest: str, fixings: Optional[pd.Series], **kw: Any):
        super().__init__(ts, curve, fixings=None, **kw)
        object.__setattr__(self, "fixings", fixings)
        object.__setattr__(self, "snapshot", snapshot)
        object.__setattr__(self, "digest", digest)
        object.__setattr__(self, "calendars", dict(snapshot.calendars))

    def describe(self) -> Mapping[str, Any]:
        d = dict(super().describe())
        d.update(self.snapshot.provenance)
        d["fixings_through"] = d.get("fixings_last")
        d["digest"] = self.digest
        return d


class WrappedQuotePricer(WrappedCurvePricer):
    """A quote-panel pricer for `RLBond` (bond / bond_quote / ytm_curve lookups), with the curve of the snapshot or, without one, a placeholder."""

    LOOKUPS = RLCurvePricer.LOOKUPS + ("bid_offer", "quote_flags", "on_the_run", "quoted_cusips", "bond_ytm", "bond_clean", "repo_fixing")

    def __init__(self, ts: pd.Timestamp, curve: Any, *, has_curve: bool, quoteset: QuoteSet, **kw: Any):
        super().__init__(ts, curve, quotes={c: BondQuote(c, clean=q.clean, ytm=q.ytm) for c, q in quoteset.quotes.items()}, universe=SnapshotUniverse(quoteset), **kw)
        object.__setattr__(self, "has_curve", bool(has_curve))
        object.__setattr__(self, "quoteset", quoteset)
        object.__setattr__(self, "_ytm_memo", {})
        if quoteset.price_convention == "market":
            object.__setattr__(self, "CLEAN_CONVENTION", "market")  # read by RLBond.mark through bond.market_clean
        if not has_curve:
            object.__setattr__(self, "curve", None)  # the placeholder only satisfied the constructor: a binding to `@pricer.curve` must fail, not price on it

    def describe(self) -> Mapping[str, Any]:
        if self.has_curve:
            d = dict(super().describe())
            d["curve"] = d.get("curve_id")
            return d
        f = self.fixings
        d = {"type": type(self).__name__, "source": self.source, "ts": str(self.ts), "reference_date": str(self.reference_date), "n_nodes": 0, "curve_id": None, "staleness_s": None,
             "fixings_last": None if f is None or len(f) == 0 else str(f.index[-1].date()), "has_vol": False, "n_quotes": len(self._quotes)}
        d.update(self.snapshot.provenance)
        d.update({"fixings_through": d["fixings_last"], "digest": self.digest, "curve": "none"})
        return d

    def bid_offer(self, cusip: str) -> Optional[Tuple[float, float]]:
        """(bid, offer) clean prices, or None when either side is missing in the source (a spread signal, not a bracket of the mark)."""
        q = self.quoteset.quotes.get(cusip)
        return None if q is None or q.bid is None or q.offer is None else (q.bid, q.offer)

    def quote_flags(self, cusip: str) -> Mapping[str, Any]:
        """Source flags of a quote, e.g. {'repeat': True, 'run': 37} for a forward-filled minute (empty when the provider gave none)."""
        return dict(self.quoteset.provenance.get("flags", {}).get(cusip, {}))

    def on_the_run(self, oi: Any, rank: int = 0) -> BondRef:
        """The rank-th newest issue of the original-issue bucket `oi` ('10-Year' or 10) on the reference date."""
        bucket = f"{int(oi)}-Year" if isinstance(oi, int) or str(oi).isdigit() else str(oi)
        token = {0: "CT", 1: "O", 2: "OO", 3: "OOO"}.get(int(rank))
        if token is None:
            raise MarketDataUnavailable(self.ts, {"oi": bucket, "rank": rank}, "on_the_run needs rank 0..3")
        return self.universe.resolve(f"{token}{bucket.split('-')[0]}", self.reference_date)

    def ytm_curve(self) -> Optional[Any]:
        """ytm-by-ttm through the quoted rank-0 (`CT<n>`) bonds, clean quotes solved at settlement = reference date as RLBond does; a panel without
        aliases has no on-the-run notion, so every quoted bond is used. None below two points."""
        if "ytm_curve" not in self._ytm_memo:
            qs, settle = self.quoteset, C.to_dt(self.reference_date)
            ids = [c for a, c in qs.aliases.items() if a.startswith("CT")] or list(qs.quotes)
            pts: Dict[float, float] = {}
            for c in ids:
                q = qs.quotes.get(c)
                if q is None:
                    continue
                ref = _ref(qs.securities[c])
                y = float(q.ytm) if q.ytm is not None else ytm_from_clean(make_ust(ref), float(q.clean), settle, market=market_clean(self))
                pts[(ref.maturity_date - self.reference_date).days / 365.25] = y
            ks = sorted(pts)
            self._ytm_memo["ytm_curve"] = YtmCurve(tuple(ks), tuple(pts[k] for k in ks)) if len(pts) >= 2 else None
        return self._ytm_memo["ytm_curve"]

    def bond_ytm(self, bond: str) -> float:
        """ytm (percent) of `bond` (id or alias such as 'CT10') at settlement = reference date, exactly as `RLBond.mark` solves it (a ytm quote is
        returned as is)."""
        ref = self.bond(bond)
        hit = self._ytm_memo.get(ref.cusip)
        if hit is not None:
            return hit
        q = self.bond_quote(ref.cusip)
        y = float(q.ytm) if q.ytm is not None else ytm_from_clean(make_ust(ref), float(q.clean), self.reference_date, market=market_clean(self))
        self._ytm_memo[ref.cusip] = y
        return y

    def bond_clean(self, bond: str) -> float:
        """Clean price per 100 face of `bond` (the quote, or priced from a ytm quote at settlement = reference date)."""
        ref = self.bond(bond)
        q = self.bond_quote(ref.cusip)
        if q.clean is not None:
            return float(q.clean)
        return C.finite(make_ust(ref).price(float(q.ytm), C.to_dt(self.reference_date), dirty=False))

    def repo_fixing(self) -> float:
        """The newest published overnight fixing (percent) dated before the reference date (what `RLBond` repo `gc_rate: pricer` uses)."""
        f = self.fixings
        if f is None or len(f) == 0:
            raise MarketDataUnavailable(self.ts, {}, "this quote pricer carries no fixings (provider `fixings: auto` or a curve_mdp)")
        return float(f.iloc[-1])


_MEMO: "weakref.WeakKeyDictionary[SnapshotPricer, Dict[Tuple[Optional[str], Optional[str]], RLCurvePricer]]" = weakref.WeakKeyDictionary()


def wrap(pricer: SnapshotPricer, *, curve: Optional[str] = None, fixings: Optional[str] = None) -> RLCurvePricer:
    """The rateslib pricer of a snapshot pricer (memoised per snapshot pricer: one snapshot, one shared immutable pricer, and no global state).

    `curve` / `fixings` name the entry of the snapshot to use when it holds several (a single entry is taken as is; none gives no curve / no fixings).
    A snapshot with a quote panel gives a `WrappedQuotePricer`, one without a `WrappedCurvePricer` (and then needs a curve).
    """
    snap = pricer.snapshot
    memo = _MEMO.setdefault(pricer, {})
    hit = memo.get((curve, fixings))
    if hit is not None:
        return hit
    cs = _pick(snap.curves, curve, "curve")
    fs = _pick(snap.fixings, fixings, "fixings")
    ts = pd.Timestamp(pricer.ts)
    common: Dict[str, Any] = {"snapshot": snap, "digest": pricer.digest, "fixings": None if fs is None else _series_of(fs), "source": str(snap.provenance.get("source", "")),
                              "tz": str(ts.tz), "reference_date": snap.reference_date}
    if snap.quotes is not None:
        out: RLCurvePricer = WrappedQuotePricer(ts, _placeholder(snap.reference_date) if cs is None else _curve_of(cs), has_curve=cs is not None, quoteset=snap.quotes, **common)
    elif cs is None:
        raise ConfigError("the snapshot has neither a curve nor a quote panel: nothing to wrap", code="WRAP")
    else:
        out = WrappedCurvePricer(ts, _curve_of(cs), **common)
    memo[(curve, fixings)] = out
    return out


def _pick(section: Mapping[str, Any], name: Optional[str], what: str) -> Any:
    if name is not None:
        if name not in section:
            raise ConfigError(f"the snapshot has no {what} {name!r}; it has {sorted(section)}", code="WRAP")
        return section[name]
    if len(section) > 1:
        raise ConfigError(f"the snapshot holds several {what}s {sorted(section)}: name one", code="WRAP")
    return next(iter(section.values()), None)
