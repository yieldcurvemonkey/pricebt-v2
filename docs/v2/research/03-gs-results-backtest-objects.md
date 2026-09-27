# 03 — gs_quant results: `backtest_objects.py`, output views, risk measures, currency

## Key facts

1. `BackTest` (backtest_objects.py:83-370) is a passive state container. `GenericEngine` fills eight dicts (`portfolio_dict`, `results`, `cash_payments`, `cash_dict`, `transaction_cost_entries`, `transaction_costs`, `hedges`, `trade_exit_risk_results`); every output view is a pure pandas function of those dicts.
2. `result_summary`: index = `datetime.date` values (object dtype). Columns, in order: `[*risk measures as RiskMeasure objects*, 'Cumulative Cash', 'Transaction Costs', 'Total']`, where `Total = price_measure + 'Cumulative Cash' + 'Transaction Costs'` and `Transaction Costs` is cumulative and negative. Rows are `ffill()`ed and then `fillna(0)`ed, and truncated at `states[-1]`.
3. `trade_ledger()`: index = trade name (sorted). Columns, exactly: `Open, Close, Open Value, Close Value, Long Short, Status, Trade PnL`, all object dtype. `Trade PnL = Close Value + Open Value`, which equals PV_exit − PV_entry.
4. `strategy_as_time_series()`: row MultiIndex `('Pricing Date', 'Instrument Name')`. Column MultiIndex level 0 has three groups: `'Static Instrument Data'`, `'Risk Measures'` (with **string** risk names) and `'Cash Payments'` (containing `'Cash Ccy'` and `'Cash Amount'`).
5. Currency: `Price` comes back in the instrument's **local** currency (a EUR swap gives EUR). `run_backtest(result_ccy=X)` re-parameterises **every** risk with `currency=X`. Without it, a multi-currency cash book raises `RuntimeError('Cannot aggregate cash in multiple currencies')`. Risk measures are hashable dataclasses whose equality covers `name` and `parameters`, so `Price != Price(currency='USD')`.

Reference: the installed `gs_quant==1.5.4` at `C:/Users/chris/anaconda3/Lib/site-packages/gs_quant`. All `file:line` references point into that tree. Everything marked **VERIFIED** was reproduced offline, with real gs_quant classes and injected engine state, by the scratch script `sim_bt.py`. It made no network calls and created no GsSession. Section 9 shows its output verbatim.

---

## 0. Class inventory for `backtests/backtest_objects.py` (798 lines)

| Name | Lines | Kind | Exists in 1.5.4? | Notes |
|---|---|---|---|---|
| `BaseBacktest` | 48-49 | `ABC`, empty | yes | Marker base class |
| `TBaseBacktest` | 52 | `TypeVar(bound='BaseBacktest')` | yes | |
| `TransactionAggType` | 55-58 | `Enum` | yes | `SUM='sum'`, `MAX='max'`, `MIN='min'` |
| `PnlAttribute` | 61-71 | `@dataclass_json @dataclass` | yes | |
| `PnlDefinition` | 74-80 | `@dataclass_json @dataclass` | yes | |
| `BackTest` | 83-370 | `@dataclass_json @dataclass`, subclasses `BaseBacktest` | yes | The main result object |
| `ScalingPortfolio` | 373-383 | plain class | yes | Hedge scaling spec |
| `TransactionModel` | 386-390 | `@dataclass(frozen=True)` | yes | Abstract-ish base |
| `ConstantTransactionModel` | 393-400 | frozen dataclass | yes | |
| `ScaledTransactionModel` | 403-420 | frozen dataclass | yes | |
| `AggregateTransactionModel` | 423-439 | frozen dataclass | yes | |
| `TransactionCostEntry` | 442-556 | plain class | yes | Stateful cost wrapper used by the engine |
| `CashPayment` | 559-573 | plain class | yes | |
| `Hedge` | 576-582 | plain class | yes | |
| `PredefinedAssetBacktest` | 585-714 | dataclass | yes | Used only by `PredefinedAssetEngine`, not by GenericEngine |
| `CashAccrualModel` | 717-723 | dataclass | yes | |
| `ConstantCashAccrualModel` | 726-740 | dataclass | yes | |
| `DataCashAccrualModel` | 743-759 | dataclass | yes | |
| `OisFixingCashAccrualModel` | 765-798 | dataclass | yes | Calls the GS pricing API. Do not port |
| `WeightedTrade` | — | — | **NO** | Not present anywhere in gs_quant 1.5.4 (`grep -rn WeightedTrade` finds nothing) |
| `CashPaymentType` | — | — | **NO** | Not present in 1.5.4. `CashPayment.direction` is a plain int |
| `BackTest.weighted_trade` | — | — | **NO** | Not present |

> The implementer must not invent `WeightedTrade` or `CashPaymentType` "for parity". The reference API does not have them.

---

## 1. `BackTest` — exact definition

```python
@dataclass_json
@dataclass
class BackTest(BaseBacktest):
    CUMULATIVE_CASH_COLUMN: ClassVar[str] = "Cumulative Cash"
    TRANSACTION_COSTS_COLUMN: ClassVar[str] = "Transaction Costs"
    TOTAL_COLUMN: ClassVar[str] = "Total"

    strategy: object
    states: Iterable                       # sorted list of dt.date (all pricing dates incl. trigger dates)
    risks: Iterable[RiskMeasure]
    price_measure: RiskMeasure             # Price, or Price(currency=result_ccy)
    holiday_calendar: Iterable[dt.date] = None
    pnl_explain_def: Optional[PnlDefinition] = None
```
Signature (VERIFIED with `inspect.signature`):
`BackTest(strategy: object, states: Iterable, risks: Iterable[RiskMeasure], price_measure: RiskMeasure, holiday_calendar: Iterable[datetime.date] = None, pnl_explain_def: Optional[PnlDefinition] = None)`

The engine builds it at generic_engine.py:814: `BackTest(strategy, strategy_pricing_dates, risks, price_risk, holiday_calendar, pnl_explain)`.

### 1.1 `__post_init__` private state (lines 97-110)

| Attribute | Initial value | Keyed by | Value | Filled by (generic_engine.py) |
|---|---|---|---|---|
| `_portfolio_dict` | `defaultdict(Portfolio)` | `dt.date` | `Portfolio` of instruments **held** on that date | Action impls (153, 366, 504, 601, 1049), `_resolve_initial_portfolio` (907) |
| `_cash_dict` | `{}` | `dt.date` | `{ccy_code: cumulative cash float}` | `_handle_cash` (1135-1175) |
| `_hedges` | `defaultdict(list)` | create date | `list[Hedge]` | `HedgeActionImpl` (438) |
| `_cash_payments` | `defaultdict(list)` | effective date | `list[CashPayment]` | Action impls, `_resolve_initial_portfolio`, hedge scaling (1059-1061) |
| `_transaction_costs` | `defaultdict(int)` | date | float (≤ 0) per date, **not cumulative** | End of `__run` (867-870) |
| `_transaction_cost_entries` | `defaultdict(list)` | date | `list[TransactionCostEntry]` | Action impls |
| `strategy` | `deepcopy(self.strategy)` | | | |
| `_results` | `defaultdict(list)` | `dt.date` | `PortfolioRiskResult` (all held trades × all `risks`) | `add_results` from `_price_semi_det_triggers` (950), `__ensure_risk_results` (979), `_calc_new_trades` (1091) |
| `_trade_exit_risk_results` | `defaultdict(list)` | exit date | `PortfolioRiskResult` of trades exiting that day | `_handle_cash` (1131), only when `calc_risk_at_trade_exits=True` |
| `risks` | `make_list(self.risks)` | | | |
| `_risk_summary_dict` | `None` | | cache built by `get_risk_summary_df`, **computed once and never invalidated** | |
| `_calc_calls` | `0` | | counter | |
| `_calculations` | `0` | | counter | |

### 1.2 Public properties and methods

| Member | Kind | Signature / returns | Setter? | Semantics |
|---|---|---|---|---|
| `cash_dict` | property | `dict[date, dict[str, float]]` | no | Cumulative cash by currency, per date (§3.3) |
| `portfolio_dict` | property | `defaultdict(Portfolio)` | yes | Holdings per date |
| `cash_payments` | property | `defaultdict(list[CashPayment])` | yes | |
| `transaction_costs` | property | `dict[date, float]` | yes | Per-date (non-cumulative) cost, sign ≤ 0 |
| `transaction_cost_entries` | property | `defaultdict(list[TransactionCostEntry])` | no | |
| `hedges` | property | `defaultdict(list[Hedge])` | yes | |
| `results` | property | `defaultdict(list)` of `PortfolioRiskResult` | no | |
| `set_results(date, results)` | method | `-> None` | | `self._results[date] = results` (overwrites) |
| `add_results(date, results, replace=False)` | method | `-> None` | | `if date in _results and len(_results[date]) and not replace: _results[date] += results` (a `PortfolioRiskResult.__add__` merge), else overwrite |
| `trade_exit_risk_results` | property | `defaultdict(list)` | no | |
| `calc_calls` | property | int | yes | Number of pricing-context batches |
| `calculations` | property | int | yes | Approximate trades × risks count |
| `get_risk_summary_df(zero_on_empty_dates=False)` | method | `pd.DataFrame` | | §4.1 |
| `result_summary` | **property** | `pd.DataFrame` | | §4 |
| `risk_summary` | **property** | `pd.DataFrame` | | `get_risk_summary_df(zero_on_empty_dates=True)` (§5) |
| `trade_ledger()` | **method** | `pd.DataFrame` | | §6 |
| `strategy_as_time_series()` | **method** | `pd.DataFrame` | | §7 |
| `pnl_explain()` | **method** | `dict[str, dict[date, float]]` or `None` | | §8 |

Note the call style. `result_summary` and `risk_summary` are **properties** (no parentheses). `trade_ledger()`, `strategy_as_time_series()` and `pnl_explain()` are **methods**. pricebt must keep this exactly.

---

## 2. How the engine fills `BackTest` (the behaviour the outputs depend on)

### 2.1 Trade naming
- `AddTradeAction` pre-names each priceable `f'{action.name}_{priceable.name}'` (actions.py:152-159). If the priceable has no name it becomes `f'{action.name}_Priceable{i}'`. The default action name is `'Action{n}'` (actions.py:93).
- On each trade date the engine clones the priceable to `f'{t.name}_{d}'`, where `d` is a `dt.date` and so formats as `YYYY-MM-DD` (generic_engine.py:120). Example: `Action1_swap_2021-12-06`.
- Initial-portfolio trades are renamed `f'{old_name}_{start:%Y-%m-%d}'` (generic_engine.py:891-893).
- Hedge trades: `f'{hedge_trade.name}_{create_date:%Y-%m-%d}'`. After scaling, the portfolio and every instrument get the prefix `Scaled_` (generic_engine.py:1038-1041).
- `ExitTradeAction` with `priceable_names` parses names as `<Action>_<TradeName>_<YYYY-MM-DD>` (generic_engine.py:474-487). **The naming convention is load-bearing.**

### 2.2 Holding window (generic_engine.py:152-153)
```python
backtest_states = (s for s in backtest.states if final_date > s >= create_date)
for s in backtest_states: backtest.portfolio_dict[s].append(inst)
```
A trade is held on the **create date** and is **not held on its final (exit) date**. So the trade has no row in `results[final_date]`, and its exit value is priced by a separate "cash" calculation (§2.4).

`final_date = get_final_date(inst, create_date, trade_duration, holiday_calendar, info)` (backtest_utils.py:56-80):

| `trade_duration` | final_date |
|---|---|
| `None` | `dt.date.max` (the trade is never unwound, and its exit `CashPayment` sits at `date.max`) |
| `dt.date` / `dt.datetime` | that date |
| a string naming an instrument attribute (e.g. `'expiration_date'`, `'termination_date'`) | `getattr(inst, duration)` |
| `'next schedule'` (case-insensitive) | `trigger_info.next_schedule or dt.date.max` |
| `CustomDuration(durations, function)` | `function(*[get_final_date(d) for d in durations])` |
| a tenor string like `'1m'` | `RelativeDate(duration, create_date).apply_rule(holiday_calendar=...)` |

### 2.3 Cash payments created per trade (AddTradeActionImpl, generic_engine.py:136-150)
For every instrument on every create date, the engine appends:
- `CashPayment(inst, effective_date=create_date, direction=-1, transaction_cost_entry=tc_enter)` to `cash_payments[create_date]`
- `CashPayment(inst, effective_date=final_date, direction=+1 (default), transaction_cost_entry=tc_exit)` to `cash_payments[final_date]`
- `TransactionCostEntry(create_date, inst, action.transaction_cost)` to `transaction_cost_entries[create_date]`
- `TransactionCostEntry(final_date, inst, action.transaction_cost_exit)` to `transaction_cost_entries[final_date]`

`ExitTradeActionImpl` (generic_engine.py:454-550) runs on exit date `s`. It:
- removes the trade from `portfolio_dict[d]` for every `d >= s` and from `results[d]`, rebuilding `PortfolioRiskResult`.
- **moves** each future `CashPayment` (effective date `> s`) to `s` and sets its `effective_date = s`.
- nets directions when the trade already has a payment on `s`: `cash_payments[s][i].direction += cp.direction`. An entry (−1) plus an exit (+1) on the same day gives **direction 0**, which is the `trade_ledger` "opened and closed same day" row.
- moves the TCE to `s` as well.

### 2.4 `_handle_cash` (generic_engine.py:1093-1175): the exact algorithm

```text
# Step 1 – price every cash-payment trade that has no result on its effective date
for each cp in all cash_payments, for each leaf trade of cp.trade (Portfolio -> all_instruments):
    if cp.effective_date and cp.effective_date <= strategy_end_date:
        if cp.effective_date not in results or trade not in results[cp.effective_date]:
            cash_trades_by_date[cp.effective_date].append(trade)
            if calc_risk_at_trade_exits and cp.direction == 1: exited_cash_trades_by_date[...].append(trade)
cash_results[date] = Portfolio(trades).calc(price_risk)        # ONLY price_risk
trade_exit_risk_results[date] = Portfolio(expiring).calc(risks) # optional

# Step 2 – roll the cash account
current_value = None
for d in sorted(set(strategy_pricing_dates + cash_payments.keys())):
    if d > strategy_end_date: continue                            # payments after end are ignored
    if current_value is not None:
        cash_dict[d] = current_value[0] if cash_accrual is None else cash_accrual.get_accrued_value(current_value, d)
    if d in cash_payments:
        for cp in cash_payments[d]:
            for trade in leaves(cp.trade):
                value = cash_results[cp.effective_date][price_risk][trade.name]   # else
                value = results[cp.effective_date][price_risk][trade.name]
                if not isinstance(value, float): raise RuntimeError('failed to get cash value for ...')
                ccy = map_ccy_name_to_ccy(next(iter(value.unit)))  # first key of FloatWithInfo.unit dict
                if d not in cash_dict: cash_dict[d] = {ccy: initial_value}   # initial_value lands here, ONCE
                if ccy not in cash_dict[d]: cash_dict[d][ccy] = 0
                cp.cash_paid[ccy] += value * cp.direction        # entry: -PV ; exit: +PV
            for ccy, paid in cp.cash_paid.items(): cash_dict[d][ccy] += paid
        current_value = cash_dict[d], d
    current_value = deepcopy(current_value)
```
Consequences the port must reproduce:
- **`cash_dict` has no keys before the first cash-payment date.** In `result_summary` those early rows get `Cumulative Cash = 0` (from `fillna(0)`), **not `initial_value`**. `initial_value` appears only from the first trade date onward, and only in that trade's currency.
- Cash is **cumulative** and carried forward to every later pricing date (optionally accrued).
- Entry cash = `−PV(entry date)`. Exit cash = `+PV(exit date)`. The PV is `price_risk` and carries the instrument's sign (a negative scaling gives a negative PV).
- A cash payment dated after `strategy_end_date` (e.g. `date.max` for `trade_duration=None`) is never priced, and its `cash_paid` stays empty.
- `map_ccy_name_to_ccy` (backtest_utils.py:90-113) maps long names (`'United States Dollar'` to `'USD'`, `'Euro'` to `'EUR'`, …, 18 entries) and returns **`None` for anything not in the table**. Two unmapped currencies therefore silently collapse onto the key `None`. This is a gs quirk; pricebt should key cash by ISO code taken from the asset config.
- Transaction costs are **not** booked into `cash_dict`. They live only in `transaction_costs`.

### 2.5 Transaction costs (generic_engine.py:866-870)
```python
backtest.transaction_costs = {d: -sum(tce.get_final_cost() for tce in tce_list)
                              for d, tce_list in backtest.transaction_cost_entries.items()}
```
The values are negative (a cost). The dict can contain dates after the end (exits) and `date.max`. `result_summary` truncates those rows, so **exit costs of trades still open at the end are never charged.**

### 2.6 `run_backtest` risk list (generic_engine.py:795-812)
```python
risks = list(set(make_list(risks) + strategy.risks + pnl_risks + [self.price_measure]))   # de-duplicated, ORDER NOT STABLE (set)
if result_ccy is not None:
    risks = [r(currency=result_ccy) if isinstance(r, ParameterisedRiskMeasure) else raiser(f'Unparameterised risk: {r}') for r in risks]
    price_risk = self.price_measure(currency=result_ccy)  # raiser if not parameterised
else:
    price_risk = self.price_measure
```
- `price_measure` is **always** added to `risks`, so `Price` is always a column of `result_summary`.
- The order of risk columns is the order they are first met in the `PortfolioRiskResult.risk_measures` of the first date. It comes from a `set`, so it is effectively arbitrary. pricebt should use a deterministic order: user order, then the price measure if it is missing. Document that as a deliberate improvement.

---

## 3. Sign and value conventions (summary)

| Quantity | Formula | Sign |
|---|---|---|
| `CashPayment.cash_paid[ccy]` (entry) | `−1 × PV_entry` | opposite to PV |
| `CashPayment.cash_paid[ccy]` (exit) | `+1 × PV_exit` | same as PV |
| `cash_dict[d][ccy]` | `initial_value + Σ_{payments with date ≤ d} cash_paid` (plus accrual) | |
| `transaction_costs[d]` | `−Σ tce.get_final_cost()` over TCEs dated `d` | ≤ 0 |
| `Transaction Costs` column | `cumsum` of `transaction_costs` sorted by date | ≤ 0 |
| `Total` column | `Price + Cumulative Cash + Transaction Costs` | P&L-like (NAV) |

---

## 4. `result_summary` (lines 209-235), the most important output

### 4.1 `get_risk_summary_df(zero_on_empty_dates=False)` (lines 185-207)
```python
if self._risk_summary_dict is not None: summary_dict = self._risk_summary_dict   # cached forever
else:
    if not self._results: return pd.DataFrame(columns=self.risks)
    dates_with_results = [(d, r) for d, r in self._results.items() if len(r)]   # dates with ≥1 held trade
    summary_dict = defaultdict(dict)
    for date, results in dates_with_results:
        for risk in results.risk_measures:
            try:    value = results[risk].aggregate(True, True)   # allow_mismatch_risk_keys, allow_heterogeneous_types
            except TypeError: value = ErrorValue(None, error='Could not aggregate risk results')
            summary_dict[date][risk] = value
    self._risk_summary_dict = summary_dict
zero_risk_sd_copy = summary_dict.copy()           # SHALLOW copy
if zero_on_empty_dates:
    for cash_only_date in set(self._cash_dict) - set(zero_risk_sd_copy):
        for risk in self.risks: zero_risk_sd_copy[cash_only_date][risk] = 0
return pd.DataFrame(zero_risk_sd_copy).T.sort_index()
```
- The **cell value** is the portfolio-aggregated risk:
  - a scalar measure (`Price`, `IRDeltaParallel`) gives a `FloatWithInfo`, which is the sum over trades.
  - a bucketed measure (`IRDelta`) gives a **`DataFrameWithInfo` inside a single cell**, holding the per-bucket sums (`aggregate_risk`: concat, groupby all non-`value` columns, sum, `sort_risk`).
  - VERIFIED: `type(rs[IRDelta].iloc[0]).__name__ == 'DataFrameWithInfo'`.
- Rows exist only for dates on which at least one trade is **held**. Flat dates are absent.
- `.copy()` is shallow, but the zero-fill only touches keys that are **absent** from the cache. Those keys get fresh inner dicts in the copy, so calling `risk_summary` does **not** pollute the cached dict used by `result_summary` (VERIFIED: the cache keys are unchanged after `risk_summary`). The cache is never invalidated, though: mutating `results` after the first view call has no effect on later views.
- Only `TypeError` is caught. `aggregate_results` raises **`ValueError('Cannot aggregate results with different units for Price')`** when trades on one date carry different `unit`s, for example a EUR swap and a USD swap without `result_ccy`. That error propagates out of `result_summary` (VERIFIED).

### 4.2 Algorithm (lines 214-235)
```python
summary = self.get_risk_summary_df()
cash_summary = defaultdict(dict)
for date, results in self._cash_dict.items():
    for ccy, value in results.items():
        cash_summary[f'Cumulative Cash {ccy}'][date] = value
if len(cash_summary) > 1:  raise RuntimeError('Cannot aggregate cash in multiple currencies')
elif len(cash_summary) == 1:
    cash = pd.concat([pd.Series(d, name='Cumulative Cash') for _, d in cash_summary.items()], axis=1, sort=True)
else:
    cash = pd.DataFrame(columns=['Cumulative Cash'])
transaction_costs = pd.Series(self.transaction_costs, name='Transaction Costs').sort_index().cumsum()
df = pd.concat([summary, cash, transaction_costs], axis=1, sort=True).ffill().fillna(0)
df['Total'] = df[self.price_measure] + df['Cumulative Cash'] + df['Transaction Costs']
return df[: self.states[-1]]
```

### 4.3 Exact shape

| Aspect | Value |
|---|---|
| Index | `datetime.date` objects (dtype `object`), sorted. The index is the union of: dates with held trades, `cash_dict` dates (first cash date onward, every pricing date after that), and `transaction_costs` dates. Truncated to `<= states[-1]` by label slicing |
| Columns, in order | `*risk_columns`, `'Cumulative Cash'`, `'Transaction Costs'`, `'Total'` |
| Risk column labels | **the `RiskMeasure` objects themselves** (VERIFIED: `[Price, IRDelta, 'Cumulative Cash', 'Transaction Costs', 'Total']`, with types `RiskMeasureWithCurrencyParameter`, `RiskMeasureWithFiniteDifferenceParameter`, `str`, `str`, `str`). Users index with `summary[Price]`, or `summary[backtest.price_measure]` when `result_ccy` is set, because `Price(currency='USD') != Price` |
| Cash column label | always the literal `'Cumulative Cash'`. The currency suffix is built and then dropped |
| Risk cell types | `FloatWithInfo` for scalars. `DataFrameWithInfo` (the whole bucketed ladder) for bucketed measures. `0` (int) on filled dates. `ErrorValue` if aggregation raised `TypeError` |
| Fill | `ffill()` then `fillna(0)` over **all** columns |

### 4.4 Quirks (VERIFIED, pricebt must pick a policy for each; see §15)

| # | Quirk | Effect |
|---|---|---|
| Q1 | **Stale-PV ffill on flat dates.** On a trade's exit date the trade is not held (§2.2), so there is no risk row. `ffill()` copies the **previous day's PV** into `Price`, while `Cumulative Cash` already contains the exit proceeds. | `Total` double counts on any date with no holdings. VERIFIED in Case B: D3 shows `Price=1500` (stale), cash 2300, `Total=3790`; the economically correct value is 2290. `risk_summary` does zero-fill such dates, but `result_summary` does not |
| Q2 | Cash before the first trade is 0, not `initial_value` | NAV jumps by `initial_value` on the first trade date |
| Q3 | Multi-currency cash raises | `RuntimeError('Cannot aggregate cash in multiple currencies')` unless `result_ccy` puts everything in one currency (VERIFIED, Case C) |
| Q4 | No results at all | `pd.DataFrame(columns=risks)` with a RangeIndex, then `df[: date]` raises `TypeError('cannot do slice indexing on RangeIndex ...')` (VERIFIED, Case D) |
| Q5 | Exit TCs and cash after `states[-1]` | Dropped by the truncation |
| Q6 | Bucketed risk is ffilled too | A stale ladder on flat dates (same cause as Q1) |

---

## 5. `risk_summary` (lines 237-242)
`get_risk_summary_df(zero_on_empty_dates=True)` has the same columns as the risk part of `result_summary`, with no cash, TC or Total columns. Every date in `cash_dict` that lacks risk rows gets `0` for **every** risk in `self.risks`. It is not truncated and not ffilled. VERIFIED Case B: row `2024-01-04` shows `Price 0, IRDelta 0`.

---

## 6. `trade_ledger()` (lines 244-281)

```python
ledger = {}; names = []
for date in sorted(self.cash_payments.keys()):
    for cash in self.cash_payments[date]:
        if cash.direction == 0:                                   # netted same-day open+close
            ledger[cash.trade.name] = {'Open': date, 'Close': date, 'Open Value': 0, 'Close Value': 0,
                                       'Long Short': 0, 'Status': 'closed', 'Trade PnL': 0}
        elif cash.trade.name in names:                             # second sighting = close
            if len(cash.cash_paid) > 0:                            # only if it was actually priced
                row = ledger[cash.trade.name]
                row['Close'] = date
                row['Close Value'] += sum(cash.cash_paid.values())
                row['Trade PnL'] = row['Close Value'] + row['Open Value']
                row['Status'] = 'closed'
        else:                                                      # first sighting = open
            names.append(cash.trade.name)
            ledger[cash.trade.name] = {'Open': date, 'Close': None, 'Open Value': sum(cash.cash_paid.values()),
                                       'Close Value': 0, 'Long Short': cash.direction, 'Status': 'open', 'Trade PnL': None}
return pd.DataFrame(ledger).T.sort_index()
```

| Column | Type | Meaning |
|---|---|---|
| index | str | trade name (e.g. `Action1_swap_2024-01-02`), sorted lexicographically |
| `Open` | `dt.date` | date of the first cash payment seen for that name |
| `Close` | `dt.date` or `None` | date of the priced exit payment. `None` while open |
| `Open Value` | float | `Σ cash_paid` at entry, i.e. **−PV_entry** (summed across currencies with no conversion) |
| `Close Value` | float / int 0 | `Σ cash_paid` at exit, i.e. **+PV_exit**. `0` while open |
| `Long Short` | int | `cash.direction` of the **opening** payment. This is **always −1** for a normal entry and **0** for a same-day open/close. It does *not* encode long vs short; the sign lives in the trade's scaling and PV. Reproduce as-is |
| `Status` | str | `'open'` or `'closed'` |
| `Trade PnL` | float or `None` | `Close Value + Open Value` = PV_exit − PV_entry. `None` while open. Excludes transaction costs |

All columns have `object` dtype because of the `.T` (VERIFIED). A trade that is still open at the end (exit payment after the end, `cash_paid` empty) stays `'open'`. Hedge payments use the scaled **portfolio's** name (`Scaled_<hedge>_<date>`).

---

## 7. `strategy_as_time_series()` (lines 283-321)

Construction:
1. **Cash payments table.** For each `CashPayment`, `cp.to_frame()` returns a DataFrame with columns `['Cash Ccy', 'Cash Amount', 'Instrument Name', 'Pricing Date']`: one row per currency in `cash_paid`, and zero rows if it was not priced. All are concatenated, `.set_index(['Pricing Date', 'Instrument Name']).sort_index()`, and the columns become the MultiIndex `('Cash Payments', 'Cash Ccy')`, `('Cash Payments', 'Cash Amount')`.
2. **Risk table.** For each `(date, risk_res)` in `self.results`, `risk_res.to_frame(values='value', index='instrument_name', columns='risk_measure')` is a `pivot_table(aggfunc='sum')`. **Bucketed risks are summed over all buckets into one number per trade** (VERIFIED: IRDelta 50+800 gives 850). `.assign(pricing_date=date)` is added, the frames are concatenated, `reset_index`, renamed to `'Pricing Date'` and `'Instrument Name'`, and indexed on both. The columns become `('Risk Measures', str(risk))`. The label is the **string** repr, e.g. `'Price'`, `'IRDelta'`, `'Price(value:USD)'`, `'IRDelta(aggregation_level:Type, currency:USD)'`.
3. **Risk and cash join.** `risk_table.join(cp_table, how='outer')`.
4. **Static data.** `info.portfolio.to_frame()` for every date's result is concatenated. `Portfolio.to_frame` (markets/portfolio.py:421-457) gives index `(portfolio, instrument)` and columns `asset_class`, `type` (or `$type`) first, then the **sorted** instrument fields. The `'name'` column is renamed to `'Instrument Name'` and `set_index(['Instrument Name'])` replaces the old index. Duplicates are dropped with `keep='first'`, and the columns become `('Static Instrument Data', field)`.
5. `static.join(risk_and_cp, how='outer').sort_index()`. Pandas joins on the shared level `'Instrument Name'`, and the result has the 2-level row index.

Exact shape (VERIFIED):

| Aspect | Value |
|---|---|
| Row index | `MultiIndex` names `['Pricing Date', 'Instrument Name']` |
| Column index | 2-level `MultiIndex`. Level 0 is `'Static Instrument Data'`, then `'Risk Measures'`, then `'Cash Payments'` |
| Rows | one per (date, trade) for every date the trade is **held**, plus one per (date, trade) for every **priced** cash payment. The exit date appears through the cash row, with NaN risk |
| Cash on non-payment dates | NaN |
| Crash cases | If `cash_payments` is empty, `pd.concat([])` raises `ValueError('No objects to concatenate')`. If `results` is empty, the same happens |

---

## 8. `pnl_explain()`, `PnlAttribute` and `PnlDefinition` (lines 61-80, 323-370)

```python
PnlAttribute(attribute_name: str, attribute_metric: RiskMeasure, market_data_metric: RiskMeasure,
             scaling_factor: float, second_order: bool = False)      # get_risks() -> [attribute_metric, market_data_metric]
PnlDefinition(attributes: Iterable[PnlAttribute])                    # get_risks() -> flattened list
```
When passed as `run_backtest(pnl_explain=...)`, the engine forces `calc_risk_at_trade_exits=True` and adds `pnl_explain.get_risks()` to `risks`.

Algorithm: `dates = sorted(results.keys() ∪ trade_exit_risk_results.keys())`. For each attribute, `cum_total = 0`, then for `idx = 1..n-1`:
- If `prev` has no results, `result[cur] = cum_total` and continue.
- Otherwise, for each instrument `i` held on `prev`:
  - `r = results[prev][i][attribute_metric]`. If `r == 0`, skip.
  - `m0 = results[prev][i][market_data_metric]`.
  - `m1 = results[cur][i][market_data_metric]` if `i` is held on `cur`, else `trade_exit_risk_results[cur][i][market_data_metric]`.
  - First order: `pnl += scaling_factor * r * (m1 - m0)`.
  - Second order: `pnl += 0.5 * scaling_factor * r * (m1 - m0)**2`.
- `cum_total += pnl` and `result[cur] = cum_total`.

The method returns `{attribute_name: {date: cumulative_pnl}}` with no entry for the first date. It returns `None` if `pnl_explain_def is None`. Typical usage: `PnlAttribute('delta', IRDeltaParallel, IRFwdRate, 10000)`. This is a scaled product of a risk and a market-data move, so `market_data_metric` must be a *level* such as a par rate in percent. pricebt equivalent: the asset config exposes a level function (e.g. `par_rate`) and a sensitivity function (e.g. `dv01`).

---

## 9. Worked examples (VERIFIED output from real gs_quant classes)

Setup: USD pay-fixed swap `Action1_swap_2024-01-02`, 1mm notional. `risks=[Price, IRDelta]`, `price_measure=Price`. The IRDelta ladder has two buckets.

### Case A: 2 dates, 1 trade, held open (`trade_duration=None`, so the exit payment sits at `date.max`)
Engine state: `portfolio_dict = {D1:[swap], D2:[swap]}`. `results[D1]` has PV 0.0 and ladder {2y: 50, 10y: 800}. `results[D2]` has PV 1500.0 and ladder {2y: 49, 10y: 798}. `cash_payments = {D1:[entry(dir −1, cash_paid {'USD': -0.0})], date.max:[exit(dir +1, cash_paid {})]}`. `cash_dict = {D1:{'USD':0.0}, D2:{'USD':0.0}}`. `transaction_costs = {D1: -0.0, date.max: -0.0}`.

`result_summary`:
```
             Price                                            IRDelta  Cumulative Cash  Transaction Costs   Total
2024-01-02     0.0    mkt_type mkt_asset mkt_class mkt_point  valu...              0.0               -0.0     0.0
2024-01-03  1500.0    mkt_type mkt_asset mkt_class mkt_point  valu...              0.0               -0.0  1500.0
```
The `IRDelta` cell on 2024-01-02 is a `DataFrameWithInfo`:
```
  mkt_type mkt_asset mkt_class mkt_point  value
0       IR       USD       OIS        2y   50.0
1       IR       USD       OIS       10y  800.0
```
`trade_ledger()`:
```
                               Open Close Open Value Close Value Long Short Status Trade PnL
Action1_swap_2024-01-02  2024-01-02  None        0.0           0         -1   open      None
```
`strategy_as_time_series()` (transposed for display):
```
Pricing Date                                          2024-01-02              2024-01-03
Instrument Name                          Action1_swap_2024-01-02 Action1_swap_2024-01-02
Static Instrument Data asset_class                         Rates                   Rates
                       type                                 Swap                    Swap
                       fee                                   0.0                     0.0
                       fixed_rate                           0.04                    0.04
                       notional_amount                 1000000.0               1000000.0
                       notional_currency                     USD                     USD
                       pay_or_receive                        Pay                     Pay
                       termination_date                      10y                     10y
Risk Measures          Price                                 0.0                  1500.0
                       IRDelta                             850.0                   847.0
Cash Payments          Cash Ccy                              USD                     NaN
                       Cash Amount                           0.0                     NaN
```
In the real engine the instrument is resolved, so there are many more static columns: every non-None field of the resolved `IRSwap`.

### Case B: 3 dates, entered D1 (PV 0, TC 5), exited D3 via ExitTradeAction (exit PV 2300, TC 5)
`result_summary` shows **quirk Q1** on the last row:
```
             Price    IRDelta   Cumulative Cash  Transaction Costs   Total
2024-01-02     0.0    <df>                 0.0               -5.0    -5.0
2024-01-03  1500.0    <df>                 0.0               -5.0  1495.0
2024-01-04  1500.0    <df>              2300.0              -10.0  3790.0   <- Price is stale (ffill); correct Total is 2290
```
`risk_summary`: rows D1 and D2 as above, and D3 is `Price 0, IRDelta 0`.
`trade_ledger()`:
```
                               Open       Close Open Value Close Value Long Short  Status Trade PnL
Action1_swap_2024-01-02  2024-01-02  2024-01-04        0.0      2300.0         -1  closed    2300.0
```
`strategy_as_time_series()`: the D3 row has NaN `Risk Measures` and `Cash Payments = ('USD', 2300.0)`.

### Case E: same-day open and close (direction netted to 0)
```
                               Open       Close Open Value Close Value Long Short  Status Trade PnL
Action1_swap_2024-01-02  2024-01-02  2024-01-02          0           0          0  closed         0
```

---

## 10. Transaction models (lines 386-556)

| Class | Fields (defaults) | `class_type` (static, serialised) | `get_unit_cost(state, info, instrument)` |
|---|---|---|---|
| `TransactionModel` | — | — | returns `None` |
| `ConstantTransactionModel` | `cost: float\|int = 0` | `'constant_transaction_model'` | `self.cost` |
| `ScaledTransactionModel` | `scaling_type: str\|RiskMeasure = 'notional_amount'`, `scaling_level: float\|int = 0.0001` | `'scaled_transaction_model'` | If `scaling_type` is a `str`, returns `getattr(instrument, scaling_type)` and raises `RuntimeError(f'{scaling_type} not recognised for instrument {instrument.type}')` on a missing attribute. If it is a `RiskMeasure`, returns `np.nan` when `state > today`, else `instrument.calc(scaling_type)` under `PricingContext(state)` (a future) |
| `AggregateTransactionModel` | `transaction_models: tuple = ()`, `aggregate_type: TransactionAggType = SUM` | none | `sum`/`max`/`min` of the children's unit costs. An empty tuple gives 0 |

All models are `frozen=True`, which makes them hashable; they are used as dict keys in `TransactionCostEntry`. VERIFIED `to_dict()`: `{'cost': 5, 'class_type': 'constant_transaction_model'}` and `{'scaling_type': 'notional_amount', 'scaling_level': 0.0001, 'class_type': 'scaled_transaction_model'}`.

Default on actions: `transaction_cost = ConstantTransactionModel(0)` (actions.py:50-51). `transaction_cost_exit=None` means "same as entry".

### `TransactionCostEntry(date, instrument, transaction_model)` (442-556)

| Member | Behaviour |
|---|---|
| `all_instruments` | `instrument.all_instruments` if it is a Portfolio, else `(instrument,)` |
| `all_transaction_models` | the children if the model is `AggregateTransactionModel`, else `(model,)` |
| `cost_aggregation_func` | `sum`/`max`/`min` from the aggregate type. Defaults to `sum` |
| `additional_scaling` (get/set, default 1) | set by hedge scaling and by `AddScaledTradeAction` |
| `date` (get/set) | moved by ExitTradeAction |
| `no_of_risk_calcs` | count of `ScaledTransactionModel`s with a `RiskMeasure` scaling type |
| `calculate_unit_cost()` | fills `_unit_cost_by_model_by_inst[m][i] = m.get_unit_cost(date, None, i)` |
| `get_final_cost()` | for each model `m`: `cost = Σ_i resolved(unit[m][i])` (**nets across a portfolio's instruments**). If `m` is Scaled: `cost = m.scaling_level * abs(cost * additional_scaling)`. Constant costs are **not** multiplied by `additional_scaling`. Returns `agg(final_costs)`, or `0` if there are none |
| `get_cost_by_component()` | `(fixed, scaled)` split. With SUM it returns both. With MIN/MAX it returns only the winning component and 0 for the other |

Worked TC (from gs test `test_scaled_transaction_cost`): 5 daily GBP swaps, 50k notional each, `ScaledTransactionModel('notional_amount', 0.0001)`, 1m duration. The entry cost is `0.0001*50000 = 5` per trade, so the final `Transaction Costs` is `-25`. The exits fall after the end and are not charged.

---

## 11. `CashPayment`, `Hedge`, `ScalingPortfolio`

```python
class CashPayment:
    def __init__(self, trade, effective_date=None, direction=1, transaction_cost_entry: Optional[TransactionCostEntry] = None)
    # attrs: trade, effective_date, direction (int: -1 entry, +1 exit, 0 netted), cash_paid = defaultdict(float) {ccy: amount}, transaction_cost_entry
    def to_frame(self) -> pd.DataFrame  # columns ['Cash Ccy','Cash Amount','Instrument Name','Pricing Date']; one row per ccy

class Hedge:
    def __init__(self, scaling_portfolio: ScalingPortfolio, entry_payment: CashPayment, exit_payment: Optional[CashPayment])

class ScalingPortfolio:
    def __init__(self, trade, dates, risk, csa_term=None, risk_transformation: Transformer = None, risk_percentage: float = 100)
    # attrs: trade (must be a Portfolio when the hedge is applied), dates (active states), risk, csa_term, risk_transformation, risk_percentage, results=None
```
Hedge scaling on date `d` (generic_engine.py:1010-1061):
```text
current_risk = results[d][p.risk].transform(p.risk_transformation).aggregate(allow_mismatch_risk_keys=True)
hedge_risk   = p.results[d][p.risk].transform(p.risk_transformation).aggregate()
if hedge_risk == 0: skip
if current_risk.unit != hedge_risk.unit: raise RuntimeError('cannot hedge in a different currency')
scaling_factor = current_risk / hedge_risk * risk_percentage / 100
hedge position = deepcopy(p.trade) renamed 'Scaled_*', .scale(-scaling_factor), added to portfolio_dict for all p.dates
entry/exit CashPayment.trade = scaled portfolio; the TCEs' additional_scaling = scaling_factor
```
`exit_payment` is `None` if `final_date > dt.date.today()` (generic_engine.py:428-432). `Transformer` / `ResultWithInfoAggregator(risk_col='value', filter_coord=None)` (risk/transform.py) collapses a bucketed result to a `FloatWithInfo` sum, optionally filtered by a market-data coordinate.

---

## 12. Cash accrual models (lines 717-798)
All take `get_accrued_value(current_value, to_state)`, where `current_value = (cash_by_ccy: dict, from_state: date)`, and return a new `{ccy: value}`.

| Class | Fields | Formula per ccy |
|---|---|---|
| `CashAccrualModel` | `class_type='cash_accrual_model'` | abstract (returns None) |
| `ConstantCashAccrualModel` | `rate: float = 0`, `annual: bool = True` | `v * (1 + rate/(365 if annual else 1)) ** days` |
| `DataCashAccrualModel` | `data_source: DataSource = None`, `annual: bool = True` | same, with `rate = data_source.get_data(from_state)` |
| `OisFixingCashAccrualModel` | `start_date='-1y'`, `end_date=dt.date.today()` (evaluated at **import**) | Prices an `IRSwap` `Cashflows` on the GS API to get OIS fixings, then delegates. **Do not port**; it is GS-infrastructure specific. pricebt equivalent: a `DataCashAccrualModel` fed from an asset or market expression |

Note: `days` is calendar days, and `(1 + r/365)^days` is daily compounding on ACT/365.

## 13. `PredefinedAssetBacktest` (lines 585-714) (PredefinedAssetEngine only, for completeness)
`PredefinedAssetBacktest(data_handler: DataHandler, initial_value: float)`. It tracks `performance: pd.Series`, `cash_asset = Cash('USD')`, `holdings: defaultdict(float)`, `historical_holdings`, `historical_weights`, `orders: list` and `results: dict`. Its `trade_ledger()` returns **different** columns: `['Instrument', 'Open', 'Close', 'Open Value', 'Close Value', 'Status', 'Trade PnL']`, built from FIFO long/short order matching with `Trade PnL = (close_px - open_px) * sign(open qty)`. It also has `mark_to_market(state, valuation_method)`, `get_level(date)`, `get_costs() -> pd.Series` and `get_orders_for_date(date) -> DataFrame`. This is out of scope for pricebt v2, which ports GenericEngine, but do not confuse the two ledgers.

---

## 14. gs_quant risk measures used by backtests

### 14.1 Identity
- `RiskMeasure` (gs_quant/common.py:61 over target/common.py:6882) is `@dataclass(unsafe_hash=True, repr=False)` with fields `asset_class, measure_type, unit, parameters, value, name`. **All fields take part in `==` and `hash`** (VERIFIED `dataclasses.fields(Price)` all have `compare=True`). So:
  - `Price(currency='USD') == Price(currency='USD')` is True, and their hashes are equal.
  - `Price(currency='USD') != Price`.
  - `IRDelta(aggregation_level='Asset') != IRDeltaParallel`: same parameters, but `name` differs.
  - `Price(currency='USD', name='X') != Price(currency='USD')`.
- `repr`/`str` is `name` for an unparameterised measure. A `ParameterisedRiskMeasure` renders as `name(k1:v1, k2:v2)` with keys sorted case-insensitively and `parameter_type` dropped (common.py:102-113). VERIFIED examples: `Price(value:USD)`, `IRDelta(aggregation_level:Type, currency:USD)`, `IRDeltaParallel(aggregation_level:Asset)`, `IRDeltaLocalCcy(currency:local)`.
- `__lt__` orders by name, then parameters (common.py:62-74), so measures are sortable.
- Calling a parameterised measure returns a **clone** with the parameters merged: `IRDelta(currency='USD')`. The `pd.Series`/`DataFrame` guard in `__call__` exists so pandas `.loc[measure]` does not treat it as a callable.

### 14.2 Measures relevant to IR backtests

| Measure | Class | Parameters | Definition | Typical result type |
|---|---|---|---|---|
| `Price` | `RiskMeasureWithCurrencyParameter` | `currency` → `CurrencyParameter(value=...)` | `name="Price", measure_type="PV"`. **Local-ccy** PV by default | `FloatWithInfo` |
| `DollarPrice` | `RiskMeasure` | none | "Dollar Price", the USD PV | `FloatWithInfo` |
| `IRDelta` | `RiskMeasureWithFiniteDifferenceParameter` | `aggregation_level, bump_size, currency, finite_difference_method, local_curve, mkt_marking_options, scale_factor` → `FiniteDifferenceParameter` | Docstring: "Change in Dollar Price (**USD** present value) due to individual 1bp moves in the interest rate instruments used to build the underlying discount curve" | `DataFrameWithInfo` (bucketed) |
| `IRDeltaParallel` | same class | preset `aggregation_level=Asset`, `name='IRDeltaParallel'` (risk/measures.py:82) | parallel (summed) delta | `FloatWithInfo` (risk_by_class_handler collapses it when there are ≤2 classes of one type) |
| `IRDeltaLocalCcy` | same class | preset `currency='local'` (measures.py:83) | bucketed delta in local ccy | `DataFrameWithInfo` |
| `IRGamma` | **plain `RiskMeasure`** (not parameterised) | none | | `DataFrameWithInfo` / Float |
| `IRGammaParallel` | plain `RiskMeasure` | none | "Change in aggregated IRDelta for an aggregated 1bp shift" | `FloatWithInfo` (single-row second-order handler) |
| `IRVega` / `IRVegaParallel` / `IRVegaLocalCcy` | FiniteDifference | as IRDelta. Parallel means `aggregation_level=Asset`; LocalCcy means `currency='local'` | per 1bp of normal vol, in USD by default | DataFrame / Float |
| `IRFwdRate`, `IRSpotRate` | plain | `unit=Percent` | par rate / spot rate **in percent** | `FloatWithInfo` |
| `IRAnnualImpliedVol` (%), `IRDailyImpliedVol` (bps) | plain | | | Float |
| `Cashflows` | plain | none | cashflow table | `DataFrameWithInfo` with columns `currency, payment_date, set_date, accrual_start_date, accrual_end_date, payment_amount, notional, payment_type ('Fix'/'Flt'), floating_rate_option, floating_rate_designated_maturity, day_count_fraction, spread, rate, discount_factor` (result_handlers.py:83-102) |
| `Annuity` | CurrencyParameter | | | Float |
| `ResolvedInstrumentValues` | plain | | used by actions to resolve trades | instrument |

`AggregationLevel` enum (target/common.py:45-52): `Type`, `Asset`, `Class`, `Point`. For IR this collapses the ladder by `mkt_type`, then `mkt_asset`, then `mkt_class`, then per point (full ladder).

**Important:** `run_backtest(result_ccy=...)` raises `RuntimeError('Unparameterised risk: IRGamma')` if an unparameterised measure such as `IRGamma`, `IRGammaParallel` or `Cashflows` is in `risks` (generic_engine.py:796-803). `result_ccy` also **overrides** the `'local'` in `IRDeltaLocalCcy`: `IRDeltaLocalCcy(currency='USD')` is VERIFIED to render as `IRDeltaLocalCcy(currency:USD)`.

### 14.3 Result objects

| Class | Base | Key attrs | Notes |
|---|---|---|---|
| `ResultInfo` | ABC | `risk_key: RiskKey`, `unit: dict`, `error`, `request_id` | |
| `FloatWithInfo` | `float` + ResultInfo | `.raw_value -> float` | `+` requires equal `unit` (otherwise `ValueError('FloatWithInfo unit mismatch')`). `repr` appends the unit, e.g. `1500.0 (USD)`, built from the unit dict `{name: power}` |
| `SeriesWithInfo` | `pd.Series` | | historical scalars |
| `DataFrameWithInfo` | `pd.DataFrame` | `.raw_value -> pd.DataFrame` | bucketed risk, cashflows |
| `ErrorValue` | ResultInfo | `.error` | `raw_value = None` |
| `RiskKey` | namedtuple | `(provider, date, market, params, scenario, risk_measure)` | |
| `PortfolioRiskResult` | future | `.portfolio`, `.risk_measures`, `[risk]`, `[trade_or_name]`, `.aggregate()`, `.to_frame()`, `.transform()` | |

`unit` is a dict `{unit_name: power}` supplied by the server. The backtester uses only `next(iter(value.unit))` as the currency.

Bucketed IRDelta frame (`risk_vector_handler`, result_handlers.py:206-225). The columns are those present in the source rows, drawn in order from: `mkt_type, mkt_asset, mkt_class, mkt_point, mkt_quoting_style, value`. Rows are sorted by `sort_values` over all columns, and `mkt_point` sorts by tenor through `point_sort_order`. For a USD swap the rows are typically `mkt_type='IR'`, `mkt_asset='USD'`, `mkt_class` in {`'Swap'`, `'OIS'`, `'Cash'`, …}, `mkt_point` like `'10Y'`, and `value` = USD per 1bp. These are illustrative values, not verifiable offline. Aggregating across trades (`aggregate_risk`) runs `concat`, `fillna(0)`, `groupby(all columns except 'value', as_index=False).sum()`, then `sort_risk` by `('date','time','mkt_type','mkt_asset','mkt_class','mkt_point')`.

`aggregation_level=Type/Asset` goes through `risk_by_class_handler`. It returns `FloatWithInfo(sum)` **only** if the measure's name is one of `IRBasisParallel, IRDeltaParallel, IRVegaParallel, PnlExplain` *and* there are ≤2 classes of a single type. Otherwise it returns a `DataFrameWithInfo` with columns `mkt_type, mkt_asset, value`. So `IRDelta(aggregation_level='Type')` (named `IRDelta`) comes back as a small DataFrame, not a float.

---

## 15. Currency semantics

| Question | Answer (source) |
|---|---|
| Currency of `Price` for a EUR swap with no `result_ccy` | **EUR (local)**. Notebook 040303 says: "The results will be in local ccy, EUR in this case. To change them to USD, specify result_ccy in the backtest args" |
| Currency of `IRDelta` / `IRVega` with no currency parameter | **USD** (docstrings, "Change in Dollar Price (USD present value)…"). `IRDeltaLocalCcy` / `IRVegaLocalCcy` give local currency |
| What `result_ccy='USD'` does | Every risk `r` becomes `r(currency='USD')`, `price_measure` becomes `Price(currency='USD')`, and cash is booked from the USD PV, so all cash lands in one currency |
| Mixed-currency book without `result_ccy` | `result_summary` fails. If the two currencies are held on the same date, `get_risk_summary_df` raises `ValueError('Cannot aggregate results with different units for Price')` (VERIFIED; `aggregate_results` checks `unit` even with `allow_mismatch_risk_keys=True`). If they are never held together, `cash_dict[d]` still ends up with two keys and the cash step raises `RuntimeError('Cannot aggregate cash in multiple currencies')`. **In gs, multi-currency effectively requires `result_ccy`** |
| Column label after `result_ccy` | `Price(value:USD)`, so user code must use `backtest.price_measure` rather than `Price` |
| `trade_ledger` with mixed currencies | `Open Value = sum(cash_paid.values())` sums across currencies **without conversion** |
| Hedging across currencies | `current_risk.unit != hedge_risk.unit` raises `RuntimeError('cannot hedge in a different currency')` |

---

## 16. Implications for pricebt v2 (for the design author)

The public shape to reproduce 1:1:

1. `BackTest` keeps the same public members (§1.2) and the same property-versus-method split. `result_summary` keeps its column order and the literal strings `'Cumulative Cash'`, `'Transaction Costs'` and `'Total'` (also exposed as `CUMULATIVE_CASH_COLUMN`, `TRANSACTION_COSTS_COLUMN` and `TOTAL_COLUMN` ClassVars). Risk columns are labelled by **pricebt's own risk-measure objects**. They must be hashable, compare equal on `(name, parameters)`, repr as `Name(k:v, ...)`, and support `Price(currency='USD')`-style cloning, so that `summary[Price]` works exactly as in gs. No gs_quant import is allowed, so re-implement a tiny frozen dataclass.
2. Keep `trade_ledger()` columns, order, dtypes (object), sign conventions, and `Long Short = direction of the opening payment`.
3. Keep the `strategy_as_time_series()` 2x2 MultiIndex shape. Risk column labels there are **strings**. Bucketed risk is summed per trade.
4. A bucketed measure (e.g. an asset-config `delta_ladder` exposed as a pricebt `IRDelta`) should put a **DataFrame per cell** in `result_summary`, with columns `mkt_type, mkt_asset, mkt_class, mkt_point, value` (value in reporting ccy per 1bp). This is the same shape as gs. Scalar measures are floats. Portfolio-level expressions (e.g. `(market, trades) -> {tenor: ccy per bp}`) map naturally onto this cell. pricebt then converts `{tenor: value}` into that frame, with `mkt_type='IR'`, `mkt_asset=<ccy>` and `mkt_class=<curve name>` taken from the asset config.
5. Multi-currency: gs refuses to mix currencies unless `result_ccy` is given. pricebt v2 must support multi-currency, and the natural 1:1 mapping is `run_backtest(..., result_ccy='USD')`. Every per-asset value (npv, cash, risk) is converted with the FX expression before it is stored, so `cash_dict` has a single key. Decide whether `result_ccy=None` with a mixed book should raise (gs parity) or default to a configured reporting currency.
6. Quirks Q1 (stale PV ffill), Q2 (`initial_value` appearing late), the never-invalidated summary cache (§4.1), the `set()`-ordered risk columns, `map_ccy_name_to_ccy` returning None, and the RebalanceActionImpl bug `transaction_cost_entries[d].append(exit)` (generic_engine.py:591, which appends the builtin `exit`) are all gs defects. Recommended policy: **fix Q1** by treating a date with no holdings as PV 0 (i.e. use `zero_on_empty_dates` semantics for `result_summary`) and **fix** the rest. Document each as an intentional deviation, and keep a `gs_compat=True` switch only if exact numerical parity with gs is demanded.
7. Do not port `OisFixingCashAccrualModel` or `PredefinedAssetBacktest` in v2.
