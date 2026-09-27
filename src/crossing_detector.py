from datetime import datetime

from shapely.geometry import LineString, Point


class CrossingDetector:
    # Sai số nhỏ để tránh lỗi số thực khi điểm đáng ra nằm trên cổng.
    GATE_TOLERANCE = 1e-9

    def __init__(self, gate_coordinates, max_gap_minutes=30):
        """
        gate_coordinates gồm hai điểm theo thứ tự (kinh độ, vĩ độ).

        Tọa độ hiện dùng để mô phỏng, chưa phải cổng Hormuz thật.
        """
        self.gate = LineString(gate_coordinates)
        self.max_gap_minutes = max_gap_minutes

        # Vị trí mới nhất đã nhận của mỗi tàu.
        self.previous_positions = {}

        # Vị trí mới nhất của mỗi tàu nằm ngoài cổng.
        # Giữ mốc này khi tàu có một hoặc nhiều vị trí đúng trên cổng.
        self.previous_off_gate_positions = {}

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
        current_point = Point(longitude, latitude)
        current_is_on_gate = self._is_on_gate(current_point)

        previous = self.previous_positions.get(mmsi)

        # Lần đầu thấy tàu: lưu vị trí làm mốc.
        if previous is None:
            self.previous_positions[mmsi] = current

            if current_is_on_gate:
                self.previous_off_gate_positions.pop(mmsi, None)
            else:
                self.previous_off_gate_positions[mmsi] = current

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

            if current_is_on_gate:
                self.previous_off_gate_positions.pop(mmsi, None)
            else:
                self.previous_off_gate_positions[mmsi] = current

            return None

        # Cập nhật mốc thời gian gần nhất để kiểm tra thứ tự bản tin
        # và khoảng trống dữ liệu ở lần tiếp theo.
        self.previous_positions[mmsi] = current

        if current_is_on_gate:
            off_gate_position = self.previous_off_gate_positions.get(mmsi)

            # Không nối một track bị gián đoạn quá lâu.
            if off_gate_position is not None:
                gap_from_off_gate = (
                    timestamp - off_gate_position["timestamp"]
                ).total_seconds() / 60

                if gap_from_off_gate > self.max_gap_minutes:
                    self.previous_off_gate_positions.pop(mmsi, None)

            # Giữ nguyên mốc ngoài cổng để kiểm tra khi tàu rời cổng.
            return None

        previous_off_gate = self.previous_off_gate_positions.get(mmsi)

        if previous_off_gate is None:
            self.previous_off_gate_positions[mmsi] = current
            return None

        gap_from_off_gate = (
            timestamp - previous_off_gate["timestamp"]
        ).total_seconds() / 60

        if gap_from_off_gate > self.max_gap_minutes:
            self.previous_off_gate_positions[mmsi] = current
            return None

        path = LineString([
            (
                previous_off_gate["longitude"],
                previous_off_gate["latitude"],
            ),
            (current["longitude"], current["latitude"]),
        ])

        # Hai đầu đoạn đều nằm ngoài cổng. intersects() bắt được
        # đoạn nối đi qua cổng, kể cả khi có một vị trí trước đó
        # nằm chính xác trên cổng.
        if not path.intersects(self.gate):
            self.previous_off_gate_positions[mmsi] = current
            return None

        intersection = path.intersection(self.gate)

        # Với hai đoạn thẳng cắt nhau tại một điểm, giao điểm phải là Point.
        if intersection.geom_type != "Point":
            self.previous_off_gate_positions[mmsi] = current
            return None

        if current["longitude"] < previous_off_gate["longitude"]:
            direction = "INBOUND"
        elif current["longitude"] > previous_off_gate["longitude"]:
            direction = "OUTBOUND"
        else:
            direction = "UNKNOWN"

        # Sau khi xử lý, dùng vị trí mới làm mốc cho lượt tiếp theo.
        self.previous_off_gate_positions[mmsi] = current

        return {
            "mmsi": mmsi,
            "ship_name": current["ship_name"],
            "ship_type": current["ship_type"],
            "crossing_time": timestamp.isoformat(),
            "direction": direction,
            "longitude": intersection.x,
            "latitude": intersection.y,
        }

    def _is_on_gate(self, point):
        return point.distance(self.gate) <= self.GATE_TOLERANCE

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