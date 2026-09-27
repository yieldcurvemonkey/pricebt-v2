"""Static scans behind the zero-dependence guards (spec 9.1).

Every function takes the tree to scan as an argument (default: the real project) so the non-vacuity twins (spec 9.2) can point the SAME code
at a temporary tree with one planted violation and assert it is reported. Nothing here imports pricebt.
"""
from __future__ import annotations

import ast
import fnmatch
import io
import re
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Set, Tuple

import yaml

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
BANNED_FILE = HERE / "banned.yaml"


def load_banned(path: Path = BANNED_FILE) -> Dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf8"))


@dataclass(frozen=True, order=True)
class Hit:
    """One violation: `file` is posix, relative to the scanned root; `token` is the banned thing; `text` the offending text."""

    file: str
    line: int
    kind: str
    token: str
    text: str = ""

    def __str__(self) -> str:
        return f"{self.file}:{self.line} [{self.kind}] {self.token!r} in {self.text!r}"


# ------------------------------------------------------------------------------ token matching
def _regex(entry: Mapping[str, str]) -> "re.Pattern[str]":
    tok, mode = entry["token"], entry.get("match", "substring")
    if mode == "substring":
        return re.compile(re.escape(tok), re.I)
    if mode == "word":
        return re.compile(r"(?<![A-Za-z0-9])" + re.escape(tok) + r"(?![A-Za-z0-9])", re.I)
    if mode == "prefix":
        return re.compile(r"(?<![A-Za-z0-9])" + re.escape(tok), re.I)
    raise ValueError(f"unknown match mode {mode!r} for token {tok!r}")


def _delimited(tok: str) -> "re.Pattern[str]":
    """Case-insensitive token bounded by non-alphanumerics on the sides where the token itself starts/ends with an alphanumeric."""
    left = r"(?<![A-Za-z0-9])" if tok[0].isalnum() else ""
    right = r"(?![A-Za-z0-9])" if tok[-1].isalnum() else ""
    return re.compile(left + re.escape(tok) + right, re.I)


def _compiled(entries: Iterable[Mapping[str, str]]) -> List[Tuple[str, "re.Pattern[str]"]]:
    return [(e["token"], _regex(e)) for e in entries]


def rel(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


# ------------------------------------------------------------------------------ file sets
def core_py_files(src: Path = PROJECT / "src") -> List[Path]:
    """Everything under src/pricebt except contrib/ (spec 1.3: core)."""
    pkg = src / "pricebt"
    return sorted(p for p in pkg.rglob("*.py") if "contrib" not in p.relative_to(pkg).parts and "__pycache__" not in p.parts)


def all_py_files(src: Path = PROJECT / "src") -> List[Path]:
    pkg = src / "pricebt"
    return sorted(p for p in pkg.rglob("*.py") if "__pycache__" not in p.parts)


def core_schema_files(src: Path = PROJECT / "src") -> List[Path]:
    return sorted((src / "pricebt").rglob("schemas/*.yaml"))


def core_config_files(configs: Path = PROJECT / "configs") -> List[Path]:
    """Shipped configs except explicit adapter/example configs (GT-Z2c)."""
    out = []
    for p in sorted(list(configs.rglob("*.yaml")) + list(configs.rglob("*.yml")) + list(configs.rglob("*.json"))):
        parts = p.relative_to(configs).parts
        if parts and parts[0] in ("adapters", "examples"):
            continue
        out.append(p)
    return out


# ------------------------------------------------------------------------------ Z1: imports
def _banned_roots(banned: Mapping[str, Any]) -> Set[str]:
    return set(banned["import_roots"])


def scan_imports(path: Path, root: Path, banned: Optional[Mapping[str, Any]] = None) -> List[Hit]:
    """Import statements at ANY depth (module level, inside functions, inside try/except) and importlib/__import__ calls with a literal name."""
    banned = banned or load_banned()
    roots = _banned_roots(banned)
    tree = ast.parse(path.read_text(encoding="utf8"), filename=str(path))
    hits: List[Hit] = []
    r = rel(path, root)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in roots:
                    hits.append(Hit(r, node.lineno, "import", a.name.split(".")[0], f"import {a.name}"))
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module.split(".")[0] in roots:
                hits.append(Hit(r, node.lineno, "import", node.module.split(".")[0], f"from {node.module} import ..."))
        elif isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else fn.id if isinstance(fn, ast.Name) else ""
            if name in ("import_module", "__import__") and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                head = node.args[0].value.split(".")[0]
                if head in roots:
                    hits.append(Hit(r, node.lineno, "import", head, f"{name}({node.args[0].value!r})"))
    return sorted(hits)


# ------------------------------------------------------------------------------ Z2: names and literals (python)
def _docstring_ids(tree: ast.AST) -> Set[int]:
    ids: Set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


def executable_texts(path: Path) -> Iterator[Tuple[int, str, str]]:
    """(line, kind, text) for every identifier and non-docstring string literal of a python file."""
    tree = ast.parse(path.read_text(encoding="utf8"), filename=str(path))
    doc = _docstring_ids(tree)
    for node in ast.walk(tree):
        ln = getattr(node, "lineno", 0)
        if isinstance(node, ast.Name):
            yield ln, "name", node.id
        elif isinstance(node, ast.Attribute):
            yield ln, "name", node.attr
        elif isinstance(node, ast.arg):
            yield ln, "name", node.arg
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield ln, "name", node.name
        elif isinstance(node, ast.keyword) and node.arg:
            yield ln, "name", node.arg
        elif isinstance(node, ast.alias):
            yield ln, "name", node.name
            if node.asname:
                yield ln, "name", node.asname
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield ln, "name", node.module
        elif isinstance(node, ast.ExceptHandler) and node.name:
            yield ln, "name", node.name
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in doc:
            yield node.lineno, "literal", node.value


def scan_names(path: Path, root: Path, banned: Optional[Mapping[str, Any]] = None) -> List[Hit]:
    banned = banned or load_banned()
    pats = _compiled(banned["executable_tokens"])
    r = rel(path, root)
    hits = []
    for ln, kind, text in executable_texts(path):
        for tok, pat in pats:
            if pat.search(text):
                hits.append(Hit(r, ln, kind, tok, text if len(text) < 80 else text[:77] + "..."))
    return sorted(set(hits))


# ------------------------------------------------------------------------------ Z2: YAML (schemas, configs)
def _walk_yaml(node: Any, trail: Tuple[str, ...] = ()) -> Iterator[Tuple[Tuple[str, ...], str, str]]:
    """(trail, 'key'|'value', text) for every string key and every string value NOT under a `doc` key."""
    if isinstance(node, Mapping):
        for k, v in node.items():
            ks = str(k)
            yield trail, "key", ks
            if ks == "doc":
                continue
            yield from _walk_yaml(v, trail + (ks,))
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            yield from _walk_yaml(v, trail + (f"[{i}]",))
    elif isinstance(node, str):
        yield trail, "value", node


def scan_yaml(path: Path, root: Path, banned: Optional[Mapping[str, Any]] = None) -> List[Hit]:
    banned = banned or load_banned()
    pats = _compiled(banned["executable_tokens"])
    data = yaml.safe_load(path.read_text(encoding="utf8"))
    r = rel(path, root)
    hits = []
    for trail, kind, text in _walk_yaml(data):
        for tok, pat in pats:
            if pat.search(text):
                hits.append(Hit(r, 0, f"yaml-{kind}", tok, ".".join(trail) + f" -> {text}"))
    return sorted(set(hits))


def scan_schema_args(path: Path, root: Path, banned: Optional[Mapping[str, Any]] = None) -> List[Hit]:
    """S1/S2: a schema must not name a library's argument as a signature key (`args`, `kwargs`, `signature` mappings)."""
    banned = banned or load_banned()
    names = {n.lower() for n in banned["schema_arg_names"]}
    data = yaml.safe_load(path.read_text(encoding="utf8"))
    r = rel(path, root)
    hits: List[Hit] = []

    def walk(node: Any, trail: Tuple[str, ...]) -> None:
        if isinstance(node, Mapping):
            for k, v in node.items():
                ks = str(k)
                if ks == "doc":
                    continue
                if trail and trail[-1] in ("args", "kwargs", "signature") and ks.lower() in names:
                    hits.append(Hit(r, 0, "schema-arg", ks, ".".join(trail + (ks,))))
                walk(v, trail + (ks,))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, trail + (f"[{i}]",))

    walk(data, ())
    return sorted(hits)


# ------------------------------------------------------------------------------ Z4: conventions
def convention_allowed(path: Path, root: Path, banned: Mapping[str, Any]) -> bool:
    p = rel(path, root)
    return any(fnmatch.fnmatch(p, e["path"]) for e in banned.get("convention_allow", []))


def scan_conventions(path: Path, root: Path, banned: Optional[Mapping[str, Any]] = None) -> List[Hit]:
    """Convention tokens in identifiers / non-docstring literals, and division by 360 / 365 / 365.25."""
    banned = banned or load_banned()
    if convention_allowed(path, root, banned):
        return []
    toks = [(t, _delimited(t)) for t in banned["convention_tokens"]]
    divs = {float(x) for x in banned["convention_divisors"]}
    r = rel(path, root)
    hits: List[Hit] = []
    for ln, kind, text in executable_texts(path):
        norm = text.lower().replace(" ", "")
        for tok, pat in toks:
            if pat.search(norm):  # word-delimited: `busday_count` (numpy) is not `day_count`, `floating_rate_day_count_fraction` is
                hits.append(Hit(r, ln, "convention", tok, text if len(text) < 80 else text[:77] + "..."))
    tree = ast.parse(path.read_text(encoding="utf8"), filename=str(path))
    named = _module_constants(tree, divs)
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.FloorDiv)):
            for f in _factors(node.right):  # `x / 360`, `x / (365.25 * 86400)`, `x / DAYS` (DAYS = 365.0 at module level), `x / (DAYS * 24 * 3600)`
                v = f.value if isinstance(f, ast.Constant) and isinstance(f.value, (int, float)) else named.get(f.id) if isinstance(f, ast.Name) else None
                if v is not None and float(v) in divs:
                    hits.append(Hit(r, node.lineno, "convention", f"/{v}", ast.unparse(node)[:80]))
    return sorted(set(hits))


def _factors(node: ast.AST) -> List[ast.AST]:
    """The operands of a chain of multiplications (`a * b * c` -> a, b, c); anything else is one operand."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        return [*_factors(node.left), *_factors(node.right)]
    return [node]


def _module_constants(tree: ast.Module, divs: set) -> Dict[str, float]:
    """Module-level `NAME = <number>` assignments whose number is a banned divisor (a day count given a name)."""
    out: Dict[str, float] = {}
    for n in tree.body:
        target, value = None, None
        if isinstance(n, ast.Assign) and len(n.targets) == 1:
            target, value = n.targets[0], n.value
        elif isinstance(n, ast.AnnAssign) and n.value is not None:
            target, value = n.target, n.value
        if isinstance(target, ast.Name) and isinstance(value, ast.Constant) and isinstance(value.value, (int, float)) and float(value.value) in divs:
            out[target.id] = value.value
    return out


# ------------------------------------------------------------------------------ Z5: prose (comments + docstrings)
def scan_prose(path: Path, root: Path, banned: Optional[Mapping[str, Any]] = None) -> List[Hit]:
    banned = banned or load_banned()
    pats = _compiled(banned["prose_tokens"])
    src = path.read_text(encoding="utf8")
    r = rel(path, root)
    hits: List[Hit] = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            text, ln = tok.string, tok.start[0]
        elif tok.type == tokenize.STRING:
            text, ln = tok.string, tok.start[0]
        else:
            continue
        for name, pat in pats:
            if pat.search(text):
                hits.append(Hit(r, ln, "prose", name, text if len(text) < 80 else text[:77] + "..."))
    return sorted(set(hits))


def package_names_with(src: Path, token: str = "arbs") -> List[str]:
    pkg = src / "pricebt"
    return sorted(p.relative_to(src).as_posix() for p in pkg.rglob("*") if token in p.name.lower() and "__pycache__" not in p.parts)
