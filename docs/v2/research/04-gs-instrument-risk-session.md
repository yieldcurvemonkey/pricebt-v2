# 04 — gs_quant pieces outside `backtests/`: instruments, enums, Portfolio, contexts, session, dates, risk measures

> **Key facts**
> 1. Reference is **gs_quant 1.5.4** (`_version.get_versions()` → `full-revisionid 1806f284…`, 2026-03-04), at `C:/Users/chris/anaconda3/Lib/site-packages/gs_quant`. Every signature below came from `inspect.signature` / `dataclasses.fields` on that install, or from reading the source (file:line given).
> 2. `IRSwap` is a dataclass with **33 fields, 31 settable in `__init__`** (`asset_class`, `type_` are `init=False`). Positional order is `pay_or_receive, termination_date, notional_currency, notional_amount, effective_date, …` (so `IRSwap('Pay', '10y', 'USD')` works). Strings are coerced to enums when the field is set; an invalid enum string raises `ValueError` at construction.
> 3. Instrument `__eq__`/`__hash__` (`unsafe_hash=True`) cover **every field, `name` included**, so the hash changes when the instrument is changed in place (`scale`, setattr). `to_dict()` **leaves out** `name`; `as_dict()` **keeps** it. `clone(**kw)` is `dataclasses.replace` and also carries over `unresolved`, `metadata` and `resolution_key`.
> 4. `calc()`/`resolve()`/`price()` return a **future** when a `PricingContext` is entered or async, and a value otherwise. Under `HistoricalPricingContext`, a scalar measure comes back as a `SeriesWithInfo` indexed by `date` and a ladder comes back as a `DataFrameWithInfo` with index `date`. `resolve(in_place=True)` is forbidden under a historical context.
> 5. `RelativeDateSchedule(freq, start, end).apply_rule()` returns `[start] + [RelativeDate(f"{n*i}{unit}", start) for i=1..]` while `<= end`. The **raw start date is kept even when it is a Saturday**, and each step is computed from `start`, not chained. With no calendars (`holiday_calendar=None`, no currencies/exchanges) nothing touches the network: only weekends are holidays.

Scope: what a backtest notebook or the backtest engine touches **outside** `gs_quant/backtests/`. Result objects (`PortfolioRiskResult`, `BackTest.result_summary`, …) are covered in `03-gs-results-backtest-objects.md`; §9 below only summarises the measure objects and the result shapes that instrument `calc` returns.

pricebt v2 must provide the **same module paths under `pricebt`** (for example `from pricebt.instrument import IRSwap`), and its core must not import gs_quant, rateslib, QuantLib or ARBS. Everything below is *reference behaviour to mimic*, not code to import.

---

## 0. Import paths used by the backtest notebooks (the public surface to mirror)

I counted every `from gs_quant.X import Y` outside `backtests` in all 24 notebooks under `gs-quant/gs_quant/documentation/04_backtesting/**` (the number is how many notebooks use it):

| gs_quant import | symbols (count) | pricebt v2 path to provide |
|---|---|---|
| `gs_quant.session` | `GsSession` (27), `Environment` (5) | `pricebt.session` |
| `gs_quant.instrument` | `IRSwap` (4), `IRSwaption` (4), `FXOption` (7), `EqOption` (6), `FXForward` (3), `InflationSwap` (1), `OptionStyle` (8), `OptionType` (5) | `pricebt.instrument` |
| `gs_quant.common` | `Currency` (4), `PayReceive` (2), `BuySell` (5), `AggregationLevel` (4), `OptionType` (4) | `pricebt.common` |
| `gs_quant.risk` | `Price` (12), `IRDelta` (4), `FXDelta` (3), `DollarPrice`, `EqVega`, `IRDailyImpliedVol`, `IRFwdRate`, `InflationDelta` (1 each) | `pricebt.risk` |
| `gs_quant.data` | `Dataset` (3), `DataFrequency` (1) | `pricebt.data` (see open question) |
| `gs_quant.datetime` | `business_day_offset` (1) | `pricebt.datetime` |
| `gs_quant.markets` | `HistoricalPricingContext` (1); `PricingContext` is used via `from gs_quant.backtests.equity_vol_engine import *` and in 040308 | `pricebt.markets` |
| `gs_quant.markets.portfolio` | `Portfolio` (1; also reached through `backtests.*` star imports) | `pricebt.markets.portfolio` |
| `gs_quant.target.common` | `UnderlierType` (5) | `pricebt.target.common` |
| `gs_quant.target.measures` | `Price` (1) | `pricebt.target.measures` |

Other imports that `backtests/*.py` itself makes from outside the package (the engine port will need these names):

| Import (count in `backtests/`) | Purpose |
|---|---|
| `gs_quant.instrument: Instrument, Cash, IRSwap, EqOption, EqVarianceSwap` | isinstance checks, cash legs |
| `gs_quant.markets.portfolio: Portfolio` (4) | wrapping trades |
| `gs_quant.markets: PricingContext, HistoricalPricingContext` | pricing dates |
| `gs_quant.risk: Price, RiskMeasure, ErrorValue, Cashflows, EqDelta, EqSpot, EqGamma, EqVega`; `gs_quant.risk.results: PricingFuture, PortfolioRiskResult`; `gs_quant.risk.transform: Transformer` | measures and results |
| `gs_quant.common: RiskMeasure, Currency, ParameterisedRiskMeasure, TradeAs, OptionType, BuySell, FieldValueMap, AssetClass, CurrencyName` | enums and measure base classes |
| `gs_quant.datetime: is_business_day, prev_business_date, business_day_offset`; `gs_quant.datetime.relative_date: RelativeDate, RelativeDateSchedule` | date grids and durations |
| `gs_quant.data: Dataset, DataFrequency` | `GsDataSource`, `MACRO_EVENTS_CALENDAR` trigger |
| `gs_quant.base: field_metadata, static_field, exclude_none, get_enum_value, Base, Priceable, EnumBase` | dataclass plumbing |
| `gs_quant.json_convertors(_common)`, `gs_quant.target.backtests`, `gs_quant.api.gs.backtests*`, `gs_quant.tracing`, `gs_quant.timeseries: interpolate, Interpolate`, `gs_quant.errors: MqValueError`, `gs_quant.context_base: nullcontext` | serialisation, the remote engine, tracing, utilities |

Re-exports that notebooks rely on (verified with `hasattr`): `gs_quant.instrument` also exposes `OptionStyle, OptionType, Currency, PayReceive, BuySell, SwapClearingHouse, SwapSettlement, Instrument, Security, DummyInstrument`. That is `instrument/__init__.py`: `from .core import Instrument, Security, DummyInstrument`, `from gs_quant.target.instrument import *`, `from gs_quant.target.common import SwapClearingHouse, SwapSettlement`.

**Trap:** `gs_quant.common.PayReceive` is **not** the same class as `gs_quant.target.common.PayReceive`:

| class | members (name → value) | notes |
|---|---|---|
| `gs_quant.common.PayReceive` (common.py:46-58) | `Pay→'Pay'`, `Receive→'Rec'`, `Straddle→'Straddle'` | `_missing_`: `'receive'`/`'receiver'` (any case) → `Receive`; otherwise case-insensitive match on value. **This is what the instrument field types use** and what `gs_quant.instrument.PayReceive` is. |
| `gs_quant.target.common.PayReceive` | `Pay, Payer, Receive, Receiver, Straddle, Rec` (values equal names) | raw generated enum; not used by instruments |

`gs_quant.common.Currency is gs_quant.target.common.Currency` → `True` (one class, re-exported).

---

## 1. `IRSwap` — full constructor

Source: `gs_quant/target/instrument.py:2538-2596`. Decorators, in order: `@handle_camel_case_args`, `@dataclass_json(letter_case=LetterCase.CAMEL)`, `@dataclass(unsafe_hash=True, repr=False)`, class `IRSwap(Instrument)`.

Exact `__init__` signature (1.5.4):

```
IRSwap(pay_or_receive=None, termination_date=None, notional_currency=None, notional_amount=None,
       effective_date=None, principal_exchange=None, floating_rate_for_the_initial_calculation_period=None,
       floating_rate_option=None, floating_rate_designated_maturity=None, floating_rate_spread=None,
       floating_rate_frequency=None, floating_rate_day_count_fraction=None,
       floating_rate_business_day_convention=None, fixed_rate=None, fixed_rate_frequency=None,
       fixed_rate_day_count_fraction=None, fixed_rate_business_day_convention=None, fee=0.0,
       fee_currency=None, fee_payment_date=None, clearing_house=None, fixed_first_stub=None,
       floating_first_stub=None, fixed_last_stub=None, floating_last_stub=None, fixed_holidays=None,
       floating_holidays=None, roll_convention=None, fixed_rate_accrual_convention=None,
       floating_rate_accrual_convention=None, name=None) -> None
```

In the table, "GS default" is the server-side default applied by `resolve()`, as documented in `documentation/02_pricing_and_risk/00_instruments_and_measures/examples/01_rates/02_swap_trade_construction.ipynb` (comments quoted). pricebt has no server, so these defaults have to live in the asset config (see §10).

| # | field | type (python) | ctor default | GS default when unset (documented) | typical values in notebooks and docs |
|---|---|---|---|---|---|
| 1 | `pay_or_receive` | `Optional[PayReceive]` | `None` | **receiver** ("defaults to a receiver swap"); refers to the **fixed** leg | `PayReceive.Pay`, `'Pay'`, `'Receive'`, `'receive'`, `'Receiver'` (all → enum) |
| 2 | `termination_date` | `Union[date, str, None]` | `None` | **`'10y'`** | `'10y'`, `'30y'`, `'50y'`, `date(2025,11,12)`, `'2030-07-15'`. A tenor is **relative to `effective_date`** |
| 3 | `notional_currency` | `Optional[Currency]` | `None` | **USD** | `Currency.EUR`, `'EUR'`, `'gbp'` (case-insensitive) |
| 4 | `notional_amount` | `Union[float, str, None]` | `None` | not documented (open question) | `1e4`, `100e6`, `10000`, `'100k'`, `'10m'`, `'=solvefor(...)'`. **Strings are stored unchanged** (`IRSwap(notional_amount='100k').notional_amount == '100k'`, type `str`) |
| 5 | `effective_date` | `Union[date, str, None]` | `None` | "default is pricing date" (spot start) | `'0b'`, `'5b'`, `'3m'`, `'1y'`, `date(2019,11,12)`, `'2025-07-15'`. A tenor is **relative to `PricingContext.pricing_date`** |
| 6 | `principal_exchange` | `Optional[PrincipalExchange]` | `None` | none | `'None'` |
| 7 | `floating_rate_for_the_initial_calculation_period` | `Optional[float]` | `None` | from the forward curve | `0.0075` (absolute: 75bp) |
| 8 | `floating_rate_option` | `Optional[str]` | `None` | a LIBOR-style index per ccy; **`'OIS'` = the ccy's default overnight index** | `'USD-LIBOR-BBA'`, `'USD-ISDA-SWAP RATE'`, `'EUR-EONIA-OIS-COMPOUND'`, `'OIS'`, `'EUR-EuroSTR-COMPOUND'` |
| 9 | `floating_rate_designated_maturity` | `Optional[str]` | `None` | = floating frequency | `'3m'` |
| 10 | `floating_rate_spread` | `Union[float, str, None]` | `None` | 0 | `0.01` (decimal: 100bp), `40/1e4` |
| 11 | `floating_rate_frequency` | `Optional[str]` | `None` | ccy/tenor market standard | `'1m'`, `'3m'`, `'6m'` |
| 12 | `floating_rate_day_count_fraction` | `Optional[DayCountFraction]` | `None` | ccy standard | `DayCountFraction.ACT_OVER_365L_ISDA`, `'30/360'`, `'ACT/360'` |
| 13 | `floating_rate_business_day_convention` | `Optional[BusinessDayConvention]` | `None` | ccy standard | `BusinessDayConvention.Following`, `'Modified Following'` |
| 14 | `fixed_rate` | `Union[float, str, None]` | `None` | **ATM (par)** | `'ATM'`, `'atm'`, `'ATM+25'` (+25bp over par), `'ATM-5'`, `'a-100'` (`a` = ATM, −100bp), `'atm+5'`, `0.0325` (**decimal**: 3.25%), `-0.025`, `'10000/pv'` (solve for PV = 10000) |
| 15 | `fixed_rate_frequency` | `Optional[str]` | `None` | ccy standard | `'6m'`, `'1y'` |
| 16 | `fixed_rate_day_count_fraction` | `Optional[DayCountFraction]` | `None` | ccy standard | enum or value string |
| 17 | `fixed_rate_business_day_convention` | `Optional[BusinessDayConvention]` | `None` | ccy standard | enum or value string |
| 18 | `fee` | `Optional[float]` | **`0.0`** | 0. "A positive fee will have a negative impact on the PV" | `50000`, `1e5` |
| 19 | `fee_currency` | `Optional[Currency]` | `None` | notional ccy | `Currency.GBP` |
| 20 | `fee_payment_date` | `Union[date, str, None]` | `None` | spot date from the pricing date | `'1y'`, `date(2020,1,30)` |
| 21 | `clearing_house` | `Optional[SwapClearingHouse]` | `None` | — | `SwapClearingHouse.LCH`, `'CME'` |
| 22 | `fixed_first_stub` | `Union[date, str, None]` | `None` | — | — |
| 23 | `floating_first_stub` | `Union[date, str, None]` | `None` | — | — |
| 24 | `fixed_last_stub` | `Union[date, str, None]` | `None` | — | — |
| 25 | `floating_last_stub` | `Union[date, str, None]` | `None` | — | — |
| 26 | `fixed_holidays` | `Optional[str]` | `None` | — | calendar code string |
| 27 | `floating_holidays` | `Optional[str]` | `None` | — | calendar code string |
| 28 | `roll_convention` | `Optional[str]` | `None` | — | — |
| 29 | `fixed_rate_accrual_convention` | `Optional[AccrualConvention]` | `None` | — | `'Adjusted'`/`'Unadjusted'` |
| 30 | `floating_rate_accrual_convention` | `Optional[AccrualConvention]` | `None` | — | same |
| 31 | `asset_class` | `Optional[AssetClass]` | `AssetClass.Rates`, **init=False** | — | read-only; setting it raises `ValueError('asset_class cannot be set')` |
| 32 | `type_` | `Optional[AssetType]` | `AssetType.Swap`, **init=False**; JSON key `type` | — | read as `swap.type` or `swap.type_` |
| 33 | `name` | `Optional[str]` | `None` | — | free label. Uses `name_metadata = config(exclude=exclude_always)`, so it is **never serialised by `to_dict()`** |

Fields the rates backtest notebooks actually set: `pay_or_receive`, `termination_date`, `notional_currency`, `notional_amount`, `fixed_rate`, `name` (040304, 040310, 040311), plus `floating_rate_option` on swaptions (040307). The pricing docs also use `effective_date`, `fixed_rate_frequency`, `floating_rate_*`, `fee*` and `clearing_house`.

`IRSwap.scale_in_place(scaling=None, check_resolved=True)` (target/instrument.py:2575-2596). `Instrument.scale` calls it:

```
if scaling is None or scaling == 1: return
if self.unresolved is None:
    if check_resolved: raise RuntimeError('Can only scale resolved instruments')
    if notional_amount is None or pay_or_receive is None: raise RuntimeError('Can only scale unresolved instruments with the buysell and primary size fields set')
    if any non-numeric among (notional_amount, fee): raise RuntimeError('All specified size fields must be numeric')
self.notional_amount *= abs(scaling)
if scaling < 0:
    pay_or_receive flipped Pay<->Receive
    if check_resolved or self.fee is not None: self.fee *= -1
```

Verified: `IRSwap('Pay','10y','USD',notional_amount=1e4).scale(-2, in_place=False, check_resolved=False)` gives `notional_amount=20000.0`, `pay_or_receive=Rec`, `fee=-0.0`. Without `check_resolved=False` it raises `RuntimeError: Can only scale resolved instruments`. **Consequence for the engine:** gs scales only resolved trades. `generic_engine.py:121,280,293,299,1044` call `.scale(...)` on trades produced by `resolve`. pricebt must keep a "resolved" flag (or equivalent) on the instrument to reproduce this.

---

## 2. Other instruments (future assets; field lists at summary level)

All share the same decorators and base, with `name` last and `asset_class`/`type_` as `init=False`. Fields are listed in **positional order**.

| class (source line) | asset_class / type | init fields in order (defaults are `None` unless shown) | `scale_in_place` rule |
|---|---|---|---|
| `IRSwaption` (target/instrument.py:2599) | Rates / Swaption | `pay_or_receive, termination_date, notional_currency, effective_date, notional_amount, expiration_date, floating_rate_option, floating_rate_designated_maturity, floating_rate_spread(float), floating_rate_frequency, floating_rate_day_count_fraction, floating_rate_business_day_convention, fixed_rate_frequency, fixed_rate_day_count_fraction, fixed_rate_business_day_convention, strike, premium, premium_payment_date, fee=0.0, fee_currency, fee_payment_date, clearing_house, settlement(SwapSettlement), buy_sell(BuySell), name` | `notional *= abs(s)`; if `s<0`, flip **`buy_sell`** and `fee *= -1` (needs `notional_amount` and `buy_sell` when unresolved) |
| `FXOption` (1163) | FX / Option | `pair, buy_sell, option_type, notional_amount, notional_currency, notional_amount_in_other_currency, strike_price, settlement_date, settlement_currency, settlement_rate_option, method_of_settlement(OptionSettlementMethod), expiration_date, expiration_time, premium, premium_currency, premium_payment_date(str), exercise_style(OptionExerciseStyle), name` | yes |
| `EqOption` (570) | Equity / Option | `underlier, expiration_date, strike_price, option_type, option_style, number_of_options, exchange, multiplier, settlement_date, settlement_currency, premium=0.0, premium_payment_date, valuation_time, method_of_settlement, underlier_type(UnderlierType), buy_sell, premium_currency, trade_as(TradeAs), future_contract, underlier_currency, premium_settlement_date, expiry_settlement_days, expiry_settle_date, name` | yes |
| `FXForward` (991) | FX / Forward | `pair, settlement_date, forward_rate, notional_amount, notional_currency, notional_amount_in_other_currency, buy_sell, name` | yes |
| `InflationSwap` (1606) | Rates / InflationSwap | `pay_or_receive, termination_date, notional_currency, effective_date, notional_amount, index, floating_rate_business_day_convention, fixed_rate, fixed_rate_business_day_convention, fee=0.0, base_cpi, clearing_house, name` | yes |
| `Cash` (96) | Cash / Cash | `currency, payment_date(date), notional_amount, name` | **no** (`scale` raises `NotImplementedError`) |

Constructions seen in notebooks (the kwargs grammar the trade-construction expression must accept unchanged):

```python
IRSwaption(expiration_date='1m', termination_date='30y', notional_currency=Currency.EUR, buy_sell='Sell')            # 040303
IRSwaption(expiration_date='6m', termination_date='10y', notional_currency=Currency.USD, buy_sell=BuySell.Sell,
           strike='=solvefor(25e3,bp)', name='swaption10y')                                                          # 040305
IRSwaption(pay_or_receive='Receiver', termination_date='10y', notional_currency='USD', expiration_date='1y',
           notional_amount=-1*n, strike='A-50', floating_rate_option='USD-LIBOR-BBA', name='swaption0')              # 040307
IRSwaption(PayReceive.Straddle, '10y', Currency.USD, expiration_date='1m', notional_amount=1e8, buy_sell='Sell')     # tutorials/Backtesting
FXOption(buy_sell=BuySell.Buy, option_type=OptionType.Put, pair='USDCNH', strike_price='ATMF', expiration_date='1m', name='1m_put', premium=0)
FXForward(pair='EURUSD', settlement_date='1y', notional_amount=1e5, name='1y_forward')
EqOption('SX5E', underlier_type=UnderlierType.BBID, expirationDate='3m', strikePrice='ATM', optionType=OptionType.Call, optionStyle=OptionStyle.European)  # camelCase kwargs!
InflationSwap(pay_or_receive='Pay', termination_date='30y', notional_currency='EUR', notional_amount=100e6, name='30yhedge')
```

Swaption-specific value grammars: `strike` accepts `'ATM'`, `'atm+40'`, `'A-50'`, `'atmf'`, `'25d'`, decimals such as `0.015`, `'10000/pv'`, and `'=solvefor(...)'`. `AddTradeAction(option, 'expiration_date')` uses **a field name as the trade duration** (`backtest_utils.get_final_date`: `if hasattr(inst, str(duration)): return getattr(inst, str(duration))`, backtest_utils.py:76-78). **The asset object must therefore expose resolved date fields as attributes** (for example `expiration_date` as a `date` once resolved).

---

## 3. Instrument base behaviour (`Base` → `Priceable` → `PriceableImpl` → `InstrumentBase` → `Instrument`)

MRO: `Instrument(PriceableImpl, InstrumentBase)` (instrument/core.py:44). `PriceableImpl(Priceable)` (priceable.py:30), `Priceable(Base)` (base.py:430), `InstrumentBase(Base)` (base.py:577), `Base(ABC)` (base.py:236).

### 3.1 Construction and attribute semantics

| behaviour | source | exact rule |
|---|---|---|
| camelCase kwargs | base.py:95-116 `handle_camel_case_args` | Each kwarg that is not ALL-CAPS is converted to snake_case (`_get_underscore`). If both `fooBar` and `foo_bar` are given → `ValueError('fooBar and foo_bar both specified')`. Then `cls._field_mappings()` maps JSON names (e.g. `type` → `type_`). |
| setattr coercion | base.py:258-271 `Base.__setattr__` | The key is snake-cased and mapped. If it is a dataclass field: `init=False` → `ValueError(f'{key} cannot be set')`; otherwise the value goes through `__coerce_value(fld.type, value)`. A value already of the right type is kept. numpy scalars → `.item()`. Objects with `.tolist()` → `.tolist()`. `Optional[Enum]` with a str → `Enum(value)`, which runs `EnumBase._missing_` (case-insensitive on **value**). Invalid → **`ValueError: 'Payy' is not a valid PayReceive`**, raised from the constructor. `Union[float,str]` fields keep strings unchanged. |
| camelCase getattr | base.py:242-256 | `swap.notionalAmount` works (maps to `notional_amount`). `swap.fixedRate = 0.02` sets `fixed_rate`. |
| `EnumBase` | base.py:159-176 | `_missing_(key)`: `str(key)`, then case-insensitive match on `.value`. `__str__`/`__repr__` return `.value`, so `repr(PayReceive.Receive) == 'Rec'` and dict reprs show `Pay`, `USD`. `__lt__` compares values. |
| `__repr__` | instrument/core.py:48-49 | `f'{ClassName}({name})'` if name else `ClassName`, e.g. `IRSwap(x)`. |
| equality/hash | `@dataclass(unsafe_hash=True)` | Dataclass-generated, over **all fields including `name`, `asset_class`, `type_`**. `IRSwap('Pay','10y','USD',name='a') == …name='b'` → `False`; same name → `True`. The hash is recomputed from the current values, so it **changes after in-place mutation** (the comment at `generic_engine.py:275` works around exactly this). |
| `instrument_quantity` | base.py:584-586 | `self.quantity_`. `quantity_` is an `InitVar` defaulting to 1, so it returns `1`. Used by `predefined_asset_engine.py:52`. |
| `properties()` / `properties_init()` | base.py:375-383 | Sets of public field names with a trailing `_` stripped. IRSwap: 33 / 31. |
| `default_instance()` | base.py:406-412 | `cls(**{f.name: default for init fields})`. IRSwap → `{'fee': 0.0, 'asset_class': Rates, 'type': Swap}`. |

### 3.2 Methods backtests call on instruments

| method | signature | semantics (exact) | used in backtests at |
|---|---|---|---|
| `clone` | `clone(self, **kwargs)` | `Base.clone` = `dataclasses.replace(self, **kwargs)` (base.py:358-373). `InstrumentBase.clone` (base.py:628-634) then copies `unresolved`, `metadata`, `resolution_key` from the source onto the new object. **So a clone of a resolved trade is still "resolved".** | actions.py:155,159,228,232,280,284,375,386,390,433,435; generic_engine.py:119,571,891 (`clone(name=f'{name}_{date}')`, `clone(**{size_parameter: x, 'name': …})`) |
| `name` | field | Plain field. Backtests rename trades as `f'{action.name}_Priceable{i}'`, `f'{action.name}_{p.name}'`, `f'{name}_{date}'`, `f'{old}_{YYYY-MM-DD}'`, and match with `self.action.priceable.name.split('_')[-1] in trade.name` (generic_engine.py:566). | many |
| `resolve` | `resolve(self, in_place: bool = True)` | instrument/core.py:74-114. Runs `self.calc(ResolvedInstrumentValues, fn=handle_result)`. `in_place=True` under a `HistoricalPricingContext` → `RuntimeError('Cannot resolve in place under a HistoricalPricingContext')`. In place: on success `self.from_instance(result)` copies every init field plus the private `__unresolved`/`__resolution_key`, and returns `None`. Not in place: returns the resolved instrument, or under a historical context a `{date: instrument}` per date (on error, `{risk_key.date: None}` with an error logged). Future-or-value semantics as for `calc`. | generic_engine.py:384 (`Portfolio(p).resolve(in_place=False)` under HPC), :902 (`init_port.resolve()` under `PricingContext(start)`) |
| `resolved` | `resolved(self, values: dict, resolution_key: RiskKey)` | base.py:619-626. `all = self.as_dict(True); all.update(values); new = self.from_dict(all); new.name = self.name; new.__unresolved = copy.copy(self); new.__resolution_key = resolution_key`. **This is the resolution contract to copy:** the resolved object remembers its unresolved original. | internal |
| `unresolved` | property | `None` until resolved, then a shallow copy of the pre-resolution instrument. | actions.py:429 (`if self.priceable.unresolved is None`) |
| `resolution_key` | property | `RiskKey(provider, date, market, params, scenario, risk_measure)` of the resolution, or `None`. | `Instrument.compose` |
| `metadata` | property + setter | Free slot. Carried over by `clone`. | triggers.py (TriggerInfo) |
| `calc` | `calc(self, risk_measure: RiskMeasure \| Iterable[RiskMeasure], fn=None)` | instrument/core.py:116-214. One measure → `PricingContext.current.calc(self, m)`. An iterable → `MultipleRiskMeasureFuture(self, {m: future})`, whose result is a `MultipleRiskMeasureResult` (a dict keyed by measure). `fn` post-processes. **Return:** `future if self._return_future else future.result()`. | backtest_objects.py:419 (`instrument.calc(self.scaling_type)` under `PricingContext(state)`), :791 (`swap.calc(Cashflows)`) |
| `_return_future` | property (priceable.py:37-42) | `True` iff the current context is `nullcontext` (already entered), `is_async`, or `is_entered`. So **inside `with PricingContext(...)` you get futures, resolved on `__exit__`**; outside, you get values immediately. | — |
| `price` | `price(self, currency=None)` | `self.calc(Price(currency=currency)) if currency else self.calc(Price)` (priceable.py:76-89). PV in **local (notional) currency** unless a currency is given. | — |
| `dollar_price` | `dollar_price(self)` | `self.calc(DollarPrice)`. PV in **USD**. | — (040307 uses the measure `DollarPrice` directly) |
| `scale` / `flip` | `scale(self, scaling: float, in_place: bool = True, check_resolved=True)`; `flip(in_place=True)` = `scale(-1, in_place)` | instrument/core.py:298-311. `scaling is None` → returns self. No `scale_in_place` → `NotImplementedError`. In place → mutates and returns **`None`**. Not in place → `deepcopy(self)` then scales the copy (the deepcopy keeps `name`). | generic_engine.py:121,280,293,299,1044; backtest_utils.py:93; strategy_systematic.py:128 (`check_resolved=False`) |
| `as_dict` | `as_dict(self, as_camel_case: bool = False) -> dict` | base.py:385-404. Non-None fields, enum **objects kept** (not strings), JSON field mapping applied (`type_` → `type`). **Includes `name`.** `fee=0.0` is included because it is not None. | strategy_systematic.py:285 (on params) |
| `to_dict` | from `dataclass_json` | camelCase keys, `exclude_none` fields dropped, **`name` always dropped**, dates → ISO strings (`termination_date=date(2030,1,2)` → `'2030-01-02'`), enums kept as enum objects. **Returns a plain `dict` (MRO `(dict, object)`), which is unhashable.** generic_engine.py:533-544 (the ExitTradeAction path, commented `# to_dict omits name`) does `set(t.to_dict() for t in trade.all_instruments)` / `{trade.to_dict()}`. In 1.5.4 **that raises `TypeError: unhashable type: 'dict'`** whenever the branch runs, so it is a latent gs bug. The intent is "match trades ignoring `name`". pricebt should implement that intent (e.g. compare `frozenset`/tuple of the name-less items, or a `name`-excluding key) rather than copy the line. | generic_engine.py:536,538,544 |
| `Instrument.from_dict` | classmethod `from_dict(cls, values: dict)` | instrument/core.py:216-248. Empty → `None`. On a concrete class, uses that class. On `Instrument` itself, reads `asset_class`/`assetClass` and `type` (popped from the dict), dispatches through the `(AssetClass, AssetType) → class` map built from all `target.instrument` classes (plus `(Cash, Currency) → Forward`), then calls `cls.from_dict(values)`. Missing keys → `ValueError('assetClass/asset_class not specified')` / `ValueError('type not specified')`. Unknown → `ValueError('unable to build instrument')`. Round trip: `Instrument.from_dict(swap.to_dict())` gives an `IRSwap` **without its name**, so it is `!=` the original. | — |
| `from_instance` | `from_instance(self, instance)` | Same type required (`ValueError` otherwise). Copies all init fields, plus `__unresolved` and `__resolution_key` for instruments. | via `resolve` |
| `compose` | staticmethod `compose(components)` | `{c.resolution_key.date (or risk_key.date for ErrorValue): c}` → the historical resolve result dict. | — |

Deepcopy keeps `name` (`copy.deepcopy(s).name == 'x'`). Instruments are **mutable** (`s.notional_amount = 5` works).

---

## 4. Enums (`gs_quant.common` / `gs_quant.target.common`), exact `(name, value)`

All derive from `(EnumBase, Enum)` apart from the `PositionType`/`DateLimit` helpers. String input is matched case-insensitively on **value**.

| enum | defining module | members `name → value` |
|---|---|---|
| `PayReceive` | `gs_quant.common` | `Pay→'Pay'`, `Receive→'Rec'`, `Straddle→'Straddle'`; plus the aliases `'receive'`/`'receiver'` → `Receive` |
| `BuySell` | target.common | `Buy→'Buy'`, `Sell→'Sell'` |
| `AggregationLevel` | target.common | `Type→'Type'`, `Asset→'Asset'`, `Class→'Class'`, `Point→'Point'` |
| `OptionType` | target.common | `Call`, `Put`, `Forward`, `Binary_Call→'Binary Call'`, `Binary_Put→'Binary Put'`, `Digital_Call→'Digital Call'`, `Digital_Put→'Digital Put'` |
| `OptionStyle` | target.common | `European`, `American`, `Bermudan`, `Asian` |
| `DayCountFraction` | target.common | `ACT_OVER_360→'ACT/360'`, `ACT_OVER_360_ISDA→'ACT/360 ISDA'`, `ACT_OVER_365_Fixed→'ACT/365 (Fixed)'`, `ACT_OVER_365_Fixed_ISDA→'ACT/365 Fixed ISDA'`, `ACT_OVER_365L_ISDA→'ACT/365L ISDA'`, `ACT_OVER_ACT_ISDA→'ACT/ACT ISDA'`, `ACT_OVER_ACT_ISMA→'ACT/ACT ISMA'`, `_30_OVER_360→'30/360'`, `_30E_OVER_360→'30E/360'` |
| `BusinessDayConvention` | target.common | `Following`, `Modified_Following→'Modified Following'`, `Previous`, `Unadjusted` |
| `SwapClearingHouse` | target.common | `LCH`, `EUREX`, `JSCC`, `CME`, `NONE` |
| `PrincipalExchange` | target.common | `_None→'None'`, `Both`, `First`, `Last` |
| `SwapSettlement` | target.common | `Phys_CLEARED→'Phys.CLEARED'`, `Physical`, `Cash_CollatCash→'Cash.CollatCash'`, `Cash_PYU→'Cash.PYU'` |
| `AccrualConvention` | target.common | `Adjusted`, `Unadjusted` |
| `UnderlierType` | target.common | `BBID`, `BID`, `CUSIP`, `ISIN`, `SEDOL`, `RIC`, `Ticker` |
| `TradeAs` | target.common | `Listed`, `Listed_Look_alike_OTC→'Listed Look alike OTC'`, `Flex`, `OTC` |
| `OptionSettlementMethod` | target.common | `Cash`, `Physical`, `ElectDfltCash`, `ElectDfltPhys`, `NetShares` |
| `OptionExerciseStyle` | target.common | `Auto`, `Manual` |
| `RiskMeasureUnit` | target.common | `Percent`, `Dollar`, `BPS`, `Pips` |
| `AssetClass` | target.common | values: `Cash, Commod, Credit, Cross Asset, Digital Asset, Debt, Econ, Equity, Fund, FX, ListedDerivative, Mortgage, Rates, Repo, Loan, Social, Cryptocurrency` |
| `AssetType` | target.common | 161 members. Relevant values: `Swap, Swaption, Option, Forward, Cash, InflationSwap, Cap, Floor, FRA, XccySwap, Future, Bond` |
| `Currency` | target.common (re-exported by common) | **341 members**, ISO-4217 codes with `name == value` (the first member is `_ → ''`). Includes all G10 and EM codes used here: `USD EUR GBP JPY CHF AUD CAD NZD SEK NOK DKK CNY CNH HKD SGD KRW INR MXN BRL ZAR PLN CZK HUF TRY ILS`. `Currency('usd')` → `Currency.USD`. |
| `CurrencyName` | target.common | `United_States_Dollar→'United States Dollar'`, `Euro→'Euro'`, `Pound_Sterling`, `Japanese_Yen`, … (18 members) |

Also in `gs_quant.common`: `PositionType`, `DateLimit`, `RiskMeasure` (common.py:61, `__lt__` sorts by name, then parameters) and `ParameterisedRiskMeasure` (common.py:86).

`get_enum_value(enum_type, value)` (base.py:677-693) is **lenient**: `None` → `None`; an invalid value → a logged warning and the raw value is returned. Setattr coercion (§3.1) is **strict**: an invalid value raises.

---

## 5. `Portfolio` (`gs_quant.markets.portfolio`, markets/portfolio.py:44-622)

`@dataclass class Portfolio(PriceableImpl)` with a custom `__init__`:

```
Portfolio(priceables: Union[PriceableImpl, Iterable[PriceableImpl], dict, None] = (), name: Optional[str] = None)
```

- A dict input **mutates each value's `.name` to its key**, then stores the values in order (lines 62-68).
- A single priceable → a 1-tuple. An iterable → a tuple. Elements may be instruments or nested `Portfolio`s.
- The notebooks use all of these forms: `Portfolio(port, name='Seagull')` (list), `Portfolio(name='portfolio', priceables=[call, put])`, `Portfolio(active_portfolio)`, `Portfolio(tuple(...))`, `Portfolio([p.trade])`.

| member | exact semantics | backtests usage |
|---|---|---|
| `priceables` (prop, setter, deleter) | Tuple of direct children. The setter rebuilds a `name → [idx]` map for children that have names. | actions.py, generic_engine.py |
| `instruments` | Direct children that are `Instrument`s, deduplicated in order (`unique_everseen`, which uses the instrument hash/eq). | — |
| `all_instruments` | Direct instruments, then those of every nested portfolio, deduplicated (lines 199-202). **Two identical trades with the same name collapse into one.** | backtest_objects.py:346,458,508…; generic_engine.py many |
| `portfolios` / `all_portfolios` | Direct and recursive sub-portfolios. | — |
| `__len__`, `__iter__` | Over **direct** children. | `for trade in backtest.portfolio_dict[state]` |
| `__getitem__(item)` | int/slice → children; `PortfolioPath` → the node; str/Instrument → all matches through `paths()` (returns one element if there is one match, else a tuple); list → concatenated. | `seagull[0]`, `initial_portfolio[index]` |
| `__contains__(item)` | Instrument: identity/eq in any (sub)portfolio's children. str: a name in any name map. | — |
| `paths(key)` | Matches by name (str), or by `p == key or p.unresolved == key` (Instrument/Portfolio), recursively. Any other key → `ValueError`. | via results |
| `__hash__` | `hash(name) ^ hash(id) ^ XOR(hash(children))` | — |
| `__eq__` | Other is a Portfolio **and**, for every leaf path of self, `path(self) == path(other)`. Names are not compared at the portfolio level. | — |
| `__add__(other)` | Portfolio only: `Portfolio(self.children + other.children)`, **with no name**. | — |
| `append(p)` / `extend(it)` / `pop(item)` | Tuple concatenation. `pop` keeps the `instruments` not equal to the popped item, which **drops nested portfolios**. | — |
| `calc(risk_measure, fn=None) -> PortfolioRiskResult` | Under `self._pricing_context`: `PortfolioRiskResult(self.clone(), measures_tuple, [p.calc(risk_measure, fn) for p in children])`. The result holds a **shallow clone** of the portfolio. | generic_engine.py:94,230,234,950,959,976,1007,1086,1128,1131 (always `tuple(risks)` or a single measure, always inside `PricingContext(pricing_date=d)` nested in an outer `PricingContext()` or `is_async=True`) |
| `resolve(in_place=True)` | Calls `p.resolve(in_place)` on each child under the context. Not in place: returns `Portfolio(name=self.name)` with the resolved children, or under HPC a `{date: Portfolio(resolved_children, name)}` (a date is skipped with an error log if any child failed). In place returns `None`. | generic_engine.py:384, :902 |
| `scale(scaling, in_place=True)` | In place: `inst.scale(scaling, True)` for each of `all_instruments`. Not in place: `Portfolio([inst.scale(scaling, False) for inst in all_instruments])`, which is **flattened, without a name**, and requires resolved instruments. | generic_engine.py:121,280,293,299,1044 |
| `clone(clone_instruments=False)` | New Portfolio with the same name; children are shared (cloned when the flag is set); nested portfolios are cloned recursively. **Takes no `**kwargs`.** | inside `calc` |
| `to_frame(mappings=None) -> DataFrame` | One row per leaf: `as_dict()` + `instrument` (the object) + `portfolio` (the parent's name). Index `['portfolio','instrument']`. Columns: `asset_class`, `type`, `$type` first (when present), then the rest sorted. | backtest_objects.py:313 (`info.portfolio.to_frame()`), 040307 (`seagull.to_frame().transpose()`) |
| `dollar_price()`, `price(currency=None)` | Inherited from `PriceableImpl`. | — |
| `id`, `quote_id`, `get/save/from_*` | Marquee persistence. **Not needed** (network). | — |

`Portfolio` has a `name` attribute but is not a field-level dataclass like instruments; `Portfolio(...).name` is set in `__init__`.

---

## 6. `PricingContext` / `HistoricalPricingContext` (`gs_quant.markets`)

Sources: markets/core.py:80-645 and markets/historical.py:30-128. Both are context managers (`ContextBaseWithDefault`); `PricingContext.current` is the ambient instance.

```
PricingContext(pricing_date: Optional[date] = None, market_data_location: Union[PricingLocation, str, None] = None,
               is_async: bool = None, is_batch: bool = None, use_cache: bool = None, visible_to_gs: Optional[bool] = None,
               request_priority: Optional[int] = None, csa_term: Optional[str] = None, timeout: Optional[int] = None,
               market: Optional[Market] = None, show_progress: Optional[bool] = None, use_server_cache: Optional[bool] = None,
               market_behaviour: Optional[str] = 'ContraintsBased', set_parameters_only: bool = False,
               use_historical_diddles_only: Optional[bool] = None, provider: Optional[Type[GenericRiskApi]] = None)

HistoricalPricingContext(start: Union[int, date, None] = None, end: Union[int, date, None] = None,
               calendars: Union[str, Tuple] = (), dates: Optional[Iterable[date]] = None, is_async: bool = None,
               is_batch: bool = None, use_cache: bool = None, visible_to_gs: bool = None, request_priority: Optional[int] = None,
               csa_term: str = None, market_data_location: Optional[str] = None, timeout: Optional[int] = None,
               show_progress: Optional[bool] = None, use_server_cache: Optional[bool] = None,
               provider: Optional[Type[GenericRiskApi]] = None)
```

| behaviour | rule (source) |
|---|---|
| `pricing_date` default | `business_day_offset(today(market_data_location), 0, roll='preceding')`, or inherited from the enclosing context (core.py:573-578). On Sunday 2026-09-27 it gave `2026-09-25`. |
| future-date guard | With no `market` and `pricing_date > date.today() + 5 days` → `ValueError('The PricingContext does not support a pricing_date in the future. …')` (core.py:162-168). `ScaledTransactionModel` returns `np.nan` for `state > today` (backtest_objects.py:416). |
| enter/exit | `_on_enter` snapshots the effective attributes. `_on_exit` runs `self.__calc()`, which **submits every pending (risk_key, instrument) request and fills the futures**, then resets the attributes. An exception inside the block is re-raised and nothing is computed (core.py:264-314). |
| `calc(instrument, risk_measure)` | Not for direct use. Builds `RiskKey(provider, pricing_date, market, parameters, scenario, measure)` and returns the pending `PricingFuture`, deduplicated on `(risk_key, instrument)`, so **pricebt must key its per-date cache by (date, measure, instrument)** (core.py:605-644). |
| nesting | Inner contexts inherit unset attributes (`_inherited_val`). The engine pattern is an outer `with PricingContext():` (or `is_async=True`) wrapping many inner `with PricingContext(pricing_date=d):` blocks; results become available once the **outer** block exits. `set_parameters_only=True` stops the context from swallowing inner contexts' submissions. |
| `csa_term` | "the csa under which the calculations are made. Default is local ccy ois index". Notebooks: `csa_term='USD-1'` (040307, passed to `run_backtest`), `HedgeAction(..., csa_term='EUR-OIS')` (040310). In pricebt this should be an **opaque string handed to the asset's market expression** (for example to pick the discount curve). |
| `market_data_location` | `PricingLocation` enum (`'LDN'` default, `'NYC'`, `'HKG'`, …). Default shown: `LDN`. |
| `clone(**kwargs)` | New instance built from the current values of every `__init__` parameter, overridden by kwargs. |
| engine's own context | `GenericEngine.new_pricing_context()` (generic_engine.py:655-677): `PricingContext(set_parameters_only=True, show_progress=…, csa_term=…, market_data_location=…, request_priority=…, is_batch=True default, use_historical_diddles_only=True)`, then `_max_concurrent = 1500`, `_dates_per_batch = 200`. |
| HPC date set | `start` **xor** `dates` (both → `ValueError('Must supply start or dates, not both')`; neither → `ValueError('Must supply start or dates')`). With `start`: `end` defaults to `date.today()` and `date_range = tuple(date_range(start, end, calendars))`. **`start` may be an int N**, meaning the last N business days ending at `end`, **in descending order** (see §8). `dates=` is used as given. |
| HPC calc | For each date: `RiskKey(provider, date, CloseMarket(date, location), …)`, collected in a `HistoricalPricingFuture`. The result is **composed**: a scalar measure → `SeriesWithInfo(index=dates as datetime.date)`; a DataFrame measure (ladder) → `DataFrameWithInfo` with the per-date frames concatenated plus a `date` column set as index (risk/core.py:134-138, 170-174, 411-415). `use_historical_diddles_only=True` is forced. |
| HPC usage in engine | generic_engine.py:383 `with HistoricalPricingContext(dates=states, csa_term=self.action.csa_term): f = Portfolio(self.action.priceable).resolve(in_place=False)` (hedge trades resolved once per date); :956 and :1004 `with HistoricalPricingContext(dates=p.dates): p.results = port.calc(tuple(risks))`. |
| HPC usage in notebook 040307 | `with HistoricalPricingContext(dates=backtest_dates_reduced, show_progress=True): ir_rates = seagull[0].calc([IRFwdRate, IRDailyImpliedVol]); entry = seagull.calc([DollarPrice])`, then `ir_rates.result().to_frame()` and `raw_data[IRFwdRate] *= 1e4` (percent → bp, see §9). |

Other exports of `gs_quant.markets` (from `dir`): `BackToTheFuturePricingContext, CloseMarket, LiveMarket, OverlayMarket, RelativeMarket, TimestampedMarket, LocationOnlyMarket, MarketDataCoordinate, PositionContext, PricingCache, PricingLocation, close_market_date, market_location, historical_risk_key`. Backtests only need `PricingContext` and `HistoricalPricingContext`. Portfolio uses `PositionContext.position_date`, and only for Marquee-backed portfolios.

---

## 7. `GsSession.use` (`gs_quant.session`, session.py:720-750)

```
GsSession.use(environment_or_domain: Union[Environment, str] = Environment.PROD, client_id: Optional[str] = None,
              client_secret: Optional[str] = None, scopes: Union[Iterable[Union[GsSession.Scopes, str]], str, None] = (),
              api_version: str = 'v1', application: str = 'gs-quant', http_adapter: requests.adapters.HTTPAdapter = None,
              use_mds: bool = False, domain: Domain = 'AppDomain') -> None      # classmethod
```

- Body: normalises an `Environment` to its `.name`, `None` domain → `MqError`, `session = cls.get(...)`, `session.init()` (**network: OAuth**), `cls.current = session`.
- `Environment`: `DEV=1`, `QA=2`, `PROD=3`.
- `GsSession.Scopes` values: `read_content, read_financial_data, read_product_data, read_user_profile, modify_content, modify_financial_data, modify_product_data, modify_user_profile, run_analytics, execute_trades`. `get_default()` returns the first four read scopes.
- Calls seen in notebooks: `GsSession.use(client_id=None, client_secret=None, scopes=('run_analytics',))`, `GsSession.use(client_id=None, client_secret=None, scopes=('run_analytics', 'read_product_data'))`, `GsSession.use(Environment.PROD, client_id=None, client_secret=None, scopes=('run_analytics',))`, `GsSession.use()`.
- **pricebt recommendation:** `pricebt.session.GsSession.use` with the **identical signature**, a no-op that records its arguments on `GsSession.current` (no network), plus an `Environment` enum with the same members. This keeps notebooks runnable unchanged.

---

## 8. `gs_quant.datetime` helpers used by backtests

### 8.1 Signatures (gs_quant/datetime/date.py, relative_date.py)

```
business_day_offset(dates: date | Iterable[date], offsets: int | Iterable[int], roll: str = 'raise',
                    calendars: str | Tuple[str, ...] = (), week_mask: Optional[str] = None) -> date | Tuple[date, ...]
is_business_day(dates, calendars=(), week_mask=None) -> bool | Tuple[bool, ...]
prev_business_date(dates=<date.today() at import time>, calendars=(), week_mask=None) -> date | Tuple[date, ...]
date_range(begin: int | date, end: int | date, calendars=(), week_mask=None) -> Iterable[date]    # a generator
business_day_count(begin_dates, end_dates, calendars=(), week_mask=None) -> int | Tuple[int, ...]
today(location: Optional[PricingLocation] = None) -> date

RelativeDate(rule: str, base_date: Optional[date] = None)
    .apply_rule(currencies: List[Currency|str] = None, exchanges: List[ExchangeCode|str] = None,
                holiday_calendar: List[date] = None, week_mask: str = '1111100', **kwargs) -> date
    .as_dict() -> {'rule': rule, 'baseDate'?: str}
RelativeDateSchedule(rule: str, base_date: Optional[date] = None, end_date: Optional[date] = None)
    .apply_rule(currencies=None, exchanges=None, holiday_calendar=None, week_mask='1111100', **kwargs) -> List[date]
```

- `business_day_offset` is `np.busday_offset(dates, offsets, roll, busdaycal=GsCalendar.get(calendars).business_day_calendar(week_mask))`, with an ndarray result → tuple. **The default `roll='raise'` raises `ValueError('Non-business day date in busday_offset')` for a weekend input.**
- `is_business_day` on a list returns a tuple of `np.bool_`.
- `prev_business_date(d)` = `business_day_offset(d, -1, roll='forward')`.
- `date_range(date, date)` steps forward by `business_day_offset(prev, 1)` from `begin` (**`begin` is yielded even if it is not a business day**; `begin > end` → `ValueError`). `date_range(date, int n)` yields `business_day_offset(begin, i)` for `i in range(n)`. `date_range(int n, date)` yields `business_day_offset(end, -i, roll='preceding')` for `i in range(n)`, i.e. **descending**.
- Base date: when `base_date` is None, `RelativeDate`/`RelativeDateSchedule` use `PricingContext.current.pricing_date` if a context is entered, else `date.today()`. Datetimes and Timestamps are converted with `.date()`.
- Holidays (`rules.RDateRule._get_holidays`): `holiday_calendar` (a list of dates) **takes precedence**; otherwise `GsCalendar(exchanges + currencies).holidays`, which returns `[]` without any network call when both are empty. With non-empty codes it queries the `HOLIDAY`/`HOLIDAY_CURRENCY` datasets (network). Any exception → a warning and `[]`. **pricebt must support `holiday_calendar: List[date]` (the backtest API passes it through as `holiday_calendar`); currency and exchange codes can be local calendar files or ignored.**

### 8.2 Rule grammar (`RelativeDate._get_rules` / `__handle_rule`, rules.py)

The rule string is split into `[sign][digits][letter]` tokens that are applied **left to right**, so `'1m+2b'` or `'-1y'` work. The letter selects `rules.<letter>Rule`; an unknown letter → `NotImplementedError(f'Rule {rule} not implemented')`; `''` → `MqValueError('Invalid Rule ""')`. `roll_convention=` passed as a kwarg to `apply_rule` overrides the default roll for rules that use `roll_convention(default)`.

| letter | meaning | business-day handling (numpy roll) |
|---|---|---|
| `b` | n business days | `busday_offset(result, n, roll)`, roll = `'forward'` if n<=0 else **`'preceding'`** |
| `u` | n business days (variant) | roll: `'preceding'` if sign `-` and n==0; `'forward'` if n<=0; else `'preceding'` |
| `d` | n calendar days | none |
| `w` | n weeks | + n weeks, then roll offset 0 `'forward'` if n>=0 else `'backward'` |
| `g` | n weeks | + n weeks, then roll `'backward'` |
| `m` | n months (`relativedelta`, clamps to month end) | then roll offset 0 **`'forward'`** |
| `y` | n years | + n years; while weekday is masked (weekend) +1 day; then roll `'backward'` |
| `k` | same as `y` | same |
| `e` | end of the current month (calendar) | none |
| `x` | end of month | roll `'backward'` |
| `v` | + n months, then end of month | roll `'backward'` |
| `J` | first day of month | none |
| `A` | Jan 1 of the year, with `relativedelta(year=n)` | none |
| `r` | Dec 31 + n years | none |
| `N/U/X/S/G/I/P` | next n-th Mon/Tue/Wed/Thu/Fri/Sat/Sun (`relativedelta(weekday=MO(n))` …) | none |
| `M/T/W/R/F/V/Z` | n-th Mon/Tue/Wed/Thu/Fri/Sat/Sun of the month | none |

### 8.3 Verified outputs (weekends only, no calendars): use these as pricebt test fixtures

Base dates: 2024-01-31 (Wednesday) and 2024-06-01 (Saturday).

| rule | from 2024-01-31 | from 2024-06-01 (Sat) |
|---|---|---|
| `'10y'` | 2034-01-31 | 2034-06-01 |
| `'1y'` | 2025-01-31 | 2025-06-02 |
| `'30y'` | 2054-02-02 | 2054-06-01 |
| `'5y'` | 2029-01-31 | 2029-06-01 |
| `'6m'` | 2024-07-31 | 2024-12-02 |
| `'3m'` | 2024-04-30 | 2024-09-02 |
| `'1m'` | 2024-02-29 | 2024-07-01 |
| `'1w'` | 2024-02-07 | 2024-06-10 |
| `'2b'` | 2024-02-02 | 2024-06-04 |
| `'0b'` | 2024-01-31 | 2024-06-03 |
| `'-1b'` | 2024-01-30 | 2024-05-31 |
| `'1d'` | 2024-02-01 | 2024-06-02 |

`RelativeDateSchedule(freq, 2024-01-31, 2024-06-03).apply_rule()`:

| freq | n | dates |
|---|---|---|
| `'1b'` | 89 | 01-31, 02-01, 02-02, 02-05, … 05-31, 06-03 |
| `'1w'` | 18 | 01-31, 02-07, 02-14, … 05-22, 05-29 |
| `'2w'` | 9 | 01-31, 02-14, 02-28, … 05-08, 05-22 |
| `'1m'` | 5 | 01-31, **02-29**, **04-01** (the 2m step lands on Sun 03-31 and rolls forward), 04-30, 05-31 |
| `'3m'` | 2 | 01-31, 04-30 |

More checks:
- `'1b'` from 2024-05-24 to 2024-05-31 with `holiday_calendar=[2024-05-27]` → `[05-24, 05-28, 05-29, 05-30, 05-31]`.
- `'1b'` from **Sat** 2024-06-01 to 2024-06-07 → `[06-01, 06-03, 06-04, 06-05, 06-06, 06-07]`. The **raw Saturday start is kept**.
- `business_day_offset(Sat 2024-06-01, -1, roll='preceding')` → 2024-05-30. `(…, 0, roll='forward')` → 2024-06-03. `business_day_offset(2024-06-03, 1)` → 2024-06-04.
- `prev_business_date(2024-06-03)` → 2024-05-31.
- `date_range(2024-05-30, 2024-06-04)` → `[05-30, 05-31, 06-03, 06-04]`.

Schedule algorithm (relative_date.py:246-258), to reproduce exactly:

```python
i = 1; schedule = [self.base_date]
while True:
    rule = f'{int(self.rule[:-1]) * i}{self.rule[-1]}'      # only a single "<int><letter>" rule is supported
    result = RelativeDate(rule, self.base_date).apply_rule(currencies, exchanges, holiday_calendar, week_mask, **kwargs)
    if self.end_date is None or result > self.end_date: break
    i += 1; schedule.append(result)
return schedule
```

Backtest usage: `GenericEngine.run_backtest(..., frequency='1m')` default → `RelativeDateSchedule(frequency, start, end).apply_rule(holiday_calendar=holiday_calendar)` (generic_engine.py:771-775), then `.sort()`. `PeriodicTriggerRequirements` does the same (triggers.py:117). Trade durations such as `'1m'`/`'1w'`/`'2b'` → `RelativeDate(duration, create_date).apply_rule(holiday_calendar=…)`, cached by `(inst, create_date, duration, holiday_calendar)` (backtest_utils.py:64-89). Notebook 040307: `end = business_day_offset(date.today(), -1, roll='preceding')`; frequency string `'1w'`, holding period `'4w'`.

---

## 9. Risk measures (`gs_quant.risk`) — objects notebooks pass to `risks=` / `HedgeAction` / triggers

Source classes: `gs_quant/target/measures.py`, `gs_quant/common.py:61-116`. A measure is a `RiskMeasure` dataclass with `asset_class, measure_type, unit, parameters, value, name`. The `ParameterisedRiskMeasure` subclasses are **callable** and return a *copy* with parameters set.

| measure | class | `measure_type` | `unit` | `asset_class` | callable | meaning (Measures tutorial) |
|---|---|---|---|---|---|---|
| `Price` | `RiskMeasureWithCurrencyParameter` | `PV` | None | None | yes: `(currency=None, name=None)` | PV in **local** ccy unless `currency` given |
| `DollarPrice` | `RiskMeasure` | `Dollar Price` | None | None | **no** | PV in USD |
| `IRDelta` | `RiskMeasureWithFiniteDifferenceParameter` | `Delta` | None | Rates | yes | "Change in Dollar Price (**USD** PV) due to individual **1bp** moves in the instruments building the discount curve" |
| `IRDeltaParallel` | FD param, preset `aggregation_level=Asset` | `Delta` | None | Rates | yes | parallel 1bp |
| `IRGamma` | `RiskMeasure` | `Gamma` | None | Rates | no | change in aggregated IRDelta per 1bp |
| `IRVega` | FD param | `Vega` | None | Rates | yes | per 1bp normal vol |
| `IRBasis`, `IRXccyDelta`, `InflationDelta` | FD param | `Basis` / `XccyDelta` / `InflationDelta` | None | Rates | yes | 1bp |
| `IRFwdRate` | `RiskMeasure` | `Forward Rate` | **Percent** | Rates | no | par rate **in percent** (040307 multiplies by 1e4) |
| `IRSpotRate` | `RiskMeasure` | `Spot Rate` | Percent | Rates | no | ATM spot rate |
| `IRDailyImpliedVol` | `RiskMeasure` | `Daily Implied Volatility` | **BPS** | Rates | no | daily normal vol in bp |
| `FXDelta` | FD param | `Delta` | None | FX | yes | |
| `EqDelta`, `EqVega` | `RiskMeasureWithCurrencyParameter` | `Delta` / `Vega` | None | Equity | yes | |
| `Cashflows` | `RiskMeasure` | `Cashflows` | None | None | no | backtest_objects.py:791 |
| `ResolvedInstrumentValues` | `RiskMeasure` | `Resolved Instrument Values` | None | None | no | the resolution measure |

Call signatures:
- `RiskMeasureWithFiniteDifferenceParameter.__call__(aggregation_level=None, bump_size=None, currency=None, finite_difference_method=None, local_curve=None, mkt_marking_options=None, scale_factor=None, name=None)`. Each unset argument **inherits** the existing parameter value (target/measures.py:193-215).
- `RiskMeasureWithCurrencyParameter.__call__(currency=None, name=None)` stores `CurrencyParameter(value=currency)` in `parameters`, which is why `repr(Price(currency='USD')) == 'Price(value:USD)'` and `.currency == 'USD'`.
- A pandas Series/DataFrame passed as the first argument returns `self` (a pandas `.loc` hack).

Equality and hashing (verified): `Price(currency='USD') != Price` and the hashes differ; `Price() != Price` (the parameter object vs `None`); `IRDelta(aggregation_level='Type')(currency='EUR') == IRDelta(aggregation_level='Type', currency='EUR')`. String `aggregation_level` is coerced to `AggregationLevel.Type`. `repr` = `Name(k:v, …)` with keys sorted case-insensitively (common.py:103-113). Sorting uses the name first (common.py:61-84).

Usage in notebooks:
- `IRDelta(aggregation_level='Type', currency=Currency.USD)`, `IRDelta(aggregation_level=AggregationLevel.Type, currency='USD')`, `IRDelta(currency='local', aggregation_level=AggregationLevel.Type)`, `IRDelta(aggregation_level='Type')`.
- `FXDelta(aggregation_level='Type', currency='USD')`, `Price`, `result_summary[Price]`, `result_summary[FXDelta(aggregation_level=AggregationLevel.Type, currency='USD')]`. **The key must be an equal measure object.**

**Multi-currency (`result_ccy`)**, generic_engine.py:796-810: when `run_backtest(result_ccy=…)` is set, **every** risk becomes `r(currency=result_ccy)`. A risk that is not a `ParameterisedRiskMeasure` (e.g. `DollarPrice`, `IRFwdRate`, `IRGamma`) → `raiser(f'Unparameterised risk: {r}')`. The price measure becomes `Price(currency=result_ccy)`. `result_summary` is then keyed by the rewritten measures.

Currency semantics to reproduce: `Price` = local ccy; `IRDelta` with no currency = **USD**; `currency='local'` = the instrument's own ccy; `currency='EUR'` = converted.

Result shapes from `calc` (full detail in note 03):
- Scalar measures and aggregated ladders (`aggregation_level='Type'`) → `FloatWithInfo` (a float subclass with `.risk_key`, `.unit`, `.error`, `.request_id`).
- An un-aggregated IRDelta → `DataFrameWithInfo` with columns **`mkt_type, mkt_asset, mkt_class, mkt_point, mkt_quoting_style, value`** (risk/result_handlers.py:217-224), sortable by `('date','time','mkt_type','mkt_asset','mkt_class','mkt_point')` (risk/core.py:33).
- Several measures → `MultipleRiskMeasureResult` (a dict keyed by measure).
- Portfolio → `PortfolioRiskResult`: `aggregate(allow_mismatch_risk_keys=False, allow_heterogeneous_types=False)`, `to_frame(values='default', index='default', columns='default', aggfunc='sum', display_options=None)`, `subset(items, name=None)`, indexing by measure / instrument / name / int / date.
- Errors → `ErrorValue(risk_key, error, request_id=None)`.

---

## 10. `gs_quant.data.Dataset.get_data` (call shape only)

```
Dataset(dataset_id: Union[str, Dataset.Vendor], provider: Optional[DataApi] = None)
Dataset.get_data(start: date|datetime|None = None, end: date|datetime|None = None, as_of: Optional[datetime] = None,
                 since: Optional[datetime] = None, fields: Optional[Iterable[str|Fields]] = None,
                 asset_id_type: Optional[str] = None, empty_intervals: Optional[bool] = None,
                 standard_fields: Optional[bool] = False, **kwargs) -> pd.DataFrame
Dataset.get_data_series(field, start=None, end=None, as_of=None, since=None, dates: Optional[List[date]] = None,
                        standard_fields=False, **kwargs) -> pd.Series
DataFrequency: DAILY='daily', REAL_TIME='realTime', ANY='any'
```

- `**kwargs` become query filters (`assetId=['MA5WM2QWRVMYKDK0']`, `city=('Boston','Austin')`). The result is a DataFrame indexed by date/time with one column per dataset field (data/dataset.py:170-206). Network only.
- Notebook 040304: `ds = Dataset('SWAPRATES_STANDARD'); data = ds.get_data(start_date, assetId=['MA5WM2QWRVMYKDK0']); data_10y = data.loc[data['tenor'] == '10y']; s = pd.Series(data_10y['rate'].to_dict()); GenericDataSource(s, MissingDataStrategy.fill_forward)`. Columns used: `tenor`, `rate`. 040306: `FXSPOT_PREMIUM` → `spot`. 040314: `TREOD` → `closePrice`.
- The backtest itself only consumes the resulting **`pd.Series` indexed by date**. For the SOFR port this series should come from the user's own data (the ERIS par rate from the asset's `par_rate` function), not from a `Dataset`.

---

## 11. Implications for the pricebt v2 design (facts, not decisions)

1. **The instrument object is the unit of identity.** The engine hashes it, compares it, renames it with `clone(name=…)`, dedupes it in `all_instruments` and scales it. A pricebt instrument class must reproduce: dataclass eq/hash over all fields including `name`, `clone` = replace plus resolution state carried over, `scale`/`scale_in_place` with the resolved-only guard, `to_dict` without `name`, and `as_dict` with `name`.
2. **The "opaque kwargs" in the asset config are exactly the constructor kwargs above.** `IRSwap(pay_or_receive=…, termination_date='10y', notional_currency='USD', notional_amount=1e4, fixed_rate='ATM', name=…)` must be accepted verbatim, including camelCase variants and positional order. Relative values (`'10y'`, `'ATM+25'`, `'100k'`) stay strings until `resolve()` under a pricing date. That is where pricebt calls the asset config's trade-construction expression with `pricebt_timestamp`/pricing date and the market.
3. **Resolution state:** keep `unresolved` (the original) and `resolution_key` (at least the date) on the resolved copy. `paths()` also matches `unresolved == key`, so results can be looked up with the unresolved object.
4. **Futures:** gs semantics are "value outside a context, future inside, filled at the outermost exit". pricebt can compute eagerly and return an already-completed future object with `.result()`, which is enough for `calc(...).result()` and `with PricingContext(): f = x.calc(m)` → `f.result()` patterns.
5. **Units:** IR risk measures are **per 1bp**. `IRFwdRate` is in **percent**, not decimal and not bp. `IRDailyImpliedVol` is in bp. `Price` is local ccy; `IRDelta` defaults to USD. `fixed_rate` numerics are **decimals** (0.0325) while the `ATM±x` offsets are **bp**.

---

## 12. Open questions (for the design author)

- The **default `notional_amount`** of an unspecified `IRSwap`/`IRSwaption` is not documented anywhere in the repo docs. pricebt should either require it in the asset config defaults or pick an explicit value and document it.
- Should `pricebt.data.Dataset` exist at all? 040304 only uses it to fetch a rate series that becomes a `pd.Series`. Options: omit it and document a replacement, or ship a shim that reads local files keyed by dataset id.
- Which `IRSwap` fields must the SOFR/ERIS asset config honour (effective/termination/notional/fixed_rate/pay_or_receive/conventions) versus accept and ignore? Should unhonoured non-None fields raise, or be passed through opaquely?
- Should `result_ccy` + an unparameterised measure raise exactly like gs (`Unparameterised risk: …`) even though pricebt could convert any measure through its FX expression?
- Should `IRDelta` with no `currency` default to **USD** (gs behaviour) or to the reporting ccy?
