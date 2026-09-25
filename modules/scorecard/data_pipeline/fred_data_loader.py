"""
fred_data_loader.py — FRED/Census Data Integration Module

Pulls external economic data from the FRED API (Federal Reserve Bank of
St. Louis) to enrich the CoStar Market Scorecard Engine with supplemental
indicators not available in the CoStar export.

Current data series:
  - Single-family building permits by MSA (monthly → quarterly aggregation)
  - Single-family housing completions (where available)

Planned extensions:
  - Census ACS age cohort data (20-34, 55+ renter-propensity weighting)
  - IRS migration flow data
  - FHFA House Price Index by MSA

Output: DataFrame with columns matching the engine's long format:
    market, concept, quarter, value

Usage:
    loader = FREDDataLoader(api_key="your_key_here")
    df = loader.load_all_permit_data()
    # Returns quarterly SF permit counts for all mapped MSAs
"""

import json
import time
import pickle
import hashlib
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

import pandas as pd
import numpy as np

from cbsa_crosswalk import (
    CBSA_TO_COSTAR,
    COSTAR_NAME_ALIASES,
    COSTAR_SUBMARKET_TO_PARENT_CBSA,
    get_cbsa_for_market,
    get_all_mappable_markets,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FRED_BASE_URL = "https://api.stlouisfed.org/fred"

# FRED Release ID for Building Permits
BUILDING_PERMITS_RELEASE_ID = 148

# FRED series IDs use non-obvious naming (e.g., ATLA013BP1FH for Atlanta).
# We discover them via the FRED API's release/series + search endpoints,
# then cache the mapping. The suffix "BP1FH" = Building Permits, 1-unit,
# Family Housing. "BP1FHSA" = same but Seasonally Adjusted.
SF_PERMIT_SUFFIX = "BP1FH"       # Not seasonally adjusted (raw counts)
SF_PERMIT_SUFFIX_SA = "BP1FHSA"  # Seasonally adjusted

# Manual overrides for markets where automated matching fails.
# Maps CoStar market name → known FRED series ID.
# Built by hand-verifying FRED series pages.
MANUAL_SERIES_OVERRIDES = {
    # FRED uses "Boise City" not "Boise"
    "Boise - ID": "BOIS216BP1FH",
    # CoStar splits these from parent MSAs — use parent series
    "Fort Lauderdale - FL": "MIAM412BP1FH",   # Part of Miami MSA
    "Palm Beach - FL": "MIAM412BP1FH",         # Part of Miami MSA
    "East Bay - CA": "SANF806BP1FH",           # Part of SF Bay Area
    "Orange County - CA": "LOSA706BP1FH",      # Part of LA MSA
    "Long Island - NY": "NEWY636BP1FH",        # Part of NY MSA
    "Northern New Jersey - NJ": "NEWY636BP1FH",# Part of NY MSA
    "Inland Empire - CA": "RIVE906BP1FH",      # Riverside-San Bernardino
    "Fort Myers - FL": "CAPE212BP1FH",          # Cape Coral-Fort Myers
    "Sarasota - FL": "NOPO412BP1FH",           # North Port-Sarasota
    "Melbourne - FL": "PALM112BP1FH",          # Palm Bay-Melbourne
    "Daytona Beach - FL": "DELT012BP1FH",      # Deltona-Daytona Beach
    # Multi-state MSAs where state code matching fails
    "Boston - MA": "BOST625BP1FH",             # MA-NH
    "Providence - RI": "PROV644BP1FH",         # RI-MA
    "Louisville - KY": "LOUI621BP1FH",         # KY-IN
    "Cincinnati - OH": "CINC739BP1FH",         # OH-KY-IN
    "Memphis - TN": "MEMP747BP1FH",            # TN-MS-AR
    "St. Louis - MO": "STLO729BP1FH",          # MO-IL
    "Kansas City - MO": "KANS729BP1FH",        # MO-KS
    "Omaha - NE": "OMAH831BP1FH",             # NE-IA
    "Portland - OR": "PORT841BP1FH",           # OR-WA
    "Minneapolis - MN": "MINN527BP1FH",        # MN-WI
    "Chattanooga - TN": "CHAT147BP1FH",        # TN-GA
    "Washington - DC": "WASH511BP1FH",         # DC-VA-MD-WV
    "Philadelphia - PA": "PHIL342BP1FH",       # PA-NJ-DE-MD
    "Virginia Beach - VA": "VIRG751BP1FH",     # VA-NC
    "Fargo - ND": "FARG838BP1FH",             # ND-MN
    "Evansville - IN": "EVAN618BP1FH",         # IN-KY
    "Davenport - IA": "DAVE719BP1FH",          # IA-IL
    "Huntington - WV": "HUNT754BP1FH",         # WV-KY-OH
    "Augusta - GA": "AUGU613BP1FH",            # GA-SC
    "Texarkana - TX": "TEXA748BP1FH",          # TX-AR
    "Clarksville - TN": "CLAR747BP1FH",        # TN-KY
    # Name mismatches between CoStar and Census MSA titles
    "Lakeland - FL": "LAKE112BP1FH",
    "Deltona-Daytona Beach - FL": "DELT012BP1FH",  # if CoStar uses this form
    "Provo - UT": "PROV849BP1FH",
    "Ogden - UT": "OGDE849BP1FH",
    "Myrtle Beach - SC": "MYRT745BP1FH",       # SC-NC
    "Greenville - SC": "GREE745BP1FH",
    "Charleston - SC": "CHAR745BP1FH",
    "Columbia - SC": "COLU745BP1FH",
    "Hilton Head Island - SC": "HILT045BP1FH",
    # Remaining MSA-level BP1FH series from FRED release 148
    "Fort Wayne - IN": "FORT018BP1FH",     # FRED title has lowercase "in"
    "Pueblo - CO": "PUEB308BP1FH",
    "Carson City - NV": "CARS132BP1FH",
    "Dover - DE": "DOVE110BP1FH",
    "Farmington - NM": "FARM135BP1FH",
    "Atlantic City - NJ": "ATLA134BP1FH",
    "Springfield - OH": "SPRI239BP1FH",
    "Harrisonburg - VA": "HARR551BP1FH",
    "Rome - GA": "ROME613BP1FH",
    "Vineland - NJ": "VINE234BP1FH",
    "Ocean City - NJ": "OCEA134BP1FH",
}

# Known FRED city name → CoStar market name mappings
# Used when the FRED title uses a different primary city name
FRED_CITY_TO_COSTAR = {
    "boise city": "Boise - ID",
    "urban honolulu": "Honolulu - HI",
    "cape coral": "Fort Myers - FL",
    "north port": "Sarasota - FL",
    "palm bay": "Melbourne - FL",
    "deltona": "Daytona Beach - FL",
    "riverside": "Inland Empire - CA",
    "oxnard": "Ventura - CA",
    "new haven": "New Haven - CT",
    "bridgeport": "Bridgeport - CT",
    "winston": "Winston-Salem - NC",
    "durham": "Durham - NC",
    "lakeland": "Lakeland - FL",
    "crestview": "Ft Walton Beach - FL",
    "prescott valley": "Prescott - AZ",
    "lake havasu city": "Lake Havasu - AZ",
    "santa maria": "Santa Barbara - CA",
    "sebastian": "Sebastian-Vero Beach - FL",
    "punta gorda": "Punta Gorda - FL",
}

# Markets that should NOT be matched via targeted search fallback
# because they share a city name with a larger MSA's series
TARGETED_SEARCH_BLACKLIST = {
    "York - PA",         # Would falsely match New York
    "Portland - ME",     # PORT941BP1FH is Portland, OR-WA
    "Jackson - MI",      # Would match Jackson, MS or Jackson, TN
    "Columbia - MO",     # CLMBP1FH is correct but verify it's actually MO
    "Springfield - IL",  # Could match Springfield OH/MO/MA
    "Springfield - MA",  # Could match Springfield OH/MO/IL
    "Springfield - MO",  # Could match Springfield OH/MA/IL
    "Rochester - MN",    # Would match Rochester, NY
    "Columbus - IN",     # Would match Columbus, OH
    "Albany - GA",       # Would match Albany, NY
    "Lancaster - PA",    # Verify not matched to wrong series
}

# Additional FRED series suffixes for broader permit coverage
# BPPRIV = all private building permits (1-unit + multi-unit)
# Available for many more MSAs than BP1FH
ALL_PERMIT_SUFFIX = "BPPRIV"       # Not seasonally adjusted
ALL_PERMIT_SUFFIX_SA = "BPPRIVSA"  # Seasonally adjusted

# Concept names (must be unique, used as keys in the scoring engine)
CONCEPT_SF_PERMITS = "SF Building Permits"
CONCEPT_ALL_PERMITS = "All Building Permits"
CONCEPT_SF_PERMITS_YOY = "SF Permits YoY Change"
CONCEPT_ALL_PERMITS_YOY = "All Permits YoY Change"

# Rate limiting: FRED allows 120 requests/minute
REQUEST_DELAY_SECONDS = 0.55  # ~109 requests/minute, safe margin

# Cache configuration
CACHE_DIR = Path(__file__).parent / "cache"
CACHE_FILE = CACHE_DIR / "fred_data.pkl"
CACHE_EXPIRY_HOURS = 168  # 1 week — permits are monthly data, no need to refresh often


# ---------------------------------------------------------------------------
# FRED API Client
# ---------------------------------------------------------------------------

class FREDClient:
    """
    Lightweight FRED API v1 client using urllib (no external dependencies).
    Handles rate limiting, retries, and JSON parsing.
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._last_request_time = 0.0

    def _rate_limit(self):
        """Enforce minimum delay between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < REQUEST_DELAY_SECONDS:
            time.sleep(REQUEST_DELAY_SECONDS - elapsed)

    def _request(self, endpoint: str, params: dict, max_retries: int = 3) -> dict:
        """
        Make a GET request to the FRED API.

        Args:
            endpoint: API endpoint (e.g., "series/observations")
            params: Query parameters (api_key is added automatically)
            max_retries: Number of retries on transient failures

        Returns:
            Parsed JSON response as dict
        """
        params["api_key"] = self.api_key
        params["file_type"] = "json"
        url = f"{FRED_BASE_URL}/{endpoint}?{urlencode(params)}"

        for attempt in range(max_retries):
            self._rate_limit()
            try:
                req = Request(url, headers={"User-Agent": "CoStarScorecard/1.0"})
                with urlopen(req, timeout=30) as resp:
                    self._last_request_time = time.time()
                    import json as _json
                    return _json.loads(resp.read().decode("utf-8"))
            except HTTPError as e:
                if e.code == 429:  # Rate limited
                    wait = 2 ** attempt * 5
                    logger.warning(f"Rate limited, waiting {wait}s (attempt {attempt+1})")
                    time.sleep(wait)
                elif e.code == 400:
                    # Bad request — likely invalid series ID
                    logger.debug(f"Series not found: {url}")
                    return {"error": f"HTTP {e.code}", "observations": []}
                else:
                    logger.error(f"HTTP {e.code} for {endpoint}: {e.reason}")
                    if attempt == max_retries - 1:
                        return {"error": f"HTTP {e.code}", "observations": []}
            except URLError as e:
                logger.error(f"Connection error for {endpoint}: {e.reason}")
                if attempt == max_retries - 1:
                    return {"error": str(e), "observations": []}
                time.sleep(2 ** attempt)

        return {"error": "max retries exceeded", "observations": []}

    def get_series_info(self, series_id: str) -> dict:
        """Get metadata for a FRED series."""
        return self._request("series", {"series_id": series_id})

    def get_observations(
        self,
        series_id: str,
        start_date: str = "2000-01-01",
        end_date: Optional[str] = None,
    ) -> list:
        """
        Get observation data for a FRED series.

        Args:
            series_id: FRED series identifier
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD), defaults to today

        Returns:
            List of {"date": str, "value": str} dicts
        """
        params = {
            "series_id": series_id,
            "observation_start": start_date,
        }
        if end_date:
            params["observation_end"] = end_date

        result = self._request("series/observations", params)

        if "error" in result and "observations" not in result:
            return []

        return result.get("observations", [])

    def test_connection(self) -> bool:
        """Test API key validity with a known series."""
        result = self._request("series", {"series_id": "GDP"})
        return "error" not in result

    def search_series(self, search_text: str, limit: int = 1000) -> list:
        """
        Search for FRED series by text.

        Args:
            search_text: Search query
            limit: Max results to return

        Returns:
            List of series metadata dicts
        """
        result = self._request("series/search", {
            "search_text": search_text,
            "limit": str(limit),
        })
        return result.get("seriess", [])

    def get_release_series(self, release_id: int, offset: int = 0,
                           limit: int = 1000) -> list:
        """
        Get all series in a FRED release.

        Args:
            release_id: FRED release ID (148 = Building Permits)
            offset: Pagination offset
            limit: Results per page (max 1000)

        Returns:
            List of series metadata dicts
        """
        result = self._request("release/series", {
            "release_id": str(release_id),
            "offset": str(offset),
            "limit": str(limit),
        })
        return result.get("seriess", [])


# ---------------------------------------------------------------------------
# Data Processing
# ---------------------------------------------------------------------------

def _month_to_quarter(date_str: str) -> str:
    """
    Convert a date string (YYYY-MM-DD) to quarter format (YYYY QN).
    Matches the CoStar quarter format used in the engine.

    Examples:
        "2024-01-01" → "2024 Q1"
        "2024-06-01" → "2024 Q2"
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    quarter = (dt.month - 1) // 3 + 1
    return f"{dt.year} Q{quarter}"


def _aggregate_monthly_to_quarterly(observations: list) -> dict:
    """
    Aggregate monthly FRED observations to quarterly totals.

    For permit data, quarterly total = sum of 3 monthly values.
    Missing values (".") are treated as NaN and excluded from sums.

    Args:
        observations: List of {"date": str, "value": str} from FRED

    Returns:
        Dict of {quarter_str: float} (e.g., {"2024 Q1": 1234.0})
    """
    quarterly = {}
    quarterly_counts = {}

    for obs in observations:
        value_str = obs.get("value", ".")
        if value_str == ".":
            continue
        try:
            value = float(value_str)
        except (ValueError, TypeError):
            continue

        quarter = _month_to_quarter(obs["date"])

        if quarter not in quarterly:
            quarterly[quarter] = 0.0
            quarterly_counts[quarter] = 0
        quarterly[quarter] += value
        quarterly_counts[quarter] += 1

    # Only keep quarters with all 3 months reported
    return {q: v for q, v in quarterly.items() if quarterly_counts.get(q, 0) == 3}


# ---------------------------------------------------------------------------
# Main Data Loader
# ---------------------------------------------------------------------------

class FREDDataLoader:
    """
    Loads FRED economic data and structures it for the CoStar Scorecard Engine.

    Usage:
        loader = FREDDataLoader(api_key="your_32_char_key")

        # Test connection
        if loader.test_connection():
            print("API key is valid!")

        # Load all SF permit data (with caching)
        df = loader.load_all_permit_data()

        # Load for specific markets
        df = loader.load_permit_data_for_markets(["Atlanta - GA", "Dallas-Fort Worth - TX"])

        # Get a summary of data coverage
        loader.print_coverage_report()
    """

    def __init__(
        self,
        api_key: str,
        start_date: str = "2000-01-01",
        use_cache: bool = True,
    ):
        self.client = FREDClient(api_key)
        self.start_date = start_date
        self.use_cache = use_cache
        self._data_cache = {}
        self._failed_series = set()
        self._series_map = {}       # costar_name → FRED BP1FH series_id
        self._all_permits_map = {}  # costar_name → FRED BPPRIV series_id

    def test_connection(self) -> bool:
        """Test that the API key works."""
        return self.client.test_connection()

    # ----- Series Discovery -----

    def _discover_permit_series(self) -> dict:
        """
        Discover FRED series IDs for SF building permits by MSA.

        FRED uses non-obvious series IDs like ATLA013BP1FH (Atlanta)
        or DALL148BP1FH (Dallas). We search FRED to find all series
        ending in BP1FH (1-unit permits, not seasonally adjusted)
        and match them to our CoStar markets.

        Returns:
            Dict of {costar_market_name: fred_series_id}
        """
        # Check for cached series map
        series_cache = CACHE_DIR / "fred_series_map.json"
        if series_cache.exists():
            try:
                with open(series_cache) as f:
                    cached = json.load(f)
                if cached.get("version") == "5.0" and cached.get("map"):
                    logger.info(f"Loaded {len(cached['map'])} SF series mappings from cache")
                    self._all_permits_map = cached.get("all_permits_map", {})
                    if self._all_permits_map:
                        logger.info(f"Loaded {len(self._all_permits_map)} All-Permits mappings from cache")
                    return cached["map"]
            except Exception:
                pass

        logger.info("Discovering FRED series IDs for building permits...")
        logger.info("(This is a one-time operation, results will be cached)")

        # Strategy: Search for "building permits 1-unit" to find BP1FH series
        all_series = []

        # Search with multiple queries to maximize coverage
        search_queries = [
            "building permits 1-unit structures MSA",
            "BP1FH housing permits",
            "private housing units authorized 1-unit",
        ]

        for query in search_queries:
            results = self.client.search_series(query, limit=1000)
            all_series.extend(results)
            logger.info(f"  Search '{query}': {len(results)} results")

        # Also try paginating through release 148 (Building Permits)
        offset = 0
        while True:
            release_series = self.client.get_release_series(
                BUILDING_PERMITS_RELEASE_ID, offset=offset, limit=1000
            )
            if not release_series:
                break
            all_series.extend(release_series)
            logger.info(f"  Release 148 offset {offset}: {len(release_series)} series")
            if len(release_series) < 1000:
                break
            offset += 1000

        # Deduplicate by series ID
        seen = set()
        unique_series = []
        for s in all_series:
            sid = s.get("id", "")
            if sid and sid not in seen:
                seen.add(sid)
                unique_series.append(s)

        logger.info(f"Total unique series found: {len(unique_series)}")

        # Filter to BP1FH (1-unit, not seasonally adjusted) series
        bp1fh_series = [
            s for s in unique_series
            if s.get("id", "").endswith(SF_PERMIT_SUFFIX)
            and not s.get("id", "").endswith(SF_PERMIT_SUFFIX_SA)
        ]
        logger.info(f"SF permit series (BP1FH): {len(bp1fh_series)}")

        # Also find BPPRIV (all permits) series for broader coverage
        bppriv_series = [
            s for s in unique_series
            if s.get("id", "").endswith(ALL_PERMIT_SUFFIX)
            and not s.get("id", "").endswith(ALL_PERMIT_SUFFIX_SA)
        ]
        logger.info(f"All permit series (BPPRIV): {len(bppriv_series)}")

        # Match to CoStar markets using the series title
        # Titles look like: "New Private Housing Units Authorized by Building
        # Permits: 1-Unit Structures for Atlanta-Sandy Springs-Alpharetta, GA (MSA)"
        all_mappable = get_all_mappable_markets()
        series_map = {}
        all_permits_map = {}  # Separate map for BPPRIV series

        for series in bp1fh_series:
            title = series.get("title", "")
            series_id = series.get("id", "")

            # Try to match against CoStar market names
            matched_market = self._match_series_to_market(title, all_mappable)
            if matched_market:
                series_map[matched_market] = series_id

        logger.info(f"Matched {len(series_map)} SF series via title matching")

        # Match BPPRIV series
        for series in bppriv_series:
            title = series.get("title", "")
            series_id = series.get("id", "")
            matched_market = self._match_series_to_market(title, all_mappable)
            if matched_market:
                all_permits_map[matched_market] = series_id

        logger.info(f"Matched {len(all_permits_map)} All-Permits series via title matching")

        # Apply manual overrides for SF permits
        for costar_name, series_id in MANUAL_SERIES_OVERRIDES.items():
            if costar_name in all_mappable and costar_name not in series_map:
                series_map[costar_name] = series_id
                logger.debug(f"  Manual override: {costar_name} → {series_id}")

        logger.info(f"SF permits after manual overrides: {len(series_map)}")

        # Pass 2: Targeted search for still-unmatched markets
        unmatched = [m for m in all_mappable if m not in series_map]
        if unmatched:
            logger.info(f"Searching FRED for {len(unmatched)} unmatched markets...")
            newly_matched = self._targeted_series_search(unmatched, all_mappable)
            series_map.update(newly_matched)
            logger.info(f"Targeted search found {len(newly_matched)} more matches")
            logger.info(f"Final total: {len(series_map)} markets mapped")

        # Cache both mappings
        CACHE_DIR.mkdir(exist_ok=True)
        try:
            with open(series_cache, "w") as f:
                json.dump({
                    "version": "5.0",
                    "map": series_map,
                    "all_permits_map": all_permits_map,
                }, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not cache series map: {e}")

        self._all_permits_map = all_permits_map
        return series_map

    def _targeted_series_search(self, unmatched: list, all_mappable: dict) -> dict:
        """
        Search FRED for specific unmatched markets by city name.

        For each unmatched CoStar market, searches FRED for
        "building permits 1-unit {city_name}" and tries to match.

        Args:
            unmatched: List of unmatched CoStar market names
            all_mappable: Full dict of mappable markets

        Returns:
            Dict of {costar_name: fred_series_id} for newly matched markets
        """
        found = {}

        for i, market_name in enumerate(unmatched):
            # Skip markets known to produce false matches
            if market_name in TARGETED_SEARCH_BLACKLIST:
                continue

            parts = market_name.rsplit(" - ", 1)
            if len(parts) != 2:
                continue
            city = parts[0]
            state = parts[1]

            # Search FRED by city name
            search_query = f"building permits 1-unit {city} {state}"
            results = self.client.search_series(search_query, limit=20)

            # Filter to BP1FH series and try to match
            for series in results:
                sid = series.get("id", "")
                title = series.get("title", "")

                if not sid.endswith(SF_PERMIT_SUFFIX):
                    continue
                if sid.endswith(SF_PERMIT_SUFFIX_SA):
                    continue

                # Check if this series title matches our market
                matched = self._match_series_to_market(title, {market_name: ""})
                if matched:
                    found[market_name] = sid
                    logger.info(f"  Found: {market_name} → {sid}")
                    break

                # Fallback: if city name appears in title, accept it
                # But require the city to be at least 5 chars to avoid
                # false positives like "York" matching "New York"
                if (len(city) >= 5
                    and city.lower() in title.lower()
                    and f", {state}" in title):
                    # Extra check: make sure city isn't a substring of a larger word
                    title_lower = title.lower()
                    city_lower = city.lower()
                    idx = title_lower.find(city_lower)
                    # Reject if city is part of a longer word
                    if idx > 0 and title_lower[idx-1].isalpha():
                        continue
                    # Reject if city appears after "New " or "North " etc.
                    if idx >= 4 and title_lower[idx-4:idx] in ["new ", "old "]:
                        continue
                    found[market_name] = sid
                    logger.info(f"  Found (fallback): {market_name} → {sid}")
                    break

            # Progress update every 25 markets
            if (i + 1) % 25 == 0:
                logger.info(f"  Targeted search progress: {i+1}/{len(unmatched)}")

        return found

    def _extract_msa_name_and_states(self, title: str) -> tuple:
        """
        Extract MSA name and state codes from a FRED series title.

        Example input:
          "...for Nashville-Davidson--Murfreesboro--Franklin, TN (MSA)"
        Returns:
          ("nashville-davidson--murfreesboro--franklin", ["tn"])

        Example input:
          "...for Boston-Cambridge-Newton, MA-NH (MSA)"
        Returns:
          ("boston-cambridge-newton", ["ma", "nh"])
        """
        import re
        # Extract "for {MSA_NAME}, {STATES} (MSA)" pattern
        # Note: FRED occasionally uses lowercase state codes (e.g., "Fort Wayne, in")
        match = re.search(r'for\s+(.+?),\s*([A-Za-z][A-Za-z](?:-[A-Za-z][A-Za-z])*)\s*\(', title)
        if match:
            msa_name = match.group(1).lower().strip()
            states_str = match.group(2).lower()
            states = states_str.split("-")
            return msa_name, states

        # Fallback: try without (MSA) suffix
        match = re.search(r'for\s+(.+?),\s*([A-Za-z][A-Za-z](?:-[A-Za-z][A-Za-z])*)$', title)
        if match:
            msa_name = match.group(1).lower().strip()
            states = match.group(2).lower().split("-")
            return msa_name, states

        return "", []

    def _match_series_to_market(self, title: str, markets: dict) -> Optional[str]:
        """
        Match a FRED series title to a CoStar market name.

        Uses multiple strategies in order of precision:
        1. Known city name overrides (FRED_CITY_TO_COSTAR)
        2. Exact primary city + state match
        3. Hyphenated CoStar name partial match
        4. First word of FRED MSA name + state match
        5. Cross-state MSA matching (any state in the MSA)

        FRED title example:
          "New Private Housing Units Authorized by Building Permits:
           1-Unit Structures for Atlanta-Sandy Springs-Alpharetta, GA (MSA)"
        CoStar name: "Atlanta - GA"
        """
        msa_name, states = self._extract_msa_name_and_states(title)
        if not msa_name or not states:
            return None

        # Extract the primary city (first city in hyphenated MSA name)
        # Handle double hyphens: "Nashville-Davidson--Murfreesboro" → "Nashville-Davidson"
        # Split on double-hyphen first, then single hyphen for the first segment
        primary_segment = msa_name.split("--")[0].strip()
        # The primary city is the first part before any hyphen,
        # BUT some cities are hyphenated: "Dallas-Fort Worth", "Winston-Salem"
        # So we'll use the full primary segment as the primary city
        primary_city = primary_segment

        # Strategy 1: Check FRED city name overrides
        for fred_city, costar_name in FRED_CITY_TO_COSTAR.items():
            if msa_name.startswith(fred_city) and costar_name in markets:
                return costar_name

        # Strategy 2-5: Match against CoStar market names
        best_match = None

        for market_name in markets:
            parts = market_name.rsplit(" - ", 1)
            if len(parts) != 2:
                continue
            costar_city = parts[0].lower()
            costar_state = parts[1].lower()

            # Check if state matches (CoStar state in any of the FRED MSA states)
            state_match = costar_state in states

            if not state_match:
                continue

            # Strategy 2: Exact primary city match
            if costar_city == primary_city:
                return market_name

            # Strategy 3: CoStar city appears at start of FRED MSA name
            if msa_name.startswith(costar_city):
                return market_name

            # Strategy 4: CoStar city is contained in FRED MSA name
            # (handles "Fort Wayne" in "Fort Wayne-xxx")
            if costar_city in msa_name:
                best_match = market_name

            # Strategy 5: Hyphenated CoStar name — check first part
            if "-" in costar_city:
                costar_primary = costar_city.split("-")[0].strip()
                if msa_name.startswith(costar_primary):
                    return market_name

            # Strategy 6: First word match for single-word cities
            # "springfield" matches "Springfield-xxx, MO"
            if " " not in costar_city and "-" not in costar_city:
                first_word = msa_name.split("-")[0].split(" ")[0].strip()
                if costar_city == first_word and state_match:
                    best_match = market_name

        # Strategy 7: Cross-state MSA matching
        # For multi-state MSAs, try matching even if CoStar state
        # isn't the primary state listed
        if best_match is None and len(states) > 1:
            for market_name in markets:
                parts = market_name.rsplit(" - ", 1)
                if len(parts) != 2:
                    continue
                costar_city = parts[0].lower()
                costar_state = parts[1].lower()

                # Check if city matches but state is secondary
                if costar_city in msa_name or msa_name.startswith(costar_city):
                    best_match = market_name

        return best_match

    # ----- Permit Data Loading -----

    def _load_single_permit_series(
        self, series_id: str, costar_name: str
    ) -> Optional[pd.DataFrame]:
        """
        Load single-family permit data for one MSA by FRED series ID.

        Returns DataFrame with columns: market, concept, quarter, value
        or None if data unavailable.
        """
        # Check if we already know this series doesn't exist
        if series_id in self._failed_series:
            return None

        observations = self.client.get_observations(
            series_id, start_date=self.start_date
        )

        if not observations:
            self._failed_series.add(series_id)
            logger.debug(f"No data for {series_id} ({costar_name})")
            return None

        # Aggregate monthly → quarterly
        quarterly_data = _aggregate_monthly_to_quarterly(observations)

        if not quarterly_data:
            self._failed_series.add(series_id)
            return None

        # Build DataFrame rows
        rows = []
        for quarter, value in sorted(quarterly_data.items()):
            rows.append({
                "market": costar_name,
                "concept": CONCEPT_SF_PERMITS,
                "quarter": quarter,
                "value": value,
            })

        df = pd.DataFrame(rows)
        logger.info(
            f"Loaded {len(df)} quarters for {costar_name} ({series_id})"
        )
        return df

    def load_permit_data_for_markets(
        self, market_names: list[str]
    ) -> pd.DataFrame:
        """
        Load SF permit data for specific CoStar markets.

        Args:
            market_names: List of CoStar market names (e.g., ["Atlanta - GA"])

        Returns:
            DataFrame with columns: market, concept, quarter, value
        """
        # Discover series IDs if we haven't already
        if not self._series_map:
            self._series_map = self._discover_permit_series()

        all_frames = []

        for name in market_names:
            series_id = self._series_map.get(name)
            if not series_id:
                logger.warning(f"No FRED series found for {name}, skipping")
                continue

            df = self._load_single_permit_series(series_id, name)
            if df is not None:
                all_frames.append(df)

        if not all_frames:
            logger.warning("No permit data loaded for any requested market")
            return pd.DataFrame(columns=["market", "concept", "quarter", "value"])

        return pd.concat(all_frames, ignore_index=True)

    def load_all_permit_data(self, force_refresh: bool = False) -> pd.DataFrame:
        """
        Load SF permit data for ALL mapped CoStar markets.

        Uses file-based caching to avoid repeated API calls.
        First run: ~5 min (discovery + data). Subsequent: ~3 min (data only).
        Cached: instant.

        Args:
            force_refresh: If True, bypass cache and re-fetch everything

        Returns:
            DataFrame with columns: market, concept, quarter, value
        """
        # Check cache
        if self.use_cache and not force_refresh:
            cached = self._load_cache()
            if cached is not None:
                logger.info(f"Loaded {len(cached)} rows from cache")
                return cached

        # Discover series IDs
        if not self._series_map:
            self._series_map = self._discover_permit_series()

        market_names = sorted(self._series_map.keys())

        logger.info(f"Fetching SF permit data for {len(market_names)} markets...")
        logger.info(f"Estimated time: {len(market_names) * REQUEST_DELAY_SECONDS / 60:.1f} minutes")

        all_frames = []
        success_count = 0
        fail_count = 0

        for i, name in enumerate(market_names, 1):
            series_id = self._series_map[name]
            df = self._load_single_permit_series(series_id, name)

            if df is not None:
                all_frames.append(df)
                success_count += 1
            else:
                fail_count += 1

            # Progress logging every 25 markets
            if i % 25 == 0:
                logger.info(
                    f"Progress: {i}/{len(market_names)} markets "
                    f"({success_count} success, {fail_count} failed)"
                )

        if not all_frames:
            logger.error("No permit data loaded for any market!")
            return pd.DataFrame(columns=["market", "concept", "quarter", "value"])

        result = pd.concat(all_frames, ignore_index=True)

        # Save cache
        if self.use_cache:
            self._save_cache(result)

        logger.info(
            f"SF permits: {len(result)} rows, "
            f"{success_count} markets with data, {fail_count} without"
        )

        # Pass 2: For markets without SF data, try All Permits (BPPRIV)
        sf_markets = set(result["market"].unique()) if len(result) > 0 else set()
        bppriv_candidates = {
            name: sid for name, sid in self._all_permits_map.items()
            if name not in sf_markets
        }

        if bppriv_candidates:
            logger.info(f"Fetching All Permits (BPPRIV) for {len(bppriv_candidates)} additional markets...")
            bppriv_frames = []
            bp_success = 0

            for i, (name, series_id) in enumerate(sorted(bppriv_candidates.items()), 1):
                df = self._load_single_permit_series(series_id, name)
                if df is not None:
                    # Relabel concept to distinguish from SF permits
                    df["concept"] = CONCEPT_ALL_PERMITS
                    bppriv_frames.append(df)
                    bp_success += 1

                if i % 25 == 0:
                    logger.info(f"  BPPRIV progress: {i}/{len(bppriv_candidates)}")

            if bppriv_frames:
                bppriv_result = pd.concat(bppriv_frames, ignore_index=True)
                result = pd.concat([result, bppriv_result], ignore_index=True)
                logger.info(f"All Permits: {bp_success} additional markets loaded")

        total_markets = result["market"].nunique() if len(result) > 0 else 0
        logger.info(f"Total: {len(result)} rows across {total_markets} markets")

        return result

    # ----- YoY Computation -----

    def compute_yoy_change(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute year-over-year percent change for permit data.

        Adds YoY rows for both SF permits and All Permits.
        This matches how CoStar demographic metrics (Population,
        Employment) are handled in the engine.

        Args:
            df: DataFrame from load_all_permit_data()

        Returns:
            DataFrame with additional YoY rows appended
        """
        yoy_pairs = [
            (CONCEPT_SF_PERMITS, CONCEPT_SF_PERMITS_YOY),
            (CONCEPT_ALL_PERMITS, CONCEPT_ALL_PERMITS_YOY),
        ]

        all_yoy_rows = []

        for source_concept, yoy_concept in yoy_pairs:
            permit_data = df[df["concept"] == source_concept]
            if len(permit_data) == 0:
                continue

            for market in permit_data["market"].unique():
                market_df = permit_data[permit_data["market"] == market].sort_values("quarter")
                q_lookup = dict(zip(market_df["quarter"], market_df["value"]))

                for q, v in q_lookup.items():
                    year = int(q.split(" ")[0])
                    qnum = q.split(" ")[1]
                    prev_q = f"{year - 1} {qnum}"

                    if prev_q in q_lookup and q_lookup[prev_q] != 0:
                        yoy = (v - q_lookup[prev_q]) / q_lookup[prev_q]
                        all_yoy_rows.append({
                            "market": market,
                            "concept": yoy_concept,
                            "quarter": q,
                            "value": yoy,
                        })

        if all_yoy_rows:
            yoy_df = pd.DataFrame(all_yoy_rows)
            return pd.concat([df, yoy_df], ignore_index=True)

        return df

    # ----- Cache Management -----

    def _load_cache(self) -> Optional[pd.DataFrame]:
        """Load cached data if it exists and is not expired."""
        if not CACHE_FILE.exists():
            return None

        try:
            with open(CACHE_FILE, "rb") as f:
                cache_data = pickle.load(f)

            cached_time = cache_data.get("timestamp", 0)
            age_hours = (time.time() - cached_time) / 3600

            if age_hours > CACHE_EXPIRY_HOURS:
                logger.info(f"Cache expired ({age_hours:.1f} hours old)")
                return None

            logger.info(f"Using cached FRED data ({age_hours:.1f} hours old)")
            return cache_data["dataframe"]

        except Exception as e:
            logger.warning(f"Cache load failed: {e}")
            return None

    def _save_cache(self, df: pd.DataFrame):
        """Save data to cache file."""
        CACHE_DIR.mkdir(exist_ok=True)
        try:
            with open(CACHE_FILE, "wb") as f:
                pickle.dump({
                    "timestamp": time.time(),
                    "dataframe": df,
                    "version": "1.0",
                }, f)
            logger.info(f"Cached {len(df)} rows to {CACHE_FILE}")
        except Exception as e:
            logger.warning(f"Cache save failed: {e}")

    # ----- Reporting -----

    def print_coverage_report(self, df: Optional[pd.DataFrame] = None):
        """
        Print a summary of data coverage.

        Args:
            df: Optional pre-loaded DataFrame. If None, loads from cache.
        """
        if df is None:
            df = self._load_cache()
            if df is None:
                print("No data available. Run load_all_permit_data() first.")
                return

        sf_df = df[df["concept"] == CONCEPT_SF_PERMITS]
        all_df = df[df["concept"] == CONCEPT_ALL_PERMITS]
        total_markets = df[df["concept"].isin([CONCEPT_SF_PERMITS, CONCEPT_ALL_PERMITS])]["market"].nunique()
        total_mappable = len(get_all_mappable_markets())

        print("\n" + "=" * 70)
        print("FRED Data Coverage Report")
        print("=" * 70)

        # SF Permits
        sf_markets = sf_df["market"].nunique()
        print(f"\nSF Building Permits (1-unit, BP1FH):")
        print(f"  Markets: {sf_markets}")
        print(f"  Data points: {len(sf_df):,}")
        if len(sf_df) > 0:
            print(f"  Date range: {sf_df['quarter'].min()} to {sf_df['quarter'].max()}")
            quarters_per = sf_df.groupby("market")["quarter"].count()
            print(f"  Quarters/market: median={quarters_per.median():.0f}, min={quarters_per.min()}, max={quarters_per.max()}")

        # All Permits
        all_markets = all_df["market"].nunique()
        print(f"\nAll Building Permits (all units, BPPRIV):")
        print(f"  Markets: {all_markets}")
        print(f"  Data points: {len(all_df):,}")
        if len(all_df) > 0:
            print(f"  Date range: {all_df['quarter'].min()} to {all_df['quarter'].max()}")
            quarters_per = all_df.groupby("market")["quarter"].count()
            print(f"  Quarters/market: median={quarters_per.median():.0f}, min={quarters_per.min()}, max={quarters_per.max()}")

        # Combined
        print(f"\nCombined Coverage:")
        print(f"  Total markets with permit data: {total_markets} / {total_mappable} mappable ({total_markets/total_mappable*100:.1f}%)")
        print(f"  Markets with SF-specific data:  {sf_markets}")
        print(f"  Markets with All-Permits only:  {all_markets}")
        print(f"  Total data points:              {len(df):,}")

        # Top 10 by SF permits
        if len(sf_df) > 0:
            latest_q = sf_df["quarter"].max()
            latest = sf_df[sf_df["quarter"] == latest_q].nlargest(10, "value")
            print(f"\nTop 10 markets by SF permits ({latest_q}):")
            for _, row in latest.iterrows():
                print(f"  {row['market']:<35} {row['value']:>8,.0f}")

        print("=" * 70)


# ---------------------------------------------------------------------------
# Convenience CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # Get API key from argument or environment
    api_key = None
    if len(sys.argv) > 1:
        api_key = sys.argv[1]
    else:
        import os
        api_key = os.environ.get("FRED_API_KEY")

    if not api_key:
        print("Usage: python fred_data_loader.py <FRED_API_KEY>")
        print("   or: FRED_API_KEY=xxx python fred_data_loader.py")
        sys.exit(1)

    loader = FREDDataLoader(api_key=api_key)

    # Test connection
    print("Testing FRED API connection...")
    if not loader.test_connection():
        print("ERROR: API key is invalid or FRED API is unreachable.")
        sys.exit(1)
    print("Connection OK!\n")

    # Load permit data for a few test markets first
    print("Loading test markets...")
    test_markets = [
        "Atlanta - GA",
        "Dallas-Fort Worth - TX",
        "Phoenix - AZ",
        "Nashville - TN",
        "Denver - CO",
    ]
    test_df = loader.load_permit_data_for_markets(test_markets)

    if len(test_df) > 0:
        print(f"\nTest pull successful: {len(test_df)} rows")
        print(f"Markets: {test_df['market'].nunique()}")
        print(f"Quarters: {test_df['quarter'].min()} to {test_df['quarter'].max()}")

        # Show sample
        print("\nSample data (latest quarter per market):")
        latest = test_df.sort_values("quarter").groupby("market").tail(1)
        print(latest.to_string(index=False))

        # Compute YoY
        test_df = loader.compute_yoy_change(test_df)
        yoy_data = test_df[test_df["concept"] == CONCEPT_SF_PERMITS_YOY]
        if len(yoy_data) > 0:
            print(f"\nYoY changes computed: {len(yoy_data)} data points")
            latest_yoy = yoy_data.sort_values("quarter").groupby("market").tail(1)
            print(latest_yoy.to_string(index=False))

        # Ask whether to load all markets
        print("\n" + "-" * 40)
        resp = input("Load all mapped markets? (y/n): ").strip().lower()
        if resp == "y":
            full_df = loader.load_all_permit_data()
            full_df = loader.compute_yoy_change(full_df)
            loader.print_coverage_report(full_df)

            # Save to CSV for inspection
            output_path = Path(__file__).parent / "output" / "fred_sf_permits.csv"
            output_path.parent.mkdir(exist_ok=True)
            full_df.to_csv(output_path, index=False)
            print(f"\nData saved to {output_path}")
    else:
        print("WARNING: No data returned for test markets.")
        print("Check your API key and network connection.")
