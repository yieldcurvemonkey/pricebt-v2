# 11 — gs instruments (swaption, bond family, caps/floors), the Instrument API, and pricing contexts / scenarios

Research note for the IR "pricing and risk" port (branch `v2-ir-risk`). Scope: (a) the gs instrument classes and the
Instrument/Priceable API compared with pricebt; (b) how pricebt generates instrument classes, and the exact, **dry-run
verified** steps to add `Bond` / `IRBondFuture` / `IRBondOption` / `IRCap` / `IRFloor` / `IRCapFloor`; (c) every API call,
measure and value grammar in the gs rates example notebooks, with the GS-server features marked; (d) `PricingContext`,
`HistoricalPricingContext`, market objects and scenarios, what can be ported generically through config, and a concrete
recommendation. Overlaps with R04 (`04-gs-instrument-risk-session.md` §1–3, §6) are referenced, not repeated.

## 0. Source conventions

| tag | path |
|---|---|
| `gs154:` | `C:\Users\chris\anaconda3\Lib\site-packages\gs_quant\` (gs_quant 1.5.4, the API reference) |
| `gs217:` | `C:\Users\chris\clee\gsquant-temp-claude\gs-quant\gs_quant\` (2.1.17 checkout, the behaviour reference) |
| `nb:` | `gs217:documentation\02_pricing_and_risk\` (notebook; `cN` = code/markdown cell index N) |
| `pb:` | `C:\Users\chris\clee\gsquant-temp-claude\pricebt-ir\` (this worktree) |
| `docs:` | `C:\Users\chris\clee\gsquant-temp-claude\gs-quant\docs\` (sphinx sources + built html) |

"Probe" = a script I ran against the real code (pricebt with the stir python; gs 1.5.4 with the base python, read-only,
outside any pricebt file). "Dry run" = the §3.3 checklist applied to a scratch copy of the worktree (never the worktree).

---

## 1. Decision-relevant facts (summary)

1. **The snapshot already has every class we need.** `pb:tests/data/gs_instruments_1_5_4.json` holds all 110
   `gs_quant.target.instrument` dataclasses, including `Bond`, `IRBondFuture`, `IRBondOption`, `IRCap`, `IRFloor`,
   `IRCapFloor`, `FRA`, `IRBasisSwap`, `IRXccySwap*`, `IRCMS*`. Their 1.5.4 and 2.1.17 field lists are identical.
2. **Adding them is NOT "data only" (decision 0.6 wording is inaccurate).** The dry run needed 10 edits in 8 files: two tool
   lists, re-running the 1.5.4 snapshot, 3 new enums (`BondStrikeType`, `SettlementType`, `CapOrFloor`), 3 new `AssetType`
   members (`BondFuture`, `BondOption`, `CapOrFloor`), the `_ENUM_CLASSES` map and `__all__` in `instrument/__init__.py`,
   and two tests (`test_common.py` AssetType literal; `test_skill_spec.py:89` uses `"Bond"` as its example of a
   *non*-instrument class). With those, the full suite is green (1195 passed) and a field-order mutation of `Bond` is caught
   by `test_gs_driven_symbol_parity[gs_quant.instrument.Bond]` (§3.3).
3. **Without re-running `tools/gs_api_snapshot.py`, a new class is never parity-checked**: `GS_DRIVEN_KEYS` comes from
   `gs_api_1_5_4.json`, which today lists only the 7 original instrument classes (`pb:tests/test_gs_api_parity.py:356-357`,
   `pb:tools/gs_api_snapshot.py:113-114`).
4. **gs 1.5.4 cannot scale bond-family or cap/floor instruments.** Only 13 classes define `scale_in_place`
   (`gs154:target/instrument.py`: EqContractDivOption, EqOption, EqVarianceSwap, FXBinary, FXCorrelationSwap, FXForward,
   FXOneTouch, FXOption, FXVarianceSwap, FXVolatilitySwap, InflationSwap, IRSwap, IRSwaption). Probe: gs
   `Bond(...).scale(2, check_resolved=False)` raises `NotImplementedError: scale_in_place not implemented on Bond`
   (`gs154:instrument/core.py:304-305`). pricebt's `quantity_` scale works for every class. This is a strict superset of gs;
   DEV-I1 ("scaling edits size fields") arguably covers it, but its text does not say "and scales classes gs cannot".
5. **`Bond`, `IRBondFuture`, `IRBondOption` are not externally supported by GS.** They are absent from the Instruments
   tutorial's "Supported Instruments" table (`nb:00_instruments_and_measures\tutorials\Instruments.ipynb` c12) and from
   `docs:instrument.rst`; no documentation notebook constructs them; `IRBondFuture` appears only in gs's
   PredefinedAssetEngine tests (`gs217:test/backtest/test_backtest_predefined.py:35-151`). The pricebt asset config would
   define all of their pricing semantics; there is no gs reference number to match.
6. **Three silent / hard parity gaps in pricebt that the notebooks exercise** (probe, §2.5):
   - `swap.termination_date = '10y'` stores an instance attribute that shadows `__getattr__`; `kwargs`, `as_dict()`,
     identity and pricing keep the old value. **Silent wrong number.** gs routes it through `Base.__setattr__`
     (`gs154:base.py:258-271`). Used by `nb:00_instruments_and_measures\examples\00_instrument_basics\03_set-a-property.ipynb` c3.
   - `PricingContext.current = PricingContext(...)` raises `AttributeError` (no setter; `pb:src/pricebt/markets/__init__.py:24-27`).
     gs has a setter (`gs217:context_base.py:63-77`). Used by `Pricing_Context.ipynb` c6.
   - `PricingContext(market=...)` is accepted and **silently ignored** (`pb:src/pricebt/markets/__init__.py:30-54`), so
     `with PricingContext(market=overlay): x.price()` prices the base market with no error.
7. **Scenario classes belong in `pricebt/risk/__init__.py`, not `markets/`.** gs exports them from `gs_quant.risk`
   (`gs154:risk/__init__.py:19-23`), and the asset-agnostic guard scans `markets/**` for `(?<![a-z])tenor(?![a-z])`
   (`pb:tests/guards/scan.py:133-137`), which `CurveScenario.tenor_start/tenor_end` and `IndexCurveShift.tenor` would trip.
   Market objects (`CloseMarket`, `OverlayMarket`, `RelativeMarket`) can live in `markets/__init__.py`.
8. **The cheapest generic seam for markets/scenarios already exists:** `PricingService` threads `(d, csa)` as an opaque
   hashable through the market, resolution, trade and unit-value caches (`pb:src/pricebt/assets/pricing.py:104-131,
   390-458`). Widening that `csa` slot to a frozen "market spec" `(csa, location, market_date, scenarios)` plus an optional
   config section `scenarios: {<gs scenario class>: '<transform expr>'}` ports CloseMarket-date overrides,
   `market_data_location`, `CurveScenario`, `MarketDataShockBasedScenario`, `IndexCurveShift`, `CurveOverlay` and (with a
   date roll in the service) `RollFwd` without the engine learning any asset semantics (§5.6).
9. **Every number in the rates notebooks is GS-server-computed**: resolution defaults, relative dates, `'ATM±x'`, `'25d'`,
   `'10000/pv'`, `'=solvefor(...)'`, `IMM1`, Price, all Greeks, Cashflows, `market()`, scenarios. The client side is only
   construction, enum/camelCase coercion, dict views, clone/scale, containers and context stacking (§4.5).

---

## 2. gs instrument classes and the Instrument/Priceable API

All classes below are `@handle_camel_case_args @dataclass_json(letter_case=CAMEL) @dataclass(unsafe_hash=True, repr=False)`
subclasses of `Instrument`; `asset_class` and `type_` are `field(init=False, default=...)`; `name` is the last field.
Every field default is `None` unless shown. Field lists are **identical in 1.5.4 and 2.1.17** (compared field by field).

### 2.1 IRSwap and IRSwaption

`IRSwap`: `gs154:target/instrument.py:2541` / `gs217:...:2627`. Full field table in R04 §1. `scale_in_place`
(`gs154:target/instrument.py:2576-2596`): `notional_amount *= abs(s)`; if `s<0`, flip `pay_or_receive` (Pay↔Receive) and
`fee *= -1`; unresolved + `check_resolved` → `RuntimeError('Can only scale resolved instruments')`; unresolved without
`notional_amount`/`pay_or_receive` → `RuntimeError('Can only scale unresolved instruments with the buysell and primary size
fields set')`.

`IRSwaption`: `gs154:target/instrument.py:2599` / `gs217:...:2685`. Rates / Swaption.

| # | field | type (enum) | notes |
|---|---|---|---|
| 1 | pay_or_receive | `gs_quant.common.PayReceive` (Pay / `'Rec'` / Straddle) | `'receive'`/`'receiver'` → Receive (`gs154:common.py:53-58`); `'Payer'` → `ValueError` (probe) |
| 2 | termination_date | date \| str | underlying swap end; tenor is relative to `effective_date` (midcurve, §4.2) |
| 3 | notional_currency | Currency | |
| 4 | effective_date | date \| str | tenor relative to **expiration** (§4.2) |
| 5 | notional_amount | float \| str | |
| 6 | expiration_date | date \| str | `'IMM1'` allowed (§4.2) |
| 7–12 | floating_rate_option, floating_rate_designated_maturity, floating_rate_spread (**float only**), floating_rate_frequency, floating_rate_day_count_fraction (DayCountFraction), floating_rate_business_day_convention (BusinessDayConvention) | | |
| 13–15 | fixed_rate_frequency, fixed_rate_day_count_fraction, fixed_rate_business_day_convention | | no `fixed_rate`: the fixed rate is `strike` |
| 16 | strike | float \| str | grammar in §4.2 |
| 17 | premium | float \| str | sign rule in §4.2 |
| 18 | premium_payment_date | date \| str | |
| 19 | fee | float, default **0.0** | |
| 20–21 | fee_currency (Currency), fee_payment_date | | |
| 22 | clearing_house | SwapClearingHouse | |
| 23 | settlement | `gs_quant.target.common.SwapSettlement` | `Phys.CLEARED`, `Physical`, `Cash.CollatCash`, `Cash.PYU` |
| 24 | buy_sell | BuySell | |
| 25 | name | str | |

`IRSwaption.scale_in_place` (`gs154:target/instrument.py:2628-2645`), quoted:

```python
if scaling is None or scaling == 1: return
if self.unresolved is None:
    if check_resolved: raise RuntimeError('Can only scale resolved instruments')
    if self.notional_amount is None or self.buy_sell is None:
        raise RuntimeError('Can only scale unresolved instruments with the buysell and primary size fields set')
    ...
self.notional_amount *= abs(scaling)
if scaling < 0:
    flip_dict = {BuySell.Buy: BuySell.Sell, BuySell.Sell: BuySell.Buy}
    self.buy_sell = flip_dict[self.buy_sell]
    if check_resolved or self.fee is not None:
        self.fee *= -1
```

So gs defines `flip(Buy x N) == Sell x N` — `Price(Sell) = −Price(Buy)` is the gs contract (probe: scaling a Buy 1e6
swaption by −2 gives `notional_amount=2e6`, `buy_sell=Sell`, `fee=-0.0`). `pay_or_receive` is **not** flipped for
swaptions (a payer stays a payer).

### 2.2 Bond family and caps/floors — exact 1.5.4 fields

| class | gs154 line | gs217 line | asset_class / type_ | init fields in order | `scale_in_place` |
|---|---|---|---|---|---|
| `Bond` | 81 | 81 | **Cross Asset** / Bond | `buy_sell` (BuySell), `identifier` (str), `identifier_type` (UnderlierType), `size` (float), `settlement_date` (date\|str), `settlement_currency` (Currency), `name` | none |
| `IRBondFuture` | 1513 | 1595 | Rates / **BondFuture** | `identifier` (str), `identifier_type` (UnderlierType), `buy_sell` (BuySell), `notional_amount` (float\|str), `underlier` (float\|str), `currency` (Currency), `expiration_date` (date\|str), `exchange` (str), `traded_price` (float), `trade_settle` (date\|str), `description` (str), `name` | none |
| `IRBondOption` | 2367 | 2453 | Rates / **BondOption** | `underlier` (float\|str), `notional_amount` (float\|str), `expiration_date` (date\|str), `option_type` (OptionType), `effective_date` (date\|str), `strike` (float\|str), `strike_type` (**BondStrikeType**), `premium` (float\|str), `premium_payment_date` (date\|str), `fee`=**0.0**, `fee_currency` (Currency), `fee_payment_date` (date\|str), `settlement` (**SettlementType**), `underlier_type` (UnderlierType), `name` | none |
| `IRCap` | 1533 | 1615 | Rates / Cap | `termination_date`, `notional_currency` (Currency), `notional_amount`, `effective_date`, `floating_rate_option`, `floating_rate_designated_maturity`, `floating_rate_frequency`, `floating_rate_day_count_fraction` (DayCountFraction), `floating_rate_business_day_convention` (BusinessDayConvention), `cap_rate` (float\|str), `premium`, `premium_payment_date`, `fee`=**0.0**, `fee_currency` (Currency), `fee_payment_date`, `name` | none |
| `IRFloor` | 1582 | 1664 | Rates / Floor | as IRCap with `floor_rate` in place of `cap_rate` | none |
| `IRCapFloor` | 1557 | 1639 | Rates / **CapOrFloor** | `cap_floor` (**CapOrFloor**), then IRCap's list with `strike` in place of `cap_rate`, and **`fee`=None** (not 0.0) | none |

Notes:
- `Bond`'s size field is `size`, not `notional_amount`; it has no `notional_currency` (only `settlement_currency`). A
  pricebt `size_attribute: size` would serve `RebalanceAction(size_parameter='size')`.
- Doc strings (`docs:_build\html\classes\gs_quant.instrument.IRCap.html`): `cap_rate` — "The rate of this cap, as value,
  percent or at-the-money e.g. 62.5, 95%, ATM-25, ATMF".
- Every default in these six classes is a plain literal (`None`/`0.0`), so the generator's documented gap for enum-valued
  or `default_factory` defaults (`pb:tools/gen_gs_fields.py:15-22`) is not hit.

### 2.3 Enums the new classes need (exact 1.5.4 members, from `pb:tests/data/gs_api_1_5_4.json`)

| enum | members `(name, value)` | in pricebt.common today? |
|---|---|---|
| `UnderlierType` | BBID, BID, CUSIP, ISIN, SEDOL, RIC, Ticker (name == value) | yes (`pb:src/pricebt/common.py:127`) |
| `BondStrikeType` | `Price='Price'`, `Yield='Yield'` | **no** |
| `SettlementType` | `Cash='Cash'`, `Physical='Physical'` | **no** |
| `CapOrFloor` | `Cap='Cap'`, `Floor='Floor'`, `Straddle='Straddle'` | **no** |
| `AssetType` additions | `BondFuture`, `BondOption`, `CapOrFloor` (Bond, Cap, Floor already present) | **no** (`pb:src/pricebt/common.py:196-212`; a subset by design) |
| `AssetClass` | `Cross_Asset='Cross Asset'` | yes |

`pricebt.instrument._build_class` does `AssetType(spec["type_"])` at import (`pb:src/pricebt/instrument/__init__.py:344`);
probe: `AssetType('BondFuture')` on the worktree raises `ValueError: 'BondFuture' is not a valid AssetType`, so without the
`AssetType` additions **`import pricebt.instrument` fails**.

### 2.4 gs Instrument / Priceable API vs pricebt

Class chain: `Instrument(PriceableImpl, InstrumentBase)` (`gs154:instrument/core.py:44`), `PriceableImpl(Priceable)`
(`gs154:priceable.py:30`), `Priceable(Base)` (`gs154:base.py:430`), `InstrumentBase(Base)` (`gs154:base.py:577`). R04 §3.1-3.2
gives the full semantics of `clone`, `resolve`, `resolved`, `calc`, `scale`, `as_dict`, `to_dict`, `from_dict`,
`from_instance`, `compose`. What is new here is the pricebt status of each member.

| member | gs 1.5.4 source | gs semantics (quoted where short) | pricebt | status |
|---|---|---|---|---|
| `__init__` + camelCase kwargs | base.py:95-116 | non-ALL-CAPS kwargs snake-cased; both spellings → `ValueError('{} and {} both specified')` | instrument/__init__.py:69-107 | ported |
| enum coercion in init | base.py:258-271, 314-330 | `Base.__setattr__` → `__coerce_value`; invalid → `ValueError` | instrument/__init__.py:77-81 (strict) | ported |
| attribute **set** `x.f = v` | base.py:258-271 | snake-cases `key`, maps names; `init=False` field → `ValueError(f'{key} cannot be set')`; value coerced | none: plain `object.__setattr__` | **gap, silent** (§2.5) |
| attribute get, camelCase (`x.fixedRate`) | base.py:242-256 | camelCase → field | `__getattr__` knows snake_case only (instrument/__init__.py:161-181) | gap (AttributeError) |
| `properties()` / `properties_init()` | base.py:376, 381 | public field names, trailing `_` stripped; IRSwaption 27, Bond 9 (probe) | absent | excepted (`pb:tests/data/gs_api_exceptions.yaml:51-60`) |
| `default_instance()` | base.py:407 | `cls(**{init fields: default})` | absent | excepted |
| `clone(**kwargs)` | base.py:358 + 628-634 | `dataclasses.replace`, then copies `unresolved`, `metadata`, `resolution_key` | instrument/__init__.py:222-244 (`**kw`; field edit on resolved → ValueError) | ported with rule |
| `as_dict(as_camel_case=False)` | base.py:385-404 | non-None fields, enums kept, `type_`→`type`, **includes `name`** | instrument/__init__.py:288-306 (+`quantity_`, DEV-I2) | ported |
| `to_dict()` | dataclasses_json | camelCase keys, dates → ISO strings, `name` dropped | instrument/__init__.py:308-311 (snake keys, native dates) | differs in key case / date type (not listed as DEV) |
| `from_dict` / `from_json` / `to_json` / `schema` | core.py:216-248 | build from dict via `(asset_class, type)` map | absent | excepted (non-goal §2.3) |
| `from_quick_entry`, `from_asset_id(s)` | core.py:250-292 | **GS server** (GsParserApi / GsAssetApi) | absent | excepted |
| `provider` | core.py:70-72 | `GsRiskApi` | absent | excepted |
| `metadata` (+setter) | base.py:602-611 | free slot, carried by `clone` | absent | excepted (TriggerInfo uses `metadata` in gs triggers) |
| `instrument_quantity` / `quantity_` | base.py:578, 584-586 | `InitVar` default **1** (int) | property; `quantity_=1.0` kw-only | ported (1 vs 1.0 only) |
| `resolution_key`, `unresolved` | base.py:588-600 | properties | plain attributes | ported (as attributes) |
| `resolve(in_place=True)` | core.py:74-114 | `calc(ResolvedInstrumentValues, fn=...)`; in-place under HPC → `RuntimeError` | instrument/__init__.py:271-274 → pricing seam | ported |
| `calc(risk_measure, fn=None)` | core.py:116-214 | single or iterable; futures inside a context; deprecation warnings | :276-279 (`risk_measure_or_iterable`) | ported (rename excepted) |
| `price(currency=None)` | priceable.py:75-88 | `calc(Price(currency=...)) if currency else calc(Price)` | :281-282 | ported |
| `dollar_price()` | priceable.py:44-73 | `calc(DollarPrice)` | :284-285 | ported |
| `market()` | priceable.py:90-140 | `calc(MarketData, fn=...)` → `OverlayMarket(base_market=result.risk_key.market, market_data={coord: value})`; historical → `{date: OverlayMarket(CloseMarket(date, location), ...)}`; non-`Granted` values → `'redacted'` | absent | excepted; **GS server** (§5) |
| `scale(s, in_place=True, check_resolved=True)` | core.py:301-311 | needs `scale_in_place` else `NotImplementedError` | :246-258 `quantity_ *= s` | DEV-I1 |
| `flip(in_place=True)` | core.py:298-299 | `scale(-1, in_place)` | :260-261 | ported |
| `compose(components)` | core.py:294-296 | `{resolution date: result}` | absent | excepted |
| `__repr__` | core.py:48-49 | `ClassName(name)` / `ClassName` | :217-220 | ported |
| `__eq__`/`__hash__` | dataclass `unsafe_hash=True` | all fields incl. `name`; hash changes on mutation | identity tuple (§5.1 item 3) | by design |

### 2.5 Verified pricebt gaps (probe output, stir python, `PYTHONPATH=src`)

```
setattr field                 -> ('10y', '7y', '7y')      # (attr, kwargs['termination_date'], as_dict()['termination_date'])
camel getattr fixedRate       -> AttributeError fixedRate
properties()                  -> AttributeError
from_dict                     -> AttributeError
market()                      -> AttributeError market
straddle                      -> Straddle
buy_sell 'sell'               -> Sell
settlement 'Cash.PYU'         -> Cash.PYU ;  'Cash' -> ValueError: 'Cash' is not a valid SwapSettlement
strike '=solvefor(...)'       -> passed through untouched (DEV-I6)
PricingContext.current = ...  -> AttributeError: property 'current' of '_ContextMeta' object has no setter
PricingContext(market='x').market / .market_data_location -> AttributeError (attribute not stored)
HistoricalPricingContext(10).date_range[:3] -> (2026-09-28, 2026-09-25, 2026-09-24)   # descending, as gs
```

Same probes on gs 1.5.4 (base python): `x.termination_date='5y'; x.fixedRate=0.02` → both read back through either
spelling; `x.asset_class='FX'` → `ValueError: asset_class cannot be set`; `IRSwaption(pay_or_receive='receiver')` → `Rec`;
`IRSwaption(buy_sell='sell', settlement='cash.pyu')` → `Sell Cash.PYU`; `strike='ATM+50'`, `premium='10k'` stay strings.

**The setattr gap is the dangerous one**: the value the user sees (`swap.termination_date → '10y'`) differs from the value
priced (`'7y'`), with no error.

---

## 3. How pricebt generates instrument classes, and how to add Bond et al.

### 3.1 Pipeline

1. `pb:tools/gs_api_snapshot.py` (base python, gs 1.5.4) writes
   - `tests/data/gs_instruments_1_5_4.json`: **all** 110 `gs_quant.target.instrument` dataclasses with
     `[name, init, default_repr, annotation_str]` (`:251-269`);
   - `tests/data/gs_api_1_5_4.json`: descriptors (signature/methods/properties/method_signatures) for the classes in
     `INSTRUMENT_CLASS_NAMES` (`:113-114`, currently 7), the backtests modules, risk measures, `gs_quant.common` enums,
     contexts, Portfolio, datetime, session (`:208-248`).
2. `pb:tools/gen_gs_fields.py` (stir python, no gs import) reads the instruments JSON for `CLASS_LIST` (`:41`) and writes
   `src/pricebt/instrument/_gs_fields.py`. `coerce_tag` = enum class name iff the annotation is exactly
   `typing.Optional[<dotted name>]` and the name is in the literal `PRICEBT_ENUM_NAMES` (`:45-65`).
3. `pb:src/pricebt/instrument/__init__.py:335-351` builds one class per `GS_FIELDS` entry by `exec` of a generated
   `__init__` (gs order, gs defaults, `name` last, then kw-only `pricebt_asset=None, quantity_=1.0, **kwargs`), maps each
   `coerce_tag` through the hand-written `_ENUM_CLASSES` dict (`:48-64`), and sets `asset_class=AssetClass(...)`,
   `type_=AssetType(...)`. `__all__` (`:365-383`) is hand-written.
4. Tests that pin this: `pb:tests/test_gen_gs_fields.py:51-56` (regenerated file byte-identical), `:77-104`
   (`PRICEBT_ENUM_NAMES` == the set of enum classes in `pricebt.common`, exactly); `pb:tests/test_gs_api_parity.py:311-319`
   (exact prefix + tail signature rule), `:402-408` (every `pricebt.common` enum exactly equals gs's members unless a
   `subset: true` exception exists — only `Currency` and `AssetType` have one, `gs_api_exceptions.yaml:304-320`);
   `pb:tests/test_common.py:124-137` (a literal `AssetType` member dict); `pb:tests/test_instrument.py:303-307` (no field
   collides with a pricebt extension name).

Annotation shapes that matter to `coerce_tag_for`: fields typed `Union[float, str, NoneType]` or `Union[date, str,
NoneType]` get no tag (pass-through), which is correct (`'ATM+25'`, `'10y'`). Enum fields are always `Optional[Enum]`.

### 3.2 Guards that do NOT need changes

No new files are needed, so `tests/guards/dag.py` `SKELETON_PATHS`/`DAG_TIERS` are unaffected. `instrument/` is exempt
from the asset-agnostic scan (`pb:tests/guards/scan.py:151-158`). The token scan has no bond/cap vocabulary.

### 3.3 Verified checklist (dry run on a scratch copy; baseline first: 336 passed, plus 1 import-blocker failure that was an
artifact of my copy omitting `notebooks/src` and passed once it was added)

| step | file | change |
|---|---|---|
| 1 | `tools/gs_api_snapshot.py:113-114` | `INSTRUMENT_CLASS_NAMES += ["Bond","IRBondFuture","IRBondOption","IRCap","IRFloor","IRCapFloor"]` |
| 2 | run | `C:\Users\chris\anaconda3\python.exe tools\gs_api_snapshot.py` → "246 symbols, 3 requested-not-found". Semantic diff vs the committed JSON: **added exactly** the 6 `gs_quant.instrument.*` symbols; `gs_instruments_1_5_4.json` changed only in `generated_date`; two pre-existing symbols changed only by a `datetime.date(2026, 9, 27→28)` default repr (`gs_quant.datetime.prev_business_date`, `...OisFixingCashAccrualModel`), both already excepted (`gs_api_exceptions.yaml:168-173` skip, `:329-342` `ignore_default`). The tool writes CRLF, matching the committed files (`git ls-files --eol`: `i/crlf w/crlf`), so a plain `git diff` shows only these lines. |
| 3 | `tools/gen_gs_fields.py:41` | `CLASS_LIST +=` the same 6 names |
| 4 | `tools/gen_gs_fields.py:45-52` | `PRICEBT_ENUM_NAMES += ["BondStrikeType", "SettlementType", "CapOrFloor"]` |
| 5 | `src/pricebt/common.py` | add `class BondStrikeType(EnumBase, str, Enum): Price="Price"; Yield="Yield"`, `class SettlementType(...): Cash="Cash"; Physical="Physical"`, `class CapOrFloor(...): Cap="Cap"; Floor="Floor"; Straddle="Straddle"`; add `BondFuture`, `BondOption`, `CapOrFloor` to `AssetType` |
| 6 | `src/pricebt/instrument/__init__.py` | import the 3 enums; add them to `_ENUM_CLASSES`; add the 6 class names to `__all__` (+ module docstring) |
| 7 | run | `C:\Users\chris\anaconda3\envs\stir\python.exe tools\gen_gs_fields.py` → "13 classes"; new tags: `Bond.buy_sell=BuySell`, `identifier_type=UnderlierType`, `IRBondOption.strike_type=BondStrikeType`, `settlement=SettlementType`, `IRCapFloor.cap_floor=CapOrFloor` |
| 8 | `tests/test_common.py:124-137` | add the 3 `AssetType` members to the literal dict (else `test_enum_members_and_values[AssetType]` fails) |
| 9 | `tests/skills/test_skill_spec.py:89` | replace `"Bond"` (its example of a *non*-instrument class) with a still-absent class, e.g. `"IRXccySwap"` |
| 10 | docs | DESIGN.md decision 0.6 ("v2 generates 7 classes", line 35) and §12.3 ("the 7 generated instrument classes", line 982); `tools/gs_api_snapshot.py:48` docstring; `tools/gen_gs_fields.py:15` docstring ("the current 7 mirrored classes") |

Result: full suite (`-m "not notebook and not live_arbs"`) **1195 passed**. Before steps 8-9: 3 real failures
(`test_common` AssetType, `test_skill_spec`), plus 2 artifacts of my partial copy (skills mirror, doc links) that pass once
`.claude/` and `notebooks/` are copied. Smoke of the new classes (probe):

```
Bond('buy','US912810TM08','isin',1e6,name='ust')  -> buy_sell Buy, identifier_type ISIN, asset_class Cross Asset, type_ Bond
signature(Bond) -> [buy_sell, identifier, identifier_type, size, settlement_date, settlement_currency, name, pricebt_asset, quantity_, kwargs]
IRBondOption(option_type='call', strike_type='price', settlement='physical', underlier_type='ISIN') -> Call Price Physical ISIN
IRBondOption(strike_type='clean') -> ValueError: 'clean' is not a valid BondStrikeType
IRCapFloor(cap_floor='floor', notional_currency='usd').type_ -> CapOrFloor ; IRBondFuture(...).type_ -> BondFuture
resolved Bond .scale(-2) -> quantity_ -2.0      (gs: NotImplementedError)
```

Non-vacuity: swapping `identifier`/`identifier_type` in the generated `Bond` entry fails
`test_gs_driven_symbol_parity[gs_quant.instrument.Bond]` and `test_regenerates_byte_identical`.

### 3.4 Findings about the pipeline

- **Decision 0.6 overstates "data only".** Any class that introduces a new enum or a new `type_` needs hand edits in
  `common.py`, `instrument/__init__.py` and `test_common.py`. Cheapest reduction (optional): build `_ENUM_CLASSES` as
  `{tag: getattr(pricebt.common, tag)}` over the tags in `GS_FIELDS`, and extend `__all__` from `GS_FIELDS` keys. Then
  adding a class whose enums already exist is truly steps 1-3 + 7; new enums still need step 5 (deliberately: members must
  be copied verbatim). `INSTRUMENT_CLASS_NAMES` and `CLASS_LIST` are the same list in two files; they could share one.
- **The exceptions YAML needs no new rows**: the `gs_quant.instrument.*` globs (`gs_api_exceptions.yaml:34-102`) already
  cover the 6 classes (their gs methods are a subset of IRSwap's; `scale_in_place` is already in `missing`).
- **DEV coverage for "pricebt scales what gs cannot"**: add one sentence to DEV-I1 or a new row, since
  HedgeAction/AddScaledTradeAction/`Portfolio.scale` with a `Bond` crash in gs but run in pricebt.

---

## 4. Rates notebooks: every API call, measure and value grammar

### 4.1 Inventory (paths under `nb:00_instruments_and_measures\`)

| notebook | instruments / constructors | measures & calls | contexts / other |
|---|---|---|---|
| `00_instrument_basics\01_view-trade-properties` | `IRSwap(PayReceive.Pay, '5y', Currency.USD)` | `IRSwap.properties()`, `.as_dict()` | `GsSession.use(Environment.PROD, client_id, client_secret, scopes=('run_analytics',))` |
| `..\02_get-a-property` | `IRSwap(PayReceive.Receive, '13m', Currency.GBP, notional_amount=5e6)` | `.termination_date`, `.to_dict()` | |
| `..\03_set-a-property` | `IRSwap(PayReceive.Pay, '7y', Currency.EUR)` | **`my_swap.termination_date = '10y'`** | |
| `..\04_enum-property` | `IRSwap(PayReceive.Receive, '5y', Currency.GBP, effective_date='1m')`; `IRSwap('Receive', ...)` | `list(PayReceive)` | |
| `..\05_resolve-a-trade` | `IRSwap(PayReceive.Pay, '5y', fixed_rate='atm+30')` | `.resolve()`, `.to_dict()`; `resolve(in_place=False)` → `.result()[date]` | `HistoricalPricingContext(date(2025,3,25), date(2025,3,27))` |
| `..\06_market-data` | `IRSwap(Receive,'10y',EUR, fixed_rate=-0.025)`; `IRSwaption(Receive,'5y',EUR, expiration_date='3m')` | `.market()` → `.market_data[0].coordinate/.value`, `.market_data_dict`, `market[c] = -0.02`; `MarketDataCoordinate.from_string('IR_EUR_SWAP_10Y.ATMRATE')`, `'IR VOL_EUR-EURIBOR-TELERATE_SWAPTION_5Y,3M'`; `OverlayMarket({coord: value})` | `PricingContext(market=market)` → `price_f.result()` |
| `..\07_float-with-info` | `IRSwap(Receive, '5y'/'10y', EUR, fixed_rate='atm+10')` | `.price()` → `FloatWithInfo`: `.raw_value`, `.unit`, `.risk_key`, `+` with FloatWithInfo / float | |
| `01_rates\01_view_swap_definition` | — | `IRSwap?` | |
| `01_rates\02_swap_trade_construction` | `IRSwap()` (all defaults); every field variant (see §4.3) | `Portfolio()`, `.append`, `swaps.price()`, `.as_dict()`, `IRSwap.from_dict(d)`, `.resolve()` | |
| `01_rates\03_calc_swap_price` | `IRSwap(Receive, date(2055,1,15), EUR)` | `.price()` | |
| `01_rates\04_calc_swap_risk_measures` | `IRSwap(Pay,'10y',GBP)` | `swap.calc((DollarPrice, IRDelta(aggregation_level='Type')))`; `result[IRDelta(aggregation_level='Type')]` | |
| `01_rates\05_..._in_pricing_context` | `IRSwap(Pay,'10y',notional_currency=GBP)` | `.price()` → future | `PricingContext(pricing_date=date(2019,1,15), market_data_location='NYC')` |
| `01_rates\06_..._historically` | 2× `IRSwap(Pay,'30y',USD,fixed_rate='atm+5')`, one pre-resolved | `.price()` → series | `HistoricalPricingContext(150)` (int = last 150 business days) |
| `01_rates\07_..._risks_in_pricing_context` | `IRSwap(Pay,'12y',GBP)` | `calc((DollarPrice, IRDelta, IRDelta(aggregation_level='Type')))`; `res_f[DollarPrice]` | `PricingContext(pricing_date=date(2019,1,15))` |
| `01_rates\08_..._risk_historically` | `IRSwap(Receive,'5y',EUR,fixed_rate='atm+10')` | `IRDelta(aggregation_level=AggregationLevel.Type, currency='local')` | `HistoricalPricingContext(d0, d1, show_progress=True)` |
| `01_rates\09_swaption_trade_construction` | `IRSwaption()` and every field variant (§4.2); `IRSwap(notional_currency='GBP', effective_date='10y')` | `IRSwaption.properties()`, `swaptions.price().to_frame()`, `swap.fixed_rate*100`, `swaption.strike*100` | |
| `01_rates\10_straddle_price` | `IRSwaption(pay_or_receive='Straddle')`, payer, receiver | `.price()`; `payer.price() + receiver.price()` | |
| `01_rates\11_midcurve_swaption_price` | `IRSwaption('Pay', expiration_date='6m', effective_date='2y', termination_date='1y')`; `expiration_date='IMM1'` | `.price()` | |
| `01_rates\12_swap_future_cashflows` | `IRSwap(Receive,'5y',EUR,fixed_rate='atm+10')` | `swap.calc(Cashflows)` → DataFrame | |
| `01_rates\13_calc_xccy_swap_price` | `IRXccySwap(payer_currency, receiver_currency, effective_date='3m', termination_date='10y', payer_spread=-0.0005)`; `IRXccySwapFltFlt(...)` | `.price()` | |
| `01_rates\14_calc_xccy_swap_risk_measures` | `IRXccySwap(...)` | `calc((IRDelta, IRXccyDelta))`, `calc(IRXccyDeltaParallel)` | |
| `01_rates\15_spread_option_grid_pricing` | `IRCMSSpreadOption(termination_date, notional_currency, notional_amount, index1_tenor, index2_tenor, name)`; `Portfolio([Portfolio([...], name=p) ...])` with **tuple names** | `portfolios.calc(Price)`, `calc(IRAnnualImpliedVol)`, `.to_frame()`, `* 10000` | `with PricingContext():` |
| `01_rates\16_change_discount_curve` | `IRSwaption(Receive,'5y',EUR, settlement='Cash.CollatCash')` | `.resolve()`, `.price()` | `PricingContext(pricing_date=today, csa_term='EUR-OIS' / 'EUR-EuroSTR')` |
| `01_rates\17_calc_xccy_swap_cashflows` | `IRXccySwap(...)`, `IRXccySwapFixFix(payer_rate=0.01, receiver_rate=0.015, ...)` | `calc(Cashflows).head()`; `mtm_swap.clone(initial_fx_rate=1.2, payer_spread=mtm_swap.payer_spread)` | |
| `01_rates\18_solve_present_value` | 2× `IRSwaption('Receive','30y','USD', notional_amount=10e6/20e6, expiration_date='3m', strike='atmf' / '=solvefor([30y_buy].risk.Price,pv)', buy_sell='Buy'/'Sell', name='30y_buy')` | `Portfolio((...))`, `.resolve(in_place=False)` iterated, `.strike*1e4`, `.price()` iterated | |
| `01_rates\19_solve_delta` | `IRSwaption('Pay','5y','USD', expiration_date='1y', buy_sell='Sell', name='payer_swaption')`; `IRSwap('Receive','5y','USD', fixed_rate='atm', notional_amount='=solvefor([payer_swaption].risk.IRDeltaParallel,bp)', name='hedge1')` | `port.resolve()`, `port.calc(IRDelta(aggregation_level='Type'))`, `port_delta['payer_swaption'] - port_delta['hedge1']` | |
| `01_rates\20_fix_float_legs_price` | `IRFixedLeg('Buy', fixed_rate=0.05, ...)`, `IRFloatLeg('Sell', floating_rate_spread=40/1e4, ...)`, `IRSwap(..., principal_exchange='None')` | `.price()`; fixed+float == swap | `PricingContext(csa_term='USD-SOFR')` |
| `01_rates\21_asset_swap_definition` | `IRAssetSwapFxdFlt()`, `IRAssetSwapFxdFlt(identifier='GB5Y', traded_clean_price=99)` | `.resolve()`, `.as_dict()`, `.price()` | |
| `01_rates\22_cap_floor` | `IRCapFloor(cap_floor="Cap", ..., strike=0.015)`, `IRCap(..., cap_rate=0.015)`, `IRFloor(..., floor_rate=0.015)` | `.resolve(in_place=False).to_dict()`, `.price()` | |
| `01_rates\23_solve_vanna_&_volga` | as 19 + `IRCapFloor(cap_floor="Cap", ...)` | `port.calc(IRVanna).to_frame()`, `calc(IRVolga)`, `IRVanna(aggregation_level=AggregationLevel.Type)` | |
| `tutorials\Instruments` | `IRSwaption(PayReceive.Receive, '5y', Currency.USD, expiration_date='13m', strike='atm+40', notional_amount=1e8)` | `.as_dict()`, `.resolve()`, `.as_dict()` | |

Measures used across these notebooks: `Price` (via `.price()`), `DollarPrice`, `IRDelta` (default, `aggregation_level='Type'`
/ `AggregationLevel.Type`, `currency='local'`), `IRDeltaParallel` (inside a solve string), `IRXccyDelta`,
`IRXccyDeltaParallel`, `IRAnnualImpliedVol`, `IRVanna`, `IRVolga`, `Cashflows`, `MarketData` (via `.market()`),
`ResolvedInstrumentValues` (via `.resolve()`). pricebt today has `IRXccyDelta`, `IRAnnualImpliedVol`, `Cashflows`,
`ResolvedInstrumentValues` but **not** `IRVanna`, `IRVolga`, `IRXccyDeltaParallel`, `MarketData`
(`gs154:target/measures.py:469, 475, 505`; `gs154:risk/measures.py:83`).

### 4.2 Swaption semantics (quoted from `nb:..\01_rates\09_swaption_trade_construction.ipynb` unless noted)

- **Defaults.** c2: "you don't need to specify any parameters to get a valid trade. All properties have defaults".
  `pay_or_receive` c4: "can be a string of 'pay', 'receive', 'straddle' (an option strategy where you enter into both a payer
  and receiver) or the PayReceive enum; relates to whether you expect to pay/receive fixed for the underlying. **default is
  'straddle'**". `expiration_date` c5: "may be a tenor relative to the active PricingContext.pricing_date or a datetime.date,
  **default is '10y'**". (All GS-server defaults. Note the pricebt toy fixture defaults `pay_or_receive: Pay`,
  `pb:tests/assets/toy_usd_swaption.yaml:11` — fine for a test, but a real config mirroring gs would default to Straddle or
  require the field.)
- **Strike grammar.** c6: "strike is the rate at which the option can be exercised. It also represents the interest rate on
  the fixed leg of the swap if the swaption expires ITM. Defaults to Par Rate (ATM). Can be expressed as 'ATM', 'ATM+25' for
  25bp above par, a-100 for 100bp below par, 0.01 for 1%, you can also solve for a specific delta or pv": examples `'ATM'`,
  `'ATM+50'`, `'a-100'`, `0.02`, `'10000/pv'` (with `notional_amount=10000`), `'25d'`. The class docstring
  (`docs:_build\html\classes\gs_quant.instrument.IRSwaption.html`): "Strike as value, percent or at-the-money e.g. 62.5, 95%,
  ATM-25, ATMF, 10/vol, 100k/pv, p=10000, p=10000USD, $200K/BP, or multiple strikes 65.4/-45.8". Also `'atmf'`
  (`18_solve_present_value` c3), `'atm+40'` (Instruments tutorial c6), `'=solvefor(25e3,bp)'` (backtest 040305, R05).
- **Effective date / midcurve.** c7: "effective_date is the start date of the underlying swap and may be a tenor relative to
  the expiration_date or a datetime.date. Default is spot dates from expiration. ... GBP spot is T+0, so effective_date =
  expiration_date; USD spot is T+2 days and the effective_date is 2b after expiration_date". Midcurve
  (`11_midcurve_swaption_price.ipynb` c2): "expiration_date='6m' - option expires in 6m; effective_date='2y' - swap starting
  2y after expiry (2.5y after trade); termination_date='1y' - swap tenor is 1y (swap matures 3.5y post trade)". c3:
  "expiration_date can also be specified as IMM date" → `'IMM1'`.
- **Strike = forward swap rate.** c8: "An IRSwaption's strike will resolve to an IRSwap's fixed_rate if the swaps' parameters
  match and the swaption's effective_date is equivalent to the swap's effective_date" (`IRSwap(effective_date='10y')` vs
  `IRSwaption(expiration_date='10y', effective_date='0b')`).
- **Settlement.** c9: "'Phys.CLEARED' (enter into cleared swap), 'Cash.PYU' (PYU - Par Yield Unadjusted, cash payment
  calculated with PYU), 'Physical' (enter into a uncleared swap), 'Cash.CollatCash' (collateralized, cash settled at
  expiry) or the SwapSettlement enum".
- **Premium.** c10: "premium is the amount to be exchanged for the option contract. **A positive premium will have a negative
  impact on the PV.** premium is a default is 0." c11: "premium_payment_date ... can be a datetime.date or a tenor. defaulted
  to spot dates from the PricingContext.pricing_date". c12: "in some markets, the convention is for premium to be exchanged
  at expiration ... changing the premium_payment_date to the swaption's expiration_date".
- **Straddle.** `10_straddle_price.ipynb` c2-c3: "A swaption straddle represents the purchase of a payer swaption and
  receiver swaption with the same strike and expiration_date. The price of a straddle is sum of the price of the payer and
  receiver swaptions."
- **Buy/Sell sign.** No notebook states it in words; the gs contract is `scale_in_place` (§2.1): a negative scale flips
  `buy_sell`, so `Price(Sell) = −Price(Buy)`. `18_solve_present_value` builds a zero-cost 1x2 (Buy 10mm, Sell 20mm) on that
  basis. pricebt DESIGN §13.3 already requires the config's `resolve` to fold `buy_sell × sign(notional_amount)` into one
  signed notional; the toy does this (`pb:tests/toylib/swaption.py:35-65`).
- **Post-expiry.** `01_scenarios_and_contexts\examples\01_rollfwd_shock\03_...lifecycling...ipynb` c5: "the OTM swaption
  prices at 0 post expiry whereas the ITM swaption prices at the value of the swap" (physical settlement).
- **Backtest consequence of premium/fee.** The gs engine books entry cash = −PV(create) and exit cash = +PV(final) and never
  books a cashflow that leaves PV between marks (DESIGN §11 "kept on purpose", §14 coupon cash). A non-zero `premium` or
  `fee` paid after the entry date therefore leaves PV without reaching cash. `premium=0` (the gs default) is the only
  setting consistent with the engine's own entry cash. The same gap hits **bonds** harder: every coupon leaves a dirty PV
  unbooked, so a Bond config must use a total-return `npv` (or clean price + booked accrual) — DESIGN §14 already names
  "a total-return npv" as the config-only workaround.

### 4.3 Swap construction semantics (`01_rates\02_swap_trade_construction.ipynb`)

c5 "pay_or_receive ... relates to paying or receiving the fixed leg. defaults to a receiver swap"; c6 "termination_date ... may
be a tenor relative to effective_date or a datetime.date. defaults to 10y"; c7 "notional currency ... defaults to USD"; c8 "the
effective date ... may be a tenor relative to the active PricingContext.pricing_date or a datetime.date, default is pricing
date"; c9 "fixed_rate ... Defaults to Par Rate (ATM). Can be expressed as 'ATM', 'ATM+25' for 25bp above par, a-100 for
100bp below par, 0.01 for 1%, can also be solved for a PV" (`'10000/pv'`); c10 "floating_rate_for_the_initial_calculation_period
sets the first fixing ... a float in absolute terms so 0.0075 is 75bp"; c11 "floating rate option ... 'OIS' will give the
default overnight index for the notional ccy" (`'USD-ISDA-SWAP RATE'`, `'USD-LIBOR-BBA'`, `'EUR-EONIA-OIS-COMPOUND'`,
`'OIS'`); c12 "floating_rate_designated_maturity is the index term. defaults to the frequency of the floating leg"; c13
"floating_rate_spread ... defaults to 0"; c14 "floating_rate_frequency ... will drive the floating_rate_designated_maturity
if that has not been independently set"; c15-16 day count / BDC as enum or string (`'30/360'`, `'ACT/360'`, `'Modified
Following'`); c17 "fee is an amount paid. **A positive fee will have a negative impact on the PV.** Defaults to 0"; c18
"Default fee currency is notional currency ... Default fee date is spot dates from the PricingContext.pricing_date"; c19
clearing house `LCH`/`EUREX`/`'CME'`; c20 "name ... has no economic effect but is useful when extracting results from a
portfolio object"; c22-23 `as_dict()` → `IRSwap.from_dict(swap_dict)` round trip.

**Cashflows result columns** (actual output, `01_rates\17_calc_xccy_swap_cashflows.ipynb` c4/c6), in order:
`currency, payment_date, set_date, accrual_start_date, accrual_end_date, payment_amount, notional, payment_type
('FIX'|'Flt'), floating_rate_option ('NA' for fixed), floating_rate_designated_maturity ('NA' for fixed),
day_count_fraction, spread (None for fixed), rate, discount_factor`. Semantics
(`01_scenarios_and_contexts\examples\01_rollfwd_shock\03_...ipynb` c3): "a dataframe of the past and implied future
cashflows ... the discount factor is 0 for paid cashflows". gs's own backtests read `payment_type == 'Flt'`,
`accrual_start_date`, `rate` (`gs217:backtests/backtest_objects.py:986-989`, OisFixingCashAccrualModel — a pricebt stub).
pricebt config functions return floats or `dict[str, float]` only (DESIGN §4.4), so a table-valued `Cashflows` has **no
config representation today** (needs a `returns: table` kind; belongs to the risk-measure note).

### 4.4 Solve grammar (all GS-server)

| form | where | meaning |
|---|---|---|
| `'10000/pv'`, `'100k/pv'` | fixed_rate / strike | solve the rate so the trade PV equals the amount |
| `'25d'`, `'10/vol'`, `'p=10000'`, `'$200K/BP'` | strike | solve for delta / vol / premium / dv01 (docstring) |
| `'=solvefor(25e3,bp)'` | strike (040305) | solve so a measure equals a literal |
| `'=solvefor([30y_buy].risk.Price,pv)'` | strike | **intra-portfolio**: solve so this trade's PV equals another member's Price (by `name`) — `18_solve_present_value` c2: "This example takes advantage of intra-portfolio formulae" |
| `'=solvefor([payer_swaption].risk.IRDeltaParallel,bp)'` | notional_amount | size a hedge to match another member's parallel delta |

pricebt passes all of these through untouched (DEV-I6; probe §2.5). A config `resolve` can implement the
single-instrument forms. The **intra-portfolio forms are structurally out of reach**: an asset's `resolve` sees only its own
`kwargs` (DESIGN §4.3), never another portfolio member's PV. They would need a portfolio-level resolve pass in
`Portfolio.resolve`; recommend the config raise `NotSupportedError`-style `ValueError` on a leading `'='` until asked.

### 4.5 Client-side vs GS-server, and the pricebt mapping

| feature | gs where | pricebt |
|---|---|---|
| construction, positional order, defaults `None`/`0.0` | client (dataclass) | generated classes (ported) |
| enum coercion, camelCase kwargs | client (`base.py:95-116, 258-330`) | ported; camelCase *get/set* gap (§2.5) |
| attribute set/get of fields | client | **set gap** (§2.5) |
| `as_dict`, `to_dict`, `from_dict`, `properties`, `default_instance` | client | `as_dict`/`to_dict` ported; rest excepted |
| `clone`, `scale`, `flip` | client (field edits) | ported (`quantity_`, DEV-I1) |
| `Portfolio` containers, `to_frame`, name lookup | client | ported (portfolio note) |
| context stacking, futures, `HistoricalPricingContext.date_range` | client | ported (`markets/__init__.py`) |
| `MarketDataCoordinate.from_string` | **client** — pure parser `GsDataApi._coordinate_from_str` (`gs217:api/gs/data.py:1208-1226`) | absent (portable) |
| `OverlayMarket` item get/set, `market_data_dict` | client dict ops (`gs217:markets/markets.py:242-308`) | absent (portable as data) |
| resolution: market defaults, tenors → dates, `'ATM±x'`, `IMM1`, strike/fixed-rate solving, premium/fee dates | **server** | config `resolve` (pins at trade date) |
| Price, DollarPrice, IRDelta*, IRVega*, Vanna/Volga, implied vols, Cashflows | **server** | config `functions` / `portfolio_functions` + `risk_measures` |
| `market()` (MarketData measure) | **server** | none; §5.6 R-C5 |
| scenarios (RollFwd, CurveScenario, shocks, overlays) | **server** | none; §5.6 |
| `from_quick_entry`, `from_asset_ids` | **server** (GsParserApi / GsAssetApi) | excepted |

---

## 5. PricingContext, HistoricalPricingContext, markets and scenarios

### 5.1 gs PricingContext (2.1.17 behaviour; 1.5.4 signature identical, R04 §6)

- Constructor docstring (`gs217:markets/core.py:106-129`), quoted: "pricing_date: the date for pricing calculations. Default is
  today; market_data_location: the location for sourcing market data ('NYC', 'LDN' or 'HKG' (defaults to LDN); ... csa_term:
  the csa under which the calculations are made. Default is local ccy ois index; ... market: a Market object; ...
  market_behaviour: the behaviour to build the curve for pricing ('ContraintsBased' or 'Calibrated' (defaults to
  ContraintsBased))".
- Tutorial (`nb:01_scenarios_and_contexts\tutorials\Pricing_Context.ipynb` c3): "Pricing date is used to compute the
  expiration date and discounting rules for a derivative instrument ... `pricing_date` is different from `market_data_as_of`
  which is the date for sourcing market data and is defaulted to 1 business day before `pricing_date`."
- Validation (`gs217:markets/core.py:157-191`): `market` and `market_data_location` with different locations → `ValueError`;
  no `market` and `pricing_date > today + 5 days` → `ValueError('The PricingContext does not support a pricing_date in the
  future. Please use the RollFwd Scenario ...')`; a `CloseMarket`/`OverlayMarket`/`RelativeMarket` dated after today →
  `ValueError`. With `market` and no location, the location comes from `market.location`.
- `market` property (`:518-527`): `self.__market` if given, else `CloseMarket(date=close_market_date(location, pricing_date,
  CloseMarket.roll_hr_and_min), location=market_data_location)`. `close_market_date` (`gs217:markets/markets.py:60-89`)
  returns the previous business day while "now" in the location's timezone is before `pricing_date + 24:00` — i.e. pricing
  today uses yesterday's close; pricing a past date uses that date's close.
- `market_data_location` (`:529-535`): own value, else inherited from the **active** context, else `PricingLocation.LDN`.
- `pricing_date` (`:573-579`): own value, else inherited, else `business_day_offset(today(location), 0, roll='preceding')`
  (a weekend gives Friday; pricebt gives `date.today()`, `pb:src/pricebt/markets/__init__.py:56-62`).
- Scenario attachment (`:464-472`): `Scenario.path` (the stack of entered scenarios, innermost first) → `None`, the single
  scenario, or `CompositeScenario(scenarios=tuple(reversed(path)))` (outermost first). The `RiskKey` is `(provider,
  pricing_date, market, parameters(csa_term, market_behaviour, use_historical_diddles_only), scenario, risk_measure)`
  (`:452-462`).
- `PricingContext.current = X` is legal outside a nested context (`gs217:context_base.py:63-77`; raises
  `MqValueError('Cannot set current while in a nested context ...')` otherwise).
- `HistoricalPricingContext.calc` (`gs217:markets/historical.py:113-126`): one `RiskKey(provider, date, CloseMarket(location,
  date, check=True), parameters, scenario, measure)` per date — **scenarios apply per date**.
- `BackToTheFuturePricingContext` (`gs217:markets/historical.py:132-225`): dates after `pricing_date` are priced as
  `RollFwd(date=d, realise_fwd=roll_to_fwds)` on the base market; past dates as HPC.
- gs backtests use only `market_data_location` from all of this (`gs217:backtests/generic_engine.py:117-125, 150-186`); no
  scenario or market object is used by any engine.

### 5.2 Market objects (`gs217:markets/markets.py`)

| class | line | what it is | data it carries |
|---|---|---|---|
| `LocationOnlyMarket` | 125 | location only; used for historical risk keys | location |
| `CloseMarket(date=None, location=None, check=True)` | 140 | "Market Object which captures market data based on market_location and close_market_date"; hash/eq on `(date, location)` | date, location |
| `TimestampedMarket(timestamp, location=None, base_date=None)` | 191 | intraday snapshot at a timestamp | timestamp |
| `LiveMarket(location=None)` | 220 | "captures market data based on location and time at runtime" | none |
| `OverlayMarket(market_data=None, base_market=None, binary_mkt_data=None)` | 242 | "overlays a base Market object ... with a MarketDataMap (a map of market coordinate to float)"; `__getitem__/__setitem__` by coordinate or coordinate string; `'redacted'` values dropped and not overridable; default base = `CloseMarket()` | `{MarketDataCoordinate: float}` |
| `RefMarket(market_ref)` | 311 | "a Reference to a Market" (server id) | ref string |
| `RelativeMarket(from_market, to_market)` | 329 | "captures the change between two Market Objects" | two markets |

`MarketDataCoordinate` fields: `mkt_type, mkt_asset, mkt_class, mkt_point (tuple), mkt_quoting_style`; `repr` =
`TYPE_ASSET_CLASS_p1,p2.QUOTING` (`:92-113`). `RelativeMarket`'s only consumer is the relative measure family:
`PnlExplain(to_market)` builds `current.clone(market=RelativeMarket(from_market=current.market, to_market=...))`
(`gs217:risk/measures.py:26-52`), plus `PnlExplainClose()` (to `CloseMarket()`), `PnlExplainLive`, `PnlPredictLive`; its
result is an unsorted `(mkt_type, mkt_asset, value)` frame (`gs217:risk/result_handlers.py:201-202`). That is **the gs P&L
decomposition hook**; the measure itself belongs to the risk-measure note.

### 5.3 Scenarios (`gs_quant.risk`; dataclasses in `gs217:target/common.py`; `Scenario(Base, ContextBase)` at `gs217:base.py:547`)

Every scenario is a context manager (`with scenario:` pushes onto `Scenario.path`). 1.5.4 exports
(`gs154:risk/__init__.py:19-23`): `MarketDataShockBasedScenario, LiborFallbackScenario, CarryScenario, CompositeScenario,
CurveScenario, IndexCurveShift, MarketDataPattern, MarketDataScenario, MarketDataShock, MarketDataShockType, RollFwd,
CurveOverlay, MultiScenario`.

| class (gs217 target/common.py line) | fields (gs order) | documented meaning (quoted) |
|---|---|---|
| `RollFwd` (6101) | `date` (date\|str), `realise_fwd`=**True**, `holiday_calendar` (PricingLocation), `name` | Scenarios.ipynb c13: "A predefined scenario used to evolve market data and trades over a period of time; date - Absolute or Relative Date to shift markets to; realise_fwd - Roll along the forward curve or roll in spot space; holiday_calendar - Calendar to use if relative date is specified". `04_yield_curves_with_rollfwd` c3: "This shift keeps fwd rates constant. So 5.5y rate today will be 5y rate under the scenario of pricing 6m in the future." `02_basic_use_of_rollfwd_scenario` c2: "If you don't resolve the trade, the resolution of the trade parameters will be done with reference to the active pricing context. Under the RollFwd scenario this means that if you don't resolve the trade will be a different trade". |
| `CurveScenario` (6208) | `market_data_pattern` (MarketDataPattern), `parallel_shift`, `curve_shift`, `pivot_point`, `tenor_start`, `tenor_end` (floats, years), `shock_type` (MarketDataShockType), `name` | Scenarios.ipynb c10: "modify the shape of the curve ... parallel_shift - A constant (X bps) which shifts all points by the same amount; curve_shift - ... the net rate change (X bps) between tenorStart and tenorEnd; pivot_point - The tenor in years ... at which there is zero rate change ... If not specified, pivot_point is the midpoint of tenor_start and tenor_end". |
| `IndexCurveShift` (6575) | `market_data_pattern`, `freeze_pattern`, `annualised_parallel_shift`, `annualised_slope_shift`, `cutoff`, `floor`, `tenor`, `rate_option`, `bucket_shift`, `bucket_start`, `bucket_end`, `name` | Scenarios.ipynb c16: "modify the shape of the index curve ... parallel shift and slope shift ... custom bucket". |
| `MarketDataShockBasedScenario` (6843; ctor override `gs217:risk/scenarios.py:32-34`: `(shocks: Mapping[MarketDataPattern, MarketDataShock], name=None)`) | `shocks: tuple[MarketDataPatternAndShock]` | "Allows the user to create a bespoke market data shock" (c4); e.g. `{MarketDataPattern('IR Vol'): MarketDataShock(MarketDataShockType.Absolute, 1e-4)}` |
| `MarketDataPattern` (5927) | `mkt_type, mkt_asset, mkt_class, mkt_point, mkt_quoting_style, is_active, is_investment_grade, currency, country_code, gics_*, name` | coordinate filter |
| `MarketDataShock` (5947) | `shock_type`, `value`, `precision`, `cap`, `floor`, `coordinate_cap`, `coordinate_floor`, `name` | |
| `MarketDataShockType` (4120) | Absolute, Proportional, Invalid, Override, StdDev, AutoDefault, CSWFFR, StdVolFactor, StdVolFactorProportional | |
| `CurveOverlay` (5090) | discount factors + dates, curve_type, rate_option, tenor, csa_term, denominated, ... | `07_curve_overlay` c4: "overlay discount curve or index curve ... customized discount factors to overwrite existing curves"; needs `PricingContext(market_behaviour=MarketBehaviour.Calibrated)` (c5) |
| `CarryScenario` (5663) | `date`, `time_shift`, `roll_to_fwds`=True, `holiday_calendar`, `name` | |
| `MultiScenario` (6996) | `scenarios: tuple[Scenario]` | `08_multi_scenario` c4: "Multi Scenario only supports a list of the same type of scenario"; `Instrument.calc` returns a `MultipleScenarioFuture` (`gs154:instrument/core.py:168-178`) |
| `CompositeScenario` (6987) | `scenarios` | built by the context from nested scenarios |

Doc defect: `02_market_shock\010202_rate_curve_shock.ipynb` c2 calls `CurveScenario(annualised_parallel_shift=5,
annualised_slope_shift=1, pivot_point=5, cutoff=50)` — those are `IndexCurveShift` fields, so it raises `TypeError` in gs.

### 5.4 pricebt today (`pb:src/pricebt/markets/__init__.py`)

- Module docstring (`:3-4`): "Only `pricing_date` and `csa_term` actually do anything here; every other constructor
  parameter is accepted, for signature parity, and ignored." `__init__` stores only `_pricing_date`, `_csa_term`,
  `_is_async` (`:50-54`). No warning for ignored arguments.
- `current` is a getter-only metaclass property (`:24-27`); no setter.
- The 1.5.4 parity exceptions list `market`, `market_data_location`, `market_behaviour`, ... as missing properties and
  `calc`, `clone`, `default_value` as missing methods (`gs_api_exceptions.yaml:228-262`).
- `run_backtest(market_data_location=...)`: "accepted for signature parity; not used"
  (`pb:src/pricebt/backtests/generic_engine.py:286, 307`).
- No `CloseMarket`/`OverlayMarket`/`RelativeMarket`/`MarketDataCoordinate`, no scenario class anywhere in `src/pricebt`.
- The pricing seam: `engine_calc` reads `ctx.pricing_date`, `ctx.csa_term`, `ctx.date_range`
  (`pb:src/pricebt/assets/pricing.py:588-615`); the market cache key is `(asset.market_key, d, csa)` (`:123-131`); injected
  names are `pricebt_date, pricebt_timestamp, pricebt_datetime, pricebt_csa` (`:104-112`).

### 5.5 Portability classification

| gs feature | what the config would define | classification |
|---|---|---|
| `pricing_date` | — | ported |
| `csa_term` | opaque string into `market.expr` (DESIGN §4.3) | ported |
| `market_data_location` | opaque string (which close snapshot / EOD time) into `market.expr`, part of the market cache key | **portable, trivial** |
| `market=CloseMarket(date=d, location)` | nothing new: the market expression is evaluated with `pricebt_date = d`, resolve/functions keep `pricebt_date = pricing_date` | **portable, generic** |
| `market=OverlayMarket({coord: value}, base)` | a config transform `overlay: 'apply(market, overrides)'`; `overrides = {repr(coord): value}`; `MarketDataCoordinate`(+`from_string`) ported as pure data | portable, generic (phase 2) |
| `Instrument.market()` | an optional config function returning `{coordinate string: value}` (the same label vocabulary as bucketed portfolio functions, DESIGN §4.2 `labels`) | portable only if a config opts in; else `NotSupportedError` |
| `market=RelativeMarket(from, to)` / `PnlExplain(to_market)` | a config portfolio function over two markets, returning `(mkt_type, mkt_asset) → value` buckets | portable, generic; **defer to the risk note** (P&L decomposition) |
| `CurveScenario`, `IndexCurveShift`, `MarketDataShockBasedScenario`, `CurveOverlay`, `CarryScenario` | a config market transform keyed by scenario class name, receiving the scenario as a plain dict | **portable, generic** |
| `RollFwd` | a config market transform (`realise_fwd` = keep forwards vs keep spot curve) **plus** a pricing-layer date roll (functions see `pricebt_date` = the rolled date so expiries/paid cashflows lifecycle) | portable, generic (the date roll is scenario-type logic, not asset logic) |
| `BackToTheFuturePricingContext` | = HPC with `RollFwd` for future dates | portable after RollFwd (defer) |
| `MultiScenario` | needs `MultipleScenarioFuture`/result shapes | stub (`NotSupportedError`) until wanted |
| `LiveMarket`, `TimestampedMarket`, `RefMarket` | GS live/intraday/reference data | stub |
| `market_behaviour`, `is_batch`, `use_cache`, `visible_to_gs`, `request_priority`, `timeout`, `use_server_cache`, `provider`, `set_parameters_only`, `use_historical_diddles_only` | GS request plumbing | accept and ignore (as today) |
| `is_async` | futures return convention | ported |
| future-date guard (`pricing_date > today+5`) | — | not needed (the config returns `None` for dates it has no data for) |

### 5.6 Recommended design (concrete)

**R-C1 — cheap parity, do now (no config change).**
- `PricingContext`: store `market` and `market_data_location`, expose both as properties with gs inheritance (own value,
  else enclosing context, else `None` — not `LDN`, since pricebt has no location semantics); add the `current` setter with
  gs's nested-context error. Removes two "missing properties" rows from `gs_api_exceptions.yaml:228-262`.
- Until R-C2/R-C3 exist, **raise `NotSupportedError`** when a non-`None` `market` reaches `engine_calc`/`engine_resolve`,
  instead of silently pricing the base market. Loud beats wrong.
- `Instrument.__setattr__`: for a name that snake-cases to a gs field of the class (from `GS_FIELDS`), coerce with the
  enum map and write into `_kwargs` (drop on `None`); on a resolved instrument raise `ValueError("clone a resolved
  instrument only with name/quantity_")` (the existing `clone` rule, `pb:src/pricebt/instrument/__init__.py:227-229`);
  `asset_class`/`type_` → `ValueError(f'{key} cannot be set')` (gs). Leave `name`, `quantity_`, `position_meta`,
  resolution attributes as plain attributes. `__getattr__` step 3 should also try `_to_snake(field)` (camelCase get).
  One test: set `termination_date`, assert `kwargs`, `as_dict()`, identity and a toy price all move.

**R-C2 — a market spec in place of the bare csa (the one pricing-layer change).**
- Replace `csa` in every `PricingService` cache key and signature with an opaque, hashable
  `spec = (csa, location, market_date, scenarios)`, built once per call in `engine_calc`/`engine_resolve` from
  `PricingContext.current` and `Scenario.path`. `market_date` = `ctx.market.date` when `ctx.market` is a `CloseMarket`
  with an explicit date, else `None` (= the pricing date). This needs `CloseMarket` (and `LocationOnlyMarket`) as plain
  data objects in `markets/__init__.py` — pull those two forward from R-C4; without them `market_date` is always `None`.
- `_base_injected` adds `pricebt_location` (str or `None`) to every evaluation; `market.expr` gets `pricebt_date =
  market_date or d`. `pricebt_csa` is unchanged, so every existing config keeps working byte for byte.
- The resolution cache key (`asset, frozen kwargs, d, spec`) then naturally reproduces the gs warning that an unresolved
  trade priced under a scenario is resolved under it (RollFwd notebook c2).
- Asset-agnostic: `spec` is opaque to `assets/pricing.py`; no field names are read there.

**R-C3 — scenarios as config transforms.**
- Port the scenario dataclasses **with gs 1.5.4 field order** into `pricebt/risk/__init__.py` (gs path `gs_quant.risk`; the
  file is exempt from the asset-agnostic scan, `pb:tests/guards/scan.py:151-158`): `Scenario` base with a context stack
  (`path`), `RollFwd`, `CurveScenario`, `IndexCurveShift`, `MarketDataShockBasedScenario` (with gs's `shocks` mapping
  ctor), `MarketDataPattern`, `MarketDataShock`, `MarketDataShockType`, `CurveOverlay`, `CarryScenario`, `MultiScenario`,
  `CompositeScenario`. Add their names to `gs_api_snapshot.py` so the parity test checks field order. No new files (no
  skeleton/DAG change); `risk` is below `assets` in the DAG, so `pricing.py` may read `Scenario.path`.
- New optional asset-config section (schema change, `ASSET_CONFIG_GUIDE.md`):

  ```yaml
  scenarios:                              # gs scenario class name -> market transform expression
    CurveScenario: 'shift_curve(market, scenario)'
    MarketDataShockBasedScenario: 'apply_shocks(market, scenario)'
    RollFwd: 'roll_market(market, scenario, pricebt_date)'   # pricebt_date = the rolled date here
  ```

  `scenario` is injected as a plain dict: the dataclass fields (enums as `.value`, nested patterns/shocks as dicts) plus
  `scenario_type`. Transforms apply outermost → innermost (gs `CompositeScenario(reversed(path))`, `gs217:markets/core.py:469-472`),
  each result feeding the next, and the transformed market is cached under the full `spec`.
- An active scenario with **no** mapping in the asset → `NotSupportedError(f"asset {name} has no scenarios: entry for
  {scenario_type}")`. Never price unshocked silently.
- `RollFwd` only: the service computes the target date from `scenario.date` (a date, or a tenor applied to the pricing date
  with `pricebt.datetime` rules and `holiday_calendar`), evaluates the transform with `pricebt_date = target`, and runs
  `functions` with `pricebt_date = target` so expiries and paid cashflows lifecycle. This is keyed on the scenario type,
  never on the asset class (MUST-5 holds).
- Under `HistoricalPricingContext`, the spec (with its scenarios) applies per date, as gs does.
- `MultiScenario` → `NotSupportedError` for now.

**R-C4 — markets as data (phase 2, only if a notebook needs it).** `CloseMarket`, `OverlayMarket`, `RelativeMarket`,
`LocationOnlyMarket`, `MarketDataCoordinate` (+ the pure `from_string` parser) in `markets/__init__.py` (their field names
contain no guarded token). `OverlayMarket` feeds an optional config key `overlay:`; `Instrument.market()` an optional config
function; `LiveMarket`/`TimestampedMarket`/`RefMarket` raise `NotSupportedError` when used.

**R-C5 — P&L decomposition.** `RelativeMarket` + `PnlExplain` are the gs hook; hand them to the risk-measure note. Together
with `RollFwd` (carry/roll-down) and `CurveScenario` (curve moves), R-C2/R-C3 give a config-defined explain without new
engine concepts.

### 5.7 Constraints to respect

- Asset-agnostic guard regex (`pb:tests/guards/scan.py:133-137`) covers `assets/**`, `markets/**`, `risk/results.py`,
  `risk/transform.py`: no scenario field names (`tenor_start`, `tenor`) may appear there. `RollFwd`, `date`,
  `realise_fwd`, `scenario_type` are safe.
- Import DAG (`pb:tests/guards/dag.py` `DAG_TIERS`): `risk` < `markets` < `instrument` < `assets` — scenario classes in `risk`
  and market objects in `markets` import nothing later.
- Token scan: messages say "GS server-side", never a vendor name (DESIGN §6.6).
- MUST-1: scenario semantics live only in config expressions; pricebt forwards plain dicts.

---

## 6. Recommendations (ordered)

1. **Fix the setattr gap** (R-C1 third bullet). It is a silent wrong number reachable from a gs basics notebook.
2. **Make ignored `market=` loud** (`NotSupportedError`) and add the `PricingContext.current` setter and the
   `market`/`market_data_location` properties.
3. **Add the 6 classes** with the §3.3 checklist in one commit (include the `test_common.py` and `test_skill_spec.py`
   edits and the re-snapshot). Optionally derive `_ENUM_CLASSES`/`__all__` from `GS_FIELDS` first so later additions
   need fewer hand edits, and correct decision 0.6's "data only" wording. Add one line to DEV-I1 ("also scales classes gs
   cannot scale: Bond, IRBondFuture, IRBondOption, IRCap, IRFloor, IRCapFloor").
4. **Bond config guidance** (ASSET_CONFIG_GUIDE): bonds have coupons between marks, so `npv` should be total-return (or
   the config should document the gap, DESIGN §14); `size_attribute: size`; `identifier`/`identifier_type` are opaque to
   pricebt; `IRBondFuture` should pin `traded_price`/entry price in `resolve` (margining, DESIGN §14).
5. **Swaption config guidance**: gs default `pay_or_receive` is Straddle (price = payer + receiver) and expiry `'10y'`;
   fold `buy_sell` into the sign; treat `premium`/`fee` ≠ 0 as unsupported in backtests (or book it deliberately);
   implement `'ATM'`, `'ATM±x'`/`'A±x'`, `'atmf'`, decimals in `resolve`; reject a leading `'='` and the `/pv`, `d`, `/vol`,
   `p=`, `/BP` solve forms unless implemented; settlement values per §4.2 (post-expiry physical → swap value).
6. **Scenarios** via R-C2 + R-C3 when P&L decomposition (carry/roll-down, curve shocks) is scheduled; `RelativeMarket` /
   `PnlExplain` with the risk-measure work; OverlayMarket/`market()` only on demand.
7. Missing notebook measures (`IRVanna`, `IRVolga`, `IRXccyDeltaParallel`, `MarketData`) and a table-valued `Cashflows`
   (columns in §4.3) are for the risk-measure note.

## 7. Open questions for the design owner

1. Should `Instrument.__setattr__` on a *resolved* instrument raise (consistent with `clone`) or re-resolve? gs just
   mutates the field.
2. `PricingContext.market_data_location` default: `None` (pricebt has no location concept) or gs's `LDN`? The parity test
   only compares names, not values.
3. `to_dict()` returns snake_case keys and native dates, gs returns camelCase keys and ISO strings. Keep (engine-only use)
   and list as a DEV row, or match gs?
4. Is `RollFwd`'s date roll (functions see the rolled `pricebt_date`) acceptable as the one scenario-type rule in the
   pricing layer, or should the roll date also be a config expression?
