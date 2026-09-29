from sympy.ntheory import isprime, sqrt_mod


def test_sqrt_mod_roots():
    for p in [q for q in range(3, 3000) if isprime(q) and q % 8 == 1]:
        for a in (2, 3, 5, 7, 10, 11):
            r = sqrt_mod(a, p)
            assert r is None or r * r % p == a % p, (a, p, r)
    assert sqrt_mod(2, 73) is not None
