"""Generate SIMULATED vessel tracks for demoing the crossing detector.

Real VesselAPI data recorded so far has no vessel crossing the temporary gate
(see docs/ais_source.md), so the detector is demonstrated with these synthetic
tracks. Every line is marked "real_data": false and "source": "simulated";
MMSIs start with 99900 and names start with "SIM-" so they cannot be mistaken
for real vessels.

    python3 scripts/make_simulated_tracks.py          # writes demo/simulated_tracks.jsonl
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "demo" / "simulated_tracks.jsonl"
START = datetime(2026, 9, 26, 22, 0, tzinfo=timezone.utc)
STEP_MIN = 5  # same spacing as the real VesselAPI data

# (mmsi, name, lat, lon_start, lon_end, knots) — straight east/west tracks crossing lon 56.1.
# No point lands exactly on lon 56.1: Person B's detector (shapely .crosses) treats a point
# on the gate as "touching" and misses that crossing — reported to Person B, not hidden here.
TRACKS = [
    (999000001, "SIM-WESTBOUND-1", 26.40, 56.17, 56.05, 12.0),   # longitude decreases
    (999000002, "SIM-EASTBOUND-1", 26.60, 56.03, 56.15, 11.0),   # longitude increases
    (999000003, "SIM-ANCHORED-1", 26.30, 56.08, 56.08, 0.0),     # never crosses
]
STEPS = 7


def records():
    out = []
    for mmsi, name, lat, lon0, lon1, knots in TRACKS:
        cog = 270.0 if lon1 < lon0 else 90.0 if lon1 > lon0 else 0.0
        for i in range(STEPS):
            ts = START + timedelta(minutes=STEP_MIN * i)
            lon = lon0 + (lon1 - lon0) * i / (STEPS - 1)
            out.append({"mmsi": mmsi, "vessel_name": name, "latitude": lat,
                        "longitude": round(lon, 5), "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "sog": knots, "cog": cog, "suspected_glitch": False})
    return out


def main():
    OUT.parent.mkdir(exist_ok=True)
    snapshot = {"source": "simulated", "real_data": False,
                "note": "SIMULATED tracks for detector demo — not AIS data",
                "vessels": records()}
    OUT.write_text(json.dumps(snapshot) + "\n", encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)}: {len(snapshot['vessels'])} SIMULATED positions")


if __name__ == "__main__":
    main()
