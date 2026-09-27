"""AssetNamespace: one isolated evaluation namespace per loaded config (DESIGN.md section 4.4).

On the first call to `eval()`, it runs the config's `imports` then its `code` -- lazily, once,
into a private dict -- then evaluates the requested compiled expression in that dict with a fresh
set of injected locals. Two instances never share state, even for the same config.

Works against anything shaped like `config.AssetConfig` or `fx.FxConfig`: an object exposing
`.name`, `.imports_code`, `.imports_src`, `.code_code`, `.code_src`, `.code(key)` and
`.expr_src(key)`.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..errors import AssetEvaluationError


class AssetNamespace:
    def __init__(self, cfg: Any):
        self._cfg = cfg
        self._globals: Optional[Dict[str, Any]] = None
        self._init_error: Optional[AssetEvaluationError] = None

    def _ready(self, date: Any, csa: Any) -> Dict[str, Any]:
        if self._init_error is not None:
            raise self._init_error
        if self._globals is not None:
            return self._globals
        ns: Dict[str, Any] = {}
        steps = (("imports", self._cfg.imports_code, self._cfg.imports_src), ("code", self._cfg.code_code, self._cfg.code_src))
        for key, code, src in steps:
            try:
                exec(code, ns)
            except Exception as exc:
                err = AssetEvaluationError(self._cfg.name, key, src, date, csa)
                self._init_error = err
                raise err from exc
        self._globals = ns
        return ns

    def eval(self, key: str, **injected: Any) -> Any:
        """Evaluate the compiled expression stored under `key`, with `injected` as fresh locals."""
        date = injected.get("pricebt_date")
        csa = injected.get("pricebt_csa")
        globals_ = self._ready(date, csa)
        code = self._cfg.code(key)
        try:
            return eval(code, globals_, dict(injected))
        except Exception as exc:
            raise AssetEvaluationError(self._cfg.name, key, self._cfg.expr_src(key), date, csa) from exc
