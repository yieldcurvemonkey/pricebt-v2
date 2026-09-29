"""The measure-contract tables in docs/v2/ASSET_CONFIG_GUIDE.md are generated from
`pricebt.risk.contracts` (KINDS, CONTRACTS, FRAME_COLUMNS), so the guide cannot drift from the code.

Regenerate after changing contracts.py (from the repo root):
    PYTHONPATH="src;tests" python tests/test_docs_contract_tables.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from pricebt.risk import contracts

GUIDE = Path(__file__).resolve().parents[1] / "docs" / "v2" / "ASSET_CONFIG_GUIDE.md"
BEGIN = "<!-- BEGIN generated contract tables: tests/test_docs_contract_tables.py; do not edit by hand -->"
END = "<!-- END generated contract tables -->"
_REGION = re.compile(re.escape(BEGIN) + r"\n(.*?)" + re.escape(END), re.S)


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def _units(kind: str) -> str:
    units, _ = contracts.KINDS[kind]
    return "frame (`returns: frame`)" if units is None else ", ".join(f"`{u}`" for u in sorted(units))


def render() -> str:
    lines = ["| Kind | Allowed units | Must be intensive (`scale_with_quantity: false`) |", "|---|---|---|"]
    for kind, (_units_, intensive) in contracts.KINDS.items():
        lines.append(f"| `{kind}` | {_units(kind)} | {'yes' if intensive else '-'} |")
    base = {r.measure: r for r in contracts.CONTRACTS["IRSwap"]}
    for cls, reqs in contracts.CONTRACTS.items():
        lines += ["", f"**`{cls}`** ({len(reqs)} measures)", "", "| Measure | Kind | Forms | Contract |", "|---|---|---|---|"]
        for r in reqs:
            same = cls != "IRSwap" and base.get(r.measure) == r
            doc = "as `IRSwap`" if same else _cell(r.doc)
            lines.append(f"| `{r.measure}` | `{r.kind}` | {', '.join(r.forms)} | {doc} |")
    lines += ["", "Required frame columns: " + "; ".join(
        f"`{m}`: {', '.join(f'`{c}`' for c in cols)} (scale columns must include {', '.join(f'`{c}`' for c in contracts.FRAME_SCALE_COLUMNS.get(m, ()))})"
        for m, cols in contracts.FRAME_COLUMNS.items()) + "."]
    return "\n".join(lines) + "\n"


def _region(text: str) -> str:
    m = _REGION.search(text)
    assert m, f"{GUIDE.name}: generated-table markers not found"
    return m.group(1)


def test_guide_contract_tables_match_contracts_py():
    assert _region(GUIDE.read_text(encoding="utf8")) == render(), (
        f"{GUIDE.name} is stale: regenerate with `PYTHONPATH=\"src;tests\" python tests/test_docs_contract_tables.py`")


def test_the_check_catches_a_one_word_drift():
    """Non-vacuity: the comparison fails when the guide's region differs from the code by one word."""
    region = _region(GUIDE.read_text(encoding="utf8"))
    stale = region.replace("pay-fixed swap > 0", "pay-fixed swap < 0", 1)
    assert region == render() and stale != region and stale != render()


if __name__ == "__main__":
    text = GUIDE.read_text(encoding="utf8")
    new = _REGION.sub(lambda _m: BEGIN + "\n" + render() + END, text, count=1)
    with open(GUIDE, "w", encoding="utf8", newline="\n") as f:
        f.write(new)
    sys.exit(0)
