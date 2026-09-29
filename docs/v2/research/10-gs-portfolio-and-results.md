# 10 — gs_quant `Portfolio` and risk results vs pricebt: exhaustive gap analysis

Scope: `gs_quant.markets.portfolio` (`Portfolio`, `Grid`), `gs_quant.priceable`, `gs_quant.risk.results`
(`PortfolioRiskResult`, `MultipleRiskMeasureResult`, `PortfolioPath`, the futures), `gs_quant.risk.core`
(`*WithInfo`, `aggregate_risk`, `aggregate_results`, `subtract_risk`, `sort_risk`, `sort_values`,
`combine_risk_key`), `gs_quant.risk.transform`; every notebook under `documentation/03_portfolios`; compared
with pricebt `v2-ir-risk` (worktree `pricebt-ir`, HEAD `a4535bd`).

## How to read this note

- **Line references.** gs lines are given as `file:L154 (2.1.17: L2117)`. The 1.5.4 files are under
  `C:\Users\chris\anaconda3\Lib\site-packages\gs_quant`, and the 2.1.17 files are under
  `C:\Users\chris\clee\gsquant-temp-claude\gs-quant\gs_quant`. When only one number is given, it is 1.5.4.
  Offsets from 1.5.4 to 2.1.17:
  - `markets/portfolio.py`: +5 up to line 377, +6 after it.
  - `risk/results.py`: +1 from line 42.
  - `priceable.py`: 0 up to line 113.
  - `risk/core.py`: the offset varies, so the 2.1.17 number is given explicitly.
- **pricebt lines** are in `pricebt-ir/src/pricebt/...`.
- **VERIFIED** means the behaviour was run in this session, not only read in the code:
  - gs: 1.5.4 with pandas 2.3.1 (`C:\Users\chris\anaconda3\python.exe`), using fabricated
    `FloatWithInfo`/`SeriesWithInfo`/`DataFrameWithInfo` results in `PricingFuture`s. No session or network
    was used.
  - pricebt: `stir` env, `PYTHONPATH=pricebt-ir/src`, `-B`.
  - The probe scripts were scratch files and are not kept. Their key outputs are quoted inline.
- **1.5.4 vs 2.1.17.** The two versions behave the same for everything in this note except the items in §6.
  Everything else is typing and import order (`diff --strip-trailing-cr -w` over all six files).

---

## Key facts (decision-relevant)

1. **pricebt breaks documented historical usage.**
   - `hist_results[Price].aggregate()` (030002 cell 16) raises `KeyError: 'mkt_type'` in pricebt. It routes a
     `SeriesWithInfo` into `combine_bucketed_frames` (`risk/results.py:486-487`). gs returns
     `SeriesWithInfo(sum(results))`, summed per date (core.py:597-598 (2.1.17: 612-613)).
   - Historical `to_frame()` in pricebt sums across dates. Per instrument this gives `2.5 = 1.0 + 1.5`,
     where gs gives a `dates × instrument_name` pivot.
   - Historical bucketed values are a `Series` of `DataFrame`s (object dtype) in pricebt
     (`assets/pricing.py:550-566`). In gs they are one `DataFrameWithInfo` indexed by `date` (core.py:410-415).
2. **Nested portfolios are only half supported by pricebt's PRR.**
   - `prr['5y']`, `prr[inst]`, `prr[[i1, i2]]` and `prr[0:2]` all raise `KeyError` on a nested result.
   - `list(prr)` yields sub-PRRs instead of leaf values.
   - `to_frame()` puts a *tuple of values* in a cell and has no `portfolio_name_N` columns, so 030006
     (`to_frame('value','portfolio_name_0','instrument_name')`) raises `KeyError`.
   - `transform()` raises `TypeError`.
3. **The keystone missing piece is `PortfolioPath` + `Portfolio.all_paths` + `Portfolio.paths()` returning
   paths.** In pricebt, `Portfolio.paths()` returns the matched *objects*, which is a semantic mismatch with gs.
   Porting these ~60 lines (results.py:548-590; portfolio.py:466-501, 224-230) unlocks all of the following at
   once:
   - `Portfolio.subset`;
   - `PRR.subset`, list indexing and slice indexing;
   - lookup of a nested name or instrument;
   - leaf-order `__iter__`;
   - `transform`;
   - correct `to_frame` labels.
4. **gs itself is buggy exactly where nesting mixes leaves and sub-portfolios (VERIFIED).**
   - `PRR.to_frame()` mislabels rows. It zips depth-first futures against breadth-first portfolio records
     that use a `records.pop(0)` placeholder scheme. gs's own notebook 030009 (`Portfolio([eur_port, usd_port,
     swaption_1])`) has this shape.
   - `PRR.transform()` on a nested result returns a PRR whose `futures` are flat leaves while `portfolio` still
     has its nested children, so `t['7y']` returned `2.0` where the right value is `5.0`.
   - `Portfolio.all_portfolios` never descends. As a result `'deep' in top` is `False`, while `top['deep']`
     finds the leaf.
   - `subtract_risk` always raises `AssertionError`.
   - `PRR + 1` raises `RuntimeError`.
   - `MultipleRiskMeasureResult * k` on historical values raises `AttributeError`.
   - pricebt should implement the intended semantics and record a DEV id for each (§5).
5. **gs builds time series by adding single-date PRRs (VERIFIED).** `p(d0) + p(d1)` with the same portfolio and
   measure and different dates gives a PRR whose leaves are `SeriesWithInfo` over `(d0, d1)`, and whose
   `.aggregate()` and `.to_frame()` are the historical shapes. pricebt raises `ValueError('Results overlap…')`
   instead. Its `dates` fallback is `(None,)` (`risk/results.py:425-427`), where gs uses
   `first_value(...).risk_key.date` (results.py:745-747).
6. **`Portfolio.calc(fn=...)` is applied per child in gs** (portfolio.py:594 → instrument/core.py:202-213). pricebt
   applies `fn` once, to the whole `PortfolioRiskResult` (`assets/pricing.py:597,608`).
7. **pricebt's `aggregate()` is weaker than gs's error contract.**
   - pricebt silently sums results whose risk keys differ (for example different dates). gs raises
     `ValueError('Cannot aggregate results with different pricing keys')` unless
     `allow_mismatch_risk_keys=True`.
   - pricebt raises a pandas error for heterogeneous types. gs raises
     `'Cannot aggregate heterogeneous types: …'` unless `allow_heterogeneous_types=True`.
   - pricebt ignores both flags. The backtester passes `(True, True)` (backtest_objects.py:213 in 2.1.17)
     and `allow_mismatch_risk_keys=True` (generic_engine.py:506).
8. **P&L decomposition in gs (030007) is server-side.**
   - `PnlExplain(to_market)` is a relative risk measure. Its `pricing_context` clones the current context with
     `RelativeMarket(from_market, to_market)` (2.1.17 risk/measures.py:26-52). The server returns a
     `mkt_type/mkt_asset/value` frame through `risk_by_class_handler` (2.1.17 risk/result_handlers.py:168-202).
   - The notebook's own arithmetic is `FloatWithInfo - FloatWithInfo` (a **plain float** in gs) and
     `.aggregate().value.sum()`.
   - gs has **no** `PortfolioRiskResult.__sub__`, `MultipleRiskMeasureResult.__sub__` or `Portfolio.__sub__`.
   - pricebt accepts and silently ignores `PricingContext(market=...)` (DESIGN §6.5), so porting 030007
     literally would silently compute a wrong "to-market" price.
9. **Server-only surface.** The following should be stubs that raise `NotSupportedError`:
   - on `Portfolio`: `id`, `quote_id`, `get`, `from_portfolio_id`, `from_portfolio_name`, `from_eti`,
     `from_book`, `from_asset_id`, `from_asset_name`, `from_quote`, `save`, `save_as_quote`,
     `save_to_shadowbook`, and `market()` (as coordinates);
   - all of `PortfolioManager`.

   The portable members pricebt lacks are:
   - on `Portfolio`: `from_frame`, `from_csv`, `to_csv`, `subset`, `all_paths`, `Grid`, and the `__repr__`
     count;
   - on the results: `PRR.__mul__`, `subset`, `display_options`, the MRMR API (`instrument`, `dates`,
     `to_frame`, `+`, `*`, date indexing), `FloatWithInfo.__mul__` and `.to_frame()`,
     `DataFrameWithInfo.to_frame()`, `aggregate_risk`, `sort_risk` and `combine_risk_key`.

---

## 1. `Portfolio` — full gs public API vs pricebt

`@dataclass class Portfolio(PriceableImpl)` (portfolio.py:45-621 (2.1.17: 50-627)). Its docstring is "A
collection of instruments. Portfolio holds a collection of instruments in order to run pricing and risk
scenarios". The class has **no dataclass fields**, because `__init__` is custom. Status legend:
**S** supported · **P** partial or different · **M** missing · **SRV** server-only (stub) · **N/A** not
applicable.

### 1.1 Construction, identity, container protocol

| Member | gs semantics (quoted/condensed) | gs line | pricebt | Status |
|---|---|---|---|---|
| `__init__(priceables=(), name=None)` | `:param priceables: constructed with an instrument, portfolio, iterable of either, or a dictionary where key is name and value is a priceable`. A dict **sets `priceable.name = key`** (mutates the value). Otherwise `self.priceables = priceables`. Also sets `name`, `__id=None` and `__quote_id=None`. | 53-74 | `portfolio.py:23-37` (dict, single, iterable, and `None`→`()`) | S |
| `priceables` get/set/del | The getter returns the tuple of direct children. The setter wraps a single `PriceableImpl` as a 1-tuple, otherwise calls `tuple(...)`, and rebuilds `__priceables_by_name` (`if i and i.name: setdefault(name, []).append(idx)`). The deleter sets both to `None`. | 177-193 | `portfolio.py:43-53`. The setter re-runs `__init__`, so a **dict** is accepted too. gs would call `tuple(dict)`, which gives the keys, and then fail on `i.name` in the name-map loop. The deleter sets `()`, not `None`. | P (edge only) |
| `__repr__` | `Portfolio(<name>, N instrument(s))` or `Portfolio(N instrument(s))`, where N = `len(all_instruments)`. VERIFIED: `Portfolio(5 instrument(s))`. | 76-83 | `portfolio.py:188-189`: `Portfolio(name)` / `Portfolio` | P (cosmetic) |
| `__len__` / `__iter__` | over **direct** children | 131-135 | `97-101` | S |
| `__hash__` | `hash(name) ^ hash(__id)`, then XOR of the children's hashes | 137-142 | `178-186` (`hash(None)` for id) | S |
| `__eq__` | Other must be a `Portfolio`. For every path in `self.all_paths`, `path(self) != path(other)` → False. `IndexError`/`TypeError` → False. **Asymmetric**, and names are not compared. | 144-157 | `163-176` (own leaf-path walk) | S |
| `__add__(other)` | `Portfolio` only, else `ValueError('Can only add instances of Portfolio')`. Returns `Portfolio(self.__priceables + other.__priceables)`, **with no name**. | 159-163 | `192-195` | S |
| `__contains__(item)` | `PriceableImpl`: `any(item in p.__priceables for p in self.all_portfolios + (self,))`. `str`: the same over the `__priceables_by_name` maps. Anything else: False. Because `all_portfolios` is depth-1 only (§5 BUG-P1), **a leaf at depth ≥ 2 is not "in"**. VERIFIED: `'deep' in top → False`. | 123-129 | `143-146`, via recursive `paths` (depth unlimited). VERIFIED: `True`. | P (pricebt more correct → DEV) |
| `__sub__`, `index`, `remove`, `__setitem__`, `__radd__`, `__mul__`, `from_dicts` | **Not defined in gs**, in either 1.5.4 or 2.1.17 (not in portfolio.py, and not in the rst autosummary). VERIFIED: `nested - flat` → `TypeError`, and `nested.index` → `AttributeError`. The only dict constructor is the inherited, fieldless `from_dict` (§1.8). | — | not defined | parity |

### 1.2 Structure properties

| Member | gs semantics | gs line | pricebt | Status |
|---|---|---|---|---|
| `instruments` | `tuple(unique_everseen(i for i in __priceables if isinstance(i, Instrument)))`: direct instruments, de-duplicated by `__eq__`/`__hash__`. VERIFIED: `Portfolio([s1, s1.clone()]).instruments → (s1,)`. | 195-197 | `55-65` (`not isinstance(c, Portfolio)` + a seen-set) | S |
| `all_instruments` | `chain(self.instruments, chain.from_iterable(p.all_instruments for p in self.all_portfolios))`, de-duplicated. It still reaches every depth, because the recursion goes through `p.all_instruments`. | 199-202 | `67-82` | S |
| `portfolios` | direct sub-portfolios | 204-206 | `84-86` | S |
| `all_portfolios` | Intended to be recursive, but **returns only the direct sub-portfolios** (VERIFIED on a 3-level tree: `(Portfolio(L1),)`). See BUG-P1. | 208-222 | `88-94`: truly recursive (VERIFIED: `(L1, L2)`), no de-duplication | P (DEV) |
| `all_paths` | `Tuple[PortfolioPath, ...]` to every **leaf**, in **breadth-first order with a level's direct leaves first**. `stack.insert(0, (path, sub))` for sub-portfolios, and leaves are appended as they are met. VERIFIED: `Portfolio([eur, usd, s5]).all_paths → ((2,), (0,0), (0,1), (1,0), (1,1))`, and a 3-level tree gives `((1,), (0,1), (0,0,0))`. | 466-480 | **missing** (`_leaf_paths` is private and depth-first, `148-155`) | M |
| `id`, `quote_id` | Marquee ids | 169-175 | — | SRV |

### 1.3 Lookup

| Member | gs semantics | gs line | pricebt | Status |
|---|---|---|---|---|
| `paths(key)` | `key` must be `str`, `Instrument` or `Portfolio`, else `ValueError('key must be a name or Instrument or Portfolio')`. For a `str`, the direct indices come from the name map; otherwise the direct indices are those where `p == key or getattr(p, "unresolved", None) == key`. Then it recurses into direct sub-portfolios, prefixing the paths. **Returns `PortfolioPath`s**: own matches first (in index order), then the sub-portfolios' matches. | 482-501 | `103-124`: returns the **matched objects**, not paths. The match order is depth-first pre-order, so the order differs too. It keeps gs's `bool(c) and c.name` name predicate. | P (semantic mismatch; blocks §2) |
| `__getitem__(item)` | `int`/`slice` → `__priceables[item]`. `PortfolioPath` → `item(self, rename_to_parent=True)`. `list` → `tuple(self[p] for it in item for p in self.paths(it))`. Any other key → `tuple(self[p] for p in self.paths(item))`. Then **`values[0] if len(values) == 1 else values`**: no match gives `()`, several matches give a tuple. VERIFIED: `nested['5y']` → a 2-tuple, `nested['nope']` → `()`, `nested[PortfolioPath((1,0))]` → the leaf, `nested[PortfolioPath(0)]` → the `EUR` sub-portfolio. | 110-121 | `126-141`: int, slice, list, str and instrument all S. **`PortfolioPath` M.** | P |
| `subset(paths, name=None)` | `paths_tuple = tuple(paths)`. If it holds one path and `self[path]` is a `Portfolio`, it returns that portfolio itself (**not a copy, and `name` is ignored**). Otherwise it returns `Portfolio(tuple(self[p] for p in paths_tuple), name=name)`, which is flattened. VERIFIED. | 224-230 | — | M |

### 1.4 Mutation and copies

| Member | gs semantics | gs line | pricebt | Status |
|---|---|---|---|---|
| `append(priceables)` | `self.priceables += (p,)` for a single `PriceableImpl`, else `tuple(p)`. This goes through the setter, so the name map is rebuilt. | 410-411 | `197-203` | S |
| `extend(portfolio)` | `self.priceables += tuple(p for p in portfolio)` | 418-419 | `205-207` | S |
| `pop(item)` | `priceable = self[item]`, then `self.priceables = [inst for inst in self.instruments if inst != priceable]`, which **drops every nested portfolio** (VERIFIED: `Portfolio([eur, s5]).pop('7y')` leaves `()`). Returns the item. If `self[item]` is a tuple (duplicate names), nothing equals the tuple, so nothing is removed. | 413-416 | `209-215` | S |
| `clone(clone_instruments=False)` | A new Portfolio with the same name. Nested portfolios are cloned recursively, and leaves are shared unless `clone_instruments`. **Copies `__id`/`__quote_id`.** It takes no `**kwargs`. | 611-621 | `217-226` | S |
| `scale(scaling, in_place=True)` | `instruments = self._get_instruments(pos_date, in_place, False)` (= `all_instruments` when there is no id). In place: `inst.scale(scaling, in_place)` for each of `all_instruments`, returning `None`. Otherwise: `Portfolio([inst.scale(scaling, False) for inst in instruments])`, which is **flattened and unnamed**. | 402-408 | `228-233` (DEV-I1: `quantity_`) | S |

### 1.5 Pricing (from `PriceableImpl`, priceable.py)

| Member | gs semantics | gs line | pricebt | Status |
|---|---|---|---|---|
| `calc(risk_measure, fn=None)` | `priceables = self._get_instruments(pos_date, False, True)` (direct children). `with self._pricing_context:` it returns `PortfolioRiskResult(self.clone(), (rm,) if isinstance(rm, RiskMeasure) else rm, [p.calc(risk_measure, fn=fn) for p in priceables])`. The comment explains why it clones: "PortfolioRiskResult should hold a copy of the portfolio instead of a reference … should it later be modified in place (eg: resolution)". **`fn` is applied per child**: `Instrument.calc` wraps its future in a callback with `ret.set_result(fn(f.result()))`, which catches the exception into the future (instrument/core.py:202-213), and a nested `Portfolio.calc` passes `fn` down. It always returns a PRR, never a future. | 585-595 | `239-240` → `assets/pricing.py:588-614`. **`fn(result)` is applied to the whole PRR** (`597`, `608`). | P (fn semantics) |
| `price(currency=None)` | "Present value in local currency": `self.calc(Price(currency=currency)) if currency else self.calc(Price)` | priceable.py:75-88 | `242-243` | S |
| `dollar_price()` | "Present value in USD": `self.calc(DollarPrice)` | priceable.py:44-73 | `245-246` (DollarPrice → Price(USD), §8.1 rule 2) | S |
| `resolve(in_place=True)` | Calls each direct child's `resolve(in_place)` under the context. **In place: returns `None` even inside an entered context**, because there is no return statement on that branch. Not in place: returns `Portfolio(name=self.name)` with the resolved children. Under HPC it returns `{date: Portfolio(priceables, name)}`; a date where any child failed is **skipped with `_logger.error`** (523-526). The result is a `PricingFuture` if `_return_future`. | 503-532 | `236-237` → `pricing.py:642-669`. In place inside a context it returns `PricingFuture(None)`, not `None` (`pricing.py:665-666`). The HPC skip-on-failure is **not probed**. | P (minor) |
| `market()` | "Market Data map of coordinates and values": the per-instrument `MarketData` measure, merged into an `OverlayMarket`, raising `ValueError('Conflicting values for …')` on a mismatch > 1e-6. Returns a dict by date under HPC. | 534-583; priceable.py:90-140 | — | SRV (no coordinates in pricebt) |
| `_get_instruments(position_date, in_place, return_priceables)` | For a persisted portfolio (`self.id`), fetches positions from Marquee. Otherwise returns `__priceables` or `all_instruments`. | 597-609 | — | SRV/N/A |

### 1.6 Frame I/O

| Member | gs semantics | gs line | pricebt | Status |
|---|---|---|---|---|
| `to_frame(mappings=None)` | One record per leaf, depth-first in `priceables` order: `as_dict()`, plus `'$type': type_` when the object has no `asset_class`, plus `instrument=<obj>` and `portfolio=<immediate parent's name>`. The index is `['portfolio','instrument']`. The columns are `$type`, `type` and `asset_class` (pushed to the front in that loop order, so the final order is `asset_class, type, $type`), then the rest **sorted**. `mappings`: a `str` value gives `df[key] = df[value]` (an alias), and a callable gives `df[key] = df.apply(value, axis=1)`. VERIFIED: the top-level leaf's `portfolio` is `NaN` when the root has no name. | 421-457 | `249-280` (no `$type`, DEV-I2 `quantity_`) | S |
| `to_csv(csv_file, mappings=None, ignored_cols=None)` | `to_frame(mappings)`, then drops `ignored_cols` via `np.setdiff1d` (**which also sorts every column alphabetically**), then `reset_index(drop=True)`, then `to_csv`. | 459-464 | — | M |
| `from_frame(data, mappings=None)` | See the algorithm below. | 367-391 | — | M |
| `from_csv(csv_file, mappings=None)` | `pd.read_csv(csv_file, skip_blank_lines=True).replace({np.nan: None})`. Any column matching `r'\.[0-9]'` (pandas' duplicate suffix) raises `ValueError(f'Duplicate column values {dupelist}')`. Then `from_frame`. | 393-400 | — | M |

`from_frame` algorithm (1.5.4 367-391; 2.1.17 373-397):

```python
def get_value(row, attribute):
    value = mappings.get(attribute, attribute)            # mapping value: column name OR callable(row)
    return value(row) if callable(value) else row.get(value)
data = data.replace({np.nan: None})
for row in (r for _, r in data.iterrows() if any(v for v in r.values if v is not None)):   # 2.1.17: data[data.notnull().any(axis=1)]
    for init_keys in (('asset_class', 'type'), ('$type',)):
        init_values = tuple(filter(None, (get_value(row, k) for k in init_keys)))
        if len(init_keys) == len(init_values):
            instrument = Instrument.from_dict(dict(zip(init_keys, init_values)))      # class by (asset_class, type)
            instrument = instrument.from_dict({p: get_value(row, p) for p in instrument.properties()})
            break
    else: raise ValueError('Neither asset_class/type nor $type specified')
return cls(instruments)          # flat, unnamed
```

- Every instrument **property** is looked up, either through a mapping or under its own name as a column.
  Properties with no such column get `None`.
- The notebooks' mappings target gs field names (`fixed_rate`, `notional_amount`, `termination_date`, …).
- pricebt has the generated classes with gs field lists (`instrument/_gs_fields.py`), so a pricebt `from_frame`
  can be built from `(asset_class, type) → class` over `GS_FIELDS`, plus a `pricebt_asset` column for
  `ConfigInstrument`.

### 1.7 Server-only (Marquee persistence): all SRV

| Member | gs line |
|---|---|
| `from_eti` | 237-239 |
| `from_book` | 241-243 |
| `from_asset_id` | 245-258 |
| `from_asset_name` | 260-263 |
| `get(portfolio_id, portfolio_name, query_instruments)` | 265-276 |
| `from_portfolio_id` / `from_portfolio_name` (deprecated) | 278-293 |
| `from_quote` | 295-300 |
| `save(overwrite)` (raises `ValueError('Cannot save portfolios with nested portfolios')` first) | 302-322 |
| `save_as_quote` | 324-348 |
| `save_to_shadowbook` | 350-365 |
| `__position_context` / `PositionContext` | 165-167 |

`gs_quant.markets.portfolio_manager.PortfolioManager` (portfolio_manager.py:82-709) is entirely server-side:
- `get_performance_report`, `schedule_reports`, `run_reports`;
- `set_entitlements`, `share`, `set_currency`;
- the tag hierarchy, the portfolio tree, `get_schedule_dates`;
- the AUM methods, `get_pnl_contribution`, `get_macro_exposure`, `get_factor_scenario_analytics`,
  `get_risk_model_predicted_beta`.

### 1.8 Inherited `Base`/`dataclass_json` plumbing (listed in the gs docs `classes/gs_quant.markets.portfolio.Portfolio.rst`)

The following are all no-ops or empty for a Portfolio, because it has no dataclass fields: `as_dict`,
`to_dict`, `to_json`, `from_dict`, `from_json`, `from_instance`, `default_instance`, `properties`,
`properties_init` and `schema`. Status: **N/A**. Do not port them, except that `as_dict()` returning `{}` may be
worth mirroring if some caller relies on it. No such caller was found in the backtests.

### 1.9 `Grid(Portfolio)`

- Location: portfolio.py:624-650 (2.1.17: 630-656). pricebt: **M**.
- Docstring: "A grid of instruments. A grid is a type of portfolio which represents a grid of similar
  instruments".
- Signature: `Grid(priceable, x_param, x_values, y_param, y_values, name=None)`.
- It builds one sub-portfolio per y value: `Portfolio([priceable.clone(**{x_param: x, 'name': x, y_param: y})
  for x in x_values], name=y)`. Each sub-portfolio is **named by the y value, which need not be a string**, and
  each leaf is **named by the x value**.
- VERIFIED: `Grid(IRSwap(...), 'termination_date', ['5y','10y'], 'fixed_rate', [0.01, 0.02])` has records
  `{'portfolio_name_0': 0.01, 'instrument_name': '5y'}, …`.
- `repr` is the dataclass default, `Grid()`.
- It needs `Instrument.clone(**kw)` on **unresolved** instruments. pricebt supports that
  (`instrument/__init__.py:222-244`).

### 1.10 `Portfolio._to_records()` (used by `PRR.to_frame`)

portfolio.py:85-108 (2.1.17: 90-113). There is no pricebt equivalent.

- A leaf's name is its `obj.name` if set, otherwise `f'{type_name}_{idx}'`, where `type_name` is
  `obj.type_.name` (an `AssetType`), so e.g. `Swap_0`. A sub-portfolio with no name is `Portfolio_{idx}`.
  VERIFIED.
- Algorithm: breadth-first with `stack.insert(0, (path, sub))` / `stack.pop()`.
  - A sub-portfolio emits a placeholder record `{**current, f'portfolio_name_{depth}': name}`.
  - A leaf emits `{**current, 'instrument_name': name}`.
  - A popped sub-portfolio takes `current_record = records.pop(0)`. **That assumes the front of `records` is
    that sub-portfolio's placeholder, which is false whenever a level mixes leaves and sub-portfolios.** See
    BUG-R1.

---

## 2. Result objects — full gs API vs pricebt

### 2.1 `PortfolioPath` (results.py:548-590 (2.1.17: 549-591)) — pricebt **M**

```python
class PortfolioPath:
    def __init__(self, path): self.__path = (path,) if isinstance(path, int) else path
    __repr__ -> repr(tuple); __iter__/__len__ over the tuple; __add__ -> PortfolioPath(a + b)
    __eq__ -> tuple equality (no isinstance guard); __hash__ -> hash(tuple); .path -> tuple
    def __call__(self, target, rename_to_parent=False):
        parent = None; path = list(self.__path)
        while path:
            elem = path.pop(0)
            parent = target if len(self) - len(path) > 1 else None
            target = target.futures[elem] if isinstance(target, CompositeResultFuture) else target[elem]
            if isinstance(target, PricingFuture) and path:
                target = target.result()
        if rename_to_parent and parent and getattr(parent, 'name', None) and not isinstance(target, InstrumentBase):
            target = copy.copy(target); target.name = parent.name
        return target
```

- The same object walks a `Portfolio` (through `[int]`), a `PRR` (through `.futures[int]`), and a plain tuple
  of futures (as in `p(self.futures)` in `subset`).
- `rename_to_parent` renames a **non-instrument** target (a sub-portfolio) copy to its parent's name.
- In pricebt, the PRR walk must resolve `_MultiMeasureFuture`/`LazyFuture` only at intermediate steps,
  mirroring `if isinstance(target, PricingFuture) and path`. That keeps the leaf future unevaluated, which is
  what DESIGN §8.2's lazy group aggregation needs.

### 2.2 `PortfolioRiskResult(CompositeResultFuture)` (results.py:593-972 (2.1.17: 594-973))

| Member | gs semantics | gs line | pricebt (`risk/results.py`) | Status |
|---|---|---|---|---|
| `__init__(portfolio, risk_measures, futures)` | `CompositeResultFuture(futures)`: completes when every future does. Stores `portfolio` and `tuple(risk_measures)`. | 594-597 | `285-288`. It wraps plain values in `PricingFuture`, which gs does **not** do (gs requires futures). | S (superset) |
| `portfolio`, `risk_measures`, `futures` | properties (`futures` is on `CompositeResultFuture`, 284-286) | 778-784 | attributes | S |
| `result(timeout=None)` → `self` | 810-812 | — | `513-514` (and `done()` `516-517`) | S |
| `__len__` | `len(self.futures)`: the **direct children** | 693-694 | `291-292` | S |
| `__iter__` | `iter(self.__results())`, which is `tuple(self.__result(p) for p in self.__portfolio.all_paths)`: the **leaf** values in **`all_paths` order** (breadth-first, direct leaves first). VERIFIED: `Portfolio([eur, usd, s5])` iterates `[5.0, 1.0, 2.0, 3.0, 4.0]`. | 696-697, 945-947 | `303-304`: the **direct children's** results (a sub-PRR for a nested child). DESIGN §8.2 line 683 specifies this, and it differs from gs for nested portfolios. | P (nested) |
| `__repr__` | `f'{risk_measures} Results' + (f' for {portfolio.name}' if name) + f' ({len(self)})'`. VERIFIED: `(Price,) Results for EUR (2)`. | 686-691 | object default | M (cosmetic) |
| `__getitem__` | the dispatch table in §2.3 | 599-676 | `319-326` | P |
| `__contains__(item)` | `RiskMeasure` → `item in risk_measures`; `date` → `item in self.dates`; anything else → `item in self.__portfolio` (Portfolio `__contains__`, so depth-1 only, BUG-P1). | 678-684 | `406-415`: RiskMeasure, date, else a **direct-child** lookup via `_by_instrument_or_name` | P |
| `get(item, default)` | `self[item]`, or `default` on `KeyError`/`ValueError`. **`default` is required** (no default value). | 967-972 | `400-404` (`default=None`) | S (superset) |
| `dates` | The sorted union over the leaf results. For MRMR/PRR it uses `.dates` if they are all `dt.date`; for a DataFrame/Series it uses `.index` if every index value is a `dt.date`. A `TypeError` during sorting gives `()`. | 786-799 | `306-316` (skips `LazyFuture`s) | S |
| `_multi_scen_key` | scenarios (MultiScenario) | 801-808 | — | N/A (no scenarios) |
| `__mul__(other)` | int/float: `PortfolioRiskResult(portfolio, risk_measures, [f * other for f in futures])`, using `PricingFuture.__mul__` → `result() * other`. Otherwise it **returns** (does not raise) `ValueError('Can only multiply by an int or float')`. VERIFIED: `prr * 2` doubles every leaf, and the leaves stay `FloatWithInfo`. | 699-703 | — (`TypeError`) | M |
| `__add__(other)` | the full algorithm in §2.4 | 705-776 | `418-445` (no date composition, no value fill-in) | P |
| `__sub__`, `__truediv__`, `__neg__`, `available_measures` | **not defined in gs** (VERIFIED: `p1 - p1` → `TypeError`) | — | not defined | parity |
| `subset(items, name=None)` | `paths = chain.from_iterable((i,) if isinstance(i, PortfolioPath) else self.__paths(i) for i in items)`, then `sub_portfolio = self.__portfolio.subset(paths, name=name)`, and returns `PortfolioRiskResult(sub_portfolio, self.risk_measures, [p(self.futures) for p in paths])`. **Items may be int, str, PortfolioPath or Priceable**, and **the result is flat** (one future per path). A single path to a sub-portfolio returns that sub-portfolio (`Portfolio.subset`). VERIFIED: `prr.subset([PortfolioPath((1,1))])` → `[4.0]`. | 814-817 | — | M |
| `transform(risk_transformation=None)` | `None` → `self`. With more than one measure → `MultipleRiskMeasureResult(self.portfolio, ((r, self[r].transform(t)) for r in measures))`. **Note the MRMR's "instrument" is the portfolio.** With one measure → `flattened = t.apply(self.__results())` (leaf values in `all_paths` order), then `PortfolioRiskResult(self.portfolio, self.risk_measures, [PricingFuture(r) for r in flattened])`. **Correct only for flat portfolios** (BUG-R2). | 819-836 | `448-456`. The multi-measure branch builds an MRMR without the instrument. The single-measure branch applies to `tuple(self)` (direct children), so a nested PRR raises `TypeError` (VERIFIED). | P |
| `aggregate(allow_mismatch_risk_keys=False, allow_heterogeneous_types=False)` | With more than one measure: `MultipleRiskMeasureResult(self.portfolio, ((r, self[r].aggregate()) for r in measures))`. **Note that the flags are NOT forwarded** in this branch. Otherwise `aggregate_results(self.__results(), …)` (§2.7). | 838-848 | `471-488`: the multi-measure branch matches (also drops the flags); there is lazy group aggregation (DESIGN §8.2); the flags are **ignored**, a historical Series gives a `KeyError`, there are no key or error checks | P |
| `to_frame(values='default', index='default', columns='default', aggfunc='sum', display_options=None)` | §2.5 | 872-923 | `491-511` (flat, single-date scalar only) | P |
| `_to_records(display_options=None)` | §2.5 | 850-870 | — | M |

### 2.3 `PortfolioRiskResult.__getitem__` dispatch (results.py:599-676), in evaluation order

| # | `item` | gs behaviour | VERIFIED gs | pricebt |
|---|---|---|---|---|
| 1 | `RiskMeasure` or an iterable of them | A measure that is not computed → `ValueError(f'{item} not computed')`. If exactly one measure was computed → **`self`**. Otherwise, for each direct child: a sub-PRR → `result[item]`; a leaf → `MultipleRiskMeasureFuture(priceable, {k: PricingFuture(v) for k, v in _value_for_measure_or_scen(result, item).items()})`. Returns `PortfolioRiskResult(portfolio, tuple(item) or (item,), futures)`. | yes | Single measure only (`328-344`). A list of measures is not detected by `_is_risk_measure` and falls through to name lookup → `KeyError`. |
| 2 | `Scenario` or an iterable | a scenario slice | — | N/A |
| 3 | `dt.date` or an iterable of dates | For each direct child: MRMR/PRR/MultipleScenarioResult → `PricingFuture(result[item])`; DataFrame/Series → `PricingFuture(_value_for_date(result, item))`; anything else → `RuntimeError('Can only index by date on historical results')`. Returns a PRR with the same portfolio and measures. | `hprr[D1]` → a PRR of `FloatWithInfo` with `risk_key.date = D1`. `hprr[[D0]]` → leaves stay `SeriesWithInfo`. `hb[D1][0]` → a `DataFrameWithInfo` with a RangeIndex. | Single date S (`361-388`); a **list of dates M** (`KeyError`) |
| 4 | an iterable whose elements are all `InstrumentBase` (a list/tuple of instruments) | `self.subset(item)`, which is flat | `prr[[s1, s5]]` → `(Price,) Results (2) [1.0, 5.0]` | M (`KeyError`) |
| 5 | a `list` of length 1 | `self.__results(items=item[0])`. The comment says "Inputs from excel always becomes a list". | — | M |
| 6 | anything else (`int`, `slice`, `str`, `Priceable`) | `__results(items=item)`: `paths = self.__paths(item)`; `KeyError(f'{items}')` if there are none; **`self.__result(paths[0])` (the FIRST match only)**, or `self.subset(paths)` for a slice. | `prr['5y']` → `1.0`, the EUR 5y (the USD 5y is ignored, while `Portfolio['5y']` returns both). `prr[0]` → the EUR sub-PRR. `prr[0:2]` → a subset PRR. `prr[PortfolioPath((1,1))]` → **`KeyError`**: `__paths` returns `None` for a `PortfolioPath`, so only `subset` accepts paths. | int S. str/instrument: **direct children only** (`390-398`), so nested → `KeyError`. slice M. |

- `__paths(items)` (925-943):
  - An `int` gives `(PortfolioPath(i),)`.
  - A `slice` gives `PortfolioPath(i) for i in range(len(portfolio))[slice]`, over **direct** children.
  - A `str` or `Priceable` gives `portfolio.paths(items)`. If that finds nothing and `items` is a resolved
    instrument, it retries with `items.unresolved`, keeps only the paths whose result
    `risk_key.ex_measure == items.resolution_key.ex_measure`, and otherwise raises `KeyError(f'{items} not in
    portfolio')` or `KeyError(f'Cannot slice {items} which is resolved in a different pricing context')`.
  - pricebt has the `unresolved` fallback (`395-397`), but not the resolution-key filter.
- `__result(path, risk_measure=None)` (955-965): `res = path(self.futures).result()`. If there is exactly one
  measure and none is given, it returns `res[measure]` when `res` is an MRMR or PRR. pricebt's
  `_result_for` (`297-301`) is the same for direct children.

### 2.4 `PortfolioRiskResult.__add__` (results.py:705-776)

```python
if isinstance(other, (int, float)):
    return PortfolioRiskResult(portfolio, measures, [f + other for f in self.futures])   # BUG-R3: raises (see below)
elif isinstance(other, PortfolioRiskResult):
    if not _risk_keys_compatible(first_value(self), first_value(other)) and not set(self.all_instruments).isdisjoint(other.all_instruments):
        raise ValueError('Results must have matching scenario and location')
    self_dt  = (first_value(self).risk_key.date,)  if len(self.dates) == 0  else self.dates     # <-- single-date PRRs use their risk_key.date
    other_dt = (first_value(other).risk_key.date,) if len(other.dates) == 0 else other.dates
    if measures overlap and dates overlap and instruments overlap:
        raise ValueError('Results overlap on risk measures, instruments or dates')
    self_futures, other_futures = as_multiple_result_futures(self).futures, as_multiple_result_futures(other).futures
    if self.portfolio is other.portfolio or self.portfolio == other.portfolio:
        portfolio = self.portfolio
        futures = [f + o for f, o in zip(self_futures, other_futures)]   # MultipleRiskMeasureFuture.__add__ -> MRMR.__add__
    else:
        portfolio = self.portfolio + other.portfolio                   # unnamed
        futures = self_futures + other_futures
    ret = PortfolioRiskResult(portfolio, set(chain(self.risk_measures, other.risk_measures)), futures)
    if portfolio is not self.portfolio and len(ret.risk_measures) > 1:
        for dest, src in ((self, other), (other, self)):               # fill values for instruments present in both
            for rm in (m for m in src.risk_measures if dest == self or m not in dest.risk_measures):
                set_value(ret, src, rm)                                 # src_result[priceable] lookup; KeyError swallowed
    return ret
else: raise ValueError('Can only add instances of PortfolioRiskResult or int, float')
```

- `first_value(r)`:
  - with more than one measure: `next(iter(r[first(all_instruments)].values()))`;
  - otherwise: `r[first(all_instruments)]`.
- `as_multiple_result_futures`: with exactly one measure, wraps every leaf future as
  `MultipleRiskMeasureFuture(p, {measure: f})`, recursively.
- `_risk_keys_compatible` (181-190) compares `historical_risk_key(key).ex_measure`. `historical_risk_key`
  (markets/markets.py:38-40) sets `date=None` and reduces the market to `LocationOnlyMarket(location)`;
  `ex_measure` drops the measure and normalises `params` (base.py:128-139). The comparison is therefore over
  provider, location, params (csa, raw_results, market_behaviour) and scenario.
- **Same portfolio, same measure, different single dates** → `MRMR.__add__` → `_compose(lhs, rhs)` (109-133):
  - scalar + scalar with different dates → `lhs.compose((lhs, rhs))`, a `SeriesWithInfo` indexed by date;
  - Series + Series → `rhs.combine_first(lhs).sort_index()`;
  - DataFrame + DataFrame → `lhs.loc[set(lhs.index) - set(rhs.index)].append(rhs).sort_index()`. This uses
    `DataFrame.append`, which **pandas 2 removed**, so gs's DataFrame composition is broken on pandas ≥ 2
    (seen in the code, not probed).
- VERIFIED: `p(d0) + p(d1)` gives `dates (d0, d1)`, and `s[s1]` is `SeriesWithInfo {d0: 1.0, d1: 1.5}`.
  `.aggregate()` gives `{d0: 3.0, d1: 4.0}`, and `.to_frame()` is `dates × instrument_name`.
- pricebt (`418-445`) differs in four ways:
  - Its `dates` fallback is `(None,)`, so any two single-date results with overlapping measures and
    instruments raise. VERIFIED: `ValueError('Results overlap…')`.
  - The same-portfolio merge is a lazy `_MultiMeasureFuture` dict union, with no composition.
  - There is no `set_value` fill-in, and no `_risk_keys_compatible` check.
  - The risk-measure order is deterministic (DEV-E14).
  - pricebt's `+ int` raises `ValueError`, while gs raises `RuntimeError`. Both fail.

### 2.5 `to_frame` — full semantics (results.py:850-923, 46-106)

**Records** (`_to_records`, 850-870):
- `get_records(self)` walks `rec.futures` **depth-first in children order**. It collects every leaf result that
  is a `ResultInfo`, `MultipleRiskMeasureResult` or `MultipleScenarioResult`.
- `portfolio_records = self.portfolio._to_records()` is breadth-first (§1.10).
- **Only if the two lengths are equal**, record `i` gets `future_records[i]._to_records({**portfolio_records[i]},
  display_options)`. Otherwise the records are `[]`, and `to_frame` returns `None`.

A leaf's `_to_records(extra, display_options)`:

| Type | Records it produces |
|---|---|
| `FloatWithInfo`/`ScalarWithInfo` | `[{**extra, 'value': self}]` (core.py:176-177) |
| `ErrorValue` | `[{**extra, 'value': self}]` (the error object is the value; 118-119) |
| `UnsupportedValue` | the same, but only if `display_options.show_na` (140-147) |
| `SeriesWithInfo` | one record per date: `{'dates': d, 'value': v, **extra}` (345-350) |
| `DataFrameWithInfo` | `raw_value.to_dict('records')` merged with `extra` (420-431). A date index becomes a `'dates'` column via `raw_value`. An **empty** frame gives `[{**extra, 'value': None}]` only if `show_na`, else `[]`. |
| `MultipleRiskMeasureResult` | each measure's records plus `'risk_measure': rm` (427-433) |

**Frame** (`to_frame`, 872-923):

```python
ori_df = pd.DataFrame.from_records(final_records)            # if no records: return None
if 'risk_measure' not in ori_df.columns: ori_df['risk_measure'] = self.risk_measures[0]
ori_df[cols_except_value] = ori_df[cols_except_value].fillna("N/A")      # shallower leaves / scalar rows next to bucket rows
other_cols = sorted(c for c in cols if 'portfolio' in c) + ['instrument_name', 'risk_measure'] (+ ['dates'] if present)
val_cols = remaining cols with 'value' moved last;  ori_df = ori_df[other_cols + val_cols]
if values is None and index is None and columns is None: return ori_df                       # raw records
elif all three == 'default':
    if 'mkt_type' in cols or 'payment_amount' in cols: return ori_df.set_index(other_cols)  # bucketed / cashflows: NO pivot
    values, index, columns = get_default_pivots('PortfolioRiskResult', has_dates, multi_measures=len(measures) > 1,
                                                simple_port=(max depth of all_paths == 1), multi_scen, ori_cols)
else:
    values = 'value' if values in ('default', ['value']) else values     # index/columns passed through as given
return pivot_to_frame(ori_df, values, index, columns, aggfunc)
```

- In the user-pivot branch, an `index` or `columns` left as the literal string `'default'` is **passed to
  `pivot_table` unchanged**. It is not converted to `None` as in `MultipleRiskMeasureResult.to_frame`
  (results.py:422-424).
- `get_default_pivots('PortfolioRiskResult', …)` (58-68) scans its rules in order, and the first match wins.
  `None` means "any". `PN` stands for the `portfolio_name_*` columns, and `PI = PN + ['instrument_name']`.

| has_dates | multi_measures | simple_port | multi_scen | → (values, index, columns) |
|---|---|---|---|---|
| True | True | – | False | `('value', 'dates', PI + ['risk_measure'])` |
| True | False | – | False | `('value', 'dates', PI)` |
| False | False | **False** | False | `('value', PN, 'instrument_name')` |
| False | – | – | False | `('value', PI, 'risk_measure')` |
| True | True | – | True | `('value', 'dates', PI + ['risk_measure','scenario'])` |
| True | False | – | True | `('value', 'dates', PI + ['scenario'])` |
| False | True | – | True | `('value', PI, ['risk_measure','scenario'])` |
| False | False | – | True | `('value', PI, 'scenario')` |

`pivot_to_frame` (91-106):
- It runs `pivot_table(values, index, columns, aggfunc)`. A `ValueError` becomes `RuntimeError('Unable to
  successfully pivot data')`.
- It then **reindexes rows and columns to first-appearance order**, e.g. `idx = df.set_index(list(pivot.index.names)).index.unique()`.
  A `KeyError` there returns the pivot unchanged.
- pricebt's bare `pivot_table` sorts labels instead. VERIFIED: `10y` comes before `5y` in pricebt, while gs
  keeps `5y`, `10y`.

VERIFIED gs outputs (fabricated values):

| Case | Default `to_frame()` |
|---|---|
| flat, 1 scalar measure | index `instrument_name`, column `Price`, rows in portfolio order |
| flat, duplicate names | the values are **summed** (`aggfunc='sum'`). `aggfunc='mean'` averages them. (030009 cells 10-11) |
| flat, (Price, DollarPrice) | index `instrument_name`, columns `[Price, DollarPrice]` |
| flat, (Price, IRDelta bucketed) | **no pivot**: MultiIndex `(instrument_name, risk_measure)` with columns `mkt_type…mkt_point, value`. The Price rows have `'N/A'` in the `mkt_*` columns. |
| nested (homogeneous levels), 1 measure | index `portfolio_name_0`, columns `instrument_name` (030006's heat map) |
| nested, (Price, DollarPrice) | index `(portfolio_name_0, instrument_name)`, columns the measures |
| historical flat Price | index `dates`, columns `instrument_name` |
| historical nested Price | index `dates`, columns MultiIndex `(portfolio_name_0, instrument_name)` |
| historical (Price, DollarPrice) | index `dates`, columns `(instrument_name, risk_measure)` |
| historical bucketed | `set_index(['instrument_name','risk_measure','dates'])`, one row per (date, bucket) |
| `to_frame(None, None, None)` | raw records with columns `[portfolio_name_*…, instrument_name, risk_measure, (dates), …, value]` |
| unnamed leaves | `instrument_name` = `Swap_0`, `Swap_1` |
| empty bucketed result | The row is **dropped** by default. With `display_options=DisplayOptions(show_na=True)` the value becomes `None` → the sum gives **0.0**. (030009 cells 13-14) |
| explicit `(values='value', index='instrument_name', columns='risk_measure')` on a **historical** PRR | **sums across dates** (`2.5`, `4.5`). This is what backtest_objects.py:317 relies on, because it calls with single-date PRRs. |
| explicit same, on a bucketed PRR | sums the buckets per instrument (`3.0`, `7.0`) |

pricebt `to_frame` (`491-511`):
- It builds records only for **direct children**. It sums a DataFrame's `value` column and a Series' values.
- Its defaults are always `index='instrument_name'`, `columns='risk_measure'`.
- It has no `display_options`, no `portfolio_name_*`, no `dates`, no `N/A` fill, and no first-appearance
  reindex. An empty frame shows `0.0` by default.
- So:
  - the explicit backtest call matches gs for flat single-date results;
  - every default shape except "flat scalar single-date" differs;
  - nested results produce **tuples in cells** (VERIFIED).

### 2.6 `MultipleRiskMeasureResult(dict)` (results.py:289-433 (2.1.17: 290-434))

| Member | gs semantics | gs line | pricebt (`156-160`) | Status |
|---|---|---|---|---|
| `__init__(instrument, dict_values)` | a dict plus a private `__instrument` | 290-292 | `MultipleRiskMeasureResult(dict_values)`; **no instrument argument** | P (signature) |
| `instrument` | the instrument (or the **portfolio**, when built by `PRR.aggregate`/`transform` with more than one measure) | 380-382 | — | M |
| `__getitem__(item)` | A date or iterable of dates: if all values are DataFrame/Series → `MRMR(inst, (k, _value_for_date(v, item)))`; if all are MultipleScenarioResult → per value; else `ValueError('Can only index by date on historical results')`. A Scenario → a scenario slice. Otherwise a plain dict lookup. VERIFIED: `mh[D1]` → `{Price: 1.5, DollarPrice: 2.5}`. | 294-313 | dict lookup only | M (date) |
| `dates` | The sorted union of the values' date indices, for DataFrame/Series values only. | 384-392 | — | M |
| `__mul__(other)` | int/float → `__op(op.mul, other)`. Otherwise it **returns** a `ValueError`. `__op` (363-378): for a Series/DataFrameWithInfo, `copy_with_resultinfo()` and then `new.value = op(value.value, operand)`, which **requires a `value` column**, so a historical `SeriesWithInfo` raises `AttributeError` (VERIFIED, BUG-R4). A scalar gives `op(v, k)`. | 315-319, 363-378 | — | M |
| `__add__(other)` | int/float → `__op(op.add, …)`. An MRMR: requires `_risk_keys_compatible`, else `ValueError('Results must have matching scenario and location')`. It raises `ValueError('Results overlap on risk measures, instruments or dates')` when the keys overlap **and** the instruments are equal **and** the dates overlap (the dates are `risk_key.date` for a scalar). **Different instruments → a `PortfolioRiskResult`** over `Portfolio((i1, i2))`. The same instrument → a per-key `_compose`. Anything else → `ValueError('Can only add instances of MultipleRiskMeasureResult or int, float')`. VERIFIED: `mr + MRMR(s1, {DollarPrice: …})` has keys `[Price, IRDelta, DollarPrice]`. | 321-361 | — | M |
| `__sub__` | not defined (VERIFIED `TypeError`) | — | — | parity |
| `to_frame(values, index, columns, aggfunc, display_options)` | Records get `risk_measure`. `(None, None, None)` → raw. Defaults: if `mkt_type` is present → `df.set_index('risk_measure')`; else the pivots `values='value', columns='risk_measure', index='dates'` (or `None`). VERIFIED: scalars give a 1-row frame with index `value` and columns `[Price, DollarPrice]`; historical gives `dates × measures`. | 401-425 | — | M |
| `transform(t)` | **not in gs** (a pricebt addition, DESIGN §8.2) | — | `159-160` | pricebt extra |
| `_multi_scen_key` | scenarios | 394-399 | — | N/A |

### 2.7 `*WithInfo` values and the `risk.core` helpers (core.py)

| Member | gs semantics (1.5.4 line / 2.1.17 line) | pricebt | Status |
|---|---|---|---|
| `ResultInfo` | properties `risk_key`, `unit` ("The units of this result"), `error` ("Any error associated with this result") and `request_id`; `composition_info(components)` (72-100/73-101) raises `ValueError('Cannot compose results with different markets')` when locations differ, and collects `ErrorValue`s into an `errors` dict by date | plain attributes; no `request_id`; no `composition_info` | P |
| `FloatWithInfo(risk_key, value, unit=None, error=None, request_id=None)` | **Positional order `(risk_key, value)`.** `raw_value` → float. `__repr__`: the error string if `error` is set, else `1.0 (USD)`, with the unit dict rendered `A*B/C^2` (198-230/207-239). `__add__`: with another FloatWithInfo of **equal unit** → `FloatWithInfo(combine_risk_key(a,b), a+b, unit)`, unequal → `ValueError('FloatWithInfo unit mismatch')`; with a plain number → **a plain `float`** (232-240/241-249). `__mul__`: with a FloatWithInfo → combined key; otherwise **keeps `risk_key`/`unit`** (242-248/251-257). `to_frame()` → `self` (250-251/259-260). VERIFIED types: `a+b` FloatWithInfo, `a-b` float, `a*2` FloatWithInfo, `2*a` float, `a/2` float, `-a` float, `sum([a,b])` float. | `FloatWithInfo(value, risk_key=, unit=, error=)` (**different positional order**). `__add__` keeps the left `risk_key` (no `combine_risk_key`) and returns FloatWithInfo even for `+ float`. `__radd__ = __add__`, so `sum` gives FloatWithInfo. `a*2` → **float**. No `to_frame`. The repr shows only the first unit key. | P |
| `StringWithInfo`, `DictWithInfo` | scalars for string or dict results | — | M (low value) |
| `SeriesWithInfo(pd.Series, ResultInfo)` | `raw_value` → `pd.Series(self)`. `compose(components)` → `SeriesWithInfo(pd.Series(index=DatetimeIndex(dates).date, data=values), risk_key=historical_risk_key(first), unit, error=errors)` (339-343/348-356). `_to_records` has `dates`/`value` columns. `__mul__` keeps the metadata. `copy_with_resultinfo`. Other pandas ops return a `SeriesWithInfo` with **`risk_key=None`** (VERIFIED: `sa - sb`). | `_metadata` + `__finalize__`, so **pricebt keeps the left operand's metadata** on every op (VERIFIED). No `compose`, `_to_records` or `copy_with_resultinfo`. | P (pricebt keeps more) |
| `DataFrameWithInfo(pd.DataFrame, ResultInfo)` | `raw_value`: `pd.DataFrame(self)` if empty; else a copy, and **if `index.values[0]` is a `dt.date` the index is renamed `'dates'` and reset into a column** (400-408/413-421). `compose` → `pd.concat(v.assign(date=d) …).set_index('date')` (410-415/423-433). `to_frame()` → `self`. `filter_by_coord(MarketDataCoordinate)` keeps the rows matching each non-None coordinate field (a `str` compares for equality; otherwise `isin`) (442-452/457-467). pandas ops return `risk_key=None` (VERIFIED: `df - df`). | `raw_value` = `pd.DataFrame(self)` (no date handling). Six fixed columns (DESIGN §8.2). No `compose`, `to_frame` or `filter_by_coord`. Ops keep the metadata (VERIFIED). | P |
| `ErrorValue(risk_key, error, request_id=None)` | `raw_value` → None. `repr` → the error. `__getattr__` raises `AttributeError(f'ErrorValue object has no attribute {item}.  Error was {error}')`. `_to_records` → `[{…, 'value': self}]`. | `(risk_key, error)`; no `__getattr__`, no `_to_records` | P |
| `UnsupportedValue(risk_key)` | `raw_value` → `'Unsupported Value'`; hidden in `to_frame` unless `show_na` | — | M (low) |
| `aggregate_risk(results, threshold=None, allow_heterogeneous_types=False)` | Docstring: "Combine the results of multiple InstrumentBase.calc() calls, into a single result". Takes the `raw_value` of each result (a Future is `.result()`-ed first; with `allow_heterogeneous_types` a Series becomes `DataFrame(series.raw_value).T`), then `pd.concat(dfs).fillna(0)`, then `groupby([c for c in cols if c != 'value'], as_index=False).sum()`, then an optional `abs(value) > threshold` filter, then `sort_risk` (501-549/516-564). Historical frames group on `'dates'` as well. | `combine_bucketed_frames` (`142-153`): the same idea, but `sort=False`, fixed columns, and first-appearance order (DEV-R5) | P (not exported under the gs name) |
| `aggregate_results(results, allow_mismatch_risk_keys=False, allow_heterogeneous_types=False)` | Empty → `None`. An `Exception` → `raise Exception`. `result.error` → `ValueError('Cannot aggregate results in error')`. Mixed types → `ValueError(f'Cannot aggregate heterogeneous types: {type(r)} vs {type(r0)}')` unless allowed. Different non-empty units → `ValueError(f'Cannot aggregate results with different units for {risk_measure}')`. Keys whose `ex_historical_diddle` differs (**the date differs**, or the measure, or the params) → `ValueError('Cannot aggregate results with different pricing keys')` unless allowed. Then: dict → per-key recursion; tuple → `tuple(set(chain(...)))`; Float → `FloatWithInfo(risk_key, sum(results), unit)`; **Series → `SeriesWithInfo(sum(results), risk_key, unit)`** (index-aligned; a date missing on one side gives NaN); DataFrame → `DataFrameWithInfo(aggregate_risk(results, …))` (555-602/570-617). VERIFIED messages. | `aggregate` → `_aggregate_scalars`/`combine_bucketed_frames`. No error, key or type checks. **A Series gives a `KeyError`.** | P (P0) |
| `subtract_risk(left, right)` | Docstring: "Subtract bucketed risk. Dimensions must be identical". `assert left.columns.names == right.columns.names; assert 'value' in left.columns.names` → **always an `AssertionError`** for normal frames, because `columns.names == [None]` (VERIFIED, BUG-R5). The intended behaviour is `aggregate_risk((left, -right))` (605-633/620-648). | — | M (port the intent) |
| `sort_values` / `sort_risk(df, by=('date','time','mkt_type','mkt_asset','mkt_class','mkt_point'))` | Docstring: "Sort bucketed risk". Sorts `label1`/`mkt_point`/`point` by `point_sort_order` (tenor-aware; VERIFIED `3m, 2y, 10y`) and puts the `by` columns first. A `'date'` column becomes the index (636-665/651-680). | not ported (DEV-R5: no point parsing) | DEV-R5 |
| `combine_risk_key(k1, k2)` | Field-wise: a field is kept if equal, else `None` (668-686/683-701) | — | M |
| `RiskKey` | a namedtuple plus the `ex_measure` and `ex_historical_diddle` properties (base.py:127-157) | a plain namedtuple (`risk/results.py:21`); no `ex_*` | P |

### 2.8 `risk/transform.py`

| Member | gs | pricebt | Status |
|---|---|---|---|
| `Transformer(ABC, Generic)` with the abstract `apply(data, *args, **kwargs)` | transform.py:28-31 | `risk/transform.py:14-18` (raises `NotImplementedError`, not ABC) | S |
| `GenericResultWithInfoTransformer(fn)`: `apply` → `fn(data, *args, **kwargs)` | 34-39 | — | M (trivial) |
| `ResultWithInfoAggregator(risk_col='value', filter_coord=None)` (a `dataclass_json` dataclass) | 42-76. A float passes through. `FloatWithInfo` → `raw_value`. `SeriesWithInfo` → **`getattr(result, risk_col).sum()`**, i.e. `series.value`, which raises `AttributeError` on a plain date-indexed Series (a gs quirk). `DataFrameWithInfo`: empty → `0`, `filter_coord` → `filter_by_coord(coord)`, then `.sum()`. Anything else → `ValueError(f'Aggregation of {type} not currently supported.')`. Returns a **list** of `FloatWithInfo(risk_key, val, unit, error)`. | `21-49`: `filter_coord` is a `{column: value}` dict, not a `MarketDataCoordinate`. A Series is summed directly. An unknown type becomes `float(r)` instead of a `ValueError`. | P (documented) |

### 2.9 Futures

| Class | gs semantics | pricebt |
|---|---|---|
| `PricingFuture(Future)` (206-256) | `result()` raises `RuntimeError('Cannot evaluate results under the same pricing context being used to produce them')` when it is not done inside an entered context. `__add__`: a number or a PricingFuture → `PricingFuture(_compose(self.result(), operand))`, and **`_compose` has no number branch, so `+ number` raises `RuntimeError(f'{lhs} and {rhs} cannot be composed')`** (VERIFIED). `__mul__`: a number → `PricingFuture(result() * k)`. | Completed-only (`163-176`); no `__add__`/`__mul__` |
| `CompositeResultFuture` (259-286) | a tuple of futures; `__getitem__` → `result()[item]`; `futures` | — (PRR holds `futures` directly) |
| `MultipleRiskMeasureFuture(instrument, measures_to_futures)` (436-455) | results in `MultipleRiskMeasureResult(instrument, zip(keys, results))`; `__add__` → an MRMR sum re-wrapped; `measures_to_futures` | `_MultiMeasureFuture` (`234-254`): lazy, no instrument, `future_for(measure)` |
| `HistoricalPricingFuture` (530-545) | Composes the per-date results: `base.compose(results)`, or per key for an MRMR. If every date failed, it sets the first error. | built eagerly in `assets/pricing.py:535-566` |

---

## 3. Notebook inventory — every Portfolio/PRR/result call (`documentation/03_portfolios`)

Cells are referenced as `nb cell N` (the index in `cells`, counting markdown cells). Status is for pricebt
today.

| Notebook | Calls (cell) | pricebt status |
|---|---|---|
| 030000 create_portfolio | `IRSwaption(PayReceive.Pay,'5y',Currency.EUR,expiration_date=,name=)` (2); `Portfolio((s1, s2))` (3) | S |
| 030001 modify_instruments | `Portfolio((s1,s2))`, `.instruments` (3); **`portfolio.pricables = (…)` (4): a typo, which sets an unrelated attribute; the next `print(portfolio.instruments)` is unchanged** | S; the typo is a silent no-op in both libraries (pricebt `Portfolio` is a plain class) |
| 030002 extracting_instruments_and_results | `PricingContext(pricing_date=)` + `IRSwaption(...)`, `trade2.resolve()` (3); `Portfolio((t1,t2))` (3); `portfolio[1]`, `portfolio[trade2]`, `portfolio['receiver']` (4); `portfolio.calc((IRVega, IRDelta, Price))` (6); `results[0]` → MRMR (7); `results[Price]['receiver']`, `results[Price][1]`, `results[Price][trade2]`, `results['receiver'][Price]` (8); `a + b` of FloatWithInfo and `results[Price].aggregate()` (9); `pd.DataFrame(results[Price])` (10); `results[IRDelta]['payer']` (11); `.value.sum()` (12); `results[IRDelta][0].value[1]`, `results[IRDelta].aggregate()` (13); `HistoricalPricingContext(d1, d2)` + `portfolio.calc(...)` (14); `hist_results[Price][trade1]` → `SeriesWithInfo` (15); **`hist_results[Price].aggregate()`** (16); **`.aggregate().at[date]`** (17); **`hist_results[IRDelta][0]`** → a date-indexed concatenated frame (18) | Cells 3-15: S. **16-17: raise `KeyError 'mkt_type'`. 18: different shape** (a Series of DataFrames). |
| 030003 resolve_portfolio | `Portfolio((s1,s2)).resolve()`, `portfolio['EUR-3m5y'].as_dict()` (3); `strike='atm+50'` | S (as_dict has `quantity_`, DEV-I2); the strike parsing is the config's job (DEV-I6) |
| 030004 price_portfolio | `portfolio.price()`, `.aggregate()` (3); `price_result['EUR-3m5y']` (4) | S |
| 030005 calculate_portfolio_risk | `portfolio.calc((risk.DollarPrice, risk.IRDelta))` (2); `result[IRDelta].aggregate()` (3); `result[DollarPrice]['EUR-3m5y']`, `result['EUR-3m5y'][DollarPrice]` (4) | S |
| 030006 portfolio_grid_calc | `Portfolio([Portfolio([IRSwaption(..., name=e) for e in expiries], name=t) for t in tails])`, `.calc(IRAnnualImpliedVol)` (3); **`results.to_frame('value', 'portfolio_name_0', 'instrument_name') * 10000`** (4); seaborn heatmap (5) | calc S. **`to_frame` → `KeyError 'portfolio_name_0'`.** The levels are homogeneous, so gs labels this one correctly. |
| 030007 pnl_explain | `Portfolio((swap, swaption)).resolve()` (2); `business_day_offset`, `CloseMarket`, `close_market_date`, **`PnlExplain(CloseMarket(date=to_date))`** (4); `with PricingContext(pricing_date=from_date): portfolio.calc((DollarPrice, explain))`; **`PricingContext(pricing_date=from_date, market=CloseMarket(date=to_date))`** + `portfolio.dollar_price()`; `to_price.aggregate() - to_market_price.aggregate()` (a float); `result[DollarPrice].aggregate()`; `result[explain].aggregate().value.sum()`; `explain_all[explain_all.value.abs() > 1.0].round(0)` | `business_day_offset` S. **`CloseMarket`, `close_market_date` and `PnlExplain` are M.** `market=` is accepted and **ignored**, which gives a silent wrong answer. The arithmetic is S. |
| 030008 portfolio_from_frame | `Portfolio.from_frame(data, mappings=mapper)` with str and lambda mappings, `type` mapped to `'Swap'/'Swaption'` plus an `asset_class` column (7); `portfolio.to_frame().reset_index(drop=True)` (7); `Portfolio.from_csv(...)` (commented out, 9) | **`from_frame`/`from_csv` M**; `to_frame` S |
| 030009 portfolio_risk_result_to_frame | `Portfolio([eur_port, usd_port, swaption_1])`, with duplicate names across sub-portfolios (2); `eur_port.price()`, `nested_port.price()`, `nested_port.calc(risk.IRVegaParallel)` (3); **`to_frame()`** (5); **`to_frame(values=None, columns=None, index=None)`** (6); **`to_frame(values='value', columns='portfolio_name_0', index='instrument_name')`** (7); a same-name `to_frame()` sum (10); `to_frame(aggfunc='mean')` (11); `nested_port_vega.to_frame()` (13); **`to_frame(display_options=DisplayOptions(show_na=True))`** (14), with `from gs_quant.config import DisplayOptions` | Flat cells 10-11 S. **Nested cells 5-7 and 13 give wrong shapes or `KeyError`. Cell 14 raises `TypeError` (no kwarg), and `DisplayOptions` is M.** Note: this portfolio **mixes leaves and sub-portfolios at the top level, so gs's own output for cells 5-7 and 13-14 is mislabelled** (BUG-R1). |
| 030010 portfolio_inter_leg_dependencies | `IRSwaption(..., strike="=[foo].strike + 5bp", name="bar")` (3); `port.resolve()` (4); `port['foo'].strike * 1e4` (5) | Portfolio S. The cross-leg `=[name].field` reference is resolved server-side in gs; for pricebt it is DEV-I6 (the config decides), and the config's `resolve` sees one instrument at a time (§4.3 injected vars), so **cross-leg references are impossible today**. |
| 030011 portfolio_from_csv | `IRSwap?` (2); mappers using `IRSwap.type_.value` and `IRSwap.asset_class.value` (4); **`Portfolio.from_csv('my_excel_portfolio.csv', mappers)`**, `p.resolve()` (5); `p[0].as_dict()` (6) | **`from_csv` M.** pricebt has class attributes `IRSwap.type_`/`IRSwap.asset_class`; whether `.value` works was not checked. |
| tutorials/Portfolios (== developer.gs.com …/pricing-and-risk/portfolios) | `Portfolio((s1,s2))` (7); **`portfolio.pricables = (s1, s2)` (9)**: the doc text says "Portfolio constituents can also be set (or reset) by assiging to instruments", but the code is the same typo, a no-op; `portfolio[0]`, `portfolio[swaption2]`, `portfolio['EUR-5y6m']` (11); `portfolio.resolve()`, `portfolio.price().aggregate()` (13); `portfolio.calc((risk.DollarPrice, risk.IRDelta))` (14); `result[DollarPrice]['EUR-5y3m']`, `result['EUR-5y3m'][DollarPrice]` (16); `result[DollarPrice].aggregate()`, `result[IRDelta].aggregate()` (18) | S (flat). The typo: parity. |
| tutorials/Create New Portfolio | `PortfolioManager`, `Portfolio.save`, `PositionSet`/`Position`, `update_positions`, `set_entitlements`, `share`, `schedule_reports`, `get_performance_report`, `CustomAUMDataPoint`, `RiskAumSource` | SRV |
| tutorials/Pull Portfolio Factor Risk Data | `PortfolioManager`, `FactorRiskReport`, `GsRiskModelApi`, `get_risk_model_id` | SRV |
| tutorials/Pull Portfolio Performance Data | `PortfolioManager`, `PerformanceReport` | SRV |
| tutorials/Pull Portfolio Risk Data | `PortfolioManager`, `PerformanceReport`, `FactorRiskReport` | SRV |
| tutorials/Update Historical Portfolio | `GsPortfolioApi`, `GsAssetApi`, `PositionSet`, `Position`, `run_reports` | SRV |

The same API is used by the backtest views (2.1.17), which R03 already covers:
- `results[risk].aggregate(True, True)` (backtest_objects.py:213);
- `risk_res.to_frame(values='value', index='instrument_name', columns='risk_measure')`, with
  `.assign(pricing_date=[date] * len(risk_res))` (317-319; this relies on one row per direct child);
- `info.portfolio.to_frame()` (334);
- `backtest.results[d][p.risk].transform(...).aggregate(allow_mismatch_risk_keys=True)` (generic_engine.py:503-507);
- `sp.results[d][sp.risk][inst.name]` + `hasattr(inst_risk, 'aggregate')` (557-560).

---

## 4. Prioritized gaps for backtesting and P&L decomposition (with the exact gs semantics to implement)

**P0: breaks documented usage, or gives silently wrong numbers**

1. **Historical result shapes**, which a P&L time series depends on.
   - `aggregate_results` with a Series must return `SeriesWithInfo(sum(results), risk_key=first.risk_key,
     unit)` (core.py:597-598), summed with index alignment. Keep gs's unit and error checks.
   - A historical bucketed value should be one `DataFrameWithInfo`, as in gs `compose` (core.py:410-415):
     ```python
     df = pd.concat(v.assign(date=d) for d, v in zip(dates, frames)).set_index('date')
     DataFrameWithInfo(df, risk_key=historical_risk_key(first), unit=unit, error=errors_by_date)
     ```
   - `raw_value` must move a date index to a `'dates'` column (core.py:400-408).
   - Date slicing is `_value_for_date` (results.py:136-167):
     ```python
     raw = result.loc[[date]] if isinstance(result, DataFrameWithInfo) and isinstance(date, dt.date) else result.loc[date]
     # DataFrame: raw.raw_value.set_index('dates'); single date -> reset_index(drop=True)
     # risk_key = RiskKey(provider, date or tuple(dates), CloseMarket(date, loc), params, scenario, measure)
     ```
   - Aggregation groups by `['dates', mkt_*]` (VERIFIED output: the columns `mkt_type..mkt_point, dates, value`).
   - A default `to_frame()` on a historical result uses the §2.5 pivot rules (index `dates`).
   - pricebt changes: `_historical_instrument_value` (`assets/pricing.py:535-566`), `aggregate` (`risk/results.py:471-488`),
     `_series_item` (`346-359`), and `to_frame`. **DESIGN §8.2 needs a line for historical shapes; today it only
     says "historical scalar results are indexed by date".**
2. **`PortfolioPath`, `Portfolio.all_paths`, and `Portfolio.paths()` returning paths.** These are the keystone.
   Port results.py:548-590 and portfolio.py:466-501 verbatim. The path order must be kept: own matches first,
   then the sub-portfolios'. Then re-implement:
   - PRR `__getitem__` rows 4-6 (§2.3): nested str/instrument returns the **first** match; list of
     instruments → `subset`; 1-element list; slice → `subset`.
   - `__iter__` over leaves in `all_paths` order;
   - `Portfolio.subset` and `PRR.subset`;
   - `__contains__`.

   This touches pricebt `Portfolio.paths` (`portfolio.py:103-124`), which today returns objects. Its three
   callers (`__getitem__`, `__contains__`, `pop`) are all inside `portfolio.py` and must switch to
   `self[path]`.
3. **`PRR.to_frame` with gs's full semantics**, and **correct labels** (DEV for BUG-R1).
   - Records are depth-first in `futures` order, **each carrying the labels of its own path**:
     `portfolio_name_{k}` = the k-th ancestor's name (or `Portfolio_{idx}`), and `instrument_name` = the
     leaf's name (or `{type_}_{idx}`).
   - Then `risk_measure` (added if missing), the `N/A` fill, and the column order.
   - The `(None, None, None)` raw form.
   - The default pivots from the §2.5 rule table. The bucketed/cashflow branch uses `set_index(other_cols)`.
   - A user pivot keeps `'value'` normalisation.
   - `pivot_to_frame`'s first-appearance reindex.
   - `display_options` with a `show_na` flag. An empty frame is dropped unless `show_na`.
   - `None` when there are no records.
   - `ErrorValue` rows carry the error object as `value`.
   - Must keep: the explicit `(values='value', index='instrument_name', columns='risk_measure')` backtest call,
     where bucket rows are summed by the pivot.
4. **`PRR.__add__` date composition.**
   - For a single-date PRR, use `first_value(...).risk_key.date` as its date (results.py:745-747). With the
     same portfolio, merge per measure with `_compose` (§2.4), so that `Σ_d p(d)` stitches a historical PRR.
   - With different portfolios and more than one measure, run gs's `set_value` fill-in.
   - Keep DEV-E14's ordered measures.
   - Add `_risk_keys_compatible` once `RiskKey` has an `ex_measure` equivalent. In pricebt the key is
     `(date, measure)` plus the csa, so the compatible key is the csa.
5. **`aggregate()` error contract and flags.**
   - Error → `ValueError('Cannot aggregate results in error')`.
   - Heterogeneous types → gs's typed message unless `allow_heterogeneous_types`, which converts Series to a
     1-row frame.
   - Unit mismatch → gs's message.
   - Different pricing keys → `ValueError` unless `allow_mismatch_risk_keys`.
   - Today pricebt silently sums across dates. VERIFIED: `3.0`, where gs raises.
   - The lazy group path must apply the same checks to the group metadata: the `group_key` date, the unit and
     the measure.
6. **`Portfolio.calc(fn=...)` per child** (portfolio.py:594; instrument/core.py:202-213). pricebt applies `fn`
   to the whole PRR. `Instrument.calc` already applies it per instrument (`pricing.py:609-611`), so the
   portfolio path should call the instrument path per leaf. An exception inside `fn` is stored in that leaf's
   future (gs `ret.set_exception(e)`).
7. **`PricingContext(market=...)`** is accepted and ignored (DESIGN §6.5). For P&L explain this silently prices
   on the wrong market. Recommendation (a design question): raise `NotSupportedError` when `market` is not
   None, or define a pricebt "market date ≠ pricing date" override. That would be a DEV and would need an
   injected `pricebt_market_date` in configs. It is the minimum needed to port 030007's "to-market,
   from-date" leg.

**P1: needed for P&L decomposition workflows beyond one flat book**

8. **`MultipleRiskMeasureResult` API.** It needs:
   - the `instrument` argument and property, which `MRMR.__add__` uses to choose between returning a PRR and
     composing;
   - date and date-list indexing, and `dates`;
   - `to_frame`;
   - `__add__` (`_compose`, overlap and compatible-key errors);
   - scalar `__mul__`/`__add__`, fixing BUG-R4 so that a Series is multiplied directly.

   Constructor parity: gs is `MRMR(instrument, dict_values)`, and pricebt code builds `MRMR(pairs)` in
   `assets/pricing.py:507,557` and `risk/results.py:249,382,452,473`.
9. **Scalar arithmetic parity.**
   - `FloatWithInfo.__mul__` should keep the metadata. gs's `aggregate() * first_scale_factor` path is
     generic_engine_action_impls.py:179.
   - `FloatWithInfo.to_frame()` → self; `DataFrameWithInfo.to_frame()` → self.
   - Decide whether to keep pricebt's `__radd__`, which returns FloatWithInfo from `sum()` where gs returns a
     float. It is harmless, but it is a difference; document it.
   - gs `FloatWithInfo(risk_key, value, …)` positional order vs pricebt `(value, risk_key=…)`: any user code
     that constructs values positionally breaks. Either match gs or add a DEV. pricebt's own call sites pass
     the value first, positionally (e.g. `assets/pricing.py:457`, `risk/results.py:278,359`,
     `risk/transform.py:42,44`), so switching to gs order touches each of them.
10. **Bucketed differencing for risk-based P&L.**
    - Port `subtract_risk` with its intent, `aggregate_risk((left, -right))`, fixing BUG-R5.
    - Export `aggregate_risk(results, threshold=None, allow_heterogeneous_types=False)` under the gs name,
      implemented by `combine_bucketed_frames`, DEV-R5 ordering, and an optional threshold.
    - Add `combine_risk_key` for the `+` of FloatWithInfo.
    - Together with (1), these let a user compute `Δ(ladder)` between dates, `ladder(d-1) · Δrates(d)`, and so
      on. For a DataFrame with the same bucket index, pandas `-` already keeps the metadata in pricebt
      (VERIFIED).
11. **`PRR.__mul__(k)`** (results.py:699-703): scale every leaf. Mirror the fact that gs returns a `ValueError`
    rather than raising it; a DEV may make it raise.
12. **`PRR.transform` on nested results** (DEV for BUG-R2): transform per leaf, keeping the structure. Also
    `GenericResultWithInfoTransformer`.

**P2: parity and convenience**

13. `Portfolio.from_frame`/`from_csv`/`to_csv` (§1.6), and `Grid` (§1.9).
14. `PRR.__repr__`, `Portfolio.__repr__` with the instrument count, `ErrorValue.__getattr__`,
    `UnsupportedValue`, `StringWithInfo`, `DictWithInfo`, `DisplayOptions` (`gs_quant.config`).
15. `__getitem__` with a list of risk measures; `PRR.get` with a required `default` (pricebt's `None` default
    is a harmless superset).
16. Stubs raising `NotSupportedError` ("GS server-side …") for:
    - `Portfolio.get`, `from_*`, `save*`, `id` and `quote_id` (returning `None` is fine), `market()`;
    - `PnlExplain`, `CloseMarket`, `close_market_date`, `PortfolioManager`.

    If no stubs are added, these names stay absent, which is the current behaviour. Decide per DESIGN §9.3.

---

## 5. gs bugs pricebt should NOT port (each needs a DEV id if pricebt deviates)

| Proposed id | gs behaviour (VERIFIED unless noted) | Evidence | Proposed pricebt behaviour |
|---|---|---|---|
| DEV-R6 (BUG-R1) | `PRR.to_frame`/`_to_records` zip **depth-first** leaf results (results.py:851-869) against `Portfolio._to_records` records, which are **breadth-first** and assembled with `records.pop(0)` (portfolio.py:93-108). The labels come out wrong whenever a level mixes leaves and sub-portfolios. `Portfolio([eur, usd, s5])` gives `N/A/7y=1.0, EUR/5y=2.0, …, USD/10y=5.0`, but the true values are 7y=5, EUR 5y=1, …. `Portfolio([s5, eur, usd])` gives `USD/N/A=5.0, N/A/5y=1.0 …`. A 3-level tree loses the top-level leaf entirely and keeps a label-only placeholder row. | probe output §2.5; gs's own 030009 cell 2 shape | Labels are derived from each leaf's own path, depth-first in `futures` order. |
| DEV-R7 (BUG-R2) | `PRR.transform` with one measure applies over the `all_paths` leaves and rebuilds `PRR(self.portfolio, …, flat_futures)`. On a nested portfolio, `len(futures)` = the leaf count while `portfolio` keeps its nested children. `t['7y']` returned `2.0` (the right value is `5.0`). | results.py:826-834 | Transform the leaves and rebuild the same tree, one future per direct child, with sub-PRRs for sub-portfolios. |
| DEV-P1 (BUG-P1) | `Portfolio.all_portfolios` never descends: `portfolios = list(unique_everseen(stack))`, and then every popped item `in portfolios` → `continue`. So `__contains__` sees depth ≤ 1 only (`'deep' in top` → False while `top['deep']` finds it). | portfolio.py:208-222, 123-129 | Keep pricebt's recursive `all_portfolios` and `__contains__` (already the case; needs a DEV marker). Consider gs's `unique_everseen` de-duplication. |
| DEV-R8 (BUG-R3) | `PRR + number` → `PricingFuture.__add__` → `_compose(value, number)` → `RuntimeError('… cannot be composed')`. The int/float branch of `PRR.__add__` and `MRMR.__add__`'s scalar branch are only reachable through MRMR. | results.py:737-738, 220-228, 109-133 | Either implement the scalar add per leaf, or raise `ValueError('Can only add instances of PortfolioRiskResult')` (pricebt today). Record which. |
| DEV-R9 (BUG-R4) | `MRMR * k` on a `SeriesWithInfo` value → `AttributeError: 'SeriesWithInfo' object has no attribute 'value'` (`__op` assumes a `value` column). Also, `PRR.__mul__`/`MRMR.__mul__` with a non-number **return** a `ValueError` object instead of raising it. | results.py:363-378, 319, 703 | Multiply a Series directly, and raise on a non-number. |
| DEV-R10 (BUG-R5) | `subtract_risk` asserts `'value' in left.columns.names`, so it always fails (`AssertionError`). | core.py:627-628 | `aggregate_risk((left, right * -1))`, with identical bucket columns required. |
| (quirk, document) | Three different leaf orders: `len(prr)` counts direct children, `iter(prr)` is `all_paths` breadth-first (direct leaves first), and `to_frame` is depth-first in futures order. `prr['name']` returns the first path match, while `Portfolio['name']` returns every match. | §2.2-2.3 | Keep `len` and `iter` as in gs. With DEV-R6, `to_frame` is depth-first. Document it. |
| (quirk, document) | `_compose` for DataFrames uses `DataFrame.append`, which pandas 2 removed, so composing historical bucketed results via `+` fails on pandas ≥ 2. Read in the code, not probed. | results.py:128 | Use `pd.concat`. |
| (quirk, document) | `ResultWithInfoAggregator` on a `SeriesWithInfo` reads `series.value`, which raises on a date-indexed Series. | transform.py:59-60 | pricebt already sums the Series (keep it, document it). |
| (doc bug) | The 030001 cell 4 and Portfolios tutorial cell 9 `portfolio.pricables = …` typo is a silent no-op. | notebooks | Parity; mention it in pricebt docs. |

---

## 6. 1.5.4 vs 2.1.17 behavioural differences (everything else is typing or imports)

1. `Portfolio.from_frame` row filter:
   - 1.5.4 keeps a row iff `any(v for v in r.values if v is not None)`, i.e. some value is **truthy**, so a row
     whose only non-null values are `0`, `''` or `False` is dropped (portfolio.py:377);
   - 2.1.17 uses `data[data.notnull().any(axis=1)]`, so any non-null value keeps the row (2.1.17
     portfolio.py:382-383).
2. `risk/core.py` `compose` (Unsupported, Scalar, Series) keeps a `DatetimeIndex` when the component dates are
   `datetime`s, instead of `.date`. `DataFrameWithInfo.compose` indexes by `timestamp` in that case (2.1.17
   core.py:137-142, 177-182, 350-355, 426-428). This is irrelevant to pricebt's date-only engine.
3. `priceable.market()` builds its dict via `apply(process_row, axis=1)`, with the same result (2.1.17
   priceable.py:114-124).

---

## 7. Not verified / open

- `Portfolio.resolve(in_place=False)` under HPC when a child fails: gs logs `Error resolving on {date},
  skipping that date` and **omits the date** (portfolio.py:523-526). pricebt behaviour was not probed (probably
  it raises).
- gs `Instrument.__eq__` includes `name` (a dataclass field), which affects `paths()` and `pop()`. It was not
  re-verified here; R04 §3 covers it.
- 030011: whether `pricebt.instrument.IRSwap.type_.value` and `.asset_class.value` exist as enum values was not
  checked.
- `DataFrameWithInfo` composition via `+` on pandas 2 (`DataFrame.append`): read in the code, not run.
- Design questions for the IR-risk work (facts above; decisions not taken here):
  - (a) Should historical bucketed values follow gs's date-indexed long frame? This would change DESIGN §8.2's
    "exactly six columns" contract for historical values: a `date` index, and `raw_value` adds `dates`.
  - (b) How should a P&L explain be expressed in configs: a portfolio function
    `explain(trades, weights, market_from, market_to)`, or a pricing-date/market-date split in
    `PricingContext`?
  - (c) Should DEV-R6/R7 (the fixed labels and transform) be the default, given that gs's own 030009 output is
    mislabelled?
