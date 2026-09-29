from sympy.utilities.iterables import binary_partitions


def count(n, largest):
    if n == 0:
        return 1
    return sum(count(n - p, p) for p in (1 << k for k in range(n.bit_length() + 1)) if p <= min(n, largest))


def test_binary_partitions_all():
    for n in range(1, 40):
        parts = [tuple(p) for p in binary_partitions(n)]
        assert all(sum(p) == n for p in parts), n
        assert len(set(parts)) == count(n, n), n
