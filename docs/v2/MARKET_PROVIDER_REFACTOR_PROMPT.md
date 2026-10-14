# Prompt: move an asset config's market into market providers and make the asset curve-agnostic

A hand-off prompt for an AI coding agent that works where your pricing library is installed. It
refactors one existing swap asset config (market loading, trade building and pricing in one file)
into market provider configs plus an asset config that holds no market-data detail, takes the curve
per instrument (`curve='estr:lch'`) and can size a trade by risk (`dv01=25_000`).

Design it follows: [`MARKET_CONTEXT_DESIGN.md`](MARKET_CONTEXT_DESIGN.md). The same pattern at toy
scale, as tests: `tests/test_market_provider.py` ("one asset, several curves"). The checker it
uses: [`tools/parity_check.py`](../../tools/parity_check.py).

What shapes it (all verified in this repository):

- **One asset config has one fixed `currency:` and one fixed `market.provider`.** So there is one
  asset file per currency, with an identical body and a short header; index and clearing house
  are chosen per instrument inside a currency.
- **Keywords pricebt does not know pass through to `resolve`.** `IRSwap('Pay', '10y', 'EUR',
  curve='estr:lch', dv01=25_000)` constructs today. The gs `clearing_house` field is an enum (LCH,
  EUREX, JSCC, CME, NONE) and rejects anything else, hence one `curve` token.
- **Sizing by DV01 belongs in `resolve`**: solve the notional once, on the trade date, with the
  same function the config maps to the `IRDelta` scalar. For a strategy-level size,
  `AddScaledTradeAction(scaling_type=ScalingActionType.risk_measure, ...)` and `HedgeAction`
  already exist and need no config change.
- **The prompt names no library.** It tells the agent to treat your file as the source of truth,
  to move code rather than rewrite it, and to stop and ask for the identifiers of any curve other
  than the one the file already prices on.

Fill in section 0, then give the agent everything below the line.

---

# Task: split a pricebt swap asset config into market providers plus a curve-agnostic asset config

## 0. Filled in by the user (ask once if any line is blank)

- Repository root: `<path>`
- How to run Python here (interpreter, PYTHONPATH, anything the pricing library needs): `<command>`
- Config to refactor: `configs/assets/<file>.yaml`
- Its currency, and the curve it prices on today as `index:clearing_house`: `<CCY>`, `<index>:<clearing house>`
- Market data location of its snapshot (`LDN`, `NYC`, `HKG`, `TKO`): `<location>`
- Three or more consecutive business dates known to have market data: `<dates>`

The examples below use EUR and `estr:<ch>`. Substitute the values above everywhere.

## 1. Goal

The config above does three jobs in one file: it loads the dated market, it builds the trade, and
it prices and risks the trade. Split it so that:

1. **Market data lives in market provider configs** (`configs/markets/*.yaml`). The asset config
   names a provider and contains no curve name, index name, snapshot time, time zone or other
   market-data detail.
2. **The asset config is curve-agnostic.** The curve is chosen per instrument with a `curve`
   keyword, a token of the form `index:clearing_house` (for example `estr:lch`, `sofr:cme`).
3. **A trade can be sized by risk**: `dv01=<currency per basis point>` instead of
   `notional_amount`.
4. **Numbers do not change.** The refactored config must reproduce the current one exactly for the
   existing curve. This is checked with `tools/parity_check.py`, not by inspection.

Target usage:

```python
PricebtSession.use(markets=["configs/markets"], assets=["configs/assets"])
swap = IRSwap("Pay", "10y", "EUR", curve="estr:lch", dv01=25_000)
```

## 2. Before you change anything

1. Confirm the checkout has market providers:
   `python -c "from pricebt.assets import load_market, MarketRegistry"` must succeed. If it fails,
   stop and tell the user to check out a branch that contains `src/pricebt/assets/market.py`.
2. Read, in this order:
   - `configs/markets/README.md`
   - `docs/v2/MARKET_CONTEXT_DESIGN.md` sections 3, 4 and 5
   - `docs/v2/ASSET_CONFIG_GUIDE.md`, sections "Injected variables" and "Market providers"
   - `tests/markets/toy_usd_ois.yaml`, `tests/markets/toy_usd_swaption_vol.yaml`
   - in `tests/test_market_provider.py`, the block "one asset, several curves": the target
     pattern on the toy library
   - the whole config to refactor, top to bottom
3. **This repository's remote is public and the config is private.** Do not `git add`, commit or
   push the config, the new provider files, your copy of the parity script or any output. Do not
   copy library identifiers into any tracked file.

## 3. Framework facts you must design around

These are how pricebt works. Do not try to work around them and do not edit anything under
`src/pricebt`.

- An asset config has **one fixed `currency:`** and **one fixed `market: {provider: <name>}`**.
  So there is one asset file per currency. Everything below its header must be identical across
  currencies; only the header differs.
- A provider's `expr` sees only `pricebt_date`, `pricebt_timestamp`, `pricebt_datetime`,
  `pricebt_csa`, `pricebt_location` and `markets`. It never sees an instrument or an asset.
- A provider is evaluated once per (provider, date, csa, location) and its object is shared by
  every asset that names it. Treat the object as read-only.
- `requires:` is all-or-nothing: if any required provider returns `None` for a date, the provider
  that requires it has no market for that date and its `expr` is not evaluated.
- `None` from a provider means "no market on this date" (the backtest drops the date). An
  exception means a real error. Keep that distinction exactly as the current market-loading
  function has it.
- Keywords pricebt does not know (`curve=`, `dv01=`) are passed through unchanged to `resolve` in
  `kwargs`. `defaults:` in the asset config fills them when they are absent.
- `resolve` runs once, on the trade date, and its output is the trade's terms for life. It must
  return plain data only (str, int, float, bool, None, date, datetime, or a tuple of those).

## 4. Target layout

```
configs/markets/eur_estr_<ch>.yaml           one provider per curve: loads that curve's dated market
configs/markets/eur_rates.yaml               one provider per currency: requires its curves, returns {token: record}
configs/assets/eur_interest_rate_swap.yaml   asset: eur_interest_rate_swap, market: {provider: eur_rates}
configs/_legacy/<original file name>         frozen copy of today's file (the parity reference)
```

Naming: curve providers are `<ccy>_<index>_<clearing house>`, lower case. The currency provider is
`<ccy>_rates`. A curve token is `<index>:<clearing house>`, lower case.

**The curve record.** Each curve provider returns one object, a `types.SimpleNamespace`, holding:

- `env`: exactly what today's market-loading function returns;
- one attribute for **every literal in today's asset code that names the currency, the index, a
  funding or discount index, a reset or fixing series, a curve, a label, or a market-mode switch
  read from an environment variable**. Typical places: the keyword arguments of the pricing calls,
  the first arguments of the bump and date helpers, the `mkt_asset` label of a ladder or a
  P&L-explain row, the key that selects a currency in a library result, a reporting currency. The
  file is the source of truth: find them all yourself. Name each attribute after the library
  argument it feeds.

The currency provider returns a plain dict `{"estr:<ch>": <record>, ...}`.

## 5. Hard rules

1. **Do not change any library call.** Same function, same arguments, same order, same constants,
   same signs, same unit conversions. You may only: move code between files; replace a literal by
   an attribute of the curve record; add the `curve` and `dv01` features.
2. **Do not invent library identifiers.** You know the identifiers of one curve only: the ones
   already in the file. For any other curve, stop and ask the user (section 6, step 7).
3. Keep every entry of `risk_measures:`, `functions:`, `portfolio_functions:`, `attributes:`,
   `resolve:` and `trade:` (including `build_on`). Only their expressions' arguments change.
4. Do not change tolerances or expected values to make a check pass. If parity fails and you
   cannot find the cause in two attempts, stop and report the differing values.
5. Do not edit anything under `src/pricebt`, `tests/`, `tools/` or `skills/`.

## 6. Steps (do them in order; each ends with a gate)

### Step 1. Freeze the reference and prove the checker works

1. Copy today's config, unchanged, to `configs/_legacy/` (same file name).
2. Run `python tools/parity_check.py` unedited, from the repository root. Expected:
   `216 values compared, 0 difference(s)`, exit code 0. This proves the script and the environment.
3. Copy it to `scratch/parity_check.py` and edit the copy's three `EDIT` blocks: `OLD` and `NEW`
   both load the legacy copy; `DATES` are the user's dates; `LOCATION` is the location of section 0
   (the provider of step 2 answers to that location only, and the default is `LDN`); `CASES` are at least a payer and a
   receiver, an at-the-money and an off-market fixed rate, and a forward-starting swap.
4. **Gate:** it reports 0 differences. Then prove it can fail: in a temporary copy of the config
   used as `NEW`, multiply one function's expression by `1.0001`; the script must report
   differences and exit 1. Delete the temporary copy.

If the script prints a `note ... OLD raises for [...]` line, report it: a function that already
fails in the legacy config is compared only by its error type, so it is not really covered.

### Step 2. Write the curve provider (see the guide in section 7)

Create `configs/markets/eur_estr_<ch>.yaml`. Move into it, unchanged: the market-loading function
and every helper and import it uses, and nothing it does not use.

### Step 3. Write the currency provider

```yaml
schema_version: 1
market: eur_rates
description: "EUR rates markets by curve token."
requires: [eur_estr_<ch>]
expr: '{"estr:<ch>": markets["eur_estr_<ch>"]}'
```

### Step 4. Refactor the asset config

Work on a copy named `configs/assets/eur_interest_rate_swap.yaml`; then delete the original from
`configs/assets/` (the legacy copy stays). Header:

```yaml
asset: eur_interest_rate_swap
instrument: IRSwap
match: {notional_currency: EUR}
currency: EUR
defaults: {<today's defaults>, curve: "estr:<ch>"}
market: {provider: eur_rates}
```

In `imports:`, keep what the pricing code uses and remove what only the market loading used. In
`code:`:

1. Delete the market-loading function and the helpers that moved.
2. Add:

   ```python
   from types import SimpleNamespace

   def curve_of(market, terms):
       """The record of the curve a trade prices on. `market` is the currency provider's dict."""
       key = terms["curve"]
       if key not in market:
           raise ValueError(f"no curve {key!r} in this market; available: {sorted(market)}")
       return market[key]

   def _with_env(m, env):
       """The same curve record on a derived environment (bumped, rolled, translated)."""
       return SimpleNamespace(**{**vars(m), "env": env})
   ```
3. In every helper, the first parameter that was the environment becomes the curve record `m`.
   Inside the helper, the environment is `m.env`, and every literal listed in section 4 becomes
   `m.<attribute>`.
4. Wherever the code builds a derived environment (a bumped, rolled or merged one) and passes it
   to another helper, pass `_with_env(m, derived_environment)` instead.
5. `resolve`: read the token with `key = str(kw["curve"]).strip().lower()`, get
   `m = curve_of(market, {"curve": key})`, and add `"curve": key` to the returned terms.
6. Expressions. In `functions:`, `trade:` and `attributes:`, replace the argument `market` by
   `curve_of(market, resolved)`; in `resolve:` pass `market` itself. A relative measure's
   `market_to` becomes `curve_of(market_to, ...)` the same way.
7. `portfolio_functions:` receive `trades` and `weights`, not `resolved`, and the trades of one
   call **may be on different curves**. Pass `market` (the dict) and look the record up per trade
   from the terms the config already attaches to each trade. Compute per trade, or per group of
   trades sharing a curve, and combine. Row labels come from the record.

**Gate A (text):** below the header, the asset file contains no currency code, index name, curve
name, time zone, snapshot time or environment-variable name that is specific to one currency.
Search for each literal you moved; every search must find nothing.

**Gate B (numbers):** in your copy of the script set
`NEW = dict(markets=["configs/markets"], assets=["configs/assets/eur_interest_rate_swap.yaml"])`,
`OLD` = the legacy copy, `IGNORED_RESOLVED_KEYS = {"curve"}`. It reports 0 differences.

**Gate C (checker):**
`python skills/pricebt-verify-asset-config/scripts/check_asset.py configs/assets/eur_interest_rate_swap.yaml --markets configs/markets --date <d1> --date <d2>`
has the same PASS/FAIL rows as the same command on the legacy copy (without `--markets`). Add
`--location <location>` to both when the location of section 0 is not `LDN`.

### Step 5. Curve selection

Check and report: `IRSwap(..., curve="ESTR:<CH>")` (upper case) resolves to the lower-case token;
an unknown token raises the `ValueError` above listing the available tokens; with no `curve`
keyword the default is used.

### Step 6. DV01 sizing

In `resolve`:

```python
target = kw.pop("dv01", None)          # take it out before the existing body reads kw
# ... existing body; when target is not None, build the terms for a notional of 1.0 ...
if target is not None:
    if float(target) <= 0:
        raise ValueError(f"dv01 must be positive (direction comes from pay_or_receive), got {target!r}")
    unit = <the function mapped to the IRDelta scalar>(m, <build the trade from terms>, terms)
    if not unit:
        raise ValueError("cannot size by dv01: the unit trade has zero delta")
    terms[<signed notional key>] *= abs(float(target) / unit)
```

Use the config's own function and key names. Scale every resolved term that is proportional to
notional, and none that is not. When `dv01` is given it defines the size and `notional_amount` is
ignored; say so in the config's `description`.

**Gate:** for a payer and a receiver, on two dates, `swap.calc(IRDelta(aggregation_level="Type"))`
under `PricingContext(trade_date)` equals `+dv01` (payer) and `-dv01` (receiver) to 1e-6 relative.
Resolve on the first date and price on the second: the resolved notional is unchanged (the size is
pinned at the trade date, not re-solved). Rerun Gate B: still 0 differences (no case there uses
`dv01`).

### Step 7. Finish

1. Search notebooks and scripts for the old file name and the old asset name; update them and add
   `markets=["configs/markets"]` to their `PricebtSession.use(...)` calls. When the location of
   section 0 is not `LDN`, also add
   `PricingContext.current = PricingContext(market_data_location="<location>")` once after their
   imports. List what you changed.
2. Do **not** add another curve or currency yourself. Report to the user the exact list of record
   attributes a new curve needs (the attribute names from section 4, with the value each has for
   the existing curve), and ask for those values for the next curve. Adding it is then: one new
   curve provider file, one entry in the currency provider's `requires:` and `expr`. A new
   currency also needs a copy of the asset file with only the header changed.

## 7. Guide: writing the curve provider

```yaml
schema_version: 1
market: eur_estr_<ch>
description: >
  <what the market is, where it comes from, snapshot time and time zone, which dates return None>
imports: |
  <only what the market loading and its helpers need>
code: |
  <the identifiers of this curve, as named constants>
  <the market-loading function and its helpers, moved unchanged>

  def record(d, location):
      if location != "<the location today's snapshot rule is for>":
          raise ValueError(f"no snapshot rule for market data location {location!r}")
      env = <market-loading function>(d)
      if env is None:
          return None
      return SimpleNamespace(env=env, <one attribute per identifier>)
expr: 'record(pricebt_date, pricebt_location)'
```

Rules:

1. **Move, do not rewrite.** The market-loading function keeps its body, its checks, its `None`
   returns and its raises exactly.
2. **Library start-up runs once per process.** Each config's `imports:` runs once per config, so
   start-up lines copied into several configs would run several times. Put only the lines with
   side effects (path set-up, session or add-in set-up) behind a process-wide flag; leave `import`
   lines outside it, because each config needs the names:

   ```python
   import sys
   if not getattr(sys, "_pricebt_library_ready", False):
       <the existing start-up lines, unchanged>
       sys._pricebt_library_ready = True
   ```
   Use the same flag name in the asset config.
3. **Location.** `pricebt_location` is the market data location of the pricing context, `"LDN"`
   unless the user sets another. A snapshot rule belongs to one location: accept that one and
   raise for anything else. Never return one location's market for another silently. If today's
   rule matches none of `LDN`, `NYC`, `HKG`, `TKO`, ask the user which name it should answer to.
   Everything that prices on the provider must then run under that location: `LOCATION` in the
   parity script, `--location` of `check_asset.py`, `PricingContext.current` in a notebook.
4. **Memoisation.** Keep any existing process-level cache of loaded markets. Its key must contain
   everything the result depends on.
5. **No instrument logic.** The provider knows nothing about swaps, notionals or measures.
6. **Check it alone** before touching the asset:

   ```python
   from pricebt.session import PricebtSession
   PricingContext.current = PricingContext(market_data_location="<location>")   # from pricebt.markets
   s = PricebtSession.use(markets=["configs/markets"])
   m = s.pricing._provider_market("eur_estr_<ch>", <business date>, None)   # a record
   assert s.pricing._provider_market("eur_estr_<ch>", <a Saturday>, None) is not m  # the "no market" sentinel
   ```
   The record's `env` must be the same kind of object today's market loading returns for that date.

## 8. Final report

State, with the command output for each: the result of every gate (A, B, C, steps 5 and 6); the
files created, moved and deleted; the list of record attributes and their values for the existing
curve; anything you could not verify; and the question for the user from step 7. Do not describe
a gate as passed unless you ran it and are quoting its output.
