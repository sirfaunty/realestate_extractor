"""
Run CPU-heavy jobs in a separate process so page requests stay fast.

The web server is one Python process: job threads and request threads share
one interpreter lock. Measured 2026-10-13 on a dev-org copy: a rules-only
property analysis running in a job thread slowed a roster render from 2.1 s
to 3.9 s and stretched itself from 7 s to 16 s. OCR ingest did NOT contend
(Tesseract runs outside Python), so ingest stays on threads.

Jobs keep their shape: the job thread calls run_analysis(...), which blocks
that thread (not the server) until the worker process finishes, relaying each
progress step to the job's on_step callback as it happens.

    CAPACTIVE_PROC_WORKERS   worker processes (default 1; 0 = run in-process)
"""

import logging
import os
import queue as _queue
import threading

logger = logging.getLogger(__name__)

_PROC_WORKERS = int(os.environ.get('CAPACTIVE_PROC_WORKERS', '1'))
_lock = threading.Lock()
_pool = None
_manager = None


def _get_pool():
    """The pool and a manager for progress queues, started on first use.
    'spawn' on every platform: forking a multi-threaded server process can
    copy a held lock into the child and deadlock it."""
    global _pool, _manager
    with _lock:
        if _pool is None:
            import multiprocessing as mp
            from concurrent.futures import ProcessPoolExecutor
            ctx = mp.get_context('spawn')
            _manager = ctx.Manager()
            _pool = ProcessPoolExecutor(max_workers=_PROC_WORKERS, mp_context=ctx)
            logger.info(f'worker pool started: {_PROC_WORKERS} process(es)')
        return _pool, _manager


def _reset_pool():
    """A worker that died (out of memory, killed) breaks the whole pool;
    drop it so the next job starts a fresh one."""
    global _pool
    with _lock:
        if _pool is not None:
            try:
                _pool.shutdown(wait=False, cancel_futures=True)
            except Exception:                      # noqa: BLE001
                pass
            _pool = None


# ─── tasks (module-level so they pickle) ──────────────────────────

def _analysis_task(progress, db_path, property_id, doc_ids=None, versioned=False,
                   use_ai=False, ollama_url=None, ollama_model=None):
    """Runs IN THE WORKER PROCESS: its own DB connection, its own LLM client."""
    from .database import Database
    from .extractors.llm_client import DisabledLLMClient, LocalLLMClient
    from .property_analyzer import PropertyAnalyzer
    kw = {k: v for k, v in (('base_url', ollama_url), ('model', ollama_model)) if v}
    llm = LocalLLMClient(**kw) if use_ai else DisabledLLMClient(**kw)
    db = Database(db_path)
    db.connect()
    try:
        pa = PropertyAnalyzer(db, llm)
        if progress is not None:
            pa._on_step = lambda step, detail='': progress.put((step, detail))
        if doc_ids is not None:
            return pa.analyze_documents(property_id, list(doc_ids))
        return pa.analyze_property(property_id, versioned=versioned)
    finally:
        db.close()


# ─── caller side (runs in the job thread) ─────────────────────────

def _run(task, on_step=None, **kwargs):
    if _PROC_WORKERS <= 0:
        relay = _CallbackQueue(on_step)
        return task(relay, **kwargs)
    from concurrent.futures.process import BrokenProcessPool
    pool, manager = _get_pool()
    progress = manager.Queue()
    try:
        fut = pool.submit(task, progress, **kwargs)
    except BrokenProcessPool:
        _reset_pool()
        pool, manager = _get_pool()
        progress = manager.Queue()
        fut = pool.submit(task, progress, **kwargs)
    while True:
        try:
            step, detail = progress.get(timeout=0.25)
            if on_step:
                on_step(step, detail)
            continue
        except _queue.Empty:
            pass
        if fut.done():
            break
    while True:                       # steps sent just before the task returned
        try:
            step, detail = progress.get_nowait()
        except _queue.Empty:
            break
        if on_step:
            on_step(step, detail)
    try:
        return fut.result()
    except BrokenProcessPool:
        _reset_pool()
        raise RuntimeError('the analysis worker process stopped unexpectedly '
                           '(out of memory?) — run Analyze again')


class _CallbackQueue:
    """In-process stand-in for the progress queue (CAPACTIVE_PROC_WORKERS=0)."""
    def __init__(self, on_step):
        self._cb = on_step

    def put(self, item):
        if self._cb:
            self._cb(*item)


def run_analysis(db_path, property_id, *, doc_ids=None, versioned=False, use_ai=False,
                 on_step=None, ollama_url=None, ollama_model=None):
    """Property analysis in a worker process. Blocks the calling job thread;
    returns the analyzer's summary dict."""
    return _run(_analysis_task, on_step=on_step, db_path=db_path, property_id=property_id,
                doc_ids=doc_ids, versioned=versioned, use_ai=use_ai,
                ollama_url=ollama_url, ollama_model=ollama_model)
