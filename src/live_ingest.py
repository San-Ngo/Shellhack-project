"""Periodic live job: VesselAPI -> normalize -> Person B's detector -> Azure SQL.

Every cycle:
    1. fetch positions since the newest one already stored (VesselAPI, quota-guarded)
    2. normalize (src.normalizer), drop duplicates and positions already processed
    3. feed positions in time order to Person B's CrossingDetector
    4. write in ONE transaction: vessels_latest (overwrite per MMSI), vessel_metadata,
       crossings (de-duplicated by mmsi + crossing_time + direction)
    5. update ingestion_status

When the API fails or the quota runs out, nothing is written except the status row:
old positions stay as they are and no position is ever invented.

Large time gaps: Person B's detector (default max_gap_minutes=30) ignores a "crossing"
between two positions that are too far apart in time -> not CONFIRMED. A second copy of
the same detector with a longer limit (--uncertain-max-gap, default 360 min) still notices
the side change; such events are stored with confidence = 'UNCERTAIN', never 'CONFIRMED'.

Run from the repo root (stop the old recorder first, it would spend the same quota):
    python3 -m src.live_ingest --once                       # one cycle, then stop
    caffeinate -i python3 -m src.live_ingest --interval 1800
    python3 -m src.live_ingest --replay samples/vesselapi_snapshots.jsonl   # no API calls
"""
import argparse
import copy
import importlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from src.config import ROOT
from src.normalizer import _parse_iso_utc, normalize_vesselapi
from src.pipeline import parse_gate
from src.vesselapi_recorder import OUT_FILE, PAGE_LIMIT, URL, StopRecording, load_bbox, load_key

SOURCE_NAME = "vesselapi"                      # row in ingestion_status
LIVE_LABEL = "vesselapi-live"                  # stored in source columns
DEFAULT_DETECTOR = "src.crossing_detector:CrossingDetector"
TEMP_GATE = "56.1,26.10,56.1,26.80"            # Person B's temporary gate (lon, lat)
MAX_WINDOW = timedelta(hours=4)                # VesselAPI: time.to - time.from <= 4 h
FIRST_WINDOW = timedelta(hours=2)
OVERLAP = timedelta(minutes=15)
LOG_FILE = ROOT / "samples" / "live_ingest.log"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def log(msg: str) -> None:
    line = f"[{now_utc().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    try:
        LOG_FILE.parent.mkdir(exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ---------- API errors (each maps to one ingestion_status value) ----------

class FetchError(Exception):
    status = "ERROR"          # temporary: retry next cycle
    stop = False


class AuthError(FetchError):
    status = "AUTH_ERROR"     # bad/expired key or no permission: stop, do not burn calls
    stop = True


class QuotaExhausted(FetchError):
    status = "QUOTA_EXHAUSTED"
    stop = True


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_window(key: str, bbox: dict, time_from: datetime, time_to: datetime,
                 max_pages: int) -> tuple:
    """GET positions in [time_from, time_to]. Returns (records, quota_remaining, pages, truncated)."""
    records, token, pages, remaining = [], None, 0, None
    while pages < max_pages:
        params = dict(bbox, **{"pagination.limit": PAGE_LIMIT,
                               "time.from": _iso(time_from), "time.to": _iso(time_to)})
        if token:
            params["pagination.nextToken"] = token
        req = urllib.request.Request(f"{URL}?{urllib.parse.urlencode(params)}",
                                     headers={"Authorization": f"Bearer {key}",
                                              "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                remaining = resp.headers.get("X-RateLimit-Remaining")
        except urllib.error.HTTPError as e:
            detail = f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}"
            if e.code in (401, 403):
                raise AuthError(detail)
            if e.code == 429:
                raise QuotaExhausted(detail)
            raise FetchError(detail)
        except urllib.error.URLError as e:
            raise FetchError(f"lỗi mạng: {e.reason}")
        except (TimeoutError, json.JSONDecodeError) as e:
            raise FetchError(f"phản hồi lỗi: {e!r}")
        pages += 1
        if not isinstance(data, dict) or not isinstance(data.get("vessels"), list):
            raise FetchError(f"phản hồi không có danh sách 'vessels': {str(data)[:200]}")
        records.extend(data["vessels"])
        token = data.get("nextToken")
        if not token:
            break
    quota = int(remaining) if remaining is not None and str(remaining).isdigit() else None
    return records, quota, pages, bool(token)


# ---------- detector state (kept in memory between cycles) ----------

@dataclass
class IngestState:
    strict: object                              # Person B's detector, default gap limit
    relaxed: Optional[object] = None            # same detector, longer gap limit -> UNCERTAIN
    last_seen: dict = field(default_factory=dict)   # mmsi -> newest processed timestamp (ISO Z)

    @property
    def newest(self) -> Optional[str]:
        return max(self.last_seen.values()) if self.last_seen else None


def make_detector(spec: str, gate: list, **kwargs):
    module_name, class_name = spec.split(":", 1)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    cls = getattr(importlib.import_module(module_name), class_name)
    return cls(gate_coordinates=gate, **kwargs)


def build_state(spec: str, gate: list, uncertain_max_gap: Optional[float],
                seed_positions: list) -> IngestState:
    """Create detectors and replay the stored latest positions as their starting point.

    A seed position is only a baseline: the detector never emits an event for the first
    position of a vessel, so seeding cannot create a crossing by itself.
    """
    state = IngestState(strict=make_detector(spec, gate))
    if uncertain_max_gap:
        try:
            state.relaxed = make_detector(spec, gate, max_gap_minutes=uncertain_max_gap)
        except TypeError:
            log("Detector không nhận max_gap_minutes → không ghi lượt UNCERTAIN.")
    for p in sorted(seed_positions, key=lambda p: p["timestamp_utc"]):
        state.strict.process_position(p)
        if state.relaxed is not None:
            state.relaxed.process_position(p)
        state.last_seen[p["mmsi"]] = p["timestamp_utc"]
    return state


def _minutes(a: str, b: str) -> float:
    return (_parse_iso_utc(b) - _parse_iso_utc(a)).total_seconds() / 60


def process_records(records: list, state: IngestState) -> dict:
    """Normalize raw VesselAPI records and run them through the detectors.

    Mutates `state`. Returns latest (1 per MMSI), metadata, crossings and counters.
    """
    stats = {"records": len(records), "skipped": 0, "duplicates": 0, "already_processed": 0,
             "new_positions": 0, "detector_errors": 0, "skip_reasons": {}}
    seen, positions, imo = set(), [], {}
    for rec in records:
        result = normalize_vesselapi(rec)
        if result.kind != "position":
            stats["skipped"] += 1
            reason = (result.reason or "unknown").split(": ", 1)[-1]
            stats["skip_reasons"][reason] = stats["skip_reasons"].get(reason, 0) + 1
            continue
        pos = result.data
        key = (pos["mmsi"], pos["timestamp_utc"])
        if key in seen:
            stats["duplicates"] += 1
            continue
        seen.add(key)
        positions.append(pos)
        if isinstance(rec, dict) and rec.get("imo"):
            imo[pos["mmsi"]] = rec.get("imo")
    positions.sort(key=lambda p: (p["timestamp_utc"], p["mmsi"]))

    latest, meta, crossings = {}, {}, []
    for pos in positions:
        mmsi, ts = pos["mmsi"], pos["timestamp_utc"]
        previous = state.last_seen.get(mmsi)
        if previous is not None and ts <= previous:
            stats["already_processed"] += 1      # overlap with the previous window
            continue
        stats["new_positions"] += 1
        gap = _minutes(previous, ts) if previous else None

        events = {}
        for name, det in (("strict", state.strict), ("relaxed", state.relaxed)):
            if det is None:
                continue
            try:
                events[name] = det.process_position(pos)
            except Exception as e:   # one bad call must not stop the job
                stats["detector_errors"] += 1
                log(f"lỗi detector ({name}) {mmsi} @ {ts}: {e!r}")
        if events.get("strict"):
            crossings.append(dict(events["strict"], confidence="CONFIRMED", gap_minutes=gap))
        elif events.get("relaxed"):
            crossings.append(dict(events["relaxed"], confidence="UNCERTAIN", gap_minutes=gap))

        state.last_seen[mmsi] = ts
        latest[mmsi] = pos
        m = meta.setdefault(mmsi, {"mmsi": mmsi, "imo": imo.get(mmsi), "ship_name": None,
                                   "ship_type": None, "first_seen_utc": ts, "last_seen_utc": ts})
        m["ship_name"] = pos.get("ship_name") or m["ship_name"]
        m["ship_type"] = pos.get("ship_type") if pos.get("ship_type") is not None else m["ship_type"]
        m["last_seen_utc"] = ts
    stats["vessels"] = len(latest)
    newest = max((p["timestamp_utc"] for p in latest.values()), default=None)
    return {"latest": list(latest.values()), "metadata": list(meta.values()),
            "crossings": crossings, "newest": newest, "stats": stats}


# ---------- one cycle ----------

def request_window(state: IngestState, now: datetime) -> tuple:
    """Ask only for what is new (plus a small overlap); never more than VesselAPI's 4 h."""
    start = now - FIRST_WINDOW
    if state.newest:
        start = _parse_iso_utc(state.newest) - OVERLAP
    return max(start, now - MAX_WINDOW + timedelta(minutes=1)), now


def run_cycle(fetch: Callable, store, state: IngestState, reserve: int = 20,
              label: str = LIVE_LABEL, archive: Optional[Callable] = None) -> tuple:
    """Fetch -> process -> write. Returns (state, outcome dict). outcome['stop'] ends the loop."""
    time_from, time_to = request_window(state, now_utc())
    try:
        records, quota, pages, truncated = fetch(time_from, time_to)
    except FetchError as e:
        log(f"API lỗi ({e.status}): {e} → giữ dữ liệu cũ, không ghi vị trí.")
        _safe_status(store, "failure", SOURCE_NAME, e.status, str(e))
        return state, {"ok": False, "status": e.status, "stop": e.stop}

    if archive is not None:
        archive(records, time_from, time_to, pages, truncated)

    before = copy.deepcopy(state)
    batch = process_records(records, state)
    try:
        written = store.write_batch(batch["latest"], batch["metadata"], batch["crossings"], label)
        store.record_success(SOURCE_NAME, rows=written["vessels"], data_time=batch["newest"], quota=quota)
    except Exception as e:
        # Roll the detector back so the same positions are processed again next cycle.
        log(f"Ghi Azure SQL lỗi: {e!r} → không mất dữ liệu, sẽ xử lý lại ở lần sau.")
        _safe_status(store, "failure", SOURCE_NAME, "ERROR", f"ghi database lỗi: {e!r}", quota)
        return before, {"ok": False, "status": "ERROR", "stop": False}

    s = batch["stats"]
    log(f"OK: {s['records']} bản ghi, {pages} trang{' (CÒN TRANG CHƯA LẤY)' if truncated else ''} → "
        f"{s['new_positions']} vị trí mới / {s['vessels']} tàu (bỏ {s['skipped']} lỗi, "
        f"{s['duplicates'] + s['already_processed']} trùng) | lượt vượt: lưu {written['crossings_stored']}, "
        f"trùng {written['crossings_duplicate']} | hạn mức còn: {quota}")
    for c in batch["crossings"]:
        log(f"  LƯỢT VƯỢT {c['confidence']}: {c.get('ship_name')} ({c['mmsi']}) {c['direction']} "
            f"@ {c['crossing_time']} (cách vị trí trước {c.get('gap_minutes')} phút)")

    if quota is not None and quota <= reserve:
        msg = f"hạn mức còn {quota} ≤ dự phòng {reserve}: ngừng gọi API, giữ dữ liệu hiện có"
        log(f"Dừng: {msg}.")
        _safe_status(store, "failure", SOURCE_NAME, "QUOTA_EXHAUSTED", msg, quota)
        return state, {"ok": True, "status": "QUOTA_EXHAUSTED", "stop": True, "written": written}
    return state, {"ok": True, "status": "OK", "stop": False, "written": written, "stats": s}


def _safe_status(store, kind: str, source: str, status: str, error: str, quota=None) -> None:
    try:
        store.record_failure(source, status, error, quota)
    except Exception as e:   # the database itself may be the problem
        log(f"  (không ghi được ingestion_status: {e!r})")


def archive_to_samples(records, time_from, time_to, pages, truncated) -> None:
    """Keep the raw response locally (git-ignored) so the pipeline can replay it later."""
    try:
        OUT_FILE.parent.mkdir(exist_ok=True)
        with open(OUT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"source": "vesselapi", "real_data": True,
                                "fetched_at": now_utc().isoformat(timespec="seconds"),
                                "time_from": _iso(time_from), "time_to": _iso(time_to),
                                "pages": pages, "truncated": truncated, "vessels": records},
                               ensure_ascii=False) + "\n")
    except OSError as e:
        log(f"  (không lưu được bản sao cục bộ: {e})")


def replay_file(path, store, state: IngestState) -> dict:
    """Load a recorded snapshot file into Azure SQL (no API call). Label follows the file."""
    records, real = [], True
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                snap = json.loads(line)
            except json.JSONDecodeError:
                continue
            real = real and snap.get("real_data") is True
            records.extend(snap.get("vessels") or [])
    label = "vesselapi-replay" if real else "simulated"
    batch = process_records(records, state)
    written = store.write_batch(batch["latest"], batch["metadata"], batch["crossings"], label)
    return {"label": label, "stats": batch["stats"], "written": written, "crossings": batch["crossings"]}


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from src.azure_store import AzureConfigError, AzureStore

    p = argparse.ArgumentParser(description="VesselAPI → detector → Azure SQL, chạy định kỳ.")
    p.add_argument("--interval", type=int, default=1800, help="giây giữa 2 lần lấy (mặc định 1800 = 30 phút)")
    p.add_argument("--max-pages", type=int, default=2, help="tối đa số trang (lượt gọi) mỗi lần")
    p.add_argument("--reserve", type=int, default=20, help="ngừng gọi API khi hạn mức còn ≤ số này")
    p.add_argument("--once", action="store_true", help="chạy 1 lần rồi dừng")
    p.add_argument("--replay", default=None, help="nạp file snapshot đã ghi (không gọi API)")
    p.add_argument("--detector", default=os.getenv("DETECTOR") or DEFAULT_DETECTOR)
    p.add_argument("--gate", default=os.getenv("GATE_COORDINATES") or TEMP_GATE, help="lon1,lat1,lon2,lat2")
    p.add_argument("--brent-every-hours", type=float, default=6,
                   help="cập nhật giá Brent (FRED) mỗi N giờ trong vòng lặp (0 = tắt)")
    p.add_argument("--uncertain-max-gap", type=float, default=360,
                   help="phút; khoảng trống lớn hơn mức của detector nhưng ≤ số này → UNCERTAIN (0 = tắt)")
    args = p.parse_args()

    try:
        gate = parse_gate(args.gate)
        store = AzureStore.from_env()
    except (ValueError, AzureConfigError, ConnectionError) as e:
        log(f"LỖI CẤU HÌNH: {e}")
        return 1

    try:
        store.init_schema()
        seed = store.load_latest_positions()
        state = build_state(args.detector, gate, args.uncertain_max_gap or None, seed)
        log(f"Azure SQL sẵn sàng | {len(seed)} tàu đã có | cổng {gate}"
            f"{' (CỔNG TẠM)' if args.gate == TEMP_GATE else ''} | detector {args.detector}")

        if args.replay:
            out = replay_file(args.replay, store, state)
            s, w = out["stats"], out["written"]
            log(f"Nạp {args.replay} (nhãn {out['label']}): {s['new_positions']} vị trí mới / "
                f"{s['vessels']} tàu → vessels_latest {w['vessels']}, lượt vượt lưu {w['crossings_stored']}, "
                f"trùng {w['crossings_duplicate']}")
            return 0

        key, bbox = load_key(), load_bbox()
        fetch = lambda t0, t1: fetch_window(key, bbox, t0, t1, args.max_pages)  # noqa: E731
        log(f"Bắt đầu: mỗi {args.interval}s, tối đa {args.max_pages} trang/lần, dự phòng {args.reserve}")
        next_brent = 0.0
        while True:
            if args.brent_every_hours and time.monotonic() >= next_brent:
                from src.brent_loader import refresh
                b = refresh(store)
                log(f"Brent: {'nạp ' + str(b['rows']) + ' ngày, mới nhất ' + b['latest']['price_date'] if b['ok'] else 'LỖI ' + b['error'] + ' → giữ giá cũ'}")
                next_brent = time.monotonic() + args.brent_every_hours * 3600
            state, outcome = run_cycle(fetch, store, state, args.reserve, archive=archive_to_samples)
            if outcome["stop"] or args.once:
                return 0 if outcome["ok"] else 2
            time.sleep(args.interval)
    except StopRecording as e:
        log(f"LỖI CẤU HÌNH: {e}")
        return 1
    except KeyboardInterrupt:
        log("Dừng theo yêu cầu (Ctrl+C).")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
