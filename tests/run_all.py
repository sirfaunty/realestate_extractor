"""
Run every test suite with one command and print one line per suite.

    venv/Scripts/python tests/run_all.py

Most suites are plain scripts (exit code 0 = pass); test_deal_config_golden
and test_rollup are pytest-style and run under pytest. The deal golden
snapshot (tests/deal_golden_snapshot.py verify ...) and the accuracy
harnesses are separate: they need client data.
"""

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PYTEST_SUITES = {'test_deal_config_golden.py', 'test_rollup.py'}


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    suites = sorted(f for f in os.listdir(HERE)
                    if f.startswith('test_') and f.endswith('.py'))
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    failed = []
    for name in suites:
        path = os.path.join(HERE, name)
        cmd = ([sys.executable, '-m', 'pytest', '-q', path] if name in PYTEST_SUITES
               else [sys.executable, path])
        t0 = time.time()
        r = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True,
                           text=True, encoding='utf-8', errors='replace')
        last = next((ln for ln in reversed(r.stdout.strip().splitlines()) if ln.strip()), '')
        status = 'ok  ' if r.returncode == 0 else 'FAIL'
        print(f'{status} {name:<32} {time.time() - t0:5.1f}s  {last[:70]}')
        if r.returncode:
            failed.append(name)
            print('\n'.join('     ' + ln for ln in (r.stdout + r.stderr).strip().splitlines()[-8:]))
    print(f'\n{len(suites) - len(failed)}/{len(suites)} suites passed')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
