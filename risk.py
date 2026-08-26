"""The gate every live order passes through. Closed unless deliberately opened.

WHY ARMING EXPIRES

An arming switch that stays on is a switch that is always on. The state here
carries a deadline - default 30 minutes - after which it lapses without anyone
doing anything. The failure mode being designed against is not a wrong click, it
is arming the system on Tuesday to place one order and finding it still armed on
Friday when a scheduled job fires.

WHY THE LIMITS ARE ABSOLUTE AND NOT ADVISORY

Every check below refuses. None of them warn and continue. A risk layer that can
be talked past by the code above it is documentation, not a control. If a limit
is wrong, the fix is to change the limit in a file you can read, not to add an
override flag to the call site.

WHAT IS ACTUALLY BEING PROTECTED AGAINST

  A stale price. The book is built from cached data that could be days old. An
  order priced off it can be far from the market, so limit prices are checked
  against the broker's own live quote and refused beyond a band.

  A quantity error. One misplaced zero turns Rs 83,000 into Rs 830,000.
  MAX_ORDER_VALUE stops that at the order, MAX_DAY_NOTIONAL stops a sequence of
  them at the session.

  A runaway loop. A rebalance that retries in a loop can place the same order
  many times. MAX_DAY_ORDERS caps the count regardless of where the calls come
  from, because the counter is on disk and survives a restart.

  Anything not in the universe. The strategy only ever wants 30 large caps. An
  order for something else means the caller is confused, and a confused caller
  should not be filled.

THE KILL SWITCH

Create the file artifacts/HALT and nothing places again until it is deleted. It
is checked first, before arming, before limits, so it works even if the state
file is corrupt.

Run with:  py -3.13 risk.py                    show state
           py -3.13 risk.py --arm 30           open the gate for 30 minutes
           py -3.13 risk.py --disarm
           py -3.13 risk.py --halt / --resume
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone

import config

IST = timezone(timedelta(hours=5, minutes=30))

STATE_PATH = os.path.join(config.MODEL_DIR, "risk_state.json")
AUDIT_PATH = os.path.join(config.MODEL_DIR, "orders_audit.jsonl")
HALT_PATH = os.path.join(config.MODEL_DIR, "HALT")

# --- the limits ------------------------------------------------------------
MAX_ORDER_VALUE = 150000.0      # rupees in a single order
MAX_DAY_NOTIONAL = 1200000.0    # rupees traded in one calendar day
MAX_DAY_ORDERS = 40             # orders placed in one calendar day
MAX_PRICE_DEVIATION = 0.03      # limit price vs live quote, 3%
DEFAULT_ARM_MINUTES = 30
MAX_ARM_MINUTES = 240


class Refused(Exception):
    """A control said no. Never caught and downgraded to a warning."""


def _today() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def _load() -> dict:
    if not os.path.exists(STATE_PATH):
        return {}
    try:
        with open(STATE_PATH, encoding="utf-8") as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError):
        # An unreadable state file means disarmed, never armed. Failing closed
        # is the only safe direction for this particular unknown.
        return {}


def _save(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)


# --- halt ------------------------------------------------------------------

def halted() -> bool:
    return os.path.exists(HALT_PATH)


def halt(reason: str = "manual") -> str:
    os.makedirs(os.path.dirname(HALT_PATH), exist_ok=True)
    with open(HALT_PATH, "w", encoding="utf-8") as handle:
        handle.write(f"{datetime.now(IST).isoformat(timespec='seconds')} {reason}\n")
    return HALT_PATH


def resume() -> bool:
    if halted():
        os.remove(HALT_PATH)
        return True
    return False


# --- arming ----------------------------------------------------------------

def arm(minutes: int = DEFAULT_ARM_MINUTES, note: str = "") -> dict:
    """Open the gate for a bounded window. Refuses while halted."""
    if halted():
        raise Refused("halted - delete artifacts/HALT before arming")
    minutes = max(1, min(int(minutes), MAX_ARM_MINUTES))
    state = _load()
    state["armed_until"] = (datetime.now(IST)
                            + timedelta(minutes=minutes)).isoformat()
    state["armed_at"] = datetime.now(IST).isoformat(timespec="seconds")
    state["note"] = note
    _save(state)
    return state


def disarm() -> None:
    state = _load()
    state.pop("armed_until", None)
    _save(state)


def armed() -> bool:
    if halted():
        return False
    until = _load().get("armed_until")
    if not until:
        return False
    try:
        return datetime.fromisoformat(until) > datetime.now(IST)
    except ValueError:
        return False


def arm_remaining() -> int:
    """Seconds of arming left; 0 when closed."""
    until = _load().get("armed_until")
    if not until or halted():
        return 0
    try:
        left = (datetime.fromisoformat(until) - datetime.now(IST)).total_seconds()
    except ValueError:
        return 0
    return max(int(left), 0)


# --- the day's tally -------------------------------------------------------

def _tally() -> dict:
    state = _load()
    tally = state.get("tally", {})
    if tally.get("date") != _today():
        return {"date": _today(), "orders": 0, "notional": 0.0}
    return tally


def record(order: dict, result: dict, venue: str) -> None:
    """Count it against the day's limits and write it to the audit log.

    Called after a live placement whether it succeeded or failed. A rejected
    order still consumed an attempt, and a log that only holds successes is
    exactly the log you cannot use to work out what went wrong.
    """
    tally = _tally()
    tally["orders"] += 1
    # Count what was actually transacted, not what was requested: a partial
    # fill or a rejection dressed as a "success" must not consume the day's
    # notional limit as though the full order went through. orders.py sets
    # result["filled_value"] from the venue's own response (see
    # orders._actual_fill); fall back to the requested value only when that is
    # absent, which is the case for a plain exception failure that never
    # reached the venue at all - unchanged from the previous behaviour there.
    transacted = result.get("filled_value")
    tally["notional"] += (float(transacted) if transacted is not None
                          else float(order.get("value", 0.0)))
    state = _load()
    state["tally"] = tally
    _save(state)

    os.makedirs(os.path.dirname(AUDIT_PATH), exist_ok=True)
    with open(AUDIT_PATH, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "time": datetime.now(IST).isoformat(timespec="seconds"),
            "venue": venue, "order": order, "result": result}) + "\n")


def audit(limit: int = 20) -> list[dict]:
    if not os.path.exists(AUDIT_PATH):
        return []
    rows = []
    with open(AUDIT_PATH, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows[-limit:]


# --- the gate --------------------------------------------------------------

def check(order: dict, venue=None) -> None:
    """Raise Refused unless this order may be placed live. Returns None or raises.

    Order of checks is deliberate: the cheapest and most absolute first, so a
    halted system does not go asking a broker for quotes.
    """
    if halted():
        raise Refused("HALT file present - trading stopped")

    if not armed():
        raise Refused("not armed - live orders are disabled by default. "
                      "Arm explicitly, and it lapses on its own.")

    symbol = order.get("symbol", "")
    if symbol not in config.UNIVERSE:
        raise Refused(f"{symbol} is not in the universe")

    quantity = int(order.get("qty", 0))
    if quantity <= 0:
        raise Refused(f"quantity must be positive, got {quantity}")

    if order.get("side") not in ("BUY", "SELL"):
        raise Refused(f"side must be BUY or SELL, got {order.get('side')!r}")

    value = float(order.get("value", 0.0))
    if value <= 0:
        raise Refused("order value is missing or zero")
    if value > MAX_ORDER_VALUE:
        raise Refused(f"order value Rs {value:,.0f} exceeds the "
                      f"Rs {MAX_ORDER_VALUE:,.0f} per-order limit")

    tally = _tally()
    if tally["orders"] >= MAX_DAY_ORDERS:
        raise Refused(f"{tally['orders']} orders already today, limit is "
                      f"{MAX_DAY_ORDERS}")
    if tally["notional"] + value > MAX_DAY_NOTIONAL:
        raise Refused(f"Rs {tally['notional']:,.0f} already traded today; this "
                      f"order would exceed the Rs {MAX_DAY_NOTIONAL:,.0f} "
                      f"daily limit")

    # Last, because it costs a network call: is our price anywhere near the
    # market's? The book is built from cached bars that may be days stale.
    price = float(order.get("price", 0.0))
    if venue is not None and price > 0:
        try:
            live = float(venue.ltp(symbol))
        except Exception as error:                              # noqa: BLE001
            raise Refused(f"cannot verify {symbol} against a live quote: "
                          f"{error}") from error
        if live > 0:
            drift = abs(price - live) / live
            if drift > MAX_PRICE_DEVIATION:
                raise Refused(
                    f"{symbol}: our price {price:,.2f} is {drift:.1%} from the "
                    f"live quote {live:,.2f}, beyond the "
                    f"{MAX_PRICE_DEVIATION:.0%} band - the cached data is stale")


def status() -> dict:
    tally = _tally()
    return {
        "halted": halted(),
        "armed": armed(),
        "seconds_left": arm_remaining(),
        "orders_today": tally["orders"],
        "notional_today": tally["notional"],
        "max_order_value": MAX_ORDER_VALUE,
        "max_day_orders": MAX_DAY_ORDERS,
        "max_day_notional": MAX_DAY_NOTIONAL,
    }


def describe() -> str:
    state = status()
    if state["halted"]:
        head = "HALTED - trading stopped, delete artifacts/HALT to resume"
    elif state["armed"]:
        head = f"ARMED - {state['seconds_left'] // 60}m {state['seconds_left'] % 60}s left"
    else:
        head = "DISARMED - live orders refused (this is the default)"
    return (f"{head}\n"
            f"  today          {state['orders_today']} orders, "
            f"Rs {state['notional_today']:,.0f}\n"
            f"  per order      max Rs {state['max_order_value']:,.0f}\n"
            f"  per day        max {state['max_day_orders']} orders, "
            f"Rs {state['max_day_notional']:,.0f}\n"
            f"  price band     {MAX_PRICE_DEVIATION:.0%} from the live quote")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--arm", nargs="?", type=int, const=DEFAULT_ARM_MINUTES,
                        metavar="MINUTES")
    parser.add_argument("--disarm", action="store_true")
    parser.add_argument("--halt", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--audit", action="store_true")
    args = parser.parse_args()

    if args.halt:
        print(f"halted, wrote {halt('cli')}")
    if args.resume:
        print("resumed" if resume() else "was not halted")
    if args.disarm:
        disarm()
        print("disarmed")
    if args.arm:
        arm(args.arm, note="cli")
        print(f"armed for {args.arm} minutes")

    print()
    print(describe())

    if args.audit:
        rows = audit()
        print(f"\nlast {len(rows)} live order attempts:")
        for row in rows:
            order, result = row["order"], row["result"]
            outcome = result.get("error") or result.get("status", "sent")
            print(f"  {row['time'][11:19]}  {order.get('side'):<4} "
                  f"{order.get('qty'):>5} {order.get('symbol'):<12} "
                  f"{outcome}")


if __name__ == "__main__":
    main()
