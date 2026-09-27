"""Tests for src/azure_store.py and src/brent_loader.py that need no Azure connection.

The SQL itself runs only against a real Azure SQL Database (python3 -m src.azure_store --check).
Here: schema file content, validation of rows before SQL, time conversion, FRED CSV parsing.
All vessels are SIMULATED.
"""
import pytest

from src.azure_store import (SCHEMA_FILE, TABLES, crossing_params, iso_z, metadata_params,
                             schema_batches, sql_time, vessel_params)
from src.brent_loader import parse_fred_csv

POS = {"mmsi": "999000001", "ship_name": "SIM-1", "ship_type": None, "latitude": 26.23483,
       "longitude": 56.15544, "speed_knots": 7.3, "course_deg": None,
       "timestamp_utc": "2026-09-27T04:01:13Z"}


def test_schema_has_all_tables_views_and_map_columns():
    text = SCHEMA_FILE.read_text(encoding="utf-8")
    for name in TABLES:
        assert f"CREATE TABLE dbo.{name}" in text
    assert "CREATE OR ALTER VIEW dbo.v_map_vessels" in text
    assert "UNIQUE (mmsi, crossing_time_utc, direction)" in text
    assert "latitude           DECIMAL(9,6)" in text and "longitude          DECIMAL(9,6)" in text
    assert len(schema_batches(text)) == 7


def test_sql_time_is_utc_without_suffix_and_rejects_naive():
    assert sql_time("2026-09-27T04:01:13Z") == "2026-09-27T04:01:13"
    assert sql_time("2026-09-27T08:01:13+04:00") == "2026-09-27T04:01:13"
    assert sql_time("2026-09-27T04:01:13") is None
    assert iso_z("2026-09-27 04:01:13") == "2026-09-27T04:01:13Z"


def test_vessel_params_keep_mmsi_as_string_and_type_unknown():
    p = vessel_params(dict(POS, mmsi=999000001), "vesselapi-live")
    assert p["mmsi"] == "999000001" and p["ship_type"] is None
    assert p["position_time_utc"] == "2026-09-27T04:01:13"


@pytest.mark.parametrize("bad", [{"mmsi": None}, {"latitude": 95}, {"longitude": -181},
                                 {"timestamp_utc": "2026-09-27T04:01:13"}, {"ship_type": "Tanker"}])
def test_invalid_vessel_is_rejected_before_sql(bad):
    with pytest.raises(ValueError):
        vessel_params(dict(POS, **bad), "vesselapi-live")


def test_crossing_params_require_confidence_and_timezone():
    c = {"mmsi": "999000001", "crossing_time": "2026-09-27T03:33:26+00:00", "direction": "OUTBOUND",
         "latitude": 26.19, "longitude": 56.1, "confidence": "UNCERTAIN", "gap_minutes": 92.34}
    p = crossing_params(c, "vesselapi-live")
    assert p["crossing_time_utc"] == "2026-09-27T03:33:26" and p["gap_minutes"] == 92.3
    with pytest.raises(ValueError):
        crossing_params(dict(c, confidence="MAYBE"), "x")
    with pytest.raises(ValueError):
        crossing_params(dict(c, crossing_time="2026-09-27T03:33:26"), "x")


def test_metadata_imo_zero_is_unknown():
    m = {"mmsi": "999000001", "imo": 0, "first_seen_utc": "2026-09-27T03:00:00Z",
         "last_seen_utc": "2026-09-27T04:00:00Z"}
    assert metadata_params(m, "x")["imo"] is None
    assert metadata_params(dict(m, imo=9123456), "x")["imo"] == "9123456"


@pytest.mark.parametrize("header, missing", [("observation_date,DCOILBRENTEU", ""),
                                             ("DATE,DCOILBRENTEU", ".")])
def test_fred_csv_both_formats_skip_missing_days(header, missing):
    text = f"{header}\n2026-09-18,112.40\n2026-09-21,{missing}\n2026-09-22,114.89\n"
    assert parse_fred_csv(text) == [{"price_date": "2026-09-18", "price_usd": 112.4},
                                    {"price_date": "2026-09-22", "price_usd": 114.89}]


def test_fred_csv_wrong_series_is_an_error():
    with pytest.raises(ValueError):
        parse_fred_csv("DATE,DCOILWTICO\n2026-09-22,90\n")
