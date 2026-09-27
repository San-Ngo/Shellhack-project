# Bàn giao phần dữ liệu (Người A → Người B)

Trạng thái: 2026-09-27, `main` @ `9cef077` (sau PR #5). Kiểm thử: `python3 -m pytest -q` → 70 passed.

## 1. Hàm cần gọi

```python
from src.database import (init_db, upsert_vessel, insert_position, insert_crossing,
                          get_recent_crossings, get_vessel, get_track)
from src.normalizer import normalize_vesselapi
from src.pipeline import load_positions, run
```

| Hàm | Dùng để |
|---|---|
| `init_db(path=None)` | Mở/tạo SQLite (`DB_PATH` trong `.env`, hoặc `":memory:"` khi test) |
| `upsert_vessel(conn, position)` | Lưu vị trí mới nhất của tàu. Cùng MMSI → cập nhật; vị trí cũ hơn không đè vị trí mới |
| `insert_position(conn, position, source=...)` | Lưu 1 vị trí vào lịch sử đường đi (`vessel_positions`). `True` = đã lưu, `False` = trùng `(mmsi, timestamp_utc)` |
| `get_track(conn, mmsi)` | Mọi vị trí của 1 tàu, cũ nhất trước — dùng để vẽ đường đi |
| `insert_crossing(conn, crossing, source=...)` | Lưu lượt vượt. `True` = đã lưu, `False` = trùng. Dữ liệu sai → `ValueError`, không ghi gì |
| `get_recent_crossings(conn, limit=20)` | Lượt vượt mới nhất trước |
| `load_positions(path)` | Đọc file snapshot → vị trí chuẩn, đã bỏ trùng và **sắp theo thời gian** |
| `run(positions, conn, detector, source)` | Lưu tàu + lưu đường đi + gọi `detector.process_position()` + lưu lượt vượt |

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

### Bảng trong SQLite

| Bảng | Mỗi dòng là | Khóa chống trùng |
|---|---|---|
| `vessels` | vị trí **mới nhất** của 1 tàu | `mmsi` |
| `vessel_positions` | **1 vị trí** trong đường đi của tàu (lịch sử) | `(mmsi, timestamp_utc)` |
| `crossings` | 1 lượt vượt cổng | `(mmsi, crossing_time, direction)` |

`vessel_positions.source` và `crossings.source`: `vesselapi-replay` (thật) hoặc `simulated`.

Ví dụ đọc đường đi:
```sql
SELECT mmsi, ship_name, timestamp_utc, latitude, longitude, speed_knots, course_deg, source
FROM vessel_positions ORDER BY mmsi, timestamp_utc;
```

### Chuyển database cho Người B

A tạo database riêng cho B (dữ liệu thật + lượt vượt):
```bash
python3 -m src.pipeline --db samples/hormuz_watch_for_B.db --detector src.crossing_detector:CrossingDetector --gate "56.1,26.10,56.1,26.80"
```
Gửi file `samples/hormuz_watch_for_B.db` trực tiếp cho B (tin nhắn/Drive riêng). **Không commit, không đăng công khai** — dữ liệu VesselAPI không được phát tán lại. B đặt file vào thư mục repo rồi mở bằng `init_db("hormuz_watch_for_B.db")` hoặc `sqlite3`.

## 3. Cách chạy quy trình

Demo detector với dữ liệu mô phỏng:
```bash
python3 -m src.pipeline --snapshots demo/simulated_tracks.jsonl --detector src.crossing_detector:CrossingDetector --gate "56.1,26.10,56.1,26.80"
```

Dữ liệu thật:
```bash
python3 -m src.pipeline --detector src.crossing_detector:CrossingDetector --gate "56.1,26.10,56.1,26.80"
```

### Biên bản kiểm tra đầu cuối — 2026-09-27 05:19 UTC, `main` @ `9cef077`

Mỗi bộ dữ liệu chạy **2 lần trên cùng một database mới**; số dòng được đếm trực tiếp trong SQLite sau mỗi lần.

| | Mô phỏng (`demo/simulated_tracks.jsonl`) | Thật (VesselAPI, phát lại) |
|---|---|---|
| Đầu vào | 21 vị trí của 3 tàu | 683 bản ghi → 266 vị trí của 14 tàu (bỏ 405 trùng, 12 `suspected_glitch`), 26/9 21:55 → 27/9 05:10 UTC |
| Lần 1: detector phát hiện / lưu mới | 2 / 2 | 1 / 1 |
| Lần 2: detector phát hiện / lưu mới / trùng | 2 / 0 / 2 | 1 / 0 / 1 |
| Bảng `vessels` sau lần 1 → lần 2 | 3 → 3 dòng (3 MMSI) | 14 → 14 dòng (14 MMSI) |
| Bảng `crossings` sau lần 1 → lần 2 | 2 → 2 dòng | 1 → 1 dòng |
| Lượt vượt | 999000001 `INBOUND` 22:20Z lat 26.4; 999000002 `OUTBOUND` 22:20Z lat 26.6; tàu neo 999000003 không tính | AL- NOOR (616002462) `OUTBOUND` 03:33:26Z, giao cổng lat 26.1929 |
| `source` / `ship_type` | `simulated` / `NULL` | `vesselapi-replay` / `NULL` |

Kết luận:
- **Database lưu đúng:** bảng `vessels` giữ đúng vị trí mới nhất của cả 14/14 tàu thật (đối chiếu với vị trí có thời gian lớn nhất của từng MMSI); `crossing_time` lưu dạng `Z`; loại tàu không rõ giữ `NULL`.
- **Chống trùng đúng:** chạy lại không thêm dòng nào ở cả hai bảng; detector vẫn phát hiện nhưng `insert_crossing` trả `False` (trùng khóa `mmsi, crossing_time, direction`).
- **Mô phỏng:** đúng 1 lượt mỗi hướng.
- **Dữ liệu thật không có lượt giả:** kiểm tra hình học độc lập với detector (hai vị trí liên tiếp ngoài cổng, cách nhau ≤ 30 phút, đổi phía lon 56.1 trong đoạn lat 26.10–26.80) cho đúng 1 trường hợp — AL- NOOR, trùng với kết quả detector; không có vị trí nào nằm đúng trên cổng.
- AL- NOOR: track 11 vị trí liên tục, ~7,3 hải lý/giờ, cắt lon 56.1 giữa 03:19 (56.084) và 03:33 (56.108).
- Kiểm thử: `python3 -m pytest -q` → 70 passed.

Lưu ý: nếu database đã có dữ liệu từ lần chạy trước, "lưu mới" có thể là 0 dù detector phát hiện — đó là chống trùng, không phải lỗi.

## 4. Lỗi và việc còn tồn tại

**Phía detector (Người B phụ trách — A không tự sửa):**
1. ✅ Đã sửa ở PR #3: vị trí nằm đúng trên cổng không còn làm bỏ sót lượt vượt; chạm cổng rồi quay lại cùng phía không tạo sự kiện. Có test cho cả hai chiều và trường hợp chạm-rồi-quay-lại.
   - ✅ PR #4–#5: thêm test cho nhiều vị trí liên tiếp trên cổng (→ 1 lượt) và khoảng trống 45 phút > `max_gap_minutes` (→ 0 lượt); MMSI dạng số. Các test này FAIL trên detector trước PR #3, nên bắt được lỗi gốc.
2. ✅ Đã sửa ở PR #2: hướng là `INBOUND` (kinh độ giảm) / `OUTBOUND` (kinh độ tăng).
3. ✅ Đã sửa ở PR #2–#3: test nằm trong `tests/test_crossing_detector.py`, `ship_type=None`, MMSI dạng số (`999000001`…).
4. **Cổng tạm** lon 56.1, lat 26.10–26.80: hai đầu cũ (26.0–26.09 và 26.81–26.9) nằm trên đất liền theo bản đồ đất/biển ~1 km, nên đã rút lại. Chưa có tọa độ cổng Hormuz chính thức.

**Phía dữ liệu (A):**
5. Dữ liệu thật chỉ phủ ven bờ tây Musandam; luồng tàu chính giữa eo không có. Đến 27/9 05:10 UTC mới có **1 lượt vượt thật** — demo nhiều lượt vẫn cần dữ liệu mô phỏng có nhãn.
6. `ship_type` luôn `NULL` với nguồn hiện tại.
7. Gói free: 150 lượt/tháng; recorder tự dừng khi còn ≤ 20.
8. Khóa API từng bị dán vào chat → nên tạo khóa mới trước demo.
