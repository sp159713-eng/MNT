import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import metrics
import walkforward

ORACLE = "oracle (perfect)"
_baselines = metrics.baselines
_edge_bp = metrics.edge_bp


def baselines(panel):
    out = dict(_baselines(panel))
    out[ORACLE] = panel["target"].to_numpy()
    return out


def edge_bp(pooled, baseline_stats):
    return _edge_bp(pooled, {k: v for k, v in (baseline_stats or {}).items() if k != ORACLE})


metrics.baselines = baselines
metrics.edge_bp = edge_bp

if __name__ == "__main__":
    print(f"oracle: '{ORACLE}' ranks by the realised forward return (perfect foresight), "
          f"excluded from EDGE", flush=True)
    walkforward.main()
