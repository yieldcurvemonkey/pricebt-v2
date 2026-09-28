"""skills/pricebt-enterprise-integration/scripts/record_replay.py: record once, replay offline, and a
replay miss must raise (an offline run can never silently reach a live client)."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "pricebt-enterprise-integration" / "scripts"))
import record_replay  # noqa: E402


class FakeClient:
    def __init__(self):
        self.calls = 0
        self.version = "1.2.3"

    def market(self, as_of, curve):
        self.calls += 1
        return {"as_of": as_of, "curve": curve, "df_10y": 0.7}

    def price(self, specs, measures=("PV",)):
        self.calls += 1
        return [{m: float(len(s)) for m in measures} for s in specs]


def test_record_then_replay_serves_identical_results_without_the_client(tmp_path):
    live = FakeClient()
    rec = record_replay.wrap(live, tmp_path, mode="record")
    m = rec.market(date(2024, 5, 20), "USD.SOFR")
    p = rec.price(("ab", "abc"), measures=("PV", "DV01"))
    assert live.calls == 2

    replay = record_replay.wrap(None, tmp_path, mode="replay")
    assert replay.market(date(2024, 5, 20), "USD.SOFR") == m
    assert replay.price(("ab", "abc"), measures=("PV", "DV01")) == p
    assert replay.hits == 2 and live.calls == 2


def test_replay_miss_raises_instead_of_reaching_anything(tmp_path):
    replay = record_replay.wrap(None, tmp_path, mode="replay")
    with pytest.raises(record_replay.ReplayMiss):
        replay.market(date(2024, 5, 21), "USD.SOFR")


def test_different_arguments_are_different_keys_and_attributes_pass_through(tmp_path):
    live = FakeClient()
    rec = record_replay.wrap(live, tmp_path, mode="record")
    rec.market(date(2024, 5, 20), "USD.SOFR")
    rec.market(date(2024, 5, 20), "EUR.ESTR")
    assert live.calls == 2
    assert rec.version == "1.2.3"  # non-callable attribute: passed through untouched
    assert len(list(Path(tmp_path).rglob("*.pkl"))) == 2


def test_mode_defaults_to_replay_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("PRICEBT_CLIENT_MODE", raising=False)
    assert record_replay.wrap(FakeClient(), tmp_path).mode == "replay"
    monkeypatch.setenv("PRICEBT_CLIENT_MODE", "live")
    assert record_replay.wrap(FakeClient(), tmp_path).mode == "live"
    with pytest.raises(ValueError):
        record_replay.wrap(FakeClient(), tmp_path, mode="bogus")
