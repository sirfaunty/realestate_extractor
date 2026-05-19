"""
Citation Bridge — Maps Capactive extracted data into the chamberlain
proforma engine's SourceDocumentRegistry and Cited[T] values.

This is the single integration seam between the extraction platform
and the proforma engine. The chamberlain engine code stays untouched;
this module translates Capactive's database records into the Pydantic
models the engine expects.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any

from ...chamberlain.models.citation import (
    AuthorityTier,
    Citation,
    Cited,
    DocumentType,
    Locator,
    SourceDocument,
    SourceDocumentRegistry,
    cite,
)

logger = logging.getLogger(__name__)

# ─── Document Type Mapping ──────────────────────────────────────────
# Map Capactive's document_type strings to chamberlain's DocumentType enum

_DOC_TYPE_MAP = {
    'operating_statement': DocumentType.MRI_INCOME_STATEMENT,
    'rent_roll': DocumentType.RENT_ROLL,
    'general_ledger': DocumentType.GL_EXPORT,
    'proforma': DocumentType.VALUATION_PROFORMA,
    'closing': DocumentType.CLOSING_STATEMENT,
    'hud_form': DocumentType.CLOSING_STATEMENT,
    'loan': DocumentType.LOAN_AGREEMENT,
    'partnership_agreement': DocumentType.LLC_AGREEMENT,
    'organizational': DocumentType.LLC_AGREEMENT,
    'equity_waterfall': DocumentType.WATERFALL_CALC,
    'due_diligence': DocumentType.ACCOUNTANT_WORKPAPER,
    'reference': DocumentType.OTHER,
    'cost_cert': DocumentType.CLOSING_STATEMENT,
    'budget': DocumentType.BUDGET_FILE,
}

# Map Capactive's DOC_TYPE_AUTHORITY scores to chamberlain AuthorityTier
# GL (5), operating_statement/rent_roll (4) -> PRIMARY
# closing/hud/cost_cert (3) -> SECONDARY
# everything else -> TERTIARY
_AUTHORITY_MAP = {
    'general_ledger': AuthorityTier.PRIMARY,
    'operating_statement': AuthorityTier.PRIMARY,
    'rent_roll': AuthorityTier.PRIMARY,
    'closing': AuthorityTier.SECONDARY,
    'hud_form': AuthorityTier.SECONDARY,
    'cost_cert': AuthorityTier.SECONDARY,
    'proforma': AuthorityTier.SECONDARY,
    'equity_waterfall': AuthorityTier.SECONDARY,
    'loan': AuthorityTier.SECONDARY,
    'due_diligence': AuthorityTier.TERTIARY,
    'partnership_agreement': AuthorityTier.TERTIARY,
    'organizational': AuthorityTier.TERTIARY,
    'reference': AuthorityTier.TERTIARY,
    'budget': AuthorityTier.SECONDARY,
}


def _make_doc_slug(doc: Dict) -> str:
    """Generate a stable slug ID from a Capactive document record."""
    filename = doc.get('filename', 'unknown')
    doc_id = doc.get('id', 0)
    # Clean filename for slug
    slug = filename.lower()
    for ch in ['.', ' ', '(', ')', ',', '-', '_']:
        slug = slug.replace(ch, '_')
    # Remove consecutive underscores and trailing
    while '__' in slug:
        slug = slug.replace('__', '_')
    slug = slug.strip('_')
    return f"cap_{doc_id}_{slug}"


def build_registry_from_db(db, property_id: int) -> SourceDocumentRegistry:
    """Build a SourceDocumentRegistry from Capactive's extracted documents.

    Queries the database for all documents linked to a property and
    creates SourceDocument entries with proper type and authority mapping.

    Args:
        db: Connected Capactive Database instance
        property_id: Property to build registry for

    Returns:
        SourceDocumentRegistry populated with all property documents
    """
    registry = SourceDocumentRegistry()

    docs = db.get_property_documents(property_id)
    prop = db.get_property(property_id)
    asset_id = prop['name'].lower().replace(' ', '_') if prop else None

    for doc in docs:
        doc_type_str = doc.get('document_type', 'reference')
        chamberlain_type = _DOC_TYPE_MAP.get(doc_type_str, DocumentType.OTHER)
        authority = _AUTHORITY_MAP.get(doc_type_str, AuthorityTier.TERTIARY)

        slug = _make_doc_slug(doc)
        filepath = doc.get('filepath')

        try:
            source_doc = SourceDocument(
                id=slug,
                doc_type=chamberlain_type,
                authority_tier=authority,
                title=doc.get('filename', 'Unknown'),
                description=f"Capactive document #{doc['id']}, type: {doc_type_str}",
                file_path=Path(filepath) if filepath else None,
                file_hash=doc.get('file_hash'),
                asset_id=asset_id,
            )
            registry.add(source_doc)
        except Exception as e:
            logger.warning(f"Failed to register document {doc.get('filename')}: {e}")

    logger.info(f"Built registry with {len(registry)} documents for property {property_id}")
    return registry


def build_cited_financials(db, property_id: int, registry: SourceDocumentRegistry) -> Dict[str, Any]:
    """Extract financial data from Capactive and wrap in Cited[T] values.

    Queries operating statement items, financial terms, and rent roll data,
    returning them as Cited values that trace back through the registry.

    Args:
        db: Connected Capactive Database instance
        property_id: Property to extract for
        registry: Pre-built SourceDocumentRegistry

    Returns:
        Dict with keys:
            - 'income_items': List of Cited operating items (income/revenue)
            - 'expense_items': List of Cited operating items (expenses)
            - 'financial_terms': List of Cited financial terms
            - 'rent_roll': List of Cited rent roll entries
            - 'periods': Dict[str, Dict] with period-level aggregates
    """
    docs = db.get_property_documents(property_id)
    doc_id_to_slug = {}
    for doc in docs:
        doc_id_to_slug[doc['id']] = _make_doc_slug(doc)

    result = {
        'income_items': [],
        'expense_items': [],
        'financial_terms': [],
        'rent_roll': [],
        'periods': {},
    }

    # Query operating statement items
    items = db.conn.execute("""
        SELECT os.*, d.filename, d.document_type, d.id as doc_id
        FROM operating_statement_items os
        JOIN documents d ON os.document_id = d.id
        WHERE d.property_id = ?
        ORDER BY os.period, os.category, os.line_item
    """, (property_id,)).fetchall()

    for item in items:
        item = dict(item)  # sqlite3.Row doesn't support .get()
        doc_slug = doc_id_to_slug.get(item['doc_id'])
        if not doc_slug or doc_slug not in registry:
            continue

        locator = Locator(
            sheet=item.get('subcategory') or None,
            row_label=item.get('line_item'),
            col_label=item.get('period'),
            note=f"from {item['filename']}",
        )

        citation = Citation(
            source_document_id=doc_slug,
            locator=locator,
            verbatim_text=f"{item.get('amount', 0):,.2f}" if item.get('amount') else None,
            extraction_method='auto',
            confidence=0.9 if not item.get('is_subtotal') else 0.7,
        )

        cited_item = {
            'line_item': item['line_item'],
            'amount': Cited(value=item.get('amount', 0), citations=[citation]),
            'period': item['period'],
            'category': item['category'],
            'is_subtotal': item.get('is_subtotal', False),
            'document_id': item['doc_id'],
            'document_type': item['document_type'],
        }

        cat = item['category']
        if cat in ('income', 'revenue'):
            result['income_items'].append(cited_item)
        elif cat == 'expense':
            result['expense_items'].append(cited_item)

        # Aggregate by period
        period = item['period']
        if period not in result['periods']:
            result['periods'][period] = {
                'income': 0, 'expense': 0,
                'income_citations': [], 'expense_citations': [],
            }
        if cat in ('income', 'revenue') and not item.get('is_subtotal'):
            result['periods'][period]['income'] += item.get('amount', 0) or 0
            result['periods'][period]['income_citations'].append(citation)
        elif cat == 'expense' and not item.get('is_subtotal'):
            result['periods'][period]['expense'] += item.get('amount', 0) or 0
            result['periods'][period]['expense_citations'].append(citation)

    # Query financial terms
    terms = db.conn.execute("""
        SELECT ft.*, d.filename, d.id as doc_id
        FROM financial_terms ft
        JOIN documents d ON ft.document_id = d.id
        WHERE d.property_id = ?
    """, (property_id,)).fetchall()

    for term in terms:
        term = dict(term)  # sqlite3.Row doesn't support .get()
        doc_slug = doc_id_to_slug.get(term['doc_id'])
        if not doc_slug or doc_slug not in registry:
            continue

        locator = Locator(
            page=term.get('page_number'),
            section=term.get('section_ref'),
            note=f"term: {term.get('term_label')}",
        )

        citation = Citation(
            source_document_id=doc_slug,
            locator=locator,
            verbatim_text=term.get('value_raw'),
            extraction_method='auto',
            confidence=term.get('confidence') or 0.85,
        )

        result['financial_terms'].append({
            'term_name': term.get('term_label', ''),
            'value': Cited(value=term.get('value_raw') or '', citations=[citation]),
            'category': term.get('term_type'),
            'document_id': term['doc_id'],
        })

    # Query rent roll
    rr_entries = db.conn.execute("""
        SELECT rr.*, d.filename, d.id as doc_id
        FROM rent_roll_entries rr
        JOIN documents d ON rr.document_id = d.id
        WHERE d.property_id = ?
    """, (property_id,)).fetchall()

    for entry in rr_entries:
        entry = dict(entry)  # sqlite3.Row doesn't support .get()
        doc_slug = doc_id_to_slug.get(entry['doc_id'])
        if not doc_slug or doc_slug not in registry:
            continue

        monthly = entry.get('monthly_rent') or 0
        locator = Locator(
            row_label=entry.get('unit_number'),
            note=f"unit {entry.get('unit_number')} from {entry['filename']}",
        )

        citation = Citation(
            source_document_id=doc_slug,
            locator=locator,
            verbatim_text=f"${monthly:,.0f}/mo" if monthly else None,
            extraction_method='auto',
            confidence=0.9,
        )

        result['rent_roll'].append({
            'unit_number': entry.get('unit_number'),
            'tenant_name': entry.get('tenant_name'),
            'monthly_rent': Cited(value=monthly, citations=[citation]),
            'annual_rent': entry.get('annual_rent') or 0,
            'sqft': entry.get('square_footage') or 0,
            'status': entry.get('status'),
        })

    logger.info(
        f"Built cited financials: {len(result['income_items'])} income, "
        f"{len(result['expense_items'])} expense, "
        f"{len(result['financial_terms'])} terms, "
        f"{len(result['rent_roll'])} rent roll entries"
    )
    return result


def generate_drillback_data(db, property_id: int) -> Dict[str, Any]:
    """Generate the full drillback dataset for a property.

    Returns all the data needed to render a drillback view:
    registry, cited financials, and period summaries.

    This is the main entry point for the proforma drillback view.
    """
    registry = build_registry_from_db(db, property_id)
    financials = build_cited_financials(db, property_id, registry)

    return {
        'registry': registry,
        'financials': financials,
        'property': db.get_property(property_id),
    }
