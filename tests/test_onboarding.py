"""
A new customer's first minutes on a fresh instance: setup needs a license
key we issued, the first sign-in says Welcome (not "back"), and the
dashboard's getting-started checklist ticks itself off. Runs the real app
(no dev bypass) against a throwaway data dir. Fictional org and people.

    venv/Scripts/python tests/test_onboarding.py
"""

import os
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix='cap_onboard_')
os.environ['CAPACTIVE_DEV_MODE'] = '0'
os.environ['CAPACTIVE_DATA_DIR'] = TMP
os.environ['CAPACTIVE_CONFIG_DB'] = os.path.join(TMP, 'config.db')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from realestate_extractor.licensing import generate_org_key  # noqa: E402
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


app.config['TESTING'] = True
c = app.test_client()
FORM = {'org_name': 'Northfield Test Group', 'admin_name': 'Jordan Lee',
        'admin_email': 'Jordan@Northfield.test', 'password': 'jordan-pass-1',
        'password_confirm': 'jordan-pass-1'}

print('\nSetup on a fresh instance')
page = c.get('/setup').get_data(as_text=True)
test('the setup page no longer offers a keyless start', 'Leave blank' not in page)
test('placeholders are neutral examples', 'e.g. Jordan Lee' in page and 'capactive.com' not in page)
test('no "No cloud" claim on a hosted instance', 'No cloud' not in page)
r = c.post('/setup', data=FORM, follow_redirects=True)
test('setup without a license key is refused', 'license key' in r.get_data(as_text=True)
     and c.get('/setup').status_code == 200)
r = c.post('/setup', data={**FORM, 'license_key': 'CAP-STD-00000000-0000'},
           follow_redirects=True)
test('setup with a made-up key is refused', c.get('/setup').status_code == 200)
r = c.post('/setup', data={**FORM, 'license_key': generate_org_key('northfield', 'standard')})
test('setup with an issued key creates the org', r.status_code == 302
     and '/setup' not in r.headers['Location'])
test('once set up, /setup is closed', c.get('/setup').status_code == 302)

print('\nFirst sign-in')
c.get('/logout')
r = c.post('/login', data={'email': 'jordan@northfield.test', 'password': 'jordan-pass-1'},
           follow_redirects=True)
page = r.get_data(as_text=True)
test('the first sign-in says Welcome, not Welcome back',
     'Welcome, Jordan Lee!' in page and 'Welcome back' not in page)
c.get('/logout')
page = c.post('/login', data={'email': 'jordan@northfield.test', 'password': 'jordan-pass-1'},
              follow_redirects=True).get_data(as_text=True)
test('later sign-ins say Welcome back', 'Welcome back, Jordan Lee!' in page)

print('\nGetting-started checklist')
page = c.get('/').get_data(as_text=True)
test('an empty account shows the checklist', 'Getting started' in page and '0 of 4 done' in page)
test('the first step opens the Add Property form', '/properties?add=1' in page)
test('admins see Invite your team', 'Invite your team' in page)
test('the upload step has no link before a property exists', '/extract' not in
     page.split('Getting started', 1)[1].split('</ol>', 1)[0])
c.post('/properties/create', data={'name': 'Harbor Test Plaza', 'property_type': 'retail'})
page = c.get('/').get_data(as_text=True)
test('adding a property ticks the first step', '1 of 4 done' in page)
test('the next step links to upload for that property', '/extract?property_id=' in page)
page = c.get('/extract').get_data(as_text=True)
test('the upload page leads with the property; document type is tucked away',
     page.index('name="property_id"') < page.index('name="doc_type"')
     and 'Set it yourself' in page)
test('the Add Property page opens its form from ?add=1',
     "get('add') === '1'" in c.get('/properties?add=1').get_data(as_text=True))

print(f'\n{passed} passed, {failed} failed')
sys.exit(1 if failed else 0)
