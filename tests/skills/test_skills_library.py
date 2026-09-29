"""Structure checks for the skills library (skills/): frontmatter, links, cited paths, README index,
public-repo hygiene, and the .claude/skills mirror."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "skills"
SKILL_DIRS = sorted(p for p in SKILLS.iterdir() if p.is_dir() and p.name.startswith("pricebt-"))
MD_FILES = sorted(SKILLS.rglob("*.md"))
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
CITED = re.compile(r"`((?:src|tests|skills|docs|configs|tools|notebooks)/[^`\s]+)`")
ABSOLUTE_USER_PATH = re.compile(r"(?i)([a-z]:[\\/]users[\\/]|/c/users/|/home/[a-z]+/)")


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path}: must start with YAML frontmatter"
    end = text.index("\n---", 4)
    return yaml.safe_load(text[4:end]) or {}


def test_library_has_the_expected_skills():
    names = {p.name for p in SKILL_DIRS}
    expected = {
        "pricebt-start-here", "pricebt-architecture", "pricebt-connect-pricing-library", "pricebt-verify-asset-config",
        "pricebt-asset-config-cookbook", "pricebt-enterprise-integration", "pricebt-port-gs-notebook",
        "pricebt-strategy-workflow", "pricebt-strategy-intake", "pricebt-strategy-recipes", "pricebt-adversarial-review",
        "pricebt-spot-checks", "pricebt-tearsheet-report", "pricebt-research-methodology",
        "pricebt-risk-measures", "pricebt-pnl-attribution",
    }
    assert expected <= names, f"missing skills: {sorted(expected - names)}"


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda p: p.name)
def test_skill_frontmatter(skill_dir):
    skill_md = skill_dir / "SKILL.md"
    assert skill_md.exists(), f"{skill_dir.name} has no SKILL.md"
    meta = _frontmatter(skill_md)
    assert meta.get("name") == skill_dir.name
    desc = meta.get("description", "")
    assert isinstance(desc, str) and 40 <= len(desc) <= 1024, f"{skill_dir.name}: description length {len(desc)}"
    body_lines = skill_md.read_text(encoding="utf-8").count("\n")
    assert body_lines < 350, f"{skill_dir.name}: SKILL.md is {body_lines} lines; move detail to references/"


def _iter_links(md: Path):
    in_code = False
    for line in md.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        for target in LINK.findall(line):
            yield target


@pytest.mark.parametrize("md", MD_FILES, ids=lambda p: str(p.relative_to(SKILLS)))
def test_relative_links_resolve(md):
    broken = []
    for target in _iter_links(md):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        path = target.split("#", 1)[0]
        if path and not (md.parent / path).exists():
            broken.append(target)
    assert not broken, f"{md.relative_to(ROOT)}: broken links {broken}"


@pytest.mark.parametrize("md", MD_FILES, ids=lambda p: str(p.relative_to(SKILLS)))
def test_cited_repository_paths_exist(md):
    missing = []
    for cited in CITED.findall(md.read_text(encoding="utf-8")):
        if any(ch in cited for ch in "<>*{}") or cited.startswith(("reports/",)):
            continue  # placeholders and generated outputs
        cited = cited.rstrip(".,:;)")
        if not (ROOT / cited).exists():
            missing.append(cited)
    assert not missing, f"{md.relative_to(ROOT)} cites missing paths: {missing}"


def test_readme_lists_every_skill():
    readme = (SKILLS / "README.md").read_text(encoding="utf-8")
    unlisted = [p.name for p in SKILL_DIRS if f"({p.name}/SKILL.md)" not in readme]
    assert not unlisted, f"skills/README.md does not list {unlisted}"


def test_the_path_check_catches_both_slash_forms():
    for path in ("C:\\Users\\someone\\x", "C:/Users/someone/x", "/c/users/someone/x", "/home/someone/x"):
        assert ABSOLUTE_USER_PATH.search(path), path
    assert not ABSOLUTE_USER_PATH.search("src/pricebt/users.py")


def test_no_machine_specific_absolute_paths_in_skills():
    offenders = []
    for f in SKILLS.rglob("*"):
        if f.is_file() and f.suffix in {".md", ".py", ".yaml", ".yml"}:
            for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if ABSOLUTE_USER_PATH.search(line):
                    offenders.append(f"{f.relative_to(ROOT)}:{i}")
    assert not offenders, f"machine-specific absolute paths (public repo): {offenders}"


def test_example_libraries_declare_they_are_fictional():
    for init in SKILLS.rglob("example/*/__init__.py"):
        head = init.read_text(encoding="utf-8").lower()[:400]
        assert "fictional" in head, f"{init.relative_to(ROOT)} must say it is fictional in its first docstring"


def test_claude_mirror_is_in_sync():
    proc = subprocess.run([sys.executable, str(ROOT / "tools" / "sync_agent_skills.py"), "--check"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr + " -> run: python tools/sync_agent_skills.py"


def test_sync_check_detects_drift(tmp_path, monkeypatch):
    """Non-vacuity: the --check mode must fail when a stub is stale."""
    sys.path.insert(0, str(ROOT / "tools"))
    import sync_agent_skills as sas

    mirror = tmp_path / "mirror"
    monkeypatch.setattr(sas, "MIRROR", mirror)
    assert sas.main([]) == 0 and sas.main(["--check"]) == 0
    stub = next(mirror.glob("*/SKILL.md"))
    stub.write_text(stub.read_text(encoding="utf-8") + "\nstale\n", encoding="utf-8")
    assert sas.main(["--check"]) == 1
