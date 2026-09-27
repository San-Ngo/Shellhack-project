"""Tests for scripts/export_powerbi.py. Vessel details here are SIMULATED examples."""
import csv
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import export_powerbi as ex  # noqa: E402


@pytest.mark.parametrize("vessel, expected", [
    (None, "Unknown"),
    ({"vessel_type": None, "vessel_subtype": None}, "Unknown"),
    ({"vessel_type": "Cargo A", "vessel_subtype": "Container Ship"}, "Not tanker"),
    ({"vessel_type": "Tanker", "vessel_subtype": "Crude Oil Tanker"}, "Oil tanker"),
    ({"vessel_type": "Tanker", "vessel_subtype": "Chemical/Oil Products Tanker"}, "Oil tanker"),
    ({"vessel_type": "Tanker", "vessel_subtype": "LNG Tanker"}, "Tanker (other/unspecified cargo)"),
    ({"vessel_type": "Tanker", "vessel_subtype": None}, "Tanker (other/unspecified cargo)"),
])
def test_tanker_class_uses_only_source_text(vessel, expected):
    assert ex.tanker_class(vessel) == expected


def test_date_and_location_formats():
    t = ex.time_fields("2026-09-27T03:33:26Z")
    assert t == {"timestamp_utc": "2026-09-27 03:33:26", "date": "2026-09-27",
                 "date_mmddyyyy": "09/27/2026", "time_utc": "03:33"}
    assert ex.location_text(26.19293, 56.1) == "26.1929°N, 56.1000°E"


def test_export_demo_file(tmp_path):
    details = tmp_path / "details.jsonl"
    details.write_text(json.dumps({"mmsi": "999000001", "status": 200, "response": {"vessel": {
        "name": "SIM-WESTBOUND-1", "vessel_type": "Tanker", "vessel_subtype": "Crude Oil Tanker"}}}) + "\n")
    out = tmp_path / "pbi"
    sys.argv = ["x", "--snapshots", str(ROOT / "demo" / "simulated_tracks.jsonl"),
                "--details", str(details), "--out", str(out)]
    assert ex.main() == 0

    with open(out / "vessels_latest.csv", encoding="utf-8-sig") as f:
        latest = {r["mmsi"]: r for r in csv.DictReader(f)}
    assert latest["999000001"]["tanker_class"] == "Oil tanker"
    assert latest["999000002"]["tanker_class"] == "Unknown"          # no details -> not guessed
    assert all(r["data_kind"] == "SIMULATED" for r in latest.values())
    with open(out / "crossings.csv", encoding="utf-8-sig") as f:
        crossings = list(csv.DictReader(f))
    assert len(crossings) == 2 and {c["date_mmddyyyy"] for c in crossings} == {"09/26/2026"}
