"""Build and execute the example notebooks from percent-format sources.

    cd <project root>
    C:\\Users\\chris\\anaconda3\\envs\\stir\\python.exe tools\\nb_build.py                     # every notebooks/src/*.py
    ... tools\\nb_build.py notebooks\\src\\showcase_swap_book.py                      # one source
    ... tools\\nb_build.py --no-exec                                                # write .ipynb without running (fast structure check)

A source file is plain Python with jupytext-style markers: `# %% [markdown]` starts a markdown cell (lines are `# `-prefixed comment text), `# %%` starts a
code cell. `notebooks/src/NAME.py` -> `notebooks/NAME.ipynb`, executed in place with the `python3` kernel from the project root (so `tools`, `data`, `configs`
resolve) and with `PYTHONPATH=src` for the checkout. Outputs (tables, figures, tqdm bars) are stored in the notebook. Exit code 1 if any notebook errors.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
import traceback
from pathlib import Path

import nbformat
from nbclient import NotebookClient
from nbclient.exceptions import CellExecutionError

ROOT = Path(__file__).resolve().parents[1]
MARK = re.compile(r"^# %%(?P<md> \[markdown\])?\s*$")


def parse(text: str) -> nbformat.NotebookNode:
    cells, kind, buf = [], None, []

    def flush() -> None:
        nonlocal buf
        if kind is None:
            return
        while buf and not buf[-1].strip():
            buf.pop()
        if not buf:
            return
        if kind == "md":
            body = "\n".join(re.sub(r"^# ?", "", ln) for ln in buf)
            cells.append(nbformat.v4.new_markdown_cell(body))
        else:
            cells.append(nbformat.v4.new_code_cell("\n".join(buf)))
        buf = []

    for line in text.splitlines():
        m = MARK.match(line)
        if m:
            flush()
            kind, buf = ("md" if m.group("md") else "code"), []
        elif kind is not None:
            buf.append(line)
    flush()
    nb = nbformat.v4.new_notebook(cells=cells)
    nb.metadata["kernelspec"] = {"display_name": "Python 3", "language": "python", "name": "python3"}
    nb.metadata["language_info"] = {"name": "python"}
    return nb


def build(src: Path, execute: bool, timeout: int) -> bool:
    out = ROOT / "notebooks" / (src.stem + ".ipynb")
    nb = parse(src.read_text(encoding="utf8"))
    nbformat.validate(nb)
    t0 = time.time()
    ok = True
    if execute:
        env_pp = os.pathsep.join([str(ROOT / "src"), str(ROOT), os.environ.get("PYTHONPATH", "")])
        os.environ["PYTHONPATH"] = env_pp
        client = NotebookClient(nb, timeout=timeout, kernel_name="python3", resources={"metadata": {"path": str(ROOT)}}, allow_errors=False)
        try:
            client.execute()
        except CellExecutionError as e:
            ok = False
            print(f"FAILED {src.name}: {str(e)[-1500:]}")
        except Exception:
            ok = False
            traceback.print_exc()
    out.parent.mkdir(parents=True, exist_ok=True)
    nbformat.write(nb, out)
    n_code = sum(c.cell_type == "code" for c in nb.cells)
    print(f"{'ok  ' if ok else 'FAIL'} {src.name} -> {out.relative_to(ROOT)}  ({n_code} code cells, {time.time() - t0:.0f}s{'' if execute else ', not executed'})")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sources", nargs="*")
    ap.add_argument("--no-exec", action="store_true")
    ap.add_argument("--timeout", type=int, default=1800, help="per-cell seconds")
    a = ap.parse_args()
    srcs = [Path(s) for s in a.sources] or sorted((ROOT / "notebooks" / "src").glob("*.py"))
    results = [build(s if s.is_absolute() else ROOT / s, not a.no_exec, a.timeout) for s in srcs]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
