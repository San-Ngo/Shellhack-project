from src.crossing_detector import CrossingDetector

GATE_COORDINATES = [(56.1, 26.10), (56.1, 26.80)]


def simulated_position(mmsi, longitude, timestamp):
    return {
        "mmsi": mmsi,
        "ship_name": f"SIMULATED TEST {mmsi}",
        "ship_type": None,
        "latitude": 26.45,
        "longitude": longitude,
        "timestamp_utc": timestamp,
    }


def test_simulated_track_crossing_outbound():
    detector = CrossingDetector(GATE_COORDINATES)

    before_gate = simulated_position(
        "SIMULATED-OUTBOUND",
        56.0,
        "2026-09-26T21:55:00Z",
    )
    after_gate = simulated_position(
        "SIMULATED-OUTBOUND",
        56.2,
        "2026-09-26T22:00:00Z",
    )

    assert detector.process_position(before_gate) is None
    event = detector.process_position(after_gate)

    assert event is not None
    assert event["direction"] == "OUTBOUND"
    assert event["longitude"] == 56.1


def test_simulated_track_crossing_inbound():
    detector = CrossingDetector(GATE_COORDINATES)

    before_gate = simulated_position(
        "SIMULATED-INBOUND",
        56.2,
        "2026-09-26T21:55:00Z",
    )
    after_gate = simulated_position(
        "SIMULATED-INBOUND",
        56.0,
        "2026-09-26T22:00:00Z",
    )

    assert detector.process_position(before_gate) is None
    event = detector.process_position(after_gate)

    assert event is not None
    assert event["direction"] == "INBOUND"
    assert event["longitude"] == 56.1