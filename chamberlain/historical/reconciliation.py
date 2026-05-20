"""
Chamberlain Warehouse: MRI vs VG Reconciliation Engine

Compares MRI accounting system (batch 08) against VG property manager reports (batch 07).

Data sources:
  MRI: Structured monthly P&L from content_block.structured JSON (authoritative)
  VG:  GPR/NRI from weekly report text + occupancy from extracted_entities

Outputs:
  reconciliation_result table — per-year, per-metric comparison with variance
  reconciliation_monthly table — per-month, per-metric detail
"""

import json, re, sqlite3
from datetime import datetime
from collections import defaultdict

DB = "/tmp/cw_fix.sqlite"

# ── Schema ───────────────────────────────────────────────────────

CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS reconciliation_result (
    id TEXT PRIMARY KEY,
    year INTEGER,
    metric TEXT,
    mri_annual REAL,
    vg_annual REAL,
    variance REAL,
    variance_pct REAL,
    direction TEXT,
    severity TEXT,
    notes TEXT,
    computed_at TEXT
);

CREATE TABLE IF NOT EXISTS reconciliation_monthly (
    id TEXT PRIMARY KEY,
    year INTEGER,
    month INTEGER,
    metric TEXT,
    mri_value REAL,
    vg_value REAL,
    variance REAL,
    variance_pct REAL,
    vg_source_file TEXT,
    computed_at TEXT
);
"""

# ── Extract MRI Data ─────────────────────────────────────────────

def extract_mri_data(conn):
    """Pull structured monthly data from MRI content blocks."""
    mri = {}  # {year: {line_item: {month_num: value, 'annual': value}}}
    
    for row in conn.execute("""
        SELECT cb.file_id, cb.locator_section, cb.structured
        FROM content_block cb
        WHERE cb.file_id LIKE 'mri_08_%' AND cb.structured IS NOT NULL
    """):
        year_match = re.search(r'mri_08_(\d{4})', row['file_id'])
        if not year_match:
            continue
        year = int(year_match.group(1))
        if year not in mri:
            mri[year] = {}
        
        data = json.loads(row['structured'])
        section = row['locator_section']
        
        for item_name, item_data in data.items():
            full_key = f"{section}|{item_name}"
            monthly = {}
            annual = item_data.get('annual')
            
            for k, v in item_data.get('monthly', {}).items():
                if isinstance(v, (int, float)):
                    # Handle both "2023-01-31" and "2023-01" formats
                    m = re.match(r'(\d{4})-(\d{2})', str(k))
                    if m:
                        month_num = int(m.group(2))
                        monthly[month_num] = v
            
            # If no 'annual' key, sum monthly
            if annual is None and monthly:
                annual = sum(monthly.values())
            
            # Also handle case where structured is {item: {date: value}} (old format)
            if not monthly and isinstance(item_data, dict) and 'annual' not in item_data and 'monthly' not in item_data:
                for k, v in item_data.items():
                    if isinstance(v, (int, float)):
                        m = re.match(r'(\d{4})-(\d{2})', str(k))
                        if m:
                            month_num = int(m.group(2))
                            monthly[month_num] = v
                if monthly:
                    annual = sum(monthly.values())
            
            mri[year][full_key] = {'monthly': monthly, 'annual': annual}
    
    return mri


# ── Extract VG Data ──────────────────────────────────────────────

def extract_vg_gpr_nri(conn):
    """Parse GPR and NRI from VG weekly report text blocks."""
    vg = {}  # {year: {'gpr': {month: value}, 'nri': {month: value}, 'market_rent': {month: value}}}
    
    month_names = {
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
        'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
        'january': 1, 'february': 2, 'march': 3, 'april': 4,
        'june': 6, 'july': 7, 'august': 8, 'september': 9,
        'october': 10, 'november': 11, 'december': 12,
    }
    
    # Pattern: "Jan-23 $450,352 $443,973 $419,350" or "January $450,352 $443,973 $419,350"
    pat = re.compile(
        r'([A-Za-z]+)(?:-(\d{2}))?\s+'
        r'\$?([\d,]+)\s+'
        r'\$?([\d,]+)\s+'
        r'\$?([\d,]+)',
    )
    
    for row in conn.execute("""
        SELECT cb.file_id, cb.text
        FROM content_block cb
        WHERE cb.file_id LIKE 'vg_07_%'
        AND (cb.text LIKE '%Gross Potential%' OR cb.text LIKE '%Market Rent%Net Rental%')
        AND cb.contains_dollar_amounts = 1
    """):
        text = row['text']
        file_id = row['file_id']
        
        # Get year from file_id
        ym = re.search(r'vg_07_(\d{4})', file_id)
        if not ym:
            continue
        file_year = int(ym.group(1))
        
        for m in pat.finditer(text):
            month_name = m.group(1).lower()
            year_suffix = m.group(2)
            market_rent = float(m.group(3).replace(',', ''))
            gpr = float(m.group(4).replace(',', ''))
            nri = float(m.group(5).replace(',', ''))
            
            month_num = month_names.get(month_name)
            if not month_num:
                continue
            
            # Determine year
            if year_suffix:
                yr = 2000 + int(year_suffix)
            else:
                yr = file_year
            
            if yr not in vg:
                vg[yr] = {'gpr': {}, 'nri': {}, 'market_rent': {}}
            
            # Take the latest value for each month (later weekly reports have more complete data)
            vg[yr]['gpr'][month_num] = gpr
            vg[yr]['nri'][month_num] = nri
            vg[yr]['market_rent'][month_num] = market_rent
    
    return vg


def extract_vg_occupancy(conn):
    """Extract monthly occupancy from VG weekly extracted_entities."""
    occ = {}  # {year: {month: [values]}}
    
    for row in conn.execute("""
        SELECT cb.file_id, cb.extracted_entities
        FROM content_block cb
        WHERE cb.file_id LIKE 'vg_07_%'
        AND cb.extracted_entities IS NOT NULL
        AND cb.seq <= 3
    """):
        entities = json.loads(row['extracted_entities'])
        occ_val = entities.get('occupancy') or entities.get('occupancy_pct')
        if not occ_val:
            continue
        
        ym = re.search(r'vg_07_(\d{4})_(\d{2})', row['file_id'])
        if not ym:
            continue
        year = int(ym.group(1))
        month = int(ym.group(2))
        
        if year not in occ:
            occ[year] = {}
        if month not in occ[year]:
            occ[year][month] = []
        occ[year][month].append(float(occ_val))
    
    # Average multiple weekly readings per month
    result = {}
    for yr in occ:
        result[yr] = {}
        for mo, vals in occ[yr].items():
            result[yr][mo] = sum(vals) / len(vals)
    
    return result


def extract_vg_delinquency_gpr(conn):
    """Extract GPR from delinquency blocks (has monthly GPR column)."""
    vg_gpr = {}  # {year: {month: value}}
    
    month_names = {
        'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
        'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12,
    }
    
    pat = re.compile(r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d+\s+\$[\d,]+\s+\$[\d,]+\s+\$?([\d,]+)', re.IGNORECASE)
    
    for row in conn.execute("""
        SELECT cb.file_id, cb.text
        FROM content_block cb
        WHERE cb.file_id LIKE 'vg_07_%' AND cb.kind = 'delinquency'
    """):
        ym = re.search(r'vg_07_(\d{4})', row['file_id'])
        if not ym:
            continue
        file_year = int(ym.group(1))
        
        for m in pat.finditer(row['text']):
            month_name = m.group(1).lower()
            gpr_val = float(m.group(2).replace(',', ''))
            month_num = month_names.get(month_name)
            if not month_num:
                continue
            
            if file_year not in vg_gpr:
                vg_gpr[file_year] = {}
            vg_gpr[file_year][month_num] = gpr_val
    
    return vg_gpr


# ── Reconciliation ───────────────────────────────────────────────

def classify_variance(pct):
    """Classify variance severity."""
    if pct is None:
        return 'unknown'
    abs_pct = abs(pct)
    if abs_pct < 1.0:
        return 'immaterial'
    elif abs_pct < 3.0:
        return 'minor'
    elif abs_pct < 10.0:
        return 'notable'
    else:
        return 'significant'


def run_reconciliation():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    
    # Create tables
    for stmt in CREATE_TABLES.split(';'):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    
    # Clear previous results
    conn.execute("DELETE FROM reconciliation_result")
    conn.execute("DELETE FROM reconciliation_monthly")
    
    now = datetime.now().isoformat()
    
    # Extract data
    print("Extracting MRI data...")
    mri = extract_mri_data(conn)
    print(f"  MRI years: {sorted(mri.keys())}")
    
    print("Extracting VG GPR/NRI...")
    vg_fin = extract_vg_gpr_nri(conn)
    print(f"  VG financial years: {sorted(vg_fin.keys())}")
    
    print("Extracting VG occupancy...")
    vg_occ = extract_vg_occupancy(conn)
    print(f"  VG occupancy years: {sorted(vg_occ.keys())}")
    
    print("Extracting VG delinquency GPR...")
    vg_dq_gpr = extract_vg_delinquency_gpr(conn)
    print(f"  VG delinquency GPR years: {sorted(vg_dq_gpr.keys())}")
    
    result_id = 0
    monthly_id = 0
    
    # ── Compare GPR: MRI vs VG ──
    for year in sorted(set(mri.keys()) & set(vg_fin.keys())):
        mri_year = mri[year]
        vg_year = vg_fin[year]
        
        # Find MRI GPR line item
        mri_gpr_key = None
        for k in mri_year:
            if 'Gross Potential Rent' in k:
                mri_gpr_key = k
                break
        
        if mri_gpr_key and 'gpr' in vg_year:
            mri_item = mri_year[mri_gpr_key]
            
            # Monthly comparison
            mri_annual_sum = 0
            vg_annual_sum = 0
            months_compared = 0
            
            for mo in range(1, 13):
                mri_val = mri_item['monthly'].get(mo)
                vg_val = vg_year['gpr'].get(mo)
                
                if mri_val is not None and vg_val is not None:
                    monthly_id += 1
                    var = vg_val - mri_val
                    var_pct = (var / mri_val * 100) if mri_val != 0 else None
                    
                    conn.execute("""INSERT INTO reconciliation_monthly
                        (id, year, month, metric, mri_value, vg_value, variance, variance_pct, computed_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (f"rm_{monthly_id}", year, mo, "gross_potential_rent",
                         mri_val, vg_val, var, var_pct, now))
                    
                    mri_annual_sum += mri_val
                    vg_annual_sum += vg_val
                    months_compared += 1
                elif mri_val is not None:
                    mri_annual_sum += mri_val
                elif vg_val is not None:
                    vg_annual_sum += vg_val
            
            if months_compared > 0:
                result_id += 1
                var = vg_annual_sum - mri_annual_sum
                var_pct = (var / mri_annual_sum * 100) if mri_annual_sum != 0 else None
                
                conn.execute("""INSERT INTO reconciliation_result
                    (id, year, metric, mri_annual, vg_annual, variance, variance_pct,
                     direction, severity, notes, computed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (f"rr_{result_id}", year, "gross_potential_rent",
                     mri_annual_sum, vg_annual_sum, var, var_pct,
                     'vg_higher' if var > 0 else 'mri_higher',
                     classify_variance(var_pct),
                     f"Compared {months_compared}/12 months. MRI=Gross Potential Rent-Residential, VG=weekly report GPR column.",
                     now))
        
        # NRI comparison
        mri_nri_key = None
        for k in mri_year:
            if 'Total Rental Revenue' in k:
                mri_nri_key = k
                break
        
        if mri_nri_key and 'nri' in vg_year:
            mri_item = mri_year[mri_nri_key]
            
            mri_annual_sum = 0
            vg_annual_sum = 0
            months_compared = 0
            
            for mo in range(1, 13):
                mri_val = mri_item['monthly'].get(mo)
                vg_val = vg_year['nri'].get(mo)
                
                if mri_val is not None and vg_val is not None:
                    monthly_id += 1
                    var = vg_val - mri_val
                    var_pct = (var / mri_val * 100) if mri_val != 0 else None
                    
                    conn.execute("""INSERT INTO reconciliation_monthly
                        (id, year, month, metric, mri_value, vg_value, variance, variance_pct, computed_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (f"rm_{monthly_id}", year, mo, "net_rental_income",
                         mri_val, vg_val, var, var_pct, now))
                    
                    mri_annual_sum += mri_val
                    vg_annual_sum += vg_val
                    months_compared += 1
                elif mri_val is not None:
                    mri_annual_sum += mri_val
                elif vg_val is not None:
                    vg_annual_sum += vg_val
            
            if months_compared > 0:
                result_id += 1
                var = vg_annual_sum - mri_annual_sum
                var_pct = (var / mri_annual_sum * 100) if mri_annual_sum != 0 else None
                
                conn.execute("""INSERT INTO reconciliation_result
                    (id, year, metric, mri_annual, vg_annual, variance, variance_pct,
                     direction, severity, notes, computed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (f"rr_{result_id}", year, "net_rental_income",
                     mri_annual_sum, vg_annual_sum, var, var_pct,
                     'vg_higher' if var > 0 else 'mri_higher',
                     classify_variance(var_pct),
                     f"Compared {months_compared}/12 months. MRI=Total Rental Revenue-Multi-Family, VG=weekly report NRI column.",
                     now))
    
    # ── MRI-only annual totals (for years without VG overlap) ──
    for year in sorted(mri.keys()):
        mri_year = mri[year]
        
        for search_key, metric_name in [
            ('TOTAL INCOME', 'total_income'),
            ('NET OPERATING INCOME', 'net_operating_income'),
            ('TOTAL OPERATING EXPENSES', 'total_operating_expenses'),
            ('NET INCOME', 'net_income'),
        ]:
            for k, v in mri_year.items():
                if search_key in k and v.get('annual') is not None:
                    result_id += 1
                    conn.execute("""INSERT INTO reconciliation_result
                        (id, year, metric, mri_annual, vg_annual, variance, variance_pct,
                         direction, severity, notes, computed_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (f"rr_{result_id}", year, metric_name,
                         v['annual'], None, None, None,
                         'mri_only', 'reference',
                         f"MRI-only reference value. No comparable VG line item extracted.",
                         now))
                    break
    
    # ── Occupancy comparison (MRI vacancy vs VG reported occupancy) ──
    for year in sorted(set(mri.keys()) & set(vg_occ.keys())):
        mri_year = mri[year]
        
        # Get MRI GPR and vacancy to compute implied occupancy
        mri_gpr_key = None
        mri_vac_key = None
        for k in mri_year:
            if 'Gross Potential Rent' in k and 'Total' not in k:
                mri_gpr_key = k
            if 'Unit Vacancy' in k:
                mri_vac_key = k
        
        if mri_gpr_key and mri_vac_key:
            mri_gpr = mri_year[mri_gpr_key]
            mri_vac = mri_year[mri_vac_key]
            
            mri_occ_months = {}
            for mo in range(1, 13):
                gpr_val = mri_gpr['monthly'].get(mo)
                vac_val = mri_vac['monthly'].get(mo)  # negative number
                if gpr_val and gpr_val > 0 and vac_val is not None:
                    occ_pct = (1.0 + vac_val / gpr_val) * 100
                    mri_occ_months[mo] = occ_pct
            
            months_compared = 0
            mri_avg = 0
            vg_avg = 0
            
            for mo in range(1, 13):
                mri_occ = mri_occ_months.get(mo)
                vg_occ_val = vg_occ.get(year, {}).get(mo)
                
                if mri_occ is not None and vg_occ_val is not None:
                    monthly_id += 1
                    var = vg_occ_val - mri_occ
                    
                    conn.execute("""INSERT INTO reconciliation_monthly
                        (id, year, month, metric, mri_value, vg_value, variance, variance_pct, computed_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (f"rm_{monthly_id}", year, mo, "occupancy_pct",
                         round(mri_occ, 1), vg_occ_val, round(var, 1), None, now))
                    
                    mri_avg += mri_occ
                    vg_avg += vg_occ_val
                    months_compared += 1
            
            if months_compared > 0:
                result_id += 1
                mri_avg /= months_compared
                vg_avg /= months_compared
                var = vg_avg - mri_avg
                
                conn.execute("""INSERT INTO reconciliation_result
                    (id, year, metric, mri_annual, vg_annual, variance, variance_pct,
                     direction, severity, notes, computed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (f"rr_{result_id}", year, "occupancy_pct_avg",
                     round(mri_avg, 1), round(vg_avg, 1), round(var, 1), None,
                     'vg_higher' if var > 0 else 'mri_higher',
                     'minor' if abs(var) < 2 else 'notable',
                     f"MRI implied occ = (GPR-Vacancy)/GPR. VG = weekly report stated occupancy. "
                     f"Compared {months_compared}/12 months.",
                     now))
    
    conn.commit()
    
    # ── Report ───────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("RECONCILIATION COMPLETE")
    print(f"{'='*70}")
    
    results = conn.execute("SELECT COUNT(*) FROM reconciliation_result").fetchone()[0]
    monthlys = conn.execute("SELECT COUNT(*) FROM reconciliation_monthly").fetchone()[0]
    print(f"Annual results:  {results}")
    print(f"Monthly details: {monthlys}")
    
    print(f"\n--- Annual Summary ---")
    for r in conn.execute("""
        SELECT year, metric, mri_annual, vg_annual, variance, variance_pct, severity
        FROM reconciliation_result
        WHERE vg_annual IS NOT NULL
        ORDER BY year, metric
    """):
        var_str = f"${r['variance']:+,.0f}" if r['variance'] else "N/A"
        pct_str = f"{r['variance_pct']:+.1f}%" if r['variance_pct'] else ""
        print(f"  {r['year']} {r['metric']:30s} MRI=${r['mri_annual']:>12,.0f}  VG=${r['vg_annual']:>12,.0f}  Var={var_str:>12s} {pct_str:>8s}  [{r['severity']}]")
    
    print(f"\n--- MRI Reference Totals ---")
    for r in conn.execute("""
        SELECT year, metric, mri_annual
        FROM reconciliation_result
        WHERE vg_annual IS NULL AND metric IN ('total_income', 'net_operating_income', 'net_income')
        ORDER BY year, metric
    """):
        print(f"  {r['year']} {r['metric']:30s} ${r['mri_annual']:>12,.0f}")
    
    conn.close()


if __name__ == '__main__':
    run_reconciliation()
