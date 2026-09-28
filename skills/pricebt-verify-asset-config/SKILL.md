---
name: pricebt-verify-asset-config
description: Automated "zero to confidence" checker for a newly written or changed pricebt asset config - loads it, evaluates the market, resolve, every function and risk measure, runs a smoke backtest, and (for IRSwap) a rates sanity pack (ATM npv, dv01 sign/size, par-rate units, ladder sum, PnL explain); prints a PASS/WARN/FAIL table. Use after writing or editing any asset config and before any strategy work, or when numbers from a config look wrong.
---

# Verify an asset config

An asset config is trusted code that turns your pricing library into pricebt numbers. One wrong sign, unit or unpinned tenor corrupts every backtest built on it, silently. `check_asset.py` registers the config in its own `PricebtSession`, probes it the way the engine will, and reports one row per check. It proves the config is *internally consistent and wired the way pricebt expects*; it cannot prove the library's numbers are *right* (see [What the checker cannot prove](#what-the-checker-cannot-prove)).

## When to use / not use

- **Use** after writing a new asset config, after any edit to one, after upgrading the pricing library, and before any strategy work on that asset.
- **Use** when a backtest number looks wrong: rerun the checker first, so you know whether the config is to blame.
- **Do not** use it as the only validation of a config going into production research. Do the independent validation in [`references/independent-validation.md`](references/independent-validation.md) once per asset.
- **Do not** use it to review a strategy. That is [`pricebt-adversarial-review`](../pricebt-adversarial-review/SKILL.md).

## Inputs

- The asset config YAML (see `docs/v2/ASSET_CONFIG_GUIDE.md` for the contract).
- The directory holding the pricing library or helper modules, if it is not already importable (`--sys-path`).
- Two sample business dates the library has data for (`--date`, twice). Default: about 120 days ago and one month later.
- Optionally: an FX config (`--fx`), instrument kwargs as JSON (`--kwargs`), and a smoke-backtest window (`--start`, `--end`; default `d1` plus three months).

## Outputs

- A markdown table on stdout: `| check | status | detail |`, then a `PASS=.. WARN=.. FAIL=.. SKIP=..` line.
- Exit code **1** if any row is FAIL, else 0.
- With `--json OUT`: the same rows as a JSON list of `{name, status, detail}`.
- In-process: `run_checks(...)` returns a list of `CheckResult(name, status, detail)`.

## Procedure

1. **Run the checker from the repository root.**

   ```powershell
   $env:PYTHONPATH = "src;tests"
   python skills/pricebt-verify-asset-config/scripts/check_asset.py configs/assets/<your_asset>.yaml --sys-path <dir with your library or helper modules> --date 2024-01-03 --date 2024-02-05
   ```

   On POSIX use `PYTHONPATH=src:tests`. Try it first on the toy config, which passes: `tests/assets/toy_usd_irs.yaml`.

   Useful options:

   | option | effect |
   |---|---|
   | `--kwargs '{"termination_date": "5y"}'` | probe a different trade than the config's `defaults` |
   | `--fx <fx config yaml>` | also check the FX round trip for a non-USD asset |
   | `--start 2024-01-02 --end 2024-06-28` | smoke-backtest window (must have market data) |
   | `--no-backtest` | skip the smoke backtest (fast iteration on one function) |
   | `--json OUT` | machine-readable rows |

   In-process (for example from a notebook):

   ```python
   import sys; sys.path.insert(0, "skills/pricebt-verify-asset-config/scripts")
   import check_asset
   from datetime import date
   rows = check_asset.run_checks("tests/assets/toy_usd_irs.yaml", dates=[date(2024, 1, 3), date(2024, 2, 5)])
   print(check_asset.to_markdown(rows))
   ```

2. **Read the table top to bottom.** Rows run in dependency order. If `config_loads`, `imports_execute` or `market_available` FAILs, the rest is a single `remaining | SKIP` row: fix that first. `SKIP` means "not applicable" (for example no `notional_amount` kwarg, or not an IRSwap), never "passed".

3. **Fix every FAIL** with the table below, then rerun. **Explain every WARN** in one line, in your notes or the config's `description:`; a WARN you cannot explain is a FAIL.

4. **Do the independent validation once per asset**: [`references/independent-validation.md`](references/independent-validation.md), using the probes in [`references/known-answer-probes.md`](references/known-answer-probes.md).

## Symptom, cause, fix

Every check is documented, with the mistake it catches, in the docstring of `skills/pricebt-verify-asset-config/scripts/check_asset.py`.

| check | symptom | likely cause | fix |
|---|---|---|---|
| `config_loads` | FAIL `ConfigError [...]` | unknown key, bad `unit:`, a `risk_measures:` entry naming a missing function, no `Price`, expression syntax error | follow the message; it names the key and suggests the nearest valid name |
| `match` | WARN "finds no asset" | `match:` rule that the config's own `defaults`/your kwargs do not satisfy | fix `match:` or `defaults`; users otherwise need `pricebt_asset=` |
| `imports_execute` | FAIL `key 'imports'` or `key 'code'` | module not importable, wrong name, a login call raising | add `--sys-path`; check the import in a bare Python shell |
| `market_available` | FAIL "returned None on business day(s)" or `key 'market'` | wrong curve id, date passed as the wrong type, CSA not handled, no data for that date | print the market expression's inputs; pick dates the library has |
| `market_weekend` | FAIL "raised on Saturday" | library raises on a non-trading day | catch it in a `code:` helper and return `None` (pricebt drops or rolls on `None` only) |
| `market_weekend` | WARN "returned an object on Saturday" | library serves Friday's data on weekends | usually fine (the toy does this); make sure it is not stale data on a real holiday |
| `resolve_pins_terms` | FAIL "still relative" | `resolve:` returns a tenor (`'10y'`) or `'ATM'` string | convert every relative term to an absolute date / number inside `resolve:` |
| `resolve_pins_terms` | WARN "identical dates" | `resolve:` ignores `pricebt_date` (for example pins from today) | use the injected `market`/`pricebt_date` as the trade date |
| `functions_finite[f]` | FAIL with an exception | wrong attribute/method name on the library trade, wrong argument order | fix the expression; the message has the expression text and date |
| `functions_finite[f]` | WARN NaN | function undefined on a live trade | allowed for dead trades only; find out why on a live one |
| `quantity_scaling[M]` | FAIL | `scale_with_quantity:` contradicts the unit | remove the override, or fix the unit |
| `notional_linearity[f]` | FAIL "it is a rate" / "it is an amount" | wrong `unit:` (a par rate declared `ccy`, a PV declared `bp`) | declare `bp`/`pct`/`decimal` for rates, `ccy`/`ccy_per_bp` for amounts |
| `notional_linearity[f]` | WARN "neither linear nor constant" | library applies notional-dependent logic, or the size kwarg is not reaching the trade | check the trade builder uses `resolved` notional |
| `risk_measures[M]` | FAIL | mapped function fails in scalar or bucketed form | fix the function, or the `{scalar, bucketed}` mapping |
| `risk_measures[M]` | WARN "not a pricebt.risk measure" | typo in the measure name | use the gs name (`IRDelta`, `IRFwdRate`, ...) |
| `measure_series` | WARN "constant" | market expression ignores `pricebt_date` (always today's curve) | pass `pricebt_date` to the library's market loader |
| `smoke_backtest` | FAIL non-finite Price | NaN or inf on some grid date | find the date in the detail and price it by hand |
| `smoke_backtest` | FAIL identity / exception | engine could not run the asset (message says why) | fix per message; rerun with `--no-backtest` to isolate |
| `fx_round_trip` | FAIL | FX config returns the same quote both ways | return the reciprocal for the reverse pair |
| `performance` | WARN SLOW | one evaluation over 1s: curve rebuilt per call, no caching in the library session | build the curve once in `market:`, reuse it in every function |
| `swap_atm_npv` | FAIL/WARN | ATM strike computed on a different curve, date or convention than valuation | strike and value off the same `market` object |
| `swap_dv01_sign` | FAIL payer <= 0 | library reports risk as PV change per -1bp, or receiver-positive | negate in the function; pricebt wants payer dv01 > 0 per +1bp |
| `swap_dv01_sign` | FAIL receiver != -payer | `pay_or_receive` not reaching the trade builder | map it in `resolve:`/`trade:` |
| `swap_dv01_band` | FAIL | dv01 per 1% (x100), per unit rate (x1e4), or per unit notional | rescale to currency per 1bp on the full notional |
| `swap_par_rate_unit` | FAIL "looks like decimal" | library returns 0.0425 and the function is declared `bp` | multiply by 1e4, or declare `decimal` |
| `swap_par_rate_unit` | WARN "could be percent" | 4.25 declared `bp` | multiply by 100, or declare `pct` |
| `swap_par_rate_atm` | FAIL | an ATM trade's par rate on its trade date != `resolved["fixed_rate"] * 1e4` (unit wrong, or resolve and par_rate use different curves) | fix the unit of `par_rate`, or price par and strike off the same market |
| `swap_bucket_sum` | FAIL | ladder in a different unit/sign than the scalar, or missing pillars | same convention as the scalar; include every pillar |
| `swap_pnl_explain` | FAIL negative ratio | npv and dv01 use opposite sign conventions | make npv payer-positive when rates rise |
| `swap_pnl_explain` | WARN ratio outside [0.5, 1.5] | large carry/roll between the dates, or a scale error | try closer dates; if it persists, check units |

## What the checker cannot prove

- **That the library's numbers are right.** A consistently wrong curve passes every check. Compare two or three trades with the desk's own risk system: [`references/independent-validation.md`](references/independent-validation.md).
- **Holiday behaviour.** It probes one Saturday, not the library's holiday calendar. Probe a real holiday by hand ([`references/known-answer-probes.md`](references/known-answer-probes.md)).
- **Seasoned trades, cashflows and fixings.** It resolves on `d1` and values a month later, so it catches maturity drift, but it cannot tell you whether a trade past a coupon date is valued with the right fixing.
- **Other trades.** It probes one set of kwargs. Rerun with `--kwargs` for each shape you will trade (short and long tenors, off-market strikes, forward starts).
- **Rates pack coverage.** Only `instrument: IRSwap` gets the pack. Other asset classes get the generic checks; use the known-answer probes for the rest.

## Checks

You are done when:

- the checker exits 0 on every asset config the strategy will use, with the dates and kwargs it will trade;
- every WARN has a one-line explanation;
- the independent validation is recorded for each asset (two or three trades within tolerance);
- after any later edit to the config, the checker has been rerun.

## Pitfalls

- **Default dates may not have data.** Always pass `--date` twice for a real library.
- **SKIP is not PASS.** A swap config with no `IRDelta` mapping skips most of the rates pack; add the mapping.
- **The toy returns a market on weekends**, so the toy shows a `market_weekend` WARN. That is expected for the toy.
- **The checker imports your library in-process.** A config that logs in or opens a connection does so on the first evaluation. Run it where that is allowed.
- **ATM-only probes hide an npv sign error**: the npv of an ATM trade is about 0. The checker therefore scales and doubles on `d2`, where the `d1` trade is off-market, and runs `swap_pnl_explain`.
- **One fixture per mistake** in `tests/skills/fixtures/check_asset/` shows what a FAIL looks like. Run one to see the table before you trust a PASS.

## Related skills

- [`pricebt-connect-pricing-library`](../pricebt-connect-pricing-library/SKILL.md): writing the config this skill checks.
- [`pricebt-asset-config-cookbook`](../pricebt-asset-config-cookbook/SKILL.md): patterns for fixing what this skill finds.
- [`pricebt-spot-checks`](../pricebt-spot-checks/SKILL.md): numeric checks on a finished backtest.
- [`pricebt-adversarial-review`](../pricebt-adversarial-review/SKILL.md): reviewing the strategy design.
