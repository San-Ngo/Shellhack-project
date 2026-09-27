"""SQLite storage: latest vessel state, position history (tracks) and gate crossings.

Tables:
    vessels           latest position per MMSI (1 row per vessel)
    vessel_positions  every position (track history), 1 row per (mmsi, timestamp_utc)
    crossings         gate crossings from Person B's detector
All SQL uses parameters — input is never pasted into SQL strings.

Usage (Person A pipeline and Person B detector):
    from src.database import init_db, upsert_vessel, insert_crossing, get_recent_crossings
    conn = init_db()                         # uses DB_PATH from .env, or pass a path / ":memory:"
    upsert_vessel(conn, position)            # position = shared vessel object (docs/data_contract.md)
    insert_position(conn, position, source="vesselapi-replay")   # track history
    get_track(conn, "616002462")             # one vessel's positions, oldest first
    insert_crossing(conn, crossing)          # crossing = object returned by Person B's detector
    get_recent_crossings(conn, limit=20)
"""
import os
import sqlite3
from typing import Optional

from src.config import ROOT
from src.normalizer import _mmsi, _number, _parse_iso_utc, _utc_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS vessels (
    mmsi             TEXT PRIMARY KEY,
    ship_name        TEXT,
    ship_type        INTEGER,          -- raw AIS type code; NULL = unknown (never guessed)
    latitude         REAL NOT NULL,
    longitude        REAL NOT NULL,
    speed_knots      REAL,
    course_deg       REAL,
    last_update_utc  TEXT NOT NULL,    -- ISO 8601 UTC, e.g. 2026-09-26T23:39:33Z
    source           TEXT
);

CREATE TABLE IF NOT EXISTS crossings (
    crossing_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    mmsi             TEXT NOT NULL,
    ship_name        TEXT,
    ship_type        INTEGER,
    crossing_time    TEXT NOT NULL,    -- ISO 8601 UTC
    direction        TEXT NOT NULL,
    latitude         REAL,             -- gate intersection or nearest position, if known
    longitude        REAL,
    source           TEXT,             -- e.g. "vesselapi-live", "vesselapi-replay", "simulated"
    note             TEXT,
    recorded_at_utc  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (mmsi, crossing_time, direction)   -- re-sending the same event is ignored
);

CREATE INDEX IF NOT EXISTS idx_crossings_time ON crossings (crossing_time);

CREATE TABLE IF NOT EXISTS vessel_positions (
    position_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    mmsi             TEXT NOT NULL,
    ship_name        TEXT,
    ship_type        INTEGER,          -- NULL = unknown (never guessed)
    latitude         REAL NOT NULL,
    longitude        REAL NOT NULL,
    speed_knots      REAL,
    course_deg       REAL,
    timestamp_utc    TEXT NOT NULL,    -- ISO 8601 UTC from the source
    source           TEXT,             -- "vesselapi-replay" (real) or "simulated"
    UNIQUE (mmsi, timestamp_utc)       -- replaying the same snapshot adds nothing
);

CREATE INDEX IF NOT EXISTS idx_positions_time ON vessel_positions (timestamp_utc);
"""


def init_db(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Open (or create) the database and make sure both tables exist."""
    path = db_path or os.getenv("DB_PATH", "hormuz_watch.db")
    if path != ":memory:" and not os.path.isabs(path):
        path = str(ROOT / path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# ---------- validation ----------

def _require_mmsi(obj: dict) -> str:
    mmsi = _mmsi(obj.get("mmsi"))
    if mmsi is None:
        raise ValueError(f"missing or invalid mmsi: {obj.get('mmsi')!r}")
    return mmsi


def _require_time(value, field: str) -> str:
    ts = _utc_iso(_parse_iso_utc(value))
    if ts is None:
        raise ValueError(f"{field} must be an ISO 8601 time with timezone, got {value!r}")
    return ts


def _coords(obj: dict, required: bool):
    lat, lon = _number(obj.get("latitude")), _number(obj.get("longitude"))
    if lat is None or lon is None:
        if required:
            raise ValueError("latitude and longitude are required")
        return None, None
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError(f"coordinates out of range: ({lat}, {lon})")
    return lat, lon


def _optional_type(value) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"ship_type must be an AIS integer code or None, got {value!r}")
    return value


# ---------- writes ----------

def upsert_vessel(conn: sqlite3.Connection, vessel: dict) -> None:
    """Insert or update the latest state of one vessel.

    - Same MMSI updates the existing row (no duplicates).
    - An OLDER position never overwrites a newer one (safe for replays/snapshots).
    - A missing name/type does not erase a name/type already known.
    """
    mmsi = _require_mmsi(vessel)
    lat, lon = _coords(vessel, required=True)
    ts = _require_time(vessel.get("timestamp_utc"), "timestamp_utc")
    conn.execute(
        """
        INSERT INTO vessels (mmsi, ship_name, ship_type, latitude, longitude,
                             speed_knots, course_deg, last_update_utc, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(mmsi) DO UPDATE SET
            ship_name   = COALESCE(excluded.ship_name, vessels.ship_name),
            ship_type   = COALESCE(excluded.ship_type, vessels.ship_type),
            latitude    = CASE WHEN excluded.last_update_utc >= vessels.last_update_utc
                               THEN excluded.latitude ELSE vessels.latitude END,
            longitude   = CASE WHEN excluded.last_update_utc >= vessels.last_update_utc
                               THEN excluded.longitude ELSE vessels.longitude END,
            speed_knots = CASE WHEN excluded.last_update_utc >= vessels.last_update_utc
                               THEN excluded.speed_knots ELSE vessels.speed_knots END,
            course_deg  = CASE WHEN excluded.last_update_utc >= vessels.last_update_utc
                               THEN excluded.course_deg ELSE vessels.course_deg END,
            source      = CASE WHEN excluded.last_update_utc >= vessels.last_update_utc
                               THEN excluded.source ELSE vessels.source END,
            last_update_utc = MAX(excluded.last_update_utc, vessels.last_update_utc)
        """,
        (mmsi, vessel.get("ship_name"), _optional_type(vessel.get("ship_type")), lat, lon,
         _number(vessel.get("speed_knots")), _number(vessel.get("course_deg")), ts,
         vessel.get("source_message_type")),
    )
    conn.commit()


def insert_crossing(conn: sqlite3.Connection, crossing: dict,
                    source: Optional[str] = None, note: Optional[str] = None) -> bool:
    """Store one crossing from Person B's detector.

    Required: mmsi, crossing_time (ISO with timezone), direction (non-empty text).
    Optional: latitude, longitude, ship_name, ship_type.
    Returns True if stored, False if the same (mmsi, crossing_time, direction) already exists.
    Raises ValueError for invalid events, so bad data never reaches the table.
    """
    mmsi = _require_mmsi(crossing)
    when = _require_time(crossing.get("crossing_time"), "crossing_time")
    direction = crossing.get("direction")
    if not isinstance(direction, str) or not direction.strip():
        raise ValueError(f"direction is required, got {direction!r}")
    lat, lon = _coords(crossing, required=False)

    cur = conn.execute(
        """
        INSERT OR IGNORE INTO crossings
            (mmsi, ship_name, ship_type, crossing_time, direction, latitude, longitude, source, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (mmsi, crossing.get("ship_name"), _optional_type(crossing.get("ship_type")), when,
         direction.strip(), lat, lon, source or crossing.get("source"), note or crossing.get("note")),
    )
    conn.commit()
    return cur.rowcount == 1


def insert_position(conn: sqlite3.Connection, position: dict, source: Optional[str] = None) -> bool:
    """Store one position in the track history (vessel_positions).

    Same validation as upsert_vessel. Returns True if stored, False if the same
    (mmsi, timestamp_utc) is already there. Raises ValueError for invalid positions.
    """
    mmsi = _require_mmsi(position)
    lat, lon = _coords(position, required=True)
    ts = _require_time(position.get("timestamp_utc"), "timestamp_utc")
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO vessel_positions
            (mmsi, ship_name, ship_type, latitude, longitude, speed_knots, course_deg,
             timestamp_utc, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (mmsi, position.get("ship_name"), _optional_type(position.get("ship_type")), lat, lon,
         _number(position.get("speed_knots")), _number(position.get("course_deg")), ts, source),
    )
    conn.commit()
    return cur.rowcount == 1


# ---------- reads ----------

def get_vessel(conn: sqlite3.Connection, mmsi) -> Optional[dict]:
    key = _mmsi(mmsi)
    if key is None:
        return None
    row = conn.execute("SELECT * FROM vessels WHERE mmsi = ?", (key,)).fetchone()
    return dict(row) if row else None


def get_recent_crossings(conn: sqlite3.Connection, limit: int = 20) -> list:
    limit = max(1, min(int(limit), 1000))
    rows = conn.execute(
        "SELECT * FROM crossings ORDER BY crossing_time DESC, crossing_id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_track(conn: sqlite3.Connection, mmsi) -> list:
    """All stored positions of one vessel, oldest first (for drawing its track)."""
    key = _mmsi(mmsi)
    if key is None:
        return []
    rows = conn.execute(
        "SELECT * FROM vessel_positions WHERE mmsi = ? ORDER BY timestamp_utc", (key,)
    ).fetchall()
    return [dict(r) for r in rows]
