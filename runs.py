"""An append-only log of every measured run, so a result outlives its terminal.

Two kinds live here. "walkforward" is the honest edge estimate over folds;
"sim" is one order-level replay from paper.py, including the GUI's Sim tab.
They are stored together and printed apart, because a replay is a single
equity path - it has no distribution over periods, so no t-statistic and no
break-even exist for it, and a row that borrowed those columns would read as
a failed significance test rather than as a different measurement.

WHY THIS EXISTS

walkforward.py prints a per-fold table and a pooled block and then throws both
away. That is fine while a number is being read once, and useless the moment
somebody asks "what did the run before the smoothing change say?" - the answer
is in a closed terminal. Fourteen folds of fits to reproduce a line of output is
not a reasonable price for a question a file could have answered.

APPEND-ONLY, DELIBERATELY

Nothing here is ever rewritten, pruned or overwritten. NNS learned this the
expensive way twice: its search checkpoint is deleted on successful completion,
which is why a 16,128-configuration grid survives only as its single winning
row, and its best_config.json holds one search behind a keep/overwrite guard,
which is how a stale record outlived several better runs. A log that edits
itself is a log that loses the run you wanted.

There is deliberately no "best run" file. Ranking runs is a real question, but
it cannot be answered from stored test statistics without selecting on test, so
this file stores and does not judge.

THE COST TRAVELS WITH THE NUMBER

Every record carries the round trip it was measured against. An excess in basis
points without its charge sheet is the one thing this repository's rules forbid
quoting, and a JSON file is easier to quote out of context than stdout, not
harder.
"""

from __future__ import annotations

import datetime
import json
import math
import os

import config

RUNS_PATH = os.path.join(config.MODEL_DIR, "runs.jsonl")


def _next_id() -> int:
    """One past the highest id on file; 1 when there is no file yet.

    Read rather than counted, so a hand-deleted line cannot make two runs share
    an id. A malformed tail costs the numbering nothing worse than a repeat,
    which is still better than refusing to record the run that just finished.
    """
    highest = 0
    try:
        with open(RUNS_PATH, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    highest = max(highest, int(json.loads(line).get("id", 0)))
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
    except OSError:
        return 1
    return highest + 1


def _signal_seed(args) -> int:
    override = getattr(args, "seed", None)
    if override is not None:
        return int(override)
    return config.GBM_SEED if args.signal == "lightgbm" else config.SEED


def settings(args) -> dict:
    """What the run was, including the parts that were never command-line flags.

    An argparse namespace alone does not describe a run: TOP_K, HOLD_BUFFER and
    the horizon come from config.py and change between runs without leaving a
    trace in the arguments. Recording only the flags produces a log in which two
    materially different runs look identical.
    """
    import walkforward

    return {
        # From the command line.
        "signal": args.signal,
        "start_year": args.start_year,
        "capital": args.capital,
        "every": args.every,
        "slippage_bp": args.slippage,
        "segment": args.segment,
        "epochs": args.epochs,
        "max_context": args.max_context,
        "fast": bool(args.fast),
        "feature_smooth": args.feature_smooth,
        "columns": getattr(args, "columns", "model"),
        # From config.py, where they are just as capable of moving the answer.
        # top_k and hold_buffer are the FALLBACK pair only: walk-forward picks
        # both per fold on that fold's validation year, so these two describe
        # what production trades, not what this run traded. Read folds[].chosen
        # for the ones that were actually used.
        "horizon": config.TARGET_HORIZON,
        "top_k": config.TOP_K,
        "hold_buffer": config.HOLD_BUFFER,
        "sizing_scheme": config.SIZING_SCHEME,
        "seed": config.SEED,
        "signal_seed": _signal_seed(args),
        "universe": len(config.UNIVERSE),
        "train_end": config.TRAIN_END,
        "val_end": config.VAL_END,
        "start": config.START,
        # The swept grids, because a fold's chosen configuration means nothing
        # without the set it was chosen from. GATES in particular is (0.0,)
        # today because gating was measured and lost.
        "sizes": list(walkforward.SIZES),
        "buffers": list(walkforward.BUFFERS),
        "windows": list(walkforward.FAST_WINDOWS if args.fast
                        else walkforward.WINDOWS),
        "gates": list(walkforward.GATES),
    }


def sim_settings(stats: dict, every: int, top_k: int, segment: str,
                 slippage_bp: float) -> dict:
    """What an order-level replay was, in the same spirit as settings().

    Deliberately does not import walkforward. Its SIZES/BUFFERS/WINDOWS/GATES
    describe a per-fold sweep that a replay never performs, and the GUI's Sim
    button should not drag that module in for four constants it cannot use.
    """
    return {
        "signal": "production",
        "sessions": stats.get("sessions"),
        "start_date": stats.get("start_date"),
        "end_date": stats.get("end_date"),
        "capital": stats.get("capital"),
        "every": every,
        "top_k": top_k,
        "segment": segment,
        "slippage_bp": slippage_bp,
        "horizon": config.TARGET_HORIZON,
        "sizing_scheme": config.SIZING_SCHEME,
        "seed": config.SEED,
        "universe": len(config.UNIVERSE),
    }


def record(kind: str, settings_dict: dict, folds: list, pooled: dict,
           baselines: dict, round_trip_bp: float, edge: dict | None = None) -> int:
    """Append one run and return its id. Never raises into the caller.

    A run that finished is worth more than a log guaranteed to be complete, so a
    failure to write says so and returns 0 rather than destroying the result the
    operator has been waiting for.
    """
    payload = {
        "id": _next_id(),
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
        "kind": kind,
        "settings": settings_dict,
        # Per fold: the year, the configuration validation picked, and what it
        # then earned. The chosen dict is the part that is otherwise
        # unrecoverable - it is where the selection story lives, and it is what
        # tells you later whether a fold's result came from the model or from a
        # buffer that happened to suit that year.
        "folds": folds,
        "pooled": pooled,
        "baselines": baselines,
        # The charge sheet the excess was measured against, stored beside it.
        "round_trip_bp": round_trip_bp,
        "edge": edge or {},
    }
    try:
        os.makedirs(config.MODEL_DIR, exist_ok=True)
        with open(RUNS_PATH, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")
    except OSError as error:
        print(f"  (could not write {RUNS_PATH}: {error})")
        return 0
    return payload["id"]


def load() -> list:
    """Every run on file, oldest first. Bad lines are skipped, not fatal."""
    runs = []
    try:
        with open(RUNS_PATH, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    runs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return runs


def _num(value: float, width: int, spec: str) -> str:
    if value is None or math.isnan(value):
        return format("-", ">" + str(width))
    return format(value, spec)


def run_edge_bp(run: dict) -> float:
    stored = run.get("edge") or {}
    if "net_excess_bp" in stored:
        return float(stored["net_excess_bp"])
    pooled = run.get("pooled") or {}
    usable = [v.get("net_excess_bp") for v in (run.get("baselines") or {}).values()
              if isinstance(v, dict) and v.get("net_excess_bp") is not None]
    if not usable or pooled.get("net_excess_bp") is None:
        return float("nan")
    return float(pooled["net_excess_bp"]) - max(usable)


def run_acc(run: dict) -> float:
    values = [f.get("acc") for f in (run.get("folds") or [])
              if isinstance(f, dict) and f.get("acc") is not None]
    if not values:
        return float("nan")
    return sum(float(v) for v in values) / len(values)


def summarise_line(run: dict) -> str:
    """One walk-forward run as one line, for `py -3.13 runs.py`."""
    settings_used = run.get("settings", {})
    pooled = run.get("pooled", {})
    nan = float("nan")
    # Deliberately no k or buffer here. They are in the record, but walk-forward
    # SWEEPS them per fold - this run held four names in thirteen folds of
    # fourteen while config.TOP_K said six - so printing the config value beside
    # the result would describe a book that was never traded. Per-fold "chosen"
    # is the honest place to read them.
    # Universe size belongs in the label, not just the record. Four runs in
    # this log read "lightgbm h20 equal 14f" and were 30, 37, 140 and 110
    # names; their excesses ranged +31 to +171bp. Without the count the table
    # shows four identical descriptions with wildly different results and no
    # visible reason, which is the opposite of what a run log is for.
    label = (f"{settings_used.get('signal', '?')} "
             f"h{settings_used.get('horizon', '?')} "
             f"{settings_used.get('universe', '?')}n "
             f"{len(run.get('folds', []))}f"
             f"{' fast' if settings_used.get('fast') else ''}")
    return (f"{run.get('id', 0):>3} "
            f"{run.get('at', '')[:16].replace('T', ' '):<16} "
            f"{label:<28}"
            f"{pooled.get('net_excess_bp', nan):>+9.0f}"
            f"{pooled.get('t_stat', nan):>+7.2f}"
            f"{pooled.get('periods', 0):>8}"
            f"{pooled.get('breakeven_bp', nan):>9.1f}"
            f"{run.get('round_trip_bp', nan):>9.1f}"
            f"{_num(run_edge_bp(run), 9, '>+9.0f')}"
            f"{_num(run_acc(run), 8, '>+8.3f')}")


def summarise_sim_line(run: dict) -> str:
    """One order-level replay as one line.

    Its own columns, not the walk-forward ones. The excess is annualised
    percent against buy-and-hold over a single window rather than an excess per
    period, and the charges column is what the replay was actually billed, so
    the modelled round trip beside it is there to be compared against, not to
    be mistaken for the same quantity.
    """
    settings_used = run.get("settings", {})
    stats = run.get("pooled", {})
    nan = float("nan")
    label = (f"{stats.get('sessions', 0)}s "
             f"k{settings_used.get('top_k', '?')} "
             f"every {settings_used.get('every', '?')} "
             f"{settings_used.get('sizing_scheme', '?')}")
    return (f"{run.get('id', 0):>3} "
            f"{run.get('at', '')[:16].replace('T', ' '):<16} "
            f"{label:<28}"
            f"{stats.get('final_equity', nan):>12,.0f}"
            f"{stats.get('excess_annual_pct', nan):>+9.1f}"
            f"{stats.get('max_drawdown_pct', nan):>8.1f}"
            f"{stats.get('trades', 0):>7}"
            f"{stats.get('charges_paid', nan):>10,.0f}"
            f"{run.get('round_trip_bp', nan):>9.1f}")


def main() -> None:
    runs = load()
    if not runs:
        print(f"No runs recorded yet ({RUNS_PATH} is absent or empty).")
        return

    folds = [r for r in runs if r.get("kind") != "sim"]
    sims = [r for r in runs if r.get("kind") == "sim"]
    print()
    print(f"{len(runs)} runs in {RUNS_PATH}")

    if folds:
        print()
        print(f"walk-forward ({len(folds)})")
        print()
        print(f"{'id':>3} {'when':<16} {'what':<28}{'excess':>9}{'t':>7}"
              f"{'periods':>8}{'b/e bp':>9}{'cost bp':>9}{'edge bp':>9}{'acc':>8}")
        print("-" * 110)
        for run in folds:
            print(summarise_line(run))
        print()
        print("A break-even below the cost column is a run that lost to its "
              "own charge sheet.")

    if sims:
        print()
        print(f"order-level replays ({len(sims)})")
        print()
        print(f"{'id':>3} {'when':<16} {'what':<28}{'closing':>12}"
              f"{'vs b&h':>9}{'max dd':>8}{'fills':>7}{'charges':>10}"
              f"{'cost bp':>9}")
        print("-" * 102)
        for run in sims:
            print(summarise_sim_line(run))
        print()
        print("vs b&h is annualised percent over that one window against "
              "holding all thirty.")
        print("One equity path carries no t-statistic, so a replay cannot be "
              "read as an edge.")

    print()


if __name__ == "__main__":
    main()
