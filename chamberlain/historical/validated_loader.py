"""Validated cross-reference loader.

Loads the Q2 2026 reconciliation work (property_noi_FINAL.json,
historical_pl.json, property_btl_items.json) and overlays the validated
Property NOI onto the HistoricalActuals from MRI / Property Overview.

Authority hierarchy:
  - MRI exports (SECONDARY): line-item detail
  - Property Overview Summary (SECONDARY): consolidated rollups for KA
  - Validated JSONs (TERTIARY): derived analysis, applied as Property NOI
    overlay; documents the "before AMF / CapEx / non-op" adjustments

The validated Property NOI ($2,998K 2024A etc.) is what shows up in the
KA Q2 2026 Leadership Board. We carry it on HistoricalPeriod.property_noi
alongside the as-reported NOI (which comes from MRI or Property Overview).

The "before X" reclasses (AMF, CapEx, non-op) are captured on the
period's amf_amount, routine_capex, improvement_capex fields.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..models.citation import (
    Citation,
    Cited,
    Locator,
    SourceDocumentRegistry,
)
from ..models.historical import HistoricalActuals


# Map of year-label-in-JSON → fiscal year and period_type for matching.
# JSON keys are like "2023A", "2024A", "2025A", "2026B", "2025P".
def _parse_year_key(key: str) -> tuple[int, str]:
    """Parse '2023A' → (2023, 'A'); '2026B' → (2026, 'B'); '2025P' → (2025, 'P')."""
    s = key.strip()
    if len(s) >= 5 and s[:4].isdigit():
        return int(s[:4]), s[4:]
    raise ValueError(f"Unable to parse year key: {key!r}")


def overlay_validated_property_noi(
    actuals: HistoricalActuals,
    registry: SourceDocumentRegistry,
    asset_name: str = "Chamberlain",
) -> None:
    """Read property_noi_FINAL.json and apply to actuals in-place.

    Sets HistoricalPeriod.property_noi for each year that has a validated value.
    """
    doc = registry.require("validated_property_noi")
    assert doc.file_path is not None
    data = json.loads(doc.file_path.read_text())

    asset_data = data.get(asset_name)
    if asset_data is None:
        return

    # asset_data is dict like {"2023A": 3997.3, "2024A": 2998.4, ...}
    # Values are in $K (thousands), need to multiply by 1000.
    for year_key, value_k in asset_data.items():
        year, suffix = _parse_year_key(year_key)
        period = actuals.period(year)
        if period is None:
            # Allow matching against reforecast labels too
            for p in actuals.periods:
                if p.period.year == year:
                    period = p
                    break
        if period is None:
            continue

        period.property_noi = Cited(
            value=float(value_k) * 1000.0,  # convert $K → $
            citations=[Citation(
                source_document_id="validated_property_noi",
                locator=Locator(
                    note=f"JSON key '{asset_name}.{year_key}' value in $K",
                ),
                verbatim_text=f"{value_k}",
                confidence=0.95,
                note="Q2 2026 reconciled Property NOI = NOI before AMF, CapEx, non-op",
            )],
            note=f"Property NOI from Q2 2026 reconciliation ({year_key} = ${value_k}K)",
        )


def overlay_validated_btl_items(
    actuals: HistoricalActuals,
    registry: SourceDocumentRegistry,
    asset_name: str = "Chamberlain",
) -> None:
    """Read property_btl_items.json and apply AMF + CapEx values."""
    doc = registry.require("validated_btl_items")
    assert doc.file_path is not None
    data = json.loads(doc.file_path.read_text())

    asset_data = data.get(asset_name)
    if asset_data is None:
        return

    field_map = {
        "AMF (in OpEx)": "amf_amount",
        "Routine CapEx": "routine_capex",
        "Improvement CapEx": "improvement_capex",
    }

    for json_field, attr in field_map.items():
        year_dict = asset_data.get(json_field, {})
        for year_key, value_k in year_dict.items():
            if value_k is None:
                continue
            year, _suffix = _parse_year_key(year_key)
            period = actuals.period(year)
            if period is None:
                for p in actuals.periods:
                    if p.period.year == year:
                        period = p
                        break
            if period is None:
                continue
            setattr(period, attr, Cited(
                value=float(value_k) * 1000.0,
                citations=[Citation(
                    source_document_id="validated_btl_items",
                    locator=Locator(
                        note=f"JSON key '{asset_name}.{json_field}.{year_key}' in $K",
                    ),
                    verbatim_text=f"{value_k}",
                    confidence=0.95,
                    note=f"Q2 2026 below-the-line classification ({json_field})",
                )],
            ))


def apply_all_validated_overlays(
    actuals: HistoricalActuals,
    registry: SourceDocumentRegistry,
    asset_name: str = "Chamberlain",
) -> None:
    """Apply all validated cross-reference overlays in sequence."""
    overlay_validated_property_noi(actuals, registry, asset_name)
    overlay_validated_btl_items(actuals, registry, asset_name)


__all__ = [
    "overlay_validated_property_noi",
    "overlay_validated_btl_items",
    "apply_all_validated_overlays",
]
