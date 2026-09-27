"""Public entry points: `load`, `build`, `run` (config path or dict -> BacktestResult)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Union

from .config import yamlio
from .config.loader import Built, build as _build

Source = Union[str, Path, Mapping[str, Any]]


def load(source: Source, *, sets: Sequence[str] = (), interpolate_env: bool = True) -> Dict[str, Any]:
    """Load a config path (YAML/JSON, `extends:` resolved) or dict, apply `--set a.b=c` overrides and ${ENV} interpolation."""
    if isinstance(source, Mapping):
        cfg: Dict[str, Any] = dict(source)
    else:
        cfg = yamlio.load_file(source)
    cfg = yamlio.apply_overrides(cfg, sets)
    return yamlio.interpolate(cfg) if interpolate_env else cfg


def build(source: Source, *, sets: Sequence[str] = (), stack: Sequence[Source] = ()) -> Built:
    """Build a config; each `stack` (a path or a mapping) is an overlay that may set only instruments.<n>.factory / .bind and market.pricers.<r>.wrap."""
    cfg = load(source, sets=sets)
    base = Path(source).resolve().parent if not isinstance(source, Mapping) else Path(".")
    return _build(cfg, base_dir=base, stack=[load(s) for s in stack])


def run(source: Source, *, sets: Sequence[str] = (), out: Optional[Union[str, Path]] = None, tearsheet: bool = False, stack: Sequence[Source] = ()) -> Any:
    """Build and run; returns pricebt.results.BacktestResult. `out` (or config `outputs.dir`) receives parquet + manifest (+ tearsheet)."""
    built = build(source, sets=sets, stack=stack)
    result = built.run()
    target = out or built.outputs.get("dir")
    if target:
        Path(target).mkdir(parents=True, exist_ok=True)
        if built.outputs.get("parquet", True):
            result.to_parquet(Path(target))
        if tearsheet or built.outputs.get("tearsheet"):
            result.tearsheet(Path(target) / "tearsheet")
    return result
