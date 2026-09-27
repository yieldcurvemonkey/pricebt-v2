"""A snapshot becomes a REMOTE market environment: upload once per snapshot digest, release when the last pricer that uses it is gone.

The pattern (copy it into `<lib>_adapter/wrap.py`; `FakeRemote` stands in for your platform's client):

* the cache key of a market environment is `SnapshotPricer.digest` (sha256 of the snapshot's CONTENT: the same digest means the same numbers, so the same environment);
* `MarketData` calls `wrap(pricer)` once per snapshot and stamp and keeps the result in an LRU (`MarketData.maxsize`, default 32); `RemotePricer` shares one `RemoteEnv` per digest;
* nothing in pricebt calls a release hook: `MarketData.reset()` clears the memo and eviction pops it, so an environment is released only when Python drops the last reference.
  `weakref.finalize` turns that into a release call. Positions also hold their entry and previous pricers, so an environment lives as long as a position that priced on it.

Run `python remote_environment.py` for the self-check. It needs `pricebt` on the path (PYTHONPATH=src) and nothing else.
"""
from __future__ import annotations

import gc
import weakref
from typing import Any, Dict, Optional

from pricebt.errors import ConfigError
from pricebt.pricer import PricerBase


# ------------------------------------------------------------------------------ the platform (replace by your client)
class FakeRemote:
    """A remote market-environment service in 15 lines: upload(key, payload) -> env id, release(env id), price(env id, ...). It counts what crosses the wire."""

    def __init__(self) -> None:
        self.uploads, self.releases, self.live = [], [], set()

    def upload_market(self, key: str, payload: Dict[str, Any]) -> str:
        env_id = f"env-{len(self.uploads):04d}-{key[:8]}"
        self.uploads.append(env_id)
        self.live.add(env_id)
        return env_id

    def release_market(self, env_id: str) -> None:
        self.releases.append(env_id)
        self.live.discard(env_id)


def to_payload(snapshot: Any) -> Dict[str, Any]:
    """Snapshot -> what your platform accepts. Plain data only: dates as ISO strings, rates in the platform's own unit (convert HERE, once, and document it)."""
    return {"reference_date": snapshot.reference_date.isoformat(), "curves": {n: {"dates": [d.isoformat() for d in c.node_dates], "dfs": list(c.values)} for n, c in snapshot.curves.items()}}


# ------------------------------------------------------------------------------ the adapter side
_CLIENT: Optional[Any] = None


def configure(client: Any = None) -> None:
    """Install the process's client. Called from Python (tests) or from `registry.setup` in the base config (`{call: "<lib>_adapter:configure", kwargs: {...}}`): secrets never travel
    through a config, the adapter reads them from the environment or a secret store inside the function that builds the client."""
    global _CLIENT
    if client is not _CLIENT:
        _ENVS.clear()  # environments live on ONE client's server: after a re-authentication (a new client) a snapshot must be uploaded again, not served the old session's environment
    _CLIENT = client  # pricers that already hold an environment keep it (and its finalizer releases it on the client that created it)


def client() -> Any:
    if _CLIENT is None:
        raise ConfigError("no client installed: call <lib>_adapter.configure(client) or add a `registry.setup` step to the base config", code="CFG-WRAP")
    return _CLIENT


class RemoteEnv:
    """One remote environment per snapshot digest; released when the last RemotePricer holding it is garbage collected."""

    def __init__(self, c: Any, digest: str, snapshot: Any):
        self.digest, self.env_id = digest, c.upload_market(digest, to_payload(snapshot))
        weakref.finalize(self, c.release_market, self.env_id)  # holds the client and the id, NOT self


_ENVS: "weakref.WeakValueDictionary[str, RemoteEnv]" = weakref.WeakValueDictionary()


class RemotePricer(PricerBase):
    LOOKUPS = ()

    def __init__(self, source: Any, c: Any):
        snap = getattr(source, "snapshot", None)
        if snap is None:
            raise ConfigError(f"wrap needs a SnapshotPricer, got {type(source).__name__}", code="CFG-WRAP")
        self.ts, self.reference_date, self.digest = source.ts, source.reference_date, source.digest
        env = _ENVS.get(self.digest)
        if env is None:
            env = _ENVS[self.digest] = RemoteEnv(c, self.digest, snap)
        self.env = env  # a strong reference: the environment lives as long as this pricer

    def describe(self) -> Dict[str, Any]:
        return {"type": type(self).__name__, "ts": str(self.ts), "reference_date": str(self.reference_date), "digest": self.digest, "env_id": self.env.env_id}


def wrap(pricer: Any) -> RemotePricer:
    return pricer if isinstance(pricer, RemotePricer) else RemotePricer(pricer, client())


# ------------------------------------------------------------------------------ self-check
def _self_check() -> None:
    import pandas as pd

    from pricebt.market import Binding, MarketData
    from pricebt.testing.synthetic import SyntheticMarket

    fake = FakeRemote()
    configure(fake)
    mdp = SyntheticMarket(start="2024-01-02", end="2024-03-29", seed=7)
    md = MarketData({"primary": Binding(mdp, {}, wrap)}, maxsize=4)
    days = [pd.Timestamp(d + " 17:00", tz="America/New_York") for d in ("2024-03-04", "2024-03-05", "2024-03-06", "2024-03-07", "2024-03-08", "2024-03-11", "2024-03-12", "2024-03-13")]
    p0 = md.pricer(days[0])
    assert len(fake.uploads) == 1 and md.pricer(days[0]) is p0 and len(fake.uploads) == 1, "the same snapshot is uploaded once"
    assert p0.describe()["env_id"] == fake.uploads[0]
    del p0  # a variable holding a pricer keeps its environment alive, exactly as a position does
    for d in days[1:]:
        md.pricer(d)
    gc.collect()
    assert len(fake.uploads) == len(days), "one upload per distinct snapshot digest"
    assert len(fake.live) == 4 and len(fake.releases) == len(days) - 4, f"the LRU (maxsize=4) evicted the rest, and eviction released them: live {len(fake.live)}"
    held = md.pricer(days[-1])  # a position would hold its pricers like this
    md.reset()
    gc.collect()
    assert fake.live == {held.env.env_id}, "reset() drops the memo; only an environment somebody still holds stays alive"
    del held
    gc.collect()
    assert not fake.live and len(fake.releases) == len(fake.uploads), "every environment is released exactly once"
    assert len(set(fake.releases)) == len(fake.releases)
    a = mdp.get_pricer(days[0])  # two pricers of ONE snapshot (same digest, two stamps) share one environment
    from pricebt.snapshot import SnapshotPricer

    b = SnapshotPricer(a.snapshot, ts=a.ts + pd.Timedelta(minutes=1))
    n = len(fake.uploads)
    pa, pb = wrap(a), wrap(b)
    assert pa.digest == pb.digest and pa.env is pb.env and len(fake.uploads) == n + 1, "one environment per digest, not per stamp"
    keep = wrap(a)  # a session that is re-established: a NEW client, the same snapshot digest
    fresh = FakeRemote()
    configure(fresh)
    again = wrap(a)
    assert len(fresh.uploads) == 1 and again.env is not keep.env, "after configure(new client) the snapshot is uploaded through the NEW client"
    assert keep.env.env_id in fake.live, "the old pricer still holds its environment, on the client that created it"
    del pa, pb, keep, again
    gc.collect()
    assert not fake.live and not fresh.live and len(fake.releases) == len(fake.uploads) and len(fresh.releases) == len(fresh.uploads)
    print("remote_environment self-check ok:", len(fake.uploads) + len(fresh.uploads), "uploads,", len(fake.releases) + len(fresh.releases), "releases")


if __name__ == "__main__":
    _self_check()
