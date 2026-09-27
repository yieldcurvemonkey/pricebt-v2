"""Layer prototype: reproduce the rateslib golden decomposition (seasoned 3y payer 50mm) with QuantLib bump-and-revalue along the realised move.
GOLD: carry -561.82, roll(folded) 1532.07, delta(folded) 70302.16, convexity -50.23, V0 -369827.43, V1 -212114.86, cash -86490.36, total 71222.20."""
import sys

sys.path.insert(0, "tools/ql_evidence")
sys.path.insert(0, "tests")
from qlswap import *  # noqa
from test_rl_common import par_curve, seeded_fixings  # noqa

D = dt.datetime
hol = load_cal("nyc")
cal = ql_calendar(hol)


def nodes_of(rc):
    return [(d.date(), float(v)) for d, v in rc.nodes.nodes.items()]


c0, c1 = par_curve(D(2025, 1, 2), 0, 0, "c0"), par_curve(D(2025, 1, 16), 3, 15, "c1")
fx = seeded_fixings()
FXD = {d.date(): float(v) / 100.0 for d, v in fx.items()}
t0, t1 = dt.date(2025, 1, 2), dt.date(2025, 1, 16)
N0, N1 = nodes_of(c0), nodes_of(c1)
eff, ten, sign, notional, rate = dt.date(2024, 1, 8), "3Y", 1, 50e6, 4.20


def world(nodes, eval_date, fixings_until, engine_kw=None):
    """Return (swap, handle, curve) priced in the world (curve nodes, eval date, fixings dated < fixings_until). Caller keeps refs alive within the eval-date guard."""
    curve = ql_curve(nodes)
    h = ql.RelinkableYieldTermStructureHandle(curve)
    idx = make_index(cal, h, "PBTLAYER")
    ql.IndexManager.instance().clearHistory(idx.name())
    ds = sorted(d for d in FXD if eff <= d < fixings_until)
    idx.addFixings([qd(d) for d in ds], [FXD[d] for d in ds], True)
    sw = make_ois2(eff, ten, sign, notional, rate, cal, idx)
    sw.setPricingEngine(ql.DiscountingSwapEngine(h, True, *(engine_kw or ())))
    return sw, h, curve, idx


def signed_flows(sw):
    """holder-signed (payment date, amount): payer pays fixed (negative), receives float (positive)."""
    out = []
    for c in sw.fixedLeg():
        out.append((pd_(c.date()), -c.amount() * sign))
    for c in sw.overnightLeg():
        out.append((pd_(c.date()), c.amount() * sign))
    return out


def pv_at(sw, h, when):
    """PV as of `when` of flows paid >= when (paid ON `when` included)."""
    curve = h.currentLink()
    return sw.NPV() if when is None else sum(a * curve.discount(qd(d)) / curve.discount(qd(when)) for d, a in signed_flows(sw) if d >= when)


with ql_state(t0):
    sw0, h0, cv0, i0 = world(N0, t0, t0)
    V0 = sw0.NPV()
    flows0 = signed_flows(sw0)
    df0 = lambda d: cv0.discount(qd(d))
    X_fwd = sum(a * df0(d) / df0(t1) for d, a in flows0 if d >= t1) + sum(a for d, a in flows0 if t0 <= d < t1)
    del sw0, h0, cv0, i0
print("V0", round(V0, 2), "X_fwd", round(X_fwd, 2))

with ql_state(t1):
    sw1, h1, cv1, i1 = world(N1, t1, t1)
    V1 = sw1.NPV()
    flows1 = signed_flows(sw1)
    cash = sum(a for d, a in flows1 if t0 <= d < t1)
    N = (t1 - t0).days
    roll_nodes = [(t1, 1.0)] + [(d + dt.timedelta(days=N), v) for d, v in N0 if d > t0]
    swr, hr, cvr, ir = world(roll_nodes, t1, t1)
    X_roll_act = swr.NPV() + cash
    # base curve: rolled curve evaluated at c1's node dates
    base = [(d, cvr.discount(qd(d))) for d, _ in N1]
    swb, hb, cvb, ib = world(base, t1, t1)
    V_base = swb.NPV()
    # zero-rate space at c1 node dates, excluding node 0
    tau = np.array([(d - t1).days / 365.0 for d, _ in N1[1:]])
    dfb = np.array([v for _, v in base[1:]])
    df1 = np.array([v for _, v in N1[1:]])
    dz = -np.log(df1) / tau - (-np.log(dfb) / tau)
    dates = [d for d, _ in N1]

    def V_eps(eps):
        nodes = [(dates[0], 1.0)] + [(d, float(b * np.exp(-eps * z * ta))) for d, b, z, ta in zip(dates[1:], dfb, dz, tau)]
        s, hh, cc, ii = world(nodes, t1, t1)
        return s.NPV()

    h = 0.1
    up, dn = V_eps(h), V_eps(-h)
    delta_dir = (up - dn) / (2 * h)
    cvx = (up + dn - 2 * V_base) / (2 * h * h)
    del sw1, h1, cv1, i1, swr, hr, cvr, ir, swb, hb, cvb, ib

carry = X_fwd - V0
roll = X_roll_act - X_fwd
resample = V_base + cash - X_roll_act
delta = resample + delta_dir
resid = (V1 - V_base) - delta_dir - cvx
total = V1 + cash - V0
print(f"V0 {V0:.2f} V1 {V1:.2f} cash {cash:.2f} total {total:.2f}")
print(f"carry {carry:.2f} (gold -561.82) | roll(folded) {roll:.2f} (1532.07) | delta(folded) {delta:.2f} (70302.16) | convexity {cvx:.2f} (-50.23) | residual {resid:.2f} (0.02)")
print("sum of layers + residual - total:", round(carry + roll + delta + cvx + resid - total, 6))
