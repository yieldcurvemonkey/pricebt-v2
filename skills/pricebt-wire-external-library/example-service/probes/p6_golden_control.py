"""p6_golden_control.py (zeta):  the seasoned 3y payer of docs/DESIGN.md section 7, through the BOUND layers of an adapter (the way the engine calls them).
Run from the repository root with PYTHONPATH="src;tests;skills/pricebt-wire-external-library/example-service". Exit 0 = all nine numbers within 0.011."""
import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd

from pricebt.contracts.binding import Env
from pricebt.contracts.evaluate import evaluate_layer, evaluate_value
from pricebt.contracts.schema import SchemaRegistry
from pricebt.contracts.spec import TradeTemplate, build_spec
from pricebt.pricable import MarkContext, as_valuation
from pricebt.snapshot import CalendarData, CurveSnapshot, FixingsSeries, MarketSnapshot, SnapshotPricer

# ---- LIB: your adapter package: its `swap` Kit, its `wrap`, its conventions block, the dotted-path prefix its function bindings need --------------------------------------
import zeta_adapter as ADAPTER                                                                               # LIB
KIT, WRAP, CONV, ALLOW = ADAPTER.swap, ADAPTER.wrap, {**ADAPTER.USD_SOFR_OIS_CONVENTIONS, "calendar": "nyc"}, ("pricebt", "zeta_adapter")   # LIB
# ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

GOLD = {"V0": -369827.43, "V1": -212114.86, "cash": -86490.36, "carry": -561.82, "roll": 1532.07, "delta": 70302.17, "convexity": -50.23, "residual": 0.02, "total": 71222.20}
DATA = json.loads((Path(__file__).resolve().parents[4] / "tests" / "data_golden_swap.json").read_text(encoding="utf8"))  # two solver-fitted curves, seeded fixings and holidays, frozen as plain numbers
iso = dt.date.fromisoformat


def pricer(ref, key):
    c = DATA[key]
    curve = CurveSnapshot("c", ref, tuple(iso(d) for d in c["dates"]), tuple(c["dfs"]))
    fx = [(iso(d), v) for d, v in zip(DATA["fixings"]["dates"], DATA["fixings"]["percent"]) if iso(d) < ref]  # only fixings strictly before the reference date
    fixings = FixingsSeries("f", tuple(d for d, _ in fx), tuple(v for _, v in fx), "percent")
    hol = CalendarData("nyc", tuple(iso(h) for h in DATA["holidays"]))
    return WRAP(SnapshotPricer(MarketSnapshot(pd.Timestamp(f"{ref} 17:00", tz="America/New_York"), ref, {"c": curve}, {"f": fixings}, None, {"nyc": hol})))


d0, d1 = dt.date(2025, 1, 2), dt.date(2025, 1, 16)
p0, p1 = pricer(d0, "c0"), pricer(d1, "c1")
spec = build_spec("golden", {"factory": KIT, "conventions": CONV}, schemas=SchemaRegistry.default(), allow=ALLOW)
terms = {"side": "pay", "effective": dt.date(2024, 1, 8), "maturity": dt.date(2027, 1, 8), "notional": 50e6, "fixed_rate": 4.20}  # NEUTRAL terms: percent, unsigned notional, side
built = TradeTemplate("golden", spec, terms).build(p0, p0.ts)  # struck at the t0 pricer, exactly as the engine does


def ctx(p, prev=None):
    return MarkContext(p.ts, p, {"primary": p}, None if prev is None else prev.ts, prev, p.ts if prev is None else prev.ts, p if prev is None else prev, {}, {})


def env(c):
    return Env(pricer=c.pricer, ctx=c, instrument=built.obj, terms=built.terms, state={})


c0, c1 = ctx(p0), ctx(p1, prev=p0)
v0, v1 = as_valuation(evaluate_value(spec, env(c0)), p0.ts), as_valuation(evaluate_value(spec, env(c1)), p1.ts)
got = {"V0": v0.pv, "V1": v1.pv, "cash": v1.cash}
got.update({n: float(evaluate_layer(spec, n, env(c1))) for n in ("carry", "roll", "delta", "convexity")})
got["total"] = v1.pv - v0.pv + v1.cash + v1.financing
got["residual"] = got["total"] - sum(got[n] for n in ("carry", "roll", "delta", "convexity"))  # what the ENGINE books as `unexplained`
bad = 0
for k, g in GOLD.items():
    ok = abs(got[k] - g) <= 0.011
    bad += not ok
    print(f"{'ok ' if ok else 'BAD'} {k:<10} {got[k]:>14,.2f}   golden {g:>14,.2f}")
sys.exit(1 if bad else 0)
