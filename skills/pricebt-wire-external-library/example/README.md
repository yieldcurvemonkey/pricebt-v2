# Worked example: wiring an external pricing library into pricebt

`acmelib` is a small FICTIONAL in-house rates library with a deliberately foreign API. `acme_adapter` is the pricebt adapter for it. Everything here was executed; the
numbers below are the ones the commands printed. The example is the spine of the skill `pricebt-wire-external-library`.

**One rule.** The core (everything under `src/pricebt` except `contrib/`) has zero dependence on any pricing library. An adapter is the only place a library is imported, and the
library is reached only through **schema name -> binding -> callable**. Nothing in this example edits `src/`, `docs/` or any other test or config.

**Honest caveat.** acmelib's numerical core is REUSED from `pricebt.testing.refstack` (schedules, discounting over fixings, the bootstrapped par-swap risk curve), so acmelib agrees
with the reference stack to round-off, by construction, and the tie-out is "exact or noise" (measured below: bit-identical at L0, L1, L4; 1e-16 .. 1e-10 relative at L2; L3
differences are at most 1.6e-6 currency). A real library is independent: expect real noise floors, some declared differences (write a `reason` for each), and a convention
document like the one that models it under `docs/design/` (`11-*-conventions.md`). What this example proves is the WIRING: units, signs, shapes, calendars, global state, errors, layers, the overlay.

## 1. Files

| file | lines | what it is |
|---|---|---|
| `acmelib/` (`errors`, `state`, `calendars`, `curve`, `swap`, `__init__`) | 437 | the fictional library (330 code lines): ISO-string dates, lower-case tenors, decimals, receiver's-point-of-view values and risk, risk per one million, a pandas ladder with an extra bucket, a process-global valuation date, a global calendar registry, its own exceptions, NO attribution |
| `acme_adapter/_compat.py` | 83 | the only module that touches acmelib's globals and exceptions: `guard`, `translate`, `load_calendar` |
| `acme_adapter/wrap.py` | 105 | snapshot -> acmelib market (zero curve, decimal fixings, registered calendars), memoised |
| `acme_adapter/conventions.py` | 76 | shared vocabulary -> acmelib codes; unsupported value = `ConfigError` naming the supported set |
| `acme_adapter/swap.py` | 274 | `AcmeSwap`, the factory, the four layers, the ladder reducer, `SWAP_BIND`, the `Kit` |
| `acme_adapter/__init__.py` | 27 | exports `swap`, `wrap`, `STACK` |
| `config/acme_swap.yaml` | 10 | the stack overlay (factory + wrap only) |
| `config/acme_tieout_base.yaml` | 55 | the BASE config (`registry.allow`, three trades over a month with two holidays) |
| `config/mistakes/*.yaml` (5), `acme_mistakes.py` | 51 + 43 | the negative controls: overlays that break exactly one thing, and the two pieces that need code |
| `../../../tests/test_skills_example_acme.py` | 740 | 67 tests, marker `core` |

## 2. The foreign API, and where each mismatch is converted

| acmelib does | pricebt wants | converted in | proven by |
|---|---|---|---|
| dates are ISO strings, tenors lower-case (`'10y'`), calendar names are its own | `dt.date`, `10Y`, the snapshot's calendar names | `_compat.iso/to_date`, `swap_factory` (`maturity.lower()`, dates back with `to_date`), `wrap` | effective/maturity resolve like the reference (7 cases, forward starts, explicit dates) |
| rates are DECIMALS (`0.0425`) | PERCENT (`4.25`) | binding `rate: {scale: 100}`; the strike `percent / 100` in the factory; fixings `unit` in `wrap` | `test_units_and_signs_agree...`, control 1 |
| values and risk are the RECEIVER's (opposite to payer-positive) | holder-signed, payer-positive | methods return `direction x receiver number`; every default binding carries `sign: -1`; `value` (a `Valuation`, which `sign` cannot touch) converts in code | conformance kit (mirror), control 2 |
| risk per ONE MILLION notional | per unit as built | methods multiply by `notional / 1e6` (a binding `scale` is a constant: it cannot read the trade) | two notionals in every check, mutation `per-million` |
| ladder = pandas Series, lower-case labels, an extra `'on'` bucket | `dict[str, float]`, upper-case tenors, exactly the bound `keys` | reducer `acme_ladder_to_tenor_dict` (drops `'on'` only if it is 0, else refuses) | reducer test, control 3 |
| a process-GLOBAL valuation date every call reads | nothing global | `_compat.guard` sets and restores it around every call | restored after every public path and after an exception |
| a GLOBAL calendar registry keyed by its own names | the snapshot's holidays | `_compat.load_calendar`: content-addressed name `PBT.<name>.<hash>`, never `replace` | holidays seen, two calendars alive at once, control 4 |
| its curve is ZERO rates on its own basis | discount factors at node dates, tag `log_linear_df` | `wrap.zero_curve`: `z = -ln(DF)/t` (ACT/365); any other tag = `ConfigError` | DF round trip 1e-14 between and beyond nodes |
| its own exception types | pricebt errors | `_compat.translate` (missing fixing -> `MarketDataUnavailable`, calendar/input -> `ConfigError`, rest -> `MethodCallError`) | 9 error paths; no `AcmeError` escapes |
| NO attribution: only value as of a date, a shifted market, a rolled market | four layers, ADR 005 definitions | `swap.py::_decomposition` from `pv(as_of=)`, `cashflows`, `Market.rolled/shifted`, `ZeroCurve.resampled` | conformance kit + agreement with the reference layers |

## 3. The steps (what you did, which file, which pricebt concept, which check proves it)

| # | what you did | file | pricebt concept | check that proves it |
|---|---|---|---|---|
| 0 | read the contract: ADR 001-007, `swap.yaml`, `binding.py`, `spec.py`, `snapshot.py`, `refstack.py`, `layer_conformance.py`, an existing adapter | - | the one rule; schema name -> binding -> callable | - |
| 1 | probe the library on known answers (a par swap is worth 0; which sign a receiver has; which unit a rate is; what is global) and write the mismatch table (section 2) | - | conventions are yours to discover, never assumed | section A of the tests (`test_acmelib_*`) |
| 2 | isolate what is global and what raises: the valuation-date guard, error translation, calendar registration | `acme_adapter/_compat.py` | an adapter owns every library global (as the `_compat.py` of every shipped adapter under `src/pricebt/contrib/`) | `test_the_valuation_date_is_set_inside_the_guard_and_restored_after_every_operation`, `..._ends_with_none_even_when_it_raises`, `test_acmelibs_own_exceptions_never_reach_pricebt...` |
| 3 | turn a snapshot into the library's objects, once | `acme_adapter/wrap.py` | `SnapshotPricer`, `wrap` memoised per digest, interpolation TAG (never a silent substitute) | `test_wrap_is_memoised...`, `test_discount_factors_become_zero_rates...`, `test_only_the_log_linear...`, `test_the_snapshots_holidays_are_what_acmelib_sees...`, `test_two_wrapped_snapshots...` |
| 4 | map the shared conventions vocabulary to the library's codes; refuse what it cannot express | `acme_adapter/conventions.py` | `AssetSchema.check_conventions`, `CFG-CONVENTION-UNSUPPORTED` | 5 unsupported values name reason + supported set; 9 supported variants agree with the reference |
| 5 | the instrument class and the factory: resolve `par`, the effective date and the maturity at the fill pricer; return concrete dates | `acme_adapter/swap.py` (`AcmeSwap`, `swap_factory`) | `factory(pricer, ts, *, terms, conventions) -> Built(obj, resolved_terms)`, ADR 003 | `test_effective_and_maturity_resolve_like_the_reference...`, `test_par_and_the_spread_over_par...` |
| 6 | value and measures in pricebt's units and signs; decide what goes in the binding (`scale`, `sign`) and what in code | `swap.py` methods, `SWAP_BIND` | binding post-processing `sign * (scale * reduce(x) + offset)`, `@ctx`, `@instrument` | `test_units_and_signs_agree_with_the_reference_stack...` (payer and receiver, two notionals) |
| 7 | the ladder: return the library's own shape, convert with a reducer, declare `keys` | `ladder_to_tenor_dict`, `register_reducer` | reducers, `keys`, `check_ladder` (upper-case tenors, exactly the bound set) | `test_the_ladder_reducer...`, control 3 |
| 8 | the four layers from the library's primitives (it has none) | `swap.py` (`_decomposition`, `carry`, `roll`, `delta`, `convexity`) bound with `function` targets | ADR 005 definitions; layers are schema names bound like any method | `test_layers_on_a_seasoned_swap_agree_with_the_reference_stack...` |
| 9 | the Kit and the Stack | `swap = Kit(..., extra={"acme_zero_dv01": "measure"})`, `STACK` | `Kit(factory, asset_class, default_bind, cls, extra)`, `Stack` | `test_conforms_to_the_swap_schema_with_only_the_kits_default_block`, `test_every_default_binding_runs_and_returns_its_declared_type` |
| 10 | run the layer conformance kit | `tests/...::test_the_layer_conformance_kit_passes_on_the_adapter` | `testing.layer_conformance.run_kit`: shock = delta + convexity, mirror, cash sweep, unexplained share | the kit passes, AND catches a layer bound without its sign (`test_the_kit_itself_catches...`) |
| 11 | the config: `registry.allow` in the BASE, an overlay of factory + wrap | `config/acme_tieout_base.yaml`, `config/acme_swap.yaml` | stack overlays (`config/stacks.py`), the import allow-list (section 5) | `test_the_overlay_sets_only_factory_bind_and_wrap...`, mutation `base-allow` |
| 12 | tie out against the reference stack, level by level | `run_tieout`, `python -m pricebt tieout` | L0-L4, statuses `exact/noise/exceeds/input/structure`, the self-test, shipped tolerances | `test_the_acme_stack_ties_out...`, `test_the_tie_out_is_not_vacuous`, the CLI test |
| 13 | negative controls: break exactly one thing, expect one located failure | `config/mistakes/*`, `acme_mistakes.py` | the harness has teeth: a check that has never failed proves nothing | `test_negative_control_...` x4, `test_a_wrap_that_forgets_the_holidays_stops_the_run...` |
| 14 | mutation-check your own tests; keep the library's name out of anything that is not an adapter | - | `tools/mutcheck.py` idea, guards `banned.yaml` | 33 one-line mutants, 33 killed (section 8); `test_no_file_of_the_example_imports_or_names...` |

## 4. Design choices worth copying

* **Reducer, not `function` target, for the ladder.** The conversion needs nothing but the method's result (a pure `Series -> dict` function, unit-testable alone) and pricebt already
  has the slot for it (`reduce:` plus the declared `keys`). A `function` target is the right tool when the conversion needs the instrument, the context or several calls:
  the four layers (`{function: "acme_adapter.swap:carry", kwargs: {swap: "@instrument", ctx: "@ctx"}}`). The price of a function target or a dotted `reduce:` is a dotted
  path, hence an allow-list entry (section 5), and the facade cannot resolve it (section 9). The registered reducer name needs no allow-list but only exists once `acme_adapter`
  has been imported, which the `factory` path does.
* **Sign and units.** `sign`/`scale` post-process a number or every value of a dict; they cannot post-process a `Valuation` and `scale` is a constant. So: `value` converts in code,
  the notional factor is in code, the receiver-to-payer flip is ONE `sign: -1` per binding (one visible, overridable, testable line; control 2 forgets it).
* **Calendars.** acmelib's registry is global and keyed by name, so `load_calendar` registers content-addressed names: two snapshots with different holidays under one snapshot name
  are two acmelib calendars (the layers hold two pricers at once), and registering the same content twice is a no-op. The pricer re-registers on unpickle.
* **Plain-data instrument.** `AcmeSwap` holds the contract and the direction; it deep-copies and pickles (the engine deep-copies positions).
* **`dv01` definition.** Analytic annuity before the start, the ladder sum after it: the tie-out only passes at 1e-4 if the definition equals the reference's.
* **Extension measure `acme_zero_dv01`** declared in `Kit.extra` (underscore prefix). It is not audited in the tie-out: listing it in `audit_measures` while any stack lacks it stops the run at load with
  `[CFG-REF] backtest.audit.measures: ... not defined by any instrument`, so it is left out of the audit and checked against the reference's `dv01_zero` in a test.

## 5. How the allow-list and the import of a package outside `pricebt` work (read from the code, then executed)

* Only a DOTTED PATH imports anything: a `factory`, a pricer `wrap`, a binding `target: {function: ...}`, a dotted `reduce:`, `registry.import`, `type:` and `$ref/$call` entries. The
  text of a config never imports anything.
* `_Builder.__init__` in `src/pricebt/config/loader.py`: the allowed prefixes are `pricebt` (always: `DEFAULT_ALLOW` in `src/pricebt/registry.py`) plus `registry.allow` of the BASE config, read after the overlays are applied. An overlay cannot set
  `registry` (`validate_overlay` in `src/pricebt/config/stacks.py`: `[CFG-STACK] a stack may not set 'registry'`), so `acme_adapter` must be listed in the base (`config/acme_tieout_base.yaml`), or passed with
  `--set "registry.allow=[acme_adapter]"` to a shipped base (executed against `configs/synthetic_swap_carry.yaml`: tie-out passed, exit 0, 18.5 s for six months).
* `resolve_dotted` in `src/pricebt/registry.py`: (1) the MODULE part must be under an allowed prefix (`name == prefix or name.startswith(prefix + ".")`: `acme_adapter` covers
  `acme_adapter.swap`, `acme` would not cover `acmelib`); (2) it is imported with `importlib.import_module`, so it must be on `sys.path`: pricebt never adds a path (CLI:
  `PYTHONPATH`; pytest: `pytest.ini` has `pythonpath = . src tests`, so a test outside those must `sys.path.insert`); (3) every attribute on the path is fetched, refused if it starts with an
  underscore, and its OWNER (`__module__`) must be under an allowed prefix (a `Kit` instance reports `pricebt.contracts.spec`, so `acme_adapter:swap` passes; a function reports its own module,
  `acme_adapter.swap`, which needs the prefix).
* The failure message is `[CFG-ALLOW] module 'acme_adapter' is not under an allowed prefix ['pricebt']` and does not say WHERE to add it: in the base config's `registry.allow`.
* acmelib itself is never in `registry.allow`: nothing in a config imports it, only the adapter does.
* A Kit whose DEFAULT block has function targets needs the allow-list wherever the spec is built: `build_spec(..., allow=("pricebt", "acme_adapter"))` in Python.

## 6. Commands (PowerShell, from the project root `pricebt/`)

```powershell
# the example's tests: 67 tests, marker core, no external library (22 s)
$env:PYTHONPATH = "src;tests"
python -m pytest tests/test_skills_example_acme.py -q -o addopts= -p no:cacheprovider
# the same with every banned vendor import root made unimportable (25 s)
$env:PYTHONPATH = "src"; python tests\guards\blocker.py tests/test_skills_example_acme.py -o addopts= -p no:cacheprovider -q
```

The layer conformance kit on the adapter (`3 passed`: the kit, the seasoned swap across a payment date, and the kit catching a missing sign):

```powershell
$env:PYTHONPATH = "src;tests"
python -m pytest tests/test_skills_example_acme.py -q -o addopts= -p no:cacheprovider -k "layer_conformance_kit or seasoned_swap_layers or kit_itself"
```

or from Python (printed `kit ok: 21 rows; unexplained share {'static': 0.0, 'moved': 9.944798161742972e-07}`):

```powershell
$env:PYTHONPATH = "src;tests;skills\pricebt-wire-external-library\example"
@'
import datetime as dt
import acme_adapter as A
from pricebt.contracts.schema import SchemaRegistry
from pricebt.contracts.spec import build_spec
from pricebt.testing.layer_conformance import Setup, flat_swap_world, run_kit

HOL = (dt.date(2024, 3, 29), dt.date(2024, 5, 27), dt.date(2024, 7, 4))
conv = {**A.USD_SOFR_OIS_CONVENTIONS, "calendar": "cal"}                      # the world below names its calendar "cal"
spec = build_spec("ois", {"factory": A.swap, "conventions": conv}, schemas=SchemaRegistry.default(), allow=("pricebt", "acme_adapter"))
terms = {"side": "pay", "maturity": "5Y", "notional": 1e7, "fixed_rate": 4.2}
report = run_kit(Setup(spec=spec, wrap=A.wrap, terms=terms, mirror_terms={**terms, "side": "receive"}, world=flat_swap_world("cal", holidays=HOL),
                       t0=dt.date(2024, 3, 4), t1=dt.date(2024, 3, 5)))
report.assert_ok()
print("kit ok:", len(report.rows), "rows; unexplained share", report.unexplained_share)
'@ | python -
```

The real tie-out through the CLI. The base is shared, the reference stack is the shipped overlay, acme is this example's overlay:

```powershell
$env:PYTHONPATH = "src;tests;skills\pricebt-wire-external-library\example"
$ex = "skills\pricebt-wire-external-library\example"
# reference against acme: exit 0, "TIE-OUT PASSED" (4.8 s: two stacks plus the harness self-test)
python -m pricebt tieout $ex\config\acme_tieout_base.yaml --stack reference=configs\adapters\refstack_swap.yaml --stack acme=$ex\config\acme_swap.yaml
# three stacks: the third has the dv01 binding WITHOUT sign -1: exit 1,
# "TIE-OUT FAILED: reference_vs_acme_wrong_sign: L2.dv01; ...: L3.tay_delta; ...: L3.tay_unexplained" (6.3 s); reference_vs_acme still passes
python -m pricebt tieout $ex\config\acme_tieout_base.yaml --stack reference=configs\adapters\refstack_swap.yaml --stack acme=$ex\config\acme_swap.yaml `
    --stack acme_wrong_sign=$ex\config\mistakes\acme_swap_wrong_sign.yaml
# the same adapter on the project's own synthetic config (six months): base needs the allow-list from the command line
python -m pricebt tieout configs\synthetic_swap_carry.yaml --stack reference=configs\adapters\refstack_swap.yaml --stack acme=$ex\config\acme_swap.yaml --set "registry.allow=[acme_adapter]"
```

The CLI cannot compare the ladder (it audits `dv01`, `gamma`, `rate` only, `DEFAULT_AUDIT_MEASURES` in `src/pricebt/tieout/runner.py`); the Python API can, and that is what the tests use:

```powershell
@'
from pricebt.tieout import run_tieout
ex = r"skills\pricebt-wire-external-library\example\config"
res = run_tieout(ex + r"\acme_tieout_base.yaml", {"reference": [r"configs\adapters\refstack_swap.yaml"], "acme": [ex + r"\acme_swap.yaml"]},
                 audit_measures=("dv01", "gamma", "rate", "delta_ladder"))
rep = res.reports["reference_vs_acme"]
print("passed:", rep.passed, "| self-test:", res.header["selftest"], "| rows:", len(rep.rows), "| ladder rows:", sum(r.quantity.startswith("delta_ladder.") for r in rep.rows))
'@ | python -
# passed: True | self-test: passed | rows: 52 | ladder rows: 11
```

## 7. Measured results

Base: three swaps (10Y receive 10mm, 5Y payer 5mm, a payer of 25mm to an explicit maturity 2024-06-12 that pays and matures inside the window), 2024-05-24 .. 2024-06-21, 19 points,
57 marks per quantity, two holidays. `run_tieout(..., audit_measures=(dv01, gamma, rate, delta_ladder))`, SHIPPED tolerances, nothing declared, self-test passed:

| level | result (52 rows: 24 `exact`, 28 `noise`, 0 anything else) |
|---|---|
| L0 inputs | snapshot digests, resolved terms, conventions digest: exact |
| L1 marks | `pv`, `cash`, `financing`, position size, row set: exact (bit-identical) |
| L2 measures | `dv01` 2.7e-13 relative, `rate` 1.2e-16, `gamma` 1.4e-10, ladder buckets <= 2.4e-11 currency (2 buckets exact) |
| L3 layers | max absolute difference (per unit): carry 1.5e-11, roll 1.5e-11, delta 9.2e-8, convexity 1.5e-6, `unexplained` 1.6e-6, baseline `tay_*` <= 6.1e-10; `reconcile()` exact in both runs |
| L4 portfolio | equity, cash, positions value, trade ledger, statistics: exact |

Tests: `67 passed` (22 s), and `67 passed` under the import blocker. Mutation check (33 one-line mutants, my own script; every mutant applied to one file, the example's tests run with `-x`, the file
restored): 28 mutants of the adapter, acmelib and the two configs (sign constant, `scale`, per-million, dv01 definition, cash window, Richardson, roll fixings, carry cash, effective adjustment, par
percent, strike percent, reducer check, guard restore, content-addressed calendar name, missing-fixing mapping, fixings unit, holidays, weekend, DF-to-zero basis, basis/frequency maps,
unsupported-value check, overlay factory, base allow-list, acmelib interpolation, roll, `on` bucket) plus the 5 control overlays "fixed" (each control that stops failing is caught): 33 killed, 0 survived.

A caution on what the shipped floors can see. `L2.delta_ladder` has an absolute floor of 100 per bucket per unit. The largest absolute bucket over this run: 3M 96, 6M 2.9, 1Y 2.8, 2Y 2.3, 3Y 51,
5Y 2,250, 7Y 154, 10Y 8,182, 15Y/20Y/30Y 0. A bucket is compared absolutely below the floor (tolerance rel x floor = 1e-3 x 100 = 0.1 currency per unit), so an error smaller than 0.1 in a small bucket is invisible: +5 percent on the 2Y bucket (2.3) is
`exceeds`, +2 percent is `noise`. The tests therefore also compare the ladder with the reference directly to 1e-8 for two directions and two notionals.

## 8. Negative controls: what the harness says for each mistake

Each control is first shown NOT to fire on the correct wiring (the same row is `exact`/`noise` in `reference_vs_acme`), then to fire. `max_rel` is the harness's number.

| control (overlay) | mistake | level.quantity | status | located / size | what else moves |
|---|---|---|---|---|---|
| `mistakes/acme_swap_no_percent.yaml` | `rate` binding without `scale: 100` | `L2.rate` | `exceeds` | `position=P000001, ts=2024-06-04`, max_rel 0.99 (0.0403 against 4.03) | L3 `tay_delta`, `tay_convexity`, `tay_unexplained` (the baseline turns the rate move into bp); L0, L1, L4, L2 `dv01/gamma` stay exact/noise |
| `mistakes/acme_swap_wrong_sign.yaml` | `dv01` binding without `sign: -1` | `L2.dv01` | `exceeds` | `position=P000003, ts=2024-06-11`, max_rel 2.0 (a sign flip) | L3 `tay_delta`, `tay_unexplained`; the rest stays exact/noise |
| `mistakes/acme_swap_lower_key.yaml` | ladder reducer lower-cases one key (`30Y` -> `30y`) | `L2.delta_ladder.<every bucket>` | `structure` | "the measure column exists in reference only" (needs `delta_ladder` in the audit measures, Python API) | `L4.stats` `n_errors` (62 recorded errors against 5: one per audited mark plus the 5 `rate` NaNs of the matured swap); pv, dv01 stay exact/noise |
| `mistakes/acme_swap_wrong_calendar.yaml` | the wrap loads one holiday too many (2024-05-28) | `L0.resolved_terms` | `input` | `P000001 effective: 2024-05-29 vs 2024-05-30` (dates first) | `L1.pv`, `L2.*`, `L3.*`, `L4.equity` are `input` (explained by L0); `L4.stats` and `L4.stats_traded` stay `exceeds` (not attributable to L0); snapshot digests and conventions exact |
| `mistakes/acme_swap_no_holidays.yaml` | the wrap loads NO holidays | the run STOPS | `MarketDataUnavailable` | "1 overnight fixings missing between 2024-05-28 and 2024-06-19; first 2024-06-19" | no report: with `backtest.on_error: record` in the base the run continues and the report shows `L0.resolved_terms input` (`effective 2024-05-29 vs 2024-05-28`) and `L1.marks_rows structure` (6 rows only in the reference) |

## 9. Traps a new agent hits (each one hit or executed here)

1. `registry.allow` belongs in the BASE; an overlay carrying it is rejected, and the `CFG-ALLOW` message does not say where it goes.
2. `PricebtSession(market=..., stack=A.STACK)` (the Python facade) FAILS for this adapter: `PricebtSession.instrument_spec` builds the spec without an allow-list, so the function targets of the default block are refused
   (`[CFG-ALLOW] bind.carry.target.function: module 'acme_adapter.swap' is not under an allowed prefix ['pricebt']`), and `stack="acme_adapter:STACK"` is refused at `PricebtSession.__init__`. Method-only default blocks
   avoid it; a package outside `pricebt` cannot ship dotted-path bindings to the facade.
3. `acme_adapter.swap` is both the `Kit` (the attribute) and the module (`sys.modules`): `from acme_adapter import swap` is the Kit, `import acme_adapter.swap as m` is ALSO the Kit; use `importlib.import_module`.
4. `register_reducer` refuses a different function under a taken name: `importlib.reload(acme_adapter.swap)` raises `CFG-REDUCER`.
5. A `TypeError` raised INSIDE a bound callable is reported as `binding 'value': method 'value' called with args=[] kwargs=['ctx']: ...` (`call_binding` in `src/pricebt/contracts/binding.py`), as if the arguments did not fit. The real cause is at the end of the message.
6. `Valuation` cannot go through `sign`/`scale`; `scale` cannot read the trade's notional.
7. A stack whose run raises aborts `run_tieout` for every stack: set `backtest.on_error: record` in the base to get a report.

## 10. What a real adapter must additionally do (not in this example)

* Guards for the new library's name: its import root in `tests/guards/banned.yaml` `import_roots` and its name/prefixes in `executable_tokens`; a scan of the adapter package (this example's version: `test_no_file_of_the_example_imports_or_names...`, with its non-vacuity twin).
* A pytest partition marker for the library (`adapter_<lib>`): registered in THREE files: `pytest.ini` `markers`, `PARTITIONS` in `tests/guards/partition.py` and the pinned tuple in `tests/guards/test_partition.py` (`pricebt-guards-and-packaging`, step 8; an unmarked test is a collection error, and a `core` test must not need the library).
* A `pyproject` extra and an `OptionalDependencyError` at import (as the shipped adapters' `_compat.py` do) when the library is missing; `importorskip` in the tests.
* An independent tie-out: measured noise floors per level, a `tieout.tolerances` declaration with a `reason` for every widened key (`tasks/tolerance_ledger.yaml`), and known-answer controls against the library's own documented numbers (the golden case).
* A conventions document (each supported value verified against the reference, each unsupported one with its reason), the data horizon of the library's calendars, and the library's own thread-safety and licence constraints.
* Live and offline tests: offline (a fixture snapshot) in the default run, live (a real data source or licence) opt-in behind a marker.
* Mutation checks of the adapter (`tools/mutcheck.py`) and a bond kit and a bond layer set if the library prices bonds.
