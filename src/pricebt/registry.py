"""Name -> object registry with allow-listed dotted-path resolution (`pkg.mod:Attr.sub`)."""
from __future__ import annotations

import difflib
import importlib
import types
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

from .errors import RegistryError

DEFAULT_ALLOW: Tuple[str, ...] = ("pricebt",)


def _under(name: str, allow: Sequence[str]) -> bool:
    return any(name == a or name.startswith(a + ".") for a in allow)


def _owner(obj: Any) -> Optional[str]:
    """The package that owns `obj`: a module's own name, else the `__module__` of a function/class (or of an instance's class); None for plain data."""
    if isinstance(obj, types.ModuleType):
        return obj.__name__
    owner = getattr(obj, "__module__", None)
    return owner if isinstance(owner, str) else None


def resolve_dotted(path: str, *, allow: Sequence[str] = DEFAULT_ALLOW) -> Any:
    """Import `package.module:Attr[.attr...]` (config strings are code execution). The module must be under an allowed prefix AND every object
    on the attribute path must be OWNED by an allowed prefix (an allowed module that re-exports `yaml` or `os` does not open them); names starting with an
    underscore are never reachable."""
    if ":" not in path:
        raise RegistryError(f"{path!r} is neither a registry name nor a dotted path 'package.module:Attr'", code="CFG-FORMAT")
    mod_name, _, attr = path.partition(":")
    if not _under(mod_name, allow):
        raise RegistryError(f"module {mod_name!r} is not under an allowed prefix {list(allow)}", code="CFG-ALLOW")
    try:
        obj: Any = importlib.import_module(mod_name)
    except ImportError as e:
        raise RegistryError(f"cannot import {mod_name!r}: {e}", code="CFG-IMPORT") from e
    for part in attr.split("."):
        if part.startswith("_"):
            raise RegistryError(f"{path!r}: private names ({part!r}) are not reachable from config", code="CFG-ALLOW")
        try:
            obj = getattr(obj, part)
        except AttributeError as e:
            raise RegistryError(f"{mod_name!r} has no attribute path {attr!r}", code="CFG-IMPORT") from e
        owner = _owner(obj)
        if owner is not None and not _under(owner, allow):
            raise RegistryError(f"{path!r}: {part!r} is owned by {owner!r}, which is not under an allowed prefix {list(allow)}", code="CFG-ALLOW")
    return obj


class Registry:
    """A registry of one kind (trigger, action, signal, cost, cash_accrual, mdp, calendar). Names are case-insensitive; `<name>_<kind>` and CamelCase are accepted."""

    def __init__(self, kind: str, allow: Iterable[str] = DEFAULT_ALLOW):
        self.kind = kind
        self.allow = tuple(allow)
        self._items: Dict[str, Any] = {}

    @staticmethod
    def _norm(name: str) -> str:
        return name.replace("-", "_").lower()

    def register(self, name: str, obj: Any = None, *, aliases: Sequence[str] = ()) -> Any:
        def _do(o: Any) -> Any:
            for n in (name, *aliases):
                self._items[self._norm(n)] = o
            return o

        return _do(obj) if obj is not None else _do

    def resolve(self, spec: str, *, extra_allow: Sequence[str] = ()) -> Any:
        key = self._norm(spec)
        if key in self._items:
            return self._items[key]
        suffix = "_" + self.kind
        if key.endswith(suffix) and key[: -len(suffix)] in self._items:
            return self._items[key[: -len(suffix)]]
        camel = "".join(ch if ch.islower() or ch.isdigit() else "_" + ch.lower() for ch in spec).lstrip("_")
        if self._norm(camel) in self._items:
            return self._items[self._norm(camel)]
        if camel.endswith(suffix) and camel[: -len(suffix)] in self._items:
            return self._items[camel[: -len(suffix)]]
        if ":" in spec:
            return resolve_dotted(spec, allow=(*self.allow, *extra_allow))
        close = difflib.get_close_matches(key, self._items, n=3)
        raise RegistryError(f"unknown {self.kind} {spec!r}; did you mean {close}? known: {sorted(self._items)}", code="CFG-UNKNOWN-TYPE")

    def names(self) -> Sequence[str]:
        return sorted(self._items)
