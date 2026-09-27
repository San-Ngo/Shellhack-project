# Azure SQL + Power BI — dữ liệu cập nhật liên tục

Luồng: **VesselAPI → chuẩn hóa → detector của Người B → Azure SQL Database → Power BI (Azure Maps)**.

Trạng thái (27/9/2026): code và test logic xong (106 test, dùng store giả). **Chưa chạy với Azure SQL thật** — bước kiểm tra đầu tiên là `python3 -m src.azure_store --check` trên máy có database.

## 1. Bảng trong Azure SQL (`sql/azure_schema.sql`)

| Bảng / view | Mỗi dòng là | Ghi thế nào |
|---|---|---|
| `vessels_latest` | vị trí **mới nhất** của 1 tàu | `MERGE` theo `mmsi`; vị trí cũ hơn không đè vị trí mới |
| `vessel_metadata` | tên, IMO, loại tàu, lần đầu/cuối thấy | `MERGE` theo `mmsi`; thiếu tên/IMO không xóa giá trị đã có |
| `crossings` | 1 lượt vượt cổng | chỉ thêm; `UNIQUE (mmsi, crossing_time_utc, direction)` → gửi lại không tạo dòng mới |
| `brent_daily` | giá Brent 1 ngày (USD/thùng) | `MERGE` theo ngày; ngày không có giá thì bỏ trống |
| `ingestion_status` | trạng thái 1 nguồn (`vesselapi`, `fred-brent`) | lần thử, lần thành công cuối, lỗi, hạn mức còn lại |
| `v_map_vessels` | view cho bản đồ | thêm `minutes_since_position`, `is_stale` (> 60 phút), `data_kind` REAL/SIMULATED |
| `v_map_crossings` | view lượt vượt | thêm `data_kind` |

- **Tọa độ cho Azure Maps:** cột `latitude`, `longitude` — độ thập phân WGS84, kiểu `DECIMAL(9,6)`, lấy nguyên từ AIS (không làm tròn thêm, không nội suy).
- `crossings.latitude/longitude` là **điểm giao với cổng** do detector tính, không phải vị trí AIS.
- `ship_type` luôn `NULL` với VesselAPI (endpoint không có loại tàu). `NULL` = chưa biết, **không** phải "không phải tanker".

## 2. Quy tắc của job định kỳ (`src/live_ingest.py`)

- Mỗi lần: lấy vị trí **từ vị trí mới nhất đã lưu − 15 phút** đến hiện tại (tối đa 4 giờ, giới hạn VesselAPI) → ít bản ghi, ít lượt gọi.
- Ghi 1 lần/transaction: lỗi giữa chừng → không ghi gì, detector quay về trạng thái trước, lần sau xử lý lại.
- **API lỗi / hết hạn mức:** không ghi vị trí nào, dữ liệu cũ giữ nguyên, `ingestion_status` ghi lỗi:

| `status` | Khi nào | Job làm gì |
|---|---|---|
| `OK` | lấy và ghi thành công | tiếp tục |
| `ERROR` | lỗi mạng, HTTP 5xx, ghi database lỗi | thử lại ở lần sau |
| `AUTH_ERROR` | HTTP 401/403 (sai khóa, hết quyền) | dừng |
| `QUOTA_EXHAUSTED` | HTTP 429 hoặc hạn mức còn ≤ `--reserve` (20) | dừng gọi API |

  `last_success_utc` và `last_data_time_utc` **giữ nguyên** khi lỗi → Power BI biết dữ liệu cũ đến mức nào.
- **Khoảng trống thời gian lớn:** detector của B chỉ nối 2 vị trí cách nhau ≤ 30 phút (`max_gap_minutes`) → `confidence = CONFIRMED`. Một bản detector thứ hai (cùng code của B, giới hạn 360 phút) bắt các lần đổi phía qua khoảng trống dài hơn → `confidence = UNCERTAIN`, **không bao giờ** là CONFIRMED. Khoảng trống > 360 phút → không ghi gì. `gap_minutes` = thời gian từ vị trí trước đó của tàu.
- Khởi động lại: detector được nạp lại từ `vessels_latest`, nên lượt vượt giữa 2 lần chạy vẫn bắt được (nếu khoảng trống ≤ 30 phút).
- Bản sao thô của mỗi lần lấy vẫn được lưu vào `samples/vesselapi_snapshots.jsonl` (không commit).
- Giá Brent: tự cập nhật mỗi 6 giờ trong vòng lặp; lỗi Brent không ảnh hưởng AIS.

## 3. Cài đặt (máy của San)

1. Tạo Azure SQL Database (Azure Portal) với **SQL authentication** (tên đăng nhập + mật khẩu).
2. Mở tường lửa: SQL server → *Networking* → *Add your client IPv4 address*. Cho Power BI Service: bật *Allow Azure services and resources to access this server*.
3. Cài thư viện:
   ```bash
   python3 -m pip install -r requirements.txt
   ```
4. Thêm 4 dòng vào `.env` (xem `.env.example`): `AZURE_SQL_SERVER`, `AZURE_SQL_DATABASE`, `AZURE_SQL_USER`, `AZURE_SQL_PASSWORD`. **Không dán mật khẩu vào chat, code hay GitHub.**
5. Kiểm tra kết nối + tạo bảng:
   ```bash
   python3 -m src.azure_store --check
   ```
6. (Tùy chọn) Nạp dữ liệu thật đã ghi, không tốn lượt gọi:
   ```bash
   python3 -m src.live_ingest --replay samples/vesselapi_snapshots.jsonl
   ```
7. Nạp giá Brent:
   ```bash
   python3 -m src.brent_loader --days 400
   ```
8. Dừng recorder cũ (Ctrl+C) — job mới thay thế nó và cũng dùng hạn mức VesselAPI. Chạy thử 1 lần:
   ```bash
   python3 -m src.live_ingest --once
   ```
9. Chạy liên tục mỗi 30 phút:
   ```bash
   caffeinate -i python3 -m src.live_ingest --interval 1800
   ```

## 4. Power BI

1. *Get data* → *Azure SQL database* → server, database → chọn **DirectQuery** (đọc thẳng database, không cần import lại).
2. Chọn `v_map_vessels`, `v_map_crossings`, `brent_daily`, `ingestion_status`.
3. Cột `latitude` → *Column tools* → *Data category* = **Latitude**; `longitude` → **Longitude**.
4. Visual **Azure Maps**: *Latitude* = `latitude`, *Longitude* = `longitude`; *Tooltips*: `ship_name`, `mmsi`, `position_time_utc`, `minutes_since_position`, `data_kind`.
5. Lớp lượt vượt: thêm `v_map_crossings`, tô màu theo `confidence`.
6. Thẻ trạng thái: `ingestion_status.status`, `last_success_utc`, `last_data_time_utc`.
7. Tự làm mới: *Format page* → *Page refresh*. Trên Power BI Service, tần suất tối thiểu tùy loại capacity — kiểm tra khi publish.

## 5. Lỗi thường gặp

- `không kết nối được Azure SQL`: kiểm tra tường lửa (bước 2), tên server có `.database.windows.net`. Database serverless đang tạm dừng cần ~1 phút để bật — code tự thử lại 3 lần.
- Đăng nhập bị từ chối dù đúng mật khẩu: thử `AZURE_SQL_USER=<login>@<tên-server-ngắn>`.
- `AUTH_ERROR` từ VesselAPI: khóa sai/hết hạn → tạo khóa mới, sửa `.env`.

## 6. Giới hạn đã biết

- Chưa kiểm tra với Azure SQL thật (chỉ kiểm tra cú pháp T-SQL bằng trình phân tích và test logic với store giả).
- VesselAPI chỉ phủ ven bờ tây Musandam; luồng tàu chính không có dữ liệu. Cổng vẫn là **cổng tạm** của Người B.
- "Live" = mới nhất theo chu kỳ 30 phút cộng độ trễ của nguồn, không phải thời gian thực từng giây.
- Brent (FRED/EIA) là giá **hằng ngày**, công bố trễ vài ngày làm việc.
