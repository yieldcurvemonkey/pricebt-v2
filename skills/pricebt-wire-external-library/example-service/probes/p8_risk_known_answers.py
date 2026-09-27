"""risk_known_answers.py: the sign and scale of an adapter's BOUND dv01 and gamma against full revaluation in a flat world (a parallel zero-rate shock).
Run from the repository root with PYTHONPATH="src;tests;skills/pricebt-wire-external-library/example-service". Exit 0 = every row ok."""
import datetime as dt
import sys

from pricebt.contracts.binding import Env
from pricebt.contracts.evaluate import evaluate_measure, evaluate_value, scalar_of
from pricebt.contracts.schema import SchemaRegistry
from pricebt.contracts.spec import TradeTemplate, build_spec
from pricebt.pricable import MarkContext, as_valuation
from pricebt.snapshot import SnapshotPricer
from pricebt.testing.layer_conformance import flat_swap_world

# ---- LIB: your adapter package: its `swap` Kit, its `wrap`, its conventions block, the dotted-path prefix its function bindings need ------------------------------------
import zeta_adapter as ADAPTER                                                                               # LIB
KIT, WRAP, ALLOW = ADAPTER.swap, ADAPTER.wrap, ("pricebt", "zeta_adapter")                                   # LIB
CONV = {**ADAPTER.USD_SOFR_OIS_CONVENTIONS, "calendar": "cal"}                                               # LIB
# --------------------------------------------------------------------------------------------------------------------------------------------------------------------------

world = flat_swap_world("cal")
spec = build_spec("r", {"factory": KIT, "conventions": CONV}, schemas=SchemaRegistry.default(), allow=ALLOW)
t0 = dt.date(2024, 3, 4)


def bound(shock_bp):
    """the BOUND world of the flat market shocked by `shock_bp` (the engine's path: spec, env, sign and scale of the bindings)"""
    p = WRAP(SnapshotPricer(world(t0, shock_bp)))
    return p, MarkContext(p.ts, p, {"primary": p}, None, None, p.ts, p, {}, {})


p0, c0 = bound(0.0)
built = TradeTemplate("r", spec, {"side": "pay", "effective": "spot", "maturity": "5Y", "notional": 1e7, "fixed_rate": "par"}).build(p0, p0.ts)


def env(c):
    return Env(pricer=c.pricer, ctx=c, instrument=built.obj, terms=built.terms, state={})


def pv(shock_bp):
    p, c = bound(shock_bp)
    return as_valuation(evaluate_value(spec, env(c)), p.ts).pv


dv01 = scalar_of(evaluate_measure(spec, "dv01", env(c0), cache=c0.cache))
gamma = scalar_of(evaluate_measure(spec, "gamma", env(c0), cache=c0.cache))
up, mid, dn = pv(+1.0), pv(0.0), pv(-1.0)
fd_dv01, fd_gamma = (up - dn) / 2.0, up + dn - 2.0 * mid  # currency per bp and per bp^2, holder-signed, for the ZERO-rate variable
bad = 0
for name, got, fd, lo, hi in (("dv01", dv01, fd_dv01, 0.9, 1.1), ("gamma", gamma, fd_gamma, 0.5, 2.0)):
    ratio = got / fd
    ok = lo <= ratio <= hi  # par-rate space (bound) against zero-rate space (the shock): a few percent apart, so the check pins SIGN and SCALE, not the variable
    bad += not ok
    print(f"{'ok ' if ok else 'BAD'} {name:<6} bound {got:>12.4f}   full revaluation {fd:>12.4f}   ratio {ratio:8.4f}   (expected within {lo} .. {hi}, sign {'+' if fd > 0 else '-'})")
sys.exit(1 if bad else 0)
