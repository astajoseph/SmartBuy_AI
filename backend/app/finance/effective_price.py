"""Upfront effective price and ranking of purchase options by user objective."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

Objective = Literal["lowest_total_cost", "lowest_monthly_payment", "shortest_tenure", "lowest_interest", "balanced"]


@dataclass
class PurchaseOption:
    vendor_id: str
    vendor_name: str
    kind: Literal["upfront", "emi"]
    total_cost: Decimal | None
    monthly_payment: Decimal | None = None
    tenure_months: int | None = None
    total_interest: Decimal | None = None
    complete: bool = True
    conditional: bool = False          # needs a specific bank/card the user has not confirmed
    emi_offer_id: int | None = None
    notes: list[str] = field(default_factory=list)


def effective_upfront_price(price: Decimal, *, instant_discount: Decimal | None = None,
                            shipping: Decimal | None = None) -> tuple[Decimal, list[str]]:
    notes = []
    total = price - (instant_discount or Decimal("0"))
    if shipping is None:
        notes.append("Shipping charge unknown; not included.")
    else:
        total += shipping
    return total, notes


def rank_options(options: list[PurchaseOption], objective: Objective, *, max_monthly: Decimal | None = None,
                 preferred_tenure: int | None = None) -> dict:
    """Returns {'ranked': [...], 'excluded': [...]}. Options with unknown totals are ranked after known
    ones for cost objectives. Options above the user's monthly limit are excluded with a reason."""
    ranked, excluded = [], []
    for o in options:
        if max_monthly is not None and o.kind == "emi" and o.monthly_payment is not None and o.monthly_payment > max_monthly:
            excluded.append({"option": o, "reason": f"Monthly EMI ₹{o.monthly_payment} exceeds your limit of ₹{max_monthly}"})
            continue
        if max_monthly is not None and o.kind == "upfront" and o.total_cost is not None and o.total_cost > max_monthly:
            o.notes.append("Paying in full exceeds your stated monthly amount.")
        ranked.append(o)

    big = Decimal("1e12")

    def total(o):
        return o.total_cost if o.total_cost is not None else big

    keys = {
        "lowest_total_cost": lambda o: (not o.complete, total(o)),
        "lowest_monthly_payment": lambda o: (o.monthly_payment if o.monthly_payment is not None else
                                             (total(o) if o.kind == "upfront" else big), total(o)),
        "shortest_tenure": lambda o: (o.tenure_months or 0, total(o)),
        "lowest_interest": lambda o: (o.total_interest if o.total_interest is not None else
                                      (Decimal("0") if o.kind == "upfront" else big), total(o)),
        # totals in the same ₹100 bucket count as a tie; ties go to the tenure closest to the preference
        "balanced": lambda o: (not o.complete, (total(o) / 100).to_integral_value(),
                               abs((o.tenure_months or 0) - (preferred_tenure or 6))),
    }
    ranked.sort(key=keys[objective])
    return {"ranked": ranked, "excluded": excluded}
