from __future__ import annotations

import numpy as np
import pandas as pd

RANK_SD = 0.2887

PHRASES = {
    "mom_21": ("1m momentum strong", "1m momentum weak"),
    "mom_63": ("3m momentum strong", "3m momentum weak"),
    "mom_126": ("6m momentum strong", "6m momentum weak"),
    "mom_252": ("1y momentum strong", "1y momentum weak"),
    "rev_5": ("bounced this week", "pulled back this week"),
    "vol_21": ("high 1m volatility", "low volatility"),
    "vol_63": ("high 3m volatility", "low volatility"),
    "vol_ratio": ("volatility rising", "volatility calming"),
    "range_21": ("wide trading range", "tight trading range"),
    "dist_high_252": ("near 1y high", "far below 1y high"),
    "dollar_vol": ("heavily traded", "thinly traded"),
    "vol_surprise": ("volume surge", "volume dry-up"),
    "skew_63": ("positive return skew", "negative return skew"),
    "trend_agree": ("trend signals agree up", "trend signals agree down"),
    "return_1d": ("up today", "down today"),
    "log_return_1d": ("up today", "down today"),
    "sma_5": ("above 5d average", "below 5d average"),
    "sma_10": ("above 10d average", "below 10d average"),
    "sma_20": ("above 20d average", "below 20d average"),
    "sma_50": ("above 50d average", "below 50d average"),
    "volatility_5": ("high 5d volatility", "low 5d volatility"),
    "volatility_10": ("high 10d volatility", "low 10d volatility"),
    "volatility_50": ("high 50d volatility", "low volatility"),
    "ema_12": ("above 12d trend", "below 12d trend"),
    "ema_26": ("above 26d trend", "below 26d trend"),
    "rsi_14": ("strong RSI", "weak RSI"),
    "macd": ("MACD bullish", "MACD bearish"),
    "macd_hist": ("MACD accelerating", "MACD fading"),
    "hl_spread": ("wide daily range", "tight daily range"),
    "oc_spread": ("closing above open", "closing below open"),
    "volume_change": ("volume rising", "volume falling"),
    "volume_sma_10": ("high average volume", "low average volume"),
    "excess_return_1d": ("beating Nifty today", "lagging Nifty today"),
    "rel_strength_5": ("beating Nifty this week", "lagging Nifty this week"),
    "rel_strength_20": ("beating Nifty this month", "lagging Nifty this month"),
    "beta_60": ("high market beta", "low market beta"),
    "corr_60": ("moves with the market", "moves independently"),
    "mkt_return_20d": ("market trending up", "market trending down"),
    "mkt_volatility_20": ("market volatile", "market calm"),
    "liq_spread_21": ("wide spreads", "tight spreads"),
    "liq_spread_63": ("wide spreads", "tight spreads"),
    "sect_mom_21": ("sector leading 1m", "sector lagging 1m"),
    "sect_mom_63": ("sector leading 3m", "sector lagging 3m"),
    "sect_rel_21": ("beating its sector", "trailing its sector"),
    "oi_chg_5": ("open interest building", "open interest unwinding"),
    "oi_chg_21": ("open interest building", "open interest unwinding"),
    "oi_price_div": ("OI and price diverge up", "OI and price diverge down"),
    "pcr_oi": ("put-heavy positioning", "call-heavy positioning"),
    "pcr_chg_5": ("puts rising", "puts falling"),
}


def label(name: str, value: float) -> str:
    pair = PHRASES.get(name)
    if pair is None:
        return name.replace("_", " ")
    return pair[0] if value >= 0 else pair[1]


def _centered(frame: pd.DataFrame, columns) -> pd.DataFrame:
    return frame[list(columns)].astype(float)


def _rank(values: np.ndarray) -> np.ndarray:
    return pd.Series(values).rank(pct=True).fillna(0.5).to_numpy()


def _contributions(signal, frame: pd.DataFrame) -> pd.DataFrame:
    if hasattr(signal, "parts") and hasattr(signal, "weights"):
        total = None
        for name, model, weight in zip(signal.parts, signal.models,
                                       signal.weights):
            if model is None:
                part = pd.DataFrame(
                    {name: weight * (_rank(frame[name].to_numpy(float)) - 0.5)},
                    index=frame.index)
            else:
                part = _contributions(model, frame) * weight
            total = part if total is None else total.add(part, fill_value=0.0)
        return total

    members = getattr(signal, "members", None) or getattr(signal, "models", None)
    if members:
        parts = [_contributions(m, frame) for m in members]
        total = parts[0]
        for part in parts[1:]:
            total = total.add(part, fill_value=0.0)
        return total / len(parts)

    model = getattr(signal, "model", None)
    columns = getattr(signal, "columns", None)
    if model is None or columns is None or type(signal).__name__ != "BoostedSignal":
        raise TypeError(f"cannot explain {type(signal).__name__}")
    matrix = frame[list(columns)].to_numpy(dtype=np.float32)
    contrib = np.asarray(model.predict(matrix, pred_contrib=True))[:, :-1]
    spread = float(np.std(np.asarray(model.predict(matrix), dtype=float)))
    if not spread:
        raise ValueError("flat predictions")
    contrib = contrib - contrib.mean(axis=0, keepdims=True)
    return pd.DataFrame(contrib * (RANK_SD / spread), index=frame.index,
                        columns=list(columns))


def _fallback_columns(signal, frame: pd.DataFrame):
    columns = getattr(signal, "columns", None)
    if not columns:
        for attribute in ("members", "models"):
            for member in getattr(signal, attribute, None) or []:
                columns = getattr(member, "columns", None)
                if columns:
                    break
            if columns:
                break
    if not columns:
        import features

        columns = features.MODEL_COLUMNS
    return [c for c in columns if c in frame.columns]


def _fallback(signal, frame: pd.DataFrame, top_n: int) -> list[str]:
    columns = _fallback_columns(signal, frame)
    values = _centered(frame, columns)
    out = []
    for _, row in values.iterrows():
        best = row.sort_values(ascending=False).head(top_n)
        out.append(", ".join(label(name, v) for name, v in best.items()))
    return out


def why(signal, frame: pd.DataFrame, top_n: int = 3) -> list[str]:
    try:
        contrib = _contributions(signal, frame)
        names = list(contrib.columns)
        values = frame[names].astype(float)
        out = []
        for index in frame.index:
            row = contrib.loc[index]
            best = row[row > 0].sort_values(ascending=False).head(top_n)
            out.append(", ".join(label(n, values.at[index, n])
                                 for n in best.index))
        return out
    except Exception:
        try:
            return _fallback(signal, frame, top_n)
        except Exception:
            return [""] * len(frame)
