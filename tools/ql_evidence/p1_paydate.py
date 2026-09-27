"""Phase 1 item 3b: payment ON the reference date. rateslib keeps a flow paid on D inside V(D); which QuantLib switch reproduces that?"""
import sys

sys.path.insert(0, "tools/ql_evidence")
from p1_started import *  # noqa

eff = dt.date(2024, 1, 8)
tenor = "3Y"
irs0 = rl.IRS(effective=fdt(eff), termination=tenor.lower(), spec="usd_irs", notional=1e7, fixed_rate=4.0, calendar=rcal)
pays = [p.settlement_params.payment.date() for p in irs0.leg1.periods]
print("payment dates:", pays[:3])
P = pays[0]  # 2025-01-10
assert rcal.is_bus_day(fdt(P))


def price_both(ref, include_flag, settings_flag):
    row = load_row(ref)
    nodes = list(zip(*row))
    rc = rl.Curve(nodes={fdt(d): v for d, v in nodes}, id="c", convention="act360", calendar=rcal, modifier="MF", interpolation="log_linear")
    rl_install(ref, eff)
    irs = rl.IRS(effective=fdt(eff), termination=tenor.lower(), spec="usd_irs", notional=1e7, fixed_rate=4.0, calendar=rcal, leg2_rate_fixings="PBT_SOFR")
    r = float(irs.npv(curves=rc))
    with ql_state(ref, include_ref=settings_flag):
        h = ql.YieldTermStructureHandle(ql_curve(nodes))
        idx = make_index(cal, h)
        ql_install(idx, ref, eff)
        sw = make_ois2(eff, tenor, 1, 1e7, 4.0, cal, idx)
        sw.setPricingEngine(ql.DiscountingSwapEngine(h, include_flag) if include_flag is not None else ql.DiscountingSwapEngine(h))
        q = sw.NPV()
    return r, q


for ref in (P - dt.timedelta(days=1), P, P + dt.timedelta(days=3)):
    if not rcal.is_bus_day(fdt(ref)):
        continue
    print("ref", ref, "(pay date %s)" % P)
    for eng, gl, label in [(True, False, "engine includeSettlementDateFlows=True, Settings.includeReferenceDateEvents=False (default)"),
                           (False, False, "engine flag False"),
                           (None, False, "engine default (uses Settings, False)"),
                           (None, True, "engine default, Settings.includeReferenceDateEvents=True")]:
        r, q = price_both(ref, eng, gl)
        print(f"   {label:95s} rl {r:14.4f} ql {q:14.4f} diff {q-r:12.4f}")
