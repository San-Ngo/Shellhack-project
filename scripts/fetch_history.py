"""Sample PAST VesselAPI positions: one 4-hour window per day (quota-friendly).

Why a sample: the free plan has 150 calls/month and one request may cover at most 4 hours,
so 15 full days would need 90+ calls. Default = 1 window/day x 1 page (<= 50 positions,
newest first) = 1 call per day. Windows already covered by recorded data are skipped, and
answers go to samples/vesselapi_history.jsonl (git-ignored), so nothing is fetched twice.

Newest day first; stops after 2 windows in a row that fail or come back empty (probably the
limit of how far back the plan keeps data), after 401/403/429, or when quota <= --reserve.

    python3 scripts/fetch_history.py --dry-run           # plan and cost only, no API call
    python3 scripts/fetch_history.py                     # 15 days, 03:00-07:00 UTC each day
    python3 scripts/fetch_history.py --start-hours 3,15  # 2 windows per day (double the calls)
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.history import HISTORY, covered_minutes, read_sources  # noqa: E402
from src.live_ingest import AuthError, FetchError, QuotaExhausted, fetch_window  # noqa: E402
from src.vesselapi_recorder import StopRecording, load_bbox, load_key  # noqa: E402


def plan_windows(now: datetime, days: int, start_hours: list, hours: float, intervals: list) -> tuple:
    """-> (to_fetch, skipped) windows newest first; skipped = already >= 90% covered."""
    to_fetch, skipped = [], []
    length = timedelta(hours=hours)
    for back in range(0, days + 1):
        day = (now - timedelta(days=back)).date()
        for h in sorted(start_hours, reverse=True):
            start = datetime(day.year, day.month, day.day, h, tzinfo=timezone.utc)
            end = start + length
            if end > now:
                continue
            if covered_minutes(intervals, start, end) >= 0.9 * length.total_seconds() / 60:
                skipped.append((start, end))
            else:
                to_fetch.append((start, end))
    return to_fetch, skipped


def fmt(window) -> str:
    s, e = window
    return f"{s:%m/%d/%Y} {s:%H:%M}–{e:%H:%M} UTC"


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    p = argparse.ArgumentParser(description="Lấy mẫu vị trí tàu trong quá khứ (VesselAPI).")
    p.add_argument("--days", type=int, default=15, help="số ngày lùi lại (tính cả hôm nay)")
    p.add_argument("--start-hours", default="3", help="giờ bắt đầu cửa sổ (UTC), vd 3 hoặc 3,15")
    p.add_argument("--hours", type=float, default=4, help="độ dài cửa sổ (tối đa 4)")
    p.add_argument("--pages", type=int, default=1, help="số trang (lượt gọi) mỗi cửa sổ")
    p.add_argument("--max-calls", type=int, default=20)
    p.add_argument("--reserve", type=int, default=20, help="dừng khi hạn mức còn <= số này")
    p.add_argument("--dry-run", action="store_true", help="chỉ in kế hoạch, không gọi API")
    args = p.parse_args()
    if not 0 < args.hours <= 4:
        print("LỖI: --hours phải trong (0, 4] (giới hạn VesselAPI).")
        return 1

    now = datetime.now(timezone.utc)
    _, intervals, _ = read_sources()
    hours = [int(h) for h in args.start_hours.split(",") if h.strip()]
    to_fetch, skipped = plan_windows(now, args.days, hours, args.hours, intervals)
    print(f"Kế hoạch: {len(to_fetch)} cửa sổ cần lấy × tối đa {args.pages} trang "
          f"= tối đa {len(to_fetch) * args.pages} lượt gọi (giới hạn lần chạy: {args.max_calls})")
    for w in skipped:
        print(f"  bỏ qua (đã có dữ liệu): {fmt(w)}")
    if args.dry_run:
        for w in to_fetch:
            print(f"  sẽ lấy: {fmt(w)}")
        return 0

    try:
        key, bbox = load_key(), load_bbox()
    except StopRecording as e:
        print(f"LỖI CẤU HÌNH: {e}")
        return 1
    HISTORY.parent.mkdir(exist_ok=True)
    calls, misses = 0, 0
    for window in to_fetch:
        if calls + args.pages > args.max_calls:
            print(f"Dừng: đạt giới hạn {args.max_calls} lượt cho lần chạy này.")
            break
        start, end = window
        line = {"source": "vesselapi", "real_data": True, "kind": "history-sample",
                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "window_from": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "window_to": end.strftime("%Y-%m-%dT%H:%M:%SZ")}
        try:
            records, quota, pages, truncated = fetch_window(key, bbox, start, end, args.pages)
        except (AuthError, QuotaExhausted) as e:
            print(f"DỪNG ({e.status}): {e}")
            return 2
        except FetchError as e:
            calls += 1
            misses += 1
            with open(HISTORY, "a", encoding="utf-8") as f:
                f.write(json.dumps(dict(line, status=None, error=str(e), vessels=[]), ensure_ascii=False) + "\n")
            print(f"{fmt(window)}: LỖI {e}")
        else:
            calls += pages
            misses = misses + 1 if not records else 0
            with open(HISTORY, "a", encoding="utf-8") as f:
                f.write(json.dumps(dict(line, status=200, pages=pages, truncated=truncated,
                                        vessels=records), ensure_ascii=False) + "\n")
            print(f"{fmt(window)}: {len(records)} vị trí, {len({r.get('mmsi') for r in records})} tàu"
                  f"{' (còn trang chưa lấy → chỉ phần mới nhất của cửa sổ)' if truncated else ''}"
                  f" | hạn mức còn {quota}")
            if quota is not None and quota <= args.reserve:
                print(f"Dừng: hạn mức còn {quota} ≤ dự phòng {args.reserve}.")
                break
        if misses >= 2:
            print("Dừng: 2 cửa sổ liên tiếp lỗi hoặc trống — có thể đã quá giới hạn lịch sử của gói.")
            break
    print(f"Xong: {calls} lượt gọi. Dữ liệu: {HISTORY.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
