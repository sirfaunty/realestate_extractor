"""
Southtown / DHOS — Co-Tenancy & Returns engine.

Reproduces the partner's "Co-Tenancy and Returns Model" from structured inputs:

  * Co-tenancy: occupancy = qualifying open Required-Tenant SF / §3.11(ix)-adjusted
    base LFA (281,637), tested against the 65% Initial/Ongoing Co-Tenancy threshold.
  * Returns: TPC (Brama's ten verbatim USES line items) + stabilized NOI -> two
    Yield-on-Cost views (all-in vs. cash basis ex-land) and an exit-cap value
    sensitivity.

Inputs below are transcribed from source (Southtown rent roll 5/31/26 + Brama's
native proforma). Local extraction of the rent roll / proforma is the next layer;
the calc engine and deliverable are the reusable pieces. `validate()` ties every
headline number back to the partner's model.
"""

# ── Co-tenancy inputs ───────────────────────────────────────────────────────
# Fallback tenant roster (used if the rent-roll PDF isn't staged locally).
# required = counts toward the co-tenancy numerator ("Required Tenant" per §1.7).
_FALLBACK_ROSTER = [
    {"suite": "0400.0a", "occupant": "Kohl’s — 1st floor", "sf": 47810, "exp": "2/1/2037", "required": True},
    {"suite": "0400.0b", "occupant": "Kohl’s — 2nd floor", "sf": 47809, "exp": "2/1/2037", "required": False},
    {"suite": "0621.0", "occupant": "T.J. Maxx", "sf": 26275, "exp": "3/31/2028", "required": True},
    {"suite": "0530.0", "occupant": "Slumberland", "sf": 48820, "exp": "5/31/2035", "required": True},
    {"suite": "0520.0", "occupant": "Southtown Bowl & Billiards", "sf": 45259, "exp": "5/31/2027", "required": False},
    {"suite": "0601.0", "occupant": "Guitar Center", "sf": 16018, "exp": "4/30/2028", "required": True},
    {"suite": "0620.0", "occupant": "Five Below", "sf": 10954, "exp": "1/31/2030", "required": True},
    {"suite": "0638.0", "occupant": "Schuler Shoes", "sf": 10677, "exp": "1/31/2029", "required": True},
    {"suite": "0632.0", "occupant": "My Salon Suites", "sf": 8400, "exp": "8/31/2030", "required": False},
    {"suite": "0630.0", "occupant": "Famous Footwear", "sf": 5478, "exp": "1/31/2034", "required": True},
    {"suite": "0207.0", "occupant": "Verizon", "sf": 4800, "exp": "5/31/2031", "required": True},
    {"suite": "0301.0", "occupant": "Applebee’s", "sf": 6426, "exp": "6/30/2034", "required": False},
    {"suite": "0100.0", "occupant": "McDonald’s", "sf": 4622, "exp": "8/14/2036", "required": False},
    {"suite": "0210.0", "occupant": "Panda Express", "sf": 2400, "exp": "11/30/2030", "required": False},
    {"suite": "0202.0", "occupant": "Palm Beach Tan", "sf": 2250, "exp": "12/31/2034", "required": False},
    {"suite": "0201.0", "occupant": "Bruegger’s Bagels", "sf": 2674, "exp": "3/31/2031", "required": False},
    {"suite": "639-641", "occupant": "Top Ten Liquors", "sf": 12000, "exp": "7/31/2036", "required": True},
    {"suite": "0203.0", "occupant": "Subway", "sf": 1600, "exp": "2/28/2036", "required": False},
    {"suite": "0204.0", "occupant": "UPS Store", "sf": 1600, "exp": "9/30/2027", "required": False},
    {"suite": "0208.0", "occupant": "Sport Clips", "sf": 1450, "exp": "4/30/2028", "required": False},
    {"suite": "0209.0", "occupant": "Salon Oriana", "sf": 1600, "exp": "10/31/2030", "required": False},
    {"suite": "0305.0", "occupant": "Southtown Tobacco", "sf": 1600, "exp": "8/31/2026", "required": False},
    {"suite": "0306.0", "occupant": "Wingstop", "sf": 1600, "exp": "2/29/2036", "required": False},
    {"suite": "0307.A", "occupant": "Great Clips", "sf": 1600, "exp": "1/31/2030", "required": False},
    {"suite": "0308.0", "occupant": "The Joint Chiropractor", "sf": 1600, "exp": "11/30/2032", "required": False},
    {"suite": "0309.0", "occupant": "Sally Beauty", "sf": 1600, "exp": "4/30/2035", "required": True},
    {"suite": "0637.0", "occupant": "Southtown Nails", "sf": 1420, "exp": "10/31/2026", "required": False},
    {"suite": "0611.0", "occupant": "H&R Block", "sf": 1013, "exp": "4/30/2027", "required": False},
    {"suite": "0612.0", "occupant": "Suite 0612.0", "sf": 1000, "exp": "4/30/2028", "required": False},
    {"suite": "0612.B", "occupant": "Suite 0612.B", "sf": 347, "exp": "4/30/2027", "required": False},
]

BASE_LFA = 281637          # §1.6/1.7 stipulated denominator (as of Effective Date)
LANES_SF = 45259           # Southtown Bowl & Billiards / Southtown Lanes non-retail carve-out
CO_TENANCY_THRESHOLD = 0.65

# Named scenarios: which Required tenants go vacant + denominator adjustments.
SCENARIOS = [
    {"name": "Base (today)", "vacate": [], "lanes_redeveloped": False, "new_retail_lfa": 0},
    {"name": "Kohl’s Vacant", "vacate": ["Kohl’s — 1st floor"], "lanes_redeveloped": False, "new_retail_lfa": 0},
    {"name": "Guitar Center Vacant", "vacate": ["Guitar Center"], "lanes_redeveloped": False, "new_retail_lfa": 0},
    {"name": "Kohl’s + GC Vacant", "vacate": ["Kohl’s — 1st floor", "Guitar Center"],
     "lanes_redeveloped": False, "new_retail_lfa": 0},
    {"name": "Multifamily + K/GC Vacant", "vacate": ["Kohl’s — 1st floor", "Guitar Center"],
     "lanes_redeveloped": True, "new_retail_lfa": 0},
]

# ── Returns inputs (Brama native proforma — verbatim USES) ──────────────────
# Fallback USES (used if Brama's proforma isn't staged locally).
_FALLBACK_USES = [
    ("Primary Construction Contract (KA hard scope)", 8620268, True),
    ("FFE & Other Hard Costs (DHOS vertical; incl. $19.8M TI)", 20754056, True),
    ("Land", 5761548, False),
    ("Government Fees (net of SAC credit)", 175000, True),
    ("Consultants, Design, Legal, Acctg", 1232868, True),
    ("Title & Closing", 152434, True),
    ("Lender & Appraisal", 407165, True),
    ("Capitalized Interest, Lease-Up Reserve (Carry)", 1850000, True),
    ("Lease Broker Commissions & Development Fee", 1740000, True),
    ("Owner’s Contingency / At-Risk Dev Fee", 656661, True),
]
STABILIZED_NOI = 2340000    # no TIF (stabilized-NOI estimate; documented assumption)
BUILDING_LFA = 120000
EXIT_CAP_BASE = 0.065
EXIT_CAP_RANGE = [0.0625, 0.065, 0.0675, 0.07]

# ── Calibration overlays (lease/judgment inputs, not in the source files) ────
KOHLS_SUITE = "0400.0"
KOHLS_1ST_LABEL = "Kohl’s — 1st floor"
KOHLS_2ND_LABEL = "Kohl’s — 2nd floor"
KOHLS_1ST_SF = 47810        # first-floor split of Kohl's 95,619 SF total
GC_LABEL = "Guitar Center"
# Suites classified as "Required Tenant" per lease §1.7 (excludes Kohl's, handled above).
REQUIRED_SUITES = {"0621.0", "0530.0", "0601.0", "0620.0", "0638.0",
                   "0630.0", "0207.0", "639-641", "0309.0"}


# ── Input loaders: prefer local extraction, fall back to transcribed ────────
def _build_roster():
    """Roster from the rent-roll PDF (+ Kohl's split & Required overlay), else fallback."""
    try:
        import extract_rent_roll
        if not extract_rent_roll.available():
            return _FALLBACK_ROSTER, "transcribed"
        roster = []
        for r in extract_rent_roll.extract():
            suite, sf, exp = r["suite"], r["sf"], r["exp"]
            if suite == KOHLS_SUITE:
                roster.append({"suite": suite + "a", "occupant": KOHLS_1ST_LABEL,
                               "sf": KOHLS_1ST_SF, "exp": exp, "required": True})
                roster.append({"suite": suite + "b", "occupant": KOHLS_2ND_LABEL,
                               "sf": sf - KOHLS_1ST_SF, "exp": exp, "required": False})
            else:
                occ = GC_LABEL if suite == "0601.0" else r["occupant"]
                roster.append({"suite": suite, "occupant": occ, "sf": sf, "exp": exp,
                               "required": suite in REQUIRED_SUITES})
        return roster, "extracted (rent roll 5/31/26)"
    except Exception:
        return _FALLBACK_ROSTER, "transcribed"


def _load_uses():
    """USES from Brama's proforma Sources & Uses, else fallback."""
    try:
        import extract_proforma
        if not extract_proforma.available():
            return _FALLBACK_USES, "transcribed"
        uses, _tpc = extract_proforma.extract_uses()
        return (uses, "extracted (Brama proforma)") if uses else (_FALLBACK_USES, "transcribed")
    except Exception:
        return _FALLBACK_USES, "transcribed"


TENANT_ROSTER, ROSTER_SOURCE = _build_roster()
TPC_USES, USES_SOURCE = _load_uses()
LAND_VALUE = sum(a for _l, a, cash in TPC_USES if not cash) or 5761548


# ── Co-tenancy engine ───────────────────────────────────────────────────────
def _required_open_sf(vacate):
    vac = set(vacate)
    return sum(t["sf"] for t in TENANT_ROSTER
               if t["required"] and t["occupant"] not in vac)


def qualifying_required_sf():
    """SF if ALL Required tenants are open (the numerator ceiling)."""
    return sum(t["sf"] for t in TENANT_ROSTER if t["required"])


def cotenancy_scenario(sc):
    numerator = _required_open_sf(sc["vacate"])
    denom = BASE_LFA - (LANES_SF if sc["lanes_redeveloped"] else 0) + sc.get("new_retail_lfa", 0)
    occ = numerator / denom
    cushion = numerator - CO_TENANCY_THRESHOLD * denom
    passing = occ >= CO_TENANCY_THRESHOLD
    return {
        "name": sc["name"], "numerator": numerator, "denominator": denom,
        "occupancy": occ, "cushion_sf": cushion, "pass": passing,
        "rent": "Full Minimum Rent" if passing
                else "SUBSTITUTE RENT — + 18-mo termination right",
    }


def cotenancy_table():
    return [cotenancy_scenario(sc) for sc in SCENARIOS]


# ── Returns engine ──────────────────────────────────────────────────────────
def total_project_cost():
    return sum(a for _l, a, _c in TPC_USES)


def cash_basis():
    """TPC excluding already-owned land — the true cash out the door."""
    return total_project_cost() - LAND_VALUE


def yield_on_cost():
    tpc, cash = total_project_cost(), cash_basis()
    return {
        "all_in": {"basis": tpc, "psf": tpc / BUILDING_LFA, "yoc": STABILIZED_NOI / tpc},
        "cash": {"basis": cash, "psf": cash / BUILDING_LFA, "yoc": STABILIZED_NOI / cash},
    }


def exit_cap_sensitivity():
    tpc, cash = total_project_cost(), cash_basis()
    rows = []
    for cap in EXIT_CAP_RANGE:
        value = STABILIZED_NOI / cap
        rows.append({"cap": cap, "value": value,
                     "vs_all_in": value - tpc, "vs_cash": value - cash})
    return rows


# ── Tie-out validation ──────────────────────────────────────────────────────
def validate():
    checks = []

    def chk(label, got, exp, tol):
        ok = abs(got - exp) <= tol
        checks.append((label, got, exp, ok))
        return ok

    ct = {s["name"]: s for s in cotenancy_table()}
    chk("Qualifying Required SF", qualifying_required_sf(), 184432, 0)
    chk("Base occupancy %", ct["Base (today)"]["occupancy"], 0.654857, 1e-5)
    chk("Base cushion SF", ct["Base (today)"]["cushion_sf"], 1367.95, 0.1)
    chk("Kohl’s Vacant occ %", ct["Kohl’s Vacant"]["occupancy"], 0.485100, 1e-5)
    chk("Kohl’s + GC occ %", ct["Kohl’s + GC Vacant"]["occupancy"], 0.428225, 1e-5)
    chk("Multifamily+K/GC occ %", ct["Multifamily + K/GC Vacant"]["occupancy"], 0.510217, 1e-5)

    chk("Total Project Cost", total_project_cost(), 41350000, 1)
    chk("Cash basis (ex-land)", cash_basis(), 35588452, 1)
    yoc = yield_on_cost()
    chk("YoC all-in", yoc["all_in"]["yoc"], 0.056590, 1e-5)
    chk("YoC cash basis", yoc["cash"]["yoc"], 0.065752, 1e-5)
    ex = {round(r["cap"], 4): r for r in exit_cap_sensitivity()}
    chk("Value @ 6.5% cap", ex[0.065]["value"], 36000000, 1)
    chk("Value @ 6.25% cap", ex[0.0625]["value"], 37440000, 1)
    return checks


if __name__ == "__main__":
    print(f"Inputs — roster: {ROSTER_SOURCE}  |  USES: {USES_SOURCE}\n")
    print("Co-tenancy scenarios:")
    for s in cotenancy_table():
        print(f"  {s['name']:26} occ={s['occupancy']:.4%}  cushion={s['cushion_sf']:>10,.0f} SF  "
              f"{'PASS' if s['pass'] else 'FAIL'}  {s['rent']}")
    y = yield_on_cost()
    print(f"\nTPC ${total_project_cost():,}  |  cash basis ${cash_basis():,}")
    print(f"YoC all-in {y['all_in']['yoc']:.2%}  |  YoC cash {y['cash']['yoc']:.2%}")
    print("Exit-cap sensitivity:")
    for r in exit_cap_sensitivity():
        print(f"  {r['cap']:.2%}  value ${r['value']:>12,.0f}  vs cash ${r['vs_cash']:>12,.0f}")
    print("\nTie-out:")
    ok_all = True
    for label, got, exp, ok in validate():
        ok_all &= ok
        g = f"{got:,.4f}" if isinstance(got, float) else f"{got:,}"
        print(f"  [{'OK ' if ok else 'XX '}] {label:26} got={g}")
    print("\nTIE-OUT:", "PASS ✓" if ok_all else "REVIEW")
