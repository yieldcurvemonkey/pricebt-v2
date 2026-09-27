# Mapping rows: the row template for the conventions document (fill every cell from YOUR library's documentation)

The mapping is NOT a separate deliverable (D17). Its rows, one per schema name, are sections 3, 5, 6 and 11 of `docs/design/11-<lib>-conventions.md` (`skills/pricebt-discover-the-library/references/conventions-doc-template.md`
creates the document at the start of phase 1 and has the same six columns, `gap` included): `value` `rate` `pv` in section 3, `dv01` `gamma` in section 5, `delta_ladder` in section 6, `carry` `roll` `delta` `convexity` in section 11, the
conventions table in section 10. Copy the TABLES of this file into those sections (not this text), fill them, and keep no second file: `check_conventions_doc.py` fails the document while a schema name has no row in
those sections or a cell is empty, and while any `<...>` token of the template is left in the document. A row is MAPPED when every cell has a documented source (a page, or a probe on a known answer); it is DONE only when
the check in `conformance-snippet.md` passes for it on YOUR kit, which needs your `wrap` and your Kit (phase 6 of `pricebt-wire-external-library`), not while you fill the rows (phase 3). Never fill a cell from memory or by analogy
with another library: if the documentation does not say, write `UNKNOWN` and probe it on a known answer (`library-questions.md`); `UNKNOWN` is allowed while you map and not in the finished document.

Columns (the same in every table of the rows):

- **schema name**: the pricebt name, in backticks, first cell (what the checker reads). The contract (unit, sign, shape) of each name is in `schema-cheatsheet.md`; do not copy it here.
- **library callable**: `module.function(args)` or `Class.method(args)` that provides it, with the argument list and what each argument is; `none: <how you build it>` if the library has no such callable.
- **unit**: what the callable RETURNS, next to what pricebt wants: decimal vs percent vs bp, per unit vs per million vs per 100 notional, per bp vs per 1%, date type and case (ISO strings, lower-case tenors), the shape (Series, DataFrame, object).
- **sign**: whose point of view the callable answers from (payer, receiver, holder) and so what the adapter flips; `none` if it already agrees.
- **binding or code**: `binding` (which of `scale`, `sign`, `offset`, `reduce`, `keys`, `kwargs`) or `code` (a method of the instrument class, a `function` target, a reducer, the factory, `wrap`). Use the decision tree of the skill.
- **gap**: what is missing or different and what you decided (a documented definition, a refused convention, a declared tolerance); `none` if nothing.

## 0. Library facts (answer first; each answer names the page or the probe that proves it; they fill the unit and sign cells below and section 8 of the document)

| fact | answer | source or probe |
|---|---|---|
| rates: decimal (0.0425) or percent (4.25) or bp, for inputs AND outputs | | |
| whose point of view are value and risk (payer / receiver / holder)? sign of the fixed-rate payer's dv01 | | |
| risk basis: per bp, per 1%, per one million, per 100 notional, per unit notional | | |
| date type and format: `datetime.date`, ISO string, serial number; tenor case (`10y` vs `10Y`) | | |
| how the library is told the valuation date (an argument, a global setting) | | |
| which globals: valuation date, calendar registry, fixings store, curve cache, session | | |
| which exceptions it raises for a missing fixing, a bad input, a missing calendar | | |
| what shape a bucketed risk comes back in (Series, DataFrame, dict, object), its labels, an extra bucket | | |
| is the par-risk pillar set fixed by the library, and which pillars? (if it differs from the reference's: `pricebt-layers-and-ladder`, "Your library's pillar set is fixed and differs from the reference's") | | |
| does it offer attribution (carry, roll, delta, convexity)? which primitives (value as-of a date, rolled or shifted market, cashflows) | | |
| does it also serve the market data (curves, fixings, calendars)? in which form (zero rates, forwards, discount factors, par rates) | | |
| thread-safety, licence and determinism constraints (repeat calls, caching, randomness) | | |

## 1. Swap

### 1.1 Terms (handled in the FACTORY: `factory(pricer, ts, *, terms, conventions) -> Built(obj, resolved_terms)`; section 3 of the document)

The factory receives the terms normalised (`side` canonical, `effective` default `spot`, tenors and dates untouched) plus `direction` (+1 pay, -1 receive), and must return concrete dates
(`dt.date`) and a numeric percent rate. It resolves `par`, `effective` and `maturity` at the fill pricer.

| term | library callable | unit | sign | binding or code | gap |
|---|---|---|---|---|---|
| `side` / `direction` | | | | code: factory | |
| `effective` | | | | code: factory | |
| `maturity` | | | | code: factory | |
| `notional` | | | | code: factory | |
| `fixed_rate` | | | | code: factory | |
| `extras` | | | | code: factory | |

Contracts (for the cells; do not paste): `side` / `direction`: `pay`/`receive`, aliases `buy`/`sell`; direction +1/-1; `effective`: date, `spot` default, or a tenor from spot; `maturity`: date or tenor from the effective date; required; `notional`: number > 0, currency, unsigned; `fixed_rate`: PERCENT or `par`, default `par`; the factory returns the resolved percent; `extras`: mapping; standard key `par_spread_bp`, bp over par when `par`; refuse unknown keys.

### 1.2 Value, measures, ladder (sections 3, 5 and 6 of the document)

| schema name | library callable | unit | sign | binding or code | gap |
|---|---|---|---|---|---|
| `value` | | | | | |
| `rate` | | | | | |
| `pv` | derived by pricebt from `value`: nothing to call | none | none | none: pricebt derives it, bind nothing | none |
| `dv01` | | | | | |
| `gamma` | | | | | |
| `delta_ladder` | | | | | |

Contracts (for the cells; do not paste): `value`: `Valuation(ts, pv, cash, financing)` per unit as built; cash = flows realised in `[prev_ts, ts)`; `rate`: float, PERCENT, par rate of the remaining swap; `pv`: derived by pricebt from `value`: NO binding; `dv01`: float, currency per +1bp, payer positive; `gamma`: float, currency per bp^2, same bump as `dv01`; `delta_ladder`: `dict[str, float]`, upper-case tenor keys = the bound `keys`, currency per +1bp of the bucket, payer positive, sums to `dv01`.


### 1.3 Layers (section 11 of the document; per unit, over the interval since the previous cadence point; `needs: prev_pricer`, `roll` also fixings)

| schema name | library callable or primitives | unit | sign | binding or code | gap |
|---|---|---|---|---|---|
| `carry` | | | | | |
| `roll` | | | | | |
| `delta` | | | | | |
| `convexity` | | | | | |

Contracts (for the cells; do not paste): `carry`: `swap.carry@1`: `X_fwd - V0`, the t0 world's flows valued at t1; `roll`: `swap.roll@1`: `X_roll_act - X_fwd`, curve unchanged in tenor space anchored at t1, realised fixings; `delta`: `swap.delta@1`: `(V_base + cash - X_roll_act) + g.dz`, directional derivative along the realised zero-rate move; `convexity`: `swap.convexity@1`: `1/2 dz' H dz`.

### 1.4 Conventions (section 10 of the document; the `conventions:` block is shared by every stack; refuse what the library cannot honour: `CFG-CONVENTION-UNSUPPORTED`, naming the reason and the supported set)

| key | vocabulary | library setting | supported values | reason for each refused value |
|---|---|---|---|---|
| `calendar` | a NAME of a calendar in the snapshot | | | |
| `spot_lag_days` | int | | | |
| `day_count` | `act360 act365f actact_icma actact_isda thirty360` | | | |
| `frequency` | `monthly quarterly semiannual annual` | | | |
| `business_day_convention` | `unadjusted following modified_following preceding modified_preceding` | | | |
| `payment_lag_days` | int | | | |
| `compounding` | `daily_compounded daily_average` | | | |
| `stub` | `short_front long_front short_back long_back` | | | |
| `end_of_month` | bool | | | |
| `fixing_lag_days` | int | | | |
| `time_accrual` | `lump linear` | | | |

### 1.5 Optional lookups on the pricer (`pricer_capabilities`; no core path requires one)

| lookup | contract | library callable | offered? |
|---|---|---|---|
| `calendar_advance(date, tenor)` | date | | |
| `spot_date()` | date | | |
| `par_rate(effective, maturity)` | float, percent | | |

### 1.6 Extension names (beyond the schema; identifier form, prefixed with the library's short name, for example `acme_zero_dv01`; declared in `Kit.extra` as `measure` or `layer`)

| name | kind | meaning and unit | library callable | done by | compared in a tie-out? (only if listed in `audit_measures` AND every stack binds it, the reference included) |
|---|---|---|---|---|---|
| | | | | | |

## 2. Bond

### 2.1 Terms (factory)

| term | library callable | unit | sign | binding or code | gap |
|---|---|---|---|---|---|
| `side` / `direction` | | | | code: factory | |
| `security` | | | | code: factory (+ provider) | |
| `notional` | | | | code: factory | |
| `extras` | | | | code: factory | |

Contracts (for the cells; do not paste): `side` / `direction`: `buy`/`sell`, aliases `long`/`short`; direction +1/-1; `security`: string: an identifier or an alias the provider's reference data resolves; `notional`: number > 0, face amount, unsigned; `extras`: mapping, for example financing terms; refuse unknown keys.

### 2.2 Value and measures

| schema name | library callable | unit | sign | binding or code | gap |
|---|---|---|---|---|---|
| `value` | | | | | |
| `dv01` | | | | | |
| `gamma` | | | | | |
| `rate` | | | | | |
| `ytm` | | | | | |
| `duration` | | | | | |
| `accrued` | | | | | |
| `pv` | derived by pricebt from `value`: nothing to call | none | none | none: pricebt derives it, bind nothing | none |

Contracts (for the cells; do not paste): `value`: `Valuation` per unit as built: dirty value plus flows due, holder-signed; `dv01`: float, currency per +1bp of the bond's yield, long NEGATIVE; `gamma`: float, currency per bp^2, same bump as `dv01`; `rate`: float, PERCENT: the bond's own yield to maturity; `ytm`: optional; float, percent; `duration`: optional; float, modified duration in years; `accrued`: optional; float, accrued interest per 100 face; `pv`: derived by pricebt: NO binding.

### 2.3 Layers (`bond.carry@1`, `bond.roll@1`, `bond.delta@1`, `bond.convexity@1`)

| schema name | library callable or primitives | unit | sign | binding or code | gap |
|---|---|---|---|---|---|
| `carry` | | | | | |
| `roll` | | | | | |
| `delta` | | | | | |
| `convexity` | | | | | |

Contracts (for the cells; do not paste): `carry`: coupon accrual and pull to par at an unchanged yield, net of financing, plus flows paid in the interval; `roll`: yield change from ageing down the yield curve at an unchanged curve; zero when the snapshot carries no such curve; `delta`: `-dv01` times the residual yield move in bp after roll; `convexity`: `1/2 gamma` times the yield move squared.

### 2.4 Conventions

| key | vocabulary | library setting | supported values | reason for each refused value |
|---|---|---|---|---|
| `calendar` | a NAME of a calendar in the snapshot | | | |
| `settlement_lag_days` | int | | | |
| `day_count` | `act360 act365f actact_icma actact_isda thirty360` | | | |
| `frequency` | `monthly quarterly semiannual annual` | | | |
| `business_day_convention` | `unadjusted following modified_following preceding modified_preceding` | | | |
| `payment_business_day_convention` | same tokens | | | |
| `payment_lag_days` | int | | | |
| `stub` | `short_front long_front short_back long_back` | | | |
| `end_of_month` | bool | | | |
| `compounding` | `monthly quarterly semiannual annual` (yield quotation) | | | |
| `yield_convention` | `treasury street` | | | |
| `ex_dividend_days` | int | | | |
| `financing_day_count` | same tokens as `day_count` | | | |
| `time_accrual` | `lump linear` | | | |

### 2.5 Optional lookups and extension names

| lookup or extension | contract | library callable | done by |
|---|---|---|---|
| `security(token)` | mapping: reference data for a security or alias | | |
| `quote(security)` | mapping: the market quote | | |
| (extension names) | | | |

## 3. Filled example: the fictional `acmelib` swap (real rows from `skills/pricebt-wire-external-library/example/acme_adapter/swap.py`; the same six columns as the template)

| schema name | library callable | unit | sign | binding or code | gap |
|---|---|---|---|---|---|
| `value` | `acme.pv(contract, market)`, `acme.cashflows(contract, market, since=iso)` | currency, one contract | the RECEIVER's: times `direction * -1`, pv and cash both | code: `AcmeSwap.value` returns the `Valuation` (a binding cannot post-process one) | none |
| `dv01` | `acme.pv01(contract, market)` before the start; the ladder sum after it | per ONE MILLION: times `notional / 1e6` | the receiver's: `sign: -1` | binding `sign: -1` + code `_per_unit` (a `scale` cannot read the notional) | the definition changes at the start date so that it equals the ladder sum |
| `gamma` | `acme.gamma(contract, market, tenors)` | per ONE MILLION, as `dv01` | the receiver's | binding `sign: -1` + code | none |
| `rate` | `acme.par_rate(contract, market)` | a DECIMAL; NaN once expired: `scale: 100.0` | none | binding `scale: 100.0` | none |
| `delta_ladder` | `acme.bucket_risk(contract, market, tenors)` | pandas Series, lower-case labels, an extra `on` bucket; per million | the receiver's | binding `reduce: acme_ladder_to_tenor_dict`, `keys`, `sign: -1` + code `_per_unit` | the `on` bucket is dropped ONLY when it is 0 |
| `carry`, `roll`, `delta`, `convexity` | none: acmelib has no attribution; built from `acme.pv(..., as_of=)`, `acme.cashflows`, `Market.rolled`, `Market.shifted`, `ZeroCurve.resampled` (ADR 005 definitions) | currency per unit over the interval | the receiver's: `sign: -1` | code: functions `acme_adapter.swap:carry` ... bound as `function` targets | needs `registry.allow` |
| extension `acme_zero_dv01` | central difference of `acme.pv` on `Market.shifted(+-0.5bp)` | per million | the receiver's | binding `sign: -1`, declared in `Kit.extra` | not compared in the tie-out: the reference does not bind it |
| conventions `day_count` | `basis` `A360` / `A365` | `act360`->`A360`, `act365f`->`A365` | none | code: `conventions.py` | `thirty360`, `actact_*` refused: "acmelib has only ACT/360 and ACT/365 bases" |


The same rows for the SERVICE example (`skills/pricebt-wire-external-library/example-service/zeta_adapter/swap.py`): `value` is holder-signed already (no flip), `dv01` `scale: 0.1` (per +10bp), `gamma` `scale: 0.01`, `rate` `scale: 0.01` (bp to percent),
`delta_ladder` `reduce: zeta_ladder_to_tenor_dict`, `scale: 0.1` and the ONLY `sign: -1` (the service reports a payer's ladder negative).
