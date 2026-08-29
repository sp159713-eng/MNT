from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import features as features_module
import signals as signals_module
import production as production_module
import training as training_module

LEDGER = os.path.join(config.MODEL_DIR, "size_sweep.jsonl")
HELD_NAMES = 30
SIZES = (30, 60, 120)


def append(record):
    os.makedirs(config.MODEL_DIR, exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record) + "\n")


def fit_on(names, raw, start, end, seed):
    subset = raw[raw["symbol"].isin(set(names))]
    panel = features_module.cross_sectionalize(subset)
    window = panel[(panel["timestamp"] >= start) & (panel["timestamp"] <= end)]
    train_panel, val_panel, _, _, _ = production_module._split(window)
    signal = signals_module.build("lightgbm", seed=seed)
    signal.fit(train_panel, val_panel)
    return signal


def one_draw(seed, sizes, log):
    rng = random.Random(seed)
    roster = list(config.UNIVERSE)
    biggest = max(sizes)
    if len(roster) < biggest + HELD_NAMES:
        log(f"universe too small: need {biggest + HELD_NAMES}, "
            f"have {len(roster)}")
        return 0

    pool = rng.sample(roster, biggest + HELD_NAMES)
    held = sorted(pool[:HELD_NAMES])
    train_pool = pool[HELD_NAMES:]

    raw = features_module.build_panel(train_pool)
    dates = np.sort(features_module.cross_sectionalize(raw)["timestamp"]
                    .unique())
    window = training_module._window(dates, rng)
    if window is None:
        log(f"seed {seed}: no usable window")
        return 0
    start, end, end_index, _ = window

    held_panel = features_module.cross_sectionalize(
        features_module.build_panel(held))
    after = pd.Timestamp(dates[end_index + int(config.TARGET_HORIZON)])
    top_k = int(config.TOP_K)
    every = max(int(config.REBALANCE_EVERY), 1)

    log(f"seed {seed}: {start.date()} .. {end.date()}, scoring after "
        f"{after.date()} on {len(held)} unseen names")

    written = 0
    for size in sizes:
        names = train_pool[:size]
        began = time.time()
        try:
            signal = fit_on(names, raw, start, end, seed)
        except Exception as error:                              # noqa: BLE001
            log(f"  {size:>4} names failed: {type(error).__name__}: {error}")
            continue
        seconds = time.time() - began
        scored = training_module._score(signal, held_panel, after, top_k,
                                        every)
        if scored is None:
            log(f"  {size:>4} names: nothing to score on")
            continue
        append({
            "seed": seed,
            "train_names": size,
            "held_names": len(held),
            "window_start": str(start.date()),
            "window_end": str(end.date()),
            "fit_seconds": round(seconds, 2),
            "unseen_rank_ic": scored["rank_ic"],
            "unseen_excess_annual_pct": scored["excess_annual_pct"],
            "periods": scored["periods"],
        })
        written += 1
        log(f"  {size:>4} names: unseen IC {scored['rank_ic']:+.4f}  "
            f"excess {scored['excess_annual_pct']:+7.2f}%/yr  "
            f"({seconds:5.1f}s)")
    return written


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--draws", type=int, default=30)
    parser.add_argument("--minutes", type=float, default=60.0)
    parser.add_argument("--first-seed", type=int, default=5000)
    parser.add_argument("--sizes", default=",".join(str(s) for s in SIZES))
    args = parser.parse_args()

    sizes = tuple(int(s) for s in args.sizes.split(",") if s.strip())
    deadline = time.time() + args.minutes * 60.0
    total = 0
    for index in range(args.draws):
        if time.time() > deadline:
            print(f"budget spent after {index} draws", flush=True)
            break
        total += one_draw(args.first_seed + index, sizes, print)
        print(f"-- {index + 1} draws, {total} rows, "
              f"{(deadline - time.time()) / 60:.0f} min left", flush=True)
    print(f"wrote {total} rows to {LEDGER}", flush=True)


if __name__ == "__main__":
    main()
