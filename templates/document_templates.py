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


class FieldPriority(Enum):
    """How important a field is for a given document type."""
    CRITICAL = "critical"   # Must always be extracted — deal-defining terms
    IMPORTANT = "important" # Should be extracted if present in document
    OPTIONAL = "optional"   # Nice to have — may not appear in all docs


@dataclass
class FieldDefinition:
    """Defines a single field to extract."""
    name: str
    description: str
    field_type: str = "text"      # text, number, date, currency, percentage, boolean
    required: bool = False
    priority: FieldPriority = FieldPriority.OPTIONAL
    aliases: List[str] = field(default_factory=list)  # alternative names in docs
    # Regex patterns for prose-based extraction (e.g., "fixed rate" → rate_type = "fixed")
    prose_patterns: List[str] = field(default_factory=list)


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
        # ── CRITICAL: Deal-defining terms ──
        FieldDefinition("tenant_name", "Name of the tenant/lessee",
                        required=True, priority=FieldPriority.CRITICAL,
                        aliases=["lessee", "tenant"]),
        FieldDefinition("landlord_name", "Name of the landlord/lessor",
                        required=True, priority=FieldPriority.CRITICAL,
                        aliases=["lessor", "landlord", "owner"]),
        FieldDefinition("property_address", "Full address of the leased premises",
                        required=True, priority=FieldPriority.CRITICAL,
                        aliases=["premises", "leased premises"]),
        FieldDefinition("base_rent", "Base/minimum rent amount", field_type="currency",
                        required=True, priority=FieldPriority.CRITICAL,
                        aliases=["minimum rent", "fixed rent", "monthly rent"]),
        FieldDefinition("lease_commencement", "Lease start date", field_type="date",
                        required=True, priority=FieldPriority.CRITICAL,
                        aliases=["commencement date", "start date"]),
        FieldDefinition("lease_expiration", "Lease end date", field_type="date",
                        required=True, priority=FieldPriority.CRITICAL,
                        aliases=["expiration date", "end date", "termination date"]),

        # ── IMPORTANT: Key financial terms ──
        FieldDefinition("suite_unit", "Suite or unit number",
                        priority=FieldPriority.IMPORTANT,
                        aliases=["suite", "unit", "space"]),
        FieldDefinition("square_footage", "Rentable or usable square footage",
                        field_type="number", priority=FieldPriority.IMPORTANT,
                        aliases=["rsf", "usf", "sf", "area", "square feet"]),
        FieldDefinition("lease_term", "Length of lease term",
                        priority=FieldPriority.IMPORTANT,
                        aliases=["term", "initial term"]),
        FieldDefinition("escalation_type", "Type of rent escalation (fixed %, CPI, fair market)",
                        priority=FieldPriority.IMPORTANT,
                        aliases=["increases", "adjustments"],
                        prose_patterns=[
                            r"(?i)(CPI|consumer\s+price\s+index)",
                            r"(?i)(fair\s+market\s+(?:value|rent))",
                            r"(?i)(fixed|annual)\s+(?:increase|escalation)",
                        ]),
        FieldDefinition("escalation_rate", "Escalation rate or schedule", field_type="percentage",
                        priority=FieldPriority.IMPORTANT,
                        aliases=["annual increase", "cpi adjustment"]),
        FieldDefinition("cam_charges", "Common area maintenance charges", field_type="currency",
                        priority=FieldPriority.IMPORTANT,
                        aliases=["CAM", "operating expenses", "additional rent"]),
        FieldDefinition("cam_structure", "Lease expense structure (NNN, modified gross, full service)",
                        priority=FieldPriority.IMPORTANT,
                        aliases=["lease type", "expense structure"],
                        prose_patterns=[
                            r"(?i)(triple\s*net|NNN)",
                            r"(?i)(modified\s+gross)",
                            r"(?i)(full\s+service|gross\s+lease)",
                        ]),
        FieldDefinition("security_deposit", "Security deposit amount", field_type="currency",
                        priority=FieldPriority.IMPORTANT,
                        aliases=["deposit"]),

        # ── OPTIONAL ──
        FieldDefinition("rent_frequency", "How often rent is paid",
                        priority=FieldPriority.OPTIONAL,
                        aliases=["payment frequency"]),
        FieldDefinition("ti_allowance", "Tenant improvement allowance", field_type="currency",
                        priority=FieldPriority.OPTIONAL,
                        aliases=["TI", "improvement allowance", "build-out allowance"]),
        FieldDefinition("free_rent", "Free rent period",
                        priority=FieldPriority.OPTIONAL,
                        aliases=["rent abatement", "concession"]),
        FieldDefinition("percentage_rent", "Percentage rent terms", field_type="percentage",
                        priority=FieldPriority.OPTIONAL,
                        aliases=["overage rent"]),
        FieldDefinition("percentage_rent_breakpoint", "Breakpoint for percentage rent",
                        field_type="currency", priority=FieldPriority.OPTIONAL,
                        aliases=["breakpoint", "natural breakpoint"]),
        FieldDefinition("renewal_options", "Renewal/extension option terms",
                        priority=FieldPriority.OPTIONAL,
                        aliases=["extension", "option to renew"]),
        FieldDefinition("termination_options", "Early termination rights",
                        priority=FieldPriority.OPTIONAL,
                        aliases=["early termination", "kick-out", "break clause"]),
        FieldDefinition("guarantor", "Name of guarantor if any",
                        priority=FieldPriority.OPTIONAL,
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
        # ── CRITICAL: Deal-defining terms — must always be extracted ──
        FieldDefinition("borrower", "Name of the borrower/mortgagor",
                        required=True, priority=FieldPriority.CRITICAL,
                        aliases=["mortgagor", "obligor"]),
        FieldDefinition("lender", "Name of the lender/mortgagee",
                        required=True, priority=FieldPriority.CRITICAL,
                        aliases=["mortgagee", "note holder", "payee"]),
        FieldDefinition("loan_amount", "Principal loan amount", field_type="currency",
                        required=True, priority=FieldPriority.CRITICAL,
                        aliases=["principal", "commitment amount", "principal sum",
                                 "amount of debt", "face amount"]),
        FieldDefinition("interest_rate", "Interest rate (annual)", field_type="percentage",
                        required=True, priority=FieldPriority.CRITICAL,
                        aliases=["rate", "coupon", "note rate", "contract rate"]),
        FieldDefinition("maturity_date", "Loan maturity date", field_type="date",
                        required=True, priority=FieldPriority.CRITICAL,
                        aliases=["due date", "final payment date"]),
        FieldDefinition("origination_date", "Loan origination/closing date", field_type="date",
                        priority=FieldPriority.CRITICAL,
                        aliases=["closing date", "effective date", "dated as of"]),
        FieldDefinition("collateral", "Description of collateral property",
                        priority=FieldPriority.CRITICAL,
                        aliases=["security", "pledged property", "mortgaged property"]),

        # ── IMPORTANT: Rate structure & payment terms ──
        FieldDefinition("rate_type", "Whether rate is fixed or variable", field_type="text",
                        priority=FieldPriority.IMPORTANT,
                        aliases=["rate structure"],
                        prose_patterns=[
                            r"(?i)(?:fixed)\s*(?:rate|interest)",
                            r"(?i)(?:variable|adjustable|floating)\s*(?:rate|interest)",
                        ]),
        FieldDefinition("loan_term", "Term of the loan (e.g., '30 years', '10 years')",
                        priority=FieldPriority.IMPORTANT,
                        aliases=["term", "loan term", "mortgage term"]),
        FieldDefinition("payment_amount", "Monthly/periodic payment amount", field_type="currency",
                        priority=FieldPriority.IMPORTANT,
                        aliases=["debt service", "monthly payment", "installment"]),
        FieldDefinition("amortization", "Amortization period or schedule",
                        priority=FieldPriority.IMPORTANT,
                        aliases=["amortization schedule", "amort", "amortization period"],
                        prose_patterns=[
                            r"(?i)amortiz(?:ed|ation)\s+(?:over|period|schedule)?\s*([\w\s]+(?:year|month)s?)",
                        ]),
        FieldDefinition("recourse", "Recourse or non-recourse status",
                        priority=FieldPriority.IMPORTANT,
                        aliases=["carve-outs", "bad boy guaranty"],
                        prose_patterns=[
                            r"(?i)(non-?\s*recourse)",
                            r"(?i)(full\s*recourse)",
                            r"(?i)(limited\s*recourse)",
                        ]),
        FieldDefinition("prepayment_terms", "Prepayment penalty/premium terms",
                        priority=FieldPriority.IMPORTANT,
                        aliases=["prepayment", "defeasance", "yield maintenance",
                                 "prepayment premium", "prepayment penalty"],
                        prose_patterns=[
                            r"(?i)((?:no|without)\s+(?:prepayment)?\s*penalty)",
                            r"(?i)(defeasance\s*(?:required|permitted|option)?)",
                            r"(?i)(yield\s*maintenance\s*(?:premium)?)",
                            # Match whole prepayment sentence for context
                            r"(?i)(?:note\s+)?(?:may|shall)\s+be\s+(?:subject\s+to\s+)?(prepayment\s+with\s+premium\s+and\s+under\s+the\s+conditions\s+stated\s+therein)",
                            r"(?i)(prepayment\s*(?:penalty|premium|fee)\s*(?:of|equal\s+to|in\s+the\s+amount)?\s*[^.]{0,80})",
                        ]),

        # ── IMPORTANT: Variable rate details (only if variable) ──
        FieldDefinition("index_rate", "Index for variable rate (SOFR, LIBOR, prime)",
                        priority=FieldPriority.IMPORTANT,
                        aliases=["SOFR", "LIBOR", "prime", "benchmark", "reference rate"],
                        prose_patterns=[
                            r"(?i)(SOFR|LIBOR|prime\s*rate|treasury\s*rate|federal\s*funds)",
                        ]),
        FieldDefinition("spread", "Spread/margin over index rate", field_type="percentage",
                        priority=FieldPriority.IMPORTANT,
                        aliases=["margin"]),
        FieldDefinition("rate_floor", "Interest rate floor", field_type="percentage",
                        priority=FieldPriority.OPTIONAL,
                        aliases=["floor"]),
        FieldDefinition("rate_cap", "Interest rate cap", field_type="percentage",
                        priority=FieldPriority.OPTIONAL,
                        aliases=["cap", "ceiling"]),

        # ── OPTIONAL: Additional terms ──
        FieldDefinition("io_period", "Interest-only period",
                        priority=FieldPriority.OPTIONAL,
                        aliases=["interest only", "IO period"],
                        prose_patterns=[
                            r"(?i)(interest[\s-]*only)\s+(?:period|for)?\s*([\w\s]+(?:year|month)s?)",
                        ]),
        FieldDefinition("extension_options", "Loan extension options",
                        priority=FieldPriority.OPTIONAL,
                        aliases=["extension", "extension option"],
                        prose_patterns=[
                            r"(?i)((?:one|two|three|1|2|3)\s+(?:extension|renewal)\s+option)",
                        ]),
        FieldDefinition("ltv", "Loan-to-value ratio", field_type="percentage",
                        priority=FieldPriority.OPTIONAL,
                        aliases=["LTV", "loan to value"]),
        FieldDefinition("dscr_requirement", "Debt service coverage ratio requirement",
                        field_type="number", priority=FieldPriority.OPTIONAL,
                        aliases=["DSCR", "debt service coverage"]),
        FieldDefinition("reserves", "Required reserves (tax, insurance, capex, etc.)",
                        priority=FieldPriority.OPTIONAL,
                        aliases=["escrows", "impounds", "reserve accounts"],
                        prose_patterns=[
                            r"(?i)(tax\s+(?:and\s+insurance\s+)?(?:escrow|reserve))",
                            r"(?i)(replacement\s+reserve)",
                            r"(?i)(capital\s+(?:expenditure|improvement)\s+reserve)",
                        ]),
        FieldDefinition("default_rate", "Default/penalty interest rate", field_type="percentage",
                        priority=FieldPriority.OPTIONAL,
                        aliases=["default interest rate", "penalty rate", "late rate"]),
        FieldDefinition("late_fee", "Late payment fee",
                        priority=FieldPriority.OPTIONAL,
                        aliases=["late charge", "late payment"]),
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

    llm_extraction_prompt="""Extract ONLY the financial terms listed below from this loan document.

RULES:
- If a field is NOT found in the document, set value_raw to null. Do NOT
  repeat the field description as the value.
- For text fields (rate_type, recourse, etc.), extract the ACTUAL value
  from the document (e.g., "Fixed", "Non-recourse", "30 years").
- Be precise with numbers — extract exactly as they appear.

Return a JSON array of objects with keys: term_type, value_raw,
value_numeric, confidence (0-1).

Fields to find:
{field_list}

Document excerpt:
{document_text}""",

    llm_clause_prompt="""Extract the following clause types from this loan document.
Return results as a JSON array with keys:
clause_type, section_ref, clause_title, full_text, summary, page_number, confidence

If a clause type is not found, omit it entirely from the array.

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
        # ── CRITICAL ──
        FieldDefinition("buyer", "Name of the buyer/purchaser",
                        required=True, priority=FieldPriority.CRITICAL),
        FieldDefinition("seller", "Name of the seller",
                        required=True, priority=FieldPriority.CRITICAL),
        FieldDefinition("property_address", "Property address",
                        required=True, priority=FieldPriority.CRITICAL),
        FieldDefinition("purchase_price", "Purchase price", field_type="currency",
                        required=True, priority=FieldPriority.CRITICAL,
                        aliases=["sale price", "consideration"]),
        FieldDefinition("closing_date", "Closing date", field_type="date",
                        required=True, priority=FieldPriority.CRITICAL),
        # ── IMPORTANT ──
        FieldDefinition("earnest_money", "Earnest money deposit", field_type="currency",
                        priority=FieldPriority.IMPORTANT,
                        aliases=["deposit", "good faith deposit"]),
        FieldDefinition("due_diligence_period", "Due diligence/inspection period",
                        priority=FieldPriority.IMPORTANT,
                        aliases=["inspection period", "feasibility period"]),
        FieldDefinition("financing_contingency", "Financing contingency terms",
                        priority=FieldPriority.IMPORTANT,
                        aliases=["loan contingency", "mortgage contingency"]),
        FieldDefinition("title_company", "Title company/escrow agent",
                        priority=FieldPriority.IMPORTANT,
                        aliases=["escrow", "settlement agent"]),
        # ── OPTIONAL ──
        FieldDefinition("prorations", "Proration details (taxes, rent, etc.)",
                        priority=FieldPriority.OPTIONAL,
                        aliases=["adjustments"]),
        FieldDefinition("closing_costs", "Closing cost allocation",
                        field_type="currency", priority=FieldPriority.OPTIONAL),
        FieldDefinition("price_psf", "Price per square foot", field_type="currency",
                        priority=FieldPriority.OPTIONAL, aliases=["$/SF"]),
        FieldDefinition("cap_rate", "Capitalization rate", field_type="percentage",
                        priority=FieldPriority.OPTIONAL,
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
        # ── CRITICAL ──
        FieldDefinition("guarantor", "Name of the guarantor",
                        required=True, priority=FieldPriority.CRITICAL),
        FieldDefinition("guaranteed_party", "Party receiving the guarantee",
                        required=True, priority=FieldPriority.CRITICAL,
                        aliases=["beneficiary", "landlord", "lender"]),
        FieldDefinition("principal_obligor", "Primary obligor (tenant/borrower)",
                        required=True, priority=FieldPriority.CRITICAL,
                        aliases=["tenant", "borrower"]),
        FieldDefinition("guarantee_type", "Type of guarantee (full, limited, good guy, springing)",
                        priority=FieldPriority.CRITICAL,
                        aliases=["full", "limited", "good guy", "springing"],
                        prose_patterns=[
                            r"(?i)(full\s+(?:and\s+unconditional\s+)?guarantee)",
                            r"(?i)(limited\s+guarantee)",
                            r"(?i)(good\s+guy\s+guarantee)",
                            r"(?i)(springing\s+guarantee)",
                        ]),
        FieldDefinition("guarantee_amount", "Maximum guarantee amount", field_type="currency",
                        priority=FieldPriority.CRITICAL,
                        aliases=["cap", "maximum liability"]),
        # ── IMPORTANT ──
        FieldDefinition("guarantee_term", "Duration of the guarantee",
                        priority=FieldPriority.IMPORTANT,
                        aliases=["term", "expiration"]),
        FieldDefinition("burn_off_provisions", "Conditions for guarantee reduction",
                        priority=FieldPriority.IMPORTANT,
                        aliases=["burn-off", "step-down", "release conditions"]),
        FieldDefinition("financial_covenants", "Financial covenants/net worth requirements",
                        priority=FieldPriority.OPTIONAL,
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


# ─── Proforma / Investment Summary ──────────────────────────────────

PROFORMA = DocumentTemplate(
    document_type="proforma",
    display_name="Proforma / Investment Summary",
    description="Forward-looking financial projections, valuation models, acquisition underwriting, and investment summaries",
    extraction_modes=[ExtractionMode.FINANCIAL],

    financial_fields=[
        # ── CRITICAL: Property identification & valuation ──
        FieldDefinition("property_name", "Name of the property",
                        required=True, priority=FieldPriority.CRITICAL,
                        aliases=["asset name", "project name"]),
        FieldDefinition("property_address", "Property address",
                        priority=FieldPriority.CRITICAL,
                        aliases=["property address", "street address", "site address"]),
        FieldDefinition("total_units", "Total number of units", field_type="number",
                        required=True, priority=FieldPriority.CRITICAL,
                        aliases=["total units", "unit count", "total apartment units",
                                 "number of units"]),
        FieldDefinition("property_type", "Property type (multifamily, office, etc.)",
                        priority=FieldPriority.CRITICAL,
                        aliases=["property type", "asset type"]),
        FieldDefinition("year_built", "Year property was built/delivered",
                        field_type="number", priority=FieldPriority.CRITICAL,
                        aliases=["year delivered", "vintage", "built"]),

        # ── CRITICAL: Valuation & pricing ──
        FieldDefinition("purchase_price", "Purchase price or cost basis",
                        field_type="currency", priority=FieldPriority.CRITICAL,
                        aliases=["acquisition price", "purchase price",
                                 "total project cost", "total development cost",
                                 "total acquisition cost"]),
        FieldDefinition("price_per_unit", "Price per unit",
                        field_type="currency", priority=FieldPriority.CRITICAL,
                        aliases=["price per unit", "cost per unit",
                                 "per unit cost", "$/unit"]),
        FieldDefinition("going_in_cap_rate", "Going-in capitalization rate",
                        field_type="percentage", priority=FieldPriority.CRITICAL,
                        aliases=["cap rate", "acquisition cap", "going-in cap"]),
        FieldDefinition("exit_cap_rate", "Exit / terminal capitalization rate",
                        field_type="percentage", priority=FieldPriority.IMPORTANT,
                        aliases=["terminal cap", "reversion cap", "exit cap"]),

        # ── IMPORTANT: Income assumptions ──
        FieldDefinition("gross_potential_rent", "Gross potential rent (GPR)",
                        field_type="currency", priority=FieldPriority.IMPORTANT,
                        aliases=["GPR", "gross potential", "gross rent",
                                 "apartment rents", "total gross potential rent"]),
        FieldDefinition("vacancy_rate", "Vacancy and credit loss assumption",
                        field_type="percentage", priority=FieldPriority.IMPORTANT,
                        aliases=["vacancy", "vacancy loss", "physical vacancy",
                                 "economic vacancy"]),
        FieldDefinition("effective_gross_income", "Effective gross income (EGI)",
                        field_type="currency", priority=FieldPriority.IMPORTANT,
                        aliases=["EGI", "effective income", "total revenues"]),
        FieldDefinition("other_income", "Other income (parking, laundry, misc)",
                        field_type="currency", priority=FieldPriority.IMPORTANT,
                        aliases=["other income", "ancillary income",
                                 "non-rental income", "miscellaneous income"]),
        FieldDefinition("rent_growth_rate", "Assumed annual rent growth",
                        field_type="percentage", priority=FieldPriority.IMPORTANT,
                        aliases=["rent growth", "revenue growth", "GPR growth"]),

        # ── IMPORTANT: Expense assumptions ──
        FieldDefinition("total_operating_expenses", "Total operating expenses",
                        field_type="currency", priority=FieldPriority.IMPORTANT,
                        aliases=["operating expenses", "total expenses", "opex"]),
        FieldDefinition("expense_ratio", "Expense ratio (expenses / EGI)",
                        field_type="percentage", priority=FieldPriority.IMPORTANT,
                        aliases=["operating expense ratio", "OER"]),
        FieldDefinition("expense_growth_rate", "Assumed annual expense growth",
                        field_type="percentage", priority=FieldPriority.IMPORTANT,
                        aliases=["expense growth", "opex growth"]),
        FieldDefinition("management_fee_pct", "Property management fee as % of EGI",
                        field_type="percentage", priority=FieldPriority.IMPORTANT,
                        aliases=["management fee", "mgmt fee"]),

        # ── IMPORTANT: Return metrics ──
        FieldDefinition("net_operating_income", "Net operating income (NOI)",
                        field_type="currency", priority=FieldPriority.IMPORTANT,
                        aliases=["NOI", "net operating"]),
        FieldDefinition("irr", "Internal rate of return (projected)",
                        field_type="percentage", priority=FieldPriority.IMPORTANT,
                        aliases=["IRR", "levered IRR", "unlevered IRR",
                                 "project IRR"]),
        FieldDefinition("equity_multiple", "Equity multiple",
                        field_type="number", priority=FieldPriority.IMPORTANT,
                        aliases=["MOIC", "multiple on invested capital",
                                 "return multiple"]),
        FieldDefinition("cash_on_cash", "Cash-on-cash return",
                        field_type="percentage", priority=FieldPriority.OPTIONAL,
                        aliases=["CoC", "cash yield", "current yield"]),
        FieldDefinition("dscr", "Debt service coverage ratio",
                        field_type="number", priority=FieldPriority.IMPORTANT,
                        aliases=["DSCR", "debt coverage", "coverage ratio"]),

        # ── IMPORTANT: Capital structure ──
        FieldDefinition("total_equity", "Total equity invested",
                        field_type="currency", priority=FieldPriority.IMPORTANT,
                        aliases=["total equity", "equity contribution",
                                 "total equity investment", "KA equity",
                                 "sponsor equity", "LP equity"]),
        FieldDefinition("loan_to_value", "Loan-to-value ratio (LTV)",
                        field_type="percentage", priority=FieldPriority.IMPORTANT,
                        aliases=["LTV"]),

        # ── OPTIONAL: Market context ──
        FieldDefinition("market", "Market / MSA",
                        priority=FieldPriority.OPTIONAL,
                        aliases=["market area", "MSA", "metro area"]),
        FieldDefinition("hold_period", "Projected hold period",
                        priority=FieldPriority.OPTIONAL,
                        aliases=["investment horizon", "holding period"]),
    ],

    llm_system_prompt="""You are a real estate investment analyst. Extract key underwriting
assumptions and return metrics from proforma documents, investment summaries,
and valuation models.""",

    llm_extraction_prompt="""Extract the following terms from this proforma/investment document.
Return results as a JSON array of objects with keys:
term_type, term_label, value_raw, value_numeric, value_unit,
effective_date, section_ref, page_number, confidence

Terms to extract:
{field_list}

Document text:
{document_text}""",
)


# ─── Equity Waterfall / JV Return Calculation ──────────────────────

EQUITY_WATERFALL = DocumentTemplate(
    document_type="equity_waterfall",
    display_name="Equity Waterfall / Return Calculation",
    description="JV equity return calculations, distribution waterfalls, surplus cash computations, and partner capital account statements",
    extraction_modes=[ExtractionMode.FINANCIAL],

    financial_fields=[
        # ── CRITICAL: Partnership structure ──
        FieldDefinition("jv_partner_a", "Name of first JV partner / managing member",
                        required=True, priority=FieldPriority.CRITICAL,
                        aliases=["managing member", "GP", "sponsor", "developer"]),
        FieldDefinition("jv_partner_b", "Name of second JV partner / investor",
                        priority=FieldPriority.CRITICAL,
                        aliases=["investor", "LP", "limited partner", "equity partner"]),
        FieldDefinition("partner_a_pct", "Partner A ownership / distribution percentage",
                        field_type="percentage", priority=FieldPriority.CRITICAL,
                        aliases=["sponsor share", "GP share", "managing member %"]),
        FieldDefinition("partner_b_pct", "Partner B ownership / distribution percentage",
                        field_type="percentage", priority=FieldPriority.CRITICAL,
                        aliases=["investor share", "LP share"]),

        # ── CRITICAL: Capital contributions ──
        FieldDefinition("total_equity_invested", "Total equity invested by all partners",
                        field_type="currency", priority=FieldPriority.CRITICAL,
                        aliases=["total equity", "total capital", "total contributions"]),
        FieldDefinition("partner_a_contribution", "Partner A capital contribution",
                        field_type="currency", priority=FieldPriority.IMPORTANT,
                        aliases=["sponsor equity", "GP contribution"]),
        FieldDefinition("partner_b_contribution", "Partner B capital contribution",
                        field_type="currency", priority=FieldPriority.IMPORTANT,
                        aliases=["investor equity", "LP contribution"]),

        # ── IMPORTANT: Distribution terms ──
        FieldDefinition("preferred_return", "Preferred return rate",
                        field_type="percentage", priority=FieldPriority.IMPORTANT,
                        aliases=["pref", "preferred", "hurdle rate",
                                 "preferred return rate"]),
        FieldDefinition("promote_structure", "Promote / carried interest structure",
                        priority=FieldPriority.IMPORTANT,
                        aliases=["promote", "carried interest", "waterfall tiers",
                                 "incentive allocation"]),
        FieldDefinition("catch_up", "GP catch-up provision",
                        priority=FieldPriority.OPTIONAL,
                        aliases=["catch up", "GP catch-up"]),

        # ── IMPORTANT: Return calculations ──
        FieldDefinition("total_distributions", "Total distributions to date",
                        field_type="currency", priority=FieldPriority.IMPORTANT,
                        aliases=["cumulative distributions", "total distributed"]),
        FieldDefinition("partner_a_distributions", "Distributions to Partner A",
                        field_type="currency", priority=FieldPriority.IMPORTANT,
                        aliases=["amount due to KA", "sponsor distributions"]),
        FieldDefinition("partner_b_distributions", "Distributions to Partner B",
                        field_type="currency", priority=FieldPriority.IMPORTANT,
                        aliases=["amount due to IDP", "investor distributions"]),
        FieldDefinition("irr_blended", "Blended / partnership IRR",
                        field_type="percentage", priority=FieldPriority.IMPORTANT,
                        aliases=["IRR", "project IRR", "partnership IRR"]),
        FieldDefinition("irr_partner_a", "Partner A IRR",
                        field_type="percentage", priority=FieldPriority.OPTIONAL,
                        aliases=["sponsor IRR", "GP IRR"]),
        FieldDefinition("irr_partner_b", "Partner B IRR",
                        field_type="percentage", priority=FieldPriority.OPTIONAL,
                        aliases=["investor IRR", "LP IRR"]),
        FieldDefinition("equity_multiple_blended", "Blended equity multiple",
                        field_type="number", priority=FieldPriority.IMPORTANT,
                        aliases=["equity multiple", "MOIC"]),

        # ── IMPORTANT: Cash flow items ──
        FieldDefinition("surplus_cash", "Surplus cash available for distribution",
                        field_type="currency", priority=FieldPriority.IMPORTANT,
                        aliases=["distributable cash", "available cash",
                                 "net cash flow"]),
        FieldDefinition("property_operations_cf", "Cash flow from property operations",
                        field_type="currency", priority=FieldPriority.IMPORTANT,
                        aliases=["property operations", "operating cash flow"]),
        FieldDefinition("debt_service_paid", "Debt service paid",
                        field_type="currency", priority=FieldPriority.OPTIONAL,
                        aliases=["debt service", "mortgage payments"]),
    ],

    llm_system_prompt="""You are a real estate partnership analyst. Extract capital structure,
distribution terms, and return calculations from equity waterfall documents,
JV return calculations, and partner capital account statements.""",

    llm_extraction_prompt="""Extract the following terms from this equity/partnership document.
Return results as a JSON array of objects with keys:
term_type, term_label, value_raw, value_numeric, value_unit,
effective_date, section_ref, page_number, confidence

Terms to extract:
{field_list}

Document text:
{document_text}""",
)


# ─── HUD Forms ──────────────────────────────────────────────────────

HUD_FORM = DocumentTemplate(
    document_type="hud_form",
    display_name="HUD Form / FHA Document",
    description="HUD cost certifications, FHA endorsement requests, mortgage insurance schedules, and escrow releases",
    extraction_modes=[ExtractionMode.FINANCIAL],

    financial_fields=[
        # ── CRITICAL: Loan identification ──
        FieldDefinition("fha_project_number", "FHA project number",
                        required=True, priority=FieldPriority.CRITICAL,
                        aliases=["project number", "FHA number", "HUD project"]),
        FieldDefinition("property_name", "Name of the property / project",
                        required=True, priority=FieldPriority.CRITICAL,
                        aliases=["project name", "property"]),
        FieldDefinition("borrower", "Borrower / mortgagor name",
                        priority=FieldPriority.CRITICAL,
                        aliases=["mortgagor", "owner", "sponsor"]),

        # ── CRITICAL: Mortgage terms ──
        FieldDefinition("mortgage_amount", "Mortgage amount / insured amount",
                        field_type="currency", priority=FieldPriority.CRITICAL,
                        aliases=["insurable mortgage", "maximum insurable mortgage",
                                 "loan amount", "mortgage insurance"]),
        FieldDefinition("interest_rate", "Interest rate on the HUD-insured mortgage",
                        field_type="percentage", priority=FieldPriority.CRITICAL,
                        aliases=["note rate", "mortgage rate"]),
        FieldDefinition("mortgage_term", "Mortgage term (years)",
                        priority=FieldPriority.IMPORTANT,
                        aliases=["loan term", "amortization term", "term"]),
        FieldDefinition("mip_rate", "Mortgage insurance premium rate",
                        field_type="percentage", priority=FieldPriority.IMPORTANT,
                        aliases=["MIP", "insurance premium", "annual MIP"]),

        # ── IMPORTANT: Cost certification data ──
        FieldDefinition("total_project_cost", "Total project cost (certified)",
                        field_type="currency", priority=FieldPriority.IMPORTANT,
                        aliases=["total cost", "certified cost",
                                 "total development cost"]),
        FieldDefinition("land_cost", "Land / acquisition cost",
                        field_type="currency", priority=FieldPriority.IMPORTANT,
                        aliases=["land", "site acquisition"]),
        FieldDefinition("construction_cost", "Construction / hard cost",
                        field_type="currency", priority=FieldPriority.IMPORTANT,
                        aliases=["hard cost", "building cost", "construction"]),
        FieldDefinition("soft_costs", "Soft costs / professional fees",
                        field_type="currency", priority=FieldPriority.IMPORTANT,
                        aliases=["professional fees", "architecture", "engineering"]),
        FieldDefinition("developer_fee", "Developer fee",
                        field_type="currency", priority=FieldPriority.IMPORTANT,
                        aliases=["builder profit", "BSPRA",
                                 "builder and sponsor profit and risk allowance"]),
        FieldDefinition("replacement_reserves", "Initial replacement reserve deposit",
                        field_type="currency", priority=FieldPriority.OPTIONAL,
                        aliases=["reserve deposit", "initial deposit",
                                 "replacement reserve"]),

        # ── IMPORTANT: Endorsement / escrow ──
        FieldDefinition("endorsement_date", "Final endorsement date",
                        field_type="date", priority=FieldPriority.IMPORTANT,
                        aliases=["final endorsement", "initial endorsement"]),
        FieldDefinition("escrow_amount", "Escrow amount being released or held",
                        field_type="currency", priority=FieldPriority.IMPORTANT,
                        aliases=["escrow", "holdback", "reserve"]),
        FieldDefinition("escrow_account_number", "Escrow account number",
                        priority=FieldPriority.OPTIONAL,
                        aliases=["account number", "escrow #"]),

        # ── OPTIONAL: Unit / rent data from HUD forms ──
        FieldDefinition("total_units", "Total number of units",
                        field_type="number", priority=FieldPriority.OPTIONAL,
                        aliases=["units", "dwelling units"]),
        FieldDefinition("annual_debt_service", "Annual debt service",
                        field_type="currency", priority=FieldPriority.OPTIONAL,
                        aliases=["debt service", "annual payment"]),
    ],

    llm_system_prompt="""You are a HUD/FHA housing finance specialist. Extract key terms from
HUD cost certifications, FHA endorsement documents, mortgage insurance
schedules, and escrow releases.""",

    llm_extraction_prompt="""Extract the following terms from this HUD/FHA document.
Return results as a JSON array of objects with keys:
term_type, term_label, value_raw, value_numeric, value_unit,
effective_date, section_ref, page_number, confidence

Terms to extract:
{field_list}

Document text:
{document_text}""",
)


# ─── Partnership / Operating Agreement ───────────────────────────────

PARTNERSHIP_AGREEMENT = DocumentTemplate(
    document_type="partnership_agreement",
    display_name="Partnership / Operating Agreement",
    description="LLC operating agreements, JV partnership agreements, and amendments",
    extraction_modes=[ExtractionMode.DUAL],

    financial_fields=[
        FieldDefinition("preferred_return_rate", "Preferred return rate for capital contributions",
                        field_type="percentage",
                        priority=FieldPriority.CRITICAL,
                        aliases=["preferred return", "annual return", "pref return"],
                        prose_patterns=[
                            r"(?i)(?:preferred|annual)\s+return\s+(?:of|equal\s+to)\s+(\d+\.?\d*)\s*%",
                            r"(?i)(\d+\.?\d*)\s*%\s+(?:preferred|annual)\s+return",
                            r"(?i)return\s+equal\s+to\s+(\d+\.?\d*)\s*%",
                        ]),
        FieldDefinition("managing_member", "Name of managing member / general partner",
                        priority=FieldPriority.CRITICAL,
                        aliases=["managing partner", "general partner", "GP", "manager"],
                        prose_patterns=[
                            r'(?i)(?:the\s+)?"?(\w[\w\s,]+?)"?\s+shall\s+be\s+the\s+["\']?Managing\s+Member',
                            r"(?i)Managing\s+Member[\"']?\s+(?:means|shall\s+mean)\s+([^,\n.]+)",
                        ]),
        FieldDefinition("investor_member", "Name of investor / limited member",
                        priority=FieldPriority.IMPORTANT,
                        aliases=["limited partner", "LP", "investor", "IDP member"],
                        prose_patterns=[
                            r'(?i)["\']?IDP\s+Member["\']?\s*(?:means|shall\s+mean)\s+([^,\n.]+)',
                            r'(?i)["\']?(?:Investor|Limited)\s+(?:Member|Partner)["\']?\s*(?:means|shall\s+mean)\s+([^,\n.]+)',
                        ]),
        FieldDefinition("membership_interest_pct", "Membership interest percentage split",
                        field_type="percentage",
                        priority=FieldPriority.IMPORTANT,
                        aliases=["membership interest", "ownership percentage", "membership percentage"],
                        prose_patterns=[
                            r"(?i)\w+(?:-\w+)?\s+percent\s*\((\d+\.?\d*)%?\)",
                            r"(?i)(\d+\.?\d*)\s*%\s+(?:membership|ownership)\s+interest",
                            r"(?i)(?:membership|ownership)\s+(?:interest|percentage)\s+.*?(\d+\.?\d*)\s*%",
                        ]),
        FieldDefinition("capital_contribution", "Required capital contribution amount",
                        field_type="currency",
                        priority=FieldPriority.CRITICAL,
                        aliases=["initial capital contribution", "equity contribution", "capital commitment"],
                        prose_patterns=[
                            r"(?i)Capital\s+Contribution[s]?\s+(?:of|in\s+(?:the|an)\s+amount\s+(?:of|equal\s+to))\s+\$?([\d,]+(?:\.\d+)?)",
                        ]),
        FieldDefinition("management_fee_pct", "Management fee percentage",
                        field_type="percentage",
                        priority=FieldPriority.IMPORTANT,
                        aliases=["management fee", "asset management fee", "property management fee"],
                        prose_patterns=[
                            r"(?i)management\s+fee\s+(?:of|equal\s+to)\s+(\d+\.?\d*)\s*%",
                            r"(?i)(\d+\.?\d*)\s*%\s+(?:of\s+)?(?:gross|effective|collected)\s+(?:revenue|rent|income)",
                        ]),
        FieldDefinition("developer_fee", "Developer fee amount or percentage",
                        field_type="currency",
                        priority=FieldPriority.IMPORTANT,
                        aliases=["development fee", "developer's fee"],
                        prose_patterns=[
                            r"(?i)(?:Developer'?s?\s+Fee|Development\s+Fee)\s+(?:of|equal\s+to|in\s+the\s+amount\s+of)\s+\$?([\d,]+(?:\.\d+)?)",
                        ]),
        FieldDefinition("total_units", "Total number of units in the project",
                        field_type="number",
                        priority=FieldPriority.IMPORTANT,
                        aliases=["total units", "number of units", "unit count"],
                        prose_patterns=[
                            r"(?i)(?:approximately\s+)?(\d+)\s+units?\s+of\s+(?:new\s+)?construction",
                            r"(?i)(\d+)\s*[-–]\s*unit",
                        ]),
        FieldDefinition("entity_type", "Legal entity type (LLC, LP, etc.)",
                        priority=FieldPriority.OPTIONAL,
                        aliases=["entity structure", "company type"],
                        prose_patterns=[
                            r"(?i)a\s+\w+\s+(limited\s+liability\s+company|limited\s+partnership|corporation|general\s+partnership)",
                        ]),
        FieldDefinition("formation_state", "State of formation / jurisdiction",
                        priority=FieldPriority.OPTIONAL,
                        aliases=["state of formation", "jurisdiction", "organized under"],
                        prose_patterns=[
                            r"(?i)formed\s+(?:as\s+)?(?:a\s+)?.*?under\s+the\s+([A-Z][a-z]{3,})\s+Limited",
                            r"(?i)a\s+([A-Z][a-z]{3,})\s+limited\s+liability\s+company",
                        ]),
    ],

    clause_types=[
        "distribution_waterfall",
        "capital_contribution",
        "preferred_return",
        "management_authority",
        "major_decisions",
        "transfer_restrictions",
        "buy_sell",
        "dissolution",
        "reporting_requirements",
        "key_person",
        "removal_of_manager",
        "default_remedies",
        "indemnification",
        "non_compete",
        "capital_call",
        "project_financing",
    ],

    llm_system_prompt="""You are a real estate partnership agreement analyst specializing in
LLC operating agreements and JV structures. Extract financial terms and
legal clauses with precision. Pay special attention to distribution
waterfalls, capital contribution structures, preferred returns, and
governance provisions.""",

    llm_extraction_prompt="""Extract ONLY the financial terms listed below from this partnership agreement.

RULES:
- If a field is NOT found in the document, set value_raw to null.
- For percentage fields, extract the exact percentage (e.g., "6.5").
- For entity names, extract the full legal name.
- Be precise with numbers — extract exactly as they appear.

Return a JSON array of objects with keys: term_type, value_raw,
value_numeric, confidence (0-1).

Fields to find:
{field_list}

Document excerpt:
{document_text}""",

    llm_clause_prompt="""Extract the following clause types from this partnership/operating agreement.
For each clause found, preserve the COMPLETE original language.

Clause types to find:
{clause_types}

Return JSON array with: clause_type, clause_title, full_text, section_ref, confidence.

Document excerpt:
{document_text}""",
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
    "proforma": PROFORMA,
    "equity_waterfall": EQUITY_WATERFALL,
    "hud_form": HUD_FORM,
    "partnership_agreement": PARTNERSHIP_AGREEMENT,
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
