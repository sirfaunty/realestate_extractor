"""
Tests for worker_pool.py: a task runs in a separate process, its progress
steps reach the caller in order, its exceptions surface, and the in-process
fallback (CAPACTIVE_PROC_WORKERS=0) behaves the same.

    venv/Scripts/python tests/test_worker_pool.py      (no pytest needed)
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from realestate_extractor import worker_pool  # noqa: E402


def _echo_task(progress, n, fail=False):
    for i in range(n):
        progress.put(('step', f'{i} pid={os.getpid()}'))
    if fail:
        raise ValueError('task failed on purpose')
    return {'n': n, 'pid': os.getpid()}


def test_runs_in_another_process_and_relays_progress():
    steps = []
    out = worker_pool._run(_echo_task, on_step=lambda s, d: steps.append(d), n=5)
    assert out['n'] == 5 and out['pid'] != os.getpid(), out
    assert [d.split()[0] for d in steps] == ['0', '1', '2', '3', '4'], steps


def test_task_exception_reaches_the_caller():
    try:
        worker_pool._run(_echo_task, n=1, fail=True)
    except ValueError as e:
        assert 'on purpose' in str(e)
    else:
        raise AssertionError('expected the task error')
    # the pool is still usable afterwards
    assert worker_pool._run(_echo_task, n=0)['n'] == 0


def test_in_process_fallback():
    saved = worker_pool._PROC_WORKERS
    worker_pool._PROC_WORKERS = 0
    try:
        steps = []
        out = worker_pool._run(_echo_task, on_step=lambda s, d: steps.append(d), n=2)
        assert out['pid'] == os.getpid() and len(steps) == 2, (out, steps)
    finally:
        worker_pool._PROC_WORKERS = saved


if __name__ == '__main__':
    fails = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith('test_')]
    for name, fn in tests:
        try:
            fn()
            print(f'  ok    {name}')
        except AssertionError as e:
            fails += 1
            print(f'  FAIL  {name}: {e}')
        except Exception as e:           # noqa: BLE001
            fails += 1
            print(f'  ERROR {name}: {type(e).__name__}: {e}')
    print(f'\n{len(tests) - fails}/{len(tests)} passed')
    sys.exit(1 if fails else 0)
