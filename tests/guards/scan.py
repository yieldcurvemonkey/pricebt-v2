"""Static scans behind the zero-dependence guards (DESIGN.md section 12.2, items 1-2-4).

Every function takes the file (or package root) to scan as an argument, so the non-vacuity twins
(item 7) can point the SAME code at a temporary tree with one planted violation and assert it is
reported. Nothing here imports pricebt.
"""
from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Set, Tuple

# ------------------------------------------------------------------------------------ item 1: import roots
ALLOWED_EXTRA_ROOTS = {"numpy", "pandas", "yaml", "dateutil", "tqdm", "pricebt"}


def allowed_import_roots() -> Set[str]:
    return set(sys.stdlib_module_names) | ALLOWED_EXTRA_ROOTS


@dataclass(frozen=True, order=True)
class Hit:
    file: str
    line: int
    token: str
    text: str = ""

    def __str__(self) -> str:
        return f"{self.file}:{self.line} {self.token!r} in {self.text!r}"


def rel(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def scan_import_roots(path: Path, root: Path) -> List[Hit]:
    """Every `import`/`from ... import` at any depth (module level, inside a function, inside a
    try/except) whose root is not in `allowed_import_roots()`. A relative import (`from .x import
    y`, level > 0) is always internal to pricebt and is never a violation."""
    tree = ast.parse(path.read_text(encoding="utf8"), filename=str(path))
    allowed = allowed_import_roots()
    r = rel(path, root)
    hits: List[Hit] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                head = a.name.split(".")[0]
                if head not in allowed:
                    hits.append(Hit(r, node.lineno, head, f"import {a.name}"))
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue
            if node.module:
                head = node.module.split(".")[0]
                if head not in allowed:
                    hits.append(Hit(r, node.lineno, head, f"from {node.module} import ..."))
    return sorted(hits)


# ------------------------------------------------------------------------------------ tokenize helper (items 2, 4)
def _docstring_positions(tree: ast.Module) -> Set[Tuple[int, int]]:
    """(line, col) of the start of every module/class/function docstring literal."""
    positions: Set[Tuple[int, int]] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                c = body[0].value
                positions.add((c.lineno, c.col_offset))
    return positions


_FSTRING_MIDDLE = getattr(tokenize, "FSTRING_MIDDLE", None)  # Python >= 3.12 splits f-strings


def iter_scan_tokens(path: Path) -> Iterator[Tuple[str, int, str, bool]]:
    """(kind, start_line, text, is_docstring) for every NAME, STRING and FSTRING_MIDDLE token.
    COMMENT tokens (whole-line and trailing) are dropped entirely -- they are never scanned."""
    src = path.read_text(encoding="utf8")
    doc_positions = _docstring_positions(ast.parse(src, filename=str(path)))
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.NAME:
            yield "NAME", tok.start[0], tok.string, False
        elif tok.type == tokenize.STRING:
            yield "STRING", tok.start[0], tok.string, tok.start in doc_positions
        elif _FSTRING_MIDDLE is not None and tok.type == _FSTRING_MIDDLE:
            yield "FSTRING_MIDDLE", tok.start[0], tok.string, False


def _clip(text: str) -> str:
    return text if len(text) < 80 else text[:77] + "..."


# ------------------------------------------------------------------------------------ item 2: token scan
VENDOR_RE = re.compile(
    r"(?<![A-Za-z])(arbs|rateslib|quantlib|irswapsmdp|mdp|rlirswapcurve|bulk_get_data|build_irswap"
    r"|ignore_cache_miss|supabase|nojumps|eris|erisfutures|bloomberg|refinitiv|marquee)(?![A-Za-z])",
    re.I,
)
GS_QUANT_RE = re.compile(r"(?<![A-Za-z])(gs_quant)(?![A-Za-z])", re.I)


def scan_vendor_tokens(path: Path, root: Path) -> List[Hit]:
    """Every NAME, STRING and FSTRING_MIDDLE token (docstrings included) matching VENDOR_RE. A
    single token (e.g. one string literal) may contain more than one banned word, so every match in
    the token is reported, not just the first."""
    r = rel(path, root)
    hits = [Hit(r, line, m.group(1).lower(), _clip(text)) for _, line, text, _ in iter_scan_tokens(path) for m in VENDOR_RE.finditer(text)]
    return sorted(set(hits))


def scan_gs_quant_tokens(path: Path, root: Path) -> List[Hit]:
    """NAME and non-docstring STRING/FSTRING_MIDDLE tokens matching GS_QUANT_RE. A docstring may
    name gs_quant for attribution (MUST-1's one exception) without failing this scan."""
    r = rel(path, root)
    hits = []
    for kind, line, text, is_doc in iter_scan_tokens(path):
        if kind == "STRING" and is_doc:
            continue
        for m in GS_QUANT_RE.finditer(text):
            hits.append(Hit(r, line, "gs_quant", _clip(text)))
    return sorted(set(hits))


# ------------------------------------------------------------------------------------ item 4: asset-agnostic scan
ASSET_AGNOSTIC_RE = re.compile(
    r"(?<![a-z])(notional|tenor|swaption|swap|fixed_rate|termination_date|expiration_date"
    r"|pay_or_receive|strike|dv01|pv01|par_rate|sofr|libor|estr)(?![a-z])",
    re.I,
)


def scan_asset_agnostic_tokens(path: Path, root: Path) -> List[Hit]:
    """Every NAME, STRING and FSTRING_MIDDLE token (docstrings included) matching ASSET_AGNOSTIC_RE."""
    r = rel(path, root)
    hits = [Hit(r, line, m.group(1).lower(), _clip(text)) for _, line, text, _ in iter_scan_tokens(path) for m in ASSET_AGNOSTIC_RE.finditer(text)]
    return sorted(set(hits))


# ------------------------------------------------------------------------------------ file sets
def all_py_files(pkg: Path) -> List[Path]:
    return sorted(p for p in pkg.rglob("*.py") if "__pycache__" not in p.parts)


def asset_agnostic_files(pkg: Path) -> List[Path]:
    """DESIGN.md section 12.2 item 4's scope: assets/**, markets/**, risk/results.py, risk/transform.py,
    risk/core.py."""
    out = []
    for p in all_py_files(pkg):
        parts = p.relative_to(pkg).parts
        if parts[0] in ("assets", "markets") or parts in (("risk", "results.py"), ("risk", "transform.py"), ("risk", "core.py")):
            out.append(p)
    return sorted(out)
