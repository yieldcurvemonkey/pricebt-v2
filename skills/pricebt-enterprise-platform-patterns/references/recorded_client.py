"""RecordedClient: record the answers of a remote pricing / market-data client once, replay them offline forever, keyed by a digest of the REQUEST.

Copy this file next to your adapter's tests (it needs only the standard library and `pricebt.errors`). It knows nothing about any platform: the client it wraps is any object
with methods that take keyword arguments and return JSON-serialisable data (numbers, strings, lists, dicts). Dates in a request are written as ISO strings.

    live = <your session's client>                                    # only ever built when PRICEBT_LIVE_<LIB> is set
    rc = RecordedClient("tests/fixtures/<lib>_recorded", live=live, mode=mode_from_env("PRICEBT_LIVE_<LIB>"))
    price = rc.npv(trade_id="T1", env="E1", as_of=dt.date(2024, 3, 4))    # same as rc.call("npv", trade_id=..., env=..., as_of=...)

Modes (one directory of `<digest>.json` files, one file per distinct request):
  replay  (default)  answer from disk; a request that was never recorded raises MarketDataUnavailable (never an empty or a made-up answer); the live client is not needed.
  record             answer from the live client, sanitise, and save; a request already on disk is REPLAYED (no second live call): `record` only ADDS requests not yet on disk.
                     To refresh after a platform or library upgrade, delete the directory (or the one file) first, then record.
  live               always call the live client, never write (a live smoke test).

Run `python recorded_client.py` for the self-check (it runs on a known answer first and proves it is not vacuous).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

from pricebt.errors import MarketDataUnavailable

# A request or response KEY is secret when it CONTAINS one of these (case-insensitive, '-', '_' and blanks ignored: `X-Api-Key`, `client_secret`, `passwd`, `user_name` all match).
# This is a FLOOR, not a guarantee: add YOUR platform's credential parameter names (`RecordedClient(secret_keys=...)`) and list the literal secret VALUES in `secrets=[...]`, which is the real guard.
# Every key that matches is dropped from the request digest, so a business key that contains one of these words (a `session_date`) would collide: narrow the tuple for such a platform.
SECRET_KEYS = ("token", "secret", "passw", "pwd", "credential", "apikey", "authorization", "bearer", "cookie", "session", "user", "host")


def _norm(s: Any) -> str:
    return re.sub(r"[-_\s]", "", str(s).lower())


def is_secret(key: Any, keys: tuple = SECRET_KEYS) -> bool:
    k = _norm(key)
    return any(_norm(x) in k for x in keys)


def _default(o: Any) -> Any:
    if isinstance(o, (dt.date, dt.datetime)):
        return o.isoformat()
    raise TypeError(f"{type(o).__name__} is not JSON serialisable: send ISO strings, numbers, lists and dicts")


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=_default, allow_nan=False)


def strip_secrets(obj: Any, keys: tuple = SECRET_KEYS) -> Any:
    """A copy of `obj` without any key that contains a word of `keys` (any nesting level, see `is_secret`)."""
    if isinstance(obj, Mapping):
        return {k: strip_secrets(v, keys) for k, v in obj.items() if not is_secret(k, keys)}
    if isinstance(obj, (list, tuple)):
        return [strip_secrets(v, keys) for v in obj]
    return obj


def request_digest(method: str, request: Mapping[str, Any], keys: tuple = SECRET_KEYS) -> str:
    """sha256 (24 hex) of the method name and the request WITHOUT its secret-named keys: independent of key order, of how a date was spelled (date object or ISO string) and of
    which credential the live run used, so a recording made with a token replays in CI without one."""
    return hashlib.sha256(canonical({"method": method, "request": json.loads(canonical(strip_secrets(request, keys)))}).encode("utf8")).hexdigest()[:24]


def redact(obj: Any, keys: tuple = SECRET_KEYS) -> Any:
    """A copy of `obj` with the value of every key that contains a word of `keys` (any nesting level, see `is_secret`) replaced by '<redacted>'."""
    if isinstance(obj, Mapping):
        return {k: "<redacted>" if is_secret(k, keys) else redact(v, keys) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact(v, keys) for v in obj]
    return obj


def mode_from_env(var: str) -> str:
    """`PRICEBT_LIVE_<LIB>` unset -> replay; `record` -> record; anything else set (for example 1) -> live."""
    v = os.environ.get(var, "").strip().lower()
    return "replay" if not v else ("record" if v == "record" else "live")


class RecordedClient:
    def __init__(self, directory: str, live: Any = None, mode: str = "replay", sanitise: Optional[Callable[[Any], Any]] = None, secrets: Sequence[str] = (), secret_keys: tuple = SECRET_KEYS):
        if mode not in ("replay", "record", "live"):
            raise ValueError(f"mode must be replay, record or live, got {mode!r}")
        if mode != "replay" and live is None:
            raise ValueError(f"mode {mode!r} needs the live client")
        self.secret_keys = secret_keys
        self.dir, self.live, self.mode, self.sanitise = Path(directory), live, mode, sanitise or (lambda o: redact(o, self.secret_keys))
        self.secrets = tuple(x for x in secrets if x)  # the literal secret VALUES this process holds (a token, a user name): scrubbed wherever they appear, whatever the key is called
        self.n_live = self.n_replayed = 0  # counters: the evaluation count of a run is a number you MEASURE (see the skill, section on cost)

    def __getattr__(self, method: str) -> Callable[..., Any]:  # rc.npv(...) == rc.call("npv", ...)
        if method.startswith("_"):
            raise AttributeError(method)
        return lambda **request: self.call(method, **request)

    def path(self, method: str, request: Mapping[str, Any]) -> Path:
        return self.dir / f"{request_digest(method, request, self.secret_keys)}.json"

    def call(self, method: str, **request: Any) -> Any:
        request = json.loads(canonical(request))  # what is digested, called and stored is ONE thing: dates are ISO strings, numbers are numbers
        p = self.path(method, request)
        if self.mode != "live" and p.exists():
            self.n_replayed += 1
            return json.loads(p.read_text(encoding="utf8"))["response"]
        if self.mode == "replay":
            raise MarketDataUnavailable(None, request, f"no recorded answer for {method}({canonical(request)}) in {self.dir} (digest {p.stem}); record it with the live platform")
        response = getattr(self.live, method)(**request)
        self.n_live += 1
        if self.mode == "record":
            text = json.dumps(self.sanitise({"method": method, "request": request, "response": response}), sort_keys=True, indent=1, default=_default, allow_nan=False)
            for x in self.secrets:
                text = text.replace(x, "<redacted>")
            self.dir.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.dir, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf8") as f:
                f.write(text)
            os.replace(tmp, p)  # atomic: a killed recording never leaves half a file
            response = json.loads(text)["response"]  # what was recorded is what this run continues with: record and replay give the same numbers
        return response

    def write_provenance(self, **facts: Any) -> Path:
        """PROVENANCE.json next to the recordings: exactly the facts you pass (library name and version, environment name (not a host), date recorded, who sanitised what), redacted by key name,
        plus `files_sha256` computed here over every recording in the directory. The fixture README points at it."""
        self.dir.mkdir(parents=True, exist_ok=True)
        p = self.dir / "PROVENANCE.json"
        files = {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(self.dir.glob("*.json")) if f.name != p.name}
        p.write_text(json.dumps({**redact(facts, self.secret_keys), "files_sha256": files}, sort_keys=True, indent=1, default=_default), encoding="utf8")
        return p


# ------------------------------------------------------------------------------ self-check
def _self_check() -> None:
    class Live:  # a stand-in for the platform: deterministic, counts its calls, and the answer depends on every argument
        calls = 0

        def npv(self, trade_id: str, env: str, as_of: str, token: str = "", **credentials: Any) -> Dict[str, Any]:
            Live.calls += 1
            return {"npv": 1000.0 * len(trade_id) + len(env) + int(as_of[-2:]), "echo": token}  # a service can echo a credential under ANY key: only the literal scrub (secrets=[...]) catches this one

    with tempfile.TemporaryDirectory() as d:
        # known answer first: the digest ignores key order and date spelling, and separates different requests
        a = request_digest("npv", {"trade_id": "T1", "as_of": dt.date(2024, 3, 4)})
        assert a == request_digest("npv", {"as_of": "2024-03-04", "trade_id": "T1"}), "digest must not depend on order or date spelling"
        assert a != request_digest("npv", {"trade_id": "T2", "as_of": "2024-03-04"}) and a != request_digest("price", {"trade_id": "T1", "as_of": "2024-03-04"})
        # a credential parameter, however the platform spells it, is not part of the digest; a business key that merely looks similar is
        for k in ("token", "Authorization", "access_token", "client_secret", "x-api-key", "apikey", "passwd", "user_name", "sessionid", "bearer", "credentials"):
            assert a == request_digest("npv", {"trade_id": "T1", "as_of": "2024-03-04", k: "v"}), f"the digest must ignore the credential parameter {k!r}"
            assert redact({k: "v"}) == {k: "<redacted>"}, f"{k!r} must be redacted on disk"
        assert a != request_digest("npv", {"trade_id": "T1", "as_of": "2024-03-04", "author": "x"}), "'author' is a business key here: it is not a secret"
        mine = RecordedClient(d, secret_keys=("tenant",))  # a platform's own credential parameter, added to the floor
        assert mine.path("npv", {"trade_id": "T1", "tenant": "x"}) == mine.path("npv", {"trade_id": "T1", "tenant": "y"}) != mine.path("npv", {"trade_id": "T2", "tenant": "x"})

        rec = RecordedClient(d, live=Live(), mode="record", secrets=["s3cret"])
        r1 = rec.npv(trade_id="T1", env="E1", as_of=dt.date(2024, 3, 4), token="s3cret")  # a date object goes in, the live client receives the ISO string
        assert r1["npv"] == 2000 + 2 + 4 and Live.calls == 1 and rec.n_live == 1
        assert rec.npv(trade_id="T1", env="E1", as_of="2024-03-04", token="s3cret") == r1 and Live.calls == 1, "a recorded request is replayed, not called again"
        on_disk = "".join(p.read_text(encoding="utf8") for p in Path(d).glob("*.json"))
        assert "s3cret" not in on_disk and "<redacted>" in on_disk, "a secret must never reach the fixture"
        rec2 = RecordedClient(d, live=Live(), mode="record")  # NO secrets=[...] here: a secret-NAMED key is still redacted by its name alone
        rec2.npv(trade_id="T3", env="E1", as_of="2024-03-04", password="p4ss", access_token="t0k3n")
        on_disk = "".join(p.read_text(encoding="utf8") for p in Path(d).glob("*.json"))
        assert "p4ss" not in on_disk and "t0k3n" not in on_disk, "a credential under a secret-named key must not reach the fixture, even when it is not listed in secrets=[...]"

        rep = RecordedClient(d, mode="replay")  # no live client at all: this is what CI runs
        assert rep.npv(trade_id="T1", env="E1", as_of="2024-03-04") == {"npv": 2006, "echo": "<redacted>"} and rep.n_live == 0, "replay needs no credential"
        try:
            rep.npv(trade_id="T9", env="E1", as_of="2024-03-04")
        except MarketDataUnavailable as e:
            assert "no recorded answer" in str(e) and "T9" in str(e)
        else:
            raise AssertionError("a request that was never recorded must not be answered")
        assert mode_from_env("PRICEBT_DEFINITELY_UNSET_VAR") == "replay"
        RecordedClient(d, mode="replay").write_provenance(library="<lib> 1.2.3", environment="uat", recorded=dt.date(2024, 3, 4), token="not-stored")
        prov = json.loads((Path(d) / "PROVENANCE.json").read_text(encoding="utf8"))
        assert "not-stored" not in json.dumps(prov) and prov["library"] == "<lib> 1.2.3", "provenance keeps the facts you pass and redacts secret-named ones"
        first = sorted(Path(d).glob("*.json"))[0]
        assert prov["files_sha256"][first.name] == hashlib.sha256(first.read_bytes()).hexdigest() and len(prov["files_sha256"]) == 2, "one sha256 per recording, PROVENANCE.json itself excluded"
    print("recorded_client self-check ok")


if __name__ == "__main__":
    _self_check()
