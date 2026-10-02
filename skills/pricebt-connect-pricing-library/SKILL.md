---
name: pricebt-connect-pricing-library
description: Fast path from "I have a pricing/data library" (a bank platform such as a Citi Datapoint/DP-style or JPM Athena-style service, an in-house library, a QuantLib- or rateslib-style object library, a REST analytics service, a bond-analytics package) to a checked pricebt asset config for an IRSwap, IRSwaption or Bond that answers the whole IR measure contract, and a running backtest. Use it to connect a library, add a swap, swaption or bond asset, work out which of your library's calls give each measure pricebt needs (delta, gamma, vega, vanna, volga, theta, rates, vols, annuity, cashflows, ladders, par spread, forward price, CRIF) in which unit and sign, and derive the ones it lacks by bump-and-reprice (swap and swaption configs must map every measure; only a Bond may declare one unsupported).
---

# Connect a pricing library to pricebt

pricebt has no pricing and no market data of its own. Every number comes from one YAML **asset
config** per asset, whose Python strings call *your* library
([`docs/v2/ASSET_CONFIG_GUIDE.md`](../../docs/v2/ASSET_CONFIG_GUIDE.md)). For `IRSwap`, `IRSwaption`
and `Bond` the config must also satisfy a **measure contract** (`src/pricebt/risk/contracts.py`,
DEV-I11 amended, DEV-I19). It lists every gs IR measure the class supports (`Price`, the own-rate
`IRDelta` and `IRGammaParallel`, `IRVega`/`IRVanna`/`IRVolga`, rate and vol levels, `Theta`,
`Annuity`, `Cashflows`, ladders; for swaps and swaptions also `ParSpread`, `FairPremium`,
`ForwardPrice`, `PremiumCents`, `LocalAnnuityInCents`, `CompoundedFixedRate`, `CRIFIRCurve` and
`PnlExplain`). An **`IRSwap` or `IRSwaption` config must map every one** with the contract's unit
([`docs/v2/IR_STRICT_CONTRACT.md`](../../docs/v2/IR_STRICT_CONTRACT.md)): a gap or a declaration fails
at load, and the error lists every gap and ends with a paste-ready **mapping skeleton**. A `Bond`
config may still declare a measure under `unsupported_measures:` with an honest reason. This skill
assumes you know your own library well. It tells you what pricebt needs for each measure, in your
library's terms.

## When to use / not use

- **Use** when you connect a library for the first time, add a swap, swaption or bond asset, map a
  measure your config does not answer yet, or when a config's numbers look wrong (units, signs,
  drift).
- **Do not use** to design a strategy (`pricebt-strategy-intake`), or to audit a finished config
  (`pricebt-verify-asset-config`, which step 7 calls).

## Inputs and outputs

- **Inputs:** your library, importable in the same Python process, and one business date it has
  data for. The commands use 2024-01-02 and 2024-04-02; change them if your history differs.
- **Outputs:** a config under `configs/assets/` that loads with no warning, a filled capability
  worksheet (one line per contract measure: native, recipe, zero by convention, or -- Bond only --
  declared with a reason),
  a passing checker run, a 3-month smoke backtest, and a test.

## Procedure

All commands run from the repository root in PowerShell. Set the environment first:

```powershell
$cfg = "configs/assets/<asset>.yaml"     # your new config
$lib = "<dir containing your package>"   # "" if it is pip-installed
$env:PYTHONPATH = "src;tests;$lib"; $env:CFG = $cfg
```

### 1. Discover what your library can do, measure by measure

Print the contract for your instrument, then fill the capability worksheet in
[`references/discovery-questionnaire.md`](references/discovery-questionnaire.md) §9. For each
measure, write down: is it native (which call)? In what unit and sign? If it is not native, which
primitives derive it? How will you verify it? Sections 1-8 of that file cover the mechanics:
session, market by date, holidays, trade construction, codes and batching. Sections 10-12 cover
bump controls, swaptions and bonds. Answer with evidence (a docstring, the library's own tests, a
REPL transcript), never from memory.

```powershell
python -c "from pricebt.risk import contracts; [print(r.measure, r.forms, '-', r.doc) for r in contracts.contract_for('IRSwap')]"
python skills/pricebt-risk-measures/scripts/measures.py contract IRSwap   # the same, as a table with units
```

### 2. Copy the template for your instrument

| Instrument | Template (loads blank; maps the whole contract) | Runnable reference (toy library) |
|---|---|---|
| `IRSwap` | [`references/config-template.yaml`](references/config-template.yaml) | `tests/assets/toy_usd_irs_full.yaml` on `tests/toylib/irrisk.py` |
| `IRSwaption` | [`references/config-template-swaption.yaml`](references/config-template-swaption.yaml) | `tests/assets/toy_usd_swaption.yaml` on `tests/toylib/swaption.py` |
| `Bond` | [`references/config-template-bond.yaml`](references/config-template-bond.yaml) | `tests/assets/toy_usd_bond.yaml` on `tests/toylib/bond.py` |

Each template has two layers in `code:`. **Primitives** (`lib_*`) are the only calls into your
library. Each is a stub that raises `NotImplementedError("TODO ...")` and whose docstring says what
pricebt needs back. **Recipes** derive every contract measure from the primitives: own-rate delta
and chain-rule gamma, a discount-only bump, translated-curve theta, vol-bump vega, vanna and volga,
key-rate and diagonal-gamma ladders, the `"<tail>;<expiry>"` vega cube, and `ExpiryInYears` from the
resolved dates. `tests/skills/test_skill_connect_example.py` fills the primitives with the toy
library and checks every measure against the toy config, so the recipes are tested code. Change
`asset:`, `description:`, `match:`, `currency:` and `market.key:`.

### 3. Fill the primitives with your library's calls

| Primitive | What pricebt needs back | Typical call by library shape (examples, not APIs) |
|---|---|---|
| `lib_market(d)` | the close of `d` (curves, and vols for options) as ONE object, or `None` on a day with no data | service: `client.market(as_of=...)` in a narrow `try/except`; object library: build or look up the day's curve |
| `lib_pv(m, t)` | holder-signed PV in `currency:` | service: `price(trades, ["PV"])`; QuantLib-style: `swap.NPV()` after setting the engine's curve handle |
| `lib_own_rate` / `lib_fwd_rate` / `lib_yield` | the own rate as a DECIMAL: swap par rate, forward swap rate, yield to maturity | `fairRate()`-style par rate (convert a percent quote); a bond's yield in the SAME convention as `lib_pv_at_yield` |
| `lib_shift(m, h)` | every curve shifted in parallel by `h` (decimal); for options, normal vols held | a spread over the curve (QuantLib-style zero-spreaded term structure; rateslib-style `Curve.shift`), or a bump of the par quotes and a re-solve |
| `lib_shift_discount`, `lib_shift_pillar` | only the discount curve, or only one pillar, shifted | the same mechanism on one curve, or on one input quote |
| `lib_translate(m, days)` | the market `days` later with **forwards fixed** (and option vols held) | QuantLib-style implied term structure at the new reference date; rateslib-style `Curve.translate`, **not** `roll` |
| `lib_vol_shift(m, h)` (swaption) | every **normal** vol shifted by `h` (1bp = 1e-4) | a normal surface: add `h`; a lognormal surface: see cookbook pattern 18 |
| `lib_annuity`, `lib_cashflows`, `lib_spot_rate` | N·A in ccy, **payer-positive** (receive-fixed < 0); the flows the PV will still drop; the spot-starting par rate | `−1e4 × fixedLegBPS` (QuantLib-style BPS is negative for a payer); the leg schedule; a probe swap |
| `lib_bond_static`, `lib_pv_at_yield` (bond) | plain static data; the dirty PV at a given yield and date | the security master; price-from-yield |
| `lib_discount_factor`, `lib_fixed_frequency`, `lib_at` (+ swaption `lib_premium_date`, `lib_with_vols`) | DF to a date; fixed payments per year; the market seen from another date with no time passing (and a market with another's vols) | the curve's DF call; the trade's schedule; a valuation-date override that keeps the zero rates |

Convert every unit and sign **once, inside the primitive**, with a `# vendor: X -> pricebt: Y`
comment. The table is in [`references/convention-conversions.md`](references/convention-conversions.md).

### 4. Keep each recipe, or map your native measure

Keep the recipe where your library has no such measure. Where it has one (a native vega, bucketed
delta, theta or yield DV01), point the function's `expr` at it, converted to the contract's unit
and sign, and check it once against the recipe. They must agree to about 1e-4 relative, or you
must be able to explain the difference. Traps: a curve DV01 is not the own-rate delta (it is exact
only when dr/ds = 1). An annuity pv01 is exact only at the money. A "gamma" that is the change in
pv01 per bp is half the gamma. A theta per year, per business day, or on a rolled curve is not
`Theta`. A lognormal vega is not a rescaled normal vega. The recipes behind each of these are in
[`pricebt-asset-config-cookbook`](../pricebt-asset-config-cookbook/SKILL.md) patterns 14-27.

### 5. Map every measure (swap, swaption); declare only on a Bond

**`IRSwap` and `IRSwaption`:** there is no escape hatch. A measure your library has no call for is
still computable from the primitives: a bump-and-reprice recipe, date arithmetic, or an identity
(`PremiumCents` = Price / |notional| x 1e4, `ForwardPrice` = Price / DF(expiry), ...). Leave one out
and the `ConfigError` lists every gap and ends with a paste-ready mapping skeleton
(`contracts.mapping_skeleton`): `functions:` / `portfolio_functions:` / `risk_measures:` stubs whose
expression `... TODO` deliberately does not compile, so a skeleton pasted unchanged never loads.
Declaring a contract measure (or a preset of one) is itself a load error. A literal constant is
honest only for the **zero-by-convention** rows (`contracts.ZERO_BY_CONVENTION`): a swap's
`IRVega`, `IRVanna`, `IRVolga` and vol levels, and `IRBasis` / `IRXccyDelta` on a single-curve,
single-currency library (R2-8); the checker's `ir_fake_constant` FAILs any other constant.

**`Bond`:** delete the measure's `risk_measures:` line; the load error ends with a paste-ready
`unsupported_measures:` block. Replace each `"TODO: ..."` with a specific, true reason. A request
for a declared measure raises `UnsupportedMeasureError` with it. Never map a fake `0.0` or a NaN.

`python skills/pricebt-risk-measures/scripts/measures.py matrix $cfg` audits the result row by row
without running your library and exits 1 while any row is missing or `TODO` (with `--strict`, also
while a Bond declaration has a hint: one every library can avoid); `measures.py block $cfg` prints
the skeleton (swap, swaption) or the declaration block (Bond).

### 6. Load, then verify every measure on one trade

```powershell
python -W error -c "import os; from pricebt.assets.config import load_asset; print(load_asset(os.environ['CFG']).name)"
@'
import os, pricebt.risk as risk
from datetime import date
from pricebt.instrument import IRSwap      # or IRSwaption / Bond
from pricebt.markets import PricingContext
from pricebt.risk import contracts
from pricebt.session import PricebtSession
s = PricebtSession.use(assets=[os.environ["CFG"]])
inst = s.pricing.resolve(IRSwap("Pay", "10y", "USD", 1e6, fixed_rate="ATM"), date(2024, 1, 2), None)
print(inst.resolved_terms)                 # absolute dates, a decimal strike, a signed notional
with PricingContext(date(2024, 1, 2)):
    for r in contracts.contract_for(type(inst).__name__):
        m = getattr(risk, r.measure)
        req = m(aggregation_level="Type") if isinstance(m, risk.RiskMeasureWithFiniteDifferenceParameter) and "scalar" in r.forms else m
        try:
            print(r.measure, inst.calc(req).result())
        except Exception as exc:           # a broken recipe (or a Bond's declared measure) prints why
            print(r.measure, type(exc).__name__, exc)
'@ | python -
```

For `PnlExplain` the loop prints a `NotSupportedError` (it needs `PnlExplain(CloseMarket(date=...))`).
`-W error` makes a Bond's stale declaration (a measure both mapped and declared) fail. Then check every
number against its "Verify with" column (questionnaire §9) and the sign self-tests in the
conversions reference. ATM payer above: `|npv| < 1e-4 x notional` (as `swap_atm_npv`), delta > 0
(about 900 per 1mm for a 10y) and equal to `Annuity x 1e-4`, `IRFwdRate` in bp = strike x 1e4, a
receiver's delta exactly the negative; rerun off the money with `fixed_rate="ATM+25"`. Swaption:
`Sell` = -`Buy`, `Straddle` = payer + receiver, vega > 0 and 0.0 after expiry. Bond: delta < 0,
gamma > 0, `Theta` about PV x yield / 365.

### 7. Run the checker

This step belongs to `pricebt-verify-asset-config`. The checker probes the config the way a backtest
will (market on weekends, resolve pinning, units, signs, ladders, P&L explain, smoke backtest) and
exits 1 on any FAIL:

```powershell
python skills/pricebt-verify-asset-config/scripts/check_asset.py $cfg --sys-path $lib --date 2024-01-02 --date 2024-04-02
```

Fix every FAIL, and read every WARN. A par rate left in percent but declared `bp` **FAILs**
`swap_par_rate_atm` (the ATM par rate is not the strike x 1e4) and WARNs `swap_par_rate_unit`
("could be percent"). See [`example/mistakes/`](example/mistakes/README.md).

### 8. Run a 3-month smoke backtest

This is a monthly-rolled 10y payer; use your instrument:

```powershell
@'
import os
from datetime import date
from pricebt.backtests.actions import AddTradeAction
from pricebt.backtests.generic_engine import GenericEngine
from pricebt.backtests.strategy import Strategy
from pricebt.backtests.triggers import PeriodicTrigger, PeriodicTriggerRequirements
from pricebt.instrument import IRSwap
from pricebt.risk import Price
from pricebt.session import PricebtSession
PricebtSession.use(assets=[os.environ["CFG"]])
swap = IRSwap("Pay", "10y", "USD", 1e4, name="swap")
trig = PeriodicTrigger(PeriodicTriggerRequirements(frequency="1m", end_date=date(2024, 4, 2)), AddTradeAction(swap, "1m"))
bt = GenericEngine().run_backtest(Strategy(None, trig), start=date(2024, 1, 2), end=date(2024, 4, 2), frequency="1b", show_progress=False)
s = bt.result_summary
print(s.tail()); print(bt.trade_ledger()); print("dropped:", bt.missing_market_dates)
assert ((s["Total"] - (s[Price] + s["Cumulative Cash"] + s["Transaction Costs"])).abs() < 1e-6).all()
'@ | python -
```

The dropped dates should be exactly your library's holidays and data gaps. For a swaption, add
`attributes: {expiration_date: ...}` (the template has it) and try `AddTradeAction(option, 'expiration_date')`.

### 9. Add a test

Copy the shape of `tests/skills/test_skill_connect_example.py`, and mark the test so it skips when
your library is absent.

### Worked example: a fictional bank SDK (swap)

[`example/`](example/README.md) wires a **fictional** bank-style SDK, Meridian, whose conventions all
differ from pricebt's on purpose (ISO-string dates, percent rates, a receiver-positive DV01, a
floating tenor, positive cashflow amounts with a direction). It maps the **whole** `IRSwap` contract:
one batched call of first-order codes gives `Price`, the own-rate `IRDelta`, `Annuity`, `IRFwdRate`,
`IRSpotRate`, the zero-curve DV01, `Cashflows` and the CRIF rows; `scenario()` markets give gamma, the
gamma ladder, `Theta` (`hold="FORWARDS"`) and `PnlExplain` (a `valuation_date` with the zero rates
held); `discount_factors()` gives `ForwardPrice` and `FairPremium`. The README shows each conversion
and three one-error variants the checker catches. Run it:

```powershell
$env:PYTHONPATH = "src;tests;skills/pricebt-connect-pricing-library/example"
python -m pytest tests/skills/test_skill_connect_example.py -o addopts= -p no:cacheprovider -q
```

### Service-style vs in-process libraries

- **Service-style** (a DP- or Athena-style platform behind a client: every call is a network round
  trip, and a session needs auth). One client at module level in `code:`; batch and memoise
  (Performance below). The bump recipes cost 2-3 PVs per measure per trade: send them as one batch
  of shifted scenarios if the platform accepts them. Keep credentials out of the YAML. Pin EOD
  snapshots, never "latest", and check the served as-of date. Record and replay for CI
  (`pricebt-enterprise-integration`).
- **In-process** (a QuantLib- or rateslib-style object library). Building the market is expensive
  because it calibrates. pricebt caches one market per (key, date, csa), so never rebuild curves
  inside a function. Build shifted markets from spreads or handles over the base curve. Watch for
  trade objects that cache the fixings of the market they were built on: use
  `build_on: each_market` or a `remark()` helper (see the ARBS config
  [`configs/assets/usd_sofr_ois_interest_rate_swap.yaml`](../../configs/assets/usd_sofr_ois_interest_rate_swap.yaml)).

### Performance

- **One client per process**, created in `code:`, never per call.
- **Memoise a value on (market, `pricebt_date`, trade, `pricebt_*` params):** the memo on `m.__dict__`,
  keyed `(pricebt_date, trade, pricebt_bump_size, ...)` (every `pricebt_*` parameter the function
  reads), since `CloseMarket` hands one market to two dates and a delta must not ignore its bump size.
  One call per trade.
- **Batch portfolio calls.** `portfolio_functions:` receive all of this asset's trades on one date
  (`trades`, `weights`): send them in ONE library call.
- **`build_on: resolve_date`** when the trade object is market-independent.
- The checker's `performance` check flags any evaluation slower than 1s.

## Checks (definition of done)

- [ ] `python -W error` loads the config: no contract problem, no stale declaration.
- [ ] Every contract measure has a worksheet line: native, recipe or zero by convention (swap,
      swaption: no declarations, no constant outside `ZERO_BY_CONVENTION`); a Bond's declarations
      have specific reasons, none still starting with `TODO`.
- [ ] Every conversion line has a `# vendor -> pricebt` comment.
- [ ] `lib_market` / `load_market` returns `None` (it does not raise) on a weekend, a holiday and a
      data gap.
- [ ] `resolved_terms` hold only absolute `date`s, a **decimal** strike and a signed notional or
      face, with `buy_sell` folded in.
- [ ] The sign self-tests pass (conversions reference): payer delta > 0 and long bond delta < 0,
      `Sell` = -`Buy`, `Straddle` = payer + receiver.
- [ ] Dead instruments: every sensitivity is `0.0`, every level is finite, `Cashflows` is empty.
- [ ] The native measures agree with the recipes (or the difference is explained). The ladders sum
      to the parallel delta.
- [ ] `check_asset.py` has no FAIL, and every WARN is explained.
- [ ] The 3-month smoke backtest holds the Total identity on every row, and `missing_market_dates`
      equals the library's closed days.
- [ ] A test runs the config, and it skips cleanly when the library is not installed.

## Pitfalls

- **Unflipped dv01 sign**: a receiver-positive vendor DV01 used as is. Hedges and risk-sized trades
  go the wrong way. See [`example/mistakes/`](example/mistakes/README.md).
- **Rate left in percent** but declared `bp`: 100x too small. The checker FAILs
  `swap_par_rate_atm` and WARNs `swap_par_rate_unit`.
- **Unpinned maturity or strike**: a tenor passed through to a library that resolves it at pricing
  time, or a "par" strike that re-strikes on every market. The trade never ages.
- **Curve DV01 or annuity pv01 as the `IRDelta` scalar** off the money, **half gamma**, a
  **per-year or rolled-curve theta**, and a **lognormal vega rescaled** as normal: see step 4.
- **Vol levels in decimal** (0.008) next to bp configs. Every asset of one book must declare the
  same unit for each level measure, or attribution is meaningless.
- **NaN on a dead trade.** `pnl_explain` has no NaN guard: one NaN poisons every later cumulative
  value. Sensitivities go to `0.0`, and levels keep their last live value.
- **Loops inside an `expr`** (`sum(f(market, x) for x in ...)`) work: injected names reach
  generators, comprehensions and lambdas (fixed). A `code:` helper sees only its asset's namespace,
  so pass `market` in as an argument. Name a pass-through parameter (`pricebt_bump_size`) at the
  expression's top level: pricebt looks for it there.
- **A bump parameter you did not name.** `IRDelta(bump_size=5)` reaches your function only if its
  expression names `pricebt_bump_size`. Otherwise it raises, which is the honest default.
- **Raising instead of returning `None`** on a closed day. Catch the library's specific exceptions,
  not bare `Exception`: a real auth or network failure must propagate.
- **Stale roll-back**: a library that quietly serves yesterday's curve for a holiday. Compare the
  served as-of date with the requested one.
- **Timezone and EOD**: an as-of at a London close for a NY strategy shifts every date by one
  session. Set `PricebtSession.use(..., tz=, eod_time=)` or use `pricebt_timestamp`.
- **Two configs matching the same instrument** in one session is an error. Give each a distinct
  `match:`, or pass `pricebt_asset=` on the instrument. A swaption asset gets its **own**
  `market.key`, even when it shares a swap asset's curve.
- **Helper names that shadow injected names** (`market`, `market_to`, `trade`, `kwargs`,
  `resolved`, `trades`, `weights`) break silently. Prefix your helpers.
- **Absolute paths or credentials in the YAML.** Configs are code, and they get committed.
- **A process-global evaluation date** (QuantLib-style `Settings.instance().evaluationDate`): pricebt
  interleaves dates in one process. Set it inside every pricing primitive, move it for `Theta` in
  `try/finally`, never at import, and key memos on the date.

## Related skills

- [`pricebt-verify-asset-config`](../pricebt-verify-asset-config/SKILL.md): the checker that step 7 runs.
- [`pricebt-asset-config-cookbook`](../pricebt-asset-config-cookbook/SKILL.md): the measure recipes (patterns 14-27), config patterns and the error catalogue.
- [`pricebt-risk-measures`](../pricebt-risk-measures/SKILL.md): the gs measure catalogue and the semantics of each contract measure.
- [`pricebt-pnl-attribution`](../pricebt-pnl-attribution/SKILL.md): what the greeks you mapped are used for (P&L decomposition).
- [`pricebt-port-gs-notebook`](../pricebt-port-gs-notebook/SKILL.md): gs swaption, portfolio and pricing-and-risk notebooks on your config.
- [`pricebt-enterprise-integration`](../pricebt-enterprise-integration/SKILL.md): service-style platforms, record/replay, sessions.
- [`pricebt-architecture`](../pricebt-architecture/SKILL.md): the mental model and the rules you must never break.
- [`pricebt-strategy-intake`](../pricebt-strategy-intake/SKILL.md): turn a strategy idea into a spec once pricing works.
