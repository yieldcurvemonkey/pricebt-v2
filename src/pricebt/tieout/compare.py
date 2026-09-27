"""Comparing two runs of one base config under two stacks (spec X2, X4, X6), level by level. Nothing here names a pricing library.

    L0 inputs      snapshot digests equal; resolved terms equal (dates first); conventions digests equal
    L1 marks       pv, cash, financing per position per point (and the position size)
    L2 measures    dv01, gamma, rate ... per position per point (the UNION of the runs' measure columns: a ladder bucket one run lacks is a failing `structure` row, never an omission)
    L3 layers      by schema name, plus the reconcile() invariants of each run, plus the engine's baseline decomposition
    L4 portfolio   equity, cash, the trade ledger (structure exact, values by tolerance), summary statistics

Both runs must carry an audit trail (`EngineSettings.audit`, see `run_tieout`). A failure is attributed: `input` when L0 already differs (every downstream difference
is then explained by it), `expected` when the layer is declared a known definition difference, `structure` when the two runs do not even have the same rows,
otherwise `exceeds` (a model or convention difference). Each result row says where its worst difference occurs.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from ..errors import PricebtError
from .tolerances import Tol, Tolerances, floor_unit

PASSING = ("exact", "noise", "expected", "info")
#: statistics that are ratios of small numbers or depend on the sizes of the trades: a 1e-6 difference in the sizes moves them by 1e-2 (their own, looser default `L4.stats_ratios`)
RATIO_STATS = frozenset({"skew", "kurt", "sortino", "calmar", "profit_factor", "var", "cvar"})
#: currency traded (`turnover` = sum |quantity x entry pv|): for par swaps the entry pv is numerical zero (~1e-10 per unit), so the total is noise of ~1e-7 currency whose relative size means nothing
#: (their own default `L4.stats_traded`: relative, but absolute below one currency unit)
TRADED_STATS = frozenset({"turnover"})


class TieoutError(PricebtError):
    """The tie-out cannot proceed (a stack overlay touches shared config, a result has no audit trail, the self-test failed)."""


@dataclass
class Row:
    """One line of the report: the worst difference of one quantity at one level (for one asset class)."""

    level: str
    quantity: str
    asset_class: str
    n: int
    max_abs: float
    max_rel: float
    where: str
    tol_rel: float
    tol_floor: float
    status: str
    note: str = ""

    @property
    def passed(self) -> bool:
        return self.status in PASSING


@dataclass
class Offender:
    level: str
    quantity: str
    asset_class: str
    position: str
    ts: Any
    a: float
    b: float
    diff: float
    rel: float


@dataclass
class TieoutReport:
    """The result of comparing run `names[0]` (the reference) with `names[1]`."""

    names: Tuple[str, str]
    rows: List[Row]
    offenders: List[Offender]
    header: Dict[str, Any]
    stack_stats: List[Dict[str, Any]] = field(default_factory=list)
    tolerances: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    disclosure: Dict[str, Any] = field(default_factory=dict)  # what the two stacks own (X1): {factories, wraps, bindings}, each keyed by stack name; set by `run_tieout`, empty for a bare `compare`

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.rows)

    def failures(self) -> List[Row]:
        return [r for r in self.rows if not r.passed]

    def level_rows(self, level: str) -> List[Row]:
        return [r for r in self.rows if r.level == level]

    def frame(self) -> pd.DataFrame:
        cols = ["level", "quantity", "asset_class", "n", "max_abs", "max_rel", "where", "tol_rel", "tol_floor", "status", "note"]
        return pd.DataFrame([{c: getattr(r, c) for c in cols} for r in self.rows], columns=cols)

    def offenders_frame(self) -> pd.DataFrame:
        cols = ["level", "quantity", "asset_class", "position", "ts", "a", "b", "diff", "rel"]
        return pd.DataFrame([{c: getattr(o, c) for c in cols} for o in self.offenders], columns=cols)

    def max_abs(self, level: str, quantity: str, asset_class: Optional[str] = None) -> float:
        got = [r.max_abs for r in self.rows if r.level == level and r.quantity == quantity and (asset_class is None or r.asset_class == asset_class)]
        if not got:
            raise KeyError(f"no result row for {level}.{quantity}")
        return max(got)

    def __repr__(self) -> str:
        bad = self.failures()
        return f"TieoutReport({self.names[0]} vs {self.names[1]}: {'PASS' if not bad else f'{len(bad)} failing rows'}, {len(self.rows)} rows)"


# ------------------------------------------------------------------------------ measuring differences
def _absdiff(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """|a - b| with equal infinities equal (a statistic that is +inf on both sides is not NaN)."""
    with np.errstate(invalid="ignore"):
        return np.where(a == b, 0.0, np.abs(a - b))


def _rel(a: np.ndarray, b: np.ndarray, floor: float, ok: np.ndarray) -> np.ndarray:
    """|a - b| / max(|a|, floor) where `ok`; an infinite difference is an infinite relative difference (not inf / inf = NaN)."""
    diff = _absdiff(a, b)
    with np.errstate(invalid="ignore", divide="ignore"):
        rel = np.where(np.isinf(diff), np.inf, diff / np.maximum(np.abs(a), floor))
    return np.where(ok, rel, 0.0)


def _measure(a: np.ndarray, b: np.ndarray, tol: Tol) -> Tuple[float, float, int, int]:
    """(max abs difference, max relative difference, index of the worst by relative, number of NaN mismatches) of two aligned arrays; a NaN on both sides is ignored."""
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    nan_a, nan_b = np.isnan(a), np.isnan(b)
    mismatch = int((nan_a ^ nan_b).sum())
    ok = ~(nan_a | nan_b)
    if not ok.any():
        return 0.0, 0.0, 0, mismatch
    diff = np.where(ok, _absdiff(a, b), 0.0)
    rel = _rel(a, b, tol.floor, ok)
    return float(diff.max()), float(rel.max()), int(rel.argmax()), mismatch


def _status(max_abs: float, max_rel: float, mismatch: int, tol: Tol) -> str:
    if mismatch:
        return "structure"
    if max_abs == 0.0:
        return "exact"
    if max_rel <= tol.rel:
        return "noise"
    return "expected" if tol.expected else "exceeds"


def _where(keys: pd.DataFrame, i: int) -> str:
    row = keys.iloc[i]
    return ", ".join(f"{k}={row[k]}" for k in keys.columns)


class _Collector:
    def __init__(self, tolerances: Tolerances, top: int, position_roles: Optional[Mapping[str, Optional[frozenset]]] = None):
        self.tol, self.top = tolerances, top
        self.rows: List[Row] = []
        self.offenders: List[Offender] = []
        self.used: Dict[str, Tuple[Tol, str]] = {}  # `<level>.<quantity>[.<asset class>]` -> (the tolerance the row was judged by, the unit of its floor)
        self.explained_positions: set = set()  # positions whose resolved terms already differ at L0
        self.explained_inputs: Dict[Any, set] = {}  # timestamp -> the market roles whose snapshot already differs at L0
        self.explained_all = False  # the conventions blocks differ: everything downstream is explained
        self.position_roles: Mapping[str, Optional[frozenset]] = position_roles or {}  # position -> the market roles its instrument reads (None or absent: the run does not say, any role explains)

    def _input_explains(self, position: Any, ts: Any) -> bool:
        """A snapshot difference at `ts` explains this position only in a market role its instrument reads (any role, when the run does not say which it reads)."""
        got = self.explained_inputs.get(ts)
        if not got:
            return False
        roles = self.position_roles.get(str(position))
        return roles is None or bool(got & roles)

    def _first_explaining_ts(self) -> Any:
        """The first timestamp whose snapshot differs in a role some position reads (portfolio quantities are cumulative over the positions)."""
        reads = self.position_roles.values()
        every = None if not reads or any(r is None for r in reads) else frozenset().union(*reads)  # no positions: the roles are unknown, any role explains
        return min((t for t, got in self.explained_inputs.items() if every is None or got & every), default=None)

    def numeric(self, level: str, quantity: str, asset_class: str, keys: pd.DataFrame, a: pd.Series, b: pd.Series, *, tol_quantity: Optional[str] = None) -> Row:
        tol = self.tol.for_(level, tol_quantity or quantity, asset_class)
        av, bv = a.to_numpy(dtype=float), b.to_numpy(dtype=float)
        mx, mr, i, mismatch = _measure(av, bv, tol)
        n = len(av)
        where = _where(keys, i) if n else ""
        status = _status(mx, mr, mismatch, tol)
        note = f"{mismatch} values are NaN on one side only" if mismatch else ""
        if n and np.isnan(av).all() and np.isnan(bv).all():  # nothing on either side: a comparison that compared nothing must not read `exact`
            status, note = "info", "NaN on both sides: nothing was compared (the measure is not bound for these instruments, or was never computed)"
        elif status == "exceeds" and self._explained(keys, av, bv, tol):
            status, note = "input", "every violating value is at a position or timestamp already explained by an L0 input difference"
        row = Row(level, quantity, asset_class, n, mx, mr, where, tol.rel, tol.floor, status, note)
        self.rows.append(row)
        if tol.rel or tol.floor:
            self.used[f"{level}.{quantity}" + (f".{asset_class}" if asset_class else "")] = (tol, floor_unit(level, quantity))
        if n and mx > 0.0:
            ok = ~(np.isnan(av) | np.isnan(bv))
            rel = _rel(av, bv, tol.floor, ok)
            for j in np.argsort(-rel)[: self.top]:
                if rel[j] <= 0.0:
                    break
                k = keys.iloc[int(j)]
                self.offenders.append(Offender(level, quantity, asset_class, str(k.get("position", "")), k.get("ts", None), float(av[j]), float(bv[j]), float(_absdiff(av[j:j + 1], bv[j:j + 1])[0]), float(rel[j])))
        return row

    def _explained(self, keys: pd.DataFrame, av: np.ndarray, bv: np.ndarray, tol: Tol) -> bool:
        """True only when EVERY value beyond the tolerance sits at a position or timestamp that L0 already explains (a second, unrelated difference is not masked)."""
        if self.explained_all:
            return True
        if not (self.explained_positions or self.explained_inputs):
            return False
        ok = ~(np.isnan(av) | np.isnan(bv))
        rel = _rel(av, bv, tol.floor, ok)
        bad = np.flatnonzero(rel > tol.rel)
        if not len(bad):
            return False
        pos = keys["position"].astype(str).to_numpy() if "position" in keys.columns else None
        ts = keys["ts"].to_numpy() if "ts" in keys.columns else None
        if pos is None and ts is None:
            return False
        if pos is None:  # a portfolio-level quantity (equity, cash) is CUMULATIVE over positions: explained once any position differs, or from the first timestamp whose snapshot differs (in a role a position reads)
            first = self._first_explaining_ts()
            return bool(self.explained_positions) or (first is not None and all(pd.Timestamp(ts[j]) >= first for j in bad))
        return all((pos[j] in self.explained_positions) or (ts is not None and self._input_explains(pos[j], ts[j])) for j in bad)

    def exact(self, level: str, quantity: str, n: int, bad: List[str], note: str = "", status_bad: str = "exceeds") -> Row:
        row = Row(level, quantity, "", n, float(len(bad)), float(len(bad)), "; ".join(bad[:3]), 0.0, 0.0, "exact" if not bad else status_bad, note if not bad else f"{len(bad)} differ: {note}".strip(": "))
        self.rows.append(row)
        return row


# ------------------------------------------------------------------------------ extraction
def _audit(res: Any, name: str) -> Mapping[str, pd.DataFrame]:
    audit = getattr(res.record, "audit", None)
    if not audit:
        raise TieoutError(f"run {name!r} has no audit trail: run it with EngineSettings.audit (pricebt.tieout.run_tieout does)")
    return audit


def _asset_classes(res: Any) -> Dict[str, str]:
    ins = res.manifest.get("instruments", {})
    pos = res.record.positions  # the engine's own table (the result's `positions` is reshaped and has no instrument column)
    return {str(r["id"]): str(ins.get(r["instrument"], {}).get("asset_class", "")) for _, r in pos.iterrows()} if len(pos) else {}


def _tag(df: pd.DataFrame, ac: Mapping[str, str]) -> pd.DataFrame:
    out = df.copy()
    out["asset_class"] = out["position"].map(lambda p: ac.get(str(p), ""))
    return out


def _missing(x: Any) -> bool:
    """None and NaN both mean "this stack did not resolve the term" (a ledger column absent from one run reads back as NaN)."""
    return x is None or (isinstance(x, (float, np.floating)) and math.isnan(x))


def _same(x: Any, y: Any) -> bool:
    if _missing(x) or _missing(y):
        return _missing(x) and _missing(y)
    if isinstance(x, (float, np.floating)) and isinstance(y, (float, np.floating)):
        return abs(float(x) - float(y)) <= 1e-12 * max(1.0, abs(float(x)))
    if isinstance(x, (dt.date, pd.Timestamp)) or isinstance(y, (dt.date, pd.Timestamp)):
        try:
            return pd.Timestamp(x) == pd.Timestamp(y)
        except (TypeError, ValueError):
            return False
    return bool(x == y)


# ------------------------------------------------------------------------------ the levels
def _level0(c: _Collector, a: Any, b: Any, names: Tuple[str, str]) -> None:
    ia, ib = _audit(a, names[0])["inputs"], _audit(b, names[1])["inputs"]
    ka = {(str(r.ts), r.role, r.digest) for r in ia.itertuples()}
    kb = {(str(r.ts), r.role, r.digest) for r in ib.itertuples()}
    if ia["digest"].isna().all() and ib["digest"].isna().all():
        c.rows.append(Row("L0", "snapshot_digests", "", len(ia), 0.0, 0.0, "", 0.0, 0.0, "info", "no digests recorded: the provider's pricers carry none, so identical inputs are not proven"))
    else:
        diff = ka ^ kb
        bad = sorted(f"{t} {r}" for t, r, _ in diff)
        for t, role, _ in diff:
            if t not in ("NaT", "None"):
                c.explained_inputs.setdefault(pd.Timestamp(t), set()).add(str(role))
        c.exact("L0", "snapshot_digests", len(ka | kb), bad, "the two runs did not consume identical snapshots", "input")
    ta, tb = a.trades, b.trades
    cols = sorted({x for x in (*ta.columns, *tb.columns) if x.startswith("term_")})
    oa, ob = _opens(ta).set_index("position"), _opens(tb).set_index("position")
    bad = []
    for pid in sorted(set(oa.index) | set(ob.index)):
        if pid not in oa.index or pid not in ob.index:
            bad.append(f"{pid} opened in one run only")
            c.explained_positions.add(str(pid))
            continue
        for col in cols:
            x, y = (oa.at[pid, col] if col in oa.columns else None), (ob.at[pid, col] if col in ob.columns else None)
            if not _same(x, y):
                bad.append(f"{pid} {col[5:]}: {x} vs {y}")
                c.explained_positions.add(str(pid))
    dates_first = sorted(bad, key=lambda s: (0 if any(k in s for k in ("effective", "maturity", "date")) else 1, s))
    c.exact("L0", "resolved_terms", len(oa), dates_first, "the resolved terms (dates first, then rates and sizes) are the first suspect of any PV difference", "input")
    ma, mb = a.manifest.get("instruments", {}), b.manifest.get("instruments", {})
    bad = [f"{n}: {ma.get(n, {}).get('conventions_digest')} vs {mb.get(n, {}).get('conventions_digest')}" for n in sorted(set(ma) | set(mb))
           if ma.get(n, {}).get("conventions_digest") != mb.get(n, {}).get("conventions_digest")]
    c.explained_all = c.explained_all or bool(bad)
    c.exact("L0", "conventions_digest", len(set(ma) | set(mb)), bad, "the conventions block is shared by every stack (X1)", "input")


def _opens(trades: pd.DataFrame) -> pd.DataFrame:
    """The opening rows of a trade ledger (an empty ledger has no `kind` column)."""
    return trades[trades["kind"] == "open"] if "kind" in trades.columns else trades.iloc[0:0].assign(position=pd.Series(dtype=str))


def _by_class(c: _Collector, level: str, quantity: str, m: pd.DataFrame, ca: str, cb: str, keys: Sequence[str], *, tol_quantity: Optional[str] = None) -> None:
    for ac, g in m.groupby("asset_class", dropna=False):
        c.numeric(level, quantity, str(ac), g[list(keys)].reset_index(drop=True), g[ca].reset_index(drop=True), g[cb].reset_index(drop=True), tol_quantity=tol_quantity)


def _merge(fa: pd.DataFrame, fb: pd.DataFrame, keys: List[str], ac: Mapping[str, str]) -> Tuple[pd.DataFrame, int, int]:
    m = _tag(fa, ac).merge(fb, on=keys, how="outer", suffixes=("_a", "_b"), indicator=True)
    only_a, only_b = int((m["_merge"] == "left_only").sum()), int((m["_merge"] == "right_only").sum())
    both = m[m["_merge"] == "both"].copy()
    return both, only_a, only_b


def _structure(c: _Collector, level: str, what: str, only_a: int, only_b: int, names: Tuple[str, str]) -> None:
    bad = [f"{only_a} rows only in {names[0]}"] * bool(only_a) + [f"{only_b} rows only in {names[1]}"] * bool(only_b)
    c.exact(level, f"{what}_rows", only_a + only_b, bad, "the two runs do not have the same rows", "structure")


def _level1_2(c: _Collector, a: Any, b: Any, names: Tuple[str, str]) -> None:
    ma, mb = _audit(a, names[0])["marks"], _audit(b, names[1])["marks"]
    ac = _asset_classes(a)
    keys = ["position", "ts"]
    qa, qb = ({x[8:] for x in m.columns if x.startswith("measure_")} for m in (ma, mb))
    # a measure column (a ladder is one column per bucket) that one run does not have is a column of NaN there: NaN against values is a `structure` failure, and nothing on either side stays `info`.
    ma, mb = ma.assign(**{f"measure_{q}": np.nan for q in qb - qa}), mb.assign(**{f"measure_{q}": np.nan for q in qa - qb})
    both, only_a, only_b = _merge(ma, mb, keys, ac)
    _structure(c, "L1", "marks", only_a, only_b, names)
    _by_class(c, "L1", "quantity", both, "quantity_a", "quantity_b", keys)
    for q in ("pv", "cash", "financing"):
        _by_class(c, "L1", q, both, f"{q}_a", f"{q}_b", keys)
    for q in sorted(qa | qb):
        first = len(c.rows)
        _by_class(c, "L2", q, both, f"measure_{q}_a", f"measure_{q}_b", keys, tol_quantity=q.split(".")[0])
        if q not in qa or q not in qb:
            for r in c.rows[first:]:
                if r.status == "structure":
                    r.note = f"the measure column exists in {names[0] if q in qa else names[1]} only: it is missing or broken in the other run ({r.note})"


def _layer_refs(res: Any) -> Dict[str, str]:
    """`instrument.layer -> id@version` of a run's manifest."""
    return {f"{n}.{ln}": str(ref) for n, v in res.manifest.get("instruments", {}).items() for ln, ref in (v.get("layers", {}) or {}).items()}


def _level3(c: _Collector, a: Any, b: Any, names: Tuple[str, str], stats: List[Dict[str, Any]]) -> None:
    la, lb = _audit(a, names[0])["layers"], _audit(b, names[1])["layers"]
    ac = _asset_classes(a)
    keys = ["position", "ts", "layer"]
    both, only_a, only_b = _merge(la, lb, keys, ac)
    names_a, names_b = set(la["layer"]), set(lb["layer"])
    lonely = sorted((names_a ^ names_b))
    baseline_missing = [n for n in lonely if n.startswith("tay_")]
    if baseline_missing:
        c.exact("L3", "baseline_layers", len(lonely), [f"{n} in one run only" for n in baseline_missing], "the baseline decomposition must be produced by both", "structure")
    if lonely and not baseline_missing:
        c.rows.append(Row("L3", "layers_in_one_run_only", "", len(lonely), 0.0, 0.0, ", ".join(lonely), 0.0, 0.0, "info", "extension layers of one adapter; not compared"))
    ra, rb = _layer_refs(a), _layer_refs(b)
    diff = [f"{i}: {ra.get(i)} vs {rb.get(i)}" for i in sorted(set(ra) | set(rb)) if ra.get(i) != rb.get(i)]
    c.rows.append(Row("L3", "layer_definitions", "", len(set(ra) | set(rb)), float(len(diff)), float(len(diff)), "; ".join(diff[:3]), 0.0, 0.0, "info" if diff else "exact",
                      "the layer ids and versions differ between the stacks: the layers below are different DEFINITIONS (spec L5, X6), compare invariants and the baseline" if diff else ""))
    both = both[both["layer"].isin(names_a & names_b)]
    common_only = only_a - int(la["layer"].isin(names_a - names_b).sum()) if only_a else 0
    common_only_b = only_b - int(lb["layer"].isin(names_b - names_a).sum()) if only_b else 0
    _structure(c, "L3", "layer", max(common_only, 0), max(common_only_b, 0), names)
    for layer, g in both.groupby("layer"):
        for ac_name, gg in g.groupby("asset_class", dropna=False):
            c.numeric("L3", str(layer), str(ac_name), gg[keys].reset_index(drop=True), gg["amount_a"].reset_index(drop=True), gg["amount_b"].reset_index(drop=True))
            if "unit_a" in gg.columns:  # per UNIT of the position: what the library alone contributes, whatever quantity each stack holds
                c.numeric("L3", f"{layer}/unit", str(ac_name), gg[keys].reset_index(drop=True), gg["unit_a"].reset_index(drop=True), gg["unit_b"].reset_index(drop=True))
    for name, res, lay in ((names[0], a, la), (names[1], b, lb)):
        rep = res.reconcile()
        c.rows.append(Row("L3", f"reconcile[{name}]", "", len(rep.checks), 0.0 if rep.ok else 1.0, 0.0 if rep.ok else 1.0,
                          "" if rep.ok else ", ".join(ch.name for ch in rep.checks if not ch.passed and ch.severity == "error")[:120], 0.0, 0.0, "exact" if rep.ok else "exceeds",
                          "" if rep.ok else "the run's own invariants fail"))
        tagged = _tag(lay, _asset_classes(res))
        lib = tagged[~tagged["layer"].str.startswith("tay_")]
        for ac_name, g in lib.groupby("asset_class", dropna=False):
            total = g.groupby(["position", "ts"])["amount"].sum().abs().sum()
            un = g[g["layer"] == "unexplained"]["amount"].abs().sum()
            stats.append({"stack": name, "asset_class": str(ac_name), "unexplained_share": float(un / total) if total > 0 else 0.0, "pnl_explained_abs": float(total)})


def _level4(c: _Collector, a: Any, b: Any, names: Tuple[str, str]) -> None:
    ea, eb = a.equity, b.equity
    idx = ea.index.union(eb.index)
    bad = [] if ea.index.equals(eb.index) else [f"the equity curves have {len(ea)} and {len(eb)} points"]
    c.exact("L4", "equity_points", len(idx), bad, "", "structure")
    common = ea.index.intersection(eb.index)
    keys = pd.DataFrame({"ts": common})
    for q in ("equity", "cash", "positions_value"):
        c.numeric("L4", q, "", keys, ea.loc[common, q].reset_index(drop=True), eb.loc[common, q].reset_index(drop=True))
    ta, tb = a.trades, b.trades
    struct = ["ts", "position", "kind", "quantity", "action", "template", "reason", "instrument"]
    have = [k for k in struct if k in ta.columns and k in tb.columns]
    classes = _asset_classes(a)
    bad = []
    if len(ta) != len(tb):
        bad.append(f"{len(ta)} trades in {names[0]}, {len(tb)} in {names[1]}")
    else:
        for k in have:
            neq = ~(ta[k].reset_index(drop=True).astype(str) == tb[k].reset_index(drop=True).astype(str))
            if k == "quantity":  # the ledger's sizes follow the position-size tolerance OF THE POSITION'S ASSET CLASS: a strategy that sizes trades from a measured risk declares it there (and says why)
                tq = [c.tol.for_("L1", "quantity", classes.get(str(p), "")) for p in ta["position"]]
                neq = ~np.isclose(ta[k].to_numpy(dtype=float), tb[k].to_numpy(dtype=float), rtol=np.array([max(t.rel, 1e-12) for t in tq]), atol=np.array([max(t.rel * t.floor, 1e-12) for t in tq]))
            if neq.any():
                bad.append(f"{k} differs at trade {int(np.argmax(np.asarray(neq)))}")
    c.exact("L4", "trade_ledger_structure", len(ta), bad, "entries, exits, sizes and timestamps must match exactly", "structure")
    if len(ta) == len(tb) and len(ta):
        keys = pd.DataFrame({"position": ta["position"].reset_index(drop=True), "ts": ta["ts"].reset_index(drop=True)})
        c.numeric("L4", "trade_pv", "", keys, ta["pv"].reset_index(drop=True), tb["pv"].reset_index(drop=True))
        c.numeric("L4", "trade_cash", "", keys, ta["cash"].reset_index(drop=True), tb["cash"].reset_index(drop=True))
    sa, sb = a.summary_stats(), b.summary_stats()
    num = [k for k in sa.index if k in sb.index and isinstance(sa[k], (int, float, np.floating, np.integer)) and not isinstance(sa[k], bool)]
    for label, names in (("stats", [k for k in num if k not in RATIO_STATS | TRADED_STATS]), ("stats_ratios", [k for k in num if k in RATIO_STATS]), ("stats_traded", [k for k in num if k in TRADED_STATS])):
        if names:
            c.numeric("L4", label, "", pd.DataFrame({"position": names}), pd.Series([float(sa[k]) for k in names]), pd.Series([float(sb[k]) for k in names]))


def _position_roles(a: Any, roles: Optional[Mapping[str, Sequence[str]]]) -> Dict[str, Optional[frozenset]]:
    """position id -> the market roles its instrument reads: from `roles` (instrument -> roles; the harness knows them from what it built), else from the run manifest when the run records
    them (`manifest.instruments.<name>.roles`), else None (unknown: any role's snapshot difference explains, as a standalone comparison of results that do not say cannot do better)."""
    ins = a.manifest.get("instruments", {})
    known = {n: (roles or {}).get(n, v.get("roles")) for n, v in ins.items()}
    pos = a.record.positions
    return {str(r["id"]): (frozenset(map(str, known[r["instrument"]])) if known.get(r["instrument"]) is not None else None) for _, r in pos.iterrows()} if len(pos) else {}


def compare(a: Any, b: Any, *, names: Tuple[str, str] = ("a", "b"), tolerances: Optional[Tolerances] = None, top: int = 10, roles: Optional[Mapping[str, Sequence[str]]] = None) -> TieoutReport:
    """Compare two `BacktestResult`s (both with an audit trail); `a` is the reference. Returns the report; it never raises on a difference.

    `roles` (instrument name -> the market roles it reads) lets a snapshot difference in a role no instrument reads stay out of the explanation of a pv difference (`run_tieout` supplies it)."""
    tol = tolerances or Tolerances()
    c = _Collector(tol, top, _position_roles(a, roles))
    stats: List[Dict[str, Any]] = []
    _level0(c, a, b, names)
    _level1_2(c, a, b, names)
    _level3(c, a, b, names, stats)
    _level4(c, a, b, names)
    header = {"reference": names[0], "other": names[1], "n_points": len(a.equity), "n_positions": len(a.positions),
              "config_hash": {names[0]: a.config_hash, names[1]: b.config_hash}, "declared_tolerances": sorted(tol.declared)}
    used = {k: {"rel": t.rel, "floor": t.floor, "expected": t.expected, "reason": t.reason, "unit": unit} for k, (t, unit) in c.used.items()}
    return TieoutReport(names, c.rows, sorted(c.offenders, key=lambda o: -o.rel)[: 4 * top], header, stats, used)
