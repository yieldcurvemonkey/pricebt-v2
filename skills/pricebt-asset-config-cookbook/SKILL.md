---
name: pricebt-asset-config-cookbook
description: Copy-paste patterns for pricebt asset configs - library shapes (service with market handles, in-process library with local curve files, curve + vol markets), resolve/pinning, unit and sign conversion, batching, attributes, CSA routing, multi-currency and FX - plus the recipes for every measure of the IR contract (IRSwap, IRSwaption, Bond) when your library lacks it (own-rate total delta, chain-rule gamma, translated-curve and constant-yield theta, vega/vanna/volga by normal-vol bumps, lognormal-to-normal vol and vega conversion, key-rate ladders, '<tail>;<expiry>' vega cubes, per-row buckets and PnlExplain, Cashflows frames, bump_size pass-through, dead instruments, buy_sell/Straddle and bond size folding, zero-by-convention measures), and a catalogue of every pricebt error message with its fix. Use while writing or debugging an asset config.
---

# Asset config cookbook

Patterns for the parts of an asset config that are hard to get right. The contract is
[`docs/v2/ASSET_CONFIG_GUIDE.md`](../../docs/v2/ASSET_CONFIG_GUIDE.md), and the IR measure contract
is `src/pricebt/risk/contracts.py`. The end-to-end procedure and the three contract templates live
in [`pricebt-connect-pricing-library`](../pricebt-connect-pricing-library/SKILL.md). Every pattern
here is in the style of the configs the test suite runs: `tests/assets/toy_usd_irs_full.yaml`,
`tests/assets/toy_usd_swaption.yaml`, `tests/assets/toy_usd_bond.yaml`, and
`configs/assets/usd_sofr_ois_interest_rate_swap.yaml`. The measure recipes (patterns 14-27) are
executed as tested code in the templates, so this page describes the math and names the template
helper instead of repeating it.

## When to use / not use

- **Use** when you know which part of the config you are writing, when your library lacks a
  contract measure, or when an error message names a config key or a measure.
- **Do not use** for your first config. Start from the connect skill's template for your instrument.

## Inputs and outputs

- **Inputs:** the config you are writing, and what your library offers for the measure at hand
  (the connect skill's capability worksheet).
- **Outputs:** a config fragment whose units and signs match the contract, with each conversion
  commented on its line.

## Procedure

1. Find the problem in the index below and open the pattern in
   [`references/patterns.md`](references/patterns.md).
2. For a contract measure, check whether your library computes it natively. If it does, convert it
   (unit, sign, bump convention) on the function's line. If it does not, keep the template's recipe
   for it. If you cannot derive it, declare it (pattern 14).
3. Load with `python -W error` (a stale declaration then fails), and verify the number as the
   pattern says.
4. Run the checker, [`pricebt-verify-asset-config`](../pricebt-verify-asset-config/SKILL.md).
5. For an error message, look it up in [`references/error-catalogue.md`](references/error-catalogue.md).

## Pattern index

| # | Problem | Pattern (full text in [`references/patterns.md`](references/patterns.md)) |
|---|---|---|
| 1 | The library is a **service**: you ask for a market handle by date | a module-level client in `code:`; `load_market(d)` catches the platform's closed/no-data errors and returns `None` |
| 2 | The library is **in-process** and data is in local files | load the whole history once in `code:`; `market.expr` builds or looks up the day's curve object |
| 3 | The market has **several parts** (curve + vol cube, or curve + fixings) | return one `SimpleNamespace(curve=..., vol=...)`; return `None` if any required part is missing |
| 4 | **Pinning** tenors and `'ATM'` at the trade date | `resolve` builds a probe trade, reads back its absolute dates and par rate, and returns plain data |
| 5 | **gs kwargs grammar** (`'ATM+25'` bp, decimal `fixed_rate`, `PayReceive.Receive == 'Rec'`, `buy_sell`) | a small parser in `code:`; fold direction into a signed notional |
| 6 | The library's **units or signs** differ from pricebt's | convert in the function expression: `* 1e4` (decimal→bp), `* 100` (pct→bp), `-` (receiver-positive dv01), `/ 100` (per 1% → per 1bp) |
| 7 | **Seasoned trades** need today's fixings | `build_on: resolve_date` plus a `remark(market, trade)` helper memoised on the market |
| 8 | **Portfolio risk**: a ladder over many trades | a `portfolio_functions` entry, one batched library call, `weights` applied once |
| 9 | gs reads **attributes** (`termination_date`, `notional_amount`) | the `attributes:` map. A `size_attribute` must be linear and signed in `pricebt_quantity` |
| 10 | **CSA / discounting choice** | branch on `pricebt_csa` in `market.expr` (it is part of the market cache key) |
| 11 | **Several currencies** | one config per currency, selected by `match: {notional_currency: EUR}`, plus an FX config |
| 12 | **FX** from a platform or a file | an FX config whose `rate(base, quote, d)` returns quote per base, or `None` |
| 13 | **Slow** valuations | memoise on the market (a value also per `pricebt_date` and trade), batch, `build_on: resolve_date`, and never rebuild the client per call |
| 14 | The **measure contract**: a measure your library cannot compute | map it with the contract's unit, or declare it under `unsupported_measures:` with a specific reason; paste the block the load error prints |
| 15 | A swap's or bond's **vega, vanna, volga and vol levels** | map them to `0.0` (zero by convention, R2-8) so mixed books work; do not declare them |
| 16 | **`IRDelta` scalar** when your library only bumps curves | the total own-rate derivative `[PV(+h) - PV(-h)] / [r(+h) - r(-h)]` (`own_rate_delta`); a curve DV01 or annuity pv01 is not it off the money |
| 17 | **`IRGammaParallel`** | the chain-rule second derivative on the same bumps (`own_rate_gamma`); never d(pv01)/dr, which is half the gamma |
| 18 | **Vol units, lognormal vols, vega** | normal vols in bp; a lognormal library uses the Bachelier-implied vol of its own premium; vega per bp of NORMAL vol |
| 19 | **`IRVanna`, `IRVolga`** | normal-vol bumps of the own-rate delta and of PV; request with `aggregation_level='Type'` |
| 20 | **`Theta`** | one calendar day on a TRANSLATED curve, forwards and vols fixed, plus the day's cash; bonds: the same yield one day later |
| 21 | **Ladders and the vega cube** | key-rate bumps; `IRGamma` diagonal; cube keys `"<tail>;<expiry>"` (`"10Y;1Y"`) |
| 22 | **Several curves in one ladder; `PnlExplain`** | a list of row dicts (`mkt_type`, `mkt_asset`, `mkt_point`, `value`); PnlExplain names `market_to` |
| 23 | **`Cashflows`** and coupons | `returns: frame`, `scale_columns: [payment_amount]`; empty for a total-return Price |
| 24 | gs **`bump_size` / `finite_difference_method`** | name `pricebt_bump_size` in the expression; unnamed parameters raise (the honest default) |
| 25 | **Dead instruments** | sensitivities `0.0`, levels finite (last live value), `Cashflows` empty; swaptions physically settled |
| 26 | **Swaption** `buy_sell`, `Straddle`, strikes | fold `buy_sell` into the signed notional; a straddle is payer + receiver legs; reject server grammar at resolve |
| 27 | **Bond** `size`, `buy_sell`, identifier | static data by identifier; signed face; `notional_amount` attribute + `size_attribute`; own rate = yield |

## Unit and sign conversion table (the most common source of wrong P&L)

| Library says | pricebt needs | Expression |
|---|---|---|
| par rate 0.0425 (decimal) | bp | `rate * 1e4` |
| par rate 4.25 (percent) | bp | `rate * 100` |
| DV01 > 0 for a **receiver** (PV change for rates **down** 1bp) | ccy per **+1bp**, payer > 0 | `-dv01` |
| DV01 per 1% (100bp) | ccy per 1bp | `dv01 / 100` |
| PV01 of the fixed leg only (annuity × 1bp) | the own-rate `IRDelta` | exact only at the money: use the recipe (pattern 16) |
| "gamma" = change in pv01 per bp | `IRGammaParallel` | half the gamma at the money: use the chain rule (pattern 17) |
| normal vol 0.0080 (decimal) / 0.80 (percent) | bp | `vol * 1e4` / `vol * 100` |
| lognormal vol 0.20 | **normal** vol, bp | `bachelier_implied_vol(PV / annuity, F, K, T, is_payer) * 1e4` (pattern 18) |
| vega per 1% lognormal | ccy per bp of normal vol | about `x * 0.01 / F` at the money; better, bump the normal vol |
| theta per year | ccy per calendar day | `theta / 365`, and only if it holds forwards fixed (pattern 20) |
| bond DV01 per 100 face, positive | ccy per +1bp, long < 0 | `-dv01 * face / 100` |
| bond clean price per 100 | dirty PV in ccy | `(clean + accrued) / 100 * face` |
| bucket keys `"USD.SOFR:2Y"` | `"2Y"` (any str label) | `{k.split(":")[1]: -v for k, v in b.items()}` |
| vega buckets keyed (expiry, tail) | `"<tail>;<expiry>"` | `f"{tail};{expiry}"` |
| notional always positive, direction flag | the direction is in resolve's signed notional | `n * (1 if pay else -1)` |
| PV in thousands | ccy | `pv * 1e3` |

The full table, with sanity numbers and sign self-tests, is
`skills/pricebt-connect-pricing-library/references/convention-conversions.md`. Prove every
conversion with the checker, [`pricebt-verify-asset-config`](../pricebt-verify-asset-config/SKILL.md).

## Error catalogue

Every pricebt error names the asset, the config key and the offending value. The causes and fixes
are in [`references/error-catalogue.md`](references/error-catalogue.md), including the contract
errors, `UnsupportedMeasureError`, stale-declaration warnings, refused measure parameters, and
frame errors. `tests/skills/test_skill_asset_config_cookbook.py` raises the error behind each
literal fragment and checks that the catalogue quotes it verbatim. Rows with placeholders, or that
need a whole backtest, are copied from the source only.

## Checks

- `python -W error` loads the config (no contract problem, no stale declaration).
- `python skills/pricebt-verify-asset-config/scripts/check_asset.py <config>` passes.
- Every conversion line in the config has a comment: `# vendor: <convention> -> pricebt: <convention>`.
- Each native measure you mapped agrees with the template recipe to about 1e-4 relative, or you can
  explain the difference.

## Pitfalls

- **A comprehension inside an `expr`** cannot see injected names (`NameError: name 'market' is not
  defined`). Move the loop into a `code:` helper.
- **Declaring a zero-by-convention measure** on a swap or bond breaks mixed books with vol
  attribution. Map `0.0` instead (pattern 15).
- **Mixed units across one book.** Every asset that can sit in one book must declare the same unit
  for `IRFwdRate` and the vol levels. The `ir_pnl_definition` family checks each level's unit and
  raises (DEV-E21); a hand-written `PnlAttribute` without `market_data_unit` is silently off by
  1e4.
- **Reversed cube keys** (`"1Y;10Y"` for a 1y x 10y) mislabel every vega row silently.
- **NaN on a dead trade** poisons every later cumulative `pnl_explain` value.

## Related skills

- [`pricebt-connect-pricing-library`](../pricebt-connect-pricing-library/SKILL.md): the procedure, capability discovery and the contract templates.
- [`pricebt-enterprise-integration`](../pricebt-enterprise-integration/SKILL.md): sessions, secrets, record/replay.
- [`pricebt-verify-asset-config`](../pricebt-verify-asset-config/SKILL.md): automated proof.
- [`pricebt-risk-measures`](../pricebt-risk-measures/SKILL.md): the measure catalogue and contract semantics these recipes implement.
- [`pricebt-port-gs-notebook`](../pricebt-port-gs-notebook/SKILL.md): gs code on your configs (units: gs returns decimals).
