"""
Seat classes and the finalize flag, on the real app (no dev bypass) with
a throwaway config DB. Fictional org and people.

- the add-user form offers five roles, each labelled with its seat class
- a starter org (1 extraction seat, 2 access seats, first admin free)
  blocks the user that would exceed either class, at Admin -> Users and at
  Admin -> Permissions (role changes), with a clear flash message
- approving a document in the Review Queue sets finalized_at (the sync
  contract); skipping or resetting it clears the flag again

    venv/Scripts/python tests/test_seats.py
"""

import os
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix='cap_seats_')
os.environ['CAPACTIVE_DEV_MODE'] = '0'
os.environ['CAPACTIVE_DATA_DIR'] = TMP
os.environ['CAPACTIVE_CONFIG_DB'] = os.path.join(TMP, 'config.db')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from werkzeug.security import generate_password_hash  # noqa: E402
from realestate_extractor.config import ConfigStore  # noqa: E402
from realestate_extractor.database import Database  # noqa: E402
from realestate_extractor.licensing import generate_org_key  # noqa: E402
from realestate_extractor.permissions import PermissionStore  # noqa: E402
from realestate_extractor import webapp  # noqa: E402

app = webapp.app
passed = failed = 0


def test(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f'  ok   {name}')
    else:
        failed += 1
        print(f'  FAIL {name}')


ORG = 'cedar-test'
store = ConfigStore(os.environ['CAPACTIVE_CONFIG_DB'], TMP)
store.connect()
store.create_org(ORG, 'Cedar Test Partners', generate_org_key(ORG, 'starter'),
                 plan='starter')
store.create_user(ORG, 'dana', 'dana@cedar.test', 'Dana Reyes', role='admin',
                  password_hash=generate_password_hash('admin-pass-1'))
ORG_DB = store.get_org_db_path(ORG)
store.close()
perms = PermissionStore(os.environ['CAPACTIVE_CONFIG_DB'])
perms.connect()
perms.init_user_permissions('dana', ORG, role='admin')
perms.close()

app.config['TESTING'] = True
admin = app.test_client()
admin.post('/login', data={'email': 'dana@cedar.test', 'password': 'admin-pass-1'})


def users():
    s = ConfigStore(os.environ['CAPACTIVE_CONFIG_DB'], TMP)
    s.connect()
    try:
        return sorted(u['user_id'] for u in s.list_users(ORG))
    finally:
        s.close()


def role_of(user_id):
    p = PermissionStore(os.environ['CAPACTIVE_CONFIG_DB'])
    p.connect()
    try:
        row = p.conn.execute(
            "SELECT role_template FROM user_permissions WHERE user_id = ? AND org_id = ?",
            (user_id, ORG)).fetchone()
        return row['role_template'] if row else None
    finally:
        p.close()


def add(name, email, role):
    r = admin.post('/admin/users', data={'action': 'add', 'display_name': name,
                                         'email': email, 'role': role},
                   follow_redirects=True)
    return r.get_data(as_text=True)


def set_role(user_id, role):
    r = admin.post('/admin/permissions',
                   data={'action': 'set_role', 'user_id': user_id, 'role_template': role},
                   follow_redirects=True)
    return r.get_data(as_text=True)


def main():
    print('\nAdd-user form')
    page = admin.get('/admin/users').get_data(as_text=True)
    for label in ('Viewer — access seat', 'Analyst — access seat',
                  'Extractor — extraction seat', 'Operator — extraction seat',
                  'Admin — extraction seat (first admin free)'):
        test(f'offers "{label}"', label in page)
    test('exactly five role options', page.count('<option value="') >= 5
         and all(f'<option value="{r}"' in page
                 for r in ('viewer', 'analyst', 'extractor', 'operator', 'admin')))

    print('\nSeat enforcement on a starter org (1 extraction, 2 access, first admin free)')
    page = add('Sam Patel', 'sam@cedar.test', 'viewer')
    test('first access seat: viewer added', 'sam' in users() and 'Invited' in page)
    page = add('Lee Okafor', 'lee@cedar.test', 'analyst')
    test('second access seat: analyst added', 'lee' in users())
    before = users()
    page = add('Kim Novak', 'kim@cedar.test', 'viewer')
    test('third access user is blocked with a clear message',
         'No access seats left (2/2 in use)' in page)
    test('...and not created', users() == before)

    page = add('Ira Lund', 'ira@cedar.test', 'extractor')
    test('the one extraction seat: extractor added', 'ira' in users())
    before = users()
    page = add('Noa Vega', 'noa@cedar.test', 'operator')
    test('a second extraction-seat user is blocked',
         'No extraction seats left (1/1 in use)' in page and users() == before)
    page = add('Bo Lind', 'bo@cedar.test', 'admin')
    test('a second admin counts as an extraction seat and is blocked too',
         'No extraction seats left' in page and users() == before)

    print('\nRole changes at Admin -> Permissions')
    page = set_role('sam', 'extractor')
    test('promoting a viewer to extractor with extraction seats full is blocked',
         'No extraction seats left' in page)
    test('...and the role is unchanged', role_of('sam') == 'viewer')
    page = set_role('ira', 'admin')
    test("moving the extractor to admin reuses the seat they free (allowed)",
         'Role updated to admin' in page and role_of('ira') == 'admin')
    page = set_role('ira', 'viewer')
    test('demoting them to an access seat when access seats are full is blocked',
         'No access seats left' in page and role_of('ira') == 'admin')
    page = set_role('sam', 'analyst')
    test('viewer -> analyst stays within the access class (allowed)',
         role_of('sam') == 'analyst')

    print('\nFinalize flow (Review Queue approval = sync-eligible)')
    webapp._auto_analyze_new_docs = lambda *a, **k: 0   # no extraction job in a unit test
    db = Database(ORG_DB)
    db.connect()
    pid = db.create_property('Cedar Point Plaza', property_type='retail')
    doc_id = db.insert_document('cedar-lease.pdf', os.path.join(TMP, 'cedar-lease.pdf'),
                                'lease', auto_create_property=False)

    def row():
        return db.conn.execute(
            "SELECT review_status, finalized_at FROM documents WHERE id = ?",
            (doc_id,)).fetchone()

    test('a new document is pending review and not finalized',
         row()['review_status'] == 'pending_review' and row()['finalized_at'] is None)
    test('...and not in the sync contract',
         doc_id not in [d['id'] for d in db.list_finalized_documents()])

    r = admin.post(f'/review/{doc_id}/approve', data={'property_id': pid},
                   follow_redirects=True)
    test('approving in the Review Queue links the property', 'linked to Cedar Point Plaza'
         in r.get_data(as_text=True))
    test('...sets finalized_at', row()['review_status'] == 'approved'
         and row()['finalized_at'] is not None)
    test('...and the document enters the sync contract',
         doc_id in [d['id'] for d in db.list_finalized_documents()])

    admin.post(f'/review/{doc_id}/skip', follow_redirects=True)
    test('skipping clears finalized_at', row()['review_status'] == 'skipped'
         and row()['finalized_at'] is None)
    test('...and leaves the sync contract',
         doc_id not in [d['id'] for d in db.list_finalized_documents()])

    admin.post(f'/review/{doc_id}/approve', data={'property_id': pid}, follow_redirects=True)
    db.reset_document_review(doc_id)
    test('resetting a document back to the queue clears finalized_at',
         row()['review_status'] == 'pending_review' and row()['finalized_at'] is None)
    db.close()

    print(f'\n{passed} passed, {failed} failed')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
