"""
Golden snapshot of the deal-analytics engines' COMPUTED OUTPUTS.

Unlike test_deal_config_golden.py (which checks defaults vs config agree
with each other), this captures the actual numbers the engines produce so a
refactor can be proven byte-identical.

    python tests/deal_golden_snapshot.py capture  data/deal_golden.json
    python tests/deal_golden_snapshot.py verify   data/deal_golden.json

Values depend on the deployment's deal seed (data/deal_seeds/), which is
gitignored — the snapshot file is written under data/ for the same reason.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.dirname(HERE))   # modules/* import "modules.base"


def compute():
    from realestate_extractor.modules.tif_analysis.engine import TIFEngine, TIFAssumptions
    from realestate_extractor.modules.tif_analysis import engine as tif_mod
    from realestate_extractor.modules.distribution.engine import (
        DistributionEngine, DistributionAssumptions)
    from realestate_extractor.modules.distribution import engine as dist_mod
    from realestate_extractor.modules.debt_analysis.engine import (
        DebtAnalysisEngine, default_debt_config)

    scenarios = tif_mod.DEFAULT_SCENARIOS
    tif_a = TIFAssumptions.seeded_defaults()
    te = TIFEngine(tif_a)
    tif = te.compare_scenarios({n: te._make_flat_schedule(t) for n, t in scenarios.items()})

    dist_a = DistributionAssumptions.seeded_defaults()
    cf = dist_mod.DEFAULT_CF
    dist = DistributionEngine(dist_a).run_distribution(dict(cf)).to_dict()

    debt = DebtAnalysisEngine(default_debt_config()).run_analysis().to_dict()
    return {'tif': tif, 'distribution': dist, 'debt': debt,
            'debt_config': default_debt_config(),
            'dist_assumptions': dist_a.to_config(),
            'tif_assumptions': tif_a.to_config(),
            'scenarios': dict(scenarios)}


def main():
    mode, path = sys.argv[1], sys.argv[2]
    cur = json.loads(json.dumps(compute(), sort_keys=True, default=str))
    if mode == 'capture':
        with open(path, 'w') as f:
            json.dump(cur, f, indent=1, sort_keys=True)
        print(f'captured {path} ({os.path.getsize(path):,} bytes)')
        return
    ref = json.load(open(path))
    bad = [k for k in ref if json.dumps(ref[k], sort_keys=True)
           != json.dumps(cur.get(k), sort_keys=True)]
    if bad:
        print('MISMATCH in:', bad)
        sys.exit(1)
    print(f'IDENTICAL — all {len(ref)} sections match the golden snapshot')


if __name__ == '__main__':
    main()
