# Offline tests, live tests, and keeping proprietary data out of the repository

## 1. The two kinds of test, and the default run

| kind | needs | runs | marker | must skip with |
|---|---|---|---|---|
| offline | recorded, sanitised answers (or a fixture snapshot); NO licence, NO network, NO credentials | in the default run of the project, and in CI | `adapter_<lib>` (the partition marker of the library) | `pytest.importorskip("<lib>")` for the parts that need the in-process library; nothing for pure replay |
| live | the real platform, a licence, credentials, the network | only when a person opts in | `adapter_<lib>` (the SAME marker) | `pytest.mark.skipif(<env var not set>, reason="live platform: set PRICEBT_LIVE_<LIB>=1 ...")` |

An UNMARKED test is a collection error (`tests/guards/partition.py`: "every test must carry one of the partition markers"), and a test marked `core` that needs a library, a fixture or a live checkout is a
"partition conflict". So a new library needs ONE marker registered in THREE places, exactly as `pricebt-guards-and-packaging` says: `pytest.ini` `markers`, the `PARTITIONS` tuple of `tests/guards/partition.py`
(add `adapter_<lib>`; entries after the first count as "needs something" automatically) and the pinned tuple asserted in `tests/guards/test_partition.py`. Live tests use the same marker and opt in by an environment variable.

## 2. The recorded client (executed, tested, mutation-checked)

`references/recorded_client.py` wraps any client whose methods take keyword arguments and return JSON-serialisable data. One file per distinct request, named by the digest of the request; the digest ignores
key order, date spelling and every key whose NAME CONTAINS a word of `SECRET_KEYS` (case-insensitive, `-` and `_` ignored: `token`, `access_token`, `client_secret`, `X-Api-Key`, `passwd`, `user_name` all match), so a recording
made with a credential replays in CI without one. The tuple is a FLOOR, not a guarantee: add your platform's own credential parameter names with `secret_keys=` (every matching key is also dropped from the digest, so a business
key such as `session_date` would collide: narrow the tuple for such a platform), and list the literal secret VALUES in `secrets=[...]`, which is the real guard because a service can echo a token under any key.

* `replay` (default): answer from disk; a request that was never recorded raises `MarketDataUnavailable("... no recorded answer for npv({...}) ... (digest ...); record it with the live platform")`. It never
  returns an empty or invented answer.
* `record`: call the live client, sanitise (`redact` by key name, plus a scrub of the literal secret VALUES the process holds, `secrets=[...]`), save atomically, and continue with exactly what was saved
  (record and replay give the same numbers). **`record` only ADDS requests that are not on disk yet: a request already recorded is replayed and the live client is never called** (executed: after a platform upgrade,
  `record` kept answering with the OLD numbers). To refresh, delete the recording directory (or the one file), then record; the live test of section 3 is what tells you a refresh is due.
* `live`: always call, never write: the smoke test.
* `mode_from_env("PRICEBT_LIVE_<LIB>")`: unset -> replay; `record` -> record; anything else -> live.
* Counters `n_live`, `n_replayed`: the evaluation count of a run is a number you measure (`cost-budget.md`).
* `write_provenance(**facts)`: writes `PROVENANCE.json` with exactly the facts you pass (secret-named keys redacted) plus `files_sha256`, one sha256 per recording, computed at the time of the call.

Its own check (`python recorded_client.py`, needs `PYTHONPATH=src`): the digest is independent of order and date spelling and separates methods and arguments; it ignores eleven spellings of a credential parameter and
keeps a business key (`author`); a recording replays without a live client; a secret never reaches the file, even echoed under another key, and a credential under a secret-named key that is NOT listed in `secrets=[...]`
is still redacted; an unrecorded request raises; provenance keeps the facts and the hashes. Mutation check: thirteen one-line mutants of the file (digest order, credential in the digest, method ignored, a miss
returning `None`, record ignoring the disk, no literal scrub, no key-name sanitiser in the record path, `redact()` matching nothing, provenance not redacted, exact instead of substring key match, separators kept in the key,
`files_sha256` of nothing, `secret_keys=` ignored by the digest) were applied one at a time: 13 killed, 0 survived.

What to record for a tie-out: the SNAPSHOT inputs your provider fetched (curves, fixings, calendars), and the platform's answers for the trades of the base config. Replay then feeds the reference stack and
your adapter from the same recorded inputs (`L0` `snapshot_digests` `exact`), and the tie-out runs offline.

## 3. A test file (executed in a scratch copy of the repository layout: `2 passed, 1 skipped`; with `PRICEBT_LIVE_ACME=1`: `3 passed`)

Layout: copy `references/recorded_client.py` to `tests/support/recorded_client.py`. `tests/support/` is a PACKAGE (it has an `__init__.py`) and `tests/` is on the path (`pytest.ini`: `pythonpath = . src tests`),
so a test imports it as `support.recorded_client`, exactly as the repository's own tests import `support.common`. A bare `from recorded_client import ...` fails with `ModuleNotFoundError: No module named 'recorded_client'`
(the directory `tests/support/` is not itself on the path); use it only if you put the file directly under `tests/`.

```python
"""tests/test_<lib>_offline.py: offline replay in the default run, a live smoke test that is opt-in and SKIPS with a reason."""
import pytest

from pricebt.errors import MarketDataUnavailable
from support.recorded_client import RecordedClient, mode_from_env  # tests/support/recorded_client.py, see above

pytestmark = pytest.mark.adapter_acme  # LIB: the partition marker of the library; offline tests need NO licence and NO network


class FakeLive:  # stand-in for the platform's client; in the repository the recordings are COMMITTED files under tests/fixtures/<lib>_recorded/
    def npv(self, trade_id, as_of):
        return {"npv": 1234.5, "trade": trade_id, "as_of": as_of}


@pytest.fixture(scope="module")
def recordings(tmp_path_factory):
    d = tmp_path_factory.mktemp("recorded")
    rec = RecordedClient(d, live=FakeLive(), mode="record")
    rec.npv(trade_id="T1", as_of="2024-03-04")
    return d


def test_replay_answers_from_disk_without_a_live_client(recordings):
    rc = RecordedClient(recordings, mode="replay")
    assert rc.npv(trade_id="T1", as_of="2024-03-04")["npv"] == pytest.approx(1234.5) and rc.n_live == 0


def test_a_request_that_was_never_recorded_is_an_error_not_an_answer(recordings):
    with pytest.raises(MarketDataUnavailable, match="no recorded answer"):
        RecordedClient(recordings, mode="replay").npv(trade_id="T2", as_of="2024-03-04")


@pytest.mark.live_acme  # LIB: registered in pytest.ini `markers` and in tests/guards/partition.py PARTITIONS (pricebt-guards-and-packaging)
@pytest.mark.skipif(mode_from_env("PRICEBT_LIVE_ACME") == "replay", reason="live platform: set PRICEBT_LIVE_ACME=1 (=record adds recordings for requests not on disk yet; delete the directory to refresh); needs a licence, a network and credentials")
def test_live_answer_equals_the_recording(recordings):
    live = RecordedClient(recordings, live=FakeLive(), mode="live")
    assert live.npv(trade_id="T1", as_of="2024-03-04")["npv"] == pytest.approx(RecordedClient(recordings, mode="replay").npv(trade_id="T1", as_of="2024-03-04")["npv"])
```

The skip is printed with its reason by `pytest -rs`: `SKIPPED ...: live platform: set PRICEBT_LIVE_ACME=1 (=record adds recordings for requests not on disk yet; delete the directory to refresh); needs a licence, a network and credentials`.
A skip with no reason is a skip nobody can act on: always give one. In the repository the recordings are files under `tests/fixtures/<lib>_recorded/` (or `data/`, next section) and the `recordings` fixture is replaced by that
directory. The mode of a RECORDING run is `PRICEBT_LIVE_<LIB>=record` with the live client built from `<lib>_adapter.session.client()`; it adds the requests that are not on disk, so to refresh a stale store delete the
directory first.

## 4. Confidentiality and licence

* **Never commit proprietary data, credentials or anything derived from them you may not redistribute**: curves, fixings, prices, positions, trade ids, user names, host names, request logs, results.
  `outputs.dir` writes parquet, `manifest.json` and a tearsheet of a run made on the bank's data: those are confidential too (no shipped ignore file covers `results/`: add it to yours).
* **Sanitise fixtures before they exist**: record with `redact` and `secrets=[...]`; replace trade and counterparty identifiers by neutral ones (`T1`); prefer a SYNTHETIC market
  (`pricebt.testing.synthetic.SyntheticMarket`, no data at all) for everything that does not need the real curve; keep only what the test needs (one date, a few tenors). Read every recorded file once by eye.
* **The `data/` pattern.** `data/.gitignore` is two lines, `*` and `!.gitignore`: nothing under `data/` is ever committed, README included. Data that may not be committed lives there (or under a directory you
  add to `.gitignore`), and the tests that need it carry a marker and SKIP WITH A REASON when it is absent (the shipped `fixtures` marker: "needs data/fixtures exported from the maintainer's data
  infrastructure; skips with a reason when absent"). What IS committed: the generator (`tools/export_fixtures.py` is the model: read-only on the source, deterministic, self-tested) and the document that
  says where the data comes from.
* **What a fixture directory says about itself.** On the maintainer's checkout `data/fixtures/` carries a `README.txt` (what generated the files, the third-party origin of each subset, which subsets may NOT be
  redistributed) and a `MANIFEST.json` (tool and its version, creation time, environment, sources, per dataset the windows and statistics, a vendor notice, a checksum file). Those are the maintainer's LOCAL, uncommitted files:
  the `data/.gitignore` above keeps them out, so a fresh clone does not have them. Write your own for a recorded store with `RecordedClient.write_provenance` (executed; the hash is of the one recording made):

  ```python
# fragment
  rc.write_provenance(library="<LIB> 4.2.1", environment="uat", recorded=dt.date(2024, 3, 4), sanitised="trade ids replaced by T1..Tn; credentials redacted by key name and by value",
                      licence="recordings must not leave the bank network: kept under an ignored directory")
  ```

  ```json
  {"environment": "uat", "files_sha256": {"a4330b2a6abbbff5b8953f16.json": "aadfd9ca...cb56"}, "library": "<LIB> 4.2.1", "licence": "recordings must not leave the bank network: kept under an ignored directory",
   "recorded": "2024-03-04", "sanitised": "trade ids replaced by T1..Tn; credentials redacted by key name and by value"}
  ```

  The environment is a PROFILE name (not a host), and `write_provenance` stores what you pass (a secret-named key is redacted) and computes only `files_sha256`.
* **Licence.** Ask the library's owner (questionnaire C6): may tests run in CI, may recorded answers be committed, may a run's results be shared? Until you know, treat the answer as no: record to an ignored
  directory, run tie-outs from it, commit the generator and the document only. The library's name stays OUT of core and out of shipped configs; the guards enforce it
  (`pricebt-guards-and-packaging`).
* **Logs.** Never log a request body, a token or a full URL with query parameters; log the method name, the digest and the timing.
