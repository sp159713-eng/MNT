from __future__ import annotations

import json
import ssl
import urllib.parse
import urllib.request

_CONTEXT = None


def _context():
    global _CONTEXT
    if _CONTEXT is None:
        context = ssl.create_default_context()
        context.verify_flags &= ~ssl.VERIFY_X509_STRICT
        _CONTEXT = context
    return _CONTEXT

BASE = ("https://groww.in/v1/api/charting_service/v2/chart/exchange/{exchange}"
        "/segment/CASH/{symbol}/{bucket}")
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

SPANS = {
    "1D": ("daily", 5),
    "1W": ("weekly", 15),
    "1M": ("monthly", 60),
}


def available(span: str) -> bool:
    return span in SPANS


def candles(symbol: str, span: str, exchange: str = "NSE",
            timeout: int = 20) -> list[tuple[int, float]]:
    if span not in SPANS:
        raise ValueError(f"groww has no bucket for {span!r}")
    bucket, minutes = SPANS[span]
    url = BASE.format(exchange=exchange,
                      symbol=urllib.parse.quote(symbol.strip().upper()),
                      bucket=bucket)
    url = f"{url}?intervalInMinutes={minutes}&minimal=true"

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Accept": "*/*"})
    with urllib.request.urlopen(request, timeout=timeout,
                                context=_context()) as response:
        payload = json.loads(response.read().decode("utf-8"))

    rows = []
    for entry in payload.get("candles") or []:
        if not entry or len(entry) < 2 or entry[1] is None:
            continue
        rows.append((int(entry[0]), float(entry[1])))
    rows.sort(key=lambda row: row[0])
    return rows


def series(symbol: str, span: str, exchange: str = "NSE"):
    import datetime as dt

    rows = candles(symbol, span, exchange)
    closes = [price for _stamp, price in rows]
    labels = [dt.datetime.fromtimestamp(stamp).strftime("%d %b %H:%M")
              for stamp, _price in rows]
    return closes, labels


def main() -> None:
    import sys

    symbol = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"
    for span in SPANS:
        try:
            closes, labels = series(symbol, span)
        except Exception as error:                              # noqa: BLE001
            print(f"{span:<4} failed: {error}")
            continue
        if not closes:
            print(f"{span:<4} no candles")
            continue
        change = ((closes[-1] / closes[0]) - 1.0) * 100.0 if closes[0] else 0.0
        print(f"{span:<4} {len(closes):>4} points  {labels[0]} .. "
              f"{labels[-1]}  {closes[-1]:,.2f}  {change:+.2f}%")


if __name__ == "__main__":
    main()
