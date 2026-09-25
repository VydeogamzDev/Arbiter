from shop.notify.email import send_email


def send_receipt(user_id, total_cents, charge_id):
    send_email(user_id, "Your receipt", f"Charged {total_cents / 100:.2f} (ref {charge_id})")
