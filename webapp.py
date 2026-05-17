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
from .templates.document_templates import list_templates, TEMPLATES
from .config import ConfigStore, PLAN_FEATURES
from .licensing import (
    generate_org_key, generate_user_key, validate_org_key,
    validate_user_key, EntitlementChecker, create_license_file,
    read_license_file
)
from .usage import UsageTracker
from .permissions import (
    PermissionStore, ROLE_TEMPLATES, SCOPES, SCOPE_ORDER, LEVELS,
    check_permission, can_read, can_edit, get_scope_categories
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

# Dev mode: skip login/setup for local testing
# Set CAPACTIVE_DEV_MODE=1 to bypass authentication
DEV_MODE = os.environ.get('CAPACTIVE_DEV_MODE', '0') == '1'

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

# Global state for background jobs
jobs = {}

# ─── Processing Queue ───────────────────────────────────────────────
# Single worker thread processes jobs sequentially so Ollama isn't
# hammered by parallel requests.

import queue as _queue

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


_worker_thread = threading.Thread(target=_queue_worker, daemon=True)
_worker_thread.start()


def enqueue_job(job_id, process_fn):
    """Add a job to the processing queue. It will run when its turn comes."""
    pending_count = _work_queue.qsize()
    jobs[job_id]['status'] = 'queued' if pending_count > 0 else 'processing'
    jobs[job_id]['queue_position'] = pending_count + 1 if pending_count > 0 else 0
    _work_queue.put({'job_id': job_id, 'fn': process_fn})

# ─── Helpers ─────────────────────────────────────────────────────────

def get_config_store():
    store = ConfigStore(CONFIG_DB, DATA_DIR)
    store.connect()
    return store

def get_usage_tracker():
    tracker = UsageTracker(CONFIG_DB)
    tracker.connect()
    return tracker

def get_org_db(org_id):
    """Get the extraction database for a specific org."""
    store = get_config_store()
    try:
        db_path = store.get_org_db_path(org_id)
    finally:
        store.close()
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
    store = PermissionStore(CONFIG_DB)
    store.connect()
    return store

def get_llm():
    return LocalLLMClient(base_url=OLLAMA_URL, model=OLLAMA_MODEL)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def is_archive(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ARCHIVE_EXTENSIONS

def extract_zip(zip_path, dest_dir):
    """Extract a zip file, return list of (filepath, ext) for all files found.
    Skips __MACOSX and hidden files. Flattens nested folders."""
    import zipfile
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

            if ext in ALLOWED_EXTENSIONS:
                extracted.append((dest_path, ext))
            else:
                skipped.append((basename, ext))

    return extracted, skipped

def is_setup_complete():
    """Check if initial setup has been completed."""
    store = get_config_store()
    try:
        orgs = store.list_orgs()
        return len(orgs) > 0
    finally:
        store.close()

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
    try:
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
            try:
                pstore.init_user_permissions('admin', 'dev', role='admin')
            finally:
                pstore.close()
        org = store.get_org('dev') or store.list_orgs()[0]
        users = store.list_users(org.org_id)
        user = users[0] if users else None
    finally:
        store.close()

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
            store = get_permission_store()
            try:
                perms = store.get_user_permissions(
                    session['user_id'], session['org_id'])
            finally:
                store.close()
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
        store = get_permission_store()
        try:
            perms = store.get_user_permissions(user['user_id'], user['org_id'])
        finally:
            store.close()

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


@app.route('/upload', methods=['GET', 'POST'])
@login_required
@permission_required('extraction.upload', 'edit')
def upload():
    """Single file upload and processing."""
    org_id = session['org_id']
    user_id = session['user_id']

    if request.method == 'POST':
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
        job_id = str(uuid.uuid4())[:8]
        jobs[job_id] = {
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
            db = None
            try:
                db = get_org_db(org_id)
                llm = get_llm()
                processor = BatchProcessor(db, llm)
                processor._on_step = on_step

                failed_count = 0
                for i, (fname, fpath) in enumerate(saved):
                    on_step('ingesting', f'Reading {fname}... ({i+1}/{file_count})')
                    try:
                        result = processor.process_single(
                            fpath, document_type=doc_type,
                            property_name=property_name)
                    except Exception as doc_err:
                        result = ProcessingResult(
                            filename=fname, success=False,
                            error=str(doc_err),
                            document_type=doc_type or 'unknown',
                            page_count=0, processing_time=0,
                            tables_stored=0
                        )
                        failed_count += 1

                    jobs[job_id]['progress'] = i + 1
                    jobs[job_id]['results'].append(_result_to_dict(result))

                    # Log usage
                    t = get_usage_tracker()
                    try:
                        t.log_document_processed(
                            org_id=org_id, user_id=user_id,
                            filename=fname,
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

                on_step('complete', 'Ingested' if file_count == 1 else f'All {file_count} files ingested')
                jobs[job_id]['status'] = 'completed'
                jobs[job_id]['failed_count'] = failed_count
                if file_count == 1 and jobs[job_id]['results']:
                    jobs[job_id]['error'] = jobs[job_id]['results'][0].get('error')
            except Exception as e:
                jobs[job_id]['status'] = 'failed'
                jobs[job_id]['error'] = str(e)
            finally:
                if db:
                    db.close()

        enqueue_job(job_id, process_async)

        return redirect(url_for('job_status', job_id=job_id))

    return render_template('upload.html', templates=list_templates())


@app.route('/batch', methods=['GET', 'POST'])
@login_required
@permission_required('extraction.batch', 'edit')
def batch():
    """Batch folder processing — accepts uploaded files via folder picker."""
    org_id = session['org_id']
    user_id = session['user_id']

    if request.method == 'POST':
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

        job_id = str(uuid.uuid4())[:8]
        jobs[job_id] = {
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
            db = None
            try:
                db = get_org_db(org_id)
                llm = get_llm()
                processor = BatchProcessor(db, llm)
                processor._on_step = on_step

                failed_count = 0
                for i, filepath in enumerate(saved_paths):
                    fname = os.path.basename(filepath)
                    on_step('ingesting', f'Processing {fname}... ({i+1}/{pdf_count})')
                    try:
                        result = processor.process_single(
                            filepath,
                            document_type=doc_type,
                            property_name=property_name
                        )
                    except Exception as doc_err:
                        # Single document failure — log and continue
                        result = ProcessingResult(
                            filename=fname, success=False,
                            error=str(doc_err),
                            document_type=doc_type or 'unknown',
                            page_count=0, processing_time=0,
                            tables_stored=0
                        )
                        failed_count += 1

                    jobs[job_id]['progress'] = i + 1
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
            finally:
                if db:
                    db.close()

        enqueue_job(job_id, process_async)

        return redirect(url_for('job_status', job_id=job_id))

    return render_template('batch.html', templates=list_templates())


@app.route('/job/<job_id>')
@login_required
def job_status(job_id):
    job = jobs.get(job_id)
    if not job:
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
    finally:
        db.close()
    return render_template('document_detail.html',
                           doc=doc, terms=terms, clauses=clauses,
                           rent_roll=rent_roll, opstat=opstat, gl=gl)


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
    from exports import EXPORT_TYPES, export_csv_bytes, export_excel, HAS_OPENPYXL

    if export_type not in EXPORT_TYPES:
        flash('Unknown export type.', 'error')
        return redirect(url_for('index'))

    # Check feature flag
    user = get_current_user()
    if user:
        from config import ConfigStore
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
    from exports import (export_property_workbook, export_csv_bytes,
                         RENT_ROLL_COLUMNS, HAS_OPENPYXL)

    # Check feature flag
    user = get_current_user()
    if user:
        from config import ConfigStore
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

    return render_template('admin_license.html',
                           org=org, usage=usage, user_count=len(users),
                           plan_features=PLAN_FEATURES)


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
        portfolio_list = db.list_portfolios()
    finally:
        db.close()

    return render_template('portfolio_comparison.html',
                           portfolio=portfolio,
                           view_title=view_title,
                           comparisons=comparisons,
                           periods=sorted_periods,
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

        # Get review queue
        docs = db.get_extraction_review_queue(
            property_id=property_id,
            status=status_filter
        )

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
                           status_filter=status_filter)


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


# ─── API Routes ──────────────────────────────────────────────────────

@app.route('/api/jobs/active')
@login_required
def api_active_jobs():
    """Return list of currently processing or queued jobs."""
    active = []
    # Recalculate queue positions for queued jobs
    queue_pos = 1
    for jid, job in jobs.items():
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
def api_job_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify(job)


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
        job_id = str(uuid.uuid4())[:8]
        jobs[job_id] = {
            'id': job_id,
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
                db2.close()

        enqueue_job(job_id, process_async)

        return jsonify({'success': True, 'job_id': job_id})
    finally:
        db.close()


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

    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {
        'id': job_id,
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
            jobs[job_id]['status'] = 'failed'
            jobs[job_id]['error'] = str(e)
        finally:
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
    app.run(host=host, port=port, debug=debug)


if __name__ == '__main__':
    run_webapp(debug=True)
