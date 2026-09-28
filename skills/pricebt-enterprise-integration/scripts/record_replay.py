"""Record / replay proxy for a remote pricing or data client (library-agnostic).

Wrap your platform client once, in the asset config's `code:` block:

    client = record_replay.wrap(connect(env="UAT"), store="recordings/usd_irs", mode="record")

- `mode="live"`: pass-through, no disk.
- `mode="record"`: call the live client and save every (method, args, kwargs) -> result to disk.
- `mode="replay"`: serve from disk only; a call that was never recorded raises `ReplayMiss`, so an
  offline run can never silently reach the network.

The mode can come from the environment: `mode=os.environ.get("PRICEBT_CLIENT_MODE", "replay")`.

Keys are a SHA-256 of a canonical repr of the method name and its arguments, so arguments must have
a stable repr (str, numbers, dates, tuples, dicts, dataclasses with a deterministic repr). Results
are pickled. **Only replay recordings you made yourself: unpickling runs code.** Recordings may hold
confidential market data, so keep them out of public repositories (add the store to .gitignore).

Results that are live objects (sockets, native handles) cannot be pickled. Record the *data* your
config needs (for example, a curve's node dates and discount factors) and rebuild the library
object from it in `code:` instead.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import pickle
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

__all__ = ["ReplayMiss", "RecordReplay", "wrap"]

MODES = ("live", "record", "replay")


class ReplayMiss(KeyError):
    """A call was made in replay mode that has no recording."""


def _canonical(obj: Any) -> str:
    if isinstance(obj, dict):
        return "{" + ",".join(f"{_canonical(k)}:{_canonical(v)}" for k, v in sorted(obj.items(), key=lambda kv: repr(kv[0]))) + "}"
    if isinstance(obj, (list, tuple)):
        return "[" + ",".join(_canonical(v) for v in obj) + "]"
    return repr(obj)


class RecordReplay:
    """Proxy one client object. Only methods named in `methods` (default: every public callable) are intercepted."""

    def __init__(self, client: Any, store: str | os.PathLike, mode: str = "replay", methods: Optional[Iterable[str]] = None):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
        if mode != "replay" and client is None:
            raise ValueError("a live client is required unless mode='replay'")
        self._client = client
        self._store = Path(store)
        self._mode = mode
        self._methods = set(methods) if methods is not None else None
        self.hits = 0
        self.misses = 0
        if mode == "record":
            self._store.mkdir(parents=True, exist_ok=True)

    @property
    def mode(self) -> str:
        return self._mode

    def _key(self, name: str, args: tuple, kwargs: dict) -> str:
        return hashlib.sha256(_canonical((name, args, kwargs)).encode("utf-8")).hexdigest()

    def _path(self, key: str) -> Path:
        return self._store / key[:2] / f"{key}.pkl"

    def _call(self, name: str, fn: Optional[Callable], args: tuple, kwargs: dict) -> Any:
        key = self._key(name, args, kwargs)
        path = self._path(key)
        if self._mode == "replay":
            if not path.exists():
                self.misses += 1
                raise ReplayMiss(f"no recording for {name}{args}{kwargs} under {self._store}")
            self.hits += 1
            with path.open("rb") as fh:
                return pickle.load(fh)
        result = fn(*args, **kwargs)
        if self._mode == "record":
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            with tmp.open("wb") as fh:
                pickle.dump(result, fh, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp, path)
        return result

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        intercept = self._methods is None or name in self._methods
        if self._mode == "replay":
            if not intercept:
                raise AttributeError(f"{name} is not recorded (replay mode has no live client)")
            return lambda *a, **k: self._call(name, None, a, k)
        target = getattr(self._client, name)
        if not intercept or not callable(target):
            return target
        return lambda *a, **k: self._call(name, target, a, k)


def wrap(client: Any, store: str | os.PathLike, mode: Optional[str] = None, methods: Optional[Iterable[str]] = None) -> RecordReplay:
    """`mode=None` reads `PRICEBT_CLIENT_MODE` (default 'replay', the safe choice for CI)."""
    return RecordReplay(client, store, mode or os.environ.get("PRICEBT_CLIENT_MODE", "replay"), methods)


def _main() -> int:
    parser = argparse.ArgumentParser(description="Inspect a record/replay store.")
    parser.add_argument("store")
    args = parser.parse_args()
    files = sorted(Path(args.store).rglob("*.pkl"))
    size = sum(f.stat().st_size for f in files)
    print(f"{len(files)} recordings, {size / 1e6:.2f} MB in {args.store}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
