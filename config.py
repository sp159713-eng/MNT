"""Every choice that is a choice, in one place.

The defaults here are not neutral. They are set against a single number: a
delivery round trip on NSE costs about 34bp (costs.py), and NNS's daily-
rebalanced ranker broke even at 6.5bp. Three of the four settings below exist
to attack that gap from the turnover side, because the gap is too wide to close
from the signal side.

  TARGET_HORIZON = 20   Earn the edge over a month, pay for the trade once.
                        A 1-day horizon must clear 34bp every day; a 20-day
                        horizon must clear it every 20 days. Same signal,
                        twentieth of the bill.

  UNIVERSE, yours       Ships empty: every name is one the operator adds in
                        Settings, and any of them can be removed. NNS measured
                        the cost of a wide pool directly - fitting on 150 names
                        instead of 29 cost the book 1.32 points - so a short
                        list of names you actually want to hold beats a long
                        one.

  Long only             Shorting Indian cash equity means SLB or single-stock
                        futures. Neither is free and neither is modelled here,
                        so a short book would be quietly subsidised by the
                        backtest. Excluded rather than faked.
"""

from __future__ import annotations

import os
import sys

# Frozen builds unpack their code into a temporary directory, so __file__ points
# somewhere that is deleted on exit. Models and cached bars have to live beside
# the EXECUTABLE instead, or a packaged build would train into a temp folder and
# lose everything the moment it closed. Same rule NNS's config uses.
FROZEN = getattr(sys, "frozen", False)
BASE_DIR = (os.path.dirname(sys.executable) if FROZEN
            else os.path.dirname(os.path.abspath(__file__)))
MODEL_DIR = os.path.join(BASE_DIR, "artifacts")
CACHE_DIR = os.path.join(BASE_DIR, "data_cache")

BASE_UNIVERSE: list[str] = []

# Names the operator added themselves, kept in artifacts/ so they survive a
# rebuild - the bundle is replaced wholesale on every build, and a list stored
# inside it would be destroyed. Two lists, because the measurement in the header
# applies to only one of them: "universe" names are fitted, ranked and holdable
# and therefore inherit the 1.32-point warning about widening the pool;
# "watchlist" names are fetched and displayed and never reach the model.
STOCKS_PATH = os.path.join(MODEL_DIR, "stocks.json")

SYMBOL_MAX_LEN = 20


def valid_symbol(symbol: str) -> str:
    text = (symbol or "").strip().upper()
    if not text or len(text) > SYMBOL_MAX_LEN:
        return ""
    if not all(ch.isalnum() or ch in "&-.^" for ch in text):
        return ""
    return text


def _read_stocks() -> dict:
    import json

    empty = {"universe": [], "watchlist": [], "sectors": {}}
    try:
        with open(STOCKS_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return empty
    if not isinstance(data, dict):
        return empty
    out = {}
    for key in ("universe", "watchlist"):
        value = data.get(key)
        names = []
        if isinstance(value, list):
            for item in value:
                symbol = valid_symbol(item) if isinstance(item, str) else ""
                if symbol and symbol not in names:
                    names.append(symbol)
        out[key] = names
    sectors = {}
    raw = data.get("sectors")
    if isinstance(raw, dict):
        for item, label in raw.items():
            symbol = valid_symbol(item) if isinstance(item, str) else ""
            if symbol and isinstance(label, str) and label.strip():
                sectors[symbol] = label.strip()
    out["sectors"] = sectors
    return out


def _write_stocks(data: dict) -> None:
    import json

    os.makedirs(MODEL_DIR, exist_ok=True)
    tmp = STOCKS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    os.replace(tmp, STOCKS_PATH)


def _rebuild_universe() -> None:
    global UNIVERSE, WATCHLIST, USER_UNIVERSE, SECTORS

    stocks = _read_stocks()
    base = {s.upper() for s in BASE_UNIVERSE}
    USER_UNIVERSE = [s for s in stocks["universe"] if s not in base]
    UNIVERSE = list(BASE_UNIVERSE) + USER_UNIVERSE
    held = {s.upper() for s in UNIVERSE}
    WATCHLIST = [s for s in stocks["watchlist"] if s not in held]
    SECTORS = dict(stocks["sectors"])


USER_UNIVERSE: list[str] = []
WATCHLIST: list[str] = []
UNIVERSE: list[str] = list(BASE_UNIVERSE)
SECTORS: dict[str, str] = {}
_rebuild_universe()


def detect_sector(symbol: str) -> str:
    import data

    name = valid_symbol(symbol)
    return data.sector(name) if name else ""


def _with_sectors(names: list[str]) -> str:
    if not names:
        return "-"
    parts = []
    for name in names:
        label = SECTORS.get(name)
        parts.append(f"{name} ({label})" if label else name)
    return ", ".join(parts)


def add_stock(symbol: str, watchlist: bool = False,
              sector: str = "") -> tuple[bool, str]:
    name = valid_symbol(symbol)
    if not name:
        return False, f"{symbol!r} is not a usable symbol."
    if name.upper() in {s.upper() for s in BASE_UNIVERSE}:
        return False, f"{name} is already in the built-in universe."

    stocks = _read_stocks()
    target = "watchlist" if watchlist else "universe"
    other = "universe" if watchlist else "watchlist"
    if name in stocks[target]:
        return False, f"{name} is already on the {target}."
    stocks[other] = [s for s in stocks[other] if s != name]
    stocks[target] = stocks[target] + [name]
    if sector and sector.strip():
        stocks["sectors"][name] = sector.strip()
    _write_stocks(stocks)
    _rebuild_universe()
    label = stocks["sectors"].get(name)
    if label:
        return True, f"{name} ({label}) added to the {target}."
    return True, f"{name} added to the {target}."


def remove_stock(symbol: str) -> tuple[bool, str]:
    name = valid_symbol(symbol)
    if not name:
        return False, f"{symbol!r} is not a usable symbol."
    if name.upper() in {s.upper() for s in BASE_UNIVERSE}:
        return False, f"{name} is built in and cannot be removed here."

    stocks = _read_stocks()
    if name not in stocks["universe"] and name not in stocks["watchlist"]:
        return False, f"{name} was not added by you."
    for key in ("universe", "watchlist"):
        stocks[key] = [s for s in stocks[key] if s != name]
    stocks["sectors"].pop(name, None)
    _write_stocks(stocks)
    _rebuild_universe()
    return True, f"{name} removed."


# Keep in step with MyAppVersion in installer.iss - the update check compares
# this string against the newest GitHub release, so a build that ships with a
# stale number here announces an update to itself.
APP_VERSION = "1.0.6"

# owner/repo, e.g. "hariom/mnt". Empty means no update check runs at all and
# the Update button never appears - which is the correct behaviour until a
# repository with published releases actually exists.
#
# A PRIVATE repo answers the anonymous releases API with 404, so a private repo
# here needs a token (artifacts/update.json or MNT_GITHUB_TOKEN) - or point this
# at a small PUBLIC repo that exists only to carry the version tag, and set
# UPDATE_PAGE to the private download page. The browser's own GitHub session
# decides who may download; nothing secret then ships inside the exe.
UPDATE_REPO = "sp159713-eng/MNT"

# Where the Update button sends the operator. Empty means the release page of
# whatever UPDATE_REPO reported.
UPDATE_PAGE = ""

BENCHMARK = "^NSEI"

TARGET_HORIZON = 20         # trading days of forward return the model predicts
REBALANCE_EVERY = 20        # how often the book is allowed to trade
TOP_K = 6                   # names held
HOLD_BUFFER = 6             # keep a name while it stays inside the top (K+buffer)

# Dates, not fractions. A random split of a panel leaks tomorrow into today
# through every overlapping window, and produces a model that looks excellent
# and is worthless.
TRAIN_END = "2020-12-31"
VAL_END = "2023-06-30"
# 2008, not 2005, because ^NSEI's history on this feed begins 2007-09-17. With
# an earlier start, 90% of pre-2008 rows carry a zero-filled beta, correlation
# and relative-strength block - and zero is not "missing" after cross-sectional
# ranking, it is the median rank, so the model reads 20,912 fabricated
# observations as real ones. Three years of prices are not worth that. The 2008
# crash is still inside the window.
START = "2008-01-01"

SEED = 7

# Which model the book actually trades. Measured on 14 walk-forward folds, same
# features, same target, same turnover machinery:
#
#   lightgbm  +46bp excess, t 1.76, break-even 208bp, ~2 seconds a fold
#   nn        +23bp excess, t 0.97, break-even 118bp, ~5 seconds a fold
#   tabpfn     -8bp excess, t -0.28, break-even  17bp, ~3 MINUTES a fold
#
# Trees win, which is what the asset-pricing literature predicts for a panel
# with this signal-to-noise ratio. TabPFN was the only model tested that lost
# to simply holding the universe.
#
# The DEFAULT stays where the measurement put it. Settings can override it, and
# the override is remembered in artifacts/ui.json, but nothing about choosing a
# different model in the interface changes which one won on fourteen folds -
# switching is an experiment the operator is running, not a new default.
SIGNALS = ("lightgbm", "nn", "tabpfn")
DEFAULT_SIGNAL = "lightgbm"


def _remembered_signal() -> str:
    """The Settings choice, read straight from the preferences file.

    Read here rather than through theme.py because theme.py imports this
    module, and production.py reads PRODUCTION_SIGNAL at import time - so the
    value has to exist before anything that could form a cycle is loaded.
    """
    import json

    try:
        with open(os.path.join(MODEL_DIR, "ui.json"), encoding="utf-8") as handle:
            name = json.load(handle).get("signal")
    except Exception:
        return DEFAULT_SIGNAL
    return name if name in SIGNALS else DEFAULT_SIGNAL


PRODUCTION_SIGNAL = _remembered_signal()

# How the book is sized. Measured on the same 14 folds, config chosen on
# validation, test read once:
#
#   equal    +46bp excess, t 1.76, turnover 27%, break-even 208bp   <- best
#   rank     +44bp         t 1.52, turnover 38%, break-even 149bp
#   score    +32bp         t 1.12, turnover 46%, break-even 103bp
#   invvol    +6bp         t 0.23, turnover 42%, break-even  47bp
#
# Sizing by model confidence loses, and the mechanism is visible in the
# turnover column: scores drift between rebalances, so weighting by them churns
# money between names the book already holds. Equal weight ignores the
# magnitudes because the model was never trained to calibrate them - the label
# is a per-date rank, and any monotone transform of the scores gives the same
# ordering. NNS reached the same conclusion independently.
#
# Set to "score" or "rank" to override; sizing.py has the implementations.
SIZING_SCHEME = "equal"

# ---------------------------------------------------------------------------
# LightGBM, carried over from NNS unchanged except for thread count.
#
# The determinism flags are the part worth keeping. LightGBM builds histograms
# in parallel and sums floating-point gradients in whatever order the threads
# finish, so a fixed seed alone does NOT give a fixed answer - NNS measured the
# spread at about 0.4 accuracy points run to run, which is wider than most of
# the differences this project spends its time measuring. deterministic and
# force_row_wise fix the reduction order; pinning num_threads stops the result
# depending on how busy the machine was. A few seconds a fit against being able
# to trust a comparison is not a close trade.
# ---------------------------------------------------------------------------
GBM_LEARNING_RATE = 0.03
GBM_N_ESTIMATORS = 3000        # upper bound; early stopping picks the real count
GBM_NUM_LEAVES = 15            # small: daily bar data is noisy and easy to overfit
GBM_MAX_DEPTH = 4
GBM_MIN_CHILD_SAMPLES = 40
GBM_SUBSAMPLE = 0.8
GBM_SUBSAMPLE_FREQ = 1
GBM_COLSAMPLE = 0.7
GBM_REG_LAMBDA = 1.0
GBM_EARLY_STOPPING_ROUNDS = 100
GBM_SEED = 42
# NOT two because the machine has two cores - os.cpu_count() reports FOUR here,
# so the old comment was wrong. Left at 2 anyway, and deliberately: measured on
# this box, predictions are bit-identical at 1, 2 and 4 threads (deterministic
# and force_row_wise already fix the reduction order, so the count does not move
# the answer), while 4 threads was SLOWER than 2 on a fold-sized fit - thread
# overhead beats the parallelism at this data size. Raising it is therefore a
# speed question with a measurable answer, not a free win, and it has not been
# measured on a full walk-forward.
GBM_THREADS = 2


def verify_symbol(symbol: str, min_years: float = 7.0) -> tuple[bool, str]:
    import data

    name = valid_symbol(symbol)
    if not name:
        return False, f"{symbol!r} is not a usable symbol."
    try:
        bars = data.fetch(name, period="max")
    except Exception as exc:
        return False, f"{name}: no data ({exc})."
    if bars is None or len(bars) == 0:
        return False, f"{name}: {data.to_yahoo(name)} returned no bars."
    years = len(bars) / 250.0
    if years < min_years:
        return False, f"{name}: only {years:.1f}y of history, needs {min_years:.0f}y."
    return True, f"{name}: {years:.1f}y of history, {len(bars)} bars."


def search_symbols(query: str, limit: int = 8) -> list[dict]:
    text = (query or "").strip()
    if not text:
        return []

    try:
        import yfinance as yf

        quotes = yf.Search(text, max_results=25).quotes
    except Exception as exc:
        direct = valid_symbol(text)
        if direct:
            return [{"symbol": direct, "name": f"search unavailable: {exc}",
                     "exchange": "-"}]
        raise RuntimeError(f"search unavailable: {exc}")

    rows, seen = [], {}
    for quote in quotes:
        raw = (quote.get("symbol") or "").strip().upper()
        if not raw.endswith((".NS", ".BO")) or raw.startswith("0P"):
            continue
        symbol = valid_symbol(raw[:-3])
        if not symbol:
            continue
        listed = "NSE" if raw.endswith(".NS") else "BSE"
        if symbol in seen:
            if listed == "NSE":
                rows[seen[symbol]]["exchange"] = "NSE"
            continue
        seen[symbol] = len(rows)
        rows.append({
            "symbol": symbol,
            "name": quote.get("shortname") or quote.get("longname") or "",
            "exchange": listed,
        })

    direct = valid_symbol(text)
    if direct and direct not in seen:
        rows.insert(0, {"symbol": direct, "name": "use this ticker as typed",
                        "exchange": "-"})
    rows.sort(key=lambda row: {"-": 0, "NSE": 1, "BSE": 2}[row["exchange"]])
    return rows[:limit]


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Manage added stocks.")
    parser.add_argument("--add", metavar="SYMBOL")
    parser.add_argument("--remove", metavar="SYMBOL")
    parser.add_argument("--watchlist", action="store_true",
                        help="add to the watchlist instead of the traded universe")
    parser.add_argument("--force", action="store_true",
                        help="skip the history check when adding")
    parser.add_argument("--no-sector", action="store_true",
                        help="skip the sector lookup when adding")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    if args.add:
        if not args.force:
            ok, detail = verify_symbol(args.add)
            print(detail)
            if not ok:
                print("Not added. Pass --force to add anyway.")
                return
        label = "" if args.no_sector else detect_sector(args.add)
        if label:
            print(f"Sector: {label}")
        elif not args.no_sector:
            print("Sector: not reported by the feed.")
        ok, detail = add_stock(args.add, watchlist=args.watchlist,
                               sector=label)
        print(detail)
    elif args.remove:
        print(remove_stock(args.remove)[1])
    else:
        print(f"Added to universe ({len(USER_UNIVERSE)}): "
              f"{_with_sectors(USER_UNIVERSE)}")
        print(f"Watchlist ({len(WATCHLIST)}): {_with_sectors(WATCHLIST)}")


if __name__ == "__main__":
    main()
