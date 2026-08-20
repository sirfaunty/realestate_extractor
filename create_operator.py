"""
Bootstrap a Capactive operator (staff) account — the credential class for
the /operator portal. Run on the instance host; there is deliberately no
way to create operators from any web UI.

    venv/Scripts/python create_operator.py ops@capactive.com "Patrick F"
    (prompts for password)
"""

import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from werkzeug.security import generate_password_hash  # noqa: E402
from realestate_extractor.config import ConfigStore  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description='Create a Capactive operator.')
    ap.add_argument('email')
    ap.add_argument('display_name')
    ap.add_argument('--config-db', default=os.environ.get(
        'CAPACTIVE_CONFIG_DB', 'capactive_config.db'))
    args = ap.parse_args()

    pw = getpass.getpass('Operator password (min 12 chars): ')
    if len(pw) < 12:
        sys.exit('Password must be at least 12 characters.')
    if pw != getpass.getpass('Confirm: '):
        sys.exit('Passwords do not match.')

    store = ConfigStore(config_path=args.config_db)
    store.connect()
    try:
        if store.get_operator_by_email(args.email):
            sys.exit(f'Operator {args.email} already exists.')
        op_id = store.create_operator(args.email, args.display_name,
                                      generate_password_hash(pw))
        store.log_operator_action(op_id, 'operator_created',
                                  detail=f'via CLI for {args.email}')
        print(f'Operator created: {op_id} ({args.email})')
        print('Portal: /operator/login')
    finally:
        store.close()


if __name__ == '__main__':
    main()
