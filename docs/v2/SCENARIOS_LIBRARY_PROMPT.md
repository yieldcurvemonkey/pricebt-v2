# Prompt: wire gs scenario contexts into asset configs that price on your own library

A hand-off prompt for an AI coding agent that works where your pricing and market-data library is
installed. Your IRSwap and IRSwaption asset configs and their market provider configs already
price. The agent adds a `scenarios:` section to each config, so unchanged gs scenario code
(`RollFwd`, rate and vol shocks, `MultiScenario`) prices through your library. It then builds the
terminal payoffs of a swaption straddle and fly.

Contract: [`SCENARIOS.md`](SCENARIOS.md). Worked example of every transform on the toy library:
`tests/toylib/scenarios.py`. Skill: [`pricebt-scenarios`](../../skills/pricebt-scenarios/SKILL.md).

What shapes it (all verified in this repository):

- **The prompt names no library and assumes nothing about one.** The agent builds an inventory of
  the library calls your configs already make before it writes anything. The strict IR contract
  (`src/pricebt/risk/contracts.py`) obliges every IRSwap/IRSwaption config to map `IRDelta`
  (along the library's own parallel shift), `IRVega` (+1bp normal vol), `Theta` (one day,
  `DF(x)/DF(t+1d)`), `IRFwdRate`, `Annuity`, `ParSpread`, `ProbabilityOfExercise` and
  `ExpiryInYears`. So the transforms reuse the config's own bump and carry code, and those
  measures are the known answers.
- **The gates are one script, proven first.** Section 6 embeds `verify_scenarios.py`. Unedited, it
  runs on the toy configs and prints `51 gates, 0 failed: ALL PASS`. Each of three planted bugs
  (a shock sign flipped, a roll that ignores its date, exercise decided from history) makes it
  exit 1. The delta-by-shock check alone cannot see the first: it is a ratio, blind to the
  shock's sign and size, so a units gate sits next to it.
- **It does not use the `scenario_grid.py` CLI.** The CLI loads no market providers, takes no
  location, and its "another currency" check shocks EUR when no currency is given. The in-process
  `check_transforms(inst, asset="<CCY>")` has none of these problems.
- **Written for a model weaker than the one that built pricebt's scenarios:** explicit gates,
  stop conditions, a transform skeleton, and excerpts of working-practice skills (section 9).

Fill in section 0, then give the agent everything below the line.

---

# Task: add gs scenario support to your pricebt asset configs, through your own library

## 0. Filled in by the user (ask once if any line is blank)

- Repository root: the pricebt-v2 clone that holds your private configs: `<path>`
- How to run Python here (interpreter, `PYTHONPATH=src;tests` on Windows or `src:tests` on POSIX,
  anything the library needs): `<command>`
- Asset configs in scope (default: every IRSwap and IRSwaption config): `configs/assets/<...>.yaml`
- Market provider configs: `configs/markets`
- Market data location the configs answer to (`LDN` if never set): `<location>`
- Currency of each config: `<CCY>`
- `D1`: a Monday to Thursday with market data on which the test trades pay and fix nothing the next
  day: `<date>`
- `D3`: a recent business date with market data (D3 + 1 year has no market data yet): `<date>`
- Where the library's source or documentation can be read (a path, a package name, or `none`):
  `<where>`
- Swaption settlement (physical, cash, or "don't know"): `<settlement>`

The examples below use `<CCY>` and `<location>`. Substitute the values above everywhere.

## 1. Get the scenarios code (do this first)

The scenario work is on `main` of `https://github.com/yieldcurvemonkey/pricebt-v2`, from merge commit
`fbdd9f6` ("Merge v2-scenarios into main"); your configs need its market providers too. Use `main`.
(The branch `v2-scenarios` contains `main` as well, plus this prompt; any commit that contains
`fbdd9f6` works.)

Every git command names its tree with `-C <path>`. In this whole task you run exactly two git
commands that change anything: `fetch` and `switch` below.

1. **Baseline before pulling.** Create `reports/scenarios/` (`reports/` is git-ignored). For each
   config, run the asset checker and save its output:
   `python skills/pricebt-verify-asset-config/scripts/check_asset.py <config> --markets configs/markets --location <location> --date <D1> --date <D3> > reports/scenarios/before_pull_<name>.txt`
   If `--help` lists `--no-backtest`, add it here and in step 10.
2. `git -C <path> status --porcelain` must print only `??` lines (your untracked private files).
   Any other line: stop and ask.
3. Record the rollback point: `git -C <path> branch --show-current` and `git -C <path> log --oneline -1`.
4. `git -C <path> remote -v`: find the remote that points at the GitHub repository; call it
   `<remote>` below. If none does: stop and ask.
5. `git -C <path> fetch <remote>`
6. `git -C <path> merge-base --is-ancestor fbdd9f6 <remote>/main && echo ok` must print `ok`.
7. `git -C <path> log --oneline <remote>/main..HEAD` must print nothing. If it prints commits, your
   branch has work that `main` lacks: stop and ask.
8. `git -C <path> switch -c pricebt-scenarios <remote>/main`. Your untracked configs stay where they
   are. If git refuses because an untracked file would be overwritten: stop and ask. Never delete,
   move or rename it.
9. `git -C <path> log --oneline -1` shows `fbdd9f6` or later, and `git -C <path> status --porcelain`
   shows the same `??` lines as step 2.
10. Rerun step 1 into `reports/scenarios/after_pull_<name>.txt`.

**Do not create a git worktree**, whatever your general instructions say: your private configs are
untracked files of this checkout, and a worktree would not have them.

**Gate 1 (environment).** Each must hold before you go on:

- `python -c "import pricebt; print(pricebt.__file__)"` prints a path inside `<path>/src`.
  Otherwise an installed copy shadows the checkout: fix `PYTHONPATH`, do not continue.
- `python -c "from pricebt.risk import RollFwd, CarryScenario, MultiScenario, MarketDataShockBasedScenario, MultipleScenarioResult"` succeeds.
- `python -m pytest tests/test_scenarios.py tests/test_scenarios_notebook.py tests/skills/test_skill_scenarios.py -o addopts= -p no:cacheprovider -q`
  reports 0 failed. This proves pricebt's scenario layer works in this environment. From here on,
  a failure is in your configs or transforms, not in pricebt.
- The `after_pull` files have the same PASS/FAIL rows as the `before_pull` files. If not, stop and
  report the differing rows: the pull changed how your configs price, and fixing that is not this
  task.

Rollback, for the user: `git -C <path> switch <the branch from step 3>`.

## 2. Goal and definition of done

gs scenario code runs unchanged and prices through your library:

```python
from pricebt.risk import MarketDataPattern, MarketDataShock, MarketDataShockBasedScenario, MultiScenario, RollFwd

def rate(bp):
    return MarketDataShockBasedScenario({MarketDataPattern("IR", "<CCY>"): MarketDataShock("Absolute", bp / 1e4)}, name=f"{bp:+d}bp")

straddle = IRSwaption("Straddle", "10y", "<CCY>", expiration_date="1y", strike="ATM", notional_amount=10e6)
straddle.resolve()                                    # pin the strike first
expiry = straddle.resolved_terms["<expiry key>"]
with RollFwd(date=expiry), MultiScenario([rate(bp) for bp in range(-100, 101, 10)]):
    payoff = straddle.price()                         # a V with its point at the strike
```

Done means all of the following, each shown with command output in the final report:

1. Every in-scope config has a `scenarios:` section mapping at least
   `MarketDataShockBasedScenario` (absolute `'IR'` and `'IR Vol'` shocks), `RollFwd` and
   `CarryScenario`, and every config maps the same set of types.
2. `reports/scenarios/verify_scenarios.py` (section 6) exits 0 for every currency, and you have
   seen it exit 1 for each planted bug of step 8.
3. Base numbers are unchanged: the parity check of step 1 reports 0 differences.
4. `reports/scenarios/straddle_payoff_<CCY>.csv` and `fly_payoff_<CCY>.csv` exist.
5. Nothing tracked by git changed, and you made no commit.

Optional, only with evidence that the library supports it (step 7): `CurveScenario`,
`MeasureScenario`, and the other types.

## 3. Read first, in this order

1. `docs/v2/SCENARIOS.md`, all of it: the contract. Section 7 is the checklist for transforms.
2. `skills/pricebt-scenarios/SKILL.md`: procedure and pitfalls.
3. `tests/toylib/scenarios.py`, all of it: one transform per scenario type on the toy library.
   Copy its *shape* (pattern matching, refusing unsupported fields, returning a new market). Do
   not copy its curve arithmetic: your transforms call your library.
4. The `scenarios:` blocks of `tests/assets/toy_usd_swaption.yaml` and `tests/assets/toy_usd_irs_full.yaml`.
5. `tests/toylib/swaption.py`, `_expiry_fwd`: how the toy makes the exercise decision on the
   scenario curve (step 6).
6. In `tests/test_scenarios.py`: `test_delta_shock_equivalent_010204`,
   `test_vega_shock_equivalent_010205`, `test_rollfwd_moves_the_valuation_date`,
   `test_rollfwd_lifecycles_a_swaption_03`, `test_terminal_payoff_of_a_straddle`,
   `test_terminal_payoff_of_a_fly_is_a_tent`, `test_an_unresolved_instrument_resolves_under_the_scenario`.
7. `skills/pricebt-scenarios/scripts/scenario_grid.py`: `rate_shock`, `vol_shock`,
   `check_transforms`, `payoff_grid`.
8. `src/pricebt/risk/contracts.py`: the `doc` of `Price`, `IRDelta`, `IRVega`, `Theta`,
   `IRFwdRate`, `IRAnnualImpliedVol`, `Annuity`, `ParSpread`, `ProbabilityOfExercise` and
   `ExpiryInYears`. Read only; your configs already satisfy them. These definitions are your
   known answers.
9. Each in-scope config, top to bottom, and the market provider configs it names.

## 4. Hard rules

1. **Edit only the in-scope config files** and new files under `reports/scenarios/`. Before you edit
   a file, `git -C <path> ls-files --error-unmatch <file>` must fail (the file is untracked). If it
   succeeds, the file is tracked and this repository is public: stop and ask. Never edit anything
   under `src/`, `tests/`, `tools/`, `skills/`, `docs/` or `.claude/`.
2. **No git writes** beyond section 1: no `add`, `commit`, `stash`, `reset`, `restore`, `checkout --`,
   `clean`, `push`. Your private configs are untracked but **not** ignored, so `git add -A` would
   stage them for a public repository.
3. **Do not invent library calls.** Every library call you write is either (a) already made by the
   config, or (b) found in the library's source or documentation at `<where>`, cited by file and
   line (or page) in the inventory of step 2. If neither, that capability is unavailable: leave the
   scenario type unmapped and say so in the report. pricebt then raises `NotSupportedError`
   whenever that type is used, which is correct.
4. **Never fake a scenario.** Map a type to `'market'` (identity) only if the asset is genuinely
   unaffected by it, and write why next to it. A scenario field your transform cannot honour (a point
   shock, caps, floors, `mkt_class`, an unsupported shock type) raises `NotImplementedError` naming
   the field. It is never silently ignored.
5. **A transform returns a new market and never mutates the one it receives.** pricebt caches the
   unshocked market, and one market provider's object is shared by several assets.
6. **Base numbers must not change.** You may refactor an existing helper to reuse it, but only while
   the parity check (step 1) stays at 0 differences.
7. **Do not loosen a gate.** Never change a tolerance or an expected value to make a check pass. If
   a gate fails twice after two different fixes, stop and report the numbers.
8. **If you believe pricebt is wrong**, reproduce it on the toy configs (`tests/assets/`) and report
   the repro. Do not patch pricebt.
9. **Stop points:** ask the user only at the stops this prompt names, once, with the evidence.
   Elsewhere, decide, and record the decision and its reason in the report.

## 5. What pricebt hands your config

Condensed from `docs/v2/SCENARIOS.md`. Design around these facts; do not work around them.

```yaml
scenarios:                                   # scenario_type -> transform expression
  MarketDataShockBasedScenario: 'scn_apply_shocks(market, scenario, pricebt_currency)'
  RollFwd: 'scn_roll(market, scenario, pricebt_from_date, pricebt_date)'
  CarryScenario: 'scn_roll(market, scenario, pricebt_from_date, pricebt_date)'
  MeasureScenario: {expr: 'scn_set_measure(market, scenario, resolved)', scope: trade}   # optional, step 7
```

- **Where the code goes.** A scenario expression runs in the asset config's own namespace, so
  functions defined in its `code:` block (and modules in `imports:`) are callable from it. Put the
  transforms in `code:`, next to the helpers they reuse, and prefix their names with `scn_` so
  they cannot clash with existing helpers. If you keep one file per currency with identical
  bodies, put the same transforms in every body, so the bodies stay identical (`pricebt_currency`
  makes the transforms currency-agnostic).
- **Injected names:**
  - `market`: your provider's object for the date, or the previous scenario's output.
  - `scenario`: the scenario as plain data.
  - `pricebt_from_date` / `pricebt_date`: the valuation date before and after this scenario.
  - `pricebt_currency`, `pricebt_asset`, `pricebt_csa`: as everywhere else.
  - `resolved`: `scope: trade` only.
- **The `scenario` dict** has every field of the scenario (None when unset), enums as their values
  and nested objects as dicts:

  ```python
  {"scenario_type": "MarketDataShockBasedScenario", "name": "+10bp",
   "shocks": [{"pattern": {"mkt_type": "IR", "mkt_asset": "<CCY>", "mkt_class": None, "mkt_point": None, ...},
               "shock": {"shock_type": "Absolute", "value": 0.001, "precision": None, "cap": None, "floor": None, ...},
               "name": None}]}
  {"scenario_type": "RollFwd", "date": "1y", "realise_fwd": True, "holiday_calendar": None, "name": None}
  {"scenario_type": "CarryScenario", "date": ..., "time_shift": None, "roll_to_fwds": True, ...}
  ```

  `scenario["date"]` may be a relative rule such as `'1y'`. **Never read it. Use `pricebt_date`**,
  which pricebt has already computed.
- **Units:** shock values are decimals (`10 / 1e4` is 10bp of rate; `1e-4` is 1bp of normal vol).
  `CurveScenario` shifts are in bp. `MeasureScenario.value` is in your config's unit for that
  measure.
- **Order:** active scenarios apply outermost first, each transform's output feeding the next.
  In the payoff recipe the roll runs **before** the shock.
- **Matching:**
  - A pattern naming data your market does not hold must leave the market unchanged (an EUR
    shock on a USD asset; `'IR Vol'` on a swap; `'FX'`).
  - An active scenario type with no entry in the config raises `NotSupportedError`.
  - `CompositeScenario` and `MultiScenario` take no entry: pricebt applies their members.
- **Caching:** results are cached per (asset, date, csa, market date, scenarios), so a transform
  must be deterministic.
- **Valuation date:** under a roll, every function of the config sees `pricebt_date` = the rolled
  date.
- **Resolution:** an *unresolved* instrument priced under a scenario resolves under it (an `'ATM'`
  strike re-strikes on the shocked curve). Resolve first, outside the scenario.
- **`build_on: resolve_date`:** such a trade is built on its resolution date's base market, while
  functions get the scenario `market`.
- **Refusals and FX:** `run_backtest`, `measure_series` and `PnlExplain` refuse an active scenario.
  FX conversion stays unshocked.

## 6. The gate script (prove it before you trust it)

Save the script below as `reports/scenarios/verify_scenarios.py`, unchanged, and run it from the
repository root. It must print `51 gates, 0 failed: ALL PASS` and exit 0 on the toy configs; this
proves the script and your environment. Only then copy the CONFIG block values from section 0 and
your configs:

- `RATE_UNIT`, `VOL_UNIT`: the `unit:` your config declares for `IRFwdRate`/`ParSpread` and for
  `IRAnnualImpliedVol`.
- `EXPIRY_KEY`: the key of the expiry date in `pay.resolved_terms` (print them).
- `STRIKES`: the strike spellings your `resolve` accepts for K-50bp, K and K+50bp.
- `HAS_STRADDLE`: whether `IRSwaption("Straddle", ...)` resolves on your config.

Set `STEPS` to the steps you have finished, and grow it as you go: `{3}`, then `{3, 4}`, and so on.

```python
"""Scenario gates for an IRSwap + IRSwaption asset-config pair (docs/v2/SCENARIOS_LIBRARY_PROMPT.md).

Unedited it runs on the toy configs and must print ALL PASS: that proves the script and the
environment. Then edit the CONFIG block only. Run from the repository root with PYTHONPATH=src;tests
(PowerShell: $env:PYTHONPATH="src;tests"). Exit code 0 = every gate of STEPS passed.
"""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, "skills/pricebt-scenarios/scripts")
import scenario_grid as sg  # noqa: E402

from pricebt.instrument import IRSwap, IRSwaption  # noqa: E402
from pricebt.markets import PricingContext  # noqa: E402
from pricebt.markets.portfolio import Portfolio  # noqa: E402
from pricebt.risk import (Annuity, CarryScenario, CompositeScenario, ExpiryInYears, IRAnnualImpliedVol, IRFwdRate,  # noqa: E402
                          MarketDataPattern, MarketDataShock, MarketDataShockBasedScenario, MarketDataShockType,
                          ParSpread, ProbabilityOfExercise, RollFwd, Theta)
from pricebt.session import PricebtSession  # noqa: E402

# ---- CONFIG: edit this block only --------------------------------------------------------------
ASSETS = ["tests/assets/toy_usd_swaption.yaml", "tests/assets/toy_usd_irs_full.yaml"]
MARKETS = []                 # your market provider files or directory, e.g. ["configs/markets"]
LOCATION = None              # the market_data_location your configs answer to, e.g. "NYC"; None = LDN
CCY = "USD"                  # the configs' currency
D1 = date(2024, 3, 5)        # Mon-Thu with market data; the test trades pay and fix nothing on D1 + 1 day
D3 = date(2024, 3, 5)        # a recent date: D3 + 1y must be a date with NO market data yet
RATE_UNIT = 1.0              # IRFwdRate and ParSpread: config units per bp (1.0 if bp, 1e-4 if decimal)
VOL_UNIT = 1.0               # IRAnnualImpliedVol: config units per bp of normal vol (1.0 if bp)
EXPIRY_KEY = "expiration_date"          # the resolved term that holds the swaption's expiry date
STRIKES = ("ATM-50", "ATM", "ATM+50")   # the config's strike spellings for K-50bp, K, K+50bp
HAS_STRADDLE = True          # False if the config cannot price IRSwaption("Straddle", ...)
N = 10e6                     # notional of every test trade
STEPS = {3, 4, 5, 6}         # the prompt steps whose gates run: add each step as you finish it
# -----------------------------------------------------------------------------------------------

PricebtSession.use(assets=ASSETS, **({"markets": MARKETS} if MARKETS else {}))
LOC = {"market_data_location": LOCATION} if LOCATION else {}
PricingContext.current = PricingContext(D1, **LOC)
f = float
rows = []
os.makedirs("reports/scenarios", exist_ok=True)


def gate(step, name, fn):
    if step not in STEPS:
        return
    try:
        ok, detail = fn()
    except Exception as exc:  # a gate that raises fails
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    rows.append((step, name, bool(ok), detail))


def close(a, b, rel=1e-6, abs_=1e-6):
    return abs(a - b) <= max(rel * max(abs(a), abs(b)), abs_)


def swpt(direction, strike="ATM", expiry="1y"):
    return IRSwaption(direction, "10y", CCY, expiration_date=expiry, strike=strike, notional_amount=N)


swap = IRSwap("Pay", "10y", CCY, notional_amount=N)
pay, rec = swpt("Pay"), swpt("Receive")
stl = swpt("Straddle") if HAS_STRADDLE else Portfolio([swpt("Pay"), swpt("Receive")])
fly_legs = [swpt("Pay", k) for k in STRIKES]
fly = Portfolio([fly_legs[0], fly_legs[1].clone(quantity_=-2), fly_legs[2]])
for x in (swap, pay, rec, stl, fly):
    x.resolve()  # pin strikes and dates before any scenario (an unresolved trade re-strikes under one)
expiry = pay.resolved_terms[EXPIRY_KEY]


def price(x):
    v = sg._value(x.price())  # a future inside an entered PricingContext
    return f(v.aggregate() if isinstance(x, Portfolio) else v)


def _under(inst, scenario):
    with scenario:
        return price(inst)


def transform_rows(inst, step):
    if step not in STEPS:
        return
    for r in sg.check_transforms(inst, asset=CCY):
        if "roll" in r["check"] and 5 not in STEPS:
            continue  # RollFwd is mapped in step 5
        gate(step, f"{type(inst).__name__}: {r['check']}", lambda r=r: (r["ok"] is not False, r["detail"]))


def unchanged_after(inst, scenario):
    base = price(inst)
    with scenario:
        price(inst)
    return price(inst) == base, "base repriced bit-identical after the scenario"


def loud_or_moves(inst, scenario):
    """A scenario the transform cannot honour must raise, never price as the base."""
    base = price(inst)
    try:
        with scenario:
            v = price(inst)
    except Exception as exc:
        return True, f"raises {type(exc).__name__} (unsupported, loud)"
    return v != base, f"moved by {v - base:+.6g}" if v != base else "silently ignored"


# ---- step 3: rate shocks (swap) ----------------------------------------------------------------
transform_rows(swap, 3)


def rate_units():
    f0 = f(swap.calc(IRFwdRate))
    with sg.rate_shock(10, CCY):
        f1 = f(swap.calc(IRFwdRate))
    move_bp = (f1 - f0) / RATE_UNIT
    return 9.0 <= move_bp <= 11.0, f"+10bp moved IRFwdRate by {move_bp:.6f}bp"


gate(3, "units: a +10bp shock moves IRFwdRate by 9..11bp", rate_units)
gate(3, "no mutation: rate shock", lambda: unchanged_after(swap, sg.rate_shock(25, CCY)))
gate(3, "an IR Vol shock leaves the swap unchanged", lambda: (close(price(swap), _under(swap, sg.vol_shock(10, CCY)), 1e-12, 0), ""))
gate(3, "Proportional shock: raises or moves", lambda: loud_or_moves(swap, MarketDataShockBasedScenario({MarketDataPattern("IR", CCY): MarketDataShock(MarketDataShockType.Proportional, 0.1)})))
gate(3, "point shock (mkt_point): raises or moves", lambda: loud_or_moves(swap, MarketDataShockBasedScenario({MarketDataPattern("IR", CCY, mkt_point=("10Y",)): MarketDataShock(MarketDataShockType.Absolute, 1e-3)})))


# ---- step 4: rate and vol shocks (swaption) ----------------------------------------------------
transform_rows(pay, 4)


def vol_units():
    v0 = f(pay.calc(IRAnnualImpliedVol))
    with sg.vol_shock(1, CCY):
        v1 = f(pay.calc(IRAnnualImpliedVol))
    return close((v1 - v0) / VOL_UNIT, 1.0, 1e-9, 1e-9), f"+1bp vol moved IRAnnualImpliedVol by {(v1 - v0) / VOL_UNIT:.12f}bp"


gate(4, "units: a +1bp vol shock moves IRAnnualImpliedVol by 1bp", vol_units)
gate(4, "no mutation: vol shock", lambda: unchanged_after(pay, sg.vol_shock(5, CCY)))
gate(4, "a vol shock leaves the swap unchanged", lambda: (price(swap) == _under(swap, sg.vol_shock(5, CCY)), ""))


def parity_straddle():
    with CompositeScenario([sg.rate_shock(25, CCY), sg.vol_shock(5, CCY)]):
        a, b, c = price(stl), price(pay), price(rec)
    return close(a, b + c, 1e-9, 1e-6), f"straddle {a:.6g}, payer + receiver {b + c:.6g}"


gate(4, "straddle == payer + receiver under +25bp / +5bp vol", parity_straddle)

# ---- step 5: RollFwd and CarryScenario ---------------------------------------------------------


def theta_identity(inst):
    base, th = price(inst), f(inst.calc(Theta))
    with RollFwd(date=D1 + timedelta(days=1)):
        p1 = price(inst)
    return close(p1 - base, th, 1e-6, 1e-6), f"roll one day {p1 - base:.8g}, Theta {th:.8g}"


def forwards_held():
    f0 = f(pay.calc(IRFwdRate))
    with RollFwd(date=D1 + timedelta(days=91)):
        f1, e1 = f(pay.calc(IRFwdRate)), f(pay.calc(ExpiryInYears))
    e0 = f(pay.calc(ExpiryInYears))
    return close(f0, f1, 1e-8, 1e-10) and close(e1, e0 - 91 / 365, 1e-12, 1e-12), f"IRFwdRate {f0} -> {f1}; ExpiryInYears {e0:.6f} -> {e1:.6f}"


def carry_equals_roll():
    t = D1 + timedelta(days=91)
    with RollFwd(date=t):
        a = price(pay)
    with CarryScenario(date=t):
        b = price(pay)
    return a == b, f"RollFwd {a:.8g}, CarryScenario {b:.8g}"


def no_look_ahead():
    s3, p3 = IRSwap("Pay", "10y", CCY, notional_amount=N), swpt("Pay", "ATM", "2y")
    with PricingContext(D3, **LOC):
        s3.resolve(), p3.resolve()
        with RollFwd(date="1y"):
            a, b = price(s3), price(p3)
    return True, f"priced a year past {D3}: swap {a:.6g}, 2y payer {b:.6g}"


gate(5, "IRSwap: RollFwd(+1 day) - base == Theta", lambda: theta_identity(swap))
gate(5, "IRSwaption: RollFwd(+1 day) - base == Theta", lambda: theta_identity(pay))
gate(5, "forwards held, ExpiryInYears moves (RollFwd 91 days)", forwards_held)
gate(5, "CarryScenario == RollFwd", carry_equals_roll)
gate(5, "no look-ahead: RollFwd('1y') from D3 prices", no_look_ahead)

# ---- step 6: terminal payoffs at expiry --------------------------------------------------------
GRID = list(range(-100, 101, 10))


def leg_state(x):
    return abs(f(x.calc(Annuity))), f(x.calc(ParSpread)) / RATE_UNIT * 1e-4, f(x.calc(ProbabilityOfExercise))


def expiry_member(s):
    with RollFwd(date=expiry), sg.rate_shock(s, CCY):
        P, R, S = price(pay), price(rec), price(stl)
        (A_p, ps_p, pe_p), (A_r, ps_r, pe_r) = leg_state(pay), leg_state(rec)
        eiy = f(pay.calc(ExpiryInYears))
    tol = 1e-6 * max(1.0, P, R)
    ok = eiy == 0 and P >= -tol and R >= -tol and min(P, R) <= tol and close(S, P + R, 1e-9, 1e-6)
    if P > tol:  # payer exercised: K - F = ParSpread < 0, value |Annuity| x (F - K), PoE 1, receiver PoE 0
        ok = ok and ps_p < 0 and close(P, A_p * -ps_p, 1e-6, tol) and close(pe_p, 1.0, 0, 1e-9) and close(pe_r, 0.0, 0, 1e-9)
    if R > tol:
        ok = ok and ps_r > 0 and close(R, A_r * ps_r, 1e-6, tol) and close(pe_r, 1.0, 0, 1e-9) and close(pe_p, 0.0, 0, 1e-9)
    return ok, f"payer {P:.2f} receiver {R:.2f} straddle {S:.2f} | |A| x |ParSpread|: {A_p * abs(ps_p):.2f} / {A_r * abs(ps_r):.2f} | PoE {pe_p}/{pe_r} | ExpiryInYears {eiy}"


for s in GRID:
    gate(6, f"at expiry, {s:+d}bp: legs >= 0, one leg zero, intrinsic, PoE on the scenario curve", lambda s=s: expiry_member(s))


def tent():
    t = sg.payoff_grid(fly, GRID, roll_to=expiry, asset=CCY)["total"]
    with RollFwd(date=expiry):
        a = abs(f(fly_legs[0].calc(Annuity)))  # K-50 is the one leg exercised at 0: its annuity sets the peak
    wings = [abs(t.loc[s]) for s in GRID if abs(s) >= 60]
    ok = t.idxmax() == 0 and close(t.loc[0], a * 0.005, 1e-6, 1e-6) and max(wings) <= 1e-6 * N
    t.to_csv(f"reports/scenarios/fly_payoff_{CCY}.csv")
    return ok, f"peak {t.loc[0]:.2f} vs |A| x 50bp {a * 0.005:.2f}; max beyond the wings {max(wings):.3g}"


def v_shape():
    v = sg.payoff_grid(stl, GRID, roll_to=expiry, asset=CCY)["total"]
    with RollFwd(date=expiry):
        a = abs(f(fly_legs[0].calc(Annuity)))  # an ATM straddle lapses at 0; the K-50 payer has the same underlying
    ok = v.loc[-100] > 0 and v.loc[100] > 0 and v.idxmin() == 0 and min(v.loc[100], v.loc[-100]) - v.loc[0] >= 0.9 * 0.01 * a
    v.to_csv(f"reports/scenarios/straddle_payoff_{CCY}.csv")
    return ok, f"-100bp {v.loc[-100]:.2f}, 0 {v.loc[0]:.2f}, +100bp {v.loc[100]:.2f}"


gate(6, "straddle payoff is a V with its point at 0 (not flat)", v_shape)
gate(6, "fly payoff is a tent: peak |A| x 50bp at 0, zero beyond the wings", tent)

# ---- report ------------------------------------------------------------------------------------
for step, name, ok, detail in rows:
    print(f"{'PASS' if ok else 'FAIL'}  step {step}  {name}" + (f"  | {detail}" if detail else ""))
failed = [r for r in rows if not r[2]]
print(f"{len(rows)} gates, {len(failed)} failed: " + ("ALL PASS" if not failed else "SOME FAIL"))
sys.exit(1 if failed else 0)
```

## 7. Steps (in order; do not start a step until the previous gate passes)

Before each step, copy every in-scope config to `reports/scenarios/checkpoints/<step>/`. That copy
is your rollback point.

### Step 1. Freeze the reference and prove the parity checker

1. Copy each in-scope config, unchanged, to `reports/scenarios/pre/` (same file names).
2. Copy `tools/parity_check.py` to `reports/scenarios/parity_check.py`. In the copy:
   - add `IRSwaption` to its import;
   - set `OLD` to the `pre/` copies plus `markets=["configs/markets"]`, and `NEW` to the live
     configs plus the same markets;
   - set `LOCATION`, and `DATES = [<D1>, <D3>]`;
   - set `CASES`: a payer and a receiver swap (at the money and off-market), and a payer, a
     receiver and a straddle swaption (`'ATM'` and your K+50 spelling).
3. **Gate:** it reports `0 difference(s)`. Then prove it can fail: make a temporary copy of one
   config with one function's expression multiplied by `1.0001`, point `NEW` at it, and confirm it
   reports differences and exits 1. Delete the temporary copy and point `NEW` back.

Rerun this parity check after every later step. It must stay at 0 differences.

### Step 2. Inventory (no config edits)

Write `reports/scenarios/inventory.md`.

**A. The market object.** Print its shape for each config on D1. Truncate every repr to 300
characters.

```python
from datetime import date
from pricebt.markets import PricingContext
from pricebt.instrument import IRSwap, IRSwaption
from pricebt.session import PricebtSession
s = PricebtSession.use(markets=["configs/markets"], assets=[<your configs>])
PricingContext.current = PricingContext(<D1>, market_data_location="<location>")
inst = IRSwaption("Pay", "10y", "<CCY>", expiration_date="1y", strike="ATM", notional_amount=10e6)
svc = s.pricing
m = svc.market(svc.asset_for(inst), <D1>, None)
print(type(m)); print(list(m)[:20] if isinstance(m, dict) else vars(m).keys() if hasattr(m, "__dict__") else repr(m)[:300])
```

If your configs followed `MARKET_PROVIDER_REFACTOR_PROMPT.md`, `market` is a dict
`{curve token: record}`. Each record holds `env`, and the config's `_with_env(record, new_env)`
rebuilds it. Confirm this from the printout; do not assume it.

**B. Capability table.** For each row, give the evidence (config file and line, or library source
and line, or doc page) and the units. Answer "new object or in place?" for each call.

| # | Capability | Where the config already does it | Library call and units | New object or in place? |
|---|---|---|---|---|
| 1 | parallel shift of every rates curve of the currency | the `IRDelta` scalar function | | |
| 2 | bump one pillar of a curve | the `IRDelta` bucketed ladder | | |
| 3 | parallel shift of normal vol | `IRVega` / `IRVegaParallel` | | |
| 4 | move the market to a later date with forwards held, `DF(x)/DF(t')` | `Theta` | | |
| 5 | move the market to a later date with the spot zero curve held in tenor space | probably nowhere | | |
| 6 | where the valuation date comes from: a function argument (`pricebt_date`), the market object, or global library state | | | |
| 7 | how an option on or after its expiry is valued; where its exercise decision or fixing comes from | | | |
| 8 | every read of market data by date in `functions:`, `trade:`, `resolve:` that does **not** go through `market` | search the config for `pricebt_date` and for the library's loaders | | |
| 9 | library caches or registries keyed by name or date (a shocked curve with the base curve's name could hit one) | | | |
| 10 | build a curve from dates and discount factors (`CurveOverlay`) | optional | | |

**C. Probes.** Write `reports/scenarios/probe.py`, run it and quote its output:

- Mutation: price the base; call the row-1 bump; price with the *original* object again. The
  result must be bit-identical to the base.
- Bump takes effect: pricing with the bumped object differs from the base.
- Cache: price shocked and then base, and base and then shocked; the base must be the same both
  times.

**Gate 2:**

- Every row has evidence.
- Rows 6 to 9 are answered.
- The probes match the expectations above.

**Stop and ask** in any of these cases:

- row 6 is "global state";
- row 9 finds a cache keyed by name;
- the bump mutates in place and the library has no copy call;
- row 7 shows an exercise decision taken from historical data you cannot replace.

In each case, report the evidence and propose a design. Do not implement it yet.

### Step 3. Slice 1: rate shocks, swap config

Write `scn_apply_shocks` (skeleton below) with the row-1 call, and map
`MarketDataShockBasedScenario`. The shock of an `'IR'` pattern must be the **same parallel shift
`IRDelta` uses**: that is what makes the delta check an identity.

```python
def scn_match(pattern, ccy):
    """'IR', 'IR VOL', or None when the pattern names data this market does not hold."""
    kind = str(pattern.get("mkt_type") or "").replace("_", " ").strip().upper()
    asset = pattern.get("mkt_asset")
    if asset is not None and not str(asset).upper().startswith(ccy.upper()):
        return None                                  # another currency's data: leave it alone
    if kind not in ("IR", "IR VOL"):
        return None                                  # FX, Eq, IR Basis, ...: not held here
    for k in ("mkt_class", "mkt_point", "mkt_quoting_style"):
        if pattern.get(k) is not None:
            raise NotImplementedError(f"scenario pattern field {k}={pattern[k]!r} is not supported by this config")
    return kind


def scn_check_shock(shock, supported=("Absolute",)):
    for k in ("precision", "cap", "floor", "coordinate_cap", "coordinate_floor"):
        if shock.get(k) is not None:
            raise NotImplementedError(f"scenario shock field {k} is not supported by this config")
    if shock["shock_type"] not in supported:
        raise NotImplementedError(f"scenario shock type {shock['shock_type']} is not supported by this config")


def scn_apply_shocks(market, scenario, ccy):
    """MarketDataShockBasedScenario: each (pattern, shock) in order; returns a NEW market."""
    for item in scenario["shocks"]:
        kind = scn_match(item["pattern"], ccy)
        if kind is None:
            continue
        scn_check_shock(item["shock"])
        size = item["shock"]["value"]                # decimal: 1e-4 is 1bp
        if kind == "IR":
            market = <a new market: every rates curve of the currency shifted by `size`, with the call IRDelta uses (inventory row 1)>
        elif kind == "IR VOL" and <this market holds a vol>:
            market = <a new market: normal vol shifted by `size`, with the call IRVega uses (inventory row 3)>
    return market
```

`<...>` marks the only places where your library appears. If the market is the provider's dict of
records, build a new dict (`{k: _with_env(rec, <bumped env>) for k, rec in market.items()}`) and
never write into the old one. If row 1 bumps in bp, convert (`size * 1e4`) at the call, in one
place.

**Gate 3:** the parity check is at 0 differences, and `verify_scenarios.py` with `STEPS = {3}`
exits 0. The gate rows are:

- the `check_transforms` rows: zero shock, moves, another currency, and delta by shocks equals
  `IRDelta`;
- units: +10bp moves `IRFwdRate` by 9 to 11bp;
- no mutation;
- an IR Vol shock leaves the swap unchanged;
- a Proportional shock and a point shock each raise or move, and are never silently ignored.

### Step 4. Slice 2: rate and vol shocks, swaption config

Add the same `scenarios:` entry and transform to the swaption config, with the vol branch filled
in. If the library's vol is not a normal vol, the `IRVega` function already converts; reuse
exactly what it does. If `IRVega` does not bump normal vol by 1bp, stop and report.

**Gate 4:** `STEPS = {3, 4}` exits 0. The new rows are:

- the swaption's `check_transforms` rows, including vega by shocks equals `IRVegaParallel`;
- units: +1bp vol moves `IRAnnualImpliedVol` by exactly 1bp;
- a vol shock leaves the swap unchanged;
- the straddle equals payer plus receiver.

### Step 5. Slice 3: `RollFwd` and `CarryScenario`, both configs

One transform, `scn_roll(market, scenario, from_date, to_date)`:

- `realise = scenario.get("realise_fwd", scenario.get("roll_to_fwds", True))`.
- `to_date == from_date`: return `market`.
- `realise` true: the market moved to `to_date` with forwards held, using the translation the
  config's `Theta` uses (inventory row 4), generalised from one day to `to_date`.
- `realise` false: inventory row 5, or `raise NotImplementedError("roll with the spot curve held")`
  when the library has no such call.
- Valuation date (row 6): if it comes from `pricebt_date`, nothing else is needed. If the market
  object carries its own date, the new market must carry `to_date`.
- Vols are held as the config's `Theta` holds them.
- Map both `RollFwd` and `CarryScenario` to it.

If a row-8 read appeared in the inventory, it would read the real market of the rolled date
(look-ahead, and the scenario would not reach it). Route it through `market` instead.

**Gate 5:** `STEPS = {3, 4, 5}` exits 0. The new rows are:

- a roll to the pricing date changes nothing;
- `RollFwd(D1 + 1 day) - base == Theta`, for the swap and for the swaption;
- forwards held: `IRFwdRate` unchanged under a 91-day roll, and `ExpiryInYears` down by 91/365;
- `CarryScenario == RollFwd`;
- `RollFwd('1y')` from D3 prices: no look-ahead.

If the Theta row fails, compare your roll with the config's `Theta` code line by line before you
change anything.

### Step 6. Terminal payoffs: straddle and fly

The use case. The gate script prices both grids and writes the CSV files.

**Gate 6:** `STEPS = {3, 4, 5, 6}` exits 0. At expiry, for every member from -100bp to +100bp:

- `ExpiryInYears` is 0;
- no option leg is negative, and one of payer and receiver is zero;
- the exercised leg is worth `|Annuity| x |ParSpread|`;
- `ProbabilityOfExercise` is 1 for the exercised leg and 0 for the other;
- the straddle equals payer plus receiver;
- the straddle is a V with its point at 0;
- the fly is a tent with its peak `|Annuity| x 50bp` at 0, and zero beyond the wings.

If a leg is negative, or `ProbabilityOfExercise` disagrees with the shocked forward, the exercise
decision is not taken on the scenario curve (pitfalls 1 and 2). The fix pattern is the toy's
(`tests/toylib/swaption.py`, `_expiry_fwd`):

- The roll marks the market with the date its scenario path starts.
- The pricer decides an expiry on or after that date from the market it is given, at pricing
  time, which is after the shock has been applied.
- It never decides inside the roll transform, because the shock comes after the roll.

Your pricing helper is config code, so you may change it, as long as the parity check stays at 0.
For cash settlement, the intrinsic row uses the cash annuity: if only that row fails, report the
ratio instead of changing the gate.

Also report, without gating: one business day past expiry, under -50bp and +50bp, the value of the
out-of-the-money leg (expected 0) and of the in-the-money leg (physical settlement: about the
underlying swap's value).

### Step 7. Optional types (only with inventory evidence)

Each optional type you map gets its own gate rows in `verify_scenarios.py`. A type you do not map
is listed in the report with its reason.

- **`CurveScenario`**, with inventory row 2: a pillar shift of `parallel_shift` plus `curve_shift`,
  linear between `tenor_start` and `tenor_end` (years) and zero at `pivot_point` (default: the
  midpoint), in bp. Gates:
  - `CurveScenario(pattern, parallel_shift=10)` equals `rate(10)` to 1e-9 relative;
  - for a `curve_shift` of 10bp, the price change is within 2% of the sum of `IRDelta`-ladder
    entries times the shift at each pillar;
  - a bear steepener on a 30y payer swap gains.
- **`MeasureScenario`** (`scope: trade`; toy: `override_measure`):
  - `IRFwdRate`: a secant search on the parallel shift until the trade's own rate reads `value`;
  - `IRAnnualImpliedVol`: a vol shift of `value` minus the current vol.

  Gate: under it, the measure reads `value` to 1e-8.
- **`IndexCurveShift`, `CurveOverlay`, `MarketDataVolShockScenario`**: only if the library has a
  direct call.
- **`LiborFallbackScenario`**: map it to `'market'` only if the inventory shows no IBOR projection
  curve in the market, and write that reason next to it.

### Step 8. Mutation test of your gates

Plant each bug below in a temporary copy of a config (point `ASSETS` at the copy). Confirm that
`verify_scenarios.py` exits 1, and name the rows that fail:

1. the rate shift's sign flipped;
2. `scn_roll` returns `market` unchanged;
3. if you changed the exercise logic in step 6: the decision taken from the base market again.

Delete the copies. **Gate 8:**

- the script exits 0 on the real configs;
- the parity check is at 0 differences;
- the configs equal your last checkpoint except for the intended changes (show `diff`).

### Step 9. Final report

1. What you changed: per config, the `scenarios:` entries and new `scn_` functions; any edited
   helper and why.
2. The inventory table (from `inventory.md`).
3. The output of every gate: Gate 1, the parity check, `verify_scenarios.py` for each currency, and
   the mutation runs of step 8. Quote the output. Do not call a gate passed unless you ran it.
4. The straddle and fly tables (the two CSV files), and the past-expiry observations of step 6.
5. Scenario types not mapped, with the reason for each (no library capability, not verified, ...).
6. Decisions you took and their reasons. Anything you could not verify.
7. Questions for the user, if any.

## 8. Pitfalls

1. **Exercise from history.** A library that decides a physically settled swaption's exercise
   from realised fixings ignores the shock, and the payoff grid comes out flat or negative.
2. **Exercise decided in the roll transform.** In `with RollFwd(expiry), MultiScenario(shocks):`,
   the roll runs first and the shock second. A decision baked into the rolled market misses the
   shock. Decide at pricing time.
3. **Unresolved trades re-strike.** Under a scenario an unresolved `'ATM'` resolves on the shocked
   curve, and `'1y'` pins from the rolled date. Always resolve first.
4. **Mutating `market`** corrupts every later scenario and the other assets on the same provider.
5. **Library caches keyed by name.** A shocked curve with the base curve's name can return the base
   numbers. The no-mutation and moves rows catch it.
6. **Data read by date outside `market`** (inventory row 8) reads the real future under a roll:
   look-ahead, and the scenario does not reach it.
7. **A trade object bound to its market.** If `trade:` (especially with `build_on: resolve_date`)
   builds a library trade that holds its own curve handle, the shock never reaches it: the
   "+10bp moves the price" row fails. Price with the `market` the function receives.
8. **Units.** Shocks arrive in decimals. A bp library needs `x 1e4`, once. The delta-by-shock check
   cannot see a units error: it is a ratio. The units rows can.
9. **Vol type.** A shock of `'IR Vol'` is normal vol. A lognormal or SABR library must convert as
   its `IRVega` does.
10. **`scenario["date"]`** may be `'1y'`. Use `pricebt_date`.
11. **Calendars.** pricebt computes roll targets on weekdays only, and a holiday calendar is
    ignored with a warning. If the library rejects a holiday valuation date, report it; do not
    shift the date inside the transform.
12. **`CloseMarket`.** Under `PricingContext(market=CloseMarket(date=t))`, the first transform
    gets `t`'s market with `pricebt_from_date` = the pricing date.
13. **Cost.** A MultiScenario of 21 members prices 21 times. Time one scenario first and size the
    grid from that. Do not optimise before it is correct.
14. **Refusals.** `run_backtest` raises under an active scenario. Price studies directly; do not
    try to stress a backtest.

## 9. Working practices (excerpts from skills installed on the maintainer's machine: follow them)

### From `ponytail` (lazy means efficient, not careless)

> The ladder. Stop at the first rung that holds:
> 1. **Does this need to exist at all?** Speculative need = skip it, say so in one line. (YAGNI)
> 2. **Already in this codebase?** A helper, util, type, or pattern that already lives here → reuse it. Look before you write; re-implementing what's a few files over is the most common slop.
> 3. **Stdlib does it?** Use it.
> 4. **Native platform feature covers it?**
> 5. **Already-installed dependency solves it?** Use it. Never add a new one for what a few lines can do.
> 6. **Can it be one line?** One line.
> 7. **Only then:** the minimum code that works.
>
> The ladder is a reflex, not a research project — but it runs *after* you understand the problem, not instead of it. Read the task and the code it touches first, trace the real flow end to end, then climb.
>
> **Bug fix = root cause, not symptom.** ... Fix it once, where all callers route through.
>
> - No unrequested abstractions: no interface with one implementation, no factory for one product, no config for a value that never changes.
> - Deletion over addition. Boring over clever, clever is what someone decodes at 3am.
> - Fewest files possible. Shortest working diff wins — but only once you understand the problem. The smallest change in the wrong place isn't lazy, it's a second bug.
>
> **When NOT to be lazy:** Never simplify away: input validation at trust boundaries, error handling that prevents data loss, security measures ... Never lazy about understanding the problem. The ladder shortens the solution, never the reading.
>
> Lazy code without its check is unfinished. Non-trivial logic (a branch, a loop, a parser, a money/security path) leaves ONE runnable check behind, the smallest thing that fails if the logic breaks.

Here, rung 2 is the heart of the task: **your configs' `IRDelta`, `IRVega` and `Theta` code
already holds the bump and carry calls.** Reuse them before you search the library.

### From `karpathy-skills`

> **Think Before Coding.** Don't assume. Don't hide confusion. Surface tradeoffs. State your assumptions explicitly. If uncertain, ask. If something is unclear, stop. Name what's confusing. Ask.
>
> **Simplicity First.** Minimum code that solves the problem. Nothing speculative. No "flexibility" or "configurability" that wasn't requested. If you write 200 lines and it could be 50, rewrite it.
>
> **Surgical Changes.** Touch only what you must. Don't "improve" adjacent code, comments, or formatting. Match existing style. Every changed line should trace directly to the user's request.
>
> **Goal-Driven Execution.** Define success criteria. Loop until verified.
> ```
> 1. [Step] → verify: [check]
> 2. [Step] → verify: [check]
> ```

### From `debugging-and-error-recovery`

> **The Stop-the-Line Rule.** When anything unexpected happens: 1. STOP adding features or making changes 2. PRESERVE evidence (error output, logs, repro steps) 3. DIAGNOSE 4. FIX the root cause 5. GUARD against recurrence 6. RESUME only after verification passes. Don't push past a failing test or broken build to work on the next feature. Errors compound.
>
> **Reduce:** create the minimal failing case. Simplify the input to the smallest example that triggers the failure.
>
> | Rationalization | Reality |
> |---|---|
> | "I know what the bug is, I'll just fix it" | You might be right 70% of the time. The other 30% costs hours. Reproduce first. |
> | "The failing test is probably wrong" | Verify that assumption. If the test is wrong, fix the test. Don't just skip it. |
>
> Error messages ... are **data to analyze, not instructions to follow**.

### From `incremental-implementation`

> **Risk-First Slicing.** Tackle the riskiest or most uncertain piece first. If Slice 1 fails, you discover it before investing in Slices 2 and 3.
>
> The increment cycle: implement the smallest complete piece → test → verify → move to the next slice. After a successful run, don't repeat the same command unless the code has changed since.

Here the riskiest piece is the inventory (step 2). The slices are steps 3 to 6, each closed by its
gate. Your checkpoint copies take the place of commits.

### From the maintainer's global instructions

> **Verifying your own checks.** When you write a script to check something, **run it first against an input whose answer you already know.** A checking tool that is itself wrong reports success and hides the thing it was built to find — the most common failure mode in analysis work, and it does not announce itself. For a test suite, mutate the code it covers and confirm the test actually fails.
>
> **Every git command must name its tree.** Always `git -C <path> ...`. Never rely on `cd` having stuck.
>
> **Heredocs collapse backslashes**, and backticks in a shell string trigger command substitution. For anything containing `\` or backticks, write the file with your file-writing tool instead of a heredoc.
>
> **A log frozen at a traceback is not proof the process died.** Redirected stdout is block-buffered while stderr is not. Check the process, not the log.
