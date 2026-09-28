# Deliberate mistakes

Each file is [`../meridian_usd_irs.yaml`](../meridian_usd_irs.yaml) with ONE classic conversion error. The changed lines are
marked `# MISTAKE`. Each file has its own `asset:` name and `market.key`, so load each one in its own
`PricebtSession`, because all four match `IRSwap` in USD. The tests
(`tests/skills/test_skill_connect_example.py`) assert the wrong behaviour, so these stay wrong.

| File | The error | Symptom | How it is detected |
|---|---|---|---|
| [`dv01_sign_not_flipped.yaml`](dv01_sign_not_flipped.yaml) | `dv01` returns the vendor `DV01` as-is (receiver-positive, per 1bp down) | a payer swap has **negative** dv01. `IRDelta` scalar and bucketed have opposite signs. Risk-sized trades (`AddScaledTradeAction` on `IRDelta`) and hedges trade the wrong way | `check_asset.py`: `swap_dv01_sign` FAIL, `swap_bucket_sum` FAIL, `swap_pnl_explain` FAIL |
| [`par_rate_in_percent.yaml`](par_rate_in_percent.yaml) | `par_rate` returns `PAR_PCT` (percent) but declares `unit: bp` | par rate ~100x too small (2.1 "bp" for a 2.1% swap). It does not equal the ATM strike ×1e4. Z-score and threshold triggers on `IRFwdRate` never fire | `check_asset.py`: `swap_par_rate_atm` FAIL (ATM `par_rate` != `resolved["fixed_rate"] * 1e4`); `swap_par_rate_unit` WARN ("could be percent") |
| [`maturity_not_pinned.yaml`](maturity_not_pinned.yaml) | `resolve` passes the `"10Y"` tenor through. Meridian resolves a tenor against the **pricing** market's spot | `resolved["termination_date"] == "10Y"` (a string). The held swap never ages: on every later date it is a fresh spot+10Y swap at the old strike, so dv01, P&L and the ledger drift | `check_asset.py`: `resolve_pins_terms` FAIL. By hand: price the same resolved trade 3 months later. Its dv01 equals that of an explicit swap maturing spot(later)+10Y |
