from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import joblib
import pandas as pd

import broker as broker_module
import config
import data as data_module
import features as features_module
import orders as orders_module
import production

CAPITAL = 500000.0
REBALANCE_DAYS = 20
MAX_MODEL_AGE_DAYS = 365
ROOT = os.path.join(config.MODEL_DIR, "bots")
REFRESH_MARK = os.path.join(ROOT, "refreshed.json")
EQUITY_FIELDS = ["date", "equity", "cash", "positions", "bench_nifty", "bench_ew"]

BOTS = {
    "gbm360": {"signal": "lightgbm"},
    "gbm100": {"signal": "gbm100"},
    "mom126": {"signal": None},
    "blend360": {"signal": "blend:lightgbm+mom_252"},
    "regime360": {"signal": "regime:blend:lightgbm+mom_252"},
}


def bot_dir(name):
    return os.path.join(ROOT, name)


def bot_path(name, leaf):
    return os.path.join(bot_dir(name), leaf)


def log(name, message):
    os.makedirs(bot_dir(name), exist_ok=True)
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {message}"
    print(f"[{name}] {message}", flush=True)
    with open(bot_path(name, "run.log"), "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = f"{path}.{os.getpid()}.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    os.replace(temporary, path)


def read_equity(name):
    path = bot_path(name, "equity.csv")
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def append_equity(name, row):
    path = bot_path(name, "equity.csv")
    fresh = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=EQUITY_FIELDS)
        if fresh:
            writer.writeheader()
        writer.writerow(row)


def refresh_cache_once():
    today = dt.date.today().isoformat()
    if read_json(REFRESH_MARK, {}).get("date") == today:
        print("cache already refreshed today", flush=True)
        return 0
    failed = 0
    for symbol in list(config.UNIVERSE) + [data_module.NIFTY]:
        for attempt in range(4):
            try:
                data_module.clear_memo()
                data_module.fetch(symbol, refresh=True, quiet=True)
                break
            except Exception as error:                          # noqa: BLE001
                if attempt == 3:
                    failed += 1
                    print(f"  refresh failed {symbol}: "
                          f"{type(error).__name__}", flush=True)
                else:
                    time.sleep(3 * (attempt + 1))
        time.sleep(0.2)
    data_module.clear_memo()
    if failed <= len(config.UNIVERSE) // 20:
        write_json(REFRESH_MARK, {"date": today, "failed": failed})
    print(f"cache refreshed, {failed} failed", flush=True)
    return failed


def market_closes():
    frames = data_module.load(list(config.UNIVERSE), interval="1d",
                              start=config.START)
    return data_module.close_panel(frames)


def latest_date(closes):
    counts = closes.notna().sum(axis=1)
    eligible = counts[counts >= 0.9 * counts.max()]
    return pd.Timestamp(eligible.index.max() if len(eligible)
                        else counts.index.max())


def nifty_level(date):
    frame = data_module.fetch(data_module.NIFTY, quiet=True)
    window = frame[frame.index <= date]
    return float(window["close"].iloc[-1]) if len(window) else float("nan")


def ew_level(closes, start, date):
    returns = closes.pct_change(fill_method=None).mean(axis=1)
    window = returns[(returns.index > start) & (returns.index <= date)]
    return CAPITAL * float((1.0 + window.fillna(0.0)).prod())


def ensure_model(name, signal_name):
    path = bot_path(name, "model.joblib")
    fitted = None
    if os.path.exists(path):
        try:
            fitted = dt.datetime.fromisoformat(
                joblib.load(path).get("fitted_at", ""))
        except (ValueError, TypeError, OSError):
            fitted = None
    if fitted is not None and (dt.datetime.now() - fitted).days < MAX_MODEL_AGE_DAYS:
        return
    log(name, f"fitting {signal_name}")
    signal, _ = production.fit(signal_name, quiet=True)
    roster = production.roster_for(signal_name)
    production.save(signal, signal_name, path=path)
    bundle = joblib.load(path)
    bundle["fitted_at"] = dt.datetime.now().isoformat(timespec="seconds")
    bundle["roster"] = roster
    joblib.dump(bundle, path)
    log(name, f"model saved, roster {len(roster) if roster else 'universe'}")


def model_targets(name, signal_name, on_date):
    signal, bundle = production.load(bot_path(name, "model.joblib"),
                                     expect_name=signal_name)
    roster = bundle.get("roster")
    panel = features_module.cross_sectionalize(
        features_module.build_panel(roster), require_target=False)
    date = (on_date if (panel["timestamp"] == on_date).any()
            else production.latest_complete_date(panel))
    frame = panel[panel["timestamp"] == date].copy()
    frame["score"] = signal.predict(frame)
    return frame.sort_values("score", ascending=False).head(
        config.TOP_K)["symbol"].tolist()


def momentum_targets(closes, date):
    row = closes.pct_change(126, fill_method=None).loc[date].dropna()
    return row.sort_values(ascending=False).head(config.TOP_K).index.tolist()


def trading_days_since(closes, last, date):
    if not last:
        return 10 ** 6
    index = closes.index
    return int(((index > pd.Timestamp(last)) & (index <= date)).sum())


def rebalance(name, spec, venue, closes, date, state):
    if spec["signal"]:
        ensure_model(name, spec["signal"])
        targets = model_targets(name, spec["signal"], date)
    else:
        targets = momentum_targets(closes, date)
    equity = venue.value()[0]
    plan = orders_module.plan(targets, venue=venue, capital=equity)
    ledger = bot_path(name, "sent_orders.jsonl")
    results = orders_module.execute(plan, venue=venue, sent_log=ledger)
    day = str(date.date())
    placed = 0
    for order, result in zip(plan, results):
        if "error" in result or result.get("outcome") == "rejected":
            log(name, f"order problem {order['symbol']} {order['side']}: "
                      f"{result.get('error') or result.get('outcome')}")
            continue
        orders_module._record_sent(day, order, ledger)
        placed += 1
    venue.save()
    state["last_rebalance"] = day
    state.setdefault("start_date", day)
    write_json(bot_path(name, "state.json"), state)
    log(name, f"rebalanced on {day}: {len(targets)} picks "
              f"({', '.join(targets)}), {placed}/{len(plan)} orders placed")


def run_bot(name, spec, closes, date):
    state_path = bot_path(name, "state.json")
    state = read_json(state_path, {})
    rows = read_equity(name)
    day = str(date.date())
    if rows and rows[-1]["date"] >= day:
        log(name, f"no new trading day (latest bar {day})")
        return
    venue = broker_module.PaperBroker(
        capital=CAPITAL, path=bot_path(name, "paper.json"))
    venue.pricing_date = date
    state.setdefault("start_date", day)
    due = (not venue.holdings()
           or trading_days_since(closes, state.get("last_rebalance"), date)
           >= REBALANCE_DAYS)
    if due and state.get("last_rebalance") != day:
        rebalance(name, spec, venue, closes, date, state)
    equity, _ = venue.value()
    row = {"date": day, "equity": round(equity, 2),
           "cash": round(venue.cash(), 2), "positions": len(venue.holdings()),
           "bench_nifty": round(nifty_level(date), 2),
           "bench_ew": round(ew_level(closes, pd.Timestamp(state["start_date"]),
                                      date), 2)}
    append_equity(name, row)
    state["last_run"] = day
    write_json(state_path, state)
    log(name, f"equity {row['equity']:.0f} on {day}")


def cmd_run():
    os.makedirs(ROOT, exist_ok=True)
    refresh_cache_once()
    closes = market_closes()
    date = latest_date(closes)
    failures = 0
    for name, spec in BOTS.items():
        try:
            run_bot(name, spec, closes, date)
        except BaseException as error:                          # noqa: BLE001
            failures += 1
            log(name, "FAILED " + "".join(
                traceback.format_exception(error)).strip())
    return 1 if failures else 0


def cmd_status():
    print(f"{'bot':<8} {'start':<11} {'equity':>10} {'ret%':>7} "
          f"{'vsNifty':>8} {'vsEW':>7} {'since':>6} {'last run':<11}")
    closes = None
    for name in BOTS:
        rows = read_equity(name)
        state = read_json(bot_path(name, "state.json"), {})
        if not rows:
            print(f"{name:<8} no runs yet")
            continue
        first, last = rows[0], rows[-1]
        equity = float(last["equity"])
        ret = (equity / float(first["equity"]) - 1) * 100

        def versus(key):
            base, now = float(first[key]), float(last[key])
            return ret - (now / base - 1) * 100 if base else float("nan")

        if closes is None:
            closes = market_closes()
        since = trading_days_since(closes, state.get("last_rebalance"),
                                   pd.Timestamp(last["date"]))
        print(f"{name:<8} {first['date']:<11} {equity:>10,.0f} {ret:>7.2f} "
              f"{versus('bench_nifty'):>8.2f} {versus('bench_ew'):>7.2f} "
              f"{since:>6} {state.get('last_run', '?'):<11}")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["run", "status"])
    args = parser.parse_args()
    sys.exit(cmd_run() if args.command == "run" else cmd_status())


if __name__ == "__main__":
    main()
