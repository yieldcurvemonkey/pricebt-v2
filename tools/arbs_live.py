"""Opt-in, GUARDED, read-only bridge to the maintainer's own pricing infrastructure: the parity oracle of spec section 10 (G2c).

The oracle values one trade with ARBS's own classes (the ARBS-native 10Y par rate, the NPV and the analytic PV01 of a payer swap struck at 4%) and returns those numbers
together with the curve nodes ARBS served, as plain JSON. The COMPARISON happens on the pricebt side (tests/test_arbs_bridge.py): the nodes become a snapshot, an adapter
values the same trade, and the two agree or they do not. This module builds no pricebt object and contains no pricing; it is the only place ARBS code is imported (lazily,
inside functions), and only in a child process (`python -m tools.arbs_live ...`), never in a test runner. Order (docs/research/mdp-feasibility.md section 5):

1. `ARBS_SUPABASE_ENABLED=0` before any ARBS import (refused if `Caching` is already imported), plus ARBS's own prod-db guard;
2. tripwires: non-loopback sockets/DNS, Excel through subprocess / `ShellExecuteW` / `os.startfile` / win32com, writes under the
   ARBS repo and `%LOCALAPPDATA%\\ARBS` except the scratch root (best effort: native writers such as sqlite bypass it);
3. `_fetch_fixings` -> read-only reader of the newest cached `fixings.csv`; Citi Excel fixings -> empty; CitiVeloQuotes offline;
4. `update_reference_data` -> read-only reader of the newest cached fiscaldata parquet (bond recipes);
5. every diskcache root -> the scratch root; `CurveStore.write_day` / `write_analytics_day` refused.
Request dicts are built fresh per call (ARBS pops keys) and the served snapshot is validated (`validate_served`), never trusted.
"""
from __future__ import annotations

import contextlib
import copy
import datetime as dt
import glob
import io
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union

import pandas as pd

from pricebt.errors import ConfigError, LookAheadError, MarketDataUnavailable, PricebtError, StaleSnapshot

NY = "America/New_York"
PathLike = Union[str, "os.PathLike[str]"]
_MARK = "__pricebt_guard__"


class GuardBlocked(PricebtError, OSError):
    """A tripwire refused a network, process, COM or protected-write call."""


class GuardSetupError(PricebtError):
    """The guard was installed out of order (an ARBS module already imported with the database flag on) or twice."""


class ArbsUnavailable(PricebtError, ImportError):
    """ARBS is absent, not opted into, or a live call was made without the guard."""


# ------------------------------------------------------------------------------ paths and status
@dataclass(frozen=True)
class ArbsPaths:
    arbs_root: Path
    appdata_root: Path
    scratch_root: Path

    @property
    def fixings_root(self) -> Path:
        return self.appdata_root / "MDP" / "IRSwaps" / "ARBS" / "MDP" / "IRSwaps" / "Cache" / "fixings_cache"

    @property
    def store_root(self) -> Path:
        return self.appdata_root / "Cache" / "curve_store" / "raw"

    @property
    def reference_root(self) -> Path:
        return self.arbs_root / "MDP" / "FixedRateBonds" / "reference_data_cache" / "ust_reference_data" / "fiscaldata"


def default_paths(arbs_root: Optional[PathLike] = None) -> ArbsPaths:
    """`arbs_root` or $PRICEBT_ARBS_ROOT (the ARBS checkout), %LOCALAPPDATA%\\ARBS, $PRICEBT_ARBS_SCRATCH (default <tempdir>/pricebt_arbs)."""
    root, local = arbs_root or os.environ.get("PRICEBT_ARBS_ROOT"), os.environ.get("LOCALAPPDATA")
    if not root or not local:
        raise ArbsUnavailable("pass arbs_root or set PRICEBT_ARBS_ROOT (and LOCALAPPDATA) to use the live ARBS recipes")
    scratch = Path(os.environ.get("PRICEBT_ARBS_SCRATCH") or Path(tempfile.gettempdir()) / "pricebt_arbs")
    return ArbsPaths(Path(root), Path(local) / "ARBS", scratch)


def arbs_status(arbs_root: Optional[PathLike] = None) -> Tuple[bool, str]:
    """(available, reason) without importing ARBS: opt-in env, checkout files and the curve store must all be present."""
    if os.environ.get("PRICEBT_LIVE_ARBS") != "1":
        return False, "PRICEBT_LIVE_ARBS is not '1' (live ARBS runs are opt-in)"
    try:
        p = default_paths(arbs_root)
    except ArbsUnavailable as e:
        return False, str(e)
    for f in (p.arbs_root / "MDP" / "IRSwaps" / "IRSwapsMDP.py", p.arbs_root / "Caching" / "curve_store.py"):
        if not f.is_file():
            return False, f"ARBS checkout incomplete: {f} missing"
    if not p.store_root.is_dir():
        return False, f"curve store not reachable: {p.store_root}"
    return True, "ok"


# ------------------------------------------------------------------------------ layer 1: tripwires (stdlib only)
@dataclass
class GuardHandle:
    """Record of refused calls; `restore` holds (owner, attribute, original) for `uninstall`."""

    protected: Tuple[Path, ...]
    allow: Tuple[Path, ...]
    attempts: List[Tuple[str, str]] = field(default_factory=list)
    restore: List[Tuple[Any, str, Any]] = field(default_factory=list)
    arbs_patched: bool = False

    def block(self, kind: str, detail: str) -> None:
        self.attempts.append((kind, detail[:300]))
        raise GuardBlocked(f"offline guard blocked {kind}: {detail[:200]}")

    def kinds(self) -> List[str]:
        return [k for k, _ in self.attempts]

    def uninstall(self) -> None:
        for owner, name, orig in reversed(self.restore):
            setattr(owner, name, orig)
        self.restore.clear()


def active_guard() -> Optional[GuardHandle]:
    import socket

    return getattr(socket.socket.connect, _MARK, None)


def _forms(p: Any) -> Tuple[Path, ...]:
    try:
        raw = Path(os.path.abspath(os.fspath(p)))
    except TypeError:
        return ()
    try:
        return (raw, raw.resolve(strict=False))
    except OSError:
        return (raw,)


def _under(p: Any, roots: Sequence[Path]) -> bool:
    return any(f == r or r in f.parents for f in _forms(p) for r in roots)


def _patch(h: GuardHandle, owner: Any, name: str, new: Any) -> None:
    h.restore.append((owner, name, getattr(owner, name)))
    setattr(owner, name, new)


def install_tripwires(protected: Sequence[PathLike], allow: Sequence[PathLike] = ()) -> GuardHandle:
    """Arm the process-global tripwires (network, Excel/process, COM, protected writes). Refuses a second install."""
    import builtins
    import socket
    import subprocess

    if active_guard() is not None:
        raise GuardSetupError("offline guard already installed in this process")
    h = GuardHandle(tuple(f for p in protected for f in _forms(p)), tuple(f for p in allow for f in _forms(p)))

    def write_ok(path: Any) -> bool:
        return not _under(path, h.protected) or _under(path, h.allow)

    local = ("127.0.0.1", "localhost", "::1", "0.0.0.0", "", None)
    real_connect, real_connect_ex, real_gai = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo

    def g_connect(self: Any, address: Any) -> Any:
        if isinstance(address, tuple) and address[0] in local:
            return real_connect(self, address)
        return h.block("net", f"connect {address!r}")

    def g_connect_ex(self: Any, address: Any) -> Any:
        if isinstance(address, tuple) and address[0] in local:
            return real_connect_ex(self, address)
        return h.block("net", f"connect_ex {address!r}")

    def g_gai(host: Any, *a: Any, **k: Any) -> Any:
        if host in local:
            return real_gai(host, *a, **k)
        return h.block("dns", f"getaddrinfo {host!r}")

    setattr(g_connect, _MARK, h)
    _patch(h, socket.socket, "connect", g_connect)
    _patch(h, socket.socket, "connect_ex", g_connect_ex)
    _patch(h, socket, "getaddrinfo", g_gai)

    real_popen = subprocess.Popen

    class GuardedPopen(real_popen):  # type: ignore[misc, valid-type]
        def __init__(self, args: Any, *a: Any, **k: Any) -> None:
            desc = args if isinstance(args, str) else " ".join(map(str, args))
            if "excel" in desc.lower():
                h.block("process", desc)
            super().__init__(args, *a, **k)

    _patch(h, subprocess, "Popen", GuardedPopen)
    real_system = os.system
    _patch(h, os, "system", lambda cmd: h.block("process", str(cmd)) if "excel" in str(cmd).lower() else real_system(cmd))
    if hasattr(os, "startfile"):
        _patch(h, os, "startfile", lambda path, *a, **k: h.block("startfile", str(path)))
    try:
        import ctypes

        shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
        _patch(h, shell32, "ShellExecuteW", lambda *a, **k: h.block("shell", repr(a[2:4])))
    except (ImportError, AttributeError, OSError):
        pass
    import importlib.util

    if importlib.util.find_spec("win32com") is not None:
        import win32com.client as wc

        for n in ("Dispatch", "DispatchEx", "GetActiveObject", "GetObject"):
            if hasattr(wc, n):
                _patch(h, wc, n, partial(lambda name, *a, **k: h.block("com", name), n))
    if importlib.util.find_spec("psycopg2") is not None:
        import psycopg2

        _patch(h, psycopg2, "connect", lambda *a, **k: h.block("db", "psycopg2.connect"))

    real_open, real_os_open = builtins.open, os.open
    wflags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC

    def g_open(file: Any, mode: str = "r", *a: Any, **k: Any) -> Any:
        if not isinstance(file, int) and any(c in mode for c in "wax+") and not write_ok(file):
            h.block("fs_write", f"open({file!r}, {mode!r})")
        return real_open(file, mode, *a, **k)

    def g_os_open(path: Any, flags: int, *a: Any, **k: Any) -> Any:
        if flags & wflags and not write_ok(path):
            h.block("fs_write", f"os.open({path!r})")
        return real_os_open(path, flags, *a, **k)

    _patch(h, builtins, "open", g_open)
    _patch(h, io, "open", g_open)
    _patch(h, os, "open", g_os_open)
    for name in ("mkdir", "remove", "unlink", "rmdir", "utime", "chmod", "truncate"):
        if hasattr(os, name):
            real = getattr(os, name)
            _patch(h, os, name, partial(lambda fn, nm, path, *a, **k: fn(path, *a, **k) if isinstance(path, int) or write_ok(path) else h.block("fs_write", f"os.{nm}({path!r})"), real, name))
    for name in ("rename", "replace", "link", "symlink"):
        real = getattr(os, name)
        _patch(h, os, name, partial(lambda fn, nm, src, dst, *a, **k: fn(src, dst, *a, **k) if write_ok(src) and write_ok(dst) else h.block("fs_write", f"os.{nm}({src!r}, {dst!r})"), real, name))
    if sys.platform == "win32":
        import _winapi

        if hasattr(_winapi, "CopyFile2"):
            real_copy = _winapi.CopyFile2
            _patch(h, _winapi, "CopyFile2", lambda src, dst, *a, **k: real_copy(src, dst, *a, **k) if write_ok(dst) else h.block("fs_write", f"CopyFile2 -> {dst!r}"))
    return h


def tripwire_selftest(h: GuardHandle, protected_dir: Path, allow_dir: Path) -> Dict[str, bool]:
    """Each refusal must raise GuardBlocked; each control must work. Returns {case: passed}."""
    import shutil
    import socket
    import subprocess

    def refused(fn: Callable[[], Any]) -> bool:
        try:
            fn()
        except GuardBlocked:
            return True
        except OSError:
            return False
        return False

    def works(fn: Callable[[], Any]) -> bool:
        fn()
        return True

    victim = protected_dir / "victim.txt"
    out = {
        "net_connect": refused(lambda: socket.create_connection(("192.0.2.1", 80), timeout=0.5)),
        "dns": refused(lambda: socket.getaddrinfo("example.com", 80)),
        "excel_popen": refused(lambda: subprocess.Popen(["EXCEL.EXE", "/x", "anchor.xlsx"])),
        "excel_os_system": refused(lambda: os.system("excel_probe_not_installed.exe /x")),
        "open_w": refused(lambda: open(protected_dir / "new.txt", "w")),
        "path_write_text": refused(lambda: (protected_dir / "new2.txt").write_text("x")),
        "open_append": refused(lambda: open(victim, "a")),
        "mkdir": refused(lambda: (protected_dir / "d").mkdir()),
        "unlink": refused(lambda: victim.unlink()),
        "rename": refused(lambda: os.rename(victim, protected_dir / "moved.txt")),
        "rmtree": refused(lambda: shutil.rmtree(protected_dir)),
        "copy2": refused(lambda: shutil.copy2(allow_dir / "src.txt", protected_dir / "copied.txt")),
        "read_protected_ok": works(lambda: victim.read_text()),
        "write_allowed_ok": works(lambda: (allow_dir / "ok.txt").write_text("fine")),
        "benign_process_ok": works(lambda: subprocess.run([sys.executable, "-c", "pass"], check=True)),
    }
    if hasattr(os, "startfile"):
        out["startfile"] = refused(lambda: os.startfile(str(victim)))
    try:
        import ctypes

        out["shell_execute"] = refused(lambda: ctypes.windll.shell32.ShellExecuteW(None, "open", "excel.exe", None, None, 1))  # type: ignore[attr-defined]
    except (ImportError, AttributeError):
        pass
    return out


# ------------------------------------------------------------------------------ layer 2: ARBS patches
def _offline_fixings(root: Path, as_of_date: Any = None, curve_name: str = "USD-SOFR-1D", force_refresh: bool = False) -> pd.Series:
    """Read-only stand-in for ARBS `_fetch_fixings`: the newest cached fixings.csv (DECIMAL, ascending); whole history like the original."""
    files = sorted(glob.glob(str(root / f"{curve_name}_fixings" / "*" / "fixings.csv")))
    if not files:
        raise FileNotFoundError(f"no cached fixings for {curve_name} under {root}")
    df = pd.read_csv(files[-1])
    idx = pd.to_datetime(df.iloc[:, 0], errors="coerce").dt.normalize()
    s = pd.Series(df["Fixing"].to_numpy(), index=pd.DatetimeIndex(idx, name=curve_name), name="Fixing")
    s = s[~s.index.isna()]
    return s[~s.index.duplicated(keep="first")].dropna().sort_index()


def _offline_reference(root: Path, source: str = "fiscaldata", source_kwargs: Any = None, force_refresh: bool = False) -> pd.DataFrame:
    files = sorted(glob.glob(str(root / "*" / "*.parquet")))
    if not files:
        raise FileNotFoundError(f"no cached fiscaldata reference parquet under {root}")
    return pd.read_parquet(files[-1])


def install_guard(paths: Optional[ArbsPaths] = None, *, frb: bool = False) -> GuardHandle:
    """Both layers in the mandatory order. Call ONCE, first thing, in a dedicated child process."""
    paths = paths or default_paths()
    if "Caching" in sys.modules and os.environ.get("ARBS_SUPABASE_ENABLED") != "0":
        raise GuardSetupError("an ARBS module is already imported with ARBS_SUPABASE_ENABLED != 0; start a fresh process")
    os.environ["ARBS_SUPABASE_ENABLED"] = "0"
    sys.dont_write_bytecode = True
    scratch = paths.scratch_root
    if _under(scratch, (paths.arbs_root, paths.appdata_root)):
        raise GuardSetupError(f"scratch root {scratch} lies inside a protected ARBS root")
    scratch.mkdir(parents=True, exist_ok=True)
    h = install_tripwires((paths.arbs_root, paths.appdata_root), (scratch,))
    if str(paths.arbs_root) not in sys.path:
        sys.path.insert(0, str(paths.arbs_root))
    from Caching.prod_db_guard import install_prod_db_guard

    install_prod_db_guard()
    from Caching.DiskCacheMixin import DiskCacheMixin

    DiskCacheMixin.CACHE_ROOT = scratch / "diskcache"
    import MDP.IRSwaps.fixings_cache.fixings_cache as fc

    reader = partial(_offline_fixings, paths.fixings_root)
    fc._fetch_fixings = reader
    import MDP.IRSwaps.IRSwapsMDP as im

    im._fetch_fixings = reader
    import MDP.IRSwaps.CITIVELO_EXCEL.fixings as cf

    cf.citi_fixings = lambda *a, **k: pd.Series(dtype="float64")
    from MDP.CitiVelocityExcel.quotes import CitiVeloQuotes

    orig_init = CitiVeloQuotes.__init__

    def offline_init(self: Any, *a: Any, **k: Any) -> None:
        k["offline"], k["auto_launch"] = True, False
        orig_init(self, *a, **k)

    CitiVeloQuotes.__init__ = offline_init
    import MDP.CitiVelocityExcel.supervisor as sup

    sup.launch_excel = lambda *a, **k: h.block("process", "supervisor.launch_excel")
    from Caching.curve_store import CurveStore

    CurveStore.write_day = lambda *a, **k: h.block("store_write", "CurveStore.write_day")
    CurveStore.write_analytics_day = lambda *a, **k: h.block("store_write", "CurveStore.write_analytics_day")
    if frb:
        import MDP.FixedRateBonds.reference_data_cache.ust_reference_data as ur

        ur.update_reference_data = partial(_offline_reference, paths.reference_root)
    h.arbs_patched = True
    return h


# ------------------------------------------------------------------------------ recipes, inner, factory
@dataclass(frozen=True)
class ArbsRecipe:
    """Plain-data recipe for an ARBS `IRSwapsMDP`; `request` is a tuple of pairs so the recipe stays immutable and picklable."""

    id: str
    source: str
    curve_name: str
    kind: str
    request: Tuple[Tuple[str, Any], ...] = ()
    snapshot_minutes: Optional[float] = None
    tolerance: str = "0min"
    fixture_asset: str = ""

    def template(self) -> Dict[str, Any]:
        return {"curve_name": self.curve_name, **copy.deepcopy(dict(self.request))}


RECIPES: Mapping[str, ArbsRecipe] = MappingProxyType({
    "irs.usd_sofr.citi.eod": ArbsRecipe("irs.usd_sofr.citi.eod", "CITIVELO_EXCEL-RL", "USD-SOFR-1D", "eod", (("offline", True),), fixture_asset="USD-SOFR-1D-CITIVELOEXCEL"),
    "irs.usd_sofr.citi.min": ArbsRecipe("irs.usd_sofr.citi.min", "CITIVELO_EXCEL-RL", "USD-SOFR-1D", "minute", (), 5.0, "5min", "USD-SOFR-1D-CITIVELOEXCELMIN"),
})


def recipe(name_or_recipe: Any) -> ArbsRecipe:
    if isinstance(name_or_recipe, ArbsRecipe):
        return name_or_recipe
    if name_or_recipe not in RECIPES:
        raise ConfigError(f"unknown ARBS recipe {name_or_recipe!r}; known {sorted(RECIPES)}", code="ARBS")
    return RECIPES[name_or_recipe]


class ArbsIrsInner:
    """ARBS's request-dict source: `get_pricer(request_dict) -> RLIRSwapCurve`, built lazily, only under the guard (the request is deep-copied: ARBS pops keys)."""

    def __init__(self, rec: Any):
        self.recipe = recipe(rec)
        self._mdp: Any = None

    def get_pricer(self, request: Mapping[str, Any]) -> Any:
        if active_guard() is None or not active_guard().arbs_patched:
            raise ArbsUnavailable("live ARBS calls need install_guard() first (in a dedicated child process)")
        req = copy.deepcopy(dict(request))
        when = req.get("timestamp")
        if self.recipe.kind == "minute":
            if not isinstance(when, dt.datetime) or when.tzinfo is None:
                raise MarketDataUnavailable(when, req, "minute recipes need a tz-aware datetime (ARBS silently localises naive ones)")
            if (when.hour, when.minute, when.second, when.microsecond) == (0, 0, 0, 0):
                raise MarketDataUnavailable(when, req, "midnight_reads_as_eod")
            from MDP.IRSwaps.CITIVELO_EXCEL.snapshot_policy import SnapshotPolicy

            req["snapshot_policy"] = SnapshotPolicy.strict(minutes=float(self.recipe.snapshot_minutes or 1.0))
        if self._mdp is None:
            from MDP.IRSwaps.IRSwapsMDP import IRSwapsMDP

            self._mdp = IRSwapsMDP(source=self.recipe.source)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                return self._mdp.get_pricer(req)
        except GuardBlocked as e:
            raise MarketDataUnavailable(when, request, f"arbs_live_path_blocked: {e}") from e
        except LookupError as e:
            raise MarketDataUnavailable(when, request, f"arbs_miss: {e}") from e


def served_stamp(raw: Any) -> Optional[pd.Timestamp]:
    """The instant the ARBS curve is really struck at: `handle().timestamp`, else a tz-aware `meta()['timestamp']`; None otherwise."""
    ts = getattr(raw.handle(), "timestamp", None)
    if ts is None:
        ts = (raw.meta() or {}).get("timestamp")
    if ts is None or isinstance(ts, dt.date) and not isinstance(ts, dt.datetime):
        return None
    t = pd.Timestamp(ts)
    return None if t.tzinfo is None else t


def request_for(rec: Any, ts: Any) -> Dict[str, Any]:
    """A FRESH request dict for `ts` (ARBS pops the keys it reads, so a dict is never reused): EOD recipes carry the local date, minute recipes the tz-aware minute (`ts`
    floored), exactly the two time policies the retired request wrapper had."""
    r = recipe(rec)
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        raise MarketDataUnavailable(t, {}, "the oracle needs a tz-aware timestamp")
    when: Any = t.tz_convert(NY).date() if r.kind == "eod" else t.floor("min").to_pydatetime()
    return {**r.template(), "timestamp": when}


def validate_served(raw: Any, ts: Any, rec: Any, *, tz: str = NY, allow_rebuilt: bool = False) -> pd.Timestamp:
    """The instant ARBS really struck the served curve at, after checking it against the request: no tz-aware stamp (StaleSnapshot); a stamp after `ts`
    (LookAheadError); a curve ARBS rebuilt from quotes (`meta()['provenance']`, unless allow_rebuilt: MarketDataUnavailable); EOD: served date or reference date != the
    requested date (StaleSnapshot); minute: ts - stamp > the recipe's tolerance (StaleSnapshot)."""
    r, t = recipe(rec), pd.Timestamp(ts)
    stamp = served_stamp(raw)
    if stamp is None:
        raise StaleSnapshot(ts, {}, "arbs served no tz-aware snapshot stamp")
    stamp = stamp.tz_convert(tz)
    if stamp > t:
        raise LookAheadError(f"ARBS served a snapshot stamped {stamp} for a request at {t} (served_future)")
    meta = dict(raw.meta() or {})
    if meta.get("provenance") and not allow_rebuilt:
        raise MarketDataUnavailable(ts, {}, f"arbs_rebuilt_curve: {meta.get('provenance')}")
    ref = pd.Timestamp(raw.reference_date()).date()
    if r.kind == "eod":
        want = t.tz_convert(tz).date()
        if stamp.date() != want or ref != want:
            raise StaleSnapshot(ts, {}, f"arbs served {stamp} (reference {ref}) for the EOD of {want}")
    elif t - stamp > pd.Timedelta(r.tolerance):
        raise StaleSnapshot(ts, {}, f"arbs served {stamp}, {t - stamp} before the request (tolerance {pd.Timedelta(r.tolerance)})")
    return stamp


# ------------------------------------------------------------------------------ child-process entry points
def oracle_case(inner: Any, rec: Any, iso: str, *, tenor: str = "10Y", notional: float = 1e7, fixed_rate_pct: float = 4.0) -> Dict[str, Any]:
    """One trade valued by ARBS's own classes at `iso`: the spot-starting `tenor` par rate (percent, `build_irswap(fwd='0D')` + `fair_rate`), and the NPV and analytic PV01
    (currency per bp) of a PAYER swap of `notional` struck at `fixed_rate_pct` (ARBS takes decimals: 4% is 0.04, never the par sentinel 0), plus the node table ARBS served
    ({ISO date: discount factor}) so the other side prices the same market. `inner.get_pricer(request) -> raw curve` is `ArbsIrsInner` (guarded) or any stand-in."""
    r = recipe(rec)
    ts = pd.Timestamp(iso)
    raw = inner.get_pricer(request_for(r, ts))
    stamp = validate_served(raw, ts, r)
    nodes = {(d.date() if isinstance(d, dt.datetime) else d).isoformat(): float(v) for d, v in raw.nodes().items()}
    with contextlib.redirect_stdout(io.StringIO()):
        par = float(raw.fair_rate(raw.build_irswap(fwd="0D", tenor=tenor, notional=1_000_000))) * 100.0
        swap = raw.build_irswap(fwd="0D", tenor=tenor, fixed_rate=fixed_rate_pct / 100.0, notional=float(notional))
        npv, pv01 = float(raw.npv(swap)), float(raw.pv01(swap))
    return {"recipe": r.id, "ts": iso, "stamp": stamp.isoformat(), "reference_date": pd.Timestamp(raw.reference_date()).date().isoformat(), "tenor": tenor, "notional": float(notional),
            "fixed_rate": float(fixed_rate_pct), "arbs_par": par, "arbs_npv": npv, "arbs_pv01": pv01, "nodes": nodes}


def oracle_cases(cases: Sequence[Tuple[str, str]], inner_for: Optional[Callable[[ArbsRecipe], Any]] = None) -> List[Dict[str, Any]]:
    """[(recipe id, ISO timestamp)] -> one oracle row each; a case ARBS cannot serve (a miss, a blocked live path, a refused snapshot) is a row with an `error`, not a crash."""
    inners: Dict[str, Any] = {}
    out: List[Dict[str, Any]] = []
    for rid, iso in cases:
        r = recipe(rid)
        inner = inners.setdefault(r.id, inner_for(r) if inner_for is not None else ArbsIrsInner(r))
        try:
            out.append(oracle_case(inner, r, iso))
        except (MarketDataUnavailable, StaleSnapshot, LookAheadError) as e:
            out.append({"recipe": r.id, "ts": iso, "error": f"{type(e).__name__}: {e}"})
    return out


def main(argv: Sequence[str]) -> int:
    """`tripwire-selftest <protected_dir> <allow_dir>` (no ARBS) | `oracle <cases.json>` (guard + ARBS). One `JSON>>` line."""
    cmd, args = (argv[0], list(argv[1:])) if argv else ("", [])
    result: Dict[str, Any]
    if cmd == "tripwire-selftest":
        prot, allow = Path(args[0]), Path(args[1])
        h = install_tripwires((prot,), (allow,))
        result = {"checks": tripwire_selftest(h, prot, allow), "attempts": h.kinds()}
    elif cmd == "oracle":
        h = install_guard(default_paths())
        cases = [tuple(c) for c in json.loads(Path(args[0]).read_text(encoding="utf8"))]
        result = {"rows": oracle_cases(cases), "attempts": h.attempts}
    else:
        sys.stderr.write("usage: python -m tools.arbs_live tripwire-selftest PROT ALLOW | oracle CASES.json\n")
        return 2
    sys.stdout.write("JSON>>" + json.dumps(result, default=str) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
