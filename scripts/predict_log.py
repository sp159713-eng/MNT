import json
import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config
import data as data_module
import features as features_module
import production

PATH = os.path.join(config.MODEL_DIR, "predictions.json")
HORIZON = 20


def close_on(symbol, date):
    frame = data_module.fetch(symbol, quiet=True)
    window = frame[frame.index <= pd.Timestamp(date)]
    return float(window["close"].iloc[-1]), window.index[-1]


def load():
    if os.path.exists(PATH):
        with open(PATH, encoding="utf-8") as handle:
            return json.load(handle)
    return []


def save(rows):
    with open(PATH, "w", encoding="utf-8") as handle:
        json.dump(rows, handle, indent=1)


def make():
    panel = features_module.cross_sectionalize(
        features_module.build_panel(), require_target=False)
    ranked = production.scored(panel)
    date = pd.Timestamp(ranked["timestamp"].iloc[0])
    if any(row["made"] == str(date.date()) for row in load()):
        print(f"already recorded for {date.date()}")
        return
    top = ranked.iloc[0]
    symbol = top["symbol"]
    price, _ = close_on(symbol, date)
    nifty, _ = close_on(data_module.NIFTY, date)
    row = {"made": str(date.date()), "symbol": symbol, "price": price,
           "nifty": nifty, "score": float(top["score"]),
           "top5": ranked["symbol"].head(5).tolist(),
           "horizon_days": HORIZON,
           "claim": f"{symbol} beats the Nifty over the next {HORIZON} trading days"}
    rows = load()
    rows.append(row)
    save(rows)
    print(json.dumps(row, indent=1))


def check():
    rows = load()
    for row in rows:
        if "result" in row:
            continue
        made = pd.Timestamp(row["made"])
        frame = data_module.fetch(row["symbol"], quiet=True)
        later = frame[frame.index > made]
        if len(later) < row["horizon_days"]:
            print(f"{row['symbol']} made {row['made']}: {len(later)}/{row['horizon_days']} days so far")
            continue
        end = later.index[row["horizon_days"] - 1]
        price, _ = close_on(row["symbol"], end)
        nifty, _ = close_on(data_module.NIFTY, end)
        stock = price / row["price"] - 1.0
        index = nifty / row["nifty"] - 1.0
        row["result"] = {"end": str(end.date()), "stock": round(stock, 4),
                         "nifty": round(index, 4), "right": stock > index}
        print(f"{row['symbol']} {row['made']} -> {end.date()}: stock {stock:+.2%}, "
              f"Nifty {index:+.2%}, {'RIGHT' if stock > index else 'WRONG'}")
    save(rows)


if __name__ == "__main__":
    {"make": make, "check": check}[sys.argv[1] if len(sys.argv) > 1 else "check"]()
