"""The market's state today, recorded so that it becomes a series.

WHY DISPERSION IS THE HEADLINE NUMBER HERE

NNS's pulse watched put-call ratios and open-interest buildup, which are the
right things to watch for a directional book. This book is not directional. It
holds six of thirty names and is measured against the equal-weight average of
all thirty, so the market going up helps it and hurts it in equal measure.

What a cross-sectional book actually needs is DISPERSION: how far apart the
names are moving. When every large cap moves together - correlation near one,
dispersion near zero - there is no gap between the best six and the average, and
no amount of ranking skill produces a return. The same signal, in the same
market, is worth several times more in a high-dispersion month. Charges,
meanwhile, are constant. That is why dispersion sits at the top of this file and
the index level does not.

WHAT IS RECORDED AND WHAT IS MISSING

Obtainable without a broker: index level and return, India VIX, breadth, and the
dispersion measures. Recorded to JSONL every run, so a series accumulates.

Not obtainable: put-call ratio and open-interest buildup, which need a
derivatives feed. The fields are present and null rather than absent, so that a
later run with a broker attached extends the same schema instead of forking it.
`take()` accepts a broker and will populate them if one is given that can.

Run with:  py -3.13 pulse.py                   snapshot and record
           py -3.13 pulse.py --history         what has been recorded so far
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

import config

IST = timezone(timedelta(hours=5, minutes=30))
PULSE_PATH = os.path.join(config.MODEL_DIR, "pulse.jsonl")
VIX = "^INDIAVIX"


def _closes() -> pd.DataFrame:
    """Recent closes for the whole universe, as a dates x symbols frame."""
    import data as data_module

    series = {}
    for symbol in config.UNIVERSE:
        try:
            series[symbol] = data_module.fetch(symbol, quiet=True)["close"]
        except Exception:                                       # noqa: BLE001
            continue
    return pd.DataFrame(series).sort_index()


def take(broker=None) -> dict:
    """One snapshot of market state. Never raises; records what it could get."""
    snapshot = {
        "time": datetime.now(IST).isoformat(timespec="seconds"),
        "errors": {},
    }

    if not config.UNIVERSE:
        snapshot["errors"]["universe"] = ("No stocks yet - add them in "
                                          "the Universe tab.")
        return snapshot

    closes = _closes()
    if closes.empty:
        snapshot["errors"]["universe"] = "no cached prices"
        return snapshot

    snapshot["as_of"] = str(closes.index[-1].date())
    snapshot["symbols"] = int(closes.shape[1])

    returns = closes.pct_change(fill_method=None)
    last = returns.iloc[-1].dropna()
    month = returns.tail(21)

    # --- dispersion: the number this book lives on ---------------------
    # Cross-sectional standard deviation of one day's returns, and of the
    # trailing month's. High means the names are separating; low means the
    # ranking has nothing to earn from.
    snapshot["dispersion_1d"] = float(last.std())
    snapshot["dispersion_21d"] = float(month.std(axis=1).mean())
    # The spread the top six could have captured over the average, one day.
    if len(last) >= 12:
        ordered = last.sort_values(ascending=False)
        snapshot["top6_minus_mean"] = float(ordered.head(6).mean() - last.mean())

    # Average pairwise correlation over the last quarter. Near 1.0 means the
    # universe is trading as one asset and stock selection cannot pay.
    window = returns.tail(63).dropna(axis=1, how="any")
    if window.shape[1] > 2:
        matrix = window.corr().to_numpy()
        upper = matrix[np.triu_indices_from(matrix, k=1)]
        snapshot["avg_correlation"] = float(np.nanmean(upper))

    # --- breadth --------------------------------------------------------
    above_50 = (closes.iloc[-1] > closes.rolling(50).mean().iloc[-1])
    snapshot["pct_above_50dma"] = float(above_50.mean())
    snapshot["advancers"] = int((last > 0).sum())
    snapshot["decliners"] = int((last < 0).sum())

    # --- index and volatility -------------------------------------------
    import data as data_module

    for key, symbol in (("index", config.BENCHMARK), ("vix", VIX)):
        try:
            frame = data_module.fetch(symbol, quiet=True)
            snapshot[key] = float(frame["close"].iloc[-1])
            snapshot[f"{key}_change"] = float(
                frame["close"].pct_change(fill_method=None).iloc[-1])
        except Exception as error:                              # noqa: BLE001
            snapshot["errors"][key] = f"{type(error).__name__}: {error}"

    # --- derivatives, if a broker can supply them ------------------------
    snapshot["pcr"] = None
    snapshot["oi_buildup"] = None
    if broker is not None:
        for field, method in (("pcr", "pcr"), ("oi_buildup", "oi_buildup")):
            call = getattr(broker, method, None)
            if not callable(call):
                snapshot["errors"][field] = f"{broker.name} has no {method}()"
                continue
            try:
                snapshot[field] = call()
            except Exception as error:                          # noqa: BLE001
                snapshot["errors"][field] = f"{type(error).__name__}: {error}"

    return snapshot


def record(snapshot: dict) -> str:
    os.makedirs(os.path.dirname(PULSE_PATH), exist_ok=True)
    with open(PULSE_PATH, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(snapshot) + "\n")
    return PULSE_PATH


def history(limit: int | None = None) -> list[dict]:
    if not os.path.exists(PULSE_PATH):
        return []
    rows = []
    with open(PULSE_PATH, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows[-limit:] if limit else rows


def summary(snapshot: dict) -> str:
    def value(key, formatter="{:.4f}", default="-"):
        raw = snapshot.get(key)
        return formatter.format(raw) if isinstance(raw, (int, float)) else default

    lines = [
        f"as of {snapshot.get('as_of', '?')}  "
        f"({snapshot.get('symbols', 0)} symbols)",
        "",
        f"  dispersion  1d   {value('dispersion_1d', '{:.2%}')}"
        f"      21d  {value('dispersion_21d', '{:.2%}')}",
        f"  avg pairwise corr {value('avg_correlation', '{:.2f}')}"
        f"      top6 - mean  {value('top6_minus_mean', '{:+.2%}')}",
        "",
        f"  above 50-DMA     {value('pct_above_50dma', '{:.0%}')}"
        f"      adv/dec  {snapshot.get('advancers', '-')}/"
        f"{snapshot.get('decliners', '-')}",
        f"  NIFTY            {value('index', '{:,.0f}')}"
        f"      {value('index_change', '{:+.2%}')}",
        f"  India VIX        {value('vix', '{:.2f}')}"
        f"      {value('vix_change', '{:+.2%}')}",
    ]

    correlation = snapshot.get("avg_correlation")
    dispersion = snapshot.get("dispersion_1d")
    if isinstance(correlation, float) and isinstance(dispersion, float):
        lines.append("")
        if correlation > 0.7:
            lines.append("  Names are moving as one block. A ranking model has "
                         "little to separate\n  and the charge sheet does not "
                         "shrink to match.")
        elif dispersion > 0.02:
            lines.append("  Wide dispersion: the gap between the best names and "
                         "the average is\n  unusually large, which is the "
                         "condition this book needs.")
        else:
            lines.append("  Ordinary conditions.")

    if snapshot.get("pcr") is None:
        lines.append("\n  pcr / oi buildup: needs a derivatives feed "
                     "(no broker attached)")
    for field, message in snapshot.get("errors", {}).items():
        lines.append(f"  ! {field}: {message}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--no-record", action="store_true")
    args = parser.parse_args()

    if args.history:
        rows = history()
        if not rows:
            raise SystemExit(f"nothing recorded yet at {PULSE_PATH}")
        print(f"\n{len(rows)} snapshots\n")
        print(f"{'time':<21}{'disp 1d':>9}{'corr':>7}{'>50dma':>9}{'VIX':>8}")
        print("-" * 54)
        for row in rows[-25:]:
            print(f"{row.get('time', '')[:19]:<21}"
                  f"{row.get('dispersion_1d', float('nan')):>8.2%}"
                  f"{row.get('avg_correlation', float('nan')):>7.2f}"
                  f"{row.get('pct_above_50dma', float('nan')):>9.0%}"
                  f"{row.get('vix', float('nan')):>8.2f}")
        return

    snapshot = take()
    print()
    print(summary(snapshot))
    if not args.no_record:
        print(f"\nrecorded to {record(snapshot)}")
        print(f"{len(history())} snapshots so far - this is a series only once "
              f"it has months in it.")


if __name__ == "__main__":
    main()
