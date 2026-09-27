# Versioning, provenance and concurrency

## 1. What pricebt records about a run, and what it does not (read, then executed)

`Engine._finalize` builds the run manifest; `BacktestResult.manifest` adds `config_hash` and `grid`; `to_parquet(dir)` writes it as `manifest.json` (keys, as printed by the driver below: `audit`,
`config_hash`, `engine_manifest`, `frames`, `grid_info`, `name`, `schema_version`, `settings`, `vectors`; the `engine_manifest` holds `elapsed_seconds`, `end`, `fetches`, `fill_lag`, `instruments`,
`memo_hits`, `n_points`, `n_positions`, `name`, `settings`, `start`). Per instrument it records the asset class, the factory path, the conventions (verbatim) and their digest, the market roles and the
`<id>@<version>` of every layer.

**There is no field for the version of the pricing library, the platform build, the environment profile or the data-set version.** Honest list of what exists:

| where | what it carries | is it persisted in the result? |
|---|---|---|
| `manifest.instruments.<n>.factory`, `.conventions`, `.conventions_digest`, `.layers` | your factory path and the shared conventions block | yes |
| `config_hash`, `base_hash` (`Built`) | sha256 prefix of the interpolated config; of the config without factory / bind / wrap | `config_hash` yes (manifest, tearsheet); `base_hash` in the tie-out header |
| audit `inputs` frame (`backtest.audit`) | per fetch: `ts`, `role`, snapshot `digest`, `stamp` | yes, with the audit |
| snapshot `provenance` (a free-form mapping on `MarketSnapshot`, `CurveSnapshot`, `FixingsSeries`, `CalendarData`, `QuoteSet`) | anything the provider puts there | NO: it is excluded from `digest()` (`compare=False`) and `SnapshotPricer.describe()`, which merges it, is called by nothing in the engine, the results or the tie-out (only defined) |
| `wrap` / pricer `describe()` | whatever your pricer reports | NO (same) |
| a `registry.setup` step | can write a file itself | only what YOU write |

So: **your driver records the environment**, next to the result, and you put the same facts in the snapshots' provenance so that a stored snapshot says where it came from. The driver below was executed on
the worked example (with `<LIB>` unresolved, so it printed the "unknown" branch; replace the one function that names your library). It needs pyarrow, an OPTIONAL extra of pricebt
(`pip install -e .[parquet]`; without it `to_parquet` raises `OptionalDependencyError: parquet persistence needs pyarrow (pip install pyarrow); it is an optional dependency of pricebt`):

```python
"""run_and_record.py: run a pricebt config and write the environment next to the result. pricebt's manifest has no field for the library version or the environment,
so the DRIVER records them (sha256 of what was consumed included). Run from the repository root."""
import hashlib
import json
import platform
import sys
from importlib import metadata
from pathlib import Path

from pricebt import api

BASE = "skills/pricebt-wire-external-library/example/config/acme_tieout_base.yaml"     # the base config
STACK = ["skills/pricebt-wire-external-library/example/config/acme_swap.yaml"]         # the stack overlay(s)
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "run_out")


def library_version() -> dict:
    # LIB: the one place that names your library's version; prefer package metadata, else `<lib>.__version__`, else the platform's own version call
    try:
        return {"library": "<LIB>", "version": metadata.version("<LIB>")}
    except metadata.PackageNotFoundError:
        return {"library": "<LIB>", "version": "unknown: not an installed distribution"}


built = api.build(BASE, stack=STACK)                    # validates and builds; nothing runs yet
result = built.run()
OUT.mkdir(parents=True, exist_ok=True)
result.to_parquet(OUT)                                   # writes manifest.json (config_hash, grid, engine manifest) and the frames
env = {
    **library_version(),
    "environment": "uat",                                # a profile name, never a host or a credential
    "python": platform.python_version(), "platform": sys.platform, "pricebt_config_hash": built.config_hash, "pricebt_base_hash": built.base_hash,
    "conventions_digests": {n: i["conventions_digest"] for n, i in result.manifest["instruments"].items()},
    "n_errors": result.n_errors,
}
(OUT / "environment.json").write_text(json.dumps(env, indent=1, sort_keys=True), encoding="utf8")
files = sorted(p.name for p in OUT.iterdir())
print("written:", [f for f in files if f in ("manifest.json", "environment.json")])
print(json.dumps(env, sort_keys=True))
```

Output (real): `written: ['environment.json', 'manifest.json']` and the environment as JSON: `conventions_digests`, `environment: uat`, `library: <LIB>`, `n_errors: 0`, `platform`, `pricebt_base_hash`,
`pricebt_config_hash`, `python`, `version: unknown: not an installed distribution`.

What to record, always: the library name and exact version (and the platform build or release if it is a service), the environment profile (`uat`, `prod`: a name, never a host), the data-set or
curve-set version and the as-of range, the Python and pricebt versions (or the git revision of the repository), `config_hash` and `base_hash`, the digest of every recorded fixture (`files_sha256`, which `RecordedClient.write_provenance` computes), the
conventions digest, and `n_errors`. Two results are comparable only if these agree; the tie-out compares configs by hash and inputs by digest, never by library version, so a version change is invisible to
it unless you write it down.

**Pin.** A new pricebt extra for the library needs a version pin in `pyproject.toml`, and adding a runtime dependency or an extra's version pin is an "Ask first" item of `docs/design/11-refactor-spec.md` (Boundaries; `pricebt-guards-and-packaging`). A conventions document
(`docs/design/11-<lib>-conventions.md`) is valid for the versions it states in its header: when the library is upgraded, re-run the probe scripts, and the fact tests (`skills/pricebt-discover-the-library/references/probe-to-test.md`) fail where a
convention moved.

## 2. Concurrency: what the engine does and what it does not

* The engine is sequential: no thread, pool or lock exists in `src/pricebt` (searched). Positions are marked one after the other, layers flush one after the other, a tie-out runs its stacks one after the other
  IN THE SAME PROCESS. Two stacks therefore share every process-global of every library loaded (a valuation date, a fixings store, a calendar registry, a session).
* So a process-global setting needs a GUARD around each library call that sets it and restores what was there (`_compat.guard` in every shipped adapter; the worked example's `guard` restores even "unset"),
  and nothing may survive a call. The guard is what makes two worlds (t0 and t1 in a layer flush) safe to build one after the other.
* If your platform client is used from more than one thread (a notebook with a background refresh, a service), the guard is not enough: serialise the adapter's calls with one `threading.RLock` around
  guard-and-call. That does not make the library thread-safe, it serialises YOUR use of it. pricebt itself never starts a thread, so a lock is a decision for the caller's environment, not for the engine.
* Never start a background thread from `configure`, a provider or a `wrap` (token refresh included): it breaks determinism and the order of calls the digests and the recordings depend on.
* Process-global registries keyed by NAME (calendars, indices): register content-addressed names (`PBT.<name>.<hash>`) and never `replace`: two snapshots with different holidays under one name must be able to
  live at once (`skills/pricebt-wire-external-library/example/acme_adapter/_compat.py: load_calendar`).
* A remote environment is per-process state on the server: release it (`remote_environment.py`), and never assume another process sees it.
