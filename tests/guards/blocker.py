"""The runtime import blocker (DESIGN.md section 12.2 item 3): a `sys.meta_path` finder installed
FIRST that raises `ImportError` for the banned roots. It sees every import, including one hidden
inside a function body or a try/except, which a `sys.modules` inspection after the fact cannot.
"""
from __future__ import annotations

import importlib.abc
import sys
from typing import Iterable, List, Optional

BANNED_ROOTS = ("gs_quant", "rateslib", "QuantLib", "MDP", "Query", "Caching", "dataclasses_json")


class BannedRoots(importlib.abc.MetaPathFinder):
    def __init__(self, roots: Iterable[str]):
        self.roots = frozenset(roots)
        self.attempts: List[str] = []

    def find_spec(self, fullname: str, path: object = None, target: object = None) -> None:
        if fullname.split(".")[0] in self.roots:
            self.attempts.append(fullname)
            raise ImportError(f"No module named {fullname!r} (blocked by the pricebt import blocker)", name=fullname)
        return None


def install(roots: Optional[Iterable[str]] = None) -> BannedRoots:
    """Install the blocker at position 0 and forget any already-imported banned module (a fresh
    interpreter has none, but this makes `install()` idempotent within one process too)."""
    finder = BannedRoots(roots if roots is not None else BANNED_ROOTS)
    for name in [m for m in sys.modules if m.split(".")[0] in finder.roots]:
        del sys.modules[name]
    sys.meta_path.insert(0, finder)
    return finder
