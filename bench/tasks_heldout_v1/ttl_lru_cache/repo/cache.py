import time


class TTLCache:
    """A least-recently-used cache whose entries also expire after a time-to-live."""

    def __init__(self, capacity, ttl, clock=time.monotonic):
        raise NotImplementedError
