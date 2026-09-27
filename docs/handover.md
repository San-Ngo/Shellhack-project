# Data handover (Person A → Person B)

Status: 2026-09-27, `main` @ `9cef077` (after PR #5). Tests: `python3 -m pytest -q` → 70 passed.

## 1. Functions to call

```python
from src.database import (init_db, upsert_vessel, insert_position, insert_crossing,
                          get_recent_crossings, get_vessel, get_track)
from src.normalizer import normalize_vesselapi
from src.pipeline import load_positions, run
```

| Function | Purpose |
|---|---|
| `init_db(path=None)` | Open/create SQLite (`DB_PATH` in `.env`, or `":memory:"` for tests) |
| `upsert_vessel(conn, position)` | Store a vessel's latest position. Same MMSI → update; an older position never overwrites a newer one |
| `insert_position(conn, position, source=...)` | Store 1 position in the track history (`vessel_positions`). `True` = stored, `False` = duplicate `(mmsi, timestamp_utc)` |
| `get_track(conn, mmsi)` | All positions of 1 vessel, oldest first — for drawing tracks |
| `insert_crossing(conn, crossing, source=...)` | Store a crossing. `True` = stored, `False` = duplicate. Invalid data → `ValueError`, nothing written |
| `get_recent_crossings(conn, limit=20)` | Most recent crossings first |
| `load_positions(path)` | Read a snapshot file → normalized positions, deduplicated and **sorted by time** |
| `run(positions, conn, detector, source)` | Store vessels + store tracks + call `detector.process_position()` + store crossings |

## 2. Object structure

Position (A → B):
```json
{"mmsi": "375606000", "ship_name": "STANFORD EAGLE", "ship_type": null,
 "latitude": 25.94801, "longitude": 56.06015, "speed_knots": 0.0, "course_deg": 60.7,
 "timestamp_utc": "2026-09-26T23:39:33Z", "source_message_type": "VesselAPI.bounding-box"}
```

Crossing (B → A) — matches the current output of `CrossingDetector`:
```json
{"mmsi": "999000001", "crossing_time": "2026-09-26T22:20:00+00:00", "direction": "INBOUND",
 "latitude": 26.4, "longitude": 56.1, "ship_name": "SIM-WESTBOUND-1", "ship_type": null}
```

- Required: `mmsi`, `crossing_time` (with timezone; `+00:00` is converted to `Z`), `direction` (non-empty).
- `ship_type`: **AIS integer or `null`**. Strings like `"Cargo"` are rejected.
- Deduplication: key `(mmsi, crossing_time, direction)`.
- `crossings.source`: `vesselapi-replay` (real data) or `simulated` — the pipeline sets it automatically based on the input file.

### SQLite tables

| Table | Each row is | Dedup key |
|---|---|---|
| `vessels` | the **latest** position of 1 vessel | `mmsi` |
| `vessel_positions` | **1 position** in a vessel's track (history) | `(mmsi, timestamp_utc)` |
| `crossings` | 1 gate crossing | `(mmsi, crossing_time, direction)` |

`vessel_positions.source` and `crossings.source`: `vesselapi-replay` (real) or `simulated`.

Example track query:
```sql
SELECT mmsi, ship_name, timestamp_utc, latitude, longitude, speed_knots, course_deg, source
FROM vessel_positions ORDER BY mmsi, timestamp_utc;
```

### Handing the database to Person B

A creates a separate database for B (real data + crossings):
```bash
python3 -m src.pipeline --db samples/hormuz_watch_for_B.db --detector src.crossing_detector:CrossingDetector --gate "56.1,26.10,56.1,26.80"
```
Send `samples/hormuz_watch_for_B.db` directly to B (private message/Drive). **Don't commit it or post it publicly** — VesselAPI data may not be redistributed. B puts the file in the repo folder and opens it with `init_db("hormuz_watch_for_B.db")` or `sqlite3`.

## 3. How to run the pipeline

Detector demo with simulated data:
```bash
python3 -m src.pipeline --snapshots demo/simulated_tracks.jsonl --detector src.crossing_detector:CrossingDetector --gate "56.1,26.10,56.1,26.80"
```

Real data:
```bash
python3 -m src.pipeline --detector src.crossing_detector:CrossingDetector --gate "56.1,26.10,56.1,26.80"
```

### End-to-end test record — 2026-09-27 05:19 UTC, `main` @ `9cef077`

Each dataset was run **twice against the same fresh database**; row counts were taken directly from SQLite after each run.

| | Simulated (`demo/simulated_tracks.jsonl`) | Real (VesselAPI, replay) |
|---|---|---|
| Input | 21 positions from 3 vessels | 683 records → 266 positions from 14 vessels (405 duplicates, 12 `suspected_glitch` dropped), 26/9 21:55 → 27/9 05:10 UTC |
| Run 1: detected / newly stored | 2 / 2 | 1 / 1 |
| Run 2: detected / newly stored / duplicate | 2 / 0 / 2 | 1 / 0 / 1 |
| `vessels` table after run 1 → run 2 | 3 → 3 rows (3 MMSI) | 14 → 14 rows (14 MMSI) |
| `crossings` table after run 1 → run 2 | 2 → 2 rows | 1 → 1 row |
| Crossings | 999000001 `INBOUND` 22:20Z lat 26.4; 999000002 `OUTBOUND` 22:20Z lat 26.6; anchored vessel 999000003 not counted | AL- NOOR (616002462) `OUTBOUND` 03:33:26Z, gate intersection lat 26.1929 |
| `source` / `ship_type` | `simulated` / `NULL` | `vesselapi-replay` / `NULL` |

Conclusions:
- **Database stores correctly:** the `vessels` table holds the correct latest position for all 14/14 real vessels (checked against the position with the greatest time per MMSI); `crossing_time` is stored in `Z` form; unknown vessel type stays `NULL`.
- **Deduplication works:** re-running adds no rows to either table; the detector still detects, but `insert_crossing` returns `False` (duplicate key `mmsi, crossing_time, direction`).
- **Simulated:** exactly 1 crossing per direction.
- **No false crossings in real data:** a geometric check independent of the detector (two consecutive positions off the gate, ≤ 30 minutes apart, switching sides of lon 56.1 within lat 26.10–26.80) found exactly 1 case — AL- NOOR, matching the detector; no position lies exactly on the gate.
- AL- NOOR: 11 consecutive positions, ~7.3 knots, crosses lon 56.1 between 03:19 (56.084) and 03:33 (56.108).
- Tests: `python3 -m pytest -q` → 70 passed.

Note: if the database already has data from a previous run, "newly stored" may be 0 even though the detector detected a crossing — that's deduplication, not a bug.

## 4. Known bugs and open items

**Detector side (owned by Person B — A does not fix these):**
1. ✅ Fixed in PR #3: a position lying exactly on the gate no longer causes a missed crossing; touching the gate and returning to the same side creates no event. Tests cover both directions and the touch-and-return case.
   - ✅ PR #4–#5: added tests for multiple consecutive positions on the gate (→ 1 crossing) and a 45-minute gap > `max_gap_minutes` (→ 0 crossings); numeric MMSI. These tests FAIL on the pre-PR #3 detector, so they catch the original bug.
2. ✅ Fixed in PR #2: direction is `INBOUND` (longitude decreasing) / `OUTBOUND` (longitude increasing).
3. ✅ Fixed in PR #2–#3: tests live in `tests/test_crossing_detector.py`, `ship_type=None`, numeric MMSI (`999000001`…).
4. **Temporary gate** lon 56.1, lat 26.10–26.80: the old ends (26.0–26.09 and 26.81–26.9) were on land according to a ~1 km land/sea map, so they were trimmed. No official Hormuz gate coordinates yet.

**Data side (A):**
5. Real data only covers the west coast of Musandam; the main lanes in the middle of the strait are missing. As of 27/9 05:10 UTC there is only **1 real crossing** — a multi-crossing demo still needs labeled simulated data.
6. `ship_type` is always `NULL` with the current source.
7. Free plan: 150 calls/month; the recorder stops itself when ≤ 20 remain.
8. The API key was once pasted into a chat → create a new key before the demo.
