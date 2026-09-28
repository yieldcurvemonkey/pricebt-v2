"""Every relative markdown link in docs/v2/*.md and README.md must resolve to a real file
(IMPLEMENTATION_PLAN.md P5.1). External links (http/https/mailto) and pure-anchor links (#section)
are skipped; a `path#fragment` link is checked by `path` alone (fragments are not verified).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOC_FILES = sorted((ROOT / "docs" / "v2").glob("*.md")) + [ROOT / "README.md"]

_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_EXTERNAL_RE = re.compile(r"^(?:[a-zA-Z][a-zA-Z0-9+.-]*:)")  # any URI scheme, e.g. http:, mailto:


def _links_in(md_path: Path) -> list[str]:
    text = md_path.read_text(encoding="utf8")
    return _LINK_RE.findall(text)


def _relative_targets(links: list[str]) -> list[str]:
    out = []
    for link in links:
        link = link.strip().split(" ", 1)[0]  # drop an optional "title" after the URL
        if not link or link.startswith("#") or _EXTERNAL_RE.match(link):
            continue
        out.append(link.split("#", 1)[0])  # strip a #fragment
    return out


def _doc_ids():
    return [str(p.relative_to(ROOT)) for p in DOC_FILES]


@pytest.mark.parametrize("md_path", DOC_FILES, ids=_doc_ids())
def test_relative_links_resolve(md_path: Path):
    missing = [target for target in _relative_targets(_links_in(md_path)) if not (md_path.parent / target).exists()]
    assert not missing, f"{md_path.relative_to(ROOT)}: broken link(s) {missing}"


def test_checker_catches_a_broken_link(tmp_path):
    # Non-vacuity: prove the scan above can fail, not just pass because nothing is checked.
    bad = tmp_path / "bad.md"
    bad.write_text("see [nope](./does_not_exist.md) and [ok](./bad.md)", encoding="utf8")
    missing = [target for target in _relative_targets(_links_in(bad)) if not (bad.parent / target).exists()]
    assert missing == ["./does_not_exist.md"]
