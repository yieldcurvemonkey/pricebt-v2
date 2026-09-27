"""Is QuantLib's `unexplained` exactly (1 - _H**2) times the reference's? (_H = the directional-derivative step of the QuantLib swap adapter.)

Runs the suite swap config under the reference and QuantLib stacks and prints the ratio of the per-unit `unexplained` rows, aligned by (position, ts).
Known-answer first: the same ratio for reference-vs-reference must be exactly 1.
"""
import sys

sys.path.insert(0, "tests")
import pricebt.contrib.quantlib  # noqa
qlswap = sys.modules["pricebt.contrib.quantlib.swap"]
from pricebt.tieout import run_stack

CFG = "configs/suite/s1_swap_carry_eod.yaml"
SETS = ["backtest.grid.start=2024-01-02", "backtest.grid.end=2024-03-29", "backtest.progress.show=false"]
OV = {"reference": "configs/adapters/refstack_swap.yaml", "quantlib": "configs/adapters/quantlib_swap.yaml"}


def layers(name):
    _, res = run_stack(CFG, [OV[name]], sets=SETS, audit_measures=("dv01", "gamma"))
    return res.record.audit["layers"]


def main():
    print("_H =", qlswap._H, " 1 - _H**2 =", 1 - qlswap._H ** 2)
    ref, ql = layers("reference"), layers("quantlib")
    ref2 = layers("reference")
    for label, a, b in (("reference vs reference (known answer: ratio 1)", ref, ref2), ("quantlib / reference", ql, ref)):
        m = a[a.layer == "unexplained"].merge(b[b.layer == "unexplained"], on=["position", "ts"], suffixes=("_a", "_b"))
        m = m[m.unit_b.abs() > 1e-3]
        r = m.unit_a / m.unit_b
        print(f"{label}: n={len(r)} ratio min {r.min():.6f} max {r.max():.6f} mean {r.mean():.6f}")


if __name__ == "__main__":
    main()
