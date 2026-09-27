"""Tests for src/history.py, scripts/fetch_history.py (planning only), scripts/fetch_portwatch.py
(parsing only) and scripts/export_daily_vessels.py. All records are SIMULATED (MMSI 99900xxxx)
but use VesselAPI's real field names; PortWatch JSON follows the real ArcGIS layout."""
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import export_daily_vessels as ed  # noqa: E402
import fetch_history as fh  # noqa: E402
import fetch_portwatch as pw  # noqa: E402
from src import history  # noqa: E402

UTC = timezone.utc


def rec(mmsi, ts, lon=56.3, lat=26.3, name=None):
    return {"mmsi": mmsi, "vessel_name": name or f"SIM {mmsi}", "latitude": lat, "longitude": lon,
            "timestamp": ts, "sog": 8.0, "cog": 90.0, "suspected_glitch": False}


@pytest.fixture
def sources(tmp_path):
    snaps = tmp_path / "snap.jsonl"          # recorder: answer at 06:00 = 04:00-06:00 on 09/27
    snaps.write_text(json.dumps({"real_data": True, "fetched_at": "2026-09-27T06:00:00+00:00",
                                 "truncated": False,
                                 "vessels": [rec(999000001, "2026-09-27T05:00:00Z"),
                                             rec(999000001, "2026-09-27T05:30:00Z", lon=56.4)]}) + "\n")
    hist = tmp_path / "hist.jsonl"
    hist.write_text(
        json.dumps({"status": 200, "window_from": "2026-09-25T03:00:00Z", "window_to": "2026-09-25T07:00:00Z",
                    "truncated": True,                      # only 06:00-07:00 really covered
                    "vessels": [rec(999000002, "2026-09-25T06:00:00Z"),
                                rec(999000002, "2026-09-25T06:30:00Z", lon=56.15),
                                rec(999000002, "2026-09-25T06:50:00Z", lon=56.05)]}) + "\n"
        + json.dumps({"status": 200, "window_from": "2026-09-24T03:00:00Z", "window_to": "2026-09-24T07:00:00Z",
                      "truncated": False, "vessels": []}) + "\n"
        + json.dumps({"status": None, "window_from": "2026-09-23T03:00:00Z", "window_to": "2026-09-23T07:00:00Z",
                      "error": "HTTP 400: too old", "vessels": []}) + "\n")
    probe = tmp_path / "probe.json"
    probe.write_text(json.dumps({"requested_from": "2026-09-20T03:00:00Z", "requested_to": "2026-09-20T07:00:00Z",
                                 "real_data": True, "response": {"vessels": [rec(999000003, "2026-09-20T04:00:00Z")]}}))
    return history.read_sources(snaps, hist, probe)


def test_read_sources_turns_every_answer_into_covered_time(sources):
    records, intervals, failures = sources
    assert len(records) == 6
    spans = [(i["start"].strftime("%m-%d %H:%M"), i["end"].strftime("%H:%M")) for i in intervals]
    assert spans == [("09-20 03:00", "07:00"), ("09-24 03:00", "07:00"),
                     ("09-25 06:00", "07:00"), ("09-27 04:00", "06:00")]
    assert failures[0]["status"] is None and "too old" in failures[0]["error"]


def test_plan_skips_windows_already_covered(sources):
    _, intervals, _ = sources
    now = datetime(2026, 9, 27, 7, 45, tzinfo=UTC)
    to_fetch, skipped = fh.plan_windows(now, 7, [3], 4, intervals)
    fetch_days = [w[0].day for w in to_fetch]
    assert fetch_days == [27, 26, 25, 23, 22, 21]           # newest first
    assert [w[0].day for w in skipped] == [24, 20]           # 25 only 25% covered -> fetched again


def test_daily_rows_separate_vessels_empty_days_and_unobserved_days(sources):
    records, intervals, _ = sources
    positions, skipped = history.normalize_all(records)
    portwatch = {"2026-09-20": {"n_total": "6", "n_tanker": "1"}}
    rows = ed.build_rows(positions, intervals, {}, portwatch, {}, date(2026, 9, 20), date(2026, 9, 27))
    by_date = {}
    for r in rows:
        by_date.setdefault(r["date"], []).append(r)
    assert [r["row_type"] for r in by_date["2026-09-20"]] == ["vessel"]
    assert by_date["2026-09-20"][0]["portwatch_transits_total"] == "6"
    assert by_date["2026-09-21"][0]["row_type"] == "not_observed"
    assert by_date["2026-09-24"][0]["row_type"] == "no_vessel_seen"
    v = by_date["2026-09-27"][0]
    assert (v["date_mmddyyyy"], v["first_seen_utc"], v["last_seen_utc"], v["positions_that_day"]) == \
        ("09/27/2026", "05:00", "05:30", 2)
    assert (v["latitude"], v["longitude"], v["first_longitude"]) == (26.3, 56.4, 56.3)
    assert v["tanker_class"] == "Unknown" and v["day_observed_utc"] == "04:00–06:00"
    assert v["source"] == "VesselAPI recorded"


def test_crossing_between_two_close_positions_is_attached_to_the_day(sources):
    records, _, _ = sources
    positions, _ = history.normalize_all(records)
    # 999000002: 56.15 at 06:30 -> 56.05 at 06:50 (20 min) crosses the temporary gate westwards
    assert ed.crossings_by_day(positions, ed.TEMP_GATE) == {("2026-09-25", "999000002"): "INBOUND 06:50"}
    # same move with a 50-minute gap is NOT a crossing (Person B's 30-minute rule)
    far = [p for p in positions if p["timestamp_utc"] != "2026-09-25T06:30:00Z"]
    assert ed.crossings_by_day(far, ed.TEMP_GATE) == {}


def test_portwatch_parse_accepts_date_string_or_epoch():
    data = {"features": [
        {"attributes": {"year": 2026, "month": 9, "day": 19, "n_total": 6, "n_tanker": 1}},
        {"attributes": {"date": "2026-09-20", "n_total": 1, "n_tanker": 0}},
        {"attributes": {"date": 1790208000000, "n_total": 3, "n_tanker": 0}},   # 2026-09-24 00:00 UTC
    ]}
    rows = pw.parse_features(data)
    assert [r["date"] for r in rows] == ["2026-09-19", "2026-09-20", "2026-09-24"]
    with pytest.raises(ValueError):
        pw.parse_features({"error": {"code": 400}})
