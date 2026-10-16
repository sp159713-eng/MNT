import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "data" / "corrections.json"
MANUAL = [("nse", "AJANTPHARM", "2013-09-17"), ("nse", "BEL", "2017-03-16"),
          ("nse", "CUMMINSIND", "2011-09-20"), ("nse", "ENGINERSIN", "2016-12-30"),
          ("nse", "HEG", "2026-09-07"), ("nse", "KPRMILL", "2016-11-29"),
          ("nse", "NATIONALUM", "2011-03-15"), ("nse", "OIL", "2017-01-12"),
          ("nse", "OIL", "2018-03-27"), ("nse", "ONGC", "2011-02-08"),
          ("nse", "PERSISTENT", "2015-03-10"), ("nse", "PIIND", "2013-04-12"),
          ("nse", "TCI", "2016-08-26"), ("nse", "VBL", "2019-07-25"),
          ("nse", "VSTIND", "2024-09-06"),
          ("yahoo", "CDSL", "2017-07-03"), ("yahoo", "INDIAMART", "2019-10-29"),
          ("yahoo", "INDIAMART", "2020-11-17"), ("yahoo", "MASTEK", "2015-06-12"),
          ("yahoo", "MOTILALOFS", "2024-01-01"), ("yahoo", "PATANJALI", "2020-01-27"),
          ("yahoo", "TRENT", "2026-01-01")]

table = [r for r in json.loads(OUT.read_text()) if not r.get("manual")]
OUT.write_text(json.dumps(table, indent=1))

import corrections


def series(source, symbol):
    if source == "nse":
        return ROOT / "data_cache" / "nse" / "adj" / f"{symbol}.csv"
    return ROOT / "data_cache" / f"{symbol}_NS_1d_adj.csv"


for source, symbol, date in MANUAL:
    d = pd.read_csv(series(source, symbol), parse_dates=["timestamp"]).set_index("timestamp")
    d = corrections.apply(d, symbol, source)
    pos = d.index.get_loc(pd.Timestamp(date))
    ratio = float(d["close"].iloc[pos] / d["close"].iloc[pos - 1])
    table.append({"symbol": symbol, "ex_date": date, "factor": round(ratio, 6), "kind": "demerger",
                  "source": source, "jump_date": date, "source_ratio_check": round(ratio, 6), "manual": True})
table.sort(key=lambda r: (r["symbol"], r["ex_date"], r["source"]))
OUT.write_text(json.dumps(table, indent=1))
print(len(table))
