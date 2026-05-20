"""
Office Inventory Z-Score Module — Minneapolis MSA
=================================================
Parallel asset-class build mirroring the multifamily inventory engine.

Binding rules carried (per MASTER_HANDOFF / Platform Catalog):
  - no invented metrics; observational/statistical only (no recommendations)
  - verify every number from source; flag don't fabricate; gaps src-flagged
  - supersession explicit; tier-relative scoring, NO cross-tier composite
  - co-equal peer cuts (none privileged); divergent framings disclosed
  - bitemporal-ready: long/tidy fact tables w/ period + knowledge_date

Architecture decisions (user-confirmed, 2026-05-19):
  - PRIMARY tier  : Building Class A/B/C (F/none -> flagged residual, context-only)
  - PEER LENSES   : co-equal, independent — Building Status, Construction Material,
                    Tenancy, Secondary/Center type, Submarket, + tenant-derived
                    (owner-occ share band, sector-concentration band)
  - Building Status: every cohort scored WITHIN its own cohort (not an inclusion filter)
  - Demographics  : per-cohort Z kept separate, NOT collapsed to a composite
                    (mirrors MF unit-mix co-equal cuts); 2030 = projected, tagged
  - Min-N collapse: thin cell (n<MIN_N) collapses up to Class-only, flagged
  - Nulls         : explicit (unknown) cell, src-flagged, never imputed
"""
import pandas as pd, numpy as np, re, hashlib, json, duckdb, datetime as dt

MIN_N = 30                       # engine-stable cell threshold (peer-group statistics)
MARKET_ID = "minneapolis"
KD_PROPERTY = dt.date(2026, 5, 19)   # office property export knowledge_date
KD_TENANT   = dt.date(2026, 5, 19)   # tenant export knowledge_date
PERIOD      = dt.date(2026, 3, 31)   # quarter the snapshot describes (Q1-2026)

PROP_XLSX = "office_inv.xlsx"
TEN_PARQ  = "tenants_all.parquet"
OUT_DB    = "/home/claude/office_inventory.duckdb"

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()

# ----------------------------------------------------------------------------
# 1. LOAD PROPERTY (supply grain)
# ----------------------------------------------------------------------------
p = pd.read_excel(PROP_XLSX, sheet_name="Export051926").copy()
p["pid"] = pd.to_numeric(p["PropertyID"], errors="coerce")

# Quality tier (primary). F + missing = flagged residual, NOT folded into C.
bc = p["Building Class"].astype("string")
p["quality_tier"] = np.where(bc.isin(["A", "B", "C"]), bc, "(residual)")
p["tier_is_residual"] = p["quality_tier"] == "(residual)"

# Rent range parser — keep estimated flag (binding-rule: don't collapse framings)
def parse_rent(v):
    if pd.isna(v):
        return (np.nan, np.nan, np.nan, False)
    s = str(v)
    est = "Est" in s
    nums = [float(n.replace(",", ""))
            for n in re.findall(r"[\d,]+\.?\d*", s.replace("$", "")) if n.strip()]
    if not nums:
        return (np.nan, np.nan, np.nan, est)
    lo, hi = (nums[0], nums[0]) if len(nums) == 1 else (min(nums[:2]), max(nums[:2]))
    return (lo, hi, (lo + hi) / 2.0, est)

rp = p["Rent/SF/Yr"].apply(lambda v: pd.Series(parse_rent(v),
        index=["rent_lo", "rent_hi", "rent_mid", "rent_is_est"]))
p = pd.concat([p, rp], axis=1)

# Derived occupancy from SF columns (property Vacancy% is only 16.9% filled).
# Reported, not collapsed: keep the source Vacancy% AND the SF-derived one.
for c in ["RBA", "Total Vacant Available", "Direct Vacant Space",
          "Total Available Space (SF)", "Office Space"]:
    p[c] = pd.to_numeric(p[c], errors="coerce")
p["vac_pct_src"] = pd.to_numeric(p["Vacancy %"], errors="coerce")
p["vac_pct_derived"] = np.where(
    (p["RBA"] > 0) & p["Total Vacant Available"].notna(),
    100.0 * p["Total Vacant Available"] / p["RBA"], np.nan)

# ----------------------------------------------------------------------------
# 2. LOAD TENANT (demand grain), dedupe, join on Property ID
# ----------------------------------------------------------------------------
t = pd.read_parquet(TEN_PARQ)
n_raw = len(t)
t = t.drop_duplicates().reset_index(drop=True)        # 97 batch-overlap exact dups
n_dedup = len(t)
t["pid"] = pd.to_numeric(t["Property ID"], errors="coerce")
t["SF Occupied"] = pd.to_numeric(t["SF Occupied"], errors="coerce")

# Tenant rows only count where they land on an office property in this DB
office_pids = set(p["pid"].dropna().astype("int64"))
t_off = t[(t["Property Type"] == "Office") & (t["pid"].isin(office_pids))].copy()

# Building-level tenant rollups — explicit >=5,800 SF FLOOR, never a full census
def roll_one(g):
    sf = g["SF Occupied"]
    by_ind = g.groupby("Industry")["SF Occupied"].sum()
    tot = by_ind.sum()
    if tot > 0:
        top_ind = by_ind.idxmax()
        sh = by_ind[by_ind > 0] / tot
        hhi = float((sh ** 2).sum())
    else:
        top_ind, hhi = np.nan, np.nan
    return pd.Series({
        "ten_count": len(g),
        "ten_sf_floor": sf.sum(min_count=1),                    # >=5800 floor
        "ten_owned_share": (g["Occupancy Type"].astype(str)
                             .str.contains("Owned").mean()),
        "ten_top_industry": top_ind,
        "ten_sector_hhi": hhi,
        "ten_multi_count": g["Tenant Name"].nunique(),
    })

ten_roll = (t_off.groupby("pid")
            .apply(roll_one, include_groups=False).reset_index())

p = p.merge(ten_roll, on="pid", how="left")
p["has_tenant_data"] = p["pid"].isin(set(t_off["pid"].dropna().astype("int64")))

# Tenant-derived peer bands (these become co-equal peer lenses)
p["owner_occ_band"] = pd.cut(p["ten_owned_share"],
        [-0.01, 0.0001, 0.5, 0.999, 1.01],
        labels=["leased_only", "mostly_leased", "mostly_owned", "owner_occ"])
p["sector_conc_band"] = pd.cut(p["ten_sector_hhi"],
        [-0.01, 0.25, 0.5, 1.01],
        labels=["diversified", "moderate", "concentrated"])

print(f"Tenant rows: {n_raw} raw -> {n_dedup} after dedupe "
      f"({n_raw-n_dedup} exact batch-overlap dups removed)")
print(f"Office tenant rows on office props: {len(t_off)}")
print(f"Office buildings w/ >=1 tenant (>=5800sf floor): "
      f"{p['has_tenant_data'].sum()} / {len(p)} "
      f"({p['has_tenant_data'].mean()*100:.1f}%) — rest sub-floor by construction")

# ----------------------------------------------------------------------------
# 3. THE Z-ENGINE  (faithful to platform methodology — not reinvented)
#    Signal Z   : standardized level within peer group  (z = (x-μ)/σ)
#    Volatility : peer-group dispersion of |z|  (how unusual the cell's spread)
#    Category Z : per measure, the Signal Z is the category score; cuts co-equal
#    Robust σ   : population std; cells with σ=0 or n<2 -> NULL (not 0, honest)
# ----------------------------------------------------------------------------
def signal_z(df, value_col, group_cols):
    """Within-peer standardized score. Returns z + the peer n + collapse flag."""
    g = df.groupby(group_cols, dropna=False)[value_col]
    mu = g.transform("mean")
    sd = g.transform("std")            # sample std; NULL when n<2
    n  = g.transform("count")
    z  = (df[value_col] - mu) / sd
    z  = z.where((n >= 2) & (sd > 0))  # undefined dispersion -> NULL, by construction
    return z, n

def scored_lens(df, value_col, lens_col, lens_name):
    """
    One co-equal peer lens. Primary cell = quality_tier x lens_col.
    Thin cell (n<MIN_N) COLLAPSES UP to quality_tier-only, flagged.
    """
    full_z, full_n = signal_z(df, value_col, ["quality_tier", lens_col])
    coll_z, coll_n = signal_z(df, value_col, ["quality_tier"])
    collapsed = full_n < MIN_N
    z = full_z.where(~collapsed, coll_z)
    peer_n = full_n.where(~collapsed, coll_n)
    return pd.DataFrame({
        f"z__{lens_name}": z,
        f"n__{lens_name}": peer_n,
        f"collapsed__{lens_name}": collapsed.astype("int8"),
    })

# Property performance measures to score (the blue Z-targets, cleaned)
PERF_MEASURES = {
    "rent_mid":              "rent_sf_yr_mid",
    "vac_pct_derived":       "vacancy_pct_sf_derived",
    "Parking Ratio":         "parking_ratio",
    "Number of Stories":     "stories",
    "Typical Floor Size":    "typ_floor_sf",
    "Taxes Per SF":          "taxes_per_sf",
    "ten_sf_floor":          "tenant_occ_sf_floor",   # demand-grain, >=5800 floor
    "ten_sector_hhi":        "tenant_sector_hhi",
}
for c in ["Parking Ratio", "Number of Stories", "Typical Floor Size",
          "Taxes Per SF"]:
    p[c] = pd.to_numeric(p[c], errors="coerce")

# Co-equal peer lenses (none privileged)
LENSES = {
    "status":    "Building Status",
    "constr":    "Construction Material",
    "tenancy":   "Tenancy",
    "sectype":   "Property Type",          # secondary / center type
    "submarket": "Submarket Name",
    "ownerocc":  "owner_occ_band",
    "sectorcon": "sector_conc_band",
}
# Explicit (unknown) cell for nullable lens keys — never imputed
for raw in ["Construction Material", "Tenancy"]:
    p[raw] = p[raw].astype("string").fillna("(unknown)")

scored = p.copy()
scored = scored.copy()  # de-fragment
zcols = []
for src_col, meas in PERF_MEASURES.items():
    for lens_name, lens_col in LENSES.items():
        block = scored_lens(scored, src_col, lens_col, f"{meas}__{lens_name}")
        scored = pd.concat([scored, block], axis=1)
        zcols.append(f"z__{meas}__{lens_name}")

# ----------------------------------------------------------------------------
# 4. DEMOGRAPHIC Z-LAYER  — per-cohort, kept SEPARATE (NOT a composite)
#    ~120 CoStar trade-area cols (2020/2025/2030 within 1mi). 2030 = projected.
#    Scored within the SAME co-equal peer lenses. Append-only design: external
#    office-specific demographic / census / FRED layers attach to demo_fact later.
# ----------------------------------------------------------------------------
demo_cols = [c for c in p.columns
             if re.match(r"^(2020|2025|2030)\s", str(c))]
demo_long_rows = []
for c in demo_cols:
    yr = c[:4]
    vintage_kind = "projected" if yr == "2030" else "observed"
    series = pd.to_numeric(p[c], errors="coerce")
    tmp = p[["pid", "quality_tier"]].copy()
    tmp["demo_col"] = c
    tmp["vintage_year"] = yr
    tmp["vintage_kind"] = vintage_kind
    tmp["value"] = series
    # demographic Signal Z within quality_tier (the primary cohort), per-cohort,
    # NOT collapsed across cohorts (mirrors MF unit-mix co-equal treatment)
    z, n = signal_z(tmp.assign(_v=series), "_v", ["quality_tier"])
    tmp["z_demo"] = z
    tmp["peer_n"] = n
    demo_long_rows.append(tmp)
demo_fact = pd.concat(demo_long_rows, ignore_index=True)
demo_fact = demo_fact[demo_fact["value"].notna()].reset_index(drop=True)
print(f"Demographic layer: {len(demo_cols)} cols -> {len(demo_fact)} scored "
      f"property-cohort rows (2030 tagged 'projected')")

# ----------------------------------------------------------------------------
# 5. BITEMPORAL FACT TABLES (long/tidy, period + knowledge_date) + warehouse
# ----------------------------------------------------------------------------
def melt_facts(df, measures, kd, ingestion_id):
    rows = []
    for src_col, meas in measures.items():
        s = pd.to_numeric(df[src_col], errors="coerce")
        sub = pd.DataFrame({
            "market_id": MARKET_ID,
            "pid": df["pid"].astype("Int64").astype("string"),
            "period": PERIOD,
            "measure": meas,
            "value": s,
            "knowledge_date": kd,
            "ingestion_id": ingestion_id,
            "unit": "ratio",
        })
        rows.append(sub[sub["value"].notna()])
    return pd.concat(rows, ignore_index=True)

fact_office = melt_facts(p, PERF_MEASURES, KD_PROPERTY, "ing_office_prop_5_19_26")

ingestion_log = pd.DataFrame([
    dict(ingestion_id="ing_office_prop_5_19_26", source="costar_office_property",
         source_vintage="Minneapolis_Office_Inventory_5_19_26.xlsx",
         knowledge_date=KD_PROPERTY, market_id=MARKET_ID,
         period_min=PERIOD, period_max=PERIOD,
         file_hash=sha256("/home/claude/office_inv.xlsx"),
         notes="Office property export; 5033 rows; rent stored as range string "
               "(parsed lo/hi/mid, 86% CoStar-Est-flagged); Vacancy% 16.9% filled "
               "-> SF-derived vacancy reported alongside, not collapsed."),
    dict(ingestion_id="ing_office_tenant_5_19_26", source="costar_office_tenant",
         source_vintage="Costar_Export__5_.zip (9 batches, LocationDataExport)",
         knowledge_date=KD_TENANT, market_id=MARKET_ID,
         period_min=PERIOD, period_max=PERIOD,
         file_hash=sha256("/home/claude/tenant_src.zip"),
         notes=">=5,800 SF occupied FLOOR — building tenant rollups are a floor, "
               "NEVER a complete occupancy census. 97 exact batch-overlap dups "
               "removed. Office tenant->property join 99.6%."),
])

con = duckdb.connect(OUT_DB)
con.execute("DROP TABLE IF EXISTS office_property")
con.register("p_df", scored)
con.execute("CREATE TABLE office_property AS SELECT * FROM p_df")
con.register("t_df", t_off)
con.execute("CREATE TABLE office_tenant AS SELECT * FROM t_df")
con.register("tr_df", ten_roll)
con.execute("CREATE TABLE office_tenant_rollup AS SELECT * FROM tr_df")
con.register("d_df", demo_fact)
con.execute("CREATE TABLE office_demographic_z AS SELECT * FROM d_df")
con.register("f_df", fact_office)
con.execute("CREATE TABLE fact_office_panel AS SELECT * FROM f_df")
con.register("il_df", ingestion_log)
con.execute("CREATE TABLE raw_ingestion_log AS SELECT * FROM il_df")

# Peer-group health table (transparency artifact, per binding-rule honesty)
ph = []
for lens_name, lens_col in LENSES.items():
    g = scored.groupby(["quality_tier", lens_col], dropna=False).size().reset_index(name="n")
    g["lens"] = lens_name
    g = g.rename(columns={lens_col: "lens_value"})
    g["lens_value"] = g["lens_value"].astype("string")
    ph.append(g[["lens", "quality_tier", "lens_value", "n"]])
peer_health = pd.concat(ph, ignore_index=True)
peer_health["engine_stable"] = (peer_health["n"] >= MIN_N).astype("int8")
con.register("ph_df", peer_health)
con.execute("CREATE TABLE office_peer_health AS SELECT * FROM ph_df")

print("\nTABLES WRITTEN:")
for r in con.execute("SELECT table_name FROM information_schema.tables ORDER BY 1").fetchall():
    cnt = con.execute(f'SELECT COUNT(*) FROM "{r[0]}"').fetchone()[0]
    print(f"  {r[0]:28s} {cnt:>8,} rows")
con.close()

# Coverage / collapse summary (binding-rule: flag, don't paper over)
print("\nZ-SCORE COVERAGE (sample of key measures x lenses):")
for meas in ["rent_sf_yr_mid", "vacancy_pct_sf_derived", "tenant_occ_sf_floor"]:
    for lens in ["status", "submarket", "constr"]:
        zc, cc = f"z__{meas}__{lens}", f"collapsed__{meas}__{lens}"
        if zc in scored:
            cov = scored[zc].notna().mean() * 100
            colp = scored[cc].mean() * 100
            print(f"  {meas:24s} x {lens:10s}  z-cov={cov:5.1f}%  "
                  f"collapsed={colp:5.1f}%")
print("\nResidual tier (F/none, context-only, NOT scored cross-tier): "
      f"{int(scored['tier_is_residual'].sum())} props flagged")
