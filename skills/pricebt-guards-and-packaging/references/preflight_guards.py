"""Preflight for a new library's guard entries: would the import root / token you are about to ban hit the EXISTING core tree?

    python skills/pricebt-guards-and-packaging/references/preflight_guards.py --root <import_root> --token <name> [--match substring|word|prefix] [--repo <repo root>]

It runs the SAME scan functions the guards run (tests/guards/scan.py) against core files, core schemas and shipped core configs, with your entries appended to a COPY of
tests/guards/banned.yaml (nothing is written). Exit 0: nothing existing would be flagged, so adding the entries cannot turn the guards red. Exit 1: the hits are printed;
use a more specific token or `--match word`. Exit 2: no pricebt checkout found (pass `--repo`). Read-only; needs no pricing library.
The repository root is searched upwards from this file, then from the current directory, for `tests/guards/scan.py`, so the script also works when it was copied elsewhere.
"""
from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path
from typing import Optional


def find_repo(explicit: Optional[str]) -> Optional[Path]:
    """The first directory at or above the starting points that holds tests/guards/scan.py."""
    starts = [Path(explicit)] if explicit else [Path(__file__), Path.cwd()]
    for s in starts:
        s = s.resolve()
        for d in [s, *s.parents]:
            if (d / "tests" / "guards" / "scan.py").is_file():
                return d
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", required=True, help="the library's import root, spelled exactly as in `import <root>` (case-sensitive)")
    ap.add_argument("--token", required=True, help="the name to ban in identifiers, string literals, schema and config values (case-insensitive)")
    ap.add_argument("--match", default="substring", choices=("substring", "word", "prefix"))
    ap.add_argument("--repo", default=None, help="repository root (default: searched upwards from this file, then from the current directory)")
    a = ap.parse_args(argv)
    repo = find_repo(a.repo)
    if repo is None:
        print("error: not a pricebt checkout (no tests/guards/scan.py found); pass --repo <repository root>", file=sys.stderr)
        return 2
    sys.path.insert(0, str(repo / "tests"))
    from guards import scan  # noqa: E402  (the guards' own functions: what they would say is what this says)

    src, cfg = repo / "src", repo / "configs"
    banned = copy.deepcopy(scan.load_banned(repo / "tests" / "guards" / "banned.yaml"))
    banned["import_roots"].append(a.root)
    banned["executable_tokens"].append({"token": a.token, "match": a.match})
    hits = []
    for p in scan.core_py_files(src):
        hits += scan.scan_imports(p, src, banned) + scan.scan_names(p, src, banned)
    for p in scan.core_schema_files(src):
        hits += scan.scan_yaml(p, src, banned)
    for p in scan.core_config_files(cfg):
        hits += scan.scan_yaml(p, cfg, banned)
    mine = [h for h in hits if h.token in (a.root, a.token) or a.token.lower() in h.token.lower()]
    for h in sorted(set(mine))[:20]:
        print(h)
    print(f"{len(set(mine))} existing core hit(s) for root={a.root!r} token={a.token!r} match={a.match}")
    return 1 if mine else 0


if __name__ == "__main__":
    raise SystemExit(main())
