from ratelimit import TokenBucket


def test_acquire():
    assert TokenBucket(1, 1, clock=lambda: 0).try_acquire()
