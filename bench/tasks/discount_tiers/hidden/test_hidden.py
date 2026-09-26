import pathlib

from shop.pricing import compute_total

ITEMS = [(100.0, 1)]


def test_gold():
    assert compute_total(ITEMS, tier="gold") == 95.0


def test_platinum():
    assert compute_total(ITEMS, tier="platinum") == 90.0


def test_unknown_tier():
    assert compute_total(ITEMS, tier="bronze") == 100.0
    assert compute_total(ITEMS, tier=None) == 100.0


def test_coupon_after_discount():
    assert compute_total(ITEMS, tier="platinum", coupon=10) == 80.0


def test_never_negative():
    assert compute_total([(5.0, 1)], coupon=50) == 0


def test_backward_compatible():
    assert compute_total([(2.5, 2), (1.0, 3)]) == 8.0


def test_rounding():
    assert compute_total([(0.335, 3)], tier="gold") == round(0.335 * 3 * 0.95, 2)


def test_added_tests():
    text = "".join(p.read_text() for p in pathlib.Path("tests").glob("test_*.py"))
    assert "tier" in text and "coupon" in text
