"""
Seed the editable per-deal config for the Chamberlain deal by introspecting the
current engine defaults. Because the seeded values are *derived from the code's
own defaults* (not re-typed by hand), the config path is guaranteed to reproduce
today's Chamberlain numbers. The golden-value test in tests/ locks this in.

Called once at app boot (see registry bootstrap). Idempotent: only writes a
module's config if absent, unless overwrite=True.
"""

from __future__ import annotations


def chamberlain_config_specs() -> dict[str, dict]:
    """Build the {module: config} map for Chamberlain from the live engine defaults."""
    from modules.tif_analysis.engine import TIFAssumptions, CHAMBERLAIN_SCENARIOS
    from modules.distribution.engine import DistributionAssumptions
    from modules.debt_analysis.engine import default_debt_config

    return {
        "tif": {
            **TIFAssumptions().to_config(),
            "scenarios": dict(CHAMBERLAIN_SCENARIOS),
        },
        "distribution": DistributionAssumptions.chamberlain_defaults().to_config(),
        "debt": default_debt_config(),
    }


def seed_chamberlain_configs(reg, deal_id: str = "chamberlain",
                             overwrite: bool = False) -> int:
    """Store Chamberlain's per-module config in the registry. Returns the number
    of module configs written."""
    written = 0
    for module, cfg in chamberlain_config_specs().items():
        if overwrite or reg.get_deal_config(deal_id, module) is None:
            reg.set_deal_config(deal_id, module, cfg)
            written += 1
    return written
