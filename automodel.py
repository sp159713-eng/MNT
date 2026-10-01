import json
import os
import re
import subprocess
import sys

import config
import runs as runs_module

CANDIDATES = ("lightgbm", "nn", "xgboost", "blend:lightgbm+mom_252",
              "regime:blend:lightgbm+mom_252")
SEEDS = (7, 8, 9)
REPORT = os.path.join(config.MODEL_DIR, "automodel.json")
_RECORDED = re.compile(r"recorded as run (\d+)")


def _command(signal, seed):
    if getattr(sys, "frozen", False):
        head = [sys.executable, "--walkforward"]
    else:
        head = [sys.executable, "-u", "walkforward.py"]
    return head + ["--signal", signal, "--seed", str(seed), "--recent-first",
                   "--cut-after", "4", "--cut-below", "0"]


def _one(signal, seed, cwd, on_line):
    process = subprocess.Popen(
        _command(signal, seed), cwd=cwd, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, bufsize=1,
        env=dict(os.environ, PYTHONUNBUFFERED="1"),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    run_id = None
    for line in process.stdout:
        found = _RECORDED.search(line)
        if found:
            run_id = int(found.group(1))
        if on_line and "bad bar" not in line:
            on_line(line)
    process.wait()
    if run_id is None:
        return None
    record = next((r for r in runs_module.load() if r.get("id") == run_id), None)
    if not record:
        return None
    return {"run": run_id, "seed": seed,
            "bp": float(record["pooled"]["net_excess_bp"]),
            "t": float(record["pooled"]["t_stat"]),
            "cut": bool(record["settings"].get("cut"))}


def rank(candidates=CANDIDATES, seeds=SEEDS, cwd=None, on_line=None):
    cwd = cwd or os.path.dirname(os.path.abspath(__file__))
    rows = []
    for index, signal in enumerate(candidates, start=1):
        results = []
        for seed in seeds:
            if on_line:
                on_line(f"\n===== {signal} seed {seed}  "
                        f"({index} of {len(candidates)}) =====\n")
            result = _one(signal, seed, cwd, on_line)
            if result is None:
                continue
            results.append(result)
            if result["cut"]:
                break
        if not results:
            rows.append({"signal": signal, "bp": None, "worst": None,
                         "runs": [], "cut": False})
            continue
        values = [r["bp"] for r in results]
        rows.append({"signal": signal, "bp": sum(values) / len(values),
                     "worst": min(values), "runs": results,
                     "cut": any(r["cut"] for r in results)})
    rows.sort(key=lambda r: (r["bp"] is None or r["cut"],
                             -(r["bp"] or 0.0)))
    report = {"current": config.PRODUCTION_SIGNAL, "seeds": list(seeds),
              "rows": rows,
              "best": next((r["signal"] for r in rows
                            if r["bp"] is not None and not r["cut"]), None)}
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=1)
    return report


def last_report():
    try:
        with open(REPORT, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def summary(report):
    lines = [f"{'signal':34} {'avg bp':>7} {'worst':>6}  runs"]
    for row in report["rows"]:
        if row["bp"] is None:
            lines.append(f"{row['signal']:34} {'failed':>7}")
            continue
        note = "  cut early" if row["cut"] else ""
        lines.append(f"{row['signal']:34} {row['bp']:+7.0f} {row['worst']:+6.0f}"
                     f"  {len(row['runs'])}{note}")
    lines.append(f"best: {report['best']}   live: {report['current']}")
    return "\n".join(lines)


def main():
    picked = [a for a in sys.argv[1:] if not a.startswith("-")]
    report = rank(picked or CANDIDATES, on_line=lambda s: print(s, end=""))
    print("\n" + summary(report))


if __name__ == "__main__":
    main()
