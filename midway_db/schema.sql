-- Midway disposition-diligence warehouse schema (ported from partner midway_psa.db)

CREATE TABLE IF NOT EXISTS agreement (
    agreement_id     INTEGER PRIMARY KEY,
    label            TEXT,      -- internal shorthand
    source_file      TEXT,
    docusign_envelope TEXT,
    title            TEXT,
    effective_month  TEXT,
    effective_year   TEXT,
    governing_law    TEXT,
    page_count       INTEGER
);

CREATE TABLE IF NOT EXISTS broker (
    broker_id    INTEGER PRIMARY KEY,
    agreement_id INTEGER,
    side         TEXT,      -- Seller's / Buyer's
    name         TEXT,
    commission_pct REAL,
    FOREIGN KEY(agreement_id) REFERENCES agreement(agreement_id)
);

CREATE TABLE IF NOT EXISTS closing_document (
    doc_id        INTEGER PRIMARY KEY,
    agreement_id  INTEGER,
    party_delivering TEXT,
    ref           TEXT,
    name          TEXT,
    description   TEXT,
    FOREIGN KEY(agreement_id) REFERENCES agreement(agreement_id)
);

CREATE TABLE IF NOT EXISTS contingency (
    cont_id       INTEGER PRIMARY KEY,
    agreement_id  INTEGER,
    description   TEXT,
    section_ref   TEXT,
    FOREIGN KEY(agreement_id) REFERENCES agreement(agreement_id)
);

CREATE TABLE IF NOT EXISTS cost_allocation (
    cost_id       INTEGER PRIMARY KEY,
    agreement_id  INTEGER,
    item          TEXT,
    responsible   TEXT,
    section_ref   TEXT,
    FOREIGN KEY(agreement_id) REFERENCES agreement(agreement_id)
);

CREATE TABLE IF NOT EXISTS cross_parcel_provision (
    id INTEGER PRIMARY KEY,
    instrument TEXT,
    ref TEXT,
    topic TEXT,
    affects_1440_cub INTEGER,
    affects_1450_walmart_athome INTEGER,
    laf_consent_right INTEGER,   -- does LA Fitness (as Owner/Major Occupant) hold a consent/veto?
    summary TEXT
);

CREATE TABLE IF NOT EXISTS due_diligence_item (
    dd_id         INTEGER PRIMARY KEY,
    agreement_id  INTEGER,
    category      TEXT,
    item          TEXT,
    FOREIGN KEY(agreement_id) REFERENCES agreement(agreement_id)
);

CREATE TABLE IF NOT EXISTS financial_term (
    term_id      INTEGER PRIMARY KEY,
    agreement_id INTEGER,
    item         TEXT,
    value        TEXT,
    notes        TEXT,
    FOREIGN KEY(agreement_id) REFERENCES agreement(agreement_id)
);

CREATE TABLE IF NOT EXISTS key_date_deadline (
    deadline_id   INTEGER PRIMARY KEY,
    agreement_id  INTEGER,
    item          TEXT,
    trigger       TEXT,
    duration      TEXT,
    section_ref   TEXT,
    FOREIGN KEY(agreement_id) REFERENCES agreement(agreement_id)
);

CREATE TABLE IF NOT EXISTS lease_abstract (
    abstract_id      INTEGER PRIMARY KEY,
    tenant_id        INTEGER,
    field            TEXT,      -- e.g. 'commencement_date','expiration','base_rent','renewal_options','premises_sf','use','assignment','exclusive','co_tenancy'
    value            TEXT,
    source_file_id   INTEGER,
    source_page      TEXT,
    confidence       TEXT,      -- high | medium | low(OCR)
    FOREIGN KEY(tenant_id) REFERENCES lease_tenant(tenant_id)
);

CREATE TABLE IF NOT EXISTS lease_document_file (
    file_id          INTEGER PRIMARY KEY,
    tenant_id        INTEGER,
    rel_path         TEXT,
    doc_role         TEXT,      -- lease_bundle | estoppel | snda | checklist_ner | correspondence | sale_letter | signage | other
    size_bytes       INTEGER,
    pages            INTEGER,
    has_text_layer   INTEGER,   -- 1/0
    needs_ocr        INTEGER,   -- 1/0 (set after diagnostic)
    extracted        INTEGER DEFAULT 0,
    text_path        TEXT,      -- where extracted text is stored
    FOREIGN KEY(tenant_id) REFERENCES lease_tenant(tenant_id)
);

CREATE TABLE IF NOT EXISTS lease_tenant (
    tenant_id        INTEGER PRIMARY KEY,
    name             TEXT,
    folder           TEXT,
    category         TEXT,      -- retail | grocery | fitness | bank | telecom | media | parking | restaurant | easement
    agreement_type   TEXT,      -- lease | license | services_agreement | easement
    status           TEXT,      -- active | terminated (all here = active/in-scope)
    parcel_hint      TEXT,      -- 1440 / 1450 / shared / unknown  (to be refined after extraction)
    notes            TEXT
);

CREATE TABLE IF NOT EXISTS missing_document (
    id INTEGER PRIMARY KEY,
    item TEXT NOT NULL,            -- what's missing
    related_to TEXT,              -- tenant / REA / PSA / parcel
    why_needed TEXT,              -- diligence reason
    source_or_where TEXT,         -- where to obtain it
    priority TEXT,                -- high / medium / low
    status TEXT DEFAULT 'open'    -- open / requested / received
);

CREATE TABLE IF NOT EXISTS no_change_area_finding (
    id INTEGER PRIMARY KEY,
    location TEXT,
    parcel TEXT,
    psa TEXT,
    in_no_change_area TEXT,
    detail TEXT
);

CREATE TABLE IF NOT EXISTS notice_copy (
    copy_id      INTEGER PRIMARY KEY,
    agreement_id INTEGER,
    name         TEXT,
    firm         TEXT,
    email        TEXT,
    FOREIGN KEY(agreement_id) REFERENCES agreement(agreement_id)
);

CREATE TABLE IF NOT EXISTS party (
    party_id     INTEGER PRIMARY KEY,
    agreement_id INTEGER,
    role         TEXT,      -- Seller / Buyer / Escrow Agent
    name         TEXT,
    entity_type  TEXT,
    state_of_org TEXT,
    address      TEXT,
    attn         TEXT,
    email        TEXT,
    signatory    TEXT,
    signatory_title TEXT,
    FOREIGN KEY(agreement_id) REFERENCES agreement(agreement_id)
);

CREATE TABLE IF NOT EXISTS property (
    property_id     INTEGER PRIMARY KEY,
    agreement_id    INTEGER,
    common_address  TEXT,
    county          TEXT,
    state           TEXT,
    parcel_label    TEXT,
    legal_desc_status TEXT,     -- subdivided / not subdivided
    legal_description TEXT,
    abstract_or_torrens TEXT,
    building_age_note TEXT,
    hvac_note       TEXT,
    FOREIGN KEY(agreement_id) REFERENCES agreement(agreement_id)
);

CREATE TABLE IF NOT EXISTS rea_instrument (
    id INTEGER PRIMARY KEY,
    instrument TEXT,
    dated TEXT,
    recorded TEXT,
    parties TEXT,
    source_file TEXT,
    in_folder TEXT,          -- present in archive? path or 'NOT IN ARCHIVE'
    substantive_for_sale INTEGER,   -- 1 = changes cross-parcel covenants relevant to PSA
    summary TEXT
);

CREATE TABLE IF NOT EXISTS rea_prohibited_use (
    id INTEGER PRIMARY KEY,
    item_no INTEGER,
    prohibited_use TEXT,
    exceptions TEXT
);

CREATE TABLE IF NOT EXISTS rea_siteplan_fact (id INTEGER PRIMARY KEY, category TEXT, detail TEXT);

CREATE TABLE IF NOT EXISTS rent_roll (
    id INTEGER PRIMARY KEY,
    suite TEXT, occupant TEXT, category TEXT,
    rent_start TEXT, expiration TEXT,
    sqft INTEGER, monthly_base_rent REAL, rate_psf REAL,
    monthly_cost_recovery REAL, status TEXT, notes TEXT
);

CREATE TABLE IF NOT EXISTS representation (
    rep_id        INTEGER PRIMARY KEY,
    agreement_id  INTEGER,
    ref           TEXT,
    topic         TEXT,
    summary       TEXT,
    FOREIGN KEY(agreement_id) REFERENCES agreement(agreement_id)
);

CREATE TABLE IF NOT EXISTS section (
    section_id    INTEGER PRIMARY KEY,
    agreement_id  INTEGER,
    number        TEXT,
    heading       TEXT,
    page          INTEGER,
    summary       TEXT,
    FOREIGN KEY(agreement_id) REFERENCES agreement(agreement_id)
);

CREATE TABLE IF NOT EXISTS site_anchor_map (
    id INTEGER PRIMARY KEY, label TEXT, building TEXT, parcel_no TEXT, psa_link TEXT, notes TEXT
);
