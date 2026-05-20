"""
Closing Books module routes — document warehouse explorer UI + API.
"""

import logging
from flask import Blueprint, jsonify, request, render_template

from .engine import ClosingBooksEngine

logger = logging.getLogger(__name__)

closing_books_bp = Blueprint('closing_books', __name__, url_prefix='/closing-books')

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = ClosingBooksEngine()
    return _engine


def register_closing_books_routes(app):
    """Register the closing_books blueprint with the Flask app."""
    app.register_blueprint(closing_books_bp)


# ─── Pages ─────────────────────────────────────────────────────────

@closing_books_bp.route('/')
def closing_books_index():
    """Closing books warehouse explorer."""
    return render_template('closing_books.html')


@closing_books_bp.route('/file/<path:file_id>')
def closing_books_file_detail(file_id):
    """File detail page with blocks and mappings."""
    return render_template('closing_books.html', detail_file_id=file_id)


# ─── API ───────────────────────────────────────────────────────────

@closing_books_bp.route('/api/stats')
def api_stats():
    """Dashboard summary statistics."""
    eng = _get_engine()
    return jsonify(eng.get_stats())


@closing_books_bp.route('/api/files')
def api_files():
    """List file records with filters."""
    eng = _get_engine()
    batch = request.args.get('batch')
    doc_type = request.args.get('doc_type')
    status = request.args.get('status')
    search = request.args.get('search', '').strip() or None
    limit = min(int(request.args.get('limit', 100)), 500)
    offset = int(request.args.get('offset', 0))
    result = eng.get_files(
        batch=batch, doc_type=doc_type, status=status,
        search=search, limit=limit, offset=offset)
    return jsonify(result)


@closing_books_bp.route('/api/file/<path:file_id>')
def api_file(file_id):
    """Single file with blocks, mappings, duplicate verdicts."""
    eng = _get_engine()
    f = eng.get_file(file_id)
    if not f:
        return jsonify({'error': 'File not found'}), 404
    return jsonify(f)


@closing_books_bp.route('/api/blocks')
def api_blocks():
    """List content blocks with filters."""
    eng = _get_engine()
    file_id = request.args.get('file_id')
    kind = request.args.get('kind')
    has_dollars = request.args.get('has_dollars')
    if has_dollars is not None:
        has_dollars = has_dollars.lower() in ('1', 'true', 'yes')
    search = request.args.get('search', '').strip() or None
    limit = min(int(request.args.get('limit', 100)), 500)
    offset = int(request.args.get('offset', 0))
    result = eng.get_blocks(
        file_id=file_id, kind=kind, has_dollars=has_dollars,
        search=search, limit=limit, offset=offset)
    return jsonify(result)


@closing_books_bp.route('/api/mappings')
def api_mappings():
    """List module mappings."""
    eng = _get_engine()
    module = request.args.get('module')
    file_id = request.args.get('file_id')
    result = eng.get_mappings(module=module, file_id=file_id)
    return jsonify(result)


@closing_books_bp.route('/api/gaps')
def api_gaps():
    """All gap records."""
    eng = _get_engine()
    return jsonify(eng.get_gaps())


@closing_books_bp.route('/api/duplicates')
def api_duplicates():
    """All duplicate verdicts."""
    eng = _get_engine()
    return jsonify(eng.get_duplicates())


@closing_books_bp.route('/api/queries')
def api_queries():
    """All saved search queries."""
    eng = _get_engine()
    return jsonify(eng.get_queries())


@closing_books_bp.route('/api/learnings')
def api_learnings():
    """All batch learnings."""
    eng = _get_engine()
    return jsonify(eng.get_learnings())
