import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
LIMIT = 0.25


def scan(folder, pattern):
    hits = []
    for p in sorted(folder.glob(pattern)):
        d = pd.read_csv(p, usecols=["timestamp", "high", "low", "close"])
        prev = d["close"].shift(1)
        gap = pd.to_datetime(d["timestamp"]).diff().dt.days
        move = d["close"] / prev - 1
        rng = (d["high"] - d["low"]) / d["close"]
        bad = (move.abs() > LIMIT) & (gap <= 30)
        for i in d.index[bad]:
            hits.append((p.stem, d.at[i, "timestamp"], float(move[i]), float(rng[i])))
    return hits


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "nse"
    if which == "nse":
        hits = scan(ROOT / "data_cache" / "nse" / "adj", "*.csv")
    else:
        hits = scan(ROOT / "data_cache", "*_NS_1d_adj.csv")
    hits.sort(key=lambda h: h[1])
    recent = [h for h in hits if h[1] >= "2011-01-01"]
    print(f"{which}: {len(hits)} moves over {LIMIT:.0%} ({len(recent)} since 2011) in {len({h[0] for h in hits})} symbols")
    for h in recent[-25:]:
        print(f"  {h[0]:<22} {h[1]}  {h[2]:+.1%}  range {h[3]:.1%}")


if __name__ == "__main__":
    main()
