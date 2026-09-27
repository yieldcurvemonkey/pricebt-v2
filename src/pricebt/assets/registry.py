"""AssetRegistry: name -> config, instrument -> config matching, market-key sharing rules
(DESIGN.md sections 4.4 'Market sharing' and 5.3).

Loading and compiling one config is `assets.config.load_asset`'s job; this module only tracks
already-loaded `AssetConfig` objects and enforces the two registration-time rules: no duplicate
asset names, and assets that share a `market.key` must be textually identical where it matters.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Union

from ..errors import ConfigError
from .config import AssetConfig, load_asset

# A source `AssetRegistry.add` accepts: a yaml path, a directory of *.yaml, an in-memory mapping,
# or an already-loaded AssetConfig.
AssetSource = Union[str, Path, Mapping[str, Any], AssetConfig]

# The market-sharing fields that must be byte-identical across assets with the same market.key,
# in the order they are checked (DESIGN.md section 4.4).
_SHARED_MARKET_FIELDS = (
    ("imports", "imports_src"),
    ("code", "code_src"),
    ("market.expr", "market_expr"),
)


class AssetRegistry:
    def __init__(self) -> None:
        self._by_name: Dict[str, AssetConfig] = {}
        # market_key -> the first-registered AssetConfig with that key (its namespace is the one a
        # shared market is evaluated in, per DESIGN.md section 4.4).
        self._market_keys: Dict[str, AssetConfig] = {}

    def add(self, source: AssetSource) -> List[AssetConfig]:
        """Add one config (a path, a dict, or an `AssetConfig`), or every `*.yaml` in a directory.
        Returns the configs added, in registration order."""
        if isinstance(source, AssetConfig):
            configs = [source]
        elif isinstance(source, Mapping):
            configs = [load_asset(source)]
        else:
            path = Path(source)
            configs = [load_asset(p) for p in sorted(path.glob("*.yaml"))] if path.is_dir() else [load_asset(path)]
        for cfg in configs:
            self._register(cfg)
        return configs

    def add_one(self, source: AssetSource) -> AssetConfig:
        """Like `add`, for a source that must yield exactly one config (never a directory)."""
        configs = self.add(source)
        if len(configs) != 1:
            raise ConfigError(f"add_one({source!r}) yielded {len(configs)} configs, expected exactly 1")
        return configs[0]

    def __getitem__(self, name: str) -> AssetConfig:
        return self._by_name[name]

    def __contains__(self, name: object) -> bool:
        return name in self._by_name

    def __len__(self) -> int:
        return len(self._by_name)

    def names(self) -> List[str]:
        return sorted(self._by_name)

    def evaluating_asset(self, market_key: str) -> str:
        """The name of the first-registered asset with this `market.key` -- a shared market is
        always evaluated in that asset's namespace (DESIGN.md section 4.4)."""
        return self._market_keys[market_key].name

    def _register(self, cfg: AssetConfig) -> None:
        if cfg.name in self._by_name:
            raise ConfigError(f"duplicate asset name {cfg.name!r}", asset=cfg.name)
        first = self._market_keys.get(cfg.market_key)
        if first is None:
            self._market_keys[cfg.market_key] = cfg
        else:
            for label, attr in _SHARED_MARKET_FIELDS:
                if getattr(first, attr) != getattr(cfg, attr):
                    raise ConfigError(
                        f"market key {cfg.market_key}: {label} differs between assets {first.name} and {cfg.name}",
                        asset=cfg.name,
                        key="market.key",
                    )
        self._by_name[cfg.name] = cfg

    @staticmethod
    def _norm(v: Any) -> str:
        """DESIGN.md section 5.3's comparison rule: compare `str(getattr(v, 'value', v)).upper()`,
        so a bare string and the coerced pricebt enum with the same meaning compare equal."""
        return str(getattr(v, "value", v)).upper()

    def _match_reason(self, cfg: AssetConfig, combined: Mapping[str, Any]) -> Optional[str]:
        """`None` if every `match:` rule of `cfg` holds on `combined`, else why it failed."""
        for key, expected in cfg.match.items():
            if key not in combined:
                return f"missing kwarg {key!r} (rule wants {self._norm(expected)!r})"
            actual = combined[key]
            if self._norm(actual) != self._norm(expected):
                return f"{key}={actual!r} (={self._norm(actual)!r}) != {self._norm(expected)!r}"
        return None

    def match(self, class_name: str, kwargs: Mapping[str, Any], explicit_asset: Optional[str] = None) -> AssetConfig:
        """Match an instrument to an asset (DESIGN.md section 5.3, verbatim)."""
        if explicit_asset is not None:
            cfg = self._by_name.get(explicit_asset)
            if cfg is None:
                raise ConfigError(f"unknown asset {explicit_asset!r}; registered: {self.names()}")
            return cfg

        candidates = [cfg for cfg in self._by_name.values() if cfg.instrument == class_name]
        matched: List[AssetConfig] = []
        failures: List[str] = []
        for cfg in candidates:
            combined = {**cfg.defaults, **kwargs}
            reason = self._match_reason(cfg, combined)
            if reason is None:
                matched.append(cfg)
            else:
                failures.append(f"{cfg.name}: {reason}")

        if len(matched) == 1:
            return matched[0]
        if len(matched) > 1:
            raise ConfigError(f"ambiguous: {class_name} matches {[c.name for c in matched]}; pass pricebt_asset=...")

        detail = "; ".join(failures) if failures else f"no asset declares instrument: {class_name!r}"
        raise ConfigError(f"no asset matches {class_name}({dict(kwargs)}): {detail}")
