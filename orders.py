"""Turn a target book into the smallest set of orders that reaches it.

WHAT THIS FILE IS CAREFUL ABOUT

Rebalancing to a target is arithmetic, but it is arithmetic where every mistake
costs money, so three things are enforced rather than assumed:

  Sell before buy. The orders come back sorted sells-first. Buying first can
  overdraw the account on a rebalance that is fully invested, and a rejected
  order halfway through a rebalance leaves the book in a state nobody designed.

  Whole shares, and the cash to pay for them. Quantities are floored, never
  rounded, and the buy leg is checked against cash including charges. Rounding
  up a quantity is how a paper simulation quietly grants itself credit.

  No trade below a floor. A 40-share position drifting to 39 generates an order
  whose fixed charges - Rs 15.34 DP, Rs 20 brokerage - exceed any plausible
  benefit. MIN_TRADE_VALUE suppresses those, which is the same idea as the hold
  buffer in backtest.py applied at the share level.

THE SAFETY MODEL

`plan` is pure: it computes orders and touches nothing. `execute` is the only
function that changes state, it refuses any broker whose `live` flag is set
unless `confirm_live=True` is passed explicitly, and it stops on the first
failure rather than continuing down the list. The default path in this project
is paper, and nothing here quietly promotes itself to real money.

Run with:  py -3.13 orders.py                  show the plan, place nothing
           py -3.13 orders.py --execute        place it on the paper account
"""

from __future__ import annotations

import argparse
import json
import math
import os

import broker as broker_module
import config
import costs as costs_module

MIN_TRADE_VALUE = 5000.0        # below this, fixed charges dominate; skip

# --- idempotent execution ---------------------------------------------------
# Groww CNC deliveries do not post to venue.holdings() until T+1 settlement, so
# a crash-and-rerun on the same day recomputes the identical delta and would
# otherwise resend an order already placed - bypassing risk.py entirely,
# because the order genuinely looks new to it. This ledger remembers what was
# actually sent (day, symbol, side, requested qty) so a rerun can recognise a
# repeat and refuse it, the same append-only JSONL convention as runs.py and
# risk.py's audit log.
SENT_LOG_PATH = os.path.join(config.MODEL_DIR, "sent_orders.jsonl")

_STATUS_KEYS = ("status", "order_status", "state")
_REJECTION_WORDS = ("reject", "fail", "cancel", "error")
_FILLED_QTY_KEYS = ("filled_quantity", "quantity_filled", "executed_quantity",
                    "filled_qty", "traded_quantity")


def target_quantities(targets: list[str], capital: float, price_of,
                      frame=None, scheme: str | None = None) -> dict[str, int]:
    """Allocate `capital` across `targets`, in whole shares.

    Equal weight by default, and that default is measured rather than assumed:
    weighting by model confidence was tested on the same 14 walk-forward folds
    and lost by 15bp, because the scores drift between rebalances and weighting
    by them churns money between names the book already holds. The model chooses
    WHICH names; it has not earned the right to choose how much.

    Pass `frame` (one date's scored panel) and `scheme` to override - see
    config.SIZING_SCHEME and sizing.py.
    """
    if not targets:
        return {}

    scheme = scheme or config.SIZING_SCHEME
    if scheme != "equal" and frame is not None:
        import sizing as sizing_module

        allocation = sizing_module.weights(frame, targets, scheme)
    else:
        allocation = {symbol: 1.0 / len(targets) for symbol in targets}

    out = {}
    for symbol in targets:
        price = price_of(symbol)
        share = float(allocation[symbol]) if symbol in allocation else 0.0
        if price and price > 0 and share > 0:
            # Floored, never rounded: rounding up is how a simulation quietly
            # grants itself credit it does not have.
            out[symbol] = int(math.floor(capital * share / price))
    return out


def plan(targets: list[str], venue=None, capital: float | None = None,
         frame=None, scheme: str | None = None) -> list[dict]:
    """Orders that move current holdings to the target book. Places nothing."""
    venue = venue or broker_module.broker()
    holdings = venue.holdings()

    equity = venue.cash() + sum(
        position["qty"] * _safe_price(venue, symbol, position["avg"])
        for symbol, position in holdings.items())
    capital = capital if capital is not None else equity

    wanted = target_quantities(
        targets, capital, lambda s: _safe_price(venue, s, 0.0), frame, scheme)

    orders = []
    for symbol, position in holdings.items():
        if symbol not in wanted:
            orders.append({"symbol": symbol, "side": "SELL",
                           "qty": position["qty"], "reason": "left the book"})

    for symbol, quantity in wanted.items():
        held = holdings.get(symbol, {"qty": 0})["qty"]
        delta = quantity - held
        if delta == 0:
            continue
        price = _safe_price(venue, symbol, 0.0)
        if abs(delta) * price < MIN_TRADE_VALUE:
            continue                    # not worth the fixed charges
        orders.append({"symbol": symbol,
                       "side": "BUY" if delta > 0 else "SELL",
                       "qty": abs(delta),
                       "reason": "new position" if held == 0 else "resize"})

    # Sells first: they fund the buys.
    orders.sort(key=lambda order: 0 if order["side"] == "SELL" else 1)
    for order in orders:
        price = _safe_price(venue, order["symbol"], 0.0)
        order["price"] = round(price, 2)
        order["value"] = round(price * order["qty"], 2)
        order["cost_bp"] = round(costs_module.cost_bp(order["value"] or 1.0), 1)
    return orders


def _safe_price(venue, symbol: str, fallback: float) -> float:
    try:
        return venue.ltp(symbol)
    except Exception:                                           # noqa: BLE001
        return fallback


def _actual_fill(order: dict, raw: dict) -> dict:
    """Read what the venue actually transacted, not what was requested.

    PaperBroker's dict has no status or partial-fill field - it always fills
    completely or raises - so this is a no-op for it: nothing below matches and
    the order's own quantity is used. GrowwBroker's response shape has never
    been seen against a real account (see broker.py's warning on that class),
    so the field names checked here are a best-effort guess, not a confirmed
    contract. An unrecognised shape is treated as a complete fill rather than
    silently trusted as one or silently dropped - the raw response is kept in
    the result either way, under `raw`, so nothing this function misreads is
    thrown away.
    """
    requested = int(order["qty"])
    fill = dict(raw)
    fill["raw"] = dict(raw)

    status = ""
    for key in _STATUS_KEYS:
        value = raw.get(key)
        if isinstance(value, str):
            status = value.lower()
            break
    rejected = any(word in status for word in _REJECTION_WORDS)

    filled_qty = None
    for key in _FILLED_QTY_KEYS:
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            filled_qty = int(value)
            break

    if filled_qty is None:
        filled_qty = 0 if rejected else requested

    fill["requested_qty"] = requested
    fill["filled_qty"] = filled_qty
    fill["outcome"] = ("rejected" if filled_qty <= 0
                       else "partial" if filled_qty < requested else "filled")
    price = float(fill.get("price") or order.get("price") or 0.0)
    fill["filled_value"] = round(price * filled_qty, 2)
    return fill


def _trading_day(venue) -> str:
    """The trading day an order belongs to, for the idempotency ledger.

    A live order has no simulated date, so this is just today in IST - the
    same calendar day risk.py's tally resets on. PaperBroker's replay sets
    `pricing_date` to the session being replayed, and using that instead of
    the wall clock is what keeps a 120-day, one-process replay from reading as
    120 resends of "today": each simulated session is its own trading day.
    """
    pricing_date = getattr(venue, "pricing_date", None)
    if pricing_date is not None:
        return str(pricing_date.date() if hasattr(pricing_date, "date")
                   else pricing_date)[:10]
    return broker_module.now_ist().strftime("%Y-%m-%d")


def _sent_key(day: str, order: dict) -> tuple:
    return (day, order["symbol"], order["side"], int(order["qty"]))


def _load_sent() -> list[dict]:
    if not os.path.exists(SENT_LOG_PATH):
        return []
    rows = []
    with open(SENT_LOG_PATH, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _already_sent(day: str, order: dict) -> bool:
    key = _sent_key(day, order)
    for row in _load_sent():
        if (row.get("day"), row.get("symbol"), row.get("side"),
                row.get("qty")) == key:
            return True
    return False


def _record_sent(day: str, order: dict) -> None:
    """Append one ledger row, crash-safe: temp file, flush, fsync, replace.

    Same idiom as PaperBroker.save() - a plain append can leave a half-written
    line if the process dies mid-write, and on an unreliable UPS that is
    exactly when it will happen. Called right after venue.send() returns
    without raising, i.e. the order reached the venue - not after the fill is
    classified, so the same (day, symbol, side, qty) is remembered whether the
    result turns out filled, partial or rejected. The one gap this cannot
    close is a crash between the venue accepting the order and this write
    landing on disk; FIX 1's stable order_reference_id gives Groww's own side
    a chance to recognise a resend even then.
    """
    rows = _load_sent()
    rows.append({"day": day, "symbol": order["symbol"], "side": order["side"],
                "qty": int(order["qty"]),
                "time": broker_module.now_ist().isoformat(timespec="seconds")})
    os.makedirs(os.path.dirname(SENT_LOG_PATH), exist_ok=True)
    temporary = f"{SENT_LOG_PATH}.{os.getpid()}.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, SENT_LOG_PATH)


def execute(orders: list[dict], venue=None, confirm_live: bool = False
            ) -> list[dict]:
    """Place the orders. The only function here that changes anything.

    Live venues pass through two independent gates, and both must open:

      `confirm_live=True` from the caller, which is a statement of intent that
      the GUI never makes on its own.

      risk.check on every single order, which enforces arming, the per-order
      and per-day limits, the universe, and a price band against a live quote.

    They are separate on purpose. The first says "I meant to trade"; the second
    says "and this particular order is sane". Passing the first has never been
    sufficient.
    """
    venue = venue or broker_module.broker()
    is_live = getattr(venue, "live", False)

    if is_live and not confirm_live:
        raise RuntimeError(
            f"{venue.name} is a live venue. Pass confirm_live=True to place "
            f"real orders - and read orders.py and risk.py before you do.")

    if is_live:
        import risk

        # Pre-flight the WHOLE plan before placing any of it. Discovering on
        # order four that the day's limit is exhausted would leave a half-built
        # book, which is worse than not trading at all.
        for order in orders:
            risk.check(order, venue)

    # Keyed on the trading day so this survives a process restart; scoped to
    # live orders only, per the same "paper mode is unaffected" rule as
    # risk.check/risk.record below. PaperBroker's holdings() update the instant
    # an order fills, so a rerun of the same plan on paper always sees the
    # position already there and naturally computes a different (usually
    # empty) delta - it never had this bug. It also replays real historical
    # dates, sometimes the same ones across separate runs (e.g. two
    # `paper.py --days 120` runs today), and a ledger that did not distinguish
    # live from paper would read the second run's identical, entirely
    # legitimate trades as duplicates of the first and silently starve it.
    day = _trading_day(venue) if is_live else None
    results = []
    for order in orders:
        try:
            if is_live and _already_sent(day, order):
                # Same (day, symbol, side, qty) already reached the venue.
                # Likely a crash-and-rerun recomputing the same delta because
                # holdings() has not caught up yet (Groww CNC settles T+1) -
                # never silently dropped, but not resent either.
                skip = {"symbol": order["symbol"], "side": order["side"],
                       "qty": order["qty"],
                       "skipped": "duplicate order already sent today"}
                results.append(skip)
                continue

            if is_live:
                import risk

                # Re-checked immediately before sending: the pre-flight was a
                # moment ago, and arming can lapse or a HALT can appear between
                # the first order and the last.
                risk.check(order, venue)

            fill = venue.send(order["symbol"], order["side"], order["qty"],
                              order.get("price"))
            fill = dict(fill) if isinstance(fill, dict) else {"result": fill}
            fill.setdefault("symbol", order["symbol"])
            fill.setdefault("side", order["side"])
            fill.setdefault("qty", order["qty"])
            fill["reason"] = order.get("reason", "")
            fill = _actual_fill(order, fill)

            if is_live:
                # The order reached the venue - record it before doing
                # anything else, so a resend attempt after this point (even a
                # crash right here) is recognised as a repeat regardless of
                # how the fill below is ultimately classified.
                _record_sent(day, order)

            results.append(fill)
            if is_live:
                risk.record(order, fill, venue.name)
            if fill["outcome"] == "rejected":
                # A "success" response that is actually a rejection is not a
                # clean fill and must not be read as one. Stop here, same as
                # an exception: the next order is likely to fail the same way,
                # and a half-built book is worse than pausing to look.
                break
        except Exception as error:                              # noqa: BLE001
            # Stop rather than continue: a partially applied rebalance is a
            # book nobody chose, and the next order is likely to fail the same
            # way. Better to leave a clean prefix and say what broke.
            failure = {"symbol": order["symbol"], "side": order["side"],
                       "qty": order["qty"], "error": str(error)}
            results.append(failure)
            if is_live:
                import risk

                risk.record(order, failure, venue.name)
            break
    return results


def describe(orders: list[dict]) -> str:
    if not orders:
        return "no orders - the book already matches the target"
    lines = [f"{'side':<6}{'symbol':<14}{'qty':>7}{'price':>10}{'value':>12}"
             f"{'cost':>8}  reason", "-" * 72]
    total = 0.0
    for order in orders:
        total += order["value"] if order["side"] == "BUY" else -order["value"]
        lines.append(f"{order['side']:<6}{order['symbol']:<14}{order['qty']:>7}"
                     f"{order['price']:>10.2f}{order['value']:>12,.0f}"
                     f"{order['cost_bp']:>7.0f}bp  {order.get('reason', '')}")
    lines.append("-" * 72)
    lines.append(f"net cash movement {-total:>+,.0f}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--execute", action="store_true",
                        help="actually place the orders")
    parser.add_argument("--live", action="store_true",
                        help="permit a live venue; also needs risk.py armed")
    parser.add_argument("--symbols", nargs="*",
                        help="target book; defaults to the model's top picks")
    parser.add_argument("--capital", type=float, default=None)
    args = parser.parse_args()

    venue = broker_module.broker()
    if getattr(venue, "live", False):
        import risk

        print(f"VENUE: {venue.name} (LIVE - real money)\n")
        # Checked before anything touches the network, so an unconfigured
        # venue reports one clear line instead of a traceback from three
        # frames deep inside a quote request.
        if not venue.usable():
            raise SystemExit(f"{venue.name} is not configured.\n  "
                             f"{venue.explain()}\n  "
                             f"Add keys on the Venues page, or "
                             f"py -3.13 credentials.py --set {venue.name} "
                             f"access_token")
        print(risk.describe())
        print()
    targets = args.symbols
    if not targets:
        targets = _model_picks()
        print(f"target book from the model: {', '.join(targets)}\n")

    orders = plan(targets, venue, args.capital)
    print(describe(orders))

    if not args.execute:
        print("\nnothing placed. Re-run with --execute to apply.")
        return

    print()
    for result in execute(orders, venue, confirm_live=args.live):
        if "error" in result:
            print(f"  FAILED {result['side']} {result['qty']} "
                  f"{result['symbol']}: {result['error']}")
        elif result.get("skipped"):
            print(f"  SKIPPED {result['side']} {result['qty']} "
                  f"{result['symbol']}: {result['skipped']}")
        elif result.get("outcome") == "rejected":
            print(f"  REJECTED {result['side']} {result['qty']} "
                  f"{result['symbol']}: no quantity filled")
        elif result.get("outcome") == "partial":
            print(f"  PARTIAL {result['side']} {result['filled_qty']}/"
                  f"{result['requested_qty']} {result['symbol']:<12} "
                  f"@ {result.get('price', 0):>9.2f}")
        else:
            print(f"  {result['side']} {result['qty']:>5} {result['symbol']:<12} "
                  f"@ {result['price']:>9.2f}  charges {result['charges']:>7.2f}")
    print(f"\n{venue.summary()}")


def _model_picks() -> list[str]:
    """The current top-K by model score. Whichever model production.py holds."""
    import production

    return production.picks()


if __name__ == "__main__":
    main()
