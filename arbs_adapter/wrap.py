"""`wrap(pricer: SnapshotPricer) -> ArbsCurvePricer`: the ARBS view of a neutral market snapshot.

Builds a bare `rl.Curve` from the snapshot's ONE curve (`arbs_adapter._compat.float_curve`: the only interpolation the snapshot vocabulary carries is
`log_linear_df`) and wraps it in ARBS's OWN `RLIRSwapCurve` -- so `value` / `rate` / `dv01` (unstarted) go through ARBS's actual value functions
(`build_irswap`, `.npv`, `.pv01`, `.fair_rate`), not a reimplementation of them. `ArbsCurvePricer` subclasses `pricebt.contrib.rateslib.pricer.RLCurvePricer`
(generic rateslib plumbing, reused rather than reinvented) so `.calendar(name)`, `.memo(key, fn)`, `.curve`, `.fixings`, `.reference_date` come for free;
it adds exactly one extra frozen attribute, `curve_handle`, ARBS's wrapper object.

Load-bearing caveat (see the report for the measured numbers). ARBS's real `ERIS_EOD_LIVE-RL_BASIC` curve for `USD-SOFR-1D` is NOT pure log-linear: it
is a MIXED curve -- plain log-linear out to roughly the last densely-spaced node (~2.7Y on the probed date) and a natural log-cubic SPLINE beyond that
(verified: `RLIRSwapCurve.handle().interpolator.local_name` reads `"log_linear"`, yet `Caching.curve_store.CurveSnapshot._extract_spline` on the SAME
handle returns real spline knots spanning the long end -- a genuine mixed interpolation the `.local_name` alone does not reveal). pricebt's
`CurveSnapshot` vocabulary is closed to `log_linear_df` (`pricebt.snapshot.INTERPOLATIONS`, untouchable outside `src/pricebt`), so ANY adapter fed
through this snapshot type rebuilds the long end as plain log-linear. Measured on a real 10Y payer, 2024-05-24 (ARBS's own numbers vs. this module's
rebuild, same nodes): par rate off by ~0.02bp (5.0e-5 relative), NPV off by ~$162 on $10mm notional, PV01 off by ~0.4 out of ~8100 (5.2e-5 relative). A
2Y swap (entirely inside the plain log-linear region) agrees to a measured relative difference of exactly 0.0. This is a genuine difference of
DEFINITION between ARBS's native long-end curve and what the closed snapshot vocabulary can carry, not a bug in this module -- see the report for the
full GATE 2 numbers and the reasoning.
"""
from __future__ import annotations

import weakref
from typing import Any, Dict

import numpy as np
import pandas as pd

from pricebt.contrib.rateslib.pricer import RLCurvePricer
from pricebt.errors import ConfigError
from pricebt.snapshot import FixingsSeries, SnapshotPricer

from . import _compat as C

#: the ARBS curve DEFINITION name (a key of `RATESLIB_CURVE_DEFINITIONS`), distinct from the snapshot's own curve dict key (cosmetic, e.g. "sofr"):
#: `RLIRSwapCurve._curve_definition_id` resolves `meta_data["reference_curve_name"]`, and this adapter only ever prices this one curve.
ARBS_CURVE_NAME = "USD-SOFR-1D"


def _series_percent_midnight(f: FixingsSeries, name: str) -> pd.Series:
    """`FixingsSeries` -> a pandas Series, PERCENT, naive midnight ns index: `RLCurvePricer`'s own `fixings` contract (`pricebt.contrib.rateslib.wrap`'s
    `_series_of`), replicated here in three lines rather than importing a private function across module boundaries."""
    scale = 100.0 if f.unit == "decimal" else 1.0
    idx = pd.DatetimeIndex(np.array(f.dates, dtype="datetime64[ns]"))
    return pd.Series(np.array(f.values, dtype=float) * scale, index=idx, name=name)


class ArbsCurvePricer(RLCurvePricer):
    """An `RLCurvePricer` whose one extra attribute, `curve_handle`, is ARBS's own `RLIRSwapCurve` wrapping `self.curve` -- the SAME bare curve object,
    so `curve_handle.pv01(...)` and a bare `rl.IRS(...).analytic_delta(curves=self.curve)` price on identical data (verified in GATE 2)."""

    def __init__(self, ts: Any, curve: Any, *, curve_handle: Any, **kw: Any) -> None:
        super().__init__(ts, curve, **kw)
        object.__setattr__(self, "curve_handle", curve_handle)


_WRAPPED: "weakref.WeakKeyDictionary[SnapshotPricer, ArbsCurvePricer]" = weakref.WeakKeyDictionary()


def wrap(pricer: SnapshotPricer) -> ArbsCurvePricer:
    """The ARBS pricer of a snapshot pricer, memoised per snapshot pricer (one snapshot, one shared immutable pricer, no global state)."""
    if isinstance(pricer, ArbsCurvePricer):
        return pricer
    hit = _WRAPPED.get(pricer)
    if hit is not None:
        return hit
    snap = pricer.snapshot
    if len(snap.curves) != 1:
        raise ConfigError(f"ArbsCurvePricer needs exactly one curve in the snapshot, got {sorted(snap.curves)}", code="CFG-WRAP")
    snap_curve_id, cs = next(iter(snap.curves.items()))  # the snapshot's OWN dict key (e.g. "sofr"); cosmetic, distinct from ARBS_CURVE_NAME
    if cs.interpolation != "log_linear_df":
        raise ConfigError(f"curve {snap_curve_id!r} has interpolation {cs.interpolation!r}; this adapter honours only 'log_linear_df'", code="CFG-INTERPOLATION")
    if len(snap.fixings) != 1:
        raise ConfigError(f"ArbsCurvePricer needs exactly one fixings series, got {sorted(snap.fixings)}", code="CFG-WRAP")
    fx = next(iter(snap.fixings.values()))
    bare = C.float_curve(dict(zip(cs.node_dates, cs.values)), snap_curve_id)
    fixings_series = _series_percent_midnight(fx, ARBS_CURVE_NAME)
    RLIRSwapCurve = C.rl_irswap_curve()
    handle = RLIRSwapCurve(
        rl_curve_id=snap_curve_id, rl_curve_handle=bare, fixings=fixings_series,
        meta_data={
            "reference_curve_name": ARBS_CURVE_NAME, "curve_name": ARBS_CURVE_NAME, "requested_curve_name": ARBS_CURVE_NAME, "timestamp": pricer.ts,
            "id": f"{snap_curve_id}-wrapped",
        },
    )
    out = ArbsCurvePricer(
        pricer.ts, bare, curve_handle=handle, fixings=fixings_series, calendars=dict(snap.calendars), source="arbs:ERIS_EOD_LIVE-RL_BASIC",
        tz=str(pd.Timestamp(pricer.ts).tz),
    )
    _WRAPPED[pricer] = out
    return out
