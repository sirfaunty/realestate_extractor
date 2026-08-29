"""
Re-link document source files after a machine migration.

Absolute filepaths stored in the extraction DB go stale when the
database moves between machines (Mac → Windows, laptop → desktop).
The files usually still exist under the local uploads/ tree with the
same relative structure — this tool finds them and repairs filepath.

Strategy per broken document:
  1. Prefix swap: take the path segment after ".../uploads/" and try it
     under the local uploads root.
  2. Filename search: unique exact-filename match anywhere under the
     uploads root (skipped if the name appears more than once — never
     guess between candidates).

Dry-run by default; nothing is written without --apply.

    venv/Scripts/python relink_documents.py                 # report only
    venv/Scripts/python relink_documents.py --apply         # fix
    venv/Scripts/python relink_documents.py --db data/org_dev.db --uploads uploads
"""

import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from realestate_extractor.database import Database  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def index_uploads(root):
    """filename -> list of absolute paths under the uploads root."""
    idx = {}
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            idx.setdefault(f, []).append(os.path.join(dirpath, f))
    return idx


def candidate(fp, root, name_index):
    """Best local path for a stale filepath, or (None, reason)."""
    norm = (fp or '').replace('\\', '/')
    if '/uploads/' in norm:
        rel = norm.split('/uploads/', 1)[1]
        p = os.path.join(root, *rel.split('/'))
        if os.path.exists(p):
            return p, 'prefix'
    base = os.path.basename(norm)
    hits = name_index.get(base, [])
    if len(hits) == 1:
        return hits[0], 'filename'
    if len(hits) > 1:
        return None, f'ambiguous ({len(hits)} same-named files)'
    return None, 'not found'


def main():
    ap = argparse.ArgumentParser(description='Repair stale document '
                                             'filepaths after migration.')
    ap.add_argument('--db', default=os.path.join(HERE, 'data', 'org_dev.db'))
    ap.add_argument('--uploads', default=os.path.join(HERE, 'uploads'))
    ap.add_argument('--apply', action='store_true',
                    help='write fixes (default: dry-run report)')
    args = ap.parse_args()

    db = Database(args.db)
    db.connect()
    rows = db.conn.execute(
        "SELECT id, filename, filepath FROM documents "
        "WHERE origin_device_id IS NULL").fetchall()

    broken = [r for r in rows
              if not (r['filepath'] and os.path.exists(r['filepath']))]
    print(f'{len(rows)} local documents, {len(broken)} with stale filepaths')
    if not broken:
        return

    name_index = index_uploads(args.uploads)
    fixes, fails = [], Counter()
    for r in broken:
        path, how = candidate(r['filepath'], args.uploads, name_index)
        if path:
            fixes.append((r['id'], path, how))
        else:
            fails[how] += 1
            print(f'  UNRESOLVED doc {r["id"]}: {r["filename"]} ({how})')

    print(f'\nresolvable: {len(fixes)} '
          f'({Counter(h for _, _, h in fixes)})  unresolved: {sum(fails.values())}')

    if not args.apply:
        print('\nDry run — re-run with --apply to write these fixes.')
        return

    for doc_id, path, _how in fixes:
        db.conn.execute("UPDATE documents SET filepath = ? WHERE id = ?",
                        (path, doc_id))
    db.conn.commit()
    print(f'applied {len(fixes)} filepath fixes.')


if __name__ == '__main__':
    main()
