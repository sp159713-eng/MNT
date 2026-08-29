from __future__ import annotations

import argparse
import glob
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import data as data_module


def cache_end(symbol: str) -> str:
    hits = glob.glob(os.path.join(data_module.CACHE_DIR, f"{symbol}_*_1d_adj.csv"))
    if not hits:
        hits = glob.glob(os.path.join(data_module.CACHE_DIR,
                                      f"{symbol}_NS_1d_adj.csv"))
    if not hits:
        return ""
    try:
        return str(pd.read_csv(hits[0], usecols=[0]).iloc[-1, 0])[:10]
    except Exception:                                           # noqa: BLE001
        return ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--older-than", default="")
    parser.add_argument("--pause", type=float, default=0.3)
    args = parser.parse_args()

    cutoff = args.older_than
    if not cutoff:
        ends = [cache_end(s) for s in config.UNIVERSE]
        ends = [e for e in ends if e]
        cutoff = max(ends) if ends else ""
    print(f"refreshing anything ending before {cutoff}", flush=True)

    stale = [s for s in config.UNIVERSE if cache_end(s) < cutoff]
    print(f"{len(stale)} of {len(config.UNIVERSE)} symbols are behind",
          flush=True)

    fixed = failed = 0
    for index, symbol in enumerate(stale, start=1):
        before = cache_end(symbol)
        try:
            data_module.clear_memo()
            frame = data_module.fetch(symbol, refresh=True, quiet=True)
            after = str(frame.index[-1])[:10] if len(frame) else "empty"
            fixed += 1
            print(f"[{index:>3}/{len(stale)}] {symbol:<14} {before} -> {after}",
                  flush=True)
        except Exception as error:                              # noqa: BLE001
            failed += 1
            print(f"[{index:>3}/{len(stale)}] {symbol:<14} FAILED "
                  f"{type(error).__name__}", flush=True)
        time.sleep(args.pause)

    print(f"\nrefreshed {fixed} | failed {failed}", flush=True)


if __name__ == "__main__":
    main()
