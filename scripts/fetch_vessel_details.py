"""Fetch REAL vessel type (tanker or not) from VesselAPI for every MMSI we recorded.

MMSIs come from all real data in samples/ (recorder, live job, history samples).
The bounding-box endpoint has no ship type, so without this step we cannot say which
vessels are tankers (and we never guess). Endpoint (VesselAPI API reference):
    GET https://api.vesselapi.com/v1/vessel/{mmsi}?filter.idType=mmsi
    -> {"_meta": {...}, "vessel": {"vessel_type": ..., "vessel_subtype": ..., "name": ..., "imo": ...}}

1 vessel = 1 API call. Answers are cached in samples/vessel_details.jsonl (git-ignored),
so a vessel is never asked twice.

    python3 scripts/fetch_vessel_details.py --probe          # 1 call: print the raw answer
    python3 scripts/fetch_vessel_details.py --max-calls 20   # the rest (cached ones are skipped)
"""
import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.history import normalize_all, read_sources  # noqa: E402
from src.vesselapi_recorder import load_key  # noqa: E402

DETAILS_URL = "https://api.vesselapi.com/v1/vessel/{}"
CACHE = ROOT / "samples" / "vessel_details.jsonl"


def load_cache() -> dict:
    out = {}
    if CACHE.exists():
        for line in CACHE.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                out[row["mmsi"]] = row
            except (json.JSONDecodeError, KeyError):
                continue
    return out


def fetch(key: str, mmsi: str) -> tuple:
    url = DETAILS_URL.format(mmsi) + "?" + urllib.parse.urlencode({"filter.idType": "mmsi"})
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8")), resp.headers.get("X-RateLimit-Remaining")
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode("utf-8", "replace")[:300]}, e.headers.get("X-RateLimit-Remaining")


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    p = argparse.ArgumentParser()
    p.add_argument("--probe", action="store_true", help="chỉ gọi 1 tàu và in nguyên phản hồi")
    p.add_argument("--max-calls", type=int, default=20)
    p.add_argument("--reserve", type=int, default=20, help="dừng khi hạn mức còn ≤ số này")
    args = p.parse_args()

    positions, _ = normalize_all(read_sources()[0])      # recorded + history samples
    mmsis = sorted({pos["mmsi"] for pos in positions})
    cache = load_cache()
    todo = [m for m in mmsis if m not in cache or cache[m].get("status") not in (200, 404)]
    print(f"{len(mmsis)} tàu trong dữ liệu; đã có {len(mmsis) - len(todo)}; cần gọi {len(todo)}")
    if args.probe:
        todo = todo[:1]
    key = load_key()

    CACHE.parent.mkdir(exist_ok=True)
    calls = 0
    for mmsi in todo[:args.max_calls]:
        status, body, remaining = fetch(key, mmsi)
        calls += 1
        vessel = body.get("vessel") if isinstance(body, dict) else None
        row = {"mmsi": mmsi, "status": status, "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "response": body}
        with open(CACHE, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        if isinstance(vessel, dict):
            print(f"{mmsi} {vessel.get('name')!r}: vessel_type={vessel.get('vessel_type')!r} "
                  f"vessel_subtype={vessel.get('vessel_subtype')!r} | hạn mức còn {remaining}")
        else:
            print(f"{mmsi}: HTTP {status} {str(body)[:150]} | hạn mức còn {remaining}")
        if args.probe:
            print("\n--- Phản hồi thô (các khóa) ---")
            print("top-level:", list(body) if isinstance(body, dict) else type(body).__name__)
            if isinstance(vessel, dict):
                print("vessel:", sorted(vessel))
        if status in (401, 403, 429):
            print("DỪNG: lỗi khóa/quyền/hạn mức.")
            return 2
        if remaining is not None and str(remaining).isdigit() and int(remaining) <= args.reserve:
            print(f"DỪNG: hạn mức còn {remaining} ≤ {args.reserve}.")
            return 0
    print(f"Xong: {calls} lượt gọi. Kết quả: {CACHE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
