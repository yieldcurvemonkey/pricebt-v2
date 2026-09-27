"""Import blocker for the zero-dependence guards (spec Z1, 9.3).

A `sys.meta_path` finder installed FIRST that raises ModuleNotFoundError for banned import roots. It sees every import, including one hidden
in a try/except or inside a function, which a `sys.modules` inspection after the fact cannot. Run as a script it starts pytest with the
blocker active ("core-only" run): `python tests/guards/blocker.py [pytest args]`.
"""
from __future__ import annotations

import importlib.abc
import sys
from pathlib import Path
from typing import Iterable, Optional, Sequence

HERE = Path(__file__).resolve().parent


class BannedRoots(importlib.abc.MetaPathFinder):
    def __init__(self, roots: Iterable[str]):
        self.roots = frozenset(roots)
        self.attempts: list = []

    def find_spec(self, fullname: str, path: object = None, target: object = None) -> None:
        if fullname.split(".")[0] in self.roots:
            self.attempts.append(fullname)
            raise ModuleNotFoundError(f"No module named {fullname!r} (blocked by the pricebt import blocker)", name=fullname)
        return None


def default_roots() -> list:
    import yaml

    return list(yaml.safe_load((HERE / "banned.yaml").read_text(encoding="utf8"))["import_roots"])


def install(roots: Optional[Iterable[str]] = None) -> BannedRoots:
    """Install the blocker at position 0 and forget already-imported banned modules (a fresh interpreter has none)."""
    finder = BannedRoots(roots if roots is not None else default_roots())
    for name in [m for m in sys.modules if m.split(".")[0] in finder.roots]:
        del sys.modules[name]
    sys.meta_path.insert(0, finder)
    return finder


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    install()
    import pytest

    return int(pytest.main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
