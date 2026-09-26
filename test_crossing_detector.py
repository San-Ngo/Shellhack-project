from src.crossing_detector import CrossingDetector

detector = CrossingDetector(
    gate_coordinates=[(0, -1), (0, 1)]
)

vessel_before = {
    "mmsi": "123456789",
    "ship_name": "Test Vessel",
    "ship_type": "Cargo",
    "latitude": 0,
    "longitude": -0.5,
    "timestamp_utc": "2026-09-26T12:00:00Z",
}

vessel_after = {
    "mmsi": "123456789",
    "ship_name": "Test Vessel",
    "ship_type": "Cargo",
    "latitude": 0,
    "longitude": 0.5,
    "timestamp_utc": "2026-09-26T12:05:00Z",
}

print("Vị trí đầu:", detector.process_position(vessel_before))
event = detector.process_position(vessel_after)
print("Sự kiện qua cổng:", event)

if event is None:
    raise SystemExit("Chưa phát hiện tàu đi qua cổng.")

if event["direction"] != "EASTBOUND":
    raise SystemExit("Hướng tàu không đúng.")

print("Kiểm tra thành công: phát hiện tàu đi về phía đông.")