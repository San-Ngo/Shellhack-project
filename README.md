# Shellhack-project

HACKATHON PROJECT — HORMUZ MARITIME MARKET INTELLIGENCE

## Phần dữ liệu (Người A) — trạng thái

| Bước | Trạng thái |
|---|---|
| GIỜ 0–1: chọn nguồn AIS (aisstream.io), cấu hình, giao diện dữ liệu | Xong — xem `docs/` |
| GIỜ 1–2: nguồn AIS | aisstream.io: 0 bản tin ở Hormuz. **VesselAPI: đã nhận dữ liệu thật** (xem `docs/ais_source.md`) |
| Ghi dữ liệu: `src/vesselapi_recorder.py` | Xong; gói free 150 lượt/tháng → ghi rồi phát lại |
| GIỜ 2–3.5: `src/normalizer.py` | Xong cho aisstream + VesselAPI (bản ghi **mô phỏng**) |
| GIỜ 3.5–4.5: `src/database.py` | Xong; bảng `vessels` + `crossings`; 23 ca kiểm thử |
| GIỜ 4.5–5: `src/pipeline.py` | Xong phần của A: phát lại dữ liệu thật → chuẩn hóa → SQLite; cắm detector của B qua `--detector`. **Chờ B chốt cổng + hướng** |
| GIỜ 5–6: kiểm tra đầu cuối, bàn giao | Chưa làm |

## Cài đặt

```bash
python3 -m pip install -r requirements.txt
cp .env.example .env
```

Rồi mở `.env` và dán khóa từ https://aisstream.io/account vào dòng `AISSTREAM_API_KEY=`.

`.env` đã nằm trong `.gitignore` — không bao giờ commit khóa.

## Chạy listener

```bash
python3 -m src.ais_listener --max-messages 20 --timeout 120
python3 -m src.ais_listener --save-raw     # lưu bản tin thô vào samples/ (không commit)
```

Mã thoát: `0` nhận được bản tin · `1` lỗi cấu hình · `2` lỗi kết nối/khóa · `3` kết nối được nhưng không có bản tin.

## Ghi dữ liệu thật (VesselAPI)

```bash
python3 -m src.vesselapi_recorder --once
caffeinate -i python3 -m src.vesselapi_recorder --interval 6600 --max-calls 120
```

Mỗi lần gọi trả về vị trí của **2 giờ gần nhất** (~5 phút/vị trí/tàu), nên chụp mỗi 110 phút là đủ. Mỗi trang (≤ 50 vị trí) = 1 lượt gọi. Recorder tự dừng khi hạn mức còn ≤ 20. Dữ liệu lưu ở `samples/vesselapi_snapshots.jsonl` (không commit, theo điều khoản VesselAPI).

## Phát lại dữ liệu vào SQLite

```bash
python3 -m src.pipeline                        # chỉ lưu tàu, chưa có detector
python3 -m src.pipeline --detector MODULE:CrossingDetector --gate "LON1,LAT1,LON2,LAT2"
```

Cổng theo thứ tự **kinh độ, vĩ độ** (cách của Người B). Có thể đặt `DETECTOR` và `GATE_COORDINATES` trong `.env`.

## Chạy kiểm thử

```bash
python3 -m pytest -q
```

## Dữ liệu thật hay mô phỏng

- **Thật:** VesselAPI, 3 lần chụp → 100 vị trí của 9 tàu, 2026-09-26 21:55–23:59 UTC, trong `samples/` (không commit). Recorder đang dừng để giữ hạn mức.
- **Mô phỏng:** mọi bản ghi trong `tests/` (đúng tên trường, MMSI tự đặt). Tổng 59 ca kiểm thử. Detector trong `tests/stub_detector.py` là **stub chỉ để test đường ống**, không phải detector thật.
- aisstream.io: kết nối được nhưng 0 bản tin ở Hormuz.

## Tài liệu

- `docs/ais_source.md` — nguồn AIS, trường đã xác minh, giới hạn
- `docs/data_contract.md` — cấu trúc vị trí tàu và lượt vượt dùng chung với Người B
