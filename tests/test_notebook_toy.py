"""Executes the built 040304 toy notebook end-to-end with nbclient (IMPLEMENTATION_PLAN.md P4.3,
section 0.6; DESIGN.md section 10, Appendix B). Marked `notebook` (pytest.ini): it spawns a real
`python3` kernel subprocess and needs nbclient/ipykernel installed.
"""
from __future__ import annotations

import os
from pathlib import Path

import nbformat
import pytest
from nbclient import NotebookClient
from nbclient.exceptions import CellExecutionError

pytestmark = pytest.mark.notebook

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "notebooks" / "040304_mean_reversion_toy.ipynb"


def test_toy_notebook_executes_end_to_end():
    nb = nbformat.read(NOTEBOOK, as_version=4)

    # IMPLEMENTATION_PLAN.md section 0.6: the kernel is a subprocess, so it only sees `src`/`tests`
    # on sys.path if PYTHONPATH says so; nbclient has no direct way to pass a kernel env, so (like
    # tools/nb_build.py) set it on THIS process and restore it after -- the kernel subprocess
    # inherits os.environ at spawn time.
    prev_pythonpath = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), str(ROOT / "tests"), prev_pythonpath or ""])
    try:
        client = NotebookClient(nb, timeout=600, kernel_name="python3", resources={"metadata": {"path": str(ROOT)}})
        try:
            client.execute()
        except CellExecutionError as exc:
            pytest.fail(f"toy notebook raised during execution: {str(exc)[-2000:]}")
    finally:
        if prev_pythonpath is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = prev_pythonpath
