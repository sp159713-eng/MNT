import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import features as features_module
import production as production_module


def main() -> None:
    panel = features_module.cross_sectionalize(
        features_module.build_panel(), require_target=False)
    date = production_module.latest_complete_date(panel)
    frame = panel[panel["timestamp"] == date].copy()
    print(f"scoring {date.date()} on {len(frame)} names\n")

    for count in config.GBM_PRESETS:
        name = f"gbm{count}"
        began = time.time()
        signal, _ = production_module.fit(name, quiet=True)
        took = time.time() - began
        scored = frame.copy()
        scored["score"] = signal.predict(scored)
        top = scored.sort_values("score", ascending=False).head(6)
        picks = ", ".join(top["symbol"].tolist())
        print(f"{name:>7}  fit {took:6.1f}s  top6: {picks}")


if __name__ == "__main__":
    main()
