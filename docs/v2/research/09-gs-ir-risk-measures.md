# 09 — gs_quant interest-rate risk measures: catalogue, parameters, units, result shapes, pricebt gaps

> **Key facts** (each one is backed by a citation in the body)
> 1. **The server picks the result handler, not the measure.** `result_handlers` is keyed by the payload's `$type` (gs154 `risk/result_handlers.py:493-516`). One measure can come back in different shapes depending on its parameters: `IRDelta` → `RiskVector` (6-column ladder); `IRDelta(aggregation_level=Type)` → `Risk` (a `FloatWithInfo`); `IRDelta(aggregation_level=Asset)` → `RiskByClass` (a `mkt_type, mkt_asset, value` frame). Evidence: recorded server payloads `cc:e114a9f5`, `cc:7dde6877` and `cc:5711e73d`.
> 2. **Two existing notes are wrong on this.** R03 §14.2 says `IRDelta(aggregation_level='Type')` "comes back as a small DataFrame, not a float". It comes back as a float: `$type: Risk`, `cc:7dde6877`, and gs's own test `test_measures.py:57`. R04 §9 and R03 §14.2 say `IRFwdRate` is "in percent". The declared `unit` is `Percent`, but the **value is a decimal rate**: `0.013165…` with server unit `{"Rate": 1.0}` (`cc:21cb074a`). `IRAnnualImpliedVol` is likewise a **decimal normal vol**: `0.00605` with unit `{"Rate Normal Volatility": 1.0}` (`cc:6f8d6b28`). The Measures tutorial multiplies it by `10000` (cell 13).
> 3. **The order of the vega-cube point is `tail;expiry`, not `expiry;tenor`.** In `cc:fa39f59c`, `IRSwaption('Pay','5y','USD', expiration_date='1y')` (gs2117 `test/risk/test_results.py:123`) has all its vega at `mkt_point "5Y;1Y"`, the 3m-expiry swaption at `"5Y;3M"` and the 6m one at `"5Y;6M"`. The row is `mkt_type 'IR VOL'`, `mkt_asset 'USD-LIBOR-BBA'`, `mkt_class 'SWAPTION'`.
> 4. **Server unit dicts use full currency names plus a `Basis Point` power.** Examples: `{"United States Dollar": 1.0}`, `{"European Euro": 1.0}`, `{"British Pound Sterling": 1.0}`, `{"Malaysian Ringgit": 1.0}`, and `{"Basis Point": -1.0, "European Euro": 1.0}` for an aggregated IRDelta. `RiskVector` and `RiskByClass` frames carry **no** `.unit`, because the handlers never pass one. These are not the ISO-code dicts in DESIGN §4.2.
> 5. **`IRDelta` / `IRVega` with no `currency` are in USD, per +1bp.** For IRDelta the bump is on the curve instruments; for IRVega it is +1bp **absolute normal vol**. The docs reproduce `IRDeltaParallel` as `(PV(+1bp) − PV(−1bp))/2` on `MarketDataPattern('IR', USD)`, and `IRVegaParallel` the same way on `'IR Vol'` with `±1e-4` absolute shocks (`doc:010204#cell4-5`, `doc:010205#cell4-5`). Every client-side FD parameter defaults to `None`. The **server** supplies bump size and method, and the docs describe that default as "a 2-sided 1bp bump".
> 6. **`risk_by_class_handler` only collapses a result to a float by measure *name*.** `IRBasisParallel`, `IRDeltaParallel`, `IRVegaParallel` and `PnlExplain` become `FloatWithInfo(sum)`, and only when there are ≤2 rows **of a single `mkt_type`** (`result_handlers.py:174-178`). So `IRDelta(aggregation_level=Asset)` is a frame, while the preset `IRDeltaParallel` with the same parameters is a float. `IRVegaParallel` on a plain swap is an **empty DataFrame**, not `0.0` (verified live). `PnlExplain` also folds every `*SPIKE*`/`*JUMP*` row into the `CROSSES` row.
> 7. **1.5.4 and 2.1.17 differ on 8 measures**:
>    - `IRVanna` and `IRVolga` are plain `RiskMeasure` in 1.5.4 (not callable: `TypeError`) and `RiskMeasureWithFiniteDifferenceParameter` in 2.1.17. The gs notebook `23_solve_vanna_&_volga` calls `IRVanna(aggregation_level=Type)`, so it only runs on 2.1.17.
>    - Six measures are 2.1.17-only: `EqForwardSpot` and `FX{Delta,DeltaHedge,Gamma,Theta,Vega}LocalCcy`.
>    - Everything else in the risk surface is byte-identical, apart from type hints: `target/measures.py` (apart from those 8 measures), `risk/measures.py`, `common.py`, `FiniteDifferenceParameter`, `AggregationLevel` and `FiniteDifferenceMethod`.
> 8. **gs has latent bugs that pricebt currently does not replicate.**
>    - Re-calling a parameterised currency measure raises `AttributeError: 'CurrencyParameter' object has no attribute 'currency'` in both versions. Examples: `Price(currency='EUR')()` and `Price(currency='EUR')(name='X')`. pricebt returns `X(value:EUR)`.
>    - `RiskMeasureWithDoubleParameter(...)()` raises `NameError: DoubleParameter`.
>    - `ListOfNumber`/`String` re-calls raise `AttributeError`.
>    - `subtract_risk` **always** fails its own `assert 'value' in left.columns.names` on a standard risk frame (verified, pandas 2.3.1).
>    - `aggregate_risk` over frames with different column sets raises `TypeError` in `sort_values`.
> 9. **Bonds have no bond-analytics measures** (no yield, duration, convexity, clean/dirty price, accrued). Beyond `Price`/`DollarPrice`/`Cashflows` there are only `LightningDV01` (`measure_type 'DV01'`) and `LightningOAS` (`'OAS'`), whose docstrings are just their names. gs `Bond` is `asset_class=Cross_Asset`, `type_=Bond`, identifier-based (gs154 `target/instrument.py:81-90`).
> 10. **Coverage.** pricebt has 32 measure instances (`pricebt-ir/src/pricebt/risk/__init__.py:229-262`).
>     - IR-relevant measures missing: **34**, plus 5 adjacent ones (§8.1).
>     - Parameter classes missing: 5 of 7 `RiskMeasureWith*` classes and 5 of 7 `*Parameter` classes.
>     - Enums/classes missing: `RiskMeasureType`, `FiniteDifferenceMethod`, `MktMarkingOptions`.
>     - The snapshot parity test only compares `class`, `name` and `str(measure_type)` (`tests/test_gs_api_parity.py:326-329`). Adding any measure fails `test_no_unexpected_extra_risk_measures` until `RISK_MEASURE_NAMES` in `tools/gs_api_snapshot.py:117-124` is extended and the snapshot is regenerated with the base python.

---

## 0. Sources, citation keys, method

| key | meaning |
|---|---|
| `gs154:` | gs_quant **1.5.4** (API reference), `C:\Users\chris\anaconda3\Lib\site-packages\gs_quant\` |
| `gs2117:` | gs_quant **2.1.17** source checkout (behaviour reference), `C:\Users\chris\clee\gsquant-temp-claude\gs-quant\gs_quant\` |
| `cc:<hash8>` | a **recorded real server response** in `gs2117:test/calc_cache/request<hash>….json`: `mocked_data` is the raw JSON the risk service returned, `request_id` names instruments + measures, `tests` names the gs test that recorded it. Structure: `[date][measure][position][…]` |
| `doc:<nb>#cellN` | gs documentation notebook under `gs2117:documentation/`, `cells[N]` (0-based) |
| `pb:` | this worktree, `C:\Users\chris\clee\gsquant-temp-claude\pricebt-ir\` |

Method:
- **Read** every file listed in the task.
- **Introspected** the measure namespace of both versions with the base python. This is read-only: it opens no session and makes no network calls. 1.5.4 was imported directly. 2.1.17 was imported via `sys.path` pointing at the checkout.
- **Exercised** the result handlers, `aggregate_risk`, `subtract_risk`, `sort_risk` and the edge cases of parameterised calls on synthetic payloads.
- **Mined** all 155 `calc_cache` recordings for `$type`, unit dicts and coordinate labels.
- **Scanned** every documentation notebook for uses of each measure.
- **Could not read** `https://developer.gs.com/docs/gsquant/pricing-and-risk/portfolios/`: it returned HTTP 403. `docs/risk.rst` only autodocs `aggregate_risk`, `subtract_risk`, `sort_risk` and 33 measures (`gs2117:../docs/risk.rst:9-47`), and adds no prose.

Legend for "evidence" columns: **REC** = recorded server payload; **DOC** = gs notebook or docstring; **SRC** = gs source; **VER** = verified by running gs code; **INF** = inferred, not verified.

---

## 1. Class machinery

### 1.1 `RiskMeasure`
- **Base dataclass.** `gs154:target/common.py:6879-6888` (2.1.17: `:6890-6899`) is `@dataclass(unsafe_hash=True, repr=False)`. Its fields, all `init=True`, all default `None`, all taking part in `==`/`hash`, are `asset_class: AssetClass`, `measure_type: RiskMeasureType`, `unit: RiskMeasureUnit`, `parameters: RiskMeasureParameter`, `value: float|str`, `name: str`. VER: `dataclasses.fields(RiskMeasure)`.
- **Public subclass** `gs154:common.py:61-83`, identical in 2.1.17:
  - `__lt__` (62-74): "if self.name != other.name: return self.name < other.name / elif self.parameters is not None: if other.parameters is None: return False; if not isinstance(other.parameters, type(self.parameters)): return self.parameters.parameter_type < other.parameters.parameter_type else: return repr(self.parameters) < repr(other.parameters) / elif other.parameters is not None: return True / return False".
  - `__repr__` (76-77): `return self.name or self.measure_type.name`. The fallback is the **enum member name** (e.g. `Dollar_Price`), not the value.
  - `pricing_context` property (79-83): returns `PricingContext.current`. `Instrument.calc` routes every measure through `curr_measure.pricing_context.calc(self, curr_measure)` (`gs2117:instrument/core.py:168-174`). This is the hook PnlExplain uses (§6).
- **Equality ignores anything that is not a field.** VER: `PnlExplain(CloseMarket(date=d1)) == PnlExplain(CloseMarket(date=d2))` is **True**, with equal hashes, because `to_market` is a private attribute rather than a dataclass field.

### 1.2 `ParameterisedRiskMeasure` (`gs154:common.py:86-116`)
- `__init__(asset_class, measure_type, unit, value, parameters, name)`. A non-`RiskMeasureParameter` `parameters` raises `TypeError(f"Unsupported parameter {parameters}")` (97-101).
- `__repr__` (103-113): `name(k:v, …)`. It drops `parameter_type`, sorts keys case-insensitively and prints an enum's `.value`. VER examples:
  - `IRDelta(aggregation_level:Type, currency:USD)`
  - `Price(value:EUR)`
  - `IRDeltaParallel(aggregation_level:Asset)`
  - `IRXccyDeltaParallel(aggregation_level:Type)`
  - `InflationDeltaParallel(aggregation_level:Type)`

  A parameter that is itself a `Base` (e.g. `MktMarkingOptions`) prints as its default object repr: `IRDelta(mkt_marking_options:<gs_quant.target.common.MktMarkingOptions object at 0x…>)` (VER).
- `parameter_is_empty()` (115-116): `return self.parameters is None`.

### 1.3 The seven callable `RiskMeasureWith*` classes (`gs154:target/measures.py`, same lines in 2.1.17)

Every `__call__` does the same things:
- returns `self` if the first argument is a `pd.Series`/`pd.DataFrame` (the pandas `.loc` hack);
- clones with `copy.copy`;
- sets `clone.name = name` if one is given;
- inherits each unset argument from the old parameters;
- **replaces** `parameters` with a fresh parameter object.

| class | lines | `__call__` signature | builds | property | IR-relevant users | gs defect (VER) |
|---|---|---|---|---|---|---|
| `RiskMeasureWithCurrencyParameter` | 26-46 | `(currency=None, name=None)` | `CurrencyParameter(value=currency)` | `.currency → parameters.value` | `Price`, `PricePips`, `Annuity`, `FairPremium` | inherit step reads `clone.parameters.currency` (42), but the field is `value`, so **`Price(currency='EUR')()` and `Price(currency='EUR')(name='X')` raise `AttributeError`** in 1.5.4 **and** 2.1.17 |
| `RiskMeasureWithDoubleParameter` | 49-69 | `(double=None, name=None)` | `DoubleParameter(value=double)` | `.double` | none | `DoubleParameter` is **not imported** in `target/measures.py:20-21`, so any call raises `NameError` (both versions) |
| `RiskMeasureWithListOfNumberParameter` | 72-92 | `(list_of_number=None, name=None)` | `ListOfNumberParameter(values=…)` | `.list_of_number → parameters.values` | none | re-call reads `.list_of_number`, which raises `AttributeError` |
| `RiskMeasureWithListOfStringParameter` | 95-115 | `(list_of_string=None, name=None)` | `ListOfStringParameter(values=…)` | `.list_of_string` | none | same pattern (`.list_of_string`) |
| `RiskMeasureWithMapParameter` | 118-138 | `(map=None, name=None)` | `MapParameter(value=map)` | `.map` | none | same pattern (`.map`) |
| `RiskMeasureWithStringParameter` | 141-161 | `(string=None, name=None)` | `StringParameter(value=string)` | `.string` | none | re-call raises `AttributeError` (VER) |
| `RiskMeasureWithFiniteDifferenceParameter` | 164-220 | `(aggregation_level=None, bump_size=None, currency=None, finite_difference_method=None, local_curve=None, mkt_marking_options=None, scale_factor=None, name=None)` | `FiniteDifferenceParameter(...)` | 7 properties (165-191) | `IRDelta`, `IRBasis`, `IRVega`, `IRXccyDelta`, `InflationDelta` (+ `IRVanna`, `IRVolga` in 2.1.17) | works; re-call inherits correctly (`IRDelta(currency='USD')(bump_size=2)` → `IRDelta(bump_size:2, currency:USD)`, VER) |

**Coercion (VER).**
- String arguments are coerced to enums by gs `Base` field typing:
  - `IRDelta(aggregation_level='Type') == IRDelta(aggregation_level=AggregationLevel.Type)` (equal hash too);
  - `finite_difference_method='Centered'` is stored as `FiniteDifferenceMethod.Centered`.
- Invalid strings raise:
  - `ValueError: 'Bogus' is not a valid AggregationLevel`;
  - `ValueError: 'Sideways' is not a valid FiniteDifferenceMethod`.
- `Price(currency=Currency.EUR)` stores the `Currency` enum member and reprs as `Price(value:EUR)`.
- `Price(currency=None)` gives `parameters=CurrencyParameter()`, repr `Price`, and **`!= Price`**.

### 1.4 Parameter dataclasses

All are `RiskMeasureParameter` subclasses (`gs154:base.py:565-574`; `gs2117:base.py:566`). All are `@dataclass(unsafe_hash=True, repr=False, order=True)`. Field order is below; `parameter_type` is `init=False`; every class **has a `name` field**. Parameter `__repr__` = `f"{parameter_type}({k:v,…})"` (`base.py:566-574`), e.g. `FiniteDifference(aggregation_level:Asset)`.

| class | `gs154:target/common.py` (2.1.17) | fields (name, init, default) | `parameter_type` |
|---|---|---|---|
| `CurrencyParameter` | 5068 (5081) | `value: str=None`, `parameter_type` (no init), `name=None` | `'Currency'` |
| `DoubleParameter` | 5106 (5118) | `value: float=None`, `parameter_type`, `name` | `'Double'` |
| `ListOfNumberParameter` | 5161 (5173) | `values: Tuple[float,…]=None`, `parameter_type`, `name` | `'ListOfNumber'` |
| `ListOfStringParameter` | 5170 (5182) | `values: Tuple[str,…]=None`, `parameter_type`, `name` | `'ListOfString'` |
| `MapParameter` | 5179 (5191) | `value: DictBase=None`, `parameter_type`, `name` | `'Map'` |
| `StringParameter` | 5386 (5407) | `value: str=None`, `parameter_type`, `name` | `'String'` |
| `FiniteDifferenceParameter` | 5783 (5789) | `aggregation_level: AggregationLevel=None`, `currency: str=None`, `local_curve: bool=None`, `bump_size: float=None`, `finite_difference_method: FiniteDifferenceMethod=None`, `scale_factor: float=None`, `mkt_marking_options: MktMarkingOptions=None`, `parameter_type` (no init), `name=None` | `'FiniteDifference'` |
| `MktMarkingOptions` (a plain `Base`, not a parameter) | 5210 (5222) | `mode: str=None`, `name=None` | n/a |

**`FiniteDifferenceParameter` defaults and semantics.**
- Every field defaults to `None` client-side. There is no default bump size, method or scale in gs code: the server fills them in.
- The only documented description is the table in `doc:02_pricing_and_risk/00_instruments_and_measures/tutorials/Measures.ipynb#cell23`, quoted verbatim:

  | Parameter name | Type | Description |
  |---|---|---|
  | currency | string | Currency of risk result |
  | aggregation_level | gs_quant.target.common.AggregationLevel | Level of aggregate shift |
  | local_curve | bool | Change in Price (present value in the denominated currency) |
  | finite_difference_method | gs_quant.target.common.FiniteDifferenceParameter | Direction and dimension of finite difference |
  | mkt_marking_options | gs_quant.target.common.MktMarkingOptions | Market marking mode |
  | bump_size | float | Bump size |
  | scale_factor | float | Scale factor |

- **Server default (DOC, INF):** `doc:…/02_market_shock/010204_delta_shock_equivalent.ipynb#cell2`: "Risk measures are short-hand for a set of predefined scenarios … We calculate delta as a 2-sided 1bp bump." Cells 4-5 compute `(up − down)/2` with `MarketDataShock(Absolute, ±1/10000)` and expect it to equal `IRDeltaParallel`. So the default is Centered, 1bp, and the result is reported per 1bp. The units of `bump_size` (bp or decimal) are **not documented anywhere**.
- `currency` accepts an ISO code or the literal `'local'` (the instrument's own currency). See `IRDeltaLocalCcy` and `IRVegaLocalCcy` (`risk/measures.py:82,85`) and `doc:Measures.ipynb#cell17,25`. `cell25` comments on `IRDelta(aggregation_level=Type, currency='local')` as "using an aggregate 1bp shift on the asset level and using change in Price only in the denominated currency".

### 1.5 Enums (identical in 1.5.4 and 2.1.17 except `RiskMeasureType`)
- **`AggregationLevel`** (`target/common.py:45-52`): `Type='Type'`, `Asset='Asset'`, `Class='Class'`, `Point='Point'`. Docstring: "Aggregation Level". Result shapes are in §3.3.
- **`FiniteDifferenceMethod`** (1.5.4 `:4016`, 2.1.17 `:4004`), docstring "Direction and dimension of finite difference": `Up`, `Centered`, `Down`, `CenteredSecondOrder`.
- **`RiskMeasureUnit`** (1.5.4 `:4645`, 2.1.17 `:4648`), docstring "The unit of change of underlying in the risk computation.": `Percent`, `Dollar`, `BPS`, `Pips`.
- **`AssetClass`** (`:55`): 17 members. pricebt matches (`pb:src/pricebt/common.py:174-193`).
- **`RiskMeasureType`** (1.5.4 `:4526-4642`, 130 members; 2.1.17 `:4524`, +6 FX LocalCcy members, listed in the introduction diff).
  - IR-relevant members **used by a predefined measure**:
    - `PV`, `Dollar_Price`, `Price`, `Theta`, `Cashflows`, `Market_Data`, `Market_Data_Assets`, `Market`, `Resolved_Instrument_Values`, `Description`;
    - `Delta`, `Gamma`, `Vega`, `Vanna`, `Volga`, `Basis`, `XccyDelta`, `InflationDelta`;
    - `ParallelGamma`, `ParallelGammaLocalCcy`, `ParallelDiscountDelta`, `ParallelDiscountDeltaLocalCcy`;
    - `Forward_Rate`, `Spot_Rate`, `Annual_Implied_Volatility`, `Annual_ATMF_Implied_Volatility`, `Daily_Implied_Volatility`, `Spread`, `AnnuityLocalCcy`, `Local_Currency_Accrual_in_Cents`, `Premium_In_Cents`, `FairPremium`, `Forward_Price`, `ExpiryInYears`, `Probability_Of_Exercise`, `Compounded_Fixed_Rate`;
    - `DV01`, `OAS`, `CRIF_IRCurve`, `BaseCPI`, `FinalCPI`, `Inflation_Compounding_Period`, `Inflation_Delta_in_Bps`, `PnlExplain`, `PnlPredict`.
  - IR-relevant members **with no predefined measure object** (a user can only reach them with `RiskMeasure(measure_type=…)`):
    - `DeltaLocalCcy`, `GammaLocalCcy`, `VegaLocalCcy`, `Local_Currency_Annuity`, `PnlExplainLocalCcy`;
    - `ParallelBasis`, `ParallelDelta`, `ParallelDeltaLocalCcy`, `ParallelVega`, `ParallelVegaLocalCcy`, `ParallelXccyDelta`, `ParallelXccyDeltaLocalCcy`, `ParallelInflationDelta`, `ParallelInflationDeltaLocalCcy`, `ParallelIndexDelta`, `ParallelIndexDeltaLocalCcy`;
    - `MV`, `PNL`, `Strike`, `Spot`.
  - The `*Parallel` presets do **not** use the `Parallel*` measure types. `IRDeltaParallel` is `measure_type Delta` plus `aggregation_level=Asset`.
  - `str(RiskMeasureType.X)` is the value (`'Delta'`, `'PV'`, VER). The snapshot relies on this.

---

## 2. Measure catalogue

### 2.1 Identity table

Columns: **class** (`RMWCur` = WithCurrencyParameter, `RMWFD` = WithFiniteDifferenceParameter, `RM` = plain, not callable), **asset_class**, **measure_type** (value), **unit** (declared `RiskMeasureUnit`), **gs docstring** (verbatim `__doc__`), **lines** (`target/measures.py` 1.5.4 / 2.1.17 unless noted), **pb** (line in `pb:src/pricebt/risk/__init__.py`, or ✗), **snap** (present as `gs_quant.risk.<name>` in `pb:tests/data/gs_api_1_5_4.json`).

**Generic (no asset class)**

| name | class | asset_class | measure_type | unit | gs docstring | lines | pb | snap |
|---|---|---|---|---|---|---|---|---|
| `Price` | RMWCur | – | `PV` | – | "Present Value" | 529 / 547 | 229 | ✓ |
| `DollarPrice` | RM | – | `Dollar Price` | – | "Price of the instrument in US Dollars" | 310 / 310 | 250 | ✓ |
| `PricePips` | RMWCur | – | `Price` | Pips | "Present value in pips" | 532 / 550 | ✗ | ✗ |
| `ForwardPrice` | RM | – | `Forward Price` | **BPS** | " Price of the instrument at expiry in the local currency" (leading space in source) | 430 / 448 | ✗ | ✗ |
| `FairPremium` | RMWCur | – | `FairPremium` | – | "Fair Premium is the instrument present value discounted to the premium settlement date" | 415 / 433 | ✗ | ✗ |
| `Theta` | RM | – | `Theta` | – | "Theta" | 550 / 568 | ✗ | ✗ |
| `ExpiryInYears` | RM | – | `ExpiryInYears` | – | "Time to Expiry expressed in fractional Years." | 331 / 334 | ✗ | ✗ |
| `ProbabilityOfExercise` | RM | – | `Probability Of Exercise` | – | "Probability Of Exercise" | 535 / 553 | ✗ | ✗ |
| `CompoundedFixedRate` | RM | – | `Compounded Fixed Rate` | – | "CompoundedFixedRate" | 298 / 298 | ✗ | ✗ |
| `Cashflows` | RM | – | `Cashflows` | – | "Cashflows" | 283 / 283 | 261 | ✓ |
| `MarketData` | RM | – | `Market Data` | – | "Market Data" | 505 / 523 | ✗ | ✗ |
| `MarketDataAssets` | RM | – | `Market Data Assets` | – | "MarketDataAssets" | 508 / 526 | ✗ | ✗ |
| `Market` | RM | – | `Market` | – | "Market" | 502 / 520 | ✗ | ✗ |
| `ResolvedInstrumentValues` | RM | – | `Resolved Instrument Values` | – | "Resolved InstrumentBase Values" | 547 / 565 | 262 | ✓ |
| `Description` | RM | – | `Description` | – | "Description" | 307 / 307 | ✗ | ✗ |
| `LightningDV01` | RM | – | `DV01` | – | "LightningDV01" | 493 / 511 | ✗ | ✗ |
| `LightningOAS` | RM | – | `OAS` | – | "LightningOAS" | 496 / 514 | ✗ | ✗ |
| `CRIFIRCurve` | RM | – | `CRIF IRCurve` | – | "CRIF IR Curve" | 280 / 280 | ✗ | ✗ |
| `BaseCPI` | RM | – | `BaseCPI` | – | "Base CPI" | 226 / 226 | ✗ | ✗ |
| `CrossMultiplier` | RM | – | `Cross Multiplier` | – | "CrossMultiplier" | 304 / 304 | ✗ | ✗ |

**Rates levels, rates and annuities (`asset_class Rates`)**

| name | class | measure_type | unit | gs docstring | lines | pb | snap |
|---|---|---|---|---|---|---|---|
| `IRFwdRate` | RM | `Forward Rate` | Percent | "Interest rate par rate (in percent)" | 454 / 472 | 253 | ✓ |
| `IRSpotRate` | RM | `Spot Rate` | Percent | "Interest rate at-the-money spot rate (in percent)" | 466 / 484 | 254 | ✓ |
| `IRAnnualImpliedVol` | RM | `Annual Implied Volatility` | Percent | "Interest rate annual implied volatility (in percent)" | 436 / 454 | 256 | ✓ |
| `IRAnnualATMImpliedVol` | RM | `Annual ATMF Implied Volatility` (note ATM**F**; FX uses `Annual ATM Implied Volatility`) | Percent | "Interest rate annual implied at-the-money volatility (in percent)" | 433 / 451 | ✗ | ✗ |
| `IRDailyImpliedVol` | RM | `Daily Implied Volatility` | BPS | "Interest rate daily implied volatility (in basis points)" | 442 / 460 | 255 | ✓ |
| `ParSpread` | RM | `Spread` | – | "Par Spread" | 520 / 538 | ✗ | ✗ |
| `Annuity` | RMWCur | `AnnuityLocalCcy` | – | "Annuity" | 223 / 223 | 233 | ✓ |
| `LocalAnnuityInCents` | RM | `Local Currency Accrual in Cents` | – | "Local Currency Accrual in Cents" | 499 / 517 | ✗ | ✗ |
| `PremiumCents` | RM | `Premium In Cents` | – | "PremiumCents" | 523 / 541 | ✗ | ✗ |

**Rates first-order sensitivities**

| name | class | measure_type | preset parameters | gs docstring | lines | pb | snap |
|---|---|---|---|---|---|---|---|
| `IRDelta` | RMWFD | `Delta` | – | "Change in Dollar Price (USD present value) due to individual 1bp moves in the interest rate instruments used to build the underlying discount curve" | 445 / 463 | 238 | ✓ |
| `IRDeltaParallel` | RMWFD | `Delta` | `aggregation_level=Asset`, name `'IRDeltaParallel'` | (inherits IRDelta's) | `risk/measures.py:81` | 239 | ✓ |
| `IRDeltaLocalCcy` | RMWFD | `Delta` | `currency='local'` | (inherits) | `risk/measures.py:82` | 240 | ✓ |
| `IRBasis` | RMWFD | `Basis` | – | "Change in Dollar Price (USD present value) due to individual 1bp moves in the interest rate instruments used to build the basis curve(s)" | 439 / 457 | 244 | ✓ |
| `IRBasisParallel` | RMWFD | `Basis` | `aggregation_level=Asset` | (inherits) | `risk/measures.py:79` | ✗ | ✗ |
| `IRXccyDelta` | RMWFD | `XccyDelta` | – | "Change in Price due to 1bp move in cross currency rates." | 478 / 496 | 245 | ✓ |
| `IRXccyDeltaParallel` | RMWFD | `XccyDelta` | `aggregation_level=`**`Type`** | (inherits) | `risk/measures.py:83` | ✗ | ✗ |
| `IRVega` | RMWFD | `Vega` | – | "Change in Dollar Price (USD present value) due to individual 1bp moves in the implied volatility (IRAnnualImpliedVol) of instruments used to build the volatility surface" | 472 / 490 | 241 | ✓ |
| `IRVegaParallel` | RMWFD | `Vega` | `aggregation_level=Asset` | (inherits) | `risk/measures.py:84` | 242 | ✓ |
| `IRVegaLocalCcy` | RMWFD | `Vega` | `currency='local'` | (inherits) | `risk/measures.py:85` | 243 | ✓ |
| `InflationDelta` | RMWFD | `InflationDelta` | – | "Change in Price due to 1bp move in inflation curve." | 490 / 508 | 246 | ✓ |
| `InflationDeltaParallel` | RMWFD | `InflationDelta` | `aggregation_level=`**`Type`** | (inherits) | `risk/measures.py:80` | ✗ | ✗ |
| `IRDiscountDeltaParallel` | RM | `ParallelDiscountDelta` | – | "Parallel Discount Delta" | 448 / 466 | ✗ | ✗ |
| `IRDiscountDeltaParallelLocalCcy` | RM | `ParallelDiscountDeltaLocalCcy` | – | "Parallel Discount Delta (Local Ccy)" | 451 / 469 | ✗ | ✗ |
| `InflDeltaParallelLocalCcyInBps` | RM | `Inflation Delta in Bps` | – | "Inflation Delta" | 481 / 499 | ✗ | ✗ |

**Rates second-order sensitivities**

| name | class 1.5.4 / 2.1.17 | measure_type | gs docstring | lines | pb | snap |
|---|---|---|---|---|---|---|
| `IRGamma` | RM / RM | `Gamma` | "IRGamma" | 457 / 475 | 251 | ✓ |
| `IRGammaParallel` | RM / RM | `ParallelGamma` | "Change in aggregated IRDelta for a aggregated 1bp shift in the interest rate instruments used to build the underlying discount curve" | 460 / 478 | 252 | ✓ |
| `IRGammaParallelLocalCcy` | RM / RM | `ParallelGammaLocalCcy` | "Interest Rate Parallel Gamma (Local Ccy)" | 463 / 481 | ✗ | ✗ |
| `IRVanna` | **RM / RMWFD** | `Vanna` | "Interest Rate Vanna (USD)" | 469 / 487 | ✗ | ✗ |
| `IRVolga` | **RM / RMWFD** | `Volga` | "Interest Rate Volga (USD)" | 475 / 493 | ✗ | ✗ |

**Inflation information (Rates)**

| name | class | measure_type | gs docstring | lines | pb | snap |
|---|---|---|---|---|---|---|
| `InflMaturityCPI` | RM | `FinalCPI` | "InflMaturityCPI" | 484 / 502 | ✗ | ✗ |
| `Infl_CompPeriod` | RM | `Inflation Compounding Period` | "Infl_CompPeriod" | 487 / 505 | ✗ | ✗ |

**Relative (P&L) measures.** These are **classes**, not instances (`gs154:risk/measures.py`, identical in 2.1.17):

| name | lines | constructor | `name` / `measure_type` | docstring |
|---|---|---|---|---|
| `__RelativeRiskMeasure` (private base, subclass of `RiskMeasure`) | 26-45 | `(to_market: Market, asset_class=None, measure_type=None, unit=None, value=None, name=None)` | – | – |
| `PnlExplain` | 48-52 | `PnlExplain(to_market: Market)` | `'PnlExplain'` / `RiskMeasureType.PnlExplain` | "Pnl Explained" |
| `PnlExplainClose` | 55-59 | `PnlExplainClose()` → `to_market=CloseMarket()` | same | (none) |
| `PnlExplainLive` | 62-66 | `PnlExplainLive()` → `to_market=LiveMarket()` | same | (none) |
| `PnlPredictLive` | 69-75 | `PnlPredictLive()` → `to_market=LiveMarket()` | `'PnlPredict'` / `RiskMeasureType.PnlPredict` | "Pnl Predicted" |

**Out of scope but adjacent.** These are Rates or FX measures pricebt does not need for IR.
- `FairPremiumInPercent` (FX, Percent, 418/436).
- The FX and Eq measures. pricebt already carries FXDelta/FXGamma/FXVega/FXSpot/FXAnnualImpliedVol/FX*LocalCcy/Eq*.
- `FairVarStrike`, `FairVolStrike`.
- Credit `CD*` and Commod measures.

### 2.2 The 2.1.17 `docs/risk.rst` Measures list (autodoc order)
`DollarPrice, Price, ForwardPrice, Theta, BaseCPI, CommodDelta, CommodTheta, CommodVega, EqDelta, EqGamma, EqVega, EqSpot, EqAnnualImpliedVol, FairVarStrike, FairVolStrike, FXDelta, FXGamma, FXVega, FXSpot, FXAnnualImpliedVol, FXAnnualATMImpliedVol, InflationDelta, IRBasis, IRDelta, IRXccyDelta, IRGammaParallel, IRGammaParallelLocalCcy, IRVega, IRAnnualImpliedVol, IRAnnualATMImpliedVol, IRDailyImpliedVol, IRSpotRate, IRFwdRate` (`gs2117:../docs/risk.rst:15-47`).

### 2.3 Discrepancies between the Measures tutorial and the code
In `doc:…/tutorials/Measures.ipynb#cell2`, `*` marks a parameterised measure.
- It lists **`IRGamma*`** as parameterised, with the definition "Change in aggregated IRDelta for a aggregated 1bp shift in the interest rate instruments used to build the underlying discount curve". In code, `IRGamma` is a plain `RiskMeasure` (not callable) in both versions, with docstring "IRGamma". That definition text is `IRGammaParallel`'s docstring.
- It lists `Annuity*` and `Price*` as parameterised: "Annuity of instrument in local currency (unless currency specified in parameters)" and "Price of the instrument (In local currency unless currency specified in parameters)".
- `FairPremium`: "Fair Premium to be paid on the premium settlement date (in the premium currency) such that the present value would be zero".
- `ForwardPrice`: "Price of the instrument at expiry in the local currency".
- `IRAnnualImpliedVol` / `IRAnnualATMImpliedVol` are "(in percent)" and `IRDailyImpliedVol` is "(in basis points)". **§4 shows the recorded values are decimals.**

---

## 3. Result handlers (how a server payload becomes a Python value)

### 3.1 Dispatch table (`gs154:risk/result_handlers.py:493-516`; 2.1.17 `:494-517`)
Keyed by the payload's `$type`. Every handler has the signature `(result: dict, risk_key: RiskKey, instrument, request_id=None)`.

| `$type` | handler (1.5.4 line / 2.1.17) | returns | shape / columns |
|---|---|---|---|
| `Risk` | `risk_handler` 151-165 / 152 | `FloatWithInfo(risk_key, result['val'], unit=result['unit'])`; **if `result['children']` is non-empty** → `DataFrameWithInfo` with columns `path, value` (a `'parent'` row for `val`, then one row per child key, i.e. leg valuations) | scalar with unit |
| `RiskVector` | `risk_vector_handler` 206-225 / 207 | `DataFrameWithInfo`. Special case: if there is 1 value and `risk_key.risk_measure.name.startswith('Eq')` → `FloatWithInfo` | columns drawn **in this order** from those present in the first row: `mkt_type`←`type`, `mkt_asset`←`asset`, `mkt_class`←`class_`, `mkt_point`←`point`, `mkt_quoting_style`←`quoteStyle`, `value` (paired from the `asset` value array). Rows sorted by `sort_values` over all columns (`mkt_point` by `point_sort_order`). **No `.unit` is set**, even when the payload has one (`cc:0b8e0fc3` carries `unit`) |
| `RiskByClass` | `risk_by_class_handler` 168-203 / 169 | a name whitelist `['IRBasisParallel','IRDeltaParallel','IRVegaParallel','PnlExplain']` (174). `if str(risk_key.risk_measure.name) in whitelist and len(types) <= 2 and len(set(types)) == 1` → `FloatWithInfo(sum(values), unit=result.get('unit'))`. Otherwise → frame | frame columns `mkt_type`←`type`, `mkt_asset`←`asset`, `value`. Rows whose `type` contains `SPIKE` or `JUMP` are **dropped and their value added to the `CROSSES` row** (183-196). For a `PnlExplain` instance the frame is **unsorted** (`__dataframe_handler_unsorted`, 200-201). Otherwise it is sorted, with columns taken from the **first row's** keys |
| `RiskSecondOrderVector` | `mdapi_second_order_table_handler` 320-351 / 321 | if there is exactly 1 value **and** `asset_class == Rates` **and** `measure_type in (ParallelGamma, ParallelGammaLocalCcy)` (`__is_single_row_2nd_order_risk`, 311-317) → `FloatWithInfo(values[0])` (via `risk_float_handler`, **no unit**). Otherwise → frame | `inner_mkt_type, inner_mkt_asset, inner_mkt_class, inner_mkt_point, inner_mkt_quoting_style, outer_mkt_type, outer_mkt_asset, outer_mkt_class, outer_mkt_point, outer_mkt_quoting_style, value, permissions`. List points are `';'`-joined. Mismatched inner/outer lengths raise `Exception("Found inner and outer points of different size")` |
| `RiskTheta` | `risk_float_handler` 293-296 / 294 | `FloatWithInfo(risk_key, result['values'][0])`, **no unit** | scalar |
| `IRPCashflowTable` | `cashflows_handler` 82-102 / 83 | `DataFrameWithInfo`, **unsorted**, server row order | 14 columns (§5.3) |
| `LegDefinition` | `leg_definition_handler` 116-119 | `instrument.resolved(result, risk_key)`, i.e. an **Instrument** | – |
| `MDAPITable` | `mdapi_table_handler` 354-376 / 355 | `DataFrameWithInfo`, sorted | `mkt_type`←`type`, `mkt_asset`←`asset`, `mkt_class`←**`assetClass`**, `mkt_point` (`';'`-joined), `mkt_quoting_style`←**`quotingStyle`**, `value`, `permissions` |
| `RequireAssets` | `required_assets_handler` 138-142 | frame `mkt_type, mkt_asset` | – |
| `Market` | `market_handler` 481-484 | `StringWithInfo(result['marketRef'])` | – |
| `Message` | `message_handler` 122-129 | `StringWithInfo(message)`, or `ErrorValue("No result returned")` | – |
| `NumberAndUnit` | `number_and_unit_handler` 132-135 | `FloatWithInfo(result['value'], unit=result['unit'])` | – |
| `Table` | `simple_valtable_handler` 242-258 | frame `label, value`. Each value is recursively dispatched, and the frame `unit` is the first row's (multi-scenario results, `cc:44b307c6`) | – |
| `FixingTable` | `fixing_table_handler` 228-239 | `SeriesWithInfo` indexed by `fixingDate` | – |
| `PriceGrid` | `dict_risk_handler` 145-148 | `DictWithInfo` | – |
| `Error` | `error_handler` 105-113 | `ErrorValue(risk_key, errorString [+ '. request Id=…'])` plus `_logger.error` | – |
| `Unsupported` | `unsupported_handler` 487-490 | `UnsupportedValue(risk_key)` (not an error) | – |
| `MMAPITable`, `MMAPIPCATable`, `MMAPIPCAHedgeTable`, `MQVSValidators`, `CanonicalProjectionTable` | 379-478 | model/PCA/validator tables | not IR measures |

### 3.2 Recorded measure → `$type` (REC; hashes in `gs2117:test/calc_cache/`)

Hard evidence from 155 recordings: every IR request found, plus the relevant other ones.

| request (measures as named in `request_id`) | `$type` recorded | recording |
|---|---|---|
| `Price`, `Price(value:MYR)`, `DollarPrice` | `Risk` (`children: {}`) | `fb80e8b3`, `093e1ad3`, `363421ae`, `91c94170` |
| `IRFwdRate` (swap, FRA) | `Risk` | `21cb074a`, `2f3646d7`, `cb5f0c0b` |
| `IRAnnualImpliedVol` on swaptions / on swaps | `Risk` / **`Unsupported`** | `6f8d6b28`; `2799d7b0` (positions 5y, 10y are swaps), `af28694c` |
| `IRDelta` (no params) | `RiskVector` | `e114a9f5`, `0b8e0fc3`, `1ce838a5`, `fce828ba` |
| `IRDelta(currency:NOK)`, `IRDelta(currency:local)` | `RiskVector` | `c64a55ad`, `dba69f5a`, `7dde6877` |
| `IRDelta(aggregation_level:Type, …)` | **`Risk`** | `7dde6877`, `56782133` (10 dates) |
| `IRDelta(aggregation_level:Asset[, currency:local])` | `RiskByClass` | `5711e73d` (2 rows: IR/EUR, IR/USD), `f1a80643`, `8d2c2ca8` |
| `IRBasis` / `IRBasis(aggregation_level:Asset)` | `RiskVector` / `RiskByClass` | `65998587` / `e0d1a0fc`, `b112a918`, `faea7371` |
| `IRVega`, `IRVega(currency:local)` | `RiskVector` | `fa39f59c`, `6d08be70`, `90eccd55` |
| `IRVega(aggregation_level:Asset[, currency:local])` | `RiskByClass` (`classes: []` for a swap) | `70a863d4`, `db428590`, `c9537f68` |
| `Cashflows` | `IRPCashflowTable` | `717adad4`, `cea65b20` |
| `ResolvedInstrumentValues` | `LegDefinition` | `4a0c2426`, `c236b51c`, `73d0ff70` |
| `PnlExplain` | `RiskByClass` | `cce83425` |
| `Theta` (on swaps) | **`Error`**: "Old Griffin Valuations Wrapper only supports Leg level scenarios" | `91c94170` (its only recording) |
| any measure under `MultiScenario` | `Table` of `{label, value: Risk}` | `44b307c6`, `6eb85d48` |

**Not recorded anywhere, so the handler is INF:**
- `Theta` (success case): `Risk` or `RiskTheta`.
- `IRGamma`: `RiskSecondOrderVector`, as a cross-gamma frame.
- `IRGammaParallel` / `…LocalCcy`: `RiskSecondOrderVector` with a single row, becoming a float (the special case exists for exactly these measure types).
- `IRVanna` / `IRVolga`: probably `RiskVector` by default and `Risk` with Type. `doc:…/01_rates/23_solve_vanna_&_volga.ipynb#cell4,10` calls `.to_frame()` and `IRVanna(aggregation_level=Type)`.
- `MarketData`: `MDAPITable` (`priceable.market()` reads a `permissions` column, `gs2117:priceable.py:90-137`).
- `MarketDataAssets`: `RequireAssets`.
- `Market`: `Market`.
- `Description`: `Message`.
- `ParSpread`, `Annuity`, `LocalAnnuityInCents`, `PremiumCents`, `FairPremium`, `ForwardPrice`, `ExpiryInYears`, `ProbabilityOfExercise`, `CompoundedFixedRate`, `Lightning*`, `IRSpotRate`, `IRAnnualATMImpliedVol`, `IRDailyImpliedVol`, `IRDiscountDelta*`, `InflationDelta*`, `IRXccyDelta*`: `Risk`/`RiskVector`/`RiskByClass` by analogy with their peers.

### 3.3 `aggregation_level` → shape rule (REC + SRC)

| parameter | server `$type` | Python value |
|---|---|---|
| `None` (default) or `Point` (Point: INF, no recording) | `RiskVector` | 6-column ladder `DataFrameWithInfo`, `.unit None` |
| `Type` | `Risk` | `FloatWithInfo` with unit, e.g. `{"Basis Point": -1.0, "United States Dollar": 1.0}` |
| `Asset` | `RiskByClass` | `DataFrameWithInfo[mkt_type, mkt_asset, value]`, **unless** the measure's *name* is whitelisted and there are ≤2 rows of one type, in which case `FloatWithInfo`. gs test `test_measures.py:48,56` asserts `IRDelta(aggregation_level=Asset)` gives `res7["mkt_asset"].size == 2` |
| `Class` | (no recording) | INF: `RiskByClass` |

- `test_measures.py:57`: `assert not isinstance(res8[local_ccy_delta], type(res8[local_aggregated_delta]))`. The ladder (`currency='local'`) is a DataFrame; `(currency='local', aggregation_level=Type)` is a Float.
- **Conflicts with DESIGN §8.1 rule 5.** Rule 5 says "Type, Asset or Class → scalar". gs gives a scalar only for Type, and for the four whitelisted names under Asset.

### 3.4 Coordinate labels seen in recordings (REC, tally over all 155 recordings)
- **`IR`**, classes `CASH`, `CASH OIS`, `FRA`, `FRA OIS`, `SWAP`, `SWAP OIS`:
  - assets are currency codes (`EUR`, `GBP`, `JPY`, `NOK`, `USD`);
  - points `CASH STUB`, `O/N`, `1 DAY`, `6 MONTH`, IMM codes `DEC23`, tenors `3M…50Y`, `18M`.
- **`IR BASIS`**, classes `SWAP FWD`, `SWAP IMM`, `SWAP FWD RISK`, `SWAP IMM RISK`:
  - assets like `GBP OIS/GBP-3M`, `GBP-3M/GBP-6M`, `USD OIS/USD-3M`;
  - forward-start points `10Y2Y`, `1B3M`, `30Y10Y`.
- **`IR VOL`**, class `SWAPTION`: asset `USD-LIBOR-BBA`, points `5Y;1Y` (tail;expiry, see Key fact 3).
- **`RiskByClass` types** in PnlExplain: `CROSSES`, `IR`, `IR BASIS`, `IR CC`, `IR JUMP`, `IR SPIKE`, `IR XC`, `IR XC SPIKE`, `IR VOL`, `FX`, `FX FWD`. Assets include `CROSSES` and `EUR-EURIBOR-TELERATE`.
- `mkt_quoting_style` is `''` for every IR row. Labels are upper-case, and `path` is present but not mapped.

---

## 4. Units and scale, measure by measure (evidence-graded)

| measure | value scale / unit, and sign | evidence |
|---|---|---|
| `Price` | PV in the instrument's **local** ccy; `Price(currency=X)` converts. Unit `{"<full ccy name>": 1.0}` | REC `093e1ad3` (`{"Malaysian Ringgit": 1.0}` for `Price(value:MYR)`), `91c94170` (EUR swap `{"European Euro": 1.0}`, GBP `{"British Pound Sterling": 1.0}`); DOC `priceable.py:75-88` "Present value in local currency"; `Measures.ipynb#cell22` |
| `DollarPrice` | PV in USD, `{"United States Dollar": 1.0}` | REC `91c94170` measure 0 |
| `IRDelta` | **per +1bp** move of each curve instrument. Default **USD** (`{"Basis Point": -1, "United States Dollar": 1}` on a GBP trade); `currency='local'` gives local ccy. Sign: `PV(rates up) − PV(rates down)`, so a pay-fixed swap has positive delta | REC `56782133`, `7dde6877` (`{"Basis Point": -1.0, "European Euro": 1.0}`), `e114a9f5` (a 5y EUR pay-fixed swap has +47991 at `SWAP 5Y`); DOC docstring; `010204#cell4-5` |
| `IRDeltaParallel` | sum of IRDelta over assets (a parallel 1bp), USD | DOC `010204` "2-sided 1bp bump", `(up−down)/2`; REC `5711e73d` |
| `IRBasis` | per 1bp of each basis-curve instrument, USD | DOC docstring; REC `65998587` |
| `IRXccyDelta` | "Change in Price due to 1bp move in cross currency rates." | DOC only |
| `InflationDelta` | "Change in Price due to 1bp move in inflation curve." | DOC only |
| `IRVega` | **per +1bp absolute normal vol** (shock `±1/10000` on `'IR Vol'`), USD by default | DOC `010205#cell4-5`; docstring "individual 1bp moves in the implied volatility (IRAnnualImpliedVol)"; REC `fa39f59c` |
| `IRGammaParallel` | change in (parallel) IRDelta for a 1bp parallel shift, i.e. ccy per bp per bp | DOC docstring only. How the server scales it (centered 2nd difference / bp²) is **not documented** |
| `IRVanna`, `IRVolga` | "(USD)" per docstring; normalisation undocumented | DOC only |
| `IRFwdRate` | **decimal** par rate (`0.013165…` = 1.3165%), unit `{"Rate": 1.0}`, despite declared `unit=Percent` and "(in percent)" | REC `21cb074a`, `2f3646d7` (EUR `-0.005378`); DOC `010902#cell4,8`: `MeasureScenario(IRFwdRate, 0.024)`, `init[IRFwdRate] + 0.0001` = +1bp |
| `IRSpotRate` | INF: same scale as IRFwdRate (decimal) | no recording |
| `IRAnnualImpliedVol` | **decimal normal (bp) vol** (`0.00605` = 60.5bp/yr), unit `{"Rate Normal Volatility": 1.0}`, despite "(in percent)". `Unsupported` for swaps | REC `6f8d6b28`, `2799d7b0`; DOC `Measures.ipynb#cell13` (`* 10000`), `010902#cell12` (`MeasureScenario(IRAnnualImpliedVol, 0.009)`) |
| `IRAnnualATMImpliedVol` | INF: same scale as IRAnnualImpliedVol | no recording |
| `IRDailyImpliedVol` | declared BPS, "daily implied volatility (in basis points)". The annual↔daily conversion convention is undocumented | DOC only; used in `040307_strategy_seagull_bullish.ipynb#cell7` |
| `Theta` | undocumented for IR. `EqTheta` = "Change in Dollar Price over one day" (`target/measures.py:325-326`); CDTheta = "Change in option Dollar Price over one day" (`Measures.ipynb#cell2`) | only recording is an `Error` |
| `Cashflows.payment_amount` | signed from the **holder's** perspective. `IRSwap('Pay','5y','EUR', fixed_rate=-0.005)` shows fixed rows `+500000.0` (paying a negative rate gives an inflow) and float rows `notional·rate·dcf`, e.g. `-70828.59` at rate `-0.00137` | REC `717adad4` |
| `ExpiryInYears` | "fractional Years" | DOC |
| `ForwardPrice` | declared **BPS**, docstring "local currency". Conflicting; unverified | DOC |

**Server unit-dict names vs gs's own currency map.**
- `backtests/backtest_utils.py:124-146` `map_ccy_name_to_ccy` maps `'Euro'`, `'Pound Sterling'` and `'Malasyan Ringgit'`.
- Recordings return `'European Euro'`, `'British Pound Sterling'` and `'Malaysian Ringgit'`: tally 145+52 EUR, 56+30 GBP, 2 MYR. For these, `map.get` would return `None`.
- The engine calls it only on cash-payment `Price` values (`generic_engine.py:717`, `ccy = map_ccy_name_to_ccy(next(iter(value.unit)))`).
- It compares hedge units by dict equality (`generic_engine.py:511`, `if current_risk.unit != hedge_risk.unit: raise RuntimeError('cannot hedge in a different currency')`). So an aggregated IRDelta hedge compares `{"Basis Point": -1, "<ccy>": 1}` dicts.
- DESIGN §4.2 instead emits ISO-code dicts: `{"<CCY>": 1}` even for `ccy_per_bp`.

---

## 5. Special measures

### 5.1 `ResolvedInstrumentValues`
- Server `$type` is `LegDefinition`, handled as `instrument.resolved(result, risk_key)`. The result is the resolved Instrument, not a number (REC `4a0c2426`).
- Used by `Instrument.resolve` (`gs2117:instrument/core.py:114`), `Portfolio.resolve` (`markets/portfolio.py:340,366`) and `generic_engine_action_impls.py:56`.
- pricebt maps it to `PricingService.resolve` (DESIGN §8.1 rule 1).

### 5.2 `MarketData` / `Instrument.market()`
- `gs2117:priceable.py:90-137`: `self.calc(MarketData, fn=handle_result)`.
- The handler rebuilds `MarketDataCoordinate`s. It splits `mkt_point` on `';'` into a tuple (118), and sets value to `'redacted'` unless `permissions == 'Granted'` (121).
- It returns `OverlayMarket(base_market=result.risk_key.market, market_data={coord: value})`, or a `{date: OverlayMarket}` dict when historical.

### 5.3 `Cashflows` frame (`cashflows_handler`, 1.5.4 82-102)
- Mapping, output ← payload field:
  - `currency`←`currency`
  - `payment_date`←`payDate`
  - `set_date`←`setDate`
  - `accrual_start_date`←`accStart`
  - `accrual_end_date`←`accEnd`
  - `payment_amount`←`payAmount`
  - `notional`←`notional`
  - `payment_type`←`paymentType`
  - `floating_rate_option`←`index`
  - `floating_rate_designated_maturity`←`indexTerm`
  - `day_count_fraction`←`dayCountFraction`
  - `spread`←`spread`
  - `rate`←`rate`
  - `discount_factor`←`discountFactor`
- The 4 date columns are parsed from `'%Y-%m-%d'` strings.
- A key missing from a row becomes `None`: fixed rows have no `setDate`/`spread`. REC `717adad4`: fixed rows have `index "NA"`, `indexTerm "NA"`, `paymentType "FIX"`; float rows have `"Flt"`.
- DOC `17_calc_xccy_swap_cashflows#cell4,6` shows the same columns.
- R03 §14.2 wrote `'Fix'`. The recorded value is `'FIX'`.

### 5.4 `PnlExplain` family (`gs154:risk/measures.py:26-75`)
- **Mechanism.** `__RelativeRiskMeasure.pricing_context` (40-45) returns `PricingContext.current.clone(market=RelativeMarket(from_market=current.market, to_market=self.__to_market))`. `RelativeMarket` is at `gs2117:markets/markets.py:329-343`. `Instrument.calc` then prices the measure inside that cloned context.
- **Result.** `$type RiskByClass`, giving an **unsorted** `mkt_type, mkt_asset, value` frame (VER):
  - `SPIKE`/`JUMP` rows are folded into `CROSSES`;
  - the `CROSSES` row has `mkt_asset None`, because the payload has no `asset` key;
  - it becomes a Float only if there are ≤2 rows of one type.
- **Excludes time.** `doc:03_portfolios/examples/030007_pnl_explain.ipynb#cell4`: "# Compute the time component (PnlExplain does not do this)". The notebook totals `result[explain].aggregate().value.sum() + time_value` to match the dollar-price difference.
- **Quirks (VER):** repr is `'PnlExplain'` and `parameters` is None; equality **ignores `to_market`** (§1.1).
- **Future dates.** `markets/core.py:176-181` uses the from/to market date of a `RelativeMarket` for the "market dated in the future" check.
- **Snapshot.** Because these are classes, `tools/gs_api_snapshot.py:_describe` would snapshot them as `kind: "class"`, with a constructor signature, methods and properties (`pricing_context`). That is a heavier parity contract than a measure instance.

---

## 6. Risk helper functions (`gs_quant.risk`, via `from .core import *`; `risk/core.py` has no `__all__`)

Signatures VER by `inspect.signature` on 1.5.4. 2.1.17 is identical except `Tuple`→`tuple` hints.

| function | 1.5.4 lines / 2.1.17 | signature | semantics (quoted / verified) |
|---|---|---|---|
| `aggregate_risk` | 501-552 / 516 | `(results: Iterable[Union[DataFrameWithInfo, Future]], threshold: Optional[float] = None, allow_heterogeneous_types: bool = False) -> pd.DataFrame` | "Combine the results of multiple InstrumentBase.calc() calls, into a single result … :param threshold: exclude values whose absolute value falls below this threshold :param allow_heterogeneous_types: allow Series to be converted to DataFrames before aggregating". Steps: resolve Futures; take `.raw_value` (a **plain `pd.DataFrame` raises `AttributeError`**, VER); `pd.concat(dfs).fillna(0)`; `groupby(all columns except 'value', as_index=False).sum()`; keep only `abs(value) > threshold` (strict); `sort_risk`. Returns a **plain `DataFrame`**, not `DataFrameWithInfo`. Frames with different column sets get `0` fills in string columns, and then `sort_values` raises `TypeError: '<' not supported between 'str' and 'int'` (VER) |
| `aggregate_results` | 555-602 / 570 | `(results, allow_mismatch_risk_keys=False, allow_heterogeneous_types=False)` | Raises on an `Exception` element, on `result.error` (`'Cannot aggregate results in error'`), on mixed types (`'Cannot aggregate heterogeneous types: …'`), on unequal units (`'Cannot aggregate results with different units for {measure}'`), and on differing `risk_key.ex_historical_diddle` (`'Cannot aggregate results with different pricing keys'`). Dispatch: dict → per-key; tuple → set-union; Float → `FloatWithInfo(risk_key, sum, unit)`; Series → `SeriesWithInfo(sum)`; DataFrame → `DataFrameWithInfo(aggregate_risk(...))` |
| `subtract_risk` | 605-633 / 620 | `(left: DataFrameWithInfo, right: DataFrameWithInfo) -> pd.DataFrame` | "Subtract bucketed risk. Dimensions must be identical". Code: `assert left.columns.names == right.columns.names; assert 'value' in left.columns.names`, then negates `right.value` and runs `aggregate_risk((left, right_negated))`. **`columns.names` is `[None]` for every normal frame, so the second assert always fails** (VER on a standard 6-column risk frame). Effectively unusable in gs |
| `sort_risk` | 648-665 / 663 | `(df: pd.DataFrame, by: Tuple[str, ...] = ('date', 'time', 'mkt_type', 'mkt_asset', 'mkt_class', 'mkt_point')) -> pd.DataFrame` | "Sort bucketed risk". It sorts rows with `sort_values` (`mkt_point`, `point` and `label1` go through `point_sort_order`, `gs154:datetime/point.py:125`), reorders columns to `by`-present first and then the rest, and **sets `date` as the index if present** (VER). Returns a plain DataFrame |
| `sort_values` | 636-645 / 651 | `(data: Iterable, columns: Tuple[str, ...], by: Tuple[str, ...]) -> Iterable` | row sort key; `None` from a sort fn becomes `0` |
| `combine_risk_key` | 668-686 / 683 | `(key_1: RiskKey, key_2: RiskKey) -> RiskKey` | "Combine two risk keys (key_1, key_2) into a new RiskKey". Each of `provider, date, market, params, scenario, risk_measure` is kept if equal, else `None`. Used by `FloatWithInfo.__add__`/`__mul__` (`core.py:232-248`) |
| `combine_risk` | – | **does not exist** in 1.5.4 or 2.1.17 (grep) | the task's `combine_risk` presumably means `combine_risk_key` |

Related result classes (`risk/core.py`, 1.5.4):
- `FloatWithInfo` (180): `__add__` requires `self.unit == other.unit`, else `ValueError('FloatWithInfo unit mismatch')`; `__repr__` renders a unit dict `{'A':1,'B':-1}` as `"A/B"`.
- `StringWithInfo` (254), `DictWithInfo` (273), `SeriesWithInfo` (303), `DataFrameWithInfo` (369, including `filter_by_coord(coordinate)` 442-454).
- `ErrorValue` (103), `UnsupportedValue` (122).

pricebt has none of the six helper functions, and no `UnsupportedValue`, `StringWithInfo` or `DictWithInfo`. Its `combine_bucketed_frames` (`pb:src/pricebt/risk/results.py:142-153`) plays `aggregate_risk`'s role, keeping first-appearance order (DEV-R5).

---

## 7. How the backtester uses measures (2.1.17)
- **`result_ccy`** (`generic_engine.py:256-267`): calls `r(currency=result_ccy)` for each `ParameterisedRiskMeasure`, and otherwise `raiser(f'Unparameterised risk: {r}')`. The same applies to the price measure (`'Unparameterised price measure: …'`). So:
  - `IRGamma`, `IRGammaParallel`, `IRFwdRate`, `IRAnnualImpliedVol`, `Theta`, `Cashflows`, `ParSpread`, `DollarPrice` and the other plain `RM`s cannot be used with `result_ccy`.
  - Neither can 1.5.4's `IRVanna`/`IRVolga`. In 2.1.17 these accept it.
  - A `RiskMeasureWithDouble/String/…` measure would get a `TypeError` from the unexpected `currency=` kwarg. No IR measure uses those classes.
- **Hedging** (`generic_engine.py:500-515`): `.transform(...).aggregate()` on both sides, then a unit-dict equality check (§4).
- **Measures imported by the backtests package**:
  - `backtest_objects.py:43-51`: `Cashflows, ErrorValue, FXAnnualImpliedVol, FXDeltaLocalCcy, FXGammaLocalCcy, FXSpot, FXVegaLocalCcy`;
  - `generic_engine.py:54`: `Price`;
  - `generic_engine_action_impls.py:56`: `ResolvedInstrumentValues`;
  - `equity_vol_engine.py:47`: `EqDelta, EqGamma, EqSpot, EqVega`.

---

## 8. pricebt today vs gs

### 8.1 Measures (IR-relevant) missing from `pb:src/pricebt/risk/__init__.py`

pricebt has 32 instances (229-262). Of the IR-relevant catalogue above, it has `Price, DollarPrice, Annuity, Cashflows, ResolvedInstrumentValues, IRDelta, IRDeltaParallel, IRDeltaLocalCcy, IRGamma, IRGammaParallel, IRVega, IRVegaParallel, IRVegaLocalCcy, IRBasis, IRXccyDelta, InflationDelta, IRFwdRate, IRSpotRate, IRDailyImpliedVol, IRAnnualImpliedVol`.

**Missing (34 IR-relevant + 5 adjacent):**

| group | missing measures |
|---|---|
| generic (19) | `PricePips`, `ForwardPrice`, `FairPremium`, `Theta`, `ExpiryInYears`, `ProbabilityOfExercise`, `CompoundedFixedRate`, `MarketData`, `MarketDataAssets`, `Market`, `Description`, `LightningDV01`, `LightningOAS`, `CRIFIRCurve`, `BaseCPI`, `PnlExplain`, `PnlExplainClose`, `PnlExplainLive`, `PnlPredictLive` (the last four are classes) |
| rates levels (4) | `IRAnnualATMImpliedVol`, `ParSpread`, `LocalAnnuityInCents`, `PremiumCents` |
| rates first-order (6) | `IRBasisParallel`, `IRXccyDeltaParallel`, `InflationDeltaParallel`, `IRDiscountDeltaParallel`, `IRDiscountDeltaParallelLocalCcy`, `InflDeltaParallelLocalCcyInBps` |
| rates second-order (3) | `IRGammaParallelLocalCcy`, `IRVanna`, `IRVolga` |
| inflation info (2) | `InflMaturityCPI`, `Infl_CompPeriod` |
| optional adjacent (5) | `CrossMultiplier`, `FairPremiumInPercent` (FX), `EqForwardSpot`, `FXThetaLocalCcy`, `FXDeltaHedgeLocalCcy` (the last three are 2.1.17-only) |

### 8.2 Classes, parameter objects and enums missing

| gs symbol | where in gs | pricebt |
|---|---|---|
| `RiskMeasureWithDoubleParameter`, `…ListOfNumberParameter`, `…ListOfStringParameter`, `…MapParameter`, `…StringParameter` | `target/measures.py:49-161`, re-exported by `gs_quant.risk` | ✗ (only Currency + FiniteDifference exist, `pb:risk/__init__.py:149-210`) |
| `DoubleParameter`, `ListOfNumberParameter`, `ListOfStringParameter`, `MapParameter`, `StringParameter` | `target/common.py` (also reachable as `gs_quant.common.X` and, except `DoubleParameter`, `gs_quant.risk.X`) | ✗ |
| `MktMarkingOptions` | `target/common.py:5210` (`gs_quant.common`) | ✗ (pricebt types the field `Any`) |
| `FiniteDifferenceMethod` enum | `target/common.py:4016` (`gs_quant.common`; **not** in `gs_quant.risk`) | ✗ (pricebt stores a raw `str`, no coercion or validation) |
| `RiskMeasureType` enum (130 members in 1.5.4) | `target/common.py:4526` (`gs_quant.common`, `gs_quant.risk`) | ✗ (`measure_type` is a plain `str`) |
| `UnsupportedValue`, `StringWithInfo`, `DictWithInfo` | `risk/core.py` | ✗ |
| `aggregate_risk`, `aggregate_results`, `subtract_risk`, `sort_risk`, `sort_values`, `combine_risk_key` | `risk/core.py` | ✗ |
| `AggregationLevel`, `AssetClass`, `RiskMeasureUnit` | – | ✓ exact members (`pb:common.py:39-45, 165-193`) |

### 8.3 Behavioural and shape differences in what pricebt already has

| # | pricebt | gs |
|---|---|---|
| 1 | `RiskMeasure.measure_type: str`; `__repr__` fallback `self.measure_type or ""` (the **value**) (`pb:risk/__init__.py:117,124-125`) | enum; fallback `self.measure_type.name` (the member **name**, e.g. `Dollar_Price`). Only matters for an unnamed measure |
| 2 | `CurrencyParameter(value, parameter_type)` and `FiniteDifferenceParameter(…, parameter_type)` are frozen dataclasses; `parameter_type` **is an init arg**; **no `name` field** (67-87) | `parameter_type` `init=False`; a `name` field exists; `order=True`; `repr` = `Currency(value:EUR)` / `FiniteDifference(…)` |
| 3 | `Price(currency='EUR')(name='X')` and `Price(currency='EUR')()` **work** (reads `.value`, 157-158) | **raise `AttributeError`** in both versions (`target/measures.py:41-42`). No DEV id covers this |
| 4 | `finite_difference_method='Centered'` stays a `str`; invalid values are accepted | coerced to `FiniteDifferenceMethod`; invalid → `ValueError` |
| 5 | `__lt__` compares `_params_repr` bodies (130-134) | compares `parameter_type` when the classes differ, else `repr(parameters)` (with the `Type(` prefix). Same order within one parameter type |
| 6 | rule 5 (DESIGN §8.1): Asset/Class → scalar | Asset → frame `mkt_type, mkt_asset, value` unless the name is whitelisted and ≤2 single-type rows (§3.3) |
| 7 | unit dicts use ISO codes, `{"USD": 1}` for `ccy_per_bp` (DESIGN §4.2) | full names; aggregated deltas carry `{"Basis Point": -1, "<name>": 1}`; `RiskVector`/`RiskByClass` frames have `unit None` |
| 8 | no `Unsupported`: a missing mapping raises `ConfigError` (DESIGN §8.1 rule 3) | an instrument that is insensitive or unsupported gives a silent `UnsupportedValue` (`IRAnnualImpliedVol` on a swap, `cc:2799d7b0`), or an empty frame (`IRVegaParallel` on a swap) |
| 9 | DESIGN §8.1 rule 3a rejects `bump_size` / `finite_difference_method` / `scale_factor` / `local_curve` / `mkt_marking_options` | gs forwards them to the server |

---

## 9. The parity snapshot and how to parity-check new measures

**What is recorded.**
- `pb:tools/gs_api_snapshot.py:175-191` `_describe`: a module-level dataclass instance becomes `{"kind": "risk_measure", "class": type(obj).__name__, "name": obj.name, "measure_type": str(obj.measure_type)}`. **Nothing else**: no `asset_class`, `unit`, `parameters`, docstring or `repr`.
- Which measures: only `RISK_MEASURE_NAMES` (117-124), 32 names. Of these, 29 are found in 1.5.4, and `FXDeltaLocalCcy`/`FXGammaLocalCcy`/`FXVegaLocalCcy` land in `"requested_not_found"` (216-222).
- `pb:tests/data/gs_api_1_5_4.json` therefore has exactly 29 `gs_quant.risk.*` entries: `Annuity, Cashflows, DollarPrice, EqDelta, EqGamma, EqSpot, EqVega, FXAnnualImpliedVol, FXDelta, FXGamma, FXSpot, FXVega, IRAnnualImpliedVol, IRBasis, IRDailyImpliedVol, IRDelta, IRDeltaLocalCcy, IRDeltaParallel, IRFwdRate, IRGamma, IRGammaParallel, IRSpotRate, IRVega, IRVegaLocalCcy, IRVegaParallel, IRXccyDelta, InflationDelta, Price, ResolvedInstrumentValues`.
- An example entry: `"gs_quant.risk.IRDelta": {"class": "RiskMeasureWithFiniteDifferenceParameter", "kind": "risk_measure", "measure_type": "Delta", "name": "IRDelta"}`.
- **Not in the snapshot:** risk classes (`RiskMeasure`, `RiskMeasureWith*`), parameter dataclasses, helper functions, result classes. `gs_quant.common` enums **are** all snapshotted (224-228), including `AggregationLevel`, `AssetClass`, `FiniteDifferenceMethod`, `RiskMeasureType` and `RiskMeasureUnit`.

**How it is checked.**
- `pb:tests/test_gs_api_parity.py:326-329`: for each snapshot risk measure, compare `class`, `name` and `measure_type` with pricebt's `describe()` (112-131, the same logic).
- `:359` `RISK_2_1_17_ONLY`: existence-only for `requested_not_found`.
- `:393-399` `test_no_unexpected_extra_risk_measures`: pricebt's `__all__` measure instances must be a subset of the snapshot names plus the 2.1.17-only names. **Adding `IRBasisParallel` (or any other §8.1 measure) to `pricebt.risk.__all__` fails this test** until the snapshot is regenerated.
- `:402-409` common enums: iteration is pricebt-driven, and members must match **exactly** unless a `subset: true` exception exists in `pb:tests/data/gs_api_exceptions.yaml`, as `Currency` and `AssetType` do (`_compare_members`, `:222-232`). So a partial `RiskMeasureType` needs a `subset` exception with a reason and a DEV id. A full copy of the 130 1.5.4 members passes, but lacks 2.1.17's 6 FX members.

**Recipe to parity-check new IR measures.**
1. Append the names to `RISK_MEASURE_NAMES` in `tools/gs_api_snapshot.py:117-124`.
2. Regenerate with `C:\Users\chris\anaconda3\python.exe tools\gs_api_snapshot.py` (the base python; the tool refuses any version other than 1.5.4, `:85-88`). 2.1.17-only names land in `requested_not_found`, which gives existence-only checks.
3. **Decide per measure whether `class` should follow 1.5.4 or 2.1.17.** For `IRVanna`/`IRVolga`, the snapshot records `"class": "RiskMeasure"` (1.5.4). MUST-2 ("1.5.4 signatures, 2.1.17 behaviour") pulls both ways, so this needs a DEV id and a `gs_api_exceptions.yaml` entry if pricebt follows 2.1.17 (the notebook `23_solve_vanna_&_volga` needs the callable form).
4. `PnlExplain*` are classes. The snapshot would need a new branch; today `_describe` would emit `kind: "class"` with signature `[["to_market", …, "NODEFAULT"]]` and methods/properties.
5. Because the snapshot does not record `asset_class`, `unit` or parameters, unit and preset parity (e.g. `IRXccyDeltaParallel` = **Type**, not Asset) needs its own unit test against the §2.1 table.

---

## 10. Corrections to earlier research notes

| note | claim | fact | evidence |
|---|---|---|---|
| R03 §14.3, last paragraph | "`IRDelta(aggregation_level='Type')` (named `IRDelta`) comes back as a small DataFrame, not a float" | Type aggregation returns `$type: Risk`, a `FloatWithInfo` with unit `{"Basis Point": -1, "<ccy>": 1}`. The frame case is **Asset** (and presumably Class) | `cc:7dde6877`, `cc:56782133`; gs test `test_measures.py:57` |
| R03 §14.2 / R04 §9 | `IRFwdRate` "par rate **in percent** (040307 multiplies by 1e4)" | the value is a **decimal** (0.0132). ×1e4 converts it to bp, which is consistent with decimal. The declared `unit=Percent` is misleading | `cc:21cb074a`; `doc:010902#cell4,8` |
| R03 §14.2 | `IRAnnualImpliedVol (%)` | decimal normal vol (0.00605), `{"Rate Normal Volatility": 1.0}` | `cc:6f8d6b28`; `Measures.ipynb#cell13` |
| R03 §14.2 | Cashflows `payment_type ('Fix'/'Flt')` | recorded `'FIX'` / `'Flt'` | `cc:717adad4`; `doc:17_calc_xccy_swap_cashflows#cell4` |
| R04 §9 | `IRGamma`: "change in aggregated IRDelta per 1bp" | that text is `IRGammaParallel`'s docstring; `IRGamma.__doc__ == "IRGamma"` | `target/measures.py:457-461` |
| task wording | "vega cube points 'expiry;tenor'" | recorded order `tail;expiry` (`'5Y;1Y'` for a 1y-expiry into 5y swaption) | `cc:fa39f59c`, `gs2117:test/risk/test_results.py:123-125` |

---

## 11. Facts the config-driven design must account for (no decisions taken here)
1. For each measure a config maps, the external library defines the number. gs fixes these conventions, which a config author must reproduce to keep notebook numbers comparable:
   - IRDelta/IRBasis/IRVega: **per +1bp, centered**;
   - vega: **absolute normal vol**;
   - IRFwdRate and IR vols: **decimals**;
   - IRDelta: **USD by default**, not local;
   - Price: **local** ccy.
2. The shape follows the parameter, not the measure (§3.3). A faithful port needs:
   - scalar for `Type`;
   - an `(mkt_type, mkt_asset, value)` frame for `Asset`, collapsing to a float only for the 4 whitelisted names;
   - a 6-column ladder for `None`/`Point`.

   DESIGN §8.1 rule 5 currently maps Asset to a scalar.
3. Second-order measures (`IRGamma`) use a **12-column inner/outer** frame, which pricebt's 6-column `make_bucketed_frame` cannot represent. `IRGammaParallel` is a scalar with **no unit**.
4. `Cashflows`, `MarketData`, `ResolvedInstrumentValues` and `PnlExplain` are not per-bucket numbers:
   - `Cashflows` is a 14-column table with dates;
   - `MarketData` is a 7-column table with `permissions`;
   - `ResolvedInstrumentValues` is an Instrument;
   - `PnlExplain` is a two-market measure. It needs a from-market and a to-market inside one evaluation, and pricebt's injected variables (DESIGN §4.3) offer only one `market`.
5. gs returns `Unsupported` (silently) or an empty frame for an inapplicable measure (vol on a swap). pricebt raises `ConfigError` for an unmapped measure.
6. Bonds: gs has only `LightningDV01`/`LightningOAS` beyond PV and cashflows. Yield, duration and convexity measures would be pricebt extensions and need DEV ids.
