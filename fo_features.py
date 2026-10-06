import os

import numpy as np
import pandas as pd

import data as data_module

FO_COLUMNS = ["oi_chg_5", "oi_chg_21", "oi_price_div", "pcr_oi", "pcr_chg_5"]
SECTOR_COLUMNS = ["sect_mom_21", "sect_mom_63", "sect_rel_21"]
PART_COLUMNS = ["fii_idx_fut_net", "fii_idx_fut_net_chg5", "dii_idx_fut_net",
                "fii_stk_fut_net", "fii_idx_opt_net", "client_idx_fut_net"]
RANKED_COLUMNS = FO_COLUMNS + SECTOR_COLUMNS
MIN_SECTOR_NAMES = 3

_STATE = {}


def _paths():
    base = os.path.join(data_module.CACHE_DIR, "nse")
    return {"fo": os.path.join(base, "fo", "fo_all.pkl"),
            "part": os.path.join(base, "fo", "participant.pkl"),
            "industry": os.path.join(base, "industry.csv")}


def _stamp(path):
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def key_bytes():
    h = []
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "fo_features.py"), "rb") as handle:
        h.append(handle.read())
    for name, path in sorted(_paths().items()):
        h.append(f"{name}|{_stamp(path)};".encode())
    return b"".join(h)


def norm(symbol):
    name = str(symbol)
    for prefix in ("US:",):
        if name.startswith(prefix):
            return None
    for suffix in (".NS", ".BO", "_NS", "_BO"):
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    return name.strip().upper()


def _cached(name, loader):
    path = _paths()[name]
    stamp = _stamp(path)
    entry = _STATE.get(name)
    if entry is not None and entry[0] == stamp:
        return entry[1]
    value = loader(path) if stamp is not None else None
    _STATE[name] = (stamp, value)
    return value


def _load_fo(path):
    df = pd.read_pickle(path)
    return {str(s): g.drop_duplicates("date").set_index("date").sort_index()
            for s, g in df.groupby("symbol")}


def _load_part(path):
    df = pd.read_pickle(path)
    return df.drop_duplicates("date").set_index("date").sort_index()


def _load_industry(path):
    df = pd.read_csv(path, dtype=str)
    df.columns = [c.strip() for c in df.columns]
    df = df.dropna(subset=["Symbol", "Industry"])
    return {s.strip().upper(): i.strip() for s, i in zip(df["Symbol"], df["Industry"])}


def fo_by_symbol():
    return _cached("fo", _load_fo)


def participant():
    return _cached("part", _load_part)


def industry_map():
    return _cached("industry", _load_industry)


def _date_index(index):
    idx = pd.DatetimeIndex(index)
    if idx.tz is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    return idx.normalize()


def add_fo(features, bars, symbol):
    for column in FO_COLUMNS:
        features[column] = np.nan
    table = fo_by_symbol()
    name = norm(symbol)
    if not table or name is None or name not in table:
        return features
    fo = table[name]
    days = _date_index(bars.index)
    aligned = fo.reindex(days)
    aligned.index = bars.index
    oi_value = aligned["fut_oi"] * aligned["fut_close"]
    smooth = oi_value.rolling(5, min_periods=3).mean()
    chg_5 = smooth / smooth.shift(5).replace(0, np.nan) - 1.0
    smooth_21 = oi_value.rolling(5, min_periods=3).mean()
    chg_21 = smooth_21 / smooth_21.shift(21).replace(0, np.nan) - 1.0
    ret_5 = bars["close"].pct_change(5, fill_method=None)
    pcr = np.log1p(aligned["put_oi"] / aligned["call_oi"].replace(0, np.nan))
    pcr_smooth = pcr.rolling(5, min_periods=3).mean()
    features["oi_chg_5"] = chg_5
    features["oi_chg_21"] = chg_21
    features["oi_price_div"] = chg_5 * np.sign(ret_5)
    features["pcr_oi"] = pcr
    features["pcr_chg_5"] = pcr_smooth - pcr_smooth.shift(5)
    return features


def add_sector(panel):
    for column in SECTOR_COLUMNS:
        panel[column] = np.nan
    mapping = industry_map()
    if not mapping or "mom_21" not in panel.columns:
        return
    symbols = panel["symbol"].astype(str)
    lookup = {s: mapping.get(norm(s)) for s in symbols.unique()}
    sector = symbols.map(lookup)
    if sector.isna().all():
        return
    day = panel["timestamp"]
    for window in (21, 63):
        source = f"mom_{window}"
        if source not in panel.columns:
            continue
        by = [day, sector]
        mean = panel[source].groupby(by).transform("mean")
        count = panel[source].groupby(by).transform("count")
        panel[f"sect_mom_{window}"] = mean.where(count >= MIN_SECTOR_NAMES)
    panel["sect_rel_21"] = panel["mom_21"] - panel["sect_mom_21"]


def finish(panel, grouped):
    for column in RANKED_COLUMNS:
        if column not in panel.columns:
            panel[column] = 0.0
            continue
        ranked = grouped[column].rank(pct=True)
        panel[column] = ((ranked - 0.5) * 2.0).fillna(0.0)
    for column in PART_COLUMNS:
        panel[column] = 0.0
    part = participant()
    if part is None or part.empty:
        return
    wide = part.drop(columns=[c for c in part.columns if c not in PART_COLUMNS]).copy()
    wide["fii_idx_fut_net_chg5"] = wide["fii_idx_fut_net"].diff(5)
    days = pd.DatetimeIndex(panel["timestamp"]).normalize()
    union = wide.index.union(pd.DatetimeIndex(days.unique())).sort_values()
    wide = wide.reindex(union).ffill(limit=3).reindex(days)
    for column in PART_COLUMNS:
        if column in wide.columns:
            panel[column] = wide[column].to_numpy()
    panel[PART_COLUMNS] = panel[PART_COLUMNS].fillna(0.0)
