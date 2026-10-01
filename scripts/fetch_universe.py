import json
import os
import sys
import time

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import config
import data as data_module

FOLDER = os.path.join(ROOT, "data_cache", "universe")
LISTING = os.path.join(FOLDER, "EQUITY_L.csv")
OUT = os.path.join(FOLDER, "yahoo_big.json")


def main():
    listing = pd.read_csv(LISTING)
    listing.columns = listing.columns.str.strip()
    symbols = sorted(listing.loc[listing["SERIES"].str.strip() == "EQ", "SYMBOL"]
                     .str.strip().unique())
    done, failed = [], []
    for index, symbol in enumerate(symbols, start=1):
        try:
            frame = data_module.fetch(symbol, start=config.START, quiet=True)
            if len(frame) >= 300:
                done.append(symbol)
        except Exception as error:
            failed.append((symbol, str(error)[:80]))
            time.sleep(1.0)
        if index % 100 == 0:
            print(f"{index}/{len(symbols)} ok {len(done)} failed {len(failed)}",
                  flush=True)
            with open(OUT, "w", encoding="utf-8") as handle:
                json.dump({"symbols": done, "failed": failed}, handle)
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump({"symbols": done, "failed": failed}, handle)
    print(f"done: {len(done)} usable (>=300 bars), {len(failed)} failed", flush=True)


if __name__ == "__main__":
    main()
