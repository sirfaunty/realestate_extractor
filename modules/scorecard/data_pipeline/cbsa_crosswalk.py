"""
CBSA to CoStar Market Crosswalk
================================
Maps CBSA (Core Based Statistical Area) codes to CoStar market names.

CBSA codes are 5-digit numeric identifiers used by the U.S. Census Bureau
to identify Metropolitan and Micropolitan Statistical Areas. The CoStar
naming convention follows the pattern: "CityName - ST" (e.g., "Atlanta - GA")

This crosswalk covers the top ~200 MSAs by population and includes mappings
for the 399 CoStar apartment markets.

Reference: U.S. Census Bureau CBSA Delineations (2020 OMB Bulletin 20-01)
"""

# CBSA Code to CoStar Market Name Mapping
# Format: CBSA code (string, 5-digit) -> CoStar market name
CBSA_TO_COSTAR = {
    # Alabama
    "13820": "Birmingham - AL",
    "26620": "Huntsville - AL",
    "33660": "Mobile - AL",
    "33860": "Montgomery - AL",
    "11500": "Anniston-Oxford - AL",
    "12220": "Auburn-Opelika - AL",
    "19460": "Decatur - AL",
    "20020": "Dothan - AL",
    "22520": "Florence-Muscle Shoals - AL",
    "23460": "Gadsden - AL",
    "46220": "Tuscaloosa - AL",
    "17980": "Daphne-Fairhope-Foley - AL",

    # Alaska
    "11260": "Anchorage - AK",
    "21820": "Fairbanks - AK",

    # Arizona
    "38060": "Phoenix - AZ",
    "46060": "Tucson - AZ",
    "39150": "Prescott Valley-Prescott - AZ",
    "29420": "Lake Havasu City-Kingman - AZ",
    "49740": "Yuma - AZ",
    "22380": "Flagstaff - AZ",
    "43420": "Sierra Vista - AZ",

    # Arkansas
    "30780": "Little Rock - AR",
    "22220": "Northwest Arkansas - AR",
    "22900": "Fort Smith - AR",
    "27860": "Jonesboro - AR",
    "26300": "Hot Springs - AR",
    "38220": "Pine Bluff - AR",

    # California
    "12540": "Bakersfield - CA",
    "23420": "Fresno - CA",
    "31080": "Los Angeles - CA",
    "40900": "Sacramento - CA",
    "41740": "San Diego - CA",
    "41860": "San Francisco - CA",
    "41940": "San Jose - CA",
    "40140": "Inland Empire - CA",
    "37100": "Ventura - CA",
    "44700": "Stockton - CA",
    "33700": "Modesto - CA",
    "42200": "Santa Barbara - CA",
    "42020": "Santa Rosa - CA",
    "42100": "Santa Cruz - CA",
    "46700": "Vallejo-Fairfield - CA",
    "34900": "Napa - CA",
    "32900": "Merced - CA",
    "31460": "Madera - CA",
    "25260": "Hanford-Corcoran - CA",
    "41500": "San Luis Obispo - CA",
    "42220": "San Rafael - CA",
    "49700": "Yuba City - CA",
    "47300": "Visalia - CA",
    "17020": "Chico - CA",
    "39820": "Redding - CA",
    "41500": "Salinas - CA",
    "20940": "El Centro - CA",

    # Colorado
    "19740": "Denver - CO",
    "17820": "Colorado Springs - CO",
    "22660": "Fort Collins - CO",
    "24300": "Grand Junction - CO",
    "24540": "Greeley - CO",
    "39740": "Pueblo - CO",

    # Connecticut
    "14860": "Bridgeport - CT",
    "25540": "Hartford - CT",
    "35300": "New Haven - CT",

    # Washington DC
    "47900": "Washington - DC",

    # Florida
    "15980": "Fort Myers - FL",
    "19660": "Daytona Beach - FL",
    "27260": "Jacksonville - FL",
    "33100": "Miami - FL",
    "36740": "Orlando - FL",
    "45300": "Tampa - FL",
    "29460": "Lakeland - FL",
    "37340": "Melbourne - FL",
    "34940": "Naples - FL",
    "36100": "Ocala - FL",
    "37860": "Pensacola - FL",
    "45220": "Tallahassee - FL",
    "23540": "Gainesville - FL",
    "38940": "Port St. Lucie - FL",
    "35840": "Sarasota - FL",
    "39460": "Punta Gorda - FL",
    "17500": "Clarksville - TN",
    "37380": "Panama City - FL",
    "18880": "Crestview-Fort Walton Beach - FL",
    "42680": "Sebastian-Vero Beach - FL",
    "45540": "The Villages - FL",

    # Georgia
    "12060": "Atlanta - GA",
    "42340": "Savannah - GA",
    "12260": "Augusta - GA",
    "31420": "Macon - GA",
    "16860": "Chattanooga - TN",
    "10500": "Albany - GA",
    "12020": "Athens - GA",
    "15260": "Brunswick - GA",
    "25980": "Hinesville - GA",
    "47580": "Warner Robins - GA",

    # Hawaii
    "27980": "Kahului-Wailuku-Lahaina - HI",
    "46520": "Honolulu - HI",

    # Idaho
    "14260": "Boise - ID",
    "17660": "Coeur d'Alene - ID",
    "26820": "Idaho Falls - ID",
    "46300": "Twin Falls - ID",

    # Illinois
    "16980": "Chicago - IL",
    "37900": "Peoria - IL",
    "40420": "Rockford - IL",
    "44100": "Springfield - IL",
    "16060": "Carbondale - IL",
    "14010": "Bloomington - IL",
    "19500": "Decatur - IL",
    "16580": "Champaign-Urbana - IL",

    # Indiana
    "26900": "Indianapolis - IN",
    "23060": "Fort Wayne - IN",
    "21140": "Elkhart-Goshen - IN",
    "29020": "Kokomo - IN",
    "29200": "Lafayette - IN",
    "34620": "Muncie - IN",
    "43780": "South Bend - IN",
    "21780": "Evansville - IN",
    "45460": "Terre Haute - IN",

    # Iowa
    "19780": "Des Moines - IA",
    "16300": "Cedar Rapids - IA",
    "19340": "Davenport - IA",
    "47940": "Waterloo - IA",

    # Kansas
    "48620": "Wichita - KS",
    "45820": "Topeka - KS",
    "29940": "Lawrence - KS",
    "31740": "Manhattan - KS",

    # Kentucky
    "30460": "Lexington - KY",
    "31140": "Louisville - KY",
    "14540": "Bowling Green - KY",
    "36980": "Owensboro - KY",

    # Louisiana
    "12940": "Baton Rouge - LA",
    "35380": "New Orleans - LA",
    "43340": "Shreveport - LA",
    "29180": "Lafayette - LA",
    "29340": "Lake Charles - LA",
    "33740": "Monroe - LA",
    "10780": "Alexandria - LA",

    # Maine
    "38860": "Portland - ME",

    # Maryland
    "12580": "Baltimore - MD",

    # Massachusetts
    "14460": "Boston - MA",
    "44140": "Springfield - MA",
    "49340": "Worcester - MA",

    # Michigan
    "19820": "Detroit - MI",
    "24340": "Grand Rapids - MI",
    "29620": "Lansing - MI",
    "11460": "Ann Arbor - MI",
    "22420": "Flint - MI",
    "28020": "Kalamazoo - MI",
    "34740": "Muskegon - MI",
    "40980": "Saginaw - MI",
    "45780": "Traverse City - MI",

    # Minnesota
    "33460": "Minneapolis - MN",
    "40340": "Rochester - MN",
    "41060": "St. Cloud - MN",
    "20260": "Duluth - MN",

    # Mississippi
    "27140": "Jackson - MS",
    "25060": "Gulfport-Biloxi - MS",
    "25620": "Hattiesburg - MS",

    # Missouri
    "28140": "Kansas City - MO",
    "41180": "St. Louis - MO",
    "44180": "Springfield - MO",
    "17860": "Columbia - MO",
    "27900": "Joplin - MO",

    # Montana
    "13740": "Billings - MT",
    "24500": "Great Falls - MT",
    "33540": "Missoula - MT",
    "14580": "Bozeman - MT",

    # Nebraska
    "36540": "Omaha - NE",
    "30700": "Lincoln - NE",

    # Nevada
    "29820": "Las Vegas - NV",
    "39900": "Reno - NV",

    # New Mexico
    "10740": "Albuquerque - NM",
    "29740": "Las Cruces - NM",
    "42140": "Santa Fe - NM",

    # New York
    "35620": "New York - NY",
    "10580": "Albany - NY",
    "15380": "Buffalo - NY",
    "40380": "Rochester - NY",
    "45060": "Syracuse - NY",
    "13780": "Binghamton - NY",
    "46540": "Utica - NY",

    # North Carolina
    "16740": "Charlotte - NC",
    "39580": "Raleigh - NC",
    "24660": "Greensboro - NC",
    "20500": "Durham - NC",
    "48900": "Wilmington - NC",
    "11700": "Asheville - NC",
    "22180": "Fayetteville - NC",
    "24780": "Greenville - NC",
    "25860": "Hickory - NC",
    "27340": "Jacksonville - NC",
    "49180": "Winston-Salem - NC",

    # North Dakota
    "22020": "Fargo - ND",
    "13900": "Bismarck - ND",
    "33500": "Minot - ND",

    # Ohio
    "17140": "Cincinnati - OH",
    "17460": "Cleveland - OH",
    "18140": "Columbus - OH",
    "10420": "Akron - OH",
    "19380": "Dayton - OH",
    "45780": "Toledo - OH",
    "49660": "Youngstown - OH",
    "15940": "Canton - OH",
    "41400": "Sandusky - OH",

    # Oklahoma
    "36420": "Oklahoma City - OK",
    "46140": "Tulsa - OK",
    "30020": "Lawton - OK",

    # Oregon
    "38900": "Portland - OR",
    "21660": "Eugene - OR",
    "13460": "Bend - OR",
    "32780": "Medford - OR",
    "41420": "Salem - OR",

    # Pennsylvania
    "37980": "Philadelphia - PA",
    "38300": "Pittsburgh - PA",
    "25420": "Harrisburg - PA",
    "10900": "Allentown - PA",
    "42540": "Scranton - PA",
    "21500": "Erie - PA",
    "29540": "Lancaster - PA",
    "39740": "Reading - PA",
    "49620": "York - PA",

    # Rhode Island
    "39300": "Providence - RI",

    # South Carolina
    "16700": "Charleston - SC",
    "17900": "Columbia - SC",
    "24860": "Greenville - SC",
    "34820": "Myrtle Beach - SC",
    "44940": "Sumter - SC",
    "43900": "Spartanburg - SC",

    # South Dakota
    "43620": "Sioux Falls - SD",
    "39660": "Rapid City - SD",

    # Tennessee
    "16860": "Chattanooga - TN",
    "28940": "Knoxville - TN",
    "32820": "Memphis - TN",
    "34980": "Nashville - TN",
    "17300": "Clarksville - TN",
    "27180": "Jackson - TN",
    "27740": "Johnson City - TN",
    "28700": "Kingsport - TN",

    # Texas
    "12420": "Austin - TX",
    "19100": "Dallas-Fort Worth - TX",
    "26420": "Houston - TX",
    "21340": "El Paso - TX",
    "41700": "San Antonio - TX",
    "18580": "Corpus Christi - TX",
    "31180": "Lubbock - TX",
    "10180": "Abilene - TX",
    "11100": "Amarillo - TX",
    "13140": "Beaumont-Port Arthur - TX",
    "15180": "Brownsville - TX",
    "28660": "Killeen - TX",
    "29700": "Laredo - TX",
    "31060": "Longview - TX",
    "32580": "McAllen - TX",
    "33260": "Midland - TX",
    "36220": "Odessa - TX",
    "41660": "San Angelo - TX",
    "43300": "Sherman - TX",
    "45500": "Texarkana - TX",
    "46340": "Tyler - TX",
    "47380": "Waco - TX",
    "48660": "Wichita Falls - TX",
    "20900": "Eagle Pass - TX",

    # Utah
    "36260": "Ogden - UT",
    "39340": "Provo - UT",
    "41620": "Salt Lake City - UT",
    "41100": "St. George - UT",
    "30860": "Logan - UT",

    # Vermont
    "15540": "Burlington - VT",

    # Virginia
    "40060": "Richmond - VA",
    "47260": "Virginia Beach - VA",
    "40220": "Roanoke - VA",
    "31340": "Lynchburg - VA",
    "16820": "Charlottesville - VA",

    # Washington
    "42660": "Seattle - WA",
    "44060": "Spokane - WA",
    "28420": "Kennewick - WA",
    "36020": "Olympia - WA",
    "13380": "Bellingham - WA",
    "14740": "Bremerton - WA",
    "49420": "Yakima - WA",

    # West Virginia
    "16620": "Charleston - WV",
    "26580": "Huntington - WV",

    # Wisconsin
    "31540": "Madison - WI",
    "33340": "Milwaukee - WI",
    "24580": "Green Bay - WI",
    "11540": "Appleton - WI",
    "36780": "Oshkosh - WI",
    "39540": "Racine - WI",

    # Wyoming
    "16940": "Cheyenne - WY",
    "16220": "Casper - WY",
}

# CoStar submarkets that map to a parent CBSA
# These CoStar markets don't have their own CBSA — they share permit data
# with a parent MSA. We track these so we know which markets can't get
# independent FRED data.
COSTAR_SUBMARKET_TO_PARENT_CBSA = {
    "East Bay - CA": "41860",           # Part of SF Bay Area CBSA
    "Orange County - CA": "31080",      # Part of LA CBSA
    "Long Island - NY": "35620",        # Part of NY CBSA
    "Northern New Jersey - NJ": "35620",# Part of NY CBSA
    "Fort Lauderdale - FL": "33100",    # Part of Miami CBSA
    "Palm Beach - FL": "33100",         # Part of Miami CBSA
    "Stamford - CT": "14860",           # Part of Bridgeport CBSA
    "Lehigh Valley - PA": "10900",      # Allentown CBSA
    "Norfolk - VA": "47260",            # Part of Virginia Beach CBSA
    "Trenton - NJ": "45940",            # Own CBSA but often grouped
    "San Rafael - CA": "41860",         # Part of SF Bay Area (Marin County)
}

# Additional CoStar market name variations → standard CBSA code
# When CoStar uses a different name than the Census MSA title
COSTAR_NAME_ALIASES = {
    "Saint Louis - MO": "41180",        # vs "St. Louis - MO"
    "Ft Walton Beach - FL": "18880",    # vs "Crestview-Fort Walton Beach"
    "Kennewick-Richland - WA": "28420", # alternate form
    "Lafayette-West Lafayette - IN": "29200",
    "Waterloo-Cedar Falls - IA": "47940",
    "Gulfport-Biloxi-Pascagoula - MS": "25060",
    "Oshkosh-Neenah - WI": "36780",
    "Brownsville-Harlingen - TX": "15180",
    "Sherman-Denison - TX": "43300",
    "Carbondale-Marion - IL": "16060",
    "Lake Havasu - AZ": "29420",
    "Prescott - AZ": "39150",
    "Sierra Vista-Douglas - AZ": "43420",
}


def get_cbsa_for_market(costar_name: str) -> str:
    """
    Reverse lookup: Get CBSA code for a CoStar market name.
    Checks the primary mapping, then aliases, then submarkets.

    Args:
        costar_name (str): CoStar market name (e.g., "Atlanta - GA")

    Returns:
        str: CBSA code if found, empty string otherwise

    Example:
        >>> get_cbsa_for_market("Atlanta - GA")
        "12060"
    """
    # Check primary mapping
    for cbsa_code, market_name in CBSA_TO_COSTAR.items():
        if market_name.lower() == costar_name.lower():
            return cbsa_code
    # Check aliases
    if costar_name in COSTAR_NAME_ALIASES:
        return COSTAR_NAME_ALIASES[costar_name]
    # Check submarkets (returns parent CBSA)
    if costar_name in COSTAR_SUBMARKET_TO_PARENT_CBSA:
        return COSTAR_SUBMARKET_TO_PARENT_CBSA[costar_name]
    return ""


def get_all_mappable_markets() -> dict:
    """
    Get all CoStar markets that can be mapped to a CBSA code.
    Combines primary mapping, aliases, and submarkets.

    Returns:
        dict: {costar_name: cbsa_code} for all mappable markets
    """
    result = {name: code for code, name in CBSA_TO_COSTAR.items()}
    result.update({name: code for name, code in COSTAR_NAME_ALIASES.items()})
    result.update({name: code for name, code in COSTAR_SUBMARKET_TO_PARENT_CBSA.items()})
    return result


def get_fred_permit_series_id(cbsa_code: str) -> str:
    """
    Generate FRED series ID for single-family housing permits for a CBSA.

    Note: This function generates the series ID prefix. The actual FRED series
    may have variations depending on data type (1FH for 1-unit structures,
    PRIV for private structures, etc.)

    FRED series naming convention for CBSA-based data:
    - PERMIT1CBSA{CBSA_CODE} for single-family permits

    Args:
        cbsa_code (str): 5-digit CBSA code

    Returns:
        str: FRED series ID prefix

    Example:
        >>> get_fred_permit_series_id("12060")
        "PERMIT1CBSA12060"
    """
    return f"PERMIT1CBSA{cbsa_code}"


def get_costar_market_for_cbsa(cbsa_code: str) -> str:
    """
    Get CoStar market name for a CBSA code.

    Args:
        cbsa_code (str): 5-digit CBSA code

    Returns:
        str: CoStar market name if found, empty string otherwise

    Example:
        >>> get_costar_market_for_cbsa("12060")
        "Atlanta - GA"
    """
    return CBSA_TO_COSTAR.get(cbsa_code, "")


def list_all_mappings() -> list:
    """
    Get all CBSA to CoStar mappings as list of tuples.

    Returns:
        list: List of (cbsa_code, costar_name) tuples sorted by market name
    """
    return sorted(CBSA_TO_COSTAR.items(), key=lambda x: x[1])


def get_markets_by_state(state_code: str) -> dict:
    """
    Get all CoStar markets in a specific state.

    Args:
        state_code (str): 2-letter state code (e.g., "GA", "TX")

    Returns:
        dict: Mapping of CBSA codes to CoStar names for that state

    Example:
        >>> get_markets_by_state("GA")
        {"12060": "Atlanta - GA", "42340": "Savannah - GA"}
    """
    return {
        cbsa: name for cbsa, name in CBSA_TO_COSTAR.items()
        if name.endswith(f" - {state_code}")
    }


if __name__ == "__main__":
    # Example usage and validation
    print("CBSA to CoStar Market Crosswalk")
    print("=" * 50)

    # Test forward lookup
    test_cbsa = "12060"
    market = get_costar_market_for_cbsa(test_cbsa)
    print(f"\nCBSA {test_cbsa} -> {market}")

    # Test reverse lookup
    test_market = "Atlanta - GA"
    cbsa = get_cbsa_for_market(test_market)
    print(f"{test_market} -> CBSA {cbsa}")

    # Test FRED series generation
    fred_series = get_fred_permit_series_id(test_cbsa)
    print(f"FRED Series ID: {fred_series}")

    # Show markets by state example
    print(f"\nMarkets in Texas:")
    tx_markets = get_markets_by_state("TX")
    for cbsa, name in sorted(tx_markets.items()):
        print(f"  {cbsa}: {name}")

    # Total count
    print(f"\nTotal mappings: {len(CBSA_TO_COSTAR)}")
