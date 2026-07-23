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

Run on Windows:  python export_for_mac.py
Then move capactive_data_export.zip to the Mac and follow the printed import steps.
"""
import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ZIP = os.path.join(HERE, "capactive_data_export.zip")
WAREHOUSE = os.path.join(HERE, "data", "warehouse.duckdb")
CSV_DIR = os.path.join(HERE, "data", "warehouse_export")
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
    print("Exporting warehouse deal-analytics CSVs (Option B)…")
    export_warehouse_csvs()

    print("Building", os.path.basename(ZIP), "…")
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

rm -rf _import
# then restart:  CAPACTIVE_DEV_MODE=1 python3 run.py
""")


if __name__ == "__main__":
    main()
