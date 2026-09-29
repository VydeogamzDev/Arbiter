import random

from sympy.utilities.iterables import least_rotation


def test_least_rotation_minimal():
    rng = random.Random(1)
    for _ in range(3000):
        x = [rng.randint(0, 2) for _ in range(rng.randint(1, 9))]
        best = min(x[i:] + x[:i] for i in range(len(x)))
        k = least_rotation(x)
        assert x[k:] + x[:k] == best, x
