---
name: pricebt-enterprise-integration
description: Patterns for hooking pricebt up to a bank's remote pricing/data platform (Citi-Datapoint-style, Athena-style, in-house services) - sessions and secrets, point-in-time market data, record/replay for offline and CI runs, batching and caching, failure semantics, confidentiality, and the safety guards learned the hard way. Use when your library is a service, needs credentials, or can write to shared stores.
---

# Enterprise integration patterns

Bank platforms are rarely a pure function of `(trade, date)`. They have sessions, entitlements, environments (UAT/PROD), latency, rate limits, and caches that can *write*. This skill is about wiring such a platform into an asset config **safely and reproducibly**. The asset-config contract itself is in [`pricebt-connect-pricing-library`](../pricebt-connect-pricing-library/SKILL.md).

## When to use / not use

- **Use** when the library is remote, authenticates, bills per call, or keeps state (caches, stores, global settings).
- **Skip** for a pure in-process library (QuantLib-style) with local data. Just follow the connect skill.

## Inputs and outputs

- **Inputs:** platform documentation, a sandbox or UAT entitlement, and a list of the calls the config needs (market handle for a date, trade construction, measures).
- **Outputs:**
  - an asset config whose `imports:`/`code:` block owns one client per process;
  - an explicit, date-bounded safety envelope;
  - a record/replay setup so CI and colleagues can re-run without credentials.

## Procedure

1. **Keep bank-specific files out of this repository.** It is public. Put your asset configs, recordings and helper modules in a private repository or folder, and pass their paths in: `PricebtSession.use(assets=[r"<private>/configs/usd_irs.yaml"])`. pricebt accepts any path.
2. **Keep secrets out of configs.** Credentials come from the environment or a keyring, read inside `code:` (`os.environ["PLATFORM_TOKEN"]`). Never write a token into YAML, a notebook or a recording key. Pin the environment explicitly (`env="UAT"`), never "default".
3. **Use one client per process.** Construct the client once at module level in `code:`. The namespace runs once per process per asset. Never construct it inside `market.expr` (that runs once per date).
4. **Serve point-in-time data only.** `market.expr` must return the market *as it was known* at `pricebt_timestamp` (end of day on `pricebt_date` in the session's timezone, default 17:00 America/New_York; change it with `PricebtSession(eod_time=..., tz=...)`).
   - Ask the platform for an **as-of snapshot**, not "latest", and never a revised or restated series.
   - If the platform stamps snapshots in another timezone, convert before choosing the date.
   - Validate what was served: the returned snapshot's own reference date must equal the date you asked for. Raise if it doesn't (see `load_market` in `configs/assets/usd_sofr_ois_interest_rate_swap.yaml`).
5. **Separate "no data" from "error".**
   - Return `None` from `market.expr` for "no market on this date": holidays, gaps, dates before the history starts, dates after your safety envelope.
   - Let genuine errors raise (authentication, schema change, timeouts after retries). pricebt wraps them with the asset, key and date.
   - Catch the platform's specific "closed"/"no data" exceptions in a helper, never a bare `except Exception`.
6. **Bound the safety envelope.** Decide the date range your config may ever serve, and return `None` outside it:
   - no `today` (a live or intraday market, which may hit the network);
   - nothing past the last date your local caches cover.

   Write the reason in a comment next to the constant.
7. **Batch and cache.**
   - Price many trades in one call inside `portfolio_functions` (the engine already groups trades by asset, market and date).
   - Memoise expensive per-market derived objects (risk curves, calibrations) on the market object (`market.__dict__.setdefault("_memo", {})`) or in a dict keyed by date.
   - pricebt caches market objects per (market key, date, csa) and unit values per resolved terms, so a well-written config makes each platform call once per date.
8. **Record, then replay.** Wrap the client with [`scripts/record_replay.py`](scripts/record_replay.py):

   ```python
   # in the asset config code: block
   import sys; sys.path.insert(0, "skills/pricebt-enterprise-integration/scripts")
   import record_replay
   _CLIENT = record_replay.wrap(platform.connect(env="UAT"), store="<private>/recordings/usd_irs")   # mode from PRICEBT_CLIENT_MODE
   ```

   Run once with `PRICEBT_CLIENT_MODE=record` and entitlements. After that, `replay` (the default) serves every call from disk and raises `ReplayMiss` for anything unrecorded, so an offline run can never reach the network. Record *data* (curve nodes, fixings), not live handles, and rebuild the library object in `code:`.
9. **Budget the cost before a long run.** Calls ≈ dates × (1 market + trades × measures ÷ batch). Time one date with the checker ([`pricebt-verify-asset-config`](../pricebt-verify-asset-config/SKILL.md)) and multiply before you launch five years of daily history.
10. **Record versions.** Put the library version, platform environment, snapshot source and config file hash in the report's reproducibility section ([`pricebt-tearsheet-report`](../pricebt-tearsheet-report/SKILL.md)).

## Hazards seen in real bank libraries (and the guard for each)

These all happened with the repository's own real-world example (see [`docs/v2/research/06-arbs-pricing-api.md`](../../docs/v2/research/06-arbs-pricing-api.md) §6 and `docs/v2/LIVE_ARBS_REPORT.md`):

| Hazard | Symptom | Guard |
|---|---|---|
| A cache miss silently falls through to a network download | the run slows down; network traffic; a firewall alert | serve from a store-only API; return `None` on a miss; block sockets in a test (`socket.socket` monkeypatch) |
| A download **overwrites a shared store** | colleagues' data changes under them | never enable write-capable sources; check the store's modification time before and after a test run |
| "Today" means live data | a backtest ending today makes live calls | `if d >= date.today(): return None` |
| An environment flag is read once at import (e.g. a production-database switch) | setting it after an earlier import does nothing | set it before the first import; after importing, assert the flag took effect and raise otherwise |
| A process-global side effect (e.g. TLS verification disabled) | other code in the process is affected | avoid the code path; run risky probes in a subprocess |
| The served snapshot date differs from the requested date | silent look-ahead or stale data | validate the reference date in `load_market` and raise |
| Holidays raise instead of returning empty | the backtest aborts on the first holiday | catch that specific exception and return `None` |
| Units and signs differ from pricebt's | the P&L sign is wrong; dv01 is 100× off | convert in the config, and prove it with the checker's rates pack |

## Checks

- The asset checker passes with `PRICEBT_CLIENT_MODE=replay` and no credentials in the environment.
- A socket-blocked run of the smoke backtest succeeds (nothing reaches the network).
- `git status` in the public repository shows no bank config, recording or token.

## Pitfalls

- A recording keyed on an argument with an unstable `repr` (a live object, a float computed differently each run) never hits in replay. Pass plain data.
- A pickled recording of a vendor object can break when the vendor upgrades. Record raw data instead.
- Rate-limit retries inside `market.expr` hide outages. Log and count them, and fail the run if they exceed a threshold.

## Related skills

- [`pricebt-connect-pricing-library`](../pricebt-connect-pricing-library/SKILL.md): the asset config itself.
- [`pricebt-verify-asset-config`](../pricebt-verify-asset-config/SKILL.md): proving the config.
- [`pricebt-asset-config-cookbook`](../pricebt-asset-config-cookbook/SKILL.md): patterns for specific library shapes.
