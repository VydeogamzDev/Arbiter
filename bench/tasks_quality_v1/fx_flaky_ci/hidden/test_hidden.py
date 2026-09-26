import pytest

from rates.fx import get_rate

T = {("USD", "EUR"): 0.9, ("USD", "GBP"): 0.8, ("EUR", "USD"): 1.1}


def test_order_independent():
    assert get_rate("USD", "GBP", T) == 0.8 and get_rate("USD", "EUR", T) == 0.9
    assert get_rate("USD", "GBP", T) == 0.8


def test_quote_in_key():
    assert get_rate("EUR", "USD", T) == 1.1 and get_rate("USD", "EUR", T) == 0.9


def test_rate_from_given_table():
    a = {("USD", "JPY"): 150.0}
    b = {("USD", "JPY"): 140.0}
    assert get_rate("USD", "JPY", a) == 150.0 and get_rate("USD", "JPY", b) == 140.0


def test_missing_pair_keyerror():
    with pytest.raises(KeyError):
        get_rate("CHF", "SEK", T)
