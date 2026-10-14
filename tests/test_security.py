"""
Sign-in throttling and session cookies, on the real app (no dev bypass)
with a throwaway config DB. Fictional org and people.

    venv/Scripts/python tests/test_security.py
"""

import glob
import io
import os
import shutil
import sys
import tempfile
import time

TMP = tempfile.mkdtemp(prefix='cap_security_')
os.environ['CAPACTIVE_DEV_MODE'] = '0'
os.environ['CAPACTIVE_DATA_DIR'] = TMP
os.environ['CAPACTIVE_CONFIG_DB'] = os.path.join(TMP, 'config.db')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from werkzeug.security import generate_password_hash  # noqa: E402
from realestate_extractor.config import ConfigStore  # noqa: E402
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


store = ConfigStore(os.environ['CAPACTIVE_CONFIG_DB'], TMP)
store.connect()
store.create_org('alder', 'Alder Test Holdings', generate_org_key('alder', 'standard'))
for uid, email in (('casey', 'casey@alder.test'), ('riley-t', 'robin@alder.test')):
    store.create_user('alder', uid, email, uid.title(), role='admin',
                      password_hash=generate_password_hash('right-pass-1'))
store.create_operator('ops@alder.test', 'Test Operator',
                      generate_password_hash('operator-pass-1'))
store.close()
perms = PermissionStore(os.environ['CAPACTIVE_CONFIG_DB'])
perms.connect()
for uid in ('casey', 'riley-t'):
    perms.init_user_permissions(uid, 'alder', role='admin')
perms.close()
app.config['TESTING'] = True


def sign_in(client, email, pw):
    r = client.post('/login', data={'email': email, 'password': pw})
    ok = r.status_code == 302 and '/login' not in r.headers.get('Location', '')
    return ok, r


print('\nSign-in throttle')
c = app.test_client()
for _ in range(5):
    sign_in(c, 'casey@alder.test', 'wrong-guess')
ok, r = sign_in(c, 'casey@alder.test', 'right-pass-1')
test('after 5 failures the account is locked, even with the right password', not ok)
page = c.get('/login').get_data(as_text=True)
test('the lock says when to try again', 'Too many failed sign-ins' in page)
ok, _ = sign_in(app.test_client(), 'robin@alder.test', 'right-pass-1')
test('another account is not affected', ok)
for _ in range(5):
    sign_in(c, 'nobody@alder.test', 'x')
test('unknown emails are throttled the same way',
     'Too many' in (lambda r: c.get('/login').get_data(as_text=True))(sign_in(c, 'nobody@alder.test', 'x')))
webapp._login_failures.clear()
ok, _ = sign_in(c, 'casey@alder.test', 'right-pass-1')
test('once the window passes, the right password works', ok)
webapp._login_failures.clear()
for _ in range(4):
    sign_in(app.test_client(), 'robin@alder.test', 'wrong-guess')
sign_in(app.test_client(), 'robin@alder.test', 'right-pass-1')
for _ in range(4):
    sign_in(app.test_client(), 'robin@alder.test', 'wrong-guess')
ok, _ = sign_in(app.test_client(), 'robin@alder.test', 'right-pass-1')
test('a successful sign-in resets the count', ok)

webapp._login_failures.clear()
old = webapp._LOGIN_MAX_PER_IP
webapp._LOGIN_MAX_PER_IP = 6
for i in range(6):
    sign_in(app.test_client(), f'spray{i}@alder.test', 'x')
ok, _ = sign_in(app.test_client(), 'casey@alder.test', 'right-pass-1')
test('many accounts tried from one address locks that address', not ok)
webapp._LOGIN_MAX_PER_IP = old
webapp._login_failures.clear()

print('\nOperator sign-in throttle')
op = app.test_client()
for _ in range(5):
    op.post('/operator/login', data={'email': 'ops@alder.test', 'password': 'nope'})
r = op.post('/operator/login', data={'email': 'ops@alder.test', 'password': 'operator-pass-1'})
test('operator sign-in locks after 5 failures', r.status_code == 200
     and 'Too many failed attempts' in r.get_data(as_text=True))
webapp._login_failures.clear()

print('\nSession cookie')
h = app.test_client()
_, r = sign_in(h, 'casey@alder.test', 'right-pass-1')
cookie = r.headers.get('Set-Cookie', '')
test('HttpOnly', 'HttpOnly' in cookie)
test('SameSite=Lax', 'SameSite=Lax' in cookie)
test('not marked Secure over plain http (localhost, SSH tunnel)', 'Secure' not in cookie)
test('expires (idle timeout) instead of living for the browser session', 'Expires=' in cookie)
s = app.test_client()
r = s.post('/login', base_url='https://demo.example.test',
           data={'email': 'casey@alder.test', 'password': 'right-pass-1'})
test('marked Secure when the request came over https', 'Secure' in r.headers.get('Set-Cookie', ''))
test('the idle timeout is 8 hours', app.permanent_session_lifetime.total_seconds() == 8 * 3600)

print('\nIsolation between orgs on one instance')
store = ConfigStore(os.environ['CAPACTIVE_CONFIG_DB'], TMP)
store.connect()
store.create_org('birch', 'Birch Test Partners', generate_org_key('birch', 'standard'))
store.create_user('birch', 'blake', 'blake@birch.test', 'Blake', role='admin',
                  password_hash=generate_password_hash('right-pass-1'))
dev_a = store.register_device('alder', 'Alder desk')['device_id']
store.close()
perms = PermissionStore(os.environ['CAPACTIVE_CONFIG_DB'])
perms.connect()
perms.init_user_permissions('blake', 'birch', role='admin')
perms.close()
a = app.test_client()
b = app.test_client()
sign_in(a, 'casey@alder.test', 'right-pass-1')
sign_in(b, 'blake@birch.test', 'right-pass-1')

b.post('/admin/users', data={'action': 'deactivate', 'user_id': 'riley-t'})
ok, _ = sign_in(app.test_client(), 'robin@alder.test', 'right-pass-1')
test("another org's admin can't deactivate our user", ok)
b.post('/admin/users', data={'action': 'invite', 'user_id': 'riley-t'})
store = ConfigStore(os.environ['CAPACTIVE_CONFIG_DB'], TMP)
store.connect()
test("...or issue a set-password link for them", not store.pending_invite_users('alder'))
b.post('/admin/devices', data={'action': 'revoke', 'device_id': dev_a})
test("another org's admin can't revoke our device",
     any(d['device_id'] == dev_a and d['is_active'] for d in store.list_devices('alder')))
store.close()

before = set(glob.glob(os.path.join(webapp.UPLOAD_FOLDER, 'up_*')))
for client, text in ((a, b'alder lease text'), (b, b'birch lease text')):
    client.post('/properties/create', data={'name': 'Test Plaza', 'property_type': 'retail'})
    client.post('/upload', data={'files': (io.BytesIO(text), 'lease.txt'), 'property_id': '1'},
                content_type='multipart/form-data')
new_dirs = sorted(set(glob.glob(os.path.join(webapp.UPLOAD_FOLDER, 'up_*'))) - before)
contents = sorted(open(os.path.join(d, 'lease.txt'), 'rb').read() for d in new_dirs
                  if os.path.exists(os.path.join(d, 'lease.txt')))
test('same-named uploads from two orgs are kept apart (no overwrite)',
     contents == [b'alder lease text', b'birch lease text'])
for d in new_dirs:   # processing may still hold the file briefly (Windows)
    for _ in range(20):
        shutil.rmtree(d, ignore_errors=True)
        if not os.path.exists(d):
            break
        time.sleep(0.25)

test('the deal registry is closed to an org with no deal module',
     b.get('/api/registry/deals').status_code == 403)
test('...and the warehouse API', b.get('/warehouse/').status_code == 403)
test('Deliverables is not on by default for a new org', b.get('/deliverables/').status_code == 403)

store = ConfigStore(os.environ['CAPACTIVE_CONFIG_DB'], TMP)
store.connect()
for org in ('alder', 'birch'):
    f = store.get_org_features(org)
    f.modules_enabled = list(f.modules_enabled or []) + ['portfolio_cashflow', 'deliverables']
    store.set_org_features(org, f)
store.close()

from realestate_extractor.modules.portfolio_cashflow import routes as pcf  # noqa: E402
wb = os.path.join(TMP, 'alder_model.xlsx')
with open(wb, 'wb') as fh:
    fh.write(b'alder workbook')
pcf._LATEST['alder'] = {'xlsx': wb, 'summary': {'note': 'alder summary'}}
pcf._JOBS['alderjob'] = {'id': 'alderjob', 'org_id': 'alder', 'status': 'done'}
test("another org can't download our cash flow workbook",
     b.get('/portfolio-cashflow/api/download').status_code == 404)
test('...but we can', a.get('/portfolio-cashflow/api/download').status_code == 200)
test("another org can't read our cash flow job",
     b.get('/portfolio-cashflow/api/status/alderjob').status_code == 404)

from realestate_extractor.modules.deliverables import routes as dl  # noqa: E402
os.makedirs(os.path.join(dl.OUTPUT_DIR, 'alder'), exist_ok=True)
doc = os.path.join(dl.OUTPUT_DIR, 'alder', 'Zz_Isolation_Test.docx')
with open(doc, 'wb') as fh:
    fh.write(b'alder deliverable')
try:
    test("another org doesn't see our deliverables",
         'Zz_Isolation_Test.docx' not in b.get('/deliverables/').get_data(as_text=True))
    test('...or download them',
         b.get('/deliverables/download/Zz_Isolation_Test.docx').status_code == 404)
    test('...but we can', a.get('/deliverables/download/Zz_Isolation_Test.docx').status_code == 200)
finally:
    os.remove(doc)
    for org in ('alder', 'birch'):
        try:
            os.rmdir(os.path.join(dl.OUTPUT_DIR, org))
        except OSError:
            pass

print(f'\n{passed} passed, {failed} failed')
sys.exit(1 if failed else 0)
