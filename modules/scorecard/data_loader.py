"""
data_loader.py — Module 1 of the CoStar Market Scorecard Engine

Imports CoStar quarterly Excel export files and normalizes the wide
time-series format into a clean, long-format DataFrame suitable for
analysis.

Source workbook: "Costar Q4 Data Export File" (the 4-sheet export)
Sheets consumed: All Units, 4 & 5 Star, 3 Star, 1 & 2 Star

Output: A single DataFrame with columns:
    market, property_class, concept, quarter, value

This replaces the INDEX/MATCH lookups used by the Summary Pull and
all 11 Detail workbooks. Once data is in this format, any metric
for any market/class/quarter is a simple filter — no formulas needed.
"""

import pandas as pd
import numpy as np
import pickle
import hashlib
from pathlib import Path


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SHEET_NAMES = ["All Units", "4 & 5 Star", "3 Star", "1 & 2 Star"]

METADATA_COLS = {
    "B": "property_class",
    "C": "slice",
    "D": "as_of",
    "E": "market",
    "F": "geography_code",
    "G": "concept",
}

# Columns A-G are metadata; H onward are quarterly values.
FIRST_DATA_COL = 7  # 0-indexed column H


# ---------------------------------------------------------------------------
# Core loader
# ---------------------------------------------------------------------------

def load_costar_export(filepath: str | Path) -> pd.DataFrame:
    """
    Read a CoStar quarterly export Excel file and return a normalized
    long-format DataFrame.

    Parameters
    ----------
    filepath : path to the .xlsx export file

    Returns
    -------
    pd.DataFrame with columns:
        market          str   — e.g. "New York - NY USA"
        property_class  str   — "All", "4 & 5 Star", "3 Star", "1 & 2 Star"
        concept         str   — e.g. "Vacancy Rate", "Inventory Units"
        quarter         str   — e.g. "2025 Q4"
        value           float — the data value (NaN where missing)
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"CoStar export not found: {filepath}")

    # Check for pickle cache (much faster than re-parsing 61MB Excel)
    cache_path = filepath.with_suffix('.pkl')
    if cache_path.exists() and cache_path.stat().st_mtime >= filepath.stat().st_mtime:
        print(f"  Loading from cache: {cache_path.name}")
        try:
            return pickle.loads(cache_path.read_bytes())
        except Exception as e:
            print(f"  Cache load failed ({e}), re-parsing Excel...")

    frames = []
    for sheet in SHEET_NAMES:
        print(f"  Loading sheet: {sheet}...")
        df_wide = pd.read_excel(
            filepath,
            sheet_name=sheet,
            header=None,       # We'll handle headers manually
            dtype=str,         # Read everything as string first
        )

        # Row 0 is the header row (A=blank, B=Property Class Name, ..., H=1982 Q1, etc.)
        # Extract quarter labels from row 0, columns H onward
        quarter_labels = df_wide.iloc[0, FIRST_DATA_COL:].values

        # Data rows start at row 1
        data_rows = df_wide.iloc[1:].copy()
        data_rows.reset_index(drop=True, inplace=True)

        # Extract metadata
        markets = data_rows.iloc[:, 4].values    # Column E = market
        concepts = data_rows.iloc[:, 6].values   # Column G = concept

        # Determine property class from the slice column (C)
        property_class = data_rows.iloc[:, 2].values  # Column C = slice

        # Extract the numeric data block (H onward)
        value_block = data_rows.iloc[:, FIRST_DATA_COL:]
        value_block.columns = quarter_labels

        # Melt from wide to long
        value_block = value_block.copy()
        value_block["market"] = markets
        value_block["property_class"] = property_class
        value_block["concept"] = concepts

        df_long = value_block.melt(
            id_vars=["market", "property_class", "concept"],
            var_name="quarter",
            value_name="value",
        )

        frames.append(df_long)

    # Combine all sheets
    result = pd.concat(frames, ignore_index=True)

    # Clean up
    result["quarter"] = result["quarter"].astype(str).str.strip()
    result["market"] = result["market"].astype(str).str.strip()
    result["concept"] = result["concept"].astype(str).str.strip()
    result["property_class"] = result["property_class"].astype(str).str.strip()
    result["value"] = pd.to_numeric(result["value"], errors="coerce")

    # Drop rows where quarter is NaN or "nan" (trailing empty columns)
    result = result[
        result["quarter"].notna()
        & (result["quarter"] != "nan")
        & (result["quarter"] != "None")
    ].copy()

    # Drop rows where market is missing
    result = result[result["market"].notna() & (result["market"] != "nan")].copy()

    result.reset_index(drop=True, inplace=True)

    print(f"\n  Loaded {len(result):,} records")
    print(f"  Markets: {result['market'].nunique()}")
    print(f"  Property classes: {sorted(result['property_class'].unique())}")
    print(f"  Concepts: {result['concept'].nunique()}")
    print(f"  Quarters: {result['quarter'].nunique()} "
          f"({result['quarter'].min()} to {result['quarter'].max()})")

    # Build a MultiIndex for O(1) lookups instead of full-table scans.
    # This is the key performance optimization — lookup() uses .loc on
    # this index instead of boolean masking 8M+ rows every call.
    print("  Building lookup index...")
    result = result.set_index(["market", "property_class", "concept", "quarter"])
    result = result.sort_index()
    print("  Index ready.")

    # Save pickle cache for fast subsequent loads
    try:
        cache_path.write_bytes(pickle.dumps(result))
        print(f"  Saved cache: {cache_path.name}")
    except Exception as e:
        print(f"  Cache save skipped: {e}")

    return result


# ---------------------------------------------------------------------------
# Convenience query function (replaces INDEX/MATCH)
# ---------------------------------------------------------------------------

def lookup(
    df: pd.DataFrame,
    market: str,
    property_class: str,
    concept: str,
    quarter: str | None = None,
) -> pd.DataFrame | float:
    """
    Look up data from the normalized DataFrame.

    This replaces the INDEX/MATCH pattern used throughout the Excel workbooks:
        =IFERROR(INDEX(sheet!$1:$1048576,
                        MATCH(key, sheet!$A:$A, 0),
                        MATCH(quarter, sheet!$1:$1, 0)), 0)

    Parameters
    ----------
    df             : the DataFrame from load_costar_export()
    market         : e.g. "New York - NY USA"
    property_class : "All", "4 & 5 Star", "3 Star", or "1 & 2 Star"
    concept        : e.g. "Absorption Units"
    quarter        : e.g. "2025 Q4" — if None, returns all quarters

    Returns
    -------
    float if quarter is specified, otherwise a DataFrame of (quarter, value)
    """
    try:
        if quarter is not None:
            val = df.loc[(market, property_class, concept, quarter), "value"]
            # loc can return a scalar or a Series if there are duplicates
            if isinstance(val, pd.Series):
                return val.iloc[0]
            return float(val)
        else:
            subset = df.loc[(market, property_class, concept), :]
            if isinstance(subset, pd.Series):
                # Single row result
                return pd.DataFrame(
                    {"quarter": [subset.name[-1]], "value": [subset["value"]]}
                )
            result = subset.reset_index()
            return result[["quarter", "value"]].sort_values("quarter").reset_index(drop=True)
    except KeyError:
        if quarter is not None:
            return 0.0  # Matches IFERROR(..., 0) behavior in Excel
        return pd.DataFrame(columns=["quarter", "value"])


def get_time_series(
    df: pd.DataFrame,
    market: str,
    property_class: str,
    concept: str,
    start_quarter: str | None = None,
    end_quarter: str | None = None,
) -> pd.Series:
    """
    Get a time series for a specific market/class/concept, indexed by quarter.

    Parameters
    ----------
    df             : the DataFrame from load_costar_export()
    market         : e.g. "New York - NY USA"
    property_class : "All", "4 & 5 Star", etc.
    concept        : e.g. "Vacancy Rate"
    start_quarter  : optional filter, e.g. "2000 Q1"
    end_quarter    : optional filter, e.g. "2025 Q4"

    Returns
    -------
    pd.Series indexed by quarter string, values are floats
    """
    result = lookup(df, market, property_class, concept)
    series = result.set_index("quarter")["value"]

    if start_quarter:
        series = series[series.index >= start_quarter]
    if end_quarter:
        series = series[series.index <= end_quarter]

    return series


# ---------------------------------------------------------------------------
# Bulk retrieval for engine operations
# ---------------------------------------------------------------------------

def get_metric_matrix(
    df: pd.DataFrame,
    property_class: str,
    concept: str,
    start_quarter: str | None = None,
    end_quarter: str | None = None,
) -> pd.DataFrame:
    """
    Get a markets x quarters matrix for a given property class and concept.
    This is the pandas equivalent of an entire Detail workbook's data pull
    section — all 387 markets at once.

    Returns
    -------
    pd.DataFrame with market names as index, quarter strings as columns,
    values as floats. Sorted by quarter chronologically.
    """
    # Use cross-section on the index for fast retrieval
    try:
        subset = df.xs(property_class, level="property_class").xs(concept, level="concept")
    except KeyError:
        return pd.DataFrame()

    subset = subset.reset_index()  # brings market and quarter back as columns

    if start_quarter:
        subset = subset[subset["quarter"] >= start_quarter]
    if end_quarter:
        subset = subset[subset["quarter"] <= end_quarter]

    matrix = subset.pivot(index="market", columns="quarter", values="value")

    # Sort columns chronologically
    matrix = matrix.reindex(sorted(matrix.columns), axis=1)

    return matrix


# ---------------------------------------------------------------------------
# Main — run standalone to test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python data_loader.py <path_to_costar_export.xlsx>")
        sys.exit(1)

    filepath = sys.argv[1]
    print(f"Loading CoStar export: {filepath}")
    df = load_costar_export(filepath)

    # Quick validation
    print("\n--- Sample lookups ---")
    val = lookup(df, "New York - NY USA", "All", "Inventory Units", "2025 Q4")
    print(f"NY All Inventory Units 2025 Q4: {val:,.0f}")

    val = lookup(df, "New York - NY USA", "All", "Vacancy Rate", "2025 Q4")
    print(f"NY All Vacancy Rate 2025 Q4: {val:.4%}")

    val = lookup(df, "New York - NY USA", "4 & 5 Star", "Market Asking Rent/Unit", "2025 Q4")
    print(f"NY 4&5 Star Asking Rent 2025 Q4: ${val:,.2f}")

    print("\n--- Time series sample ---")
    ts = get_time_series(df, "New York - NY USA", "All", "Vacancy Rate",
                         start_quarter="2023 Q1", end_quarter="2025 Q4")
    print(ts)

    print("\n--- Metric matrix sample (first 5 markets, last 4 quarters) ---")
    matrix = get_metric_matrix(df, "All", "Vacancy Rate",
                               start_quarter="2025 Q1", end_quarter="2025 Q4")
    print(matrix.head())
