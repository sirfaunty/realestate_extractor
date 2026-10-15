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


def main():
    # everything runs here, not at import: the upload test starts a 'spawn'
    # worker process, which re-imports this file and must not re-run the tests
    global passed, failed
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

    print('\nSessions end when access is taken away')
    webapp._login_failures.clear()


    def set_sql(sql, *args):
        st = ConfigStore(os.environ['CAPACTIVE_CONFIG_DB'], TMP)
        st.connect()
        st.conn.execute(sql, args)
        st.conn.commit()
        st.close()


    def signed_in(client, path='/'):
        r = client.get(path)
        return r.status_code == 200, r


    store = ConfigStore(os.environ['CAPACTIVE_CONFIG_DB'], TMP)
    store.connect()
    store.create_user('birch', 'drew', 'drew@birch.test', 'Drew', role='admin',
                      password_hash=generate_password_hash('right-pass-1'))
    store.close()
    perms = PermissionStore(os.environ['CAPACTIVE_CONFIG_DB'])
    perms.connect()
    perms.init_user_permissions('drew', 'birch', role='admin')
    perms.close()
    drew = app.test_client()
    sign_in(drew, 'drew@birch.test', 'right-pass-1')
    test('a signed-in user has access', signed_in(drew)[0])
    b.post('/admin/users', data={'action': 'deactivate', 'user_id': 'drew'})
    ok, r = signed_in(drew)
    test('deactivating them ends their open session at once', not ok
         and '/login' in r.headers.get('Location', ''))
    test('...with a reason on the sign-in page',
         'no longer active' in drew.get('/login').get_data(as_text=True))

    set_sql("UPDATE users SET is_active = 1 WHERE user_id = 'drew'")
    sign_in(drew, 'drew@birch.test', 'right-pass-1')
    set_sql("UPDATE organizations SET is_active = 0 WHERE org_id = 'birch'")
    test('deactivating the organization ends its sessions', not signed_in(drew)[0])
    set_sql("UPDATE organizations SET is_active = 1 WHERE org_id = 'birch'")

    sign_in(drew, 'drew@birch.test', 'right-pass-1')
    other = app.test_client()
    sign_in(other, 'drew@birch.test', 'right-pass-1')
    page = b.post('/admin/users', data={'action': 'invite', 'user_id': 'drew'},
                  follow_redirects=True).get_data(as_text=True)
    import re  # noqa: E402
    link = re.search(r'value="http[^"]*?(/invite/[^"]+)"', page).group(1)
    test('a new link alone does not end sessions (old password still good)', signed_in(drew)[0])
    app.test_client().post(link, data={'password': 'new-pass-123', 'password2': 'new-pass-123'})
    test('setting a new password ends every earlier session', not signed_in(drew)[0]
         and not signed_in(other)[0])
    ok, _ = sign_in(drew, 'drew@birch.test', 'new-pass-123')
    test('...and the new password signs in', ok and signed_in(drew)[0])

    test('an admin can open the Users page', signed_in(drew, '/admin/users')[0])
    set_sql("UPDATE users SET role = 'viewer' WHERE user_id = 'drew'")
    test('a role change applies on the next request', not signed_in(drew, '/admin/users')[0])
    set_sql("UPDATE users SET role = 'admin' WHERE user_id = 'drew'")

    print('\nAudit trail')
    for _ in range(5):
        sign_in(app.test_client(), 'drew@birch.test', 'wrong-guess')
    sign_in(app.test_client(), 'drew@birch.test', 'new-pass-123')
    webapp._login_failures.clear()
    sign_in(drew, 'drew@birch.test', 'new-pass-123')
    page = drew.get('/admin/audit').get_data(as_text=True)
    for action, label in (('sign in failed', 'failed sign-ins'),
                          ('sign in blocked', 'the lockout'),
                          ('user deactivated', 'deactivations'),
                          ('session ended', 'ended sessions'),
                          ('invite issued', 'new invite links'),
                          ('password set', 'passwords set from a link')):
        test(f'the Audit Trail shows {label}', action in page)
    test('entries say who did it', '· by blake' in page)
    test("another org's audit trail stays out",
         'drew' not in a.get('/admin/audit').get_data(as_text=True))

    print('\nOperator: modules that read instance data are flagged')
    webapp._login_failures.clear()
    opc = app.test_client()
    opc.post('/operator/login', data={'email': 'ops@alder.test', 'password': 'operator-pass-1'})
    page = opc.get('/operator/enter/alder?to=modules', follow_redirects=True).get_data(as_text=True)
    test('the Modules page flags instance-data modules', 'instance data' in page)
    test('...and warns that this instance hosts other organizations',
         'hosts other organizations' in page)
    set_sql("UPDATE users SET password_hash = 'x', session_epoch = 5 WHERE user_id = 'casey'")
    test("an operator inside a workspace isn't signed out by the user's password version",
         opc.get('/').status_code == 200)

    print(f'\n{passed} passed, {failed} failed')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
