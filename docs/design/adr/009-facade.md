# ADR 009: The gs_quant facade is terms plus a session-registered spec

Status: accepted, built (`instrument.py`, `session.py`, `backtests/`). Requirements F-1..F-6, C1-C7.

## Decision

* `pricebt.instrument.IRSwap('Pay', '10y', 'USD', ...)` builds **terms** and does nothing else: it looks up the instrument spec registered for (asset class, currency), and the spec's library
  resolves dates and the par rate when the trade is filled. It imports no library and hardcodes no convention.
* `PricebtSession.use(market=..., stack=<a Stack object or "pricebt.<...>:STACK">, calendar=..., instruments={"swap:USD": name | raw spec})` replaces `GsSession.use()`. A `Stack` is the adapter's bundle: the `wrap`, the
  instrument specs it ships by name, the defaults `"<asset_class>:<CCY>" -> name` and `accepts_gs` (constructor values its conventions already imply, for example a floating-rate option name). The session
  also takes an override for one asset class and currency; the reference stack ships none (a spec needs the snapshot's calendar name).
* gs decimals become pricebt percent in the facade and nowhere else (`fixed_rate=0.04` is 4.0; `4.0` is refused with a message naming decimals). The gs field vocabulary is in ONE place
  (`backtests/gs_fields.py`); `ATMF+25` becomes the token `par` plus `extras.par_spread_bp`. Every argument problem is reported before a session is needed, and the spec lookup is last.
* Named calendars are not known to core: `resolve_calendar` goes through the `CALENDARS` registry that adapters and config populate (default: weekends only). The reporting currency defaults to USD and is checked per session.
* The gs `1 + r/365` cash accrual lives in `backtests/` (allow-listed as gs behaviour); core cash accrual takes its basis and compounding as explicit parameters with no default (`accrual.py`).
* `IRSwaption` and every other gs instrument raise `NotSupportedError` with a pointer. Swaptions and volatility are out of scope.

## Enforcement

`tests/test_facade_terms.py` (mutation-checked: 18 mutants on `instrument.py`, 12 on `session.py`), `tests/test_gs_compat_*.py`, `tests/test_stacks.py`, and guard Z4.
