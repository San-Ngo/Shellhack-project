# Shellhack-project — HORMUZ WATCH

HACKATHON PROJECT — HORMUZ MARITIME MARKET INTELLIGENCE

Data part (Person A): AIS source → normalization → gate-crossing detector (Person B) → SQLite.

## 6-hour foundation status

| Step | Status |
|---|---|
| HOUR 0–1: choose source, configuration, data interface | ✅ `docs/ais_source.md`, `docs/data_contract.md` |
| HOUR 1–2: receive AIS | ✅ aisstream.io: 0 messages in Hormuz → **VesselAPI: real data** |
| HOUR 2–3.5: normalization (`src/normalizer.py`) | ✅ |
| HOUR 3.5–4.5: SQLite (`src/database.py`) | ✅ tables `vessels`, `crossings` |
| HOUR 4.5–5: integrate with Person B (`src/pipeline.py`) | ✅ runs with B's `src/crossing_detector.py` |
| HOUR 5–6: end-to-end check, handover | ✅ 70 tests (now 125); **1 real crossing**; handover: `docs/handover.md` |

## Installation

```bash
python3 -m pip install -r requirements.txt
```

On Mac use `python3`, not `python`.

## Create `.env`

```bash
cp .env.example .env
```
```bash
open -e .env
```

Paste your key into `VESSELAPI_KEY=` (create one at https://dashboard.vesselapi.com/). `.env` is in `.gitignore`. **Never paste the key into chat, code or GitHub.**

## Record real data (VesselAPI)

Single test snapshot:
```bash
python3 -m src.vesselapi_recorder --once
```

Record continuously, every 30 minutes, max 36 calls:
```bash
caffeinate -i python3 -m src.vesselapi_recorder --interval 1800 --max-calls 36
```

- Each call returns positions from the **last 2 hours**, newest first. Each page ≤ 50 positions = 1 call.
- The recorder stops itself when `--max-calls` is used up or when the monthly quota is ≤ 20.
- Data: `samples/vesselapi_snapshots.jsonl`. Log and errors: `samples/recorder.log`. Neither is **committed** (VesselAPI terms).

Try fetching historical data (1 call, 4-hour window):
```bash
python3 scripts/probe_history.py --days-ago 7
```

## Replay into SQLite

**Real** data, vessels only:
```bash
python3 -m src.pipeline
```

**Real** data + Person B's detector:
```bash
python3 -m src.pipeline --detector src.crossing_detector:CrossingDetector --gate "56.1,26.10,56.1,26.80"
```

Detector demo with **simulated** data (automatically sets `source = simulated`):
```bash
python3 -m src.pipeline --snapshots demo/simulated_tracks.jsonl --detector src.crossing_detector:CrossingDetector --gate "56.1,26.10,56.1,26.80"
```

- SQLite has 3 tables: `vessels` (latest position per vessel), `vessel_positions` (full tracks), `crossings` (crossings). Re-running creates no duplicate rows.
- Gate order is **longitude, latitude**. The gate above is a **temporary gate** proposed by Person B.
- Results are saved to `hormuz_watch.db` (not committed).

## Azure SQL + Power BI (continuously updated)

VesselAPI → normalization → Person B's detector → Azure SQL (`vessels_latest`, `crossings`, `vessel_metadata`, `brent_daily`, `ingestion_status`) → Power BI Azure Maps. Full guide: `docs/azure_setup.md`.

```bash
python3 -m src.azure_store --check
```
```bash
caffeinate -i python3 -m src.live_ingest --interval 1800
```

Not yet tested against a real Azure SQL — see the limits in `docs/azure_setup.md`.

## Tests

```bash
python3 -m pytest -q
```

Expected result: `125 passed`.

## Real vs simulated data

- **Real (VesselAPI):** 26/9 21:55 → 27/9 05:10 UTC → 266 positions from 14 vessels; 7 days earlier (20/9 03:05–05:04 UTC) → 44 positions from 9 vessels. Stored only in `samples/` on the recording machine.
- **Simulated:** `demo/simulated_tracks.jsonl` (MMSI `99900…`, names `SIM-…`, `real_data: false`) and every record in `tests/`. `tests/stub_detector.py` is only a stub for testing the pipeline.
- **First real crossing:** the vessel AL- NOOR (MMSI 616002462) crossed the temporary gate at 27/9 03:33 UTC, direction `OUTBOUND` (11 consecutive positions, ~7.3 knots, no glitch flags). The crossings in `demo/` are simulated.

## Known limits

- **Coverage:** VesselAPI (free plan, terrestrial stations) only has vessels along the west coast of Musandam (lat 26.0–26.8, mostly lon 56.00–56.21). The main lanes in the middle of the strait have **no data**, both today and 7 days ago. Satellite AIS (`filter.sat=true`) costs extra and isn't used yet.
- **Quota:** 150 calls/month. Filter box total `|dLat| + |dLon|` ≤ 4°. Each time query ≤ 4 hours.
- **No vessel type:** the endpoint in use doesn't return vessel type → `ship_type` is always `NULL`. No guessing; `NULL` ≠ "not a tanker".
- **Data quality:** some vessels report 12 knots while their position stays still (NAUTILUS I, 25 identical positions). `suspected_glitch` records are dropped.
- **aisstream.io:** connection and key valid but 0 messages in Hormuz (2 attempts, 26/9). `src/ais_listener.py` is kept but unused.
- **Detector (Person B):** direction `INBOUND` (longitude decreasing) / `OUTBOUND` (longitude increasing). The missed-crossing bug when a position lies exactly on the gate was fixed in PR #3, with regression tests (PR #4–#5). End-to-end test record: `docs/handover.md` section 3.
- **API key:** never entered Git (full history scanned), but was once pasted into a chat → create a new key.

## Docs

- `docs/ais_source.md` — AIS source, verified fields, test log, coverage
- `docs/data_contract.md` — vessel position and crossing objects shared with Person B
- `docs/azure_setup.md` — Azure SQL, scheduled job, Power BI
- `docs/handover.md` — handover to Person B: functions, structure, how to run, open issues
