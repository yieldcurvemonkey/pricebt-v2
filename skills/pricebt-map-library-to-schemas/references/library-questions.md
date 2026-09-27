# Questions you must answer from YOUR library's documentation before a mapping row can be filled

pricebt knows nothing about your library and this skill invents nothing about it. Every `<LIB>` below is a question, not an answer. Answer each from the documentation, code or examples, then PROVE the
answer with a probe whose result you know in advance (skill `pricebt-discover-the-library` has the probing method; `docs/design/11-quantlib-conventions.md` is the model of how the answers are recorded:
a headline table of measured disagreements, one section per item with the script that produced each number, the process-global state, the gaps that remain).

## Units and signs (each one silently multiplies a result by 100, 1e6, or -1)

| question | how to prove it on a known answer |
|---|---|
| Are rates decimals (0.0425), percent (4.25) or basis points, on input AND on output? Is a fixed rate given the same way as a returned par rate? | price a swap at `par_rate`: its value is 0; feed the par rate back in unchanged |
| Whose point of view are `value`, risk and layers (payer, receiver, "long")? What is the sign of the fixed-rate payer's dv01? | a payer struck above par has a NEGATIVE value and a POSITIVE dv01 in pricebt; find the same trade in your library and compare signs |
| What is the risk basis: per bp, per 1%, per one million, per 100 face, per unit notional? Does it scale with the trade's notional? | value and risk of a 1mm and a 50mm trade: is the ratio 50? |
| Is the bump one-sided or central, and what size? (`gamma` must use the same +1bp move as `dv01`) | `gamma` from a second difference against the library's own number |
| Is the value clean or dirty, with or without the coupon paid today, holder-signed or long-only? | value the day before and the day of a payment: the drop equals the cash |

## Dates, calendars, conventions

| question | how to prove it |
|---|---|
| Date type: `datetime.date`, `Timestamp`, ISO string, serial number, library object? Tenor case and grammar (`10y`, `10Y`, `120M`)? | round trip: what you pass in, what comes out |
| How is the valuation date set: an argument, or a process-global that every call reads? Is the curve required to be anchored on it? | value the same trade twice with the date changed in between; call with the date unset |
| Which calendars does it have, by which names, over which horizon? Can you register the snapshot's holiday set? Is registration global, replaceable, or an error on conflict? | a business-day count over a known holiday |
| Which of the schema's conventions can it express (`day_count`, `frequency`, `business_day_convention`, `compounding`, `stub`, `end_of_month`, `payment_lag_days`, `fixing_lag_days`, `time_accrual`, ...)? For each one it cannot: the REASON | one probe per value; the refusal text in your `conventions.py` quotes the reason and the supported set |
| How does it resolve spot, a forward start, a maturity tenor, the par rate? (the factory must return concrete dates and a percent rate) | compare with the reference stack on the same snapshot: `TradeTemplate.build` on both, dates first |

## Shapes and failures

| question | how to prove it |
|---|---|
| What shape does a bucketed risk come back in (Series, DataFrame, dict, object)? Labels and case? An extra bucket (overnight, "total")? Do the buckets sum to the parallel dv01? | print it; sum it; compare with the analytic dv01 |
| Does it offer attribution? Which primitives: value as of a later date with the market unchanged, a market rolled down the curve, a shifted or bumped market, the cashflows of a period, a curve resampled at other pillars? | list the callables that take an as-of date, a market and a shift |
| Which exceptions does it raise for a missing fixing, a bad input, an unknown calendar, an unsupported convention? Does any of them mean "no data" (pricebt: `MarketDataUnavailable`) rather than "bad config" (`ConfigError`)? | provoke each one |
| Which process-global state does a call read or write (valuation date, calendar registry, fixings store, curve cache, a session)? Is it thread-safe? Is a result deterministic across repeated calls? | call twice, in two orders, in two threads |

## When the library also serves the data, or is a remote service

| question | consequence for the mapping |
|---|---|
| In which form does it serve curves: discount factors, zero rates, forwards, par rates? On which dates? With which interpolation? | the provider converts to discount factors at node dates (the only snapshot value kind) and the interpolation TAG must be one pricebt has (`log_linear_df`); a different native interpolation is resampled or refused, never silently substituted |
| Does it price from data you SEND (a curve in the request) or from data it holds server-side as of a date? | the first keeps the snapshot digest meaningful: what you send is what you hashed. The second needs the provider to fetch exactly what the service will use, or the tie-out inputs (L0) prove nothing about the service |
| What does one call cost, and does it accept a batch? Is a call idempotent and cacheable per snapshot? | memoise per snapshot in `wrap` (skill `pricebt-enterprise-platform-patterns`); `ctx.cache` shares a result between `value`, layers and measures at one mark |
| Authentication, sessions, rate limits, confidentiality of what may be written to results and logs | `pricebt-enterprise-platform-patterns` |
