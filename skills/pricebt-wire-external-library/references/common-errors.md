# Common errors, once

The failures below are met by every adapter, and used to be repeated in eight skills. Each has a stable id; other skills carry a one-line row that links here (`common-errors.md#ce-allow` and so on). Every message is the real text printed by the commands in this file, run from the repository root against the worked example (`skills/pricebt-wire-external-library/example/`) with `PYTHONPATH=src;tests;skills/pricebt-wire-external-library/example` (Windows separator `;`, `:` on Linux).

| id | symptom in one line |
|---|---|
| [CE-ALLOW](#ce-allow) | `[CFG-ALLOW] ... is not under an allowed prefix ['pricebt']` |
| [CE-IMPORT](#ce-import) | `[CFG-IMPORT] ... cannot import '<pkg>': No module named '<pkg>'`, or a collection error in pytest |
| [CE-STACK-REGISTRY](#ce-stack-registry) | `[CFG-STACK] registry: a stack may not set 'registry'` |
| [CE-STACK-SYNTAX](#ce-stack-syntax) | `[CFG-FILE] config file not found: a.yaml,b.yaml` (or `...\$ex\b.yaml` in PowerShell), `unrecognized arguments`, `--stack 'x' is given twice` |
| [CE-TYPEERROR](#ce-typeerror) | `binding 'value': method 'value' called with args=[] kwargs=['ctx']: ...` reads like an argument mismatch |
| [CE-FACADE](#ce-facade) | `PricebtSession` refuses an adapter that lives outside `pricebt` |
| [CE-LADDER-CLI](#ce-ladder-cli) | no `L2.delta_ladder.*` rows in a CLI tie-out |

## CE-ALLOW

`error: [CFG-ALLOW] market.pricers.primary.wrap: [CFG-ALLOW] module 'acme_adapter' is not under an allowed prefix ['pricebt']`

* **Cause.** A dotted path (`factory`, `wrap`, a `function` binding target, a dotted `reduce`) names a package outside `pricebt` and no allow-list says it may be imported (`DEFAULT_ALLOW = ("pricebt",)`, `src/pricebt/registry.py`). The message names the prefix, NOT the place to fix it.
* **Fix.** `registry: {allow: [<your_pkg>]}` in the BASE config (also list `support` when the stacks read a recorded store through `support.curves:CurveStore`). For a shipped base you cannot edit: `--set "registry.allow=[<your_pkg>]"`. An overlay may not carry it ([CE-STACK-REGISTRY](#ce-stack-registry)).
* **The same text from a spec built in Python** (`bind.carry.target.function: ...` instead of `market.pricers.primary.wrap: ...`): `build_spec(..., allow=("pricebt", "<your_pkg>"))`; see [CE-FACADE](#ce-facade) for `PricebtSession`.

## CE-IMPORT

`error: [CFG-IMPORT] market.pricers.primary.wrap: [CFG-IMPORT] cannot import 'acme_adapter': No module named 'acme_adapter'`

* **Cause.** pricebt never adds a path. The directory that CONTAINS your adapter package must be importable.
* **Fix by tool.**

  | how you run | what puts your package on the path |
  |---|---|
  | `python -m pricebt ...` | the WORKING DIRECTORY is `sys.path[0]` for `python -m`, so an adapter at the repository root imports with no help; an adapter in a subdirectory needs that directory in `PYTHONPATH` |
  | `python tools/<script>.py` (a script) | only the SCRIPT'S directory (`tools/`), never the working directory: put the adapter's parent in `PYTHONPATH` (`.` for an adapter at the repository root) |
  | `python -m pytest` | `pytest.ini` adds `. src tests` only; a package elsewhere needs `sys.path.insert(0, str(Path(__file__).resolve().parents[n]))` in the test module (an import error there stops the whole collection: `Interrupted: 1 error during collection`) |
  | `python tools/mutcheck.py` | the caller's `PYTHONPATH` is kept after `src`, so it works the same as the shell |

* **Two-line proof** (run on the example copied to a scratch directory, `PYTHONPATH` holding `src` only): `python -m pricebt validate config/acme_tieout_base.yaml --stack config/acme_swap.yaml` prints `OK`; a script in `tools/` that calls `pricebt.api.build` on the same files fails with the error above until `.` is added to `PYTHONPATH`.

## CE-STACK-REGISTRY

`error: [CFG-STACK] registry: a stack may not set 'registry': only instruments.<name>.factory / .bind and market.pricers.<role>.wrap (everything else is shared by every stack)`

* **Cause.** An overlay carries `registry` (or conventions, terms, strategy, providers): all of that is shared by every stack, so only the BASE owns it.
* **Fix.** Move it to the base config. An overlay sets ONLY `instruments.<n>.factory`, `instruments.<n>.bind` and `market.pricers.<role>.wrap`.

## CE-STACK-SYNTAX

The three CLI verbs that take overlays do not spell `--stack` the same way (`python -m pricebt <verb> --help`):

| verb | one overlay | several overlays for ONE stack | several stacks |
|---|---|---|---|
| `validate`, `run` | `--stack overlay.yaml` | repeat the flag: `--stack a.yaml --stack b.yaml` | not applicable (one stack per run) |
| `tieout` | `--stack NAME=overlay.yaml` | a comma, no space: `--stack reference=a.yaml,b.yaml` | repeat the flag with a different NAME each time |

```text
python -m pricebt validate base.yaml --stack configs/adapters/refstack_swap.yaml --stack second_overlay.yaml
python -m pricebt tieout base.yaml --stack reference=configs/adapters/refstack_swap.yaml,second_overlay.yaml --stack mylib=my_swap.yaml --reference reference
```

* The comma form on `validate` or `run`: `error: [CFG-FILE] config file not found: configs/adapters/refstack_swap.yaml,second_overlay.yaml`, exit 2. A space-separated list: `pricebt: error: unrecognized arguments: second_overlay.yaml`, exit 2.
* The same NAME twice on `tieout`: `error: [CLI] --stack 'reference' is given twice`, exit 2. Give one NAME its overlays with commas.
* **PowerShell: quote a comma-joined list that contains a variable.** Unquoted, `--stack reference=configs/adapters/refstack_swap.yaml,$ex/refstack_zeta_pillars.yaml` passes `$ex` LITERALLY (PowerShell does not expand a variable inside an unquoted token that contains a comma): `error: [CFG-FILE] config file not found: <working directory>\$ex\refstack_zeta_pillars.yaml`, exit 2 (executed). Write `--stack "reference=configs/adapters/refstack_swap.yaml,$ex/refstack_zeta_pillars.yaml"`. A list of literal paths works unquoted; a single `--stack zeta=$ex/zeta_swap.yaml` expands `$ex` unquoted, so only the comma form bites.
* Overlays are applied in the order given. `factory` and `wrap` merge key by key; a LATER overlay's `bind` REPLACES the earlier overlay's whole `bind` block (the Kit's own defaults fill the names it does not list), so put every `bind` entry of a stack in one overlay unless only one of them has a `bind`.

## CE-TYPEERROR

`MethodCallError binding 'value': method 'value' called with args=[] kwargs=['ctx']: object of type 'int' has no len()`

* **Cause.** `call_binding` (`src/pricebt/contracts/binding.py`) turns EVERY `TypeError` into this text, including one raised INSIDE your callable, so it reads like a signature mismatch.
* **Fix.** Read to the END of the message: the tail is the real error. Call the bound method directly in a REPL with the same arguments. A genuine mismatch reads `... missing 1 required keyword-only argument: 'ctx'` (add `kwargs: {ctx: "@ctx"}`).

## CE-FACADE

The Python facade (`PricebtSession`, `pricebt.instrument.IRSwap`) builds specs without an allow-list, so an adapter outside `pricebt` fails there and works through the CLI and `pricebt.api`:

* `PricebtSession(market=..., stack="acme_adapter:STACK")`: `RegistryError [CFG-ALLOW] module 'acme_adapter' is not under an allowed prefix ['pricebt']`, at construction.
* `PricebtSession(market=..., stack=A.STACK).instrument_spec("swap", "USD")` with a `Stack` object: `ConfigError [CFG-ALLOW] bind.carry.target.function: [CFG-ALLOW] module 'acme_adapter.swap' is not under an allowed prefix ['pricebt']`, at the first `instrument_spec`.
* **Fix.** Drive it with a config (`run`, `tieout`, `pricebt.api.run`), or ship METHOD-only default bindings so a `Stack` object works. An in-repo adapter (`pricebt.contrib.<lib>`) has no such limit.

## CE-LADDER-CLI

`python -m pricebt tieout` compares `dv01`, `gamma` and `rate`; the delta ladder is compared ONLY through the Python API, so a CLI tie-out that exits 0 has not looked at your ladder (no `L2.delta_ladder.<bucket>` rows in the report).

* **Fix.** `run_tieout(base, {"reference": [...], "<stack>": [...]}, audit_measures=("dv01", "gamma", "rate", "delta_ladder"))` and read `res.reports[...]`; the executed snippet is in `skills/pricebt-conformance-and-tieout/SKILL.md`, step 9. Only a measure that EVERY stack's Kit defines can be audited (`[CFG-REF] backtest.audit.measures: ... not defined by any instrument`).
