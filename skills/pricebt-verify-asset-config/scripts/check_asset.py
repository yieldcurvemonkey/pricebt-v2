"""check_asset: the "zero to confidence" checker for one newly written pricebt asset config.

Registers the config in its own PricebtSession, probes it the way a backtest will, and returns a
list of CheckResult(name, status, detail) with status PASS / WARN / FAIL / SKIP. The CLI prints a
markdown table and exits 1 if any check FAILed.

    python skills/pricebt-verify-asset-config/scripts/check_asset.py CONFIG [--fx FX]
        [--date YYYY-MM-DD [--date YYYY-MM-DD]] [--start YYYY-MM-DD --end YYYY-MM-DD]
        [--kwargs JSON] [--sys-path DIR ...] [--no-backtest] [--json OUT]

Run from the repository root with PYTHONPATH=src;tests (Windows) or src:tests (POSIX).
This script never imports a pricing library itself: pricing is reached only through pricebt.

Each check, and the realistic mistake it catches:

GENERIC (any asset)
  config_loads        schema errors: unknown key, bad unit, a risk measure naming a missing
                      function, missing Price mapping, expression syntax error.
  match               a `match:` rule the config's own defaults/kwargs fail, so a user's plain
                      `IRSwap(...)` finds no asset (WARN: pricebt_asset=... still works).
  imports_execute     `imports:`/`code:` that raise: wrong module name, library not on sys.path,
                      a login/licence call that fails.
  market_available    a market expression that raises or returns None on a normal business day
                      (wrong date type passed to the library, wrong curve name, wrong CSA).
  market_weekend      a market that RAISES on a non-trading day instead of returning None; the
                      engine's drop/roll-to-next-market-date logic only understands None.
                      Returning an object on a weekend is a WARN (stale-data risk).
  resolve_pins_terms  a `resolve:` that leaves a relative term (a '10y' tenor, an 'ATM' rate) as a
                      string: it is re-interpreted on every later pricing date, so the trade
                      "grows back to par" and P&L is silently wrong. Also WARNs when resolving on
                      two different dates gives identical dates for tenor kwargs (resolve ignores
                      the trade date).
  functions_finite    a `functions:`/`portfolio_functions:` entry that raises or returns a
                      non-number on a live trade (NaN is a WARN: allowed, but suspicious on d1).
  quantity_scaling    `scale_with_quantity:` contradicting the unit (an extensive ccy measure that
                      does not scale with quantity_, or an intensive rate that does).
  notional_linearity  a wrong `unit:` declaration: a par rate declared `ccy` (doubles nothing but
                      is scaled by quantity_) or a PV declared `bp` (never scaled). Probed by
                      doubling `notional_amount`; SKIP when the instrument has no such kwarg.
  risk_measures       a `risk_measures:` entry that fails to evaluate in its scalar or bucketed
                      form, or a measure name pricebt does not know (typo, WARN).
  measure_series      a market expression that ignores `pricebt_date` (always loads today's
                      curve): the intensive rate series is constant (WARN), or the series fails.
  smoke_backtest      NaN prices on some grid date (masked later by result_summary's ffill), or
                      the Total == Price + Cumulative Cash + Transaction Costs identity breaking.
  fx_round_trip       (--fx, non-USD asset) an FX config returning the same quote both ways.
  performance         a single evaluation slower than 1s (e.g. a curve rebuilt per function call
                      because the market object is not reused), reported with eval counts.

RATES-SWAP PACK (only when `instrument: IRSwap`)
  swap_atm_npv        ATM resolution struck on a different curve/date than the one used to value
                      (|npv| of an ATM payer >= 1e-4 * notional).
  swap_dv01_sign      dv01 sign convention flipped (payer dv01 must be > 0: PV change per +1bp),
                      or payer/receiver asymmetry (pay_or_receive ignored by the builder).
  swap_dv01_band      dv01 per 1% or per unit rate instead of per 1bp, or per unit notional.
  swap_par_rate_unit  par rate returned in decimal or percent while declared `bp` (or similar).
  swap_bucket_sum     a bucketed IRDelta ladder that does not sum to the scalar dv01.
  swap_pnl_explain    npv and dv01 with opposite sign conventions: npv(d2)-npv(d1) of the same
                      resolved payer should be ~ dv01 * change in its par rate (bp).
  swap_pv_identity    npv/dv01/par/fixed_rate disagreeing on sign or unit in a way no single
                      other check can see: PV != pv01*(par - K) at the ATM date or the off-market
                      one (PNL_EXPLAIN_PLAN.md 2.7).
  swap_gamma          gamma computed from dv01 differences instead of the true second npv
                      difference (the "half-gamma trap", PNL_EXPLAIN_PLAN.md 2.1), a payer/
                      receiver/band violation, or (via a probe over nearby dates) a gamma that is
                      roughly half what a clean second difference would give.
  swap_theta          theta per day instead of per year, another unit error (|theta| implausibly
                      large relative to dv01), or receiver != -payer.
  year_fraction       a quantity-scaled (extensive) time measure: declared `unit: number` instead
                      of `decimal`, so pricebt multiplies it by trade size (PNL_EXPLAIN_PLAN.md 2.3).
  cash_paid_to_date   receiver != -payer, or a fresh ATM trade's cumulative paid cash != 0.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

import numpy as np
from dateutil.relativedelta import relativedelta

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"

SLOW_EVAL_SECONDS = 1.0
# ponytail: the size kwarg is hardcoded to the gs field name every sized gs instrument uses; add a
# CLI flag if an asset sizes by something else (e.g. EqOption number_of_options).
SIZE_KWARG = "notional_amount"

_TENOR_RE = re.compile(r"^\s*\d+\s*[dwmy]\s*$", re.I)
_ATM_RE = re.compile(r"^\s*atm\b", re.I)
_EXTENSIVE_UNITS = {"ccy", "ccy_per_bp", "ccy_per_bp2", "number"}
_INTENSIVE_UNITS = {"bp", "pct", "decimal"}


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str = ""


@dataclass
class _Ctx:
    cfg: Any
    session: Any
    service: Any
    asset: Any
    inst: Any
    kwargs: Dict[str, Any]  # effective kwargs: defaults overlaid by the instrument's non-None kwargs
    d1: date
    d2: date
    start: date
    end: date
    fx_given: bool
    r1: Any = None
    evals: Dict[str, List[float]] = field(default_factory=dict)


# ------------------------------------------------------------------------------------ helpers


def _bday(d: date, step: int = 1) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=step)
    return d


def _is_relative(key: str, v: Any) -> bool:
    """A term that must be absolute after resolve but is not: a tenor string in a *_date field, or
    an ATM string in a rate/strike field. Convention strings (fixed_rate_frequency='6m') are fine."""
    if not isinstance(v, str):
        return False
    k = key.lower()
    return (k.endswith("_date") and bool(_TENOR_RE.match(v))) or (("rate" in k or "strike" in k) and bool(_ATM_RE.match(v)))


def _finite(v: Any) -> bool:
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def _swap_pnl_module():
    """Lazily import skills/pricebt-strategy-recipes/scripts/swap_pnl.py -- the SAME cross-skill
    sys.path pattern this test's own test file (tests/skills/test_skill_check_asset.py) already
    uses to import check_asset.py itself. Returns None (never raises) if that skill script is not
    importable in this invocation context, so check_asset.py's unrelated checks keep working
    regardless (PNL_EXPLAIN_PLAN.md 3.4)."""
    try:
        import swap_pnl

        return swap_pnl
    except ImportError:
        pass
    scripts_dir = Path(__file__).resolve().parents[2] / "pricebt-strategy-recipes" / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        import swap_pnl

        return swap_pnl
    except ImportError:
        return None


def _custom_measures() -> Dict[str, Any]:
    """swap_pnl.py's IRTheta/YearFraction/CashPaidToDate custom RiskMeasure singletons, keyed by
    their own `.name` (PNL_EXPLAIN_PLAN.md 3.4: "do not duplicate the strings" -- the key comes
    from the real measure object, never a hand-typed literal). Empty when swap_pnl.py is not
    importable here."""
    mod = _swap_pnl_module()
    if mod is None:
        return {}
    return {m.name: m for m in (mod.IRTheta, mod.YearFraction, mod.CashPaidToDate)}


def _risk(name: str):
    import pricebt.risk as pr

    measure = getattr(pr, name, None)
    return measure if measure is not None else _custom_measures().get(name)


def _scalar_form(measure):
    from pricebt.risk import RiskMeasureWithFiniteDifferenceParameter

    return measure(aggregation_level="Type") if isinstance(measure, RiskMeasureWithFiniteDifferenceParameter) else measure


def _fmt(v: float) -> str:
    return f"{v:,.6g}"


def _instrument(cfg, kwargs: Dict[str, Any]):
    import pricebt.instrument as pi

    if cfg.instrument == "ConfigInstrument":
        return pi.ConfigInstrument(cfg.name, name="probe", **kwargs)
    cls = getattr(pi, cfg.instrument, None)
    if cls is None:
        raise ValueError(f"instrument {cfg.instrument!r} is not a pricebt instrument class")
    return cls(pricebt_asset=cfg.name, name="probe", **kwargs)


def _install_eval_timer(ctx: _Ctx) -> None:
    """Record count and wall time of every evaluation of this asset's expressions.
    Uses the private PricingService._ns (instance-level wrap only; nothing global is patched)."""
    ns = ctx.service._ns(ctx.asset)
    orig = ns.eval

    def timed(key, **injected):
        t0 = time.perf_counter()
        try:
            return orig(key, **injected)
        finally:
            ctx.evals.setdefault(key, []).append(time.perf_counter() - t0)

    ns.eval = timed


# ------------------------------------------------------------------------------------ generic checks


def check_match(ctx: _Ctx) -> List[CheckResult]:
    from pricebt.errors import ConfigError

    if ctx.cfg.instrument == "ConfigInstrument":
        return [CheckResult("match", SKIP, "ConfigInstrument always names its asset")]
    try:
        ctx.session.registry.match(ctx.cfg.instrument, ctx.inst.kwargs)
    except ConfigError as exc:
        return [CheckResult("match", WARN, f"{ctx.cfg.instrument}(...) without pricebt_asset= finds no asset: {exc}")]
    return [CheckResult("match", PASS, f"{ctx.cfg.instrument}(...) matches {ctx.cfg.name} by its match: rules")]


def check_market_weekend(ctx: _Ctx) -> List[CheckResult]:
    sat = ctx.d1 + timedelta(days=(5 - ctx.d1.weekday()) % 7 or 7)
    try:
        has = ctx.service.has_market(ctx.asset, sat, None)
    except Exception as exc:
        return [CheckResult("market_weekend", FAIL, f"market raised on Saturday {sat} instead of returning None: {exc}")]
    if has:
        return [CheckResult("market_weekend", WARN, f"market returned an object on Saturday {sat}; check it is not stale data served for a non-trading day")]
    return [CheckResult("market_weekend", PASS, f"None on Saturday {sat}")]


def check_resolve(ctx: _Ctx) -> List[CheckResult]:
    terms = ctx.r1.resolved_terms
    shown = ", ".join(f"{k}={v!r}" for k, v in terms.items())
    bad = {k: v for k, v in terms.items() if _is_relative(k, v)}
    if bad:
        return [CheckResult("resolve_pins_terms", FAIL, f"resolved terms still relative (re-interpreted on every pricing date): {bad}")]
    str_dates = {k: v for k, v in terms.items() if k.lower().endswith("_date") and isinstance(v, str)}
    if str_dates:
        return [CheckResult("resolve_pins_terms", FAIL, f"date terms are strings, not date objects: {str_dates}; return datetime.date")]
    tenor_kwargs = {k: v for k, v in ctx.kwargs.items() if isinstance(v, str) and _TENOR_RE.match(v)}
    if tenor_kwargs:
        r2 = ctx.service.resolve(ctx.inst, ctx.d2, None)
        dates1 = {k: v for k, v in terms.items() if isinstance(v, date)}
        dates2 = {k: v for k, v in r2.resolved_terms.items() if isinstance(v, date)}
        if dates1 == dates2:
            return [CheckResult("resolve_pins_terms", WARN, f"tenor kwargs {tenor_kwargs} but resolving on {ctx.d1} and {ctx.d2} gives identical dates {dates1}: resolve ignores the trade date?")]
    return [CheckResult("resolve_pins_terms", PASS, shown)]


def check_functions(ctx: _Ctx) -> List[CheckResult]:
    out = []
    for fname, spec in ctx.cfg.functions.items():
        if spec.unit == "date":
            continue
        try:
            v = ctx.service.unit_value(ctx.r1, ctx.d1, fname, None)
        except Exception as exc:
            out.append(CheckResult(f"functions_finite[{fname}]", FAIL, f"{type(exc).__name__}: {exc}"))
            continue
        status = PASS if math.isfinite(v) else WARN
        out.append(CheckResult(f"functions_finite[{fname}]", status, f"{_fmt(v)} {spec.unit} on {ctx.d1}"))
    for fname, spec in ctx.cfg.portfolio_functions.items():
        try:
            v = ctx.service.portfolio_value(ctx.asset, ctx.d1, fname, [ctx.r1], None)
        except Exception as exc:
            out.append(CheckResult(f"functions_finite[{fname}]", FAIL, f"{type(exc).__name__}: {exc}"))
            continue
        values = list(v.values()) if isinstance(v, dict) else [v]
        status = PASS if all(_finite(x) for x in values) else WARN
        shown = {k: _fmt(x) for k, x in v.items()} if isinstance(v, dict) else _fmt(v)
        out.append(CheckResult(f"functions_finite[{fname}]", status, f"{shown} {spec.unit}"))
    return out


def check_quantity_scaling(ctx: _Ctx) -> List[CheckResult]:
    out = []
    scaled = ctx.r1.clone(quantity_=-3.0)
    for mname, mapping in ctx.cfg.risk_measures.items():
        fname = mapping.scalar
        measure = _risk(mname)
        if fname is None or measure is None:
            continue
        spec = ctx.cfg.functions.get(fname) or ctx.cfg.portfolio_functions[fname]
        if spec.unit not in _EXTENSIVE_UNITS | _INTENSIVE_UNITS:
            continue
        m = _scalar_form(measure)
        # d2, not d1: the d1-resolved trade has moved off ATM there, so an ATM npv is not ~0
        v1 = float(ctx.service.value(ctx.r1, ctx.d2, m, None))
        v3 = float(ctx.service.value(scaled, ctx.d2, m, None))
        factor = -3.0 if spec.unit in _EXTENSIVE_UNITS else 1.0
        ok = math.isclose(v3, factor * v1, rel_tol=1e-9, abs_tol=1e-12)
        kind = "extensive" if factor < 0 else "intensive"
        detail = f"{fname} ({spec.unit}, {kind}): q=1 {_fmt(v1)}, q=-3 {_fmt(v3)} on {ctx.d2}, expected x{factor:g}"
        if ok and factor < 0 and math.isclose(v3, v1, rel_tol=1e-9, abs_tol=1e-12):
            detail += " (~0: inconclusive)"
        out.append(CheckResult(f"quantity_scaling[{mname}]", PASS if ok else FAIL, detail if ok else detail + "; fix scale_with_quantity or the unit"))
    return out or [CheckResult("quantity_scaling", SKIP, "no scalar risk measure with a ccy/bp/pct/decimal/number unit")]


def check_notional_linearity(ctx: _Ctx) -> List[CheckResult]:
    n = ctx.kwargs.get(SIZE_KWARG)
    if not isinstance(n, (int, float)):
        return [CheckResult("notional_linearity", SKIP, f"no numeric {SIZE_KWARG} kwarg")]
    doubled = ctx.service.resolve(ctx.inst.clone(**{SIZE_KWARG: 2 * n}), ctx.d1, None)
    out = []
    for fname, spec in ctx.cfg.functions.items():
        if spec.unit not in _EXTENSIVE_UNITS | _INTENSIVE_UNITS:
            continue
        v1 = ctx.service.unit_value(ctx.r1, ctx.d2, fname, None)  # d2: off ATM (see quantity_scaling)
        v2 = ctx.service.unit_value(doubled, ctx.d2, fname, None)
        tol = dict(rel_tol=1e-6, abs_tol=1e-9 * abs(n))
        doubles, same = math.isclose(v2, 2 * v1, **tol), math.isclose(v2, v1, **tol)
        extensive = spec.unit in _EXTENSIVE_UNITS
        detail = f"{fname} ({spec.unit}): N {_fmt(v1)}, 2N {_fmt(v2)}"
        if doubles and same:
            out.append(CheckResult(f"notional_linearity[{fname}]", PASS, detail + " (~0: inconclusive)"))
        elif (extensive and doubles) or (not extensive and same):
            out.append(CheckResult(f"notional_linearity[{fname}]", PASS, detail))
        elif extensive and same:
            out.append(CheckResult(f"notional_linearity[{fname}]", FAIL, detail + ": does not scale with notional, so it is a rate; declare bp/pct/decimal"))
        elif not extensive and doubles:
            out.append(CheckResult(f"notional_linearity[{fname}]", FAIL, detail + ": scales with notional, so it is an amount; declare ccy/ccy_per_bp"))
        else:
            out.append(CheckResult(f"notional_linearity[{fname}]", WARN, detail + ": neither linear nor constant in notional"))
    return out


def check_risk_measures(ctx: _Ctx) -> List[CheckResult]:
    from pricebt.risk.results import LazyFuture

    out = []
    for mname, mapping in ctx.cfg.risk_measures.items():
        measure = _risk(mname)
        if measure is None:
            out.append(CheckResult(f"risk_measures[{mname}]", WARN, "not a pricebt.risk measure name: typo? (reachable only via a custom RiskMeasure(name=...))"))
            continue
        shown = []
        if mapping.scalar is not None or mapping.bucketed is not None:
            v = ctx.service.value(ctx.r1, ctx.d1, _scalar_form(measure), None)
            v = v.result() if isinstance(v, LazyFuture) else v
            shown.append(f"scalar {_fmt(float(v))}" if not hasattr(v, "columns") else f"{len(v)} buckets")
        if mapping.bucketed is not None:
            lazy = ctx.service.value(ctx.r1, ctx.d1, measure, None)
            df = lazy.result() if isinstance(lazy, LazyFuture) else lazy
            shown.append(f"{len(df)} buckets summing to {_fmt(float(df['value'].sum()))}")
        out.append(CheckResult(f"risk_measures[{mname}]", PASS, "; ".join(shown)))
    return out


def check_measure_series(ctx: _Ctx) -> List[CheckResult]:
    from pricebt.data import measure_series

    # A rate, else a non-Price amount (dv01, vega): a fresh ATM npv is ~0 every day and would look constant.
    price_fn = ctx.cfg.risk_measures["Price"].scalar
    rates = [f for f, s in ctx.cfg.functions.items() if s.unit in _INTENSIVE_UNITS]
    others = [f for f, s in ctx.cfg.functions.items() if s.unit in _EXTENSIVE_UNITS and f != price_fn]
    fname = (rates or others or [None])[0]
    if fname is None:
        return [CheckResult("measure_series", SKIP, "no rate or non-Price function to track over time")]
    s = measure_series(ctx.inst, fname, ctx.d1, _bday(ctx.d1 + timedelta(days=14)))
    if s.empty or not np.isfinite(s.to_numpy()).all():
        return [CheckResult("measure_series", FAIL, f"{fname}: empty or non-finite series {s.to_dict()}")]
    if s.nunique() == 1:
        return [CheckResult("measure_series", WARN, f"{fname} is constant ({_fmt(s.iloc[0])}) over {len(s)} dates: does the market expression use pricebt_date?")]
    return [CheckResult("measure_series", PASS, f"{fname}: {len(s)} points, {_fmt(s.min())}..{_fmt(s.max())} {s.attrs.get('unit')}, missing {len(s.attrs.get('missing_dates', []))}")]


def check_smoke_backtest(ctx: _Ctx) -> List[CheckResult]:
    from pricebt.backtests.actions import AddTradeAction
    from pricebt.backtests.generic_engine import GenericEngine
    from pricebt.backtests.strategy import Strategy
    from pricebt.backtests.triggers import PeriodicTrigger, PeriodicTriggerRequirements
    from pricebt.risk import Price

    trigger = PeriodicTrigger(PeriodicTriggerRequirements(frequency="1m", end_date=ctx.end), AddTradeAction(ctx.inst.clone(name="smoke"), "1m"))
    t0 = time.perf_counter()
    bt = GenericEngine().run_backtest(Strategy(None, trigger), start=ctx.start, end=ctx.end, frequency="1b", show_progress=False)
    secs = time.perf_counter() - t0
    raw = bt.get_risk_summary_df()[Price].astype(float)
    bad_dates = [str(d) for d, v in raw.items() if not math.isfinite(v)]
    if bad_dates:
        return [CheckResult("smoke_backtest", FAIL, f"non-finite Price on {bad_dates[:5]} (result_summary would ffill over them)")]
    summary = bt.result_summary
    expected = summary[Price] + summary["Cumulative Cash"] + summary["Transaction Costs"]
    if summary.empty or not np.allclose(summary["Total"].to_numpy(float), expected.to_numpy(float), rtol=1e-9, atol=1e-6, equal_nan=False):
        return [CheckResult("smoke_backtest", FAIL, "Total != Price + Cumulative Cash + Transaction Costs on some row (or empty summary)")]
    return [CheckResult("smoke_backtest", PASS, f"{len(summary)} rows {ctx.start}..{ctx.end}, {len(bt.trade_ledger())} trades, identity holds, {len(bt.missing_market_moves)} missing-market moves, {secs:.1f}s")]


def check_fx(ctx: _Ctx) -> List[CheckResult]:
    ccy = ctx.cfg.currency
    if not ctx.fx_given or ccy == "USD":
        return [CheckResult("fx_round_trip", SKIP, "no --fx, or asset is USD")]
    a, b = ctx.service.fx(ccy, "USD", ctx.d1), ctx.service.fx("USD", ccy, ctx.d1)
    ok = math.isclose(a * b, 1.0, rel_tol=1e-9)
    return [CheckResult("fx_round_trip", PASS if ok else FAIL, f"fx({ccy},USD)={_fmt(a)} * fx(USD,{ccy})={_fmt(b)} = {_fmt(a * b)}")]


def check_performance(ctx: _Ctx) -> List[CheckResult]:
    if not ctx.evals:
        return [CheckResult("performance", SKIP, "no evaluations recorded")]
    parts = [f"{k}: {len(v)}x max {max(v):.3f}s" for k, v in sorted(ctx.evals.items())]
    slow = [k for k, v in ctx.evals.items() if max(v) > SLOW_EVAL_SECONDS]
    status = WARN if slow else PASS
    return [CheckResult("performance", status, ("SLOW (>1s): " + ", ".join(slow) + "; " if slow else "") + "; ".join(parts))]


# ------------------------------------------------------------------------------------ RATES-SWAP optional pack


def swap_pack(ctx: _Ctx) -> List[CheckResult]:
    """Optional check pack, run only when the config's instrument is IRSwap. Conventions assumed
    (pricebt's, from gs): payer dv01 > 0 is the PV change per +1bp; pay_or_receive 'Pay'/'Receive'."""
    from pricebt.risk import IRDelta, IRFwdRate, IRGammaParallel, Price
    from pricebt.risk.results import LazyFuture

    svc, d1, d2 = ctx.service, ctx.d1, ctx.d2
    payer = svc.resolve(ctx.inst.clone(pay_or_receive="Pay"), d1, None)
    recv = svc.resolve(ctx.inst.clone(pay_or_receive="Receive"), d1, None)
    n = ctx.kwargs.get(SIZE_KWARG)
    n = abs(float(n)) if isinstance(n, (int, float)) else None
    has_dv01 = "IRDelta" in ctx.cfg.risk_measures
    dv01 = lambda inst, d: float(svc.value(inst, d, IRDelta(aggregation_level="Type"), None))  # noqa: E731
    npv = lambda inst, d: float(svc.value(inst, d, Price, None))  # noqa: E731
    out = []

    # PNL_EXPLAIN_PLAN.md 3.4: a par-rate accessor (in bp), hoisted for the new checks below to
    # share. The existing swap_par_rate_unit/swap_par_rate_atm checks further down keep their own,
    # separate par_fn resolution unchanged, per this task's "do not restructure the existing
    # checks" rule -- this is purely additive.
    _par_fn = ctx.cfg.risk_measures.get("IRFwdRate")
    _par_fn = _par_fn.scalar if _par_fn is not None else ("par_rate" if "par_rate" in ctx.cfg.functions else None)
    _par_unit = ctx.cfg.functions[_par_fn].unit if (_par_fn is not None and _par_fn in ctx.cfg.functions) else None
    _to_bp = {"bp": 1.0, "pct": 100.0, "decimal": 1e4}.get(_par_unit)
    par = (lambda inst, d: svc.unit_value(inst, d, _par_fn, None) * _to_bp) if _to_bp is not None else None

    # ATM npv ~ 0
    fr = ctx.kwargs.get("fixed_rate")
    if n is None or not (fr is None or (isinstance(fr, str) and _ATM_RE.match(fr))):
        out.append(CheckResult("swap_atm_npv", SKIP, f"fixed_rate={fr!r} is not ATM, or no {SIZE_KWARG}"))
    else:
        v = npv(payer, d1)
        status = PASS if abs(v) < 1e-4 * n else WARN if abs(v) < 1e-3 * n else FAIL
        out.append(CheckResult("swap_atm_npv", status, f"ATM payer npv {_fmt(v)} vs 1e-4*N = {_fmt(1e-4 * n)}"))

    if not has_dv01:
        out.append(CheckResult("swap_dv01_sign", SKIP, "no IRDelta mapping"))
    else:
        dp, dr = dv01(payer, d1), dv01(recv, d1)
        if dp <= 0:
            out.append(CheckResult("swap_dv01_sign", FAIL, f"payer dv01 {_fmt(dp)} <= 0: pricebt wants PV change per +1bp (payer > 0)"))
        elif not math.isclose(dr, -dp, rel_tol=0.01):
            out.append(CheckResult("swap_dv01_sign", FAIL, f"receiver dv01 {_fmt(dr)} != -payer {_fmt(-dp)}: is pay_or_receive reaching the trade builder?"))
        else:
            out.append(CheckResult("swap_dv01_sign", PASS, f"payer {_fmt(dp)}, receiver {_fmt(dr)}"))

        eff, mat = payer.resolved_terms.get("effective_date"), payer.resolved_terms.get("termination_date")
        if n is None or not (isinstance(eff, date) and isinstance(mat, date)):
            out.append(CheckResult("swap_dv01_band", SKIP, "needs notional and resolved effective/termination dates"))
        else:
            # dv01 = N * annuity * 1bp, annuity = sum(accrual * DF). Annuity <= years at rates >= 0
            # (<= ~1.16*years at -1% over 30y) and >= ~0.3*years at 10% over 30y, so the band below
            # holds for any sane curve and T <= 30y; x100 (per 1%) or x1e4 (per unit) errors fall out.
            years = (mat - eff).days / 365.25
            lo, hi = 0.5e-4 * n * years * 0.5, 1.2e-4 * n * years
            status = PASS if lo < abs(dp) < hi else FAIL
            out.append(CheckResult("swap_dv01_band", status, f"abs(dv01) {_fmt(abs(dp))} vs band ({_fmt(lo)}, {_fmt(hi)}) for N={_fmt(n)}, {years:.1f}y"))

        bucketed = ctx.cfg.risk_measures["IRDelta"].bucketed
        if bucketed is None or ctx.cfg.risk_measures["IRDelta"].scalar is None:
            out.append(CheckResult("swap_bucket_sum", SKIP, "IRDelta needs both scalar and bucketed mappings"))
        else:
            lazy = svc.value(payer, d1, IRDelta, None)
            total = float((lazy.result() if isinstance(lazy, LazyFuture) else lazy)["value"].sum())
            ok = math.isclose(total, dp, rel_tol=0.02)
            out.append(CheckResult("swap_bucket_sum", PASS if ok else FAIL, f"sum of {bucketed} {_fmt(total)} vs scalar {_fmt(dp)} (2% tolerance)"))

    par_fn = ctx.cfg.risk_measures.get("IRFwdRate")
    par_fn = par_fn.scalar if par_fn is not None else ("par_rate" if "par_rate" in ctx.cfg.functions else None)
    if par_fn is None or par_fn not in ctx.cfg.functions:
        out.append(CheckResult("swap_par_rate_unit", SKIP, "no IRFwdRate mapping or par_rate function"))
    else:
        unit = ctx.cfg.functions[par_fn].unit
        v = svc.unit_value(payer, d1, par_fn, None)
        bands = {"bp": (-500, 2000), "pct": (-5, 20), "decimal": (-0.05, 0.20)}
        if unit not in bands:
            out.append(CheckResult("swap_par_rate_unit", FAIL, f"{par_fn} declared {unit!r}; a rate must be bp/pct/decimal"))
        else:
            lo, hi = bands[unit]
            status, note = PASS, ""
            if not lo < v < hi:
                status, note = FAIL, f" outside plausible {unit} band ({lo}, {hi})"
            elif unit == "bp" and abs(v) < 0.2:
                status, note = FAIL, " looks like decimal or percent, expected bp"
            elif unit == "bp" and abs(v) < 20:
                status, note = WARN, " could be percent (a swap rate under 20bp is rare)"
            out.append(CheckResult("swap_par_rate_unit", status, f"{par_fn} = {_fmt(v)} {unit}{note}"))

        # exact: an ATM trade's par rate on its trade date is its own fixed rate (resolved, gs decimal)
        k = payer.resolved_terms.get("fixed_rate")
        to_bp = {"bp": 1.0, "pct": 100.0, "decimal": 1e4}.get(unit)
        if not (fr is None or (isinstance(fr, str) and fr.strip().upper() == "ATM")) or not isinstance(k, (int, float)) or to_bp is None:
            out.append(CheckResult("swap_par_rate_atm", SKIP, f"needs fixed_rate='ATM' (got {fr!r}) and a numeric resolved fixed_rate"))
        else:
            par_bp, k_bp = v * to_bp, float(k) * 1e4
            status = PASS if abs(par_bp - k_bp) < 0.5 else FAIL
            out.append(CheckResult("swap_par_rate_atm", status, f"{par_fn} at trade date {par_bp:.4g}bp vs resolved fixed_rate x 1e4 = {k_bp:.4g}bp (0.5bp tolerance)"))

    if not has_dv01 or par_fn is None or par_fn not in ctx.cfg.functions:
        out.append(CheckResult("swap_pnl_explain", SKIP, "needs IRDelta and a par rate function"))
    else:
        to_bp = {"bp": 1.0, "pct": 100.0, "decimal": 1e4}.get(ctx.cfg.functions[par_fn].unit)
        dpar = (svc.unit_value(payer, d2, par_fn, None) - svc.unit_value(payer, d1, par_fn, None)) * (to_bp or 0)
        dnpv = npv(payer, d2) - npv(payer, d1)
        predicted = 0.5 * (dv01(payer, d1) + dv01(payer, d2)) * dpar
        if to_bp is None or abs(dpar) < 5:
            out.append(CheckResult("swap_pnl_explain", SKIP, f"par rate moved {dpar:.2f}bp between {d1} and {d2} (< 5bp: carry dominates)"))
        else:
            ratio = dnpv / predicted if predicted else float("nan")
            status = FAIL if ratio < 0 else PASS if 0.5 <= ratio <= 1.5 else WARN
            out.append(CheckResult("swap_pnl_explain", status, f"payer npv change {_fmt(dnpv)} vs dv01 x {dpar:.2f}bp = {_fmt(predicted)} (ratio {ratio:.3f})"))

    # -------------------------------------------------------------------------- PNL_EXPLAIN_PLAN.md 3.4

    # swap_pv_identity: PV = pv01*(par - K) at d1 (ATM) AND d2 (now off-market). Catches
    # npv/dv01/par/fixed_rate unit or sign disagreements no single-function check sees alone (2.7).
    #
    # The identity is algebraically EXACT only when "dv01" is the fixed-leg ANNUITY (PNL_EXPLAIN_
    # PLAN.md 2.1's convention for the toy and ARBS: strike-independent, since PV is linear in K with
    # slope -annuity). A config may instead report a realistic full-curve PV sensitivity (also a
    # valid "dv01"/IRDelta, per this file's own "PV change per +1bp" convention) that DOES depend on
    # the strike -- that is not a bug, just a different, equally legitimate convention (confirmed on
    # skills/pricebt-connect-pricing-library/example/meridian_usd_irs.yaml: its DV01 is an analytic
    # full-curve sensitivity, docs/v2/DECISIONS_LOG.md 2026-09-29 T1-C). Probe which one this config
    # uses BEFORE trusting the exact tolerance, so a strike-dependent dv01 gets a WARN (with a sign-
    # only FAIL check, since a sign disagreement is never a convention difference) instead of a
    # false FAIL from a legitimate design choice.
    fixed_rate_1 = payer.resolved_terms.get("fixed_rate")
    if not has_dv01 or par is None or n is None or not isinstance(fixed_rate_1, (int, float)):
        out.append(CheckResult("swap_pv_identity", SKIP, "needs IRDelta, a bp/pct/decimal par-rate function, a numeric notional, and a numeric resolved fixed_rate"))
    else:
        k_bp = float(fixed_rate_1) * 1e4
        dv01_atm = dv01(payer, d1)
        off_market_probe = svc.resolve(ctx.inst.clone(pay_or_receive="Pay", fixed_rate=fixed_rate_1 + 0.01), d1, None)
        dv01_probe = dv01(off_market_probe, d1)
        is_annuity = math.isclose(dv01_probe, dv01_atm, rel_tol=1e-6)

        if is_annuity:
            parts, status = [], PASS
            for label, d in (("d1(ATM)", d1), ("d2", d2)):
                v_npv, v_dv01, v_par = npv(payer, d), dv01(payer, d), par(payer, d)
                lhs = abs(v_npv - v_dv01 * (v_par - k_bp))
                tol = 1e-6 * n + 1e-3 * abs(v_dv01)
                ok = lhs <= tol
                status = status if ok else FAIL
                parts.append(f"{label}: abs(npv-dv01*(par-K))={_fmt(lhs)} vs tol {_fmt(tol)} ({'ok' if ok else 'BREAKS'})")
            out.append(CheckResult("swap_pv_identity", status, "; ".join(parts)))
        else:
            v_npv2, v_dv012, v_par2 = npv(payer, d2), dv01(payer, d2), par(payer, d2)
            predicted2 = v_dv012 * (v_par2 - k_bp)
            sign_ok = predicted2 == 0 or v_npv2 == 0 or (v_npv2 > 0) == (predicted2 > 0)
            detail = (
                f"dv01 depends on the strike (dv01(ATM)={_fmt(dv01_atm)} vs dv01(K+100bp)={_fmt(dv01_probe)}): "
                f"a full-curve PV sensitivity, not an annuity pv01, so PV=dv01*(par-K) is exact only for the "
                f"annuity convention (PNL_EXPLAIN_PLAN.md 2.1); d2 npv={_fmt(v_npv2)} vs dv01*(par-K)={_fmt(predicted2)}"
            )
            if not sign_ok:
                detail += " -- SIGN DISAGREEMENT (never just a convention difference)"
            out.append(CheckResult("swap_pv_identity", WARN if sign_ok else FAIL, detail))

    # swap_gamma: only if IRGammaParallel is mapped. Sign/receiver/band, plus the half-gamma probe.
    gamma_mapping = ctx.cfg.risk_measures.get("IRGammaParallel")
    if gamma_mapping is None or gamma_mapping.scalar is None:
        out.append(CheckResult("swap_gamma", SKIP, "no IRGammaParallel mapping"))
    elif not has_dv01 or par is None:
        out.append(CheckResult("swap_gamma", SKIP, "IRGammaParallel mapped, but needs IRDelta and a bp/pct/decimal par-rate function too"))
    else:
        gamma_measure = _risk("IRGammaParallel")
        gamma_val = lambda inst, d: float(svc.value(inst, d, gamma_measure, None))  # noqa: E731
        g_payer, g_recv = gamma_val(payer, d1), gamma_val(recv, d1)
        dv01_1 = dv01(payer, d1)

        problems = []
        if g_payer >= 0:
            problems.append(f"payer Gamma {_fmt(g_payer)} >= 0 (want < 0)")
        if not math.isclose(g_recv, -g_payer, rel_tol=0.01):
            problems.append(f"receiver Gamma {_fmt(g_recv)} != -payer {_fmt(-g_payer)}")
        eff, mat = payer.resolved_terms.get("effective_date"), payer.resolved_terms.get("termination_date")
        band_note = ""
        if isinstance(eff, date) and isinstance(mat, date) and dv01_1:
            years = (mat - eff).days / 365.0
            band_ratio = abs(g_payer) / (abs(dv01_1) * years * 1e-4)
            band_note = f", abs(Gamma)/(abs(dv01)*T*1e-4)={band_ratio:.3f}"
            if not (0.2 <= band_ratio <= 2.0):
                problems.append(f"abs(Gamma)/(abs(dv01)*T*1e-4)={band_ratio:.3f} outside [0.2, 2]")
        base_status = FAIL if problems else PASS
        base_detail = "; ".join(problems) if problems else f"payer {_fmt(g_payer)}, receiver {_fmt(g_recv)}{band_note}"

        # Half-gamma probe (3.4): search up to 30 business days from d1 for a pair up to 10
        # business days apart (need not be consecutive) where the SAME resolved ATM trade's
        # |dpar| >= 3bp; Gamma_est = 2*ddv01/dpar over that pair. Ratio Gamma/Gamma_est in [0.7,1.4]
        # -> PASS; in [0.4,0.6] -> FAIL (half-gamma trap); otherwise -> WARN; no qualifying pair ->
        # SKIP. Among every qualifying pair, use the one with the LARGEST |dpar| (cleanest signal,
        # least sensitive to the higher-order terms T-GAMMA-3/DECISIONS_LOG 2026-09-29 found to be
        # non-negligible at a small bump) rather than just the first one found.
        bdays = [d1]
        for _ in range(30):
            bdays.append(_bday(bdays[-1] + timedelta(days=1)))
        pars = {d: par(payer, d) for d in bdays}
        best_pair = None  # (abs_dpar, i, gap)
        for gap in range(1, 11):
            for i in range(len(bdays) - gap):
                dpar_probe = pars[bdays[i + gap]] - pars[bdays[i]]
                if abs(dpar_probe) >= 3.0 and (best_pair is None or abs(dpar_probe) > best_pair[0]):
                    best_pair = (abs(dpar_probe), i, gap)
        if best_pair is None:
            probe_status, probe_detail = SKIP, "no business-day pair within 30 business days had abs(dpar) >= 3bp"
        else:
            _, i, gap = best_pair
            di, dj = bdays[i], bdays[i + gap]
            dpar_probe = pars[dj] - pars[di]
            gamma_est = 2.0 * (dv01(payer, dj) - dv01(payer, di)) / dpar_probe
            ratio = g_payer / gamma_est if gamma_est else float("nan")
            where = f"{di}->{dj} (gap {gap}bd) dpar={dpar_probe:.2f}bp, Gamma/Gamma_est={ratio:.3f}"
            if 0.7 <= ratio <= 1.4:
                probe_status, probe_detail = PASS, where
            elif 0.4 <= ratio <= 0.6:
                probe_status, probe_detail = FAIL, f"{where}: looks like gamma from dv01 differences (half-gamma trap)"
            else:
                probe_status, probe_detail = WARN, where

        final_status = base_status
        if probe_status == FAIL:
            final_status = FAIL
        elif probe_status == WARN and base_status == PASS:
            final_status = WARN
        out.append(CheckResult("swap_gamma", final_status, f"{base_detail}; half-gamma probe: {probe_detail}"))

    # swap_theta: only if IRTheta is mapped. receiver ~= -payer; |theta| <= 1000*|dv01| (unit check).
    theta_mapping = ctx.cfg.risk_measures.get("IRTheta")
    if theta_mapping is None or theta_mapping.scalar is None:
        out.append(CheckResult("swap_theta", SKIP, "no IRTheta mapping"))
    elif not has_dv01:
        out.append(CheckResult("swap_theta", SKIP, "IRTheta mapped, but needs IRDelta too"))
    else:
        theta_measure = _risk("IRTheta")
        if theta_measure is None:
            out.append(CheckResult("swap_theta", SKIP, "IRTheta mapped, but skills/pricebt-strategy-recipes/scripts/swap_pnl.py is not importable here"))
        else:
            # d2, not d1: the fresh ATM trade's theta at d1 is ~0 (same reasoning check_quantity_
            # scaling already uses), which would make both the sign-symmetry and magnitude checks
            # below vacuous. At d2 the trade has moved off ATM, so theta is a real, checkable number.
            th_payer = float(svc.value(payer, d2, theta_measure, None))
            th_recv = float(svc.value(recv, d2, theta_measure, None))
            dv01_2 = dv01(payer, d2)
            problems = []
            if not math.isclose(th_recv, -th_payer, rel_tol=0.01, abs_tol=1e-6):
                problems.append(f"receiver theta {_fmt(th_recv)} != -payer {_fmt(-th_payer)}")
            if abs(th_payer) > 1000 * abs(dv01_2):
                problems.append(f"abs(theta) {_fmt(abs(th_payer))} > 1000*abs(dv01) {_fmt(1000 * abs(dv01_2))}: looks like a per-day theta or other unit error")
            status = FAIL if problems else PASS
            out.append(CheckResult("swap_theta", status, "; ".join(problems) if problems else f"payer {_fmt(th_payer)}, receiver {_fmt(th_recv)} (at {d2})"))

    # year_fraction: only if YearFraction is mapped. delta(d1,d2) == (d2-d1).days/365; identical at x3
    # quantity (intensive; DESIGN.md 4.2 -- pricebt would otherwise scale it by trade size).
    yf_mapping = ctx.cfg.risk_measures.get("YearFraction")
    if yf_mapping is None or yf_mapping.scalar is None:
        out.append(CheckResult("year_fraction", SKIP, "no YearFraction mapping"))
    else:
        yf_measure = _risk("YearFraction")
        if yf_measure is None:
            out.append(CheckResult("year_fraction", SKIP, "YearFraction mapped, but skills/pricebt-strategy-recipes/scripts/swap_pnl.py is not importable here"))
        else:
            yf1 = float(svc.value(payer, d1, yf_measure, None))
            yf2 = float(svc.value(payer, d2, yf_measure, None))
            expected_delta = (d2 - d1).days / 365.0
            yf1_x3 = float(svc.value(payer.clone(quantity_=3.0), d1, yf_measure, None))
            problems = []
            if abs((yf2 - yf1) - expected_delta) > 1e-12:
                problems.append(f"delta_year_fraction {yf2 - yf1:.12g} != (d2-d1).days/365 = {expected_delta:.12g}")
            if abs(yf1_x3 - yf1) > 1e-12:
                problems.append(f"year_fraction x1={yf1:.12g} != x3={yf1_x3:.12g}: not intensive (unit: number instead of decimal?)")
            status = FAIL if problems else PASS
            out.append(CheckResult("year_fraction", status, "; ".join(problems) if problems else f"delta={yf2 - yf1:.6g} == (d2-d1)/365; identical at quantity x3 (intensive)"))

    # cash_paid_to_date: only if mapped. A fresh ATM trade's cash at d1 -> 0; receiver = -payer at d2.
    cash_mapping = ctx.cfg.risk_measures.get("CashPaidToDate")
    if cash_mapping is None or cash_mapping.scalar is None:
        out.append(CheckResult("cash_paid_to_date", SKIP, "no CashPaidToDate mapping"))
    else:
        cash_measure = _risk("CashPaidToDate")
        if cash_measure is None:
            out.append(CheckResult("cash_paid_to_date", SKIP, "CashPaidToDate mapped, but skills/pricebt-strategy-recipes/scripts/swap_pnl.py is not importable here"))
        else:
            c1 = float(svc.value(payer, d1, cash_measure, None))
            c2_payer = float(svc.value(payer, d2, cash_measure, None))
            c2_recv = float(svc.value(recv, d2, cash_measure, None))
            problems = []
            is_fresh_atm = fr is None or (isinstance(fr, str) and _ATM_RE.match(fr))
            if is_fresh_atm and abs(c1) > 1e-6:
                problems.append(f"fresh ATM trade cash_paid_to_date at d1 = {_fmt(c1)} != 0")
            if not math.isclose(c2_recv, -c2_payer, rel_tol=0.01, abs_tol=1e-6):
                problems.append(f"receiver cash {_fmt(c2_recv)} != -payer {_fmt(-c2_payer)} at d2")
            status = FAIL if problems else PASS
            out.append(CheckResult("cash_paid_to_date", status, "; ".join(problems) if problems else f"d1 fresh-ATM cash {_fmt(c1)}; d2 payer {_fmt(c2_payer)} receiver {_fmt(c2_recv)}"))

    return out


# ------------------------------------------------------------------------------------ runner


def _default_dates(dates: Sequence[date], start: Optional[date], end: Optional[date]):
    d1 = _bday(dates[0]) if dates else _bday(date.today() - timedelta(days=120))
    d2 = _bday(dates[1]) if len(dates) > 1 else _bday(d1 + relativedelta(months=1))
    start = start or d1
    end = end or _bday(start + relativedelta(months=3), -1)
    return d1, d2, start, end


def run_checks(
    config,
    *,
    fx=None,
    dates: Sequence[date] = (),
    start: Optional[date] = None,
    end: Optional[date] = None,
    kwargs: Optional[Dict[str, Any]] = None,
    sys_path: Sequence[str] = (),
    backtest: bool = True,
) -> List[CheckResult]:
    """Run every applicable check on one asset config (a path or a mapping). Never raises for a
    config problem: each failure becomes a FAIL row."""
    for p in reversed(list(sys_path)):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))

    from pricebt.assets import load_asset
    from pricebt.errors import AssetEvaluationError
    from pricebt.session import PricebtSession

    results: List[CheckResult] = []
    try:
        cfg = load_asset(config)
    except Exception as exc:
        return [CheckResult("config_loads", FAIL, f"{type(exc).__name__}: {exc}")]
    results.append(CheckResult("config_loads", PASS, f"asset {cfg.name}: instrument {cfg.instrument}, {len(cfg.functions)} functions, measures {sorted(cfg.risk_measures)}"))

    d1, d2, start, end = _default_dates(dates, start, end)
    with PricebtSession(assets=[cfg], fx=fx) as session:
        service = session.pricing
        asset = session.registry[cfg.name]
        try:
            inst = _instrument(cfg, dict(kwargs or {}))
        except Exception as exc:
            return results + [CheckResult("instrument_builds", FAIL, f"{type(exc).__name__}: {exc}")]
        eff = {**cfg.defaults, **{k: v for k, v in inst.kwargs.items() if v is not None}}
        ctx = _Ctx(cfg, session, service, asset, inst, eff, d1, d2, start, end, fx is not None)
        _install_eval_timer(ctx)

        # imports/code run on the first evaluation, which is the d1 market
        try:
            ok = [service.has_market(asset, d, None) for d in (d1, d2)]
        except AssetEvaluationError as exc:
            if exc.key in ("imports", "code"):
                results.append(CheckResult("imports_execute", FAIL, str(exc)))
            else:
                results.append(CheckResult("imports_execute", PASS, "imports/code ran"))
                results.append(CheckResult("market_available", FAIL, str(exc)))
            return results + [CheckResult("remaining", SKIP, "every later check needs a market")]
        results.append(CheckResult("imports_execute", PASS, "imports/code ran"))
        if not all(ok):
            missing = [str(d) for d, o in zip((d1, d2), ok) if not o]
            return results + [CheckResult("market_available", FAIL, f"market returned None on business day(s) {missing}"), CheckResult("remaining", SKIP, "every later check needs a market")]
        results.append(CheckResult("market_available", PASS, f"market on {d1} and {d2}"))

        try:
            ctx.r1 = service.resolve(inst, d1, None)
        except Exception as exc:
            return results + [CheckResult("resolve_pins_terms", FAIL, f"resolve failed on {d1}: {type(exc).__name__}: {exc}"), CheckResult("remaining", SKIP, "every later check needs a resolved trade")]

        checks: List[tuple] = [
            ("match", check_match),
            ("market_weekend", check_market_weekend),
            ("resolve_pins_terms", check_resolve),
            ("functions_finite", check_functions),
            ("quantity_scaling", check_quantity_scaling),
            ("notional_linearity", check_notional_linearity),
            ("risk_measures", check_risk_measures),
            ("measure_series", check_measure_series),
        ]
        if backtest:
            checks.append(("smoke_backtest", check_smoke_backtest))
        checks.append(("fx_round_trip", check_fx))
        if cfg.instrument == "IRSwap":
            checks.append(("swap_pack", swap_pack))
        checks.append(("performance", check_performance))

        for name, fn in checks:
            try:
                results.extend(fn(ctx))
            except Exception as exc:  # a config (or checker) error becomes a row, never a traceback
                results.append(CheckResult(name, FAIL, f"{type(exc).__name__}: {exc}"))
    return results


def to_markdown(results: Sequence[CheckResult]) -> str:
    lines = ["| check | status | detail |", "|---|---|---|"]
    lines += [f"| {r.name} | {r.status} | {r.detail.replace('|', '/')} |" for r in results]
    counts = {s: sum(r.status == s for r in results) for s in (PASS, WARN, FAIL, SKIP)}
    lines.append("")
    lines.append(" ".join(f"{k}={v}" for k, v in counts.items()))
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Check one pricebt asset config end to end.")
    ap.add_argument("config", help="asset config YAML")
    ap.add_argument("--fx", help="FX config YAML (enables fx_round_trip for a non-USD asset)")
    ap.add_argument("--date", action="append", default=[], type=date.fromisoformat, help="sample business date; give twice for d1 and d2")
    ap.add_argument("--start", type=date.fromisoformat, help="smoke backtest start (default d1)")
    ap.add_argument("--end", type=date.fromisoformat, help="smoke backtest end (default start + 3 months)")
    ap.add_argument("--kwargs", default="{}", help="instrument kwargs as JSON, e.g. '{\"termination_date\": \"5y\"}'")
    ap.add_argument("--sys-path", action="append", default=[], help="directory to prepend to sys.path (the pricing library / helper modules)")
    ap.add_argument("--no-backtest", action="store_true", help="skip the smoke backtest")
    ap.add_argument("--json", help="also write the results as JSON to this file")
    a = ap.parse_args(argv)
    results = run_checks(a.config, fx=a.fx, dates=a.date, start=a.start, end=a.end, kwargs=json.loads(a.kwargs), sys_path=a.sys_path, backtest=not a.no_backtest)
    print(to_markdown(results))
    if a.json:
        Path(a.json).write_text(json.dumps([asdict(r) for r in results], indent=2), encoding="utf-8")
    return 1 if any(r.status == FAIL for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
