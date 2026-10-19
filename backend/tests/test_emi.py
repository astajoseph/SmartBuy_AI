from decimal import Decimal as D

import pytest

from app.finance.effective_price import PurchaseOption, rank_options
from app.finance.emi_calculator import (NO_COST_UNVERIFIED, EmiTerms, calculate, compare_tenures, flat_emi,
                                        implied_annual_rate, reducing_emi)


def test_zero_interest_emi_is_principal_over_tenure():
    assert reducing_emi(D("60000"), D("0"), 12) == D("5000")


def test_reducing_balance_emi_matches_standard_formula():
    # 60,000 at 12% p.a. reducing for 12 months: r = 1% -> EMI 5,330.93 (standard amortisation tables)
    emi = reducing_emi(D("60000"), D("0.01"), 12)
    assert emi.quantize(D("0.01")) == D("5330.93")


def test_flat_rate_interest_on_full_principal():
    emi, interest = flat_emi(D("60000"), D("0.12"), 12, periods_per_year=1)
    assert interest == D("7200")                 # 60,000 x 12% x 1 year
    assert emi == D("5600")


def test_principal_not_counted_twice():
    # spec example: financed 60,000, instalments add up to 66,000 -> interest 6,000
    r = calculate(EmiTerms(emi_price=D("60000"), tenure_months=12, advertised_emi=D("5500"), processing_fee=D("0"),
                           down_payment=D("0"), upfront_price=D("60000")))
    assert r.total_instalments == D("66000.00")
    assert r.total_interest == D("6000.00")
    assert r.total_cost == D("66000.00")
    assert r.extra_cost_vs_upfront == D("6000.00")


def test_down_payment_reduces_financed_amount_and_is_added_to_cost():
    r = calculate(EmiTerms(emi_price=D("70000"), down_payment=D("10000"), tenure_months=12, rate_pct=D("12"),
                           rate_type="annual_reducing", processing_fee=D("0"), upfront_price=D("70000")))
    assert r.financed_amount == D("60000.00")
    assert r.monthly_emi == D("5330.93")
    assert r.total_cost == r.down_payment + r.total_instalments


def test_processing_fee_and_default_gst_are_added_with_assumption():
    r = calculate(EmiTerms(emi_price=D("60000"), tenure_months=6, rate_pct=D("0"), rate_type="annual_reducing",
                           processing_fee=D("199"), down_payment=D("0"), upfront_price=D("60000")))
    assert r.processing_fee_gst == D("35.82")    # 18% of 199
    assert r.total_cost == D("60234.82")
    assert any("GST" in a for a in r.assumptions)


def test_disclosed_gst_is_used_instead_of_default():
    r = calculate(EmiTerms(emi_price=D("60000"), tenure_months=6, rate_pct=D("0"), rate_type="annual_reducing",
                           processing_fee=D("199"), processing_fee_gst=D("0"), down_payment=D("0")))
    assert r.processing_fee_gst == D("0.00")


def test_no_cost_emi_with_fee_costs_more_than_upfront():
    r = calculate(EmiTerms(emi_price=D("30000"), tenure_months=6, advertised_emi=D("5000"), processing_fee=D("199"),
                           no_cost_claim=True, down_payment=D("0"), upfront_price=D("30000")))
    assert r.total_interest == D("0.00")
    assert r.extra_cost_vs_upfront == D("234.82")
    assert "costs ₹234.82 more" in r.no_cost_verdict


def test_no_cost_emi_where_upfront_discount_is_lost():
    # upfront buyers get ₹1,500 off; EMI buyers do not -> "no cost" EMI is ₹1,500 dearer
    r = calculate(EmiTerms(emi_price=D("30000"), tenure_months=6, advertised_emi=D("5000"), processing_fee=D("0"),
                           no_cost_claim=True, down_payment=D("0"), upfront_price=D("30000"),
                           upfront_only_discount=D("1500")))
    assert r.extra_cost_vs_upfront == D("1500.00")


def test_no_cost_emi_unverified_when_fee_unknown():
    r = calculate(EmiTerms(emi_price=D("30000"), tenure_months=6, advertised_emi=D("5000"), no_cost_claim=True,
                           down_payment=D("0"), upfront_price=D("30000")))
    assert r.no_cost_verdict == NO_COST_UNVERIFIED
    assert "processing_fee" in r.missing
    assert r.status == "incomplete"


def test_missing_tenure_is_incomplete_not_zero():
    r = calculate(EmiTerms(emi_price=D("30000"), advertised_emi=D("2500")))
    assert r.status == "incomplete"
    assert r.total_cost is None
    assert "tenure_months" in r.missing


def test_contradictory_terms_are_flagged():
    r = calculate(EmiTerms(emi_price=D("60000"), tenure_months=12, rate_pct=D("12"), rate_type="annual_reducing",
                           advertised_emi=D("5000"), processing_fee=D("0"), down_payment=D("0")))
    assert any("differs" in w for w in r.warnings) or any("less than" in w for w in r.warnings)


def test_unknown_rate_basis_is_not_used():
    r = calculate(EmiTerms(emi_price=D("60000"), tenure_months=12, rate_pct=D("14"), rate_type="unknown"))
    assert "interest_rate_or_monthly_emi" in r.missing
    assert any("basis" in w for w in r.warnings)


def test_implied_rate_roundtrip():
    emi = reducing_emi(D("60000"), D("0.01"), 12)
    assert implied_annual_rate(D("60000"), emi, 12) == pytest.approx(D("12.00"), abs=D("0.02"))


def test_longer_tenure_lowers_emi_but_raises_total_interest():
    rows = compare_tenures(D("60000"), D("15"), [6, 12, 24])
    assert rows[0]["monthly_emi"] > rows[1]["monthly_emi"] > rows[2]["monthly_emi"]
    assert rows[0]["total_interest"] < rows[1]["total_interest"] < rows[2]["total_interest"]


def test_max_monthly_emi_excludes_plans():
    opts = [PurchaseOption("a", "A", "emi", D("61000"), D("10200"), 6, D("1000")),
            PurchaseOption("a", "A", "emi", D("63000"), D("5250"), 12, D("3000")),
            PurchaseOption("b", "B", "upfront", D("60000"))]
    res = rank_options(opts, "lowest_total_cost", max_monthly=D("6000"))
    assert [o.total_cost for o in res["ranked"]] == [D("60000"), D("63000")]
    assert "exceeds your limit" in res["excluded"][0]["reason"]


def test_lowest_monthly_vs_lowest_total_objectives_differ():
    opts = [PurchaseOption("a", "A", "emi", D("61000"), D("10200"), 6, D("1000")),
            PurchaseOption("a", "A", "emi", D("63000"), D("5250"), 12, D("3000"))]
    assert rank_options(opts, "lowest_total_cost")["ranked"][0].tenure_months == 6
    assert rank_options(opts, "lowest_monthly_payment")["ranked"][0].tenure_months == 12
