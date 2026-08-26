"""NSE price bars, fetched once and cached on disk.

WHY THIS EXISTS

A backtest that re-downloads on every run is a backtest you will stop running.
More importantly it is one whose results drift: vendors restate history, and a
strategy that looked fine on Tuesday's download can look different on Friday's
for reasons that have nothing to do with your code. Everything is cached under
data_cache/ and reused until you explicitly refresh.

WHAT THE PRICES MEAN

Bars come back split- and bonus-adjusted (`auto_adjust=True`). This is not
optional on NSE. Corporate actions here are frequent and large - a 1:1 bonus
halves the raw price overnight, and an unadjusted series hands your strategy a
fake -50% return with a fake volume spike next to it. Every momentum rule ever
written will trade on that, and the backtest will be nonsense.

The consequence to keep in mind: adjusted prices are not the prices you could
have traded at. They are restated with hindsight. That is the correct choice for
measuring returns and the wrong one for computing rupee position sizes or
checking whether a stock was above Rs 100 at the time. Use `unadjusted=True`
when the question is about the actual traded price.

INTRADAY IS RENTED, NOT OWNED

Yahoo serves at most 60 days of intraday bars, and only 730 days at 1h. If your
rules need years of 5-minute data, this file cannot give it to you and no
argument to it will help - that is a paid-vendor problem. Daily history goes
back decades and is fine.

Run with:  py -3.13 data.py RELIANCE TCS INFY
           py -3.13 data.py --interval 15m --refresh RELIANCE
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np
import pandas as pd

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_cache")

# Yahoo's ceiling per interval. Asking for more silently returns less, which is
# worse than failing, so the request is clamped and the clamp is announced.
MAX_DAYS = {"1m": 7, "2m": 60, "5m": 60, "15m": 60, "30m": 60, "60m": 730,
            "1h": 730, "1d": 20000, "1wk": 20000, "1mo": 20000}

NIFTY = "^NSEI"          # the index, for benchmarking - has no .NS suffix
BANKNIFTY = "^NSEBANK"


def to_yahoo(symbol: str) -> str:
    """RELIANCE -> RELIANCE.NS. Indices and already-suffixed names pass through."""
    symbol = symbol.strip().upper()
    if symbol.startswith("^") or "." in symbol:
        return symbol
    return f"{symbol}.NS"


def sector(symbol: str) -> str:
    import yfinance as yf

    try:
        info = yf.Ticker(to_yahoo(symbol)).info
    except Exception:
        return ""
    if not isinstance(info, dict):
        return ""
    for key in ("sector", "sectorDisp", "industryDisp", "industry"):
        value = info.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def clean_bars(frame: pd.DataFrame, symbol: str = "", quiet: bool = True
               ) -> pd.DataFrame:
    """Remove bars that record something other than a trade.

    Yahoo's NSE history contains occasional garbage prints. BAJFINANCE on
    2005-07-28 is the specimen: a close of 252.95 between neighbours at 2.31,
    with zero volume. One bar, and it produced a 20-day forward return of
    +11,618% - enough on its own to triple the standard deviation of the target
    across a 152,000-row panel.

    The filter that does the work is spike-and-revert: a move over 50% that
    undoes itself the next day is not a move, it is a bad print. Real 50% days
    in a liquid large cap do exist, but they do not reverse completely within 24
    hours. The revert condition is what keeps this from eating genuine crashes
    and limit moves.

    ZERO VOLUME IS NOT A BAD BAR

    An earlier version deleted every zero-volume row, on the theory that if
    nothing traded the price is invented. Measured, that theory cost NESTLEIND
    1,245 bars - Yahoo simply does not report volume for it across 2005-2009,
    roughly 247 days a year, while carrying perfectly good prices. Missing
    volume is missing volume, not a missing trade. So volume of zero becomes
    NaN, which keeps the price and stops the two volume features from reading a
    fabricated zero as a real one.

    Worth noting the filters were not independent: every genuine glitch bar
    found in this universe had zero volume AND reverted, so spike-and-revert
    alone catches all of them and the volume rule was pure collateral damage.

    Applied on load rather than at download, so the cache stays raw and this
    logic can change without re-fetching a decade of history.
    """
    before = len(frame)
    frame = frame[frame["close"] > 0].copy()

    log_return = np.log(frame["close"]).diff()
    spike = log_return.abs() > np.log(1.5)
    reverts = (log_return + log_return.shift(-1)).abs() < np.log(1.2)
    frame = frame[~(spike & reverts)]

    frame.loc[frame["volume"] <= 0, "volume"] = np.nan

    dropped = before - len(frame)
    if dropped and not quiet:
        print(f"  {symbol}: dropped {dropped} bad bar(s)", file=sys.stderr)
    return frame


def _cache_path(symbol: str, interval: str, unadjusted: bool) -> str:
    tag = "raw" if unadjusted else "adj"
    safe = to_yahoo(symbol).replace("^", "IDX_").replace(".", "_")
    return os.path.join(CACHE_DIR, f"{safe}_{interval}_{tag}.csv")


def fetch(symbol: str, interval: str = "1d", period: str | None = None,
          start: str | None = None, end: str | None = None,
          unadjusted: bool = False, refresh: bool = False,
          clean: bool = True, quiet: bool = False) -> pd.DataFrame:
    """One symbol's bars, from cache when possible.

    Returns a frame indexed by timestamp with open/high/low/close/volume, and
    nothing else - no vendor column names leak past this function, so swapping
    Yahoo for a real feed later touches this file only.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = _cache_path(symbol, interval, unadjusted)

    if os.path.exists(path) and not refresh:
        frame = pd.read_csv(path, index_col=0, parse_dates=True)
        if len(frame):
            if clean:
                frame = clean_bars(frame, symbol, quiet)
            return _slice(frame, start, end)

    import yfinance as yf   # imported late: cache hits should not pay for it

    if period is None and start is None:
        cap = MAX_DAYS.get(interval, 60)
        period = "max" if cap > 3000 else f"{cap}d"
        if not quiet and cap <= 730:
            print(f"  {symbol}: {interval} history is capped at {cap} days by "
                  f"the vendor", file=sys.stderr)

    raw = yf.download(to_yahoo(symbol), interval=interval, period=period,
                      start=start, end=end, auto_adjust=not unadjusted,
                      progress=False, threads=False)

    if raw is None or raw.empty:
        raise RuntimeError(f"no data returned for {symbol} at {interval} - "
                           f"check the symbol, or the interval's history limit")

    # yfinance hands back a two-level column index when it feels like it.
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)

    frame = raw.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    frame.index.name = "timestamp"
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    frame.to_csv(path)                              # cache stays raw
    if clean:
        frame = clean_bars(frame, symbol, quiet)
    return _slice(frame, start, end)


def _slice(frame: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    if start:
        frame = frame[frame.index >= pd.Timestamp(start).tz_localize(frame.index.tz)]
    if end:
        frame = frame[frame.index <= pd.Timestamp(end).tz_localize(frame.index.tz)]
    return frame


def load(symbols: list[str], interval: str = "1d", **kwargs) -> dict[str, pd.DataFrame]:
    """Several symbols. Failures are reported and skipped, never silently dropped.

    A universe that quietly shrinks from 50 names to 43 because seven downloads
    failed is survivorship bias introduced by a network error, and it will not
    announce itself in the results.
    """
    out, failed = {}, []
    for symbol in symbols:
        try:
            out[symbol] = fetch(symbol, interval=interval, **kwargs)
        except Exception as error:                      # noqa: BLE001
            failed.append((symbol, str(error).split("\n")[0]))
            continue
        time.sleep(0.15)        # be unremarkable to the vendor
    if failed:
        print(f"\n{len(failed)} of {len(symbols)} symbols failed:", file=sys.stderr)
        for symbol, reason in failed:
            print(f"  {symbol}: {reason}", file=sys.stderr)
    return out


def close_panel(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Closes for every symbol in one frame, dates x symbols.

    Deliberately does NOT forward-fill. A missing bar means the stock did not
    trade - suspended, halted, or not yet listed - and filling it invents a
    price your strategy can then pretend to trade at.
    """
    return pd.DataFrame({s: f["close"] for s, f in frames.items()}).sort_index()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("symbols", nargs="*", default=["RELIANCE", "TCS", "INFY"])
    parser.add_argument("--interval", default="1d", choices=sorted(MAX_DAYS))
    parser.add_argument("--start", default=None)
    parser.add_argument("--refresh", action="store_true", help="ignore the cache")
    parser.add_argument("--unadjusted", action="store_true",
                        help="raw traded prices, not adjusted for splits/bonuses")
    args = parser.parse_args()

    frames = load(args.symbols, interval=args.interval, start=args.start,
                  refresh=args.refresh, unadjusted=args.unadjusted)
    if not frames:
        raise SystemExit("nothing loaded")

    print(f"\n{'symbol':<12}{'bars':>7}{'from':>13}{'to':>13}{'last close':>12}")
    print("-" * 57)
    for symbol, frame in frames.items():
        print(f"{symbol:<12}{len(frame):>7}"
              f"{frame.index[0].date().isoformat():>13}"
              f"{frame.index[-1].date().isoformat():>13}"
              f"{frame['close'].iloc[-1]:>12,.2f}")
    print(f"\ncached under {CACHE_DIR}")


if __name__ == "__main__":
    main()
