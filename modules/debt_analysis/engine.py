"""Debt & Loan Analysis engine.

Standalone engine for Chamberlain debt analysis. Can run with hardcoded
defaults or with live data from the chamberlain proforma engine.

Capabilities:
  - Full amortization schedule (monthly → annual rollup)
  - DSCR tracking against covenants
  - MIP (Mortgage Insurance Premium) schedule
  - LTV tracking over hold period
  - Debt maturity / payoff analysis
  - Refinance scenario modeling

All Chamberlain-specific defaults sourced from:
  - Property Overview Summary 11/7/25 ("Existing Loan" tab)
  - ANNUAL PROFORMA ("ANNUAL PROFORMA" tab)
  - LLC Agreement §5.2, HUD Regulatory Agreement
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Chamberlain Defaults ─────────────────────────────────────────

CHAMBERLAIN_LOAN = {
    'lender': 'Colliers Mortgage, LLC',
    'loan_type': 'HUD 223(f)',
    'original_principal': 52_967_700.00,
    'rate': 0.0233,
    'term_months': 420,
    'amortization_months': 420,
    'io_months': 0,
    'first_payment_date': '2021-12-01',
    'maturity_date': '2056-11-01',
    'monthly_payment': 184_565.17,
    'proforma_start_balance': 48_771_038.11,
    'proforma_start_date': '2025-12-31',
}

CHAMBERLAIN_MIP = {
    'rate': 0.0035,  # 0.35% of UPB annually
    'description': 'HUD Mortgage Insurance Premium',
}

CHAMBERLAIN_CAPEX_LOAN = {
    'max_principal': 1_013_857.00,
    'rate': 0.0,
    'description': 'Capital Funding Loan (capex shortfall)',
}

CHAMBERLAIN_SURPLUS_NOTE = {
    'principal': 682_850.00,
    'rate': 0.02,
    'annual_payment': 45_282.00,
    'description': 'HRA Surplus Cash Note',
}

CHAMBERLAIN_PROPERTY = {
    'acquisition_cost_basis': 61_805_592.00,
    'total_equity': 8_837_892.00,
    'units': 150,
    'hold_years': 10,
}


# ─── Data Classes ─────────────────────────────────────────────────

@dataclass
class AmortizationMonth:
    """Single month in the amortization schedule."""
    month: int  # 1-based proforma month
    calendar_date: str  # YYYY-MM
    beginning_balance: float
    payment: float
    principal: float
    interest: float
    ending_balance: float
    cumulative_principal: float
    cumulative_interest: float

    def to_dict(self):
        return {
            'month': self.month,
            'calendar_date': self.calendar_date,
            'beginning_balance': round(self.beginning_balance, 2),
            'payment': round(self.payment, 2),
            'principal': round(self.principal, 2),
            'interest': round(self.interest, 2),
            'ending_balance': round(self.ending_balance, 2),
            'cumulative_principal': round(self.cumulative_principal, 2),
            'cumulative_interest': round(self.cumulative_interest, 2),
        }


@dataclass
class AmortizationYear:
    """Annual rollup of amortization data."""
    year: int  # proforma year 1–N
    calendar_year: int
    beginning_balance: float
    ending_balance: float
    total_payment: float
    total_principal: float
    total_interest: float
    principal_pct: float  # % of payment going to principal
    balance_reduction_pct: float  # year-over-year balance reduction

    def to_dict(self):
        return {
            'year': self.year,
            'calendar_year': self.calendar_year,
            'beginning_balance': round(self.beginning_balance, 2),
            'ending_balance': round(self.ending_balance, 2),
            'total_payment': round(self.total_payment, 2),
            'total_principal': round(self.total_principal, 2),
            'total_interest': round(self.total_interest, 2),
            'principal_pct': round(self.principal_pct, 4),
            'balance_reduction_pct': round(self.balance_reduction_pct, 4),
        }


@dataclass
class DSCRYear:
    """DSCR data for one proforma year."""
    year: int
    calendar_year: int
    noi: float
    debt_service: float
    dscr: float
    dscr_with_mip: float  # NOI / (debt_service + MIP)
    noi_breakeven: float  # debt_service / NOI — breakeven occupancy proxy
    covenant_status: str  # 'pass', 'warning', 'breach'

    def to_dict(self):
        return {
            'year': self.year,
            'calendar_year': self.calendar_year,
            'noi': round(self.noi, 2),
            'debt_service': round(self.debt_service, 2),
            'dscr': round(self.dscr, 3),
            'dscr_with_mip': round(self.dscr_with_mip, 3),
            'noi_breakeven': round(self.noi_breakeven, 4),
            'covenant_status': self.covenant_status,
        }


@dataclass
class MIPYear:
    """MIP data for one proforma year."""
    year: int
    calendar_year: int
    avg_upb: float
    mip_amount: float
    cumulative_mip: float

    def to_dict(self):
        return {
            'year': self.year,
            'calendar_year': self.calendar_year,
            'avg_upb': round(self.avg_upb, 2),
            'mip_amount': round(self.mip_amount, 2),
            'cumulative_mip': round(self.cumulative_mip, 2),
        }


@dataclass
class LTVYear:
    """Loan-to-value data for one proforma year."""
    year: int
    calendar_year: int
    loan_balance: float
    estimated_value: float  # NOI / cap rate
    ltv: float
    equity_value: float

    def to_dict(self):
        return {
            'year': self.year,
            'calendar_year': self.calendar_year,
            'loan_balance': round(self.loan_balance, 2),
            'estimated_value': round(self.estimated_value, 2),
            'ltv': round(self.ltv, 4),
            'equity_value': round(self.equity_value, 2),
        }


@dataclass
class DebtSummary:
    """Overall debt position summary."""
    # Acquisition loan
    lender: str
    loan_type: str
    original_principal: float
    current_balance: float
    rate: float
    monthly_payment: float
    annual_debt_service: float
    term_months: int
    amortization_months: int
    maturity_date: str
    remaining_term_months: int
    # MIP
    mip_rate: float
    year1_mip: float
    # Capex loan
    capex_loan_max: float
    capex_loan_rate: float
    # Surplus cash note
    surplus_note_principal: float
    surplus_note_rate: float
    surplus_note_annual_payment: float
    # Totals
    total_debt_obligations: float  # annual: P&I + MIP + surplus note
    total_cost_basis: float
    total_equity: float
    initial_ltv: float

    def to_dict(self):
        return {
            'lender': self.lender,
            'loan_type': self.loan_type,
            'original_principal': round(self.original_principal, 2),
            'current_balance': round(self.current_balance, 2),
            'rate': self.rate,
            'rate_pct': f'{self.rate * 100:.2f}%',
            'monthly_payment': round(self.monthly_payment, 2),
            'annual_debt_service': round(self.annual_debt_service, 2),
            'term_months': self.term_months,
            'amortization_months': self.amortization_months,
            'maturity_date': self.maturity_date,
            'remaining_term_months': self.remaining_term_months,
            'mip_rate': self.mip_rate,
            'mip_rate_pct': f'{self.mip_rate * 100:.2f}%',
            'year1_mip': round(self.year1_mip, 2),
            'capex_loan_max': round(self.capex_loan_max, 2),
            'capex_loan_rate': self.capex_loan_rate,
            'surplus_note_principal': round(self.surplus_note_principal, 2),
            'surplus_note_rate': self.surplus_note_rate,
            'surplus_note_annual_payment': round(self.surplus_note_annual_payment, 2),
            'total_debt_obligations': round(self.total_debt_obligations, 2),
            'total_cost_basis': round(self.total_cost_basis, 2),
            'total_equity': round(self.total_equity, 2),
            'initial_ltv': round(self.initial_ltv, 4),
        }


@dataclass
class PayoffScenario:
    """Prepayment / payoff analysis for a specific year."""
    year: int
    calendar_year: int
    outstanding_balance: float
    total_paid_to_date: float
    total_interest_to_date: float
    total_principal_to_date: float
    remaining_payments: int
    remaining_interest: float  # total interest if held to maturity from this point
    interest_savings_vs_maturity: float

    def to_dict(self):
        return {
            'year': self.year,
            'calendar_year': self.calendar_year,
            'outstanding_balance': round(self.outstanding_balance, 2),
            'total_paid_to_date': round(self.total_paid_to_date, 2),
            'total_interest_to_date': round(self.total_interest_to_date, 2),
            'total_principal_to_date': round(self.total_principal_to_date, 2),
            'remaining_payments': self.remaining_payments,
            'remaining_interest': round(self.remaining_interest, 2),
            'interest_savings_vs_maturity': round(self.interest_savings_vs_maturity, 2),
        }


@dataclass
class DebtAnalysisResult:
    """Complete debt analysis output."""
    summary: DebtSummary = None
    amortization_monthly: list[AmortizationMonth] = field(default_factory=list)
    amortization_annual: list[AmortizationYear] = field(default_factory=list)
    dscr_by_year: list[DSCRYear] = field(default_factory=list)
    mip_by_year: list[MIPYear] = field(default_factory=list)
    ltv_by_year: list[LTVYear] = field(default_factory=list)
    payoff_scenarios: list[PayoffScenario] = field(default_factory=list)

    # Source tracking
    proforma_source: str = 'defaults'
    tif_scenario: str = 'baseline'

    def to_dict(self):
        return {
            'summary': self.summary.to_dict() if self.summary else None,
            'amortization_annual': [a.to_dict() for a in self.amortization_annual],
            'dscr_by_year': [d.to_dict() for d in self.dscr_by_year],
            'mip_by_year': [m.to_dict() for m in self.mip_by_year],
            'ltv_by_year': [l.to_dict() for l in self.ltv_by_year],
            'payoff_scenarios': [p.to_dict() for p in self.payoff_scenarios],
            'proforma_source': self.proforma_source,
            'tif_scenario': self.tif_scenario,
        }


# ─── Engine ───────────────────────────────────────────────────────

class DebtAnalysisEngine:
    """Runs all debt analysis computations."""

    def __init__(self):
        self.loan = dict(CHAMBERLAIN_LOAN)
        self.mip = dict(CHAMBERLAIN_MIP)
        self.capex = dict(CHAMBERLAIN_CAPEX_LOAN)
        self.surplus = dict(CHAMBERLAIN_SURPLUS_NOTE)
        self.prop = dict(CHAMBERLAIN_PROPERTY)

    # ── PMT helper ────────────────────────────────────────────────

    @staticmethod
    def _pmt(principal: float, annual_rate: float, n_periods: int) -> float:
        """Excel-style PMT."""
        if n_periods <= 0:
            return 0.0
        if annual_rate <= 0.0:
            return principal / n_periods
        r = annual_rate / 12.0
        return principal * (r * (1 + r) ** n_periods) / ((1 + r) ** n_periods - 1)

    # ── Amortization ──────────────────────────────────────────────

    def build_amortization(self, months: int = 120,
                           start_balance: float = None,
                           rate: float = None,
                           monthly_payment: float = None,
                           start_year: int = 2026) -> list[AmortizationMonth]:
        """Build monthly amortization schedule.

        Args:
            months: number of months to project (default 120 = 10-year hold)
            start_balance: UPB at start (default: proforma start balance)
            rate: annual rate (default: loan rate)
            monthly_payment: P&I payment (default: loan payment)
            start_year: first calendar year

        Returns:
            List of AmortizationMonth objects.
        """
        upb = start_balance or self.loan['proforma_start_balance']
        ann_rate = rate or self.loan['rate']
        pmt = monthly_payment or self.loan['monthly_payment']

        schedule = []
        cum_principal = 0.0
        cum_interest = 0.0

        for m in range(1, months + 1):
            interest = upb * ann_rate / 12.0
            principal = max(0.0, min(pmt - interest, upb))
            payment = interest + principal
            cum_principal += principal
            cum_interest += interest
            ending = max(0.0, upb - principal)

            cal_year = start_year + (m - 1) // 12
            cal_month = ((m - 1) % 12) + 1
            cal_date = f'{cal_year}-{cal_month:02d}'

            schedule.append(AmortizationMonth(
                month=m,
                calendar_date=cal_date,
                beginning_balance=upb,
                payment=round(payment, 2),
                principal=round(principal, 2),
                interest=round(interest, 2),
                ending_balance=round(ending, 2),
                cumulative_principal=round(cum_principal, 2),
                cumulative_interest=round(cum_interest, 2),
            ))
            upb = ending

        return schedule

    def rollup_annual(self, monthly: list[AmortizationMonth],
                      start_year: int = 2026) -> list[AmortizationYear]:
        """Roll up monthly amortization to annual summaries."""
        years = {}
        for m in monthly:
            yr = (m.month - 1) // 12 + 1
            if yr not in years:
                years[yr] = {
                    'year': yr,
                    'calendar_year': start_year + yr - 1,
                    'begin': m.beginning_balance,
                    'end': m.ending_balance,
                    'payment': 0.0,
                    'principal': 0.0,
                    'interest': 0.0,
                }
            y = years[yr]
            y['payment'] += m.payment
            y['principal'] += m.principal
            y['interest'] += m.interest
            y['end'] = m.ending_balance

        result = []
        for yr_num in sorted(years.keys()):
            y = years[yr_num]
            ppct = y['principal'] / y['payment'] if y['payment'] > 0 else 0
            reduction = (y['begin'] - y['end']) / y['begin'] if y['begin'] > 0 else 0
            result.append(AmortizationYear(
                year=y['year'],
                calendar_year=y['calendar_year'],
                beginning_balance=y['begin'],
                ending_balance=y['end'],
                total_payment=y['payment'],
                total_principal=y['principal'],
                total_interest=y['interest'],
                principal_pct=ppct,
                balance_reduction_pct=reduction,
            ))
        return result

    # ── DSCR ──────────────────────────────────────────────────────

    def compute_dscr(self, noi_by_year: dict[int, float],
                     debt_service_by_year: dict[int, float],
                     mip_by_year_dict: dict[int, float] = None,
                     calendar_years: dict[int, int] = None,
                     dscr_covenant: float = 1.15,
                     dscr_warning: float = 1.25) -> list[DSCRYear]:
        """Compute DSCR for each proforma year.

        Args:
            noi_by_year: {proforma_year: NOI}
            debt_service_by_year: {proforma_year: annual P&I}
            mip_by_year_dict: {proforma_year: annual MIP}
            calendar_years: {proforma_year: calendar_year}
            dscr_covenant: minimum DSCR threshold (HUD typical 1.15x)
            dscr_warning: warning threshold

        Returns:
            List of DSCRYear objects.
        """
        results = []
        for yr in sorted(noi_by_year.keys()):
            noi = noi_by_year[yr]
            ds = debt_service_by_year.get(yr, self.loan['monthly_payment'] * 12)
            mip = (mip_by_year_dict or {}).get(yr, 0.0)
            cal = (calendar_years or {}).get(yr, 2025 + yr)

            dscr = noi / ds if ds > 0 else 99.99
            dscr_inc_mip = noi / (ds + mip) if (ds + mip) > 0 else 99.99
            breakeven = ds / noi if noi > 0 else 1.0

            if dscr < dscr_covenant:
                status = 'breach'
            elif dscr < dscr_warning:
                status = 'warning'
            else:
                status = 'pass'

            results.append(DSCRYear(
                year=yr,
                calendar_year=cal,
                noi=noi,
                debt_service=ds,
                dscr=dscr,
                dscr_with_mip=dscr_inc_mip,
                noi_breakeven=breakeven,
                covenant_status=status,
            ))
        return results

    # ── MIP Schedule ──────────────────────────────────────────────

    def compute_mip_schedule(self, amort_annual: list[AmortizationYear]) -> list[MIPYear]:
        """Compute MIP for each year based on average UPB."""
        results = []
        cumulative = 0.0
        for a in amort_annual:
            avg_upb = (a.beginning_balance + a.ending_balance) / 2.0
            mip_amt = avg_upb * self.mip['rate']
            cumulative += mip_amt
            results.append(MIPYear(
                year=a.year,
                calendar_year=a.calendar_year,
                avg_upb=avg_upb,
                mip_amount=mip_amt,
                cumulative_mip=cumulative,
            ))
        return results

    # ── LTV Tracking ──────────────────────────────────────────────

    def compute_ltv(self, amort_annual: list[AmortizationYear],
                    noi_by_year: dict[int, float] = None,
                    exit_cap_rate: float = 0.055) -> list[LTVYear]:
        """Track LTV over hold period.

        Estimates property value as NOI / cap_rate. If NOI not provided,
        uses a simple 2% annual growth from acquisition basis.
        """
        results = []
        base_value = self.prop['acquisition_cost_basis']

        for a in amort_annual:
            if noi_by_year and a.year in noi_by_year:
                est_value = noi_by_year[a.year] / exit_cap_rate if exit_cap_rate > 0 else base_value
            else:
                est_value = base_value * (1.02 ** a.year)

            ltv = a.ending_balance / est_value if est_value > 0 else 1.0
            equity = est_value - a.ending_balance

            results.append(LTVYear(
                year=a.year,
                calendar_year=a.calendar_year,
                loan_balance=a.ending_balance,
                estimated_value=est_value,
                ltv=ltv,
                equity_value=equity,
            ))
        return results

    # ── Payoff Analysis ───────────────────────────────────────────

    def compute_payoff_scenarios(self, monthly: list[AmortizationMonth],
                                 hold_years: int = 10) -> list[PayoffScenario]:
        """Compute payoff/prepayment economics at each year-end."""
        # First, compute total interest if held to full amortization
        full_schedule = self.build_amortization(
            months=self.loan['amortization_months'],
            start_balance=self.loan['proforma_start_balance'],
        )
        total_interest_full = sum(m.interest for m in full_schedule)

        results = []
        for yr in range(1, hold_years + 1):
            end_month = yr * 12
            if end_month > len(monthly):
                break

            m = monthly[end_month - 1]  # last month of year

            # Total interest paid to date
            interest_to_date = sum(monthly[i].interest for i in range(end_month))
            principal_to_date = sum(monthly[i].principal for i in range(end_month))
            total_paid = sum(monthly[i].payment for i in range(end_month))

            # Remaining interest from this point forward (if held to maturity)
            remaining_months = self.loan['amortization_months'] - end_month
            # Build remaining schedule from current balance
            remaining_schedule = self.build_amortization(
                months=max(remaining_months, 0),
                start_balance=m.ending_balance,
            )
            remaining_interest = sum(rm.interest for rm in remaining_schedule)

            # Interest savings if paid off now vs. held to maturity
            savings = remaining_interest

            cal_year = m.calendar_date[:4]

            results.append(PayoffScenario(
                year=yr,
                calendar_year=int(cal_year),
                outstanding_balance=m.ending_balance,
                total_paid_to_date=total_paid,
                total_interest_to_date=interest_to_date,
                total_principal_to_date=principal_to_date,
                remaining_payments=max(remaining_months, 0),
                remaining_interest=remaining_interest,
                interest_savings_vs_maturity=savings,
            ))
        return results

    # ── Summary ───────────────────────────────────────────────────

    def build_summary(self, mip_schedule: list[MIPYear] = None) -> DebtSummary:
        """Build overall debt position summary."""
        year1_mip = mip_schedule[0].mip_amount if mip_schedule else (
            self.loan['proforma_start_balance'] * self.mip['rate']
        )
        annual_ds = self.loan['monthly_payment'] * 12

        # Remaining term from proforma start
        # Original term: 420 months from 12/2021. Proforma starts ~1/2026 = ~49 months in
        elapsed = 49  # approximate months from first payment to proforma start
        remaining = max(self.loan['term_months'] - elapsed, 0)

        total_obligations = annual_ds + year1_mip + self.surplus['annual_payment']
        initial_ltv = self.loan['proforma_start_balance'] / self.prop['acquisition_cost_basis']

        return DebtSummary(
            lender=self.loan['lender'],
            loan_type=self.loan['loan_type'],
            original_principal=self.loan['original_principal'],
            current_balance=self.loan['proforma_start_balance'],
            rate=self.loan['rate'],
            monthly_payment=self.loan['monthly_payment'],
            annual_debt_service=annual_ds,
            term_months=self.loan['term_months'],
            amortization_months=self.loan['amortization_months'],
            maturity_date=self.loan['maturity_date'],
            remaining_term_months=remaining,
            mip_rate=self.mip['rate'],
            year1_mip=year1_mip,
            capex_loan_max=self.capex['max_principal'],
            capex_loan_rate=self.capex['rate'],
            surplus_note_principal=self.surplus['principal'],
            surplus_note_rate=self.surplus['rate'],
            surplus_note_annual_payment=self.surplus['annual_payment'],
            total_debt_obligations=total_obligations,
            total_cost_basis=self.prop['acquisition_cost_basis'],
            total_equity=self.prop['total_equity'],
            initial_ltv=initial_ltv,
        )

    # ── Full Run ──────────────────────────────────────────────────

    def run_analysis(self,
                     noi_by_year: dict[int, float] = None,
                     debt_service_by_year: dict[int, float] = None,
                     calendar_years: dict[int, int] = None,
                     exit_cap_rate: float = 0.055,
                     hold_years: int = 10) -> DebtAnalysisResult:
        """Run the full debt analysis.

        Args:
            noi_by_year: {proforma_year: NOI} from proforma (optional)
            debt_service_by_year: {proforma_year: annual_ds} from proforma
            calendar_years: {proforma_year: calendar_year}
            exit_cap_rate: cap rate for LTV estimation
            hold_years: projection period

        Returns:
            DebtAnalysisResult with all computed schedules.
        """
        result = DebtAnalysisResult()

        # 1. Amortization (full hold period, monthly)
        monthly = self.build_amortization(
            months=hold_years * 12,
            start_year=2026,
        )
        result.amortization_monthly = monthly

        # 2. Annual rollup
        annual = self.rollup_annual(monthly, start_year=2026)
        result.amortization_annual = annual

        # 3. MIP schedule
        mip_schedule = self.compute_mip_schedule(annual)
        result.mip_by_year = mip_schedule
        mip_dict = {m.year: m.mip_amount for m in mip_schedule}

        # 4. Build debt service by year if not provided
        if debt_service_by_year is None:
            debt_service_by_year = {
                a.year: a.total_payment for a in annual
            }

        # 5. DSCR (needs NOI from proforma or defaults)
        if noi_by_year is None:
            # Use rough NOI estimates (growing ~2%/year from ~$3.5M)
            noi_by_year = {yr: 3_500_000 * (1.02 ** (yr - 1)) for yr in range(1, hold_years + 1)}

        result.dscr_by_year = self.compute_dscr(
            noi_by_year, debt_service_by_year, mip_dict, calendar_years,
        )

        # 6. LTV tracking
        result.ltv_by_year = self.compute_ltv(annual, noi_by_year, exit_cap_rate)

        # 7. Payoff scenarios
        result.payoff_scenarios = self.compute_payoff_scenarios(monthly, hold_years)

        # 8. Summary
        result.summary = self.build_summary(mip_schedule)

        return result

    # ── Refinance Scenario ────────────────────────────────────────

    def run_refi_scenario(self,
                          refi_year: int = 5,
                          new_principal: float = 30_000_000,
                          new_rate: float = 0.06,
                          new_term_months: int = 60,
                          new_amort_months: int = 360,
                          new_io_months: int = 24,
                          noi_by_year: dict[int, float] = None,
                          hold_years: int = 10) -> dict:
        """Model a refinance scenario.

        Returns comparison of current vs. refi debt service and returns.
        """
        # Current schedule (no refi)
        current = self.run_analysis(
            noi_by_year=noi_by_year,
            hold_years=hold_years,
        )

        # Refi: build schedule pre-refi (years 1 to refi_year-1)
        pre_refi_months = (refi_year - 1) * 12
        pre_schedule = self.build_amortization(months=pre_refi_months)
        payoff_balance = pre_schedule[-1].ending_balance if pre_schedule else self.loan['proforma_start_balance']

        # Net proceeds from refi
        net_proceeds = new_principal - payoff_balance

        # New loan payment
        if new_io_months > 0:
            io_payment = new_principal * new_rate / 12.0
        new_payment = self._pmt(new_principal, new_rate, new_amort_months)

        # Post-refi schedule
        post_months = (hold_years - refi_year + 1) * 12
        post_schedule = self.build_amortization(
            months=post_months,
            start_balance=new_principal,
            rate=new_rate,
            monthly_payment=new_payment,
            start_year=2026 + refi_year - 1,
        )

        # Apply IO period
        for i, m in enumerate(post_schedule):
            if i < new_io_months:
                io_interest = m.beginning_balance * new_rate / 12.0
                m.payment = round(io_interest, 2)
                m.principal = 0.0
                m.interest = round(io_interest, 2)
                if i + 1 < len(post_schedule):
                    post_schedule[i + 1] = AmortizationMonth(
                        month=post_schedule[i + 1].month,
                        calendar_date=post_schedule[i + 1].calendar_date,
                        beginning_balance=m.beginning_balance,
                        payment=0, principal=0, interest=0,
                        ending_balance=m.beginning_balance,
                        cumulative_principal=0, cumulative_interest=0,
                    )
                m.ending_balance = m.beginning_balance

        # Compare annual debt service
        refi_ds_by_year = {}
        for yr in range(1, hold_years + 1):
            if yr < refi_year:
                refi_ds_by_year[yr] = self.loan['monthly_payment'] * 12
            elif yr == refi_year:
                # Partial year: pre-refi months + post-refi months
                refi_ds_by_year[yr] = new_payment * 12  # simplified
            else:
                refi_ds_by_year[yr] = new_payment * 12

        current_ds = {a.year: a.total_payment for a in current.amortization_annual}

        comparison = []
        for yr in range(1, hold_years + 1):
            c_ds = current_ds.get(yr, 0)
            r_ds = refi_ds_by_year.get(yr, 0)
            comparison.append({
                'year': yr,
                'calendar_year': 2025 + yr,
                'current_ds': round(c_ds, 2),
                'refi_ds': round(r_ds, 2),
                'delta': round(r_ds - c_ds, 2),
            })

        # Ending balance at exit
        refi_exit_balance = post_schedule[-1].ending_balance if post_schedule else new_principal

        return {
            'refi_year': refi_year,
            'payoff_balance': round(payoff_balance, 2),
            'new_principal': round(new_principal, 2),
            'net_proceeds': round(net_proceeds, 2),
            'new_rate': new_rate,
            'new_monthly_payment': round(new_payment, 2),
            'new_annual_ds': round(new_payment * 12, 2),
            'current_annual_ds': round(self.loan['monthly_payment'] * 12, 2),
            'ds_increase': round((new_payment * 12) - (self.loan['monthly_payment'] * 12), 2),
            'exit_balance_current': round(current.amortization_annual[-1].ending_balance, 2),
            'exit_balance_refi': round(refi_exit_balance, 2),
            'comparison_by_year': comparison,
        }


__all__ = [
    'DebtAnalysisEngine',
    'DebtAnalysisResult',
    'CHAMBERLAIN_LOAN',
]
