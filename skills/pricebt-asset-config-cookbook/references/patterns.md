# Asset config patterns (full code)

Each pattern is a fragment of an asset config. Names such as `platform`, `client.market(...)` and `lib.Curve` stand for **your** library's calls. They are placeholders, not a real API. Injected names (`pricebt_date`, `market`, `kwargs`, `resolved`, `trade`, `trades`, `weights`, `pricebt_quantity`, `pricebt_csa`, `pricebt_bump_size`, `market_to`, ...) are defined in [`docs/v2/ASSET_CONFIG_GUIDE.md`](../../../docs/v2/ASSET_CONFIG_GUIDE.md).

Patterns 1-13 are config mechanics. Patterns 14-27 are the **measure recipes** for the IR contract (`src/pricebt/risk/contracts.py`). Each one is implemented, as tested code, in the contract templates of [`pricebt-connect-pricing-library`](../../pricebt-connect-pricing-library/SKILL.md) (`skills/pricebt-connect-pricing-library/references/config-template.yaml`, `config-template-swaption.yaml`, `config-template-bond.yaml`). The recipe names below (`own_rate_delta`, `theta_one_day`, ...) are those templates' helpers.

**Injected names are `eval` locals.** A comprehension or generator expression inside an `expr` (`'sum(f(market, x) for x in trade.legs)'`) has its own scope and raises `NameError: name 'market' is not defined`. Put the loop in a `code:` helper and pass `market` in.

## 1. A service library: a market handle by date

```yaml
imports: |
  import datetime as _dt
  import platform_sdk                      # your bank SDK, installed in the environment
code: |
  _CLIENT = platform_sdk.connect(env="UAT")          # ONE client per process (the namespace runs once)
  _FIRST, _LAST_SAFE = _dt.date(2019, 1, 2), _dt.date(2026, 8, 20)   # the safety envelope: the dates this config may serve

  def load_market(d):
      if d >= _dt.date.today() or not (_FIRST <= d <= _LAST_SAFE):
          return None                                  # outside the envelope: never ask the service
      try:
          m = _CLIENT.market(as_of=d.isoformat(), curve="USD.SOFR")
      except (platform_sdk.MarketClosed, platform_sdk.NoData):
          return None                                  # "no market" is None; anything else raises
      if m.as_of != d:
          raise ValueError(f"served {m.as_of} for {d}")   # validate the serve
      return m
market:
  expr: 'load_market(pricebt_date)'
```

## 2. An in-process library with local curve files

```yaml
imports: |
  import pandas as pd
  import mylib                                   # e.g. a QuantLib/rateslib-style library
code: |
  _DF = pd.read_parquet(r"<private>/curves/usd_sofr_dfs.parquet")   # index: date; columns: pillar dates; values: DFs
  _CACHE = {}
  def load_market(d):
      if d not in _DF.index:
          return None
      if d not in _CACHE:
          row = _DF.loc[d].dropna()
          _CACHE[d] = mylib.Curve(ref=d, nodes=dict(zip(pd.to_datetime(row.index).date, row.values)))
      return _CACHE[d]
market:
  expr: 'load_market(pricebt_date)'
```

pricebt already caches the market per (key, date, csa). `_CACHE` only matters if you also build markets outside pricebt.

## 3. A market with several parts (curve + vol)

```yaml
code: |
  from types import SimpleNamespace
  def load_market(d):
      curve, vol = load_curve(d), load_vol_cube(d)
      if curve is None or vol is None:
          return None                  # a missing part = no market (never a half market)
      return SimpleNamespace(curve=curve, vol=vol)
functions:
  npv: {expr: 'lib.swaption_pv(market.curve, market.vol, trade)', unit: ccy}
```

Give such an asset its **own** `market.key`, even when the curve also serves a swap asset. Assets that share a key must have byte-identical `imports`, `code` and `market.expr`. The swaption template builds every measure on such a market (patterns 16-21).

## 4. Pinning at the trade date (the resolve step)

```yaml
code: |
  def resolve_swap(m, kw):
      eff = m.spot_date()                                          # or parse kw.get("effective_date")
      probe = m.build_swap(start=eff, end=kw["termination_date"],  # '10y' -> absolute by the LIBRARY'S calendar
                           notional=float(kw["notional_amount"]), fixed_rate=None)   # None = strike at par
      k = parse_fixed_rate(kw.get("fixed_rate", "ATM"), par=m.par_rate(probe))        # decimal
      return {                                     # PLAIN data only: str/int/float/bool/None/date/tuple
          "effective_date": m.start_date(probe),   # absolute dates
          "termination_date": m.end_date(probe),
          "fixed_rate": k,
          "notional": float(kw["notional_amount"]) * signed_direction(kw["pay_or_receive"]),
      }
resolve:
  expr: 'resolve_swap(market, kwargs)'
```

- `resolve` sees the **trade-date** market (`pricebt_date` = the trade date). Its output is frozen for the life of the position.
- Test it by resolving on `d1`, then valuing on `d2`: the maturity must not move (`check_asset.py` does this).

## 5. gs kwargs grammar

```python
import re
_ATM = re.compile(r"^\s*(atm|a)\s*(?:([+-])\s*(\d+(?:\.\d+)?))?\s*$", re.I)   # 'ATM', 'ATM+25', 'a-100' (offsets in bp)

def parse_fixed_rate(fr, par):
    mm = _ATM.match(fr) if isinstance(fr, str) else None
    if mm:
        bp = float(mm.group(3) or 0.0) * (-1.0 if mm.group(2) == "-" else 1.0)
        return par + bp / 1e4               # decimal
    if isinstance(fr, str):
        raise ValueError(f"unsupported fixed_rate {fr!r}")   # '=solvefor(...)' etc.: reject loudly
    return float(fr)                        # gs convention: decimal (0.0325 = 3.25%)

def signed_direction(por):
    s = str(getattr(por, "value", por)).strip().lower()      # pricebt PayReceive.Receive has value 'Rec'
    if s == "pay": return 1.0
    if s in ("rec", "receive", "receiver"): return -1.0
    raise ValueError(f"pay_or_receive must be Pay or Receive, got {por!r}")
```

For options and bonds: fold `buy_sell` into a signed notional or face in `resolve`, because pricebt never reads `buy_sell` itself (patterns 26 and 27).

## 6. Units and signs

Convert inside the function expression and comment every line:

```yaml
functions:
  npv:      {expr: 'client.price([trade], market, ["PV"])[0]["PV"]', unit: ccy}
  dv01:     {expr: '-client.price([trade], market, ["DV01"])[0]["DV01"]', unit: ccy_per_bp}   # vendor: receiver-positive -> pricebt: payer-positive
  par_rate: {expr: 'client.price([trade], market, ["PAR_PCT"])[0]["PAR_PCT"] * 100', unit: bp}  # vendor: percent -> pricebt: bp
```

Several functions on the same trade each call `price(...)`. Memoise one call per (market, `pricebt_date`, trade) in `code:` if the platform is slow (pattern 13; the Meridian example's `_risk`). Vol, gamma, theta and bond conversions are in pattern 18 and in `skills/pricebt-connect-pricing-library/references/convention-conversions.md`.

## 7. Seasoned trades and fixings

With `build_on: resolve_date` the trade object is built once, on the trade-date market. A function needing *today's* fixings rebuilds it on today's market:

```python
def remark(m, t):
    memo = m.__dict__.setdefault("_remark", {})      # memo dies with the market object
    hit = memo.get(id(t))
    if hit is None or hit[0] is not t:
        hit = memo[id(t)] = (t, m.rebuild(t))         # same economic swap, this market's fixings
    return hit[1]
```

If your library's trade object captures its curve at build time (rateslib-style `IRS(curves=...)`), use `build_on: each_market` instead.

## 8. Portfolio functions: batching and ladders

```yaml
portfolio_functions:
  delta_ladder:
    expr: 'ladder(market, trades, weights, ("2Y","5Y","10Y","30Y"))'
    unit: ccy_per_bp
    returns: buckets
    labels: {mkt_type: IR, mkt_asset: USD-SOFR, mkt_class: OIS}
code: |
  def ladder(m, trades, weights, tenors):
      res = client.price(list(trades), m, ["BUCKET_DV01"])       # ONE batched call for the whole group
      out = {t: 0.0 for t in tenors}
      for r, w in zip(res, weights):                            # weights = position quantities; apply ONCE
          for k, v in r["BUCKET_DV01"].items():
              out[k.split(":")[1]] = out.get(k.split(":")[1], 0.0) - v * w   # vendor receiver-positive -> payer-positive
      return out
```

- pricebt calls a single trade with `weights=[1.0]` and then multiplies by quantity itself. The group call passes the quantities and is not multiplied again.
- Bucket keys are free labels. A 2-D point is one `';'`-joined string; the `IRVega` cube uses gs's order, `"<tail>;<expiry>"` (`"10Y;1Y"`, pattern 21).
- A ladder over several curves, or `PnlExplain` rows, returns a list of row dicts instead (pattern 22).

## 9. Attributes and size

```yaml
attributes:
  termination_date: 'resolved["termination_date"]'               # AddTradeAction(swap, 'termination_date') exit
  notional_amount:  'abs(resolved["notional"]) * pricebt_quantity'   # signed by quantity: RebalanceAction nets correctly
size_attribute: notional_amount
```

## 10. CSA routing

```yaml
market:
  expr: 'load_market(pricebt_date, pricebt_csa)'   # pricebt_csa: HedgeAction.csa_term during hedge resolution, else run_backtest(csa_term)
code: |
  def load_market(d, csa):
      curve = "USD.SOFR" if csa in (None, "USD-SOFR") else CSA_TO_CURVE[csa]
      ...
```

## 11–12. Multi-currency and FX

- **Currencies.** Write one config per currency (`match: {notional_currency: EUR}`, `currency: EUR`), with each asset's functions returning its local currency.
- **FX config.** Register one with `PricebtSession.use(assets=[...], fx="<private>/fx.yaml")`, and run with `run_backtest(..., result_ccy="USD")`. A template is in `configs/fx/README.md`, and the tested example is `tests/assets/toy_fx.yaml`.

## 13. Performance

- Measure with the checker, which prints its evaluation counts and timings.
- One client per process. One market per date (pricebt caches it).
- One batched call per group (portfolio functions).
- Memoise per market for derived objects (risk curves, calibrations, remarks).
- `build_on: resolve_date` when rebuilding is expensive and your functions re-mark explicitly.

---

# Measure recipes (the IR contract)

## 14. The measure contract: map or declare

An `IRSwap`, `IRSwaption` or `Bond` config loads only if every (measure, form) of its class's
contract is either mapped with an allowed unit or declared (DEV-I11). Print the contract with
`python -c "from pricebt.risk import contracts; print(*contracts.contract_for('Bond'), sep='\n')"`.

```yaml
risk_measures:
  IRVega: {scalar: vega, bucketed: vega_cube}                    # mapped: the unit must be ccy_per_bp
unsupported_measures:
  IRAnnualATMImpliedVol: "yourlib's surface is quoted by strike only; no ATM-forward lookup"   # every form
  IRDelta: {bucketed: "yourlib has no key-rate bump"}                                            # one form
```

- **Units by kind.** `ccy` for Price, Theta and Annuity; `ccy_per_bp` for first-order sensitivities;
  `ccy_per_bp2` for second-order ones. Rates and vols are `bp`, `pct` or `decimal` and must be
  intensive (the default for those units). `ExpiryInYears` and `ProbabilityOfExercise` take
  `decimal`: `number` is extensive by default, so it fails "must be intensive". Cashflows is
  `returns: frame` (pattern 23).
- **One error lists every gap**, and ends with a paste-ready block. Paste it and replace each
  `TODO` reason. Any non-empty reason loads, so a leftover `TODO` loads too: the checker skill
  flags it.
- **Preset keys count.** `IRDeltaParallel: dv01` counts as the `IRDelta` scalar and also prices
  `IRDelta(aggregation_level='Type')`. Declare the **base** measure, never a preset: a declared
  preset loads with a warning and declares nothing.
- **A mapping wins over a declaration** of the same form, with a `UserWarning` about a stale
  declaration. Remove the declaration.
- Measure names outside the contract (a custom `IRTheta`, say) are unrestricted. Classes without a
  contract (`FXOption`, `EqOption`, `InflationSwap`, `Cash`, `FXForward`, `ConfigInstrument`)
  need only `Price`.

## 15. Zero by convention (swaps and bonds)

gs answers every IR measure on every IR instrument, and a swap's vega really is 0. On swaps and
bonds, **map** these (do not declare them):

```yaml
functions:
  zero_per_bp:  {expr: '0.0', unit: ccy_per_bp}    # IRVega scalar; IRBasis and IRXccyDelta on one curve, one currency
  zero_per_bp2: {expr: '0.0', unit: ccy_per_bp2}   # IRVanna, IRVolga
  zero_vol:     {expr: '0.0', unit: bp}            # IRAnnualImpliedVol, IRAnnualATMImpliedVol, IRDailyImpliedVol
portfolio_functions:
  vega_cube: {expr: '{}', unit: ccy_per_bp, returns: buckets, labels: {mkt_type: IR VOL}}
```

Why: `pnl_explain` and `aggregate` need every held instrument to answer every measure of a
definition. A swaption book hedged with swaps under `swaption_pnl_definition` asks the swaps for
vega. Declaring these unsupported is allowed, but it breaks mixed books with vol attribution
(R2-8). `IRBasis` and `IRXccyDelta` are zero only if your library really has one curve and one
currency. A multi-curve library maps the real basis delta (shift the projection-minus-discount
spread). Keep the zero vol's unit equal to your swaption configs' vol unit.

## 16. Own-rate IRDelta: the total derivative from curve bumps

The contract's `IRDelta` scalar is the total derivative of PV with respect to the instrument's own
quoted rate r (`IRFwdRate`), along your library's parallel curve shift, with the own-strike vol
held (DEV-I12). Template helper `own_rate_delta`, with h = 1bp and r in bp:

`delta = [PV(+h) - PV(-h)] / [r(+h) - r(-h)]`

It needs three primitives: a parallel shift, a PV and the own rate. Any library that can shift a
curve can produce it. How it relates to what your library calls a DV01:

| Your library's measure | Is it the IRDelta scalar? |
|---|---|
| PV change per +1bp of the trade's **own** par quote | yes |
| a curve DV01: PV change per +1bp parallel shift of zero rates or of all par quotes | only if the own rate moves exactly 1bp with the curve; the recipe divides by the actual move |
| an annuity pv01 (N·A x 1e-4) | only at the money: off the money it misses `N·(F-K)·dA/dr` |
| a bond's yield DV01, dPV/dy | yes (a bond's own rate is its yield) |

- **Sign:** the PV change per +1bp. A payer swap and a payer swaption are > 0, a long bond is < 0.
  A receiver-positive DV01 must be negated (pattern 6).
- **Additivity:** own-rate deltas of instruments quoted on different rates are not strictly
  additive. `IRDeltaParallel` summed across swaps and swaptions (hedges, `aggregate`, risk
  triggers) is approximate; it is exact for a hedge of the same type (R2-2). The bucketed ladder
  sums to the parallel curve delta, not to the scalar.
- **Verify:** with a 0.5bp and a 2bp bump the recipe agrees to about 1e-4. For a swap at the
  money it equals the annuity pv01.

## 17. IRGammaParallel: the chain rule (the half-gamma trap)

On the same bumps as pattern 16, with Δ = (n₊ - n₋)/(r₊ - r₋) (template helper `own_rate_gamma`):

`Γ = [n₊ + n₋ - 2·n₀ - Δ·(r₊ + r₋ - 2·r₀)] / ((r₊ - r₋)/2)²`, per bp² of r.

The second term removes the curvature of r(shift) itself, so Γ is d²PV/dr², not d²PV/ds².

- **Never** map the change of an annuity pv01 per bp, or `d(dv01)/d(shift)`. For a swap at the
  money that is **half** the gamma: PV = N·(F - K)·A gives d²PV/dF² = 2·N·dA/dF at F = K.
  Known answer at the money: Γ = 2 x (change in pv01) / (change in par rate).
- **A bond:** `yield_gamma` = P(y + h) + P(y - h) - 2·P(y) per bp², which equals convexity x dirty
  PV x 1e-8.
- **Native gammas:** divide a "per 1% move" gamma by 1e4; multiply a per-unit-rate² gamma by 1e-8.
- **`IRGamma` (bucketed)** is the diagonal only (DEV-I13): per pillar P(+hᵢ) + P(-hᵢ) - 2·P
  (`diagonal_gamma_ladder`). gs's 12-column cross-gamma is not produced.

## 18. Vol units, normal vs lognormal, and vega

The contract's vols are **normal** (Bachelier) vols. The shipped configs report the levels in bp
(80.0). gs's `IRAnnualImpliedVol` returns a decimal (0.0080) despite its "(in percent)"
docstring, so a ported notebook's `* 10000` must go.

- **Levels:** multiply a decimal by 1e4, or a percent by 100, to get bp. `IRDailyImpliedVol` is the
  annual vol / sqrt(252), in the same unit.
- **A lognormal or SABR library:** the normal vol is not σ_LN times a constant. Exact: the
  Bachelier-implied vol of the library's own premium,
  `bachelier_implied_vol(PV / annuity, F, K, T, is_payer)` (swaption template). PV and annuity are
  both holder-signed, so their ratio is the unit premium. At the money only, σ_N ≈ σ_LN·F; away
  from it, to leading order, σ_N ≈ σ_LN·(F - K)/ln(F/K).
- **Vega per bp of NORMAL vol** (`IRVega`): best is a normal-vol bump,
  `[PV(σ + 1bp) - PV(σ - 1bp)] / 2` (`vol_bump_vega`). A library that can only bump lognormal vols
  should bump σ_LN by h/(dσ_N/dσ_LN), about h/F at the money, so the normal vol moves by h. To
  convert a native lognormal vega per 1%, multiply by 0.01/F at the money (0.01·ln(F/K)/(F - K) to
  leading order), and state the approximation in `description:`. A vega per 1% normal: divide by
  100. A vega per unit vol: multiply by 1e-4.
- **Sticky strike:** `lib_shift` must hold each option's **normal** vol. A lognormal surface held
  fixed under a curve shift moves the normal vol (σ_N ≈ σ_LN·F), which leaks vega into the delta.
- **Verify:** an ATM normal vega is about N·A·sqrt(T)·0.3989·1e-4 (about 330 per 1mm 1y10y). The
  vol level must reprice the option through Bachelier.

## 19. Vanna and volga by vol bumps

- `IRVanna` = d(IRDelta scalar)/dσ_N, per bp x bp: `[Δ(σ + h) - Δ(σ - h)] / 2`, with Δ the own-rate
  delta of pattern 16 (`vol_bump_vanna`).
- `IRVolga` = d²PV/dσ_N², per bp²: `PV(σ + h) + PV(σ - h) - 2·PV(σ)` (`vol_bump_volga`).
- Both are finite-difference measures in pricebt (DEV-I9, gs 2.1.17). Request
  `IRVanna(aggregation_level='Type')`: a bare `IRVanna` asks for a bucketed form the contract does
  not have, and the error says so.
- Swaps and bonds: 0.0 (pattern 15). After expiry: exactly 0.0.
- `ir_pnl_definition` uses them: `PNL_vanna` = IRVanna x ΔIRFwdRate x ΔIRAnnualImpliedVol, and
  `PNL_volga` is second order in the vol.

## 20. Theta: a translated curve, and a bond's constant yield

The contract (DEV-I15) is one calendar day of carry, total return, with the own rate and vol held:
`Theta = PV(t+1d) + flows PV drops in (t, t+1d] - PV(t)`, in ccy per day.

- **Swaps and swaptions:** revalue on the curve **translated** one day, `DF'(x) = DF(x) / DF(t+1d)`,
  so every forward is unchanged and the valuation date is t+1d. Hold the option's vol at today's
  value; T shrinks by 1/365 (`theta_one_day`, primitive `lib_translate`). QuantLib-style: an
  implied term structure at t+1d. rateslib-style: `Curve.translate`, not `roll`.
- **Not Theta:** a curve rebuilt at t+1 from today's zero rates (that is roll-down: the forwards
  move); a per-year number (`IRTheta` = 365 x `Theta`); a per-business-day number (a Friday's
  theta is still one calendar day); a theta without the day's coupon.
- **Bonds:** the same yield one day later: `lib_pv_at_yield(t, y, d+1) + coupons paid in (d, d+1]
  - PV(d)`. That is about PV·y/365, and exactly PV·(exp(y/365) - 1) for a continuously compounded
  yield between coupons.
- **The cash term** matters only when Price drops paid flows (pattern 23). For a total-return
  Price, Cashflows is empty and the term is 0.
- `ir_pnl_definition` books theta as `Theta x ΔExpiryInYears x -365`. That factor is right only for
  a per-day Theta and an `ExpiryInYears` of `max(final - t, 0).days / 365` (DEV-I17: a swap's or
  bond's final date, a swaption's expiry; helper `years_to`).

## 21. Ladders and the vega cube

- **Delta ladder** (`IRDelta` bucketed): central key-rate bumps of each pillar
  (`key_rate_ladder`). Keys are plain tenors, with the scalar's sign and unit. It sums to the
  **parallel** curve delta (about `IRDiscountDeltaParallel` on one curve), not to the own-rate
  scalar. For a native bucketed delta, map its keys (pattern 8), flip and scale it like the scalar,
  and raise on an unknown pillar.
- **Gamma ladder** (`IRGamma`, bucketed only): the diagonal (pattern 17).
- **Vega cube** (`IRVega` bucketed): one row per (expiry, tail), `mkt_point = "<tail>;<expiry>"`
  in gs's order (`"10Y;1Y"` is the 1y expiry into a 10y tail), `labels: {mkt_type: IR VOL}`. The
  recipe puts each trade's vega on the pillars nearest its tail and its time to expiry
  (`vega_by_expiry_tail`). For a native bucketed vega, build the key as `f"{tail};{expiry}"`: the
  reverse order silently mislabels every row.
- **Swaps and bonds:** the cube is `{}`.
- **Batching:** a portfolio function gets all of this asset's trades on one date. Make one library
  call per bump for the whole book.

## 22. Per-row buckets: several curves, and PnlExplain

A `returns: buckets` portfolio function may return a **list of row dicts** instead of
`{mkt_point: value}` (R2-14). The keys come from `mkt_type, mkt_asset, mkt_class, mkt_point,
mkt_quoting_style, value`, and `value` is required. The function's `labels:` fill the missing
coordinates, and quantity and FX scale only `value`. A ladder over several curves:

```python
def multi_curve_ladder(m, trades, weights):
    rows = []
    for curve in ("USD-SOFR", "USD-FF"):                           # your projection / discount curves
        for p in lib_pillars(m, curve):
            up, dn = lib_shift_pillar(m, curve, p, H), lib_shift_pillar(m, curve, p, -H)
            rows.append({"mkt_asset": curve, "mkt_point": p,
                         "value": sum(w * (pv(up, t) - pv(dn, t)) / 2.0 for t, w in zip(trades, weights))})
    return rows
```

**PnlExplain** is optional (it is not in the contract; gs 030007). `PnlExplain(CloseMarket(date=to))`
maps to a buckets portfolio function that also receives `market_to` (the target date's market) and
`pricebt_to_date`. Its expression must name one of them (DEV-M2). It is a full revaluation by risk
factor, with no time component:

```yaml
portfolio_functions:
  pnl_explain: {expr: 'explain_by_factor(market, market_to, trades, weights, pricebt_date)', unit: ccy, returns: buckets}
risk_measures:
  PnlExplain: pnl_explain
```

```python
def explain_by_factor(m0, m1, trades, weights, d):
    """IR = curves moved (vols held), IR VOL = vols moved (curves held), CROSSES = the rest."""
    parts, total = {"IR": 0.0, "IR VOL": 0.0}, 0.0
    for t, w in zip(trades, weights):
        base = pv_on(m0, t, d)                                      # value every leg on the pricing date d
        parts["IR"] += w * (pv_on(lib_with_curves(m0, m1), t, d) - base)   # your primitive: m0 with m1's curves
        parts["IR VOL"] += w * (pv_on(lib_with_vols(m0, m1), t, d) - base)
        total += w * (pv_on(m1, t, d) - base)
    return [{"mkt_type": k, "value": v} for k, v in parts.items()] + [{"mkt_type": "CROSSES", "value": total - sum(parts.values())}]
```

Value on the pricing date, not on the market's own date, so no time passes. gs 030007 adds the time
part separately. The runnable references are `pnl_explain` in `tests/toylib/swaption.py` and
`explain_rows` in `tests/toylib/irrisk.py`.

## 23. Frames: Cashflows, and when Price drops paid flows

```yaml
functions:
  cashflows: {expr: 'lib_cashflows(market, trade)', unit: ccy, returns: frame, scale_columns: [payment_amount]}
risk_measures:
  Cashflows: cashflows
```

- **Shape.** `returns: frame` is allowed on `functions:` entries only. It returns a DataFrame or a
  list of row dicts, and `[]` is fine (pricebt supplies the required columns). The required
  columns are `payment_date, payment_amount, currency, payment_type`; the other gs columns
  (`accrual_start_date`, `notional`, `rate`, `discount_factor`, ...) are optional.
  `scale_columns` must include `payment_amount`. Only those columns are multiplied by the quantity,
  and an extensive frame without `scale_columns` fails to load. A frame is never FX-converted.
- **Semantics (R2-6):** the flows that Price still INCLUDES and will drop on their payment date
  (`payment_date` > pricing date), holder-signed. Both Price conventions are legal; say which one
  you use in `description:`:

  | Price convention | Cashflows | Theta's cash term | Backtest Total |
  |---|---|---|---|
  | drops each flow on its payment date (most libraries; the toy bond) | the future flows | the flow paid tomorrow | falls by the coupon on its payment date: pricebt books no coupon cash (gs parity). Use `BackTest.pnl_explain_table()`: `economic_pnl = actual_pnl + cashflow_pnl` |
  | total return: never drops a paid flow (the toy swap) | empty | 0 | continuous |

- **Tables are skipped by the summaries.** `result_summary`, `risk_summary`, `summary_stats` and
  `strategy_as_time_series` skip frames, which stay in `backtest.results[d]` (DEV-R11).
  `PortfolioRiskResult.aggregate()` concatenates them, and `to_frame(values='value')` leaves them
  out.

## 24. Finite-difference parameters (bump size, method)

A gs request such as `IRDelta(aggregation_level='Type', bump_size=5, finite_difference_method='Up')`
reaches your function as `pricebt_bump_size`, `pricebt_finite_difference_method`,
`pricebt_scale_factor` and `pricebt_local_curve` (None when unset). It does so **only if the
function's own expression names the variable**. A helper that reads a global does not count.
Otherwise the request raises `NotSupportedError: ... sets bump_size; function 'f' does not
reference pricebt_bump_size`. `mkt_marking_options` always raises (DEV-I8, narrowed by DEV-I10).

```yaml
functions:
  delta: {expr: 'own_rate_delta(market, trade, pricebt_bump_size, pricebt_finite_difference_method)', unit: ccy_per_bp}
```

- **You define the units.** gs never documented `bump_size`'s unit; the templates read it as bp
  (gs's default is a centred 1bp). `finite_difference_method` arrives as the
  `FiniteDifferenceMethod` enum, which is a `str` (`Up`, `Down`, `Centered`,
  `CenteredSecondOrder`); compare `getattr(x, "value", x)`. Coerce `bump_size` with `float()`,
  because spec files pass strings.
- **Name a parameter only if the function honours it.** An unreferenced parameter that raises is
  the honest answer: the request is refused, never silently ignored.
- **Each parameter set has its own cache and group keys**, so `IRDelta(bump_size=1)` and
  `IRDelta(bump_size=10)` never share a value.
- **A bucketed request checks the bucketed function.** `IRDelta(bump_size=2)` with no
  `aggregation_level` needs the ladder's expression to name the variable.
- **Verify:** `bump_size=1` equals the default exactly; 10 stays within a linear band; `Up` and
  `Centered` differ by about ½·Γ·h. `tests/skills/test_skill_connect_example.py` checks this on
  the swap template.

## 25. Dead instruments (matured, expired, fully paid)

- **Every sensitivity is exactly `0.0`**, never NaN. `pnl_explain` skips a risk that is exactly 0
  and never guards NaN, so one NaN poisons every later cumulative value.
- **Every level stays finite** (`IRFwdRate`, `IRSpotRate`, the vol levels, `ExpiryInYears`), and
  continuous with its last live value wherever the previous date had a non-zero sensitivity
  (R2-7). `Cashflows` is empty.
- **A swaption at and after expiry is physically settled.** A leg in the money at
  `expiration_date` IS the underlying swap (value and sensitivities); an unexercised leg is 0. The
  vol levels stay at their value at expiry: read it from `lib_market(expiration_date)` if your
  surface drops expired options. `IRFwdRate` is the live par rate of the underlying.
- **A matured swap or bond** has PV 0, delta 0, `IRFwdRate` at its last live value (or the par rate
  of a one-day swap, as the toy does), and `ExpiryInYears` 0.
- **The loader does not check these rules.** Test them by pricing one day after expiry or maturity.
  The templates' recipes return 0.0 where a bumped rate no longer moves.

## 26. Swaption resolve: buy_sell, Straddle, strike grammar

- **Fold `buy_sell`.** pricebt never reads `buy_sell` or `pay_or_receive` itself; scaling is a
  signed `quantity_` (DEV-I1). `resolve` folds `buy_sell x sign(notional_amount)` into one signed
  notional (bought > 0), so `Sell` = -`Buy` exactly for every extensive measure.
- **`pay_or_receive` is the option TYPE, not the position.** Pay or Payer is a payer; Rec,
  Receive or Receiver is a receiver (pricebt's `PayReceive.Receive` has the value `'Rec'`). A
  `Straddle`, gs's default, is payer + receiver. Price it as a tuple of legs (`swaption_trade`),
  or reject it in `resolve` with a clear error: at resolve time, never later.
- **Strike grammar.** `'ATM'` and `'ATMF'` are the forward par rate at the trade date. `'A+25'` and
  `'ATM-50'` are offsets in bp. A number is a decimal. `'=solvefor(...)'`, `'25d'` and `'10000/pv'`
  are GS server grammar (DEV-I6): raise, or implement the solve yourself in `resolve` (for example,
  bisect the strike against `lib_pv`).
- **Pin at the trade date:** `expiration_date`, `termination_date` and the strike. Expose
  `attributes: {expiration_date: ...}` so `AddTradeAction(option, 'expiration_date')` exits on it.
- **Market:** the swaption gets its own `market.key` for curves + vols (pattern 3).

## 27. Bond resolve: size, buy_sell, identifier

- **Fields.** gs `Bond` has `buy_sell, identifier, identifier_type, size, settlement_date,
  settlement_currency`: no `notional_amount`, no tenor, no strike. Use
  `match: {settlement_currency: USD}`.
- **`resolve`** looks up the identifier's static data as plain values (point in time; raise on an
  unknown identifier), and folds `buy_sell x sign(size)` into a signed face, long > 0
  (`resolve_bond`).
- **The engine sizes by attributes.** Give it
  `notional_amount: 'abs(resolved["face"]) * pricebt_quantity'` (the default
  `ScaledTransactionModel` reads it), `termination_date: 'resolved["maturity"]'`, and
  `size_attribute: notional_amount`. pricebt scales a Bond by `quantity_`, although gs's
  `Bond.scale()` raises (DEV-I1).
- **The own rate is the yield to maturity** (DEV-I12). `IRFwdRate` = `IRSpotRate` = the yield.
  The `IRDelta` scalar = `LightningDV01` = dPV/dy by yield bumps (long < 0). `IRGammaParallel` =
  d²PV/dy² (pattern 17). Theta is at a constant yield (pattern 20).
- **Price** is the holder-signed DIRTY PV of the unpaid flows (dirty price / 100 x face). Clean
  price and accrued interest are extra functions if you want them as series; they are not in the
  contract.
- **Quoted prices only.** A bond marked only by a quoted price has no curve to shift. Compute its
  Z-spread once and shift the curve holding it, or declare `IRDiscountDeltaParallel`, the `IRDelta`
  ladder and `IRGamma` with that reason.
