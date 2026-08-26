"""Headlines for the universe, scored, and kept as a series.

WHAT THIS IS AND IS NOT

It is a reader for Google News RSS, one query per symbol, with a lexicon
sentiment score attached and the result appended to a JSONL file so that
repeated runs build history rather than replacing it.

It is not a signal, and nothing in this project trades on it. That distinction
is worth stating in the file itself, because sentiment data has a way of
drifting into a model on the strength of looking sophisticated. Two reasons it
stays out here:

  The score is a weighted word count. `beats`, `record` are positive;
  `probe`, `fine`, `slump` are negative, each on a 1-5 intensity scale so
  "collapses" outweighs "slips" instead of counting the same. That is still
  a crude instrument, it has no idea what a sentence means, and it will read
  "shares slump on record profit" as a wash. A transformer sentiment model
  would be better and is not installed.

  There is no history to test it against. Google News RSS returns what is
  current; it cannot be queried for what was current in March 2019. A feature
  that cannot be backtested cannot be shown to help, and this project's whole
  discipline is refusing to trade things that have not cleared that bar. The
  JSONL accumulates precisely so that in a year there is something to test.

Until then it is a dashboard: what is being said about the names you hold.

Run with:  py -3.13 news.py                    headlines for the book
           py -3.13 news.py --symbols TCS INFY --limit 8
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from xml.etree import ElementTree

import config

FEED = "https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
NEWS_PATH = os.path.join(config.MODEL_DIR, "news.jsonl")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MNT/1.0"

# Company names, because "ITC" as a search term returns everything and nothing.
QUERY_NAMES = {
    "RELIANCE": "Reliance Industries", "TCS": "Tata Consultancy Services",
    "HDFCBANK": "HDFC Bank", "ICICIBANK": "ICICI Bank", "INFY": "Infosys",
    "HINDUNILVR": "Hindustan Unilever", "ITC": "ITC Limited",
    "SBIN": "State Bank of India", "BHARTIARTL": "Bharti Airtel",
    "KOTAKBANK": "Kotak Mahindra Bank", "LT": "Larsen Toubro",
    "AXISBANK": "Axis Bank", "ASIANPAINT": "Asian Paints",
    "MARUTI": "Maruti Suzuki", "TITAN": "Titan Company",
    "SUNPHARMA": "Sun Pharmaceutical", "ULTRACEMCO": "UltraTech Cement",
    "BAJFINANCE": "Bajaj Finance", "NESTLEIND": "Nestle India",
    "WIPRO": "Wipro", "ONGC": "Oil and Natural Gas Corporation",
    "NTPC": "NTPC Limited", "POWERGRID": "Power Grid Corporation",
    "HEROMOTOCO": "Hero MotoCorp", "TATASTEEL": "Tata Steel",
    "HCLTECH": "HCL Technologies", "JSWSTEEL": "JSW Steel",
    "GRASIM": "Grasim Industries", "CIPLA": "Cipla", "COALINDIA": "Coal India",
}

# Intensity 1 (mild) to 5 (extreme) per word, not a flat +-1 - "plunges" and
# "gains" used to count the same as "slips" and "edges up". Graded so a
# headline with one extreme word outweighs one with a mild word, the way a
# reader would. Display only (see module docstring) so no [-1, 1] contract
# to preserve here - the sum stays a plain int, same as before.
POSITIVE = {
    "beat": 3, "beats": 3, "surge": 4, "surges": 4, "jump": 4, "jumps": 4,
    "rise": 3, "rises": 3, "gain": 3, "gains": 3, "record": 3, "profit": 3,
    "profits": 3, "growth": 3, "upgrade": 3, "upgrades": 3, "outperform": 3,
    "bullish": 3, "rally": 4, "rallies": 4, "high": 3, "strong": 3,
    "expansion": 3, "wins": 3, "win": 3, "approval": 2, "approved": 2,
    "dividend": 2, "buyback": 2, "order": 2, "orders": 2, "deal": 2,
    "acquire": 2, "acquisition": 2, "boost": 2, "boosts": 2,
    "blockbuster": 5, "multibagger": 5,
    "edges_up": 1, "ticks_up": 1, "inches_up": 1, "nudges_up": 1,
}
NEGATIVE = {
    "fall": 3, "falls": 3, "drop": 3, "drops": 3, "slump": 4, "slumps": 4,
    "plunge": 4, "plunges": 4, "loss": 2, "losses": 2, "decline": 3,
    "declines": 3, "downgrade": 3, "downgrades": 3, "bearish": 3, "weak": 2,
    "miss": 2, "misses": 2, "probe": 3, "fine": 3, "fined": 3, "penalty": 3,
    "raid": 3, "fraud": 5, "lawsuit": 3, "cut": 2, "cuts": 2, "layoff": 3,
    "layoffs": 3, "warning": 2, "concern": 2, "concerns": 2, "risk": 2,
    "debt": 2, "default": 4, "resign": 2, "resigns": 2,
    "collapse": 5, "collapses": 5, "bankruptcy": 5, "insolvency": 5,
    "slips": 1, "slip": 1, "dips": 1, "dip": 1, "eases": 1, "eased": 1,
    "edges_down": 1, "ticks_down": 1, "inches_down": 1, "nudges_down": 1,
}

# "edges up" / "edges down" need opposite scores, so the direction word is
# folded into one token before matching - same fix as NNS's news.py.
PHRASE_JOINS = [
    (re.compile(pattern), replacement) for pattern, replacement in (
        (r"\bedges? (up|higher)\b", "edges_up"),
        (r"\bedges? (down|lower)\b", "edges_down"),
        (r"\bticks? (up|higher)\b", "ticks_up"),
        (r"\bticks? (down|lower)\b", "ticks_down"),
        (r"\binches? (up|higher)\b", "inches_up"),
        (r"\binches? (down|lower)\b", "inches_down"),
        (r"\bnudges? (up|higher)\b", "nudges_up"),
        (r"\bnudges? (down|lower)\b", "nudges_down"),
    )
]

WORD = re.compile(r"[a-z_']+")


def score(text: str) -> int:
    """Sum of matched words' intensity (1-5), positive minus negative.

    Still an int, still positive-words-minus-negative-words in spirit - just
    weighted per word instead of counting every hit as +-1. Crude, and
    labelled as such.
    """
    lowered = text.lower()
    for pattern, replacement in PHRASE_JOINS:
        lowered = pattern.sub(replacement, lowered)
    words = set(WORD.findall(lowered))
    total = sum(POSITIVE.get(w, 0) for w in words)
    total -= sum(NEGATIVE.get(w, 0) for w in words)
    return total


def fetch(symbol: str, limit: int = 6, timeout: int = 12) -> list[dict]:
    """Recent headlines for one symbol."""
    name = QUERY_NAMES.get(symbol, symbol)
    query = urllib.parse.quote(f"{name} stock NSE")
    request = urllib.request.Request(FEED.format(query=query),
                                     headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        tree = ElementTree.fromstring(response.read())

    items = []
    for item in tree.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        items.append({
            "symbol": symbol,
            "title": title,
            "published": (item.findtext("pubDate") or "").strip(),
            "link": (item.findtext("link") or "").strip(),
            "score": score(title),
        })
        if len(items) >= limit:
            break
    return items


def collect(symbols: list[str], limit: int = 6, pause: float = 0.4,
            quiet: bool = False) -> list[dict]:
    """Headlines for several symbols. Failures are reported, never silent."""
    out = []
    for symbol in symbols:
        try:
            out.extend(fetch(symbol, limit))
        except Exception as error:                              # noqa: BLE001
            if not quiet:
                print(f"  {symbol}: {type(error).__name__}: {error}")
        time.sleep(pause)
    return out


def record(items: list[dict]) -> str:
    """Append to the JSONL, so runs accumulate into something testable later."""
    if not items:
        return NEWS_PATH
    os.makedirs(os.path.dirname(NEWS_PATH), exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(NEWS_PATH, "a", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps({**item, "collected": stamp}) + "\n")
    return NEWS_PATH


def by_symbol(items: list[dict]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for item in items:
        totals[item["symbol"]] = totals.get(item["symbol"], 0) + item["score"]
    return totals


def seen_titles() -> set[str]:
    """Titles already in the JSONL, so a watch loop can report only new ones."""
    if not os.path.exists(NEWS_PATH):
        return set()
    titles = set()
    with open(NEWS_PATH, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                titles.add(json.loads(line)["title"])
            except (json.JSONDecodeError, KeyError):
                continue
    return titles


def watch(symbols: list[str], every: int = 900, limit: int = 5) -> None:
    """Poll forever, printing only headlines not already recorded.

    `every` defaults to 15 minutes. Faster is not better: Google News RSS
    updates on the order of minutes, the same headline is simply re-served in
    between, and hammering it is the reliable way to start getting 429s. The
    de-duplication is against the recorded file rather than an in-memory set,
    so restarting the watcher does not replay everything it has already shown.
    """
    known = seen_titles()
    print(f"watching {len(symbols)} symbols every {every}s "
          f"({len(known)} headlines already recorded). Ctrl-C to stop.\n")
    try:
        while True:
            items = collect(symbols, limit, quiet=True)
            fresh = [i for i in items if i["title"] not in known]
            if fresh:
                stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
                for item in sorted(fresh, key=lambda i: -i["score"]):
                    sign = f"{item['score']:+d}" if item["score"] else " 0"
                    print(f"[{stamp}] {sign}  {item['symbol']:<12} "
                          f"{item['title'][:88]}")
                record(fresh)
                known.update(i["title"] for i in fresh)
            else:
                print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] "
                      f"nothing new", flush=True)
            time.sleep(every)
    except KeyboardInterrupt:
        print(f"\nstopped. {len(known)} headlines recorded in total.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--all", action="store_true",
                        help="every name in the universe, not just the book")
    parser.add_argument("--watch", action="store_true",
                        help="keep polling and print only new headlines")
    parser.add_argument("--every", type=int, default=900,
                        help="--watch only: seconds between polls")
    args = parser.parse_args()

    symbols = args.symbols
    if not symbols:
        symbols = config.UNIVERSE if args.all else _held_or_default()

    if args.watch:
        watch(symbols, args.every, args.limit)
        return

    print(f"\nfetching headlines for {len(symbols)} symbols...\n")
    items = collect(symbols, args.limit)
    if not items:
        raise SystemExit("no headlines returned")

    for symbol in symbols:
        rows = [i for i in items if i["symbol"] == symbol]
        if not rows:
            continue
        total = sum(r["score"] for r in rows)
        mark = "+" if total > 0 else "-" if total < 0 else " "
        print(f"{symbol}  [{mark}{abs(total)}]")
        for row in rows:
            sign = f"{row['score']:+d}" if row["score"] else " 0"
            print(f"   {sign}  {row['title'][:96]}")
        print()

    path = record(items)
    totals = by_symbol(items)
    ranked = sorted(totals.items(), key=lambda kv: -kv[1])
    print(f"{len(items)} headlines appended to {path}\n")
    print("most positive: " + ", ".join(f"{s} {v:+d}" for s, v in ranked[:3]))
    print("most negative: " + ", ".join(f"{s} {v:+d}" for s, v in ranked[-3:]))
    print("\nThis is a word count, not a signal. Nothing trades on it.")


def _held_or_default() -> list[str]:
    """Whatever the paper account holds, else the first six of the universe."""
    try:
        import broker as broker_module

        held = list(broker_module.broker("paper").holdings())
        if held:
            return held
    except Exception:                                           # noqa: BLE001
        pass
    return config.UNIVERSE[:6]


if __name__ == "__main__":
    main()
