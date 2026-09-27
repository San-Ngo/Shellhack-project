from datetime import datetime
from shapely.geometry import LineString


class CrossingDetector:
    def __init__(self, gate_coordinates, max_gap_minutes=30):
        """
        gate_coordinates là hai điểm theo thứ tự:
        (kinh độ, vĩ độ).

        Tọa độ hiện dùng để mô phỏng, chưa phải cổng Hormuz thật.
        """
        self.gate = LineString(gate_coordinates)
        self.max_gap_minutes = max_gap_minutes
        self.previous_positions = {}

    def process_position(self, vessel):
        mmsi = str(vessel["mmsi"])
        latitude = float(vessel["latitude"])
        longitude = float(vessel["longitude"])
        timestamp = self._parse_timestamp(vessel["timestamp_utc"])

        if not -90 <= latitude <= 90:
            return None

        if not -180 <= longitude <= 180:
            return None

        current = {
            "longitude": longitude,
            "latitude": latitude,
            "timestamp": timestamp,
            "ship_name": vessel.get("ship_name"),
            "ship_type": vessel.get("ship_type"),
        }

        previous = self.previous_positions.get(mmsi)

        # Lần đầu thấy tàu: lưu vị trí làm mốc.
        if previous is None:
            self.previous_positions[mmsi] = current
            return None

        # Bỏ qua bản tin cũ hoặc trùng thời gian.
        if timestamp <= previous["timestamp"]:
            return None

        elapsed_minutes = (
            timestamp - previous["timestamp"]
        ).total_seconds() / 60

        # Nếu khoảng trống dữ liệu quá lâu, đặt lại mốc vị trí.
        if elapsed_minutes > self.max_gap_minutes:
            self.previous_positions[mmsi] = current
            return None

        path = LineString([
            (previous["longitude"], previous["latitude"]),
            (current["longitude"], current["latitude"]),
        ])

        self.previous_positions[mmsi] = current

        # Kiểm tra đoạn di chuyển có cắt qua cổng không.
        if not path.crosses(self.gate):
            return None

        intersection = path.intersection(self.gate)

        if current["longitude"] > previous["longitude"]:
            direction = "EASTBOUND"
        elif current["longitude"] < previous["longitude"]:
            direction = "WESTBOUND"
        else:
            direction = "UNKNOWN"

        return {
            "mmsi": mmsi,
            "ship_name": current["ship_name"],
            "ship_type": current["ship_type"],
            "crossing_time": timestamp.isoformat(),
            "direction": direction,
            "longitude": intersection.x,
            "latitude": intersection.y,
        }

    @staticmethod
    def _parse_timestamp(value):
        if isinstance(value, datetime):
            timestamp = value
        else:
            timestamp = datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )

        if timestamp.tzinfo is None:
            raise ValueError(
                "timestamp_utc must include a timezone"
            )

        return timestamp