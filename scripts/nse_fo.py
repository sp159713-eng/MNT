import argparse
import io
import json
import sys
import zipfile
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nse_archive as na

ROOT = na.ROOT
FO_DIR = ROOT / "fo"
RAW_PART = ROOT / "raw" / "part"
MISSING = FO_DIR / "missing.json"
INDUSTRY = ROOT / "industry.csv"
INDUSTRY_URL = "https://nsearchives.nseindia.com/content/indices/ind_niftytotalmarket_list.csv"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
FO_COLS = ["date", "symbol", "fut_oi", "fut_close", "und_close", "call_oi", "put_oi", "fut_vol"]
na.UA = UA
na.MISSING = MISSING


def old_url(d):
    mon = na.MONS[d.month - 1]
    return (f"https://nsearchives.nseindia.com/content/historical/DERIVATIVES/"
            f"{d.year}/{mon}/fo{d.day:02d}{mon}{d.year}bhav.csv.zip")


def new_url(d):
    return f"https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"


def part_url(d):
    return f"https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_{d:%d%m%Y}.csv"


def read_zip_csv(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        raw = z.read(z.namelist()[0])
    df = pd.read_csv(io.BytesIO(raw), dtype=str, index_col=False)
    df.columns = [c.strip() for c in df.columns]
    return df


def num(s):
    return pd.to_numeric(s, errors="coerce")


def reduce_frame(sym, kind, expiry, close, und, oi, vol, d):
    df = pd.DataFrame({"symbol": sym.str.strip(), "kind": kind, "expiry": expiry,
                       "close": num(close), "und": und, "oi": num(oi), "vol": num(vol)})
    fut = df[df["kind"] == "F"]
    if fut.empty:
        return None
    near = fut.sort_values("expiry").groupby("symbol", sort=False).first()
    out = pd.DataFrame({
        "fut_oi": fut.groupby("symbol")["oi"].sum(),
        "fut_vol": fut.groupby("symbol")["vol"].sum(),
        "fut_close": near["close"],
        "und_close": near["und"],
    })
    out["call_oi"] = df[df["kind"] == "C"].groupby("symbol")["oi"].sum()
    out["put_oi"] = df[df["kind"] == "P"].groupby("symbol")["oi"].sum()
    out[["call_oi", "put_oi"]] = out[["call_oi", "put_oi"]].fillna(0.0)
    out["und_close"] = out["und_close"].fillna(out["fut_close"])
    out = out.reset_index()
    out.insert(0, "date", pd.Timestamp(d))
    return out[FO_COLS]


def parse_old(data, d):
    df = read_zip_csv(data)
    df = df[df["INSTRUMENT"].str.strip().isin(["FUTSTK", "OPTSTK"])]
    opt = df["OPTION_TYP"].str.strip()
    kind = np.where(df["INSTRUMENT"].str.strip() == "FUTSTK", "F",
                    np.where(opt == "CE", "C", np.where(opt == "PE", "P", "X")))
    expiry = pd.to_datetime(df["EXPIRY_DT"].str.strip(), format="%d-%b-%Y", errors="coerce")
    close = df["CLOSE"]
    return reduce_frame(df["SYMBOL"], pd.Series(kind, index=df.index), expiry, close,
                        np.nan, df["OPEN_INT"], df["CONTRACTS"], d)


def parse_new(data, d):
    df = read_zip_csv(data)
    tp = df["FinInstrmTp"].str.strip()
    df = df[tp.isin(["STF", "STO"])]
    tp = df["FinInstrmTp"].str.strip()
    opt = df["OptnTp"].str.strip()
    kind = np.where(tp == "STF", "F", np.where(opt == "CE", "C", np.where(opt == "PE", "P", "X")))
    expiry = pd.to_datetime(df["XpryDt"].str.strip(), errors="coerce")
    return reduce_frame(df["TckrSymb"], pd.Series(kind, index=df.index), expiry, df["ClsPric"],
                        num(df["UndrlygPric"]), df["OpnIntrst"], df["TtlTradgVol"], d)


def fetch_fo(d):
    order = [(old_url, parse_old), (new_url, parse_new)]
    if d > na.OLD_LAST:
        order.reverse()
    for url, parser in order:
        data = na.fetch(url(d))
        if data is not None:
            return parser(data, d)
    return None


def year_path(y):
    return FO_DIR / f"fo_{y}.pkl"


def load_year(y):
    p = year_path(y)
    if p.exists():
        return pd.read_pickle(p)
    return pd.DataFrame({"date": pd.Series(dtype="datetime64[ns]"), **{c: pd.Series(dtype=object if c == "symbol" else float) for c in FO_COLS[1:]}})


def flush_year(y, parts):
    if not parts:
        return
    FO_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.concat([load_year(y)] + parts, ignore_index=True)
    df = df.drop_duplicates(["date", "symbol"], keep="last").sort_values(["date", "symbol"])
    tmp = year_path(y).with_suffix(".tmp")
    df.to_pickle(tmp)
    tmp.replace(year_path(y))


def part_path(d):
    return RAW_PART / str(d.year) / f"fao_participant_oi_{d:%d%m%Y}.csv"


def get_industry():
    data = na.fetch(INDUSTRY_URL)
    if data is None:
        print("industry: 404")
        return
    INDUSTRY.parent.mkdir(parents=True, exist_ok=True)
    INDUSTRY.write_bytes(data)
    print(f"industry: {len(pd.read_csv(INDUSTRY))} rows")


def cmd_download(args):
    what = set(args.what.split(","))
    if "industry" in what:
        get_industry()
    start = date.fromisoformat(args.start)
    end = date.today() if args.end == "today" else date.fromisoformat(args.end)
    today = date.today()
    missing = na.load_missing()
    stats = {"new": 0, "err": 0}
    have = {}
    pending = {}
    n = 0
    for d in na.weekdays(start, end):
        n += 1
        y = d.year
        if y not in have:
            have[y] = set(load_year(y)["date"].dt.date)
            pending[y] = []
        try:
            if "fo" in what and d not in have[y]:
                key = f"fo:{d.isoformat()}"
                recheck = (today - d).days <= 7
                if key not in missing or recheck:
                    na.time.sleep(na.DELAY)
                    frame = fetch_fo(d)
                    if frame is None:
                        missing[key] = datetime.now().isoformat(timespec="seconds")
                    else:
                        pending[y].append(frame)
                        have[y].add(d)
                        missing.pop(key, None)
                        stats["new"] += 1
            if "part" in what:
                na.get_one(f"part:{d.isoformat()}", part_url(d), part_path(d), missing, today, stats)
        except Exception as e:
            print(f"ERR {d} {e}", flush=True)
            stats["err"] += 1
        if n % 100 == 0:
            for yy in list(pending):
                flush_year(yy, pending[yy])
                pending[yy] = []
            na.save_missing(missing)
            print(f"{d} days={n} new={stats['new']} err={stats['err']} missing={len(missing)}", flush=True)
    for yy in pending:
        flush_year(yy, pending[yy])
    na.save_missing(missing)
    print(f"done days={n} new={stats['new']} err={stats['err']} missing={len(missing)}", flush=True)


def net(a, b):
    return (a - b) / (a + b).replace(0, np.nan)


def parse_part(path, d):
    df = pd.read_csv(path, skiprows=1, dtype=str, index_col=False, skipinitialspace=True)
    df.columns = [str(c).strip() for c in df.columns]
    df = df.dropna(subset=[df.columns[0]])
    df[df.columns[0]] = df[df.columns[0]].str.strip().str.upper()
    df = df.set_index(df.columns[0])
    for c in df.columns:
        df[c] = num(df[c].str.strip())

    def g(who, col):
        try:
            return float(df.loc[who, col])
        except KeyError:
            return np.nan

    def opt_net(who):
        cl, pl = g(who, "Option Index Call Long"), g(who, "Option Index Put Long")
        cs, ps = g(who, "Option Index Call Short"), g(who, "Option Index Put Short")
        den = cl + pl + cs + ps
        return (cl + ps - cs - pl) / den if den else np.nan

    def fut_net(who, kind):
        a, b = g(who, f"Future {kind} Long"), g(who, f"Future {kind} Short")
        return (a - b) / (a + b) if (a + b) else np.nan

    return {"date": pd.Timestamp(d),
            "fii_idx_fut_net": fut_net("FII", "Index"),
            "dii_idx_fut_net": fut_net("DII", "Index"),
            "fii_stk_fut_net": fut_net("FII", "Stock"),
            "client_idx_fut_net": fut_net("CLIENT", "Index"),
            "fii_idx_opt_net": opt_net("FII")}


def cmd_build(args):
    parts = [load_year(int(p.stem.split("_")[1])) for p in sorted(FO_DIR.glob("fo_[0-9]*.pkl"))]
    if parts:
        allfo = pd.concat(parts, ignore_index=True).sort_values(["date", "symbol"])
        allfo.to_pickle(FO_DIR / "fo_all.pkl")
        print(f"fo_all rows={len(allfo)} {allfo['date'].min().date()}..{allfo['date'].max().date()}")
    rows = []
    for p in sorted(RAW_PART.glob("*/fao_participant_oi_*.csv")):
        try:
            d = datetime.strptime(p.stem.rsplit("_", 1)[1], "%d%m%Y").date()
            rows.append(parse_part(p, d))
        except Exception as e:
            print(f"ERR {p.name} {e}")
    if rows:
        part = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
        part.to_pickle(FO_DIR / "participant.pkl")
        print(f"participant rows={len(part)} {part['date'].min().date()}..{part['date'].max().date()}")


def cmd_status(args):
    for name, col in [("fo_all.pkl", "date"), ("participant.pkl", "date")]:
        p = FO_DIR / name
        if p.exists():
            df = pd.read_pickle(p)
            print(f"{name}: rows={len(df)} {df[col].min().date()}..{df[col].max().date()} days={df[col].nunique()}")
        else:
            print(f"{name}: absent")
    for y in sorted(FO_DIR.glob("fo_[0-9]*.pkl")):
        df = pd.read_pickle(y)
        print(f"{y.name}: rows={len(df)} days={df['date'].nunique()}")
    raws = list(RAW_PART.glob("*/*.csv"))
    print(f"raw part files={len(raws)}")
    print(f"industry.csv: {'present' if INDUSTRY.exists() else 'absent'}")
    m = na.load_missing()
    print(f"missing keys={len(m)}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download")
    d.add_argument("--start", default="2008-01-01")
    d.add_argument("--end", default="today")
    d.add_argument("--what", default="fo,part,industry")
    d.set_defaults(fn=cmd_download)
    b = sub.add_parser("build")
    b.set_defaults(fn=cmd_build)
    s = sub.add_parser("status")
    s.set_defaults(fn=cmd_status)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
