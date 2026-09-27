# Terms, resolved terms and the conventions vocabulary

Sources of truth: `src/pricebt/contracts/schemas/swap.yaml`, `bond.yaml`, `generic.yaml` (names, units, signs), `src/pricebt/contracts/schema.py` (`AssetSchema.normalise_terms`,
`check_resolved`, `check_conventions`), `src/pricebt/contracts/spec.py` (`TradeTemplate.build`). Everything below was read there and then executed.

## 1. What the factory receives

`TradeTemplate(name, spec, terms)` normalises the terms of one trade against the schema once; `TradeTemplate.build(pricer, ts)` calls
`spec.factory(pricer, ts, terms=<normalised terms + "direction">, conventions=<a deep copy of the spec's conventions>)`. Executed spy (`side: sell`, `maturity: 10y`, `notional: 10000000`):

```
factory received: pricer AcmePricer | ts 2024-06-12 17:00:00-04:00
  terms: {'side': 'receive', 'effective': 'spot', 'maturity': '10y', 'notional': 10000000, 'fixed_rate': 'par', 'direction': -1}
  conventions keys: ['business_day_convention', 'calendar', 'compounding', 'day_count', 'end_of_month', 'fixing_lag_days', 'frequency', 'payment_lag_days', 'spot_lag_days', 'stub', 'time_accrual']
```

| swap term (`swap.yaml`) | type | what arrives | note |
|---|---|---|---|
| `side` | enum `pay` / `receive` (aliases `buy` -> pay, `sell` -> receive) | the canonical word | `direction` +1 = payer of fixed = long the rate, -1 = receiver |
| `effective` | date or tenor or `spot` (default `spot`) | UNTOUCHED: `'spot'`, a tenor string, a `dt.date` / `Timestamp`, or an ISO string as written | the library resolves it |
| `maturity` | date or tenor (required) | UNTOUCHED (`'10Y'` from YAML, `'10y'` from the gs facade, `'2034-06-14'`, a `dt.date`) | accept both cases of a tenor; a tenor is from the EFFECTIVE date |
| `notional` | positive number | as given (an `int` stays an `int`) | unsigned: direction is `side`, never the sign |
| `fixed_rate` | number (PERCENT: 4.0 = 4%) or `par` (default `par`) | the number, or the lower-case token | a fraction (0.04) is NOT converted: the gs facade alone converts gs decimals |
| `extras` | mapping, only present when given | the mapping | per-trade extras for the adapter; an unknown one is YOUR error to raise |
| `direction` | +1 / -1 | added by pricebt from `side` | never returned by the factory (a returned one is ignored: pricebt's wins) |

| bond term (`bond.yaml`) | type | note |
|---|---|---|
| `side` | `buy` / `sell` (aliases `long`, `short`) | `direction` +1 long; a long bond's `dv01` is NEGATIVE |
| `security` | non-empty string | an id, or an alias the provider's reference data resolves (`CT10`, `O10`, ...) |
| `notional` | positive number | face amount |
| `extras` | mapping | the shipped bond kits take `{"repo": {"gc_rate": <percent or "pricer">, "specialness_bps": ..., "haircut": ...}}` |

Term errors are raised by pricebt before your factory runs, all `[CFG-TERMS] asset class 'swap': ...` followed by the list of terms (executed):

```
[CFG-TERMS] asset class 'swap': unknown terms ['tenor']. Terms: side(enum, required); effective(date_or_tenor); maturity(date_or_tenor, required); notional(number, required); fixed_rate(number_or_token); extras(mapping)
[CFG-TERMS] asset class 'swap': missing required term 'maturity'. Terms: ...
[CFG-TERMS] asset class 'swap': term 'side' must be one of ['pay', 'receive'] (aliases {'buy': 'pay', 'sell': 'receive'}), got 'long'. Terms: ...
[CFG-TERMS] asset class 'swap': term 'notional' must be positive (direction is `side`, never the sign of the size), got -10000000.0. Terms: ...
[CFG-TERMS] asset class 'swap': term 'maturity' must be a date, a valid ISO date string, a tenor like 10Y or one of [], got 'ten years'. Terms: ...
[CFG-TERMS] asset class 'swap': term 'notional' holds a reference, which is only allowed in an action's terms. Terms: ...
```

## 2. What the factory must resolve and return: `Built(obj, resolved_terms)`

Resolve at the FILL pricer (the pricer at the point the order is priced: the point itself for `fill_lag: 0`, the decision point for a lagged fill; `src/pricebt/engine/engine.py: _make_position`): a curve of
another day gives another par rate and other dates.

| return | for | rule |
|---|---|---|
| `effective` | swap | `dt.date` (concrete, business-day adjusted by YOUR conventions on the SNAPSHOT's holidays) |
| `maturity` | swap, bond | `dt.date` |
| `fixed_rate` | swap | float PERCENT: the number given, or the par rate resolved on this snapshot (+ `extras.par_spread_bp / 100` when `par`) |
| `notional` | both | echo, the same number (a positive size may not change) |
| `security`, `coupon` (PERCENT) | bond | the resolved id (an alias becomes the id), the reference-data coupon |
| any other key | | keep the SAME key set as the reference stack: see below |

pricebt then merges your dict over the given terms and validates it (`AssetSchema.check_resolved`), with these executed outcomes:

```
[CFG-TERMS-UNRESOLVED] term 'fixed_rate' came back unresolved ('par'): the library must return a concrete number (T4)
[CFG-TERMS-UNRESOLVED] term 'effective' came back unresolved ('spot'): the library must return a concrete date (T4)
[CFG-TERMS-UNRESOLVED] term 'maturity' came back unresolved ('5Y'): the library must return a concrete date (T4)
[CFG-TERMS] asset class 'swap': the factory changed term 'notional' from 10000000.0 to 1.0; only relative dates and tokens are resolved. Terms: ...
[CFG-FACTORY] factory of instrument 'ois' must return Built(obj, resolved_terms), got dict
```

* Return `dt.date`, not the library's strings: `check_resolved` ACCEPTS an ISO string as a resolved date silently (executed: `'2024-06-14'` came back as a `str` in `Built.terms`, which
  becomes the position's resolved terms and the `term_<name>` columns of the trade ledger). Downstream date comparisons, the L0 comparison and any `trade_duration` that reads a resolved date then see strings. Convert in the factory (`C.to_date`).
* Every stack must return the SAME resolved keys: the tie-out compares `term_<key>` of each opened position, and a key one stack lacks is `None` on its side. Executed: a Kit that returns one
  extra key `curve_id` gives `L0 resolved_terms input | 3 differ ... P000001 curve_id: None vs USD-SOFR; P000002 ...` and every later level is `input` (the shipped kits return exactly
  `effective, maturity, fixed_rate, notional` for swaps and `security, coupon, maturity, notional` for bonds).
* The factory must not mutate `terms` or `conventions` (both are copies, but the habit protects you) and must not keep the pricer: the instrument is PLAIN DATA (the library contract + the
  direction); it is priced later on whatever pricer the mark uses.

## 3. `extras`: adapter-owned, refuse what you do not know

Standard swap key: `par_spread_bp` (bp over par, only with `fixed_rate: par`; 25 bp is +0.25 percent). Executed messages of the shipped kits:

```
[CFG-EXTRAS] swap extras ['repo'] are not supported; allowed: ['par_spread_bp']
[CFG-EXTRAS] extras par_spread_bp applies only to fixed_rate 'par'
[CFG-EXTRAS] extras par_spread_bp must be a finite number of basis points, got 'x'
[CFG-EXTRAS] bond extras ['haircut'] are not supported; allowed: ['repo']
```

## 4. The conventions vocabulary and what each adapter does with it

`conventions:` (per instrument spec, written ONCE in the base config, shared by every stack, hashed into the manifest: `conventions_digest`, compared at L0) is validated against the schema by
`AssetSchema.check_conventions(raw, require_all=True)`. pricebt itself applies none of it. Refused there, with `[CFG-CONVENTION]` (executed): an unknown key
(`unknown convention keys ['spot_lag'] for asset class 'swap'; the vocabulary is [...] (spot_lag: did you mean 'spot_lag_days'?)`), a missing key
(`missing convention keys ['stub'] for asset class 'swap': there are no implicit defaults`), a wrong type (`convention 'spot_lag_days' must be a non-negative integer, got '2'`), a token outside
the list (`convention 'day_count' must be one of ['act360', 'act365f', 'actact_icma', 'actact_isda', 'thirty360'], got 'act365'`).

On top of that YOUR module decides, per key, which values it honours. A vocabulary token you cannot express is refused with the reason and the supported set:

```
[CFG-CONVENTION-UNSUPPORTED] convention day_count='thirty360' is in the vocabulary but this adapter does not support it: acmelib has only ACT/360 and ACT/365 bases; supported: ['act360', 'act365f']
```

Swap vocabulary and decisions (read from the code of each adapter; `-` = not applicable):

| key | vocabulary | reference stack | rateslib | QuantLib | acme (example) |
|---|---|---|---|---|---|
| `calendar` | name (in the snapshot) | any | any | any | any |
| `spot_lag_days` | int | any | any | any | any |
| `day_count` | act360, act365f, actact_icma, actact_isda, thirty360 | act360, act365f | act360, act365f | act360 | act360, act365f |
| `frequency` | monthly, quarterly, semiannual, annual | all four | all four | annual, semiannual | all four |
| `business_day_convention` | unadjusted, following, modified_following, preceding, modified_preceding | all but modified_preceding | unadjusted, following, modified_following, preceding | modified_following, following | unadjusted, following, modified_following, preceding |
| `payment_lag_days` | int | any | any | any | any |
| `compounding` | daily_compounded, daily_average | daily_compounded | daily_compounded | daily_compounded | daily_compounded |
| `stub` | short_front, long_front, short_back, long_back | short_front | short_front | short_front | short_front |
| `end_of_month` | bool | false | false | false | false |
| `fixing_lag_days` | int | 1 | 1 | 1 | 1 |
| `time_accrual` | lump, linear | lump | lump | lump | lump |

Bond vocabulary (reference stack = rateslib = QuantLib; every one a single supported value except `calendar`): `settlement_lag_days` {0}, `day_count` {actact_icma}, `frequency` {semiannual},
`business_day_convention` {unadjusted}, `payment_business_day_convention` {following}, `payment_lag_days` {0}, `stub` {short_front}, `end_of_month` {true}, `compounding` {semiannual},
`yield_convention` {treasury}, `ex_dividend_days` {0}, `financing_day_count` {act360}, `time_accrual` {lump}. Executed refusals: `settlement_lag_days=1` (`the mark is the value at the reference date; a later settlement date would need a forward value; supported: [0]`), `yield_convention='street'` (QuantLib: `no compounded-except-final-period-simple yield convention ...; supported: ['treasury']`).

### How to decide each key for `<LIB>` (do this per key, write the verdict down)

1. Read `<LIB>`'s documentation: what does it call this convention, which values exist, which is its default? Q: which of pricebt's tokens map to which of its codes?
2. Honour a value only after a PROBE: the same snapshot (one node table, one holiday set, one fixings history) priced by `<LIB>` and by the reference stack (`pricebt.testing.refstack`, the known
   answer) with that value set; the dates and the PV must agree to the noise floor. The acme tests do it for every supported non-default value
   (`test_every_supported_non_default_convention_reaches_acmelib_and_agrees_with_the_reference_stack`), and first assert that the variant CHANGES something (agreeing with the reference
   proves nothing if the value was ignored).
3. Refuse the rest with a reason a user can act on. A value you did not verify is refused too (`not implemented or not verified against the reference adapter`): supporting more than you
   verified is how a silent difference reaches a tie-out.
4. Record the decision in a conventions document modelled on `docs/design/11-quantlib-conventions.md`: headline table of worst disagreements, method and controls, one section per item
   (calendar and dates; an at-inception swap; a started swap with fixings; dv01; the ladder; bonds), the library's process-global state, the gaps that remain (calendar horizon, stub
   tenors, yield conventions), the vocabulary decision per key, and the evidence (tests and mutation checks).
5. A key you honour with a non-default value needs the reference stack to honour it too, or the tie-out compares different things: check `_SWAP_SUPPORTED` in `src/pricebt/testing/refstack.py`.

### The ready-made blocks

A complete block names EVERY key (no implicit default). The shipped ones: `USD_SOFR_OIS_CONVENTIONS` and `UST_CONVENTIONS` in `src/pricebt/contrib/quantlib/conventions.py` and
`contrib/rateslib/conventions.py` (`calendar: "nyc"`), the acme block (`calendar: "usd_fed"`), the suite base `configs/suite/_swap_base.yaml`. `calendar` is the NAME of a calendar in the SNAPSHOT
the provider serves: `usd_fed` for `SyntheticMarket` and `support.curves:CurveStore`, `cal` for `flat_swap_world("cal")`, whatever your provider names it. A block for another market
(other currency, index, day count) is a new dict with every key: copy the shipped one and change values; the schema's own vocabulary is `SchemaRegistry.default().get("swap").conventions`
(`SWAP_KEYS` in `src/pricebt/contrib/quantlib/conventions.py` and in the acme adapter is derived from it for documentation; rateslib and the reference stack define none).
