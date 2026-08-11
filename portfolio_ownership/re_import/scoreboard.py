#!/usr/bin/env python3
"""
#77 Re-import campaign scoreboard — one table across every pilot DB.

Discovers data/pilot_*.db, maps each to its master property (by the pilot's
property name against dim_property), reruns the tie-out scoring in-process
(read-only), and prints the campaign status: score, ops-closures, extraction
depth vs the master, and coverage.

    venv/Scripts/python portfolio_ownership/re_import/scoreboard.py
"""

import glob
import io
import os
import re
import sqlite3
import sys
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, HERE)

import tie_out_property as tp  # noqa: E402

# pilot db name fragment -> master property_key (extend as the campaign grows)
KNOWN = {
    'hadley': 'OSBORN-3007',
    'maplewood': 'ENGELS-2010',
    'northcourt': 'KAINC-1016',
    'midway_mkt': 'KAINC-1014',
    'normandale': 'KAINC-1015',
    'cottage_grove': 'OSBORN-3020',
    'crossing_meadows': 'CMX',
    'maplewood_ii': 'ENGELS-2011',
    'market_street': 'KAINC-1013',
}


def resolve_property(db_path):
    frag = re.sub(r'^pilot_|\.db$', '', os.path.basename(db_path))
    if frag in KNOWN:
        return KNOWN[frag]
    # fall back: match the pilot's property name against dim_property
    try:
        p = sqlite3.connect(f'file:{db_path.replace(os.sep, "/")}?mode=ro',
                            uri=True)
        name = p.execute("SELECT property_name FROM documents "
                         "WHERE property_name IS NOT NULL LIMIT 1").fetchone()
        m = sqlite3.connect(f'file:{tp.MASTER.replace(os.sep, "/")}?mode=ro',
                            uri=True)
        if name:
            row = m.execute(
                "SELECT property_key FROM dim_property WHERE "
                "LOWER(property_name) LIKE '%' || LOWER(?) || '%'",
                (name[0].split()[0],)).fetchone()
            return row[0] if row else None
    except sqlite3.Error:
        return None
    return None


def score(db_path, property_id):
    """Run the tie-out in-process, capture its output, parse the totals."""
    argv = sys.argv
    sys.argv = ['tie_out_property.py', '--db', db_path,
                '--property-id', property_id]
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            tp.main()
    except SystemExit:
        pass
    finally:
        sys.argv = argv
    out = buf.getvalue()
    g = lambda rx: (re.search(rx, out) or [None]) and re.search(rx, out)
    m_score = re.search(r'TIE-OUT: (\d+)/(\d+) \((\d+)%\)', out)
    m_ops = re.search(r'of which (\d+) field', out)
    return {
        'score': f'{m_score.group(1)}/{m_score.group(2)}' if m_score else '—',
        'pct': int(m_score.group(3)) if m_score else 0,
        'ops': int(m_ops.group(1)) if m_ops else 0,
        'output': out,
    }


def depth(db_path, property_id):
    p = sqlite3.connect(f'file:{db_path.replace(os.sep, "/")}?mode=ro', uri=True)
    docs = p.execute("SELECT count(*) FROM documents").fetchone()[0]
    clauses = p.execute("SELECT count(*) FROM clauses").fetchone()[0]
    m = sqlite3.connect(f'file:{tp.MASTER.replace(os.sep, "/")}?mode=ro', uri=True)
    mprov = m.execute("SELECT count(*) FROM lease_provision WHERE property_id=?",
                      (property_id,)).fetchone()[0]
    return docs, clauses, mprov


def main():
    dbs = sorted(glob.glob(os.path.join(ROOT, 'data', 'pilot_*.db')))
    if not dbs:
        sys.exit('no pilot DBs found under data/')
    print('=' * 78)
    print('RE-IMPORT CAMPAIGN SCOREBOARD — Capactive engine vs Riley masters')
    print('=' * 78)
    rows = []
    for db in dbs:
        pid = resolve_property(db)
        if not pid:
            print(f'  {os.path.basename(db)}: could not resolve master property — skipped')
            continue
        docs, clauses, mprov = depth(db, pid)
        s = score(db, pid)
        rows.append((pid, os.path.basename(db), docs, clauses, mprov,
                     s['score'], s['pct'], s['ops']))
    print(f"\n{'Property':<14} {'Pilot DB':<24} {'Docs':>4} {'Clauses':>8} "
          f"{'Master':>7} {'Score':>7} {'%':>4} {'Ops':>4}")
    print('-' * 78)
    for r in rows:
        print(f'{r[0]:<14} {r[1]:<24} {r[2]:>4} {r[3]:>8,} {r[4]:>7,} '
              f'{r[5]:>7} {r[6]:>3}% {r[7]:>4}')
    if rows:
        tot_c = sum(r[3] for r in rows)
        tot_m = sum(r[4] for r in rows)
        print('-' * 78)
        print(f'{len(rows)} properties validated · {tot_c:,} clauses extracted '
              f'vs {tot_m:,} master provisions · '
              f'lease layers remaining in master: '
              f'{22 - len(rows)} of 22')


if __name__ == '__main__':
    main()
