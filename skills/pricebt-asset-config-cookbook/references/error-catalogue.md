# pricebt error catalogue (asset configs, sessions, pricing)

The message fragments below are copied from `src/pricebt/assets/*.py` and `src/pricebt/errors.py`. Every `ConfigError` carries `asset` and `key` attributes and, where possible, a did-you-mean suggestion.

## At load (`load_asset`, `PricebtSession.use`)

| Message contains | Cause | Fix |
|---|---|---|
| `unknown key 'X'` | a typo, or a key from another schema (e.g. `pricing:` instead of `functions:`) | use the suggested key; the schema is in `docs/v2/ASSET_CONFIG_GUIDE.md` |
| `syntax error at line N` | an expression is not valid Python | fix the quoted snippet; YAML quoting often eats `\` or `'`, so prefer single-quoted YAML strings |
| `unit 'X' is not one of [...]` | a unit outside the closed set | `ccy`, `ccy_per_bp`, `ccy_per_bp2`, `bp`, `pct`, `decimal`, `number`, `date` |
| `must include 'Price'` | `risk_measures` lacks `Price` | map `Price: npv` (the engine books cash with it) |
| `references unknown function 'f'` | a `risk_measures` entry names a function that does not exist | declare it under `functions:` or `portfolio_functions:` |
| `collide with the reserved keys` | a function or attribute is named `market`, `trade`, `kwargs`, … | rename it |
| `declared in both functions and portfolio_functions` | the same name in both | keep one |
| `size_attribute 'x' is not a key of attributes` | `size_attribute` does not name an attribute | add the attribute or fix the name |
| `must be 1, got` (schema_version) | wrong or missing version | `schema_version: 1` |
| `duplicate mapping key` | a repeated YAML key (the loader is strict) | delete the duplicate |
| `duplicate asset name` | two configs with the same `asset:` id | make the ids unique |
| `market key K: ... differs between assets` | two assets share `market.key` but their imports, code or market expression differ | give each asset its own key |

## At first evaluation (`AssetEvaluationError`)

`asset 'A' key 'K' expr '...' date=D csa=C: <OriginalError>: <message>`. The key tells you where to look:

| key | Typical cause |
|---|---|
| `imports` | the library is not installed or not on `sys.path`; a missing environment variable. The error is cached, so fix it and restart the process. |
| `code` | an exception in module-level code (client connection, file not found) |
| `market` | the market loader raised instead of returning `None` (holiday, no data) |
| `resolve` | kwargs the parser does not accept (`'100k'`, `'=solvefor(...)'`), a missing default |
| `trade` | the resolved terms don't match the library's builder |
| `functions.<name>` / `portfolio_functions.<name>` | a wrong method name or argument order, or a measure the library does not support |
| `attributes.<name>` | the attribute reads a key that `resolve` does not produce |

## At pricing

| Message contains | Cause | Fix |
|---|---|---|
| `no asset matches IRSwap({...})` | no registered asset's `match:` fits these kwargs | register the right config, fix `match:`, or pass `pricebt_asset=` |
| `ambiguous: IRSwap matches [...]` | two configs match | tighten `match:` or pass `pricebt_asset=` |
| `unknown asset 'x'; registered: [...]` | `pricebt_asset=` names an unregistered config | add it to `PricebtSession.use(assets=[...])` |
| `has no mapping for risk measure X` | the strategy requests a measure the config does not map | add `X:` under `risk_measures` (presets such as `IRDeltaParallel` fall back to `IRDelta`) |
| `has no scalar mapping` / `has no bucketed mapping` | `IRDelta(aggregation_level='Type')` needs `scalar`; a bare `IRDelta` needs `bucketed` | add the missing form |
| `sets bump_size; pricebt passes only aggregation_level and currency` | a gs measure parameter pricebt cannot honour | drop the parameter, or compute that variant as its own function |
| `has unit bp; it cannot be converted to USD` | a currency conversion was requested for a rate measure | request it without `currency=`, or give the function a currency unit |
| `no FX config: cannot convert` | a mixed-currency book or `result_ccy` without an FX config | `PricebtSession.use(..., fx=...)` |
| `no FX rate` (`MarketDataUnavailable`) | the FX config returned `None` or a value ≤ 0 | fix the FX data for that date |
| `MarketDataUnavailable(asset, date)` | a market was required on a date with `None` | check the envelope and holidays; the engine's `missing_market='drop'` covers grid dates |
| `is not hashable plain data` / `is not plain data` | `resolve` returned a library object, a list or a dict | return plain values (str/int/float/bool/None/date/tuple) |
| gs `Cannot aggregate cash in multiple currencies` | a mixed-currency book without `result_ccy` | `run_backtest(result_ccy="USD")` plus an FX config |
| gs `cannot hedge in a different currency` | the hedge risk measure has no `currency` for a cross-currency hedge | `IRDelta(aggregation_level='Type', currency='USD')` |
