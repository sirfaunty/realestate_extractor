"""
Usage Tracking and Audit Trail for Capactive Document Extractor.

Tracks:
- Per-org document processing volume (for billing/limits)
- Per-user activity log (who processed what, when)
- Detailed audit trail for compliance
- Volume limit enforcement for tiered pricing

Usage data lives in the central config database alongside
org/user records — separate from the per-org extraction databases.
"""

import sqlite3
import json
from datetime import datetime, timedelta
from typing import Optional, List, Dict
from dataclasses import dataclass


USAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id          TEXT NOT NULL,
    user_id         TEXT,
    action          TEXT NOT NULL,
    document_type   TEXT,
    filename        TEXT,
    page_count      INTEGER,
    processing_time REAL,
    success         BOOLEAN,
    error_message   TEXT,
    metadata        TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS usage_monthly (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id          TEXT NOT NULL,
    period          TEXT NOT NULL,
    documents_processed INTEGER DEFAULT 0,
    pages_processed INTEGER DEFAULT 0,
    ocr_pages       INTEGER DEFAULT 0,
    llm_calls       INTEGER DEFAULT 0,
    total_processing_time REAL DEFAULT 0,
    terms_extracted INTEGER DEFAULT 0,
    clauses_extracted INTEGER DEFAULT 0,
    tabular_rows_extracted INTEGER DEFAULT 0,
    UNIQUE(org_id, period)
);

CREATE INDEX IF NOT EXISTS idx_usage_log_org ON usage_log(org_id);
CREATE INDEX IF NOT EXISTS idx_usage_log_user ON usage_log(user_id);
CREATE INDEX IF NOT EXISTS idx_usage_log_date ON usage_log(created_at);
CREATE INDEX IF NOT EXISTS idx_usage_monthly_org ON usage_monthly(org_id, period);
"""


@dataclass
class UsageEvent:
    """A single usage event to be logged."""
    org_id: str
    action: str                    # process_document, batch_process, search, export, login
    user_id: str = None
    document_type: str = None
    filename: str = None
    page_count: int = None
    processing_time: float = None
    success: bool = True
    error_message: str = None
    metadata: Dict = None


class UsageTracker:
    """
    Tracks usage per organization and user.

    Integrates with the central config database to store usage
    alongside org/user records.
    """

    def __init__(self, db_path: str = "capactive_config.db"):
        self.db_path = db_path
        self.conn = None

    def connect(self):
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(USAGE_SCHEMA)
        self.conn.commit()

    def close(self):
        if self.conn:
            self.conn.close()

    # ─── Event Logging ───────────────────────────────────────────────

    def log_event(self, event: UsageEvent):
        """Log a usage event and update monthly aggregates."""
        # Insert detailed log
        self.conn.execute("""
            INSERT INTO usage_log (org_id, user_id, action, document_type,
                                   filename, page_count, processing_time,
                                   success, error_message, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (event.org_id, event.user_id, event.action, event.document_type,
              event.filename, event.page_count, event.processing_time,
              event.success, event.error_message,
              json.dumps(event.metadata) if event.metadata else None))

        # Update monthly aggregates for document processing
        if event.action == 'process_document' and event.success:
            period = datetime.now().strftime('%Y-%m')
            self._update_monthly(event.org_id, period, event)

        self.conn.commit()

    def log_document_processed(self, org_id: str, user_id: str,
                                filename: str, document_type: str,
                                page_count: int, processing_time: float,
                                terms_count: int = 0, clauses_count: int = 0,
                                tabular_rows: int = 0, is_ocr: bool = False,
                                used_llm: bool = False, success: bool = True,
                                error: str = None):
        """Convenience method for logging a document processing event."""
        event = UsageEvent(
            org_id=org_id,
            user_id=user_id,
            action='process_document',
            document_type=document_type,
            filename=filename,
            page_count=page_count,
            processing_time=processing_time,
            success=success,
            error_message=error,
            metadata={
                'terms_extracted': terms_count,
                'clauses_extracted': clauses_count,
                'tabular_rows': tabular_rows,
                'is_ocr': is_ocr,
                'used_llm': used_llm,
            }
        )
        self.log_event(event)

    # ─── Monthly Aggregates ──────────────────────────────────────────

    def _update_monthly(self, org_id: str, period: str, event: UsageEvent):
        """Update monthly usage aggregates."""
        # Upsert monthly record
        self.conn.execute("""
            INSERT INTO usage_monthly (org_id, period, documents_processed,
                                       pages_processed, total_processing_time)
            VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(org_id, period) DO UPDATE SET
                documents_processed = documents_processed + 1,
                pages_processed = pages_processed + ?,
                total_processing_time = total_processing_time + ?
        """, (org_id, period,
              event.page_count or 0, event.processing_time or 0,
              event.page_count or 0, event.processing_time or 0))

        # Update extraction counts from metadata
        if event.metadata:
            terms = event.metadata.get('terms_extracted', 0)
            clauses = event.metadata.get('clauses_extracted', 0)
            rows = event.metadata.get('tabular_rows', 0)
            is_ocr = event.metadata.get('is_ocr', False)
            used_llm = event.metadata.get('used_llm', False)

            updates = []
            params = []
            if terms:
                updates.append("terms_extracted = terms_extracted + ?")
                params.append(terms)
            if clauses:
                updates.append("clauses_extracted = clauses_extracted + ?")
                params.append(clauses)
            if rows:
                updates.append("tabular_rows_extracted = tabular_rows_extracted + ?")
                params.append(rows)
            if is_ocr:
                updates.append("ocr_pages = ocr_pages + ?")
                params.append(event.page_count or 0)
            if used_llm:
                updates.append("llm_calls = llm_calls + 1")

            if updates:
                params.extend([org_id, period])
                self.conn.execute(
                    f"UPDATE usage_monthly SET {', '.join(updates)} "
                    f"WHERE org_id = ? AND period = ?",
                    params)

    # ─���─ Usage Queries ───────────────────────────────────────────────

    def get_monthly_usage(self, org_id: str,
                          period: str = None) -> Optional[Dict]:
        """Get monthly usage for an org. Defaults to current month."""
        if not period:
            period = datetime.now().strftime('%Y-%m')

        cur = self.conn.execute(
            "SELECT * FROM usage_monthly WHERE org_id = ? AND period = ?",
            (org_id, period))
        row = cur.fetchone()
        return dict(row) if row else {
            'org_id': org_id,
            'period': period,
            'documents_processed': 0,
            'pages_processed': 0,
            'ocr_pages': 0,
            'llm_calls': 0,
            'total_processing_time': 0,
            'terms_extracted': 0,
            'clauses_extracted': 0,
            'tabular_rows_extracted': 0,
        }

    def get_usage_history(self, org_id: str,
                          months: int = 12) -> List[Dict]:
        """Get usage history for the last N months."""
        cur = self.conn.execute("""
            SELECT * FROM usage_monthly
            WHERE org_id = ?
            ORDER BY period DESC
            LIMIT ?
        """, (org_id, months))
        return [dict(row) for row in cur.fetchall()]

    def get_user_activity(self, org_id: str, user_id: str = None,
                          days: int = 30, limit: int = 100) -> List[Dict]:
        """Get recent activity log entries."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        query = """
            SELECT * FROM usage_log
            WHERE org_id = ? AND created_at >= ?
        """
        params = [org_id, cutoff]

        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        cur = self.conn.execute(query, params)
        return [dict(row) for row in cur.fetchall()]

    def get_org_summary(self, org_id: str) -> Dict:
        """Get a comprehensive usage summary for an org."""
        current_month = self.get_monthly_usage(org_id)
        history = self.get_usage_history(org_id, months=6)

        # Total all-time
        cur = self.conn.execute("""
            SELECT COUNT(*) as total_events,
                   SUM(CASE WHEN action = 'process_document' AND success = 1 THEN 1 ELSE 0 END) as total_documents,
                   COUNT(DISTINCT user_id) as active_users
            FROM usage_log
            WHERE org_id = ?
        """, (org_id,))
        totals = dict(cur.fetchone())

        return {
            'current_month': current_month,
            'history': history,
            'all_time': totals,
        }

    # ─── Volume Limit Enforcement ────────────────────────────────────

    def check_volume_limit(self, org_id: str,
                            monthly_limit: int) -> tuple:
        """
        Check if an org has reached their monthly document limit.

        Returns (allowed, current_count, limit, message).
        """
        usage = self.get_monthly_usage(org_id)
        current = usage['documents_processed']

        if current >= monthly_limit:
            return (
                False, current, monthly_limit,
                f"Monthly document limit reached ({current}/{monthly_limit}). "
                f"Upgrade your plan or wait until next month."
            )

        remaining = monthly_limit - current
        return (True, current, monthly_limit,
                f"{remaining} documents remaining this month")

    # ─── Audit Trail ─────────────────────────────────────────────────

    def get_audit_trail(self, org_id: str, start_date: str = None,
                         end_date: str = None, user_id: str = None,
                         action: str = None,
                         limit: int = 500) -> List[Dict]:
        """
        Get detailed audit trail with optional filters.
        Designed for compliance and review purposes.
        """
        query = "SELECT * FROM usage_log WHERE org_id = ?"
        params = [org_id]

        if start_date:
            query += " AND created_at >= ?"
            params.append(start_date)
        if end_date:
            query += " AND created_at <= ?"
            params.append(end_date)
        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        if action:
            query += " AND action = ?"
            params.append(action)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        cur = self.conn.execute(query, params)
        return [dict(row) for row in cur.fetchall()]

    def export_audit_trail(self, org_id: str, filepath: str,
                            start_date: str = None,
                            end_date: str = None) -> int:
        """Export audit trail to CSV."""
        import csv

        records = self.get_audit_trail(
            org_id, start_date=start_date, end_date=end_date, limit=99999)

        if not records:
            return 0

        with open(filepath, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=records[0].keys())
            writer.writeheader()
            writer.writerows(records)

        return len(records)
