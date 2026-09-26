from shop.pricing import compute_total


def test_total():
    assert compute_total([(2.5, 2), (1.0, 3)]) == 8.0
