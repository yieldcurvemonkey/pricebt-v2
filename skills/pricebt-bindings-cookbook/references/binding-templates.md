# Binding templates (copy, then replace the `<LIB>` parts with what YOUR library's documentation says)

Every real line below is from the worked example (`skills/pricebt-wire-external-library/example/acme_adapter/swap.py`, executed by `tests/test_skills_example_acme.py`) or from the shipped adapters
(`src/pricebt/testing/refstack.py`, `src/pricebt/contrib/quantlib/swap.py`). `<LIB>` marks what only your library's documentation can answer: never guess it.

## 1. The default block of a Kit (one entry per name; helpers keep it readable)

```python
# fragment: the <...> parts are placeholders for what YOUR library's documentation says
from pricebt.contracts.spec import Built, Kit

def _method(name, **extra):
    return {"target": {"method": name}, "kwargs": {"ctx": "@ctx", **extra}}          # the callable takes the mark context

def _layer(fn):
    return {"target": {"function": f"<your_pkg>.swap:{fn}"}, "kwargs": {"swap": "@instrument", "ctx": "@ctx"}, "sign": <-1 or 1>}   # needs registry.allow: [<your_pkg>]

DEFAULT_TENORS = ("3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y", "20Y", "30Y")   # your choice; the tie-out compares what you report

SWAP_BIND = {
    "value": _method("value"),                                       # returns a Valuation: NO sign/scale here (convert pv and cash in the method)
    "dv01": {**_method("dv01", tenors=list(DEFAULT_TENORS)), "sign": <-1 if <LIB> is the receiver's point of view, else omit>},
    "gamma": {**_method("gamma", tenors=list(DEFAULT_TENORS)), "sign": <same as dv01>},
    "rate": {**_method("rate"), "scale": <100.0 if <LIB> returns decimals, else omit>},              # the schema wants PERCENT
    "delta_ladder": {**_method("delta_ladder", tenors=list(DEFAULT_TENORS)), "keys": list(DEFAULT_TENORS), "reduce": "<lib>_ladder_to_tenor_dict", "sign": <as dv01>},
    "carry": _layer("carry"), "roll": _layer("roll"), "delta": _layer("delta"), "convexity": _layer("convexity"),
    "<lib>_zero_dv01": {**_method("<lib>_zero_dv01"), "sign": <as dv01>},                            # an EXTENSION measure: also declare it in Kit.extra
}

swap = Kit(factory=swap_factory, asset_class="swap", default_bind=SWAP_BIND, cls=<YourSwapClass>, extra={"<lib>_zero_dv01": "measure"}, doc="...")
```

Rules baked into this block: layers carry the same `sign` as the measures (a layer that forgets it fails the layer conformance kit: `fd_vs_delta_convexity`); the ladder `keys` equal the `tenors`
kwarg; the extension name is an identifier prefixed with the library's short name; the Kit has `cls` so method targets and signatures are checked at load.

The bond block differs: there is no `delta_ladder` (a bond has no such schema name: `[CFG-UNKNOWN-BINDING] ... binding 'delta_ladder' is not a name of asset class 'bond'`), `rate` is the bond's own yield in percent,
and `ytm duration accrued` are optional. The shipped pattern is `BOND_BIND` in `src/pricebt/testing/refstack.py`; the block below was executed as `Kit(factory=R.bond_factory, asset_class="bond", default_bind=BOND_BIND, cls=R.RefBond)`
(`R` = `pricebt.testing.refstack`): `build_spec` binds all eleven names, and the same block without the three optional ones binds eight.

```python
# fragment: a bond Kit's default block; `_method` is the helper above, "ytm", "accrued_interest" ... are the method names of the reference bond class (yours differ)
BOND_BIND = {name: _method(meth) for name, meth in (
    ("value", "value"), ("dv01", "dv01"), ("gamma", "gamma"), ("rate", "ytm"),          # required; `rate` is the yield in PERCENT; dv01 of a LONG is negative
    ("ytm", "ytm"), ("duration", "duration"), ("accrued", "accrued_interest"),          # optional measures
    ("carry", "carry"), ("roll", "roll"), ("delta", "delta"), ("convexity", "convexity"))}   # the four layers, `bond.*@1`
```

## 2. The instrument class the `method` targets name

```python
# fragment: the class name and the bodies are yours
class <YourSwapClass>:
    """One unit = one swap of `notional` (unsigned). `direction` +1 = payer/buyer (pricebt's sign). Plain data: it is deep-copied and pickled by the engine."""
    asset_class = "swap"

    def value(self, *, ctx):                                   # -> Valuation(ts, pv, cash, financing) per unit as built
        p, prev = ctx.pricer, ctx.prev_pricer                  # `ctx.pricer` is what YOUR wrap returned; `prev_pricer` the previous mark's (None at the first)
        ...
    def dv01(self, *, ctx, tenors=DEFAULT_TENORS): ...         # a float: currency per +1bp, ONE unit as built with its notional, payer-positive
    def delta_ladder(self, *, ctx, tenors=DEFAULT_TENORS): ... # the library's own shape; the reducer converts it
```

Scratch: `ctx.cache` is a dict shared by `value`, the layers and the measures of one position at one mark (use it to price once); `ctx.state` persists per position between marks.

## 3. The reducer for the library's ladder shape

```python
from pricebt.contracts.binding import REDUCERS, register_reducer

def ladder_to_tenor_dict(series):
    """<LIB>'s ladder -> {'3M': ...}: drop a bucket pricebt has no name for ONLY when it is empty, refuse it otherwise (dropping risk breaks sum(ladder) == dv01)."""
    ...
    return REDUCERS["series_to_tenor_dict"](series.drop(extra))

register_reducer("<lib>_ladder_to_tenor_dict", ladder_to_tenor_dict)      # once per function; never reload this module
```

## 4. When the pricing call lives on the pricer (a service client, an object that owns `npv(instrument)`)

```yaml
bind:
  value: {target: {pricer_method: npv}, args: ["@instrument"]}            # getattr(<what your wrap returned>, "npv")(<the built instrument>)
  rate:  {target: {pricer_method: level}, kwargs: {name: rate}}           # pattern of src/pricebt/testing/toys.py (_RATE)
```

(`pv` and `notional` take no binding on swap and bond: `[CFG-UNKNOWN-BINDING] ... 'notional' is derived by pricebt (terms.notional) and takes no binding`.) Your `wrap` returns the object that owns those methods; pricebt never looks inside it. `pricer_method` and `attribute` targets are NOT validated at load: call every one at a real mark
(`skills/pricebt-map-library-to-schemas/references/conformance-snippet.md`). A remote call per mark is slow and non-deterministic unless you memoise it per snapshot (skill `pricebt-enterprise-platform-patterns`).

## 5. Override ONE binding in config (in a tie-out: the stack overlay)

```yaml
instruments:
  usd_sofr_ois:
    factory: "<your_pkg>:swap"
    bind:
      rate: {target: {method: rate}, kwargs: {ctx: "@ctx"}}        # merges over the Kit's default block BY NAME; the other defaults stay
```

The block above is a single run. In a TIE-OUT the instrument entry belongs to the BASE config, which every stack shares (the reference included; `config/stacks.py::base_digest` excludes `bind`, so nothing flags a base `bind`): put a library-specific
override (a unit, a sign) in the stack overlay instead, as `skills/pricebt-wire-external-library/example/config/mistakes/acme_swap_no_percent.yaml` does. There the same `bind:` REPLACES the base config's whole
`bind` block (it does not merge with it) and is merged over the Kit's default block by name. A stack may set only `factory`, `bind` and the pricer's `wrap`.

## 6. What a negative control looks like (a check that has never failed proves nothing)

Copy the shipped overlay, delete ONE conversion, and expect ONE root failure (`L2.rate` or `L2.dv01`) first, plus the L3 baseline rows that read it (`tay_delta`, `tay_unexplained`, and for `rate` also `tay_convexity`, each with its `/unit` twin); nothing at L0, L1 or L4:
`skills/pricebt-wire-external-library/example/config/mistakes/acme_swap_no_percent.yaml` (no `scale: 100` on `rate`: `L2.rate` exceeds) and `acme_swap_wrong_sign.yaml` (no `sign: -1` on `dv01`: `L2.dv01` exceeds).
The later rows are the same mistake seen through the baseline, not a second bug. Details: skill `pricebt-debug-tieout-differences`.
