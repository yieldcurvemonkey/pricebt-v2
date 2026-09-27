# Schema cheat-sheet: every name pricebt expects, with unit, sign and return type

Generated from the RESOLVED shipped schemas (`SchemaRegistry.default().get("swap")` and `.get("bond")`: each `extends: generic`), so the inherited names (`value`, `pv`, `notional`) are included.
The YAML files (`src/pricebt/contracts/schemas/{generic,swap,bond}.yaml`) are the source of truth; the `doc:` text of each entry carries the definition. If a schema file changes, regenerate
(script at the end) and diff.

Column notes. **required**: strict mode fails the load if it is unbound. **binding**: `bind` = you write a binding; `none` = pricebt derives it from another name (a binding for it is
`[CFG-UNKNOWN-BINDING] ... takes no binding`). **synonyms**: names a "did you mean" hint may suggest; they are hints, not aliases (a synonym is never resolved implicitly).

## Swap

##### swap: terms

| term | type | required / default | unit | values, tokens, aliases |
|---|---|---|---|---|
| `side` | enum | required | - | values `pay`, `receive`; aliases `buy`->`pay`, `sell`->`receive` |
| `effective` | date_or_tenor | default `spot` | - | tokens `spot` |
| `maturity` | date_or_tenor | required | - | - |
| `notional` | number | required | currency | positive |
| `fixed_rate` | number_or_token | default `par` | percent | tokens `par` |
| `extras` | mapping | optional | - | - |

Direction: term `side`, sign `pay` = +1, `receive` = -1.

##### swap: methods, measures, layers

| name | kind | required | returns | unit | sign | synonyms | binding |
|---|---|---|---|---|---|---|---|
| `value` | method | yes | `Valuation \| float` | currency | holder: positive is an asset of the holder | - | bind |
| `pv` | measure | yes | `float` | currency | holder | - | none: derived from `value.pv` |
| `notional` | measure | no | `float` | currency | unsigned | face, size, principal | none: derived from `terms.notional` |
| `dv01` | measure | yes | `float` | currency per +1bp | holder: positive for the payer | pv01, dollar_delta, delta, bpv | bind |
| `gamma` | measure | yes | `float` | currency per bp^2 | holder | convexity, curvature, second_derivative | bind |
| `rate` | measure | yes | `float` | percent | - | par_rate, par, market_rate | bind |
| `delta_ladder` | measure | yes | `dict[str, float]` | currency per +1bp of the bucket's rate | holder: a payer has positive entries | ladder, bucketed_delta, delta_buckets, key_rate_delta | bind |

| layer | id@version | required | returns | unit | needs |
|---|---|---|---|---|---|
| `carry` | `swap.carry@1` | yes | `float` | currency, per unit, over the interval since the previous cadence point | prev_pricer |
| `roll` | `swap.roll@1` | yes | `float` | currency, per unit, over the interval | prev_pricer, fixings |
| `delta` | `swap.delta@1` | yes | `float` | currency, per unit, over the interval | prev_pricer |
| `convexity` | `swap.convexity@1` | yes | `float` | currency, per unit, over the interval | prev_pricer |

default_layers: carry, roll, delta, convexity

##### swap: conventions vocabulary

| key | type | accepted values |
|---|---|---|
| `calendar` | name | - |
| `spot_lag_days` | int | - |
| `day_count` | token | `act360`, `act365f`, `actact_icma`, `actact_isda`, `thirty360` |
| `frequency` | token | `monthly`, `quarterly`, `semiannual`, `annual` |
| `business_day_convention` | token | `unadjusted`, `following`, `modified_following`, `preceding`, `modified_preceding` |
| `payment_lag_days` | int | - |
| `compounding` | token | `daily_compounded`, `daily_average` |
| `stub` | token | `short_front`, `long_front`, `short_back`, `long_back` |
| `end_of_month` | bool | - |
| `fixing_lag_days` | int | - |
| `time_accrual` | token | `lump`, `linear` |

##### swap: pricer_capabilities (optional lookups: a pricer MAY offer them, no core path requires one)

| lookup | args | returns |
|---|---|---|
| `calendar_advance` | date: date, tenor: tenor | date |
| `spot_date` | - | date |
| `par_rate` | effective: date_or_tenor, maturity: date_or_tenor | float |

## Bond

##### bond: terms

| term | type | required / default | unit | values, tokens, aliases |
|---|---|---|---|---|
| `side` | enum | required | - | values `buy`, `sell`; aliases `long`->`buy`, `short`->`sell` |
| `security` | string | required | - | - |
| `notional` | number | required | currency | positive |
| `extras` | mapping | optional | - | - |

Direction: term `side`, sign `buy` = +1, `sell` = -1.

##### bond: methods, measures, layers

| name | kind | required | returns | unit | sign | synonyms | binding |
|---|---|---|---|---|---|---|---|
| `value` | method | yes | `Valuation \| float` | currency | holder: positive is an asset of the holder | - | bind |
| `pv` | measure | yes | `float` | currency | holder | - | none: derived from `value.pv` |
| `notional` | measure | no | `float` | currency | unsigned | face, size, principal | none: derived from `terms.notional` |
| `dv01` | measure | yes | `float` | currency per +1bp | holder: negative for a long | pv01, dollar_delta, bpv, risk | bind |
| `gamma` | measure | yes | `float` | currency per bp^2 | holder | convexity, curvature | bind |
| `rate` | measure | yes | `float` | percent | - | ytm, yield, market_rate | bind |
| `ytm` | measure | no | `float` | percent | - | - | bind |
| `duration` | measure | no | `float` | years | - | - | bind |
| `accrued` | measure | no | `float` | per 100 face | - | - | bind |

| layer | id@version | required | returns | unit | needs |
|---|---|---|---|---|---|
| `carry` | `bond.carry@1` | yes | `float` | currency, per unit, over the interval | prev_pricer |
| `roll` | `bond.roll@1` | yes | `float` | currency, per unit, over the interval | prev_pricer |
| `delta` | `bond.delta@1` | yes | `float` | currency, per unit, over the interval | prev_pricer |
| `convexity` | `bond.convexity@1` | yes | `float` | currency, per unit, over the interval | prev_pricer |

default_layers: carry, roll, delta, convexity

##### bond: conventions vocabulary

| key | type | accepted values |
|---|---|---|
| `calendar` | name | - |
| `settlement_lag_days` | int | - |
| `day_count` | token | `act360`, `act365f`, `actact_icma`, `actact_isda`, `thirty360` |
| `frequency` | token | `monthly`, `quarterly`, `semiannual`, `annual` |
| `business_day_convention` | token | `unadjusted`, `following`, `modified_following`, `preceding`, `modified_preceding` |
| `payment_business_day_convention` | token | `unadjusted`, `following`, `modified_following`, `preceding`, `modified_preceding` |
| `payment_lag_days` | int | - |
| `stub` | token | `short_front`, `long_front`, `short_back`, `long_back` |
| `end_of_month` | bool | - |
| `compounding` | token | `monthly`, `quarterly`, `semiannual`, `annual` |
| `yield_convention` | token | `treasury`, `street` |
| `ex_dividend_days` | int | - |
| `financing_day_count` | token | `act360`, `act365f`, `actact_icma`, `actact_isda`, `thirty360` |
| `time_accrual` | token | `lump`, `linear` |

##### bond: pricer_capabilities (optional lookups)

| lookup | args | returns |
|---|---|---|
| `security` | token: string | mapping |
| `quote` | security: string | mapping |

##### reserved rows (the engine owns them; never a layer, never a binding)

`total`, `transactions`, `cash_interest`, `financing`, `unexplained` (and the baseline rows `tay_delta`, `tay_convexity`, `tay_unexplained`)

## What each name MEANS (from the `doc:` text and ADR 005; read the YAML for the full text)

| name | definition in one line | classic mistakes |
|---|---|---|
| `value` -> `Valuation(ts, pv, cash, financing)` | ONE unit as built, with its notional. `pv` = full economic value, holder-signed (an asset is positive). `cash` = flows realised in `[prev_ts, ts)`. `financing` = funding accrual over the same interval. Step P&L per unit = `pv - prev_pv + cash + financing`. A bare number means `Valuation(pv=number)` | pv per unit notional instead of per unit as built; a sign flipped for a receiver; `cash` forgotten (a swap payment then looks like a loss); pv including a flow already paid |
| `dv01` | DOLLAR DELTA: change in value for a +1bp move of the instrument's own market rate, per unit as built with its notional. A 120mm ten-year payer is roughly +100,000. Payer positive (swap), long negative (bond) | per one million, per 100 face, per unit notional; the receiver's sign |
| `gamma` | second derivative of the value for the SAME +1bp move as `dv01`, per unit as built (currency per bp squared) | a different bump size than `dv01`; per 1% instead of per bp |
| `rate` | the market rate of the (remaining) instrument: a par rate for a swap, a yield for a bond, in PERCENT (4.25, not 0.0425) | decimals; the contract fixed rate instead of the par rate |
| `delta_ladder` | bucketed dollar delta: `{'3M': ..., '10Y': ...}` currency per +1bp of that bucket's rate; sums to `dv01` within a declared tolerance; exactly the bound `keys` | lower-case keys, an extra bucket, a Series, buckets that do not sum to `dv01`, a different bump definition than the reference |
| `carry` | value change over the interval if the market were unchanged in DATE space (forwards are realised), including flows paid and the funding accretion | forgetting the flows paid in the interval |
| `roll` | value change beyond carry if the market were unchanged in TENOR space (the curve rolls down), plus realised versus assumed fixings | ignoring realised fixings |
| `delta` | first-order change from the realised market move: dV/dx times dx over the curve's risk factors (directional derivative along the realised zero-rate move) | a parallel dv01 times a rate change (that is the engine's baseline, not this layer) |
| `convexity` | second-order change: `1/2 dz' H dz` over the same risk factors as `delta` | a different move than `delta` |
| `ytm`, `duration`, `accrued` (bond, optional) | yield to maturity in percent; modified duration in years; accrued interest per 100 face | |

Layer definitions in symbols (ADR 005, both adapters and the reference stack): `carry = X_fwd - V0`, `roll = X_roll_act - X_fwd`, `delta = (V_base + cash - X_roll_act) + g.dz`, `convexity = 1/2 dz'H dz`;
`unexplained` is the engine's remainder. Skill `pricebt-layers-and-ladder` builds them.

## The generator (run it again after any schema change)

```python
from pricebt.contracts.schema import SchemaRegistry

S = SchemaRegistry.default()
for ac in ("swap", "bond"):
    sch = S.get(ac)
    print(ac, "terms:", {n: (t.type, "required" if t.required else "optional") for n, t in sch.terms.items()})
    print(ac, "direction:", sch.direction.term, dict(sch.direction.sign))
    for sec in (sch.methods, sch.measures):
        for n, s in sec.items():
            print(f"{ac}.{n}: {s.kind} required={s.required} returns={s.returns!r} unit={s.unit!r} sign={s.sign!r} synonyms={list(s.synonyms)} derived={s.derived!r}")
    for n, s in sch.layers.items():
        print(f"{ac}.{n}: layer {s.id}@{s.version} required={s.required} returns={s.returns!r} unit={s.unit!r} needs={list(s.needs)}")
    print(ac, "conventions:", {n: (c.type, list(c.values)) for n, c in sch.conventions.items()})
    print(ac, "lookups:", list(sch.capabilities))
```
