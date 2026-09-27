# AIS source — HOUR 0–1 decision

Checked on 2026-09-26 against the official documentation: https://aisstream.io/documentation
and the official data model: https://github.com/aisstream/ais-message-models (type-definition.yaml)

## Decision: use aisstream.io

Reasons: public documentation, coordinate filtering, both position and static vessel messages, usable over WebSocket from Python, and an official Python example. No web scraping needed.

## Verified from the documentation

| Item | Result |
|---|---|
| Connection | WebSocket `wss://stream.aisstream.io/v0/stream`, with `permessage-deflate` compression enabled |
| API key | Required. Created at aisstream.io/account; a new key is shown only once |
| Hormuz filter | Yes — `BoundingBoxes` (required), each corner is `[lat, lon]` |
| Other filters | `FiltersShipMMSI` (max 200, 9-character strings), `FilterMessageTypes` |
| Position messages | `PositionReport` (Class A); also `StandardClassBPositionReport`, `ExtendedClassBPositionReport` |
| Static vessel messages | `ShipStaticData` (has `Name`, `Type`, `ImoNumber`, `CallSign`, `Destination`…) |
| Message framing | Binary frames containing UTF-8 JSON → must be decoded before parsing |

## Verified data fields

Message envelope: `MessageType`, `MetaData`, `Message.<MessageType>`

`Message.PositionReport`: `UserID` (MMSI, integer), `Latitude`, `Longitude`, `Sog` (knots), `Cog` (degrees), `TrueHeading`, `NavigationalStatus`, `Valid`, `Timestamp` (**only the UTC second 0–59**, not a full time)

`Message.ShipStaticData`: `UserID`, `Name`, `Type` (integer AIS code), `ImoNumber`, `CallSign`, `Destination`, `Dimension`, `Eta`, `MaximumStaticDraught`

`MetaData` (example in the docs): `MMSI`, `ShipName`, `Latitude`, `Longitude`

## NOT yet verified — check with real messages in HOUR 1–2

- **Full time field in `MetaData`**: the official schema doesn't list `MetaData` fields, and the docs example has no time field. Exact name unknown → don't guess. Print a real message to see it.
- Whether the Hormuz area actually has messages (terrestrial receiver coverage may be sparse).
- Actual update frequency: AIS is event-driven, not fixed-interval.

## Connection test log (San's MacBook)

| Time (UTC) | Filter box [lat, lon] | Wait | Result |
|---|---|---|---|
| 2026-09-26 23:04:50 → 23:06:50 | [27.5, 55.0] – [25.0, 58.0] (Hormuz) | 120 s | Key valid, subscription confirmed, compression on. **0 messages.** |
| 2026-09-26 23:08:24 → 23:13:24 | [30.5, 47.5] – [22.0, 60.5] (Persian Gulf + Gulf of Oman) | 300 s | Key valid, subscription confirmed. **0 messages.** |

Interim conclusion: **no real AIS data received for this region yet.** The listener hasn't been tested in a busy area, so a code-side bug isn't fully ruled out; however, the connection, key and subscription were all confirmed by the server.

Consequence: the next steps use **simulated messages** (marked `SIMULATED`) only to test the code. `timestamp_utc` temporarily uses **the time the listener received the message** (UTC), rather than guessing the name of the time field in `MetaData`.

## Candidate source: VesselAPI — REAL DATA RECEIVED

Why we tried it: another Hormuz project also got 0 messages in the Gulf from aisstream.io (their feed is mostly European terrestrial stations).

- Docs: https://vesselapi.com/docs — REST `GET https://api.vesselapi.com/v1/location/vessels/bounding-box`, header `Authorization: Bearer <key>`
- Parameters: `filter.latBottom`, `filter.latTop`, `filter.lonLeft`, `filter.lonRight`, `pagination.limit` (≤ 50), `pagination.nextToken`
- **Box limit (from a real 400 error): `|dLat| + |dLon|` ≤ 4 degrees.** Test box: lat 25.5–27.0, lon 55.5–57.5 (= 3.5)

**Real call: 2026-09-26 ~23:40 UTC, San's machine, free plan**
- HTTP 200, **50 position records** on the first page, with a next page (`nextToken`) — these are positions, not vessel count (see below)
- Header `X-RateLimit-Remaining: 150` after the call — unclear whether this is a monthly quota or a 5-minute window → **must check on the Subscription page**
- Full response saved to `samples/vesselapi_probe.json` (not committed)

**Verified fields in each `vessels[]` record:**
`mmsi` (integer), `imo`, `vessel_name`, `latitude`, `longitude`, `location` (GeoJSON Point), `timestamp` (ISO UTC with `Z` — AIS time), `processed_timestamp`, `sog`, `cog`, `heading`, `nav_status`, `suspected_glitch`

**Differences from aisstream.io:**
- REST polling, not WebSocket → quota is spent per call and per page
- **No vessel type field** in this endpoint → `ship_type` will be `null` (no guessing)
- **Has a source `timestamp`** → replaces the temporary receive time
- Has `suspected_glitch` → can be used to drop suspect positions

**The endpoint returns POSITION HISTORY, not 1 position per vessel:** without `time.from/time.to` it defaults to the **last 2 hours**. Analysis of the 23:53 UTC snapshot (box lat 26.0–26.9, lon 56.0–56.9): **97 records = 8 vessels**, 2–23 positions each, ~5 minutes apart; the oldest position was 118 minutes before the call. Each page (≤ 50 records) = 1 call.

**Recording:** started 2026-09-26 23:53 UTC. Initially a snapshot every 10 minutes (~90% overlap) → changed to **every 110 minutes** (2-hour window, 10-minute overlap to avoid gaps) ≈ 1 call/hour. Duplicate records (mmsi, timestamp) are filtered on replay. The `X-RateLimit-Remaining` header drops by 2 per snapshot (148 → 146) → this is the **monthly quota** (Subscription page: 150 calls/month).

## Limits and risks

- Max 3 subscribed connections per account; 3 open connections per IP.
- The subscription must be sent within **3 seconds** of opening the connection, or it is closed.
- Subscription updates at most once per second; an update replaces (does not merge) the previous config.
- Must read continuously; slow readers get messages dropped by the server.
- From September 2026: uncompressed connections are bandwidth-limited → enable compression.
- **No SLA**, no replay of missed events → must reconnect ourselves (backoff + jitter).
- Must not connect directly from a browser; the key stays server-side.
- Detailed terms of use: not read yet — review before any public demo.

## Historical data (checked 2026-09-27 03:54 UTC)

- `time.from` / `time.to` (RFC3339, max 4 hours per request) **can fetch data from 7 days ago**: window 2026-09-20 03:00–07:00 UTC → 44 positions from 9 vessels (03:05–05:04 UTC), 1 page, 1 call.
- **Coverage area identical to today:** lat 26.01–26.30, lon 56.00–56.21 (the strip along the west coast of Musandam). No positions in the eastern part of the box (lon 56.21–56.9), where the main shipping lanes run.
- Vessels do move (4–5 km) but mostly north–south along the coast; **0 crossings of the temporary gate at lon 56.1 (lat 26.10–26.80)**.
- Conclusion: two points in time 7 days apart show the same coverage → most likely a **receiver coverage limit of the source (terrestrial stations)**, not a timing issue. Pulling more historical data is unlikely to produce real strait crossings. VesselAPI has a satellite option (`filter.sat=true`, paid with satellite credits) — not used yet.

## First real gate crossing (checked 2026-09-27 04:21 UTC)

- Data recorded 26/9 21:55 → 27/9 04:09 UTC: 483 records → 226 positions from 14 vessels; 5 dropped for `suspected_glitch`.
- Person B's detector (temporary gate lon 56.1, lat 26.10–26.80) → **1 crossing**: AL- NOOR (MMSI 616002462), 03:33:26 UTC, `OUTBOUND`, crossing the gate at lat 26.193.
- Reliability check: 11 consecutive positions 02:47–04:01 UTC, steady speed ~7.3 knots, heading northeast (cog 40–58°), no glitch flags; crosses lon 56.1 between 03:19 (56.084) and 03:33 (56.108).
- `ship_type` still `NULL` (the source has no vessel type) → unknown whether it is a tanker.
