# pricebt

A config-driven, event-driven backtester in the spirit of `gs_quant.backtests` (Strategy / Trigger / Action / signals / engine) whose **core has no dependence on any
pricing library, market-data infrastructure or vendor SDK**. Pricing libraries plug in through configuration only: a schema name is bound to a callable, market data arrives as plain
snapshots, and an adapter turns a snapshot into a library's objects. The same backtest can therefore be run under rateslib, QuantLib and a dependency-free reference stack and
the results tied out level by level.

```
schema name  --binding-->  callable            (what pricebt expects vs. how a library provides it: docs/design/adr/001)
provider     --snapshot--> wrap --> pricer      (plain data in, one place where a library object is built:   adr/004)
base config  + stack overlay (factory, bind, wrap)  -->  N runs  -->  tie-out report     (adr/006)
```

* **Start here:** [`notebooks/showcase_swap_book.ipynb`](notebooks/showcase_swap_book.ipynb), a monthly rebalance of a swap book to a target dollar delta, from passing in the
  library to the tearsheet. Architecture: [`docs/DESIGN.md`](docs/DESIGN.md); user guide: [`docs/guides/backtesting.md`](docs/guides/backtesting.md); decisions:
  [`docs/design/adr/`](docs/design/adr/README.md); what changed and why: [`docs/design/11-refactor-changelog.md`](docs/design/11-refactor-changelog.md); where pricebt
  deliberately differs from gs_quant: [`docs/guides/gs-quant-deviations.md`](docs/guides/gs-quant-deviations.md).
* **Connecting YOUR pricing library** (an in-house bank platform, or any other): [`skills/README.md`](skills/README.md), a library of skills for an AI agent or a person, with an ordered playbook
  ([`skills/pricebt-wire-external-library`](skills/pricebt-wire-external-library/SKILL.md)) and two tested worked examples (an in-process library and a service-style one that prices only from a market it is sent).

## Use

```
# from this directory (PowerShell); the Python environment with rateslib and QuantLib is `stir`
$env:PYTHONPATH = "src;tests"        # `tests` holds the fixture-backed market-data providers (`support.curves`, ...) that the real-data configs name
python -m pricebt validate configs\synthetic_swap_carry.yaml
python -m pricebt run configs\synthetic_swap_carry.yaml --out results\demo --tearsheet
python -m pricebt run configs\suite\s1_swap_carry_eod.yaml --stack configs\adapters\rateslib_swap.yaml      # under QuantLib: --stack configs\adapters\quantlib_swap.yaml
python -m pricebt tieout configs\suite\s1_swap_carry_eod.yaml --stack rateslib=configs\adapters\rateslib_swap.yaml --stack quantlib=configs\adapters\quantlib_swap.yaml `
    --stack reference=configs\adapters\refstack_swap.yaml --reference reference --out results\tieout\s1
python -m pytest tests                       # the whole suite; fixture-dependent tests skip when data/fixtures is absent
python -m pytest -m core                     # toys and the reference stack only: needs no pricing library
python tests\guards\blocker.py -m core       # the same, with rateslib, QuantLib, ARBS and gs_quant made unimportable
```

The gs_quant-style facade replaces `GsSession.use()` with one call that supplies a market and a stack:

```python
from pricebt.session import PricebtSession
from pricebt.instrument import IRSwap

PricebtSession.use(market=my_provider, stack="pricebt.contrib.rateslib:STACK")   # or "pricebt.contrib.quantlib:STACK"
swap = IRSwap("Pay", "10y", "USD", notional_amount=1e7)                        # terms only: the library resolves dates and par when the trade is filled
```

## What is in the box

| | |
|---|---|
| `pricebt` core | time model, engine, strategy layer (triggers, actions, signals), results (stats, attribution, reconcile, tearsheet), config loader, the neutral **schemas** and **bindings** (`pricebt.contracts`), plain-data **snapshots**, the tie-out harness (`pricebt.tieout`) |
| `pricebt.contrib.rateslib`, `pricebt.contrib.quantlib` | adapters: a `swap` and a `bond` kit each (factory + default bindings), `wrap`, the shared conventions blocks and a `STACK` |
| `pricebt.testing` | toys with deliberately mismatched names, a library-free **reference stack** (closed-form OIS swap and fixed-coupon bond: the known answer), a synthetic market, the layer conformance kit |
| `tests/support` | market-data providers over `data/fixtures` (emit snapshots; import no pricing library) |
| `tests/guards` | the zero-dependence guards: import blocker, name/convention scans, semantic guards on a mismatched-names toy, each with a non-vacuity twin |

Core (`import pricebt`) needs numpy, pandas, pyyaml and tqdm. Optional: `rateslib` (adapter; its licence is non-commercial unless you hold a commercial one), `QuantLib` (adapter),
`matplotlib` (tearsheet), `pyarrow` (parquet), `jupyter` (notebooks).

## Data and licences

pricebt is Apache-2.0; it ports logic and vocabulary from gs_quant (Apache-2.0, see `NOTICE`). `data/fixtures` derive from third-party market data and must not be redistributed;
the package ships no provider, store or vendor reader.
