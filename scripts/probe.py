"""Print the raw tail of the price feeds, for diagnosing feed problems: python3 scripts/probe.py"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts import engine  # noqa: E402

print("now", datetime.now(timezone.utc).isoformat())
for rng in ("3mo", "15d"):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/SPY?range={rng}&interval=1d&events=div%2Csplits"
    try:
        res = engine.http_json(url)["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
        ts = res["timestamp"]
        print(rng, "rows", len(ts), "marketTime", res["meta"].get("regularMarketTime"))
        for i in range(-3, 0):
            print("  ", ts[i], datetime.fromtimestamp(ts[i], timezone.utc).isoformat(), q["open"][i], q["close"][i], q["volume"][i])
    except Exception as exc:
        print(rng, "failed:", exc)
