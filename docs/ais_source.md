# Nguồn AIS — quyết định GIỜ 0–1

Kiểm tra ngày 2026-09-26 từ tài liệu chính thức: https://aisstream.io/documentation
và mô hình dữ liệu chính thức: https://github.com/aisstream/ais-message-models (type-definition.yaml)

## Quyết định: dùng aisstream.io

Lý do: tài liệu công khai, có lọc theo tọa độ, có cả bản tin vị trí và bản tin tàu tĩnh, dùng WebSocket từ Python được, có ví dụ Python chính thức. Không cần quét trang web.

## Đã xác minh từ tài liệu

| Mục | Kết quả |
|---|---|
| Cách kết nối | WebSocket `wss://stream.aisstream.io/v0/stream`, bật nén `permessage-deflate` |
| Khóa API | Bắt buộc. Tạo tại aisstream.io/account; khóa mới chỉ hiện 1 lần |
| Lọc Hormuz | Có — `BoundingBoxes` (bắt buộc), mỗi góc là `[lat, lon]` |
| Lọc khác | `FiltersShipMMSI` (tối đa 200, chuỗi 9 ký tự), `FilterMessageTypes` |
| Bản tin vị trí | `PositionReport` (Class A); cũng có `StandardClassBPositionReport`, `ExtendedClassBPositionReport` |
| Bản tin tàu tĩnh | `ShipStaticData` (có `Name`, `Type`, `ImoNumber`, `CallSign`, `Destination`…) |
| Khung bản tin | Frame nhị phân chứa JSON UTF-8 → phải decode trước khi parse |

## Trường dữ liệu đã xác minh

Vỏ bản tin: `MessageType`, `MetaData`, `Message.<MessageType>`

`Message.PositionReport`: `UserID` (MMSI, số nguyên), `Latitude`, `Longitude`, `Sog` (hải lý/giờ), `Cog` (độ), `TrueHeading`, `NavigationalStatus`, `Valid`, `Timestamp` (**chỉ là giây UTC 0–59**, không phải thời gian đầy đủ)

`Message.ShipStaticData`: `UserID`, `Name`, `Type` (mã AIS số nguyên), `ImoNumber`, `CallSign`, `Destination`, `Dimension`, `Eta`, `MaximumStaticDraught`

`MetaData` (ví dụ trong tài liệu): `MMSI`, `ShipName`, `Latitude`, `Longitude`

## CHƯA xác minh — kiểm tra bằng bản tin thật ở GIỜ 1–2

- **Trường thời gian đầy đủ trong `MetaData`**: schema chính thức không liệt kê trường của `MetaData`, ví dụ tài liệu cũng không có trường thời gian. Chưa biết tên chính xác → không đoán. Phải in một bản tin thật ra để xem.
- Vùng Hormuz có bản tin thực tế hay không (vùng phủ trạm thu mặt đất có thể thưa).
- Tần suất cập nhật thực tế: AIS phát theo sự kiện, không cố định.

## Nhật ký thử kết nối (máy MacBook của San)

| Thời gian (UTC) | Khung lọc [lat, lon] | Chờ | Kết quả |
|---|---|---|---|
| 2026-09-26 23:04:50 → 23:06:50 | [27.5, 55.0] – [25.0, 58.0] (Hormuz) | 120 s | Khóa hợp lệ, đăng ký được xác nhận, nén bật. **0 bản tin.** |
| 2026-09-26 23:08:24 → 23:13:24 | [30.5, 47.5] – [22.0, 60.5] (Vịnh Ba Tư + Vịnh Oman) | 300 s | Khóa hợp lệ, đăng ký được xác nhận. **0 bản tin.** |

Kết luận tạm thời: **chưa nhận được dữ liệu AIS thật cho khu vực này.** Chưa kiểm tra listener ở vùng đông tàu, nên chưa loại trừ hoàn toàn khả năng lỗi phía code; tuy vậy kết nối, khóa và đăng ký đều đã được máy chủ xác nhận.

Hệ quả: các bước sau dùng **bản tin mô phỏng** (đánh dấu `SIMULATED`) chỉ để kiểm tra mã. `timestamp_utc` tạm lấy **thời điểm listener nhận bản tin** (có múi giờ UTC), không đoán tên trường thời gian trong `MetaData`.

## Nguồn ứng viên: VesselAPI — ĐÃ NHẬN DỮ LIỆU THẬT

Lý do thử: một project Hormuz khác cũng nhận 0 bản tin ở Vịnh từ aisstream.io (feed của họ chủ yếu là trạm mặt đất châu Âu).

- Tài liệu: https://vesselapi.com/docs — REST `GET https://api.vesselapi.com/v1/location/vessels/bounding-box`, header `Authorization: Bearer <key>`
- Tham số: `filter.latBottom`, `filter.latTop`, `filter.lonLeft`, `filter.lonRight`, `pagination.limit` (≤ 50), `pagination.nextToken`
- **Giới hạn khung (từ lỗi 400 thật): `|dLat| + |dLon|` ≤ 4 độ.** Khung thử: lat 25.5–27.0, lon 55.5–57.5 (= 3.5)

**Lần gọi thật: 2026-09-26 ~23:40 UTC, máy của San, gói free**
- HTTP 200, **50 bản ghi vị trí** trong trang đầu, còn trang sau (`nextToken`) — là vị trí, không phải số tàu (xem bên dưới)
- Header `X-RateLimit-Remaining: 150` sau lần gọi — chưa rõ là hạn mức tháng hay theo cửa sổ 5 phút → **phải kiểm tra trên trang Subscription**
- Phản hồi đầy đủ lưu tại `samples/vesselapi_probe.json` (không commit)

**Trường đã xác minh trong mỗi bản ghi `vessels[]`:**
`mmsi` (số nguyên), `imo`, `vessel_name`, `latitude`, `longitude`, `location` (GeoJSON Point), `timestamp` (ISO UTC có `Z` — thời điểm AIS), `processed_timestamp`, `sog`, `cog`, `heading`, `nav_status`, `suspected_glitch`

**Khác biệt so với aisstream.io:**
- REST gọi theo chu kỳ, không phải WebSocket → tốn hạn mức theo số lần gọi và số trang
- **Không có trường loại tàu** trong endpoint này → `ship_type` sẽ là `null` (không đoán)
- **Có `timestamp` từ nguồn** → thay được thời điểm nhận tạm thời
- Có `suspected_glitch` → có thể dùng để bỏ vị trí nghi lỗi

**Endpoint trả về LỊCH SỬ VỊ TRÍ, không phải 1 vị trí/tàu:** không truyền `time.from/time.to` thì mặc định là **2 giờ gần nhất**. Phân tích lần chụp 23:53 UTC (khung lat 26.0–26.9, lon 56.0–56.9): **97 bản ghi = 8 tàu**, mỗi tàu 2–23 vị trí, cách nhau ~5 phút; vị trí cũ nhất 118 phút trước lúc gọi. Mỗi trang (≤ 50 bản ghi) = 1 lượt gọi.

**Ghi dữ liệu:** bắt đầu 2026-09-26 23:53 UTC. Ban đầu chụp mỗi 10 phút (trùng ~90%) → đổi sang **mỗi 110 phút** (cửa sổ 2 giờ, chồng 10 phút để không hở) ≈ 1 lượt/giờ. Bản ghi trùng (mmsi, timestamp) được lọc khi phát lại. Header `X-RateLimit-Remaining` giảm 2 mỗi lần chụp (148 → 146) → đây là **hạn mức tháng** (trang Subscription: 150 lượt/tháng).

## Giới hạn và rủi ro

- Tối đa 3 kết nối đã đăng ký / tài khoản; 3 kết nối mở / IP.
- Phải gửi bản đăng ký trong **3 giây** sau khi mở kết nối, nếu không bị đóng.
- Cập nhật đăng ký tối đa 1 lần/giây; cập nhật thay thế (không gộp) cấu hình cũ.
- Phải đọc liên tục; đọc chậm thì máy chủ bỏ bản tin.
- Từ tháng 9/2026: kết nối không nén bị giới hạn băng thông → bật nén.
- **Không có SLA**, không phát lại sự kiện đã mất → cần tự kết nối lại (backoff + jitter).
- Không được kết nối trực tiếp từ trình duyệt; khóa chỉ ở phía máy chủ.
- Điều khoản sử dụng chi tiết: chưa đọc — xem trước khi demo công khai.
