# Strict IR measure contracts for IRSwap and IRSwaption (revision 3)

Status: specification for branch `v2-ir-required` (worktree `pricebt-req`), which is `v2-ir-risk` plus a merge of
`v2-pnl-explain`. Author: Opus session, 2026-10-01. The user's instruction: "i want to require all the irswap and
irswaptions related measures in the config", with full authority to make every related design and implementation
decision.

This revision **extends `IR_RISK_DESIGN.md`** (contracts §2, revision-2 rules R2-1..R2-32). Where the two conflict,
this document wins. Every MUST of `DESIGN.md` §2.1 still holds.

## R3-0. The rule

Today (DEV-I11, IR_RISK_DESIGN §2.1) a contract row is satisfied by a mapping **or** by a reasoned declaration under
`unsupported_measures:`.

For the classes **`IRSwap` and `IRSwaption`**, a contract row is satisfied **only by a mapping**. Call these the
*strict classes*. Rules for a strict class:

- **Every contract form must be provided by a mapping**, either directly or through a preset/fallback key (R2-10).
- **Declaring a contract measure is an error**, whether it is mapped or not. This applies to:
  - the measure itself;
  - any of its forms;
  - any preset or fallback that resolves to it (for example `IRDeltaParallel`, `IRGammaParallelLocalCcy`,
    `PnlExplainClose`).

  The error text says that `IRSwap`/`IRSwaption` configs must map every contract measure, and that
  `unsupported_measures:` cannot satisfy them. The R2-9 "mapping wins" warning therefore never applies to a strict
  class: it becomes this error.
- **Declaring names outside the contract** keeps today's handling: warnings for unknown or preset names, and no effect
  on loading.
- **The load error lists every gap.** It ends with a **paste-ready mapping skeleton** (`contracts.mapping_skeleton`)
  instead of an `unsupported_measures:` block:
  - a `functions:` stub per missing scalar or frame form, with the contract's unit and `expr: '...'`;
  - a `portfolio_functions:` stub per missing bucketed form;
  - the `risk_measures:` lines.

  The stub expressions are deliberately not valid Python (`'...'`). A config cannot load from the skeleton alone,
  because a strict contract forbids placeholders.
- **`UnsupportedMeasureError` can no longer arise for a strict class.** It still applies to `Bond`.
- **`Bond` is unchanged.** It keeps map-or-declare, R2-9 and the unsupported block.
- **Shared data constants in `contracts.py`.** The checker skill imports these and never keeps its own copy:
  - `ZERO_BY_CONVENTION = {"IRSwap": frozenset({IRVega, IRVanna, IRVolga, IRAnnualImpliedVol, IRAnnualATMImpliedVol, IRDailyImpliedVol, IRBasis, IRXccyDelta}), "IRSwaption": frozenset({IRBasis, IRXccyDelta})}`
    (names as strings; a single-curve or single-currency library may legally map these to a literal 0);
  - `SIMM_IR_TENORS = ("2w", "1m", "3m", "6m", "1y", "2y", "3y", "5y", "10y", "15y", "20y", "30y")`.
- **"Zero by convention" mappings stay legal and honest:**
  - vol measures on a swap (R2-8);
  - `IRBasis` for a single-curve library;
  - `IRXccyDelta` for a single-currency instrument.

  The contract text defines these values as 0, so a literal `'0.0'` function **is** the correct computation. A
  literal constant for any **other** contract measure is a FAIL in the checker skill (`ir_fake_constant`). The loader
  does not inspect expressions.

DEV id: **DEV-I11 amended**. Add the sentence "for IRSwap and IRSwaption a mapping is required (no declarations)" to
the DESIGN §11 row and to `DEVIATIONS.md`.

## R3-1. Contract additions (both strict classes)

The aim is "all the IRSwap- and IRSwaption-related measures". Add these rows to `CONTRACTS["IRSwap"]` and to
`CONTRACTS["IRSwaption"]`, after `Cashflows` and before the swaption's `ProbabilityOfExercise`. `Bond` gets none of
them (it already has its own `ParSpread`). All values are holder-signed and per unit trade, as in §2.2.

### New kind

`"notional_level": (frozenset({"bp", "pct", "decimal", "number"}), True)` (must be intensive).

### New rows

| Measure | Kind | Forms | Contract semantics |
|---|---|---|---|
| `ParSpread` | rate | s | The spread, in the declared rate unit, added to the floating leg's rate that makes `Price` zero. It does not depend on direction: payer and receiver of the same terms share it. With a single curve and matching leg schedules it equals `fixed_rate − IRFwdRate`. Swaption: the underlying swap's (strike minus forward). Dead instruments: continuous with the last live value (R2-7). The dead-swap convention `IRFwdRate = fixed_rate` gives 0. Intensive. |
| `FairPremium` | value | s | The premium, paid by the holder on the **premium settlement date**, that makes the instrument plus premium worth zero: `Price / DF(settlement)`, in ccy. The settlement date is the swaption's `premium_payment_date` if the library supports it and it is set; otherwise the library's spot date for the currency. A library with no spot lag (the toys) uses the pricing date, so `FairPremium == Price`. **DEV-I19.** |
| `ForwardPrice` | value | s | `Price` forward-valued to the instrument's expiry date, the same date `ExpiryInYears` counts to: a swaption's `expiration_date`, a swap's termination date (DEV-I17). That is `Price / DF(expiry)`, in ccy. On or after expiry: `Price`. **DEV-I19:** gs declares the unit `BPS` but documents "price at expiry in the local currency"; pricebt follows the docstring. |
| `PremiumCents` | notional_level | s | `Price / |notional|` in the declared unit. In `bp` this is gs's "premium in cents": 1 cent per 100 of notional = 1bp of notional. `|notional|` is the unit trade's absolute notional: the swaption's or swap's `notional_amount`. Intensive. **DEV-I19.** |
| `LocalAnnuityInCents` | notional_level | s | `Annuity / |notional|`, i.e. the PV of 1.0 per annum per unit of notional, holder-signed like `Annuity` (a 10y pay-fixed swap ≈ +8.5). Declare unit `decimal`, or `number` with `scale_with_quantity: false`. This number equals the PV in cents, per 100 of notional, of 1bp per annum. Intensive. **DEV-I19.** |
| `CompoundedFixedRate` | rate | s | The fixed rate (swaption: the strike) restated as an **annually compounded** rate: `(1 + K/f)^f − 1` for a fixed leg paying f times a year. An annual fixed leg gives K itself. It is a trade term, finite on every date. Intensive. **DEV-I19.** |
| `CRIFIRCurve` | table | frame | ISDA SIMM CRIF rows for IR curve delta, one row per ladder pillar. Required columns: `RiskType` (always `"Risk_IRCurve"`), `Qualifier` (the currency ISO code), `Bucket` (a SIMM currency volatility group as a string; `"1"` for regular-volatility currencies such as USD and EUR), `Label1` (SIMM tenor, lower case, one of `2w 1m 3m 6m 1y 2y 3y 5y 10y 15y 20y 30y`), `Label2` (sub-curve, e.g. `"OIS"`, `"SOFR"`, `"Libor3m"`), `Amount` (PV change for +1bp at that pillar, in `AmountCurrency`, holder-signed), `AmountCurrency`. `returns: frame` with `scale_columns` including `Amount`. **Identity: Σ Amount = Σ of the `IRDelta` bucketed ladder for the same instrument.** Dead instrument: an empty frame with these columns. **DEV-I19:** the gs server returns the full CRIF schema; pricebt requires this subset. |
| `PnlExplain` | value | s | Already served by the Phase-E mechanism (IR_RISK_DESIGN §8): a `returns: buckets` **portfolio function** in the `risk_measures:` scalar slot. It receives `market_to` and `pricebt_to_date` and returns risk-factor rows (`mkt_type` IR, IR VOL, ...), in ccy. Swaps: one IR row equal to `Price(market_to) − Price(market)`, plus a vol row of 0 (optional). `PnlExplainClose` resolves here. *Implementer: confirm how `_mapped_slots` classifies a buckets portfolio function in the scalar slot, and make the row's `forms` match. A `PnlExplain` key must satisfy the row.* |

### Frame data

```python
FRAME_COLUMNS["CRIFIRCurve"] = ("RiskType", "Qualifier", "Bucket", "Label1", "Label2", "Amount", "AmountCurrency")
FRAME_SCALE_COLUMNS["CRIFIRCurve"] = ("Amount",)
```

### Excluded, with reasons

These are the IR-relevant gs measures that are deliberately **not** in the strict contract. They go in a new
`contracts.EXCLUDED: Dict[str, str]` (name → reason). A test enumerates every `pricebt.risk` measure whose
`asset_class` is Rates or None, plus the `PnlExplain*` classes. It asserts that each one is in one of four places:
- the IRSwaption contract;
- a preset/fallback (`base_measure`) of a contract measure;
- `EXCLUDED`;
- an explicit non-IR list (FX, Equity, Commod, Credit asset classes are skipped by the filter).

That test is what makes "all" checkable.

| Name(s) | Reason |
|---|---|
| `DollarPrice` | engine maps it to `Price(currency='USD')` |
| `ResolvedInstrumentValues` | engine (resolution), not a config function |
| `PricePips` | FX quoting (pips) |
| `Description`, `Market`, `MarketData`, `MarketDataAssets` | non-numeric gs server metadata |
| `LightningDV01`, `LightningOAS` | bond analytics (`Bond` contract) |
| `BaseCPI`, `InflMaturityCPI`, `Infl_CompPeriod`, `InflationDelta`, `InflationDeltaParallel`, `InflDeltaParallelLocalCcyInBps` | inflation instruments |
| `PnlExplainLive`, `PnlPredictLive` | live market: `NotSupportedError` (DEV-M1) |
| `FairVarStrike`, `FairVolStrike` | variance swaps |
| `CrossMultiplier`, `FairPremiumInPercent` | FX |
| any `pricebt.risk` measure with a `base_name` whose base is in the contract | resolves to its base (R2-10) |

Adjust the list to what `pricebt.risk` actually exports. The test is the referee.

## R3-2. What changes where (file ownership for parallel work)

| Owner | Files |
|---|---|
| **A: contract core** | `src/pricebt/risk/contracts.py`, `src/pricebt/assets/config.py` (error composition only), `src/pricebt/errors.py` (if needed), `tests/test_contracts.py`, `tests/test_unsupported_measures.py` (move IRSwap declaration cases to `Bond`; add strict cases), `tests/test_registry.py` helper, `tests/test_docs_contract_tables.py`, docs: `docs/v2/IR_RISK_DESIGN.md` (new §000 "Revision 3" pointing here, plus "What was built" rows), `docs/v2/IR_STRICT_CONTRACT.md` (this file: add "as built" notes), `docs/v2/DESIGN.md` §11 (DEV-I11 amended, DEV-I19 new), `docs/v2/DEVIATIONS.md`, `docs/v2/ASSET_CONFIG_GUIDE.md` (contract tables and the strict rule), `docs/v2/DECISIONS_LOG.md` (append), `AGENTS.md`, `README.md` |
| **B: toys and core-test configs** | `tests/toylib/**` (`irrisk.py`, `swaption.py`, and `rates.py`, which may be edited now that the merge is committed), `tests/assets/**`, every non-skills test under `tests/` that builds an IRSwap/IRSwaption config (inline YAML) or relies on a declaration, `tests/test_toylib_ir.py` (known answers for every new toy function), `notebooks/**` |
| **C: ARBS** | `configs/assets/usd_sofr_ois_interest_rate_swap.yaml`, `tests/test_arbs_config_static.py`, `tests/test_live_arbs*.py`, `docs/v2/LIVE_ARBS_REPORT.md` |
| **D: skills** | `skills/**` (meridian example SDK and configs and mistakes, config templates, `check_asset.py`, `check_asset_ir.py`, `measures.py`, `attribution.py`, every SKILL.md and reference), `tests/skills/**` (fixtures, inline YAML, tests), `.claude/skills/**` (regenerate with `tools/sync_agent_skills.py`) |

**Rules for every agent:**
- Never run `git add`, `git commit`, `git stash`, `git checkout -- <file>` or `git reset`. The orchestrator commits
  per owner.
- Write LF line endings: use the Edit/Write tools, or `open(p, "w", newline="
")`. Never use `Path.write_text` on
  Windows, and never use heredocs for text with backslashes or backticks.
- Interim verification does not wait for owner A. Under the current contract, a config that maps every base row and
  has no `unsupported_measures:` block already loads, and extra mapped names are unrestricted. So check that:
  - the config loads;
  - every contract measure (the 19 old + 8 new) evaluates finite on two dates;
  - your known-answer tests pass.

  Only the strict-error tests and the new-row unit enforcement need A.

Agents share one worktree, and each edits only its own files. A test that fails because of another owner's in-flight
file is reported, not "fixed". The orchestrator integrates.

## R3-3. Implementation notes per owner

**A, the contract core.**
- Add `STRICT_CLASSES = frozenset({"IRSwap", "IRSwaption"})` and `is_strict(instrument)`.
- Add `mapping_skeleton(instrument, missing) -> str`, plus the new rows, the kind and the frame data.
- Add `EXCLUDED`.
- In `check(...)`, for a strict class:
  - a declaration never satisfies a requirement;
  - every declared contract measure, or a preset of one, is a problem;
  - a missing form is a problem whose text says "not mapped (IRSwap/IRSwaption require a mapping for every contract
    measure)".
- `assets/config.py` appends `mapping_skeleton` for strict classes and `unsupported_block` otherwise.
- Tests:
  - every strict class rejects each kind of declaration;
  - the error lists every gap at once;
  - the skeleton names every missing measure and form, with its unit;
  - Bond is unchanged (its existing tests keep passing);
  - the EXCLUDED-completeness test (R3-1);
  - one test per new row's unit/intensive/frame rule;
  - mutations: drop `IRSwaption` from `STRICT_CLASSES` → a named test fails; let a declaration satisfy a strict row
    → a named test fails.
- Also:
  - the generated guide tables (`test_docs_contract_tables.py`);
  - the gs parity test needs every `dev_id` cited in DESIGN §11 (add DEV-I19);
  - guards (`tests/guards/`) stay green: `contracts.py` lives in `risk/`, which may name rates vocabulary.

**B, the toys** (on `toylib.rates` + `toylib.irrisk`, and `toylib.swaption`).
- Also fix the known gamma bias (MERGE_NOTES_pnl_explain.md §4). `tr.gamma` is `(n₊+n₋−2n₀)/((p₊−p₋)/2)²`, about 10%
  low at 10y ATM. Make it the chain-rule second derivative (the same formula as `toylib.irrisk.ir_gamma`, or call
  it). Re-baseline the hand-rolled check in `tests/skills/test_skill_swap_pnl.py` T-GAMMA-1 to the same formula: that
  file is owner D's, so send D the exact formula and D edits it. `check_asset_ir`'s `ir_gamma_ratio` on
  `toy_usd_irs` should then PASS.
- New functions:
  - `par_spread`;
  - `fair_premium` (no spot lag, so `= npv`);
  - `forward_price` (`npv / DF(expiry)`; the toy DF of a past date is >1, so use `npv` on or after expiry);
  - `premium_cents` (`npv / |N| · 1e4`, unit bp);
  - `local_annuity_in_cents` (`annuity / |N|`, unit decimal);
  - `compounded_fixed_rate` (the toy fixed leg is annual, so K in bp);
  - `crif_ir_curve` (a frame from the single-trade delta ladder: Qualifier = ccy, Bucket "1", Label1 = pillar in
    lower case, Label2 "OIS");
  - `pnl_explain` (exists for the full toys).
- Swaption equivalents, with the strike and the underlying.
- Configs:
  - Every IRSwap/IRSwaption config under `tests/assets/` maps the **whole** strict contract: `toy_usd_irs.yaml`,
    `toy_eur_irs.yaml`, `toy_usd_irs_full.yaml`, `toy_usd_swaption.yaml`, and any other.
  - `toy_usd_irs` keeps its annuity `dv01` as the `IRDelta` scalar (documented: at-the-money-exact, R2-1) and its
    in-flight P&L functions. It maps `Theta` to the one-day translated-curve theta (`tri.theta_1d`).
  - `toy_eur_irs` loses its role as the "explain not supported" negative case: under the strict rule an IRSwap config
    without gamma cannot exist. That negative case becomes a load-time test of a minimal inline IRSwap config.
    Coordinate with owner D, who owns `tests/skills/test_skill_swap_pnl.py::test_t_missing...`.
- Known-answer tests: each new toy measure is compared against an independent computation in the test:
  - `ParSpread == K − par` (single curve, annual both legs in the toy);
  - `PremiumCents == Price/|N|·1e4`;
  - `ForwardPrice·DF(expiry) == Price`;
  - `Σ CRIF Amount == Σ ladder`;
  - payer/receiver symmetry (ParSpread and CompoundedFixedRate equal, the signed ones opposite);
  - scaling (quantity 2.5: the extensive ones ×2.5, the intensive ones unchanged).
- Mutations listed per test.
- Inline IRSwap configs in non-skills tests: give them full mappings. For tests that are not about measures, a shared
  test helper `tests/ir_contract_stub.py` may generate the boilerplate. It is test-only, documented as such, and the
  shipped and skills configs never use it.

**C, ARBS** (live reads authorised: store-only, within the rails of `docs/v2/PNL_EXPLAIN_PLAN.md` §1.1 and
`tests/test_live_arbs.py`).
- **Order of work:**
  1. Implement and live-verify the uncertain measures first: `Cashflows` (the drop-day shift),
     `IRDiscountDeltaParallel` (two-curve npv), the `IRGamma` ladder (Solver gamma diagonal), per-day `Theta` with
     its cash term, and `PnlExplain` under `CloseMarket`.
  2. Report status to the orchestrator.
  3. Then do the easy identities.
- **Under the strict rule there is no escape hatch.** If a measure is genuinely blocked, the only legal outcome is an
  honest, documented approximation: a limitation row in `LIVE_ARBS_REPORT.md` plus a checker WARN. Never a constant
  outside `ZERO_BY_CONVENTION`.
- Also make the T2 `gamma` the chain-rule second derivative (MERGE_NOTES §4).
- Run the live file only after owner A's `src` changes land: ask the orchestrator. Until then, verify by loading the
  config and evaluating functions directly in a scratch script under the live env var.
- Map every strict-contract measure in the ARBS config:
  - Delete its `unsupported_measures:` block (it would now be an error).
  - `IRDelta` scalar stays the annuity `dv01` (unchanged, documented as at-the-money-exact; changing it would change
    the existing 040304 notebook's sizing).
- New ARBS functions:
  - `IRDiscountDeltaParallel`: a discount-curve-only ±1bp shift, keeping the projection forwards (rateslib
    `curves=[forecast, discount]`);
  - `IRGamma`: a diagonal ladder from the existing `_risk_model` (Solver gamma diagonal), pillars as in
    `delta_ladder`;
  - `IRVega`/`IRVanna`/`IRVolga`/vol levels: 0.0 or `{}`;
  - `IRBasis`: 0.0 (single curve, documented);
  - `IRXccyDelta`: 0.0;
  - `IRSpotRate`: the par rate of the spot-starting swap with the same termination date;
  - `Theta`: per day, own par rate fixed, curve translated one day, plus the cash `npv` drops in (t, t+1d]. Reuse T2's
    translated-curve code; never map the per-year `IRTheta` function to `Theta`;
  - `ExpiryInYears`: to the termination date;
  - `Annuity`: holder-signed, `1e4 × pv01`, payer > 0;
  - `Cashflows`: the flows `npv` will drop. ARBS `npv` keeps a coupon **on** its payment date and drops it the day
    after (T2 note; MERGE_NOTES §4), so shift accordingly. Columns per contract; `scale_columns: [payment_amount]`;
  - `PnlExplain`: a portfolio function, IR row = `npv(market_to) − npv(market)` per trade times weight;
  - the seven R3-1 measures.
- Live tests (opt-in `PRICEBT_LIVE_ARBS=1`, lazy imports only, dates in 2024): every new function against its
  identity:
  - `PremiumCents == Price/|N|·1e4`;
  - `LocalAnnuityInCents == Annuity/|N|`;
  - `ForwardPrice·DF(term) == Price` using the curve's DF;
  - `FairPremium·DF(spot) == Price`;
  - `ParSpread ≈ K − par` within 0.5bp (ARBS legs: annual fixed vs annual compounded SOFR);
  - `Σ CRIF == Σ delta_ladder` to 1e-9 relative;
  - `IRGamma` diagonal sum compared with `IRGammaParallel` (record the ratio; cross terms make them differ);
  - `IRDiscountDeltaParallel` compared with `dv01` (record);
  - `Theta·365` against T2's `IRTheta` (they differ only by the cash term and FD details: record);
  - `Cashflows` over a coupon date: the flow leaves the frame on exactly the step where `npv` drops;
  - `PnlExplain` IR row `== Δnpv` for a fixed trade across two dates;
  - `check_asset.py` on the ARBS config: no FAIL.
- Record every number in `LIVE_ARBS_REPORT.md` under a new "Strict contract (R3)" section.
- `tests/test_arbs_config_static.py` must pass without ARBS: it parses the YAML only.

**D, skills.**
- Make every IRSwap/IRSwaption config under `skills/` and `tests/skills/fixtures/` map the whole strict contract:
  - the meridian example and its three mistakes;
  - `config-template.yaml` and `config-template-swaption.yaml`;
  - every `check_asset` fixture.

  The meridian SDK is **fictional and ours**. Add whatever SDK methods a real enterprise library would expose
  (bucketed risk, cashflows, discount factors), keeping the "fictional" header and the enterprise shape. Or compute
  in config `code:` by bump-and-reprice through `Client.price`.
- Each mistake/fixture still demonstrates exactly its one error, and the checker still FAILs exactly its row.
- `check_asset_ir.py`:
  - new rows for the R3-1 identities (each a known-answer relation; FAIL on sign or identity break, WARN on
    tolerance);
  - `ir_fake_constant`: a literal-constant expression mapped to a contract measure outside the zero-by-convention set
    → FAIL;
  - its contract row must describe the strict rule (no "declared" state for strict classes).
- `measures.py` (capability matrix, paste-ready block) prints the mapping skeleton for strict classes.
- `attribution.py`: any "declared unsupported" message paths for swaps now become load errors; update.
- Docs to update:
  - every SKILL.md and reference that tells an agent it may "declare unsupported" for a swap or swaption must now
    say it must map (Bond may still declare): connect-pricing-library, risk-measures (contract table plus the seven
    new rows and the EXCLUDED list), asset-config-cookbook (recipes for the seven new measures, plus error catalogue
    entries for the strict-rule errors), verify-asset-config (new rows), pnl-attribution, start-here, architecture;
  - `tests/skills/test_skill_swap_pnl.py`: T-MISSING becomes "a minimal inline IRSwap config without
    gamma/theta fails to load, naming them"; `_SLOPED_YAML` maps everything (reuse `toylib.irrisk`).
- Regenerate `.claude/skills` and keep `tests/skills` green.

## R3-4. Gates

1. The full suite is green: `PYTHONPATH="src;tests" C:/Users/chris/anaconda3/envs/stir/python.exe -m pytest tests -o addopts= -p no:cacheprovider -q`.
2. `tools/sync_agent_skills.py --check` passes.
3. `check_asset.py` has no FAIL on every shipped/toy IRSwap/IRSwaption config.
4. The live ARBS file for R3 is green with `PRICEBT_LIVE_ARBS=1` (owner C runs it).
5. Every new rule has a named mutation that makes a named test fail.
6. No CRLF in touched files.
7. Guards are green, and `src/pricebt` imports no library.
