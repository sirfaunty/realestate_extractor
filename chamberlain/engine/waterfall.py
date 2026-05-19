"""Configurable waterfall engine.

Evaluates a list of WaterfallTiers in order against a stream of
distributable cash, producing per-investor-class distributions per period.

This is a generic engine; Chamberlain's actual structure is the first
configured instance:

  Tier 1 ESCROW_RECAPTURE  — 100% to KA until escrow cap returned
  Tier 2 PREFERRED_RETURN  — 6.5% pref on contributed capital, both classes,
                             pro-rata to unreturned pref balance
  Tier 3 PARI_PASSU        — 75% KA / 25% IDP on remaining

Other deals can add CATCH_UP and PROMOTE tiers; the evaluator handles
them generically.

Pref accrual model:
  - Each class accrues pref monthly on its unreturned contributed-capital
    balance at rate `pref_return_rate`, compounded monthly.
  - Contributions increase the capital base; return-of-capital distributions
    reduce it; pref distributions reduce accrued-but-unpaid pref first.

The engine works on an annual grid for v1 (the Excel partnership returns
tab is annual). Monthly pref compounding is approximated by applying
(1+r/12)^12 − 1 effective annual accrual on the average balance. A future
refinement can move this to true monthly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..models.partnership import (
    PartnershipConfig,
    TierType,
    WaterfallTier,
)


@dataclass
class ClassLedger:
    """Running capital-account state for one investor class."""

    class_id: str
    contributed_capital: float = 0.0
    returned_capital: float = 0.0
    accrued_pref: float = 0.0
    paid_pref: float = 0.0
    escrow_contributed: float = 0.0
    escrow_returned: float = 0.0
    total_distributions: float = 0.0

    @property
    def unreturned_capital(self) -> float:
        return max(0.0, self.contributed_capital - self.returned_capital)

    @property
    def unpaid_pref(self) -> float:
        return max(0.0, self.accrued_pref - self.paid_pref)

    @property
    def unrecouped_escrow(self) -> float:
        return max(0.0, self.escrow_contributed - self.escrow_returned)


@dataclass
class WaterfallPeriodResult:
    """One period's distribution outcome."""

    period_label: str
    distributable_cash: float
    distributions_by_class: dict[str, float] = field(default_factory=dict)
    tier_detail: list[dict] = field(default_factory=list)  # [{tier, class, amount}]


@dataclass
class WaterfallResult:
    """Full multi-period waterfall outcome."""

    periods: list[WaterfallPeriodResult] = field(default_factory=list)
    final_ledgers: dict[str, ClassLedger] = field(default_factory=dict)

    def total_to_class(self, class_id: str) -> float:
        return sum(p.distributions_by_class.get(class_id, 0.0) for p in self.periods)


def _accrue_pref(ledger: ClassLedger, annual_rate: float, compounding: str) -> None:
    """Accrue one year of preferred return on unreturned capital."""
    if annual_rate <= 0 or ledger.unreturned_capital <= 0:
        return
    if compounding == "monthly":
        effective = (1.0 + annual_rate / 12.0) ** 12 - 1.0
    elif compounding == "quarterly":
        effective = (1.0 + annual_rate / 4.0) ** 4 - 1.0
    else:
        effective = annual_rate
    ledger.accrued_pref += ledger.unreturned_capital * effective


def run_waterfall(
    partnership: PartnershipConfig,
    *,
    contributions_by_period: list[dict[str, float]],
    distributable_by_period: list[float],
    period_labels: Optional[list[str]] = None,
    escrow_contributions_by_period: Optional[list[dict[str, float]]] = None,
) -> WaterfallResult:
    """Run the configured waterfall.

    Args:
        partnership: PartnershipConfig with investor_classes + waterfall tiers
        contributions_by_period: per-period {class_id: contribution amount}
            (capital called from each investor that period)
        distributable_by_period: per-period distributable cash to run through
            the waterfall
        period_labels: optional labels (e.g. ["Year 1", ...])
        escrow_contributions_by_period: per-period {class_id: escrow amount}
            for the ESCROW_RECAPTURE tier (KA-only for Chamberlain)

    Returns:
        WaterfallResult with per-period and final ledger state.
    """
    ledgers: dict[str, ClassLedger] = {
        c.id: ClassLedger(class_id=c.id) for c in partnership.investor_classes
    }
    tiers = sorted(partnership.waterfall, key=lambda t: t.tier_order)
    n = len(distributable_by_period)
    labels = period_labels or [f"Year {i+1}" for i in range(n)]

    result = WaterfallResult()

    for p in range(n):
        # 1. Apply contributions
        contribs = contributions_by_period[p] if p < len(contributions_by_period) else {}
        for cid, amt in contribs.items():
            if cid in ledgers:
                ledgers[cid].contributed_capital += amt

        # Escrow contributions (separate bucket)
        if escrow_contributions_by_period and p < len(escrow_contributions_by_period):
            for cid, amt in escrow_contributions_by_period[p].items():
                if cid in ledgers:
                    ledgers[cid].escrow_contributed += amt

        # 2. Accrue pref for the period
        for c in partnership.investor_classes:
            rate = c.pref_return_rate.value if c.pref_return_rate else 0.0
            _accrue_pref(ledgers[c.id], rate, c.pref_compounding)

        # 3. Run distributable cash through the tiers
        cash = distributable_by_period[p]
        pr = WaterfallPeriodResult(period_label=labels[p], distributable_cash=cash)
        for cid in ledgers:
            pr.distributions_by_class[cid] = 0.0

        for tier in tiers:
            if cash <= 1e-9:
                break
            paid = _apply_tier(tier, cash, ledgers, partnership)
            tier_total = sum(paid.values())
            cash -= tier_total
            for cid, amt in paid.items():
                pr.distributions_by_class[cid] += amt
                ledgers[cid].total_distributions += amt
                if amt > 0:
                    pr.tier_detail.append(
                        {"tier": tier.name, "class": cid, "amount": amt}
                    )

        result.periods.append(pr)

    result.final_ledgers = ledgers
    return result


def _apply_tier(
    tier: WaterfallTier,
    available: float,
    ledgers: dict[str, ClassLedger],
    partnership: PartnershipConfig,
) -> dict[str, float]:
    """Apply one tier; return {class_id: amount paid}."""
    out: dict[str, float] = {cid: 0.0 for cid in ledgers}

    if tier.tier_type == TierType.ESCROW_RECAPTURE:
        # 100% to the class(es) in allocation until escrow recouped
        for cid, share in tier.allocation.items():
            if share.value <= 0:
                continue
            led = ledgers[cid]
            need = led.unrecouped_escrow
            pay = min(available, need)
            out[cid] += pay
            led.escrow_returned += pay
        return out

    if tier.tier_type == TierType.PREFERRED_RETURN:
        # Pay unpaid pref pro-rata to unpaid-pref balances of pref_classes
        classes = tier.pref_classes or list(ledgers.keys())
        total_unpaid = sum(ledgers[c].unpaid_pref for c in classes)
        if total_unpaid <= 0:
            return out
        pay_total = min(available, total_unpaid)
        for c in classes:
            led = ledgers[c]
            if led.unpaid_pref <= 0:
                continue
            frac = led.unpaid_pref / total_unpaid
            amt = pay_total * frac
            out[c] += amt
            led.paid_pref += amt
        return out

    if tier.tier_type == TierType.RETURN_OF_CAPITAL:
        classes = list(tier.allocation.keys()) or list(ledgers.keys())
        total_unret = sum(ledgers[c].unreturned_capital for c in classes)
        if total_unret <= 0:
            return out
        pay_total = min(available, total_unret)
        for c in classes:
            led = ledgers[c]
            if led.unreturned_capital <= 0:
                continue
            frac = led.unreturned_capital / total_unret
            amt = pay_total * frac
            out[c] += amt
            led.returned_capital += amt
        return out

    if tier.tier_type in (TierType.PARI_PASSU, TierType.CATCH_UP, TierType.PROMOTE):
        # Split by fixed allocation
        for cid, share in tier.allocation.items():
            out[cid] += available * share.value
        return out

    return out


__all__ = [
    "ClassLedger",
    "WaterfallPeriodResult",
    "WaterfallResult",
    "run_waterfall",
]
