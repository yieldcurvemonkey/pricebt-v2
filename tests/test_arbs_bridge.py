"""ARBS as a TEST CASE only (spec section 10, B8): foreign infrastructure plugs in through config and bindings alone.

Default suite (no ARBS, no rateslib): an ARBS-SHAPED STUB proves the two patterns of G2(a) end to end, driven by a YAML config and dotted paths only:

  * a REQUEST-DICT data source (`get_pricer(request) -> raw curve`, which POPS the keys it reads, as ARBS does) wrapped by TEST-SIDE code (`RequestSnapshotProvider`)
    into a snapshot provider: the user code that replaced the removed request-dict wrapper class (B8);
  * a PRICER that owns the methods (`npv(sw)`, `pv01(sw)`, `fair_rate(sw)`, ...: the instrument is an ARGUMENT), reached through `pricer_method` bindings whose targets are
    deliberately named unlike the schema; the same-named methods are traps that must never run (Z3).

The stub's "library" is the dependency-free reference stack, so a backtest through the foreign path can be compared to the native run of the same data to the last bit.
The bridge tool (`tools/arbs_live.py`: guard, served-snapshot validation, the oracle) is tested against stand-ins, including a FAKE ARBS checkout in a temporary directory
that tries to launch Excel and write a cache (G4).

Opt-in (`PRICEBT_LIVE_ARBS=1`): the parity oracle values the same trade with ARBS's own classes and with the rateslib adapter (G2c). Never run by default.
"""
import copy
import datetime as dt
import inspect
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Mapping, Optional

import pandas as pd
import pytest

from pricebt import api
from pricebt.config import yamlio
from pricebt.config.loader import build as build_cfg
from pricebt.contracts.spec import Built
from pricebt.errors import ConfigError, LookAheadError, MarketDataUnavailable, StaleSnapshot
from pricebt.market import Binding, MarketData
from pricebt.pricable import MarkContext
from pricebt.pricer import PricerBase
from pricebt.snapshot import CalendarData, CurveSnapshot, FixingsSeries, MarketSnapshot, SnapshotPricer
from pricebt.testing import refstack as R
from pricebt.testing.synthetic import SyntheticMarket
from support.known import ARBS_PAR_10Y, EOD, MIN, needs_fixtures, ny
from tools import arbs_live as live

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
NY = "America/New_York"
core = pytest.mark.core


# ============================================================================ the tool: status, guard, served-snapshot validation, requests
class _Raw:
    """Stand-in for ARBS `RLIRSwapCurve` as the bridge reads it: handle(), reference_date(), meta(); plain python, no library."""

    def __init__(self, ref: dt.date, stamp=None, provenance=None):
        self._ref, self._meta = ref, {"timestamp": stamp, **({"provenance": provenance} if provenance else {})}

    def handle(self):
        return SimpleNamespace()  # no `.timestamp`: the served stamp is meta()['timestamp']

    def reference_date(self):
        return dt.datetime.combine(self._ref, dt.time())

    def meta(self):
        return dict(self._meta)


@core
def test_status_requires_explicit_opt_in_and_a_checkout(monkeypatch, tmp_path):
    monkeypatch.delenv("PRICEBT_LIVE_ARBS", raising=False)
    ok, why = live.arbs_status(tmp_path)
    assert not ok and "PRICEBT_LIVE_ARBS" in why
    monkeypatch.setenv("PRICEBT_LIVE_ARBS", "1")
    monkeypatch.delenv("PRICEBT_ARBS_ROOT", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert "PRICEBT_ARBS_ROOT" in live.arbs_status()[1]
    ok, why = live.arbs_status(tmp_path)
    assert not ok and "checkout incomplete" in why and str(tmp_path) in why


@core
def test_inner_refuses_without_the_guard_and_imports_no_arbs():
    with pytest.raises(live.ArbsUnavailable):
        live.ArbsIrsInner("irs.usd_sofr.citi.eod").get_pricer({"curve_name": "USD-SOFR-1D", "timestamp": dt.date(2026, 8, 5)})
    assert not {m for m in sys.modules if m.split(".")[0] in ("MDP", "Caching", "Query")}


@core
def test_served_snapshot_is_validated_never_trusted():
    eod = "irs.usd_sofr.citi.eod"
    ok = live.validate_served(_Raw(dt.date(2026, 8, 5), ny("2026-08-05 17:00")), ny("2026-08-05 17:00"), eod)
    assert ok == ny("2026-08-05 17:00")
    with pytest.raises(LookAheadError, match="served_future"):
        live.validate_served(_Raw(dt.date(2026, 8, 5), ny("2026-08-05 17:00")), ny("2026-08-05 10:00"), eod)
    with pytest.raises(StaleSnapshot, match="for the EOD of 2026-09-08"):
        live.validate_served(_Raw(dt.date(2026, 9, 4), ny("2026-09-04 15:00")), ny("2026-09-08 17:00"), eod)
    with pytest.raises(StaleSnapshot, match="reference 2026-07-02"):
        live.validate_served(_Raw(dt.date(2026, 7, 2), ny("2026-07-03 17:00")), ny("2026-07-03 17:00"), eod)
    with pytest.raises(MarketDataUnavailable, match="arbs_rebuilt_curve"):
        live.validate_served(_Raw(dt.date(2026, 9, 3), ny("2026-09-03 15:00"), provenance="rateslib_spec"), ny("2026-09-03 17:00"), eod)
    assert live.validate_served(_Raw(dt.date(2026, 9, 3), ny("2026-09-03 15:00"), provenance="rateslib_spec"), ny("2026-09-03 17:00"), eod, allow_rebuilt=True)
    with pytest.raises(StaleSnapshot, match="no tz-aware"):
        live.validate_served(_Raw(dt.date(2026, 8, 5), dt.date(2026, 8, 5)), ny("2026-08-05 17:00"), eod)
    with pytest.raises(StaleSnapshot, match="no tz-aware"):
        live.validate_served(_Raw(dt.date(2026, 8, 5), dt.datetime(2026, 8, 5, 17, 0)), ny("2026-08-05 17:00"), eod)  # a naive stamp: ARBS silently localises these
    mn = "irs.usd_sofr.citi.min"
    assert live.validate_served(_Raw(dt.date(2026, 8, 5), ny("2026-08-05 10:27")), ny("2026-08-05 10:30"), mn) == ny("2026-08-05 10:27")
    with pytest.raises(StaleSnapshot):
        live.validate_served(_Raw(dt.date(2026, 8, 5), ny("2026-08-05 10:24")), ny("2026-08-05 10:30"), mn)
    assert live.validate_served(_Raw(dt.date(2026, 8, 5), ny("2026-08-05 10:25")), ny("2026-08-05 10:30"), mn), "exactly the 5 minute tolerance is served"
    with pytest.raises(StaleSnapshot):
        live.validate_served(_Raw(dt.date(2026, 8, 5), ny("2026-08-05 10:24:59")), ny("2026-08-05 10:30"), mn)


@core
def test_requests_are_fresh_per_call_and_carry_the_recipes_time_policy():
    a, b = live.request_for("irs.usd_sofr.citi.eod", ny("2026-08-05 17:00")), live.request_for("irs.usd_sofr.citi.eod", ny("2026-08-05 17:00"))
    assert a == b == {"curve_name": "USD-SOFR-1D", "offline": True, "timestamp": dt.date(2026, 8, 5)} and a is not b
    a.pop("curve_name")  # ARBS pops what it reads: the next request and the recipe are unaffected
    assert live.request_for("irs.usd_sofr.citi.eod", ny("2026-08-06 09:00"))["curve_name"] == "USD-SOFR-1D"
    assert live.RECIPES["irs.usd_sofr.citi.eod"].template() == {"curve_name": "USD-SOFR-1D", "offline": True}
    late_utc = pd.Timestamp("2026-08-06 01:00", tz="UTC")  # 21:00 on 2026-08-05 in New York
    assert live.request_for("irs.usd_sofr.citi.eod", late_utc)["timestamp"] == dt.date(2026, 8, 5), "the EOD date is the LOCAL date, whatever zone the stamp is in"
    m = live.request_for("irs.usd_sofr.citi.min", ny("2026-08-05 10:30:45"))["timestamp"]
    assert isinstance(m, dt.datetime) and m.tzinfo is not None and (m.hour, m.minute, m.second) == (10, 30, 0), "a minute recipe gets the tz-aware minute (ts floored)"
    with pytest.raises(MarketDataUnavailable, match="tz-aware"):
        live.request_for("irs.usd_sofr.citi.eod", pd.Timestamp("2026-08-05 17:00"))
    with pytest.raises(ConfigError, match="unknown ARBS recipe"):
        live.request_for("nope", ny("2026-08-05 17:00"))


class _FlatRaw:
    """The oracle's view of an ARBS curve with a closed-form pricer (flat continuously compounded rate `r`, annual fixed payments): every number re-derivable by hand.
    Stdlib only: this class is also written into the FAKE ARBS checkout below (`inspect.getsource`)."""

    R_PCT = 4.0

    def __init__(self, ref, stamp, calls=None, r=None):
        import math

        self._ref, self._stamp, self._math, self._r = ref, stamp, math, (self.R_PCT if r is None else r) / 100.0
        self._nodes = {ref + dt.timedelta(days=d): math.exp(-self._r * d / 365.0) for d in (0, 30, 182, 365, 730, 1095, 1826, 2557, 3652, 5479, 10958)}

    def handle(self):
        return type("H", (), {})()

    def meta(self):
        return {"timestamp": self._stamp}

    def reference_date(self):
        return dt.datetime.combine(self._ref, dt.time())

    def nodes(self):
        return {dt.datetime.combine(d, dt.time()): v for d, v in self._nodes.items()}  # ARBS: {datetime: DF}

    def _df(self, years):
        return self._math.exp(-self._r * years)

    def build_irswap(self, fwd=None, tenor="10Y", fixed_rate=-0, notional=None):
        years = int(tenor[:-1])
        sw = type("Swap", (), {})()
        sw.years, sw.notional, sw.fixed_rate = years, 1e6 if notional is None else notional, fixed_rate
        if fixed_rate == 0:
            sw.fixed_rate = self.fair_rate(sw)  # the par sentinel, as in ARBS
        return sw

    def _annuity(self, sw):
        return sum(self._df(i) for i in range(1, sw.years + 1))

    def fair_rate(self, sw):  # DECIMAL
        return (1.0 - self._df(sw.years)) / self._annuity(sw)

    def npv(self, sw):  # rateslib sign: positive notional PAYS fixed
        return sw.notional * self._annuity(sw) * (self.fair_rate(sw) - sw.fixed_rate)

    def pv01(self, sw):  # currency per bp of the fixed rate; sign follows the notional
        return sw.notional * self._annuity(sw) * 1e-4


class _StandInInner:
    """`get_pricer(request) -> raw curve` like `ArbsIrsInner`, but POPPING the request like ARBS does; records what it was asked."""

    def __init__(self, raw_of=None):
        self.seen: List[Dict[str, Any]] = []
        self.raw_of = raw_of or (lambda day, when: _FlatRaw(day, pd.Timestamp(when) if isinstance(when, dt.datetime) else pd.Timestamp(dt.datetime.combine(when, dt.time(17)), tz=NY)))

    def get_pricer(self, request):
        self.seen.append(dict(request))
        name, when = request.pop("curve_name"), request.pop("timestamp")
        assert name == "USD-SOFR-1D"
        return self.raw_of(when if not isinstance(when, dt.datetime) else when.date(), when)


@core
def test_the_oracle_case_reports_arbs_native_numbers_in_pricebt_units_and_never_reuses_a_request():
    inner = _StandInInner()
    row = live.oracle_case(inner, "irs.usd_sofr.citi.eod", "2026-08-05T17:00:00-04:00")
    r = 0.04
    annuity = sum(2.718281828459045 ** (-r * i) for i in range(1, 11))
    par = (1 - 2.718281828459045 ** (-r * 10)) / annuity
    assert row["arbs_par"] == pytest.approx(par * 100.0, rel=1e-12), "ARBS decimals -> percent, the schema's unit"
    assert row["arbs_pv01"] == pytest.approx(1e7 * annuity * 1e-4, rel=1e-12) and row["arbs_npv"] == pytest.approx(1e7 * annuity * (par - 0.04), rel=1e-12)
    assert row["arbs_npv"] > 0 and par > 0.04 and row["fixed_rate"] == 4.0 and row["notional"] == 1e7, "a payer struck BELOW par gains (par is 4.08% on a 4% continuous curve): a positive notional pays fixed"
    assert row["stamp"] == "2026-08-05T17:00:00-04:00" and row["reference_date"] == "2026-08-05"
    assert list(row["nodes"])[:2] == ["2026-08-05", "2026-09-04"] and row["nodes"]["2026-08-05"] == 1.0 and all(isinstance(k, str) for k in row["nodes"])
    live.oracle_case(inner, "irs.usd_sofr.citi.eod", "2026-08-06T17:00:00-04:00")
    assert [(q["curve_name"], q["timestamp"]) for q in inner.seen] == [("USD-SOFR-1D", dt.date(2026, 8, 5)), ("USD-SOFR-1D", dt.date(2026, 8, 6))], "the popped keys never leaked into the next call"
    json.dumps(row)


@core
def test_a_case_arbs_cannot_serve_is_an_error_row_not_a_crash():
    bad = _StandInInner(lambda day, when: _FlatRaw(day - dt.timedelta(days=3), pd.Timestamp(dt.datetime.combine(day - dt.timedelta(days=3), dt.time(17)), tz=NY)))
    rows = live.oracle_cases([("irs.usd_sofr.citi.eod", "2026-08-05T17:00:00-04:00")], inner_for=lambda r: bad)
    assert len(rows) == 1 and "StaleSnapshot" in rows[0]["error"] and "arbs_par" not in rows[0]
    future = _StandInInner(lambda day, when: _FlatRaw(day, pd.Timestamp(dt.datetime.combine(day, dt.time(17)), tz=NY) + pd.Timedelta(hours=2)))
    (row,) = live.oracle_cases([("irs.usd_sofr.citi.eod", "2026-08-05T17:00:00-04:00")], inner_for=lambda r: future)
    assert "LookAheadError" in row["error"] and "served_future" in row["error"], "a snapshot from the future is a refused case too"


@core
def test_tripwires_refuse_in_a_child_process(tmp_path):
    prot, allow = tmp_path / "protected", tmp_path / "scratch"
    prot.mkdir()
    allow.mkdir()
    (prot / "victim.txt").write_text("keep", encoding="utf8")
    (allow / "src.txt").write_text("src", encoding="utf8")
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    r = subprocess.run([sys.executable, "-m", "tools.arbs_live", "tripwire-selftest", str(prot), str(allow)], capture_output=True, text=True, env=env, timeout=180)
    assert r.returncode == 0, r.stderr
    line = [ln for ln in r.stdout.splitlines() if ln.startswith("JSON>>")][-1]
    res = json.loads(line[len("JSON>>"):])
    failed = [k for k, v in res["checks"].items() if not v]
    assert not failed, (failed, res)
    assert sorted(p.name for p in prot.iterdir()) == ["victim.txt"] and (prot / "victim.txt").read_text(encoding="utf8") == "keep"
    assert {"net", "dns", "process", "fs_write"} & set(res["attempts"]) and "fs_write" in res["attempts"]


# ---------------------------------------------------------------------------- a FAKE ARBS checkout: the real guard and the real `oracle` command, no ARBS
_STUB_MODULES = {
    "Caching/__init__.py": "",
    "Caching/prod_db_guard.py": "import os\n\n\ndef install_prod_db_guard():\n    assert os.environ.get('ARBS_SUPABASE_ENABLED') == '0', 'the database flag must be off before any ARBS import'\n",
    "Caching/DiskCacheMixin.py": "class DiskCacheMixin:\n    CACHE_ROOT = None\n",
    "Caching/curve_store.py": "class CurveStore:\n    def write_day(self, *a, **k):\n        raise RuntimeError('a store write reached ARBS code')\n    write_analytics_day = write_day\n",
    "MDP/__init__.py": "",
    "MDP/IRSwaps/__init__.py": "",
    "MDP/IRSwaps/fixings_cache/__init__.py": "",
    "MDP/IRSwaps/fixings_cache/fixings_cache.py": "def _fetch_fixings(*a, **k):\n    raise RuntimeError('unpatched')\n",
    "MDP/IRSwaps/CITIVELO_EXCEL/__init__.py": "",
    "MDP/IRSwaps/CITIVELO_EXCEL/fixings.py": "def citi_fixings(*a, **k):\n    raise RuntimeError('unpatched')\n",
    "MDP/IRSwaps/CITIVELO_EXCEL/snapshot_policy.py": "class SnapshotPolicy:\n    @staticmethod\n    def strict(minutes=1.0):\n        return ('strict', minutes)\n",
    "MDP/CitiVelocityExcel/__init__.py": "",
    "MDP/CitiVelocityExcel/quotes.py": "class CitiVeloQuotes:\n    def __init__(self, *a, **k):\n        pass\n",
    "MDP/CitiVelocityExcel/supervisor.py": "def launch_excel(*a, **k):\n    raise RuntimeError('unpatched')\n",
}


def fake_arbs(tmp_path: Path, trap: str = "") -> Dict[str, str]:
    """A directory shaped like the ARBS checkout (the modules the guard imports) whose `IRSwapsMDP.get_pricer` serves the closed-form flat curve, optionally after TRYING to
    launch Excel (a program that does not exist: were the guard broken, nothing would start) or to write a cache file under the checkout. Returns the child's environment."""
    root, local = tmp_path / "arbs", tmp_path / "local"
    for rel, text in _STUB_MODULES.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf8")
    mdp = root / "MDP" / "IRSwaps" / "IRSwapsMDP.py"
    mdp.write_text(
        "import datetime as dt\nimport subprocess\nimport pandas as pd\n"
        + textwrap.dedent(inspect.getsource(_FlatRaw)).replace("    Stdlib only: this class is also written into the FAKE ARBS checkout below (`inspect.getsource`).", "")
        + textwrap.dedent(f'''

        def _fetch_fixings(*a, **k):
            raise RuntimeError("unpatched")


        class IRSwapsMDP:
            def __init__(self, source=None):
                self.source = source

            def get_pricer(self, request):
                import os

                from Caching.DiskCacheMixin import DiskCacheMixin
                from Caching.curve_store import CurveStore

                assert str(DiskCacheMixin.CACHE_ROOT).startswith(os.environ["PRICEBT_ARBS_SCRATCH"]), "disk caches must live under the scratch root"
                assert len(_fetch_fixings()) == 2, "fixings must come from the read-only cached csv"
                trap = {trap!r}
                if trap == "excel":
                    subprocess.Popen(["excel_probe_not_installed.exe", "/x"])
                if trap == "write":
                    open({str(root / "trap_cache.txt")!r}, "w").write("cache")
                if trap == "store":
                    CurveStore().write_day()
                day = request["timestamp"]
                return _FlatRaw(day, pd.Timestamp(dt.datetime.combine(day, dt.time(17)), tz="America/New_York"))
        '''),
        encoding="utf8",
    )
    (local / "ARBS" / "Cache" / "curve_store" / "raw").mkdir(parents=True, exist_ok=True)
    fx = local / "ARBS" / "MDP" / "IRSwaps" / "ARBS" / "MDP" / "IRSwaps" / "Cache" / "fixings_cache" / "USD-SOFR-1D_fixings" / "20260805" / "fixings.csv"
    fx.parent.mkdir(parents=True, exist_ok=True)
    fx.write_text("Date,Fixing\n2026-08-03,0.043\n2026-08-04,0.0431\n", encoding="utf8")
    return {**os.environ, "PYTHONPATH": f"{SRC}{os.pathsep}{ROOT}", "PYTHONDONTWRITEBYTECODE": "1", "PRICEBT_LIVE_ARBS": "1", "PRICEBT_ARBS_ROOT": str(root), "LOCALAPPDATA": str(local),
            "PRICEBT_ARBS_SCRATCH": str(tmp_path / "scratch")}


def run_oracle(tmp_path, env, cases):
    cf = tmp_path / "cases.json"
    cf.write_text(json.dumps(cases), encoding="utf8")
    r = subprocess.run([sys.executable, "-m", "tools.arbs_live", "oracle", str(cf)], capture_output=True, text=True, env=env, timeout=300, cwd=str(tmp_path))
    assert r.returncode == 0, r.stderr[-3000:]
    return json.loads([ln for ln in r.stdout.splitlines() if ln.startswith("JSON>>")][-1][len("JSON>>"):])


def tree(root: Path):
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


@core
def test_the_oracle_command_runs_the_guard_and_the_bridge_against_a_fake_checkout(tmp_path):
    env = fake_arbs(tmp_path)
    before = tree(tmp_path / "arbs")
    res = run_oracle(tmp_path, env, [["irs.usd_sofr.citi.eod", "2026-08-05T17:00:00-04:00"], ["irs.usd_sofr.citi.eod", "2026-08-06T17:00:00-04:00"]])
    assert res["attempts"] == [] and len(res["rows"]) == 2
    a, b = res["rows"]
    assert a["reference_date"] == "2026-08-05" and b["reference_date"] == "2026-08-06" and a["arbs_par"] == pytest.approx(b["arbs_par"], rel=1e-12)
    assert a["arbs_par"] == live.oracle_case(_StandInInner(), "irs.usd_sofr.citi.eod", "2026-08-05T17:00:00-04:00")["arbs_par"], "the child served the same closed form as the stand-in"
    assert tree(tmp_path / "arbs") == before, "the fake checkout is byte-for-byte unchanged (no bytecode, no cache)"


@core
@pytest.mark.parametrize("trap,kind", [("excel", "process"), ("write", "fs_write"), ("store", "store_write")])
def test_the_guard_refuses_a_live_path_that_launches_excel_or_writes_a_cache(tmp_path, trap, kind):
    env = fake_arbs(tmp_path, trap)
    before = tree(tmp_path / "arbs")
    res = run_oracle(tmp_path, env, [["irs.usd_sofr.citi.eod", "2026-08-05T17:00:00-04:00"]])
    assert [k for k, _ in res["attempts"]] == [kind], res["attempts"]
    (row,) = res["rows"]
    assert "arbs_live_path_blocked" in row["error"] and "arbs_par" not in row, "a blocked live path is reported per case, the run does not crash"
    assert tree(tmp_path / "arbs") == before and not (tmp_path / "arbs" / "trap_cache.txt").exists()


# ============================================================================ the ARBS-SHAPED STUB: plugging in through config and bindings alone
CALLS: List[Any] = []  # what the request-dict source was asked, in order (the stub is built by a config, so the record lives at module level)
TRAPPED: List[str] = []  # any same-named trap method that ran


class RawCurve:
    """What an ARBS-style source returns: a curve object with `nodes()`, `reference_date()`, `meta()`, `index()` (fixings, PERCENT) and `calendar()`; it prices nothing."""

    def __init__(self, nodes, ref, stamp, fixings, calendar):
        self._nodes, self._ref, self._stamp, self._fx, self._cal = nodes, ref, stamp, fixings, calendar

    def handle(self):
        return SimpleNamespace()

    def reference_date(self):
        return dt.datetime.combine(self._ref, dt.time())

    def meta(self):
        return {"timestamp": self._stamp}

    def nodes(self):
        return dict(self._nodes)

    def index(self):
        return self._fx

    def calendar(self):
        return self._cal


class RequestDictSource:
    """`get_pricer(request) -> RawCurve`, popping the keys it reads (a caller that reuses the dict breaks). Its data come from the library-free synthetic market."""

    def __init__(self, market: SyntheticMarket, stamp_shift: pd.Timedelta = pd.Timedelta(0)):
        self.market, self.stamp_shift = market, stamp_shift

    def get_pricer(self, request: Dict[str, Any]) -> RawCurve:
        name, day = request.pop("curve_name"), request.pop("timestamp")
        request.pop("offline", None)
        assert name == "USD-SOFR-1D" and not request, request
        CALLS.append((name, day))
        ts = pd.Timestamp(dt.datetime.combine(day, dt.time(17)), tz=NY)
        snap = self.market.get_pricer(ts).snapshot
        c, f = snap.curves["sofr"], snap.fixings["USD-SOFR-1D"]
        cal = next(iter(snap.calendars.values()))
        return RawCurve(dict(zip(c.node_dates, c.values)), snap.reference_date, ts + self.stamp_shift, pd.Series(f.values, index=pd.DatetimeIndex(f.dates)),
                        (cal.name, cal.holidays, cal.weekmask))


RECIPE = live.ArbsRecipe("stub.eod", "STUB", "USD-SOFR-1D", "eod", (("offline", True),))


class RequestSnapshotProvider:
    """TEST-SIDE code (the B8 replacement of the removed request wrapper): deep-copies the request per call, injects the local date, validates what came back, and emits a
    plain-data snapshot. Nothing of the foreign library survives into the snapshot."""

    def __init__(self, inner: Any):
        self.inner, self.template = inner, RECIPE.template()

    def get_pricer(self, ts: pd.Timestamp, request: Optional[Mapping[str, Any]] = None) -> SnapshotPricer:
        ts = pd.Timestamp(ts)
        req = {**copy.deepcopy(self.template), **copy.deepcopy(dict(request or {})), "timestamp": ts.tz_convert(NY).date()}
        raw = self.inner.get_pricer(req)
        stamp = live.validate_served(raw, ts, RECIPE)
        ref = raw.reference_date().date()
        dates = tuple(raw.nodes())
        fx = raw.index()
        name, holidays, weekmask = raw.calendar()
        snap = MarketSnapshot(stamp, ref, {"sofr": CurveSnapshot("sofr", ref, dates, tuple(raw.nodes()[d] for d in dates))},
                              {"USD-SOFR-1D": FixingsSeries("USD-SOFR-1D", tuple(t.date() for t in fx.index), tuple(float(v) for v in fx.values), "percent")}, None,
                              {name: CalendarData(name, holidays, weekmask)})
        return SnapshotPricer(snap)

    def available_timestamps(self, start, end):
        return self.inner.market.available_timestamps(start, end)


def arbs_shaped_provider(start: str, end: str, seed: int = 7) -> RequestSnapshotProvider:
    """Reached by a dotted path from the config (`kwargs` are plain YAML values)."""
    return RequestSnapshotProvider(RequestDictSource(SyntheticMarket(str(start), str(end), seed=int(seed))))


class ArbsSwap:
    """The raw backend object of the foreign library: it has NO behaviour (in ARBS the pricable is a bare `rl.IRS` and every method lives on the pricer)."""

    def __init__(self, backend: Any):
        self.backend = backend


class ArbsLikePricer(PricerBase):
    """The foreign pricer: it OWNS the methods and takes the instrument as an argument (`pricer.npv(sw)`). Its own library is the reference stack. Rates are DECIMALS, the
    ladder a Series: the bindings convert (scale, reducer). The methods named like the schema (`value`, `dv01`, ...) are traps: nothing may resolve a schema name by name."""

    LOOKUPS = ()

    def __init__(self, sp: SnapshotPricer):
        self.snapshot, self.digest, self.ts, self.reference_date = sp.snapshot, sp.digest, sp.ts, sp.reference_date
        self.ref = R.wrap(sp)

    def describe(self) -> Mapping[str, Any]:
        return {"type": "ArbsLikePricer", "ts": str(self.ts), "reference_date": str(self.reference_date), "digest": self.digest}

    def _ctx(self, ctx) -> MarkContext:
        prev = getattr(ctx, "prev_pricer", None)
        return MarkContext.standalone(self.ref, prev=None if prev is None else prev.ref)

    # ---- the methods the config binds (arbitrary names, instrument as an argument)
    def npv(self, sw: ArbsSwap, ctx: Any) -> tuple:
        v = sw.backend.value(ctx=self._ctx(ctx))
        return (v.pv, v.cash, v.financing)

    def pv01(self, sw: ArbsSwap) -> float:
        return sw.backend.dv01(ctx=MarkContext.standalone(self.ref))

    def gamma_01(self, sw: ArbsSwap) -> float:
        return sw.backend.gamma(ctx=MarkContext.standalone(self.ref))

    def fair_rate(self, sw: ArbsSwap) -> float:
        return sw.backend.rate(ctx=MarkContext.standalone(self.ref)) / 100.0  # DECIMAL

    def ladder(self, sw: ArbsSwap, buckets: List[str]) -> pd.Series:
        return pd.Series(sw.backend.delta_ladder(ctx=MarkContext.standalone(self.ref), tenors=list(buckets)))

    def layer(self, sw: ArbsSwap, which: str, ctx: Any) -> float:
        return getattr(sw.backend, which)(ctx=self._ctx(ctx))

    # ---- traps: the schema's own names must never be called by pricebt
    def value(self, *a, **k):
        TRAPPED.append("value")
        raise AssertionError("value() was resolved by name")

    def dv01(self, *a, **k):
        TRAPPED.append("dv01")
        raise AssertionError("dv01() was resolved by name")

    def gamma(self, *a, **k):
        TRAPPED.append("gamma")
        raise AssertionError("gamma() was resolved by name")

    def delta_ladder(self, *a, **k):
        TRAPPED.append("delta_ladder")
        raise AssertionError("delta_ladder() was resolved by name")

    def carry(self, *a, **k):
        TRAPPED.append("carry")
        raise AssertionError("carry() was resolved by name")


def wrap_stub(sp: SnapshotPricer) -> ArbsLikePricer:
    return ArbsLikePricer(sp)


def stub_factory(pricer: ArbsLikePricer, ts: Any, *, terms: Mapping[str, Any], conventions: Mapping[str, Any]) -> Built:
    """The foreign library builds its instrument (and resolves dates and `par`) on ITS pricer; pricebt gets back the raw object and the resolved terms (T4)."""
    b = R.swap_factory(pricer.ref, ts, terms=terms, conventions=conventions)
    return Built(ArbsSwap(b.obj), b.terms)


TENORS = ["2Y", "5Y", "10Y"]
SW_KW = {"sw": "@instrument"}
FOREIGN_BIND = {
    "value": {"target": {"pricer_method": "npv"}, "kwargs": {**SW_KW, "ctx": "@ctx"}},
    "dv01": {"target": {"pricer_method": "pv01"}, "kwargs": SW_KW},
    "gamma": {"target": {"pricer_method": "gamma_01"}, "kwargs": SW_KW},
    "rate": {"target": {"pricer_method": "fair_rate"}, "kwargs": SW_KW, "scale": 100.0},  # ARBS decimals -> the schema's percent
    "delta_ladder": {"target": {"pricer_method": "ladder"}, "kwargs": {**SW_KW, "buckets": TENORS}, "keys": TENORS, "reduce": "series_to_tenor_dict"},
    **{name: {"target": {"pricer_method": "layer"}, "kwargs": {**SW_KW, "which": name, "ctx": "@ctx"}} for name in ("carry", "roll", "delta", "convexity")},
}
NATIVE_BIND = {
    "delta_ladder": {"target": {"method": "delta_ladder"}, "kwargs": {"ctx": "@ctx", "tenors": TENORS}, "keys": TENORS, "reduce": "dict_of_floats"},
}
CFG = """
name: arbs_shaped_stub
registry: {allow: [pricebt, test_arbs_bridge]}
backtest:
  tz: America/New_York
  grid: {start: 2024-02-01, end: 2024-04-30, freq: 1b}
  progress: {show: false}
  attribution: {layers: [carry, roll, delta, convexity], cadence: eod}
  measures: [dv01]
  vector_measures: [delta_ladder]
market:
  mdps:
    rates: {type: "test_arbs_bridge:arbs_shaped_provider", kwargs: {start: 2024-01-02, end: 2024-06-28, seed: 7}}
  pricers:
    primary: {mdp: rates, wrap: "test_arbs_bridge:wrap_stub"}
instruments:
  sw:
    asset_class: swap
    factory: "test_arbs_bridge:stub_factory"
    layers: [carry, roll, delta, convexity]
trades:
  seasoned: {instrument: sw, terms: {side: pay, effective: 2023-03-08, maturity: 3Y, notional: 2e7, fixed_rate: 4.10}}
  book: {instrument: sw, terms: {side: receive, maturity: 5Y, notional: 1e7}}
strategy:
  initial_portfolio: [{template: seasoned, quantity: 1}]
  triggers:
    - type: periodic
      frequency: 1m
      actions:
        - {type: add_trade, priceables: book, trade_duration: 2m}
"""


def foreign_cfg(**edits):
    c = yamlio.loads(CFG)
    c["instruments"]["sw"]["conventions"] = {**R.USD_SOFR_OIS_CONVENTIONS, "calendar": "usd_fed"}  # the reference stack's shared block + the snapshot's calendar name
    c["instruments"]["sw"]["bind"] = copy.deepcopy(FOREIGN_BIND)
    for k, v in edits.items():
        yamlio.set_path(c, k.replace("__", "."), v)
    return c


def native_cfg():
    """The SAME strategy on the SAME synthetic data through the reference stack's own kit: no foreign code."""
    c = foreign_cfg()
    c["market"]["mdps"]["rates"] = {"type": "pricebt.testing.synthetic:SyntheticMarket", "kwargs": {"start": "2024-01-02", "end": "2024-06-28", "seed": 7}}
    c["market"]["pricers"]["primary"] = {"mdp": "rates", "wrap": "pricebt.testing.refstack:wrap"}
    c["instruments"]["sw"] = {"factory": "pricebt.testing.refstack:swap", "conventions": c["instruments"]["sw"]["conventions"], "layers": ["carry", "roll", "delta", "convexity"],
                              "bind": copy.deepcopy(NATIVE_BIND)}
    return c


@pytest.fixture(autouse=True)
def _clean():
    CALLS.clear()
    TRAPPED.clear()
    yield


@pytest.fixture(scope="module")
def runs():
    CALLS.clear()
    TRAPPED.clear()
    foreign = api.run(foreign_cfg())
    calls, trapped = list(CALLS), list(TRAPPED)  # captured here: the per-test cleanup must not empty them before a test looks
    return foreign, api.run(native_cfg()), calls, trapped


@core
def test_a_request_dict_source_and_a_pricer_owning_its_methods_run_a_backtest_through_config_alone(runs):
    foreign, native, calls, trapped = runs
    assert foreign.n_errors == 0 and foreign.reconcile().ok and len(foreign.trades) >= 4
    assert trapped == [] and len(calls) > 30, "no schema name was resolved by a same-named method (and the run did call the pricer's methods: the source served the whole window)"
    pd.testing.assert_frame_equal(foreign.record.equity, native.record.equity, rtol=1e-9, atol=1e-6)
    pd.testing.assert_frame_equal(foreign.vectors["delta_ladder"], native.vectors["delta_ladder"], rtol=1e-9, atol=1e-6)
    assert list(foreign.vectors["delta_ladder"].columns) == TENORS and foreign.record.equity["flows_cum"].abs().max() > 0, "the seasoned swap paid a coupon inside the window"
    layers = foreign.record.layers_by_position.set_index(["position", "layer"])["pnl"].unstack()
    assert {"carry", "roll", "delta", "convexity", "unexplained"} <= set(layers.columns) and layers["delta"].abs().sum() > 1e4


@core
def test_every_request_was_fresh_and_asked_for_its_own_day(runs):
    foreign, _, calls, _ = runs
    days = [d for _, d in calls]
    assert calls and {n for n, _ in calls} == {"USD-SOFR-1D"} and all(isinstance(d, dt.date) and not isinstance(d, dt.datetime) for d in days)
    assert min(days) == dt.date(2024, 2, 1) and max(days) <= dt.date(2024, 4, 30), "the provider asked for the days the engine marked, injecting the date itself"


@core
def test_the_foreign_path_consumes_the_same_snapshots_as_the_native_one():
    src = SyntheticMarket("2024-01-02", "2024-06-28", seed=7)
    prov = arbs_shaped_provider("2024-01-02", "2024-06-28")
    for d in ("2024-02-01", "2024-03-15", "2024-04-30"):
        ts = pd.Timestamp(f"{d} 17:00", tz=NY)
        a, b = src.get_pricer(ts).snapshot, prov.get_pricer(ts).snapshot
        assert a.curves == b.curves and a.fixings == b.fixings and a.calendars == b.calendars and a.ts == b.ts and a.reference_date == b.reference_date


@core
def test_the_same_foreign_pricer_without_bindings_is_rejected_and_receives_nothing():
    c = foreign_cfg()
    del c["instruments"]["sw"]["bind"]
    with pytest.raises(ConfigError) as e:
        build_cfg(c)
    assert e.value.code == "CFG-REQUIRED-BINDING" and "delta_ladder" in str(e.value)
    assert TRAPPED == [] and CALLS == []


@core
def test_a_binding_naming_a_method_the_pricer_lacks_names_it_at_the_first_call():
    """A `pricer_method` target cannot be checked at load (there is no pricer yet); the first call says what is missing and suggests the real name."""
    from pricebt.contracts.binding import Env
    from pricebt.contracts.evaluate import evaluate_measure
    from pricebt.contracts.schema import SchemaRegistry
    from pricebt.contracts.spec import TradeTemplate, build_spec
    from pricebt.errors import MeasureError

    raw = {"asset_class": "swap", "factory": stub_factory, "conventions": {**R.USD_SOFR_OIS_CONVENTIONS, "calendar": "usd_fed"}, "bind": copy.deepcopy(FOREIGN_BIND)}
    good = build_spec("sw", raw, schemas=SchemaRegistry.default())
    raw["bind"]["dv01"] = {"target": {"pricer_method": "pv_01"}, "kwargs": SW_KW}
    bad = build_spec("sw", raw, schemas=SchemaRegistry.default())  # loads: nothing can tell yet
    p = wrap_stub(arbs_shaped_provider("2024-01-02", "2024-06-28").get_pricer(pd.Timestamp("2024-03-15 17:00", tz=NY)))
    for spec, ok in ((good, True), (bad, False)):
        obj = TradeTemplate("t", spec, {"side": "pay", "maturity": "5Y", "notional": 1e7}).build(p, p.ts).obj
        env = Env(pricer=p, ctx=MarkContext.standalone(p), instrument=obj, terms={})
        if ok:
            assert evaluate_measure(spec, "dv01", env) > 0
        else:
            with pytest.raises(MeasureError, match="pv_01") as e:
                evaluate_measure(spec, "dv01", env)
            assert "pv01" in str(e.value) and "did you mean" in str(e.value)


@core
def test_a_source_that_serves_the_future_is_refused_by_the_provider_and_by_the_engines_guard():
    prov = RequestSnapshotProvider(RequestDictSource(SyntheticMarket("2024-01-02", "2024-06-28", seed=7), stamp_shift=pd.Timedelta(hours=1)))
    ts = pd.Timestamp("2024-03-15 17:00", tz=NY)
    with pytest.raises(LookAheadError, match="served_future"):
        prov.get_pricer(ts)
    with pytest.raises(LookAheadError):
        MarketData({"primary": Binding(prov, wrap=wrap_stub)}).pricer(ts)
    fine = MarketData({"primary": Binding(arbs_shaped_provider("2024-01-02", "2024-06-28"), wrap=wrap_stub)}).pricer(ts)
    assert isinstance(fine, ArbsLikePricer) and fine.ts == ts


@core
def test_the_provider_never_reuses_or_mutates_the_callers_request():
    prov = arbs_shaped_provider("2024-01-02", "2024-06-28")
    mine = {"offline": True}
    prov.get_pricer(pd.Timestamp("2024-03-14 17:00", tz=NY), mine)
    prov.get_pricer(pd.Timestamp("2024-03-15 17:00", tz=NY), mine)
    assert mine == {"offline": True} and prov.template == {"curve_name": "USD-SOFR-1D", "offline": True}
    assert [d for _, d in CALLS] == [dt.date(2024, 3, 14), dt.date(2024, 3, 15)]


# ============================================================================ opt-in: the parity oracle (ARBS's own classes vs the adapter)
def check_row(row: Mapping[str, Any], tol_par: float = 1e-9, tol_npv: float = 1e-2, tol_pv01: float = 1e-9) -> Dict[str, float]:
    """The same trade valued by the rateslib adapter on the market ARBS served, compared with ARBS's numbers. Raises AssertionError on a difference."""
    import datetime as _dt

    import pricebt.contrib.rateslib as RL
    from test_rl_common import SWAP_CONV, ctx, nyc_data, template

    ref = _dt.date.fromisoformat(row["reference_date"])
    nodes = sorted((_dt.date.fromisoformat(k), v) for k, v in row["nodes"].items())
    snap = MarketSnapshot(pd.Timestamp(row["stamp"]), ref, {"sofr": CurveSnapshot("sofr", ref, tuple(d for d, _ in nodes), tuple(v for _, v in nodes))}, {}, None, {"nyc": nyc_data()})
    p = RL.wrap(SnapshotPricer(snap))
    terms = {"side": "pay", "maturity": row["tenor"], "notional": row["notional"]}
    par = template(RL.swap, SWAP_CONV, {**terms, "fixed_rate": "par"}).build(p, p.ts).terms["fixed_rate"]
    obj = template(RL.swap, SWAP_CONV, {**terms, "fixed_rate": row["fixed_rate"]}).build(p, p.ts).obj
    got = {"par": par, "npv": obj.value(ctx=ctx(p)).pv, "pv01": obj.dv01(ctx=ctx(p))}
    assert abs(got["par"] - row["arbs_par"]) < tol_par, (row["recipe"], row["ts"], "par", got["par"], row["arbs_par"])
    assert abs(got["npv"] - row["arbs_npv"]) < tol_npv, (row["recipe"], row["ts"], "npv", got["npv"], row["arbs_npv"])
    assert got["pv01"] == pytest.approx(row["arbs_pv01"], rel=tol_pv01), (row["recipe"], row["ts"], "pv01", got["pv01"], row["arbs_pv01"])
    return got


class _RateslibBackedRaw:
    """An independent stand-in for ARBS's pricer built directly on the public rateslib API (`quantlib_support.rl_reference`): what `RLIRSwapCurve` does for a fresh swap
    (`usd_irs` on the served curve, `fair_rate` in decimals, `npv`, `analytic_delta`). It lets the parity CHECK run without ARBS, on real numbers."""

    def __init__(self, nodes, stamp):
        from quantlib_support import rl_reference as REF
        from test_rl_common import nyc_data

        self.REF, self._stamp = REF, stamp
        self._ref = next(iter(nodes))
        self._cal = REF.cal_of(nyc_data().holidays)
        self._curve = REF.curve_of(list(nodes), list(nodes.values()), self._cal)
        self._nodes = nodes

    handle, meta, reference_date = _FlatRaw.handle, _FlatRaw.meta, _FlatRaw.reference_date

    def nodes(self):
        return {dt.datetime.combine(d, dt.time()): v for d, v in self._nodes.items()}

    def build_irswap(self, fwd=None, tenor="10Y", fixed_rate=-0, notional=None):
        spot = self._cal.add_bus_days(dt.datetime.combine(self._ref, dt.time()), 2, True)
        sw = self.REF.rl.IRS(effective=spot, termination=tenor.lower(), spec="usd_irs", calendar=self._cal, fixed_rate=fixed_rate * 100.0, notional=notional or 1e6)
        return sw

    def fair_rate(self, sw):
        return float(sw.rate(curves=self._curve)) / 100.0

    def npv(self, sw):
        return float(sw.npv(curves=self._curve))

    def pv01(self, sw):
        return float(sw.analytic_delta(curves=self._curve, leg=1))


def _rl_backed_inner(day_nodes):
    def raw_of(day, when):
        return _RateslibBackedRaw(day_nodes(day), pd.Timestamp(dt.datetime.combine(day, dt.time(17)), tz=NY))

    return _StandInInner(raw_of)


@pytest.mark.adapter_rateslib
def test_the_parity_check_agrees_on_a_rateslib_backed_stand_in_and_detects_a_difference():
    """The oracle's comparison logic, run WITHOUT ARBS on real numbers: rows produced by `oracle_case` from a stand-in that prices with direct rateslib, checked against the
    adapter. Non-vacuity: a row nudged by 1e-6 percent, 1 currency unit or 1e-6 relative in the PV01 fails the check."""
    pytest.importorskip("rateslib")
    from test_rl_common import par_nodes

    day_nodes = lambda day: dict(par_nodes(day, 0.0, 0.0))  # noqa: E731
    rows = [live.oracle_case(_rl_backed_inner(day_nodes), "irs.usd_sofr.citi.eod", iso) for iso in ("2025-01-16T17:00:00-05:00", "2025-03-14T17:00:00-04:00")]
    for row in rows:
        got = check_row(row)
        assert 3.5 < got["par"] < 4.6 and abs(row["arbs_npv"]) > 1e3 and row["arbs_pv01"] > 0
        assert row["arbs_par"] == pytest.approx(got["par"], abs=1e-9)
    for key, delta in (("arbs_par", 1e-6), ("arbs_npv", 1.0), ("arbs_pv01", rows[0]["arbs_pv01"] * 1e-6)):
        with pytest.raises(AssertionError):
            check_row({**rows[0], key: rows[0][key] + delta})


LIVE_ROOT = os.environ.get("PRICEBT_ARBS_ROOT") or r"C:\Users\chris\clee\ARBS"
LIVE_OK, LIVE_WHY = live.arbs_status(LIVE_ROOT)


@pytest.mark.live_arbs
@pytest.mark.adapter_rateslib
@pytest.mark.fixtures
@pytest.mark.skipif(not LIVE_OK, reason=f"live ARBS: {LIVE_WHY}")
@needs_fixtures
def test_live_arbs_and_the_adapter_value_the_same_trade_identically(tmp_path):
    """G2(c): ARBS's own `IRSwapsMDP` classes (guarded child process, read-only) value a 10Y payer at 4% on 3 EOD dates and 3 minutes; the adapter values it on the curve ARBS
    served (par, NPV, PV01 within the tolerances of `check_row`), and its par also matches the fixtures' snapshot and the recorded provenance numbers, ARBS untouched."""
    pytest.importorskip("rateslib")
    pytest.importorskip("pyarrow")
    from support.curves import CurveStore

    import pricebt.contrib.rateslib as RL
    from test_rl_common import SWAP_CONV, template

    arbs = Path(LIVE_ROOT)

    def git_status() -> str:
        return subprocess.run(["git", "--no-optional-locks", "-C", str(arbs), "status", "--porcelain"], capture_output=True, text=True, timeout=300).stdout

    cases = [("irs.usd_sofr.citi.eod", "2026-08-05T17:00:00-04:00"), ("irs.usd_sofr.citi.eod", "2024-06-14T17:00:00-04:00"), ("irs.usd_sofr.citi.eod", "2019-03-15T17:00:00-04:00"),
             ("irs.usd_sofr.citi.min", "2026-08-05T10:30:00-04:00"), ("irs.usd_sofr.citi.min", "2026-03-09T09:30:00-04:00"), ("irs.usd_sofr.citi.min", "2025-11-03T10:00:00-05:00")]
    cf = tmp_path / "cases.json"
    cf.write_text(json.dumps(cases), encoding="utf8")
    before = git_status()
    env = {**os.environ, "PYTHONPATH": f"{SRC}{os.pathsep}{ROOT}", "PYTHONDONTWRITEBYTECODE": "1", "PRICEBT_ARBS_SCRATCH": str(tmp_path / "scratch"), "PRICEBT_ARBS_ROOT": LIVE_ROOT}
    r = subprocess.run([sys.executable, "-m", "tools.arbs_live", "oracle", str(cf)], capture_output=True, text=True, env=env, timeout=900, cwd=str(tmp_path))
    after = git_status()
    assert before == after, "ARBS working tree changed during the live run"
    assert r.returncode == 0, r.stderr[-3000:]
    res = json.loads([ln for ln in r.stdout.splitlines() if ln.startswith("JSON>>")][-1][len("JSON>>"):])
    assert res["attempts"] == [], res["attempts"]
    stores = {"irs.usd_sofr.citi.eod": CurveStore(EOD), "irs.usd_sofr.citi.min": CurveStore(MIN)}
    for row in res["rows"]:
        assert "error" not in row, row
        check_row(row)
        p = RL.wrap(stores[row["recipe"]].get_pricer(pd.Timestamp(row["ts"])))
        fixture_par = template(RL.swap, {**SWAP_CONV, "calendar": "usd_fed"}, {"side": "pay", "maturity": row["tenor"], "notional": 1e7, "fixed_rate": "par"}).build(p, p.ts).terms["fixed_rate"]
        assert abs(fixture_par - row["arbs_par"]) < 1e-9, row
    known = {(EOD if r_["recipe"].endswith("eod") else MIN, pd.Timestamp(r_["ts"]).tz_convert(NY).strftime("%Y-%m-%d %H:%M")): r_["arbs_par"] for r_ in res["rows"]}
    for k, v in known.items():
        if k in ARBS_PAR_10Y:
            assert abs(v - ARBS_PAR_10Y[k]) < 1e-9, k
