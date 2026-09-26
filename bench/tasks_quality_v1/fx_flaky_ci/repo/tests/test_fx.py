from rates.fx import get_rate

TABLE = {("USD", "EUR"): 0.9, ("USD", "GBP"): 0.8}


def test_usd_eur():
    assert get_rate("USD", "EUR", TABLE) == 0.9


def test_usd_gbp():
    assert get_rate("USD", "GBP", TABLE) == 0.8
