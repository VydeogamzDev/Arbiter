import hashlib
import hmac

WEBHOOK_SECRET = b"change-me"


class PaymentDeclined(Exception):
    pass


def charge_card(card_token, amount_cents, currency):
    if card_token.startswith("tok_declined"):
        raise PaymentDeclined(card_token)
    return f"ch_{hashlib.sha1(card_token.encode()).hexdigest()[:12]}"


def verify_webhook(body, signature):
    expected = hmac.new(WEBHOOK_SECRET, body, "sha256").hexdigest()
    return hmac.compare_digest(expected, signature)
