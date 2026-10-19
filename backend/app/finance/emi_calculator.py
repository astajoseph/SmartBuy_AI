"""EMI and total-cost calculations (Decimal arithmetic, rupee-paisa precision).

Definitions used throughout (principal is never counted twice):
    financed amount   = EMI price - upfront discount applied to the EMI purchase - down payment
    total instalments = monthly EMI x tenure
    total interest    = total instalments - financed amount
    total cost        = down payment + total instalments + processing fee + GST on fee + other fees - cashback
    extra vs upfront  = total cost - effective upfront price (what paying in full would cost)

Rates:
    annual_reducing   nominal annual rate, monthly rate = annual / 12, standard amortising EMI
    monthly_reducing  monthly rate as stated
    annual_flat       interest = financed x annual rate x months / 12, spread evenly
    monthly_flat      interest = financed x monthly rate x months
    unknown           not used for calculation; an advertised EMI can still be costed

Unknown inputs stay None and are listed in `assumptions` / `missing`; they are not treated as 0
unless an explicit, labelled assumption says so.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

RateType = Literal["annual_reducing", "annual_flat", "monthly_reducing", "monthly_flat", "unknown"]
D0 = Decimal("0")
CENT = Decimal("0.01")
DEFAULT_GST_ON_FEES_PCT = Decimal("18")   # GST on financial-service fees in India
NO_COST_UNVERIFIED = "Advertised as No-Cost EMI; effective cost not fully verified."


def q(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def reducing_emi(principal: Decimal, monthly_rate: Decimal, months: int) -> Decimal:
    """EMI = P r (1+r)^n / ((1+r)^n - 1); EMI = P / n when r = 0."""
    if months <= 0:
        raise ValueError("tenure must be positive")
    if principal < 0 or monthly_rate < 0:
        raise ValueError("principal and rate must be non-negative")
    if monthly_rate == 0:
        return principal / months
    f = (1 + monthly_rate) ** months
    return principal * monthly_rate * f / (f - 1)


def flat_emi(principal: Decimal, rate_per_period: Decimal, months: int, periods_per_year: int) -> tuple[Decimal, Decimal]:
    """Flat-rate loan. rate_per_period is a fraction per year (periods_per_year=12 -> monthly rate x months)."""
    if months <= 0:
        raise ValueError("tenure must be positive")
    if periods_per_year == 1:
        interest = principal * rate_per_period * months / 12
    else:
        interest = principal * rate_per_period * months
    return (principal + interest) / months, interest


def implied_annual_rate(principal: Decimal, emi: Decimal, months: int) -> Decimal | None:
    """Nominal annual reducing-balance rate (%) that produces `emi`; None if EMI < P/n (negative rate)."""
    if principal <= 0 or months <= 0:
        return None
    if emi * months < principal - CENT:
        return None
    if abs(emi * months - principal) <= CENT * months:
        return D0
    lo, hi = Decimal("0"), Decimal("0.2")
    for _ in range(80):
        mid = (lo + hi) / 2
        if reducing_emi(principal, mid, months) > emi:
            hi = mid
        else:
            lo = mid
    return (lo * 12 * 100).quantize(Decimal("0.01"))


@dataclass
class EmiTerms:
    emi_price: Decimal                          # product price when buying on EMI
    tenure_months: int | None = None
    rate_pct: Decimal | None = None             # as stated (percent)
    rate_type: RateType = "unknown"
    advertised_emi: Decimal | None = None
    down_payment: Decimal | None = None
    upfront_discount: Decimal | None = None     # instant discount that applies to the EMI purchase
    processing_fee: Decimal | None = None
    processing_fee_gst: Decimal | None = None   # if disclosed
    fee_includes_gst: bool = False
    other_fees: Decimal | None = None
    cashback: Decimal | None = None
    no_cost_claim: bool = False
    upfront_price: Decimal | None = None        # price if paid in full (for comparison)
    upfront_only_discount: Decimal | None = None  # discount available only when paying in full


@dataclass
class EmiResult:
    status: Literal["complete", "incomplete"]
    financed_amount: Decimal | None = None
    down_payment: Decimal | None = None
    monthly_emi: Decimal | None = None
    tenure_months: int | None = None
    total_instalments: Decimal | None = None
    total_interest: Decimal | None = None
    processing_fee: Decimal | None = None
    processing_fee_gst: Decimal | None = None
    other_fees: Decimal | None = None
    cashback: Decimal | None = None
    total_cost: Decimal | None = None
    effective_upfront_price: Decimal | None = None
    extra_cost_vs_upfront: Decimal | None = None
    implied_annual_rate_pct: Decimal | None = None
    emi_source: Literal["advertised", "calculated", "none"] = "none"
    no_cost_verdict: str | None = None
    assumptions: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in self.__dict__.items()}


def calculate(t: EmiTerms) -> EmiResult:
    r = EmiResult(status="incomplete")
    if t.emi_price is None or t.emi_price <= 0:
        r.missing.append("emi_price")
        return r

    down = t.down_payment
    if down is None:
        down = D0
        r.assumptions.append("No down payment stated; assumed ₹0.")
    discount = t.upfront_discount or D0
    if discount > t.emi_price:
        r.warnings.append("Upfront discount exceeds price; terms are contradictory.")
        r.missing.append("valid_discount")
        return r
    financed = t.emi_price - discount - down
    if financed <= 0:
        r.warnings.append("Nothing left to finance after discount and down payment.")
        return r
    r.financed_amount, r.down_payment, r.tenure_months = q(financed), q(down), t.tenure_months

    # upfront comparison price
    if t.upfront_price is not None:
        r.effective_upfront_price = q(t.upfront_price - (t.upfront_only_discount or D0))

    n = t.tenure_months
    if not n:
        r.missing.append("tenure_months")
        return r

    calc_emi = None
    rate = t.rate_pct
    if rate is not None and t.rate_type != "unknown":
        frac = rate / 100
        if t.rate_type == "annual_reducing":
            calc_emi = reducing_emi(financed, frac / 12, n)
        elif t.rate_type == "monthly_reducing":
            calc_emi = reducing_emi(financed, frac, n)
        elif t.rate_type == "annual_flat":
            calc_emi, _ = flat_emi(financed, frac, n, periods_per_year=1)
        elif t.rate_type == "monthly_flat":
            calc_emi, _ = flat_emi(financed, frac, n, periods_per_year=12)
    elif rate is not None:
        r.warnings.append("Interest rate basis (annual/monthly, flat/reducing) not stated; rate not used.")

    if t.advertised_emi is not None:
        emi = t.advertised_emi
        r.emi_source = "advertised"
        if calc_emi is not None and abs(calc_emi - emi) > max(Decimal("1"), emi * Decimal("0.01")):
            r.warnings.append(f"Advertised EMI ₹{emi} differs from ₹{q(calc_emi)} calculated from the stated rate; "
                              "costs below use the advertised EMI.")
    elif calc_emi is not None:
        emi = calc_emi
        r.emi_source = "calculated"
    else:
        if t.no_cost_claim and (t.rate_pct is None):
            emi = financed / n
            r.emi_source = "calculated"
            r.assumptions.append("No-Cost EMI with no rate or EMI amount stated; assumed instalments = financed amount / tenure.")
        else:
            r.missing.append("interest_rate_or_monthly_emi")
            return r

    emi = q(emi)
    total_inst = emi * n
    interest = total_inst - financed
    if interest < -CENT * n:
        r.warnings.append("Instalments add up to less than the financed amount; terms look contradictory.")
    r.monthly_emi, r.total_instalments, r.total_interest = emi, q(total_inst), q(max(interest, D0))
    r.implied_annual_rate_pct = implied_annual_rate(financed, emi, n)

    # fees
    fees_known = True
    fee = t.processing_fee
    if fee is None:
        fees_known = False
        r.missing.append("processing_fee")
        r.assumptions.append("Processing fee not disclosed; excluded from the total (actual cost may be higher).")
        fee, gst = D0, D0
    elif t.fee_includes_gst:
        gst = D0
    elif t.processing_fee_gst is not None:
        gst = t.processing_fee_gst
    else:
        gst = fee * DEFAULT_GST_ON_FEES_PCT / 100
        if fee > 0:
            r.assumptions.append(f"GST on processing fee not stated; {DEFAULT_GST_ON_FEES_PCT}% assumed.")
    other = t.other_fees or D0
    cash = t.cashback or D0
    if cash:
        r.assumptions.append("Cashback assumed to be received in full; it is often credited later and may have conditions.")
    r.processing_fee, r.processing_fee_gst = q(fee), q(gst)
    r.other_fees, r.cashback = q(other), q(cash)
    r.total_cost = q(down + total_inst + fee + gst + other - cash)
    if r.effective_upfront_price is not None:
        r.extra_cost_vs_upfront = q(r.total_cost - r.effective_upfront_price)

    r.status = "complete" if fees_known and not r.missing else "incomplete"
    if t.no_cost_claim:
        r.no_cost_verdict = no_cost_verdict(r, fees_known)
    return r


def no_cost_verdict(r: EmiResult, fees_known: bool) -> str:
    if r.extra_cost_vs_upfront is None:
        return NO_COST_UNVERIFIED + " (no upfront price to compare with)"
    if r.extra_cost_vs_upfront > Decimal("1"):
        return (f"Advertised as No-Cost EMI, but costs ₹{r.extra_cost_vs_upfront} more than paying upfront "
                "(interest, fees or a discount you lose by choosing EMI).")
    if not fees_known:
        return NO_COST_UNVERIFIED
    return "Verified: total cost matches the upfront price under the disclosed terms."


def compare_tenures(financed: Decimal, annual_rate_pct: Decimal, tenures: list[int]) -> list[dict]:
    """Show the monthly-vs-total trade-off for the same loan at several tenures."""
    rows = []
    for n in tenures:
        emi = q(reducing_emi(financed, annual_rate_pct / 100 / 12, n))
        rows.append({"tenure_months": n, "monthly_emi": emi, "total_paid": q(emi * n), "total_interest": q(emi * n - financed)})
    return rows
