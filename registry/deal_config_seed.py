"""
Seed the editable per-deal config for the deployment's default deal from the
engines' seeded defaults (which read data/deal_seeds/deal_defaults.json —
gitignored, see modules/deal_seed.py). Because the stored values are *derived
from* the engines rather than re-typed, the config path reproduces the
no-config numbers exactly.

Called once at app boot. Idempotent: only writes a module's config if absent,
unless overwrite=True. Does nothing without a deal seed — a clean instance
must not invent a deal.
"""

from __future__ import annotations


def default_config_specs() -> dict[str, dict]:
    """Build the {module: config} map from the engines' seeded defaults."""
    from modules.tif_analysis.engine import TIFAssumptions, default_scenarios
    from modules.distribution.engine import DistributionAssumptions
    from modules.debt_analysis.engine import default_debt_config

    return {
        "tif": {
            **TIFAssumptions.seeded_defaults().to_config(),
            "scenarios": default_scenarios(),
        },
        "distribution": DistributionAssumptions.seeded_defaults().to_config(),
        "debt": default_debt_config(),
    }


def seed_default_deal_configs(reg, deal_id: str | None = None,
                              overwrite: bool = False) -> int:
    """Store the seeded deal's per-module config in the registry. Returns the
    number of module configs written (0 when no deal seed is present)."""
    from modules import deal_seed
    if not deal_seed.present():
        return 0
    deal_id = deal_id or deal_seed.load().get("deal_id")
    if not deal_id:
        return 0
    written = 0
    for module, cfg in default_config_specs().items():
        if overwrite or reg.get_deal_config(deal_id, module) is None:
            reg.set_deal_config(deal_id, module, cfg)
            written += 1
    return written
