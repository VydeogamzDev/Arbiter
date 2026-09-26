from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

TAX = {"CA": Decimal("0.0725"), "NY": Decimal("0.08875"), "OR": Decimal("0")}
CENT = Decimal("0.01")
ZERO = Decimal("0")


def _q(x):
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def price_order(order: dict) -> dict:
    region = order["region"]
    if region not in TAX:
        raise ValueError(f"unknown region {region}")
    cust = order.get("customer") or {}
    lines = [(ln["category"], Decimal(str(ln["unit_price"])) * int(ln["qty"])) for ln in order["lines"]]
    subtotal = sum((a for _, a in lines), ZERO)
    business = cust.get("type") == "business"
    after_biz = [(c, a * Decimal("0.90") if business and c == "hardware" else a) for c, a in lines]
    biz = subtotal - sum((a for _, a in after_biz), ZERO)
    base = subtotal - biz
    coupon = order.get("coupon")
    if coupon is None:
        cd = ZERO
    elif coupon == "SAVE5":
        cd = min(Decimal("5.00"), base)
    elif coupon == "PCT15":
        cd = base * Decimal("0.15")
    else:
        raise ValueError(f"unknown coupon {coupon}")
    cap = subtotal * Decimal("0.30")
    if biz + cd > cap:
        cd = max(ZERO, cap - biz)
    discount = biz + cd
    after = subtotal - discount
    shipping = ZERO if after >= 100 else Decimal(str(order.get("shipping") or "0"))
    if cust.get("tax_exempt"):
        tax = ZERO
    else:
        ratio = cd / base if base else ZERO
        taxable = sum((a * (1 - ratio) for c, a in after_biz if c != "food"), ZERO)
        if region != "NY":
            taxable += shipping
        tax = (taxable * TAX[region]).quantize(CENT, rounding=ROUND_HALF_EVEN)
    total = _q(after) + _q(shipping) + tax
    if total < 1:
        raise ValueError("order total below the 1.00 minimum")
    return {"subtotal": _q(subtotal), "discount": _q(discount), "shipping": _q(shipping), "tax": tax,
            "total": total}
