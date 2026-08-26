"""What a round trip on NSE cash equity actually costs, itemised.

WHY THIS EXISTS

NNS charged a flat 25bp round trip and swept it. That is the right instinct and
the wrong instrument. A single percentage is blind to the two things that decide
whether a small book can trade at all:

  Fixed charges do not scale down. Brokerage is capped at Rs 20 per order and DP
  is a flat Rs 15.34 per scrip on every delivery sell. On a Rs 5,000 trade those
  two alone are 71bp. On a Rs 5,00,000 trade they are 0.7bp. The same strategy
  is unprofitable and profitable at the two ends, and a flat bp number cannot
  tell you which end you are on.

  Delivery and intraday are different taxes, not different rates. STT on
  delivery is 0.1% charged on BOTH legs; intraday is 0.025% on the SELL leg
  only. Squaring off the same day is roughly 15bp cheaper in STT before anything
  else is counted. Whether your rules hold overnight is therefore a cost
  decision as much as a signal decision.

WHAT IS INCLUDED

Brokerage, STT, exchange transaction charges, SEBI turnover fees, stamp duty,
GST on the taxable components, and DP charges on delivery sells. That is the
whole statutory schedule.

WHAT IS NOT, AND WHY IT USUALLY DOMINATES

Spread and impact. Crossing the book on a liquid NIFTY 50 name costs a couple of
basis points; on a smallcap with a 40 paise touch on a Rs 200 stock it is 20bp
before you have moved anything. Statutory charges are knowable and fixed, so
they are computed here exactly. Execution cost is a property of YOUR order in
YOUR name at YOUR size, so it is passed in as `slippage_bp` and defaults to
something deliberately non-zero. Leaving it at zero does not make it zero.

THE RATES GO STALE

Every rate below is a policy number that has been changed before and will be
changed again - exchange transaction charges were revised in Oct 2024, STT on
options in Oct 2024, stamp duty unified in Jul 2020. They are constants at the
top of this file for exactly that reason. Check them against your broker's live
charge sheet before you trust a backtest, not after.

Run with:  py -3.13 costs.py
           py -3.13 costs.py --value 25000 --segment intraday
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# The rate schedule. NSE equity cash segment, retail discount broker.
# Verify against your broker before use. Rates as published 2024-2025.
# ---------------------------------------------------------------------------

BROKERAGE_PCT = 0.0003          # 0.03% intraday, subject to the cap below
BROKERAGE_CAP = 20.0            # rupees per executed order
BROKERAGE_DELIVERY = 0.0        # discount brokers charge nothing for delivery

STT_DELIVERY = 0.001            # 0.1%, charged on BOTH buy and sell
STT_INTRADAY_SELL = 0.00025     # 0.025%, charged on the sell leg only

EXCHANGE_TXN = 0.0000297        # 0.00297% of turnover, both legs (NSE)
SEBI_TURNOVER = 0.000001        # Rs 10 per crore
STAMP_DELIVERY_BUY = 0.00015    # 0.015% on the buy leg
STAMP_INTRADAY_BUY = 0.00003    # 0.003% on the buy leg
GST = 0.18                      # on brokerage + exchange txn + SEBI fees

DP_CHARGE = 15.34               # flat, per scrip, per day, on delivery SELLS

DEFAULT_SLIPPAGE_BP = 5.0       # half-spread crossed on each leg, liquid name


@dataclass
class Leg:
    """One side of a trade, with every charge kept separate.

    Nothing is netted here. The point of the breakdown is to see which line is
    actually killing the strategy, because the fix differs: high STT means stop
    holding overnight, high brokerage means trade bigger, high slippage means
    trade something more liquid.
    """

    value: float
    brokerage: float = 0.0
    stt: float = 0.0
    exchange: float = 0.0
    sebi: float = 0.0
    stamp: float = 0.0
    gst: float = 0.0
    dp: float = 0.0
    slippage: float = 0.0

    @property
    def total(self) -> float:
        return (self.brokerage + self.stt + self.exchange + self.sebi
                + self.stamp + self.gst + self.dp + self.slippage)

    def items(self) -> list[tuple[str, float]]:
        return [("brokerage", self.brokerage), ("STT", self.stt),
                ("exchange", self.exchange), ("SEBI", self.sebi),
                ("stamp duty", self.stamp), ("GST", self.gst),
                ("DP", self.dp), ("slippage", self.slippage)]


@dataclass
class RoundTrip:
    """A buy and a matching sell, and what the pair cost together."""

    buy: Leg
    sell: Leg
    segment: str

    @property
    def total(self) -> float:
        return self.buy.total + self.sell.total

    @property
    def basis_points(self) -> float:
        """Cost as bp of the position's value - the number a backtest charges.

        Measured against the BUY value, i.e. the capital actually committed. A
        strategy earning `x` bp per round trip is profitable only if x exceeds
        this, which is the entire question and the reason this file exists.
        """
        return self.total / self.buy.value * 10000 if self.buy.value else 0.0


def _leg(value: float, side: str, segment: str, slippage_bp: float) -> Leg:
    """Charge one leg. `side` is "buy" or "sell", `segment` is delivery/intraday."""
    leg = Leg(value=value)
    intraday = (segment == "intraday")

    if intraday:
        leg.brokerage = min(value * BROKERAGE_PCT, BROKERAGE_CAP)
    else:
        leg.brokerage = BROKERAGE_DELIVERY

    if intraday:
        # Sell leg only. This is the single biggest reason intraday is cheaper.
        leg.stt = value * STT_INTRADAY_SELL if side == "sell" else 0.0
    else:
        leg.stt = value * STT_DELIVERY

    leg.exchange = value * EXCHANGE_TXN
    leg.sebi = value * SEBI_TURNOVER

    if side == "buy":
        leg.stamp = value * (STAMP_INTRADAY_BUY if intraday else STAMP_DELIVERY_BUY)

    # GST rides on the broker's and the exchange's cut, never on STT or stamp.
    leg.gst = (leg.brokerage + leg.exchange + leg.sebi) * GST

    # Demat debit, so it lands only when shares actually leave the account.
    if side == "sell" and not intraday:
        leg.dp = DP_CHARGE

    leg.slippage = value * slippage_bp / 10000.0
    return leg


def round_trip(buy_value: float, sell_value: float | None = None,
               segment: str = "delivery",
               slippage_bp: float = DEFAULT_SLIPPAGE_BP) -> RoundTrip:
    """Cost of buying `buy_value` worth and selling it back out.

    `sell_value` defaults to the buy value. Passing the real exit value matters
    only at the margin - charges scale with turnover, so a position that doubled
    pays more on the way out - but it keeps the arithmetic honest.
    """
    if segment not in ("delivery", "intraday"):
        raise ValueError(f"segment must be delivery or intraday, got {segment!r}")
    sell_value = buy_value if sell_value is None else sell_value
    return RoundTrip(
        buy=_leg(buy_value, "buy", segment, slippage_bp),
        sell=_leg(sell_value, "sell", segment, slippage_bp),
        segment=segment,
    )


def cost_bp(trade_value: float, segment: str = "delivery",
            slippage_bp: float = DEFAULT_SLIPPAGE_BP) -> float:
    """Round-trip cost in bp for a position of this size. The backtest hook."""
    return round_trip(trade_value, segment=segment, slippage_bp=slippage_bp).basis_points


def breakeven_move_pct(trade_value: float, segment: str = "delivery",
                       slippage_bp: float = DEFAULT_SLIPPAGE_BP) -> float:
    """How far the stock must move, in percent, just to get your money back."""
    return cost_bp(trade_value, segment, slippage_bp) / 100.0


def _print_breakdown(trip: RoundTrip) -> None:
    print(f"  {'charge':<12}{'buy':>12}{'sell':>12}{'total':>12}")
    print("  " + "-" * 48)
    for (name, buy_amount), (_, sell_amount) in zip(trip.buy.items(), trip.sell.items()):
        if buy_amount or sell_amount:
            print(f"  {name:<12}{buy_amount:>12.2f}{sell_amount:>12.2f}"
                  f"{buy_amount + sell_amount:>12.2f}")
    print("  " + "-" * 48)
    print(f"  {'TOTAL':<12}{trip.buy.total:>12.2f}{trip.sell.total:>12.2f}"
          f"{trip.total:>12.2f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--value", type=float, default=100000.0,
                        help="rupee value of one position")
    parser.add_argument("--segment", choices=("delivery", "intraday"),
                        default="delivery")
    parser.add_argument("--slippage", type=float, default=DEFAULT_SLIPPAGE_BP,
                        help="bp of spread/impact crossed per leg")
    args = parser.parse_args()

    trip = round_trip(args.value, segment=args.segment, slippage_bp=args.slippage)
    print(f"\nRs {args.value:,.0f} position, {args.segment}, "
          f"{args.slippage:.0f}bp slippage per leg\n")
    _print_breakdown(trip)
    print(f"\n  round trip = {trip.basis_points:.1f} bp "
          f"= {breakeven_move_pct(args.value, args.segment, args.slippage):.2f}% "
          f"move just to break even\n")

    # The whole reason a flat bp figure misleads: fixed charges dominate small
    # trades. Anyone sizing positions off a percentage cost needs to see this.
    print("cost in bp by position size, both segments, same slippage:\n")
    print(f"  {'position':>12}{'delivery':>12}{'intraday':>12}{'fixed share':>14}")
    print("  " + "-" * 50)
    for value in (5000, 10000, 25000, 50000, 100000, 250000, 500000, 1000000):
        delivery = cost_bp(value, "delivery", args.slippage)
        intraday = cost_bp(value, "intraday", args.slippage)
        # How much of the delivery cost is charges that do not scale with size.
        trip_d = round_trip(value, segment="delivery", slippage_bp=args.slippage)
        fixed = trip_d.sell.dp + trip_d.buy.brokerage + trip_d.sell.brokerage
        print(f"  {value:>12,}{delivery:>11.1f}{intraday:>11.1f}"
              f"{fixed / trip_d.total:>13.0%}")

    print("\n  Delivery pays STT on both legs and a flat DP fee on the sell.")
    print("  Intraday pays STT on the sell only, but pays brokerage on both.")
    print("  Below roughly Rs 25,000 a position, fixed charges decide the")
    print("  outcome and no signal is strong enough to matter.\n")


if __name__ == "__main__":
    main()
