"""Record REAL VesselAPI snapshots of the Hormuz box to samples/ for later replay.

Free plan = 150 calls/month, so every page request is counted and the recorder
stops before the quota runs out. Raw data stays in samples/ (git-ignored):
VesselAPI's terms say data may not be shared without consent.

Run from the repo root:
    python3 -m src.vesselapi_recorder --once                 # 1 snapshot, then stop
    python3 -m src.vesselapi_recorder --interval 6600 --max-calls 120

Each call returns ALL positions of the last 2 hours (API default window), ~5 min apart
per vessel. So one snapshot every 110 min covers time without gaps (10 min overlap);
duplicates (mmsi, timestamp) are removed at replay time.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from src.config import ROOT

URL = "https://api.vesselapi.com/v1/location/vessels/bounding-box"
OUT_FILE = ROOT / "samples" / "vesselapi_snapshots.jsonl"
MAX_SPAN_DEG = 4.0          # VesselAPI rule: |dLat| + |dLon| <= 4 (seen in a real 400 error)
PAGE_LIMIT = 50             # VesselAPI max page size
# Temporary box around the strait narrows. Person B owns the real gate; override with
# VESSELAPI_BBOX=latBottom,latTop,lonLeft,lonRight in .env
DEFAULT_BBOX = "26.0,26.9,56.0,56.9"


class StopRecording(Exception):
    """Unrecoverable problem (bad key, no permission, quota) — stop instead of burning calls."""


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(msg: str) -> None:
    print(f"[{now_utc()}] {msg}", flush=True)


def load_key() -> str:
    key = (os.getenv("VESSELAPI_KEY") or "").strip()
    if key in ("", "your_key_here"):
        raise StopRecording("thiếu VESSELAPI_KEY trong .env")
    return key


def load_bbox() -> dict:
    raw = os.getenv("VESSELAPI_BBOX", DEFAULT_BBOX)
    try:
        lat_b, lat_t, lon_l, lon_r = (float(x) for x in raw.split(","))
    except ValueError:
        raise StopRecording(f"VESSELAPI_BBOX={raw!r} phải có dạng latBottom,latTop,lonLeft,lonRight")
    if not (-90 <= lat_b < lat_t <= 90 and -180 <= lon_l < lon_r <= 180):
        raise StopRecording(f"VESSELAPI_BBOX={raw!r} không hợp lệ")
    span = (lat_t - lat_b) + (lon_r - lon_l)
    if span > MAX_SPAN_DEG:
        raise StopRecording(f"khung rộng {span:.2f}° > {MAX_SPAN_DEG}° (giới hạn VesselAPI)")
    return {"filter.latBottom": lat_b, "filter.latTop": lat_t,
            "filter.lonLeft": lon_l, "filter.lonRight": lon_r}


def fetch_page(key: str, bbox: dict, next_token):
    """One HTTP request = one counted API call. Returns (json, remaining_header)."""
    params = dict(bbox, **{"pagination.limit": PAGE_LIMIT})
    if next_token:
        params["pagination.nextToken"] = next_token
    req = urllib.request.Request(f"{URL}?{urllib.parse.urlencode(params)}",
                                 headers={"Authorization": f"Bearer {key}",
                                          "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8")), resp.headers.get("X-RateLimit-Remaining")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        if e.code in (400, 401, 403, 429):
            raise StopRecording(f"HTTP {e.code}: {detail}")
        raise RuntimeError(f"HTTP {e.code}: {detail}")


def snapshot(key: str, bbox: dict, max_pages: int, calls_left: int):
    """Fetch up to max_pages pages. Returns (record, calls_used, remaining_header)."""
    vessels, token, pages, remaining = [], None, 0, None
    while pages < min(max_pages, calls_left):
        data, remaining = fetch_page(key, bbox, token)
        pages += 1
        if not isinstance(data, dict) or not isinstance(data.get("vessels"), list):
            raise StopRecording(f"phản hồi không có danh sách 'vessels': {str(data)[:200]}")
        vessels.extend(data["vessels"])
        token = data.get("nextToken")
        if not token:
            break
    record = {"source": "vesselapi", "real_data": True, "fetched_at": now_utc(),
              "bbox": bbox, "pages": pages, "truncated": bool(token), "vessels": vessels}
    return record, pages, remaining


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

    p = argparse.ArgumentParser(description="Record real VesselAPI snapshots (quota-guarded).")
    p.add_argument("--interval", type=int, default=6600, help="giây giữa 2 lần chụp (mặc định 6600 = 110 phút)")
    p.add_argument("--max-calls", type=int, default=120, help="tối đa số lượt gọi cho lần chạy này")
    p.add_argument("--max-pages", type=int, default=2, help="tối đa số trang mỗi lần chụp")
    p.add_argument("--reserve", type=int, default=20, help="dừng khi hạn mức còn lại <= số này")
    p.add_argument("--once", action="store_true", help="chụp 1 lần rồi dừng")
    args = p.parse_args()

    try:
        key, bbox = load_key(), load_bbox()
    except StopRecording as e:
        log(f"LỖI CẤU HÌNH: {e}")
        return 1

    OUT_FILE.parent.mkdir(exist_ok=True)
    log(f"Ghi vào {OUT_FILE.relative_to(ROOT)} | khung={bbox} | mỗi {args.interval}s | "
        f"tối đa {args.max_calls} lượt, {args.max_pages} trang/lần")
    calls = 0
    try:
        while calls < args.max_calls:
            try:
                record, used, remaining = snapshot(key, bbox, args.max_pages, args.max_calls - calls)
            except RuntimeError as e:  # server/network hiccup: count 1 call to be safe, retry later
                calls += 1
                log(f"Lỗi tạm thời ({e}); thử lại sau {args.interval}s")
            except urllib.error.URLError as e:
                log(f"LỖI MẠNG: {e.reason}; thử lại sau {args.interval}s")
            else:
                calls += used
                with open(OUT_FILE, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                log(f"Chụp: {len(record['vessels'])} vị trí của {len({v.get('mmsi') for v in record['vessels']})} tàu, {used} trang"
                    f"{' (CÒN TRANG CHƯA LẤY — nên thu nhỏ khung)' if record['truncated'] else ''}"
                    f" | đã dùng {calls}/{args.max_calls} | hạn mức còn: {remaining}")
                if remaining is not None and remaining.isdigit() and int(remaining) <= args.reserve:
                    log(f"Dừng: hạn mức còn {remaining} ≤ dự phòng {args.reserve}.")
                    return 0
            if args.once:
                return 0
            if calls < args.max_calls:
                time.sleep(args.interval)
    except StopRecording as e:
        log(f"DỪNG: {e}")
        return 2
    except KeyboardInterrupt:
        log("Dừng theo yêu cầu (Ctrl+C).")
        return 0
    log(f"Đạt giới hạn {args.max_calls} lượt cho lần chạy này.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
