# MNT

An NSE equity book: rank thirty large caps, hold six, rebalance monthly, and pay
the real charge sheet for it.

This is the project that gets used. NNS is where things get tried.

## The state of it, honestly

Measured across 14 walk-forward folds (2013-2026), refit each year, configuration
chosen on validation and test read once:

| | excess per period | t | break-even | net/yr |
|---|---|---|---|---|
| **LightGBM** (production) | **+46bp** | **1.76** | **207.7bp** | 27.0% |
| neural net | +23bp | 0.97 | 117.8bp | 23.4% |
| TabPFN-2.5 | -8bp | -0.28 | 16.9bp | 18.7% |
| momentum, one line | +5bp | 0.17 | 61.1bp | - |

Benchmark - equal weight all thirty, never traded - is 19.9%/yr.

**t = 1.76 is not significance.** It needs 2.0, and this universe cannot get
there: reaching it would take roughly 26 years of history against the 21
available. The edge is real enough to act on or not act on as a judgement call;
it is not established.

Two more things that are true and easy to forget:

- The universe is thirty names that are liquid large caps *today*, traded back to
  2013. Absolute returns are inflated by that. The excess figure largely cancels
  it, since the benchmark holds the same survivors.
- The order-level simulation is harsher than the weight-space backtest, because
  whole shares, a finite cash balance and per-order fixed charges all bite.

## Running it

    py -3.13 gui.py                     the application
    py -3.13 walkforward.py             the honest edge estimate
    py -3.13 production.py              refit the traded model
    py -3.13 production.py --picks      today's book
    py -3.13 orders.py                  the rebalance plan, places nothing
    py -3.13 orders.py --execute        apply it on paper
    py -3.13 paper.py --days 252        replay a year order by order
    py -3.13 runs.py                    every run ever recorded
    py -3.13 walkforward.py --signal nn the same estimate for the network
    py -3.13 costs.py                   what a round trip actually costs
    py -3.13 risk.py                    live-trading gate status

## The modules

**Data and features** - `data.py` caches NSE bars and strips bad prints;
`features.py` builds 39 cross-sectionally ranked features plus two market-context
columns; `config.py` holds every choice that is a choice.

**Models** - `signals.py` is the pluggable interface (lightgbm / nn / tabpfn),
`boosting.py` the LightGBM block, `model.py` the PyTorch MLP, `metrics.py` the
target and scoring shared by all of them, `production.py` the one that trades.

**Evaluation** - `backtest.py` runs a book in weight space, `walkforward.py`
refits and re-tests every year, `sizing_test.py` compares position-sizing rules,
`sizing.py` implements them. `runs.py` is the append-only log both
`walkforward.py` and `paper.py` write to, so a result outlives the terminal it
was printed in; walk-forward rows and order-level replays are stored together
and printed as separate tables, because one equity path has no t-statistic and
must not be read under a column that implies it does.

**Trading** - `costs.py` is the itemised NSE charge sheet, `broker.py` the venue
interface and paper account, `orders.py` turns a target book into orders,
`risk.py` gates live placement, `paper.py` replays history order by order,
`credentials.py` stores API keys outside the repo.

**Context** - `news.py` headlines and a crude score, `pulse.py` dispersion and
breadth. Neither feeds the model; both are dashboards.

**Interface** - `gui.py`, `pages.py`, `theme.py`. Ten tabs, tkinter, no
matplotlib.

## Switching the model

Settings has a Model card. It writes the choice to `artifacts/ui.json`, so it
survives a restart, and `config.PRODUCTION_SIGNAL` reads it at import.

The default stays LightGBM because that is what the measurement chose.
Switching is an experiment being run, not a new default.

Changing it does not refit anything, so the saved `production.joblib` and the
configured signal will disagree until "Refit now" is pressed. `production.load()`
refuses that state outright rather than serving predictions from a model the
operator believes was replaced - Book, Orders and Sim will say so instead of
quietly trading the wrong one.

## Live trading

Disarmed. `risk.py` refuses every live order unless armed, arming expires on its
own, and there are per-order, per-day, universe and price-band limits plus a
`artifacts/HALT` kill switch. Set keys on the Venues tab, `MNT_BROKER=groww`,
then arm.

**The Groww adapter has never placed a real order.** Its request shapes follow a
working implementation, but the first live placement will be the first test.
Place one small order by hand and confirm it in the app before trusting a
rebalance to it.

## Things that were tried and lost

Recorded so they are not retried by accident.

- **Sizing by model confidence** - equal weight beat score-proportional by 15bp
  and inverse-vol by 41bp. Scores drift between rebalances, so weighting by them
  churns money between names already held. The model is trained on a per-date
  rank; the magnitudes were never calibrated.
- **A confidence gate** on whether to rebalance at all - looked outstanding swept
  on test (t 1.92), gave t 1.10 when the threshold was chosen on validation.
- **TabPFN-2.5** - the only model tested that lost to the benchmark, at roughly
  300x the compute of the GBM.
- **More features** - the 39-feature set scored below the original 14 for the
  neural net. About 200 effectively independent observations cannot support 39
  inputs.
- **A narrower net on the core 16 columns** (2026-08-20, run 4) - won on
  validation at rank IC +0.1097 against +0.1028, then scored **-9bp, t -0.44,
  break-even 5.1bp** on the 14 folds against +23bp for the 39-column net. The
  validation gap was 0.007 while one configuration's own spread across seeds
  reached 0.014, so validation never actually preferred it. Selecting inside
  that noise is the same mistake as selecting on test, and it cost the same.

## Licence

Copyright (c) 2026 sp159713-eng

MNT is free software: you may redistribute and modify it under the terms of the
GNU Affero General Public License, version 3, as published by the Free Software
Foundation. The full text is in [LICENSE](LICENSE).

The Affero clause is the operative one: if you run a modified MNT as a network
service, you must offer that service's users the modified source. A closed fork
behind a web front end is exactly what this licence exists to prevent.

## No warranty, and no advice

MNT places real orders against a real broker. It is distributed WITHOUT ANY
WARRANTY, without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE - see the licence for the full terms.

Nothing here is investment advice. Every measurement in this README was taken
on historical NSE data by its author, and past results do not establish future
ones. The defaults ship in paper mode with live orders gated behind an explicit
`--live` flag and an armed risk check; if you remove those gates, the
consequences are yours.
