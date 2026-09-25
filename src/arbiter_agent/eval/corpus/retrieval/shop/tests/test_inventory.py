import pytest

from shop.catalog.inventory import OutOfStock, reserve_stock


def test_out_of_stock():
    with pytest.raises(OutOfStock):
        reserve_stock(None, "x", 1)
