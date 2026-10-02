# Deliberate mistakes

Each file is [`../meridian_usd_irs.yaml`](../meridian_usd_irs.yaml) (the whole strict `IRSwap` contract, so
each one loads) with ONE classic conversion error. The checker FAILs the row named below; the
same error can also break rows that read the broken value (a percent par rate also breaks `ir_taylor`,
`ir_cashflow_drop`, `ir_forward_price`, `ir_par_spread` and `swap_pv_identity`, which all read `IRFwdRate`; an
unpinned maturity also breaks `ExpiryInYears`, `ForwardPrice` and `FairPremium`), and nothing unrelated FAILs.
They are regenerated from the fixed example by patching only the `# MISTAKE` lines, the `asset:` name and the
`market.key`. The changed lines are
marked `# MISTAKE`. Each file has its own `asset:` name and `market.key`, so load each one in its own
`PricebtSession`, because all four match `IRSwap` in USD. The tests
(`tests/skills/test_skill_connect_example.py`) assert the wrong behaviour, so these stay wrong.

| File | The error | Symptom | How it is detected |
|---|---|---|---|
| [`dv01_sign_not_flipped.yaml`](dv01_sign_not_flipped.yaml) | `_flip` returns the vendor `DV01` / `BUCKET_DV01` as-is (receiver-positive, per 1bp down) | a payer swap has a **negative** `dv01` (the zero-curve DV01) and a negative `IRDelta` ladder and `CRIFIRCurve` (which still sum to each other). `IRDiscountDeltaParallel` rediscounts the cash flows, so it keeps its sign. The own-rate scalar keeps its sign: it is a ratio of DV01s times the `Annuity`, so the flip cancels. Ladder hedges and ladder-sized trades go the wrong way | `check_asset.py`: `swap_bucket_sum` FAIL (ladder and scalar of opposite sign), `ir_ladder_sum[IRDelta]` WARN |
| [`par_rate_in_percent.yaml`](par_rate_in_percent.yaml) | `par_rate` returns `PAR_PCT` (percent) but declares `unit: bp` | par rate ~100x too small (2.1 "bp" for a 2.1% swap). It does not equal the ATM strike ×1e4. Z-score and threshold triggers on `IRFwdRate` never fire | `check_asset.py`: `swap_par_rate_atm` FAIL (ATM `par_rate` != `resolved["fixed_rate"] * 1e4`); `swap_par_rate_unit` WARN ("could be percent") |
| [`maturity_not_pinned.yaml`](maturity_not_pinned.yaml) | `resolve` passes the `"10Y"` tenor through. Meridian resolves a tenor against the **pricing** market's spot | `resolved["termination_date"] == "10Y"` (a string). The held swap never ages: on every later date it is a fresh spot+10Y swap at the old strike, so dv01, P&L and the ledger drift | `check_asset.py`: `resolve_pins_terms` FAIL. By hand: price the same resolved trade 3 months later. Its dv01 equals that of an explicit swap maturing spot(later)+10Y |
