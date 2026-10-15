"""
Outgoing email (invites, password resets, password-changed notices).

Plain SMTP, so the provider is a configuration choice, not code. Our
provider is Postmark (transactional stream):

    CAPACTIVE_SMTP_HOST=smtp.postmarkapp.com
    CAPACTIVE_SMTP_PORT=587                 # STARTTLS; 465 = implicit TLS
    CAPACTIVE_SMTP_USER=<server API token>
    CAPACTIVE_SMTP_PASSWORD=<same server API token>
    CAPACTIVE_MAIL_FROM=Capactive <no-reply@capactive.co>
    CAPACTIVE_MAIL_REPLY_TO=support@capactive.co   # optional

With no host or From address, email is off: nothing is sent and every
page falls back to a link the admin copies and sends themselves.
"""

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

logger = logging.getLogger(__name__)


def _settings():
    env = os.environ.get
    return {
        'host': env('CAPACTIVE_SMTP_HOST', '').strip(),
        'port': int(env('CAPACTIVE_SMTP_PORT', '587') or 587),
        'user': env('CAPACTIVE_SMTP_USER', '').strip(),
        'password': env('CAPACTIVE_SMTP_PASSWORD', ''),
        'sender': env('CAPACTIVE_MAIL_FROM', '').strip(),
        'reply_to': env('CAPACTIVE_MAIL_REPLY_TO', '').strip(),
        # 0 only for a local test server that has no TLS
        'tls': env('CAPACTIVE_SMTP_TLS', '1') != '0',
    }


def mail_enabled() -> bool:
    s = _settings()
    return bool(s['host'] and s['sender'])


def send_email(to: str, subject: str, body: str) -> bool:
    """Send a plain-text email. Returns True if the server accepted it.
    Never raises: a mail problem must not break the page that sent it."""
    s = _settings()
    if not (s['host'] and s['sender'] and to):
        return False
    msg = EmailMessage()
    msg['From'] = s['sender']
    msg['To'] = to
    msg['Subject'] = subject
    msg['Date'] = formatdate(localtime=True)
    msg['Message-ID'] = make_msgid(domain=s['sender'].rsplit('@', 1)[-1].strip('> '))
    if s['reply_to']:
        msg['Reply-To'] = s['reply_to']
    msg.set_content(body)
    try:
        if s['port'] == 465 and s['tls']:
            server = smtplib.SMTP_SSL(s['host'], s['port'], timeout=15,
                                      context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(s['host'], s['port'], timeout=15)
        with server:
            if s['tls'] and s['port'] != 465:
                server.starttls(context=ssl.create_default_context())
            if s['user']:
                server.login(s['user'], s['password'])
            server.send_message(msg)
        logger.info('email sent to %s: %s', to, subject)
        return True
    except Exception as e:
        logger.warning('email to %s failed (%s): %s: %s',
                       to, subject, type(e).__name__, e)
        return False


# ─── Messages ──────────────────────────────────────────────────────

SIGNATURE = '\n\n— Capactive\n'


def invite_message(name, org_name, email, url, login_url, admin=False):
    lines = [f'Hi {name},', '']
    if admin:
        lines += [f'Your Capactive workspace for {org_name} is ready.', '']
    else:
        lines += [f"You've been added to {org_name}'s Capactive workspace.", '']
    lines += [
        '1. Set your password (this link works once, for 7 days):',
        f'   {url}',
        f'2. After that, sign in at {login_url} with {email}.',
    ]
    if admin:
        lines += ['3. Add your first property, upload its leases, then open its Lease Roster.',
                  '   Add teammates under Admin > Users; each gets their own invite email.']
    return (f'Set your Capactive password — {org_name}',
            '\n'.join(lines) + SIGNATURE)


def reset_message(name, url, hours):
    return ('Reset your Capactive password',
            f'Hi {name},\n\n'
            f'Someone (hopefully you) asked to reset your Capactive password.\n'
            f'Choose a new one here (the link works once, for {hours} hour'
            f'{"s" if hours != 1 else ""}):\n\n   {url}\n\n'
            f"If you didn't ask, ignore this email — your password stays as it is."
            + SIGNATURE)


def password_changed_message(name, how):
    return ('Your Capactive password was changed',
            f'Hi {name},\n\n'
            f'Your Capactive password was just changed ({how}), and any other '
            f'signed-in sessions were ended.\n\n'
            f"If this wasn't you, contact your admin right away."
            + SIGNATURE)
