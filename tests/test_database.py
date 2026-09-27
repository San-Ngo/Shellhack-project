"""Tests for src/database.py. All vessels and crossings here are SIMULATED."""
import pytest

from src.database import (get_recent_crossings, get_track, get_vessel, init_db, insert_crossing,
                          insert_position, upsert_vessel)


@pytest.fixture
def conn():
    c = init_db(":memory:")
    yield c
    c.close()


def sim_vessel(**overrides):
    v = {"mmsi": "123456789", "ship_name": "SIM VESSEL", "ship_type": None,
         "latitude": 26.4, "longitude": 56.4, "speed_knots": 11.5, "course_deg": 60.7,
         "timestamp_utc": "2026-09-26T23:00:00Z", "source_message_type": "VesselAPI.bounding-box"}
    v.update(overrides)
    return v


def sim_crossing(**overrides):
    c = {"mmsi": "123456789", "crossing_time": "2026-09-26T23:05:00Z", "direction": "INBOUND",
         "latitude": 26.45, "longitude": 56.45, "ship_name": "SIM VESSEL", "ship_type": None}
    c.update(overrides)
    return c


def test_init_db_creates_tables(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"vessels", "crossings", "vessel_positions"} <= names


def test_init_db_is_idempotent_on_file(tmp_path):
    path = str(tmp_path / "t.db")
    init_db(path).close()
    c = init_db(path)             # second call must not fail or wipe data
    upsert_vessel(c, sim_vessel())
    c.close()
    assert get_vessel(init_db(path), "123456789") is not None


def test_upsert_and_read_back(conn):
    upsert_vessel(conn, sim_vessel())
    v = get_vessel(conn, "123456789")
    assert v["latitude"] == 26.4 and v["last_update_utc"] == "2026-09-26T23:00:00Z"
    assert v["ship_type"] is None


def test_same_mmsi_updates_not_duplicates(conn):
    upsert_vessel(conn, sim_vessel())
    upsert_vessel(conn, sim_vessel(latitude=26.5, timestamp_utc="2026-09-26T23:10:00Z"))
    assert conn.execute("SELECT COUNT(*) FROM vessels").fetchone()[0] == 1
    assert get_vessel(conn, "123456789")["latitude"] == 26.5


def test_older_position_does_not_overwrite_newer(conn):
    upsert_vessel(conn, sim_vessel(latitude=26.5, timestamp_utc="2026-09-26T23:10:00Z"))
    upsert_vessel(conn, sim_vessel(latitude=26.1, timestamp_utc="2026-09-26T23:00:00Z"))
    v = get_vessel(conn, "123456789")
    assert v["latitude"] == 26.5 and v["last_update_utc"] == "2026-09-26T23:10:00Z"


def test_missing_name_or_type_does_not_erase_known_values(conn):
    upsert_vessel(conn, sim_vessel(ship_type=80))
    upsert_vessel(conn, sim_vessel(ship_name=None, ship_type=None,
                                   timestamp_utc="2026-09-26T23:10:00Z"))
    v = get_vessel(conn, "123456789")
    assert v["ship_name"] == "SIM VESSEL" and v["ship_type"] == 80


@pytest.mark.parametrize("bad", [
    {"mmsi": None}, {"mmsi": "abc"}, {"latitude": 95}, {"longitude": None},
    {"timestamp_utc": "2026-09-26T23:00:00"}, {"ship_type": "tanker"},
])
def test_invalid_vessel_is_rejected(conn, bad):
    with pytest.raises(ValueError):
        upsert_vessel(conn, sim_vessel(**bad))
    assert conn.execute("SELECT COUNT(*) FROM vessels").fetchone()[0] == 0


def test_insert_and_read_crossing(conn):
    assert insert_crossing(conn, sim_crossing(), source="simulated") is True
    [c] = get_recent_crossings(conn)
    assert c["mmsi"] == "123456789" and c["direction"] == "INBOUND"
    assert c["crossing_time"] == "2026-09-26T23:05:00Z" and c["source"] == "simulated"
    assert c["crossing_id"] == 1


def test_resending_same_crossing_is_not_duplicated(conn):
    assert insert_crossing(conn, sim_crossing()) is True
    assert insert_crossing(conn, sim_crossing()) is False
    # same instant written with an offset is the same event
    assert insert_crossing(conn, sim_crossing(crossing_time="2026-09-26T19:05:00-04:00")) is False
    assert len(get_recent_crossings(conn)) == 1


@pytest.mark.parametrize("bad", [
    {"mmsi": None}, {"crossing_time": None}, {"crossing_time": "2026-09-26T23:05:00"},
    {"direction": ""}, {"direction": None}, {"latitude": 200},
])
def test_invalid_crossing_is_rejected(conn, bad):
    with pytest.raises(ValueError):
        insert_crossing(conn, sim_crossing(**bad))
    assert get_recent_crossings(conn) == []


def test_crossing_without_coordinates_is_allowed(conn):
    assert insert_crossing(conn, sim_crossing(latitude=None, longitude=None)) is True


def test_recent_crossings_newest_first_and_limited(conn):
    for minute in range(5):
        insert_crossing(conn, sim_crossing(crossing_time=f"2026-09-26T23:0{minute}:00Z"))
    recent = get_recent_crossings(conn, limit=3)
    assert [c["crossing_time"][-6:-4] for c in recent] == ["04", "03", "02"]


def test_text_is_stored_literally_not_executed(conn):
    evil = "X'); DROP TABLE vessels; --"
    upsert_vessel(conn, sim_vessel(ship_name=evil))
    assert get_vessel(conn, "123456789")["ship_name"] == evil


# ---------- vessel_positions (track history) ----------

def test_track_keeps_every_position_oldest_first(conn):
    assert insert_position(conn, sim_vessel(timestamp_utc="2026-09-26T23:10:00Z", longitude=56.5),
                           source="simulated")
    assert insert_position(conn, sim_vessel(), source="simulated")          # older, sent later
    track = get_track(conn, "123456789")
    assert [p["timestamp_utc"] for p in track] == ["2026-09-26T23:00:00Z", "2026-09-26T23:10:00Z"]
    assert [p["longitude"] for p in track] == [56.4, 56.5]
    assert all(p["source"] == "simulated" and p["ship_type"] is None for p in track)


def test_same_position_twice_is_not_duplicated(conn):
    assert insert_position(conn, sim_vessel()) is True
    assert insert_position(conn, sim_vessel()) is False
    assert conn.execute("SELECT COUNT(*) FROM vessel_positions").fetchone()[0] == 1


@pytest.mark.parametrize("bad", [
    {"mmsi": None},
    {"latitude": 95},
    {"longitude": None},
    {"timestamp_utc": "2026-09-26T23:00:00"},          # no timezone
])
def test_invalid_position_is_rejected_and_not_stored(conn, bad):
    with pytest.raises(ValueError):
        insert_position(conn, sim_vessel(**bad))
    assert conn.execute("SELECT COUNT(*) FROM vessel_positions").fetchone()[0] == 0


def test_get_track_unknown_or_invalid_mmsi_is_empty(conn):
    assert get_track(conn, "999999999") == []
    assert get_track(conn, None) == []
