"""A direct rateslib reference for the QuantLib tie-out tests: only the public `rateslib` API (no pricebt adapter), given EXACTLY the same holiday set and fixings as the
QuantLib side. Importing this module without rateslib raises ImportError (tests importorskip it)."""
from __future__ import annotations

import contextlib
import datetime as dt
import io
import os
import warnings
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Sequence, Tuple

os.environ.setdefault("MPLBACKEND", "Agg")
warnings.filterwarnings("ignore", message=r"(?s).*Rateslib is source-available.*")
import pandas as pd
import rateslib as rl  # noqa: E402

FIX_ID = "PBTQLTEST_SOFR"
FIX_KEY = f"{FIX_ID}_1B"


def dtm(d: dt.date) -> dt.datetime:
    return dt.datetime(d.year, d.month, d.day)


def cal_of(holidays: Iterable[dt.date]) -> Any:
    return rl.Cal(holidays=[dtm(h) for h in holidays], week_mask=[5, 6])


def curve_of(dates: Sequence[dt.date], dfs: Sequence[float], cal: Any, cid: str = "c") -> Any:
    return rl.Curve(nodes={dtm(d): float(v) for d, v in zip(dates, dfs)}, id=cid, convention="act360", calendar=cal, modifier="MF", interpolation="log_linear")


@contextlib.contextmanager
def fixings(series_percent: Mapping[dt.date, float]) -> Iterator[None]:
    """rateslib's process-global fixings store: install for the block, remove after (percent values, naive midnight index)."""
    s = pd.Series(list(series_percent.values()), index=pd.DatetimeIndex([dtm(d) for d in series_percent]))
    if FIX_KEY in rl.fixings.loader.loaded:
        rl.fixings.pop(FIX_KEY)
    rl.fixings.add(FIX_KEY, s)
    try:
        yield
    finally:
        if FIX_KEY in rl.fixings.loader.loaded:
            rl.fixings.pop(FIX_KEY)


def irs(effective: dt.date, termination: Any, sign: int, notional: float, rate_percent: float, cal: Any, started: bool = False, **spec_kw: Any) -> Any:
    """rateslib `usd_irs` swap; `sign` +1 = payer (positive notional = pay fixed in rateslib). `termination`: a tenor string or a date."""
    kw: Dict[str, Any] = dict(spec="usd_irs", calendar=cal, fixed_rate=rate_percent, notional=sign * notional, **spec_kw)
    if started:
        kw["leg2_rate_fixings"] = FIX_ID
    t = termination if isinstance(termination, str) else dtm(termination)
    return rl.IRS(effective=dtm(effective), termination=t.lower() if isinstance(t, str) else t, **kw)


def par_curve(asof: dt.datetime, short_bp: float = 0.0, long_bp: float = 0.0, cid: str = "sofr") -> Any:
    """Solver-fitted SOFR curve from 12 par swaps on rateslib's own `nyc` calendar (the construction behind the golden numbers), returned as a float curve."""
    par_in = {"1M": 4.32, "3M": 4.30, "6M": 4.22, "1Y": 4.05, "2Y": 3.90, "3Y": 3.88, "5Y": 3.92, "7Y": 4.00, "10Y": 4.08, "15Y": 4.15, "20Y": 4.12, "30Y": 3.98}
    tenors = list(par_in)
    par = {t: par_in[t] + (short_bp + (long_bp - short_bp) * i / (len(tenors) - 1)) / 100.0 for i, t in enumerate(tenors)}
    spot = rl.get_calendar("nyc").add_bus_days(asof, 2, True)
    insts = {t: rl.IRS(effective=spot, termination=t.lower(), spec="usd_irs", curves=cid, fixed_rate=r) for t, r in par.items()}
    mats = {t: i.leg1.schedule.termination for t, i in insts.items()}
    order = sorted(insts, key=mats.get)
    crv = rl.Curve(nodes={asof: 1.0, **{mats[t]: 1.0 for t in order}}, id=cid, convention="act360", calendar="nyc", modifier="MF", interpolation="log_linear")
    with contextlib.redirect_stdout(io.StringIO()):
        sv = rl.Solver(curves=[crv], instruments=[insts[t] for t in order], s=[par[t] for t in order], id=cid, func_tol=1e-12, conv_tol=1e-12)
    assert sv.result["status"] == "SUCCESS"
    return rl.Curve(nodes={k: float(v) for k, v in crv.nodes.nodes.items()}, id=cid, convention="act360", calendar="nyc", modifier="MF", interpolation="log_linear")


def nyc_weekday_holidays() -> List[dt.date]:
    """rateslib's built-in `nyc` holidays (weekdays only): the full-horizon set the golden numbers were computed with."""
    return [h.date() for h in rl.get_calendar("nyc").holidays if h.weekday() < 5]


# ------------------------------------------------------------------------------ the par-swap ladder, exactly the algorithm of the shipped rateslib adapter
def build_ladder(dates: Sequence[dt.date], dfs: Sequence[float], cal: Any, tenors: Sequence[str], lag: int = 2, cid: str = "dense", shift_bp: float = 0.0, tol: Tuple[float, float] = (1e-9, 1e-11)) -> Tuple[Any, Any]:
    dense = curve_of(dates, dfs, cal, cid)
    rcal = rl.get_calendar(cal) if isinstance(cal, str) else cal
    eff = rcal.add_bus_days(dtm(dates[0]), lag, True)
    rid = f"{cid}-RISK"
    nodes = {dtm(dates[0]): 1.0}
    insts, pars = [], []
    for t in tenors:
        term = rl.IRS(effective=eff, termination=t.lower(), spec="usd_irs", notional=1e6, calendar=cal).leg1.schedule.termination
        par = float(rl.IRS(effective=eff, termination=term, spec="usd_irs", notional=1e6, calendar=cal).rate(curves=dense))
        nodes[term] = float(dense[term])
        pars.append(par)
        insts.append(rl.IRS(effective=eff, termination=term, spec="usd_irs", fixed_rate=par, notional=1e6, curves=rid, calendar=cal))
    curve = rl.Curve(nodes=nodes, convention="act360", calendar=cal, modifier="MF", interpolation="log_linear", id=rid)
    with contextlib.redirect_stdout(io.StringIO()):
        solver = rl.Solver(curves=[curve], instruments=insts, s=[x + shift_bp / 100.0 for x in pars], instrument_labels=list(tenors), id=rid, func_tol=tol[0], conv_tol=tol[1])
    assert solver.result["status"] == "SUCCESS", solver.result
    return curve, solver


def ladder_of(swap: Any, curve: Any, solver: Any, tenors: Sequence[str]) -> Dict[str, float]:
    d = swap.delta(curves=curve, solver=solver)
    block = d.xs("instruments", level=0) if "instruments" in set(d.index.get_level_values(0)) else d
    s = block.iloc[:, 0].astype(float)
    s.index = s.index.get_level_values(-1)
    s = s.reindex(list(tenors)).fillna(0.0)
    return {t: float(s[t]) for t in tenors}


def bond(issue: dt.date, maturity: dt.date, coupon_pct: float, cal: Any, notional: float = -1e6, spec: str = "us_gb_tsy") -> Any:
    """rateslib FixedRateBond, pricebt's overrides (ex_div=0); rateslib notional < 0 = long."""
    return rl.FixedRateBond(effective=dtm(issue), termination=dtm(maturity), spec=spec, fixed_rate=coupon_pct, notional=notional, ex_div=0, calendar=cal)


def parallel_gamma(dates: Sequence[dt.date], dfs: Sequence[float], cal: Any, tenors: Sequence[str], position: Any, h_bp: float = 1.0) -> float:
    """Second difference of the position's PV for a parallel +-h_bp move of ALL pillar par rates of the par-swap risk curve, per bp^2 (par-rate space, like the QuantLib `gamma`)."""
    def pv(shift: float) -> float:
        curve, _ = build_ladder(dates, dfs, cal, tenors, shift_bp=shift, tol=(1e-20, 1e-20))
        return float(position.npv(curves=curve))

    return (pv(h_bp) + pv(-h_bp) - 2 * pv(0.0)) / h_bp**2
