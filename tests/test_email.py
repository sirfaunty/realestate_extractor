"""
Outgoing email against a fake local mail server: emailed invites, the
"Forgot password?" flow (reset link, one hour, no account enumeration,
throttled), password-changed notices, and the copy-link fallback when the
mail server is down. Runs the real app (no dev bypass) against a throwaway
config DB. Fictional org and people.

    venv/Scripts/python tests/test_email.py
"""

import os
import re
import socketserver
import sys
import tempfile
import threading
from email import message_from_bytes, policy


class _Box:
    messages = []   # (rcpt, EmailMessage)


class _SMTPHandler(socketserver.StreamRequestHandler):
    """Just enough SMTP for smtplib: EHLO, AUTH PLAIN, MAIL, RCPT, DATA."""

    def _say(self, line):
        self.wfile.write((line + '\r\n').encode())

    def handle(self):
        self._say('220 fake.test ESMTP')
        rcpt = []
        while True:
            line = self.rfile.readline().decode(errors='replace').strip()
            if not line:
                return
            cmd = line.split(' ', 1)[0].upper()
            if cmd == 'EHLO':
                self._say('250-fake.test')
                self._say('250 AUTH PLAIN LOGIN')
            elif cmd == 'AUTH':
                self._say('235 ok')
            elif cmd == 'MAIL':
                rcpt = []
                self._say('250 ok')
            elif cmd == 'RCPT':
                rcpt.append(line.split(':', 1)[1].strip(' <>'))
                self._say('250 ok')
            elif cmd == 'DATA':
                self._say('354 go')
                data = b''
                while True:
                    chunk = self.rfile.readline()
                    if chunk in (b'.\r\n', b'.\n', b''):
                        break
                    data += chunk[1:] if chunk.startswith(b'..') else chunk
                msg = message_from_bytes(data, policy=policy.default)
                for r in rcpt:
                    _Box.messages.append((r, msg))
                self._say('250 queued')
            elif cmd == 'QUIT':
                self._say('221 bye')
                return
            else:
                self._say('250 ok')


def main():
    server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), _SMTPHandler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()

    TMP = tempfile.mkdtemp(prefix='cap_email_')
    os.environ.update({
        'CAPACTIVE_DEV_MODE': '0', 'CAPACTIVE_DATA_DIR': TMP,
        'CAPACTIVE_CONFIG_DB': os.path.join(TMP, 'config.db'),
        'CAPACTIVE_SMTP_HOST': '127.0.0.1',
        'CAPACTIVE_SMTP_PORT': str(server.server_address[1]),
        'CAPACTIVE_SMTP_USER': 'test-token', 'CAPACTIVE_SMTP_PASSWORD': 'test-token',
        'CAPACTIVE_SMTP_TLS': '0',
        'CAPACTIVE_MAIL_FROM': 'Capactive <no-reply@capactive.test>',
        'CAPACTIVE_MAIL_REPLY_TO': 'support@capactive.test',
    })
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))

    from werkzeug.security import generate_password_hash
    from realestate_extractor.config import ConfigStore
    from realestate_extractor.licensing import generate_org_key
    from realestate_extractor.permissions import PermissionStore
    from realestate_extractor.webapp import app

    passed = failed = 0

    def test(name, cond):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f'  ok   {name}')
        else:
            failed += 1
            print(f'  FAIL {name}')

    def mail_to(addr):
        return [m for r, m in _Box.messages if r == addr]

    def link_in(msg):
        m = re.search(r'https?://\S+/invite/\S+', msg.get_content())
        return m.group(0).split('localhost', 1)[1] if m else None

    ORG = 'cedar-test'
    store = ConfigStore(os.environ['CAPACTIVE_CONFIG_DB'], TMP)
    store.connect()
    store.create_org(ORG, 'Cedar Test Partners', generate_org_key(ORG, 'standard'))
    store.create_user(ORG, 'avery', 'avery@cedar.test', 'Avery Quinn', role='admin',
                      password_hash=generate_password_hash('admin-pass-1'))
    store.create_operator('ops@cedar.test', 'Test Operator',
                          generate_password_hash('operator-pass-1'))
    store.close()
    perms = PermissionStore(os.environ['CAPACTIVE_CONFIG_DB'])
    perms.connect()
    perms.init_user_permissions('avery', ORG, role='admin')
    perms.close()

    app.config['TESTING'] = True
    admin = app.test_client()
    admin.post('/login', data={'email': 'avery@cedar.test', 'password': 'admin-pass-1'})

    print('\nEmailed invites')
    page = admin.post('/admin/users', data={'action': 'add', 'display_name': 'Morgan Lake',
                                            'email': 'morgan@cedar.test', 'role': 'analyst'},
                      follow_redirects=True).get_data(as_text=True)
    msgs = mail_to('morgan@cedar.test')
    test('adding a teammate emails them the invite', len(msgs) == 1)
    msg = msgs[0] if msgs else None
    test('the email has From, Reply-To and a clear subject', msg is not None
         and 'no-reply@capactive.test' in msg['From']
         and msg['Reply-To'] == 'support@capactive.test'
         and 'Set your Capactive password' in msg['Subject'])
    test('it names the workspace and the sign-in email', msg is not None
         and 'Cedar Test Partners' in msg.get_content()
         and 'morgan@cedar.test' in msg.get_content())
    test('the Users page says it was emailed, link kept as a backup',
         'Invite emailed to Morgan Lake' in page and '/invite/' in page)
    link = link_in(msg) if msg else None
    morgan = app.test_client()
    r = morgan.post(link, data={'password': 'morgan-pass-1', 'password2': 'morgan-pass-1'})
    test('the emailed link sets the password', r.status_code == 302)
    test('a first password sends no "changed" notice', len(mail_to('morgan@cedar.test')) == 1)

    print('\nForgot password')
    page = app.test_client().get('/login').get_data(as_text=True)
    test('sign-in offers "Forgot password?" when email is on', 'Forgot password?' in page)
    _Box.messages.clear()
    anon = app.test_client()
    r1 = anon.post('/forgot', data={'email': 'Morgan@Cedar.test'}).get_data(as_text=True)
    r2 = anon.post('/forgot', data={'email': 'nobody@cedar.test'}).get_data(as_text=True)
    test('the same answer for a real and an unknown address',
         'a reset link is on its way' in r1 and r1 == r2)
    msgs = mail_to('morgan@cedar.test')
    test('only the real account gets an email', len(msgs) == 1 and len(_Box.messages) == 1)
    reset = link_in(msgs[0]) if msgs else None
    test('the reset email says the link lasts an hour',
         msgs and '1 hour)' in msgs[0].get_content())
    st = ConfigStore(os.environ['CAPACTIVE_CONFIG_DB'], TMP)
    st.connect()
    inv = st.get_invite(reset.rsplit('/', 1)[1]) if reset else None
    st.close()
    from datetime import datetime, timedelta
    test('the reset link expires within the hour', inv is not None
         and inv['expires_at'] <= (datetime.now() + timedelta(minutes=61)).isoformat())
    morgan.get('/')   # still signed in before the reset
    r = anon.post(reset, data={'password': 'morgan-pass-2', 'password2': 'morgan-pass-2'})
    test('the reset link sets a new password', r.status_code == 302)
    test('the old session is ended', morgan.get('/').status_code == 302)
    notices = [m for m in mail_to('morgan@cedar.test')
               if 'was changed' in m['Subject']]
    test('a "password was changed" notice is emailed', len(notices) == 1)
    r = app.test_client().post('/login', data={'email': 'morgan@cedar.test',
                                               'password': 'morgan-pass-2'})
    test('the new password signs in', r.status_code == 302 and '/login' not in r.headers['Location'])
    test('the reset link works once', anon.get(reset).status_code == 410)

    _Box.messages.clear()
    spam = app.test_client()
    for _ in range(8):
        spam.post('/forgot', data={'email': 'avery@cedar.test'})
    test('reset emails are throttled per address (5 in 15 minutes)',
         len(mail_to('avery@cedar.test')) == 5)

    print('\nAudit trail')
    page = admin.get('/admin/audit').get_data(as_text=True)
    test('the reset request is in the Audit Trail',
         'password_reset_requested' in page or 'reset link emailed' in page)
    test('the emailed invite is in the Audit Trail', 'invite emailed' in page)

    print('\nOperator')
    _Box.messages.clear()
    op = app.test_client()
    op.post('/operator/login', data={'email': 'ops@cedar.test', 'password': 'operator-pass-1'})
    page = op.post('/operator/provision', data={
        'org_name': 'Birch Test Realty', 'plan': 'standard', 'admin_name': 'Casey Stone',
        'admin_email': 'rstone@birch.test'}, follow_redirects=True).get_data(as_text=True)
    msgs = mail_to('rstone@birch.test')
    test('provisioning emails the welcome message', len(msgs) == 1
         and 'workspace for Birch Test Realty is ready' in msgs[0].get_content()
         and link_in(msgs[0]))
    test('the console says it was emailed and keeps a copy', 'Emailed to rstone@birch.test' in page
         and 'welcome-msg' in page)
    op.post(f'/operator/org/{ORG}/reset-password', data={
        'user_id': 'morgan', 'new_password': 'support-pass-1', 'new_password2': 'support-pass-1'})
    test('a support reset emails a changed notice',
         any('by Capactive support' in m.get_content() for m in mail_to('morgan@cedar.test')))

    print('\nMail server down')
    server.shutdown()
    server.server_close()
    _Box.messages.clear()
    page = admin.post('/admin/users', data={'action': 'add', 'display_name': 'Jamie Ortiz',
                                            'email': 'jamie@cedar.test', 'role': 'viewer'},
                      follow_redirects=True).get_data(as_text=True)
    test('the page still works and shows the link to send by hand',
         'Invite link for Jamie Ortiz' in page and 'Send this to jamie@cedar.test' in page)

    print('\nEmail off')
    for k in ('CAPACTIVE_SMTP_HOST', 'CAPACTIVE_MAIL_FROM'):
        os.environ.pop(k)
    page = app.test_client().get('/login').get_data(as_text=True)
    test('no "Forgot password?" link without email', 'Forgot password?' not in page)
    page = app.test_client().post('/forgot', data={'email': 'avery@cedar.test'}).get_data(as_text=True)
    test('/forgot says to ask the admin', 'Ask your admin' in page)

    print(f'\n{passed} passed, {failed} failed')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
