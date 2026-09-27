"""Tests for normalize_vesselapi and the recorder's config guards.

Records below are SIMULATED, shaped like the real VesselAPI response seen on
2026-09-26 (same field names). Values and MMSIs are made up.
"""
import pytest

from src.normalizer import StaticCache, normalize_vesselapi
from src import vesselapi_recorder as rec


def sim_record(**overrides):
    r = {"mmsi": 123456789, "imo": 1234567, "vessel_name": "SIM VESSEL",
         "latitude": 26.4, "longitude": 56.4,
         "location": {"type": "Point", "coordinates": [56.4, 26.4]},
         "timestamp": "2026-09-26T23:39:33Z", "processed_timestamp": "2026-09-26T23:40:05.3Z",
         "suspected_glitch": False, "cog": 60.7, "sog": 11.5, "heading": 60, "nav_status": 0}
    r.update(overrides)
    return r


def test_valid_record_matches_shared_contract():
    r = normalize_vesselapi(sim_record())
    assert r.kind == "position"
    assert r.data == {
        "mmsi": "123456789", "ship_name": "SIM VESSEL", "ship_type": None,
        "latitude": 26.4, "longitude": 56.4, "speed_knots": 11.5, "course_deg": 60.7,
        "timestamp_utc": "2026-09-26T23:39:33Z", "source_message_type": "VesselAPI.bounding-box",
    }


def test_uses_source_timestamp_and_converts_offsets_to_utc():
    r = normalize_vesselapi(sim_record(timestamp="2026-09-26T19:39:33-04:00"))
    assert r.data["timestamp_utc"] == "2026-09-26T23:39:33Z"


@pytest.mark.parametrize("bad", [None, "", "not a time", "2026-09-26T23:39:33"])
def test_missing_bad_or_naive_timestamp_is_skipped(bad):
    assert normalize_vesselapi(sim_record(timestamp=bad)).kind == "skip"


def test_suspected_glitch_is_skipped():
    r = normalize_vesselapi(sim_record(suspected_glitch=True))
    assert r.kind == "skip" and "glitch" in r.reason


def test_missing_mmsi_and_bad_coordinates_are_skipped():
    assert normalize_vesselapi(sim_record(mmsi=None)).kind == "skip"
    assert normalize_vesselapi(sim_record(latitude=95)).kind == "skip"
    assert normalize_vesselapi(sim_record(longitude=None)).kind == "skip"


def test_missing_name_is_null_and_type_never_guessed():
    r = normalize_vesselapi(sim_record(vessel_name=None))
    assert r.data["ship_name"] is None and r.data["ship_type"] is None


def test_type_from_cache_is_merged_when_known():
    cache = StaticCache()
    cache.update("123456789", None, 80)
    assert normalize_vesselapi(sim_record(), cache).data["ship_type"] == 80


def test_garbage_does_not_crash():
    assert normalize_vesselapi("x").kind == "skip"
    assert normalize_vesselapi({}).kind == "skip"


def test_recorder_rejects_box_over_4_degrees(monkeypatch):
    monkeypatch.setenv("VESSELAPI_BBOX", "25.0,27.5,55.0,58.0")  # 5.5° — the real 400 case
    with pytest.raises(rec.StopRecording):
        rec.load_bbox()


def test_recorder_default_box_is_within_limit(monkeypatch):
    monkeypatch.delenv("VESSELAPI_BBOX", raising=False)
    b = rec.load_bbox()
    span = (b["filter.latTop"] - b["filter.latBottom"]) + (b["filter.lonRight"] - b["filter.lonLeft"])
    assert span <= 4.0


def test_recorder_requires_key(monkeypatch):
    monkeypatch.setenv("VESSELAPI_KEY", "your_key_here")
    with pytest.raises(rec.StopRecording):
        rec.load_key()


def test_recorder_counts_pages_and_stops_at_budget(monkeypatch):
    calls = []

    def fake_fetch(key, bbox, token):
        calls.append(token)
        return {"vessels": [sim_record()], "nextToken": f"t{len(calls)}"}, "100"

    monkeypatch.setattr(rec, "fetch_page", fake_fetch)
    record, used, remaining = rec.snapshot("k", {}, max_pages=5, calls_left=2)
    assert used == 2 and len(calls) == 2          # never exceeds remaining budget
    assert record["truncated"] is True and record["real_data"] is True
