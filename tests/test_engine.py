import json
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts import engine

REPO = Path(__file__).resolve().parent.parent


class FakeProvider:
    def __init__(self, now, stocks, crypto=None, dividends=None, splits=None, fail=()):
        self.now, self.fail = now, set(fail)
        self.table = {"stocks": stocks, "crypto": crypto or {}}
        self.dividends, self.splits = dividends or {}, splits or {}

    def bars(self, portfolio, symbol):
        if symbol in self.fail or symbol not in self.table[portfolio]:
            raise RuntimeError("no data")
        bars = [{"date": d, "open": o, "close": c, "volume": 5_000_000}
                for d, o, c in self.table[portfolio][symbol]
                if engine.bar_complete(portfolio, d, self.now)]
        if not bars:
            raise RuntimeError("no completed bars")
        return {"bars": bars, "dividends": self.dividends.get(symbol, {}),
                "splits": self.splits.get(symbol, {}), "source": "test"}


def utc(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


# Weekdays 2 to 13 November 2026, with 21 earlier sessions in October so liquidity checks have history.
OCT = [f"2026-10-{d:02d}" for d in (1, 2, 5, 6, 7, 8, 9, 12, 13, 14, 15, 16, 19, 20, 21, 22, 23, 26, 27, 28, 29, 30)]
NOV = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09", "2026-11-10"]


def series(opens_closes, base=100.0):
    rows = [(d, base, base) for d in OCT]
    rows += [(d, o, c) for d, (o, c) in zip(NOV, opens_closes)]
    return rows


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "config").mkdir()
        (self.root / "decisions" / "inbox").mkdir(parents=True)
        settings = json.loads((REPO / "config" / "settings.json").read_text())
        settings["stocks"]["watchlist"] = ["SPY"]
        settings["crypto"]["watchlist"] = ["BTC"]
        (self.root / "config" / "settings.json").write_text(json.dumps(settings))
        self.stocks = {
            "SPY": series([(100, 100), (100, 100), (100, 110), (110, 110), (110, 121), (121, 121), (121, 121)]),
            "XLE": series([(50, 50), (50, 50), (50, 50), (40, 44), (44, 44), (44, 22), (22, 22)], base=50.0),
        }
        self.crypto = {"BTC": [(f"2026-11-{d:02d}", 1000.0, 1000.0) for d in range(1, 11)],
                       "ETH": [(f"2026-11-{d:02d}", 10.0 * d, 10.0 * d + 5) for d in range(1, 11)]}

    def tearDown(self):
        shutil.rmtree(self.root)

    def decide(self, stem, portfolio, orders, **extra):
        doc = {"id": stem, "portfolio": portfolio, "decided_at_utc": "2026-11-04T13:00:00Z",
               "summary": "test", "thesis": "test thesis", "orders": orders, **extra}
        (self.root / "decisions" / "inbox" / f"{stem}.json").write_text(json.dumps(doc))

    def cycle(self, when, **kw):
        now = utc(when)
        provider = FakeProvider(now, self.stocks, self.crypto, **kw)
        return engine.run_cycle(self.root, now, provider)

    def read(self, name):
        return json.loads((self.root / "data" / name).read_text())

    def test_first_run_sets_baseline_without_trading(self):
        status = self.cycle("2026-11-03T22:00:00")
        self.assertTrue(status["ok"])
        acct = self.read("state.json")["portfolios"]["stocks"]
        self.assertEqual(acct["cash"], 1000)
        self.assertEqual(acct["benchmark"]["start_date"], "2026-11-03")
        self.assertEqual(self.read("history.json")["stocks"], [
            {"date": "2026-11-03", "equity": 1000, "benchmark": 1000, "cash": 1000}])

    def test_order_fills_only_at_a_later_open(self):
        self.cycle("2026-11-03T22:00:00")
        # Seen at 15:00 UTC on 4 Nov, after that day's 14:30 UTC open: must wait for 5 Nov.
        self.decide("stocks-late", "stocks", [{"side": "BUY", "symbol": "XLE", "usd": 200}])
        self.cycle("2026-11-04T15:00:00")
        self.cycle("2026-11-04T22:00:00")
        order = self.read("journal.json")[0]["orders"][0]
        self.assertEqual(order["status"], "pending")
        self.cycle("2026-11-05T22:00:00")
        order = self.read("journal.json")[0]["orders"][0]
        self.assertEqual(order["status"], "filled")
        self.assertEqual(order["fill"]["date"], "2026-11-05")
        self.assertAlmostEqual(order["fill"]["price"], 40 * 1.0005)
        acct = self.read("state.json")["portfolios"]["stocks"]
        self.assertAlmostEqual(acct["cash"], 1000 - 200 - 0.2)
        units = 200 / (40 * 1.0005)
        self.assertAlmostEqual(acct["positions"]["XLE"]["units"], units)
        self.assertAlmostEqual(acct["equity"], round(799.8 + units * 44, 2))
        self.assertEqual(acct["benchmark_value"], 1100)

    def test_premarket_order_fills_same_day(self):
        self.cycle("2026-11-03T22:00:00")
        self.decide("stocks-early", "stocks", [{"side": "BUY", "symbol": "XLE", "usd": 100}])
        self.cycle("2026-11-04T13:20:00")
        self.cycle("2026-11-04T22:00:00")
        order = self.read("journal.json")[0]["orders"][0]
        self.assertEqual(order["fill"]["date"], "2026-11-04")

    def test_position_cap_and_sell(self):
        self.cycle("2026-11-03T22:00:00")
        self.decide("stocks-big", "stocks", [{"side": "BUY", "symbol": "XLE", "usd": 900}])
        self.cycle("2026-11-04T13:00:00")
        self.cycle("2026-11-04T22:00:00")
        order = self.read("journal.json")[0]["orders"][0]
        self.assertEqual(order["fill"]["usd"], 350)
        self.assertIn("reduced", order["note"])
        self.decide("stocks-exit", "stocks", [{"side": "SELL", "symbol": "XLE", "pct": 100},
                                              {"side": "SELL", "symbol": "QQQ"}])
        self.cycle("2026-11-05T13:00:00")
        self.cycle("2026-11-05T22:00:00")
        acct = self.read("state.json")["portfolios"]["stocks"]
        self.assertEqual(acct["positions"], {})
        orders = self.read("journal.json")[1]["orders"]
        self.assertEqual(orders[0]["status"], "filled")
        units = 350 / (50 * 1.0005)
        proceeds = units * 40 * 0.9995
        self.assertAlmostEqual(acct["cash"], 1000 - 350.35 + proceeds * 0.999)
        self.assertEqual(orders[1]["status"], "pending")  # QQQ has no data in this test

    def test_split_and_dividend(self):
        self.cycle("2026-11-03T22:00:00")
        self.decide("stocks-div", "stocks", [{"side": "BUY", "symbol": "XLE", "usd": 100}])
        self.cycle("2026-11-04T13:00:00")
        self.cycle("2026-11-06T22:00:00")
        before = self.read("state.json")["portfolios"]["stocks"]
        self.cycle("2026-11-09T22:00:00", dividends={"XLE": {"2026-11-09": 1.0}, "SPY": {"2026-11-09": 1.21}},
                   splits={"XLE": {"2026-11-09": 2.0}})
        after = self.read("state.json")["portfolios"]["stocks"]
        units = before["positions"]["XLE"]["units"]
        self.assertAlmostEqual(after["positions"]["XLE"]["units"], units * 2)
        self.assertAlmostEqual(after["cash"], before["cash"] + units * 2 * 1.0)
        self.assertAlmostEqual(after["benchmark"]["units"], before["benchmark"]["units"] * 1.01)

    def test_missing_price_for_a_holding_freezes_the_books(self):
        self.cycle("2026-11-03T22:00:00")
        self.decide("stocks-hold", "stocks", [{"side": "BUY", "symbol": "XLE", "usd": 100}])
        self.cycle("2026-11-04T13:00:00")
        self.cycle("2026-11-04T22:00:00")
        status = self.cycle("2026-11-05T22:00:00", fail={"XLE"})
        self.assertFalse(status["ok"])
        self.assertEqual(self.read("state.json")["portfolios"]["stocks"]["last_bar_date"], "2026-11-04")
        self.cycle("2026-11-06T22:00:00")
        dates = [h["date"] for h in self.read("history.json")["stocks"]]
        self.assertEqual(dates, ["2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"])

    def test_crypto_is_separate_and_uses_utc_days(self):
        self.cycle("2026-11-03T22:00:00")
        self.decide("crypto-eth", "crypto", [{"side": "BUY", "symbol": "ETH", "usd": 100}])
        self.cycle("2026-11-03T23:50:00")
        self.cycle("2026-11-05T00:10:00")
        state = self.read("state.json")["portfolios"]
        order = self.read("journal.json")[0]["orders"][0]
        self.assertEqual(order["fill"]["date"], "2026-11-04")
        self.assertAlmostEqual(order["fill"]["price"], 40 * 1.0005)
        self.assertEqual(state["stocks"]["cash"], 1000)
        self.assertAlmostEqual(state["crypto"]["cash"], 899.9)

    def test_bad_files_are_refused_and_bad_orders_rejected_or_expired(self):
        self.cycle("2026-11-03T22:00:00")
        self.decide("crypto-bad", "crypto", [{"side": "BUY", "symbol": "SCAMCOIN", "usd": 100}])
        (self.root / "decisions" / "inbox" / "broken.json").write_text("{nope")
        self.decide("stocks-none", "stocks", [{"side": "SELL", "symbol": "XLE"},
                                              {"side": "BUY", "symbol": "ZZZZ", "usd": 50}])
        self.decide("stocks-wait", "stocks", [])
        self.cycle("2026-11-04T13:00:00")
        self.cycle("2026-11-04T22:00:00")
        by_id = {e["id"]: e for e in self.read("journal.json")}
        self.assertEqual(by_id["crypto-bad"]["status"], "refused")
        self.assertEqual(by_id["broken"]["status"], "refused")
        self.assertEqual(by_id["stocks-wait"]["orders"], [])
        self.assertEqual(by_id["stocks-none"]["orders"][0]["status"], "rejected")
        self.cycle("2026-11-10T22:00:00")
        by_id = {e["id"]: e for e in self.read("journal.json")}
        self.assertEqual(by_id["stocks-none"]["orders"][1]["status"], "expired")
        self.assertEqual(self.read("state.json")["portfolios"]["stocks"]["cash"], 1000)

    def test_thin_stock_is_rejected(self):
        self.stocks["TINY"] = [(d, 2.0, 2.0) for d in OCT + NOV]
        self.cycle("2026-11-03T22:00:00")
        self.decide("stocks-tiny", "stocks", [{"side": "BUY", "symbol": "TINY", "usd": 50}])
        self.cycle("2026-11-04T13:00:00")
        self.cycle("2026-11-04T22:00:00")
        order = self.read("journal.json")[0]["orders"][0]
        self.assertEqual(order["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
