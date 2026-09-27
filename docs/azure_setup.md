# Azure SQL + Power BI — continuously updated data

Flow: **VesselAPI → normalization → Person B's detector → Azure SQL Database → Power BI (Azure Maps)**.

Status (27/9/2026): code and logic tests done (106 tests, using a fake store). **Not yet run against a real Azure SQL** — the first check is `python3 -m src.azure_store --check` on a machine with the database.

## 1. Tables in Azure SQL (`sql/azure_schema.sql`)

| Table / view | Each row is | How it's written |
|---|---|---|
| `vessels_latest` | the **latest** position of 1 vessel | `MERGE` on `mmsi`; an older position never overwrites a newer one |
| `vessel_metadata` | name, IMO, vessel type, first/last seen | `MERGE` on `mmsi`; a missing name/IMO doesn't erase an existing value |
| `crossings` | 1 gate crossing | insert only; `UNIQUE (mmsi, crossing_time_utc, direction)` → resending creates no new row |
| `brent_daily` | Brent price for 1 day (USD/barrel) | `MERGE` on date; days without a price are left empty |
| `ingestion_status` | status of 1 source (`vesselapi`, `fred-brent`) | last attempt, last success, error, remaining quota |
| `v_map_vessels` | map view | adds `minutes_since_position`, `is_stale` (> 60 minutes), `data_kind` REAL/SIMULATED |
| `v_map_crossings` | crossings view | adds `data_kind` |

- **Coordinates for Azure Maps:** `latitude`, `longitude` columns — WGS84 decimal degrees, type `DECIMAL(9,6)`, taken as-is from AIS (no extra rounding, no interpolation).
- `crossings.latitude/longitude` is the **gate intersection point** computed by the detector, not an AIS position.
- `ship_type` is always `NULL` with VesselAPI (the endpoint has no vessel type). `NULL` = unknown, **not** "not a tanker".

## 2. Scheduled job rules (`src/live_ingest.py`)

- Each run: fetch positions **from the latest stored position − 15 minutes** up to now (max 4 hours, a VesselAPI limit) → fewer records, fewer calls.
- One transaction per write: a mid-way failure → nothing written, detector rolls back to its previous state, reprocessed next run.
- **API error / quota exhausted:** no positions written, old data kept, error recorded in `ingestion_status`:

| `status` | When | What the job does |
|---|---|---|
| `OK` | fetched and written successfully | continues |
| `ERROR` | network error, HTTP 5xx, database write error | retries next run |
| `AUTH_ERROR` | HTTP 401/403 (wrong key, no access) | stops |
| `QUOTA_EXHAUSTED` | HTTP 429 or remaining quota ≤ `--reserve` (20) | stops calling the API |

  `last_success_utc` and `last_data_time_utc` are **kept unchanged** on error → Power BI knows how stale the data is.
- **Large time gaps:** B's detector only joins 2 positions ≤ 30 minutes apart (`max_gap_minutes`) → `confidence = CONFIRMED`. A second detector instance (same code from B, 360-minute limit) catches side changes across longer gaps → `confidence = UNCERTAIN`, **never** CONFIRMED. Gaps > 360 minutes → nothing recorded. `gap_minutes` = time since the vessel's previous position.
- Restart: the detector is reloaded from `vessels_latest`, so crossings between two runs are still caught (if the gap is ≤ 30 minutes).
- A raw copy of each fetch is still saved to `samples/vesselapi_snapshots.jsonl` (not committed).
- Brent price: updated automatically every 6 hours inside the loop; Brent errors don't affect AIS.

## 3. Setup (San's machine)

1. Create an Azure SQL Database (Azure Portal) with **SQL authentication** (login + password).
2. Open the firewall: SQL server → *Networking* → *Add your client IPv4 address*. For Power BI Service: enable *Allow Azure services and resources to access this server*.
3. Install libraries:
   ```bash
   python3 -m pip install -r requirements.txt
   ```
4. Add 4 lines to `.env` (see `.env.example`): `AZURE_SQL_SERVER`, `AZURE_SQL_DATABASE`, `AZURE_SQL_USER`, `AZURE_SQL_PASSWORD`. **Never paste the password into chat, code or GitHub.**
5. Check the connection + create tables:
   ```bash
   python3 -m src.azure_store --check
   ```
6. (Optional) Load the real data already recorded, without using any API calls:
   ```bash
   python3 -m src.live_ingest --replay samples/vesselapi_snapshots.jsonl
   ```
7. Load Brent prices:
   ```bash
   python3 -m src.brent_loader --days 400
   ```
8. Stop the old recorder (Ctrl+C) — the new job replaces it and also uses the VesselAPI quota. Test run once:
   ```bash
   python3 -m src.live_ingest --once
   ```
9. Run continuously every 30 minutes:
   ```bash
   caffeinate -i python3 -m src.live_ingest --interval 1800
   ```

## 4. Power BI

1. *Get data* → *Azure SQL database* → server, database → choose **DirectQuery** (reads the database directly, no re-import needed).
2. Select `v_map_vessels`, `v_map_crossings`, `brent_daily`, `ingestion_status`.
3. `latitude` column → *Column tools* → *Data category* = **Latitude**; `longitude` → **Longitude**.
4. **Azure Maps** visual: *Latitude* = `latitude`, *Longitude* = `longitude`; *Tooltips*: `ship_name`, `mmsi`, `position_time_utc`, `minutes_since_position`, `data_kind`.
5. Crossings layer: add `v_map_crossings`, color by `confidence`.
6. Status cards: `ingestion_status.status`, `last_success_utc`, `last_data_time_utc`.
7. Auto refresh: *Format page* → *Page refresh*. On Power BI Service, the minimum interval depends on the capacity type — check when publishing.

## 5. Common errors

- `could not connect to Azure SQL`: check the firewall (step 2) and that the server name includes `.database.windows.net`. A paused serverless database needs ~1 minute to wake up — the code retries 3 times automatically.
- Login rejected despite the correct password: try `AZURE_SQL_USER=<login>@<short-server-name>`.
- `AUTH_ERROR` from VesselAPI: key wrong/expired → create a new key and update `.env`.

## 6. Known limits

- Not yet tested against a real Azure SQL (only T-SQL syntax checked with a parser, and logic tested with a fake store).
- VesselAPI only covers the west coast of Musandam; the main lanes have no data. The gate is still Person B's **temporary gate**.
- "Live" = latest as of a 30-minute cycle plus source latency, not real-time to the second.
- Brent (FRED/EIA) is a **daily** price, published a few business days late.
