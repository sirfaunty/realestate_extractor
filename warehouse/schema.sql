-- ═══════════════════════════════════════════════════════════════════
-- Capactive Analytical Warehouse — DuckDB Schema
-- Bitemporal Zone A/B/C architecture
-- ═══════════════════════════════════════════════════════════════════

-- ─── ZONE A: Ingestion Provenance ──────────────────────────────────
-- Every data load is registered here. Every fact row in Zone B
-- links back via ingestion_id so we can trace any number to its source.

CREATE TABLE IF NOT EXISTS raw_ingestion_log (
    ingestion_id    INTEGER PRIMARY KEY,
    source          VARCHAR NOT NULL,      -- e.g. 'costar_inventory', 'costar_sales_comps'
    source_vintage  VARCHAR,               -- e.g. 'Aug 2024', 'Q1 2026'
    knowledge_date  DATE NOT NULL,         -- earliest date a decision-maker could act on this
    ingested_at     TIMESTAMP DEFAULT current_timestamp,
    file_hash       VARCHAR,               -- SHA-256 of source file for integrity
    file_path       VARCHAR,               -- original file path
    record_count    INTEGER,               -- rows ingested
    notes           VARCHAR
);

CREATE SEQUENCE IF NOT EXISTS seq_ingestion_id START 1;

-- ─── ZONE B: Dimension Tables ──────────────────────────────────────
-- Type-2 slowly-changing dimensions with valid_from/valid_to.

CREATE TABLE IF NOT EXISTS dim_property (
    property_key    INTEGER PRIMARY KEY,
    property_id     VARCHAR NOT NULL,      -- CoStar PropertyID (canonical)
    capactive_id    INTEGER,               -- FK to SQLite properties.id (nullable)
    address         VARCHAR,
    city            VARCHAR,
    state           VARCHAR,
    zip             VARCHAR,
    market          VARCHAR,               -- CoStar Market Name
    submarket       VARCHAR,               -- CoStar Submarket Name
    submarket_cluster VARCHAR,             -- CoStar Submarket Cluster
    lat             DOUBLE,
    lon             DOUBLE,
    year_built      INTEGER,
    num_units       INTEGER,
    building_class  VARCHAR,               -- A/B/C or Star Rating
    style           VARCHAR,
    property_name   VARCHAR,
    address_hash    VARCHAR,               -- MD5(normalized_address+city+state) for sales comp join
    valid_from      DATE DEFAULT '1900-01-01',
    valid_to        DATE DEFAULT '9999-12-31'
);

CREATE SEQUENCE IF NOT EXISTS seq_property_key START 1;

CREATE TABLE IF NOT EXISTS dim_market (
    market_key      INTEGER PRIMARY KEY,
    market_name     VARCHAR NOT NULL UNIQUE,
    cbsa_code       VARCHAR,
    state           VARCHAR,
    region          VARCHAR,
    valid_from      DATE DEFAULT '1900-01-01',
    valid_to        DATE DEFAULT '9999-12-31'
);

CREATE SEQUENCE IF NOT EXISTS seq_market_key START 1;

-- ─── ZONE B: Fact Tables ───────────────────────────────────────────

-- Property-level z-scores from the national inventory engine
CREATE TABLE IF NOT EXISTS fact_property_zscore (
    property_id     VARCHAR NOT NULL,
    universe        VARCHAR NOT NULL,       -- 'Market Rate Apartments', 'Senior', etc.
    peer_cut        VARCHAR NOT NULL,       -- e.g. 'Market x Size x Quality'
    view            VARCHAR NOT NULL,       -- 'standard', 'micro-market borrowed peers', etc.
    peer_group_key  VARCHAR NOT NULL,       -- e.g. 'Albuquerque | 2: 50-74 | 3 Star'
    metric          VARCHAR NOT NULL,
    value           DOUBLE,
    peer_mean       DOUBLE,
    peer_std        DOUBLE,
    peer_n          INTEGER,
    z_score         DOUBLE,
    knowledge_date  DATE NOT NULL,
    ingestion_id    INTEGER REFERENCES raw_ingestion_log(ingestion_id)
);

-- Peer group statistics (aggregate reference data)
CREATE TABLE IF NOT EXISTS fact_peer_group_stats (
    universe        VARCHAR NOT NULL,
    peer_cut        VARCHAR NOT NULL,
    view            VARCHAR NOT NULL,
    peer_group_key  VARCHAR NOT NULL,
    metric          VARCHAR NOT NULL,
    peer_n          INTEGER,
    peer_mean       DOUBLE,
    peer_std        DOUBLE,
    knowledge_date  DATE NOT NULL,
    ingestion_id    INTEGER REFERENCES raw_ingestion_log(ingestion_id)
);

-- Sales transactions from the comps pipeline
CREATE TABLE IF NOT EXISTS fact_sales_transaction (
    transaction_id  VARCHAR PRIMARY KEY,
    property_id     VARCHAR,               -- MD5 hash from sales comps pipeline
    asset_class     VARCHAR,
    sale_date       DATE,
    sale_year       INTEGER,
    sale_quarter    VARCHAR,               -- 'YYYY QN' format
    sale_price      DOUBLE,
    cap_rate_actual DOUBLE,
    cap_rate_proforma DOUBLE,
    price_per_unit  DOUBLE,
    price_per_sf    DOUBLE,
    num_units       INTEGER,
    year_built      INTEGER,
    building_class  VARCHAR,
    property_name   VARCHAR,
    property_address VARCHAR,
    city            VARCHAR,
    state           VARCHAR,
    market          VARCHAR,
    submarket       VARCHAR,
    buyer_name      VARCHAR,
    seller_name     VARCHAR,
    source_file     VARCHAR,
    source_sheet    VARCHAR,
    source_row      INTEGER,
    knowledge_date  DATE NOT NULL,
    ingestion_id    INTEGER REFERENCES raw_ingestion_log(ingestion_id)
);

-- Cap rate aggregates (market-level time series)
CREATE TABLE IF NOT EXISTS fact_cap_rate_aggregate (
    market          VARCHAR,               -- NULL for national
    asset_class     VARCHAR NOT NULL,
    period          VARCHAR NOT NULL,      -- sale_year or sale_quarter
    period_type     VARCHAR NOT NULL,      -- 'year' or 'quarter'
    granularity     VARCHAR NOT NULL,      -- 'national', 'market', 'submarket', etc.
    building_class  VARCHAR,               -- NULL for all-class aggregates
    n_deals         INTEGER,
    cap_rate_median DOUBLE,
    cap_rate_mean   DOUBLE,
    cap_rate_std    DOUBLE,
    cap_rate_p25    DOUBLE,
    cap_rate_p75    DOUBLE,
    is_clean        BOOLEAN DEFAULT true,  -- clean (filtered) vs all
    knowledge_date  DATE NOT NULL,
    ingestion_id    INTEGER REFERENCES raw_ingestion_log(ingestion_id)
);

-- Pricing aggregates ($/unit, $/SF by market/class/vintage)
CREATE TABLE IF NOT EXISTS fact_pricing_aggregate (
    market          VARCHAR,
    building_class  VARCHAR,
    vintage_bucket  VARCHAR,
    sale_year       INTEGER NOT NULL,
    granularity     VARCHAR NOT NULL,
    n_deals         INTEGER,
    total_volume    DOUBLE,
    median_price    DOUBLE,
    median_ppu      DOUBLE,
    p25_ppu         DOUBLE,
    p75_ppu         DOUBLE,
    mean_ppu        DOUBLE,
    median_ppsf     DOUBLE,
    knowledge_date  DATE NOT NULL,
    ingestion_id    INTEGER REFERENCES raw_ingestion_log(ingestion_id)
);

-- Ownership history (property-level ownership chain)
CREATE TABLE IF NOT EXISTS fact_ownership (
    property_id     VARCHAR NOT NULL,
    owner_canonical VARCHAR,
    acquisition_date DATE,
    disposition_date DATE,
    acquisition_price DOUBLE,
    disposition_price DOUBLE,
    hold_months     INTEGER,
    is_current      BOOLEAN,
    knowledge_date  DATE NOT NULL,
    ingestion_id    INTEGER REFERENCES raw_ingestion_log(ingestion_id)
);

-- ─── ZONE B: Deal Analytics Fact Tables ────────────────────────────
-- Deal-level projections from proforma, distribution, debt, and TIF engines.
-- All keyed by (deal_id, tif_scenario) with annual grain where applicable.

-- Partner dimension (investor/operator entities)
CREATE TABLE IF NOT EXISTS dim_partner (
    partner_key     INTEGER PRIMARY KEY,
    deal_id         VARCHAR NOT NULL,          -- e.g. 'chamberlain'
    partner_id      VARCHAR NOT NULL,          -- e.g. 'KA', 'IDP'
    partner_name    VARCHAR,
    role            VARCHAR,                   -- 'GP', 'LP', 'Operator'
    ownership_pct   DOUBLE,
    distribution_pct DOUBLE,
    pref_rate       DOUBLE,
    initial_equity  DOUBLE,
    valid_from      DATE DEFAULT '1900-01-01',
    valid_to        DATE DEFAULT '9999-12-31'
);

CREATE SEQUENCE IF NOT EXISTS seq_partner_key START 1;

-- Deal-level summary (one row per deal × tif_scenario)
CREATE TABLE IF NOT EXISTS fact_deal_summary (
    deal_id         VARCHAR NOT NULL,
    tif_scenario    VARCHAR NOT NULL,
    hold_years      INTEGER,
    initial_equity  DOUBLE,
    acquisition_cost_basis DOUBLE,
    levered_irr     DOUBLE,
    equity_multiple DOUBLE,
    avg_dscr        DOUBLE,
    exit_cap_rate   DOUBLE,
    gross_sale_price DOUBLE,
    net_sale_proceeds DOUBLE,
    loan_repayment_at_sale DOUBLE,
    total_distributed DOUBLE,
    deal_irr        DOUBLE,
    deal_em         DOUBLE,
    knowledge_date  DATE NOT NULL,
    ingestion_id    INTEGER REFERENCES raw_ingestion_log(ingestion_id),
    PRIMARY KEY (deal_id, tif_scenario, knowledge_date)
);

-- Annual proforma projections per TIF scenario
CREATE TABLE IF NOT EXISTS fact_proforma_annual (
    deal_id         VARCHAR NOT NULL,
    tif_scenario    VARCHAR NOT NULL,
    year            INTEGER NOT NULL,          -- hold year (1-based)
    calendar_year   INTEGER,
    noi             DOUBLE,
    debt_service    DOUBLE,
    capex           DOUBLE,
    non_operating   DOUBLE,
    levered_cf      DOUBLE,
    dscr            DOUBLE,
    knowledge_date  DATE NOT NULL,
    ingestion_id    INTEGER REFERENCES raw_ingestion_log(ingestion_id)
);

-- Annual distribution allocations per partner per TIF scenario
CREATE TABLE IF NOT EXISTS fact_distribution_annual (
    deal_id         VARCHAR NOT NULL,
    tif_scenario    VARCHAR NOT NULL,
    year            INTEGER NOT NULL,
    calendar_year   INTEGER,
    partner_id      VARCHAR NOT NULL,
    distribution    DOUBLE,
    pref_accrued    DOUBLE,
    pref_paid       DOUBLE,
    cash_on_cash    DOUBLE,
    knowledge_date  DATE NOT NULL,
    ingestion_id    INTEGER REFERENCES raw_ingestion_log(ingestion_id)
);

-- Annual debt metrics (amortization, DSCR, LTV, MIP)
CREATE TABLE IF NOT EXISTS fact_debt_annual (
    deal_id         VARCHAR NOT NULL,
    tif_scenario    VARCHAR NOT NULL,
    year            INTEGER NOT NULL,
    calendar_year   INTEGER,
    beginning_balance DOUBLE,
    ending_balance  DOUBLE,
    total_payment   DOUBLE,
    total_principal DOUBLE,
    total_interest  DOUBLE,
    noi             DOUBLE,
    debt_service    DOUBLE,
    dscr            DOUBLE,
    dscr_with_mip   DOUBLE,
    mip_amount      DOUBLE,
    ltv             DOUBLE,
    estimated_value DOUBLE,
    knowledge_date  DATE NOT NULL,
    ingestion_id    INTEGER REFERENCES raw_ingestion_log(ingestion_id)
);

-- Annual TIF projections per scenario
CREATE TABLE IF NOT EXISTS fact_tif_annual (
    deal_id         VARCHAR NOT NULL,
    tif_scenario    VARCHAR NOT NULL,
    year            INTEGER NOT NULL,
    tmv             DOUBLE,
    ntc             DOUBLE,
    captured_ntc    DOUBLE,
    tax_increment   DOUBLE,
    osa             DOUBLE,
    admin           DOUBLE,
    net_tif         DOUBLE,
    note_beg_bal    DOUBLE,
    note_interest   DOUBLE,
    note_principal  DOUBLE,
    note_end_bal    DOUBLE,
    property_tax    DOUBLE,
    knowledge_date  DATE NOT NULL,
    ingestion_id    INTEGER REFERENCES raw_ingestion_log(ingestion_id)
);

-- TIF scenario comparison (one row per scenario vs baseline)
CREATE TABLE IF NOT EXISTS fact_tif_comparison (
    deal_id         VARCHAR NOT NULL,
    tif_scenario    VARCHAR NOT NULL,
    payoff_year     INTEGER,
    total_net_tif   DOUBLE,
    npv_net_tif     DOUBLE,
    npv_property_tax DOUBLE,
    nominal_tax_savings DOUBLE,
    nominal_tif_reduction DOUBLE,
    nominal_net_benefit DOUBLE,
    npv_tax_savings DOUBLE,
    npv_tif_reduction DOUBLE,
    npv_net_benefit DOUBLE,
    attorney_fees   DOUBLE,
    knowledge_date  DATE NOT NULL,
    ingestion_id    INTEGER REFERENCES raw_ingestion_log(ingestion_id)
);


-- ─── ZONE C: Convenience Views ─────────────────────────────────────
-- These provide backward-compatible read surfaces.

-- as_of(T) pattern: for any fact table, get the latest knowledge_date <= T
-- Implemented as a macro in the warehouse engine, not as a SQL view.

-- Property master: latest snapshot joined with dim_property
CREATE OR REPLACE VIEW v_property_master AS
SELECT
    dp.*,
    dp.property_id AS costar_id
FROM dim_property dp
WHERE dp.valid_to = '9999-12-31';

-- Current cap rates by market (latest knowledge_date, clean set)
CREATE OR REPLACE VIEW v_current_cap_rates AS
SELECT * FROM fact_cap_rate_aggregate
WHERE is_clean = true
  AND period_type = 'year'
  AND knowledge_date = (SELECT MAX(knowledge_date) FROM fact_cap_rate_aggregate);

-- Deal analytics: latest proforma + debt + distribution joined by year
CREATE OR REPLACE VIEW v_deal_annual_summary AS
SELECT
    p.deal_id,
    p.tif_scenario,
    p.year,
    p.calendar_year,
    p.noi,
    p.debt_service,
    p.capex,
    p.levered_cf,
    p.dscr AS proforma_dscr,
    d.beginning_balance AS loan_balance,
    d.dscr AS debt_dscr,
    d.ltv,
    d.mip_amount,
    t.net_tif,
    t.property_tax,
    t.note_end_bal AS tif_note_balance
FROM fact_proforma_annual p
LEFT JOIN fact_debt_annual d
    ON p.deal_id = d.deal_id AND p.tif_scenario = d.tif_scenario AND p.year = d.year
    AND d.knowledge_date = (SELECT MAX(knowledge_date) FROM fact_debt_annual)
LEFT JOIN fact_tif_annual t
    ON p.deal_id = t.deal_id AND p.tif_scenario = t.tif_scenario AND p.year = t.year
    AND t.knowledge_date = (SELECT MAX(knowledge_date) FROM fact_tif_annual)
WHERE p.knowledge_date = (SELECT MAX(knowledge_date) FROM fact_proforma_annual);

-- Deal summary: latest snapshot per deal × scenario
CREATE OR REPLACE VIEW v_deal_summary_latest AS
SELECT * FROM fact_deal_summary
WHERE knowledge_date = (SELECT MAX(knowledge_date) FROM fact_deal_summary);
