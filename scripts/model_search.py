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
import production as production_module
import signals as signals_module
import training as training_module

LEDGER = os.path.join(config.MODEL_DIR, "model_search.jsonl")

TRAIN_NAMES = 30
HELD_NAMES = 30
ENSEMBLE_MEMBERS = 5
ENSEMBLE_SLICE = 20


class Momentum:
    columns = ["mom_126"]

    def predict(self, frame):
        return frame["mom_126"].to_numpy(dtype=float)


class Reversal:
    columns = ["mom_21"]

    def predict(self, frame):
        return -frame["mom_21"].to_numpy(dtype=float)


class Noise:
    columns = []

    def __init__(self, seed):
        self.rng = np.random.default_rng(seed)

    def predict(self, frame):
        return self.rng.standard_normal(len(frame))


def _split_window(panel, start, end):
    window = panel[(panel["timestamp"] >= start) & (panel["timestamp"] <= end)]
    return production_module._split(window)


def _fit(signal_name, columns, panel, start, end, seed):
    train_panel, val_panel, _, _, _ = _split_window(panel, start, end)
    kwargs = {"seed": seed}
    if columns is not None:
        kwargs["columns"] = columns
    if signal_name == "nn":
        kwargs["epochs"] = 30
    signal = signals_module.build(signal_name, **kwargs)
    signal.fit(train_panel, val_panel)
    return signal


def _fit_ensemble(names, panel, start, end, seed):
    rng = random.Random(seed)
    members = []
    for index in range(ENSEMBLE_MEMBERS):
        slice_names = rng.sample(list(names), min(ENSEMBLE_SLICE, len(names)))
        subset = panel[panel["symbol"].isin(slice_names)]
        train_panel, val_panel, _, _, _ = _split_window(subset, start, end)
        signal = signals_module.build("lightgbm", seed=seed + index)
        signal.fit(train_panel, val_panel)
        members.append(signal)
    return production_module.SubsetEnsemble(members)


def configurations(names, panel, start, end, seed, want_nn, only_nn=False):
    if only_nn:
        return [
            ("nn-39", lambda: _fit("nn", None, panel, start, end, seed)),
            ("nn-core14",
             lambda: _fit("nn", features_module.CORE_FEATURE_COLUMNS,
                          panel, start, end, seed)),
        ]
    built = [
        ("momentum", lambda: Momentum()),
        ("reversal", lambda: Reversal()),
        ("random", lambda: Noise(seed)),
        ("lgbm-39", lambda: _fit("lightgbm", None, panel, start, end, seed)),
        ("lgbm-core14",
         lambda: _fit("lightgbm", features_module.CORE_FEATURE_COLUMNS,
                      panel, start, end, seed)),
        ("lgbm-ensemble",
         lambda: _fit_ensemble(names, panel, start, end, seed)),
    ]
    if want_nn:
        built += [
            ("nn-39", lambda: _fit("nn", None, panel, start, end, seed)),
            ("nn-core14",
             lambda: _fit("nn", features_module.CORE_FEATURE_COLUMNS,
                          panel, start, end, seed)),
        ]
    return built


def append(record):
    os.makedirs(config.MODEL_DIR, exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record) + "\n")


def one_draw(seed, want_nn, log, only_nn=False):
    rng = random.Random(seed)
    roster = list(config.UNIVERSE)
    chosen = sorted(rng.sample(roster, TRAIN_NAMES))
    rest = [name for name in roster if name not in set(chosen)]
    held = sorted(rng.sample(rest, min(HELD_NAMES, len(rest))))

    panel = features_module.cross_sectionalize(
        features_module.build_panel(chosen))
    dates = np.sort(panel["timestamp"].unique())
    window = training_module._window(dates, rng)
    if window is None:
        log(f"seed {seed}: no usable window, skipped")
        return 0
    start, end, end_index, _ = window
    held_panel = features_module.cross_sectionalize(
        features_module.build_panel(held))
    after = pd.Timestamp(dates[end_index + int(config.TARGET_HORIZON)])

    top_k = int(config.TOP_K)
    every = max(int(config.REBALANCE_EVERY), 1)
    log(f"seed {seed}: {start.date()} .. {end.date()}, scoring after "
        f"{after.date()}")

    written = 0
    for name, make in configurations(chosen, panel, start, end, seed,
                                     want_nn, only_nn):
        began = time.time()
        try:
            signal = make()
        except Exception as error:                              # noqa: BLE001
            log(f"  {name:<14} failed: {type(error).__name__}: {error}")
            continue
        seconds = time.time() - began
        unseen = training_module._score(signal, held_panel, after, top_k, every)
        same = training_module._score(signal, panel, after, top_k, every)
        if unseen is None or same is None:
            log(f"  {name:<14} nothing to score on")
            continue
        append({
            "seed": seed,
            "config": name,
            "window_start": str(start.date()),
            "window_end": str(end.date()),
            "fit_seconds": round(seconds, 2),
            "unseen_rank_ic": unseen["rank_ic"],
            "unseen_excess_annual_pct": unseen["excess_annual_pct"],
            "same_rank_ic": same["rank_ic"],
            "same_excess_annual_pct": same["excess_annual_pct"],
            "periods": unseen["periods"],
        })
        written += 1
        log(f"  {name:<14} unseen IC {unseen['rank_ic']:+.4f}  "
            f"excess {unseen['excess_annual_pct']:+7.2f}%/yr  "
            f"({seconds:5.1f}s)")
    return written


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--draws", type=int, default=40)
    parser.add_argument("--minutes", type=float, default=45.0)
    parser.add_argument("--no-nn", action="store_true")
    parser.add_argument("--nn-only", action="store_true")
    parser.add_argument("--first-seed", type=int, default=1000)
    args = parser.parse_args()

    deadline = time.time() + args.minutes * 60.0
    log = print
    total = 0
    for index in range(args.draws):
        if time.time() > deadline:
            log(f"budget spent after {index} draws")
            break
        total += one_draw(args.first_seed + index, not args.no_nn, log,
                          args.nn_only)
        log(f"-- {index + 1} draws done, {total} rows, "
            f"{(deadline - time.time()) / 60:.1f} min left")
    log(f"wrote {total} rows to {LEDGER}")


if __name__ == "__main__":
    main()
