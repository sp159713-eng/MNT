import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
NSE = ROOT / "data_cache" / "nse"
ADJ = NSE / "adj"
CACHE = ROOT / "data_cache"
SERIES_RANK = {"EQ": 0, "BE": 1, "BZ": 2}
CHECK_SYMBOLS = ["RELIANCE", "TCS", "INFY", "ITC", "SBIN", "HDFCBANK", "MARUTI", "BAJFINANCE", "TATASTEEL", "WIPRO"]
BAD = re.compile(r'[\\/:*?"<>|]')


def safe(sym):
    return BAD.sub("_", sym)


def load():
    frames = []
    for p in sorted(NSE.glob("eq_*.pkl")):
        df = pd.read_pickle(p)
        df = df[df["series"].isin(SERIES_RANK)]
        frames.append(df[["date", "symbol", "series", "open", "high", "low", "close", "prevclose", "volume", "deliv_pct"]])
    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"])
    df["rank"] = df["series"].map(SERIES_RANK)
    df = df.sort_values(["symbol", "date", "rank"], kind="stable")
    df = df.drop_duplicates(["symbol", "date"], keep="first")
    return df.reset_index(drop=True)


KNOWN = np.array([1.25, 4 / 3, 1.5, 5 / 3, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 15.0, 20.0, 25.0, 50.0, 100.0])
LO = np.concatenate([1 / KNOWN[::-1], KNOWN])


def snap(drop):
    lg = np.log(drop.to_numpy())
    cand = np.log(LO)
    idx = np.abs(lg[:, None] - cand[None, :]).argmin(axis=1)
    near = LO[idx]
    tol = np.where(near > 1.6, 0.05, 0.03)
    tol = np.where(near < 1 / 1.6, 0.05, tol)
    tol = np.where((near < 1) & (near >= 1 / 1.6), 0.03, tol)
    ok = np.abs(lg - np.log(near)) < tol
    return near, ok


def factors(df):
    g = df.groupby("symbol", sort=False)
    prev_close = g["close"].shift(1)
    prev_date = g["date"].shift(1)
    valid = ((prev_close > 0) & (df["close"] > 0)).to_numpy()
    drop = (prev_close / df["close"]).where(valid, 1.0).fillna(1.0)
    near, ok = snap(drop)
    move = (df["close"] / prev_close - 1).abs()
    rng = (df["high"] - df["low"]) / df["close"]
    calm = (rng < 0.5 * move).to_numpy()
    big = pd.Series(ok & valid & calm & (np.abs(np.log(drop.to_numpy())) > 0.2), index=df.index)
    gap = (df["date"] - prev_date).dt.days
    stale = big & (gap > 30)
    act = big & ~stale
    f = pd.Series(1.0, index=df.index)
    f[act] = 1.0 / near[act.to_numpy()]
    return f, int(act.sum()), int(stale.sum())


def multipliers(df, f):
    lf = np.log(f)
    fut = lf.groupby(df["symbol"], sort=False).transform(lambda s: s[::-1].cumsum()[::-1]) - lf
    return np.exp(fut)


def adjust():
    df = load()
    f, actions, stale = factors(df)
    m = multipliers(df, f)
    out = pd.DataFrame({
        "symbol": df["symbol"],
        "timestamp": df["date"].dt.strftime("%Y-%m-%d"),
        "open": df["open"] * m,
        "high": df["high"] * m,
        "low": df["low"] * m,
        "close": df["close"] * m,
        "volume": df["volume"] / m,
        "deliv_pct": df["deliv_pct"],
    })
    ADJ.mkdir(parents=True, exist_ok=True)
    for old in ADJ.glob("*.csv"):
        old.unlink()
    cols = ["timestamp", "open", "high", "low", "close", "volume", "deliv_pct"]
    symbols = []
    last = {}
    for sym, grp in out.groupby("symbol", sort=True):
        grp[cols].to_csv(ADJ / f"{safe(sym)}.csv", index=False)
        symbols.append(sym)
        last[sym] = grp["timestamp"].iloc[-1]
    dates = sorted(df["date"].dt.strftime("%Y-%m-%d").unique())
    cutoff = dates[max(0, len(dates) - 11)]
    alive = [s for s in symbols if last[s] >= cutoff]
    dead = [s for s in symbols if last[s] < cutoff]
    index = {
        "symbols": symbols,
        "actions": actions,
        "stale_gaps": stale,
        "first_date": dates[0],
        "last_date": dates[-1],
        "alive": alive,
        "dead": dead,
    }
    (ADJ / "_index.json").write_text(json.dumps(index), encoding="utf-8")
    print(f"symbols={len(symbols)} alive={len(alive)} dead={len(dead)} actions={actions} stale_gaps={stale}")


def check():
    ok = True
    print(f"{'symbol':<11}{'overlap':>8}{'first_yr':>10}{'last_yr':>10}{'max_jump':>10}  result")
    for sym in CHECK_SYMBOLS:
        yp = CACHE / f"{sym}_NS_1d_adj.csv"
        np_ = ADJ / f"{sym}.csv"
        if not yp.exists() or not np_.exists():
            print(f"{sym:<11} missing file")
            ok = False
            continue
        y = pd.read_csv(yp)
        y["d"] = pd.to_datetime(y.iloc[:, 0], utc=True, errors="coerce").dt.tz_localize(None).dt.normalize()
        y = y.dropna(subset=["d"]).drop_duplicates("d").set_index("d")["close"]
        n = pd.read_csv(np_)
        n["d"] = pd.to_datetime(n["timestamp"])
        n = n.set_index("d")["close"]
        r = (n / y).dropna()
        r = r[np.isfinite(r) & (r > 0)]
        if len(r) < 2:
            print(f"{sym:<11} no overlap")
            ok = False
            continue
        lr = np.log(r)
        jumps = lr.diff().abs()
        mj = float(jumps.max())
        first = float(r[r.index < r.index[0] + pd.Timedelta(days=365)].median())
        lastv = float(r[r.index > r.index[-1] - pd.Timedelta(days=365)].median())
        good = mj < 0.05
        ok = ok and good
        print(f"{sym:<11}{len(r):>8}{first:>10.3f}{lastv:>10.3f}{mj:>10.4f}  {'PASS' if good else 'FAIL ' + str(jumps.idxmax().date())}")
    print("ALL PASS" if ok else "CHECK FAILED")
    return ok


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "adjust":
        adjust()
    elif cmd == "check":
        sys.exit(0 if check() else 1)
    else:
        print("usage: nse_adjust.py adjust|check")
        sys.exit(2)
