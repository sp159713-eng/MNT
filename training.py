from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import time

import joblib
import numpy as np
import pandas as pd

import config
import features as features_module
import production as production_module

DIR = os.path.join(config.MODEL_DIR, "training")
LEDGER = os.path.join(config.MODEL_DIR, "training_runs.jsonl")

NAMES = 30
HELD_OUT_NAMES = 30
MIN_TRAIN_YEARS = 4
MAX_TRAIN_YEARS = 10
MIN_SCORE_SESSIONS = 252
MAX_SCORE_SESSIONS = 756


def runs() -> list:
    if not os.path.exists(LEDGER):
        return []
    out = []
    with open(LEDGER, encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def _next_id() -> int:
    existing = [record.get("id", 0) for record in runs()]
    return (max(existing) + 1) if existing else 1


def _append(record: dict) -> None:
    os.makedirs(config.MODEL_DIR, exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record) + "\n")


def _window(dates, rng):
    horizon = int(config.TARGET_HORIZON)
    score_sessions = rng.randint(MIN_SCORE_SESSIONS, MAX_SCORE_SESSIONS)
    end_index = len(dates) - 1 - horizon - score_sessions
    train_years = rng.randint(MIN_TRAIN_YEARS, MAX_TRAIN_YEARS)
    start_index = max(0, end_index - train_years * 252)
    if end_index - start_index < MIN_TRAIN_YEARS * 252:
        return None
    if end_index + horizon >= len(dates) - 1:
        return None
    return (pd.Timestamp(dates[start_index]), pd.Timestamp(dates[end_index]),
            end_index, score_sessions)


def _score(signal, panel, after, top_k, every):
    from scipy.stats import spearmanr

    tail = np.sort(panel.loc[panel["timestamp"] > after,
                             "timestamp"].unique())
    if not len(tail):
        return None

    ics, excesses = [], []
    for date in tail[::every]:
        frame = panel[panel["timestamp"] == date]
        if len(frame) < top_k * 2:
            continue
        predicted = np.asarray(signal.predict(frame), dtype=float)
        realised = frame["target_excess"].to_numpy(dtype=float)
        if np.isnan(predicted).any() or np.isnan(realised).any():
            continue
        correlation = spearmanr(predicted, realised).statistic
        if correlation == correlation:
            ics.append(float(correlation))
        order = np.argsort(-predicted)[:top_k]
        excesses.append(float(realised[order].mean()))

    if not excesses:
        return None
    per_period = float(np.mean(excesses))
    horizon = int(config.TARGET_HORIZON)
    return {
        "periods": len(excesses),
        "rank_ic": float(np.mean(ics)) if ics else float("nan"),
        "excess_per_period_pct": per_period * 100.0,
        "excess_annual_pct": per_period * (252.0 / horizon) * 100.0,
        "score_from": str(pd.Timestamp(tail[0]).date()),
        "score_to": str(pd.Timestamp(tail[-1]).date()),
    }


def run(names: int = NAMES, seed: int | None = None,
        signal_name: str | None = None, on_log=None) -> dict:
    log = on_log or (lambda text: None)
    signal_name = signal_name or config.PRODUCTION_SIGNAL
    seed = int(seed) if seed is not None else random.randrange(1, 1_000_000)
    rng = random.Random(seed)

    roster = list(config.UNIVERSE)
    wanted = max(2, int(names))
    reserve = min(HELD_OUT_NAMES, max(2, len(roster) // 6))
    count = max(2, min(wanted, len(roster) - reserve))
    chosen = sorted(rng.sample(roster, count))
    rest = [name for name in roster if name not in set(chosen)]
    held = sorted(rng.sample(rest, min(HELD_OUT_NAMES, len(rest)))) if rest else []
    if count < wanted:
        log(f"asked for {wanted} names, training on {count} so {len(rest)} "
            f"are left to score on - a run that trains on every name has "
            f"nothing unseen to prove itself against")

    log(f"seed {seed} | signal {signal_name} | "
        f"{count} of {len(roster)} names")
    log("train: " + ", ".join(chosen))
    log(f"score on {len(held)} other names: " + ", ".join(held))

    panel = features_module.cross_sectionalize(
        features_module.universe_panel(chosen))
    dates = np.sort(panel["timestamp"].unique())
    log(f"panel {len(panel):,} rows, {len(dates):,} sessions")

    chosen_window = _window(dates, rng)
    if chosen_window is None:
        raise SystemExit("not enough history for a training window on these "
                         "names - fetch more bars or lower MIN_TRAIN_YEARS")
    start, end, end_index, score_sessions = chosen_window
    log(f"window {start.date()} .. {end.date()}, "
        f"{score_sessions} sessions held out to score on")

    began = time.time()
    signal, _ = production_module.fit_window(
        chosen, signal_name, panel=panel, start=start, end=end, quiet=False)
    log(f"fitted in {time.time() - began:.1f}s")

    top_k = int(config.TOP_K)
    every = max(int(config.REBALANCE_EVERY), 1)
    after = pd.Timestamp(dates[end_index + int(config.TARGET_HORIZON)])

    same = _score(signal, panel, after, top_k, every)
    if same is None:
        raise SystemExit("nothing to score on after the window")

    unseen = None
    if held:
        held_panel = features_module.cross_sectionalize(
            features_module.universe_panel(held))
        unseen = _score(signal, held_panel, after, top_k, every)

    os.makedirs(DIR, exist_ok=True)
    run_id = _next_id()
    path = os.path.join(DIR, f"run_{run_id:04d}.joblib")
    joblib.dump({"signal": signal, "name": signal_name,
                 "features": list(getattr(signal, "columns",
                                          features_module.MODEL_COLUMNS)),
                 "horizon": int(config.TARGET_HORIZON)}, path)

    record = {
        "id": run_id,
        "at": time.strftime("%Y-%m-%d %H:%M"),
        "seed": seed,
        "signal": signal_name,
        "count": count,
        "names": chosen,
        "held_out": held,
        "window_start": str(start.date()),
        "window_end": str(end.date()),
        "path": path,
        "same_rank_ic": same["rank_ic"],
        "same_excess_annual_pct": same["excess_annual_pct"],
        "same_periods": same["periods"],
        "score_from": same["score_from"],
        "score_to": same["score_to"],
        "rank_ic": (unseen or same)["rank_ic"],
        "excess_annual_pct": (unseen or same)["excess_annual_pct"],
        "periods": (unseen or same)["periods"],
        "scored_on_unseen_names": unseen is not None,
    }
    _append(record)

    log(f"trained names : rank IC {same['rank_ic']:+.4f}  "
        f"top-{top_k} excess {same['excess_annual_pct']:+.2f}%/yr  "
        f"({same['periods']} periods)")
    if unseen:
        log(f"unseen names  : rank IC {unseen['rank_ic']:+.4f}  "
            f"top-{top_k} excess {unseen['excess_annual_pct']:+.2f}%/yr  "
            f"({unseen['periods']} periods)")
        log("the unseen-names row is the one that decides whether this run "
            "generalises")
    log(f"saved slot {run_id}")
    return record


def promote(run_id: int) -> str:
    record = next((r for r in runs() if r.get("id") == int(run_id)), None)
    if record is None:
        return f"no training run with id {run_id}"
    path = record.get("path", "")
    if not path or not os.path.exists(path):
        return f"the saved fit for run {run_id} is missing"

    bundle = joblib.load(path)
    backup = production_module.PATH + ".before-promote"
    had_previous = os.path.exists(production_module.PATH)
    if had_previous:
        shutil.copyfile(production_module.PATH, backup)

    production_module.save(bundle["signal"], bundle.get("name"))
    try:
        production_module.load()
    except SystemExit as error:
        if had_previous:
            shutil.copyfile(backup, production_module.PATH)
        elif os.path.exists(production_module.PATH):
            os.remove(production_module.PATH)
        return f"refused: {error}"
    return ""


def line(record: dict) -> str:
    return (f"{record.get('id', 0):>4}  {record.get('at', ''):<17}"
            f"{record.get('seed', 0):>7}  {record.get('count', 0):>3}n  "
            f"{record.get('window_start', ''):<12}"
            f"{record.get('window_end', ''):<12}"
            f"{record.get('rank_ic', float('nan')):>9.4f}"
            f"{record.get('excess_annual_pct', 0.0):>9.2f}%"
            f"{record.get('same_rank_ic', float('nan')):>11.4f}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train on a random slice of the universe over a random "
                    "window, score it on names it never saw, keep it as a slot.")
    parser.add_argument("--names", type=int, default=NAMES)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--signal", default=None,
                        choices=("nn", "lightgbm", "tabpfn"))
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--promote", type=int, default=None)
    args = parser.parse_args()

    if args.list:
        records = runs()
        if not records:
            print("no training runs yet")
            return
        print(f"{'id':>4}  {'when':<17}{'seed':>7}  {'names':>5}  "
              f"{'from':<12}{'to':<12}{'unseen IC':>9}{'excess':>10}"
              f"{'trained IC':>11}")
        print("-" * 88)
        for record in sorted(records, key=lambda r: r.get("id", 0)):
            print(line(record))
        return

    if args.promote is not None:
        problem = promote(args.promote)
        print(problem or f"promoted run {args.promote} to the production model")
        return

    run(args.names, args.seed, args.signal, on_log=print)


if __name__ == "__main__":
    main()
