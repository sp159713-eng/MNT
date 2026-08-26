"""The target, the score, and the one-line rules everything is measured against.

WHY THESE ARE NOT IN model.py

They were, and it made the boosted model import "the neural network" to find out
what it was predicting. None of this is specific to any estimator: the label is a
per-date rank, the score is a per-date correlation, and the baselines are sign
flips of two features. Whichever model is production, these stay fixed - which is
the whole point, because a comparison in which the target moved is not a
comparison.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import config


def rank_target(panel: pd.DataFrame) -> np.ndarray:
    """Forward excess return -> per-date rank in [-1, 1]. The training label.

    A rank rather than the return itself, for two reasons:

      Fat tails. One name up 40% on an earnings gap would dominate the squared
      error for its whole date, and the model would spend capacity memorising an
      event nothing could have predicted. Ranks bound any single row's
      contribution to the loss.

      Relevance. The book buys the top six. It never asks how much a stock will
      return, only whether it beats the other twenty-nine.
    """
    ranked = panel.groupby("timestamp")["target_excess"].rank(pct=True)
    return ((ranked - 0.5) * 2.0).to_numpy(dtype=np.float32)


def rank_ic(predictions: np.ndarray, targets: np.ndarray,
            dates: np.ndarray) -> tuple[float, float, int]:
    """Mean per-date Spearman IC, its t-statistic, and the number of dates.

    Pooling every row into one correlation would be wrong twice over: it lets a
    good year and a bad year average into a number describing neither, and it
    treats 30 names on one date as 30 independent observations when they are
    closer to one. Correlating within each date and then averaging across dates
    is the standard fix, and it is what makes the t-statistic mean anything.
    """
    frame = pd.DataFrame({"pred": predictions, "target": targets, "date": dates})
    per_date = frame.groupby("date").apply(
        lambda g: g["pred"].corr(g["target"], method="spearman"),
        include_groups=False).dropna()
    if len(per_date) < 2:
        return 0.0, 0.0, len(per_date)

    # Overlapping windows: a 20-day target shares 19 days with the next date's,
    # so consecutive ICs are not independent and the naive standard error is too
    # small by roughly sqrt(horizon).
    effective = max(len(per_date) / config.TARGET_HORIZON, 1.0)
    t_stat = float(per_date.mean() / (per_date.std(ddof=1) / np.sqrt(effective)))
    return float(per_date.mean()), t_stat, len(per_date)


def baselines(panel: pd.DataFrame) -> dict[str, np.ndarray]:
    """One-line rules any model has to beat to justify existing.

    All three are sign flips or straight copies of existing features, so they
    cost nothing to compute and they are exactly the structures a model is most
    likely to have rediscovered on its own. A model that does not clear them has
    not earned its complexity, whatever its IC was.
    """
    return {
        "momentum (mom_126)": panel["mom_126"].to_numpy(),
        "reversal (-rev_5)": -panel["rev_5"].to_numpy(),
        "long-horizon (mom_252)": panel["mom_252"].to_numpy(),
    }


def accuracy(predictions: np.ndarray, targets: np.ndarray,
             dates: np.ndarray) -> tuple[float, float, int]:
    return rank_ic(predictions, targets, dates)


def edge_bp(pooled: dict, baseline_stats: dict) -> tuple[float, str]:
    usable = {name: stats for name, stats in (baseline_stats or {}).items()
              if isinstance(stats, dict) and "net_excess_bp" in stats}
    if not usable:
        return 0.0, ""
    best = max(usable, key=lambda name: usable[name]["net_excess_bp"])
    return (float(pooled["net_excess_bp"]) -
            float(usable[best]["net_excess_bp"])), best
