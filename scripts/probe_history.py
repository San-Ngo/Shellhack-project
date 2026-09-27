"""One-shot check: can VesselAPI return PAST positions (e.g. 7 days ago) for Hormuz?

Makes exactly ONE request for a past time window, then prints how many positions
came back and the real time range of those positions, so we can see whether the
API honoured the window. Saves the raw response to samples/ (git-ignored).

Run from the repo root:
    python3 scripts/probe_history.py
    python3 scripts/probe_history.py --days-ago 3 --hours 2
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

URL = "https://api.vesselapi.com/v1/location/vessels/bounding-box"
MAX_WINDOW_HOURS = 4  # VesselAPI docs: time.to - time.from must not exceed 4 hours


def iso(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    p = argparse.ArgumentParser(description="One call: test VesselAPI historical lookback.")
    p.add_argument("--days-ago", type=float, default=7, help="how far back the window starts")
    p.add_argument("--hours", type=float, default=4, help=f"window length (<= {MAX_WINDOW_HOURS})")
    args = p.parse_args()
    if not 0 < args.hours <= MAX_WINDOW_HOURS:
        print(f"LỖI: --hours phải trong (0, {MAX_WINDOW_HOURS}]")
        return 1

    key = (os.getenv("VESSELAPI_KEY") or "").strip()
    if key in ("", "your_key_here"):
        print("LỖI CẤU HÌNH: thiếu VESSELAPI_KEY trong .env")
        return 1

    lat_b, lat_t, lon_l, lon_r = (os.getenv("VESSELAPI_BBOX") or "26.0,26.9,56.0,56.9").split(",")
    start = (datetime.now(timezone.utc) - timedelta(days=args.days_ago)).replace(minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=args.hours)
    params = {"filter.latBottom": lat_b, "filter.latTop": lat_t,
              "filter.lonLeft": lon_l, "filter.lonRight": lon_r,
              "time.from": iso(start), "time.to": iso(end), "pagination.limit": "50"}

    print(f"Gọi 1 lần | khung lat {lat_b}–{lat_t}, lon {lon_l}–{lon_r}")
    print(f"Cửa sổ yêu cầu: {iso(start)} → {iso(end)} ({args.days_ago:g} ngày trước)")
    req = urllib.request.Request(f"{URL}?{urllib.parse.urlencode(params)}",
                                 headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            remaining = resp.headers.get("X-RateLimit-Remaining")
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"LỖI HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:400]}")
        return 2
    except urllib.error.URLError as e:
        print(f"LỖI MẠNG: {e.reason}")
        return 2

    (ROOT / "samples").mkdir(exist_ok=True)
    out = ROOT / "samples" / "vesselapi_history_probe.json"
    out.write_text(json.dumps({"requested_from": iso(start), "requested_to": iso(end),
                               "real_data": True, "response": data}, indent=2), "utf-8")

    vessels = data.get("vessels") if isinstance(data, dict) else None
    if vessels is None:
        print(f"Không có khóa 'vessels'. Phản hồi: {str(data)[:300]}")
        return 2
    print(f"HTTP 200 | hạn mức còn: {remaining}")
    print(f"Số vị trí: {len(vessels)}{' (còn trang sau)' if data.get('nextToken') else ''} | "
          f"số tàu: {len({v.get('mmsi') for v in vessels})}")
    times = sorted(v["timestamp"] for v in vessels if v.get("timestamp"))
    if times:
        inside = start.isoformat() <= times[0].replace("Z", "+00:00") and \
                 times[-1].replace("Z", "+00:00") <= end.isoformat()
        print(f"Thời gian thật của dữ liệu: {times[0]} → {times[-1]}")
        print("→ ĐÚNG cửa sổ quá khứ: có dữ liệu lịch sử." if inside
              else "→ KHÔNG khớp cửa sổ yêu cầu: API có thể không hỗ trợ lùi xa như vậy.")
    else:
        print("→ Không có vị trí nào trong cửa sổ này (có thể API không lưu xa như vậy, hoặc vùng không có tàu).")
    print(f"Đã lưu vào {out.relative_to(ROOT)}")
    return 0 if vessels else 3


if __name__ == "__main__":
    sys.exit(main())
