"""
Golden-value regression guard for the multi-deal config externalization.

The Deal A constants moved out of the engines and into the editable per-deal
config store. These tests assert that loading the *config* path reproduces the
engines' hardcoded-default behavior byte-for-byte, so making the modules
deal-aware does not change Deal A's numbers.

Run:  pytest tests/test_deal_config_golden.py
"""

import json
from dataclasses import asdict

from modules.tif_analysis.engine import (
    TIFAssumptions, TIFEngine, DEAL_A_SCENARIOS)
from modules.distribution.engine import DistributionAssumptions
from modules.debt_analysis.engine import DebtAnalysisEngine, default_debt_config


def test_tif_config_reproduces_full_output():
    """Full 4-scenario comparison must be identical from config vs. defaults."""
    a = TIFAssumptions()
    seed = {**a.to_config(), "scenarios": dict(DEAL_A_SCENARIOS)}
    ac = TIFAssumptions.from_config(seed)
    ed, ec = TIFEngine(a), TIFEngine(ac)
    sd = {n: ed._make_flat_schedule(t) for n, t in DEAL_A_SCENARIOS.items()}
    sc = {n: ec._make_flat_schedule(t) for n, t in seed["scenarios"].items()}
    assert (json.dumps(ed.compare_scenarios(sd), sort_keys=True)
            == json.dumps(ec.compare_scenarios(sc), sort_keys=True))


def test_tif_none_config_is_defaults():
    assert asdict(TIFAssumptions.from_config(None)) == asdict(TIFAssumptions())


def test_distribution_config_roundtrip():
    d = DistributionAssumptions.proforma_engine_defaults()
    assert asdict(DistributionAssumptions.from_config(d.to_config())) == asdict(d)
    assert asdict(DistributionAssumptions.from_config(None)) == asdict(d)


def test_debt_config_reproduces_defaults():
    keys = ("loan", "mip", "capex", "surplus", "prop")
    base = DebtAnalysisEngine()
    cfg = DebtAnalysisEngine(default_debt_config())
    none = DebtAnalysisEngine(None)
    for k in keys:
        assert getattr(cfg, k) == getattr(base, k)
        assert getattr(none, k) == getattr(base, k)


def test_seed_specs_match_engine_defaults():
    """The registry seed must be derived from (and equal to) the engine defaults."""
    from registry.deal_config_seed import proforma_engine_config_specs
    specs = proforma_engine_config_specs()

    assert (TIFAssumptions.from_config(specs["tif"]).to_config()
            == TIFAssumptions().to_config())

    base = DebtAnalysisEngine()
    cfg = DebtAnalysisEngine(specs["debt"])
    for k in ("loan", "mip", "capex", "surplus", "prop"):
        assert getattr(cfg, k) == getattr(base, k)

    d = DistributionAssumptions.proforma_engine_defaults()
    assert asdict(DistributionAssumptions.from_config(specs["distribution"])) == asdict(d)
