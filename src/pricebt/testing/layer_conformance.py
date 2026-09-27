"""The layer conformance kit (spec L4): independent identities every adapter's layers must satisfy, run through the adapter's own bound instrument spec.

A small `unexplained` only proves the layers are COMPLETE, not that they are RIGHT, so each check here is an identity that does not come from the layers themselves:

1. full revaluation vs delta + convexity   a parallel shock (small and large) of the market at ONE reference date: V1 - V0 equals `delta + convexity` (and `carry`, `roll` are zero)
2. mirror symmetry                        a payer and the matching receiver (a long and the matching short) have equal and opposite value, dv01, gamma and every layer
3. cash-sweep continuity                  with the market unchanged in date space, the value drop across a payment date equals the cash booked: V1 + cash - V0 == carry
                                          and the market-move layers (roll, delta, convexity) are zero
4. the unexplained share                  `unexplained` (interval P&L minus the layers) is what is left. It is REPORTED for an unchanged and for a moved market (spec L4 item 5),
                                          and bounded by a per-asset threshold. It is a residual by construction, so the check is the BOUND, not an identity

`Setup` names the adapter's spec (built with the adapter's kit and the shared conventions block), its `wrap`, two sets of trade terms (a holder and the mirror) and a world
factory: `world(reference_date, shock_bp) -> MarketSnapshot`, an unchanged market re-anchored at another date, or a parallel zero-rate shock of it. `flat_swap_world` and
`flat_bond_world` are library-free world factories: a flat continuously-compounded curve with fixings consistent with it, or a flat-yield bond panel.

    report = run_kit(Setup(spec=spec, wrap=wrap, terms=..., mirror_terms=..., world=flat_swap_world("nyc"), t0=dt.date(2024, 3, 4), t1=dt.date(2024, 3, 5)))
    report.assert_ok()
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import pandas as pd

from ..contracts.binding import Env
from ..contracts.evaluate import evaluate_layer, evaluate_measure, evaluate_value, scalar_of
from ..contracts.spec import InstrumentSpec, TradeTemplate
from ..pricable import MarkContext, as_valuation
from ..snapshot import MarketSnapshot, SnapshotPricer
from .synthetic import flat_bond_world, flat_swap_world  # noqa: F401  (re-exported: the library-free worlds a Setup needs)

LAYERS = ("carry", "roll", "delta", "convexity")


# ------------------------------------------------------------------------------ the kit
@dataclass
class Setup:
    """What the kit needs from an adapter."""

    spec: InstrumentSpec
    wrap: Callable[[Any], Any]
    terms: Mapping[str, Any]  # the holder: side 'pay' / 'buy' (+1) and the rest of the terms
    mirror_terms: Mapping[str, Any]  # the mirror image: the opposite side, everything else equal
    world: Callable[[dt.date, float], MarketSnapshot]
    t0: dt.date  # the reference date the position is struck on and first marked at
    t1: dt.date  # the next marking date (a later business day)
    shocks_bp: Sequence[float] = (-25.0, -3.0, 3.0, 25.0)  # small AND large enough that the convexity term is visible
    move_bp: float = 5.0  # the market move of the realistic-move check (with the date change)
    tol: Mapping[str, float] = field(default_factory=lambda: {"fd": 2e-3, "mirror": 1e-9, "static": 1e-8, "unexplained_share": 5e-2})
    payment_window: Optional[Tuple[dt.date, dt.date]] = None  # two dates around a payment date of the position for the cash-sweep check (before, after)
    birth: Optional[dt.date] = None  # the date the position is struck on (default t0); an earlier one gives a SEASONED position (started swap, aged bond)


@dataclass
class Row:
    check: str
    quantity: str
    value: float
    expected: float
    tolerance: float
    passed: bool
    note: str = ""


@dataclass
class KitReport:
    rows: List[Row] = field(default_factory=list)
    unexplained_share: Dict[str, float] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(r.passed for r in self.rows)

    def failures(self) -> List[Row]:
        return [r for r in self.rows if not r.passed]

    def assert_ok(self) -> None:
        bad = self.failures()
        if bad:
            raise AssertionError("layer conformance failed:\n" + "\n".join(f"  {r.check}/{r.quantity}: {r.value:.10g} vs expected {r.expected:.10g} (tolerance {r.tolerance:g}) {r.note}" for r in bad))

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame([r.__dict__ for r in self.rows])


class _Position:
    """One built position of a setup, valued and decomposed between two worlds."""

    def __init__(self, setup: Setup, terms: Mapping[str, Any], birth: dt.date):
        self.setup = setup
        tpl = TradeTemplate("kit", setup.spec, terms)
        p = setup.wrap(SnapshotPricer(setup.world(birth, 0.0)))
        built = tpl.build(p, p.ts)
        self.obj, self.terms = built.obj, built.terms

    def ctx(self, ref: dt.date, shock_bp: float, prev: Optional[Tuple[dt.date, float]] = None, base: Optional[Tuple[dt.date, float]] = None) -> MarkContext:
        s = self.setup
        p = s.wrap(SnapshotPricer(s.world(ref, shock_bp)))
        pp = s.wrap(SnapshotPricer(s.world(*prev))) if prev is not None else None
        e = s.wrap(SnapshotPricer(s.world(*(base or prev or (ref, shock_bp)))))
        return MarkContext(p.ts, p, {"primary": p}, pp.ts if pp is not None else None, pp, e.ts, e, {}, {}, s.spec.params)

    def env(self, ctx: MarkContext) -> Env:
        return Env(pricer=ctx.pricer, ctx=ctx, instrument=self.obj, terms=self.terms, state={})

    def value(self, ctx: MarkContext) -> Any:
        return as_valuation(evaluate_value(self.setup.spec, self.env(ctx)), ctx.ts)

    def layer(self, name: str, ctx: MarkContext) -> float:
        return float(evaluate_layer(self.setup.spec, name, self.env(ctx)))

    def measure(self, name: str, ctx: MarkContext) -> float:
        return scalar_of(evaluate_measure(self.setup.spec, name, self.env(ctx), cache=ctx.cache))


def _add(rep: KitReport, check: str, quantity: str, value: float, expected: float, tol: float, *, scale: float = 1.0, note: str = "") -> None:
    ok = abs(value - expected) <= tol * max(scale, 1e-12)  # NaN and inf compare False: they fail
    rep.rows.append(Row(check, quantity, float(value), float(expected), tol, ok, note))


def _layers(pos: _Position, ctx: MarkContext) -> Dict[str, float]:
    return {n: pos.layer(n, ctx) for n in LAYERS}


def run_kit(setup: Setup) -> KitReport:
    """Run every check of the kit; the report lists each row with its verdict (never raises on a failing identity: call `assert_ok`)."""
    rep = KitReport()
    tol = dict(setup.tol)
    pos = _Position(setup, setup.terms, setup.birth or setup.t0)
    mirror = _Position(setup, setup.mirror_terms, setup.birth or setup.t0)
    c0 = pos.ctx(setup.t0, 0.0)
    v0 = pos.value(c0)

    # 1. full revaluation against delta + convexity: a small parallel shock at ONE reference date
    for shock in setup.shocks_bp:
        c1 = pos.ctx(setup.t0, shock, prev=(setup.t0, 0.0), base=(setup.t0, 0.0))
        v1 = pos.value(c1)
        lay = _layers(pos, c1)
        pnl = (v1.pv + v1.cash) - (v0.pv + v0.cash)
        scale = abs(lay["delta"]) if abs(lay["delta"]) > 0 else max(abs(pnl), 1.0)
        _add(rep, "fd_vs_delta_convexity", f"shock {shock:+g}bp", lay["delta"] + lay["convexity"], pnl, tol["fd"], scale=scale, note="full revaluation minus (delta + convexity)")
        _add(rep, "fd_vs_delta_convexity", f"carry {shock:+g}bp", lay["carry"], 0.0, tol["static"], scale=scale, note="no time passes: nothing accrues")
        _add(rep, "fd_vs_delta_convexity", f"roll {shock:+g}bp", lay["roll"], 0.0, tol["static"], scale=scale, note="no time passes: nothing rolls")

    # 2. mirror symmetry: value, dv01, gamma and every layer are equal and opposite
    cm0 = mirror.ctx(setup.t0, 0.0)
    cm1 = mirror.ctx(setup.t1, setup.move_bp, prev=(setup.t0, 0.0), base=(setup.t0, 0.0))
    c1m = pos.ctx(setup.t1, setup.move_bp, prev=(setup.t0, 0.0), base=(setup.t0, 0.0))
    lay_a, lay_b = _layers(pos, c1m), _layers(mirror, cm1)
    scale = max(abs(v0.pv), abs(lay_a["delta"]), 1.0)
    _add(rep, "mirror", "value", pos.value(c0).pv + mirror.value(cm0).pv, 0.0, tol["mirror"], scale=scale)
    for m in ("dv01", "gamma"):
        a, b = pos.measure(m, c1m), mirror.measure(m, cm1)
        _add(rep, "mirror", m, a + b, 0.0, tol["mirror"], scale=max(abs(a), 1.0))
    for n in LAYERS:
        _add(rep, "mirror", n, lay_a[n] + lay_b[n], 0.0, tol["mirror"], scale=max(abs(lay_a[n]), abs(lay_a["delta"]), 1.0))

    # 3. cash-sweep continuity across a payment date, market unchanged in date space
    if setup.payment_window is not None:
        a, b = setup.payment_window
        ca, cb = pos.ctx(a, 0.0), pos.ctx(b, 0.0, prev=(a, 0.0), base=(a, 0.0))
        va, vb = pos.value(ca), pos.value(cb)
        lay = _layers(pos, cb)
        pnl = vb.pv + vb.cash + vb.financing - va.pv
        scale = max(abs(vb.cash), abs(pnl), 1.0)
        if abs(vb.cash) <= 0.0:
            rep.rows.append(Row("cash_sweep", "a payment falls in the window", 0.0, 1.0, 0.0, False, "the window has no payment: choose dates around a payment date"))
        else:
            _add(rep, "cash_sweep", "value drop equals cash booked", vb.pv + vb.cash + vb.financing - va.pv, lay["carry"], tol["static"], scale=scale,
                 note="V1 + cash + financing - V0 == carry on an unchanged market")
            for n in ("roll", "delta", "convexity"):
                _add(rep, "cash_sweep", n, lay[n], 0.0, tol["static"], scale=scale)

    # 4. + 5. layers plus unexplained are the interval P&L; the share left over is reported
    for label, shock in (("static", 0.0), ("moved", setup.move_bp)):
        cn = pos.ctx(setup.t1, shock, prev=(setup.t0, 0.0), base=(setup.t0, 0.0))
        v1 = pos.value(cn)
        lay = _layers(pos, cn)
        pnl = v1.pv + v1.cash + v1.financing - v0.pv
        share = abs(pnl - sum(lay.values())) / max(abs(pnl), sum(abs(x) for x in lay.values()), 1e-9)
        rep.unexplained_share[label] = share
        _add(rep, "unexplained_share", f"share ({label} market)", share, 0.0, tol["unexplained_share"], note="reported, and bounded by the per-asset threshold")
    return rep
