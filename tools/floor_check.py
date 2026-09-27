"""Regression floor (spec 9.5): every test of the recorded pre-refactor baseline is still collected, or is named in section 2 (or section 12) of the change log.

    python tools/floor_check.py                 # collects the current suite (`pytest --collect-only`) and checks it against tasks/baseline_test_ids.txt
    python tools/floor_check.py --current ids.txt

A missing baseline id is EXPLAINED when the change log names its test function (`test_x.py::test_name` or `test_name`) or, for a whole file that was deleted, migrated or
rewritten, names the file WITHOUT a test (`test_x.py` or `test_x`, not `test_x::test_name`) in a row of section 2 or 12. Exit 0 when nothing is unexplained. Pure functions (`classify`) are unit-tested on known answers in
`tests/test_floor_check.py`.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

ROOT = Path(__file__).resolve().parents[1]


def base_id(test_id: str) -> str:
    """`tests/test_x.py::test_name[param]` -> `tests/test_x.py::test_name` (parametrisation is not a separate test for the floor)."""
    return test_id.split("[", 1)[0]


def section2(changelog: str) -> str:
    """Section 2 (tests removed or changed) plus section 12 (the reconciled rows of the review cycles and of the sub-agents' migrations): both list tests with their reason."""
    m = re.search(r"^## 2\. .*?$(.*?)^## 3\. ", changelog, re.S | re.M)
    if not m:
        raise ValueError("the change log has no section 2 (tests removed or changed)")
    m12 = re.search(r"^## 12\. .*?$(.*)\Z", changelog, re.S | re.M)
    return m.group(1) + ("\n" + m12.group(1) if m12 else "")


def classify(baseline: Iterable[str], current: Iterable[str], changelog: str) -> Dict[str, List[str]]:
    """{'present': [...], 'explained': [...], 'unexplained': [...]} over the baseline ids (a base id present in `current` counts as present)."""
    text = section2(changelog)
    have = {base_id(i) for i in current}
    out: Dict[str, List[str]] = {"present": [], "explained": [], "unexplained": []}
    for tid in sorted({base_id(i) for i in baseline}):
        if tid in have:
            out["present"].append(tid)
            continue
        path, _, name = tid.partition("::")
        stem = Path(path).stem
        by_name = re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text) is not None
        by_file = re.search(rf"(?<![A-Za-z0-9_]){re.escape(stem)}(?:\.py)?(?![A-Za-z0-9_:])", text) is not None  # a FILE-level row: the file is named without `::`
        (out["explained"] if by_name or by_file else out["unexplained"]).append(tid)
    return out


def collect_current() -> List[str]:
    env = {**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")}
    r = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-o", "addopts=", "-p", "no:cacheprovider", "tests"], capture_output=True, text=True, cwd=ROOT, env=env)
    return [ln.strip() for ln in r.stdout.splitlines() if "::" in ln and not ln.startswith(("ERROR", "FAILED"))]


def main(argv: Sequence[str] = ()) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", default=str(ROOT / "tasks" / "baseline_test_ids.txt"))
    ap.add_argument("--changelog", default=str(ROOT / "docs" / "design" / "11-refactor-changelog.md"))
    ap.add_argument("--current", default=None, help="a file of current test ids (default: collect them)")
    a = ap.parse_args(list(argv) or None)
    baseline = [ln.strip() for ln in Path(a.baseline).read_text(encoding="utf8").splitlines() if "::" in ln]
    current = [ln.strip() for ln in Path(a.current).read_text(encoding="utf8").splitlines() if "::" in ln] if a.current else collect_current()
    res = classify(baseline, current, Path(a.changelog).read_text(encoding="utf8"))
    print(f"baseline {len(set(map(base_id, baseline)))} tests: {len(res['present'])} still collected, {len(res['explained'])} explained by the change log, {len(res['unexplained'])} UNEXPLAINED")
    for tid in res["unexplained"]:
        print("  UNEXPLAINED:", tid)
    return 1 if res["unexplained"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
