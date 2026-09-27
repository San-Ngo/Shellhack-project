# Giao diện dữ liệu chung — Người A ↔ Người B

## 1. Vị trí tàu (A gửi cho B)

```json
{
  "mmsi": "123456789",
  "ship_name": null,
  "ship_type": null,
  "latitude": 26.5,
  "longitude": 56.3,
  "speed_knots": null,
  "course_deg": null,
  "timestamp_utc": "2026-09-26T22:00:00Z",
  "source_message_type": "PositionReport"
}
```

- Có đủ 6 trường B cần (`mmsi, latitude, longitude, timestamp_utc, ship_name, ship_type`). 3 trường thêm (`speed_knots, course_deg, source_message_type`) B có thể bỏ qua.
- `mmsi`: chuỗi 9 ký tự.
- `latitude` ∈ [-90, 90], `longitude` ∈ [-180, 180] — A đã lọc, bản tin sai không đến B.
- `timestamp_utc`: chuỗi ISO 8601, luôn có `Z`. Với VesselAPI (nguồn chính hiện tại) đây là **thời điểm AIS thật từ nguồn** (trường `timestamp`).
- Dữ liệu VesselAPI là ảnh chụp mỗi ~10 phút → hai vị trí liên tiếp của một tàu có thể cách nhau vài km. Detector nên kiểm tra **đoạn thẳng nối 2 vị trí** có cắt cổng hay không.
- `ship_type`: **mã AIS số nguyên** (vd 80–89 = tanker) hoặc `null` khi không rõ. **Endpoint VesselAPI đang dùng không có loại tàu → hiện luôn `null`.** Không bao giờ đoán. `null` ≠ "không phải tanker".
- `ship_name`: chuỗi đã cắt khoảng trắng, hoặc `null`.

## 2. Lượt vượt (B trả cho A)

```json
{
  "mmsi": "123456789",
  "crossing_time": "2026-09-26T22:05:00Z",
  "direction": "?",
  "latitude": 26.55,
  "longitude": 56.35,
  "ship_name": null,
  "ship_type": null
}
```

A lưu bằng `src/database.py` (đã có, 23 ca kiểm thử):

```python
from src.database import init_db, insert_crossing, get_recent_crossings
conn = init_db()                               # DB_PATH trong .env, hoặc init_db(":memory:") khi test
stored = insert_crossing(conn, crossing, source="vesselapi-replay")  # True = đã lưu, False = trùng
```

- Bắt buộc: `mmsi`, `crossing_time` (ISO có múi giờ), `direction` (chuỗi khác rỗng). Thiếu/sai → `ValueError`, không ghi gì.
- Không bắt buộc: `latitude`, `longitude`, `ship_name`, `ship_type`.
- Gửi lại cùng `(mmsi, crossing_time, direction)` → không ghi lặp, trả về `False`.
- `source`: `"vesselapi-live"`, `"vesselapi-replay"` hoặc `"simulated"` — để luôn biết sự kiện đến từ dữ liệu nào.

## Đã thống nhất với B (2026-09-26 tối)

- **Cách gọi:** `detector = CrossingDetector(gate_coordinates=[(lon1, lat1), (lon2, lat2)])`, rồi `detector.process_position(position)` cho từng vị trí → sự kiện hoặc `None`. Detector tự nhớ vị trí trước theo MMSI. **Gửi theo thứ tự thời gian** — `src/pipeline.py` đã bỏ trùng và sắp xếp trước khi gửi.
- **Thứ tự tọa độ cổng: (kinh độ, vĩ độ).**
- **`crossing_time`:** hiện là thời gian của vị trí thứ hai, dạng `+00:00`. `insert_crossing` tự đổi sang `Z` — không cần sửa.
- **`latitude/longitude` của sự kiện:** điểm giao với đường cổng.

## Còn mở (B phụ trách)

1. **`direction`:** code hiện trả `EASTBOUND`/`WESTBOUND` (theo kinh độ tăng/giảm), chưa tương đương `INBOUND`/`OUTBOUND`. Database lưu được cả hai, nhưng **chưa dựa vào nhãn hướng** cho đến khi B chốt phía nào là Vịnh Ba Tư.
2. **Tọa độ cổng Hormuz thật:** chưa có (test của B dùng đường giả lập). Pipeline từ chối chạy detector nếu thiếu cổng.
   - Lưu ý từ dữ liệu thật (21:55–23:59 UTC): vị trí mới nhất của cả 9 tàu nằm ở **kinh độ 56.00–56.21**, vĩ độ 26.00–26.81 — sát mép tây khung ghi. Cổng nên nằm trong vùng thực sự có dữ liệu, hoặc cần ghi thêm/đổi khung trước khi chốt.
