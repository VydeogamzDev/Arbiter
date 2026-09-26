import time

from fib import fib


def test_exact_small():
    assert fib(30) == 832040


def test_large_exact():
    v = fib(10_000)
    assert isinstance(v, int) and len(str(v)) == 2090 and str(v).endswith("366875")


def test_fast():
    t = time.perf_counter()
    fib(10_000)
    assert time.perf_counter() - t < 1.0
