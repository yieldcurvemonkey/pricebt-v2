"""rateslib par-swap ladder with a caller-supplied holiday calendar. Same algorithm as src/pricebt/contrib/rateslib/ladder.py build_par_swap_ladder
(validated against it in p1_ladder_control.py with calendar='nyc')."""
import contextlib
import io

from common import *  # noqa


def rl_build_ladder(nodes, cal, tenors, cid="dense"):
    """nodes: [(date, df)]. cal: a rateslib Cal or the name 'nyc'. Returns (risk_curve, solver, dense, pars, terms)."""
    ref = nodes[0][0]
    dense = rl.Curve(nodes={fdt(d): v for d, v in nodes}, id=cid, convention="act360", calendar=cal, modifier="MF", interpolation="log_linear")
    rcal = rl.get_calendar(cal) if isinstance(cal, str) else cal
    eff = rcal.add_bus_days(fdt(ref), 2, True)
    rid = f"{cid}-RISK"
    rn = {fdt(ref): 1.0}
    insts, pars, terms = [], [], []
    for t in tenors:
        term = rl.IRS(effective=eff, termination=t.lower(), spec="usd_irs", notional=1e6, calendar=cal).leg1.schedule.termination
        par = float(rl.IRS(effective=eff, termination=term, spec="usd_irs", notional=1e6, calendar=cal).rate(curves=dense))
        rn[term] = float(dense[term])
        pars.append(par)
        terms.append(term)
        insts.append(rl.IRS(effective=eff, termination=term, spec="usd_irs", fixed_rate=par, notional=1e6, curves=rid, calendar=cal))
    curve = rl.Curve(nodes=rn, convention="act360", calendar=cal, modifier="MF", interpolation="log_linear", id=rid)
    with contextlib.redirect_stdout(io.StringIO()):
        solver = rl.Solver(curves=[curve], instruments=insts, s=pars, instrument_labels=list(tenors), id=rid, func_tol=1e-9, conv_tol=1e-11)
    assert solver.result["status"] == "SUCCESS", solver.result
    return curve, solver, dense, pars, terms


def rl_ladder_of(irs, curve, solver, tenors):
    d = irs.delta(curves=curve, solver=solver)
    block = d.xs("instruments", level=0) if "instruments" in set(d.index.get_level_values(0)) else d
    s = block.iloc[:, 0].astype(float)
    s.index = s.index.get_level_values(-1)
    return s.reindex(list(tenors)).fillna(0.0)
