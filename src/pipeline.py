"""GIỜ 4.5–5: replay recorded REAL VesselAPI data through the whole data flow.

    snapshots (samples/vesselapi_snapshots.jsonl)
      -> normalize_vesselapi            (shared vessel object)
      -> drop duplicates, sort by time  (snapshots overlap ~10 min)
      -> upsert_vessel                  (SQLite: latest state per MMSI)
      -> insert_position                (SQLite: vessel_positions, full track history)
      -> detector.process_position      (Person B's CrossingDetector)
      -> insert_crossing                (SQLite: crossings, de-duplicated)

Person A does NOT implement crossing detection. The detector is loaded from
Person B's code by import path, and the gate is passed in as (lon, lat) pairs:

    python3 -m src.pipeline                                  # vessels only, no detector
    python3 -m src.pipeline --detector detector:CrossingDetector \
        --gate "LON1,LAT1,LON2,LAT2"                         # gate order: lon, lat (Person B's API)

Or put GATE_COORDINATES=LON1,LAT1,LON2,LAT2 in .env.
"""
import argparse
import importlib
import json
import os
import sys
from pathlib import Path
from typing import Iterable, Optional

from src.config import ROOT
from src.database import init_db, insert_crossing, insert_position, upsert_vessel
from src.normalizer import normalize_vesselapi

DEFAULT_SNAPSHOTS = ROOT / "samples" / "vesselapi_snapshots.jsonl"


def load_positions(path: Path) -> tuple:
    """Read snapshot lines -> normalized positions, de-duplicated and time-ordered.

    Returns (positions, stats). A bad line or record is counted and skipped, never fatal.
    """
    stats = {"snapshots": 0, "records": 0, "skipped": 0, "duplicates": 0, "bad_lines": 0,
             "real_data": True, "skip_reasons": {}}
    seen, positions = set(), []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                snap = json.loads(line)
            except json.JSONDecodeError:
                stats["bad_lines"] += 1
                continue
            stats["snapshots"] += 1
            if snap.get("real_data") is not True:
                stats["real_data"] = False
            for rec in snap.get("vessels") or []:
                stats["records"] += 1
                result = normalize_vesselapi(rec)
                if result.kind != "position":
                    stats["skipped"] += 1
                    key = (result.reason or "unknown").split(": ", 1)[-1]
                    stats["skip_reasons"][key] = stats["skip_reasons"].get(key, 0) + 1
                    continue
                pos = result.data
                dedupe_key = (pos["mmsi"], pos["timestamp_utc"])
                if dedupe_key in seen:
                    stats["duplicates"] += 1
                    continue
                seen.add(dedupe_key)
                positions.append(pos)
    # Person B: "send positions in time order". ISO 'Z' strings sort chronologically.
    positions.sort(key=lambda p: (p["timestamp_utc"], p["mmsi"]))
    stats["positions"] = len(positions)
    stats["vessels"] = len({p["mmsi"] for p in positions})
    if positions:
        stats["from"], stats["to"] = positions[0]["timestamp_utc"], positions[-1]["timestamp_utc"]
    return positions, stats


def run(positions: Iterable[dict], conn, detector=None, source: str = "vesselapi-replay") -> dict:
    """Push positions through storage and (optionally) Person B's detector."""
    stats = {"stored_positions": 0, "rejected_positions": 0,
             "track_positions_stored": 0, "track_positions_duplicate": 0, "detector_errors": 0,
             "events": 0, "crossings_stored": 0, "crossings_duplicate": 0,
             "crossings_rejected": 0, "errors": []}
    for pos in positions:
        try:
            upsert_vessel(conn, pos)
            stats["stored_positions"] += 1
            if insert_position(conn, pos, source=source):
                stats["track_positions_stored"] += 1
            else:
                stats["track_positions_duplicate"] += 1
        except ValueError as e:
            stats["rejected_positions"] += 1
            stats["errors"].append(f"vessel {pos.get('mmsi')}: {e}")
            continue

        if detector is None:
            continue
        try:
            event = detector.process_position(pos)
        except Exception as e:  # one bad detector call must not stop the replay
            stats["detector_errors"] += 1
            stats["errors"].append(f"detector {pos.get('mmsi')} @ {pos.get('timestamp_utc')}: {e!r}")
            continue
        if event is None:
            continue

        stats["events"] += 1
        try:
            if insert_crossing(conn, event, source=source):
                stats["crossings_stored"] += 1
            else:
                stats["crossings_duplicate"] += 1
        except ValueError as e:
            stats["crossings_rejected"] += 1
            stats["errors"].append(f"crossing {event!r}: {e}")
    return stats


def parse_gate(raw: Optional[str]) -> Optional[list]:
    """'lon1,lat1,lon2,lat2' -> [(lon1, lat1), (lon2, lat2)] (Person B's order)."""
    if not raw:
        return None
    try:
        lon1, lat1, lon2, lat2 = (float(x) for x in raw.split(","))
    except ValueError:
        raise ValueError(f"gate must be 'lon1,lat1,lon2,lat2', got {raw!r}")
    for lon, lat in ((lon1, lat1), (lon2, lat2)):
        if not -180 <= lon <= 180 or not -90 <= lat <= 90:
            raise ValueError(f"gate point out of range (lon={lon}, lat={lat}); order is lon,lat")
    if (lon1, lat1) == (lon2, lat2):
        raise ValueError("gate endpoints must differ")
    return [(lon1, lat1), (lon2, lat2)]


def load_detector(spec: str, gate: list):
    """'module.path:ClassName' -> ClassName(gate_coordinates=gate)."""
    if ":" not in spec:
        raise ValueError(f"detector must look like 'module:ClassName', got {spec!r}")
    module_name, class_name = spec.split(":", 1)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    cls = getattr(importlib.import_module(module_name), class_name)
    return cls(gate_coordinates=gate)


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

    p = argparse.ArgumentParser(description="Replay recorded VesselAPI data into SQLite.")
    p.add_argument("--snapshots", default=str(DEFAULT_SNAPSHOTS))
    p.add_argument("--db", default=None, help="SQLite path (default: DB_PATH in .env)")
    p.add_argument("--detector", default=os.getenv("DETECTOR"),
                   help="Person B's detector, e.g. detector:CrossingDetector")
    p.add_argument("--gate", default=os.getenv("GATE_COORDINATES"), help="lon1,lat1,lon2,lat2")
    p.add_argument("--source", default=None,
                   help="label stored with crossings (default: vesselapi-replay for real data, "
                        "simulated otherwise)")
    args = p.parse_args()

    path = Path(args.snapshots)
    if not path.exists():
        print(f"LỖI: không thấy {path}. Chạy src.vesselapi_recorder trước.")
        return 1

    detector = None
    if args.detector:
        try:
            gate = parse_gate(args.gate)
            if gate is None:
                print("LỖI: có --detector nhưng chưa có tọa độ cổng (--gate hoặc GATE_COORDINATES).")
                return 1
            detector = load_detector(args.detector, gate)
        except (ValueError, ImportError, AttributeError, TypeError) as e:
            print(f"LỖI khi tạo detector: {e}")
            return 1

    positions, load_stats = load_positions(path)
    source = args.source or ("vesselapi-replay" if load_stats["real_data"] else "simulated")
    conn = init_db(args.db)
    run_stats = run(positions, conn, detector, source=source)
    conn.close()

    label = "THẬT (VesselAPI, phát lại)" if load_stats["real_data"] else "MÔ PHỎNG (không phải dữ liệu thật)"
    print(f"Dữ liệu: {label} | nhãn source = {source!r}")
    print(f"  {load_stats['snapshots']} lần chụp, {load_stats['records']} bản ghi → "
          f"{load_stats['positions']} vị trí của {load_stats['vessels']} tàu "
          f"(bỏ {load_stats['duplicates']} trùng, {load_stats['skipped']} không hợp lệ)")
    if load_stats.get("from"):
        print(f"  Thời gian: {load_stats['from']} → {load_stats['to']}")
    for reason, n in load_stats["skip_reasons"].items():
        print(f"  bỏ qua {n}: {reason}")
    print(f"Đã lưu {run_stats['stored_positions']} vị trí vào bảng vessels "
          f"({run_stats['rejected_positions']} bị từ chối)")
    print(f"Bảng vessel_positions (đường đi): thêm {run_stats['track_positions_stored']}, "
          f"trùng {run_stats['track_positions_duplicate']} | nhãn source = {source!r}")
    if detector is None:
        print("Detector: CHƯA DÙNG — chưa đánh giá lượt vượt cổng.")
    else:
        print(f"Detector: {run_stats['events']} sự kiện → lưu {run_stats['crossings_stored']}, "
              f"trùng {run_stats['crossings_duplicate']}, từ chối {run_stats['crossings_rejected']}, "
              f"lỗi detector {run_stats['detector_errors']}")
    for err in run_stats["errors"][:10]:
        print(f"  ! {err}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
