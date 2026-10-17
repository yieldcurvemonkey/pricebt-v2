"""Market provider configs and their registry (docs/v2/MARKET_CONTEXT_DESIGN.md section 3).

A **market provider** is one self-contained YAML file that says how to obtain the dated market
object of one market (a curve, a vol surface): its own `imports`, `code` and one
`expr`. It is to an asset config what an FX config is to a currency conversion (`assets.fx`): a
standalone, named piece of trusted code an asset config references (`market: {provider: <name>}`)
instead of carrying the market expression itself. Several assets that name one provider share one
market object per (provider, date, csa, location); a provider that lists others under `requires:`
builds on their objects (a vol surface on a curve).

Loading compiles and never executes: `imports`/`code` run lazily, once per process, on the first
evaluation (`assets.namespace.AssetNamespace`), exactly as for an asset config.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

from ..errors import ConfigError
from . import yamlio
from .config import _check_unknown_keys, _compile_expr, _fail, _require_mapping, _require_str, _suggest

_TOP_KEYS = {"schema_version", "market", "description", "imports", "code", "requires", "expr"}


@dataclass(frozen=True)
class MarketConfig:
    """One loaded, validated, compiled market provider config. `requires` names the providers
    whose market objects `expr` receives as `markets[<name>]`."""

    name: str
    source: str
    description: Optional[str]
    expr: str
    requires: Tuple[str, ...]
    imports_src: str
    code_src: str
    imports_code: Any = field(repr=False)
    code_code: Any = field(repr=False)
    _compiled: Dict[str, Tuple[Any, str]] = field(repr=False)

    def code(self, key: str) -> Any:
        return self._compiled[key][0]

    def expr_src(self, key: str) -> str:
        return self._compiled[key][1]


# A source `MarketRegistry.add` accepts: a yaml path, a directory of *.yaml, an in-memory mapping,
# or an already-loaded MarketConfig.
MarketSource = Union[str, Path, Mapping[str, Any], MarketConfig]


def load_market(source: Union[str, Path, Mapping[str, Any]]) -> MarketConfig:
    """Load, validate and compile one market provider config from a file path or an in-memory
    mapping. The provider's name is its `market:` key, else the file stem."""
    if isinstance(source, Mapping):
        raw_top: Any = source
        src_label = "<mapping>"
        default_name = None
    else:
        path = Path(source)
        raw_top = yamlio.load_file(path)
        src_label = str(path)
        default_name = path.stem

    raw = _require_mapping(raw_top, default_name, "<root>")
    name = raw.get("market", default_name) or default_name
    if isinstance(name, Mapping):
        # an asset config handed to the market loader: its `market:` is a section, not a name
        _fail(default_name, "market", "must be the provider's name (a string); this looks like an asset config, whose market: is a section")
    _check_unknown_keys(raw, _TOP_KEYS, name, "")
    if not name:
        raise ConfigError("config has no 'market' id and no file path to default it from", key="market")
    _require_str(name, default_name, "market")

    schema_version = raw.get("schema_version")
    if schema_version != 1:
        _fail(name, "schema_version", f"must be 1, got {schema_version!r}")

    description = raw.get("description")
    if description is not None and not isinstance(description, str):
        _fail(name, "description", f"must be a string, got {description!r}")

    imports_src = raw.get("imports") or ""
    if not isinstance(imports_src, str):
        _fail(name, "imports", f"must be a string, got {imports_src!r}")
    code_src = raw.get("code") or ""
    if not isinstance(code_src, str):
        _fail(name, "code", f"must be a string, got {code_src!r}")
    imports_code = _compile_expr(imports_src, name, "imports", "exec")
    code_code = _compile_expr(code_src, name, "code", "exec")

    requires_raw = raw.get("requires") or ()
    if isinstance(requires_raw, str) or not isinstance(requires_raw, (list, tuple)) or not all(isinstance(r, str) and r for r in requires_raw):
        _fail(name, "requires", f"must be a list of market provider names, got {requires_raw!r}")
    requires = tuple(requires_raw)
    if len(set(requires)) != len(requires):
        _fail(name, "requires", f"lists a provider twice: {list(requires)}")
    if name in requires:
        _fail(name, "requires", "a market provider cannot require itself")

    expr = _require_str(raw.get("expr"), name, "expr")
    expr_code = _compile_expr(expr, name, "expr", "eval")

    return MarketConfig(
        name=name,
        source=src_label,
        description=description,
        expr=expr,
        requires=requires,
        imports_src=imports_src,
        code_src=code_src,
        imports_code=imports_code,
        code_code=code_code,
        _compiled={"market": (expr_code, expr)},
    )


class MarketRegistry:
    """name -> market provider config. A provider's `requires:` must already be registered, so the
    providers always form a DAG; a directory is registered in dependency order whatever its file
    names."""

    def __init__(self) -> None:
        self._by_name: Dict[str, MarketConfig] = {}

    def add(self, source: MarketSource) -> List[MarketConfig]:
        """Add one config (a path, a dict, or a `MarketConfig`), or every `*.yaml` in a directory.
        Returns the configs added, in registration order."""
        if isinstance(source, MarketConfig):
            pending = [source]
        elif isinstance(source, Mapping):
            pending = [load_market(source)]
        else:
            path = Path(source)
            pending = [load_market(p) for p in sorted(path.glob("*.yaml"))] if path.is_dir() else [load_market(path)]
        added: List[MarketConfig] = []
        while pending:
            ready = [c for c in pending if all(r in self._by_name for r in c.requires)]
            # nothing ready: the first one's own error names the unknown (or circular) requirement
            for cfg in ready or pending[:1]:
                self._register(cfg)
                added.append(cfg)
            pending = [c for c in pending if c not in added]
        return added

    def add_one(self, source: MarketSource) -> MarketConfig:
        """Like `add`, for a source that must yield exactly one config (never a directory)."""
        configs = self.add(source)
        if len(configs) != 1:
            raise ConfigError(f"add_one({source!r}) yielded {len(configs)} configs, expected exactly 1")
        return configs[0]

    def __getitem__(self, name: str) -> MarketConfig:
        return self._by_name[name]

    def __contains__(self, name: object) -> bool:
        return name in self._by_name

    def __len__(self) -> int:
        return len(self._by_name)

    def names(self) -> List[str]:
        return sorted(self._by_name)

    def unknown(self, name: str) -> str:
        """The "not a registered market provider" text, with the registered names and a
        did-you-mean; shared with `AssetRegistry`, which checks an asset's `market.provider`."""
        return f"{name!r} is not a registered market provider (registered: {self.names()}){_suggest(name, self._by_name)}"

    def _register(self, cfg: MarketConfig) -> None:
        if cfg.name in self._by_name:
            raise ConfigError(f"duplicate market provider name {cfg.name!r}", asset=cfg.name)
        for required in cfg.requires:
            if required not in self._by_name:
                raise ConfigError(f"requires {self.unknown(required)}; register it first (a circular requires: can never load)", asset=cfg.name, key="requires")
        self._by_name[cfg.name] = cfg
