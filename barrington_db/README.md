# Barrington Portfolio — Cash Flow Database

A modular, SQLite-backed Python database for the Barrington office portfolio.
It ingests the **April 2026** source financial documents (cash flows + rent
rolls), exposes a **per-property module** for each asset and a **portfolio
roll-up module**, and lays the groundwork for a **2026–2028 cash flow** driven
by lease-rollover assumptions.

This first build focuses on the **cash flow**: operating items (NOI) plus
**forecasted capital** — Tenant Improvements (TI), Leasing Commissions (LC),
and Building / Base-Building & Landlord (LL) work.

---

## Portfolio universe (10 properties)

| Code | Property | Market | Source CF | Source RR |
|------|----------|--------|-----------|-----------|
| DRAKE | Drake Oakbrook Plaza | Oak Brook | xlsx | ✓ |
| ONB | One Northbrook Place | Northbrook | pdf | ✓ |
| OHP1 | O'Hare Plaza I | O'Hare | pdf | ✓ |
| OHP2 | O'Hare Plaza II | O'Hare | pdf | ✓ |
| CCI | Corporate Center I | Northbrook | (corp consol) | ✓ |
| CCII | Corporate Center II | Northbrook | (corp consol) | ✓ |
| COMBINED | Combined Centre (own 3-bldg Northbrook complex) | Northbrook | pdf | ✓ |
| JTM | Milwaukee Portfolio – JTM | Milwaukee | pdf | ✓ |
| MB | MB MKE | Milwaukee | pdf | ✓ |
| WACKER | Wacker | Chicago CBD | pdf | ✓ |

> **Note:** Combined Centre is its own three-building Northbrook complex — it is
> *not* a roll-up of Corporate Center I & II. All ten are independent and are
> summed in the portfolio roll-up.

---

## Architecture

```
barrington_db/
  schema.sql              SQLite schema (star-ish: facts + dimensions)
  reference.py            Property universe, canonical line items, aliases,
                          per-property extraction config
  db.py                   Connection, schema init, dimension loading
  extract_cashflow.py     Drake xlsx extractor + generic text PDF extractor
  extract_pdf_coords.py   Coordinate-based PDF extractor (column detection,
                          fragment re-merge, multi-page geometry carry-forward)
  extract_rentroll.py     Tenant/lease-level rent roll parser
  load_cashflow.py        Orchestrates all property cash flow loads
  load_rentroll.py        Orchestrates all rent roll loads
  leasing.py              Lease-rollover analysis + forward leasing assumptions
  validate.py             Data-quality checks, NOI tie-out, quarantine
  property_module.py      PropertyModule: per-property accessors
  portfolio.py            Portfolio: roll-up across all properties
  report.py               Human-readable cash flow / capital / rollover reports
  build.py                End-to-end build entry point
```

### Data model (key tables)

- **`property`** — identity (code, name, Yardi id, market).
- **`line_item`** — canonical cash-flow taxonomy with `section`, `is_capital`,
  `is_subtotal`. Heterogeneous source labels map here via `LINE_ITEM_ALIASES`.
- **`period`** — monthly grain, 2026–2028.
- **`cash_flow_fact`** — one row per (property, period, line_item, scenario).
  `scenario` ∈ {ACTUAL (Jan–Apr 26), REFORECAST (May–Dec 26), FORECAST (27–28)}.
- **`rent_roll`** — tenant/lease level, snapshot as-of 2026-04-30.
- **`leasing_assumption`** — one editable row per expiring lease; sets
  renewal / vacate / re-lease outcome + TI/LC/downtime that feed the forecast.

---

## Quick start

```bash
# Build the database from the source folders
python -m barrington_db.build "April 2026 Cash Flows" "April 2026 Rent Rolls"
```

```python
from barrington_db.db import connect
from barrington_db.portfolio import Portfolio
from barrington_db.property_module import PropertyModule
from barrington_db.report import (property_cashflow_statement,
                                  portfolio_capital_summary, rollover_report)

conn = connect()

# Portfolio roll-up
pf = Portfolio(conn)
pf.total_noi(2026)                 # 25,518,067
pf.capital_by_property(2026)       # capital matrix (TI/LC/Base-Bldg per asset)
pf.rollover_by_year()              # expiring SF/rent by year through 2028

# A single property module
onb = PropertyModule(conn, "ONB")
onb.monthly(2026)                  # monthly cash flow by line item
onb.capital_detail(2026)           # TI / LC / Base-Bldg detail
onb.expirations()                  # leases rolling through 2028

# Reports
print(property_cashflow_statement(conn, "ONB"))
print(portfolio_capital_summary(conn))
print(rollover_report(conn))
```

---

## Data quality / tie-out

**NOI ties to source for every property** (Drake, ONB, OHP I/II, Combined, JTM,
MB, Wacker). Forecasted capital (Base-Bldg/LL, TI, LC) ties to source for
**Drake, ONB, Combined, JTM, MB**.

Known gaps (flagged, not silently wrong):

1. **OHP I & II capital** — the source cash flows only present a *combined*
   "Leasing Related Capital Items" subtotal at the page level (the Building/TI/
   LC split lives in tenant-specific detail rows). Captured as `CAP_SUBTOTAL`;
   the TI/LC/Base-Bldg split is pending a detail-schedule parse.
2. **Wacker TI & LC** — Wacker's model is a 13-column layout with separate
   *Committed* and *Estimated* capital sections across pages. Building ties;
   TI and LC currently double-count across sections and need a Wacker-specific
   reconciliation. (Building/NOI are correct.)
3. **JTM & Wacker debt service** — multi-section layouts cause the interest/
   principal rows to mis-parse; these are auto-quarantined by `validate.py`
   (debt service is outside this build's operating+capital focus).
4. **Wacker rent roll** — different (non-Yardi) format; not yet parsed.

Run the tie-out any time:

```python
from barrington_db.validate import validate_noi, tie_out
validate_noi(conn)   # extracted vs expected NOI per property
tie_out(conn)        # NOI + capital per property
```

---

## Forward model (2027–2028) — next stage

`leasing.py` flags every lease expiring through 2028-12-31 and seeds an
editable `leasing_assumption` row per lease (seeded with market-default TI/LC/
downtime and a flat-renewal rent). Setting each `outcome`
(RENEW / VACATE / RELEASE) plus rents/downtime will drive forecast TI, LC, and
base-building spend into the `FORECAST` scenario for 2027–2028. The cash-flow
fact table and period dimension already span 2026–2028 to receive it.
