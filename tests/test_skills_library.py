"""The skills library (skills/): structure, links and cited paths stay true. A skill that cites a file that no longer exists sends an agent in a new environment down a dead end, so the
library is checked like code: frontmatter, required sections, links, every repository path a skill names, sibling names, and that nothing names the maintainer's private systems."""
import ast
import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.core

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
EXPECTED = (
    "pricebt-wire-external-library", "pricebt-architecture-and-rules", "pricebt-discover-the-library", "pricebt-enterprise-platform-patterns", "pricebt-map-library-to-schemas",
    "pricebt-bindings-cookbook", "pricebt-market-data-snapshots", "pricebt-wrap-and-pricer", "pricebt-instrument-kit", "pricebt-layers-and-ladder", "pricebt-conformance-and-tieout",
    "pricebt-debug-tieout-differences", "pricebt-guards-and-packaging", "pricebt-run-config-and-reports",
)
SECTIONS = ("Purpose", "Prerequisites", "Steps", "Checks", "Common failures", "Related skills")
TOP = "src|tests|docs|configs|tools|skills|tasks|data|notebooks|results"
PATH = re.compile(rf"(?<![\w./<>-])((?:{TOP})/[A-Za-z0-9_./\\<>*{{}},$-]*[A-Za-z0-9_/>*}}])")
PLACEHOLDER = "<>*{}$,"  # a token with one of these is a pattern (tests/test_<lib>_*.py), not a path


def skill_files():
    return sorted(SKILLS.rglob("*.md"))


def text_of(p):
    return p.read_text(encoding="utf8")


def frontmatter(p):
    m = re.match(r"---\n(.*?)\n---\n", text_of(p).replace("\r\n", "\n"), re.S)
    assert m, f"{p}: no YAML frontmatter"
    return yaml.safe_load(m.group(1))


def strip_fences(s):
    return re.sub(r"```.*?```", "", s, flags=re.S)


def test_the_library_has_exactly_the_documented_skills():
    have = sorted(d.name for d in SKILLS.iterdir() if d.is_dir())
    assert have == sorted(EXPECTED), (set(have) ^ set(EXPECTED))
    readme = text_of(SKILLS / "README.md")
    for name in EXPECTED:
        assert f"[`{name}`]({name}/SKILL.md)" in readme, f"README does not list {name}"


@pytest.mark.parametrize("name", EXPECTED)
def test_frontmatter_and_required_sections(name):
    p = SKILLS / name / "SKILL.md"
    fm = frontmatter(p)
    assert set(fm) == {"name", "description"}, fm.keys()
    assert fm["name"] == name
    assert 40 <= len(fm["description"]) < 500 and "use when" in fm["description"].lower(), fm["description"]
    body = text_of(p)
    assert body.count("\n") < 300, f"{name}: SKILL.md has {body.count(chr(10))} lines (limit 300: move detail to references/)"
    heads = [h.strip() for h in re.findall(r"^#{1,3} (.+)$", strip_fences(body), re.M)]
    for s in SECTIONS:
        assert any(h.startswith(s) for h in heads), f"{name}: no '{s}' section (headings: {heads})"


def test_every_markdown_link_resolves():
    bad = []
    for p in skill_files():
        for target in re.findall(r"\]\(([^)\s]+)\)", strip_fences(text_of(p))):
            if re.match(r"[a-z]+://|#|mailto:", target):
                continue
            path = (p.parent / target.split("#")[0]).resolve()
            if not path.exists():
                bad.append(f"{p.relative_to(ROOT)} -> {target}")
    assert not bad, bad


#: files a skill tells the READER to create in their own repository (a destination, not a source): they do not exist here and must not
TO_CREATE = {"tests/support/recorded_client.py", "tests/support/acme_probe.py", "tests/test_acme_facts.py"}


def test_every_repository_path_a_skill_cites_exists():
    """Backticked or fenced paths that start with a top-level directory of the repository. Tokens with a placeholder (<lib>, *, {a,b}) are patterns, not paths."""
    bad = []
    for p in skill_files():
        for tok in set(PATH.findall(text_of(p).replace("\\", "/"))):
            if any(c in tok for c in PLACEHOLDER) or tok in TO_CREATE:
                continue
            if tok.startswith("data/fixtures") and not (ROOT / "data" / "fixtures").exists():
                continue  # the fixtures derive from third-party data and are git-ignored: a fresh clone has none
            if not (ROOT / tok).exists():
                bad.append(f"{p.relative_to(ROOT)}: {tok}")
    assert not bad, "\n".join(sorted(bad))
    assert not [t for t in TO_CREATE if (ROOT / t).exists()], "a path a skill tells the reader to create now exists here: remove it from TO_CREATE"


def test_skill_relative_paths_into_references_and_the_example_exist():
    bad = []
    for p in skill_files():
        base = p.parent if p.name == "SKILL.md" else p.parents[1]
        for tok in set(re.findall(r"(?<![\w./-])((?:references|example)/[A-Za-z0-9_./-]*[A-Za-z0-9_])", text_of(p))):
            if not ((base / tok).exists() or (SKILLS / "pricebt-wire-external-library" / tok).exists()):
                bad.append(f"{p.relative_to(ROOT)}: {tok}")
    assert not bad, "\n".join(sorted(bad))


def test_sibling_skill_names_that_are_cited_exist():
    bad = []
    for p in skill_files():
        for name in set(re.findall(r"pricebt-[a-z]+(?:-[a-z]+)+", text_of(p))):
            if name not in EXPECTED and not name.startswith(("pricebt-final", "pricebt-baseline")):
                bad.append(f"{p.relative_to(ROOT)}: {name}")
    assert not bad, "\n".join(sorted(bad))


def test_nothing_names_the_maintainers_private_systems_or_leaves_a_todo():
    bad = []
    for p in skill_files():
        s = text_of(p)
        if re.search(r"(?<![A-Za-z])ARBS(?![A-Za-z])", s):
            bad.append(f"{p.relative_to(ROOT)}: names ARBS")
        if re.search(r"\b(TODO|FIXME|XXX)\b", s):
            bad.append(f"{p.relative_to(ROOT)}: a TODO marker")
    assert not bad, bad


def blocks(p, lang):
    return re.findall(rf"```{lang}\n(.*?)```", text_of(p).replace("\r\n", "\n"), re.S)


def test_python_blocks_parse_unless_marked_as_fragments():
    bad = []
    for p in skill_files():
        for i, b in enumerate(blocks(p, "python")):
            if b.lstrip().startswith("# fragment"):
                continue
            try:
                ast.parse(b)
            except SyntaxError as e:
                bad.append(f"{p.relative_to(ROOT)} python block {i}: {e.msg} line {e.lineno} (mark a fragment with a first line '# fragment')")
    assert not bad, "\n".join(bad)


def test_yaml_blocks_parse_unless_marked_as_fragments():
    bad = []
    for p in skill_files():
        for i, b in enumerate(blocks(p, "yaml")):
            if b.lstrip().startswith("# fragment"):
                continue
            try:
                yaml.safe_load(b)
            except yaml.YAMLError as e:
                bad.append(f"{p.relative_to(ROOT)} yaml block {i}: {str(e).splitlines()[0]}")
    assert not bad, "\n".join(bad)


def test_the_check_itself_can_fail(tmp_path):
    """Non-vacuity: the path regex finds a missing path in a planted skill and ignores a placeholder."""
    planted = "see `src/pricebt/does_not_exist.py` and `src/pricebt/contrib/<lib>/` and `src/pricebt/engine/engine.py`"
    toks = set(PATH.findall(planted))
    missing = [t for t in toks if not any(c in t for c in PLACEHOLDER) and not (ROOT / t).exists()]
    assert missing == ["src/pricebt/does_not_exist.py"], missing


# ---------------------------------------------------------------- the checker of the conventions document (skills/pricebt-discover-the-library/references/check_conventions_doc.py)
CHECKER = SKILLS / "pricebt-discover-the-library" / "references" / "check_conventions_doc.py"


def _checker():
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_conventions_doc", CHECKER)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _finished(m):
    """The template with every placeholder replaced: the known-answer document that must pass."""
    text = m.SKELETON
    for t in m.TOKENS:
        text = text.replace(t, "x")
    return text


def test_the_conventions_checker_passes_its_own_known_answers():
    _checker().selftest()


def test_the_conventions_checker_wants_a_filled_row_for_every_required_schema_name():
    m = _checker()
    names = m.schema_names()
    doc = _finished(m)
    assert not any(m.problems(doc, names).values())
    assert {"dv01", "gamma", "delta_ladder", "value", "rate"} <= set(names[0]) and {"carry", "roll", "delta", "convexity"} <= set(names[1])  # the names come from required_names() / required_layers()
    row = next(ln for ln in doc.splitlines() if ln.startswith("| `dv01` |"))
    for broken in (doc.replace(row + "\n", ""), doc.replace(row, "| `dv01` | x | | x | x |"), doc.replace(row, "| `dv01` | x |"), doc.replace(row, "dv01 is described in prose, and `dv01` too")):
        assert m.problems(broken, names)["schema names without a row"] == ["dv01"]


def test_a_placeholder_of_the_template_is_rejected_anywhere_but_a_foreign_token_is_not():
    m = _checker()
    doc = _finished(m)
    assert m.problems(doc + "\nthe marker adapter_<lib>\n", None)["unfilled placeholders"] == ["<lib>"]
    assert not any(m.problems(doc + "\nthe marker adapter_zeta and a <pkg> pattern\n", None).values())


def test_the_conventions_checker_exit_codes_on_a_finished_and_an_unfinished_document(tmp_path):
    import subprocess
    import sys

    m = _checker()
    doc = _finished(m)
    good, bad = tmp_path / "good.md", tmp_path / "bad.md"
    good.write_text(doc, encoding="utf8")
    bad.write_text(doc.replace(next(ln for ln in doc.splitlines() if ln.startswith("| `gamma` |")) + "\n", ""), encoding="utf8")
    run = lambda *a: subprocess.run([sys.executable, str(CHECKER), *a], capture_output=True, text=True, cwd=ROOT)  # noqa: E731
    assert run("--selftest").returncode == 0
    ok, ko = run(str(good)), run(str(bad))
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert ko.returncode == 1 and "schema names without a row: ['gamma']" in ko.stdout, ko.stdout + ko.stderr
    # `--no-names` is the mode for a machine without the checkout: copied next to its template and run from a directory that is not a checkout, nothing may need the schema
    alone = tmp_path / "alone"
    alone.mkdir()
    for f in (CHECKER, CHECKER.with_name("conventions-doc-template.md")):
        (alone / f.name).write_bytes(f.read_bytes())
    lone = lambda *a: subprocess.run([sys.executable, str(alone / CHECKER.name), *a], capture_output=True, text=True, cwd=alone)  # noqa: E731
    assert lone("--no-names", "--selftest").returncode == 0
    assert lone("--no-names", str(good)).returncode == 0 and lone("--no-names", str(bad)).returncode == 0, "the row check is off: a missing row is not looked at"
    needs = lone(str(good))
    assert needs.returncode == 1 and "no pricebt checkout found" in needs.stdout + needs.stderr, "without the flag it says what is missing"


def test_the_quantlib_model_document_passes_the_checker_without_the_row_check():
    """It predates the mapping rows (`--no-names`); everything else the checker tests it satisfies."""
    m = _checker()
    assert not any(m.problems((ROOT / "docs" / "design" / "11-quantlib-conventions.md").read_text(encoding="utf8")).values())


# ---------------------------------------------------------------- the master's fast path, its two guards rows, and the common-errors page every skill points to
MASTER = SKILLS / "pricebt-wire-external-library" / "SKILL.md"
COMMON_ERRORS = SKILLS / "pricebt-wire-external-library" / "references" / "common-errors.md"
CE_IDS = ("CE-ALLOW", "CE-IMPORT", "CE-STACK-REGISTRY", "CE-STACK-SYNTAX", "CE-TYPEERROR", "CE-FACADE", "CE-LADDER-CLI")
FAST_PATH_LINES = 60  # what a cold reader reads before opening any other skill: it grows only by cutting something else


def section_lines(text, heading):
    """The lines of the `## <heading...>` section, its heading included, up to the next `## ` heading that is not inside a code fence."""
    out, on, fenced = [], False, False
    for ln in text.replace("\r\n", "\n").split("\n"):
        if ln.startswith("```"):
            fenced = not fenced
        m = None if fenced else re.match(r"## (.+)$", ln)
        if m:
            if on:
                break
            on = m.group(1).startswith(heading)
        if on:
            out.append(ln)
    return out


def phase_rows(text):
    """The rows of the master's `## Steps` table as lists of cells: [phase number, phase, skill to open, ...]."""
    rows = []
    for ln in section_lines(text, "Steps"):
        cells = [c.strip() for c in ln.strip().strip("|").split("|")] if ln.startswith("|") else []
        if len(cells) >= 3 and re.fullmatch(r"\d+[a-z]?", cells[0]):
            rows.append(cells)
    return rows


def guards_rows(rows):
    """(the rows that open the guards skill and say START, the ones that say END)."""
    mine = [r for r in rows if "pricebt-guards-and-packaging" in r[2]]
    return [r for r in mine if "START" in r[1]], [r for r in mine if "END" in r[1]]


def links_to(text, page_dir, target):
    """Does a markdown link outside the code fences of a page that sits in `page_dir` resolve to the file `target` (an anchor is allowed)?"""
    found = re.findall(r"\]\(([^)\s]+)\)", strip_fences(text))
    return any((page_dir / t.split("#")[0]).resolve() == target.resolve() for t in found if not re.match(r"[a-z]+://|#|mailto:", t))


def ce_ids(text):
    """The ids of the common-errors page: its upper-case `## CE-...` headings (the anchors the other skills link to are these, lower-cased)."""
    return re.findall(r"^## (CE-[A-Z-]+)$", text, re.M)


def test_the_masters_fast_path_stays_short():
    n = len(section_lines(text_of(MASTER), "Fast path"))
    assert 0 < n <= FAST_PATH_LINES, f"the master's Fast path section is {n} lines (limit {FAST_PATH_LINES}): move detail into the phase table or a reference"


def test_the_master_has_a_guards_row_at_the_start_and_one_at_the_end():
    rows = phase_rows(text_of(MASTER))
    order = [r[0] for r in rows]
    start, end = guards_rows(rows)
    assert [r[0] for r in start] == ["2b"] and order.index("2b") < order.index("3"), f"one START row for the guards skill, before any adapter code (phases: {order})"
    assert len(end) == 1 and order.index(end[0][0]) > order.index("8"), f"one END row for the guards skill, after the tie-out phase (phases: {order})"


def test_every_skill_but_the_master_links_to_the_common_errors_page():
    """The failures every adapter meets are on ONE page. All thirteen skills carry a row or a link for at least one of them (measured when this test was added), so all thirteen must point there."""
    bad = [n for n in EXPECTED if n != MASTER.parent.name and not links_to(text_of(SKILLS / n / "SKILL.md"), SKILLS / n, COMMON_ERRORS)]
    assert not bad, f"no link to references/common-errors.md in: {bad}"


def test_the_common_errors_page_has_its_seven_ids_and_every_anchor_cited_into_it_exists():
    ids = ce_ids(text_of(COMMON_ERRORS))
    assert set(CE_IDS) <= set(ids), f"missing from the page: {sorted(set(CE_IDS) - set(ids))}"
    anchors = {i.lower() for i in ids}
    bad = sorted({f"{p.relative_to(ROOT)} -> #{a}" for p in skill_files() for a in re.findall(r"common-errors\.md#([A-Za-z0-9-]+)", text_of(p)) if a not in anchors})
    assert not bad, bad


def test_the_new_checks_can_fail():
    """Non-vacuity: each check above rejects a planted known-bad input and accepts the known-good one."""
    assert len(section_lines("## Fast path\n" + "x\n" * (FAST_PATH_LINES - 1) + "## Purpose\n", "Fast path")) == FAST_PATH_LINES  # a section of exactly the limit is counted as such
    assert len(section_lines("## Fast path\n" + "x\n" * FAST_PATH_LINES + "## Purpose\n", "Fast path")) == FAST_PATH_LINES + 1 > FAST_PATH_LINES
    assert len(section_lines("## Fast path\n```\n## not a heading\n```\n## Purpose\n", "Fast path")) == 4  # a `## ` line inside a fence does not end the section
    good = "## Steps (in order)\n| # | Phase | Open |\n|---|---|---|\n| 2 | Decide | `pricebt-guards-and-packaging` |\n| 2b | **START** of it | `pricebt-guards-and-packaging` |\n| 10 | **END** of it | `pricebt-guards-and-packaging` |\n"
    start, end = guards_rows(phase_rows(good))
    assert [r[0] for r in start] == ["2b"] and [r[0] for r in end] == ["10"]
    assert guards_rows(phase_rows(good.replace("| 2b | **START** of it | `pricebt-guards-and-packaging` |\n", "")))[0] == []  # the START row is gone
    assert guards_rows(phase_rows(good.replace("| 10 | **END** of it |", "| 10 | the end of it |")))[1] == []  # the END row does not say END
    assert guards_rows(phase_rows(good.replace("| 2b | **START** of it | `pricebt-guards-and-packaging` |", "| 2b | **START** of it | `pricebt-instrument-kit` |")))[0] == []  # the START row opens another skill
    page_dir = SKILLS / "pricebt-wrap-and-pricer"
    link = "see [x](../pricebt-wire-external-library/references/common-errors.md#ce-allow)"
    assert links_to(link, page_dir, COMMON_ERRORS)
    assert not links_to(f"```\n{link}\n```", page_dir, COMMON_ERRORS)  # a link inside a fence is an example, not a pointer
    assert not links_to("see [x](references/common-errors.md)", page_dir, COMMON_ERRORS)  # resolves under this skill, where there is no such page
    assert not links_to("see [x](../pricebt-wire-external-library/SKILL.md)", page_dir, COMMON_ERRORS)
    page = "".join(f"## {i}\ntext\n" for i in CE_IDS)
    assert set(CE_IDS) <= set(ce_ids(page))
    assert not set(CE_IDS) <= set(ce_ids(page.replace("## CE-FACADE\n", "## Facade\n")))  # a renamed heading: the id is gone
    assert not set(CE_IDS) <= set(ce_ids(page.replace("## CE-TYPEERROR\n", "### CE-TYPEERROR\n")))  # a heading of another level is not the page's id
