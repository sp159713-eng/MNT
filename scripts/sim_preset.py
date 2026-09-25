import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import production as production_module

_gbm_size = production_module.preset_size


def preset_size(name):
    if name and name.startswith("xgb") and name[3:].isdigit():
        return int(name[3:])
    return _gbm_size(name)


production_module.preset_size = preset_size
config.SIGNALS = tuple(config.SIGNALS) + tuple(
    f"xgb{n}" for n in config.GBM_PRESETS)

import walkforward

if __name__ == "__main__":
    walkforward.main()
