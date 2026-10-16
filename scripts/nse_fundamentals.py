import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "fundamentals"
CACHE = ROOT / "data_cache"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
TAGS = ("RevenueFromOperations", "Income", "ProfitBeforeTax", "ProfitLossForPeriod",
        "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
        "InterestEarned", "ProfitLossFromOrdinaryActivitiesBeforeTax",
        "ProfitLossForThePeriod", "BasicEarningsPerShareAfterExtraordinaryItems")
MIN_YEAR = 2016
DELAY = 0.35
TAG_RE = {t: re.compile(r"<[\w-]+:%s\s[^>]*contextRef=\"OneD\"[^>]*>([^<]*)<" % t) for t in TAGS}
API = "https://www.nseindia.com/api/"


def iso(d):
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y"):
        try:
            return datetime.strptime(d.strip().title(), fmt).strftime(
                "%Y-%m-%d %H:%M" if " " in d.strip() else "%Y-%m-%d")
        except ValueError:
            pass
    return None


def universe():
    syms = []
    for p in sorted(CACHE.glob("*_NS_1d_adj.csv")):
        syms.append(p.name[: -len("_NS_1d_adj.csv")])
    return syms


def session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json,text/html,*/*",
                      "Referer": "https://www.nseindia.com/"})
    s.get("https://www.nseindia.com/", timeout=30)
    return s


def get(s, url, params=None, tries=4):
    for i in range(tries):
        try:
            r = s.get(url, params=params, timeout=30)
            if r.status_code == 200:
                return r
            if r.status_code == 404:
                return None
            if r.status_code in (401, 403):
                s.get("https://www.nseindia.com/", timeout=30)
        except requests.RequestException:
            pass
        time.sleep(2 * (i + 1))
    return None


def parse_xbrl(text):
    out = {}
    for t, rx in TAG_RE.items():
        m = rx.search(text)
        if m:
            try:
                out[t] = float(m.group(1))
            except ValueError:
                pass
    return out


def list_json(s, ep, params):
    r = get(s, API + ep, params)
    if r is None:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def candidates(s, sym):
    nse_sym = sym.replace("_", "&")
    old = list_json(s, "corporates-financial-results",
                    {"index": "equities", "symbol": nse_sym, "period": "Quarterly"})
    new = list_json(s, "integrated-filing-results", {"index": "equities", "symbol": nse_sym})
    if old is None or new is None:
        return None
    out = []
    for row in old:
        if row.get("xbrl") and row.get("cumulative") == "Non-cumulative":
            out.append((iso(row.get("toDate") or ""), iso(row.get("filingDate") or ""),
                        row.get("consolidated") == "Consolidated", row["xbrl"]))
    for row in (new.get("data") or []) if isinstance(new, dict) else []:
        if row.get("xbrl") and "Financials" in (row.get("type") or ""):
            out.append((iso(row.get("qe_Date") or ""), iso(row.get("broadcast_Date") or ""),
                        row.get("consolidated") == "Consolidated", row["xbrl"]))
    return [c for c in out if c[0] and c[1] and int(c[0][:4]) >= MIN_YEAR]


def fetch_symbol(s, sym):
    cands = candidates(s, sym)
    if cands is None:
        return None
    keep = {}
    for to, filed, cons, url in cands:
        rank = (not cons, filed)
        if to not in keep or rank < keep[to][0]:
            keep[to] = (rank, filed, cons, url)
    res = []
    for to in sorted(keep):
        _, filed, cons, url = keep[to]
        time.sleep(DELAY)
        x = get(s, url)
        if x is None:
            continue
        vals = parse_xbrl(x.text)
        if vals:
            res.append({"toDate": to, "filingDate": filed, "consolidated": cons, "v": vals})
    return res


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    syms = universe()
    if len(sys.argv) > 1:
        syms = sys.argv[1:]
    s = session()
    done = fail = 0
    for i, sym in enumerate(syms):
        f = OUT / (sym + ".json")
        if f.exists():
            continue
        time.sleep(DELAY)
        res = fetch_symbol(s, sym)
        if res is None:
            fail += 1
            if fail % 20 == 0:
                s = session()
            continue
        f.write_text(json.dumps(res), encoding="utf-8")
        done += 1
        if done % 25 == 0:
            print(f"{i + 1}/{len(syms)} done={done} fail={fail}", flush=True)
    print(f"finished done={done} fail={fail}", flush=True)


if __name__ == "__main__":
    main()
