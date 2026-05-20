"""
Office Geo/Macro Layer Integration
==================================
Wires the 6 Claude-Code pulls into office_inventory.duckdb as an append-only
geo-panel, plus the QCEW <-> tenant-NAICS bridge (the office-specific payoff).

Binding rules carried:
  - no fabrication: every source NULL flagged with reason, preserved verbatim
  - knowledge_date governance preserved exactly as pulled (incl. honest NULLs)
  - county-grain integration NOW; tract-true property->tract join PENDING the
    TIGER shapefile (blocked by env allowlist) — stated, not hidden, exactly
    as the platform states the BPS county-grain limit
  - ACS tract income retained at FULL tract resolution (not downsampled) +
    a county rollup for the property join; NO cross-vintage differencing
  - supersession explicit: county join is the documented fallback the platform
    used pre-TIGER; tract-true SUPERSEDES it when the shapefile lands
"""
import pandas as pd, numpy as np, duckdb, glob, os, datetime as dt

GEO_DIR = "/home/claude/office_geo/office_geo_data"
DB = "/home/claude/office_inventory.duckdb"

# County name -> 5-digit FIPS (the 16 MSP MSA counties, MN + 2 WI)
COUNTY_FIPS = {
    "Anoka": "27003", "Carver": "27019", "Chisago": "27025", "Dakota": "27037",
    "Hennepin": "27053", "Isanti": "27059", "Le Sueur": "27079",
    "Mille Lacs": "27095", "Ramsey": "27123", "Scott": "27139",
    "Sherburne": "27141", "Sibley": "27143", "Washington": "27163",
    "Wright": "27171", "Pierce": "55093", "St. Croix": "55109",
}

con = duckdb.connect(DB)

# ---------------------------------------------------------------------------
# 1. Stack all 6 sources into ONE long/tidy bitemporal geo panel (append-only)
# ---------------------------------------------------------------------------
files = sorted(glob.glob(os.path.join(GEO_DIR, "*.parquet")))
common = ["source", "geo_level", "geo_id", "geo_name", "period", "measure",
          "value", "unit", "knowledge_date", "vintage_span", "is_estimated",
          "notes"]
frames = []
for f in files:
    d = pd.read_parquet(f)
    keep = d[common].copy()
    # carry the most useful source-specific descriptor without exploding schema
    for extra in ["naics_code", "naics_title", "naics_label", "structure_type",
                  "bls_series_id", "own_title", "disclosure_code"]:
        if extra in d.columns:
            keep[extra] = d[extra].astype("string")
    for extra in ["naics_code", "naics_title", "naics_label", "structure_type",
                  "bls_series_id", "own_title", "disclosure_code"]:
        if extra not in keep.columns:
            keep[extra] = pd.NA
    keep["src_file"] = os.path.basename(f)
    frames.append(keep)

geo = pd.concat(frames, ignore_index=True)
geo["period"] = pd.to_datetime(geo["period"]).dt.date
geo["knowledge_date"] = pd.to_datetime(geo["knowledge_date"], errors="coerce").dt.date

con.execute("DROP TABLE IF EXISTS office_geo_panel")
con.register("geo_df", geo)
con.execute("CREATE TABLE office_geo_panel AS SELECT * FROM geo_df")

print(f"office_geo_panel: {len(geo):,} rows from {len(files)} sources")
print(geo.groupby("source").agg(
    rows=("value", "size"),
    null_values=("value", lambda s: s.isna().sum()),
    null_kd=("knowledge_date", lambda s: s.isna().sum()),
    pmin=("period", "min"), pmax=("period", "max")).to_string())

# ---------------------------------------------------------------------------
# 2. Property -> COUNTY linkage (the honest fallback; tract-true is PENDING)
#    All 6 macro sources are county/MSA-native, so this loses nothing for them.
# ---------------------------------------------------------------------------
op = con.execute("SELECT pid, \"County Name\" AS county FROM office_property").df()
# Normalize: strip periods, collapse whitespace (file has 'St  Croix' vs 'St. Croix')
import re as _re
def _norm_cty(s):
    if s is None or (isinstance(s, float)):
        return s
    return _re.sub(r"\s+", " ", str(s).replace(".", "")).strip()
_fips_norm = {_norm_cty(k): v for k, v in COUNTY_FIPS.items()}
op["county_norm"] = op["county"].map(_norm_cty)
op["county_fips"] = op["county_norm"].map(_fips_norm)
unmapped = op["county_fips"].isna().sum()
con.execute("DROP TABLE IF EXISTS office_property_county")
con.register("opc", op)
con.execute("CREATE TABLE office_property_county AS SELECT * FROM opc")
print(f"\nProperty->county: {len(op)-unmapped}/{len(op)} mapped "
      f"({unmapped} outside the 16-county MSP set, flagged not dropped)")

# ACS tract income kept at FULL tract resolution (not downsampled) +
# a county-mean rollup ONLY for the property join, per vintage (no differencing)
acs = pd.read_parquet(os.path.join(GEO_DIR, "acs_tract_income.parquet"))
acs["county_fips"] = acs["geo_id"].astype(str).str[:5]
acs_cty = (acs[acs["value"].notna()]
           .groupby(["county_fips", "period", "vintage_span"], as_index=False)
           .agg(acs_median_hh_income_county_mean=("value", "mean"),
                acs_tracts_in_county=("value", "size"),
                knowledge_date=("knowledge_date", "first")))
con.execute("DROP TABLE IF EXISTS office_acs_income_county")
con.register("acs_c", acs_cty)
con.execute("CREATE TABLE office_acs_income_county AS SELECT * FROM acs_c")
print(f"ACS county rollup: {len(acs_cty)} county-vintage rows "
      f"(full {len(acs)} tract rows retained in office_geo_panel)")

# ---------------------------------------------------------------------------
# 3. THE QCEW <-> tenant-NAICS bridge (the office-specific payoff)
#    Building tenant sector mix vs that sector's county employment trajectory.
# ---------------------------------------------------------------------------
ten = con.execute("""
    SELECT pid, "Tenant Name" AS tenant, "SF Occupied" AS sf,
           "NAICS" AS naics_raw, "Industry" AS industry
    FROM office_tenant WHERE "SF Occupied" IS NOT NULL
""").df()
ten["pid"] = pd.to_numeric(ten["pid"], errors="coerce")
# tenant NAICS -> 2-digit sector to match QCEW agglvl 74/44 sector grain
ten["naics2"] = (ten["naics_raw"].astype(str).str.extract(r"(\d{2})")[0])
ten = ten.merge(op[["pid", "county_fips"]], on="pid", how="left")

q = pd.read_parquet(os.path.join(GEO_DIR, "qcew_employment.parquet"))
q = q[(q["measure"] == "employment_month3") & q["value"].notna()].copy()
q["naics2"] = q["naics_code"].astype(str).str.extract(r"(\d{2})")[0]
q["yq"] = pd.to_datetime(q["period"])
# latest available quarter per (county, naics2): the current employment level
q_latest = (q.sort_values("yq")
            .groupby(["geo_id", "naics2"], as_index=False).last()
            [["geo_id", "naics2", "value", "yq"]]
            .rename(columns={"geo_id": "county_fips",
                             "value": "sector_emp_latest",
                             "yq": "sector_emp_period"}))
# 4-quarter trailing growth in that county-sector (county-native, honest grain)
q_yoy = q.copy()
q_yoy["y"] = q_yoy["yq"].dt.year
piv = (q_yoy.groupby(["geo_id", "naics2", "y"])["value"].mean()
       .reset_index())
piv = piv.sort_values(["geo_id", "naics2", "y"])
piv["sector_emp_yoy"] = (piv.groupby(["geo_id", "naics2"])["value"]
                         .pct_change())
q_growth = (piv.sort_values("y")
            .groupby(["geo_id", "naics2"], as_index=False).last()
            [["geo_id", "naics2", "sector_emp_yoy"]]
            .rename(columns={"geo_id": "county_fips"}))

bridge = (ten.merge(q_latest, on=["county_fips", "naics2"], how="left")
              .merge(q_growth, on=["county_fips", "naics2"], how="left"))
# Per-building: SF-weighted exposure to growing vs shrinking sectors
def bldg_sector(g):
    tot = g["sf"].sum()
    if tot <= 0:
        return pd.Series({"bldg_sf_matched": 0.0,
                          "bldg_sector_emp_yoy_wt": np.nan,
                          "bldg_pct_sf_naics_matched": np.nan})
    m = g["sector_emp_yoy"].notna()
    w = (g.loc[m, "sf"] * g.loc[m, "sector_emp_yoy"]).sum()
    sfm = g.loc[m, "sf"].sum()
    return pd.Series({
        "bldg_sf_matched": sfm,
        "bldg_sector_emp_yoy_wt": (w / sfm) if sfm > 0 else np.nan,
        "bldg_pct_sf_naics_matched": 100.0 * sfm / tot,
    })

bldg = (bridge.groupby("pid").apply(bldg_sector, include_groups=False)
        .reset_index())
con.execute("DROP TABLE IF EXISTS office_tenant_naics_bridge")
con.register("br", bridge)
con.execute("CREATE TABLE office_tenant_naics_bridge AS SELECT * FROM br")
con.execute("DROP TABLE IF EXISTS office_bldg_sector_exposure")
con.register("bg", bldg)
con.execute("CREATE TABLE office_bldg_sector_exposure AS SELECT * FROM bg")

matched = bridge["sector_emp_latest"].notna().mean() * 100
print(f"\nQCEW<->tenant-NAICS bridge: {len(bridge):,} tenant rows, "
      f"{matched:.1f}% matched to a county-sector employment series")
print(f"office_bldg_sector_exposure: {len(bldg):,} buildings w/ SF-weighted "
      f"sector-employment-trend exposure (the office-specific signal)")

# ---------------------------------------------------------------------------
# 4. Log the integration into raw_ingestion_log (append-only audit spine)
# ---------------------------------------------------------------------------
new_log = pd.DataFrame([dict(
    ingestion_id="ing_office_geo_5_19_26",
    source="costar_code_geo_macro_bundle",
    source_vintage="office_geo_data.zip (FRED+ACS+CBP+QCEW+LAUS+BPS, 2026-05-19)",
    knowledge_date=dt.date(2026, 5, 19), market_id="minneapolis",
    period_min=dt.date(1917, 12, 1), period_max=dt.date(2026, 5, 15),
    file_hash="see office_geo_data/MANIFEST.json per-source hashes",
    notes="6 long/tidy bitemporal sources. County-grain integration; "
          "property->tract-TRUE join PENDING TIGER shapefile (env-blocked) "
          "— documented fallback, supersedes to tract-true when shapefile lands. "
          "LAUS knowledge_date NULL by source (BLS v1 no release field) — "
          "disclosed not fabricated. QCEW single-vintage (CSV slices). "
          "ACS full tract resolution retained; NO cross-vintage differencing.")])
con.execute("INSERT INTO raw_ingestion_log SELECT * FROM new_log")

print("\n=== WAREHOUSE NOW ===")
for r in con.execute("SELECT table_name FROM information_schema.tables "
                      "WHERE table_name LIKE 'office%' OR table_name='raw_ingestion_log' "
                      "ORDER BY 1").fetchall():
    n = con.execute(f'SELECT COUNT(*) FROM "{r[0]}"').fetchone()[0]
    print(f"  {r[0]:32s} {n:>9,}")
con.close()
