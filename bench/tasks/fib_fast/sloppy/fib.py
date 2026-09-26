from functools import lru_cache


@lru_cache(maxsize=None)
def fib(n):
    """The n-th Fibonacci number (fib(0) = 0, fib(1) = 1)."""
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)
