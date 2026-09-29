# Notes for AI agents working in this repository

This is **pricebt v2**: a gs_quant.backtests-compatible backtester whose pricing and data come from YAML asset configs. The v1 tree is recoverable at git tag `v1-final`; do not follow any v1 document.

* **Start here: the skills library, `skills/README.md` (entry skill `skills/pricebt-start-here/SKILL.md`).** It covers:
  - connecting your pricing/data library (a bank platform or an in-house library) and getting to a first backtest fast;
  - the strategy workflow: idea → questions → implementation → adversarial review → spot checks → tearsheet.

  Claude Code discovers the same skills through the stubs in `.claude/skills/`.
* **Design and contracts:** `docs/v2/README.md`, `docs/v2/DESIGN.md` (requirements), `docs/v2/ASSET_CONFIG_GUIDE.md` (the asset-config contract), `docs/v2/DEVIATIONS.md` (every difference from gs_quant).
* **The one rule (MUST-1):** nothing under `src/pricebt` may import, name, or assume the shape of any pricing library or market-data infrastructure. External libraries are reached only through the Python expression strings in asset config files (`configs/assets/*.yaml`, or your own private configs). `tests/guards/` enforces this.
* **API parity (MUST-2):** the public API mirrors `gs_quant.backtests` (1.5.4 signatures, 2.1.17 behaviour). Every behavioural difference has a `DEV-*` id in `docs/v2/DESIGN.md` §11 and a `# pricebt DEV-..` marker in code.
* **Measure contracts (IR pricing and risk):** an asset config for `IRSwap`, `IRSwaption` or `Bond` must map every measure of its class's contract (`src/pricebt/risk/contracts.py`) or declare it under `unsupported_measures:` with a reason; the loader refuses anything else. Design: `docs/v2/IR_RISK_DESIGN.md`; how-to: `skills/pricebt-risk-measures`.
* **This repository is public.** Keep bank configs, recordings, credentials and reports out of it (`reports/` is git-ignored).
* **Real-library example:** `configs/assets/usd_sofr_ois_interest_rate_swap.yaml` targets the maintainer's own library (ARBS). Never import it from `src/` or from normal tests. Its live tests carry the marker `live_arbs` and are opt-in (`PRICEBT_LIVE_ARBS=1`).
* **Run tests** from the repository root: `$env:PYTHONPATH="src;tests"; python -m pytest tests -o addopts= -p no:cacheprovider` (POSIX: `PYTHONPATH=src:tests`).
