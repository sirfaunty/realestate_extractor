"""Builds the SourceDocumentRegistry for Chamberlain.

Catalogs every source file in source_docs/ as a SourceDocument with
appropriate type, authority tier, and metadata. The result is a registry
that any Citation in the model can reference.

Authority tier assignments:
  - PRIMARY: LLC Agreement + Amendment 1, MAA, TIF Plan, TIF Note,
    executed loan agreements, signed closing statements
  - SECONDARY: MRI exports (internal accounting), Property Overview
    workbooks, Equity ledger workpapers, Property Manager 12-Month
    statements (CBL PDFs), JV-Equity Return Calcs, internal models
  - TERTIARY: validated JSON cross-references, derived analyses

The registry is the single source of truth for what documents exist;
every Citation in YAML configs and loaders references doc ids from here.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

from ..models.citation import (
    AuthorityTier,
    DocumentType,
    SourceDocument,
    SourceDocumentRegistry,
)


# Default location of the source-docs tree relative to project root
DEFAULT_SOURCE_DOCS_ROOT = Path(__file__).resolve().parents[3] / "source_docs"


def _path(root: Path, *parts: str) -> Optional[Path]:
    """Return the path if it exists, else None (file may not be staged)."""
    p = root.joinpath(*parts)
    return p if p.exists() else None


def build_registry(source_docs_root: Optional[Path] = None) -> SourceDocumentRegistry:
    """Construct and return the Chamberlain SourceDocumentRegistry.

    Args:
        source_docs_root: path to the source_docs/ directory; defaults to
            the one in the project root.

    Returns:
        SourceDocumentRegistry with all known Chamberlain source files
        registered.
    """
    root = source_docs_root or DEFAULT_SOURCE_DOCS_ROOT
    reg = SourceDocumentRegistry()

    # --------------------------------------------------------------------------
    # PRIMARY (executed legal docs, recorded instruments)
    # --------------------------------------------------------------------------

    reg.add(SourceDocument(
        id="llc_agreement_2017",
        doc_type=DocumentType.LLC_AGREEMENT,
        authority_tier=AuthorityTier.PRIMARY,
        title="LLC Agreement of Chamberlain Apartments LLC (executed)",
        description="Operating agreement of Chamberlain Apartments LLC executed July 5, 2017",
        document_date=date(2017, 7, 5),
        effective_date=date(2017, 7, 5),
        file_path=_path(root, "legal_tif",
                        "LLC Agrmt of Chamberlain Apts LLC 070517 (Executed)_62704128(1) (2)-c-c.pdf"),
        entity_id="chamberlain_apartments_llc",
        provider="Borrower's Organizational Documents Certification (11.24.20)",
    ))

    reg.add(SourceDocument(
        id="llc_amendment_1",
        doc_type=DocumentType.LLC_AMENDMENT,
        authority_tier=AuthorityTier.PRIMARY,
        title="Amendment No. 1 to LLC Agreement of Chamberlain Apartments LLC",
        description="First amendment to the LLC Agreement (fully executed)",
        file_path=_path(root, "legal_tif", "Amendment No. 1 to LLC Agrmt (fully executed).pdf"),
        entity_id="chamberlain_apartments_llc",
    ))

    reg.add(SourceDocument(
        id="borrower_org_chart_2018",
        doc_type=DocumentType.OTHER,
        authority_tier=AuthorityTier.PRIMARY,
        title="Borrower Organizational Chart — Original HUD Loan (7/1/2018)",
        document_date=date(2018, 7, 1),
        file_path=_path(root, "legal_tif", "Borrower Organizational Chart (Original HUD Loan - 7.1.18).pdf"),
        entity_id="chamberlain_apartments_llc",
    ))

    reg.add(SourceDocument(
        id="borrower_org_cert_2020",
        doc_type=DocumentType.OTHER,
        authority_tier=AuthorityTier.PRIMARY,
        title="Borrower's Organizational Documents Certification (11/24/2020)",
        document_date=date(2020, 11, 24),
        file_path=_path(root, "legal_tif",
                        "3.  Borrower's Organizational Documents Certification (11.24.20).pdf"),
        entity_id="chamberlain_apartments_llc",
    ))

    # --------------------------------------------------------------------------
    # SECONDARY: MRI Income Statements (internal accounting)
    # --------------------------------------------------------------------------

    for yr in range(2017, 2024):
        reg.add(SourceDocument(
            id=f"mri_chamb_{yr}_actuals",
            doc_type=DocumentType.MRI_INCOME_STATEMENT,
            authority_tier=AuthorityTier.SECONDARY,
            title=f"Chamberlain {yr} 12-Month Income Statement (MRI export)",
            description=f"KA internal MRI accounting actuals for fiscal year {yr}",
            document_date=date(yr, 12, 31),
            effective_date=date(yr, 12, 31),
            file_path=_path(root, "mri_actuals", f"Chamb {yr} Actuals.XLSX"),
            provider="KA Asset Management (MRI export, database KAREA)",
        ))

    # 2024 reforecast
    reg.add(SourceDocument(
        id="mri_chamb_2024_reforecast",
        doc_type=DocumentType.REFORECAST,
        authority_tier=AuthorityTier.SECONDARY,
        title="Chamberlain 2024 12-Month Income Statement Reforecast (MRI export)",
        document_date=date(2024, 12, 31),
        effective_date=date(2024, 12, 31),
        file_path=_path(root, "mri_actuals", "Chamb 2024 Reforecast.XLSX"),
        provider="KA Asset Management",
    ))

    # MRI-native full-year 2025 income statement (Kraus-Anderson KARE
    # database). Authoritative MRI cross-reference vs the CBL/Village Green
    # 2025 statement. NOTE: the MRI statement INCLUDES TIF receivable
    # (~$694K) in income; the CBL statement does NOT — this is the primary
    # reason the two FY2025 NOIs differ. TIF is handled by the model's
    # separate TIF sub-engine, so the CBL (ex-TIF) NOI is the cleaner
    # property-operations figure.
    reg.add(SourceDocument(
        id="mri_chamb_2025_actuals",
        doc_type=DocumentType.MRI_INCOME_STATEMENT,
        authority_tier=AuthorityTier.SECONDARY,
        title="Chamberlain 2025 12-Month Income Statement (MRI export)",
        description="MRI-native FY2025 actuals (KARE db); includes TIF "
                    "receivable in income — cross-reference for the CBL "
                    "2025 statement",
        document_date=date(2026, 5, 16),
        effective_date=date(2025, 12, 31),
        file_path=_path(root, "mri_actuals",
                        "Chamberlain MRI 2025 12 Month Income Statement.xlsx"),
        provider="KA Asset Management",
    ))

    # TTM Sep 2025 (9 months actual + 3 months reforecast)
    reg.add(SourceDocument(
        id="mri_chamb_ttm_sep_2025",
        doc_type=DocumentType.MRI_INCOME_STATEMENT,
        authority_tier=AuthorityTier.SECONDARY,
        title="Chamberlain TTM September 2025 (MRI export)",
        description="9 months actuals Jan-Sep 2025 + 3 months reforecast Oct-Dec 2025",
        document_date=date(2025, 10, 28),  # per source 'Date: 10/28/2025'
        effective_date=date(2025, 9, 30),
        file_path=_path(root, "mri_actuals", "Chamberlain TTM _September 2025.xlsx"),
        provider="KA Asset Management",
    ))

    # --------------------------------------------------------------------------
    # SECONDARY: CBL Property-Manager statements (12-month statements, PDF)
    # --------------------------------------------------------------------------

    for yr in [2023, 2024, 2025]:
        reg.add(SourceDocument(
            id=f"cbl_{yr}_statement",
            doc_type=DocumentType.MRI_INCOME_STATEMENT,
            authority_tier=AuthorityTier.SECONDARY,
            title=f"CBL {yr} 12-Month Statement (PDF)",
            description=f"Third-party property manager 12-month income statement for {yr}",
            document_date=date(yr, 12, 31),
            effective_date=date(yr, 12, 31),
            file_path=_path(root, "mri_actuals", f"CBL {yr} 12 Month Statement.pdf"),
            provider="VG (third-party property manager)",
        ))

    reg.add(SourceDocument(
        id="cbl_2026_budget",
        doc_type=DocumentType.BUDGET_FILE,
        authority_tier=AuthorityTier.SECONDARY,
        title="CBL 2026 12-Month Budget (PDF)",
        document_date=date(2025, 11, 7),
        effective_date=date(2026, 1, 1),
        file_path=_path(root, "mri_actuals", "CBL 2026 12 Month Budget.pdf"),
        provider="VG / KA Asset Management",
    ))

    reg.add(SourceDocument(
        id="cbl_ytd_budget_comparison",
        doc_type=DocumentType.REFORECAST,
        authority_tier=AuthorityTier.SECONDARY,
        title="CBL YTD Budget Comparison Cash Flow",
        file_path=_path(root, "mri_actuals", "CBL YTD Budget Comparison Cash Flow.pdf"),
        provider="VG (third-party property manager)",
    ))

    # Real trailing-twelve statement ending 3/31/2026 (Apr 2025 - Mar 2026).
    # This is the authoritative reforecast bridge between FY2025 actuals and
    # the forward proforma starting 4/1/2026. Sourced from Village Green's
    # March EOM financials (emailed 5/6/2026).
    reg.add(SourceDocument(
        id="cbl_ttm_mar_2026",
        doc_type=DocumentType.MRI_INCOME_STATEMENT,
        authority_tier=AuthorityTier.SECONDARY,
        title="CBL 12-Month Statement — Apr 2025 to Mar 2026 (TTM 3/31/26)",
        description="Trailing-twelve monthly income statement Apr 2025-Mar 2026; "
                    "the reforecast bridge period for the 4/1/2026 proforma start",
        document_date=date(2026, 3, 31),
        effective_date=date(2026, 3, 31),
        file_path=_path(root, "mri_actuals", "CBL 03.2026 12 Month Statement.xlsx"),
        provider="Village Green (third-party property manager)",
    ))

    # Note: cbl_2025_statement (full-year Jan-Dec 2025 actuals) is already
    # registered above in the CBL statement loop, pointing at
    # "CBL 2025 12 Month Statement.pdf". load_cy2025_actuals() reads it.

    # --------------------------------------------------------------------------
    # SECONDARY: Property Overview workbooks (authoritative for forward
    # proforma context; not contractual but represents KA's consolidated view)
    # --------------------------------------------------------------------------

    reg.add(SourceDocument(
        id="property_overview_11_7_25",
        doc_type=DocumentType.PROPERTY_OVERVIEW,
        authority_tier=AuthorityTier.SECONDARY,
        title="Chamberlain Property Overview Summary (11/7/2025)",
        description="16-tab consolidated property summary including Sources & Uses, "
                    "historical cash flows, rent roll, TIF overview, valuation, and "
                    "executed loan terms",
        document_date=date(2025, 11, 7),
        effective_date=date(2025, 11, 7),
        file_path=_path(root, "property_overview",
                        "Chamberlain Property Overview Summary_11.7.25.xlsx"),
        provider="KA Asset Management",
    ))

    reg.add(SourceDocument(
        id="budget_overview_11_7_25",
        doc_type=DocumentType.BUDGET_FILE,
        authority_tier=AuthorityTier.SECONDARY,
        title="Chamberlain 2026 Budget Overview (11/7/2025)",
        document_date=date(2025, 11, 7),
        effective_date=date(2026, 1, 1),
        file_path=_path(root, "property_overview", "Chamberlain 2026 Budget Overview_11.7.25.xlsx"),
        provider="KA Asset Management",
    ))

    reg.add(SourceDocument(
        id="cash_flow_reforecast_11_7_25",
        doc_type=DocumentType.REFORECAST,
        authority_tier=AuthorityTier.SECONDARY,
        title="Chamberlain Cash Flow Reforecast (11/7/2025)",
        document_date=date(2025, 11, 7),
        file_path=_path(root, "property_overview", "Chamberlain - Cash Flow Reforecast_11.7.25.xlsx"),
        provider="KA Asset Management",
    ))

    reg.add(SourceDocument(
        id="rent_roll_4_28_26",
        doc_type=DocumentType.RENT_ROLL,
        authority_tier=AuthorityTier.PRIMARY,
        title="CBL Current Rent Roll (4/28/2026)",
        description="Unit-level rent roll snapshot as of 4/28/2026 (most recent)",
        document_date=date(2026, 4, 28),
        effective_date=date(2026, 4, 28),
        file_path=_path(root, "property_overview", "CBL Current Rent Roll.xlsx"),
        provider="VG (third-party property manager)",
    ))

    # Valuation proformas (existing Excel models, the ones we're replacing)
    reg.add(SourceDocument(
        id="valuation_proforma_with_improvements_11_16_25",
        doc_type=DocumentType.VALUATION_PROFORMA,
        authority_tier=AuthorityTier.SECONDARY,
        title="Chamberlain Valuation Proforma — Including Improvements (11/16/2025)",
        description="Forward 10-year proforma scenario with unit upgrade capex plan",
        document_date=date(2025, 11, 16),
        file_path=_path(root, "property_overview",
                        "The_Chamberlain__-_Valuation_Proforma_11_16_25__Inc__Improvements_.xlsx"),
        provider="KA Asset Management",
    ))

    reg.add(SourceDocument(
        id="valuation_proforma_no_improvements_11_16_25",
        doc_type=DocumentType.VALUATION_PROFORMA,
        authority_tier=AuthorityTier.SECONDARY,
        title="Chamberlain Valuation Proforma — No Improvements (11/16/2025)",
        description="Forward 10-year proforma scenario without renovation capex",
        document_date=date(2025, 11, 16),
        file_path=_path(root, "property_overview",
                        "The_Chamberlain__-_Valuation_Proforma_11_16_25__No_Improvments_.xlsx"),
        provider="KA Asset Management",
    ))

    # --------------------------------------------------------------------------
    # SECONDARY: Equity / Partnership backup workbooks
    # --------------------------------------------------------------------------

    reg.add(SourceDocument(
        id="equity_account_details",
        doc_type=DocumentType.EQUITY_LEDGER,
        authority_tier=AuthorityTier.SECONDARY,
        title="Chamberlain Equity Account Details (MRI capital accounts)",
        description="MRI export of KA + IDP capital account transactions",
        file_path=_path(root, "equity_backup", "Copy of Chamberlain Equity Account Details.xlsx"),
        provider="KA Asset Management",
        entity_id="chamberlain_apartments_llc",
    ))

    reg.add(SourceDocument(
        id="closing_proceeds_10_26_21",
        doc_type=DocumentType.CLOSING_STATEMENT,
        authority_tier=AuthorityTier.PRIMARY,
        title="Chamberlain Closing Proceeds Summary (10/26/2021)",
        description="Refi closing proceeds summary, HUD 223(f) refinance",
        document_date=date(2021, 10, 26),
        effective_date=date(2021, 10, 26),
        file_path=_path(root, "equity_backup", "Copy of Chamberlain Closing Proceeds Summary 10-26-21.xlsx"),
    ))

    reg.add(SourceDocument(
        id="jv_equity_return_calc_8_1_22",
        doc_type=DocumentType.WATERFALL_CALC,
        authority_tier=AuthorityTier.SECONDARY,
        title="Chamberlain JV-Equity Return Calc (8/1/2022)",
        description="Historical waterfall application of contributions and distributions through 8/1/2022",
        document_date=date(2022, 8, 1),
        file_path=_path(root, "equity_backup", "Copy of Chamberlain JV-Equity Return Calc_8-1-22.xlsx"),
        provider="KA Asset Management",
    ))

    reg.add(SourceDocument(
        id="jv_equity_return_calc_2_14_23",
        doc_type=DocumentType.WATERFALL_CALC,
        authority_tier=AuthorityTier.SECONDARY,
        title="Chamberlain JV-Equity Return Calc (2/14/2023, MJ_IDP version)",
        description="Updated waterfall calc through 2/14/2023, shared with IDP",
        document_date=date(2023, 2, 14),
        file_path=_path(root, "equity_backup",
                        "Copy of Chamberlain JV-Equity Return Calc_2-14-23_MJ_IDP.xlsx"),
        provider="KA Asset Management → IDP",
    ))

    reg.add(SourceDocument(
        id="project_loan_interest_calc_4_22",
        doc_type=DocumentType.PROJECT_LOAN_CALC,
        authority_tier=AuthorityTier.SECONDARY,
        title="Chamberlain Project Loan Interest Calc (4/2022)",
        description="Calculation of project loan interest accrued through April 2022, shared with IDP",
        document_date=date(2022, 4, 1),
        file_path=_path(root, "equity_backup",
                        "Copy of Chamberlain Project Loan Interest Calc_4-2022_to IDP.xlsx"),
        provider="KA Asset Management → IDP",
    ))

    reg.add(SourceDocument(
        id="leadership_rollup_feb_2023",
        doc_type=DocumentType.INTERNAL_MODEL,
        authority_tier=AuthorityTier.SECONDARY,
        title="KA Chamberlain Leadership Rollup — Feb 2023 with Forecast",
        document_date=date(2023, 2, 1),
        file_path=_path(root, "equity_backup",
                        "Copy of KA Chamberlain Leadership Rollup - Feb 2023 w Forecast.xlsx"),
        provider="KA Asset Management",
    ))

    # --------------------------------------------------------------------------
    # SECONDARY: Live TIF Model (the working analytical model we ported)
    # --------------------------------------------------------------------------

    reg.add(SourceDocument(
        id="tif_live_model",
        doc_type=DocumentType.INTERNAL_MODEL,
        authority_tier=AuthorityTier.SECONDARY,
        title="Chamberlain Live TIF Model (April 2026)",
        description="14-tab analytical TIF model with 4 scenarios reconciling to Ehlers within $86",
        document_date=date(2026, 4, 1),
        file_path=_path(root, "legal_tif", "Chamberlain_Live_TIF_Model.xlsx"),
        provider="KA Asset Management",
    ))

    # TIF Note mechanics & payoff schedule — the documentary basis for the
    # model's TIF-Note-first treatment (TIF receipts amortize the Note
    # before any cash reaches the LLC; projected payoff ~2039 baseline /
    # ~2043 appeal-adjusted; contractual maturity 2045/2046).
    reg.add(SourceDocument(
        id="tif_valuation_analysis",
        doc_type=DocumentType.INTERNAL_MODEL,
        authority_tier=AuthorityTier.SECONDARY,
        title="Chamberlain TIF Analysis - Valuation Sensitivity",
        description="TIF Note interest/principal schedule and payoff dates "
                    "across $54.86M/$52.15M/$50.0M assessed-value scenarios; "
                    "Total TIF Principal $7,142,377; payoff 8/1/2038-2/1/2044",
        document_date=date(2025, 11, 10),
        file_path=_path(root, "legal_tif",
                        "Chamberlain TIF & Valuation Analysis.pdf"),
        provider="KA Asset Management",
    ))
    reg.add(SourceDocument(
        id="tif_overview",
        doc_type=DocumentType.INTERNAL_MODEL,
        authority_tier=AuthorityTier.SECONDARY,
        title="Chamberlain TIF Overview",
        description="TIF Note payment stream & projected payoff: baseline "
                    "~2039, appeal-adjusted late-2043; ownership receives "
                    "material positive cash flow only in the late years "
                    "after baseline amortization completes (~2039)",
        document_date=date(2025, 11, 8),
        file_path=_path(root, "legal_tif", "Chamberlain TIF Overview.pdf"),
        provider="KA Asset Management",
    ))
    reg.add(SourceDocument(
        id="tif_valuation_overview",
        doc_type=DocumentType.INTERNAL_MODEL,
        authority_tier=AuthorityTier.SECONDARY,
        title="Chamberlain TIF / Tax / Assessed Value Overview",
        description="Historical TIF paid 2021-2025, assessed values, and "
                    "2026-2044 ownership-impact projection; Note fully "
                    "amortizes before Feb 2045/2046 maturity",
        document_date=date(2025, 11, 10),
        file_path=_path(root, "legal_tif",
                        "Chamberlain TIF & Valuation Overview.pdf"),
        provider="KA Asset Management",
    ))
    reg.add(SourceDocument(
        id="tif_update",
        doc_type=DocumentType.INTERNAL_MODEL,
        authority_tier=AuthorityTier.SECONDARY,
        title="Chamberlain TIF Update",
        description="Updated TIF Note status and appeal impact narrative",
        document_date=date(2025, 11, 8),
        file_path=_path(root, "legal_tif", "Chamberlain Tif Update.pdf"),
        provider="KA Asset Management",
    ))

    # --------------------------------------------------------------------------
    # TERTIARY: Validated cross-references (derived analyses, not primary sources)
    # --------------------------------------------------------------------------

    reg.add(SourceDocument(
        id="validated_property_noi",
        doc_type=DocumentType.VALIDATED_DATASET,
        authority_tier=AuthorityTier.TERTIARY,
        title="Validated Property NOI Series (Q2 2026 reconciliation)",
        description="Property NOI defined as NOI before AMF, CapEx, non-operating items; "
                    "reconciled across all 6 multifamily assets in Q2 2026 leadership work",
        document_date=date(2026, 5, 15),
        file_path=_path(root, "validated_xrefs", "property_noi_FINAL.json"),
        provider="KA Asset Management (Q2 2026 reconciliation)",
    ))

    reg.add(SourceDocument(
        id="validated_historical_pl",
        doc_type=DocumentType.VALIDATED_DATASET,
        authority_tier=AuthorityTier.TERTIARY,
        title="Validated Historical P&L Series (Q2 2026 reconciliation)",
        description="As-reported revenue, OpEx, NOI by year per validated reconciliation",
        document_date=date(2026, 5, 15),
        file_path=_path(root, "validated_xrefs", "historical_pl.json"),
        provider="KA Asset Management (Q2 2026 reconciliation)",
    ))

    reg.add(SourceDocument(
        id="validated_btl_items",
        doc_type=DocumentType.VALIDATED_DATASET,
        authority_tier=AuthorityTier.TERTIARY,
        title="Validated Below-The-Line Items (Q2 2026 reconciliation)",
        description="AMF, routine capex, improvement capex reclassed below the NOI line",
        document_date=date(2026, 5, 15),
        file_path=_path(root, "validated_xrefs", "property_btl_items.json"),
        provider="KA Asset Management (Q2 2026 reconciliation)",
    ))

    return reg


__all__ = ["build_registry", "DEFAULT_SOURCE_DOCS_ROOT"]
