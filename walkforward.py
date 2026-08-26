"""Retrain and re-test every year, so the verdict rests on 20 years, not 3.

WHY THE SINGLE SPLIT WAS NOT ENOUGH

backtest.py gave the network a break-even of 220bp against a 34bp charge sheet,
which sounds decisive, on a t-statistic of 0.74, which is not. The reason is
arithmetic: one 3-year test window, rebalanced monthly, is 37 non-overlapping
observations. Nothing can be established at that sample size.

The proof that it was noise came from the baselines. Momentum earned +14.8% net
at buffer 6 and +1.1% at buffer 12 - a signal that inverts on a housekeeping
parameter is not being measured, it is being sampled. The configuration was
explaining more than the model was.

WHAT THIS DOES INSTEAD

Walk forward one year at a time. At each step: train on everything up to a
cutoff, select the configuration on the following year, then trade the year
after that and record the result. Then roll the cutoff forward and repeat. The
model is refit each fold, so it only ever knows its own past.

That converts one 37-period test into roughly a dozen consecutive out-of-sample
years - about 150 non-overlapping periods, across regimes that include 2008,
2013, 2020 and 2022. A strategy that survives that has been asked a real
question. One that only worked in 2023-2026 will show it here.

WHAT IS STILL NOT PROVEN BY THIS

Walk-forward fixes sample size, not survivorship. The universe in config.py is
30 names that are liquid large caps TODAY, so the test implicitly knows which
companies would still matter in 2026 while trading them in 2008. That biases
results upward and this file does not correct it - fixing it properly needs
point-in-time index membership, which Yahoo does not provide.

Run with:  py -3.13 walkforward.py
           py -3.13 walkforward.py --start-year 2012 --capital 200000
"""

from __future__ import annotations

import argparse
import warnings

import numpy as np
import pandas as pd

import backtest as backtest_module
import config
import costs as costs_module
import features as features_module
import metrics as metrics_module
import runs as runs_module
import signals as signals_module

warnings.filterwarnings("ignore", category=FutureWarning)

SIZES, BUFFERS, WINDOWS = (4, 6, 8), (0, 6, 12), (1, 10, 20)

# Confidence gate, in cross-sectional standard deviations of the score: below
# this separation between the chosen names and the average name, hold rather
# than rebalance. Disabled - (0.0,) - because it was measured and it lost.
#
# The full story, because it is the most instructive negative result here.
# Swept on TEST, gate 1.2 looked outstanding: same +46bp of excess while
# skipping 100 of 162 rebalances, turnover 27% -> 18%, break-even 207 -> 297,
# t 1.92. It was also a sharp peak - 1.3 collapsed to +27bp - which is the
# signature of a parameter describing the test set rather than the strategy.
#
# Swept on VALIDATION and read once on test, the same idea gives +28bp and
# t 1.10, comfortably worse than not gating at all. Turnover did fall to 16%,
# so the mechanism works; the edge simply falls with it, and the trade is
# roughly one-for-one.
#
# Restore the sweep to (0.0, 1.0, 1.2, 1.4) to reproduce that.
GATES = (0.0,)

# In --fast mode the smoothing has already been applied to the features, so the
# post-hoc prediction smoothing sweep collapses to a single no-op value.
FAST_WINDOWS = (1,)


def fold_dates(panel: pd.DataFrame, start_year: int, horizon: int
               ) -> list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
    """(train_end, val_end, test_end) for each fold, one per year."""
    last = panel["timestamp"].max()
    folds = []
    year = start_year
    while True:
        train_end = pd.Timestamp(f"{year}-12-31")
        val_end = pd.Timestamp(f"{year + 1}-12-31")
        test_end = pd.Timestamp(f"{year + 2}-12-31")
        if val_end >= last:
            break
        folds.append((train_end, val_end, min(test_end, last)))
        year += 1
    return folds


def cut(panel: pd.DataFrame, lo: pd.Timestamp | None, hi: pd.Timestamp,
        horizon: int) -> pd.DataFrame:
    """Rows in (lo, hi], with the embargo applied at the lower edge."""
    gap = pd.offsets.Day(int(horizon * 1.5))
    frame = panel[panel["timestamp"] <= hi]
    if lo is not None:
        frame = frame[frame["timestamp"] > lo + gap]
    return frame.copy()


def choose(val_panel: pd.DataFrame, predictions: np.ndarray, every: int,
           capital: float, slippage: float, segment: str,
           windows: tuple[int, ...] = WINDOWS) -> dict:
    """Pick size/buffer/smoothing on this fold's validation year.

    Selected on break-even rather than raw return. Break-even asks "how much
    cost could this survive", which is the question that killed the first two
    versions, and it is far more stable across folds than an annualised return
    computed from twelve observations.
    """
    best = None
    for k in SIZES:
        for buffer in BUFFERS:
            for window in windows:
                val_panel["pred"] = predictions
                val_panel["pred"] = backtest_module.smooth(val_panel, window)
                for gate in GATES:
                    book = backtest_module.run(val_panel, k, buffer, every,
                                               capital, slippage, segment,
                                               "equal", gate)
                    if len(book) < 3:
                        continue
                    score = backtest_module.breakeven_bp(book)
                    if best is None or score > best["score"]:
                        best = {"k": k, "buffer": buffer, "window": window,
                                "gate": gate, "score": score}
    return best or {"k": config.TOP_K, "buffer": config.HOLD_BUFFER,
                    "window": 20, "gate": 0.0, "score": float("nan")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start-year", type=int, default=2011,
                        help="first fold trains on data up to this year end")
    parser.add_argument("--capital", type=float, default=500000.0)
    parser.add_argument("--every", type=int, default=config.REBALANCE_EVERY)
    parser.add_argument("--slippage", type=float,
                        default=costs_module.DEFAULT_SLIPPAGE_BP)
    parser.add_argument("--segment", choices=("delivery", "intraday"),
                        default="delivery")
    parser.add_argument("--epochs", type=int, default=60)
    parser.add_argument("--signal", default=config.PRODUCTION_SIGNAL,
                        choices=("nn", "lightgbm", "tabpfn"),
                        help="which model generates the ranking; defaults to "
                             "whatever production trades")
    parser.add_argument("--max-context", type=int, default=8000,
                        help="tabpfn only: rows of in-context training data")
    parser.add_argument("--fast", action="store_true",
                        help="smooth the features instead of the predictions, "
                             "and evaluate only on rebalance dates - 20x fewer "
                             "model calls, required for tabpfn on CPU")
    parser.add_argument("--feature-smooth", type=int, default=20,
                        help="--fast only: trailing days averaged into features")
    parser.add_argument("--columns", choices=("model", "core"),
                        default="model",
                        help="which feature set the signal trains on")
    parser.add_argument("--seed", type=int, default=None,
                        help="override the signal's own default seed; omitted "
                             "leaves each signal on its config.py default")
    args = parser.parse_args()

    if args.signal == "tabpfn":
        ok, message = signals_module.tabpfn_available()
        if not ok:
            raise SystemExit(f"cannot run tabpfn: {message}")

    columns = (features_module.MODEL_COLUMNS if args.columns == "model"
               else features_module.CORE_COLUMNS)

    horizon = config.TARGET_HORIZON
    panel = features_module.cross_sectionalize(features_module.build_panel())

    # In fast mode the trailing average moves from the predictions to the
    # features, and every date the book never consults is dropped. Both are
    # applied before the split so training sees the same representation the
    # book will be scored on.
    # Thinning is applied to the panels we PREDICT on, never to the one we
    # train on. An earlier version thinned before the split and cut the
    # network's training rows from 116,000 to 5,800 - the resulting collapse
    # from +39bp to +3bp looked like evidence about smoothing and was evidence
    # about sample size. Inference volume is the cost being managed here;
    # training volume is free.
    windows, every = WINDOWS, args.every
    if args.fast:
        panel = features_module.smooth_features(panel, args.feature_smooth)
        windows, every = FAST_WINDOWS, 1

    signal_kwargs = {"epochs": args.epochs,
                     "max_context": args.max_context,
                     "columns": columns}
    if args.seed is not None:
        signal_kwargs["seed"] = args.seed

    folds = fold_dates(panel, args.start_year, horizon)
    print(f"\n{len(folds)} folds, signal '{args.signal}'"
          f"{', fast mode' if args.fast else ''}\n")

    print(f"{'test year':<11}{'cfg':<16}{'net':>8}{'bench':>8}{'excess bp':>11}"
          f"{'turn':>7}{'b/e bp':>9}{'acc':>9}")
    print("-" * 79)

    collected, baseline_books = [], {name: [] for name in
                                     ("momentum (mom_126)", "reversal (-rev_5)",
                                      "long-horizon (mom_252)")}
    # The same rows the table below prints, kept in a form runs.py can store.
    # Printing and recording are fed from one place so the log cannot drift
    # away from what the operator actually read on screen.
    fold_records = []

    for train_end, val_end, test_end in folds:
        train_panel = cut(panel, None, train_end, horizon)
        val_panel = cut(panel, train_end, val_end, horizon)
        test_panel = cut(panel, val_end, test_end, horizon)
        if args.fast:
            val_panel = features_module.thin_to_rebalance(val_panel, args.every)
            test_panel = features_module.thin_to_rebalance(test_panel, args.every)
        minimum = 60 if args.fast else 200
        if len(val_panel) < minimum or len(test_panel) < minimum:
            continue

        signal = signals_module.build(args.signal, **signal_kwargs)
        signal.fit(train_panel, val_panel)
        chosen = choose(val_panel, signal.predict(val_panel),
                        every, args.capital, args.slippage, args.segment,
                        windows)

        test_panel["pred"] = signal.predict(test_panel)
        test_panel["pred"] = backtest_module.smooth(test_panel, chosen["window"])
        fold_acc, fold_acc_t, _ = metrics_module.accuracy(
            test_panel["pred"].to_numpy(),
            metrics_module.rank_target(test_panel),
            test_panel["timestamp"].to_numpy())
        book = backtest_module.run(test_panel, chosen["k"], chosen["buffer"],
                                   every, args.capital, args.slippage,
                                   args.segment, "equal", chosen["gate"])
        if book.empty:
            continue
        stats = backtest_module.summarise(book, horizon)
        collected.append(book)

        label = f"k{chosen['k']} b{chosen['buffer']} s{chosen['window']} g{chosen['gate']}"
        # flush=True so a piped run shows progress. Without it Python holds the
        # whole report in an 8KB buffer and emits nothing until the process
        # exits, which on a slow signal is indistinguishable from a hang.
        print(f"{str(test_end.year):<11}{label:<16}{stats['net']:>7.1f}%"
              f"{stats['benchmark']:>7.1f}%{stats['net_excess_bp']:>+11.0f}"
              f"{stats['turnover']:>6.0%}"
              f"{backtest_module.breakeven_bp(book):>9.1f}"
              f"{fold_acc:>+9.3f}", flush=True)

        fold_records.append({
            "test_year": int(test_end.year),
            "train_end": str(train_end.date()),
            "val_end": str(val_end.date()),
            "test_end": str(test_end.date()),
            # What validation picked, and the break-even it picked it on. Kept
            # whole: a fold's result is only interpretable next to the
            # configuration that produced it.
            "chosen": dict(chosen),
            "stats": stats,
            "breakeven_bp": backtest_module.breakeven_bp(book),
            "acc": fold_acc,
            "acc_t": fold_acc_t,
        })

        # Baselines get this fold's chosen configuration too - identical
        # treatment, so any difference left is the model and not the plumbing.
        for name, values in metrics_module.baselines(test_panel).items():
            test_panel["pred"] = values
            test_panel["pred"] = backtest_module.smooth(test_panel, chosen["window"])
            part = backtest_module.run(test_panel, chosen["k"], chosen["buffer"],
                                       every, args.capital, args.slippage,
                                       args.segment)
            if not part.empty:
                baseline_books[name].append(part)

    if not collected:
        raise SystemExit("no folds produced a book")

    everything = pd.concat(collected, ignore_index=True)
    pooled = backtest_module.summarise(everything, horizon)
    round_trip = costs_module.cost_bp(args.capital / config.TOP_K, args.segment,
                                      args.slippage)

    print("\n" + "=" * 70)
    print(f"POOLED over {pooled['periods']} non-overlapping periods "
          f"({len(collected)} folds)\n")
    print(f"{'signal':<24}{'net excess bp':>15}{'t':>8}{'hit':>7}{'b/e bp':>9}")
    print("-" * 63)
    print(f"{args.signal:<24}{pooled['net_excess_bp']:>+15.0f}"
          f"{pooled['t_stat']:>+8.2f}{pooled['hit']:>7.0%}"
          f"{backtest_module.breakeven_bp(everything):>9.1f}")
    baseline_stats = {}
    for name, books in baseline_books.items():
        if not books:
            continue
        merged = pd.concat(books, ignore_index=True)
        stats = backtest_module.summarise(merged, horizon)
        baseline_stats[name] = dict(stats,
                                    breakeven_bp=backtest_module.breakeven_bp(merged))
        print(f"{name:<24}{stats['net_excess_bp']:>+15.0f}{stats['t_stat']:>+8.2f}"
              f"{stats['hit']:>7.0%}{backtest_module.breakeven_bp(merged):>9.1f}")

    edge_value, edge_name = metrics_module.edge_bp(pooled, baseline_stats)
    if edge_name:
        print(f"\nEDGE {edge_value:+.0f} bp over best baseline "
              f"'{edge_name}'")
    print(f"\nactual round trip {round_trip:.1f} bp | "
          f"mean turnover {pooled['turnover']:.0%} | "
          f"net {pooled['net']:.1f}%/yr vs benchmark {pooled['benchmark']:.1f}%/yr")

    # Recorded before the verdict is printed, because the verdict is the part
    # that gets remembered and the numbers behind it are the part that gets
    # lost. Failing to write is reported and does not fail the run.
    run_id = runs_module.record(
        "walkforward",
        runs_module.settings(args),
        fold_records,
        dict(pooled, breakeven_bp=backtest_module.breakeven_bp(everything)),
        baseline_stats,
        round_trip,
        edge={"net_excess_bp": edge_value, "baseline": edge_name},
    )
    if run_id:
        print(f"recorded as run {run_id} in {runs_module.RUNS_PATH}")

    if pooled["t_stat"] >= 2.0 and backtest_module.breakeven_bp(everything) > round_trip:
        print("\nSurvives costs across regimes with a defensible t-statistic.")
    elif backtest_module.breakeven_bp(everything) > round_trip:
        print(f"\nClears costs, but t={pooled['t_stat']:.2f} across "
              f"{pooled['periods']} periods is still not significance.")
    else:
        print("\nDoes not clear costs once every year is counted, not just "
              "the convenient ones.")


if __name__ == "__main__":
    main()
