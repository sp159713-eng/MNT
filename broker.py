"""One interface for every venue, and a paper venue that needs no venue at all.

WHY THE PAPER BROKER IS THE DEFAULT AND NOT AN AFTERTHOUGHT

The strategy in this project has a walk-forward t-statistic below 2. Wiring it to
a live account would be connecting real money to an edge that has not been
established, so the default implementation here fills orders against cached
prices and moves numbers in a JSON file. Everything above this module - orders,
paper, pulse, the GUI - talks to `Broker`, so the day a live adapter is
configured, nothing else changes.

That is also the honest reason the live adapters below are thin. They declare
their credential requirements and refuse to pretend otherwise; an adapter that
cannot be tested against a real account should not carry code that looks tested.

WHAT A FILL COSTS

PaperBroker does not fill at the price you asked for. It crosses the spread by
`slippage_bp` and charges the full statutory schedule from costs.py, because a
paper record that ignores costs teaches the opposite of the lesson this project
exists to teach - a strategy that broke even at 4.6bp against a 34bp charge
sheet looked excellent right up until the charge sheet was applied.

Run with:  py -3.13 broker.py                 show state
           py -3.13 broker.py --reset         start a fresh paper account
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone

import pandas as pd

import config
import costs as costs_module

IST = timezone(timedelta(hours=5, minutes=30))
STATE_PATH = os.path.join(config.MODEL_DIR, "paper_account.json")

# Which credential provider backs each venue. The fields themselves, and the
# rule for when they are sufficient, live in credentials.py.
CREDENTIAL_PROVIDER = {"groww": "groww", "angelone": "angelone"}


def now_ist() -> datetime:
    return datetime.now(IST)


class Broker:
    """What every adapter promises. Anything above this file uses only these."""

    name = ""
    title = ""
    live = False

    def usable(self) -> bool:
        """Whether this venue has everything it needs to be opened."""
        raise NotImplementedError

    def explain(self) -> str:
        """One line saying what is missing, for the UI to show."""
        raise NotImplementedError

    def ltp(self, symbol: str) -> float:
        raise NotImplementedError

    def holdings(self) -> dict:
        """symbol -> {'qty': int, 'avg': float}."""
        raise NotImplementedError

    def cash(self) -> float:
        raise NotImplementedError

    def send(self, symbol: str, side: str, quantity: int,
             limit: float | None = None) -> dict:
        raise NotImplementedError

    def summary(self) -> str:
        raise NotImplementedError


class PaperBroker(Broker):
    """A full account simulator backed by the local price cache.

    State is a single JSON file so a paper run survives a restart, which matters
    more than it sounds: the point of paper trading is to accumulate a record
    over weeks, and an account that resets whenever the process exits produces
    a sequence of one-day experiments instead.
    """

    name = "paper"
    title = "Paper account"
    live = False

    def __init__(self, capital: float = 500000.0,
                 slippage_bp: float = costs_module.DEFAULT_SLIPPAGE_BP,
                 segment: str = "delivery", path: str = STATE_PATH):
        self.path = path
        self.slippage_bp = slippage_bp
        self.segment = segment
        # When set, prices come from this date instead of the last cached bar.
        # This is what lets paper.py replay history through the same fill and
        # charge logic a live paper session uses, rather than a second
        # implementation that could disagree with it.
        self.pricing_date = None
        self.state = self._load(capital)

    # -- state ------------------------------------------------------------
    def _fresh(self, capital: float) -> dict:
        return {"cash": capital, "opened": capital, "positions": {},
                "trades": [], "created": now_ist().isoformat()}

    def _load(self, capital: float) -> dict:
        """Read the account, and survive a state file that cannot be read.

        A half-written state file used to raise straight out of the
        constructor, which bricks every later run - including the sim, whose
        first act is to reset the account it just failed to parse. That is a
        run lost to a file nobody needed.

        The unreadable file is moved aside rather than overwritten. This class
        also backs the live paper account, where silently starting fresh would
        destroy the trade history the account exists to keep; a copy on disk
        and a loud line is the difference between a recoverable accident and a
        deleted record.
        """
        if not os.path.exists(self.path):
            return self._fresh(capital)
        try:
            with open(self.path, encoding="utf-8") as handle:
                state = json.load(handle)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as error:
            spoiled = f"{self.path}.corrupt-{now_ist():%Y%m%d-%H%M%S}"
            try:
                os.replace(self.path, spoiled)
                moved = spoiled
            except OSError:
                moved = "(could not be moved)"
            print(f"  {os.path.basename(self.path)} could not be read "
                  f"({type(error).__name__}); kept as {moved} and starting "
                  f"from a fresh account.")
            return self._fresh(capital)
        if not isinstance(state, dict):
            return self._fresh(capital)
        return state

    def save(self) -> None:
        """Write via a temporary file, so an interrupted save cannot brick it.

        Writing in place is what produced the 35 KB of NUL bytes this loader
        now has to tolerate: the file is extended before the contents reach the
        disk, so a process that dies mid-write leaves a file that exists, has a
        plausible size, and parses as nothing.
        """
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        temporary = f"{self.path}.{os.getpid()}.tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(self.state, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)

    def reset(self, capital: float | None = None) -> None:
        opened = capital if capital is not None else self.state.get("opened", 500000.0)
        self.state = {"cash": opened, "opened": opened, "positions": {},
                      "trades": [], "created": now_ist().isoformat()}
        self.save()

    # -- market data ------------------------------------------------------
    def usable(self) -> bool:
        return True

    def explain(self) -> str:
        return "paper account, no credentials required"

    def ltp(self, symbol: str) -> float:
        """Last cached close, or the close on `pricing_date` when replaying.

        Not a live quote, and does not pretend to be.
        """
        import data as data_module

        frame = data_module.fetch(symbol, quiet=True)
        if frame.empty:
            raise RuntimeError(f"no cached price for {symbol}")
        if self.pricing_date is not None:
            # Strictly at or before the date, so a replay can never read a
            # price that had not printed yet.
            window = frame[frame.index <= self.pricing_date]
            if window.empty:
                raise RuntimeError(f"{symbol} has no bar by {self.pricing_date}")
            return float(window["close"].iloc[-1])
        return float(frame["close"].iloc[-1])

    def as_of(self, symbol: str) -> str:
        import data as data_module

        frame = data_module.fetch(symbol, quiet=True)
        return str(frame.index[-1].date()) if len(frame) else "?"

    # -- account ----------------------------------------------------------
    def holdings(self) -> dict:
        return self.state["positions"]

    def cash(self) -> float:
        return float(self.state["cash"])

    def value(self) -> tuple[float, float]:
        """(equity, unrealised pnl) marked at the last cached close."""
        equity = self.cash()
        unrealised = 0.0
        for symbol, position in self.state["positions"].items():
            try:
                price = self.ltp(symbol)
            except Exception:                                   # noqa: BLE001
                price = position["avg"]
            equity += price * position["qty"]
            unrealised += (price - position["avg"]) * position["qty"]
        return equity, unrealised

    # -- trading ----------------------------------------------------------
    def send(self, symbol: str, side: str, quantity: int,
             limit: float | None = None) -> dict:
        """Fill immediately, crossing the spread and paying the charge sheet."""
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        side = side.upper()
        if side not in ("BUY", "SELL"):
            raise ValueError("side must be BUY or SELL")

        mid = limit if limit is not None else self.ltp(symbol)
        # Cross the spread in the direction that hurts - buys pay up, sells hit
        # down. A simulator that fills at mid is a simulator that always wins.
        edge = mid * self.slippage_bp / 10000.0
        price = mid + edge if side == "BUY" else mid - edge
        gross = price * quantity

        leg = costs_module._leg(gross, side.lower(), self.segment, 0.0)
        charges = leg.total

        positions = self.state["positions"]
        if side == "BUY":
            if gross + charges > self.cash():
                raise RuntimeError(
                    f"insufficient cash: need {gross + charges:,.0f}, "
                    f"have {self.cash():,.0f}")
            existing = positions.get(symbol, {"qty": 0, "avg": 0.0})
            total = existing["qty"] + quantity
            existing["avg"] = ((existing["avg"] * existing["qty"] + gross)
                               / total) if total else 0.0
            existing["qty"] = total
            positions[symbol] = existing
            self.state["cash"] -= gross + charges
        else:
            existing = positions.get(symbol)
            if not existing or existing["qty"] < quantity:
                raise RuntimeError(f"cannot sell {quantity} {symbol}: "
                                   f"hold {existing['qty'] if existing else 0}")
            existing["qty"] -= quantity
            if existing["qty"] == 0:
                positions.pop(symbol)
            else:
                positions[symbol] = existing
            self.state["cash"] += gross - charges

        record = {"time": now_ist().isoformat(timespec="seconds"),
                  "symbol": symbol, "side": side, "qty": quantity,
                  "price": round(price, 2), "charges": round(charges, 2),
                  "value": round(gross, 2)}
        self.state["trades"].append(record)
        self.save()
        return record

    def summary(self) -> str:
        equity, unrealised = self.value()
        opened = self.state.get("opened", equity)
        pnl = equity - opened
        return (f"paper | equity Rs {equity:,.0f} | cash Rs {self.cash():,.0f} | "
                f"{len(self.state['positions'])} positions | "
                f"P&L {pnl:+,.0f} ({pnl / opened:+.2%}) | "
                f"{len(self.state['trades'])} trades")


class GrowwBroker(Broker):
    """The Groww SDK. Real quotes, real holdings, real orders.

    UNTESTED AGAINST A LIVE ACCOUNT. The request shapes follow a working
    implementation, but no order from THIS file has ever been acknowledged by
    Groww, so the first real placement is the first test. Place one small order
    manually and check it in the app before trusting a rebalance to it.

    Orders are CNC limit orders. CNC because the book holds for twenty days and
    MIS would be auto-squared-off by the broker at the close. Limit rather than
    market because a market order on a stale price has no floor - the whole
    point of the price band in risk.py is that our prices can be days old, and
    a limit order that does not fill is a far better outcome than a market
    order that fills anywhere.
    """

    name = "groww"
    title = "Groww"
    live = True

    def __init__(self):
        self._client = None

    def usable(self) -> bool:
        import credentials

        return credentials.usable("groww")

    def explain(self) -> str:
        import credentials

        return credentials.explain("groww")

    def connect(self):
        """The SDK client, made once per process and kept.

        Re-authenticating per call would burn a TOTP code on every quote, and
        the codes only change every 30 seconds.
        """
        if self._client is not None:
            return self._client

        import credentials
        from growwapi import GrowwAPI

        values = credentials.effective("groww")

        if values.get("access_token"):
            self._client = GrowwAPI(values["access_token"])
            return self._client

        if not values.get("api_key"):
            raise RuntimeError(credentials.explain("groww"))

        if values.get("totp_secret"):
            import pyotp

            response = GrowwAPI.get_access_token(
                api_key=values["api_key"],
                totp=pyotp.TOTP(values["totp_secret"]).now())
        else:
            response = GrowwAPI.get_access_token(
                api_key=values["api_key"], secret=values["api_secret"])

        token = response.get("token") or response.get("access_token")
        if not token:
            raise RuntimeError(
                f"Groww returned no access token; keys: {list(response)}")
        self._client = GrowwAPI(token)
        return self._client

    def ltp(self, symbol: str) -> float:
        client = self.connect()
        response = client.get_ltp(
            exchange_trading_symbols=(f"NSE_{symbol}",),
            segment=client.SEGMENT_CASH)
        if isinstance(response, dict):
            for value in response.values():
                if isinstance(value, (int, float)) and value > 0:
                    return float(value)
        raise RuntimeError(f"no live price for {symbol}: {response!r}")

    def holdings(self) -> dict:
        client = self.connect()
        raw = client.get_holdings_for_user()
        rows = raw.get("holdings", raw) if isinstance(raw, dict) else raw
        out = {}
        for row in rows or []:
            symbol = (row.get("trading_symbol") or row.get("symbol") or "")
            quantity = int(row.get("quantity") or 0)
            if symbol and quantity:
                out[symbol] = {"qty": quantity,
                               "avg": float(row.get("average_price") or 0.0)}
        return out

    def cash(self) -> float:
        client = self.connect()
        margin = client.get_available_margin_details(
            segment=client.SEGMENT_CASH)
        for key in ("net_margin_available", "available_margin", "net_available"):
            if isinstance(margin, dict) and key in margin:
                return float(margin[key])
        raise RuntimeError(f"cannot read available cash: {margin!r}")

    def send(self, symbol: str, side: str, quantity: int,
             limit: float | None = None) -> dict:
        """Place one CNC limit order. Assumes risk.check already passed."""
        import hashlib

        client = self.connect()
        price = limit if limit is not None else self.ltp(symbol)
        # Derived from the order's own content plus the trading day, not from
        # time.time(): that was second-granularity, so two orders in the same
        # rebalance second collided on the same reference id. This is stable
        # for a retry of the SAME (day, symbol, side, quantity) - which is the
        # point, since it lets Groww's own duplicate handling, if any, recognise
        # a resend - while two different orders placed in the same second no
        # longer share an id. 3 + 6 + 11 = 20 characters, Groww's own limit.
        day = now_ist().strftime("%y%m%d")
        digest = hashlib.sha1(
            f"{day}|{symbol}|{side}|{int(quantity)}".encode()).hexdigest()[:11]
        request = {
            "trading_symbol": symbol,
            "quantity": int(quantity),
            "price": round(float(price), 2),
            "validity": client.VALIDITY_DAY,
            "exchange": client.EXCHANGE_NSE,
            "segment": client.SEGMENT_CASH,
            "product": client.PRODUCT_CNC,
            "order_type": client.ORDER_TYPE_LIMIT,
            "transaction_type": (client.TRANSACTION_TYPE_BUY if side == "BUY"
                                 else client.TRANSACTION_TYPE_SELL),
            "order_reference_id": f"MNT{day}{digest}",
        }
        return client.place_order(**request)

    def summary(self) -> str:
        try:
            positions = self.holdings()
            return (f"groww | {len(positions)} holdings | "
                    f"cash Rs {self.cash():,.0f}")
        except Exception as error:                              # noqa: BLE001
            return f"groww | not connected: {error}"


class LiveBroker(Broker):
    """A venue whose adapter has not been written. Declares, then refuses."""

    def __init__(self, name: str):
        self.name = name
        self.title = name.title()
        self.live = True

    def usable(self) -> bool:
        # Asked of credentials.py, never decided here. The rule for what counts
        # as configured differs per venue and must have exactly one home.
        import credentials

        return credentials.usable(self.name)

    def explain(self) -> str:
        import credentials

        detail = credentials.explain(self.name)
        if credentials.usable(self.name):
            return f"{detail} Adapter not implemented - MNT trades on paper."
        return detail

    def _refuse(self, *_args, **_kwargs):
        raise NotImplementedError(
            f"{self.name} adapter is not implemented. MNT trades on paper "
            f"only. Implement this deliberately, with a tested order path, "
            f"before pointing it at money.")

    ltp = holdings = cash = send = summary = _refuse


BROKERS = {"paper": PaperBroker, "groww": GrowwBroker,
           "angelone": lambda: LiveBroker("angelone")}
_INSTANCES: dict = {}


def broker(name: str | None = None) -> Broker:
    """The named venue, created once and reused."""
    name = name or os.environ.get("MNT_BROKER", "paper")
    if name not in BROKERS:
        raise ValueError(f"unknown broker {name!r}; have {sorted(BROKERS)}")
    if name not in _INSTANCES:
        _INSTANCES[name] = BROKERS[name]()
    return _INSTANCES[name]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--capital", type=float, default=500000.0)
    args = parser.parse_args()

    account = broker("paper")
    if args.reset:
        account.reset(args.capital)
        print(f"paper account reset to Rs {args.capital:,.0f}")

    print(account.summary())
    positions = account.holdings()
    if positions:
        print(f"\n{'symbol':<14}{'qty':>7}{'avg':>11}{'last':>11}{'pnl':>12}")
        print("-" * 55)
        for symbol, position in positions.items():
            try:
                last = account.ltp(symbol)
            except Exception:                                   # noqa: BLE001
                last = position["avg"]
            pnl = (last - position["avg"]) * position["qty"]
            print(f"{symbol:<14}{position['qty']:>7}{position['avg']:>11.2f}"
                  f"{last:>11.2f}{pnl:>+12,.0f}")

    print("\nlive venues:")
    for name in ("groww", "angelone"):
        venue = broker(name)
        print(f"  {name:<10}{'ready' if venue.usable() else venue.explain()}")


if __name__ == "__main__":
    main()
