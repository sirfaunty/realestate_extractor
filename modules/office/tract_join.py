"""
Tract-True Point-in-Polygon Join (office) — SUPERSEDES county-grain fallback
============================================================================
The handoff documented this as the one pending item. TIGER tl_2023_27 now
available -> property->true-census-tract assignment, mirroring the multifamily
property_tract result (which achieved 100%, zero attrition).

Binding rules:
  - supersession EXPLICIT: tract-true supersedes county; county retained as
    documented fallback (not deleted) for the records tract-true can't reach
  - no fabrication: MN-only shapefile -> 118 WI (St. Croix) props get NULL
    tract WITH reason, keep county-grain, flagged not dropped
  - ACS tract income already at full tract resolution in office_geo_panel;
    this join makes property->tract->ACS a real, queryable path
  - verify from source: report match rate; flag any attrition explicitly
"""
import geopandas as gpd, pandas as pd, numpy as np, duckdb, datetime as dt
from shapely.geometry import Point

DB = "/home/claude/office_inventory.duckdb"
SHP = "/tmp/tractshp/tl_2023_27_tract.shp"

con = duckdb.connect(DB)
op = con.execute("""
    SELECT pid, "Latitude" AS lat, "Longitude" AS lon, "County Name" AS county
    FROM office_property
""").df()
op["lat"] = pd.to_numeric(op["lat"], errors="coerce")
op["lon"] = pd.to_numeric(op["lon"], errors="coerce")

tracts = gpd.read_file(SHP)[["GEOID", "COUNTYFP", "NAMELSAD", "geometry"]]
tracts = tracts.rename(columns={"GEOID": "tract_geoid",
                                "NAMELSAD": "tract_name"})

# Build property points in the shapefile CRS (EPSG:4269, NAD83 lat/lon)
gdf = gpd.GeoDataFrame(
    op.copy(),
    geometry=[Point(xy) if pd.notna(xy[0]) and pd.notna(xy[1]) else None
              for xy in zip(op["lon"], op["lat"])],
    crs="EPSG:4269")

has_pt = gdf.geometry.notna()
joined = gpd.sjoin(gdf[has_pt], tracts, how="left", predicate="within")
joined = joined[~joined.index.duplicated(keep="first")]  # boundary edge safety

res = op[["pid", "county"]].copy()
# tract result is per-pid; collapse to one tract per pid BEFORE merge so the
# 118 duplicate-PropertyID source rows don't fan out (source has 5033 rows /
# 4915 distinct pids — known dup-pid condition flagged at initial inspection)
tr_per_pid = (joined[["pid", "tract_geoid", "tract_name", "COUNTYFP"]]
              .drop_duplicates(subset="pid", keep="first"))
res = res.drop_duplicates(subset="pid").merge(tr_per_pid, on="pid", how="left")

n = len(res)
placed = res["tract_geoid"].notna().sum()
# Both MSP-area WI counties (Pierce 55093, St Croix 55109) are outside the
# MN-only TIGER shapefile by construction — flag, don't mislabel as "unplaced"
wi = res["county"].astype(str).str.replace(".", "", regex=False).str.strip(
    ).isin(["Pierce", "St Croix", "St  Croix"])
wi_n = int(wi.sum())
unplaced_mn = res[res["tract_geoid"].isna() & ~wi]

res["tract_join_status"] = np.where(
    res["tract_geoid"].notna(), "tract_true",
    np.where(wi, "county_only_WI_outside_MN_shapefile",
             "county_only_unplaced_CHECK_LATLON"))
res["tract_supersedes_county"] = res["tract_geoid"].notna()

con.execute("DROP TABLE IF EXISTS office_property_tract")
con.register("res_df", res)
con.execute("CREATE TABLE office_property_tract AS SELECT * FROM res_df")

print(f"Office property -> tract-true point-in-polygon:")
print(f"  total properties        : {n:,}")
print(f"  placed in a true tract  : {placed:,} ({placed/n*100:.1f}%)")
print(f"  WI (St Croix) county-only: {wi_n} "
      f"(MN-only shapefile by construction — flagged, county retained)")
print(f"  MN but unplaced         : {len(unplaced_mn)} "
      f"(would indicate bad lat/lon — investigate if >0)")
if len(unplaced_mn):
    print(unplaced_mn[["pid", "county"]].head(10).to_string())

# Distinct tracts hit + the intra-county income-gradient check (the MF payoff)
dt_ = con.execute("""
  SELECT COUNT(DISTINCT tract_geoid) FROM office_property_tract
  WHERE tract_geoid IS NOT NULL
""").fetchone()[0]
print(f"\n  distinct census tracts occupied by office: {dt_}")

# Now property -> tract -> ACS income is a real path. Demonstrate the
# intra-Hennepin gradient that county-grain flattens (mirrors MF finding).
grad = con.execute("""
  WITH latest_acs AS (
    SELECT geo_id AS tract_geoid, value AS med_inc
    FROM office_geo_panel
    WHERE source='acs5_b19013' AND vintage_span='2019-2023' AND value IS NOT NULL
  )
  SELECT pt.county,
         COUNT(*) AS office_props,
         ROUND(MIN(a.med_inc)) AS tract_inc_min,
         ROUND(MAX(a.med_inc)) AS tract_inc_max,
         ROUND(MAX(a.med_inc)*1.0/NULLIF(MIN(a.med_inc),0),2) AS gradient_x
  FROM office_property_tract pt
  JOIN latest_acs a USING (tract_geoid)
  WHERE pt.county IN ('Hennepin','Ramsey','Dakota')
  GROUP BY pt.county ORDER BY office_props DESC
""").df()
print("\nIntra-county income gradient now visible (county-grain flattens this):")
print(grad.to_string(index=False))

# Append-only audit: log the supersession event
con.execute("""
  INSERT INTO raw_ingestion_log
  SELECT 'ing_office_tract_true_5_19_26','tiger_tl_2023_27_tract',
         'tl_2023_27_tract.zip (TIGER2023, MN)', DATE '2026-05-19','minneapolis',
         DATE '2023-01-01', DATE '2023-12-31',
         'shapefile sha in office_geo_data; MANIFEST pull_tiger',
         'Tract-true point-in-polygon join. SUPERSEDES county-grain fallback '
         'for placed props. MN-only shapefile -> WI St Croix props retain '
         'county-grain (flagged county_only_WI). Mirrors MF property_tract.'
""")
con.execute("CREATE OR REPLACE TABLE raw_ingestion_log AS "
            "SELECT DISTINCT * FROM raw_ingestion_log")
print("\ningestion log:",
      con.execute("SELECT ingestion_id FROM raw_ingestion_log").fetchall())
con.close()
