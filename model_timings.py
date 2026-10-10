from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime

import config

PATH = os.path.join(config.MODEL_DIR, "model_timings.json")


def load() -> dict:
    try:
        with open(PATH, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def record(signal: str | None, kind: str, seconds: float) -> None:
    if not signal:
        return
    try:
        data = load()
        entry = data.get(signal)
        entry = entry if isinstance(entry, dict) else {}
        entry[f"{kind}_s"] = round(float(seconds), 2)
        entry[f"{kind}_at"] = datetime.now().isoformat(timespec="seconds")
        data[signal] = entry
        os.makedirs(os.path.dirname(PATH), exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=os.path.dirname(PATH), suffix=".tmp")
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(data, out, indent=1)
        os.replace(temp, PATH)
    except Exception:
        pass


def get(signal: str) -> dict:
    entry = load().get(signal)
    return entry if isinstance(entry, dict) else {}


def human(seconds) -> str:
    try:
        seconds = float(seconds)
    except Exception:
        return "-"
    if seconds < 90:
        return f"{seconds:.0f}s"
    return f"{seconds / 60:.1f} min"
