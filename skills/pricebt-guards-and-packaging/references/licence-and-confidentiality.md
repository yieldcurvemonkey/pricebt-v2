# Licence, confidentiality and secrets checklist for a new adapter

Answer every line from YOUR library's licence, your organisation's policy and the maintainer. Nothing below assumes anything about the library. Tick each item in the change-log entry of the adapter (`references/documents.md`).

## A. Licence

| # | Question | Action if the answer is "yes" / "unknown" |
|---|---|---|
| L1 | Is `<LIB>` open source with a licence compatible with redistribution next to Apache-2.0 code (pyproject `license`, `NOTICE`)? | Say so in the ADR and the conventions document. If "unknown": treat as NOT redistributable. |
| L2 | Is it proprietary, source-available or non-commercial (the shipped precedent: rateslib)? | Never vendor or copy any of its files. Add a sentence to `NOTICE` in the shipped style: "`<LIB>` is an optional dependency under its own licence (<terms>); it is not redistributed here." Spec Z6: no licence obligation may propagate into core. |
| L3 | Does importing it print a licence banner or warning (rateslib does)? | Filter it in the adapter's `_compat.py` (the module that owns every library global, spec A1) and, if it still reaches pytest, add one `filterwarnings` line to `pytest.ini` next to the rateslib ones. |
| L4 | Does it need a licence server, a seat or a token to run? | Then it cannot be in the default offline suite: see section C. |
| L5 | May its NAME, function names, docstrings or conventions appear in an Apache-2.0 tree? | If not: keep the adapter OUTSIDE the repository (external package plus `registry.allow`, see `SKILL.md`), and keep the ban entries (`tests/guards/banned.yaml` is plain data in an open tree) in a private branch or fork. |
| L6 | Is the market data it serves licensed (vendor data, internal data with a classification)? | Recorded snapshots derived from it are data under the same licence: never commit them. The repository keeps `data/` git-ignored (`data/.gitignore`) and states fixtures "must not be redistributed" (spec G5). Keep recorded snapshots outside the repo or in an ignored directory. |

## B. What pricebt writes to disk (so you know what can leak)

Executed on a synthetic run: `python -m pricebt run configs/synthetic_swap_carry.yaml --out <dir>` writes `manifest.json`; its `engine_manifest.instruments.<name>` holds the keys `asset_class`, `conventions`, `conventions_digest`, `factory`, `layers`, `roles`.

* `factory` is the dotted path of your Kit (for example `<pkg>:swap`): the internal package name is in every result directory.
* `conventions` is the RAW block of the instrument spec (`Engine._finalize` builds `manifest` with `dict(sp.conventions)`): never put a credential, host name or user id in `conventions`.
* The `errors` frame stores the exception TEXT of every recorded failure; the library's own exception messages (endpoints, user names, tokens) end up there. Scrub or translate them in the adapter (`_compat.translate`, as the example's `acme_adapter/_compat.py` does for the exception types).
* `config_hash` is a sha256 of the config AFTER `${ENV}` interpolation, truncated to 16 hex characters: it does not reveal a secret, but the digest changes when the secret changes.
* A snapshot's `provenance` mapping is free text that rides along in the shipped adapters' pricer `describe()`: put no host names or user ids in it.

## C. Offline versus live, and secrets

* **Offline (default run, marker `adapter_<lib>`):** needs the library installed but NO network, no credentials, no licence server, no clock. Feed it recorded or synthetic snapshots (`pricebt.testing.synthetic`, or snapshots you build in the test). Enforce it: the autouse `no_network` fixture of `references/test-templates.md` makes a Python-level TCP connect, UDP send or DNS lookup fail loudly, and a test proves each patch is not vacuous. CEILING: a client in native code (gRPC, a C extension, an agent process) never goes through those Python calls and bypasses the fixture; for such a library the offline guarantee is "the client is never constructed in an offline test and the data is a recorded snapshot", and the fixture cannot prove it.
* **Live (opt-in):** same marker `adapter_<lib>` PLUS `skipif(env var != "1")` with a reason that names the variable (`PRICEBT_LIVE_<LIB>=1`). A second partition marker is not needed and would add one more pinned entry (`tests/guards/partition.py`). The shipped precedent for a live oracle is a dedicated marker plus an environment variable plus a `skipif` that states why it is off.
* **Secrets:** read them from the environment or from the library's own session, never from a file in the repository. In a config, `${VAR}` is replaced at load (`pricebt.config.yamlio.interpolate`); an unset variable without default fails with `[CFG-ENV] environment variable 'ZZ_NOPE' is not set and has no default`, `${VAR:-default}` supplies one. Do not use it for anything that ends up in `conventions` (section B).
* **Heuristic scan** of the files you add, before every commit (PowerShell; expected output: nothing). It is a FLOOR, not proof: it finds the usual shapes and misses anything spelled differently, so read the diff too. Tested on eleven small files: it flags `API_KEY = 'abcd1234efgh5678'`, `password: hunter2hunter2`, `connect(host, password="hunter2hunter2")`, `{"password": "hunter2hunter2"}`, `cfg = {"api_key": "abcd1234efgh5678"}`, `"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9abcdef"`, `token = "abcd1234efgh5678wxyz"` and `https://user:hunter2hunter2@host/db`, and does not flag `os.environ[...]`, `os.getenv(...)`, `${VAR}` or a plain assignment. (An earlier, shorter pattern found only the first three of those eight.) It also flags harmless lines such as `token_count = 1234567890`: judge each hit.

```powershell
$pat = '(?i)((password|passwd|secret|api[_-]?key|token|credential)\w*[''"]?\s*[:=]\s*([''"][^''"\s]{8,}[''"]|[A-Za-z0-9+/_.-]{8,}\s*$)|bearer\s+[A-Za-z0-9._-]{16,}|://[^/\s:@]+:[^/\s@]+@)'
Get-ChildItem -Recurse -File src\pricebt\contrib\<lib>, tests\test_<lib>_*.py, configs\adapters\<lib>_*.yaml | Select-String -Pattern $pat
```

## D. Confidentiality of names

The guards ban the library's NAME from core (`tests/guards/banned.yaml`), so the name is written into a shared data file, into the change log and into the ADR. If the name or the existence of the integration is confidential, decide with the maintainer BEFORE step 5 of `SKILL.md`. Two consistent options; never a mixture:

| | private fork or branch (default) | public tree byte-identical |
|---|---|---|
| steps 1, 2, 4, 9, 16, and 12 except its `filterwarnings` line | as written | as written, in a private copy |
| 3(b) spec G-5, 5 ban entries, 7 pin test, 8 marker, 11 literal lists | as written, in the fork | skipped; replaced by ONE private test, `references/test-templates.md` file 5 (entries added in memory) |
| 6 skeleton | not applicable: the adapter is outside (`<pkg>`) | not applicable |
| 10 extra, 12 `filterwarnings` line, 13 NOTICE, 14 documents, 15 change log | in the fork, or your own notes if the fork has none | your own notes: nothing of them enters the public tree |
| adapter tests | in the fork's `tests/` with the marker, or in your package directory (not subject to the partition hook: an unmarked test outside `tests/` runs) | in your package directory |
| what is lost | nothing | GT-Z1a (a child that imports every core module with the default ban) and the core-only run `python tests/guards/blocker.py -m core` know no ban of your root. File 5 covers the same ground statically; for the core-only run use `python -c "import sys; sys.path[:0]=['tests']; from guards import blocker; blocker.install(['<root>']); sys.exit(blocker.main(['-m','core','-o','addopts=','-q','-p','no:cacheprovider']))"` (observed: a core test that imports a module which imports `<root>` then fails with `ModuleNotFoundError ... (blocked by the pricebt import blocker)`; with the plain command it passes) |

Whatever the option, nothing in the public tree may carry a name derived from the library. A marker with a neutral alias (`adapter_<alias>`) is technically fine (no test compares a marker with a library name) but adding it to the public tree is the spec G-5 edit.
