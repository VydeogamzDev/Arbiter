def fmt(amount_cents: int, currency: str = "USD") -> str:
    symbol = {"USD": "$", "EUR": "EUR ", "AUD": "A$"}.get(currency, currency + " ")
    return f"{symbol}{amount_cents / 100:,.2f}"
