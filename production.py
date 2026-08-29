"""The one model the book actually trades, and the only place that decides it.

WHY THIS FILE EXISTS

Book, Orders and Sim each used to load ranker.pt and rebuild a torch Ranker by
hand. Three copies of "which model is production" is how a project ends up
trading a stale artifact from a page nobody remembered to update - and it very
nearly did here: after the ranker was rewritten with 39 features, all three were
still scoring with the 14-feature file until it was retrained.

Now they ask this file. Changing config.PRODUCTION_SIGNAL changes the book.

WHAT IS FITTED, AND ON WHAT

Everything available up to an embargo, with the last stretch held out purely to
early-stop on. That differs from walkforward.py deliberately: walk-forward exists
to ESTIMATE the edge honestly and therefore refits per fold on that fold's past
only. This exists to TRADE, so it uses every observation it is allowed to,
including the most recent ones - which are the ones most like tomorrow.

The consequence to keep straight: the numbers this model produces in a
simulation over recent history are optimistic, because it was fitted on part of
that history. walkforward.py is the number to quote. This is the model to run.

Run with:  py -3.13 production.py               fit and save
           py -3.13 production.py --picks       what it would hold today
"""

from __future__ import annotations

import argparse
import os
import random

import joblib
import numpy as np
import pandas as pd

import config
import features as features_module
import signals as signals_module

PATH = os.path.join(config.MODEL_DIR, "production.joblib")


SUBSET_SIZE = 40
SUBSET_MEMBERS = 5


class SubsetEnsemble:
    """Several fits, each on a random slice of the universe, averaged by rank.

    Every feature the model sees is a rank within that day's universe, so the
    inputs depend on which other names are present. One fit on the whole roster
    learns one composition; add or drop a stock and every number shifts beneath
    it. Fitting each member on a different random slice means no member has
    ever seen the full roster, so the average is taken over models that
    disagree about composition rather than one that assumes a fixed one.

    Averaged by rank, not by score: members are fitted separately and their
    score scales have no reason to be comparable. Ranks are.
    """

    def __init__(self, members, columns=None):
        self.members = list(members)
        self.columns = columns or getattr(self.members[0], "columns", None)

    def predict(self, panel):
        from scipy.stats import rankdata

        return np.mean([rankdata(m.predict(panel)) for m in self.members],
                       axis=0)


def _split(panel):
    """Train and validation, with the embargo split_panel uses."""
    dates = np.sort(panel["timestamp"].unique())
    gap = pd.offsets.Day(int(config.TARGET_HORIZON * 1.5))
    val_start = pd.Timestamp(dates[-1]) - pd.offsets.Day(400)
    train_end = val_start - gap
    return (panel[panel["timestamp"] <= train_end],
            panel[panel["timestamp"] > val_start], dates, train_end, val_start)


def _fit_one(symbols, signal_name: str, quiet: bool, panel=None,
             window_start=None, window_end=None):
    if panel is None:
        panel = features_module.cross_sectionalize(
            features_module.build_panel(symbols))
    window = panel
    if window_start is not None:
        window = window[window["timestamp"] >= pd.Timestamp(window_start)]
    if window_end is not None:
        window = window[window["timestamp"] <= pd.Timestamp(window_end)]
    train_panel, val_panel, dates, train_end, val_start = _split(window)

    if len(train_panel) < 1000 or len(val_panel) < 100:
        raise SystemExit(f"not enough data: train {len(train_panel)}, "
                         f"val {len(val_panel)}")

    signal = signals_module.build(signal_name)
    signal.fit(train_panel, val_panel)

    if not quiet:
        print(f"  fitted on {len(train_panel):,} rows "
              f"({pd.Timestamp(dates[0]).date()} .. {train_end.date()}), "
              f"early-stopped on {len(val_panel):,} rows after "
              f"{val_start.date()}")
    return signal, panel


def fit_window(symbols, signal_name: str | None = None, panel=None,
               start=None, end=None, quiet: bool = True):
    return _fit_one(symbols, signal_name or config.PRODUCTION_SIGNAL, quiet,
                    panel, start, end)


def fit(signal_name: str | None = None, quiet: bool = False,
        subsets: bool = False, size: int = SUBSET_SIZE,
        members: int = SUBSET_MEMBERS):
    """Fit the production signal on everything up to the embargo.

    One fit over the whole roster, which is the default because it measured
    better. Pass subsets=True to instead fit `members` models on random slices
    of `size` names each and average them by rank.

    The subset ensemble was the default from 54eaa69 until it was measured
    against a plain fit on 41 paired draws - same names, same window, same seed
    for both, each scored on thirty names it had never seen. It lost on every
    axis: rank IC -0.0065, excess -1.80%/yr, ahead in only 17 of 41 draws, at
    roughly twice the fit time. Neither difference is significant on its own,
    but nothing supports paying double to fit on 40 names at a time when the
    book trades all of them, so the whole roster is the default again.
    """
    signal_name = signal_name or config.PRODUCTION_SIGNAL
    names = list(config.UNIVERSE)

    if not subsets or len(names) < size * 2:
        return _fit_one(None, signal_name, quiet)

    rng = random.Random(config.SEED)
    fitted, panel = [], None
    for index in range(members):
        chosen = rng.sample(names, size)
        if not quiet:
            print(f"member {index + 1}/{members}: {len(chosen)} names")
        signal, panel = _fit_one(chosen, signal_name, quiet)
        fitted.append(signal)

    if not quiet:
        print(f"ensemble of {len(fitted)} fits, {size} names each, "
              f"from a roster of {len(names)}")
    return SubsetEnsemble(fitted), panel


def save(signal, signal_name: str | None = None) -> str:
    """Store the fit, stamped with what it actually is.

    The feature list is the signal's OWN columns, not MODEL_COLUMNS. Those were
    the same thing while every model took all thirty-nine; the network now takes
    the core sixteen, and stamping the module default would record a feature set
    the saved model was never fitted on - turning the guard in load() into a
    check that passes precisely when it should not.
    """
    os.makedirs(config.MODEL_DIR, exist_ok=True)
    joblib.dump({"signal": signal,
                 "name": signal_name or config.PRODUCTION_SIGNAL,
                 "features": list(getattr(signal, "columns",
                                          features_module.MODEL_COLUMNS)),
                 "horizon": config.TARGET_HORIZON}, PATH)
    return PATH


def load():
    """The saved production signal, or a clear instruction if there is none."""
    if not os.path.exists(PATH):
        raise SystemExit(f"no production model at {PATH} - "
                         f"run: py -3.13 production.py")
    bundle = joblib.load(PATH)

    # Which model this is, before what it was fitted on. Since the signal is
    # selectable, the stored fit and config.PRODUCTION_SIGNAL can disagree -
    # someone switches to the network and the file still holds the boosted fit.
    # Nothing downstream would notice: Book, Orders and Sim would serve
    # predictions from a model the operator believes was replaced. That is the
    # one failure here that could reach a trading decision, so it is refused
    # rather than warned about.
    saved_name = bundle.get("name")
    wanted = config.PRODUCTION_SIGNAL
    if saved_name and saved_name != wanted:
        raise SystemExit(
            f"production model on disk is '{saved_name}', but the configured "
            f"signal is '{wanted}'. Refit before trading it: "
            f"py -3.13 production.py")

    # A model fitted on a different feature set will score nonsense rather than
    # fail, so the mismatch is caught here instead of showing up as a strange
    # book. This is exactly the bug the file was written to prevent. Compared
    # against what THIS signal would be built with, because the models no
    # longer agree on their inputs.
    saved = list(bundle.get("features", []))
    current = list(getattr(signals_module.build(wanted), "columns",
                           features_module.MODEL_COLUMNS))
    if saved != current:
        raise SystemExit(
            f"production model was fitted on {len(saved)} features, the code "
            f"now builds {len(current)} for '{wanted}'. "
            f"Refit: py -3.13 production.py")
    return bundle["signal"], bundle


def latest_complete_date(panel: pd.DataFrame, floor: float = 0.9):
    counts = panel.groupby("timestamp")["symbol"].size()
    if counts.empty:
        return None
    reference = counts.max()
    eligible = counts[counts >= floor * reference]
    date = eligible.index.max() if len(eligible) else counts.index.max()
    newest = counts.index.max()
    if date != newest:
        print(f"  scoring {pd.Timestamp(date).date()} on {counts.loc[date]} "
              f"names, not {pd.Timestamp(newest).date()} on "
              f"{counts.loc[newest]}")
    return date


def picks(panel: pd.DataFrame | None = None, k: int | None = None,
          on_date=None) -> list[str]:
    """The k highest-scoring symbols, by default on the latest date available."""
    k = k or config.TOP_K
    signal, _ = load()
    if panel is None:
        panel = features_module.cross_sectionalize(
            features_module.build_panel(), require_target=False)

    date = on_date if on_date is not None else latest_complete_date(panel)
    frame = panel[panel["timestamp"] == date].copy()
    if frame.empty:
        return []
    frame["score"] = signal.predict(frame)
    return frame.sort_values("score", ascending=False).head(k)["symbol"].tolist()


def scored(panel: pd.DataFrame, on_date=None) -> pd.DataFrame:
    """Every name on one date with its score attached, highest first."""
    signal, _ = load()
    date = on_date if on_date is not None else latest_complete_date(panel)
    frame = panel[panel["timestamp"] == date].copy()
    frame["score"] = signal.predict(frame)
    return frame.sort_values("score", ascending=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--signal", default=None,
                        choices=("nn", "lightgbm", "tabpfn"))
    parser.add_argument("--picks", action="store_true",
                        help="load the saved model and show today's book")
    args = parser.parse_args()

    if args.picks:
        signal, bundle = load()
        panel = features_module.cross_sectionalize(
            features_module.build_panel(), require_target=False)
        frame = scored(panel)
        date = frame["timestamp"].iloc[0]
        print(f"\n{bundle['name']} | {date.date()} | top {config.TOP_K} of "
              f"{len(frame)}\n")
        print(f"{'#':>3}  {'symbol':<14}{'score':>10}{'mom_126':>10}{'vol_21':>9}")
        print("-" * 48)
        for position, (_, row) in enumerate(frame.iterrows(), start=1):
            marker = " <-" if position <= config.TOP_K else ""
            print(f"{position:>3}  {row['symbol']:<14}{row['score']:>10.4f}"
                  f"{row['mom_126']:>10.2f}{row['vol_21']:>9.2f}{marker}")
        return

    signal, _ = fit(args.signal)
    path = save(signal, args.signal)
    print(f"saved {path}")
    print(f"\nBook, Orders and Sim now use {args.signal or config.PRODUCTION_SIGNAL}.")


if __name__ == "__main__":
    main()
