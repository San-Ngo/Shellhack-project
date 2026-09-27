# Shared data interface — Person A ↔ Person B

## 1. Vessel position (A sends to B)

```json
{
  "mmsi": "123456789",
  "ship_name": null,
  "ship_type": null,
  "latitude": 26.5,
  "longitude": 56.3,
  "speed_knots": null,
  "course_deg": null,
  "timestamp_utc": "2026-09-26T22:00:00Z",
  "source_message_type": "PositionReport"
}
```

- Contains all 6 fields B needs (`mmsi, latitude, longitude, timestamp_utc, ship_name, ship_type`). B can ignore the 3 extra fields (`speed_knots, course_deg, source_message_type`).
- `mmsi`: 9-character string.
- `latitude` ∈ [-90, 90], `longitude` ∈ [-180, 180] — A already filters these; invalid messages never reach B.
- `timestamp_utc`: ISO 8601 string, always ending in `Z`. With VesselAPI (the current primary source) this is the **actual AIS time from the source** (the `timestamp` field).
- VesselAPI data is a snapshot roughly every 10 minutes → two consecutive positions of one vessel can be several km apart. The detector should check whether the **line segment joining the 2 positions** crosses the gate.
- `ship_type`: **integer AIS code** (e.g. 80–89 = tanker) or `null` when unknown. **The VesselAPI endpoint in use has no vessel type → currently always `null`.** Never guess. `null` ≠ "not a tanker".
- `ship_name`: whitespace-trimmed string, or `null`.

## 2. Crossing (B returns to A)

```json
{
  "mmsi": "123456789",
  "crossing_time": "2026-09-26T22:05:00Z",
  "direction": "?",
  "latitude": 26.55,
  "longitude": 56.35,
  "ship_name": null,
  "ship_type": null
}
```

A stores it with `src/database.py` (already built, 23 test cases):

```python
from src.database import init_db, insert_crossing, get_recent_crossings
conn = init_db()                               # DB_PATH from .env, or init_db(":memory:") for tests
stored = insert_crossing(conn, crossing, source="vesselapi-replay")  # True = stored, False = duplicate
```

- Required: `mmsi`, `crossing_time` (ISO with timezone), `direction` (non-empty string). Missing/invalid → `ValueError`, nothing is written.
- Optional: `latitude`, `longitude`, `ship_name`, `ship_type`.
- Resending the same `(mmsi, crossing_time, direction)` → no duplicate row, returns `False`.
- `source`: `"vesselapi-live"`, `"vesselapi-replay"` or `"simulated"` — so we always know which data an event came from.

## Agreed with B (2026-09-26 evening)

- **How to call it:** `detector = CrossingDetector(gate_coordinates=[(lon1, lat1), (lon2, lat2)])`, then `detector.process_position(position)` for each position → an event or `None`. The detector remembers the previous position per MMSI on its own. **Send in time order** — `src/pipeline.py` already deduplicates and sorts before sending.
- **Gate coordinate order: (longitude, latitude).**
- **`crossing_time`:** currently the time of the second position, in `+00:00` form. `insert_crossing` converts it to `Z` automatically — no change needed.
- **Event `latitude/longitude`:** the intersection point with the gate line.

## Still open (owned by B)

1. **`direction`:** the code currently returns `EASTBOUND`/`WESTBOUND` (by increasing/decreasing longitude), which is not yet the same as `INBOUND`/`OUTBOUND`. The database can store both, but **don't rely on the direction labels** until B confirms which side is the Persian Gulf.
2. **Real Hormuz gate coordinates:** not available yet (B's tests use a simulated line). The pipeline refuses to run the detector without a gate.
   - Note from real data (21:55–23:59 UTC): the latest positions of all 9 vessels are at **longitude 56.00–56.21**, latitude 26.00–26.81 — right at the western edge of the recording box. The gate should sit where there is actually data, or we need to record more / change the box before finalizing it.
