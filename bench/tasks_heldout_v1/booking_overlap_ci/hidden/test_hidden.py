from datetime import date

from scheduling.ranges import free_days, overlaps

D = lambda n: date(2026, 1, n)  # noqa: E731


def test_shared_endpoint_conflicts():
    assert overlaps(D(1), D(3), D(3), D(5)) and overlaps(D(3), D(5), D(1), D(3))


def test_disjoint_ok():
    assert not overlaps(D(1), D(2), D(3), D(4)) and not overlaps(D(5), D(6), D(1), D(4))


def test_contained():
    assert overlaps(D(1), D(10), D(3), D(4)) and overlaps(D(3), D(4), D(1), D(10))


def test_single_day():
    assert overlaps(D(3), D(3), D(3), D(3)) and overlaps(D(3), D(3), D(1), D(3))


def test_free_days():
    assert free_days(D(1), D(6), [(D(2), D(3)), (D(5), D(5))]) == [D(1), D(4), D(6)]
