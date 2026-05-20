#!/usr/bin/env python3
"""Re-classify documents with empty document_type."""
import sqlite3, re, json

# ── Inline classifier (filename patterns + keyword scoring) ──────────

FILENAME_PATTERNS = [
    (r'llc.?agreement|limited.?liability.?company.?agreement', 'partnership_agreement', 0.92),
    (r'amendment.{0,10}llc|llc.{0,10}amendment', 'partnership_agreement', 0.90),
    (r'exhibit.*llc|llc.*exhibit|llc.?section|llc.?definition', 'partnership_agreement', 0.88),
    (r'hud.?cost.?cert', 'hud_form', 0.92),
    (r'hud.?final.?endors', 'hud_form', 0.92),
    (r'hud.?max.?insur', 'loan', 0.90),
    (r'hud.?escrow.?release|hud.?offsite|hud.?wc.?escrow', 'hud_form', 0.90),
    (r'hud.?closing.?checklist', 'hud_form', 0.92),
    (r'application.?for.?insurance.?of.?advance', 'hud_form', 0.90),
    (r'request.?for.?(?:final\s*)?endorsement', 'hud_form', 0.90),
    (r'request.?for.?approval.?of.?advance', 'hud_form', 0.90),
    (r'lender.?s?.?certification', 'hud_form', 0.88),
    (r'contractor.?s?.?requisition', 'hud_form', 0.88),
    (r'maximum.?insurable.?mortgage', 'hud_form', 0.90),
    (r'certification.?re.?litigation', 'hud_form', 0.88),
    (r'special.?conditions.?from.?firm', 'hud_form', 0.88),
    (r'davis.?bacon|labor.?standards.?clearance', 'hud_form', 0.86),
    (r'diagnostic.?memo|due.?diligence|forensic.?review', 'due_diligence', 0.90),
    (r'phase.?[i1].?(?:esa|environmental)|environmental.?(?:site|report)', 'due_diligence', 0.90),
    (r'appraisal|market.?study|property.?valuation', 'due_diligence', 0.88),
    (r'(?:alta|boundary).?survey|site.?plan|(?:^|[\d_\-\s])survey(?:[\d_\-\s.]|$)|surveyor', 'due_diligence', 0.88),
    (r'property.?condition|pca|engineering.?report', 'due_diligence', 0.88),
    (r'seismic.?report|zoning.?(?:report|letter|compliance)', 'due_diligence', 0.88),
    (r'proforma|valuation.?proforma|portfolio.?proforma', 'proforma', 0.90),
    (r'investment.?summary|investment.?overview', 'proforma', 0.85),
    (r'equity.?return.?calc|jv.?equity|equity.?waterfall', 'equity_waterfall', 0.90),
    (r'equity.?account|equity.?detail', 'equity_waterfall', 0.88),
    (r'budget.?overview|operating.?budget|detailed.?budget', 'operating_statement', 0.90),
    (r'property.?overview.?summary', 'operating_statement', 0.85),
    (r'leadership.?rollup|cash.?activity', 'operating_statement', 0.85),
    (r'closing.?(?:proceeds|book|transcript|binder)', 'closing', 0.90),
    (r'settlement.?statement|closing.?statement', 'closing', 0.90),
    (r'sources.?and.?uses|sources.?uses', 'closing', 0.88),
    (r'development.?agreement|contract.{0,20}development', 'closing', 0.88),
    (r'management.?agreement', 'closing', 0.86),
    (r'title.?(?:commitment|policy|insurance|search|report|exception|endorsement)', 'closing', 0.88),
    (r'(?:owner|lender).?s?.?(?:title|affidavit)', 'closing', 0.86),
    (r'subordination.?(?:agreement|nondisturbance)|snda', 'closing', 0.88),
    (r'estoppel.?(?:certificate|letter)', 'closing', 0.88),
    (r'assignment.?(?:of\s*)?(?:lease|rent|collateral)', 'closing', 0.88),
    (r'ucc.?[1-3]|uniform.?commercial', 'closing', 0.86),
    (r'attorney.?opinion|(?:legal|enforceability).?opinion', 'closing', 0.86),
    (r'alta.?closing.?protection', 'closing', 0.86),
    (r'certificate.?of.?(?:substantial.?)?completion', 'closing', 0.86),
    (r'surplus.?cash.?note', 'loan', 0.88),
    (r'loan.?interest.?calc|project.?loan', 'loan', 0.88),
    (r'promissory.?note|mortgage.?schedule', 'loan', 0.90),
    (r'payoff.?(?:letter|statement)|loan.?payoff', 'loan', 0.90),
    (r'mortgage.?(?:note|deed|modification|assumption)', 'loan', 0.90),
    (r'security.?instrument|deed.?of.?trust', 'loan', 0.88),
    (r'regulatory.?agreement', 'loan', 0.86),
    (r'firm.?commitment(?:.?amendment)?', 'loan', 0.90),
    (r'endorsed.?note', 'loan', 0.90),
    (r'escrow.?agreement', 'loan', 0.86),
    (r'byrd.?amendment', 'loan', 0.84),
    (r'guarantee|guaranty', 'guarantee', 0.90),
    (r'(?:insurance|flood).?cert', 'due_diligence', 0.86),
    (r'certificate.?of.?insurance|certificates.?of.?insurance', 'due_diligence', 0.88),
    (r'tax.?(?:certificate|search|clearance)', 'closing', 0.86),
    (r'certificate.?of.?occupancy|certificates.?of.?occupancy', 'due_diligence', 0.88),
    (r'radon.?(?:report|test|measurement)', 'due_diligence', 0.88),
    (r'rent.?roll', 'rent_roll', 0.92),
    (r'general.?ledger|gl.?detail', 'general_ledger', 0.90),
    (r'organizational.?chart|org.?chart|borrower.?org', 'organizational', 0.88),
    (r'organizational.?(?:documents|certification)', 'organizational', 0.88),
    (r'managing.?member.?s?.?organizational', 'organizational', 0.88),
    (r'borrower.?s?.?(?:organizational|managing)', 'organizational', 0.86),
    (r'context\.md$|context\.txt$|readme', 'reference', 0.85),
    (r'contact.?list', 'reference', 0.88),
]

compiled_patterns = [(re.compile(p, re.IGNORECASE), t, c) for p, t, c in FILENAME_PATTERNS]


def classify_filename(filename):
    fn = filename.lower().replace(' ', '_')
    for pat, doc_type, conf in compiled_patterns:
        if pat.search(fn):
            return doc_type, conf
    return None, 0.0


def classify(filename, text):
    # Try filename first
    result = classify_filename(filename)
    if result[0]:
        return result
    # Fallback: correspondence if it looks like an email
    if any(kw in text.lower()[:500] for kw in ['from:', 'to:', 'subject:', 'sent:']):
        return 'correspondence', 0.50
    return 'unknown', 0.0


# ── Main ─────────────────────────────────────────────────────────────

db = sqlite3.connect('data/org_dev.db')
db.row_factory = sqlite3.Row

docs = db.execute(
    'SELECT id, filename FROM documents WHERE property_id=1 AND document_type=""'
).fetchall()
print(f'Re-classifying {len(docs)} docs...')

for doc in docs:
    doc_id, fn = doc['id'], doc['filename']
    ft = db.execute(
        'SELECT content FROM document_fulltext WHERE document_id=?',
        (str(doc_id),)
    ).fetchone()
    text = ft['content'][:3000] if ft else ''
    doc_type, conf = classify(fn, text)
    meta = json.dumps({'classification_confidence': conf})
    db.execute(
        'UPDATE documents SET document_type=?, analysis_status=?, metadata=? WHERE id=?',
        (doc_type, 'ingested', meta, doc_id)
    )
    print(f'  #{doc_id} {doc_type:<22} ({conf:.0%}) {fn[:55]}')

db.commit()
print('Done')
db.close()
