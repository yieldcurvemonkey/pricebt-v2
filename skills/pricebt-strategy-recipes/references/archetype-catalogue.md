# Archetype catalogue

One entry per archetype of `skills/pricebt-strategy-recipes/scripts/recipes.py`: the intent, the spec
fields it reads, the exact pricebt constructs (the gs_quant.backtests API), the gs semantics you
must state in a report, and how to extend it. Every snippet assumes:

```python
from pricebt.backtests.actions import *          # AddTradeAction, AddScaledTradeAction, HedgeAction, ...
from pricebt.backtests.triggers import *         # PeriodicTrigger, DateTrigger, AggregateTrigger, ...
from pricebt.backtests.backtest_objects import ConstantTransactionModel, ScaledTransactionModel
from pricebt.backtests.data_sources import GenericDataSource, MissingDataStrategy
from pricebt.backtests.generic_engine import GenericEngine
from pricebt.backtests.strategy import Strategy
from pricebt.data import measure_series
from pricebt.instrument import IRSwap
from pricebt.risk import IRDelta, Price
from pricebt.session import PricebtSession

PricebtSession.use(assets=["tests/assets/toy_usd_irs.yaml"])
DV01 = IRDelta(aggregation_level="Type")         # scalar book dv01, currency per +1bp, payer > 0
payer = IRSwap(pay_or_receive="Pay", termination_date="10y", notional_currency="USD",
               notional_amount=10e6, fixed_rate="ATM", name="primary")
tc = ScaledTransactionModel(DV01, 0.25)          # costs.model dv01_bp, level 0.25
```

and every strategy runs with `GenericEngine().run_backtest(strategy, start=..., end=..., frequency="1b", risks=[...])`.
Every action is **named** explicitly: unnamed actions take a process-global `Action{N}` name, so
trade names would change from one build to the next.

## Semantics common to every archetype

- **Same close.** A trigger evaluated on date `d` reads data up to and including `d`, and its trade is
  resolved and struck at `d`'s close. There is no execution lag. Use `signal.lag: 1` to trade on the
  previous observation instead (a look-ahead check).
- **Holding window.** A trade created on `c` with final date `f` is held on grid dates `c <= s < f`; its
  entry cash is `-PV(c)` and its exit cash `+PV(f)`. Coupons while held are not booked separately (the
  PV carries them). `trade_duration=None` holds to the end of the backtest.
- **`'next schedule'`** means "exit at the next date of the trigger that fired": Periodic (and an
  Aggregate containing one) and DateTrigger provide it; after the last date the trade is held to the
  end. A lone `MktTrigger` or a `StrategyRiskTrigger` provides no info, and `'next schedule'` raises.
- **Missing markets are dropped**: with the default `missing_market='drop'`, a grid date with no market
  for a traded asset is removed (`backtest.missing_market_dates`); a trigger never fires on it, and an
  exit date that falls on it rolls to the next kept date (`backtest.missing_market_moves`).
- **Costs** (`transaction_cost`) apply per side: entry, and the exit (`transaction_cost_exit` defaults to
  the entry model).
- **`result_ccy`**: `run_backtest` rewrites its `risks` to `r(currency=result_ccy)`, but a trigger or
  hedge looks results up with the measure it was given, so give it `DV01(currency=result_ccy)`.

## Entry action and sizing (shared)

`recipes.entry_action(inst, name, trade_duration, sizing, transaction_cost)`:

```python
# sizing.method: notional  (sizing.notional overrides every instrument's notional_amount)
AddTradeAction(payer, "next schedule", name="Roll", transaction_cost=tc)

# sizing.method: dv01_target -- scale = scaling_level / unit dv01, so the level carries the leg's sign
AddScaledTradeAction(payer, "next schedule", name="Roll", scaling_type=ScalingActionType.risk_measure,
                     scaling_risk=DV01, scaling_level=+5000, transaction_cost=tc)   # a receiver needs -5000

# sizing.method: nav -- scale = cash / entry PV (premium instruments only; an ATM swap's PV is ~0)
AddScaledTradeAction(swaption, None, name="Buy", scaling_type=ScalingActionType.NAV, scaling_level=250_000)
```

The sizing measure is not added to the results automatically: put `DV01` in `run_backtest(risks=[...])`
(the recipes do) if you want to see the book dv01.

---

## periodic_roll (carry / roll-down harvest)

**Intent.** Hold a fresh instrument continuously and roll it on a schedule, harvesting carry and
roll-down (e.g. a monthly-rolled 10y payer or receiver).

**Spec fields.** `instruments.primary`, `rebalance.frequency`, `rebalance.trade_duration`, `sizing`, `costs`.

**Constructs.**

```python
trigger = PeriodicTrigger(PeriodicTriggerRequirements(frequency="1m", end_date=end),
                          AddTradeAction(payer, "next schedule", name="Roll", transaction_cost=tc))
strategy = Strategy(None, [trigger])
```

Trade names are `Roll_primary_<date>`, one per roll date; with `'next schedule'` each roll closes on the
next roll's entry date.

**Caveats.** Always pass `end_date` (`start_date=None` means the backtest start, DEV-T4). A duration
longer than the period (e.g. `'3m'` rolled monthly) stacks overlapping positions: that is a ladder, not a
roll. The roll-date grid is `RelativeDateSchedule(frequency, start, end)` with weekends skipped; extra
holidays come from `PeriodicTriggerRequirements.calendar`.

**Extend.** Several tenors: one `AddTradeAction` with a list of priceables, or one action per tenor.
A carry filter: wrap the periodic requirement in an `AggregateTrigger(ALL_OF)` with a
`MktTriggerRequirements` on a carry series (see momentum).

## mean_reversion (gs 040304)

**Intent.** Fade unusual moves: when the signal is more than `z_entry` rolling standard deviations
away from its rolling mean, trade against it; unwind when it crosses back through the mean.

**Spec fields.** `signal.type: par_rate_zscore`, `signal.instrument`, `signal.measure`, `signal.lookback`,
`signal.params.z_entry`, `signal.lag`, `sizing.method: notional` only.

**Constructs.**

```python
series = measure_series(IRSwap(termination_date="10y", notional_currency="USD", fixed_rate="ATM"),
                        "par_rate", start, end, frequency="1b")                     # a fresh, unresolved copy
ds = GenericDataSource(series, MissingDataStrategy.fill_forward)
trigger = MeanReversionTrigger(MeanReversionTriggerRequirements(ds, 2.0, 30, 30),
                               AddTradeAction(payer, None, name="MeanRev", transaction_cost=tc))
```

**Caveats (state them in the report).**

- The trigger is stateful: flat -> `|z| > z_entry` enters with scaling `-1` (signal above its mean:
  sell the primary) or `+1`; from a position, crossing back through the mean fires the **opposite
  scaling**. That exit is an **offsetting trade**: both trades stay open forever, the trade ledger shows
  every row `open`, and P&L must be read from `result_summary`.
- The rolling window is the `lookback` observations strictly **before** `d` (sample std, `ddof=1`);
  today's value is compared with it. A zero std gives `inf`, which fires.
- The ±1 travels in `AddTradeActionInfo.scaling`, which only `AddTradeAction` reads. `AddScaledTradeAction`
  would get no direction, so dv01/NAV sizing is rejected by `validate_spec`.
- `trade_duration` is ignored by the recipe (`None`): the trigger's own exit logic closes the exposure.

**Extend.** A different band on exit (e.g. exit at `|z| < 0.5`): write a `TriggerRequirements` subclass
with its own `has_triggered` returning `TriggerInfo(True, {AddTradeAction: AddTradeActionInfo(scaling=±1, next_schedule=None)})`,
copying `MeanReversionTriggerRequirements` in `src/pricebt/backtests/triggers.py` (give it a `reset()`).

## momentum (trend following)

**Intent.** On each rebalance date, hold the primary if the signal rose by more than `threshold_bp`
over `lookback` observations, the opposite position if it fell by more, and nothing otherwise.

**Spec fields.** `signal.type: rate_momentum`, `signal.instrument`, `signal.measure`, `signal.lookback`,
`signal.params.threshold_bp` (0.0 when unset, recorded in the notes), `signal.lag`, `rebalance.*`, `sizing`.

**Constructs.**

```python
change = series.diff(20).dropna()              # value on d uses observations <= d only
ds = GenericDataSource(change, MissingDataStrategy.fill_forward)
receiver = payer.clone(name="primary_opp", pay_or_receive="Receive")
up = AggregateTrigger(AggregateTriggerRequirements(
         [PeriodicTriggerRequirements(frequency="1m", end_date=end),          # Periodic FIRST
          MktTriggerRequirements(ds, +1.0, TriggerDirection.ABOVE)], AggType.ALL_OF),
     AddTradeAction(payer, "next schedule", name="MomUp", transaction_cost=tc))
down = AggregateTrigger(AggregateTriggerRequirements(
         [PeriodicTriggerRequirements(frequency="1m", end_date=end),
          MktTriggerRequirements(ds, -1.0, TriggerDirection.BELOW)], AggType.ALL_OF),
     AddTradeAction(receiver, "next schedule", name="MomDown", transaction_cost=tc))
strategy = Strategy(None, [up, down])
```

**Why this works with `'next schedule'`.** `AggregateTriggerRequirements.has_triggered` (ALL_OF)
merges its children's `info_dict`s; the Periodic child supplies `next_schedule` for `AddTradeAction`,
`AddScaledTradeAction` and `HedgeAction`, and the Mkt child supplies none, so the action receives the
next roll date. Verified by `tests/skills/test_skill_recipes.py` (every closed momentum trade closes on
a later roll date). No DateTrigger fallback is needed.

**Caveats.** "The opposite" flips `pay_or_receive` when the instrument has one, else negates `quantity_`.
Specify the primary as the position you want when the signal has **risen** (for a rate: a payer).
`MktTrigger` compares strictly (`>`/`<`). The first `lookback` observations have no signal, so no trade.

**Extend.** Volatility-scaled size: `AddScaledTradeAction(scaling_level={date: level})` takes a dated
dict of levels (interpolated forward). Several signals: add more `MktTriggerRequirements` children.

## curve_trade (dv01-neutral steepener / flattener)

**Intent.** Two opposite legs, each carrying the same |dv01|, so the book has no parallel exposure and
earns the change in the spread (2s10s steepener: receive 2y, pay 10y).

**Spec fields.** `instruments.primary` and `instruments.second` (opposite `pay_or_receive`),
`sizing.method: dv01_target`, `sizing.dv01_target`, `rebalance.*`.

**Constructs.**

```python
leg = lambda inst, level, name: AddScaledTradeAction(inst, "next schedule", name=name,
          scaling_type=ScalingActionType.risk_measure, scaling_risk=DV01, scaling_level=level, transaction_cost=tc)
front = IRSwap(pay_or_receive="Receive", termination_date="2y", notional_currency="USD", notional_amount=10e6, name="primary")
back = IRSwap(pay_or_receive="Pay", termination_date="10y", notional_currency="USD", notional_amount=10e6, name="second")
trigger = PeriodicTrigger(PeriodicTriggerRequirements(frequency="1m", end_date=end),
                          [leg(front, -5000, "Leg1"), leg(back, +5000, "Leg2")])
```

**Caveats.** The sign: `scale = scaling_level / unit_dv01`. The receiver's unit dv01 is negative, so it
needs a **negative** level to keep a positive quantity; with `+5000` it would silently become a payer
and the "curve trade" would be a doubled outright. The recipe signs the level by `pay_or_receive`, and the
test asserts each leg's `quantity_ > 0`, each leg's dv01 is `∓5000` and the book nets to 0 on entry
dates. Between rolls the legs' dv01 drift apart (the book is neutral only on entry dates). One
`AddScaledTradeAction` with both legs would scale them by a single factor against the *net* dv01 (≈ 0):
never do that.

**Extend.** A butterfly: three legs, levels in the ratio you want (e.g. `-0.5, +1, -0.5` × target).
A non-parallel neutrality (e.g. bucketed): not expressible in one gs action; size legs yourself with
`AddScaledTradeAction(scaling_type=size, scaling_level={date: q})` from precomputed ratios.

## delta_hedged

**Intent.** Hold a position and neutralise its dv01 with a hedge instrument every rebalance date, to
isolate what is left (curve, vol, carry).

**Spec fields.** `instruments.primary`, `instruments.hedge`, `rebalance.frequency`, `sizing`, `costs`.

**Constructs.**

```python
enter = DateTrigger(DateTriggerRequirements([start]), AddTradeAction(payer, None, name="Enter", transaction_cost=tc))
hedge = IRSwap(pay_or_receive="Receive", termination_date="5y", notional_currency="USD", notional_amount=10e6, name="hedge")
hedger = PeriodicTrigger(PeriodicTriggerRequirements(frequency="1b", end_date=end),
                         HedgeAction(DV01, hedge, None, name="Hedge", transaction_cost=tc))
strategy = Strategy(None, [enter, hedger])
```

**Caveats.**

- The entry uses a `DateTrigger([start])` instead of `Strategy(initial_portfolio=[...])` because an
  initial portfolio books no transaction cost and cannot be dv01-sized.
- `HedgeAction` sizes `h = -risk_percentage/100 × book_risk / hedge_unit_risk` against the book's risk
  **before** that day's hedges; every hedge placed on the same date is sized against that same pre-hedge
  risk, so two hedge actions on one date would each hedge the whole book.
- With `trade_duration=None` hedges accumulate: each date trades only the residual, and positions pile
  up (a year of daily hedges is hundreds of positions repriced daily). `'next schedule'` keeps one hedge
  but re-trades the whole hedge (and pays its costs) every period.
- `HedgeAction(csa_term=...)` resolves the hedge under that CSA only; later valuation uses the run's CSA.

**Extend.** Hedge a different risk: any scalar measure the asset config maps (e.g. a vega measure with a
swaption hedge). Partial hedges: `risk_percentage=50`. Hedge only above a threshold: see risk_band.

## risk_band

**Intent.** Let exposure build up (e.g. by periodic adds) but keep the book's |dv01| inside a band:
whenever it leaves `[-max_abs_dv01, +max_abs_dv01]`, hedge it back on the same date.

**Spec fields.** `instruments.primary`, `instruments.hedge`, `risk_limits.max_abs_dv01`,
optional `risk_limits.hedge_risk_percentage` (default 100; not in the template), `rebalance.*`.

**Constructs.**

```python
add = PeriodicTrigger(PeriodicTriggerRequirements(frequency="1m", end_date=end),
                      AddTradeAction(payer, None, name="Add", transaction_cost=tc))
hi = StrategyRiskTrigger(RiskTriggerRequirements(DV01, 12_000, TriggerDirection.ABOVE),
                         HedgeAction(DV01, hedge, None, name="BandHi", transaction_cost=tc, risk_percentage=100))
lo = StrategyRiskTrigger(RiskTriggerRequirements(DV01, -12_000, TriggerDirection.BELOW),
                         HedgeAction(DV01, hedge, None, name="BandLo", transaction_cost=tc, risk_percentage=100))
strategy = Strategy(None, [add, hi, lo])
```

**Caveats.** `StrategyRiskTrigger` is path-dependent: the engine prices the day's book (including that
day's scheduled adds) before evaluating it (DEV-E1), then sizes the hedge on the same date. With
`risk_percentage=100` the book returns to ≈ 0, not to the band edge; with `p < 100` the post-hedge book
can still exceed the band when `|risk| > max / (1 - p/100)`. The test asserts `|dv01| <= max` on every
date of `result_summary`.

**Extend.** Exit instead of hedging: replace the `HedgeAction` with `ExitAllPositionsAction()` or
`ExitTradeAction(priceable_names=[...])`. A position-count limit: `TradeCountTrigger` (see the cheatsheet).

## event

**Intent.** Trade around known dates (central-bank meetings, auctions, data releases).

**Spec fields.** `event_dates`, `instruments.primary`, `rebalance.trade_duration` (`'1w'`, `'next schedule'`
= until the next event, `None`), `sizing`.

**Constructs.**

```python
trigger = DateTrigger(DateTriggerRequirements([date(2024, 1, 31), date(2024, 3, 20)]),
                      AddTradeAction(payer, "1w", name="Event", transaction_cost=tc))
```

**Caveats.** The engine adds every trigger date inside `[start, end]` to the pricing grid (gs), even a
non-business day; a date with no market for the traded asset is then dropped and never fires. The trade is struck at the event date's close, i.e. *after* the event;
to be positioned before it, list the business day before. `EventTrigger` needs a `DataSource` whose
`get_data(None, eventName=..., ...)` returns a frame indexed by datetimes (a `GenericDataSource` does not
accept those keywords); for known dates a `DateTrigger` is simpler.

**Extend.** Different instruments per date: `AddScaledTradeAction(..., dated_priceables={date: [inst]})`.
Enter before and exit after: two `DateTrigger`s, or `trade_duration` as a tenor.

## stop_loss_mtm overlay

**Intent.** Cut all exposure when the book's mark-to-market falls below `-stop_loss_mtm`.

**Spec fields.** `risk_limits.stop_loss_mtm` (added to any archetype).

**Constructs.**

```python
stop = StrategyRiskTrigger(RiskTriggerRequirements(Price, -1_000_000, TriggerDirection.BELOW),
                           ExitAllPositionsAction(name="StopLoss"))
strategy = Strategy(None, [*entry_triggers, stop])
```

**Caveats (an approximation; say so).** It reads the aggregated `Price` of the positions open that day,
i.e. open-position PV, **not** realised or cumulative P&L: gs has no P&L trigger. An ATM entry has PV ≈ 0,
so for par swaps open-position PV is close to unrealised P&L, but realised P&L from closed trades is
ignored. Exits happen at that day's close (the book is flat that day). Later entry triggers still fire,
so the strategy can re-enter after a stop. Not supported with `result_ccy` (duplicate Price column).

## custom

`build` refuses `archetype: custom`. Compose the strategy directly:

1. Pick the closest entry above and copy its constructs.
2. Reuse the builders as parts: `recipes.periodic_roll(...)`, `recipes.stop_loss_overlay(...)`,
   `recipes.entry_action(...)`, `recipes.transaction_model(...)` all return plain pricebt objects.
3. Check each trigger and action against [construct-cheatsheet.md](construct-cheatsheet.md), especially
   its calc type (simple / semi-path-dependent / path-dependent) and what info it passes to actions.
4. Name every action; request every measure a trigger or hedge reads in `run_backtest(risks=[...])`.
5. Write a toy test in the style of `tests/skills/test_skill_recipes.py` that asserts the behaviour you
   intend, and the invariants (Total identity, costs negative, determinism).
