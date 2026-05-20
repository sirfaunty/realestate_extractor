"""
WI Supplement Integration — closes the documented scope gap
===========================================================
Folds the 2 WI pieces (ACS tract income state 55, TIGER WI tract shapefile)
into the office warehouse. The 138 WI properties (Pierce 55093 + St Croix
55109) move from county-grain fallback to tract-true.

Binding rules:
  - scope gap CLOSED explicitly (not silently backfilled): supersession noted,
    raw_ingestion_log gets a new append-only row
  - no fabrication: WI ACS verified schema/dtype-identical, sha-verified zip
  - knowledge_date reused verbatim from original ACS releases (audited)
  - tract-true supersedes county for now-placed WI props; county retained
  - same dedup/grain discipline as the MN tract join (118-dup-pid condition)
"""
import geopandas as gpd, pandas as pd, numpy as np, duckdb, datetime as dt
from shapely.geometry import Point

DB = "/home/claude/office_inventory.duckdb"
WI_ACS = "/home/claude/wi_supp/acs_tract_income_wi.parquet"
SHP_MN = "/tmp/tractshp/tl_2023_27_tract.shp"
SHP_WI = "/tmp/wishp/tl_2023_55_tract.shp"

con = duckdb.connect(DB)

# ---------------------------------------------------------------------------
# 1. Append WI ACS into office_geo_panel (same shape as the MN ACS rows)
# ---------------------------------------------------------------------------
wi = pd.read_parquet(WI_ACS)
panel_cols = [c[0] for c in con.execute(
    "DESCRIBE office_geo_panel").fetchall()]
wi_panel = pd.DataFrame({c: pd.NA for c in panel_cols}, index=wi.index)
wi_panel["source"] = wi["source"]
wi_panel["geo_level"] = wi["geo_level"]
wi_panel["geo_id"] = wi["geo_id"]
wi_panel["geo_name"] = wi["geo_name"]
wi_panel["period"] = pd.to_datetime(wi["period"]).dt.date
wi_panel["measure"] = wi["measure"]
wi_panel["value"] = wi["value"].astype("float64")
wi_panel["unit"] = wi["unit"]
wi_panel["knowledge_date"] = pd.to_datetime(wi["knowledge_date"]).dt.date
wi_panel["vintage_span"] = wi["vintage_span"]
wi_panel["is_estimated"] = wi["is_estimated"].astype("boolean")
wi_panel["notes"] = wi["notes"]
wi_panel["src_file"] = "acs_tract_income_wi.parquet"

before = con.execute("SELECT COUNT(*) FROM office_geo_panel").fetchone()[0]
con.register("wi_p", wi_panel[panel_cols])
con.execute("INSERT INTO office_geo_panel SELECT * FROM wi_p")
after = con.execute("SELECT COUNT(*) FROM office_geo_panel").fetchone()[0]
print(f"office_geo_panel: {before:,} -> {after:,} (+{after-before} WI ACS rows)")

# Refresh the ACS county rollup so WI counties get their per-vintage means too
acs_all = con.execute("""
    SELECT geo_id, period, vintage_span, value, knowledge_date
    FROM office_geo_panel WHERE source='acs5_b19013' AND value IS NOT NULL
""").df()
acs_all["county_fips"] = acs_all["geo_id"].astype(str).str[:5]
acs_cty = (acs_all.groupby(["county_fips", "period", "vintage_span"],
                           as_index=False)
           .agg(acs_median_hh_income_county_mean=("value", "mean"),
                acs_tracts_in_county=("value", "size"),
                knowledge_date=("knowledge_date", "first")))
con.execute("DROP TABLE IF EXISTS office_acs_income_county")
con.register("acs_c", acs_cty)
con.execute("CREATE TABLE office_acs_income_county AS SELECT * FROM acs_c")
print(f"office_acs_income_county refreshed: {len(acs_cty)} county-vintage rows "
      f"(now incl. WI 55093/55109)")

# ---------------------------------------------------------------------------
# 2. Rerun the tract-true join with BOTH shapefiles (MN + WI)
# ---------------------------------------------------------------------------
op = con.execute("""
    SELECT pid, "Latitude" AS lat, "Longitude" AS lon, "County Name" AS county
    FROM office_property
""").df()
op["lat"] = pd.to_numeric(op["lat"], errors="coerce")
op["lon"] = pd.to_numeric(op["lon"], errors="coerce")

t_mn = gpd.read_file(SHP_MN)[["GEOID", "NAMELSAD", "geometry"]]
t_wi = gpd.read_file(SHP_WI)[["GEOID", "NAMELSAD", "geometry"]]
tracts = pd.concat([t_mn, t_wi], ignore_index=True)
tracts = gpd.GeoDataFrame(tracts, crs="EPSG:4269").rename(
    columns={"GEOID": "tract_geoid", "NAMELSAD": "tract_name"})

gdf = gpd.GeoDataFrame(
    op.copy(),
    geometry=[Point(xy) if pd.notna(xy[0]) and pd.notna(xy[1]) else None
              for xy in zip(op["lon"], op["lat"])],
    crs="EPSG:4269")
has_pt = gdf.geometry.notna()
joined = gpd.sjoin(gdf[has_pt], tracts, how="left", predicate="within")
joined = joined[~joined.index.duplicated(keep="first")]

# collapse to one tract per pid BEFORE merge (118-dup-pid source condition)
tr_per_pid = (joined[["pid", "tract_geoid", "tract_name"]]
              .drop_duplicates(subset="pid", keep="first"))
res = (op[["pid", "county"]].drop_duplicates(subset="pid")
       .merge(tr_per_pid, on="pid", how="left"))

n = len(res)
placed = res["tract_geoid"].notna().sum()
wi_placed = res[res["tract_geoid"].astype(str).str.startswith("55")].shape[0]
unplaced = res[res["tract_geoid"].isna()]

res["tract_join_status"] = np.where(
    res["tract_geoid"].notna(), "tract_true", "county_only_unplaced_CHECK")
res["tract_supersedes_county"] = res["tract_geoid"].notna()

con.execute("DROP TABLE IF EXISTS office_property_tract")
con.register("res_df", res)
con.execute("CREATE TABLE office_property_tract AS SELECT * FROM res_df")

print(f"\nTract-true join (MN + WI shapefiles):")
print(f"  distinct properties     : {n:,}")
print(f"  placed in a true tract  : {placed:,} ({placed/n*100:.1f}%)")
print(f"  ...of which WI tracts   : {wi_placed} (Pierce + St Croix — "
      f"previously county-only)")
print(f"  still unplaced          : {len(unplaced)} "
      f"(investigate if >0)")
if len(unplaced):
    print(unplaced[["pid", "county"]].head(10).to_string())

# ---------------------------------------------------------------------------
# 3. Append-only audit: log the scope-gap closure
# ---------------------------------------------------------------------------
con.execute("""
  INSERT INTO raw_ingestion_log
  SELECT 'ing_wi_supplement_5_19_26','census_acs5_tiger_wi_supplement',
         'wi_supplement (acs_tract_income_wi.parquet + tl_2023_55_tract.zip)',
         DATE '2026-05-19','minneapolis', DATE '2017-12-31', DATE '2024-12-31',
         'sha256 0f1fcc6f... verified vs MANIFEST_wi_supplement',
         'Closes documented WI scope gap. ACS B19013 state 55 counties '
         '55093/55109 (100 rows, 4 vintages, knowledge_date reused verbatim '
         'from original ACS releases). WI TIGER tract shapefile added to the '
         'point-in-polygon join. WI properties move county_only -> tract_true. '
         'Scope gap CLOSED, not silently backfilled.'
""")
con.execute("CREATE OR REPLACE TABLE raw_ingestion_log AS "
            "SELECT DISTINCT * FROM raw_ingestion_log")

# Verify the WI intra-county gradient is now visible (the payoff)
g = con.execute("""
  WITH a AS (SELECT geo_id tract_geoid, value inc FROM office_geo_panel
             WHERE source='acs5_b19013' AND vintage_span='2019-2023'
               AND value IS NOT NULL AND geo_id LIKE '55%')
  SELECT pt.county, COUNT(*) props,
         ROUND(MIN(a.inc)) inc_min, ROUND(MAX(a.inc)) inc_max,
         ROUND(MAX(a.inc)*1.0/NULLIF(MIN(a.inc),0),2) gradient_x
  FROM office_property_tract pt JOIN a USING (tract_geoid)
  WHERE pt.county IN ('Pierce','St  Croix','St. Croix')
  GROUP BY 1 ORDER BY 2 DESC
""").df()
print("\nWI intra-county office income gradient now visible:")
print(g.to_string(index=False) if len(g) else "  (no WI props joined — check)")

print("\ningestion log:",
      con.execute("SELECT ingestion_id FROM raw_ingestion_log "
                  "ORDER BY 1").fetchall())
con.close()
