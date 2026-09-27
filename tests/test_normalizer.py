"""Tests for src/normalizer.py.

ALL messages below are SIMULATED. They follow the field names in the official
aisstream.io docs/schema but are NOT real AIS data. MMSIs are made up.
"""
from datetime import datetime, timezone, timedelta

from src.normalizer import StaticCache, normalize

RECEIVED = datetime(2026, 9, 26, 22, 0, 0, tzinfo=timezone.utc)
MMSI = 123456789  # SIMULATED


def sim_position(**overrides):
    body = {"MessageID": 1, "UserID": MMSI, "Valid": True,
            "Latitude": 26.5, "Longitude": 56.3, "Sog": 12.4, "Cog": 86.7}
    body.update(overrides)
    return {"MessageType": "PositionReport",
            "MetaData": {"MMSI": MMSI, "ShipName": "SIM VESSEL   "},
            "Message": {"PositionReport": body}}


def sim_static(type_code=80, name="SIM TANKER@@@"):
    return {"MessageType": "ShipStaticData",
            "MetaData": {"MMSI": MMSI},
            "Message": {"ShipStaticData": {"UserID": MMSI, "Valid": True,
                                           "Name": name, "Type": type_code}}}


def test_valid_position_matches_shared_contract():
    r = normalize(sim_position(), RECEIVED, StaticCache())
    assert r.kind == "position"
    assert r.data == {
        "mmsi": "123456789", "ship_name": "SIM VESSEL", "ship_type": None,
        "latitude": 26.5, "longitude": 56.3, "speed_knots": 12.4, "course_deg": 86.7,
        "timestamp_utc": "2026-09-26T22:00:00Z", "source_message_type": "PositionReport",
    }


def test_missing_ship_name_is_null_not_error():
    msg = sim_position()
    msg["MetaData"].pop("ShipName")
    r = normalize(msg, RECEIVED, StaticCache())
    assert r.kind == "position"
    assert r.data["ship_name"] is None
    assert r.data["ship_type"] is None  # unknown type stays None, never guessed


def test_out_of_range_coordinates_are_skipped_with_reason():
    r = normalize(sim_position(Latitude=95.0), RECEIVED, StaticCache())
    assert r.kind == "skip" and "out of range" in r.reason
    r = normalize(sim_position(Longitude=-200.0), RECEIVED, StaticCache())
    assert r.kind == "skip" and "out of range" in r.reason


def test_ais_not_available_position_is_skipped():
    r = normalize(sim_position(Latitude=91.0, Longitude=181.0), RECEIVED, StaticCache())
    assert r.kind == "skip" and "not available" in r.reason


def test_missing_mmsi_is_skipped():
    msg = sim_position()
    msg["Message"]["PositionReport"].pop("UserID")
    msg["MetaData"].pop("MMSI")
    r = normalize(msg, RECEIVED, StaticCache())
    assert r.kind == "skip" and "MMSI" in r.reason


def test_static_message_is_not_a_position_but_is_cached_and_merged():
    cache = StaticCache()
    r = normalize(sim_static(type_code=80), RECEIVED, cache)
    assert r.kind == "static"
    assert r.data == {"mmsi": "123456789", "ship_name": "SIM TANKER", "ship_type": 80}

    pos = normalize(sim_position(), RECEIVED, cache)
    assert pos.data["ship_type"] == 80
    assert pos.data["ship_name"] == "SIM TANKER"


def test_static_type_zero_means_unknown():
    cache = StaticCache()
    normalize(sim_static(type_code=0), RECEIVED, cache)
    assert normalize(sim_position(), RECEIVED, cache).data["ship_type"] is None


def test_naive_or_missing_timestamp_is_skipped():
    naive = datetime(2026, 9, 26, 22, 0, 0)
    assert normalize(sim_position(), naive, StaticCache()).kind == "skip"
    assert normalize(sim_position(), None, StaticCache()).kind == "skip"


def test_non_utc_timestamp_is_converted_to_utc():
    miami = datetime(2026, 9, 26, 18, 0, 0, tzinfo=timezone(timedelta(hours=-4)))
    r = normalize(sim_position(), miami, StaticCache())
    assert r.data["timestamp_utc"] == "2026-09-26T22:00:00Z"


def test_unavailable_speed_and_course_become_null():
    r = normalize(sim_position(Sog=102.3, Cog=360.0), RECEIVED, StaticCache())
    assert r.data["speed_knots"] is None and r.data["course_deg"] is None


def test_other_message_types_and_garbage_do_not_crash():
    cache = StaticCache()
    assert normalize({"MessageType": "BaseStationReport", "Message": {}}, RECEIVED, cache).kind == "skip"
    assert normalize("not a dict", RECEIVED, cache).kind == "skip"
    assert normalize({"MessageType": "PositionReport"}, RECEIVED, cache).kind == "skip"
    assert normalize(sim_position(Valid=False), RECEIVED, cache).kind == "skip"
