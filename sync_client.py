"""
Sync client — runs on an extraction device, pushes finalized documents
(and their source PDFs) to the org's web instance.

The push contract (docs/PACKAGING_DESIGN.md §4):
  1. GET  /api/sync/ping      — token + fingerprint handshake
  2. GET  /api/sync/manifest  — what the instance already holds from us
  3. POST /api/sync/run       — finalized documents + financial terms
                                (delta only: finalized_at newer than what
                                the manifest shows)
  4. POST /api/sync/pdf       — source PDFs, sha256-verified + deduped

Configuration (either flags or a [sync] section in capactive_sync.ini):
    url   = https://client.capactive.app
    token = cap_...                     (from Admin → Devices, shown once)

Usage on the extraction device:
    venv/Scripts/python sync_client.py --status          # ping + delta count
    venv/Scripts/python sync_client.py --push            # push docs + PDFs
    venv/Scripts/python sync_client.py --push --no-pdfs  # structured data only
"""

import argparse
import configparser
import hashlib
import json
import os
import platform
import sys
import uuid

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
INI = os.path.join(HERE, 'capactive_sync.ini')
BATCH = 25          # documents per /api/sync/run request
CHUNK = 4 * 1024 * 1024   # PDF upload chunk size (4 MB — survives flaky uplinks)
CHUNK_RETRIES = 8


def upload_pdf(s, url, origin_doc_id, path):
    """Chunked, resumable upload. Returns (status, detail):
    'uploaded' | 'deduped' | 'resumed' | 'error'."""
    import time
    with open(path, 'rb') as f:
        blob = f.read()
    sha = hashlib.sha256(blob).hexdigest()
    total = max(1, -(-len(blob) // CHUNK))          # ceil

    # what does the instance already have?
    try:
        st = s.get(f'{url}/api/sync/pdf/status', params={'sha256': sha},
                   timeout=60).json()
    except Exception as e:
        return 'error', f'status check failed: {e}'
    have = set(st.get('chunks', []))
    already_complete = st.get('complete', False)

    sent = 0
    if not already_complete:
        for i in range(total):
            if i in have:
                continue
            part = blob[i * CHUNK:(i + 1) * CHUNK]
            for attempt in range(1, CHUNK_RETRIES + 1):
                try:
                    r = s.post(f'{url}/api/sync/pdf/chunk',
                               params={'sha256': sha, 'index': i, 'total': total},
                               data=part,
                               headers={'Content-Type': 'application/octet-stream'},
                               timeout=120)
                    if r.status_code == 200:
                        sent += 1
                        break
                    last = f'HTTP {r.status_code}: {r.text[:100]}'
                except requests.exceptions.RequestException as e:
                    last = str(e)[:100]
                if attempt == CHUNK_RETRIES:
                    return 'error', f'chunk {i + 1}/{total} failed: {last}'
                time.sleep(min(2 ** attempt, 20))   # backoff: 2,4,8,16,20…

    try:
        r = s.post(f'{url}/api/sync/pdf/complete',
                   params={'sha256': sha, 'origin_doc_id': origin_doc_id,
                           'total': total}, timeout=300)
    except requests.exceptions.RequestException as e:
        return 'error', f'complete failed: {e}'
    if r.status_code != 200:
        return 'error', f'complete: HTTP {r.status_code}: {r.text[:120]}'
    body = r.json()
    if body.get('deduped') or already_complete:
        return 'deduped', f'{len(blob) // 1024} KB already on instance'
    if have:
        return 'resumed', f'{sent} of {total} chunks sent ({len(have)} were already there)'
    return 'uploaded', f'{total} chunk{"s" if total != 1 else ""}, {len(blob) // 1024} KB'


def fingerprint() -> str:
    """Stable, non-sensitive machine identity: hostname + MAC hash."""
    raw = f'{platform.node()}|{uuid.getnode()}'
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def load_config(args):
    url, token = args.url, args.token
    if (not url or not token) and os.path.exists(INI):
        cp = configparser.ConfigParser()
        cp.read(INI)
        url = url or cp.get('sync', 'url', fallback=None)
        token = token or cp.get('sync', 'token', fallback=None)
    if not url or not token:
        sys.exit('Missing instance URL or device token. Pass --url/--token '
                 'or create capactive_sync.ini with a [sync] section.')
    return url.rstrip('/'), token


def session_for(token):
    s = requests.Session()
    s.headers.update({'Authorization': f'Bearer {token}',
                      'X-Device-Fingerprint': fingerprint()})
    return s


def get_local_db():
    sys.path.insert(0, os.path.dirname(HERE))
    from realestate_extractor.database import Database
    # same default the webapp uses for the dev org; --db overrides
    return Database


def delta(local_docs, manifest):
    """Documents whose finalization is newer than the instance's copy."""
    out = []
    for d in local_docs:
        known = manifest.get(str(d['id']))
        if known is None or (known.get('finalized_at') or '') < d['finalized_at']:
            out.append(d)
    return out


def main():
    ap = argparse.ArgumentParser(description='Push finalized extraction '
                                             'results to the org instance.')
    ap.add_argument('--url', help='instance base URL')
    ap.add_argument('--token', help='device token (cap_...)')
    ap.add_argument('--db', default=os.path.join(HERE, 'data', 'org_dev.db'),
                    help='local extraction database')
    ap.add_argument('--status', action='store_true',
                    help='ping + show what would push, then exit')
    ap.add_argument('--push', action='store_true', help='push the delta')
    ap.add_argument('--no-pdfs', action='store_true',
                    help='skip source-PDF upload')
    ap.add_argument('--limit', type=int, default=None,
                    help='push at most N documents (testing / incremental '
                         'first push)')
    ap.add_argument('--ids', default=None,
                    help='push only these local document ids, comma-separated '
                         '(e.g. --ids 466,467) — for targeted tests')
    args = ap.parse_args()
    if not (args.status or args.push):
        ap.error('choose --status or --push')

    url, token = load_config(args)
    s = session_for(token)

    try:
        r = s.get(f'{url}/api/sync/ping', timeout=30)
    except requests.exceptions.ConnectionError:
        sys.exit(f'Cannot reach {url} — is the instance running and the '
                 f'URL correct?')
    except requests.exceptions.Timeout:
        sys.exit(f'{url} did not respond within 30s — check the '
                 f'connection and try again.')
    if r.status_code != 200:
        sys.exit(f'Handshake failed ({r.status_code}): {r.text[:200]}')
    ping = r.json()
    print(f"Connected as {ping['device_name']} ({ping['device_id']}) "
          f"→ org {ping['org_id']}")

    Database = get_local_db()
    db = Database(args.db)
    db.connect()
    finalized = db.list_finalized_documents()

    manifest = s.get(f'{url}/api/sync/manifest', timeout=60).json()['documents']
    to_push = delta(finalized, manifest)
    print(f'{len(finalized)} finalized locally, {len(manifest)} known to '
          f'instance, {len(to_push)} to push')
    if args.ids:
        want = {int(x) for x in args.ids.split(',') if x.strip()}
        to_push = [d for d in to_push if d['id'] in want]
        print(f'--ids: {len(to_push)} of the requested {len(want)} are in the delta')
    if args.status or not to_push:
        return
    if args.limit:
        to_push = to_push[:args.limit]
        print(f'--limit {args.limit}: pushing first {len(to_push)}')

    # ── push structured data in batches ──
    pushed = failed = 0
    for i in range(0, len(to_push), BATCH):
        batch = to_push[i:i + BATCH]
        items = []
        for d in batch:
            terms = [dict(t) for t in db.conn.execute(
                'SELECT * FROM financial_terms WHERE document_id = ?',
                (d['id'],))]
            clauses = [dict(t) for t in db.conn.execute(
                'SELECT * FROM clauses WHERE document_id = ?', (d['id'],))]
            doc = dict(d)
            doc['origin_doc_id'] = d['id']
            # the instance resolves properties by NAME (its ids differ);
            # the documents row often carries only property_id locally
            if d.get('property_id') and not d.get('property_name'):
                p = db.conn.execute(
                    'SELECT name, address FROM properties WHERE id = ?',
                    (d['property_id'],)).fetchone()
                if p:
                    doc['property_name'] = p['name']
                    doc['property_address'] = doc.get('property_address') or p['address']
            items.append({'document': doc, 'terms': terms,
                          'clauses': clauses})
        r = s.post(f'{url}/api/sync/run', json={'documents': items},
                   timeout=300)
        body = r.json() if r.headers.get('content-type', '').startswith(
            'application/json') else {}
        if r.status_code != 200:
            print(f'  batch {i // BATCH + 1}: HTTP {r.status_code} — '
                  f'{str(body or r.text)[:300]}')
        pushed += len(body.get('results', []))
        for e in body.get('errors', []):
            failed += 1
            print(f"  ERROR doc {e.get('origin_doc_id')}: {e.get('error')}")
        print(f'  batch {i // BATCH + 1}: {len(body.get("results", []))} ok')

    # ── push source PDFs (chunked + resumable) ──
    if not args.no_pdfs:
        tally = {'uploaded': 0, 'resumed': 0, 'deduped': 0, 'error': 0}
        missing = 0
        for d in to_push:
            fp = d.get('filepath') or ''
            if not fp or not os.path.exists(fp):
                missing += 1
                continue
            status, detail = upload_pdf(s, url, d['id'], fp)
            tally[status] += 1
            if status == 'error':
                print(f'  PDF ERROR doc {d["id"]} ({os.path.basename(fp)}): {detail}')
            elif status == 'resumed':
                print(f'  PDF resumed doc {d["id"]}: {detail}')
        print(f"PDFs: {tally['uploaded']} uploaded, {tally['resumed']} resumed, "
              f"{tally['deduped']} deduped, {tally['error']} failed, "
              f"{missing} missing locally")

    print(f'Done: {pushed} documents pushed, {failed} failed.')


if __name__ == '__main__':
    main()
