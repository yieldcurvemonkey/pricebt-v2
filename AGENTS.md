# Notes for AI agents working in this repository

This branch (`v2-redesign`) is **pricebt v2**, a rewrite. The v1 tree is recoverable at git tag `v1-final`; do not follow any v1 document.

* **Start here:** `docs/v2/README.md`, then `docs/v2/DESIGN.md` (requirements and contracts), then `docs/v2/IMPLEMENTATION_PLAN.md` (phases, file ownership, acceptance commands).
* **The one rule (MUST-1):** nothing under `src/pricebt` may import, name, or assume the shape of any pricing library or market-data infrastructure (ARBS, rateslib, QuantLib, gs_quant, ...). External libraries are reached only through the Python expression strings in asset config files (`configs/assets/*.yaml`). `tests/guards/` enforces this.
* **API parity (MUST-2):** the public API mirrors `gs_quant.backtests` (1.5.4 signatures, 2.1.17 behaviour). Every behavioural difference must have a `DEV-*` id in `docs/v2/DESIGN.md` §11 and a `# pricebt DEV-..` marker in code.
* **Never import ARBS** (`C:\Users\chris\clee\ARBS`) from `src/` or from normal tests. Live ARBS tests are marker `live_arbs`, opt-in (`PRICEBT_LIVE_ARBS=1`), and the first run needs the user's approval.
* **Run tests** from the worktree root: `$env:PYTHONPATH="src;tests"; C:\Users\chris\anaconda3\envs\stir\python.exe -m pytest tests -o addopts= -p no:cacheprovider`.
