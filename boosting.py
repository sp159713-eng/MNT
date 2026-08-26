"""Shared LightGBM setup, carried over from NNS.

One parameter block, explicit overrides. NNS's reason for centralising it holds
here too: three near-identical copies drift, and a "baseline" trained with
different regularisation than the model it benchmarks is not a baseline.

The determinism flags are the part that matters most and the part most easily
left out. LightGBM builds histograms in parallel and sums floating-point
gradients in whatever order the threads happen to finish, so the same data with
the same seed does not give the same model. NNS measured that spread at roughly
0.4 accuracy points - wider than most of the differences this project exists to
measure, which means a comparison run without these flags cannot distinguish a
better model from a luckier thread schedule.
"""

from __future__ import annotations

import lightgbm as lgb

import config


_KNOWN_NAMES = None


def _known_names() -> frozenset:
    """Every parameter name LightGBM will actually read, aliases included.

    From lightgbm's own alias table plus the sklearn wrapper's arguments.
    Cached: the table is 141 entries and there is no reason to rebuild it.
    """
    global _KNOWN_NAMES
    if _KNOWN_NAMES is None:
        try:
            from lightgbm.basic import _ConfigAliases

            table = _ConfigAliases._get_all_param_aliases()
            names = set(table) | {a for group in table.values() for a in group}
            names |= set(lgb.LGBMModel().get_params())
        except Exception:
            # Private API. If a future lightgbm moves it, the check disables
            # itself rather than taking every fit in the project with it.
            names = set()
        _KNOWN_NAMES = frozenset(names)
    return _KNOWN_NAMES


def params(**overrides) -> dict:
    # LightGBM accepts a misspelled parameter in silence: `learning_rat=0.5` is
    # carried along doing nothing while learning_rate keeps its default, with
    # no warning. In a sweep that produces the same number at every setting,
    # and the honest reading of that output is "this parameter does not
    # matter". Measured on 4.7.0 before this guard existed.
    known = _known_names()
    if known:
        unknown = sorted(k for k in overrides if k not in known)
        if unknown:
            raise TypeError(
                f"boosting.params got {len(unknown)} name(s) LightGBM does not "
                f"read: {', '.join(unknown)}. A misspelling here is silent - "
                "the value is ignored and the default stands - so it is "
                "refused rather than passed on.")

    base = dict(
        learning_rate=config.GBM_LEARNING_RATE,
        n_estimators=config.GBM_N_ESTIMATORS,
        num_leaves=config.GBM_NUM_LEAVES,
        max_depth=config.GBM_MAX_DEPTH,
        min_child_samples=config.GBM_MIN_CHILD_SAMPLES,
        subsample=config.GBM_SUBSAMPLE,
        subsample_freq=config.GBM_SUBSAMPLE_FREQ,
        colsample_bytree=config.GBM_COLSAMPLE,
        reg_lambda=config.GBM_REG_LAMBDA,
        random_state=config.GBM_SEED,
        verbose=-1,
        deterministic=True,
        force_row_wise=True,
        num_threads=config.GBM_THREADS,
    )
    base.update(overrides)
    return base


def regressor(**overrides) -> lgb.LGBMRegressor:
    return lgb.LGBMRegressor(**params(**overrides))


def classifier(**overrides) -> lgb.LGBMClassifier:
    return lgb.LGBMClassifier(**params(**overrides))


def fit(model, x_train, y_train, x_val, y_val, metric: str = "l2",
        sample_weight=None):
    """Fit with early stopping on the validation split.

    sample_weight re-weights the TRAINING rows only. Validation is deliberately
    left unweighted: early stopping decides when the model has stopped improving
    on the distribution it will actually be used on, and weighting both halves
    would stop it against a distribution that does not exist outside this fit.
    """
    if x_val is None or len(x_val) == 0:
        # Early stopping with nothing to stop against fails inside a callback,
        # several frames down, naming a LightGBM internal. The caller's real
        # mistake is an empty validation split, so say that here.
        raise ValueError(
            "boosting.fit needs a non-empty validation split - early stopping "
            "has nothing to measure against. Pass one, or fit the estimator "
            f"directly to take all {config.GBM_N_ESTIMATORS} rounds.")

    model.fit(
        x_train, y_train,
        sample_weight=sample_weight,
        # eval_X/eval_y rather than eval_set: lightgbm 4.7.0 deprecated
        # eval_set in favour of these, and the old spelling now emits
        # LGBMDeprecationWarning on every fit this project makes. NNS's copy
        # was already correct; this one had lagged behind it.
        eval_X=x_val, eval_y=y_val,
        eval_metric=metric,
        callbacks=[lgb.early_stopping(config.GBM_EARLY_STOPPING_ROUNDS,
                                      verbose=False)],
    )

    # n_estimators is documented as a ceiling early stopping never reaches.
    # When it IS reached the fit is capped rather than converged, the ceiling
    # is doing the choosing, and comparing it against a model that stopped
    # early compares two different things. Checked against the model's own
    # ceiling, since it is overridable per call.
    ceiling = model.get_params().get("n_estimators") or config.GBM_N_ESTIMATORS
    reached = getattr(model, "best_iteration_", None)
    if reached and reached >= ceiling:
        print(f"  NOTE: early stopping never fired - the fit used all "
              f"{ceiling} rounds and is capped, not converged. Raise the "
              f"n_estimators ceiling before reading this fit as finished.")
    return model
