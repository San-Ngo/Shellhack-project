"""Daily transit counts through the Strait of Hormuz from IMF PortWatch (no key needed).

Dataset: "Daily Chokepoint Transit Calls and Trade Volume Estimates" (IMF PortWatch, built from
satellite AIS). Counts only — no vessel names or positions. Published weekly with a lag of
several days, so the most recent days are usually missing.
    https://portwatch.imf.org/datasets/42132aa4e2fc4d41bdaf9a445f688931_0/about

    python3 scripts/fetch_portwatch.py --days 30
"""
import argparse
import csv
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
URL = ("https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
       "Daily_Chokepoints_Data/FeatureServer/0/query")
OUT = ROOT / "samples" / "portwatch_hormuz_daily.csv"
COUNT_FIELDS = ["n_total", "n_tanker", "n_container", "n_dry_bulk", "n_general_cargo", "n_roro", "n_cargo"]
CAPACITY_FIELDS = ["capacity_tanker", "capacity"]   # PortWatch transit trade-volume estimates (tonnes)
FIELDS = COUNT_FIELDS + CAPACITY_FIELDS


def parse_features(data: dict) -> list:
    """ArcGIS JSON -> [{'date': 'YYYY-MM-DD', 'n_total': .., 'n_tanker': .., ...}], oldest first."""
    if not isinstance(data, dict) or "features" not in data:
        raise ValueError(f"PortWatch trả lỗi: {str(data)[:300]}")
    rows = []
    for feat in data["features"]:
        a = feat.get("attributes") or {}
        try:
            d = date(int(a["year"]), int(a["month"]), int(a["day"]))
        except (KeyError, TypeError, ValueError):
            raw = a.get("date")
            if isinstance(raw, (int, float)):                    # epoch milliseconds
                d = datetime.fromtimestamp(raw / 1000, tz=timezone.utc).date()
            elif isinstance(raw, str):
                d = date.fromisoformat(raw[:10])
            else:
                continue
        rows.append(dict({"date": d.isoformat()}, **{k: a.get(k) for k in FIELDS}))
    rows.sort(key=lambda r: r["date"])
    return rows


def fetch(days: int) -> list:
    params = {"where": "portname='Strait of Hormuz'",
              "outFields": ",".join(["year", "month", "day", "date"] + FIELDS),
              "orderByFields": "date DESC", "resultRecordCount": str(days), "f": "json"}
    req = urllib.request.Request(f"{URL}?{urllib.parse.urlencode(params)}",
                                 headers={"User-Agent": "hormuz-watch-hackathon/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return parse_features(json.loads(resp.read().decode("utf-8")))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"PortWatch HTTP {e.code}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"PortWatch lỗi mạng: {e.reason}")


def load(path: Path = OUT) -> dict:
    """date -> row, for the export script. Missing file -> {}."""
    if not path.exists():
        return {}
    with open(path, encoding="utf-8-sig") as f:
        return {r["date"]: r for r in csv.DictReader(f)}


def main() -> int:
    p = argparse.ArgumentParser(description="Tải số lượt tàu qua eo Hormuz mỗi ngày (IMF PortWatch).")
    p.add_argument("--days", type=int, default=30)
    args = p.parse_args()
    try:
        rows = fetch(args.days)
    except (RuntimeError, ValueError) as e:
        print(f"LỖI: {e}")
        return 2
    OUT.parent.mkdir(exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["date"] + FIELDS)
        w.writeheader()
        w.writerows(rows)
    if rows:
        print(f"PortWatch: {len(rows)} ngày, {rows[0]['date']} → {rows[-1]['date']} (ngày mới nhất đã công bố)")
        for r in rows[-7:]:
            print(f"  {r['date']}: tổng {r['n_total']} lượt, tanker {r['n_tanker']}")
    print(f"Đã ghi {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
