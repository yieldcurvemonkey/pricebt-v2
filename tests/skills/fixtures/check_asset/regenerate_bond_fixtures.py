"""Regenerate the bond check_asset fixtures (bad_bond_*.yaml) from tests/assets/toy_usd_bond.yaml: a verbatim
copy with ONE line broken each and a header naming the mistake and the row it must FAIL
(tests/skills/test_skill_check_asset_ir.py BROKEN_IR). Run it after any change to the toy bond config:

    python tests/skills/fixtures/check_asset/regenerate_bond_fixtures.py
"""
from pathlib import Path

OUT = Path(__file__).resolve().parent
TOY = OUT.parents[2] / "assets" / "toy_usd_bond.yaml"

FIXTURES = {
    "bad_bond_dv01_positive": (
        "the bond's IRDelta is the library's risk per -1bp (positive for a long bond) mapped without\n"
        "# negating. Must FAIL bond_dv01_sign.",
        "  delta:           {expr: 'tb.delta(market, trade)', unit: ccy_per_bp}",
        "  delta:           {expr: '-tb.delta(market, trade)', unit: ccy_per_bp}   # MISTAKE: per -1bp (long bond > 0)",
    ),
    "bad_bond_financing_sign": (
        "FinancingToDate reported as the interest amount (positive for a long that PAYS the repo)\n"
        "# instead of holder-signed. Must FAIL bond_financing and nothing else (the engine books the config's own\n"
        "# numbers consistently, so bond_holding_cash passes).",
        "  financing:       {expr: 'tb.financing_to_date(market, trade)', unit: ccy}",
        "  financing:       {expr: '-tb.financing_to_date(market, trade)', unit: ccy}   # MISTAKE: interest paid as a positive number",
    ),
    "bad_bond_clean_dirty": (
        "the clean price adds the accrued instead of subtracting it. Must FAIL bond_clean_dirty and\n"
        "# nothing else.",
        "  clean_price:     {expr: 'tb.clean_price(market, trade, pricebt_date)', unit: pct}",
        "  clean_price:     {expr: 'tb.dirty_price(market, trade, pricebt_date) + 100 * tb.accrued(market, trade, pricebt_date) / trade[\"face\"]', unit: pct}   # MISTAKE: + accrued",
    ),
    "bad_bond_forward_parity": (
        "ForwardPrice finances Price to the horizon but keeps a coupon paid before it (no\n"
        "# coupon term). Must FAIL bond_forward_parity (on a date whose horizon holds a coupon) and nothing else.",
        "  forward_price:   {expr: 'tb.forward_price(market, trade)', unit: ccy}",
        "  forward_price:   {expr: 'tb.npv(market, trade) * (1 + tb._repo(market, trade) * (tb.horizon(tb.settle(market.curve.ref_date)) - tb.settle(market.curve.ref_date)).days / 360)', unit: ccy}   # MISTAKE: coupons before H kept",
    ),
}


def main() -> None:
    text = TOY.read_text(encoding="utf-8")
    assert "asset: toy_usd_bond\n" in text
    for name, (why, old, new) in FIXTURES.items():
        assert text.count(old) == 1, (name, old)
        body = text.replace("asset: toy_usd_bond\n", f"asset: {name}\n").replace(old, new)
        header = (f"# Broken on purpose (check_asset fixture): {why}\n"
                  "# Copy of tests/assets/toy_usd_bond.yaml with ONE thing broken: regenerate_bond_fixtures.py rewrites it from the toy.\n")
        (OUT / f"{name}.yaml").write_bytes((header + body).encode("utf-8"))
        print("wrote", name)


if __name__ == "__main__":
    main()
