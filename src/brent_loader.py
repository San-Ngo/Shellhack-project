"""Daily Brent spot price -> Azure SQL table brent_daily.

Source: FRED series DCOILBRENTEU "Crude Oil Prices: Brent - Europe", daily, USD per barrel,
original source U.S. Energy Information Administration (EIA). Official CSV download, no key:
    https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILBRENTEU&cosd=YYYY-MM-DD
Citation (FRED): U.S. Energy Information Administration, Crude Oil Prices: Brent - Europe
[DCOILBRENTEU], retrieved from FRED, Federal Reserve Bank of St. Louis.

Days without a price (weekends, holidays, not yet published) are skipped — never filled in.

    python3 -m src.brent_loader --days 400
"""
import argparse
import csv
import io
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

from src.config import ROOT

SERIES = "DCOILBRENTEU"
SOURCE_LABEL = "FRED:DCOILBRENTEU (EIA)"
STATUS_NAME = "fred-brent"
CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


def parse_fred_csv(text: str) -> list:
    """FRED CSV -> [{'price_date': 'YYYY-MM-DD', 'price_usd': float}], oldest first.

    Accepts both header styles (DATE / observation_date) and both missing-value styles
    ('.' or empty). Rows that are not a real date + positive number are skipped.
    """
    rows = []
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    header = next(reader, None)
    if not header or len(header) < 2 or SERIES not in [h.strip() for h in header]:
        raise ValueError(f"CSV không đúng dạng FRED {SERIES}: header={header!r}")
    col = [h.strip() for h in header].index(SERIES)
    for row in reader:
        if len(row) <= col:
            continue
        raw_date, raw_value = row[0].strip(), row[col].strip()
        try:
            d = date.fromisoformat(raw_date)
            value = float(raw_value)
        except ValueError:
            continue          # '.', '' or junk -> no price that day
        if value <= 0:
            continue
        rows.append({"price_date": d.isoformat(), "price_usd": round(value, 2)})
    rows.sort(key=lambda r: r["price_date"])
    return rows


def fetch_fred(days: int) -> list:
    start = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
    url = f"{CSV_URL}?{urllib.parse.urlencode({'id': SERIES, 'cosd': start})}"
    req = urllib.request.Request(url, headers={"User-Agent": "hormuz-watch-hackathon/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return parse_fred_csv(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"FRED HTTP {e.code}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"FRED lỗi mạng: {e.reason}")


def refresh(store, days: int = 400) -> dict:
    """Fetch and upsert. On failure old prices stay and ingestion_status says why."""
    try:
        rows = fetch_fred(days)
        if not rows:
            raise RuntimeError("FRED không trả về giá nào")
        n = store.upsert_brent(rows, SOURCE_LABEL)
        latest = rows[-1]["price_date"] + "T00:00:00Z"
        store.record_success(STATUS_NAME, rows=n, data_time=latest)
        return {"ok": True, "rows": n, "latest": rows[-1]}
    except Exception as e:
        try:
            store.record_failure(STATUS_NAME, "ERROR", str(e))
        except Exception:
            pass
        return {"ok": False, "error": str(e)}


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from src.azure_store import AzureConfigError, AzureStore

    p = argparse.ArgumentParser(description="Nạp giá Brent hằng ngày (FRED) vào Azure SQL.")
    p.add_argument("--days", type=int, default=400, help="số ngày gần nhất cần nạp")
    args = p.parse_args()
    try:
        store = AzureStore.from_env()
    except (AzureConfigError, ConnectionError) as e:
        print(f"LỖI: {e}")
        return 1
    try:
        store.init_schema()
        out = refresh(store, args.days)
    finally:
        store.close()
    if out["ok"]:
        print(f"Brent: nạp {out['rows']} ngày; mới nhất {out['latest']['price_date']} = "
              f"{out['latest']['price_usd']} USD/thùng (nguồn {SOURCE_LABEL})")
        return 0
    print(f"LỖI Brent: {out['error']} → giữ giá cũ, đã ghi trạng thái lỗi.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
