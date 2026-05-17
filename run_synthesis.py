#!/usr/bin/env python3
"""
Run financial synthesis on a property and print results.

Usage:
    cd ~/Desktop/realestate_extractor
    python3 run_synthesis.py                    # lists available properties
    python3 run_synthesis.py --property-id 1    # synthesize property 1
    python3 run_synthesis.py --all              # synthesize all properties

Run this AFTER run_analysis.py completes to see the reconciled financial summary.
"""
import sys
import os
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from realestate_extractor.database import Database
from realestate_extractor.financial_synthesis import FinancialSynthesizer

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'org_dev.db')


def list_properties(db):
    """List all properties and their operating item counts."""
    rows = db.conn.execute("""
        SELECT p.id, p.name,
               COUNT(DISTINCT d.id) as doc_count,
               COUNT(os.id) as item_count
        FROM properties p
        LEFT JOIN documents d ON d.property_id = p.id
        LEFT JOIN operating_statement_items os ON os.document_id = d.id
        GROUP BY p.id
        ORDER BY p.id
    """).fetchall()
    if not rows:
        print("No properties found in database.")
        return []
    print(f"\n{'ID':>4}  {'Property Name':<40} {'Docs':>5}  {'Op Items':>9}")
    print("-" * 65)
    for r in rows:
        print(f"{r[0]:>4}  {r[1] or '(unnamed)':<40} {r[2]:>5}  {r[3]:>9}")
    return rows


def synthesize_property(db, property_id, validated_data=None):
    """Run synthesis on a single property and print results."""
    # Get property info
    prop = db.conn.execute(
        "SELECT id, name FROM properties WHERE id = ?", (property_id,)
    ).fetchone()
    if not prop:
        print(f"Error: Property ID {property_id} not found.")
        return

    prop_name = prop[1] or f"Property #{prop[0]}"
    synth = FinancialSynthesizer(db)

    # Check if there's data
    count = db.conn.execute("""
        SELECT COUNT(*) FROM operating_statement_items os
        JOIN documents d ON os.document_id = d.id
        WHERE d.property_id = ?
    """, (property_id,)).fetchone()[0]
    print(f"Operating statement items for {prop_name}: {count}")
    if count == 0:
        print(f"\nNo data found. Run 'python3 run_analysis.py -p {property_id}' first.")
        return

    print(f"\nRunning financial synthesis for {prop_name} (property_id={property_id})...")
    print("=" * 70)

    result = synth.synthesize(property_id)

    # NOI Timeline
    hidden = result.get('secondary_periods', [])
    print(f"\nNOI TIMELINE — {len(result['periods'])} periods" +
          (f" ({len(hidden)} low-quality hidden)" if hidden else ""))
    print("-" * 70)
    print(f"{'Period':<10} {'Income':>14} {'Expenses':>14} {'Calc NOI':>14} {'Rptd NOI':>14} {'Sources':>8}")
    print("-" * 70)
    for t in result['noi_timeline']:
        ps = result['period_summaries'][t['period']]
        rptd = f"${t['reported_noi']:,.0f}" if t['reported_noi'] else "—"
        flag = " *" if t['has_discrepancies'] else ""
        print(f"{t['period']:<10} ${ps['total_income']:>13,.0f} ${ps['total_expenses']:>13,.0f} ${t['calculated_noi']:>13,.0f} {rptd:>14} {t['source_count']:>7}{flag}")

    # Document sources
    print(f"\nSOURCE DOCUMENTS")
    print("-" * 70)
    for src in result['document_sources']:
        stars = "*" * src['authority'] + "." * (5 - src['authority'])
        print(f"  [{stars}] {src['filename']} ({src['doc_type']}, {src['item_count']} items)")

    # Discrepancies
    all_disc = []
    for period in result['periods']:
        for d in result['period_summaries'][period]['discrepancies']:
            all_disc.append(d)

    if all_disc:
        print(f"\nDISCREPANCIES ({len(all_disc)})")
        print("-" * 70)
        for d in all_disc:
            print(f"  ! {d}")

    # Synthesis notes
    print(f"\nNOTES")
    print("-" * 70)
    for note in result['synthesis_notes']:
        print(f"  - {note}")

    # Compare against validated data if available
    prop_validated = None
    if validated_data:
        # Match property by name (case-insensitive partial match)
        prop_lower = prop_name.lower()
        for vname, vdata in validated_data.items():
            if isinstance(vdata, dict) and (vname.lower() in prop_lower or prop_lower in vname.lower()):
                prop_validated = vdata
                print(f"\n  Matched validated data: '{vname}'")
                break

    if prop_validated:
        validated_noi = {}
        for period_key, period_data in prop_validated.items():
            if isinstance(period_data, dict) and 'noi' in period_data:
                validated_noi[period_key] = period_data['noi']

        if validated_noi:
            print(f"\n{'=' * 70}")
            print(f"COMPARISON AGAINST VALIDATED DATA — {prop_name}")
            print("=" * 70)

            comparisons = synth.compare_noi(property_id, validated_noi)
            print(f"\n{'Period':<10} {'Validated':>14} {'Calculated':>14} {'Diff':>12} {'Diff%':>8} {'Closest':>14} {'Match%':>8}")
            print("-" * 82)
            for c in comparisons:
                if c.get('note'):
                    print(f"{c['period']:<10} ${c['validated']:>13,.0f}  {'— ' + c['note']:>58}")
                    continue
                val = f"${c['validated']:,.0f}"
                calc = f"${c.get('calculated', 0):,.0f}" if c.get('calculated') else "—"
                diff = f"${c.get('calc_diff', 0):,.0f}" if c.get('calc_diff') else "—"
                diff_pct = f"{c.get('calc_diff_pct', 0):.1f}%" if c.get('calc_diff_pct') else "—"
                closest = f"${c.get('reported', 0):,.0f}" if c.get('reported') else "—"
                closest_pct = f"{c.get('closest_diff_pct', 0):.1f}%" if c.get('closest_diff_pct') else "—"
                print(f"{c['period']:<10} {val:>14} {calc:>14} {diff:>12} {diff_pct:>8} {closest:>14} {closest_pct:>8}")

            # Income/expense comparison
            print(f"\nINCOME / EXPENSE COMPARISON")
            print("-" * 82)
            print(f"{'Period':<10} {'':>10} {'Validated':>14} {'Extracted':>14} {'Diff':>12} {'Diff%':>8}")
            print("-" * 82)
            for period_key in sorted(prop_validated.keys()):
                vdata = prop_validated[period_key]
                if not isinstance(vdata, dict):
                    continue
                ps = result['period_summaries'].get(period_key, {})
                if not ps:
                    continue
                # Income
                v_inc = vdata.get('total_income', 0)
                e_inc = ps.get('total_income', 0)
                inc_diff = e_inc - v_inc
                inc_pct = f"{inc_diff/v_inc*100:.1f}%" if v_inc else "—"
                print(f"{period_key:<10} {'Income':>10} ${v_inc:>13,.0f} ${e_inc:>13,.0f} ${inc_diff:>11,.0f} {inc_pct:>8}")
                # Expense
                v_exp = vdata.get('total_opex', 0)
                e_exp = ps.get('total_expenses', 0)
                exp_diff = e_exp - v_exp
                exp_pct = f"{exp_diff/v_exp*100:.1f}%" if v_exp else "—"
                print(f"{'':10} {'Expense':>10} ${v_exp:>13,.0f} ${e_exp:>13,.0f} ${exp_diff:>11,.0f} {exp_pct:>8}")
                # NOI
                v_noi = vdata.get('noi', 0)
                e_noi = ps.get('calculated_noi', 0)
                noi_diff = e_noi - v_noi
                noi_pct = f"{noi_diff/v_noi*100:.1f}%" if v_noi else "—"
                print(f"{'':10} {'NOI':>10} ${v_noi:>13,.0f} ${e_noi:>13,.0f} ${noi_diff:>11,.0f} {noi_pct:>8}")
                print()

    print(f"\n{'=' * 70}")
    print(f"Full synthesis JSON: GET /api/property/{property_id}/synthesis")
    print(f"Or view in Property Dashboard → Valuation / NOI tab")


def main():
    parser = argparse.ArgumentParser(
        description="Run financial synthesis on property data"
    )
    parser.add_argument('--property-id', '-p', type=int,
                        help='Property ID to synthesize')
    parser.add_argument('--all', action='store_true',
                        help='Synthesize all properties')
    args = parser.parse_args()

    db = Database(DB_PATH)
    db.connect()

    # Load validated data if available
    validated_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        'reference_data', 'validated', 'historical_pl.json'
    )
    validated_data = None
    if os.path.exists(validated_path):
        with open(validated_path) as f:
            validated_data = json.load(f)

    if args.all:
        rows = db.conn.execute(
            "SELECT id FROM properties ORDER BY id"
        ).fetchall()
        if not rows:
            print("No properties found.")
            db.close()
            return
        for (prop_id,) in rows:
            synthesize_property(db, prop_id, validated_data)
            print("\n" + "=" * 70 + "\n")
    elif args.property_id:
        synthesize_property(db, args.property_id, validated_data)
    else:
        print("No property specified. Available properties:")
        rows = list_properties(db)
        if rows:
            print(f"\nUsage: python3 run_synthesis.py --property-id <ID>")
            print(f"       python3 run_synthesis.py --all")

    db.close()


if __name__ == '__main__':
    main()
