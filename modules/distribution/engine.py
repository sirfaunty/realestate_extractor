"""Distribution & Surplus Cash engine.

Standalone waterfall engine that models the Chamberlain LLC distribution
mechanics from the LLC Agreement §5.2:

  Tier 1: KA Escrow Recapture — 100% to KA until escrow returned
  Tier 2: 6.5% Preferred Return — pro-rata to unpaid pref balances
  Tier 3: 75/25 Pari Passu — KA 75% / IDP 25% on remaining

Also models:
  - Surplus Cash Note: $22,641 semi-annual payments (Feb 1 / Aug 1)
  - Capital account tracking with pref accrual
  - Return metrics: IRR, Equity Multiple, Cash-on-Cash

The engine uses Chamberlain-specific defaults but is parameterized for
future multi-deal use.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


# ─── Chamberlain Defaults ──────────────────────────────────────────

CHAMBERLAIN_PARTNERS = {
    'KA': {
        'name': 'Kraus-Anderson, Incorporated',
        'role': 'Managing Member',
        'ownership_pct': 0.75,
        'distribution_pct': 0.75,
        'pref_rate': 0.065,
        'pref_compounding': 'monthly',
    },
    'IDP': {
        'name': 'Inland Development Partners',
        'role': 'Limited Partner',
        'ownership_pct': 0.25,
        'distribution_pct': 0.25,
        'pref_rate': 0.065,
        'pref_compounding': 'monthly',
    },
}

# Chamberlain equity: Total Cost Basis $61,805,592 - Acq Loan $52,967,700
CHAMBERLAIN_TOTAL_EQUITY = 8_837_892.0
CHAMBERLAIN_KA_EQUITY = CHAMBERLAIN_TOTAL_EQUITY * 0.75   # $6,628,419
CHAMBERLAIN_IDP_EQUITY = CHAMBERLAIN_TOTAL_EQUITY * 0.25  # $2,209,473

# Surplus Cash Note
CHAMBERLAIN_SURPLUS_CASH_NOTE = {
    'principal': 682_850.0,
    'rate': 0.02,
    'annual_payment': 45_282.0,        # $22,641 × 2
    'semi_annual_payment': 22_641.0,
    'payment_months': [2, 8],           # Feb 1 and Aug 1
    'source': 'Amended & Restated Surplus Cash Note',
    'note': '75% of Surplus Cash per HUD Regulatory Agreement',
}

# Default distributable CF by year (from proforma base scenario estimates)
# These are approximate levered CF values for Chamberlain — users can override
CHAMBERLAIN_DEFAULT_CF = {
    1: 450_000,
    2: 510_000,
    3: 580_000,
    4: 620_000,
    5: 660_000,
    6: 700_000,
    7: 740_000,
    8: 780_000,
    9: 820_000,
    10: 860_000,
}


# ─── Data Classes ──────────────────────────────────────────────────

@dataclass
class PartnerConfig:
    """Configuration for one investor class."""
    id: str
    name: str
    role: str
    ownership_pct: float
    distribution_pct: float
    pref_rate: float
    pref_compounding: str = 'monthly'
    initial_equity: float = 0.0


@dataclass
class WaterfallTierConfig:
    """One tier of the distribution waterfall."""
    order: int
    name: str
    tier_type: str  # escrow_recapture | preferred_return | pari_passu
    allocation: dict[str, float] = field(default_factory=dict)
    pref_classes: list[str] = field(default_factory=list)
    cap_amount: Optional[float] = None
    governing_provision: Optional[str] = None


@dataclass
class SurplusCashNoteConfig:
    """Surplus Cash Note parameters."""
    principal: float = 682_850.0
    rate: float = 0.02
    annual_payment: float = 45_282.0
    starting_balance: Optional[float] = None  # if None, use principal


@dataclass
class DistributionAssumptions:
    """All assumptions for the distribution model."""
    partners: list[PartnerConfig] = field(default_factory=list)
    waterfall_tiers: list[WaterfallTierConfig] = field(default_factory=list)
    surplus_cash_note: Optional[SurplusCashNoteConfig] = None
    entity_name: str = 'Chamberlain Apartments LLC'
    hold_years: int = 10
    first_year: int = 2026
    escrow_amount: float = 0.0  # KA escrow to recapture

    @staticmethod
    def chamberlain_defaults() -> 'DistributionAssumptions':
        """Build Chamberlain-specific assumptions."""
        partners = [
            PartnerConfig(
                id='KA',
                name='Kraus-Anderson, Incorporated',
                role='Managing Member',
                ownership_pct=0.75,
                distribution_pct=0.75,
                pref_rate=0.065,
                pref_compounding='monthly',
                initial_equity=CHAMBERLAIN_KA_EQUITY,
            ),
            PartnerConfig(
                id='IDP',
                name='Inland Development Partners',
                role='Limited Partner',
                ownership_pct=0.25,
                distribution_pct=0.25,
                pref_rate=0.065,
                pref_compounding='monthly',
                initial_equity=CHAMBERLAIN_IDP_EQUITY,
            ),
        ]
        tiers = [
            WaterfallTierConfig(
                order=1,
                name='KA Escrow Recapture',
                tier_type='escrow_recapture',
                allocation={'KA': 1.0, 'IDP': 0.0},
                governing_provision='LLC §5.2(a)',
            ),
            WaterfallTierConfig(
                order=2,
                name='6.5% Preferred Return',
                tier_type='preferred_return',
                pref_classes=['KA', 'IDP'],
                allocation={'KA': 0.75, 'IDP': 0.25},
                governing_provision='LLC §5.2(b)',
            ),
            WaterfallTierConfig(
                order=3,
                name='75/25 Pari Passu',
                tier_type='pari_passu',
                allocation={'KA': 0.75, 'IDP': 0.25},
                governing_provision='LLC §5.2(c)',
            ),
        ]
        return DistributionAssumptions(
            partners=partners,
            waterfall_tiers=tiers,
            surplus_cash_note=SurplusCashNoteConfig(),
            entity_name='Chamberlain Apartments LLC',
            hold_years=10,
            first_year=2026,
            escrow_amount=0.0,
        )

    def to_config(self) -> dict:
        """Serialize the full assumptions (partners, tiers, surplus note, scalars)
        for the editable per-deal config store."""
        return asdict(self)

    @classmethod
    def from_config(cls, config: dict | None) -> 'DistributionAssumptions':
        """Rebuild assumptions from an editable per-deal config. A missing/None
        config returns the Chamberlain defaults unchanged, so the default deal is
        identical to the hardcoded behavior."""
        if not config:
            return cls.chamberlain_defaults()
        partners = [PartnerConfig(**p) for p in config.get('partners', [])]
        tiers = [WaterfallTierConfig(**t) for t in config.get('waterfall_tiers', [])]
        scn = config.get('surplus_cash_note')
        surplus = SurplusCashNoteConfig(**scn) if scn else None
        return cls(
            partners=partners,
            waterfall_tiers=tiers,
            surplus_cash_note=surplus,
            entity_name=config.get('entity_name', 'Chamberlain Apartments LLC'),
            hold_years=config.get('hold_years', 10),
            first_year=config.get('first_year', 2026),
            escrow_amount=config.get('escrow_amount', 0.0),
        )


# ─── Ledger & Results ─────────────────────────────────────────────

@dataclass
class CapitalAccount:
    """Running capital account state for one partner."""
    partner_id: str
    contributed_capital: float = 0.0
    accrued_pref: float = 0.0
    paid_pref: float = 0.0
    escrow_contributed: float = 0.0
    escrow_returned: float = 0.0
    total_distributions: float = 0.0

    @property
    def unreturned_capital(self) -> float:
        return max(0.0, self.contributed_capital)

    @property
    def unpaid_pref(self) -> float:
        return max(0.0, self.accrued_pref - self.paid_pref)

    @property
    def unrecouped_escrow(self) -> float:
        return max(0.0, self.escrow_contributed - self.escrow_returned)

    @property
    def equity_multiple(self) -> float:
        if self.contributed_capital <= 0:
            return 0.0
        return self.total_distributions / self.contributed_capital

    def to_dict(self) -> dict:
        return {
            'partner_id': self.partner_id,
            'contributed_capital': round(self.contributed_capital, 2),
            'accrued_pref': round(self.accrued_pref, 2),
            'paid_pref': round(self.paid_pref, 2),
            'unpaid_pref': round(self.unpaid_pref, 2),
            'escrow_contributed': round(self.escrow_contributed, 2),
            'escrow_returned': round(self.escrow_returned, 2),
            'unrecouped_escrow': round(self.unrecouped_escrow, 2),
            'total_distributions': round(self.total_distributions, 2),
            'equity_multiple': round(self.equity_multiple, 4),
        }


@dataclass
class YearDistribution:
    """One year's distribution outcome."""
    year: int
    calendar_year: int
    distributable_cash: float
    surplus_cash_note_payment: float
    net_distributable: float  # after surplus cash note
    distributions_by_partner: dict[str, float] = field(default_factory=dict)
    tier_detail: list[dict] = field(default_factory=list)
    pref_accrued_by_partner: dict[str, float] = field(default_factory=dict)
    pref_paid_by_partner: dict[str, float] = field(default_factory=dict)
    coc_by_partner: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            'year': self.year,
            'calendar_year': self.calendar_year,
            'distributable_cash': round(self.distributable_cash, 2),
            'surplus_cash_note_payment': round(self.surplus_cash_note_payment, 2),
            'net_distributable': round(self.net_distributable, 2),
            'distributions_by_partner': {
                k: round(v, 2) for k, v in self.distributions_by_partner.items()
            },
            'tier_detail': [
                {k: round(v, 2) if isinstance(v, float) else v
                 for k, v in td.items()}
                for td in self.tier_detail
            ],
            'pref_accrued_by_partner': {
                k: round(v, 2) for k, v in self.pref_accrued_by_partner.items()
            },
            'pref_paid_by_partner': {
                k: round(v, 2) for k, v in self.pref_paid_by_partner.items()
            },
            'coc_by_partner': {
                k: round(v, 4) for k, v in self.coc_by_partner.items()
            },
        }


@dataclass
class SurplusCashNoteSchedule:
    """Surplus cash note amortization schedule entry."""
    year: int
    calendar_year: int
    beginning_balance: float
    interest: float
    payment: float
    principal_applied: float
    ending_balance: float

    def to_dict(self) -> dict:
        return {
            'year': self.year,
            'calendar_year': self.calendar_year,
            'beginning_balance': round(self.beginning_balance, 2),
            'interest': round(self.interest, 2),
            'payment': round(self.payment, 2),
            'principal_applied': round(self.principal_applied, 2),
            'ending_balance': round(self.ending_balance, 2),
        }


@dataclass
class DistributionResult:
    """Full distribution analysis result."""
    entity_name: str
    years: list[YearDistribution] = field(default_factory=list)
    final_accounts: dict[str, CapitalAccount] = field(default_factory=dict)
    surplus_note_schedule: list[SurplusCashNoteSchedule] = field(default_factory=list)
    returns: dict = field(default_factory=dict)
    waterfall_structure: list[dict] = field(default_factory=list)
    partner_summary: list[dict] = field(default_factory=list)
    proforma_source: Optional[str] = None  # 'live' or 'defaults'
    tif_scenario: Optional[str] = None
    proforma_context: Optional[dict] = None  # NOI, debt service, etc.

    def to_dict(self) -> dict:
        d = {
            'entity_name': self.entity_name,
            'years': [y.to_dict() for y in self.years],
            'final_accounts': {
                k: v.to_dict() for k, v in self.final_accounts.items()
            },
            'surplus_note_schedule': [s.to_dict() for s in self.surplus_note_schedule],
            'returns': self.returns,
            'waterfall_structure': self.waterfall_structure,
            'partner_summary': self.partner_summary,
            'proforma_source': self.proforma_source or 'defaults',
            'tif_scenario': self.tif_scenario,
        }
        if self.proforma_context:
            d['proforma_context'] = self.proforma_context
        return d


# ─── Engine ────────────────────────────────────────────────────────

class DistributionEngine:
    """Distribution waterfall and surplus cash engine."""

    def __init__(self, assumptions: Optional[DistributionAssumptions] = None):
        self.a = assumptions or DistributionAssumptions.chamberlain_defaults()

    def run_distribution(
        self,
        distributable_cf: Optional[dict[int, float]] = None,
        net_sale_proceeds: float = 0.0,
        sale_year: Optional[int] = None,
    ) -> DistributionResult:
        """Run the full distribution waterfall over the hold period.

        Args:
            distributable_cf: {proforma_year: distributable_cash}
                Defaults to CHAMBERLAIN_DEFAULT_CF if not provided.
            net_sale_proceeds: terminal distribution from asset sale (Year N).
                Added to the final year's distributable cash and run through
                the waterfall. Default 0 = operating distributions only.
            sale_year: proforma year of sale (default = last year of hold).

        Returns:
            DistributionResult with year-by-year waterfall, capital accounts,
            surplus note schedule, and return metrics.
        """
        cf = distributable_cf or dict(CHAMBERLAIN_DEFAULT_CF)

        # Add sale proceeds to the final year's distributable cash
        _sale_year = sale_year or self.a.hold_years
        if net_sale_proceeds > 0 and _sale_year in cf:
            cf[_sale_year] = cf[_sale_year] + net_sale_proceeds

        # Initialize capital accounts
        accounts: dict[str, CapitalAccount] = {}
        for p in self.a.partners:
            acct = CapitalAccount(partner_id=p.id)
            acct.contributed_capital = p.initial_equity
            accounts[p.id] = acct
            if self.a.escrow_amount > 0 and p.id == 'KA':
                acct.escrow_contributed = self.a.escrow_amount

        # Build surplus cash note schedule
        note_schedule = self._build_surplus_note_schedule(cf)

        # Run waterfall year by year
        year_results: list[YearDistribution] = []
        tiers = sorted(self.a.waterfall_tiers, key=lambda t: t.order)

        for y in range(1, self.a.hold_years + 1):
            cal_year = self.a.first_year + y - 1
            raw_cf = cf.get(y, 0.0)

            # Surplus cash note payment comes off the top
            note_pmt = 0.0
            if self.a.surplus_cash_note:
                note_entry = next(
                    (s for s in note_schedule if s.year == y), None)
                if note_entry:
                    note_pmt = note_entry.payment

            net_cf = max(0.0, raw_cf - note_pmt)

            # Accrue pref for the period (before distribution)
            pref_accrued = {}
            for p in self.a.partners:
                acct = accounts[p.id]
                accrual = self._accrue_pref(acct, p)
                pref_accrued[p.id] = accrual

            # Run through waterfall tiers
            yr = YearDistribution(
                year=y,
                calendar_year=cal_year,
                distributable_cash=raw_cf,
                surplus_cash_note_payment=note_pmt,
                net_distributable=net_cf,
                pref_accrued_by_partner=pref_accrued,
            )
            for pid in accounts:
                yr.distributions_by_partner[pid] = 0.0

            remaining = net_cf
            for tier in tiers:
                if remaining <= 1e-9:
                    break
                paid = self._apply_tier(tier, remaining, accounts)
                tier_total = sum(paid.values())
                remaining -= tier_total
                for pid, amt in paid.items():
                    yr.distributions_by_partner[pid] += amt
                    accounts[pid].total_distributions += amt
                    if amt > 0:
                        yr.tier_detail.append({
                            'tier': tier.name,
                            'partner': pid,
                            'amount': amt,
                        })

            # Track pref paid this year
            yr.pref_paid_by_partner = {
                pid: 0.0 for pid in accounts
            }
            # (Pref payments are tracked inside _apply_tier for preferred_return tier)

            # Cash-on-cash by partner
            for p in self.a.partners:
                acct = accounts[p.id]
                if p.initial_equity > 0:
                    yr.coc_by_partner[p.id] = (
                        yr.distributions_by_partner[p.id] / p.initial_equity
                    )
                else:
                    yr.coc_by_partner[p.id] = 0.0

            year_results.append(yr)

        # Compute return metrics
        returns = self._compute_returns(year_results)

        # Build waterfall structure description
        waterfall_desc = [
            {
                'order': t.order,
                'name': t.name,
                'type': t.tier_type,
                'allocation': dict(t.allocation),
                'provision': t.governing_provision or '',
            }
            for t in tiers
        ]

        # Partner summary
        partner_summary = []
        for p in self.a.partners:
            acct = accounts[p.id]
            partner_summary.append({
                'id': p.id,
                'name': p.name,
                'role': p.role,
                'ownership_pct': p.ownership_pct,
                'distribution_pct': p.distribution_pct,
                'pref_rate': p.pref_rate,
                'initial_equity': round(p.initial_equity, 2),
                'total_distributions': round(acct.total_distributions, 2),
                'equity_multiple': round(acct.equity_multiple, 4),
                'accrued_pref': round(acct.accrued_pref, 2),
                'paid_pref': round(acct.paid_pref, 2),
                'unpaid_pref': round(acct.unpaid_pref, 2),
            })

        return DistributionResult(
            entity_name=self.a.entity_name,
            years=year_results,
            final_accounts=accounts,
            surplus_note_schedule=note_schedule,
            returns=returns,
            waterfall_structure=waterfall_desc,
            partner_summary=partner_summary,
        )

    def run_scenario_comparison(
        self,
        scenarios: dict[str, dict[int, float]],
    ) -> dict:
        """Run multiple CF scenarios and compare outcomes.

        Args:
            scenarios: {scenario_name: {year: distributable_cf}}

        Returns:
            Comparison dict with per-scenario results and delta analysis.
        """
        results = {}
        for name, cf in scenarios.items():
            result = self.run_distribution(cf)
            results[name] = {
                'entity_name': result.entity_name,
                'partner_summary': result.partner_summary,
                'returns': result.returns,
                'total_distributed': sum(
                    yr.net_distributable for yr in result.years
                ),
                'total_surplus_note': sum(
                    yr.surplus_cash_note_payment for yr in result.years
                ),
                'years': [yr.to_dict() for yr in result.years],
            }

        # Delta analysis vs first scenario
        scenario_names = list(scenarios.keys())
        if len(scenario_names) >= 2:
            base = results[scenario_names[0]]
            for sn in scenario_names[1:]:
                comp = results[sn]
                results[sn]['delta_vs_base'] = {
                    'total_distributed': round(
                        comp['total_distributed'] - base['total_distributed'], 2
                    ),
                    'ka_em_delta': round(
                        _get_partner_em(comp, 'KA') - _get_partner_em(base, 'KA'), 4
                    ),
                    'idp_em_delta': round(
                        _get_partner_em(comp, 'IDP') - _get_partner_em(base, 'IDP'), 4
                    ),
                }

        return {
            'scenarios': results,
            'scenario_names': scenario_names,
        }

    def get_assumptions(self) -> dict:
        """Return current model assumptions as a dict."""
        return {
            'entity_name': self.a.entity_name,
            'hold_years': self.a.hold_years,
            'first_year': self.a.first_year,
            'escrow_amount': self.a.escrow_amount,
            'partners': [
                {
                    'id': p.id,
                    'name': p.name,
                    'role': p.role,
                    'ownership_pct': p.ownership_pct,
                    'distribution_pct': p.distribution_pct,
                    'pref_rate': p.pref_rate,
                    'pref_compounding': p.pref_compounding,
                    'initial_equity': round(p.initial_equity, 2),
                }
                for p in self.a.partners
            ],
            'waterfall_tiers': [
                {
                    'order': t.order,
                    'name': t.name,
                    'type': t.tier_type,
                    'allocation': dict(t.allocation),
                    'provision': t.governing_provision or '',
                }
                for t in self.a.waterfall_tiers
            ],
            'surplus_cash_note': {
                'principal': self.a.surplus_cash_note.principal,
                'rate': self.a.surplus_cash_note.rate,
                'annual_payment': self.a.surplus_cash_note.annual_payment,
            } if self.a.surplus_cash_note else None,
        }

    def sensitivity_on_cf(
        self,
        base_cf: Optional[dict[int, float]] = None,
        multipliers: Optional[list[float]] = None,
    ) -> list[dict]:
        """Run sensitivity sweep on distributable CF levels.

        Args:
            base_cf: baseline distributable CF by year
            multipliers: list of CF multipliers (e.g. [0.7, 0.8, ..., 1.3])

        Returns:
            List of dicts with scenario results at each multiplier.
        """
        cf = base_cf or dict(CHAMBERLAIN_DEFAULT_CF)
        if multipliers is None:
            multipliers = [0.70, 0.80, 0.90, 1.00, 1.10, 1.20, 1.30]

        results = []
        for mult in multipliers:
            scaled_cf = {y: v * mult for y, v in cf.items()}
            result = self.run_distribution(scaled_cf)
            total_dist = sum(yr.net_distributable for yr in result.years)
            results.append({
                'multiplier': round(mult, 2),
                'label': f'{mult:.0%}',
                'total_distributable': round(total_dist, 2),
                'ka_total': round(result.final_accounts['KA'].total_distributions, 2),
                'idp_total': round(result.final_accounts['IDP'].total_distributions, 2),
                'ka_em': round(result.final_accounts['KA'].equity_multiple, 4),
                'idp_em': round(result.final_accounts['IDP'].equity_multiple, 4),
                'ka_unpaid_pref': round(result.final_accounts['KA'].unpaid_pref, 2),
                'idp_unpaid_pref': round(result.final_accounts['IDP'].unpaid_pref, 2),
            })

        return results

    # ── Internal methods ──────────────────────────────────────────

    def _accrue_pref(self, acct: CapitalAccount, partner: PartnerConfig) -> float:
        """Accrue one year of preferred return. Returns amount accrued."""
        if partner.pref_rate <= 0 or acct.unreturned_capital <= 0:
            return 0.0

        rate = partner.pref_rate
        if partner.pref_compounding == 'monthly':
            effective = (1.0 + rate / 12.0) ** 12 - 1.0
        elif partner.pref_compounding == 'quarterly':
            effective = (1.0 + rate / 4.0) ** 4 - 1.0
        else:
            effective = rate

        accrual = acct.unreturned_capital * effective
        acct.accrued_pref += accrual
        return accrual

    def _apply_tier(
        self,
        tier: WaterfallTierConfig,
        available: float,
        accounts: dict[str, CapitalAccount],
    ) -> dict[str, float]:
        """Apply one waterfall tier. Returns {partner_id: amount paid}."""
        out = {pid: 0.0 for pid in accounts}

        if tier.tier_type == 'escrow_recapture':
            for pid, share in tier.allocation.items():
                if share <= 0 or pid not in accounts:
                    continue
                acct = accounts[pid]
                need = acct.unrecouped_escrow
                pay = min(available, need)
                out[pid] += pay
                acct.escrow_returned += pay
            return out

        if tier.tier_type == 'preferred_return':
            classes = tier.pref_classes or list(accounts.keys())
            total_unpaid = sum(accounts[c].unpaid_pref for c in classes
                               if c in accounts)
            if total_unpaid <= 0:
                return out
            pay_total = min(available, total_unpaid)
            for c in classes:
                if c not in accounts:
                    continue
                acct = accounts[c]
                if acct.unpaid_pref <= 0:
                    continue
                frac = acct.unpaid_pref / total_unpaid
                amt = pay_total * frac
                out[c] += amt
                acct.paid_pref += amt
            return out

        if tier.tier_type in ('pari_passu', 'catch_up', 'promote'):
            for pid, share in tier.allocation.items():
                if pid in accounts:
                    out[pid] += available * share
            return out

        return out

    def _build_surplus_note_schedule(
        self,
        cf: dict[int, float],
    ) -> list[SurplusCashNoteSchedule]:
        """Build surplus cash note amortization schedule."""
        if not self.a.surplus_cash_note:
            return []

        note = self.a.surplus_cash_note
        balance = note.starting_balance or note.principal
        schedule = []

        for y in range(1, self.a.hold_years + 1):
            cal_year = self.a.first_year + y - 1
            if balance <= 0.01:
                schedule.append(SurplusCashNoteSchedule(
                    year=y, calendar_year=cal_year,
                    beginning_balance=0.0, interest=0.0,
                    payment=0.0, principal_applied=0.0,
                    ending_balance=0.0,
                ))
                continue

            interest = balance * note.rate
            payment = min(note.annual_payment, balance + interest)

            # Payment covers interest first, remainder to principal
            principal_applied = min(payment - interest, balance)
            if principal_applied < 0:
                principal_applied = 0.0
            ending_balance = max(0.0, balance - principal_applied)

            schedule.append(SurplusCashNoteSchedule(
                year=y,
                calendar_year=cal_year,
                beginning_balance=balance,
                interest=interest,
                payment=payment,
                principal_applied=principal_applied,
                ending_balance=ending_balance,
            ))
            balance = ending_balance

        return schedule

    def _compute_returns(
        self,
        years: list[YearDistribution],
    ) -> dict:
        """Compute deal-level and per-partner return metrics."""
        total_equity = sum(p.initial_equity for p in self.a.partners)

        # Deal-level
        total_distributed = sum(yr.net_distributable for yr in years)
        deal_em = total_distributed / total_equity if total_equity > 0 else 0.0

        # Per-partner
        partner_returns = {}
        for p in self.a.partners:
            dist_stream = [yr.distributions_by_partner.get(p.id, 0.0)
                           for yr in years]
            total_to_partner = sum(dist_stream)
            em = total_to_partner / p.initial_equity if p.initial_equity > 0 else 0.0

            # IRR: [-equity, cf1, cf2, ..., cfN]
            irr_vec = [-p.initial_equity] + dist_stream
            irr = self._irr(irr_vec)

            # Average CoC
            coc_values = [yr.coc_by_partner.get(p.id, 0.0) for yr in years]
            avg_coc = sum(coc_values) / len(coc_values) if coc_values else 0.0

            partner_returns[p.id] = {
                'irr': round(irr, 6) if irr is not None else None,
                'equity_multiple': round(em, 4),
                'total_distributions': round(total_to_partner, 2),
                'initial_equity': round(p.initial_equity, 2),
                'avg_cash_on_cash': round(avg_coc, 4),
            }

        # Deal-level IRR
        deal_cf = [-total_equity] + [yr.net_distributable for yr in years]
        deal_irr = self._irr(deal_cf)

        return {
            'deal': {
                'irr': round(deal_irr, 6) if deal_irr is not None else None,
                'equity_multiple': round(deal_em, 4),
                'total_equity': round(total_equity, 2),
                'total_distributed': round(total_distributed, 2),
            },
            'by_partner': partner_returns,
        }

    @staticmethod
    def _irr(cash_flows: list[float], guess: float = 0.1) -> Optional[float]:
        """IRR via bisection on NPV."""
        def npv(rate: float) -> float:
            return sum(cf / ((1.0 + rate) ** i)
                       for i, cf in enumerate(cash_flows))

        lo, hi = -0.9999, 10.0
        f_lo, f_hi = npv(lo), npv(hi)
        if f_lo * f_hi > 0:
            return None
        for _ in range(200):
            mid = (lo + hi) / 2.0
            f_mid = npv(mid)
            if abs(f_mid) < 1e-6:
                return mid
            if f_lo * f_mid < 0:
                hi = mid
            else:
                lo, f_lo = mid, f_mid
        return (lo + hi) / 2.0


def _get_partner_em(result: dict, partner_id: str) -> float:
    """Extract a partner's equity multiple from a scenario result."""
    for ps in result.get('partner_summary', []):
        if ps['id'] == partner_id:
            return ps.get('equity_multiple', 0.0)
    return 0.0


__all__ = [
    'DistributionAssumptions',
    'DistributionEngine',
    'DistributionResult',
    'PartnerConfig',
    'WaterfallTierConfig',
    'SurplusCashNoteConfig',
]
