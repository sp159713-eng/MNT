"""Turn OHLCV bars into a model-ready panel, without leaking the future.

WHY THE FEATURES LOOK LIKE THIS

Every column is computed from data strictly at or before its own timestamp, and
every one is then ranked ACROSS stocks on that date. The cross-sectional step is
what makes the problem learnable: absolute momentum tells the model what the
market did, which is the same for every name and therefore useless for choosing
between them. Relative momentum tells it which name is beating the others, which
is the only question a stock-picking book asks.

Ranking also disposes of the regime problem for free. A 12% three-month return
is exceptional in 2019 and unremarkable in 2021; a rank of 0.95 means the same
thing in both. The model never sees a number whose meaning drifts with the year.

THE TARGET

Forward 20-day return, minus the cross-sectional mean of that same date. So the
label is "did this beat the average name over the next month", not "did it go
up". A model trained on raw forward returns spends its capacity learning the
market's direction, which it cannot predict and does not need to - the book is
always fully invested in six names regardless.

WHERE THE LOOKAHEAD WOULD BE, IF THERE WERE ANY

  Features at date t use bars up to and including t.
  The target at date t uses bars t+1 .. t+20.
  Rows within `horizon` days of the split boundary are dropped by split_panel,
  so a training row's future never overlaps a validation row's past.

That last one is the embargo, and it is the difference between a real backtest
and a very encouraging one.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import config
import data as data_module

# Cross-sectionally ranked features: every one differs BETWEEN names on a date,
# so a rank among them carries information.
FEATURE_COLUMNS = [
    # Original MNT set.
    "mom_21", "mom_63", "mom_126", "mom_252",
    "rev_5", "vol_21", "vol_63", "vol_ratio",
    "range_21", "dist_high_252", "dollar_vol", "vol_surprise",
    "skew_63", "trend_agree",
    # Ported from NNS: trend/oscillator/spread block.
    "return_1d", "log_return_1d",
    "sma_5", "sma_10", "sma_20", "sma_50",
    "volatility_5", "volatility_10", "volatility_50",
    "ema_12", "ema_26",
    "rsi_14", "macd", "macd_hist",
    "hl_spread", "oc_spread",
    "volume_change", "volume_sma_10",
    # Ported from NNS: index-relative block. Kept because these vary across
    # names; the pure market columns below do not.
    "excess_return_1d", "rel_strength_5", "rel_strength_20",
    "beta_60", "corr_60",
]

# Market context: identical for every name on a given date. Deliberately NOT
# ranked cross-sectionally - a column with the same value in all thirty rows
# ranks to a constant and tells the model nothing. Left raw so it can still act
# as regime context, which is the only thing it can honestly provide.
MARKET_CONTEXT_COLUMNS = ["mkt_return_20d", "mkt_volatility_20"]

MODEL_COLUMNS = FEATURE_COLUMNS + MARKET_CONTEXT_COLUMNS

# The fourteen columns MNT started with, before NNS's block was ported in.
#
# Kept as a named set because the measurement that matters here points at it:
# the full thirty-nine scored BELOW these fourteen for the neural network - see
# the tried-and-lost table in README.md - and the reason is capacity, not
# quality. Roughly two hundred effectively independent observations cannot
# support thirty-nine inputs into a model that has to estimate every weight.
# LightGBM is far less exposed to that, which is why MODEL_COLUMNS remains the
# right default for the trees and this is the right default for the net.
CORE_FEATURE_COLUMNS = FEATURE_COLUMNS[:14]

CORE_COLUMNS = CORE_FEATURE_COLUMNS + MARKET_CONTEXT_COLUMNS


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI, carrying NNS's fix for the unbroken-up-run case.

    Zero average loss makes rs infinite, not undefined: an unbroken up-run is
    RSI 100, the most overbought reading there is. Filling it with a neutral 50
    reports the exact opposite of the truth at the strongest trends. Only the
    genuinely flat case, where neither side moved, is neutral.
    """
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    strong = (avg_loss.fillna(0) == 0) & (avg_gain > 0)
    return rsi.where(~strong, 100.0).fillna(50)


def _macd(close: pd.Series) -> tuple[pd.Series, pd.Series]:
    """MACD and its histogram, divided by price - which NNS did not do.

    NNS left MACD in rupees. That is harmless when every row is one stock, but
    here features are ranked ACROSS names, and a rupee-denominated MACD ranks
    NESTLEIND above everything simply because it trades near 2,400 while
    POWERGRID trades near 300. The rank would encode the price level, not the
    momentum. Dividing by close makes it comparable between names, which is the
    only form in which it is usable here.
    """
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = (ema12 - ema26) / close.replace(0, np.nan)
    signal = macd.ewm(span=9, adjust=False).mean()
    return macd, macd - signal


def _returns(close: pd.Series) -> pd.Series:
    return close.pct_change(fill_method=None)


def build_features(frame: pd.DataFrame,
                   market: pd.Series | None = None) -> pd.DataFrame:
    """Per-stock features from one symbol's bars. No cross-sectional step yet.

    `market` is the benchmark close series, used for the index-relative block.
    """
    close, high, low, volume = (frame["close"], frame["high"],
                                frame["low"], frame["volume"])
    daily = _returns(close)
    out = pd.DataFrame(index=frame.index)

    # Momentum over several horizons. The 21-day one is included knowing it
    # often carries the opposite sign to the longer ones - short-horizon
    # reversal against long-horizon trend is the oldest structure in the data,
    # and the model is allowed to find it rather than being told about it.
    out["mom_21"] = close.pct_change(21, fill_method=None)
    out["mom_63"] = close.pct_change(63, fill_method=None)
    out["mom_126"] = close.pct_change(126, fill_method=None)
    # Skips the most recent month, the standard fix for momentum being
    # contaminated by short-term reversal.
    out["mom_252"] = close.shift(21).pct_change(231, fill_method=None)

    out["rev_5"] = close.pct_change(5, fill_method=None)

    out["vol_21"] = daily.rolling(21).std()
    out["vol_63"] = daily.rolling(63).std()
    # Is the stock getting more volatile than it usually is? Regime change in
    # one name, not in the market.
    out["vol_ratio"] = out["vol_21"] / out["vol_63"].replace(0, np.nan)

    # Intraday range says something volatility of closes does not: a name that
    # travels 4% a day and closes flat is not a quiet stock.
    out["range_21"] = ((high - low) / close.replace(0, np.nan)).rolling(21).mean()

    out["dist_high_252"] = close / close.rolling(252).max().replace(0, np.nan) - 1.0

    turnover = (close * volume).rolling(21).mean()
    out["dollar_vol"] = np.log1p(turnover)
    # Volume today against its own recent normal. Participation, not size.
    out["vol_surprise"] = volume / volume.rolling(63).mean().replace(0, np.nan)

    out["skew_63"] = daily.rolling(63).skew()

    # Do the horizons agree? Trend strength is not the same as trend size, and
    # a name up over 1, 3 and 6 months is a different animal from one whose
    # six-month number is carried entirely by a single gap.
    out["trend_agree"] = (np.sign(out["mom_21"]) + np.sign(out["mom_63"])
                          + np.sign(out["mom_126"])) / 3.0

    # ---- ported from NNS -------------------------------------------------
    out["return_1d"] = daily
    out["log_return_1d"] = np.log(close / close.shift(1))

    # Distance from each moving average, as a fraction of price. NNS's sign
    # convention is kept (sma/close - 1), so positive means price is BELOW the
    # average.
    for window in (5, 10, 20, 50):
        out[f"sma_{window}"] = close.rolling(window).mean() / close - 1
    for window in (5, 10, 50):
        out[f"volatility_{window}"] = daily.rolling(window).std()

    out["ema_12"] = close.ewm(span=12, adjust=False).mean() / close - 1
    out["ema_26"] = close.ewm(span=26, adjust=False).mean() / close - 1

    out["rsi_14"] = _rsi(close, 14)
    out["macd"], out["macd_hist"] = _macd(close)

    out["hl_spread"] = (high - low) / close.replace(0, np.nan)
    out["oc_spread"] = (close - frame["open"]) / frame["open"].replace(0, np.nan)

    # fill_method=None so a missing-volume gap stays NaN rather than carrying
    # the previous session forward and inventing volume that never traded.
    safe_volume = volume.replace(0, np.nan)
    out["volume_change"] = safe_volume.pct_change(fill_method=None)
    # min_periods=9 so ONE missing session does not erase ten rows - NNS found
    # market-wide zero-volume dates wiping the same block from every symbol.
    out["volume_sma_10"] = (safe_volume.rolling(10, min_periods=9).mean()
                            / safe_volume - 1)

    if market is not None:
        aligned = market.reindex(frame.index)
        market_daily = aligned.pct_change(fill_method=None).fillna(0.0)
        out["excess_return_1d"] = daily - market_daily
        for window in (5, 20):
            out[f"rel_strength_{window}"] = (daily.rolling(window).mean()
                                             - market_daily.rolling(window).mean())
        # How strongly this name has been tracking the index lately. Clipped
        # because a near-zero market variance produces meaningless extremes.
        covariance = daily.rolling(60).cov(market_daily)
        variance = market_daily.rolling(60).var()
        out["beta_60"] = (covariance / variance.replace(0, np.nan)).clip(-5, 5)
        out["corr_60"] = daily.rolling(60).corr(market_daily)

        out["mkt_return_20d"] = market_daily.rolling(20).mean()
        out["mkt_volatility_20"] = market_daily.rolling(20).std()

    return out


def build_panel(symbols: list[str] | None = None, horizon: int | None = None,
                start: str | None = None) -> pd.DataFrame:
    """The long-format panel: one row per (date, symbol), features plus target."""
    symbols = symbols or config.UNIVERSE
    horizon = horizon or config.TARGET_HORIZON
    start = start or config.START

    frames = data_module.load(symbols, interval="1d", start=start)
    if not frames:
        raise RuntimeError("no price data loaded")

    # The index, for the relative-strength and beta block. If it cannot be
    # fetched the run continues without those columns rather than failing - but
    # it says so, because silently dropping nine features would otherwise show
    # up later as an unexplained change in results.
    market = None
    try:
        market = data_module.fetch(config.BENCHMARK, start=start, quiet=True)["close"]
    except Exception as error:                              # noqa: BLE001
        print(f"  no benchmark ({error}); index-relative features disabled")

    rows = []
    for symbol, bars in frames.items():
        if len(bars) < 300:
            print(f"  skipping {symbol}: only {len(bars)} bars")
            continue
        features = build_features(bars, market)
        features["symbol"] = symbol
        # The label. shift(-horizon) is the only place the future is touched,
        # and it is touched on purpose.
        forward = bars["close"].shift(-horizon) / bars["close"] - 1.0
        features["target"] = forward
        rows.append(features.reset_index())

    panel = pd.concat(rows, ignore_index=True)
    panel = panel.rename(columns={panel.columns[0]: "timestamp"})
    panel["timestamp"] = pd.to_datetime(panel["timestamp"], utc=True).dt.tz_localize(None)
    return panel.sort_values(["timestamp", "symbol"]).reset_index(drop=True)


def cross_sectionalize(panel: pd.DataFrame) -> pd.DataFrame:
    """Rank every feature within its date; centre the target within its date.

    Ranks are mapped to [-1, 1]. Dates with fewer than 10 live names are dropped
    entirely: a "rank" among four stocks is noise wearing the costume of a
    feature, and those dates are early history where half the universe had not
    listed yet.
    """
    panel = panel.dropna(subset=["target"]).copy()
    counts = panel.groupby("timestamp")["symbol"].transform("size")
    panel = panel[counts >= 10]

    grouped = panel.groupby("timestamp")
    for column in FEATURE_COLUMNS:
        if column not in panel.columns:
            panel[column] = 0.0
            continue
        ranked = grouped[column].rank(pct=True)
        panel[column] = (ranked - 0.5) * 2.0

    # Market context is left on its own scale - see MARKET_CONTEXT_COLUMNS.
    for column in MARKET_CONTEXT_COLUMNS:
        if column not in panel.columns:
            panel[column] = 0.0

    # Excess over the equal-weight universe on that date. This is what the book
    # actually harvests, since the book is always long six of these names.
    panel["target_excess"] = panel["target"] - grouped["target"].transform("mean")

    panel[MODEL_COLUMNS] = panel[MODEL_COLUMNS].fillna(0.0)
    return panel.dropna(subset=["target_excess"]).reset_index(drop=True)


def smooth_features(panel: pd.DataFrame, window: int) -> pd.DataFrame:
    """Average each name's features over its own trailing `window` days.

    Equivalent in intent to smoothing predictions after the fact, which is what
    backtest.py does, but applied to the inputs instead. Two consequences:

      It is cheaper by the rebalance interval. Smoothed predictions need a
      prediction on every day in the trailing window; smoothed features need
      one prediction per rebalance date. For a model whose inference costs
      milliseconds that is irrelevant, and for TabPFN on CPU - two minutes per
      600 rows - it is the difference between 11 hours and 30 minutes.

      It is not identical. Smoothing inputs then applying a non-linear model is
      not the same as applying the model then smoothing outputs; they coincide
      only for a linear one. Arguably inputs is the more defensible end: it
      says the features are noisy estimates of a slower-moving state, which is
      what the daily jitter in a 126-day momentum number actually is.

    Applied after cross_sectionalize, so it averages ranks in [-1, 1] and the
    scale the model was trained on is preserved.
    """
    if window <= 1:
        return panel
    out = panel.sort_values(["symbol", "timestamp"]).copy()
    out[MODEL_COLUMNS] = (out.groupby("symbol")[MODEL_COLUMNS]
                          .transform(lambda s: s.rolling(window, min_periods=1).mean()))
    return out.sort_values(["timestamp", "symbol"])


def thin_to_rebalance(panel: pd.DataFrame, every: int) -> pd.DataFrame:
    """Keep only the dates a book trading every `every` days would ever consult.

    The backtest already walks dates[::every]; every other row is computed and
    discarded. Dropping them up front changes no result and removes 95% of the
    inference bill.
    """
    if every <= 1:
        return panel
    dates = np.sort(panel["timestamp"].unique())[::every]
    return panel[panel["timestamp"].isin(dates)]


def split_panel(panel: pd.DataFrame, horizon: int | None = None
                ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Train / validation / test by date, with a `horizon` embargo at each seam.

    Without the embargo, the last training row's 20-day target is built from the
    first 20 days of validation. The model would be scored on data it had
    already been shown the answer to, and it would score well.
    """
    horizon = horizon or config.TARGET_HORIZON
    train_end = pd.Timestamp(config.TRAIN_END)
    val_end = pd.Timestamp(config.VAL_END)
    # Trading days -> calendar days. pd.Timedelta is avoided deliberately: with
    # numpy 2.5 it raises a deprecation warning from inside pandas itself.
    gap = pd.offsets.Day(int(horizon * 1.5))

    train = panel[panel["timestamp"] <= train_end]
    val = panel[(panel["timestamp"] > train_end + gap)
                & (panel["timestamp"] <= val_end)]
    test = panel[panel["timestamp"] > val_end + gap]
    return train.copy(), val.copy(), test.copy()


def main() -> None:
    panel = build_panel()
    panel = cross_sectionalize(panel)
    train, val, test = split_panel(panel)

    print(f"\npanel: {len(panel):,} rows, {panel['symbol'].nunique()} symbols, "
          f"{panel['timestamp'].nunique():,} dates")
    print(f"  {panel['timestamp'].min().date()} .. {panel['timestamp'].max().date()}\n")
    for name, part in (("train", train), ("val", val), ("test", test)):
        print(f"  {name:<6}{len(part):>9,} rows  "
              f"{part['timestamp'].min().date()} .. {part['timestamp'].max().date()}")

    spread = panel["target_excess"].std()
    print(f"\ntarget: {config.TARGET_HORIZON}d excess return, "
          f"sd {spread:.2%}, mean {panel['target_excess'].mean():+.4%}")
    print("(mean must be ~0 by construction - it is centred per date)")


if __name__ == "__main__":
    main()
