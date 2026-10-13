import json
import math
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import scan_jumps

RAW_DIR = ROOT / "data_cache" / "nse" / "corp_actions"
OUT = ROOT / "data" / "corrections.json"
REPORT = RAW_DIR / "_unmatched.json"
PAGE = "https://www.nseindia.com/companies-listing/corporate-filings-actions"
API = "https://www.nseindia.com/api/corporates-corporateActions"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": PAGE,
}
SINCE = "2011-01-01"
WINDOW = 3
SPLIT_TOL = 0.15
DEMERGER_TOL = 0.05
SUFFIX = "_NS_1d_adj"

NUM = r"([0-9]+(?:\.[0-9]+)?)"
SPLIT_RE = re.compile(r"from\s*(?:rs|re)?\.?\s*" + NUM + r"[^0-9]*?to\s*(?:rs|re)?\.?\s*" + NUM, re.I)
BONUS_RE = re.compile(r"bonus[^0-9]*?(\d+)\s*:\s*(\d+)", re.I)
DEMERGER_RE = re.compile(r"demerger|de-merger|spin[\s-]*off|arrangement|scheme|reconstruction|hive", re.I)
LOOKS_RE = re.compile(r"split|sub-?division|bonus|demerger|arrangement|spin|capital reduction", re.I)


def universe():
    return json.load(open(ROOT / "artifacts" / "stocks.json"))["universe"]


def yahoo_stem(symbol):
    import data
    return data.to_yahoo(symbol).replace("^", "IDX_").replace(".", "_") + "_1d_adj"


def hits_by_source():
    nse = scan_jumps.scan(ROOT / "data_cache" / "nse" / "adj", "*.csv")
    yah = scan_jumps.scan(ROOT / "data_cache", "*_NS_1d_adj.csv")
    nse = [h for h in nse if h[1] >= SINCE]
    yah = [(h[0][:-len(SUFFIX)],) + h[1:] for h in yah if h[1] >= SINCE]
    return {"nse": nse, "yahoo": yah}


def target_symbols(hits):
    uni = universe()
    nse_names = {p.stem for p in (ROOT / "data_cache" / "nse" / "adj").glob("*.csv")}
    extra = {h[0] for h in hits["nse"] if "-" not in h[0]}
    extra |= {h[0] for h in hits["yahoo"] if h[0] in nse_names}
    return uni + sorted(extra - set(uni))


def make_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    s.get(PAGE, timeout=30)
    return s


def fetch_symbol(session, symbol):
    for attempt in range(4):
        try:
            r = session.get(API, params={"index": "equities", "symbol": symbol}, timeout=30)
        except requests.RequestException:
            time.sleep(3)
            session = make_session()
            continue
        if r.status_code in (401, 403):
            time.sleep(3 + attempt * 3)
            session = make_session()
            continue
        if r.status_code == 200:
            try:
                return session, r.json()
            except ValueError:
                pass
        time.sleep(2)
    return session, None


def fetch_all(symbols):
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    session = make_session()
    todo = [s for s in symbols if not (RAW_DIR / f"{safe(s)}.json").exists()]
    print(f"fetching {len(todo)} of {len(symbols)} symbols", flush=True)
    for i, symbol in enumerate(todo, 1):
        session, rows = fetch_symbol(session, symbol)
        if rows is None:
            print(f"  FAILED {symbol}", flush=True)
        else:
            (RAW_DIR / f"{safe(symbol)}.json").write_text(json.dumps(rows))
        if i % 50 == 0:
            print(f"  {i}/{len(todo)}", flush=True)
        time.sleep(1.0)


def safe(symbol):
    return "".join("_" if ch in r'\/:*?"<>|' else ch for ch in symbol)


def parse_date(text):
    try:
        return datetime.strptime(text.strip(), "%d-%b-%Y").strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        return None


def parse_subject(subject):
    ratio = 1.0
    kinds = []
    m = SPLIT_RE.search(subject)
    if m and re.search(r"split|sub-?division|face value", subject, re.I):
        old, new = float(m.group(1)), float(m.group(2))
        if old > 0 and new > 0 and old != new:
            ratio *= new / old
            kinds.append("split")
    b = BONUS_RE.search(subject)
    if b:
        a, c = int(b.group(1)), int(b.group(2))
        if a > 0 and c > 0:
            ratio *= c / (a + c)
            kinds.append("bonus")
    if kinds:
        return ("split" if kinds == ["split"] else "bonus" if kinds == ["bonus"] else "bonus"), ratio
    if DEMERGER_RE.search(subject) and not re.search(r"dividend|annual general|rights", subject, re.I):
        return "demerger", None
    return "other", None


def load_events(symbol, unparsed):
    path = RAW_DIR / f"{safe(symbol)}.json"
    if not path.exists():
        return []
    out, seen = [], set()
    for row in json.loads(path.read_text()):
        subject = row.get("subject") or ""
        ex = parse_date(row.get("exDate") or "")
        if not ex:
            continue
        kind, ratio = parse_subject(subject)
        if kind == "other":
            if LOOKS_RE.search(subject):
                unparsed.append({"symbol": symbol, "ex_date": ex, "subject": subject})
            continue
        key = (ex, kind, ratio)
        if key in seen:
            continue
        seen.add(key)
        out.append({"symbol": symbol, "ex_date": ex, "kind": kind, "ratio": ratio, "subject": subject})
    return out


def read_bars(source, symbol):
    if source == "nse":
        path = ROOT / "data_cache" / "nse" / "adj" / f"{safe(symbol)}.csv"
    else:
        import data
        path = ROOT / "data_cache" / f"{yahoo_stem(symbol)}.csv"
    if not path.exists():
        return None
    return pd.read_csv(path, usecols=["timestamp", "close"], index_col=0, parse_dates=True)["close"]


def window_events(events, bars, date):
    idx = bars.index
    pos = idx.get_loc(pd.Timestamp(date))
    lo = idx[max(pos - WINDOW, 0)]
    hi = idx[min(pos + WINDOW, len(idx) - 1)]
    return [e for e in events if lo <= pd.Timestamp(e["ex_date"]) <= hi]


def build(hits):
    unparsed, entries, unmatched = [], {}, []
    events_cache = {}
    for source, rows in hits.items():
        bars_cache = {}
        for symbol, date, move, _ in rows:
            if symbol not in events_cache:
                events_cache[symbol] = load_events(symbol, unparsed)
            events = events_cache[symbol]
            if symbol not in bars_cache:
                bars_cache[symbol] = read_bars(source, symbol)
            bars = bars_cache[symbol]
            if bars is None:
                continue
            pos = bars.index.get_loc(pd.Timestamp(date))
            observed = float(bars.iloc[pos] / bars.iloc[pos - 1])
            near = window_events(events, bars, date)
            best = None
            for e in near:
                if e["kind"] == "demerger":
                    gap = 0.0
                    expected = observed
                    tol_ok = observed < 0.95
                else:
                    expected = e["ratio"]
                    gap = abs(math.log(observed / expected))
                    tol_ok = gap < SPLIT_TOL
                if tol_ok and (best is None or gap < best[0]):
                    best = (gap, e, expected)
            if best is None:
                reason = "no event" if not near else "event ratio mismatch: " + "; ".join(
                    f"{e['kind']} {e['ex_date']} {e['ratio']}" for e in near)
                unmatched.append({"source": source, "symbol": symbol, "date": date,
                                  "move": round(move, 4), "reason": reason})
                continue
            _, e, expected = best
            if e["kind"] == "demerger":
                key = (symbol, e["ex_date"], "demerger", source)
                entries[key] = {"symbol": symbol, "ex_date": e["ex_date"], "factor": round(observed, 6),
                                "kind": "demerger", "source": source, "jump_date": date,
                                "source_ratio_check": round(observed, 6)}
            else:
                key = (symbol, e["ex_date"], e["kind"], "both")
                prior = entries.get(key)
                entries[key] = {"symbol": symbol, "ex_date": e["ex_date"], "factor": round(e["ratio"], 8),
                                "kind": e["kind"], "source": "both", "jump_date": date,
                                "source_ratio_check": round(observed, 6) if prior is None
                                else prior["source_ratio_check"]}
    table = sorted(entries.values(), key=lambda r: (r["symbol"], r["ex_date"], r["source"]))
    return table, unmatched, unparsed


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    hits = hits_by_source()
    symbols = target_symbols(hits)
    if mode in ("fetch", "all"):
        fetch_all(symbols)
    if mode in ("build", "all"):
        table, unmatched, unparsed = build(hits)
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(table, indent=1))
        REPORT.write_text(json.dumps({"unmatched": unmatched, "unparsed": unparsed}, indent=1))
        kinds = {}
        for r in table:
            kinds[(r["kind"], r["source"])] = kinds.get((r["kind"], r["source"]), 0) + 1
        print("symbols", len(symbols), "corrections", len(table), kinds)
        print("unmatched", len(unmatched), "unparsed", len(unparsed))


if __name__ == "__main__":
    main()
