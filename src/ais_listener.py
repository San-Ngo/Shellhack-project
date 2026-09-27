"""GIỜ 1–2: minimal AIS Stream listener.

Connects, subscribes to PositionReport + ShipStaticData inside the Hormuz box,
and prints a few real fields. No normalizing or database yet.

Run from the repo root:
    python -m src.ais_listener --max-messages 20 --timeout 120
    python -m src.ais_listener --save-raw   # also writes samples/raw_messages.jsonl
"""
import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.exceptions import (ConnectionClosed, InvalidProxyStatus, InvalidStatus,
                                   WebSocketException)

from src.config import AIS_STREAM_URL, ROOT, ConfigError, load_settings
from src.normalizer import StaticCache, normalize

MESSAGE_TYPES = ["PositionReport", "ShipStaticData"]
SUBSCRIBE_DEADLINE_S = 3  # service closes the socket if no subscription within 3 s


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(msg: str) -> None:
    print(f"[{now_utc()}] {msg}", flush=True)


def summarize(msg: dict) -> str:
    """One line of fields that really exist in the message (no guessing)."""
    mtype = msg.get("MessageType")
    body = (msg.get("Message") or {}).get(mtype) or {}
    meta = msg.get("MetaData") or {}
    if mtype == "PositionReport":
        return (f"PositionReport MMSI={body.get('UserID')} lat={body.get('Latitude')} "
                f"lon={body.get('Longitude')} sog={body.get('Sog')} cog={body.get('Cog')} "
                f"meta_keys={sorted(meta)}")
    if mtype == "ShipStaticData":
        return (f"ShipStaticData MMSI={body.get('UserID')} name={str(body.get('Name', '')).strip()!r} "
                f"type={body.get('Type')} meta_keys={sorted(meta)}")
    return f"{mtype} (not used yet)"


async def listen(max_messages: int, timeout_s: float, save_raw: bool) -> int:
    settings = load_settings()
    subscription = {
        "APIKey": settings.api_key,
        "BoundingBoxes": [settings.bounding_box],
        "FilterMessageTypes": MESSAGE_TYPES,
    }
    raw_file = None
    if save_raw:
        (ROOT / "samples").mkdir(exist_ok=True)
        raw_file = open(ROOT / "samples" / "raw_messages.jsonl", "a", encoding="utf-8")

    log(f"Kết nối {AIS_STREAM_URL} | box={settings.bounding_box} | types={MESSAGE_TYPES}")
    received = 0
    cache = StaticCache()
    try:
        async with connect(AIS_STREAM_URL, compression="deflate", open_timeout=15) as ws:
            await asyncio.wait_for(ws.send(json.dumps(subscription)), SUBSCRIBE_DEADLINE_S)
            log("Đã gửi đăng ký. Đang chờ bản tin...")
            while received < max_messages:
                try:
                    frame = await asyncio.wait_for(ws.recv(), timeout_s)
                except asyncio.TimeoutError:
                    log(f"KHÔNG CÓ BẢN TIN trong {timeout_s:.0f}s. Kết nối vẫn mở nhưng vùng lọc "
                        "có thể không có tàu phát/được thu. Chưa xác nhận có dữ liệu Hormuz.")
                    return 3
                text = frame.decode("utf-8") if isinstance(frame, bytes) else frame
                try:
                    msg = json.loads(text)
                except json.JSONDecodeError:
                    log(f"Bỏ qua frame không phải JSON: {text[:120]!r}")
                    continue
                if msg.get("MessageType") == "SubscriptionConfirmation":
                    log(f"Đăng ký được chấp nhận: {msg.get('Message')}")
                    continue
                if "error" in msg:  # defensive: surface any server error payload as-is
                    log(f"MÁY CHỦ BÁO LỖI: {msg}")
                    return 2
                received += 1
                log(f"#{received} {summarize(msg)}")
                result = normalize(msg, datetime.now(timezone.utc), cache)
                if result.kind == "skip":
                    log(f"    bỏ qua: {result.reason}")
                else:
                    log(f"    chuẩn hóa ({result.kind}): {json.dumps(result.data, ensure_ascii=False)}")
                if raw_file:
                    raw_file.write(text + "\n")
    except InvalidProxyStatus as e:
        log(f"PROXY CHẶN KẾT NỐI ({e}). Mạng hiện tại không cho ra aisstream.io — "
            "chạy trên máy/mạng khác.")
        return 2
    except InvalidStatus as e:
        log(f"LỖI BẮT TAY HTTP {e.response.status_code}: máy chủ từ chối kết nối.")
        return 2
    except ConnectionClosed as e:
        log(f"MẤT KẾT NỐI (code={e.rcvd.code if e.rcvd else None}, "
            f"reason={e.rcvd.reason if e.rcvd else ''!r}). Nguyên nhân thường gặp: khóa sai, "
            "đăng ký sai định dạng/trễ hơn 3s, vượt 3 kết nối, hoặc mạng.")
        return 2
    except (OSError, asyncio.TimeoutError, WebSocketException) as e:
        log(f"LỖI MẠNG/KẾT NỐI: {type(e).__name__}: {e}")
        return 2
    finally:
        if raw_file:
            raw_file.close()

    log(f"Xong: nhận {received} bản tin AIS THẬT từ aisstream.io.")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Minimal AIS Stream listener (Hormuz box).")
    p.add_argument("--max-messages", type=int, default=20)
    p.add_argument("--timeout", type=float, default=120, help="giây chờ mỗi bản tin")
    p.add_argument("--save-raw", action="store_true", help="lưu bản tin thô vào samples/")
    args = p.parse_args()
    try:
        code = asyncio.run(listen(args.max_messages, args.timeout, args.save_raw))
    except ConfigError as e:
        log(f"LỖI CẤU HÌNH: {e}")
        code = 1
    except KeyboardInterrupt:
        code = 0
    sys.exit(code)


if __name__ == "__main__":
    main()
