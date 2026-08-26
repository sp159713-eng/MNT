"""Whether the names in the book still deserve to be in it, and what is outside.

Carried over from NNS, where the idea was tried first. Reimplemented rather than
imported: MNT is standalone by design, and a production book that breaks because
an experiment was edited is not production. The thresholds below and the shape
of the checks are the same; the data layer and the framing are MNT's.

THE PRIMARY USE HERE IS THE BOOK, NOT THE CANDIDATES

In NNS this screen answers "what else could I look at". In production the more
valuable question is the reverse: has anything ALREADY HELD quietly stopped
being holdable. A name that has drifted into penny territory, dried up, or
appeared on an exchange surveillance list is a position to reconsider, and
nothing else in this project would notice - config.UNIVERSE is a fixed list and
has no opinion about whether its members are still liquid.

ON WIDENING THE UNIVERSE, WHICH THIS FILE MAKES EASIER AND SHOULD NOT

config.py records a measurement: fitting on 150 names instead of 29 cost the
book 1.32 points, because the extra names are less liquid, more gap-prone and
share less structure with the ones worth holding. The candidate half of this
module does not overturn that and is not evidence against it. It is a way to
look at a name before considering it, not a recommendation to hold more names.
Anything that survives the screen is a candidate for MEASUREMENT, not for the
book.

WHAT THE COMPUTED FLAGS ARE, AND ARE NOT

They are not fraud detection. Nothing computable from daily bars can establish
that a company is a scam - that is an accounting and disclosure question,
answered by auditors, regulators and the exchange. What bars can show is the
shape a manipulated stock usually has: no liquidity, repeated closes pinned to a
circuit band, unbroken climbs, sessions where nothing traded. Those are flags on
the TRADING PATTERN. A name that trips none of them has not been cleared; it has
only failed to trip these specific tripwires.

THE SURVEILLANCE LISTS ARE THE PART THAT IS ACTUALLY AUTHORITATIVE

NSE and BSE publish ASM and GSM - the exchanges naming what they are watching.
Stored in a CSV. fetch_surveillance() fills it from NSE's own report endpoints,
which do answer a request carrying a browser User-Agent and the report page as
Referer - www.nseindia.com returns 403 to this client, the report endpoints
return 200. Useful, and still not something to depend on: the fetch is an
explicit action that reports its own failure, and a hand-made CSV in the same
format works identically. The file records its fetch date, because a list from
six weeks ago reports a newly listed name as clean.

AND A MISSING FILE IS NOT A PASS

surveillance() returns None when the file is absent, and None renders as NOT
CHECKED everywhere it surfaces - never as "not listed". A missing file means the
question was never asked, and showing that as clean would turn the most
authoritative check here into a silent yes.
"""

from __future__ import annotations

import datetime
import os

import numpy as np
import pandas as pd

import config

SURVEILLANCE_PATH = os.path.join(config.MODEL_DIR, "surveillance.csv")

WINDOW = 250            # one trading year, the window every rate is measured on
PENNY_PRICE = 20.0      # where moving the price gets cheap
MIN_TURNOVER = 5_00_00_000.0   # 5 crore median daily traded value
CIRCUIT_BANDS = (5.0, 10.0, 20.0)
CIRCUIT_EPSILON = 0.25
CIRCUIT_LIMIT = 8
RUN_LIMIT = 14          # consecutive UP closes; see _longest_run
DEAD_LIMIT = 5
MIN_ROWS = 400

# Liquid NSE names outside config.UNIVERSE. Copied from NNS's pool.py rather
# than imported, for the standalone rule above. Nothing here is endorsed - it is
# a list of things liquid enough to be worth screening at all.
CANDIDATES = """
ABB ACC ADANIENT ADANIGREEN ADANIPOWER ALKEM AMBUJACEM APOLLOHOSP ASHOKLEY
ASTRAL AUBANK AUROPHARMA BAJAJ-AUTO BAJAJFINSV BALKRISIND BANKBARODA BEL
BERGEPAINT BHARATFORG BHEL BIOCON BOSCHLTD BPCL BRITANNIA CANBK CHOLAFIN
COFORGE COLPAL CONCOR CUMMINSIND DABUR DALBHARAT DIVISLAB DLF DRREDDY
EICHERMOT ESCORTS EXIDEIND FEDERALBNK GAIL GODREJCP GODREJPROP HAVELLS HDFCAMC
HDFCLIFE HINDALCO HINDPETRO ICICIGI ICICIPRULI IDFCFIRSTB IGL INDHOTEL INDIGO
INDUSINDBK INDUSTOWER IOC IPCALAB IRCTC JINDALSTEL JSWENERGY JUBLFOOD LICHSGFIN
LTTS LUPIN M&M MANAPPURAM MARICO MAXHEALTH MFSL MOTHERSON MPHASIS MRF
MUTHOOTFIN NATIONALUM NAUKRI NAVINFLUOR NMDC OBEROIRLTY OFSS OIL PAGEIND
PERSISTENT PETRONET PFC PIDILITIND PIIND PNB POLYCAB RAMCOCEM RECLTD SAIL
SBILIFE SHREECEM SHRIRAMFIN SIEMENS SRF SUNTV SUPREMEIND SYNGENE TATACHEM
TATACOMM TATACONSUM TATAELXSI TATAPOWER TECHM THERMAX TORNTPHARM TRENT TVSMOTOR
UBL UNIONBANK UNITDSPR UPL VEDL VOLTAS WHIRLPOOL ZYDUSLIFE
""".split()


NSE_ASM_URL = "https://www.nseindia.com/api/reportASM"
NSE_GSM_URL = "https://www.nseindia.com/api/reportGSM"

# NSE refuses an obvious script but answers a request that looks like it came
# from its own report page. www.nseindia.com itself returns 403 to this client;
# the two report endpoints return 200 with these headers.
NSE_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                   " (KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/reports/asm",
}

# Past this the file is reported as stale. NSE republishes daily, and a list
# from last month will report a name as not listed when it was added since -
# which is indistinguishable from a pass.
STALE_AFTER_DAYS = 7


def _nse_json(url: str, timeout: int):
    import gzip
    import json
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
    response = opener.open(
        urllib.request.Request(url, headers=NSE_HEADERS), timeout=timeout)
    raw = response.read()
    if response.headers.get("Content-Encoding") == "gzip":
        raw = gzip.decompress(raw)
    return json.loads(raw)


def fetch_surveillance(timeout: int = 20) -> dict:
    """Refresh the ASM/GSM file from NSE. Never raises; returns {ok, rows, error}.

    A partial answer counts as a failure and the existing file is left alone.
    Writing only the half that downloaded would drop every name on the other
    list, and each of those would then read as "checked, not listed" - the
    silent pass this module exists to prevent.
    """
    rows = {}
    try:
        asm = _nse_json(NSE_ASM_URL, timeout)
        for block in ("longterm", "shortterm"):
            for row in (asm.get(block) or {}).get("data") or []:
                symbol = str(row.get("symbol") or "").strip().upper()
                if symbol:
                    rows[symbol] = ("ASM",
                                    str(row.get("asmSurvIndicator") or "").strip())

        gsm = _nse_json(NSE_GSM_URL, timeout)
        for row in gsm or []:
            symbol = str(row.get("symbol") or "").strip().upper()
            if symbol:
                # GSM outranks ASM on a name carrying both: it is the more
                # serious listing.
                rows[symbol] = ("GSM", str(row.get("gsmStage") or "").strip())
    except Exception as exc:
        return {"ok": False, "rows": 0,
                "error": f"{type(exc).__name__}: {str(exc)[:120]}"}

    if not rows:
        return {"ok": False, "rows": 0,
                "error": "NSE answered but listed nothing - treated as a "
                         "failure, not as an empty market"}

    stamp = datetime.date.today().isoformat()
    os.makedirs(os.path.dirname(SURVEILLANCE_PATH), exist_ok=True)
    temporary = f"{SURVEILLANCE_PATH}.tmp"
    pd.DataFrame([{"symbol": s, "list": kind, "stage": stage, "fetched": stamp}
                  for s, (kind, stage) in sorted(rows.items())]
                 ).to_csv(temporary, index=False)
    os.replace(temporary, SURVEILLANCE_PATH)
    return {"ok": True, "rows": len(rows), "error": None}


def surveillance_age_days() -> int | None:
    """How old the stored list says it is, or None when it carries no date."""
    if not os.path.exists(SURVEILLANCE_PATH):
        return None
    try:
        frame = pd.read_csv(SURVEILLANCE_PATH)
        if "fetched" not in frame.columns or frame.empty:
            return None
        fetched = datetime.date.fromisoformat(str(frame["fetched"].iloc[0]))
    except Exception:
        return None
    return (datetime.date.today() - fetched).days


def surveillance() -> dict | None:
    """{symbol: "ASM II"} from the CSV, or None when it is absent.

    None means NOT CHECKED. Every caller must render it that way - see the
    module docstring.
    """
    if not os.path.exists(SURVEILLANCE_PATH):
        return None
    try:
        frame = pd.read_csv(SURVEILLANCE_PATH)
    except Exception:
        return None
    if "symbol" not in frame.columns:
        return None

    out = {}
    for _, row in frame.iterrows():
        name = str(row["symbol"]).strip().upper().replace(".NS", "")
        if not name or name == "NAN":
            continue
        listing = str(row.get("list", "")).strip().upper()
        stage = str(row.get("stage", "")).strip()
        out[name] = " ".join(part for part in (listing, stage)
                             if part and part.upper() != "NAN")
    return out


def _longest_run(returns: np.ndarray) -> tuple[int, int]:
    """Longest unbroken climb and longest unbroken slide, as (up, down).

    Separate because only the climb is evidence here. Counting runs in either
    direction flagged TCS in NNS for falling twelve sessions straight - a large
    cap grinding lower, which is a downtrend and not a stock being pushed.
    """
    best = {1: 0, -1: 0}
    run = previous = 0
    for value in returns:
        sign = 1 if value > 0 else (-1 if value < 0 else 0)
        if sign == 0:
            run, previous = 0, 0
            continue
        run = run + 1 if sign == previous else 1
        previous = sign
        best[sign] = max(best[sign], run)
    return best[1], best[-1]


def check(symbol: str, frame: pd.DataFrame, watch: dict | None) -> dict:
    """Screen one name. `watch` is surveillance(); pass None through unchanged
    rather than substituting {}, or a missing list reads as a clean bill."""
    flags, stats = [], {}
    bare = symbol.strip().upper().replace(".NS", "")

    if frame is None or len(frame) < 2:
        return {"symbol": bare, "flags": ["no price history"], "severity": "avoid",
                "listed": None, "checked_surveillance": watch is not None,
                "stats": {}}

    recent = frame.tail(WINDOW)
    close = recent["close"].to_numpy(dtype=float)
    volume = recent["volume"].to_numpy(dtype=float)
    last = float(close[-1])

    stats["price"] = last
    stats["rows"] = len(frame)

    # np.median propagates NaN, and this feed carries gaps in volume - RELIANCE
    # alone has four missing bars in a recent year and 119 across its history.
    # A NaN median would then be compared against MIN_TURNOVER, and `nan < x` is
    # False, so the least liquid names on the book would have sailed through the
    # liquidity check reporting "clean". Non-finite bars are dropped, and if
    # NOTHING usable is left the answer is None - which flags, rather than
    # passing. An unanswerable question must not read as a pass; that is the
    # same rule the surveillance file follows.
    traded = close * volume
    traded = traded[np.isfinite(traded)]
    turnover = float(np.median(traded)) if traded.size else None
    stats["turnover"] = turnover
    stats["volume_missing"] = int(len(volume) - np.isfinite(volume).sum())

    if len(frame) < MIN_ROWS:
        flags.append(f"only {len(frame)} sessions of history")
    if last < PENNY_PRICE:
        flags.append(f"penny price {last:,.2f}")
    if turnover is None:
        flags.append("turnover unknown - no usable volume")
    elif turnover < MIN_TURNOVER:
        flags.append(f"median turnover {turnover / 1e7:,.2f} Cr/day")

    # A NaN volume is an unrecorded session, not a session where nothing traded.
    # Counting the two together would invent dead days out of feed gaps.
    dead = int((volume == 0).sum())
    stats["dead"] = dead
    if dead > DEAD_LIMIT:
        flags.append(f"{dead} sessions with no trades")

    returns = np.diff(close) / close[:-1] * 100.0 if len(close) > 1 else np.array([])
    hits = 0
    for band in CIRCUIT_BANDS:
        hits += int((np.abs(np.abs(returns) - band) <= CIRCUIT_EPSILON).sum())
    stats["circuit"] = hits
    if hits >= CIRCUIT_LIMIT:
        flags.append(f"{hits} closes on a circuit band")

    up_run, down_run = _longest_run(returns)
    stats["run_up"], stats["run_down"] = up_run, down_run
    if up_run >= RUN_LIMIT:
        flags.append(f"{up_run} sessions climbing without a down day")

    # No liquidity and no history make a name untradeable at any size; the rest
    # make it odd. Only the first kind outranks the second.
    hard = any(f.startswith(("penny", "median turnover", "turnover unknown",
                             "only", "no price")) for f in flags)
    severity = "avoid" if hard else ("caution" if flags else "clean")

    return {
        "symbol": bare,
        "flags": flags,
        "severity": severity,
        # None = not checked. "" = checked, not listed. A string = listed.
        "listed": (watch.get(bare, "") if watch is not None else None),
        "checked_surveillance": watch is not None,
        "stats": stats,
    }


def verdict(result: dict) -> str:
    """One phrase for a table cell, surveillance first when it applies."""
    if result["listed"]:
        return f"LISTED {result['listed']}"
    suffix = "" if result["checked_surveillance"] else " (ASM/GSM not checked)"
    if result["severity"] == "clean":
        return "no flags" + suffix
    return f"{len(result['flags'])} flag(s)" + suffix


def scan(symbols, progress=None) -> list:
    """Screen a list of bare NSE symbols against cache, then network.

    Deliberately does not consult the model. It ranks by its own target and has
    never been asked whether a name is tradeable, and dressing a rank up as a
    safety opinion would be the docstring claiming something the code does not
    do.
    """
    import data as data_module

    watch = surveillance()
    rows = []
    for index, symbol in enumerate(symbols, start=1):
        if progress is not None:
            progress(index, len(symbols), symbol)
        try:
            frame = data_module.fetch(symbol, quiet=True)
        except Exception:
            frame = None
        result = check(symbol, frame, watch)
        stats = result["stats"]
        rows.append({
            "symbol": result["symbol"],
            "price": stats.get("price", 0.0),
            "turnover": stats.get("turnover"),
            "severity": result["severity"],
            "listed": result["listed"],
            "verdict": verdict(result),
            "flags": "; ".join(result["flags"]),
        })

    order = {"avoid": 0, "caution": 1, "clean": 2}
    rows.sort(key=lambda r: (not bool(r["listed"]), order.get(r["severity"], 3),
                             -(r["turnover"] or 0.0)))
    return rows


def book() -> list:
    """Screen what is actually held. The reason this module exists."""
    return scan(list(config.UNIVERSE))


def candidates() -> list:
    """Screen the names outside the book. Read the module docstring first."""
    held = {s.upper() for s in config.UNIVERSE}
    return scan([s for s in CANDIDATES if s.upper() not in held])


def describe_source() -> str:
    """Where the list came from and how old it is. Age is part of the answer: a
    stale list reports a newly added name as not listed, which reads as a pass."""
    watch = surveillance()
    if watch is None:
        return ("ASM/GSM list NOT LOADED - nothing below has been checked "
                "against it. Run `py -3.13 screen.py --update`, or download "
                f"the lists from nseindia.com and save as {SURVEILLANCE_PATH} "
                "with columns symbol,list,stage.")

    age = surveillance_age_days()
    if age is None:
        return (f"ASM/GSM list loaded: {len(watch)} symbols. Age unknown - the "
                "file carries no fetch date, so it may predate anything.")
    if age > STALE_AFTER_DAYS:
        return (f"ASM/GSM list is {age} days old ({len(watch)} symbols). NSE "
                "republishes daily, so a name added since would show as not "
                "listed. Update before trusting a clean row.")
    return (f"ASM/GSM list loaded: {len(watch)} symbols under surveillance, "
            f"fetched {'today' if age == 0 else f'{age} day(s) ago'}.")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--candidates", action="store_true",
                        help="screen names outside the book instead of in it")
    parser.add_argument("--update", action="store_true",
                        help="refresh the ASM/GSM list from NSE first")
    args = parser.parse_args()

    if args.update:
        result = fetch_surveillance()
        if result["ok"]:
            print(f"ASM/GSM updated: {result['rows']} symbols.")
        else:
            print(f"ASM/GSM update FAILED: {result['error']}\n"
                  "  The previous file, if any, is untouched.")

    print(describe_source() + "\n")
    rows = candidates() if args.candidates else book()
    print(f"{'SYMBOL':<14}{'PRICE':>10}{'TURNOVER':>12}  {'SCREEN':<9}WHY")
    for row in rows:
        turnover = ("unknown" if row["turnover"] is None
                    else f"{row['turnover'] / 1e7:,.1f}Cr")
        print(f"{row['symbol']:<14}{row['price']:>10,.2f}{turnover:>12}  "
              f"{row['severity']:<9}"
              f"{row['verdict'] if row['listed'] else row['flags']}")

    flagged = sum(1 for r in rows if r["severity"] != "clean")
    listed = sum(1 for r in rows if r["listed"])
    print(f"\n{len(rows)} screened, {flagged} with flags, {listed} listed. "
          "Clean means no flag tripped, not cleared.")


if __name__ == "__main__":
    main()
