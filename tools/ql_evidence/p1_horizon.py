"""What does the fixture calendar horizon (holidays end 2035-12-25) cost? rateslib built-in 'nyc' (to 2200) vs the JSON holiday set, both in rateslib."""
import sys
sys.path.insert(0, "tools/ql_evidence")
from qlswap import *
hol = load_cal("nyc"); rj = rl_calendar(hol)
refs = sample_dates(dt.date(2018, 4, 2), dt.date(2026, 9, 4), n=25, seed=9)
w = {}
for ref in refs:
    row = load_row(ref); nodes = list(zip(*row))
    for cname, cal_ in (("nyc-builtin", "nyc"), ("json", rj)):
        pass
    spot = rl.get_calendar("nyc").add_bus_days(fdt(ref), 2, True)
    for t in ("10Y", "15Y", "20Y", "30Y"):
        res = []
        for cal_ in ("nyc", rj):
            rc = rl.Curve(nodes={fdt(d): v for d, v in nodes}, id="c", convention="act360", calendar=cal_, modifier="MF", interpolation="log_linear")
            irs = rl.IRS(effective=spot, termination=t.lower(), spec="usd_irs", notional=1e7, fixed_rate=4.0, calendar=cal_)
            res.append((float(irs.npv(curves=rc)), irs.leg1.schedule.termination))
        d = res[0][0] - res[1][0]
        w.setdefault(t, []).append((abs(d), res[0][1] != res[1][1]))
for t, v in w.items():
    print(t, "max |PV diff| per 10mm payer:", f"{max(x[0] for x in v):.2f}", "; cases where the termination date differs:", sum(x[1] for x in v), "of", len(v))
