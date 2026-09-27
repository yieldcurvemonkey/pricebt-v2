"""Hardened YAML/JSON loading: YAML-1.2-ish scalars, duplicate-key errors with position, ${ENV} interpolation, extends/include, --set overrides."""
from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

import yaml

from ..errors import ConfigError

_BOOL = re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$")
_INT = re.compile(r"^[-+]?(?:0|[1-9][0-9_]*)$")
_FLOAT = re.compile(
    r"""^(?:
        [-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+]?[0-9]+)?
       |[-+]?\.[0-9][0-9_]*(?:[eE][-+]?[0-9]+)?
       |[-+]?[0-9][0-9_]*(?:[eE][-+]?[0-9]+)
       |[-+]?\.(?:inf|Inf|INF)
       |\.(?:nan|NaN|NAN)
    )$""",
    re.X,
)


class _Loader(yaml.SafeLoader):
    """SafeLoader with YAML-1.2 core scalars: 1e7 is a float; only true/false are bools; no sexagesimal ints; duplicate keys are errors."""


def _strip(loader_cls: type, tags: Sequence[str]) -> None:
    for first, resolvers in list(loader_cls.yaml_implicit_resolvers.items()):
        loader_cls.yaml_implicit_resolvers[first] = [(t, r) for (t, r) in resolvers if t not in tags]


_Loader.yaml_implicit_resolvers = {k: list(v) for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()}
_strip(_Loader, ("tag:yaml.org,2002:bool", "tag:yaml.org,2002:float", "tag:yaml.org,2002:int"))
_Loader.add_implicit_resolver("tag:yaml.org,2002:bool", _BOOL, list("tTfF"))
_Loader.add_implicit_resolver("tag:yaml.org,2002:int", _INT, list("-+0123456789"))
_Loader.add_implicit_resolver("tag:yaml.org,2002:float", _FLOAT, list("-+0123456789."))


def _construct_int(loader: yaml.Loader, node: yaml.Node) -> int:
    return int(str(loader.construct_scalar(node)).replace("_", ""))


def _construct_float(loader: yaml.Loader, node: yaml.Node) -> float:
    s = str(loader.construct_scalar(node)).replace("_", "").lower()
    if s.endswith(".inf"):
        return float("-inf") if s.startswith("-") else float("inf")
    if s == ".nan":
        return float("nan")
    return float(s)


def _construct_bool(loader: yaml.Loader, node: yaml.Node) -> bool:
    return str(loader.construct_scalar(node)).lower() == "true"


def _construct_mapping(loader: yaml.Loader, node: yaml.MappingNode, deep: bool = False) -> Dict[Any, Any]:
    seen: Dict[Any, yaml.Node] = {}
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in seen:
            m = key_node.start_mark
            raise ConfigError(f"duplicate mapping key {key!r} (first at line {seen[key].start_mark.line + 1})", path=f"{m.name}:{m.line + 1}:{m.column + 1}", code="CFG-YAML")
        seen[key] = key_node
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


_Loader.add_constructor("tag:yaml.org,2002:int", _construct_int)
_Loader.add_constructor("tag:yaml.org,2002:float", _construct_float)
_Loader.add_constructor("tag:yaml.org,2002:bool", _construct_bool)
_Loader.add_constructor("tag:yaml.org,2002:map", _construct_mapping)


def loads(text: str, *, name: str = "<string>", fmt: Optional[str] = None) -> Any:
    """Parse YAML (hardened) or JSON text. fmt: 'yaml' | 'json' | None (json if it starts with { or [ and parses)."""
    if fmt == "json" or (fmt is None and text.lstrip().startswith(("{", "[")) and _looks_json(text)):
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ConfigError(str(e), path=f"{name}:{e.lineno}:{e.colno}", code="CFG-YAML") from e
    try:
        return yaml.load(_named(text, name), Loader=_Loader)
    except yaml.YAMLError as e:
        raise ConfigError(str(e).replace("\n", " "), path=name, code="CFG-YAML") from e


def _looks_json(text: str) -> bool:
    try:
        json.loads(text)
        return True
    except json.JSONDecodeError:
        return False


def _named(text: str, name: str) -> Any:
    import io

    s = io.StringIO(text)
    s.name = name  # type: ignore[attr-defined]
    return s


_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def interpolate(obj: Any, env: Optional[Mapping[str, str]] = None) -> Any:
    """Replace ${VAR} / ${VAR:-default} in every string. A missing variable without default is an error."""
    env = os.environ if env is None else env

    def sub(s: str) -> Any:
        def rep(m: "re.Match[str]") -> str:
            v = env.get(m.group(1), m.group(2))
            if v is None:
                raise ConfigError(f"environment variable {m.group(1)!r} is not set and has no default", code="CFG-ENV")
            return v

        out = _ENV.sub(rep, s)
        return out

    if isinstance(obj, str):
        return sub(obj)
    if isinstance(obj, list):
        return [interpolate(x, env) for x in obj]
    if isinstance(obj, dict):
        return {k: interpolate(v, env) for k, v in obj.items()}
    return obj


def deep_merge(base: Any, over: Any) -> Any:
    """Mappings merge recursively; everything else (lists, scalars) is replaced by `over`."""
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = deep_merge(base[k], v) if k in base else copy.deepcopy(v)
        return out
    return copy.deepcopy(over)


def load_file(path: Union[str, Path], *, _seen: Optional[List[Path]] = None) -> Dict[str, Any]:
    """Load a config file, resolving `extends: <path or list>` (base first, this file overrides) with cycle detection."""
    p = Path(path).resolve()
    seen = list(_seen or [])
    if p in seen:
        raise ConfigError(f"extends cycle: {' -> '.join(map(str, seen + [p]))}", code="CFG-EXTENDS")
    if not p.exists():
        raise ConfigError(f"config file not found: {p}", code="CFG-FILE")
    raw = loads(p.read_text(encoding="utf8"), name=str(p), fmt="json" if p.suffix.lower() == ".json" else None)
    if not isinstance(raw, dict):
        raise ConfigError("top level of a config must be a mapping", path=str(p), code="CFG-YAML")
    ext = raw.pop("extends", None)
    merged: Dict[str, Any] = {}
    for e in ([ext] if isinstance(ext, str) else list(ext or [])):
        merged = deep_merge(merged, load_file(p.parent / e, _seen=seen + [p]))
    return deep_merge(merged, raw)


def set_path(cfg: Dict[str, Any], dotted: str, value: Any) -> None:
    """--set a.b.c=1 style override; list indices allowed as a.b.0.c."""
    parts = dotted.split(".")
    cur: Any = cfg
    for i, part in enumerate(parts[:-1]):
        nxt = parts[i + 1]
        if isinstance(cur, list):
            cur = cur[int(part)]
        else:
            if part not in cur or cur[part] is None:
                cur[part] = [] if nxt.isdigit() else {}
            cur = cur[part]
    last = parts[-1]
    if isinstance(cur, list):
        cur[int(last)] = value
    else:
        cur[last] = value


def apply_overrides(cfg: Dict[str, Any], sets: Sequence[str]) -> Dict[str, Any]:
    out = copy.deepcopy(cfg)
    for s in sets:
        if "=" not in s:
            raise ConfigError(f"--set expects key=value, got {s!r}", code="CFG-SET")
        k, v = s.split("=", 1)
        set_path(out, k.strip(), loads(v, name="--set"))
    return out
