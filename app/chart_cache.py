from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any


class ChartCache:
    """Small in-process LRU cache for the expensive per-symbol indicator
    computation behind /api/v1/symbols/{symbol}/bars. Thread-safe since
    FastAPI runs sync route handlers in a thread pool."""

    def __init__(self, max_size: int = 300):
        self._max_size = max_size
        self._lock = threading.Lock()
        self._store: OrderedDict[tuple, Any] = OrderedDict()

    def get(self, key: tuple) -> Any | None:
        with self._lock:
            value = self._store.get(key)
            if value is not None:
                self._store.move_to_end(key)
            return value

    def set(self, key: tuple, value: Any) -> None:
        with self._lock:
            self._store[key] = value
            self._store.move_to_end(key)
            while len(self._store) > self._max_size:
                self._store.popitem(last=False)
