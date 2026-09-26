import time


class TokenBucket:
    def __init__(self, rate, capacity, clock=time.monotonic):
        if rate <= 0 or capacity <= 0:
            raise ValueError("rate and capacity must be positive")
        self.rate, self.capacity, self.clock = rate, capacity, clock
        self.tokens = float(capacity)
        self.last = clock()

    def _refill(self):
        now = self.clock()
        self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)
        self.last = now

    def _check(self, n):
        if n <= 0 or n > self.capacity:
            raise ValueError("n must be in 1..capacity")

    def try_acquire(self, n=1):
        self._check(n)
        self._refill()
        if self.tokens + 1e-9 >= n:
            self.tokens -= n
            return True
        return False

    def wait_time(self, n=1):
        self._check(n)
        self._refill()
        return max(0.0, (n - self.tokens) / self.rate)
