"""Maps source labels from MRI / Property Overview to standardized
HistoricalLineCategory values.

Building blocks:
  - LABEL_TO_CATEGORY: explicit mapping for every label seen in the corpus
  - SUBTOTAL_LABELS: rows that are roll-ups in the source (skip when summing
    line-items; they're informational, not their own data)
  - SKIP_LABELS: rows that are headers, blank lines, or cash flow statement
    items we don't carry as P&L line items
  - PATTERN_RULES: substring patterns for labels not explicitly mapped

The mapper is conservative: if a label isn't recognized it returns
HistoricalLineCategory.OTHER and emits a warning. Adding new labels is a
normal maintenance task.

Reasoning notes:
  - "Vacancy Loss" (2018-2021) and "Unit Vacancy" (2022-2024) both → VACANCY
  - "Loss Gain to Lease" (2022+) → LOSS_TO_LEASE; older years didn't have a
    dedicated L/G to Lease line (it was implicit in vacancy)
  - "Manager Free Unit" / "Other Free Unit" / "Employee Rent Credit" / 
    "Concession - Other" → NON_REVENUE_UNITS (these are units pulled out of
    rent roll, distinct from straight concessions)
  - Reimbursement income lines (Utilities Reimb, Electric Reimb, Trash Reimb,
    Cable TV/Internet Reimbursement, Resident Utility Reimbursement) all roll
    into REIMBURSEMENT_INCOME
  - "Misc Tenant Revenue", "Other Fees", "Renters Insurance", "Renters Ins
    Providers Fee", "Damages", "Retained Deposits", "Transfer Fee Income",
    "Lease Termination Fee Income", "Non Refundable-Admin Fees" → OTHER_INCOME
  - "Bad Debt - Residential", "Bad Debt Recovery - Residential", "Rent
    Adj-Move Out" → these are revenue offsets but the source has them in
    different sub-blocks; classify all as OTHER_INCOME (they net out)
  - "Asset Management Fee" → ASSET_MGMT_FEE (distinct from property mgmt fee;
    it's a 1% non-operating fee in Chamberlain's case)
  - "Management Fees-3rd Party", "Incentive Mgmt Fee-3rd Party" → MANAGEMENT_FEES
  - "Interest Expense-KAFSG" → INTERCOMPANY_INTEREST (the KA Financial Services
    Group intercompany loan interest, an FSG construct)
  - "MIP Expense" → MIP
  - "Mortgage Interest", "Interest Expense-Non Mortgage", "Other Mortgage 
    Expenses" → DEBT_SERVICE_INTEREST
  - "Deduct: Building Improvements", "Deduct: Other Capital Expenditures",
    "Deduct: Land Improvements", "Deduct: Mortgage Refinancing/Other Activity"
    → these are below-NOI items on the MRI cash flow statement; the P&L
    side has its own CapEx category if any. We tag them as
    DEBT_SERVICE_PRINCIPAL or ROUTINE_CAPEX based on label.
"""

from __future__ import annotations

import re
from typing import Optional

from ..models.historical import HistoricalLineCategory


# --------------------------------------------------------------------------
# Explicit label → category mapping
# --------------------------------------------------------------------------


LABEL_TO_CATEGORY: dict[str, HistoricalLineCategory] = {
    # ---- Revenue
    "Gross Potential Rent-Residential": HistoricalLineCategory.GROSS_POTENTIAL_RENT,
    "Gross Potential Rent": HistoricalLineCategory.GROSS_POTENTIAL_RENT,
    "Loss Gain to Lease": HistoricalLineCategory.LOSS_TO_LEASE,
    "Vacancy Loss": HistoricalLineCategory.VACANCY,
    "Unit Vacancy": HistoricalLineCategory.VACANCY,
    "Concessions": HistoricalLineCategory.CONCESSIONS,
    "Concession - Other": HistoricalLineCategory.CONCESSIONS,
    "Manager Free Unit": HistoricalLineCategory.NON_REVENUE_UNITS,
    "Other Free Unit": HistoricalLineCategory.NON_REVENUE_UNITS,
    "Employee Rent Credit": HistoricalLineCategory.NON_REVENUE_UNITS,
    "Parking": HistoricalLineCategory.PARKING,

    # Reimbursement income block (-> single category)
    "Utilities Reimb": HistoricalLineCategory.REIMBURSEMENT_INCOME,
    "Electric Reimb": HistoricalLineCategory.REIMBURSEMENT_INCOME,
    "Trash Reimb": HistoricalLineCategory.REIMBURSEMENT_INCOME,
    "Cable TV/Internet Reimbursement": HistoricalLineCategory.REIMBURSEMENT_INCOME,
    "Resident Utility Reimbursement": HistoricalLineCategory.UTILITIES,  # Actually contra-utility (negative expense)

    # Pet
    "Pet Rent": HistoricalLineCategory.PET_FEES,

    # Late / fee income
    "Late Fees": HistoricalLineCategory.LATE_FEES,
    "NSF Fees": HistoricalLineCategory.LATE_FEES,

    # Other tenant revenue
    "Misc Tenant Revenue": HistoricalLineCategory.MISC_TENANT_REVENUE,
    "Other Fees": HistoricalLineCategory.MISC_TENANT_REVENUE,
    "Non Refundable-Admin Fees": HistoricalLineCategory.MISC_TENANT_REVENUE,
    "Transfer Fee Income": HistoricalLineCategory.MISC_TENANT_REVENUE,
    "Lease Termination Fee Income": HistoricalLineCategory.MISC_TENANT_REVENUE,
    "Damages": HistoricalLineCategory.MISC_TENANT_REVENUE,
    "Renters Insurance": HistoricalLineCategory.MISC_TENANT_REVENUE,
    "Renters Ins Providers Fee": HistoricalLineCategory.MISC_TENANT_REVENUE,
    "Retained Deposits": HistoricalLineCategory.MISC_TENANT_REVENUE,
    "Rent Adj-Move Out": HistoricalLineCategory.MISC_TENANT_REVENUE,
    "Bad Debt Recovery - Residential": HistoricalLineCategory.MISC_TENANT_REVENUE,
    "Bad Debt - Residential": HistoricalLineCategory.MISC_TENANT_REVENUE,
    "Bad Debt Expense": HistoricalLineCategory.NON_OPERATING_OTHER,  # G&A bad debt
    "Guest Suite Rent": HistoricalLineCategory.OTHER_INCOME,
    "Guest Suite Expense": HistoricalLineCategory.REPAIRS_MAINTENANCE,

    # Senior housing block (Chamberlain has a tiny amount of this)
    "Laundry Revenue": HistoricalLineCategory.OTHER_INCOME,
    "Misc Revenue": HistoricalLineCategory.OTHER_INCOME,

    # Interest income / TIF
    "Interest Revenue-Non Tenant": HistoricalLineCategory.INTEREST_INCOME,
    "Interest Revenue-Money Market": HistoricalLineCategory.INTEREST_INCOME,
    "Interest Revenue-KAFSG": HistoricalLineCategory.INTEREST_INCOME,
    "Miscellaneous Revenue- TIF Receivable": HistoricalLineCategory.TIF_REVENUE,

    # ---- OpEx: Advertising/Marketing
    "Advertising/Marketing": HistoricalLineCategory.ADVERTISING_MARKETING,
    "Referrals": HistoricalLineCategory.ADVERTISING_MARKETING,
    "Promotion": HistoricalLineCategory.ADVERTISING_MARKETING,
    "Credit/Background Checks": HistoricalLineCategory.ADVERTISING_MARKETING,

    # ---- OpEx: Administrative
    "Seminars & Subscriptions": HistoricalLineCategory.ADMINISTRATIVE,
    "Office Supplies": HistoricalLineCategory.ADMINISTRATIVE,
    "Postage/Shipping": HistoricalLineCategory.ADMINISTRATIVE,
    "Office Equipment": HistoricalLineCategory.ADMINISTRATIVE,
    "Photocopies": HistoricalLineCategory.ADMINISTRATIVE,
    "Revenue Management": HistoricalLineCategory.ADMINISTRATIVE,
    "Automated AP System": HistoricalLineCategory.ADMINISTRATIVE,
    "Accounting": HistoricalLineCategory.ADMINISTRATIVE,
    "Accounting & Audit Fees": HistoricalLineCategory.ADMINISTRATIVE,
    "Legal Expenses": HistoricalLineCategory.ADMINISTRATIVE,
    "Legal-General & Administrative": HistoricalLineCategory.ADMINISTRATIVE,
    "LL Legal": HistoricalLineCategory.ADMINISTRATIVE,
    "Telephone": HistoricalLineCategory.ADMINISTRATIVE,
    "Cell Phone/Pager": HistoricalLineCategory.ADMINISTRATIVE,
    "Training": HistoricalLineCategory.ADMINISTRATIVE,
    "Permits & Licenses": HistoricalLineCategory.ADMINISTRATIVE,
    "Travel & Entertainment": HistoricalLineCategory.ADMINISTRATIVE,
    "Cable TV/Internet": HistoricalLineCategory.ADMINISTRATIVE,
    "Uniforms": HistoricalLineCategory.ADMINISTRATIVE,
    "Professional Fees": HistoricalLineCategory.ADMINISTRATIVE,
    "Consulting Fees": HistoricalLineCategory.ADMINISTRATIVE,

    # ---- OpEx: Management & Leasing
    "Management Fees-3rd Party": HistoricalLineCategory.MANAGEMENT_FEES,
    "Incentive Mgmt Fee-3rd Party": HistoricalLineCategory.MANAGEMENT_FEES,
    "Leasing Commissions": HistoricalLineCategory.LEASING_COMMISSIONS,
    "LL Leasing Fees-New": HistoricalLineCategory.LEASING_COMMISSIONS,
    "Employment Expenses": HistoricalLineCategory.LEASING_COMMISSIONS,

    # ---- OpEx: Payroll
    "Salaries-Maintenance": HistoricalLineCategory.PAYROLL,
    "Salaries-Administrative": HistoricalLineCategory.PAYROLL,
    "Bonuses": HistoricalLineCategory.PAYROLL,
    "Payroll Taxes & Benefits": HistoricalLineCategory.PAYROLL,
    "Employee Benefits": HistoricalLineCategory.PAYROLL,
    "401K": HistoricalLineCategory.PAYROLL,
    "Insurance-Employees": HistoricalLineCategory.PAYROLL,

    # ---- OpEx: R&M
    "Fire Panel Inspection": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Common Area Maintenance": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Cleaning & Janitorial": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Cleaning Supplies": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Elevator": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Exterminating": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Plumbing": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Plumbing Supplies": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Water Heater/Boiler Repair": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Electrical": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Electrical Supplies": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Locks & Keys": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Trash Removal": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Appliances": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Appliances-R&M": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Snow Removal": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Salt-Ice Melt": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Painting": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Painting & Decorating (Common Area)": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Misc Repairs & Maintenance": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Misc Repairs and Maint": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Equipment": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Maintenance Tools & Equipment": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Carpeting & Flooring Repairs": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Flooring Repairs (Common Areas)": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Building Repairs": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "HVAC": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "HVAC Supplies": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Outside Grounds": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Landscape Supplies": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Garage": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Pool": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Pool Supplies": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Emergency Maintenance": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Security- Fire Life Safety": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Extinguisher Inspection": HistoricalLineCategory.REPAIRS_MAINTENANCE,

    # ---- OpEx: Turnover
    "Turnover Expense": HistoricalLineCategory.TURNOVER_COMMON_AREA,
    "Turnover - Carpet Cleaning": HistoricalLineCategory.TURNOVER_COMMON_AREA,
    "Turnover - Cleaning": HistoricalLineCategory.TURNOVER_COMMON_AREA,

    # ---- OpEx: Utilities
    "Gas": HistoricalLineCategory.UTILITIES,
    "Electricity": HistoricalLineCategory.UTILITIES,
    "Electricity-Vacant Units": HistoricalLineCategory.UTILITIES,
    "Water": HistoricalLineCategory.UTILITIES,

    # ---- Non-controllable: RE Taxes
    "Real Estate Tax Expense": HistoricalLineCategory.REAL_ESTATE_TAXES,
    "Real Estate Tax Expense-Prior Year": HistoricalLineCategory.REAL_ESTATE_TAXES,
    "Real Estate Tax Protest Fees-CY": HistoricalLineCategory.REAL_ESTATE_TAXES,
    "Real Estate Taxes Paid": HistoricalLineCategory.REAL_ESTATE_TAXES,
    "Real Estate Taxes Paid (Contra)": HistoricalLineCategory.REAL_ESTATE_TAXES,

    # ---- Insurance
    "Liability Insurance": HistoricalLineCategory.INSURANCE,
    "Multi-Peril Insurance": HistoricalLineCategory.INSURANCE,
    "Other Insurance": HistoricalLineCategory.INSURANCE,
    "Insurance Deductible": HistoricalLineCategory.INSURANCE,

    # ---- Landlord / Asset
    "Asset Management Fee": HistoricalLineCategory.ASSET_MGMT_FEE,
    "LL Tenant Relations": HistoricalLineCategory.NON_OPERATING_OTHER,
    "LL Miscellaneous": HistoricalLineCategory.NON_OPERATING_OTHER,

    # ---- Outside CAM
    # (these are MRI flow-through expenses on the CAM block — typically tiny)
    # Already-captured 'Cell Phone/Pager' as administrative

    # ---- Interest / Debt service / MIP / Bank Fees
    "Mortgage Interest": HistoricalLineCategory.DEBT_SERVICE_INTEREST,
    "Interest Expense-Non Mortgage": HistoricalLineCategory.NON_OPERATING_OTHER,
    "Interest Expense-KAFSG": HistoricalLineCategory.INTERCOMPANY_INTEREST,
    "Other Mortgage Expenses": HistoricalLineCategory.NON_OPERATING_OTHER,
    "MIP Expense": HistoricalLineCategory.MIP,
    "Bank Fees": HistoricalLineCategory.NON_OPERATING_OTHER,

    # ---- Depreciation / Amortization
    "Depreciation-Building": HistoricalLineCategory.DEPRECIATION,
    "Depreciation-FF&E": HistoricalLineCategory.DEPRECIATION,
    "Depreciation-Land Improvements": HistoricalLineCategory.DEPRECIATION,
    "Amortization-Mortgage Costs": HistoricalLineCategory.AMORTIZATION,

    # ---- Income Tax (rarely material at LLC level — passes through)
    "State Income Tax Expense": HistoricalLineCategory.NON_OPERATING_OTHER,

    # ---- Security Deposit Interest (tiny)
    "Security Deposit Interest": HistoricalLineCategory.NON_OPERATING_OTHER,

    # ---- Additional labels surfaced during loader testing
    "Application Fees": HistoricalLineCategory.MISC_TENANT_REVENUE,
    "Move Out Charges": HistoricalLineCategory.MISC_TENANT_REVENUE,
    "Miscellaneous Revenue": HistoricalLineCategory.OTHER_INCOME,
    "Miscellaneous": HistoricalLineCategory.NON_OPERATING_OTHER,
    "Antenna Rent": HistoricalLineCategory.OTHER_INCOME,
    "Club House Rental": HistoricalLineCategory.OTHER_INCOME,
    "Month-to-Month Fee": HistoricalLineCategory.MISC_TENANT_REVENUE,
    "Less Model Unit": HistoricalLineCategory.NON_REVENUE_UNITS,
    "Keys": HistoricalLineCategory.MISC_TENANT_REVENUE,
    "Rent-Up Charge": HistoricalLineCategory.MISC_TENANT_REVENUE,
    "Marketing": HistoricalLineCategory.ADVERTISING_MARKETING,
    "Internet": HistoricalLineCategory.ADMINISTRATIVE,
    "Signage": HistoricalLineCategory.ADVERTISING_MARKETING,
    "Fire/Alarm Supplies": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Cleaning Other": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Turnover - Carpet Repairs": HistoricalLineCategory.TURNOVER_COMMON_AREA,
    "Debt Service Fees": HistoricalLineCategory.NON_OPERATING_OTHER,
    "Cash Received Collection In": HistoricalLineCategory.NON_OPERATING_OTHER,
    "Change in Income Taxes Payable": HistoricalLineCategory.NON_OPERATING_OTHER,
    "Gas-Vacant Units": HistoricalLineCategory.UTILITIES,
    "Window and Door Repairs": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Outside Legal": HistoricalLineCategory.ADMINISTRATIVE,
    "Window Cleaning": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Fitness Ctr Equipment": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Landscape-Irrigation Repairs": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Security System": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Other Inc Adj-SD Interest": HistoricalLineCategory.NON_OPERATING_OTHER,
    "Vehicle Maintenance & Gas": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Unit Upgrades": HistoricalLineCategory.IMPROVEMENT_CAPEX,
    "Vehicle Expense": HistoricalLineCategory.REPAIRS_MAINTENANCE,
    "Deduct: Mortgage Principal Payment": HistoricalLineCategory.DEBT_SERVICE_PRINCIPAL,
}

# Additional subtotals that surfaced during loader testing
_EXTRA_SUBTOTALS = {
    "Total-Leasing Fees",
    "Total - Rental Revenue",
    "Total - Other Income-Commercial",
}
SUBTOTAL_LABELS_ADDITIONS = _EXTRA_SUBTOTALS


# Subtotal labels — these are roll-ups in the MRI source; we don't carry
# them as individual line items (the sum of underlying items reproduces them).
# However, we DO read them from the source for tie-out / sanity checks.
SUBTOTAL_LABELS: set[str] = {
    "REVENUE",
    "RENTAL REVENUE-MULTI-FAMILY",
    "REIMBURSABLE INCOME",
    "OTHER INCOME-MULTI-FAMILY",
    "OTHER INCOME-SENIOR HOUSING",
    "OTHER INCOME",
    "Total Rental Revenue-Multi-Family",
    "Total - Reimbursable Income",
    "Total - Other Income-Multi-Family",
    "Total - Other Income-Senior Housing",
    "Total - Other Income",
    "TOTAL INCOME",
    "EXPENSE",
    "OPERATING EXPENSES-MULTI-FAMILY",
    "Total - Operating Expenses-Multi-Family",
    "OUTSIDE CAM EXPENSES",
    "Total - Outside CAM Expenses",
    "REAL ESTATE TAX EXPENSES",
    "Total - Real Estate Tax Expenses",
    "INSURANCE EXPENSES",
    "Total - Insurance Expenses",
    "LANDLORD EXPENSES",
    "Total - Landlord Expenses",
    "GENERAL & ADMINISTRATIVE",
    "Total - General & Administrative",
    "TOTAL OPERATING EXPENSES",
    "NET OPERATING INCOME",
    "NET OPERATING INCOME AFTER LEASING FEES",
    "INTEREST EXPENSES",
    "Total - Interest Expense",
    "INCOME TAX EXPENSES",
    "Total - Income Tax Expenses",
    "Total - Depreciation & Amortization",
    "NET INCOME (Before Taxes)",
    "NET INCOME",
    "Funds From Operations (FFO)",
    "Total-Leasing Fees",
    "Total - Rental Revenue",
    "Total - Other Income-Commercial",
}

# Skip labels — cash flow statement reconciliation rows that aren't P&L items.
# We log them but don't create HistoricalLineItem records for them.
SKIP_LABELS: set[str] = {
    "Add: Depreciation & Amortization",
    "Add: Interest Expense",
    "Add: Accrued Real Estate Taxes",
    "Deduct: Paid Real Estate Taxes",
    "Change in Accounts Receivable",
    "Change in Prepaid Expenses",
    "Change in Accounts Payable",
    "Change in Capital",
    "Net Cash From Operating Activities",
    "Net Cash After Debt Service",
    "Net Cash Provided",
    "Net Cash Flow",
    "Deduct: Mortgage Principal Payments",
    "Deduct: Mortgage Refinancing/Other Activity",
    "Deduct: Interest Expense",
    "Deduct: Building Improvements",
    "Deduct: Other Capital Expenditures",
    "Deduct: Land Improvements",
    "Deduct: Development Projects in Process",
    "Accrual",
}


# Pattern rules — applied if no explicit mapping found.
# (regex pattern, category)
PATTERN_RULES: list[tuple[re.Pattern, HistoricalLineCategory]] = [
    (re.compile(r"\bpayroll\b", re.I), HistoricalLineCategory.PAYROLL),
    (re.compile(r"\bsalaries?\b", re.I), HistoricalLineCategory.PAYROLL),
    (re.compile(r"\bdepreciation\b", re.I), HistoricalLineCategory.DEPRECIATION),
    (re.compile(r"\bamortization\b", re.I), HistoricalLineCategory.AMORTIZATION),
    (re.compile(r"\binsurance\b", re.I), HistoricalLineCategory.INSURANCE),
    (re.compile(r"\bproperty tax\b|\bre tax\b|\breal estate tax\b", re.I), HistoricalLineCategory.REAL_ESTATE_TAXES),
    (re.compile(r"\butilit", re.I), HistoricalLineCategory.UTILITIES),
    (re.compile(r"\breimb", re.I), HistoricalLineCategory.REIMBURSEMENT_INCOME),
    (re.compile(r"\bparking\b", re.I), HistoricalLineCategory.PARKING),
    (re.compile(r"\bpet\b", re.I), HistoricalLineCategory.PET_FEES),
    (re.compile(r"\blate fee\b", re.I), HistoricalLineCategory.LATE_FEES),
    (re.compile(r"\bvacanc", re.I), HistoricalLineCategory.VACANCY),
    (re.compile(r"concession", re.I), HistoricalLineCategory.CONCESSIONS),
    (re.compile(r"\btif\b", re.I), HistoricalLineCategory.TIF_REVENUE),
    (re.compile(r"asset mgmt|asset management", re.I), HistoricalLineCategory.ASSET_MGMT_FEE),
    (re.compile(r"3rd party|management fee", re.I), HistoricalLineCategory.MANAGEMENT_FEES),
    (re.compile(r"mortgage interest", re.I), HistoricalLineCategory.DEBT_SERVICE_INTEREST),
    (re.compile(r"\bmip\b", re.I), HistoricalLineCategory.MIP),
    (re.compile(r"intercompany|kafsg", re.I), HistoricalLineCategory.INTERCOMPANY_INTEREST),
    (re.compile(r"capital improve|building improve|land improve", re.I), HistoricalLineCategory.IMPROVEMENT_CAPEX),
]


def classify_label(label: str) -> tuple[Optional[HistoricalLineCategory], str]:
    """Return (category, classification_method).

    method is one of:
      - 'explicit'  — direct match in LABEL_TO_CATEGORY
      - 'pattern'   — matched a PATTERN_RULES regex
      - 'subtotal'  — recognized as a roll-up (skip as line item)
      - 'skip'      — recognized as cash-flow / reconciliation row
      - 'unknown'   — no match; classified as OTHER for caller to review
    """
    label_clean = label.strip()
    if not label_clean:
        return None, "skip"

    if label_clean in SKIP_LABELS:
        return None, "skip"
    if label_clean in SUBTOTAL_LABELS:
        return None, "subtotal"
    if label_clean in LABEL_TO_CATEGORY:
        return LABEL_TO_CATEGORY[label_clean], "explicit"

    for pattern, cat in PATTERN_RULES:
        if pattern.search(label_clean):
            return cat, "pattern"

    return HistoricalLineCategory.OTHER, "unknown"
