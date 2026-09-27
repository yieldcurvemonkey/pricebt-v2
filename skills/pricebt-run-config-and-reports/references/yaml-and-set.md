# YAML 1.2 rules, `--set`, `extends`, stack overlays, `registry.allow`

Sources: `src/pricebt/config/yamlio.py` (the hardened loader), `src/pricebt/api.py` (`load`), `src/pricebt/config/stacks.py`, `src/pricebt/registry.py` (`resolve_dotted`). All tables below were executed.

## 1. What the loader reads (`yamlio.loads`)

| you write | you get | trap |
|---|---|---|
| `1e7`, `1e-3`, `1.0e-3`, `.5`, `4.25` | float | (plain PyYAML reads `1e7` as a string: pricebt's loader does not) |
| `1_000` | int `1000` | |
| `010`, `0o10` | the STRING `'010'`, `'0o10'` | no octal |
| `true`, `false` (any of `true True TRUE`) | bool | `yes`, `no`, `on`, `off` are STRINGS: `progress: {show: no}` fails `[CFG-TYPE] backtest.progress.show: ... must be true or false, got 'no' (a string such as "false" would read as true)` |
| `2024-05-27` | `datetime.date` | a bare date is a date object, not text; quote it (`"2024-05-27"`) only if a string is wanted |
| `[2024-05-27, 2024-06-19]` | a list of dates | `{holidays: [...]}` |
| `10Y`, `1m`, `next schedule` | strings | quote a value that contains `:` or `,` |
| a duplicate key in one mapping | `[CFG-YAML] <file>:2:1: duplicate mapping key 'backtest' (first at line 1)` | the last one never silently wins |
| `${VAR}`, `${VAR:-default}` inside any string | the environment value, applied by `yamlio.interpolate`, which `api.load` (hence the CLI) calls after `extends`, `--set` and the parse: `yamlio.loads` alone returns the raw `${...}` text (executed: `{'x': '${VAR:-dflt}'}`) | an unset `${VAR}` without a default is `[CFG-ENV] environment variable 'VAR' is not set and has no default` |
| a file whose text is JSON (`.json`, or starts with `{` / `[` and parses) | parsed as JSON | same model |

Anchors and aliases work (`&holidays` / `*holidays`, used by the worked example so that the backtest calendar and the market calendar cannot drift apart). A sexagesimal `12:30` stays a string.

## 2. `extends`

`extends: base.yaml` (or a list; base first, later ones and the file itself override). Mappings merge recursively; LISTS AND SCALARS ARE REPLACED (a `triggers:` list in the child replaces the base's whole list). Paths are relative to the file that names them;
a cycle is `[CFG-EXTENDS] extends cycle: <a> -> <b> -> <a>` (with full paths). A JSON file can `extends` a YAML one (executed). Overlays are loaded by the same `api.load`, so `extends` and `${ENV}` work in them too.

## 3. `--set KEY=VALUE` (validate, run, describe, tieout; repeatable, applied in order to the loaded base before overlays)

* `KEY` is a dotted path; a numeric part indexes a list (`strategy.triggers.0.actions.1.trade_duration=2m`); missing intermediate mappings are created (a typo becomes a real key and is then refused by the loader: `[CFG-UNKNOWN-KEY] backtest.gird: unknown key 'gird' (did you mean 'grid'?)`).
* `VALUE` is parsed by the same hardened YAML loader, so flow syntax works and replaces a whole mapping or list: `--set "registry.allow=[acme_adapter]"`, `--set backtest.progress.show=false`, `--set backtest.grid.end=2024-06-14`,
  `--set 'strategy.triggers.0.actions=[{type: add_trade, priceables: {instrument: usd_sofr_ois, name: one, terms: {side: pay, maturity: 5Y, notional: 1e6, fixed_rate: par}}, trade_duration: 1m}]'`,
  `--set 'tieout.tolerances={"L2.dv01": {rel: 1.0e-3, reason: "why"}}'`.
* A key that contains a dot cannot be addressed: `--set tieout.tolerances.L2.dv01.rel=1.0e-3` builds `{L2: {dv01: {rel: ...}}}` and fails `[CFG-TIEOUT] tieout.tolerances.L2: malformed tolerance key 'L2': expected `<level>.<quantity>[.<asset class>]` ...`. Set the whole mapping.
* Quote the whole argument in PowerShell with single quotes when it contains `{`, `}`, `:` or spaces; inside YAML flow, strings with `:` need quotes.
* `--set` cannot touch an overlay (it applies to the base); an option is to edit the overlay file or give the Python API a dict. In `tieout`, `--set` changes the base for EVERY stack (which is what makes it a fair experiment).
* `--set name="${RUN_TAG:-dev}"` interpolates after the override (executed: `name` becomes `dev`).

## 4. Stack overlays

A stack overlay is a YAML mapping that may contain only `instruments.<name>.factory`, `instruments.<name>.bind` and `market.pricers.<role>.wrap` (`validate_overlay`; the error names the path). It can neither add an instrument or a role nor set `registry`, `conventions`, terms, the strategy or a provider.

```yaml
# skills/pricebt-wire-external-library/example/config/acme_swap.yaml (comments removed)
instruments:
  usd_sofr_ois: {factory: "acme_adapter:swap"}
market:
  pricers:
    primary: {wrap: "acme_adapter:wrap"}
```

* Apply with `--stack overlay.yaml` (`validate`, `run`, `describe`, repeatable: applied in order, the last wins on the keys they share) or, in `tieout`, `--stack NAME=overlay.yaml[,overlay2.yaml]`; in Python `api.build(base, stack=[overlay, ...])` or `run_tieout(base, {name: [overlay, ...]})`.
* Merge rules (two): an overlay `bind` REPLACES the base's whole `bind` block of that instrument; the Kit's default block then merges with the remaining `bind` BY NAME (`src/pricebt/contracts/spec.py`, `build_spec`). So an overlay that says `bind: {dv01: {...}}` changes exactly one binding and keeps the Kit's other defaults.
* `Built.base_hash` (and `pricebt describe`'s `base_hash:`) hashes the config WITHOUT those three keys: equal for every stack over one base (the proof that only the library changed); `config_hash` is the whole config.
* An overlay that names a package outside `pricebt` needs that package in `registry.allow` of the BASE, and on `PYTHONPATH`.

## 5. `registry.allow` for an external package

* Put the package (the top-level import name of your adapter) in the BASE: `registry: {allow: [acme_adapter]}`. It covers `acme_adapter` and every `acme_adapter.<sub>` module, not `acme_adapter_two`, and not the foreign library itself (`acmelib` is imported by the adapter, never by a config).
* A shipped base that lacks it: `--set "registry.allow=[acme_adapter]"` (executed against `configs/synthetic_swap_carry.yaml` with the acme overlay: the tie-out passes). Several prefixes: `--set "registry.allow=[support, tools.swap_suite_support]"`.
* The three messages: `[CFG-ALLOW] ... module 'acme_adapter' is not under an allowed prefix ['pricebt']` (missing from the BASE; the text does not say where to add it), `[CFG-STACK] registry: a stack may not set 'registry': ...` (you put it in an overlay),
  `[CFG-IMPORT] ... cannot import 'acme_adapter': No module named 'acme_adapter'` (allowed, but not on `PYTHONPATH`).
* A Kit whose DEFAULT bindings include `function` targets (like the worked example's four layers) needs the allow-list wherever a spec is built: `build_spec(..., allow=("pricebt", "acme_adapter"))` in Python. The Python facade `PricebtSession` builds specs without an allow-list, so it cannot use such an adapter
  (`[CFG-ALLOW] bind.carry.target.function: module 'acme_adapter.swap' is not under an allowed prefix ['pricebt']`); the facade refuses `stack="acme_adapter:STACK"` too. Use the config route (`run`, `tieout`) for an external adapter, or ship method-only default bindings.
* pytest (`pytest.ini`: `pythonpath = . src tests`) does not know a package that lives elsewhere: insert its directory from the test file (`sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "<dir>"))`).

## 6. PowerShell and quoting cheat sheet

```powershell
$env:PYTHONPATH = "src;tests;<parent directory of your adapter package>"     # ';' on Windows, ':' on Linux
python -m pricebt validate cfg.yaml --stack overlay.yaml --set 'backtest.grid.end=2024-06-14'
python -m pricebt tieout cfg.yaml --stack reference=configs/adapters/refstack_swap.yaml --stack mylib=overlay.yaml --set "registry.allow=[mylib_adapter]"
```

A backtick continues a line in PowerShell; `$LASTEXITCODE` holds the exit status of the last native command (0 ok, 1 tie-out failed, 2 error). In bash use `\` and `$?`.
