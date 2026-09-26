import time


class TokenBucket:
    def __init__(self, rate, capacity, clock=time.monotonic):
        raise NotImplementedError
