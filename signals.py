"""Pluggable signal generators, so the harness scores the model and not the wiring.

WHY THIS INTERFACE EXISTS

The turnover machinery in backtest.py - trailing smoothing plus the hold buffer -
is applied after prediction and knows nothing about where the prediction came
from. That is the whole reason a bake-off is cheap here: swap the thing that
produces the ranking, keep every other line identical, and the difference in
break-even is attributable to the model rather than to the plumbing.

Each signal exposes the same two calls:

    signal.fit(train_panel, val_panel)      -> None
    signal.predict(panel)                   -> np.ndarray, higher is better

WHAT TABPFN IS DOING DIFFERENTLY

Nothing is trained. TabPFN is a transformer pre-trained on millions of synthetic
tabular tasks; `fit` just hands it a context and `predict` runs in-context
learning in a single forward pass. That is why it is interesting for this
problem: the panel has roughly 200 effectively independent observations, which
is far too few to fit a network properly but well inside the regime TabPFN was
built for.

Two adaptations were necessary and neither is cosmetic:

  Context size. TabPFN is designed for datasets up to about 50k rows and slows
  down sharply with context length on CPU. Training folds here reach 120k rows,
  so the context is subsampled.

  What to subsample. The obvious choice - keep the most recent N rows - is
  wrong for an in-context learner. It has no parameters to carry a memory, so
  its context IS its knowledge, and a context drawn only from 2024 can only
  pattern-match 2024. Rows are sampled uniformly across the whole training
  history to keep 2008, 2013 and 2020 represented.

A CAVEAT THAT DOES NOT APPLY TO THE MLP

TabPFN's pre-training prior is i.i.d. tabular data. This panel is neither -
returns are cross-sectionally correlated and the 20-day targets overlap. The
prior is therefore misspecified for financial panels in a way it is not for the
benchmarks TabPFN wins on, so its leaderboard standing does not transfer
automatically. That is exactly what the bake-off is for.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

import config
import features as features_module
import metrics as metrics_module


class NeuralSignal:
    """The PyTorch MLP from model.py, wrapped in the common interface.

    Thirty-nine columns, thirty-two wide - the configuration that measured
    better on test, which is not the one validation preferred.

    A narrower net on the core sixteen columns was fitted and measured on
    2026-08-20. On validation it won: rank IC +0.1097 against +0.1028 for this
    one, over three seeds, and both far above the best one-line rule at +0.0260.
    On the fourteen walk-forward folds it then scored -9bp excess at t -0.44,
    break-even 5.1bp against a 34.1bp round trip - against +23bp and t 0.97 for
    what is here. It is recorded as run 4 in the run log.

    The lesson is in the size of the validation gap. +0.1097 against +0.1028 is
    0.007, and the spread across seeds of a single configuration reached 0.014
    - twice the difference being selected on. Validation did not prefer the
    narrow net; it was indistinguishable from it, and picking the higher number
    inside that noise is how a validation set gets overfit exactly the way a
    test set does. The one honest read on test is spent, so this is not
    reopened by trying a third shape.

    Width, dropout, learning rate and the column list stay exposed so the next
    attempt can be swept on VALIDATION, which remains the only surface any of
    them may be swept on.

    The column list is carried on the fitted net, so a saved signal predicts
    with the inputs it was fitted on rather than whatever the module default
    happens to be when it is loaded back.
    """

    name = "neural net"

    def __init__(self, epochs: int = 60, seed: int = config.SEED,
                 hidden: int = 32, dropout: float = 0.2,
                 learning_rate: float = 1e-3, patience: int = 12,
                 weight_decay: float = 1e-4, columns=None):
        self.epochs = epochs
        self.seed = seed
        self.hidden = hidden
        self.dropout = dropout
        self.learning_rate = learning_rate
        self.patience = patience
        self.weight_decay = weight_decay
        self.columns = list(columns) if columns else list(
            features_module.MODEL_COLUMNS)
        self.net = None
        self.report = {}

    def fit(self, train_panel: pd.DataFrame, val_panel: pd.DataFrame) -> None:
        import model as model_module

        self.net, self.report = model_module.train(
            train_panel, val_panel, hidden=self.hidden, epochs=self.epochs,
            learning_rate=self.learning_rate, dropout=self.dropout,
            patience=self.patience, seed=self.seed,
            weight_decay=self.weight_decay, columns=self.columns, quiet=True)

    def predict(self, panel: pd.DataFrame) -> np.ndarray:
        import model as model_module

        if self.net is None:
            raise RuntimeError("fit first")
        return model_module.predict(self.net, panel, self.columns)


class TabPFNSignal:
    """TabPFN-2.5 as an in-context ranker. No training loop, no gradients."""

    name = "tabpfn"

    def __init__(self, max_context: int = 8000, seed: int = config.SEED,
                 device: str = "cpu", chunk: int = 4000, columns=None):
        self.max_context = max_context
        self.seed = seed
        self.device = device
        self.chunk = chunk
        self.columns = list(columns) if columns else list(
            features_module.MODEL_COLUMNS)
        self.regressor = None

    def subsample(self, panel: pd.DataFrame) -> pd.DataFrame:
        """Cut the context to size by dropping whole DATES, never single rows.

        The label is a rank computed across the names alive on one date. Sample
        rows at random and you hand the model a date with nine of its thirty
        names present, whose ranks now describe a cross-section that never
        existed - the top name of a nine-name subset is not the top name of the
        thirty. Keeping dates whole preserves the only structure the label has.

        Dates are taken evenly across the whole history rather than from the
        recent end. An in-context learner has no parameters to remember 2008
        with; its context is its entire knowledge, so a context drawn from the
        last two years can only pattern-match the last two years.
        """
        if len(panel) <= self.max_context:
            return panel

        dates = np.sort(panel["timestamp"].unique())
        per_date = max(len(panel) / max(len(dates), 1), 1.0)
        keep_n = max(int(self.max_context / per_date), 2)
        if keep_n >= len(dates):
            return panel

        # Evenly spaced, so every regime in the training window contributes.
        picked = dates[np.linspace(0, len(dates) - 1, keep_n).astype(int)]
        return panel[panel["timestamp"].isin(picked)]

    def fit(self, train_panel: pd.DataFrame, val_panel: pd.DataFrame) -> None:
        from tabpfn import TabPFNRegressor

        columns = self.columns
        panel = self.subsample(train_panel)

        x = panel[columns].to_numpy(dtype=np.float32)
        # The same per-date rank label the MLP trains on, so the comparison is
        # between models rather than between targets.
        y = metrics_module.rank_target(panel)

        self.regressor = TabPFNRegressor(device=self.device,
                                         random_state=self.seed)
        self.regressor.fit(x, y)

    def predict(self, panel: pd.DataFrame) -> np.ndarray:
        if self.regressor is None:
            raise RuntimeError("fit first")
        x = panel[self.columns].to_numpy(dtype=np.float32)

        # Chunked because memory in a single forward pass grows with the test
        # block, and a fold can ask for 20k rows at once.
        out = []
        for start in range(0, len(x), self.chunk):
            out.append(self.regressor.predict(x[start:start + self.chunk]))
        return np.concatenate(out) if out else np.array([])


class BoostedSignal:
    """LightGBM on the identical target, as the control for the MLP.

    Included to settle a question rather than to ship: the asset-pricing
    literature is fairly consistent that shallow models beat deep ones on
    financial panels, because the signal-to-noise ratio is low and trees are
    harder to overfit into. If that holds here, the network has no business
    being the production model, and the honest response is to say so.

    Everything except the estimator is held fixed - same 39 features, same
    per-date rank label, same early stopping on the validation set, same
    turnover machinery downstream. Any difference in break-even is therefore
    attributable to the model.

    Kept deliberately small for the same reason the MLP is: 30 names on
    overlapping 20-day windows is closer to 200 independent observations than
    to the 100,000 rows it looks like, and num_leaves is set against that.

    Parameters come from boosting.py, which is NNS's block unchanged - including
    deterministic=True and force_row_wise=True. Those are not tuning. Without
    them LightGBM's answer depends on the order its threads finished, and a
    model comparison that cannot be reproduced is not a comparison.
    """

    name = "lightgbm"

    def __init__(self, seed: int = config.GBM_SEED, columns=None, **overrides):
        self.seed = seed
        self.overrides = overrides
        self.columns = list(columns) if columns else list(
            features_module.MODEL_COLUMNS)
        self.model = None

    def fit(self, train_panel: pd.DataFrame, val_panel: pd.DataFrame) -> None:
        import boosting

        columns = self.columns
        self.model = boosting.regressor(random_state=self.seed, **self.overrides)
        boosting.fit(
            self.model,
            train_panel[columns].to_numpy(dtype=np.float32),
            metrics_module.rank_target(train_panel),
            val_panel[columns].to_numpy(dtype=np.float32),
            metrics_module.rank_target(val_panel),
            metric="l2")

    def predict(self, panel: pd.DataFrame) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("fit first")
        return self.model.predict(
            panel[self.columns].to_numpy(dtype=np.float32))


def build(name: str, **kwargs):
    """Factory. `name` is one of the keys below."""
    if name == "nn":
        return NeuralSignal(**{k: v for k, v in kwargs.items()
                               if k in ("epochs", "seed", "hidden", "dropout",
                                        "learning_rate", "patience",
                                        "weight_decay", "columns")})
    if name == "lightgbm":
        return BoostedSignal(**{k: v for k, v in kwargs.items()
                                if k in ("seed", "rounds", "columns")})
    if name == "tabpfn":
        return TabPFNSignal(**{k: v for k, v in kwargs.items()
                               if k in ("max_context", "seed", "device",
                                        "chunk", "columns")})
    raise ValueError(f"unknown signal {name!r}")


# Where the package stores a token after a successful interactive login. Checked
# alongside the environment variable so that authenticating once in a normal
# terminal is enough - the credential never has to be pasted anywhere else.
TOKEN_FILES = (
    os.path.join(os.path.expanduser("~"), ".cache", "tabpfn", "auth_token"),
    os.path.join(os.path.expanduser("~"), ".tabpfn", "token"),
)


def tabpfn_available() -> tuple[bool, str]:
    """Is TabPFN importable AND licensed? The second half is the usual blocker.

    The package installs freely but refuses to download weights until a licence
    has been accepted against a Prior Labs account, and it tries to arrange that
    interactively - opening a browser and reading a key from stdin. In a
    non-interactive shell that does not degrade, it raises WinError 10038 from
    the middle of `fit`. Checked up front so a 14-fold run does not die on fold
    one having already spent minutes training.
    """
    try:
        import tabpfn                                          # noqa: F401
    except ImportError:
        return False, "tabpfn is not installed (py -3.13 -m pip install tabpfn)"

    if os.environ.get("TABPFN_TOKEN"):
        return True, "ready (token from environment)"
    for path in TOKEN_FILES:
        if os.path.exists(path) and open(path).read().strip():
            return True, f"ready (token cached at {path})"

    return False, ("no licence token - run this once in an interactive "
                   "terminal to accept the licence and cache a token:\n"
                   "    py -3.13 -c \"from tabpfn import TabPFNRegressor; "
                   "import numpy as np; r = TabPFNRegressor(device='cpu'); "
                   "r.fit(np.random.rand(50, 3), np.random.rand(50))\"")


if __name__ == "__main__":
    ok, message = tabpfn_available()
    print(f"tabpfn: {message}")
