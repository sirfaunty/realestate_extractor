"""
Export Capactic data from this Windows extraction device so the whole app can be
mirrored on another machine (Mac demo).

Bundles into `capactive_data_export.zip`:
  - CORE (per WINDOWS_DATA_EXPORT.md):
      * data/org_dev.db          — the extraction database
      * capactive_config.db      — org/user config
      * warehouse deal-analytics tables as CSVs (Option B — the 28 GB
        warehouse.duckdb itself is NOT shipped; the Mac imports these CSVs into its
        existing warehouse).
  - RETAIL (built in this workspace, gitignored/local-only) — so all three no-code
    modules work on the Mac:
      * barrington_db / southtown_db / midway_db : their data/ + source_docs/
  - PORTFOLIO SAMPLE — enough verified KA data that Portfolio Ownership,
    Deliverables, and Residential all render for UI work without the multi-GB
    corpus:
      * portfolio_warehouse.db sampled to 3 properties (Maplewood I ENGELS-2010
        w/ loans+leases, Northcourt KAINC-1016 w/ 3 facilities, Cottage Grove
        OSBORN-3020 w/ the 33-tenancy roster)
      * portfolio_rentroll.db (whole — small; deliverables economics + vacancy)
      * residential handoff_package (whole — small)
      * up to 4 newest generated .docx deliverables as samples

Run on Windows:  venv/Scripts/python export_for_mac.py
Then move capactive_data_export.zip to the Mac and follow the printed import steps.
"""
import os
import zipfile

PORTFOLIO_SAMPLE_KEYS = ["ENGELS-2010", "KAINC-1016", "OSBORN-3020"]
KEY_COLS = ("property_key", "entity_code", "property_id", "property_code")

HERE = os.path.dirname(os.path.abspath(__file__))
ZIP = os.path.join(HERE, "capactive_data_export.zip")
WAREHOUSE = os.path.join(HERE, "data", "warehouse.duckdb")
CSV_DIR = os.path.join(HERE, "data", "warehouse_export")
MASTER_DB = os.path.join(HERE, "portfolio_ownership",
                         "KA_OWNERSHIP_MODULE_MASTER_20260724",
                         "portfolio_warehouse.db")
RENTROLL_DB = os.path.join(HERE, "portfolio_ownership", "inbox",
                           "Portfolio Financial Source Data & Modules",
                           "Financial Modules",
                           "Final Portfolio Rent Roll Module_7.10.26",
                           "database", "portfolio_rentroll.db")
RESIDENTIAL_PKG = os.path.join(HERE, "portfolio_ownership", "residential",
                               "handoff_package")
DELIVERABLES_DIR = os.path.join(HERE, "data", "deliverables")
SAMPLE_MASTER = os.path.join(HERE, "data", "portfolio_warehouse_sample.db")
DEAL_TABLES = ["fact_deal_summary", "fact_proforma_annual", "fact_distribution_annual",
               "fact_debt_annual", "fact_tif_annual", "fact_tif_comparison", "dim_partner"]
RETAIL_ENGINES = ["barrington_db", "southtown_db", "midway_db"]


def _mb(b):
    return f"{b / 1048576:.1f} MB"


def _add_file(z, src, arc):
    if os.path.isfile(src):
        z.write(src, arc)
        return os.path.getsize(src)
    return 0


def _add_tree(z, root, arc_prefix):
    total = 0
    if not os.path.isdir(root):
        return 0
    for dp, _dn, fn in os.walk(root):
        for f in fn:
            s = os.path.join(dp, f)
            arc = f"{arc_prefix}/{os.path.relpath(s, root).replace(os.sep, '/')}"
            total += _add_file(z, s, arc)
    return total


def sample_master(src, dst, keys):
    """Copy the KA master schema + only the rows belonging to `keys`
    (MASTER property keys). Two passes: first learn each property's entity
    codes (loan tables mix composite ENGELS-2010 and bare 1603 keys), then
    filter every table that carries a key column. Keyless dimension/meta
    tables are copied whole. Pure stdlib sqlite3."""
    import sqlite3
    if not os.path.isfile(src):
        print("  master not found — skipping portfolio sample")
        return False
    if os.path.exists(dst):
        os.remove(dst)
    s = sqlite3.connect(f"file:{src.replace(os.sep, '/')}?mode=ro", uri=True)
    d = sqlite3.connect(dst)
    objs = s.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'").fetchall()

    keyset = {str(k) for k in keys} | {str(k).split('-')[-1] for k in keys}
    # pass 1: collect entity codes tied to the sampled property keys
    for typ, name, _sql in objs:
        if typ != 'table':
            continue
        cols = [r[1] for r in s.execute(f'PRAGMA table_info("{name}")')]
        low = [c.lower() for c in cols]
        if 'property_key' in low and 'entity_code' in low:
            pk = cols[low.index('property_key')]
            ec = cols[low.index('entity_code')]
            ph = ",".join("?" * len(keyset))
            for (v,) in s.execute(
                    f'SELECT DISTINCT "{ec}" FROM "{name}" '
                    f'WHERE CAST("{pk}" AS TEXT) IN ({ph})', tuple(keyset)):
                if v is not None:
                    keyset.add(str(v))

    # pass 2: create tables and copy filtered rows
    total = 0
    for typ, name, sql in objs:
        if typ != 'table':
            continue
        d.execute(sql)
        cols = [r[1] for r in s.execute(f'PRAGMA table_info("{name}")')]
        low = [c.lower() for c in cols]
        kc = [cols[i] for i, c in enumerate(low) if c in KEY_COLS]
        if kc:
            ph = ",".join("?" * len(keyset))
            where = " OR ".join(
                f'CAST("{c}" AS TEXT) IN ({ph})' for c in kc)
            rows = s.execute(f'SELECT * FROM "{name}" WHERE {where}',
                             tuple(keyset) * len(kc)).fetchall()
            tag = ""
        else:
            rows = s.execute(f'SELECT * FROM "{name}"').fetchall()
            tag = "  (no key col — copied whole)"
        if rows:
            d.executemany(
                f'INSERT INTO "{name}" VALUES '
                f'({",".join("?" * len(cols))})', rows)
        total += len(rows)
        print(f"  {name}: {len(rows)} rows{tag}")

    # indexes / views / triggers last (best-effort)
    for typ, _name, sql in objs:
        if typ in ('index', 'view', 'trigger'):
            try:
                d.execute(sql)
            except Exception:
                pass
    d.commit()
    d.close()
    s.close()
    print(f"  sample rows total: {total:,}  "
          f"({_mb(os.path.getsize(dst))} vs full {_mb(os.path.getsize(src))})")
    return True


def export_warehouse_csvs():
    """Option B: dump the deal-analytics tables to CSV. Skips silently if duckdb or the
    warehouse isn't available (the Mac already has the market-data tables)."""
    try:
        import duckdb
    except Exception:
        print("  warehouse: duckdb not installed — skipping CSV export")
        return
    if not os.path.isfile(WAREHOUSE):
        print("  warehouse: data/warehouse.duckdb not found — skipping CSV export")
        return
    os.makedirs(CSV_DIR, exist_ok=True)
    conn = duckdb.connect(WAREHOUSE, read_only=True)
    for t in DEAL_TABLES:
        try:
            n = conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            if n:
                out = os.path.join(CSV_DIR, f"{t}.csv").replace(os.sep, "/")
                conn.execute(f"COPY {t} TO '{out}' (HEADER, DELIMITER ',')")
                print(f"  warehouse CSV {t}: {n} rows")
        except Exception as e:
            print(f"  warehouse {t}: skip ({str(e)[:50]})")
    conn.close()


def main():
    import argparse
    ap = argparse.ArgumentParser(
        description="Bundle Capactive data for another machine. The portfolio "
                    "master is SAMPLED to a property list so you never ship "
                    "the full multi-GB corpus — pass more keys as you need "
                    "more properties on the other device.")
    ap.add_argument("--properties",
                    default=",".join(PORTFOLIO_SAMPLE_KEYS),
                    help="comma-separated MASTER property keys to include "
                         f"(default: {','.join(PORTFOLIO_SAMPLE_KEYS)})")
    ap.add_argument("--skip-portfolio", action="store_true",
                    help="omit the portfolio sample entirely (original core/"
                         "retail export only)")
    args = ap.parse_args()
    prop_keys = [k.strip() for k in args.properties.split(",") if k.strip()]

    print("Exporting warehouse deal-analytics CSVs (Option B)…")
    export_warehouse_csvs()

    have_sample = False
    if not args.skip_portfolio:
        print(f"\nSampling portfolio master → {len(prop_keys)} properties: "
              f"{', '.join(prop_keys)}")
        have_sample = sample_master(MASTER_DB, SAMPLE_MASTER, prop_keys)

    print("\nBuilding", os.path.basename(ZIP), "…")
    sizes = {}
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        sizes["org_dev.db"] = _add_file(z, os.path.join(HERE, "data", "org_dev.db"), "org_dev.db")
        sizes["capactive_config.db"] = _add_file(
            z, os.path.join(HERE, "capactive_config.db"), "capactive_config.db")
        sizes["warehouse CSVs"] = _add_tree(z, CSV_DIR, "warehouse_export")
        retail = 0
        for eng in RETAIL_ENGINES:
            retail += _add_tree(z, os.path.join(HERE, eng, "data"), f"retail/{eng}/data")
            retail += _add_tree(z, os.path.join(HERE, eng, "source_docs"),
                                f"retail/{eng}/source_docs")
        sizes["retail data + source docs"] = retail

        if not args.skip_portfolio:
            port = 0
            if have_sample:
                port += _add_file(z, SAMPLE_MASTER,
                                  "portfolio/portfolio_warehouse.db")
            port += _add_file(z, RENTROLL_DB,
                              "portfolio/portfolio_rentroll.db")
            port += _add_tree(z, RESIDENTIAL_PKG,
                              "portfolio/residential/handoff_package")
            docx = 0
            if os.path.isdir(DELIVERABLES_DIR):
                newest = sorted(
                    (f for f in os.listdir(DELIVERABLES_DIR)
                     if f.lower().endswith(".docx")),
                    key=lambda f: os.path.getmtime(
                        os.path.join(DELIVERABLES_DIR, f)),
                    reverse=True)[:4]
                for f in newest:
                    docx += _add_file(z, os.path.join(DELIVERABLES_DIR, f),
                                      f"portfolio/deliverables/{f}")
            sizes["portfolio sample"] = port + docx

    print("\nWrote", ZIP)
    for k, v in sizes.items():
        print(f"  {k:28} {_mb(v)}")
    print(f"  {'ZIP on disk (compressed)':28} {_mb(os.path.getsize(ZIP))}")

    print("\n" + "=" * 70)
    print("MAC IMPORT — from the repo root on the Mac (after copying the zip over):")
    print("=" * 70)
    print("""
unzip -o capactive_data_export.zip -d _import

# core databases
cp _import/org_dev.db data/org_dev.db
cp _import/capactive_config.db capactive_config.db

# retail module data + source docs (all three modules)
cp -R _import/retail/. .

# warehouse deal-analytics CSVs -> existing Mac warehouse
python3 -c "
import duckdb, os, glob
d='_import/warehouse_export'
if os.path.isdir(d):
    c=duckdb.connect('data/warehouse.duckdb')
    for f in glob.glob(d+'/*.csv'):
        t=os.path.splitext(os.path.basename(f))[0]
        try:
            c.execute(f'DELETE FROM {t}')
            c.execute(f\\\"COPY {t} FROM '{f}' (HEADER, DELIMITER ',')\\\")
            print('imported', t, c.execute(f'SELECT count(*) FROM {t}').fetchone()[0])
        except Exception as e: print(t,'skip',str(e)[:50])
    c.close()
"

# portfolio sample -> where the module engines' fallback paths look
mkdir -p portfolio_ownership data/deliverables
cp _import/portfolio/portfolio_warehouse.db portfolio_ownership/ 2>/dev/null
cp _import/portfolio/portfolio_rentroll.db portfolio_ownership/ 2>/dev/null
cp -R _import/portfolio/residential portfolio_ownership/ 2>/dev/null
cp _import/portfolio/deliverables/* data/deliverables/ 2>/dev/null

rm -rf _import
# then restart:  CAPACTIVE_DEV_MODE=1 python3 run.py
""")


if __name__ == "__main__":
    main()
