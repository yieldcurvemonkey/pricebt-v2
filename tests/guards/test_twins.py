"""Non-vacuity twins (DESIGN.md section 12.2 item 7): each scanning guard is pointed at a synthetic
tree with ONE planted violation and must report exactly that violation; the same guard run on a
clean file, or on an explicitly allowed variant, must report nothing. This is the global CLAUDE.md
rule ("verify a checker against a known answer") applied to the scanners themselves.
"""
from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Dict

import pytest

from guards import scan

pytestmark = pytest.mark.core


def tree(root: Path, files: Dict[str, str]) -> Path:
    for relp, text in files.items():
        p = root / relp
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text), encoding="utf8")
    return root


# ------------------------------------------------------------------------------ item 1: import roots
def test_twin_import_scan_must_fail_a_disallowed_root(tmp_path):
    src = tree(tmp_path, {"pricebt/a.py": "import rateslib\n"})
    hits = scan.scan_import_roots(src / "pricebt" / "a.py", src)
    assert [h.token for h in hits] == ["rateslib"]


def test_twin_import_scan_must_pass_an_allowed_root_and_a_relative_import(tmp_path):
    src = tree(tmp_path, {"pricebt/a.py": "import numpy as np\nfrom .errors import ConfigError\n"})
    assert scan.scan_import_roots(src / "pricebt" / "a.py", src) == []


# ------------------------------------------------------------------------------ item 2: vendor token scan
def test_twin_token_scan_must_fail_arbs_supabase_env_var(tmp_path):
    """DESIGN.md section 12.2 item 2's own example: letter look-arounds must still catch this."""
    src = tree(tmp_path, {"pricebt/a.py": 'import os\nos.environ["ARBS_SUPABASE_ENABLED"] = "0"\n'})
    hits = scan.scan_vendor_tokens(src / "pricebt" / "a.py", src)
    assert {h.token for h in hits} == {"arbs", "supabase"}


def test_twin_token_scan_must_fail_a_source_key_naming_the_live_source(tmp_path):
    src = tree(tmp_path, {"pricebt/a.py": 'key = "usd_sofr_eris_rlbasic"\n'})
    hits = scan.scan_vendor_tokens(src / "pricebt" / "a.py", src)
    assert [h.token for h in hits] == ["eris"]


def test_twin_token_scan_must_fail_a_getattr_of_a_banned_method_name(tmp_path):
    src = tree(tmp_path, {"pricebt/a.py": 'm = object()\ngetattr(m, "bulk_get_data")\n'})
    hits = scan.scan_vendor_tokens(src / "pricebt" / "a.py", src)
    assert [h.token for h in hits] == ["bulk_get_data"]


def test_twin_token_scan_must_fail_an_fstring_naming_arbs(tmp_path):
    src = tree(tmp_path, {"pricebt/a.py": 'd = 1\nraise ValueError(f"ARBS served {d}")\n'})
    hits = scan.scan_vendor_tokens(src / "pricebt" / "a.py", src)
    assert [h.token for h in hits] == ["arbs"]


def test_twin_token_scan_must_pass_series_and_a_trailing_comment(tmp_path):
    """DESIGN.md section 12.2 item 2: 'ARBS_SUPABASE_ENABLED is caught while Series is not', and a
    trailing comment mentioning ARBS must not fail the token scan (comments are dropped entirely)."""
    src = tree(
        tmp_path,
        {
            "pricebt/a.py": "import pandas as pd\nx = pd.Series\n",
            "pricebt/b.py": "x = 1  # uses ARBS\n",
            "pricebt/c.py": "class OisFixingCashAccrualModel:\n    pass\n",
            "pricebt/d.py": "ois_fixings = {}\n",
        },
    )
    for name in ("a.py", "b.py", "c.py", "d.py"):
        assert scan.scan_vendor_tokens(src / "pricebt" / name, src) == [], name


# ------------------------------------------------------------------------------ item 2: gs_quant scan
def test_twin_gs_quant_scan_must_fail_an_import_and_pass_a_docstring_mention(tmp_path):
    src = tree(
        tmp_path,
        {
            "pricebt/bad.py": "import gs_quant\n",
            "pricebt/ok.py": '"""Ported from gs_quant.backtests.strategy; see NOTICE."""\n',
        },
    )
    assert [h.token for h in scan.scan_gs_quant_tokens(src / "pricebt" / "bad.py", src)] == ["gs_quant"]
    assert scan.scan_gs_quant_tokens(src / "pricebt" / "ok.py", src) == []


# ------------------------------------------------------------------------------ item 4: asset-agnostic scan
def test_twin_asset_agnostic_scan_must_fail_a_literal_notional_key(tmp_path):
    src = tree(tmp_path, {"pricebt/assets/a.py": "def f(terms):\n    return terms['notional']\n"})
    hits = scan.scan_asset_agnostic_tokens(src / "pricebt" / "assets" / "a.py", src)
    assert [h.token for h in hits] == ["notional"]


def test_twin_asset_agnostic_scan_must_pass_a_neutral_module(tmp_path):
    src = tree(tmp_path, {"pricebt/assets/a.py": "def f(x):\n    return x.upper()\n"})
    assert scan.scan_asset_agnostic_tokens(src / "pricebt" / "assets" / "a.py", src) == []


def test_twin_asset_agnostic_file_set_is_scoped_to_assets_markets_and_three_risk_files(tmp_path):
    pkg = (
        tree(
            tmp_path,
            {
                "pricebt/assets/a.py": "",
                "pricebt/markets/b.py": "",
                "pricebt/risk/results.py": "",
                "pricebt/risk/transform.py": "",
                "pricebt/risk/core.py": "",
                "pricebt/risk/contracts.py": "",
                "pricebt/risk/__init__.py": "",
                "pricebt/instrument/__init__.py": "",
                "pricebt/backtests/actions.py": "",
            },
        )
        / "pricebt"
    )
    got = sorted(p.relative_to(pkg).as_posix() for p in scan.asset_agnostic_files(pkg))
    assert got == ["assets/a.py", "markets/b.py", "risk/core.py", "risk/results.py", "risk/transform.py"]
