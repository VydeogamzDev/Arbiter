from shop.pricing import compute_total


def test_tier_and_coupon():
    assert compute_total([(100.0, 1)], tier="platinum", coupon=10) == 80.0
