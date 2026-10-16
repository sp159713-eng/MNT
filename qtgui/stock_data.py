import csv
import io
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(BASE_DIR, "data_cache")
NAMES_FILE = os.path.join(CACHE, "EQUITY_L.csv")
NAMES_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"


def company_names():
    text = None
    if os.path.exists(NAMES_FILE):
        with open(NAMES_FILE, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    else:
        try:
            import requests

            reply = requests.get(NAMES_URL, timeout=20, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 Chrome/120 Safari/537.36"})
            if reply.status_code == 200 and reply.text.startswith("SYMBOL"):
                text = reply.text
                os.makedirs(CACHE, exist_ok=True)
                with open(NAMES_FILE, "w", encoding="utf-8") as handle:
                    handle.write(text)
        except Exception:
            text = None
    if not text:
        return {}
    names = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) > 1 and row[0] != "SYMBOL":
            names[row[0].strip().upper()] = row[1].strip()
    return names


def history(symbol, bars=260):
    path = os.path.join(CACHE, f"{symbol}_NS_1d_adj.csv")
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - 90 * (bars + 2)))
            lines = handle.read().decode("utf-8", "replace").strip().splitlines()
    except OSError:
        return []
    rows = []
    for line in lines[1:][-bars:]:
        parts = line.split(",")
        try:
            rows.append((parts[0], float(parts[4])))
        except (IndexError, ValueError):
            continue
    return rows


def _ret(closes, back):
    if len(closes) <= back or not closes[-1 - back]:
        return None
    return (closes[-1] / closes[-1 - back] - 1) * 100


def top_picks():
    try:
        import json

        with open(os.path.join(BASE_DIR, "artifacts", "predictions.json"),
                  encoding="utf-8") as handle:
            made = json.load(handle)
        return set(made[-1].get("top5") or [])
    except Exception:
        return set()


def quotes(symbols):
    names = company_names()
    picks = top_picks()
    out = {}
    for symbol in symbols:
        rows = history(symbol)
        closes = [c for _, c in rows]
        quote = {"name": names.get(symbol.upper(), ""), "pick": symbol in picks,
                 "date": rows[-1][0] if rows else None,
                 "close": closes[-1] if closes else None,
                 "day": _ret(closes, 1), "month": _ret(closes, 21),
                 "year": _ret(closes, 251), "spark": closes[-63:]}
        if closes:
            year = closes[-252:]
            quote["low"], quote["high"] = min(year), max(year)
        out[symbol] = quote
    return out
