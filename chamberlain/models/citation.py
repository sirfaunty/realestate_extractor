"""Core entity models for source-document-anchored facts.

These are the platform foundation. Every input number, contract term, or
extracted fact in the model carries a Citation that points back to its
Source Document — page, cell, section — with verbatim text. This is the
non-negotiable architecture from DATA_MODEL_AND_ARCHITECTURE.md §3.

The four entities here:

  - SourceDocument: the original file an extracted fact came from
  - Locator: where in the file (page, cell, section)
  - Citation: links a fact to its source with verbatim text
  - AuthorityTier: drives conflict resolution between sources

Everything downstream — assumptions, equity ledger entries, historical
line items — references these. The Cited[T] wrapper composes any value
with a citation so the data flow is uniformly traceable.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Generic, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator


# --------------------------------------------------------------------------
# Authority Tier
# --------------------------------------------------------------------------


class AuthorityTier(str, Enum):
    """Source authority tiers driving conflict resolution.

    From DATA_MODEL_AND_ARCHITECTURE.md §4: when two sources conflict,
    the higher tier wins, but the conflict is surfaced rather than
    silently resolved.

    Examples:
      - PRIMARY: signed closing statements, executed agreements,
        recorded instruments, bank statements, official rent rolls
      - SECONDARY: accountant working files, GL exports, draft documents,
        internal models, third-party valuations
      - TERTIARY: informal notes, summary emails, derived analyses
    """

    PRIMARY = "primary"
    SECONDARY = "secondary"
    TERTIARY = "tertiary"

    @property
    def rank(self) -> int:
        """Higher = more authoritative. Used for conflict resolution."""
        return {"primary": 3, "secondary": 2, "tertiary": 1}[self.value]


class DocumentType(str, Enum):
    """Enumerated document types we ingest.

    Not exhaustive — adding new types is a normal change. Drives
    extraction strategy and lens routing.
    """

    # Legal / governing
    LLC_AGREEMENT = "llc_agreement"
    LLC_AMENDMENT = "llc_amendment"
    LOAN_AGREEMENT = "loan_agreement"
    LOAN_NOTE = "loan_note"
    MANAGEMENT_AGREEMENT = "management_agreement"
    REGULATORY_AGREEMENT = "regulatory_agreement"
    MINIMUM_ASSESSMENT_AGREEMENT = "minimum_assessment_agreement"
    TIF_PLAN = "tif_plan"
    TIF_NOTE = "tif_note"

    # Financial / operating
    CLOSING_STATEMENT = "closing_statement"
    MRI_INCOME_STATEMENT = "mri_income_statement"
    GL_EXPORT = "gl_export"
    BANK_STATEMENT = "bank_statement"
    BUDGET_FILE = "budget_file"
    REFORECAST = "reforecast"
    RENT_ROLL = "rent_roll"

    # Equity / partnership
    EQUITY_LEDGER = "equity_ledger"
    CAPITAL_ACCOUNT = "capital_account"
    WATERFALL_CALC = "waterfall_calc"
    PROJECT_LOAN_CALC = "project_loan_calc"

    # Working product / accountant
    ACCOUNTANT_WORKPAPER = "accountant_workpaper"
    INTERNAL_MODEL = "internal_model"
    PROPERTY_OVERVIEW = "property_overview"
    VALUATION_PROFORMA = "valuation_proforma"
    APPRAISAL = "appraisal"

    # Derived / analytical
    VALIDATED_DATASET = "validated_dataset"
    RECONCILED_ANALYSIS = "reconciled_analysis"
    EHLERS_TIF_UPDATE = "ehlers_tif_update"

    # Tax
    TAX_STATEMENT = "tax_statement"

    OTHER = "other"


# --------------------------------------------------------------------------
# Locator — where in a document a fact lives
# --------------------------------------------------------------------------


class Locator(BaseModel):
    """Where in a Source Document an extracted fact lives.

    At least one locator field must be specified. The most precise
    locator type for the document format should be used:

      - PDFs: page (and optional section/paragraph)
      - Excel: sheet + cell (e.g. "ASSUMPTIONS!N194")
      - Markdown / structured: section + paragraph
      - Anything: a verbatim snippet always grounds the citation

    Examples:
      Locator(page=24, section="§5.2(a)")
      Locator(sheet="MRI_12MINCS", cell="N12")
      Locator(sheet="Annual Cash Flow Reforecast", row=37, column="J",
              col_label="2026 Proforma")
    """

    model_config = ConfigDict(extra="forbid")

    # PDF-style
    page: Optional[int] = Field(default=None, ge=1, description="1-indexed page")
    section: Optional[str] = Field(default=None, description="e.g. '§5.2(a)' or 'Article III'")
    paragraph: Optional[str] = Field(default=None, description="paragraph identifier within section")

    # Spreadsheet-style
    sheet: Optional[str] = Field(default=None, description="worksheet name")
    cell: Optional[str] = Field(default=None, description="e.g. 'N194'")
    row: Optional[int] = Field(default=None, ge=1, description="1-indexed row")
    column: Optional[str] = Field(default=None, description="column letter or label")
    col_label: Optional[str] = Field(default=None, description="header label of the column, for human readability")
    row_label: Optional[str] = Field(default=None, description="label in column A of the row")

    # Universal fallbacks
    line: Optional[int] = Field(default=None, ge=1, description="line number (text files)")
    note: Optional[str] = Field(default=None, description="free-form locator hint")

    @field_validator("cell")
    @classmethod
    def _validate_cell(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().upper().replace("$", "")
        return v

    def render(self) -> str:
        """Human-readable locator string for citation rendering."""
        parts: list[str] = []
        if self.sheet:
            if self.cell:
                parts.append(f"{self.sheet}!{self.cell}")
            elif self.row is not None and self.column:
                parts.append(f"{self.sheet}!{self.column}{self.row}")
            elif self.row is not None:
                parts.append(f"{self.sheet}!R{self.row}")
            else:
                parts.append(f"sheet '{self.sheet}'")
        if self.page is not None:
            parts.append(f"p.{self.page}")
        if self.section:
            parts.append(self.section)
        if self.paragraph:
            parts.append(f"¶{self.paragraph}")
        if self.row_label and not self.sheet:
            parts.append(f'row "{self.row_label}"')
        if self.col_label and not self.cell:
            parts.append(f'col "{self.col_label}"')
        if self.line is not None:
            parts.append(f"line {self.line}")
        if not parts and self.note:
            parts.append(self.note)
        return ", ".join(parts) if parts else "(unspecified)"


# --------------------------------------------------------------------------
# SourceDocument — the original file an extracted fact descends from
# --------------------------------------------------------------------------


class SourceDocument(BaseModel):
    """A single original document in the corpus.

    Every SourceDocument has a stable id, a type, an authority tier,
    a file pointer (path), and provenance metadata. The original file
    must remain accessible — citations reference back to it.

    Once instantiated and used, a SourceDocument's id and contents
    should not change. Corrections to extracted facts route through
    new Citation records, not by mutating SourceDocuments.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(description="stable, human-readable id (e.g. 'mri_chamb_2023_actuals')")
    doc_type: DocumentType
    authority_tier: AuthorityTier

    title: str = Field(description="human-readable document title")
    description: Optional[str] = None

    # Dates
    document_date: Optional[date] = Field(
        default=None,
        description="date the document was created/dated (cover page date)",
    )
    effective_date: Optional[date] = Field(
        default=None,
        description="date the document's content is effective (e.g. as-of date for actuals)",
    )

    # File pointer
    file_path: Optional[Path] = Field(
        default=None,
        description="absolute path to the original file (never deleted)",
    )
    file_hash: Optional[str] = Field(
        default=None,
        description="SHA-256 of file contents for integrity verification",
    )

    # Scope
    asset_id: Optional[str] = Field(
        default="chamberlain",
        description="asset this document pertains to",
    )
    entity_id: Optional[str] = Field(
        default=None,
        description="legal entity (e.g. 'chamberlain_apartments_llc')",
    )

    # Provenance
    provider: Optional[str] = Field(default=None, description="who provided this document")
    received_date: Optional[date] = None

    # Extraction state
    extraction_notes: Optional[str] = None

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        v = v.strip().lower()
        if not v:
            raise ValueError("SourceDocument.id must be non-empty")
        if " " in v or "/" in v:
            raise ValueError("SourceDocument.id must be a slug (no spaces or slashes)")
        return v

    def compute_hash(self) -> Optional[str]:
        """Compute SHA-256 of the underlying file. Returns None if no file."""
        if not self.file_path or not self.file_path.exists():
            return None
        h = hashlib.sha256()
        with open(self.file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()


# --------------------------------------------------------------------------
# Citation — link from a Fact to its SourceDocument with verbatim text
# --------------------------------------------------------------------------


class Citation(BaseModel):
    """Links an extracted fact to its source.

    The citation is what makes the platform audit-grade. From
    DATA_MODEL_AND_ARCHITECTURE.md §3, every Extracted Fact carries
    a Citation with at minimum: source_document_id, locator,
    verbatim_text, extraction_date.

    A figure in a deliverable should be drillable down to:
        deliverable → fact → citation → source document → rendered page
    """

    model_config = ConfigDict(extra="forbid")

    source_document_id: str = Field(description="id of the SourceDocument cited")
    locator: Locator = Field(default_factory=Locator)
    verbatim_text: Optional[str] = Field(
        default=None,
        description="exact text the fact was extracted from; "
        "for numeric facts, the formatted value as shown in source",
    )
    extraction_date: datetime = Field(
        default_factory=datetime.utcnow,
        description="when the fact was extracted from the document",
    )
    extraction_method: str = Field(
        default="manual",
        description="manual | auto | expert_corrected",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="0-1 confidence in the extraction (1.0 = primary source manually verified)",
    )
    note: Optional[str] = Field(default=None, description="free-form context")

    def render(self, registry: Optional["SourceDocumentRegistry"] = None) -> str:
        """Render the citation as a human-readable string.

        If a registry is supplied, the document title is resolved.
        Otherwise the source_document_id is shown verbatim.
        """
        if registry is not None:
            doc = registry.get(self.source_document_id)
            title = doc.title if doc else self.source_document_id
        else:
            title = self.source_document_id
        loc = self.locator.render()
        if loc == "(unspecified)":
            return title
        return f"{title} ({loc})"


# --------------------------------------------------------------------------
# Cited[T] — wraps a value with its citation(s)
# --------------------------------------------------------------------------


T = TypeVar("T")


class Cited(BaseModel, Generic[T]):
    """A value paired with its citation(s).

    Use Cited to mark any input value that should carry source
    attribution. Most numeric inputs to the model are Cited values
    so that every figure in the output traces back to its source.

    The value is generic; pydantic preserves its type. Multiple
    citations are allowed when the same fact is corroborated across
    sources or compiled from multiple line items.

    Examples:
      total_units = Cited[int](
          value=316,
          citations=[Citation(
              source_document_id='property_overview_11_7_25',
              locator=Locator(sheet='Property Summary', cell='C13'),
              verbatim_text='316',
          )],
      )
    """

    model_config = ConfigDict(extra="forbid")

    value: T
    citations: list[Citation] = Field(default_factory=list, min_length=1)
    note: Optional[str] = Field(default=None, description="why this value, edge cases, etc.")

    @field_validator("citations")
    @classmethod
    def _at_least_one(cls, v: list[Citation]) -> list[Citation]:
        if not v:
            raise ValueError(
                "Cited values must have at least one Citation. "
                "If no source is known, use UncitedFact() with explicit reason."
            )
        return v

    @property
    def primary_citation(self) -> Citation:
        """The first citation (by convention, the primary source)."""
        return self.citations[0]


def cite(
    value: T,
    source_document_id: str,
    *,
    locator: Optional[Locator] = None,
    verbatim_text: Optional[str] = None,
    note: Optional[str] = None,
    confidence: float = 1.0,
    extraction_method: str = "manual",
) -> Cited[T]:
    """Convenience constructor: build a Cited[T] with a single citation.

    Example:
        units = cite(
            316, "property_overview_11_7_25",
            locator=Locator(sheet="Property Summary", cell="C13"),
            verbatim_text="316",
        )
    """
    citation = Citation(
        source_document_id=source_document_id,
        locator=locator or Locator(),
        verbatim_text=verbatim_text,
        confidence=confidence,
        extraction_method=extraction_method,
    )
    return Cited[T](value=value, citations=[citation], note=note)


# --------------------------------------------------------------------------
# Uncited / Derived — escape hatches with explicit rationale
# --------------------------------------------------------------------------


class UncitedReason(str, Enum):
    """Reasons a value might lack a direct source citation.

    Required when a Derived value can't be cited to a single source.
    """

    DERIVED_CALCULATION = "derived_calculation"
    USER_ASSUMPTION = "user_assumption"
    SCENARIO_OVERLAY = "scenario_overlay"
    STANDARD_INDUSTRY_PARAMETER = "standard_industry_parameter"
    PENDING_SOURCE = "pending_source"


class Derived(BaseModel, Generic[T]):
    """A value computed from other (cited) values.

    Carries the formula description and references to the upstream
    citations so drill-down still works. Used by the engine when
    producing computed outputs.
    """

    model_config = ConfigDict(extra="forbid")

    value: T
    formula: str = Field(description="human-readable derivation, e.g. 'GPR - vacancy - concessions'")
    upstream_citations: list[Citation] = Field(default_factory=list)
    note: Optional[str] = None


# --------------------------------------------------------------------------
# SourceDocumentRegistry — collection of all source documents
# --------------------------------------------------------------------------


class SourceDocumentRegistry(BaseModel):
    """In-memory registry of all SourceDocuments for an asset.

    Provides lookup by id, type, and authority tier; verifies that
    every Citation references an existing document.
    """

    model_config = ConfigDict(extra="forbid")

    documents: dict[str, SourceDocument] = Field(default_factory=dict)

    def add(self, doc: SourceDocument) -> None:
        if doc.id in self.documents:
            raise ValueError(f"SourceDocument id collision: {doc.id}")
        self.documents[doc.id] = doc

    def get(self, doc_id: str) -> Optional[SourceDocument]:
        return self.documents.get(doc_id)

    def require(self, doc_id: str) -> SourceDocument:
        doc = self.get(doc_id)
        if doc is None:
            raise KeyError(
                f"SourceDocument not found in registry: {doc_id!r}. "
                f"Add via registry.add(SourceDocument(...)) before referencing."
            )
        return doc

    def by_type(self, doc_type: DocumentType) -> list[SourceDocument]:
        return [d for d in self.documents.values() if d.doc_type == doc_type]

    def by_tier(self, tier: AuthorityTier) -> list[SourceDocument]:
        return [d for d in self.documents.values() if d.authority_tier == tier]

    def verify_citations(self, citations: list[Citation]) -> list[str]:
        """Return a list of unresolvable citation source_document_ids."""
        missing: list[str] = []
        for c in citations:
            if c.source_document_id not in self.documents:
                missing.append(c.source_document_id)
        return sorted(set(missing))

    def __len__(self) -> int:
        return len(self.documents)

    def __contains__(self, doc_id: object) -> bool:
        return isinstance(doc_id, str) and doc_id in self.documents


__all__ = [
    "AuthorityTier",
    "DocumentType",
    "Locator",
    "SourceDocument",
    "Citation",
    "Cited",
    "cite",
    "Derived",
    "UncitedReason",
    "SourceDocumentRegistry",
]
