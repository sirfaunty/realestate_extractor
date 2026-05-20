"""
Closing Books Engine — read-only access to the partner's SQLite
document extraction warehouse.

Tables (cumulative across 8 batches):
  file_record       — 468 rows (8 batches: 01_CLOSING_BOOKS, 02_LLC, 03_IDP_COMMS,
                       04_PARTNERSHIP_CONTEXT, 05_VG_MONTHLY_REPORTS, 06_IDP_CORRESPONDENCE,
                       07_VG_WEEKLY_REPORTS, 08_MRI_FINANCIALS)
  content_block     — 2649 rows (extracted text, dollar amounts, dates)
  module_mapping    — 839 rows (block → platform module links)
  gap_record        — 19 rows (expected-but-missing documents)
  duplicate_verdict — 21 rows (cross-file dedup decisions)
  search_query      — 80 rows (saved extraction queries)
  batch_learning    — 85 rows (lessons learned during extraction)
"""

import json
import logging
import os
import sqlite3

logger = logging.getLogger(__name__)

# Default path relative to project root
DEFAULT_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'data', 'chamberlain_warehouse_v3.sqlite')


class ClosingBooksEngine:
    """Read-only interface to the Chamberlain closing-books warehouse."""

    def __init__(self, db_path=None):
        self.db_path = db_path or DEFAULT_DB
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(f'Warehouse not found: {self.db_path}')
        logger.info(f'ClosingBooksEngine: {self.db_path}')

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only = ON')
        return conn

    # ── Files ──────────────────────────────────────────────────────

    def get_files(self, batch=None, doc_type=None, status=None,
                  search=None, limit=100, offset=0):
        """List file records with optional filters."""
        conn = self._conn()
        clauses, params = [], []
        if batch:
            clauses.append('subfolder_batch = ?')
            params.append(batch)
        if doc_type:
            clauses.append('doc_type = ?')
            params.append(doc_type)
        if status:
            clauses.append('ingest_status = ?')
            params.append(status)
        if search:
            clauses.append('(original_filename LIKE ? OR extraction_notes LIKE ?)')
            params.extend([f'%{search}%', f'%{search}%'])
        where = f'WHERE {" AND ".join(clauses)}' if clauses else ''

        total = conn.execute(
            f'SELECT COUNT(*) FROM file_record {where}', params).fetchone()[0]
        rows = conn.execute(
            f'SELECT * FROM file_record {where} ORDER BY original_filename '
            f'LIMIT ? OFFSET ?', params + [limit, offset]).fetchall()
        conn.close()
        return {
            'rows': [dict(r) for r in rows],
            'total': total,
            'limit': limit,
            'offset': offset,
        }

    def get_file(self, file_id):
        """Single file record with its blocks and mappings."""
        conn = self._conn()
        row = conn.execute(
            'SELECT * FROM file_record WHERE id = ?', (file_id,)).fetchone()
        if not row:
            conn.close()
            return None
        file_dict = dict(row)

        blocks = conn.execute(
            'SELECT * FROM content_block WHERE file_id = ? ORDER BY seq',
            (file_id,)).fetchall()
        file_dict['blocks'] = [dict(b) for b in blocks]

        mappings = conn.execute(
            'SELECT * FROM module_mapping WHERE file_id = ? ORDER BY module',
            (file_id,)).fetchall()
        file_dict['mappings'] = [dict(m) for m in mappings]

        # Also find any duplicate verdicts involving this file
        dupes = conn.execute(
            'SELECT * FROM duplicate_verdict WHERE file_id_a = ? OR file_id_b = ?',
            (file_id, file_id)).fetchall()
        file_dict['duplicate_verdicts'] = [dict(d) for d in dupes]

        conn.close()
        return file_dict

    # ── Content blocks ─────────────────────────────────────────────

    def get_blocks(self, file_id=None, kind=None, has_dollars=None,
                   search=None, limit=100, offset=0):
        """List content blocks with optional filters."""
        conn = self._conn()
        clauses, params = [], []
        if file_id:
            clauses.append('file_id = ?')
            params.append(file_id)
        if kind:
            clauses.append('kind = ?')
            params.append(kind)
        if has_dollars is not None:
            clauses.append('contains_dollar_amounts = ?')
            params.append(1 if has_dollars else 0)
        if search:
            clauses.append('(text LIKE ? OR verbatim_excerpt LIKE ?)')
            params.extend([f'%{search}%', f'%{search}%'])
        where = f'WHERE {" AND ".join(clauses)}' if clauses else ''

        total = conn.execute(
            f'SELECT COUNT(*) FROM content_block {where}', params).fetchone()[0]
        rows = conn.execute(
            f'SELECT * FROM content_block {where} ORDER BY file_id, seq '
            f'LIMIT ? OFFSET ?', params + [limit, offset]).fetchall()
        conn.close()
        return {
            'rows': [dict(r) for r in rows],
            'total': total,
            'limit': limit,
            'offset': offset,
        }

    # ── Module mappings ────────────────────────────────────────────

    def get_mappings(self, module=None, file_id=None, limit=200, offset=0):
        """List module mappings."""
        conn = self._conn()
        clauses, params = [], []
        if module:
            clauses.append('module = ?')
            params.append(module)
        if file_id:
            clauses.append('file_id = ?')
            params.append(file_id)
        where = f'WHERE {" AND ".join(clauses)}' if clauses else ''

        total = conn.execute(
            f'SELECT COUNT(*) FROM module_mapping {where}', params).fetchone()[0]
        rows = conn.execute(
            f'SELECT * FROM module_mapping {where} ORDER BY module, role '
            f'LIMIT ? OFFSET ?', params + [limit, offset]).fetchall()
        conn.close()
        return {
            'rows': [dict(r) for r in rows],
            'total': total,
        }

    # ── Gaps ───────────────────────────────────────────────────────

    def get_gaps(self):
        """All gap records."""
        conn = self._conn()
        rows = conn.execute(
            'SELECT * FROM gap_record ORDER BY severity DESC').fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ── Duplicates ─────────────────────────────────────────────────

    def get_duplicates(self):
        """All duplicate verdicts."""
        conn = self._conn()
        rows = conn.execute('SELECT * FROM duplicate_verdict').fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ── Search queries ─────────────────────────────────────────────

    def get_queries(self):
        """All saved search queries."""
        conn = self._conn()
        rows = conn.execute(
            'SELECT * FROM search_query ORDER BY name').fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ── Learnings ──────────────────────────────────────────────────

    def get_learnings(self):
        """All batch learnings."""
        conn = self._conn()
        rows = conn.execute(
            'SELECT * FROM batch_learning ORDER BY batch_id, seq').fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ── Stats / summary ───────────────────────────────────────────

    def get_stats(self):
        """Summary statistics for the dashboard header.

        Consolidated into two queries instead of 10+ sequential COUNT(*)s.
        """
        conn = self._conn()

        # Single query for all scalar counts across tables
        row = conn.execute("""
            SELECT
                (SELECT COUNT(*) FROM file_record) AS total_files,
                (SELECT COUNT(*) FROM file_record
                 WHERE ingest_status = 'extracted') AS extracted_files,
                (SELECT COUNT(*) FROM file_record
                 WHERE ingest_status != 'extracted') AS pending_files,
                (SELECT COUNT(*) FROM content_block) AS total_blocks,
                (SELECT COUNT(*) FROM content_block
                 WHERE contains_dollar_amounts = 1) AS blocks_with_dollars,
                (SELECT COUNT(*) FROM module_mapping) AS total_mappings,
                (SELECT COUNT(*) FROM gap_record) AS total_gaps,
                (SELECT COUNT(*) FROM duplicate_verdict) AS total_duplicates,
                (SELECT COUNT(*) FROM search_query) AS total_queries,
                (SELECT COUNT(*) FROM batch_learning) AS total_learnings
        """).fetchone()

        stats = {
            'total_files': row['total_files'],
            'extracted_files': row['extracted_files'],
            'pending_files': row['pending_files'],
            'total_blocks': row['total_blocks'],
            'blocks_with_dollars': row['blocks_with_dollars'],
            'total_mappings': row['total_mappings'],
            'total_gaps': row['total_gaps'],
            'total_duplicates': row['total_duplicates'],
            'total_queries': row['total_queries'],
            'total_learnings': row['total_learnings'],
        }

        # Group-by breakdowns (still need separate queries for different tables)
        doc_types = conn.execute(
            'SELECT doc_type, COUNT(*) as cnt FROM file_record '
            'GROUP BY doc_type ORDER BY cnt DESC').fetchall()
        stats['doc_types'] = [{'type': r[0], 'count': r[1]} for r in doc_types]

        modules = conn.execute(
            'SELECT module, COUNT(*) as cnt FROM module_mapping '
            'GROUP BY module ORDER BY cnt DESC').fetchall()
        stats['modules'] = [{'module': r[0], 'count': r[1]} for r in modules]

        tiers = conn.execute(
            'SELECT authority_tier, COUNT(*) as cnt FROM file_record '
            'GROUP BY authority_tier ORDER BY cnt DESC').fetchall()
        stats['authority_tiers'] = [{'tier': r[0], 'count': r[1]} for r in tiers]

        batches = conn.execute(
            'SELECT subfolder_batch, COUNT(*) as cnt FROM file_record '
            'GROUP BY subfolder_batch ORDER BY cnt DESC').fetchall()
        stats['batches'] = [{'batch': r[0], 'count': r[1]} for r in batches]

        conn.close()
        return stats
