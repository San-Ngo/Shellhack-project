"""End-to-end: recorded snapshot file -> normalizer -> Person B's CrossingDetector -> SQLite.

Uses demo/simulated_tracks.jsonl (SIMULATED, clearly labelled) and Person B's real
detector from src/crossing_detector.py. Direction labels are not asserted exactly
because Person B is renaming EASTBOUND/WESTBOUND to INBOUND/OUTBOUND.
"""
from pathlib import Path

import pytest

pytest.importorskip("shapely")

from src.crossing_detector import CrossingDetector  # noqa: E402  (Person B's code)
from src.database import get_recent_crossings, get_vessel, init_db  # noqa: E402
from src.pipeline import load_positions, run  # noqa: E402

SIM_FILE = Path(__file__).resolve().parent.parent / "demo" / "simulated_tracks.jsonl"
GATE = [(56.1, 26.10), (56.1, 26.80)]  # temporary gate proposed by Person B (lon, lat)
KNOWN_DIRECTIONS = {"EASTBOUND", "WESTBOUND", "INBOUND", "OUTBOUND"}


@pytest.fixture
def replay():
    positions, stats = load_positions(SIM_FILE)
    conn = init_db(":memory:")
    yield positions, stats, conn
    conn.close()


def test_simulated_file_is_labelled_as_not_real(replay):
    positions, stats, _ = replay
    assert stats["real_data"] is False
    assert all(p["ship_name"].startswith("SIM-") for p in positions)


def test_two_crossings_one_per_direction_stored_as_simulated(replay):
    positions, _, conn = replay
    stats = run(positions, conn, CrossingDetector(gate_coordinates=GATE), source="simulated")
    assert stats["crossings_stored"] == 2 and stats["detector_errors"] == 0

    crossings = {c["ship_name"]: c for c in get_recent_crossings(conn)}
    assert set(crossings) == {"SIM-WESTBOUND-1", "SIM-EASTBOUND-1"}   # anchored vessel: none
    assert {c["direction"] for c in crossings.values()} <= KNOWN_DIRECTIONS
    assert crossings["SIM-WESTBOUND-1"]["direction"] != crossings["SIM-EASTBOUND-1"]["direction"]
    for c in crossings.values():
        assert c["source"] == "simulated"
        assert c["crossing_time"].endswith("Z")
        assert c["longitude"] == pytest.approx(56.1)


def test_unknown_ship_type_stays_null_end_to_end(replay):
    positions, _, conn = replay
    run(positions, conn, CrossingDetector(gate_coordinates=GATE), source="simulated")
    assert all(c["ship_type"] is None for c in get_recent_crossings(conn))
    assert get_vessel(conn, "999000001")["ship_type"] is None


def test_replaying_the_same_data_does_not_duplicate_crossings(replay):
    positions, _, conn = replay
    run(positions, conn, CrossingDetector(gate_coordinates=GATE), source="simulated")
    second = run(positions, conn, CrossingDetector(gate_coordinates=GATE), source="simulated")
    assert second["crossings_stored"] == 0 and second["crossings_duplicate"] == 2
    assert len(get_recent_crossings(conn)) == 2
