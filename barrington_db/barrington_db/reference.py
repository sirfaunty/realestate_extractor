"""
reference.py — Static reference data for the Barrington portfolio.

Defines:
  * PROPERTIES: the property universe and identity metadata.
  * LINE_ITEMS: the canonical cash-flow taxonomy that all heterogeneous
    source layouts map into.
  * LINE_ITEM_ALIASES: maps raw source labels -> canonical codes.

NOTE on the portfolio universe (per ownership):
  - Combined Centre is its OWN three-building office complex in Northbrook.
    It is NOT a roll-up of Corporate Center I & II.
  - Corporate Center I (CCI) and Corporate Center II (CCII) are separate.
  All three are independent properties and are summed in the portfolio roll-up.
"""

# ---------------------------------------------------------------------------
# Property universe
# ---------------------------------------------------------------------------
PROPERTIES = [
    # code,     name,                          yardi_id,   market,       submarket
    ("DRAKE",   "Drake Oakbrook Plaza",        "o0493400", "Chicago",    "Oak Brook"),
    ("ONB",     "One Northbrook Place",        "o0047200", "Chicago",    "Northbrook"),
    ("OHP1",    "O'Hare Plaza I",              "o0454700", "Chicago",    "O'Hare"),
    ("OHP2",    "O'Hare Plaza II",             "o0475200", "Chicago",    "O'Hare"),
    ("CCI",     "Corporate Center I",          "o0449001", "Chicago",    "Northbrook"),
    ("CCII",    "Corporate Center II",         "o0448900", "Chicago",    "Northbrook"),
    ("COMBINED","Combined Centre",             None,       "Chicago",    "Northbrook"),
    ("JTM",     "Milwaukee Portfolio - JTM",   "o0175401", "Milwaukee",  "CBD"),
    ("MB",      "MB MKE",                      "o0175405", "Milwaukee",  "CBD"),
    ("WACKER",  "Wacker",                      None,       "Chicago",    "CBD"),
]

# Map each cash-flow source file to its property code.
CASHFLOW_FILES = {
    "DRAKE":    "03 Drake Cash Flow 2026-04.xlsx",
    "ONB":      "03 ONB Cash Flow 2026-04.pdf",
    "OHP1":     "03 OHP I Cash Flow 04.2026.pdf",
    "OHP2":     "03 Cash Flow OHP II 04.2026.pdf",
    "COMBINED": "03 Combined Cash Flow 2026-04.pdf",
    "JTM":      "03 JTM Cash Flow 2026-04.pdf",
    "MB":       "03 MB- Cashflow 2026-04.pdf",
    "WACKER":   "Wacker Cash Flow 4-26.pdf",
    # CCI / CCII: corp-consol file carries the detail; handled separately.
    "CORPCONSOL": "03 CorpConsol Cash Flow 2026-04.pdf",
}

RENTROLL_FILES = {
    "DRAKE":    "Drake RR 2026-04.pdf",
    "ONB":      "ONB RR 2026-04.pdf",
    "OHP1":     "OHP I RR 2026.4.pdf",
    "OHP2":     "OHP II RR 2026.4.pdf",
    "CCI":      "CCI RR 2026-04.pdf",
    "CCII":     "CCII RR 2026-04.pdf",
    "COMBINED": "Combined RR 2026-04.pdf",
    "JTM":      "JTM RR 2026-04.pdf",
    "MB":       "MB RR 2026-04.pdf",
    "WACKER":   "Wacker Rent Roll 4-26.pdf",
}

# ---------------------------------------------------------------------------
# Canonical line-item taxonomy
#   (code, label, section, sort_order, is_capital, is_subtotal)
# Outflows are stored as NEGATIVE numbers (natural sign convention).
# ---------------------------------------------------------------------------
LINE_ITEMS = [
    # --- Operating ---
    ("REVENUE",          "Revenue",                              "OPERATING",    10, 0, 0),
    ("REVENUE_ADJ",      "Revenue Adjustment",                   "OPERATING",    15, 0, 0),
    ("OPEX",             "Operating Expenses (recoverable)",     "OPERATING",    20, 0, 0),
    ("NOI",              "Net Operating Income",                 "SUBTOTAL",     30, 0, 1),
    ("TAX_PAYMENT",      "Tax Payments",                         "OPERATING",    32, 0, 0),
    ("INSURANCE_PAYMENT","Insurance Payments",                   "OPERATING",    34, 0, 0),
    ("NOI_AFTER_TAX",    "NOI after Tax/Insurance",              "SUBTOTAL",     36, 0, 1),

    # --- Debt service ---
    ("INTEREST",         "Mortgage Interest",                    "DEBT_SERVICE", 40, 0, 0),
    ("PRINCIPAL",        "Mortgage Principal",                   "DEBT_SERVICE", 42, 0, 0),

    # --- Reserves ---
    ("RESERVE_ACTIVITY", "Reserve Activity (Tax/Ins)",           "RESERVE",      50, 0, 0),
    ("NCF_AFTER_DS",     "Net Cash Flow after Debt Service",     "SUBTOTAL",     55, 0, 1),

    # --- Capital (focus area) ---
    ("CAP_BUILDING",     "Building Improvements / Base Bldg & LL Work", "CAPITAL", 60, 1, 0),
    ("CAP_TI",           "Tenant Improvements",                  "CAPITAL",      62, 1, 0),
    ("CAP_LC",           "Leasing Commissions",                  "CAPITAL",      64, 1, 0),
    ("CAP_DEFERRED",     "Deferred / Other Leasing Costs",       "CAPITAL",      66, 1, 0),
    ("CAP_NONOP",        "Non-Operating / Legal Capital",        "CAPITAL",      68, 1, 0),
    ("CAP_SUBTOTAL",     "Subtotal: Capital Items",              "SUBTOTAL",     70, 1, 1),
    ("NCF_AFTER_CAPEX",  "Net Cash Flow after Capital",          "SUBTOTAL",     75, 0, 1),

    # --- Non-operating ---
    ("NONOP_OTHER",      "Non-Operating / Refunds / Misc",       "NON_OPERATING",80, 0, 0),

    # --- Financing ---
    ("CONTRIBUTION",     "Ownership Contribution",               "FINANCING",    90, 0, 0),
    ("DISTRIBUTION",     "Ownership Distribution",               "FINANCING",    92, 0, 0),
    ("NCF_FINAL",        "Net Cash Flow after Contrib/Distrib",  "SUBTOTAL",     95, 0, 1),
]

# Raw source label (lowercased, stripped) -> canonical line_item_code.
# Used by the extractors to normalize each property's idiosyncratic labels.
LINE_ITEM_ALIASES = {
    "revenue": "REVENUE",
    "prepaid rent adjustment": "REVENUE_ADJ",
    "revenue adjustment": "REVENUE_ADJ",
    "expenses (recoverable) prior to tax payment": "OPEX",
    "expenses (recoverable) prior to tax / insurance": "OPEX",
    "net operating income/(loss)": "NOI",
    "net operating income": "NOI",
    "budgeted net operating income": "NOI",
    "less: tax payments": "TAX_PAYMENT",
    "tax payments": "TAX_PAYMENT",
    "less: insurance payments": "INSURANCE_PAYMENT",
    "insurance payments": "INSURANCE_PAYMENT",
    "interest payment": "INTEREST",
    "mortgage interest": "INTEREST",
    "principle payment": "PRINCIPAL",
    "principal payment": "PRINCIPAL",
    "mortgage principal": "PRINCIPAL",
    "reserve activity total": "RESERVE_ACTIVITY",
    "less: tax reserve deposit": "RESERVE_ACTIVITY",
    "building improvements": "CAP_BUILDING",
    "building improvement": "CAP_BUILDING",
    "tenant improvements": "CAP_TI",
    "leasing commissions": "CAP_LC",
    "deferred expenses": "CAP_DEFERRED",
    "leasing other costs": "CAP_DEFERRED",
    "leasing other costs/deffered leasing": "CAP_DEFERRED",
    "legal capital/non op/other leasing costs": "CAP_NONOP",
    "non-operating expenses": "CAP_NONOP",
    "subtotal: capital items": "CAP_SUBTOTAL",
    "ownership contribution": "CONTRIBUTION",
    "add: ownership contribution": "CONTRIBUTION",
    "ownership contributions": "CONTRIBUTION",
    "ownership distribution": "DISTRIBUTION",
    "less: ownership distribution": "DISTRIBUTION",
    "deduct: ownership distribution": "DISTRIBUTION",
}

# ---------------------------------------------------------------------------
# Property-specific extraction config for non-standard PDF layouts.
#   noi_aliases:   extra raw-label substrings that mean NOI for this property
#   capital_label_map: raw-label substring (lowercase) -> canonical capital code
#                  Used when the canonical total sits on a custom-labeled row
#                  (e.g. Wacker 'Total Tenant Improvements:').
# Order matters: more specific substrings should be checked first, so these
# are lists of (substring, code) pairs evaluated in order.
# ---------------------------------------------------------------------------
PROPERTY_EXTRACT_CONFIG = {
    # JTM & MB present capital in a detail schedule as POSITIVE spend; flip to
    # the canonical outflow-negative convention.
    "JTM": {"flip_capital_sign": True},
    "MB":  {"flip_capital_sign": True},
    "WACKER": {
        "noi_label_substring": "operating income (includes",
        "skip_generic_capital": True,
        "capital_label_map": [
            ("total building improvements:", "CAP_BUILDING"),
            ("total tenant improvements:", "CAP_TI"),
            ("total: lease commissions", "CAP_LC"),
            ("total lease commissions:", "CAP_LC"),
            ("subtotal: committed/in lease lease commissions", "CAP_LC"),
        ],
    },
    # OHP I / II: Building/TI/LC are section headers with no monthly totals;
    # only the combined 'Subtotal: Leasing Related Capital Items' carries
    # monthly capital. Map that to the capital subtotal.
    "OHP1": {
        "capital_label_map": [
            ("subtotal: leasing related capital items", "CAP_SUBTOTAL"),
        ],
    },
    "OHP2": {
        "capital_label_map": [
            ("subtotal: leasing related capital items", "CAP_SUBTOTAL"),
        ],
    },
}

