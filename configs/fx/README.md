# Writing an FX config

An FX config is a self-contained YAML file, loaded with `pricebt.assets.load_fx`, that answers
"how many units of `quote` per 1 unit of `base`, on date `d`" for whichever currency pairs it
chooses to support. It is used only when a risk measure asks for a currency conversion
(`currency='USD'` on a measure, or `run_backtest(result_ccy=...)`) — see
[`../../docs/v2/ASSET_CONFIG_GUIDE.md`](../../docs/v2/ASSET_CONFIG_GUIDE.md) for the full asset-config
contract this shares (`schema_version`, `imports`, `code`, lazy execution).

## Schema (DESIGN.md §4.5)

```yaml
schema_version: 1
fx: my_fx_table                            # required id
imports: |                                 # optional, exec'd once (lazily)
  import pandas as pd
code: |                                    # optional, exec'd once after imports
  _T = pd.read_csv(r"C:\data\fx_fixings.csv", index_col=0, parse_dates=True)    # columns like EURUSD
  def rate(base, quote, d):
      col, inv = f"{base}{quote}", f"{quote}{base}"
      if col in _T: return float(_T[col].asof(pd.Timestamp(d)))
      if inv in _T: return 1.0 / float(_T[inv].asof(pd.Timestamp(d)))
      return None
rate: 'rate(base, quote, pricebt_date)'    # required: QUOTE units per 1 BASE unit; None = unavailable
```

`base`, `quote` (ISO codes) and `pricebt_date` are the only names injected into `rate` — see the
injected-variables table in the asset config guide. Returning the *reciprocal* rate for the
opposite direction, as above, is a convention this file adopts, not something pricebt enforces:
`rate` is free to compute each direction however it likes, or to return `None` for a direction it
doesn't support.

`PricingService.fx(from_ccy, to_ccy, date)` never evaluates anything when `from_ccy == to_ccy`
(returns `1.0`); otherwise it calls `rate(base=from_ccy, quote=to_ccy, d=date)` and raises
`MarketDataUnavailable` on a `None` or non-positive result.

## Worked example

[`../../tests/assets/toy_fx.yaml`](../../tests/assets/toy_fx.yaml) is a real, working FX config —
a single sinusoidal `EURUSD` series (deterministic, from `tests/toylib/rates.py`) with `USDEUR`
computed as its reciprocal, both served from one `rate` function:

```yaml
schema_version: 1
fx: toy_fx
imports: |
  import math
  import toylib.rates as tr
code: |
  def _eurusd(d):
      n = tr.business_day_index(d)
      return 1.10 + 0.01 * math.sin(2 * math.pi * n / 252)

  def rate(base, quote, d):
      if (base, quote) == ("EUR", "USD"):
          return _eurusd(d)
      if (base, quote) == ("USD", "EUR"):
          return 1.0 / _eurusd(d)
      return None
rate: 'rate(base, quote, pricebt_date)'
```

One FX config can cover as many pairs as its `rate` function handles — write a second FX config
only when a different pair needs genuinely different sourcing (a different data file or provider),
not merely a different currency pair.

ARBS itself has no offline FX history, so a real ARBS-backed multi-currency book needs its own FX
config reading a user-supplied source (a CSV of fixings, another vendor, etc.) — pricebt has no
opinion on what that source is, only on the `rate(base, quote, d)` contract above.
