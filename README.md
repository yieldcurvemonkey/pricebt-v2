# pricebt

pricebt is an event-driven backtester whose public API is a near 1:1 port of `gs_quant.backtests`.
It has no pricing or market data of its own: every number comes from a self-contained YAML asset
config that names the library it calls (see `docs/v2/DESIGN.md` section 4). This is a placeholder;
the full quick start and asset-config primer land in a later phase.

Start here: `docs/v2/README.md`, then `docs/v2/DESIGN.md`, then `docs/v2/IMPLEMENTATION_PLAN.md`.
