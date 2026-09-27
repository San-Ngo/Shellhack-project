"""All REAL VesselAPI data kept in samples/ (git-ignored), plus the time it actually covers.

Sources:
    samples/vesselapi_snapshots.jsonl     recorder + live job (each answer = last 2 h, or its window)
    samples/vesselapi_history.jsonl       sampled past windows (scripts/fetch_history.py)
    samples/vesselapi_history_probe.json  one 4-hour window 7 days back (scripts/probe_history.py)

Coverage matters: a day with no vessel in our files may simply be a day we never looked at,
so every answer is turned into the time interval it covers.
"""
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from src.config import ROOT
from src.normalizer import normalize_vesselapi

SNAPSHOTS = ROOT / "samples" / "vesselapi_snapshots.jsonl"
HISTORY = ROOT / "samples" / "vesselapi_history.jsonl"
PROBE = ROOT / "samples" / "vesselapi_history_probe.json"
DEFAULT_LOOKBACK = timedelta(hours=2)   # VesselAPI window when no time.from / time.to is sent


def parse_time(value) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else None


def _lines(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _interval(start, end, vessels, truncated: bool, source: str) -> dict:
    """Time covered by one answer. Pages come newest first, so a truncated answer
    only covers from its oldest record onwards."""
    if truncated:
        times = sorted(t for t in (parse_time(v.get("timestamp")) for v in vessels
                                   if isinstance(v, dict)) if t)
        if times:
            start = max(start, times[0])
    return {"start": start, "end": end, "source": source}


def read_sources(snapshots: Path = SNAPSHOTS, history: Path = HISTORY, probe: Path = PROBE) -> tuple:
    """-> (records, intervals, failures)

    records: [(raw VesselAPI record, source)], source = recorded | history-sample
    intervals: merged [{start, end, sources}] actually looked at
    failures: history windows that returned an error
    """
    records, intervals, failures = [], [], []
    for snap in _lines(snapshots):
        if snap.get("real_data") is not True:
            continue
        vessels = snap.get("vessels") or []
        end = parse_time(snap.get("time_to")) or parse_time(snap.get("fetched_at"))
        start = parse_time(snap.get("time_from")) or (end - DEFAULT_LOOKBACK if end else None)
        if start and end:
            intervals.append(_interval(start, end, vessels, bool(snap.get("truncated")), "recorded"))
        records += [(v, "recorded") for v in vessels]

    for h in _lines(history):
        start, end = parse_time(h.get("window_from")), parse_time(h.get("window_to"))
        if h.get("status") != 200:
            failures.append({"start": start, "end": end, "status": h.get("status"), "error": h.get("error")})
            continue
        vessels = h.get("vessels") or []
        if start and end:
            intervals.append(_interval(start, end, vessels, bool(h.get("truncated")), "history-sample"))
        records += [(v, "history-sample") for v in vessels]

    if probe.exists():
        try:
            d = json.loads(probe.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            d = {}
        resp = d.get("response") or {}
        vessels = resp.get("vessels") or []
        start, end = parse_time(d.get("requested_from")), parse_time(d.get("requested_to"))
        if start and end and d.get("real_data") is True:
            intervals.append(_interval(start, end, vessels, bool(resp.get("nextToken")), "history-sample"))
            records += [(v, "history-sample") for v in vessels]
    return records, merge(intervals), failures


def merge(intervals: list) -> list:
    out = []
    for iv in sorted((i for i in intervals if i["start"] < i["end"]), key=lambda i: i["start"]):
        if out and iv["start"] <= out[-1]["end"]:
            out[-1]["end"] = max(out[-1]["end"], iv["end"])
            out[-1]["sources"].add(iv["source"])
        else:
            out.append({"start": iv["start"], "end": iv["end"], "sources": {iv["source"]}})
    return out


def covered_minutes(intervals: list, start: datetime, end: datetime) -> float:
    total = 0.0
    for iv in intervals:
        s, e = max(iv["start"], start), min(iv["end"], end)
        if e > s:
            total += (e - s).total_seconds() / 60
    return total


def day_bounds(day: date) -> tuple:
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def day_coverage(intervals: list, day: date) -> list:
    """Intervals clipped to one UTC day: [(start, end, sources)]."""
    d0, d1 = day_bounds(day)
    out = []
    for iv in intervals:
        s, e = max(iv["start"], d0), min(iv["end"], d1)
        if e > s:
            out.append((s, e, iv["sources"]))
    return out


def normalize_all(records: list) -> tuple:
    """Normalize, drop invalid and duplicate (mmsi, timestamp). -> (positions sorted by time, skipped)."""
    seen, skipped = {}, 0
    for rec, source in records:
        result = normalize_vesselapi(rec)
        if result.kind != "position":
            skipped += 1
            continue
        pos = result.data
        seen.setdefault((pos["mmsi"], pos["timestamp_utc"]), dict(pos, source=source))
    positions = sorted(seen.values(), key=lambda p: (p["timestamp_utc"], p["mmsi"]))
    return positions, skipped
