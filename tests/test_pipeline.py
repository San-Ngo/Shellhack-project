"""Tests for src/pipeline.py. Snapshot records are SIMULATED (real VesselAPI field names)."""
import json

import pytest

from src.database import get_recent_crossings, get_vessel, init_db
from src.pipeline import load_detector, load_positions, parse_gate, run
from tests.stub_detector import BrokenDetector, StubDetector


def rec(mmsi, ts, lat=26.4, lon=56.4, **extra):
    r = {"mmsi": mmsi, "vessel_name": f"SIM {mmsi}", "latitude": lat, "longitude": lon,
         "timestamp": ts, "sog": 10.0, "cog": 90.0, "suspected_glitch": False}
    r.update(extra)
    return r


def write_snapshots(path, *snapshots):
    with open(path, "w", encoding="utf-8") as f:
        for vessels in snapshots:
            f.write(json.dumps({"source": "vesselapi", "real_data": True, "vessels": vessels}) + "\n")
    return path


@pytest.fixture
def snapshots(tmp_path):
    # two overlapping snapshots, out of order inside, one duplicate, one glitch, one bad coord
    return write_snapshots(
        tmp_path / "snap.jsonl",
        [rec(111111111, "2026-09-26T22:10:00Z"), rec(222222222, "2026-09-26T22:00:00Z"),
         rec(111111111, "2026-09-26T22:00:00Z")],
        [rec(111111111, "2026-09-26T22:10:00Z"),                       # duplicate of snapshot 1
         rec(111111111, "2026-09-26T22:20:00Z", lon=56.5),
         rec(222222222, "2026-09-26T22:05:00Z", suspected_glitch=True),
         rec(333333333, "2026-09-26T22:15:00Z", lat=95)],
    )


def test_load_positions_dedupes_sorts_and_counts(snapshots):
    positions, stats = load_positions(snapshots)
    times = [p["timestamp_utc"] for p in positions]
    assert times == sorted(times)
    assert stats["positions"] == 4 and stats["duplicates"] == 1 and stats["skipped"] == 2
    assert stats["vessels"] == 2 and stats["real_data"] is True


def test_bad_lines_are_skipped_not_fatal(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text('not json\n\n' + json.dumps({"real_data": True, "vessels": [
        rec(111111111, "2026-09-26T22:00:00Z")]}) + "\n", encoding="utf-8")
    positions, stats = load_positions(p)
    assert stats["bad_lines"] == 1 and len(positions) == 1


def test_non_real_snapshot_is_flagged(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps({"real_data": False, "vessels": []}) + "\n", encoding="utf-8")
    assert load_positions(p)[1]["real_data"] is False


def test_run_without_detector_stores_latest_vessels_only(snapshots):
    conn = init_db(":memory:")
    positions, _ = load_positions(snapshots)
    stats = run(positions, conn, detector=None)
    assert stats["stored_positions"] == 4 and stats["events"] == 0
    assert get_vessel(conn, "111111111")["last_update_utc"] == "2026-09-26T22:20:00Z"
    assert get_recent_crossings(conn) == []


def test_run_passes_positions_in_time_order_and_stores_events(snapshots):
    conn = init_db(":memory:")
    positions, _ = load_positions(snapshots)
    det = StubDetector(gate_coordinates=[(56.0, 26.0), (56.0, 27.0)])
    stats = run(positions, conn, det, source="test-stub")
    assert det.calls == sorted(det.calls)
    assert stats["crossings_stored"] == 1
    [c] = get_recent_crossings(conn)
    assert c["crossing_time"] == "2026-09-26T22:10:00Z"   # +00:00 from detector → stored as Z
    assert c["direction"] == "EASTBOUND" and c["source"] == "test-stub"


def test_replaying_twice_does_not_duplicate_crossings(snapshots):
    conn = init_db(":memory:")
    positions, _ = load_positions(snapshots)
    run(positions, conn, StubDetector(gate_coordinates=[(0, 0), (0, 1)]))
    stats = run(positions, conn, StubDetector(gate_coordinates=[(0, 0), (0, 1)]))
    assert stats["crossings_duplicate"] == 1 and len(get_recent_crossings(conn)) == 1


def test_detector_errors_do_not_stop_the_replay(snapshots):
    conn = init_db(":memory:")
    positions, _ = load_positions(snapshots)
    stats = run(positions, conn, BrokenDetector(gate_coordinates=[(0, 0), (0, 1)]))
    assert stats["detector_errors"] == 4 and stats["stored_positions"] == 4


def test_invalid_event_from_detector_is_rejected_not_stored(snapshots):
    class NoDirection(StubDetector):
        def process_position(self, position):
            e = super().process_position(position)
            return dict(e, direction="") if e else None

    conn = init_db(":memory:")
    positions, _ = load_positions(snapshots)
    stats = run(positions, conn, NoDirection(gate_coordinates=[(0, 0), (0, 1)]))
    assert stats["crossings_rejected"] == 1 and get_recent_crossings(conn) == []


def test_parse_gate_uses_lon_lat_order_and_validates():
    assert parse_gate("56.1,26.2,56.3,26.9") == [(56.1, 26.2), (56.3, 26.9)]
    assert parse_gate(None) is None
    for bad in ["1,2,3", "56,95,57,26", "56,26,56,26", "a,b,c,d"]:
        with pytest.raises(ValueError):
            parse_gate(bad)


def test_load_detector_by_import_path():
    det = load_detector("tests.stub_detector:StubDetector", [(56.0, 26.0), (56.0, 27.0)])
    assert det.gate_coordinates == [(56.0, 26.0), (56.0, 27.0)]
    with pytest.raises(ValueError):
        load_detector("no_colon_here", [(0, 0), (0, 1)])


def test_run_stores_full_track_with_source_label_and_no_duplicates_on_replay(snapshots):
    positions, _ = load_positions(snapshots)
    conn = init_db(":memory:")
    first = run(positions, conn, source="simulated")
    assert first["track_positions_stored"] == len(positions) == 4
    second = run(positions, conn, source="simulated")
    assert second["track_positions_stored"] == 0 and second["track_positions_duplicate"] == 4
    rows = conn.execute("SELECT mmsi, source FROM vessel_positions").fetchall()
    assert len(rows) == 4 and {r["source"] for r in rows} == {"simulated"}
    # vessels still holds 1 row per MMSI
    assert conn.execute("SELECT COUNT(*) FROM vessels").fetchone()[0] == 2
    conn.close()
