"""TEST STUB ONLY — not a crossing detector.

Mimics Person B's interface (CrossingDetector(gate_coordinates=...),
process_position(position) -> event | None) and output format (+00:00 time,
EASTBOUND/WESTBOUND) so the pipeline plumbing can be tested. It does no geometry:
it simply reports an event on the 2nd position it sees for each MMSI.
"""


class StubDetector:
    def __init__(self, gate_coordinates):
        self.gate_coordinates = gate_coordinates
        self.seen = {}
        self.calls = []

    def process_position(self, position):
        self.calls.append(position["timestamp_utc"])
        count = self.seen.get(position["mmsi"], 0) + 1
        self.seen[position["mmsi"]] = count
        if count != 2:
            return None
        return {
            "mmsi": position["mmsi"],
            "crossing_time": position["timestamp_utc"].replace("Z", "+00:00"),
            "direction": "EASTBOUND",
            "latitude": position["latitude"],
            "longitude": position["longitude"],
            "ship_name": position["ship_name"],
            "ship_type": position["ship_type"],
        }


class BrokenDetector(StubDetector):
    def process_position(self, position):
        raise RuntimeError("boom")
