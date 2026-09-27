"""Tests for src/live_ingest.py with a fake Azure store and a fake API.

Every vessel here is SIMULATED (MMSI 99900xxxx). Records use real VesselAPI field names.
Person B's real CrossingDetector is used; gate = temporary gate lon 56.1, lat 26.10-26.80.
"""
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("shapely")

from src import live_ingest as li  # noqa: E402

GATE = [(56.1, 26.10), (56.1, 26.80)]
DET = "src.crossing_detector:CrossingDetector"
T0 = datetime(2026, 9, 27, 6, 0, tzinfo=timezone.utc)


def rec(mmsi, minutes, lon, lat=26.4, **extra):
    r = {"mmsi": mmsi, "imo": 9000001, "vessel_name": f"SIM {mmsi}", "latitude": lat, "longitude": lon,
         "timestamp": (T0 + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "sog": 10.0, "cog": 270.0, "suspected_glitch": False}
    r.update(extra)
    return r


class FakeStore:
    """Mimics the SQL semantics of AzureStore (MERGE newer-only, UNIQUE crossings, status rows)."""

    def __init__(self, fail_writes=0):
        self.vessels, self.meta, self.crossings, self.status = {}, {}, {}, {}
        self.fail_writes = fail_writes
        self.write_calls = 0

    def write_batch(self, latest, metadata, crossings, source):
        self.write_calls += 1
        if self.fail_writes:
            self.fail_writes -= 1
            raise ConnectionError("simulated database outage")
        stored = dup = 0
        for p in latest:
            old = self.vessels.get(p["mmsi"])
            if old is None or p["timestamp_utc"] >= old["timestamp_utc"]:
                self.vessels[p["mmsi"]] = dict(p, source=source)
        for m in metadata:
            self.meta[m["mmsi"]] = m
        for c in crossings:
            key = (c["mmsi"], li._iso(datetime.fromisoformat(c["crossing_time"])), c["direction"])
            if key in self.crossings:
                dup += 1
            else:
                self.crossings[key] = dict(c, source=source)
                stored += 1
        return {"vessels": len(latest), "metadata": len(metadata),
                "crossings_stored": stored, "crossings_duplicate": dup}

    def record_success(self, source_name, rows, data_time=None, quota=None):
        old = self.status.get(source_name, {})
        self.status[source_name] = {"status": "OK", "last_success": "now", "rows": rows,
                                    "data_time": data_time or old.get("data_time"),
                                    "quota": quota if quota is not None else old.get("quota"),
                                    "failures": 0, "error": None}

    def record_failure(self, source_name, status, error, quota=None):
        old = self.status.get(source_name, {"last_success": None, "data_time": None, "failures": 0})
        self.status[source_name] = dict(old, status=status, error=error,
                                        failures=old.get("failures", 0) + 1,
                                        quota=quota if quota is not None else old.get("quota"))

    def load_latest_positions(self):
        return list(self.vessels.values())


def fetcher(*batches, quota=100):
    """Each call returns the next batch of records (or raises it, if it is an exception)."""
    batches = list(batches)

    def fetch(t0, t1):
        item = batches.pop(0)
        if isinstance(item, Exception):
            raise item
        return item, quota, 1, False
    return fetch


def new_state(seed=()):
    return li.build_state(DET, GATE, 360, list(seed))


# ---------- normal cycle ----------

def test_cycle_overwrites_latest_position_per_mmsi_and_records_success():
    store, state = FakeStore(), new_state()
    fetch = fetcher([rec(999000001, 0, 56.30), rec(999000001, 5, 56.29), rec(999000002, 3, 56.5)])
    state, out = li.run_cycle(fetch, store, state)
    assert out["ok"] and out["status"] == "OK"
    assert set(store.vessels) == {"999000001", "999000002"}
    assert store.vessels["999000001"]["longitude"] == 56.29            # newest of the two
    assert store.vessels["999000001"]["source"] == "vesselapi-live"
    assert store.meta["999000001"]["imo"] == 9000001
    assert store.meta["999000001"]["ship_type"] is None                # never guessed
    st = store.status["vesselapi"]
    assert st["status"] == "OK" and st["data_time"] == "2026-09-27T06:05:00Z" and st["quota"] == 100


def test_invalid_records_are_skipped_not_invented():
    store, state = FakeStore(), new_state()
    fetch = fetcher([rec(None, 0, 56.3), rec(999000003, 0, 56.3, suspected_glitch=True),
                     rec(999000004, 0, 56.3, lat=95), rec(999000005, 0, 56.3)])
    li.run_cycle(fetch, store, state)
    assert set(store.vessels) == {"999000005"}


# ---------- API errors / quota ----------

@pytest.mark.parametrize("error, status, stop", [
    (li.FetchError("HTTP 503: down"), "ERROR", False),
    (li.AuthError("HTTP 401: bad key"), "AUTH_ERROR", True),
    (li.QuotaExhausted("HTTP 429: limit"), "QUOTA_EXHAUSTED", True),
])
def test_api_error_keeps_old_data_and_updates_status(error, status, stop):
    store, state = FakeStore(), new_state()
    li.run_cycle(fetcher([rec(999000001, 0, 56.3)]), store, state)
    before = dict(store.vessels)
    state, out = li.run_cycle(fetcher(error), store, state)
    assert out == {"ok": False, "status": status, "stop": stop}
    assert store.vessels == before                                      # old position kept
    assert store.write_calls == 1                                       # nothing written this time
    st = store.status["vesselapi"]
    assert st["status"] == status and st["last_success"] == "now" and st["failures"] == 1
    assert st["data_time"] == "2026-09-27T06:00:00Z"                    # last good data time kept


def test_low_quota_writes_this_batch_then_stops():
    store, state = FakeStore(), new_state()
    state, out = li.run_cycle(fetcher([rec(999000001, 0, 56.3)], quota=20), store, state, reserve=20)
    assert out["ok"] and out["stop"] and out["status"] == "QUOTA_EXHAUSTED"
    assert "999000001" in store.vessels
    assert store.status["vesselapi"]["status"] == "QUOTA_EXHAUSTED"


def test_database_failure_rolls_state_back_so_nothing_is_lost():
    store, state = FakeStore(fail_writes=1), new_state()
    batch = [rec(999000001, 0, 56.12), rec(999000001, 10, 56.08)]       # crosses lon 56.1
    state, out = li.run_cycle(fetcher(batch), store, state)
    assert not out["ok"] and store.status["vesselapi"]["status"] == "ERROR"
    assert store.crossings == {} and store.vessels == {}
    state, out = li.run_cycle(fetcher(batch), store, state)             # same window again
    assert out["ok"] and len(store.crossings) == 1                      # crossing not lost


# ---------- crossings and time gaps ----------

def only_crossing(store):
    assert len(store.crossings) == 1
    return next(iter(store.crossings.values()))


def test_close_positions_give_confirmed_crossing_with_gap():
    store, state = FakeStore(), new_state()
    li.run_cycle(fetcher([rec(999000001, 0, 56.12), rec(999000001, 10, 56.08)]), store, state)
    c = only_crossing(store)
    assert c["confidence"] == "CONFIRMED" and c["direction"] == "INBOUND"
    assert c["gap_minutes"] == 10 and c["longitude"] == pytest.approx(56.1)


def test_crossing_between_two_cycles_is_detected():
    store, state = FakeStore(), new_state()
    state, _ = li.run_cycle(fetcher([rec(999000001, 0, 56.12)]), store, state)
    state, _ = li.run_cycle(fetcher([rec(999000001, 0, 56.12), rec(999000001, 15, 56.08)]), store, state)
    assert only_crossing(store)["confidence"] == "CONFIRMED"


def test_far_apart_positions_are_never_confirmed():
    store, state = FakeStore(), new_state()
    li.run_cycle(fetcher([rec(999000001, 0, 56.12), rec(999000001, 90, 56.08)]), store, state)
    c = only_crossing(store)
    assert c["confidence"] == "UNCERTAIN" and c["gap_minutes"] == 90


def test_gap_beyond_uncertain_limit_gives_no_crossing():
    store, state = FakeStore(), new_state()
    li.run_cycle(fetcher([rec(999000001, 0, 56.12), rec(999000001, 8 * 60, 56.08)]), store, state)
    assert store.crossings == {}


def test_overlapping_windows_do_not_duplicate_anything():
    store, state = FakeStore(), new_state()
    batch = [rec(999000001, 0, 56.12), rec(999000001, 10, 56.08)]
    state, _ = li.run_cycle(fetcher(batch), store, state)
    state, out = li.run_cycle(fetcher(batch), store, state)
    assert out["stats"]["new_positions"] == 0 and out["stats"]["already_processed"] == 2
    assert len(store.crossings) == 1


def test_restart_reseeds_detector_from_stored_positions():
    store = FakeStore()
    state = new_state()
    li.run_cycle(fetcher([rec(999000001, 0, 56.12)]), store, state)
    restarted = new_state(store.load_latest_positions())               # new process, same DB
    li.run_cycle(fetcher([rec(999000001, 0, 56.12), rec(999000001, 12, 56.08)]), store, restarted)
    assert only_crossing(store)["confidence"] == "CONFIRMED"


def test_request_window_starts_before_newest_and_never_exceeds_4_hours():
    state = new_state()
    now = datetime(2026, 9, 27, 7, 0, tzinfo=timezone.utc)
    start, end = li.request_window(state, now)
    assert end == now and start == now - timedelta(hours=2)            # nothing stored yet
    state.last_seen["999000001"] = "2026-09-27T06:30:00Z"
    start, _ = li.request_window(state, now)
    assert start == datetime(2026, 9, 27, 6, 15, tzinfo=timezone.utc)   # 15 min overlap
    state.last_seen["999000001"] = "2026-09-26T20:00:00Z"               # job was down for hours
    start, end = li.request_window(state, now)
    assert end - start < timedelta(hours=4)


def test_replay_of_simulated_file_is_labelled_simulated():
    store = FakeStore()
    out = li.replay_file(li.ROOT / "demo" / "simulated_tracks.jsonl", store, new_state())
    assert out["label"] == "simulated"
    assert {c["source"] for c in store.crossings.values()} == {"simulated"}
    assert {c["confidence"] for c in store.crossings.values()} == {"CONFIRMED"}
    assert len(store.crossings) == 2 and len(store.vessels) == 3
