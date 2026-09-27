"""Hardened YAML loading for asset and FX config files: YAML-1.2-ish scalars (see `_Loader`) and a
duplicate mapping key is a load-time error with its position. There is no `${VAR}` interpolation:
an asset config is meant to be self-contained (DESIGN.md §4.1), so its expression strings are never
silently rewritten before they are compiled.
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any, Dict, Sequence, Union

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
    """`yaml.SafeLoader` with YAML-1.2 core scalar rules: `1e7` is a float (stock PyYAML reads it as
    a string), only `true/True/TRUE/false/False/FALSE` are booleans (`yes`/`no`/`on`/`off` stay
    plain strings), integers never use a sexagesimal or octal reading, and underscores are allowed
    inside numbers. A duplicate mapping key raises instead of silently keeping the last value."""


def _strip(tags: Sequence[str]) -> None:
    for first, resolvers in list(_Loader.yaml_implicit_resolvers.items()):
        _Loader.yaml_implicit_resolvers[first] = [(t, r) for (t, r) in resolvers if t not in tags]


_Loader.yaml_implicit_resolvers = {k: list(v) for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()}
_strip(("tag:yaml.org,2002:bool", "tag:yaml.org,2002:float", "tag:yaml.org,2002:int"))
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
            first = seen[key].start_mark.line + 1
            raise ConfigError(f"{m.name}:{m.line + 1}:{m.column + 1}: duplicate mapping key {key!r} (first seen at line {first})")
        seen[key] = key_node
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


_Loader.add_constructor("tag:yaml.org,2002:int", _construct_int)
_Loader.add_constructor("tag:yaml.org,2002:float", _construct_float)
_Loader.add_constructor("tag:yaml.org,2002:bool", _construct_bool)
_Loader.add_constructor("tag:yaml.org,2002:map", _construct_mapping)


def load_text(text: str, *, name: str = "<string>") -> Any:
    """Parse one config file's YAML text (hardened: see `_Loader`). `name` is used only for error
    positions and appears nowhere in the parsed result."""
    stream = io.StringIO(text)
    stream.name = name  # type: ignore[attr-defined]
    try:
        return yaml.load(stream, Loader=_Loader)
    except yaml.YAMLError as e:
        raise ConfigError(str(e).replace("\n", " ")) from e


def load_file(path: Union[str, Path]) -> Any:
    """Load and parse one config file. The top level need not be a mapping; callers validate shape."""
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"config file not found: {p}")
    return load_text(p.read_text(encoding="utf8"), name=str(p))
