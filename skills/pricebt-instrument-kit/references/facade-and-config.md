# Naming the Kit in a config, the allow-list, overlays, the `Stack` and the gs-style facade (exact limits)

Sources: `src/pricebt/contracts/spec.py` (`Kit`, `Stack`, `build_spec`, `TradeTemplate`), `src/pricebt/registry.py` (`resolve_dotted`, `DEFAULT_ALLOW`), `src/pricebt/config/loader.py`
(`_Builder.__init__`, `.mdp`, `.market`), `src/pricebt/config/stacks.py` (`validate_overlay`, `apply_stack`, `base_digest`), `src/pricebt/session.py` (`PricebtSession`),
`src/pricebt/instrument.py` (`IRSwap`), `docs/design/adr/003-instrument-spec-and-terms.md`, `006-stacks-and-tieout.md`, `009-facade.md`. Every message below was produced by running it.

## 1. The `Kit` and how a config names it

```python
@dataclass(frozen=True)
class Kit:
    factory: Callable[..., Built]      # (pricer, ts, *, terms, conventions) -> Built(obj, terms)
    asset_class: str                   # a schema name: "swap", "bond", or your extension schema's
    default_bind: Mapping[str, Any]    # schema name -> binding (dict); the block a user's `bind:` overrides BY NAME
    cls: Optional[type] = None         # the class whose METHODS the bindings name: load-time check that each method exists and can bind exactly its declared kwargs
    extra: Mapping[str, str] = {}      # extension names the adapter binds beyond the schema: name -> 'measure' | 'layer'
    schema: Optional[Mapping] = None   # an extension schema (raw mapping) defining `asset_class`, usually `extends: swap`; registered for this kit only
    doc: str = ""
```

```yaml
instruments:
  usd_sofr_ois:
    asset_class: swap                          # optional for a Kit (taken from it); REQUIRED for a plain-callable factory
    factory: "<lib>_adapter:swap"              # a dotted path to the Kit (or to a plain callable)
    conventions: {calendar: usd_fed, ...}      # ALL keys, written once in the BASE (a stack overlay cannot set it)
    bind: {dv01: {target: {method: dv01}, kwargs: {ctx: "@ctx"}}}   # optional: overrides ONE default binding by name
    # also allowed: layers, pricer, pricers, params, doc. Anything else: [CFG-UNKNOWN-KEY] instruments.ois.foo: instrument 'ois': unknown key 'foo'; allowed ['asset_class', 'bind', 'conventions', 'doc', 'factory', 'layers', 'params', 'pricer', 'pricers']
```

Executed `build_spec` load-time errors (each `[CODE] instruments.<name>...: instrument '<name>': ...`):

```
[CFG-FACTORY] the factory must accept (pricer, ts, *, terms, conventions): got an unexpected keyword argument 'conventions'
[CFG-REQUIRED-BINDING] required names of asset class 'swap' are not bound: ['dv01'] (dv01: did you mean ['dv01']?). Bind each in `bind:` (pricebt never resolves a schema name by a same-named method)
[CFG-UNKNOWN-BINDING] binding 'dv01_' is not a name of asset class 'swap'; bindable: ['acme_zero_dv01', 'carry', ...]; did you mean ['dv01']?
[CFG-BINDING] binding 'dv01': AcmeSwap has no method 'dv0l'; it has ['acme_zero_dv01', 'delta_ladder', 'dv01', 'expired', 'gamma', 'rate', 'started', 'value']; did you mean ['dv01']?
[CFG-BINDING] instruments.ois.bind.rate: instrument 'ois': binding 'rate': 'rate'(*, ctx: 'Any') -> 'float' cannot take args=0 kwargs=['ctx', 'tenors']: got an unexpected keyword argument 'tenors'
[CFG-SCHEMA] asset_class 'bond' does not match the kit's 'swap'
[CFG-REQUIRED] a plain-callable factory needs `asset_class`
[CFG-UNKNOWN-BINDING] kit extension 'dv01' collides with a reserved row or a name of asset class 'swap'; extension names must be namespaced (for example 'lib.name')
```

Extension names (`Kit.extra`): the shipped ones are plain identifiers (`dv01_zero`, `gamma_zero`, `acme_zero_dv01`), while the collision message asks for a dotted `lib.name`. Use a plain identifier with your
library's prefix: the tie-out splits a quantity name on `.` when it builds an L2 row and the tolerance keys want identifiers, and a dotted extension name was never run through a tie-out. An extension
measure the reference stack does not bind cannot be audited (listing it in `audit_measures` stops the run at load with `[CFG-REF]`): leave it out and check it against your own reference in a test.

Extension schema (an in-house term beyond the shipped ones; executed, `tests/test_spec_review.py: LABEL_SCHEMA` is the repository's model):

```python
SCHEMA = {"schema_version": 1, "asset_class": "swap_tagged", "extends": "swap", "terms": {"desk_tag": {"kind": "term", "type": "string"}}}
kit = Kit(factory=factory, asset_class="swap_tagged", default_bind=SWAP_BIND, cls=LibSwap, schema=SCHEMA)     # the factory receives desk_tag in `terms`; it comes back in the resolved terms
```

`[CFG-SCHEMA] instruments.tagged: instrument 'tagged': asset_class 'swap' does not match the kit's 'swap_tagged'` and `... the kit's extension schema defines asset class 'swap_tagged', not 'swap'`
are the two ways to get the names out of step. A schema file can also be registered for a whole config: `registry: {schemas: [path.yaml]}` (relative to the config).

## 2. The import allow-list: what an adapter package outside `pricebt` needs

Only a DOTTED PATH imports anything (`factory`, a pricer `wrap`, a binding `target: {function: ...}`, a dotted `reduce:`, `registry.import`, a provider `type:`, `$ref`/`$call`); the text of a
config never does. `registry.resolve_dotted(path, allow=DEFAULT_ALLOW)`:

1. the MODULE part must be under an allowed prefix: `name == prefix or name.startswith(prefix + ".")` (`acme_adapter` covers `acme_adapter.swap`; `acme` would not cover `acmelib`);
2. it is imported with `importlib.import_module`: the package must be on `sys.path` (pricebt never adds a path: CLI `PYTHONPATH`; pytest `pytest.ini` has `pythonpath = . src tests`, so a test
   outside those puts its own package on `sys.path` from `Path(__file__)`, never from the working directory);
3. each attribute on the path is fetched, refused if it starts with an underscore, and its OWNER (`__module__`) must be under an allowed prefix too: a `Kit` instance reports
   `pricebt.contracts.spec` (so `<lib>_adapter:swap` passes), a function reports its own module (`<lib>_adapter.swap`), a `functools.partial` reports `functools` (refused).

The allowed prefixes are `pricebt` (always) plus `registry.allow` of the BASE config, read after the overlays are applied; an overlay cannot set `registry`:

```yaml
registry: {allow: [<lib>_adapter]}       # in the BASE config only
```

Executed messages (note that none says WHERE to add the prefix, and the doubled code is real):

```
[CFG-ALLOW] instruments.ois.factory: [CFG-ALLOW] module 'acme_adapter' is not under an allowed prefix ['pricebt']
[CFG-ALLOW] bind.carry.target.function: [CFG-ALLOW] module 'acme_adapter.swap' is not under an allowed prefix ['pricebt']
[CFG-STACK] registry: a stack may not set 'registry': only instruments.<name>.factory / .bind and market.pricers.<role>.wrap (everything else is shared by every stack)
```

To run a SHIPPED base with an external adapter: `--set "registry.allow=[<lib>_adapter]"` on the command line (`python -m pricebt tieout configs/synthetic_swap_carry.yaml --stack ... --set "registry.allow=[<lib>_adapter]"`),
or `run_tieout(base, stacks, sets=["registry.allow=[<lib>_adapter]"])`.

Where a spec is built in Python you must pass the same allow-list: `build_spec(name, raw, schemas=SchemaRegistry.default(), allow=("pricebt", "<lib>_adapter"))`. A test helper that builds the spec without `allow=`
(`tests/test_layer_conformance_adapters.py` does) fails for a Kit whose default block holds a `function` target: write your own helper.

## 3. Stack overlays: what a stack owns, and the two merge rules

`config/stacks.py: validate_overlay` allows exactly `instruments.<n>.factory`, `instruments.<n>.bind` and `market.pricers.<r>.wrap`; an overlay cannot add an instrument or a role. `base_digest` hashes
the config WITHOUT exactly those keys, so equal `base_hash` values prove two runs share their base (`TieoutError: the stacks do not share one base config (X1)` otherwise). Executed refusals:

```
[CFG-STACK] instruments.ois.conventions: a stack may not set instruments.ois.conventions: only ['factory', 'bind'] (conventions and terms are shared so a difference cannot be a convention)
[CFG-STACK] market.mdps: a stack may not set market.mdps: only market.pricers.<role>.wrap
```

Two merge rules, not stated in ADR 006 (executed):

1. **Overlay against base** (`apply_stack`): an overlay's `bind` REPLACES the base's whole `bind` block for that instrument (`tgt.update(spec)`): a base that overrides `gamma` loses it when an overlay overrides `dv01`.
2. **Kit against user** (`build_spec`): the Kit's `default_bind` is merged with the surviving `bind` BY NAME (`{**default_bind, **bind}`): one user entry replaces one default.

Executed: base `bind: {gamma: ...}` + overlay `bind: {dv01: ...}` gives `['dv01']` after rule 1; after rule 2 `dv01` is the overlay's (no `sign`: `1.0`) and `gamma` is the Kit's (`sign: -1.0`).
Put a unit conversion or a literal argument in the Kit's default block, not in a base config: an overlay replaces the base's block, and the report discloses each stack's effective bindings.

## 4. The `Stack` and the gs-style facade

```python
STACK = Stack(name="<lib>", wrap=wrap,
              instruments={"usd_sofr_ois": {"factory": swap, "conventions": dict(USD_SOFR_OIS_CONVENTIONS)}},     # raw `instruments:` entries by NAME (factory = the Kit OBJECT)
              defaults={"swap:USD": "usd_sofr_ois"},                                                             # "<asset_class>:<CCY>" -> name: how IRSwap('Pay','10y','USD') finds its spec
              accepts_gs={"floating_rate_option": ("USD-SOFR", "USD-SOFR-COMPOUND", "USD-SOFR-OIS", "SOFR")})    # gs constructor field -> values the shipped block's conventions already imply
```

```python
# fragment: <lib>_adapter and my_provider are yours
from pricebt.session import PricebtSession
from pricebt.instrument import IRSwap
with PricebtSession.use(market=my_provider, stack=<lib>_adapter.STACK, calendar="weekends") as s:      # the Stack OBJECT
    t = IRSwap("Pay", "10y", "USD", fixed_rate="ATMF", notional_amount=1e7, floating_rate_option="USD-SOFR")   # -> a TradeTemplate on the registered spec
```

Limits, stated exactly (all executed against the acme adapter):

| limit | where | message / effect | consequence |
|---|---|---|---|
| a `stack=` STRING is resolved with the default allow-list | `PricebtSession.__init__`: `resolve_dotted(stack)` | `RegistryError [CFG-ALLOW] module 'acme_adapter' is not under an allowed prefix ['pricebt']` | pass the `Stack` OBJECT; a dotted string works only for a package under `pricebt` |
| the session builds each spec with the default allow-list | `PricebtSession.instrument_spec`: `build_spec(name, raw, schemas=SchemaRegistry.default())`, no `allow=` | a default block with a `function` target (or a dotted `reduce:`, or a raw `factory:` string) outside `pricebt` fails: `[CFG-ALLOW] bind.carry.target.function: ... module 'acme_adapter.swap' is not under an allowed prefix ['pricebt']` | an external adapter must ship METHOD-ONLY (or `attribute` / `pricer_method`) default blocks for the facade, and reducers by registered NAME. Executed: the acme kit with its layers as methods of the instrument passes and `IRSwap(...).build(pricer, ts)` returns the resolved terms |
| `wrap=` given as a string | `PricebtSession._wraps` | same allow-list | pass the callable (or take the stack's) |
| gs fields that are conventions | `instrument.IRSwap` | `NotSupportedError: IRSwap: ['floating_rate_option'] are not terms of the registered instrument spec: its `conventions` block defines them. Change the convention on the spec (PricebtSession(instruments={...})) or write an adapter for the variant` | list in `accepts_gs` the values your default block already implies; nothing else is accepted |
| currency | `IRSwap` | `NotSupportedError: IRSwap: currency 'EUR' is not supported: results and instruments are in the session's reporting currency 'USD' (multi-currency is out of scope)` | one currency per session |
| other gs instruments | `instrument.py` | `IRSwaption`, FX, equity, credit, inflation, `Bond` raise `NotSupportedError` | write a Kit and use configs, or a pricable |
| a substituted spec | `accepted_gs` | a raw spec or `InstrumentSpec` given as `PricebtSession(instruments={"swap:USD": ...})` is not vouched for by the stack: `accepts_gs` yields `{}` | the substituted spec's conventions are the session's own |
| tenor case and rate unit | facade | tenors reach the factory untouched and LOWER-case (`'10y'`); numeric `fixed_rate` is a gs DECIMAL converted to percent in the facade (the only unit conversion there); `'ATMF'` becomes `fixed_rate: par`, `'ATMF+25'` becomes `par` with `extras: {par_spread_bp: 25.0}`, `0.04` becomes `4.0` (percent) | accept both cases of a tenor in the factory |

`PricebtSession.use(...)` makes the session the process-wide default for `GenericEngine()` and works as a context manager (restores the previous default on exit). A named calendar for the session
(`calendar="nyc"`) is registered in `pricebt.registries.CALENDARS` by the adapter that owns it when it is imported (`contrib/rateslib/__init__.py` registers `nyc`); otherwise `'weekends'`, a
`.json` path, a `pricebt.timeutil.Calendar` or a list of holidays.

## 5. The bond kit: differences from the swap kit

| | swap | bond |
|---|---|---|
| schema | `swap.yaml`: `side` pay/receive, `effective`, `maturity`, `notional`, `fixed_rate`, `extras` | `bond.yaml`: `side` buy/sell (aliases long/short), `security`, `notional`, `extras` (no `effective`, `maturity`, rate) |
| needed from the snapshot | a curve, calendar, fixings (started swaps) | a `QuoteSet` (quotes + reference data + aliases) and a calendar; no curve needed |
| factory resolves | dates and par from the curve and holidays | the `security` token through the snapshot: `sec = p.security(token)` (id or alias such as `CT10`), returns `security`, `coupon`, `maturity`, `notional` |
| `rate` binding | the par rate (`rate` method) | the yield: `("rate", "ytm")` binds the schema name `rate` to the method `ytm` (`BOND_BIND`) |
| extra measures | (ladder) | `ytm`, `duration`, `accrued` (`required: false`) |
| `dv01` sign | payer positive | a long bond's dv01 is NEGATIVE (yield up, value down) |
| ladder | `delta_ladder` required | none |
| layers | four, in curve space | four, in yield space (`carry`: coupon accrual and pull to par net of financing; `roll`: ageing down the on-the-run yield curve, 0 without one; `delta`, `convexity`) |
| `extras` | `par_spread_bp` | `repo: {gc_rate: percent or "pricer", specialness_bps, haircut}`; without `gc_rate` no financing |
| pricer capabilities (`bond.yaml`) | `calendar_advance`, `spot_date`, `par_rate` (all optional lookups) | `security`, `quote` (optional lookups; the shipped pricers expose them through `LOOKUPS`) |
| conventions | the swap vocabulary (`references/terms-and-conventions.md`) | its own vocabulary, among others `settlement_lag_days`, `payment_business_day_convention`, `compounding` (a yield frequency), `yield_convention` treasury/street, `ex_dividend_days`, `financing_day_count` |

Executed on the QuantLib bond kit (a tiny panel):

```
alias resolved to the id: {'side': 'buy', 'security': 'KIT001', 'notional': 10000000.0, 'coupon': 4.0, 'maturity': datetime.date(2033, 11, 15), 'direction': 1}
MarketDataUnavailable: market data unavailable at 2024-06-04 17:00:00-04:00: unknown security 'NOPE'; aliases ['CT10']
[PRICE-CONVENTION] KIT001: a clean price on the coupon date 2024-05-15 needs the snapshot's price_convention 'market' (accrued 0 on a coupon date), got 'unspecified'
MarketDataUnavailable: market data unavailable at 2024-06-04 17:00:00-04:00: the snapshot carries no bond quotes
```

If your library prices bonds: also write the bond Kit, its conventions (single supported values are normal: verify each), and run `run_kit` with `flat_bond_world` (`pricebt-layers-and-ladder`).
The shipped models are `src/pricebt/contrib/quantlib/bond.py`, `contrib/rateslib/bond.py` and `testing/refstack.py: bond`.

## 6. Two Python-level traps of a Kit package

* `import <lib>_adapter.swap as m` gives the KIT, not the module: the package `__init__` re-exports `swap` over the submodule (the shipped `contrib` packages do the same). To reach the module
  (for `carry`, `swap_factory`) use `sys.modules["<lib>_adapter.swap"]` or `importlib.import_module`.
* `register_reducer(name, fn)` is idempotent for the same function and refuses another under a taken name: `importlib.reload(<lib>_adapter.swap)` raises
  `[CFG-REDUCER] reducer 'acme_ladder_to_tenor_dict' is already registered`. A reducer named in a binding needs no allow-list but only exists once the package is imported, which the
  `factory` path does.
