# 13 — The agent skills library (`skills/`): inventory, IRSwap-only assumptions, and what the IR pricing-and-risk port needs from it

> **Key facts**
> 1. The library is **14 skills** (`skills/pricebt-*/SKILL.md`), 9 scripts, 1 fictional SDK example, 2 index files (`skills/README.md`, `skills/CONVENTIONS.md`), a generator (`tools/sync_agent_skills.py`) that mirrors 14 **git-tracked** stubs into `.claude/skills/`, and **219 tests** in `tests/skills/` (collected with `pytest --collect-only`, 2026-09-28).
> 2. **Every worked example, template, fixture and test is an `IRSwap`** (toy swap, Meridian swap, ARBS swap). The only non-swap asset any skill test touches is `tests/assets/toy_usd_swaption.yaml`, in one recipes test (`tests/skills/test_skill_recipes.py:243-253`, NAV sizing). There is no bond anywhere: `pricebt.instrument` has no `Bond` class (`src/pricebt/instrument/_gs_fields.py` has 5 classes).
> 3. The whole library assumes a **fixed measure set** — `Price`, `IRDelta` (scalar + ladder), `IRDeltaParallel`, `IRFwdRate` — and a **fixed function vocabulary** — `npv`, `dv01`, `par_rate`, `delta_ladder`. The name `par_rate` is hard-coded in `run_study.py:66`, `check_asset.py:446`, the spec template (`strategy_spec.yaml:45`) and the question bank (`question-bank.md:15`).
> 4. `check_asset.py` runs **one** asset-class pack, selected by the exact string test `if cfg.instrument == "IRSwap"` (`check_asset.py:577-578`). The pack hard-codes `pay_or_receive` (`:395-396`), `fixed_rate` (`:405`, `:467`), `effective_date`/`termination_date` (`:424`), `IRDelta`/`IRFwdRate`/`Price` (`:391`) and the size kwarg `notional_amount` (`SIZE_KWARG`, `:82`). Running it on the toy swaption gives **16 PASS / 1 WARN / 0 FAIL / 1 SKIP** with **no rates pack at all** (§5.7). Nothing in the checker knows what an `IRSwaption` or a `Bond` must map.
> 5. **P&L attribution is single-factor everywhere**: `spot_check.check_pnl_explain` is `corr(ΔTotal, risk[t-1]·Δrate)` (`spot_check.py:161-183`), `check_asset.swap_pack` is `dv01·Δpar` (`check_asset.py:476-488`), the tearsheet has one scalar "risk" column (`tearsheet.py:98-105`, `:300-312`). **No skill mentions `PnlDefinition`/`PnlAttribute`/`BackTest.pnl_explain()`**, although pricebt already ports them (`src/pricebt/backtests/backtest_objects.py:88-118`, `:404-450`; `generic_engine.py:289`).
> 6. **Finite-difference parameters are documented only as "they raise"** (`pricebt-port-gs-notebook/SKILL.md:22`, `pricebt-asset-config-cookbook/references/error-catalogue.md:45`), matching DEV-I8 (`src/pricebt/assets/pricing.py:356-367`). No skill, template or check covers `bump_size`, `finite_difference_method`, `scale_factor`, `local_curve` or `mkt_marking_options`.
> 7. **Portfolio / PortfolioRiskResult are not taught anywhere.** `PortfolioRiskResult` appears only in the architecture diagram (`pricebt-architecture/SKILL.md:23`); `Portfolio` is otherwise named only as a hedge priceable (`pricebt-strategy-recipes/references/construct-cheatsheet.md:57`, "one instrument or a Portfolio") and via `PortfolioTrigger` (`:36`). No skill shows `Portfolio.calc`, `PRR.aggregate`, `to_frame`, indexing by measure/name/date, or `HistoricalPricingContext`.
> 8. Governance that any new skill must satisfy is **test-enforced** (`tests/skills/test_skills_library.py`): frontmatter `name` == directory, description 40–1024 chars, SKILL.md < 350 lines, README lists it, every backticked repo path exists, relative links resolve, no absolute user paths, example SDKs say "fictional", and the `.claude/skills` mirror is in sync (`--check`).
> 9. One doc defect: the connect skill says a par rate left in percent is "only a WARN" (`pricebt-connect-pricing-library/SKILL.md:110`, `:217-218`), but `swap_par_rate_atm` FAILs it and a test asserts that FAIL (`tests/skills/test_skill_check_asset.py:124-133`).

Scope: everything under `skills/`, `tools/sync_agent_skills.py`, `tests/skills/`, and the `.claude/skills/` mirror, in the worktree `pricebt-ir` (branch `v2-ir-risk`). Purpose: decide how the library must change so an agent can wire **any** external pricing/data library into pricebt for the gs IR pricing-and-risk surface (IRSwap, IRSwaption, Bond; the gs IR measure catalogue; finite-difference parameters; Portfolio/PortfolioRiskResult; P&L decomposition).

Citation roots: pricebt paths are repository-relative. `GS2` = `C:/Users/chris/clee/gsquant-temp-claude/gs-quant/gs_quant` (2.1.17 checkout); `GS1` = `C:/Users/chris/anaconda3/Lib/site-packages/gs_quant` (1.5.4); `GSDOC` = `GS2/documentation`. Line numbers are file-local (re-derived with `grep -n` per file).

---

## 1. Method and evidence

- Read in full: every `SKILL.md`; `skills/README.md`, `skills/CONVENTIONS.md`; `check_asset.py`, `spot_check.py`, `record_replay.py`, `recipes.py` (main sections), `run_study.py` (main sections); every reference of the verify, connect and cookbook skills; `tools/sync_agent_skills.py`; `tests/skills/test_skill_check_asset.py`, `test_skills_library.py`, the `check_asset` fixtures. Grepped the rest for instrument, measure, direction and unit assumptions.
- Ran (read-only, `PYTHONDONTWRITEBYTECODE=1`):
  - `python tools/sync_agent_skills.py --check` → `in sync (14 skills)`.
  - `pytest tests/skills --collect-only` → 219 tests (per file: check_asset 24, connect_example 14, recipes 21, record_replay 4, research_stats 9, robustness 8, run_study 3, spec 28, spot_checks 10, tearsheet 4, skills_library 94).
  - `python skills/pricebt-verify-asset-config/scripts/check_asset.py tests/assets/toy_usd_swaption.yaml --date 2024-01-03 --date 2024-02-05` → exit 0, result in §5.7.
- `git -C pricebt-ir ls-files .claude` lists the stubs: the mirror is **committed**, so any new or renamed skill changes tracked files under `.claude/skills/`.

---

## 2. Library layout and governance

### 2.1 What the index files promise

- `skills/README.md:3`: "The library assumes you know **your** pricing and data library ... and that you do not know pricebt." Two journeys: A (library → first backtest: start-here, architecture, connect, verify, cookbook, enterprise, port-gs-notebook) and B (idea → tearsheet: workflow, intake, recipes, adversarial-review, spot-checks, tearsheet, research-methodology).
- `skills/README.md` "Honest limits": "Nothing here knows your bank library's real API ... The example library (`meridian_sdk`) is **fictional**."
- `skills/CONVENTIONS.md`: layout (`SKILL.md` + optional `references/`, `scripts/`, `templates/`, `example/`); SKILL.md body order (purpose; when to use/not; inputs/outputs; procedure; checks; pitfalls; related); "Keep a SKILL.md under about 300 lines"; scripts expose functions **and** an argparse CLI, are imported with `sys.path.insert(0, "skills/<skill>/scripts")`, "never import a vendor library", and may use matplotlib/scipy/jinja2; every script/recipe/example is executed by `tests/skills/test_skill_<name>.py`; no secrets, no real vendor APIs claimed, books cited as `(Author, Title, ch. N, p. M)`.

### 2.2 Test-enforced rules (`tests/skills/test_skills_library.py`)

| Rule | Where | Consequence for new IR skills |
|---|---|---|
| The 14 current skill names must exist (a superset is fine) | `:29-37` | add new names to `expected` if they become mandatory |
| `name` == directory; description is a str of 40–1024 chars | `:40-47` | trigger text for new skills must fit 1024 chars |
| SKILL.md has < 350 newlines | `:48-49` | the connect SKILL.md is already 241 lines: swaption/bond material must go to `references/` or new skills |
| Every relative Markdown link resolves (code fences skipped) | `:52-73` | |
| Every backticked path starting `src|tests|skills|docs|configs|tools|notebooks/` exists (placeholders with `<>*{}` and `reports/` skipped) | `:18`, `:76-85` | citing a not-yet-written fixture (`tests/assets/toy_usd_bond.yaml`) fails until the file exists |
| README lists every skill as `(<name>/SKILL.md)` | `:88-91` | |
| No `C:\Users\`, `/c/users/`, `/home/<x>/` in `skills/**` (.md/.py/.yaml) | `:19`, `:94-101` | |
| Every `skills/**/example/*/__init__.py` says "fictional" in its first 400 chars | `:104-107` | a fictional swaption/bond SDK must say so |
| `.claude/skills` mirror in sync (`sync_agent_skills.py --check` exit 0) | `:110-112` | rerun the generator after any frontmatter change |
| `--check` really detects drift (non-vacuity) | `:115-125` | |

---

## 3. `tools/sync_agent_skills.py`

- **What it syncs:** source `ROOT/skills/*/SKILL.md` (`:19-21`, `:45`); target `ROOT/.claude/skills/<name>/SKILL.md` (`MIRROR`, `:21`), where Claude Code discovers project skills (`:3-4`).
- **Stub content** (`stub_text`, `:34-40`): YAML frontmatter with the same `name` and `description` (dumped with `yaml.safe_dump(..., sort_keys=False, allow_unicode=True, width=10_000)`), the marker `<!-- generated by tools/sync_agent_skills.py from skills/{name}/SKILL.md - do not edit; edit the source and re-run -->` (`:22`), and one pointer line: "This skill lives at `skills/{name}/SKILL.md` ... Library index: `skills/README.md`."
- **Frontmatter parser** (`read_frontmatter`, `:25-31`): the file must start with `---\n`; raises `ValueError` otherwise.
- **Default mode** (`main`, `:65-71`): deletes mirror directories whose name is no longer a skill (`shutil.rmtree`), then writes every stub with `newline="\n"`; prints `wrote N stubs to <MIRROR>`.
- **`--check`** (`:58-64`): `drift = sorted(set(want) ^ set(have) | {n for n in want if n in have and want[n] != have[n]})` — any missing, extra or content-different stub; prints `out of date: <names>` and returns **1**, else prints `in sync (N skills)` and returns 0. Only the name and description reach the stub, so body edits never cause drift; **a changed `description:` (the trigger text) always does.**
- Tests: `test_claude_mirror_is_in_sync` (subprocess `--check`) and `test_sync_check_detects_drift` (monkeypatched `MIRROR` to `tmp_path`, stale stub → exit 1) (`test_skills_library.py:110-125`).

---

## 4. Per-skill inventory

Column "IR-scope assumptions" lists every place that assumes swaps only / `IRSwap` only / the fixed measure set / no swaptions / no bonds / no portfolio-level measures / no P&L attribution.

### 4.1 `pricebt-start-here` (43 lines; no other files; no dedicated test)
- **Purpose:** router for journeys A and B.
- **Trigger:** "Entry point for any AI agent working in the pricebt repository - routes you to the right skill for connecting a bank pricing/data library (Citi Datapoint-style, JPM Athena-style, in-house), running your first backtest, porting gs_quant notebooks, or taking a strategy idea through intake, implementation, adversarial review, spot checks and a tearsheet. Use first, whenever you are new to this repository or unsure which skill applies."
- **IR-scope assumptions:** no route for "price/risk a portfolio", "explain P&L", "add a swaption/bond asset". Step A3 sends everyone to `check_asset.py` (`:17`).

### 4.2 `pricebt-architecture` (84 lines; no files; no dedicated test)
- **Purpose:** mental model, MUST rules, engine semantics that change numbers.
- **Trigger:** "The mental model of pricebt v2 — a gs_quant.backtests port whose every number comes from YAML asset configs — plus the rules you must never break and the engine semantics that change results. Read before touching pricebt code, writing an asset config, or interpreting a backtest."
- **IR-scope assumptions:**
  - `:23` names `Portfolio`, `PortfolioRiskResult` only in the diagram; no usage anywhere.
  - `:37` MUST-4 "Risk is in currency per 1bp; rates are in bp." No vol units (bp normal vol vs % lognormal), no bond price units (per 100), no second-order units (`ccy_per_bp2`).
  - `:38` MUST-5 names swaptions as "a new config, never engine code" (correct), but nothing tells the agent **which measures** that config must map.
  - `:48` coupons not booked "for multi-year swap holds" — for bonds this is the dominant P&L term (carry), not a footnote.
  - `:64` lists `Price`, `IRDelta`, `IRDeltaParallel` only.

### 4.3 `pricebt-connect-pricing-library` (241 lines)
- **Purpose:** fast path from "I have a library" to a checked config and a 3-month smoke backtest; discovery questionnaire; worked example against the fictional Meridian SDK.
- **Trigger:** "Fast path from "I have a bank pricing/data library (a Citi Datapoint/DP-style or JPM Athena-style platform, or an in-house one)" to a running pricebt backtest. Use it when you need to write a pricebt asset config against a new library, work out its units and signs, or connect a library for the first time."
- **Files:** `references/config-template.yaml` (116 lines, "vanilla fixed-float swap shape", `:1`), `references/convention-conversions.md`, `references/discovery-questionnaire.md`, `example/README.md`, `example/meridian_sdk/__init__.py` (fictional SDK, `MEASURES = ("PV", "PAR_PCT", "DV01", "BUCKET_DV01", "FIXED_PCT")`, `:57`), `example/meridian_usd_irs.yaml` (137 lines), `example/mistakes/{dv01_sign_not_flipped,maturity_not_pinned,par_rate_in_percent}.yaml` + README.
- **Scripts:** none (the SDK is a fixture library: `connect`, `Client.market/spot_date/maturity/swap/price`, `Client.calls` counter).
- **Tests:** `tests/skills/test_skill_connect_example.py` (14 tests: SDK raises on weekend/holiday/gap; receiver-positive DV01 and one batched call; config loads without touching the SDK; blank template loads; ATM payer at par, payer dv01 > 0, par rate in bp, `ATM+25`; seasoned trade keeps maturity; ladder sums to dv01 in one vendor call; 3-month backtest Total identity; each mistake reproduces its wrong behaviour). Cross-skill: `test_skill_check_asset.py:120-133` runs the checker on Meridian and its mistakes.
- **IR-scope assumptions:**
  - Step-3 mapping table (`SKILL.md:65-73`) is `resolve_swap`/`build_swap`/`_risk(PV,DV01,PAR_PCT)`/`delta_ladder` only.
  - Step 5 expectations (`:84-85`) and Definition of Done (`:203-206`): ATM payer npv ≈ 0, dv01 > 0 ≈ 900/1mm, `fixed_rate*1e4 == par_rate`, ladder sums to dv01.
  - Section C conversions (`:152-165`) are swap/rate/dv01 only; no vol, vega, gamma, theta, price-per-100, accrued, yield, duration, convexity.
  - Template: `instrument: IRSwap` (`config-template.yaml:10`), swap defaults (`:13-18`), functions `npv/dv01/par_rate` (`:98-101`), `risk_measures: Price, IRDelta, IRFwdRate` (`:113-116`).
  - Questionnaire §5 (`discovery-questionnaire.md:46-57`): "For **each** pricebt function you need (`npv`, `dv01`, `par_rate`, plus a ladder)" (`:48`); §6 bucketed delta only (`:59`). No questions on vol surface/model, option exercise/settlement, `buy_sell`, straddles, gamma/vanna/volga availability, analytic vs bumped risk, bump controls, bond identifiers/static data, clean vs dirty, accrual, yield conventions, repo/financing.
  - `convention-conversions.md:7-19` "What pricebt expects" is swap-only; the self-test (`:51-57`) is payer-swap-only.
  - **Defect:** `SKILL.md:110` "a par rate left in percent is only a WARN" and `:217-218` "The checker only WARNs on this" contradict `swap_par_rate_atm` (FAIL) and the test asserting that FAIL (`test_skill_check_asset.py:124-133`). The mistakes README states it correctly (`example/mistakes/README.md:11`).

### 4.4 `pricebt-verify-asset-config` (136 lines) — see §5 for the script
- **Purpose:** automated "zero to confidence" checker plus manual independent validation.
- **Trigger:** "Automated "zero to confidence" checker for a newly written or changed pricebt asset config - loads it, evaluates the market, resolve, every function and risk measure, runs a smoke backtest, and (for IRSwap) a rates sanity pack (ATM npv, dv01 sign/size, par-rate units, ladder sum, PnL explain); prints a PASS/WARN/FAIL table. Use after writing or editing any asset config and before any strategy work, or when numbers from a config look wrong."
- **Files:** `scripts/check_asset.py` (618 lines), `references/independent-validation.md`, `references/known-answer-probes.md`.
- **Tests:** `tests/skills/test_skill_check_asset.py` (24) + fixtures `tests/skills/fixtures/check_asset/{bad_dv01_sign,bad_par_rate_decimal,bad_unpinned_maturity,bad_weekend_raises}.yaml` and `check_asset_fixture_lib.py`.
- **IR-scope assumptions:**
  - Symptom table (`SKILL.md:72-103`): 10 of 30 rows are `swap_*`; `:88` tells the agent to use "the gs name (`IRDelta`, `IRFwdRate`, ...)".
  - `:111` "Rates pack coverage. Only `instrument: IRSwap` gets the pack. Other asset classes get the generic checks".
  - `:125` "SKIP is not PASS. A swap config with no `IRDelta` mapping skips most of the rates pack".
  - `independent-validation.md:25` (`from pricebt.instrument import IRSwap`), `:34` (`for m in (Price, IRDelta(aggregation_level="Type"), IRFwdRate)`), tolerance table `:53-59` (PV, dv01, par rate, ladder only).
  - `known-answer-probes.md:16` (`payer = IRSwap(...)`), probes 1–9 (`:25-33`) are swap probes (par PV ≈ 0, payer/receiver symmetry, dv01 vs bump, roll-down sign, par rate independent of direction).

### 4.5 `pricebt-asset-config-cookbook` (61 lines)
- **Purpose:** copy-paste patterns by library shape, unit/sign tables, error catalogue.
- **Trigger:** "Copy-paste patterns for pricebt asset configs by library shape (service with market handles, in-process library with local curve files, multi-part markets with vols), plus resolve/pinning, unit and sign conversion, batching portfolio functions, attributes, CSA routing, multi-currency and FX configs, and a catalogue of pricebt error messages with their fixes. Use while writing or debugging an asset config."
- **Files:** `references/patterns.md` (13 patterns), `references/error-catalogue.md`.
- **Tests:** none dedicated (only the library-integrity tests).
- **IR-scope assumptions:**
  - Pattern 3 (`patterns.md:51-65`) shows a curve+vol market and a single `swaption_pv` npv line (`:62`) — the only swaption content; no swaption resolve, no vega/gamma, no bucketed vol cube.
  - `patterns.md:111`: "For options: fold `buy_sell` into a signed notional in `resolve`, because pricebt never reads `buy_sell` itself" — correct, one line.
  - Pattern 4 pinning (`:67-`) is `resolve_swap` only; pattern 8 ladder is delta only.
  - Unit table (`SKILL.md:35-44`) is rates/dv01 only; `:46` "Prove every conversion with the checker's rates pack".
  - `error-catalogue.md:45`: `sets bump_size; pricebt passes only aggregation_level and currency` → fix "drop the parameter, or compute that variant as its own function" (DEV-I8 behaviour).

### 4.6 `pricebt-enterprise-integration` (89 lines)
- **Purpose:** remote platforms: sessions/secrets, point-in-time data, record/replay, batching, safety envelope.
- **Trigger:** "Patterns for hooking pricebt up to a bank's remote pricing/data platform (Citi-Datapoint-style, Athena-style, in-house services) - sessions and secrets, point-in-time market data, record/replay for offline and CI runs, batching and caching, failure semantics, confidentiality, and the safety guards learned the hard way. Use when your library is a service, needs credentials, or can write to shared stores."
- **Script:** `scripts/record_replay.py` — `ReplayMiss(KeyError)`, `RecordReplay(client, store, mode, methods)` (SHA-256 key of a canonical repr of `(name, args, kwargs)`, pickled results under `store/<2 hex>/<key>.pkl`, modes `live|record|replay`), `wrap(client, store, mode=None, methods=None)` (`:109`; `mode=None` reads `PRICEBT_CLIENT_MODE`, default `replay`); CLI `record_replay.py STORE` prints count and size.
- **Tests:** `tests/skills/test_skill_record_replay.py` (4).
- **IR-scope assumptions:** measure-agnostic. Only `SKILL.md:71` ("prove it with the checker's rates pack"). Vol surfaces and bond static data are not mentioned as things to record.

### 4.7 `pricebt-port-gs-notebook` (57 lines)
- **Purpose:** port GenericEngine notebooks with three edits (imports, session, datasets) plus checks.
- **Trigger:** "Port a gs_quant backtesting notebook or script (GenericEngine, triggers, actions, result_summary / trade_ledger) to pricebt with the three edits - imports, session, datasets - plus the checks for instruments without an asset config, currencies, unsupported engines and the documented behavioural deviations. Use when a user has existing gs_quant backtest code, or when an idea is easiest to express by adapting a gs example notebook."
- **Tests:** none dedicated.
- **IR-scope assumptions:**
  - Scoped to **backtesting** notebooks; nothing for the `02_pricing_and_risk` / `03_portfolios` notebooks (§7.4).
  - `:17` import examples are `IRSwap`, `Price`, `IRDelta`; no `Portfolio`, `PricingContext`, `HistoricalPricingContext`, `PnlDefinition`.
  - `:20` "`IRSwaption`, `FXOption` and the other gs classes exist, but each needs its own config" (no Bond).
  - `:22` "`bump_size` and other finite-difference parameters raise `NotSupportedError`."
  - `:24` result objects listed are BackTest views only.

### 4.8 `pricebt-strategy-workflow` (108 lines)
- **Purpose:** gated pipeline idea → spec → backtest → review → spot checks → tearsheet.
- **Trigger:** "The end-to-end pipeline for a strategy study in pricebt - plain-English idea -> follow-up questions and a frozen strategy spec -> implementation -> adversarial review -> spot checks -> tearsheet delivery - with a gate between stages and a one-command driver (run_study.py) that produces the automated parts. Use whenever someone says "backtest this", "test this idea", or "is this strategy any good"."
- **Script:** `scripts/run_study.py` — `red_flags(sig, metrics, spec)` (`:112`), `run_study(spec_path, out=None, with_robustness=True)` (`:130`), `main` CLI `run_study.py SPEC [--out DIR] [--no-robustness]` (`:171-175`). Example `example/toy_momentum_spec.yaml` (IRSwap, `measure: par_rate`, `risks_to_report: [Price, IRDeltaParallel]`).
- **Tests:** `tests/skills/test_skill_run_study.py` (3).
- **IR-scope assumptions:**
  - `_report_risk` (`:47-55`) only looks for `IRDeltaParallel` or `IRDelta(aggregation_level="Type")`.
  - `_rate_series` (`:58-69`) hard-codes `measure_series(fresh, "par_rate", ...)` (`:66`) and keeps it only if its unit is `bp`.
  - `STANDARD_CAVEATS_BY_ARCHETYPE` (`:40-44`): swap caveats only.
  - `SKILL.md:57` gate: "`check_asset.py <config>` exits 0" — passes vacuously for a swaption/bond config (no pack).

### 4.9 `pricebt-strategy-intake` (77 lines)
- **Purpose:** idea → frozen `strategy_spec.yaml` via a prioritised question bank; `spec.py` fills defaults, records assumptions and validates.
- **Trigger:** "Turn a plain-English trading idea into a complete, validated strategy_spec.yaml by asking the portfolio-management follow-up questions (hypothesis, instruments, signal and lookback windows, rebalance and holding period, sizing, risk limits, costs, financing, dates and out-of-sample split, success criteria) - interactively, or autonomously with stated defaults. Use at the start of any "backtest this idea" request, before writing strategy code."
- **Script:** `scripts/spec.py` — `load_spec` (`:64`), `template` (`:75`), `apply_defaults` (`:94`), `resolve_path` (`:114`), `validate_spec` (`:129`), `parse_risk` (`:301`; `'Name'` or `"Name(k='v', ...)"` → a `pricebt.risk` object, string values only), `dump_spec` (`:319`), CLI `spec.py validate SPEC` / `spec.py defaults SPEC [--out]` (`:326-333`). Template `templates/strategy_spec.yaml`, `references/question-bank.md`.
- **Tests:** `tests/skills/test_skill_spec.py` (28).
- **IR-scope assumptions:**
  - `spec.py:29` `SIGNAL_TYPES = ("none", "par_rate_zscore", "rate_momentum", "custom")`; `:30` `COST_MODELS` includes `dv01_bp`; `:33` archetype→signal map.
  - `spec.py:124-126` `_direction` reads `pay_or_receive` only; `:239-245` curve_trade requires "opposite pay_or_receive (one Pay, one Receive)" — a swaption or bond curve/RV trade cannot validate.
  - `parse_risk` passes kwargs as strings (`:312-314`): `IRDelta(bump_size=0.5)` would pass `'0.5'` (a str) — irrelevant today (DEV-I8 raises) but wrong once FD params pass through.
  - Template: `:5` "Units: rates in bp, risk in currency per 1bp (IRDelta / dv01)"; `:20-29` every instrument `class: IRSwap`; `:43` `type: par_rate_zscore`; `:45` `measure: par_rate`; `:66` `max_abs_dv01`; `:73` `model: dv01_bp`; `:80` `risks_to_report: [Price, IRDeltaParallel]`.
  - `question-bank.md:12` default instrument "ATM, spot-starting, `notional_amount` 10mm"; `:15` default signal "the asset's `par_rate`"; `:30` costs "for liquid swaps". PM-language table (`SKILL.md:48-59`) has no vol/option/bond phrases ("sell vol", "straddle", "gamma scalp", "carry and roll-down on bonds", "asset swap").

### 4.10 `pricebt-strategy-recipes` (133 lines)
- **Purpose:** spec → gs constructs, one builder per archetype (`periodic_roll`, `mean_reversion`, `momentum`, `curve_trade`, `delta_hedged`, `risk_band`, `event`, stop-loss overlay).
- **Trigger:** "Turn a validated pricebt strategy spec into a runnable gs-style backtest (Strategy + triggers + actions + GenericEngine.run_backtest kwargs) with one recipe per archetype — periodic roll/carry, mean reversion, momentum, dv01-neutral curve trade, delta-hedged, risk band, event — plus a stop-loss overlay, cost and financing mapping. Use after pricebt-strategy-intake has produced a spec, or when you need the exact pricebt construct for a trading rule."
- **Script:** `scripts/recipes.py` — `make_instrument`, `direction_sign`, `flipped`, `transaction_model`, `with_ccy`, `entry_action`, the 7 archetype builders + `stop_loss_overlay`, `use_session`, `signal_series`, `build` (`:341`), `run`, `describe` (`:464`), CLI `recipes.py {describe,run} SPEC [--out CSV]` (`:482-501`). References `archetype-catalogue.md`, `construct-cheatsheet.md`.
- **Tests:** `tests/skills/test_skill_recipes.py` (21; only `:243-253` uses a swaption).
- **IR-scope assumptions:**
  - `recipes.py:76` `BOOK_DV01 = IRDelta(aggregation_level="Type")` is the only sizing/hedge/cost risk; added to results at `:416`.
  - `recipes.py:111-115` `direction_sign` = −1 iff `pay_or_receive` starts with "rec". For an `IRSwaption`, `pay_or_receive` is the **option type**, not the position: a sold payer (`buy_sell=Sell`) gets +1 and a bought receiver gets −1 regardless of `buy_sell`.
  - `recipes.py:118-123` `flipped` swaps `pay_or_receive`: for a swaption this turns a payer into a receiver, **not** the opposite position (should flip `buy_sell` or negate `quantity_`). A momentum recipe on a swaption would trade the wrong instrument.
  - `recipes.py:132` `notional_bp` cost → `ScaledTransactionModel("notional_amount", ...)`; gs `Bond` sizes by `size` (§7.3).
  - `recipes.py:106-107` sizing notional overrides `notional_amount` only.
  - `SKILL.md:118` (dv01_target signs by `pay_or_receive`), `:122` NAV sizing "use it only for premium instruments such as swaptions" (the only swaption guidance); `archetype-catalogue.md:62` (a swaption NAV snippet), `:229-230` ("Hedge a different risk: any scalar measure the asset config maps (e.g. a vega measure with a swaption hedge)"). No vega-neutral, gamma-scalping, straddle, or bond carry/roll archetype.

### 4.11 `pricebt-adversarial-review` (66 lines)
- **Purpose:** severity-ranked checklist, robustness experiments, reviewer-agent prompts, review template.
- **Trigger:** "Adversarially review a pricebt backtest before anyone trusts it - a severity-ranked checklist of pricebt-specific traps (coupons not booked, same-close execution, offsetting MR exits, dropped market dates, frictionless defaults, unit/sign errors) and textbook pitfalls (look-ahead, data snooping, overfitting, costs, regime, bad marks), automated robustness experiments (cost ladder, parameter sweep, truncation and signal-shift look-ahead tests, sub-periods, in/out-of-sample), and a findings report. Use after every backtest and before any tearsheet."
- **Script:** `scripts/robustness.py` — `cost_ladder`, `parameter_sweep`, `truncation`, `signal_shift`, `sub_periods`, `is_oos` (`:72-189`), `EXPERIMENTS` (`:206`), `run_all(spec, experiments=None, base=None)` (`:212`), `to_markdown` (`:229`); CLI `robustness.py SPEC [--only ...]` (`:243-244`).
- **Tests:** `tests/skills/test_skill_robustness.py` (8).
- **IR-scope assumptions:** checklist A2 (`references/checklist.md:10`) "dv01 > 0 for a payer; rates in bp; ladder sum ≈ dv01 | the checker's rates pack"; A4 (`:12`) coupons for swaps; reviewer prompt (`reviewer-prompts.md:20`, `:23`) dv01 sign and dv01 weights. No option items (vol surface point-in-time, smile dynamics, expiry/exercise handling, vega-bucket concentration) and no bond items (clean vs dirty, accrued, repo, on-the-run roll, identifier survivorship).

### 4.12 `pricebt-spot-checks` (113 lines)
- **Purpose:** automated bookkeeping checks on a finished BackTest plus five manual checks.
- **Trigger:** "Verify a finished pricebt backtest before believing or reporting it — accounting identities, independent repricing of trades and of the book, cash roll-forward, P&L explain, frictions, missing data, determinism — plus the manual checks (reprice by hand, look-ahead shift, costs doubled). Use after every backtest run and before any tearsheet, or when a result "looks too good" or a number surprises you."
- **Script:** `scripts/spot_check.py` — `check_ledger_identity`, `check_closed_trades`, `check_trade_repricing` (`:88`), `check_book_repricing` (`:117`), `check_cash_rollforward` (`:136`), `check_pnl_explain` (`:161`), `check_missing_market`, `check_frictions`, `check_determinism`, `check_open_positions`, `LIMITATIONS` (`:245-248`), `run_spot_checks(backtest, session=None, sample=5, seed=0, rerun=None, rate_measure=None, risk=None)` (`:251`), `to_markdown` (`:291`), `_demo` (`:297`, IRSwap + `IRDeltaParallel`); CLI `spot_check.py --demo`.
- **Tests:** `tests/skills/test_skill_spot_checks.py` (10).
- **IR-scope assumptions:** `check_pnl_explain` (`:161-183`) = one scalar ccy/bp `risk` × one bp `rate_measure`; `SKILL.md:22-23` (inputs `IRDeltaParallel`, `par_rate` series), `:65` (P&L explain row); `manual-checks.md:19` ("fixed rate, effective and termination dates, notional times `quantity_`, pay/receive"), `:45` ("pay ↔ receive" perturbation). The repricing checks (`:88-133`) are instrument-agnostic (they use `bt.price_measure`).

### 4.13 `pricebt-tearsheet-report` (106 lines)
- **Purpose:** metrics, charts, success criteria, self-contained HTML/MD/JSON/CSV.
- **Trigger:** "Turn a checked pricebt backtest into a decision-ready tearsheet — metrics (Sharpe, t-stat, drawdown, hit rate, cost drag, dv01 use, P&L in bp, IS vs OOS), charts, success-criteria PASS/FAIL against the strategy spec, trade ledger, spot checks, review findings and caveats — written as self-contained HTML plus Markdown, JSON and CSV. Use when asked to "report", "write up", "summarise" or "make a tearsheet" for a backtest, after the spot checks."
- **Script:** `scripts/tearsheet.py` — `compute_metrics(backtest, risk=None, annualisation_factor=252, in_sample_end=None)` (`:65`), `evaluate_success` (`:114`), `make_figures` (`:160`), `build_tearsheet(...)` (`:386`), `demo_backtest` (`:440`), CLI `tearsheet.py --demo OUT_DIR` (`:474-476`). References `metrics-definitions.md`, `report-template.md`.
- **Tests:** `tests/skills/test_skill_tearsheet.py` (4).
- **IR-scope assumptions:** one scalar ccy/bp `risk` (`SKILL.md:21` "normally `IRDeltaParallel`"; `tearsheet.py:98-105`, `:300-312`; `metrics-definitions.md:11`, `:53-56`); no greek exposures, no P&L attribution section, no vega/theta use metrics.

### 4.14 `pricebt-research-methodology` (93 lines)
- **Purpose:** research standard from four books; significance calculator.
- **Trigger:** "The research standard every pricebt strategy study is held to, distilled from four quant-research books (Grinold & Kahn; Chan; Tulchinsky et al. "Finding Alphas"; Dunis, Laws & Naim) and mapped to rates backtests - hypothesis first, trial logging, breadth and significance (t ~ Sharpe x sqrt(years)), out-of-sample design, costs, sizing (half-Kelly, risk budgets), and a significance calculator. Use when designing a study, judging whether a result is real, or writing the methodology section of a report."
- **Script:** `scripts/research_stats.py` — 15 pure functions (`annualised_sharpe` … `sharpe_summary`, `:22-119`); CLI `research_stats.py CSV [--trials N]` (`:143-144`).
- **Tests:** `tests/skills/test_skill_research_stats.py` (9).
- **IR-scope assumptions:** `references/rates-mapping.md:7` (size = dv01 via `IRDelta(Type)`), `:9` (neutrality = dv01-neutral), `:12` (costs = bp × dv01). No vega/theta budget or duration-based bond sizing.

---

## 5. `check_asset.py` in detail (the file the swaption and bond packs will extend)

### 5.1 Structure

| Piece | Lines | What it is |
|---|---|---|
| Docstring check catalogue | `:1-59` | lists generic checks and the "RATES-SWAP PACK (only when `instrument: IRSwap`)". **Drift:** `swap_par_rate_atm` (emitted at `:466-474`) is not listed. |
| Constants | `:77-87` | `PASS/WARN/FAIL/SKIP`; `SLOW_EVAL_SECONDS = 1.0`; **`SIZE_KWARG = "notional_amount"`** with a `ponytail:` note ("add a CLI flag if an asset sizes by something else", `:80-82`); `_TENOR_RE`, `_ATM_RE`; **`_EXTENSIVE_UNITS = {"ccy","ccy_per_bp","ccy_per_bp2","number"}`**, **`_INTENSIVE_UNITS = {"bp","pct","decimal"}`** (`:86-87`, mirror of DESIGN §4.2 units) |
| `CheckResult(name, status, detail)` | `:90-94` | dataclass |
| `_Ctx` | `:97-111` | `cfg, session, service, asset, inst, kwargs` (defaults overlaid by non-None instrument kwargs, `:539`), `d1, d2, start, end, fx_given, r1` (d1-resolved trade), `evals` (timings) |
| `_bday` | `:117-120` | roll forward/back over weekends only |
| `_is_relative(key, v)` | `:123-129` | **pinning heuristic:** a str value is "relative" if the key ends `_date` and matches a tenor, or the key contains `rate`/`strike` and starts with `atm`. Keys not so named (a bond `identifier`, `expiration` without `_date`, a `premium` of `'ATM'`) are not scanned. |
| `_risk(name)` | `:139-142` | `getattr(pricebt.risk, name, None)` — **the measure allowlist is "whatever `pricebt.risk` exports"** |
| `_scalar_form(measure)` | `:145-148` | wraps only `RiskMeasureWithFiniteDifferenceParameter` in `aggregation_level="Type"`; plain `RiskMeasure`s (e.g. `IRGamma`, `IRGammaParallel`, `Theta`) are used as-is |
| `_instrument(cfg, kwargs)` | `:155-163` | `ConfigInstrument(cfg.name, ...)` or `getattr(pricebt.instrument, cfg.instrument)(pricebt_asset=..., name="probe", **kwargs)`; unknown class → `ValueError` → `instrument_builds` FAIL (`:536-538`). **A `Bond` config FAILs here today.** |
| `_install_eval_timer` | `:166-179` | wraps the private `PricingService._ns(asset).eval` per instance |

### 5.2 Runner order and early exits (`run_checks`, `:503-586`)

1. `sys_path` prepended (`:516-518`).
2. `config_loads` (`:525-529`): `load_asset(config)`; any exception → single FAIL row, return.
3. Dates (`_default_dates`, `:495-500`): d1 = `--date` or today−120d (bday); d2 = second `--date` or d1+1m; smoke window d1..start+3m.
4. New `PricebtSession(assets=[cfg], fx=fx)` (`:532`); build probe instrument (`instrument_builds` FAIL on error, `:535-538`).
5. `imports_execute` / `market_available` (`:544-557`): `has_market` on d1 and d2; `AssetEvaluationError` keyed `imports`/`code` → `imports_execute` FAIL; else `market_available` FAIL; `None` on a business day → FAIL; each returns with `remaining | SKIP`.
6. `resolve` on d1 (`:559-562`); failure → `resolve_pins_terms` FAIL + `remaining | SKIP`.
7. Check list (`:564-579`): `match`, `market_weekend`, `resolve_pins_terms`, `functions_finite`, `quantity_scaling`, `notional_linearity`, `risk_measures`, `measure_series`, [`smoke_backtest` unless `--no-backtest`], `fx_round_trip`, **[`swap_pack` iff `cfg.instrument == "IRSwap"`]**, `performance`. Each check's exception becomes a FAIL row named after the check (`:581-585`).

### 5.3 Generic checks (what they assume)

| Check | Lines | Logic | Swap/measure assumptions |
|---|---|---|---|
| `match` | `:185-194` | `registry.match(cfg.instrument, inst.kwargs)`; `ConfigError` → WARN | none |
| `market_weekend` | `:197-205` | Saturday after d1: raise → FAIL, object → WARN, None → PASS | none |
| `resolve_pins_terms` | `:208-224` | `_is_relative` scan (FAIL); `*_date` str values (FAIL); if tenor kwargs exist, resolve on d2 and WARN if all date terms equal | key-name heuristics (§5.1) |
| `functions_finite[f]` | `:227-249` | every non-`date` function via `unit_value(r1, d1, f)`; every portfolio function via `portfolio_value(asset, d1, f, [r1])`; exception FAIL, NaN WARN | none |
| `quantity_scaling[M]` | `:252-274` | for each `risk_measures` entry **with a scalar mapping**: value at q=1 and q=−3 on d2, expects ×−3 (extensive) or ×1 (intensive) | **bucketed-only mappings are skipped** (`:256-259`) — a bucketed-only vega cube is never scaling-checked |
| `notional_linearity[f]` | `:277-302` | doubles `SIZE_KWARG` and compares **`functions:` only** (`:283`) on d2 | `notional_amount` only (SKIP for a Bond's `size`); **portfolio functions skipped** |
| `risk_measures[M]` | `:305-324` | unknown name → WARN "not a pricebt.risk measure name: typo?"; scalar form and bucketed form evaluated | allowlist = `pricebt.risk` exports; no check that the **required** measures for the instrument exist |
| `measure_series` | `:327-342` | series of the first intensive function, else first extensive non-Price function, over d1..d1+14d; constant → WARN | needs a `Price` mapping (`:331`) |
| `smoke_backtest` | `:345-364` | monthly `AddTradeAction(inst, "1m")`; non-finite Price → FAIL; Total identity | uses `Price` only; fine for any instrument |
| `fx_round_trip` | `:367-373` | `fx(ccy,USD)*fx(USD,ccy) == 1` | none |
| `performance` | `:376-382` | any eval > 1s → WARN | none |

### 5.4 The swap pack (`swap_pack`, `:388-489`)

Docstring (`:389-390`): "Optional check pack, run only when the config's instrument is IRSwap. Conventions assumed (pricebt's, from gs): payer dv01 > 0 is the PV change per +1bp; pay_or_receive 'Pay'/'Receive'."

| Row | Lines | Relation tested | Hard-coded names |
|---|---|---|---|
| setup | `:391-402` | payer/receiver clones resolved on d1; `n = abs(notional_amount)`; `dv01 = value(IRDelta(aggregation_level="Type"))`; `npv = value(Price)` | `IRDelta`, `IRFwdRate`, `Price`; `pay_or_receive="Pay"/"Receive"` |
| `swap_atm_npv` | `:404-411` | ATM payer `|npv| < 1e-4·N` PASS, `< 1e-3·N` WARN, else FAIL; SKIP unless `fixed_rate` is None/ATM | `fixed_rate` |
| `swap_dv01_sign` | `:413-422` | payer dv01 > 0; receiver ≈ −payer (1%); SKIP without an `IRDelta` mapping | `"IRDelta" in cfg.risk_measures` |
| `swap_dv01_band` | `:424-434` | `0.25e-4·N·T < |dv01| < 1.2e-4·N·T` (T in years from resolved dates) | `effective_date`, `termination_date` |
| `swap_bucket_sum` | `:436-443` | Σ ladder ≈ scalar (2%) | `IRDelta` scalar+bucketed |
| `swap_par_rate_unit` | `:445-464` | par function = `IRFwdRate` scalar mapping, else a function literally named `par_rate`; unit ∈ `bands = {"bp": (-500, 2000), "pct": (-5, 20), "decimal": (-0.05, 0.20)}` (`:452`); bp with `|v| < 0.2` FAIL, `< 20` WARN | `par_rate` literal (`:446`) |
| `swap_par_rate_atm` | `:466-474` | ATM trade: `par·to_bp == resolved fixed_rate·1e4` within 0.5bp; `to_bp = {"bp": 1.0, "pct": 100.0, "decimal": 1e4}` (`:468`) | `fixed_rate` (decimal, gs convention) |
| `swap_pnl_explain` | `:476-488` | `npv(d2)−npv(d1)` vs `½(dv01(d1)+dv01(d2))·Δpar_bp`; SKIP if `|Δpar| < 5bp`; ratio < 0 FAIL, in [0.5, 1.5] PASS, else WARN | first-order only |

### 5.5 Tests and fixtures (all IRSwap)

- `test_toy_configs_pass` (`test_skill_check_asset.py:39-46`): `toy_usd_irs.yaml`, `toy_eur_irs.yaml` with `toy_fx.yaml`; no FAIL; asserts `swap_pnl_explain` PASS.
- `BROKEN` fixtures (`:27-32`, `:49-52`): `bad_dv01_sign`→`swap_dv01_sign`, `bad_par_rate_decimal`→`swap_par_rate_unit`, `bad_unpinned_maturity`→`resolve_pins_terms`, `bad_weekend_raises`→`market_weekend`. Each fixture is a copy of `tests/assets/toy_usd_irs.yaml` with one thing broken; two import `check_asset_fixture_lib` (`resolve_unpinned`, `build_swap_repinning`, `market_raising_on_weekend`).
- `MUTATIONS` (`:68-83`, 12 rows): in-memory edits of the toy swap config, each asserting one check's status (imports FAIL, market FAIL, match WARN, `scale_with_quantity` FAIL, par_rate unit `ccy` FAIL, dv01×100 band FAIL, negated npv → `swap_pnl_explain` FAIL, NaN npv → smoke FAIL, frozen market → `measure_series` WARN, typo measure WARN, a convention tenor PASS, an ISO-string date FAIL).
- CLI tests (`:102-116`): toy passes with exit 0; `bad_unpinned_maturity` with `--sys-path` exits 1.
- Meridian cross-skill (`:120-133`): example passes; `dv01_sign_not_flipped`→`swap_dv01_sign`, `par_rate_in_percent`→`swap_par_rate_atm`, `maturity_not_pinned`→`resolve_pins_terms`.

### 5.6 How a pack is "selected" today and what that implies

- Selection is `cfg.instrument == "IRSwap"` (`:577`). A `ConfigInstrument` swap, or any other class, never gets a pack; there is no CLI override.
- Direction is assumed to be `pay_or_receive` (`:395-396`), size `notional_amount` (`:82`, `:397`), strike `fixed_rate` (`:405`, `:467`), tenor terms `effective_date`/`termination_date` (`:424`).
- Measure names are resolved through `pricebt.risk` (`_risk`, `:139-142`), so any measure the port adds to `pricebt.risk` (e.g. `IRVolga`, `Theta`) automatically stops the "typo?" WARN (`:312`); a measure that is **not** in the gs catalogue (a bond "carry" or "roll") will always WARN unless an allowlist is added.

### 5.7 Evidence: the checker on the toy swaption today

`tests/assets/toy_usd_swaption.yaml` maps only `Price: npv` and `IRVega: {scalar: vega}` (no `IRDelta`, no gamma, no theta; `:30-32`). Output (2024-01-03 / 2024-02-05):

```
config_loads PASS (IRSwaption, 2 functions, measures ['IRVega', 'Price'])   imports_execute PASS   market_available PASS
match PASS   market_weekend WARN   resolve_pins_terms PASS (expiration_date, termination_date, strike=0.0387..., notional pinned)
functions_finite[npv|vega] PASS   quantity_scaling[Price|IRVega] PASS   notional_linearity[npv|vega] PASS
risk_measures[Price|IRVega] PASS   measure_series PASS (vega)   smoke_backtest PASS   fx_round_trip SKIP   performance PASS
PASS=16 WARN=1 FAIL=0 SKIP=1, exit 0
```

So a swaption config that has **no delta, no gamma, no theta, a vega of unknown basis, and an untested `buy_sell` fold** is "green". The workflow gate (`pricebt-strategy-workflow/SKILL.md:57`) would let it through.

---

## 6. Cross-cutting assumption matrix

| Assumption | Where it is baked in |
|---|---|
| Instrument = `IRSwap` | template `config-template.yaml:10`; Meridian example; toy fixtures; `check_asset.py:577`; `known-answer-probes.md:16`; `independent-validation.md:25`; `strategy_spec.yaml:20-29`; `toy_momentum_spec.yaml`; `spot_check.py:_demo`; `tearsheet.py:demo_backtest`; `archetype-catalogue.md` code (lines 15-21, 182-183, 211) |
| Direction kwarg = `pay_or_receive` (payer = long risk) | `check_asset.py:395-396`; `recipes.py:111-123`; `spec.py:124-126`, `:239-245`; `manual-checks.md:19`, `:45`; `SKILL.md:118` (recipes) |
| Size kwarg = `notional_amount` | `check_asset.py:82`; `recipes.py:106-107`, `:132`; question bank `:12` |
| Measure set = `Price`, `IRDelta` (+ladder), `IRDeltaParallel`, `IRFwdRate` | `check_asset.py:391`; `recipes.py:76`; `run_study.py:47-55`; spec template `:80`; tearsheet `SKILL.md:21`; `independent-validation.md:34`; architecture `:64`; port skill `:17`, `:22` |
| Function names `npv`, `dv01`, `par_rate`, `delta_ladder` | connect step-3 table `:65-73`; questionnaire `:48`; template `:98-107`; `check_asset.py:446`; `run_study.py:66`; spec template `:45`; question bank `:15` |
| Units = ccy, ccy per bp, rates in bp | architecture `:37`; `convention-conversions.md:7-19`; spec template `:5`; `metrics-definitions.md:11`; `rates-mapping.md:7-12` |
| First-order, single-factor P&L explain | `check_asset.py:476-488`; `spot_check.py:161-183`; `tearsheet.py:98-105`, `:300-312`; `run_study.py:152` |
| Instrument-level only; no Portfolio/PRR usage | architecture `:23` (diagram only); `construct-cheatsheet.md:57` (HedgeAction priceables "one instrument or a Portfolio"), `:36` (`PortfolioTrigger`); no skill shows `Portfolio.calc`/`aggregate`/`to_frame`/historical |
| FD parameters raise | port `:22`; error catalogue `:45` |
| No bond anywhere | only mention of "Bond" in the library is a methodology aside (`research-methodology/references/principles.md:48`, "Bond managers historically run little active risk") |

---

## 7. gs reference facts the updated library must carry

These are the facts the skills will have to teach an agent; the authoritative port-side analysis belongs to the sibling IR notes. Quotes are gs docstrings.

### 7.1 IR risk-measure catalogue (GS2 `target/measures.py`; presets GS2 `risk/measures.py:79-85`)

| gs measure | class | measure_type / unit | gs docstring (quoted) | line | in `pricebt.risk`? |
|---|---|---|---|---|---|
| `Price` | currency-param | PV | "Present Value" | 547 | yes |
| `DollarPrice` | plain | Dollar Price | "Price of the instrument in US Dollars" | 310 | yes |
| `Annuity` | currency-param | AnnuityLocalCcy | "Annuity" | 223 | yes |
| `IRDelta` | FD-param | Delta | "Change in Dollar Price (USD present value) due to individual 1bp moves in the interest rate instruments used to build the underlying discount curve" | 463 | yes |
| `IRDeltaParallel`, `IRDeltaLocalCcy` | presets | `aggregation_level=Asset`; `currency='local'` | — | `risk/measures.py:81-82` | yes |
| `IRGamma` | plain | Gamma | "IRGamma" | 475 | yes |
| `IRGammaParallel` | plain | ParallelGamma | "Change in aggregated IRDelta for a aggregated 1bp shift in the interest rate instruments used to build the underlying discount curve" | 478 | yes |
| `IRGammaParallelLocalCcy` | plain | ParallelGammaLocalCcy | "Interest Rate Parallel Gamma (Local Ccy)" | 481 | **no** |
| `IRVega` | FD-param | Vega | "Change in Dollar Price (USD present value) due to individual 1bp moves in the implied volatility (IRAnnualImpliedVol) of instruments used to build the volatility surface" | 490 | yes |
| `IRVegaParallel`, `IRVegaLocalCcy` | presets | — | — | `risk/measures.py:84-85` | yes |
| `IRVanna` | FD-param | Vanna | "Interest Rate Vanna (USD)" | 487 | **no** |
| `IRVolga` | FD-param | Volga | "Interest Rate Volga (USD)" | 493 | **no** |
| `IRBasis` (+`IRBasisParallel` preset) | FD-param | Basis | "... individual 1bp moves in the interest rate instruments used to build the basis curve(s)" | 457; preset `:79` | measure yes, **preset no** |
| `IRXccyDelta` (+`IRXccyDeltaParallel`) | FD-param | XccyDelta | "Change in Price due to 1bp move in cross currency rates." | 496; preset `:83` | measure yes, **preset no** |
| `IRDiscountDeltaParallel`, `...LocalCcy` | plain | ParallelDiscountDelta(LocalCcy) | "Parallel Discount Delta" | 466, 469 | **no** |
| `IRFwdRate` | plain | Forward Rate / **Percent** | "Interest rate par rate (in percent)" | 472 | yes (DEV-I7: config unit) |
| `IRSpotRate` | plain | Spot Rate / Percent | "Interest rate at-the-money spot rate (in percent)" | 484 | yes |
| `IRAnnualImpliedVol` | plain | Annual Implied Volatility / Percent | "Interest rate annual implied volatility (in percent)" | 454 | yes |
| `IRAnnualATMImpliedVol` | plain | Annual ATMF Implied Volatility / Percent | "Interest rate annual implied at-the-money volatility (in percent)" | 451 | **no** |
| `IRDailyImpliedVol` | plain | Daily Implied Volatility / BPS | "Interest rate daily implied volatility (in basis points)" | 460 | yes |
| `ParSpread` | plain | Spread | "Par Spread" | 538 | **no** |
| `Theta` | plain | Theta | "Theta" | 568 | **no** |
| `ForwardPrice` | plain | Forward Price / BPS | " Price of the instrument at expiry in the local currency" | 448 | **no** |
| `Cashflows`, `ResolvedInstrumentValues` | plain | — | "Cashflows"; "Resolved InstrumentBase Values" | 283, 565 | yes |
| `PremiumCents`, `LocalAnnuityInCents`, `MarketData` | plain | — | "PremiumCents"; "Local Currency Accrual in Cents"; "Market Data" | 541, 517, 523 | **no** |
| `InflationDelta` (+`InflationDeltaParallel`), `InflDeltaParallelLocalCcyInBps`, `InflMaturityCPI`, `Infl_CompPeriod` | mixed | — | "Change in Price due to 1bp move in inflation curve." … | 508, 499, 502, 505; preset `:80` | only `InflationDelta` |
| `PnlExplain(to_market)`, `PnlExplainClose`, `PnlExplainLive`, `PnlPredictLive` | `__RelativeRiskMeasure` (sets a `RelativeMarket` pricing context) | PnlExplain / PnlPredict | "Pnl Explained"; "Pnl Predicted" | `risk/measures.py:26-75` | **no** (server-side relative-market calc) |

pricebt's list is `src/pricebt/risk/__init__.py:229-262`. The gs tutorial table (GSDOC `02_pricing_and_risk/00_instruments_and_measures/tutorials/Measures.ipynb`, cell 2) marks the parameterised ("*") IR measures as `Annuity`, `Price`, `IRBasis`, `IRDelta`, `IRGamma`, `IRVega`, `IRXccyDelta`, `InflationDelta`; it defines `IRGamma*` as "Change in aggregated IRDelta for a aggregated 1bp shift ..." (the same text the source gives `IRGammaParallel`).

**Unit ambiguity to pin in the contract.** gs says `IRAnnualImpliedVol` is "(in percent)" (`target/measures.py:454-455`), yet the tutorial multiplies it by 10 000 (`Measures.ipynb` cell 13: `swaption.calc(risk.IRAnnualImpliedVol) * 10000`), i.e. the returned number behaves like a decimal normal vol. `IRVega` is defined per "1bp moves in the implied volatility (IRAnnualImpliedVol)". The skill contract must make the config **declare** the vol basis (normal vs lognormal) and the vol unit, exactly as DEV-I7 does for `IRFwdRate`, and the checker must band-check it.

### 7.2 Finite-difference parameters

- gs `FiniteDifferenceParameter` fields: `aggregation_level`, `currency`, `local_curve`, `bump_size`, `finite_difference_method`, `scale_factor`, `mkt_marking_options`, `parameter_type='FiniteDifference'`, `name` (GS2 `target/common.py:5789-5799`).
- `FiniteDifferenceMethod`: `Up`, `Centered`, `Down`, `CenteredSecondOrder` — "Direction and dimension of finite difference" (GS2 `target/common.py:4004-4011`). `AggregationLevel`: `Type`, `Asset`, `Class`, `Point` (`:45-52`).
- gs tutorial (`Measures.ipynb` cell 23): "For some finite difference risk measures (noted * in the table above), you can now pass in the specifics of the calculation methodology", with the table currency / aggregation_level ("Level of aggregate shift") / local_curve ("Change in Price (present value in the denominated currency)") / finite_difference_method ("Direction and dimension of finite difference") / mkt_marking_options ("Market marking mode") / bump_size ("Bump size") / scale_factor ("Scale factor").
- pricebt today: `_check_no_extra_parameters` raises `NotSupportedError(... "sets {f.name}; pricebt passes only aggregation_level and currency to asset configs")` (`src/pricebt/assets/pricing.py:356-367`, DEV-I8, DESIGN §8.1 rule 3a). Skills that repeat this: port `:22`, error catalogue `:45`. **No skill says how a config would receive or honour these parameters** — whatever the core decides (an injected parameter dict, extra function kwargs, or per-variant functions), the connect template, cookbook, checker and error catalogue all need matching changes (§8, item U-FD).

### 7.3 Instruments

- `IRSwaption` exists in pricebt (`_gs_fields.py:54`). DESIGN §13 (`docs/v2/DESIGN.md:1032-1054`) prescribes: market = curve **and** vol surface as one object with its own key; `resolve` pins `expiration_date`, `termination_date`, strike; functions `npv`, `dv01`, `vega` ("`unit: ccy_per_bp`, per bp of normal vol") and `gamma`; `attributes: {expiration_date: ...}` so `AddTradeAction(option, 'expiration_date')` works; `risk_measures: {Price, IRDelta: {scalar: dv01}, IRVega: {scalar: vega}}`; a bucketed IRVega uses `';'`-joined `'expiry;tenor'` keys; `resolve` "MUST fold [`buy_sell`] into a signed resolved notional" and "MUST reject `pay_or_receive` values it does not price (e.g. `Straddle` ...)"; strike grammar `'ATM'`, `'A-50'`, decimals, `'=solvefor(...)'` → raise. The toy swaption config implements only part of this (`Price`, `IRVega`).
- `Bond` is **not** a pricebt instrument (the `_gs_fields.py` table has `IRSwap`, `IRSwaption`, `FXOption`, `FXForward`, `EqOption`; `pricebt.instrument` also exports `InflationSwap`, `Cash`, `ConfigInstrument`). gs `Bond` fields: `buy_sell`, `identifier`, `identifier_type`, `size`, `settlement_date`, `settlement_currency` (GS2 `target/instrument.py:81-90`; identical in GS1 `target/instrument.py:81-90`). So a bond sizes by **`size`**, direction by **`buy_sell`**, and has no tenor/strike fields — every swap-shaped assumption in §6 breaks.

### 7.4 Portfolio, PortfolioRiskResult, historical pricing (what the skills must show)

- gs docs notebooks: `03_portfolios/examples/030005_calculate_portfolio_risk.ipynb` (`portfolio.calc((risk.DollarPrice, risk.IRDelta))`; `result[risk.IRDelta].aggregate()`; `result[risk.DollarPrice]['EUR-3m5y']` and `result['EUR-3m5y'][risk.DollarPrice]`), `030009_portfolio_risk_result_to_frame.ipynb` (nested portfolios; `to_frame()`, `to_frame(values=None, columns=None, index=None)`, `to_frame(values='value', columns='portfolio_name_0', index='instrument_name')`, `aggfunc='mean'`, `display_options=DisplayOptions(show_na=True)`), `030007_pnl_explain.ipynb` (`PnlExplain(CloseMarket(date=to_date))` under `PricingContext(pricing_date=from_date)`; "Compute the time component (PnlExplain does not do this)"), `02_pricing_and_risk/.../01_rates/08_calc_swap_risk_historically.ipynb` (`HistoricalPricingContext`), `23_solve_vanna_&_volga.ipynb` (`port.calc(IRVanna).to_frame()`, `IRVolga`, and `notional_amount='=solvefor([payer_swaption].risk.IRDeltaParallel,bp)'`).
- pricebt implements: `Portfolio.resolve/calc/price/dollar_price/to_frame` (`src/pricebt/markets/portfolio.py:236-249`), `PortfolioRiskResult.__getitem__` (`src/pricebt/risk/results.py:319`), `.aggregate` (`:471-489`, group-aggregated ladders), `.to_frame(values, index, columns, aggfunc)` (`:491-514`: columns only `instrument_name`, `risk_measure`, `value`; bucketed values are **summed** per instrument; no `portfolio_name_N`, no `mkt_*` columns, no dates, no `display_options`), `PricingContext` and `HistoricalPricingContext` (`src/pricebt/markets/__init__.py:30`, `:92`). The contract is DESIGN §8.2 (`docs/v2/DESIGN.md` "PortfolioRiskResult contract"). A skill must document exactly this subset (and what raises), not the gs superset.

### 7.5 P&L attribution machinery that already exists

- gs `PnlAttribute(attribute_name, attribute_metric, market_data_metric, scaling_factor, second_order=False)` and `PnlDefinition(attributes)` (GS2 `backtests/backtest_objects.py:71-89`); `BackTest.pnl_explain()` (`:344-391`): per attribute, per consecutive date pair, per instrument held on the previous date, `scaling_factor · risk[t-1] · (m[t] − m[t-1])`, or `0.5 · scaling_factor · risk[t-1] · (m[t] − m[t-1])²` when `second_order`, cumulated; exits read `trade_exit_risk_results`. `fx_pnl_definition()` (`:996-1025`) is the worked template: delta (`FXDeltaLocalCcy` × `FXSpot`), gamma (`FXGammaLocalCcy` × `FXSpot`, second order), vega (`FXVegaLocalCcy` × `FXAnnualImpliedVol`, `scaling_factor=100.0`).
- pricebt ports all of it (`src/pricebt/backtests/backtest_objects.py:88-118`, `:404-450`; `run_backtest(..., pnl_explain=...)` at `generic_engine.py:289`, `:311`, `:413`).
- Consequence for the skills: an IR decomposition is a `PnlDefinition` whose attributes pair a config-mapped **greek** with a config-mapped **market-data measure**, e.g. delta = `IRDelta(Type)` × `IRFwdRate` (scaling 1 when the config's par rate is in bp; 100 if it followed gs percent), gamma = `IRGammaParallel` × `IRFwdRate` (second order), vega = `IRVega(Type)` × `IRAnnualImpliedVol`/`IRDailyImpliedVol` (scaling set by the vol unit), volga = `IRVolga` × vol (second order). **Theta, carry and roll are not market-move attributes**: they need either a `Theta` mapping integrated over days (a custom attribute with a "days" market-data measure) or a residual, as gs itself does in `030007` ("Compute the time component (PnlExplain does not do this)"). The scaling factors depend on the units the config declares — which is why the skill must tie decomposition to the measure contract.

---

## 8. Update needs (numbered, per file, each with an acceptance test)

Priorities: **P0** blocks correct swaption/bond configs; **P1** needed for the requested coverage; **P2** polish.

### 8.1 New content: a per-instrument measure contract (single source of truth)

- **U1 (P0) — `skills/pricebt-ir-measure-contracts/` (new skill) or `skills/pricebt-verify-asset-config/references/measure-contracts.md` + a machine-readable table** (e.g. `skills/pricebt-verify-asset-config/scripts/contracts.py` with `CONTRACTS: {instrument: {required, recommended, units, signs, direction_kwarg, size_kwarg, pinned_terms, attributes}}`).
  - Content per instrument (proposed; to be confirmed against the sibling gs-catalogue note):
    - `IRSwap` — required `Price`, `IRDelta` (scalar; bucketed recommended), `IRFwdRate`; recommended `Annuity`, `IRGammaParallel`, `Theta`, `ParSpread`, `Cashflows`; direction `pay_or_receive` (payer dv01 > 0 per +1bp); size `notional_amount`; pinned `effective_date`, `termination_date`, `fixed_rate` (decimal).
    - `IRSwaption` — required `Price`, `IRDelta` (scalar), `IRVega` (scalar, declared vol basis), `IRGammaParallel` or `IRGamma`; recommended `IRVolga`, `IRVanna`, `Theta`, `IRAnnualImpliedVol` and/or `IRDailyImpliedVol` (declared unit), `IRFwdRate` of the underlying, `ForwardPrice`, bucketed `IRVega` (`'expiry;tenor'` keys); direction `buy_sell` folded into signed notional, `pay_or_receive` = option type (Straddle priced or rejected); size `notional_amount`; pinned `expiration_date`, `termination_date`, `strike`; attribute `expiration_date`.
    - `Bond` — required `Price` (declare clean/dirty and per-unit-of-`size` convention), `IRDelta` (scalar; long bond < 0 per +1bp under the pricebt/gs "PV change per +1bp" sign), a yield measure (catalogue gap: gs has none for bonds in the IR list — decide a name, e.g. map `IRSpotRate`/`IRFwdRate` or a documented custom `RiskMeasure(name=...)`), `IRGammaParallel` (convexity); recommended `Theta` (carry+roll per day), `Cashflows`, accrued; direction `buy_sell`; size `size`; pinned `identifier`/`settlement_date` and static data.
  - Each row states: gs definition (quoted), declared `unit:` from the closed set, sign convention, which `aggregation_level` forms must work, bucket-key grammar.
  - **Acceptance:** a test loads the contract table and asserts every measure name it lists is a `pricebt.risk` export (or is on an explicit allowlist with a reason), and that `tests/assets/toy_usd_irs.yaml` satisfies the IRSwap required set.

### 8.2 `check_asset.py` (verify-asset-config)

- **U2 (P0) — pack registry.** Replace `if cfg.instrument == "IRSwap"` (`:577-578`) with `PACKS = {"IRSwap": swap_pack, "IRSwaption": swaption_pack, "Bond": bond_pack}` plus `--pack NAME` (needed for `ConfigInstrument` assets). *Acceptance:* the toy swaption run emits `swaption_*` rows; `--pack IRSwap` on a `ConfigInstrument` swap emits `swap_*` rows.
- **U3 (P0) — contract check.** New generic row `measure_contract[<instrument>]` after `risk_measures`: FAIL if a required measure is unmapped, WARN per missing recommended measure, FAIL if a declared unit is outside the contract's allowed set (e.g. vega declared `bp`). *Acceptance:* today's toy swaption FAILs it (no `IRDelta`, no gamma); a completed toy swaption passes; a MUTATION removing `IRDelta` from the toy swap FAILs it.
- **U4 (P0) — parameterise the probe kwargs.** Direction (`pay_or_receive` vs `buy_sell`), size (`SIZE_KWARG` `:82` → per-contract, plus `--size-kwarg`), strike (`fixed_rate` vs `strike`), tenor terms. *Acceptance:* `notional_linearity` runs (not SKIP) on a toy bond with `size`.
- **U5 (P1) — allowlist.** `_risk` (`:139-142`) + `risk_measures` WARN (`:312`): accept names from `pricebt.risk` **or** the contract allowlist; keep WARN for true typos. *Acceptance:* existing `risk_measures[IRDeltaTypo]` MUTATION still WARNs (`test_skill_check_asset.py:79`).
- **U6 (P1) — close scaling blind spots.** `quantity_scaling` also checks bucketed-only mappings via the bucket sum (`:256-259`); `notional_linearity` also checks portfolio functions with `trades=[t]`, `weights=[1]` (`:283`). *Acceptance:* a MUTATION setting `scale_with_quantity: false` on a bucketed-only vega cube FAILs.
- **U7 (P1) — pinning heuristic.** `_is_relative` (`:123-129`) scans only `*_date` and `rate`/`strike` keys; extend to `expiration`, `premium`, `spread`, and to option `'A-50'` style strings (`_ATM_RE` matches only `atm...`, `:85`; DESIGN §13 grammar uses `'A-50'`). *Acceptance:* a fixture whose `resolve` leaves `strike: 'A-50'` FAILs `resolve_pins_terms`.
- **U8 (P1) — swaption pack** (candidate rows; each with a fixture that FAILs it):
  - `swaption_buy_sell_fold`: `npv(Sell) == −npv(Buy)` and `vega(Sell) == −vega(Buy)` (catches `buy_sell` ignored; DESIGN §13 item 3).
  - `swaption_premium_sign`: long payer and long receiver `npv > 0` at any strike.
  - `swaption_vega_sign_band`: long vega > 0; normal-model ATM band `vega ≈ N·A·√T·φ(0)·1e-4` per bp of normal vol (A = forward annuity ≈ swap dv01/(N·1e-4)), within a factor band; catches per-1%-vol (×100) and lognormal-vs-normal basis errors.
  - `swaption_delta_bounds`: `0 < Δ_payer < dv01_fwd_swap`, `−dv01_fwd_swap < Δ_receiver < 0`, ATM `Δ_payer − Δ_receiver ≈ dv01_fwd_swap`.
  - `swaption_parity`: `npv_payer(K) − npv_receiver(K) ≈` forward-swap PV at K (needs either a swap config via `--parity-config` or the config's own `ForwardPrice`/annuity measures).
  - `swaption_gamma_sign`: long gamma ≥ 0 (`IRGammaParallel`/`IRGamma` scalar).
  - `swaption_theta_sign`: long theta < 0 with the market held fixed (if `Theta` mapped).
  - `swaption_expiry`: `expiration_date` pinned to a `date`, `attributes.expiration_date` present; value on/after expiry follows the declared settlement (cash → 0/intrinsic; physical → swap), probed on a date after expiry.
  - `swaption_vol_unit`: `IRAnnualImpliedVol`/`IRDailyImpliedVol` inside unit bands (normal annual ~20–300bp; daily ≈ annual/√252).
  - `swaption_pnl_explain`: `npv(d2)−npv(d1)` vs `Δ·Δf + ½Γ·Δf² + ν·Δσ + θ·Δt` (ratio band, like `:476-488`).
- **U9 (P1) — bond pack** (same pattern): `bond_price_band` (clean per 100 in (50, 150) or the declared basis), `bond_dirty_clean` (dirty − clean = accrued ≥ 0, resets on coupon date), `bond_dv01_sign` (long < 0 per +1bp), `bond_dv01_band` (≈ modified duration × dirty price × 1e-4 × face, duration ≤ time to maturity), `bond_convexity_sign` (> 0 for a bullet), `bond_yield_unit` (bands per unit), `bond_price_yield` (price(y+1bp) − price(y) ≈ dv01), `bond_size_linearity`, `bond_carry_roll` (declared carry = coupon accrual − repo cost; roll-down sign on an upward curve), `bond_pnl_explain` (dv01·Δy + ½·convexity·Δy² + carry·Δt).
- **U10 (P1) — finite-difference probe (`fd_params`).** If the core keeps DEV-I8: assert `IRDelta(aggregation_level='Type', bump_size=1.0)` raises `NotSupportedError` with the documented text (keeps skills honest). If the core adds pass-through: probe that `bump_size=1` equals the default, `bump_size=10` stays within a linear band for a swap, `Centered` vs `Up` differ by ≈ ½·Γ·bump for an option, `scale_factor=2` doubles the value, and an unsupported `mkt_marking_options` raises. *Acceptance:* one fixture that silently ignores `bump_size` FAILs.
- **U11 (P2) — docstring drift:** add `swap_par_rate_atm` to the docstring catalogue (`:50-59`).
- **U12 (P0 for tests) — fixtures.** New toy libraries/configs: complete `tests/assets/toy_usd_swaption.yaml` (add `IRDelta`, gamma, theta, vol measures) or a second config; `tests/toylib/bond.py` + `tests/assets/toy_usd_bond.yaml` (requires a pricebt `Bond` class first, or a `ConfigInstrument` stand-in with `--pack Bond`); one `bad_*` fixture per new row (e.g. `bad_swaption_sell_not_folded.yaml`, `bad_swaption_vega_per_pct.yaml`, `bad_bond_dv01_sign.yaml`, `bad_bond_price_per_unit.yaml`). Extend `BROKEN` (`test_skill_check_asset.py:27-32`) and `test_toy_configs_pass` (`:39-46`).

### 8.3 `pricebt-verify-asset-config` docs

- **U13 (P0)** Frontmatter description (`SKILL.md:3`): replace "(for IRSwap) a rates sanity pack" with the per-instrument packs and the contract check; rerun `python tools/sync_agent_skills.py` (the mirror drifts otherwise, §3). Symptom table (`:72-103`): add swaption/bond/contract/FD rows. Pitfalls `:111`, `:125`: rewrite.
- **U14 (P1)** `references/known-answer-probes.md` (`:16`, `:25-33`): add swaption probes (put–call parity, ATM straddle symmetry, vega vs a vol bump in the library, delta vs a curve bump, expiry behaviour) and bond probes (price–yield round trip, accrued on a coupon date, dv01 vs a yield bump, carry over one day with the curve fixed). `references/independent-validation.md:53-59`: tolerance rows for vega, gamma, theta, implied vol, clean/dirty price, yield, duration, convexity.

### 8.4 `pricebt-connect-pricing-library`

- **U15 (P0)** Reframe for "the agent already knows the external library": replace the Meridian-shaped step-3 table (`SKILL.md:65-73`) with a **capability matrix worksheet** — one row per contract measure (§8.1): library call, analytic/AD/bump, native unit and sign, conversion expression, batching. The agent fills it from its knowledge of the library *before* writing YAML; the checker's contract row (U3) verifies it.
- **U16 (P0)** `references/discovery-questionnaire.md`: extend §5 (`:46-57`, currently "`npv`, `dv01`, `par_rate`, plus a ladder") and §6 (`:59-`) with: vol surface/cube handle by date and its point-in-time source; normal vs lognormal vs shifted; vol quote unit; vega per 1bp normal vs per 1% lognormal; gamma/vanna/volga/theta availability and definitions (theta per calendar or business day, with or without carry); how the library bumps (size, direction, curve vs quotes — this is where FD parameters map); `buy_sell` and straddle support; exercise/settlement (cash/physical, expiry behaviour); bond identifiers and static-data resolution, clean vs dirty, price basis (per 100 / per unit), accrued convention, yield convention (street/true, compounding), settlement lag, ex-coupon, repo/financing curve for carry.
- **U17 (P1)** `references/convention-conversions.md` (`:7-19`, `:51-57`): add rows for vega (per 1% ↔ per bp; lognormal ↔ normal needs a model, not a scale), vol quotes (decimal/percent/bp), gamma (per bp² → `ccy_per_bp2`), theta (per day vs per year), bond price per 100 ↔ per unit of `size`, dv01 per 100 face, duration → dv01, convexity scaling; sign self-tests for a long payer swaption and a long bond.
- **U18 (P1)** `references/config-template.yaml`: add swaption and bond templates (or split into `config-template-irswap.yaml`, `-irswaption.yaml`, `-bond.yaml`) with the contract measures as TODOs.
- **U19 (P1)** Worked example: extend the fictional SDK (`example/meridian_sdk/__init__.py`, `MEASURES` at `:57`) with a swaption and a bond (measure codes such as `VEGA_LN_PCT`, `GAMMA`, `THETA_1D`, `CLEAN_PX`, `ACCRUED`, `YTM_PCT`, `MOD_DUR`, `CONVEXITY`), each with deliberately non-pricebt conventions and one-error mistakes, tested like `test_skill_connect_example.py`. Keep the "fictional" first line (`test_skills_library.py:104-107`).
- **U20 (P0, defect)** Fix `SKILL.md:110` and `:217-218` ("only a WARN") → "`swap_par_rate_atm` FAILs; `swap_par_rate_unit` WARNs".

### 8.5 `pricebt-asset-config-cookbook`

- **U21 (P1)** `references/patterns.md`: new patterns — swaption `resolve` (expiry/tenor pinning, strike grammar incl. `'A-50'`, `buy_sell` fold, straddle policy); bucketed `IRVega` with `'expiry;tenor'` keys (DESIGN §8.2/§13); gamma/volga as analytic vs bumped; bond `resolve` (identifier → pinned static data, settlement), clean/dirty/accrued functions, carry/roll functions; FD-parameter handling pattern (once the core mechanism exists). Unit table (`SKILL.md:35-44`) gets vol/price/second-order rows; `:46` "rates pack" → "the instrument's pack".
- **U22 (P1)** `references/error-catalogue.md:45`: rewrite when DEV-I8 changes; add rows for the new contract/pack errors.

### 8.6 `pricebt-port-gs-notebook` and a Portfolio/PRR skill

- **U23 (P1)** New skill **`pricebt-portfolio-risk`** (or a `references/` page under port): `Portfolio` construction/nesting, `resolve`, `calc`/`price`/`dollar_price`, indexing `result[measure][name]` / `result[name][measure]`, `.aggregate()` (group-aggregated ladders), `.to_frame()` **as implemented** (`src/pricebt/risk/results.py:491-514`: `instrument_name`/`risk_measure`/`value` only, bucketed summed), `PricingContext(pricing_date=...)`, `HistoricalPricingContext` (series/ladder shapes), multi-currency with `Price(currency=...)`, and a table of gs features that do **not** port (nested `portfolio_name_N` pivots, `display_options`, `PnlExplain`, `'=solvefor(...)'` notionals). Worked on the toy swap + toy swaption, executed by `tests/skills/test_skill_portfolio_risk.py`.
- **U24 (P1)** `pricebt-port-gs-notebook/SKILL.md`: `:17` add `from pricebt.markets.portfolio import Portfolio`, `from pricebt.markets import PricingContext, HistoricalPricingContext`, `from pricebt.backtests.backtest_objects import PnlDefinition, PnlAttribute`; `:20` add Bond status; `:22` FD parameters per the core decision; add a "pricing-and-risk notebooks" row set: `030005`, `030009`, `030007` (→ `PnlDefinition`), `01_rates/04`, `08`, `23_solve_vanna_&_volga` (IRVanna/IRVolga must be in the catalogue; `solvefor` not supported).

### 8.7 P&L decomposition (new skill + spot checks + tearsheet)

- **U25 (P1)** New skill **`pricebt-pnl-attribution`** with `scripts/pnl_attrib.py`:
  - builders `swaption_pnl_definition(units)` → `PnlDefinition` of delta (`IRDelta(Type)` × `IRFwdRate`), gamma (`IRGammaParallel` × `IRFwdRate`, second order), vega (`IRVega(Type)` × vol measure), volga (`IRVolga` × vol, second order), vanna (cross term: not expressible as one `PnlAttribute`, so computed in the script from `risk_summary`); theta as `Theta × Δdays` (script-side, since gs attributes are market-move only); residual = ΔTotal − Σ.
  - `bond_pnl_definition(units)` → dv01 (`IRDelta(Type)` × yield), convexity (`IRGammaParallel` × yield, second order), carry and roll-down (script-side from `Theta` or dedicated carry/roll functions, with the coupons-not-booked caveat, `pricebt-architecture/SKILL.md:48`), residual.
  - scaling factors derived from the **declared units** of the mapped functions (e.g. `IRFwdRate` in bp → 1, in pct → 100, in decimal → 1e4), so a config in gs percent and one in bp give the same attribution; this ties to U1.
  - acceptance: on the toy swaption, Σ attribution + residual == ΔTotal exactly; residual share below a stated bound on daily steps; a sign-flipped vega fixture makes the vega bucket anti-correlated with ΔTotal.
- **U26 (P1)** `spot_check.py:161-183`: add `check_pnl_attribution(bt)` that, when `bt.pnl_explain()` is not None, reports each attribute's share and the residual share (WARN above a threshold); keep the single-factor check for swaps. `SKILL.md:22-23`, `:65` and `manual-checks.md:19`, `:45` get option/bond variants (reprice by hand from `strike`/`expiration_date`/`buy_sell`; "buy ↔ sell flips P&L exactly").
- **U27 (P1)** `tearsheet.py` (`:98-105`, `:300-312`) and `references/metrics-definitions.md` (`:53-56`): accept a list of exposure measures (dv01, vega, gamma, theta) and an optional attribution frame; add a stacked attribution chart and "exposure use" metrics per greek; the single `risk` stays for P&L-in-bp.
- **U28 (P1)** `run_study.py`: `_report_risk` (`:47-55`) and `_rate_series` (`:58-69`, `"par_rate"` at `:66`) read the measure/function names from the spec (U29) or the contract; write `pnl_attribution.csv/md` when the spec sets a `pnl_explain` block; add swaption/bond entries to `STANDARD_CAVEATS_BY_ARCHETYPE` (`:40-44`).

### 8.8 Strategy intake / recipes / review / methodology

- **U29 (P1)** `spec.py` and template: add `instruments.<k>.direction_kwarg` (or infer from the contract: `buy_sell` for swaption/bond); generalise `_direction` (`:124-126`) and the curve_trade rule (`:239-245`); add signal types (implied-vol z-score, vol carry, yield/ASW z-score, bond carry/roll) to `SIGNAL_TYPES` (`:29`); add `sizing.method: vega_target` and `duration`/`dv01` for bonds; a `pnl_explain:` block; make `parse_risk` (`:301-316`) parse numeric kwargs (needed once FD params pass through). Template `:5`, `:20-29`, `:43-45`, `:73`, `:80` and question bank `:12`, `:15`, `:30` get option/bond defaults; PM-language table (`SKILL.md:48-59`) gets "sell vol", "straddle", "gamma scalp", "vega-neutral", "bond carry/roll", "asset swap", "basis".
- **U30 (P0 for swaptions)** `recipes.py`: `direction_sign` (`:111-115`) and `flipped` (`:118-123`) must use the instrument's direction kwarg from the contract (`buy_sell` for swaptions/bonds) — today `flipped` turns a payer swaption into a receiver instead of the opposite position; `BOOK_DV01` (`:76`) becomes a per-archetype sizing/hedge measure (vega for vol trades); `transaction_model('notional_bp')` (`:132`) uses the contract's size kwarg; new archetypes (delta-hedged swaption with `HedgeAction(IRDelta(Type), swap)`, vega-neutral calendar, short straddle carry, bond carry/roll with periodic roll into the on-the-run identifier). *Acceptance:* a momentum spec on a swaption produces opposite `buy_sell`, same `pay_or_receive`, on the `_opp` leg.
- **U31 (P2)** `pricebt-adversarial-review/references/checklist.md` (A2 `:10`, A4 `:12`), `reviewer-prompts.md:20-25`: option items (vol surface point-in-time, smile dynamics assumption, expiry/exercise, vega-bucket concentration, theta sign) and bond items (clean vs dirty, accrued and coupons not booked, repo financing, on-the-run roll and identifier survivorship).
- **U32 (P2)** `pricebt-research-methodology/references/rates-mapping.md:7-12`: vega/theta budgets and duration sizing rows.
- **U33 (P2)** `pricebt-enterprise-integration/SKILL.md:71` "rates pack" → packs; add "record vol surfaces and bond static data as data" to step 8.

### 8.9 Routing, governance, architecture

- **U34 (P1)** `pricebt-architecture/SKILL.md`: `:37` MUST-4 add vol and price units; `:48` coupons item: bonds; `:64` list the IR catalogue link (U1); add Portfolio/PRR and `PnlDefinition` to "Where things live".
- **U35 (P1)** `skills/README.md` journeys and `pricebt-start-here/SKILL.md` tables: rows for "add a swaption/bond asset" (connect + contracts + verify packs), "price and risk a portfolio" (U23), "explain P&L" (U25).
- **U36 (P0 when adding skills)** For each new skill: directory `skills/pricebt-<name>/SKILL.md` with frontmatter (`name` = dir, description ≤ 1024 chars), < 350 lines, README row, `tests/skills/test_skill_<name>.py`, optional addition to `expected` in `test_skills_library.py:31-36`, then `python tools/sync_agent_skills.py` and commit the new `.claude/skills/<name>/SKILL.md` stub. Any description change on an existing skill (U13 and others) also requires the regenerate-and-commit step.

---

## 9. Open decisions (need the user or the core design, not the skills)

1. **FD parameter pass-through mechanism** (DEV-I8 today): injected dict vs function kwargs vs per-variant functions. U10, U16, U21, U22, U24 and `parse_risk` depend on it.
2. **Bond instrument**: add a generated `Bond` to `pricebt.instrument` (gs fields `buy_sell, identifier, identifier_type, size, settlement_date, settlement_currency`) or use `ConfigInstrument`? U4, U9, U12 depend on it.
3. **Vol units and basis**: which declared units the contract allows for `IRAnnualImpliedVol`, `IRDailyImpliedVol` and `IRVega` (gs docstring "in percent" vs tutorial `* 10000`; normal vs lognormal). Likely a new DEV id like DEV-I7.
4. **Bond yield/carry/roll measure names**: gs has no bond-specific yield, carry or roll measure in the IR catalogue; choose between reusing `IRSpotRate`/`IRFwdRate`/`Theta`, adding custom `RiskMeasure(name=...)` entries (then the checker's allowlist, U5), or treating them as functions consumed only by `pnl_attrib.py`.
5. **Where the contract lives**: inside `check_asset.py`, a shared `contracts.py` imported by check_asset, recipes, run_study and pnl_attrib, or a YAML under `skills/`. A single shared module avoids three copies of the direction/size/measure tables.
6. **Mandatory vs recommended** measures per instrument: e.g. is `IRGammaParallel` mandatory for a swaption config, or only for strategies that request it?

## 10. Defects found in passing (skills only)

| # | Where | Defect |
|---|---|---|
| D1 | `skills/pricebt-connect-pricing-library/SKILL.md:110`, `:217-218` | says par-rate-in-percent is only a WARN; the checker FAILs `swap_par_rate_atm` and `tests/skills/test_skill_check_asset.py:124-133` asserts it |
| D2 | `skills/pricebt-verify-asset-config/scripts/check_asset.py:50-59` | docstring omits the emitted `swap_par_rate_atm` row (`:466-474`) |
| D3 | `skills/pricebt-strategy-recipes/scripts/recipes.py:111-123` | `direction_sign`/`flipped` are wrong for swaptions (use option type, ignore `buy_sell`); latent until a swaption momentum/curve spec is run |
| D4 | `skills/pricebt-strategy-intake/scripts/spec.py:312-314` | `parse_risk` passes every kwarg as a string; harmless while only `aggregation_level`/`currency` are accepted |
| D5 | `skills/pricebt-verify-asset-config/scripts/check_asset.py:85`, `:123-129` | `_ATM_RE` matches only `atm...`, not the DESIGN §13 option grammar `'A-50'`; an unpinned `'A-50'` strike would pass `resolve_pins_terms` |

## Status after v2-ir-risk (2026-09-29)

Appended note; the body above is the pre-implementation evidence and is left as written.

- **Done:** U1-U18 and U20-U36 (commit `86dfeae`; the skills tests under `tests/skills/` cover them).
- **Skipped on purpose:** U19 (a swaption and bond extension of the fictional `meridian_sdk` worked example). The runnable contract templates `skills/pricebt-connect-pricing-library/references/config-template-swaption.yaml` and `config-template-bond.yaml`, filled with the toy library by `tests/skills/test_skill_connect_example.py`, carry the same teaching load.
- **Resolved concern: "a per-year Theta passes every check".** It no longer does. `check_asset_ir.py`'s `ir_theta` row FAILs a theta that "looks per year" against the step's implied one-day carry, `ir_taylor` flags the step whose Taylor sum it dwarfs (`tests/skills/test_skill_check_asset_ir.py`, fixture `bad_swaption_theta_per_year.yaml`), and the P&L-attribution grade (`skills/pricebt-pnl-attribution/scripts/attribution.py`, `grade`/`grade_reason`: "per-year theta, a x100 or x1e4 unit?") fails the residual it leaves in a backtest.
