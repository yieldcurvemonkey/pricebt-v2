# Writing a market provider config

A market provider is a self-contained YAML file, loaded with `pricebt.assets.load_market`, that
answers one question: **what is the market object of date `d`** (for a location and a CSA)? An
asset config names it with `market: {provider: <name>}` instead of carrying a market expression of
its own. Design: [`../../docs/v2/MARKET_CONTEXT_DESIGN.md`](../../docs/v2/MARKET_CONTEXT_DESIGN.md);
it shares the asset-config contract in
[`../../docs/v2/ASSET_CONFIG_GUIDE.md`](../../docs/v2/ASSET_CONFIG_GUIDE.md) (`schema_version`,
`imports`, `code`, lazy execution, configs are trusted code).

## Schema

```yaml
schema_version: 1
market: usd_sofr_ois                       # the provider's name; defaults to the file stem
description: "USD SOFR OIS curve, end of day"
imports: |                                 # optional, exec'd once (lazily)
  import my_lib
code: |                                    # optional, exec'd once after imports
  def load_curve(d, location, csa):
      try:
          return my_lib.curves.eod("USD-SOFR", d, close=location, csa=csa)
      except my_lib.NoDataError:           # "no market for this date" is None, never an exception
          return None
requires: []                               # optional: other providers this market is built on
expr: 'load_curve(pricebt_date, pricebt_location, pricebt_csa)'   # required; None = unavailable
```

`expr` sees `pricebt_date`, `pricebt_timestamp`, `pricebt_datetime`, `pricebt_csa`,
`pricebt_location` (the `market_data_location` of the pricing context, `'LDN'` unless set) and
`markets` (the objects of the providers under `requires:`). It never sees an asset name: a market
has no single owning asset.

## Using it

```yaml
# configs/assets/usd_sofr_irs.yaml
market: {provider: usd_sofr_ois}           # instead of market: {expr: ..., key: ...}
```

```python
from pricebt.session import PricebtSession

PricebtSession.use(markets=["configs/markets"], assets=["configs/assets"])
```

Providers are registered before assets; an asset naming an unknown provider fails at registration
with the registered names and a did-you-mean.

## A market built on another

A vol surface is priced on a curve. List the curve under `requires:` and its object arrives in
`markets`:

```yaml
schema_version: 1
market: usd_swaption_vol
requires: [usd_sofr_ois]
imports: |
  import my_lib
expr: 'my_lib.vols.eod("USD", pricebt_date, curve=markets["usd_sofr_ois"])'
```

A swap asset on `usd_sofr_ois` and a swaption asset on `usd_swaption_vol` then price on **one**
curve object per date. If the curve has no market on a date, neither has the vol surface, and its
expression is not evaluated.

## What is evaluated when

One evaluation per (provider, date, csa, location), whichever assets ask, in the provider's own
namespace. `session.reset_caches()` (and the start of every `run_backtest`) forgets them;
`imports`/`code` still run only once per process.

## Worked example

[`../../tests/markets/toy_usd_ois.yaml`](../../tests/markets/toy_usd_ois.yaml) and
[`../../tests/markets/toy_usd_swaption_vol.yaml`](../../tests/markets/toy_usd_swaption_vol.yaml)
are working providers on the deterministic toy library in `tests/toylib/`, used by
`tests/test_market_provider.py` and `tests/test_market_context.py`.

The shipped real-library example,
[`../assets/usd_sofr_ois_interest_rate_swap.yaml`](../assets/usd_sofr_ois_interest_rate_swap.yaml),
still carries its market inline: it is verified only by an opt-in live tier, so it is split into a
provider only when that tier is rerun.
