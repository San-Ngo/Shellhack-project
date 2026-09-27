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

## Cần B xác nhận

1. `direction` dùng giá trị nào? Đề xuất: `"INBOUND"` / `"OUTBOUND"` (vào / ra vịnh Ba Tư).
2. `crossing_time` cũng là chuỗi ISO UTC có `Z`?
3. `latitude/longitude` của lượt vượt là điểm giao với cổng hay vị trí gần nhất?
4. Có nhiều cổng không? Nếu có, thêm trường `gate_id`.
5. Chống ghi lặp: A đề xuất khóa duy nhất `(mmsi, crossing_time, direction)`.
