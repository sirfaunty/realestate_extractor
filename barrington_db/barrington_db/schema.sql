-- ============================================================================
-- Barrington Portfolio — Cash Flow Database Schema
-- ----------------------------------------------------------------------------
-- Design goals:
--   * One row-per-(property, period, line_item) fact table for cash flow, so
--     both per-property modules and the portfolio roll-up are simple GROUP BYs.
--   * A canonical line-item taxonomy so heterogeneous source layouts map to a
--     single, comparable structure (Operating + forecasted Capital: TI, LC,
--     Base Building / LL work).
--   * Rent roll stored at the tenant/lease level, with lease expirations
--     surfaced to drive the 2027-2028 forward leasing assumptions.
--   * Source-year (2026 = actuals + reforecast) separated from forecast years
--     (2027-2028 = model-driven) via the `scenario` dimension.
-- ============================================================================

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Dimension: properties
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS property (
    property_code   TEXT PRIMARY KEY,      -- e.g. 'DRAKE', 'ONB', 'WACKER'
    property_name   TEXT NOT NULL,         -- e.g. 'Drake Oakbrook Plaza'
    yardi_id        TEXT,                  -- e.g. 'o0493400'
    market          TEXT,                  -- e.g. 'Chicago', 'Milwaukee'
    submarket       TEXT,
    rentable_sf     REAL,                  -- total building rentable SF
    notes           TEXT
);

-- ---------------------------------------------------------------------------
-- Dimension: canonical cash-flow line items
-- section groups items into the cash flow waterfall.
--   OPERATING       -> revenue, expenses, NOI components
--   DEBT_SERVICE    -> interest, principal
--   RESERVE         -> tax/insurance reserve activity
--   CAPITAL         -> Building/Base-Building & LL work, TI, LC  <-- focus
--   NON_OPERATING   -> sec deposit, refunds, misc
--   FINANCING       -> ownership contributions / distributions
--   SUBTOTAL        -> computed waterfall subtotals (NOI, NCF lines)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS line_item (
    line_item_code  TEXT PRIMARY KEY,      -- canonical code, e.g. 'CAP_TI'
    label           TEXT NOT NULL,         -- display label
    section         TEXT NOT NULL,         -- waterfall section (see above)
    sort_order      INTEGER NOT NULL,      -- display ordering
    is_capital      INTEGER NOT NULL DEFAULT 0,
    is_subtotal     INTEGER NOT NULL DEFAULT 0,
    sign_convention TEXT DEFAULT 'natural' -- 'natural' = outflows negative
);

-- ---------------------------------------------------------------------------
-- Dimension: period (monthly grain, 2026-2028)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS period (
    period_id   INTEGER PRIMARY KEY,       -- yyyymm, e.g. 202604
    year        INTEGER NOT NULL,
    month       INTEGER NOT NULL,
    period_date TEXT NOT NULL,             -- 'YYYY-MM-01'
    UNIQUE (year, month)
);

-- ---------------------------------------------------------------------------
-- Fact: cash flow ($ per property / period / line item / scenario)
--   scenario:
--     'ACTUAL'    -> Jan-Apr 2026 actuals from source
--     'REFORECAST'-> May-Dec 2026 reforecast from source
--     'FORECAST'  -> 2027-2028 model-driven
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cash_flow_fact (
    property_code   TEXT NOT NULL REFERENCES property(property_code),
    period_id       INTEGER NOT NULL REFERENCES period(period_id),
    line_item_code  TEXT NOT NULL REFERENCES line_item(line_item_code),
    scenario        TEXT NOT NULL,
    amount          REAL NOT NULL DEFAULT 0,
    source_file     TEXT,                  -- provenance
    source_label    TEXT,                  -- original line label from source
    PRIMARY KEY (property_code, period_id, line_item_code, scenario)
);

CREATE INDEX IF NOT EXISTS ix_cff_prop   ON cash_flow_fact(property_code);
CREATE INDEX IF NOT EXISTS ix_cff_period ON cash_flow_fact(period_id);
CREATE INDEX IF NOT EXISTS ix_cff_item   ON cash_flow_fact(line_item_code);

-- ---------------------------------------------------------------------------
-- Fact: rent roll (tenant / lease level, snapshot as-of 2026-04-30)
-- Drives forward leasing: lease_to flags rollover within the horizon.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rent_roll (
    rent_roll_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    property_code   TEXT NOT NULL REFERENCES property(property_code),
    as_of_date      TEXT NOT NULL,
    units           TEXT,                  -- unit(s) string
    tenant_name     TEXT,
    lease_type      TEXT,
    area_sf         REAL,
    lease_from      TEXT,                  -- ISO date or NULL
    lease_to        TEXT,                  -- ISO date or NULL (month-to-month)
    term_months     REAL,
    monthly_rent    REAL,
    annual_rent     REAL,
    annual_rent_psf REAL,
    annual_rec_psf  REAL,
    security_deposit REAL,
    is_vacant       INTEGER DEFAULT 0,
    source_file     TEXT
);

CREATE INDEX IF NOT EXISTS ix_rr_prop ON rent_roll(property_code);
CREATE INDEX IF NOT EXISTS ix_rr_to   ON rent_roll(lease_to);

-- ---------------------------------------------------------------------------
-- Leasing assumptions (forward 2027-2028) — one row per expiring lease,
-- editable to set renewal / vacate / re-lease outcomes that feed the model.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS leasing_assumption (
    leasing_assumption_id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_code   TEXT NOT NULL REFERENCES property(property_code),
    rent_roll_id    INTEGER REFERENCES rent_roll(rent_roll_id),
    units           TEXT,
    tenant_name     TEXT,
    area_sf         REAL,
    expiry_date     TEXT,
    outcome         TEXT DEFAULT 'TBD',    -- 'RENEW' | 'VACATE' | 'RELEASE' | 'TBD'
    downtime_months REAL DEFAULT 0,        -- vacancy gap before re-lease
    new_rent_psf    REAL,                  -- assumed renewal/new face rent
    ti_psf          REAL,                  -- assumed TI allowance $/SF
    lc_psf          REAL,                  -- assumed leasing commission $/SF
    free_rent_months REAL DEFAULT 0,
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS ix_la_prop ON leasing_assumption(property_code);
