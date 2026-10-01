import argparse
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"D:\project\MNT\data_cache\nse")
RAW_BHAV = ROOT / "raw" / "bhav"
RAW_MTO = ROOT / "raw" / "mto"
MISSING = ROOT / "missing.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
OLD_LAST = date(2024, 7, 5)
MONS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
DELAY = 0.35
COLS = ["date", "symbol", "series", "open", "high", "low", "close", "prevclose",
        "volume", "value", "trades", "isin", "deliv_qty", "deliv_pct"]


def bhav_name(d):
    if d <= OLD_LAST:
        return f"cm{d.day:02d}{MONS[d.month - 1]}{d.year}bhav.csv.zip"
    return f"BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"


def bhav_url(d):
    if d <= OLD_LAST:
        return (f"https://nsearchives.nseindia.com/content/historical/EQUITIES/"
                f"{d.year}/{MONS[d.month - 1]}/{bhav_name(d)}")
    return f"https://nsearchives.nseindia.com/content/cm/{bhav_name(d)}"


def mto_name(d):
    return f"MTO_{d:%d%m%Y}.DAT"


def mto_url(d):
    return f"https://nsearchives.nseindia.com/archives/equities/mto/{mto_name(d)}"


def sec_name(d):
    return f"sec_bhavdata_full_{d:%d%m%Y}.csv"


def sec_url(d):
    return f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv"


def fetch(url):
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            last = e
        except Exception as e:
            last = e
        if attempt < 3:
            time.sleep(2 * (3 ** attempt))
    raise RuntimeError(f"{url}: {last}")


def load_missing():
    if MISSING.exists():
        try:
            return json.loads(MISSING.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_missing(m):
    MISSING.parent.mkdir(parents=True, exist_ok=True)
    tmp = MISSING.with_suffix(".tmp")
    tmp.write_text(json.dumps(m, sort_keys=True), encoding="utf-8")
    tmp.replace(MISSING)


def weekdays(start, end):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def get_one(key, url, path, missing, today, stats):
    if path.exists():
        return "have"
    recheck = (today - date.fromisoformat(key.split(":")[1])).days <= 7
    if key in missing and not recheck:
        return "missing"
    time.sleep(DELAY)
    try:
        data = fetch(url)
    except Exception as e:
        print(f"ERR {key} {e}", flush=True)
        stats["err"] += 1
        return "err"
    if data is None:
        missing[key] = datetime.now().isoformat(timespec="seconds")
        return "missing"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)
    missing.pop(key, None)
    stats["new"] += 1
    return "got"


def cmd_download(args):
    start = date.fromisoformat(args.start)
    end = date.today() if args.end == "today" else date.fromisoformat(args.end)
    today = date.today()
    missing = load_missing()
    stats = {"new": 0, "err": 0}
    n = 0
    for d in weekdays(start, end):
        n += 1
        try:
            bpath = RAW_BHAV / str(d.year) / bhav_name(d)
            b = get_one(f"bhav:{d.isoformat()}", bhav_url(d), bpath, missing, today, stats)
            if b in ("have", "got"):
                mpath = RAW_MTO / str(d.year) / mto_name(d)
                spath = RAW_MTO / str(d.year) / sec_name(d)
                if spath.exists():
                    m = "have"
                else:
                    m = get_one(f"mto:{d.isoformat()}", mto_url(d), mpath, missing, today, stats)
                    if m == "missing":
                        get_one(f"sec:{d.isoformat()}", sec_url(d), spath, missing, today, stats)
        except Exception as e:
            print(f"ERR {d} {e}", flush=True)
            stats["err"] += 1
        if n % 100 == 0:
            save_missing(missing)
            print(f"{d} days={n} new={stats['new']} err={stats['err']} missing={len(missing)}", flush=True)
    save_missing(missing)
    print(f"done days={n} new={stats['new']} err={stats['err']} missing={len(missing)}", flush=True)


def num(s):
    return pd.to_numeric(s, errors="coerce")


def parse_old(path, d):
    with zipfile.ZipFile(path) as z:
        raw = z.read(z.namelist()[0])
    df = pd.read_csv(io.BytesIO(raw), dtype=str, index_col=False)
    df.columns = [c.strip().upper() for c in df.columns]
    out = pd.DataFrame(index=df.index)
    out["date"] = pd.Timestamp(d)
    out["symbol"] = df["SYMBOL"].str.strip()
    out["series"] = df["SERIES"].str.strip()
    for k, c in [("open", "OPEN"), ("high", "HIGH"), ("low", "LOW"), ("close", "CLOSE"),
                 ("prevclose", "PREVCLOSE"), ("volume", "TOTTRDQTY"), ("value", "TOTTRDVAL"),
                 ("trades", "TOTALTRADES")]:
        out[k] = num(df[c]) if c in df else np.nan
    out["isin"] = df["ISIN"].str.strip() if "ISIN" in df else None
    return out


def parse_new(path, d):
    with zipfile.ZipFile(path) as z:
        raw = z.read(z.namelist()[0])
    df = pd.read_csv(io.BytesIO(raw), dtype=str, index_col=False)
    df.columns = [c.strip() for c in df.columns]
    out = pd.DataFrame(index=df.index)
    out["date"] = pd.Timestamp(d)
    out["symbol"] = df["TckrSymb"].str.strip()
    out["series"] = df["SctySrs"].str.strip()
    for k, c in [("open", "OpnPric"), ("high", "HghPric"), ("low", "LwPric"), ("close", "ClsPric"),
                 ("prevclose", "PrvsClsgPric"), ("volume", "TtlTradgVol"), ("value", "TtlTrfVal"),
                 ("trades", "TtlNbOfTxsExctd")]:
        out[k] = num(df[c]) if c in df else np.nan
    out["isin"] = df["ISIN"].str.strip() if "ISIN" in df else None
    return out


def parse_mto(path, d):
    rows = []
    with open(path, "r", encoding="latin-1") as f:
        for line in f:
            if not line.startswith("20,"):
                continue
            p = [x.strip() for x in line.strip().split(",")]
            if len(p) < 7:
                continue
            rows.append((p[2], p[3], p[5], p[6]))
    df = pd.DataFrame(rows, columns=["symbol", "series", "deliv_qty", "deliv_pct"])
    df["deliv_qty"] = num(df["deliv_qty"])
    df["deliv_pct"] = num(df["deliv_pct"])
    df["date"] = pd.Timestamp(d)
    return df


def parse_sec(path, d):
    df = pd.read_csv(path, dtype=str, skipinitialspace=True, index_col=False)
    df.columns = [c.strip() for c in df.columns]
    out = pd.DataFrame({
        "symbol": df["SYMBOL"].str.strip(),
        "series": df["SERIES"].str.strip(),
        "deliv_qty": num(df["DELIV_QTY"].str.strip()),
        "deliv_pct": num(df["DELIV_PER"].str.strip()),
    })
    out["date"] = pd.Timestamp(d)
    return out


OLD_RE = re.compile(r"^cm(\d{2})([A-Z]{3})(\d{4})bhav\.csv\.zip$")
NEW_RE = re.compile(r"^BhavCopy_NSE_CM_0_0_0_(\d{8})_F_0000\.csv\.zip$")
MTO_RE = re.compile(r"^MTO_(\d{8})\.DAT$")
SEC_RE = re.compile(r"^sec_bhavdata_full_(\d{8})\.csv$")


def file_date(name):
    m = OLD_RE.match(name)
    if m:
        return date(int(m.group(3)), MONS.index(m.group(2)) + 1, int(m.group(1))), "old"
    m = NEW_RE.match(name)
    if m:
        return datetime.strptime(m.group(1), "%Y%m%d").date(), "new"
    m = MTO_RE.match(name)
    if m:
        return datetime.strptime(m.group(1), "%d%m%Y").date(), "mto"
    m = SEC_RE.match(name)
    if m:
        return datetime.strptime(m.group(1), "%d%m%Y").date(), "sec"
    return None, None


def cmd_build(args):
    years = sorted({p.name for p in RAW_BHAV.glob("*") if p.is_dir()})
    for y in years:
        files = {}
        for p in sorted((RAW_BHAV / y).iterdir()):
            d, kind = file_date(p.name)
            if d is None:
                continue
            if d in files and kind == "new":
                continue
            files[d] = (p, kind)
        bh = []
        for d, (p, kind) in sorted(files.items()):
            try:
                bh.append(parse_old(p, d) if kind == "old" else parse_new(p, d))
            except Exception as e:
                print(f"BAD {p.name}: {e}", flush=True)
        if not bh:
            continue
        df = pd.concat(bh, ignore_index=True)
        dl = []
        mdir = RAW_MTO / y
        if mdir.exists():
            mtos, secs = {}, {}
            for p in mdir.iterdir():
                d, kind = file_date(p.name)
                if kind == "mto":
                    mtos[d] = p
                elif kind == "sec":
                    secs[d] = p
            for d in sorted(set(mtos) | set(secs)):
                try:
                    dl.append(parse_mto(mtos[d], d) if d in mtos else parse_sec(secs[d], d))
                except Exception as e:
                    print(f"BAD delivery {d}: {e}", flush=True)
        if dl:
            dv = pd.concat(dl, ignore_index=True)
            dv = dv.drop_duplicates(["date", "symbol", "series"], keep="last")
            df = df.merge(dv, on=["date", "symbol", "series"], how="left")
        else:
            df["deliv_qty"] = np.nan
            df["deliv_pct"] = np.nan
        df = df.drop_duplicates(["date", "symbol", "series"], keep="last")
        df = df[COLS].sort_values(["date", "symbol", "series"]).reset_index(drop=True)
        df["date"] = pd.to_datetime(df["date"])
        for c in ["volume", "value"]:
            df[c] = df[c].astype(float)
        out = ROOT / f"eq_{y}.pkl"
        df.to_pickle(out)
        print(f"{y}: {len(df)} rows, {df['date'].nunique()} days -> {out.name}", flush=True)


def cmd_status(args):
    for label, base in [("bhav", RAW_BHAV), ("mto", RAW_MTO)]:
        print(f"raw {label}:")
        if base.exists():
            for yd in sorted(base.glob("*")):
                if yd.is_dir():
                    print(f"  {yd.name}: {sum(1 for _ in yd.iterdir())}")
    m = load_missing()
    print(f"missing days recorded: {len(m)}")
    print("pickles:")
    for p in sorted(ROOT.glob("eq_*.pkl")):
        try:
            print(f"  {p.name}: {len(pd.read_pickle(p))} rows")
        except Exception as e:
            print(f"  {p.name}: unreadable {e}")


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    d = sp.add_parser("download")
    d.add_argument("--start", default="2008-01-01")
    d.add_argument("--end", default="today")
    d.set_defaults(fn=cmd_download)
    sp.add_parser("build").set_defaults(fn=cmd_build)
    sp.add_parser("status").set_defaults(fn=cmd_status)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
