"""Mutation check: apply one textual mutation to a file, run the named tests, expect them to FAIL, restore the file.

    python tools/mutcheck.py <spec.json>

spec.json = {"file": "tests/guards/scan.py", "tests": ["tests/guards/test_twins.py"], "mutations": [{"name": "...", "find": "...", "replace": "..."}]}
   or, for several pytest calls per mutant (each entry is one argument list, so a `-k` on one file does not filter the others):
           {"file": "...", "runs": [["tests/test_a.py", "-k", "wrap"], ["tests/test_b.py"]], "mutations": [...]}

The child pytest gets PYTHONPATH = `src` + the caller's PYTHONPATH, so an adapter or library that lives outside `src` (importable only through the caller's PYTHONPATH) is visible to it.

Exit code 0 only if EVERY mutation made the tests fail (a mutation that leaves them green means the tests do not cover that code). The original
file is always restored (byte-for-byte) even on error. Run from the project root. A mutation is written with plain newlines; the tree is CRLF on
Windows, so `find` / `replace` are converted to the target file's own line ending before matching and applying.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PY = sys.executable
ROOT = Path(__file__).resolve().parents[1]
CR, LF = chr(13), chr(10)


def child_env() -> dict:
    """The caller's environment with `src` FIRST on PYTHONPATH and the caller's own entries kept after it: an adapter or a library that lives outside `src` (every external adapter) is importable
    in the caller's shell only through PYTHONPATH, and a child that replaced it would skip those tests, so a mutant in that adapter could never be seen to fail."""
    path = os.pathsep.join(filter(None, ["src", os.environ.get("PYTHONPATH", "")]))
    return {**os.environ, "PYTHONPATH": path, "PYTHONDONTWRITEBYTECODE": "1"}


def run_tests(tests: list, extra: list) -> tuple:
    p = subprocess.run([PY, "-m", "pytest", "-q", "--tb=no", "-x", "-p", "no:cacheprovider", *extra, *tests], capture_output=True, text=True, cwd=ROOT, env=child_env(), timeout=1800)
    tail = (p.stdout + p.stderr).strip().splitlines()[-3:]
    return p.returncode, " | ".join(tail)


def run_all(spec: dict) -> tuple:
    """Every run of the spec, stopping at the first that fails. A spec is either `tests` (+ optional `pytest_args`, one pytest call) or `runs`: a list of pytest argument lists (test paths, `-k`, `-m`
    ...), one call each, so a `-k` filter on one file cannot weaken the others. A mutant is killed when ANY run fails; the control needs ALL of them green."""
    runs = spec["runs"] if "runs" in spec else [[*spec.get("pytest_args", []), *spec["tests"]]]
    tail = ""
    for args in runs:
        rc, tail = run_tests([], args)
        if rc != 0:
            return rc, tail
    return 0, tail


def main(argv: list) -> int:
    spec = json.loads(Path(argv[1]).read_text(encoding="utf8"))
    target = ROOT / spec["file"]
    original = target.read_bytes()
    text = original.decode("utf8")
    crlf = (CR + LF) in text

    def nl(x: str) -> str:
        return x.replace(LF, CR + LF) if crlf else x

    code0, tail0 = run_all(spec)  # control: the unmutated file must be green
    print(f"control (unmutated): rc={code0} {tail0}")
    if code0 != 0:
        print("ABORT: the control run is not green; fix the tests before mutating")
        return 2
    survived = []
    try:
        for m in spec["mutations"]:
            if text.count(nl(m["find"])) < 1:
                print(f"[{m['name']}] SKIPPED-BAD-MUTATION: text not found")
                survived.append(m["name"] + " (find text missing)")
                continue
            target.write_bytes(text.replace(nl(m["find"]), nl(m["replace"]), 1).encode("utf8"))
            rc, tail = run_all(spec)
            status = "KILLED" if rc != 0 else "SURVIVED"
            print(f"[{m['name']}] {status}: rc={rc} {tail}")
            if rc == 0:
                survived.append(m["name"])
    finally:
        target.write_bytes(original)
    assert target.read_bytes() == original
    print("restored:", spec["file"], "| survivors:", survived or "none")
    return 1 if survived else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
