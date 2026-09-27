"""ONE CSV: vessels seen each day (name, position, tanker class) for the last N days.

Output: samples/powerbi/daily_vessels.csv — one row per vessel per UTC day.

What a row means (be precise when presenting it):
  - ship_name = name broadcast by AIS; registry_name / vessel_type = VesselAPI's vessel record
    for that MMSI (they can differ, e.g. after a rename).
  - mmsi / ship_name / latitude / longitude: a REAL VesselAPI observation near the western
    Musandam coast (the only area this source covers). Seen ≠ transited the strait.
    latitude/longitude = the vessel's LAST position that day; first_* = its first.
  - day_observed_utc / day_observed_hours: the part of that day we actually have data for
    (sampled days usually 4 h, not 24 h). Few vessels on a sampled day ≠ few vessels that day.
  - portwatch_*: IMF PortWatch transit counts for the WHOLE Strait of Hormuz that day
    (counts only, published with a delay; empty = not published yet).
  - Days without any data still get a row (row_type = not_observed / no_vessel_seen).

    python3 scripts/export_daily_vessels.py --days 15
"""
import argparse
import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from export_powerbi import TEMP_GATE, load_details, location_text, vessel_fields  # noqa: E402
from fetch_portwatch import load as load_portwatch  # noqa: E402
from src.history import day_coverage, normalize_all, parse_time, read_sources  # noqa: E402
from src.pipeline import parse_gate  # noqa: E402

OUT = ROOT / "samples" / "powerbi" / "daily_vessels.csv"
COLS = ["row_type", "date", "date_mmddyyyy", "mmsi", "ship_name", "registry_name", "imo", "vessel_type", "vessel_subtype",
        "tanker_class", "is_tanker", "first_seen_utc", "last_seen_utc", "positions_that_day",
        "latitude", "longitude", "location", "first_latitude", "first_longitude", "speed_knots",
        "crossed_temp_gate", "day_observed_utc", "day_observed_hours", "source",
        "portwatch_transits_total", "portwatch_tanker_transits", "data_kind"]


def crossings_by_day(positions: list, gate_raw: str) -> dict:
    """(date, mmsi) -> 'OUTBOUND 03:33' from Person B's detector (gap rule: <= 30 min)."""
    from src.crossing_detector import CrossingDetector
    detector = CrossingDetector(gate_coordinates=parse_gate(gate_raw))
    out = {}
    for p in positions:
        event = detector.process_position(p)
        if event:
            t = parse_time(event["crossing_time"])
            key = (t.date().isoformat(), event["mmsi"])
            out[key] = "; ".join(filter(None, [out.get(key), f"{event['direction']} {t:%H:%M}"]))
    return out


def build_rows(positions: list, intervals: list, details: dict, portwatch: dict,
               crossings: dict, first_day, last_day) -> list:
    by_day = {}
    for p in positions:
        by_day.setdefault(p["timestamp_utc"][:10], {}).setdefault(p["mmsi"], []).append(p)

    rows, day = [], first_day
    while day <= last_day:
        d = day.isoformat()
        cover = day_coverage(intervals, day)
        observed = "; ".join(f"{s:%H:%M}–{'24:00' if e.date() > day else format(e, '%H:%M')}"
                             for s, e, _ in cover)
        hours = round(sum((e - s).total_seconds() for s, e, _ in cover) / 3600, 1)
        pw = portwatch.get(d, {})
        base = {"date": d, "date_mmddyyyy": day.strftime("%m/%d/%Y"),
                "day_observed_utc": observed or "none", "day_observed_hours": hours,
                "portwatch_transits_total": pw.get("n_total", ""),
                "portwatch_tanker_transits": pw.get("n_tanker", ""),
                "data_kind": "REAL"}
        vessels = by_day.get(d, {})
        if not vessels:
            rows.append(dict(base, row_type="no_vessel_seen" if cover else "not_observed"))
        for mmsi, track in sorted(vessels.items(), key=lambda kv: (kv[1][-1].get("ship_name") or "", kv[0])):
            first, last = track[0], track[-1]
            name = next((p["ship_name"] for p in reversed(track) if p.get("ship_name")), None)
            rows.append(dict(base, row_type="vessel", **vessel_fields(mmsi, name, details),
                             registry_name=(details.get(mmsi) or {}).get("name") or "",
                             first_seen_utc=first["timestamp_utc"][11:16],
                             last_seen_utc=last["timestamp_utc"][11:16],
                             positions_that_day=len(track),
                             latitude=last["latitude"], longitude=last["longitude"],
                             location=location_text(last["latitude"], last["longitude"]),
                             first_latitude=first["latitude"], first_longitude=first["longitude"],
                             speed_knots="" if last.get("speed_knots") is None else last["speed_knots"],
                             crossed_temp_gate=crossings.get((d, mmsi), ""),
                             source="VesselAPI " + "+".join(sorted({p["source"] for p in track}))))
        day += timedelta(days=1)
    return rows


def main() -> int:
    p = argparse.ArgumentParser(description="Xuất 1 CSV: tàu thấy được mỗi ngày, vị trí, loại tàu.")
    p.add_argument("--days", type=int, default=15, help="số ngày lùi lại từ hôm nay (UTC)")
    p.add_argument("--out", default=str(OUT))
    p.add_argument("--gate", default=TEMP_GATE)
    args = p.parse_args()

    today = datetime.now(timezone.utc).date()
    first_day = today - timedelta(days=args.days)
    records, intervals, failures = read_sources()
    positions, skipped = normalize_all(records)
    details = load_details(ROOT / "samples" / "vessel_details.jsonl")
    portwatch = load_portwatch()
    rows = build_rows(positions, intervals, details, portwatch, crossings_by_day(positions, args.gate),
                      first_day, today)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    vessel_rows = [r for r in rows if r["row_type"] == "vessel"]
    print(f"{first_day:%m/%d/%Y} → {today:%m/%d/%Y} (UTC): {len(vessel_rows)} dòng tàu, "
          f"{len({r['mmsi'] for r in vessel_rows})} tàu khác nhau (bỏ {skipped} bản ghi lỗi)")
    for r in rows:
        if r["row_type"] != "vessel":
            print(f"  {r['date_mmddyyyy']}: {r['row_type']} | quan sát {r['day_observed_utc']}"
                  f" | PortWatch tổng {r['portwatch_transits_total'] or '—'}")
    tankers = {r["mmsi"] for r in vessel_rows if r["is_tanker"] == "Yes"}
    unknown = {r["mmsi"] for r in vessel_rows if r["is_tanker"] == "Unknown"}
    print(f"Tanker: {len(tankers)} tàu | chưa rõ loại: {len(unknown)} tàu"
          f"{' → chạy scripts/fetch_vessel_details.py' if unknown else ''}")
    if not portwatch:
        print("PortWatch: chưa có file → chạy scripts/fetch_portwatch.py")
    for f_ in failures:
        print(f"  cửa sổ lỗi: {f_['start']} → {f_['end']}: {f_['error']}")
    print(f"Đã ghi {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
