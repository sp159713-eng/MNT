import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import corrections

OUT = ROOT / "data" / "corrections.json"
MANUAL = [("AJANTPHARM", "2013-09-17"), ("BEL", "2017-03-16"), ("CUMMINSIND", "2011-09-20"),
          ("ENGINERSIN", "2016-12-30"), ("HEG", "2026-09-07"), ("KPRMILL", "2016-11-29"),
          ("NATIONALUM", "2011-03-15"), ("OIL", "2017-01-12"), ("OIL", "2018-03-27"),
          ("ONGC", "2011-02-08"), ("PERSISTENT", "2015-03-10"), ("PIIND", "2013-04-12"),
          ("TCI", "2016-08-26"), ("VBL", "2019-07-25"), ("VSTIND", "2024-09-06")]

table = [r for r in json.loads(OUT.read_text()) if not r.get("manual")]
for symbol, date in MANUAL:
    d = pd.read_csv(ROOT / "data_cache" / "nse" / "adj" / f"{symbol}.csv", parse_dates=["timestamp"]).set_index("timestamp")
    d = corrections.apply(d, symbol, "nse")
    pos = d.index.get_loc(pd.Timestamp(date))
    ratio = float(d["close"].iloc[pos] / d["close"].iloc[pos - 1])
    table.append({"symbol": symbol, "ex_date": date, "factor": round(ratio, 6), "kind": "demerger",
                  "source": "nse", "jump_date": date, "source_ratio_check": round(ratio, 6), "manual": True})
table.sort(key=lambda r: (r["symbol"], r["ex_date"], r["source"]))
OUT.write_text(json.dumps(table, indent=1))
print(len(table))
