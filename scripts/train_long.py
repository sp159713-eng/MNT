from __future__ import annotations

import argparse
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import training as training_module


def stamp():
    return time.strftime("%H:%M:%S")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=float, default=180.0)
    parser.add_argument("--names", type=int, default=30)
    parser.add_argument("--signal", default="lightgbm")
    args = parser.parse_args()

    deadline = time.time() + args.minutes * 60.0
    started = time.time()
    done = failed = 0
    print(f"[{stamp()}] training {args.signal} on {args.names} random names "
          f"per run for {args.minutes:.0f} minutes", flush=True)

    while time.time() < deadline:
        began = time.time()
        try:
            record = training_module.run(args.names, signal_name=args.signal)
            done += 1
            print(f"[{stamp()}] slot {record['id']:>4} seed {record['seed']:>7} "
                  f"unseen IC {record['rank_ic']:+.4f} "
                  f"excess {record['excess_annual_pct']:+7.2f}%/yr "
                  f"({time.time() - began:.0f}s) "
                  f"[{done} done, {failed} failed, "
                  f"{(deadline - time.time()) / 60:.0f} min left]", flush=True)
        except KeyboardInterrupt:
            print(f"[{stamp()}] interrupted", flush=True)
            break
        except BaseException:                                   # noqa: BLE001
            failed += 1
            print(f"[{stamp()}] run failed:\n{traceback.format_exc()}",
                  flush=True)
            if failed > 20 and failed > done:
                print(f"[{stamp()}] too many failures, stopping", flush=True)
                break
            time.sleep(2)

    print(f"[{stamp()}] finished: {done} runs in "
          f"{(time.time() - started) / 60:.1f} minutes, {failed} failed",
          flush=True)


if __name__ == "__main__":
    main()
