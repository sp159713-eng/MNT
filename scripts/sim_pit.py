import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

import config
import data as data_module
import features as features_module
import production as production_module
import walkforward

SIZE = int(os.environ.get("MNT_PIT_SIZE", "100"))
AS_OF_TODAY = os.environ.get("MNT_PIT_ASOF") == "today"

_rank = features_module.cross_sectionalize
_cut = walkforward.cut
_fold_dates = walkforward.fold_dates
_state = {"folds": [], "calls": 0, "rosters": {}}


def _turnover() -> pd.DataFrame:
    frames = data_module.load(list(config.UNIVERSE), interval="1d",
                              start=config.START)
    value = pd.DataFrame({s: b["close"] * b["volume"] for s, b in frames.items()})
    return value.sort_index().rolling(250, min_periods=200).mean()


TURNOVER = None if AS_OF_TODAY else _turnover()
TODAY = production_module.liquid_names(SIZE) if AS_OF_TODAY else None


def roster_at(date) -> list[str]:
    if AS_OF_TODAY:
        return TODAY
    history = TURNOVER.loc[:date]
    if history.empty:
        return []
    return sorted(history.iloc[-1].dropna().nlargest(SIZE).index)


def fold_dates(panel, start_year, horizon):
    folds = _fold_dates(panel.dropna(subset=["target"]), start_year, horizon)
    _state["folds"] = folds
    return folds


def cut(panel, lo, hi, horizon):
    fold = _state["folds"][_state["calls"] // 3]
    _state["calls"] += 1
    names = roster_at(fold[1])
    if fold[1] not in _state["rosters"]:
        _state["rosters"][fold[1]] = names
        print(f"roster {fold[1].date()}: {len(names)} names", flush=True)
    return _rank(_cut(panel[panel["symbol"].isin(names)], lo, hi, horizon))


features_module.cross_sectionalize = lambda panel, require_target=True: panel
walkforward.fold_dates = fold_dates
walkforward.cut = cut

if __name__ == "__main__":
    mode = "today" if AS_OF_TODAY else "point-in-time"
    print(f"pit roster: top {SIZE} by 250-day traded value, {mode}", flush=True)
    walkforward.main()
    if not AS_OF_TODAY:
        today = set(production_module.liquid_names(SIZE))
        for date, names in _state["rosters"].items():
            print(f"{date.date()} overlap with today's top {SIZE}: "
                  f"{len(today & set(names))}/{len(names)}")
