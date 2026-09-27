-- HORMUZ WATCH — Azure SQL Database schema (T-SQL).
-- Safe to run many times: tables are only created if missing; views are CREATE OR ALTER.
-- Run with:  python3 -m src.azure_store --init
-- or paste into Azure Portal > SQL database > Query editor (batches are separated by GO).
--
-- Power BI / Azure Maps: use the columns `latitude` and `longitude` (decimal degrees, WGS84).
-- In Power BI set Data category = Latitude / Longitude on those columns.

IF OBJECT_ID(N'dbo.vessels_latest', N'U') IS NULL
CREATE TABLE dbo.vessels_latest (
    mmsi               VARCHAR(9)     NOT NULL PRIMARY KEY,   -- 9-digit string, never a number
    ship_name          NVARCHAR(100)  NULL,
    ship_type          INT            NULL,                   -- AIS type code; NULL = unknown (never guessed)
    latitude           DECIMAL(9,6)   NOT NULL CHECK (latitude  BETWEEN -90  AND 90),
    longitude          DECIMAL(9,6)   NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    speed_knots        FLOAT          NULL,
    course_deg         FLOAT          NULL,
    position_time_utc  DATETIME2(0)   NOT NULL,               -- AIS time from the source (UTC)
    source             VARCHAR(40)    NOT NULL,               -- vesselapi-live | vesselapi-replay | simulated
    updated_at_utc     DATETIME2(0)   NOT NULL DEFAULT SYSUTCDATETIME()
);
GO

IF OBJECT_ID(N'dbo.vessel_metadata', N'U') IS NULL
CREATE TABLE dbo.vessel_metadata (
    mmsi               VARCHAR(9)     NOT NULL PRIMARY KEY,
    imo                VARCHAR(10)    NULL,
    ship_name          NVARCHAR(100)  NULL,
    ship_type          INT            NULL,                   -- NULL = unknown; NOT "not a tanker"
    first_seen_utc     DATETIME2(0)   NOT NULL,
    last_seen_utc      DATETIME2(0)   NOT NULL,
    source             VARCHAR(40)    NOT NULL
);
GO

IF OBJECT_ID(N'dbo.crossings', N'U') IS NULL
CREATE TABLE dbo.crossings (
    crossing_id        INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    mmsi               VARCHAR(9)     NOT NULL,
    ship_name          NVARCHAR(100)  NULL,
    ship_type          INT            NULL,
    crossing_time_utc  DATETIME2(0)   NOT NULL,
    direction          VARCHAR(10)    NOT NULL,               -- INBOUND | OUTBOUND | UNKNOWN (Person B)
    latitude           DECIMAL(9,6)   NULL,                   -- gate intersection computed by the detector
    longitude          DECIMAL(9,6)   NULL,
    confidence         VARCHAR(10)    NOT NULL CHECK (confidence IN ('CONFIRMED', 'UNCERTAIN')),
    gap_minutes        DECIMAL(8,1)   NULL,                   -- time since the vessel's previous position
    source             VARCHAR(40)    NOT NULL,
    detected_at_utc    DATETIME2(0)   NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT uq_crossings_event UNIQUE (mmsi, crossing_time_utc, direction)  -- re-sending adds nothing
);
GO

IF OBJECT_ID(N'dbo.brent_daily', N'U') IS NULL
CREATE TABLE dbo.brent_daily (
    price_date         DATE           NOT NULL PRIMARY KEY,
    price_usd          DECIMAL(10,2)  NOT NULL,               -- USD per barrel
    source             VARCHAR(60)    NOT NULL,
    loaded_at_utc      DATETIME2(0)   NOT NULL DEFAULT SYSUTCDATETIME()
);
GO

IF OBJECT_ID(N'dbo.ingestion_status', N'U') IS NULL
CREATE TABLE dbo.ingestion_status (
    source_name          VARCHAR(40)    NOT NULL PRIMARY KEY, -- vesselapi | fred-brent
    status               VARCHAR(20)    NOT NULL,             -- OK | ERROR | AUTH_ERROR | QUOTA_EXHAUSTED
    last_attempt_utc     DATETIME2(0)   NOT NULL,
    last_success_utc     DATETIME2(0)   NULL,                 -- kept when a later attempt fails
    last_data_time_utc   DATETIME2(0)   NULL,                 -- newest data time written so far
    rows_last_success    INT            NULL,
    quota_remaining      INT            NULL,
    consecutive_failures INT            NOT NULL DEFAULT 0,
    last_error           NVARCHAR(1000) NULL
);
GO

-- One row per vessel, ready for the Azure Maps visual.
CREATE OR ALTER VIEW dbo.v_map_vessels AS
SELECT
    v.mmsi,
    COALESCE(v.ship_name, m.ship_name)                             AS ship_name,
    m.imo,
    v.ship_type,
    CASE WHEN v.ship_type IS NULL THEN 'UNKNOWN'
         ELSE CAST(v.ship_type AS VARCHAR(10)) END                 AS ship_type_label,
    v.latitude,
    v.longitude,
    v.speed_knots,
    v.course_deg,
    v.position_time_utc,
    DATEDIFF(MINUTE, v.position_time_utc, SYSUTCDATETIME())        AS minutes_since_position,
    CASE WHEN DATEDIFF(MINUTE, v.position_time_utc, SYSUTCDATETIME()) > 60
         THEN 1 ELSE 0 END                                         AS is_stale,
    CASE WHEN v.source = 'simulated' THEN 'SIMULATED' ELSE 'REAL' END AS data_kind,
    v.source
FROM dbo.vessels_latest AS v
LEFT JOIN dbo.vessel_metadata AS m ON m.mmsi = v.mmsi;
GO

-- Crossings with a readable label; UNCERTAIN = the two positions were far apart in time.
CREATE OR ALTER VIEW dbo.v_map_crossings AS
SELECT
    c.crossing_id,
    c.mmsi,
    c.ship_name,
    c.ship_type,
    c.crossing_time_utc,
    c.direction,
    c.latitude,
    c.longitude,
    c.confidence,
    c.gap_minutes,
    CASE WHEN c.source = 'simulated' THEN 'SIMULATED' ELSE 'REAL' END AS data_kind,
    c.source
FROM dbo.crossings AS c;
GO
