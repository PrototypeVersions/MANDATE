"""MANDATE paper-trading engine.

Collects daily price bars, takes in decision files, simulates fills at the open
of the next full daily bar, and keeps the books for two separate paper accounts.
Standard library only. No real money is involved anywhere.
"""
from __future__ import annotations

import json
import re
import statistics
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
NY = ZoneInfo("America/New_York")
PORTFOLIOS = ("stocks", "crypto")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{5,80}$")
SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
HEADERS = {
    "User-Agent": "MANDATE-Ledger/1.0 (+https://github.com/PrototypeVersions/MANDATE)",
    "Accept": "application/json,text/csv,*/*",
}


# ---------------------------------------------------------------- utilities

def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(text: str) -> datetime:
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamp needs a timezone")
    return dt.astimezone(timezone.utc)


def load(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def r2(x: float) -> float:
    return round(x + 0.0, 2)


def bar_start(portfolio: str, date: str) -> datetime:
    """UTC moment a daily bar begins: 09:30 New York for stocks, 00:00 UTC for crypto."""
    y, m, d = (int(p) for p in date.split("-"))
    if portfolio == "stocks":
        return datetime(y, m, d, 9, 30, tzinfo=NY).astimezone(timezone.utc)
    return datetime(y, m, d, tzinfo=timezone.utc)


def bar_complete(portfolio: str, date: str, now: datetime) -> bool:
    y, m, d = (int(p) for p in date.split("-"))
    if portfolio == "stocks":
        end = datetime(y, m, d, 16, 0, tzinfo=NY).astimezone(timezone.utc) + timedelta(minutes=20)
    else:
        end = datetime(y, m, d, tzinfo=timezone.utc) + timedelta(days=1, minutes=5)
    return now >= end


# ---------------------------------------------------------------- live prices

def http_json(url: str):
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=25) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # network errors are reported, never hidden
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{type(last).__name__}: {last}")


class LiveProvider:
    """Yahoo Finance daily chart for stocks and ETFs, Coinbase Exchange candles for crypto."""

    def __init__(self, now: datetime):
        self.now = now

    def bars(self, portfolio: str, symbol: str) -> dict:
        data = self._stock(symbol) if portfolio == "stocks" else self._crypto(symbol)
        data["bars"] = [b for b in data["bars"] if bar_complete(portfolio, b["date"], self.now)]
        if not data["bars"]:
            raise RuntimeError("no completed bars returned")
        time.sleep(0.25)
        return data

    def _stock(self, symbol: str) -> dict:
        ticker = symbol.replace(".", "-")
        url = ("https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(ticker)
               + "?range=3mo&interval=1d&events=div%2Csplits")
        res = http_json(url)["chart"]["result"][0]
        if res.get("meta", {}).get("currency") not in (None, "USD"):
            raise RuntimeError("not a US dollar listing")
        quote = res["indicators"]["quote"][0]
        bars = []
        for i, ts in enumerate(res.get("timestamp") or []):
            o, c = quote["open"][i], quote["close"][i]
            if o is None or c is None or o <= 0 or c <= 0:
                continue
            date = datetime.fromtimestamp(ts, NY).strftime("%Y-%m-%d")
            bars.append({"date": date, "open": float(o), "close": float(c),
                         "volume": float(quote["volume"][i] or 0)})
        events = res.get("events") or {}
        dividends, splits = {}, {}
        for ev in (events.get("dividends") or {}).values():
            date = datetime.fromtimestamp(ev["date"], NY).strftime("%Y-%m-%d")
            dividends[date] = dividends.get(date, 0.0) + float(ev["amount"])
        for ev in (events.get("splits") or {}).values():
            date = datetime.fromtimestamp(ev["date"], NY).strftime("%Y-%m-%d")
            if ev.get("numerator") and ev.get("denominator"):
                splits[date] = float(ev["numerator"]) / float(ev["denominator"])
        bars.sort(key=lambda b: b["date"])
        return {"bars": bars, "dividends": dividends, "splits": splits, "source": "Yahoo Finance daily chart"}

    def _crypto(self, symbol: str) -> dict:
        url = ("https://api.exchange.coinbase.com/products/" + urllib.parse.quote(symbol)
               + "-USD/candles?granularity=86400")
        rows = http_json(url)
        if not isinstance(rows, list):
            raise RuntimeError("unexpected candle response")
        bars = []
        for ts, _low, _high, o, c, vol in rows:
            date = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
            bars.append({"date": date, "open": float(o), "close": float(c), "volume": float(vol) * float(c)})
        bars.sort(key=lambda b: b["date"])
        bars = bars[-95:]
        return {"bars": bars, "dividends": {}, "splits": {}, "source": "Coinbase Exchange daily candles"}


# ---------------------------------------------------------------- decisions

def validate_decision(doc, stem: str, settings: dict) -> tuple[dict | None, str | None]:
    """Return (clean decision, None) or (None, reason it was refused)."""
    if not isinstance(doc, dict):
        return None, "file is not a JSON object"
    if doc.get("id") != stem or not ID_RE.match(stem):
        return None, "id must match the file name and use 5-80 letters, digits, - or _"
    portfolio = doc.get("portfolio")
    if portfolio not in PORTFOLIOS:
        return None, "portfolio must be stocks or crypto"
    for field in ("summary", "thesis"):
        if not isinstance(doc.get(field), str) or not doc[field].strip():
            return None, f"{field} is required"
    try:
        decided = iso(parse_iso(str(doc.get("decided_at_utc"))))
    except Exception:
        return None, "decided_at_utc must be an ISO 8601 time with a timezone"
    raw_orders = doc.get("orders", [])
    if not isinstance(raw_orders, list) or len(raw_orders) > 8:
        return None, "orders must be a list of at most 8"
    orders = []
    for n, raw in enumerate(raw_orders, 1):
        if not isinstance(raw, dict):
            return None, f"order {n} is not an object"
        side = raw.get("side")
        symbol = str(raw.get("symbol", "")).upper().strip()
        if side not in ("BUY", "SELL"):
            return None, f"order {n}: side must be BUY or SELL"
        if not SYMBOL_RE.match(symbol):
            return None, f"order {n}: bad symbol"
        if portfolio == "crypto" and symbol not in settings["crypto"]["allowed"]:
            return None, f"order {n}: {symbol} is not on the supported coin list"
        order = {"order_id": f"{stem}#{n}", "side": side, "symbol": symbol,
                 "reason": str(raw.get("reason", ""))[:600], "status": "pending"}
        usd, pct = raw.get("usd"), raw.get("pct")
        if side == "BUY":
            if not isinstance(usd, (int, float)) or isinstance(usd, bool) or usd < settings["min_order_usd"]:
                return None, f"order {n}: BUY needs usd of at least {settings['min_order_usd']}"
            order["usd"] = float(usd)
        elif isinstance(usd, (int, float)) and not isinstance(usd, bool) and usd >= settings["min_order_usd"]:
            order["usd"] = float(usd)
        else:
            pct = 100 if pct is None else pct
            if not isinstance(pct, (int, float)) or isinstance(pct, bool) or not 0 < pct <= 100:
                return None, f"order {n}: SELL needs pct between 0 and 100, or usd"
            order["pct"] = float(pct)
        orders.append(order)
    sources = [s for s in (doc.get("sources") or []) if isinstance(s, str) and s.startswith(("https://", "http://"))]
    clean = {
        "id": stem,
        "portfolio": portfolio,
        "decided_at_utc": decided,
        "model": str(doc.get("model", "Claude"))[:80],
        "summary": doc["summary"].strip()[:300],
        "thesis": doc["thesis"].strip()[:6000],
        "risks": str(doc.get("risks", "")).strip()[:3000],
        "strategy_change": str(doc.get("strategy_change", "")).strip()[:3000],
        "sources": [s[:600] for s in sources[:12]],
        "orders": orders,
    }
    return clean, None


def intake(root: Path, state: dict, journal: list, settings: dict, now: datetime) -> int:
    """Record every decision file not seen before. First-seen time is set here, by the engine."""
    new = 0
    for path in sorted((root / "decisions" / "inbox").glob("*.json")):
        stem = path.stem
        if stem in state["decisions_seen"]:
            continue
        state["decisions_seen"][stem] = iso(now)
        new += 1
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            doc, err = None, f"not valid JSON ({exc})"
        else:
            doc, err = validate_decision(doc, stem, settings)
        if err:
            journal.append({"id": stem, "portfolio": None, "first_seen_utc": iso(now), "status": "refused",
                            "summary": "Decision file refused", "thesis": err, "orders": []})
            continue
        doc["first_seen_utc"] = iso(now)
        doc["status"] = "recorded"
        journal.append(doc)
    return new


# ---------------------------------------------------------------- bookkeeping

def new_state(settings: dict, now: datetime) -> dict:
    def account(name):
        return {"starting_cash": settings["starting_cash"], "cash": float(settings["starting_cash"]),
                "positions": {}, "last_bar_date": None,
                "benchmark": {"symbol": settings[name]["benchmark"], "units": None,
                              "start_date": None, "start_price": None}}
    return {"version": 1, "created_utc": iso(now), "decisions_seen": {},
            "portfolios": {name: account(name) for name in PORTFOLIOS}}


def price_on(data: dict, date: str, field: str = "close"):
    """Latest price at or before a date: that day's bar if it exists, otherwise the prior close."""
    best = None
    for bar in data["bars"]:
        if bar["date"] > date:
            break
        best = bar
    if best is None:
        return None
    return best[field] if best["date"] == date else best["close"]


def liquidity_problem(data: dict, date: str, rules: dict):
    prior = [b for b in data["bars"] if b["date"] < date][-20:]
    if len(prior) < 5:
        return "too little price history"
    if prior[-1]["close"] < rules["min_price"]:
        return f"share price is under ${rules['min_price']}"
    median = statistics.median(b["close"] * b["volume"] for b in prior)
    if median < rules["min_median_dollar_volume"]:
        return "trades too thinly (median daily dollar volume is under the minimum)"
    return None


def pending_orders(journal: list, portfolio: str):
    for entry in journal:
        if entry.get("portfolio") == portfolio:
            for order in entry["orders"]:
                if order["status"] == "pending":
                    yield entry, order


def process_day(portfolio: str, date: str, acct: dict, journal: list, market: dict, settings: dict) -> None:
    """Advance one account through one completed daily bar: corporate actions, fills at the open, mark at the close."""
    fee, slip = settings["fee_rate"], settings["slippage_rate"]
    bench = acct["benchmark"]

    # Splits and dividends take effect before the open, for what was held coming into the day.
    for sym, pos in acct["positions"].items():
        data = market[sym]
        if date in data["splits"]:
            pos["units"] *= data["splits"][date]
        if date in data["dividends"]:
            paid = pos["units"] * data["dividends"][date]
            acct["cash"] += paid
            pos["dividends"] = pos.get("dividends", 0.0) + paid
    bdata = market[bench["symbol"]]
    if date in bdata["splits"]:
        bench["units"] *= bdata["splits"][date]
    if date in bdata["dividends"]:
        bclose = price_on(bdata, date)
        bench["units"] *= 1 + bdata["dividends"][date] / bclose

    start = bar_start(portfolio, date)
    todo = []
    for entry, order in pending_orders(journal, portfolio):
        data = market.get(order["symbol"])
        if not data or not any(b["date"] == date for b in data["bars"]):
            continue
        if start <= parse_iso(entry["first_seen_utc"]):
            continue  # this bar had already begun when the decision was first seen
        todo.append((0 if order["side"] == "SELL" else 1, entry["first_seen_utc"], order))
    todo.sort(key=lambda t: (t[0], t[1], t[2]["order_id"]))

    def equity_at_open():
        total = acct["cash"]
        for sym, pos in acct["positions"].items():
            total += pos["units"] * price_on(market[sym], date, "open")
        return total

    for _, _, order in todo:
        sym = order["symbol"]
        data = market[sym]
        opened = next(b["open"] for b in data["bars"] if b["date"] == date)
        pos = acct["positions"].get(sym)
        if order["side"] == "SELL":
            if not pos:
                order.update(status="rejected", note="nothing held to sell")
                continue
            units = pos["units"] * order["pct"] / 100 if "pct" in order else min(order["usd"] / opened, pos["units"])
            if (pos["units"] - units) * opened < 1:
                units = pos["units"]
            price = opened * (1 - slip)
            gross = units * price
            cost_out = pos["cost"] * units / pos["units"]
            charge = gross * fee
            acct["cash"] += gross - charge
            pos["units"] -= units
            pos["cost"] -= cost_out
            order.update(status="filled", fill={"date": date, "price": price, "units": units,
                                                "usd": r2(gross), "fee": round(charge, 4),
                                                "realized_pnl": r2(gross - charge - cost_out)})
            if pos["units"] <= 1e-12:
                del acct["positions"][sym]
            continue

        if portfolio == "stocks":
            problem = liquidity_problem(data, date, settings["stocks"])
            if problem:
                order.update(status="rejected", note=problem)
                continue
        equity = equity_at_open()
        held = pos["units"] * opened if pos else 0.0
        room = settings["max_position_fraction"] * equity - held
        notional = min(order["usd"], room, acct["cash"] / (1 + fee))
        if notional < settings["min_order_usd"]:
            why = "position limit already reached" if room < settings["min_order_usd"] else "not enough cash"
            order.update(status="rejected", note=why)
            continue
        price = opened * (1 + slip)
        units = notional / price
        charge = notional * fee
        acct["cash"] -= notional + charge
        if pos:
            pos["units"] += units
            pos["cost"] += notional + charge
        else:
            acct["positions"][sym] = {"units": units, "cost": notional + charge, "opened": date}
        order.update(status="filled", fill={"date": date, "price": price, "units": units,
                                            "usd": r2(notional), "fee": round(charge, 4)})
        if notional < order["usd"] - 0.005:
            order["note"] = f"reduced from ${order['usd']:.2f} to fit the cash or position limit"

    # Mark everything at the close.
    equity = acct["cash"]
    for sym, pos in acct["positions"].items():
        close = price_on(market[sym], date)
        pos["price"], pos["price_date"] = close, date
        pos["value"] = r2(pos["units"] * close)
        equity += pos["units"] * close
    acct["equity"] = r2(equity)
    acct["benchmark_value"] = r2(bench["units"] * price_on(bdata, date))
    acct["last_bar_date"] = date


def run_cycle(root: Path, now: datetime, provider) -> dict:
    settings = load(root / "config" / "settings.json", None)
    data_dir = root / "data"
    state = load(data_dir / "state.json", None) or new_state(settings, now)
    journal = load(data_dir / "journal.json", [])
    history = load(data_dir / "history.json", {name: [] for name in PORTFOLIOS})
    prices = load(data_dir / "market.json", {"prices": {name: {} for name in PORTFOLIOS}})["prices"]
    errors, warnings = [], []

    new_decisions = intake(root, state, journal, settings, now)

    for portfolio in PORTFOLIOS:
        acct = state["portfolios"][portfolio]
        bench = acct["benchmark"]
        must_have = {bench["symbol"], *acct["positions"]}
        wanted = set(must_have) | set(settings[portfolio]["watchlist"])
        wanted |= {order["symbol"] for _, order in pending_orders(journal, portfolio)}
        market, failed = {}, []
        for sym in sorted(wanted):
            try:
                market[sym] = provider.bars(portfolio, sym)
            except Exception as exc:
                failed.append(sym)
                (errors if sym in must_have else warnings).append(f"{portfolio} {sym}: {exc}")
        for sym, data in market.items():
            last = data["bars"][-1]
            prev = data["bars"][-2]["close"] if len(data["bars"]) > 1 else None
            prices[portfolio][sym] = {
                "date": last["date"], "close": last["close"],
                "change_pct": round((last["close"] / prev - 1) * 100, 2) if prev else None,
                "source": data.get("source", ""),
            }
        for sym in list(prices[portfolio]):
            if sym not in wanted:
                del prices[portfolio][sym]

        blocked = sorted(must_have & set(failed))
        if blocked:
            errors.append(f"{portfolio}: books not advanced, missing prices for {', '.join(blocked)}")
        else:
            dates = [b["date"] for b in market[bench["symbol"]]["bars"]]
            if bench["units"] is None:
                first = dates[-1]
                close = price_on(market[bench["symbol"]], first)
                bench.update(units=acct["starting_cash"] / close, start_date=first, start_price=close)
                acct.update(last_bar_date=first, equity=r2(acct["cash"]), benchmark_value=r2(acct["starting_cash"]))
                history[portfolio].append({"date": first, "equity": acct["equity"],
                                           "benchmark": acct["benchmark_value"], "cash": r2(acct["cash"])})
            for date in dates:
                if date <= acct["last_bar_date"]:
                    continue
                process_day(portfolio, date, acct, journal, market, settings)
                history[portfolio].append({"date": date, "equity": acct["equity"],
                                           "benchmark": acct["benchmark_value"], "cash": r2(acct["cash"])})

        limit = timedelta(days=settings["order_expiry_days"])
        for entry, order in pending_orders(journal, portfolio):
            if now - parse_iso(entry["first_seen_utc"]) > limit:
                order.update(status="expired", note="no price bar became available in time")

    status = {"last_run_utc": iso(now), "ok": not errors, "errors": errors[:40], "warnings": warnings[:40],
              "decisions_recorded": len(state["decisions_seen"]), "new_decisions_this_run": new_decisions}
    save(data_dir / "state.json", state)
    save(data_dir / "journal.json", journal)
    save(data_dir / "history.json", history)
    save(data_dir / "market.json", {"as_of_utc": iso(now), "prices": prices})
    save(data_dir / "status.json", status)
    return status


def main() -> int:
    now = datetime.now(timezone.utc)
    status = run_cycle(ROOT, now, LiveProvider(now))
    print(json.dumps(status, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
