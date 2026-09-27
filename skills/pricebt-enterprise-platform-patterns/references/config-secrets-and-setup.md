# Where the session, the credentials and the clients live (and what a config may carry)

Rule: pricebt's core knows no session, no login, no client. Your adapter package owns them, and the ONLY doors from a config into your code are the ones below. Everything here was read from
`src/pricebt/config/yamlio.py`, `src/pricebt/config/loader.py`, `src/pricebt/registry.py` and executed.

## 1. The complete injection surface

| door | syntax | when it runs | what it can do |
|---|---|---|---|
| environment | `${VAR}` or `${VAR:-default}` in ANY string of the config | at load (`pricebt.api.load`, `interpolate_env=True` by default), after `extends:` and `--set` | replace text. Missing and no default: `[CFG-ENV] environment variable 'BANKDEMO_ENDPOINT' is not set and has no default` (executed); with a default the default is used |
| `registry.setup` | `registry: {allow: [<pkg>], setup: [{call: "<pkg>.session:configure", kwargs: {profile: uat}}]}` | in `loader.build`, after the overlays and BEFORE the market and the instruments are built, in order | call your function once: install the session, register the client, start the licence |
| `$call` / `$ref` | `{$call: "<pkg>:make_client", kwargs: {profile: uat}}` (call it with kwargs) or `{$ref: "<pkg>:Attr"}` (the object itself, NO kwargs) anywhere inside a component's kwargs | while that component is built | inject an object (or a call result); a failing call is `[CFG-CALL] <path>(**kwargs) failed: <Type>: <message>` (the foreign message is printed); kwargs next to `$ref` are `[CFG-UNKNOWN-KEY] ...: unexpected keys ['kwargs'] next to $ref (use $call for kwargs)` |
| provider | `market.mdps.<n>: {type: "<pkg>:Provider", kwargs: {...}, time: {...}}` | at build: `Provider(**kwargs)` | your provider receives its kwargs (and `time` as a dict or a `TimeMapping`) |
| `factory`, `wrap`, `type` | a plain dotted path `pkg.mod:Attr` | at build | resolve one callable. NO kwargs |
| `registry.import` | `import: [pkg.module]` | at build | side-effect imports, allow-listed |

All dotted paths are allow-listed: `pricebt` always, everything else only under `registry.allow` of the BASE config (an overlay cannot set `registry`). The text of a config never imports anything.

Consequences (each executed on the worked example):

* `wrap` and `factory` take no kwargs. A `$call` mapping where a `wrap` string belongs fails with the unhelpful `[CFG-ALLOW] market.pricers.primary.wrap: [CFG-ALLOW] module "{'$call'" is not under an allowed
  prefix ['pricebt', 'acme_adapter']`. So a `wrap` reaches its client through MODULE STATE installed by a `registry.setup` step (below), and `functools.partial` is a Python-only way.
* The provider is the natural owner of the data session (it is constructed with kwargs); the `wrap` is not. Two objects that need the same client both ask the adapter's `session.client()`.

## 2. The pattern: one `session.py`, one setup step, no secret in any file

```python
"""<lib>_adapter/session.py: the ONLY module that knows how to log in. A `registry.setup` step of the base config calls `configure`; the provider, the `wrap` and the factory call `client()`."""
import os

from pricebt.errors import ConfigError

_STATE = {"profile": None, "client": None}


def _connect(profile: str, token: str):  # LIB: open the platform session for `profile` with the credential; return the client object
    return {"profile": profile, "authenticated": True}


def configure(profile: str, token_env: str = "BANKDEMO_TOKEN") -> None:
    """Idempotent: the same profile twice keeps the first session (a re-run notebook cell must not log in again). The credential is read HERE, from the environment or a secret store."""
    if _STATE["client"] is not None and _STATE["profile"] == profile:
        return
    token = os.environ.get(token_env)
    if not token:
        raise ConfigError(f"no credential for profile {profile!r}: set {token_env} (login happens BEFORE a run; a run never prompts)", code="CFG-AUTH")
    _STATE.update(profile=profile, client=_connect(profile, token))


def client():
    if _STATE["client"] is None:
        raise ConfigError("no session: add `registry: {setup: [{call: '<lib>_adapter.session:configure', kwargs: {profile: <name>}}]}` to the BASE config", code="CFG-AUTH")
    return _STATE["client"]
```

Base config (the overlay of a stack never carries it):

```yaml
registry:
  allow: [<lib>_adapter]
  setup:
    - {call: "<lib>_adapter.session:configure", kwargs: {profile: uat}}     # a PROFILE NAME, never a URL with credentials, never a token
```

Executed (`registry.allow=[acme_adapter, acme_mistakes, bankdemo]` and the setup step given with `--set` to the worked example's base): the step ran once, before any market object was built:
`['uat'] {'profile': 'uat', 'authenticated': True}`. The credential was read by the function from the environment (`BANKDEMO_TOKEN`); replace that line by your secret store or a Kerberos
ticket. Login is done BEFORE the run: a run is non-interactive, so a prompt (MFA, password) must never be reachable from `configure`, `wrap` or a provider.

## 3. What happens to a value a config carries (so what NOT to put there)

The script that printed the lines below (save the `session.py` block of section 2 as `bankdemo.py` in a directory on `PYTHONPATH`, next to the worked example's directory; repository root):

```python
"""config_facts.py: what the config layer does with ${ENV}, `registry.setup` and literal binding kwargs (each printed line is a fact quoted below)."""
import os

import bankdemo  # the session.py block of section 2, saved as bankdemo.py
from pricebt import api
from pricebt.config import yamlio
from pricebt.errors import ConfigError
from pricebt.tieout.runner import disclose_bindings

try:
    yamlio.interpolate({"endpoint": "${BANKDEMO_ENDPOINT}"})
except ConfigError as e:
    print("missing variable :", e)
print("with a default   :", yamlio.interpolate({"endpoint": "${BANKDEMO_ENDPOINT:-uat}"}))

BASE = "skills/pricebt-wire-external-library/example/config/acme_tieout_base.yaml"
SETS = ["registry.allow=[acme_adapter, bankdemo]", "registry.setup=[{call: 'bankdemo:configure', kwargs: {profile: uat}}]"]  # the setup step is given with --set here; in real life it lives in the base file
os.environ.pop("BANKDEMO_TOKEN", None)
try:
    api.build(BASE, sets=SETS)
except ConfigError as e:
    print("no credential    :", e)
os.environ["BANKDEMO_TOKEN"] = "tok-A"
b1 = api.build(BASE, sets=SETS)
print("setup step ran   :", bankdemo._STATE["profile"], bankdemo.client())

SEC = ["market.mdps.rates.kwargs.seed=${SEED_LIKE_SECRET}"]  # a ${VAR} anywhere is substituted BEFORE the hash: a rotated value changes config_hash and base_hash
os.environ["SEED_LIKE_SECRET"] = "7"
h1 = api.build(BASE, sets=SEC)
os.environ["SEED_LIKE_SECRET"] = "8"
h2 = api.build(BASE, sets=SEC)
print("config_hash      : %s vs %s   base_hash %s vs %s" % (h1.config_hash, h2.config_hash, h1.base_hash, h2.base_hash))
print("the substituted value is in Built.cfg (memory):", h1.cfg["market"]["mdps"]["rates"]["kwargs"]["seed"], "->", h2.cfg["market"]["mdps"]["rates"]["kwargs"]["seed"])
d = disclose_bindings(b1)["usd_sofr_ois"]["dv01"]  # a literal in a `bind:` kwarg is printed by the tie-out report
print("disclosed binding:", {k: d[k] for k in ("target", "kwargs", "sign")})
```

```
missing variable : [CFG-ENV] environment variable 'BANKDEMO_ENDPOINT' is not set and has no default
with a default   : {'endpoint': 'uat'}
no credential    : [CFG-AUTH] no credential for profile 'uat': set BANKDEMO_TOKEN (login happens BEFORE a run; a run never prompts)
setup step ran   : uat {'profile': 'uat', 'authenticated': True}
config_hash      : 39a9238879c8456f vs a39e542214b82b82   base_hash abe548827004f36d vs 82650a38858f7fff
the substituted value is in Built.cfg (memory): 7 -> 8
disclosed binding: {'target': 'method:dv01', 'kwargs': {'ctx': '@ctx', 'tenors': ['3M', '6M', '1Y', '2Y', '3Y', '5Y', '7Y', '10Y', '15Y', '20Y', '30Y']}, 'sign': 1.0}
```

* **`${VAR}` is substituted BEFORE hashing.** `Built.cfg` (in memory, for the life of the process) holds the substituted text, and `config_hash` and `base_hash` (sha256 prefixes of the JSON of that dict)
  change when the value changes: above, a `seed` of 7 against 8 gives two different hashes. A secret in `${...}` would therefore sit in memory, rotate the run's identity, and can appear in any
  message that echoes the config (`CFG-CALL` echoes the foreign exception's text). Use `${VAR}` for non-secret selectors (a profile, an environment name, a data-set version) only.
* **The tie-out report prints every binding with its literals** (`disclose_bindings` in `src/pricebt/tieout/runner.py`: target, kwargs, scale, offset, sign, reduce, keys). A literal in a `bind:` kwarg is
  published in the report; never a secret, and think before an internal identifier.
* **Errors are persisted.** `BacktestResult.errors` (frame `errors.parquet`) and the tearsheet manifest carry the text of every recorded error and of the manifest. An exception message from a remote client
  can contain a URL, a user name, a token echo or a request body: sanitise it in `translate` (the error-mapping reference) before it becomes a pricebt error.
* **Files that are committed and printed**: configs, overlays, `tieout.tolerances` reasons, fixture READMEs. Assume a stranger reads them.

## 4. Sessions that expire, and clients that are shared

* Re-authenticate at most ONCE per failure inside the client wrapper, then raise (`ConfigError`, code `CFG-AUTH`): an endless retry hides a revoked entitlement. Do not refresh from a background thread
  (pricebt is single-threaded and deterministic; see `versioning-and-concurrency.md`). If re-authentication replaces the client object, the environments uploaded through the old one are not the new session's:
  `remote_environment.py`'s `configure(new_client)` drops its registry, so the next `wrap` of a snapshot uploads it again through the new client (its self-check runs that case).
* One client per process: `configure` is idempotent (calling it twice with the same profile returns the same client) so a notebook that re-runs a cell does not open a second session.
* Two stacks in one tie-out share the process and therefore the session: install it once in the base config's `registry.setup`, not in each overlay.
* The library may be BOTH pricing and data source: one `session.client()` serves the provider (curves, fixings, calendars) and the `wrap`/factory (pricing); keep the two uses in separate modules so that
  the recorded/offline mode can replace either (`offline-and-live-tests.md`).
