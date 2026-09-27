# Bàn giao phần dữ liệu (Người A → Người B)

Trạng thái: 2026-09-27, `main` sau PR #2. Kiểm thử: `python3 -m pytest -q` → 65 passed.

## 1. Hàm cần gọi

```python
from src.database import init_db, upsert_vessel, insert_crossing, get_recent_crossings, get_vessel
from src.normalizer import normalize_vesselapi
from src.pipeline import load_positions, run
```

| Hàm | Dùng để |
|---|---|
| `init_db(path=None)` | Mở/tạo SQLite (`DB_PATH` trong `.env`, hoặc `":memory:"` khi test) |
| `upsert_vessel(conn, position)` | Lưu vị trí mới nhất của tàu. Cùng MMSI → cập nhật; vị trí cũ hơn không đè vị trí mới |
| `insert_crossing(conn, crossing, source=...)` | Lưu lượt vượt. `True` = đã lưu, `False` = trùng. Dữ liệu sai → `ValueError`, không ghi gì |
| `get_recent_crossings(conn, limit=20)` | Lượt vượt mới nhất trước |
| `load_positions(path)` | Đọc file snapshot → vị trí chuẩn, đã bỏ trùng và **sắp theo thời gian** |
| `run(positions, conn, detector, source)` | Lưu tàu + gọi `detector.process_position()` + lưu lượt vượt |

## 2. Cấu trúc đối tượng

Vị trí (A → B):
```json
{"mmsi": "375606000", "ship_name": "STANFORD EAGLE", "ship_type": null,
 "latitude": 25.94801, "longitude": 56.06015, "speed_knots": 0.0, "course_deg": 60.7,
 "timestamp_utc": "2026-09-26T23:39:33Z", "source_message_type": "VesselAPI.bounding-box"}
```

Lượt vượt (B → A) — khớp với output hiện tại của `CrossingDetector`:
```json
{"mmsi": "999000001", "crossing_time": "2026-09-26T22:20:00+00:00", "direction": "INBOUND",
 "latitude": 26.4, "longitude": 56.1, "ship_name": "SIM-WESTBOUND-1", "ship_type": null}
```

- Bắt buộc: `mmsi`, `crossing_time` (có múi giờ; `+00:00` được đổi sang `Z`), `direction` (khác rỗng).
- `ship_type`: **số nguyên AIS hoặc `null`**. Chuỗi như `"Cargo"` bị từ chối.
- Chống trùng: khóa `(mmsi, crossing_time, direction)`.
- `crossings.source`: `vesselapi-replay` (dữ liệu thật) hoặc `simulated` — pipeline tự gán theo file đầu vào.

## 3. Cách chạy quy trình

Demo detector với dữ liệu mô phỏng:
```bash
python3 -m src.pipeline --snapshots demo/simulated_tracks.jsonl --detector src.crossing_detector:CrossingDetector --gate "56.1,26.10,56.1,26.80"
```
Kết quả đã kiểm tra: 21 vị trí của 3 tàu → **2 lượt vượt** (1 mỗi chiều), tàu neo không bị tính, chạy lại → 0 lượt mới (2 trùng).

Dữ liệu thật:
```bash
python3 -m src.pipeline --detector src.crossing_detector:CrossingDetector --gate "56.1,26.10,56.1,26.80"
```
Kết quả đã kiểm tra (27/9 00:21, sau PR #2): 483 bản ghi → 226 vị trí của 14 tàu (5 bị bỏ vì `suspected_glitch`) → **1 lượt vượt thật**: AL- NOOR (MMSI 616002462), 03:33:26 UTC, `OUTBOUND`, giao cổng tại lat 26.193. Track 11 vị trí liên tục, ~7,3 hải lý/giờ, cắt lon 56.1 giữa 03:19 (56.084) và 03:33 (56.108).

## 4. Lỗi và việc còn tồn tại

**Phía detector (Người B phụ trách — A không tự sửa):**
1. **Bỏ sót lượt vượt khi một vị trí nằm đúng trên đường cổng.** `LineString.crosses()` trả `False` khi đoạn đường chỉ *chạm* cổng tại một đầu mút. Ví dụ đã kiểm tra: kinh độ 56.12 → **56.10** → 56.08 qua cổng lon 56.1 → 0 sự kiện. Gợi ý: kiểm tra phía của hai đầu mút so với cổng, hoặc dùng `intersects()` và xử lý riêng trường hợp chạm.
2. ✅ Đã sửa ở PR #2: hướng là `INBOUND` (kinh độ giảm) / `OUTBOUND` (kinh độ tăng).
3. ✅ Đã sửa ở PR #2: test nằm trong `tests/test_crossing_detector.py`, `ship_type=None`. Lưu ý nhỏ: test dùng MMSI dạng chữ (`"SIMULATED-OUTBOUND"`) — chạy được với detector, nhưng `insert_crossing` sẽ từ chối nếu đưa thẳng vào database.
4. **Cổng tạm** lon 56.1, lat 26.10–26.80: hai đầu cũ (26.0–26.09 và 26.81–26.9) nằm trên đất liền theo bản đồ đất/biển ~1 km, nên đã rút lại. Chưa có tọa độ cổng Hormuz chính thức.

**Phía dữ liệu (A):**
5. Dữ liệu thật chỉ phủ ven bờ tây Musandam; luồng tàu chính giữa eo không có. Đến 27/9 04:09 UTC mới có **1 lượt vượt thật** — demo nhiều lượt vẫn cần dữ liệu mô phỏng có nhãn.
6. `ship_type` luôn `NULL` với nguồn hiện tại.
7. Gói free: 150 lượt/tháng; recorder tự dừng khi còn ≤ 20.
8. Khóa API từng bị dán vào chat → nên tạo khóa mới trước demo.
