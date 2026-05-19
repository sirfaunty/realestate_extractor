"""
engine.py — LeaseAnalysisEngine

Integrates the lease pricing model with the platform's SQLite database.
Loads lease data from extracted documents, computes break-even floors
from operating data, and runs the full pricing pipeline.
"""

from __future__ import annotations

import datetime
import logging
import statistics
from collections import defaultdict
from typing import Optional

logger = logging.getLogger(__name__)


class LeaseAnalysisEngine:
    """Runs the full lease pricing pipeline against the platform database."""

    # CRE defaults for operating cost assumptions
    DEFAULT_MAKE_READY    = 1_500.0
    DEFAULT_MARKETING     = 300.0
    DEFAULT_COMMISSION_FL = 150.0   # new lease flat fee
    DEFAULT_DOWNTIME_DAYS = 30.0

    def __init__(self, db):
        """db: platform OrgDatabase (has .conn SQLite connection)."""
        self.db = db

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def get_properties_with_lease_data(self) -> list[dict]:
        """Return properties that have rent-roll or lease document data."""
        cur = self.db.conn.cursor()
        cur.execute("""
            SELECT DISTINCT p.id, p.name, p.address, p.city, p.state,
                   p.total_units, p.status,
                   COUNT(DISTINCT rr.id) AS rr_count,
                   COUNT(DISTINCT d.id)  AS doc_count
            FROM properties p
            LEFT JOIN rent_roll_entries rr ON rr.property_id = p.id
            LEFT JOIN documents d ON d.property_id = p.id
                AND d.document_type IN ('lease', 'rent_roll', 'lease_abstract')
            GROUP BY p.id
            HAVING rr_count > 0 OR doc_count > 0
            ORDER BY p.name
        """)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def get_property(self, property_id: int) -> Optional[dict]:
        cur = self.db.conn.cursor()
        cur.execute("SELECT * FROM properties WHERE id = ?", (property_id,))
        row = cur.fetchone()
        if not row:
            return None
        cols = [c[0] for c in cur.description]
        return dict(zip(cols, row))

    def get_rent_roll(self, property_id: int) -> list[dict]:
        """Load current rent-roll entries for a property."""
        cur = self.db.conn.cursor()
        cur.execute("""
            SELECT rr.*, u.unit_type, u.bedrooms, u.bathrooms, u.square_footage
            FROM rent_roll_entries rr
            LEFT JOIN units u ON u.property_id = rr.property_id
                AND u.unit_number = rr.unit_number
            WHERE rr.property_id = ?
            ORDER BY rr.unit_number
        """, (property_id,))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def get_operating_expenses(self, property_id: int) -> dict:
        """Load operating expense summary from operating_statement_items."""
        cur = self.db.conn.cursor()
        cur.execute("""
            SELECT category, subcategory, line_item, SUM(amount) as total
            FROM operating_statement_items
            WHERE property_id = ?
            GROUP BY category, subcategory, line_item
            ORDER BY category, subcategory
        """, (property_id,))
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        return {"items": rows, "total": sum(r["total"] or 0 for r in rows)}

    def get_financial_terms(self, property_id: int) -> list[dict]:
        """Load financial terms extracted from documents for a property."""
        cur = self.db.conn.cursor()
        cur.execute("""
            SELECT ft.*
            FROM financial_terms ft
            JOIN documents d ON d.id = ft.document_id
            WHERE d.property_id = ?
            ORDER BY ft.term_type, ft.effective_date
        """, (property_id,))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    # ------------------------------------------------------------------
    # LeaseRecord construction from DB rows
    # ------------------------------------------------------------------

    def build_lease_records(self, property_id: int):
        """Build LeaseRecord list from DB rent-roll + financial terms."""
        from .models import LeaseRecord

        def _parse_date(v) -> Optional[datetime.date]:
            if isinstance(v, datetime.date):
                return v
            if isinstance(v, str):
                for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
                    try:
                        return datetime.datetime.strptime(v, fmt).date()
                    except ValueError:
                        pass
            return None

        rr_rows = self.get_rent_roll(property_id)
        records = []
        for row in rr_rows:
            unit = row.get("unit_number") or row.get("suite") or ""
            unit_type = row.get("unit_type") or self._infer_unit_type(row)
            general_type = self._infer_general_type(row)

            # Derive face vs effective rent
            face_rent = row.get("monthly_rent") or row.get("market_rent")
            effective_rent = row.get("monthly_rent")  # DB may not have concession split

            r = LeaseRecord(
                unit=str(unit),
                unit_type=str(unit_type) if unit_type else "",
                general_type=str(general_type),
                lease_start=_parse_date(row.get("lease_start")),
                lease_exp=_parse_date(row.get("lease_end")),
                exec_date=_parse_date(row.get("lease_start")),  # best available proxy
                move_out=_parse_date(row.get("lease_end")),
                actual_move_out=None,
                face_rent=float(face_rent) if face_rent else None,
                effective_rent=float(effective_rent) if effective_rent else None,
                term_months=self._compute_term(
                    _parse_date(row.get("lease_start")),
                    _parse_date(row.get("lease_end")),
                ),
                is_renewal=False,
            )
            records.append(r)
        return records

    def _infer_unit_type(self, row: dict) -> str:
        """Infer unit type from available fields."""
        if row.get("unit_type"):
            return row["unit_type"]
        beds = row.get("bedrooms")
        if beds is not None:
            beds = int(float(beds))
            return f"{beds}BR" if beds > 0 else "Studio"
        sqft = row.get("square_footage")
        if sqft:
            sqft = float(sqft)
            if sqft < 450:
                return "Studio"
            elif sqft < 700:
                return "1BR"
            elif sqft < 1000:
                return "2BR"
            else:
                return "3BR"
        return "Unknown"

    def _infer_general_type(self, row: dict) -> str:
        beds = row.get("bedrooms")
        if beds is not None:
            beds = int(float(beds))
            if beds == 0:
                return "Studio"
            return f"{beds}BR"
        return self._infer_unit_type(row)

    def _compute_term(self, start, end) -> Optional[float]:
        if start and end:
            days = (end - start).days
            if 30 <= days <= 900:
                return round(days / 30.4, 1)
        return 12.0

    # ------------------------------------------------------------------
    # Availability snapshot from DB
    # ------------------------------------------------------------------

    def build_availability_snapshot(self, property_id: int) -> dict:
        """Build AvailabilitySnapshot per unit_type from DB units."""
        from .models import snapshot_from_db_units

        cur = self.db.conn.cursor()
        cur.execute("""
            SELECT unit_number, unit_type, bedrooms, status, current_rent,
                   market_rent, square_footage
            FROM units
            WHERE property_id = ?
        """, (property_id,))
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]

        # Normalise unit_type
        for r in rows:
            if not r.get("unit_type"):
                beds = r.get("bedrooms")
                if beds is not None:
                    r["unit_type"] = f"{int(float(beds))}BR" if float(beds) > 0 else "Studio"
                else:
                    r["unit_type"] = "Unknown"

        return snapshot_from_db_units(rows)

    # ------------------------------------------------------------------
    # Break-even floor computation
    # ------------------------------------------------------------------

    def compute_floors(
        self,
        property_id: int,
        leases=None,
        scenario: str = "new",
    ) -> dict:
        """Compute break-even floors for all unit types of a property."""
        from .models import (
            BreakevenAssumptions, breakeven_for_all_unit_types, downtime_table,
        )

        if leases is None:
            leases = self.build_lease_records(property_id)

        # Derive in-place rents per unit type from rent roll
        rr_rows = self.get_rent_roll(property_id)
        inplace_rents: dict[str, list[float]] = defaultdict(list)
        for row in rr_rows:
            ut = self._infer_unit_type(row)
            rent = row.get("monthly_rent")
            if rent:
                inplace_rents[ut].append(float(rent))

        inplace = {ut: statistics.mean(v) for ut, v in inplace_rents.items() if v}

        # Derive term months per unit type
        term_by_ut: dict[str, list[float]] = defaultdict(list)
        for l in leases:
            if l.term_months and 6 <= l.term_months <= 24:
                term_by_ut[l.unit_type].append(l.term_months)
        terms = {ut: statistics.median(v) for ut, v in term_by_ut.items() if v}

        # Downtime
        dt = downtime_table(leases)

        # Unit type metadata
        ut_to_fp = {ut: ut for ut in inplace}  # use UT as its own FP if no index
        ut_to_gen = {}
        for l in leases:
            ut_to_gen.setdefault(l.unit_type, l.general_type)

        assumptions = BreakevenAssumptions(
            downtime_statistic="median",
            downtime_outlier_cap_multiple=2.0,
            new_commission_flat=self.DEFAULT_COMMISSION_FL,
            renewal_commission_flat=75.0,
            marketing_cost_flat=self.DEFAULT_MARKETING,
        )

        unit_types = sorted(inplace.keys())
        return breakeven_for_all_unit_types(
            unit_types, inplace, terms, dt, ut_to_fp, ut_to_gen,
            assumptions, scenario=scenario,
        )

    # ------------------------------------------------------------------
    # Full pricing pipeline
    # ------------------------------------------------------------------

    def run_full_analysis(
        self,
        property_id: int,
        as_of: Optional[datetime.date] = None,
        scenario: str = "new",
    ) -> dict:
        """Run the complete 7-layer pricing pipeline for a property.

        Returns a dict with keys: property, floors, availability, forward,
        velocity, gap, seasonality, pricing, summary.
        """
        from .models import (
            build_forward_exposure, compute_velocity, portfolio_velocity,
            compute_gap, build_seasonality_table,
            price_all, PricingAssumptions,
        )

        as_of = as_of or datetime.date.today()

        prop = self.get_property(property_id)
        leases = self.build_lease_records(property_id)

        # 1. Floors (break-even)
        floors = self.compute_floors(property_id, leases=leases, scenario=scenario)

        # 2. Availability
        avail = self.build_availability_snapshot(property_id)

        # 3. Forward exposure
        total_by_ut = {ut: a.total_units for ut, a in avail.items()}
        current_by_ut = {ut: a.available_units for ut, a in avail.items()}
        rr_rows = self.get_rent_roll(property_id)
        fwd_rows = [
            {"unit_type": self._infer_unit_type(r), "lease_end": r.get("lease_end")}
            for r in rr_rows
        ]
        fwd = build_forward_exposure(fwd_rows, current_by_ut, total_by_ut, as_of)

        # 4. Velocity
        total_units = sum(total_by_ut.values())
        vel = compute_velocity(leases, total_by_ut, as_of, window_days=90)
        port_vel = portfolio_velocity(leases, total_units, as_of, window_days=90)

        # 5. Asking-vs-achieved gap
        port_gap = compute_gap(leases, as_of, window_days=90)["PORTFOLIO"]
        gap_by_ut = compute_gap(leases, as_of, window_days=90, by="unit_type")

        # 6. Seasonality
        season_tab = build_seasonality_table(leases, as_of=as_of, window_months=24)
        season_exec = season_tab["exec_date"]

        # 7. Price (all 7 layers combined)
        pricing_a = PricingAssumptions()
        pricing = price_all(
            breakevens=floors,
            availability=avail,
            forward=fwd,
            velocity=vel,
            gap_by_ut=gap_by_ut,
            portfolio_gap=port_gap,
            season_index=season_exec,
            pricing_month=as_of.month,
            scenario=scenario,
            assumptions=pricing_a,
        )

        # Summary stats
        feasible = [p for p in pricing.values() if p.floor > 0]
        summary = {
            "property_id": property_id,
            "property_name": prop["name"] if prop else "",
            "as_of": as_of.isoformat(),
            "scenario": scenario,
            "unit_type_count": len(pricing),
            "lease_count": len(leases),
            "portfolio_velocity_tier": port_vel.tier,
            "portfolio_gap_level": port_gap.level_tier,
            "avg_floor": round(statistics.mean(p.floor for p in feasible), 2) if feasible else 0,
            "avg_recommended": round(statistics.mean(p.recommended for p in feasible), 2) if feasible else 0,
        }

        return {
            "property": prop,
            "floors": floors,
            "availability": avail,
            "forward": fwd,
            "velocity": vel,
            "portfolio_velocity": port_vel,
            "gap": gap_by_ut,
            "portfolio_gap": port_gap,
            "seasonality": season_exec,
            "pricing": pricing,
            "summary": summary,
        }

    def get_analysis_summary(self, property_id: int) -> dict:
        """Lightweight summary for the dashboard (no full pipeline)."""
        prop = self.get_property(property_id)
        rr = self.get_rent_roll(property_id)

        if not rr:
            return {"property_id": property_id, "has_data": False}

        rents = [float(r["monthly_rent"]) for r in rr if r.get("monthly_rent")]
        vacant = sum(
            1 for r in rr
            if str(r.get("status", "")).lower() in ("vacant", "available", "notice")
        )
        total = len(rr)

        return {
            "property_id": property_id,
            "property_name": prop["name"] if prop else "",
            "has_data": True,
            "total_units": total,
            "vacant_units": vacant,
            "occupancy_pct": round((total - vacant) / max(total, 1) * 100, 1),
            "avg_rent": round(statistics.mean(rents), 2) if rents else 0,
            "min_rent": round(min(rents), 2) if rents else 0,
            "max_rent": round(max(rents), 2) if rents else 0,
            "unit_type_count": len({self._infer_unit_type(r) for r in rr}),
        }
