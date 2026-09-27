"""Azure SQL Database storage for the live pipeline (Power BI reads these tables).

Tables (sql/azure_schema.sql): vessels_latest, vessel_metadata, crossings, brent_daily,
ingestion_status, plus views v_map_vessels / v_map_crossings for the Azure Maps visual.

Connection settings come from .env only (never from code):
    AZURE_SQL_SERVER=<name>.database.windows.net
    AZURE_SQL_DATABASE=<database>
    AZURE_SQL_USER=<sql login>
    AZURE_SQL_PASSWORD=<password>

Check the connection and create the tables:
    python3 -m src.azure_store --check
"""
import argparse
import os
import re
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable, Optional

from src.config import ROOT
from src.normalizer import _mmsi, _parse_iso_utc

SCHEMA_FILE = ROOT / "sql" / "azure_schema.sql"
TABLES = ("vessels_latest", "vessel_metadata", "crossings", "brent_daily", "ingestion_status")
ENV_KEYS = ("AZURE_SQL_SERVER", "AZURE_SQL_DATABASE", "AZURE_SQL_USER", "AZURE_SQL_PASSWORD")


class AzureConfigError(Exception):
    """Missing or placeholder Azure SQL settings in .env."""


# ---------- value conversion (all times stored as UTC, no timezone suffix) ----------

def sql_time(value) -> Optional[str]:
    """Any ISO time with timezone (or aware datetime) -> 'YYYY-MM-DDTHH:MM:SS' in UTC.

    ISO 8601 with 'T' converts to DATETIME2 the same way whatever the server's language
    or date-format settings are. Returns None for missing or timezone-naive input.
    """
    if value is None:
        return None
    dt = value if isinstance(value, datetime) else _parse_iso_utc(value)
    if dt is None or dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def iso_z(value) -> Optional[str]:
    """DATETIME2 value read back from SQL (naive UTC datetime or string) -> ISO string with Z."""
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "").replace(" ", "T")[:19])
    return value.replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%SZ")


def _float(value) -> Optional[float]:
    return float(value) if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool) else None


def schema_batches(text: str) -> list:
    """Split a T-SQL script on 'GO' lines (GO is a client command, not SQL)."""
    return [b.strip() for b in re.split(r"(?im)^\s*GO\s*$", text) if b.strip()]


# ---------- SQL (pyformat parameters; values are never pasted into SQL) ----------

MERGE_VESSEL = """
MERGE dbo.vessels_latest WITH (HOLDLOCK) AS t
USING (SELECT CAST(%(mmsi)s AS VARCHAR(9)) AS mmsi,
              CAST(%(ship_name)s AS NVARCHAR(100)) AS ship_name,
              CAST(%(ship_type)s AS INT) AS ship_type,
              CAST(%(latitude)s AS DECIMAL(9,6)) AS latitude,
              CAST(%(longitude)s AS DECIMAL(9,6)) AS longitude,
              CAST(%(speed_knots)s AS FLOAT) AS speed_knots,
              CAST(%(course_deg)s AS FLOAT) AS course_deg,
              CAST(%(position_time_utc)s AS DATETIME2(0)) AS position_time_utc,
              CAST(%(source)s AS VARCHAR(40)) AS source) AS s
ON t.mmsi = s.mmsi
WHEN MATCHED AND s.position_time_utc >= t.position_time_utc THEN UPDATE SET
    ship_name = COALESCE(s.ship_name, t.ship_name),
    ship_type = COALESCE(s.ship_type, t.ship_type),
    latitude = s.latitude, longitude = s.longitude,
    speed_knots = s.speed_knots, course_deg = s.course_deg,
    position_time_utc = s.position_time_utc, source = s.source,
    updated_at_utc = SYSUTCDATETIME()
WHEN NOT MATCHED THEN INSERT
    (mmsi, ship_name, ship_type, latitude, longitude, speed_knots, course_deg, position_time_utc, source)
    VALUES (s.mmsi, s.ship_name, s.ship_type, s.latitude, s.longitude, s.speed_knots, s.course_deg,
            s.position_time_utc, s.source);
"""

MERGE_METADATA = """
MERGE dbo.vessel_metadata WITH (HOLDLOCK) AS t
USING (SELECT CAST(%(mmsi)s AS VARCHAR(9)) AS mmsi, CAST(%(imo)s AS VARCHAR(10)) AS imo,
              CAST(%(ship_name)s AS NVARCHAR(100)) AS ship_name, CAST(%(ship_type)s AS INT) AS ship_type,
              CAST(%(first_seen_utc)s AS DATETIME2(0)) AS first_seen_utc,
              CAST(%(last_seen_utc)s AS DATETIME2(0)) AS last_seen_utc,
              CAST(%(source)s AS VARCHAR(40)) AS source) AS s
ON t.mmsi = s.mmsi
WHEN MATCHED THEN UPDATE SET
    imo = COALESCE(s.imo, t.imo),
    ship_name = COALESCE(s.ship_name, t.ship_name),
    ship_type = COALESCE(s.ship_type, t.ship_type),
    first_seen_utc = CASE WHEN s.first_seen_utc < t.first_seen_utc THEN s.first_seen_utc ELSE t.first_seen_utc END,
    last_seen_utc  = CASE WHEN s.last_seen_utc  > t.last_seen_utc  THEN s.last_seen_utc  ELSE t.last_seen_utc  END,
    source = s.source
WHEN NOT MATCHED THEN INSERT (mmsi, imo, ship_name, ship_type, first_seen_utc, last_seen_utc, source)
    VALUES (s.mmsi, s.imo, s.ship_name, s.ship_type, s.first_seen_utc, s.last_seen_utc, s.source);
"""

INSERT_CROSSING = """
INSERT INTO dbo.crossings
    (mmsi, ship_name, ship_type, crossing_time_utc, direction, latitude, longitude,
     confidence, gap_minutes, source)
SELECT CAST(%(mmsi)s AS VARCHAR(9)), CAST(%(ship_name)s AS NVARCHAR(100)), CAST(%(ship_type)s AS INT),
       CAST(%(crossing_time_utc)s AS DATETIME2(0)), CAST(%(direction)s AS VARCHAR(10)),
       CAST(%(latitude)s AS DECIMAL(9,6)), CAST(%(longitude)s AS DECIMAL(9,6)),
       CAST(%(confidence)s AS VARCHAR(10)), CAST(%(gap_minutes)s AS DECIMAL(8,1)),
       CAST(%(source)s AS VARCHAR(40))
WHERE NOT EXISTS (
    SELECT 1 FROM dbo.crossings WITH (UPDLOCK, HOLDLOCK)
    WHERE mmsi = %(mmsi)s
      AND crossing_time_utc = CAST(%(crossing_time_utc)s AS DATETIME2(0))
      AND direction = %(direction)s);
"""

STATUS_SUCCESS = """
MERGE dbo.ingestion_status WITH (HOLDLOCK) AS t
USING (SELECT CAST(%(source_name)s AS VARCHAR(40)) AS source_name,
              CAST(%(data_time)s AS DATETIME2(0)) AS data_time,
              CAST(%(rows)s AS INT) AS rows_written, CAST(%(quota)s AS INT) AS quota) AS s
ON t.source_name = s.source_name
WHEN MATCHED THEN UPDATE SET
    status = 'OK', last_attempt_utc = SYSUTCDATETIME(), last_success_utc = SYSUTCDATETIME(),
    last_data_time_utc = CASE WHEN s.data_time IS NOT NULL
                               AND (t.last_data_time_utc IS NULL OR s.data_time > t.last_data_time_utc)
                              THEN s.data_time ELSE t.last_data_time_utc END,
    rows_last_success = s.rows_written,
    quota_remaining = COALESCE(s.quota, t.quota_remaining),
    consecutive_failures = 0, last_error = NULL
WHEN NOT MATCHED THEN INSERT
    (source_name, status, last_attempt_utc, last_success_utc, last_data_time_utc,
     rows_last_success, quota_remaining, consecutive_failures, last_error)
    VALUES (s.source_name, 'OK', SYSUTCDATETIME(), SYSUTCDATETIME(), s.data_time,
            s.rows_written, s.quota, 0, NULL);
"""

# Failure: last_success_utc and last_data_time_utc are left untouched (old data stays valid).
STATUS_FAILURE = """
MERGE dbo.ingestion_status WITH (HOLDLOCK) AS t
USING (SELECT CAST(%(source_name)s AS VARCHAR(40)) AS source_name,
              CAST(%(status)s AS VARCHAR(20)) AS status,
              CAST(%(error)s AS NVARCHAR(1000)) AS error, CAST(%(quota)s AS INT) AS quota) AS s
ON t.source_name = s.source_name
WHEN MATCHED THEN UPDATE SET
    status = s.status, last_attempt_utc = SYSUTCDATETIME(),
    quota_remaining = COALESCE(s.quota, t.quota_remaining),
    consecutive_failures = t.consecutive_failures + 1, last_error = s.error
WHEN NOT MATCHED THEN INSERT
    (source_name, status, last_attempt_utc, quota_remaining, consecutive_failures, last_error)
    VALUES (s.source_name, s.status, SYSUTCDATETIME(), s.quota, 1, s.error);
"""

MERGE_BRENT = """
MERGE dbo.brent_daily WITH (HOLDLOCK) AS t
USING (SELECT CAST(%(price_date)s AS DATE) AS price_date,
              CAST(%(price_usd)s AS DECIMAL(10,2)) AS price_usd,
              CAST(%(source)s AS VARCHAR(60)) AS source) AS s
ON t.price_date = s.price_date
WHEN MATCHED AND (t.price_usd <> s.price_usd OR t.source <> s.source) THEN UPDATE SET
    price_usd = s.price_usd, source = s.source, loaded_at_utc = SYSUTCDATETIME()
WHEN NOT MATCHED THEN INSERT (price_date, price_usd, source) VALUES (s.price_date, s.price_usd, s.source);
"""

SELECT_LATEST = """
SELECT mmsi, ship_name, ship_type, latitude, longitude, speed_knots, course_deg, position_time_utc, source
FROM dbo.vessels_latest ORDER BY position_time_utc;
"""


# ---------- store ----------

class AzureStore:
    """Thin wrapper around a DB-API connection (pymssql). One writer process at a time."""

    def __init__(self, conn):
        self.conn = conn

    @classmethod
    def from_env(cls, retries: int = 3, wait_seconds: int = 20) -> "AzureStore":
        settings = {k: (os.getenv(k) or "").strip() for k in ENV_KEYS}
        missing = [k for k, v in settings.items() if not v or v.startswith("your_")]
        if missing:
            raise AzureConfigError("thiếu trong .env: " + ", ".join(missing))
        import pymssql  # imported here so tests and other modules do not need the driver

        last_error = None
        for attempt in range(1, retries + 1):
            try:
                conn = pymssql.connect(server=settings["AZURE_SQL_SERVER"], port=1433,
                                       user=settings["AZURE_SQL_USER"],
                                       password=settings["AZURE_SQL_PASSWORD"],
                                       database=settings["AZURE_SQL_DATABASE"],
                                       login_timeout=60, timeout=120, autocommit=False)
                return cls(conn)
            except pymssql.Error as e:
                # A paused serverless database needs up to ~1 minute to resume: retry.
                last_error = e
                if attempt < retries:
                    time.sleep(wait_seconds)
        raise ConnectionError(f"không kết nối được Azure SQL sau {retries} lần: {last_error}")

    def close(self) -> None:
        self.conn.close()

    # --- schema ---
    def init_schema(self) -> None:
        cur = self.conn.cursor()
        for batch in schema_batches(SCHEMA_FILE.read_text(encoding="utf-8")):
            cur.execute(batch)
        self.conn.commit()

    def table_counts(self) -> dict:
        cur = self.conn.cursor()
        counts = {}
        for name in TABLES:   # fixed names from this module, not user input
            cur.execute(f"SELECT COUNT(*) FROM dbo.{name};")
            counts[name] = cur.fetchone()[0]
        return counts

    # --- reads ---
    def load_latest_positions(self) -> list:
        """vessels_latest as shared position objects (used to re-seed the detector after a restart)."""
        cur = self.conn.cursor()
        cur.execute(SELECT_LATEST)
        out = []
        for mmsi, name, stype, lat, lon, sog, cog, ts, source in cur.fetchall():
            out.append({"mmsi": mmsi, "ship_name": name, "ship_type": stype,
                        "latitude": float(lat), "longitude": float(lon),
                        "speed_knots": _float(sog), "course_deg": _float(cog),
                        "timestamp_utc": iso_z(ts), "source_message_type": source})
        return out

    # --- writes ---
    def write_batch(self, latest: Iterable[dict], metadata: Iterable[dict],
                    crossings: Iterable[dict], source: str) -> dict:
        """Write one ingestion cycle in ONE transaction: all or nothing.

        latest: newest position per MMSI; metadata: per-MMSI name/imo/first-last seen;
        crossings: detector events with 'confidence' and 'gap_minutes'.
        Returns counts; a crossing already stored is counted as duplicate, not an error.
        """
        stats = {"vessels": 0, "metadata": 0, "crossings_stored": 0, "crossings_duplicate": 0}
        cur = self.conn.cursor()
        try:
            for p in latest:
                cur.execute(MERGE_VESSEL, vessel_params(p, source))
                stats["vessels"] += 1
            for m in metadata:
                cur.execute(MERGE_METADATA, metadata_params(m, source))
                stats["metadata"] += 1
            for c in crossings:
                cur.execute(INSERT_CROSSING, crossing_params(c, source))
                if cur.rowcount == 1:
                    stats["crossings_stored"] += 1
                else:
                    stats["crossings_duplicate"] += 1
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return stats

    def record_success(self, source_name: str, rows: int, data_time=None, quota=None) -> None:
        self.conn.cursor().execute(STATUS_SUCCESS, {
            "source_name": source_name, "data_time": sql_time(data_time) if data_time else None,
            "rows": int(rows), "quota": quota})
        self.conn.commit()

    def record_failure(self, source_name: str, status: str, error: str, quota=None) -> None:
        self.conn.cursor().execute(STATUS_FAILURE, {
            "source_name": source_name, "status": status, "error": str(error)[:1000], "quota": quota})
        self.conn.commit()

    def upsert_brent(self, rows: Iterable[dict], source: str) -> int:
        params = [{"price_date": r["price_date"], "price_usd": r["price_usd"], "source": source}
                  for r in rows]
        if not params:
            return 0
        cur = self.conn.cursor()
        try:
            cur.executemany(MERGE_BRENT, params)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return len(params)


# ---------- row builders (validation happens here, before any SQL runs) ----------

def _require(mmsi) -> str:
    key = _mmsi(mmsi)
    if key is None:
        raise ValueError(f"missing or invalid mmsi: {mmsi!r}")
    return key


def _coord(value, low, high, name) -> float:
    v = _float(value)
    if v is None or not low <= v <= high:
        raise ValueError(f"{name} out of range or missing: {value!r}")
    return v


def _type(value) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"ship_type must be an AIS integer code or None, got {value!r}")
    return value


def vessel_params(p: dict, source: str) -> dict:
    ts = sql_time(p.get("timestamp_utc"))
    if ts is None:
        raise ValueError(f"timestamp_utc must have a timezone: {p.get('timestamp_utc')!r}")
    return {"mmsi": _require(p.get("mmsi")), "ship_name": p.get("ship_name"),
            "ship_type": _type(p.get("ship_type")),
            "latitude": _coord(p.get("latitude"), -90, 90, "latitude"),
            "longitude": _coord(p.get("longitude"), -180, 180, "longitude"),
            "speed_knots": _float(p.get("speed_knots")), "course_deg": _float(p.get("course_deg")),
            "position_time_utc": ts, "source": source}


def metadata_params(m: dict, source: str) -> dict:
    first, last = sql_time(m.get("first_seen_utc")), sql_time(m.get("last_seen_utc"))
    if first is None or last is None:
        raise ValueError("first_seen_utc / last_seen_utc must have a timezone")
    imo = m.get("imo")
    imo = str(imo) if isinstance(imo, int) and not isinstance(imo, bool) and imo > 0 else None
    return {"mmsi": _require(m.get("mmsi")), "imo": imo, "ship_name": m.get("ship_name"),
            "ship_type": _type(m.get("ship_type")), "first_seen_utc": first, "last_seen_utc": last,
            "source": source}


def crossing_params(c: dict, source: str) -> dict:
    when = sql_time(c.get("crossing_time"))
    if when is None:
        raise ValueError(f"crossing_time must have a timezone: {c.get('crossing_time')!r}")
    direction = c.get("direction")
    if not isinstance(direction, str) or not direction.strip():
        raise ValueError(f"direction is required, got {direction!r}")
    confidence = c.get("confidence")
    if confidence not in ("CONFIRMED", "UNCERTAIN"):
        raise ValueError(f"confidence must be CONFIRMED or UNCERTAIN, got {confidence!r}")
    lat, lon = _float(c.get("latitude")), _float(c.get("longitude"))
    if lat is not None and not -90 <= lat <= 90 or lon is not None and not -180 <= lon <= 180:
        raise ValueError(f"crossing coordinates out of range: ({lat}, {lon})")
    gap = _float(c.get("gap_minutes"))
    return {"mmsi": _require(c.get("mmsi")), "ship_name": c.get("ship_name"),
            "ship_type": _type(c.get("ship_type")), "crossing_time_utc": when,
            "direction": direction.strip(), "latitude": lat, "longitude": lon,
            "confidence": confidence, "gap_minutes": round(gap, 1) if gap is not None else None,
            "source": source}


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    p = argparse.ArgumentParser(description="Kiểm tra kết nối Azure SQL và tạo bảng.")
    p.add_argument("--check", action="store_true", help="kết nối, tạo bảng nếu thiếu, in số dòng")
    args = p.parse_args()
    if not args.check:
        p.print_help()
        return 0
    try:
        store = AzureStore.from_env()
    except (AzureConfigError, ConnectionError) as e:
        print(f"LỖI: {e}")
        return 1
    try:
        cur = store.conn.cursor()
        cur.execute("SELECT DB_NAME(), SYSUTCDATETIME();")
        db, now = cur.fetchone()
        print(f"Kết nối OK: database {db}, giờ máy chủ (UTC) {now}")
        store.init_schema()
        print("Bảng và view đã sẵn sàng (tạo nếu thiếu).")
        for name, n in store.table_counts().items():
            print(f"  {name}: {n} dòng")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
