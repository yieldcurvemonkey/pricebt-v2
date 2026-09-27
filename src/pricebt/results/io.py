"""Directory persistence of a BacktestResult: one parquet file per frame plus ``manifest.json`` (written last, atomically).

Layout::

    <dir>/equity.parquet positions.parquet trades.parquet orders.parquet errors.parquet events.parquet layers.parquet
    <dir>/manifest.json

The manifest holds ``schema_version``, name, config hash, grid info, the primitive engine settings, the engine manifest,
and per-frame row counts that `from_parquet` verifies. Its presence means the directory is complete. Settings that are
objects (the cash accrual model) are recorded as text only and load as ``None``. pyarrow is imported lazily.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Union

import pandas as pd

from ..engine.state import EngineSettings, RunRecord
from ..errors import OptionalDependencyError
from .errors import ResultIntegrityError
from .result import BacktestResult

SCHEMA_VERSION = 1
FRAMES = ("equity", "positions", "trades", "orders", "errors", "events", "layers")
_ATTR = {"layers": "layers_by_position"}
_SETTINGS_TUPLES = ("layers", "measures", "vector_measures", "record_signals", "audit_measures")


def _require_pyarrow() -> None:
    try:
        import pyarrow  # noqa: F401
    except ImportError as e:
        raise OptionalDependencyError("parquet persistence needs pyarrow (pip install pyarrow); it is an optional dependency of pricebt") from e


def _settings_json(s: EngineSettings) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in vars(s).items():
        if k == "cash_accrual":
            out[k] = None if v is None else repr(v)
        else:
            out[k] = list(v) if isinstance(v, tuple) else v
    return out


def _settings_from_json(d: Dict[str, Any]) -> EngineSettings:
    kw = {k: (tuple(v) if k in _SETTINGS_TUPLES else v) for k, v in d.items() if k != "cash_accrual"}
    kw["show_progress"] = False
    return EngineSettings(**kw)


def _encode(name: str, df: pd.DataFrame) -> pd.DataFrame:
    if name == "events" and "detail" in df.columns:
        df = df.copy()
        df["detail"] = df["detail"].map(lambda d: json.dumps(d, default=str, sort_keys=True))
    return df


def _decode(name: str, df: pd.DataFrame) -> pd.DataFrame:
    if name == "events" and "detail" in df.columns:
        df = df.copy()
        df["detail"] = df["detail"].map(json.loads)
    return df


def to_parquet(result: BacktestResult, directory: Union[str, os.PathLike]) -> Path:
    """Write `result` to `directory` (created if missing); returns the directory."""
    _require_pyarrow()
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    manifest_path = d / "manifest.json"
    if manifest_path.exists():
        manifest_path.unlink()
    rows: Dict[str, int] = {}
    for name in FRAMES:
        df = getattr(result.record, _ATTR.get(name, name))
        _encode(name, df).to_parquet(d / f"{name}.parquet", index=True)
        rows[name] = int(len(df))
    vec_rows: Dict[str, int] = {}
    for vname, vdf in result.record.vectors.items():
        vdf.astype(float).to_parquet(d / f"vector_{vname}.parquet", index=True)
        vec_rows[vname] = int(len(vdf))
    audit_rows: Dict[str, int] = {}
    for aname, adf in result.record.audit.items():
        adf.to_parquet(d / f"audit_{aname}.parquet", index=False)
        audit_rows[aname] = int(len(adf))
    body = {
        "audit": audit_rows,
        "schema_version": SCHEMA_VERSION,
        "name": result.name,
        "config_hash": result.config_hash,
        "grid_info": result.grid_info,
        "settings": _settings_json(result.record.settings),
        "engine_manifest": result.record.manifest,
        "frames": rows,
        "vectors": vec_rows,
    }
    tmp = d / "manifest.json.tmp"
    tmp.write_text(json.dumps(body, indent=2, sort_keys=True, default=str) + "\n", encoding="utf8")
    os.replace(tmp, manifest_path)
    return d


def from_parquet(directory: Union[str, os.PathLike]) -> BacktestResult:
    """Load a directory written by `to_parquet`; raises `ResultIntegrityError` when incomplete or inconsistent."""
    _require_pyarrow()
    d = Path(directory)
    mpath = d / "manifest.json"
    if not mpath.exists():
        raise ResultIntegrityError(f"{d}: no manifest.json (missing or incomplete result directory)")
    try:
        body = json.loads(mpath.read_text(encoding="utf8"))
    except json.JSONDecodeError as e:
        raise ResultIntegrityError(f"{mpath}: unreadable manifest ({e})") from e
    if body.get("schema_version") != SCHEMA_VERSION:
        raise ResultIntegrityError(f"{mpath}: schema_version {body.get('schema_version')!r} != {SCHEMA_VERSION}")
    frames: Dict[str, pd.DataFrame] = {}
    for name in FRAMES:
        f = d / f"{name}.parquet"
        if not f.exists():
            raise ResultIntegrityError(f"{d}: missing {f.name}")
        df = _decode(name, pd.read_parquet(f))
        if len(df) != body["frames"][name]:
            raise ResultIntegrityError(f"{f}: {len(df)} rows, manifest says {body['frames'][name]}")
        frames[name] = df
    vectors: Dict[str, pd.DataFrame] = {}
    for vname, n in (body.get("vectors") or {}).items():
        f = d / f"vector_{vname}.parquet"
        if not f.exists():
            raise ResultIntegrityError(f"{d}: missing {f.name}")
        vdf = pd.read_parquet(f)
        if len(vdf) != n:
            raise ResultIntegrityError(f"{f}: {len(vdf)} rows, manifest says {n}")
        vectors[vname] = vdf
    audit: Dict[str, pd.DataFrame] = {}
    for aname, n in (body.get("audit") or {}).items():
        f = d / f"audit_{aname}.parquet"
        if not f.exists():
            raise ResultIntegrityError(f"{d}: missing {f.name}")
        adf = pd.read_parquet(f)
        if len(adf) != n:
            raise ResultIntegrityError(f"{f}: {len(adf)} rows, manifest says {n}")
        audit[aname] = adf
    record = RunRecord(
        settings=_settings_from_json(body["settings"]), equity=frames["equity"], positions=frames["positions"], trades=frames["trades"],
        orders=frames["orders"], errors=frames["errors"], events=frames["events"], layers_by_position=frames["layers"], manifest=body["engine_manifest"], vectors=vectors, audit=audit,
    )
    return BacktestResult(record, config_hash=body["config_hash"], grid_info=body["grid_info"])
