from __future__ import annotations

import json
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

LEDGER = os.path.join(config.MODEL_DIR, "model_search.jsonl")
REFERENCE = "lgbm-39"


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


def summarise(values):
    n = len(values)
    if not n:
        return 0.0, 0.0, 0.0
    if n < 2:
        return values[0], 0.0, 0.0
    mean = st.mean(values)
    sd = st.stdev(values)
    t = mean / (sd / (n ** 0.5)) if sd else 0.0
    return mean, sd, t


def main():
    rows = load()
    if not rows:
        print("no rows yet")
        return

    by_seed = {}
    for row in rows:
        by_seed.setdefault(row["seed"], {})[row["config"]] = row
    names = sorted({row["config"] for row in rows})
    complete = [seed for seed, got in by_seed.items()
                if all(name in got for name in names)]
    complete.sort()

    print(f"{len(rows)} rows | {len(by_seed)} seeds | "
          f"{len(complete)} complete across all {len(names)} configs")
    print(f"configs: {', '.join(names)}\n")

    print("ABSOLUTE, on names the fit never saw (all seeds where present)")
    print(f"{'config':<16}{'n':>4}{'rank IC':>10}{'t':>7}"
          f"{'excess %/yr':>13}{'t':>7}{'win':>7}{'fit s':>8}")
    print("-" * 72)
    for name in names:
        got = [by_seed[s][name] for s in by_seed if name in by_seed[s]]
        ic = [g["unseen_rank_ic"] for g in got]
        ex = [g["unseen_excess_annual_pct"] for g in got]
        secs = [g["fit_seconds"] for g in got]
        ic_m, _, ic_t = summarise(ic)
        ex_m, _, ex_t = summarise(ex)
        wins = sum(1 for v in ex if v > 0)
        print(f"{name:<16}{len(got):>4}{ic_m:>+10.4f}{ic_t:>+7.2f}"
              f"{ex_m:>+13.2f}{ex_t:>+7.2f}"
              f"{wins:>4}/{len(got):<2}{st.mean(secs):>8.1f}")

    if len(by_seed) < 2 or REFERENCE not in names:
        print("\nnot enough complete paired draws yet")
        return

    for reference in (REFERENCE, "random"):
        if reference not in names:
            continue
        print(f"\nPAIRED against {reference}, same names, window and seed "
              f"- paired draw count shown per row")
        print(f"{'config':<16}{'d rank IC':>11}{'sd':>8}{'t':>7}"
              f"{'d excess':>11}{'sd':>8}{'t':>7}{'better':>9}")
        print("-" * 78)
        for name in names:
            if name == reference:
                continue
            pair = sorted(seed for seed, got in by_seed.items()
                          if name in got and reference in got)
            dic, dex = [], []
            for seed in pair:
                dic.append(by_seed[seed][name]["unseen_rank_ic"]
                           - by_seed[seed][reference]["unseen_rank_ic"])
                dex.append(by_seed[seed][name]["unseen_excess_annual_pct"]
                           - by_seed[seed][reference]["unseen_excess_annual_pct"])
            ic_m, ic_sd, ic_t = summarise(dic)
            ex_m, ex_sd, ex_t = summarise(dex)
            better = sum(1 for v in dex if v > 0)
            print(f"{name:<16}{ic_m:>+11.4f}{ic_sd:>8.4f}{ic_t:>+7.2f}"
                  f"{ex_m:>+11.2f}{ex_sd:>8.2f}{ex_t:>+7.2f}"
                  f"{better:>6}/{len(pair):<2}")

    print("\nGENERALISATION, trained names minus unseen names, per config")
    print(f"{'config':<16}{'d rank IC':>11}{'t':>7}")
    print("-" * 34)
    for name in names:
        gap = [by_seed[s][name]["same_rank_ic"] - by_seed[s][name]["unseen_rank_ic"]
               for s in by_seed if name in by_seed[s]]
        gap_m, _, gap_t = summarise(gap)
        print(f"{name:<16}{gap_m:>+11.4f}{gap_t:>+7.2f}")

    print("\nt is against zero and the draws share one roster and overlapping "
          "windows, so treat it as optimistic.")


if __name__ == "__main__":
    main()
