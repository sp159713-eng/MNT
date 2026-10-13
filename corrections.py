from __future__ import annotations

import hashlib
import json
import math
import os
import sys

import numpy as np
import pandas as pd

SPLIT_TOL = 0.15
DEMERGER_TOL = 0.05
WINDOW_DAYS = 5
PRICE_COLUMNS = ("open", "high", "low", "close")

_TABLE: dict | None = None
_VERSION: str | None = None


def _candidates() -> list[str]:
    here = os.path.dirname(os.path.abspath(__file__))
    out = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        out.append(os.path.join(meipass, "data", "corrections.json"))
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
        out += [os.path.join(base, "_internal", "data", "corrections.json"),
                os.path.join(base, "data", "corrections.json")]
    out.append(os.path.join(here, "data", "corrections.json"))
    return out


def _path() -> str | None:
    for candidate in _candidates():
        if os.path.exists(candidate):
            return candidate
    return None


def version() -> str:
    global _VERSION
    if _VERSION is None:
        path = _path()
        if path is None:
            _VERSION = "none"
        else:
            with open(path, "rb") as handle:
                _VERSION = hashlib.sha256(handle.read()).hexdigest()[:12]
    return _VERSION


def _table() -> dict:
    global _TABLE
    if _TABLE is None:
        _TABLE = {}
        path = _path()
        if path is not None:
            with open(path, encoding="utf-8") as handle:
                for row in json.load(handle):
                    _TABLE.setdefault(row["symbol"], []).append(row)
    return _TABLE


def apply(frame: pd.DataFrame, symbol: str, source: str) -> pd.DataFrame:
    rows = _table().get(symbol)
    if not rows or frame is None or len(frame) < 2 or "close" not in frame:
        return frame
    close = frame["close"].to_numpy(dtype=float)
    index = frame.index
    stamps = index.tz_localize(None) if index.tz is not None else index
    factors = np.ones(len(frame))
    for row in rows:
        if row["source"] not in (source, "both"):
            continue
        ex = pd.Timestamp(row["ex_date"])
        lo = stamps.searchsorted(ex - pd.Timedelta(days=WINDOW_DAYS), side="left")
        hi = stamps.searchsorted(ex + pd.Timedelta(days=WINDOW_DAYS), side="right")
        expected = row["factor"]
        tol = DEMERGER_TOL if row["kind"] == "demerger" else SPLIT_TOL
        best = None
        for j in range(max(lo, 1), hi):
            if close[j - 1] <= 0 or close[j] <= 0:
                continue
            gap = abs(math.log(close[j] / close[j - 1] / expected))
            if gap < tol and (best is None or gap < best[0]):
                best = (gap, j)
        if best is not None:
            factors[:best[1]] *= expected
    if np.all(factors == 1.0):
        return frame
    frame = frame.copy()
    for column in PRICE_COLUMNS:
        if column in frame:
            frame[column] = frame[column].to_numpy(dtype=float) * factors
    if "volume" in frame:
        frame["volume"] = frame["volume"].to_numpy(dtype=float) / factors
    return frame
