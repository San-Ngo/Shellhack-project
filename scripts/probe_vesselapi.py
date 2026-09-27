"""One-shot check: does VesselAPI return any vessels inside the Hormuz box?

Makes exactly ONE request, prints what really comes back, and saves the raw
response to samples/vesselapi_probe.json (git-ignored). Changes nothing else.

Run from the repo root:
    python3 scripts/probe_vesselapi.py
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

URL = "https://api.vesselapi.com/v1/location/vessels/bounding-box"
# VesselAPI rule (from its 400 error): |dLat| + |dLon| must not exceed 4 degrees.
# Box around the Strait of Hormuz narrows: 1.5 + 2.0 = 3.5 degrees.
PARAMS = {
    "filter.latBottom": "25.5",
    "filter.latTop": "27.0",
    "filter.lonLeft": "55.5",
    "filter.lonRight": "57.5",
    "pagination.limit": "50",
}


def main() -> int:
    key = (os.getenv("VESSELAPI_KEY") or "").strip()
    if key in ("", "your_key_here"):
        print("LỖI CẤU HÌNH: thiếu VESSELAPI_KEY trong .env")
        return 1

    url = f"{URL}?{urllib.parse.urlencode(PARAMS)}"
    print(f"Gọi 1 lần: {URL}")
    print(f"Khung: lat {PARAMS['filter.latBottom']}–{PARAMS['filter.latTop']}, "
          f"lon {PARAMS['filter.lonLeft']}–{PARAMS['filter.lonRight']}")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}",
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = resp.status
            remaining = resp.headers.get("X-RateLimit-Remaining")
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        hint = {401: "khóa sai hoặc thiếu", 403: "không có quyền với endpoint này",
                429: "vượt giới hạn gọi"}.get(e.code, "lỗi từ máy chủ")
        print(f"LỖI HTTP {e.code} ({hint}): {detail}")
        return 2
    except urllib.error.URLError as e:
        print(f"LỖI MẠNG: {e.reason}")
        return 2

    print(f"HTTP {status} | lượt gọi còn lại: {remaining}")
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        print(f"Phản hồi không phải JSON: {body[:300]!r}")
        return 2

    (ROOT / "samples").mkdir(exist_ok=True)
    (ROOT / "samples" / "vesselapi_probe.json").write_text(json.dumps(data, indent=2), "utf-8")

    vessels = data.get("vessels") if isinstance(data, dict) else None
    if vessels is None:
        print(f"Không thấy khóa 'vessels'. Các khóa trả về: "
              f"{sorted(data) if isinstance(data, dict) else type(data).__name__}")
        return 2

    print(f"SỐ TÀU TRONG KHUNG HORMUZ: {len(vessels)}"
          f"{' (còn trang sau)' if data.get('nextToken') else ''}")
    if vessels:
        print(f"Tên trường của bản ghi đầu: {sorted(vessels[0])}")
        print("Bản ghi đầu tiên:")
        print(json.dumps(vessels[0], indent=2, ensure_ascii=False)[:1500])
    print("Đã lưu phản hồi đầy đủ vào samples/vesselapi_probe.json")
    return 0 if vessels else 3


if __name__ == "__main__":
    sys.exit(main())
