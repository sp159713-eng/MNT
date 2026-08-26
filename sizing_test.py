"""Does confidence sizing, or a confidence gate, actually beat equal weight?

Both ideas are run through the SAME walk-forward as everything else: refit each
year, choose k and buffer on that fold's validation, read the test year once.
The only thing that varies between rows is how the chosen names are weighted, or
whether a weak date is traded at all.

Read the equal/0.0 row as the control. It is the current production behaviour,
and every other row is an attempt to beat it.

Run with:  py -3.13 sizing_test.py
           py -3.13 sizing_test.py --start-year 2014
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
import signals as signals_module
import walkforward as walkforward_module

warnings.filterwarnings("ignore")

# Gates are in cross-sectional standard deviations of the score. The range that
# matters was found by measuring, not guessed: the spread between the chosen
# names and the average name runs 0.81 to 1.56 with a mean of 1.22, so a gate
# below 0.8 can never fire and one above 1.6 always does. An earlier version
# swept 0.0-1.0 and skipped exactly zero dates in 162 periods - it measured
# nothing and looked like it had measured something.
SCHEMES = ("equal", "score", "rank", "invvol")
GATES = (0.0, 0.3, 0.6, 1.0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start-year", type=int, default=2011)
    parser.add_argument("--capital", type=float, default=500000.0)
    parser.add_argument("--signal", default=config.PRODUCTION_SIGNAL)
    parser.add_argument("--schemes", nargs="*", default=list(SCHEMES))
    parser.add_argument("--gates", nargs="*", type=float, default=list(GATES))
    args = parser.parse_args()

    schemes, gates = args.schemes, args.gates

    horizon = config.TARGET_HORIZON
    slippage = costs_module.DEFAULT_SLIPPAGE_BP
    panel = features_module.cross_sectionalize(features_module.build_panel())
    folds = walkforward_module.fold_dates(panel, args.start_year, horizon)
    print(f"\n{len(folds)} folds, signal '{args.signal}'\n", flush=True)

    # Fit once per fold and reuse the predictions across every scheme and gate.
    # Refitting inside the loop would multiply the cost by 16 and, worse, let
    # model noise leak into the comparison between sizing rules.
    predictions = []
    for train_end, val_end, test_end in folds:
        train_panel = walkforward_module.cut(panel, None, train_end, horizon)
        val_panel = walkforward_module.cut(panel, train_end, val_end, horizon)
        test_panel = walkforward_module.cut(panel, val_end, test_end, horizon)
        if len(val_panel) < 200 or len(test_panel) < 200:
            continue
        signal = signals_module.build(args.signal)
        signal.fit(train_panel, val_panel)
        val_panel = val_panel.copy()
        test_panel = test_panel.copy()
        val_panel["pred"] = signal.predict(val_panel)
        test_panel["pred"] = signal.predict(test_panel)
        predictions.append((val_panel, test_panel, test_end.year))
        print(f"  fitted fold {test_end.year}", flush=True)

    print(f"\n{'sizing':<9}{'gate':>6}{'excess bp':>11}{'t':>7}{'turn':>7}"
          f"{'b/e bp':>9}{'net %/yr':>10}{'skipped':>9}")
    print("-" * 68)

    control = None
    for scheme in schemes:
        for gate in gates:
            books, skipped, total = [], 0, 0
            for val_panel, test_panel, _year in predictions:
                # Config still chosen on validation, under the same scheme and
                # gate, so each row is selected as honestly as the control.
                best = None
                for k in walkforward_module.SIZES:
                    for buffer in walkforward_module.BUFFERS:
                        for window in walkforward_module.WINDOWS:
                            val_panel["pred_s"] = backtest_module.smooth(
                                val_panel.assign(pred=val_panel["pred"]), window)
                            trial = val_panel.assign(pred=val_panel["pred_s"])
                            book = backtest_module.run(
                                trial, k, buffer, config.REBALANCE_EVERY,
                                args.capital, slippage, "delivery", scheme, gate)
                            if len(book) < 3:
                                continue
                            score = backtest_module.breakeven_bp(book)
                            if best is None or score > best["score"]:
                                best = {"k": k, "buffer": buffer,
                                        "window": window, "score": score}
                if best is None:
                    continue

                scored = test_panel.assign(
                    pred=backtest_module.smooth(test_panel, best["window"]))
                book = backtest_module.run(
                    scored, best["k"], best["buffer"], config.REBALANCE_EVERY,
                    args.capital, slippage, "delivery", scheme, gate)
                if book.empty:
                    continue
                books.append(book)
                skipped += book.attrs.get("skipped", 0)
                total += len(book)

            if not books:
                continue
            everything = pd.concat(books, ignore_index=True)
            stats = backtest_module.summarise(everything, horizon)
            breakeven = backtest_module.breakeven_bp(everything)
            row = (f"{scheme:<9}{gate:>6.1f}{stats['net_excess_bp']:>+11.0f}"
                   f"{stats['t_stat']:>+7.2f}{stats['turnover']:>7.0%}"
                   f"{breakeven:>9.1f}{stats['net']:>10.1f}"
                   f"{skipped}/{total:<6}")
            if scheme == "equal" and gate == 0.0:
                control = stats["net_excess_bp"]
                row += "  <- control"
            elif control is not None:
                row += f"  {stats['net_excess_bp'] - control:+.0f}bp"
            print(row, flush=True)

    print("\nControl is equal weight with no gate - current production "
          "behaviour.\nAnything not clearly above it is not worth the extra "
          "moving part.")


if __name__ == "__main__":
    main()
