"""How much to put in each name, and whether to trade at all.

TWO DIFFERENT IDEAS, BOTH CALLED "CONFIDENCE"

  Sizing. The model scores INFY at 0.0875 and TCS at 0.0425. Should INFY get
  twice the money? That is confidence as a WEIGHT.

  Gating. On some dates the top six barely separate from the average name. Is
  that a date worth paying 34bp to rebalance into? That is confidence as a
  TRIGGER.

They are not the same bet and they do not have the same prior. NNS measured
weighting directly and found equal weight beat every model-based sizing rule it
tried; the model's only durable edge there was lower turnover. Gating is the
turnover idea, so it starts from a better place - a skipped rebalance is a
guaranteed 34bp saved against an uncertain gain.

WHY EQUAL WEIGHT IS SO HARD TO BEAT

A score is a ranking device, not a magnitude. Nothing in the training objective
asks the model to make 0.0875 mean "twice as good as 0.0425" - the label is a
per-date rank, and any monotone transform of the scores gives the same book.
Weighting by a number the loss never calibrated is reading precision into it
that was never fitted. Equal weight ignores the magnitudes precisely because
they were never trained to carry information.

That is an argument, though, not a measurement. Every scheme below is run
through the same walk-forward harness so the question gets an answer.

Run with:  py -3.13 sizing.py         compare the schemes on the test split
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def equal(frame: pd.DataFrame, chosen: list[str]) -> pd.Series:
    """1/k in each name. The baseline everything else has to beat."""
    if not chosen:
        return pd.Series(dtype=float)
    return pd.Series(1.0 / len(chosen), index=chosen)


def score_proportional(frame: pd.DataFrame, chosen: list[str]) -> pd.Series:
    """Weight by score above the date's mean, normalised to sum to one.

    The literal reading of "size by confidence". Its weakness is that the scores
    are not calibrated magnitudes - see the module docstring - and that a single
    high score can concentrate most of the book in one name on a date where the
    model happens to be emphatic.
    """
    if not chosen:
        return pd.Series(dtype=float)
    scores = frame.set_index("symbol")["pred"].reindex(chosen)
    edge = (scores - frame["pred"].mean()).clip(lower=0.0)
    total = edge.sum()
    if not np.isfinite(total) or total <= 0:
        return equal(frame, chosen)
    return edge / total


def rank_linear(frame: pd.DataFrame, chosen: list[str]) -> pd.Series:
    """Linearly decaying weights by rank: the best name gets k units, the last 1.

    A middle course. It uses the ORDER, which the model was actually trained to
    produce, and ignores the magnitudes, which it was not. If confidence sizing
    helps at all, this is the version most likely to show it.
    """
    if not chosen:
        return pd.Series(dtype=float)
    k = len(chosen)
    units = pd.Series(np.arange(k, 0, -1, dtype=float), index=chosen)
    return units / units.sum()


def inverse_vol(frame: pd.DataFrame, chosen: list[str]) -> pd.Series:
    """Weight by 1 / recent volatility. Not confidence - a risk control.

    Included as a control of a different kind: if any non-equal scheme wins, it
    matters whether it won for a reason connected to the model at all. This one
    has nothing to do with the score.
    """
    if not chosen:
        return pd.Series(dtype=float)
    vol = frame.set_index("symbol")["vol_21"].reindex(chosen)
    # vol_21 is a cross-sectional rank in [-1, 1]; shift it into a positive band.
    scale = 1.0 / (vol + 1.5)
    total = scale.sum()
    if not np.isfinite(total) or total <= 0:
        return equal(frame, chosen)
    return scale / total


SCHEMES = {
    "equal": equal,
    "score": score_proportional,
    "rank": rank_linear,
    "invvol": inverse_vol,
}


def spread(frame: pd.DataFrame, chosen: list[str]) -> float:
    """How far the chosen names sit above the average name, in score units.

    The gating statistic. Expressed relative to the cross-sectional standard
    deviation of scores on that date, so it is comparable across dates and
    across models - a raw score gap means nothing when one date's scores span
    0.01 and another's span 0.2.
    """
    if not chosen or "pred" not in frame:
        return 0.0
    scores = frame["pred"]
    deviation = scores.std()
    if not np.isfinite(deviation) or deviation <= 0:
        return 0.0
    picked = frame.set_index("symbol")["pred"].reindex(chosen).mean()
    return float((picked - scores.mean()) / deviation)


def weights(frame: pd.DataFrame, chosen: list[str], scheme: str = "equal"
            ) -> pd.Series:
    if scheme not in SCHEMES:
        raise ValueError(f"unknown sizing {scheme!r}; have {sorted(SCHEMES)}")
    result = SCHEMES[scheme](frame, chosen)
    return result.fillna(0.0) if len(result) else result
