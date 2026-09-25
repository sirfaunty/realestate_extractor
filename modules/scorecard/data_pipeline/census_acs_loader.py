"""
census_acs_loader.py — Census American Community Survey Data Loader

Pulls demographic data from the Census Bureau's ACS API to enrich the
CoStar Market Scorecard with age-cohort and housing tenure breakdowns.

Key data series:
  - Population by age cohort (5-year ACS, Table B01001)
  - Housing tenure by age (owner vs. renter, Table B25007)
  - Renter-propensity-weighted population growth

The Census ACS API is free and does not require an API key for most
endpoints (though having one removes the 500-request/day limit).

Census ACS API docs: https://www.census.gov/data/developers/data-sets/acs-5year.html

Target cohorts for apartment demand analysis:
  - Age 20-34: Peak renter years (~65% renter rate nationally)
  - Age 35-54: Transitional cohort (~40% renter rate)
  - Age 55+:   Growing renter segment (downsizers, ~20% renter rate)

Output: DataFrame with columns matching the engine's long format:
    market, concept, quarter, value

Note: ACS data is annual (released ~September for prior year).
We assign ACS data to Q4 of the reference year for alignment with
quarterly CoStar data.

Usage:
    loader = CensusACSLoader(api_key="optional_key")
    df = loader.load_age_cohort_data(year=2023)
"""

import json
import time
import logging
from typing import Optional
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

import pandas as pd
import numpy as np

from cbsa_crosswalk import (
    CBSA_TO_COSTAR,
    get_all_mappable_markets,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CENSUS_ACS_BASE_URL = "https://api.census.gov/data"

# ACS 5-Year Table B01001: Sex by Age
# Variables for key age cohorts (male + female combined)
# See: https://api.census.gov/data/2022/acs/acs5/variables.html
AGE_COHORT_VARIABLES = {
    # Total population
    "B01001_001E": "total_population",

    # Ages 20-24 (Male + Female)
    "B01001_008E": "male_20_24",
    "B01001_032E": "female_20_24",

    # Ages 25-29
    "B01001_009E": "male_25_29",
    "B01001_033E": "female_25_29",

    # Ages 30-34
    "B01001_010E": "male_30_34",
    "B01001_034E": "female_30_34",

    # Ages 35-39
    "B01001_011E": "male_35_39",
    "B01001_035E": "female_35_39",

    # Ages 40-44
    "B01001_012E": "male_40_44",
    "B01001_036E": "female_40_44",

    # Ages 45-49
    "B01001_013E": "male_45_49",
    "B01001_037E": "female_45_49",

    # Ages 50-54
    "B01001_014E": "male_50_54",
    "B01001_038E": "female_50_54",

    # Ages 55-59
    "B01001_015E": "male_55_59",
    "B01001_039E": "female_55_59",

    # Ages 60-64
    "B01001_016E": "male_60_64",
    "B01001_040E": "female_60_64",

    # Ages 65-69
    "B01001_017E": "male_65_69",
    "B01001_041E": "female_65_69",

    # Ages 70-74
    "B01001_018E": "male_70_74",
    "B01001_042E": "female_70_74",

    # Ages 75-79
    "B01001_019E": "male_75_79",
    "B01001_043E": "female_75_79",

    # Ages 80-84
    "B01001_020E": "male_80_84",
    "B01001_044E": "female_80_84",

    # Ages 85+
    "B01001_021E": "male_85_plus",
    "B01001_045E": "female_85_plus",
}

# National renter rates by age cohort (2022 ACS estimates)
# Source: Census Bureau Current Population Survey / Housing Vacancy Survey
NATIONAL_RENTER_RATES = {
    "20_34": 0.65,  # ~65% of 20-34 year olds rent
    "35_54": 0.40,  # ~40% of 35-54 year olds rent
    "55_plus": 0.22, # ~22% of 55+ rent (growing rapidly)
}

# Concept names for the scoring engine
CONCEPT_POP_20_34 = "Population Age 20-34"
CONCEPT_POP_35_54 = "Population Age 35-54"
CONCEPT_POP_55_PLUS = "Population Age 55+"
CONCEPT_RENTER_WEIGHTED_POP = "Renter-Weighted Population"
CONCEPT_POP_20_34_SHARE = "Population Age 20-34 Share"
CONCEPT_POP_55_PLUS_SHARE = "Population Age 55+ Share"
CONCEPT_POP_20_34_YOY = "Population Age 20-34 YoY Change"
CONCEPT_POP_55_PLUS_YOY = "Population Age 55+ YoY Change"
CONCEPT_RENTER_WEIGHTED_YOY = "Renter-Weighted Pop YoY Change"


# ---------------------------------------------------------------------------
# Census API Client
# ---------------------------------------------------------------------------

class CensusClient:
    """
    Lightweight Census API client using urllib.
    No API key required for basic access (500 requests/day limit).
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        self._last_request_time = 0.0

    def _rate_limit(self):
        elapsed = time.time() - self._last_request_time
        if elapsed < 0.5:
            time.sleep(0.5 - elapsed)

    def get_acs5_data(
        self,
        year: int,
        variables: list[str],
        geography: str = "metropolitan statistical area/micropolitan statistical area:*",
    ) -> list[list[str]]:
        """
        Query ACS 5-Year data for all MSAs.

        Args:
            year: ACS year (e.g., 2022 for 2018-2022 5-year estimates)
            variables: List of Census variable codes
            geography: Census geography specification

        Returns:
            List of lists (first row is header)
        """
        # Census API allows max ~50 variables per request
        from urllib.parse import quote
        var_str = ",".join(variables)
        geo_encoded = quote(geography)
        url = f"{CENSUS_ACS_BASE_URL}/{year}/acs/acs5?get={var_str}&for={geo_encoded}"

        if self.api_key:
            url += f"&key={self.api_key}"

        self._rate_limit()
        try:
            req = Request(url, headers={"User-Agent": "CoStarScorecard/1.0"})
            with urlopen(req, timeout=60) as resp:
                self._last_request_time = time.time()
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            logger.error(f"Census API error: HTTP {e.code} for year {year}")
            return []
        except URLError as e:
            logger.error(f"Census API connection error: {e.reason}")
            return []


# ---------------------------------------------------------------------------
# Data Processing
# ---------------------------------------------------------------------------

def _compute_age_cohorts(row_data: dict) -> dict:
    """
    Aggregate individual age bins into analysis cohorts.

    Args:
        row_data: Dict of variable_name → value for one MSA

    Returns:
        Dict with cohort populations
    """
    def safe_int(v):
        try:
            return int(v) if v and v != "null" else 0
        except (ValueError, TypeError):
            return 0

    pop_20_34 = sum([
        safe_int(row_data.get("male_20_24", 0)),
        safe_int(row_data.get("female_20_24", 0)),
        safe_int(row_data.get("male_25_29", 0)),
        safe_int(row_data.get("female_25_29", 0)),
        safe_int(row_data.get("male_30_34", 0)),
        safe_int(row_data.get("female_30_34", 0)),
    ])

    pop_35_54 = sum([
        safe_int(row_data.get("male_35_39", 0)),
        safe_int(row_data.get("female_35_39", 0)),
        safe_int(row_data.get("male_40_44", 0)),
        safe_int(row_data.get("female_40_44", 0)),
        safe_int(row_data.get("male_45_49", 0)),
        safe_int(row_data.get("female_45_49", 0)),
        safe_int(row_data.get("male_50_54", 0)),
        safe_int(row_data.get("female_50_54", 0)),
    ])

    pop_55_plus = sum([
        safe_int(row_data.get("male_55_59", 0)),
        safe_int(row_data.get("female_55_59", 0)),
        safe_int(row_data.get("male_60_64", 0)),
        safe_int(row_data.get("female_60_64", 0)),
        safe_int(row_data.get("male_65_69", 0)),
        safe_int(row_data.get("female_65_69", 0)),
        safe_int(row_data.get("male_70_74", 0)),
        safe_int(row_data.get("female_70_74", 0)),
        safe_int(row_data.get("male_75_79", 0)),
        safe_int(row_data.get("female_75_79", 0)),
        safe_int(row_data.get("male_80_84", 0)),
        safe_int(row_data.get("female_80_84", 0)),
        safe_int(row_data.get("male_85_plus", 0)),
        safe_int(row_data.get("female_85_plus", 0)),
    ])

    total_pop = safe_int(row_data.get("total_population", 0))

    # Renter-weighted population = sum of cohort × renter_rate
    renter_weighted = (
        pop_20_34 * NATIONAL_RENTER_RATES["20_34"]
        + pop_35_54 * NATIONAL_RENTER_RATES["35_54"]
        + pop_55_plus * NATIONAL_RENTER_RATES["55_plus"]
    )

    return {
        "total_population": total_pop,
        "pop_20_34": pop_20_34,
        "pop_35_54": pop_35_54,
        "pop_55_plus": pop_55_plus,
        "renter_weighted_pop": renter_weighted,
        "share_20_34": pop_20_34 / total_pop if total_pop > 0 else 0,
        "share_55_plus": pop_55_plus / total_pop if total_pop > 0 else 0,
    }


# ---------------------------------------------------------------------------
# Main Loader
# ---------------------------------------------------------------------------

class CensusACSLoader:
    """
    Loads Census ACS age cohort data and structures it for the scoring engine.

    Usage:
        loader = CensusACSLoader()
        df = loader.load_age_cohort_data(year=2023)
        multi_year_df = loader.load_multi_year(start_year=2015, end_year=2023)
    """

    def __init__(self, api_key: Optional[str] = None):
        self.client = CensusClient(api_key)

    def load_age_cohort_data(self, year: int) -> pd.DataFrame:
        """
        Load age cohort data for all MSAs for a single ACS year.

        Args:
            year: ACS 5-year estimate year (e.g., 2023 = 2019-2023 estimates)

        Returns:
            DataFrame with columns: market, concept, quarter, value
        """
        variables = list(AGE_COHORT_VARIABLES.keys())
        raw_data = self.client.get_acs5_data(year, variables)

        if not raw_data or len(raw_data) < 2:
            logger.error(f"No ACS data returned for {year}")
            return pd.DataFrame(columns=["market", "concept", "quarter", "value"])

        # Parse header and data rows
        header = raw_data[0]
        data_rows = raw_data[1:]

        # Find the CBSA code column (usually last column, labeled
        # "metropolitan statistical area/micropolitan statistical area")
        cbsa_col_idx = None
        for i, col in enumerate(header):
            if "metropolitan" in col.lower() or "micropolitan" in col.lower():
                cbsa_col_idx = i
                break

        if cbsa_col_idx is None:
            # Fallback: last column is typically the geography code
            cbsa_col_idx = len(header) - 1

        # Map variable codes to friendly names
        var_to_name = AGE_COHORT_VARIABLES
        var_indices = {}
        for i, col in enumerate(header):
            if col in var_to_name:
                var_indices[var_to_name[col]] = i

        # Assign ACS annual data to Q4 of that year
        quarter = f"{year} Q4"

        rows = []
        for data_row in data_rows:
            cbsa_code = data_row[cbsa_col_idx]

            # Look up CoStar market name
            costar_name = CBSA_TO_COSTAR.get(cbsa_code, "")
            if not costar_name:
                continue  # Not a market we track

            # Extract variable values
            row_data = {}
            for name, idx in var_indices.items():
                row_data[name] = data_row[idx]

            # Compute cohorts
            cohorts = _compute_age_cohorts(row_data)

            # Add rows for each concept
            rows.extend([
                {"market": costar_name, "concept": CONCEPT_POP_20_34,
                 "quarter": quarter, "value": cohorts["pop_20_34"]},
                {"market": costar_name, "concept": CONCEPT_POP_35_54,
                 "quarter": quarter, "value": cohorts["pop_35_54"]},
                {"market": costar_name, "concept": CONCEPT_POP_55_PLUS,
                 "quarter": quarter, "value": cohorts["pop_55_plus"]},
                {"market": costar_name, "concept": CONCEPT_RENTER_WEIGHTED_POP,
                 "quarter": quarter, "value": cohorts["renter_weighted_pop"]},
                {"market": costar_name, "concept": CONCEPT_POP_20_34_SHARE,
                 "quarter": quarter, "value": cohorts["share_20_34"]},
                {"market": costar_name, "concept": CONCEPT_POP_55_PLUS_SHARE,
                 "quarter": quarter, "value": cohorts["share_55_plus"]},
            ])

        return pd.DataFrame(rows)

    def load_multi_year(
        self,
        start_year: int = 2015,
        end_year: int = 2023,
    ) -> pd.DataFrame:
        """
        Load age cohort data across multiple ACS years and compute YoY changes.

        ACS 5-year estimates are released annually, so we can track trends.
        Note: 5-year windows overlap (e.g., 2022 = 2018-2022, 2023 = 2019-2023),
        so year-over-year changes reflect marginal shifts, not independent samples.

        Args:
            start_year: First ACS year to load
            end_year: Last ACS year to load

        Returns:
            DataFrame with level and YoY change concepts
        """
        all_frames = []

        for year in range(start_year, end_year + 1):
            logger.info(f"Loading ACS {year}...")
            df = self.load_age_cohort_data(year)
            if len(df) > 0:
                all_frames.append(df)
            time.sleep(1)  # Be nice to the Census API

        if not all_frames:
            return pd.DataFrame(columns=["market", "concept", "quarter", "value"])

        combined = pd.concat(all_frames, ignore_index=True)

        # Compute YoY changes for key cohorts
        yoy_rows = []
        for concept, yoy_concept in [
            (CONCEPT_POP_20_34, CONCEPT_POP_20_34_YOY),
            (CONCEPT_POP_55_PLUS, CONCEPT_POP_55_PLUS_YOY),
            (CONCEPT_RENTER_WEIGHTED_POP, CONCEPT_RENTER_WEIGHTED_YOY),
        ]:
            cohort_df = combined[combined["concept"] == concept]
            for market in cohort_df["market"].unique():
                market_df = cohort_df[cohort_df["market"] == market].sort_values("quarter")
                values = market_df.set_index("quarter")["value"]

                for i in range(1, len(values)):
                    prev_val = values.iloc[i - 1]
                    curr_val = values.iloc[i]
                    if prev_val > 0:
                        yoy = (curr_val - prev_val) / prev_val
                        yoy_rows.append({
                            "market": market,
                            "concept": yoy_concept,
                            "quarter": values.index[i],
                            "value": yoy,
                        })

        if yoy_rows:
            combined = pd.concat(
                [combined, pd.DataFrame(yoy_rows)], ignore_index=True
            )

        return combined

    def print_cohort_summary(self, df: pd.DataFrame, year: Optional[int] = None):
        """Print a summary of age cohort data."""
        if year:
            quarter = f"{year} Q4"
            df = df[df["quarter"] == quarter]

        print("\n" + "=" * 70)
        print("Census ACS Age Cohort Summary")
        print("=" * 70)

        markets = df["market"].nunique()
        print(f"Markets with data: {markets}")

        for concept in [CONCEPT_POP_20_34_SHARE, CONCEPT_POP_55_PLUS_SHARE]:
            concept_df = df[df["concept"] == concept]
            if len(concept_df) > 0:
                print(f"\n{concept}:")
                top5 = concept_df.nlargest(5, "value")
                for _, row in top5.iterrows():
                    print(f"  {row['market']:<35} {row['value']:.1%}")

                bottom5 = concept_df.nsmallest(5, "value")
                print(f"  ...")
                for _, row in bottom5.iterrows():
                    print(f"  {row['market']:<35} {row['value']:.1%}")

        print("=" * 70)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    api_key = sys.argv[1] if len(sys.argv) > 1 else None

    loader = CensusACSLoader(api_key=api_key)

    # Load most recent year
    print("Loading ACS 2023 age cohort data...")
    df = loader.load_age_cohort_data(2023)

    if len(df) > 0:
        print(f"Loaded {len(df)} data points for {df['market'].nunique()} markets")
        loader.print_cohort_summary(df)

        # Save to CSV
        output_path = Path(__file__).parent / "output" / "census_age_cohorts.csv"
        output_path.parent.mkdir(exist_ok=True)
        df.to_csv(output_path, index=False)
        print(f"\nData saved to {output_path}")
    else:
        print("No data returned. Check Census API availability.")
