"""
market_classifier.py — Module 2 of the CoStar Market Scorecard Engine

Replicates the Summary Pull workbook:
  - Assigns inventory tiers based on configurable thresholds
  - Tags markets with general and specific regions from the reference table
  - Generates all peer group memberships for each market
  - Computes summary statistics (market counts per tier/region)

Source workbook sections replicated:
  - Rows 4-11:  Tier/region summary counts (COUNTIF formulas)
  - Rows 14-402: Market data table (VLOOKUP, IFS formulas)
  - Cols U-X:    Reference table (region assignments)

Peer group schemes (from the outline):
  1. Inventory Tier:           Over 50K, 15K-50K, 5K-15K, Under 5K
  2. Specific Region:          Midwest, Sunbelt, East Coast, West Coast, Mountain West
  3. General Region:           Midwest, Coastal, Smile Belt
  4. Inventory Tier + General Region:  e.g. "Over 50K + Coastal"
"""

import pandas as pd
import numpy as np
import json
from pathlib import Path
from .data_loader import load_costar_export, lookup


# ---------------------------------------------------------------------------
# Configuration — Tier thresholds (mirrors cells D6:D9 in Summary Pull)
# These are the default values; users can override them.
# ---------------------------------------------------------------------------

DEFAULT_TIER_THRESHOLDS = {
    "Over 50K":   50_000,
    "15K - 50K":  15_000,
    "5K - 15K":    5_000,
    "Under 5K":        0,   # Everything below 5K
}


# ---------------------------------------------------------------------------
# Reference table loader
# ---------------------------------------------------------------------------

def load_reference_table(ref_path: str | Path | None = None) -> dict:
    """
    Load the region reference table extracted from the Summary Pull.

    Returns a dict: market_name -> {general_region, specific_region}
    """
    if ref_path is None:
        ref_path = Path(__file__).parent / "reference_data.json"

    with open(ref_path) as f:
        data = json.load(f)

    region_map = {}
    for entry in data["reference_table"]:
        market_name = entry["costar_raw"].strip() + " USA"
        region_map[market_name] = {
            "general_region": entry["general_region"],
            "specific_region": entry["specific_region"],
        }

    return region_map


# ---------------------------------------------------------------------------
# Tier classification (replaces the IFS array formula in column D)
# ---------------------------------------------------------------------------

def classify_tier(
    inventory_units: float,
    thresholds: dict | None = None,
) -> str:
    """
    Classify a market into an inventory tier based on unit count.

    Replicates the Summary Pull formula:
        =IFS(N16>=$D$6, "Over 50K",
             AND(N16<$D$6, N16>=$D$7), "15K - 50K",
             N16<$D$9, "Under 5K",
             AND(N16<$D$7, N16>=$D$8), "5K - 15K")
    """
    if thresholds is None:
        thresholds = DEFAULT_TIER_THRESHOLDS

    if pd.isna(inventory_units) or inventory_units == 0:
        return "Under 5K"

    if inventory_units >= thresholds["Over 50K"]:
        return "Over 50K"
    elif inventory_units >= thresholds["15K - 50K"]:
        return "15K - 50K"
    elif inventory_units >= thresholds["5K - 15K"]:
        return "5K - 15K"
    else:
        return "Under 5K"


# ---------------------------------------------------------------------------
# Main classifier — builds the full market classification table
# ---------------------------------------------------------------------------

def classify_markets(
    df: pd.DataFrame,
    report_quarter: str = "2025 Q4",
    thresholds: dict | None = None,
    ref_path: str | Path | None = None,
) -> pd.DataFrame:
    """
    Build the market classification table that replicates the Summary Pull.

    Parameters
    ----------
    df              : normalized DataFrame from data_loader.load_costar_export()
    report_quarter  : the quarter to use for inventory unit counts (cell D2)
    thresholds      : optional custom tier thresholds
    ref_path        : path to reference_data.json

    Returns
    -------
    pd.DataFrame with columns:
        market, inventory_tier, general_region, specific_region,
        population, total_employment, office_employment,
        industrial_employment, median_household_income,
        inventory_all, inventory_4_5_star, inventory_3_star, inventory_1_2_star
    """
    if thresholds is None:
        thresholds = DEFAULT_TIER_THRESHOLDS

    # Load region reference
    region_map = load_reference_table(ref_path)

    # Get unique markets from the "All" property class
    # The DataFrame uses a MultiIndex (market, property_class, concept, quarter),
    # so we pull markets from the index level.
    idx = df.index.get_level_values("property_class") == "All"
    all_markets = sorted(df.index.get_level_values("market")[idx].unique())

    rows = []
    for market in all_markets:
        # --- Replicate columns I-Q of Summary Pull ---
        # All these are INDEX/MATCH lookups into the data sheets
        population = lookup(df, market, "All", "Population", report_quarter)
        total_employment = lookup(df, market, "All", "Total Employment", report_quarter)
        office_employment = lookup(df, market, "All", "Office Employment", report_quarter)
        industrial_employment = lookup(df, market, "All", "Industrial Employment", report_quarter)
        median_income = lookup(df, market, "All", "Median Household Income", report_quarter)

        inv_all = lookup(df, market, "All", "Inventory Units", report_quarter)
        inv_45 = lookup(df, market, "4 & 5 Star", "Inventory Units", report_quarter)
        inv_3 = lookup(df, market, "3 Star", "Inventory Units", report_quarter)
        inv_12 = lookup(df, market, "1 & 2 Star", "Inventory Units", report_quarter)

        # --- Tier classification (replaces IFS formula in col D) ---
        tier = classify_tier(inv_all, thresholds)

        # --- Region lookup (replaces VLOOKUPs in cols E-F) ---
        regions = region_map.get(market, {})
        gen_region = regions.get("general_region", "Unknown")
        spec_region = regions.get("specific_region", "Unknown")

        rows.append({
            "market": market,
            "inventory_tier": tier,
            "general_region": gen_region,
            "specific_region": spec_region,
            "population": population,
            "total_employment": total_employment,
            "office_employment": office_employment,
            "industrial_employment": industrial_employment,
            "median_household_income": median_income,
            "inventory_all": inv_all,
            "inventory_4_5_star": inv_45,
            "inventory_3_star": inv_3,
            "inventory_1_2_star": inv_12,
        })

    result = pd.DataFrame(rows)

    # Sort by inventory (descending) to match Summary Pull rank order
    result = result.sort_values("inventory_all", ascending=False).reset_index(drop=True)
    result.index = result.index + 1  # 1-based rank
    result.index.name = "rank"

    return result


# ---------------------------------------------------------------------------
# Peer group membership
# ---------------------------------------------------------------------------

def get_peer_groups(classifications: pd.DataFrame) -> dict:
    """
    Generate peer group membership lists from the classification table.

    Returns a dict where keys are peer group names and values are lists
    of market names belonging to that group.

    Peer group schemes:
      1. Inventory Tier
      2. Specific Region
      3. General Region
      4. Inventory Tier + General Region (composite)
    """
    groups = {}

    # Scheme 1: Inventory Tier
    for tier in classifications["inventory_tier"].unique():
        markets = classifications.loc[
            classifications["inventory_tier"] == tier, "market"
        ].tolist()
        groups[tier] = markets

    # Scheme 2: Specific Region
    for region in classifications["specific_region"].unique():
        markets = classifications.loc[
            classifications["specific_region"] == region, "market"
        ].tolist()
        groups[region] = markets

    # Scheme 3: General Region
    for region in classifications["general_region"].unique():
        markets = classifications.loc[
            classifications["general_region"] == region, "market"
        ].tolist()
        groups[region] = markets

    # Scheme 4: Inventory Tier + General Region (composite)
    for tier in classifications["inventory_tier"].unique():
        for region in classifications["general_region"].unique():
            mask = (
                (classifications["inventory_tier"] == tier)
                & (classifications["general_region"] == region)
            )
            markets = classifications.loc[mask, "market"].tolist()
            if markets:
                key = f"{tier} + {region}"
                groups[key] = markets

    return groups


def get_market_peer_groups(
    market: str,
    classifications: pd.DataFrame,
) -> dict:
    """
    Get all peer groups that a specific market belongs to.

    Returns a dict: {scheme_name: group_name}
    """
    row = classifications.loc[classifications["market"] == market]
    if row.empty:
        return {}

    row = row.iloc[0]
    return {
        "inventory_tier": row["inventory_tier"],
        "specific_region": row["specific_region"],
        "general_region": row["general_region"],
        "tier_region": f"{row['inventory_tier']} + {row['general_region']}",
    }


# ---------------------------------------------------------------------------
# Summary statistics (replaces COUNTIF formulas in rows 6-11)
# ---------------------------------------------------------------------------

def get_summary_stats(classifications: pd.DataFrame) -> dict:
    """
    Compute summary counts by tier and region, replicating the
    COUNTIF formulas in the Summary Pull header section.
    """
    total = len(classifications)

    stats = {
        "total_markets": total,
        "by_inventory_tier": {},
        "by_specific_region": {},
        "by_general_region": {},
    }

    for tier, count in classifications["inventory_tier"].value_counts().items():
        stats["by_inventory_tier"][tier] = {
            "count": int(count),
            "pct": round(count / total, 4),
        }

    for region, count in classifications["specific_region"].value_counts().items():
        stats["by_specific_region"][region] = {
            "count": int(count),
            "pct": round(count / total, 4),
        }

    for region, count in classifications["general_region"].value_counts().items():
        stats["by_general_region"][region] = {
            "count": int(count),
            "pct": round(count / total, 4),
        }

    return stats


# ---------------------------------------------------------------------------
# Validation — export to Excel for cell-by-cell comparison
# ---------------------------------------------------------------------------

def validate_against_excel(
    classifications: pd.DataFrame,
    output_path: str | Path = "validation_summary_pull.xlsx",
):
    """
    Export the classification table to Excel so your team can compare
    it cell-by-cell against the Summary Pull workbook.
    """
    output_path = Path(output_path)

    # Create a DataFrame that mirrors the Summary Pull layout
    export = classifications.copy()
    export = export.reset_index()

    export.to_excel(output_path, index=False, sheet_name="Summary Pull Validation")
    print(f"Validation file saved: {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python market_classifier.py <path_to_costar_export.xlsx>")
        sys.exit(1)

    filepath = sys.argv[1]

    print("Step 1: Loading CoStar data...")
    df = load_costar_export(filepath)

    print("\nStep 2: Classifying markets...")
    classifications = classify_markets(df)

    print(f"\n--- Classification Table (top 15) ---")
    print(classifications.head(15).to_string())

    print(f"\n--- Summary Statistics ---")
    stats = get_summary_stats(classifications)
    print(f"Total markets: {stats['total_markets']}")

    print(f"\nBy Inventory Tier:")
    for tier, info in sorted(stats["by_inventory_tier"].items()):
        print(f"  {tier:15s}: {info['count']:3d} markets ({info['pct']:.1%})")

    print(f"\nBy Specific Region:")
    for region, info in sorted(stats["by_specific_region"].items()):
        print(f"  {region:15s}: {info['count']:3d} markets ({info['pct']:.1%})")

    print(f"\nBy General Region:")
    for region, info in sorted(stats["by_general_region"].items()):
        print(f"  {region:15s}: {info['count']:3d} markets ({info['pct']:.1%})")

    print(f"\n--- Peer Groups ---")
    peer_groups = get_peer_groups(classifications)
    print(f"Total peer groups: {len(peer_groups)}")
    for group_name, markets in sorted(peer_groups.items()):
        print(f"  {group_name:30s}: {len(markets):3d} markets")

    print(f"\n--- Sample: New York peer groups ---")
    ny_groups = get_market_peer_groups("New York - NY USA", classifications)
    for scheme, group in ny_groups.items():
        print(f"  {scheme}: {group}")

    # Export validation file
    validate_against_excel(classifications)
