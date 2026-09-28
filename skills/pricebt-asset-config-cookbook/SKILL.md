---
name: pricebt-asset-config-cookbook
description: Copy-paste patterns for pricebt asset configs by library shape (service with market handles, in-process library with local curve files, multi-part markets with vols), plus resolve/pinning, unit and sign conversion, batching portfolio functions, attributes, CSA routing, multi-currency and FX configs, and a catalogue of pricebt error messages with their fixes. Use while writing or debugging an asset config.
---

# Asset config cookbook

Patterns for the parts of an asset config that are hard to get right. The contract is [`docs/v2/ASSET_CONFIG_GUIDE.md`](../../docs/v2/ASSET_CONFIG_GUIDE.md); the end-to-end procedure is [`pricebt-connect-pricing-library`](../pricebt-connect-pricing-library/SKILL.md). Every pattern here is in the style of the configs the test suite runs: `tests/assets/toy_usd_irs.yaml`, `tests/assets/toy_usd_swaption.yaml`, and `configs/assets/usd_sofr_ois_interest_rate_swap.yaml`.

## When to use / not use

- **Use** when you know which part of the config you are writing, or when an error message names a config key.
- **Do not use** for your first config. Start from the connect skill's template instead.

## Pattern index

| # | Problem | Pattern (full code in [`references/patterns.md`](references/patterns.md)) |
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
| 13 | **Slow** valuations | memoise per market, batch, `build_on: resolve_date`, and never rebuild the client per call |

## Unit and sign conversion table (the most common source of wrong P&L)

| Library says | pricebt needs | Expression |
|---|---|---|
| par rate 0.0425 (decimal) | bp | `rate * 1e4` |
| par rate 4.25 (percent) | bp | `rate * 100` |
| DV01 > 0 for a **receiver** (PV change for rates **down** 1bp) | ccy per **+1bp**, payer > 0 | `-dv01` |
| DV01 per 1% (100bp) | ccy per 1bp | `dv01 / 100` |
| PV01 of the fixed leg only (annuity × 1bp) | dollar delta of the swap | usually the same sign as a payer's dv01; check a known answer |
| bucket keys `"USD.SOFR:2Y"` | `"2Y"` (any str label) | `{k.split(":")[1]: -v for k, v in b.items()}` |
| notional always positive, direction flag | the direction is in resolve's signed notional | `n * (1 if pay else -1)` |
| PV in thousands | ccy | `pv * 1e3` |

Prove every conversion with the checker's rates pack: [`pricebt-verify-asset-config`](../pricebt-verify-asset-config/SKILL.md).

## Error catalogue

Every pricebt error names the asset, the config key and the offending value. Looked-up causes and fixes: [`references/error-catalogue.md`](references/error-catalogue.md).

## Checks

- `python skills/pricebt-verify-asset-config/scripts/check_asset.py <config>` passes.
- Every conversion line in the config has a comment: `# vendor: <convention> -> pricebt: <convention>`.

## Related skills

- [`pricebt-connect-pricing-library`](../pricebt-connect-pricing-library/SKILL.md): the procedure and the template.
- [`pricebt-enterprise-integration`](../pricebt-enterprise-integration/SKILL.md): sessions, secrets, record/replay.
- [`pricebt-verify-asset-config`](../pricebt-verify-asset-config/SKILL.md): automated proof.
