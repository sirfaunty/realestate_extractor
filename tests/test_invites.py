"""
Invite links: an admin adds a teammate, the teammate sets their own
password from a one-time link, then signs in. Runs the real app (no dev
bypass) against a throwaway config DB. Fictional org and people.

    venv/Scripts/python tests/test_invites.py
"""

import os
import re
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix='cap_invites_')
os.environ['CAPACTIVE_DEV_MODE'] = '0'
os.environ['CAPACTIVE_DATA_DIR'] = TMP
os.environ['CAPACTIVE_CONFIG_DB'] = os.path.join(TMP, 'config.db')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from werkzeug.security import generate_password_hash  # noqa: E402
from realestate_extractor.config import ConfigStore  # noqa: E402
from realestate_extractor.licensing import generate_org_key  # noqa: E402
from realestate_extractor.permissions import PermissionStore  # noqa: E402
from realestate_extractor.webapp import app  # noqa: E402

passed = failed = 0


def test(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f'  ok   {name}')
    else:
        failed += 1
        print(f'  FAIL {name}')


ORG = 'harbor-test'
store = ConfigStore(os.environ['CAPACTIVE_CONFIG_DB'], TMP)
store.connect()
store.create_org(ORG, 'Harbor Test Holdings', generate_org_key(ORG, 'standard'))
store.create_user(ORG, 'dana', 'dana@harbor.test', 'Dana Reyes', role='admin',
                  password_hash=generate_password_hash('admin-pass-1'))
store.create_operator('ops@harbor.test', 'Test Operator',
                      generate_password_hash('operator-pass-1'))
store.close()
perms = PermissionStore(os.environ['CAPACTIVE_CONFIG_DB'])
perms.connect()
perms.init_user_permissions('dana', ORG, role='admin')
perms.close()

app.config['TESTING'] = True
admin = app.test_client()
admin.post('/login', data={'email': 'dana@harbor.test', 'password': 'admin-pass-1'})


def invite_url(page):
    m = re.search(r'value="(http[^"]*/invite/[^"]+)"', page)
    return m.group(1).split('localhost', 1)[1] if m else None


print('\nAdding a teammate')
r = admin.post('/admin/users', data={'action': 'add', 'display_name': 'Sam Patel',
                                     'email': 'Sam@Harbor.test', 'role': 'analyst'},
               follow_redirects=True)
page = r.get_data(as_text=True)
link = invite_url(page)
test('the Users page shows a one-time invite link', link is not None)
test('the user-key column is gone', 'User Key' not in page and 'CU-' not in page)
test('the new user shows as Invited', 'Invited' in page)
again = admin.get('/admin/users').get_data(as_text=True)
test('the link is shown only once', invite_url(again) is None)

print('\nBefore the link is used')
sam = app.test_client()
r = sam.post('/login', data={'email': 'sam@harbor.test', 'password': 'anything'},
             follow_redirects=True)
test('login points to the invite link', 'invite link' in r.get_data(as_text=True))

print('\nSetting the password')
r = sam.get(link)
test('the link opens the set-password page',
     r.status_code == 200 and 'Set your password' in r.get_data(as_text=True))
r = sam.post(link, data={'password': 'sam-pass-123', 'password2': 'different'},
             follow_redirects=True)
test('mismatched passwords are refused', 'match.' in r.get_data(as_text=True))
r = sam.post(link, data={'password': 'short', 'password2': 'short'},
             follow_redirects=True)
test('short passwords are refused', 'at least 8' in r.get_data(as_text=True))
r = sam.post(link, data={'password': 'sam-pass-123', 'password2': 'sam-pass-123'})
test('a valid password redirects to sign in',
     r.status_code == 302 and '/login' in r.headers['Location'])
test('the link works only once', sam.get(link).status_code == 410)
r = sam.post('/login', data={'email': 'sam@harbor.test', 'password': 'sam-pass-123'})
test('the teammate can sign in (email typed in mixed case at invite)',
     r.status_code == 302 and '/login' not in r.headers['Location'])

print('\nNew link (forgotten password)')
page = admin.post('/admin/users', data={'action': 'invite', 'user_id': 'sam'},
                  follow_redirects=True).get_data(as_text=True)
first = invite_url(page)
page = admin.post('/admin/users', data={'action': 'invite', 'user_id': 'sam'},
                  follow_redirects=True).get_data(as_text=True)
second = invite_url(page)
test('a new link is issued', first and second and first != second)
test('an earlier unused link stops working', app.test_client().get(first).status_code == 410)
fresh = app.test_client()
r = fresh.post('/login', data={'email': 'sam@harbor.test', 'password': 'sam-pass-123'})
test('the old password keeps working until the new link is used',
     r.status_code == 302 and '/login' not in r.headers['Location'])

print('\nExpired and bogus links')
store = ConfigStore(os.environ['CAPACTIVE_CONFIG_DB'], TMP)
store.connect()
store.conn.execute("UPDATE user_invites SET expires_at = '2000-01-01' WHERE used_at IS NULL")
store.conn.commit()
store.close()
test('an expired link is refused', app.test_client().get(second).status_code == 410)
test('a made-up link is refused', app.test_client().get('/invite/nope').status_code == 410)

print('\nOperator provisioning without a password')
op = app.test_client()
op.post('/operator/login', data={'email': 'ops@harbor.test', 'password': 'operator-pass-1'})
r = op.post('/operator/provision', data={
    'org_name': 'Lakeview Test Partners', 'plan': 'standard',
    'admin_name': 'Lee Morgan', 'admin_email': 'lee@lakeview.test'}, follow_redirects=True)
page = r.get_data(as_text=True)
m = re.search(r'(http\S*/invite/[\w-]+)', page)
test('provisioning shows a welcome message with an invite link', 'Welcome message' in page and m)
lee = app.test_client()
path = m.group(1).split('localhost', 1)[1] if m else '/invite/x'
lee.post(path, data={'password': 'lee-pass-123', 'password2': 'lee-pass-123'})
r = lee.post('/login', data={'email': 'lee@lakeview.test', 'password': 'lee-pass-123'})
test('the new admin sets a password and signs in',
     r.status_code == 302 and '/login' not in r.headers['Location'])

print(f'\n{passed} passed, {failed} failed')
sys.exit(1 if failed else 0)
