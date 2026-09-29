from sympy import divisor_count, divisors


def test_divisor_count_modulus():
    for n in range(1, 300):
        for m in (2, 3, 5, 7):
            assert divisor_count(n, m) == sum(1 for d in divisors(n) if d % m == 0), (n, m)
