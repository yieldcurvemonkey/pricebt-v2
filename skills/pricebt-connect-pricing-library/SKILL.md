---
name: pricebt-connect-pricing-library
description: Fast path from "I have a bank pricing/data library (a Citi Datapoint/DP-style or JPM Athena-style platform, or an in-house one)" to a running pricebt backtest. Use it when you need to write a pricebt asset config against a new library, work out its units and signs, or connect a library for the first time.
---

# Connect a pricing library to pricebt

pricebt has no pricing and no market data of its own. Every number comes from one YAML **asset
config** per asset, whose Python strings call *your* library
([`docs/v2/ASSET_CONFIG_GUIDE.md`](../../docs/v2/ASSET_CONFIG_GUIDE.md)). This skill gets you from
the library to a checked config and a green smoke backtest. It follows a worked, tested example
against a **fictional** bank-style SDK, Meridian
([`example/`](example/README.md)). Meridian's conventions differ from pricebt's on purpose, so the
example shows every conversion you are likely to need.

## When to use / not use

- **Use** when you are connecting a library to pricebt for the first time, when adding a new
  instrument or curve from a library you already use, or when a config's numbers look wrong
  (units, signs, drift).
- **Do not use** to design a strategy (see `pricebt-strategy-intake`) or to audit a finished config
  (run `pricebt-verify-asset-config`, which step 6 below calls).

## Inputs and outputs

- **Inputs:** your library, importable in the same Python process. You also need one business date
  it has data for (the commands use 2024-01-02 and 2024-04-02; change them if your history differs).
- **Outputs:** a config under `configs/assets/`, a passing checker run, and a 3-month smoke backtest
  whose `Total == Price + Cumulative Cash + Transaction Costs` on every row.

## Procedure

### A. Fast path (about 30 minutes if your library resembles the example)

All commands run from the repository root in PowerShell. First set the environment. To dry-run the
fast path on the example itself, use the commented values.

```powershell
$cfg = "configs/assets/<asset>.yaml"     # your new config;  example: "skills/pricebt-connect-pricing-library/example/meridian_usd_irs.yaml"
$lib = "<dir containing your package>"   # "" if pip-installed; example: "skills/pricebt-connect-pricing-library/example"
$env:PYTHONPATH = "src;tests;$lib"; $env:CFG = $cfg
```

1. **See the example pass** (proves your environment):

```powershell
$env:PYTHONPATH = "src;tests;skills/pricebt-connect-pricing-library/example"
python -m pytest tests/skills/test_skill_connect_example.py -o addopts= -p no:cacheprovider -q
$env:PYTHONPATH = "src;tests;$lib"
```

2. **Copy the example config**. Or copy the blank, commented
   [`references/config-template.yaml`](references/config-template.yaml), which has a TODO at every
   decision.

```powershell
Copy-Item skills/pricebt-connect-pricing-library/example/meridian_usd_irs.yaml $cfg
```

   Change `asset:`, `description:`, `match:`, `currency:` and `market.key:`.

3. **Replace each Meridian call with your library's equivalent.** If you cannot fill a row, work
   through section B for that row.

   | In the config | Meridian call | What you need from your library |
   |---|---|---|
   | `imports:` | `import meridian_sdk as mdn` | the import (set any import-time env vars first) |
   | `_CLIENT` | `mdn.connect(env="SIM")` | one session per process |
   | `load_market` | `_CLIENT.market(iso, curve)` and `except (MarketClosed, NoData)` | the market by as-of date, and the exceptions for "no data" |
   | `resolve_swap` | `spot_date`, `maturity`, and `price(..., ["PAR_PCT"])` | spot date, tenor→date, and the par rate at the trade date |
   | `build_swap` | `_CLIENT.swap(ccy, start, end, fixed_rate_pct, notional, direction)` | the trade constructor, its rate unit and its side convention |
   | `_risk` | `_CLIENT.price([t], m, ["PV","DV01","PAR_PCT"])` | PV, scalar DV01 and par rate, in one call if possible |
   | `delta_ladder` | `_CLIENT.price(trades, m, ["BUCKET_DV01"])` | bucketed delta for many trades in one call |

   Convert every unit and sign **on the line that uses it**, with a `# vendor: X -> pricebt: Y`
   comment (see the table in section C).

4. **Load it.** This checks the schema and compiles every expression. It never runs your library:

```powershell
python -c "import os; from pricebt.assets.config import load_asset; print(load_asset(os.environ['CFG']).name)"
```

5. **Price one ATM payer.** Expect `|npv| < 1e-6 * notional`, `dv01 > 0` (about 900 per 1mm for a
   10y) and `par_rate` in bp (hundreds). Also expect `fixed_rate * 1e4 == par_rate` on the trade date.

```powershell
@'
import os
from datetime import date
from pricebt.instrument import IRSwap
from pricebt.session import PricebtSession
s = PricebtSession.use(assets=[os.environ["CFG"]])
d = date(2024, 1, 2)
r = s.pricing.resolve(IRSwap("Pay", "10y", "USD", 10_000_000, fixed_rate="ATM"), d, None)
print(r.resolved_terms)                    # absolute dates, decimal strike, signed notional
for f in ("npv", "dv01", "par_rate"):
    print(f, s.pricing.unit_value(r, d, f, None))
'@ | python -
```

6. **Run the checker.** This is the next step, owned by `pricebt-verify-asset-config`. It probes
   the config the way a backtest will (market on weekends, resolve pinning, units, signs, ladder sum,
   P&L explain, smoke backtest) and exits 1 on any FAIL:

```powershell
python skills/pricebt-verify-asset-config/scripts/check_asset.py $cfg --sys-path $lib --date 2024-01-02 --date 2024-04-02
```

   Fix every FAIL. Read every WARN: a par rate left in percent is only a WARN (see
   [`example/mistakes/`](example/mistakes/README.md)).

7. **Run a 3-month smoke backtest** (a monthly-rolled 10y payer):

```powershell
@'
import os
from datetime import date
from pricebt.backtests.actions import AddTradeAction
from pricebt.backtests.generic_engine import GenericEngine
from pricebt.backtests.strategy import Strategy
from pricebt.backtests.triggers import PeriodicTrigger, PeriodicTriggerRequirements
from pricebt.instrument import IRSwap
from pricebt.risk import Price
from pricebt.session import PricebtSession
PricebtSession.use(assets=[os.environ["CFG"]])
swap = IRSwap("Pay", "10y", "USD", 1e4, name="swap")
trig = PeriodicTrigger(PeriodicTriggerRequirements(frequency="1m", end_date=date(2024, 4, 2)), AddTradeAction(swap, "1m"))
bt = GenericEngine().run_backtest(Strategy(None, trig), start=date(2024, 1, 2), end=date(2024, 4, 2), frequency="1b", show_progress=False)
s = bt.result_summary
print(s.tail()); print(bt.trade_ledger()); print("dropped:", bt.missing_market_dates)
assert ((s["Total"] - (s[Price] + s["Cumulative Cash"] + s["Transaction Costs"])).abs() < 1e-6).all()
'@ | python -
```

   The dropped dates should be exactly your library's holidays and data gaps.

8. **Add a test** that runs your config (copy the shape of
   `tests/skills/test_skill_connect_example.py`). Mark it so it skips when the library is absent.

### B. Unknown library: discovery

When a row of the step-3 table has no obvious answer, go through
[`references/discovery-questionnaire.md`](references/discovery-questionnaire.md). It covers
session/auth, the market handle by date, trade construction, measure codes/units/signs, bucket
keys, holiday/no-data behaviour, batching, the EOD timestamp and timezone, and fixings. Answer
each question with evidence (a docstring, the library's own tests, or a REPL transcript), never
from memory.

### C. Convention conversions (full table: [`references/convention-conversions.md`](references/convention-conversions.md))

| Vendor convention | pricebt convention | Conversion |
|---|---|---|
| rate in percent / decimal | `par_rate` in **bp** | `* 100` / `* 1e4` |
| strike input in percent | resolved `fixed_rate` is **decimal** | `k * 100` in `build` only |
| `'ATM+25'` | offset in **bp** | `par + 25 / 1e4` in `resolve` |
| DV01 per 1bp **down** (receiver > 0) | `ccy_per_bp`, per **+1bp**, payer > 0 | `-x` |
| delta per 1% / per unit rate | per bp | `/ 100` / `* 1e-4` |
| risk per unit notional | total for one unit of the instrument | `* abs(notional)` |
| bucket key `"USD.SOFR:2Y"` | `"2Y"` | `key.split(":", 1)[1]`, and raise on unknown pillars |
| notional > 0 plus `PAY`/`RECEIVE` | signed notional, payer > 0 | map in `resolve`/`build` |
| ISO string / datetime dates | `datetime.date` | `date.fromisoformat` / `.date()` |
| raises on holiday / gap | `None` from `market.expr` | narrow `try/except` in `load_market` |
| tenor `end` resolved at pricing time | absolute date pinned at the trade date | library maturity helper in `resolve` |
| strike `None` = "at par" | decimal strike fixed at the trade date | price the par rate once in `resolve` |

### D. Service-style vs in-process libraries

- **Service-style** (a DP- or Athena-style platform behind a client: every call is a network round
  trip, and a session needs auth). Make one client at module level in `code:` (it runs once per
  process). Batch everything. Memoise per market. Keep credentials out of the YAML: read them from
  the environment or the platform's own credential store. Make the backtest deterministic: pin
  historical EOD snapshots, never "latest", and check the served as-of date equals the one you
  asked for. For CI and reruns, record responses once and replay them (see
  `pricebt-enterprise-integration`).
- **In-process** (a Python/C++ library such as a QuantLib- or rateslib-style curve builder). The
  market object is usually expensive, because it builds or calibrates a curve. pricebt already caches
  one market per (key, date, csa), so never build curves inside a function. Watch for trade objects
  that cache the fixings of the market they were built on: use `build_on: each_market` or a
  `remark()` helper (see the ARBS config
  [`configs/assets/usd_sofr_ois_interest_rate_swap.yaml`](../../configs/assets/usd_sofr_ois_interest_rate_swap.yaml)).

### E. Performance

- **One client per process**, created in `code:`, never per call.
- **Memoise per market.** Either `m.__dict__.setdefault("_pricebt_memo", {})` (the example's
  `_risk`), or a module dict keyed by `(date, trade)` when the market object has no `__dict__`.
  Request every per-trade measure in one call, so `npv`, `dv01` and `par_rate` on one date cost one
  round trip.
- **Batch portfolio calls.** `portfolio_functions:` receive all of this asset's trades on one date
  (`trades`, `weights`): send them in ONE library call. The example test asserts this with
  `Client.calls`.
- **`build_on: resolve_date`** when the trade object is market-independent. It is then built once.
- Measure it: the checker's `performance` check flags any evaluation slower than 1s.

## Checks (definition of done)

- [ ] `load_asset($cfg)` succeeds. Every conversion line has a `# vendor -> pricebt` comment.
- [ ] `load_market` returns `None` (it does not raise) on a weekend, a holiday and a data gap. It
      returns a market on a normal business day.
- [ ] `resolved_terms` hold only absolute `date`s, a **decimal** strike and a signed notional (no
      `"10y"`, no `"ATM"`).
- [ ] ATM payer on the trade date: `|npv| < 1e-6 * notional`, `dv01 > 0`, and `par_rate` equals the
      strike ×1e4 in bp. The receiver's dv01 is exactly the negative.
- [ ] Ladder keys are plain tenors and sum to `dv01` (state the tolerance). The ladder is one
      library call for the whole book.
- [ ] A seasoned trade keeps its resolved maturity when priced on a later date.
- [ ] `check_asset.py` has no FAIL, and every WARN is explained.
- [ ] 3-month smoke backtest: the Total identity holds on every row, the ledger has trades, and
      `missing_market_dates` equals the library's closed days.
- [ ] A test runs the config, and it skips cleanly when the library is not installed.

## Pitfalls

- **Unflipped dv01 sign**: a receiver-positive vendor DV01 used as-is. Hedges and risk-sized
  trades then go the wrong way. See [`example/mistakes/`](example/mistakes/README.md).
- **Rate left in percent** but declared `bp`: 100x too small. Thresholds never fire. The checker
  only WARNs on this.
- **Unpinned maturity or strike**: a tenor passed through to a library that resolves it at pricing
  time, or a "par" strike (`None`) that re-strikes on every market. The trade never ages and P&L is
  silently wrong.
- **Raising instead of returning `None`** on a closed day. The engine only understands `None`. Catch
  the library's specific exceptions, not bare `Exception`: a real auth or network failure must
  propagate.
- **Stale roll-back**: a library that quietly serves yesterday's curve for a holiday. Compare the
  served as-of date with the requested one.
- **Timezone and EOD**: an as-of at a London close for a NY strategy shifts every date by one
  session. Set `PricebtSession.use(..., tz=, eod_time=)` or use `pricebt_timestamp`.
- **Two configs matching the same instrument** in one session is an error. Give each a distinct
  `match:`, or pass `pricebt_asset=` on the instrument.
- **Helper names that shadow injected names** (`market`, `trade`, `kwargs`, `resolved`, `trades`,
  `weights`) break silently. Prefix your helpers.
- **Absolute paths or credentials in the YAML.** Configs are code, and they get committed.

## Related skills

- [`pricebt-verify-asset-config`](../pricebt-verify-asset-config/SKILL.md): the checker that step 6 runs.
- [`pricebt-asset-config-cookbook`](../pricebt-asset-config-cookbook/SKILL.md): recipes for other instruments and config patterns.
- [`pricebt-enterprise-integration`](../pricebt-enterprise-integration/SKILL.md): service-style platforms, record/replay, sessions.
- [`pricebt-architecture`](../pricebt-architecture/SKILL.md): the mental model and rules you must never break.
- [`pricebt-strategy-intake`](../pricebt-strategy-intake/SKILL.md): turn a strategy idea into a spec once pricing works.
