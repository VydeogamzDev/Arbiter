from sympy import isprime, prevprime


def test_prevprime_correct():
    for n in list(range(10**6, 10**6 + 3000)) + [10**9 + 9, 10**12 + 40]:
        q = n - 1
        while not isprime(q):
            q -= 1
        assert prevprime(n) == q, n
