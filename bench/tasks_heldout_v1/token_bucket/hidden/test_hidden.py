import pytest

from ratelimit import TokenBucket


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_starts_full():
    b = TokenBucket(1, 5, clock=Clock())
    assert all(b.try_acquire() for _ in range(5)) and not b.try_acquire()


def test_fail_takes_nothing():
    clk = Clock()
    b = TokenBucket(1, 5, clock=clk)
    assert b.try_acquire(4)
    assert not b.try_acquire(2)     # only 1 left
    assert b.try_acquire(1)


def test_fractional_refill():
    clk = Clock()
    b = TokenBucket(2, 4, clock=clk)
    assert b.try_acquire(4)
    clk.t += 0.25
    assert not b.try_acquire(1)     # 0.5 tokens
    clk.t += 0.25
    assert b.try_acquire(1)         # 1.0 token


def test_capped():
    clk = Clock()
    b = TokenBucket(10, 3, clock=clk)
    clk.t += 100
    assert b.try_acquire(3) and not b.try_acquire(1)


@pytest.mark.parametrize("n", [0, -1, 6])
def test_invalid_n(n):
    with pytest.raises(ValueError):
        TokenBucket(1, 5, clock=Clock()).try_acquire(n)


@pytest.mark.parametrize(("rate", "cap"), [(0, 1), (-1, 1), (1, 0), (1, -2)])
def test_invalid_ctor(rate, cap):
    with pytest.raises(ValueError):
        TokenBucket(rate, cap)


def test_wait_time():
    clk = Clock()
    b = TokenBucket(2, 4, clock=clk)
    assert b.wait_time(3) == 0.0
    b.try_acquire(4)
    assert b.wait_time(1) == pytest.approx(0.5)
    clk.t += 0.25
    assert b.wait_time(2) == pytest.approx(0.75)
