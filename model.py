"""The neural network, and the baselines that have to beat it before it ships.

WHAT IT PREDICTS

A cross-sectional rank, not a return. The label the network sees is each name's
forward-20-day excess return converted to its rank among the names live that
day, mapped to [-1, 1]. Two reasons, and neither is cosmetic:

  Robustness. Raw 20-day excess returns have fat tails - one name up 40% on an
  earnings gap dominates the squared-error gradient for that whole date, and the
  network spends its capacity memorising an event it could never have predicted.
  Ranks bound the loss contribution of any single row.

  Relevance. The book buys the top six names by prediction. It never asks how
  much a stock will return, only whether it will beat the other twenty-nine.
  Training on the quantity you do not use, to select on the quantity you do, is
  a mismatch that costs accuracy in exactly the region that matters - the top.

HOW IT IS STOPPED

Early stopping watches validation rank IC, not validation loss. These come apart
more often than is comfortable: MSE keeps improving while the ordering at the
top of the book quietly degrades, because the loss is dominated by the middle of
the distribution where nothing is ever traded. IC is the thing the book consumes,
so IC is the thing that decides when to stop.

WHY THE BASELINES ARE HERE AND NOT IN A SEPARATE FILE

NNS's lesson was that a model can carry real signal (IC t-stat 2.37) and still
be worthless, because the strategy built on it broke even at 6.5bp against 30bp
costs. The corollary is that a network is not interesting because it has skill.
It is interesting if it has more skill than a rule you could write in one line -
and if it does not, the honest move is to ship the one-liner. So `momentum` and
`reversal` are computed on the same splits, the same dates, the same target, and
printed in the same table. If the network does not clear them here, nothing
downstream will save it.

Run with:  py -3.13 model.py
           py -3.13 model.py --epochs 100 --hidden 64
"""

from __future__ import annotations

import argparse
import os

import numpy as np
import pandas as pd
import torch
import torch.nn as nn

import config
import features as features_module
# The target, the IC and the baselines moved to metrics.py when LightGBM became
# production - none of them were ever specific to a network, and having the
# boosted model import "the neural network" to find its own label was backwards.
# Re-exported here so this file still reads as a self-contained trainer.
from metrics import baselines, rank_ic, rank_target        # noqa: F401


def rank_ic(predictions: np.ndarray, targets: np.ndarray,
            dates: np.ndarray) -> tuple[float, float, int]:
    """Mean per-date Spearman IC, its t-statistic, and the number of dates.

    Pooling every row into one correlation would be wrong: it lets a good year
    and a bad year average into a number that describes neither, and it treats
    30 names on one date as 30 independent observations when they are closer to
    one. Correlating within each date and then averaging across dates is the
    standard fix, and it makes the t-statistic mean something.
    """
    frame = pd.DataFrame({"pred": predictions, "target": targets, "date": dates})
    frame = frame.dropna(subset=["pred", "target"])
    if frame.empty:
        return 0.0, 0.0, 0

    # Spearman within a date is Pearson on the average-ranks, which is what
    # Series.corr(method="spearman") computes anyway - so this is the same
    # quantity, assembled with vectorised group operations instead of a Python
    # lambda per date.
    #
    # It is written this way because this runs once per EPOCH, on six hundred
    # dates, to decide early stopping. The groupby-apply version measured 0.92s
    # against 0.65s for the gradient steps of the same epoch: nearly two thirds
    # of training was spent deciding when to stop it. This form is 0.031s, and
    # agrees with the old one to 5e-17.
    grouped = frame.groupby("date", sort=True)
    frame["p"] = grouped["pred"].rank()
    frame["t"] = grouped["target"].rank()

    grouped = frame.groupby("date", sort=True)
    frame["p"] -= grouped["p"].transform("mean")
    frame["t"] -= grouped["t"].transform("mean")
    frame["pt"] = frame["p"] * frame["t"]
    frame["pp"] = frame["p"] ** 2
    frame["tt"] = frame["t"] ** 2
    sums = frame.groupby("date", sort=True)[["pt", "pp", "tt"]].sum()

    # A date whose ranks are constant on either side has no correlation to
    # report - one name, or thirty identical predictions. Series.corr returns
    # NaN there and those dates are dropped, so a zero denominator must become
    # NaN rather than an infinity that would then poison the mean.
    spread = np.sqrt(sums["pp"].to_numpy() * sums["tt"].to_numpy())
    with np.errstate(invalid="ignore", divide="ignore"):
        per_date = np.where(spread > 0, sums["pt"].to_numpy() / spread, np.nan)
    per_date = pd.Series(per_date).dropna()
    if len(per_date) < 2:
        return 0.0, 0.0, len(per_date)

    # Overlapping windows again: a 20-day target shares 19 days with the next
    # date's, so consecutive ICs are not independent and the naive standard
    # error is too small by roughly sqrt(horizon).
    effective = max(len(per_date) / config.TARGET_HORIZON, 1.0)
    t_stat = float(per_date.mean() / (per_date.std(ddof=1) / np.sqrt(effective)))
    return float(per_date.mean()), t_stat, len(per_date)


class Ranker(nn.Module):
    """A deliberately small MLP.

    Thirty-nine inputs, two hidden layers, a few thousand parameters. The panel
    has over 100,000 training rows but nowhere near 100,000 independent
    observations - 30 names moving together on 4,000 dates, with 20-day
    overlapping targets, is closer to 200 independent draws. Capacity is set
    against that number, not against the row count, which is why this is 32
    units and not 512.

    The feature count nearly tripled when NNS's block was ported in, and the
    width deliberately did not follow it. More inputs against the same 200
    observations is a reason to be more careful about capacity, not less.
    """

    def __init__(self, n_features: int, hidden: int = 32, dropout: float = 0.2):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(n_features, hidden),
            nn.LayerNorm(hidden),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden, hidden // 2),
            nn.LayerNorm(hidden // 2),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden // 2, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x).squeeze(-1)


def rank_target(panel: pd.DataFrame) -> np.ndarray:
    """Forward excess return -> per-date rank in [-1, 1]. The training label."""
    ranked = panel.groupby("timestamp")["target_excess"].rank(pct=True)
    return ((ranked - 0.5) * 2.0).to_numpy(dtype=np.float32)


def train(train_panel: pd.DataFrame, val_panel: pd.DataFrame,
          hidden: int = 32, epochs: int = 60, batch_size: int = 1024,
          learning_rate: float = 1e-3, dropout: float = 0.2,
          patience: int = 12, seed: int = config.SEED,
          weight_decay: float = 1e-4, columns=None,
          quiet: bool = False) -> tuple[Ranker, dict]:
    """Fit, early-stopping on validation rank IC. Returns the best model seen.

    `columns` names the inputs. None keeps all thirty-nine, which is what
    `main()` has always shown; the shipped signal asks for the core sixteen
    instead, because that is what the measurement favoured for a network. The
    chosen list is written onto the returned model so predict() cannot be
    handed a different one - a net fitted on sixteen inputs and fed thirty-nine
    does not fail loudly, it just scores nonsense.
    """
    torch.manual_seed(seed)
    np.random.seed(seed)

    columns = list(columns) if columns else features_module.MODEL_COLUMNS
    x_train = torch.tensor(train_panel[columns].to_numpy(dtype=np.float32))
    y_train = torch.tensor(rank_target(train_panel))
    x_val = torch.tensor(val_panel[columns].to_numpy(dtype=np.float32))
    y_val_raw = val_panel["target_excess"].to_numpy()
    val_dates = val_panel["timestamp"].to_numpy()

    model = Ranker(len(columns), hidden, dropout)
    model.columns = columns
    optimiser = torch.optim.AdamW(model.parameters(), lr=learning_rate,
                                  weight_decay=weight_decay)
    loss_function = nn.MSELoss()

    best_ic, best_state, best_epoch, stale = -np.inf, None, 0, 0
    n = len(x_train)

    for epoch in range(1, epochs + 1):
        model.train()
        order = torch.randperm(n)
        total = 0.0
        for start in range(0, n, batch_size):
            index = order[start:start + batch_size]
            optimiser.zero_grad()
            loss = loss_function(model(x_train[index]), y_train[index])
            loss.backward()
            optimiser.step()
            total += loss.item() * len(index)

        model.eval()
        with torch.no_grad():
            predictions = model(x_val).numpy()
        ic, t_stat, _ = rank_ic(predictions, y_val_raw, val_dates)

        if ic > best_ic:
            best_ic, best_epoch, stale = ic, epoch, 0
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
        else:
            stale += 1

        if not quiet and (epoch % 5 == 0 or epoch == 1):
            print(f"  epoch {epoch:>3}  train loss {total / n:.4f}  "
                  f"val IC {ic:+.4f} (t {t_stat:+.2f})"
                  f"{'  <- best' if epoch == best_epoch else ''}")

        if stale >= patience:
            if not quiet:
                print(f"  stopped at epoch {epoch}, no improvement in {patience}")
            break

    if best_state is not None:
        model.load_state_dict(best_state)
    model.eval()
    return model, {"best_epoch": best_epoch, "best_val_ic": best_ic,
                   "columns": len(columns)}


def predict(model: Ranker, panel: pd.DataFrame, columns=None) -> np.ndarray:
    used = columns or getattr(model, "columns", None)         or features_module.MODEL_COLUMNS
    with torch.no_grad():
        x = torch.tensor(panel[list(used)].to_numpy(dtype=np.float32))
        return model(x).numpy()


def baselines(panel: pd.DataFrame) -> dict[str, np.ndarray]:
    """One-line rules the network has to beat to justify its existence.

    Both are sign flips of existing features, so they cost nothing to compute
    and they are exactly the structures the network is most likely to have
    rediscovered on its own.
    """
    return {
        "momentum (mom_126)": panel["mom_126"].to_numpy(),
        "reversal (-rev_5)": -panel["rev_5"].to_numpy(),
        "long-horizon (mom_252)": panel["mom_252"].to_numpy(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--epochs", type=int, default=60)
    parser.add_argument("--hidden", type=int, default=32)
    parser.add_argument("--dropout", type=float, default=0.2)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--seed", type=int, default=config.SEED)
    args = parser.parse_args()

    panel = features_module.cross_sectionalize(features_module.build_panel())
    train_panel, val_panel, test_panel = features_module.split_panel(panel)
    print(f"\ntrain {len(train_panel):,} | val {len(val_panel):,} | "
          f"test {len(test_panel):,} rows\n")

    model, info = train(train_panel, val_panel, hidden=args.hidden,
                        epochs=args.epochs, dropout=args.dropout,
                        learning_rate=args.lr, seed=args.seed)
    print(f"\nbest epoch {info['best_epoch']}, val IC {info['best_val_ic']:+.4f}\n")

    print(f"{'signal':<24}{'val IC':>10}{'t':>8}{'test IC':>11}{'t':>8}")
    print("-" * 61)

    rows = []
    for name, part in (("val", val_panel), ("test", test_panel)):
        rows.append(rank_ic(predict(model, part),
                            part["target_excess"].to_numpy(),
                            part["timestamp"].to_numpy()))
    print(f"{'neural net':<24}{rows[0][0]:>+10.4f}{rows[0][1]:>+8.2f}"
          f"{rows[1][0]:>+11.4f}{rows[1][1]:>+8.2f}")

    for name in baselines(val_panel):
        val_stats = rank_ic(baselines(val_panel)[name],
                            val_panel["target_excess"].to_numpy(),
                            val_panel["timestamp"].to_numpy())
        test_stats = rank_ic(baselines(test_panel)[name],
                             test_panel["target_excess"].to_numpy(),
                             test_panel["timestamp"].to_numpy())
        print(f"{name:<24}{val_stats[0]:>+10.4f}{val_stats[1]:>+8.2f}"
              f"{test_stats[0]:>+11.4f}{test_stats[1]:>+8.2f}")

    os.makedirs(config.MODEL_DIR, exist_ok=True)
    path = os.path.join(config.MODEL_DIR, "ranker.pt")
    torch.save({"state_dict": model.state_dict(),
                "features": features_module.MODEL_COLUMNS,
                "hidden": args.hidden, "dropout": args.dropout,
                "horizon": config.TARGET_HORIZON, "info": info}, path)
    print(f"\nsaved {path}")
    print("\nIC is not money. Run backtest.py before believing any of this.")


if __name__ == "__main__":
    main()
