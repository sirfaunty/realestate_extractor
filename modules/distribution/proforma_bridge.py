"""Bridge between the Chamberlain proforma engine and the distribution module.

Loads the Chamberlain scenario YAML, runs the proforma under a given TIF
scenario, and returns the data the distribution engine needs:

  - levered_cash_flow by year (operating distributable CF)
  - net_sale_proceeds (Year-10 terminal distribution)
  - NOI by year (for reference/display)
  - initial_equity (from the scenario config)
  - proforma-level return metrics

Results are cached per TIF scenario to avoid re-running the full proforma
on every API call.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Resolve the YAML config path relative to this file
_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / 'chamberlain' / 'config'
_BASE_YAML = _CONFIG_DIR / 'chamberlain_base.yaml'


@dataclass
class ProformaSnapshot:
    """Extracted proforma data for the distribution engine."""
    scenario_name: str
    tif_scenario: str
    hold_years: int
    first_calendar_year: int
    initial_equity: float
    acquisition_cost_basis: float

    # Annual data
    levered_cf_by_year: dict[int, float] = field(default_factory=dict)
    noi_by_year: dict[int, float] = field(default_factory=dict)
    debt_service_by_year: dict[int, float] = field(default_factory=dict)
    capex_by_year: dict[int, float] = field(default_factory=dict)
    non_op_by_year: dict[int, float] = field(default_factory=dict)
    dscr_by_year: dict[int, float] = field(default_factory=dict)
    calendar_years: dict[int, int] = field(default_factory=dict)

    # Residual
    net_sale_proceeds: float = 0.0
    gross_sale_price: float = 0.0
    exit_cap_rate: float = 0.0
    loan_repayment_at_sale: float = 0.0
    sale_year: int = 10

    # Proforma-level returns
    levered_irr: Optional[float] = None
    equity_multiple: float = 0.0
    avg_dscr: float = 0.0

    def to_dict(self) -> dict:
        return {
            'scenario_name': self.scenario_name,
            'tif_scenario': self.tif_scenario,
            'hold_years': self.hold_years,
            'first_calendar_year': self.first_calendar_year,
            'initial_equity': round(self.initial_equity, 2),
            'acquisition_cost_basis': round(self.acquisition_cost_basis, 2),
            'levered_cf_by_year': {
                str(k): round(v, 2) for k, v in self.levered_cf_by_year.items()
            },
            'noi_by_year': {
                str(k): round(v, 2) for k, v in self.noi_by_year.items()
            },
            'net_sale_proceeds': round(self.net_sale_proceeds, 2),
            'gross_sale_price': round(self.gross_sale_price, 2),
            'exit_cap_rate': self.exit_cap_rate,
            'sale_year': self.sale_year,
            'levered_irr': round(self.levered_irr, 6) if self.levered_irr else None,
            'equity_multiple': round(self.equity_multiple, 4),
            'avg_dscr': round(self.avg_dscr, 3),
        }


# ─── Cache ─────────────────────────────────────────────────────────

_scenario_cache = None        # loaded Scenario object
_snapshot_cache: dict[str, ProformaSnapshot] = {}


def _get_scenario():
    """Lazy-load and cache the Chamberlain scenario."""
    global _scenario_cache
    if _scenario_cache is None:
        from chamberlain.io.yaml_loader import load_scenario_from_yaml
        logger.info(f'Loading Chamberlain scenario from {_BASE_YAML}')
        _scenario_cache = load_scenario_from_yaml(_BASE_YAML)
    return _scenario_cache


def get_proforma_snapshot(
    tif_scenario: str = 'baseline',
) -> ProformaSnapshot:
    """Run the proforma for a given TIF scenario and return a snapshot.

    Results are cached per TIF scenario name.

    Args:
        tif_scenario: one of 'baseline', 'mid_appeal', 'aggressive_appeal', 'maa_floor'

    Returns:
        ProformaSnapshot with all data needed by the distribution engine.
    """
    if tif_scenario in _snapshot_cache:
        return _snapshot_cache[tif_scenario]

    from chamberlain.engine.runner import run_proforma
    from chamberlain.models.tif import TIFScenarioName

    scenario = _get_scenario()

    # Map string to enum
    tif_enum = TIFScenarioName(tif_scenario)

    logger.info(f'Running proforma for TIF scenario: {tif_scenario}')
    result = run_proforma(scenario, tif_enum)

    # Extract what the distribution engine needs
    snap = ProformaSnapshot(
        scenario_name=result.scenario_name,
        tif_scenario=tif_scenario,
        hold_years=len(result.annual_lines),
        first_calendar_year=result.annual_lines[0].calendar_year if result.annual_lines else 2026,
        initial_equity=result.initial_equity,
        acquisition_cost_basis=result.acquisition_cost_basis,
    )

    for ln in result.annual_lines:
        snap.levered_cf_by_year[ln.year] = ln.levered_cash_flow
        snap.noi_by_year[ln.year] = ln.net_operating_income
        snap.debt_service_by_year[ln.year] = ln.debt_service
        snap.capex_by_year[ln.year] = ln.capex
        snap.non_op_by_year[ln.year] = ln.non_operating_total
        snap.dscr_by_year[ln.year] = ln.dscr
        snap.calendar_years[ln.year] = ln.calendar_year

    if result.residual:
        snap.net_sale_proceeds = result.residual.net_sale_proceeds
        snap.gross_sale_price = result.residual.gross_sale_price
        snap.exit_cap_rate = result.residual.residual_cap_rate
        snap.loan_repayment_at_sale = result.residual.loan_repayment
        snap.sale_year = result.residual.sale_year

    if result.returns:
        snap.levered_irr = result.returns.levered_irr
        snap.equity_multiple = result.returns.equity_multiple
        snap.avg_dscr = result.returns.avg_dscr

    _snapshot_cache[tif_scenario] = snap
    return snap


def get_available_tif_scenarios() -> list[dict]:
    """Return list of available TIF scenarios."""
    from chamberlain.models.tif import TIFScenarioName
    return [
        {'id': s.value, 'label': s.value.replace('_', ' ').title()}
        for s in TIFScenarioName
    ]


def clear_cache():
    """Clear the proforma cache (e.g., after config changes)."""
    global _scenario_cache
    _scenario_cache = None
    _snapshot_cache.clear()


__all__ = [
    'ProformaSnapshot',
    'get_proforma_snapshot',
    'get_available_tif_scenarios',
    'clear_cache',
]
