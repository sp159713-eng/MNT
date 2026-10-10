import hashlib
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CACHE = ROOT / "data_cache"
NEWS = ROOT / "artifacts" / "news_nse"
SCORES = NEWS / "scores.csv"
REPORT = ROOT / "artifacts" / "finbert_quicktest.txt"
REFERER = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
API = "https://www.nseindia.com/api/corporate-announcements"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
START_YEAR = 2012
HORIZON = 20
CLOSE_MIN = 15 * 60 + 30


def symbols():
    import production
    return production.liquid_names(30)


def quarters():
    today = date.today()
    out = []
    for year in range(START_YEAR, today.year + 1):
        for q in range(4):
            a = date(year, 3 * q + 1, 1)
            if a > today:
                break
            nxt = date(year + (q == 3), (3 * q + 4 - 1) % 12 + 1, 1)
            out.append((a, min(nxt - timedelta(days=1), today)))
    return out


def months(a, b):
    out = []
    cur = a
    while cur <= b:
        nxt = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
        out.append((cur, min(nxt - timedelta(days=1), b)))
        cur = nxt
    return out


class Nse:
    def __init__(self):
        import requests
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": REFERER,
        })
        self.last = 0.0
        self.warm()

    def warm(self):
        self.s.get(REFERER, timeout=30)

    def get(self, sym, a, b):
        for attempt in range(8):
            wait = 1.0 - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            try:
                r = self.s.get(API, params={
                    "index": "equities", "symbol": sym,
                    "from_date": a.strftime("%d-%m-%Y"),
                    "to_date": b.strftime("%d-%m-%Y")}, timeout=30)
            except Exception:
                time.sleep(2 ** attempt)
                self.warm()
                continue
            if r.status_code == 200:
                try:
                    data = r.json()
                except ValueError:
                    data = None
                if isinstance(data, list):
                    return data
                if isinstance(data, dict) and isinstance(data.get("data"), list):
                    return data["data"]
            if r.status_code in (401, 403, 429) or r.status_code == 200:
                time.sleep(min(60, 3 * 2 ** attempt))
                try:
                    self.warm()
                except Exception:
                    pass
                continue
            time.sleep(2 ** attempt)
        raise RuntimeError("NSE blocked persistently for %s %s-%s" % (sym, a, b))


def fetch():
    NEWS.mkdir(parents=True, exist_ok=True)
    nse = Nse()
    for sym in symbols():
        path = NEWS / (sym.replace("&", "_") + ".json")
        cache = json.loads(path.read_text()) if path.exists() else {}
        n_new = 0
        for a, b in quarters():
            key = "%s_%s" % (a, b)
            if key in cache and (b < date.today() - timedelta(days=5)):
                continue
            rows = nse.get(sym, a, b)
            if len(rows) >= 95:
                rows = []
                for ma, mb in months(a, b):
                    rows += nse.get(sym, ma, mb)
            cache[key] = rows
            n_new += 1
            if n_new % 8 == 0:
                path.write_text(json.dumps(cache))
        path.write_text(json.dumps(cache))
        total = sum(len(v) for v in cache.values())
        print("fetched", sym, total, flush=True)


def load_rows():
    recs = []
    for sym in symbols():
        path = NEWS / (sym.replace("&", "_") + ".json")
        if not path.exists():
            continue
        seen = set()
        for rows in json.loads(path.read_text()).values():
            for r in rows:
                sd = r.get("sort_date")
                if not sd:
                    continue
                desc = (r.get("desc") or "").strip()
                text = desc + ". " + (r.get("attchmntText") or "").strip()
                h = hashlib.md5(text.encode("utf-8")).hexdigest()[:12]
                k = (sd, h)
                if k in seen:
                    continue
                seen.add(k)
                recs.append((sym, sd, desc, text, h))
    df = pd.DataFrame(recs, columns=["sym", "sort_date", "desc", "text", "h"])
    df["key"] = df["sym"] + "|" + df["sort_date"] + "|" + df["h"]
    return df


def score(limit=None):
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    torch.set_num_threads(2)
    df = load_rows()
    done = pd.read_csv(SCORES) if SCORES.exists() else pd.DataFrame(
        columns=["key", "pos", "neg", "neu"])
    todo = df[~df["key"].isin(set(done["key"]))]
    if limit:
        todo = todo.sample(min(limit, len(todo)), random_state=0)
    print("rows %d, to score %d" % (len(df), len(todo)), flush=True)
    if todo.empty:
        return
    tok = AutoTokenizer.from_pretrained("ProsusAI/finbert")
    model = AutoModelForSequenceClassification.from_pretrained("ProsusAI/finbert").eval()
    labels = {v.lower(): k for k, v in model.config.id2label.items()}
    todo = todo.assign(n=todo["text"].str.len()).sort_values("n")
    keys = todo["key"].tolist()
    texts = todo["text"].tolist()
    out = []
    t0 = time.time()
    for i in range(0, len(texts), 32):
        enc = tok(texts[i:i + 32], truncation=True, max_length=512,
                  padding=True, return_tensors="pt")
        with torch.no_grad():
            p = torch.softmax(model(**enc).logits, dim=-1).numpy()
        for k, row in zip(keys[i:i + 32], p):
            out.append((k, row[labels["positive"]], row[labels["negative"]],
                        row[labels["neutral"]]))
        if (i // 32) % 20 == 19 or i + 32 >= len(texts):
            part = pd.DataFrame(out, columns=["key", "pos", "neg", "neu"])
            pd.concat([done, part]).to_csv(SCORES, index=False)
            print("scored %d/%d  %.1f/s" % (
                min(i + 32, len(texts)), len(texts),
                min(i + 32, len(texts)) / (time.time() - t0)), flush=True)


def load_prices(syms):
    close = {}
    for s in syms:
        f = CACHE / ("%s_NS_1d_adj.csv" % s)
        d = pd.read_csv(f, usecols=["timestamp", "close"])
        d["timestamp"] = pd.to_datetime(d["timestamp"]).dt.normalize()
        close[s] = d.drop_duplicates("timestamp").set_index("timestamp")["close"]
    return close


def rank_ic(feat, fwd, step_days):
    mask = feat.notna() & fwd.notna()
    f = feat.where(mask).iloc[::step_days]
    r = fwd.where(mask).iloc[::step_days]
    ok = mask.iloc[::step_days].sum(axis=1) >= 8
    ic = f.rank(axis=1).corrwith(r.rank(axis=1), axis=1)
    return ic[ok].dropna()


def summarize(ic):
    if len(ic) < 5:
        return "n=%d" % len(ic), None
    t = ic.mean() / (ic.std() / np.sqrt(len(ic)))
    yr = ic.groupby(ic.index.year).mean()
    return "IC %+.4f  t %+.2f  n %d  yrs+ %d/%d" % (
        ic.mean(), t, len(ic), (yr > 0).sum(), len(yr)), (ic.mean(), t, (yr > 0).sum(), len(yr))


def tstat(x):
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    if len(x) < 3 or x.std() == 0:
        return float("nan")
    return x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))


def prepare():
    df = load_rows()
    sc = pd.read_csv(SCORES).drop_duplicates("key")
    df = df.merge(sc, on="key", how="inner")
    df["sent"] = df["pos"] - df["neg"]
    df["label"] = df[["pos", "neg", "neu"]].idxmax(axis=1)
    df["ts"] = pd.to_datetime(df["sort_date"])
    syms = sorted(df["sym"].unique())
    close = load_prices(syms)
    cal = pd.DatetimeIndex(sorted(set().union(*[set(c.index) for c in close.values()])))
    cal = cal[cal >= pd.Timestamp("2011-06-01")]
    fwd = pd.DataFrame({s: close[s].shift(-HORIZON).div(close[s]).sub(1).reindex(cal) for s in syms})
    mom = pd.DataFrame({s: close[s].div(close[s].shift(252)).sub(1).reindex(cal) for s in syms})
    days = cal.values.astype("datetime64[D]")
    mins = (df["ts"].dt.hour * 60 + df["ts"].dt.minute).values
    dts = df["ts"].dt.normalize().values.astype("datetime64[D]")
    idx = np.searchsorted(days, dts, side="left")
    hit = (idx < len(days)) & (days[np.minimum(idx, len(days) - 1)] == dts)
    df["ix"] = np.where(hit & (mins > CLOSE_MIN), idx + 1, idx)
    df = df[df["ix"] < len(cal)].copy()
    df["day"] = cal[df["ix"].values]
    return df, syms, cal, fwd, mom


def build_features(df, syms, cal):
    shape = (len(cal), len(syms))
    col = {s: i for i, s in enumerate(syms)}
    cnt = np.zeros(shape)
    tot = np.zeros(shape)
    g = df.groupby(["ix", "sym"])["sent"].agg(["sum", "count"]).reset_index()
    for ix, sym, sm, c in g.itertuples(index=False):
        cnt[ix, col[sym]] = c
        tot[ix, col[sym]] = sm
    cnt = pd.DataFrame(cnt, index=cal, columns=syms)
    tot = pd.DataFrame(tot, index=cal, columns=syms)
    feats = {}
    for w in (5, 21):
        c = cnt.rolling(w, min_periods=1).sum()
        s = tot.rolling(w, min_periods=1).sum()
        feats["sent_%d" % w] = (s / c).where(c > 0)
        if w == 21:
            feats["cnt_21"] = c
            feats["sent_sum_21"] = s
    cats = df["desc"].value_counts().head(8).index.tolist()
    for cat in cats:
        sub = df[df["desc"] == cat].groupby(["ix", "sym"]).size().reset_index()
        flag = np.zeros(shape)
        for ix, sym, _ in sub.itertuples(index=False):
            flag[ix, col[sym]] = 1
        feats["cat5:" + cat[:40]] = pd.DataFrame(flag, index=cal, columns=syms).rolling(5, min_periods=1).max()
    return feats, cats


def evaluate():
    df, syms, cal, fwd, mom = prepare()
    feats, cats = build_features(df, syms, cal)
    L = []
    L.append("DATA")
    L.append("stocks %d, announcements scored %d, years %d-%d" % (
        len(syms), len(df), df["ts"].dt.year.min(), df["ts"].dt.year.max()))
    yc = df["ts"].dt.year.value_counts().sort_index()
    L.append("per year: " + ", ".join("%d:%d" % (y, n) for y, n in yc.items()))
    lab = df["label"].value_counts(normalize=True)
    L.append("argmax share: pos %.1f%% neg %.1f%% neutral %.1f%%  mean sent %+.4f" % (
        100 * lab.get("pos", 0), 100 * lab.get("neg", 0), 100 * lab.get("neu", 0), df["sent"].mean()))
    top = df.groupby("desc")["sent"].agg(["count", "mean"]).sort_values("count", ascending=False).head(10)
    L.append("top 10 categories (count, mean sent):")
    for name, r in top.iterrows():
        L.append("  %-60s %6d  %+.3f" % (name[:60], r["count"], r["mean"]))
    L.append("")
    L.append("RANK IC vs fwd %dd return (non-overlapping steps)" % HORIZON)
    rows = {}
    allf = dict(feats)
    allf["mom_252"] = mom
    verdict_rows = {}
    for name, f in allf.items():
        ic = rank_ic(f, fwd, HORIZON)
        txt, stats = summarize(ic)
        L.append("%-52s %s" % (name, txt))
        rows[name] = ic
        verdict_rows[name] = stats
    L.append("per-year mean IC:")
    years = sorted({y for ic in rows.values() for y in ic.index.year})
    L.append("%-30s" % "feature" + "".join("%8d" % y for y in years))
    for name, ic in rows.items():
        yr = ic.groupby(ic.index.year).mean()
        L.append("%-30s" % name[:30] + "".join("%8s" % ("%+.3f" % yr[y] if y in yr.index else "-") for y in years))
    L.append("")
    L.append("EVENT STUDY: fwd %dd return minus equal-weight average of the same day" % HORIZON)
    ew = fwd.mean(axis=1)
    ex = fwd.sub(ew, axis=0)
    vals = ex.values
    df["ex"] = vals[df["ix"].values, [syms.index(s) for s in df["sym"]]]
    ev = df.dropna(subset=["ex"])
    q20, q80 = ev["sent"].quantile([0.2, 0.8])
    groups = {
        "bottom quintile sent (<=%+.3f)" % q20: ev[ev["sent"] <= q20],
        "top quintile sent (>=%+.3f)" % q80: ev[ev["sent"] >= q80],
        "all announcements": ev,
    }
    for c in cats:
        groups["cat: " + c[:50]] = ev[ev["desc"] == c]
    for name, g in groups.items():
        L.append("%-62s n %6d  mean excess %+.3f%%  t %+.2f" % (
            name, len(g), 100 * g["ex"].mean(), tstat(g["ex"])))
    for name, g in list(groups.items())[:2]:
        d = g.groupby(["day", "sym"])["ex"].mean()
        L.append("  (%s collapsed to stock-days: n %d, mean %+.3f%%, t %+.2f)" % (
            name[:20], len(d), 100 * d.mean(), tstat(d)))
    L.append("  (event t-stats are naive: overlapping 20d windows inflate them)")
    best = []
    for name, st in verdict_rows.items():
        if name == "mom_252" or st is None:
            continue
        best.append((abs(st[1]), name, st))
    best.sort(reverse=True)
    usable = [b for b in best if b[0] > 2 and (b[2][2] >= 0.7 * b[2][3] or b[2][2] <= 0.3 * b[2][3])]
    head = ["VERDICT"]
    if usable:
        head.append("POSSIBLE SIGNAL: " + "; ".join(
            "%s IC %+.4f t %+.2f yrs+ %d/%d" % (n, s[0], s[1], s[2], s[3]) for _, n, s in usable))
    else:
        head.append("NO USABLE SIGNAL: no feature has |t|>2 with consistent sign across years.")
    if best:
        _, n, s = best[0]
        head.append("strongest: %s IC %+.4f t %+.2f yrs+ %d/%d" % (n, s[0], s[1], s[2], s[3]))
    head.append("")
    text = "\n".join(head + L)
    REPORT.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "eval"
    if cmd == "fetch":
        fetch()
    elif cmd == "score":
        score(int(sys.argv[2]) if len(sys.argv) > 2 else None)
    else:
        evaluate()
