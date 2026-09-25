import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import features as features_module
import production as production_module
import training as training_module

import joblib


def _cache_panels() -> None:
    build, sectionalize = features_module.universe_panel, features_module.cross_sectionalize
    built, done = {}, {}

    def universe_panel(names, *a, **k):
        key = (None if names is None else tuple(names), a, tuple(sorted(k.items())))
        if key not in built:
            if len(built) >= 4:
                built.clear()
                done.clear()
            built[key] = build(names, *a, **k)
        return built[key]

    def cross_sectionalize(frame, *a, **k):
        if not any(frame is kept for kept in built.values()):
            return sectionalize(frame, *a, **k)
        key = id(frame)
        if key not in done:
            done[key] = sectionalize(frame, *a, **k)
        return done[key]

    features_module.universe_panel = universe_panel
    features_module.cross_sectionalize = cross_sectionalize


def _run_pair(names, seed):
    began = time.time()
    lgb = training_module.run(names, seed, "lightgbm")
    lgb_seconds = time.time() - began

    began = time.time()
    xgb = training_module.run(names, seed, "xgboost")
    xgb_seconds = time.time() - began

    return {
        "seed": seed,
        "lightgbm": {"rank_ic": lgb["rank_ic"],
                     "excess_annual_pct": lgb["excess_annual_pct"],
                     "fit_seconds": lgb_seconds},
        "xgboost": {"rank_ic": xgb["rank_ic"],
                    "excess_annual_pct": xgb["excess_annual_pct"],
                    "fit_seconds": xgb_seconds},
    }


def _summarize(draws, key):
    diffs = [d["xgboost"][key] - d["lightgbm"][key] for d in draws]
    n = len(diffs)
    mean = sum(diffs) / n
    if n > 1:
        var = sum((x - mean) ** 2 for x in diffs) / (n - 1)
        sd = var ** 0.5
        t_stat = mean / (sd / (n ** 0.5)) if sd > 0 else float("inf")
    else:
        sd = 0.0
        t_stat = float("nan")
    wins = sum(1 for x in diffs if x > 0)
    return {"mean_diff": mean, "sd": sd, "t_stat": t_stat, "wins": wins,
            "total": n}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--draws", type=int, default=40)
    parser.add_argument("--names", type=int, default=120)
    parser.add_argument("--final-names", type=int, default=120)
    parser.add_argument("--seeds-from", type=int, default=1)
    parser.add_argument("--out-dir",
                        default=os.path.join(config.MODEL_DIR, "gpu360"))
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    _cache_panels()
    jsonl_path = os.path.join(args.out_dir, "gpu360_compare.jsonl")

    draws = []
    if os.path.exists(jsonl_path):
        with open(jsonl_path, encoding="utf-8") as handle:
            draws = [json.loads(line) for line in handle if line.strip()]
    done = {d["seed"] for d in draws}
    with open(jsonl_path, "a", encoding="utf-8", newline="\n") as handle:
        for i in range(args.draws):
            seed = args.seeds_from + i
            if seed in done:
                continue
            print(f"draw {i + 1}/{args.draws}  seed {seed}")
            record = _run_pair(args.names, seed)
            draws.append(record)
            handle.write(json.dumps(record) + "\n")
            handle.flush()
            print(f"  lightgbm  rank_ic {record['lightgbm']['rank_ic']:+.4f}"
                  f"  excess {record['lightgbm']['excess_annual_pct']:+.2f}%"
                  f"  ({record['lightgbm']['fit_seconds']:.1f}s)")
            print(f"  xgboost   rank_ic {record['xgboost']['rank_ic']:+.4f}"
                  f"  excess {record['xgboost']['excess_annual_pct']:+.2f}%"
                  f"  ({record['xgboost']['fit_seconds']:.1f}s)")

    ic = _summarize(draws, "rank_ic")
    excess = _summarize(draws, "excess_annual_pct")

    print()
    print(f"paired draws: {len(draws)}")
    print(f"rank IC   (xgb - lgbm): mean {ic['mean_diff']:+.4f}  "
          f"sd {ic['sd']:.4f}  t {ic['t_stat']:+.2f}  "
          f"wins {ic['wins']}/{ic['total']}")
    print(f"excess/yr (xgb - lgbm): mean {excess['mean_diff']:+.2f}%  "
          f"sd {excess['sd']:.2f}%  t {excess['t_stat']:+.2f}  "
          f"wins {excess['wins']}/{excess['total']}")

    print()
    label = f"XGB-{args.final_names}"
    final_names = production_module.liquid_names(args.final_names)
    print(f"fitting {label} on the {len(final_names)} most liquid names, full history...")
    began = time.time()
    signal, _ = production_module.fit_window(final_names, "xgboost", quiet=False)
    fit_seconds = time.time() - began
    print(f"fitted in {fit_seconds:.1f}s")

    out_path = os.path.join(args.out_dir, f"xgb{args.final_names}.joblib")
    joblib.dump({"signal": signal, "name": "xgboost",
                 "features": list(getattr(signal, "columns",
                                          features_module.MODEL_COLUMNS)),
                 "horizon": int(config.TARGET_HORIZON),
                 "names": final_names}, out_path)
    print(f"saved {out_path}")


if __name__ == "__main__":
    main()
