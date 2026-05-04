"""
SQLite database layer for Real Estate Document Extractor.

All extracted data flows into a single local SQLite database file.
Supports full-text search across document content and structured
queries across financial terms, clauses, and tabular data.
"""

import sqlite3
import json
import os
import re
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
from difflib import SequenceMatcher


# ─── Schema Definition ───────────────────────────────────────────────

SCHEMA_SQL = """
-- ─── Asset Hierarchy ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS portfolios (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    description     TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata        TEXT                    -- JSON blob
);

CREATE TABLE IF NOT EXISTS properties (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id    INTEGER REFERENCES portfolios(id),
    name            TEXT NOT NULL,
    address         TEXT,
    city            TEXT,
    state           TEXT,
    zip_code        TEXT,
    property_type   TEXT NOT NULL DEFAULT 'multifamily',  -- multifamily, industrial, commercial, office, retail, mixed_use
    year_built      INTEGER,
    total_units     INTEGER,
    total_sqft      REAL,
    acquisition_date TEXT,
    acquisition_price REAL,
    status          TEXT DEFAULT 'active',  -- active, disposed, under_contract, pipeline
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata        TEXT
);

CREATE TABLE IF NOT EXISTS buildings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id     INTEGER NOT NULL REFERENCES properties(id),
    name            TEXT NOT NULL,          -- e.g., "Building A", "North Tower"
    address         TEXT,                   -- if different from property
    floors          INTEGER,
    total_units     INTEGER,
    total_sqft      REAL,
    year_built      INTEGER,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata        TEXT
);

CREATE TABLE IF NOT EXISTS units (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    building_id     INTEGER NOT NULL REFERENCES buildings(id),
    property_id     INTEGER NOT NULL REFERENCES properties(id),
    unit_number     TEXT NOT NULL,
    unit_type       TEXT,                   -- 1BR, 2BR, studio, office, warehouse, retail, etc.
    square_footage  REAL,
    floor           INTEGER,
    bedrooms        REAL,                   -- 0.5 for studio, etc.
    bathrooms       REAL,
    status          TEXT DEFAULT 'vacant',  -- occupied, vacant, down, model, employee
    current_tenant  TEXT,
    current_rent    REAL,
    market_rent     REAL,
    lease_start     TEXT,
    lease_end       TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata        TEXT
);

-- ─── Core Document Registry ────────────────────────────────────────

CREATE TABLE IF NOT EXISTS documents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    filename        TEXT NOT NULL,
    filepath        TEXT NOT NULL,
    document_type   TEXT NOT NULL,          -- lease, loan, closing, guarantee, rent_roll, operating_statement, general_ledger
    property_name   TEXT,
    property_address TEXT,
    property_id     INTEGER REFERENCES properties(id),
    building_id     INTEGER REFERENCES buildings(id),
    unit_id         INTEGER REFERENCES units(id),
    portfolio_id    INTEGER REFERENCES portfolios(id),
    page_count      INTEGER,
    is_scanned      BOOLEAN DEFAULT 0,
    ocr_confidence  REAL,                   -- avg OCR confidence score (0-100)
    processed_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    file_hash       TEXT,                   -- SHA-256 to detect duplicates
    review_status   TEXT DEFAULT 'pending_review',  -- pending_review, approved, skipped
    metadata        TEXT                    -- JSON blob for extra fields
);

-- Full text content for search
CREATE VIRTUAL TABLE IF NOT EXISTS document_fulltext USING fts5(
    document_id,
    page_number,
    content,
    tokenize='porter unicode61'
);

-- ─── Legal Clause Extraction (narrative documents) ──────────────────

CREATE TABLE IF NOT EXISTS clauses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id     INTEGER NOT NULL REFERENCES documents(id),
    clause_type     TEXT NOT NULL,          -- e.g., assignment, subletting, default, insurance, estoppel, subordination, permitted_use, maintenance, indemnification
    section_ref     TEXT,                   -- e.g., "Section 12.3(a)"
    clause_title    TEXT,                   -- heading if present
    full_text       TEXT NOT NULL,          -- complete clause language preserved
    summary         TEXT,                   -- LLM-generated plain-language summary
    page_number     INTEGER,
    confidence      REAL,                   -- extraction confidence (0-1)
    metadata        TEXT                    -- JSON blob
);

-- ─── Financial Term Extraction (structured data from leases/loans) ──

CREATE TABLE IF NOT EXISTS financial_terms (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id     INTEGER NOT NULL REFERENCES documents(id),
    term_type       TEXT NOT NULL,          -- base_rent, escalation, cam_charges, percentage_rent, ti_allowance, free_rent, security_deposit, interest_rate, principal, maturity_date, etc.
    term_label      TEXT,                   -- descriptive label from document
    value_raw       TEXT,                   -- original text as it appeared
    value_numeric   REAL,                   -- normalized numeric value
    value_unit      TEXT,                   -- monthly, annual, psf, percentage, etc.
    effective_date  TEXT,                   -- ISO date string
    expiration_date TEXT,
    escalation_type TEXT,                   -- fixed, cpi, percentage, step
    escalation_detail TEXT,                 -- e.g., "3% annual increase"
    section_ref     TEXT,
    page_number     INTEGER,
    confidence      REAL,
    metadata        TEXT
);

-- ─── Rent Roll Entries ──────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS rent_roll_entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id     INTEGER NOT NULL REFERENCES documents(id),
    property_id     INTEGER REFERENCES properties(id),
    unit_id         INTEGER REFERENCES units(id),
    property_name   TEXT,
    unit_number     TEXT,
    tenant_name     TEXT,
    suite           TEXT,
    square_footage  REAL,
    lease_start     TEXT,
    lease_end       TEXT,
    monthly_rent    REAL,
    annual_rent     REAL,
    rent_psf        REAL,
    status          TEXT,                   -- occupied, vacant, month-to-month, etc.
    notes           TEXT,
    page_number     INTEGER,
    metadata        TEXT
);

-- ─── Operating Statement Items ──────────────────────────────────────

CREATE TABLE IF NOT EXISTS operating_statement_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id     INTEGER NOT NULL REFERENCES documents(id),
    property_id     INTEGER REFERENCES properties(id),
    property_name   TEXT,
    period          TEXT,                   -- e.g., "2024", "Q1 2024", "Jan 2024"
    category        TEXT NOT NULL,          -- revenue, expense, noi, debt_service, etc.
    subcategory     TEXT,                   -- e.g., "property_tax", "insurance", "repairs"
    line_item       TEXT NOT NULL,          -- exact line item description
    amount          REAL,
    amount_psf      REAL,
    is_subtotal     BOOLEAN DEFAULT 0,
    is_total        BOOLEAN DEFAULT 0,
    page_number     INTEGER,
    metadata        TEXT
);

-- ─── General Ledger Entries ─────────────────────────────────────────

CREATE TABLE IF NOT EXISTS gl_entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id     INTEGER NOT NULL REFERENCES documents(id),
    property_id     INTEGER REFERENCES properties(id),
    property_name   TEXT,
    account_code    TEXT,
    account_name    TEXT,
    entry_date      TEXT,
    description     TEXT,
    debit           REAL,
    credit          REAL,
    balance         REAL,
    period          TEXT,
    vendor          TEXT,
    reference       TEXT,                   -- check number, invoice number, etc.
    page_number     INTEGER,
    metadata        TEXT
);

-- ─── Indexes ────────────────────────────────────────────────────────

-- Asset hierarchy indexes
CREATE INDEX IF NOT EXISTS idx_properties_portfolio ON properties(portfolio_id);
CREATE INDEX IF NOT EXISTS idx_properties_type ON properties(property_type);
CREATE INDEX IF NOT EXISTS idx_buildings_property ON buildings(property_id);
CREATE INDEX IF NOT EXISTS idx_units_building ON units(building_id);
CREATE INDEX IF NOT EXISTS idx_units_property ON units(property_id);
CREATE INDEX IF NOT EXISTS idx_units_status ON units(status);

-- Document indexes
CREATE INDEX IF NOT EXISTS idx_documents_type ON documents(document_type);
CREATE INDEX IF NOT EXISTS idx_documents_property ON documents(property_name);
CREATE INDEX IF NOT EXISTS idx_documents_property_id ON documents(property_id);
CREATE INDEX IF NOT EXISTS idx_clauses_doc ON clauses(document_id);
CREATE INDEX IF NOT EXISTS idx_clauses_type ON clauses(clause_type);
CREATE INDEX IF NOT EXISTS idx_financial_terms_doc ON financial_terms(document_id);
CREATE INDEX IF NOT EXISTS idx_financial_terms_type ON financial_terms(term_type);
CREATE INDEX IF NOT EXISTS idx_rent_roll_doc ON rent_roll_entries(document_id);
CREATE INDEX IF NOT EXISTS idx_rent_roll_tenant ON rent_roll_entries(tenant_name);
CREATE INDEX IF NOT EXISTS idx_opstat_doc ON operating_statement_items(document_id);
CREATE INDEX IF NOT EXISTS idx_opstat_category ON operating_statement_items(category);
CREATE INDEX IF NOT EXISTS idx_gl_doc ON gl_entries(document_id);
CREATE INDEX IF NOT EXISTS idx_gl_account ON gl_entries(account_code);
CREATE INDEX IF NOT EXISTS idx_gl_date ON gl_entries(entry_date);
"""


class Database:
    """Local SQLite database for all extracted real estate document data."""

    def __init__(self, db_path: str = "realestate_extractions.db"):
        self.db_path = db_path
        self.conn = None

    def connect(self):
        """Open connection and initialize schema."""
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")      # better concurrent reads
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._create_schema()

    def _create_schema(self):
        """Create all tables and indexes if they don't exist."""
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()

    def close(self):
        if self.conn:
            self.conn.close()

    # ─── Portfolio Operations ────────────────────────────────────────

    def create_portfolio(self, name: str, description: str = None,
                         metadata: dict = None) -> int:
        cur = self.conn.execute("""
            INSERT INTO portfolios (name, description, metadata)
            VALUES (?, ?, ?)
        """, (name, description, json.dumps(metadata) if metadata else None))
        self.conn.commit()
        return cur.lastrowid

    def get_portfolio(self, portfolio_id: int) -> Optional[Dict]:
        cur = self.conn.execute("SELECT * FROM portfolios WHERE id = ?", (portfolio_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def list_portfolios(self) -> List[Dict]:
        cur = self.conn.execute("SELECT * FROM portfolios ORDER BY name")
        return [dict(row) for row in cur.fetchall()]

    def update_portfolio(self, portfolio_id: int, **kwargs):
        allowed = ['name', 'description', 'metadata']
        updates, values = [], []
        for key, value in kwargs.items():
            if key in allowed:
                if key == 'metadata':
                    value = json.dumps(value)
                updates.append(f"{key} = ?")
                values.append(value)
        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            values.append(portfolio_id)
            self.conn.execute(
                f"UPDATE portfolios SET {', '.join(updates)} WHERE id = ?", values)
            self.conn.commit()

    def delete_portfolio(self, portfolio_id: int):
        """Unlink properties from portfolio, then delete it."""
        self.conn.execute(
            "UPDATE properties SET portfolio_id = NULL WHERE portfolio_id = ?",
            (portfolio_id,))
        self.conn.execute("DELETE FROM portfolios WHERE id = ?", (portfolio_id,))
        self.conn.commit()

    def get_portfolio_with_properties(self, portfolio_id: int) -> Optional[Dict]:
        """Get portfolio with all its properties."""
        portfolio = self.get_portfolio(portfolio_id)
        if not portfolio:
            return None
        portfolio['properties'] = self.list_properties(portfolio_id=portfolio_id)
        return portfolio

    # ─── Property Operations ─────────────────────────────────────────

    def create_property(self, name: str, property_type: str = 'multifamily',
                        portfolio_id: int = None, address: str = None,
                        city: str = None, state: str = None, zip_code: str = None,
                        year_built: int = None, total_units: int = None,
                        total_sqft: float = None, acquisition_date: str = None,
                        acquisition_price: float = None, status: str = 'active',
                        metadata: dict = None) -> int:
        cur = self.conn.execute("""
            INSERT INTO properties (name, property_type, portfolio_id, address,
                                     city, state, zip_code, year_built, total_units,
                                     total_sqft, acquisition_date, acquisition_price,
                                     status, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (name, property_type, portfolio_id, address, city, state, zip_code,
              year_built, total_units, total_sqft, acquisition_date, acquisition_price,
              status, json.dumps(metadata) if metadata else None))
        self.conn.commit()
        return cur.lastrowid

    def get_property(self, property_id: int) -> Optional[Dict]:
        cur = self.conn.execute("""
            SELECT p.*, pf.name as portfolio_name
            FROM properties p
            LEFT JOIN portfolios pf ON p.portfolio_id = pf.id
            WHERE p.id = ?
        """, (property_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def list_properties(self, portfolio_id: int = None,
                        property_type: str = None,
                        status: str = None) -> List[Dict]:
        query = """
            SELECT p.*, pf.name as portfolio_name,
                   (SELECT COUNT(*) FROM buildings WHERE property_id = p.id) as building_count,
                   (SELECT COUNT(*) FROM units WHERE property_id = p.id) as unit_count,
                   (SELECT COUNT(*) FROM documents WHERE property_id = p.id) as document_count
            FROM properties p
            LEFT JOIN portfolios pf ON p.portfolio_id = pf.id
            WHERE 1=1
        """
        params = []
        if portfolio_id:
            query += " AND p.portfolio_id = ?"
            params.append(portfolio_id)
        if property_type:
            query += " AND p.property_type = ?"
            params.append(property_type)
        if status:
            query += " AND p.status = ?"
            params.append(status)
        query += " ORDER BY p.name"
        cur = self.conn.execute(query, params)
        return [dict(row) for row in cur.fetchall()]

    def update_property(self, property_id: int, **kwargs):
        allowed = ['name', 'property_type', 'portfolio_id', 'address', 'city',
                    'state', 'zip_code', 'year_built', 'total_units', 'total_sqft',
                    'acquisition_date', 'acquisition_price', 'status', 'metadata']
        updates, values = [], []
        for key, value in kwargs.items():
            if key in allowed:
                if key == 'metadata':
                    value = json.dumps(value)
                updates.append(f"{key} = ?")
                values.append(value)
        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            values.append(property_id)
            self.conn.execute(
                f"UPDATE properties SET {', '.join(updates)} WHERE id = ?", values)
            self.conn.commit()

    # ─── Building Operations ─────────────────────────────────────────

    def create_building(self, property_id: int, name: str,
                        address: str = None, floors: int = None,
                        total_units: int = None, total_sqft: float = None,
                        year_built: int = None, metadata: dict = None) -> int:
        cur = self.conn.execute("""
            INSERT INTO buildings (property_id, name, address, floors,
                                    total_units, total_sqft, year_built, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (property_id, name, address, floors, total_units, total_sqft,
              year_built, json.dumps(metadata) if metadata else None))
        self.conn.commit()
        return cur.lastrowid

    def get_building(self, building_id: int) -> Optional[Dict]:
        cur = self.conn.execute("""
            SELECT b.*, p.name as property_name
            FROM buildings b
            JOIN properties p ON b.property_id = p.id
            WHERE b.id = ?
        """, (building_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def list_buildings(self, property_id: int) -> List[Dict]:
        cur = self.conn.execute("""
            SELECT b.*,
                   (SELECT COUNT(*) FROM units WHERE building_id = b.id) as unit_count
            FROM buildings b
            WHERE b.property_id = ?
            ORDER BY b.name
        """, (property_id,))
        return [dict(row) for row in cur.fetchall()]

    def update_building(self, building_id: int, **kwargs):
        allowed = ['name', 'address', 'floors', 'total_units', 'total_sqft',
                    'year_built', 'metadata']
        updates, values = [], []
        for key, value in kwargs.items():
            if key in allowed:
                if key == 'metadata':
                    value = json.dumps(value)
                updates.append(f"{key} = ?")
                values.append(value)
        if updates:
            values.append(building_id)
            self.conn.execute(
                f"UPDATE buildings SET {', '.join(updates)} WHERE id = ?", values)
            self.conn.commit()

    # ─── Unit Operations ─────────────────────────────────────────────

    def create_unit(self, building_id: int, property_id: int, unit_number: str,
                    unit_type: str = None, square_footage: float = None,
                    floor: int = None, bedrooms: float = None,
                    bathrooms: float = None, status: str = 'vacant',
                    current_tenant: str = None, current_rent: float = None,
                    market_rent: float = None, lease_start: str = None,
                    lease_end: str = None, metadata: dict = None) -> int:
        cur = self.conn.execute("""
            INSERT INTO units (building_id, property_id, unit_number, unit_type,
                               square_footage, floor, bedrooms, bathrooms,
                               status, current_tenant, current_rent, market_rent,
                               lease_start, lease_end, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (building_id, property_id, unit_number, unit_type, square_footage,
              floor, bedrooms, bathrooms, status, current_tenant, current_rent,
              market_rent, lease_start, lease_end,
              json.dumps(metadata) if metadata else None))
        self.conn.commit()
        return cur.lastrowid

    def get_unit(self, unit_id: int) -> Optional[Dict]:
        cur = self.conn.execute("""
            SELECT u.*, b.name as building_name, p.name as property_name
            FROM units u
            JOIN buildings b ON u.building_id = b.id
            JOIN properties p ON u.property_id = p.id
            WHERE u.id = ?
        """, (unit_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def list_units(self, property_id: int = None, building_id: int = None,
                   status: str = None) -> List[Dict]:
        query = """
            SELECT u.*, b.name as building_name
            FROM units u
            JOIN buildings b ON u.building_id = b.id
            WHERE 1=1
        """
        params = []
        if property_id:
            query += " AND u.property_id = ?"
            params.append(property_id)
        if building_id:
            query += " AND u.building_id = ?"
            params.append(building_id)
        if status:
            query += " AND u.status = ?"
            params.append(status)
        query += " ORDER BY b.name, u.unit_number"
        cur = self.conn.execute(query, params)
        return [dict(row) for row in cur.fetchall()]

    def update_unit(self, unit_id: int, **kwargs):
        allowed = ['unit_number', 'unit_type', 'square_footage', 'floor',
                    'bedrooms', 'bathrooms', 'status', 'current_tenant',
                    'current_rent', 'market_rent', 'lease_start', 'lease_end',
                    'metadata']
        updates, values = [], []
        for key, value in kwargs.items():
            if key in allowed:
                if key == 'metadata':
                    value = json.dumps(value)
                updates.append(f"{key} = ?")
                values.append(value)
        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            values.append(unit_id)
            self.conn.execute(
                f"UPDATE units SET {', '.join(updates)} WHERE id = ?", values)
            self.conn.commit()

    # ─── Document-to-Asset Linking ───────────────────────────────────

    def link_document_to_property(self, doc_id: int, property_id: int,
                                  building_id: int = None, unit_id: int = None):
        """Link an existing document to a property (and optionally building/unit)."""
        # Also resolve portfolio_id from property
        prop = self.get_property(property_id)
        portfolio_id = prop['portfolio_id'] if prop else None

        self.conn.execute("""
            UPDATE documents
            SET property_id = ?, building_id = ?, unit_id = ?, portfolio_id = ?
            WHERE id = ?
        """, (property_id, building_id, unit_id, portfolio_id, doc_id))
        self.conn.commit()

    def get_property_documents(self, property_id: int,
                               document_type: str = None) -> List[Dict]:
        """Get all documents linked to a property."""
        query = "SELECT * FROM documents WHERE property_id = ?"
        params = [property_id]
        if document_type:
            query += " AND document_type = ?"
            params.append(document_type)
        query += " ORDER BY processed_at DESC"
        cur = self.conn.execute(query, params)
        return [dict(row) for row in cur.fetchall()]

    # ─── Property Resolution / Review Queue ────────────────────────

    @staticmethod
    def _normalize(text: str) -> str:
        """Normalize text for fuzzy matching — lowercase, strip noise."""
        if not text:
            return ''
        text = text.lower().strip()
        # Remove common CRE suffixes/prefixes that shouldn't affect matching
        noise = ['llc', 'lp', 'inc', 'corp', 'the', 'at', 'property', 'properties']
        words = text.split()
        words = [w for w in words if w not in noise]
        # Remove punctuation
        text = re.sub(r'[^\w\s]', '', ' '.join(words))
        return ' '.join(text.split())

    @staticmethod
    def _similarity(a: str, b: str) -> float:
        """String similarity ratio (0-1) using SequenceMatcher."""
        if not a or not b:
            return 0.0
        return SequenceMatcher(None, a, b).ratio()

    def match_property(self, property_name: str = None,
                       property_address: str = None,
                       city: str = None, state: str = None,
                       threshold: float = 0.4) -> List[Dict]:
        """
        Fuzzy-match extracted property info against existing properties.

        Returns ranked list of candidates:
        [{'property': {...}, 'score': 0.85, 'match_details': {...}}, ...]

        Scoring:
        - Name similarity: up to 50 points
        - Address similarity: up to 30 points
        - City+State match: up to 20 points
        """
        properties = self.list_properties()
        if not properties:
            return []

        norm_name = self._normalize(property_name)
        norm_addr = self._normalize(property_address)
        norm_city = (city or '').lower().strip()
        norm_state = (state or '').lower().strip()

        # Also try to extract city/state from address string
        if not norm_city and property_address:
            # Simple heuristic: last comma-separated segment might be "City, ST ZIP"
            parts = property_address.split(',')
            if len(parts) >= 2:
                city_candidate = parts[-2].strip() if len(parts) >= 3 else ''
                state_zip = parts[-1].strip().split()
                if state_zip:
                    if not norm_state and len(state_zip[0]) == 2:
                        norm_state = state_zip[0].lower()
                    if not norm_city and city_candidate:
                        norm_city = city_candidate.lower()

        candidates = []
        for prop in properties:
            score = 0
            details = {}

            # Name matching (50 pts max)
            if norm_name:
                prop_norm = self._normalize(prop['name'])
                name_sim = self._similarity(norm_name, prop_norm)
                # Boost exact substring matches
                if norm_name in prop_norm or prop_norm in norm_name:
                    name_sim = max(name_sim, 0.85)
                name_score = name_sim * 50
                score += name_score
                details['name_similarity'] = round(name_sim, 2)

            # Address matching (30 pts max)
            if norm_addr:
                prop_addr = self._normalize(prop.get('address') or '')
                addr_sim = self._similarity(norm_addr, prop_addr)
                # Check if street number matches
                norm_nums = re.findall(r'\d+', norm_addr)
                prop_nums = re.findall(r'\d+', prop_addr)
                if norm_nums and prop_nums and norm_nums[0] == prop_nums[0]:
                    addr_sim = max(addr_sim, 0.6)
                addr_score = addr_sim * 30
                score += addr_score
                details['address_similarity'] = round(addr_sim, 2)

            # City + State (20 pts max)
            geo_score = 0
            if norm_city and prop.get('city'):
                city_sim = self._similarity(norm_city, prop['city'].lower())
                geo_score += city_sim * 12
                details['city_match'] = round(city_sim, 2)
            if norm_state and prop.get('state'):
                if norm_state == prop['state'].lower():
                    geo_score += 8
                    details['state_match'] = True
            score += geo_score

            # Normalize to 0-1 scale
            normalized_score = score / 100

            if normalized_score >= threshold:
                candidates.append({
                    'property': prop,
                    'score': round(normalized_score, 3),
                    'match_details': details,
                })

        # Sort by score descending
        candidates.sort(key=lambda x: x['score'], reverse=True)
        return candidates[:5]  # Top 5 matches

    def get_review_queue(self, limit: int = 50) -> List[Dict]:
        """Get documents pending property review."""
        cur = self.conn.execute("""
            SELECT * FROM documents
            WHERE review_status = 'pending_review'
            ORDER BY processed_at DESC
            LIMIT ?
        """, (limit,))
        docs = [dict(row) for row in cur.fetchall()]

        # Enrich each document with match suggestions
        for doc in docs:
            doc['suggested_matches'] = self.match_property(
                property_name=doc.get('property_name'),
                property_address=doc.get('property_address'),
            )
            if doc['suggested_matches']:
                best = doc['suggested_matches'][0]
                doc['best_match'] = best['property']
                doc['best_score'] = best['score']
                doc['confidence'] = (
                    'high' if best['score'] >= 0.7 else
                    'medium' if best['score'] >= 0.5 else
                    'low'
                )
            else:
                doc['best_match'] = None
                doc['best_score'] = 0
                doc['confidence'] = None

        return docs

    def get_review_count(self) -> int:
        """Get count of documents pending review."""
        cur = self.conn.execute(
            "SELECT COUNT(*) as count FROM documents WHERE review_status = 'pending_review'")
        return cur.fetchone()['count']

    def approve_document_match(self, doc_id: int, property_id: int,
                               building_id: int = None, unit_id: int = None):
        """Approve a property match for a document."""
        self.link_document_to_property(doc_id, property_id, building_id, unit_id)
        self.conn.execute(
            "UPDATE documents SET review_status = 'approved' WHERE id = ?",
            (doc_id,))
        self.conn.commit()

    def skip_document_review(self, doc_id: int):
        """Skip review for a document (can revisit later)."""
        self.conn.execute(
            "UPDATE documents SET review_status = 'skipped' WHERE id = ?",
            (doc_id,))
        self.conn.commit()

    def reset_document_review(self, doc_id: int):
        """Put a skipped document back in the review queue."""
        self.conn.execute(
            "UPDATE documents SET review_status = 'pending_review' WHERE id = ?",
            (doc_id,))
        self.conn.commit()

    # ─── Property-Level Aggregations (Layer 2 data) ──────────────────

    def get_property_operations_summary(self, property_id: int) -> Dict:
        """Operations bucket: tenants, rent, lease terms, occupancy."""
        summary = {}

        # Unit/occupancy stats
        cur = self.conn.execute("""
            SELECT COUNT(*) as total_units,
                   SUM(CASE WHEN status = 'occupied' THEN 1 ELSE 0 END) as occupied,
                   SUM(CASE WHEN status = 'vacant' THEN 1 ELSE 0 END) as vacant,
                   SUM(square_footage) as total_sqft,
                   SUM(current_rent) as total_monthly_rent,
                   AVG(current_rent) as avg_rent,
                   SUM(market_rent) as total_market_rent
            FROM units WHERE property_id = ?
        """, (property_id,))
        row = cur.fetchone()
        summary['units'] = dict(row) if row else {}

        # Calculate occupancy rate
        total = summary['units'].get('total_units') or 0
        occupied = summary['units'].get('occupied') or 0
        summary['occupancy_rate'] = round((occupied / total * 100), 1) if total > 0 else 0

        # Rent roll from latest document
        cur = self.conn.execute("""
            SELECT rr.* FROM rent_roll_entries rr
            JOIN documents d ON rr.document_id = d.id
            WHERE d.property_id = ?
            ORDER BY d.processed_at DESC
        """, (property_id,))
        summary['rent_roll'] = [dict(row) for row in cur.fetchall()]

        # Lease expirations (units with lease_end dates)
        cur = self.conn.execute("""
            SELECT unit_number, current_tenant, lease_end, current_rent
            FROM units
            WHERE property_id = ? AND lease_end IS NOT NULL
            ORDER BY lease_end
        """, (property_id,))
        summary['lease_expirations'] = [dict(row) for row in cur.fetchall()]

        # Operating expenses from latest operating statement
        cur = self.conn.execute("""
            SELECT os.* FROM operating_statement_items os
            JOIN documents d ON os.document_id = d.id
            WHERE d.property_id = ? AND os.category = 'expense'
            ORDER BY d.processed_at DESC, os.id
        """, (property_id,))
        summary['operating_expenses'] = [dict(row) for row in cur.fetchall()]

        return summary

    def get_property_debt_summary(self, property_id: int) -> Dict:
        """Debt bucket: loan terms, guarantees, covenants."""
        summary = {}

        # Financial terms from loan documents
        cur = self.conn.execute("""
            SELECT ft.* FROM financial_terms ft
            JOIN documents d ON ft.document_id = d.id
            WHERE d.property_id = ? AND d.document_type IN ('loan', 'closing')
            ORDER BY d.processed_at DESC
        """, (property_id,))
        summary['loan_terms'] = [dict(row) for row in cur.fetchall()]

        # Clauses from loan and guarantee documents
        cur = self.conn.execute("""
            SELECT c.*, d.document_type, d.filename FROM clauses c
            JOIN documents d ON c.document_id = d.id
            WHERE d.property_id = ? AND d.document_type IN ('loan', 'guarantee')
            ORDER BY d.document_type, c.clause_type
        """, (property_id,))
        summary['loan_clauses'] = [dict(row) for row in cur.fetchall()]

        # Guarantee documents
        cur = self.conn.execute("""
            SELECT * FROM documents
            WHERE property_id = ? AND document_type = 'guarantee'
            ORDER BY processed_at DESC
        """, (property_id,))
        summary['guarantees'] = [dict(row) for row in cur.fetchall()]

        return summary

    def get_property_valuation_summary(self, property_id: int) -> Dict:
        """Valuation bucket: NOI calculation from operations + debt data."""
        summary = {}

        # Revenue items from operating statements
        cur = self.conn.execute("""
            SELECT os.* FROM operating_statement_items os
            JOIN documents d ON os.document_id = d.id
            WHERE d.property_id = ? AND os.category = 'revenue'
            ORDER BY d.processed_at DESC, os.id
        """, (property_id,))
        summary['revenue_items'] = [dict(row) for row in cur.fetchall()]

        # Expense items
        cur = self.conn.execute("""
            SELECT os.* FROM operating_statement_items os
            JOIN documents d ON os.document_id = d.id
            WHERE d.property_id = ? AND os.category = 'expense'
            ORDER BY d.processed_at DESC, os.id
        """, (property_id,))
        summary['expense_items'] = [dict(row) for row in cur.fetchall()]

        # NOI line items (if extracted directly)
        cur = self.conn.execute("""
            SELECT os.* FROM operating_statement_items os
            JOIN documents d ON os.document_id = d.id
            WHERE d.property_id = ? AND os.category IN ('noi', 'debt_service', 'cash_flow')
            ORDER BY d.processed_at DESC, os.id
        """, (property_id,))
        summary['noi_items'] = [dict(row) for row in cur.fetchall()]

        # Calculate NOI from revenue - expenses
        total_revenue = sum(
            (r.get('amount') or 0) for r in summary['revenue_items']
            if not r.get('is_subtotal') and not r.get('is_total'))
        total_expenses = sum(
            (e.get('amount') or 0) for e in summary['expense_items']
            if not e.get('is_subtotal') and not e.get('is_total'))
        summary['calculated_noi'] = total_revenue - total_expenses
        summary['total_revenue'] = total_revenue
        summary['total_expenses'] = total_expenses

        # Debt service from loan terms
        cur = self.conn.execute("""
            SELECT ft.* FROM financial_terms ft
            JOIN documents d ON ft.document_id = d.id
            WHERE d.property_id = ?
              AND ft.term_type IN ('debt_service', 'annual_debt_service', 'monthly_payment')
            ORDER BY d.processed_at DESC LIMIT 5
        """, (property_id,))
        debt_terms = [dict(row) for row in cur.fetchall()]
        summary['debt_service_terms'] = debt_terms

        # DSCR if we have both NOI and debt service
        annual_ds = 0
        for dt in debt_terms:
            if dt.get('value_numeric'):
                if dt.get('value_unit') == 'monthly':
                    annual_ds = dt['value_numeric'] * 12
                else:
                    annual_ds = dt['value_numeric']
                break
        summary['annual_debt_service'] = annual_ds
        summary['dscr'] = round(summary['calculated_noi'] / annual_ds, 2) if annual_ds > 0 else None

        # Property acquisition info for cap rate
        prop = self.get_property(property_id)
        if prop and prop.get('acquisition_price') and summary['calculated_noi']:
            summary['cap_rate'] = round(
                (summary['calculated_noi'] / prop['acquisition_price']) * 100, 2)
        else:
            summary['cap_rate'] = None

        return summary

    # ─── Document Operations ─────────────────────────────────────────

    def insert_document(self, filename: str, filepath: str, document_type: str,
                        property_name: str = None, property_address: str = None,
                        page_count: int = None, is_scanned: bool = False,
                        ocr_confidence: float = None, file_hash: str = None,
                        metadata: dict = None) -> int:
        """Insert a new document record. Returns the document ID."""
        cur = self.conn.execute("""
            INSERT INTO documents (filename, filepath, document_type, property_name,
                                   property_address, page_count, is_scanned,
                                   ocr_confidence, file_hash, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (filename, filepath, document_type, property_name, property_address,
              page_count, is_scanned, ocr_confidence, file_hash,
              json.dumps(metadata) if metadata else None))
        self.conn.commit()
        return cur.lastrowid

    def document_exists(self, file_hash: str) -> bool:
        """Check if a document with this hash has already been processed."""
        cur = self.conn.execute(
            "SELECT id FROM documents WHERE file_hash = ?", (file_hash,))
        return cur.fetchone() is not None

    def get_document(self, doc_id: int) -> Optional[Dict]:
        """Get a single document by ID."""
        cur = self.conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def list_documents(self, document_type: str = None,
                       property_name: str = None) -> List[Dict]:
        """List documents with optional filters."""
        query = "SELECT * FROM documents WHERE 1=1"
        params = []
        if document_type:
            query += " AND document_type = ?"
            params.append(document_type)
        if property_name:
            query += " AND property_name LIKE ?"
            params.append(f"%{property_name}%")
        query += " ORDER BY processed_at DESC"
        cur = self.conn.execute(query, params)
        return [dict(row) for row in cur.fetchall()]

    # ─── Full-Text Content ───────────────────────────────────────────

    def insert_fulltext(self, document_id: int, page_number: int, content: str):
        """Store page-level full text for search."""
        self.conn.execute("""
            INSERT INTO document_fulltext (document_id, page_number, content)
            VALUES (?, ?, ?)
        """, (str(document_id), str(page_number), content))
        self.conn.commit()

    def search_fulltext(self, query: str, limit: int = 20) -> List[Dict]:
        """Full-text search across all document content."""
        cur = self.conn.execute("""
            SELECT document_id, page_number, snippet(document_fulltext, 2, '<b>', '</b>', '...', 40) as snippet,
                   rank
            FROM document_fulltext
            WHERE content MATCH ?
            ORDER BY rank
            LIMIT ?
        """, (query, limit))
        return [dict(row) for row in cur.fetchall()]

    # ─── Clause Operations ───────────────────────────────────────────

    def insert_clause(self, document_id: int, clause_type: str, full_text: str,
                      section_ref: str = None, clause_title: str = None,
                      summary: str = None, page_number: int = None,
                      confidence: float = None, metadata: dict = None) -> int:
        cur = self.conn.execute("""
            INSERT INTO clauses (document_id, clause_type, section_ref, clause_title,
                                 full_text, summary, page_number, confidence, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (document_id, clause_type, section_ref, clause_title, full_text,
              summary, page_number, confidence,
              json.dumps(metadata) if metadata else None))
        self.conn.commit()
        return cur.lastrowid

    def get_clauses(self, document_id: int = None,
                    clause_type: str = None) -> List[Dict]:
        query = "SELECT c.*, d.filename FROM clauses c JOIN documents d ON c.document_id = d.id WHERE 1=1"
        params = []
        if document_id:
            query += " AND c.document_id = ?"
            params.append(document_id)
        if clause_type:
            query += " AND c.clause_type = ?"
            params.append(clause_type)
        cur = self.conn.execute(query, params)
        return [dict(row) for row in cur.fetchall()]

    # ─── Financial Term Operations ───────────────────────────────────

    def insert_financial_term(self, document_id: int, term_type: str,
                              value_raw: str = None, value_numeric: float = None,
                              value_unit: str = None, term_label: str = None,
                              effective_date: str = None, expiration_date: str = None,
                              escalation_type: str = None, escalation_detail: str = None,
                              section_ref: str = None, page_number: int = None,
                              confidence: float = None, metadata: dict = None) -> int:
        cur = self.conn.execute("""
            INSERT INTO financial_terms (document_id, term_type, term_label, value_raw,
                                         value_numeric, value_unit, effective_date,
                                         expiration_date, escalation_type, escalation_detail,
                                         section_ref, page_number, confidence, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (document_id, term_type, term_label, value_raw, value_numeric, value_unit,
              effective_date, expiration_date, escalation_type, escalation_detail,
              section_ref, page_number, confidence,
              json.dumps(metadata) if metadata else None))
        self.conn.commit()
        return cur.lastrowid

    def get_financial_terms(self, document_id: int = None,
                            term_type: str = None) -> List[Dict]:
        query = "SELECT ft.*, d.filename FROM financial_terms ft JOIN documents d ON ft.document_id = d.id WHERE 1=1"
        params = []
        if document_id:
            query += " AND ft.document_id = ?"
            params.append(document_id)
        if term_type:
            query += " AND ft.term_type = ?"
            params.append(term_type)
        cur = self.conn.execute(query, params)
        return [dict(row) for row in cur.fetchall()]

    # ─── Rent Roll Operations ────────────────────────────────────────

    def insert_rent_roll_entry(self, document_id: int, **kwargs) -> int:
        fields = ['property_name', 'unit_number', 'tenant_name', 'suite',
                  'square_footage', 'lease_start', 'lease_end', 'monthly_rent',
                  'annual_rent', 'rent_psf', 'status', 'notes', 'page_number', 'metadata']
        values = {f: kwargs.get(f) for f in fields}
        if isinstance(values.get('metadata'), dict):
            values['metadata'] = json.dumps(values['metadata'])
        cols = ['document_id'] + [k for k, v in values.items() if v is not None]
        vals = [document_id] + [v for v in values.values() if v is not None]
        placeholders = ','.join(['?'] * len(cols))
        col_str = ','.join(cols)
        cur = self.conn.execute(
            f"INSERT INTO rent_roll_entries ({col_str}) VALUES ({placeholders})", vals)
        self.conn.commit()
        return cur.lastrowid

    def get_rent_roll(self, document_id: int = None,
                      property_name: str = None) -> List[Dict]:
        query = "SELECT rr.*, d.filename FROM rent_roll_entries rr JOIN documents d ON rr.document_id = d.id WHERE 1=1"
        params = []
        if document_id:
            query += " AND rr.document_id = ?"
            params.append(document_id)
        if property_name:
            query += " AND rr.property_name LIKE ?"
            params.append(f"%{property_name}%")
        cur = self.conn.execute(query, params)
        return [dict(row) for row in cur.fetchall()]

    # ─── Operating Statement Operations ──────────────────────────────

    def insert_operating_statement_item(self, document_id: int, category: str,
                                         line_item: str, **kwargs) -> int:
        fields = ['property_name', 'period', 'subcategory', 'amount', 'amount_psf',
                  'is_subtotal', 'is_total', 'page_number', 'metadata']
        values = {f: kwargs.get(f) for f in fields}
        if isinstance(values.get('metadata'), dict):
            values['metadata'] = json.dumps(values['metadata'])
        cols = ['document_id', 'category', 'line_item'] + [k for k, v in values.items() if v is not None]
        vals = [document_id, category, line_item] + [v for v in values.values() if v is not None]
        placeholders = ','.join(['?'] * len(cols))
        col_str = ','.join(cols)
        cur = self.conn.execute(
            f"INSERT INTO operating_statement_items ({col_str}) VALUES ({placeholders})", vals)
        self.conn.commit()
        return cur.lastrowid

    def get_operating_statement(self, document_id: int = None,
                                 category: str = None,
                                 period: str = None) -> List[Dict]:
        query = "SELECT os.*, d.filename FROM operating_statement_items os JOIN documents d ON os.document_id = d.id WHERE 1=1"
        params = []
        if document_id:
            query += " AND os.document_id = ?"
            params.append(document_id)
        if category:
            query += " AND os.category = ?"
            params.append(category)
        if period:
            query += " AND os.period = ?"
            params.append(period)
        query += " ORDER BY os.id"
        cur = self.conn.execute(query, params)
        return [dict(row) for row in cur.fetchall()]

    # ─── General Ledger Operations ───────────────────────────────────

    def insert_gl_entry(self, document_id: int, **kwargs) -> int:
        fields = ['property_name', 'account_code', 'account_name', 'entry_date',
                  'description', 'debit', 'credit', 'balance', 'period',
                  'vendor', 'reference', 'page_number', 'metadata']
        values = {f: kwargs.get(f) for f in fields}
        if isinstance(values.get('metadata'), dict):
            values['metadata'] = json.dumps(values['metadata'])
        cols = ['document_id'] + [k for k, v in values.items() if v is not None]
        vals = [document_id] + [v for v in values.values() if v is not None]
        placeholders = ','.join(['?'] * len(cols))
        col_str = ','.join(cols)
        cur = self.conn.execute(
            f"INSERT INTO gl_entries ({col_str}) VALUES ({placeholders})", vals)
        self.conn.commit()
        return cur.lastrowid

    def get_gl_entries(self, document_id: int = None,
                       account_code: str = None,
                       date_from: str = None,
                       date_to: str = None) -> List[Dict]:
        query = "SELECT gl.*, d.filename FROM gl_entries gl JOIN documents d ON gl.document_id = d.id WHERE 1=1"
        params = []
        if document_id:
            query += " AND gl.document_id = ?"
            params.append(document_id)
        if account_code:
            query += " AND gl.account_code = ?"
            params.append(account_code)
        if date_from:
            query += " AND gl.entry_date >= ?"
            params.append(date_from)
        if date_to:
            query += " AND gl.entry_date <= ?"
            params.append(date_to)
        query += " ORDER BY gl.entry_date"
        cur = self.conn.execute(query, params)
        return [dict(row) for row in cur.fetchall()]

    # ─── Cross-Document Queries ──────────────────────────────────────

    def get_portfolio_summary(self, property_name: str = None) -> Dict:
        """Get a high-level summary across all ingested documents."""
        params = []
        where = ""
        if property_name:
            where = "WHERE property_name LIKE ?"
            params.append(f"%{property_name}%")

        summary = {}

        # Document counts by type
        cur = self.conn.execute(f"""
            SELECT document_type, COUNT(*) as count
            FROM documents {where}
            GROUP BY document_type
        """, params)
        summary['document_counts'] = {row['document_type']: row['count'] for row in cur.fetchall()}

        # Total properties
        cur = self.conn.execute(f"""
            SELECT COUNT(DISTINCT property_name) as count
            FROM documents {where}
        """, params)
        summary['total_properties'] = cur.fetchone()['count']

        # Rent roll summary
        cur = self.conn.execute("""
            SELECT COUNT(*) as units,
                   SUM(monthly_rent) as total_monthly_rent,
                   AVG(rent_psf) as avg_rent_psf,
                   SUM(square_footage) as total_sqft
            FROM rent_roll_entries
        """)
        row = cur.fetchone()
        summary['rent_roll'] = dict(row) if row else {}

        return summary

    def export_to_csv(self, table: str, filepath: str, filters: Dict = None):
        """Export any table to CSV with optional filters."""
        import csv
        query = f"SELECT * FROM {table}"
        params = []
        if filters:
            conditions = []
            for key, value in filters.items():
                conditions.append(f"{key} = ?")
                params.append(value)
            query += " WHERE " + " AND ".join(conditions)

        cur = self.conn.execute(query, params)
        rows = cur.fetchall()

        if rows:
            with open(filepath, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(rows[0].keys())
                writer.writerows([tuple(row) for row in rows])
            return len(rows)
        return 0
