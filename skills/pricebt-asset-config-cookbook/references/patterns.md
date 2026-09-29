# Asset config patterns (full code)

Each pattern is a fragment of an asset config. Names such as `platform`, `client.market(...)` and `lib.Curve` stand for **your** library's calls. They are placeholders, not a real API. Injected names (`pricebt_date`, `market`, `kwargs`, `resolved`, `trade`, `trades`, `weights`, `pricebt_quantity`, `pricebt_csa`) are defined in [`docs/v2/ASSET_CONFIG_GUIDE.md`](../../../docs/v2/ASSET_CONFIG_GUIDE.md).

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

Give such an asset its **own** `market.key`, even when the curve also serves a swap asset. Assets that share a key must have byte-identical `imports`, `code` and `market.expr`.

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

For options: fold `buy_sell` into a signed notional in `resolve`, because pricebt never reads `buy_sell` itself (`tests/toylib/swaption.py` `resolve_swaption`).

## 6. Units and signs

Convert inside the function expression and comment every line:

```yaml
functions:
  npv:      {expr: 'client.price([trade], market, ["PV"])[0]["PV"]', unit: ccy}
  dv01:     {expr: '-client.price([trade], market, ["DV01"])[0]["DV01"]', unit: ccy_per_bp}   # vendor: receiver-positive -> pricebt: payer-positive
  par_rate: {expr: 'client.price([trade], market, ["PAR_PCT"])[0]["PAR_PCT"] * 100', unit: bp}  # vendor: percent -> pricebt: bp
```

Several functions on the same trade each call `price(...)`. Memoise one call per (market, trade) in `code:` if the platform is slow (pattern 13).

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
- Bucket keys are free labels. A 2-D point is one `';'`-joined string (`"1y;10y"`).

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

## 14. P&L explain functions (gamma, theta, year_fraction, cash_paid_to_date)

Four optional functions turn on `skills/pricebt-strategy-recipes/scripts/swap_pnl.py`'s
`swap_pnl_definition()`/`explain_table()` for an `IRSwap` config (`docs/v2/PNL_EXPLAIN_PLAN.md`
sections 2-2.5 have the exact conventions; `docs/v2/ASSET_CONFIG_GUIDE.md` has the contract). Toy
shape (`tests/assets/toy_usd_irs.yaml`):

```yaml
functions:
  gamma:             {expr: 'tr.gamma(market, trade)', unit: ccy_per_bp2}
  theta:             {expr: 'tr.theta(market, trade)', unit: ccy}                # ccy per YEAR, not per day
  year_fraction:     {expr: 'tr.year_fraction(market)', unit: decimal}           # intensive: never unit: number
  cash_paid_to_date: {expr: 'tr.cash_paid_to_date(market, trade)', unit: ccy}
risk_measures:
  IRGammaParallel: gamma
  IRTheta: theta
  YearFraction: year_fraction
  CashPaidToDate: cash_paid_to_date
```

ARBS-shaped (illustrative style, matching `configs/assets/usd_sofr_ois_interest_rate_swap.yaml`'s own
`code:`/`functions:` pattern -- not something T1 tests against real ARBS; the real functions are T2's
job):

```yaml
code: |
  def _shift(curve, bp):
      # library-specific: rebuild the SAME curve type with every zero node bumped by `bp`
      return curve.parallel_shift(bp * 1e-4)

  def gamma(market, trade):
      up, down = _shift(market.curve, 1.0), _shift(market.curve, -1.0)
      npv_up, npv_down, npv_mid = price(up, trade), price(down, trade), price(market.curve, trade)
      par_up, par_down = par_rate(up, trade), par_rate(down, trade)              # the trade's OWN measured move
      return (npv_up + npv_down - 2.0 * npv_mid) / ((par_up - par_down) / 2.0) ** 2

  def theta(market, trade):
      translated = market.curve.translate(days=1)                              # forward rates held fixed
      return (price(translated, trade) - price(market.curve, trade)) * 365.0

  def year_fraction(market):
      return (market.reference_date() - EPOCH).days / 365.0                    # intensive: no `trade` argument

  def cash_paid_to_date(market, trade):
      cashflows = remark(market, trade).cashflows(curves=market.handle())
      return sum(cf.amount for cf in cashflows if cf.payment_date <= market.reference_date())
functions:
  gamma:             {expr: 'gamma(market, trade)', unit: ccy_per_bp2}
  theta:             {expr: 'theta(market, trade)', unit: ccy}
  year_fraction:     {expr: 'year_fraction(market)', unit: decimal}
  cash_paid_to_date: {expr: 'cash_paid_to_date(market, trade)', unit: ccy}
```

**Every explain function must return `0.0` for a dead (matured) trade**, never `NaN`
(`pnl_explain()`'s own `cum_total += metric_pnl` has no guard, so one `NaN` poisons every later
cumulative value -- PNL_EXPLAIN_PLAN.md section 2.5).

**The traps** (each one is a test in `tests/skills/test_skill_swap_pnl.py`, and a checker row in
`skills/pricebt-verify-asset-config/scripts/check_asset.py`):

- **Half gamma.** `gamma` must be the second derivative of `npv` (a central difference of `npv`
  itself), never the difference of `dv01`/`pv01` across the same bump -- `(dv01(up)-dv01(down))/
  (par(up)-par(down))` looks plausible but is only ABOUT half the true convexity (`T-GAMMA-2`'s
  "half-gamma trap"; the exact ratio is tenor-dependent, not a clean 0.5 -- see
  `docs/v2/DECISIONS_LOG.md`).
- **Per-day theta, not per year.** `theta` is `ccy` per YEAR (`Δyear_fraction` is what converts it
  to a step's actual carry); a `θ` that is really "PV change over one calendar day" with no `* 365`
  looks like a plausible number but is 365x too small (`T-THETA-1`).
- **Extensive time.** `year_fraction` must be `unit: decimal` (intensive -- the SAME value
  regardless of position size), never `unit: number` (pricebt would then scale it by `quantity_`,
  so a 3x position gets 3x the "time", and `PNL_carry` ends up scaled by the position size SQUARED
  through `theta x Δyear_fraction`) (`T-YF`, `T-SCALE`).
- **Static-roll theta (the double-count).** `theta` must translate the curve (forward rates held
  fixed) -- ARBS's own `roll_curve`/`carry_bps_running` are a DIFFERENT, static-SHAPE measure. Using
  a rolled curve for `theta` books real roll-down twice: once correctly in `PNL_delta` (a genuine
  `Δpar`), and again in `PNL_carry` (`T-SLOPE-2`).
- **NaN on a dead trade.** A swap that matures between two marks has `dv01(t-1) != 0` but a naive
  `par_rate(t) = NaN`; every function above must guard the dead case and return `0.0` (or, for the
  market rate feeding `PNL_delta`, `fixed_rate * 1e4` so the maturity step's delta term correctly
  equals `-PV(t-1)`), never let the `NaN` reach `pnl_explain()`.
