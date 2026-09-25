from shop.payments.gateway import charge_card, PaymentDeclined
from shop.checkout.receipts import send_receipt


def checkout(session, cart, settings, card_token):
    total = cart.total(settings)
    try:
        charge_id = charge_card(card_token, total, settings["currency"])
    except PaymentDeclined:
        return {"status": "declined"}
    session.query("INSERT INTO orders (user_id, total_cents, status) VALUES (?, ?, 'paid')", (cart.user_id, total))
    send_receipt(cart.user_id, total, charge_id)
    return {"status": "paid", "charge": charge_id}
