"""The ONLY module of the adapter that imports zeta, holds its client and translates its errors.

zeta (ZETA_DOCS.md) is a SERVICE: JSON-like dicts in and out, no zeta objects, no process-global state, no environment configuration, deterministic. So unlike an in-process library there
is NO valuation date to guard here; what this module owns instead is

* the client: markets belong to the client that uploaded them (ids are not portable between clients), so one module-level `Client` is created lazily and replaced only by `set_client`
  (a test that wants its own `client.stats`). Every wrapped pricer remembers the client it uploaded to;
* the upload counter `UPLOADS`: `snapshot` = markets uploaded because a snapshot was wrapped and priced (ONCE per snapshot, memoised on the wrapped pricer); `derived` = markets the layers
  build from a pair of snapshots (rolled and shocked worlds, one upload per distinct content). `client.stats["markets_uploaded"]` is the sum: the tests prove both;
* error translation: `price()` never raises for a service error, it answers `{"status": "ERROR", "code": ...}`, so a response is checked here, once, and turned into a pricebt error:
  Z530 (a required fixing is missing) -> MarketDataUnavailable; Z101 / Z204 / Z301 (bad request, unsupported tenor, unsorted curve) -> ConfigError; Z412 (unknown market, closed client) and
  Z500 (a zeta bug) -> MethodCallError. A closed client raises RuntimeError on upload: also MethodCallError.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import pandas as pd

from pricebt.errors import ConfigError, MarketDataUnavailable, MethodCallError, OptionalDependencyError

try:
    import zeta
except ImportError:  # the library is optional: core and the other adapters never need it
    # EXAMPLE ONLY: this repository keeps the fictional client next to the adapter (`../zeta_lib`), so one PYTHONPATH entry (the example-service directory) is enough. A real adapter has no
    # such fallback: the platform's client is installed, or its directory is on PYTHONPATH, and a missing client is the OptionalDependencyError below.
    _LIB = str(Path(__file__).resolve().parents[1] / "zeta_lib")
    if _LIB not in sys.path:
        sys.path.append(_LIB)
    try:
        import zeta
    except ImportError as e:
        raise OptionalDependencyError("zeta_adapter needs the service client `zeta` (it is not on PYTHONPATH)") from e

UPLOADS: Dict[str, int] = {"snapshot": 0, "derived": 0}
_CLIENT: Optional["zeta.Client"] = None


def client() -> "zeta.Client":
    """The module's zeta client (created on first use)."""
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = zeta.Client()
    return _CLIENT


def set_client(c: Optional["zeta.Client"]) -> None:
    """Install `c` (None: forget it, the next `client()` makes a new one). Markets already uploaded stay with the client that holds them."""
    global _CLIENT
    _CLIENT = c


def to_date(d: Any) -> dt.date:
    """A date from a date, a Timestamp or an ISO string."""
    if isinstance(d, pd.Timestamp):
        return d.date()
    if isinstance(d, dt.datetime):
        return d.date()
    if isinstance(d, dt.date):
        return d
    if isinstance(d, str):
        return dt.date.fromisoformat(d.strip()[:10])
    raise TypeError(f"cannot convert {d!r} to a date")


def iso(d: Any) -> str:
    """zeta's date spelling (an ISO STRING: a `datetime.date` is Z101)."""
    return to_date(d).isoformat()


def _raise(resp: Mapping[str, Any], ts: Any) -> None:
    code, msg = resp.get("code", "?"), resp.get("message", "")
    if code == "Z530":
        raise MarketDataUnavailable(ts, {}, f"zeta {code}: {msg}")
    if code in ("Z101", "Z204", "Z301"):
        raise ConfigError(f"zeta {code}: {msg}", code="CFG-ZETA")
    raise MethodCallError(f"zeta {code}: {msg}")


def upload(cli: "zeta.Client", market: Mapping[str, Any], ts: Any, kind: str) -> str:
    """Upload a market (`kind`: 'snapshot' or 'derived', for the counter) and return its id. zeta validates nothing here: a malformed market is reported by the first price()."""
    try:
        mid = cli.upload_market(dict(market))
    except RuntimeError as e:
        raise MethodCallError(f"zeta: {e}") from e
    UPLOADS[kind] += 1
    return mid


def price(cli: "zeta.Client", mid: str, as_of: Any, trades: List[Mapping[str, Any]], measures: List[str], ts: Any, scenario: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
    """One price() request, checked: the list of per-trade results, or a pricebt error."""
    req: Dict[str, Any] = {"market_id": mid, "as_of": iso(as_of), "trades": list(trades), "measures": list(measures)}
    if scenario:
        req["scenario"] = dict(scenario)
    try:
        resp = cli.price(req)
    except RuntimeError as e:
        raise MethodCallError(f"zeta: {e}") from e
    if resp.get("status") != "OK":
        _raise(resp, ts)
    return resp["results"]
