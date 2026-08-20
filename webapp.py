"""
Flask Web Application for Capactive Document Extractor.

Runs locally at http://localhost:5000 — all processing stays on-device.
No external network calls, no cloud dependencies.

Multi-tenant with org/user authentication, feature gating,
usage tracking, and admin panel.
"""

import os
import json
import threading
import time
import uuid
import functools
from pathlib import Path
import re
from datetime import datetime

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, jsonify, send_file, session, g, Response
)
from werkzeug.utils import secure_filename

from .database import Database
from .batch_processor import BatchProcessor, ProcessingResult
from .property_analyzer import PropertyAnalyzer
from .financial_synthesis import FinancialSynthesizer
from .extractors.llm_client import LocalLLMClient
from .extractors.extraction_engine import DocumentClassifier
from .templates.document_templates import list_templates, TEMPLATES
from .reconciliation import reconcile_terms
from .config import ConfigStore, PLAN_FEATURES
from .licensing import (
    generate_org_key, generate_user_key, validate_org_key,
    validate_user_key, EntitlementChecker, create_license_file,
    read_license_file
)
from .usage import UsageTracker
from .permissions import (
    PermissionStore, ROLE_TEMPLATES, SCOPES, SCOPE_ORDER, LEVELS,
    check_permission, can_read, can_edit, get_scope_categories,
    seat_class_for_role, count_seats, SEAT_CLASS_LABELS
)

# ─── App Setup ───────────────────────────────────────────────────────

app = Flask(__name__, template_folder='web/templates', static_folder='web/static')
app.secret_key = os.environ.get('CAPACTIVE_SECRET_KEY', os.urandom(24))

# Configuration
DATA_DIR = os.environ.get('CAPACTIVE_DATA_DIR', 'data')
CONFIG_DB = os.environ.get('CAPACTIVE_CONFIG_DB', 'capactive_config.db')
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
OLLAMA_URL = os.environ.get('CAPACTIVE_OLLAMA_URL', 'http://localhost:11434')
OLLAMA_MODEL = os.environ.get('CAPACTIVE_OLLAMA_MODEL', 'llama3.1:8b')
ALLOWED_EXTENSIONS = {'pdf', 'xlsx', 'xls', 'docx', 'doc', 'msg', 'png', 'jpg', 'jpeg', 'md', 'txt', 'csv', 'tsv'}
ARCHIVE_EXTENSIONS = {'zip'}

# Max upload size: 500 MB (supports ~100 large PDFs in a single batch)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024


@app.after_request
def _inject_global_progress(resp):
    """Inject the universal job-progress widget into every HTML page.

    Module pages are standalone render_template_string documents that don't
    extend base.html, so a template include can't reach them — this hook
    makes the running-jobs pill truly app-wide. The script defers to the
    inline banner on base.html pages and to /job/ status pages.
    """
    try:
        if (resp.status_code == 200 and not resp.direct_passthrough
                and resp.mimetype == 'text/html'):
            body = resp.get_data(as_text=True)
            if '</body>' in body and 'global_progress.js' not in body:
                resp.set_data(body.replace(
                    '</body>',
                    '<script src="/static/global_progress.js"></script></body>',
                    1))
    except Exception:
        pass  # never let the widget break a page
    return resp

# Dev mode: skip login/setup for local testing
# Set CAPACTIVE_DEV_MODE=1 to bypass authentication
DEV_MODE = os.environ.get('CAPACTIVE_DEV_MODE', '0') == '1'

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

# Global state for background jobs — org-scoped with thread safety
# Jobs are stored as jobs[job_id] = {org_id: ..., ...}
# Old jobs are automatically cleaned up after JOB_EXPIRY_HOURS
import collections

jobs = {}
_jobs_lock = threading.Lock()
JOB_EXPIRY_HOURS = int(os.environ.get('CAPACTIVE_JOB_EXPIRY_HOURS', '24'))


MAX_RETAINED_JOBS = int(os.environ.get('CAPACTIVE_MAX_JOBS', '500'))


def _cleanup_expired_jobs():
    """Remove jobs older than JOB_EXPIRY_HOURS and enforce size cap."""
    cutoff = datetime.now().timestamp() - (JOB_EXPIRY_HOURS * 3600)
    with _jobs_lock:
        # Remove expired jobs
        expired = [
            jid for jid, job in jobs.items()
            if datetime.fromisoformat(job.get('started', datetime.now().isoformat())).timestamp() < cutoff
        ]
        for jid in expired:
            del jobs[jid]

        # If still over cap, evict oldest completed jobs
        if len(jobs) > MAX_RETAINED_JOBS:
            completed = sorted(
                [(jid, job) for jid, job in jobs.items()
                 if job.get('status') in ('completed', 'failed')],
                key=lambda x: x[1].get('started', ''),
            )
            to_evict = len(jobs) - MAX_RETAINED_JOBS
            for jid, _ in completed[:to_evict]:
                del jobs[jid]


# ─── Rate Limiting ──────────────────────────────────────────────────
# Simple in-memory per-org rate limiter for upload/batch endpoints.
# Limits: max N requests per window (sliding window counter).

_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX = int(os.environ.get('CAPACTIVE_RATE_LIMIT', '30'))  # requests per window
_rate_counters = {}  # org_id -> list of timestamps
_rate_lock = threading.Lock()


def _check_rate_limit(org_id):
    """Returns True if request is allowed, False if rate limited."""
    now = time.time()
    with _rate_lock:
        if org_id not in _rate_counters:
            _rate_counters[org_id] = []
        # Remove expired entries
        _rate_counters[org_id] = [
            t for t in _rate_counters[org_id]
            if now - t < _RATE_LIMIT_WINDOW
        ]
        if len(_rate_counters[org_id]) >= _RATE_LIMIT_MAX:
            return False
        _rate_counters[org_id].append(now)
        return True

# ─── Processing Queue ───────────────────────────────────────────────
# Configurable worker pool for parallel job processing.
# Phase 1 (ingest) is CPU/IO-bound with no LLM, so parallel workers
# are safe and dramatically speed up large batches.
# Phase 2 (analyze) uses Ollama, so those are still sequential per-job.

import queue as _queue
from concurrent.futures import ThreadPoolExecutor, as_completed

# Number of concurrent job workers (each job runs in its own thread)
_NUM_JOB_WORKERS = int(os.environ.get('CAPACTIVE_JOB_WORKERS', '2'))

# Number of parallel document processors within a single batch job
_NUM_DOC_WORKERS = int(os.environ.get('CAPACTIVE_DOC_WORKERS', '4'))

_work_queue = _queue.Queue()


def _queue_worker():
    """Persistent worker thread — pulls jobs from the queue one at a time."""
    while True:
        work_item = _work_queue.get()
        try:
            job_id = work_item['job_id']
            jobs[job_id]['status'] = 'processing'
            jobs[job_id]['step'] = 'ingesting'
            jobs[job_id]['queue_position'] = 0
            # Update queue positions for remaining queued jobs
            _refresh_queue_positions()
            work_item['fn']()
        except Exception as e:
            jobs[work_item['job_id']]['status'] = 'failed'
            jobs[work_item['job_id']]['error'] = str(e)
        finally:
            _work_queue.task_done()


def _refresh_queue_positions():
    """Recalculate queue positions for all queued jobs."""
    pos = 1
    for jid, job in jobs.items():
        if job.get('status') == 'queued':
            job['queue_position'] = pos
            pos += 1


# Start worker pool — multiple workers can process jobs in parallel
_worker_threads = []
for _i in range(_NUM_JOB_WORKERS):
    _t = threading.Thread(target=_queue_worker, daemon=True, name=f'job-worker-{_i}')
    _t.start()
    _worker_threads.append(_t)


def enqueue_job(job_id, process_fn):
    """Add a job to the processing queue. It will run when its turn comes."""
    pending_count = _work_queue.qsize()
    jobs[job_id]['status'] = 'queued' if pending_count > 0 else 'processing'
    jobs[job_id]['queue_position'] = pending_count + 1 if pending_count > 0 else 0
    _work_queue.put({'job_id': job_id, 'fn': process_fn})


def _process_single_doc_thread(org_id, user_id, filepath, doc_type, property_name):
    """Process a single document in its own thread with its own DB connection.
    Returns (result, error_occurred) tuple."""
    fname = os.path.basename(filepath)
    db = None
    try:
        db = get_org_db(org_id)
        llm = get_llm()
        processor = BatchProcessor(db, llm)
        result = processor.process_single(
            filepath, document_type=doc_type,
            property_name=property_name)
        return result, False
    except Exception as doc_err:
        result = ProcessingResult(
            filename=fname, success=False,
            error=str(doc_err),
            document_type=doc_type or 'unknown',
            page_count=0, processing_time=0,
            tables_stored=0
        )
        return result, True
    finally:
        if db:
            db.close()

# ─── Helpers ─────────────────────────────────────────────────────────

def get_config_store():
    """Get or create a per-request ConfigStore cached in flask.g."""
    # Check if we're in a request context before accessing flask.g
    try:
        if hasattr(g, '_config_store') and g._config_store is not None:
            store = g._config_store
            # Reconnect if a prior caller closed it within this request
            if store.conn is None:
                store.connect()
            else:
                try:
                    store.conn.execute("SELECT 1")
                except Exception:
                    store.connect()
            return store
    except RuntimeError:
        pass  # Outside request context (background thread) — create fresh

    store = ConfigStore(CONFIG_DB, DATA_DIR)
    store.connect()
    try:
        g._config_store = store
    except RuntimeError:
        # Outside request context (e.g. background thread) — return uncached
        return store
    return store

def get_usage_tracker():
    """Get or create a per-request UsageTracker cached in flask.g."""
    try:
        if hasattr(g, '_usage_tracker') and g._usage_tracker is not None:
            return g._usage_tracker
    except RuntimeError:
        pass
    tracker = UsageTracker(CONFIG_DB)
    tracker.connect()
    try:
        g._usage_tracker = tracker
    except RuntimeError:
        return tracker
    return tracker

def get_org_db(org_id):
    """Get the extraction database for a specific org."""
    store = get_config_store()
    db_path = store.get_org_db_path(org_id)
    # Note: don't close store here — it's cached in flask.g
    # and will be closed by _close_cached_stores teardown
    if not db_path:
        return None
    db = Database(db_path)
    db.connect()
    return db

@app.errorhandler(413)
def request_entity_too_large(error):
    flash('Upload too large. Maximum total upload size is 500 MB. '
          'Try splitting into smaller batches.', 'error')
    return redirect(request.url or url_for('batch'))


def get_permission_store():
    """Get or create a per-request PermissionStore cached in flask.g."""
    try:
        if hasattr(g, '_permission_store') and g._permission_store is not None:
            store = g._permission_store
            if store.conn is None:
                store.connect()
            else:
                try:
                    store.conn.execute("SELECT 1")
                except Exception:
                    store.connect()
            return store
    except RuntimeError:
        pass  # Outside request context (background thread) — create fresh

    store = PermissionStore(CONFIG_DB)
    store.connect()
    try:
        g._permission_store = store
    except RuntimeError:
        return store
    return store

@app.teardown_appcontext
def _close_cached_stores(exc):
    """Close any per-request cached stores at end of request."""
    for attr in ('_config_store', '_permission_store', '_usage_tracker'):
        store = g.pop(attr, None)
        if store is not None:
            try:
                store.close()
            except Exception:
                pass

def get_llm():
    return LocalLLMClient(base_url=OLLAMA_URL, model=OLLAMA_MODEL)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def is_archive(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ARCHIVE_EXTENSIONS

def extract_zip(zip_path, dest_dir, _depth=0):
    """Extract a zip file, return list of (filepath, ext) for all files found.
    Skips __MACOSX and hidden files. Flattens nested folders.
    Recursively expands nested ZIP files up to 3 levels deep."""
    import zipfile
    MAX_DEPTH = 3
    extracted = []
    skipped = []
    with zipfile.ZipFile(zip_path, 'r') as zf:
        for member in zf.namelist():
            # Skip directories, macOS resource forks, hidden files
            if member.endswith('/'):
                continue
            basename = os.path.basename(member)
            if not basename or basename.startswith('.') or '__MACOSX' in member:
                continue

            ext = basename.rsplit('.', 1)[1].lower() if '.' in basename else ''

            # Extract to flat dest_dir with safe name
            safe_name = secure_filename(basename)
            if not safe_name:
                continue

            # Handle duplicate filenames
            dest_path = os.path.join(dest_dir, safe_name)
            counter = 1
            while os.path.exists(dest_path):
                name_part, ext_part = os.path.splitext(safe_name)
                dest_path = os.path.join(dest_dir, f"{name_part}_{counter}{ext_part}")
                counter += 1

            # Extract the single file
            with zf.open(member) as src, open(dest_path, 'wb') as dst:
                dst.write(src.read())

            # Recursively expand nested ZIP files
            if ext == 'zip' and _depth < MAX_DEPTH:
                try:
                    nested_extracted, nested_skipped = extract_zip(dest_path, dest_dir, _depth + 1)
                    extracted.extend(nested_extracted)
                    skipped.extend(nested_skipped)
                    # Remove the nested zip after expansion
                    os.remove(dest_path)
                except Exception as e:
                    logger.warning(f"Failed to expand nested ZIP {basename}: {e}")
                    skipped.append((basename, ext))
            elif ext in ALLOWED_EXTENSIONS:
                extracted.append((dest_path, ext))
            else:
                skipped.append((basename, ext))

    return extracted, skipped

def is_setup_complete():
    """Check if initial setup has been completed."""
    store = get_config_store()
    orgs = store.list_orgs()
    return len(orgs) > 0

def get_current_user():
    """Get the current logged-in user from session."""
    if 'user_id' not in session:
        return None
    return {
        'user_id': session.get('user_id'),
        'org_id': session.get('org_id'),
        'org_name': session.get('org_name'),
        'display_name': session.get('display_name'),
        'role': session.get('role'),
        'plan': session.get('plan'),
    }


# ──�� Auth Decorators ─────────────────────────────────────────────────

def _ensure_dev_session():
    """In dev mode, auto-create org/user and populate session if needed."""
    if 'user_id' in session:
        return
    # Auto-provision a dev org and admin user
    store = get_config_store()
    orgs = store.list_orgs()
    if not orgs:
        from .licensing import generate_org_key, generate_user_key
        from werkzeug.security import generate_password_hash
        org_key = generate_org_key('dev', 'enterprise')
        store.create_org('dev', 'Dev Testing', org_key, plan='enterprise')
        store.create_user('dev', 'admin', 'admin@capactive.local',
                          'Dev Admin', role='admin',
                          password_hash=generate_password_hash('devadmin'))
        # Init permissions
        pstore = get_permission_store()
        pstore.init_user_permissions('admin', 'dev', role='admin')
    org = store.get_org('dev') or store.list_orgs()[0]
    users = store.list_users(org.org_id)
    user = users[0] if users else None

    if org and user:
        session['user_id'] = user['user_id']
        session['org_id'] = org.org_id
        session['org_name'] = org.org_name
        session['display_name'] = user['display_name']
        session['role'] = user['role']
        session['plan'] = org.plan

    # Ensure the org's extraction database exists
    db = get_org_db(session.get('org_id', 'dev'))
    if db:
        db.close()


def login_required(f):
    """Require authentication for a route."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if DEV_MODE:
            _ensure_dev_session()
            return f(*args, **kwargs)
        if not is_setup_complete():
            return redirect(url_for('setup'))
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def device_required(f):
    """Machine-to-machine auth for extraction clients (Phase 2 sync).

    Expects `Authorization: Bearer cap_...` and optionally
    `X-Device-Fingerprint`. Resolves to a registered, active device;
    fingerprint pins on first contact and must match thereafter.
    Attaches g.device / g.device_org_id.
    """
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get('Authorization', '')
        if not auth.startswith('Bearer '):
            return jsonify({'error': 'device token required'}), 401
        token = auth[7:].strip()
        fingerprint = request.headers.get('X-Device-Fingerprint')
        store = get_config_store()
        try:
            device = store.authenticate_device(token, fingerprint)
        finally:
            store.close()
        if not device:
            return jsonify({'error': 'invalid, revoked, or '
                                     'fingerprint-mismatched token'}), 401
        g.device = device
        g.device_org_id = device['org_id']
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Require admin role for a route."""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if DEV_MODE:
            _ensure_dev_session()
            return f(*args, **kwargs)
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if session.get('role') != 'admin':
            flash('Admin access required.', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated


def permission_required(scope, level='read'):
    """Require a specific permission scope and level for a route."""
    def decorator(f):
        @functools.wraps(f)
        def decorated(*args, **kwargs):
            if DEV_MODE:
                _ensure_dev_session()
                return f(*args, **kwargs)
            if 'user_id' not in session:
                return redirect(url_for('login'))
            store = get_permission_store()  # cached in flask.g
            perms = store.get_user_permissions(
                session['user_id'], session['org_id'])
            if not check_permission(perms, scope, level):
                flash(f'You don\'t have permission to access this feature.', 'error')
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return decorated
    return decorator


# ─── Context Processor ───────────────────────────────────────────────

@app.context_processor
def inject_user():
    """Make current user and permissions available in all templates."""
    user = get_current_user()
    perms = {}
    if user:
        store = get_permission_store()  # cached in flask.g, closed by teardown
        perms = store.get_user_permissions(user['user_id'], user['org_id'])

    def user_can_read(scope):
        return can_read(perms, scope)

    def user_can_edit(scope):
        return can_edit(perms, scope)

    return {
        'current_user': user,
        'user_permissions': perms,
        'can_read': user_can_read,
        'can_edit': user_can_edit,
    }


# ─── Routes: Setup ───────────────────────────────────────────────────

@app.route('/setup', methods=['GET', 'POST'])
def setup():
    """First-run setup wizard."""
    if is_setup_complete():
        return redirect(url_for('login'))

    if request.method == 'POST':
        from werkzeug.security import generate_password_hash

        org_name = request.form.get('org_name', '').strip()
        admin_name = request.form.get('admin_name', '').strip()
        admin_email = request.form.get('admin_email', '').strip()
        password = request.form.get('password', '')
        password_confirm = request.form.get('password_confirm', '')
        license_key = request.form.get('license_key', '').strip()

        if not all([org_name, admin_name, admin_email, password]):
            flash('All fields are required.', 'error')
            return redirect(request.url)

        if len(password) < 8:
            flash('Password must be at least 8 characters.', 'error')
            return redirect(request.url)

        if password != password_confirm:
            flash('Passwords do not match.', 'error')
            return redirect(request.url)

        # Determine plan from license key or default to standard
        plan = 'standard'
        if license_key:
            valid, detected_plan = validate_org_key(license_key)
            if valid:
                plan = detected_plan
            else:
                flash('Invalid license key. Using standard plan.', 'error')

        # Generate IDs and keys
        org_id = org_name.lower().replace(' ', '-')[:32]
        user_id = admin_email.split('@')[0].lower().replace('.', '-')

        if not license_key:
            license_key = generate_org_key(org_id, plan)

        pw_hash = generate_password_hash(password)

        store = get_config_store()
        try:
            org = store.create_org(org_id, org_name, license_key, plan=plan)
            user = store.create_user(org_id, user_id, admin_email,
                                     admin_name, role='admin',
                                     password_hash=pw_hash)
        finally:
            store.close()

        # Initialize the org's extraction database
        db = get_org_db(org_id)
        if db:
            db.close()

        # Initialize admin permissions
        pstore = get_permission_store()
        try:
            pstore.init_user_permissions(user_id, org_id, role='admin')
        finally:
            pstore.close()

        # Auto-login
        session['user_id'] = user_id
        session['org_id'] = org_id
        session['org_name'] = org_name
        session['display_name'] = admin_name
        session['role'] = 'admin'
        session['plan'] = plan

        flash(f'Welcome to capactive, {admin_name}! Your organization is ready.', 'success')
        return redirect(url_for('index'))

    return render_template('setup.html')


# ──��� Routes: Auth ────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    """User login page — email and password."""
    if not is_setup_complete():
        return redirect(url_for('setup'))

    if request.method == 'POST':
        from werkzeug.security import check_password_hash

        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        if not email or not password:
            flash('Email and password are required.', 'error')
            return redirect(request.url)

        store = get_config_store()
        try:
            user = store.get_user_by_email(email)
            if not user:
                flash('Invalid email or password.', 'error')
                return redirect(request.url)

            if not user.get('password_hash'):
                flash('No password set for this account. Contact your admin.', 'error')
                return redirect(request.url)

            if not check_password_hash(user['password_hash'], password):
                flash('Invalid email or password.', 'error')
                return redirect(request.url)

            # Get the org
            org = store.get_org(user['org_id'])
            if not org or not org.is_active:
                flash('Organization is not active.', 'error')
                return redirect(request.url)

            # Login successful
            store.update_user_login(user['user_id'])

            session['user_id'] = user['user_id']
            session['org_id'] = org.org_id
            session['org_name'] = org.org_name
            session['display_name'] = user['display_name']
            session['role'] = user['role']
            session['plan'] = org.plan

        finally:
            store.close()

        # Log login event
        tracker = get_usage_tracker()
        try:
            from .usage import UsageEvent
            tracker.log_event(UsageEvent(
                org_id=org.org_id,
                user_id=user['user_id'],
                action='login',
            ))
        finally:
            tracker.close()

        flash(f'Welcome back, {user["display_name"]}!', 'success')
        return redirect(url_for('index'))

    return render_template('login.html')


@app.route('/logout')
def logout():
    """Log out the current user."""
    session.clear()
    flash('You have been logged out.', 'success')
    return redirect(url_for('login'))


# ─── Routes: Main Pages ────────────────────────────────────���────────

@app.route('/')
@login_required
def index():
    """Dashboard / home page."""
    org_id = session['org_id']
    db = get_org_db(org_id)
    if not db:
        flash('Database error.', 'error')
        return redirect(url_for('login'))
    try:
        dashboard = db.get_dashboard_stats()
        llm = get_llm()
        llm_status = llm.is_available()
    finally:
        db.close()

    # Get usage info
    tracker = get_usage_tracker()
    try:
        usage = tracker.get_monthly_usage(org_id)
    finally:
        tracker.close()

    return render_template('index.html',
                           dashboard=dashboard,
                           llm_status=llm_status,
                           usage=usage)


@app.route('/extract')
@login_required
def file_extractor():
    """Unified file extractor page — single files, folders, or ZIP archives."""
    return render_template('file_extractor.html', templates=list_templates())


@app.route('/upload', methods=['GET', 'POST'])
@login_required
@permission_required('extraction.upload', 'edit')
def upload():
    """Single file upload and processing."""
    org_id = session['org_id']
    user_id = session['user_id']

    if request.method == 'POST':
        # Rate limit check
        if not _check_rate_limit(org_id):
            flash('Too many requests. Please wait a moment and try again.', 'error')
            return redirect(request.url)

        # Check volume limit
        store = get_config_store()
        tracker = get_usage_tracker()
        try:
            org = store.get_org(org_id)
            if org:
                allowed, current, limit, msg = tracker.check_volume_limit(
                    org_id, org.features.max_documents_per_month)
                if not allowed:
                    flash(msg, 'error')
                    return redirect(request.url)
        finally:
            store.close()
            tracker.close()

        # Accept one or more files (PDFs or ZIPs)
        uploaded_files = request.files.getlist('files')
        if not uploaded_files or not any(f.filename for f in uploaded_files):
            flash('No file selected.', 'error')
            return redirect(request.url)

        doc_type = request.form.get('doc_type') or None
        property_name = request.form.get('property_name') or None

        # Check document type entitlement
        if doc_type:
            store = get_config_store()
            try:
                checker = EntitlementChecker(config_store=store)
                allowed_type, reason = checker.check_document_type(org_id, doc_type)
                if not allowed_type:
                    flash(reason, 'error')
                    return redirect(request.url)
            finally:
                store.close()

        # Save uploaded files — handle ZIPs and PDFs
        saved = []
        all_skipped = []
        for f in uploaded_files:
            if not f.filename:
                continue
            filename = secure_filename(f.filename)

            if is_archive(f.filename):
                # Save zip to temp location, then extract PDFs
                zip_path = os.path.join(UPLOAD_FOLDER, filename)
                f.save(zip_path)
                zip_dest = os.path.join(UPLOAD_FOLDER, f'zip_{str(uuid.uuid4())[:8]}')
                os.makedirs(zip_dest, exist_ok=True)
                try:
                    extracted, skipped = extract_zip(zip_path, zip_dest)
                    for fpath, ext in extracted:
                        saved.append((os.path.basename(fpath), fpath))
                    all_skipped.extend(skipped)
                except Exception as e:
                    flash(f'Failed to extract {filename}: {e}', 'error')
                    continue
            elif allowed_file(f.filename):
                filepath = os.path.join(UPLOAD_FOLDER, filename)
                f.save(filepath)
                saved.append((filename, filepath))

        if not saved:
            if all_skipped:
                skip_types = set(ext for _, ext in all_skipped)
                flash(f'No supported files found. Skipped {len(all_skipped)} file(s) of type: {", ".join(skip_types)}.', 'error')
            else:
                flash('No supported files found. Upload documents (PDF, XLSX, DOCX, MSG, etc.) or ZIP archives.', 'error')
            return redirect(request.url)

        if all_skipped:
            skip_types = set(ext for _, ext in all_skipped)
            flash(f'Found {len(saved)} documents. Skipped {len(all_skipped)} unsupported file(s) ({", ".join(skip_types)}).', 'info')

        file_count = len(saved)
        first_filename = saved[0][0]

        # Process in background
        _cleanup_expired_jobs()
        job_id = str(uuid.uuid4())[:8]
        jobs[job_id] = {
            'org_id': org_id,
            'status': 'processing',
            'type': 'single' if file_count == 1 else 'batch',
            'filename': first_filename if file_count == 1 else f'{file_count} documents',
            'progress': 0,
            'total': file_count,
            'results': [],
            'started': datetime.now().isoformat(),
            'step': 'ingesting',
            'step_detail': f'Reading {first_filename}...',
            'steps_log': [{'step': 'ingesting', 'detail': f'Reading {first_filename}...', 'time': datetime.now().isoformat()}],
        }

        def on_step(step, detail=''):
            jobs[job_id]['step'] = step
            jobs[job_id]['step_detail'] = detail
            jobs[job_id]['steps_log'].append({
                'step': step, 'detail': detail,
                'time': datetime.now().isoformat()
            })

        def process_async():
            try:
                failed_count = 0
                completed_count = 0
                _lock = threading.Lock()

                def _on_result(result, failed):
                    nonlocal failed_count, completed_count
                    with _lock:
                        if failed:
                            failed_count += 1
                        completed_count += 1
                        jobs[job_id]['progress'] = completed_count
                        jobs[job_id]['results'].append(_result_to_dict(result))

                    # Log usage
                    t = get_usage_tracker()
                    try:
                        t.log_document_processed(
                            org_id=org_id, user_id=user_id,
                            filename=result.filename,
                            document_type=result.document_type or 'unknown',
                            page_count=result.page_count,
                            processing_time=result.processing_time,
                            terms_count=0,
                            clauses_count=0,
                            tabular_rows=result.tables_stored,
                            success=result.success, error=result.error
                        )
                    finally:
                        t.close()

                if file_count == 1:
                    # Single file — process directly
                    fname, fpath = saved[0]
                    on_step('ingesting', f'Reading {fname}...')
                    result, was_error = _process_single_doc_thread(
                        org_id, user_id, fpath, doc_type, property_name)
                    _on_result(result, was_error)
                else:
                    # Multiple files — process in parallel
                    on_step('ingesting', f'Processing {file_count} files with {_NUM_DOC_WORKERS} workers...')
                    with ThreadPoolExecutor(max_workers=_NUM_DOC_WORKERS) as executor:
                        futures = {}
                        for fname, fpath in saved:
                            future = executor.submit(
                                _process_single_doc_thread,
                                org_id, user_id, fpath,
                                doc_type, property_name
                            )
                            futures[future] = fname

                        for future in as_completed(futures):
                            fname = futures[future]
                            result, was_error = future.result()
                            _on_result(result, was_error)
                            on_step('ingesting',
                                    f'Processed {fname} ({completed_count}/{file_count})')

                on_step('complete', 'Ingested' if file_count == 1 else f'All {file_count} files ingested')
                jobs[job_id]['status'] = 'completed'
                jobs[job_id]['failed_count'] = failed_count
                if file_count == 1 and jobs[job_id]['results']:
                    jobs[job_id]['error'] = jobs[job_id]['results'][0].get('error')
            except Exception as e:
                jobs[job_id]['status'] = 'failed'
                jobs[job_id]['error'] = str(e)

        enqueue_job(job_id, process_async)

        return redirect(url_for('job_status', job_id=job_id))

    # GET requests redirect to unified file extractor page
    return redirect(url_for('file_extractor'))


@app.route('/batch', methods=['GET', 'POST'])
@login_required
@permission_required('extraction.batch', 'edit')
def batch():
    """Batch folder processing — accepts uploaded files via folder picker."""
    org_id = session['org_id']
    user_id = session['user_id']

    if request.method == 'POST':
        # Rate limit check
        if not _check_rate_limit(org_id):
            flash('Too many requests. Please wait a moment and try again.', 'error')
            return redirect(request.url)

        uploaded_files = request.files.getlist('files')

        # Save all uploaded files to a batch subfolder
        batch_id = str(uuid.uuid4())[:8]
        batch_dir = os.path.join(UPLOAD_FOLDER, f'batch_{batch_id}')
        os.makedirs(batch_dir, exist_ok=True)

        saved_paths = []
        all_skipped = []

        for f in uploaded_files:
            if not f.filename:
                continue
            basename = os.path.basename(f.filename)
            filename = secure_filename(basename)
            if not filename:
                continue

            if is_archive(basename):
                # Save zip, extract PDFs from it
                zip_path = os.path.join(batch_dir, filename)
                f.save(zip_path)
                try:
                    extracted, skipped = extract_zip(zip_path, batch_dir)
                    for fpath, ext in extracted:
                        saved_paths.append(fpath)
                    all_skipped.extend(skipped)
                except Exception as e:
                    flash(f'Failed to extract {filename}: {e}', 'error')
            elif allowed_file(basename):
                filepath = os.path.join(batch_dir, filename)
                f.save(filepath)
                saved_paths.append(filepath)

        if not saved_paths:
            if all_skipped:
                skip_types = set(ext for _, ext in all_skipped)
                flash(f'No supported files found. Skipped {len(all_skipped)} file(s) of type: {", ".join(skip_types)}.', 'error')
            else:
                flash('No supported files found in the selected folder.', 'error')
            return redirect(request.url)

        if all_skipped:
            skip_types = set(ext for _, ext in all_skipped)
            flash(f'Found {len(saved_paths)} documents. Skipped {len(all_skipped)} unsupported file(s) ({", ".join(skip_types)}).', 'info')

        pdf_count = len(saved_paths)

        # Check volume limit
        store = get_config_store()
        tracker = get_usage_tracker()
        try:
            org = store.get_org(org_id)
            if org:
                allowed, current, limit, msg = tracker.check_volume_limit(
                    org_id, org.features.max_documents_per_month)
                remaining = limit - current
                if not allowed:
                    flash(msg, 'error')
                    return redirect(request.url)
                if pdf_count > remaining:
                    flash(f'This batch has {pdf_count} files but you only have {remaining} documents remaining this month. Processing will stop at the limit.', 'error')
        finally:
            store.close()
            tracker.close()

        doc_type = request.form.get('doc_type') or None
        property_name = request.form.get('property_name') or None

        _cleanup_expired_jobs()
        job_id = str(uuid.uuid4())[:8]
        jobs[job_id] = {
            'org_id': org_id,
            'status': 'processing',
            'type': 'batch',
            'folder': f'Uploaded batch ({pdf_count} files)',
            'progress': 0,
            'total': pdf_count,
            'results': [],
            'started': datetime.now().isoformat(),
            'step': 'ingesting',
            'step_detail': 'Starting batch...',
            'steps_log': [{'step': 'ingesting', 'detail': 'Starting batch...', 'time': datetime.now().isoformat()}],
        }

        def on_step(step, detail=''):
            jobs[job_id]['step'] = step
            jobs[job_id]['step_detail'] = detail
            jobs[job_id]['steps_log'].append({
                'step': step, 'detail': detail,
                'time': datetime.now().isoformat()
            })

        def process_async():
            try:
                failed_count = 0
                completed_count = 0
                _lock = threading.Lock()

                def _on_result(result, failed):
                    nonlocal failed_count, completed_count
                    with _lock:
                        if failed:
                            failed_count += 1
                        completed_count += 1
                        jobs[job_id]['progress'] = completed_count
                        jobs[job_id]['results'].append(_result_to_dict(result))

                    # Log each document
                    t = get_usage_tracker()
                    try:
                        t.log_document_processed(
                            org_id=org_id, user_id=user_id,
                            filename=result.filename,
                            document_type=result.document_type or 'unknown',
                            page_count=result.page_count,
                            processing_time=result.processing_time,
                            terms_count=0,
                            clauses_count=0,
                            tabular_rows=result.tables_stored,
                            success=result.success, error=result.error
                        )
                    finally:
                        t.close()

                on_step('ingesting', f'Processing {pdf_count} files with {_NUM_DOC_WORKERS} workers...')

                # Process documents in parallel using thread pool
                with ThreadPoolExecutor(max_workers=_NUM_DOC_WORKERS) as executor:
                    futures = {}
                    for filepath in saved_paths:
                        future = executor.submit(
                            _process_single_doc_thread,
                            org_id, user_id, filepath,
                            doc_type, property_name
                        )
                        futures[future] = os.path.basename(filepath)

                    for future in as_completed(futures):
                        fname = futures[future]
                        result, was_error = future.result()
                        _on_result(result, was_error)
                        on_step('ingesting',
                                f'Processed {fname} ({completed_count}/{pdf_count})')

                if failed_count > 0:
                    on_step('complete',
                            f'{pdf_count - failed_count} of {pdf_count} files processed '
                            f'({failed_count} failed)')
                else:
                    on_step('complete',
                            'All files processed' if pdf_count > 1 else 'File processed')
                jobs[job_id]['status'] = 'completed'
                jobs[job_id]['failed_count'] = failed_count
            except Exception as e:
                jobs[job_id]['status'] = 'failed'
                jobs[job_id]['error'] = str(e)

        enqueue_job(job_id, process_async)

        return redirect(url_for('job_status', job_id=job_id))

    # GET requests redirect to unified file extractor page
    return redirect(url_for('file_extractor'))


@app.route('/job/<job_id>')
@login_required
def job_status(job_id):
    # Periodically clean up expired jobs
    _cleanup_expired_jobs()
    job = jobs.get(job_id)
    if not job or job.get('org_id') != session.get('org_id'):
        flash('Job not found.', 'error')
        return redirect(url_for('index'))
    return render_template('job_status.html', job_id=job_id, job=job)


@app.route('/documents')
@login_required
def documents():
    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        doc_type = request.args.get('type')
        property_name = request.args.get('property')
        docs = db.list_documents(document_type=doc_type, property_name=property_name)
    finally:
        db.close()
    return render_template('documents.html', documents=docs,
                           templates=list_templates(),
                           filter_type=doc_type,
                           filter_property=property_name)


@app.route('/document/<int:doc_id>')
@login_required
def document_detail(doc_id):
    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        doc = db.get_document(doc_id)
        if not doc:
            flash('Document not found.', 'error')
            return redirect(url_for('documents'))
        terms = db.get_financial_terms(document_id=doc_id)
        clauses = db.get_clauses(document_id=doc_id)
        rent_roll = db.get_rent_roll(document_id=doc_id)
        opstat = db.get_operating_statement(document_id=doc_id)
        gl = db.get_gl_entries(document_id=doc_id)
        sync_history = (db.get_sync_history(doc_id)
                        if doc.get('origin_device_id') else [])
    finally:
        db.close()
    return render_template('document_detail.html',
                           doc=doc, terms=terms, clauses=clauses,
                           rent_roll=rent_roll, opstat=opstat, gl=gl,
                           sync_history=sync_history)


@app.route('/search')
@login_required
def search():
    query = request.args.get('q', '')
    field = request.args.get('field', 'all')
    property_id = request.args.get('property_id', type=int)
    portfolio_id = request.args.get('portfolio_id', type=int)
    document_type = request.args.get('doc_type', '')

    results = {}
    total_count = 0
    properties_list = []
    portfolios_list = []
    stats = {}

    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        properties_list = db.list_properties()
        portfolios_list = db.list_portfolios()
        stats = db.get_search_stats()

        if query:
            results = db.search_advanced(
                query=query,
                field=field,
                property_id=property_id,
                portfolio_id=portfolio_id,
                document_type=document_type or None,
            )
            total_count = sum(len(v) for v in results.values())
    finally:
        db.close()

    return render_template('search.html',
                           query=query, field=field, results=results,
                           total_count=total_count,
                           properties=properties_list,
                           portfolios=portfolios_list,
                           stats=stats,
                           filter_property_id=property_id,
                           filter_portfolio_id=portfolio_id,
                           filter_doc_type=document_type)


# ─── Routes: Data Export ─────────────────────────────────────────────

@app.route('/export/<export_type>')
@login_required
def export_data(export_type):
    """Export data as CSV or Excel."""
    from .exports import EXPORT_TYPES, export_csv_bytes, export_excel, HAS_OPENPYXL

    if export_type not in EXPORT_TYPES:
        flash('Unknown export type.', 'error')
        return redirect(url_for('index'))

    # Check feature flag
    user = get_current_user()
    if user:
        from .config import ConfigStore
        cfg = ConfigStore()
        cfg.connect()
        try:
            org = cfg.get_org(session['org_id'])
            if org and not org.features.csv_export_enabled:
                flash('CSV/Excel export is not available on your current plan.', 'error')
                return redirect(url_for('index'))
        finally:
            cfg.close()

    fmt = request.args.get('format', 'csv')  # csv or xlsx
    export_def = EXPORT_TYPES[export_type]

    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        method = getattr(db, export_def['db_method'])
        rows = method()
    finally:
        db.close()

    timestamp = datetime.now().strftime('%Y%m%d')
    base_name = f'capactive_{export_type}_{timestamp}'

    if fmt == 'xlsx' and HAS_OPENPYXL:
        data = export_excel(rows, export_def['columns'],
                           sheet_name=export_def['label'],
                           title=f'Capactive — {export_def["label"]}')
        return Response(
            data,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={'Content-Disposition': f'attachment; filename="{base_name}.xlsx"'}
        )
    else:
        data = export_csv_bytes(rows, export_def['columns'])
        return Response(
            data,
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename="{base_name}.csv"'}
        )


@app.route('/export/property/<int:property_id>')
@login_required
def export_property(property_id):
    """Export all data for a specific property as a multi-sheet Excel workbook."""
    from .exports import (export_property_workbook, export_csv_bytes,
                         RENT_ROLL_COLUMNS, HAS_OPENPYXL)

    # Check feature flag
    user = get_current_user()
    if user:
        from .config import ConfigStore
        cfg = ConfigStore()
        cfg.connect()
        try:
            org = cfg.get_org(session['org_id'])
            if org and not org.features.csv_export_enabled:
                flash('CSV/Excel export is not available on your current plan.', 'error')
                return redirect(url_for('index'))
        finally:
            cfg.close()

    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        prop = db.get_property(property_id)
        if not prop:
            flash('Property not found.', 'error')
            return redirect(url_for('properties'))

        # Gather all data linked to this property
        docs = db.get_property_documents(property_id)
        doc_ids = [d['id'] for d in docs]

        rent_roll = []
        operating_statement = []
        financial_terms = []
        gl_entries = []
        clauses = []

        for doc_id in doc_ids:
            rent_roll.extend(db.get_rent_roll(document_id=doc_id))
            operating_statement.extend(db.get_operating_statement(document_id=doc_id))
            financial_terms.extend(db.get_financial_terms(document_id=doc_id))
            gl_entries.extend(db.get_gl_entries(document_id=doc_id))
            clauses.extend(db.get_clauses(document_id=doc_id))

        data = {
            'rent_roll': rent_roll,
            'operating_statement': operating_statement,
            'financial_terms': financial_terms,
            'gl_entries': gl_entries,
            'clauses': clauses,
        }
    finally:
        db.close()

    timestamp = datetime.now().strftime('%Y%m%d')
    safe_name = re.sub(r'[^\w\s-]', '', prop['name']).strip().replace(' ', '_')
    base_name = f'capactive_{safe_name}_{timestamp}'

    if HAS_OPENPYXL:
        workbook_bytes = export_property_workbook(prop['name'], data)
        return Response(
            workbook_bytes,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={'Content-Disposition': f'attachment; filename="{base_name}.xlsx"'}
        )
    else:
        # Fallback: export rent roll as CSV
        csv_data = export_csv_bytes(rent_roll, RENT_ROLL_COLUMNS)
        return Response(
            csv_data,
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename="{base_name}_rent_roll.csv"'}
        )


# ─── Routes: Admin Panel ────────────────────────────────────────────

@app.route('/admin')
@admin_required
def admin_dashboard():
    """Admin dashboard with org overview."""
    org_id = session['org_id']

    store = get_config_store()
    tracker = get_usage_tracker()
    try:
        org = store.get_org(org_id)
        users = store.list_users(org_id, active_only=False)
        usage = tracker.get_monthly_usage(org_id)
        usage_history = tracker.get_usage_history(org_id, months=6)
        recent_activity = tracker.get_user_activity(org_id, days=7, limit=20)
    finally:
        store.close()
        tracker.close()

    return render_template('admin.html',
                           org=org, users=users, usage=usage,
                           usage_history=usage_history,
                           recent_activity=recent_activity,
                           plan_features=PLAN_FEATURES)


def _org_seat_state(org_id):
    """Per-user role templates, current seat usage by class, and plan
    limits (docs/PACKAGING_DESIGN.md §5 — role-derived seat classes,
    first admin free)."""
    store = get_config_store()
    pstore = get_permission_store()
    try:
        org = store.get_org(org_id)
        users = store.list_users(org_id)  # active only
        perms = {p['user_id']: p for p in pstore.list_org_permissions(org_id)}
        roles = {u['user_id']:
                 (perms.get(u['user_id']) or {}).get('role_template', 'viewer')
                 for u in users}
        limits = {'extraction': org.features.max_extraction_seats,
                  'access': org.features.max_access_seats}
        return roles, count_seats(list(roles.values())), limits
    finally:
        store.close()
        pstore.close()


def _seat_block_reason(org_id, new_role, exclude_user=None):
    """None if the org can accommodate `new_role`, else a human message.
    `exclude_user` = user whose current seat frees up (role changes)."""
    roles, _used, limits = _org_seat_state(org_id)
    if exclude_user is not None:
        roles.pop(exclude_user, None)
    after = count_seats(list(roles.values()) + [new_role])
    cls = seat_class_for_role(new_role)
    if after[cls] > limits[cls]:
        return (f"No {SEAT_CLASS_LABELS[cls].lower()}s left "
                f"({after[cls] - 1}/{limits[cls]} in use). "
                f"Upgrade the plan or free a seat.")
    return None


@app.route('/admin/users', methods=['GET', 'POST'])
@admin_required
def admin_users():
    """Manage users."""
    org_id = session['org_id']

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'add':
            name = request.form.get('display_name', '').strip()
            email = request.form.get('email', '').strip()
            role = request.form.get('role', 'member')

            if not name or not email:
                flash('Name and email are required.', 'error')
                return redirect(request.url)

            blocked = _seat_block_reason(org_id, role)
            if blocked:
                flash(blocked, 'error')
                return redirect(request.url)

            user_id = email.split('@')[0].lower().replace('.', '-')

            store = get_config_store()
            try:
                user = store.create_user(org_id, user_id, email, name, role=role)
                user_key = generate_user_key(org_id, user_id)

                # Initialize permissions based on role
                pstore = get_permission_store()
                try:
                    pstore.init_user_permissions(user_id, org_id, role=role)
                finally:
                    pstore.close()

                flash(f'User {name} created. Their key: {user_key}', 'success')
            except ValueError as e:
                flash(str(e), 'error')
            finally:
                store.close()

        elif action == 'deactivate':
            target_user_id = request.form.get('user_id')
            if target_user_id == session['user_id']:
                flash("You can't deactivate yourself.", 'error')
            else:
                store = get_config_store()
                try:
                    store.deactivate_user(target_user_id)
                    flash('User deactivated.', 'success')
                finally:
                    store.close()

        return redirect(request.url)

    store = get_config_store()
    try:
        org = store.get_org(org_id)
        users = store.list_users(org_id, active_only=False)
    finally:
        store.close()

    # Generate keys for display
    for u in users:
        u['user_key'] = generate_user_key(org_id, u['user_id'])

    return render_template('admin_users.html', org=org, users=users)


@app.route('/api/sync/ping', methods=['GET', 'POST'])
@device_required
def api_sync_ping():
    """Extraction-client handshake: proves the token works, pins the
    fingerprint on first contact, reports what the server sees."""
    return jsonify({
        'ok': True,
        'device_id': g.device['device_id'],
        'device_name': g.device['device_name'],
        'org_id': g.device_org_id,
        'fingerprint_pinned': g.device['fingerprint'] is not None,
        'server_time': datetime.now().isoformat(),
    })


@app.route('/api/sync/manifest')
@device_required
def api_sync_manifest():
    """What the instance already holds from this device — the client
    diffs its finalized documents against this and pushes only deltas."""
    db = get_org_db(g.device_org_id)
    try:
        return jsonify({'ok': True,
                        'documents': db.sync_manifest(g.device['device_id'])})
    finally:
        db.close()


@app.route('/api/sync/run', methods=['POST'])
@device_required
def api_sync_run():
    """Receive finalized documents from an extraction device.

    Body: {"documents": [{"document": {...}, "terms": [...]}, ...]}
    Each document upserts keyed on (device, origin_doc_id); re-pushes
    snapshot the prior version into sync_versions (history kept).
    """
    data = request.get_json(silent=True) or {}
    items = data.get('documents', [])
    if not items:
        return jsonify({'error': 'no documents in payload'}), 400
    db = get_org_db(g.device_org_id)
    results, errors = [], []
    try:
        for item in items:
            try:
                results.append(db.upsert_synced_document(
                    item.get('document') or {},
                    item.get('terms') or [],
                    g.device['device_id']))
            except Exception as e:
                errors.append({'origin_doc_id':
                               (item.get('document') or {}).get('origin_doc_id'),
                               'error': str(e)})
        app.logger.info(f"sync/run from {g.device['device_id']}: "
                        f"{len(results)} ok, {len(errors)} failed")
        return jsonify({'ok': not errors, 'results': results,
                        'errors': errors})
    finally:
        db.close()


@app.route('/api/sync/pdf', methods=['POST'])
@device_required
def api_sync_pdf():
    """Receive a source PDF for a previously pushed document.

    Query args: origin_doc_id, sha256. Body: raw PDF bytes.
    Content-addressed storage — identical files dedupe by hash; the
    document's filepath is pointed at the stored file either way.
    """
    import hashlib as _hashlib
    origin_doc_id = request.args.get('origin_doc_id')
    claimed = (request.args.get('sha256') or '').lower()
    if not origin_doc_id or not claimed:
        return jsonify({'error': 'origin_doc_id and sha256 required'}), 400
    blob = request.get_data()
    if not blob:
        return jsonify({'error': 'empty body'}), 400
    actual = _hashlib.sha256(blob).hexdigest()
    if actual != claimed:
        return jsonify({'error': 'sha256 mismatch — transfer corrupted'}), 400

    store_dir = os.path.join(DATA_DIR, 'synced_pdfs', g.device_org_id)
    os.makedirs(store_dir, exist_ok=True)
    fp = os.path.join(store_dir, f'{actual}.pdf')
    deduped = os.path.exists(fp)
    if not deduped:
        with open(fp, 'wb') as f:
            f.write(blob)

    db = get_org_db(g.device_org_id)
    try:
        matched = db.attach_synced_pdf(g.device['device_id'],
                                       origin_doc_id, fp)
    finally:
        db.close()
    if not matched:
        return jsonify({'error': f'no synced document with origin_doc_id '
                                 f'{origin_doc_id} — push /api/sync/run '
                                 f'first'}), 404
    return jsonify({'ok': True, 'sha256': actual, 'deduped': deduped,
                    'bytes': len(blob)})


@app.route('/admin/devices', methods=['GET', 'POST'])
@admin_required
def admin_devices():
    """Register and revoke extraction devices (extraction seats)."""
    org_id = session['org_id']
    new_token = None
    new_device = None

    if request.method == 'POST':
        action = request.form.get('action')
        store = get_config_store()
        try:
            if action == 'register':
                name = request.form.get('device_name', '').strip()
                if not name:
                    flash('Device name is required.', 'error')
                    return redirect(request.url)
                try:
                    reg = store.register_device(
                        org_id, name, created_by=session.get('user_id'))
                    new_token = reg['token']
                    new_device = name
                    flash('Device registered. Copy the token now — it will '
                          'not be shown again.', 'success')
                except ValueError as e:
                    flash(str(e), 'error')
                    return redirect(request.url)
            elif action == 'revoke':
                store.revoke_device(request.form.get('device_id'))
                flash('Device revoked. Its token no longer authenticates.',
                      'success')
                return redirect(request.url)
        finally:
            store.close()

    store = get_config_store()
    try:
        org = store.get_org(org_id)
        devices = store.list_devices(org_id)
    finally:
        store.close()
    active = sum(1 for d in devices if d['is_active'])
    return render_template('admin_devices.html', org=org, devices=devices,
                           active_count=active,
                           seat_limit=org.features.max_extraction_seats,
                           new_token=new_token, new_device=new_device)


@app.route('/admin/license')
@admin_required
def admin_license():
    """View license and plan details."""
    org_id = session['org_id']

    store = get_config_store()
    tracker = get_usage_tracker()
    try:
        org = store.get_org(org_id)
        usage = tracker.get_monthly_usage(org_id)
        users = store.list_users(org_id)
    finally:
        store.close()
        tracker.close()

    _roles, seat_usage, seat_limits = _org_seat_state(org_id)
    return render_template('admin_license.html',
                           org=org, usage=usage, user_count=len(users),
                           seat_usage=seat_usage, seat_limits=seat_limits,
                           plan_features=PLAN_FEATURES)


@app.route('/admin/modules', methods=['GET', 'POST'])
@admin_required
def admin_modules():
    """Per-org module activation (tier overrides)."""
    from .modules.gating import MODULE_GROUPS, MODULE_ROUTES
    from .modules import registry as module_registry
    org_id = session['org_id']

    store = get_config_store()
    org = store.get_org(org_id)
    features = org.features

    if request.method == 'POST':
        if request.form.get('mode') == 'all':
            features.modules_enabled = ['*']
        else:
            selected = request.form.getlist('modules')
            # only accept known module names
            valid = set(MODULE_ROUTES.keys())
            features.modules_enabled = [m for m in selected if m in valid]
        store.set_org_features(org_id, features)
        flash('Module access updated.', 'success')
        return redirect(url_for('admin_modules'))

    # module display metadata from the registry
    if not module_registry._loaded:
        module_registry.discover()
    meta = {}
    for name, inst in module_registry._modules.items():
        meta[name] = {
            'display_name': inst.display_name,
            'description': inst.description,
        }

    mods = list(features.modules_enabled or [])
    all_enabled = '*' in mods
    enabled_set = set(mods)

    groups = []
    for label, names in MODULE_GROUPS:
        entries = []
        for n in names:
            m = meta.get(n, {})
            entries.append({
                'name': n,
                'display_name': m.get('display_name', n.replace('_', ' ').title()),
                'description': m.get('description', ''),
                'enabled': all_enabled or n in enabled_set,
            })
        groups.append({'label': label, 'entries': entries})

    return render_template('admin_modules.html',
                           org=org, groups=groups, all_enabled=all_enabled,
                           plan=org.plan)


@app.route('/admin/permissions', methods=['GET', 'POST'])
@admin_required
def admin_permissions():
    """Manage user permissions."""
    org_id = session['org_id']

    if request.method == 'POST':
        target_user = request.form.get('user_id')
        action = request.form.get('action')

        pstore = get_permission_store()
        try:
            if action == 'set_role':
                role = request.form.get('role_template')
                blocked = _seat_block_reason(org_id, role,
                                             exclude_user=target_user)
                if blocked:
                    flash(blocked, 'error')
                    return redirect(url_for('admin_permissions'))
                pstore.set_user_role(target_user, org_id, role)
                flash(f'Role updated to {role}.', 'success')

            elif action == 'set_overrides':
                overrides = {}
                for scope in SCOPE_ORDER:
                    level = request.form.get(f'perm_{scope}')
                    if level and level in LEVELS:
                        overrides[scope] = level
                pstore.set_bulk_overrides(target_user, org_id, overrides)
                flash('Permission overrides saved.', 'success')
        finally:
            pstore.close()

        return redirect(url_for('admin_permissions'))

    # GET — show permissions grid
    store = get_config_store()
    pstore = get_permission_store()
    try:
        users = store.list_users(org_id)
        org_perms = pstore.list_org_permissions(org_id)

        # Build lookup by user_id
        perms_by_user = {p['user_id']: p for p in org_perms}

        # Enrich users with permissions
        for u in users:
            uid = u['user_id']
            if uid in perms_by_user:
                u['role_template'] = perms_by_user[uid]['role_template']
                u['permissions'] = perms_by_user[uid]['permissions']
                u['overrides'] = perms_by_user[uid]['overrides']
            else:
                u['role_template'] = 'viewer'
                u['permissions'] = ROLE_TEMPLATES['viewer']['permissions']
                u['overrides'] = {}
    finally:
        store.close()
        pstore.close()

    return render_template('admin_permissions.html',
                           users=users,
                           role_templates=ROLE_TEMPLATES,
                           scopes=SCOPES,
                           scope_order=SCOPE_ORDER,
                           scope_categories=get_scope_categories(),
                           levels=LEVELS)


@app.route('/admin/audit')
@admin_required
def admin_audit():
    """Audit trail viewer."""
    org_id = session['org_id']

    tracker = get_usage_tracker()
    try:
        days = int(request.args.get('days', 30))
        user_filter = request.args.get('user')
        activity = tracker.get_user_activity(
            org_id, user_id=user_filter, days=days, limit=200)
    finally:
        tracker.close()

    # Get user list for filter dropdown
    store = get_config_store()
    try:
        users = store.list_users(org_id)
    finally:
        store.close()

    return render_template('admin_audit.html',
                           activity=activity, users=users,
                           days=days, user_filter=user_filter)


@app.route('/analytics/corrections')
@login_required
def corrections_analytics():
    """Corrections analytics page — identify extraction quality patterns."""
    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        analytics = db.get_corrections_analytics()
    finally:
        db.close()

    return render_template('corrections_analytics.html', analytics=analytics)


# ─── Routes: Document Review Queue ───────────────────────────────────

@app.route('/review')
@login_required
@permission_required('extraction.review', 'read')
def review_queue():
    """Document review queue — match documents to properties."""
    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        queue = db.get_review_queue()
        review_count = db.get_review_count()
        property_list = db.list_properties()
        portfolio_list = db.list_portfolios()
    finally:
        db.close()
    return render_template('review.html',
                           queue=queue, review_count=review_count,
                           properties=property_list,
                           portfolios=portfolio_list)


@app.route('/review/bulk-approve', methods=['POST'])
@login_required
@permission_required('extraction.review', 'edit')
def review_bulk_approve():
    """Approve all high-confidence property matches at once."""
    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        count = db.bulk_approve_matches(min_score=0.7)
        flash(f'{count} document{"s" if count != 1 else ""} approved with high-confidence matches.', 'success')
    finally:
        db.close()
    return redirect(url_for('review_queue'))


@app.route('/review/<int:doc_id>/approve', methods=['POST'])
@login_required
@permission_required('extraction.review', 'edit')
def review_approve(doc_id):
    """Approve a suggested property match."""
    org_id = session['org_id']
    property_id = request.form.get('property_id')
    if not property_id:
        flash('No property selected.', 'error')
        return redirect(url_for('review_queue'))

    db = get_org_db(org_id)
    try:
        building_id = int(request.form['building_id']) if request.form.get('building_id') else None
        unit_id = int(request.form['unit_id']) if request.form.get('unit_id') else None
        db.approve_document_match(doc_id, int(property_id), building_id, unit_id)

        doc = db.get_document(doc_id)
        prop = db.get_property(int(property_id))
        flash(f'"{doc["filename"]}" linked to {prop["name"]}.', 'success')
    finally:
        db.close()
    return redirect(url_for('review_queue'))


@app.route('/review/<int:doc_id>/create-property', methods=['POST'])
@login_required
@permission_required('extraction.review', 'edit')
def review_create_property(doc_id):
    """Create a new property from extracted data and link the document."""
    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        name = request.form.get('name', '').strip()
        if not name:
            flash('Property name is required.', 'error')
            return redirect(url_for('review_queue'))

        prop_id = db.create_property(
            name=name,
            property_type=request.form.get('property_type', 'multifamily'),
            portfolio_id=int(request.form['portfolio_id']) if request.form.get('portfolio_id') else None,
            address=request.form.get('address') or None,
            city=request.form.get('city') or None,
            state=request.form.get('state') or None,
            zip_code=request.form.get('zip_code') or None,
        )
        db.approve_document_match(doc_id, prop_id)

        flash(f'Property "{name}" created and document linked.', 'success')
    finally:
        db.close()
    return redirect(url_for('review_queue'))


@app.route('/review/<int:doc_id>/skip', methods=['POST'])
@login_required
@permission_required('extraction.review', 'edit')
def review_skip(doc_id):
    """Skip this document for now."""
    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        db.skip_document_review(doc_id)
        flash('Document skipped. You can review it later.', 'success')
    finally:
        db.close()
    return redirect(url_for('review_queue'))


# ─── Routes: Property Intelligence (Layer 2) ────────────────────────

@app.route('/portfolios')
@login_required
def portfolios():
    """Portfolio and property management."""
    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        portfolio_list = db.list_portfolios()
        # Enrich with property counts
        for pf in portfolio_list:
            props = db.list_properties(portfolio_id=pf['id'])
            pf['property_count'] = len(props)
            pf['total_units'] = sum(p.get('unit_count', 0) for p in props)
        # Also get unlinked properties
        all_props = db.list_properties()
        unlinked = [p for p in all_props if not p.get('portfolio_id')]
    finally:
        db.close()
    return render_template('portfolios.html',
                           portfolios=portfolio_list,
                           unlinked_properties=unlinked)


@app.route('/portfolio/comparison')
@app.route('/portfolio/<int:portfolio_id>/comparison')
@login_required
def portfolio_comparison(portfolio_id=None):
    """Cross-property financial comparison view."""
    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        if portfolio_id:
            portfolio = db.get_portfolio(portfolio_id)
            if not portfolio:
                flash('Portfolio not found.', 'error')
                return redirect(url_for('portfolios'))
            prop_list = db.list_properties(portfolio_id=portfolio_id)
            view_title = portfolio['name']
        else:
            portfolio = None
            prop_list = db.list_properties()
            view_title = 'All Properties'

        # Run synthesis for each property that has operating data
        synthesizer = FinancialSynthesizer(db)
        comparisons = []
        all_periods = set()

        for prop in prop_list:
            count = db.conn.execute("""
                SELECT COUNT(*) FROM operating_statement_items os
                JOIN documents d ON os.document_id = d.id
                WHERE d.property_id = ?
            """, (prop['id'],)).fetchone()[0]
            if count == 0:
                continue
            synth = synthesizer.synthesize(prop['id'])
            if synth and synth.get('periods'):
                comparisons.append({
                    'property': prop,
                    'synthesis': synth,
                })
                all_periods.update(synth['periods'])

        # Sort periods chronologically
        def period_sort_key(p):
            year = ''.join(c for c in p if c.isdigit())[:4]
            suffix = p[4:] if len(p) > 4 else ''
            suffix_order = {'A': 0, 'F': 1, 'P': 2, 'B': 3}
            return (year, suffix_order.get(suffix, 9))

        sorted_periods = sorted(all_periods, key=period_sort_key)

        # Build display periods: newest first, split actuals vs budgets
        reversed_periods = list(reversed(sorted_periods))
        actuals = [p for p in reversed_periods if p.endswith('A')]
        budgets = [p for p in reversed_periods if p.endswith('B')]
        # Default view: most recent budget + last 3 actuals
        display_periods = budgets[:1] + actuals[:3]
        # Remaining older periods available via toggle
        extra_periods = [p for p in reversed_periods if p not in display_periods]

        portfolio_list = db.list_portfolios()
    finally:
        db.close()

    return render_template('portfolio_comparison.html',
                           portfolio=portfolio,
                           view_title=view_title,
                           comparisons=comparisons,
                           periods=sorted_periods,
                           display_periods=display_periods,
                           extra_periods=extra_periods,
                           portfolios=portfolio_list)


@app.route('/api/portfolio/comparison')
@app.route('/api/portfolio/<int:portfolio_id>/comparison')
@login_required
def api_portfolio_comparison(portfolio_id=None):
    """JSON API for cross-property financial comparison."""
    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        if portfolio_id:
            prop_list = db.list_properties(portfolio_id=portfolio_id)
        else:
            prop_list = db.list_properties()

        synthesizer = FinancialSynthesizer(db)
        results = {}
        for prop in prop_list:
            count = db.conn.execute("""
                SELECT COUNT(*) FROM operating_statement_items os
                JOIN documents d ON os.document_id = d.id
                WHERE d.property_id = ?
            """, (prop['id'],)).fetchone()[0]
            if count == 0:
                continue
            results[prop['name']] = synthesizer.synthesize(prop['id'])
    finally:
        db.close()
    return jsonify(results)


@app.route('/portfolios/create', methods=['POST'])
@login_required
@permission_required('property.operations', 'edit')
def create_portfolio():
    org_id = session['org_id']
    name = request.form.get('name', '').strip()
    description = request.form.get('description', '').strip()
    if not name:
        flash('Portfolio name is required.', 'error')
        return redirect(url_for('portfolios'))
    db = get_org_db(org_id)
    try:
        db.create_portfolio(name, description)
        flash(f'Portfolio "{name}" created.', 'success')
    finally:
        db.close()
    return redirect(url_for('portfolios'))


@app.route('/properties')
@login_required
def properties():
    """All properties list."""
    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        prop_type = request.args.get('type')
        status = request.args.get('status')
        property_list = db.list_properties(property_type=prop_type, status=status)
        portfolio_list = db.list_portfolios()
    finally:
        db.close()
    return render_template('properties.html',
                           properties=property_list,
                           portfolios=portfolio_list,
                           filter_type=prop_type,
                           filter_status=status)


@app.route('/properties/create', methods=['POST'])
@login_required
@permission_required('property.operations', 'edit')
def create_property():
    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        name = request.form.get('name', '').strip()
        if not name:
            flash('Property name is required.', 'error')
            return redirect(url_for('properties'))

        prop_id = db.create_property(
            name=name,
            property_type=request.form.get('property_type', 'multifamily'),
            portfolio_id=int(request.form['portfolio_id']) if request.form.get('portfolio_id') else None,
            address=request.form.get('address') or None,
            city=request.form.get('city') or None,
            state=request.form.get('state') or None,
            zip_code=request.form.get('zip_code') or None,
            year_built=int(request.form['year_built']) if request.form.get('year_built') else None,
            total_units=int(request.form['total_units']) if request.form.get('total_units') else None,
            total_sqft=float(request.form['total_sqft']) if request.form.get('total_sqft') else None,
            acquisition_price=float(request.form['acquisition_price']) if request.form.get('acquisition_price') else None,
        )
        flash(f'Property "{name}" created.', 'success')
        return redirect(url_for('property_detail', property_id=prop_id))
    finally:
        db.close()


@app.route('/properties/bulk-create', methods=['POST'])
@login_required
@permission_required('property.operations', 'edit')
def bulk_create_properties():
    """Create multiple properties from a CSV or line-separated list."""
    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        # Accept either a CSV file upload or a text field with names
        created = []
        skipped = []

        uploaded = request.files.get('csv_file')
        if uploaded and uploaded.filename:
            import csv
            import io
            content = uploaded.read().decode('utf-8-sig')
            reader = csv.DictReader(io.StringIO(content))
            for row in reader:
                name = (row.get('name') or row.get('Name') or
                        row.get('property_name') or row.get('Property Name') or '').strip()
                if not name:
                    continue
                # Check if property already exists
                existing = db._fuzzy_match_property(name)
                if existing:
                    skipped.append(f'{name} (matches "{existing["name"]}")')
                    continue
                prop_type = (row.get('type') or row.get('property_type') or 'multifamily').strip()
                address = (row.get('address') or row.get('Address') or '').strip() or None
                city = (row.get('city') or row.get('City') or '').strip() or None
                state = (row.get('state') or row.get('State') or '').strip() or None
                zip_code = (row.get('zip') or row.get('zip_code') or '').strip() or None
                units = None
                units_raw = (row.get('units') or row.get('total_units') or '').strip()
                if units_raw:
                    try:
                        units = int(units_raw)
                    except ValueError:
                        pass
                db.create_property(
                    name=name, property_type=prop_type,
                    address=address, city=city, state=state,
                    zip_code=zip_code, total_units=units)
                created.append(name)
        else:
            # Fallback: line-separated names from text field
            names_text = request.form.get('property_names', '')
            for line in names_text.strip().split('\n'):
                name = line.strip()
                if not name:
                    continue
                existing = db._fuzzy_match_property(name)
                if existing:
                    skipped.append(f'{name} (matches "{existing["name"]}")')
                    continue
                db.create_property(name=name)
                created.append(name)

        if created:
            flash(f'Created {len(created)} properties: {", ".join(created)}', 'success')
        if skipped:
            flash(f'Skipped {len(skipped)} (already exist): {", ".join(skipped)}', 'info')
        if not created and not skipped:
            flash('No property names found in input.', 'error')
    finally:
        db.close()

    return redirect(url_for('properties'))


@app.route('/property/<int:property_id>')
@login_required
def property_detail(property_id):
    """Property detail — the three-bucket view."""
    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        prop = db.get_property(property_id)
        if not prop:
            flash('Property not found.', 'error')
            return redirect(url_for('properties'))

        buildings = db.list_buildings(property_id)
        units = db.list_units(property_id=property_id)
        documents = db.get_property_documents(property_id)

        # Three buckets
        operations = db.get_property_operations_summary(property_id)
        debt = db.get_property_debt_summary(property_id)
        valuation = db.get_property_valuation_summary(property_id)

        # Analysis status
        latest_analysis = db.get_latest_analysis(property_id)

        # Count docs by analysis status
        ingested_count = sum(1 for d in documents
                             if d.get('analysis_status', 'ingested') == 'ingested')
        analyzed_count = sum(1 for d in documents
                             if d.get('analysis_status') == 'analyzed')

        # Financial synthesis (reconciled multi-source view)
        synthesizer = FinancialSynthesizer(db)
        synthesis = synthesizer.synthesize(property_id)
    finally:
        db.close()

    return render_template('property_detail.html',
                           prop=prop, buildings=buildings, units=units,
                           documents=documents,
                           operations=operations, debt=debt,
                           valuation=valuation,
                           latest_analysis=latest_analysis,
                           ingested_count=ingested_count,
                           analyzed_count=analyzed_count,
                           synthesis=synthesis)


@app.route('/property/<int:property_id>/building/create', methods=['POST'])
@login_required
@permission_required('property.units', 'edit')
def create_building(property_id):
    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        name = request.form.get('name', '').strip()
        if not name:
            flash('Building name is required.', 'error')
            return redirect(url_for('property_detail', property_id=property_id))
        db.create_building(
            property_id, name,
            floors=int(request.form['floors']) if request.form.get('floors') else None,
            total_units=int(request.form['total_units']) if request.form.get('total_units') else None,
            total_sqft=float(request.form['total_sqft']) if request.form.get('total_sqft') else None,
        )
        flash(f'Building "{name}" added.', 'success')
    finally:
        db.close()
    return redirect(url_for('property_detail', property_id=property_id))


@app.route('/property/<int:property_id>/unit/create', methods=['POST'])
@login_required
@permission_required('property.units', 'edit')
def create_unit(property_id):
    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        unit_number = request.form.get('unit_number', '').strip()
        building_id = request.form.get('building_id')
        if not unit_number or not building_id:
            flash('Unit number and building are required.', 'error')
            return redirect(url_for('property_detail', property_id=property_id))
        db.create_unit(
            building_id=int(building_id),
            property_id=property_id,
            unit_number=unit_number,
            unit_type=request.form.get('unit_type') or None,
            square_footage=float(request.form['square_footage']) if request.form.get('square_footage') else None,
            bedrooms=float(request.form['bedrooms']) if request.form.get('bedrooms') else None,
            bathrooms=float(request.form['bathrooms']) if request.form.get('bathrooms') else None,
            market_rent=float(request.form['market_rent']) if request.form.get('market_rent') else None,
        )
        flash(f'Unit {unit_number} added.', 'success')
    finally:
        db.close()
    return redirect(url_for('property_detail', property_id=property_id))


@app.route('/property/<int:property_id>/link-document', methods=['POST'])
@login_required
@permission_required('property.documents', 'edit')
def link_document(property_id):
    """Link an existing document to this property."""
    org_id = session['org_id']
    doc_id = request.form.get('document_id')
    if not doc_id:
        flash('No document selected.', 'error')
        return redirect(url_for('property_detail', property_id=property_id))
    db = get_org_db(org_id)
    try:
        building_id = int(request.form['building_id']) if request.form.get('building_id') else None
        unit_id = int(request.form['unit_id']) if request.form.get('unit_id') else None
        db.link_document_to_property(int(doc_id), property_id, building_id, unit_id)
        flash('Document linked to property.', 'success')
    finally:
        db.close()
    return redirect(url_for('property_detail', property_id=property_id))


# ─── Extraction Review ──────────────────────────────────────────────

@app.route('/extraction-review')
@app.route('/extraction-review/<int:property_id>')
@login_required
def extraction_review(property_id=None):
    """Batch review UI for scanning extraction results across documents."""
    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        # Get filter params
        status_filter = request.args.get('status')  # pending, approved, flagged
        type_filter = request.args.get('type')       # document type filter

        # Get review queue (single query)
        all_docs = db.get_extraction_review_queue(
            property_id=property_id,
            status=status_filter
        )

        # Build type list from full result, then filter
        doc_types = sorted(set(
            d.get('document_type', 'unknown') for d in all_docs
        ))
        if type_filter:
            docs = [d for d in all_docs if d.get('document_type') == type_filter]
        else:
            docs = all_docs

        # Get review stats
        stats = db.get_extraction_review_stats(property_id=property_id)

        # Get property list for filter dropdown
        all_props = db.list_properties()

        # Current property info
        prop = db.get_property(property_id) if property_id else None
    finally:
        db.close()

    return render_template('extraction_review.html',
                           docs=docs,
                           stats=stats,
                           properties=all_props,
                           current_property=prop,
                           property_id=property_id,
                           status_filter=status_filter,
                           type_filter=type_filter,
                           doc_types=doc_types)


@app.route('/api/extraction-review/<int:doc_id>', methods=['POST'])
@login_required
def api_set_extraction_review(doc_id):
    """Set extraction review status for a document."""
    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        data = request.get_json()
        status = data.get('status')  # approved, flagged, pending
        notes = data.get('notes', '')

        if status not in ('approved', 'flagged', 'pending'):
            return jsonify({'error': 'Invalid status'}), 400

        db.set_extraction_review(doc_id, status, notes or None)

        return jsonify({'ok': True, 'status': status})
    finally:
        db.close()


@app.route('/api/extraction-review/bulk', methods=['POST'])
@login_required
def api_bulk_extraction_review():
    """Set extraction review status for multiple documents."""
    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        data = request.get_json()
        doc_ids = data.get('doc_ids', [])
        status = data.get('status')
        notes = data.get('notes', '')

        if status not in ('approved', 'flagged', 'pending'):
            return jsonify({'error': 'Invalid status'}), 400

        for doc_id in doc_ids:
            db.set_extraction_review(int(doc_id), status, notes or None)

        return jsonify({'ok': True, 'count': len(doc_ids)})
    finally:
        db.close()


@app.route('/api/extraction-review/bulk-by-filter', methods=['POST'])
@login_required
def api_bulk_extraction_review_by_filter():
    """Bulk-update extraction review status using filters.

    Body JSON:
      status       — target status (approved, flagged, pending)
      from_status  — only update docs currently in this status (optional)
      property_id  — restrict to this property (optional)
      doc_type     — restrict to this document type (optional)
      notes        — review notes (optional)
    """
    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        data = request.get_json()
        status = data.get('status')
        from_status = data.get('from_status')
        property_id = data.get('property_id')
        doc_type = data.get('doc_type')
        notes = data.get('notes', '')

        if status not in ('approved', 'flagged', 'pending'):
            return jsonify({'error': 'Invalid status'}), 400

        count = db.bulk_set_extraction_review(
            new_status=status,
            notes=notes or None,
            current_status=from_status,
            property_id=int(property_id) if property_id else None,
            document_type=doc_type,
        )
        return jsonify({'ok': True, 'count': count})
    finally:
        db.close()


# ─── API Routes ──────────────────────────────────────────────────────

@app.route('/api/jobs/active')
@login_required
def api_active_jobs():
    """Return list of currently processing or queued jobs for this org."""
    org_id = session.get('org_id')
    active = []
    # Recalculate queue positions for queued jobs
    queue_pos = 1
    for jid, job in jobs.items():
        if job.get('org_id') != org_id:
            continue
        if job.get('status') in ('processing', 'queued'):
            if job['status'] == 'queued':
                job['queue_position'] = queue_pos
                queue_pos += 1
            active.append({
                'id': jid,
                'type': job.get('type', 'single'),
                'filename': job.get('filename', ''),
                'status': job['status'],
                'progress': job.get('progress', 0),
                'total': job.get('total', 1),
                'step': job.get('step', ''),
                'step_detail': job.get('step_detail', ''),
                'started': job.get('started', ''),
                'queue_position': job.get('queue_position', 0),
            })
    return jsonify(active)


@app.route('/api/job/<job_id>')
@login_required
def api_job_status(job_id):
    job = jobs.get(job_id)
    if not job or job.get('org_id') != session.get('org_id'):
        return jsonify({'error': 'Job not found'}), 404
    # Don't leak org_id to the client
    safe_job = {k: v for k, v in job.items() if k != 'org_id'}
    return jsonify(safe_job)


@app.route('/api/term/<int:term_id>', methods=['PUT'])
@login_required
def api_update_term(term_id):
    """Update a financial term with user correction."""
    data = request.get_json()
    if not data or 'value' not in data:
        return jsonify({'error': 'Missing value'}), 400

    user_value = data['value'].strip()
    if not user_value:
        return jsonify({'error': 'Value cannot be empty'}), 400

    # Try to parse a numeric value
    user_numeric = None
    try:
        cleaned = user_value.replace(',', '').replace('$', '').replace('%', '').strip()
        user_numeric = float(cleaned)
    except (ValueError, TypeError):
        pass

    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        db.update_financial_term(term_id, user_value, user_numeric)
    finally:
        db.close()

    return jsonify({'success': True, 'value': user_value, 'numeric': user_numeric})


@app.route('/api/document/<int:doc_id>/reextract', methods=['POST'])
@login_required
def api_reextract(doc_id):
    """Delete a document's extracted data and reprocess it."""
    org_id = session['org_id']
    user_id = session['user_id']
    db = get_org_db(org_id)
    try:
        info = db.delete_document_extractions(doc_id)
        if not info:
            return jsonify({'error': 'Document not found'}), 404

        filepath = info['filepath']
        if not filepath or not os.path.exists(filepath):
            return jsonify({'error': 'Original PDF file not found on disk'}), 404

        # Create a job and reprocess in background
        filename = os.path.basename(filepath)
        _cleanup_expired_jobs()
        job_id = str(uuid.uuid4())[:8]
        jobs[job_id] = {
            'id': job_id,
            'org_id': org_id,
            'status': 'processing',
            'type': 'single',
            'filename': filename,
            'total': 1,
            'progress': 0,
            'results': [],
            'error': None,
            'started': datetime.now().isoformat(),
            'step': 'ingesting',
            'step_detail': f'Reading {filename}...',
            'steps_log': [{'step': 'ingesting', 'detail': f'Reading {filename}...', 'time': datetime.now().isoformat()}],
        }

        def on_step(step, detail=''):
            jobs[job_id]['step'] = step
            jobs[job_id]['step_detail'] = detail
            jobs[job_id]['steps_log'].append({
                'step': step, 'detail': detail,
                'time': datetime.now().isoformat()
            })

        def process_async():
            db2 = None
            try:
                db2 = get_org_db(org_id)
                llm = get_llm()
                processor = BatchProcessor(db2, llm)
                processor._on_step = on_step
                result = processor.process_single(
                    filepath,
                    document_type=None,  # let classifier re-detect
                    property_name=info.get('property_name')
                )
                on_step('complete', 'Re-ingested')
                jobs[job_id]['results'] = [_result_to_dict(result)]
                jobs[job_id]['progress'] = 1
                jobs[job_id]['status'] = 'completed' if result.success else 'failed'
                jobs[job_id]['error'] = result.error
            except Exception as e:
                jobs[job_id]['status'] = 'failed'
                jobs[job_id]['error'] = str(e)
            finally:
                if db2 is not None:
                    db2.close()

        enqueue_job(job_id, process_async)

        return jsonify({'success': True, 'job_id': job_id})
    finally:
        db.close()


# ─── Extraction Versioning & Comparison ──────────────────────────────

@app.route('/api/property/<int:property_id>/reanalyze', methods=['POST'])
@login_required
def api_versioned_reanalyze(property_id):
    """Re-analyze all documents for a property with extraction versioning.
    Creates new extraction runs so results can be compared against previous runs."""
    org_id = session['org_id']

    _cleanup_expired_jobs()
    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {
        'id': job_id,
        'org_id': org_id,
        'status': 'processing',
        'type': 'versioned_reanalyze',
        'filename': f'Versioned re-analysis of property {property_id}',
        'total': 1,
        'progress': 0,
        'results': [],
        'error': None,
        'started': datetime.now().isoformat(),
        'step': 'analyzing',
        'step_detail': 'Starting versioned re-analysis...',
        'steps_log': [{'step': 'analyzing',
                       'detail': 'Starting versioned re-analysis...',
                       'time': datetime.now().isoformat()}],
    }

    def on_step(step, detail=''):
        jobs[job_id]['step'] = step
        jobs[job_id]['step_detail'] = detail
        jobs[job_id]['steps_log'].append({
            'step': step, 'detail': detail,
            'time': datetime.now().isoformat()
        })

    def process_async():
        db2 = None
        try:
            db2 = get_org_db(org_id)
            llm = get_llm()
            from .property_analyzer import PropertyAnalyzer
            analyzer = PropertyAnalyzer(db2, llm)
            analyzer._on_step = on_step
            summary = analyzer.analyze_property(property_id, versioned=True)
            on_step('complete', f'Re-analysis complete — {summary.get("doc_count", 0)} documents')
            jobs[job_id]['results'] = [summary]
            jobs[job_id]['progress'] = 1
            jobs[job_id]['status'] = 'completed' if 'error' not in summary else 'failed'
            jobs[job_id]['error'] = summary.get('error')
        except Exception as e:
            jobs[job_id]['status'] = 'failed'
            jobs[job_id]['error'] = str(e)
        finally:
            if db2 is not None:
                db2.close()

    enqueue_job(job_id, process_async)
    return jsonify({'success': True, 'job_id': job_id})


@app.route('/api/document/<int:doc_id>/extraction-runs')
@login_required
def api_extraction_runs(doc_id):
    """List all extraction runs for a document."""
    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        runs = db.get_extraction_runs(doc_id)
        return jsonify({'runs': runs})
    finally:
        db.close()


@app.route('/api/document/<int:doc_id>/compare-runs')
@login_required
def api_compare_runs(doc_id):
    """Compare two extraction runs for a document.
    Query params: run_a, run_b (extraction_run IDs)"""
    org_id = session['org_id']
    run_a = request.args.get('run_a', type=int)
    run_b = request.args.get('run_b', type=int)
    if not run_a or not run_b:
        return jsonify({'error': 'run_a and run_b query params required'}), 400
    db = get_org_db(org_id)
    try:
        comparison = db.get_run_comparison(doc_id, run_a, run_b)
        return jsonify(comparison)
    finally:
        db.close()


@app.route('/api/property/<int:property_id>/extraction-summary')
@login_required
def api_property_extraction_summary(property_id):
    """Get extraction run summary across all documents for a property.
    Shows which documents have multiple runs and are ready for comparison."""
    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        docs = db.conn.execute("""
            SELECT d.id, d.filename, d.document_type,
                   COUNT(er.id) as run_count,
                   MAX(er.run_number) as latest_run,
                   MAX(CASE WHEN er.is_current = 1 THEN er.id END) as current_run_id,
                   MIN(er.id) as first_run_id
            FROM documents d
            LEFT JOIN extraction_runs er ON er.document_id = d.id
            WHERE d.property_id = ?
            GROUP BY d.id
            ORDER BY d.filename
        """, (property_id,)).fetchall()
        return jsonify({
            'property_id': property_id,
            'documents': [dict(d) for d in docs],
            'total_docs': len(docs),
            'docs_with_multiple_runs': sum(1 for d in docs if d['run_count'] and d['run_count'] > 1),
        })
    finally:
        db.close()


@app.route('/api/extraction-run/<int:run_id>/set-current', methods=['POST'])
@login_required
def api_set_current_run(run_id):
    """Switch which extraction run is the active one for a document."""
    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        run = db.conn.execute("SELECT document_id FROM extraction_runs WHERE id = ?", (run_id,)).fetchone()
        if not run:
            return jsonify({'error': 'Run not found'}), 404
        db.set_current_run(run['document_id'], run_id)
        return jsonify({'success': True})
    finally:
        db.close()


# ─── Bulk Re-extract & Document Groups ─────────────────────────────

@app.route('/api/documents/groups')
@login_required
def api_document_groups():
    """List documents grouped by upload directory (batch) for management.

    Returns groups with doc counts, type breakdown, and extraction status.
    """
    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        docs = db.list_documents()
        # Group by the directory portion of filepath
        groups = {}
        for doc in docs:
            fp = doc.get('filepath', '')
            # Extract the batch/upload directory
            dir_path = os.path.dirname(fp)
            dir_name = os.path.basename(dir_path) if dir_path else 'ungrouped'

            if dir_name not in groups:
                groups[dir_name] = {
                    'name': dir_name,
                    'dir_path': dir_path,
                    'doc_count': 0,
                    'doc_ids': [],
                    'types': {},
                    'properties': set(),
                    'oldest': None,
                    'newest': None,
                    'has_files': True,
                }
            g = groups[dir_name]
            g['doc_count'] += 1
            g['doc_ids'].append(doc['id'])
            dtype = doc.get('document_type', 'unknown')
            g['types'][dtype] = g['types'].get(dtype, 0) + 1
            if doc.get('property_name'):
                g['properties'].add(doc['property_name'])
            ts = doc.get('processed_at', '')
            if ts:
                if not g['oldest'] or ts < g['oldest']:
                    g['oldest'] = ts
                if not g['newest'] or ts > g['newest']:
                    g['newest'] = ts

        # Check if source files still exist for each group
        for g in groups.values():
            if g['dir_path'] and not os.path.isdir(g['dir_path']):
                # Check if any individual doc files exist
                g['has_files'] = False
                for doc in docs:
                    if os.path.dirname(doc.get('filepath', '')) == g['dir_path']:
                        if os.path.exists(doc['filepath']):
                            g['has_files'] = True
                            break

        # Convert sets to lists for JSON
        result = []
        for name, g in sorted(groups.items(), key=lambda x: x[1].get('newest', ''), reverse=True):
            result.append({
                'name': g['name'],
                'dir_path': g['dir_path'],
                'doc_count': g['doc_count'],
                'doc_ids': g['doc_ids'],
                'types': g['types'],
                'properties': sorted(g['properties']),
                'oldest': g['oldest'],
                'newest': g['newest'],
                'has_files': g['has_files'],
            })
        return jsonify({'groups': result, 'total_docs': len(docs)})
    finally:
        db.close()


@app.route('/api/documents/bulk-reextract', methods=['POST'])
@login_required
def api_bulk_reextract():
    """Re-extract multiple documents by ID list.

    JSON body:
      doc_ids: list of document IDs to re-extract
    """
    org_id = session['org_id']
    user_id = session['user_id']
    data = request.get_json()
    if not data or 'doc_ids' not in data:
        return jsonify({'error': 'doc_ids required'}), 400

    doc_ids = data['doc_ids']
    if not doc_ids:
        return jsonify({'error': 'No documents specified'}), 400

    # Cap at 500 docs per batch for safety
    if len(doc_ids) > 500:
        return jsonify({'error': f'Too many documents ({len(doc_ids)}), max 500'}), 400

    # Validate all docs exist and collect file info
    db = get_org_db(org_id)
    try:
        doc_infos = []
        missing_files = []
        for doc_id in doc_ids:
            doc = db.get_document(doc_id)
            if not doc:
                continue
            fp = doc.get('filepath', '')
            if not fp or not os.path.exists(fp):
                missing_files.append({'id': doc_id, 'filename': doc.get('filename', '?')})
                continue
            doc_infos.append({
                'id': doc_id,
                'filepath': fp,
                'property_name': doc.get('property_name'),
                'filename': doc.get('filename', os.path.basename(fp)),
            })

        if not doc_infos:
            return jsonify({
                'error': 'No documents have source files on disk',
                'missing_files': missing_files
            }), 400

        # Delete all extractions first (batch delete)
        deleted_count = 0
        for info in doc_infos:
            result = db.delete_document_extractions(info['id'])
            if result:
                deleted_count += 1

    finally:
        db.close()

    # Create a job for the bulk re-extract
    _cleanup_expired_jobs()
    job_id = str(uuid.uuid4())[:8]
    total = len(doc_infos)
    jobs[job_id] = {
        'id': job_id,
        'org_id': org_id,
        'status': 'processing',
        'type': 'bulk_reextract',
        'filename': f'Re-extracting {total} documents',
        'total': total,
        'progress': 0,
        'results': [],
        'error': None,
        'started': datetime.now().isoformat(),
        'step': 'reprocessing',
        'step_detail': f'Queued {total} documents for re-extraction...',
        'steps_log': [{'step': 'reprocessing',
                       'detail': f'Starting re-extraction of {total} documents',
                       'time': datetime.now().isoformat()}],
    }

    def process_bulk():
        results_list = []
        errors = 0
        for i, info in enumerate(doc_infos):
            fname = info['filename']
            jobs[job_id]['step_detail'] = f'Processing {i+1}/{total}: {fname}'
            jobs[job_id]['steps_log'].append({
                'step': 'reprocessing',
                'detail': f'Processing {fname}',
                'time': datetime.now().isoformat()
            })

            result, had_error = _process_single_doc_thread(
                org_id, user_id,
                info['filepath'],
                None,  # let classifier re-detect type
                info['property_name']
            )

            results_list.append(_result_to_dict(result))
            if had_error:
                errors += 1
            jobs[job_id]['progress'] = i + 1

        jobs[job_id]['results'] = results_list
        jobs[job_id]['status'] = 'completed' if errors == 0 else 'completed_with_errors'
        jobs[job_id]['step'] = 'complete'
        jobs[job_id]['step_detail'] = (
            f'Re-extracted {total} documents'
            + (f' ({errors} errors)' if errors else '')
        )

    enqueue_job(job_id, process_bulk)

    return jsonify({
        'success': True,
        'job_id': job_id,
        'doc_count': total,
        'missing_files': missing_files,
        'deleted_extractions': deleted_count,
    })


@app.route('/api/property/<int:property_id>/analyze', methods=['POST'])
@login_required
def api_analyze_property(property_id):
    """Run Phase 2 analysis on all ingested documents for a property."""
    org_id = session['org_id']

    # Verify property exists and has documents
    db = get_org_db(org_id)
    try:
        prop = db.get_property(property_id)
        if not prop:
            return jsonify({'error': 'Property not found'}), 404
        docs = db.get_property_documents(property_id)
        if not docs:
            return jsonify({'error': 'No documents linked to this property'}), 400
    finally:
        db.close()

    _cleanup_expired_jobs()
    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {
        'id': job_id,
        'org_id': org_id,
        'status': 'processing',
        'type': 'analysis',
        'filename': f'Analyzing {prop["name"]}',
        'total': len(docs),
        'progress': 0,
        'results': [],
        'error': None,
        'started': datetime.now().isoformat(),
        'step': 'analyzing',
        'step_detail': f'Analyzing {len(docs)} documents...',
        'steps_log': [{'step': 'analyzing', 'detail': f'Starting analysis for {prop["name"]}...', 'time': datetime.now().isoformat()}],
    }

    def on_step(step, detail=''):
        jobs[job_id]['step'] = step
        jobs[job_id]['step_detail'] = detail
        jobs[job_id]['steps_log'].append({
            'step': step, 'detail': detail,
            'time': datetime.now().isoformat()
        })

    def process_async():
        db2 = None
        try:
            db2 = get_org_db(org_id)
            llm = get_llm()
            analyzer = PropertyAnalyzer(db2, llm)
            analyzer._on_step = on_step
            summary = analyzer.analyze_property(property_id)

            jobs[job_id]['results'] = [summary]
            jobs[job_id]['progress'] = summary.get('doc_count', 0)

            if summary.get('error'):
                on_step('failed', summary['error'])
                jobs[job_id]['status'] = 'failed'
                jobs[job_id]['error'] = summary['error']
            else:
                on_step('complete', f'Analysis complete — {summary.get("doc_count", 0)} documents processed')
                jobs[job_id]['status'] = 'completed'
        except Exception as e:
            import traceback
            print(f"[ERROR] Analysis job {job_id} failed: {e}", flush=True)
            traceback.print_exc()
            on_step('failed', str(e))
            jobs[job_id]['status'] = 'failed'
            jobs[job_id]['error'] = str(e)
        finally:
            if db2 is not None:
                db2.close()

    enqueue_job(job_id, process_async)

    return jsonify({'success': True, 'job_id': job_id, 'doc_count': len(docs)})


@app.route('/api/property/<int:property_id>/analysis')
@login_required
def api_property_analysis(property_id):
    """Get the latest analysis results for a property."""
    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        analysis = db.get_latest_analysis(property_id)
        if not analysis:
            return jsonify({'status': 'none', 'message': 'No analysis has been run yet'})
        return jsonify(analysis)
    finally:
        db.close()


@app.route('/api/property/<int:property_id>/synthesis')
@login_required
def api_property_synthesis(property_id):
    """Get a reconciled financial synthesis for a property."""
    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        synthesizer = FinancialSynthesizer(db)
        result = synthesizer.synthesize(property_id)
        return jsonify(result)
    finally:
        db.close()


# ─── Extraction Health Assessment ──────────────────────────────────

# Doc types that should produce extraction results
_EXTRACTABLE_TYPES = DocumentClassifier.EXTRACTABLE_TYPES
_OS_TYPES = {'operating_statement'}
_NO_EXTRACT_TYPES = {
    'unknown',
}


def _assess_doc(db, doc):
    """
    Assess a single document's extraction health.
    Returns (status, reason) where status is:
        'good', 'needs_rerun', 'new', or 'skip'
    """
    doc_id = doc['id']
    doc_type = doc.get('document_type', 'unknown')
    analysis_status = doc.get('analysis_status', 'ingested')

    conf = 1.0
    if doc.get('metadata'):
        try:
            meta = json.loads(doc['metadata']) if isinstance(doc['metadata'], str) else doc['metadata']
            conf = meta.get('classification_confidence', 1.0)
        except (json.JSONDecodeError, TypeError):
            pass

    # Empty/blank document_type = needs reclassification → always flag
    if not doc_type or not doc_type.strip():
        return 'needs_rerun', 'Unclassified — needs reclassification before extraction'

    if analysis_status != 'analyzed':
        return 'new', 'Not yet analyzed'

    if doc_type in _NO_EXTRACT_TYPES:
        return 'skip', f'No extraction for {doc_type}'

    term_count = db.conn.execute(
        "SELECT COUNT(*) FROM financial_terms WHERE document_id = ?", (doc_id,)
    ).fetchone()[0]
    clause_count = db.conn.execute(
        "SELECT COUNT(*) FROM clauses WHERE document_id = ?", (doc_id,)
    ).fetchone()[0]
    os_count = db.conn.execute(
        "SELECT COUNT(*) FROM operating_statement_items WHERE document_id = ?", (doc_id,)
    ).fetchone()[0]
    rr_count = db.conn.execute(
        "SELECT COUNT(*) FROM rent_roll_entries WHERE document_id = ?", (doc_id,)
    ).fetchone()[0]
    total_extracted = term_count + clause_count + os_count + rr_count

    if doc_type in _OS_TYPES:
        if os_count > 0:
            return 'good', f'{os_count} OS items'
        if conf < 0.5:
            return 'skip', f'Low-confidence OS ({conf:.0%}), likely misclassified'
        return 'needs_rerun', 'Operating statement with 0 items'

    if doc_type == 'rent_roll':
        if rr_count > 0:
            return 'good', f'{rr_count} rent roll entries'
        if conf < 0.5:
            return 'skip', f'Low-confidence rent roll ({conf:.0%}), likely misclassified'
        return 'needs_rerun', 'Rent roll with 0 entries'

    if doc_type == 'general_ledger':
        gl_count = db.conn.execute(
            "SELECT COUNT(*) FROM gl_entries WHERE document_id = ?", (doc_id,)
        ).fetchone()[0]
        if gl_count > 0:
            return 'good', f'{gl_count} GL entries'
        if conf < 0.5:
            return 'skip', f'Low-confidence GL ({conf:.0%}), likely misclassified'
        return 'needs_rerun', 'General ledger with 0 entries'

    if doc_type in _EXTRACTABLE_TYPES:
        if total_extracted > 0:
            parts = []
            if term_count:
                parts.append(f'{term_count} terms')
            if clause_count:
                parts.append(f'{clause_count} clauses')
            return 'good', ', '.join(parts)
        # If a dedicated template exists and doc hasn't been analyzed yet,
        # flag for re-run.  But if it's already been analyzed and still has
        # 0 results, accept that — re-running won't help (the content is
        # genuinely sparse or the file format can't be parsed).
        has_template = doc_type in TEMPLATES
        if conf < 0.5 and not has_template:
            return 'skip', f'Low-confidence {doc_type} ({conf:.0%}), likely misclassified'
        if analysis_status == 'analyzed':
            return 'good', f'{doc_type} — analyzed, no extractable content'
        return 'needs_rerun', f'{doc_type} with 0 extraction results'

    if total_extracted > 0:
        return 'good', f'{total_extracted} total extracted'
    return 'skip', f'No extraction template for {doc_type}'


@app.route('/api/property/<int:property_id>/assess')
@login_required
def api_assess_property(property_id):
    """Return per-document extraction health assessment."""
    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        prop = db.get_property(property_id)
        if not prop:
            return jsonify({'error': 'Property not found'}), 404

        docs = db.conn.execute(
            "SELECT * FROM documents WHERE property_id = ? ORDER BY document_type, id",
            (property_id,)
        ).fetchall()
        docs = [dict(d) for d in docs]

        results = []
        counts = {'good': 0, 'needs_rerun': 0, 'new': 0, 'skip': 0}
        for doc in docs:
            status, reason = _assess_doc(db, doc)
            counts[status] = counts.get(status, 0) + 1
            results.append({
                'id': doc['id'],
                'filename': doc.get('filename', ''),
                'document_type': doc.get('document_type', 'unknown'),
                'status': status,
                'reason': reason,
            })

        rerun_count = counts['needs_rerun'] + counts['new']
        return jsonify({
            'property_id': property_id,
            'total': len(docs),
            'counts': counts,
            'rerun_count': rerun_count,
            'documents': results,
        })
    finally:
        db.close()


@app.route('/api/property/<int:property_id>/reclassify', methods=['POST'])
@login_required
def api_reclassify_property(property_id):
    """
    Re-run document classification for a property.

    By default, only re-classifies docs with empty/blank document_type.
    Pass {"force": true} to re-classify ALL docs for the property.

    Returns updated classification results per document.
    """
    org_id = session['org_id']
    body = request.get_json(force=True) if request.data else {}
    force_all = body.get('force', False)

    db = get_org_db(org_id)
    try:
        prop = db.get_property(property_id)
        if not prop:
            return jsonify({'error': 'Property not found'}), 404

        docs = db.conn.execute(
            "SELECT * FROM documents WHERE property_id = ? ORDER BY id",
            (property_id,)
        ).fetchall()
        docs = [dict(d) for d in docs]

        if not force_all:
            docs = [d for d in docs if not d.get('document_type', '').strip()]

        if not docs:
            return jsonify({
                'success': True,
                'message': 'No documents need reclassification.',
                'reclassified': 0,
            })

        from .pdf_ingestion import DocumentContent, PageContent

        llm = get_llm()
        classifier = DocumentClassifier(llm)
        results = []
        reclassified = 0

        for doc in docs:
            doc_id = doc['id']
            old_type = doc.get('document_type', '')

            # Reconstruct doc content from stored fulltext
            rows = db.conn.execute("""
                SELECT page_number, content FROM document_fulltext
                WHERE CAST(document_id AS INTEGER) = ?
                ORDER BY CAST(page_number AS INTEGER)
            """, (doc_id,)).fetchall()

            if not rows:
                results.append({
                    'id': doc_id,
                    'filename': doc.get('filename', ''),
                    'old_type': old_type,
                    'new_type': old_type,
                    'confidence': 0,
                    'changed': False,
                    'error': 'No fulltext stored',
                })
                continue

            pages = [PageContent(
                page_number=int(r['page_number']),
                text=r['content'],
                tables=[],
                is_scanned=bool(doc.get('is_scanned')),
            ) for r in rows]

            doc_content = DocumentContent(
                filepath=doc.get('filepath', ''),
                filename=doc.get('filename', ''),
                pages=pages,
                page_count=len(pages),
                is_scanned=bool(doc.get('is_scanned')),
                file_hash=doc.get('file_hash', ''),
            )

            new_type, conf = classifier.classify(doc_content, use_llm=False)
            changed = (new_type != old_type) and new_type and new_type != 'unknown'

            if changed:
                meta = json.dumps({'classification_confidence': conf})
                db.conn.execute(
                    'UPDATE documents SET document_type=?, analysis_status=?, metadata=? WHERE id=?',
                    (new_type, 'ingested', meta, doc_id)
                )
                reclassified += 1

            results.append({
                'id': doc_id,
                'filename': doc.get('filename', ''),
                'old_type': old_type,
                'new_type': new_type if changed else old_type,
                'confidence': round(conf, 2),
                'changed': changed,
            })

        db.conn.commit()
        return jsonify({
            'success': True,
            'total': len(docs),
            'reclassified': reclassified,
            'documents': results,
        })
    finally:
        db.close()


@app.route('/api/property/<int:property_id>/reconcile')
@login_required
def api_reconcile_terms(property_id):
    """Return cross-document term reconciliation for a property."""
    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        prop = db.get_property(property_id)
        if not prop:
            return jsonify({'error': 'Property not found'}), 404

        result = reconcile_terms(db.conn, property_id)
        return jsonify(result)
    finally:
        db.close()


@app.route('/api/property/<int:property_id>/analyze-selective', methods=['POST'])
@login_required
def api_analyze_selective(property_id):
    """
    Run Phase 2 analysis with mode selection.

    POST body (JSON):
        mode: 'full' | 'smart' | 'new_only'  (default: 'full')
        doc_types: ['closing', 'loan', ...]   (optional filter)
    """
    org_id = session['org_id']
    body = request.get_json(force=True) if request.data else {}
    mode = body.get('mode', 'full')
    doc_types = body.get('doc_types')

    if mode not in ('full', 'smart', 'new_only'):
        return jsonify({'error': f'Invalid mode: {mode}'}), 400

    db = get_org_db(org_id)
    try:
        prop = db.get_property(property_id)
        if not prop:
            return jsonify({'error': 'Property not found'}), 404

        # Get all docs for this property
        docs = db.conn.execute(
            "SELECT * FROM documents WHERE property_id = ? ORDER BY document_type, id",
            (property_id,)
        ).fetchall()
        docs = [dict(d) for d in docs]

        if doc_types:
            doc_type_set = set(doc_types)
            docs = [d for d in docs if d.get('document_type') in doc_type_set]

        if not docs:
            return jsonify({'error': 'No documents match the criteria'}), 400

        # Determine which docs to process
        if mode == 'full':
            process_ids = [d['id'] for d in docs]
            skip_ids = []
        elif mode == 'new_only':
            process_ids = [d['id'] for d in docs if d.get('analysis_status') != 'analyzed']
            skip_ids = [d['id'] for d in docs if d.get('analysis_status') == 'analyzed']
        else:  # smart
            process_ids = []
            skip_ids = []
            for d in docs:
                status, _ = _assess_doc(db, d)
                if status in ('new', 'needs_rerun'):
                    process_ids.append(d['id'])
                else:
                    skip_ids.append(d['id'])

        if not process_ids:
            return jsonify({
                'success': True,
                'message': f'All {len(skip_ids)} documents already have good extraction results.',
                'skipped': len(skip_ids),
                'processing': 0,
            })
    finally:
        db.close()

    mode_label = {'full': 'Full re-run', 'smart': 'Smart (needs rerun only)', 'new_only': 'New docs only'}.get(mode, mode)
    _cleanup_expired_jobs()
    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {
        'id': job_id,
        'org_id': org_id,
        'status': 'processing',
        'type': 'analysis',
        'filename': f'{mode_label} — {prop["name"]}',
        'total': len(process_ids),
        'progress': 0,
        'results': [],
        'error': None,
        'started': datetime.now().isoformat(),
        'step': 'analyzing',
        'step_detail': f'{mode_label}: processing {len(process_ids)} of {len(docs)} documents...',
        'steps_log': [{
            'step': 'analyzing',
            'detail': f'{mode_label} for {prop["name"]} — {len(process_ids)} to process, {len(skip_ids)} skipped',
            'time': datetime.now().isoformat(),
        }],
    }

    def on_step(step, detail=''):
        jobs[job_id]['step'] = step
        jobs[job_id]['step_detail'] = detail
        jobs[job_id]['steps_log'].append({
            'step': step, 'detail': detail,
            'time': datetime.now().isoformat()
        })

    _process_ids = list(process_ids)
    _skip_ids = list(skip_ids)
    _mode = mode

    def process_async():
        db2 = None
        try:
            db2 = get_org_db(org_id)
            llm = get_llm()

            # Clear extraction data for docs being re-processed
            if _mode == 'full':
                # Full mode: clear ALL extraction data for this property's docs
                all_ids = _process_ids + _skip_ids
                if all_ids:
                    ph = ','.join('?' * len(all_ids))
                    for table in ['clauses', 'financial_terms', 'rent_roll_entries',
                                  'operating_statement_items', 'gl_entries']:
                        db2.conn.execute(f"DELETE FROM {table} WHERE document_id IN ({ph})", all_ids)
                    db2.conn.commit()
            else:
                # Smart/new_only: only clear data for docs being re-processed
                if _process_ids:
                    ph = ','.join('?' * len(_process_ids))
                    for table in ['clauses', 'financial_terms', 'rent_roll_entries',
                                  'operating_statement_items', 'gl_entries']:
                        db2.conn.execute(f"DELETE FROM {table} WHERE document_id IN ({ph})", _process_ids)
                    db2.conn.commit()

            analyzer = PropertyAnalyzer(db2, llm)
            analyzer._on_step = on_step

            if _mode == 'full' and not doc_types:
                summary = analyzer.analyze_property(property_id)
            else:
                summary = analyzer.analyze_documents(property_id, _process_ids)

            summary['skipped'] = len(_skip_ids)
            summary['mode'] = _mode
            jobs[job_id]['results'] = [summary]
            jobs[job_id]['progress'] = summary.get('doc_count', 0)

            if summary.get('error'):
                on_step('failed', summary['error'])
                jobs[job_id]['status'] = 'failed'
                jobs[job_id]['error'] = summary['error']
            else:
                on_step('complete',
                        f'Analysis complete — {summary.get("doc_count", 0)} docs processed, '
                        f'{len(_skip_ids)} skipped')
                jobs[job_id]['status'] = 'completed'
        except Exception as e:
            import traceback
            print(f"[ERROR] Analysis job {job_id} failed: {e}", flush=True)
            traceback.print_exc()
            on_step('failed', str(e))
            jobs[job_id]['status'] = 'failed'
            jobs[job_id]['error'] = str(e)
        finally:
            if db2 is not None:
                db2.close()

    enqueue_job(job_id, process_async)

    return jsonify({
        'success': True,
        'job_id': job_id,
        'processing': len(process_ids),
        'skipped': len(skip_ids),
        'mode': mode,
    })


@app.route('/document/<int:doc_id>/pdf')
@login_required
def document_pdf(doc_id):
    """Serve the original PDF file for viewing/download."""
    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        doc = db.get_document(doc_id)
        if not doc:
            flash('Document not found.', 'error')
            return redirect(url_for('documents'))
        filepath = doc.get('filepath', '')
        if not filepath or not os.path.exists(filepath):
            flash('Original PDF file not found on disk.', 'error')
            return redirect(url_for('document_detail', doc_id=doc_id))
    finally:
        db.close()

    return send_file(
        filepath,
        mimetype='application/pdf',
        as_attachment=False,
        download_name=doc['filename']
    )


@app.route('/api/properties/search')
@login_required
def api_property_search():
    """Autocomplete endpoint for property names."""
    q = request.args.get('q', '').strip()
    if len(q) < 1:
        return jsonify([])
    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        names = db.search_property_names(q, limit=10)
    finally:
        db.close()
    return jsonify(names)


@app.route('/api/admin/auto-link-documents', methods=['POST'])
@login_required
def api_auto_link_documents():
    """Auto-link unlinked documents to matching properties by name."""
    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        count = db.auto_link_documents_by_name()
    finally:
        db.close()
    return jsonify({'linked': count})


@app.route('/api/admin/rename-property/<int:property_id>', methods=['POST'])
@login_required
def api_rename_property(property_id):
    """Rename a property and re-link documents."""
    new_name = request.json.get('name') if request.is_json else request.form.get('name')
    if not new_name:
        return jsonify({'error': 'name required'}), 400
    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        db.update_property(property_id, name=new_name)
        linked = db.auto_link_documents_by_name()
    finally:
        db.close()
    return jsonify({'renamed': True, 'new_name': new_name, 'documents_linked': linked})


@app.route('/api/export/<table>')
@login_required
def api_export(table):
    allowed_tables = ['documents', 'clauses', 'financial_terms',
                      'rent_roll_entries', 'operating_statement_items', 'gl_entries']
    if table not in allowed_tables:
        return jsonify({'error': 'Invalid table'}), 400

    import tempfile
    org_id = session['org_id']
    db = get_org_db(org_id)
    try:
        filepath = os.path.join(tempfile.gettempdir(), f'{table}_export.csv')
        count = db.export_to_csv(table, filepath)
    finally:
        db.close()

    if count == 0:
        flash('No data to export.', 'error')
        return redirect(url_for('documents'))

    return send_file(filepath, as_attachment=True,
                     download_name=f'{table}_{datetime.now().strftime("%Y%m%d")}.csv')


@app.route('/api/status')
def api_system_status():
    llm = get_llm()
    return jsonify({
        'ollama_connected': llm.is_available(),
        'ollama_models': llm.list_models(),
    })


# ─── Helpers ─────────────────────────────────────────────────────────

def _result_to_dict(result: ProcessingResult) -> dict:
    return {
        'filename': result.filename,
        'success': result.success,
        'document_type': result.document_type,
        'document_id': result.document_id,
        'page_count': result.page_count,
        'tables_stored': result.tables_stored,
        'error': result.error,
        'time': round(result.processing_time, 1),
    }


# ─── Market Analytics Landing Page ──────────────────────────────────

@app.route('/market-analytics')
@login_required
def market_analytics():
    """Landing page for all market analytics modules."""
    return render_template('market_analytics.html')


# ─── Module Registration ────────────────────────────────────────────
# Discover and register all platform modules (proforma, etc.)

try:
    from .modules import registry as module_registry
    module_registry.register_routes(app)
except Exception as e:
    import logging
    logging.getLogger(__name__).warning(f'Module registration failed: {e}')

# ─── Module Gating (per-org module activation / tiers) ─────────────
try:
    from .modules.gating import register_gating
    register_gating(app)
except Exception as e:
    import logging
    logging.getLogger(__name__).warning(f'Module gating registration failed: {e}')

# ─── Warehouse Registration ────────────────────────────────────────
# Analytical warehouse (DuckDB) — property z-scores, sales comps, cap rates

try:
    from .warehouse.routes import warehouse_bp
    app.register_blueprint(warehouse_bp)
except Exception as e:
    import logging
    logging.getLogger(__name__).warning(f'Warehouse registration failed: {e}')

# ─── Registry Registration ─────────────────────────────────────────
# Shared entity registry (funds / sub-funds / portfolios / deals) + deal picker
# API. Also seeds Chamberlain's editable per-deal config from the engine defaults
# (idempotent) so the config path is populated for editing. Absolute imports keep
# a single registry singleton shared with the deal-analytics route helpers.

try:
    from registry.routes import register_registry_routes
    from registry import get_registry
    from registry.deal_config_seed import seed_chamberlain_configs
    register_registry_routes(app)
    seed_chamberlain_configs(get_registry())
except Exception as e:
    import logging
    logging.getLogger(__name__).warning(f'Registry registration failed: {e}')


# ─── App Runner ──────────────────────────────────────────────────────

def run_webapp(host='127.0.0.1', port=5000, debug=False):
    """Start the local web application."""
    print(f"\n{'='*50}")
    print(f"  capactive — Document Extractor")
    print(f"  See the signal. Make the move.")
    print(f"")
    print(f"  Running locally at http://{host}:{port}")
    print(f"  All data stays on this device.")
    print(f"{'='*50}\n")
    # threaded=True so one slow request (e.g. a heavy DuckDB warehouse query) doesn't
    # block every other page — the dev server otherwise handles one request at a time.
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == '__main__':
    run_webapp(debug=True)
