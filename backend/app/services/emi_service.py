"""Vendor + EMI comparison built from (cached or freshly fetched) vendor data."""
from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal

from app.finance.effective_price import Objective, PurchaseOption, effective_upfront_price, rank_options
from app.extraction.vendor_offers import offer_value
from app.finance.emi_calculator import EmiTerms, calculate, compare_tenures
from app.services.vendor_service import VendorData

EMI_STATUS_LABEL = {
    "available": "EMI plans shown on the page",
    "not_offered": "Vendor states EMI is not available for this item",
    "not_shown": "No EMI option shown on the product page",
    "unknown": "EMI availability could not be determined",
    None: "EMI availability could not be determined",
}


def _bank_offer_rows(vd: VendorData, price: Decimal | None, user_banks_l: set[str]) -> list[dict]:
    rows = []
    for o in vd.bank_offers:
        value, why = offer_value(o, price) if price is not None else (None, "Vendor price unknown.")
        banks = [b.strip() for b in (o["bank"] or "").split(",") if b.strip()]
        eligible = None if not banks else (True if user_banks_l and any(b.lower() in user_banks_l for b in banks)
                                           else (False if user_banks_l else None))
        rows.append({"title": o["title"], "description": o["description"], "category": o["category"], "kind": o["kind"],
                     "bank": o["bank"], "card_type": o["card_type"], "min_spend": _s(o["min_spend"]),
                     "max_discount": _s(o["max_discount"]), "percent": _s(o["percent"]),
                     "estimated_saving": _s(value), "saving_note": why, "terms_complete": o["terms_complete"],
                     "you_hold_this_bank": eligible})
    return rows


def build_comparison(vendor_data: list[VendorData], *, objective: Objective = "lowest_total_cost",
                     max_monthly: Decimal | None = None, preferred_tenure: int | None = None,
                     user_banks: list[str] | None = None) -> dict:
    rows, options, conditional, data_issues = [], [], [], []
    user_banks_l = {b.lower() for b in (user_banks or [])}

    for vd in vendor_data:
        vs = vd.vendor_source
        name = vs.get("vendor_name") or vs["vendor_id"]
        if vd.status == "unavailable" or vd.snapshot is None:
            data_issues.append({"vendor": name, "message": vd.message})
            rows.append({"vendor": name, "status": "unavailable", "message": vd.message})
            continue
        snap = vd.snapshot
        price = snap.get("price")
        upfront = None
        notes = []
        if price is not None:
            upfront, notes = effective_upfront_price(price)
            options.append(PurchaseOption(vendor_id=vs["vendor_id"], vendor_name=name, kind="upfront",
                                          total_cost=upfront, notes=notes))
        else:
            notes.append("Vendor price could not be extracted.")

        emi_rows = []
        for o in vd.emi_offers:
            emi_price = o["product_price"] or price
            if emi_price is None:
                continue
            res = calculate(EmiTerms(
                emi_price=emi_price, tenure_months=o["tenure_months"], rate_pct=o["interest_rate"],
                rate_type=o["interest_rate_type"] or "unknown", advertised_emi=o["monthly_emi"],
                down_payment=o["down_payment"], processing_fee=o["processing_fee"],
                processing_fee_gst=o["processing_fee_gst"], other_fees=o["other_fees"],
                upfront_discount=o["upfront_discount"], cashback=o["cashback"],
                no_cost_claim=o["no_cost_emi_claim"], upfront_price=price))
            is_conditional = bool(o["bank_name"]) and o["bank_name"].lower() not in user_banks_l
            emi_rows.append({
                "emi_offer_id": o["id"], "bank": o["bank_name"], "card_type": o["card_type"],
                "tenure_months": o["tenure_months"], "advertised_monthly_emi": _s(o["monthly_emi"]),
                "interest_rate_pct": _s(o["interest_rate"]), "rate_type": o["interest_rate_type"],
                "no_cost_claim": o["no_cost_emi_claim"], "calculation": res.as_dict(),
                "verification_status": o["verification_status"],
                "label": "Parsed from vendor page text (unverified)" if o["verification_status"] == "parsed_heuristically"
                         else "Extracted from vendor page",
                "conditional_on_bank": is_conditional, "raw_text": o["raw_text"], "assumptions": o["assumptions"],
                "offer_valid_until": o["offer_valid_until"].isoformat() if o["offer_valid_until"] else None,
            })
            opt = PurchaseOption(vendor_id=vs["vendor_id"], vendor_name=name, kind="emi", total_cost=res.total_cost,
                                 monthly_payment=res.monthly_emi, tenure_months=res.tenure_months,
                                 total_interest=res.total_interest, complete=res.status == "complete",
                                 conditional=is_conditional, emi_offer_id=o["id"],
                                 notes=res.assumptions + res.warnings + ([res.no_cost_verdict] if res.no_cost_verdict else []))
            (conditional if is_conditional else options).append(opt)

        offer_rows = _bank_offer_rows(vd, price, user_banks_l)
        best_conditional = None
        for orow in offer_rows:
            sv = Decimal(orow["estimated_saving"]) if orow["estimated_saving"] else Decimal("0")
            if price is not None and sv > 0 and (best_conditional is None or sv > best_conditional["saving"]):
                best_conditional = {"saving": sv, "offer": orow}
        if best_conditional and upfront is not None:
            opt = PurchaseOption(vendor_id=vs["vendor_id"], vendor_name=name, kind="upfront",
                                 total_cost=upfront - best_conditional["saving"],
                                 conditional=best_conditional["offer"]["you_hold_this_bank"] is not True,
                                 notes=[f"With offer: {best_conditional['offer']['title']} ({best_conditional['offer']['saving_note']})"])
            (options if not opt.conditional else conditional).append(opt)
        details = snap.get("details") or {}
        rows.append({
            "vendor": name, "vendor_id": vs["vendor_id"], "status": vd.status, "source_url": vs["source_url"],
            "displayed_price": _s(price), "mrp": _s(snap.get("mrp")),
            "discount_pct": (str(round((snap["mrp"] - price) / snap["mrp"] * 100)) if snap.get("mrp") and price else None),
            "effective_upfront_price": _s(upfront), "currency": snap.get("currency"),
            "emi_status": snap.get("emi_status"), "emi_status_label": EMI_STATUS_LABEL.get(snap.get("emi_status"), snap.get("emi_status")),
            "bank_offers": offer_rows, "return_policy": snap.get("return_policy"), "details": details,
            "availability": snap.get("availability"), "seller": snap.get("seller_name"),
            "delivery": snap.get("delivery_text"), "emi_options": emi_rows, "notes": notes,
            "freshness": vd.freshness, "message": vd.message,
        })

    ranking = rank_options(options, objective, max_monthly=max_monthly, preferred_tenure=preferred_tenure)
    statuses = [(r["vendor"], r.get("emi_status")) for r in rows if r.get("status") != "unavailable"]
    if statuses and not any(st == "available" for _, st in statuses):
        emi_summary = ("No vendor offers EMI for this product: " +
                       "; ".join(f"{v}: {EMI_STATUS_LABEL.get(st, st)[0].lower()}{EMI_STATUS_LABEL.get(st, st)[1:]}" for v, st in statuses) +
                       ". For a low-priced item, paying upfront is the only option shown.")
    elif statuses:
        emi_summary = "EMI plans found at: " + ", ".join(v for v, st in statuses if st == "available") + "."
    else:
        emi_summary = None
    tradeoff = None
    rated = [o for vd in vendor_data for o in vd.emi_offers
             if o["interest_rate"] and o["interest_rate_type"] == "annual_reducing" and o["product_price"]]
    if rated:
        o = rated[0]
        tradeoff = {"financed": str(o["product_price"]), "annual_rate_pct": str(o["interest_rate"]),
                    "rows": [{k: str(v) for k, v in r.items()} for r in
                             compare_tenures(o["product_price"], o["interest_rate"], [3, 6, 9, 12, 18, 24])],
                    "explanation": "Longer tenures lower the monthly EMI but increase the total interest paid."}
    return {
        "objective": objective, "max_monthly_emi": _s(max_monthly),
        "vendors": rows,
        "ranked_options": [_opt(o) for o in ranking["ranked"]],
        "excluded_options": [{"option": _opt(e["option"]), "reason": e["reason"]} for e in ranking["excluded"]],
        "conditional_options": [_opt(o) for o in conditional],
        "tenure_tradeoff": tradeoff,
        "emi_summary": emi_summary,
        "data_issues": data_issues,
        "disclaimer": "Calculations use the terms shown on vendor pages at the time of retrieval. "
                      "This is not personalised financial advice; confirm final terms with the lender before buying.",
    }


def _opt(o: PurchaseOption) -> dict:
    d = asdict(o)
    return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in d.items()}


def _s(v) -> str | None:
    return None if v is None else str(v)
