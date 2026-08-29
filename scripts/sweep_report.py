from __future__ import annotations

import json
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

LEDGER = os.path.join(config.MODEL_DIR, "size_sweep.jsonl")


def load():
    if not os.path.exists(LEDGER):
        return []
    rows = []
    with open(LEDGER, encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    return rows


def stats(values):
    n = len(values)
    if not n:
        return 0.0, 0.0, 0.0
    mean = st.mean(values)
    if n < 2:
        return mean, 0.0, 0.0
    sd = st.stdev(values)
    return mean, sd, (mean / (sd / (n ** 0.5)) if sd else 0.0)


def main():
    rows = load()
    if not rows:
        print("no sweep rows yet")
        return

    by_seed = {}
    for row in rows:
        by_seed.setdefault(row["seed"], {})[row["train_names"]] = row
    sizes = sorted({row["train_names"] for row in rows})

    print(f"{len(rows)} rows | {len(by_seed)} draws | sizes {sizes}\n")
    print("ABSOLUTE, scored on 30 names the fit never saw")
    print(f"{'train names':>12}{'n':>5}{'rank IC':>10}{'t':>7}"
          f"{'excess %/yr':>13}{'t':>7}{'win':>8}{'fit s':>8}")
    print("-" * 70)
    for size in sizes:
        got = [by_seed[s][size] for s in by_seed if size in by_seed[s]]
        ic = [g["unseen_rank_ic"] for g in got]
        ex = [g["unseen_excess_annual_pct"] for g in got]
        secs = [g["fit_seconds"] for g in got]
        ic_m, _, ic_t = stats(ic)
        ex_m, _, ex_t = stats(ex)
        wins = sum(1 for v in ex if v > 0)
        print(f"{size:>12}{len(got):>5}{ic_m:>+10.4f}{ic_t:>+7.2f}"
              f"{ex_m:>+13.2f}{ex_t:>+7.2f}{wins:>5}/{len(got):<2}"
              f"{st.mean(secs):>8.1f}")

    base = sizes[0]
    print(f"\nPAIRED against {base} training names, same draw")
    print(f"{'train names':>12}{'n':>5}{'d rank IC':>11}{'t':>7}"
          f"{'d excess':>11}{'t':>7}{'better':>9}")
    print("-" * 63)
    for size in sizes[1:]:
        pair = [s for s in by_seed if size in by_seed[s] and base in by_seed[s]]
        dic = [by_seed[s][size]["unseen_rank_ic"]
               - by_seed[s][base]["unseen_rank_ic"] for s in pair]
        dex = [by_seed[s][size]["unseen_excess_annual_pct"]
               - by_seed[s][base]["unseen_excess_annual_pct"] for s in pair]
        ic_m, _, ic_t = stats(dic)
        ex_m, _, ex_t = stats(dex)
        better = sum(1 for v in dex if v > 0)
        print(f"{size:>12}{len(pair):>5}{ic_m:>+11.4f}{ic_t:>+7.2f}"
              f"{ex_m:>+11.2f}{ex_t:>+7.2f}{better:>6}/{len(pair):<2}")

    print("\nDraws share one roster and overlapping windows, so t is "
          "optimistic.")


if __name__ == "__main__":
    main()
