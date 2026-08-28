"""Replay the book order by order, with whole shares and a cash balance.

WHY THIS EXISTS WHEN backtest.py ALREADY REPORTS RETURNS

backtest.py works in weights. Every position is exactly 1/k of the book, cash is
infinite and infinitely divisible, and turnover is a fraction. That is the right
abstraction for asking whether a signal has an edge, and it quietly assumes away
four things that decide whether the edge is reachable with Rs 5,00,000:

  Whole shares. One LT share is Rs 4,057. A sixth of a Rs 5,00,000 book is
  Rs 83,333, which buys 20 shares - and 20 shares is Rs 81,140, not Rs 83,333.
  The book is never actually equal-weight; it is equal-weight rounded down, and
  the error grows as the account shrinks.

  Cash constraints. Buys cannot exceed the balance. In weight-space a rebalance
  is simultaneous; here the sells must clear first, and if they do not raise
  enough, the last buy does not happen at all.

  The minimum trade floor. orders.py suppresses trades under Rs 5,000 because
  fixed charges eat them. backtest.py has no concept of a trade too small to be
  worth making, so it makes them and pays a percentage for them.

  Charges on the real rupee amount, not on turnover-as-a-fraction.

A strategy that looks fine in weights and fails here fails for reasons that will
not go away by finding a better model.

NO LOOKAHEAD

At each rebalance the panel is truncated to rows at or before that date, and the
broker is told to price as of that date. The model is loaded once and is the
same model throughout, which does mean a model fitted on data that includes part
of the replay window - use walkforward.py for the clean answer on edge, and this
for the mechanics of reaching it.

Run with:  py -3.13 paper.py                   replay the last year
           py -3.13 paper.py --days 500 --capital 200000
"""

from __future__ import annotations

import argparse
import os

import numpy as np
import pandas as pd

import broker as broker_module
import config
import features as features_module
import orders as orders_module


def load_model():
    """The production signal. Not necessarily a network - see production.py."""
    import production

    return production.load()


def build_panel() -> pd.DataFrame:
    """The cross-sectional panel every walk here is driven from.

    Split out so a caller running both the model and the hindsight ceiling
    over the same window pays for it once rather than twice.
    """
    return features_module.cross_sectionalize(features_module.build_panel())


def perfect_targets(closes: dict, symbols: list[str], date, ahead,
                    top_k: int) -> list[str]:
    """The names that actually rose most between the two dates.

    This is the cheat. It reads prices that had not printed yet, which is the
    entire point: it answers "what was there to be had", not "what could have
    been known". Only risers are returned - a bot that saw the future would
    sit in cash rather than buy the least-bad loser.
    """
    gains = []
    for symbol in symbols:
        series = closes.get(symbol)
        if series is None or series.empty:
            continue
        now = series[series.index <= date]
        later = series[series.index <= ahead]
        if now.empty or later.empty:
            continue
        start, end = float(now.iloc[-1]), float(later.iloc[-1])
        if start <= 0:
            continue
        gains.append((end / start - 1.0, symbol))
    gains.sort(key=lambda pair: -pair[0])
    return [symbol for gain, symbol in gains[:top_k] if gain > 0]


def ceiling_curve(days: int, capital: float, every: int, top_k: int,
                  panel: pd.DataFrame | None = None) -> pd.DataFrame:
    """What a bot that guessed right every single time would have made.

    Same universe, same rebalance cadence, same broker and the same charges -
    only the ranking is replaced by hindsight. It is not attainable and is not
    meant to be. It is there so the model's curve is read against the money
    that was actually on the table, instead of against zero, which flatters
    every strategy that makes anything at all.
    """
    import data as data_module

    if panel is None:
        panel = build_panel()
    dates = np.sort(panel["timestamp"].unique())[-days:]
    if len(dates) < every * 2:
        raise SystemExit(f"only {len(dates)} dates available")

    closes = {}
    for symbol in sorted(panel["symbol"].unique()):
        try:
            frame = data_module.fetch(symbol, quiet=True)
        except Exception:                                       # noqa: BLE001
            continue
        if not frame.empty:
            closes[symbol] = frame["close"]

    account = broker_module.PaperBroker(
        capital=capital, path=os.path.join(config.MODEL_DIR, "ceiling.json"))
    account.reset(capital)

    rows = []
    for index, date in enumerate(dates):
        account.pricing_date = pd.Timestamp(date)

        if index % every == 0:
            frame = panel[panel["timestamp"] == date]
            if len(frame) >= top_k * 2:
                ahead = pd.Timestamp(dates[min(index + every, len(dates) - 1)])
                targets = perfect_targets(closes, frame["symbol"].tolist(),
                                          pd.Timestamp(date), ahead, top_k)
                if targets:
                    try:
                        orders_module.execute(
                            orders_module.plan(targets, account), account)
                    except Exception:                           # noqa: BLE001
                        pass

        equity, _ = account.value()
        rows.append({"timestamp": pd.Timestamp(date), "equity": equity})

    account.pricing_date = None
    return pd.DataFrame(rows)


def replay(days: int, capital: float, every: int, top_k: int,
           quiet: bool = False, panel: pd.DataFrame | None = None
           ) -> pd.DataFrame:
    """Walk the last `days` sessions, rebalancing every `every` of them."""
    signal, _ = load_model()
    if panel is None:
        panel = build_panel()

    dates = np.sort(panel["timestamp"].unique())[-days:]
    if len(dates) < every * 2:
        raise SystemExit(f"only {len(dates)} dates available")

    account = broker_module.PaperBroker(
        capital=capital, path=os.path.join(config.MODEL_DIR, "replay.json"))
    account.reset(capital)

    rows = []
    for index, date in enumerate(dates):
        account.pricing_date = pd.Timestamp(date)

        if index % every == 0:
            frame = panel[panel["timestamp"] == date].copy()
            if len(frame) >= top_k * 2:
                frame["score"] = signal.predict(frame)
                targets = (frame.sort_values("score", ascending=False)
                           .head(top_k)["symbol"].tolist())
                plan = orders_module.plan(targets, account)
                results = orders_module.execute(plan, account)
                failed = [r for r in results if "error" in r]
                if failed and not quiet:
                    print(f"  {pd.Timestamp(date).date()}: "
                          f"{failed[0]['error'][:70]}")

        equity, unrealised = account.value()
        rows.append({"timestamp": pd.Timestamp(date), "equity": equity,
                     "cash": account.cash(), "unrealised": unrealised,
                     "positions": len(account.holdings()),
                     "trades": len(account.state["trades"])})

    account.pricing_date = None
    return pd.DataFrame(rows)


def benchmark_curve(dates: pd.Series, capital: float) -> pd.Series:
    """Equal-weight the whole universe, buy once, hold. Costs paid on entry only.

    The comparison that matters. It requires no model, no rebalancing and no
    monthly charge sheet, and in this project it has repeatedly been the thing
    to beat rather than a formality.
    """
    import data as data_module

    frames = {}
    for symbol in config.UNIVERSE:
        try:
            frames[symbol] = data_module.fetch(symbol, quiet=True)["close"]
        except Exception:                                       # noqa: BLE001
            continue
    closes = pd.DataFrame(frames).reindex(dates).ffill()
    if closes.empty:
        return pd.Series(capital, index=dates)

    normalised = closes.div(closes.iloc[0])
    # One entry cost, then never traded again.
    import costs as costs_module
    entry = costs_module.round_trip(capital / max(len(closes.columns), 1)).buy.total
    drag = entry * len(closes.columns)
    return normalised.mean(axis=1) * (capital - drag)


def charges_and_fills(state=None) -> tuple:
    """What the replay actually paid, read from the paper account it wrote."""
    if state is None:
        state = broker_module.PaperBroker(
            path=os.path.join(config.MODEL_DIR, "replay.json")).state
    trades = state.get("trades", [])
    return float(sum(t["charges"] for t in trades)), len(trades)


def summarise(curve: pd.DataFrame, bench: pd.Series, capital: float,
              state=None) -> dict:
    """One replay as a dict of numbers, defined here and nowhere else.

    Both callers - `main()` and the GUI's Sim tab - computed closing equity,
    drawdown and charges separately. Two definitions of "what the sim said" is
    one more than a log can store honestly, so they now share this.

    Deliberately not the same field names as backtest.summarise. A replay is a
    single equity path: there is no distribution over periods, so no t-stat and
    no break-even are computable from it, and borrowing those names would let a
    sim row be read as though a significance test had been run on it.
    """
    start = float(curve["equity"].iloc[0])
    end = float(curve["equity"].iloc[-1])
    years = len(curve) / 252.0
    annual = ((end / start) ** (1 / years) - 1) * 100 if years > 0 and start else 0.0

    bench_end = float(bench.iloc[-1])
    if years > 0 and capital:
        bench_annual = ((bench_end / capital) ** (1 / years) - 1) * 100
    else:
        bench_annual = 0.0

    daily = curve["equity"].pct_change().dropna()
    peak = curve["equity"].cummax()
    drawdown = float(((curve["equity"] - peak) / peak).min() * 100)
    charges, fills = charges_and_fills(state)

    return {
        "sessions": int(len(curve)),
        "start_date": str(curve["timestamp"].iloc[0].date()),
        "end_date": str(curve["timestamp"].iloc[-1].date()),
        "capital": float(capital),
        "final_equity": end,
        "benchmark_equity": bench_end,
        "annual_pct": annual,
        "benchmark_annual_pct": bench_annual,
        "excess_annual_pct": annual - bench_annual,
        "max_drawdown_pct": drawdown,
        "sharpe": (float(daily.mean() / daily.std() * np.sqrt(252))
                   if daily.std() else float("nan")),
        "trades": fills,
        "charges_paid": charges,
        "charges_pct_capital": (100.0 * charges / capital) if capital else 0.0,
    }


def record(stats: dict, every: int, top_k: int, segment: str = "delivery",
           slippage_bp=None) -> int:
    """Append this replay to the run log. Returns 0 if it could not be written.

    The modelled round trip travels with the record so a sim row can be read
    beside a walk-forward one, and `charges_paid` in the stats is what this
    replay was actually billed - the order sim knows that exactly, which is the
    better number of the two and the reason both are stored.
    """
    import costs as costs_module
    import runs as runs_module

    slippage = (costs_module.DEFAULT_SLIPPAGE_BP if slippage_bp is None
                else slippage_bp)
    per_trade = stats["capital"] / max(int(top_k), 1)
    round_trip = costs_module.cost_bp(per_trade, segment, slippage)
    return runs_module.record(
        "sim",
        runs_module.sim_settings(stats, every, top_k, segment, slippage),
        [], stats, {}, round_trip)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=252)
    parser.add_argument("--capital", type=float, default=500000.0)
    parser.add_argument("--every", type=int, default=config.REBALANCE_EVERY)
    parser.add_argument("--topk", type=int, default=config.TOP_K)
    args = parser.parse_args()

    curve = replay(args.days, args.capital, args.every, args.topk)
    bench = benchmark_curve(curve["timestamp"], args.capital)

    stats = summarise(curve, bench, args.capital)
    end = stats["final_equity"]
    annual = stats["annual_pct"]
    bench_end = stats["benchmark_equity"]
    bench_annual = stats["benchmark_annual_pct"]
    drawdown = stats["max_drawdown_pct"]
    daily = curve["equity"].pct_change().dropna()

    print(f"\n{len(curve)} sessions, {curve['timestamp'].iloc[0].date()} .. "
          f"{curve['timestamp'].iloc[-1].date()}\n")
    print(f"  opening equity      Rs {args.capital:>12,.0f}")
    print(f"  closing equity      Rs {end:>12,.0f}   {annual:+.1f}%/yr")
    print(f"  buy-and-hold all 30 Rs {bench_end:>12,.0f}   {bench_annual:+.1f}%/yr")
    print(f"  difference          Rs {end - bench_end:>+12,.0f}")
    print(f"\n  trades              {curve['trades'].iloc[-1]:>12}")
    print(f"  max drawdown        {drawdown:>12.1f}%")
    if daily.std():
        print(f"  sharpe (rf 0)       {daily.mean() / daily.std() * np.sqrt(252):>12.2f}")

    charges = stats["charges_paid"]
    print(f"  charges paid        Rs {charges:>12,.0f}   "
          f"({stats['charges_pct_capital']:.2f}% of capital)")

    run_id = record(stats, args.every, args.topk)
    if run_id:
        import runs as runs_module

        print(f"  recorded as run {run_id} in {runs_module.RUNS_PATH}")

    print(f"\n{'date':<13}{'equity':>13}{'cash':>12}{'pos':>6}{'trades':>8}")
    print("-" * 52)
    for _, row in curve.iloc[::max(len(curve) // 12, 1)].iterrows():
        print(f"{str(row['timestamp'].date()):<13}{row['equity']:>13,.0f}"
              f"{row['cash']:>12,.0f}{row['positions']:>6}{row['trades']:>8}")

    if end < bench_end:
        print("\nThe traded book lost to buying the universe and never "
              "touching it again.")
    else:
        print("\nThe traded book beat buy-and-hold over this window.")


if __name__ == "__main__":
    main()
