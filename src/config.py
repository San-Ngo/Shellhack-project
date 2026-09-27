"""Load settings from .env. Never hard-code the API key here."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

AIS_STREAM_URL = "wss://stream.aisstream.io/v0/stream"
PLACEHOLDER_KEYS = {"", "your_key_here"}


class ConfigError(Exception):
    """Raised when .env is missing or has invalid values."""


@dataclass(frozen=True)
class Settings:
    api_key: str
    bounding_box: list  # [[lat1, lon1], [lat2, lon2]]
    db_path: str


def _float_env(name: str) -> float:
    raw = os.getenv(name)
    if raw is None:
        raise ConfigError(f"Thiếu {name} trong .env (xem .env.example).")
    try:
        return float(raw)
    except ValueError:
        raise ConfigError(f"{name}={raw!r} không phải số.") from None


def load_settings() -> Settings:
    api_key = (os.getenv("AISSTREAM_API_KEY") or "").strip()
    if api_key in PLACEHOLDER_KEYS:
        raise ConfigError(
            "Chưa có AISSTREAM_API_KEY. Chạy `cp .env.example .env` rồi dán khóa "
            "từ https://aisstream.io/account vào .env."
        )

    lat1, lon1 = _float_env("BBOX_LAT_1"), _float_env("BBOX_LON_1")
    lat2, lon2 = _float_env("BBOX_LAT_2"), _float_env("BBOX_LON_2")
    for lat in (lat1, lat2):
        if not -90 <= lat <= 90:
            raise ConfigError(f"Vĩ độ khung lọc {lat} ngoài khoảng -90..90.")
    for lon in (lon1, lon2):
        if not -180 <= lon <= 180:
            raise ConfigError(f"Kinh độ khung lọc {lon} ngoài khoảng -180..180.")

    return Settings(
        api_key=api_key,
        bounding_box=[[lat1, lon1], [lat2, lon2]],
        db_path=os.getenv("DB_PATH", "hormuz_watch.db"),
    )
