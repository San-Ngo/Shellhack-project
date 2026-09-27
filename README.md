# Shellhack-project — HORMUZ WATCH

HACKATHON PROJECT — HORMUZ MARITIME MARKET INTELLIGENCE

Phần dữ liệu (Người A): nguồn AIS → chuẩn hóa → detector vượt cổng (Người B) → SQLite.

## Trạng thái nền tảng 6 giờ

| Bước | Trạng thái |
|---|---|
| GIỜ 0–1: chọn nguồn, cấu hình, giao diện dữ liệu | ✅ `docs/ais_source.md`, `docs/data_contract.md` |
| GIỜ 1–2: nhận AIS | ✅ aisstream.io: 0 bản tin ở Hormuz → **VesselAPI: dữ liệu thật** |
| GIỜ 2–3.5: chuẩn hóa (`src/normalizer.py`) | ✅ |
| GIỜ 3.5–4.5: SQLite (`src/database.py`) | ✅ bảng `vessels`, `crossings` |
| GIỜ 4.5–5: ghép với Người B (`src/pipeline.py`) | ✅ chạy với `src/crossing_detector.py` của B |
| GIỜ 5–6: kiểm tra đầu cuối, bàn giao | ✅ 65 ca kiểm thử; **1 lượt vượt thật**; bàn giao: `docs/handover.md` |

## Cài đặt

```bash
python3 -m pip install -r requirements.txt
```

Trên Mac dùng `python3`, không dùng `python`.

## Tạo `.env`

```bash
cp .env.example .env
```
```bash
open -e .env
```

Dán khóa vào `VESSELAPI_KEY=` (tạo tại https://dashboard.vesselapi.com/). `.env` nằm trong `.gitignore`. **Không dán khóa vào chat, code hay GitHub.**

## Ghi dữ liệu thật (VesselAPI)

Chụp thử 1 lần:
```bash
python3 -m src.vesselapi_recorder --once
```

Ghi liên tục, mỗi 30 phút, tối đa 36 lượt:
```bash
caffeinate -i python3 -m src.vesselapi_recorder --interval 1800 --max-calls 36
```

- Mỗi lần gọi trả vị trí của **2 giờ gần nhất**, mới nhất trước. Mỗi trang ≤ 50 vị trí = 1 lượt gọi.
- Recorder tự dừng khi hết `--max-calls` hoặc khi hạn mức tháng còn ≤ 20.
- Dữ liệu: `samples/vesselapi_snapshots.jsonl`. Nhật ký và lỗi: `samples/recorder.log`. Cả hai **không commit** (điều khoản VesselAPI).

Thử lấy dữ liệu quá khứ (1 lượt, cửa sổ 4 giờ):
```bash
python3 scripts/probe_history.py --days-ago 7
```

## Phát lại vào SQLite

Dữ liệu **thật**, chỉ lưu tàu:
```bash
python3 -m src.pipeline
```

Dữ liệu **thật** + detector của Người B:
```bash
python3 -m src.pipeline --detector src.crossing_detector:CrossingDetector --gate "56.1,26.10,56.1,26.80"
```

Demo detector bằng dữ liệu **mô phỏng** (tự ghi `source = simulated`):
```bash
python3 -m src.pipeline --snapshots demo/simulated_tracks.jsonl --detector src.crossing_detector:CrossingDetector --gate "56.1,26.10,56.1,26.80"
```

- Cổng theo thứ tự **kinh độ, vĩ độ**. Cổng trên là **cổng tạm** do Người B đề xuất.
- Kết quả lưu ở `hormuz_watch.db` (không commit).

## Kiểm thử

```bash
python3 -m pytest -q
```

Kết quả mong đợi: `65 passed`.

## Dữ liệu thật hay mô phỏng

- **Thật (VesselAPI):** 26/9 21:55 → 27/9 04:09 UTC → 226 vị trí của 14 tàu; 7 ngày trước (20/9 03:05–05:04 UTC) → 44 vị trí của 9 tàu. Chỉ nằm trong `samples/` trên máy ghi.
- **Mô phỏng:** `demo/simulated_tracks.jsonl` (MMSI `99900…`, tên `SIM-…`, `real_data: false`) và mọi bản ghi trong `tests/`. `tests/stub_detector.py` chỉ là stub để test đường ống.
- **Lượt vượt thật đầu tiên:** tàu AL- NOOR (MMSI 616002462) cắt cổng tạm lúc 27/9 03:33 UTC, hướng `OUTBOUND` (11 vị trí liên tục, ~7,3 hải lý/giờ, không có cờ nghi lỗi). Các lượt vượt trong `demo/` là mô phỏng.

## Giới hạn đã biết

- **Vùng phủ:** VesselAPI (gói free, trạm mặt đất) chỉ có tàu ở ven bờ tây Musandam (lat 26.0–26.8, phần lớn lon 56.00–56.21). Luồng tàu chính giữa eo **không có dữ liệu**, cả hôm nay lẫn 7 ngày trước. AIS vệ tinh (`filter.sat=true`) tốn phí, chưa dùng.
- **Hạn mức:** 150 lượt/tháng. Khung lọc tổng `|dLat| + |dLon|` ≤ 4°. Mỗi truy vấn thời gian ≤ 4 giờ.
- **Không có loại tàu:** endpoint đang dùng không trả loại tàu → `ship_type` luôn `NULL`. Không đoán; `NULL` ≠ "không phải tanker".
- **Chất lượng dữ liệu:** có tàu báo tốc độ 12 hải lý/giờ nhưng vị trí đứng yên (NAUTILUS I, 25 vị trí trùng). Bản ghi `suspected_glitch` bị bỏ.
- **aisstream.io:** kết nối và khóa hợp lệ nhưng 0 bản tin ở Hormuz (2 lần thử, 26/9). `src/ais_listener.py` giữ lại nhưng không dùng.
- **Detector (Người B):** hướng `INBOUND` (kinh độ giảm) / `OUTBOUND` (kinh độ tăng). Còn lỗi: một vị trí nằm **đúng trên đường cổng** làm lượt vượt bị bỏ sót. Chi tiết: `docs/handover.md`.
- **Khóa API:** chưa bao giờ vào Git (đã quét toàn bộ lịch sử), nhưng từng bị dán vào chat → nên tạo khóa mới.

## Tài liệu

- `docs/ais_source.md` — nguồn AIS, trường đã xác minh, nhật ký thử, vùng phủ
- `docs/data_contract.md` — đối tượng vị trí tàu và lượt vượt dùng chung với Người B
- `docs/handover.md` — bàn giao cho Người B: hàm, cấu trúc, cách chạy, lỗi còn tồn tại
