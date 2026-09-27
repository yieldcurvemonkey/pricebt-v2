"""The tie-out harness (spec X1-X6): one base config, N stacks, one report. Library-free: it compares recorded results and never imports a pricing library."""
from __future__ import annotations

from .compare import Offender, Row, TieoutError, TieoutReport, compare
from .report import save, stack_summary, to_html, to_markdown
from .runner import DEFAULT_AUDIT_MEASURES, TieoutResult, run_stack, run_tieout
from .tolerances import DEFAULT_TOLERANCES, Tol, Tolerances, merge_declarations, validate_declaration

__all__ = ["compare", "run_tieout", "run_stack", "TieoutResult", "TieoutReport", "TieoutError", "Row", "Offender", "Tolerances", "Tol", "DEFAULT_TOLERANCES",
           "DEFAULT_AUDIT_MEASURES", "to_markdown", "to_html", "save", "stack_summary", "validate_declaration", "merge_declarations"]
