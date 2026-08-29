from __future__ import annotations

import argparse
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import training as training_module


def line(record, rank):
    return (f"{rank:>3}  slot {record['id']:>4}  seed {record['seed']:>7}  "
            f"IC {record['rank_ic']:+.4f}  "
            f"excess {record['excess_annual_pct']:+8.2f}%/yr  "
            f"{record['window_start']} .. {record['window_end']}  "
            f"{record['periods']:>3}p")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--by", default="rank_ic",
                        choices=("rank_ic", "excess_annual_pct"))
    args = parser.parse_args()

    rows = [r for r in training_module.runs()
            if r.get("scored_on_unseen_names", True) and r.get("held_out", ["?"])]
    if not rows:
        print("no scored runs")
        return

    ics = [r["rank_ic"] for r in rows]
    exs = [r["excess_annual_pct"] for r in rows]
    mean_ic, sd_ic = st.mean(ics), (st.stdev(ics) if len(ics) > 1 else 0.0)
    mean_ex, sd_ex = st.mean(exs), (st.stdev(exs) if len(exs) > 1 else 0.0)

    print(f"{len(rows)} scored runs")
    print(f"unseen rank IC : mean {mean_ic:+.4f}  sd {sd_ic:.4f}  "
          f"positive {sum(1 for v in ics if v > 0)}/{len(ics)}")
    print(f"unseen excess  : mean {mean_ex:+.2f}%/yr  sd {sd_ex:.2f}  "
          f"positive {sum(1 for v in exs if v > 0)}/{len(exs)}\n")

    ranked = sorted(rows, key=lambda r: r[args.by], reverse=True)
    print(f"TOP {args.top} by {args.by}, scored on names the fit never saw")
    for index, record in enumerate(ranked[:args.top], start=1):
        print(line(record, index))

    best = ranked[0]
    z_ic = ((best["rank_ic"] - mean_ic) / sd_ic) if sd_ic else 0.0
    z_ex = ((best["excess_annual_pct"] - mean_ex) / sd_ex) if sd_ex else 0.0
    print(f"\nbest is slot {best['id']}: {z_ic:+.1f} sd above mean on IC, "
          f"{z_ex:+.1f} sd on excess, out of {len(rows)} draws")
    print(f"promote with: py -3.13 training.py --promote {best['id']}")


if __name__ == "__main__":
    main()
