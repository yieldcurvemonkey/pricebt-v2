"""Phase 1 item 6b: yield from clean (incl. market convention on coupon dates), duration/convexity/dv01, payment dates, on real reference data."""
import sys
sys.path.insert(0, "tools/ql_evidence")
from p1_bond import *

HOL = load_cal("nyc")
QCAL = ql_calendar(HOL)
RCAL = rl.Cal(holidays=[fdt(h) for h in HOL], week_mask=[5, 6])


def make_bond_pay(issue, mat, cpn, cal=QCAL):
    return make_bond(issue, mat, cpn)  # UNADJUSTED payment dates: QuantLib's yield math discounts at cf.date()


rng = np.random.default_rng(3)
ROWS = []
idx = rng.permutation(len(REF))
stats = dict(pay_dates_bad=0, bonds=0, points=0, cpn_pts=0)
W = {k: 0.0 for k in ("ql_roundtrip_y", "rl_roundtrip_y", "y_from_clean_off", "y_from_clean_on", "risk", "modified", "convexity", "ql_analytic_modified", "rl_analytic_vs_rl_fd_risk", "rl_analytic_vs_rl_fd_cvx", "ql_fd_vs_rl_fd_risk", "ql_fd_vs_rl_fd_cvx", "dirty", "flows")}
for i in idx:
    r = REF.iloc[i]
    issue, mat, cpn = r.issue_date, r.maturity_date, float(r.cpn)
    if issue < dt.date(2010, 1, 5) or mat > dt.date(2060, 1, 1) or cpn <= 0.0 or mat < dt.date(2011, 1, 1):
        continue
    rb = rl.FixedRateBond(effective=fdt(issue), termination=fdt(mat), spec="us_gb_tsy", fixed_rate=cpn, notional=-1e6, ex_div=0, calendar=RCAL)
    qb, dc = make_bond_pay(issue, mat, cpn)
    stats["bonds"] += 1
    rf = [(p.settlement_params.payment.date(), float(p.cashflow()) / 1e6 * 100) for p in rb.leg1.periods]
    qf = [(pd_(QCAL.adjust(c.date(), ql.Following)), c.amount()) for c in qb.cashflows()]  # payment dates: Following on the holiday set, applied OUTSIDE the yield math
    if [a[0] for a in rf] != [b[0] for b in qf]:
        stats["pay_dates_bad"] += 1
    W["flows"] = max(W["flows"], max(abs(a[1] - b[1]) for a, b in zip(rf, qf)))
    ends = {p.period_params.end.date() for p in rb.leg1.periods if hasattr(p, "period_params")}
    cds = [d for d, _ in qf][:-1]
    life = (mat - max(issue, dt.date(2010, 1, 5))).days
    gen = [max(issue, dt.date(2010, 1, 5)) + dt.timedelta(days=int(k)) for k in rng.integers(1, max(2, life - 1), 4)]
    spec_dates = []
    for d in cds[1:5]:
        spec_dates += [d - dt.timedelta(days=1), d, d + dt.timedelta(days=1)]
    for s in sorted(set(gen + spec_dates)):
        if not (issue < s < mat) or not RCAL.is_bus_day(fdt(s)):
            continue
        on = s in ends
        for y in (0.7, 3.1, 4.6, 6.2):
            try:
                r_dirty = float(rb.price(y, fdt(s), dirty=True))
                r_risk = float(rb.duration(y, fdt(s), "risk"))
                r_mod = float(rb.duration(y, fdt(s), "modified"))
                r_cvx = float(rb.convexity(y, fdt(s), "risk"))
            except Exception as e:
                continue
            stats["points"] += 1
            with ql_settings(s):
                q_dirty = dirty_from_yield(qb, dc, y, s)
                acc = qb.accruedAmount(qd(s))
                q_clean = qb.cleanPrice(y / 100.0, dc, ql.SimpleThenCompounded, ql.Semiannual, qd(s))
                h = 0.005
                p_up, p_dn = dirty_from_yield(qb, dc, y + h, s), dirty_from_yield(qb, dc, y - h, s)
                q_risk = -(p_up - p_dn) / (2 * h)
                q_mod = q_risk / q_dirty * 100.0
                hc = 0.01
                q_cvx = (dirty_from_yield(qb, dc, y + hc, s) - 2 * q_dirty + dirty_from_yield(qb, dc, y - hc, s)) / hc**2
                ir = ql.InterestRate(y / 100.0, dc, ql.SimpleThenCompounded, ql.Semiannual)
                q_amod = ql.BondFunctions.duration(qb, ir, ql.Duration.Modified, qd(s))
                q_arisk = q_amod * q_dirty / 100.0 * 100.0  # modified * dirty per 100 per 1.00 -> per 1% = /100 then *100 (risk is per 1%)
                y_clean = yield_from_clean(qb, dc, q_clean, s)
            # rateslib yield from the (market) clean price: on a coupon date accrued is 0 (market) so clean == dirty and the price is solved as dirty
            r_y = float(rb.ytm(q_clean, fdt(s), dirty=on))
            W["y_from_clean_on" if on else "y_from_clean_off"] = max(W["y_from_clean_on" if on else "y_from_clean_off"], abs(r_y - y_clean))
            W["ql_roundtrip_y"] = max(W["ql_roundtrip_y"], abs(y_clean - y))
            W["rl_roundtrip_y"] = max(W["rl_roundtrip_y"], abs(r_y - y))
            W["dirty"] = max(W["dirty"], abs(r_dirty - q_dirty))
            W["risk"] = max(W["risk"], abs(r_risk - q_risk) / abs(r_risk))
            W["modified"] = max(W["modified"], abs(r_mod - q_mod) / abs(r_mod))
            W["convexity"] = max(W["convexity"], abs(r_cvx - q_cvx) / max(abs(r_cvx), 1.0))
            rp = lambda yy: float(rb.price(yy, fdt(s), dirty=True))
            r_fd_risk = -(rp(y + h) - rp(y - h)) / (2 * h)
            r_fd_cvx = (rp(y + hc) - 2 * rp(y) + rp(y - hc)) / hc**2
            W["rl_analytic_vs_rl_fd_risk"] = max(W["rl_analytic_vs_rl_fd_risk"], abs(r_risk - r_fd_risk) / abs(r_risk))
            W["rl_analytic_vs_rl_fd_cvx"] = max(W["rl_analytic_vs_rl_fd_cvx"], abs(r_cvx - r_fd_cvx) / max(abs(r_cvx), 1.0))
            W["ql_fd_vs_rl_fd_risk"] = max(W["ql_fd_vs_rl_fd_risk"], abs(q_risk - r_fd_risk) / abs(r_fd_risk))
            W["ql_fd_vs_rl_fd_cvx"] = max(W["ql_fd_vs_rl_fd_cvx"], abs(q_cvx - r_fd_cvx) / max(abs(r_fd_cvx), 1.0))
            ROWS.append((abs(q_cvx - r_fd_cvx) / max(abs(r_fd_cvx), 1.0), r["cusip"], issue, mat, cpn, s, y, on, q_cvx, r_fd_cvx, r_cvx))
            W["ql_analytic_modified"] = max(W["ql_analytic_modified"], abs(r_mod - q_amod) / abs(r_mod))
    if stats["bonds"] >= int(sys.argv[1] if len(sys.argv) > 1 else 40):
        break
print(stats)
print({k: f"{v:.2e}" for k, v in W.items()})

ROWS.sort(reverse=True)
for row in ROWS[:6]:
    print(row)
