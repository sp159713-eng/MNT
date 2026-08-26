"""Does the network's ordering survive an actual NSE charge sheet?

WHAT THIS DOES DIFFERENTLY FROM THE USUAL BACKTEST

Non-overlapping periods. The panel has a row every day carrying a 20-day forward
return, so consecutive rows share 19 of their 20 days. Trading that panel daily
and averaging the results produces a return series whose standard error is
understated by roughly sqrt(20), and therefore a t-statistic inflated by the
same factor. Here the book rebalances every 20 trading days and holds, so each
period's return is measured on days no other period touches. Fewer observations,
honest ones.

Costs by rupee size, not by percentage. Charges come from costs.py at the actual
position value, so the fixed components - Rs 20 brokerage cap, Rs 15.34 DP fee -
land where they really land. This matters more than it sounds: at Rs 50,000 a
position the round trip is 35bp, at Rs 5,000 it is 63bp. A strategy is not
"profitable" in the abstract, it is profitable at a size.

Turnover, not position count. If the new top six shares four names with the old,
one round trip is charged for two names, not six. Charging per position would
understate the hold buffer's entire purpose.

THE COMPARISON THAT DECIDES IT

Every book is printed next to equal-weight-everything. That benchmark pays
nothing, trades nothing, and requires no model. NNS found it beat every
model-based sizing rule tried against it. A network that cannot beat a book that
holds all thirty names and never rebalances has not earned its complexity, no
matter what its IC was.

Run with:  py -3.13 backtest.py
           py -3.13 backtest.py --capital 200000 --topk 6 --buffer 6
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
import config
import costs as costs_module
import features as features_module
import metrics as metrics_module


def select(frame: pd.DataFrame, k: int, held: set[str], buffer: int) -> list[str]:
    """The k names to hold, letting incumbents keep their place inside k+buffer.

    Without the buffer a name that slips from sixth to seventh is sold and
    rebought a period later, paying two round trips for a rank change that is
    almost entirely noise. With it, the book only trades when a holding falls
    clearly out of favour. This is the cheapest turnover reduction available and
    it does not touch the predictions at all.
    """
    ordered = frame.sort_values("pred", ascending=False)["symbol"].tolist()
    if not buffer:
        return ordered[:k]

    wide = ordered[:min(k + buffer, len(ordered))]
    kept = [s for s in wide if s in held][:k]
    for symbol in ordered:
        if len(kept) >= k:
            break
        if symbol not in kept:
            kept.append(symbol)
    return kept


def smooth(panel: pd.DataFrame, window: int) -> np.ndarray:
    """Average each name's prediction over its own trailing `window` days.

    The diagnosis this answers: the raw network turns over 48% of the book every
    rebalance against momentum's 18%, for no more edge - so it pays the charge
    sheet roughly three times as often to end up in the same place. That is not
    a bad ordering, it is an unstable one. The network is re-deciding the
    ranking every day on features that themselves jitter day to day.

    Averaging a name's own score over the trailing month keeps whatever
    persistent view the model holds and discards the part that changes its mind
    every session. It uses only past predictions, so it introduces no lookahead.

    This is the same trick as the hold buffer applied one level earlier: the
    buffer stops noisy ranks from causing trades, this stops the ranks being
    noisy in the first place.
    """
    if window <= 1:
        return panel["pred"].to_numpy()
    ordered = panel.sort_values(["symbol", "timestamp"])
    rolled = (ordered.groupby("symbol")["pred"]
              .transform(lambda s: s.rolling(window, min_periods=1).mean()))
    return rolled.reindex(panel.index).to_numpy()


def run(panel: pd.DataFrame, k: int, buffer: int, every: int,
        capital: float, slippage_bp: float, segment: str,
        scheme: str = "equal", gate: float = 0.0) -> pd.DataFrame:
    """Walk non-overlapping periods, recording gross return, turnover and cost.

    `scheme` sizes the positions (see sizing.py). `gate` is the minimum
    score-spread, in cross-sectional standard deviations, required to rebalance
    at all - below it the previous book is simply held, paying nothing.
    """
    import sizing as sizing_module

    dates = sorted(panel["timestamp"].unique())[::every]
    held: set[str] = set()
    previous: pd.Series = pd.Series(dtype=float)
    rows = []
    skipped = 0

    for date in dates:
        frame = panel[panel["timestamp"] == date]
        if len(frame) < k * 2:
            continue

        candidate = select(frame, k, held, buffer)

        # The gate. A weak date is one where the top k barely separate from the
        # average name; holding through it costs nothing and risks only the
        # difference between a stale book and a marginally better one.
        confident = sizing_module.spread(frame, candidate) >= gate
        if held and not confident:
            chosen = list(held)
            skipped += 1
        else:
            chosen = candidate

        realised = frame.set_index("symbol")["target"]
        allocation = sizing_module.weights(frame, chosen, scheme)

        # Turnover as absolute weight CHANGE, halved so replacing one name with
        # another counts once. Weight-aware, because a sizing scheme can churn
        # money between names it already holds and that trades just as surely.
        if len(previous):
            change = allocation.subtract(previous, fill_value=0.0).abs().sum() / 2.0
        else:
            change = allocation.abs().sum()
        turnover = float(change)

        gross = float((allocation * realised.reindex(allocation.index)).sum())
        benchmark = float(realised.mean())
        previous = allocation

        # Charge the real schedule at the real position size, on the fraction
        # of the book that actually changed hands.
        position_value = capital / k
        round_trip_bp = costs_module.cost_bp(position_value, segment, slippage_bp)
        cost = turnover * round_trip_bp / 10000.0

        rows.append({"timestamp": date, "gross": gross, "benchmark": benchmark,
                     "excess": gross - benchmark, "turnover": turnover,
                     "cost": cost, "net": gross - cost,
                     "net_excess": gross - benchmark - cost,
                     "names": len(chosen)})
        held = set(chosen)

    book = pd.DataFrame(rows)
    if len(book):
        book.attrs["skipped"] = skipped
    return book


def summarise(book: pd.DataFrame, horizon: int) -> dict:
    """Annualised return and the t-statistic on the excess, after costs."""
    if book.empty:
        return {}
    periods = 252.0 / horizon
    net_excess = book["net_excess"]
    t_stat = (float(net_excess.mean() / (net_excess.std(ddof=1) / np.sqrt(len(net_excess))))
              if len(net_excess) > 1 and net_excess.std() else 0.0)

    def annualise(series: pd.Series) -> float:
        return float((1.0 + series.mean()) ** periods - 1.0) * 100

    return {
        "gross": annualise(book["gross"]),
        "net": annualise(book["net"]),
        "benchmark": annualise(book["benchmark"]),
        "net_excess_bp": float(net_excess.mean() * 10000),
        "net_excess_annual": annualise(net_excess),
        "t_stat": t_stat,
        "turnover": float(book["turnover"].mean()),
        "cost_bp": float(book["cost"].mean() * 10000),
        "hit": float((net_excess > 0).mean()),
        "periods": len(book),
    }


def breakeven_bp(book: pd.DataFrame) -> float:
    """Round-trip cost at which the excess reaches zero. The number that matters.

    Compare it against costs.py. If break-even is below the real charge, the
    strategy is a losing one no matter how good the IC looked.
    """
    turnover = book["turnover"].mean()
    if turnover <= 0:
        return float("inf")
    return float(book["excess"].mean() / turnover * 10000)


def select_on_validation(val_panel: pd.DataFrame, test_panel: pd.DataFrame,
                         raw_val: np.ndarray, raw_test: np.ndarray,
                         args) -> None:
    """Choose k, buffer and smoothing on validation, then read test exactly once.

    The discipline matters more than the sweep. There are 36 combinations below,
    and the best of 36 cells read off the test set is optimistically biased by
    construction - that is how a strategy that earns nothing acquires a
    backtest. The configuration is fixed on data the test set never saw, and the
    test number is whatever it turns out to be.

    The gap between "best cell chosen on validation" and "best cell had we
    cheated" is printed at the end, because that gap is the size of the lie you
    would have told yourself.
    """
    sizes, buffers, windows = (4, 6, 8), (0, 4, 8, 12), (1, 10, 20)

    scored = []
    for k in sizes:
        for buffer in buffers:
            for window in windows:
                val_panel["pred"] = raw_val
                val_panel["pred"] = smooth(val_panel, window)
                book = run(val_panel, k, buffer, args.every, args.capital,
                           args.slippage, args.segment)
                if len(book) < 5:
                    continue
                scored.append({"k": k, "buffer": buffer, "window": window,
                               "breakeven": breakeven_bp(book),
                               "turnover": book["turnover"].mean()})

    if not scored:
        print("nothing scoreable on validation")
        return

    best = max(scored, key=lambda row: row["breakeven"])
    print(f"\nSELECTION on validation, {len(scored)} configurations "
          f"(size x buffer x smoothing)")
    print(f"  best: top {best['k']}, buffer {best['buffer']}, "
          f"smooth {best['window']}d  ->  break-even {best['breakeven']:.1f}bp, "
          f"turnover {best['turnover']:.0%}")

    test_panel["pred"] = raw_test
    test_panel["pred"] = smooth(test_panel, best["window"])
    book = run(test_panel, best["k"], best["buffer"], args.every, args.capital,
               args.slippage, args.segment)
    stats = summarise(book, config.TARGET_HORIZON)
    round_trip = costs_module.cost_bp(args.capital / best["k"], args.segment,
                                      args.slippage)

    print("\nTEST, opened once for that configuration:")
    print(f"  gross                 {stats['gross']:.1f}%/yr")
    print(f"  net                   {stats['net']:.1f}%/yr")
    print(f"  benchmark             {stats['benchmark']:.1f}%/yr")
    print(f"  turnover              {stats['turnover']:.0%}")
    print(f"  break-even round trip {breakeven_bp(book):.1f} bp")
    print(f"  actual round trip     {round_trip:.1f} bp")
    print(f"  net excess            {stats['net_excess_bp']:+.0f} bp/period "
          f"(t {stats['t_stat']:+.2f})")

    cheated = []
    for k in sizes:
        for buffer in buffers:
            for window in windows:
                test_panel["pred"] = raw_test
                test_panel["pred"] = smooth(test_panel, window)
                part = run(test_panel, k, buffer, args.every, args.capital,
                           args.slippage, args.segment)
                if len(part) >= 5:
                    cheated.append(breakeven_bp(part))
    if cheated:
        print(f"\n  best cell had we picked on test: {max(cheated):.1f}bp")
        print(f"  median test cell               : {np.median(cheated):.1f}bp")
        print("  The gap between the first of those and the honest number")
        print("  above is what selecting on test would have bought you.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--capital", type=float, default=500000.0)
    parser.add_argument("--topk", type=int, default=config.TOP_K)
    parser.add_argument("--buffer", type=int, default=config.HOLD_BUFFER)
    parser.add_argument("--every", type=int, default=config.REBALANCE_EVERY)
    parser.add_argument("--slippage", type=float,
                        default=costs_module.DEFAULT_SLIPPAGE_BP)
    parser.add_argument("--segment", choices=("delivery", "intraday"),
                        default="delivery")
    parser.add_argument("--split", choices=("test", "val"), default="test")
    parser.add_argument("--smooth", type=int, default=1,
                        help="average each name's prediction over N trailing days")
    parser.add_argument("--select", action="store_true",
                        help="sweep size/buffer/smoothing on validation, "
                             "then read test once")
    args = parser.parse_args()

    panel = features_module.cross_sectionalize(features_module.build_panel())
    train_panel, val_panel, test_panel = features_module.split_panel(panel)
    target_panel = (test_panel if args.split == "test" else val_panel).copy()

    # Whichever model production.py holds - lightgbm by default. This used to
    # rebuild a torch Ranker directly, which meant the backtest silently kept
    # scoring with the neural net after production moved to the GBM.
    import production

    signal, bundle = production.load()

    position_value = args.capital / args.topk
    round_trip = costs_module.cost_bp(position_value, args.segment, args.slippage)
    print(f"\nRs {args.capital:,.0f} across {args.topk} names "
          f"= Rs {position_value:,.0f} each")
    print(f"round trip at that size: {round_trip:.1f} bp ({args.segment})")
    print(f"rebalance every {args.every} days, buffer {args.buffer}, "
          f"{args.split} split\n")

    if args.select:
        select_on_validation(val_panel, test_panel,
                             signal.predict(val_panel),
                             signal.predict(test_panel), args)
        return

    # Every signal gets the SAME buffer and the SAME smoothing. Tuning the
    # model's turnover treatment and then comparing it against a raw one-line
    # baseline is how a tuning result gets mistaken for a modelling result - the
    # baselines benefit from a hold buffer just as much, and momentum is already
    # the slowest-moving signal here, so it has the least to gain and must be
    # given the chance anyway.
    raw = {bundle["name"]: signal.predict(target_panel)}
    raw.update(metrics_module.baselines(target_panel))

    signals = {}
    for name, values in raw.items():
        target_panel["pred"] = values
        signals[name] = smooth(target_panel, args.smooth)

    print(f"{'signal':<24}{'gross':>8}{'net':>8}{'bench':>8}{'excess bp':>11}"
          f"{'t':>7}{'turn':>7}{'b/e bp':>9}")
    print("-" * 80)

    results = {}
    for name, values in signals.items():
        target_panel["pred"] = values
        book = run(target_panel, args.topk, args.buffer, args.every,
                   args.capital, args.slippage, args.segment)
        stats = summarise(book, config.TARGET_HORIZON)
        if not stats:
            continue
        results[name] = (stats, book)
        print(f"{name:<24}{stats['gross']:>7.1f}%{stats['net']:>7.1f}%"
              f"{stats['benchmark']:>7.1f}%{stats['net_excess_bp']:>+11.0f}"
              f"{stats['t_stat']:>+7.2f}{stats['turnover']:>6.0%}"
              f"{breakeven_bp(book):>9.1f}")

    if bundle["name"] not in results:
        raise SystemExit("no periods produced - check the split")

    stats, book = results[bundle["name"]]
    breakeven = breakeven_bp(book)
    print(f"\n{stats['periods']} non-overlapping periods, "
          f"hit rate {stats['hit']:.0%}")
    print(f"benchmark = equal weight all {target_panel['symbol'].nunique()} "
          f"names, no trading, no costs\n")

    print(f"break-even round trip : {breakeven:.1f} bp")
    print(f"actual round trip     : {round_trip:.1f} bp")
    if breakeven < round_trip:
        print("\nThe book does not clear its own costs. Whatever the IC said,")
        print("this is not tradeable as specified.")
    elif stats["t_stat"] < 2.0:
        print(f"\nIt clears costs, but t={stats['t_stat']:.2f} on "
              f"{stats['periods']} periods is not significance.")
        print("Non-overlapping monthly periods over three years cannot produce")
        print("significance; this needs more history before it means anything.")
    else:
        print("\nClears costs with a t-statistic worth taking seriously.")


if __name__ == "__main__":
    main()
