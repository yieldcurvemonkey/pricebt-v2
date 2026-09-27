"""Loading the shipped adapters (which need their libraries) never touches ARBS (spec G1, G2)."""
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.adapter_rateslib, pytest.mark.adapter_quantlib]

ROOT = Path(__file__).resolve().parents[1]
ARBS_TOP_LEVEL = {"MDP", "Query", "Caching", "TB", "BT", "RVUtils", "SDRUtils", "Simulation", "definitions"}


def test_default_adapters_import_without_arbs_on_the_path():
    pytest.importorskip("rateslib")
    pytest.importorskip("QuantLib")
    code = ("import sys, pricebt.contrib.rateslib, pricebt.contrib.quantlib;"
            "bad=[m for m in sys.modules if m.split('.')[0] in %r];print(bad)" % sorted(ARBS_TOP_LEVEL))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, env={**__import__("os").environ, "PYTHONPATH": "src"})
    assert out.stdout.strip() == "[]", out.stdout + out.stderr
