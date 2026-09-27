"""Control for the probe rateslib ladder: with calendar='nyc' it must equal the ORIGINAL adapter (src/pricebt/contrib/rateslib/ladder.py) exactly."""
import sys

sys.path.insert(0, "tools/ql_evidence")
from rlladder import *  # noqa
from pricebt.contrib.rateslib.pricer import RLCurvePricer
from pricebt.contrib.rateslib.ladder import build_par_swap_ladder, DEFAULT_TENORS
from pricebt.contrib.rateslib.swap import RLSwap

ref = dt.date(2024, 6, 12)
row = load_row(ref)
nodes = list(zip(*row))
tenors = list(DEFAULT_TENORS)
pr = RLCurvePricer.from_nodes(pd.Timestamp("2024-06-12 17:00", tz="America/New_York"), {d: v for d, v in nodes})
lm = build_par_swap_ladder(pr, tenors)
curve, solver, dense, pars, terms = rl_build_ladder(nodes, "nyc", tenors)
worst = 0.0
for t, side, rate in [("5Y", "pay", 4.0), ("10Y", "receive", 3.5), ("7Y", "pay", 4.4)]:
    sw = RLSwap.build(pr, pr.ts, tenor=t, side=side, notional=1e7, fixed_rate=rate, risk_model={"ladder": build_par_swap_ladder, "tenors": tenors})
    old = lm.delta(sw._inst_fwd)
    irs = rl.IRS(effective=fdt(sw.effective), termination=fdt(sw.termination), spec="usd_irs", notional=(1 if side == "pay" else -1) * 1e7, fixed_rate=rate, calendar="nyc")
    new = rl_ladder_of(irs, curve, solver, tenors)
    worst = max(worst, float((old - new).abs().max()))
    print(t, side, "old sum", round(float(old.sum()), 4), "new sum", round(float(new.sum()), 4))
print("max abs diff probe vs original adapter ladder:", worst)
assert worst < 1e-8
# non-vacuity: a different tenor set must give a different ladder
c2, s2, *_ = rl_build_ladder(nodes, "nyc", ["2Y", "5Y", "10Y", "30Y"])
assert abs(float(rl_ladder_of(irs, c2, s2, ["2Y", "5Y", "10Y", "30Y"]).sum()) - float(new.sum())) > 1e-3
print("control ok")
