# Discovery questionnaire: an unknown pricing library

Answer every question **with evidence** before you write the config: a code path, a docstring, or
a REPL transcript. A guess becomes a silent P&L bug. Where to look: the library's docstrings and
`help()`, its own unit tests (they show real call sequences and expected units), example
notebooks, internal wiki pages, and `dir()` on the objects the library returns. The last column
says where the answer goes in the config.

## 1. Session and auth

| Question | How to find out | Goes into |
|---|---|---|
| How do I open a session (client, context, login)? Is it expensive? | Look for `connect`, `login`, `Session`, `Context` or `init` in the package's top level and its quick-start. Time it. | `code:`, module-level, **once** |
| Does it need credentials, a VPN, an env var, or a licence file? | Read the quick-start and the exceptions it raises when unset. | `imports:` (set env vars *before* importing, like the ARBS config) |
| Does it have side effects (writes a cache, opens Excel, hits production)? | Read the source of the calls you will make. Run under a network monitor or with the network off. | A safety comment, plus a guard in `code:` |
| Is it thread-safe or process-safe? | Docs. Assume **no**. | One client per process |

## 2. Market handle by date

| Question | How to find out | Goes into |
|---|---|---|
| Which call gives a curve or market for a historical as-of date? | Look for `market`, `curve`, `snapshot`, `env`, `scenario` or `as_of`. | `load_market(d)` |
| What date type does it take: `date`, `datetime`, ISO string, Excel serial, tz-aware timestamp? | Signature and docstring. Pass each type and see which one raises. | the conversion in `load_market` |
| Is "as of" an end-of-day close, and in **which timezone** (NY 17:00, London 16:00, Tokyo)? | Docs, or compare with a known published close. | `PricebtSession.use(..., tz=, eod_time=)`, or use `pricebt_timestamp` |
| Which curve or market id do I pass for my trade (e.g. `USD.SOFR`, `USD-SOFR-1D`)? | List the available ids. | a `_CURVE` constant |
| Is the returned object live (re-fetches) or a frozen snapshot? | Price twice, minutes apart. | Must be frozen for a backtest |

## 3. Holidays, weekends, missing data

| Question | How to find out | Goes into |
|---|---|---|
| What happens on a weekend? A holiday? A data gap? Before the history starts? | Call it on a known Saturday, 25 Dec, and a date 20 years ago. | `load_market` returns **`None`** for every "no data" case. It catches the exception type, and catches it narrowly |
| Does it silently return the **previous** day's curve instead (stale roll-back)? | Compare the returned object's own reference date with the date you asked for. | Validate `ref_date == d`, else raise or return `None` |
| Which holiday calendar does it use for spot, schedules and rolls? | Docs, or `spot_date` around a holiday. | Nothing: the library owns it. Just never compute dates yourself |

## 4. Trade construction

| Question | How to find out | Goes into |
|---|---|---|
| How do I build the trade: a constructor, a dict, a builder? | Examples, tests. | `build_swap(resolved)` |
| Side: signed notional, a direction flag (`PAY`/`RECEIVE`, `BUY`/`SELL`), or a sign on the rate? Pay/receive **of which leg**? | Price a payer and a receiver. Their PVs must be exact negatives. | `build_*` maps pricebt's signed notional (payer > 0) |
| Fixed rate: decimal, percent or bp? Can it be "at par" (`None`/`0`/`"ATM"`)? Does par re-strike on each market? | Build with 3.0 and with 0.03, and compare the PV to the par rate. | `resolve` pins a **decimal** strike. `build` converts to the vendor unit |
| Dates: can `end` be a tenor? Is a tenor counted from start, from spot, or from the pricing date? | Build a `"10Y"` trade and price it on two dates. Does the maturity move? | `resolve` pins **absolute** dates using the library's own date helpers |
| Is the trade object market-independent (safe to reuse on any date)? | Does building it need a market? Does it hold fixings? | `trade.build_on: resolve_date` (independent) or `each_market` |

## 5. Measures: codes, units, signs

For **each** pricebt function you need (`npv`, `dv01`, `par_rate`, plus a ladder):

| Question | How to find out | Goes into |
|---|---|---|
| What is the measure code or method name? | Look for a measure enum, a `measures=` list, or the method names. | `functions:` |
| Currency: trade currency, a reporting currency, or a CSA currency? | Price a EUR trade: which currency comes back? | `currency:`, or a conversion |
| **Unit**: per 1bp, per 1% or per unit rate (1.0)? Per unit notional or total? | A 10y 1mm swap has dv01 of roughly 850–950 USD per bp. A number near 0.09 is per unit notional; one near 90,000 is per 1%. | the scale factor |
| **Sign**: PV change for rates **up** or **down**? Payer-positive or receiver-positive? | Bump the curve yourself: `PV(up) - PV(base)` for a payer must be > 0 in pricebt. | the sign flip |
| Rates: decimal, percent or bp? | An ATM 10y par rate of ~0.04, ~4 or ~400. | `* 1e4`, `* 100` or nothing, and `unit: bp` |
| Is `dv01` a zero-rate or a par-rate sensitivity? | Docs. Either is fine, but be consistent with the ladder. | a comment |

## 6. Bucketed risk

| Question | How to find out | Goes into |
|---|---|---|
| Which call gives bucketed delta? What are the key formats (`"USD.SOFR:2Y"`, `("USD", "2Y")`, a DataFrame index)? | Print one result. | `delta_ladder` maps the keys to plain tenors `"2Y"` |
| Same sign and unit as the scalar? Does the ladder sum to the scalar dv01? | Sum it. | Flip or scale it the same way as the scalar |
| Fixed pillars, or pillars that depend on the trade? | Price a 3y and a 30y swap. | fixed output keys, and **raise** on an unknown key |

## 7. Batching and performance

| Question | How to find out | Goes into |
|---|---|---|
| Can one call price many trades or many measures? | Look for list arguments. | `portfolio_functions:` send the whole book in ONE call; `_risk()` requests all measures at once |
| What does one call cost (latency, rate limits)? | Time 10 calls. | memoise per market (`market.__dict__` or a dict keyed by date) |
| Is building a market the expensive step? | Time it. | pricebt already caches one market per (key, date, csa). Never rebuild inside functions |

## 8. Fixings and seasoned trades

| Question | How to find out | Goes into |
|---|---|---|
| Does a seasoned trade (start < as-of) need a fixings history? Where does it come from? | Price a trade that started 3 months ago. | the market object must carry fixings as of `d` (no look-ahead) |
| Does the trade object cache fixings from the market it was built on? | Build on d1, price on d2, then compare with a rebuild on d2. | `build_on: each_market`, or a `remark()` helper (see the ARBS config) |
| What happens after maturity: 0, NaN, or an error? | Price a matured trade. | guard in the functions: `npv` is 0 after the last payment; `par_rate` is NaN |
