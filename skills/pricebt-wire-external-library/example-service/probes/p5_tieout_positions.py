"""p5_tieout_positions.py: the tie-out through the Python API with the LADDER audited (the CLI cannot), reference on zeta's twelve pillars. Run from the repository root:
    $env:PYTHONPATH = "src;tests;skills/pricebt-wire-external-library/example-service"; python skills/pricebt-wire-external-library/example-service/probes/p5_tieout_positions.py
Prints the verdict, the self-test, every non-exact row, and the ladder rows. Exit 0 = the tie-out passed."""
import sys
from pathlib import Path

from pricebt.tieout import run_tieout

EXAMPLE = Path(__file__).resolve().parents[1]  # example-service/
REPO = EXAMPLE.parents[2]  # the repository root: the shipped reference overlay lives under configs/
CONFIG = EXAMPLE / "zeta_adapter" / "config"
REF = [REPO / "configs" / "adapters" / "refstack_swap.yaml", CONFIG / "refstack_zeta_pillars.yaml"]
res = run_tieout(CONFIG / "zeta_tieout_base.yaml", {"reference": REF, "zeta": [CONFIG / "zeta_swap.yaml"]}, audit_measures=("dv01", "gamma", "rate", "delta_ladder"))
rep = res.reports["reference_vs_zeta"]
print("selftest:", res.header["selftest"], "| passed:", res.passed, "| failures:", [(r.level, r.quantity, r.status) for r in rep.failures()], "| n_positions:", rep.header["n_positions"])
fr = rep.frame()
print(fr[fr["quantity"].str.startswith("delta_ladder")][["level", "quantity", "n", "max_abs", "max_rel", "tol_rel", "tol_floor", "status"]].to_string())
print("errors:", {k: len(v.errors) for k, v in res.results.items()})
sys.exit(0 if res.passed else 1)
