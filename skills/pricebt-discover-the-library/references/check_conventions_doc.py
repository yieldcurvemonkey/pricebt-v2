"""check_conventions_doc.py [--no-names] <docs/design/11-<lib>-conventions.md>: is the conventions document finished?

Fails (exit 1) while any `TBD` is left, while any placeholder of the template (`<LIB>`, `<n>`, `<version>`, ... every `<...>` token of the skeleton in conventions-doc-template.md, wherever it
appears in the document) is still in it, while a section heading `## 0.` ... `## 13.` is missing, or while a schema name has no mapping row: every name of `required_names()` needs a table row
whose first cell is `name` in backticks, with every cell filled, in sections 3 to 6, and every name of `required_layers()` the same in section 11 (D17: these rows are the mapping worksheet).
`--no-names` skips the last check (the QuantLib model document predates the rows). Run from the repository root. `python check_conventions_doc.py --selftest` runs it on known answers first."""
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).parent
SKELETON = re.findall(r"```markdown\n(.*?)```", (HERE / "conventions-doc-template.md").read_text(encoding="utf8"), re.S)[0]
TOKENS = sorted(set(re.findall(r"<[^<>\n]{1,80}>", SKELETON)))


def schema_names() -> tuple:
    """(names of the methods and measures, names of the layers) that every swap stack must bind, read from the schema of the repository above this file or the current directory."""
    for start in (pathlib.Path(__file__).resolve(), pathlib.Path.cwd().resolve()):
        for d in [start, *start.parents]:
            if (d / "src" / "pricebt" / "contracts" / "schema.py").is_file():
                sys.path.insert(0, str(d / "src"))
                from pricebt.contracts.schema import SchemaRegistry

                swap = SchemaRegistry.default().get("swap")
                return [n for kind in swap.required_names().values() for n in kind], swap.required_layers()
    raise SystemExit("error: no pricebt checkout found above this file or the current directory (src/pricebt/contracts/schema.py); run from the repository root or use --no-names")


def section(text: str, first: int, after: int) -> str:
    """The text from the heading `## <first>. ` up to the heading `## <after>. ` (or the end)."""
    a = re.search(rf"^## {first}\. ", text, re.M)
    b = re.search(rf"^## {after}\. ", text, re.M)
    return text[a.start(): b.start() if b else len(text)] if a else ""


def unmapped(text: str, names: list, layers: list) -> list:
    """Schema names without a filled row: a table row whose first cell is `name` and whose every cell is non-empty, in sections 3-6 (methods, measures) or 11 (layers)."""
    def rows(part: str) -> dict:
        got = {}
        for line in part.splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.strip().startswith("|") else []
            if len(cells) >= 4 and re.fullmatch(r"`\w+`", cells[0]):
                got[cells[0].strip("`")] = all(cells)
        return got

    body, lay = rows(section(text, 3, 7)), rows(section(text, 11, 12))
    return sorted(n for n in names if not body.get(n)) + sorted(n for n in layers if not lay.get(n))


def problems(text: str, names: tuple = None) -> dict:
    """`names` = (methods and measures, layers) to require a mapping row for, or None to skip that check."""
    out = {
        "TBD": len(re.findall(r"\bTBD\b", text)),
        "unfilled placeholders": sorted(t for t in TOKENS if t in text),
        "missing sections": [n for n in range(14) if not re.search(rf"^## {n}\. ", text, re.M)],
    }
    if names is not None:
        out["schema names without a row"] = unmapped(text, *names)
    return out


def selftest(with_names: bool = True) -> None:
    """Known answers first. `with_names=False` (the `--no-names` mode) needs no checkout: it skips the answers that read the schema."""
    names = schema_names() if with_names else None
    p = problems(SKELETON, names)
    assert not p["missing sections"] and p["unfilled placeholders"], "known answer 1: the untouched template has every heading and every placeholder"
    assert p.get("schema names without a row", []) == [], "known answer 1b: the template already carries one row per schema name; its cells are placeholders, which the placeholder check reports"
    filled = SKELETON
    for tok in TOKENS:
        filled = filled.replace(tok, "x")
    assert not any(problems(filled, names).values()), "known answer 2: a template with every placeholder replaced is finished, mapping rows included"
    assert problems(filled.replace("## 8. ", "## eight. "), names)["missing sections"] == [8], "known answer 3: a lost heading is reported"
    assert problems(filled + "\nTBD\n", names)["TBD"] == 1, "known answer 4: a TBD is reported"
    assert problems(filled + "\nadapter_<lib> is the marker\n", names)["unfilled placeholders"] == ["<lib>"], "known answer 5: a placeholder in the prose is reported"
    if names is None:
        return
    gamma = [ln for ln in filled.splitlines() if ln.startswith("| `gamma` |")]
    assert len(gamma) == 1, "the template has exactly one gamma row"
    assert problems(filled.replace(gamma[0] + "\n", ""), names)["schema names without a row"] == ["gamma"], "known answer 6: a schema name with no row is reported, and only that one"
    prose = filled.replace(gamma[0], "gamma is mentioned in the prose, `gamma` too")
    assert problems(prose, names)["schema names without a row"] == ["gamma"], "known answer 7: a name that is only mentioned is not a row"
    assert problems(filled.replace(gamma[0], "| `gamma` | x | | x | x |"), names)["schema names without a row"] == ["gamma"], "known answer 8: a row with an empty cell is not filled"
    carry = [ln for ln in filled.splitlines() if ln.startswith("| `carry` |")][0]
    moved = filled.replace(carry + "\n", "").replace(gamma[0], gamma[0] + "\n" + carry)
    assert problems(moved, names)["schema names without a row"] == ["carry"], "known answer 9: a layer row must sit in section 11, a row in sections 3-6 does not count"


if __name__ == "__main__":
    args = sys.argv[1:]
    skip = "--no-names" in args
    selftest(not skip)
    if "--selftest" in args:
        print(f"selftest ok: {len(TOKENS)} placeholder tokens in the template" + ("" if skip else f", {sum(map(len, schema_names()))} schema names checked"))
        sys.exit(0)
    path = [a for a in args if not a.startswith("--")][0]
    p = problems(pathlib.Path(path).read_text(encoding="utf8"), None if skip else schema_names())
    print("; ".join(f"{k}: {v}" for k, v in p.items()))
    sys.exit(1 if any(p.values()) else 0)
