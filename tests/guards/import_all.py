"""Child process of GT-Z1a: import every core module (everything except contrib) with the import blocker installed; print failures as JSON."""
from __future__ import annotations

import importlib
import json
import pkgutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import blocker  # noqa: E402

finder = blocker.install()

import pricebt  # noqa: E402

failed = {}
imported = []


def walk(package_name: str, path: list) -> None:
    """Depth-first over packages WITHOUT importing contrib (an adapter may import its library)."""
    for info in pkgutil.iter_modules(path, package_name + "."):
        leaf = info.name.rsplit(".", 1)[-1]
        if leaf in ("contrib", "__main__"):
            continue
        try:
            mod = importlib.import_module(info.name)
            imported.append(info.name)
        except BaseException as e:  # noqa: BLE001 - report everything that stops an import
            failed[info.name] = f"{type(e).__name__}: {e}"
            continue
        if info.ispkg:
            walk(info.name, list(mod.__path__))


walk("pricebt", list(pricebt.__path__))
print(json.dumps({"imported": len(imported), "failed": failed, "blocked_attempts": finder.attempts}))
