# zeta 2.3 - rates pricing service: developer reference

zeta prices USD SOFR overnight-indexed swaps (OIS). It is a *service*: you upload a market once, then send pricing requests that refer to it by id. Every request and every response is a
plain JSON-like `dict` (str, int, float, list, dict, None). There are no zeta objects to hold, no global state, no environment configuration, and every answer is deterministic.

```python
import zeta
c = zeta.Client()
mid = c.upload_market(market)                 # -> "mkt-0001"; do this ONCE per market
resp = c.price({"market_id": mid, "as_of": "2024-05-24", "trades": [...], "measures": [...]})
c.close()
```

## 1. Client

| call | returns | notes |
|---|---|---|
| `zeta.Client()` | client | independent instance: markets belong to the client that uploaded them; ids are not portable between clients |
| `client.upload_market(market: dict)` | `str` market id | stores a private copy (later changes to your dict have no effect). Performs **no validation**: a malformed market is reported by the first `price()` that uses it. Raises `RuntimeError` only on a closed client |
| `client.price(request: dict)` | `dict` | **never raises for a service error**: failures come back as `{"status": "ERROR", "code": ..., "message": ...}`. Always test `resp["status"]` |
| `client.close()` | None | drops every stored market; afterwards `price()` answers `Z412`. `with zeta.Client() as c:` also works |
| `client.stats` | `dict` of ints | see section 9 |

## 2. Market (`upload_market`)

```json
{"asof": "2024-05-24",
 "calendar": {"name": "USD", "holidays": ["2024-05-27", "2024-06-19", "2024-07-04"]},
 "curve": {"name": "SOFR", "day_count": "ACT/360",
           "nodes": [[30, 532.0], [91, 530.0], [182, 522.0], [365, 500.0], [730, 456.0], [1826, 420.0], [3653, 420.0], [10958, 410.0]]},
 "fixings": {"SOFR": {"2024-05-22": 531.0, "2024-05-23": 531.0}}}
```

| field | type / unit | meaning |
|---|---|---|
| `asof` | ISO date `YYYY-MM-DD` (string) | the date the market is quoted for. Required |
| `calendar.name` | str | a label only; two markets may use the same name with different holidays (holidays belong to the market, never to the name) |
| `calendar.holidays` | list of ISO dates | non-business days in addition to Saturday and Sunday. If `calendar` is omitted only Saturdays and Sundays are non-business days |
| `curve.name` | `"SOFR"` | the only curve |
| `curve.day_count` | `"ACT/360"` or `"ACT/365"` | the day count of the zero rates below: `DF(t) = exp(-z * days / basis)` with `z` as a decimal |
| `curve.nodes` | list of `[days, zero_rate_bp]` | `days`: whole ACT calendar days from `asof`, **>= 1** (day 0 is implicit, discount factor 1), strictly ascending. `zero_rate_bp`: **continuously compounded zero rate in basis points** (500.0 = 5%). Between nodes the discount factor is log-linear in days; beyond the last node the forward rate is held flat |
| `fixings.SOFR` | `{ISO date: bp}` | published overnight SOFR by date, in **basis points** |

## 3. Price request

| field | type | meaning |
|---|---|---|
| `market_id` | str | from `upload_market`. Required |
| `as_of` | ISO date string | the valuation date. Must satisfy `asof <= as_of < asof + last node days`. Required |
| `scenario` | optional dict | exactly one key: `{"parallel_bp": x}` or `{"roll": "TENOR"}` (section 5) |
| `trades` | non-empty list of trade dicts | priced together in one call (section 4). Required |
| `measures` | non-empty list of str | any of `NPV`, `RISK_10BP`, `CONVEXITY_10BP`, `PAR_BP`, `LADDER_10BP`, `CASH_TO_DATE` (section 6). Required |

Unknown keys anywhere in a request, trade or market are rejected with `Z101`. Dates are always ISO **strings** (a `datetime.date` is `Z101`). The request is atomic: if any trade fails, no result is returned.
Checks run in this order: request shape (Z101), `market_id` (Z412), market content (Z101, Z301), `as_of`, `scenario`, `measures`, `trades` in list order (Z101, Z204), then pricing (Z530).

## 4. Trade

| field | value | unit / meaning |
|---|---|---|
| `product` | `"OIS"` | the only product |
| `leg_fixed` | `"PAY"` or `"RECEIVE"` (upper case) | `PAY`: you pay the fixed leg and receive compounded SOFR (you gain when rates rise) |
| `notional_mm` | float > 0 | notional in **millions** of currency (10.0 = 10,000,000). Unsigned: direction is `leg_fixed` |
| `start` | ISO date or `{"spot_lag": n}` | a date is adjusted modified following (the next business day, unless that falls in the next month, then the previous business day). `{"spot_lag": n}` is `n` business days (0..10) after **the request's `as_of`** |
| `end` | ISO date, `{"tenor_years": n}` or `{"tenor_months": n}` | a tenor is measured from the (adjusted) start, whole months or years only, 1 month to 100 years. A date is the final accrual end: the schedule rolls on the start date's day-of-month when that day adjusts to the end date over a whole number of years, else on the end date's own day-of-month |
| `fixed_rate_bp` | float or `"PAR"` | fixed rate in **basis points** (425.0 = 4.25%). `"PAR"` means the par rate as of the request's `as_of` |

Everything relative (`spot_lag`, tenors, `"PAR"`) is resolved **at the request's `as_of`**; zeta keeps no trades. Each result echoes the resolved terms (section 7). To re-price a booked trade on a later `as_of`, send back its `start` date and its `fixed_rate_bp` and keep the original `end` (a tenor is then measured from the same start). `resolved.end` is the *adjusted* last accrual date: sent back as an explicit `end` it reproduces the schedule for whole-year terms, but for other spans (18 months, say) the schedule would roll on that date's own day-of-month and interior dates can move by a day.

## 5. Dates: `asof`, `as_of`, fixings, scenarios

* `as_of == asof`: price on the market as quoted.
* `as_of > asof`: the market is **rolled in date space**. Discount factors are re-based to `as_of` (`DF(d) / DF(as_of)`), so every forward keeps its calendar dates (forwards are realised). Holidays and fixings apply.
* **Fixings.** The fixing dated `D` is the overnight rate for the accrual from business day `D` to the next business day; it is published on the next business day, so it is used only when `as_of > D`. For a trade with adjusted start `S < as_of`, every business day from `S` to the business day before `as_of` needs a fixing (this holds even after the trade has ended, until the request's `as_of`).
  Business days **before `asof`** must be in `market.fixings.SOFR`, else `Z530` names the first missing date. Business days from `asof` up to `as_of` are taken from `market.fixings.SOFR` when present (entries dated on or after `asof` are allowed) and otherwise **implied by the market's own curve** (the overnight forward of that day, taken from the market curve as uploaded, never from a scenario). Fixings dated on or after `as_of` are ignored, as are entries on non-business days.
* `{"parallel_bp": x}`: every continuously compounded **zero rate** of the curve as of `as_of` moves by `x` bp (negative allowed), on the curve's day-count basis. Implied fixings are not affected.
* `{"roll": "TENOR"}`: the curve is moved in **tenor space** instead: a node `k` days from `asof` becomes a node `k` days from `as_of` with the same zero rate (the curve "rolls down"). The elapsed period uses fixings as above. With `as_of == asof` it changes nothing.

## 6. Measures (per trade, holder-signed currency amounts unless stated)

| measure | unit | definition |
|---|---|---|
| `NPV` | currency | present value at `as_of` of the flows paid **strictly after** `as_of`, to the holder of the booked side: `PAY` gains when rates rise. A flow paid exactly on `as_of` is not in `NPV`; it is reported in `paid_on_as_of` and, if `as_of > asof`, in `CASH_TO_DATE` |
| `RISK_10BP` | currency per +10bp | change in value for a **+10bp parallel move of the par swap curve**, for the whole trade: 10 x the central difference of a +/-1bp move of every par-swap-rate pillar (the 12 pillars of `LADDER_10BP`; the curve is re-bootstrapped so each pillar swap reprices). `PAY` is positive, `RECEIVE` negative. It is first order: the convexity of a 10bp move is not in it |
| `CONVEXITY_10BP` | currency per (10bp)^2 | second difference `V(+10bp) - 2 V(0) + V(-10bp)` of the same par-curve parallel move, estimated as 100 x the second difference of a +/-1bp move |
| `PAR_BP` | bp | the fixed rate (bp) at which the flows paid **on or after** `as_of` are worth zero; `null` once the trade has no such flow. Note the boundary: a coupon paid on `as_of` counts here, so with `fixed_rate_bp = "PAR"` and a coupon due on `as_of`, `NPV = -paid_on_as_of` |
| `LADDER_10BP` | currency per +10bp | list of `{"pillar_months": int, "risk": float}` for the pillars 1, 3, 6, 12, 24, 36, 60, 84, 120, 180, 240, 360 months, in that order, always all twelve. `risk` is the value change for +10bp of that pillar's par swap rate (spot-start annual OIS of that tenor, curve re-bootstrapped), from a +/-0.5bp central difference scaled to 10bp. **Sign: `LADDER_10BP` is reported with the OPPOSITE sign to `RISK_10BP`**: a `PAY` trade has positive `RISK_10BP` and a negative ladder (its main entry is negative; small entries next to it can have either sign). The sum of the ladder equals `-RISK_10BP` to better than 1e-5 relative |
| `CASH_TO_DATE` | currency | undiscounted cash paid to the holder in `(asof, as_of]`: payment date strictly after the market `asof` and on or before `as_of` (both ends refer to payment dates). 0 when `as_of == asof` |

`RISK_10BP` and `CONVEXITY_10BP` move the *par* curve; the `parallel_bp` scenario moves *zero* rates. They are different shocks: for a spot-start 5Y swap `NPV(parallel_bp=+1) - NPV` is about 5% larger than `RISK_10BP / 10`, because a 1bp zero shift moves par swap rates by slightly more than 1bp.
There is no attribution in zeta (no carry, roll or delta): assemble what you need from repeated `price()` calls with different `as_of` and `scenario`.

## 7. Response

```json
{"status": "OK", "market_id": "mkt-0001", "as_of": "2024-05-24", "latency_ms": 28,
 "results": [{"index": 0,
              "resolved": {"start": "2024-05-29", "end": "2029-05-29", "fixed_rate_bp": 425.0, "notional_mm": 10.0},
              "paid_on_as_of": 0.0,
              "measures": {"NPV": 25530.8794544163, "RISK_10BP": 44394.936306560325, "CONVEXITY_10BP": -252.64170357913827, "PAR_BP": 430.7414495911988, "CASH_TO_DATE": 0.0}}]}
```

`results` follows the order of `trades`; `measures` holds exactly the measures asked for. `resolved.start` and `resolved.end` are the adjusted first and last accrual dates; `resolved.fixed_rate_bp` is the strike used (the par rate for `"PAR"`).
`paid_on_as_of`: the holder-signed flow paid exactly on `as_of` (0.0 if none). An error is `{"status": "ERROR", "code": "Z...", "message": "...", "latency_ms": n}`.

## 8. Error codes

| code | meaning | typical cause and message |
|---|---|---|
| `Z101` | bad field | unknown key, wrong type, lower-case `"pay"`, `"par"`, negative notional, `as_of` outside the market, day-0 node. `trades[0].leg_fixed: expected 'PAY' or 'RECEIVE' (upper case), got 'pay'` |
| `Z204` | tenor not supported | any tenor unit but whole months/years: `{"tenor_weeks": 6}`, `{"tenor_years": 1.5}` |
| `Z301` | curve node grid unsorted | days not strictly ascending: `node 4 (day 365) does not follow node 3 (day 730)` |
| `Z412` | unknown market_id | never uploaded to this client, or the client was closed |
| `Z530` | required fixing missing | `trades[0]: no SOFR fixing dated 2024-05-20 in the market (...)`; the message names the first missing date |
| `Z500` | internal error | a bug in zeta; not caused by your request |

## 9. Statistics and batching

`client.stats` counts, per client: `requests` (every `price()` call, including failed ones), `markets_uploaded` (every `upload_market()` call), `trade_evaluations` (trades in requests that returned OK), `errors` (requests answered ERROR) and `latency_ms`, a deterministic simulated
service time: 120 ms per upload, and per request 25 ms plus 3 ms per trade (also returned as `latency_ms` in each response; nothing actually sleeps). Uploading is the expensive step: upload a market once and put all trades and all measures of one (`as_of`, `scenario`) into one request.

## 10. Conventions (fixed; nothing is configurable)

Currency USD. Spot: 2 business days. Business-day calendar: Monday-Friday except the market's holidays. Schedule dates roll modified following, generated backward from the end date with a short first period; no end-of-month rule. Fixed and floating legs both pay **annually**, 2 business days after the accrual end (adjusted).
Accrual ACT/360 (adjusted dates). The floating leg is SOFR compounded daily over the accrual period: each business day `D` accrues its fixing for `D` to the next business day at ACT/360, compounded. The floating amount of a period is `notional x (compounded growth - 1)`, the fixed amount `notional x rate x accrual`.
A fixed-rate `RECEIVE` trade's amounts are the negative of the `PAY` trade's.

## 11. Worked examples (markets as in section 2; all numbers printed by zeta 2.3.1)

1. `PAY` 5Y, 10mm, 425bp, spot start, `as_of 2024-05-24`, measures NPV, RISK_10BP, CONVEXITY_10BP, PAR_BP, CASH_TO_DATE: the response in section 7.
2. `RECEIVE` 18 months at `"PAR"`: `resolved.fixed_rate_bp` 479.31334591400577, `NPV` 0.0 (to 1e-10), `LADDER_10BP` risks 0.0015 (1M), 0.0 (3M), 118.49 (6M), 4463.47 (12M), 9666.74 (24M) and 0.0 for the seven longer pillars (positive: the receiver's `RISK_10BP` is negative).
3. Same 5Y payer with `"scenario": {"parallel_bp": 1.0}`: `NPV` 30184.58129807207 (unshocked 25530.88).
4. Payer started 2024-05-29 (explicit start), `as_of 2024-06-14`: date-space roll `NPV` 25610.2333507663; with `{"roll": "TENOR"}` `NPV` 33059.693616437115.
5. Payer started 2024-05-20, `as_of 2024-05-24`, market above: `Z530`, `no SOFR fixing dated 2024-05-20 in the market`.
6. A trade with `{"tenor_weeks": 6}` as `end`: `Z204`.

## 12. Limits

One curve, one currency, OIS only; markets are held in memory for the life of the client; at most 100 years of tenor; `as_of` must lie inside the curve.
