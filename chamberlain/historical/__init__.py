"""Historical-data loaders for Chamberlain.

Pipeline:
  1. registry_builder.build_registry() — registers all source docs
  2. mri_loader.load_all_chamberlain_actuals() — parses MRI 2017-2024 + TTM Sep 2025
  3. overview_loader.load_historical_rollups() — pulls Property Overview rollups
  4. validated_loader.apply_all_validated_overlays() — overlays Property NOI

End-to-end usage:

    from ..historical import (
        build_registry, load_all_chamberlain_actuals,
        apply_all_validated_overlays,
    )

    registry = build_registry()
    actuals = load_all_chamberlain_actuals(registry)
    apply_all_validated_overlays(actuals, registry)
    # Now actuals.periods carry both MRI line items + validated Property NOI
"""

from .line_item_mapper import (
    LABEL_TO_CATEGORY,
    SKIP_LABELS,
    SUBTOTAL_LABELS,
    classify_label,
)
from .mri_loader import (
    load_all_chamberlain_actuals,
    load_mri_file,
)
from .overview_loader import (
    load_historical_rollups,
    load_property_info,
    load_unit_roster,
)
from .registry_builder import (
    DEFAULT_SOURCE_DOCS_ROOT,
    build_registry,
)
from .validated_loader import (
    apply_all_validated_overlays,
    overlay_validated_btl_items,
    overlay_validated_property_noi,
)

__all__ = [
    "LABEL_TO_CATEGORY",
    "SKIP_LABELS",
    "SUBTOTAL_LABELS",
    "classify_label",
    "load_all_chamberlain_actuals",
    "load_mri_file",
    "load_historical_rollups",
    "load_property_info",
    "load_unit_roster",
    "DEFAULT_SOURCE_DOCS_ROOT",
    "build_registry",
    "apply_all_validated_overlays",
    "overlay_validated_btl_items",
    "overlay_validated_property_noi",
]
