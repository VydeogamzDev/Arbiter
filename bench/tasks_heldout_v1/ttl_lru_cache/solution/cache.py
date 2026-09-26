import time
from collections import OrderedDict


class TTLCache:
    """A least-recently-used cache whose entries also expire after a time-to-live."""

    def __init__(self, capacity, ttl, clock=time.monotonic):
        if capacity < 1 or ttl <= 0:
            raise ValueError("capacity must be >= 1 and ttl > 0")
        self.capacity, self.ttl, self.clock = capacity, ttl, clock
        self._data = OrderedDict()   # key -> (value, expires_at); order = recency

    def _expired(self, key, now):
        return self._data[key][1] <= now

    def _purge(self):
        now = self.clock()
        for k in [k for k in self._data if self._expired(k, now)]:
            del self._data[k]

    def put(self, key, value):
        now = self.clock()
        if key in self._data:
            del self._data[key]
        elif len(self._data) >= self.capacity:
            self._purge()
            if len(self._data) >= self.capacity:
                self._data.popitem(last=False)
        self._data[key] = (value, now + self.ttl)

    def get(self, key, default=None):
        if key not in self._data:
            return default
        if self._expired(key, self.clock()):
            del self._data[key]
            return default
        self._data.move_to_end(key)
        return self._data[key][0]

    def delete(self, key):
        return self._data.pop(key, None) is not None

    def __len__(self):
        self._purge()
        return len(self._data)

    def __contains__(self, key):
        return key in self._data and not self._expired(key, self.clock())
