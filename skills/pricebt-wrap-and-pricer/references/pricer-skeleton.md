# Skeleton: `<lib>_adapter/_compat.py` and `<lib>_adapter/wrap.py`

Structure copied from `src/pricebt/contrib/quantlib/wrap.py` + `_compat.py` (lazy, frozen, private memo) and `skills/pricebt-wire-external-library/example/acme_adapter/wrap.py` +
`_compat.py` (eager build, weak module memo). EXECUTED: the two files below are the files that were run against `acmelib` (`import acmelib as lib` stands in for `import <LIB> as lib`),
under the tests of `references/tests-template.md` (all passed) and its mutation check (every mutant killed). EVERY line that uses `lib.` or a name of `acmelib` (ISO-string dates, `ZeroCurve`,
`Market`, its exception classes) is yours to rewrite from `<LIB>`'s documentation, and most of them carry a `# <LIB>` tag; the scaffolding around them is not library-specific. `acmelib`'s API
(ISO-string dates, zero-rate curve on ACT/365, `set_valuation_date`, `register_calendar`) is fictional.

## `_compat.py`: the ONLY module that imports `<LIB>`, touches its globals and catches its exceptions

```python
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import threading
from typing import Any, Iterator

import pandas as pd

from pricebt.errors import ConfigError, MarketDataUnavailable, MethodCallError, OptionalDependencyError
from pricebt.snapshot import CalendarData

try:
    import acmelib as lib  # <LIB>: `import <LIB> as lib`, the ONLY import of the library in the whole adapter package
except ImportError as e:  # the optional extra is absent: say so at import, not at the first call
    raise OptionalDependencyError("<lib>_adapter needs the optional extra `<LIB>` (pip install <LIB>)") from e

_LOCK = threading.RLock()  # OPTIONAL: pricebt's engine is single-threaded; take the lock only if your host runs backtests in threads


def to_date(d: Any) -> dt.date:
    """The one date normaliser: date | datetime | Timestamp | ISO string -> dt.date. Everything the adapter RETURNS to pricebt goes through it."""
    if isinstance(d, pd.Timestamp):
        return d.date()
    if isinstance(d, dt.datetime):
        return d.date()
    if isinstance(d, dt.date):
        return d
    if isinstance(d, str):
        return dt.date.fromisoformat(d.strip()[:10])
    raise TypeError(f"cannot convert {d!r} to a date")


def lib_date(d: Any) -> Any:
    """<LIB>: the library's date type (here an ISO string)."""
    return to_date(d).isoformat()


@contextlib.contextmanager
def guard(valuation_date: Any) -> Iterator[dt.date]:
    """<LIB>'s process-global valuation date = `valuation_date` inside the block, and exactly what it was before (even 'unset') afterwards, also when the block raises."""
    with _LOCK:
        prev = lib.valuation_date()  # <LIB>: read the global
        lib.set_valuation_date(lib_date(valuation_date))  # <LIB>: set the global
        try:
            yield to_date(valuation_date)
        finally:
            lib.set_valuation_date(prev)  # <LIB>: restore what was there


@contextlib.contextmanager
def translate(ts: Any = None) -> Iterator[None]:
    """<LIB>'s exceptions -> pricebt's. Order: most specific first; the last clause catches the library's base class, so no library exception type ever escapes."""
    try:
        yield
    except lib.MissingFixing as e:  # <LIB>: a data gap
        raise MarketDataUnavailable(ts, {}, str(e)) from e
    except lib.CalendarError as e:  # <LIB>: a configuration problem
        raise ConfigError(str(e), code="CFG-CALENDAR") from e
    except lib.BadInput as e:  # <LIB>: malformed input
        raise ConfigError(str(e), code="CFG-TERMS") from e
    except lib.AcmeError as e:  # <LIB>: the library's base exception
        raise MethodCallError(f"<LIB>: {type(e).__name__}: {e}") from e


def load_calendar(cd: CalendarData) -> str:
    """Register a snapshot calendar in <LIB>'s GLOBAL registry under a CONTENT-ADDRESSED name; the same content twice is a no-op, different content is a different name."""
    mask = {t.strip().lower()[:3] for t in cd.weekmask.split()}
    weekend = [d for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun") if d not in mask]
    holidays = sorted(lib_date(h) for h in cd.holidays)
    name = f"PBT.{cd.name}.{hashlib.sha256(json.dumps([holidays, weekend]).encode('utf8')).hexdigest()[:8]}"
    with translate():
        lib.register_calendar(name, holidays, weekend)  # <LIB>: register (never 'replace' an existing name)
    return name
```

## `wrap.py`: the ONE place a snapshot becomes `<LIB>` objects

```python
from __future__ import annotations

import datetime as dt
import math
import weakref
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from pricebt.errors import ConfigError, MarketDataUnavailable
from pricebt.pricer import PricerBase
from pricebt.snapshot import CurveSnapshot, SnapshotPricer

from . import _compat as C

lib = C.lib  # <LIB>: the library module, re-exported by _compat


def _choose(name: Optional[str], available: Mapping[str, Any], what: str) -> Optional[str]:
    if name is not None:
        if name not in available:
            raise ConfigError(f"the snapshot has no {what} {name!r}; it has {sorted(available)}", code="CFG-WRAP")
        return name
    if len(available) > 1:
        raise ConfigError(f"the snapshot has several {what}s {sorted(available)}: name one in wrap(..., {what}=...)", code="CFG-WRAP")
    return next(iter(available), None)


def check_curve(c: CurveSnapshot) -> None:
    """Honour the snapshot TAG or refuse AT WRAP TIME; never substitute silently. (The vocabulary today is `log_linear_df` / `discount_factor`: pricebt.snapshot.INTERPOLATIONS.)"""
    if c.interpolation != "log_linear_df":
        raise ConfigError(f"curve {c.name!r} has interpolation {c.interpolation!r}; this adapter honours only 'log_linear_df'", code="CFG-INTERPOLATION")
    if c.value_kind != "discount_factor":
        raise ConfigError(f"curve {c.name!r} has value_kind {c.value_kind!r}; this adapter needs discount factors", code="CFG-INTERPOLATION")


def build_curve(c: CurveSnapshot) -> Any:
    """<LIB>: discount factors at node dates -> the library's curve object, in the library's own storage convention."""
    a = c.reference_date
    zeros = [-math.log(v) / ((d - a).days / lib.ZeroCurve.BASIS_DAYS) for d, v in zip(c.node_dates[1:], c.values[1:])]  # <LIB>: zero rates on its own basis
    return lib.ZeroCurve(C.lib_date(a), [C.lib_date(d) for d in c.node_dates[1:]], zeros)  # <LIB>: its curve constructor


class LibPricer(PricerBase):
    """One snapshot at `ts`, ready for this adapter's instruments. Created by `wrap`; do not mutate."""

    LOOKUPS = ()  # optional schema capabilities ('security', 'quote' for bonds); none required by any core code path

    def __init__(self, source: SnapshotPricer, *, curve: Optional[str] = None, fixings: Optional[str] = None):
        snap = getattr(source, "snapshot", None)
        if snap is None:
            raise ConfigError(f"wrap needs a SnapshotPricer (a pricer holding a MarketSnapshot), got {type(source).__name__}", code="CFG-WRAP")
        object.__setattr__(self, "_frozen", False)
        self.snapshot, self.ts, self.reference_date = snap, source.ts, source.reference_date  # PricerBase protocol: ts, reference_date
        self.digest = getattr(source, "digest", snap.digest())  # carried, so the audit and the tie-out can name the input
        self.curve_name = _choose(curve, snap.curves, "curve")
        self.fixings_name = _choose(fixings, snap.fixings, "fixings")
        if self.curve_name is not None:
            check_curve(snap.curves[self.curve_name])  # fail now, not at the first price
        self._lazy: Dict[Any, Any] = {}
        object.__setattr__(self, "_frozen", True)

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_frozen", False):
            raise AttributeError(f"{type(self).__name__} is immutable")
        object.__setattr__(self, name, value)

    def __getstate__(self) -> Dict[str, Any]:
        st = dict(self.__dict__)
        st["_lazy"] = {}  # <LIB> objects are caches: never pickled, rebuilt on demand
        return st

    def __setstate__(self, st: Dict[str, Any]) -> None:
        self.__dict__.update(st)

    def memo(self, key: Any, build: Callable[[], Any]) -> Any:
        """Per-wrapped-pricer scratch: expensive derived objects (risk curves, schedules) live HERE, never on the snapshot and never on the instrument."""
        if key not in self._lazy:
            self._lazy[key] = build()
        return self._lazy[key]

    def market(self) -> Any:
        """The <LIB> market object: curve + published fixings in the library's units (here DECIMAL, keyed by ISO date)."""
        if self.curve_name is None:
            raise MarketDataUnavailable(self.ts, {}, "this snapshot carries no discount curve")

        def make() -> Any:
            fx: Dict[str, float] = {}
            if self.fixings_name is not None:
                f = self.snapshot.fixings[self.fixings_name]
                scale = 0.01 if f.unit == "percent" else 1.0  # the snapshot says its unit; <LIB> wants decimals
                fx = {C.lib_date(d): v * scale for d, v in zip(f.dates, f.values)}
            with C.translate(self.ts):
                return lib.Market(build_curve(self.snapshot.curves[self.curve_name]), fx)  # <LIB>: curve + fixings -> its market object

        return self.memo("market", make)

    def calendar(self, name: str) -> str:
        """The name <LIB> has the SNAPSHOT's calendar `name` under (registered from the snapshot's holidays, content-addressed)."""
        def make() -> str:
            cd = self.snapshot.calendars.get(name)
            if cd is None:
                raise ConfigError(f"the snapshot has no calendar {name!r}; it has {sorted(self.snapshot.calendars)}", code="CFG-CALENDAR")
            return C.load_calendar(cd)

        return self.memo(("calendar", name), make)

    def describe(self) -> Mapping[str, Any]:
        return {"type": type(self).__name__, "ts": str(self.ts), "reference_date": str(self.reference_date), "digest": self.digest, "curve": self.curve_name, "fixings": self.fixings_name}


_WRAPPED: "weakref.WeakKeyDictionary[SnapshotPricer, Dict[Tuple[Optional[str], Optional[str]], LibPricer]]" = weakref.WeakKeyDictionary()


def wrap(pricer: SnapshotPricer, *, curve: Optional[str] = None, fixings: Optional[str] = None) -> LibPricer:
    """Idempotent and memoised per SnapshotPricer OBJECT (MarketData memoises per role/digest/stamp; this second layer serves direct callers: layers, tests, the conformance kit)."""
    if isinstance(pricer, LibPricer):
        return pricer
    memo = _WRAPPED.setdefault(pricer, {})
    if (curve, fixings) not in memo:
        memo[(curve, fixings)] = LibPricer(pricer, curve=curve, fixings=fixings)
    return memo[(curve, fixings)]
```

`__init__.py`: `from .wrap import LibPricer, wrap` and export both (the shipped adapters also export `STACK`, the kits and the conventions, see `pricebt-instrument-kit`).

## What each block is for (the obligations)

| block | obligation | model in the repo |
|---|---|---|
| `try: import <LIB>` | one import site; a missing optional extra is `OptionalDependencyError` at import (executed without the library on the path: `OptionalDependencyError: lib_adapter needs the optional extra <LIB> (pip install <LIB>)`) | `contrib/quantlib/_compat.py`, `contrib/rateslib/_compat.py` |
| `to_date` / `lib_date` | dates go in and out as `dt.date`; the library's own spelling never crosses the boundary | `to_date`, `qd`, `pd_` in `contrib/quantlib/_compat.py`; `iso` in the acme `_compat.py` |
| `guard` | every process-global the library reads is set for one call and restored, also on error | `guard` in the quantlib `_compat.py` (evaluation date, `includeReferenceDateEvents`, index histories); acme `guard` |
| `translate` | no library exception type reaches the engine or the tie-out | acme `translate` |
| `load_calendar` | the snapshot's holidays become the library's calendar; content-addressed name | quantlib `calendar_from_holidays` (`BespokeCalendar`, no global registry); acme `load_calendar` |
| `check_curve` (at wrap time) | refuse a tag or kind the adapter cannot honour, never substitute | `QLPricer.__init__`, `rl_interpolation` in `contrib/rateslib/wrap.py` |
| `_choose` | several curves or fixings series in one snapshot need a name; an unknown name lists what exists | all four adapters |
| `LibPricer.__setattr__`, `__getstate__` | immutable; pickles without its library objects | `QLPricer` |
| `memo` | expensive derived objects are cached on the WRAPPED pricer | `QLPricer.memo`, `RefPricer.memo` |
| `wrap` + `_WRAPPED` | idempotent; one wrapped pricer per `SnapshotPricer` for direct callers | `_WRAPPED` in acme's `wrap.py` and in `src/pricebt/testing/refstack.py`, `_MEMO` in `src/pricebt/contrib/rateslib/wrap.py` (QuantLib has no second layer) |

## Two traps of the skeleton's shape (both executed)

* `from <lib>_adapter import wrap` is the FUNCTION, because the package `__init__` re-exports it over the submodule of the same name (the same for `acme_adapter.swap`, the Kit, and for
  `pricebt.contrib.quantlib.wrap`). A test that needs the module (for `_WRAPPED`) uses `sys.modules["<lib>_adapter.wrap"]` or `importlib.import_module("<lib>_adapter.wrap")`;
  `import <lib>_adapter.wrap as m` still gives the function.
* The weak memo frees an entry when its `SnapshotPricer` dies (executed: 5 entries, 0 after the pricers are dropped) ONLY if the wrapped value does not hold the `SnapshotPricer` itself:
  a `LibPricer` that keeps `source` pins its own key for ever (executed: 5 entries never freed). Keep `source.snapshot`, never `source`.
