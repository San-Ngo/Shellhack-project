from src.crossing_detector import CrossingDetector


GATE_COORDINATES = [(56.1, 26.10), (56.1, 26.80)]


def simulated_position(mmsi, longitude, timestamp):
    return {
        "mmsi": str(mmsi),
        "ship_name": f"SIMULATED TEST {mmsi}",
        "ship_type": None,
        "latitude": 26.45,
        "longitude": longitude,
        "timestamp_utc": timestamp,
    }


def test_simulated_track_crossing_outbound():
    detector = CrossingDetector(GATE_COORDINATES)

    before_gate = simulated_position(
        "999000001",
        56.0,
        "2026-09-26T21:55:00Z",
    )
    after_gate = simulated_position(
        "999000001",
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
        "999000002",
        56.2,
        "2026-09-26T21:55:00Z",
    )
    after_gate = simulated_position(
        "999000002",
        56.0,
        "2026-09-26T22:00:00Z",
    )

    assert detector.process_position(before_gate) is None
    event = detector.process_position(after_gate)

    assert event is not None
    assert event["direction"] == "INBOUND"
    assert event["longitude"] == 56.1


def test_crossing_inbound_with_position_exactly_on_gate():
    detector = CrossingDetector(GATE_COORDINATES)

    before_gate = simulated_position(
        "999000003",
        56.12,
        "2026-09-26T21:55:00Z",
    )
    on_gate = simulated_position(
        "999000003",
        56.10,
        "2026-09-26T22:00:00Z",
    )
    after_gate = simulated_position(
        "999000003",
        56.08,
        "2026-09-26T22:05:00Z",
    )

    assert detector.process_position(before_gate) is None
    assert detector.process_position(on_gate) is None

    event = detector.process_position(after_gate)

    assert event is not None
    assert event["direction"] == "INBOUND"
    assert event["longitude"] == 56.1
    assert event["latitude"] == 26.45


def test_crossing_outbound_with_position_exactly_on_gate():
    detector = CrossingDetector(GATE_COORDINATES)

    before_gate = simulated_position(
        "999000004",
        56.08,
        "2026-09-26T21:55:00Z",
    )
    on_gate = simulated_position(
        "999000004",
        56.10,
        "2026-09-26T22:00:00Z",
    )
    after_gate = simulated_position(
        "999000004",
        56.12,
        "2026-09-26T22:05:00Z",
    )

    assert detector.process_position(before_gate) is None
    assert detector.process_position(on_gate) is None

    event = detector.process_position(after_gate)

    assert event is not None
    assert event["direction"] == "OUTBOUND"
    assert event["longitude"] == 56.1
    assert event["latitude"] == 26.45


def test_touching_gate_then_returning_to_same_side_is_not_crossing():
    detector = CrossingDetector(GATE_COORDINATES)

    before_gate = simulated_position(
        "999000005",
        56.12,
        "2026-09-26T21:55:00Z",
    )
    on_gate = simulated_position(
        "999000005",
        56.10,
        "2026-09-26T22:00:00Z",
    )
    back_to_same_side = simulated_position(
        "999000005",
        56.12,
        "2026-09-26T22:05:00Z",
    )

    assert detector.process_position(before_gate) is None
    assert detector.process_position(on_gate) is None
    assert detector.process_position(back_to_same_side) is None


def test_multiple_positions_on_gate_produce_one_crossing():
    detector = CrossingDetector(GATE_COORDINATES)

    positions = [
        simulated_position("999000006", 56.0, "2026-09-26T21:55:00Z"),
        simulated_position("999000006", 56.1, "2026-09-26T22:00:00Z"),
        simulated_position("999000006", 56.1, "2026-09-26T22:05:00Z"),
        simulated_position("999000006", 56.1, "2026-09-26T22:10:00Z"),
        simulated_position("999000006", 56.2, "2026-09-26T22:15:00Z"),
    ]

    events = [detector.process_position(p) for p in positions]
    crossings = [event for event in events if event is not None]

    assert len(crossings) == 1
    assert crossings[0]["direction"] == "OUTBOUND"


def test_gap_over_maximum_time_does_not_count_as_crossing():
    detector = CrossingDetector(GATE_COORDINATES)

    before_gap = simulated_position(
        "999000007", 56.0, "2026-09-26T21:55:00Z"
    )
    after_gap = simulated_position(
        "999000007", 56.2, "2026-09-26T22:40:00Z"
    )
    next_position = simulated_position(
        "999000007", 56.3, "2026-09-26T22:45:00Z"
    )

    assert detector.process_position(before_gap) is None
    assert detector.process_position(after_gap) is None
    assert detector.process_position(next_position) is None
