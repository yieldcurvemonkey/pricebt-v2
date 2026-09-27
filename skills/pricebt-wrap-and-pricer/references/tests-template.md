# Tests that pin `wrap` and `_compat` (template, executed, mutation-checked)

Put it in `tests/test_<lib>_wrap.py`, mark it with your library's partition marker (`adapter_<lib>`, see `pricebt-guards-and-packaging`), and start it with
`pytest.importorskip("<LIB>")` in a real adapter (an unmarked test is a collection error). Here `lib_adapter` (the skeleton of `references/pricer-skeleton.md`) and `acmelib` stand in;
EVERY line that uses `lib.` (the library's spellings: ISO-string dates, its exception classes, its business-day test) is yours to rewrite, and each carries a `# <LIB>` tag. A tests directory outside `pytest.ini`'s `pythonpath = . src tests` must put its own package on `sys.path` from `Path(__file__)`
(never from the working directory).

```python
import copy
import datetime as dt
import pickle

import pandas as pd
import pytest

import acmelib as lib  # <LIB>: import <LIB> as lib
import lib_adapter as L  # <LIB>: import <lib>_adapter as L
from lib_adapter import _compat as C  # <LIB>: from <lib>_adapter import _compat as C
from pricebt.errors import ConfigError, MarketDataUnavailable, MethodCallError
from pricebt.market import Binding, MarketData
from pricebt.pricer import FunctionMDP
from pricebt.snapshot import CurveSnapshot, FixingsSeries, MarketSnapshot, SnapshotPricer
from pricebt.testing.layer_conformance import flat_swap_world
from pricebt.timeutil import Clock

HOL = (dt.date(2024, 3, 29), dt.date(2024, 5, 27), dt.date(2024, 7, 4))
REF = dt.date(2024, 6, 12)
WORLD = flat_swap_world("cal", holidays=HOL)  # a snapshot: curve "curve", fixings "fixings" (percent), calendar "cal"


@pytest.fixture(autouse=True)
def clean_library_global():
    lib.set_valuation_date(None)  # <LIB>: every test starts and ends with the process-global state as a fresh process has it
    yield
    lib.set_valuation_date(None)  # <LIB>


def snap(ref=REF, holidays=HOL):
    return SnapshotPricer(flat_swap_world("cal", holidays=holidays)(ref, 0.0))


def test_the_wrapped_pricer_carries_the_protocol_and_the_digest():
    sp = snap()
    p = L.wrap(sp)
    assert (p.ts, p.reference_date, p.digest) == (sp.ts, REF, sp.digest) and p.describe()["digest"] == sp.digest


def test_wrap_is_memoised_per_snapshot_pricer_and_idempotent():
    sp = snap()
    p = L.wrap(sp)
    assert L.wrap(sp) is p and L.wrap(p) is p and L.wrap(snap()) is not p


def test_marketdata_wraps_each_snapshot_once_whatever_the_request():
    n, clock = [], Clock()
    md = MarketData({"primary": Binding(FunctionMDP(lambda ts, req: snap(ts.date())), {}, lambda x: n.append(1) or L.wrap(x))}, clock)
    clock.advance(pd.Timestamp("2024-06-20 17:00", tz="America/New_York"))
    t = pd.Timestamp("2024-06-12 17:00", tz="America/New_York")
    assert md.pricer(t) is md.pricer(t, request={"k": 1}) and len(n) == 1


def test_the_wrapped_pricer_is_immutable():
    with pytest.raises(AttributeError, match="immutable"):
        L.wrap(snap()).ts = None


def test_pickle_and_deepcopy_keep_the_digest_and_rebuild_the_library_objects():
    p = L.wrap(snap())
    p.market(), p.calendar("cal")
    for clone in (pickle.loads(pickle.dumps(p)), copy.deepcopy(p)):
        assert clone.digest == p.digest and clone._lazy == {} and clone.calendar("cal") == p.calendar("cal") and clone.market() is not p.market()


@pytest.mark.parametrize("unit", ["percent", "decimal"])
def test_fixings_reach_the_library_in_its_unit_whatever_the_snapshot_unit(unit):
    s = snap().snapshot
    f = s.fixings["fixings"]
    vals = tuple(v * (1.0 if unit == "percent" else 0.01) for v in f.values)  # the same rates, spelled in `unit`
    p = L.wrap(SnapshotPricer(MarketSnapshot(s.ts, REF, s.curves, {"fixings": FixingsSeries("fixings", f.dates, vals, unit)}, None, s.calendars)))
    got = p.market().fixings
    assert got[C.lib_date(f.dates[-1])] == pytest.approx(f.values[-1] / 100.0, rel=1e-12)  # <LIB>: decimals


def test_a_tag_the_library_cannot_honour_is_refused_at_wrap_time_never_substituted():
    sp = snap()
    c = sp.snapshot.curves["curve"]
    object.__setattr__(c, "interpolation", "cubic_spline")  # the snapshot itself refuses to be BUILT with another tag: force it
    try:
        with pytest.raises(ConfigError, match="honours only 'log_linear_df'"):
            L.LibPricer(sp)
    finally:
        object.__setattr__(c, "interpolation", "log_linear_df")


def test_several_curves_need_a_name_and_an_unknown_name_lists_what_there_is():
    s = snap().snapshot
    c = s.curves["curve"]
    two = SnapshotPricer(MarketSnapshot(s.ts, REF, {"curve": c, "other": CurveSnapshot("other", REF, c.node_dates, c.values)}, s.fixings, None, s.calendars))
    with pytest.raises(ConfigError, match="several curves"):
        L.LibPricer(two)
    assert L.LibPricer(two, curve="other").curve_name == "other"
    with pytest.raises(ConfigError, match=r"no curve 'nope'; it has \['curve', 'other'\]"):
        L.LibPricer(two, curve="nope")


def test_the_snapshots_holidays_are_the_calendar_the_library_sees_and_two_calendars_coexist():
    with_hol, without = L.wrap(snap(holidays=HOL)), L.wrap(snap(holidays=()))
    a, b = with_hol.calendar("cal"), without.calendar("cal")
    assert a != b and a.startswith("PBT.cal.")  # content-addressed: the same snapshot name, different holidays
    assert not lib.is_business_day("2024-05-27", a) and lib.is_business_day("2024-05-27", b)  # <LIB>: the library's own business-day test
    assert not lib.is_business_day("2024-05-27", with_hol.calendar("cal"))  # <LIB>: registering the second did not replace the first
    with pytest.raises(ConfigError, match=r"the snapshot has no calendar 'nyse'; it has \['cal'\]"):
        with_hol.calendar("nyse")


def test_the_global_state_is_set_inside_the_guard_and_restored_also_on_error():
    lib.set_valuation_date("2031-03-03")  # <LIB>: somebody else's date must survive
    with C.guard(REF) as d:
        assert d == REF and lib.valuation_date() == "2024-06-12"  # <LIB>: the library's global, read back in ITS spelling
    assert lib.valuation_date() == "2031-03-03"  # <LIB>
    with pytest.raises(RuntimeError):
        with C.guard(REF):
            raise RuntimeError("boom")
    assert lib.valuation_date() == "2031-03-03"  # <LIB>


@pytest.mark.parametrize("raised,expected", [(lib.MissingFixing, MarketDataUnavailable), (lib.CalendarError, ConfigError), (lib.BadInput, ConfigError), (lib.SwapExpired, MethodCallError)])  # <LIB>: its exception classes
def test_the_librarys_exceptions_become_pricebts_and_keep_their_cause(raised, expected):
    with pytest.raises(expected) as ei:
        with C.translate(pd.Timestamp("2024-06-12 17:00", tz="America/New_York")):
            raise raised("boom")
    assert not isinstance(ei.value, lib.AcmeError) and isinstance(ei.value.__cause__, lib.AcmeError)  # <LIB>: its BASE exception class
```

Run (PowerShell, project root; the skeleton's directory on the path): expected: every test passes.

```powershell
$env:PYTHONPATH = "src;tests;<dir containing lib_adapter>"
python -m pytest <path>/test_lib_wrap_template.py -q -o addopts= -p no:cacheprovider
```

## Mutation check (do it: a test that has never failed proves nothing)

Each mutant is ONE textual change to a copy of the skeleton; the template must fail on it (the unmutated copy passed first). All were killed (every exit code 1, a real assertion failure,
not a collection error: check that, a mutant that only breaks the syntax is killed by nothing).

| mutant | which test kills it |
|---|---|
| `guard` no longer restores the previous value | the guard test |
| calendar name not content-addressed (`PBT.<name>`) | the two-calendars test |
| fixings unit ignored (scale 1.0) | the unit test, `percent` parametrisation only (the `decimal` case still passes: 0.0531 is already decimal) |
| the tag is not checked at wrap time | the refused-tag test |
| `__getstate__` keeps the lazy objects | the pickle test |
| `wrap` not idempotent | the memo test |
| `MissingFixing` translated to `MethodCallError` | the translate test |
| the pricer is not frozen | the immutability test |

## Service variant: a library that is a SERVICE (it prices only from a market you upload)

The template above pins an in-process library: a process-global, a calendar registry, library objects. A service has none of that; what can go wrong is COST and STATE at the boundary: a market uploaded twice, uploaded for
nothing, uploaded again by a clone, an error dict that is not turned into an error, and derived markets (the layers) that multiply uploads. So the tests that matter are the counters of the SERVICE's own client
(`client.stats`), from a FRESH client per test. Copy the block to `tests/test_<lib>_wrap_service.py` (the block carries `pytestmark = pytest.mark.core`, right for the fictional zeta; a real adapter changes it to `adapter_<lib>` once the guards of phase 2b exist, and adds `pytest.importorskip("<LIB>")` for a real client), and rewrite every line tagged `<LIB>`
(the first seven tests need only a snapshot; the last two need a base config with a few trades): the client, the counters your adapter keeps, the request your service answers, its error codes. Executed against `skills/pricebt-wire-external-library/example-service/` in a repository-shaped
layout (9 passed, about 10 s); the repository's own version is `tests/test_skills_example_zeta_wrap.py`.

```python
import copy
import datetime as dt
import pickle
import socket
import sys
from pathlib import Path

import pandas as pd
import pytest

EX = Path(__file__).resolve().parents[1] / "skills" / "pricebt-wire-external-library" / "example-service"  # <LIB>: the directory that CONTAINS your adapter package and your client package, found from THIS file, never from the working directory
for p in (str(EX), str(EX / "zeta_lib")):
    if p not in sys.path:
        sys.path.insert(0, p)

import zeta  # <LIB>: import <LIB>, the service client package (a wrong path should fail loudly here)
import zeta_adapter as A  # <LIB>: import <lib>_adapter as A
from zeta_adapter import _compat as C  # <LIB>: from <lib>_adapter import _compat as C (the holder of the client and of the upload counters)
from pricebt import api
from pricebt.errors import ConfigError, MarketDataUnavailable, MethodCallError
from pricebt.market import Binding, MarketData
from pricebt.pricer import FunctionMDP
from pricebt.snapshot import FixingsSeries, MarketSnapshot, SnapshotPricer
from pricebt.testing.layer_conformance import flat_swap_world
from pricebt.timeutil import Clock

pytestmark = pytest.mark.core  # <LIB>: zeta is fictional and needs no library, so its tests are `core`. A real adapter marks them `adapter_<lib>` once the guards of phase 2b exist (until then that marker is unknown and the run refuses the file; without ANY marker pytest exits 4)

HOL = (dt.date(2024, 3, 29), dt.date(2024, 5, 27), dt.date(2024, 7, 4))
REF = dt.date(2024, 6, 12)
CONFIG = EX / "zeta_adapter/config"  # <LIB>
BASE, OVERLAY = CONFIG / "zeta_tieout_base.yaml", CONFIG / "zeta_swap.yaml"  # <LIB>: a base with a few trades over about a month, and your stack overlay


@pytest.fixture(autouse=True)
def own_client(monkeypatch):
    """A FRESH client per test (so `client.stats` is this test's and no market id of another test exists) and no network: an offline test that touches it fails."""
    def refuse(*a, **k):
        raise RuntimeError("an offline test touched the network")

    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    cli = zeta.Client()  # <LIB>: your client, offline or recorded
    C.set_client(cli)  # <LIB>: install it where the adapter looks for it
    C.UPLOADS.update(snapshot=0, derived=0)  # <LIB>: the adapter's own counters (snapshot markets and derived markets apart)
    yield cli
    C.set_client(None)


def snap(ref=REF):
    return SnapshotPricer(flat_swap_world("cal", holidays=HOL)(ref, 0.0))


def some_trade(**kw):  # <LIB>: any request your service answers for a swap at par
    return {"product": "OIS", "leg_fixed": "PAY", "notional_mm": 1.0, "start": {"spot_lag": 2}, "end": {"tenor_years": 1}, "fixed_rate_bp": "PAR", **kw}


# ---- uploads: lazily, once per snapshot, never for a snapshot nobody prices
def test_wrap_uploads_nothing_so_a_snapshot_that_is_never_priced_costs_nothing(own_client):
    A.wrap(snap())
    assert own_client.stats["markets_uploaded"] == 0


def test_a_snapshot_is_uploaded_once_whatever_is_asked_of_it(own_client):
    p = A.wrap(snap())
    assert len({p.market_id() for _ in range(5)}) == 1 and own_client.stats["markets_uploaded"] == 1 and C.UPLOADS == {"snapshot": 1, "derived": 0}


def test_marketdata_wraps_each_snapshot_once_and_the_service_holds_one_market_for_it(own_client):
    clock = Clock()
    md = MarketData({"primary": Binding(FunctionMDP(lambda ts, req: snap(ts.date())), {}, A.wrap)}, clock)
    clock.advance(pd.Timestamp("2024-06-20 17:00", tz="America/New_York"))
    t = pd.Timestamp("2024-06-12 17:00", tz="America/New_York")
    p = md.pricer(t)
    assert p is md.pricer(t, request={"k": 1}) and md.n_wraps == 1  # two requests, one snapshot, one wrap
    for _ in range(3):  # three requests to the service, all on the same market
        p.price(p.market_id(), p.reference_date, [some_trade()], ["NPV"])
    assert own_client.stats["markets_uploaded"] == 1 and own_client.stats["requests"] == 3


def test_a_clone_uploads_its_own_market_because_a_market_id_belongs_to_the_client_that_made_it(own_client):
    p = A.wrap(snap())
    mid = p.market_id()
    assert mid.encode() not in pickle.dumps(p), "the memo (market ids, answers) is a cache of THIS client: it is not pickled"
    for clone in (pickle.loads(pickle.dumps(p)), copy.deepcopy(p)):
        assert clone.digest == p.digest and clone._memo == {}
        clone.market_id()  # a clone that asks for a market uploads its own
    assert C.UPLOADS["snapshot"] == 3  # the original and one per clone that asked


# ---- the service answers with an ERROR DICT, never raises: `_compat` turns it into pricebt's errors
def test_a_missing_fixing_is_market_data_unavailable_and_names_what_is_missing():
    s = snap().snapshot
    f = s.fixings["fixings"]
    holed = FixingsSeries("fixings", f.dates[:-5], f.values[:-5], "percent")  # the last five fixings are missing
    p = A.wrap(SnapshotPricer(MarketSnapshot(s.ts, REF, s.curves, {"fixings": holed}, None, s.calendars)))
    with pytest.raises(MarketDataUnavailable, match=r"Z530.*no SOFR fixing dated"):  # <LIB>: its code and its message
        p.price(p.market_id(), REF, [some_trade(start=(REF - dt.timedelta(days=30)).isoformat(), fixed_rate_bp=400.0)], ["NPV"])


def test_a_bad_request_is_a_config_error_with_the_services_code():
    p = A.wrap(snap())
    with pytest.raises(ConfigError, match=r"CFG-ZETA.*Z101"):  # <LIB>: a request field the service refuses
        p.price(p.market_id(), REF, [some_trade(leg_fixed="pay")], ["NPV"])


def test_an_unknown_market_and_a_closed_client_are_method_call_errors(own_client):
    p = A.wrap(snap())
    mid = p.market_id()
    with pytest.raises(MethodCallError, match="Z412"):
        p.price("mkt-9999", REF, [some_trade()], ["NPV"])
    own_client.close()
    with pytest.raises(MethodCallError, match="Z412"):
        p.price(mid, REF, [some_trade()], ["NPV"])


# ---- the accounting of a real backtest (step 3 of pricebt-enterprise-platform-patterns): the counter of the SERVICE, not of your code
def test_a_backtest_uploads_one_market_per_snapshot_with_the_layers_off(own_client):
    cfg = api.load(BASE)
    cfg["backtest"]["attribution"] = {"layers": []}
    b = api.build(cfg, stack=[api.load(OVERLAY)])
    b.run()
    assert b.market.n_wraps == 19 and own_client.stats["markets_uploaded"] == 19 == C.UPLOADS["snapshot"] and C.UPLOADS["derived"] == 0


def test_with_the_layers_on_each_interval_adds_five_derived_worlds_shared_by_every_position(own_client):
    b = api.build(BASE, stack=[OVERLAY])
    b.run()
    n = b.market.n_wraps
    assert C.UPLOADS["snapshot"] == n == 19
    # per interval: the rolled world (= the resampled base) and four shocked worlds (h and h/2, up and down). The base runs at cadence `eod`: every step is within the payment lag (2 business days), so there is no cash world
    assert C.UPLOADS["derived"] == 5 * (n - 1), C.UPLOADS  # at `every_n:5` it would be 5 x intervals + one cash world per interval (43 = 19 + 5 x 4 + 4 uploads in all)
    assert own_client.stats["markets_uploaded"] == C.UPLOADS["snapshot"] + C.UPLOADS["derived"]
```

What each group pins, and the rule behind it:

* **An autouse fixture installs a fresh client per test** and refuses the network: without it the counters of one test leak into the next (a market id belongs to the client that uploaded it) and an "offline" test can
  quietly call the platform. The adapter needs a way to swap its client (`set_client`) and, better, two counters of its own (`UPLOADS`: snapshot markets and derived markets), so a test can tell a snapshot upload from a derived one.
* **Lazily and once**: `wrap` uploads nothing (a snapshot that is never priced costs nothing); the first price uploads; every later request reuses the id; two requests for one snapshot are one wrap and one upload.
* **A clone re-uploads**: a pickled or deep-copied pricer drops its memo, because an id is only meaningful to the client that made it; the clone uploads its own on first use.
* **An error dict becomes a pricebt error**: a service that answers `{"status": "ERROR", "code": ...}` never raises, so nothing stops a run on a missing fixing unless the adapter checks every response: unavailable data ->
  `MarketDataUnavailable`, a bad request -> `ConfigError` with the service's code, an unknown market or a closed client -> `MethodCallError` (`pricebt-enterprise-platform-patterns`, step 7).
* **Derived-world accounting**: the last two tests run a real backtest and compare the service's counter with `snapshots` (layers off) and `snapshots + 5 x intervals` (layers on, at cadence `eod`, where no step exceeds the payment lag; a longer step adds one cash world per interval): the rule and its terms are step 3 of
  `pricebt-enterprise-platform-patterns` and section 5 of its `cost-budget.md`. A count that drifts up with the number of positions is a derived world that is not content-addressed.

Mutation check (executed on a scratch copy of the example service with `tools/mutcheck.py`, control green, all seven killed: `mutcheck.py` keeps your `PYTHONPATH` after `src` and takes `runs`, a list of pytest argument lists, one
call each; see `pricebt-debug-tieout-differences`, `regression-test-template.md`):

| mutant (one textual change) | killed by |
|---|---|
| the market id is not memoised (every call uploads) | `test_a_snapshot_is_uploaded_once_whatever_is_asked_of_it` |
| `wrap` uploads eagerly | `test_wrap_uploads_nothing_so_a_snapshot_that_is_never_priced_costs_nothing` |
| the memo is pickled with the pricer | `test_a_clone_uploads_its_own_market_because_...` |
| derived worlds are not content-addressed | `test_with_the_layers_on_each_interval_adds_five_derived_worlds_...` |
| the missing-fixing code is not mapped | `test_a_missing_fixing_is_market_data_unavailable_...` |
| the request-error code is not a `ConfigError` | `test_a_bad_request_is_a_config_error_with_the_services_code` |
| the upload counter never counts | `test_a_snapshot_is_uploaded_once_whatever_is_asked_of_it` |

## What the repository's own tests pin (read them as models)

| test file | what it pins |
|---|---|
| `tests/test_market_wrap.py` | wrap once per digest whatever the request; a different snapshot is wrapped separately; an unwrapped role returns the provider's pricer untouched; look-ahead still applies before the wrap; the wrap memo is bounded; two roles have their own wrappers |
| `tests/test_quantlib_wrap.py` | DF round trip to 1e-14; interpolation between and beyond nodes; only the `log_linear_df` tag; several curves need a name; wrap needs a `SnapshotPricer` and is idempotent; calendar from holidays equals plain python over ten years; a custom holiday does not leak into any named library calendar; weekmask; fixings in the library's unit; immutable and picklable; derived products memoised on the wrapped pricer, not on the snapshot |
| `tests/test_quantlib_globals.py` | the global evaluation date is set inside the guard and restored after every operation and after an exception; no fixings history survives a call; time moving backwards gives a fresh process's numbers; other users of the library keep their state; the adapter leaves no observer behind |
| `tests/test_snapshot_wrap_rateslib.py` | the curve is built from the snapshot nodes; a tag without a native equivalent is an error, never a substitute; fixings arrive in the library's form; a fixings history is never seen beyond the reference date; memoised per snapshot pricer and picklable; the synthetic market prices through the wrap |
| `tests/test_skills_example_acme.py` (section E) | the worked example's wrap: memo, DF to zero rates round trip, one tag, several curves, content-addressed calendars, order-independent numbers with two calendars alive, the global restored on every path and on error, every library exception translated, plain-data objects deep-copy and pickle and the pricer re-registers its calendars in a fresh process |
