"""
Document Type Templates for Real Estate Document Extractor.

Each template defines:
- What fields to extract
- Which extraction mode(s) to use (legal, financial, tabular)
- LLM prompts tailored to each document type
- Column mappings for tabular documents
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional
from enum import Enum


class ExtractionMode(Enum):
    LEGAL = "legal"           # Clause extraction — preserves full legal language
    FINANCIAL = "financial"   # Structured key-value financial terms
    TABULAR = "tabular"       # Row/column data (rent rolls, GL, etc.)
    DUAL = "dual"             # Both legal AND financial (e.g., leases)


@dataclass
class FieldDefinition:
    """Defines a single field to extract."""
    name: str
    description: str
    field_type: str = "text"      # text, number, date, currency, percentage, boolean
    required: bool = False
    aliases: List[str] = field(default_factory=list)  # alternative names in docs


@dataclass
class DocumentTemplate:
    """Template defining extraction rules for a document type."""
    document_type: str
    display_name: str
    description: str
    extraction_modes: List[ExtractionMode]
    financial_fields: List[FieldDefinition] = field(default_factory=list)
    clause_types: List[str] = field(default_factory=list)
    table_columns: List[FieldDefinition] = field(default_factory=list)
    llm_system_prompt: str = ""
    llm_extraction_prompt: str = ""
    llm_clause_prompt: str = ""


# ─── Template Definitions ────────────────────────────────────────────

LEASE_AGREEMENT = DocumentTemplate(
    document_type="lease",
    display_name="Lease Agreement",
    description="Commercial or residential lease agreements — dual-mode extraction for both legal clauses and financial terms",
    extraction_modes=[ExtractionMode.DUAL],

    financial_fields=[
        FieldDefinition("tenant_name", "Name of the tenant/lessee", required=True,
                        aliases=["lessee", "tenant"]),
        FieldDefinition("landlord_name", "Name of the landlord/lessor", required=True,
                        aliases=["lessor", "landlord", "owner"]),
        FieldDefinition("property_address", "Full address of the leased premises",
                        required=True, aliases=["premises", "leased premises"]),
        FieldDefinition("suite_unit", "Suite or unit number",
                        aliases=["suite", "unit", "space"]),
        FieldDefinition("square_footage", "Rentable or usable square footage",
                        field_type="number", aliases=["rsf", "usf", "sf", "area"]),
        FieldDefinition("lease_commencement", "Lease start date", field_type="date",
                        required=True, aliases=["commencement date", "start date"]),
        FieldDefinition("lease_expiration", "Lease end date", field_type="date",
                        required=True, aliases=["expiration date", "end date", "termination date"]),
        FieldDefinition("lease_term", "Length of lease term",
                        aliases=["term", "initial term"]),
        FieldDefinition("base_rent", "Base/minimum rent amount", field_type="currency",
                        required=True, aliases=["minimum rent", "fixed rent", "monthly rent"]),
        FieldDefinition("rent_frequency", "How often rent is paid",
                        aliases=["payment frequency"]),
        FieldDefinition("escalation_type", "Type of rent escalation",
                        aliases=["increases", "adjustments"]),
        FieldDefinition("escalation_rate", "Escalation rate or schedule", field_type="percentage",
                        aliases=["annual increase", "cpi adjustment"]),
        FieldDefinition("cam_charges", "Common area maintenance charges", field_type="currency",
                        aliases=["CAM", "operating expenses", "additional rent"]),
        FieldDefinition("cam_structure", "CAM structure type — NNN, modified gross, full service",
                        aliases=["lease type", "expense structure"]),
        FieldDefinition("security_deposit", "Security deposit amount", field_type="currency",
                        aliases=["deposit"]),
        FieldDefinition("ti_allowance", "Tenant improvement allowance", field_type="currency",
                        aliases=["TI", "improvement allowance", "build-out allowance"]),
        FieldDefinition("free_rent", "Free rent period",
                        aliases=["rent abatement", "concession"]),
        FieldDefinition("percentage_rent", "Percentage rent terms", field_type="percentage",
                        aliases=["overage rent"]),
        FieldDefinition("percentage_rent_breakpoint", "Breakpoint for percentage rent",
                        field_type="currency", aliases=["breakpoint", "natural breakpoint"]),
        FieldDefinition("renewal_options", "Renewal/extension option terms",
                        aliases=["extension", "option to renew"]),
        FieldDefinition("termination_options", "Early termination rights",
                        aliases=["early termination", "kick-out", "break clause"]),
        FieldDefinition("guarantor", "Name of guarantor if any",
                        aliases=["guarantee", "personal guarantee"]),
    ],

    clause_types=[
        "permitted_use",
        "assignment_subletting",
        "default_remedies",
        "insurance_requirements",
        "indemnification",
        "maintenance_repairs",
        "alterations_improvements",
        "estoppel",
        "subordination_nondisturbance",
        "holdover",
        "force_majeure",
        "condemnation",
        "casualty_damage",
        "environmental",
        "signage",
        "parking",
        "right_of_first_refusal",
        "co_tenancy",
        "exclusive_use",
        "radius_restriction",
        "relocation",
        "confidentiality",
        "governing_law",
    ],

    llm_system_prompt="""You are a real estate lease abstraction specialist. Your job is to extract
specific financial terms and legal clauses from commercial lease agreements.
Be precise with numbers, dates, and legal language. When extracting clauses,
preserve the COMPLETE original language — do not paraphrase or summarize the
clause text itself. Provide a brief plain-language summary separately.""",

    llm_extraction_prompt="""Extract the following financial terms from this lease agreement.
For each term found, provide:
- The exact value as it appears in the document
- A normalized numeric value where applicable
- The section reference where it was found
- Your confidence level (0-1)

If a term is not found in the document, indicate it as "not_found".

Return results as a JSON array of objects with keys:
term_type, term_label, value_raw, value_numeric, value_unit,
effective_date, expiration_date, escalation_type, escalation_detail,
section_ref, page_number, confidence

Financial terms to extract:
{field_list}

Document text:
{document_text}""",

    llm_clause_prompt="""Extract the following clause types from this lease agreement.
For each clause found, provide:
- The COMPLETE original text of the clause (do not paraphrase)
- The section reference (e.g., "Section 12.3")
- The clause heading/title if present
- A brief 1-2 sentence plain-language summary
- Your confidence level (0-1)

Return results as a JSON array of objects with keys:
clause_type, section_ref, clause_title, full_text, summary, page_number, confidence

Clause types to extract:
{clause_list}

Document text:
{document_text}"""
)


LOAN_DOCUMENT = DocumentTemplate(
    document_type="loan",
    display_name="Loan Document",
    description="Mortgage, promissory note, or loan agreement",
    extraction_modes=[ExtractionMode.DUAL],

    financial_fields=[
        FieldDefinition("borrower", "Name of the borrower", required=True),
        FieldDefinition("lender", "Name of the lender", required=True),
        FieldDefinition("loan_amount", "Principal loan amount", field_type="currency",
                        required=True, aliases=["principal", "commitment amount"]),
        FieldDefinition("interest_rate", "Interest rate", field_type="percentage",
                        required=True, aliases=["rate", "coupon"]),
        FieldDefinition("rate_type", "Fixed or variable rate",
                        aliases=["rate structure"]),
        FieldDefinition("index_rate", "Index for variable rate",
                        aliases=["SOFR", "LIBOR", "prime", "benchmark"]),
        FieldDefinition("spread", "Spread over index", field_type="percentage",
                        aliases=["margin"]),
        FieldDefinition("rate_floor", "Interest rate floor", field_type="percentage",
                        aliases=["floor"]),
        FieldDefinition("rate_cap", "Interest rate cap", field_type="percentage",
                        aliases=["cap", "ceiling"]),
        FieldDefinition("maturity_date", "Loan maturity date", field_type="date",
                        required=True, aliases=["due date"]),
        FieldDefinition("origination_date", "Loan origination date", field_type="date",
                        aliases=["closing date", "effective date"]),
        FieldDefinition("loan_term", "Term of the loan",
                        aliases=["term"]),
        FieldDefinition("amortization", "Amortization period/schedule",
                        aliases=["amortization schedule", "amort"]),
        FieldDefinition("payment_amount", "Monthly/periodic payment", field_type="currency",
                        aliases=["debt service", "monthly payment"]),
        FieldDefinition("io_period", "Interest-only period",
                        aliases=["interest only", "IO"]),
        FieldDefinition("prepayment_terms", "Prepayment penalty/premium terms",
                        aliases=["prepayment", "defeasance", "yield maintenance"]),
        FieldDefinition("extension_options", "Loan extension options",
                        aliases=["extension"]),
        FieldDefinition("ltv", "Loan-to-value ratio", field_type="percentage",
                        aliases=["LTV"]),
        FieldDefinition("dscr_requirement", "Debt service coverage ratio requirement",
                        field_type="number", aliases=["DSCR"]),
        FieldDefinition("collateral", "Description of collateral property",
                        aliases=["security", "pledged property"]),
        FieldDefinition("recourse", "Recourse/non-recourse status",
                        aliases=["carve-outs", "bad boy guaranty"]),
        FieldDefinition("reserves", "Required reserves (tax, insurance, capex, etc.)",
                        aliases=["escrows", "impounds"]),
    ],

    clause_types=[
        "representations_warranties",
        "covenants",
        "events_of_default",
        "remedies",
        "transfer_restrictions",
        "insurance_requirements",
        "environmental",
        "due_on_sale",
        "subordination",
        "cross_default",
        "reporting_requirements",
        "cash_management",
        "lockbox",
    ],

    llm_system_prompt="""You are a commercial real estate loan document analyst. Extract financial
terms and legal provisions from loan documents, promissory notes, and
mortgage agreements with precision. Pay special attention to rate structures,
payment waterfalls, and default triggers.""",

    llm_extraction_prompt="""Extract the following financial terms from this loan document.
Return results as a JSON array of objects with keys:
term_type, term_label, value_raw, value_numeric, value_unit,
effective_date, expiration_date, section_ref, page_number, confidence

Financial terms to extract:
{field_list}

Document text:
{document_text}""",

    llm_clause_prompt="""Extract the following clause types from this loan document.
Return results as a JSON array with keys:
clause_type, section_ref, clause_title, full_text, summary, page_number, confidence

Clause types to extract:
{clause_list}

Document text:
{document_text}"""
)


CLOSING_DOCUMENT = DocumentTemplate(
    document_type="closing",
    display_name="Purchase/Closing Document",
    description="Purchase and sale agreements, closing statements, settlement documents",
    extraction_modes=[ExtractionMode.DUAL],

    financial_fields=[
        FieldDefinition("buyer", "Name of the buyer/purchaser", required=True),
        FieldDefinition("seller", "Name of the seller", required=True),
        FieldDefinition("property_address", "Property address", required=True),
        FieldDefinition("purchase_price", "Purchase price", field_type="currency",
                        required=True, aliases=["sale price", "consideration"]),
        FieldDefinition("earnest_money", "Earnest money deposit", field_type="currency",
                        aliases=["deposit", "good faith deposit"]),
        FieldDefinition("closing_date", "Closing date", field_type="date",
                        required=True),
        FieldDefinition("due_diligence_period", "Due diligence/inspection period",
                        aliases=["inspection period", "feasibility period"]),
        FieldDefinition("financing_contingency", "Financing contingency terms",
                        aliases=["loan contingency", "mortgage contingency"]),
        FieldDefinition("prorations", "Proration details (taxes, rent, etc.)",
                        aliases=["adjustments"]),
        FieldDefinition("closing_costs", "Closing cost allocation",
                        field_type="currency"),
        FieldDefinition("title_company", "Title company/escrow agent",
                        aliases=["escrow", "settlement agent"]),
        FieldDefinition("price_psf", "Price per square foot", field_type="currency",
                        aliases=["$/SF"]),
        FieldDefinition("cap_rate", "Capitalization rate", field_type="percentage",
                        aliases=["cap rate", "going-in cap"]),
    ],

    clause_types=[
        "representations_warranties",
        "conditions_to_closing",
        "indemnification",
        "environmental",
        "assignment",
        "default_remedies",
        "due_diligence",
        "title_survey",
        "casualty_condemnation",
        "confidentiality",
    ],

    llm_system_prompt="""You are a real estate transaction analyst. Extract key terms from
purchase and sale agreements, closing statements, and settlement documents.""",

    llm_extraction_prompt="""Extract the following terms from this closing/purchase document.
Return results as a JSON array of objects with keys:
term_type, term_label, value_raw, value_numeric, value_unit,
effective_date, section_ref, page_number, confidence

Terms to extract:
{field_list}

Document text:
{document_text}""",

    llm_clause_prompt="""Extract the following clause types from this document.
Return results as a JSON array with keys:
clause_type, section_ref, clause_title, full_text, summary, page_number, confidence

Clause types:
{clause_list}

Document text:
{document_text}"""
)


GUARANTEE_AGREEMENT = DocumentTemplate(
    document_type="guarantee",
    display_name="Guarantee Agreement",
    description="Personal or corporate guarantees associated with leases or loans",
    extraction_modes=[ExtractionMode.DUAL],

    financial_fields=[
        FieldDefinition("guarantor", "Name of the guarantor", required=True),
        FieldDefinition("guaranteed_party", "Party receiving the guarantee",
                        required=True, aliases=["beneficiary", "landlord", "lender"]),
        FieldDefinition("principal_obligor", "Primary obligor (tenant/borrower)",
                        required=True, aliases=["tenant", "borrower"]),
        FieldDefinition("guarantee_type", "Type of guarantee",
                        aliases=["full", "limited", "good guy", "springing"]),
        FieldDefinition("guarantee_amount", "Maximum guarantee amount", field_type="currency",
                        aliases=["cap", "maximum liability"]),
        FieldDefinition("guarantee_term", "Duration of the guarantee",
                        aliases=["term", "expiration"]),
        FieldDefinition("burn_off_provisions", "Conditions for guarantee reduction",
                        aliases=["burn-off", "step-down", "release conditions"]),
        FieldDefinition("financial_covenants", "Financial covenants/net worth requirements",
                        aliases=["net worth", "liquidity requirement"]),
    ],

    clause_types=[
        "scope_of_guarantee",
        "waivers",
        "subrogation",
        "reinstatement",
        "financial_reporting",
        "transfer_restrictions",
        "events_of_default",
        "remedies",
        "governing_law",
    ],

    llm_system_prompt="""You are a legal analyst specializing in guarantee agreements.
Extract all material terms and provisions with precision.""",

    llm_extraction_prompt="""Extract the following terms from this guarantee agreement.
Return as JSON array with keys:
term_type, term_label, value_raw, value_numeric, value_unit,
section_ref, page_number, confidence

Terms:
{field_list}

Document text:
{document_text}""",

    llm_clause_prompt="""Extract the following clauses from this guarantee agreement.
Return as JSON array with keys:
clause_type, section_ref, clause_title, full_text, summary, page_number, confidence

Clauses:
{clause_list}

Document text:
{document_text}"""
)


RENT_ROLL = DocumentTemplate(
    document_type="rent_roll",
    display_name="Rent Roll",
    description="Tenant rent rolls — tabular extraction of unit-level occupancy and rent data",
    extraction_modes=[ExtractionMode.TABULAR],

    table_columns=[
        FieldDefinition("unit_number", "Unit or suite number", required=True,
                        aliases=["unit", "suite", "space", "#", "unit #"]),
        FieldDefinition("tenant_name", "Tenant name", required=True,
                        aliases=["tenant", "lessee", "occupant"]),
        FieldDefinition("square_footage", "Square footage", field_type="number",
                        aliases=["sf", "sqft", "rsf", "area", "sq ft"]),
        FieldDefinition("lease_start", "Lease start date", field_type="date",
                        aliases=["start", "commencement", "move-in"]),
        FieldDefinition("lease_end", "Lease end date", field_type="date",
                        aliases=["end", "expiration", "move-out"]),
        FieldDefinition("monthly_rent", "Monthly rent", field_type="currency",
                        aliases=["rent", "monthly", "base rent"]),
        FieldDefinition("annual_rent", "Annual rent", field_type="currency",
                        aliases=["annual", "yearly rent"]),
        FieldDefinition("rent_psf", "Rent per square foot", field_type="currency",
                        aliases=["$/sf", "psf", "rent/sf"]),
        FieldDefinition("status", "Occupancy status",
                        aliases=["status", "occupied", "vacant"]),
    ],

    llm_system_prompt="""You are a real estate data analyst. Parse rent roll tables and
map columns to standardized field names. Handle variations in column headers
and formats across different property management systems.""",

    llm_extraction_prompt="""Parse this rent roll data into structured rows.
Map the columns to these standardized fields:
{field_list}

The data may have varying column headers. Match them to the closest field.
Return as a JSON array of row objects.

Rent roll data:
{document_text}"""
)


OPERATING_STATEMENT = DocumentTemplate(
    document_type="operating_statement",
    display_name="Operating Statement",
    description="Property operating statements / income & expense reports",
    extraction_modes=[ExtractionMode.TABULAR],

    table_columns=[
        FieldDefinition("line_item", "Line item description", required=True,
                        aliases=["description", "item", "account"]),
        FieldDefinition("category", "Category — revenue, expense, noi, etc.",
                        required=True, aliases=["type", "section"]),
        FieldDefinition("amount", "Dollar amount", field_type="currency",
                        required=True, aliases=["total", "actual", "budget"]),
        FieldDefinition("amount_psf", "Amount per square foot", field_type="currency",
                        aliases=["$/sf", "psf"]),
        FieldDefinition("period", "Time period", aliases=["year", "month", "quarter"]),
    ],

    llm_system_prompt="""You are a commercial real estate financial analyst. Parse operating
statements and classify each line item into the correct category (revenue,
expense, NOI, debt service, etc.). Identify subtotals and totals.""",

    llm_extraction_prompt="""Parse this operating statement into structured line items.
For each line, identify:
- line_item: the description
- category: one of [revenue, expense, noi, debt_service, capital, other]
- subcategory: more specific classification
- amount: the dollar value
- is_subtotal: true if this is a subtotal row
- is_total: true if this is a grand total row

Return as a JSON array of objects.

Operating statement data:
{document_text}"""
)


GENERAL_LEDGER = DocumentTemplate(
    document_type="general_ledger",
    display_name="General Ledger",
    description="General ledger detail / transaction reports",
    extraction_modes=[ExtractionMode.TABULAR],

    table_columns=[
        FieldDefinition("account_code", "Account/GL code",
                        aliases=["account #", "gl code", "acct"]),
        FieldDefinition("account_name", "Account name",
                        aliases=["account", "description"]),
        FieldDefinition("entry_date", "Transaction date", field_type="date",
                        aliases=["date", "post date", "effective date"]),
        FieldDefinition("description", "Transaction description",
                        aliases=["memo", "narrative", "detail"]),
        FieldDefinition("debit", "Debit amount", field_type="currency"),
        FieldDefinition("credit", "Credit amount", field_type="currency"),
        FieldDefinition("balance", "Running balance", field_type="currency"),
        FieldDefinition("vendor", "Vendor/payee name",
                        aliases=["payee", "name"]),
        FieldDefinition("reference", "Reference number",
                        aliases=["ref", "check #", "invoice #"]),
    ],

    llm_system_prompt="""You are an accounting data analyst. Parse general ledger detail
reports into structured transaction entries. Map columns accurately
even when headers vary between accounting systems.""",

    llm_extraction_prompt="""Parse this general ledger data into structured entries.
Map columns to these fields:
{field_list}

Return as a JSON array of transaction objects.

General ledger data:
{document_text}"""
)


# ─── Template Registry ───────────────────────────────────────────────

TEMPLATES: Dict[str, DocumentTemplate] = {
    "lease": LEASE_AGREEMENT,
    "loan": LOAN_DOCUMENT,
    "closing": CLOSING_DOCUMENT,
    "guarantee": GUARANTEE_AGREEMENT,
    "rent_roll": RENT_ROLL,
    "operating_statement": OPERATING_STATEMENT,
    "general_ledger": GENERAL_LEDGER,
}


def get_template(document_type: str) -> Optional[DocumentTemplate]:
    """Get the extraction template for a document type."""
    return TEMPLATES.get(document_type)


def list_templates() -> List[Dict]:
    """List all available document templates."""
    return [
        {
            "type": t.document_type,
            "name": t.display_name,
            "description": t.description,
            "modes": [m.value for m in t.extraction_modes],
        }
        for t in TEMPLATES.values()
    ]
