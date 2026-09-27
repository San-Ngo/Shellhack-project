"""Export recorded REAL VesselAPI data as CSV files for Power BI (map + table).

Inputs (all in samples/, git-ignored):
    vesselapi_snapshots.jsonl   positions (recorder / live job)
    vessel_details.jsonl        vessel type per MMSI (scripts/fetch_vessel_details.py)

Outputs in samples/powerbi/:
    vessel_positions.csv   every position (track), with tanker class, MM/DD/YYYY date, location
    vessels_latest.csv     newest position per vessel
    crossings.csv          gate crossings from Person B's detector (temporary gate)

Tanker rule (only from VesselAPI's own vessel_type / vessel_subtype text, never guessed):
    no details            -> Unknown
    no "tanker" in text   -> Not tanker
    "oil" or "crude"      -> Oil tanker
    otherwise             -> Tanker (other/unspecified cargo)

    python3 scripts/export_powerbi.py
    python3 scripts/export_powerbi.py --snapshots demo/simulated_tracks.jsonl --out samples/powerbi_demo
"""
import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.normalizer import _parse_iso_utc  # noqa: E402
from src.pipeline import load_positions, parse_gate  # noqa: E402

TEMP_GATE = "56.1,26.10,56.1,26.80"


def load_details(path: Path) -> dict:
    """mmsi -> vessel dict from VesselAPI (last successful answer wins)."""
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        vessel = (row.get("response") or {}).get("vessel") if row.get("status") == 200 else None
        if isinstance(vessel, dict):
            out[str(row.get("mmsi"))] = vessel
    return out


def tanker_class(vessel: dict | None) -> str:
    if not vessel:
        return "Unknown"
    text = f"{vessel.get('vessel_type') or ''} {vessel.get('vessel_subtype') or ''}".lower()
    if not text.strip():
        return "Unknown"
    if "tanker" not in text:
        return "Not tanker"
    if "oil" in text or "crude" in text:
        return "Oil tanker"
    return "Tanker (other/unspecified cargo)"


def is_tanker(cls: str) -> str:
    return {"Unknown": "Unknown", "Not tanker": "No"}.get(cls, "Yes")


def location_text(lat: float, lon: float) -> str:
    return f"{abs(lat):.4f}°{'N' if lat >= 0 else 'S'}, {abs(lon):.4f}°{'E' if lon >= 0 else 'W'}"


def time_fields(iso: str) -> dict:
    dt = _parse_iso_utc(iso)
    return {"timestamp_utc": dt.strftime("%Y-%m-%d %H:%M:%S"),   # Power BI reads as Date/Time
            "date": dt.strftime("%Y-%m-%d"),                    # set type Date, format MM/dd/yyyy
            "date_mmddyyyy": dt.strftime("%m/%d/%Y"),           # ready-made text label
            "time_utc": dt.strftime("%H:%M")}


def vessel_fields(mmsi: str, name, details: dict) -> dict:
    v = details.get(mmsi)
    cls = tanker_class(v)
    return {"mmsi": mmsi,
            "ship_name": name or (v or {}).get("name") or "",
            "imo": (v or {}).get("imo") or "",
            "vessel_type": (v or {}).get("vessel_type") or "",
            "vessel_subtype": (v or {}).get("vessel_subtype") or "",
            "flag": (v or {}).get("country") or "",
            "tanker_class": cls,
            "is_tanker": is_tanker(cls)}


POSITION_COLS = ["mmsi", "ship_name", "imo", "vessel_type", "vessel_subtype", "flag", "tanker_class",
                 "is_tanker", "latitude", "longitude", "location", "timestamp_utc", "date",
                 "date_mmddyyyy", "time_utc", "speed_knots", "course_deg", "data_kind"]
CROSSING_COLS = ["mmsi", "ship_name", "vessel_type", "vessel_subtype", "tanker_class", "is_tanker",
                 "direction", "latitude", "longitude", "location", "timestamp_utc", "date",
                 "date_mmddyyyy", "time_utc", "confidence", "gate", "data_kind"]


def build_rows(positions: list, details: dict, data_kind: str) -> tuple:
    rows, latest = [], {}
    for p in positions:
        row = dict(vessel_fields(p["mmsi"], p.get("ship_name"), details),
                   latitude=p["latitude"], longitude=p["longitude"],
                   location=location_text(p["latitude"], p["longitude"]),
                   speed_knots="" if p.get("speed_knots") is None else p["speed_knots"],
                   course_deg="" if p.get("course_deg") is None else p["course_deg"],
                   data_kind=data_kind, **time_fields(p["timestamp_utc"]))
        rows.append(row)
        latest[p["mmsi"]] = row          # positions are time-ordered -> last one wins
    return rows, list(latest.values())


def detect_crossings(positions: list, details: dict, gate_raw: str, data_kind: str) -> list:
    from src.crossing_detector import CrossingDetector   # Person B's detector
    detector = CrossingDetector(gate_coordinates=parse_gate(gate_raw))
    out = []
    for p in positions:
        event = detector.process_position(p)
        if not event:
            continue
        lat, lon = event["latitude"], event["longitude"]
        out.append(dict(vessel_fields(event["mmsi"], event.get("ship_name"), details),
                        direction=event["direction"], latitude=round(lat, 6), longitude=round(lon, 6),
                        location=location_text(lat, lon), confidence="CONFIRMED",
                        gate=f"{gate_raw} (temporary)", data_kind=data_kind,
                        **time_fields(event["crossing_time"])))
    return out


def write_csv(path: Path, cols: list, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:   # BOM: Excel/Power BI read UTF-8 right
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    p = argparse.ArgumentParser(description="Xuất CSV cho Power BI (bản đồ tàu + bảng).")
    p.add_argument("--snapshots", default=str(ROOT / "samples" / "vesselapi_snapshots.jsonl"))
    p.add_argument("--details", default=str(ROOT / "samples" / "vessel_details.jsonl"))
    p.add_argument("--out", default=str(ROOT / "samples" / "powerbi"))
    p.add_argument("--gate", default=TEMP_GATE)
    args = p.parse_args()

    positions, stats = load_positions(Path(args.snapshots))
    details = load_details(Path(args.details))
    data_kind = "REAL (VesselAPI)" if stats["real_data"] else "SIMULATED"
    rows, latest = build_rows(positions, details, data_kind)
    crossings = detect_crossings(positions, details, args.gate, data_kind)

    out = Path(args.out)
    write_csv(out / "vessel_positions.csv", POSITION_COLS, rows)
    write_csv(out / "vessels_latest.csv", POSITION_COLS, latest)
    write_csv(out / "crossings.csv", CROSSING_COLS, crossings)

    counts = {}
    for r in latest:
        counts[r["tanker_class"]] = counts.get(r["tanker_class"], 0) + 1
    print(f"Dữ liệu: {data_kind} | {len(rows)} vị trí, {len(latest)} tàu, {len(crossings)} lượt vượt")
    print(f"Thời gian (UTC): {stats.get('from')} → {stats.get('to')}")
    print("Phân loại tàu:", ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    if counts.get("Unknown"):
        print("  (Unknown = chưa có loại tàu → chạy scripts/fetch_vessel_details.py)")
    print(f"Đã ghi: {out}/vessel_positions.csv, vessels_latest.csv, crossings.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
