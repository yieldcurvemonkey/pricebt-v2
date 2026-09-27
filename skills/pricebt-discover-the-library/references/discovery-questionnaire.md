# Discovery questionnaire: answer it from the library's documentation BEFORE writing adapter code

Nothing here is known about your library. Every question is answered from ITS documentation, source, examples or a probe; the right-hand columns say why pricebt asks and where the answer lands.
Mark every answer with its evidence: `DOC <page>`, `CODE <file>`, `PROBE <script>` or `UNKNOWN`. An `UNKNOWN` is a probe to write (`probe-skeleton.md`), never a guess.
Copy the tables into `docs/design/11-<lib>-conventions.md` (`conventions-doc-template.md`, section 0 and the section named in the last column) and fill them in.

Vocabulary: a **schema name** is a name pricebt expects (`value`, `dv01`, ...; `src/pricebt/contracts/schemas/swap.yaml`); a **snapshot** is pricebt's plain immutable market data
(`src/pricebt/snapshot.py`); **wrap** turns a snapshot into your library's market objects; **known answer** is a number you can compute without the library.

## A. The API surface, one row per schema name (the inventory)

Fill one row per name of `swap.yaml` (and of `bond.yaml` if the library prices bonds). Columns for YOUR answer: candidate call(s) | signature | what it returns (type, unit, whose sign) | what it needs
(market object, valuation date, session) | cost of one call | evidence.

| id | schema name | question | why pricebt asks | lands in |
|---|---|---|---|---|
| A1 | `value` | Which call values a swap for a market and a valuation date? Does it return a number, an object, a table of legs? Currency? Whose view (payer or receiver), and is the sign of a payer negative? | `value` returns a `Valuation(pv, cash, financing)` per ONE unit, holder-signed (`pricebt.pricable`); a wrong view is a wrong sign everywhere | `swap.py` `value`; section 3 |
| A2 | `dv01` | Which call gives the change in value for +1bp of the swap's own market rate? Analytic or bump (which size, central or one-sided)? Per what notional (the trade's, per million, per unit)? Which variable moves (par rate, zero rate, input quotes)? | `dv01` is currency per +1bp for one unit as built, payer positive; the variable decides the number (par vs zero differ by a few percent) | `swap.py` `dv01`; section 5 |
| A3 | `gamma` | Second derivative in the SAME variable? Per bp squared? Or only a bump you must do yourself? | `gamma` must be the second derivative of the variable of `dv01` | `swap.py` `gamma`; section 5 |
| A4 | `rate` | Which call gives the par (market) rate of the REMAINING swap? Decimal or percent? What after maturity? | `rate` is in PERCENT; the baseline decomposition reads the bp size from its unit | `swap.py` `rate`; section 3 |
| A5 | `delta_ladder` | Is there bucketed / key-rate risk? Labels (case, spelling, an extra overnight bucket?), the pillar set, the variable, the unit, the notional basis, a Series or a dict? | `dict[str, float]`, upper-case tenor keys, sums to `dv01` | `swap.py` reducer; section 6 |
| A6 | `carry`, `roll`, `delta`, `convexity` | Is there any attribution, theta, roll-down or scenario API? Which of these exist: (a) value as of a later date on an unchanged market; (b) a curve rolled forward in tenor space; (c) a shifted / bumped market with per-node shifts; (d) a constructor for a curve from node dates and discount factors; (e) the projected cash-flow list with pay dates and amounts; (f) the flows paid between two dates | the four layers are assembled from (a) to (f) (`pricebt-layers-and-ladder`); each missing one is a fallback you must write | `swap.py` `decomposition`; section 11 |
| A7 | terms | How do you build a swap: effective date (spot rule, forward start), maturity (tenor or date, tenor grammar), notional (signed or not), fixed rate (unit), side, a par-rate helper, a spread over par? | the factory receives NEUTRAL terms (`side`, `effective`, `maturity`, `notional`, `fixed_rate` in PERCENT, `extras.par_spread_bp`) and returns concrete dates and a percent rate | `swap.py` factory; section 2 |
| A8 | conventions | For EACH key of the swap vocabulary (`calendar, spot_lag_days, day_count, frequency, business_day_convention, payment_lag_days, compounding, stub, end_of_month, fixing_lag_days, time_accrual`): how does the library express it, which values does it support, which is hard-wired, which default is silent? | an adapter must raise `[CFG-CONVENTION-UNSUPPORTED] convention day_count='act365f' is in the vocabulary but this adapter does not support it: <reason>; supported: [...]` for what it cannot honour, never diverge silently | `conventions.py`; section 10 |

## B. Market data: what goes in, in what shape

| id | question | why pricebt asks | lands in |
|---|---|---|---|
| B1 | What does the library want as a curve: discount factors at dates, zero rates, par/OIS quotes, a named curve id in a service? Discount AND projection curve (one or two)? | the snapshot carries DISCOUNT FACTORS at node dates (`CurveSnapshot`); `wrap` converts, and the conversion must round-trip | `wrap.py`; section 3 |
| B2 | Which interpolation does it use, on which variable? Can it do `log_linear_df` (ln DF linear in calendar time, extended past the last node with the last segment's forward)? Extrapolation? | an adapter maps the snapshot's interpolation TAG or raises; a silent substitute moves every price | `wrap.py` (`ConfigError` for another tag); section 3 |
| B3 | How are fixings supplied (per index and date; a store keyed by name)? Unit (percent or decimal)? Which are allowed to be present (is the fixing of the valuation date itself used if stored)? What does a missing fixing do (raise, forecast, zero)? | a snapshot carries only fixings strictly BEFORE the reference date, in the unit its `FixingsSeries.unit` says; a started swap needs every business day | `wrap.py`, `swap.py` fixings guard; section 4 |
| B4 | How are calendars defined: a named calendar (which holiday data, until which date), or built from a list of holidays and a weekend rule? Are named calendars mutable process-global objects? What happens for a date AFTER the last holiday it knows (silent business day?) | the snapshot ships the holidays (`CalendarData`, weekmask); the library must see THOSE, never its own named calendar | `wrap.py`; section 2 |
| B5 | Date and tenor formats: date object, ISO string, serial number, time zone attached? Tenor grammar (case, `D W M Y` supported)? Month-end and roll rules of tenor addition? | dates cross the boundary as `datetime.date`; convert once, in `_compat.py` | `_compat.py`; section 2 |
| B6 | Does the library ALSO serve the market data (curves, fixings, calendars) through a service or a database? As-of semantics (latest, dated, bi-temporal knowledge time)? Entitlements, rate limits? | a provider turns it into snapshots and must respect the look-ahead guard (`pricebt-market-data-snapshots`, `pricebt-enterprise-platform-patterns`) | provider; section 0 |
| B7 | What is the smallest complete input for one valuation, and what does building it cost? | decides what `wrap` builds once per snapshot and what a position may not rebuild | `wrap.py` memo |

## C. Global state, runtime and failure

| id | question | why pricebt asks | lands in |
|---|---|---|---|
| C1 | List every process-global the library keeps: valuation / evaluation date, settings switches (include flows on the reference date), a fixings store, a calendar registry, caches, a licence banner, logging configuration. For each: how do you set it, how do you restore it, what observes a change? | the engine is sequential but the layers hold TWO worlds; anything global must be set and restored around every call (`_compat.guard`) | `_compat.py`; section 8 |
| C2 | Is it thread-safe? Can two markets (two valuation dates) coexist in one process? Fork safe? | tie-out and notebooks run several stacks in one process | section 8 |
| C3 | Do its objects deep-copy and pickle? | the engine deep-copies positions; an adapter object should be plain data that rebuilds library objects per call | `swap.py` class; section 8 |
| C4 | List its exception classes and what each means: missing data, bad input, unsupported, timeout, permission, licence. | pricebt understands ONLY its own errors: a raw exception aborts a run even under `on_error: record` (`pricebt-enterprise-platform-patterns`) | `_compat.translate`; section 8 |
| C5 | Cost: seconds and calls per valuation, per bump, per curve build; batching; caching; limits. | budget = positions x cadence points x calls; a full layer flush is 6 (QuantLib adapter) to 13 (worked example) valuations per position | section 0; `pricebt-enterprise-platform-patterns` |
| C6 | Licence and data terms: may tests run in CI, may fixtures be committed, may results be shared? Warnings printed on import? | never commit proprietary data; the library's name stays out of core | section 13; `pricebt-guards-and-packaging` |
| C7 | Version and environment: how to read the library version, the environment profile (uat/prod), the data set version. | a result is only reproducible with them; pricebt has no manifest field for it (`pricebt-enterprise-platform-patterns`) | section header; provenance |

## D. Numerical facts to verify with a probe on a KNOWN ANSWER

Each is one `fact` row of `probe-skeleton.md` (hypothesis, probe, number) and later one `test_fact_...` (`probe-to-test.md`).

| id | fact | known answer that proves the probe works | consequence |
|---|---|---|---|
| D1 | a swap struck at its own par is worth 0 | the value is 0 to round-off | if not, the probe or the construction is wrong |
| D2 | the sign of the library's value for a rate rise | a payer gains when rates rise | `sign: -1` on all bindings and in `value` if it is the receiver's |
| D3 | the unit of every rate (curve, fixings, par rate, fixed rate) | a 4 percent market gives 0.04 or 4.0 | `scale`, or a conversion in code |
| D4 | risk versus notional | risk at 1e6 and at 1e7 | per-million: multiply by `notional / basis` in code |
| D5 | risk equals the fixed-leg PV01 | lowering the strike by 1bp changes the value by exactly the fixed leg's PV01 | pins unit and sign of `dv01` (`probe-skeleton.md` H4) |
| D6 | which variable the risk moves | ratio of the risk to a parallel zero-curve bump | par space vs zero space (a few percent): name it, keep the other as an extension measure |
| D7 | schedule: stub side, roll day, adjusted versus unadjusted dates, payment lag | a 1Y annual swap has two dates; an 18M swap has a short FRONT stub | must equal the reference over thousands of dates and tenors (`docs/design/11-quantlib-conventions.md` section 2 is the model) |
| D8 | explicit maturity date: which roll day results | maturity on the effective day of month | stub tenors: the resolved terms alone do not pin the schedule (doc 11 section 9.2) |
| D9 | a flow paid ON the valuation date: in `value` or dropped? | a swap with a coupon paid today | pricebt: a flow paid on D is inside `V(D)` and is cash at the next point (`[t0, t1)`) |
| D10 | started swap: does the current coupon use the fixings? which fixing dates are asked? | a swap started a month ago vs the same swap with an absurd fixing | missing fixing must raise `MarketDataUnavailable`, not price silently |
| D11 | curve round trip | discount factor in, discount factor out, at and between nodes (1e-14) | the interpolation tag |
| D12 | calendar: the library's business days versus the snapshot's holidays | 1,000+ dates | the horizon: what after the last holiday |
| D13 | end-of-month and other silent defaults | a spot date on the last business day of a month | set every such flag EXPLICITLY (doc 11 section 6: the helper's `endOfMonth` default turned on and cost 2.3e-3) |
| D14 | a process-global read: what if it is unset or changed mid-call | call without the global; change it and restore | `guard` + `translate` |

## E. The facts table (write it down, one line each; evidence in the right column)

| fact | your library's answer | evidence | consequence in pricebt (binding, code, unsupported) |
|---|---|---|---|
| units (rates, fixings, risk, ladder) | | | `scale` / conversion |
| signs (value, dv01, gamma, ladder, layers) | | | `sign`, code |
| date and tenor formats, time zones | | | `_compat.to_date` |
| calendars, holiday data and its horizon | | | `wrap` |
| day counts (accrual, curve time) | | | conventions map |
| fixings: unit, visibility, missing behaviour | | | fixings guard |
| interpolation and extrapolation | | | tag check |
| curve inputs and builders | | | `wrap` |
| bump sizes and risk definitions (dv01, gamma, ladder) | | | measures |
| process-global state | | | `guard` |
| thread safety / re-entrancy | | | docs, guards |
| exceptions (classes and meaning) | | | `translate` |
| cost per call (valuation, ladder, curve build) | | | budget |
| licence and data terms | | | do not commit; document |
| version and environment | | | provenance |

The filled model is the worked example's mismatch table: `skills/pricebt-wire-external-library/example/README.md` section 2 ("The foreign API, and where each mismatch is converted"), and the real one is
`docs/design/11-quantlib-conventions.md`.

## F. Stop conditions (ask the owner of the library, do not guess)

* A convention you cannot verify against a known answer or a second source (the reference stack, another library). Record it as UNSUPPORTED with the reason; a `ConfigError` beats a silent 1e-3.
* A licence clause that forbids running the library in CI or committing any data derived from it: use recorded, sanitised snapshots and opt-in live tests (`pricebt-enterprise-platform-patterns`).
* A risk number whose variable you cannot identify: do not bind it to `dv01`; keep it as an extension measure until you can.
