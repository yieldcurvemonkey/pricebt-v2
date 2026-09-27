"""p9_pillar_gap.py: the numbers of the pillar-set caveat (README section 6, and `pricebt-layers-and-ladder`), reproduced on ONE market: the base config's synthetic market (seed 7, two holidays) at 2024-05-24.
zeta's risk curve has twelve pillars (1M ... 30Y), the reference stack's default eleven (no 1M). Run from the repository root:
    $env:PYTHONPATH = "src;tests;skills/pricebt-wire-external-library/example-service"; python skills/pricebt-wire-external-library/example-service/probes/p9_pillar_gap.py     (about 10 s)
Prints (1) the dv01 relative difference between the reference on its default eleven and on zeta's twelve, for 20mm payers struck 3.9 of different ages (zeta agrees with the reference on twelve to round-off: README section 7);
(2) what folding or dropping the 1M bucket leaves on the seasoned 2Y over the 19 grid days; (3) the tie-out against the DEFAULT reference with the ladder audited (the CLI cannot). Exit 0 = all three match the README."""
import datetime as dt
import sys
from pathlib import Path

import pandas as pd
import yaml

from pricebt.pricable import MarkContext
from pricebt.testing import refstack as R
from pricebt.testing.synthetic import SyntheticMarket
from pricebt.tieout import run_tieout
from pricebt.timeutil import Calendar

EXAMPLE = Path(__file__).resolve().parents[1]  # example-service/
REPO = EXAMPLE.parents[2]  # the repository root: the shipped reference overlay lives under configs/
CONFIG = EXAMPLE / "zeta_adapter" / "config"
BASE = CONFIG / "zeta_tieout_base.yaml"
CONV = dict(yaml.safe_load(BASE.read_text(encoding="utf8"))["instruments"]["usd_sofr_ois"]["conventions"])
HOLIDAYS = [dt.date(2024, 5, 27), dt.date(2024, 6, 19)]
CAL = Calendar(HOLIDAYS)
MDP = SyntheticMarket("2024-01-02", "2024-12-31", calendar=CAL, seed=7)  # what the base config's `market` builds
ZETA = ["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y", "20Y", "30Y"]
PAYER = {"side": "pay", "direction": 1, "notional": 2e7, "fixed_rate": 3.9}  # 20mm payers struck 3.9, as the base's seasoned 2Y
SWAPS = (  # (label, effective, maturity): all started at 2024-05-24
    ("seasoned 2Y (P000003)", dt.date(2023, 6, 12), "2Y"),
    ("6 months left (a 1Y swap)", dt.date(2023, 11, 27), dt.date(2024, 11, 27)),
    ("1 month left (a 1Y swap)", dt.date(2023, 6, 26), dt.date(2024, 6, 24)),
    ("2 weeks left (a 1Y swap)", dt.date(2023, 6, 7), dt.date(2024, 6, 7)),
)


def swap_at(day, eff, mat):
    p = R.wrap(MDP.get_pricer(pd.Timestamp(f"{day} 17:00", tz="America/New_York")))
    sw = R.swap_factory(p, p.ts, terms={**PAYER, "effective": eff, "maturity": mat}, conventions=CONV).obj
    return sw, MarkContext(p.ts, p, {"primary": p}, None, None, p.ts, p, {}, {}), p


bad = 0
print("1. dv01, the reference on its default eleven pillars against the same reference on zeta's twelve (2024-05-24)")
rel = {}
for label, eff, mat in SWAPS:
    sw, c, _ = swap_at(dt.date(2024, 5, 24), eff, mat)
    d11, d12 = sw.dv01(ctx=c), sw.dv01(ctx=c, tenors=ZETA)
    l11, l12 = sw.delta_ladder(ctx=c), sw.delta_ladder(ctx=c, tenors=ZETA)
    rel[label] = abs(d11 - d12) / abs(d12)
    print(f"   {label:<28} relative difference {rel[label]:.3e}   3M bucket {l11['3M']:9.3f} on 11 pillars, {l12['3M']:9.3f} on 12 (the 1M bucket: {l12['1M']:.3f})")
ok = 1.0e-4 < rel["seasoned 2Y (P000003)"] < 3e-4 and all(rel[k] > 1e-4 for k in rel)  # the pin of the zeta proofs test: 1.0e-4 < max_rel < 3e-4 on P000003, and the shorter swaps are further off
bad += not ok
print("   " + ("ok " if ok else "BAD") + " the seasoned 2Y sits in the pinned band (1e-4, 3e-4), every shorter swap is above 1e-4")

print("2. the seasoned 2Y over the 19 grid days: the largest bucket difference against the reference's eleven, when the 12-pillar ladder is folded (1M into 3M) or the 1M bucket dropped")
folds, drops, day = [], [], dt.date(2024, 5, 24)
while day <= dt.date(2024, 6, 21):
    if CAL.is_business_day(day):
        sw, c, _ = swap_at(day, SWAPS[0][1], SWAPS[0][2])
        l11, l12 = sw.delta_ladder(ctx=c), sw.delta_ladder(ctx=c, tenors=ZETA)
        folded = {k: l12[k] + (l12["1M"] if k == "3M" else 0.0) for k in l11}
        folds.append(max(abs(folded[k] - l11[k]) for k in l11))
        drops.append(max(abs(l12[k] - l11[k]) for k in l11))
    day += dt.timedelta(days=1)
print(f"   {len(folds)} days; fold: {min(folds):.2f} to {max(folds):.2f} currency (first day {folds[0]:.2f}), the ladder's absolute floor is 0.1; drop: {min(drops):.1f} to {max(drops):.1f} (first day {drops[0]:.1f}), and sum(ladder) no longer equals dv01")
ok = len(folds) == 19 and 0.2 < folds[0] < 0.22 and folds[0] > 0.1 > min(folds) and 31 < drops[0] < 32
bad += not ok
print("   " + ("ok " if ok else "BAD") + " fold 0.21 on the first day, dropping 31.6")

print("3. the tie-out against the DEFAULT reference (one overlay), the ladder audited: shipped tolerances, nothing declared")
res = run_tieout(BASE, {"reference": [REPO / "configs" / "adapters" / "refstack_swap.yaml"], "zeta": [CONFIG / "zeta_swap.yaml"]}, audit_measures=("dv01", "gamma", "rate", "delta_ladder"))
fr = res.reports["reference_vs_zeta"].frame().set_index("quantity")
moved = sorted(q for q, r in fr.iterrows() if r["status"] not in ("exact", "noise"))
for q in ("delta_ladder.1M", "delta_ladder.3M"):
    print(f"   {q:<18} {fr.loc[q, 'status']:<10} max_abs {fr.loc[q, 'max_abs']:.2f}  max_rel {fr.loc[q, 'max_rel']:.2f}")
print("   rows that are neither exact nor noise:", moved)
ok = (not res.passed and fr.loc["delta_ladder.1M", "status"] == "structure" and fr.loc["delta_ladder.3M", "status"] == "exceeds" and 0.3 < fr.loc["delta_ladder.3M", "max_rel"] < 0.33
      and moved == sorted(["delta_ladder.1M", "delta_ladder.3M", "dv01", "tay_delta", "tay_delta/unit", "tay_unexplained", "tay_unexplained/unit"]))
bad += not ok
print("   " + ("ok " if ok else "BAD") + " 1M is `structure` (the reference has no such bucket), 3M `exceeds` at 0.32, dv01 and what reads it follow, nothing else moves")
sys.exit(1 if bad else 0)
