"""GIỜ 2–3.5: turn aisstream.io messages into the shared vessel-position object.

Only field names verified in the official docs/schema are read (see docs/ais_source.md):
  Message.PositionReport: UserID, Latitude, Longitude, Sog, Cog, Valid
  Message.ShipStaticData: UserID, Name, Type
  MetaData:               MMSI, ShipName

timestamp_utc: the full-time field in MetaData is not documented and has not been
seen in a live message yet, so it is NOT read. The caller passes `received_at`
(when our listener received the message, timezone-aware). Switch to the source
time once a real message confirms the field name.

Conventions agreed with Person B (docs/data_contract.md):
  - mmsi is a 9-character string
  - ship_name / ship_type / speed_knots / course_deg are None when unknown
  - ship_type is the raw AIS integer code; never guessed. None != "not a tanker".
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

POSITION_TYPES = {"PositionReport"}
STATIC_TYPES = {"ShipStaticData"}

# "Not available" values defined by the AIS standard (ITU-R M.1371)
LAT_NOT_AVAILABLE = 91.0
LON_NOT_AVAILABLE = 181.0
SOG_NOT_AVAILABLE = 102.3
COG_NOT_AVAILABLE = 360.0
TYPE_NOT_AVAILABLE = 0


@dataclass
class Result:
    kind: str                      # "position" | "static" | "skip"
    data: Optional[dict] = None
    reason: Optional[str] = None   # why a message was skipped


@dataclass
class StaticCache:
    """Ship name/type from ShipStaticData, keyed by MMSI, merged into later positions."""
    by_mmsi: dict = field(default_factory=dict)

    def update(self, mmsi: str, ship_name: Optional[str], ship_type: Optional[int]) -> None:
        entry = self.by_mmsi.setdefault(mmsi, {"ship_name": None, "ship_type": None})
        if ship_name is not None:
            entry["ship_name"] = ship_name
        if ship_type is not None:
            entry["ship_type"] = ship_type

    def get(self, mmsi: str) -> dict:
        return self.by_mmsi.get(mmsi, {"ship_name": None, "ship_type": None})


# ---------- small helpers ----------

def _mmsi(value) -> Optional[str]:
    """Return a 9-digit string, or None if missing/invalid."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text.isdigit():
        return None
    if int(text) <= 0 or len(text.lstrip("0")) > 9:
        return None
    return text.zfill(9)


def _clean_name(value) -> Optional[str]:
    if not isinstance(value, str):
        return None
    name = value.replace("@", " ").strip()  # AIS pads unused characters with '@'
    return name or None


def _number(value) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _utc_iso(ts: Optional[datetime]) -> Optional[str]:
    if not isinstance(ts, datetime) or ts.tzinfo is None or ts.utcoffset() is None:
        return None
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------- main entry point ----------

def normalize(msg: dict, received_at: Optional[datetime], cache: StaticCache) -> Result:
    """Normalize one decoded aisstream.io message. Never raises on bad input."""
    if not isinstance(msg, dict):
        return Result("skip", reason="not a JSON object")

    mtype = msg.get("MessageType")
    body = (msg.get("Message") or {}).get(mtype) if isinstance(msg.get("Message"), dict) else None
    meta = msg.get("MetaData") if isinstance(msg.get("MetaData"), dict) else {}

    if mtype in STATIC_TYPES:
        return _static(body, meta, cache)
    if mtype not in POSITION_TYPES:
        return Result("skip", reason=f"message type {mtype!r} is not a position report")
    if not isinstance(body, dict):
        return Result("skip", reason=f"{mtype} has no Message.{mtype} body")

    mmsi = _mmsi(body.get("UserID", meta.get("MMSI")))
    if mmsi is None:
        return Result("skip", reason="missing or invalid MMSI")

    if body.get("Valid") is False:
        return Result("skip", reason=f"MMSI {mmsi}: source marked message as not valid")

    lat, lon = _number(body.get("Latitude")), _number(body.get("Longitude"))
    if lat is None or lon is None:
        return Result("skip", reason=f"MMSI {mmsi}: missing latitude/longitude")
    if lat == LAT_NOT_AVAILABLE or lon == LON_NOT_AVAILABLE:
        return Result("skip", reason=f"MMSI {mmsi}: position not available (91/181)")
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        return Result("skip", reason=f"MMSI {mmsi}: coordinates out of range ({lat}, {lon})")

    timestamp = _utc_iso(received_at)
    if timestamp is None:
        return Result("skip", reason=f"MMSI {mmsi}: missing or timezone-naive timestamp")

    sog, cog = _number(body.get("Sog")), _number(body.get("Cog"))
    if sog is not None and (sog >= SOG_NOT_AVAILABLE or sog < 0):
        sog = None
    if cog is not None and (cog >= COG_NOT_AVAILABLE or cog < 0):
        cog = None

    known = cache.get(mmsi)
    ship_name = known["ship_name"] or _clean_name(meta.get("ShipName"))

    return Result("position", data={
        "mmsi": mmsi,
        "ship_name": ship_name,
        "ship_type": known["ship_type"],
        "latitude": lat,
        "longitude": lon,
        "speed_knots": sog,
        "course_deg": cog,
        "timestamp_utc": timestamp,
        "source_message_type": mtype,
    })


def _static(body, meta: dict, cache: StaticCache) -> Result:
    if not isinstance(body, dict):
        return Result("skip", reason="ShipStaticData has no Message.ShipStaticData body")
    mmsi = _mmsi(body.get("UserID", meta.get("MMSI")))
    if mmsi is None:
        return Result("skip", reason="ShipStaticData: missing or invalid MMSI")

    ship_name = _clean_name(body.get("Name")) or _clean_name(meta.get("ShipName"))
    raw_type = body.get("Type")
    ship_type = raw_type if isinstance(raw_type, int) and not isinstance(raw_type, bool) \
        and raw_type != TYPE_NOT_AVAILABLE else None

    cache.update(mmsi, ship_name, ship_type)
    return Result("static", data={"mmsi": mmsi, "ship_name": ship_name, "ship_type": ship_type})


# ---------- VesselAPI (REST bounding-box) ----------
# Field names verified from a real response on 2026-09-26 (docs/ais_source.md):
# mmsi, vessel_name, latitude, longitude, timestamp, sog, cog, suspected_glitch, ...
# This endpoint has no vessel-type field, so ship_type stays None unless the cache has it.

VESSELAPI_SOURCE = "VesselAPI.bounding-box"


def _parse_iso_utc(value) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        ts = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo is not None else None


def normalize_vesselapi(rec: dict, cache: Optional[StaticCache] = None) -> Result:
    """Normalize one record from VesselAPI's `vessels` list. Never raises on bad input."""
    if not isinstance(rec, dict):
        return Result("skip", reason="not a JSON object")

    mmsi = _mmsi(rec.get("mmsi"))
    if mmsi is None:
        return Result("skip", reason="missing or invalid MMSI")
    if rec.get("suspected_glitch") is True:
        return Result("skip", reason=f"MMSI {mmsi}: source flagged suspected_glitch")

    lat, lon = _number(rec.get("latitude")), _number(rec.get("longitude"))
    if lat is None or lon is None:
        return Result("skip", reason=f"MMSI {mmsi}: missing latitude/longitude")
    if lat == LAT_NOT_AVAILABLE or lon == LON_NOT_AVAILABLE:
        return Result("skip", reason=f"MMSI {mmsi}: position not available (91/181)")
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        return Result("skip", reason=f"MMSI {mmsi}: coordinates out of range ({lat}, {lon})")

    timestamp = _utc_iso(_parse_iso_utc(rec.get("timestamp")))
    if timestamp is None:
        return Result("skip", reason=f"MMSI {mmsi}: missing or timezone-naive timestamp")

    sog, cog = _number(rec.get("sog")), _number(rec.get("cog"))
    if sog is not None and (sog >= SOG_NOT_AVAILABLE or sog < 0):
        sog = None
    if cog is not None and (cog >= COG_NOT_AVAILABLE or cog < 0):
        cog = None

    known = (cache or StaticCache()).get(mmsi)
    return Result("position", data={
        "mmsi": mmsi,
        "ship_name": _clean_name(rec.get("vessel_name")) or known["ship_name"],
        "ship_type": known["ship_type"],
        "latitude": lat,
        "longitude": lon,
        "speed_knots": sog,
        "course_deg": cog,
        "timestamp_utc": timestamp,
        "source_message_type": VESSELAPI_SOURCE,
    })
