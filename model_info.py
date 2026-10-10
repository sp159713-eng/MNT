from __future__ import annotations

import importlib.util

DEFAULT = "sect:blend:lightgbm+mom_252"

LABELS = {
    "sect:blend:lightgbm+mom_252": "Sector blend (default)",
    "blend:lightgbm+mom_252": "Blend: LightGBM + 1y momentum",
    "regime:blend:lightgbm+mom_252": "Regime blend",
    "lightgbm": "LightGBM",
    "nn": "Neural net",
    "gbmall": "Untrained",
    "momentum": "Momentum",
    "blend:nn346+mom_252": "NN blend",
}

SHORT = {
    "sect:blend:lightgbm+mom_252": "Sector blend",
    "blend:lightgbm+mom_252": "LGBM+mom",
    "regime:blend:lightgbm+mom_252": "Regime blend",
}


def label(name, short: bool = False) -> str:
    if not name:
        return "Momentum"
    if short and name in SHORT:
        return SHORT[name]
    if name in LABELS:
        return LABELS[name]
    if name.startswith("gbm") and name[3:].isdigit():
        return f"GBM {name[3:]} names"
    return name


def options(presets=()) -> list[tuple[str, str]]:
    names = [DEFAULT, "blend:lightgbm+mom_252", "regime:blend:lightgbm+mom_252",
             "lightgbm"]
    if importlib.util.find_spec("torch") is not None:
        names.append("nn")
    names += [f"gbm{c}" for c in presets]
    names.append("gbmall")
    return [(name, label(name)) for name in names]


def _record(signal: str, oracle: bool = False):
    import gapcard
    import runs

    plain = not signal.startswith(("sect:", "regime:"))
    mine = []
    for r in runs.load():
        if not (isinstance(r, dict) and r.get("kind") == "walkforward"
                and isinstance(r.get("pooled"), dict)):
            continue
        if not gapcard.matches(r, signal):
            continue
        added = str((r.get("settings") or {}).get("add") or "").strip()
        if plain and added:
            continue
        if oracle and gapcard.ORACLE not in (r.get("baselines") or {}):
            continue
        mine.append(r)
    return mine[-1] if mine else None


def _num(source, key):
    try:
        return float(source[key])
    except Exception:
        return None


def stats(signal: str) -> dict:
    import gapcard
    import model_timings

    out = {"signal": signal, "record": None, "timing": model_timings.get(signal)}
    try:
        record = _record(signal)
    except Exception:
        record = None
    if not record:
        return out
    pooled = record.get("pooled") or {}
    rows = gapcard.gap_rows(record)
    ours = next((v for _, v, k in rows if k == "ours"), None)
    oracle = next((v for _, v, k in rows if k == "oracle"), None)
    net, bench = _num(pooled, "net"), _num(pooled, "benchmark")
    edge = record.get("edge") or {}
    captured, cap_from = None, None
    if ours is not None and oracle:
        captured = ours / oracle * 100
    else:
        try:
            ref = _record(signal, oracle=True)
        except Exception:
            ref = None
        if ref:
            ref_rows = gapcard.gap_rows(ref)
            ref_ours = next((v for _, v, k in ref_rows if k == "ours"), None)
            ref_oracle = next((v for _, v, k in ref_rows if k == "oracle"), None)
            if ref_ours is not None and ref_oracle:
                captured = ref_ours / ref_oracle * 100
                cap_from = str(ref.get("at", ""))[:10]
    out["record"] = {
        "net": net,
        "bench": bench,
        "excess": None if net is None or bench is None else net - bench,
        "edge_bp": _num(edge, "net_excess_bp"),
        "edge_vs": edge.get("baseline"),
        "captured": captured,
        "captured_from": cap_from,
        "turnover": _num(pooled, "turnover"),
        "folds": len(record.get("folds") or []),
        "date": str(record.get("at", ""))[:10],
        "run": record.get("id"),
    }
    return out
