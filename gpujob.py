from __future__ import annotations

import ast
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import time

import config

ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_WORKSPACE = "D:/mnt-kaggle"
KAGGLE_HINT = ("Needs the kaggle CLI (pip install kaggle) and a login: run "
               "'kaggle auth login' or put kaggle.json in ~/.kaggle.")
STATES_LIVE = ("preparing", "pushed", "running")
DELIVERY = "deliv_21,deliv_surprise"
REFERENCE = "blend:lightgbm+mom_252"
KERNEL_CAP_H = 12.0
PARALLEL = 5
US_TOP_MAX = 2000
US_TOP_DEFAULT = 1000


def dry_run() -> bool:
    return os.environ.get("MNT_GPU_DRY_RUN") == "1"


def find_kaggle() -> str | None:
    import shutil as shutil_module

    found = shutil_module.which("kaggle")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA", "")
    pattern = os.path.join(local, "Packages", "PythonSoftwareFoundation.Python.*",
                           "LocalCache", "local-packages", "Python*", "Scripts",
                           "kaggle.exe")
    hits = sorted(glob.glob(pattern))
    return hits[-1] if hits else None


def username() -> str | None:
    if os.environ.get("KAGGLE_USERNAME"):
        return os.environ["KAGGLE_USERNAME"]
    home = os.path.join(os.path.expanduser("~"), ".kaggle")
    for name in ("kaggle.json", "credentials.json"):
        try:
            with open(os.path.join(home, name), encoding="utf-8") as handle:
                value = json.load(handle).get("username")
        except Exception:
            continue
        if value:
            return str(value)
    return None


def kaggle_ready() -> tuple[bool, str]:
    if find_kaggle() is None:
        return False, "kaggle CLI not found. " + KAGGLE_HINT
    if username() is None:
        return False, "no Kaggle login found. " + KAGGLE_HINT
    return True, ""


def kaggle(args: list, timeout: int = 900) -> tuple[int, str]:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    cwd = None
    args = list(args)
    if "-p" in args:
        idx = args.index("-p")
        if idx + 1 < len(args) and os.path.isabs(args[idx + 1]):
            cwd = args[idx + 1]
            args[idx + 1] = "."
    try:
        done = subprocess.run([find_kaggle(), *args], capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, env=env, cwd=cwd)
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    return done.returncode, (done.stdout + done.stderr).strip()


def clamp_hours(hours: float) -> tuple[float, float, float]:
    hours = min(max(float(hours), 1.0), KERNEL_CAP_H - 0.5)
    return hours, max(0.5, hours - 1.0), hours - 0.5


def clamp_top(top) -> int:
    try:
        top = int(top)
    except (TypeError, ValueError):
        return US_TOP_DEFAULT
    return US_TOP_DEFAULT if top < 1 else min(top, US_TOP_MAX)


def make_groups(presets, us: bool, top: int, delivery: bool, reference: bool):
    top = clamp_top(top)
    extra = []
    if delivery:
        extra += ["--add", DELIVERY]
    if us:
        extra += ["--extra-train", "data_cache/us/_index.json",
                  "--extra-top-liquid", str(int(top))]
    groups = []
    if reference:
        groups.append(("ref_blend", REFERENCE, None, list(extra)))
    for name, values in presets:
        groups.append((name, "nn", nn_dict(values), list(extra)))
    return groups


def estimate(groups, seeds: int, us: bool) -> tuple[int, float]:
    nn_runs = sum(1 for g in groups if g[2]) * seeds
    ref_runs = sum(1 for g in groups if not g[2]) * seeds
    wall = nn_runs * (0.27 if us else 0.2) + ref_runs * 0.05
    return nn_runs + ref_runs, wall


def nn_dict(settings: dict) -> dict:
    return {k: settings[k] for k in config.NN_DEFAULTS if k in settings}


TEMPLATE = r'''import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile

OUT = "/kaggle/working"
WORK = os.path.join(OUT, "mnt")
UNIVERSE = ["--universe", "data_cache/nse/adj/_index.json", "--top-liquid", "500"]
SEEDS = __SEEDS__
GROUPS = __GROUPS__
NN_BASE = __NN_BASE__
PREBUILD_US = __PREBUILD__
LAUNCH_UNTIL_H = __LAUNCH__
KILL_AT_H = __KILL__
PARALLEL = __PARALLEL__
RESULTS = "__RESULTS__"
started = time.time()


def stamp(text):
    print(f"[{(time.time() - started) / 60:6.1f} min] {text}", flush=True)


src_matches = glob.glob("/kaggle/input/**/walkforward.py", recursive=True)
if not src_matches:
    message = f"walkforward.py not found in /kaggle/input. Inputs: {glob.glob('/kaggle/input/**')}"
    print(f"[ERROR] {message}", flush=True)
    with open(os.path.join(OUT, "error.txt"), "w") as handle:
        handle.write(message + "\n")
    sys.exit(1)
src = os.path.dirname(src_matches[0])
os.makedirs(WORK, exist_ok=True)
for path in glob.glob(os.path.join(src, "*.py")):
    shutil.copy(path, WORK)
for pattern in ("/kaggle/input/**/data.pkg", "/kaggle/input/**/nse.pkg", "/kaggle/input/**/us.pkg"):
    found = glob.glob(pattern, recursive=True)
    if found:
        zipfile.ZipFile(found[0]).extractall(WORK)

building_top = None
try:
    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    stamp(f"device {device}; nse csvs {len(glob.glob(os.path.join(WORK, 'data_cache/nse/adj/*.csv')))}")
    env = dict(os.environ, MNT_DATA_SOURCE="nse", MNT_TORCH_DEVICE=device,
               PYTHONUNBUFFERED="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
    subprocess.run([sys.executable, "-c",
                    "import json, features; s=json.load(open('data_cache/nse/adj/_index.json'))['symbols'];"
                    "p=features.cached_panel(s, top_liquid=500); print(len(p), p['symbol'].nunique())"],
                   cwd=WORK, check=True, env=env)
    for top in PREBUILD_US:
        building_top = top
        subprocess.run([sys.executable, "-c",
                        "import json, features; s=json.load(open('data_cache/nse/adj/_index.json'))['symbols'];"
                        "u=json.load(open('data_cache/us/_index.json'))['symbols'];"
                        f"p=features.cached_panel(s, top_liquid=500, extra_symbols=u, extra_top_liquid={top}); print(len(p))"],
                       cwd=WORK, check=True, env=env)
    stamp("panel built")

    queue = [(tag, signal, nn, extra, seed) for seed in SEEDS for tag, signal, nn, extra in GROUPS]
    running, results = [], []
    _BP = re.compile(r"^(?:nn|blend\S*)\s+([+-]\d+)", re.M)
    _FIT = re.compile(r"fit: train IC ([+-][\d.]+)\s+val IC ([+-][\d.]+)")
    while queue or running:
        hours = (time.time() - started) / 3600
        if hours > LAUNCH_UNTIL_H and queue:
            stamp(f"time guard: dropping {len(queue)} queued")
            queue = []
        if hours > KILL_AT_H:
            for item in running:
                item[4].kill()
        while queue and len(running) < PARALLEL:
            tag, signal, nn, extra, seed = queue.pop(0)
            name = os.path.join(OUT, f"{tag}_s{seed}.log")
            log = open(name, "w")
            command = [sys.executable, "-u", "walkforward.py", "--signal", signal,
                       "--seed", str(seed)] + UNIVERSE + extra
            if nn:
                command += ["--nn-budget", "240"]
            job_env = dict(env, MNT_NN_JSON=json.dumps({**NN_BASE, **nn})) if nn else env
            running.append((tag, seed, name, log, subprocess.Popen(
                command, cwd=WORK, stdout=log, stderr=subprocess.STDOUT, env=job_env)))
        time.sleep(30)
        for item in list(running):
            tag, seed, name, log, process = item
            if process.poll() is None:
                continue
            log.close()
            running.remove(item)
            text = open(name).read()
            bp = _BP.findall(text)
            fits = [(float(a), float(b)) for a, b in _FIT.findall(text)]
            result = {"tag": tag, "seed": seed, "exit": process.returncode,
                      "bp": int(bp[-1]) if bp else None,
                      "train_ic": sum(a for a, _ in fits) / len(fits) if fits else None,
                      "val_ic": sum(b for _, b in fits) / len(fits) if fits else None}
            results.append(result)
            stamp(json.dumps(result))
            with open(os.path.join(OUT, RESULTS), "w") as handle:
                json.dump(results, handle, indent=1)

    for tag, *_ in GROUPS:
        rows = [r for r in results if r["tag"] == tag and r["bp"] is not None]
        if rows:
            bps = [r["bp"] for r in rows]
            gap = [r["train_ic"] - r["val_ic"] for r in rows if r["train_ic"] is not None]
            stamp(f"SUMMARY {tag}: bp {bps} avg {sum(bps) / len(bps):+.0f} worst {min(bps):+d}"
                  + (f" | train-val IC gap {sum(gap) / len(gap):+.4f}" if gap else ""))
    shutil.rmtree(WORK, ignore_errors=True)
    stamp("done")
except BaseException as exc:
    import traceback
    tail = "".join(traceback.format_exc().splitlines(True)[-12:])
    if isinstance(exc, subprocess.CalledProcessError) and exc.returncode == -9:
        label = f"US top {building_top}" if building_top else "NSE panel"
        message = f"out of memory building the panel ({label}) - lower 'US top' (<= 2000 works)"
    else:
        message = f"{type(exc).__name__}: {exc}"
    with open(os.path.join(OUT, "error.txt"), "w") as handle:
        handle.write(message + "\n\n" + tail)
    stamp("FAILED: " + message)
    if not isinstance(exc, Exception):
        raise
finally:
    shutil.rmtree(WORK, ignore_errors=True)
'''


def render(groups, seeds, settings, us_tops, hours) -> str:
    _, launch, kill = clamp_hours(hours)
    script = TEMPLATE
    for token, value in (("__SEEDS__", repr(tuple(seeds))),
                         ("__GROUPS__", repr(groups)),
                         ("__NN_BASE__", repr(nn_dict(settings))),
                         ("__PREBUILD__", repr(list(us_tops))),
                         ("__LAUNCH__", repr(launch)),
                         ("__KILL__", repr(kill)),
                         ("__PARALLEL__", str(PARALLEL)),
                         ("__RESULTS__", "results.json")):
        script = script.replace(token, value)
    ast.parse(script)
    return script


def local_modules() -> list:
    seen, todo = set(), ["walkforward"]
    while todo:
        name = todo.pop()
        path = os.path.join(ROOT, name + ".py")
        if name in seen or not os.path.exists(path):
            continue
        seen.add(name)
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                todo += [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                todo.append(node.module.split(".")[0])
    return sorted(seen)


def sync_src(workspace: str) -> tuple[str, str]:
    folder = os.path.join(workspace, "src")
    os.makedirs(folder, exist_ok=True)
    digest = hashlib.sha256()
    for name in local_modules():
        source = os.path.join(ROOT, name + ".py")
        shutil.copy(source, os.path.join(folder, name + ".py"))
        with open(source, "rb") as handle:
            digest.update(name.encode() + handle.read())
    return folder, digest.hexdigest()


def metadata(user: str, slug: str, us: bool) -> dict:
    sources = [f"{user}/mnt-src", f"{user}/mnt-nse"] + ([f"{user}/mnt-us"] if us else [])
    return {"id": f"{user}/{slug}", "title": slug, "code_file": "run.py",
            "language": "python", "kernel_type": "script", "is_private": True,
            "enable_gpu": True, "enable_internet": False,
            "dataset_sources": sources, "competition_sources": [],
            "kernel_sources": []}


def build_job(workspace: str, params: dict, settings: dict, user: str) -> dict:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    slug = f"mnt-gui-{stamp}"
    folder = os.path.join(workspace, "gui_jobs", stamp).replace("\\", "/")
    os.makedirs(folder, exist_ok=True)
    groups = make_groups(params["presets"], params["us"], params["top"],
                         params["delivery"], params["reference"])
    seeds = list(range(7, 7 + int(params["seeds"])))
    tops = [clamp_top(params["top"])] if params["us"] else []
    script = render(groups, seeds, settings, tops, params["hours"])
    with open(os.path.join(folder, "run.py"), "w", encoding="utf-8",
              newline="\n") as handle:
        handle.write(script)
    with open(os.path.join(folder, "kernel-metadata.json"), "w",
              encoding="utf-8") as handle:
        json.dump(metadata(user, slug, params["us"]), handle, indent=1)
    return {"slug": slug, "dir": folder, "groups": groups, "seeds": seeds}


def load_results(folder: str) -> list:
    for path in sorted(glob.glob(os.path.join(folder, "**", "*.json"),
                                 recursive=True)):
        try:
            with open(path, encoding="utf-8") as handle:
                rows = json.load(handle)
        except Exception:
            continue
        if isinstance(rows, list) and rows and all(
                isinstance(r, dict) and "tag" in r and "seed" in r
                for r in rows):
            return rows
    return []


def load_error_log(folder: str) -> str | None:
    for path in sorted(glob.glob(os.path.join(folder, "**", "error.txt"),
                                 recursive=True)):
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                first = handle.readline().strip()
        except Exception:
            continue
        if first:
            return first[:300]
    patterns = [
        os.path.join(folder, "**", "*.log"),
        os.path.join(folder, "**", "*.txt"),
        os.path.join(folder, "*.log"),
        os.path.join(folder, "*.txt"),
    ]
    files = []
    for p in patterns:
        files.extend(glob.glob(p, recursive=True))
    for path in sorted(set(files)):
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                lines = [line.strip() for line in handle if line.strip()]
            if not lines:
                continue
            for i, line in enumerate(lines):
                if any(k in line for k in ("Traceback", "Error:", "Exception:", "FAILED", "error:")):
                    return " ".join(lines[i:i + 3])[:200]
            return lines[-1][:200]
        except Exception:
            pass
    return None


def summarise(rows: list) -> list:
    out = []
    for tag in dict.fromkeys(r["tag"] for r in rows):
        mine = [r for r in rows if r["tag"] == tag]
        good = [r for r in mine if r.get("bp") is not None]
        bps = [r["bp"] for r in good]
        gaps = [r["train_ic"] - r["val_ic"] for r in good
                if r.get("train_ic") is not None and r.get("val_ic") is not None]
        out.append({"tag": tag, "bps": bps, "failed": len(mine) - len(good),
                    "avg": sum(bps) / len(bps) if bps else None,
                    "worst": min(bps) if bps else None,
                    "gap": sum(gaps) / len(gaps) if gaps else None})
    return out


def status_of(text: str) -> str:
    upper = text.upper()
    for word, state in (("COMPLETE", "complete"), ("ERROR", "error"),
                        ("CANCEL", "error"), ("RUNNING", "running"),
                        ("QUEUED", "running")):
        if word in upper:
            return state
    return "running"
